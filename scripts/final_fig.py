#!/usr/bin/env python
"""Main results figure for CAVEAT.

Reads benchmark_data/reports/figure_data.json (schema 3, written by
scripts/build_figure_data.py) and renders benchmark_data/reports/fig_final.{png,pdf}.

  (a) clean vs steered P* across the relativeness spectrum, one facet per headline
      model (b80 matrix: 5 products x 5 levels x n=5, max_steps 80)
  (b) model leaderboard - steered P* pooled over the graded levels (2-4), with the
      clean baseline as a cap tick and the capitulation-ceiling band C_L drawn in
  (c) the 9 storefront clones - C4 (gpt-5.5-high, steered, level 4) with every run
      plotted, against the C4 < 0.5 criterion
  (d) budget sensitivity - steered P* at 50 vs 80 steps (dumbbell), give-up rates
      annotated

Palette: dataviz reference instance (categorical slots 1-3 + the blue sequential
ramp), validated for light mode on a white surface; every sub-3:1 fill carries
direct labels, and the generated benchmark reports hold the table view.
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, PathPatch
from matplotlib.path import Path as MPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from figdata import VARIANTS, boot_ci                      # noqa: E402

DATA = json.loads((ROOT / "benchmark_data/reports/figure_data.json").read_text())
OUT = ROOT / "benchmark_data/reports/fig_final"

# --------------------------------------------------------------- metric switch
# P* (default) is the graded headline. "B" is the all-or-nothing read: 1 if the run
# bought the user's genuine best item (the hero), else 0 — identity B == 1[P* == 1].
# Binarising happens on the PER-RUN values, so bootstrap CIs are the CIs of a rate.
METRIC = "B" if "--metric" in sys.argv and sys.argv[sys.argv.index("--metric") + 1] == "B" else "pstar"
BINARY = METRIC == "B"
if BINARY:
    OUT = ROOT / "benchmark_data/reports/fig_final_binary"
MET_LABEL = "Hero-exact rate  B" if BINARY else "Preference fidelity  P*"
MET_SHORT = "B" if BINARY else "P*"
_EPS = 1e-9


def mvals(values):
    """Per-run metric values: raw P*, or 1/0 hero-exact under --metric B."""
    if not values:
        return []
    return [1.0 if v >= 1.0 - _EPS else 0.0 for v in values] if BINARY else list(values)


def mscalar(c, key="pstar"):
    """Cell-level scalar for the active metric (falls back to the stored B field)."""
    if not c:
        return float("nan")
    return c.get("B", float("nan")) if BINARY else c.get(key, float("nan"))

# ----------------------------------------------------------------- design tokens
SURFACE = "#ffffff"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
S1 = "#2a78d6"        # categorical slot 1 - blue
S2 = "#eb6834"        # categorical slot 2 - orange
S3 = "#1baf7a"        # categorical slot 3 - aqua  (sub-3:1 -> always direct-labelled)
BLUE_250 = "#86b6ef"  # blue ramp step 250 - the "before" end of the dumbbell
BAND = "#e4edfa"      # C_L band wash (blue ramp, ~10%)
DEEMPH = "#c3c2b7"    # de-emphasis gray (panel c boundary envs)

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Helvetica", "Arial"],
    "pdf.fonttype": 42, "ps.fonttype": 42, "svg.fonttype": "none",
    "axes.linewidth": 0.8, "axes.edgecolor": AXIS,
    "xtick.color": MUTED, "ytick.color": MUTED,
    "xtick.labelcolor": INK2, "ytick.labelcolor": INK2,
    "text.color": INK, "axes.labelcolor": INK2,
    "xtick.major.size": 0, "ytick.major.size": 0,
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
})

REL_TICK = ["0\nabsolute", "1", "2", "3", "4\nmost relative"]
DISP = {
    "gpt-5.6-sol-high": "GPT-5.6-Sol (high)", "gpt-5.6-sol-medium": "GPT-5.6-Sol (med)",
    "gpt-5.6-sol-low": "GPT-5.6-Sol (low)", "gpt-5.6-terra-low": "GPT-5.6-Terra (low)",
    "gpt-5.5-high": "GPT-5.5 (high)", "gpt-5.5-medium": "GPT-5.5 (med)",
    "gpt-5.5-low": "GPT-5.5 (low)", "gpt-5-low": "GPT-5 (low)",
    "gpt-5-mini-low": "GPT-5 Mini (low)", "gpt-5-nano-low": "GPT-5 Nano (low)",
    "gpt-4.1": "GPT-4.1", "gpt-4o": "GPT-4o", "gpt-oss-120b-low": "GPT-OSS-120B (low)",
    "Kimi-K2.6": "Kimi K2.6", "Qwen3.5-122B": "Qwen3.5-122B",
    "DeepSeek-V4-Pro": "DeepSeek-V4 Pro", "DeepSeek-V4-Flash": "DeepSeek-V4 Flash",
    "grok-4.3": "Grok-4.3", "grok-4-1-fast-reasoning": "Grok-4.1 Fast",
}
ENV_DISP = {"caveat_sport": "CAVEAT-Sport", "caveat_market": "CAVEAT-Market", "caveat_craft": "CAVEAT-Craft", "caveat_services": "CAVEAT-Services",
            "caveat_kicks": "CAVEAT-Kicks", "caveat_food": "CAVEAT-Food",
            "caveat_grocery": "CAVEAT-Grocery", "caveat_stay": "CAVEAT-Stay"}
BOUNDARY = {"caveat_stay"}
GRADED = ("graded", "graded3", "graded4")

random.seed(11)


# ----------------------------------------------------------------- mark helpers
def _px(ax, n, axis):
    """n display pixels expressed in this axes' data units."""
    inv = ax.transData.inverted()
    (x0, y0), (x1, y1) = inv.transform((0, 0)), inv.transform((n, n))
    return abs(x1 - x0) if axis == "x" else abs(y1 - y0)


