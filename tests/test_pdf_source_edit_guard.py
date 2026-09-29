"""An edited bundle `source.md` is never replaced by extracting or importing again.

PIN (lead 260928, astra consult, docs/260928 unit A record): an existing
`bundle/source.md` may be replaced only when it still matches its recorded
baseline, `source.working_sha256`. A run that would extract or import again
over an edited one is refused before anything in the bundle is touched --
"Nothing was changed" is a promise the tests hold byte for byte over the
whole bundle directory. A source with no baseline on record is unknown, not
untouched. A rerun with the settings the bundle was made with reuses it and
keeps the edit; `translate` and `export` never ask. No override flag.

docling is never imported except by the one real-extraction test at the end,
which skips without it: the conversion is replaced at its seam
(`docling_parser._convert`), as are the device probe and the text-layer
reader, so the decision table runs on a base install.
"""

import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import (  # noqa: E402
    FakeTranslator,
    pandoc_or_skip,
    register_fake_format,
    write_fixture,
    write_pdf,
)

from book_maker.pipeline import docling_parser, pdf_common, stages  # noqa: E402
from book_maker.pipeline.bundle import Bundle  # noqa: E402
from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.pipeline.messages import (  # noqa: E402
    DEVICE_SELECTED,
    SOURCE_BASELINE_UNKNOWN,
    SOURCE_EDITED,
    SOURCE_REASON_EXTRACT,
    SOURCE_REASON_IMPORT,
    SOURCE_REASON_PAGES,
    SOURCE_REASON_PARSER,
    SOURCE_REASON_PDF,
    SOURCE_REASON_SETTINGS,
    SOURCE_REASON_STRUCTURE,
    STAGE_COMPLETE,
)

HARNESS = Path(__file__).resolve().parent.parent / "tools" / "pdf_to_book.py"
TRANSLATE = ["--api_format", "faketest", "--language", "zh-hans"]
EDIT = "\nA sentence the operator added by hand.\n"

BREAK = docling_parser.PAGE_BREAK
CONVERTED = (
    "# Chapter One\n\nThe first paragraph of prose.\n"
    f"{BREAK}\n## Notes\n\nA closing paragraph on page two.\n"
)


def load_harness():
    import importlib.util

    spec = importlib.util.spec_from_file_location("pdf_to_book", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(root):
    """Every file under the bundle, with its bytes: what 'nothing' means."""
    root = Path(root)
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def edit(bundle):
    with bundle.source.open("a", encoding="utf-8") as handle:
        handle.write(EDIT)
    return bundle.source.read_text(encoding="utf-8")


def refusal(template, bundle, reason):
    return template.format(bundle=shlex.quote(str(bundle.root)), reason=reason)


@pytest.fixture
def pandoc():
    return pandoc_or_skip()


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF-1.7\n%fake\n")
    return path


@pytest.fixture
def fake_docling(monkeypatch):
    """The device, the text layer and the conversion, without docling."""
    state = {"calls": 0, "missing": [], "markdown": CONVERTED}

    def resolve(requested):
        return "cpu", DEVICE_SELECTED.format(device="cpu")

    def report(pdf_path, page_range=None):
        return pdf_common.TextLayerReport(list(state["missing"]), 2, [])

    def convert(pdf_path, **kwargs):
        state["calls"] += 1
        return state["markdown"]

    monkeypatch.setattr(docling_parser, "resolve_device", resolve)
    monkeypatch.setattr(docling_parser, "text_layer_report", report)
    monkeypatch.setattr(docling_parser, "_convert", convert)
    return state


def harness_extract(pandoc, pdf, bundle_root, *extra):
    return load_harness().main(
        ["--pandoc", pandoc, "extract", str(pdf), "--output", str(bundle_root), *extra]
    )


def extracted(tmp_path, pandoc, pdf, fake_docling, *extra):
    root = tmp_path / "bundle"
    assert harness_extract(pandoc, pdf, root, *extra) == 0
    assert fake_docling["calls"] == 1
    return Bundle(root)


# --------------------------------------------------------------------------
# The refusal, one reason per check, through the harness
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "first, second, reason",
    [
        ((), ("--pages", "1"), SOURCE_REASON_PAGES),
        (("--pages", "1-2"), ("--pages", "1"), SOURCE_REASON_PAGES),
        ((), ("--no-formula-images",), SOURCE_REASON_SETTINGS),
    ],
    ids=["pages-added", "pages-changed", "formula-images-toggled"],
)
def test_an_edited_source_is_refused_and_nothing_is_changed(
    tmp_path, pandoc, pdf, fake_docling, capsys, first, second, reason
):
    bundle = extracted(tmp_path, pandoc, pdf, fake_docling, *first)
    edited = edit(bundle)
    before = snapshot(bundle.root)
    assert "source.md" in before and "source.raw.md" in before
    assert "manifest.json" in before
    capsys.readouterr()

    code = harness_extract(pandoc, pdf, bundle.root, *second)

    out = capsys.readouterr().out.replace("\n", "")
    assert code == 1
    assert refusal(SOURCE_EDITED, bundle, reason) in out
    assert fake_docling["calls"] == 1, "the refused run reached the parser"
    # Nothing was changed: source.md, the raw source, the manifest (stages,
    # limitations, figure records) and every asset, byte for byte.
    assert snapshot(bundle.root) == before
    assert bundle.source.read_text(encoding="utf-8") == edited


