"""Figures drawn by our own renderer at a chosen resolution (packet Q).

PIN (owner 260925, accepted plan; docs/250925-eval-CODEX_CONSULT_PDF_PICTURE_RESOLUTION.md
and docs/260925-docs-PDF_FIGURE_QUALITY_DESIGN.md): docling keeps the layout
and the placement, and the figure pixels are drawn with pypdfium2 at a
resolution a general, pluggable `FigurePolicy` chooses. A figure's name in
`source.md` is its identity, never its pixels, so changing the policy
redraws the pictures only -- no extraction, no translation -- and the EPUB
is rebuilt with them. The policy is not an extraction setting and never
part of the translation identity.

No model is loaded apart from the one test marked for a real docling
conversion (skipped without docling). The document-to-Markdown step runs on
real `DoclingDocument`s through the `convert=` seam, the page geometry and
the drawing on real PDFs written by `pipeline_helpers.write_pdf`.
"""

import argparse
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import (  # noqa: E402
    FakeTranslator,
    pandoc_or_skip,
    register_fake_format,
    write_image_pdf,
    write_pdf,
)

from book_maker.pipeline import docling_parser, pdf_figures, pdf_formula  # noqa: E402
from book_maker.pipeline.bundle import Bundle  # noqa: E402
from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.pipeline.messages import (  # noqa: E402
    DEVICE_SELECTED,
    FIGURE_CAPPED,
    FIGURE_FALLBACK_MISSING,
    FIGURE_RECORD_BROKEN,
    FIGURE_RECORD_MISSING,
    FIGURE_RECORD_UNLISTED,
    FIGURE_RECORD_UNREADABLE,
    FIGURE_RENDER_FAILED,
    FIGURES_DRAWN,
    FIGURES_LEGACY,
    FIGURES_NATIVE,
    FIGURES_REDRAWN,
    FIGURES_UNREFERENCED,
)
from book_maker.pipeline.pdf_figures import (  # noqa: E402
    FIGURE_POLICY_DEFAULT,
    FigurePolicy,
    figure_scale,
    parse_figure_policy,
)
from book_maker.pipeline.pdf_settings import ExtractionSettings  # noqa: E402

HARNESS = Path(__file__).resolve().parent.parent / "tools" / "pdf_to_book.py"
OPTIONS = ["--api_format", "faketest", "--language", "zh-hans"]

A4 = (595.28, 841.89)
LETTER = (612.0, 792.0)


def load_harness():
    import importlib.util

    spec = importlib.util.spec_from_file_location("pdf_to_book", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _render_or_skip():
    pypdfium2 = pytest.importorskip("pypdfium2")
    pytest.importorskip("PIL")
    if not hasattr(pypdfium2, "PdfDocument"):
        pytest.skip("pypdfium2 is installed but unusable")


@pytest.fixture
def pandoc():
    return pandoc_or_skip()


@pytest.fixture
def device(monkeypatch):
    """The resolved device, without docling (the one call that imports it)."""
    monkeypatch.setattr(
        docling_parser,
        "resolve_device",
        lambda requested: ("cpu", DEVICE_SELECTED.format(device="cpu")),
    )


# --------------------------------------------------------------------------
# The policy: a pure function of the box, the page and the policy
# --------------------------------------------------------------------------
BOX = (72.0, 100.0, 472.0, 300.0)  # 400 x 200 points, top-left origin


@pytest.mark.parametrize("page", [A4, LETTER, (792.0, 612.0)])
def test_dpi_is_the_pdf_s_own_resolution_on_any_page(page):
    assert figure_scale(BOX, page, FigurePolicy("dpi", 200)) == pytest.approx(200 / 72)


def test_page_width_divides_the_budget_by_the_displayed_page_width():
    policy = FigurePolicy("page-width", 1654)
    assert figure_scale(BOX, A4, policy) == pytest.approx(1654 / 595.28)
    assert figure_scale(BOX, LETTER, policy) == pytest.approx(1654 / 612)
    # A portrait page turned by /Rotate 90 is shown 792 points wide.
    assert figure_scale(BOX, (792.0, 612.0), policy) == pytest.approx(1654 / 792)


def test_figure_px_gives_the_figure_its_own_width():
    policy = FigurePolicy("figure-px", 1600)
    assert figure_scale(BOX, A4, policy) == pytest.approx(4.0)
    # The page does not enter into it.
    assert figure_scale(BOX, LETTER, policy) == pytest.approx(4.0)


def test_figure_px_holds_a_tall_figure_to_one_and_a_half_times_its_width():
    tall = (0.0, 0.0, 400.0, 800.0)  # 4.0 by width, 3.0 by the height cap
    scale = figure_scale(tall, A4, FigurePolicy("figure-px", 1600))
    assert scale == pytest.approx(1.5 * 1600 / 800)
    assert 800 * scale == pytest.approx(2400)


def test_figure_px_never_draws_above_300_dpi():
    small = (0.0, 0.0, 100.0, 50.0)  # 16x by width; the ceiling is 300/72
    scale = figure_scale(small, A4, FigurePolicy("figure-px", 1600))
    assert scale == pytest.approx(300 / 72)


@pytest.mark.parametrize(
    "box", [(10.0, 10.0, 10.0, 50.0), (10.0, 50.0, 60.0, 50.0), (5.0, 5.0, 1.0, 9.0)]
)
def test_a_box_with_no_area_cannot_be_drawn(box):
    with pytest.raises(ValueError):
        figure_scale(box, A4, FigurePolicy("dpi", 200))


def test_the_policy_describes_itself_in_the_operator_s_words():
    assert FigurePolicy("dpi", 200).describe() == "200 DPI"
    assert FigurePolicy("page-width", 1654).describe() == "1654 px per page width"
    assert FigurePolicy("figure-px", 1600).describe() == "1600 px per figure"
    assert FigurePolicy("dpi", 200.0) == FigurePolicy("dpi", 200)
    assert FigurePolicy("dpi", 200.0).to_manifest() == {"kind": "dpi", "value": 200}


@pytest.mark.parametrize(
    "text, expected",
    [
        ("dpi:300", FigurePolicy("dpi", 300)),
        ("page-width:1654", FigurePolicy("page-width", 1654)),
        ("figure-px:1600", FigurePolicy("figure-px", 1600)),
        ("dpi:150.5", FigurePolicy("dpi", 150.5)),
    ],
)
def test_the_policy_parses_from_kind_and_value(text, expected):
    assert parse_figure_policy(text) == expected


@pytest.mark.parametrize(
    "text", ["dots:300", "dpi:0", "dpi:-72", "dpi:", "300", "dpi:many", "dpi:inf"]
)
def test_a_policy_that_does_not_parse_names_the_three_kinds(text):
    with pytest.raises(argparse.ArgumentTypeError) as refused:
        parse_figure_policy(text)
    assert "dpi, page-width, figure-px" in str(refused.value)


@pytest.mark.parametrize("value", ["bogus:1", "dpi:0", "page-width:-5"])
def test_the_harness_refuses_a_bad_policy_at_parse_time(value, capsys):
    harness = load_harness()
    with pytest.raises(SystemExit) as stopped:
        harness.build_parser().parse_args(
            ["extract", "x.pdf", "--output", "b", "--figure-policy", value]
        )
    assert stopped.value.code == 2
    assert "dpi, page-width, figure-px" in capsys.readouterr().err


def test_the_harness_default_is_the_one_constant():
    parser = load_harness().build_parser()
    for command in (["extract", "x.pdf"], ["run", "x.pdf"]):
        options = parser.parse_args(command + ["--output", "b"])
        assert options.figure_policy is FIGURE_POLICY_DEFAULT
    # `export` redraws only when asked.
    assert parser.parse_args(["export", "b"]).figure_policy is None
    assert parser.parse_args(
        ["export", "b", "--figure-policy", "dpi:300"]
    ).figure_policy == FigurePolicy("dpi", 300)


def test_the_policy_is_not_an_extraction_setting():
    """PIN (packet Q contract 4): a new policy must never re-extract."""
    import dataclasses

    fields = {field.name for field in dataclasses.fields(ExtractionSettings)}
    assert not any("figure" in name or "policy" in name for name in fields)
    identity = ExtractionSettings().identity()
    assert not any("figure" in key or "policy" in key for key in identity)


def test_the_policy_never_enters_the_translation_identity():
    """PIN (packet Q contracts 5 and 9): the route owns the option, and a
    namespace that carries it hashes the same as one that does not."""
    from book_maker.pipeline.to_epub import translation_argv
    from book_maker.pipeline.translate import option_identity, parse_bbm_options

    assert translation_argv(
        ["--figure-policy", "dpi:300", "--model", "m", "--figure-policy=dpi:100"]
    ) == ["--model", "m"]
    plain = parse_bbm_options(["--language", "ja"])
    carrying = parse_bbm_options(["--language", "ja"])
    carrying.figure_policy = FigurePolicy("dpi", 300)
    assert option_identity(carrying) == option_identity(plain)


def test_formulas_keep_their_own_resolution():
    # PIN: owner 260925 accepted plan: formulas keep 216 DPI;
    # docs/250925-eval-CODEX_CONSULT_PDF_PICTURE_RESOLUTION.md
    assert pdf_formula.SCALE == 3.0


# --------------------------------------------------------------------------
# Drawing a box from a real page
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "policy",
    [
        FigurePolicy("dpi", 150),
        FigurePolicy("page-width", 1654),
        FigurePolicy("figure-px", 1600),
    ],
)
@pytest.mark.parametrize("rotate", [None, 90])
def test_a_drawn_figure_is_as_wide_as_the_policy_says(tmp_path, policy, rotate):
    _render_or_skip()
    import pypdfium2

    _render_or_skip()
    from PIL import Image

    pdf = write_pdf(
        tmp_path / "figure.pdf", ["Prose."], figure=(1, ["Label"]), rotate=rotate
    )
    document = pypdfium2.PdfDocument(str(pdf))
    page = document[0]
    width, height = page.get_size()
    # The turned page is shown landscape: its displayed width is 792.
    assert width == pytest.approx(792 if rotate else 612)
    record = {"id": "p0001-01", "page": 1, "bbox": [40.0, 60.0, 340.0, 260.0]}
    out = tmp_path / "out.png"
    try:
        pdf_figures._draw(page, record, policy, out)
    finally:
        page.close()
        document.close()
    scale = figure_scale(record["bbox"], (width, height), policy)
    with Image.open(out) as image:
        assert abs(image.width - round(300 * scale)) <= 1
        assert abs(image.height - round(200 * scale)) <= 1


