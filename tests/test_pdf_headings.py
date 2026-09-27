"""Heading levels read from the page.

PIN (lead 260922, docs/260922-feat-PDF_HEADING_LEVELS.md): docling writes
every heading at `##`, so the EPUB contents came out flat -- 42.6% of
levels exact on twenty arXiv papers. Numbering first (a bare integer only
with a neighbour), then a numbered heading's level for any other heading
in the same typographic style, then the largest style as the title,
measured 95.9% exact, 19 of 20 papers entirely right. The style rank is
opendataloader-pdf's (Apache-2.0), written afresh; its heading detector
is not used. docling's own heading model (off by default) measured 24.6%
on the same corpus and puts the title level with its sections on 15 of
the 20 papers, so it is not switched on.

PIN (lead 260923, docs/260923-feat-PDF_ROLE_DECISIONS.md): when the
document carries a title item (docling's own, or one the region-role pass
made from a heading), the largest-style rule does not fire; unnumbered
headings with no numbered sibling are level 2. Measured on arxiv_2010_03667
and web_en_chrome_1: once the title left the section headers, the section
style became the largest left and every section stood at `#` beside it.
"""

import pytest

from book_maker.pipeline import pdf_headings
from pipeline_helpers import write_pdf


@pytest.mark.parametrize(
    "text,level",
    [
        ("1 Introduction", None),  # bare: see the neighbour test below
        ("1. Introduction", 2),
        ("2.3 Method", 3),
        ("2.3.1 Detail", 4),
        ("1.2.3.4.5.6.7 deep", 6),
        ("II. RELATED WORK", 2),
        ("A. Proofs", 3),
        ("3) Third", 4),
        ("Abstract", None),
        ("2024 was a year", None),  # a bare number needs a neighbour
        ("1", None),  # a number alone is not a heading with a number
    ],
)
def test_numbering_states_the_level(text, level):
    assert pdf_headings.numbering_level(text) == level


def test_a_bare_number_counts_with_a_neighbour_in_its_style_and_not_alone():
    assert pdf_headings.numbering_level("1 Introduction") is None
    assert pdf_headings.numbering_level("1 Introduction", bare=True) == 2
    big, section, sub = (17.0, False), (12.0, True), (10.0, True)
    vouched = pdf_headings._vouched
    # A chapter run starting anywhere: 7 vouches for 8 and 8 for 7.
    assert vouched([("7 Results", section), ("8 Discussion", section)]) == {0, 1}
    # A dotted heading under it vouches for a bare one; a `1.` marker too.
    assert vouched([("3 Results", section), ("3.1 Setup", sub)]) == {0}
    assert vouched([("1. Intro", section), ("2 Method", section)]) == {1}
    # A scan: no style at all, the run still reads.
    assert vouched([("1 Intro", None), ("2 Method", None)]) == {0, 1}
    # A number next in line but set in another style is not a peer: the
    # title `2 Fast Algorithms` above `1 Introduction`, or a year-titled
    # paper above a `2025 outlook` section.
    assert vouched([("2 Fast Algorithms", big), ("1 Introduction", section)]) == set()
    assert vouched([("2024 in review", big), ("2025 outlook", section)]) == set()
    # A year in a title, with no neighbour, is a title that starts with a number.
    assert vouched([("2024 was a year", big), ("1.1 Sub", sub)]) == set()
    # A dotted heading vouches for its own parent, the nearest `2` above
    # it, not for a title that happens to start with the same number.
    assert vouched(
        [
            ("2 Fast Algorithms", big),
            ("1 Introduction", section),
            ("2 Method", section),
            ("2.1 Setup", sub),
        ]
    ) == {1, 2}
    assert vouched([("2.1 Setup", sub), ("2 Later", section)]) == set()
    # A `2.` parent in between consumes the evidence just the same.
    assert (
        vouched(
            [
                ("2 Fast Algorithms", big),
                ("1. Introduction", section),
                ("2. Method", section),
                ("2.1 Setup", sub),
            ]
        )
        == set()
    )
    assert pdf_headings.levels(
        [("2024 was a year", big), ("Abstract", section), ("1.1 Sub", section)]
    ) == [1, 3, 3]
    assert pdf_headings.levels(
        [("2 Fast Algorithms", big), ("1 Introduction", section), ("1.1 Sub", sub)]
    ) == [1, 2, 3]
    assert pdf_headings.levels(
        [
            ("2 Fast Algorithms", big),
            ("1 Introduction", section),
            ("2 Method", section),
            ("2.1 Setup", sub),
        ]
    ) == [1, 2, 2, 3]


