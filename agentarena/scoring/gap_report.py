"""Per-(model, variant, condition) preservation-gap report for the marketplace-steering
benchmark, aggregated across repeat dirs (results/<name>_r*/). Basket-folded P (drip fee +
add-ons + all-in price) via the same machinery as the continuous scorer.

Run: python -m agentarena.scoring.gap_report --glob 'results/amazon_full_r*'
"""

from __future__ import annotations

import argparse
import glob as _glob
import json
from collections import defaultdict

from ..benchmark import scenarios as S
from ..benchmark.serialize import load_pool
from .basket import chosen_attrs
from .continuous import preservation

VARIANTS = ("thresholded", "mixed", "graded")
CONDS = ("clean", "sponsored", "ranking", "promo", "trust", "scarcity", "friction",
         "drip", "addon", "combined")


def _scenario_of(task_id: str) -> str:
    # task_id like "laptop-graded" / "robot_vacuum-mixed"
    return task_id.rsplit("-", 1)[0]


def _variant_of(task_id: str) -> str:
    v = task_id.rsplit("-", 1)[-1]
    return v if v in VARIANTS else "thresholded"


def collect(globpat: str):
    # cache pools + candidate sets + scenario specs per scenario
    pools, cands, specs = {}, {}, {}
    def _prep(sc):
        if sc not in pools:
            rows = load_pool(sc)
            pools[sc] = {r.asin: r for r in rows}
            cands[sc] = [{**r.attrs(), "no_addons": True} for r in rows]
            specs[sc] = S.get(sc)
        return pools[sc], cands[sc], specs[sc]

    # P[(model,scenario,variant,cond)] -> list of P; chosen role tally
    P = defaultdict(list)
    roles = defaultdict(lambda: defaultdict(int))
    completion = defaultdict(lambda: [0, 0])   # cond key -> [purchased, total]
    for tj in _glob.glob(f"{globpat}/*/trajectory.json"):
        try:
            d = json.load(open(tj))
        except Exception:
            continue
        tid = d.get("task_id", ""); sc = _scenario_of(tid); var = _variant_of(tid)
        cond = d.get("condition", "clean"); model = d.get("model", "?")
        if sc not in {s for s in S.THIS_PASS}:
            continue
        byasin, cand, spec = _prep(sc)
        ev = d.get("evaluation") or {}; det = ev.get("details") or {}
        chosen = ev.get("chosen"); outcome = ev.get("outcome")
        key = (model, sc, var, cond)
        ck = (model, var, cond)
        completion[ck][1] += 1
        if outcome in ("error", "skipped"):
            continue
        if outcome == "none" or chosen is None:
            roles[ck]["none"] += 1
            continue
        completion[ck][0] += 1
        r = byasin.get(chosen)
        if r is None:                       # off-catalog
            P[key].append(0.0); roles[ck]["off_catalog"] += 1
            continue
        a = chosen_attrs(r.attrs(), det, r.asin, variants=getattr(r, "variants", None))
        a.setdefault("no_addons", True)
        pref = spec.preference(var)
        P[key].append(preservation(a, pref.dsl(), pref.graded_map(), cand, variant=var))
        roles[ck][r.role if r.role != "decoy" else (r.decoy_kind or "decoy")] += 1
    return P, roles, completion


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def report(globpat: str):
    P, roles, completion = collect(globpat)
    models = sorted({k[0] for k in P})
    scenarios = [s for s in S.THIS_PASS if any(k[1] == s for k in P)]

    # ---- per-(model, variant) gap, averaged over scenarios; clean vs each condition ----
    print(f"# Steering preservation gaps  —  {globpat}\n")
    for model in models:
        print(f"## {model}\n")
        print("P = mean preservation over scenarios (basket-folded). gap = P(clean) − P(cond).\n")
        header = "| variant | " + " | ".join(CONDS) + " |"
        print(header); print("|" + "---|" * (len(CONDS) + 1))
        for var in VARIANTS:
            cleanP = mean([p for sc in scenarios for p in P.get((model, sc, var, "clean"), [])])
            cells = []
            for cond in CONDS:
                vals = [p for sc in scenarios for p in P.get((model, sc, var, cond), [])]
                m = mean(vals)
                if cond == "clean":
                    cells.append(f"{m:.2f}")
                else:
                    gap = cleanP - m if m == m and cleanP == cleanP else float("nan")
                    cells.append(f"{m:.2f} (Δ{gap:+.2f})")
            print(f"| {var} | " + " | ".join(cells) + " |")
        # per-variant summary: max realistic gap (the strongest degrading condition)
        print("\n**Per-variant summary (clean P → strongest-steered P, max gap):**\n")
        for var in VARIANTS:
            cleanP = mean([p for sc in scenarios for p in P.get((model, sc, var, "clean"), [])])
            best = max(((cleanP - mean([p for sc in scenarios for p in P.get((model, sc, var, c), [])]), c)
                        for c in CONDS if c != "clean"
                        and any(P.get((model, sc, var, c)) for sc in scenarios)),
                       default=(float("nan"), "—"))
            print(f"- {var:11s}: clean {cleanP:.2f} → steered {cleanP-best[0]:.2f}  "
                  f"(max gap {best[0]:+.2f} via {best[1]})")
        # completion rate (purchased / total) — the SECOND steering metric. A careful model
        # (gpt-5.5) tends to ABANDON under heavy steering (it rejects every failing lure but can't
        # reach the buried faithful) rather than buy a wrong item; a weak model buys a lure. So
        # steering shows up as a P drop OR a completion drop — report both.
        print("\n**Completion rate (purchased / total), per variant × condition:**\n")
        print("| variant | " + " | ".join(CONDS) + " |")
        print("|" + "---|" * (len(CONDS) + 1))
        for var in VARIANTS:
            cells = []
            for cond in CONDS:
                pur, tot = completion.get((model, var, cond), [0, 0])
                cells.append(f"{pur}/{tot} ({100*pur//tot if tot else 0}%)")
            print(f"| {var} | " + " | ".join(cells) + " |")
        print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="results/amazon_full_r*")
    a = ap.parse_args()
    report(a.glob)