# --------------------------------------------------------------------------
# Extraction: stable names, widths, the record
# --------------------------------------------------------------------------
def figure_document(pictures):
    """A real `DoclingDocument` over a two-page Letter PDF.

    `pictures` is `[(page, (l, b, r, t) bottom-left, colour)]`, in reading
    order; each carries a docling picture of its own colour, so a file can
    be traced back to the item it came from.
    """
    pytest.importorskip("docling_core")
    from docling_core.types.doc import document as d

    _render_or_skip()
    from PIL import Image

    document = d.DoclingDocument(name="figures")
    for number in (1, 2):
        document.add_page(page_no=number, size=d.Size(width=612.0, height=792.0))

    def prov(page, box, text=""):
        l, b, r, t = box
        bbox = d.BoundingBox(l=l, t=t, r=r, b=b, coord_origin="BOTTOMLEFT")
        return d.ProvenanceItem(page_no=page, bbox=bbox, charspan=(0, len(text)))

    for page in (1, 2):
        text = f"Prose on page {page}."
        document.add_text(
            label=d.DocItemLabel.TEXT, text=text, prov=prov(page, (72, 680, 540, 700))
        )
    for page, box, colour in pictures:
        size = (int(box[2] - box[0]), int(box[3] - box[1]))
        document.add_picture(
            image=d.ImageRef.from_pil(Image.new("RGB", size, colour), dpi=72),
            prov=prov(page, box),
        )
    return document


# Two figures on page 2: a wide one near the top, a narrow one below.
TWO = [(2, (72, 400, 372, 650), "red"), (2, (100, 100, 253, 300), "blue")]


def exporting(make):
    """The `convert=` seam running the real document-to-Markdown step on a
    fresh document from `make()`; counts its calls."""

    def convert(pdf_path, **kwargs):
        convert.calls += 1
        return docling_parser._document_markdown(
            make(),
            pdf_path,
            out_dir=kwargs["out_dir"],
            span=kwargs["span"],
            formulas=kwargs["formulas"],
            report=kwargs["report"],
        )

    convert.calls = 0
    return convert


def two_page_pdf(tmp_path):
    _render_or_skip()
    return write_pdf(
        tmp_path / "paper.pdf",
        ["Prose on page 1.", "Prose on page 2."],
        figure=(2, ["Axis label"]),
        figure_clip=False,
    )


def extracted(tmp_path, pandoc, pictures=TWO, name="bundle"):
    pdf = two_page_pdf(tmp_path)
    bundle = Bundle(tmp_path / name).create()
    docling_parser.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        convert=exporting(lambda: figure_document(pictures)),
    )
    return bundle, pdf


def records(bundle):
    return json.loads(
        (bundle.work_file("extraction") / "figures.json").read_text(encoding="utf-8")
    )


def test_each_picture_is_named_by_its_page_and_order_with_its_width(
    tmp_path, pandoc, device
):
    _render_or_skip()
    from PIL import Image

    bundle, _pdf = extracted(tmp_path, pandoc)
    source = bundle.source.read_text(encoding="utf-8")
    # No alt text: docling's "Image" would become a caption (see below).
    first = "![](assets/figures/p0002-01.png){width=49%}"
    second = "![](assets/figures/p0002-02.png){width=25%}"
    assert first in source and second in source
    assert source.index(first) < source.index(second)
    # No hash-named docling picture reaches the Markdown or the assets.
    assert "assets/images/image_" not in source
    assert not list(bundle.assets.glob("images/image_*"))

    figures = records(bundle)
    assert [r["id"] for r in figures] == ["p0002-01", "p0002-02"]
    # Top-left origin, points, the page's displayed frame.
    assert figures[0]["bbox"] == [72.0, 142.0, 372.0, 392.0]
    assert figures[1]["bbox"] == [100.0, 492.0, 253.0, 692.0]
    # The box drawn: 2 pt of room on every side, nothing near either.
    assert figures[0]["crop"] == [70.0, 140.0, 374.0, 394.0]
    assert figures[1]["crop"] == [98.0, 490.0, 255.0, 694.0]
    assert [r["file"] for r in figures] == [
        "assets/figures/p0002-01.png",
        "assets/figures/p0002-02.png",
    ]
    # One record per reference, one reference per record.
    assert source.count("](assets/figures/") == len(figures)
    # By identity: each name holds its own item's picture (the stand-in,
    # docling's pixels, until the render step draws it).
    with Image.open(bundle.root / figures[0]["file"]) as red:
        assert red.getpixel((0, 0)) == (255, 0, 0)
    with Image.open(bundle.root / figures[1]["file"]) as blue:
        assert blue.getpixel((0, 0)) == (0, 0, 255)
    # docling's own file stays in the working directory as the fallback.
    for record in figures:
        fallback = bundle.root / record["fallback"]
        assert fallback.is_file()
        assert fallback.parent == bundle.work_file("extraction") / "images"


