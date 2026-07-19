from pathlib import Path
from tempfile import TemporaryDirectory

from book_maker.loader.pdf_loader import create_bilingual_pdf


def _run_check(tmp_dir):
    pairs = [
        ("Hello world\nThis is the original.", "你好，世界\n这是译文。"),
        (
            "A longer English line that should wrap inside the PDF column.",
            "一行较长的中文译文，应当在 PDF 栏内自动换行。",
        ),
    ]
    tmp_dir = Path(tmp_dir)
    for layout in ("top-bottom", "side-by-side"):
        out_path = tmp_dir / f"{layout}.pdf"
        assert create_bilingual_pdf(pairs, out_path, "PDF layout test", layout)
        assert out_path.exists()
        assert out_path.stat().st_size > 0


def test_pdf_layouts(tmp_path):
    _run_check(tmp_path)


if __name__ == "__main__":
    with TemporaryDirectory() as tmp_dir:
        _run_check(tmp_dir)
