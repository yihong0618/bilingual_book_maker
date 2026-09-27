"""House style for the wiki figures (scientific-figure-making skill).

Every figure is written as a lossy JPEG at quality 85 (owner rule,
2026-09-23); the pages reference the .jpg files in docs/img/ only.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

OUT = Path(__file__).resolve().parent.parent

PALETTE = {
    "blue_main": "#0F4D92",
    "blue_secondary": "#3775BA",
    "green": "#8BCF8B",
    "red_strong": "#B64342",
    "red_soft": "#E9A6A1",
    "teal": "#42949E",
    "violet": "#9A4D8E",
    "neutral": "#CFCECE",
}


def apply():
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans", "sans-serif"],
            "font.size": 15,
            "axes.linewidth": 2,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "legend.frameon": False,
            "xtick.major.width": 2,
            "ytick.major.width": 2,
        }
    )


def save(fig, name):
    path = OUT / f"{name}.jpg"
    fig.tight_layout()
    fig.savefig(path, format="jpg", dpi=200, pil_kwargs={"quality": 85})
    plt.close(fig)
    print(f"wrote {path.relative_to(OUT.parent.parent)}")