def test_a_page_the_selection_drops_takes_its_figures_with_it(tmp_path, pandoc, device):
    pictures = [(1, (72, 400, 372, 650), "green")] + TWO
    pdf = write_pdf(
        tmp_path / "three.pdf", ["One.", "Two.", "Three."], figure=(3, ["x"])
    )
    _render_or_skip()
    bundle = Bundle(tmp_path / "bundle").create()

    def make():
        from docling_core.types.doc import document as d

        document = figure_document(pictures)
        document.add_page(page_no=3, size=d.Size(width=612.0, height=792.0))
        box = d.BoundingBox(l=72, t=700, r=540, b=680, coord_origin="BOTTOMLEFT")
        document.add_text(
            label=d.DocItemLabel.TEXT,
            text="Prose on page 3.",
            prov=d.ProvenanceItem(page_no=3, bbox=box, charspan=(0, 16)),
        )
        return document

    docling_parser.extract_pdf(
        bundle, pdf, pandoc=pandoc, page_range="1,3", convert=exporting(make)
    )
    assert [r["id"] for r in records(bundle)] == ["p0001-01"]
    assert "p0002-" not in bundle.source.read_text(encoding="utf-8")


def test_the_first_draw_writes_the_figures_and_the_manifest_block(
    tmp_path, pandoc, device, capsys
):
    _render_or_skip()
    from PIL import Image

    bundle, pdf = extracted(tmp_path, pandoc)
    before = bundle.source.read_bytes()
    capsys.readouterr()
    block = pdf_figures.render_figures(bundle, pdf, FigurePolicy("dpi", 144))
    out = capsys.readouterr().out
    assert block["policy"] == {"kind": "dpi", "value": 144}
    assert block["revision"] == pdf_figures.FIGURE_REVISION
    assert block["count"] == 2 and block["failed"] == []
    files = [bundle.root / r["file"] for r in records(bundle)]
    assert block["bytes"] == sum(f.stat().st_size for f in files)
    assert bundle.read_manifest()["figures"] == block
    assert (
        FIGURES_DRAWN.format(
            count=2, policy="144 DPI", size=pdf_figures.human_size(block["bytes"])
        )
        in out
    )
    assert FIGURES_REDRAWN.split("{")[0] not in out
    assert "kept at the resolution of the picture" not in out
    # 2 px per point: the 300-point figure and 2 pt of padding each side
    # are 608 px wide, drawn from the page, not docling's 300-px picture.
    with Image.open(files[0]) as image:
        assert abs(image.width - 608) <= 1
    # Each file's state, for the reuse check: size, hash, the scales.
    state = block["files"]["p0002-01"]
    assert state["size"] == files[0].stat().st_size
    assert state["requested_scale"] == state["effective_scale"] == 2.0
    assert "native_dpi" not in state
    # Written through a sibling and a rename: nothing else is left.
    assert sorted(p.name for p in files[0].parent.iterdir()) == [
        "p0002-01.png",
        "p0002-02.png",
    ]
    assert bundle.source.read_bytes() == before


def test_a_figure_that_cannot_be_drawn_keeps_docling_s_picture(
    tmp_path, pandoc, device, capsys, monkeypatch
):
    bundle, pdf = extracted(tmp_path, pandoc)
    real = pdf_figures._draw

    def failing(page, record, policy, destination, **kwargs):
        if record["id"] == "p0002-02":
            raise RuntimeError("injected")
        return real(page, record, policy, destination, **kwargs)

    monkeypatch.setattr(pdf_figures, "_draw", failing)
    capsys.readouterr()
    block = pdf_figures.render_figures(bundle, pdf, FigurePolicy("dpi", 144))
    out = capsys.readouterr().out
    line = FIGURE_RENDER_FAILED.format(
        id="p0002-02", page=2, policy="144 DPI", err="RuntimeError: injected"
    )
    assert out.count(line) == 1
    assert block["failed"] == ["p0002-02"]
    assert line in bundle.read_manifest()["limitations"]
    failed = next(r for r in records(bundle) if r["id"] == "p0002-02")
    assert (bundle.root / failed["file"]).read_bytes() == (
        bundle.root / failed["fallback"]
    ).read_bytes()
    assert (
        FIGURES_DRAWN.format(
            count=1, policy="144 DPI", size=pdf_figures.human_size(block["bytes"])
        )
        in out
    )

    # The next drawing takes the old failure back.
    monkeypatch.setattr(pdf_figures, "_draw", real)
    block = pdf_figures.render_figures(bundle, pdf, FigurePolicy("dpi", 100))
    assert block["failed"] == []
    assert line not in bundle.read_manifest()["limitations"]


def test_a_degenerate_box_is_a_failure_with_the_fallback(
    tmp_path, pandoc, device, capsys
):
    bundle, pdf = extracted(tmp_path, pandoc)
    path = bundle.work_file("extraction") / "figures.json"
    figures = records(bundle)
    figures[0]["bbox"] = [100.0, 100.0, 100.0, 300.0]
    path.write_text(json.dumps(figures), encoding="utf-8")
    block = pdf_figures.render_figures(bundle, pdf, FigurePolicy("dpi", 144))
    assert block["failed"] == ["p0002-01"]
    assert "Figure p0002-01 on page 2 could not be drawn" in capsys.readouterr().out


def test_a_bundle_from_before_figures_were_drawn_is_left_alone(
    tmp_path, pandoc, device, capsys
):
    import re

    bundle, pdf = extracted(tmp_path, pandoc)
    # The shape of a bundle made before this change: docling's hash-named
    # pictures in source.md, no stable name, no record, no `figures` block.
    (bundle.work_file("extraction") / "figures.json").unlink()
    legacy, count = re.subn(
        r"assets/figures/p0002-0(\d)\.png\)\{width=\d+%\}",
        r"assets/images/image_00000\1_abc.png)",
        bundle.source.read_text(encoding="utf-8"),
    )
    assert count == 2
    bundle.source.write_text(legacy, encoding="utf-8")
    before = bundle.source.read_bytes()
    assets = {p: p.read_bytes() for p in bundle.assets.rglob("*") if p.is_file()}
    capsys.readouterr()
    assert pdf_figures.render_figures(bundle, pdf, FIGURE_POLICY_DEFAULT) is None
    assert capsys.readouterr().out.count(FIGURES_LEGACY) == 1
    assert bundle.source.read_bytes() == before
    assert "figures" not in bundle.read_manifest()
    assert {p: p.read_bytes() for p in bundle.assets.rglob("*") if p.is_file()} == (
        assets
    )


# PIN (Codex review 260925, lead's fix list; docs/260925-feat-PDF_FIGURE_RENDER*):
# legacy is decided from source.md alone. A bundle whose source.md names
# stable figures must carry a record for each of them, else the run stops
# before anything is drawn or reused -- a missing record must never look
# like a legacy bundle and skip a requested redraw. A record source.md no
# longer names is the operator's edit and is only skipped (lead 260925).
@pytest.mark.parametrize(
    "damage, count, problem",
    [
        ("missing", 2, FIGURE_RECORD_MISSING),
        ("corrupt", 2, FIGURE_RECORD_UNREADABLE),
        ("not-a-list", 2, FIGURE_RECORD_UNREADABLE),
        ("reference-without-record", 2, FIGURE_RECORD_UNLISTED.format(ids="p0001-02")),
    ],
)
def test_a_broken_figure_record_stops_before_anything_is_drawn(
    tmp_path, monkeypatch, damage, count, problem
):
    pdf = write_image_pdf(tmp_path / "photo.pdf", [PHOTO])
    bundle = drawn_bundle(
        tmp_path, pdf, [("p0001-01", PHOTO_BOX), ("p0001-02", PHOTO_BOX)]
    )
    policy = FigurePolicy("dpi", 144)
    pdf_figures.render_figures(bundle, pdf, policy)
    drawn = bundle.read_manifest()["figures"]
    path = pdf_figures.records_path(bundle)
    if damage == "missing":
        path.unlink()
    elif damage == "corrupt":
        path.write_text('[{"id": ', encoding="utf-8")
    elif damage == "not-a-list":
        path.write_text('{"id": "p0001-01"}', encoding="utf-8")
    else:
        pdf_figures.write_records(path, records(bundle)[:1])

    def never(*args, **kwargs):
        raise AssertionError("drawn despite a broken record")

    monkeypatch.setattr(pdf_figures, "_draw_all", never)
    monkeypatch.setattr(pdf_figures, "masked_sizes", never)
    # Another policy would redraw; the same one would reuse: both stop.
    for asked in (FigurePolicy("dpi", 200), policy):
        with pytest.raises(PipelineError) as caught:
            pdf_figures.render_figures(bundle, pdf, asked)
        assert str(caught.value) == FIGURE_RECORD_BROKEN.format(
            count=count, problem=problem
        )
    assert bundle.read_manifest()["figures"] == drawn


