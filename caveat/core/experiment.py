# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Experiments = a matrix of (environment × scaffold × model × task × condition),
run in parallel and written to disk in the shared trajectory format.

The runner fans the matrix out across worker processes, with one isolated browser
and environment server per cell. One crash cannot poison the full run, and the
matrix is resumable: a completed cell is skipped on restart.

    exp = Experiment(name="laptops", scaffolds=["browseruse"],
                     models=["gpt-5.5", "gpt-4.1"], tasks=tasks,
                     conditions=["clean", "steered"])
    Runner(results_dir="results").run(exp, jobs=8)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from .environment import ENVIRONMENTS, ServerHandle
from .models import ModelSpec
from .scaffold import SCAFFOLDS, RawTrajectory, RunContext
from .task import TaskSpec
from .trajectory import Evaluation, Trajectory

# browseruse raised 40->60 (2026-06-28, owner-approved: max_steps is a resource budget, not the harness):
# Nested environments need room for the agent to browse listings and complete via the UI
# rather than prematurely calling done() when it senses a tight step budget.
# 2026-07 policy: step budgets are a SAFETY BACKSTOP against pathological loops, not a
# measured constraint — set high enough that a persistent agent never fails because of them.
# (The budget is invisible to the agent, so raising it cannot change uncapped trajectories;
# supplement data showed budget-capped give-ups were mostly agents still productively
# searching.) Report per-arm cap-rates with every run; they should be ~0.
DEFAULT_STEPS = {"browseruse": 250, "caveat-harness": 250}


def auto_jobs() -> int:
    """A sensible default parallelism for this machine. Each cell runs a browser +
    a server (~0.7GB RAM, mostly I/O-bound waiting on the model), so we cap by both
    CPU and memory and leave some headroom."""
    import os

    cpu = os.cpu_count() or 4
    mem_gb = _total_mem_gb()
    by_mem = int(mem_gb / 1.5) if mem_gb else cpu
    return max(1, min(cpu, by_mem, 24))


def _total_mem_gb() -> float:
    import os

    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") / (1024**3)
    except (ValueError, OSError, AttributeError):
        try:
            with open("/proc/meminfo") as meminfo:
                for line in meminfo:
                    if line.startswith("MemTotal"):
                        return int(line.split()[1]) / (1024**2)
        except OSError:
            pass
    return 0.0


@dataclass
class Cell:
    env: str
    scaffold: str
    model: ModelSpec
    task: TaskSpec
    condition: str
    port: int

    @property
    def name(self) -> str:
        safe = lambda s: str(s).replace("/", "-")
        return "__".join(
            safe(x)
            for x in (
                self.env,
                self.scaffold,
                self.model.name,
                self.task.task_id,
                self.condition,
            )
        )


@dataclass
class Experiment:
    name: str
    scaffolds: list[str]
    models: list  # list[str | dict | ModelSpec]
    tasks: list[TaskSpec]
    conditions: list[str] = field(default_factory=lambda: ["clean"])
    max_steps: dict[str, int] = field(default_factory=dict)  # per-scaffold override
    base_port: int = 9100
    plugins: list[str] = field(
        default_factory=list
    )  # import paths registering custom scaffolds
    tier: str | None = None

    def model_specs(self) -> list[ModelSpec]:
        return [ModelSpec.parse(m) for m in self.models]

    def cells(self) -> list[Cell]:
        out: list[Cell] = []
        i = 0
        for task in self.tasks:
            for scaffold in self.scaffolds:
                for model in self.model_specs():
                    for cond in self.conditions:
                        out.append(
                            Cell(
                                task.env,
                                scaffold,
                                model,
                                task,
                                cond,
                                self.base_port + i,
                            )
                        )
                        i += 1
        return out

    def steps_for(self, scaffold: str) -> int:
        return self.max_steps.get(scaffold, DEFAULT_STEPS.get(scaffold, 30))


