"""Heading levels read from the page, for a parser that gives none.

docling finds a paper's headings almost without fail (195 of 196 on the
260922 corpus of twenty arXiv papers) and then writes every one of them,
the title included, at the same level: `##`. The EPUB's table of contents
is that outline, so it came out flat. Measured on that corpus, 42.6% of
heading levels were exact as shipped.

The level is decided here, before the export, from two things the page
can tell us:

1. **Section numbering**, when a heading carries it: `1 Introduction` is
   level 2, `1.1` level 3, `1.1.1` level 4; `I.` is 2, `A.` is 3, `1)` is
   4. Numbering is the author's own statement of depth and beats any
   typographic guess -- `3.1` and `3.2.1` can be set in the same font. A
   bare integer (`2024 was the year`) counts only with a neighbour: another
   heading numbered one above or below it, or a dotted one under it
   (`3.1`), so a chapter run starting anywhere is read and a year is not.
2. **Typographic style** otherwise: the rendered font size and whether the
   glyphs under the heading's box are bold, read from the PDF's text layer
   with pypdfium2. An unnumbered heading takes the level of a numbered
   heading set in the same style; failing that, the largest style is
   level 1 (the title), and anything else is level 2. The numbered
   sibling is asked first so that, when docling has already taken the
   title out as a title item, `Abstract` in the section font does not
   become a second title. When the document has a title item, the
   largest-style rule does not fire at all (260923): with no numbered
   sibling to anchor it, the section style would otherwise be the
   largest left and every section would stand level with the title.

docling 2.129 ships its own `HeadingHierarchyModel` (off by default:
bookmarks, then numbering, then cell style). Measured on the same corpus
(260922, `heading_hierarchy_options.enabled`, `generate_parsed_pages`):
24.6% of levels exact, and on 15 of 20 papers the title sits at the level
of its sections, because it compresses `1 Introduction` to level 1. That
is the flat contents page again, so it is not used.

Measured 260922 (headings-eval, same corpus): 95.9% of levels exact, 19 of
20 papers entirely right. The one miss is a paper with no numbering whose
two heading levels share one font. A scanned page has no glyphs, so only
rule 1 applies there and everything unnumbered is level 2.

The style rank -- one level per distinct (size, weight) pair, largest
first -- is opendataloader-pdf's `HeadingProcessor.detectHeadingsLevels`
(Apache License 2.0, Copyright Hancom Inc.; `HeadingProcessor.java`,
`TextNodeStatistics.java`, `ModeWeightStatistics.java`), written afresh in
Python. Its rendered-size measure (the `Tf` size times the text matrix
scale) and font-weight fallback follow the same idea. Nothing of
veraPDF's heading *detector* is used: docling's is far better, and that
code is under a different licence.
"""

import collections
import ctypes
import re

# `2.3 Method`, `2.3.1. Detail`: the dots state the depth.
DOTTED = re.compile(r"^(\d+(?:\.\d+)+)\.?\s+\S")
# `1. Introduction`: the dot makes it a marker.
ARABIC = re.compile(r"^\d+\.\s+\S")
# `1 Introduction`: a number alone, read as a marker only with a neighbour.
BARE = re.compile(r"^(\d+)\s+\S")
ROMAN = re.compile(r"^[IVXL]+\.\s+\S")
LETTER = re.compile(r"^[A-H]\.\s+\S")
PAREN = re.compile(r"^\d+\)\s+\S")
MAX_LEVEL = 6
# A font descriptor weight from here up is bold. 600 is semibold; on a
# heading that reads as bold.
BOLD = 600
# When the PDF gives no /FontWeight (the base-14 Times-Bold, for one), the
# face name says it; order matters, `Bold` after `SemiBold`.
NAME_WEIGHTS = (
    ("Thin", 100),
    ("ExtraLight", 200),
    ("UltraLight", 200),
    ("Light", 300),
    ("SemiBold", 600),
    ("Semibold", 600),
    ("DemiBold", 600),
    ("ExtraBold", 800),
    ("UltraBold", 800),
    ("Bold", 700),
    ("Black", 900),
    ("Heavy", 900),
    ("Medium", 500),
    ("Regular", 400),
    ("Book", 400),
)
# Sizes within half a point are one style: TeX sets 9.96 and 10.0.
SIZE_STEP = 0.5


