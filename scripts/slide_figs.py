#!/usr/bin/env python
"""Seven standalone slide figures derived from fig_final's panels (owner request, 2026-07-16).

All in panel (a)'s visual style, valid-only strict P* from figure_data.json:
  1. gpt-5.5-low: clean (gray) vs steered (blue+CI) aggregate — two bars.
  2. gpt-5.5-low: five clean/steered bar pairs, one per relativeness level.
  3. gpt-5.5-low: its exact panel-(a) group (aggregate + 5 alpha-ramp level bars + clean caps).
  4. Panel (a) full leaderboard, WITHOUT clean caps, EXCLUDING gpt-5.6-sol high/medium.
  5. Panel (b) scale     -> bars, no relativeness fan, x reversed (Nano -> Full).
  6. Panel (c) vintage   -> bars, no relativeness fan, x reversed (4o -> 5.5).
  7. Panel (d) reasoning -> bars, no relativeness fan, x reversed (low -> high).

Outputs benchmark_data/reports/slide_figs/fig{1..7}_*.png/.pdf (200 dpi + vector).
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

import random
from figdata import mean, boot_ci, VARIANTS

random.seed(7)

# ---------------------------------------------------------------- data (as final_fig.py)
YLAB = "Preference fidelity"
_SNAP = json.load(open("benchmark_data/reports/figure_data.json"))


def load_snap(model, scaffold="browseruse"):
    out = defaultdict(lambda: {"valid": [], "none": 0})
    for c in _SNAP:
        if c.get("model") != model or c.get("scaffold") != scaffold:
            continue
        tid = c.get("task_id", "")
        v = tid.split("-", 1)[1] if "-" in tid else tid
        key = (v, c.get("condition"))
        if c.get("outcome") == "none":
            out[key]["none"] += 1
        elif isinstance(c.get("preservation_strict"), (int, float)):
            out[key]["valid"].append(c["preservation_strict"])
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


def stats(model, scaffold="browseruse"):
    d = load_snap(model, scaffold)
    per, slists, clists, cper = [], [], [], []
    for v in VARIANTS:
        cell = d[(v, "combined")]
        per.append(boot_ci(cell["valid"]))
        slists.append(cell["valid"])
        cc = d[(v, "clean")]
        cper.append(mean(cc["valid"]) if cc["valid"] else float("nan"))
        clists.append(cc["valid"])
    return boot_mom(slists), per, mom(clists), cper, slists, clists


# ---------------------------------------------------------------- style (as final_fig.py)
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
CLEANGRAY = "#6f7782"                       # the clean-cap slate, used as the clean BAR colour here
FAM_COL = {"openai": "#2E6CA8", "moonshot": "#2E8B74", "alibaba": "#C9982E",
           "deepseek": "#7E5AA2", "xai": "#C0504D"}
FAM_NAME = {"openai": "OpenAI", "moonshot": "Moonshot", "alibaba": "Alibaba",
            "deepseek": "DeepSeek", "xai": "xAI"}
RALPHA = [0.30, 0.46, 0.62, 0.78, 0.95]
REL_LBL = ["0 : Fully absolute", "1", "2", "3", "4 : Most relative"]

OUTDIR = Path("benchmark_data/reports/slide_figs")
OUTDIR.mkdir(exist_ok=True)


def deco(ax, ylabel=True, ylim=1.08):
    ax.axhline(1.0, color="#b0b0b0", lw=1.0, ls=(0, (2, 3)), zorder=1)
    ax.set_ylim(0, ylim)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.tick_params(axis="y", labelsize=15)
    if ylabel:
        ax.set_ylabel(YLAB, fontsize=18)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#e8e8e8", lw=0.8, zorder=0)
    ax.set_axisbelow(True)


def vlabel(ax, x, mu, fs=13, rot=90):
    """Panel-a value label: white inside the bar, ink above tiny bars. rot=0 -> horizontal."""
    if mu != mu:
        return
    if mu >= 0.14:
        ax.text(x, mu / 2, f"{mu:.2f}", rotation=rot, ha="center", va="center",
                fontsize=fs, color="white", fontweight="bold", zorder=9)
    else:
        ax.text(x, mu + 0.03, f"{mu:.2f}", rotation=rot, ha="center", va="bottom",
                fontsize=fs, color=INK, fontweight="bold", zorder=9)


def save(fig, stem):
    fig.savefig(OUTDIR / f"{stem}.png", dpi=200, facecolor="white")
    fig.savefig(OUTDIR / f"{stem}.pdf", facecolor="white")
    plt.close(fig)
    print("wrote", OUTDIR / stem)


M55 = "gpt-5.5-low"
agg55, per55, cagg55, cper55, slists55, clists55 = stats(M55)

# ================================================================ 1: clean vs steered
fig, ax = plt.subplots(figsize=(6.6, 5.6))
cmu = cagg55
smu, slo, shi = agg55
ax.bar(0, cmu, 0.55, color=CLEANGRAY, linewidth=0, zorder=3)
ax.bar(1, smu, 0.55, color=AGGBLUE, linewidth=0, zorder=3)
ax.errorbar([1], [smu], yerr=[[smu - slo], [shi - smu]], fmt="none", ecolor=INK,
            elinewidth=1.6, capsize=6, capthick=1.6, zorder=6)
vlabel(ax, 0, cmu, fs=17, rot=0)
vlabel(ax, 1, smu, fs=17, rot=0)
ax.set_xticks([0, 1])
ax.set_xticklabels(["Clean", "Steered"], fontsize=18, fontweight="bold")
ax.set_xlim(-0.6, 1.6)
deco(ax)
fig.tight_layout()
save(fig, "fig1_gpt55low_clean_vs_steered")

# ================================================================ 2: pairs per level
fig, ax = plt.subplots(figsize=(9.6, 5.8))
W = 0.34
xs = np.arange(len(VARIANTS))
for j, v in enumerate(VARIANTS):
    cmu = cper55[j]
    smu, slo, shi = per55[j]
    ax.bar(xs[j] - W / 2, cmu, W, color=CLEANGRAY, linewidth=0, zorder=3)
    # steered bar carries panel (a)'s relativeness style: alpha ramps with the level
    ax.bar(xs[j] + W / 2, smu, W, color=AGGBLUE, alpha=RALPHA[j], linewidth=0, zorder=3)
    if smu == smu:
        ax.errorbar([xs[j] + W / 2], [smu], yerr=[[smu - slo], [shi - smu]], fmt="none",
                    ecolor=INK, elinewidth=1.4, capsize=5, capthick=1.4, zorder=6)
    vlabel(ax, xs[j] - W / 2, cmu, fs=12.5)
    vlabel(ax, xs[j] + W / 2, smu, fs=12.5)
ax.set_xticks(xs)
ax.set_xticklabels(["0", "1", "2", "3", "4"], fontsize=17)
ax.set_xlabel("Number of relative preferences in instructions", fontsize=17)
ax.set_xlim(-0.6, len(VARIANTS) - 0.4)
deco(ax)
fig.legend(handles=[Patch(facecolor=CLEANGRAY, label="Clean"),
                    Patch(facecolor=AGGBLUE, label="Steered")],
           loc="upper right", bbox_to_anchor=(0.985, 0.995), ncol=2, fontsize=15,
           frameon=False, handlelength=1.4, columnspacing=1.2)
fig.suptitle("GPT-5.5 (low)", fontsize=17, fontweight="bold", x=0.2)
fig.tight_layout(rect=(0, 0, 1, 0.94))
save(fig, "fig2_gpt55low_pairs_by_level")

# ================================================================ 3: its exact panel-a group
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


fig, ax = plt.subplots(figsize=(7.2, 5.8))
panel_a_group(ax, -0.45, agg55, per55, cagg55, cper55, AGGBLUE, vfs=15, caps=False)
ax.set_xticks([0])
ax.set_xticklabels(["GPT-5.5 (low)"], fontsize=18, fontweight="bold")
for t in ax.get_xticklabels():
    t.set_color(AGGBLUE)
ax.set_xlim(-0.65, 0.65)
deco(ax, ylim=1.06)
fig.tight_layout()
save(fig, "fig3_gpt55low_panel_a_group")

# ================================================================ 4: panel a, no clean caps, no sol-high/med
MODELS_A = [
    ("gpt-5.6-sol-low", "GPT-5.6-Sol", "openai"),
    # gpt-5.6-sol-medium / -high: excluded from the slide leaderboard (owner request 2026-07-16)
    ("gpt-5.6-terra-low", "GPT-5.6-Terra", "openai"),
    ("gpt-5.5-low", "GPT-5.5 (low)", "openai"),
    ("gpt-5.5-medium", "GPT-5.5 (med)", "openai"),
    ("gpt-5.5-high", "GPT-5.5 (high)", "openai"),
    ("gpt-5-low", "GPT-5", "openai"),
    ("gpt-5-mini-low", "GPT-5 Mini", "openai"),
    ("gpt-5-nano-low", "GPT-5 Nano", "openai"),
    ("gpt-5.1-low", "GPT-5.1", "openai"),
    ("gpt-4.1", "GPT-4.1", "openai"),
    ("gpt-4o", "GPT-4o", "openai"),
    ("gpt-oss-120b-low", "GPT-OSS-120B", "openai"),
    ("Kimi-K2.6", "Kimi K2.6", "moonshot"),
    ("Qwen3.5-122B", "Qwen3.5-122B", "alibaba"),
    ("DeepSeek-V4-Pro", "DeepSeek-V4 Pro", "deepseek"),
    ("DeepSeek-V4-Flash", "DeepSeek-V4 Flash", "deepseek"),
    ("grok-4-1-fast-reasoning", "Grok-4.1 Fast", "xai"),
]
fig, ax = plt.subplots(figsize=(20, 6.8))
rows = []
for mname, disp, fam in MODELS_A:
    agg, per, cagg, cper, _, _ = stats(mname)
    rows.append(dict(disp=disp, fam=fam, agg=agg, per=per, cagg=cagg, cper=cper))
rows.sort(key=lambda r: -(r["agg"][0] if r["agg"][0] == r["agg"][0] else -1))
xs = np.arange(len(rows))
for i in range(len(rows)):
    if i % 2:
        ax.axvspan(i - 0.5, i + 0.5, color="#f5f7f9", zorder=0)
for i, r in enumerate(rows):
    panel_a_group(ax, xs[i] - 0.45, r["agg"], r["per"], r["cagg"], r["cper"],
                  FAM_COL[r["fam"]], caps=False)
ax.set_xticks(xs)
ax.set_xticklabels([r["disp"] for r in rows], fontsize=15, rotation=30, ha="right")
for tick, r in zip(ax.get_xticklabels(), rows):
    tick.set_color(FAM_COL[r["fam"]]); tick.set_fontweight("bold")
ax.set_xlim(-0.65, len(rows) - 0.35)
deco(ax, ylim=1.06)
fig.tight_layout()
save(fig, "fig4_panel_a_noclean")

# ================================================================ 5/6/7: b/c/d as bars, reversed x
def bar_panel(stem, models, xlabels, xlabel, figsize=(6.6, 5.6)):
    """Aggregated steered fidelity as clean bars (no relativeness fan, no bar edges)."""
    fig, ax = plt.subplots(figsize=figsize)
    xs = np.arange(len(models))
    for i, mname in enumerate(models):
        d = load_snap(mname)
        mu, lo, hi = boot_mom([d[(v, "combined")]["valid"] for v in VARIANTS])
        if mu != mu:
            continue
        ax.bar(xs[i], mu, 0.55, color=AGGBLUE, linewidth=0, zorder=3)
        ax.errorbar([xs[i]], [mu], yerr=[[mu - lo], [hi - mu]], fmt="none", ecolor=INK,
                    elinewidth=1.5, capsize=6, capthick=1.5, zorder=6)
        vlabel(ax, xs[i], mu, fs=15, rot=0)
    ax.set_xticks(xs)
    ax.set_xticklabels(xlabels, fontsize=17)
    ax.set_xlim(-0.6, len(models) - 0.4)
    ax.set_xlabel(xlabel, fontsize=18)
    deco(ax)
    fig.tight_layout()
    save(fig, stem)


# panel (b) scale, reversed: Nano -> Full
bar_panel("fig5_scale_bars",
          ["gpt-5-nano-low", "gpt-5-mini-low", "gpt-5-low"],
          ["Nano", "Mini", "Full"], "GPT-5 model scale")
# panel (c) vintage, reversed: 4o -> 5.5, plus gpt-5.6-sol-low at the new end
bar_panel("fig6_vintage_bars",
          ["gpt-4o", "gpt-4.1", "gpt-5-low", "gpt-5.1-low", "gpt-5.5-low", "gpt-5.6-sol-low"],
          ["4o", "4.1", "5", "5.1", "5.5", "5.6-Sol"], "GPT model vintage", figsize=(8.4, 5.6))
# panel (d) reasoning effort, reversed: low -> high
bar_panel("fig7_reasoning_bars",
          ["gpt-5.5-low", "gpt-5.5-medium", "gpt-5.5-high"],
          ["low", "med", "high"], "GPT-5.5 reasoning effort")
