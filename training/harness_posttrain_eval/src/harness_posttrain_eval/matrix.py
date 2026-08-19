from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .common import (
    IntegrityError,
    canonical_bytes,
    sha256_bytes,
    stable_int,
    write_json_create_only,
)


def _row(
    *,
    config: dict[str, Any],
    phase: str,
    arm: str,
    scenario: str,
    variant: str,
    condition: str,
    repetition: int,
    held_out: bool,
) -> dict[str, Any]:
    pair_id = f"{phase}::{scenario}::{variant}::{condition}::r{repetition:02d}"
    return {
        "run_id": f"{pair_id}::{arm}",
        "pair_id": pair_id,
        "phase": phase,
        "arm": arm,
        "environment": config["environment"],
        "scaffold": config["scaffold"],
        "scenario": scenario,
        "task_id": f"{scenario}-{variant}",
        "variant": variant,
        "condition": condition,
        "repetition": repetition,
        "held_out": held_out,
        # This seed fixes matched-block identity and launch randomization only.
        # The unchanged production harness has no provider-sampling seed input.
        "block_seed": stable_int(config["seed"], pair_id),
        "launch_order_key": stable_int(config["seed"], "launch", pair_id, arm),
        "results_partition": f"{phase}_r{repetition + 1:02d}",
    }


def diagnostic_matrix(config: dict[str, Any]) -> dict[str, Any]:
    variants = config["amazon"]["variants"]
    scenario = config["amazon"]["development_scenarios"][0]
    rows = []
    counts = config["matrices"]
    for condition, repetitions in (
        ("combined", int(counts["diagnostic_combined_repetitions"])),
        ("clean", int(counts["diagnostic_clean_repetitions"])),
    ):
        for variant in variants:
            for repetition in range(repetitions):
                rows.append(
                    _row(
                        config=config,
                        phase="diagnostic",
                        arm="base_trapi_diagnostic",
                        scenario=scenario,
                        variant=variant,
                        condition=condition,
                        repetition=repetition,
                        held_out=False,
                    )
                )
    return _matrix(config, "diagnostic", rows)


def shadow_selection_matrix(
    config: dict[str, Any], split: dict[str, Any], *, candidate_arms: Iterable[str]
) -> dict[str, Any]:
    arms = ["base", *candidate_arms]
    if len(arms) != len(set(arms)) or len(arms) < 2:
        raise IntegrityError("candidate arms must be unique and nonempty")
    tasks = [
        task
        for task in split["tasks"]
        if task["split"] == "validation" and task["validation_role"] == "selection"
    ]
    rows = []
    for task in tasks:
        for arm in arms:
            rows.append(
                _row(
                    config=config,
                    phase="shadow_selection",
                    arm=arm,
                    scenario=task["family"],
                    variant=task["variant"],
                    condition=task["condition"],
                    repetition=task["repetition"],
                    held_out=True,
                )
            )
    return _matrix(config, "shadow_selection", rows)


def shadow_gate_matrix(
    config: dict[str, Any], split: dict[str, Any], *, selected_arm: str
) -> dict[str, Any]:
    if selected_arm == "base":
        raise IntegrityError("selected shadow arm must differ from base")
    tasks = [
        task
        for task in split["tasks"]
        if task["split"] == "validation" and task["validation_role"] == "gate"
    ]
    rows = []
    for task in tasks:
        for arm in ("base", selected_arm):
            rows.append(
                _row(
                    config=config,
                    phase="shadow_gate",
                    arm=arm,
                    scenario=task["family"],
                    variant=task["variant"],
                    condition=task["condition"],
                    repetition=task["repetition"],
                    held_out=True,
                )
            )
    return _matrix(config, "shadow_gate", rows)