def rbar(ax, pos, length, width, color, horiz=False, base=0.0, alpha=1.0, zorder=3):
    """A bar with a 4px-rounded data-end and a square baseline end (no stroke)."""
    if not np.isfinite(length):
        return
    rx, ry = _px(ax, 4, "x"), _px(ax, 4, "y")
    if horiz:
        r = min(rx, abs(length) * 0.9, ry * 1e9)
        r = min(r, _px(ax, 4, "x"), abs(length))
        rw = min(_px(ax, 4, "y"), width / 2)
        a, b = base, base + length
        lo, hi = pos - width / 2, pos + width / 2
        verts = [(a, lo), (b - r, lo), (b, lo), (b, lo + rw),
                 (b, hi - rw), (b, hi), (b - r, hi), (a, hi), (a, lo)]
        codes = [MPath.MOVETO, MPath.LINETO, MPath.CURVE3, MPath.CURVE3,
                 MPath.LINETO, MPath.CURVE3, MPath.CURVE3, MPath.LINETO, MPath.CLOSEPOLY]
    else:
        r = min(_px(ax, 4, "y"), abs(length))
        rw = min(_px(ax, 4, "x"), width / 2)
        a, b = base, base + length
        lo, hi = pos - width / 2, pos + width / 2
        verts = [(lo, a), (lo, b - r), (lo, b), (lo + rw, b),
                 (hi - rw, b), (hi, b), (hi, b - r), (hi, a), (lo, a)]
        codes = [MPath.MOVETO, MPath.LINETO, MPath.CURVE3, MPath.CURVE3,
                 MPath.LINETO, MPath.CURVE3, MPath.CURVE3, MPath.LINETO, MPath.CLOSEPOLY]
    ax.add_patch(PathPatch(MPath(verts, codes), facecolor=color, edgecolor="none",
                           alpha=alpha, zorder=zorder))


