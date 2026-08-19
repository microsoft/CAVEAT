#!/usr/bin/env python
"""fig_cu_magentic_one under a CANDIDATE strict metric — the exact layout of
scripts/fig_cu3.py (Magentic-One CU vs browser-use, sol-high | 5.5-high, combined
bars + clean caps, valid-only + boot CI), but per-cell scores come from
benchmark_data/reports/scoring_variants_data.json (snapshots cu_v3 + mm_v1).

  METRIC=vgeo .venv/bin/python scripts/fig_cu3_ns.py
      ->  fig_cu_magentic_one_<METRIC>.png/.pdf

The production fig_cu_magentic_one.png (P*) is untouched.
"""
import json
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe

from figdata import mean, boot_ci, VARIANTS

random.seed(7)

YLAB = "Preference fidelity"
METRIC = os.environ.get("METRIC", "vgeo")
_D = json.load(open("benchmark_data/reports/scoring_variants_data.json"))["snapshots"]
SNAP_CU = _D["cu_v3"]
SNAP_BU = _D["mm_v1"]


def load_snap(snap, model, scaffold):
    out = defaultdict(lambda: {"valid": [], "none": 0})
    for c in snap:
        if c.get("model") != model or c.get("scaffold") != scaffold:
            continue
        key = (c.get("variant"), c.get("condition"))
        if c.get("outcome") == "none":
            out[key]["none"] += 1
        elif c.get("metrics") and isinstance(c["metrics"].get(METRIC), (int, float)):
            out[key]["valid"].append(c["metrics"][METRIC])
    return out


def mom(lists):
    ms = [mean(xs) for xs in lists if xs]
    return mean(ms) if ms else float("nan")


def boot_mom(lists, B=3000):
    L = [xs for xs in lists if xs]
    if not L:
        return (float("nan"),) * 3
    point = mean([mean(xs) for xs in L])
    bs = sorted(mean([mean([random.choice(xs) for _ in xs]) for xs in L]) for _ in range(B))
    return point, bs[int(0.025 * B)], bs[int(0.975 * B)]


def stats(snap, model, scaffold):
    d = load_snap(snap, model, scaffold)
    per, slists, clists, cper = [], [], [], []
    for v in VARIANTS:
        cell = d[(v, "combined")]
        per.append(boot_ci(cell["valid"]))
        slists.append(cell["valid"])
        cc = d[(v, "clean")]
        cper.append(mean(cc["valid"]) if cc["valid"] else float("nan"))
        clists.append(cc["valid"])
    return boot_mom(slists), per, mom(clists), cper


# ---------------------------------------------------------------- style (as slide_figs.py)
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
RALPHA = [0.30, 0.46, 0.62, 0.78, 0.95]


def deco(ax, ylim=1.06):
    ax.axhline(1.0, color="#b0b0b0", lw=1.0, ls=(0, (2, 3)), zorder=1)
    ax.set_ylim(0, ylim)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.tick_params(axis="y", labelsize=15)
    ax.set_ylabel(YLAB, fontsize=18)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#e8e8e8", lw=0.8, zorder=0)
    ax.set_axisbelow(True)


def cleanmark(ax, x, y, w):
    if y != y:
        return
    ax.plot([x - w * 0.46, x + w * 0.46], [y, y], color=CLEANGRAY, lw=1.7, solid_capstyle="butt",
            zorder=7, path_effects=[pe.Stroke(linewidth=3.2, foreground="white"), pe.Normal()])


def panel_a_group(ax, start, agg, per, cagg, cper, col, WA=0.185, GAP=0.05, WV=0.128,
                  caps=True, vfs=11):
    ax0 = start + WA / 2
    amu, alo, ahi = agg
    ax.bar(ax0, amu, WA, color=col, alpha=1.0, edgecolor=INK, linewidth=0.8, zorder=3)
    ax.plot([ax0, ax0], [alo, ahi], color=INK, lw=1.1, alpha=0.7, zorder=6)
    if caps:
        cleanmark(ax, ax0, cagg, WA)
    ax.text(ax0, amu / 2, f"{amu:.2f}", rotation=90, ha="center", va="center",
            fontsize=vfs, color="white", fontweight="bold", zorder=9)
    for j in range(len(VARIANTS)):
        x = start + WA + GAP + WV / 2 + j * WV
        mu, lo, hi = per[j]
        if mu != mu:
            continue
        ax.bar(x, mu, WV, color=col, alpha=RALPHA[j], edgecolor="white", linewidth=0.3, zorder=3)
        ax.plot([x, x], [lo, hi], color=INK, lw=0.8, alpha=0.4, zorder=4)
        if caps:
            cleanmark(ax, x, cper[j], WV)


GROUPS = [
    ("gpt-5.6-sol-high", "magentic-one", SNAP_CU, "Magentic-One (CU)"),
    ("gpt-5.6-sol-high", "browseruse", SNAP_BU, "browser-use"),
    ("gpt-5.5-high", "magentic-one", SNAP_CU, "Magentic-One (CU)"),
    ("gpt-5.5-high", "browseruse", SNAP_BU, "browser-use"),
]

fig, ax = plt.subplots(figsize=(11.5, 5.9))
xs = np.arange(len(GROUPS))
ax.axvspan(1.5, 3.65, color="#f5f7f9", zorder=0)   # pair the second model's two harnesses
for i, (m, sc, snap, _lbl) in enumerate(GROUPS):
    agg, per, cagg, cper = stats(snap, m, sc)
    panel_a_group(ax, xs[i] - 0.45, agg, per, cagg, cper, AGGBLUE, caps=True, vfs=12)
ax.set_xticks(xs)
ax.set_xticklabels([g[3] for g in GROUPS], fontsize=15)
for xc, name in ((0.5, "GPT-5.6-Sol (high)"), (2.5, "GPT-5.5 (high)")):
    ax.text(xc, -0.155, name, ha="center", va="top", fontsize=17, fontweight="bold",
            color=AGGBLUE, clip_on=False)
ax.set_xlim(-0.65, 3.65)
deco(ax)
fig.tight_layout(rect=(0, 0.05, 1, 1))
out = Path(f"benchmark_data/reports/fig_cu_magentic_one_{METRIC}")
fig.savefig(f"{out}.png", dpi=200, facecolor="white")
fig.savefig(f"{out}.pdf", facecolor="white")
print("wrote", out, "| metric =", METRIC)