def test_a_different_pdf_under_the_same_bundle_is_refused(
    tmp_path, pandoc, pdf, fake_docling, capsys
):
    bundle = extracted(tmp_path, pandoc, pdf, fake_docling)
    edit(bundle)
    before = snapshot(bundle.root)
    pdf.write_bytes(b"%PDF-1.7\n%another book\n")
    capsys.readouterr()

    assert harness_extract(pandoc, pdf, bundle.root) == 1

    out = capsys.readouterr().out.replace("\n", "")
    assert refusal(SOURCE_EDITED, bundle, SOURCE_REASON_PDF) in out
    assert fake_docling["calls"] == 1
    assert snapshot(bundle.root) == before


def test_a_bundle_another_parser_wrote_is_refused(
    tmp_path, pandoc, pdf, fake_docling, capsys
):
    bundle = extracted(tmp_path, pandoc, pdf, fake_docling)
    bundle.update_manifest(extraction={"provider": "opendataloader"})
    edit(bundle)
    before = snapshot(bundle.root)
    capsys.readouterr()

    assert harness_extract(pandoc, pdf, bundle.root) == 1

    out = capsys.readouterr().out.replace("\n", "")
    assert refusal(SOURCE_EDITED, bundle, SOURCE_REASON_PARSER) in out
    assert snapshot(bundle.root) == before


def test_an_unfinished_structure_pass_asked_for_again_is_refused(
    tmp_path, pandoc, pdf, fake_docling, capsys
):
    """The structure retry is a re-extraction like any other."""
    bundle = extracted(tmp_path, pandoc, pdf, fake_docling)
    bundle.update_manifest(
        extraction={
            "structure": "m",
            "structure_rev": "r1",
            "structure_status": "partial",
        }
    )
    edit(bundle)
    before = snapshot(bundle.root)

    with pytest.raises(PipelineError) as refused:
        stages.prepare(
            bundle,
            pdf,
            pandoc=pandoc,
            progress=False,
            structure=SimpleNamespace(model="m", rev="r1", base=None),
        )

    reason = SOURCE_REASON_STRUCTURE.format(status="partial")
    assert refused.value.detail == refusal(SOURCE_EDITED, bundle, reason)
    assert refused.value.stage == "extract"
    assert fake_docling["calls"] == 1
    assert snapshot(bundle.root) == before


def test_a_failed_extraction_does_not_lift_the_protection(
    tmp_path, pandoc, pdf, fake_docling, capsys
):
    """No stage status is consulted: a failed rerun leaves the source as it
    was, the operator edits it, and the next extraction must not take it."""
    bundle = extracted(tmp_path, pandoc, pdf, fake_docling)
    # an unedited rerun that fails (a page with no text layer, OCR off)
    fake_docling["missing"] = [2]
    assert harness_extract(pandoc, pdf, bundle.root, "--pages", "1-2") == 1
    assert bundle.stage_status("extract") == "failed"
    fake_docling["missing"] = []
    edit(bundle)
    before = snapshot(bundle.root)
    capsys.readouterr()

    assert harness_extract(pandoc, pdf, bundle.root) == 1

    out = capsys.readouterr().out.replace("\n", "")
    assert refusal(SOURCE_EDITED, bundle, SOURCE_REASON_EXTRACT) in out
    assert fake_docling["calls"] == 1
    assert snapshot(bundle.root) == before