def cap(ax, pos, val, width, horiz=False, color=MUTED):
    """The clean (un-steered) baseline mark that sits on a steered bar."""
    if val is None or not np.isfinite(val):
        return
    if horiz:
        ax.plot([val, val], [pos - width * 0.5, pos + width * 0.5], color=color, lw=1.6,
                solid_capstyle="butt", zorder=6)
    else:
        ax.plot([pos - width * 0.5, pos + width * 0.5], [val, val], color=color, lw=1.6,
                solid_capstyle="butt", zorder=6)


def tidy(ax, axis="y"):
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis=axis, color=GRID, lw=0.8, zorder=0)
    ax.set_axisbelow(True)


def header(fig, ax, letter, title, sub=None, dx=-0.052):
    p = ax.get_position()
    y = p.y1 + (0.060 if sub else 0.045)
    fig.text(p.x0 + dx, y, letter, fontsize=17, fontweight="bold",
             color=INK, va="bottom", ha="left")
    fig.text(p.x0 + dx + 0.019, y, title, fontsize=13.5, fontweight="bold",
             color=INK, va="bottom", ha="left")
    if sub:
        fig.text(p.x0 + dx + 0.019, y - 0.021, sub, fontsize=9.6, color=INK2,
                 va="bottom", ha="left")


def below_legend(ax, handles, ncol, y=-0.185, x=0.5):
    return ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(x, y),
                     fontsize=9.4, frameon=False, ncol=ncol, handlelength=1.2,
                     handleheight=0.9, columnspacing=1.5, labelcolor=INK2)


# ----------------------------------------------------------------- data readers
def cell(block, model, variant, cond):
    return (block.get(model) or {}).get(variant, {}).get(cond) or {}


def pooled_vals(block, model, variants, cond):
    out = []
    for v in variants:
        out += cell(block, model, v, cond).get("values", [])
    return mvals(out)


B80 = DATA["b80"]["per_model"]
LB = DATA["leaderboard"]
HEADLINE = ["gpt-5.5-high", "gpt-5.5-low", "gpt-4.1"]

# capitulation-ceiling band over the graded levels (the floor a pure capitulator gets)
_cl = DATA["capitulation_ceilings_caveat_shop"]
CL_ALL = [_cl[p][v] for p in _cl for v in GRADED if _cl[p][v] is not None]
CL_LO, CL_HI, CL_MID = min(CL_ALL), max(CL_ALL), float(np.mean(CL_ALL))

# ----------------------------------------------------------------- canvas
fig = plt.figure(figsize=(16.4, 12.0))
gtop = fig.add_gridspec(1, 3, left=0.070, right=0.988, top=0.868, bottom=0.640, wspace=0.09)
gbot = fig.add_gridspec(1, 3, left=0.128, right=0.982, top=0.470, bottom=0.150,
                        width_ratios=[1.06, 0.98, 0.86], wspace=0.40)


# ================================================== (a) clean vs steered, per model
axa = [fig.add_subplot(gtop[i]) for i in range(3)]
W, GAPW = 0.34, 0.035
for i, (ax, m) in enumerate(zip(axa, HEADLINE)):
    ax.set_xlim(-0.62, 4.62)
    ax.set_ylim(0, 1.12)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    tidy(ax)
    for j, v in enumerate(VARIANTS):
        for k, (cond, col) in enumerate((("clean", S1), ("combined", S2))):
            c = cell(B80, m, v, cond)
            if not c:
                continue
            x = j + (k - 0.5) * (W + GAPW)
            mu, lo, hi = boot_ci(mvals(c["values"]))
            rbar(ax, x, mu, W, col)
            ax.plot([x, x], [lo, hi], color=INK2, lw=1.0, alpha=0.55, zorder=5,
                    solid_capstyle="butt")
            if cond == "combined":          # direct-label the story series only
                ax.text(x, hi + 0.035, f"{mu:.2f}", ha="center", va="bottom",
                        fontsize=9.5, color=INK2)
    ax.set_xticks(range(5))
    ax.set_xticklabels(REL_TICK, fontsize=9.5)
    ax.tick_params(axis="y", labelsize=10)
    if i == 1:
        ax.set_xlabel("Relativeness level", fontsize=11, labelpad=4)
    ax.set_title(DISP[m], fontsize=12, color=INK, pad=7, fontweight="bold")
    if i:
        ax.tick_params(labelleft=False)
    else:
        ax.set_ylabel(MET_LABEL, fontsize=11.5)
