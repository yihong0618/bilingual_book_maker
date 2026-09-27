"""What is in this Markdown, asked of Pandoc rather than of a parser of ours.

Two questions, both answered before anything is paid for or published:

* Does every local resource this file names actually resolve inside the
  bundle? A missing image, a link to a heading that is not there and an
  image the book would have to fetch from the network are all failures, not
  warnings on an otherwise finished ebook.
* Does it contain a structure this pipeline does not carry end to end? Those
  are named and refused here, where the answer is "normalize the source",
  instead of being translated as prose or dropped on the way to the EPUB.

Pandoc's own reader decides what the document *is*; this module only walks
the tree it returns. Three forms are checked on the raw text instead, because
Pandoc's answer for them is indistinguishable from ordinary prose or from a
supported form: reference-style images, which the Markdown loader would hand
to the model as text, HTML images, which carry no Pandoc target, and C0
control characters, which XML forbids in the EPUB.
"""

import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from .bundle import contained_path
from .errors import PipelineError
from .messages import (
    PANDOC_MIN_VERSION,
    PANDOC_REQUIRED,
    PANDOC_TOO_OLD,
    PRESERVED_WITHOUT_TRANSLATION,
    UNSUPPORTED_STRUCTURE,
)

# Pandoc's own Markdown: pipe tables, fenced divs, footnotes, header
# attributes and `$...$` math are all in it, and they are exactly the
# structures this pipeline preserves. Named explicitly so the reader and
# the writer agree, and so a future Pandoc default change is visible here.
#
# `markdown_in_html_blocks` is off. With it on, an extractor's
# `<table>...</table>` comes back as one raw block per line, because the
# cell text is read as Markdown; the pipeline needs the table to be one
# opaque block, which is also how the Markdown loader carries it.
MARKDOWN_FORMAT = "markdown-markdown_in_html_blocks"

REMOTE_SCHEMES = {"http", "https", "ftp", "ftps", "data"}

# Inline HTML that is preserved verbatim and means nothing structural: a
# printed note number, a line break, an emphasis the extractor kept. The
# note these `<sup>` numbers point at is NOT reconstructed.
ALLOWED_INLINE_HTML = re.compile(
    r"^\s*</?(?:sup|sub|br|em|strong|i|b|u|small)\b[^>]*>\s*$", re.I
)
HTML_COMMENT = re.compile(r"^\s*<!--.*-->\s*$", re.S)

REFERENCE_IMAGE = re.compile(r"!\[[^\]\n]*\]\s*\[[^\]\n]*\]")
HTML_IMAGE = re.compile(r"<img\b", re.I)

# C0 control characters other than tab, LF and CR. XML 1.0 forbids them, so
# one in the text makes the EPUB's XHTML not well-formed; a model shown one
# may also write it back as a `\u0000` escape, which Pandoc reads as raw TeX.
# Both failures used to surface at export, after the translation was paid
# for (skill field test 260925); refused here instead, code fences included.
CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass
class Problem:
    kind: str
    location: str

    def __str__(self):
        return UNSUPPORTED_STRUCTURE.format(kind=self.kind, location=self.location)


@dataclass
class Report:
    images: list = field(default_factory=list)
    heading_ids: list = field(default_factory=list)
    headings: list = field(default_factory=list)
    preserved: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)
    top_level_headings: int = 0
    second_level_headings: int = 0
    # `(level, text, identifier)` for the headings Pandoc found at the top
    # level of the document (including transparent Divs), in document order.
    # Pandoc excludes headings inside lists and block quotes from its TOC.
    outline: list = field(default_factory=list)

    @property
    def top_level_heading_ids(self):
        """The same headings' identifiers, in document order.

        The Markdown loader recognises the same set (a line beginning with
        `#`), so the reading edition can stamp each heading with the
        identifier the source already resolved its internal links against.
        """
        return [identifier for _, _, identifier in self.outline]

    @property
    def ok(self):
        return not self.problems

    def preserved_lines(self):
        return [
            PRESERVED_WITHOUT_TRANSLATION.format(kind=kind, count=count)
            for kind, count in sorted(self.preserved.items())
            if count
        ]

    def raise_if_problems(self, stage):
        if self.problems:
            raise PipelineError("\n".join(str(p) for p in self.problems), stage=stage)


