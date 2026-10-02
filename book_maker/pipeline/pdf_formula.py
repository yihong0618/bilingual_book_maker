"""Display formulas, kept as pictures.

docling's layout model finds a display equation but does not read it: the
Markdown export writes `<!-- formula-not-decoded -->` where the equation
was, and the mathematics is simply gone from the book. Its own formula
model (`do_formula_enrichment`) was measured and rejected -- it
hallucinated on hard equations, swallowed body prose into `$$` blocks and
cost 117 s a page, 29x the default (docs/260921-eval-DOCLING_VS_OPENDATALOADER.md).

So the equation is preserved the way a reader actually wants it: the
region is cropped out of the PDF and written beside the text as an image.
It is pixel-exact because it *is* the page, it costs no model and no
network, and the translator never sees it -- an image is not prose, so
nothing can be mistranslated into a wrong formula.

Placement is by identity, not by position. Before the export every
undecoded formula is given a marker as its text, the serializer writes
that marker where the equation stands, and the marker is swapped for the
equation's own picture. Whatever order the serializer walks the document
in -- tables, groups, captions -- a picture can only ever land where its
own item was written.

What it does not do: inline mathematics inside a paragraph is not a
formula region at all, and on a scan it arrives as whatever OCR made of
it. That text is untouched here.
"""

import re

from .messages import (
    FORMULA_NO_POSITION,
    FORMULA_NOT_EXPORTED,
    FORMULA_REGION_OVERSIZE,
    FORMULA_UNPLACEABLE,
)

# The serializer's placeholder, verbatim: what an undecoded formula leaves
# when it is not turned into a picture. PIN: docling-core's Markdown
# serializer writes `$$text$$` when a FormulaItem has text, this comment
# when it has none but has `orig`, and NOTHING AT ALL when it has neither.
# `mark` gives every undecoded formula a text, so the third branch is
# never reached and every equation leaves a trace.
PLACEHOLDER = "<!-- formula-not-decoded -->"
# The marker `mark` writes as an undecoded formula's text. The serializer
# passes a formula's text through unescaped, wrapped in `$$` as a block,
# `$` inline, and bare inside a nested table cell; the pattern accepts all
# three, and any index (four digits is a minimum width, not a cap).
MARKER = "bbm-formula-{index:04d}"
_MARKER_RE = re.compile(r"\${0,2}bbm-formula-(\d{4,})\${0,2}")

IMAGE_DIR = "images"
# 3x the PDF's own 72 dpi. Enough that a subscript stays legible on a
# high-density screen without making the bundle heavy.
SCALE = 3.0
# Padding, measured. The layout box sits tight against the glyphs and
# clips a tall bracket or an integral sign, so the horizontal pad is
# generous. Vertically no fixed number works: at 12pt the sentence under
# a derivation was dragged into the picture ("Thus (Equation 2.36)" on
# the Griffiths scan), at 2pt a tall brace lost its bottom 8pt (2310.19788
# p. 2, where the next line began 15pt below the box). So the vertical
# pad reaches as far as PAD_Y_MAX unless something the layout model
# placed stands in the same column within reach, and then it stops
# PAD_CLEAR short of that item. PAD_Y is the floor, used when the
# neighbour already touches or overlaps the box.
PAD_X = 12.0
PAD_Y = 2.0
PAD_Y_MAX = 12.0
PAD_CLEAR = 1.0
# A "formula" covering most of the page is a layout mistake, not an
# equation; cropping it would silently replace the page's prose with a
# picture of itself.
MAX_PAGE_SHARE = 0.8


class Region:
    """One formula item: where it is, and whether it can be cropped."""

    def __init__(self, page, box):
        self.page = page
        self.box = box  # (left, bottom, right, top), PDF points, bottom-left

    def __repr__(self):  # pragma: no cover - debugging only
        return f"Region(page={self.page}, box={self.box})"


