"""Display formulas kept as pictures.

PIN (owner 260922, docs/260922-feat-PDF_FORMULA_IMAGES.md): docling finds a
display equation and does not read it, so the Markdown export carries
`<!-- formula-not-decoded -->` and the mathematics is gone from the book.
The equation is preserved by cropping its region out of the PDF, not by
docling's own formula model: `do_formula_enrichment` was measured in
docs/260921-eval-DOCLING_VS_OPENDATALOADER.md and hallucinated on hard
equations, swallowed prose into `$$` blocks and cost 29x the default. Do
not "improve" this into a LaTeX decoder without re-running that eval.
"""

from pathlib import Path

import pytest

from book_maker.pipeline import pdf_formula
from book_maker.pipeline.messages import (
    FORMULA_NO_POSITION,
    FORMULA_NOT_EXPORTED,
    FORMULA_REGION_OVERSIZE,
)
from pipeline_helpers import write_pdf

PLACEHOLDER = pdf_formula.PLACEHOLDER


def marker(index):
    return "$$" + pdf_formula.MARKER.format(index=index) + "$$"


class FakeBox:
    def __init__(self, l, b, r, t):
        self.l, self.b, self.r, self.t = l, b, r, t


class FakeProv:
    def __init__(self, page_no, box):
        self.page_no = page_no
        self.bbox = FakeBox(*box)


class FakeItem:
    def __init__(
        self, label="formula", text="", orig=None, page=1, box=(10, 10, 90, 30)
    ):
        self.label = label
        self.text = text
        self.orig = orig
        self.prov = [FakeProv(page, box)] if box is not None else []


class FakeDoc:
    def __init__(self, *items):
        self.items = items

    def iterate_items(self):
        return ((item, 0) for item in self.items)


# --------------------------------------------------------------------------
# mark
# --------------------------------------------------------------------------
def test_every_undecoded_formula_is_marked_in_reading_order():
    doc = FakeDoc(
        FakeItem(page=1, box=(0, 0, 10, 10)),
        FakeItem(label="text", box=(0, 0, 10, 10)),
        FakeItem(page=2, box=(5, 5, 20, 20)),
    )
    regions = pdf_formula.mark(doc)
    assert [r.page for r in regions] == [1, 2]
    assert regions[1].box == (5.0, 5.0, 20.0, 20.0)


def test_an_undecoded_formula_is_given_its_region_s_marker_as_text():
    """Placement is by identity: the marker written into the item's text is
    what the serializer emits where the equation stands, so the picture
    for region n can only ever land at item n. (Codex 260922 found that
    counting placeholders could not tell a reordering from alignment --
    docling-core walks rich table cells in grid order -- so the ordinal
    scheme it replaced is not to be brought back.) Checked against the
    real serializer in the last test of this module."""
    first = FakeItem(orig=None)
    second = FakeItem(orig="E=mc^2")
    pdf_formula.mark(FakeDoc(first, second))
    assert first.text == pdf_formula.MARKER.format(index=0)
    assert second.text == pdf_formula.MARKER.format(index=1)
    assert second.orig == "E=mc^2", "orig is not touched"


def test_a_formula_docling_did_read_is_not_touched():
    decoded = FakeItem(text="E = mc^2")
    assert pdf_formula.mark(FakeDoc(decoded)) == []


def test_a_formula_with_no_position_is_marked_but_has_no_box():
    regions = pdf_formula.mark(FakeDoc(FakeItem(box=None)))
    assert len(regions) == 1 and regions[0].page is None


# --------------------------------------------------------------------------
# merge
# --------------------------------------------------------------------------
def test_overlapping_regions_on_one_page_become_a_single_crop():
    """Seen on the fixture: a scan makes the layout model draw two boxes
    over one equation, neither containing the other. Two crops would show
    the reader the same equation twice."""
    regions = pdf_formula.mark(
        FakeDoc(
            FakeItem(page=51, box=(121.3, 149.1, 374.0, 171.7)),
            FakeItem(page=51, box=(174.3, 143.4, 298.7, 176.1)),
        )
    )
    groups = pdf_formula.merge(regions)
    assert len(groups) == 1
    assert groups[0][1] == (121.3, 143.4, 374.0, 176.1)
    assert groups[0][2] == [0, 1]