def test_numbering_first_then_the_largest_style_then_a_numbered_sibling_s_level():
    big, section, sub = (17.0, False), (12.0, True), (10.0, True)
    headings = [
        ("Attention Is All You Need", big),
        ("Abstract", section),
        ("1 Introduction", section),
        ("2 Background", section),
        ("3.1 Encoder and Decoder Stacks", sub),
        ("3.2.1 Scaled Dot-Product Attention", sub),  # same font as 3.1
        ("Acknowledgements", sub),
        ("References", section),
        ("Appendix", None),  # no glyphs under it: a scanned page
    ]
    assert pdf_headings.levels(headings) == [1, 2, 2, 2, 3, 4, 3, 2, 2]


def test_a_numbered_sibling_s_level_comes_before_the_title_rule():
    # docling took the title out as a title item, so the largest style
    # among the section headers is the section font: `Abstract` set in it
    # is a section, not a second title. Nothing unnumbered in a style of
    # its own is left, so no heading gets level 1.
    section, sub = (12.0, True), (10.0, True)
    headings = [
        ("Abstract", section),
        ("1 Introduction", section),
        ("2 Method", section),
        ("2.1 Data", sub),
        ("Acknowledgements", sub),
        ("References", section),
    ]
    assert pdf_headings.levels(headings) == [2, 2, 2, 3, 3, 2]


def test_a_document_with_no_style_at_all_still_levels_its_numbering():
    headings = [("Title", None), ("1 Intro", None), ("1.1 Sub", None), ("Refs", None)]
    assert pdf_headings.levels(headings) == [2, 2, 3, 2]


def test_the_style_is_read_under_the_box_in_docling_s_cropbox_frame(tmp_path):
    pytest.importorskip("pypdfium2")
    # The helper writes 18pt Helvetica at MediaBox (72, 700); with this
    # CropBox docling would report the line at (32, 640).
    pdf = write_pdf(
        tmp_path / "book.pdf", ["A heading line"], cropbox=(40, 60, 452, 750)
    )
    found = pdf_headings.styles(
        pdf, {0: (1, (20.0, 630.0, 300.0, 660.0)), 1: (1, (20.0, 100.0, 300.0, 130.0))}
    )
    assert found == {0: (18.0, False), 1: None}


@pytest.mark.parametrize(
    "rotation,box",
    [
        # The fixture sets 18pt text at user-space (72, 700) on a 612x792
        # page, so its glyph centres sit near y=706, x from 72 on. docling
        # reports a turned page in its displayed frame (measured 260922 on
        # a paper saved at each rotation: at 90 a heading at user (159,
        # 606)-(203, 617) came back as (606, 392)-(617, 436), at 270 as
        # (H - t, l)-(H - b, r)); these boxes are where the line is then.
        (0, (60.0, 690.0, 320.0, 720.0)),
        (90, (690.0, 380.0, 720.0, 545.0)),
        (180, (290.0, 70.0, 545.0, 100.0)),
        (270, (70.0, 60.0, 100.0, 330.0)),
    ],
)
def test_the_style_is_read_in_the_frame_of_the_page_as_displayed(
    tmp_path, rotation, box
):
    pdfium = pytest.importorskip("pypdfium2")
    source = write_pdf(tmp_path / "flat.pdf", ["A heading line"])
    document = pdfium.PdfDocument(str(source))
    document[0].set_rotation(rotation)
    pdf = tmp_path / "turned.pdf"
    document.save(str(pdf))
    document.close()
    unturned = (60.0, 690.0, 320.0, 720.0)
    found = pdf_headings.styles(pdf, {0: (1, box), 1: (1, unturned)})
    assert found[0] == (18.0, False)
    # On a turned page the unturned box lands on blank paper.
    assert found[1] == (None if rotation else (18.0, False))


class FakeBox:
    def __init__(self, l, b, r, t):
        self.l, self.b, self.r, self.t = l, b, r, t


class FakeProv:
    def __init__(self, page_no, box):
        self.page_no, self.bbox = page_no, FakeBox(*box)


class FakeItem:
    def __init__(self, label, text, box=None):
        self.label, self.text, self.level = label, text, 1
        self.prov = [FakeProv(1, box)] if box else []


class FakeDoc:
    def __init__(self, *items):
        self.items = items

    def iterate_items(self):
        return ((item, 0) for item in self.items)


def test_assign_sets_the_level_on_every_section_header(tmp_path):
    pytest.importorskip("pypdfium2")
    pdf = write_pdf(tmp_path / "book.pdf", ["A heading line"])
    title = FakeItem("section_header", "The Title", (60.0, 690.0, 320.0, 720.0))
    numbered = FakeItem("section_header", "2.1 Sub", (60.0, 100.0, 320.0, 120.0))
    plain = FakeItem("section_header", "Notes")  # no position at all
    body = FakeItem("text", "prose", (60.0, 690.0, 320.0, 720.0))
    assert pdf_headings.assign(FakeDoc(title, numbered, plain, body), pdf) == (3, 1)
    assert (title.level, numbered.level, plain.level, body.level) == (1, 3, 2, 1)


