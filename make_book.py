import argparse
import os
import pickle
import time
from abc import abstractmethod
from copy import copy
from os import environ as env
from pathlib import Path

import openai
import requests
from bs4 import BeautifulSoup as bs
from ebooklib import epub
from rich import print
from tqdm import tqdm

from utils import LANGUAGES, TO_LANGUAGE_CODE

NO_LIMIT = False
IS_TEST = False
RESUME = False


class Base:
    def __init__(self, key, language, api_base=None):
        self.key = key
        self.language = language
        self.current_key_index = 0

    def get_key(self, key_str):
        keys = key_str.split(",")
        key = keys[self.current_key_index]
        self.current_key_index = (self.current_key_index + 1) % len(keys)
        return key

    @abstractmethod
    def translate(self, text):
        pass


class GPT3(Base):
    def __init__(self, key, language, api_base=None):
        super().__init__(key, language)
        self.api_key = key
        if not api_base:
            self.api_url = "https://api.openai.com/v1/completions"
        else:
            self.api_url = api_base + "v1/completions"
        self.headers = {
            "Content-Type": "application/json",
        }
        # TODO support more models here
        self.data = {
            "prompt": "",
            "model": "text-davinci-003",
            "max_tokens": 1024,
            "temperature": 1,
            "top_p": 1,
        }
        self.session = requests.session()
        self.language = language

    def translate(self, text):
        print(text)
        self.headers["Authorization"] = f"Bearer {self.get_key(self.api_key)}"
        self.data["prompt"] = f"Please help me to translate，`{text}` to {self.language}"
        r = self.session.post(self.api_url, headers=self.headers, json=self.data)
        if not r.ok:
            return text
        t_text = r.json().get("choices")[0].get("text", "").strip()
        print(t_text)
        return t_text


class DeepL(Base):
    def __init__(self, session, key, api_base=None):
        super().__init__(session, key, api_base=api_base)

    def translate(self, text):
        return super().translate(text)


class ChatGPT(Base):
    def __init__(self, key, language, api_base=None):
        super().__init__(key, language, api_base=api_base)
        self.key = key
        self.language = language
        if api_base:
            openai.api_base = api_base

    def translate(self, text):
        print(text)
        openai.api_key = self.get_key(self.key)
        try:
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "user",
                        # english prompt here to save tokens
                        "content": f"Please help me to translate,`{text}` to {self.language}, please return only translated content not include the origin text",
                    }
                ],
            )
            t_text = (
                completion["choices"][0]
                .get("message")
                .get("content")
                .encode("utf8")
                .decode()
            )
            if not NO_LIMIT:
                # for time limit
                time.sleep(3)
        except Exception as e:
            # TIME LIMIT for open api please pay
            key_len = self.key.count(",") + 1
            sleep_time = int(60 / key_len)
            time.sleep(sleep_time)
            print(str(e), "will sleep  " + str(sleep_time) + " seconds")
            openai.api_key = self.get_key(self.key)
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "user",
                        "content": f"Please help me to translate,`{text}` to Simplified Chinese, please return only translated content not include the origin text",
                    }
                ],
            )
            t_text = (
                completion["choices"][0]
                .get("message")
                .get("content")
                .encode("utf8")
                .decode()
            )
        print(t_text)
        return t_text


