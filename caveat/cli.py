"""Run, validate, and score the CAVEAT benchmark."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path


def _load_config(path: str) -> dict:
    source = Path(path)
    text = source.read_text()
    if source.suffix.lower() in {".yaml", ".yml"}:
        import yaml

        value = yaml.safe_load(text)
    else:
        value = json.loads(text)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SystemExit(f"{path}: run config must be a mapping")
    return value


def _cli_models(args, config: dict) -> list:
    configured = config.get("models")
    if configured is None and config.get("model") is not None:
        configured = [config["model"]]
    models = list(
        args.model
        or configured
        or ([os.environ["OPENAI_MODEL"]] if os.environ.get("OPENAI_MODEL") else [])
    )
    if not models:
        raise SystemExit(
            "no model configured; set models in the config, pass --model, or set OPENAI_MODEL"
        )
    endpoint_overrides = any(
        value is not None
        for value in (args.base_url, args.api_key, args.deployment, args.vision)
    )
    if endpoint_overrides:
        if len(models) != 1 or not isinstance(models[0], str):
            raise SystemExit(
                "endpoint flags require exactly one --model; use config mappings for multiple models"
            )
        models[0] = {
            "name": models[0],
            "base_url": args.base_url,
            "api_key": args.api_key,
            "deployment": args.deployment,
            "vision": args.vision,
        }
    return models


def _max_steps(value, scaffolds: list[str]) -> dict[str, int]:
    if value is None:
        return {}
    if isinstance(value, int):
        return {scaffold: value for scaffold in scaffolds}
    if isinstance(value, dict):
        return {str(key): int(item) for key, item in value.items()}
    raise SystemExit("max_steps must be an integer or a scaffold-to-integer mapping")


def _build_experiments(config: dict, args) -> tuple[Path, list]:
    import caveat.envs
    import caveat.scaffolds  # noqa: F401  (register built-ins)
    from caveat.benchmark.tiers import CAVEAT_STANDARD, DEFAULT_REPEATS, tier_groups
    from caveat.core.experiment import Experiment
    from caveat.core.models import ModelSpec
    from caveat.core.scaffold import SCAFFOLDS

    tier = args.tier or config.get("tier") or CAVEAT_STANDARD
    environments = args.environment or config.get("environments")
    scaffolds = list(args.scaffold or config.get("scaffolds") or ["caveat-harness"])
    plugins = list(args.plugin or config.get("plugins") or [])
    for module in plugins:
        try:
            importlib.import_module(module)
        except Exception as exc:
            raise SystemExit(
                f"could not import harness plugin {module!r}: {exc}"
            ) from exc
    unknown_scaffolds = sorted(set(scaffolds) - set(SCAFFOLDS.names()))
    if unknown_scaffolds:
        raise SystemExit(
            f"unknown harnesses: {', '.join(unknown_scaffolds)}; "
            f"registered: {', '.join(SCAFFOLDS.names())}"
        )

    models = _cli_models(args, config)
    try:
        model_specs = [ModelSpec.parse(model) for model in models]
        groups = tier_groups(tier, environments)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not args.dry_run:
        for model in model_specs:
            try:
                model.openai_endpoint()
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc

    repeats = (
        args.repeats
        if args.repeats is not None
        else int(config.get("repeats", DEFAULT_REPEATS[tier]))
    )
    if repeats < 1:
        raise SystemExit("repeats must be at least 1")
    name = args.name or config.get("name") or tier.lower()
    result_root = Path(args.results) / name
    max_steps = _max_steps(
        args.max_steps if args.max_steps is not None else config.get("max_steps"),
        scaffolds,
    )
    base_port = int(
        args.base_port if args.base_port is not None else config.get("base_port", 9100)
    )

    experiments = []
    for repeat in range(1, repeats + 1):
        for group_index, group in enumerate(groups):
            experiments.append(
                Experiment(
                    name=f"{tier}__{group.name}__r{repeat}",
                    tier=tier,
                    scaffolds=scaffolds,
                    models=models,
                    tasks=list(group.tasks),
                    conditions=list(group.conditions),
                    max_steps=max_steps,
                    base_port=base_port + group_index * 1000,
                    plugins=plugins,
                )
            )
    return result_root, experiments


def cmd_run(args) -> int:
    from caveat.core.experiment import Runner, auto_jobs

    config = _load_config(args.config) if args.config else {}
    result_root, experiments = _build_experiments(config, args)
    cells = sum(len(experiment.cells()) for experiment in experiments)
    print(f"tier: {experiments[0].tier}")
    print(f"harnesses: {', '.join(experiments[0].scaffolds)}")
    print(f"models: {', '.join(model.name for model in experiments[0].model_specs())}")
    print(f"runs: {cells} -> {result_root}")
    if args.dry_run:
        for experiment in experiments:
            print(f"  {experiment.name}: {len(experiment.cells())}")
        return 0

    jobs = (
        args.jobs if args.jobs is not None else int(config.get("jobs") or auto_jobs())
    )
    runner = Runner(results_dir=result_root, headless=not args.no_headless)
    for experiment in experiments:
        runner.run(experiment, jobs=jobs, force=args.force)
    print(f"Score this run with: caveat score {result_root}")
    return 0


def cmd_list(_args) -> int:
    import caveat.scaffolds  # noqa: F401
    from caveat.benchmark.tiers import (
        ALL_ENVIRONMENTS,
        CAVEAT_HARD,
        CAVEAT_STANDARD,
        DEFAULT_REPEATS,
        expected_runs,
    )
    from caveat.core.scaffold import SCAFFOLDS

    print("tiers:")
    for tier in (CAVEAT_STANDARD, CAVEAT_HARD):
        print(
            f"  {tier}: {expected_runs(tier)} runs/model/harness ({DEFAULT_REPEATS[tier]} repeats)"
        )
    print("environments:", ", ".join(ALL_ENVIRONMENTS))
    print("harnesses:", ", ".join(SCAFFOLDS.names()))
    return 0


def cmd_score(args) -> int:
    from caveat.scoring.report import main

    forwarded = [args.results]
    if args.json:
        forwarded.append("--json")
    if args.output:
        forwarded.extend(["--output", args.output])
    return main(forwarded)


def cmd_validate(args) -> int:
    from caveat.benchmark.tiers import TIER_NAMES
    from caveat.benchmark.validate import print_validation

    return 0 if print_validation(args.tier or TIER_NAMES) else 1


def cmd_setup(_args) -> int:
    from caveat.scaffolds._browser import find_chromium

    browser = find_chromium()
    if browser:
        print(f"Chromium: {browser}")
        print("CAVEAT is ready.")
        return 0
    print(
        "Chromium was not found. Install it with: python -m playwright install chromium"
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="caveat", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run CAVEAT-Standard or CAVEAT-Hard")
    run.add_argument("config", nargs="?", help="YAML or JSON run config")
    run.add_argument("--tier", choices=("CAVEAT-Standard", "CAVEAT-Hard"))
    run.add_argument("--name", help="result-set name")
    run.add_argument(
        "--environment", action="append", help="run a tier subset (repeatable)"
    )
    run.add_argument("--scaffold", action="append", help="harness name (repeatable)")
    run.add_argument(
        "--plugin", action="append", help="Python module registering a custom harness"
    )
    run.add_argument(
        "--model", action="append", help="model display/wire name (repeatable)"
    )
    run.add_argument("--base-url", help="OpenAI-compatible API base URL")
    run.add_argument("--api-key", help="API key or env:VARIABLE")
    run.add_argument("--deployment", help="model identifier sent to the endpoint")
    run.add_argument("--vision", action=argparse.BooleanOptionalAction, default=None)
    run.add_argument(
        "--repeats", type=int, help="override the tier's publication repetitions"
    )
    run.add_argument("--jobs", type=int, help="parallel browser workers")
    run.add_argument("--max-steps", type=int, help="per-run safety backstop")
    run.add_argument("--base-port", type=int)
    run.add_argument("--results", default="results", help="parent result directory")
    run.add_argument("--no-headless", action="store_true")
    run.add_argument("--force", action="store_true", help="rerun completed cells")
    run.add_argument(
        "--dry-run", action="store_true", help="show the resolved matrix and exit"
    )
    run.set_defaults(func=cmd_run)

    score = subparsers.add_parser("score", help="compute optimal-selection rate")
    score.add_argument("results", help="run or result directory")
    score.add_argument("--json", action="store_true")
    score.add_argument("--output", help="write a JSON report")
    score.set_defaults(func=cmd_score)

    validate = subparsers.add_parser(
        "validate", help="validate committed benchmark oracles"
    )
    validate.add_argument(
        "--tier", choices=("CAVEAT-Standard", "CAVEAT-Hard"), action="append"
    )
    validate.set_defaults(func=cmd_validate)

    subparsers.add_parser(
        "list", aliases=["ls"], help="list tiers, environments, and harnesses"
    ).set_defaults(func=cmd_list)
    subparsers.add_parser("setup", help="check the browser installation").set_defaults(
        func=cmd_setup
    )

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