def test_promote_lifts_every_heading_one_level_and_leaves_a_title_item():
    markdown = (
        "# Title item\n\n## Top\n\nprose\n\n### 1 Intro\n\n#### 1.1 Sub\n| ## cell |\n"
    )
    assert pdf_headings.promote(markdown) == (
        "# Title item\n\n# Top\n\nprose\n\n## 1 Intro\n\n### 1.1 Sub\n| ## cell |\n"
    )


def test_promote_leaves_a_fenced_code_block_alone():
    markdown = (
        "## Top\n\n```python\n## a comment\n```\n\n### 1 Intro\n\n"
        "~~~\n## tilde fenced\n``` not a closer\n## still code\n~~~\n\n### 2 Next\n"
        "````\n```\n## inside a longer fence\n````\n\n### 3 Last\n"
    )
    assert pdf_headings.promote(markdown) == (
        "# Top\n\n```python\n## a comment\n```\n\n## 1 Intro\n\n"
        "~~~\n## tilde fenced\n``` not a closer\n## still code\n~~~\n\n## 2 Next\n"
        "````\n```\n## inside a longer fence\n````\n\n## 3 Last\n"
    )
    # A fence line with an info string inside a block is code, not a closer.
    nested = "## Top\n\n```\n```python\n## preserve this\n```\n\n### 1 Intro\n"
    assert pdf_headings.promote(nested) == (
        "# Top\n\n```\n```python\n## preserve this\n```\n\n## 1 Intro\n"
    )


def test_promote_leaves_the_serializer_s_own_code_fence_alone():
    """PIN: docling-core fences a code item; a docling upgrade that changes
    how code is serialized must fail here, not silently corrupt code."""
    DoclingDocument = pytest.importorskip("docling_core.types.doc").DoclingDocument
    document = DoclingDocument(name="t")
    document.add_heading("Top", level=1)
    document.add_code("## not a heading\n```python\n## preserve this")
    document.add_heading("1 Intro", level=2)
    assert pdf_headings.promote(document.export_to_markdown()) == (
        "# Top\n\n```\n## not a heading\n```python\n## preserve this\n```\n\n## 1 Intro"
    )


def test_with_a_title_item_the_largest_style_is_not_a_second_title():
    section = (10.0, True)
    headings = [
        ("ABSTRACT", section),
        ("INTRODUCTION", section),
        ("Aside", (9.0, True)),
    ]
    # today's rule without a title item: the largest style is the title
    assert pdf_headings.levels(headings) == [1, 1, 2]
    # with one, every unnumbered heading is a section
    assert pdf_headings.levels(headings, title_present=True) == [2, 2, 2]
    # numbering and the numbered sibling still decide
    numbered = [("1 Introduction", section), ("2 Method", section), ("Notes", section)]
    assert pdf_headings.levels(numbered, title_present=True) == [2, 2, 2]
    dotted = [("2.1 Setup", section), ("Remarks", section)]
    assert pdf_headings.levels(dotted, title_present=True) == [3, 3]


def acm_paper(with_title):
    docling = pytest.importorskip("docling_core.types.doc")
    d = docling
    document = d.DoclingDocument(name="acm")
    document.add_page(page_no=1, size=d.Size(width=612.0, height=792.0))

    def prov(top):
        return d.ProvenanceItem(
            page_no=1,
            bbox=d.BoundingBox(
                l=60, t=top, r=320, b=top - 30, coord_origin=d.CoordOrigin.BOTTOMLEFT
            ),
            charspan=(0, 1),
        )

    if with_title:
        document.add_title(text="Rescribe", prov=prov(760))
    for text, top in (("ABSTRACT", 720), ("INTRODUCTION", 500), ("BACKGROUND", 300)):
        document.add_heading(text=text, prov=prov(top))
        document.add_text(label=d.DocItemLabel.TEXT, text="Prose.", prov=prov(top - 40))
    return document


@pytest.mark.parametrize("with_title", [True, False])
def test_an_acm_paper_s_sections_sit_under_its_title(tmp_path, monkeypatch, with_title):
    """ACM style: unnumbered ABSTRACT/INTRODUCTION in one section style."""
    pytest.importorskip("pypdfium2")
    pdf = write_pdf(tmp_path / "book.pdf", ["A heading line"])
    # every section header is set in the same (bold, 10pt) section style
    monkeypatch.setattr(
        pdf_headings, "styles", lambda path, boxes: {i: (10.0, True) for i in boxes}
    )
    document = acm_paper(with_title)
    pdf_headings.assign(document, pdf)
    markdown = pdf_headings.promote(document.export_to_markdown())
    tops = [line for line in markdown.splitlines() if line.startswith("# ")]
    if with_title:
        assert tops == ["# Rescribe"]
        assert "## ABSTRACT" in markdown.splitlines()
        assert "## INTRODUCTION" in markdown.splitlines()
    else:
        # unchanged: without a title item the largest style is level 1
        assert tops == ["# ABSTRACT", "# INTRODUCTION", "# BACKGROUND"]