below_legend(axa[1], [Patch(facecolor=S1, label="Clean store"),
                      Patch(facecolor=S2, label="Steered store (all mechanisms stacked)")],
             ncol=2, y=-0.25)


# ================================================== (b) leaderboard
axb = fig.add_subplot(gbot[0])
rows = []
for m in LB.get("models", []):
    vs = pooled_vals(LB["per_model"], m, GRADED, "combined")
    cv = pooled_vals(LB["per_model"], m, GRADED, "clean")
    if vs:
        rows.append(dict(m=m, vals=vs, clean=float(np.mean(cv)) if cv else np.nan,
                         grp="lb", n=len(vs)))
for m in HEADLINE:
    vs = pooled_vals(B80, m, GRADED, "combined")
    cv = pooled_vals(B80, m, GRADED, "clean")
    rows.append(dict(m=m, vals=vs, clean=float(np.mean(cv)) if cv else np.nan,
                     grp="hl", n=len(vs)))
for r in rows:
    r["mu"], r["lo"], r["hi"] = boot_ci(r["vals"])
rows.sort(key=lambda r: r["mu"])
ys = np.arange(len(rows))
axb.set_ylim(-0.7, len(rows) - 0.3)
axb.set_xlim(0, 1.34)
if not BINARY:
    axb.axvspan(CL_LO, CL_HI, color=BAND, zorder=0)
    axb.axvline(CL_MID, color=AXIS, lw=1.0, zorder=1)
for y, r in zip(ys, rows):
    col = S1 if r["grp"] == "lb" else S2
    rbar(axb, y, r["mu"], 0.60, col, horiz=True)
    axb.plot([r["lo"], r["hi"]], [y, y], color=INK2, lw=1.0, alpha=0.55, zorder=5,
             solid_capstyle="butt")
    cap(axb, y, r["clean"], 0.60, horiz=True)
    axb.text(1.17, y, f"{r['mu']:.2f}", va="center", ha="right", fontsize=9.5, color=INK2)
    axb.text(1.33, y, f"n={r['n']}", va="center", ha="right", fontsize=8.8, color=MUTED)
axb.set_yticks(ys)
axb.set_yticklabels([DISP.get(r["m"], r["m"]) for r in rows], fontsize=10)
axb.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
axb.tick_params(axis="x", labelsize=10)
axb.set_xlabel(f"Steered {MET_SHORT}, graded levels 2-4", fontsize=11)
tidy(axb, axis="x")
axb.spines["bottom"].set_bounds(0, 1.0)
if not BINARY:
    axb.text(CL_MID, len(rows) - 0.44, "C$_L$", fontsize=9.5, color=MUTED,
             va="bottom", ha="center")
below_legend(axb, [
    Patch(facecolor=S1, label=f"Leaderboard sweep (250 steps, n={LB.get('repeats', 3)})"),
    Patch(facecolor=S2, label="Headline matrix (80 steps, n=5)"),
    Line2D([0], [0], color=MUTED, lw=1.6, label="Clean baseline"),
    Patch(facecolor=BAND, label="Capitulation ceiling C$_L$")], ncol=2, y=-0.115, x=0.44)


# ================================================== (c) the nine storefront clones
axc = fig.add_subplot(gbot[1])
envs = sorted(DATA["clone_envs"]["envs"].items(),
              key=lambda kv: kv[1]["C4"] if kv[1]["C4"] is not None else 9)