def final_matrix(
    config: dict[str, Any],
    *,
    selected_arm: str = "trained",
    include_sft_parent: bool = False,
    scenario_scope: str = "all",
) -> dict[str, Any]:
    if selected_arm == "base":
        raise IntegrityError("selected final arm must differ from base")
    if scenario_scope == "all":
        scenarios = [
            *config["amazon"]["development_scenarios"],
            *config["amazon"]["held_out_scenarios"],
        ]
    elif scenario_scope == "development":
        scenarios = list(config["amazon"]["development_scenarios"])
    elif scenario_scope == "heldout":
        scenarios = list(config["amazon"]["held_out_scenarios"])
    else:
        raise IntegrityError(f"unknown final scenario scope: {scenario_scope}")
    held_out = set(config["amazon"]["held_out_scenarios"])
    rows = []
    counts = config["matrices"]
    for condition, repetitions in (
        ("combined", int(counts["final_combined_repetitions"])),
        ("clean", int(counts["final_clean_repetitions"])),
    ):
        for scenario in scenarios:
            for variant in config["amazon"]["variants"]:
                for repetition in range(repetitions):
                    for arm in ("base", selected_arm):
                        rows.append(
                            _row(
                                config=config,
                                phase="final",
                                arm=arm,
                                scenario=scenario,
                                variant=variant,
                                condition=condition,
                                repetition=repetition,
                                held_out=scenario in held_out,
                            )
                        )
                    if (
                        include_sft_parent
                        and condition == "combined"
                        and repetition < int(counts["post_sft_ablation_repetitions"])
                    ):
                        rows.append(
                            _row(
                                config=config,
                                phase="final",
                                arm="sft_parent",
                                scenario=scenario,
                                variant=variant,
                                condition=condition,
                                repetition=repetition,
                                held_out=scenario in held_out,
                            )
                        )
    return _matrix(config, "final", rows)


def _matrix(config: dict[str, Any], kind: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows.sort(key=lambda row: (row["launch_order_key"], row["run_id"]))
    core = {
        "schema_version": 1,
        "campaign_id": config["campaign_id"],
        "campaign_contract_sha256": sha256_bytes(canonical_bytes(config)),
        "kind": kind,
        "runs": rows,
    }
    matrix = {**core, "matrix_sha256": sha256_bytes(canonical_bytes(core))}
    audit_matrix(matrix)
    return matrix


def audit_matrix(matrix: dict[str, Any]) -> dict[str, Any]:
    rows = matrix.get("runs")
    if not isinstance(rows, list) or not rows:
        raise IntegrityError("matrix has no runs")
    core = {key: value for key, value in matrix.items() if key != "matrix_sha256"}
    if matrix.get("matrix_sha256") != sha256_bytes(canonical_bytes(core)):
        raise IntegrityError("matrix_sha256 mismatch")
    run_ids = [row["run_id"] for row in rows]
    if len(run_ids) != len(set(run_ids)):
        raise IntegrityError("matrix contains duplicate run_id")
    pairs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        pairs[row["pair_id"]].append(row)
    if matrix["kind"] in {"shadow_gate", "final"}:
        for pair_id, pair_rows in pairs.items():
            arm_counts = Counter(row["arm"] for row in pair_rows)
            selected = set(arm_counts) - {"base", "sft_parent"}
            if arm_counts["base"] != 1:
                raise IntegrityError(f"matched pair must contain one base arm: {pair_id}")
            if len(selected) != 1 or arm_counts[next(iter(selected), "")] != 1:
                raise IntegrityError(f"matched pair must contain one selected arm: {pair_id}")
            if arm_counts["sft_parent"] > 1:
                raise IntegrityError(f"matched pair repeats sft_parent arm: {pair_id}")
    return {
        "valid": True,
        "run_count": len(rows),
        "arms": dict(Counter(row["arm"] for row in rows)),
        "conditions": dict(Counter(row["condition"] for row in rows)),
        "pairs": len(pairs),
    }


def write_matrix(path: Path, matrix: dict[str, Any]) -> None:
    write_json_create_only(path, matrix)