def find_pandoc(explicit=None):
    """The Pandoc to run, resolved before any paid work happens.

    Resolves the executable and checks its release: a Pandoc older than
    PANDOC_MIN_VERSION writes a table of contents the navigation check
    refuses, and that refusal would otherwise come after the translation.
    """
    candidate = explicit or shutil.which("pandoc")
    if not candidate:
        raise PipelineError(PANDOC_REQUIRED)
    path = Path(candidate)
    if path.is_absolute() or path.parent != Path("."):
        if not path.is_file():
            raise PipelineError(PANDOC_REQUIRED)
        resolved = str(path)
    else:
        resolved = shutil.which(candidate)
        if not resolved:
            raise PipelineError(PANDOC_REQUIRED)
    require_pandoc_version(resolved)
    return resolved


def pandoc_version(pandoc):
    result = run_tool([pandoc, "--version"])
    first = (result.stdout or "").splitlines()[:1]
    return first[0].strip() if first else "pandoc"


def require_pandoc_version(pandoc):
    """Refuse a Pandoc older than PANDOC_MIN_VERSION, naming what was found."""
    found = pandoc_version(pandoc)
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?(?:\.(\d+))?", found)
    if not match:
        raise PipelineError(PANDOC_TOO_OLD.format(found=found))
    release = tuple(int(part or 0) for part in match.groups())
    if release < PANDOC_MIN_VERSION:
        raise PipelineError(PANDOC_TOO_OLD.format(found=found))


