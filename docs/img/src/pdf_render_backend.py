"""CER by page-render backend on the scanned slices.

Numbers copied from docs/260923-eval-PDF_BACKEND_JBIG2_MASK_RENDER.md
(CER charspan: docling-parse, pypdfium2, hybrid).
Run from the repository root: python docs/img/src/pdf_render_backend.py
"""

import matplotlib.pyplot as plt
import numpy as np

from _style import PALETTE, apply, save

PAGES = ["innerspace", "zh", "hekate", "cia p1", "cia p2", "deg2", "hant2"]
PARSE = [0.0146, 0.3828, 0.0795, 0.1635, 0.2589, 0.4292, 0.1053]
PDFIUM = [0.0112, 0.6512, 0.0220, 0.1042, 0.2417, 0.4567, 0.1128]
HYBRID = [0.0142, 0.1119, 0.1598, 0.1042, 0.2417, 0.4567, 0.1128]


def main():
    apply()
    fig, ax = plt.subplots(figsize=(12, 5))
    x = np.arange(len(PAGES))
    w = 0.27
    ax.bar(
        x - w,
        PARSE,
        w,
        color=PALETTE["neutral"],
        edgecolor="black",
        linewidth=1.2,
        label="docling-parse",
    )
    ax.bar(
        x,
        PDFIUM,
        w,
        color=PALETTE["red_soft"],
        edgecolor="black",
        linewidth=1.2,
        label="pypdfium2",
    )
    ax.bar(
        x + w,
        HYBRID,
        w,
        color=PALETTE["blue_main"],
        edgecolor="black",
        linewidth=1.2,
        label="hybrid",
    )
    ax.set_xticks(x)
    ax.set_xticklabels(PAGES)
    ax.set_ylabel("character error rate")
    ax.legend(fontsize=13)
    save(fig, "pdf-render-backend")


if __name__ == "__main__":
    main()
