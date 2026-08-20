"""Build and run an experiment over generated CAVEAT scenarios.

    python -m caveat.benchmark.run --name laptop_pilot --scenarios laptop \
        --conditions clean sponsored ranking drip promo trust --jobs 8

Repeats are separate experiments (``<name>_r1``, ``<name>_r2`` ...) written under the same
results directory.
"""

from __future__ import annotations

import argparse

from ..core.experiment import Experiment, Runner, auto_jobs
from . import registry
from .schema import STEERING_TYPES, VARIANTS

DEFAULT_SCENARIOS = ("laptop", "office_chair", "mattress", "backpack", "tent")

# steering conditions whose effect renders via the existing (un-rebuilt) frontend / backend data
BACKEND_CONDITIONS = ["clean", "combined", "sponsored", "ranking", "drip", "promo", "trust",
                      "scarcity", "addon"]
ALL_CONDITIONS = ["clean", *STEERING_TYPES]


def build_experiment(name, scenarios, conditions, *, scaffolds=("browseruse",),
                     models=("gpt-5.5",), max_steps=32, variants=VARIANTS,
                     base_port=9100) -> Experiment:
    tasks = []
    for sid in scenarios:
        tasks.extend(registry.benchmark_tasks(sid, variants=list(variants)))
    return Experiment(name=name, scaffolds=list(scaffolds), models=list(models),
                      tasks=tasks, conditions=list(conditions),
                      max_steps={s: max_steps for s in scaffolds}, base_port=base_port)


def main() -> int:
    ap = argparse.ArgumentParser(prog="caveat.benchmark.run")
    ap.add_argument("--name", required=True)
    ap.add_argument("--scenarios", nargs="*", default=list(DEFAULT_SCENARIOS))
    ap.add_argument("--conditions", nargs="*", default=BACKEND_CONDITIONS)
    ap.add_argument("--variants", nargs="*", default=list(VARIANTS))
    ap.add_argument("--scaffolds", nargs="*", default=["browseruse"])
    ap.add_argument("--models", nargs="*", default=["gpt-5.5"])
    ap.add_argument("--results", default="results")
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--max-steps", type=int, default=250)   # backstop, not a measured constraint (2026-07 policy)
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--base-port", type=int, default=9100)
    ap.add_argument("--no-headless", action="store_true")
    args = ap.parse_args()

    jobs = args.jobs or auto_jobs()
    for r in range(args.repeats):
        name = args.name if args.repeats == 1 else f"{args.name}_r{r + 1}"
        exp = build_experiment(name, args.scenarios, args.conditions,
                               scaffolds=args.scaffolds, models=args.models,
                               max_steps=args.max_steps, variants=args.variants,
                               base_port=args.base_port)
        print(f"[run] {name}: {len(exp.cells())} cells (jobs={jobs})")
        Runner(results_dir=args.results, headless=not args.no_headless).run(exp, jobs=jobs)
        # Backfill the one release metric for compatibility with any evaluator that did
        # not place it directly in the summary.
        try:
            from ..scoring.optimal_selection import write_optimal_selection
            count = write_optimal_selection(f"{args.results.rstrip('/')}/{name}")
            print(f"[run] {name}: wrote optimal-selection indicators into {count} summaries")
        except Exception as e:
            print(f"[run] optimal-selection scoring skipped: {e}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