# --------------------------------------------------------------------------- #
# Run a single cell (this is what each worker process executes)
# --------------------------------------------------------------------------- #
def run_cell(
    env_name: str,
    scaffold_name: str,
    model: ModelSpec,
    task: TaskSpec,
    condition: str,
    port: int,
    out_dir: Path,
    *,
    max_steps: int = 30,
    headless: bool = True,
) -> Trajectory:
    """Execute one (env, scaffold, model, task, condition) cell and save it."""
    out_dir = Path(out_dir)
    env = ENVIRONMENTS.create(env_name)
    scaffold = SCAFFOLDS.create(scaffold_name)
    task = TaskSpec.parse(task) if isinstance(task, dict) else task

    ok, reason = scaffold.supports(model)
    if not ok:
        traj = Trajectory(
            env_name,
            scaffold_name,
            model.name,
            task.task_id,
            condition,
            task.instruction,
            task.preferences,
            evaluation=Evaluation("skipped", success=False),
            stats={"error": f"unsupported: {reason}", "skipped": True},
        )
        traj.save(out_dir)
        return traj

    handle: ServerHandle | None = None
    t0 = time.time()
    task.condition = condition  # the env seeds the right (clean/steered) catalog
    try:
        handle = env.start(
            port, task, work_dir=out_dir
        )  # server DB lives in the cell dir
        ctx = RunContext(
            task=task,
            start_url=env.start_url(port, task),
            model=model,
            work_dir=out_dir / "_work",
            max_steps=max_steps,
            headless=headless,
        )
        ctx.work_dir.mkdir(parents=True, exist_ok=True)
        raw: RawTrajectory = scaffold.run(ctx)
        evaluation = env.evaluate(handle, task)
        stats = {
            "seconds": round(time.time() - t0, 1),
            "num_steps": len(raw.steps),
            **raw.stats,
        }
        traj = Trajectory(
            env_name,
            scaffold_name,
            model.name,
            task.task_id,
            condition,
            task.instruction,
            task.preferences,
            steps=raw.steps,
            answer=raw.answer,
            evaluation=evaluation,
            stats=stats,
        )
    except Exception as e:  # noqa: BLE001
        import traceback

        traj = Trajectory(
            env_name,
            scaffold_name,
            model.name,
            task.task_id,
            condition,
            task.instruction,
            task.preferences,
            evaluation=Evaluation("error", success=False),
            stats={
                "seconds": round(time.time() - t0, 1),
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc()[-2000:],
            },
        )
    finally:
        if handle is not None:
            handle.stop()
    traj.save(out_dir)
    return traj


# --------------------------------------------------------------------------- #
# Runner — fan the matrix out across worker processes
# --------------------------------------------------------------------------- #
_GLYPH = {
    "compliant": "✓",
    "success": "✓",
    "violation": "○",
    "decoy": "⚠",
    "advertised": "⚠",
    "none": "–",
    "error": "✗",
    "skipped": "·",
}