# --------------------------------------------------------------------------
# Unknown baseline
# --------------------------------------------------------------------------
def _forget_baseline(bundle):
    manifest = bundle.read_manifest()
    del manifest["source"]["working_sha256"]
    bundle.write_manifest(manifest)


@pytest.mark.parametrize("edited", [True, False], ids=["edited", "unedited"])
def test_no_recorded_baseline_refuses_a_replacement(
    tmp_path, pandoc, pdf, fake_docling, capsys, edited
):
    bundle = extracted(tmp_path, pandoc, pdf, fake_docling)
    _forget_baseline(bundle)
    if edited:
        edit(bundle)
    before = snapshot(bundle.root)
    capsys.readouterr()

    assert harness_extract(pandoc, pdf, bundle.root, "--no-formula-images") == 1

    out = capsys.readouterr().out.replace("\n", "")
    assert refusal(SOURCE_BASELINE_UNKNOWN, bundle, SOURCE_REASON_SETTINGS) in out
    assert fake_docling["calls"] == 1
    assert snapshot(bundle.root) == before


def test_no_recorded_baseline_still_reuses_with_the_same_settings(
    tmp_path, pandoc, pdf, fake_docling, capsys
):
    bundle = extracted(tmp_path, pandoc, pdf, fake_docling)
    _forget_baseline(bundle)
    edited = edit(bundle)
    capsys.readouterr()

    assert harness_extract(pandoc, pdf, bundle.root) == 0

    assert STAGE_COMPLETE.format(stage="extract") in capsys.readouterr().out
    assert fake_docling["calls"] == 1
    assert bundle.source.read_text(encoding="utf-8") == edited


# --------------------------------------------------------------------------
# What must keep working
# --------------------------------------------------------------------------
def test_the_same_settings_reuse_the_edited_source(
    tmp_path, pandoc, pdf, fake_docling, capsys
):
    bundle = extracted(tmp_path, pandoc, pdf, fake_docling, "--pages", "1-2")
    edited = edit(bundle)
    capsys.readouterr()

    assert harness_extract(pandoc, pdf, bundle.root, "--pages", "1-2") == 0

    assert STAGE_COMPLETE.format(stage="extract") in capsys.readouterr().out
    assert fake_docling["calls"] == 1
    assert bundle.stage_status("extract") == "completed"
    assert bundle.source.read_text(encoding="utf-8") == edited


def test_an_untouched_source_is_extracted_again_as_before(
    tmp_path, pandoc, pdf, fake_docling
):
    bundle = extracted(tmp_path, pandoc, pdf, fake_docling)
    fake_docling["markdown"] = CONVERTED.replace("first paragraph", "new paragraph")

    assert harness_extract(pandoc, pdf, bundle.root, "--no-formula-images") == 0

    assert fake_docling["calls"] == 2
    assert "new paragraph" in bundle.source.read_text(encoding="utf-8")
    extraction = bundle.read_manifest()["extraction"]
    assert extraction["formula_images"] is False
    # and the new text is the new baseline: an edit to it is protected too
    edit(bundle)
    assert harness_extract(pandoc, pdf, bundle.root) == 1
    assert fake_docling["calls"] == 2


def test_translate_and_export_are_not_guarded(
    tmp_path, pandoc, pdf, fake_docling, monkeypatch
):
    """The way out the refusal names: translate the bundle as it is."""
    register_fake_format(monkeypatch)
    bundle = extracted(tmp_path, pandoc, pdf, fake_docling)
    _forget_baseline(bundle)  # unknown baseline: still no business of theirs
    edit(bundle)
    harness = load_harness()

    assert (
        harness.main(
            ["--pandoc", pandoc, "translate", str(bundle.root), "--", *TRANSLATE]
        )
        == 0
    )
    assert "added by hand" in bundle.bilingual_markdown.read_text(encoding="utf-8")
    assert FakeTranslator.instances
    assert harness.main(["--pandoc", pandoc, "export", str(bundle.root)]) == 0
    assert bundle.epub.is_file()
    assert fake_docling["calls"] == 1


