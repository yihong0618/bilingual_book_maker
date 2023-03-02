import argparse
import time
from abc import abstractmethod
from copy import copy
from os import environ as env

import openai
import requests
from bs4 import BeautifulSoup as bs
from ebooklib import epub
from rich import print
import multiprocessing as mp

NO_LIMIT = False
IS_TEST = False


class Base:
    def __init__(self, key):
        pass

    @abstractmethod
    def translate(self, text):
        pass


class GPT3(Base):
    def __init__(self, key):
        self.api_key = key
        self.api_url = "https://api.openai.com/v1/completions"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
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

    def translate(self, text):
        print(text)
        self.data["prompt"] = f"Please help me to translate，`{text}` to Chinese"
        r = self.session.post(self.api_url, headers=self.headers, json=self.data)
        if not r.ok:
            return text
        t_text = r.json().get("choices")[0].get("text", "").strip()
        print(t_text)
        return t_text


class DeepL(Base):
    def __init__(self, session, key):
        super().__init__(session, key)

    def translate(self, text):
        return super().translate(text)


class ChatGPT(Base):
    def __init__(self, key):
        super().__init__(key)
        self.key = key

    def translate(self, text):
        print(text)
        openai.api_key = self.key
        try:
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "user",
                        # english prompt here to save tokens
                        "content": f"Please help me to translate，`{text}` to Chinese, please return only translated content not include the origin text",
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
            print(str(e), "will sleep 60 seconds")
            # TIME LIMIT for open api please pay
            time.sleep(60)
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "user",
                        "content": f"Please help me to translate，`{text}` to Simplified Chinese, please return only translated content not include the origin text",
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
    def __init__(self, epub_name, model, key):
        self.epub_name = epub_name
        self.translate_model = model(key)
        self.origin_book = epub.read_epub(self.epub_name)
        self.test_limit_index = 0
    
    def translate_p(self, p):
        if p.string and not p.string.isdigit():
            new_p = copy(p)
            new_p.string = self.translate_model.translate(p.string)
            p.insert_after(new_p)
    
    def translate_item(self, item):
        soup = bs(item.content, "html.parser")
        p_list = soup.findAll("p")
        is_test_done = IS_TEST and self.test_limit_index > 20
        for p in p_list:
            if not is_test_done:
                self.translate_p(p)
                self.test_limit_index += 1
        item.content = soup.prettify().encode()
        return item
    
    def make_bilingual_book(self):
        new_book = epub.EpubBook()
        new_book.metadata = self.origin_book.metadata
        new_book.spine = self.origin_book.spine
        new_book.toc = self.origin_book.toc
        all_items = list(self.origin_book.get_items())
        # we just translate tag p
        all_p_length = sum(
            [len(bs(i.content, "html.parser").findAll("p")) for i in all_items]
        )
        print("TODO need process bar here: " + str(all_p_length))
        pool = mp.Pool()
        processed_items = pool.map(self.translate_item, all_items)
        pool.close()
        pool.join()
        for item in processed_items:
            new_book.add_item(item)

        for item in new_book.spine:
            new_item = new_book.get_item_with_href(item.href)
            new_book.add_item(new_item)
            new_book.spine.remove(item)
            new_book.spine.append(new_item)

        name = self.epub_name.split(".")[0]
        epub.write_epub(f"{name}_bilingual.epub", new_book, {})

    def make_bilingual_book(self):
        new_book = epub.EpubBook()
        new_book.metadata = self.origin_book.metadata
        new_book.spine = self.origin_book.spine
        new_book.toc = self.origin_book.toc
        
        self.origin_book.spine
        # we just translate tag 
        with mp.Pool() as pool:
            translated_p_list = pool.map(
                lambda x: (x[0], self.translate_model.translate(x[1])),
                [(i, p.string) for i in self.origin_book.get_items()
                    if i.get_type() == 9
                    for p in bs(i.content, "html.parser").findAll("p")
                    if p.string and not p.string.isdigit()]
                )
        print("TODO need process bar here: " + len(translated_p_list))
        
         # Update the "p" tags with their translations
        index = 0
        for i, translated_p in translated_p_list:
            soup = bs(i.content, "html.parser")
            p_list = soup.findAll("p")
            for j, p in enumerate(p_list):
                if p.string == translated_p[j][0]:
                    new_p = copy(p)
                    new_p.string = translated_p[j][1]
                    p.insert_after(new_p)
                    index += 1
                    if IS_TEST and index > 20:
                        break
            i.content = soup.prettify().encode()
            new_book.add_item(i)
            if IS_TEST and index > 20:
                break

        # Add remaining items to the new book
        for i in self.origin_book.get_items():
            if i.get_type() != 9:
                new_book.add_item(i)
        
        name = self.epub_name.split(".")[0]
        epub.write_epub(f"{name}_bilingual.epub", new_book, {})


if __name__ == "__main__":
    MODEL_DICT = {"gpt3": GPT3, "chatgpt": ChatGPT}
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--book_name",
        dest="book_name",
        type=str,
        help="your epub book name",
    )
    parser.add_argument(
        "--openai_key",
        dest="openai_key",
        type=str,
        default="",
        help="openai api key",
    )
    parser.add_argument(
        "--no_limit",
        dest="no_limit",
        action="store_true",
        help="if you pay add it",
    )
    parser.add_argument(
        "--test",
        dest="test",
        action="store_true",
        help="if test we only translat 20 contents you can easily check",
    )
    parser.add_argument(
        "-m",
        "--model",
        dest="model",
        type=str,
        default="chatgpt",
        choices=["chatgpt", "gpt3"],  # support DeepL later
        help="Use which model",
    )
    options = parser.parse_args()
    NO_LIMIT = options.no_limit
    IS_TEST = options.test
    OPENAI_API_KEY = options.openai_key or env.get("OPENAI_API_KEY")
    if not OPENAI_API_KEY:
        raise Exception("Need openai API key, please google how to")
    if not options.book_name.endswith(".epub"):
        raise Exception("please use epub file")
    model = MODEL_DICT.get(options.model, "chatgpt")
    e = BEPUB(options.book_name, model, OPENAI_API_KEY)
    e.make_bilingual_book()