def test_regions_on_different_pages_never_merge():
    regions = pdf_formula.mark(
        FakeDoc(
            FakeItem(page=1, box=(10, 10, 90, 30)),
            FakeItem(page=2, box=(10, 10, 90, 30)),
        )
    )
    assert len(pdf_formula.merge(regions)) == 2


def test_two_groups_brought_into_contact_by_a_union_are_merged():
    regions = pdf_formula.mark(
        FakeDoc(
            FakeItem(page=1, box=(0, 0, 10, 10)),
            FakeItem(page=1, box=(30, 0, 40, 10)),
            FakeItem(page=1, box=(5, 0, 35, 10)),
        )
    )
    groups = pdf_formula.merge(regions)
    assert len(groups) == 1 and groups[0][2] == [0, 1, 2]


def test_groups_keep_the_reading_order_of_their_first_member():
    regions = pdf_formula.mark(
        FakeDoc(
            FakeItem(page=1, box=(200, 200, 260, 220)),
            FakeItem(page=1, box=(0, 0, 60, 20)),
        )
    )
    assert [g[2][0] for g in pdf_formula.merge(regions)] == [0, 1]


# --------------------------------------------------------------------------
# replace
# --------------------------------------------------------------------------
def test_each_marker_becomes_its_own_group_s_image_wherever_it_appears():
    regions = pdf_formula.mark(
        FakeDoc(
            FakeItem(page=1, box=(0, 0, 10, 10)), FakeItem(page=2, box=(0, 0, 10, 10))
        )
    )
    groups = pdf_formula.merge(regions)
    # The serializer wrote the second formula first -- a table walked in
    # grid order, say. Each picture still lands at its own marker.
    markdown = f"a\n\n{marker(1)}\n\nb\n\n{marker(0)}\n"
    out, placed, unplaced = pdf_formula.replace(
        markdown, regions, groups, {0: "one.png", 1: "two.png"}
    )
    assert (placed, unplaced) == (2, [])
    assert out == "a\n\n![](images/two.png)\n\nb\n\n![](images/one.png)\n"


def test_any_marker_index_is_matched_and_the_wrapping_may_be_absent():
    """Four digits is the marker's minimum width, not its range; and a
    nested table cell flattens a formula's text without `$` (Codex 260922
    probed the installed serializer)."""
    for index in (0, 9999, 10000, 123456):
        assert int(pdf_formula._MARKER_RE.fullmatch(marker(index)).group(1)) == index
        bare = pdf_formula.MARKER.format(index=index)
        assert int(pdf_formula._MARKER_RE.fullmatch(bare).group(1)) == index


def test_an_inline_marker_is_replaced_too():
    regions = pdf_formula.mark(FakeDoc(FakeItem(page=1, box=(0, 0, 10, 10))))
    groups = pdf_formula.merge(regions)
    inline = "$" + pdf_formula.MARKER.format(index=0) + "$"
    out, placed, _ = pdf_formula.replace(
        f"| {inline} |\n", regions, groups, {0: "one.png"}
    )
    assert out == "| ![](images/one.png) |\n" and placed == 1


def test_a_merged_group_fills_the_first_placeholder_and_drops_the_rest():
    regions = pdf_formula.mark(
        FakeDoc(
            FakeItem(page=1, box=(0, 0, 20, 10)),
            FakeItem(page=1, box=(10, 0, 30, 10)),
        )
    )
    groups = pdf_formula.merge(regions)
    out, placed, unplaced = pdf_formula.replace(
        f"{marker(0)}\n\n{marker(1)}\n", regions, groups, {0: "one.png"}
    )
    assert (placed, unplaced) == (1, [])
    assert out.count("![](images/one.png)") == 1
    assert PLACEHOLDER not in out and "bbm-formula" not in out


