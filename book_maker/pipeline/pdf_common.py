"""What reading a PDF needs regardless of which parser reads it.

Page geometry, the scan test, and the Markdown post-processing that runs on
whatever the parser produced: page markers, the heading a page selection
needs, the density warning and the check that an OCR pass actually returned
words. None of it knows which engine wrote the Markdown, which is the point
-- these rules are about the book and the page, not about the parser, and a
second copy of them inside an adapter would be a second set of answers.
"""

import ctypes
import re
from pathlib import Path

from .bundle import parse_pages
from .errors import PipelineError
from .preflight import CONTROL_CHARACTER
from .messages import (
    OCR_EMPTY,
    OCR_EMPTY_PAGES,
    PDF_ROUTE_NOT_INSTALLED,
    PDFIUM_UNUSABLE,
)

STAGE = "extract"

# An HTML comment is a block the Markdown loader passes through untouched
# and Pandoc drops from the rendered book, so the provenance marker
# survives translation without becoming prose. Pages are numbered from 1.
PAGE_MARKER = re.compile(r"<!--\s*page\s+(\d+)\s*-->")
# Items docling gave no page are written once after the last page, under
# this line (`docling_parser._export_pages`). Every page-based check ends
# the numbered pages here (Codex 260924: the tail counted as text on the
# final page and hid an empty scanned one).
UNPLACED_MARKER = "<!-- unplaced -->"


def numbered_pages(markdown_text):
    """The Markdown up to the unplaced tail: what the page checks read."""
    return markdown_text.split(UNPLACED_MARKER, 1)[0]


COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

# More prose than a printed page can show. A dense two-column page at nine
# points holds six or seven thousand characters; the measured failure
# (arXiv 2609.20519, page 1) returned a hundred thousand. Well above the
# first, well below the second; a warning, not a refusal.
PAGE_CHARS_LIMIT = 12000

# A page is a scan when a picture covers this much of it and the text
# layer holds fewer than this many characters: a stamped page number or a
# running header over a scanned page is not a text layer. Guards, not
# measurements; a blank page (no picture, no text) also counts as unread.
SCAN_IMAGE_AREA = 0.6
SCAN_MAX_CHARS = 200

# A page carries only an invisible text layer -- a scanned book with the
# recognised text laid under the picture in render mode 3 (or 7, clip
# only, which paints nothing either) -- when it has more invisible
# characters than visible ones and at most this many visible ones, so a
# visible page number or running header does not hide it. Measured 260923
# on pages 1-2 of 60 fixtures: the Internet Archive and hekate scans are
# all mode 3, every born-digital page has no invisible character at all
# (ABBYY's layer on innerspace is mode 0, drawn under the picture, and is
# not reported). Guards, not measurements.
INVISIBLE_MAX_VISIBLE_CHARS = 20


class TextLayerReport(tuple):
    """`(missing, examined)`, as `text_layer_report` always returned it,
    with the pages that carry only an invisible text layer as `invisible`.

    A tuple, so every caller that unpacks or compares the pair is
    unchanged; the third answer is read by name.
    """

    def __new__(cls, missing, examined, invisible=()):
        report = super().__new__(cls, (missing, examined))
        report.invisible = list(invisible)
        return report


def _invisible_only(raw, textpage):
    """Whether the page's text is all but a handful of characters invisible.

    Counted character by character from the text page: each character's
    text object says its render mode. Whitespace is skipped, and the count
    stops as soon as the visible characters exceed the allowance, so a
    typed page costs a few dozen calls.
    """
    hidden_modes = (raw.FPDF_TEXTRENDERMODE_INVISIBLE, raw.FPDF_TEXTRENDERMODE_CLIP)
    visible = invisible = 0
    for index in range(raw.FPDFText_CountChars(textpage)):
        if chr(raw.FPDFText_GetUnicode(textpage, index)).isspace():
            continue
        obj = raw.FPDFText_GetTextObject(textpage, index)
        if obj and raw.FPDFTextObj_GetTextRenderMode(obj) in hidden_modes:
            invisible += 1
        else:
            visible += 1
            if visible > INVISIBLE_MAX_VISIBLE_CHARS:
                return False
    return invisible > visible


def _pdfium():
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
    except ImportError as err:
        raise PipelineError(PDF_ROUTE_NOT_INSTALLED.format(err=err), stage=STAGE)
    if not hasattr(pdfium, "PdfDocument"):
        raise PipelineError(PDFIUM_UNUSABLE, stage=STAGE)
    return pdfium, raw


def _transform(matrix, box):
    a, b, c, d, e, f = matrix
    left, bottom, right, top = box
    corners = [
        (a * x + c * y + e, b * x + d * y + f)
        for x, y in ((left, bottom), (right, bottom), (left, top), (right, top))
    ]
    return (
        min(x for x, _ in corners),
        min(y for _, y in corners),
        max(x for x, _ in corners),
        max(y for _, y in corners),
    )


