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
        model_api_base=None,
        is_test=False,
        test_num=5,
        prompt_config=None,
        single_translate=False,
        context_flag=False,
        context_paragraph_limit=0,
        temperature=1.0,
        source_lang="auto",
        parallel_workers=1,
    ) -> None:
        self.txt_name = txt_name
        self.translate_model = model(
            key,
            language,
            api_base=model_api_base,
            temperature=temperature,
            source_lang=source_lang,
            **prompt_config_to_kwargs(prompt_config),
        )
        self.is_test = is_test
        self.p_to_save = []
        self.bilingual_result = []
        self.bilingual_temp_result = []
        self.test_num = test_num
        self.batch_size = 10
        self.single_translate = single_translate
        self.parallel_workers = max(1, parallel_workers)

        try:
            with open(f"{txt_name}", encoding="utf-8") as f:
                self.origin_book = f.read().splitlines()

        except Exception as e:
            raise Exception("can not load file") from e

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
        try:
            self.bilingual_result = self._render_bilingual_result(
                translate_missing=True
            )

            out_path = (
                f"{Path(self.txt_name).parent}/"
                f"{Path(self.txt_name).stem}_bilingual.txt"
            )
            self.save_file(out_path, self.bilingual_result)
            self.announce_saved_book(out_path)

        except KeyboardInterrupt:
            print("Interrupted. Saving progress so you can resume later.")
            print("you can resume it next time")
            self._save_progress()
            self._save_temp_book()
            sys.exit(0)
        except Exception as e:
            print(e)
            print("you can resume it next time")
            self._save_progress()
            self._save_temp_book()
            raise

    def _render_bilingual_result(self, translate_missing):
        result = []
        batch_index = 0
        translated_count = 0
        cursor = 0

        while cursor < len(self.origin_book):
            remaining = self.test_num - translated_count if self.is_test else None
            if self.is_test and remaining <= 0:
                break

            batch_len = (
                min(self.batch_size, remaining) if remaining else self.batch_size
            )
            batch_lines = self.origin_book[cursor : cursor + batch_len]
            cursor += batch_len

            # fix the format thanks https://github.com/tudoujunha
            batch_text = "\n".join(batch_lines)
            if self._is_special_text(batch_text):
                translated_count += len(batch_lines)
                continue

            if batch_index < len(self.p_to_save):
                translated_text = self.p_to_save[batch_index]
            elif translate_missing:
                try:
                    translated_text = self.translate_model.translate(batch_text)
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    print(e)
                    raise Exception("Something is wrong when translate") from e
                self.p_to_save.append(translated_text)
            else:
                translated_text = None

            if not self.single_translate:
                result.append(batch_text)
            if translated_text is not None:
                result.append(translated_text)

            translated_count += len(batch_lines)
            batch_index += 1

        return result

    def _save_temp_book(self):
        self.bilingual_temp_result = self._render_bilingual_result(
            translate_missing=False
        )

        self.save_file(
            f"{Path(self.txt_name).parent}/{Path(self.txt_name).stem}_bilingual_temp.txt",
            self.bilingual_temp_result,
        )

