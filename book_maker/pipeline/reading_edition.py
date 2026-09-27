"""The bilingual Markdown a reader gets, laid out for Pandoc.

`MarkdownBookLoader` already knows which translation belongs to which source
block; nothing here re-derives that from the rendered file, because a
rendered file cannot be read back into pairs once an image, a preserved
table or a two-paragraph translation is in it.

What this subclass changes is only the shape of the output:

* blocks are separated by a blank line, so two adjacent text entries stay
  two parsed Markdown blocks instead of collapsing into one paragraph;
* a source heading stays the only heading — it carries a unique identifier
  and defines the table of contents — and its translation follows as an
  ordinary paragraph, so one chapter never appears twice in the TOC;
* every translation sits in a fenced div, which Pandoc turns into a `div`
  the stylesheet can address, carrying the run's target language tag when
  the run stated one and nothing when it did not;
* the machine-translation credit line is written once, at the end, on the
  success path only.

The ordinary `--book_name book.md` path is untouched: none of this happens
unless the harness asks for this class.
"""

import json
import re
import time
from pathlib import Path

from book_maker.loader.disclosure import CREDIT_CLASS, CREDIT_PREFIX, credit_name
from book_maker.loader.md_loader import MarkdownBookLoader
from book_maker.markdown_spans import CODE_SPAN, MATH_SPANS
from book_maker.redaction import redact
from book_maker.translation_checks import suspected_echo

from .bundle import TRANSLATE_RESULT, TRANSLATE_STATE, TRANSLATE_TEMP, sha256_text
from .messages import ECHO_UNRESOLVED
from .preflight import MARKDOWN_FORMAT, parse_markdown, run_tool

TRANSLATION_CLASS = "bbm-translation"

# How many echoed blocks the warning names before it counts the rest.
ECHO_LINES_NAMED = 10

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
HEADING_ID = re.compile(r"\s*\{#([A-Za-z0-9_\-:.]+)\}\s*$")
HEADING_ATTRIBUTES = re.compile(r"\s+\{((?:[#.][^\s{}]+|[\w-]+=)[^{}]*)\}\s*$")

# Spans the ordinary Markdown path does not protect, added here so the
# reading edition can carry them through a translation unchanged: maths
# (`markdown_spans.MATH_SPANS`, display forms first) and footnote references.
EXTRA_PROTECTED = [*MATH_SPANS, (r"\[\^[^\]\s]+\]", 0)]

# Blocks kept once, in the source language, and never sent to the model.
FOOTNOTE_DEFINITION = re.compile(r"^\[\^[^\]\s]+\]:")
HTML_TABLE = re.compile(r"^\s*<table\b", re.I)
DISPLAY_MATH = re.compile(r"\$\$.+?\$\$|\\\[.+?\\\]", re.DOTALL)

# Removed from the translated copy of a paragraph. The image belongs to the
# source block above it -- one picture, not two -- and a second footnote
# reference would make Pandoc emit the definition twice.
TRANSLATION_ONLY_DROP = re.compile(
    r"!\[[^\]\n]*\]\((?:<[^>\n]*>|[^)\s]+)(?:\s+\"[^\"\n]*\")?\)" r"|\[\^[^\]\s]+\]"
)


# What Pandoc takes for a list item at the start of a block: a bullet, or an
# enumerator (`1.`, `1)`, `(a)`, `iv.`) followed by a space. The escape goes
# on the last character of the marker, which is always punctuation.
LIST_MARKER = re.compile(
    r"^(?P<mark>[-*+]|\(?[0-9]+[.)]|\(?[a-zA-Z]{1,4}[.)]|#{1,6})(?=\s|$)"
)


def _escape_emphasis(text):
    """`text` with every unescaped `*` and `_` outside code spans escaped."""
    parts = []
    last = 0
    for match in CODE_SPAN.finditer(text):
        parts.append(re.sub(r"(?<!\\)([*_])", r"\\\1", text[last : match.start()]))
        parts.append(match.group(0))
        last = match.end()
    parts.append(re.sub(r"(?<!\\)([*_])", r"\\\1", text[last:]))
    return "".join(parts)


