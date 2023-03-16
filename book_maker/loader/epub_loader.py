import os
import re
import pickle
import tiktoken
import sys
from copy import copy
from pathlib import Path

from bs4 import BeautifulSoup as bs
from bs4.element import NavigableString
from ebooklib import ITEM_DOCUMENT, epub
from rich import print
from tqdm import tqdm

from book_maker.utils import prompt_config_to_kwargs

from .base_loader import BaseBookLoader


class EPUBBookLoaderHelper:
    def __init__(self, translate_model, accumulated_num):
        self.translate_model = translate_model
        self.accumulated_num = accumulated_num

    def deal_new(self, p, wait_p_list):
        self.deal_old(wait_p_list)
        new_p = copy(p)
        new_p.string = self.translate_model.translate(p.text)
        p.insert_after(new_p)

    def deal_old(self, wait_p_list):
        if len(wait_p_list) == 0:
            return

        result_txt_list = self.translate_model.translate_list(wait_p_list)

        for i in range(len(wait_p_list)):
            if i < len(result_txt_list):
                p = wait_p_list[i]
                new_p = copy(p)
                new_p.string = result_txt_list[i]
                p.insert_after(new_p)

        wait_p_list.clear()


# ref: https://platform.openai.com/docs/guides/chat/introduction
def num_tokens_from_text(text, model="gpt-3.5-turbo-0301"):
    messages = (
        {
            "role": "user",
            "content": text,
        },
    )

    """Returns the number of tokens used by a list of messages."""
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    if model == "gpt-3.5-turbo-0301":  # note: future models may deviate from this
        num_tokens = 0
        for message in messages:
            num_tokens += (
                4  # every message follows <im_start>{role/name}\n{content}<im_end>\n
            )
            for key, value in message.items():
                num_tokens += len(encoding.encode(value))
                if key == "name":  # if there's a name, the role is omitted
                    num_tokens += -1  # role is always required and always 1 token
        num_tokens += 2  # every reply is primed with <im_start>assistant
        return num_tokens
    else:
        raise NotImplementedError(
            f"""num_tokens_from_messages() is not presently implemented for model {model}.
  See https://github.com/openai/openai-python/blob/main/chatml.md for information on how messages are converted to tokens."""
        )


def is_link(text):
    url_pattern = re.compile(
        r"http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+"
    )
    return bool(url_pattern.match(text.strip()))


def is_tail_Link(text, num=100):
    text = text.strip()
    url_pattern = re.compile(
        r".*http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+$"
    )
    return bool(url_pattern.match(text)) and len(text) < num


def is_source(text):
    return text.strip().startswith("Source: ")


def is_list(text, num=80):
    text = text.strip()
    return re.match(r"^Listing\s*\d+", text) and len(text) < num


def is_figure(text, num=80):
    text = text.strip()
    return re.match(r"^Figure\s*\d+", text) and len(text) < num


