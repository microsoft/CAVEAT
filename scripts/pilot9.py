#!/usr/bin/env python
"""BrowserUse pilot across environments, models, conditions, and preference variants.
Uses the generic core.experiment.Runner (NOT caveat_shop's benchmark.run). Resumable (done cells skipped).

  python scripts/pilot9.py caveat_sport caveat_grocery ... --repeats 2 --jobs 10 \
      --models gpt-5.5#high,gpt-4.1 --variants thresholded,graded,graded4
"""
import argparse
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from caveat.core.experiment import Experiment, Runner  # noqa: E402


def env_tasks(env, variants):
    importlib.import_module(f"caveat.envs.{env}")          # register the env
    m = importlib.import_module(f"caveat.envs.{env}.tasks")
    return [t for t in m.TASKS if t.task_id.rsplit("-", 1)[-1] in variants]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("envs", nargs="+")
    ap.add_argument("--repeats", type=int, default=5)      # the published measurements use n=5
    ap.add_argument("--jobs", type=int, default=6)         # >6 provoked TRAPI 429 spikes; 6 is the proven safe ceiling
    ap.add_argument("--models", default="gpt-5.5#high,gpt-4.1")
    ap.add_argument("--variants", default="thresholded,mixed,graded,graded3,graded4")
    ap.add_argument("--conditions", default="clean,steered")
    ap.add_argument("--base-port", type=int, default=8900)
    ap.add_argument("--results", default="results/pilot9")
    ap.add_argument("--max-steps", type=int, default=250)   # backstop, not a measured constraint (2026-07 policy)
    ap.add_argument("--repeat-ids", default="", help="comma list of repeat indices to run (parallel-repeats mode)")
    a = ap.parse_args()

    models = a.models.split(",")
    variants = a.variants.split(",")
    conds = a.conditions.split(",")
    runner = Runner(results_dir=a.results, headless=True)
    # Repeats OUTER, envs INNER: finish r1 across ALL envs before r2, so a broad first read (n=1 on every
    # env) lands early and data-design failures surface before the whole n=3 budget is spent. Cell names
    # (env_r{r}) are unchanged, so resumability/accumulation is identical to the env-outer order.
    # --repeat-ids "2" or "2,4": run ONLY those repeat indices (parallel-repeats orchestration —
    # launch one invocation per repeat on distinct --base-port windows to eliminate the sequential
    # repeat/env tails). Default: 1..repeats as before.
    rids = ([int(x) for x in a.repeat_ids.split(",") if x.strip()]
            if getattr(a, "repeat_ids", "") else list(range(1, a.repeats + 1)))
    for r in rids:
        for env in a.envs:
            tasks = env_tasks(env, variants)
            if not tasks:
                print(f"!! {env}: no tasks for variants {variants}", flush=True)
                continue
            exp = Experiment(name=f"{env}_r{r}", scaffolds=["browseruse"], models=models,
                             tasks=tasks, conditions=conds, base_port=a.base_port,
                             max_steps={"browseruse": a.max_steps})
            print(f"=== {env} repeat {r}: {len(exp.cells())} cells ===", flush=True)
            runner.run(exp, jobs=a.jobs)


if __name__ == "__main__":
    main()