def test_a_figure_removed_from_source_md_is_skipped_with_one_line(tmp_path, capsys):
    pdf = write_image_pdf(tmp_path / "photo.pdf", [PHOTO])
    bundle = drawn_bundle(
        tmp_path, pdf, [("p0001-01", PHOTO_BOX), ("p0001-02", PHOTO_BOX)]
    )
    policy = FigurePolicy("dpi", 144)
    pdf_figures.render_figures(bundle, pdf, policy)
    removed = bundle.root / "assets/figures/p0001-02.png"
    kept = removed.read_bytes()
    text = bundle.source.read_text(encoding="utf-8")
    bundle.source.write_text(
        text.replace("![](assets/figures/p0001-02.png)\n", ""), encoding="utf-8"
    )
    line = FIGURES_UNREFERENCED.format(count=1, ids="p0001-02")
    capsys.readouterr()
    # The rest is drawn: the record set changed, so this run draws again.
    block = pdf_figures.render_figures(bundle, pdf, policy)
    out = capsys.readouterr().out
    assert out.count(line) == 1
    assert block["count"] == 1 and list(block["files"]) == ["p0001-01"]
    assert "Figures: 1 drawn at 144 DPI" in out
    assert removed.read_bytes() == kept
    # The next run reuses the drawing (the skipped record forces nothing)
    # and says the line again, once.
    block_again = pdf_figures.render_figures(bundle, pdf, policy)
    out = capsys.readouterr().out
    assert block_again == block
    assert out.count(line) == 1
    assert "Figures:" not in out
    assert removed.read_bytes() == kept


def test_the_skipped_figures_line_names_ten_and_counts_the_rest(tmp_path, capsys):
    pdf = write_image_pdf(tmp_path / "photo.pdf", [PHOTO])
    names = [f"p0001-{n:02d}" for n in range(1, 13)]
    bundle = drawn_bundle(tmp_path, pdf, [(name, PHOTO_BOX) for name in names])
    bundle.source.write_text("# No figures left\n", encoding="utf-8")
    capsys.readouterr()
    block = pdf_figures.render_figures(bundle, pdf, FIGURE_POLICY_DEFAULT)
    ids = ", ".join(names[:10]) + " and 2 more"
    out = capsys.readouterr().out
    assert out.count(FIGURES_UNREFERENCED.format(count=12, ids=ids)) == 1
    assert block["count"] == 0 and block["files"] == {}


def test_a_broken_record_names_ten_figures_and_counts_the_rest(tmp_path):
    pdf = write_image_pdf(tmp_path / "photo.pdf", [PHOTO])
    bundle = drawn_bundle(tmp_path, pdf, [])
    names = [f"p0001-{n:02d}" for n in range(1, 13)]
    bundle.source.write_text(
        "".join(f"![](assets/figures/{name}.png){{width=10%}}\n\n" for name in names),
        encoding="utf-8",
    )
    with pytest.raises(PipelineError) as caught:
        pdf_figures.render_figures(bundle, pdf, FIGURE_POLICY_DEFAULT)
    ids = ", ".join(names[:10]) + " and 2 more"
    assert str(caught.value) == FIGURE_RECORD_BROKEN.format(
        count=12, problem=FIGURE_RECORD_UNLISTED.format(ids=ids)
    )


def test_a_bundle_with_no_extraction_is_not_drawn(tmp_path, capsys):
    bundle = Bundle(tmp_path / "bundle").create()
    assert pdf_figures.render_figures(bundle, tmp_path / "x.pdf") is None
    assert capsys.readouterr().out == ""


def test_the_translation_identity_skips_only_the_drawn_figures(tmp_path):
    from book_maker.pipeline.translate import _translated_assets

    bundle = Bundle(tmp_path / "bundle").create()
    for name in (
        "figures/p0002-01.png",
        "figures/plate.png",
        "images/formula_p0001_000.png",
    ):
        path = bundle.assets / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    bundle.update_manifest(source={"kind": "pdf"})
    assert sorted(_translated_assets(bundle)) == [
        "assets/figures/plate.png",
        "assets/images/formula_p0001_000.png",
    ]
    # A Markdown import's own `figures/` directory is its content: hashed.
    bundle.update_manifest(source={"kind": "markdown"})
    assert "assets/figures/p0002-01.png" in _translated_assets(bundle)


def test_a_new_extraction_forgets_the_last_drawing(tmp_path, pandoc, device):
    bundle, pdf = extracted(tmp_path, pandoc)
    pdf_figures.render_figures(bundle, pdf, FigurePolicy("dpi", 144))
    assert "figures" in bundle.read_manifest()
    docling_parser.extract_pdf(
        bundle, pdf, pandoc=pandoc, convert=exporting(lambda: figure_document(TWO))
    )
    assert "figures" not in bundle.read_manifest()
    # The stable names land on their own names, not `-2` beside a drawing.
    names = sorted(p.name for p in (bundle.assets / "figures").iterdir())
    assert names == ["p0002-01.png", "p0002-02.png"]


# --------------------------------------------------------------------------
# The route: another policy redraws and rebuilds, and translates nothing
# --------------------------------------------------------------------------
def _epub_images(path):
    with zipfile.ZipFile(path) as archive:
        media = {
            name: archive.read(name)
            for name in archive.namelist()
            if name.startswith("EPUB/media/")
        }
        body = "".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".xhtml")
        )
    return media, body


def test_another_policy_redraws_the_figures_and_nothing_else(
    tmp_path, pandoc, device, monkeypatch, capsys
):
    _render_or_skip()
    from PIL import Image

    from book_maker.pipeline.to_epub import pdf_to_epub
    from book_maker.pipeline.translate import translation_fingerprint

    register_fake_format(monkeypatch)
    pdf = two_page_pdf(tmp_path)
    convert = exporting(lambda: figure_document(TWO))
    monkeypatch.setattr(docling_parser, "_convert", convert)

    def run(policy):
        FakeTranslator.instances = []
        capsys.readouterr()
        book = pdf_to_epub(pdf, list(OPTIONS), pandoc=pandoc, figure_policy=policy)
        return book, capsys.readouterr().out

    book, out = run(FigurePolicy("dpi", 72))
    assert "Figures: 2 drawn at 72 DPI" in out
    assert sum(len(t.translated) for t in FakeTranslator.instances) > 0
    bundle = Bundle(tmp_path / "paper_book")
    manifest = bundle.read_manifest()
    source = bundle.source.read_bytes()
    fingerprint = translation_fingerprint(bundle, OPTIONS)
    figure = bundle.assets / "figures" / "p0002-01.png"
    old_pixels = figure.read_bytes()
    old_media, body = _epub_images(book)
    assert old_pixels in old_media.values()
    # Pandoc writes the width into the book; epub.css only caps it at 100%.
    assert body.count('style="width:49.0%"') == 1
    assert body.count('style="width:25.0%"') == 1
    # PIN (packet Q, 260925): with an empty alt text Pandoc writes a plain
    # <img alt=""> in a paragraph -- docling's "Image" had become a hidden
    # <figcaption>Image</figcaption> under every figure.
    assert "<figcaption" not in body and 'alt="Image"' not in body
    assert body.count('class="bbm-figure" style="width:49.0%" alt=""') == 1

    book, out = run(FigurePolicy("dpi", 216))
    assert convert.calls == 1  # the extraction was reused
    assert (
        FIGURES_REDRAWN.format(policy="216 DPI", old="72 DPI") in out
        and "Figures: 2 drawn at 216 DPI" in out
    )
    assert sum(len(t.translated) for t in FakeTranslator.instances) == 0
    after = bundle.read_manifest()
    for key in ("source", "extraction", "translation"):
        assert after[key] == manifest[key], key
    assert after["stages"]["extract"] == manifest["stages"]["extract"]
    assert after["figures"]["policy"] == {"kind": "dpi", "value": 216}
    assert bundle.source.read_bytes() == source
    assert translation_fingerprint(bundle, OPTIONS) == fingerprint
    new_pixels = figure.read_bytes()
    assert new_pixels != old_pixels
    with Image.open(figure) as image:
        assert abs(image.width - 912) <= 1  # 304 points at 3 px per point
    # The book beside the PDF was rebuilt with the new pixels.
    new_media, body = _epub_images(book)
    assert new_pixels in new_media.values() and old_pixels not in new_media.values()
    assert body.count('style="width:49.0%"') == 1

    # The same policy again draws nothing.
    drawn = []
    monkeypatch.setattr(pdf_figures, "_draw", lambda *a, **k: drawn.append(a))
    book, out = run(FigurePolicy("dpi", 216))
    assert drawn == [] and "Figures:" not in out
    assert figure.read_bytes() == new_pixels


