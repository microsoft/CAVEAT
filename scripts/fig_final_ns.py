#!/usr/bin/env python
"""fig_final under a CANDIDATE strict metric (scoring-upgrade study) — the exact layout of
scripts/final_fig.py (panels a-e, valid-only, same style), but per-cell scores come from
benchmark_data/reports/scoring_variants_data.json (mm_v1) instead of figure_data.json.

  METRIC=vgeoF .venv/bin/python scripts/fig_final_ns.py   ->  fig_final_<METRIC>.png/.pdf

The production fig_final.png (P*) is untouched; adoption = a separate owner decision.
"""
import json
import os
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

METRIC = os.environ.get("METRIC", "vgeoF")


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


YLAB = "Preference fidelity"
_SNAP = json.load(open("benchmark_data/reports/scoring_variants_data.json"))["snapshots"]["mm_v1"]


def load_snap(model, scaffold="browseruse"):
    """{(variant,condition): {'valid':[metric...], 'none':int}} — mirrors final_fig.load_snap
    (none counted; error/unscored skipped; off-catalog = 0.0 via its metrics dict)."""
    out = defaultdict(lambda: {"valid": [], "none": 0})
    for c in _SNAP:
        if c.get("model") != model or c.get("scaffold") != scaffold:
            continue
        key = (c.get("variant"), c.get("condition"))
        if c.get("outcome") == "none":
            out[key]["none"] += 1
        elif c.get("metrics"):
            out[key]["valid"].append(float(c["metrics"][METRIC]))
    return out


def stats(model, scaffold="browseruse"):
    d = load_snap(model, scaffold)
    per, slists, clists, ncells, nnone, nvalid = [], [], [], 0, 0, 0
    cper = []
    for v in VARIANTS:
        cell = d[(v, "combined")]
        per.append(boot_ci(cell["valid"]))
        slists.append(cell["valid"])
        ncells += len(cell["valid"]) + cell["none"]
        nnone += cell["none"]
        nvalid += len(cell["valid"])
        cc = d[(v, "clean")]
        cper.append(mean(cc["valid"]) if cc["valid"] else float("nan"))
        clists.append(cc["valid"])
    comp = (ncells - nnone) / ncells if ncells else float("nan")
    return boot_mom(slists), per, comp, nvalid, mom(clists), cper


# ----------------------------------------------------------------------------- style
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
RLINE = ["#d2d2d2", "#aeaeae", "#888888", "#606060", "#383838"]
FAM_COL = {"openai": "#2E6CA8", "moonshot": "#2E8B74", "alibaba": "#C9982E",
           "deepseek": "#7E5AA2", "xai": "#C0504D"}
FAM_NAME = {"openai": "OpenAI", "moonshot": "Moonshot", "alibaba": "Alibaba",
            "deepseek": "DeepSeek", "xai": "xAI"}
RALPHA = [0.30, 0.46, 0.62, 0.78, 0.95]
REL_LBL = ["0 : Fully absolute", "1", "2", "3", "4 : Most relative"]
AGG_FX = [pe.Stroke(linewidth=7.5, foreground="white"), pe.Normal()]

MODELS_A = [
    ("gpt-5.6-sol-low", "browseruse", "GPT-5.6-Sol (low)", "openai"),
    ("gpt-5.6-sol-medium", "browseruse", "GPT-5.6-Sol (med)", "openai"),
    ("gpt-5.6-sol-high", "browseruse", "GPT-5.6-Sol (high)", "openai"),
    ("gpt-5.6-terra-low", "browseruse", "GPT-5.6-Terra", "openai"),
    ("gpt-5.5-low", "browseruse", "GPT-5.5 (low)", "openai"),
    ("gpt-5.5-medium", "browseruse", "GPT-5.5 (med)", "openai"),
    ("gpt-5.5-high", "browseruse", "GPT-5.5 (high)", "openai"),
    ("gpt-5-low", "browseruse", "GPT-5", "openai"),
    ("gpt-5-mini-low", "browseruse", "GPT-5 Mini", "openai"),
    ("gpt-5-nano-low", "browseruse", "GPT-5 Nano", "openai"),
    ("gpt-5.1-low", "browseruse", "GPT-5.1", "openai"),
    ("gpt-4.1", "browseruse", "GPT-4.1", "openai"),
    ("gpt-4o", "browseruse", "GPT-4o", "openai"),
    ("gpt-oss-120b-low", "browseruse", "GPT-OSS-120B", "openai"),
    ("Kimi-K2.6", "browseruse", "Kimi K2.6", "moonshot"),
    ("Qwen3.5-122B", "browseruse", "Qwen3.5-122B", "alibaba"),
    ("DeepSeek-V4-Pro", "browseruse", "DeepSeek-V4 Pro", "deepseek"),
    ("DeepSeek-V4-Flash", "browseruse", "DeepSeek-V4 Flash", "deepseek"),
    ("grok-4-1-fast-reasoning", "browseruse", "Grok-4.1 Fast", "xai"),
]


