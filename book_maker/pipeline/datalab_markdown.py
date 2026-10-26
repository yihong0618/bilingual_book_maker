"""Datalab's Markdown, rewritten into the forms this pipeline carries.

Datalab Convert (`paginate=true`) writes a few things nothing downstream
reads correctly, all measured on a scanned textbook (Purcell, 261026):

* a page separator line, `{30}------------------------------------------------`
  (0-based), which the Markdown loader passes through but Pandoc prints as
  text in the book. It becomes the route's own page marker,
  `<!-- page 31 -->` (`pdf_common.PAGE_MARKER`, 1-based);
* `<math>...</math>` around TeX, inside table cells: raw HTML to Pandoc, so
  the EPUB would show TeX source inside an element no reader renders. It
  becomes `$...$` (or `$$...$$` for `display="block"` outside a table), which
  Pandoc writes as MathML like every other formula;
* a paragraph cut by the page break, with the page's footnotes printed
  between the two halves: `... the mission of Pioneer` / `<sup>†</sup>...` /
  separator / `10. The spatial variation ...`. Left alone, the second half is
  a paragraph of its own -- here an ordered list starting at 10 -- and each
  half is translated without the other. The halves are joined and the
  footnotes and the marker are moved after the joined paragraph, so a
  marker there is the page boundary to within one paragraph. Joined only
  across consecutive pages, only prose to prose, only when the first half
  ends with no punctuation at all and the second starts in lower case, a
  digit or a formula. A hyphen at the break is kept (`assem-bled`): it
  cannot be told from a compound split at its own hyphen.

What is left alone, on purpose: the figure descriptions Datalab writes as
body text after an image even with captions disabled (they are not marked
and cannot be told from prose), and heading levels.
"""

import html
import re
from dataclasses import dataclass

# `{N}` and a long run of dashes, alone on the line.
SEPARATOR = re.compile(r"^\{(\d+)\}-{8,}\s*$")
MATH_ELEMENT = re.compile(
    r"<math(?P<attrs>\s[^>]*)?>(?P<tex>.*?)</math>", re.DOTALL | re.IGNORECASE
)
BLOCK_DISPLAY = re.compile(r"""display\s*=\s*["']block["']""", re.IGNORECASE)
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
# A page-bottom footnote as Datalab prints it: the mark in <sup>, then text.
FOOTNOTE = re.compile(r"^<sup>[*†‡§¶‖#0-9]{1,3}</sup>\s*\S")
# The last character of a paragraph the page cut: a word, a number, a comma,
# a hyphen or an inline formula. A paragraph that ends a sentence, a
# heading-like line ending in markup, or an equation introduction ending
# in a colon is never joined.
RUNS_ON = re.compile(r"[\w,\-$]$")
# The first character of a continuation: lower case, a digit or a formula.
CONTINUES = re.compile(r"^[a-z0-9$(]")
# A thematic break, which Datalab writes above a page's footnotes.
RULE = re.compile(r"^([-*_])( *\1){2,} *$")
MARKER = re.compile(r"^<!--\s*page\s+(\d+)\s*-->$")
# Shorter than this and the "paragraph" is a figure label or a stray line,
# never a sentence the page cut in two.
MIN_PROSE = 40


@dataclass
class Normalized:
    text: str
    pages: int = 0
    math: int = 0
    joined: int = 0

    def lines(self):
        """What the rewrite did, for the terminal and the manifest."""
        return [
            f"Datalab Markdown normalized: {self.pages} page separators to page "
            f"markers, {self.math} <math> elements to $...$, {self.joined} "
            f"paragraphs rejoined across a page break."
        ]


def is_datalab(text):
    """Whether `text` carries Datalab's page separators."""
    return any(SEPARATOR.match(line) for line in text.splitlines())


def normalize(text):
    """`Normalized` for one Datalab document."""
    lines = text.splitlines()
    pages = 0
    out = []
    for line in lines:
        match = SEPARATOR.match(line)
        if match:
            out.append(f"<!-- page {int(match.group(1)) + 1} -->")
            pages += 1
        else:
            out.append(line)
    blocks = _blocks(out)
    blocks, math = _math(blocks)
    blocks, joined = _join_across_pages(blocks)
    body = "\n\n".join(blocks).strip("\n") + "\n"
    return Normalized(body, pages=pages, math=math, joined=joined)


def _blocks(lines):
    """Blank-line separated blocks; a fence or a `$$` run is never split."""
    blocks, current = [], []
    fence = None
    display = False
    for line in lines:
        stripped = line.strip()
        if fence is None and not display and not stripped:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
        match = FENCE.match(line)
        if fence is None and not display and match:
            fence = match.group(1)
        elif fence is not None and stripped.startswith(fence[0] * len(fence)):
            if set(stripped) <= {fence[0]} and len(current) > 1:
                fence = None
        elif fence is None and stripped.count("$$") % 2:
            display = not display
    if current:
        blocks.append("\n".join(current))
    return blocks


def _math(blocks):
    count = 0
    result = []
    for block in blocks:
        if FENCE.match(block):
            result.append(block)
            continue
        in_table = block.lstrip().startswith("|")

        def replace(match):
            nonlocal count
            tex = html.unescape(match.group("tex")).strip()
            if not tex:
                return ""
            count += 1
            # A pipe table cell holds inlines only, and an unescaped bar
            # would end the cell: inline form there, bars escaped.
            if in_table:
                return "$" + tex.replace("|", r"\vert{}") + "$"
            if BLOCK_DISPLAY.search(match.group("attrs") or ""):
                return f"$${tex}$$"
            return f"${tex}$"

        result.append(MATH_ELEMENT.sub(replace, block))
    return result, count


def _kind(block):
    stripped = block.strip()
    if MARKER.match(stripped):
        return "marker"
    if FOOTNOTE.match(stripped):
        return "footnote"
    if stripped.startswith(("#", "![", "|", "$$", "<", ">", "```", "~~~", "- ", "* ")):
        return "other"
    return "prose"


def _join_across_pages(blocks):
    """Join a paragraph the page break cut, moving what sat between them."""
    blocks = list(blocks)
    joined = 0
    index = 0
    last_page = None
    while index < len(blocks):
        if _kind(blocks[index]) != "marker":
            index += 1
            continue
        page = int(MARKER.match(blocks[index].strip()).group(1))
        consecutive = last_page is not None and page == last_page + 1
        last_page = page
        if not consecutive:
            index += 1
            continue
        # Back over the page-bottom footnotes to the paragraph they followed,
        # and over the rule Datalab may draw above them (Purcell p. 403).
        before = index - 1
        while before >= 0 and _kind(blocks[before]) == "footnote":
            before -= 1
        if before < index - 1 and before >= 0 and RULE.match(blocks[before].strip()):
            before -= 1
        after = index + 1
        if before < 0 or after >= len(blocks):
            index += 1
            continue
        head, tail = blocks[before], blocks[after]
        if not (
            _kind(head) == "prose"
            and _kind(tail) == "prose"
            and len(head.strip()) >= MIN_PROSE
            and RUNS_ON.search(head.rstrip())
            and CONTINUES.match(tail.lstrip())
        ):
            index += 1
            continue
        head = head.rstrip()
        tail = tail.lstrip()
        merged = head + tail if head.endswith("-") else f"{head} {tail}"
        between = blocks[before + 1 : after]
        blocks[before : after + 1] = [merged, *between]
        joined += 1
        index = before + 1 + len(between)
    return blocks, joined
