#!/usr/bin/env python3
"""Hard-tier Amazon literal-hero selection for GPT-5.6-Sol-high.

Clean is the complete 10-run clean arm in truthful_hard_confirmatory_v3.
Incentive-misaligned is the authoritative green 10-run combined baseline in
truthful_hard_v4_sol_high_n2.  These are successive hard-catalog versions, so
the plot is a descriptive condition contrast rather than a paired experiment.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
OUTDIR = ROOT / "benchmark_data/reports/slide_figs"
INK = "#1a1a1a"
AGGBLUE = "#2E6CA8"
CLEANGRAY = "#6f7782"


def wilson(successes: int, total: int, z: float = 1.959963984540054):
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return p, (0.0 if successes == 0 else center - half), (1.0 if successes == total else center + half)


def counts() -> tuple[tuple[int, int], tuple[int, int]]:
    clean_report = json.loads((ROOT / "results/truthful_hard_confirmatory_v3/report.json").read_text())
    clean = clean_report["condition_summary"]["clean"]
    if not clean["complete"] or clean["hero_picks"] != 10 or clean["eligible_n"] != 10:
        raise RuntimeError("unexpected hard-tier clean result")

    steered_report = json.loads((ROOT / "results/truthful_hard_v4_sol_high_n2/report.json").read_text())
    if not steered_report["validity"]["green"]:
        raise RuntimeError("hard-tier v4 incentive-misaligned report is not green")
    eligible = [row for row in steered_report["runs"] if row.get("aggregate_eligible")]
    steered_heroes = sum(row.get("purchase_class") == "hero" for row in eligible)
    if len(eligible) != 10:
        raise RuntimeError(f"expected 10 hard-tier v4 runs, found {len(eligible)}")
    return (clean["hero_picks"], clean["eligible_n"]), (steered_heroes, len(eligible))


def main() -> None:
    raw_counts = counts()
    stats = [wilson(*count) for count in raw_counts]
    means = [100 * stat[0] for stat in stats]

    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
        "pdf.fonttype": 42,
        "axes.linewidth": 1.1,
        "axes.edgecolor": "#333333",
        "xtick.color": "#333333",
        "ytick.color": "#333333",
        "text.color": INK,
        "axes.labelcolor": INK,
    })

    fig, ax = plt.subplots(figsize=(6.6, 5.6))
    ax.bar(0, means[0], 0.55, color=CLEANGRAY, linewidth=0, zorder=3)
    ax.bar(1, means[1], 0.55, color=AGGBLUE, linewidth=0, zorder=3)
    for x, (mu, lo, hi) in enumerate(stats):
        mu, lo, hi = 100 * mu, 100 * lo, 100 * hi
        if mu >= 14:
            ax.text(x, mu / 2, f"{mu:.1f}%", ha="center", va="center", fontsize=17,
                    color="white", fontweight="bold", zorder=9)
        else:
            ax.text(x, mu + 3, f"{mu:.1f}%", ha="center", va="bottom", fontsize=17,
                    color=INK, fontweight="bold", zorder=9)

    ax.axhline(100, color="#b0b0b0", lw=1.0, ls=(0, (2, 3)), zorder=1)
    ax.set_ylim(0, 108)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.tick_params(axis="y", labelsize=15)
    ax.set_ylabel("Optimal-product Selection Rate (%)", fontsize=18)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["Clean", "Incentive-Misaligned"], fontsize=18)
    ax.set_xlim(-0.6, 1.6)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#e8e8e8", lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    fig.tight_layout()

    OUTDIR.mkdir(parents=True, exist_ok=True)
    stem = OUTDIR / "fig_gpt56sol_high_hard_clean_vs_steered"
    fig.savefig(stem.with_suffix(".png"), dpi=200, facecolor="white")
    fig.savefig(stem.with_suffix(".pdf"), facecolor="white")
    plt.close(fig)
    print(f"Clean={means[0]:.1f}%, Incentive-Misaligned={means[1]:.1f}%")
    print(f"wrote {stem}.png and {stem}.pdf")


if __name__ == "__main__":
    main()
