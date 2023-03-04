
import os
import pickle

from copy import copy
from rich import print
from pathlib import Path
from ebooklib import epub
from bs4 import BeautifulSoup as bs

from abstract import FileEngineBase, TranslateEngineBase


class BEPUB(FileEngineBase):
    def __init__(self, engine: TranslateEngineBase, book_name: str, resume: bool, is_test: bool, test_number: int):
        self.is_test = is_test
        self.test_number = test_number
        self.epub_name = book_name
        self.new_epub = epub.EpubBook()
        self.translate_model = engine
        self.origin_book = epub.read_epub(self.epub_name)
        self.p_to_save = []
        self.resume = resume
        self.bin_path = f"{Path(book_name).parent}/.{Path(book_name).stem}.temp.bin"
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
        all_p_length = sum(
            [len(bs(i.content, "html.parser").findAll("p")) for i in all_items]
        )
        print("TODO need process bar here: " + str(all_p_length))
        index = 0
        p_to_save_len = len(self.p_to_save)
        try:
            for i in self.origin_book.get_items():
                if i.get_type() == 9:
                    soup = bs(i.content, "html.parser")
                    p_list = soup.findAll("p")
                    is_test_done = self.is_test and index > self.test_number
                    for p in p_list:
                        if is_test_done or not p.text or self._is_special_text(p.text):
                            continue
                        new_p = copy(p)
                        # TODO banch of p to translate then combine
                        # PR welcome here
                        if self.resume and index < p_to_save_len:
                            new_p.string = self.p_to_save[index]
                        else:
                            new_p.string = self.translate_model.translate(
                                p.text)
                            self.p_to_save.append(new_p.text)
                        p.insert_after(new_p)
                        index += 1
                        if self.is_test and index > self.test_number:
                            break
                    i.content = soup.prettify().encode()
                new_book.add_item(i)
            name = self.epub_name.split(".")[0]
            epub.write_epub(f"{name}_bilingual.epub", new_book, {})
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


class BText(FileEngineBase):
    def __init__(self, engine: TranslateEngineBase, book_name: str, resume: bool, is_test: bool, test_number: int):
        self.is_test = is_test
        self.test_number = test_number
        self.book_name = book_name
        self.translate_model = engine
        self.origin_book = self.load_file(self.book_name).split("\n")
        self.p_to_save = []
        self.resume = resume
        self.bin_path = f"{os.path.abspath(self.book_name)}.bin.temp"
        if self.resume:
            self.load_state()

    @staticmethod
    def _is_special_text(text):
        return text.isdigit() or text.isspace()

    def make_bilingual_book(self):
        all_p_length = len(self.origin_book)
        print("TODO need process bar here: " + str(all_p_length))
        index = 0
        p_to_save_len = len(self.p_to_save)
        try:
            for i in self.origin_book:
                if self._is_special_text(i):
                    continue
                if self.resume and index < p_to_save_len:
                    pass
                else:
                    temp = self.translate_model.translate(i)
                    self.p_to_save.append(temp)
                index += 1
                if self.is_test and index > self.test_number:
                    break
            name = self.book_name.split(".")[0]
            self.save_file(f"{name}_bilingual.txt", self.p_to_save)
        except (KeyboardInterrupt, Exception) as e:
            print(e)
            print("you can resume it next time")
            self.save_progress()
            exit(0)

    def load_file(self, book_path):
        try:
            with open(book_path, "r", encoding="utf-8") as f:
                return f.read()
        except:
            raise Exception("can not load file")

    def save_file(self, book_path, content):
        try:
            with open(book_path, "w", encoding="utf-8") as f:
                f.write("\n".join(content))
        except:
            raise Exception("can not save file")

    def load_state(self):
        try:
            with open(self.bin_path, "r", encoding="utf-8") as f:
                self.p_to_save = f.read().split("\n")
        except:
            raise Exception("can not load resume file")

    def save_progress(self):
        try:
            with open(self.bin_path, "w") as f:
                f.write("\n".join(self.p_to_save))
        except:
            raise Exception("can not save resume file")
