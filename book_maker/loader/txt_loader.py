import sys

from .base_loader import BaseBookLoader
from pathlib import Path


class TXTBookLoader(BaseBookLoader):
    def __init__(
        self,
        txt_name,
        model,
        key,
        resume,
        language,
        translate_tags,
        allow_navigable_strings,
        model_api_base=None,
        is_test=False,
        test_num=5,
    ):
        self.txt_name = txt_name
        self.translate_model = model(key, language, model_api_base)
        self.is_test = is_test
        self.p_to_save = []
        self.bilingual_result = []
        self.test_num = test_num

        try:
            with open(f"{txt_name}", "r", encoding="utf-8") as f:
                self.origin_book = f.read().split("\n")

        except Exception:
            raise Exception("can not load file")

        self.resume = resume
        self.bin_path = f"{Path(txt_name).parent}/.{Path(txt_name).stem}.temp.bin"
        if self.resume:
            self.load_state()

    @staticmethod
    def _is_special_text(text):
        return text.isdigit() or text.isspace() or len(text) == 0

    def _make_new_book(self, book):
        pass

    def make_bilingual_book(self):
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
                    self.bilingual_result.append(i)
                    self.bilingual_result.append(temp)
                index += 1
                if self.is_test and index > self.test_num:
                    break

            self.save_file(
                f"{Path(self.txt_name).parent}/{Path(self.txt_name).stem}_bilingual.txt",
                self.bilingual_result,
            )

        except (KeyboardInterrupt, Exception) as e:
            print(e)
            print("you can resume it next time")
            self._save_progress()
            self._save_temp_book()
            sys.exit(0)

    def _save_temp_book(self):
        """
        TODO
        """

    def _save_progress(self):
        try:
            with open(self.bin_path, "w") as f:
                f.write("\n".join(self.p_to_save))
        except:
            raise Exception("can not save resume file")

    def load_state(self):
        try:
            with open(self.bin_path, "r", encoding="utf-8") as f:
                self.p_to_save = f.read().split("\n")
        except Exception:
            raise Exception("can not load resume file")

    def save_file(self, book_path, content):
        try:
            with open(book_path, "w", encoding="utf-8") as f:
                f.write("\n".join(content))
        except:
            raise Exception("can not save file")
