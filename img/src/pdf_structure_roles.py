"""What an LLM role pass did to each page's labeled faults.

Numbers copied from docs/260923-eval-PDF_STRUCTURE_FAULTS_LUNA_REGION_ROLES.md
(per-page rows: faults fixed / kept wrong / changed to another wrong label).
Run from the repository root: python docs/img/src/pdf_structure_roles.py
"""

import matplotlib.pyplot as plt
import numpy as np

from _style import PALETTE, apply, save

ROWS = [
    ("1906_p34 p4", 1, 2, 0),
    ("2009 p1", 5, 0, 0),
    ("2010 p1", 4, 0, 0),
    ("latexbook p2", 2, 4, 0),
    ("mixed_p3031 p1", 9, 0, 0),
    ("mixed_p3031 p2", 1, 0, 0),
    ("statement p1", 1, 2, 1),
    ("statement p2", 0, 5, 0),
    ("web_en p1", 15, 0, 0),
    ("word_mid p1", 0, 1, 0),
    ("word_p12 p2", 2, 0, 0),
    ("zhqm p1", 0, 9, 2),
]


def main():
    apply()
    fig, ax = plt.subplots(figsize=(11, 6.5))
    y = np.arange(len(ROWS))
    fixed = np.array([r[1] for r in ROWS])
    kept = np.array([r[2] for r in ROWS])
    changed = np.array([r[3] for r in ROWS])
    ax.barh(
        y,
        fixed,
        color=PALETTE["blue_main"],
        edgecolor="black",
        linewidth=1.2,
        label="fixed",
    )
    ax.barh(
        y,
        kept,
        left=fixed,
        color=PALETTE["neutral"],
        edgecolor="black",
        linewidth=1.2,
        label="kept wrong",
    )
    ax.barh(
        y,
        changed,
        left=fixed + kept,
        color=PALETTE["red_strong"],
        edgecolor="black",
        linewidth=1.2,
        label="changed to another wrong label",
    )
    ax.set_yticks(y)
    ax.set_yticklabels([r[0] for r in ROWS], fontsize=13)
    ax.invert_yaxis()
    ax.set_xlabel("labeled faults on the page")
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=3, fontsize=12)
    save(fig, "pdf-structure-roles")


if __name__ == "__main__":
    main()
