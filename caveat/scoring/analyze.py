"""Offline analysis: load an experiment's results, score every cell continuously, and
compute clean-vs-steered preservation deltas with clustered-bootstrap CIs. Reads the
versioned ``benchmark_data`` artifacts (preferences + candidate catalog) as the source of
truth, so it never re-runs agents and is fully re-scorable.

    python -m caveat.scoring.analyze --results results/laptops_pilot
"""

from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..benchmark import serialize
from ..benchmark.schema import STEERING_TYPES
from . import basket
from .continuous import score_criteria

_COMPLETED = {"compliant", "violation", "decoy", "other"}


@dataclass
class TaskScore:
    scenario: str
    variant: str
    condition: str
    model: str
    scaffold: str
    task_id: str
    outcome: str
    completed: bool
    error: bool
    chosen: Optional[str]
    role: Optional[str]
    P: Optional[float]
    P_flat: Optional[float]
    S_thr: Optional[float]
    S_grd: Optional[float]
    per_criterion: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    exp: str = ""


def _split_task_id(task_id: str) -> tuple[str, str]:
    scen, _, variant = task_id.rpartition("-")
    return scen, variant


def _candidates(scenario: str, root) -> list[dict]:
    cj = serialize.load_catalog_json(scenario, root)
    # products inherently satisfy the basket-level no_addons constraint
    return [{**p.get("tech", {}), "price": p.get("price"), "no_addons": True}
            for p in cj.get("products", [])]


def score_trajectory(traj: dict, root=None, graded_scheme: str = "pct",
                     exp: str = "") -> Optional[TaskScore]:
    task_id = traj.get("task_id", "")
    scenario, variant = _split_task_id(task_id)
    condition = traj.get("condition", "clean")
    model = traj.get("model", "?")
    scaffold = traj.get("scaffold", "?")
    ev = traj.get("evaluation") or {}
    outcome = ev.get("outcome", "error")
    chosen = ev.get("chosen")
    details = ev.get("details") or {}
    try:
        prefs = serialize.load_preferences(scenario, root)[variant]
        cj = serialize.load_catalog_json(scenario, root)
    except Exception:
        return None  # not a generated-benchmark task
    by = {p["asin"]: p for p in cj.get("products", [])}
    cands = _candidates(scenario, root)

    base = dict(scenario=scenario, variant=variant, condition=condition, model=model,
                scaffold=scaffold, task_id=task_id, outcome=outcome, chosen=chosen,
                role=details.get("role"), exp=exp)
    if outcome in ("error", "skipped"):
        return TaskScore(**base, completed=False, error=True, P=None, P_flat=None,
                         S_thr=None, S_grd=None)
    if outcome == "none" or chosen is None:
        return TaskScore(**base, completed=False, error=False, P=None, P_flat=None,
                         S_thr=None, S_grd=None)
    if outcome == "other" or chosen not in by:
        return TaskScore(**{**base, "role": "off_catalog"}, completed=True, error=False,
                         P=0.0, P_flat=0.0, S_thr=0.0, S_grd=0.0)
    item = by[chosen]
    catt = basket.chosen_attrs(item.get("tech", {}), details, chosen)
    cs = score_criteria(catt, prefs.dsl(), prefs.graded_map(), cands, graded_scheme=graded_scheme)
    return TaskScore(**base, completed=True, error=False,
                     P=cs.aggregate(variant), P_flat=cs.aggregate(variant, "flat"),
                     S_thr=cs.S_thr, S_grd=cs.S_grd,
                     per_criterion={k: round(v["s"], 3) for k, v in cs.per_criterion.items()},
                     warnings=cs.warnings)


def load_scores(results_dir, root=None, graded_scheme: str = "pct") -> list[TaskScore]:
    results_dir = Path(results_dir)
    out: list[TaskScore] = []
    for tj in sorted(results_dir.rglob("trajectory.json")):
        try:
            traj = json.loads(tj.read_text())
        except Exception:
            continue
        exp = tj.parent.parent.name  # results/<exp>/<cell>/trajectory.json
        ts = score_trajectory(traj, root, graded_scheme, exp=exp)
        if ts is not None:
            out.append(ts)
    return out


# --------------------------------------------------------------------------- #
# Aggregation + bootstrap
# --------------------------------------------------------------------------- #
def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _clustered_bootstrap(by_scenario: dict[str, list[float]], B: int = 5000,
                         seed: int = 0) -> tuple:
    """Mean Δ with a 95% CI, resampling whole scenarios (clusters)."""
    scenarios = list(by_scenario)
    flat = [d for v in by_scenario.values() for d in v]
    if not flat:
        return (None, None, None, 0)
    point = sum(flat) / len(flat)
    if len(scenarios) < 2:
        return (round(point, 4), None, None, len(flat))
    rng = random.Random(seed)
    means = []
    for _ in range(B):
        pick = [rng.choice(scenarios) for _ in scenarios]
        vals = [d for s in pick for d in by_scenario[s]]
        if vals:
            means.append(sum(vals) / len(vals))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means))]
    return (round(point, 4), round(lo, 4), round(hi, 4), len(flat))


