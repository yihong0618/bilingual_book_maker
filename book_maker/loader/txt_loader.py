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
        batch_size=0,
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
        self.batch_size = batch_size if batch_size is not None and batch_size > 0 else 1
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

    def _batch_has_content(self, batch_lines):
        """Check if batch has at least one line with actual content."""
        for line in batch_lines:
            if not self._is_special_text(line):
                return True
        return False

    def _make_new_book(self, book):
        pass

    def make_bilingual_book(self):
        index = 0
        p_to_save_len = len(self.p_to_save)
        trans_index = 0  # Track translated paragraphs separately

        try:
            # Process lines, keeping track of which ones need translation
            lines_to_translate = [
                line
                for line in self.origin_book
                if line.strip() and not self._is_special_text(line)
            ]

            # Create batches for translation
            sliced_list = [
                lines_to_translate[i : i + self.batch_size]
                for i in range(0, len(lines_to_translate), self.batch_size)
            ]

            # Translate batches and store results
            translated_results = []
            for batch_lines in sliced_list:
                if not batch_lines:
                    continue

                if trans_index >= self.test_num and self.is_test:
                    # Stop translating but continue processing original file
                    break

                try:
                    temp_list = self.translate_model.translate_list(batch_lines)
                    translated_results.extend(temp_list)
                except Exception as e:
                    print(e)
                    raise Exception("Something is wrong when translate") from e

                trans_index += len(batch_lines)
                if self.is_test and trans_index >= self.test_num:
                    break

            # Reconstruct output preserving original structure
            trans_idx = 0
            for line in self.origin_book:
                if line.strip() and not self._is_special_text(line):
                    # This line should be translated
                    if trans_idx < len(translated_results):
                        # Use translated version
                        if not self.single_translate:
                            print(line)
                            print(translated_results[trans_idx])
                            print()
                            self.bilingual_result.append(line)
                        self.bilingual_result.append(translated_results[trans_idx])
                        self.p_to_save.append(translated_results[trans_idx])
                        trans_idx += 1
                    else:
                        # No translation (beyond test limit), keep original
                        self.bilingual_result.append(line)
                else:
                    # Preserve empty/special lines as-is
                    self.bilingual_result.append(line)

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
            with open(self.bin_path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.p_to_save))
        except Exception as e:
            raise Exception("can not save resume file") from e

    def load_state(self):
        try:
            with open(self.bin_path, encoding="utf-8") as f:
                self.p_to_save = f.read().splitlines()
        except Exception as e:
            raise Exception("can not load resume file") from e

    def save_file(self, book_path, content):
        try:
            with open(book_path, "w", encoding="utf-8") as f:
                f.write("\n".join(content))
        except Exception as e:
            raise Exception("can not save file") from e
