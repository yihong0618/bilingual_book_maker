import sys
from pathlib import Path

from book_maker.utils import prompt_config_to_kwargs

from .base_loader import BaseBookLoader

import fitz

from ebooklib import epub


class PDFBookLoader(BaseBookLoader):
    def __init__(
        self,
        pdf_name,
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
        if fitz is None:
            raise Exception("PyMuPDF (fitz) is required to use PDF loader")

        self.pdf_name = pdf_name
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
            doc = fitz.open(self.pdf_name)
            lines = []
            for page in doc:
                text = page.get_text("text")
                if not text:
                    continue
                lines.extend(text.splitlines())
            self.origin_book = lines
        except Exception as e:
            raise Exception("can not load file") from e

        self.resume = resume
        self.bin_path = f"{Path(pdf_name).parent}/.{Path(pdf_name).stem}.temp.bin"
        if self.resume:
            self.load_state()

    def _make_new_book(self, book):
        pass

    def _try_create_epub(self):
        """Try to create an EPUB file from translated content.

        The EPUB is created from the `self.bilingual_result` list which alternates
        original and translated strings. If EPUB creation fails for any reason,
        this function will log the error and leave the TXT fallback intact.
        """
        if epub is None:
            # ebooklib not installed; skip EPUB generation
            return False

        if not self.bilingual_result:
            return False

        try:
            book = epub.EpubBook()
            title = Path(self.pdf_name).stem
            # Minimal metadata
            try:
                book.set_identifier(title)
                book.set_title(title)
                book.set_language(
                    self.translate_model.language
                    if hasattr(self.translate_model, "language")
                    else "en"
                )
            except Exception:
                # be tolerant about metadata API differences
                pass

            chapters = []
            # build chapters from bilingual_result (pairs)
            for i in range(0, len(self.bilingual_result), 2):
                orig = self.bilingual_result[i]
                trans = (
                    self.bilingual_result[i + 1]
                    if i + 1 < len(self.bilingual_result)
                    else ""
                )
                # basic html content: original then translated
                content = ""
                if orig:
                    content += (
                        '<div class="original">'
                        + "<p>"
                        + orig.replace("\n", "<br/>")
                        + "</p></div>"
                    )
                if trans:
                    content += (
                        '<div class="translation">'
                        + "<p>"
                        + trans.replace("\n", "<br/>")
                        + "</p></div>"
                    )

                chap = epub.EpubHtml(
                    title=f"part_{i//2}",
                    file_name=f"index_split_{i//2:03d}.xhtml",
                    lang=(
                        book.get_metadata("DC", "language")[0][0]
                        if book.get_metadata("DC", "language")
                        else None
                    ),
                )
                chap.content = content
                book.add_item(chap)
                chapters.append(chap)

            # table of contents and spine
            book.toc = tuple(chapters)
            book.spine = ["nav"] + chapters

            # add navigation files
            book.add_item(epub.EpubNcx())
            book.add_item(epub.EpubNav())

            out_path = f"{Path(self.pdf_name).parent}/{Path(self.pdf_name).stem}_bilingual.epub"
            epub.write_epub(out_path, book)
            return True
        except Exception as e:
            print(f"create epub failed: {e}")
            return False

    def make_bilingual_book(self):
        index = 0
        p_to_save_len = len(self.p_to_save)

        try:
            sliced_list = [
                self.origin_book[i : i + self.batch_size]
                for i in range(0, len(self.origin_book), self.batch_size)
            ]
            for i in sliced_list:
                # fix the format thanks https://github.com/tudoujunha
                batch_text = "\n".join(i)
                if not batch_text.strip():
                    continue
                if not self.resume or index >= p_to_save_len:
                    try:
                        temp = self.translate_model.translate(batch_text)
                    except Exception as e:
                        print(e)
                        raise Exception("Something is wrong when translate") from e
                    self.p_to_save.append(temp)
                    if not self.single_translate:
                        self.bilingual_result.append(batch_text)
                    self.bilingual_result.append(temp)
                index += self.batch_size
                if self.is_test and index > self.test_num:
                    break

            txt_out = (
                f"{Path(self.pdf_name).parent}/{Path(self.pdf_name).stem}_bilingual.txt"
            )
            self.save_file(txt_out, self.bilingual_result)

            # try to create an EPUB alongside the TXT fallback; if EPUB fails we still keep the TXT file
            epub_ok = self._try_create_epub()
            if epub_ok:
                print(f"created epub: {Path(self.pdf_name).stem}_bilingual.epub")
            else:
                print(
                    "epub creation skipped or failed; bilingual text saved to txt fallback"
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
            if index < len(self.p_to_save):
                self.bilingual_temp_result.append(self.p_to_save[index])
            index += 1

        self.save_file(
            f"{Path(self.pdf_name).parent}/{Path(self.pdf_name).stem}_bilingual_temp.txt",
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
