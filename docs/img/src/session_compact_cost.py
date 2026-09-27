"""Session cost against --context-compact-at, and the price of the 260907 defaults.

Numbers copied from docs/260905-eval-SESSION_COST_OPTIMIZATION-RESULTS.md
(k input-token-equivalents per book, B=800) and
docs/260907-fix-CONSERVATIVE_GROUP_DEFAULTS.md (animal_farm, 300 units).
Run from the repository root: python docs/img/src/session_compact_cost.py
"""

import matplotlib.pyplot as plt

from _style import PALETTE, apply, save

C = [1500, 4000, 8000, 20000]
BOOKS = {
    "childrens-literature": ([218.8, 220.1, 228.4, 340.7], PALETTE["blue_main"]),
    "moby-dick-mo": ([207.9, 201.7, 237.2, 263.3], PALETTE["red_strong"]),
    "epub30-spec": ([117.2, 108.4, 111.9, 169.0], PALETTE["teal"]),
}
PRICE = [("old defaults", 283361), ("C=4096", 360681), ("C=8192", 396197)]


def main():
    apply()
    fig, (left, right) = plt.subplots(
        1, 2, figsize=(13, 5), gridspec_kw={"width_ratios": [1.4, 1]}
    )
    for book, (values, color) in BOOKS.items():
        left.plot(C, values, marker="o", lw=2.5, ms=8, color=color, label=book)
    left.set_xscale("log")
    left.set_xticks(C)
    left.set_xticklabels([str(c) for c in C])
    left.minorticks_off()
    left.set_xlabel("--context-compact-at C (estimated tokens)")
    left.set_ylabel("cost per book (k input-equivalents)")
    left.set_title("260905: cost vs C at B=800", fontsize=15)
    left.legend(fontsize=12)

    labels = [p[0] for p in PRICE]
    tokens = [p[1] / 1000 for p in PRICE]
    colors = [PALETTE["neutral"], PALETTE["blue_secondary"], PALETTE["blue_main"]]
    bars = right.bar(labels, tokens, color=colors, edgecolor="black", linewidth=1.5)
    for bar, text in zip(bars, ["", "+27.3%", "+39.8%"]):
        if text:
            right.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 5,
                text,
                ha="center",
                fontsize=13,
            )
    right.set_ylabel("session tokens (thousands)")
    right.set_ylim(0, 450)
    right.set_title("260907: 300 units, new grouping", fontsize=15)
    save(fig, "session-compact-cost")


if __name__ == "__main__":
    main()
