"""Exact heading levels on 20 arXiv papers (195 headings), four methods.

Numbers copied from docs/260922-eval-DOCLING_HEADING_HIERARCHY_BENCHMARK.md
and docs/260922-feat-PDF_HEADING_LEVELS.md.
Run from the repository root: python docs/img/src/pdf_heading_levels.py
"""

import matplotlib.pyplot as plt

from _style import PALETTE, apply, save

ARMS = [
    ("docling\nHeadingHierarchyModel", 48, PALETTE["red_soft"]),
    ("docling as is\n(all ##)", 83, PALETTE["neutral"]),
    ("docling + ODL\nstyle rank", 163, PALETTE["blue_secondary"]),
    ("numbering first,\nthen style (shipped)", 187, PALETTE["blue_main"]),
]
TOTAL = 195


def main():
    apply()
    fig, ax = plt.subplots(figsize=(10, 5))
    names = [a[0] for a in ARMS]
    exact = [a[1] for a in ARMS]
    bars = ax.bar(
        names, exact, color=[a[2] for a in ARMS], edgecolor="black", linewidth=1.5
    )
    for bar, n in zip(bars, exact):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            n + 3,
            f"{n}/{TOTAL}",
            ha="center",
            fontsize=14,
        )
    ax.set_ylim(0, 215)
    ax.set_ylabel("headings at the exact level")
    ax.tick_params(axis="x", labelsize=12)
    save(fig, "pdf-heading-levels")


if __name__ == "__main__":
    main()