def numbering_level(text, bare=False):
    """The level a heading's own numbering states, or None.

    `bare` accepts a number without a dot (`1 Introduction`); `levels`
    grants it only with the evidence of a neighbour.
    """
    text = text.strip()
    dotted = DOTTED.match(text)
    if dotted:
        return min(MAX_LEVEL, dotted.group(1).count(".") + 2)
    if ARABIC.match(text):
        return 2
    if ROMAN.match(text):
        return 2
    if LETTER.match(text):
        return 3
    if PAREN.match(text):
        return 4
    if bare and BARE.match(text):
        return 2
    return None


def _vouched(headings):
    """The indices of bare leading integers a neighbour vouches for.

    `7 Results` is numbering beside `8 Discussion` set in the same style,
    or above `7.1 Setup`; alone, or beside `2025 outlook` in another
    style, it is a title that starts with a number. A run of years in one
    style (`2024 …`, `2025 …`) still passes, as it would for a reader. A
    dotted heading vouches for its parent -- the nearest heading of its
    number above it, bare or `N.` -- and for nothing else, so a title
    that happens to start with the same number is not reached past a
    real parent. Numbering asserted by a dot or a paren needs no
    vouching.
    """
    bare = {}  # index -> (number, style)
    parents = []  # (index, number, is bare), in order
    marked = set()  # (number, style) of every `N.` heading
    vouched = set()
    for index, (text, style) in enumerate(headings):
        text = text.strip()
        dotted = DOTTED.match(text)
        if dotted:
            number = int(dotted.group(1).split(".")[0])
            parent = next(
                ((i, is_bare) for i, n, is_bare in reversed(parents) if n == number),
                None,
            )
            if parent and parent[1]:
                vouched.add(parent[0])
            continue
        marker = re.match(r"^(\d+)\.\s+\S", text)
        if marker:
            marked.add((int(marker.group(1)), style))
            parents.append((index, int(marker.group(1)), False))
            continue
        if numbering_level(text) is not None:
            continue  # a roman, letter or paren marker
        alone = BARE.match(text)
        if alone:
            bare[index] = (int(alone.group(1)), style)
            parents.append((index, int(alone.group(1)), True))
    peers = set(bare.values()) | marked
    for index, (number, style) in bare.items():
        if (number - 1, style) in peers or (number + 1, style) in peers:
            vouched.add(index)
    return vouched


def levels(headings, title_present=False):
    """Levels for `[(text, style or None), ...]`, in that order.

    A style is `(size, bold)`. The rule is the module docstring's, with one
    exception: when the document already has its title as a title item
    (`title_present`: docling's own, or one the region-role pass made),
    no section header is promoted to level 1 for being the largest style
    left -- that style is the sections', and the title would otherwise
    sit level with them (measured 260923 on arxiv_2010_03667 and
    web_en_chrome_1, docs/260923-feat-PDF_ROLE_DECISIONS.md). Numbering
    and the numbered-sibling rule are unchanged.
    """
    vouched = _vouched(headings)
    numbers = [
        numbering_level(text, bare=index in vouched)
        for index, (text, _style) in enumerate(headings)
    ]
    styled = [style for _text, style in headings if style is not None]
    top = max(styled) if styled else None
    by_style = {}
    for (_text, style), number in zip(headings, numbers):
        if number and style is not None:
            by_style.setdefault(style, number)
    out = []
    for (_text, style), number in zip(headings, numbers):
        if number:
            out.append(number)
        elif style in by_style:
            out.append(by_style[style])
        elif style is not None and style == top and not title_present:
            out.append(1)
        else:
            out.append(2)
    return out


def _weight(raw, textpage, index, buffer, flags):
    weight = raw.FPDFText_GetFontWeight(textpage, index)
    if weight > 0:
        return weight
    raw.FPDFText_GetFontInfo(textpage, index, buffer, len(buffer), ctypes.byref(flags))
    name = buffer.value.decode("latin1", "replace")
    return next((value for key, value in NAME_WEIGHTS if key in name), 400)


def _glyphs(raw, textpage):
    """`(x, y, rendered size, weight)` per visible character."""
    buffer = ctypes.create_string_buffer(256)
    flags = ctypes.c_int()
    matrix = raw.FS_MATRIX()
    for index in range(textpage.count_chars()):
        char = chr(raw.FPDFText_GetUnicode(textpage, index))
        if char.isspace() or char == "\x00":
            continue
        left, bottom, right, top = textpage.get_charbox(index)
        raw.FPDFText_GetMatrix(textpage, index, ctypes.byref(matrix))
        scale = (matrix.c * matrix.c + matrix.d * matrix.d) ** 0.5
        size = raw.FPDFText_GetFontSize(textpage, index) * scale
        yield (
            (left + right) / 2,
            (bottom + top) / 2,
            size,
            _weight(raw, textpage, index, buffer, flags),
        )