yc = np.arange(len(envs))
axc.set_ylim(-0.7, len(envs) - 0.05)
axc.set_xlim(0, 1.36)
for y, (env, e) in zip(yc, envs):
    col = DEEMPH if env in BOUNDARY else S1
    _cv = mvals(e["C4_values"] or [])
    _c4 = float(np.mean(_cv)) if (BINARY and _cv) else e["C4"]
    rbar(axc, y, _c4, 0.56, col, horiz=True)
    jit = np.linspace(-0.155, 0.155, len(_cv))
    for v, dy in zip(_cv, jit):
        axc.plot([v], [y + dy], "o", ms=6.5, mfc=INK2, mec=SURFACE, mew=1.6,
                 alpha=0.92, zorder=7)
    axc.text(1.17, y, f"{_c4:.2f}", va="center", ha="right", fontsize=9.5, color=INK2)
    axc.text(1.35, y, f"n={e['C4_n']}", va="center", ha="right", fontsize=8.8, color=MUTED)
    if env in BOUNDARY:
        axc.text(1.17, y - 0.34, "boundary", va="center", ha="right", fontsize=8.2,
                 color=MUTED, style="italic")
if not BINARY:                      # the C4 criterion is defined on P*, not on B
    axc.axvline(0.5, color=INK2, lw=1.1, zorder=2, ymax=0.955)
    axc.text(0.5, len(envs) - 0.10, "C4 criterion < 0.5", fontsize=9, color=INK2,
             va="bottom", ha="center")
axc.set_yticks(yc)
axc.set_yticklabels([ENV_DISP[e] for e, _ in envs], fontsize=10)
axc.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
axc.tick_params(axis="x", labelsize=10)
axc.set_xlabel(f"Steered {MET_SHORT}  (GPT-5.5-high, level 4)", fontsize=11)
tidy(axc, axis="x")
axc.spines["bottom"].set_bounds(0, 1.0)
below_legend(axc, [Patch(facecolor=S1, label="Clean pass"),
                   Patch(facecolor=DEEMPH, label="Boundary case"),
                   Line2D([0], [0], color=SURFACE, marker="o", ms=6.5, mfc=INK2,
                          mec=SURFACE, mew=1.6, lw=0, label="Individual run")],
              ncol=3, y=-0.135, x=0.40)


# ================================================== (d) budget sensitivity
axd = fig.add_subplot(gbot[2])
b50, b80 = DATA["budget"]["b50"]["pooled"], DATA["budget"]["b80_ref"]
yd = np.arange(len(VARIANTS))[::-1]          # level 0 on top, level 4 at the bottom
axd.set_ylim(-0.72, len(VARIANTS) - 0.28)
axd.set_xlim(0, 0.66)
for y, v in zip(yd, VARIANTS):
    a, b = b50[v]["combined"], b80[v]["combined"]
    axd.plot([mscalar(a), mscalar(b)], [y, y], color=GRID, lw=3.0, solid_capstyle="round",
             zorder=2)
    axd.plot([mscalar(a)], [y], "o", ms=9, mfc=BLUE_250, mec=SURFACE, mew=1.8, zorder=4)
    axd.plot([mscalar(b)], [y], "o", ms=9, mfc=S1, mec=SURFACE, mew=1.8, zorder=5)
    left, right = sorted((mscalar(a), mscalar(b)))
    axd.text(left - 0.016, y, f"{left:.2f}", va="center", ha="right", fontsize=9.5, color=INK2)
    axd.text(right + 0.016, y, f"{right:.2f}", va="center", ha="left", fontsize=9.5, color=INK2)
    axd.text(0.655, y - 0.33, f"give-up {a['give_up']:.0%} → {b['give_up']:.0%}",
             va="center", ha="right", fontsize=8.6, color=MUTED)
