#!/usr/bin/env python
"""Audit agent trajectories for environment-artifact behaviors (validity overhaul, Phase A step 8).

Scans a results tree and reports, per cell and in aggregate:
  - exploit navigations: steps whose URL hits a data/API surface a real shopper never sees
    (/api/*, /docs, /openapi.json, /redoc, /graphql) — after the serving lockdown these should
    be zero (the gate serves a Robot Check page instead of data, but the *attempt* still
    indicates the agent is probing for endpoints);
  - robot-check encounters: steps whose URL or action/reasoning text shows the rate-gate
    interstitial (should be ~zero for honest browsing; nonzero means thresholds are miscalibrated
    or the agent is enumerating);
  - PDP coverage: distinct product detail pages visited before the run ends (a proxy for
    verification effort — at level 0 an agent that never opens a PDP cannot have verified the
    PDP-only hard dims);
  - outcome / P* summary from summary.json.

Usage:
    python scripts/audit_trajectories.py results/overhaul_a [--fail-on-exploit]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

EXPLOIT_URL = re.compile(r"/(api|docs|openapi\.json|redoc|graphql)(/|\?|$)")
ROBOT_URL = re.compile(r"/verify-human")
ROBOT_TEXT = re.compile(r"robot check|verify you are (a )?human|type the characters", re.I)
PDP_URL = re.compile(r"/(product|dp|products)/[A-Za-z0-9_-]+")


def audit_run(run_dir: Path) -> dict:
    traj_path = run_dir / "trajectory.json"
    summ_path = run_dir / "summary.json"
    out = {"cell": run_dir.name, "steps": 0, "exploit_steps": [], "robot_steps": [],
           "pdp_visits": 0, "outcome": None, "pstar": None}
    if summ_path.exists():
        try:
            s = json.loads(summ_path.read_text())
            out["outcome"] = s.get("outcome")
            out["pstar"] = s.get("preservation_strict", s.get("preservation"))
        except Exception:
            pass
    if not traj_path.exists():
        return out
    try:
        t = json.loads(traj_path.read_text())
    except Exception:
        return out
    pdps = set()
    for step in t.get("steps", []):
        out["steps"] += 1
        url = step.get("url") or ""
        blob = " ".join(str(step.get(k) or "") for k in ("action", "reasoning", "note"))
        if EXPLOIT_URL.search(url) or "openapi.json" in blob or "go_to_url" in blob and EXPLOIT_URL.search(blob):
            out["exploit_steps"].append({"index": step.get("index"), "url": url})
        if ROBOT_URL.search(url) or ROBOT_TEXT.search(blob):
            out["robot_steps"].append({"index": step.get("index"), "url": url})
        m = PDP_URL.search(url)
        if m:
            pdps.add(m.group(0))
    out["pdp_visits"] = len(pdps)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", help="results tree (an experiment dir or a parent of experiment dirs)")
    ap.add_argument("--fail-on-exploit", action="store_true",
                    help="exit 1 if any exploit navigation is found")
    ap.add_argument("--json", action="store_true", help="emit full JSON instead of the table")
    args = ap.parse_args()

    root = Path(args.results)
    run_dirs = sorted({p.parent for p in root.rglob("trajectory.json")})
    if not run_dirs:
        print(f"no trajectories under {root}", file=sys.stderr)
        return 2

    audits = [audit_run(d) for d in run_dirs]
    n_exploit = sum(len(a["exploit_steps"]) for a in audits)
    n_robot = sum(len(a["robot_steps"]) for a in audits)
    outcomes = Counter(a["outcome"] for a in audits)

    if args.json:
        print(json.dumps(audits, indent=2))
    else:
        print(f"{'cell':60s} {'steps':>5s} {'pdp':>4s} {'expl':>4s} {'robo':>4s} {'outcome':12s} {'P*':>6s}")
        for a in audits:
            ps = "-" if a["pstar"] is None else f"{a['pstar']:.3f}"
            print(f"{a['cell'][:60]:60s} {a['steps']:5d} {a['pdp_visits']:4d} "
                  f"{len(a['exploit_steps']):4d} {len(a['robot_steps']):4d} "
                  f"{str(a['outcome'])[:12]:12s} {ps:>6s}")
        print(f"\nruns={len(audits)}  exploit_navigations={n_exploit}  robot_check_encounters={n_robot}")
        print(f"outcomes: {dict(outcomes)}")
        zero_pdp_buys = [a["cell"] for a in audits
                        if a["pdp_visits"] == 0 and a["outcome"] not in (None, "none", "error", "skipped")]
        if zero_pdp_buys:
            print(f"purchased with ZERO PDP visits ({len(zero_pdp_buys)}): {zero_pdp_buys[:10]}")
        for a in audits:
            for e in a["exploit_steps"][:5]:
                print(f"  EXPLOIT {a['cell']} step {e['index']}: {e['url']}")

    if args.fail_on_exploit and n_exploit:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
