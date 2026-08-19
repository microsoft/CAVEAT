#!/usr/bin/env python
"""Delete cell dirs poisoned by a TRAPI 503 outage so a re-run actually retries them.

A 503-crashed browser-use cell writes outcome="none" (the agent technically ran but every
LLM call failed, so it bought nothing) — and core.experiment._is_done() counts "none" as DONE,
so a plain re-run SKIPS it forever. We detect the infra signature in run.log and remove only
those cells. A genuine no-buy "none" (agent browsed, no 503) is a REAL result and is KEPT.

  python scripts/purge_503.py results/byenv [env_substr]
"""
import shutil
import sys
from pathlib import Path

SIG = ("all-backends-unhealthy", "consecutive failures",
       "All backends for this model are temporarily unavailable")

root = Path(sys.argv[1] if len(sys.argv) > 1 else "results/byenv")
envsub = sys.argv[2] if len(sys.argv) > 2 else ""
purged = 0
for runlog in root.rglob("run.log"):
    cell = runlog.parent
    if envsub and envsub not in str(cell):
        continue
    try:
        txt = runlog.read_text(errors="ignore")
    except Exception:
        continue
    if not any(s in txt for s in SIG):
        continue
    # A cell can hit a transient 503 yet RECOVER to a valid buy — keep those. Only purge if
    # the 503 left the cell with no valid outcome (none/error/skipped/missing trajectory).
    outcome = None
    tj = cell / "trajectory.json"
    if tj.exists():
        try:
            import json
            outcome = (json.loads(tj.read_text()).get("evaluation") or {}).get("outcome")
        except Exception:
            outcome = None
    if outcome in ("none", "error", "skipped", None):
        shutil.rmtree(cell, ignore_errors=True)
        purged += 1
        print(f"purged {cell.relative_to(root)} (outcome={outcome})")
print(f"== purged {purged} infra-poisoned cells under {root} {('('+envsub+')') if envsub else ''}")
