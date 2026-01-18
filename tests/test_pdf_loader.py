import os
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from book_maker.loader.pdf_loader import PDFBookLoader


class DummyModel:
    def __init__(
        self,
        key,
        language,
        api_base=None,
        temperature=1.0,
        source_lang="auto",
        **kwargs,
    ):
        pass

    def translate(self, text):
        return f"<T>{text}"

    def translate_list(self, texts):
        return [f"<T>{t}" for t in texts]


def test_pdf_loader_extracts_and_translates(tmp_path):
    pdf_path = tmp_path / "test.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello world\nThis is a PDF test")
    doc.save(str(pdf_path))

    loader = PDFBookLoader(
        str(pdf_path),
        DummyModel,
        key="",
        resume=False,
        language="en",
        is_test=True,
        test_num=5,
    )

    assert len(loader.origin_book) > 0

    loader.make_bilingual_book()

    out_file = tmp_path / "test_bilingual.txt"
    assert out_file.exists()
    assert out_file.stat().st_size > 0
    # basic content check
    content = out_file.read_text(encoding="utf-8")
    assert "<T>" in content

    # if ebooklib is installed, an EPUB should also be produced
    try:
        import ebooklib
    except Exception:
        ebooklib = None

    if ebooklib is not None:
        epub_file = tmp_path / "test_bilingual.epub"
        assert epub_file.exists()
        assert epub_file.stat().st_size > 0