def cleanmark(ax, x, y, w):
    if y != y:
        return
    ax.plot([x - w * 0.46, x + w * 0.46], [y, y], color="#6f7782", lw=1.7, solid_capstyle="butt",
            zorder=7, path_effects=[pe.Stroke(linewidth=3.2, foreground="white"), pe.Normal()])


def panel_header(fig, ax, letter, title):
    pos = ax.get_position()
    yt = pos.y1 + 0.014
    fig.text(pos.x0 - 0.012, yt, letter, fontsize=25, fontweight="bold", va="bottom", ha="left")
    fig.text((pos.x0 + pos.x1) / 2, yt, title, fontsize=18, fontweight="bold", va="bottom", ha="center")


# ----------------------------------------------------------------------------- figure
fig = plt.figure(figsize=(22, 13))
gs = fig.add_gridspec(2, 1, height_ratios=[1.15, 0.72], hspace=0.46,
                      left=0.06, right=0.99, top=0.92, bottom=0.13)
gbot = gs[1].subgridspec(1, 4, width_ratios=[1, 1, 1, 1], wspace=0.13)

# ============================================================= (a) all model variants
axa = fig.add_subplot(gs[0])
rows = []
for mname, sf, disp, fam in MODELS_A:
    agg, per, comp, n, cagg, cper = stats(mname, sf)
    rows.append(dict(disp=disp, fam=fam, agg=agg, per=per, comp=comp, n=n, cagg=cagg, cper=cper))
rows.sort(key=lambda r: -(r["agg"][0] if r["agg"][0] == r["agg"][0] else -1))
xs = np.arange(len(rows))
WA, GAP, WV = 0.185, 0.05, 0.128
for i in range(len(rows)):
    if i % 2:
        axa.axvspan(i - 0.5, i + 0.5, color="#f5f7f9", zorder=0)
for i, r in enumerate(rows):
    col = FAM_COL[r["fam"]]
    start = xs[i] - 0.45
    ax0 = start + WA / 2
    amu, alo, ahi = r["agg"]
    axa.bar(ax0, amu, WA, color=col, alpha=1.0, edgecolor=INK, linewidth=0.8, zorder=3)
    axa.plot([ax0, ax0], [alo, ahi], color=INK, lw=1.1, alpha=0.7, zorder=6)
    cleanmark(axa, ax0, r["cagg"], WA)
    axa.text(ax0, max(amu / 2, 0.028), f"{amu:.2f}", rotation=90, ha="center", va="center",
             fontsize=11, color="white" if amu > 0.1 else INK, fontweight="bold", zorder=9)
    for j, v in enumerate(VARIANTS):
        x = start + WA + GAP + WV / 2 + j * WV
        mu, lo, hi = r["per"][j]
        if mu != mu:
            continue
        axa.bar(x, mu, WV, color=col, alpha=RALPHA[j], edgecolor="white", linewidth=0.3, zorder=3)
        axa.plot([x, x], [lo, hi], color=INK, lw=0.8, alpha=0.4, zorder=4)
        cleanmark(axa, x, r["cper"][j], WV)
axa.axhline(1.0, color="#b0b0b0", lw=1.0, ls=(0, (2, 3)), zorder=1)
axa.set_xticks(xs)
axa.set_xticklabels([r["disp"] for r in rows], fontsize=15, rotation=30, ha="right")
for tick, r in zip(axa.get_xticklabels(), rows):
    tick.set_color(FAM_COL[r["fam"]]); tick.set_fontweight("bold")