# --------------------------------------------------------------------------
# A real docling conversion, two pages
# --------------------------------------------------------------------------
def test_a_real_conversion_names_its_figures_whatever_the_policy(
    tmp_path, pandoc, monkeypatch, capsys
):
    pytest.importorskip("docling.document_converter")
    _render_or_skip()
    _render_or_skip()
    from PIL import Image

    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    pdf = write_pdf(
        tmp_path / "vector.pdf",
        ["A title line for page one.\nProse on page one.", "Prose on page two."],
        figure=(2, ["Label A", "Label B"]),
        figure_clip=False,
        figure_scale=1.2,
        figure_shown=2,
    )
    harness = load_harness()
    widths = {}
    sources = {}
    for policy in ("dpi:150", "dpi:300"):
        out = tmp_path / policy.replace(":", "")
        code = harness.main(
            [
                "extract",
                str(pdf),
                "--output",
                str(out),
                "--device",
                "cpu",
                "--figure-policy",
                policy,
            ]
        )
        assert code == 0, capsys.readouterr().out
        bundle = Bundle(out)
        source = bundle.source.read_text(encoding="utf-8")
        figures = records(bundle)
        assert figures, source  # docling found the vector figure
        refs = [line for line in source.splitlines() if "](assets/figures/" in line]
        assert len(refs) == len(figures)
        for record, ref in zip(figures, refs):
            assert ref == (f"![]({record['file']}){{width={record['width']}%}}")
        sources[policy] = bundle.source.read_bytes()
        with Image.open(bundle.root / figures[0]["file"]) as image:
            widths[policy] = image.width
    assert sources["dpi:150"] == sources["dpi:300"]
    assert abs(widths["dpi:300"] - 2 * widths["dpi:150"]) <= 2


def test_the_harness_export_redraws_only_when_asked(
    tmp_path, pandoc, device, monkeypatch, capsys
):
    from book_maker.pipeline.translate import translate_bundle

    register_fake_format(monkeypatch)
    bundle, pdf = extracted(tmp_path, pandoc)
    pdf_figures.render_figures(bundle, pdf, FigurePolicy("dpi", 72))
    translate_bundle(bundle, list(OPTIONS), pandoc=pandoc)
    harness = load_harness()
    figure = bundle.assets / "figures" / "p0002-01.png"
    drawn = figure.read_bytes()

    capsys.readouterr()
    assert harness.main(["export", str(bundle.root)]) == 0
    assert "Figures" not in capsys.readouterr().out
    assert figure.read_bytes() == drawn

    assert harness.main(["export", str(bundle.root), "--figure-policy", "dpi:144"]) == 0
    out = capsys.readouterr().out
    assert FIGURES_REDRAWN.format(policy="144 DPI", old="72 DPI") in out
    assert figure.read_bytes() != drawn
    media, _body = _epub_images(bundle.epub)
    assert figure.read_bytes() in media.values()


# --------------------------------------------------------------------------
# The main CLI's flag: --pdf-image-dpi N
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["71", "601", "0", "200.5", "many"])
def test_the_image_dpi_is_refused_outside_72_to_600(value, capsys):
    from book_maker.cli import parse_args

    with pytest.raises(SystemExit) as stopped:
        parse_args(["--book_name", "b.pdf", "--pdf-image-dpi", value])
    assert stopped.value.code == 2
    assert "must be a whole number from 72 to 600" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["72", "600"])
def test_the_image_dpi_bounds_are_accepted(value):
    from book_maker.cli import parse_args

    options = parse_args(["--book_name", "b.pdf", "--pdf-image-dpi", value])
    assert options.pdf_image_dpi == int(value)


def test_the_image_dpi_default_is_the_policy_constant():
    from book_maker.cli import parse_args

    assert parse_args(["--book_name", "b.pdf"]).pdf_image_dpi == (
        FIGURE_POLICY_DEFAULT.value
    )
    # PIN: owner 260925: physical PDF DPI, default 200;
    # docs/260925-eval-PDF_FIGURE_RESOLUTION_POLICIES.md
    assert FIGURE_POLICY_DEFAULT == FigurePolicy("dpi", 200)


@pytest.mark.parametrize(
    "typed, expected", [([], 200), (["--pdf-image-dpi", "300"], 300)]
)
def test_the_typed_dpi_reaches_the_route_as_a_dpi_policy(typed, expected, monkeypatch):
    from book_maker import cli
    from book_maker.pipeline import to_epub

    seen = {}

    def route(*args, **kwargs):
        seen.update(kwargs)
        return Path("x.epub")

    monkeypatch.setattr(to_epub, "pdf_to_epub", route)
    argv = ["--book_name", "b.pdf", "--to-epub"] + typed
    cli.run_to_epub(cli.parse_args(argv), argv)
    assert seen["figure_policy"] == FigurePolicy("dpi", expected)


def test_the_image_dpi_never_reaches_the_inner_run_or_the_identity():
    import dataclasses

    from book_maker.pipeline.to_epub import translation_argv
    from book_maker.pipeline.translate import option_identity, parse_bbm_options

    assert translation_argv(
        ["--pdf-image-dpi", "300", "--language", "ja", "--pdf-image-dpi=150"]
    ) == ["--language", "ja"]
    plain = parse_bbm_options(["--language", "ja"])
    typed = parse_bbm_options(["--language", "ja", "--pdf-image-dpi", "300"])
    assert typed.pdf_image_dpi == 300
    assert option_identity(typed) == option_identity(plain)
    fields = {field.name for field in dataclasses.fields(ExtractionSettings)}
    assert not any("dpi" in name or "figure" in name for name in fields)


def test_the_harness_takes_the_image_dpi_as_a_dpi_policy():
    parser = load_harness().build_parser()
    for command in (["extract", "x.pdf", "--output", "b"], ["export", "b"]):
        options = parser.parse_args(command + ["--pdf-image-dpi", "300"])
        assert options.figure_policy == FigurePolicy("dpi", 300)
    options = parser.parse_args(["extract", "x.pdf", "--output", "b"])
    assert options.figure_policy is FIGURE_POLICY_DEFAULT
    with pytest.raises(SystemExit):
        parser.parse_args(["export", "b", "--pdf-image-dpi", "601"])