def test_a_merged_group_whose_first_marker_was_not_exported_still_shows_once():
    """Codex 260922: anchoring the picture on the group's first member
    deleted the equation when the serializer dropped that member and kept
    the other. The picture goes to whichever member was exported."""
    regions = pdf_formula.mark(
        FakeDoc(
            FakeItem(page=1, box=(0, 0, 20, 10)),
            FakeItem(page=1, box=(10, 0, 30, 10)),
        )
    )
    groups = pdf_formula.merge(regions)
    out, placed, unplaced = pdf_formula.replace(
        f"x\n\n{marker(1)}\n", regions, groups, {0: "one.png"}
    )
    assert out == "x\n\n![](images/one.png)\n" and (placed, unplaced) == (1, [])


def test_a_group_with_no_image_keeps_its_placeholder():
    """A region that could not be cropped still tells the reader that an
    equation was there; it is never silently deleted."""
    regions = pdf_formula.mark(FakeDoc(FakeItem(page=1, box=(0, 0, 10, 10))))
    groups = pdf_formula.merge(regions)
    out, placed, _ = pdf_formula.replace(f"{marker(0)}\n", regions, groups, {})
    assert out.strip() == PLACEHOLDER and placed == 0


# --------------------------------------------------------------------------
# apply: the guards
# --------------------------------------------------------------------------
def test_a_marker_the_serializer_never_wrote_is_reported_not_guessed(tmp_path):
    """A formula the export left out (furniture, a dropped group) has no
    place to put its picture. It is named, in reading order and by page,
    rather than placed anywhere else."""
    pdfium_or_skip()
    pdf = write_pdf(tmp_path / "book.pdf", ["prose"])
    regions = pdf_formula.mark(
        FakeDoc(
            FakeItem(page=1, box=(10, 10, 60, 30)),
            FakeItem(page=1, box=(10, 50, 60, 70)),
        )
    )
    markdown = f"only the first\n\n{marker(0)}\n"
    out, placed, warnings = pdf_formula.apply(markdown, regions, pdf, tmp_path)
    assert out.count("![](images/") == 1
    assert placed == 1, "the count is pictures placed, not files written"
    assert warnings == [FORMULA_NOT_EXPORTED.format(number=2, page=1)]


def test_a_formula_with_no_position_keeps_its_placeholder_and_is_reported(tmp_path):
    pdfium_or_skip()
    pdf = write_pdf(tmp_path / "book.pdf", ["prose"])
    regions = pdf_formula.mark(FakeDoc(FakeItem(box=None)))
    out, count, warnings = pdf_formula.apply(f"{marker(0)}\n", regions, pdf, tmp_path)
    assert out.strip() == PLACEHOLDER and count == 0
    assert warnings == [FORMULA_NO_POSITION.format(number=1)]


def test_a_document_with_no_formulas_does_nothing(tmp_path):
    out, count, warnings = pdf_formula.apply(
        "plain\n", [], tmp_path / "x.pdf", tmp_path
    )
    assert (out, count, warnings) == ("plain\n", 0, [])


# --------------------------------------------------------------------------
# The real thing: pdfium, a real PDF, real pixels
# --------------------------------------------------------------------------
def pdfium_or_skip():
    try:
        import pypdfium2
    except ImportError:
        pytest.skip("pypdfium2 is not installed")
    if not hasattr(pypdfium2, "PdfDocument"):
        pytest.skip("pypdfium2 is not installed")