axa.set_ylabel(YLAB, fontsize=19)
axa.set_ylim(0, 1.18)
axa.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
axa.tick_params(axis="y", labelsize=16)
axa.set_xlim(-0.65, len(rows) - 0.35)
axa.spines[["top", "right"]].set_visible(False)
axa.grid(axis="y", color="#e8e8e8", lw=0.8, zorder=0)
axa.set_axisbelow(True)
rel_handles = [Patch(facecolor="#3a3a3a", alpha=1.0, edgecolor="white", label="Aggregated (steered)")] + [
    Patch(facecolor="#3a3a3a", alpha=RALPHA[i], edgecolor="white", label=REL_LBL[i]) for i in range(5)]
rel_handles += [Line2D([0], [0], color="#6f7782", lw=1.7, label="Clean")]
leg1 = axa.legend(handles=rel_handles, loc="upper left", bbox_to_anchor=(0.005, 0.99),
                  fontsize=14, frameon=False, ncol=7, handlelength=1.3, columnspacing=1.4)
axa.add_artist(leg1)
fam_order = []
for r in rows:
    if r["fam"] not in fam_order:
        fam_order.append(r["fam"])
fam_handles = [Patch(facecolor=FAM_COL[k], label=FAM_NAME[k]) for k in fam_order]
axa.legend(handles=fam_handles, loc="upper right", bbox_to_anchor=(0.995, 0.99),
           fontsize=14, frameon=False, ncol=5, handlelength=1.3, columnspacing=1.5)


# ---- line panel ------------------------------------------------------------
def line_panel(ax, series, xlabels, xlabel, ylabel=False, per_level_bg=True):
    xv = np.arange(len(xlabels))
    if per_level_bg:
        perlevel = {v: [] for v in VARIANTS}
        for _, mname, sf in series[0][2]:
            d = load_snap(mname, sf)
            for v in VARIANTS:
                cell = d[(v, "combined")]["valid"]
                perlevel[v].append(mean(cell) if cell else np.nan)
        for i, v in enumerate(VARIANTS):
            ax.plot(xv, perlevel[v], "-", color=RLINE[i], lw=2.0, alpha=0.8, zorder=2,
                    marker="o", ms=4, mec="none")
    for lab, _col, pts in series:
        points, los, his = [], [], []
        for _, mname, sf in pts:
            d = load_snap(mname, sf)
            p, lo, hi = boot_mom([d[(v, "combined")]["valid"] for v in VARIANTS])
            points.append(p if p == p else np.nan)
            los.append(lo if lo == lo else np.nan)
            his.append(hi if hi == hi else np.nan)
        points, los, his = np.array(points), np.array(los), np.array(his)
        ax.plot(xv, points, "-", color=AGGBLUE, lw=4.6, zorder=6, path_effects=AGG_FX, label=lab)
        ax.errorbar(xv, points, yerr=[points - los, his - points], fmt="none",
                    ecolor=AGGBLUE, elinewidth=2.3, capsize=6, capthick=2.3, zorder=8)
        ax.plot(xv, points, "o", color=AGGBLUE, ms=8, mfc=AGGBLUE, mec="white", mew=1.5, zorder=7)
    ax.axhline(1.0, color="#b0b0b0", lw=1.0, ls=(0, (2, 3)), zorder=1)
    ax.set_xticks(xv); ax.set_xticklabels(xlabels, fontsize=16)
    ax.set_xlim(-0.4, len(xlabels) - 0.6)
    ax.set_ylim(0, 1.08); ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.tick_params(axis="y", labelsize=15)
    ax.set_xlabel(xlabel, fontsize=17)
    if ylabel:
        ax.set_ylabel(YLAB, fontsize=19)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#ededed", lw=0.8, zorder=0); ax.set_axisbelow(True)


axb = fig.add_subplot(gbot[0])
line_panel(axb, [
    ("GPT-5", AGGBLUE, [(None, "gpt-5-low", "browseruse"), (None, "gpt-5-mini-low", "browseruse"), (None, "gpt-5-nano-low", "browseruse")]),
], ["Full", "Mini", "Nano"], "GPT-5 model scale", ylabel=True)