def test_the_image_dpi_help_is_the_owner_s_text():
    # PIN: owner 260925: physical PDF DPI, default 200;
    # docs/260925-eval-PDF_FIGURE_RESOLUTION_POLICIES.md
    from book_maker.cli import build_parser
    from book_maker.pipeline.messages import HELP_PDF_IMAGE_DPI_CLI

    text = (
        "PDF only, with --to-epub: how sharp the figures are, in dots per "
        "inch of the PDF's own page size. Default 200: sharp on a tablet or "
        "a high-density e-reader; 150 for a smaller book, 300 for figures "
        "with tiny labels. Changing it on a rerun redraws the figures only; "
        "the extraction and the translation are kept. Formulas keep their "
        "own resolution."
    )
    assert HELP_PDF_IMAGE_DPI_CLI.format(default=200) == text
    assert text in " ".join(build_parser().format_help().split())


# --------------------------------------------------------------------------
# Centring: a figure alone in its paragraph, never an image in a sentence
# --------------------------------------------------------------------------
def test_an_image_alone_in_its_paragraph_is_centred_and_one_in_prose_is_not(
    tmp_path, monkeypatch
):
    """PIN (packet Q, 260925): figures lost their <figure> wrapper, and its
    centring, when their alt text went; the export's Lua filter classes an
    image that is its paragraph's only content and the shipped CSS centres
    that class. CSS alone cannot tell: `p > img:only-child` ignores text
    nodes, so it would also pull an image out of a sentence."""
    import re

    from pipeline_helpers import write_fixture

    from book_maker.pipeline.epub_export import export_epub
    from book_maker.pipeline.importer import import_markdown
    from book_maker.pipeline.translate import translate_bundle

    pandoc = pandoc_or_skip()
    register_fake_format(monkeypatch)
    book = write_fixture(
        tmp_path / "src",
        "# Chapter\n\n"
        "![](assets/plate.png){width=60%}\n\n"
        "A sentence with ![](assets/plate.png) inside it.\n",
    )
    bundle = Bundle(tmp_path / "bundle").create()
    import_markdown(bundle, book, pandoc=pandoc)
    translate_bundle(bundle, list(OPTIONS), pandoc=pandoc)
    export_epub(bundle, pandoc=pandoc)

    with zipfile.ZipFile(bundle.epub) as archive:
        names = archive.namelist()
        body = "".join(
            archive.read(n).decode("utf-8") for n in names if n.endswith(".xhtml")
        )
        css = "".join(
            archive.read(n).decode("utf-8") for n in names if n.endswith(".css")
        )
    rule = re.search(r"img\.bbm-figure\s*\{([^}]*)\}", css)
    assert rule, css
    declarations = " ".join(rule.group(1).split())
    for part in ("display: block;", "margin-left: auto;", "margin-right: auto;"):
        assert part in declarations
    # No width in the rule: the figure's own style decides its size.
    assert "width" not in declarations
    standalone = re.findall(r"<img[^>]*bbm-figure[^>]*>", body)
    assert len(standalone) == 1 and 'style="width:60.0%"' in standalone[0]
    inline = re.search(r"A sentence with (<img[^>]*>) inside it", body)
    assert inline and "bbm-figure" not in inline.group(1)


# --------------------------------------------------------------------------
# Codex astra follow-ups (260925, docs/260925-docs-PDF_IMAGE_FIDELITY_DESIGN.md):
# padding, the megapixel ceiling, a lone embedded picture's own resolution,
# the reuse check on the files themselves, a fallback that is not there.
# --------------------------------------------------------------------------
def test_the_padding_stops_short_of_a_caption_and_is_whole_elsewhere():
    box = [100.0, 100.0, 300.0, 300.0]
    caption = [100.0, 301.5, 300.0, 320.0]  # 1.5 pt below the figure
    crop = pdf_figures.figure_crop(box, LETTER, [caption])
    # 1 pt of clearance leaves 0.5 pt below; 2 pt on the other sides.
    assert crop == pytest.approx([98.0, 98.0, 302.0, 300.5])


def test_the_padding_is_clipped_to_the_page():
    crop = pdf_figures.figure_crop([0.0, 1.0, 612.0, 791.0], LETTER, [])
    assert crop == pytest.approx([0.0, 0.0, 612.0, 792.0])


def test_the_padding_ignores_what_lies_inside_the_figure():
    box = [100.0, 100.0, 300.0, 300.0]
    inside = [[110.0, 110.0, 150.0, 120.0], list(box)]  # a label, the picture
    crop = pdf_figures.figure_crop(box, LETTER, inside)
    assert crop == pytest.approx([98.0, 98.0, 302.0, 302.0])


def test_an_item_over_the_edge_leaves_no_padding_on_its_side():
    box = [100.0, 100.0, 300.0, 300.0]
    overlapping = [250.0, 150.0, 350.0, 160.0]  # reaches past the right edge
    beside = [301.5, 400.0, 320.0, 420.0]  # below-right, 100 pt down: far
    crop = pdf_figures.figure_crop(box, LETTER, [overlapping, beside])
    assert crop == pytest.approx([98.0, 98.0, 300.0, 302.0])


A3 = (841.89, 1190.55)


def drawn_bundle(tmp_path, pdf, figures):
    """A bundle whose extraction completed with `figures` recorded.

    Each figure is `(id, bbox)` on page 1, its crop as the extraction pads
    it (nothing else on the page), and a docling fallback picture on disk.
    """
    _render_or_skip()
    from PIL import Image

    bundle = Bundle(tmp_path / "bundle").create()
    bundle.set_stage("extract", "completed")
    images = bundle.work_file("extraction") / "images"
    images.mkdir(parents=True)
    records = []
    with pypdfium_page(pdf) as page:
        size = tuple(float(v) for v in page.get_size())
    for ident, bbox in figures:
        Image.new("RGB", (8, 8), "green").save(images / f"{ident}.png")
        records.append(
            {
                "id": ident,
                "page": 1,
                "bbox": list(bbox),
                "crop": pdf_figures.figure_crop(bbox, size, []),
                "file": f"assets/figures/{ident}.png",
                "width": pdf_figures.display_width(bbox, size[0]),
                "fallback": f".work/extraction/images/{ident}.png",
            }
        )
    pdf_figures.write_records(pdf_figures.records_path(bundle), records)
    bundle.source.write_text(
        "# Figures\n\n"
        + "".join(f"![](assets/figures/{ident}.png)\n" for ident, _ in figures),
        encoding="utf-8",
    )
    return bundle


class pypdfium_page:
    def __init__(self, pdf):
        import pypdfium2

        self.document = pypdfium2.PdfDocument(str(pdf))

    def __enter__(self):
        self.page = self.document[0]
        return self.page

    def __exit__(self, *exc):
        self.page.close()
        self.document.close()


def test_a_figure_past_the_megapixel_ceiling_is_drawn_below_it(
    tmp_path, monkeypatch, capsys
):
    _render_or_skip()
    import pypdfium2

    pdf = write_image_pdf(tmp_path / "a3.pdf", [], size=A3)
    bundle = drawn_bundle(tmp_path, pdf, [("p0001-01", (0.0, 0.0) + A3)])
    bitmaps = []
    render = pypdfium2.PdfPage.render

    def spy(self, **kwargs):
        bitmap = render(self, **kwargs)
        bitmaps.append((bitmap.width, bitmap.height))
        return bitmap

    monkeypatch.setattr(pypdfium2.PdfPage, "render", spy)
    capsys.readouterr()
    block = pdf_figures.render_figures(bundle, pdf, FigurePolicy("dpi", 200))
    out = capsys.readouterr().out
    # A full A3 page at 200 DPI is 2339 x 3307 px, 7.7 MP: never allocated.
    assert len(bitmaps) == 1
    width, height = bitmaps[0]
    assert width * height <= pdf_figures.FIGURE_MAX_PIXELS
    state = block["files"]["p0001-01"]
    assert state["requested_scale"] == pytest.approx(200 / 72)
    dpi = round(state["effective_scale"] * 72)
    assert 169 <= dpi <= 171
    line = FIGURE_CAPPED.format(
        id="p0001-01", page=1, mp=7.7, policy="200 DPI", dpi=dpi, cap=5.6
    )
    assert out.count(line) == 1, out
    # The shown width is the page's, whatever the pixels.
    assert pdf_figures.records_path(bundle).read_text().count('"width": 100') == 1


