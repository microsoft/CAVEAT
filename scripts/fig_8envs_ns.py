#!/usr/bin/env python
"""fig_8envs under a CANDIDATE strict metric — the exact layout of scripts/fig_8envs.py
(2x4 per-env panel-a groups, pilot_report5 conventions: none=0, infra excluded, SEM bars),
but per-cell scores come from benchmark_data/reports/scoring_variants_8env.json
(scripts/score_variants_8env.py) instead of the stored details.preservation_strict.

  METRIC=vgeoF .venv/bin/python scripts/fig_8envs_ns.py  ->  fig_8envs_<METRIC>.png/.pdf

The production fig_8envs.png (P*) is untouched; adoption = a separate owner decision.
"""
import json
import os
from collections import defaultdict
from pathlib import Path

import numpy as np
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

METRIC = os.environ.get("METRIC", "vgeoF")
VARIANTS = ["thresholded", "mixed", "graded", "graded3", "graded4"]
MODELS = [("gpt-5.5-high", "GPT-5.5 (high)"), ("gpt-4.1", "GPT-4.1")]
ENVS = [("airbnb", "Airbnb"), ("doordash", "DoorDash"), ("ebay", "eBay"), ("etsy", "Etsy"),
        ("fiverr", "Fiverr"), ("instacart", "Instacart"), ("nike", "Nike"), ("stockx", "StockX")]


def load_cells():
    """{(env, model, cond, var): [(rep, score), ...]} — pilot_report5 conventions."""
    cells = json.load(open("benchmark_data/reports/scoring_variants_8env.json"))["cells"]
    agg = defaultdict(list)
    model_ids = {m for m, _ in MODELS}
    infra = 0
    for r in cells:
        if r["variant"] not in VARIANTS or r["model"] not in model_ids:
            continue
        if r.get("infra"):
            infra += 1
            continue
        done = r["outcome"] not in (None, "none", "error", "skipped")
        val = float(r["metrics"][METRIC]) if (done and r.get("metrics")) else 0.0
        agg[(r["env"], r["model"], r["condition"], r["variant"])].append((r["rep"], val))
    if infra:
        print(f"[infra-excluded cells: {infra}]")
    return agg


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def sem(xs):
    return float(np.std(xs, ddof=1) / np.sqrt(len(xs))) if len(xs) > 1 else 0.0


def stats(agg, env, model):
    per, cper, byrep = [], [], defaultdict(list)
    for v in VARIANTS:
        cells = agg.get((env, model, "steered", v), [])
        vals = [p for _, p in cells]
        per.append((mean(vals), sem(vals)))
        for rep, p in cells:
            byrep[rep].append(p)
        cvals = [p for _, p in agg.get((env, model, "clean", v), [])]
        cper.append(mean(cvals))
    amu = mean([mu for mu, _ in per if mu == mu])
    reps = [mean(ps) for ps in byrep.values()]
    return (amu, sem(reps)), per, mean([c for c in cper if c == c]), cper


# ------------------------------------------------------------------ style (= fig_8envs.py)
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
RALPHA = [0.30, 0.46, 0.62, 0.78, 0.95]
REL_LBL = ["0 : Fully absolute", "1", "2", "3", "4 : Most relative"]
YLAB = "Preference fidelity"
WA, GAP, WV = 0.185, 0.05, 0.128


def cleanmark(ax, x, y, w):
    if y != y:
        return
    ax.plot([x - w * 0.46, x + w * 0.46], [y, y], color="#6f7782", lw=1.7, solid_capstyle="butt",
            zorder=7, path_effects=[pe.Stroke(linewidth=3.2, foreground="white"), pe.Normal()])


def draw_env(ax, agg, env, first_col):
    for i, (model, disp) in enumerate(MODELS):
        (amu, ase), per, cagg, cper = stats(agg, env, model)
        start = i - 0.45
        ax0 = start + WA / 2
        ax.bar(ax0, amu, WA, color=AGGBLUE, alpha=1.0, edgecolor=INK, linewidth=0.8, zorder=3)
        ax.plot([ax0, ax0], [amu - ase, amu + ase], color=INK, lw=1.1, alpha=0.7, zorder=6)
        cleanmark(ax, ax0, cagg, WA)
        ax.text(ax0, max(amu / 2, 0.028), f"{amu:.2f}", rotation=90, ha="center", va="center",
                fontsize=11, color="white" if amu > 0.1 else INK, fontweight="bold", zorder=9)
        for j in range(len(VARIANTS)):
            x = start + WA + GAP + WV / 2 + j * WV
            mu, se = per[j]
            if mu != mu:
                continue
            ax.bar(x, mu, WV, color=AGGBLUE, alpha=RALPHA[j], edgecolor="white", linewidth=0.3, zorder=3)
            ax.plot([x, x], [mu - se, mu + se], color=INK, lw=0.8, alpha=0.4, zorder=4)
            cleanmark(ax, x, cper[j], WV)
    ax.axhline(1.0, color="#b0b0b0", lw=1.0, ls=(0, (2, 3)), zorder=1)
    ax.set_xticks([0, 1])
    ax.set_xticklabels([d for _, d in MODELS], fontsize=15)
    for tick in ax.get_xticklabels():
        tick.set_color(AGGBLUE); tick.set_fontweight("bold")
    ax.set_ylim(0, 1.08)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.tick_params(axis="y", labelsize=15)
    if not first_col:
        ax.tick_params(labelleft=False)
    ax.set_xlim(-0.62, 1.55)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#ededed", lw=0.8, zorder=0)
    ax.set_axisbelow(True)


def main():
    agg = load_cells()
    for env, _ in ENVS:
        for cond in ("clean", "steered"):
            for m, _d in MODELS:
                row = "  ".join(f"{mean([p for _, p in agg.get((env, m, cond, v), [])]):.2f}"
                                for v in VARIANTS)
                print(f"{env:10} {cond:7} {m:13} {row}")

    fig, axes = plt.subplots(2, 4, figsize=(19, 9.6))
    fig.subplots_adjust(left=0.055, right=0.992, top=0.93, bottom=0.115, wspace=0.09, hspace=0.34)
    for k, (env, disp) in enumerate(ENVS):
        ax = axes[k // 4][k % 4]
        draw_env(ax, agg, env, first_col=(k % 4 == 0))
        ax.set_title(disp, fontsize=18, fontweight="bold", pad=8)
    fig.supylabel(YLAB, fontsize=19, x=0.012)

    handles = [Patch(facecolor="#3a3a3a", alpha=1.0, edgecolor="white", label="Aggregated (steered)")] + [
        Patch(facecolor="#3a3a3a", alpha=RALPHA[i], edgecolor="white", label=REL_LBL[i]) for i in range(5)]
    handles += [Line2D([0], [0], color="#6f7782", lw=1.7, label="Clean")]
    fig.legend(handles=handles, loc="center", bbox_to_anchor=(0.5, 0.035),
               ncol=7, frameon=False, fontsize=14, handlelength=1.3, columnspacing=1.4)

    out = Path(f"benchmark_data/reports/fig_8envs_{METRIC}")
    fig.savefig(str(out) + ".png", dpi=300, facecolor="white")
    fig.savefig(str(out) + ".pdf", facecolor="white")
    print(f"wrote {out}.png + .pdf | metric={METRIC} (none=0, SEM over repeats)")


if __name__ == "__main__":
    main()