axc = fig.add_subplot(gbot[1])
line_panel(axc, [("vintage", AGGBLUE, [(None, "gpt-5.5-low", "browseruse"), (None, "gpt-5.1-low", "browseruse"), (None, "gpt-5-low", "browseruse"),
                                       (None, "gpt-4.1", "browseruse"), (None, "gpt-4o", "browseruse")])],
           ["5.5", "5.1", "5", "4.1", "4o"], "GPT model vintage")
axc.tick_params(labelleft=False)

axd = fig.add_subplot(gbot[2])
line_panel(axd, [("reasoning", AGGBLUE, [(None, "gpt-5.5-high", "browseruse"), (None, "gpt-5.5-medium", "browseruse"), (None, "gpt-5.5-low", "browseruse")])],
           ["high", "med", "low"], "GPT-5.5 reasoning effort")
axd.tick_params(labelleft=False)

# (e) harness
axe = fig.add_subplot(gbot[3])
HARN = [("browser-use", "browseruse"), ("playwright-mcp", "playwright-mcp")]
for i, (lab, sf) in enumerate(HARN):
    agg, per, comp, n, cagg, cper = stats("gpt-5.5-low", sf)
    start = i - 0.45
    ax0 = start + WA / 2
    amu, alo, ahi = agg
    axe.bar(ax0, amu, WA, color=AGGBLUE, alpha=1.0, edgecolor=INK, linewidth=0.8, zorder=3)
    axe.plot([ax0, ax0], [alo, ahi], color=INK, lw=1.1, alpha=0.7, zorder=6)
    cleanmark(axe, ax0, cagg, WA)
    axe.text(ax0, max(amu / 2, 0.028), f"{amu:.2f}", rotation=90, ha="center", va="center", fontsize=11,
             color="white" if amu > 0.1 else INK, fontweight="bold", zorder=9)
    for j, v in enumerate(VARIANTS):
        x = start + WA + GAP + WV / 2 + j * WV
        mu, lo, hi = per[j]
        if mu != mu:
            continue
        axe.bar(x, mu, WV, color=AGGBLUE, alpha=RALPHA[j], edgecolor="white", linewidth=0.3, zorder=3)
        axe.plot([x, x], [lo, hi], color=INK, lw=0.8, alpha=0.4, zorder=4)
        cleanmark(axe, x, cper[j], WV)
axe.axhline(1.0, color="#b0b0b0", lw=1.0, ls=(0, (2, 3)), zorder=1)
axe.set_xticks([0, 1]); axe.set_xticklabels([h[0] for h in HARN], fontsize=15)
axe.set_ylim(0, 1.08); axe.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
axe.tick_params(labelleft=False)
axe.set_xlim(-0.62, 1.55)
axe.set_xlabel("Agent harness (GPT-5.5)", fontsize=17)
axe.spines[["top", "right"]].set_visible(False)
axe.grid(axis="y", color="#ededed", lw=0.8, zorder=0); axe.set_axisbelow(True)

for ax, L, T in [(axa, "a", "Model leaderboard (strong → weak)"), (axb, "b", "Effect of model scale"),
                 (axc, "c", "Effect of model vintage"), (axd, "d", "Effect of reasoning effort"),
                 (axe, "e", "Effect of harness")]:
    panel_header(fig, ax, L, T)

fig.canvas.draw()
pb, pd = axb.get_position(), axd.get_position()
lh = [Line2D([0], [0], color=AGGBLUE, lw=4.0, marker="o", ms=8, mfc=AGGBLUE, mec="white", mew=1.5,
             label="Aggregated (steered)", path_effects=AGG_FX)]
lh += [Line2D([0], [0], color=RLINE[i], lw=2.6, alpha=0.95, marker="o", ms=5, label=REL_LBL[i]) for i in range(5)]
fig.legend(handles=lh, loc="center", bbox_to_anchor=((pb.x0 + pd.x1) / 2, 0.045),
           ncol=6, frameon=False, fontsize=14, handlelength=1.8, columnspacing=2.0)

OUT = Path(f"benchmark_data/reports/fig_final_{METRIC}")
fig.savefig(str(OUT) + ".png", dpi=200, facecolor="white")
fig.savefig(str(OUT) + ".pdf", facecolor="white")
print(f"wrote {OUT}.png + .pdf | metric={METRIC} (valid-only)")