def mark(document):
    """Give every undecoded formula a marker as its text; return its regions.

    Region `n` belongs to the formula whose text is now `MARKER` with
    index `n`. Coordinates are as docling reports them: relative to the
    page as it is rendered (the CropBox, after any /Rotate), origin at
    the bottom left -- the same frame pypdfium2 renders in.
    """
    regions = []
    for item, _level in document.iterate_items():
        if "formula" not in str(getattr(item, "label", "")).lower():
            continue
        if getattr(item, "text", ""):
            # docling read this one; leave its $$...$$ alone. Truthiness,
            # not content: the serializer tests `if text:` too.
            continue
        item.text = MARKER.format(index=len(regions))
        prov = list(getattr(item, "prov", None) or [])
        if not prov:
            regions.append(Region(None, None))
            continue
        first = prov[0]
        box = first.bbox
        regions.append(
            Region(
                first.page_no, (float(box.l), float(box.b), float(box.r), float(box.t))
            )
        )
    return regions


def neighbours(document):
    """`{page: [box, ...]}` of every positioned item, for the vertical pad."""
    boxes = {}
    for item, _level in document.iterate_items():
        for prov in getattr(item, "prov", None) or []:
            box = prov.bbox
            boxes.setdefault(prov.page_no, []).append(
                (float(box.l), float(box.b), float(box.r), float(box.t))
            )
    return boxes


def _vertical_pads(box, others, floor, ceiling, pad_x):
    """`(below, above)`: up to `ceiling`, stopping `PAD_CLEAR` short of an
    item in the same column -- the column being the crop's own footprint,
    `pad_x` wider than the box on each side. An item beside that footprint
    does not count. An item that already overlaps the box's edge (a
    neighbour the layout model drew over it) leaves `floor` on that side;
    an item merely close leaves whatever clearance is left, floor or not.
    Members of a merged group lie inside their union and constrain
    nothing."""
    left, bottom, right, top = box
    left, right = left - pad_x, right + pad_x
    below = above = ceiling
    for o_left, o_bottom, o_right, o_top in others:
        if o_right <= left or o_left >= right:
            continue
        if o_top <= bottom:
            below = min(below, max(0.0, bottom - o_top - PAD_CLEAR))
        elif o_bottom >= top:
            above = min(above, max(0.0, o_bottom - top - PAD_CLEAR))
        else:
            if o_bottom < bottom:
                below = min(below, floor)
            if o_top > top:
                above = min(above, floor)
    return below, above


def _overlap(first, second):
    l1, b1, r1, t1 = first
    l2, b2, r2, t2 = second
    return l1 < r2 and l2 < r1 and b1 < t2 and b2 < t1


def _union(first, second):
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[2], second[2]),
        max(first[3], second[3]),
    )


def merge(regions):
    """Overlapping regions on one page become a single crop.

    A scan makes the layout model draw two boxes over one equation --
    seen on the fixture, where neither box contains the other. Two crops
    would show the reader the same equation twice, so they are unioned
    and the extra markers are dropped rather than filled.
    """
    groups = []  # each: [page, box, [region index, ...]]
    for index, region in enumerate(regions):
        if region.page is None:
            continue
        for group in groups:
            if group[0] == region.page and _overlap(group[1], region.box):
                group[1] = _union(group[1], region.box)
                group[2].append(index)
                break
        else:
            groups.append([region.page, region.box, [index]])
    # A union can bring two previously separate groups into contact.
    changed = True
    while changed:
        changed = False
        for i in range(len(groups)):
            for j in range(len(groups) - 1, i, -1):
                if groups[i][0] == groups[j][0] and _overlap(
                    groups[i][1], groups[j][1]
                ):
                    groups[i][1] = _union(groups[i][1], groups[j][1])
                    groups[i][2].extend(groups[j][2])
                    del groups[j]
                    changed = True
    for group in groups:
        group[2].sort()
    groups.sort(key=lambda group: group[2][0])
    return groups


def _crop(page, box, pad_x, pads):
    """The render `crop` margins for `box`, clamped to the rendered page.

    `pads` is `(below, above)` in points.

    The frame is the page as rendered -- `get_size()` is the CropBox
    after /Rotate -- because that is the frame docling reports in
    (measured: an offset CropBox shifts every docling coordinate by its
    origin, and a rotated page is reported at its rotated size) and the
    frame pypdfium2 takes its crop margins in. The MediaBox and the
    CropBox's own origin play no part.
    """
    width, height = (float(value) for value in page.get_size())
    below, above = pads
    l = max(0.0, box[0] - pad_x)
    b = max(0.0, box[1] - below)
    r = min(width, box[2] + pad_x)
    t = min(height, box[3] + above)
    if r <= l or t <= b:
        return None, 0.0
    share = ((r - l) * (t - b)) / max(width * height, 1e-9)
    return (l, b, width - r, height - t), share


