#!/usr/bin/env python
"""Compose the 9 environment homepage screenshots into one labeled grid figure.

Reads committed shots from benchmark_data/reports/env_shots/<env>.png and writes
benchmark_data/reports/fig_environments.png — a 3x3 grid (the 8 harvested brand clones +
the generated Amazon benchmark; zillow excluded), each tile titled with the brand name only,
brand colour-coded. Sized 16:9 to fill a PowerPoint slide (no suptitle / no vertical
descriptions — slide layout, 2026-07-15); each shot is bottom-cropped IN-PLOT to the tile's
box aspect so tiles fill edge-to-edge without distortion (source shots untouched).

To refresh the shots: run `python scripts/shoot_envs.py` (launches each env headless and
captures its homepage to /tmp/env_shots), then copy into the committed folder.
"""
from pathlib import Path
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

SHOTS = Path("benchmark_data/reports/env_shots")
# (file, Brand, vertical, brand colour)
TILES = [
    ("amazon",    "Amazon",    "general retail",        "#ff9900"),
    ("ebay",      "eBay",      "auction / resale",      "#e53238"),
    ("etsy",      "Etsy",      "handmade goods",        "#f1641e"),
    ("stockx",    "StockX",    "sneaker resale",        "#006340"),
    ("nike",      "Nike",      "athletic footwear",     "#111111"),
    ("doordash",  "DoorDash",  "food delivery",         "#ff3008"),
    ("instacart", "Instacart", "grocery delivery",      "#43b02a"),
    ("airbnb",    "Airbnb",    "vacation rentals",      "#ff5a5f"),
    ("fiverr",    "Fiverr",    "freelance services",    "#1dbf73"),
]
NCOL, NROW = 3, 3
# 16:9 — fills a widescreen PowerPoint slide (13.333 x 7.5 in)
fig, axes = plt.subplots(NROW, NCOL, figsize=(13.333, 7.5))
fig.subplots_adjust(left=0.004, right=0.996, top=0.962, bottom=0.006, wspace=0.02, hspace=0.14)
FW, FH = fig.get_size_inches()

for ax, (f, brand, vert, col) in zip(axes.ravel(), TILES):
    img = mpimg.imread(SHOTS / f"{f}.png")
    # crop (bottom only) to the tile box's aspect so imshow(aspect='auto') fills the tile
    # edge-to-edge with ZERO distortion; the header/hero at the top of every shot survives.
    pos = ax.get_position()
    target = (pos.width * FW) / (pos.height * FH)
    h, w = img.shape[:2]
    if w / h < target:
        img = img[: int(round(w / target)), :]
    else:
        img = img[:, : int(round(h * target))]
    ax.imshow(img, aspect="auto")
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_edgecolor("#d6d6d6"); sp.set_linewidth(1.0)
    ax.set_title(f"{brand}", fontsize=17, fontweight="bold", color=col, pad=4, loc="left", x=0.005)

OUT = Path("benchmark_data/reports/fig_environments")
fig.savefig(str(OUT) + ".png", dpi=200, facecolor="white")
fig.savefig(str(OUT) + ".pdf", facecolor="white")
print("wrote", OUT)
