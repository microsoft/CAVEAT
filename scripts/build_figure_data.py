#!/usr/bin/env python
"""Rebuild benchmark_data/reports/figure_data.json from CAVEAT result trees.

Sources (all committed under results/):
  * results/overhaul_b80/overhaul_b80_r*        headline Amazon matrix, max_steps 80, n=5
  * results/overhaul_b/overhaul_b_*_r*          the 50-step twin (budget-sensitivity arm)
  * results/overhaul_b_supp80/supp80            at-cap give-up supplement (re-run at 80 steps)
  * results/overhaul_lb/overhaul_lb_r*          leaderboard, 14 model configs, max_steps 250, n=3
  * results/overhaul_c_pilot2/<env>_r*          the 9 storefront clones on hardened catalogs

Metrics are read straight off each cell's summary.json after `rescore.write_strict`:
  P* = preservation_strict (headline, per-variant gate, G.O)
  B  = strict_binary       (all-or-nothing met-or-0; identity B == 1[P*==1])
  M  = resistance_margin   (clip((P*-C_L)/(1-C_L)); the cross-level-fair read)
plus the diagnostics: pinned-purchase rate, give-up ("none") rate, and the
gate-failure / optimality-shortfall decomposition.

Runs are dropped from the sample ONLY when infrastructure invalidated the measurement.
That call is made by scripts/_infra_classify.py, the single shared implementation that
other benchmark reporting scripts also use -- read its docstring before changing anything here.
A model that could not emit a valid action, gave up, or looped has FAILED THE TASK and
scores 0; it is never excluded.

Usage:  .venv/bin/python scripts/build_figure_data.py [--no-rescore]
"""

from __future__ import annotations

import argparse
import glob
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from caveat.benchmark import serialize  # noqa: E402

VARIANTS = ["thresholded", "mixed", "graded", "graded3", "graded4"]   # relativeness 0..4
PRODUCTS = ["laptop", "office_chair", "mattress", "backpack", "tent"]
HEADLINE_MODELS = ["gpt-5.5-high", "gpt-5.5-low", "gpt-4.1"]
CLONE_ENVS = ["nike", "ebay", "etsy", "fiverr", "stockx", "zillow", "doordash", "instacart", "airbnb"]

B80 = "results/overhaul_b80/overhaul_b80_r*"
B50 = "results/overhaul_b/overhaul_b_*_r*"
SUPP = "results/overhaul_b_supp80/supp80"
LB = "results/overhaul_lb/overhaul_lb_r*"
CPILOT = "results/overhaul_c_pilot2"
OUT = ROOT / "benchmark_data/reports/figure_data.json"

# Infra-vs-capability is decided by ONE shared implementation -- scripts/_infra_classify.py
# -- which the other benchmark reporting scripts also import. Do not re-derive it here; read that module's
# docstring for the rule and the per-signature justification.  In short: only a run that
# INFRASTRUCTURE terminated is excluded (zero-step launch failure, an endpoint/transport
# outage that ran out the consecutive-failure guard).  Anything the MODEL did wrong --
# unparseable action JSON, giving up, looping -- is a capability failure and scores 0.
sys.path.insert(0, str(ROOT / "scripts"))
from _infra_classify import INFRA, classify_run          # noqa: E402

_pin_cache: dict = {}


def pinned_skus(scenario: str) -> set:
    if scenario not in _pin_cache:
        try:
            st = json.loads((serialize.scenario_dir(scenario) / "steering.json").read_text())
            spec = st.get("combined", st) if isinstance(st, dict) else {}
            _pin_cache[scenario] = set(spec.get("decoy_skus") or [])
        except OSError:
            _pin_cache[scenario] = set()
    return _pin_cache[scenario]


def variant_of(task_id: str) -> str:
    for v in VARIANTS:
        if task_id.endswith(v):
            return v
    return task_id.rsplit("-", 1)[-1]