def _display(page):
    """User-space point -> the point on the page as displayed.

    The frame is docling's: CropBox origin at (0, 0), the page turned by
    its `/Rotate`. The four cases were measured against docling's own
    heading boxes on a page saved at each rotation (260922).
    """
    left, bottom, right, top = page.get_bbox()
    width, height = right - left, top - bottom
    rotation = page.get_rotation()

    def display(x, y):
        x, y = x - left, y - bottom
        if rotation == 90:
            return y, width - x
        if rotation == 180:
            return width - x, height - y
        if rotation == 270:
            return height - y, x
        return x, y

    return display


def styles(pdf_path, boxes):
    """`{index: (size, bold) or None}` for `boxes = {index: (page, box)}`.

    The box is docling's: PDF points, bottom-left origin, relative to the
    CropBox, on the page as displayed (`/Rotate` applied: a 90-degree page
    is reported as wide as it is tall, measured 260922 on docling 2.129).
    pdfium's character boxes are in user space, unrotated, so the CropBox
    origin is subtracted and the page's rotation applied before a glyph is
    looked for under a box. A box with no glyphs under it (a scan) gets
    None.
    """
    from .pdf_common import _pdfium

    pdfium, raw = _pdfium()
    by_page = collections.defaultdict(list)
    for index, (page_number, box) in boxes.items():
        by_page[page_number].append((index, box))
    found = {index: None for index in boxes}
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for page_number, wanted in by_page.items():
            page = document[page_number - 1]
            display = _display(page)
            textpage = page.get_textpage()
            glyphs = [
                (*display(x, y), size, weight)
                for x, y, size, weight in _glyphs(raw, textpage)
            ]
            for index, (l, b, r, t) in wanted:
                inside = [
                    (size, weight)
                    for x, y, size, weight in glyphs
                    if l - 1 <= x <= r + 1 and b - 1 <= y <= t + 1
                ]
                if not inside:
                    continue
                size = max(size for size, _weight in inside)
                weight = collections.Counter(w for _s, w in inside).most_common(1)[0][0]
                found[index] = (round(size / SIZE_STEP) * SIZE_STEP, weight >= BOLD)
    finally:
        document.close()
    return found


def assign(document, pdf_path):
    """Set every section header's level. Returns `(headings, with a style)`.

    A title item anywhere in the document holds level 1 by itself, so no
    section header is made a second title (see `levels`).
    """
    items = []
    title_present = False
    for item, _level in document.iterate_items():
        label = str(getattr(item, "label", "")).lower()
        if label.endswith("title"):
            title_present = True
        if label != "section_header":
            continue
        items.append(item)
    boxes = {}
    for index, item in enumerate(items):
        prov = list(getattr(item, "prov", None) or [])
        if prov:
            box = prov[0].bbox
            boxes[index] = (
                prov[0].page_no,
                (float(box.l), float(box.b), float(box.r), float(box.t)),
            )
    found = styles(pdf_path, boxes) if boxes else {}
    headings = [(item.text or "", found.get(index)) for index, item in enumerate(items)]
    for item, level in zip(items, levels(headings, title_present)):
        item.level = level
    return len(items), sum(style is not None for _text, style in headings)


_ATX = re.compile(r"^#(#+\s)")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CLOSER = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*$")


def promote(markdown):
    """Every heading one level up.

    The serializer writes a section header with one hash more than its
    level, keeping `#` for a title item, so with levels assigned the top
    heading arrives as `##`. Lifting every `##`-or-deeper line by one puts
    it at `#`, and a title item already there stays. A code block's lines
    are left alone: the serializer fences a code item, and `## comment`
    inside it is code.
    """
    out = []
    fence = None
    for line in markdown.split("\n"):
        if fence is None:
            found = _FENCE.match(line)
            if found:
                fence = found.group(1)
            else:
                line = _ATX.sub(r"\1", line)
        else:
            # A closer is the same character, at least as long, and
            # nothing after it: "```python" inside a block is code.
            closer = _CLOSER.match(line)
            if (
                closer
                and closer.group(1)[0] == fence[0]
                and len(closer.group(1)) >= len(fence)
            ):
                fence = None
        out.append(line)
    return "\n".join(out)
