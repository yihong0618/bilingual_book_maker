"""The PDF route end to end, as the CI `pdf-route` job runs it.

A two-page typed PDF built by the test helper goes through the harness's
three stages -- extract (docling, CPU, no OCR), translate (the fixed
stand-in translator, no model and no key), export (the real Pandoc) --
and the EPUB is opened and read back. Only the model is fixed.

Marked `pdf_route_ci` and deselected from a bare `pytest` (pyproject's
addopts): docling downloads its layout models on the first run, which is
the CI job's business, not every suite run's. The job asks for it by
name: `pytest -p no:cacheprovider -m pdf_route_ci tests/test_ci_pdf_route_smoke.py`.
"""

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import (  # noqa: E402
    FakeTranslator,
    pandoc_or_skip,
    register_fake_format,
    write_pdf,
)

from book_maker.pipeline.bundle import Bundle  # noqa: E402

pytestmark = pytest.mark.pdf_route_ci

HARNESS = Path(__file__).resolve().parent.parent / "tools" / "pdf_to_book.py"

PAGES = (
    "The first page of a typed PDF, with a full sentence of prose on it.",
    "The second page of it, also a sentence long.",
)


def load_harness():
    spec = importlib.util.spec_from_file_location("pdf_to_book", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_two_page_pdf_extracts_translates_and_exports(tmp_path, monkeypatch):
    pytest.importorskip("docling")
    pytest.importorskip("pypdfium2")
    pandoc = pandoc_or_skip()
    fake = register_fake_format(monkeypatch)

    pdf = write_pdf(tmp_path / "ci_smoke.pdf", pages=PAGES)
    book = tmp_path / "ci_smoke_book"
    harness = load_harness()

    def stage(*argv):
        code = harness.main(["--pandoc", pandoc, *argv])
        assert code == 0, f"harness {argv[0]} exited {code}"

    stage(
        "extract", str(pdf), "--output", str(book), "--pages", "1-2", "--device", "cpu"
    )
    source = (book / "source.md").read_text(encoding="utf-8")
    assert "first page" in source and "second page" in source

    stage("translate", str(book), "--", "--api_format", fake, "--language", "zh-hans")
    sent = [text for i in FakeTranslator.instances for text in i.translated]
    assert any("first page" in text for text in sent), sent
    assert any("second page" in text for text in sent), sent

    stage("export", str(book))
    epub = Bundle(book).epub
    assert epub.is_file(), f"no EPUB at {epub}"

    with zipfile.ZipFile(epub) as archive:
        names = archive.namelist()
        nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
        documents = [n for n in names if n.endswith(".xhtml") and "nav" not in n]
        body = "".join(archive.read(name).decode("utf-8") for name in documents)

    # The route opens page 1 with `# <pdf stem>` (heading_for_top); the nav
    # lists it, and the translation is not a second contents entry.
    assert nav.count('<a href="text/ch') == 1, nav
    assert ">ci_smoke<" in nav, nav

    # Both phrases of the extraction reached the book, each followed by the
    # stand-in's deterministic translation of the same paragraph.
    assert "first page" in body and "second page" in body
    for text in PAGES:
        original = body.find(text)
        assert original != -1, f"{text!r} is not in the EPUB"
        translated = body.find(f"译:{text}", original)
        assert translated != -1, f"no translation of {text!r} in the EPUB"
        between = body[original + len(text) : translated]
        assert '<div class="bbm-translation"' in between, between
        # Adjacent: no other source paragraph sits between the pair.
        assert all(other not in between for other in PAGES)
