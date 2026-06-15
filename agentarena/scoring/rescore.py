"""Backfill the unified continuous preservation P into each cell's summary.json, so the viewer
(and any summary reader) shows the current graded metric — config-drip aware (scored on the
purchased storage configuration), price treated like any spec.

Benchmark-layer (imports scenarios), so it stays out of the generic env/core. Run after a run, or
standalone:  python -m agentarena.scoring.rescore --glob 'results/cfg18_r*'
"""
from __future__ import annotations

import argparse
import glob as _glob
import json

from ..benchmark import scenarios as S
from ..benchmark.serialize import load_pool
from .basket import chosen_attrs
from .continuous import preservation

from ..benchmark.schema import _VARIANT_NGRADED
VARIANTS = tuple(_VARIANT_NGRADED)   # thresholded, mixed, graded, graded3, graded4 (graded-N spectrum)
_pool_cache: dict = {}


def _variant(task_id: str) -> str:
    v = task_id.rsplit("-", 1)[-1]
    return v if v in VARIANTS else "thresholded"


def _scenario(task_id: str) -> str:
    return task_id.rsplit("-", 1)[0]


def _pool(sc: str):
    if sc not in _pool_cache:
        rows = {r.asin: r for r in load_pool(sc)}
        cands = [{**r.attrs(), "no_addons": True} for r in rows.values()]
        _pool_cache[sc] = (rows, cands)
    return _pool_cache[sc]


def cell_preservation(traj_path: str):
    """Continuous P for one recorded cell, or None when there is no scorable purchase (none/
    error) or the cell ran on a different catalog generation than the current pool (stale →
    can't trust the asin mapping; we don't fabricate a number)."""
    try:
        d = json.load(open(traj_path))
    except Exception:
        return None
    tid = d.get("task_id", "")
    sc = _scenario(tid)
    if sc not in S.SCENARIOS:
        return None
    ev = d.get("evaluation") or {}
    det = ev.get("details") or {}
    outcome = ev.get("outcome")
    chosen = ev.get("chosen")
    if outcome in ("error", "skipped", "none", None) or chosen is None:
        return None
    rows, cands = _pool(sc)
    r = rows.get(chosen)
    if r is None:
        return 0.0  # off-catalog purchase
    # staleness guard: only score when we can CONFIRM the cell ran on the current catalog — the
    # recorded basket title for the chosen asin must match the current pool. ASINs are reassigned
    # across catalog regenerations, so an unconfirmable/mismatched title means we must not fabricate
    # a P (it would be mis-attributed). Current + future runs always record a matching title.
    li = (det.get("basket") or {}).get("line_items") or []
    bt = next((it.get("title") for it in li if it.get("asin") == chosen and it.get("title")), None)
    if not bt or (r.title and bt != r.title):
        return None
    a = chosen_attrs(r.attrs(), det, r.asin, variants=getattr(r, "variants", None))
    a.setdefault("no_addons", True)
    pref = S.get(sc).preference(_variant(tid))
    return round(preservation(a, pref.dsl(), pref.graded_map(), cands, variant=_variant(tid)), 4)


def write_preservation(globpat: str) -> int:
    """Write `preservation` (+ chosen config) into every summary.json under the glob. Returns the
    count updated. Safe to re-run."""
    n = 0
    for tj in _glob.glob(f"{globpat}/*/trajectory.json"):
        P = cell_preservation(tj)
        sf = tj[: -len("trajectory.json")] + "summary.json"
        try:
            s = json.load(open(sf))
        except Exception:
            continue
        s["preservation"] = P
        # surface the purchased storage configuration (config-drip) for the viewer sub-line
        try:
            det = (json.load(open(tj)).get("evaluation") or {}).get("details") or {}
            li = (det.get("basket") or {}).get("line_items") or []
            ch = s.get("chosen")
            s["chosen_config"] = next((it.get("variant_value") for it in li
                                       if it.get("asin") == ch and it.get("variant_value")), None)
        except Exception:
            pass
        json.dump(s, open(sf, "w"), indent=2, default=str)
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="results/*")
    a = ap.parse_args()
    print(f"updated {write_preservation(a.glob)} summaries under {a.glob}")


if __name__ == "__main__":
    main()
