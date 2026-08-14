import os
import sys
from copy import copy
from pathlib import Path

from bs4 import BeautifulSoup as bs
from rich import print
from tqdm import tqdm

from book_maker.utils import prompt_config_to_kwargs

from .epub_loader import EPUBBookLoader
from .helper import EPUBBookLoaderHelper

# ebooklib item-type constant for a content document; the reused
# EPUBBookLoader helpers gate on this value via `get_type()`.
ITEM_DOCUMENT = 9


class _HTMLItem:
    """Minimal stand-in for an ebooklib document item.

    The paragraph-level machinery inherited from EPUBBookLoader
    (`process_item`, `_count_translatable_paragraphs`) only ever touches
    `get_type()`, `file_name`, and the readable/writable `content`
    attribute, so a single HTML file can masquerade as one epub item.
    """

    def __init__(self, content: bytes, file_name: str):
        self.content = content
        self.file_name = file_name

    def get_type(self):
        return ITEM_DOCUMENT


class _HTMLCollector:
    """Stand-in for the new epub book; `process_item` calls `add_item`."""

    def add_item(self, item):
        pass


class HTMLBookLoader(EPUBBookLoader):
    """Translate a standalone HTML file in place, HTML in / HTML out.

    Reuses EPUBBookLoader's BeautifulSoup traversal, block_size batching
    (the glossary-critical path), and bilingual insertion. Only the epub
    container plumbing (spine/TOC/OPF/zip) is replaced by plain single-file
    read and write.
    """

    def __init__(
        self,
        html_name,
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
    ):
        self.html_name = html_name
        # kept only so any stray inherited reference resolves; the methods
        # that actually use it for epub I/O are all overridden below.
        self.epub_name = html_name
        self.translate_model = model(
            key,
            language,
            api_base=model_api_base,
            context_flag=context_flag,
            context_paragraph_limit=context_paragraph_limit,
            temperature=temperature,
            source_lang=source_lang,
            **prompt_config_to_kwargs(prompt_config),
        )
        self.is_test = is_test
        self.test_num = test_num
        # broader default than epub's "p": Docling HTML puts body text in
        # lists and headings too. --translate-tags overrides this.
        self.translate_tags = "p,li,h1,h2,h3,h4,h5,h6"
        self.exclude_translate_tags = "sup,code"
        self.allow_navigable_strings = False
        self.accumulated_num = 1
        self.translation_style = ""
        self.context_flag = context_flag
        self.helper = EPUBBookLoaderHelper(
            self.translate_model,
            self.accumulated_num,
            self.translation_style,
            self.context_flag,
        )
        self.retranslate = None
        self.exclude_filelist = ""
        self.only_filelist = ""
        self.single_translate = single_translate
        self.block_size = 1
        self.sentence_mode = False
        self.batch_use_flag = False
        self.batch_flag = False
        self.parallel_workers = 1
        self.enable_parallel = False
        from threading import Lock

        self._progress_lock = Lock()
        self._translation_index = 0
        self.set_parallel_workers(parallel_workers)

        try:
            with open(html_name, "rb") as f:
                self.html_content = f.read()
        except Exception as e:
            raise Exception("can not load file") from e

        self.p_to_save = []
        self.resume = resume
        self.bin_path = f"{Path(html_name).parent}/.{Path(html_name).stem}.temp.bin"
        if self.resume:
            self.load_state()

    def _make_new_book(self, book):
        return _HTMLCollector()

    def make_bilingual_book(self):
        self.helper = EPUBBookLoaderHelper(
            self.translate_model,
            self.accumulated_num,
            self.translation_style,
            self.context_flag,
        )

        if self.translate_model._fatal_error_detected:
            print(
                "[bold red]Fatal translation error detected. Aborting book creation.[/bold red]"
            )
            return

        trans_taglist = self.translate_tags.split(",")
        item = _HTMLItem(self.html_content, Path(self.html_name).name)
        new_book = _HTMLCollector()

        all_p_length = self._count_translatable_paragraphs([item], trans_taglist)
        pbar = tqdm(
            total=self.test_num if self.is_test else all_p_length,
            leave=not self.is_test,
        )
        print()
        index = 0
        p_to_save_len = len(self.p_to_save)
        try:
            index = self.process_item(
                item, index, p_to_save_len, pbar, new_book, trans_taglist
            )
            pbar.close()
            name, _ = os.path.splitext(self.html_name)
            with open(f"{name}_bilingual.html", "wb") as f:
                f.write(item.content)
        except KeyboardInterrupt:
            print("you can resume it next time")
            self._save_progress()
            self._save_temp_book()
            sys.exit(0)
        except Exception as e:
            print(f"Error: {e}")
            print("Saving progress...")
            self._save_progress()
            self._save_temp_book()
            sys.exit(0)

    def _save_temp_book(self):
        trans_taglist = self.translate_tags.split(",")
        soup = bs(self.html_content, "html.parser")
        p_list = soup.findAll(trans_taglist)
        if self.allow_navigable_strings:
            p_list.extend(soup.findAll(text=True))
        p_to_save_len = len(self.p_to_save)
        index = 0
        for p in p_list:
            if index >= p_to_save_len:
                break
            if not p.text or self._is_special_text(p.text):
                continue
            if self._is_content_only_excluded_tags(p):
                continue
            self._insert_trans_preserving_tags(
                p,
                self.p_to_save[index],
                self.translation_style,
                self.single_translate,
            )
            index += 1
        name, _ = os.path.splitext(self.html_name)
        with open(f"{name}_bilingual_temp.html", "wb") as f:
            f.write(soup.encode())