class BEPUB:
    def __init__(self, epub_name, model, key, resume, language, model_api_base=None):
        self.epub_name = epub_name
        self.new_epub = epub.EpubBook()
        self.translate_model = model(key, language, model_api_base)
        self.origin_book = epub.read_epub(self.epub_name)
        self.p_to_save = []
        self.resume = resume
        self.bin_path = f"{Path(epub_name).parent}/.{Path(epub_name).stem}.temp.bin"
        if self.resume:
            self.load_state()

    @staticmethod
    def _is_special_text(text):
        return text.isdigit() or text.isspace()

    def make_bilingual_book(self):
        new_book = epub.EpubBook()
        new_book.metadata = self.origin_book.metadata
        new_book.spine = self.origin_book.spine
        new_book.toc = self.origin_book.toc
        all_items = list(self.origin_book.get_items())
        # we just translate tag p
        all_p_length = 0
        for i in all_items:
            if i.file_name.endswith(".xhtml"):
                all_p_length += len(bs(i.content, "html.parser").findAll("p"))
            else:
                all_p_length += len(bs(i.content, "xml").findAll("p"))
        if IS_TEST:
            pbar = tqdm(total=TEST_NUM)
        else:
            pbar = tqdm(total=all_p_length)
        index = 0
        p_to_save_len = len(self.p_to_save)
        try:
            for i in self.origin_book.get_items():
                pbar.update(index)
                if i.get_type() == 9:
                    soup = bs(i.content, "html.parser")
                    p_list = soup.findAll("p")
                    is_test_done = IS_TEST and index > TEST_NUM
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
                        if IS_TEST and index > TEST_NUM:
                            break
                    i.content = soup.prettify().encode()
                new_book.add_item(i)
            name = self.epub_name.split(".")[0]
            epub.write_epub(f"{name}_bilingual.epub", new_book, {})
            pbar.close()
        except (KeyboardInterrupt, Exception) as e:
            print(e)
            print("you can resume it next time")
            self.save_progress()
            exit(0)

    def load_state(self):
        try:
            with open(self.bin_path, "rb") as f:
                self.p_to_save = pickle.load(f)
        except:
            raise Exception("can not load resume file")

    def save_progress(self):
        try:
            with open(self.bin_path, "wb") as f:
                pickle.dump(self.p_to_save, f)
        except:
            raise Exception("can not save resume file")


if __name__ == "__main__":
    MODEL_DICT = {"gpt3": GPT3, "chatgpt": ChatGPT}
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--book_name",
        dest="book_name",
        type=str,
        help="your epub book file path",
    )
    parser.add_argument(
        "--openai_key",
        dest="openai_key",
        type=str,
        default="",
        help="openai api key,if you have more than one key,you can use comma"
        " to split them and you can break through the limitation",
    )
    parser.add_argument(
        "--no_limit",
        dest="no_limit",
        action="store_true",
        help="If you are a paying customer you can add it",
    )
    parser.add_argument(
        "--test",
        dest="test",
        action="store_true",
        help="if test we only translat 10 contents you can easily check",
    )
    parser.add_argument(
        "--test_num",
        dest="test_num",
        type=int,
        default=10,
        help="test num for the test",
    )
    parser.add_argument(
        "-m",
        "--model",
        dest="model",
        type=str,
        default="chatgpt",
        choices=["chatgpt", "gpt3"],  # support DeepL later
        help="Which model to use",
    )
    parser.add_argument(
        "--language",
        type=str,
        choices=sorted(LANGUAGES.keys())
        + sorted([k.title() for k in TO_LANGUAGE_CODE.keys()]),
        default="zh-hans",
        help="language to translate to",
    )
    parser.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        help="if program accidentally stop you can use this to resume",
    )
    parser.add_argument(
        "-p",
        "--proxy",
        dest="proxy",
        type=str,
        default="",
        help="use proxy like http://127.0.0.1:7890",
    )
    # args to change api_base
    parser.add_argument(
        "--api_base",
        dest="api_base",
        type=str,
        help="replace base url from openapi",
    )

    options = parser.parse_args()
    NO_LIMIT = options.no_limit
    IS_TEST = options.test
    TEST_NUM = options.test_num
    PROXY = options.proxy
    if PROXY != "":
        os.environ["http_proxy"] = PROXY
        os.environ["https_proxy"] = PROXY

    OPENAI_API_KEY = options.openai_key or env.get("OPENAI_API_KEY")
    RESUME = options.resume
    if not OPENAI_API_KEY:
        raise Exception("Need openai API key, please google how to")
    if not options.book_name.endswith(".epub"):
        raise Exception("please use epub file")
    model = MODEL_DICT.get(options.model, "chatgpt")
    language = options.language
    if options.language in LANGUAGES:
        # use the value for prompt
        language = LANGUAGES.get(language, language)

    # change api_base for issue #42
    model_api_base = options.api_base
    e = BEPUB(
        options.book_name,
        model,
        OPENAI_API_KEY,
        RESUME,
        language=language,
        model_api_base=model_api_base,
    )
    e.make_bilingual_book()
