"""Convert the legacy figures (kept as sources in docs/img/src/) to JPEG.

The wiki references JPEG only (owner rule, 2026-09-23): every image is a
lossy JPEG at quality 85. These four figures came from the September 2026
grouping and session evaluations as WebP; their plotting scripts live with
the raw artifacts outside the repository, so the WebP files are the
sources here. pdf_reading_edition.webp stays in docs/img/ because the
README links it; its JPEG copy is what the wiki uses. output_style is the
--translation_style screenshot the old cmd.md hot-linked from GitHub; its
PNG is fetched from that URL rather than stored (1.6 MB).

Run from the repository root:  python docs/img/src/convert_legacy.py
"""

import io
import urllib.request
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE.parent
SOURCES = {
    HERE / "compression_ratio.webp": OUT / "compression_ratio.jpg",
    HERE / "fault-emergence.webp": OUT / "fault-emergence.jpg",
    HERE / "retry_overhead_vs_units.webp": OUT / "retry_overhead_vs_units.jpg",
    HERE / "session-cost-curves.webp": OUT / "session-cost-curves.jpg",
    OUT / "pdf_reading_edition.webp": OUT / "pdf_reading_edition.jpg",
    "https://user-images.githubusercontent.com/89069008/"
    "226104545-7c029bb1-5325-46d4-a1eb-ec4e7bbaee97.png": OUT / "output_style.jpg",
}


def to_jpeg(src, dst):
    if isinstance(src, str):
        with urllib.request.urlopen(src, timeout=60) as reply:
            image = Image.open(io.BytesIO(reply.read()))
        name = "output_style.png (" + src.split("/")[2] + ")"
        # a 3010 px screenshot; 1600 px is plenty for a page column
        image.thumbnail((1600, 1600 * image.height // image.width))
    else:
        image = Image.open(src)
        name = src.name
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        flat = Image.new("RGB", image.size, "white")
        flat.paste(image, mask=image.split()[-1])
        image = flat
    else:
        image = image.convert("RGB")
    image.save(dst, format="JPEG", quality=85)
    print(f"{name} -> {dst.relative_to(OUT.parent.parent)} {image.size}")


if __name__ == "__main__":
    for src, dst in SOURCES.items():
        to_jpeg(src, dst)