def test_a_region_is_cropped_out_of_a_real_pdf(tmp_path):
    pdfium_or_skip()
    pytest.importorskip("PIL")
    from PIL import Image

    pdf = write_pdf(tmp_path / "book.pdf", ["A typed line of prose on the page."])
    regions = pdf_formula.mark(FakeDoc(FakeItem(page=1, box=(50, 700, 300, 730))))
    groups = pdf_formula.merge(regions)
    images, warnings = pdf_formula.rasterize(pdf, groups, tmp_path)
    assert warnings == []
    written = tmp_path / pdf_formula.IMAGE_DIR / images[0]
    assert written.is_file()
    with Image.open(written) as picture:
        # (250 + 2*PAD_X) x (30 + 2*PAD_Y) points at SCALE, within rounding.
        assert (
            abs(picture.width - (250 + 2 * pdf_formula.PAD_X) * pdf_formula.SCALE) <= 2
        )
        assert (
            abs(picture.height - (30 + 2 * pdf_formula.PAD_Y) * pdf_formula.SCALE) <= 2
        )


def _expected_window(full, box, height, scale):
    # Pixel rows count from the top: top = height - box top.
    return full.crop(
        (
            round(box[0] * scale),
            round((height - box[3]) * scale),
            round(box[2] * scale),
            round((height - box[1]) * scale),
        )
    )


def _same_pixels(crop, expected):
    from PIL import ImageChops

    assert crop.size == expected.size
    assert expected.getextrema() != ((255, 255), (255, 255), (255, 255)), "blank"
    return ImageChops.difference(crop, expected).getbbox() is None


def test_the_vertical_pad_reaches_out_but_stops_short_of_a_neighbour():
    """PIN (lead 260922, docs/260922-feat-PDF_FORMULA_IMAGES.md, measured on
    the Opus corpus run): a fixed vertical pad cannot be right -- 12pt
    dragged the next sentence into a Griffiths crop, 2pt clipped a brace on
    2310.19788 p. 2 whose next line was 15pt away. The pad reaches PAD_Y_MAX
    unless an item in the crop's column is nearer, then stops PAD_CLEAR
    short of it -- below the floor if that is what the clearance leaves
    (Codex 260922: the floor must not cross a neighbour 1pt away). The
    column is the crop's footprint, PAD_X wider than the box (Codex again:
    prose beginning 5pt beyond the box's edge was inside the crop and
    ignored). An item overlapping the box leaves the floor on that side."""
    box = (100.0, 500.0, 300.0, 530.0)
    floor, ceiling, pad_x = pdf_formula.PAD_Y, pdf_formula.PAD_Y_MAX, pdf_formula.PAD_X
    clear = pdf_formula.PAD_CLEAR

    def pads(others):
        return pdf_formula._vertical_pads(box, others, floor, ceiling, pad_x)

    # nothing near (the box itself is in the list): the ceiling both ways
    assert pads([box]) == (ceiling, ceiling)
    # a line 20pt above and one 5pt below, both in the column
    assert pads([(90.0, 550.0, 310.0, 562.0), (90.0, 480.0, 310.0, 495.0)]) == (
        5.0 - clear,
        ceiling,
    )
    # a line 1pt below: the clearance wins over the floor
    assert pads([(90.0, 480.0, 310.0, 499.0)]) == (0.0, ceiling)
    # 5pt below, beginning 5pt beyond the box's right edge: inside the
    # crop's footprint, so it counts
    assert pads([(305.0, 480.0, 500.0, 495.0)]) == (5.0 - clear, ceiling)
    # 5pt below but beyond the footprint (the other column): ignored
    assert pads([(320.0, 480.0, 500.0, 495.0)]) == (ceiling, ceiling)
    # a neighbour the layout model drew over the box's bottom edge: the floor
    assert pads([(90.0, 495.0, 310.0, 505.0)]) == (floor, ceiling)


def test_neighbours_are_every_positioned_item_by_page():
    doc = FakeDoc(
        FakeItem(label="text", page=1, box=(0, 0, 10, 10)),
        FakeItem(page=2, box=(5, 5, 20, 20)),
        FakeItem(label="text", box=None),
    )
    assert pdf_formula.neighbours(doc) == {
        1: [(0.0, 0.0, 10.0, 10.0)],
        2: [(5.0, 5.0, 20.0, 20.0)],
    }


