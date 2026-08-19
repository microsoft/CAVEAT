#!/usr/bin/env python3
"""GPT-5.6-Sol-low literal-hero selection, clean versus steered.

This follows the audited nine-environment leaderboard exactly: literal unique-
hero identity (H), relative-preference levels L1--L4 only, and task-pooled over
five Amazon tasks plus the eight other storefront tasks (n=156 per condition).
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent.parent
CLONE_REPORT = ROOT / "results/clone8_hardened_targeted/expanded_clone8_leaderboard.json"
OUTDIR = ROOT / "benchmark_data/reports/slide_figs"
MODEL = "gpt-5.6-sol-low"
LEVELS = {"mixed", "graded", "graded3", "graded4"}

INK = "#1a1a1a"
AGGBLUE = "#2E6CA8"
CLEANGRAY = "#6f7782"


def counts() -> dict[str, tuple[int, int]]:
    """Return audited 13-task literal-hero counts for each condition."""
    payload = json.loads(CLONE_REPORT.read_text())
    clone = {}
    for condition in ("clean", "steered"):
        rows = [
            row for row in payload["runs"]
            if row["model"] == MODEL
            and row["condition"] == condition
            and row["variant"] in LEVELS
        ]
        if len(rows) != 96:
            raise RuntimeError(f"expected 96 clone runs for {condition}, found {len(rows)}")
        clone[condition] = sum(int(row["literal_hero"]) for row in rows)

    # Audited literal-hero overlay in docs/benchmark_results_and_harness_improvement.md,
    # also frozen as AMAZON_RELATIVE in the nine-environment suite's plot.py.
    amazon = {"clean": 59, "steered": 31}
    return {condition: (amazon[condition] + clone[condition], 156)
            for condition in ("clean", "steered")}


def wilson(successes: int, total: int, z: float = 1.959963984540054):
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / denom
    return p, center - half, center + half


def main() -> None:
    audited_counts = counts()
    stats = [wilson(*audited_counts[c]) for c in ("clean", "steered")]
    means = [100 * s[0] for s in stats]

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
        ax.errorbar([x], [mu], yerr=[[mu - lo], [hi - mu]], fmt="none", ecolor=INK,
                    elinewidth=1.6, capsize=6, capthick=1.6, zorder=6)
        ax.text(x, mu / 2, f"{mu:.1f}%", ha="center", va="center", fontsize=17,
                color="white", fontweight="bold", zorder=9)

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
    stem = OUTDIR / "fig1_gpt56sol_low_clean_vs_steered"
    fig.savefig(stem.with_suffix(".png"), dpi=200, facecolor="white")
    fig.savefig(stem.with_suffix(".pdf"), facecolor="white")
    plt.close(fig)
    print(f"Clean={means[0]:.1f}%, Steered={means[1]:.1f}%")
    print(f"wrote {stem}.png and {stem}.pdf")


if __name__ == "__main__":
    main()