def test_the_ceiling_steps_down_until_the_rounded_bitmap_fits():
    for crop in [(841.89, 1190.55), (1000.3, 1000.7), (5000.0, 13.3)]:
        scale = pdf_figures.capped_scale(*crop, 600 / 72)
        w, h = pdf_figures.pixel_size(*crop, scale)
        assert w * h <= pdf_figures.FIGURE_MAX_PIXELS
    # Under the ceiling nothing changes.
    assert pdf_figures.capped_scale(300.0, 200.0, 200 / 72) == 200 / 72


# One 96 x 72 px picture placed at 72 x 54 pt: 96 DPI. Its box on a Letter
# page, top-left origin: [100, 138, 172, 192].
PHOTO = (72, 0, 0, 54, 100, 600)
PHOTO_BOX = (100.0, 138.0, 172.0, 192.0)


@pytest.mark.parametrize(
    "shape, native",
    [
        ({"placements": [PHOTO]}, 96.0),
        ({"placements": [PHOTO], "label": (110, 620, "Label")}, None),
        ({"placements": [(0, 54, -72, 0, 172, 600)]}, None),  # rotated
        ({"placements": [(36, 0, 0, 54, 100, 600), (36, 0, 0, 54, 136, 600)]}, None),
        ({"placements": [PHOTO], "smask": True}, None),
        ({"placements": [PHOTO], "rotate": 90}, None),
        ({"placements": [PHOTO], "empty_form": (130, 640)}, 96.0),
        ({"placements": [PHOTO], "state_smask": True}, None),
        ({"placements": [PHOTO], "clip": "100 600 m 172 600 l 136 654 l h"}, None),
        ({"placements": [PHOTO], "clip": "100.5 600.5 71 53 re"}, 96.0),
    ],
    ids=[
        "alone",
        "text-over-it",
        "rotated",
        "two-rasters",
        "soft-mask",
        "page-turned",
        "empty-anchor",
        "graphics-state-soft-mask",
        "clipped",
        "rectangle-clip",
    ],
)
def test_only_a_lone_plain_picture_keeps_its_own_resolution(
    tmp_path, capsys, shape, native
):
    _render_or_skip()
    from PIL import Image

    pdf = write_image_pdf(tmp_path / "photo.pdf", **shape)
    bundle = drawn_bundle(tmp_path, pdf, [("p0001-01", PHOTO_BOX)])
    capsys.readouterr()
    block = pdf_figures.render_figures(bundle, pdf, FigurePolicy("dpi", 200))
    out = capsys.readouterr().out
    state = block["files"]["p0001-01"]
    record = json.loads(pdf_figures.records_path(bundle).read_text())[0]
    crop_w = record["crop"][2] - record["crop"][0]
    with Image.open(bundle.root / record["file"]) as image:
        drawn = image.width
    note = FIGURES_NATIVE.format(count=1, policy="200 DPI")
    if native is None:
        assert "native_dpi" not in state
        assert state["effective_scale"] == pytest.approx(200 / 72)
        assert note not in out
        if "rotate" not in shape:
            assert drawn == round(crop_w * 200 / 72)
    else:
        assert state["native_dpi"] == native
        assert state["effective_scale"] == pytest.approx(native / 72)
        assert drawn == round(crop_w * native / 72)
        # Right after the drawn line, once.
        drawn_line = out.splitlines().index(
            FIGURES_DRAWN.format(
                count=1, policy="200 DPI", size=pdf_figures.human_size(block["bytes"])
            )
        )
        assert out.splitlines()[drawn_line + 1] == note
        assert out.count(note) == 1


def test_a_picture_sharper_than_the_policy_is_drawn_at_the_policy(tmp_path, capsys):
    _render_or_skip()
    pdf = write_image_pdf(tmp_path / "photo.pdf", [PHOTO], pixels=(400, 300))
    bundle = drawn_bundle(tmp_path, pdf, [("p0001-01", PHOTO_BOX)])
    block = pdf_figures.render_figures(bundle, pdf, FigurePolicy("dpi", 200))
    assert "native_dpi" not in block["files"]["p0001-01"]
    assert "kept at the resolution" not in capsys.readouterr().out


def test_a_deleted_figure_file_is_drawn_again(tmp_path, pandoc, device, capsys):
    bundle, pdf = extracted(tmp_path, pandoc)
    policy = FigurePolicy("dpi", 144)
    pdf_figures.render_figures(bundle, pdf, policy)
    capsys.readouterr()
    assert pdf_figures.render_figures(bundle, pdf, policy) is not None
    assert "Figures:" not in capsys.readouterr().out  # all in place: reused
    target = bundle.root / records(bundle)[1]["file"]
    target.unlink()
    pdf_figures.render_figures(bundle, pdf, policy)
    assert "Figures: 2 drawn at 144 DPI" in capsys.readouterr().out
    assert target.is_file()


def test_a_replaced_figure_file_is_drawn_again(tmp_path, pandoc, device, capsys):
    bundle, pdf = extracted(tmp_path, pandoc)
    policy = FigurePolicy("dpi", 144)
    pdf_figures.render_figures(bundle, pdf, policy)
    target = bundle.root / records(bundle)[0]["file"]
    drawn = target.read_bytes()
    target.write_bytes(drawn[:-1] + b"\x00")  # same size, other bytes
    capsys.readouterr()
    pdf_figures.render_figures(bundle, pdf, policy)
    assert "Figures: 2 drawn at 144 DPI" in capsys.readouterr().out
    assert target.read_bytes() == drawn


def test_an_edited_record_is_drawn_again(tmp_path, pandoc, device, capsys):
    bundle, pdf = extracted(tmp_path, pandoc)
    policy = FigurePolicy("dpi", 144)
    pdf_figures.render_figures(bundle, pdf, policy)
    figures = records(bundle)
    figures[0]["crop"] = figures[0]["bbox"]
    pdf_figures.write_records(pdf_figures.records_path(bundle), figures)
    capsys.readouterr()
    block = pdf_figures.render_figures(bundle, pdf, policy)
    assert "Figures: 2 drawn at 144 DPI" in capsys.readouterr().out
    assert block["records_sha256"] == pdf_figures.records_digest(records(bundle))
    assert block["files"]["p0002-01"]["size"] > 0


def test_a_failed_figure_is_tried_again_on_every_run(
    tmp_path, pandoc, device, capsys, monkeypatch
):
    bundle, pdf = extracted(tmp_path, pandoc)
    real = pdf_figures._draw

    def failing(page, record, policy, destination, **kwargs):
        if record["id"] == "p0002-02":
            raise RuntimeError("injected")
        return real(page, record, policy, destination, **kwargs)

    monkeypatch.setattr(pdf_figures, "_draw", failing)
    policy = FigurePolicy("dpi", 144)
    line = FIGURE_RENDER_FAILED.format(
        id="p0002-02", page=2, policy="144 DPI", err="RuntimeError: injected"
    )
    for _run in range(2):
        capsys.readouterr()
        block = pdf_figures.render_figures(bundle, pdf, policy)
        assert capsys.readouterr().out.count(line) == 1
        assert block["failed"] == ["p0002-02"]
        assert bundle.read_manifest()["limitations"].count(line) == 1
    monkeypatch.setattr(pdf_figures, "_draw", real)
    block = pdf_figures.render_figures(bundle, pdf, policy)
    assert block["failed"] == []
    assert line not in bundle.read_manifest()["limitations"]