class Runner:
    def __init__(
        self, results_dir: Path | str = "results", headless: bool = True
    ) -> None:
        # Absolute so per-cell DB/output paths survive the cwd=server_dir subprocesses.
        self.results_dir = Path(results_dir).resolve()
        self.headless = headless

    def run(self, exp: Experiment, *, jobs: int = 1, force: bool = False) -> Path:
        if exp.plugins:
            import importlib

            for module in exp.plugins:
                importlib.import_module(module)
        exp_dir = self.results_dir / exp.name
        exp_dir.mkdir(parents=True, exist_ok=True)
        (exp_dir / "experiment.json").write_text(
            json.dumps(_exp_manifest(exp), indent=2, default=str)
        )

        cells = [c for c in exp.cells() if force or not _is_done(exp_dir / c.name)]
        skipped = len(exp.cells()) - len(cells)
        print(f"\n=== experiment: {exp.name}  →  {exp_dir} ===")
        print(f"    {len(exp.cells())} cells ({skipped} already done), jobs={jobs}\n")

        running: list[tuple[Cell, subprocess.Popen, object]] = []
        pending = list(cells)
        done = 0
        while pending or running:
            while pending and len(running) < jobs:
                cell = pending.pop(0)
                out_dir = exp_dir / cell.name
                out_dir.mkdir(parents=True, exist_ok=True)
                spec = _cell_spec(exp, cell, out_dir, self.headless)
                # The process poll loop owns and closes this long-lived stream.
                log = open(out_dir / "run.log", "w")  # noqa: SIM115
                p = subprocess.Popen(
                    [sys.executable, "-m", "caveat.run_cell"],
                    stdin=subprocess.PIPE,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=os.environ.copy(),
                )
                p.stdin.write(json.dumps(spec))
                p.stdin.close()
                running.append((cell, p, log))
                print(
                    f"  ▶ {cell.name}  (port {cell.port})  [{len(running)} running, {len(pending)} queued]"
                )
                # Optional launch pacing: N chromiums + env servers cold-starting in the same
                # instant can starve each other into mass navigation timeouts (0-step cells).
                # CAVEAT_SPAWN_STAGGER=<seconds> spaces out spawns; 0/absent = no pacing.
                stagger = float(os.environ.get("CAVEAT_SPAWN_STAGGER", "0") or "0")
                if stagger > 0 and pending:
                    time.sleep(stagger)
            still = []
            for cell, p, log in running:
                if p.poll() is None:
                    still.append((cell, p, log))
                    continue
                log.close()
                done += 1
                s = _read_summary(exp_dir / cell.name)
                g = _GLYPH.get(s.get("outcome", "error"), "?")
                print(
                    f"  {g} {cell.name}  → {s.get('outcome')} "
                    f"chosen={s.get('chosen_label') or s.get('chosen')} "
                    f"({s.get('seconds')}s)  [{done}/{len(cells)}]"
                )
            running = still
            if pending or running:
                time.sleep(1.5)

        self._write_index(exp, exp_dir)
        print(f"\nResults: {exp_dir}\n")
        return exp_dir

    def _write_index(self, exp: Experiment, exp_dir: Path) -> None:
        results = []
        for cell in exp.cells():
            s = _read_summary(exp_dir / cell.name)
            if s:
                results.append(s)
        (exp_dir / "index.json").write_text(
            json.dumps(
                {"experiment": exp.name, "results": results}, indent=2, default=str
            )
        )


# --------------------------------------------------------------------------- #
# (de)serialization helpers shared with the worker
# --------------------------------------------------------------------------- #
def _cell_spec(exp: Experiment, cell: Cell, out_dir: Path, headless: bool) -> dict:
    return {
        "env": cell.env,
        "scaffold": cell.scaffold,
        "condition": cell.condition,
        "port": cell.port,
        "out_dir": str(out_dir),
        "headless": headless,
        "max_steps": exp.steps_for(cell.scaffold),
        "plugins": exp.plugins,
        "model": _model_to_dict(cell.model),
        "task": _task_to_dict(cell.task),
    }


def _model_to_dict(m: ModelSpec) -> dict:
    return {
        "name": m.name,
        "provider": m.provider,
        "base_url": m.base_url,
        "api_key": m.api_key,
        "deployment": m.deployment,
        "vision": m.vision,
        "extra": m.extra,
    }


def _task_to_dict(t: TaskSpec) -> dict:
    return {
        "task_id": t.task_id,
        "env": t.env,
        "instruction": t.instruction,
        "preferences": t.preferences,
        "catalog": t.catalog,
        "start_path": t.start_path,
        "params": t.params,
        "metadata": t.metadata,
    }


def _exp_manifest(exp: Experiment) -> dict:
    return {
        "name": exp.name,
        "tier": exp.tier,
        "scaffolds": exp.scaffolds,
        "models": [m.name for m in exp.model_specs()],
        "conditions": exp.conditions,
        "plugins": exp.plugins,
        "tasks": [_task_to_dict(t) for t in exp.tasks],
    }


def _read_summary(cell_dir: Path) -> dict:
    f = cell_dir / "summary.json"
    return json.loads(f.read_text()) if f.exists() else {}


def _is_done(cell_dir: Path) -> bool:
    """A cell counts as done only if it finished without error — so a re-run
    automatically retries errored/skipped cells (no --force needed)."""
    s = _read_summary(cell_dir)
    return bool(s) and s.get("outcome") not in ("error", "skipped", None)
