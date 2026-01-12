import subprocess
import sys
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")


def test_pdf_cli_creates_txt_and_optional_epub(tmp_path):
    pdf_path = tmp_path / "cli_test.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "CLI test\nPDF content")
    doc.save(str(pdf_path))

    # run CLI
    subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            str(pdf_path),
            "--test",
            "--test_num",
            "5",
            "--model",
            "google",
        ],
        check=True,
    )

    txt_out = tmp_path / "cli_test_bilingual.txt"
    assert txt_out.exists()
    assert txt_out.stat().st_size > 0

    # if ebooklib is installed, an epub should be created
    try:
        import ebooklib
    except Exception:
        ebooklib = None

    if ebooklib is not None:
        epub_out = tmp_path / "cli_test_bilingual.epub"
        assert epub_out.exists()
        assert epub_out.stat().st_size > 0
