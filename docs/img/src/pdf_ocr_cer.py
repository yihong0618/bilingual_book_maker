"""Character error rate per page class: docling auto OCR vs gpt-5.6-luna.

Numbers copied from docs/260923-eval-PDF_OCR_LUNA_VS_LOCAL_BASELINE.md
(the per-class table, 25 pages). Log scale: the classes span 0.003 to 0.905.
Run from the repository root: python docs/img/src/pdf_ocr_cer.py
"""

import matplotlib.pyplot as plt
import numpy as np

from _style import PALETTE, apply, save

ROWS = [
    ("synthetic clean EN", 0.069, 0.003),
    ("synthetic degraded EN", 0.08, 0.009),
    ("real EN book/magazine", 0.267, 0.014),
    ("degraded typewriter", 0.206, 0.006),
    ("degraded old print", 0.449, 0.003),
    ("embedded text layer", 0.04, 0.009),
    ("zh-Hans synthetic clean", 0.059, 0.023),
    ("zh-Hans synthetic degraded", 0.162, 0.027),
    ("zh-Hans real scan", 0.383, 0.045),
    ("zh-Hant horizontal", 0.069, 0.035),
    ("zh-Hant vertical", 0.905, 0.285),
]


def main():
    apply()
    fig, ax = plt.subplots(figsize=(11, 7))
    y = np.arange(len(ROWS))
    h = 0.38
    ax.barh(
        y - h / 2,
        [r[1] for r in ROWS],
        h,
        color=PALETTE["neutral"],
        edgecolor="black",
        linewidth=1.2,
        label="docling auto OCR",
    )
    ax.barh(
        y + h / 2,
        [r[2] for r in ROWS],
        h,
        color=PALETTE["blue_main"],
        edgecolor="black",
        linewidth=1.2,
        label="gpt-5.6-luna",
    )
    ax.set_yticks(y)
    ax.set_yticklabels([r[0] for r in ROWS], fontsize=13)
    ax.invert_yaxis()
    ax.set_xscale("log")
    ax.set_xlabel("character error rate (log scale, lower is better)")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=2, fontsize=13)
    save(fig, "pdf-ocr-cer")


if __name__ == "__main__":
    main()
