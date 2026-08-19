#!/usr/bin/env python
"""Figure: adversarial agent-targeted injection collapses gpt-5.5-low preference fidelity to ~0.

Two conditions (CLEAN vs the invisible AI-INJECTION layer), identical human-visible store, gpt-5.5
low effort, laptop/graded4, browser-use. Per condition: aggregate preference fidelity under BOTH
the zero-dominant vgeo metric, none/error/off = 0, bootstrap 95% CI.
Reads results/adv_v1* live via the frozen strict_variants scorer. Repo slide style.

  .venv/bin/python scripts/fig_adv.py
"""
import glob
import json
import os
import random
import sys

sys.path.insert(0, __file__.rsplit("scripts/", 1)[0] or ".")  # repo root (portable: works from any checkout)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))            # scripts/ (shared rule)
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe

from agentarena.scoring.strict_variants import cell_variants
# Infra-vs-capability is decided by the ONE shared implementation in scripts/_infra_classify.py
# (build_figure_data.py and _crash_sweep.py import the same module).  Only a run INFRASTRUCTURE
# terminated is excluded; unparseable output, give-ups and loops stay in and score 0.
from _infra_classify import is_infra_fail

random.seed(7)
PREFIX = "results/adv_v1"


def collect():
    out = {"clean": {"vgeo": []}, "ai-injection": {"vgeo": []}}
    for tj in sorted(glob.glob(f"{PREFIX}*/amazon__*/trajectory.json")):
        cond = os.path.basename(os.path.dirname(tj)).split("__")[-1]
        if cond not in out:
            continue
        d = json.load(open(tj))
        if is_infra_fail(os.path.dirname(tj)):
            continue
        _c, m = cell_variants(tj)                       # none/error -> None -> scored 0
        out[cond]["vgeo"].append((m or {}).get("vgeo") or 0.0)
    return out


def boot(xs, B=4000):
    if not xs:
        return (float("nan"),) * 3
    mean = lambda a: sum(a) / len(a)
    bs = sorted(mean([random.choice(xs) for _ in xs]) for _ in range(B))
    return mean(xs), bs[int(0.025 * B)], bs[int(0.975 * B)]


D = collect()
n_clean = len(D["clean"]["vgeo"])
n_adv = len(D["ai-injection"]["vgeo"])

plt.rcParams.update({
    "font.family": "sans-serif", "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"],
    "svg.fonttype": "none", "pdf.fonttype": 42,
    "axes.linewidth": 1.1, "axes.edgecolor": "#333333",
    "xtick.color": "#333333", "ytick.color": "#333333",
    "text.color": "#1a1a1a", "axes.labelcolor": "#1a1a1a",
})
INK = "#1a1a1a"
CLEANBLUE = "#2E6CA8"
ADVRED = "#B23A48"
METRICS = [("vgeo", "Preference fidelity  (vgeo)")]

fig, ax0 = plt.subplots(1, 1, figsize=(5.4, 5.2))
axes = [ax0]
groups = [("clean", "clean\nstore", CLEANBLUE, n_clean),
          ("ai-injection", "invisible\nagent-injection", ADVRED, n_adv)]
for ax, (mkey, mlabel) in zip(axes, METRICS):
    ax.axhline(1.0, color="#b0b0b0", lw=1.0, ls=(0, (2, 3)), zorder=1)
    for i, (cond, lab, col, n) in enumerate(groups):
        mu, lo, hi = boot(D[cond][mkey])
        ax.bar(i, mu, 0.62, color=col, edgecolor="white", linewidth=0.6, zorder=3)
        ax.plot([i, i], [lo, hi], color=INK, lw=1.1, alpha=0.6, zorder=5)
        yl = mu + 0.035 if mu < 0.12 else mu / 2
        va, tc = ("bottom", INK) if mu < 0.12 else ("center", "white")
        ax.text(i, yl, f"{mu:.3f}", ha="center", va=va, fontsize=13, color=tc, fontweight="bold",
                zorder=9, path_effects=[pe.Stroke(linewidth=2.6, foreground="white"), pe.Normal()]
                if tc == INK else None)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels([g[1] for g in groups], fontsize=12)
    ax.tick_params(axis="x", length=0)
    ax.set_xlim(-0.7, len(groups) - 0.3)
    ax.set_ylim(0, 1.08)
    ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.tick_params(axis="y", labelsize=12)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", color="#e8e8e8", lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    ax.set_title(mlabel, fontsize=13, fontweight="bold", pad=10)
axes[0].set_ylabel("gpt-5.5 (low) — laptop / graded4  ·  preference fidelity (vgeo)", fontsize=12)
fig.suptitle(f"Invisible agent-targeted injection collapses fidelity to ~0   (n={n_adv})",
             fontsize=13.5, fontweight="bold", x=0.54, y=0.985)
fig.subplots_adjust(left=0.11, right=0.97, top=0.85, bottom=0.11, wspace=0.07)
for ext in ("png", "pdf"):
    fig.savefig(f"benchmark_data/reports/fig_adv_injection.{ext}", dpi=300, bbox_inches="tight")
print(f"wrote benchmark_data/reports/fig_adv_injection.png/.pdf  | clean n={n_clean} adv n={n_adv}")
print("clean :", boot(D['clean']['vgeo']))
print("adv   :", boot(D['ai-injection']['vgeo']))
