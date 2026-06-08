"""``agentarena`` command-line interface.

    agentarena ls                          # list environments, scaffolds, tasks
    agentarena run examples/configs/laptops.yaml
    agentarena run --env amazon --scaffolds browseruse --models gpt-5.5 gpt-4.1 \
                   --conditions clean steered --jobs 4
    agentarena view                        # launch the trajectory viewer
    agentarena setup                       # build the airbnb UI + check the browser
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_config(path: str) -> dict:
    text = Path(path).read_text()
    if path.endswith((".yaml", ".yml")):
        import yaml
        return yaml.safe_load(text)
    return json.loads(text)


def _resolve_tasks(items, envs):
    import agentarena.envs as E
    from agentarena.core.task import TaskSpec, load_tasks
    by_id = {f"{t.env}:{t.task_id}": t for t in E.ALL_TASKS}
    by_id.update({t.task_id: t for t in E.ALL_TASKS})
    if not items:
        return [t for t in E.ALL_TASKS if not envs or t.env in envs]
    out = []
    for it in items:
        if isinstance(it, str):
            if it in by_id:
                out.append(by_id[it])
            elif Path(it).exists():
                out.extend(load_tasks(it))
            else:
                raise SystemExit(f"unknown task {it!r}. Known: {', '.join(sorted(by_id))}")
        else:
            out.append(TaskSpec.parse(it))
    return out


def _build_experiment(cfg: dict):
    from agentarena.core.experiment import Experiment
    envs = cfg.get("envs") or []
    tasks = _resolve_tasks(cfg.get("tasks"), envs)
    if not tasks:
        raise SystemExit("no tasks selected")
    return Experiment(
        name=cfg.get("name", "exp"),
        scaffolds=cfg.get("scaffolds", ["simple"]),
        models=cfg.get("models", ["gpt-5.5"]),
        tasks=tasks,
        conditions=cfg.get("conditions", ["clean"]),
        max_steps=cfg.get("max_steps", {}),
        base_port=cfg.get("base_port", 9100),
    )


def cmd_run(args) -> int:
    import agentarena.envs   # noqa: F401  (register)
    import agentarena.scaffolds  # noqa: F401
    from agentarena.core.experiment import Runner

    cfg = _load_config(args.config) if args.config else {}
    for key, val in (("name", args.name), ("scaffolds", args.scaffolds),
                     ("models", args.models), ("conditions", args.conditions),
                     ("tasks", args.tasks)):
        if val:
            cfg[key] = val
    if args.env:
        cfg["envs"] = [args.env]
    exp = _build_experiment(cfg)
    Runner(results_dir=args.results, headless=not args.no_headless).run(
        exp, jobs=args.jobs, force=args.force)
    return 0


def cmd_view(args) -> int:
    from agentarena.viewer.app import serve
    serve(results_dir=args.results, port=args.port)
    return 0


def cmd_ls(args) -> int:
    import agentarena.envs   # noqa: F401
    import agentarena.scaffolds  # noqa: F401
    from agentarena.core.environment import ENVIRONMENTS
    from agentarena.core.scaffold import SCAFFOLDS
    from agentarena.envs import ALL_TASKS
    from agentarena.llm_client import TRAPI_DEPLOY

    print("environments:", ", ".join(ENVIRONMENTS.names()))
    print("scaffolds:   ", ", ".join(SCAFFOLDS.names()))
    print("\ntasks:")
    for t in ALL_TASKS:
        print(f"  {t.env}:{t.task_id:12s}  {t.instruction[:70]}...")
    print("\nmodels (trapi logical names; or bring your own via a dict):")
    print("  " + ", ".join(sorted(TRAPI_DEPLOY)[:24]) + ", ...")
    return 0


def cmd_setup(args) -> int:
    import shutil
    import subprocess

    from agentarena.scaffolds._browser import find_chromium
    root = Path(__file__).resolve().parent
    print("→ Chromium:", find_chromium() or "NOT FOUND (run `python -m playwright install chromium`)")
    print("→ Node:", shutil.which("node") or "not found (needed only for the stagehand scaffold)")

    airbnb_fe = root / "envs" / "airbnb" / "server" / "frontend"
    if not (airbnb_fe / "dist" / "index.html").exists() and shutil.which("npm"):
        print(f"→ Building airbnb frontend in {airbnb_fe} ...")
        subprocess.run(["npm", "install"], cwd=airbnb_fe, check=False)
        subprocess.run(["npm", "run", "build"], cwd=airbnb_fe, check=False)
    print("→ airbnb dist:", "built" if (airbnb_fe / "dist" / "index.html").exists() else "MISSING")
    print("→ amazon dist:", "built" if (root / "envs/amazon/server/frontend/dist/index.html").exists() else "MISSING")
    print("\nDone. Try:  agentarena run --env amazon --scaffolds simple --models gpt-5.5")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="agentarena", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run an experiment")
    r.add_argument("config", nargs="?", help="YAML/JSON experiment config (optional)")
    r.add_argument("--name"); r.add_argument("--env")
    r.add_argument("--scaffolds", nargs="+"); r.add_argument("--models", nargs="+")
    r.add_argument("--tasks", nargs="+"); r.add_argument("--conditions", nargs="+")
    r.add_argument("--jobs", type=int, default=1); r.add_argument("--results", default="results")
    r.add_argument("--no-headless", action="store_true"); r.add_argument("--force", action="store_true")
    r.set_defaults(func=cmd_run)

    v = sub.add_parser("view", help="launch the trajectory viewer")
    v.add_argument("--results", default="results"); v.add_argument("--port", type=int, default=8800)
    v.set_defaults(func=cmd_view)

    sub.add_parser("ls", help="list environments / scaffolds / tasks").set_defaults(func=cmd_ls)
    sub.add_parser("setup", help="build UIs + check the browser").set_defaults(func=cmd_setup)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
