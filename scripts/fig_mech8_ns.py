#!/usr/bin/env python
"""fig_mech8 under a CANDIDATE strict metric — taxonomy layout (2026-07-21).

Two panels (GPT-5.5 low | GPT-4.1), ONE bar per taxonomy category of
marketplace_steering_taxonomy.md, all treated uniformly (no grouping, no bands):

  §1 sponsored placement   only-sponsored  (pinned slot + "Sponsored" label — placement-coupled)
  §2 platform ranking      only-ranking    (pinned slot + "Amazon's Choice" badge)
  §3 drip pricing          only-drip       (checkout fee, zero placement change)
  §4 promo framing         org-promo       (deal flags at ORGANIC rank — isolated cue)
  §5 add-on defaults       only-addon      (prechecked cart add-on, zero placement change)
  §6 scarcity & urgency    org-scarcity    (scarcity cues at ORGANIC rank — isolated cue)
  §7 trust signals         org-trust       (trust cues at ORGANIC rank — isolated cue)
  §8 friction/obstruction  only-friction   (hero buried, zero promotion)

Dashed line = mm_v1 graded4 ALL-MECHANISMS-COMBINED anchor (labeled inline). Valid-only,
boot CI. Scores from benchmark_data/reports/scoring_variants_data.json.

  METRIC=vgeo .venv/bin/python scripts/fig_mech8_ns.py  ->  fig_mech8_<METRIC>.png/.pdf

The production fig_mech8.png is untouched.
"""
import json
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe

from figdata import mean

random.seed(7)

METRIC = os.environ.get("METRIC", "vgeo")
_D = json.load(open("benchmark_data/reports/scoring_variants_data.json"))["snapshots"]
SNAP = _D["mech8_v1"]
ANCH = _D["mm_v1"]

# (condition, taxonomy tick label), in taxonomy order §1..§8
BARS = [
    ("only-sponsored", "sponsored\nplacement"),
    ("only-ranking", "platform\nranking"),
    ("only-drip", "drip\npricing"),
    ("org-promo", "promo\nframing"),
    ("only-addon", "add-on\ndefaults"),
    ("org-scarcity", "scarcity &\nurgency"),
    ("org-trust", "trust\nsignals"),
    ("only-friction", "friction &\nobstruction"),
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


def vals(model, cond):
    return [c["metrics"][METRIC] for c in SNAP
            if c["model"] == model and c["condition"] == cond
            and c["outcome"] != "none" and c.get("metrics")]


def anchor(model, cond):
    v = [c["metrics"][METRIC] for c in ANCH
         if c.get("model") == model and c.get("scaffold") == "browseruse"
         and c.get("condition") == cond and c.get("variant") == "graded4"
         and c.get("outcome") != "none" and c.get("metrics")]
    return mean(v) if v else float("nan")


def boot(xs, B=3000):
    if not xs:
        return (float("nan"),) * 3
    bs = sorted(mean([random.choice(xs) for _ in xs]) for _ in range(B))
    return mean(xs), bs[int(0.025 * B)], bs[int(0.975 * B)]


fig, axes = plt.subplots(1, 2, figsize=(13.2, 5.4), sharey=True)
W = 0.72
for ax, (model, mlabel) in zip(axes, MODELS):
    ax.axhline(1.0, color="#b0b0b0", lw=1.0, ls=(0, (2, 3)), zorder=1)
    cb = anchor(model, "combined")
    ax.axhline(cb, color=INK, lw=1.4, ls=(0, (4, 2)), zorder=6)
    # identify the dashed anchor in the free strip above the bars (labels on the line
    # itself collide with bars when the anchor sits at 0)
    ax.plot([4.30, 4.65], [1.038, 1.038], color=INK, lw=1.4, ls=(0, (4, 2)), zorder=10)
    ax.text(4.80, 1.038, f"all combined  {cb:.2f}", ha="left", va="center",
            fontsize=11.5, color=INK, fontweight="bold", zorder=10)
    for i, (cond, _lab) in enumerate(BARS):
        xs = vals(model, cond)
        mu, lo, hi = boot(xs)
        ax.bar(i, mu, W, color=AGGBLUE, edgecolor="white", linewidth=0.5, zorder=3)
        ax.plot([i, i], [lo, hi], color=INK, lw=1.0, alpha=0.55, zorder=5)
        if mu >= 0.09:
            ax.text(i, mu / 2, f"{mu:.2f}", ha="center", va="center",
                    fontsize=11, color="white", fontweight="bold", zorder=9)
        else:
            ax.text(i, mu + 0.03, f"{mu:.2f}", ha="center", va="bottom",
                    fontsize=10.5, color=INK, zorder=9,
                    path_effects=[pe.Stroke(linewidth=2.6, foreground="white"), pe.Normal()])
    ax.set_xticks(range(len(BARS)))
    ax.set_xticklabels([lab for _c, lab in BARS], fontsize=10)
    ax.tick_params(axis="x", length=0)
    ax.set_xlim(-0.62, len(BARS) - 0.38)
    ax.set_ylim(0, 1.06)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.tick_params(axis="y", labelsize=13)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#e8e8e8", lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.text(0.5, -0.26, mlabel, transform=ax.transAxes, ha="center", fontsize=16,
            fontweight="bold")
axes[0].set_ylabel("Preference fidelity", fontsize=16)
fig.subplots_adjust(left=0.065, right=0.995, top=0.96, bottom=0.21, wspace=0.06)
for ext in ("png", "pdf"):
    fig.savefig(f"benchmark_data/reports/fig_mech8_{METRIC}.{ext}", dpi=300)
print(f"wrote benchmark_data/reports/fig_mech8_{METRIC}.png/.pdf | metric={METRIC}")
