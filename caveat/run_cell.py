# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Worker entrypoint: run a single experiment cell from a JSON spec on stdin.

The Runner spawns one of these per cell (`python -m caveat.run_cell`), so each
(env, scaffold, model, task, condition) gets an isolated process + browser + server.
Importing ``caveat.envs`` and ``caveat.scaffolds`` registers every plugin.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import caveat.envs
import caveat.scaffolds  # noqa: F401  (registers scaffolds)
from caveat.core.experiment import run_cell
from caveat.core.models import ModelSpec
from caveat.core.task import TaskSpec


def main() -> int:
    spec = json.loads(sys.stdin.read())
    for module in spec.get("plugins", []):
        importlib.import_module(module)
    model = ModelSpec.parse(spec["model"])
    task = TaskSpec.parse(spec["task"])
    traj = run_cell(
        env_name=spec["env"],
        scaffold_name=spec["scaffold"],
        model=model,
        task=task,
        condition=spec["condition"],
        port=spec["port"],
        out_dir=Path(spec["out_dir"]),
        max_steps=spec.get("max_steps", 30),
        headless=spec.get("headless", True),
    )
    s = traj.summary()
    print(f"DONE {s['outcome']} chosen={s.get('chosen')} steps={s['num_steps']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
