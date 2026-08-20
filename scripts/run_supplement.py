#!/usr/bin/env python
"""Re-run an explicit list of cells at a HIGHER step budget, into a separately-labeled results
tree (never mixed into the uniform-budget main matrix). Used for the at-cap give-up supplement:
cells whose agent exhausted the main matrix's step budget while still searching get one re-run
at --max-steps 80 so budget exhaustion can be separated from genuine give-up.

    python scripts/run_supplement.py scratchpad/atcap_cells.json \
        --results results/overhaul_b_supp80 --max-steps 80 --jobs 10 --base-port 9800

The cell list is JSON: [[task_id, model_name, condition, orig_steps], ...] where task_id is
"<scenario>-<variant>" and model_name a routed logical name like "gpt-5.5-low" (the '#effort'
form is reconstructed).
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

_EFFORTS = ("low", "medium", "high")


def _model_arg(name: str) -> str:
    """gpt-5.5-low -> gpt-5.5#low ; names without an effort suffix pass through."""
    for e in _EFFORTS:
        if name.endswith("-" + e):
            return f"{name[: -len(e) - 1]}#{e}"
    return name


def _run_one(args):
    task_id, model_name, condition, port, out_root, max_steps = args
    import caveat.envs      # register envs + benchmark catalogs
    import caveat.scaffolds
    from caveat.benchmark import registry
    from caveat.core.experiment import run_cell
    from caveat.core.models import ModelSpec

    scenario, variant = task_id.rsplit("-", 1)
    task = registry.benchmark_tasks(scenario, variants=[variant])[0]
    model = ModelSpec.parse(_model_arg(model_name))
    cell_name = f"caveat_shop__browseruse__{model.name}__{task_id}__{condition}"
    out_dir = Path(out_root) / cell_name
    if (out_dir / "summary.json").exists():
        return f"skip {cell_name}"
    try:
        traj = run_cell("caveat_shop", "browseruse", model, task, condition, port, out_dir,
                        max_steps=max_steps, headless=True)
        return f"done {cell_name} -> {traj.evaluation.outcome if traj.evaluation else '?'}"
    except Exception as e:
        return f"ERROR {cell_name}: {type(e).__name__} {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cells_json")
    ap.add_argument("--results", default="results/overhaul_b_supp80")
    ap.add_argument("--max-steps", type=int, default=80)
    ap.add_argument("--jobs", type=int, default=10)
    ap.add_argument("--base-port", type=int, default=9800)
    a = ap.parse_args()

    cells = json.loads(Path(a.cells_json).read_text())
    # de-dup (task_id, model, condition) — the list may contain repeats across experiment dirs;
    # the supplement measures each unique cell once
    seen, uniq = set(), []
    for task_id, model, condition, *_ in cells:
        key = (task_id, model, condition)
        if key not in seen:
            seen.add(key)
            uniq.append(key)
    out_root = Path(a.results) / "supp80"
    out_root.mkdir(parents=True, exist_ok=True)
    jobs = [(t, m, c, a.base_port + i, str(out_root), a.max_steps)
            for i, (t, m, c) in enumerate(uniq)]
    print(f"[supplement] {len(jobs)} unique cells at max_steps={a.max_steps} (jobs={a.jobs})")
    with ProcessPoolExecutor(max_workers=a.jobs) as ex:
        for msg in ex.map(_run_one, jobs):
            print(" ", msg, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
