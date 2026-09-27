"""`--ocr-replace-layer`: fresh OCR replaces an embedded text layer, as an
explicit setting.

PIN (owner ruling 260923, docs/260923-docs-OWNER_RULINGS_OCR_PROMPT_LAYER_WIKI.md
section 6, packet G): replacement is explicit and off by default; the choice
is extraction identity, so toggling it re-extracts; neither OCR nor an image
model implies it; and when replacement is asked for and the engine reads
nothing on a page that carried a layer, that page is empty and said to be,
never silently filled from the layer. Measured reason it is off by default:
docs/260923-eval-PDF_OCR_LUNA_VS_LOCAL_BASELINE.md findings 1 and 4 (a
full-page rerun was worse than the layer on all three scored pages and came
back empty on innerspace with a success status).

No model is loaded here. The conversion is the `convert=` seam; the text
layer is the real reader over PDFs written by `pipeline_helpers.write_pdf`
(pypdfium2), with visible text and with the invisible OCR-layer shape.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import PNG, pandoc_or_skip, write_pdf  # noqa: E402

from book_maker.pipeline import docling_parser, stages  # noqa: E402
from book_maker.pipeline.bundle import Bundle  # noqa: E402
from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.pipeline.pdf_common import (  # noqa: E402
    UNPLACED_MARKER,
    blank_pages,
    check_recognised_text,
)
from book_maker.pipeline.messages import (  # noqa: E402
    DEVICE_SELECTED,
    EXTRACTION_EMPTY,
    INVISIBLE_TEXT_LAYER,
    OCR_LANG_DEFAULT,
    OCR_REPLACE_ALL_EMPTY,
    OCR_REPLACE_EMPTY,
    OCR_REPLACE_EMPTY_MORE,
    OCR_REPLACE_NEEDS_OCR,
    OCR_REPLACING_LAYER,
    PAGE_MAP_UNPLACED,
    PDF_OPTIONS_INERT,
)
from book_maker.pipeline.pdf_settings import ExtractionSettings  # noqa: E402

BREAK = docling_parser.PAGE_BREAK
REPLACE = ExtractionSettings(ocr=True, ocr_mode="full_page")
KEEP = ExtractionSettings(ocr=True)


def _pdfium_or_skip():
    pypdfium2 = pytest.importorskip("pypdfium2")
    if not hasattr(pypdfium2, "PdfDocument"):
        pytest.skip("pypdfium2 is installed but unusable")


@pytest.fixture
def pandoc():
    return pandoc_or_skip()


@pytest.fixture(autouse=True)
def device(monkeypatch):
    """The resolved device, without docling (the one call that imports it)."""
    monkeypatch.setattr(
        docling_parser,
        "resolve_device",
        lambda requested: ("cpu", DEVICE_SELECTED.format(device="cpu")),
    )


@pytest.fixture
def bundle(tmp_path):
    return Bundle(tmp_path / "bundle").create()


@pytest.fixture(params=["visible", "invisible"])
def layered(request, tmp_path):
    """A PDF whose pages all carry a text layer, `count` pages long.

    `visible` is a typed page; `invisible` is the scanned-book shape, the
    recognised text in render mode 3 (tests/test_docling_adapter.py's
    invisible-layer fixture).
    """
    _pdfium_or_skip()

    def make(count=2):
        pages = [f"Layer text on page {n}." for n in range(1, count + 1)]
        modes = {n: 3 for n in range(1, count + 1)}
        return write_pdf(
            tmp_path / f"{request.param}.pdf",
            pages,
            render_mode=modes if request.param == "invisible" else None,
        )

    make.kind = request.param
    return make


def converting(pages):
    """The `convert=` seam, returning `pages` (text or None) as the export,
    one segment per page, as `_export_pages` writes it."""
    calls = []

    def convert(pdf_path, **kwargs):
        calls.append(kwargs)
        images = Path(kwargs["out_dir"]) / docling_parser.IMAGE_DIR
        images.mkdir(parents=True, exist_ok=True)
        (images / "imageFile1.png").write_bytes(PNG)
        return BREAK.join(text or "" for text in pages)

    convert.calls = calls
    return convert


def _extract(bundle, pdf, pandoc, convert, settings=REPLACE, **kwargs):
    return docling_parser.extract_pdf(
        bundle, pdf, pandoc=pandoc, settings=settings, convert=convert, **kwargs
    )


# --------------------------------------------------------------- settings
def test_the_flag_is_the_full_page_ocr_mode_and_nothing_else():
    assert REPLACE.ocr_replace_layer is True
    assert KEEP.ocr_replace_layer is False
    # without OCR there is nothing to replace the layer with
    assert ExtractionSettings(ocr_mode="full_page").ocr_replace_layer is False


def test_toggling_the_flag_changes_the_identity():
    assert REPLACE.identity() != KEEP.identity()
    assert REPLACE.identity()["ocr_mode"] == "full_page"
    # no new identity field: `ocr_mode` already is one
    assert set(REPLACE.identity()) == set(KEEP.identity())


def test_the_stage_builds_the_setting_from_the_flag(tmp_path, monkeypatch):
    seen = []

    def record(bundle, path, *, settings, **kwargs):
        seen.append(settings)

    monkeypatch.setattr(docling_parser, "extract_pdf", record)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%fake\n")
    for flag in (True, False):
        stages.prepare(
            Bundle(tmp_path / f"b{flag}").create(),
            pdf,
            pandoc="pandoc",
            ocr=True,
            ocr_replace_layer=flag,
        )
    assert [s.ocr_mode for s in seen] == ["full_page", "default"]


def test_the_converter_asks_docling_for_full_page_ocr_only_with_the_flag(
    monkeypatch,
):
    pytest.importorskip("docling.document_converter")
    import docling.document_converter as module
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import OcrMode

    built = []

    class Recorder:
        def __init__(self, format_options):
            built.append(format_options[InputFormat.PDF].pipeline_options)

    monkeypatch.setattr(module, "DocumentConverter", Recorder)
    docling_parser._converter("cpu", REPLACE)
    docling_parser._converter("cpu", KEEP)
    assert [options.ocr_options.mode for options in built] == [
        OcrMode.FULL_PAGE,
        OcrMode.DEFAULT,
    ]
    assert all(options.do_ocr for options in built)


def real_document(texts, pages=None, picture_on=None, headings=None, empty_tables=()):
    """A real `DoclingDocument`: `texts` is `{page: text}`, `pages` the
    page numbers it has (docling keeps an empty page in `pages`),
    `picture_on` a page that also carries a picture, and `empty_tables`
    pages that carry a 2x2 table with no text in any cell. Built the way
    tests/test_pdf_structure_wiring.py builds one."""
    pytest.importorskip("docling_core")
    from docling_core.types.doc import document as d
    from PIL import Image

    document = d.DoclingDocument(name="real")
    for number in pages or sorted(texts):
        document.add_page(page_no=number, size=d.Size(width=612.0, height=792.0))

    def prov(page, text=""):
        box = d.BoundingBox(l=72, t=700, r=540, b=680, coord_origin="BOTTOMLEFT")
        return d.ProvenanceItem(page_no=page, bbox=box, charspan=(0, len(text)))

    for page, text in sorted(texts.items()):
        for heading in (headings or {}).get(page, ()):
            document.add_heading(text=heading, level=1, prov=prov(page, heading))
        document.add_text(label=d.DocItemLabel.TEXT, text=text, prov=prov(page, text))
    for page in empty_tables:
        cells = [
            d.TableCell(
                text="",
                start_row_offset_idx=row,
                end_row_offset_idx=row + 1,
                start_col_offset_idx=col,
                end_col_offset_idx=col + 1,
            )
            for row in range(2)
            for col in range(2)
        ]
        document.add_table(
            data=d.TableData(num_rows=2, num_cols=2, table_cells=cells),
            prov=prov(page),
        )
    if picture_on is not None:
        document.add_picture(
            image=d.ImageRef.from_pil(Image.new("RGB", (40, 30), "red"), dpi=72),
            prov=prov(picture_on),
        )
    return document


def exporting(document):
    """The `convert=` seam running the real document-to-Markdown step."""

    def convert(pdf_path, **kwargs):
        return docling_parser._document_markdown(
            document,
            pdf_path,
            out_dir=kwargs["out_dir"],
            span=kwargs["span"],
            formulas=kwargs["formulas"],
            report=kwargs["report"],
        )

    return convert


def page_bodies(bundle):
    """`{page number: body}` of the extracted source.md."""
    from book_maker.pipeline.pdf_common import PAGE_MARKER

    parts = PAGE_MARKER.split(bundle.source.read_text(encoding="utf-8"))
    return {int(n): body for n, body in zip(parts[1::2], parts[2::2])}


def _pdf(tmp_path, count):
    _pdfium_or_skip()
    return write_pdf(
        tmp_path / f"pages{count}.pdf", [f"Layer {n}." for n in range(1, count + 1)]
    )


# ---------------------------------- page markers from each item's own page
# Codex review 260924 (HIGH): docling writes no page break for a page with
# no item, so a page OCR read nothing on shifted every later page's text
# onto the wrong marker, and a gapped selection kept the wrong page.
def test_an_empty_first_page_keeps_page_two_s_text_under_page_two(
    bundle, tmp_path, pandoc, capsys
):
    pdf = _pdf(tmp_path, 2)
    document = real_document({2: "Text read on page two."}, pages=[1, 2])
    _extract(bundle, pdf, pandoc, exporting(document))
    bodies = page_bodies(bundle)
    assert "Text read on page two." in bodies[2]
    assert "Text read on page two." not in bodies[1]
    out = capsys.readouterr().out
    assert OCR_REPLACE_EMPTY.format(page=1) in out
    assert OCR_REPLACE_EMPTY.format(page=2) not in out


def test_an_empty_middle_page_keeps_page_three_s_text_under_page_three(
    bundle, tmp_path, pandoc, capsys
):
    pdf = _pdf(tmp_path, 3)
    document = real_document(
        {1: "Text on page one.", 3: "Text on page three."}, pages=[1, 2, 3]
    )
    _extract(bundle, pdf, pandoc, exporting(document))
    bodies = page_bodies(bundle)
    assert "Text on page one." in bodies[1]
    assert "Text on page three." in bodies[3]
    assert "Text on page" not in bodies[2]
    assert OCR_REPLACE_EMPTY.format(page=2) in capsys.readouterr().out


@pytest.mark.parametrize("page_two", [None, "Text on page two."])
def test_a_gapped_selection_keeps_page_three_and_drops_page_two(
    bundle, tmp_path, pandoc, page_two
):
    # --pages 1,3: the run 1-3 is read and page 2 dropped afterwards
    pdf = _pdf(tmp_path, 3)
    texts = {1: "Text on page one.", 3: "Text on page three."}
    if page_two:
        texts[2] = page_two
    document = real_document(texts, pages=[1, 2, 3])
    _extract(bundle, pdf, pandoc, exporting(document), page_range="1,3")
    bodies = page_bodies(bundle)
    assert sorted(bodies) == [1, 3]
    assert "Text on page one." in bodies[1]
    assert "Text on page three." in bodies[3]
    source = bundle.source.read_text(encoding="utf-8")
    assert "Text on page two." not in source


def test_a_heading_that_opens_a_page_is_promoted_like_any_other(
    bundle, tmp_path, pandoc
):
    # smoke 260924 (zh_hans scan): the pages were joined with the break
    # alone, so a heading opening page 2 did not start a line and
    # `pdf_headings.promote` left it one level too deep
    pdf = _pdf(tmp_path, 2)
    document = real_document(
        {1: "Text on page one.", 2: "Text on page two."},
        headings={2: ["Section Two"]},
    )
    _extract(bundle, pdf, pandoc, exporting(document), settings=KEEP)
    lines = page_bodies(bundle)[2].splitlines()
    assert "# Section Two" in lines
    assert "## Section Two" not in lines


# Codex re-verify 260924 (HIGH): an empty table exports its pipes, and the
# Markdown then called the page read.
def test_a_page_holding_only_an_empty_table_is_an_empty_page(
    bundle, tmp_path, pandoc, capsys
):
    pdf = _pdf(tmp_path, 2)
    document = real_document({2: "Text on page two."}, pages=[1, 2], empty_tables=[1])
    _extract(bundle, pdf, pandoc, exporting(document))
    assert "|" in page_bodies(bundle)[1]  # the table's pipes are there
    assert OCR_REPLACE_EMPTY.format(page=1) in capsys.readouterr().out


def test_empty_tables_on_every_page_stop_before_translation(bundle, tmp_path, pandoc):
    pdf = _pdf(tmp_path, 2)
    document = real_document({}, pages=[1, 2], empty_tables=[1, 2])
    with pytest.raises(PipelineError) as stopped:
        _extract(bundle, pdf, pandoc, exporting(document))
    assert stopped.value.detail == OCR_REPLACE_ALL_EMPTY


def test_a_table_with_text_is_a_page_read(bundle, tmp_path, pandoc, capsys):
    pytest.importorskip("docling_core")
    from docling_core.types.doc import document as d

    pdf = _pdf(tmp_path, 2)
    document = real_document({2: "Text on page two."}, pages=[1, 2], empty_tables=[1])
    document.tables[0].data.table_cells[0].text = "cell"
    _extract(bundle, pdf, pandoc, exporting(document))
    assert OCR_REPLACE_EMPTY.format(page=1) not in capsys.readouterr().out
    assert isinstance(document.tables[0], d.TableItem)


def test_a_picture_is_written_once_and_placed_on_its_own_page(
    bundle, tmp_path, pandoc, monkeypatch
):
    # pins docling-core's `_with_pictures_refs`, which `_export_pages`
    # calls once rather than deep-copying the document for every page
    pdf = _pdf(tmp_path, 2)
    document = real_document({1: "Text on page one."}, pages=[1, 2], picture_on=2)
    original = type(document)._with_pictures_refs
    calls = []

    def counted(self, *args, **kwargs):
        calls.append(kwargs.get("page_no"))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(type(document), "_with_pictures_refs", counted)
    _extract(bundle, pdf, pandoc, exporting(document), settings=KEEP)
    assert calls == [None]  # once, for the whole document
    bodies = page_bodies(bundle)
    assert "![" in bodies[2] and "![" not in bodies[1]
    pictures = [p for p in bundle.assets.rglob("*.png")]
    assert len(pictures) == 1


def test_an_item_over_two_pages_is_written_once_under_its_first(
    bundle, tmp_path, pandoc
):
    # docling's page filter reads prov[0]: a paragraph that runs onto the
    # next page is written once, on the page it starts on
    pytest.importorskip("docling_core")
    from docling_core.types.doc import document as d

    pdf = _pdf(tmp_path, 2)
    document = real_document({2: "Text on page two."}, pages=[1, 2])
    box = d.BoundingBox(l=72, t=700, r=540, b=680, coord_origin="BOTTOMLEFT")
    document.add_text(
        label=d.DocItemLabel.TEXT,
        text="Runs over the page.",
        prov=d.ProvenanceItem(page_no=1, bbox=box, charspan=(0, 19)),
    ).prov.append(d.ProvenanceItem(page_no=2, bbox=box, charspan=(0, 19)))
    _extract(bundle, pdf, pandoc, exporting(document), settings=KEEP)
    source = bundle.source.read_text(encoding="utf-8")
    assert source.count("Runs over the page.") == 1
    assert "Runs over the page." in page_bodies(bundle)[1]


# Codex re-verify 260924 (MEDIUM): the unplaced tail counted as text on the
# last numbered page, so an empty final page went unnamed.
def test_the_unplaced_tail_does_not_fill_an_empty_final_page(
    bundle, tmp_path, pandoc, capsys
):
    pytest.importorskip("docling_core")
    from docling_core.types.doc import document as d

    pdf = _pdf(tmp_path, 2)
    document = real_document({1: "Text on page one."}, pages=[1, 2])
    document.add_text(label=d.DocItemLabel.TEXT, text="A note with no page.")
    _extract(bundle, pdf, pandoc, exporting(document))
    source = bundle.source.read_text(encoding="utf-8")
    assert "A note with no page." in source
    assert blank_pages(source) == ([2], True)
    assert docling_parser._pages_read(None, source) == {1}
    assert OCR_REPLACE_EMPTY.format(page=2) in " ".join(capsys.readouterr().out.split())


def test_the_scanned_page_check_ends_at_the_unplaced_tail(tmp_path):
    source = tmp_path / "source.md"
    source.write_text(
        "<!-- page 1 -->\n\nText on page one.\n\n<!-- page 2 -->\n\n"
        f"{UNPLACED_MARKER}\n\nA note with no page.\n",
        encoding="utf-8",
    )
    assert check_recognised_text(source, [2]) == [2]


# Codex re-verify 260924 (MEDIUM): one item without a page switched the
# whole document back to docling's breaks, and the misnumbering with it.
def test_an_item_with_no_page_goes_after_the_last_page_and_is_said(
    bundle, tmp_path, pandoc, capsys
):
    pytest.importorskip("docling_core")
    from docling_core.types.doc import document as d

    pdf = _pdf(tmp_path, 2)
    document = real_document({2: "Text on page two."}, pages=[1, 2])
    document.add_text(label=d.DocItemLabel.TEXT, text="A note with no page.")
    _extract(bundle, pdf, pandoc, exporting(document))
    bodies = page_bodies(bundle)
    assert "Text on page two." in bodies[2]
    assert "Text on page two." not in bodies[1]
    assert "A note with no page." not in bodies[1]
    unplaced = bodies[2].split(docling_parser.UNPLACED_MARKER)
    assert len(unplaced) == 2
    assert "Text on page two." in unplaced[0]
    assert "A note with no page." in unplaced[1]
    line = PAGE_MAP_UNPLACED.format(n=1)
    assert line in capsys.readouterr().out
    manifest = bundle.read_manifest()
    assert line in manifest["limitations"]
    assert line in manifest["extraction"]["limitations"]
    # the empty page is still named
    assert OCR_REPLACE_EMPTY.format(page=1) in " ".join(
        manifest["extraction"]["limitations"]
    )


def test_a_document_with_every_item_placed_says_nothing_about_it(
    bundle, tmp_path, pandoc, capsys
):
    pdf = _pdf(tmp_path, 2)
    document = real_document({1: "One.", 2: "Two."}, pages=[1, 2])
    _extract(bundle, pdf, pandoc, exporting(document))
    assert "carry no page number" not in capsys.readouterr().out
    assert docling_parser.UNPLACED_MARKER not in bundle.source.read_text(
        encoding="utf-8"
    )


def test_nothing_read_on_a_real_document_stops_before_translation(
    bundle, tmp_path, pandoc
):
    # Codex next step: the all-empty stop through the real export, with
    # no text item on any selected page
    pdf = _pdf(tmp_path, 2)
    document = real_document({}, pages=[1, 2])
    with pytest.raises(PipelineError) as stopped:
        _extract(bundle, pdf, pandoc, exporting(document))
    assert stopped.value.detail == OCR_REPLACE_ALL_EMPTY
    assert not bundle.source.exists()


# ------------------------------------------------------ failure semantics
def test_a_page_read_empty_is_named_and_recorded_and_the_run_goes_on(
    bundle, layered, pandoc, capsys
):
    pdf = layered()
    _extract(bundle, pdf, pandoc, converting(["Read by OCR on one.", None]))
    out = capsys.readouterr().out
    line = OCR_REPLACE_EMPTY.format(page=2)
    assert line in out
    assert OCR_REPLACE_EMPTY.format(page=1) not in out
    assert bundle.stage_status("extract") == "completed"
    manifest = bundle.read_manifest()
    assert line in manifest["limitations"]
    assert line in manifest["extraction"]["limitations"]
    # the empty page is empty: nothing of the layer was put in its place
    assert "Layer text" not in bundle.source.read_text(encoding="utf-8")


def test_every_page_read_empty_stops_before_translation(
    bundle, layered, pandoc, capsys
):
    pdf = layered()
    with pytest.raises(PipelineError) as stopped:
        _extract(bundle, pdf, pandoc, converting([None, None]))
    # the replacement's own stop, not the generic "rerun with --pdf-ocr"
    assert stopped.value.detail == OCR_REPLACE_ALL_EMPTY
    assert stopped.value.detail != EXTRACTION_EMPTY
    out = capsys.readouterr().out
    assert OCR_REPLACE_EMPTY.format(page=1) in out
    assert OCR_REPLACE_EMPTY.format(page=2) in out
    assert bundle.stage_status("extract") == "failed"
    assert not bundle.source.exists()


# Codex review 260924 (MEDIUM): the stop wrote nothing to the manifest.
def test_the_all_empty_stop_is_recorded_in_the_manifest(bundle, layered, pandoc):
    pdf = layered()
    with pytest.raises(PipelineError):
        _extract(bundle, pdf, pandoc, converting([None, None]))
    manifest = bundle.read_manifest()
    assert manifest["stages"]["extract"]["status"] == "failed"
    extraction = manifest["extraction"]
    assert extraction["status"] == "failed"
    assert extraction["failure"] == OCR_REPLACE_ALL_EMPTY
    assert extraction["empty_pages"] == [1, 2]
    assert extraction["ocr_mode"] == "full_page"
    assert extraction["ocr_replace_layer"] is True
    lines = [OCR_REPLACE_EMPTY.format(page=n) for n in (1, 2)]
    for line in lines + [OCR_REPLACE_ALL_EMPTY]:
        assert line in manifest["limitations"]
        assert line in extraction["limitations"]


def test_a_stopped_retry_leaves_no_completed_extraction_behind(bundle, layered, pandoc):
    pdf = layered()
    _extract(bundle, pdf, pandoc, converting(["One.", "Two."]), settings=KEEP)
    assert stages.already_prepared(bundle, pdf, "docling", None, KEEP)
    with pytest.raises(PipelineError):
        _extract(bundle, pdf, pandoc, converting([None, None]))
    extraction = bundle.read_manifest()["extraction"]
    # the earlier run's block is gone, not merged under the failure
    assert extraction["status"] == "failed"
    assert "provider" not in extraction
    assert "pages_without_text_layer" not in extraction
    assert not stages.already_prepared(bundle, pdf, "docling", None, KEEP)
    assert not stages.already_prepared(bundle, pdf, "docling", None, REPLACE)


# Codex re-verify 260924 (MEDIUM): the success writer merged into the
# failed block, leaving status 'failed', `failure` and `empty_pages` beside
# a completed stage.
def test_a_successful_retry_after_the_stop_leaves_no_failure_behind(
    bundle, layered, pandoc
):
    pdf = layered()
    with pytest.raises(PipelineError):
        _extract(bundle, pdf, pandoc, converting([None, None]))
    failed = bundle.read_manifest()["extraction"]["limitations"]
    _extract(bundle, pdf, pandoc, converting(["One.", "Two."]))
    manifest = bundle.read_manifest()
    assert manifest["stages"]["extract"]["status"] == "completed"
    extraction = manifest["extraction"]
    for key in ("status", "failure", "empty_pages"):
        assert key not in extraction, key
    assert extraction["provider"] == "docling"
    assert extraction["ocr_replace_layer"] is True
    # the failed attempt's lines are gone, from both lists
    for line in failed:
        assert line not in manifest["limitations"], line
        assert line not in extraction["limitations"], line
    assert stages.already_prepared(bundle, pdf, "docling", None, REPLACE)


def test_without_the_flag_an_empty_page_is_not_called_a_replaced_layer(
    bundle, layered, pandoc, capsys
):
    pdf = layered()
    _extract(bundle, pdf, pandoc, converting(["Text.", None]), settings=KEEP)
    out = capsys.readouterr().out
    assert "--ocr-replace-layer the layer is not used" not in out
    assert OCR_REPLACING_LAYER not in out


def test_a_long_list_of_empty_pages_is_capped_on_the_terminal_only(
    bundle, tmp_path, pandoc, capsys
):
    _pdfium_or_skip()
    pdf = write_pdf(tmp_path / "long.pdf", [f"Layer {n}." for n in range(1, 13)])
    _extract(bundle, pdf, pandoc, converting(["Only page one read."] + [None] * 11))
    out = capsys.readouterr().out
    assert out.count("the OCR engine read nothing where the PDF carried") == 10
    assert OCR_REPLACE_EMPTY.format(page=11) in out
    assert OCR_REPLACE_EMPTY.format(page=12) not in out
    assert OCR_REPLACE_EMPTY_MORE.format(count=1) in out
    limitations = bundle.read_manifest()["extraction"]["limitations"]
    assert [n for n in limitations if "read nothing where" in n] == [
        OCR_REPLACE_EMPTY.format(page=n) for n in range(2, 13)
    ]


def test_a_page_without_a_layer_is_not_a_replaced_layer(
    bundle, tmp_path, pandoc, capsys
):
    # page 2 has no text layer at all: OCR_EMPTY_PAGES is its warning,
    # not the replacement's
    _pdfium_or_skip()
    pdf = write_pdf(tmp_path / "mixed.pdf", ["Layer on one.", None])
    _extract(bundle, pdf, pandoc, converting(["Read on one.", None]))
    out = capsys.readouterr().out
    assert OCR_REPLACE_EMPTY.format(page=2) not in out
    assert "no text was recognised on page(s) 2" in out


def test_a_selection_is_checked_page_by_page_with_its_own_numbers(
    bundle, tmp_path, pandoc, capsys
):
    _pdfium_or_skip()
    pdf = write_pdf(tmp_path / "four.pdf", [f"Layer {n}." for n in range(1, 5)])
    convert = converting(["Read on three.", None])
    _extract(bundle, pdf, pandoc, convert, page_range="3-4")
    out = capsys.readouterr().out
    assert OCR_REPLACE_EMPTY.format(page=4) in out
    assert OCR_REPLACE_EMPTY.format(page=3) not in out


# ------------------------------------------------------ what is said/kept
def test_the_replacement_is_said_and_asks_for_languages(
    bundle, layered, pandoc, capsys
):
    pdf = layered()
    _extract(bundle, pdf, pandoc, converting(["One.", "Two."]))
    out = capsys.readouterr().out
    assert OCR_REPLACING_LAYER in out
    # the case where the language matters most: every page is read again
    assert out.count(OCR_LANG_DEFAULT) == 1
    if layered.kind == "invisible":
        # the invisible layer line keeps printing, and names the flag
        assert INVISIBLE_TEXT_LAYER.format(count=2, total=2) in out
        assert "--ocr-replace-layer" in INVISIBLE_TEXT_LAYER


def test_languages_given_silence_the_hint(bundle, layered, pandoc, capsys):
    pdf = layered()
    settings = ExtractionSettings(ocr=True, ocr_mode="full_page", ocr_lang=("ch",))
    _extract(bundle, pdf, pandoc, converting(["One.", "Two."]), settings=settings)
    assert OCR_LANG_DEFAULT not in capsys.readouterr().out


def test_the_manifest_says_the_layer_was_replaced(bundle, layered, pandoc):
    pdf = layered()
    _extract(bundle, pdf, pandoc, converting(["One.", "Two."]))
    extraction = bundle.read_manifest()["extraction"]
    assert extraction["ocr_mode"] == "full_page"
    assert extraction["ocr_replace_layer"] is True
    assert extraction["pages_read_by_ocr"] == [1, 2]
    # read back as the same setting; the derived field is not identity
    assert ExtractionSettings.from_manifest(extraction) == REPLACE


def test_the_manifest_says_a_kept_layer_was_kept(bundle, layered, pandoc):
    pdf = layered()
    _extract(bundle, pdf, pandoc, converting(["One.", "Two."]), settings=KEEP)
    extraction = bundle.read_manifest()["extraction"]
    assert extraction["ocr_replace_layer"] is False
    assert extraction["pages_read_by_ocr"] == []


def test_a_rerun_that_toggles_the_flag_extracts_again(bundle, layered, pandoc):
    pdf = layered()
    _extract(bundle, pdf, pandoc, converting(["One.", "Two."]))
    assert stages.already_prepared(bundle, pdf, "docling", None, REPLACE)
    assert not stages.already_prepared(bundle, pdf, "docling", None, KEEP)


# ---------------------------------------------------------------- harness
def _harness():
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "tools" / "pdf_to_book.py"
    spec = importlib.util.spec_from_file_location("pdf_to_book", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_harness_takes_the_flag_to_the_extraction(tmp_path, pandoc, monkeypatch):
    seen = []

    def record(bundle, path, *, settings, **kwargs):
        seen.append(settings)
        raise PipelineError("stopped before the models", stage="extract")

    monkeypatch.setattr(docling_parser, "extract_pdf", record)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%fake\n")
    code = _harness().main(
        [
            "--pandoc",
            pandoc,
            "extract",
            str(pdf),
            "--output",
            str(tmp_path / "b"),
            "--pdf-ocr",
            "--ocr-replace-layer",
        ]
    )
    assert code == 1
    assert [s.ocr_mode for s in seen] == ["full_page"]


def test_the_harness_refuses_the_flag_without_ocr(
    tmp_path, pandoc, monkeypatch, capsys
):
    def refuse(*args, **kwargs):
        raise AssertionError("extracted without OCR to replace the layer with")

    monkeypatch.setattr(docling_parser, "extract_pdf", refuse)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%fake\n")
    code = _harness().main(
        [
            "--pandoc",
            pandoc,
            "extract",
            str(pdf),
            "--output",
            str(tmp_path / "b"),
            "--ocr-replace-layer",
        ]
    )
    assert code == 1
    assert OCR_REPLACE_NEEDS_OCR in " ".join(capsys.readouterr().out.split())


def test_the_flag_on_markdown_is_refused_as_a_pdf_option():
    options = type("Options", (), {"ocr_replace_layer": True, "pdf_ocr": True})()
    with pytest.raises(PipelineError) as refused:
        stages.check_pdf_options("markdown", options)
    assert refused.value.detail == PDF_OPTIONS_INERT
    assert "--ocr-replace-layer" in PDF_OPTIONS_INERT