# --------------------------------------------------------------------------
# Markdown imports and the main CLI's route
# --------------------------------------------------------------------------
def test_a_direct_import_over_an_edited_bundle_is_refused(tmp_path, pandoc, capsys):
    book = write_fixture(tmp_path / "in")
    root = tmp_path / "bundle"
    harness = load_harness()
    command = ["--pandoc", pandoc, "import", str(book), "--output", str(root)]
    assert harness.main(command) == 0
    bundle = Bundle(root)
    # an unedited bundle may be imported again
    assert harness.main(command) == 0
    edit(bundle)
    before = snapshot(root)
    capsys.readouterr()

    assert harness.main(command) == 1

    out = capsys.readouterr().out.replace("\n", "")
    assert refusal(SOURCE_EDITED, bundle, SOURCE_REASON_IMPORT) in out
    assert snapshot(root) == before


def test_an_import_the_preflight_refused_can_be_imported_again(tmp_path, pandoc):
    """The baseline is recorded when source.md is written, not only on
    success: a refused import left the tool's own text, which a corrected
    import may replace."""
    harness = load_harness()
    root = tmp_path / "bundle"
    bad = write_fixture(
        tmp_path / "bad", '# Title\n\nProse.\n\n<img src="assets/plate.png">\n'
    )
    command = ["--pandoc", pandoc, "import", str(bad), "--output", str(root)]
    assert harness.main(command) == 1
    assert Bundle(root).source.is_file()
    good = write_fixture(tmp_path / "good")
    assert (
        harness.main(["--pandoc", pandoc, "import", str(good), "--output", str(root)])
        == 0
    )
    assert "Chapter One" in Bundle(root).source.read_text(encoding="utf-8")


def test_the_main_cli_route_is_guarded_too(
    tmp_path, pandoc, pdf, fake_docling, monkeypatch
):
    from book_maker.pipeline.to_epub import bundle_path, pdf_to_epub

    register_fake_format(monkeypatch)
    pdf_to_epub(pdf, list(TRANSLATE), pandoc=pandoc)
    bundle = Bundle(bundle_path(pdf))
    edit(bundle)
    before = snapshot(bundle.root)

    with pytest.raises(PipelineError) as refused:
        pdf_to_epub(pdf, list(TRANSLATE), pandoc=pandoc, formula_images=False)

    assert refused.value.detail == refusal(
        SOURCE_EDITED, bundle, SOURCE_REASON_SETTINGS
    )
    assert fake_docling["calls"] == 1
    assert snapshot(bundle.root) == before


# --------------------------------------------------------------------------
# A real extraction
# --------------------------------------------------------------------------
def test_a_real_extraction_is_protected_the_same_way(tmp_path, pandoc, capsys):
    pytest.importorskip("docling")
    pytest.importorskip("pypdfium2")
    pdf = write_pdf(
        tmp_path / "paper.pdf", ["A line of prose on page one.", "Page two prose."]
    )
    root = tmp_path / "bundle"
    assert harness_extract(pandoc, pdf, root, "--device", "cpu", "--pages", "1-2") == 0
    bundle = Bundle(root)
    edited = edit(bundle)
    before = snapshot(root)
    capsys.readouterr()

    code = harness_extract(pandoc, pdf, root, "--device", "cpu", "--pages", "1")

    out = capsys.readouterr().out.replace("\n", "")
    assert code == 1
    assert refusal(SOURCE_EDITED, bundle, SOURCE_REASON_PAGES) in out
    assert snapshot(root) == before

    # the same settings reuse it, edit and all
    assert harness_extract(pandoc, pdf, root, "--device", "cpu", "--pages", "1-2") == 0
    assert STAGE_COMPLETE.format(stage="extract") in capsys.readouterr().out
    assert bundle.source.read_text(encoding="utf-8") == edited
