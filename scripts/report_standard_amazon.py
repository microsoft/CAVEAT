#!/usr/bin/env python3
"""Audit and report a three-repeat standard-Amazon evaluation.

The headline quantity is literal unique-hero identity, not the transaction
``strict_binary`` field.  The reporter fails closed on an incomplete matrix,
duplicate cells, infrastructure errors, missing headline-score fields, or a
run that reaches the step-budget backstop.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


SCENARIOS = ("laptop", "office_chair", "mattress", "backpack", "tent")
VARIANTS = ("mixed", "graded", "graded3", "graded4")
REPEATS = (1, 2, 3)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repeat(experiment_name: str) -> int:
    match = re.search(r"_r([123])$", experiment_name)
    if not match:
        raise RuntimeError(f"cannot recover repeat from {experiment_name!r}")
    return int(match.group(1))


def _heroes(repo: Path) -> dict[str, str]:
    result = {}
    for scenario in SCENARIOS:
        pool = json.loads(
            (repo / "benchmark_data/amazon" / scenario / "pool.json").read_text()
        )
        heroes = [row["asin"] for row in pool if row.get("decoy_kind") == "hero"]
        if len(heroes) != 1:
            raise RuntimeError(f"{scenario}: expected one hero, found {heroes}")
        result[scenario] = heroes[0]
    return result


def build_report(root: Path, repo: Path, max_steps: int) -> dict:
    heroes = _heroes(repo)
    expected = {
        (repeat, scenario, variant)
        for repeat in REPEATS
        for scenario in SCENARIOS
        for variant in VARIANTS
    }
    records = []
    seen = set()
    models = set()

    for summary_path in sorted(root.glob("*/*/summary.json")):
        summary = json.loads(summary_path.read_text())
        repeat = _repeat(summary_path.parent.parent.name)
        try:
            scenario, variant = summary["task_id"].rsplit("-", 1)
        except (KeyError, ValueError) as exc:
            raise RuntimeError(f"bad task id in {summary_path}") from exc
        key = (repeat, scenario, variant)
        if key in seen:
            raise RuntimeError(f"duplicate cell {key}")
        seen.add(key)

        if summary.get("env") != "amazon" or summary.get("condition") != "combined":
            raise RuntimeError(f"protocol mismatch in {summary_path}")
        if summary.get("scaffold") != "browseruse":
            raise RuntimeError(f"scaffold mismatch in {summary_path}")
        if "preservation_strict" not in summary:
            raise RuntimeError(f"missing preservation_strict in {summary_path}")
        steps = summary.get("num_steps")
        if not isinstance(steps, int) or steps <= 0:
            raise RuntimeError(f"zero/invalid-step cell in {summary_path}: {steps!r}")
        if summary.get("error"):
            raise RuntimeError(f"infrastructure error in {summary_path}: {summary['error']}")
        if steps >= max_steps:
            raise RuntimeError(f"step backstop bound in {summary_path}: {steps}/{max_steps}")

        models.add(summary.get("model"))
        hero_asin = heroes[scenario]
        literal_hero = int(summary.get("chosen") == hero_asin)
        records.append(
            {
                "repeat": repeat,
                "scenario": scenario,
                "variant": variant,
                "task_id": summary["task_id"],
                "outcome": summary.get("outcome"),
                "chosen": summary.get("chosen"),
                "hero_asin": hero_asin,
                "literal_hero": literal_hero,
                "strict_binary": summary.get("strict_binary"),
                "preservation_strict": summary.get("preservation_strict"),
                "num_steps": steps,
                "seconds": summary.get("seconds"),
                "summary": str(summary_path.relative_to(repo)),
                "summary_sha256": _sha256(summary_path),
            }
        )

    missing = sorted(expected - seen)
    extra = sorted(seen - expected)
    if missing or extra or len(records) != 60:
        raise RuntimeError(
            f"matrix mismatch: records={len(records)} missing={missing} extra={extra}"
        )
    if len(models) != 1 or None in models:
        raise RuntimeError(f"expected exactly one model, found {sorted(models)}")

    by_variant = defaultdict(lambda: {"heroes": 0, "runs": 0})
    for row in records:
        bucket = by_variant[row["variant"]]
        bucket["heroes"] += row["literal_hero"]
        bucket["runs"] += 1
    for variant in VARIANTS:
        if by_variant[variant]["runs"] != 15:
            raise RuntimeError(f"{variant}: expected 15 runs, got {by_variant[variant]}")
        by_variant[variant]["hero_rate"] = (
            by_variant[variant]["heroes"] / by_variant[variant]["runs"]
        )

    hero_total = sum(row["literal_hero"] for row in records)
    records.sort(key=lambda row: (row["repeat"], SCENARIOS.index(row["scenario"]),
                                  VARIANTS.index(row["variant"])))
    return {
        "schema": "agentarena.standard-amazon-literal-hero-report.v1",
        "root": str(root.relative_to(repo)),
        "model": models.pop(),
        "protocol": {
            "environment": "amazon",
            "scenarios": list(SCENARIOS),
            "condition": "combined",
            "variants": list(VARIANTS),
            "fully_absolute_excluded": True,
            "repeats": 3,
            "scaffold": "browseruse",
            "max_steps_backstop": max_steps,
        },
        "headline_metric": {
            "name": "literal_hero",
            "definition": "1 iff chosen ASIN is the scenario's unique hero",
            "heroes": hero_total,
            "runs": len(records),
            "hero_rate": hero_total / len(records),
        },
        "by_variant": {variant: by_variant[variant] for variant in VARIANTS},
        "audit": {
            "matrix_complete": True,
            "infrastructure_errors": 0,
            "backstop_bound_runs": 0,
            "max_num_steps": max(row["num_steps"] for row in records),
            "preservation_strict_key_present": len(records),
            "outcomes": dict(Counter(row["outcome"] for row in records)),
        },
        "runs": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--max-steps", type=int, default=250)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parent.parent
    root = args.root.resolve()
    output = args.output.resolve() if args.output else root / "report.json"
    report = build_report(root, repo, args.max_steps)
    output.write_text(json.dumps(report, indent=2) + "\n")
    metric = report["headline_metric"]
    print(output)
    print(f"literal hero: {metric['heroes']}/{metric['runs']} = {metric['hero_rate']:.1%}")


if __name__ == "__main__":
    main()