class EPUBBookLoader(BaseBookLoader):
    def __init__(
        self,
        epub_name,
        model,
        key,
        resume,
        language,
        batch_size,
        model_api_base=None,
        is_test=False,
        test_num=5,
        translate_tags="p",
        allow_navigable_strings=False,
        accumulated_num=1,
        prompt_template=None,
        prompt_config=None,
    ):
        self.epub_name = epub_name
        self.new_epub = epub.EpubBook()
        self.translate_model = model(
            key,
            language,
            api_base=model_api_base,
            **prompt_config_to_kwargs(prompt_config),
        )
        self.is_test = is_test
        self.test_num = test_num
        self.translate_tags = translate_tags
        self.allow_navigable_strings = allow_navigable_strings
        self.accumulated_num = accumulated_num
        self.helper = EPUBBookLoaderHelper(self.translate_model, self.accumulated_num)

        try:
            self.origin_book = epub.read_epub(self.epub_name)
        except Exception:
            # tricky for #71 if you don't know why please check the issue and ignore this
            # when upstream change will TODO fix this
            def _load_spine(self):
                spine = self.container.find(
                    "{%s}%s" % (epub.NAMESPACES["OPF"], "spine")
                )

                self.book.spine = [
                    (t.get("idref"), t.get("linear", "yes")) for t in spine
                ]
                self.book.set_direction(spine.get("page-progression-direction", None))

            epub.EpubReader._load_spine = _load_spine
            self.origin_book = epub.read_epub(self.epub_name)

        self.p_to_save = []
        self.resume = resume
        self.bin_path = f"{Path(epub_name).parent}/.{Path(epub_name).stem}.temp.bin"
        if self.resume:
            self.load_state()

    @staticmethod
    def _is_special_text(text):
        return text.isdigit() or text.isspace() or is_link(text)

    def _make_new_book(self, book):
        new_book = epub.EpubBook()
        new_book.metadata = book.metadata
        new_book.spine = book.spine
        new_book.toc = book.toc
        return new_book

    def _process_paragraph(self, p, index, p_to_save_len):
        if not p.text or self._is_special_text(p.text):
            return index

        new_p = copy(p)

        if self.resume and index < p_to_save_len:
            new_p.string = self.p_to_save[index]
        else:
            if type(p) == NavigableString:
                new_p = self.translate_model.translate(p.text)
                self.p_to_save.append(new_p)
            else:
                new_p.string = self.translate_model.translate(p.text)
                self.p_to_save.append(new_p.text)

        p.insert_after(new_p)
        index += 1

        if index % 20 == 0:
            self._save_progress()

        return index

    def translate_paragraphs_acc(self, p_list, send_num):
        count = 0
        wait_p_list = []
        for i in range(len(p_list)):
            p = p_list[i]
            temp_p = copy(p)
            for sup in temp_p.find_all("sup"):
                sup.extract()
            if (
                not p.text
                or self._is_special_text(temp_p.text)
                or is_source(temp_p.text)
                or is_list(temp_p.text)
                or is_figure(temp_p.text)
                or is_tail_Link(temp_p.text)
            ):
                continue
            length = num_tokens_from_text(temp_p.text)
            if length > send_num:
                self.helper.deal_new(p, wait_p_list)
                continue
            if i == len(p_list) - 1:
                if count + length < send_num:
                    wait_p_list.append(p)
                    self.helper.deal_old(wait_p_list)
                else:
                    self.helper.deal_new(p, wait_p_list)
                break
            if count + length < send_num:
                count += length
                wait_p_list.append(p)
                # This is because the more paragraphs, the easier it is possible to translate different numbers of paragraphs, maybe you should find better values than 15 and 2
                # if len(wait_p_list) > 15 and count > send_num / 2:
                #     self.helper.deal_old(wait_p_list)
                #     count = 0
            else:
                self.helper.deal_old(wait_p_list)
                wait_p_list.append(p)
                count = length

    def make_bilingual_book(self):
        new_book = self._make_new_book(self.origin_book)
        all_items = list(self.origin_book.get_items())
        trans_taglist = self.translate_tags.split(",")
        all_p_length = sum(
            0
            if i.get_type() != ITEM_DOCUMENT
            else len(bs(i.content, "html.parser").findAll(trans_taglist))
            for i in all_items
        )
        all_p_length += self.allow_navigable_strings * sum(
            0
            if i.get_type() != ITEM_DOCUMENT
            else len(bs(i.content, "html.parser").findAll(text=True))
            for i in all_items
        )
        pbar = tqdm(total=self.test_num) if self.is_test else tqdm(total=all_p_length)
        index = 0
        p_to_save_len = len(self.p_to_save)
        try:
            # Add the things that don't need to be translated first, so that you can see the img after the interruption
            for item in self.origin_book.get_items():
                if item.get_type() != ITEM_DOCUMENT:
                    new_book.add_item(item)

            for item in self.origin_book.get_items_of_type(ITEM_DOCUMENT):
                # if item.file_name != "OEBPS/ch01.xhtml":
                #     continue
                if not os.path.exists("log"):
                    os.makedirs("log")

                soup = bs(item.content, "html.parser")
                p_list = soup.findAll(trans_taglist)
                if self.allow_navigable_strings:
                    p_list.extend(soup.findAll(text=True))

                send_num = self.accumulated_num
                if send_num > 1:
                    with open("log/buglog.txt", "a") as f:
                        print(f"------------- {item.file_name} -------------", file=f)

                    print("------------------------------------------------------")
                    print(f"dealing {item.file_name} ...")
                    self.translate_paragraphs_acc(p_list, send_num)
                else:
                    is_test_done = self.is_test and index > self.test_num
                    for p in p_list:
                        if is_test_done:
                            break
                        index = self._process_paragraph(p, index, p_to_save_len)
                        # pbar.update(delta) not pbar.update(index)?
                        pbar.update(1)
                        if self.is_test and index >= self.test_num:
                            break

                item.content = soup.prettify().encode()
                new_book.add_item(item)
                if self.accumulated_num > 1:
                    name, _ = os.path.splitext(self.epub_name)
                    epub.write_epub(f"{name}_bilingual.epub", new_book, {})
            name, _ = os.path.splitext(self.epub_name)
            epub.write_epub(f"{name}_bilingual.epub", new_book, {})
            if self.accumulated_num == 1:
                pbar.close()
        except (KeyboardInterrupt, Exception) as e:
            print(e)
            if self.accumulated_num == 1:
                print("you can resume it next time")
                self._save_progress()
                self._save_temp_book()
            sys.exit(0)

    def load_state(self):
        try:
            with open(self.bin_path, "rb") as f:
                self.p_to_save = pickle.load(f)
        except Exception:
            raise Exception("can not load resume file")

    def _save_temp_book(self):
        # TODO refactor this logic
        origin_book_temp = epub.read_epub(self.epub_name)
        new_temp_book = self._make_new_book(origin_book_temp)
        p_to_save_len = len(self.p_to_save)
        trans_taglist = self.translate_tags.split(",")
        index = 0
        try:
            for item in origin_book_temp.get_items():
                if item.get_type() == ITEM_DOCUMENT:
                    soup = bs(item.content, "html.parser")
                    p_list = soup.findAll(trans_taglist)
                    if self.allow_navigable_strings:
                        p_list.extend(soup.findAll(text=True))
                    for p in p_list:
                        if not p.text or self._is_special_text(p.text):
                            continue
                        # TODO banch of p to translate then combine
                        # PR welcome here
                        if index < p_to_save_len:
                            new_p = copy(p)
                            if type(p) == NavigableString:
                                new_p = self.p_to_save[index]
                            else:
                                new_p.string = self.p_to_save[index]
                            p.insert_after(new_p)
                            index += 1
                        else:
                            break
                    # for save temp book
                    item.content = soup.prettify().encode()
                new_temp_book.add_item(item)
            name, _ = os.path.splitext(self.epub_name)
            epub.write_epub(f"{name}_bilingual_temp.epub", new_temp_book, {})
        except Exception as e:
            # TODO handle it
            print(e)

    def _save_progress(self):
        try:
            with open(self.bin_path, "wb") as f:
                pickle.dump(self.p_to_save, f)
        except Exception:
            raise Exception("can not save resume file")