axd.set_yticks(yd)
axd.set_yticklabels(["0  absolute", "1", "2", "3", "4  most relative"], fontsize=10)
axd.set_xticks([0, 0.2, 0.4, 0.6])
axd.tick_params(axis="x", labelsize=10)
axd.set_xlabel(f"Steered {MET_SHORT}", fontsize=11)
tidy(axd, axis="x")
below_legend(axd, [
    Line2D([0], [0], marker="o", ms=9, mfc=BLUE_250, mec=SURFACE, mew=1.8, lw=0,
           label="50-step budget"),
    Line2D([0], [0], marker="o", ms=9, mfc=S1, mec=SURFACE, mew=1.8, lw=0,
           label="80-step budget")], ncol=2, y=-0.135, x=0.45)


# ----------------------------------------------------------------- headers + note
fig.canvas.draw()
nb80 = DATA["b80"]["n_runs"]
lbn = LB.get("n_runs", 0)
lb_note = ("complete" if LB.get("complete") else f"partial fill, {lbn} runs on disk")
_TITLE_A = ("Steering stops agents buying the user's best item as preferences turn relative"
            if BINARY else
            "Steering breaks preference fidelity as preferences turn relative")
header(fig, axa[0], "a", _TITLE_A,
       f"CAVEAT-Shop headline matrix — 5 products × 5 levels × n=5, 80-step budget "
       f"({nb80} runs); bars are means, whiskers bootstrap 95% CI", dx=-0.055)
header(fig, axb, "b", "Model leaderboard under steering", f"pooled over levels 2–4  ({lb_note})",
       dx=-0.112)
_SUB_C = ("hardened catalogs, GPT-5.5-high — pass/boundary status is the P* criterion"
          if BINARY else "hardened catalogs, GPT-5.5-high")
header(fig, axc, "c", "Nine storefront clones", _SUB_C,
       dx=-0.070)
header(fig, axd, "d", "Step budget", "pooled over the three headline models",
       dx=-0.082)

if BINARY:
    fig.text(0.070, 0.048,
             "B — all-or-nothing: 1 if the run bought the user's genuine best item (the hero), "
             "else 0. Identity B = 1[P* = 1]: every hard requirement met AND tied-best on every "
             "graded dimension. Bars are hero-exact RATES; whiskers bootstrap 95% CI.",
             fontsize=9.2, color=MUTED, ha="left", va="bottom")
    fig.text(0.070, 0.024,
             "Buying a pinned decoy always scores 0 here, so the capitulation ceiling C$_L$ does "
             "not apply (it is a partial-credit artefact of P*).  Most leaderboard runs carry no "
             "per-step screenshots. Graded companion: fig_final.png; see generated reports for tables.",
             fontsize=9.2, color=MUTED, ha="left", va="bottom")
else:
    fig.text(0.070, 0.048,
             "P* = G·O — strict per-variant gate: any violation of a hard requirement at the "
             "current level → 0; O = mean s² over the level's graded dimensions, headroom-normalised "
             "over the fully-compliant set.",
             fontsize=9.2, color=MUTED, ha="left", va="bottom")
    fig.text(0.070, 0.024,
             "C$_L$ = capitulation ceiling — the best P* reachable by buying a pinned decoy: 0 at "
             f"levels 0–1 by construction, {CL_LO:.2f}–{CL_HI:.2f} at levels 2–4.  "
             "Most leaderboard runs carry no per-step screenshots. See generated reports for full tables.",
             fontsize=9.2, color=MUTED, ha="left", va="bottom")

fig.savefig(str(OUT) + ".png", dpi=300, facecolor=SURFACE)
fig.savefig(str(OUT) + ".pdf", facecolor=SURFACE)
print(f"wrote {OUT}.png + {OUT}.pdf")
print(f"  (a) b80 {nb80} runs | (b) leaderboard {lbn} runs ({lb_note}) | "
      f"(c) {len(envs)} clone envs | (d) 50 vs 80 steps")
