"""`--to-epub` with the hosted engine, and with Markdown handed in.

Datalab is the alternative to docling's local models (owner 261026): the
route sends the PDF there and never loads docling's pass, its device, OCR
or figure drawing. A Markdown file -- one Datalab wrote earlier -- goes
through the import instead. Nothing here starts an engine, a model or
Pandoc; the stages are stand-ins that record what they were handed.
"""

from pathlib import Path

import pytest

from book_maker import cli
from book_maker.pipeline.pdf_figures import FigurePolicy
from book_maker.pipeline import docling_parser, to_epub
from book_maker.pipeline.errors import PipelineError

TRANSLATION = ["--api_format", "google", "--language", "zh-hans"]


@pytest.fixture(autouse=True)
def no_pandoc_lookup(monkeypatch):
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF-1.7\n%fake\n")
    return path


@pytest.fixture
def no_docling_pass(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("docling's image pass was loaded")

    monkeypatch.setattr(docling_parser, "image_request", refuse)


def stages(seen):
    def prepare_stage(bundle, source, **kwargs):
        seen.append(("prepare", Path(source).suffix, kwargs.get("parser")))
        bundle.source.write_text("# Title\n\nProse.\n", encoding="utf-8")

    def figure_stage(bundle, source, policy):
        seen.append(("figures",))

    def translate_stage(bundle, options, *, pandoc):
        seen.append(("translate",))
        bundle.bilingual_markdown.write_text("# Title\n", encoding="utf-8")

    def export_stage(bundle, *, pandoc):
        seen.append(("export",))
        bundle.epub.write_bytes(b"PK\x03\x04")
        return bundle.epub

    return {
        "prepare_stage": prepare_stage,
        "figure_stage": figure_stage,
        "translate_stage": translate_stage,
        "export_stage": export_stage,
    }


def test_datalab_reaches_the_extract_stage_and_docling_is_never_loaded(
    pdf, no_docling_pass
):
    seen = []
    # A provider entry would normally resolve docling's image model.
    to_epub.pdf_to_epub(
        pdf,
        [*TRANSLATION, "--provider", "openai"],
        pdf_parser="datalab",
        **stages(seen),
    )
    assert seen[0] == ("prepare", ".pdf", "datalab")
    assert (pdf.parent / "book_bilingual.epub").is_file()


@pytest.mark.parametrize(
    "typed, flag",
    [
        ({"pdf_ocr": True}, "--pdf-ocr"),
        ({"device": "cpu"}, "--device"),
        ({"ocr_lang": "iso:zh"}, "--ocr-lang"),
        ({"img_model": "gpt-5.6-luna"}, "--img-model"),
        ({"formula_images": False}, "--no-formula-images"),
        ({"ocr_engine": "rapidocr"}, "--ocr-engine"),
        ({"figure_policy": FigurePolicy("dpi", 300)}, "--pdf-image-dpi"),
    ],
)
def test_docling_options_with_datalab_are_refused_before_anything_runs(
    pdf, typed, flag
):
    seen = []
    with pytest.raises(PipelineError) as refused:
        to_epub.pdf_to_epub(
            pdf, TRANSLATION, pdf_parser="datalab", **typed, **stages(seen)
        )
    assert flag in refused.value.detail
    assert seen == []


def test_markdown_is_imported_and_no_engine_runs(tmp_path, no_docling_pass):
    book = tmp_path / "datalab.md"
    book.write_text("# Title\n\nProse.\n", encoding="utf-8")
    seen = []
    result = to_epub.pdf_to_epub(book, TRANSLATION, **stages(seen))
    assert seen[0] == ("prepare", ".md", None)
    assert result == tmp_path / "datalab_bilingual.epub"
    assert (tmp_path / "datalab_book" / "source.md").is_file()


@pytest.mark.parametrize(
    "typed",
    [
        {"pages": "1-2"},
        {"pdf_ocr": True},
        {"device": "cpu"},
        {"figure_policy": FigurePolicy("dpi", 300)},
    ],
)
def test_pdf_options_with_markdown_are_refused(tmp_path, typed):
    book = tmp_path / "datalab.md"
    book.write_text("# Title\n", encoding="utf-8")
    seen = []
    with pytest.raises(PipelineError):
        to_epub.pdf_to_epub(book, TRANSLATION, **typed, **stages(seen))
    assert seen == []


def test_the_cli_takes_markdown_to_the_route_with_the_engine_typed(
    tmp_path, monkeypatch
):
    book = tmp_path / "datalab.md"
    book.write_text("# Title\n", encoding="utf-8")
    seen = {}

    def fake(path, argv, **kwargs):
        seen.update(path=Path(path), parser=kwargs["pdf_parser"])

    monkeypatch.setattr(to_epub, "pdf_to_epub", fake)
    cli.main(["--book_name", str(book), "--to-epub", *TRANSLATION])
    assert seen == {"path": book, "parser": "docling"}

    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    cli.main(
        ["--book_name", str(pdf), "--to-epub", "--pdf-parser", "datalab", *TRANSLATION]
    )
    assert seen == {"path": pdf, "parser": "datalab"}


def test_the_engine_flag_is_the_route_s_own_and_never_reaches_the_inner_run():
    kept = to_epub.translation_argv(
        ["--to-epub", "--pdf-parser", "datalab", "--model", "m", "--batch-api"]
    )
    assert kept == ["--model", "m", "--batch-api"]


def test_the_harness_figure_policy_is_refused_with_datalab():
    """The harness keeps `--pdf-image-dpi` as `figure_policy`; it slipped past
    the check and a paid conversion was sent for a setting Datalab ignores."""
    from types import SimpleNamespace

    from book_maker.pipeline import stages

    options = SimpleNamespace(
        pdf_parser="datalab", figure_policy=FigurePolicy("dpi", 300)
    )
    with pytest.raises(PipelineError) as refused:
        stages.check_pdf_options("pdf", options)
    assert "--pdf-image-dpi" in refused.value.detail
    default = SimpleNamespace(
        pdf_parser="datalab", figure_policy=FigurePolicy("dpi", 200)
    )
    stages.check_pdf_options("pdf", default)