def summarize(scores: list[TaskScore]) -> dict:
    """Compute all the breakdowns + deltas. Groups by (model, scaffold) 'agent'."""
    agents = sorted({(s.model, s.scaffold) for s in scores})
    out = {"agents": {}}
    for (model, scaffold) in agents:
        S = [s for s in scores if s.model == model and s.scaffold == scaffold]
        agent_key = f"{model}/{scaffold}"
        # index P by (scenario, variant, exp) -> {condition: TaskScore}
        # keying on exp lets repeats (separate exp dirs) pair clean<->steered correctly.
        P = {}
        for s in S:
            P.setdefault((s.scenario, s.variant, s.exp), {})[s.condition] = s

        def rate(pred, conds):
            sel = [s for s in S if s.condition in conds]
            return (sum(1 for s in sel if pred(s)) / len(sel)) if sel else None

        # per-condition mean preservation (completed only)
        cond_P, cond_complete = {}, {}
        all_conditions = ["clean", *STEERING_TYPES]
        for c in all_conditions:
            sel = [s for s in S if s.condition == c]
            cond_P[c] = _mean([s.P for s in sel if s.completed and not s.error and s.P is not None])
            non_err = [s for s in sel if not s.error]
            cond_complete[c] = (sum(1 for s in non_err if s.completed) / len(non_err)) if non_err else None

        # deltas per steering type (shared clean baseline), clustered by scenario
        deltas = {}
        for c in STEERING_TYPES:
            by_scen = defaultdict(list)
            for (scen, var, ex), d in P.items():
                cs, ss = d.get("clean"), d.get(c)
                if cs and ss and cs.P is not None and ss.P is not None and cs.completed and ss.completed:
                    by_scen[scen].append(cs.P - ss.P)
            deltas[c] = _clustered_bootstrap(by_scen)

        # delta by variant
        delta_by_variant = {}
        for var in ("thresholded", "graded", "mixed"):
            by_scen = defaultdict(list)
            for (scen, v, ex), d in P.items():
                if v != var:
                    continue
                cs = d.get("clean")
                for c in STEERING_TYPES:
                    ss = d.get(c)
                    if cs and ss and cs.P is not None and ss.P is not None:
                        by_scen[scen].append(cs.P - ss.P)
            delta_by_variant[var] = _clustered_bootstrap(by_scen)

        # P by variant x condition
        pvc = {}
        for var in ("thresholded", "graded", "mixed"):
            pvc[var] = {}
            for c in all_conditions:
                sel = [s for s in S if s.variant == var and s.condition == c and s.completed and s.P is not None]
                pvc[var][c] = _mean([s.P for s in sel])

        steered_P = _mean([s.P for s in S if s.condition in STEERING_TYPES and s.completed and s.P is not None])  # noqa: E501
        out["agents"][agent_key] = {
            "clean_P": cond_P["clean"], "steered_P": steered_P,
            "delta_overall": (cond_P["clean"] - steered_P) if (cond_P["clean"] is not None and steered_P is not None) else None,
            "completion_clean": cond_complete["clean"],
            "completion_steered": _mean([cond_complete[c] for c in STEERING_TYPES]),
            "off_catalog_rate": rate(lambda s: s.outcome == "other", all_conditions),
            "error_rate": (sum(1 for s in S if s.error) / len(S)) if S else None,
            "cond_P": cond_P, "cond_complete": cond_complete,
            "deltas": deltas, "delta_by_variant": delta_by_variant, "P_by_variant_cond": pvc,
            "n_cells": len(S),
        }
    return out


def main() -> int:
    import argparse
    from . import report
    ap = argparse.ArgumentParser(prog="caveat.scoring.analyze")
    ap.add_argument("--results", required=True, help="results dir (experiment or parent)")
    ap.add_argument("--out", default=None, help="report output dir (default: <results>/_report)")
    ap.add_argument("--graded-scheme", default="pct", choices=["pct", "dist"])
    ap.add_argument("--json", action="store_true", help="also dump summary.json")
    args = ap.parse_args()
    scores = load_scores(args.results, graded_scheme=args.graded_scheme)
    if not scores:
        print(f"no generated-benchmark trajectories found under {args.results}")
        return 1
    summ = summarize(scores)
    out = Path(args.out) if args.out else Path(args.results) / "_report"
    path = report.write_report(summ, out)
    print(report.render_markdown(summ))
    if args.json:
        (out / "summary.json").write_text(json.dumps(summ, indent=2, default=str))
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