def load_cells(pattern: str) -> tuple[list, int]:
    """Per-run records under a results glob, with infra-invalidated runs dropped.

    Only runs the shared classifier calls INFRA are dropped.  Model-capability failures
    (and anything it cannot decide) are KEPT and score 0 -- see scripts/_infra_classify.py.
    """
    rows, infra = [], Counter()
    exp_dirs = sorted(d for d in glob.glob(str(ROOT / pattern)) if Path(d).is_dir())
    for d in exp_dirs:
        for sp in Path(d).glob("*/summary.json"):
            s = json.loads(sp.read_text())
            tid = s.get("task_id", "")
            var, scen = variant_of(tid), tid.rsplit("-", 1)[0]
            outcome, pstar = s.get("outcome"), s.get("preservation_strict")
            if outcome in (None, "none", "error", "skipped") and pstar is None:
                verdict = classify_run(str(sp.parent))
                if verdict["class"] == INFRA:
                    infra[s.get("model")] += 1
                    continue
                pstar = 0.0                      # behavioural no-buy / hard failure scores 0
            rows.append({
                "exp": Path(d).name, "cell": sp.parent.name,
                "model": s.get("model"), "scaffold": s.get("scaffold"),
                "scenario": scen, "variant": var, "condition": s.get("condition"),
                "outcome": outcome, "steps": s.get("num_steps"),
                "pstar": float(pstar if pstar is not None else 0.0),
                "B": s.get("strict_binary"), "M": s.get("resistance_margin"),
                "chosen": s.get("chosen"), "pinned": s.get("chosen") in pinned_skus(scen),
            })
    return rows, dict(infra)


def agg(rows: list) -> dict:
    """The reported bundle for one group of runs."""
    if not rows:
        return {}
    ps = [r["pstar"] for r in rows]
    bs = [r["B"] if r["B"] is not None else (1.0 if r["pstar"] >= 1.0 else 0.0) for r in rows]
    ms = [r["M"] if r["M"] is not None else r["pstar"] for r in rows]
    done = [r for r in rows if r["outcome"] not in (None, "none", "error", "skipped")]
    pos = [r["pstar"] for r in done if r["pstar"] > 0]
    return {
        "n": len(rows),
        "pstar": round(statistics.mean(ps), 4),
        "pstar_sd": round(statistics.stdev(ps), 4) if len(ps) > 1 else 0.0,
        "pstar_sem": round(statistics.stdev(ps) / len(ps) ** 0.5, 4) if len(ps) > 1 else 0.0,
        "B": round(statistics.mean(bs), 4),
        "M": round(statistics.mean(ms), 4),
        "pin_buy": round(sum(r["pinned"] for r in rows) / len(rows), 4),
        "give_up": round(sum(r["outcome"] == "none" for r in rows) / len(rows), 4),
        "gate0": round(statistics.mean([1.0 if r["pstar"] == 0.0 else 0.0 for r in done]), 4) if done else None,
        "o_shortfall": round(1.0 - statistics.mean(pos), 4) if pos else None,
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "values": [round(p, 4) for p in ps],
    }


def by(rows, *keys):
    d = defaultdict(list)
    for r in rows:
        d[tuple(r[k] for k in keys)].append(r)
    return d


def matrix(rows, models):
    """{model: {variant: {condition: agg}}} + pooled-over-model rows."""
    g = by(rows, "model", "variant", "condition")
    per = {m: {v: {c: agg(g.get((m, v, c), [])) for c in ("clean", "combined")}
               for v in VARIANTS} for m in models}
    gp = by(rows, "variant", "condition")
    pooled = {v: {c: agg(gp.get((v, c), [])) for c in ("clean", "combined")} for v in VARIANTS}
    return per, pooled