@pytest.mark.parametrize("damage", ["missing", "unreadable"])
def test_a_figure_with_no_fallback_stops_the_run(
    tmp_path, pandoc, device, monkeypatch, damage
):
    bundle, pdf = extracted(tmp_path, pandoc)
    record = records(bundle)[1]
    fallback = bundle.root / record["fallback"]
    if damage == "missing":
        fallback.unlink()
    else:
        fallback.write_bytes(b"not a picture")
    real = pdf_figures._draw

    def failing(page, record, policy, destination, **kwargs):
        if record["id"] == "p0002-02":
            raise RuntimeError("injected")
        return real(page, record, policy, destination, **kwargs)

    monkeypatch.setattr(pdf_figures, "_draw", failing)
    with pytest.raises(PipelineError) as caught:
        pdf_figures.render_figures(bundle, pdf, FigurePolicy("dpi", 144))
    assert str(caught.value) == FIGURE_FALLBACK_MISSING.format(
        id="p0002-02", page=2, err="RuntimeError: injected", path=fallback
    )
    # Nothing claims the drawing: the next run draws again.
    assert "figures" not in bundle.read_manifest()


def test_a_figure_file_is_replaced_whole_or_not_at_all(tmp_path):
    target = tmp_path / "figures" / "p0001-01.png"
    target.parent.mkdir()
    target.write_bytes(b"old")

    def broken(partial):
        partial.write_bytes(b"half")
        raise OSError("disk full")

    with pytest.raises(OSError):
        pdf_figures._replace(target, broken)
    assert target.read_bytes() == b"old"
    assert [p.name for p in target.parent.iterdir()] == ["p0001-01.png"]
    pdf_figures._replace(target, lambda partial: partial.write_bytes(b"new"))
    assert target.read_bytes() == b"new"
    assert [p.name for p in target.parent.iterdir()] == ["p0001-01.png"]


def test_the_drawing_revision_is_two():
    # Bumped once for padding, the ceiling and the native path, so a bundle
    # drawn by revision 1 is drawn again once.
    assert pdf_figures.FIGURE_REVISION == 2
    assert pdf_figures.FIGURE_MAX_PIXELS == 5_600_000
    assert pdf_figures.FIGURE_PAD_PT == 2.0


@pytest.mark.parametrize(
    "box, native",
    [
        ((98.5, 138.0, 172.0, 192.0), 96.0),  # docling 1.5 pt loose: the picture
        ((90.0, 138.0, 172.0, 192.0), None),  # 10 pt of the box it does not fill
    ],
)
def test_the_picture_must_fill_the_detected_box(tmp_path, box, native):
    _render_or_skip()
    pdf = write_image_pdf(tmp_path / "photo.pdf", [PHOTO])
    bundle = drawn_bundle(tmp_path, pdf, [("p0001-01", box)])
    block = pdf_figures.render_figures(bundle, pdf, FigurePolicy("dpi", 200))
    assert block["files"]["p0001-01"].get("native_dpi") == native


def test_pdfium_s_graphics_state_refuses_a_soft_mask_the_scan_cannot_see(
    tmp_path, monkeypatch
):
    # An ExtGState in a compressed object stream never reaches the byte
    # scan; pdfium's own reading of the picture's graphics state does.
    _render_or_skip()
    pdf = write_image_pdf(tmp_path / "photo.pdf", [PHOTO], state_smask=True)
    assert pdf_figures.masked_sizes(pdf) is None
    monkeypatch.setattr(pdf_figures, "masked_sizes", lambda path: set())
    bundle = drawn_bundle(tmp_path, pdf, [("p0001-01", PHOTO_BOX)])
    block = pdf_figures.render_figures(bundle, pdf, FigurePolicy("dpi", 200))
    assert "native_dpi" not in block["files"]["p0001-01"]


def test_a_clip_pdfium_cannot_report_trusts_no_picture(tmp_path, monkeypatch):
    _render_or_skip()
    import pypdfium2.raw as raw

    pdf = write_image_pdf(tmp_path / "photo.pdf", [PHOTO])
    bundle = drawn_bundle(tmp_path, pdf, [("p0001-01", PHOTO_BOX)])
    monkeypatch.delattr(raw, "FPDFPageObj_GetClipPath")
    block = pdf_figures.render_figures(bundle, pdf, FigurePolicy("dpi", 200))
    assert "native_dpi" not in block["files"]["p0001-01"]


def test_a_bundle_without_figures_never_reads_the_pdf(tmp_path, monkeypatch):
    pdf = write_image_pdf(tmp_path / "photo.pdf", [PHOTO])
    bundle = drawn_bundle(tmp_path, pdf, [])

    def never(*args, **kwargs):
        raise AssertionError("the PDF was scanned with no figure to draw")

    monkeypatch.setattr(pdf_figures, "masked_sizes", never)
    block = pdf_figures.render_figures(bundle, pdf, FigurePolicy("dpi", 200))
    assert block["count"] == 0 and block["files"] == {}


def test_the_mask_scan_reads_in_pieces_and_finds_a_dictionary_across_two(tmp_path):
    pdf = write_image_pdf(tmp_path / "photo.pdf", [PHOTO], smask=True)
    data = pdf.read_bytes()
    # The piece boundary falls inside the masked picture's `/SMask` key.
    boundary = data.index(b"/SMask") + 3
    assert pdf_figures.masked_sizes(pdf) == {(96, 72)}
    assert pdf_figures.masked_sizes(pdf, chunk=boundary, overlap=1024) == {(96, 72)}
    # Without the overlap the split dictionary is seen by neither piece.
    assert pdf_figures.masked_sizes(pdf, chunk=boundary, overlap=0) == set()


def test_the_mask_scan_never_reads_the_whole_file_at_once(tmp_path, monkeypatch):
    pdf = write_image_pdf(tmp_path / "photo.pdf", [PHOTO], smask=True)
    monkeypatch.setattr(
        Path, "read_bytes", lambda self: pytest.fail("the PDF was read whole")
    )
    reads = []
    real_open = open

    class Spy:
        def __init__(self, handle):
            self.handle = handle

        def read(self, size=-1):
            reads.append(size)
            return self.handle.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

    monkeypatch.setattr(
        "builtins.open", lambda path, mode="r", *a, **k: Spy(real_open(path, mode))
    )
    assert pdf_figures.masked_sizes(pdf, chunk=4096, overlap=1024) == {(96, 72)}
    assert reads and all(0 < size <= 4096 for size in reads)


@pytest.mark.parametrize(
    "hide",
    [
        lambda line: "<!-- " + line.strip() + " -->\n",
        lambda line: "```\n" + line + "```\n",
        lambda line: line.replace("![]", "[a link]"),
    ],
    ids=["commented-out", "in-a-code-block", "a-plain-link"],
)
def test_a_figure_the_book_does_not_show_is_not_drawn(tmp_path, capsys, hide):
    # PIN: Codex re-verify 260925 (docs/260925-feat-PDF_FIGURE_RENDER.md):
    # only a Markdown image names a figure; a comment, a fenced block or a
    # plain link does not.
    pdf = write_image_pdf(tmp_path / "photo.pdf", [PHOTO])
    bundle = drawn_bundle(
        tmp_path, pdf, [("p0001-01", PHOTO_BOX), ("p0001-02", PHOTO_BOX)]
    )
    text = bundle.source.read_text(encoding="utf-8")
    line = next(
        row + "\n" for row in text.splitlines() if "assets/figures/p0001-02.png" in row
    )
    bundle.source.write_text(text.replace(line, hide(line)), encoding="utf-8")
    capsys.readouterr()
    block = pdf_figures.render_figures(bundle, pdf, FigurePolicy("dpi", 144))
    out = capsys.readouterr().out
    assert list(block["files"]) == ["p0001-01"]
    assert out.count(FIGURES_UNREFERENCED.format(count=1, ids="p0001-02")) == 1


def test_shown_markdown_keeps_prose_and_drops_comments_and_fences():
    text = "a\n<!-- ![](x) -->\nb\n```\n![](y)\n```\nc ![](z)\n"
    assert pdf_figures.shown_markdown(text) == "a\n\nb\n\nc ![](z)\n"
