"""Predeclared checkpoint-selection gate for paired procedural shadow tasks."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import ArtifactError, read_json, sha256_file
from .config import Campaign

GATE_ARM_SCHEMA = "harness-posttrain.selection-arm.v1"
GATE_REPORT_SCHEMA = "harness-posttrain.selection-gate.v1"


def _arm(path: str | Path, campaign: Campaign, label: str) -> dict[str, dict[str, Any]]:
    value = read_json(path)
    if (
        not isinstance(value, Mapping)
        or value.get("schema") != GATE_ARM_SCHEMA
        or value.get("campaign_digest") != campaign.digest
        or value.get("split") != "selection"
        or value.get("source") != "procedural"
    ):
        raise ArtifactError(f"{label} selection arm is malformed or outside the frozen split")
    rows = value.get("runs")
    settings = campaign.campaign["selection_gate"]
    expected = int(settings["procedural_shadow_tasks"]) * int(settings["episodes_per_task"])
    if not isinstance(rows, list) or len(rows) != expected:
        raise ArtifactError(f"{label} selection arm must contain exactly {expected} runs")
    result: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ArtifactError(f"{label} run {index} is not a mapping")
        run_id = row.get("run_id")
        if not isinstance(run_id, str) or not run_id or run_id in result:
            raise ArtifactError(f"{label} run IDs are invalid or duplicated")
        if not isinstance(row.get("task_id"), str) or not row["task_id"]:
            raise ArtifactError(f"{label} run {run_id}.task_id must be nonempty")
        for key in (
            "contract_valid",
            "tool_valid",
            "optimal",
            "infrastructure_complete",
            "binding_backstop",
        ):
            if not isinstance(row.get(key), bool):
                raise ArtifactError(f"{label} run {run_id}.{key} must be Boolean")
        result[run_id] = dict(row)
    task_counts = Counter(row["task_id"] for row in result.values())
    if len(task_counts) != int(settings["procedural_shadow_tasks"]) or set(
        task_counts.values()
    ) != {int(settings["episodes_per_task"])}:
        raise ArtifactError(f"{label} selection arm has the wrong task/episode matrix")
    return result


def evaluate_selection_gate(
    campaign: Campaign,
    *,
    parent_path: str | Path,
    candidate_path: str | Path,
) -> dict[str, Any]:
    parent = _arm(parent_path, campaign, "parent")
    candidate = _arm(candidate_path, campaign, "candidate")
    if parent.keys() != candidate.keys():
        raise ArtifactError("parent and candidate selection run IDs differ")
    if any(
        not row["infrastructure_complete"] or row["binding_backstop"]
        for row in [*parent.values(), *candidate.values()]
    ):
        raise ArtifactError(
            "selection evidence contains infrastructure failure or a binding backstop"
        )
    total = len(parent)
    parent_tool = sum(row["tool_valid"] for row in parent.values()) / total
    candidate_tool = sum(row["tool_valid"] for row in candidate.values()) / total
    candidate_contract = sum(row["contract_valid"] for row in candidate.values()) / total
    wins = sum(
        not parent[run_id]["optimal"] and candidate[run_id]["optimal"] for run_id in parent
    )
    regressions = sum(
        parent[run_id]["optimal"] and not candidate[run_id]["optimal"] for run_id in parent
    )
    settings = campaign.campaign["selection_gate"]
    checks = {
        "contract_validity": candidate_contract
        >= float(settings["minimum_contract_validity"]),
        "tool_validity": candidate_tool
        >= parent_tool - float(settings["maximum_tool_validity_regression"]),
        "net_hero_wins": wins - regressions
        >= int(settings["minimum_net_hero_wins_over_parent"]),
        "hero_regressions": regressions <= int(settings["maximum_hero_regressions"]),
    }
    passed = all(checks.values())
    return {
        "schema": GATE_REPORT_SCHEMA,
        "campaign_digest": campaign.digest,
        "passed": passed,
        "selection": "candidate" if passed else "parent",
        "safe_fallback_applied": not passed,
        "parent_sha256": sha256_file(parent_path),
        "candidate_sha256": sha256_file(candidate_path),
        "runs": total,
        "metrics": {
            "parent_tool_validity": parent_tool,
            "candidate_tool_validity": candidate_tool,
            "candidate_contract_validity": candidate_contract,
            "hero_wins": wins,
            "hero_regressions": regressions,
            "net_hero_wins": wins - regressions,
        },
        "checks": checks,
    }
