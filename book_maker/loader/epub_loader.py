import os
import pickle
import sys
from copy import copy
from pathlib import Path

from bs4 import BeautifulSoup as bs
from ebooklib import ITEM_DOCUMENT, epub
from rich import print
from tqdm import tqdm

from .base_loader import BaseBookLoader


class EPUBBookLoader(BaseBookLoader):
    def __init__(
        self,
        epub_name,
        model,
        key,
        resume,
        language,
        model_api_base=None,
        is_test=False,
        test_num=5,
        translate_tags="p",
        allow_navigable_strings=False,
        accumulated_num=1,
    ):
        self.epub_name = epub_name
        self.new_epub = epub.EpubBook()
        self.translate_model = model(key, language, model_api_base)
        self.is_test = is_test
        self.test_num = test_num
        self.translate_tags = translate_tags
        self.allow_navigable_strings = allow_navigable_strings
        self.accumulated_num = accumulated_num

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
        return text.isdigit() or text.isspace()

    def _make_new_book(self, book):
        new_book = epub.EpubBook()
        new_book.metadata = book.metadata
        new_book.spine = book.spine
        new_book.toc = book.toc
        return new_book

    def make_bilingual_book(self):
        def deal_new(p, waitPList):
            ret = deal_old(waitPList)
            new_p = copy(p)
            new_p.string = self.translate_model.translate(p.text)
            p.insert_after(new_p)
            return ret

        def deal_old(waitPList):
            if len(waitPList) == 0:
                return []

            resultTxtList = self.translate_model.translate_list(waitPList)

            for i in range(0, len(waitPList)):
                if i < len(resultTxtList):
                    p = waitPList[i]
                    new_p = copy(p)
                    new_p.string = resultTxtList[i]
                    p.insert_after(new_p)

            return []

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
                soup = bs(item.content, "html.parser")
                p_list = soup.findAll(trans_taglist)
                if self.allow_navigable_strings:
                    p_list.extend(soup.findAll(text=True))

                sendNum = self.accumulated_num
                if sendNum > 1:
                    count = 0
                    waitPList = []
                    for i in range(0, len(p_list)):
                        p = p_list[i]
                        if not p.text or self._is_special_text(p.text):
                            continue
                        length = len(p.text)
                        if length > sendNum:
                            waitPList = deal_new(p, waitPList)
                            continue
                        if i == len(p_list) - 1:
                            if count + length < sendNum:
                                waitPList.append(p)
                                waitPList = deal_old(waitPList)
                            else:
                                waitPList = deal_new(p, waitPList)
                            break
                        if count + length < sendNum:
                            count += length
                            waitPList.append(p)
                        else:
                            waitPList = deal_old(waitPList)
                            waitPList.append(p)
                            count = len(p.text)
                else:
                    is_test_done = self.is_test and index > self.test_num
                    for p in p_list:
                        if is_test_done or not p.text or self._is_special_text(p.text):
                            continue
                        new_p = copy(p)
                        # TODO banch of p to translate then combine
                        # PR welcome here
                        if self.resume and index < p_to_save_len:
                            new_p.string = self.p_to_save[index]
                        else:
                            new_p.string = self.translate_model.translate(p.text)
                            self.p_to_save.append(new_p.text)
                        p.insert_after(new_p)
                        index += 1
                        if index % 20 == 0:
                            self._save_progress()
                        # pbar.update(delta) not pbar.update(index)?
                        pbar.update(1)
                        if self.is_test and index >= self.test_num:
                            break

                item.content = soup.prettify().encode()
                new_book.add_item(item)
            name, _ = os.path.splitext(self.epub_name)
            epub.write_epub(f"{name}_bilingual.epub", new_book, {})
            pbar.close()
        except (KeyboardInterrupt, Exception) as e:
            print(e)
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
                    for p in p_list:
                        if not p.text or self._is_special_text(p.text):
                            continue
                        # TODO banch of p to translate then combine
                        # PR welcome here
                        if index < p_to_save_len:
                            new_p = copy(p)
                            new_p.string = self.p_to_save[index]
                            print(new_p.string)
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
