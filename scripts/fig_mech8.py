#!/usr/bin/env python
"""Slide figure for the mech8 clean add-one taxonomy ablation (results/mech8_v1).

Two panels (gpt-5.5-low | gpt-4.1), one bar per only-* condition (aggregate strict P*
over 5 scenarios x 3 reps at graded4, boot CI), grouped substrate -> pin+flavor ->
transaction, with the mm_v1 clean (gray) and combined (dark dashed) anchors as lines.
Slide style (as slide_figs.py): no title, no legend, short condition labels.

Outputs benchmark_data/reports/fig_mech8.png/.pdf (200 dpi + vector).
"""
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe

from figdata import mean

random.seed(7)

SNAP = json.load(open("benchmark_data/reports/mech8_data.json"))
ANCH = json.load(open("benchmark_data/reports/figure_data.json"))

# (condition, short label); grouped: substrate | pin + one display flavor | transaction-side
GROUPSPEC = [
    [("only-shelves", "shelves"), ("only-pin", "pin"), ("only-friction", "burial")],
    [("only-sponsored", "+ad label"), ("only-ranking", "+badge"), ("only-promo", "+deals"),
     ("only-trust", "+trust"), ("only-scarcity", "+scarcity")],
    [("only-drip", "drip fee"), ("only-addon", "addon")],
]
MODELS = [("gpt-5.5-low", "GPT-5.5 (low)"), ("gpt-4.1", "GPT-4.1")]

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
    "svg.fonttype": "none", "pdf.fonttype": 42,
    "axes.linewidth": 1.1, "axes.edgecolor": "#333333",
    "xtick.color": "#333333", "ytick.color": "#333333",
    "text.color": "#1a1a1a", "axes.labelcolor": "#1a1a1a",
})
INK = "#1a1a1a"
AGGBLUE = "#2E6CA8"
CLEANGRAY = "#6f7782"


def vals(model, cond):
    return [c["preservation_strict"] for c in SNAP
            if c["model"] == model and c["condition"] == cond
            and c["outcome"] != "none" and isinstance(c.get("preservation_strict"), (int, float))]


def anchor(model, cond):
    v = [c["preservation_strict"] for c in ANCH
         if c.get("model") == model and c.get("scaffold") == "browseruse"
         and c.get("condition") == cond and str(c.get("task_id", "")).endswith("graded4")
         and c.get("outcome") != "none" and isinstance(c.get("preservation_strict"), (int, float))]
    return mean(v) if v else float("nan")


def boot(xs, B=3000):
    if not xs:
        return (float("nan"),) * 3
    bs = sorted(mean([random.choice(xs) for _ in xs]) for _ in range(B))
    return mean(xs), bs[int(0.025 * B)], bs[int(0.975 * B)]


fig, axes = plt.subplots(1, 2, figsize=(12.6, 5.4), sharey=True)
W, GAP = 0.72, 0.55
for ax, (model, mlabel) in zip(axes, MODELS):
    ax.axhline(1.0, color="#b0b0b0", lw=1.0, ls=(0, (2, 3)), zorder=1)
    cl, cb = anchor(model, "clean"), anchor(model, "combined")
    xmax = sum(len(g) for g in GROUPSPEC) + GAP * (len(GROUPSPEC) - 1)
    ax.axhline(cl, color=CLEANGRAY, lw=1.7, zorder=2)
    ax.axhline(cb, color=INK, lw=1.4, ls=(0, (4, 2)), zorder=2)
    ax.text(xmax - 0.06, cl + 0.022, f"clean {cl:.2f}", ha="right", fontsize=11.5,
            color=CLEANGRAY, fontweight="bold")
    ax.text(-0.40, cb + 0.022, f"combined {cb:.2f}", ha="left", fontsize=11.5,
            color=INK, fontweight="bold", zorder=10,
            path_effects=[pe.Stroke(linewidth=3.0, foreground="white"), pe.Normal()])
    x, ticks, labels = 0.0, [], []
    for gi, group in enumerate(GROUPSPEC):
        for cond, lab in group:
            xs = vals(model, cond)
            mu, lo, hi = boot(xs)
            ax.bar(x + W / 2, mu, W, color=AGGBLUE, edgecolor=INK, linewidth=0.8, zorder=3)
            ax.plot([x + W / 2, x + W / 2], [lo, hi], color=INK, lw=1.0, alpha=0.6, zorder=5)
            if mu >= 0.18:
                ax.text(x + W / 2, mu / 2, f"{mu:.2f}", rotation=90, ha="center", va="center",
                        fontsize=11, color="white", fontweight="bold", zorder=9)
            else:
                ax.text(x + W / 2, mu + 0.03, f"{mu:.2f}", rotation=90, ha="center", va="bottom",
                        fontsize=10.5, color=INK, zorder=9,
                        path_effects=[pe.Stroke(linewidth=2.6, foreground="white"), pe.Normal()])
            ticks.append(x + W / 2)
            labels.append(lab)
            x += 1.0
        if gi < len(GROUPSPEC) - 1:
            x += GAP
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, fontsize=12.5, rotation=38, ha="right")
    ax.set_xlim(-0.45, xmax - 0.1)
    ax.set_ylim(0, 1.06)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.tick_params(axis="y", labelsize=14)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#e8e8e8", lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.text(0.5, -0.30, mlabel, transform=ax.transAxes, ha="center", fontsize=16,
            fontweight="bold")
axes[0].set_ylabel("Preference fidelity", fontsize=17)
fig.subplots_adjust(left=0.08, right=0.995, top=0.97, bottom=0.26, wspace=0.06)
for ext in ("png", "pdf"):
    fig.savefig(f"benchmark_data/reports/fig_mech8.{ext}", dpi=200)
print("wrote benchmark_data/reports/fig_mech8.png/.pdf")