def _intersect(first, second):
    left = max(first[0], second[0])
    bottom = max(first[1], second[1])
    right = min(first[2], second[2])
    top = min(first[3], second[3])
    if right <= left or top <= bottom:
        return (0.0, 0.0, 0.0, 0.0)
    return (left, bottom, right, top)


def _area(box):
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _clip_box(raw, pageobj):
    """The box an object's clip confines it to, in its parent's space.

    A clip is the intersection of its paths, so each path's bounding box
    narrows the result; a path that is not a rectangle is over-approximated
    by its box, which can only count text as visible, never as hidden.
    """
    clip = raw.FPDFPageObj_GetClipPath(pageobj)
    if not clip:
        return None
    box = None
    for index in range(raw.FPDFClipPath_CountPaths(clip)):
        xs, ys = [], []
        for segment in range(raw.FPDFClipPath_CountPathSegments(clip, index)):
            point = raw.FPDFClipPath_GetPathSegment(clip, index, segment)
            x, y = ctypes.c_float(), ctypes.c_float()
            if raw.FPDFPathSegment_GetPoint(point, x, y):
                xs.append(x.value)
                ys.append(y.value)
        if not xs:
            continue
        own = (min(xs), min(ys), max(xs), max(ys))
        box = own if box is None else _intersect(box, own)
    return box


def _page_space(raw, obj):
    """`(box, clip)` of an object in page space.

    pdfium keeps each object's bounds and clip in its parent's coordinate
    space. The object's own clip comes first; walking outward, each
    ancestor's matrix carries both into the next space up and the
    ancestor's clip narrows what is left.
    """
    box = obj.get_bounds()
    clip = _clip_box(raw, obj.raw)
    ancestor = obj.container
    while ancestor is not None:
        matrix = ancestor.get_matrix().get()
        box = _transform(matrix, box)
        if clip is not None:
            clip = _transform(matrix, clip)
        own = _clip_box(raw, ancestor.raw)
        if own is not None:
            clip = own if clip is None else _intersect(clip, own)
        ancestor = ancestor.container
    return box, clip


def _page_box(page):
    # The effective page: media box cut to the crop box, inherited or not.
    left, bottom, right, top = page.get_bbox()
    return (float(left), float(bottom), float(right), float(top))


def picture_share(page):
    """How much of the effective page its pictures cover, 0 to 1.

    Pictures at any depth, in page space, cut to their clips: a scanner's
    page image is as often wrapped in a form as drawn directly. Overlaps
    are summed, not unioned; two pictures each covering half a page make
    a scan by this measure too, which is the right answer for a page that
    is pictures and nothing else.
    """
    pdfium, raw = _pdfium()
    page_box = _page_box(page)
    total = _area(page_box)
    if total <= 0:
        return 0.0
    covered = 0.0
    for obj in page.get_objects(max_depth=16):
        if obj.type != raw.FPDF_PAGEOBJ_IMAGE:
            continue
        box, clip = _page_space(raw, obj)
        shown = _intersect(box, page_box)
        if clip is not None:
            shown = _intersect(shown, clip)
        covered += _area(shown)
    return min(1.0, covered / total)


def text_layer_report(pdf_path, page_range=None):
    """`(pages the text layer does not spell out, pages examined)`, from 1.

    Returned as a `TextLayerReport`, whose `invisible` lists the pages
    that do carry a text layer, but only an invisible one: a scanned book
    with its recognised text underneath. Those count as typed here -- the
    layer is usable text -- and are told apart so the operator can be told.

    pypdfium2 comes with the pdf extra for exactly this, and reading what
    the page itself says is the only honest way to know whether the models
    will find anything: a page with no characters, or a page that is one
    big picture with a few characters stamped on it, has to be read by OCR
    or not at all.
    """
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
    except ImportError as err:
        raise PipelineError(PDF_ROUTE_NOT_INSTALLED.format(err=err), stage=STAGE)
    if not hasattr(pdfium, "PdfDocument"):
        # An empty `pypdfium2` directory left behind by an uninstall imports
        # perfectly well and can do nothing; say that, rather than blaming
        # the PDF for it.
        raise PipelineError(PDFIUM_UNUSABLE, stage=STAGE)
    ranges = parse_pages(page_range)
    missing = []
    invisible = []
    examined = 0
    try:
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception as err:
        raise PipelineError(
            f"{Path(pdf_path).name} could not be opened as a PDF: "
            f"{type(err).__name__}: {err}",
            stage=STAGE,
        )
    try:
        for number in range(1, len(document) + 1):
            if ranges and not any(start <= number <= end for start, end in ranges):
                continue
            examined += 1
            page = document[number - 1]
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_bounded()
                chars = len(text.strip())
                if not chars or (
                    chars < SCAN_MAX_CHARS and picture_share(page) >= SCAN_IMAGE_AREA
                ):
                    missing.append(number)
                elif _invisible_only(raw, textpage):
                    invisible.append(number)
            finally:
                textpage.close()
            page.close()
    except Exception as err:
        raise PipelineError(
            f"{Path(pdf_path).name} could not be read page by page: "
            f"{type(err).__name__}: {err}",
            stage=STAGE,
        )
    finally:
        document.close()
    return TextLayerReport(missing, examined, invisible)


