"""Cleanup pass: re-run expansion cells that came back `none` (no purchase) — these are concentrated
in slow/weaker models (gpt-5.2, gpt-5.4-mini, DeepSeek, gpt-oss) that under-complete under the 10-cell
contention used for throughput. Deletes the `none` cell dirs so run.py re-runs only them, at LOW
concurrency (jobs 3) where pages are faster and the slow models complete.

Usage: python scripts/cleanup_none.py oc_exp mat_exp bp_exp tent_exp
"""
import glob
import json
import shutil
import subprocess
import sys

PY = ".venv/bin/python"
VARIANTS = ["thresholded", "mixed", "graded", "graded3", "graded4"]
CONDS = ["clean", "combined"]
SCEN = {"oc_exp": "office_chair", "mat_exp": "mattress", "bp_exp": "backpack", "tent_exp": "tent"}
EXP_MODELS = ["phyagi/gpt-5", "phyagi/gpt-5.1", "phyagi/gpt-5.2", "phyagi/gpt-5.4",
              "phyagi/gpt-5.4-mini", "phyagi/gpt-5.4-nano",
              "phyagi/gpt-5.5#low", "phyagi/gpt-5.5#medium", "phyagi/gpt-5.5#high",
              "phyagi/DeepSeek-V4-Pro", "grok-4-1-fast-non-reasoning", "gpt-oss-120b"]
PORT = {"oc_exp": 9100, "mat_exp": 9300, "bp_exp": 9500, "tent_exp": 9700}


def main():
    names = sys.argv[1:] or list(SCEN)
    # gpt-5.2 fails COMBINED regardless of concurrency (capability limit, not contention — probe
    # confirmed: clean P=1.0, combined none even at jobs2). Don't re-run it; it's dropped from figures.
    SKIP_MODELS = {"gpt-5.2"}
    for name in names:
        # delete none-cell dirs (except the skip-models, whose summaries stay so run.py skips them)
        deleted = 0
        for sf in glob.glob(f"results/{name}_r*/*/summary.json"):
            try:
                d = json.load(open(sf))
            except Exception:
                continue
            if d.get("preservation") is None and d.get("model") not in SKIP_MODELS:
                shutil.rmtree(sf.rsplit("/", 1)[0], ignore_errors=True)
                deleted += 1
        print(f"[{name}] deleted {deleted} none-cell dirs; re-running at jobs 3 ...", flush=True)
        # re-run the run (run.py skips the still-present cells, re-runs only the deleted none ones)
        reps = max(len(glob.glob(f"results/{name}_r*")), 2)
        subprocess.run([PY, "-m", "agentarena.benchmark.run", "--name", name,
                        "--scenarios", SCEN[name], "--variants", *VARIANTS, "--conditions", *CONDS,
                        "--models", *EXP_MODELS, "--jobs", "4", "--max-steps", "85",
                        "--base-port", str(PORT[name]), "--repeats", str(reps), "--results", "results"])
        print(f"[{name}] cleanup re-run done", flush=True)


if __name__ == "__main__":
    main()
