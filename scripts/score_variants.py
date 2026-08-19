#!/usr/bin/env python
"""Sweep every amazon result snapshot and compute the candidate strict-metric family
(caveat.scoring.strict_variants) per cell -> benchmark_data/reports/scoring_variants_data.json.

READ-ONLY over results/: never writes summary.json (the measured pipeline stays frozen). The
catalog-staleness guard drops any cell whose recorded basket no longer matches the current pool.

  .venv/bin/python scripts/score_variants.py
"""
import glob
import json
import os
import sys
from collections import Counter

sys.path.insert(0, __file__.rsplit("scripts/", 1)[0] or ".")  # repo root (portable: works from any checkout)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))         # scripts/ (shared rule)
from caveat.scoring.strict_variants import METRICS, cell_variants  # noqa: E402
# Infra-vs-capability is decided by the ONE shared implementation in scripts/_infra_classify.py
# (build_figure_data.py imports the same module). Here it only TAGS each
# record (rec["infra"]); nothing is dropped from the emitted dataset.
from _infra_classify import is_infra_fail                              # noqa: E402

SNAPSHOTS = {
    "mm_v1":     "results/mm_v1/*/amazon__*/trajectory.json",         # all scaffolds (browseruse + playwright-mcp)
    "cu_v3":     "results/cu_v3/cu3_r*/amazon__magentic-one__*__*/trajectory.json",
    "cu_v1":     "results/cu_v1/cu_r*/amazon__websurfer__*__*/trajectory.json",
    "mech8_v1":  "results/mech8_v1/mech8_r*/amazon__browseruse__*__*/trajectory.json",
    "scrape_v1": "results/scrape_v1/sc_r*/amazon__browseruse__*__*/trajectory.json",
}
IDENTITY = ("model", "scaffold", "condition", "task_id", "outcome", "num_steps")
OUT = "benchmark_data/reports/scoring_variants_data.json"


def main():
    data, guard_blocked = {}, []
    for snap, pat in SNAPSHOTS.items():
        recs, cov = [], Counter()
        for tj in sorted(glob.glob(pat)):
            cell = os.path.dirname(tj)
            sf = os.path.join(cell, "summary.json")
            try:
                s = json.load(open(sf))
            except Exception:
                cov["no_summary"] += 1
                continue
            rec = {k: s.get(k) for k in IDENTITY}
            tid = rec.get("task_id") or ""
            rec["scenario"], _, rec["variant"] = tid.rpartition("-")
            rec["stored_strict"] = s.get("preservation_strict")
            rec["infra"] = is_infra_fail(cell)
            comp, scores = cell_variants(tj)
            if comp is None:
                # no scorable purchase — split genuine none/error from guard-blocked purchases
                ev = {}
                try:
                    d = json.load(open(tj))
                    ev = d.get("evaluation") or {}
                except Exception:
                    pass
                if ev.get("outcome") in ("error", "skipped", "none", None) or ev.get("chosen") is None:
                    cov["no_purchase"] += 1
                else:
                    cov["guard_blocked"] += 1
                    guard_blocked.append(tj)
                rec["components"], rec["metrics"] = None, None
            elif comp.get("off"):
                cov["off_catalog"] += 1
                rec["components"], rec["metrics"] = comp, scores
            else:
                cov["scored"] += 1
                rec["components"], rec["metrics"] = comp, scores
            recs.append(rec)
        data[snap] = recs
        total = len(recs)
        print(f"{snap:10s} cells={total:5d} scored={cov['scored']:5d} "
              f"no_purchase={cov['no_purchase']:4d} off={cov['off_catalog']:2d} "
              f"guard_blocked={cov['guard_blocked']:2d} no_summary={cov['no_summary']:2d}")

    if guard_blocked:
        print(f"\nFATAL: {len(guard_blocked)} purchases blocked by the staleness guard:")
        for tj in guard_blocked[:10]:
            print("  ", tj)
        sys.exit(1)
    json.dump({"metrics": list(METRICS), "snapshots": data}, open(OUT, "w"))
    n = sum(len(v) for v in data.values())
    print(f"{OUT}: {n} records across {len(data)} snapshots")


if __name__ == "__main__":
    main()
