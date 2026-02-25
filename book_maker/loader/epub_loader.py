import os
import pickle
import string
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import copy
from pathlib import Path
import traceback
from threading import Lock

from bs4 import BeautifulSoup as bs
from bs4 import Tag
from bs4.element import NavigableString
from ebooklib import ITEM_DOCUMENT, epub
from rich import print
from tqdm import tqdm

from book_maker.utils import num_tokens_from_text, prompt_config_to_kwargs

from .base_loader import BaseBookLoader
from .helper import EPUBBookLoaderHelper, is_text_link, not_trans


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
        prompt_config=None,
        single_translate=False,
        context_flag=False,
        context_paragraph_limit=0,
        temperature=1.0,
        source_lang="auto",
        parallel_workers=1,
    ):
        self.epub_name = epub_name
        self.new_epub = epub.EpubBook()
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
        self.translate_tags = "p"
        self.exclude_translate_tags = "sup"
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
        self.block_size = -1
        self.batch_use_flag = False
        self.batch_flag = False
        self.parallel_workers = 1
        self.enable_parallel = False
        self._progress_lock = Lock()
        self._translation_index = 0
        self.set_parallel_workers(parallel_workers)

        # monkey patch for # 173
        def _write_items_patch(obj):
            for item in obj.book.get_items():
                if isinstance(item, epub.EpubNcx):
                    obj.out.writestr(
                        "%s/%s" % (obj.book.FOLDER_NAME, item.file_name), obj._get_ncx()
                    )
                elif isinstance(item, epub.EpubNav):
                    obj.out.writestr(
                        "%s/%s" % (obj.book.FOLDER_NAME, item.file_name),
                        obj._get_nav(item),
                    )
                elif item.manifest:
                    obj.out.writestr(
                        "%s/%s" % (obj.book.FOLDER_NAME, item.file_name), item.content
                    )
                else:
                    obj.out.writestr("%s" % item.file_name, item.content)

        def _check_deprecated(obj):
            pass

        epub.EpubWriter._write_items = _write_items_patch
        epub.EpubReader._check_deprecated = _check_deprecated

        try:
            self.origin_book = epub.read_epub(self.epub_name)
        except Exception:
            # tricky monkey patch for #71 if you don't know why please check the issue and ignore this
            # when upstream change will TODO fix this
            def _load_spine(obj):
                spine = obj.container.find("{%s}%s" % (epub.NAMESPACES["OPF"], "spine"))

                obj.book.spine = [
                    (t.get("idref"), t.get("linear", "yes")) for t in spine
                ]
                obj.book.set_direction(spine.get("page-progression-direction", None))

            epub.EpubReader._load_spine = _load_spine
            self.origin_book = epub.read_epub(self.epub_name)

        self.p_to_save = []
        self.resume = resume
        self.bin_path = f"{Path(epub_name).parent}/.{Path(epub_name).stem}.temp.bin"
        if self.resume:
            self.load_state()

    @staticmethod
    def _is_special_text(text):
        return (
            text.isdigit()
            or text.isspace()
            or is_text_link(text)
            or all(char in string.punctuation for char in text)
        )

    def _make_new_book(self, book):
        new_book = epub.EpubBook()
        allowed_ns = set(epub.NAMESPACES.keys()) | set(epub.NAMESPACES.values())

        for namespace, metas in book.metadata.items():
            # Only keep namespaces recognized by ebooklib
            if namespace not in allowed_ns:
                continue

            if isinstance(metas, dict):
                entries = (
                    (name, value, others)
                    for name, values in metas.items()
                    for value, others in (
                        (item if isinstance(item, tuple) else (item, None))
                        for item in values
                    )
                )
            else:
                entries = metas

            for entry in entries:
                if not entry:
                    continue

                if isinstance(entry, tuple):
                    if len(entry) == 3:
                        name, value, others = entry
                    elif len(entry) == 2:
                        name, value = entry
                        others = None
                    else:
                        continue
                else:
                    # Unexpected metadata format; skip gracefully
                    continue

                # `others` can be {} or None
                if others:
                    new_book.add_metadata(namespace, name, value, others)
                else:
                    new_book.add_metadata(namespace, name, value)

        new_book.spine = book.spine
        new_book.toc = self._fix_toc_uids(book.toc)
        return new_book

    def _fix_toc_uids(self, toc, counter=None):
        """Fix TOC items that have uid=None to prevent TypeError when writing NCX."""
        if counter is None:
            counter = [0]  # Use list to allow mutation in nested calls

        fixed_toc = []
        for item in toc:
            if isinstance(item, tuple):
                # Section with sub-items: (Section, [sub-items])
                section, sub_items = item
                if hasattr(section, "uid") and section.uid is None:
                    section.uid = f"navpoint-{counter[0]}"
                    counter[0] += 1
                fixed_sub_items = self._fix_toc_uids(sub_items, counter)
                fixed_toc.append((section, fixed_sub_items))
            elif hasattr(item, "uid"):
                # Link or EpubHtml item
                if item.uid is None:
                    item.uid = f"navpoint-{counter[0]}"
                    counter[0] += 1
                fixed_toc.append(item)
            else:
                fixed_toc.append(item)

        return fixed_toc

    def _extract_paragraph(self, p):
        for p_exclude in self.exclude_translate_tags.split(","):
            # for issue #280
            if type(p) is NavigableString:
                continue
            for pt in p.find_all(p_exclude):
                pt.extract()
        return p

    def _process_paragraph(self, p, new_p, index, p_to_save_len, thread_safe=False):
        if self.resume and index < p_to_save_len:
            p.string = self.p_to_save[index]
            new_p.string = self.p_to_save[index]  # Fix: also update new_p to cached translation
        else:
            t_text = ""
            if self.batch_flag:
                self.translate_model.add_to_batch_translate_queue(index, new_p.text)
            elif self.batch_use_flag:
                t_text = self.translate_model.batch_translate(index)
            else:
                t_text = self.translate_model.translate(new_p.text)
            if t_text is None:
                raise RuntimeError(
                    "`t_text` is None: your translation model is not working as expected. Please check your translation model configuration."
                )
            if type(p) is NavigableString:
                new_p = t_text
                self.p_to_save.append(new_p)
            else:
                new_p.string = t_text
                self.p_to_save.append(new_p.text)

        self.helper.insert_trans(
            p, new_p.string, self.translation_style, self.single_translate
        )
        index += 1

        if thread_safe:
            with self._progress_lock:
                if index % 20 == 0:
                    self._save_progress()
        else:
            if index % 20 == 0:
                self._save_progress()
        return index

    def _process_combined_paragraph(
        self, p_block, index, p_to_save_len, thread_safe=False
    ):
        text = []

        for p in p_block:
            if self.resume and index < p_to_save_len:
                p.string = self.p_to_save[index]
            else:
                p_text = p.text.rstrip()
                text.append(p_text)

            if self.is_test and index >= self.test_num:
                break

            index += 1

        if len(text) > 0:
            translated_text = self.translate_model.translate("\n".join(text))
            translated_text = translated_text.split("\n")
            text_len = len(translated_text)

            for i in range(text_len):
                t = translated_text[i]

                if i >= len(p_block):
                    p = p_block[-1]
                else:
                    p = p_block[i]

                if type(p) is NavigableString:
                    p = t
                else:
                    p.string = t

                self.helper.insert_trans(
                    p, p.string, self.translation_style, self.single_translate
                )

        if thread_safe:
            with self._progress_lock:
                self._save_progress()
        else:
            self._save_progress()
        return index

    def translate_paragraphs_acc(self, p_list, send_num):
        count = 0
        wait_p_list = []
        for i in range(len(p_list)):
            p = p_list[i]
            print(f"translating {i}/{len(p_list)}")
            temp_p = copy(p)

            for p_exclude in self.exclude_translate_tags.split(","):
                # for issue #280
                if type(p) is NavigableString:
                    continue
                for pt in temp_p.find_all(p_exclude):
                    pt.extract()

            if any(
                [not p.text, self._is_special_text(temp_p.text), not_trans(temp_p.text)]
            ):
                if i == len(p_list) - 1:
                    self.helper.deal_old(wait_p_list, self.single_translate)
                continue
            length = num_tokens_from_text(temp_p.text)
            if length > send_num:
                self.helper.deal_new(p, wait_p_list, self.single_translate)
                continue
            if i == len(p_list) - 1:
                if count + length < send_num:
                    wait_p_list.append(p)
                    self.helper.deal_old(wait_p_list, self.single_translate)
                else:
                    self.helper.deal_new(p, wait_p_list, self.single_translate)
                break
            if count + length < send_num:
                count += length
                wait_p_list.append(p)
            else:
                self.helper.deal_old(wait_p_list, self.single_translate)
                wait_p_list.append(p)
                count = length

    def get_item(self, book, name):
        for item in book.get_items():
            if item.file_name == name:
                return item

    def find_items_containing_string(self, book, search_string):
        matching_items = []

        for item in book.get_items_of_type(ITEM_DOCUMENT):
            content = item.get_content()
            soup = bs(content, "html.parser")
            if search_string in soup.get_text():
                matching_items.append(item)

        return matching_items

    def retranslate_book(self, index, p_to_save_len, pbar, trans_taglist, retranslate):
        complete_book_name = retranslate[0]
        fixname = retranslate[1]
        fixstart = retranslate[2]
        fixend = retranslate[3]

        if fixend == "":
            fixend = fixstart

        name_fix = complete_book_name

        complete_book = epub.read_epub(complete_book_name)

        if fixname == "":
            fixname = self.find_items_containing_string(complete_book, fixstart)[
                0
            ].file_name
            print(f"auto find fixname: {fixname}")

        new_book = self._make_new_book(complete_book)

        complete_item = self.get_item(complete_book, fixname)
        if complete_item is None:
            return

        ori_item = self.get_item(self.origin_book, fixname)
        if ori_item is None:
            return

        content_complete = complete_item.content
        content_ori = ori_item.content
        soup_complete = bs(content_complete, "html.parser")
        soup_ori = bs(content_ori, "html.parser")

        p_list_complete = soup_complete.findAll(trans_taglist)
        p_list_ori = soup_ori.findAll(trans_taglist)

        target = None
        tagl = []

        # extract from range
        find_end = False
        find_start = False
        for tag in p_list_complete:
            if find_end:
                tagl.append(tag)
                break

            if fixend in tag.text:
                find_end = True
            if fixstart in tag.text:
                find_start = True

            if find_start:
                if not target:
                    target = tag.previous_sibling
                tagl.append(tag)

        for t in tagl:
            t.extract()

        flag = False
        extract_p_list_ori = []
        for p in p_list_ori:
            if fixstart in p.text:
                flag = True
            if flag:
                extract_p_list_ori.append(p)
            if fixend in p.text:
                break

        for t in extract_p_list_ori:
            if target:
                target.insert_after(t)
                target = t

        for item in complete_book.get_items():
            if item.file_name != fixname:
                new_book.add_item(item)
        if soup_complete:
            complete_item.content = soup_complete.encode()

        index = self.process_item(
            complete_item,
            index,
            p_to_save_len,
            pbar,
            new_book,
            trans_taglist,
            fixstart,
            fixend,
        )
        epub.write_epub(f"{name_fix}", new_book, {})

    def has_nest_child(self, element, trans_taglist):
        if isinstance(element, Tag):
            for child in element.children:
                if child.name in trans_taglist:
                    return True
                if self.has_nest_child(child, trans_taglist):
                    return True
        return False

    def filter_nest_list(self, p_list, trans_taglist):
        filtered_list = [p for p in p_list if not self.has_nest_child(p, trans_taglist)]
        return filtered_list

    def process_item(
        self,
        item,
        index,
        p_to_save_len,
        pbar,
        new_book,
        trans_taglist,
        fixstart=None,
        fixend=None,
    ):
        if self.only_filelist != "" and item.file_name not in self.only_filelist.split(
            ","
        ):
            return index
        elif self.only_filelist == "" and item.file_name in self.exclude_filelist.split(
            ","
        ):
            new_book.add_item(item)
            return index

        if not os.path.exists("log"):
            os.makedirs("log")

        content = item.content
        soup = bs(content, "html.parser")
        p_list = soup.findAll(trans_taglist)

        p_list = self.filter_nest_list(p_list, trans_taglist)

        if self.retranslate:
            new_p_list = []

            if fixstart is None or fixend is None:
                return

            start_append = False
            for p in p_list:
                text = p.get_text()
                if fixstart in text or fixend in text or start_append:
                    start_append = True
                    new_p_list.append(p)
                if fixend in text:
                    p_list = new_p_list
                    break

        if self.allow_navigable_strings:
            p_list.extend(soup.findAll(text=True))

        send_num = self.accumulated_num
        if send_num > 1:
            with open("log/buglog.txt", "a") as f:
                print(f"------------- {item.file_name} -------------", file=f)

            print("------------------------------------------------------")
            print(f"dealing {item.file_name} ...")
            self.translate_paragraphs_acc(p_list, send_num)
        else:
            is_test_done = self.is_test and index > self.test_num
            p_block = []
            block_len = 0
            for p in p_list:
                if is_test_done:
                    break
                if not p.text or self._is_special_text(p.text):
                    pbar.update(1)
                    continue

                new_p = self._extract_paragraph(copy(p))
                if self.single_translate and self.block_size > 0:
                    p_len = num_tokens_from_text(new_p.text)
                    block_len += p_len
                    if block_len > self.block_size:
                        index = self._process_combined_paragraph(
                            p_block, index, p_to_save_len, thread_safe=False
                        )
                        p_block = [p]
                        block_len = p_len
                        print()
                    else:
                        p_block.append(p)
                else:
                    index = self._process_paragraph(
                        p, new_p, index, p_to_save_len, thread_safe=False
                    )
                    print()

                # pbar.update(delta) not pbar.update(index)?
                pbar.update(1)

                if self.is_test and index >= self.test_num:
                    break
            if self.single_translate and self.block_size > 0 and len(p_block) > 0:
                index = self._process_combined_paragraph(
                    p_block, index, p_to_save_len, thread_safe=False
                )

        if soup:
            item.content = soup.encode(encoding="utf-8")
        new_book.add_item(item)

        return index

    def set_parallel_workers(self, workers):
        """Set number of parallel workers for chapter processing.

        Args:
            workers (int): Number of parallel workers. Will be automatically
                         optimized based on actual chapter count during processing.
        """
        self.parallel_workers = max(1, workers)
        self.enable_parallel = workers > 1

        if workers > 8:
            print(
                f"⚠️  Warning: {workers} workers is quite high. Consider using 2-8 workers for optimal performance."
            )

    def _get_next_translation_index(self):
        """Thread-safe method to get next translation index."""
        with self._progress_lock:
            index = self._translation_index
            self._translation_index += 1
            return index

    def _process_chapter_parallel(self, chapter_data):
        """Process a single chapter in parallel mode with proper accumulated_num handling."""
        item, trans_taglist, p_to_save_len = chapter_data
        chapter_result = {
            "item": item,
            "processed_content": None,
            "success": False,
            "error": None,
        }

        try:
            # Create a chapter-specific translator instance to avoid context conflicts
            # This ensures each chapter has its own independent context
            thread_translator = self._create_chapter_translator()

            content = item.content
            soup = bs(content, "html.parser")
            p_list = soup.findAll(trans_taglist)
            p_list = self.filter_nest_list(p_list, trans_taglist)

            if self.allow_navigable_strings:
                p_list.extend(soup.findAll(text=True))

            # Initialize chapter-specific context lists
            chapter_context_list = []
            chapter_translated_list = []

            # Apply accumulated_num logic for this chapter independently
            send_num = self.accumulated_num
            if send_num > 1:
                # Use accumulated translation logic for this chapter
                self._translate_paragraphs_acc_parallel(
                    p_list,
                    send_num,
                    thread_translator,
                    chapter_context_list,
                    chapter_translated_list,
                )
            else:
                # Process paragraphs individually for this chapter
                for p in p_list:
                    if not p.text or self._is_special_text(p.text):
                        continue

                    new_p = self._extract_paragraph(copy(p))
                    index = self._get_next_translation_index()

                    if self.resume and index < p_to_save_len:
                        t_text = self.p_to_save[index]
                    else:
                        # Use chapter-specific context for translation
                        t_text = self._translate_with_chapter_context(
                            thread_translator,
                            new_p.text,
                            chapter_context_list,
                            chapter_translated_list,
                        )
                        t_text = "" if t_text is None else t_text
                        with self._progress_lock:
                            self.p_to_save.append(t_text)

                    if isinstance(p, NavigableString):
                        translated_node = NavigableString(t_text)
                        p.insert_after(translated_node)
                        if self.single_translate:
                            p.extract()
                    else:
                        self.helper.insert_trans(
                            p, t_text, self.translation_style, self.single_translate
                        )

                    with self._progress_lock:
                        if index % 20 == 0:
                            self._save_progress()

            if soup:
                chapter_result["processed_content"] = soup.encode(encoding="utf-8")
            chapter_result["success"] = True

        except Exception as e:
            chapter_result["error"] = str(e)
            print(f"Error processing chapter {item.file_name}: {e}")

        return chapter_result

    def _create_chapter_translator(self):
        """Create a translator instance for a specific chapter with independent context."""
        # Return the main translator - we'll handle context at the chapter level
        return self.translate_model

    def _translate_with_chapter_context(
        self, translator, text, chapter_context_list, chapter_translated_list
    ):
        """Translate text with chapter-specific context management."""
        if not translator.context_flag:
            return translator.translate(text)

        # Temporarily replace global context with chapter context
        original_context = getattr(translator, "context_list", [])
        original_translated = getattr(translator, "context_translated_list", [])

        try:
            # Use chapter-specific context
            translator.context_list = chapter_context_list.copy()
            translator.context_translated_list = chapter_translated_list.copy()

            # Perform translation
            result = translator.translate(text)

            # Update chapter context
            chapter_context_list[:] = translator.context_list
            chapter_translated_list[:] = translator.context_translated_list

            return result

        finally:
            # Restore original context
            translator.context_list = original_context
            translator.context_translated_list = original_translated

    def _translate_paragraphs_acc_parallel(
        self,
        p_list,
        send_num,
        translator,
        chapter_context_list,
        chapter_translated_list,
    ):
        """Apply accumulated_num logic for a single chapter in parallel mode with independent context."""
        from book_maker.utils import num_tokens_from_text
        from .helper import not_trans

        count = 0
        wait_p_list = []

        # Create chapter-specific helper instance with context-aware translation
        class ChapterHelper:
            def __init__(
                self, parent_loader, translator, context_list, translated_list
            ):
                self.parent_loader = parent_loader
                self.translator = translator
                self.context_list = context_list
                self.translated_list = translated_list

            def translate_with_context(self, text):
                return self.parent_loader._translate_with_chapter_context(
                    self.translator, text, self.context_list, self.translated_list
                )

            def deal_old(self, wait_p_list, single_translate):
                if not wait_p_list:
                    return

                # Use the same translate_list logic as sequential processing
                # Create a temporary translator with chapter context
                original_context = getattr(self.translator, "context_list", [])
                original_translated = getattr(
                    self.translator, "context_translated_list", []
                )

                try:
                    # Set chapter context to the translator
                    self.translator.context_list = self.context_list.copy()
                    self.translator.context_translated_list = (
                        self.translated_list.copy()
                    )

                    # Call translate_list for consistent batch translation logic
                    result_txt_list = self.translator.translate_list(wait_p_list)

                    # Update chapter context from translator
                    self.context_list[:] = self.translator.context_list
                    self.translated_list[:] = self.translator.context_translated_list

                    # Apply translations using the same logic as helper.deal_old
                    for i in range(len(wait_p_list)):
                        if i < len(result_txt_list):
                            p = wait_p_list[i]
                            from .helper import shorter_result_link

                            self.parent_loader.helper.insert_trans(
                                p,
                                shorter_result_link(result_txt_list[i]),
                                self.parent_loader.translation_style,
                                single_translate,
                            )

                finally:
                    # Restore original context
                    self.translator.context_list = original_context
                    self.translator.context_translated_list = original_translated

                wait_p_list.clear()

            def deal_new(self, p, wait_p_list, single_translate):
                self.deal_old(wait_p_list, single_translate)
                translation = self.translate_with_context(p.text)
                self.parent_loader.helper.insert_trans(
                    p,
                    translation,
                    self.parent_loader.translation_style,
                    single_translate,
                )

        chapter_helper = ChapterHelper(
            self, translator, chapter_context_list, chapter_translated_list
        )

        for i in range(len(p_list)):
            p = p_list[i]
            temp_p = copy(p)

            for p_exclude in self.exclude_translate_tags.split(","):
                if type(p) == NavigableString:
                    continue
                for pt in temp_p.find_all(p_exclude):
                    pt.extract()

            if any(
                [not p.text, self._is_special_text(temp_p.text), not_trans(temp_p.text)]
            ):
                if i == len(p_list) - 1:
                    chapter_helper.deal_old(wait_p_list, self.single_translate)
                continue

            length = num_tokens_from_text(temp_p.text)
            if length > send_num:
                chapter_helper.deal_new(p, wait_p_list, self.single_translate)
                continue

            if i == len(p_list) - 1:
                if count + length < send_num:
                    wait_p_list.append(p)
                    chapter_helper.deal_old(wait_p_list, self.single_translate)
                else:
                    chapter_helper.deal_new(p, wait_p_list, self.single_translate)
                break

            if count + length < send_num:
                count += length
                wait_p_list.append(p)
            else:
                chapter_helper.deal_old(wait_p_list, self.single_translate)
                wait_p_list.append(p)
                count = length

    def batch_init_then_wait(self):
        name, _ = os.path.splitext(self.epub_name)
        if self.batch_flag or self.batch_use_flag:
            self.translate_model.batch_init(name)
            if self.batch_use_flag:
                start_time = time.time()
                while not self.translate_model.is_completed_batch():
                    print("Batch translation is not completed yet")
                    time.sleep(2)
                    if time.time() - start_time > 300:  # 5 minutes
                        raise Exception("Batch translation timed out after 5 minutes")

    def make_bilingual_book(self):
        self.helper = EPUBBookLoaderHelper(
            self.translate_model,
            self.accumulated_num,
            self.translation_style,
            self.context_flag,
        )
        self.batch_init_then_wait()
        new_book = self._make_new_book(self.origin_book)
        all_items = list(self.origin_book.get_items())
        trans_taglist = self.translate_tags.split(",")
        all_p_length = sum(
            (
                0
                if (
                    (i.get_type() != ITEM_DOCUMENT)
                    or (i.file_name in self.exclude_filelist.split(","))
                    or (
                        self.only_filelist
                        and i.file_name not in self.only_filelist.split(",")
                    )
                )
                else len(bs(i.content, "html.parser").findAll(trans_taglist))
            )
            for i in all_items
        )
        all_p_length += self.allow_navigable_strings * sum(
            (
                0
                if (
                    (i.get_type() != ITEM_DOCUMENT)
                    or (i.file_name in self.exclude_filelist.split(","))
                    or (
                        self.only_filelist
                        and i.file_name not in self.only_filelist.split(",")
                    )
                )
                else len(bs(i.content, "html.parser").findAll(text=True))
            )
            for i in all_items
        )
        pbar = tqdm(total=self.test_num) if self.is_test else tqdm(total=all_p_length)
        print()
        index = 0
        p_to_save_len = len(self.p_to_save)
        try:
            if self.retranslate:
                self.retranslate_book(
                    index, p_to_save_len, pbar, trans_taglist, self.retranslate
                )
                exit(0)
            # Add the things that don't need to be translated first, so that you can see the img after the interruption
            for item in self.origin_book.get_items():
                if item.get_type() != ITEM_DOCUMENT:
                    new_book.add_item(item)

            document_items = list(self.origin_book.get_items_of_type(ITEM_DOCUMENT))

            if self.enable_parallel and len(document_items) > 1:
                # Optimize worker count: no point having more workers than chapters
                effective_workers = min(self.parallel_workers, len(document_items))

                # Parallel processing with proper accumulated_num handling
                print(f"🚀 Parallel processing: {len(document_items)} chapters")
                if effective_workers < self.parallel_workers:
                    print(
                        f"📊 Optimized workers: {effective_workers} (reduced from {self.parallel_workers})"
                    )
                else:
                    print(f"📊 Using {effective_workers} workers")

                if self.accumulated_num > 1:
                    print(
                        f"📝 Each chapter applies accumulated_num={self.accumulated_num} independently"
                    )

                if self.context_flag:
                    print(
                        f"🔗 Context enabled: each chapter maintains independent context (limit={self.translate_model.context_paragraph_limit})"
                    )
                else:
                    print(f"🚫 Context disabled for this translation")

                # Create a simpler progress bar for parallel processing
                pbar.close()  # Close the original progress bar
                chapter_pbar = tqdm(
                    total=len(document_items), desc="Chapters", unit="ch"
                )

                chapter_data_list = [
                    (item, trans_taglist, p_to_save_len) for item in document_items
                ]

                with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                    future_to_item = {
                        executor.submit(
                            self._process_chapter_parallel, chapter_data
                        ): chapter_data[0]
                        for chapter_data in chapter_data_list
                    }

                    for future in as_completed(future_to_item):
                        item = future_to_item[future]
                        try:
                            result = future.result()
                            if result["success"] and result["processed_content"]:
                                item.content = result["processed_content"]
                            new_book.add_item(item)
                            chapter_pbar.update(1)
                            chapter_pbar.set_postfix_str(
                                f"Latest: {item.file_name[:20]}..."
                            )

                        except Exception as e:
                            print(f"❌ Error processing {item.file_name}: {e}")
                            new_book.add_item(item)
                            chapter_pbar.update(1)

                chapter_pbar.close()
                print(f"✅ Completed all {len(document_items)} chapters")
            else:
                # Sequential processing (original behavior or single chapter)
                if len(document_items) == 1 and self.enable_parallel:
                    print(f"📄 Single chapter detected - using sequential processing")

                for item in document_items:
                    index = self.process_item(
                        item, index, p_to_save_len, pbar, new_book, trans_taglist
                    )

                if self.accumulated_num > 1:
                    name, _ = os.path.splitext(self.epub_name)
                    epub.write_epub(f"{name}_bilingual.epub", new_book, {})
            name, _ = os.path.splitext(self.epub_name)
            if self.batch_flag:
                self.translate_model.batch()
            else:
                epub.write_epub(f"{name}_bilingual.epub", new_book, {})
            if self.accumulated_num == 1:
                pbar.close()
        except KeyboardInterrupt as e:
            print(e)
            if self.accumulated_num == 1:
                print("you can resume it next time")
                self._save_progress()
                self._save_temp_book()
            sys.exit(0)
        except Exception:
            traceback.print_exc()
            sys.exit(0)

    def load_state(self):
        try:
            with open(self.bin_path, "rb") as f:
                self.p_to_save = pickle.load(f)
        except Exception:
            raise Exception("can not load resume file")

    def _save_temp_book(self):
        # TODO refactor this logic
        origin_book_temp = epub.read_epub(self.epub_name)
        new_temp_book = self._make_new_book(origin_book_temp)
        p_to_save_len = len(self.p_to_save)
        trans_taglist = self.translate_tags.split(",")
        index = 0
        try:
            for item in origin_book_temp.get_items():
                if item.get_type() == ITEM_DOCUMENT:
                    content = item.content
                    soup = bs(content, "html.parser")
                    p_list = soup.findAll(trans_taglist)
                    if self.allow_navigable_strings:
                        p_list.extend(soup.findAll(text=True))
                    for p in p_list:
                        if not p.text or self._is_special_text(p.text):
                            continue
                        # TODO banch of p to translate then combine
                        # PR welcome here
                        if index < p_to_save_len:
                            new_p = copy(p)
                            if type(p) is NavigableString:
                                new_p = self.p_to_save[index]
                            else:
                                new_p.string = self.p_to_save[index]
                            self.helper.insert_trans(
                                p,
                                new_p.string,
                                self.translation_style,
                                self.single_translate,
                            )
                            index += 1
                        else:
                            break
                    # for save temp book
                    if soup:
                        item.content = soup.encode()
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