class ReadingEditionMarkdownLoader(MarkdownBookLoader):
    """Bilingual Markdown written for a reading edition rather than a diff."""

    # Blocks are joined with a blank line. The base separator is a single
    # newline, which makes a source paragraph and its translation one
    # Markdown paragraph; every other rule here depends on them being two.
    SAVE_FILE_SEPARATOR = "\n\n"

    # Set by `reading_edition_loader_class`; None means "behave like the
    # plain loader", which is what makes this class usable on its own.
    _state_file = None
    _output_file = None
    _temp_file = None
    _completion_file = None
    _language_tag = None
    # Pandoc's identifiers for the source headings, in document order.
    _heading_ids = ()
    _pandoc = None
    # The bundle whose manifest records what the run noticed; None outside
    # the harness.
    _bundle = None
    # Each assembly's stamped heading lines (`_heading_line`), and the
    # `source.md` lines of blocks that came back as their source.
    _stamped_headings = None
    echo_lines = ()

    # -- where the files go -------------------------------------------
    def _state_path(self, md_name):
        if self._state_file is None:
            return super()._state_path(md_name)
        Path(self._state_file).parent.mkdir(parents=True, exist_ok=True)
        return str(self._state_file)

    def _output_path(self):
        if self._output_file is None:
            return super()._output_path()
        return str(self._output_file)

    def _temp_output_path(self):
        if self._temp_file is None:
            return super()._temp_output_path()
        Path(self._temp_file).parent.mkdir(parents=True, exist_ok=True)
        return str(self._temp_file)

    # -- what never reaches the model ----------------------------------
    def _flush_paragraph(self, current_paragraph, line=None):
        """A paragraph, unless it is a structure that is carried as it is.

        Three shapes are kept once, in the source language, and never sent
        anywhere: a footnote definition (a second, translated definition of
        the same note is a duplicate Pandoc resolves by printing both), an
        HTML table (an extractor's normal output, whose cells are not
        prose the pipeline can pair), and a block that is nothing but
        display math.
        """
        if not current_paragraph:
            return
        text = "\n".join(current_paragraph)
        # Only Pandoc decides whether an underlined paragraph is a heading.
        # Normalize that block for the base loader's ATX-heading machinery;
        # leave the inspectable source and every other Markdown block alone.
        if self._pandoc and re.search(r"\n[=-]+\s*$", text):
            ast = parse_markdown(self._pandoc, text)
            blocks = ast.get("blocks", [])
            if len(blocks) == 1 and blocks[0]["t"] == "Header":
                normalized = run_tool(
                    [
                        self._pandoc,
                        "-f",
                        "json",
                        "-t",
                        MARKDOWN_FORMAT,
                        "--markdown-headings=atx",
                        "--wrap=none",
                    ],
                    stdin_text=json.dumps(ast),
                ).stdout.strip()
                self._append_block([normalized], translatable=True, line=line)
                return
        self._append_block(
            current_paragraph, translatable=not self._is_carried_block(text), line=line
        )

    @staticmethod
    def _is_carried_block(text):
        stripped = text.strip()
        if FOOTNOTE_DEFINITION.match(stripped):
            return True
        if HTML_TABLE.match(stripped):
            return True
        # Display math with nothing else in the block: translating it would
        # send the formula to the model and then print it twice.
        without_math = DISPLAY_MATH.sub("", stripped)
        return bool(DISPLAY_MATH.search(stripped)) and not re.search(
            r"\w", without_math
        )

    @staticmethod
    def _protect_inline_markdown(text):
        """The base protection, plus math and footnote references.

        The token counter continues from the base's dictionary, so the two
        passes cannot mint the same token, and `_restore_inline_markdown`
        puts all of them back unchanged.
        """
        protected, replacements = MarkdownBookLoader._protect_inline_markdown(text)

        def replace(match):
            token = f"@@BBM_MD_PROTECT_{len(replacements)}@@"
            replacements[token] = match.group(0)
            return token

        for pattern, flags in EXTRA_PROTECTED:
            protected = re.sub(pattern, replace, protected, flags=flags)
        return protected, replacements

    # -- assembly ------------------------------------------------------
    def _render_bilingual_result(self, translate_missing):
        self._used_ids = {}
        self._heading_serial = 0
        self._pending_ids = list(self._heading_ids)
        self._stamped_headings = {}
        result = super()._render_bilingual_result(translate_missing)
        result = self._settle_heading_attributes(result)
        if translate_missing:
            # Only a finished book is stamped. The partial file an
            # interrupted run leaves behind is not a translation of the
            # book and must not claim to be one.
            result.append(self._credit_block())
        return result

    def _assemble_render_items(self, render_items, batches, translated_batches):
        self.pair_count = 0
        self.batch_count = len(batches)
        self.untranslated_batches = sum(
            1 for translated in translated_batches if not translated
        )
        self.echo_lines = self._echoed_lines(batches, translated_batches)
        return super()._assemble_render_items(render_items, batches, translated_batches)

    def _echoed_lines(self, batches, translated_batches):
        """The `source.md` lines of blocks that came back as their source.

        Judged on every pair the book is assembled from, resumed batches
        included, so the report covers the whole book and not only what
        this process translated. Warning only: the block is kept as the
        model returned it (T3, lead 260925 with astra consult, rescoped to
        detection; docs/260925-docs-SKILL_FIELD_TEST_FRICTIONS.md).

        Wired here only, for the reading edition. The plain Markdown,
        EPUB, txt and srt loaders do not run this check.
        """
        target = self._language_tag or self.target_language
        lines = []
        for batch, translated in zip(batches, translated_batches):
            if not translated or len(translated) != len(batch.block_texts):
                continue
            for source, reply, line in zip(
                batch.block_texts, translated, batch.block_lines
            ):
                if line is not None and suspected_echo(source, reply, target):
                    lines.append(line)
        return lines

    def _emit_pair(self, result, source_text, translated_text):
        self.pair_count += 1
        match = HEADING.match(source_text.strip())
        if match:
            translation = self._heading_translation(translated_text)
            if self.single_translate:
                result.append(self._heading_line(match, translation))
                return
            result.append(self._heading_line(match, None))
            result.append(self._translation_block(self._shield_marker("", translation)))
            return
        translation = self._translation_text(translated_text)
        if not self.single_translate:
            result.append(source_text)
        if translation:
            translation = self._shield_marker(source_text, translation)
            result.append(self._translation_block(translation))
        elif self.single_translate:
            # Nothing but an image or a note marker: the source copy of the
            # block is the only place it can live.
            result.append(source_text)

    @staticmethod
    def _translation_text(translated_text):
        """The translated paragraph, with what belongs to the source removed.

        `_restore_inline_markdown` puts every protected span back into both
        copies, which is right for a link or a code span and wrong for an
        image and a footnote reference: the first would print the picture
        twice, the second would make Pandoc emit two identically-worded
        definitions of one note. The surrounding translated prose stays.
        """
        cleaned = TRANSLATION_ONLY_DROP.sub("", translated_text)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"[ \t]+([,.;:!?)\u3001\u3002\uff0c\uff0e])", r"\1", cleaned)
        return cleaned.strip()

    @staticmethod
    def _shield_marker(source_text, translation):
        """A translation that starts like a list item, kept as prose.

        `2.1. 方法` or `(a) 动作融合` begins with what Pandoc reads as a list
        marker, so a numbered heading's translation came back as an ordered
        list inside its div (found 260920). The marker's punctuation is
        escaped, unless the source block began with a marker too -- then it
        is a list, and stays one.
        """
        if LIST_MARKER.match(source_text.lstrip()):
            return translation
        match = LIST_MARKER.match(translation)
        if not match:
            return translation
        punctuation = match.end("mark") - 1
        return translation[:punctuation] + "\\" + translation[punctuation:]

    # -- headings -------------------------------------------------------
    def _heading_line(self, match, replacement):
        hashes, text = match.group(1), match.group(2)
        existing = HEADING_ATTRIBUTES.search(text)
        attributes = existing.group(1) if existing else ""
        if existing:
            text = text[: existing.start()].rstrip()
        source_id = re.search(r"(?:^|\s)#([^\s]+)", attributes)
        identifier = self._next_identifier(source_id.group(1) if source_id else text)
        # Replace just the ID; keep source classes and key/value attributes.
        attributes = re.sub(r"(?:^|\s)#[^\s]+", "", attributes).strip()
        suffix = f" {attributes}" if attributes else ""
        if replacement is not None:
            text = replacement
        attribute = f"{{#{identifier}{suffix}}}"
        line = f"{hashes} {text} {attribute}"
        if self._stamped_headings is not None:
            self._stamped_headings[line] = (hashes, text, attribute, identifier)
        return line

    def _settle_heading_attributes(self, result):
        """Every stamped heading's `{#id}` read by Pandoc as its attribute.

        An emphasis opener that never closes (an OCR'd `## *习题5.22`, a
        stray `**`) makes Pandoc read the attribute as heading text: the
        literal `{#...}` then shows in the heading and in the contents.
        Asked of Pandoc once for the whole book; a heading it misreads has
        its `*` and `_` escaped, which prints them as the source printed
        them (skill field test 260925). Only a heading with a `*` or `_` in
        its text can be misread that way, or changed by the escape, so only
        those are asked about; a book with none makes no Pandoc call.
        """
        stamped = self._stamped_headings
        indexes = [
            i
            for i, item in enumerate(result)
            if item in stamped and re.search(r"[*_]", stamped[item][1])
        ]
        if not indexes or not self._pandoc:
            return result
        lines = [result[i] for i in indexes]
        blocks = parse_markdown(self._pandoc, "\n\n".join(lines)).get("blocks", [])
        if len(blocks) != len(lines):
            return result
        for index, line, block in zip(indexes, lines, blocks):
            hashes, text, attribute, identifier = stamped[line]
            if block.get("t") == "Header" and block["c"][1][0] == identifier:
                continue
            result[index] = f"{hashes} {_escape_emphasis(text)} {attribute}"
        return result

    def _next_identifier(self, fallback):
        """The identifier Pandoc already gave this heading, if we have it.

        Reusing the canonical one is what keeps a `[see](#chapter-two)`
        written against the source working in the bilingual file and in the
        EPUB; a slug of our own would silently break every internal link,
        and Pandoc's own duplicate suffixes (`notes`, `notes-1`) come along
        with it. The slug below is only for a heading Pandoc did not report
        -- an edited file, or one nested where the loader sees it and the
        parser does not.
        """
        while self._pending_ids:
            candidate = self._pending_ids.pop(0)
            if candidate and candidate not in self._used_ids:
                self._used_ids[candidate] = 1
                self._heading_serial += 1
                return candidate
        return self._claim_id(self._slug(fallback))

    def _claim_id(self, candidate):
        """A heading identifier that is used exactly once in this book.

        Repeated chapter titles ("Notes", "Introduction") are normal, and
        two headings sharing an id give Pandoc two navigation entries
        pointing at the same place.
        """
        self._heading_serial += 1
        if not candidate or not re.match(r"^[^\W\d_]", candidate, re.UNICODE):
            candidate = f"section-{self._heading_serial}"
        if candidate not in self._used_ids:
            self._used_ids[candidate] = 1
            return candidate
        suffix = 1
        unique = f"{candidate}-{suffix}"
        while unique in self._used_ids:
            suffix += 1
            unique = f"{candidate}-{suffix}"
        self._used_ids[unique] = 1
        return unique

    @staticmethod
    def _slug(text):
        slug = re.sub(r"[^\w\s-]", "", text.strip().lower(), flags=re.UNICODE)
        slug = re.sub(r"[\s_]+", "-", slug).strip("-")
        return re.sub(r"-{2,}", "-", slug)

    @staticmethod
    def _heading_translation(text):
        """A translated heading as running text.

        Models return `## 第一章` for `## Chapter One`; kept as a heading it
        would be a second TOC entry for the same chapter, and kept with the
        hashes visible it would be neither.
        """
        stripped = text.strip()
        stripped = re.sub(r"^#{1,6}\s*", "", stripped)
        stripped = HEADING_ATTRIBUTES.sub("", stripped)
        return stripped.strip()

    # -- fenced divs ----------------------------------------------------
    def _translation_block(self, text):
        attributes = f".{TRANSLATION_CLASS}"
        if self._language_tag:
            attributes += f' lang="{self._language_tag}"'
        return self._fence(text.strip(), attributes)

    def _credit_block(self):
        name = redact(str(credit_name(getattr(self, "translate_model", None))))
        line = f"{CREDIT_PREFIX}{name}, {time.localtime().tm_year}."
        return self._fence(line, f".{CREDIT_CLASS}")

    @staticmethod
    def _fence(text, attributes):
        """A fenced div long enough that the text inside cannot close it."""
        longest = 0
        for line in text.splitlines():
            run = re.match(r"^\s*(:{3,})", line)
            if run:
                longest = max(longest, len(run.group(1)))
        colons = ":" * max(3, longest + 1)
        return f"{colons} {{{attributes}}}\n{text}\n{colons}"

    # -- completion ------------------------------------------------------
    def make_bilingual_book(self):
        super().make_bilingual_book()
        self._report_echoes()
        self._write_completion_record()

    def _report_echoes(self):
        """Say once which blocks came back identical, and record them.

        On a bundle the warning is also a manifest limitation. The previous
        translation's line is dropped first (`translation.limitations`), so
        a rerun that no longer echoes leaves nothing stale behind.
        """
        warning = echo_warning(self.echo_lines)
        if warning:
            print(warning)
        if self._bundle is None:
            return
        previous = (self._bundle.read_manifest().get("translation") or {}).get(
            "limitations"
        )
        self._bundle.drop_limitations(previous or [])
        recorded = [warning] if warning else []
        self._bundle.add_limitations(recorded)
        self._bundle.update_manifest(translation={"limitations": recorded})

    def _write_completion_record(self):
        """Proof, on disk, that the book was finished.

        The interrupt path in the base loader exits zero after saving
        progress, so a caller that reads an exit status alone cannot tell a
        finished translation from an abandoned one. This file is written
        after `save_file` returned, on the success path, and nowhere else.
        """
        if self._completion_file is None:
            return
        output = Path(self._output_path())
        record = {
            "completed": True,
            "output": str(output),
            "output_sha256": sha256_text(output.read_text(encoding="utf-8")),
            "pairs": getattr(self, "pair_count", 0),
            "batches": getattr(self, "batch_count", 0),
            "untranslated_batches": getattr(self, "untranslated_batches", 0),
            "sample": bool(self.is_test),
            "test_num": self.test_num if self.is_test else None,
            "translator": redact(str(credit_name(self.translate_model))),
        }
        path = Path(self._completion_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


def echo_warning(lines):
    """`ECHO_UNRESOLVED` for these `source.md` lines, or None when there are none."""
    if not lines:
        return None
    named = ", ".join(f"line {line}" for line in lines[:ECHO_LINES_NAMED])
    rest = len(lines) - ECHO_LINES_NAMED
    if rest > 0:
        named += f" and {rest} more"
    return ECHO_UNRESOLVED.format(count=len(lines), blocks=named)


def reading_edition_loader_class(
    bundle, *, language_tag=None, heading_ids=(), pandoc=None
):
    """A loader class bound to one bundle, for `cli.main`'s md seam.

    A subclass rather than a configured instance because the CLI builds the
    loader itself — that is the whole point of going through it — and a
    subclass carries the bundle without any global being patched.
    """

    class BundleMarkdownLoader(ReadingEditionMarkdownLoader):
        _state_file = bundle.work_file(TRANSLATE_STATE)
        _output_file = bundle.bilingual_markdown
        _temp_file = bundle.work_file(TRANSLATE_TEMP)
        _completion_file = bundle.work_file(TRANSLATE_RESULT)
        _language_tag = language_tag
        _heading_ids = tuple(heading_ids)
        _pandoc = pandoc
        _bundle = bundle

    return BundleMarkdownLoader
