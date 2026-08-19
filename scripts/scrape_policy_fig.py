"""Scrape-policy figure — the anti-scrape axis experiment (results/scrape_v1, 2026-07-15).

Panel-(a)-style bars (mirrors scripts/final_fig.py): x = the two models, and within each
group an alpha-ramped bar per anti-scrape level (easy -> hardest), exactly as panel (a)
ramps alpha over relativeness. Steered (combined) graded4 strict P*, valid-only, bootstrap
95% CIs, n=15 cells/bar (5 scenarios x 3 reps, max-steps 80 across ALL levels so the
scrape budget is the only variable; "medium" is the main-matrix baseline budget of 25,
re-measured under this protocol rather than reused from mm_v1's 32-step run).

Data: benchmark_data/reports/scrape_policy_data.json (built by fold_scrape_policy.py) —
kept separate from figure_data.json because the step protocol differs.
"""
import json
import random
from collections import defaultdict
from statistics import mean

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import numpy as np                        # noqa: E402

random.seed(7)

SNAP = json.load(open("benchmark_data/reports/scrape_policy_data.json"))

# anti-scrape ladder, loosest -> strictest: the session spec-sheet budget (distinct products
# whose full spec prose the detail API serves; see AmazonEnvironment._SCRAPE_BUDGETS)
LEVELS = [
    ("combined-scrape-easy", "Easy\n(∞)"),
    ("combined", "Medium\n(25)"),
    ("combined-scrape-hard", "Hard\n(8)"),
    ("combined-scrape-hardest", "Hardest\n(2)"),
]
MODELS = [
    ("gpt-5.6-sol-high", "GPT-5.6-Sol (high)"),
    ("gpt-5.5-high", "GPT-5.5 (high)"),
]
VARIANT = "graded4"


def cells(model, cond):
    valid, nones = [], 0
    for c in SNAP:
        if c.get("model") != model or c.get("condition") != cond:
            continue
        if not c.get("task_id", "").endswith(f"-{VARIANT}"):
            continue
        if c.get("outcome") == "none":
            nones += 1
        elif isinstance(c.get("preservation_strict"), (int, float)):
            valid.append(c["preservation_strict"])
    return valid, nones


def boot_ci(xs, B=3000):
    if not xs:
        return (float("nan"),) * 3
    point = mean(xs)
    bs = sorted(mean([random.choice(xs) for _ in xs]) for _ in range(B))
    return point, bs[int(0.025 * B)], bs[int(0.975 * B)]


# ---- style (copied from final_fig.py) ---------------------------------------
plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
    "svg.fonttype": "none", "pdf.fonttype": 42,
    "axes.linewidth": 1.1, "axes.edgecolor": "#333333",
    "xtick.color": "#333333", "ytick.color": "#333333",
    "text.color": "#1a1a1a", "axes.labelcolor": "#1a1a1a",
})
INK = "#1a1a1a"
AGGBLUE = "#2E6CA8"                      # OpenAI-family blue, as in panel (a)

fig, ax = plt.subplots(figsize=(10.5, 7))
xs = np.arange(len(MODELS))
WV, GAP = 0.19, 0.015

for i in range(len(MODELS)):
    if i % 2:
        ax.axvspan(i - 0.5, i + 0.5, color="#f5f7f9", zorder=0)

bar_xs, bar_lbls = [], []
for i, (mname, disp) in enumerate(MODELS):
    start = xs[i] - (len(LEVELS) * (WV + GAP) - GAP) / 2
    for j, (cond, lbl) in enumerate(LEVELS):
        valid, nones = cells(mname, cond)
        mu, lo, hi = boot_ci(valid)
        if mu != mu:
            continue
        x = start + WV / 2 + j * (WV + GAP)
        bar_xs.append(x); bar_lbls.append(lbl)
        ax.bar(x, mu, WV, color=AGGBLUE, edgecolor="none", zorder=3)
        ax.plot([x, x], [lo, hi], color=INK, lw=1.0, alpha=0.6, zorder=5)
        ax.text(x, mu + 0.035 if mu < 0.12 else mu / 2, f"{mu:.2f}", rotation=90,
                ha="center", va="center" if mu >= 0.12 else "bottom", fontsize=12,
                color="white" if mu >= 0.12 else INK, fontweight="bold", zorder=9)

ax.axhline(1.0, color="#b0b0b0", lw=1.0, ls=(0, (2, 3)), zorder=1)
# per-bar anti-scrape-level tick labels; model names sit below as group labels
ax.set_xticks(bar_xs)
ax.set_xticklabels(bar_lbls, fontsize=11.5)
ax.tick_params(axis="x", pad=6, length=0)
for i, (_, disp) in enumerate(MODELS):
    ax.text(xs[i], -0.155, disp, ha="center", va="top", fontsize=16,
            fontweight="bold", color=AGGBLUE, clip_on=False)
ax.set_ylabel("Preference fidelity", fontsize=17)
ax.set_ylim(0, 1.12)
ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
ax.tick_params(axis="y", labelsize=14)
ax.set_xlim(-0.55, len(MODELS) - 0.45)
ax.spines[["top", "right"]].set_visible(False)
ax.grid(axis="y", color="#e8e8e8", lw=0.8, zorder=0)
ax.set_axisbelow(True)

fig.tight_layout()
fig.savefig("benchmark_data/reports/fig_scrape_policy.png", dpi=200)
fig.savefig("benchmark_data/reports/fig_scrape_policy.pdf")
print("wrote benchmark_data/reports/fig_scrape_policy.{png,pdf}")
