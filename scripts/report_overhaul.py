#!/usr/bin/env python
"""Aggregate report for the validity-overhaul pilot runs (Phase A step 7+).

Backfills strict P* into every summary (rescore.write_strict), then prints per
(variant x condition): mean P*, outcome mix, pinned-purchase (capitulation) rate,
and the clean-steered gap — the quantities the Phase A exit gates read.

Usage:
    python scripts/report_overhaul.py 'results/overhaul_a*'
"""

from __future__ import annotations

import glob
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _infra_classify import is_infra_fail          # noqa: E402

VARIANT_ORDER = ["thresholded", "mixed", "graded", "graded3", "graded4"]

# Infra-vs-capability is decided by the ONE shared implementation in
# scripts/_infra_classify.py (which build_figure_data.py also imports).
# Do not re-derive it here.  In short: a run leaves the sample only when INFRASTRUCTURE
# terminated it (zero-step launch failure, or an endpoint/transport outage that ran out the
# consecutive-failure guard).  Unparseable action JSON, giving up and looping are MODEL
# capability failures -- they stay in and score 0.


def _variant(task_id: str) -> str:
    for v in VARIANT_ORDER:
        if task_id.endswith(v):
            return v
    return task_id.rsplit("-", 1)[-1]


_pin_cache: dict = {}


def _pinned_skus(scenario: str) -> set:
    """Combined-condition pinned decoy set for a scenario (cached)."""
    if scenario not in _pin_cache:
        try:
            steering = json.loads(
                (Path("benchmark_data/amazon") / scenario / "steering.json").read_text())
            spec = steering.get("combined", steering) if isinstance(steering, dict) else {}
            _pin_cache[scenario] = set(spec.get("decoy_skus") or [])
        except OSError:
            _pin_cache[scenario] = set()
    return _pin_cache[scenario]


def main() -> int:
    pat = sys.argv[1] if len(sys.argv) > 1 else "results/overhaul_a*"
    exp_dirs = sorted(d for d in glob.glob(pat) if Path(d).is_dir())
    if not exp_dirs:
        print(f"no experiment dirs match {pat}", file=sys.stderr)
        return 2

    from caveat.scoring.rescore import write_strict
    for d in exp_dirs:
        n = write_strict(d)
        print(f"[rescore] {d}: wrote strict P* into {n} summaries")

    cells = defaultdict(list)   # (variant, condition) -> list of dicts
    infra_excluded = 0
    for d in exp_dirs:
        for sp in Path(d).glob("*/summary.json"):
            s = json.loads(sp.read_text())
            cond = s.get("condition")
            var = _variant(s.get("task_id", ""))
            scenario = s.get("task_id", "").rsplit("-", 1)[0]
            pins = _pinned_skus(scenario)
            outcome = s.get("outcome")
            pstar = s.get("preservation_strict")
            if outcome in (None, "none", "error", "skipped") and pstar is None:
                # only an INFRASTRUCTURE-terminated run leaves the sample (shared rule)
                if is_infra_fail(str(sp.parent)):
                    infra_excluded += 1
                    continue
                pstar = 0.0   # behavioral no-buy / hard failure scores 0
            cells[(var, cond)].append({
                "pstar": pstar if pstar is not None else 0.0,
                "binary": s.get("strict_binary"),
                "margin": s.get("resistance_margin"),
                "outcome": outcome,
                "chosen": s.get("chosen"),
                "pinned": s.get("chosen") in pins,
                "cell": sp.parent.name, "exp": Path(d).name,
            })

    print(f"\n{'variant':12s} {'condition':10s} {'n':>3s} {'P* mean':>8s} {'P* sd':>7s} "
          f"{'B(met)':>7s} {'M(marg)':>8s} {'pin-buy%':>8s} {'outcomes'}")
    gaps = {}
    for var in VARIANT_ORDER:
        for cond in ("clean", "combined"):
            rows = cells.get((var, cond), [])
            if not rows:
                continue
            ps = [r["pstar"] for r in rows]
            mean = statistics.mean(ps)
            sd = statistics.stdev(ps) if len(ps) > 1 else 0.0
            # secondary reads: B = all-or-nothing met-or-0 rate; M = cross-level-fair margin
            # (missing on cells that predate the rescore fields -> fall back to P*-derived 0)
            bs = [r["binary"] if r["binary"] is not None else (1.0 if r["pstar"] >= 1.0 else 0.0)
                  for r in rows]
            ms = [r["margin"] if r["margin"] is not None else r["pstar"] for r in rows]
            pin_rate = 100.0 * sum(r["pinned"] for r in rows) / len(rows)
            from collections import Counter
            oc = dict(Counter(r["outcome"] for r in rows))
            print(f"{var:12s} {cond:10s} {len(rows):3d} {mean:8.3f} {sd:7.3f} "
                  f"{statistics.mean(bs):7.2f} {statistics.mean(ms):8.3f} "
                  f"{pin_rate:7.0f}% {oc}")
            gaps.setdefault(var, {})[cond] = mean
    print()
    for var in VARIANT_ORDER:
        g = gaps.get(var, {})
        if "clean" in g and "combined" in g:
            print(f"gap {var:12s} clean {g['clean']:.3f} - steered {g['combined']:.3f} "
                  f"= {g['clean'] - g['combined']:+.3f}")
    if infra_excluded:
        print(f"\ninfra-excluded cells: {infra_excluded}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
