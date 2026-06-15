#!/usr/bin/env python
"""Generalized graded-N spectrum figure (hand-rolled SVG, no matplotlib).

2 rows (Clean / Steered) x N variant columns (the graded-N spectrum), grouped bars = the SERIES
being compared (models / reasoning-efforts / scales / families / vintages). Each bar is mean
preservation P with a 95% bootstrap CI whisker; the value sits above the upper cap. Non-bold
Helvetica, white-haloed value labels, faint P=1.0 ceiling. The same clean template is reused for
every Phase-2 sweep — only the `series` + colours change.

Usage:
    python scripts/spectrum_fig.py CONFIG.json OUT.svg
CONFIG = {
  "title": "...", "xlabel": "...",
  "variants": [["thresholded","0"], ["mixed","1"], ["graded","2"], ["graded3","3"], ["graded4","4"]],
  "condition": "combined",                # which steered condition is the bottom row (default combined)
  "series": [ {"label":"gpt-4.1", "globs":["results/lap_gN3_g41_r*"], "model":"gpt-4.1",
               "color":"#e8743b"}, ... ]
}
"""
import json, glob, sys, random
from collections import defaultdict

random.seed(7)
cfg = json.load(open(sys.argv[1]))
OUT = sys.argv[2]
VARIANTS = cfg["variants"]                       # [[key,label], ...]
SERIES = cfg["series"]
STEER = cfg.get("condition", "combined")
mean = lambda xs: sum(xs) / len(xs) if xs else float("nan")

def boot_ci(vals, B=4000):
    if not vals: return (float("nan"),) * 3
    m = mean(vals)
    if max(vals) - min(vals) < 1e-9: return (m, m, m)
    bs = sorted(sum(random.choice(vals) for _ in vals) / len(vals) for _ in range(B))
    return m, bs[int(0.025 * B)], bs[int(0.975 * B)]

# data[(si, vkey, cond)] = [P,...]
data = defaultdict(list)
for si, s in enumerate(SERIES):
    seen = set()
    for g in s["globs"]:
        for sm in glob.glob(g + "/*/summary.json"):
            try: d = json.load(open(sm))
            except Exception: continue
            if s.get("model") and d.get("model") != s["model"]: continue
            if s.get("scaffold") and d.get("scaffold") != s["scaffold"]: continue
            tid = d.get("task_id", "")
            if s.get("scenario") and tid.rsplit("-", 1)[0] != s["scenario"]: continue
            p = d.get("preservation")
            if not isinstance(p, (int, float)): continue
            vk = tid.split("-", 1)[1] if "-" in tid else tid
            key = (d.get("seconds"), p, vk, d.get("condition"))
            if key in seen: continue
            seen.add(key)
            data[(si, vk, d.get("condition"))].append(p)

# ---------------- style ----------------
FONT = "Helvetica, 'Helvetica Neue', Arial, sans-serif"
INK, MUTE, FAINT, BASE = "#222222", "#6b6b6b", "#e2e2e2", "#c9c9c9"
ROWS = [("clean", "Clean"), (STEER, "Steered")]
nV, nS = len(VARIANTS), len(SERIES)
# per-series extra left-gap (family grouping) and fill-opacity (effort/scale/visibility tints)
GAPB = [float(s.get("gap_before", 0)) for s in SERIES]
ALPHA = [float(s.get("alpha", 1.0)) for s in SERIES]
_EXTRA = sum(GAPB)
LGUT = 116
COLW, COLGAP = max(150, 70 + nS * 26 + int(_EXTRA)), 16
TOPHDR = 104
ROWH = 188
ROW_TOP = [TOPHDR + i * ROWH for i in range(2)]
COL_X = [LGUT + j * (COLW + COLGAP) for j in range(nV)]
W = COL_X[-1] + COLW + 24
H = ROW_TOP[-1] + ROWH + 40
PLOT_H, BASE_OFF = 126, 156
BW = min(46, int((COLW - 24 - _EXTRA) / nS) - 8)
BGAP = 8

