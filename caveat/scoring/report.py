"""Render the analysis summary into the two required reporting views:
  (a) a single combined preservation score per agent (Table 1);
  (b) preservation broken out by constraint variant and by steering type (Tables 2-3).
Emits Markdown + CSV.
"""

from __future__ import annotations

from pathlib import Path

from ..benchmark.schema import STEERING_TYPES


def _f(x, nd=3):
    return "—" if x is None else f"{x:.{nd}f}"


def _ci(t):
    if not t:
        return "—"
    point, lo, hi, n = t
    if point is None:
        return "—"
    if lo is None:
        return f"{point:+.3f} (n={n})"
    return f"{point:+.3f} [{lo:+.3f},{hi:+.3f}] (n={n})"


def render_markdown(summary: dict) -> str:
    L = ["# CAVEAT — Preservation Report", ""]

    # Table 1: combined per agent
    L += ["## Table 1 — Combined preservation per agent", "",
          "| agent | P(clean) | P(steered) | Δ overall | compl.(clean) | compl.(steered) | off-cat | error | cells |",
          "|---|---|---|---|---|---|---|---|---|"]
    for agent, a in summary["agents"].items():
        L.append(f"| {agent} | {_f(a['clean_P'])} | {_f(a['steered_P'])} | "
                 f"{_f(a['delta_overall'])} | {_f(a['completion_clean'],2)} | "
                 f"{_f(a['completion_steered'],2)} | {_f(a['off_catalog_rate'],2)} | "
                 f"{_f(a['error_rate'],2)} | {a['n_cells']} |")
    L.append("")

    # Table 2: P by variant x condition (per agent)
    for agent, a in summary["agents"].items():
        L += [f"## Table 2 — P by variant × condition  ({agent})", "",
              "| variant | " + " | ".join(["clean", *STEERING_TYPES]) + " |",
              "|" + "---|" * (len(STEERING_TYPES) + 2)]
        for var in ("thresholded", "graded", "mixed"):
            row = a["P_by_variant_cond"].get(var, {})
            cells = " | ".join(_f(row.get(c)) for c in ["clean", *STEERING_TYPES])
            L.append(f"| {var} | {cells} |")
        L.append("")

        # Table 3: Δ by steering type (overall + by variant)
        L += [f"## Table 3 — Steering effect Δ = P(clean) − P(steered)  ({agent})", "",
              "| steering type | Δ overall [95% CI] |",
              "|---|---|"]
        for c in STEERING_TYPES:
            L.append(f"| {c} | {_ci(a['deltas'].get(c))} |")
        L += ["", "| variant | Δ (pooled over steering) [95% CI] |", "|---|---|"]
        for var in ("thresholded", "graded", "mixed"):
            L.append(f"| {var} | {_ci(a['delta_by_variant'].get(var))} |")
        L.append("")
    return "\n".join(L)


def write_report(summary: dict, out_dir) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    md = render_markdown(summary)
    (out / "report.md").write_text(md)
    # CSV: per (agent, steering type) delta
    rows = ["agent,steering_type,delta,ci_lo,ci_hi,n"]
    for agent, a in summary["agents"].items():
        for c in STEERING_TYPES:
            t = a["deltas"].get(c) or (None, None, None, 0)
            rows.append(f"{agent},{c},{t[0]},{t[1]},{t[2]},{t[3]}")
    (out / "deltas.csv").write_text("\n".join(rows))
    return out / "report.md"