def rasterize(
    pdf_path,
    groups,
    out_dir,
    *,
    neighbours=None,
    scale=SCALE,
    pad_x=PAD_X,
    pad_y=PAD_Y,
    pad_y_max=PAD_Y_MAX,
):
    """Write one PNG per group; return {group index: file name} and warnings.

    With `neighbours` (from `neighbours(document)`) the vertical pad is
    chosen per equation; without, it is `pad_y` both ways.
    """
    from pathlib import Path

    from .pdf_common import _pdfium

    pdfium, _raw = _pdfium()
    images, warnings = {}, []
    directory = Path(out_dir) / IMAGE_DIR
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for index, (page_number, box, _members) in enumerate(groups):
            page = document[page_number - 1]
            if neighbours is None:
                pads = (pad_y, pad_y)
            else:
                pads = _vertical_pads(
                    box, neighbours.get(page_number, ()), pad_y, pad_y_max, pad_x
                )
            margins, share = _crop(page, box, pad_x, pads)
            if margins is None:
                warnings.append(FORMULA_UNPLACEABLE.format(page=page_number))
                continue
            if share > MAX_PAGE_SHARE:
                warnings.append(
                    FORMULA_REGION_OVERSIZE.format(
                        page=page_number, share=round(share * 100)
                    )
                )
                continue
            bitmap = page.render(scale=scale, crop=margins, rotation=0)
            directory.mkdir(parents=True, exist_ok=True)
            name = f"formula_p{page_number:04d}_{index:03d}.png"
            bitmap.to_pil().convert("RGB").save(directory / name)
            images[index] = name
    finally:
        document.close()
    return images, warnings


def replace(markdown, regions, groups, images):
    """Each marker becomes its own group's image, or the placeholder.

    Returns the Markdown, how many images were placed, and the indices of
    groups whose image had nowhere to go because none of the group's
    markers appeared in the export. The image goes to the group's first
    member that *was* exported -- a merged extra whose partner the
    serializer dropped still shows the equation once -- and the group's
    other markers are removed. A marker whose group produced no image
    becomes the placeholder, so a region that could not be cropped still
    tells the reader that an equation was there.
    """
    present = {int(index) for index in _MARKER_RE.findall(markdown)}
    target, placed, unplaced = {}, 0, []
    for index, (_page, _box, members) in enumerate(groups):
        name = images.get(index)
        if name is None:
            continue
        exported = [member for member in members if member in present]
        if not exported:
            unplaced.append(members[0])
            continue
        # No alt text on purpose: Pandoc turns an image with alt text that
        # stands alone in a paragraph into a figure with the alt as its
        # caption, and every equation would carry one.
        target[exported[0]] = f"![]({IMAGE_DIR}/{name})"
        for extra in exported[1:]:
            target[extra] = ""
        placed += 1

    def swap(match):
        return target.get(int(match.group(1)), PLACEHOLDER)

    return _MARKER_RE.sub(swap, markdown), placed, unplaced


def apply(
    markdown,
    regions,
    pdf_path,
    out_dir,
    *,
    neighbours=None,
    scale=SCALE,
    pad_x=PAD_X,
    pad_y=PAD_Y,
):
    """Rasterize every marked formula. Returns (markdown, placed, warnings).

    `placed` counts pictures that reached the Markdown, not files written.
    """
    if not regions:
        return markdown, 0, []
    groups = merge(regions)
    images, warnings = rasterize(
        pdf_path,
        groups,
        out_dir,
        neighbours=neighbours,
        scale=scale,
        pad_x=pad_x,
        pad_y=pad_y,
    )
    for index, region in enumerate(regions):
        if region.page is None:
            warnings.append(FORMULA_NO_POSITION.format(number=index + 1))
    markdown, placed, unplaced = replace(markdown, regions, groups, images)
    for index in unplaced:
        warnings.append(
            FORMULA_NOT_EXPORTED.format(number=index + 1, page=regions[index].page)
        )
    return markdown, placed, warnings