s = []
add = s.append
def txt(x, y, t, size, col=INK, anchor="middle", halo=False):
    h = ' paint-order="stroke" stroke="#fff" stroke-width="3" stroke-linejoin="round"' if halo else ""
    add(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{col}" text-anchor="{anchor}"{h}>{t}</text>')
def line(x1, y1, x2, y2, col, w=1.0, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{col}" stroke-width="{w}"{d}/>')

add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}">')
add(f'<rect width="{W}" height="{H}" fill="#fff"/>')
if cfg.get("title"):
    txt(W / 2, 26, cfg["title"], 20, INK, "middle")
# legend (top) — item width adapts to the longest label, but is capped to fit the canvas
_lblw = int(max(len(s["label"]) for s in SERIES) * 8.8) + 38
itemw = max(96, min(_lblw, int((W - 24) / nS)))
_lfs = 16 if itemw >= 150 else (14 if itemw >= 120 else 12)
lx = W / 2 - (nS * itemw) / 2 + 12
for k, ser in enumerate(SERIES):
    x = lx + k * itemw
    add(f'<rect x="{x:.1f}" y="40" width="16" height="16" rx="4" fill="{ser["color"]}" fill-opacity="{ALPHA[k]:.2f}"/>')
    txt(x + 22, 53, ser["label"], _lfs, INK, "start")
# column headers (variant labels)
for j, (_, vlab) in enumerate(VARIANTS):
    txt(COL_X[j] + COLW / 2, 92, vlab, 19, INK, "middle")
if cfg.get("xlabel"):
    txt(W / 2, H - 10, cfg["xlabel"], 15, MUTE, "middle")
# row labels
for i, (_, rlab) in enumerate(ROWS):
    txt(LGUT - 16, ROW_TOP[i] + BASE_OFF / 2 + 5, rlab, 20, INK, "end")
# cells
for i, (ckey, _) in enumerate(ROWS):
    cy = ROW_TOP[i]; base_y = cy + BASE_OFF; ceil_y = base_y - PLOT_H
    for j, (vkey, _) in enumerate(VARIANTS):
        cx = COL_X[j]
        line(cx + 12, ceil_y, cx + COLW - 12, ceil_y, FAINT, 1.0, dash="2 4")
        line(cx + 12, base_y, cx + COLW - 12, base_y, BASE, 1.2)
        grp = nS * BW + (nS - 1) * BGAP + _EXTRA
        x0 = cx + (COLW - grp) / 2
        xcur = x0
        for si, ser in enumerate(SERIES):
            xcur += GAPB[si]
            bx = xcur
            xcur += BW + BGAP
            mu, lo, hi = boot_ci(data.get((si, vkey, ckey), []))
            if mu != mu:
                # no completed cells for this (series, variant, condition): mark "n/a" so a missing
                # data point (cell returned `none` / not collected) is visibly DISTINCT from a genuine
                # P≈0 (which draws a flat filled bar labelled 0.00). A small dashed hollow stub at the
                # baseline + a muted label, in the chart's grey palette (harmonious, unobtrusive).
                sh = 11
                add(f'<rect x="{bx:.1f}" y="{base_y - sh:.1f}" width="{BW}" height="{sh}" rx="3" '
                    f'fill="none" stroke="{BASE}" stroke-width="1.1" stroke-dasharray="3 3"/>')
                txt(bx + BW / 2, base_y - sh - 7, "n/a", 11, MUTE, "middle")
                continue
            by = base_y - mu * PLOT_H
            add(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{BW}" height="{mu*PLOT_H:.1f}" rx="3" fill="{ser["color"]}" fill-opacity="{ALPHA[si]:.2f}"/>')
            top = by
            if hi - lo > 1e-6:
                wx = bx + BW / 2
                line(wx, base_y - lo * PLOT_H, wx, base_y - hi * PLOT_H, INK, 1.3)
                line(wx - 4, base_y - lo * PLOT_H, wx + 4, base_y - lo * PLOT_H, INK, 1.3)
                line(wx - 4, base_y - hi * PLOT_H, wx + 4, base_y - hi * PLOT_H, INK, 1.3)
                top = base_y - hi * PLOT_H
            txt(bx + BW / 2, top - 8, f"{mu:.2f}", 13, INK, "middle", halo=True)
add('</svg>')
open(OUT, "w").write("\n".join(s))
# stats to stderr
for si, ser in enumerate(SERIES):
    row = " ".join(f"{vk}={mean(data.get((si,vk,STEER),[])):.2f}" for vk, _ in VARIANTS)
    sys.stderr.write(f"{ser['label']:14} steered: {row}\n")
sys.stderr.write(f"wrote {OUT} ({W}x{H})\n")
