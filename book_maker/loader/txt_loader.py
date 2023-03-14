import sys
from pathlib import Path

from book_maker.utils import prompt_config_to_kwargs

from .base_loader import BaseBookLoader


class TXTBookLoader(BaseBookLoader):
    def __init__(
        self,
        txt_name,
        model,
        key,
        resume,
        language,
        batch_size,
        translate_tags,
        allow_navigable_strings,
        model_api_base=None,
        is_test=False,
        test_num=5,
        accumulated_num=1,
        prompt_template=None,
        prompt_config=None,
    ):
        self.txt_name = txt_name
        self.translate_model = model(
            key,
            language,
            api_base=model_api_base,
            **prompt_config_to_kwargs(prompt_config),
        )
        self.is_test = is_test
        self.p_to_save = []
        self.bilingual_result = []
        self.bilingual_temp_result = []
        self.test_num = test_num
        self.batch_size = batch_size

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
            sliced_list = [
                self.origin_book[i : i + self.batch_size]
                for i in range(0, len(self.origin_book), self.batch_size)
            ]
            for i in sliced_list:
                batch_text = "".join(i)
                if self._is_special_text(batch_text):
                    continue
                if self.resume and index < p_to_save_len:
                    pass
                else:
                    try:
                        temp = self.translate_model.translate(batch_text)
                    except Exception as e:
                        print(str(e))
                        raise Exception("Something is wrong when translate")
                    self.p_to_save.append(temp)
                    self.bilingual_result.append(batch_text)
                    self.bilingual_result.append(temp)
                index += self.batch_size
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
        index = 0
        sliced_list = [
            self.origin_book[i : i + self.batch_size]
            for i in range(0, len(self.origin_book), self.batch_size)
        ]

        for i in range(len(sliced_list)):
            batch_text = "".join(sliced_list[i])
            self.bilingual_temp_result.append(batch_text)
            if self._is_special_text(self.origin_book[i]):
                continue
            if index < len(self.p_to_save):
                self.bilingual_temp_result.append(self.p_to_save[index])
            index += 1

        self.save_file(
            f"{Path(self.txt_name).parent}/{Path(self.txt_name).stem}_bilingual_temp.txt",
            self.bilingual_temp_result,
        )

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