def test_rasterize_uses_the_neighbour_aware_pads(tmp_path):
    """Pixel-equal to the window the pads describe, with the text inside it
    and the pads asymmetric, so swapping above and below would fail."""
    pdfium_or_skip()
    pytest.importorskip("PIL")
    import pypdfium2 as pdfium
    from PIL import Image

    # The helper writes its text at (72, 700), 18pt, on a 612x792 page.
    pdf = write_pdf(tmp_path / "book.pdf", ["A typed line of prose on the page."])
    box = (60.0, 690.0, 320.0, 720.0)
    regions = pdf_formula.mark(FakeDoc(FakeItem(page=1, box=box)))
    near = {1: [box, (60.0, 675.0, 320.0, 685.0)]}  # 5pt below, same column
    images, warnings = pdf_formula.rasterize(
        pdf, pdf_formula.merge(regions), tmp_path, neighbours=near, scale=2.0
    )
    assert warnings == []
    below, above = 5.0 - pdf_formula.PAD_CLEAR, pdf_formula.PAD_Y_MAX
    window = (
        box[0] - pdf_formula.PAD_X,
        box[1] - below,
        box[2] + pdf_formula.PAD_X,
        box[3] + above,
    )
    page = pdfium.PdfDocument(str(pdf))[0]
    full = page.render(scale=2.0).to_pil().convert("RGB")
    crop = Image.open(tmp_path / pdf_formula.IMAGE_DIR / images[0]).convert("RGB")
    assert _same_pixels(crop, _expected_window(full, window, page.get_size()[1], 2.0))


def test_the_crop_is_taken_in_the_frame_docling_reports_in(tmp_path):
    """PIN (lead 260922, measured on the Griffiths scan with a CropBox set to
    (40, 60, 452, 600)): docling reports every coordinate relative to the
    CropBox origin, at the page's rendered size. A crop taken against the
    MediaBox would be shifted by that origin -- Codex 260922 predicted it,
    the measurement confirmed it. Asserted on pixels, with the fixture's
    text inside the window: the crop must equal the same window cut from
    the full rendered page, and that window must not be blank (the first
    version of this test compared two white images and pinned nothing --
    Codex again)."""
    pdfium_or_skip()
    pytest.importorskip("PIL")
    import pypdfium2 as pdfium
    from PIL import Image

    # The helper writes its text at (72, 700) on a 612x792 MediaBox, 18pt.
    pdf = write_pdf(
        tmp_path / "book.pdf", ["A typed line of prose."], cropbox=(40, 60, 452, 750)
    )
    page = pdfium.PdfDocument(str(pdf))[0]
    width, height = page.get_size()
    assert (width, height) == (412.0, 690.0), "the fixture's CropBox took"
    # docling's frame: relative to the CropBox, bottom-left origin -- so the
    # text line at MediaBox (72, 700) sits at (32, 640) here.
    box = (20.0, 630.0, 300.0, 660.0)
    regions = pdf_formula.mark(FakeDoc(FakeItem(page=1, box=box)))
    images, warnings = pdf_formula.rasterize(
        pdf, pdf_formula.merge(regions), tmp_path, scale=2.0, pad_x=0.0, pad_y=0.0
    )
    assert warnings == []
    crop = Image.open(tmp_path / pdf_formula.IMAGE_DIR / images[0]).convert("RGB")
    full = page.render(scale=2.0).to_pil().convert("RGB")
    assert _same_pixels(crop, _expected_window(full, box, height, 2.0))