# --------------------------------------------------------------------- capitulation ceilings
def ceilings() -> dict:
    from caveat.scoring.rescore import capitulation_ceiling
    out = {}
    for sc in PRODUCTS:
        for v in VARIANTS:
            try:
                out.setdefault(sc, {})[v] = capitulation_ceiling(sc, v)
            except Exception as e:                                    # noqa: BLE001
                out.setdefault(sc, {})[v] = None
                print(f"  [C_L] {sc}/{v}: {type(e).__name__} {e}", file=sys.stderr)
    return out


# --------------------------------------------------------------------- the 9 clone envs
def clone_envs() -> dict:
    """Mirror of scripts/pilot_report5.py's data path (trajectory.json evaluation details)."""
    import importlib
    MODELS = ["gpt-5.5-high", "gpt-4.1"]

    def advertised(env):
        try:
            cat_mod = importlib.import_module(f"caveat.envs.{env}.catalog")
            cats = [v for v in vars(cat_mod).values()
                    if hasattr(v, "name") and (hasattr(v, "items") or hasattr(v, "listings"))]
            skus = {getattr(it, "sku", None) for cat in cats
                    for it in (getattr(cat, "items", None) or getattr(cat, "listings"))
                    if getattr(it, "advertised", False)}
            return skus or None
        except Exception:                                             # noqa: BLE001
            return None

    def cl_echo(env):
        try:
            from caveat.envs._storefront.scoring import score
            from caveat.envs._storefront.tasks7 import project
            spec = importlib.import_module(f"caveat.envs.{env}.tasks").PREF7
            cat_mod = importlib.import_module(f"caveat.envs.{env}.catalog")
            cats = [v for v in vars(cat_mod).values()
                    if hasattr(v, "name") and (hasattr(v, "items") or hasattr(v, "listings"))]
            cat = next((c for c in cats if getattr(c, "name", None) == spec.catalog), cats[0])
            items = [it for it in (getattr(cat, "items", None) or getattr(cat, "listings"))
                     if getattr(it, "role", "") != "addon"]
            cands = [it.attrs() for it in items]
            adv = [it for it in items if getattr(it, "advertised", False)]
            out = {}
            for vv in VARIANTS:
                prefs, graded = project(spec, vv)
                out[vv] = round(max((score(it.attrs(), prefs, graded, cands, variant=vv)[1]
                                     for it in adv), default=0.0), 4)
            return out
        except Exception:                                             # noqa: BLE001
            return None

    data, infra = defaultdict(list), Counter()
    for tj in glob.glob(str(ROOT / CPILOT / "*/*/trajectory.json")):
        try:
            t = json.loads(Path(tj).read_text())
        except Exception:                                             # noqa: BLE001
            continue
        env, model = t.get("env"), t.get("model")
        cond = "steered" if t.get("condition") == "combined" else t.get("condition")
        var = t.get("task_id", "").rsplit("-", 1)[-1]
        if var not in VARIANTS or model not in MODELS or env not in CLONE_ENVS:
            continue
        ev = t.get("evaluation") or {}
        outcome = ev.get("outcome")
        if outcome in (None, "none"):
            # same shared rule as load_cells -- a give-up only leaves the sample if
            # infrastructure ended the run (see scripts/_infra_classify.py).
            if classify_run(str(Path(tj).parent))["class"] == INFRA:
                infra[model] += 1
                continue
        done = outcome not in (None, "none", "error", "skipped")
        ps = ((ev.get("details") or {}).get("preservation_strict")) if done else None
        data[(env, model, cond, var)].append({
            "pstar": float(ps) if isinstance(ps, (int, float)) else 0.0,
            "done": done, "chosen": ev.get("chosen"),
        })

    out = {"infra_excluded": sum(infra.values()), "infra_excluded_by_model": dict(infra),
           "envs": {}}
    for env in CLONE_ENVS:
        adv = advertised(env)
        cl = cl_echo(env)
        e = {"C_L": cl, "levels": {}}
        for m in MODELS:
            for cond in ("clean", "steered"):
                for v in VARIANTS:
                    rs = data.get((env, m, cond, v), [])
                    if not rs:
                        continue
                    ps = [r["pstar"] for r in rs]
                    c = (cl or {}).get(v, 0.0) or 0.0
                    e["levels"].setdefault(f"{cond}|{m}", {})[v] = {
                        "n": len(rs),
                        "pstar": round(statistics.mean(ps), 4),
                        "completion": round(sum(r["done"] for r in rs) / len(rs), 4),
                        "B": round(statistics.mean([1.0 if p >= 1 - 1e-9 else 0.0 for p in ps]), 4),
                        "M": round(statistics.mean([max(0.0, min(1.0, (p - c) / (1.0 - c)))
                                                    for p in ps]), 4) if c < 1.0 else 0.0,
                        "pin_buy": (round(statistics.mean(
                            [1.0 if r["chosen"] in adv else 0.0 for r in rs if r["done"]]), 4)
                            if adv and any(r["done"] for r in rs) else None),
                        "values": [round(p, 4) for p in ps],
                    }
        g4 = e["levels"].get("steered|gpt-5.5-high", {}).get("graded4", {})
        e["C4"] = g4.get("pstar")
        e["C4_n"] = g4.get("n")
        e["C4_values"] = g4.get("values")
        e["C4_pass"] = (g4.get("pstar") is not None and g4["pstar"] < 0.5)
        out["envs"][env] = e
    return out


