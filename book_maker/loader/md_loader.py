import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import copy
from dataclasses import dataclass
from pathlib import Path

from book_maker.utils import prompt_config_to_kwargs

from .base_loader import BaseBookLoader


@dataclass(frozen=True)
class MarkdownBlock:
    text: str
    translatable: bool = True


@dataclass(frozen=True)
class MarkdownBatch:
    block_texts: list[str]
    breadcrumb: str = ""


class MarkdownBookLoader(BaseBookLoader):
    def __init__(
        self,
        md_name,
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
        self.md_name = md_name
        self.translate_model = model(
            key,
            language,
            api_base=model_api_base,
            temperature=temperature,
            source_lang=source_lang,
            context_flag=context_flag,
            context_paragraph_limit=context_paragraph_limit,
            **prompt_config_to_kwargs(prompt_config),
        )
        self.is_test = is_test
        self.p_to_save = []
        self.bilingual_result = []
        self.bilingual_temp_result = []
        self.test_num = test_num
        self.batch_size = 10
        self.md_chunk_char_budget = 2000
        self.single_translate = single_translate
        self.context_flag = context_flag
        self.context_paragraph_limit = context_paragraph_limit
        self.parallel_workers = max(1, parallel_workers)
        self.md_blocks = []

        try:
            with open(f"{md_name}", encoding="utf-8") as f:
                self.origin_book = f.read().splitlines()

        except Exception as e:
            raise Exception("can not load file") from e

        self.resume = resume
        self.bin_path = f"{Path(md_name).parent}/.{Path(md_name).stem}.temp.bin"
        if self.resume:
            self.load_state()

        self.process_markdown_content()

    def process_markdown_content(self):
        """Split Markdown into translatable prose blocks and pass-through blocks."""
        self.md_blocks = []
        lines = self.origin_book
        i = 0

        if len(lines) >= 2 and lines[0].strip() == "---":
            for end in range(1, len(lines)):
                if lines[end].strip() == "---":
                    self._append_block(lines[: end + 1], translatable=False)
                    i = end + 1
                    break

        current_paragraph = []

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if self._is_fence_start(stripped):
                self._flush_paragraph(current_paragraph)
                current_paragraph = []
                fence_lines, i = self._collect_fence(lines, i)
                self._append_block(fence_lines, translatable=False)
                continue

            if self._is_table_start(lines, i):
                self._flush_paragraph(current_paragraph)
                current_paragraph = []
                table_lines, i = self._collect_table(lines, i)
                self._append_block(table_lines, translatable=False)
                continue

            if self._is_pass_through_line(stripped):
                self._flush_paragraph(current_paragraph)
                current_paragraph = []
                self._append_block([line], translatable=False)
                i += 1
                continue

            if not line.strip():
                if current_paragraph:
                    self._flush_paragraph(current_paragraph)
                    current_paragraph = []
            elif line.strip().startswith("#"):
                if current_paragraph:
                    self._flush_paragraph(current_paragraph)
                    current_paragraph = []
                self._append_block([line], translatable=True)
            else:
                current_paragraph.append(line)
            i += 1

        if current_paragraph:
            self._flush_paragraph(current_paragraph)

    def _append_block(self, lines, translatable=True):
        text = "\n".join(lines)
        block = MarkdownBlock(text=text, translatable=translatable)
        self.md_blocks.append(block)

    def _flush_paragraph(self, current_paragraph):
        if current_paragraph:
            self._append_block(current_paragraph, translatable=True)

    @staticmethod
    def _is_fence_start(stripped_line):
        return bool(re.match(r"^(`{3,}|~{3,})", stripped_line))

    def _collect_fence(self, lines, start):
        opening = lines[start].strip()
        marker = re.match(r"^(`{3,}|~{3,})", opening).group(1)
        fence_char = marker[0]
        min_len = len(marker)
        fence_lines = [lines[start]]
        i = start + 1

        while i < len(lines):
            fence_lines.append(lines[i])
            stripped = lines[i].strip()
            if re.match(rf"^{re.escape(fence_char)}{{{min_len},}}\s*$", stripped):
                i += 1
                break
            i += 1

        return fence_lines, i

    @staticmethod
    def _is_table_separator(line):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2:
            return False
        return all(re.match(r"^:?-{3,}:?$", cell or "") for cell in cells)

    def _is_table_start(self, lines, index):
        if "|" not in lines[index]:
            return False
        return index + 1 < len(lines) and self._is_table_separator(lines[index + 1])

    def _collect_table(self, lines, start):
        table_lines = [lines[start], lines[start + 1]]
        i = start + 2
        while i < len(lines) and "|" in lines[i] and lines[i].strip():
            table_lines.append(lines[i])
            i += 1
        return table_lines, i

    @staticmethod
    def _is_pass_through_line(stripped_line):
        return (
            bool(re.match(r"^\{\d+\}-+$", stripped_line))
            or stripped_line.startswith("![")
            or bool(re.match(r"^<!--.*-->$", stripped_line))
        )

    @staticmethod
    def _is_special_text(text):
        stripped = text.strip()
        return stripped.isdigit() or len(stripped) == 0

    def _make_new_book(self, book):
        pass

    def make_bilingual_book(self):
        try:
            self.bilingual_result = self._render_bilingual_result(translate_missing=True)

            self.save_file(
                f"{Path(self.md_name).parent}/{Path(self.md_name).stem}_bilingual.md",
                self.bilingual_result,
            )

        except KeyboardInterrupt:
            print("Interrupted. Saving progress so you can resume later.")
            self._save_progress()
            self._save_temp_book()
            sys.exit(0)
        except Exception as e:
            print(f"Error: {e}")
            print("Saving progress so you can resume later.")
            self._save_progress()
            self._save_temp_book()
            raise

    def _render_bilingual_result(self, translate_missing):
        render_items, batches = self._enumerate_render_items()
        translated_batches = self._translate_batches(batches, translate_missing)
        return self._assemble_render_items(render_items, batches, translated_batches)

    def _enumerate_render_items(self):
        render_items = []
        batches = []
        batch = []
        batch_breadcrumb = ""
        heading_stack = []
        translated_count = 0
        stop_after_batch = False

        def flush_batch():
            nonlocal batch, batch_breadcrumb, translated_count, stop_after_batch
            if not batch:
                return

            batch_texts = [block.text for block in batch]
            batch_text = "\n\n".join(batch_texts)
            if self._is_special_text(batch_text):
                batch = []
                batch_breadcrumb = ""
                return

            batch_index = len(batches)
            batches.append(MarkdownBatch(batch_texts, batch_breadcrumb))
            render_items.append(("batch", batch_index))

            translated_count += len(batch)
            batch = []
            batch_breadcrumb = ""
            if self.is_test and translated_count >= self.test_num:
                stop_after_batch = True

        for block in self.md_blocks:
            if stop_after_batch:
                break

            if not block.translatable or self._is_special_text(block.text):
                flush_batch()
                render_items.append(("text", block.text))
                continue

            if self.is_test and translated_count + len(batch) >= self.test_num:
                flush_batch()
                if stop_after_batch:
                    break

            if self._is_heading(block.text):
                flush_batch()
                heading_stack = self._update_heading_stack(heading_stack, block.text)
            elif self._would_exceed_chunk_budget(batch, block):
                flush_batch()

            if not batch:
                batch_breadcrumb = self._breadcrumb(heading_stack)

            batch.append(block)
            remaining = self.test_num - translated_count if self.is_test else None
            target_size = min(self.batch_size, remaining) if remaining else self.batch_size
            if len(batch) >= target_size and not self._batch_is_heading_only(batch):
                flush_batch()
            elif (
                self._batch_char_count(batch) >= self.md_chunk_char_budget
                and not self._batch_is_heading_only(batch)
            ):
                flush_batch()

        if not stop_after_batch:
            flush_batch()

        return render_items, batches

    def _translate_batches(self, batches, translate_missing):
        results = [None] * len(batches)

        for index, batch in enumerate(batches):
            if index < len(self.p_to_save):
                results[index] = self._coerce_saved_batch(
                    self.p_to_save[index],
                    batch.block_texts,
                )

        if not translate_missing:
            return [result if result is not None else [] for result in results]

        pending = [
            (index, batch)
            for index, batch in enumerate(batches)
            if results[index] is None
        ]
        if not pending:
            return results

        try:
            if self.context_flag and self.parallel_workers > 1:
                self._translate_sections_parallel(pending, results)
            elif self.context_flag:
                for index, batch in pending:
                    results[index] = self._translate_batch(
                        batch.block_texts,
                        batch.breadcrumb,
                    )
            else:
                effective_workers = min(self.parallel_workers, len(pending))
                if effective_workers <= 1:
                    for index, batch in pending:
                        results[index] = self._translate_batch(
                            batch.block_texts,
                            batch.breadcrumb,
                        )
                else:
                    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                        futures = {
                            executor.submit(
                                self._translate_batch,
                                batch.block_texts,
                                batch.breadcrumb,
                            ): index
                            for index, batch in pending
                        }
                        for future in as_completed(futures):
                            results[futures[future]] = future.result()
        except (KeyboardInterrupt, Exception):
            self.p_to_save = self._contiguous_results(results)
            raise

        self.p_to_save = self._contiguous_results(results)
        return results

    def _translate_sections_parallel(self, pending, results):
        """Translate independent Markdown sections concurrently while keeping
        per-section context intact.

        Used when both --use_context and --parallel-workers are active. Pending
        batches are grouped into heading-delimited sections; each section is
        translated sequentially (so context accumulates in reading order) on
        its own context-isolated translator clone, and sections run in
        parallel. `results` is filled by batch index, so output order is
        preserved regardless of completion order.
        """
        sections = self._group_sections(pending)
        effective_workers = min(self.parallel_workers, len(sections))

        if effective_workers <= 1:
            for section in sections:
                self._translate_section(section, results)
            return

        print(
            f"Markdown parallel context: {len(sections)} sections "
            f"across {effective_workers} workers."
        )
        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            futures = [
                executor.submit(self._translate_section, section, results)
                for section in sections
            ]
            for future in as_completed(futures):
                future.result()

    def _group_sections(self, pending):
        """Split pending (index, batch) items into contiguous sections keyed by
        their top-level heading, so each section is an independent context unit.
        """
        sections = []
        current = []
        current_key = None
        for index, batch in pending:
            key = self._section_key(batch)
            if current and key != current_key:
                sections.append(current)
                current = []
            current.append((index, batch))
            current_key = key
        if current:
            sections.append(current)
        return sections

    @staticmethod
    def _section_key(batch):
        breadcrumb = batch.breadcrumb or ""
        return breadcrumb.split(" > ")[0].strip()

    def _translate_section(self, section, results):
        """Translate one section's batches in reading order on an isolated
        translator clone, writing each finished batch into `results` so an
        interrupt only loses in-flight batches, not the whole section. Each
        section owns a distinct set of indexes, so writes never collide."""
        translator = self._clone_translator_for_context()
        for index, batch in section:
            results[index] = self._translate_batch(
                batch.block_texts,
                batch.breadcrumb,
                translator=translator,
            )

    def _clone_translator_for_context(self):
        """Return a translator with its own context buffers so parallel
        sections do not share or clobber each other's context. Falls back to
        the shared model if the translator cannot be cloned."""
        if self.parallel_workers <= 1:
            return self.translate_model
        try:
            clone = copy(self.translate_model)
        except Exception:
            return self.translate_model
        if hasattr(clone, "context_list"):
            clone.context_list = []
        if hasattr(clone, "context_translated_list"):
            clone.context_translated_list = []
        return clone

    def _assemble_render_items(self, render_items, batches, translated_batches):
        result = []
        for item_type, value in render_items:
            if item_type == "text":
                result.append(value)
                continue

            batch = batches[value]
            translated_texts = translated_batches[value] or []
            batch_text = "\n\n".join(batch.block_texts)

            if translated_texts:
                source_items = (
                    batch.block_texts
                    if len(translated_texts) == len(batch.block_texts)
                    else [batch_text]
                )
                for source_text, translated_text in zip(source_items, translated_texts):
                    if not self.single_translate:
                        result.append(source_text)
                    result.append(translated_text)
            elif not self.single_translate:
                result.append(batch_text)

        return result

    @staticmethod
    def _contiguous_results(results):
        contiguous = []
        for result in results:
            if result is None:
                break
            contiguous.append(result)
        return contiguous

    def _translate_batch(self, batch_texts, breadcrumb="", translator=None):
        translator = translator if translator is not None else self.translate_model
        protected_items = []
        replacement_items = []
        for text in batch_texts:
            protected_text, replacements = self._protect_inline_markdown(text)
            protected_items.append(protected_text)
            replacement_items.append(replacements)

        try:
            translated_items = self._with_breadcrumb_context(
                breadcrumb,
                lambda: self._translate_list(protected_items, translator),
                translator,
            )
            if len(translated_items) != len(batch_texts):
                raise ValueError(
                    f"Expected {len(batch_texts)} translations, got {len(translated_items)}"
                )
            return [
                self._restore_inline_markdown(translated_text, replacements)
                for translated_text, replacements in zip(
                    translated_items,
                    replacement_items,
                )
            ]
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"Translation failed: {e}")
            raise Exception("Something is wrong when translating") from e

    def _translate_list(self, texts, translator=None):
        translator = translator if translator is not None else self.translate_model
        if hasattr(translator, "translate_list"):
            return translator.translate_list(texts)
        return [translator.translate(text) for text in texts]

    def _coerce_saved_batch(self, saved_batch, batch_texts):
        if isinstance(saved_batch, list):
            return [str(item) for item in saved_batch]
        if len(batch_texts) == 1:
            return [str(saved_batch)]

        parts = str(saved_batch).split("\n\n")
        if len(parts) == len(batch_texts):
            return parts
        return [str(saved_batch)]

    @staticmethod
    def _is_heading(text):
        return bool(re.match(r"^#{1,6}\s+\S", text.strip()))

    @staticmethod
    def _heading_level(text):
        return len(re.match(r"^(#{1,6})", text.strip()).group(1))

    @staticmethod
    def _heading_label(text):
        label = re.sub(r"^#{1,6}\s+", "", text.strip())
        label = re.sub(r"[ \t]*\{#[A-Za-z0-9_-]+\}[ \t]*$", "", label)
        return label.strip("*_` ")

    def _update_heading_stack(self, heading_stack, heading_text):
        level = self._heading_level(heading_text)
        label = self._heading_label(heading_text)
        next_stack = heading_stack[: level - 1]
        next_stack.append(label)
        return next_stack

    @staticmethod
    def _breadcrumb(heading_stack):
        return " > ".join(item for item in heading_stack if item)

    @staticmethod
    def _batch_char_count(batch):
        return sum(len(block.text) for block in batch)

    def _would_exceed_chunk_budget(self, batch, next_block):
        if not batch or self._batch_is_heading_only(batch):
            return False
        return (
            self._batch_char_count(batch) + len(next_block.text)
            > self.md_chunk_char_budget
        )

    def _batch_is_heading_only(self, batch):
        return len(batch) == 1 and self._is_heading(batch[0].text)

    def _with_breadcrumb_context(self, breadcrumb, translate, translator=None):
        translator = translator if translator is not None else self.translate_model
        if not self.context_flag or not breadcrumb:
            return translate()
        if not getattr(translator, "context_flag", False):
            return translate()

        context_list = getattr(translator, "context_list", None)
        translated_list = getattr(translator, "context_translated_list", None)
        if not isinstance(context_list, list) or not isinstance(translated_list, list):
            return translate()

        context_marker = f"Markdown section context: {breadcrumb}"
        translated_marker = "Context acknowledged."
        context_list.append(context_marker)
        translated_list.append(translated_marker)
        try:
            return translate()
        finally:
            if context_marker in context_list:
                context_list.remove(context_marker)
            if translated_marker in translated_list:
                translated_list.remove(translated_marker)

    @staticmethod
    def _protect_inline_markdown(text):
        replacements = {}

        def replace(match):
            token = f"@@BBM_MD_PROTECT_{len(replacements)}@@"
            replacements[token] = match.group(0)
            return token

        patterns = [
            (r"!\[[^\]\n]*\]\([^)]+\)", 0),
            (r"\[[^\]\n]+\]\([^)]+\)", 0),
            (r"(?<!`)`[^`\n]+`(?!`)", 0),
            (r"<https?://[^>\s]+>", 0),
            (r"[ \t]*\{#[A-Za-z0-9_-]+\}[ \t]*$", re.MULTILINE),
        ]
        protected = text
        for pattern, flags in patterns:
            protected = re.sub(pattern, replace, protected, flags=flags)
        return protected, replacements

    @staticmethod
    def _restore_inline_markdown(text, replacements):
        for token, original in replacements.items():
            text = text.replace(token, original)
        return text

    def _save_temp_book(self):
        self.bilingual_temp_result = self._render_bilingual_result(
            translate_missing=False
        )

        self.save_file(
            f"{Path(self.md_name).parent}/{Path(self.md_name).stem}_bilingual_temp.txt",
            self.bilingual_temp_result,
        )

    def _save_progress(self):
        try:
            with open(self.bin_path, "w", encoding="utf-8") as f:
                json.dump(self.p_to_save, f, ensure_ascii=False)
        except Exception as e:
            raise Exception("can not save resume file") from e

    def load_state(self):
        try:
            with open(self.bin_path, encoding="utf-8") as f:
                content = f.read()
                try:
                    state = json.loads(content)
                except json.JSONDecodeError:
                    state = content.splitlines()
                if not isinstance(state, list):
                    raise ValueError("resume file must contain a list")
                self.p_to_save = state
        except Exception as e:
            raise Exception("can not load resume file") from e

    def save_file(self, book_path, content):
        try:
            with open(book_path, "w", encoding="utf-8") as f:
                f.write("\n".join(content))
        except Exception as e:
            raise Exception("can not save file") from e