def test_the_crop_follows_the_page_s_own_rotation(tmp_path):
    """On a /Rotate 90 page docling reports the rotated size (measured on
    the Griffiths scan: 624.8x452 for a 452x624.8 page) and pdfium renders
    rotated, so the crop frame is the rendered page in both. Whether docling
    finds a formula on such a page at all is its business -- on the two
    fixtures it found none -- but if it does, the crop is right."""
    pdfium_or_skip()
    pytest.importorskip("PIL")
    import pypdfium2 as pdfium
    from PIL import Image

    plain = write_pdf(tmp_path / "plain.pdf", ["A typed line of prose."])
    document = pdfium.PdfDocument(str(plain))
    document[0].set_rotation(90)
    pdf = tmp_path / "rotated.pdf"
    document.save(str(pdf))
    document.close()
    page = pdfium.PdfDocument(str(pdf))[0]
    width, height = page.get_size()
    assert (width, height) == (792.0, 612.0), "rendered size is rotated"
    full = page.render(scale=2.0).to_pil().convert("RGB")
    # Find the text in the rotated render rather than reason about where
    # it went: the window is the ink's bounding box, padded.
    ink = full.convert("L").point(lambda v: 255 if v < 128 else 0).getbbox()
    left, top, right, bottom = (value / 2.0 for value in ink)
    box = (left - 5, height - bottom - 5, right + 5, height - top + 5)
    regions = pdf_formula.mark(FakeDoc(FakeItem(page=1, box=box)))
    images, warnings = pdf_formula.rasterize(
        pdf, pdf_formula.merge(regions), tmp_path, scale=2.0, pad_x=0.0, pad_y=0.0
    )
    assert warnings == []
    crop = Image.open(tmp_path / pdf_formula.IMAGE_DIR / images[0]).convert("RGB")
    assert _same_pixels(crop, _expected_window(full, box, height, 2.0))


def test_a_region_covering_the_page_is_refused_rather_than_cropped(tmp_path):
    """A box over most of the page is a layout mistake. Cropping it would
    replace the page's prose with a picture of itself."""
    pdfium_or_skip()
    pdf = write_pdf(tmp_path / "book.pdf", ["Prose."])
    regions = pdf_formula.mark(FakeDoc(FakeItem(page=1, box=(0, 0, 612, 792))))
    groups = pdf_formula.merge(regions)
    images, warnings = pdf_formula.rasterize(pdf, groups, tmp_path)
    assert images == {}
    assert warnings == [FORMULA_REGION_OVERSIZE.format(page=1, share=100)]


def test_the_serializer_still_behaves_the_way_mark_depends_on():
    """PIN: the three branches of docling-core's FormulaItem serializer.

    `mark` exists because the third one exports nothing at all. If a
    docling upgrade changes this, the placeholders stop lining up with the
    regions and equations would land in the wrong place -- so it fails
    here, loudly, rather than in somebody's book. Exercised on a real
    document, not read off the library's source: the serializer's shape
    can change without either string moving.
    """
    docling_core = pytest.importorskip("docling_core.types.doc")
    DoclingDocument = docling_core.DoclingDocument
    FORMULA = docling_core.DocItemLabel.FORMULA
    TEXT = docling_core.DocItemLabel.TEXT

    document = DoclingDocument(name="pin")
    document.add_text(label=TEXT, text="before")
    document.add_text(label=FORMULA, text="E = mc^2")
    document.add_text(label=FORMULA, text="", orig="raw")
    document.add_text(label=FORMULA, text="")
    document.add_text(label=TEXT, text="after")

    exported = document.export_to_markdown()
    assert "$$E = mc^2$$" in exported
    assert exported.count(PLACEHOLDER) == 1  # the `orig`-only one
    # The third formula left nothing: exactly the gap `mark` closes.
    assert exported.split("$$E = mc^2$$")[1].strip() == f"{PLACEHOLDER}\n\nafter"

    # And after `mark`, each undecoded formula exports as its own marker,
    # verbatim -- the serializer passes a formula's text through
    # unescaped -- so the picture can only land at its own item.
    regions = pdf_formula.mark(document)
    assert len(regions) == 2
    marked = document.export_to_markdown()
    assert PLACEHOLDER not in marked
    assert marked.index(marker(0)) < marked.index(marker(1))
    assert "$$E = mc^2$$" in marked
