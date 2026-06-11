"""Build + run an experiment over generated benchmark scenarios.

    python -m agentarena.benchmark.run --name laptop_pilot --scenarios laptop \
        --conditions clean sponsored ranking drip promo trust --jobs 8

Repeats are separate experiments (``<name>_r1``, ``<name>_r2`` ...) written under the same
results dir; the scorer pairs clean<->steered within each repeat.
"""

from __future__ import annotations

import argparse

from ..core.experiment import Experiment, Runner, auto_jobs
from . import registry
from .scenarios import THIS_PASS
from .schema import STEERING_TYPES, VARIANTS

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
    ap = argparse.ArgumentParser(prog="agentarena.benchmark.run")
    ap.add_argument("--name", required=True)
    ap.add_argument("--scenarios", nargs="*", default=THIS_PASS)
    ap.add_argument("--conditions", nargs="*", default=BACKEND_CONDITIONS)
    ap.add_argument("--variants", nargs="*", default=list(VARIANTS))
    ap.add_argument("--scaffolds", nargs="*", default=["browseruse"])
    ap.add_argument("--models", nargs="*", default=["gpt-5.5"])
    ap.add_argument("--results", default="results")
    ap.add_argument("--jobs", type=int, default=0)
    ap.add_argument("--max-steps", type=int, default=32)
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
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