def _prose(chunk):
    """What is left of a chunk once markers and pictures are removed."""
    return IMAGE.sub(" ", COMMENT.sub(" ", chunk)).strip()


FIRST_CONTENT_HEADING = re.compile(r"^#{1,6}\s+\S")
LEADING_MARKER = re.compile(r"^<!--.*-->$")


def first_selected_page(page_range):
    """The first page of a selection, from 1; None for the whole PDF."""
    ranges = parse_pages(page_range)
    return ranges[0][0] if ranges else None


TOP_HEADING = re.compile(r"^#\s+\S")


def heading_for_top(markdown_text, first_page, title):
    """The Markdown opened with a level-1 heading, or None if it already is.

    The EPUB's table of contents follows the headings, and Pandoc gives a
    document that does not open with a level-1 heading a book-title entry
    of its own -- at any split level -- which the navigation check then
    refuses as an entry the outline never had. docling writes every
    section heading, the paper's title included, as `##`, so without this
    every page-1 paper failed at export, after the translation was paid
    for (Opus corpus run, 260922: 9 of 12 bundles). A selection that
    starts after page 1 is headed with the page it starts on, as before;
    anything else is headed with `title`. Written into source.md, where
    the operator sees it before anything is paid for and can rename it.

    Returns `(text, heading text)`, or None when the first content line is
    already a level-1 heading or there is no content to head.
    """
    lines = markdown_text.splitlines()
    insert_at = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or LEADING_MARKER.match(stripped):
            insert_at = index + 1
            continue
        if TOP_HEADING.match(stripped):
            return None
        break
    else:
        return None  # nothing but markers: no content to head
    text = f"Page {first_page}" if first_page and first_page >= 2 else title
    heading = [f"# {text}", ""]
    if insert_at and lines[insert_at - 1].strip():
        heading.insert(0, "")
    return "\n".join(lines[:insert_at] + heading + lines[insert_at:]) + "\n", text


def blank_pages(markdown_text):
    """`(page numbers that carry no prose, whether any page does)`."""
    parts = PAGE_MARKER.split(numbered_pages(markdown_text))
    any_prose = bool(_prose(parts[0]))
    blank = []
    for number, body in zip(parts[1::2], parts[2::2]):
        if _prose(body):
            any_prose = True
        else:
            blank.append(int(number))
    return blank, any_prose


def strip_control_characters(markdown_text):
    """`(text, count, pages)`: the Markdown without C0 control characters.

    Tab, LF and CR stay. `pages` names, in order, the pages (from their
    markers) the removed characters stood on; one after the unplaced tail
    marker is named "unplaced", one before any marker is not named.
    """
    stripped, count = CONTROL_CHARACTER.subn("", markdown_text)
    if not count:
        return markdown_text, 0, []
    numbered = numbered_pages(markdown_text)
    parts = PAGE_MARKER.split(numbered)
    pages = []
    for number, body in zip(parts[1::2], parts[2::2]):
        if number not in pages and CONTROL_CHARACTER.search(body):
            pages.append(number)
    if CONTROL_CHARACTER.search(markdown_text[len(numbered) :]):
        pages.append("unplaced")
    return stripped, count, pages


def dense_pages(markdown_text, limit=PAGE_CHARS_LIMIT):
    """`[(page number, characters)]` for pages carrying more prose than fits."""
    parts = PAGE_MARKER.split(numbered_pages(markdown_text))
    dense = []
    for number, body in zip(parts[1::2], parts[2::2]):
        chars = len(_prose(body))
        if chars > limit:
            dense.append((int(number), chars))
    return dense


def check_recognised_text(markdown_path, missing):
    """What the OCR pass actually returned for the pages that needed it.

    A conversion that sends every page to the models and still comes back
    with nothing but pictures has not read the book, and saying "completed"
    over that is the silent failure this pipeline refuses. A single page
    that came back empty is reported instead of refused: a plate with no
    words on it is a legitimate empty page.
    """
    if not missing:
        return []
    blank, any_prose = blank_pages(markdown_path.read_text(encoding="utf-8"))
    if not any_prose:
        raise PipelineError(OCR_EMPTY, stage=STAGE)
    silent = sorted(set(blank) & set(missing))
    if silent:
        print(OCR_EMPTY_PAGES.format(pages=", ".join(str(n) for n in silent)))
    return silent
