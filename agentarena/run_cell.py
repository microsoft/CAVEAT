"""Worker entrypoint: run a single experiment cell from a JSON spec on stdin.

The Runner spawns one of these per cell (`python -m agentarena.run_cell`), so each
(env, scaffold, model, task, condition) gets an isolated process + browser + server.
Importing ``agentarena.envs`` and ``agentarena.scaffolds`` registers every plugin.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import agentarena.envs   # noqa: F401  (registers environments)
import agentarena.scaffolds  # noqa: F401  (registers scaffolds)
from agentarena.core.experiment import run_cell
from agentarena.core.models import ModelSpec
from agentarena.core.task import TaskSpec


def main() -> int:
    spec = json.loads(sys.stdin.read())
    model = ModelSpec.parse(spec["model"])
    task = TaskSpec.parse(spec["task"])
    traj = run_cell(
        env_name=spec["env"], scaffold_name=spec["scaffold"], model=model, task=task,
        condition=spec["condition"], port=spec["port"], out_dir=Path(spec["out_dir"]),
        max_steps=spec.get("max_steps", 30), headless=spec.get("headless", True))
    s = traj.summary()
    print(f"DONE {s['outcome']} chosen={s.get('chosen')} steps={s['num_steps']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
