import os
import subprocess
import sys
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

# Same offline stand-in test_cli.py uses: tests/hermetic/sitecustomize.py
# swaps the `google` translator at interpreter startup. This test is about
# what the CLI writes for a PDF input, not about translation quality, and
# routing it through the public endpoint made it fail whenever that endpoint
# rate-limited the run — the flake the retry wrapper exists to paper over.
HERMETIC = Path(__file__).resolve().parent / "hermetic"


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(HERMETIC), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return env


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
        env=_env(),
    )

    txt_out = tmp_path / "cli_test_bilingual.txt"
    assert txt_out.exists()
    assert txt_out.stat().st_size > 0
    # the stand-in's marker: proof the CLI wrote a *translation*, which a
    # size check alone never established
    assert "[offline]" in txt_out.read_text(encoding="utf-8")

    # if ebooklib is installed, an epub should be created
    try:
        import ebooklib
    except Exception:
        ebooklib = None

    if ebooklib is not None:
        epub_out = tmp_path / "cli_test_bilingual.epub"
        assert epub_out.exists()
        assert epub_out.stat().st_size > 0
