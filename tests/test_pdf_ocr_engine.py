"""`--ocr-engine`: the OCR engine for the PDF route, chosen by name.

PIN (packet K, owner brief 260924: "we should expose that to the user";
measured guide docs/features/pdf-ocr-engines.md, numbers in
docs/evaluation/pdf-ocr-engines.md): the flag is `auto` by default and
names one of `pdf_settings.OCR_ENGINES`; the help text is the lead's,
verbatim; an engine this install cannot run (not importable, tesseract not
on PATH, ocrmac off macOS) is refused before any page is read, with the
line that installs it; `auto` is never refused. The engine is already
extraction identity (`ExtractionSettings.ocr_engine`): a rerun with another
engine extracts again (tests/test_docling_adapter.py
`test_a_changed_setting_is_not_answered_from_the_bundle`), and the manifest
records it as `ocr_engine_requested` (`test_an_engine_asked_for_by_name_is_
the_engine` there). What is tested here is the flag reaching that value.

No model is loaded: the extraction is replaced where it is called.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import pandoc_or_skip  # noqa: E402

from book_maker import cli  # noqa: E402
from book_maker.pipeline import (  # noqa: E402
    docling_parser,
    pdf_settings,
    stages,
    to_epub,
)
from book_maker.pipeline.bundle import Bundle  # noqa: E402
from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.pipeline.messages import (  # noqa: E402
    HELP_OCR_ENGINE,
    HELP_OCR_ENGINE_CLI,
    OCR_ENGINE_INSTALL,
    OCR_ENGINE_MISSING,
    OCR_ENGINE_NOT_MACOS,
    PDF_OPTIONS_INERT,
)
from book_maker.pipeline.pdf_settings import (  # noqa: E402
    OCR_ENGINES,
    check_ocr_engine,
)

ROOT = Path(__file__).resolve().parent.parent
TRANSLATION = ["--api_format", "google", "--language", "zh-hans"]

# The lead's text (packet K; the rapidocr sentence revised in packet N,
# 260925; revised again in packet P: onnxruntime and ocrmac (darwin) in the
# extra, owner 250925, docs/250925-feat-PDF_EXTRA_OCR_RUNTIMES.md), verbatim:
# not to be reworded.
LEAD_HELP = (
    "PDF only, with --to-epub --pdf-ocr: the OCR engine for pages with no text "
    "layer (every page with --ocr-replace-layer). auto (default) takes the "
    "first installed of ocrmac, rapidocr, easyocr. The pdf extra installs "
    "rapidocr with onnxruntime, models included, and on macOS also ocrmac "
    "(Apple's Vision framework); neither downloads anything, so auto reads "
    "with ocrmac on a Mac and rapidocr elsewhere. easyocr downloads its models "
    "on first use (pip install easyocr). tesseract uses the tesseract program "
    "and its language data from PATH. Language codes differ by engine; see "
    "--ocr-lang. The run names the engine it used."
)


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF-1.7\n%fake\n")
    return path


@pytest.fixture
def installed(monkeypatch):
    """Which engines this install has: `installed(*modules, tesseract=...)`.

    Module lookups go through `importlib.util.find_spec`, which the check
    uses (it must not import an engine: easyocr starts torch); the program
    lookup through `shutil.which`. The platform is macOS unless set.
    """
    real = importlib.util.find_spec

    def setup(*modules, tesseract=False, platform="darwin"):
        engine_modules = {
            m for group in pdf_settings.ENGINE_MODULES.values() for m in group
        }

        def find_spec(name, *args, **kwargs):
            if name in engine_modules:
                return object() if name in modules else None
            return real(name, *args, **kwargs)

        monkeypatch.setattr(pdf_settings.importlib.util, "find_spec", find_spec)
        monkeypatch.setattr(
            pdf_settings.shutil,
            "which",
            lambda name: (
                "/usr/bin/tesseract" if tesseract and name == "tesseract" else None
            ),
        )
        monkeypatch.setattr(pdf_settings.sys, "platform", platform)

    return setup


# ----------------------------------------------------------------- parser
def _parse(*argv):
    return cli.build_parser().parse_args(["--book_name", "b.pdf", *argv])


def test_the_default_is_auto():
    assert _parse().ocr_engine == "auto"


def test_the_choices_are_the_settings_engines():
    assert OCR_ENGINES == ("auto", "rapidocr", "easyocr", "ocrmac", "tesseract")
    for engine in OCR_ENGINES:
        assert _parse("--ocr-engine", engine).ocr_engine == engine


def test_an_unknown_engine_is_an_argparse_error(capsys):
    with pytest.raises(SystemExit) as exited:
        _parse("--ocr-engine", "paddle")
    assert exited.value.code == 2
    assert "invalid choice: 'paddle'" in capsys.readouterr().err


def test_the_help_is_the_lead_s_text_verbatim():
    assert HELP_OCR_ENGINE_CLI == LEAD_HELP
    # the harness has no --to-epub; the rest of the sentence is the same
    assert HELP_OCR_ENGINE == "With --pdf-ocr: " + LEAD_HELP.split(": ", 1)[1]


def test_help_renders_the_flag():
    proc = subprocess.run(
        [sys.executable, "make_book.py", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--ocr-engine {auto,rapidocr,easyocr,ocrmac,tesseract}" in proc.stdout
    help_text = " ".join(proc.stdout.split())
    assert "The run names the engine it used." in help_text


# ----------------------------------------------------------------- check
@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_auto_is_never_refused(installed, platform):
    installed(platform=platform)  # nothing at all
    check_ocr_engine("auto")
    check_ocr_engine(None)


@pytest.mark.parametrize(
    "engine,modules,tesseract",
    [
        ("rapidocr", ("rapidocr", "onnxruntime"), False),
        ("easyocr", ("easyocr",), False),
        ("ocrmac", ("ocrmac",), False),
        ("tesseract", (), True),
    ],
)
def test_an_installed_engine_is_accepted(installed, engine, modules, tesseract):
    installed(*modules, tesseract=tesseract)
    check_ocr_engine(engine)


@pytest.mark.parametrize(
    "engine,modules,install_words",
    [
        ("easyocr", (), "pip install easyocr"),
        ("ocrmac", (), "pip install ocrmac"),
        ("rapidocr", (), "pip install rapidocr onnxruntime"),
        # rapidocr runs on onnxruntime: the package alone is not enough
        ("rapidocr", ("rapidocr",), "pip install rapidocr onnxruntime"),
        ("tesseract", ("easyocr", "ocrmac", "rapidocr", "onnxruntime"), "PATH"),
    ],
)
def test_a_missing_engine_is_refused_with_its_install_line(
    installed, engine, modules, install_words
):
    installed(*modules, tesseract=False)
    with pytest.raises(PipelineError) as refused:
        check_ocr_engine(engine)
    detail = refused.value.detail
    assert detail == OCR_ENGINE_MISSING.format(
        engine=engine, install=OCR_ENGINE_INSTALL[engine]
    )
    assert f"--ocr-engine {engine}" in detail
    assert install_words in detail


def test_the_rapidocr_install_line_is_the_lead_s_text_verbatim():
    # PIN (lead, packet N, 260925): the extra brings rapidocr; the line names
    # onnxruntime too, because a named rapidocr runs on it. Packet P:
    # onnxruntime and ocrmac (darwin) in the extra, owner 250925,
    # docs/250925-feat-PDF_EXTRA_OCR_RUNTIMES.md.
    assert OCR_ENGINE_INSTALL["rapidocr"] == (
        "The pdf extra brings it with onnxruntime; if it is missing here, "
        'reinstall the extra (pip install ".[pdf]") or pip install rapidocr '
        "onnxruntime."
    )


def test_the_ocrmac_install_line_is_the_lead_s_text_verbatim():
    # PIN (lead, packet P): onnxruntime and ocrmac (darwin) in the extra,
    # owner 250925, docs/250925-feat-PDF_EXTRA_OCR_RUNTIMES.md.
    assert OCR_ENGINE_INSTALL["ocrmac"] == (
        "The pdf extra brings it on macOS; if it is missing here: pip install "
        "ocrmac (nothing to download)."
    )


def test_the_tesseract_line_says_language_data_and_path(installed):
    installed()
    with pytest.raises(PipelineError) as refused:
        check_ocr_engine("tesseract")
    assert (
        "Install tesseract and its language data, then put it on PATH."
        in refused.value.detail
    )


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_ocrmac_off_macos_is_refused_even_when_importable(installed, platform):
    installed("ocrmac", platform=platform)
    with pytest.raises(PipelineError) as refused:
        check_ocr_engine("ocrmac")
    assert refused.value.detail == OCR_ENGINE_NOT_MACOS


def test_the_check_imports_no_engine(installed, monkeypatch):
    # find_spec only: importing easyocr would start torch before a page
    installed("easyocr")
    before = set(sys.modules)
    check_ocr_engine("easyocr")
    assert "easyocr" not in set(sys.modules) - before


# ---------------------------------------------------------------- main CLI
def _route(pdf, monkeypatch, *flags):
    seen = {}

    def fake(path, argv, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(to_epub, "pdf_to_epub", fake)
    cli.main(["--book_name", str(pdf), "--to-epub", *flags, *TRANSLATION])
    return seen


def test_the_route_is_handed_the_engine(pdf, monkeypatch):
    seen = _route(pdf, monkeypatch, "--pdf-ocr", "--ocr-engine", "ocrmac")
    assert seen["ocr_engine"] == "ocrmac"
    assert seen["pdf_ocr"] is True


def test_the_route_is_handed_auto_by_default(pdf, monkeypatch):
    assert _route(pdf, monkeypatch, "--pdf-ocr")["ocr_engine"] == "auto"


@pytest.fixture
def no_pandoc_lookup(monkeypatch):
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")


def _fake_stages(handed):
    def prepare_stage(bundle, source, **kwargs):
        handed.append(kwargs)
        bundle.source.write_text("# Title\n\nProse.\n", encoding="utf-8")

    def translate_stage(bundle, options, *, pandoc):
        bundle.bilingual_markdown.write_text("# Title\n", encoding="utf-8")

    def export_stage(bundle, *, pandoc):
        bundle.epub.write_bytes(b"PK\x03\x04 not really a zip")
        return bundle.epub

    return {
        "prepare_stage": prepare_stage,
        "translate_stage": translate_stage,
        "export_stage": export_stage,
    }


def test_the_pipeline_hands_a_named_engine_to_the_extraction(
    pdf, installed, no_pandoc_lookup
):
    installed("easyocr")
    handed = []
    to_epub.pdf_to_epub(
        pdf,
        ["--book_name", str(pdf), "--to-epub", *TRANSLATION],
        pdf_ocr=True,
        ocr_engine="easyocr",
        **_fake_stages(handed),
    )
    assert handed[0]["ocr_engine"] == "easyocr"


def test_the_pipeline_leaves_auto_to_the_stage_default(pdf, no_pandoc_lookup):
    handed = []
    to_epub.pdf_to_epub(
        pdf,
        ["--book_name", str(pdf), "--to-epub", *TRANSLATION],
        pdf_ocr=True,
        **_fake_stages(handed),
    )
    assert "ocr_engine" not in handed[0]


def test_a_missing_engine_is_refused_before_anything_is_read(
    pdf, installed, no_pandoc_lookup
):
    installed()  # easyocr not installed
    handed = []
    with pytest.raises(PipelineError) as refused:
        to_epub.pdf_to_epub(
            pdf,
            ["--book_name", str(pdf), "--to-epub", *TRANSLATION],
            pdf_ocr=True,
            ocr_engine="easyocr",
            **_fake_stages(handed),
        )
    assert "pip install easyocr" in refused.value.detail
    assert handed == []
    # not even the bundle directory: nothing was started
    assert not to_epub.bundle_path(pdf).exists()


def test_the_cli_prints_the_refusal_as_one_line(
    pdf, installed, no_pandoc_lookup, monkeypatch, capsys
):
    installed()
    monkeypatch.setattr(
        docling_parser,
        "extract_pdf",
        lambda *a, **k: pytest.fail("a page was read with a missing engine"),
    )
    with pytest.raises(SystemExit) as stopped:
        cli.main(
            [
                "--book_name",
                str(pdf),
                "--to-epub",
                "--pdf-ocr",
                "--ocr-engine",
                "tesseract",
                *TRANSLATION,
            ]
        )
    assert stopped.value.code == 1
    out = " ".join(capsys.readouterr().out.split())
    assert "--ocr-engine tesseract was asked for, but it is not installed here." in out


def test_without_ocr_the_engine_is_not_checked(pdf, installed, no_pandoc_lookup):
    # no OCR runs, so nothing uses the engine: C36 says so off the route,
    # and a missing engine is no reason to stop a run that never needs it
    installed()
    handed = []
    to_epub.pdf_to_epub(
        pdf,
        ["--book_name", str(pdf), "--to-epub", *TRANSLATION],
        pdf_ocr=False,
        ocr_engine="easyocr",
        **_fake_stages(handed),
    )
    assert len(handed) == 1


# ------------------------------------------------------------------ stages
def test_the_stage_builds_the_setting_from_the_engine(tmp_path, pdf, monkeypatch):
    seen = []

    def record(bundle, path, *, settings, **kwargs):
        seen.append(settings)

    monkeypatch.setattr(docling_parser, "extract_pdf", record)
    for engine in ("tesseract", "auto"):
        stages.prepare(
            Bundle(tmp_path / engine).create(),
            pdf,
            pandoc="pandoc",
            ocr=True,
            ocr_engine=engine,
        )
    assert [s.ocr_engine for s in seen] == ["tesseract", "auto"]
    # identity, so a rerun with another engine extracts again: that half is
    # test_docling_adapter.py's test_a_changed_setting_is_not_answered_from_
    # the_bundle, over a real extraction's manifest
    assert seen[0].identity()["ocr_engine"] == "tesseract"


# ------------------------------------------------------------------ harness
def _harness():
    path = ROOT / "tools" / "pdf_to_book.py"
    spec = importlib.util.spec_from_file_location("pdf_to_book", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_harness_takes_the_engine_to_the_extraction(
    tmp_path, pdf, monkeypatch, installed
):
    pandoc = pandoc_or_skip()
    installed("ocrmac")
    seen = []

    def record(bundle, path, *, settings, **kwargs):
        seen.append(settings)
        raise PipelineError("stopped before the models", stage="extract")

    monkeypatch.setattr(docling_parser, "extract_pdf", record)
    code = _harness().main(
        [
            "--pandoc",
            pandoc,
            "extract",
            str(pdf),
            "--output",
            str(tmp_path / "b"),
            "--pdf-ocr",
            "--ocr-engine",
            "ocrmac",
        ]
    )
    assert code == 1
    assert [s.ocr_engine for s in seen] == ["ocrmac"]


def test_the_harness_refuses_a_missing_engine_before_extracting(
    tmp_path, pdf, monkeypatch, installed, capsys
):
    pandoc = pandoc_or_skip()
    installed()
    monkeypatch.setattr(
        docling_parser,
        "extract_pdf",
        lambda *a, **k: pytest.fail("extracted with a missing engine"),
    )
    code = _harness().main(
        [
            "--pandoc",
            pandoc,
            "extract",
            str(pdf),
            "--output",
            str(tmp_path / "b"),
            "--pdf-ocr",
            "--ocr-engine",
            "easyocr",
        ]
    )
    assert code == 1
    assert "pip install easyocr" in " ".join(capsys.readouterr().out.split())
    assert not (tmp_path / "b").exists()


def test_the_harness_help_and_choices():
    parser = _harness().build_parser()
    options = parser.parse_args(["extract", "x.pdf", "--output", "b"])
    assert options.ocr_engine == "auto"
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["extract", "x.pdf", "--output", "b", "--ocr-engine", "paddle"]
        )


def test_the_engine_on_markdown_is_refused_as_a_pdf_option():
    options = type("Options", (), {"ocr_engine": "ocrmac", "pdf_ocr": False})()
    with pytest.raises(PipelineError) as refused:
        stages.check_pdf_options("markdown", options)
    assert refused.value.detail == PDF_OPTIONS_INERT
    assert "--ocr-engine" in PDF_OPTIONS_INERT


def test_auto_on_markdown_is_no_pdf_option():
    options = type("Options", (), {"ocr_engine": "auto"})()
    stages.check_pdf_options("markdown", options)
