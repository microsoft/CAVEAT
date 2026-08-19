#!/usr/bin/env python
"""Per-environment results figure (preference fidelity) for the CAVEAT benchmark.

8 subfigures (2x4), one per env, each styled EXACTLY like panel (a) of fig_final.png
(scripts/final_fig.py): per model group an OPAQUE aggregated-steered bar (value label inside,
error bar) followed by 5 increasing-alpha relativeness bars (thresholded..graded4 = levels 0..4),
each capped with a slate CLEAN (un-steered) baseline tick. Both models are OpenAI -> same blue.

Per-cell scoring + averaging MATCH scripts/pilot_report5.py exactly so the numbers agree with
benchmark_data/reports/pilot_final_8envs.txt:
  P* = trajectory.json evaluation.details.preservation_strict; a cell whose outcome is
  none/error/skipped/missing scores 0.0 (only INFRASTRUCTURE-terminated cells are excluded, by
  the shared rule in scripts/_infra_classify.py);
  per-variant P = mean over repeat cells. Aggregated bar = mean of the 5 per-variant means
  (equal-weight, as final_fig.py's mom()); error bars = SEM across repeats (per-variant bars)
  and SEM across per-repeat aggregates (aggregated bar).

  PILOT_RESULTS=results/byenv_v2 .venv/bin/python scripts/fig_8envs.py
"""
import glob
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))            # scripts/ (shared rule)

import numpy as np
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

VARIANTS = ["thresholded", "mixed", "graded", "graded3", "graded4"]   # relativeness levels 0..4
MODELS = [("gpt-5.5-high", "GPT-5.5 (high)"), ("gpt-4.1", "GPT-4.1")]
ENVS = [("airbnb", "Airbnb"), ("doordash", "DoorDash"), ("ebay", "eBay"), ("etsy", "Etsy"),
        ("fiverr", "Fiverr"), ("instacart", "Instacart"), ("nike", "Nike"), ("stockx", "StockX")]
RESULTS = os.environ.get("PILOT_RESULTS", "results/byenv_v2")

# ------------------------------------------------------------------ data (= pilot_report5.py)
# Infra-vs-capability is decided by the ONE shared implementation in scripts/_infra_classify.py,
# exactly as pilot_report5.py and build_figure_data.py do. Only a run that
# INFRASTRUCTURE terminated is excluded; unparseable output, give-ups and loops score 0.
from _infra_classify import is_infra_fail   # noqa: E402


def load_cells():
    """{(env, model, cond, var): [(rep_dir, P*), ...]} with pilot_report5 per-cell scoring."""
    agg = defaultdict(list)
    infra = 0
    model_ids = {m for m, _ in MODELS}
    for tj in glob.glob(f"{RESULTS}/*/*/trajectory.json"):
        try:
            t = json.load(open(tj))
        except Exception:
            continue
        env = t.get("env"); model = t.get("model"); cond = t.get("condition")
        tid = t.get("task_id", ""); var = tid.rsplit("-", 1)[-1]
        if var not in VARIANTS or model not in model_ids:
            continue
        ev = t.get("evaluation") or {}
        det = ev.get("details") or {}
        ps = det.get("preservation_strict")
        outcome = ev.get("outcome")
        if is_infra_fail(os.path.dirname(tj)):
            infra += 1
            continue
        done = outcome not in (None, "none", "error", "skipped")
        rep = Path(tj).parent.parent.name                       # e.g. ebay_r2
        agg[(env, model, cond, var)].append(
            (rep, float(ps) if (done and isinstance(ps, (int, float))) else 0.0))
    if infra:
        print(f"[infra-excluded cells: {infra}]")
    return agg


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def sem(xs):
    return float(np.std(xs, ddof=1) / np.sqrt(len(xs))) if len(xs) > 1 else 0.0


def stats(agg, env, model):
    """Steered: (agg_mu, agg_sem), [(mu, sem) x5 variants]; clean: agg_mu, [mu x5]."""
    per, cper, byrep = [], [], defaultdict(list)
    for v in VARIANTS:
        cells = agg.get((env, model, "steered", v), [])
        vals = [p for _, p in cells]
        per.append((mean(vals), sem(vals)))
        for rep, p in cells:
            byrep[rep].append(p)
        cvals = [p for _, p in agg.get((env, model, "clean", v), [])]
        cper.append(mean(cvals))
    amu = mean([mu for mu, _ in per if mu == mu])               # equal-weight mean of level means
    reps = [mean(ps) for ps in byrep.values()]                  # per-repeat aggregate -> SEM
    return (amu, sem(reps)), per, mean([c for c in cper if c == c]), cper


# ------------------------------------------------------------------ style (= final_fig.py)
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
    "svg.fonttype": "none", "pdf.fonttype": 42,
    "axes.linewidth": 1.1, "axes.edgecolor": "#333333",
    "xtick.color": "#333333", "ytick.color": "#333333",
    "text.color": "#1a1a1a", "axes.labelcolor": "#1a1a1a",
})
INK = "#1a1a1a"
AGGBLUE = "#2E6CA8"                                     # OpenAI-family blue (both models are OpenAI)
RALPHA = [0.30, 0.46, 0.62, 0.78, 0.95]                 # increasing alpha for relativeness 0..4
REL_LBL = ["0 : Fully absolute", "1", "2", "3", "4 : Most relative"]
YLAB = "Preference fidelity"
WA, GAP, WV = 0.185, 0.05, 0.128                        # panel-a bar geometry


def cleanmark(ax, x, y, w):
    """Clean (un-steered) fidelity baseline on a steered bar — a slate cap so the steering gap is visible."""
    if y != y:
        return
    ax.plot([x - w * 0.46, x + w * 0.46], [y, y], color="#6f7782", lw=1.7, solid_capstyle="butt",
            zorder=7, path_effects=[pe.Stroke(linewidth=3.2, foreground="white"), pe.Normal()])


def draw_env(ax, agg, env, first_col):
    for i, (model, disp) in enumerate(MODELS):
        (amu, ase), per, cagg, cper = stats(agg, env, model)
        start = i - 0.45
        # aggregated bar — OPAQUE (alpha=1), the anchor: wider, value label
        ax0 = start + WA / 2
        ax.bar(ax0, amu, WA, color=AGGBLUE, alpha=1.0, edgecolor=INK, linewidth=0.8, zorder=3)
        ax.plot([ax0, ax0], [amu - ase, amu + ase], color=INK, lw=1.1, alpha=0.7, zorder=6)
        cleanmark(ax, ax0, cagg, WA)
        ax.text(ax0, amu / 2, f"{amu:.2f}", rotation=90, ha="center", va="center",
                fontsize=11, color="white", fontweight="bold", zorder=9)
        # 5 relativeness bars — increasing alpha
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
    # printed cross-check table (matches pilot_report5.py / pilot_final_8envs.txt)
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

    out = Path("benchmark_data/reports/fig_8envs")
    fig.savefig(str(out) + ".png", dpi=300, facecolor="white")
    fig.savefig(str(out) + ".pdf", facecolor="white")
    print("wrote", out, ".png + .pdf | per-env P* (none=0), SEM over repeats, panel-a style")


if __name__ == "__main__":
    main()
