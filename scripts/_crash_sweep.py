#!/usr/bin/env python
"""Report (and, only under an explicit destructive flag, delete) cells that FAILED because
INFRASTRUCTURE invalidated the measurement -- so the self-heal loop can re-run them.

The infra-vs-capability decision lives in ONE place, scripts/_infra_classify.py, which
scripts/build_figure_data.py imports too.  Read that module's docstring for the rule and
the evidence behind every signature.  Do not add signatures here.

HISTORY -- why this file no longer carries its own signature list.  It used to sweep on

    SIGS = ("ConnectionRefused", "reconnection attempts failed", "consecutive failures",
            "All 3 reconnection attempts failed", "no more fallbacks available",
            "Fallback LLM also failed", "validation error for AgentOutput",
            "TargetClosedError", ..., "RateLimitError", ..., "no team event for")

Three of those are capability failures or wrappers around them, and one is a substring
trap, so `delete` mode would have removed genuine model failures from results/ -- which
silently improves the score of exactly the models that fail that way:
  * "validation error for AgentOutput" -- the model emitted unparseable action JSON.
  * "Fallback LLM also failed" / "no more fallbacks available" -- wrappers whose payload
    is sometimes an HTTP fault and sometimes a parse failure; you must read inside.
  * "consecutive failures" -- names the guard that stopped the run, not what caused it.
  * "RateLimitError" -- a substring of "ModelRateLimitError", which the scaffold logs for
    a 429 it then RECOVERS from on the fallback deployment.
The only rule that survived unchanged is the zero-step one, which was already correct.

Usage:
    _crash_sweep.py <results_dir>                    # count  (default; read-only)
    _crash_sweep.py <results_dir> list               # per-cell classification + evidence
    _crash_sweep.py <results_dir> audit              # every give-up run, both classes
    _crash_sweep.py <results_dir> delete --i-understand-this-deletes-results
"""
import glob
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _infra_classify import INFRA, SCORED, classify_run, is_infra_fail   # noqa: F401,E402

DELETE_CONFIRM = "--i-understand-this-deletes-results"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    res = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "count"

    cells = sorted(os.path.dirname(f) for f in glob.glob(f"{res}/*/*/summary.json"))
    verdicts = [(cd, classify_run(cd)) for cd in cells]
    infra = [(cd, v) for cd, v in verdicts if v["class"] == INFRA]

    if mode == "count":
        print(len(infra))
        return 0

    if mode in ("list", "audit"):
        show = infra if mode == "list" else [(cd, v) for cd, v in verdicts
                                             if v["class"] != SCORED]
        for cd, v in show:
            print(f"{v['class']:10s} {v['code']:20s} steps={v['steps']:>4} "
                  f"{os.path.relpath(cd, res)}\n    {v['evidence'][:180]}")
        print(f"\n{len(show)} cells; {len(infra)} infra-excluded of {len(cells)} total")
        return 0

    if mode == "delete":
        if DELETE_CONFIRM not in sys.argv:
            print(f"REFUSING to delete {len(infra)} cells under {res}.\n"
                  f"This is destructive and irreversible.  Re-run with {DELETE_CONFIRM} "
                  f"if that is really what you want, after checking `list` output.",
                  file=sys.stderr)
            return 1
        for cd, _ in infra:
            shutil.rmtree(cd)
        print(len(infra))
        return 0

    print(f"unknown mode {mode!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