# --------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-rescore", action="store_true", help="skip rescore.write_strict")
    ap.add_argument("--skip-lb", action="store_true", help="leave the leaderboard block empty")
    a = ap.parse_args()

    if not a.no_rescore:
        from caveat.scoring.rescore import write_strict
        for pat in (B80, B50, SUPP) + (() if a.skip_lb else (LB,)):
            for d in sorted(glob.glob(str(ROOT / pat))):
                if Path(d).is_dir():
                    print(f"[rescore] {Path(d).name}: {write_strict(d)} summaries")

    out = {"schema": 3, "generated_from": {
        "b80": B80, "b50": B50, "supplement": SUPP, "leaderboard": LB, "clone_pilot": CPILOT}}

    # ---- headline b80 matrix ------------------------------------------------
    r80, infra80 = load_cells(B80)
    per80, pool80 = matrix(r80, HEADLINE_MODELS)
    out["b80"] = {
        "max_steps": 80, "repeats": 5, "products": PRODUCTS, "models": HEADLINE_MODELS,
        "n_runs": len(r80), "infra_excluded": sum(infra80.values()),
        "infra_excluded_by_model": infra80,
        "per_model": per80, "pooled": pool80,
        "per_product": {p: {v: {c: agg([r for r in r80 if r["scenario"] == p
                                        and r["variant"] == v and r["condition"] == c])
                                for c in ("clean", "combined")} for v in VARIANTS} for p in PRODUCTS},
        "gap": {v: round(pool80[v]["clean"]["pstar"] - pool80[v]["combined"]["pstar"], 4)
                for v in VARIANTS},
    }

    # ---- 50-step twin + supplement -----------------------------------------
    r50, infra50 = load_cells(B50)
    per50, pool50 = matrix(r50, HEADLINE_MODELS)
    supp, _ = load_cells(SUPP)
    out["budget"] = {
        "b50": {"max_steps": 50, "repeats": 5, "n_runs": len(r50),
                "infra_excluded": sum(infra50.values()), "infra_excluded_by_model": infra50,
                "per_model": per50, "pooled": pool50},
        "b80_ref": pool80,
        "delta": {v: {c: round(pool80[v][c]["pstar"] - pool50[v][c]["pstar"], 4)
                      for c in ("clean", "combined")} for v in VARIANTS},
        "supplement": {
            "description": "at-cap give-up runs from the 50-step matrix re-run at max_steps 80",
            "n": len(supp),
            "purchased": sum(r["outcome"] not in (None, "none", "error", "skipped") for r in supp),
            "hero": sum(r["pstar"] >= 1.0 for r in supp),
            "still_none": sum(r["outcome"] == "none" for r in supp),
            "runs": [{"cell": r["cell"], "steps": r["steps"], "outcome": r["outcome"],
                      "pstar": r["pstar"], "chosen": r["chosen"]} for r in sorted(supp, key=lambda x: x["cell"])],
        },
    }

    # ---- leaderboard --------------------------------------------------------
    if a.skip_lb:
        out["leaderboard"] = {"status": "skipped"}
    else:
        rlb, infralb = load_cells(LB)
        lb_models = sorted({r["model"] for r in rlb})
        perlb, poollb = matrix(rlb, lb_models)
        gm = by(rlb, "model", "condition")
        graded = ("graded", "graded3", "graded4")
        summary = {}
        for m in lb_models:
            row = {"n_runs": len(gm.get((m, "clean"), [])) + len(gm.get((m, "combined"), [])),
                   "infra_excluded": infralb.get(m, 0)}
            for c in ("clean", "combined"):
                a_all = agg(gm.get((m, c), []))
                a_gr = agg([r for r in gm.get((m, c), []) if r["variant"] in graded])
                row[c] = {"all": a_all, "graded": a_gr}
                # equal-weight mean of the 5 per-level means (does not over-weight easy levels)
                lv = [perlb[m][v][c] for v in VARIANTS if perlb[m][v][c]]
                row[c]["level_mean_pstar"] = round(statistics.mean([x["pstar"] for x in lv]), 4) if lv else None
                row[c]["level_mean_M"] = round(statistics.mean([x["M"] for x in lv]), 4) if lv else None
            summary[m] = row
        done_marker = (ROOT / "results/overhaul_lb/final.log")
        # CAVEAT_NO_SHOT_PERSIST=1 was set from 2026-07-25; cells run before that switch
        # still carry a filmstrip, so report the measured split rather than asserting "none".
        cells_on_disk = [d.parent for d in Path(ROOT / "results/overhaul_lb").glob(
            "overhaul_lb_r*/*/summary.json")]
        with_shots = sum(any(d.glob("step_*.png")) for d in cells_on_disk)
        out["leaderboard"] = {
            "max_steps": 250, "repeats": 3, "models": lb_models,
            "n_runs": len(rlb), "infra_excluded": sum(infralb.values()),
            "infra_excluded_by_model": infralb,
            "expected_runs": 14 * len(PRODUCTS) * len(VARIANTS) * 2 * 3,
            "complete": ("final fill complete" in done_marker.read_text(errors="ignore")
                         if done_marker.exists() else False),
            "screenshots": {"cells_on_disk": len(cells_on_disk), "with_filmstrip": with_shots,
                            "without_filmstrip": len(cells_on_disk) - with_shots,
                            "note": "CAVEAT_NO_SHOT_PERSIST=1 from 2026-07-25; scores, "
                                    "actions, reasoning and URLs are complete either way"},
            "per_model": perlb, "pooled": poollb, "summary": summary,
        }

    # ---- clone envs + ceilings ---------------------------------------------
    out["clone_envs"] = clone_envs()
    out["capitulation_ceilings_amazon"] = ceilings()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1))
    print(f"\nwrote {OUT} ({OUT.stat().st_size/1e6:.2f} MB)")
    print(f"  b80: {len(r80)} runs ({sum(infra80.values())} infra-excluded) | "
          f"b50: {len(r50)} runs ({sum(infra50.values())} infra-excluded)")
    print(f"  supplement: {out['budget']['supplement']['n']} runs")
    if not a.skip_lb:
        print(f"  leaderboard: {out['leaderboard']['n_runs']}/"
              f"{out['leaderboard']['expected_runs']} runs, complete={out['leaderboard']['complete']}")
    print(f"  clone envs: {len(out['clone_envs']['envs'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
