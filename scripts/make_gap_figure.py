#!/usr/bin/env python
"""Nature-style 2x3 small-multiples figure (hand-rolled SVG, no matplotlib).

Grid: rows = {Clean, Steered}; columns = preference type {Absolute, Mixed, Relative}.
Each cell is a 2-bar chart — left gpt-5.5, right gpt-4.1 — of preservation P (0-1), with a 95%
bootstrap CI whisker and the value above the upper cap. No title; light/non-bold Helvetica; the two
models are one hue at two tints (same colour, different "alpha").

Usage: python scripts/make_gap_figure.py [run] [results_dir] > out.svg   (stats -> stderr)
"""
import json, glob, sys, random
from collections import defaultdict

NAME = sys.argv[1] if len(sys.argv) > 1 else "lap_v14"
ROOT = sys.argv[2] if len(sys.argv) > 2 else "results"
random.seed(7)

P = defaultdict(list)
seen = set()
for pat in (f"{ROOT}/{NAME}/*/summary.json", f"{ROOT}/{NAME}_r*/*/summary.json"):
    for sm in glob.glob(pat):
        try: d = json.load(open(sm))
        except Exception: continue
        p = d.get("preservation")
        if not isinstance(p, (int, float)): continue
        tid = d.get("task_id", ""); var = tid.split("-", 1)[1] if "-" in tid else tid
        rp = (d.get("seconds"), p, d.get("model"), var, d.get("condition"))
        if rp in seen: continue
        seen.add(rp)
        P[(d.get("model"), var, d.get("condition"))].append(p)

MODELS = [m for m in ("gpt-5.5", "gpt-4.1") if any(k[0] == m for k in P)]
# columns = preference softness (display wording); rows = store condition
COLS = [("thresholded", "Absolute"), ("mixed", "Mixed"), ("graded", "Relative")]
ROWS = [("clean", "Clean"), ("combined", "Steered")]
mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")

def mean_ci(vals, B=4000):
    if not vals: return (float("nan"), float("nan"), float("nan"))
    m = mean(vals)
    if max(vals) - min(vals) < 1e-9: return (m, m, m)
    bs = sorted(sum(random.choice(vals) for _ in vals) / len(vals) for _ in range(B))
    return m, bs[int(0.025 * B)], bs[int(0.975 * B)]

# ---------------- style ----------------
FONT = "Helvetica, 'Helvetica Neue', Arial, sans-serif"
# one hue, two tints: gpt-5.5 = full teal, gpt-4.1 = the same teal at ~50% (lighter "alpha")
COL = {"gpt-5.5": "#0f766e", "gpt-4.1": "#87bab4"}
INK, MUTE, FAINT, BASE = "#222222", "#6b6b6b", "#e2e2e2", "#c9c9c9"

# geometry  (2 rows x 3 cols)
LGUT = 124                          # left gutter for row labels
COLW, COLGAP = 200, 20
TOPHDR = 104                        # space for legend + column headers
ROWH = 198
ROW_TOP = [TOPHDR + i * ROWH for i in range(len(ROWS))]
COL_X = [LGUT + j * (COLW + COLGAP) for j in range(len(COLS))]
W = COL_X[-1] + COLW + 24
H = ROW_TOP[-1] + ROWH + 20
PLOT_H = 132                        # P=1.0 bar height
BASE_OFF = 162                      # baseline below cell top
BW, BGAP = 58, 26

s = []
def add(x): s.append(x)
def txt(x, y, t, size, col=INK, anchor="middle", halo=False):
    h = ' paint-order="stroke" stroke="#ffffff" stroke-width="3" stroke-linejoin="round"' if halo else ""
    add(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{col}" text-anchor="{anchor}"{h}>{t}</text>')
def line(x1, y1, x2, y2, col, w=1.0, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{col}" stroke-width="{w}"{d}/>')

add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
    f'font-family="{FONT}">')
add(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')

# legend (top, centered) — two tints of one hue
lab = [(m, COL[m]) for m in MODELS]
itemw = 132
lx = W/2 - (len(lab) * itemw) / 2 + 18
for k, (m, c) in enumerate(lab):
    x = lx + k * itemw
    add(f'<rect x="{x:.1f}" y="26" width="19" height="19" rx="4" fill="{c}"/>')
    txt(x + 27, 42, m, 19, INK, "start")

# column headers (preference type)
for j, (_, clab) in enumerate(COLS):
    txt(COL_X[j] + COLW / 2, 86, clab, 23, INK, "middle")

# row labels (store condition)
for i, (_, rlab) in enumerate(ROWS):
    txt(LGUT - 18, ROW_TOP[i] + BASE_OFF / 2 + 5, rlab, 23, INK, "end")

# cells
for i, (ckey, _) in enumerate(ROWS):
    cy = ROW_TOP[i]
    base_y = cy + BASE_OFF
    ceil_y = base_y - PLOT_H
    for j, (vkey, _) in enumerate(COLS):
        cx = COL_X[j]
        line(cx + 16, ceil_y, cx + COLW - 16, ceil_y, FAINT, 1.0, dash="2 4")   # P=1.0 ceiling
        line(cx + 16, base_y, cx + COLW - 16, base_y, BASE, 1.2)                 # P=0 baseline
        start_x = cx + (COLW - (len(MODELS) * BW + (len(MODELS) - 1) * BGAP)) / 2
        for k, m in enumerate(MODELS):
            mu, lo, hi = mean_ci(P.get((m, vkey, ckey), []))
            if mu != mu:
                continue
            bx = start_x + k * (BW + BGAP)
            by = base_y - mu * PLOT_H
            add(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{BW}" height="{mu*PLOT_H:.1f}" rx="3" fill="{COL[m]}"/>')
            top = by
            if hi - lo > 1e-6:                  # CI whisker
                wx = bx + BW / 2
                line(wx, base_y - lo * PLOT_H, wx, base_y - hi * PLOT_H, INK, 1.4)
                line(wx - 5, base_y - lo * PLOT_H, wx + 5, base_y - lo * PLOT_H, INK, 1.4)
                line(wx - 5, base_y - hi * PLOT_H, wx + 5, base_y - hi * PLOT_H, INK, 1.4)
                top = base_y - hi * PLOT_H       # value label clears the upper cap
            txt(bx + BW / 2, top - 12, f"{mu:.2f}", 19, INK, "middle", halo=True)

add('</svg>')
sys.stdout.write("\n".join(s))
for ckey, clab in ROWS:
    for vkey, vlab in COLS:
        for m in MODELS:
            mu, lo, hi = mean_ci(P.get((m, vkey, ckey), []))
            sys.stderr.write(f"{clab:8} {vlab:9} {m:8} P={mu:.3f} CI[{lo:.3f},{hi:.3f}]\n")
