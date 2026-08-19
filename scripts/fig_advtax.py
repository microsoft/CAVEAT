#!/usr/bin/env python
"""Figure: the adversarial agent-targeted steering taxonomy.

One horizontal bar per family, grouped by the layer of the agent's decision loop it attacks,
against the clean baseline. Metric is vgeo at the fully-absolute (`thresholded`) preference, where
a run scores 1 only if the purchased item meets ALL seven stated requirements — so a bar is
literally "fraction of runs that bought a conforming item". Bootstrap 95% CI. Repo slide style.

  .venv/bin/python scripts/fig_advtax.py [results/advtax_v1]
"""
import json
import random
import subprocess
import sys

sys.path.insert(0, __file__.rsplit("scripts/", 1)[0] or ".")  # repo root (portable: works from any checkout)
import matplotlib as mpl
mpl.use("Agg")
import matplotlib.pyplot as plt

random.seed(7)
PREFIX = sys.argv[1] if len(sys.argv) > 1 else "results/advtax_v1"

LABEL = {
    "clean": "clean storefront (baseline)",
    "adv-hidden": "visually-nulled text injection",
    "adv-apighost": "consumption-channel cloaking",
    "adv-subllm": "extraction-channel payload",
    "adv-suppress": "selective truth suppression",
    "adv-metrology": "metrological framing",
    "adv-flood": "observation-window flooding",
    "adv-promptfmt": "control-frame forgery",
    "adv-filter": "corrupted verification affordances",
    "adv-precomputed": "computation substitution",
    "adv-costblind": "budget-integrity attack",
    "adv-budget": "verification-cost asymmetry",
    "adv-principal": "forged principal state",
    "adv-policy": "automation-policy framing",
    "adv-consensus": "machine-directed social proof",
    "adv-all": "all deniable families stacked",
    "adv-exec": "transaction substitution",
}
GROUPS = [
    ("", ["clean"]),
    ("1  channel asymmetry", ["adv-hidden", "adv-apighost", "adv-subllm", "adv-suppress"]),
    ("2  comprehension", ["adv-metrology", "adv-flood", "adv-promptfmt"]),
    ("3  delegated verification",
     ["adv-filter", "adv-precomputed", "adv-costblind", "adv-budget"]),
    ("4  principal & authority", ["adv-principal", "adv-policy", "adv-consensus"]),
    ("combined", ["adv-all"]),
    ("beyond deniable", ["adv-exec"]),
]
CLR = {"": "#4b5563", "1  channel asymmetry": "#1f77b4", "2  comprehension": "#2ca02c",
       "3  delegated verification": "#d62728", "4  principal & authority": "#9467bd",
       "combined": "#111827", "beyond deniable": "#8c8c8c"}


def main() -> int:
    subprocess.run([sys.executable, "scripts/score_advtax.py", PREFIX],
                   check=True, capture_output=True)
    table = json.load(open(f"{PREFIX.rstrip('/')}_table.json"))

    rows, colors, ticks = [], [], []
    for gname, fams in GROUPS:
        present = [f for f in fams if f in table and table[f]["n"]]
        if not present:
            continue
        if rows:
            rows.append(None)                      # spacer between groups
            colors.append(None)
            ticks.append("")
        for i, f in enumerate(present):
            t = table[f]
            rows.append((LABEL.get(f, f), t["pooled"], t["lo"], t["hi"], t["n"]))
            colors.append(CLR[gname])
            ticks.append(f"{gname}" if i == 0 and gname else "")

    n = len(rows)
    fig, ax = plt.subplots(figsize=(11.5, 0.42 * n + 1.6), dpi=220)
    ys = list(range(n))[::-1]
    for y, r, c in zip(ys, rows, colors):
        if r is None:
            continue
        _lab, v, lo, hi, cnt = r
        ax.barh(y, v, height=0.66, color=c, zorder=3)
        ax.plot([lo, hi], [y, y], color="#111827", lw=1.4, zorder=4, solid_capstyle="butt")
        txt = f"{v:.2f}"
        inside = v > 0.16
        ax.text(v - 0.015 if inside else v + 0.02, y, txt, va="center",
                ha="right" if inside else "left", fontsize=10.5, zorder=5,
                color="white" if inside else "#111827", fontweight="bold")
        ax.text(1.015, y, f"n={cnt}", va="center", ha="left", fontsize=8, color="#6b7280")

    ax.set_yticks([y for y, r in zip(ys, rows) if r is not None])
    ax.set_yticklabels([r[0] for r in rows if r is not None], fontsize=10.5)
    for y, t in zip(ys, ticks):
        if t:
            ax.text(-0.365, y + 0.85, t, transform=ax.get_yaxis_transform(),
                    fontsize=9.5, color="#374151", fontweight="bold", clip_on=False)
    ax.set_xlim(0, 1.0)
    ax.set_ylim(-0.8, n - 0.2)
    ax.set_xlabel("preference fidelity  (vgeo at the fully-absolute preference =\n"
                  "fraction of runs buying an item that meets ALL 7 stated requirements)",
                  fontsize=10.5)
    ax.axvline(table.get("clean", {}).get("pooled", 1.0), color="#4b5563", lw=1,
               ls=":", zorder=2)
    ax.grid(axis="x", color="#e5e7eb", lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(f"benchmark_data/reports/fig_advtax.{ext}", bbox_inches="tight")
    print("wrote benchmark_data/reports/fig_advtax.png/.pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
