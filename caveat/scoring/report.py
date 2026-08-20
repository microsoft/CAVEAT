"""Aggregate CAVEAT results using only optimal-selection rate.

Usage::

    python -m caveat.scoring.report results/my_experiment
    python -m caveat.scoring.report results/byenv --json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .optimal_selection import cell_optimal_selection, optimal_selection_rate


def _indicator(summary: dict[str, Any], summary_path: Path) -> float | None:
    value = summary.get("optimal_selection")
    if value in {0}:
        return 0.0
    if value in {1}:
        return 1.0
    outcome = summary.get("outcome")
    if outcome in {"error", "skipped"}:
        return None
    if outcome in {"none", "other", "violation", "decoy"}:
        return 0.0
    trajectory_path = summary_path.with_name("trajectory.json")
    if summary.get("env") == "caveat_shop" and trajectory_path.exists():
        fresh = cell_optimal_selection(trajectory_path)
        return 0.0 if fresh is None and outcome == "none" else fresh
    return None


def collect(results_dir: str | Path) -> list[dict[str, Any]]:
    rows = []
    for summary_path in sorted(Path(results_dir).rglob("summary.json")):
        try:
            summary = json.loads(summary_path.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        indicator = _indicator(summary, summary_path)
        rows.append(
            {
                "path": str(summary_path),
                "env": summary.get("env", "?"),
                "scaffold": summary.get("scaffold", "?"),
                "model": summary.get("model", "?"),
                "task_id": summary.get("task_id", "?"),
                "condition": summary.get("condition", "?"),
                "outcome": summary.get("outcome", "?"),
                "optimal_selection": indicator,
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple(row[field] for field in ("env", "scaffold", "model", "condition"))
        groups[key].append(row)
    result = []
    for key, members in sorted(groups.items()):
        evaluated = [
            row["optimal_selection"]
            for row in members
            if row["optimal_selection"] is not None
        ]
        result.append(
            {
                "env": key[0],
                "scaffold": key[1],
                "model": key[2],
                "condition": key[3],
                "optimal_selection_rate": optimal_selection_rate(evaluated),
                "optimal_selections": int(sum(evaluated)),
                "evaluated_runs": len(evaluated),
                "invalid_runs": len(members) - len(evaluated),
                "total_runs": len(members),
            }
        )
    return result


def build_report(results_dir: str | Path) -> dict[str, Any]:
    rows = collect(results_dir)
    evaluated = [
        row["optimal_selection"] for row in rows if row["optimal_selection"] is not None
    ]
    return {
        "metric": "optimal-selection rate",
        "optimal_selection_rate": optimal_selection_rate(evaluated),
        "optimal_selections": int(sum(evaluated)),
        "evaluated_runs": len(evaluated),
        "invalid_runs": len(rows) - len(evaluated),
        "total_runs": len(rows),
        "groups": summarize(rows),
        "runs": rows,
    }


def print_report(report: dict[str, Any]) -> None:
    print(
        "optimal-selection rate: "
        f"{report['optimal_selection_rate']:.3f} "
        f"({report['optimal_selections']}/{report['evaluated_runs']}; "
        f"{report['invalid_runs']} invalid)"
    )
    print("env scaffold model condition rate selected/evaluated invalid")
    for row in report["groups"]:
        print(
            f"{row['env']} {row['scaffold']} {row['model']} {row['condition']} "
            f"{row['optimal_selection_rate']:.3f} "
            f"{row['optimal_selections']}/{row['evaluated_runs']} {row['invalid_runs']}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report CAVEAT optimal-selection rate")
    parser.add_argument("results")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", help="write the JSON report to this path")
    args = parser.parse_args(argv)
    report = build_report(args.results)
    if not report["runs"]:
        print(f"no summaries found under {args.results}")
        return 1
    payload = json.dumps(report, indent=2)
    if args.output:
        Path(args.output).write_text(payload + "\n")
    if args.json:
        print(payload)
        return 0
    print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