def run_tool(argv, stdin_text=None):
    """One argument array, never a shell string."""
    try:
        result = subprocess.run(
            argv,
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as err:
        raise PipelineError(f"could not run {argv[0]}: {err}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise PipelineError(
            f"{Path(argv[0]).name} exited {result.returncode}: {detail or 'no output'}"
        )
    return result


def parse_markdown(pandoc, text):
    """The document as Pandoc's JSON AST."""
    result = run_tool([pandoc, "-f", MARKDOWN_FORMAT, "-t", "json"], stdin_text=text)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as err:
        raise PipelineError(f"pandoc returned unreadable JSON: {err}")


def inspect(text, *, root, pandoc, for_translation=False):
    """Walk one Markdown document and report resources, limits and refusals."""
    ast = parse_markdown(pandoc, text)
    report = Report()
    walker = _Walker(report, Path(root), for_translation=for_translation)
    walker.scan_raw(text)
    walker.blocks(ast.get("blocks") or [])
    walker.resolve_anchors()
    return report


def _with_lines(text, pattern):
    """`(match, 1-based line number)` for each match of `pattern` in `text`,
    newlines counted once over the whole scan."""
    last_pos, last_line = 0, 1
    for match in pattern.finditer(text):
        last_line += text.count("\n", last_pos, match.start())
        last_pos = match.start()
        yield match, last_line


class _Walker:
    def __init__(self, report, root, *, for_translation=False):
        self.report = report
        self.for_translation = for_translation
        self.root = Path(root)
        self.heading = None
        self.block_index = 0
        self.depth = 0
        self.seen_ids = set()
        self.pending_anchors = []

    # -- locations ----------------------------------------------------
    def location(self):
        if self.heading:
            return f'block {self.block_index} under heading "{self.heading}"'
        return f"block {self.block_index}"

    def problem(self, kind, location=None):
        self.report.problems.append(Problem(kind, location or self.location()))

    def preserve(self, kind):
        self.report.preserved[kind] = self.report.preserved.get(kind, 0) + 1

    # -- raw text -----------------------------------------------------
    def scan_raw(self, text):
        for match, line in _with_lines(text, CONTROL_CHARACTER):
            self.problem(
                f"control character U+{ord(match.group()):04X}", f"line {line}"
            )
        text = blank_out_code(text)
        for match, line in _with_lines(text, REFERENCE_IMAGE):
            self.problem("reference-style image", f"line {line}")
        for match, line in _with_lines(text, HTML_IMAGE):
            self.problem("HTML image", f"line {line}")

    # -- blocks -------------------------------------------------------
    def blocks(self, blocks, top=True):
        # `depth` is how deeply nested this list is; only the outermost one
        # holds the headings the Markdown loader also treats as headings.
        self.depth += 0 if top else 1
        try:
            for block in blocks:
                if top:
                    self.block_index += 1
                self.block(block)
        finally:
            self.depth -= 0 if top else 1

    def block(self, block):
        kind = block.get("t")
        content = block.get("c")
        if kind == "Header":
            attr, inlines = content[1], content[2]
            self.heading = plain_text(inlines)
            level = content[0]
            identifier = attr[0] if attr else ""
            self.report.headings.append((level, self.heading, identifier))
            if level == 1:
                self.report.top_level_headings += 1
            elif level == 2:
                self.report.second_level_headings += 1
            if identifier:
                if identifier in self.seen_ids:
                    self.problem(f"duplicate heading identifier {identifier!r}")
                self.seen_ids.add(identifier)
            if self.depth == 0:
                self.report.outline.append((level, self.heading, identifier))
            self.inlines(inlines)
            return
        if kind == "Table":
            self.preserve("tables")
            return
        if kind == "CodeBlock":
            self.preserve("code blocks")
            return
        if kind == "RawBlock":
            fmt, raw = content[0], content[1]
            if HTML_COMMENT.match(raw or ""):
                return
            if fmt == "html" and _is_html_table(raw or ""):
                # An extractor's tables arrive like this. Carried through
                # once, in the source language, never sent to the model.
                self.preserve("HTML tables")
                return
            self.problem(_raw_kind(fmt, raw))
            return
        if kind in ("Para", "Plain", "LineBlock"):
            self.inlines(_flatten(content))
            return
        if kind == "Div":
            if self.for_translation:
                self.problem("source fenced div")
            # Divs are transparent to Pandoc's TOC; block quotes/lists are not.
            self.blocks(content[1], top=True)
            return
        if kind == "BlockQuote":
            self.blocks(content, top=False)
            return
        if kind in ("BulletList",):
            for item in content:
                self.blocks(item, top=False)
            return
        if kind == "OrderedList":
            for item in content[1]:
                self.blocks(item, top=False)
            return
        if kind == "DefinitionList":
            for term, definitions in content:
                self.inlines(term)
                for definition in definitions:
                    self.blocks(definition, top=False)
            return
        if kind == "Figure":
            self.blocks(content[2], top=False)
            return
        if kind in ("HorizontalRule", "Null"):
            return
        # Anything Pandoc knows that this pipeline has never carried.
        self.problem(f"unsupported block {kind}")

    # -- inlines ------------------------------------------------------
    def inlines(self, inlines):
        for inline in inlines or []:
            self.inline(inline)

    def inline(self, inline):
        if not isinstance(inline, dict):
            return
        kind = inline.get("t")
        content = inline.get("c")
        if kind == "Image":
            self.image(content)
            return
        if kind == "Link":
            self.link(content)
            return
        if kind == "Math":
            # Carried, not translated: the reading edition protects the
            # same spans Pandoc calls math, so the formula reaches the
            # output unchanged and the prose around it is translated.
            self.preserve("math")
            return
        if kind == "Note":
            # One definition, kept in the source language. The reference
            # stays on the source copy of the paragraph, which is what
            # keeps a single definition with a working backlink.
            self.preserve("footnotes")
            self.blocks(content, top=False)
            return
        if kind == "RawInline":
            fmt, raw = content[0], content[1]
            if HTML_COMMENT.match(raw or "") or ALLOWED_INLINE_HTML.match(raw or ""):
                return
            self.problem(_raw_kind(fmt, raw))
            return
        if kind in (
            "Emph",
            "Strong",
            "Strikeout",
            "Superscript",
            "Subscript",
            "SmallCaps",
            "Underline",
        ):
            self.inlines(content)
            return
        if kind in ("Quoted", "Cite"):
            self.inlines(content[1])
            return
        if kind == "Span":
            self.inlines(content[1])
            return
        # Str, Space, SoftBreak, LineBreak, Code — nothing to resolve.

    # -- resources ----------------------------------------------------
    def image(self, content):
        target = _target(content[2])
        if not target:
            self.problem("image with no target")
            return
        scheme = urlparse(target).scheme.lower()
        if scheme in REMOTE_SCHEMES:
            self.problem(f"remote image {target!r}")
            return
        relative = _local_path(target)
        if Path(relative).is_absolute():
            self.problem(f"absolute image path {target!r}")
            return
        try:
            resolved = contained_path(self.root, relative, what="image")
        except PipelineError:
            self.problem(f"image outside the bundle {target!r}")
            return
        if not resolved.is_file():
            self.problem(f"missing image {target!r}")
            return
        self.report.images.append(relative)
        self.inlines(content[1])

    def link(self, content):
        target = _target(content[2])
        self.inlines(content[1])
        if not target:
            return
        if target.startswith("#"):
            self.pending_anchors.append((target[1:], self.location()))
            return
        scheme = urlparse(target).scheme.lower()
        if scheme:
            # An ordinary external hyperlink stays as it is; it does not
            # make the ebook depend on the network to render.
            return
        relative = _local_path(target).split("#", 1)[0]
        if not relative:
            return
        try:
            resolved = contained_path(self.root, relative, what="link")
        except PipelineError:
            self.problem(f"link outside the bundle {target!r}")
            return
        if not resolved.is_file():
            self.problem(f"missing link target {target!r}")

    def resolve_anchors(self):
        known = {identifier for _, _, identifier in self.report.headings if identifier}
        self.report.heading_ids = sorted(known)
        for anchor, location in self.pending_anchors:
            if anchor not in known:
                self.problem(f"link to unknown anchor #{anchor}", location)


FENCE = re.compile(r"^(\s*)(`{3,}|~{3,}).*$")


def blank_out_code(text):
    """`text` with fenced code replaced by empty lines of the same count.

    The two raw-text checks below look for forms Pandoc's AST cannot
    distinguish from prose. Neither means anything inside a code fence, and
    a book that documents `<img>` must not be refused for printing it.
    """
    lines = text.splitlines()
    out = []
    marker = None
    for line in lines:
        if marker is None:
            match = FENCE.match(line)
            if match:
                marker = match.group(2)
                out.append("")
                continue
            out.append(line)
        else:
            out.append("")
            stripped = line.strip()
            if stripped.startswith(marker[0] * len(marker)) and set(stripped) <= {
                marker[0]
            }:
                marker = None
    return "\n".join(out)


HTML_TABLE_START = re.compile(r"^\s*<table\b", re.I)


def _is_html_table(raw):
    """A complete, parseable `<table>` element and nothing else.

    Complete matters: an unclosed table is the shape that silently loses
    the rest of a chapter, and it is refused by name instead.
    """
    if not HTML_TABLE_START.match(raw):
        return False
    try:
        ElementTree.fromstring(raw.strip())
    except ElementTree.ParseError:
        return False
    return True


def _raw_kind(fmt, raw):
    snippet = (raw or "").strip().splitlines()[:1]
    head = snippet[0][:40] if snippet else ""
    if HTML_IMAGE.search(head):
        return "HTML image"
    if re.match(r"^\s*</?table\b", head, re.I):
        return "malformed HTML table"
    return f"raw {fmt} {head!r}" if head else f"raw {fmt}"


def _target(target):
    value = (target or ["", ""])[0]
    value = (value or "").strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    return value


def _local_path(target):
    return unquote(target.split("#", 1)[0])


def _flatten(content):
    # LineBlock is a list of inline lists; Para/Plain are already flat.
    if content and isinstance(content[0], list):
        flat = []
        for line in content:
            flat.extend(line)
        return flat
    return content


def plain_text(inlines):
    parts = []
    for inline in inlines or []:
        if not isinstance(inline, dict):
            continue
        kind, content = inline.get("t"), inline.get("c")
        if kind == "Str":
            parts.append(content)
        elif kind in ("Space", "SoftBreak", "LineBreak"):
            parts.append(" ")
        elif kind == "Code":
            parts.append(content[1])
        elif kind in (
            "Emph",
            "Strong",
            "Strikeout",
            "Superscript",
            "Subscript",
            "SmallCaps",
            "Underline",
        ):
            parts.append(plain_text(content))
        elif kind == "Quoted":
            opening, closing = (
                ("‘", "’") if content[0]["t"] == "SingleQuote" else ("“", "”")
            )
            parts.append(opening + plain_text(content[1]) + closing)
        elif kind in ("Cite", "Span", "Link", "Image"):
            parts.append(plain_text(content[1]))
    return "".join(parts).strip()
