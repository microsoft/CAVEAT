"""Step35 trajectory-level BrowserUse action distillation contract.

This module owns the create-once inert stage and the fail-closed validation of
the TRAIN-only trajectory materialization.  Token rendering and PRIME batch
construction live in :mod:`step35_trajectory_training`; no rationale, memory,
or next-goal token is admitted by this adapter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import step34_rollback_adapter as step34
from .precommit_any_dagger_ops import _canonical
from .step35_proximal_patch import (
    OUTPUT_SHA256,
    PROFILES,
    SOURCE_STEP,
)

STAGE_SCHEMA = "harness-distill.step35-trajectory-action-stage-plan.v1"
MANIFEST_SCHEMA = "harness-distill.hero50-sequential-dagger-materialization.v1"
ROW_SCHEMA = "harness-distill.hero50-sequential-dagger-training-row.v1"
TRAJECTORY_SCHEMA = "harness-distill.hero50-sequential-dagger-outcome.v1"
ADAPTER_SCHEMA = "harness-distill.step35-trajectory-action-proximal-adapter.v1"
MASK_POLICY = "browseruse_structured_action_only"
PROFILE_STEPS = {
    "A": tuple(range(SOURCE_STEP + 1, int(PROFILES["A"]["final_step"]) + 1)),
    "B": tuple(range(SOURCE_STEP + 1, int(PROFILES["B"]["final_step"]) + 1)),
}
UPDATE_STEPS = PROFILE_STEPS["A"]
TARGET_ASIN = "EXP-LAPTOP-50"

# This is an on-policy distillation mix, not a row-count quota.  Every fresh
# trajectory receives equal mass; each subgoal within it receives equal mass.
# Parent replay is normalized by the same hierarchy and retains the full
# Step32 Add/Cart/Delete/Proceed/Place chain plus untouched upstream actions.
FRESH_TRAJECTORY_MASS = Fraction(3, 5)
STEP32_ACTION_REPLAY_MASS = Fraction(2, 5)
SEMANTIC_PAIRED_SPLIT = {
    "chosen_action_ce": Fraction(4, 5),
    "rejected_semantic_unlikelihood": Fraction(1, 5),
}
CHOSEN_ONLY_SPLIT = {"chosen_action_ce": Fraction(1, 1)}

PARENT_RECEIPT = dict(step34.PARENT_RECEIPT)
PARENT_ADAPTER_FILE_SHA256 = step34.PARENT_ADAPTER_FILE_SHA256
PARENT_ADAPTER_BODY_SHA256 = step34.PARENT_ADAPTER_BODY_SHA256

CANARY_SELECTOR_KEYS = (
    "sqlite_sole_hero_completion",
    "joint_hero_open_and_chosen",
    "full_ordered_chain_completion",
    "lowest_parent_drift",
)
CANDIDATE_INVENTORY = {
    "strong_proximal": {
        "learning_rate": "2e-7",
        "microsteps": 3,
        "candidate_steps": [33, 34, 35],
        "proximal_lambda": "1e-2",
        "max_delta_rms": "2e-6",
    },
    "medium_proximal": {
        "learning_rate": "5e-7",
        "microsteps": 2,
        "candidate_steps": [33, 34],
        "proximal_lambda": "5e-3",
        "max_delta_rms": "4e-6",
    },
}


class Step35TrajectoryAdapterError(RuntimeError):
    """A Step35 data, lineage, normalization, or selection invariant failed."""


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _body_sha(value: Mapping[str, Any], field: str) -> str:
    return hashlib.sha256(
        _canonical({key: item for key, item in value.items() if key != field})
    ).hexdigest()


def _is_hex64(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _write_new(path: Path, value: Mapping[str, Any] | bytes) -> None:
    payload = value if isinstance(value, bytes) else _canonical(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _descriptor_file(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    logical_name: str,
) -> tuple[Path, list[dict[str, Any]]]:
    descriptor = manifest.get(logical_name)
    if not isinstance(descriptor, Mapping):
        raise Step35TrajectoryAdapterError(f"manifest lacks {logical_name} descriptor")
    relative = descriptor.get("path", descriptor.get("relative_path"))
    if not isinstance(relative, str) or Path(relative).name != relative:
        raise Step35TrajectoryAdapterError(f"unsafe {logical_name} relative path")
    path = manifest_path.parent / relative
    if (
        not path.is_file()
        or path.is_symlink()
        or descriptor.get("sha256") != _sha(path)
        or descriptor.get("bytes") != path.stat().st_size
    ):
        raise Step35TrajectoryAdapterError(f"{logical_name} bytes drifted")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            raise Step35TrajectoryAdapterError(
                f"{logical_name} contains blank line {line_number}"
            )
        row = json.loads(line)
        if not isinstance(row, dict):
            raise Step35TrajectoryAdapterError(
                f"{logical_name} line {line_number} is not an object"
            )
        rows.append(row)
    if descriptor.get("rows") != len(rows):
        raise Step35TrajectoryAdapterError(f"{logical_name} row count drifted")
    return path, rows


def _same_action_schema(chosen: Mapping[str, Any], rejected: Mapping[str, Any]) -> bool:
    def shape(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: shape(item) for key, item in sorted(value.items())}
        if isinstance(value, list):
            return [shape(item) for item in value]
        return type(value).__name__

    return shape(chosen) == shape(rejected)


def _stable_relative_pair(
    chosen: Mapping[str, Any],
    rejected: Mapping[str, Any],
    semantic: Mapping[str, Any],
) -> bool:
    chosen_navigate = chosen.get("navigate")
    rejected_navigate = rejected.get("navigate")
    if not isinstance(chosen_navigate, Mapping) or not isinstance(
        rejected_navigate, Mapping
    ):
        return False
    chosen_url = urlparse(str(chosen_navigate.get("url", "")))
    rejected_url = urlparse(str(rejected_navigate.get("url", "")))
    chosen_relative = chosen_url.path + (
        f"?{chosen_url.query}" if chosen_url.query else ""
    )
    rejected_relative = rejected_url.path + (
        f"?{rejected_url.query}" if rejected_url.query else ""
    )
    return bool(
        semantic.get("kind") == "navigate_same_origin"
        and chosen_relative == semantic.get("relative_target")
        and rejected_relative != chosen_relative
        and chosen_url.scheme == rejected_url.scheme
        and chosen_url.netloc == rejected_url.netloc
        and chosen_navigate.get("new_tab") is False
        and rejected_navigate.get("new_tab") is False
    )


def _row_valid(row: Mapping[str, Any]) -> bool:
    chosen = row.get("chosen")
    chosen_action = row.get("chosen_action")
    rejected = row.get("rejected")
    semantic = row.get("semantic_action")
    successor = row.get("successor")
    objective = row.get("objective_kind")
    request = row.get("effective_request")
    row_without_materialized = {
        key: value for key, value in row.items() if key != "materialized_row_sha256"
    }
    row_core = {
        key: value
        for key, value in row_without_materialized.items()
        if key not in {"row_id", "state_id"}
    }
    base = bool(
        row.get("schema") == ROW_SCHEMA
        and row.get("source_split") == "train"
        and row.get("trajectory_role") in {"optimizer", "canary"}
        and isinstance(row.get("trajectory_id"), str)
        and isinstance(row.get("transition_id"), str)
        and isinstance(row.get("run_id"), str)
        and row.get("variant") in {"graded", "graded3", "graded4", "mixed"}
        and isinstance(row.get("subgoal_id"), str)
        and type(row.get("subgoal_ordinal")) is int
        and row["subgoal_ordinal"] >= 0
        and type(row.get("subgoal_attempt")) is int
        and row["subgoal_attempt"] >= 0
        and isinstance(row.get("source_state_id"), str)
        and isinstance(request, Mapping)
        and row.get("effective_request_sha256")
        == hashlib.sha256(_canonical(request)).hexdigest()
        and row.get("messages_before_action") == request.get("messages")
        and row.get("tools") == request.get("tools", [])
        and row.get("tool_choice") == request.get("tool_choice")
        and row.get("parallel_tool_calls") == request.get("parallel_tool_calls")
        and row.get("response_format") == request.get("response_format")
        and isinstance(chosen, Mapping)
        and row.get("chosen_sha256") == hashlib.sha256(_canonical(chosen)).hexdigest()
        and isinstance(chosen_action, Mapping)
        and row.get("chosen_action_sha256")
        == hashlib.sha256(_canonical(chosen_action)).hexdigest()
        and isinstance(semantic, Mapping)
        and row.get("mask_policy") == MASK_POLICY
        and objective in {"paired", "chosen_only"}
        and isinstance(successor, Mapping)
        and successor.get("successor_validated") is True
        and isinstance(successor.get("db_proof"), Mapping)
        and row.get("same_exact_candidate_request") is True
        and row.get("reasoning_training_enabled") is False
        and row.get("thinking_training_enabled") is False
        and row.get("memory_training_enabled") is False
        and row.get("next_goal_training_enabled") is False
        and row.get("evaluation_previous_goal_training_enabled") is False
        and _is_hex64(row.get("row_id"))
        and row.get("state_id") == row.get("row_id")
        and hashlib.sha256(_canonical(row_core)).hexdigest() == row.get("row_id")
        and _is_hex64(row.get("materialized_row_sha256"))
        and hashlib.sha256(_canonical(row_without_materialized)).hexdigest()
        == row.get("materialized_row_sha256")
    )
    if not base:
        return False
    if objective == "chosen_only":
        return rejected is None and row.get("rejected_sha256") is None
    rejected_action = row.get("rejected_action")
    return bool(
        isinstance(rejected, Mapping)
        and row.get("rejected_sha256") == hashlib.sha256(_canonical(rejected)).hexdigest()
        and isinstance(rejected_action, Mapping)
        and row.get("rejected_action_sha256")
        in {None, hashlib.sha256(_canonical(rejected_action)).hexdigest()}
        and row.get("rejected_semantic_policy")
        == "same_method_stable_relative_target_mismatch"
        and set(chosen_action) == {"navigate"}
        and _same_action_schema(chosen_action, rejected_action)
        and _stable_relative_pair(chosen_action, rejected_action, semantic)
    )


def _trajectory_valid(
    receipt: Mapping[str, Any],
    *,
    expected_ids: Sequence[str],
) -> bool:
    new_items = receipt.get("new_order_items")
    receipt_body = {
        key: value
        for key, value in receipt.items()
        if key not in {"receipt_body_sha256", "receipt_file_sha256"}
    }
    return bool(
        receipt.get("schema") == TRAJECTORY_SCHEMA
        and receipt.get("status") == "admitted"
        and receipt.get("source_split") == "train"
        and receipt.get("trajectory_role") in {"optimizer", "canary"}
        and isinstance(receipt.get("trajectory_id"), str)
        and receipt.get("ordered_transition_ids") == list(expected_ids)
        and receipt.get("full_ordered_action_chain") is True
        and receipt.get("hero_open_and_chosen") is True
        and receipt.get("sqlite_exact_sole_hero_completion") is True
        and isinstance(receipt.get("first_divergence"), (Mapping, type(None)))
        and _is_hex64(receipt.get("trace_sha256"))
        and _is_hex64(receipt.get("database_file_sha256"))
        and isinstance(receipt.get("new_order_ids"), list)
        and len(receipt["new_order_ids"]) == 1
        and isinstance(new_items, list)
        and len(new_items) == 1
        and new_items[0].get("asin") == TARGET_ASIN
        and new_items[0].get("quantity") == 1
        and _is_hex64(receipt.get("receipt_body_sha256"))
        and _is_hex64(receipt.get("receipt_file_sha256"))
        and hashlib.sha256(_canonical(receipt_body)).hexdigest()
        == receipt.get("receipt_body_sha256")
    )


def _canary_valid(
    contract: Mapping[str, Any],
    *,
    optimizer_trajectory_ids: set[str],
    canary_trajectory_ids: set[str],
) -> bool:
    probe2 = contract.get("probe2_trajectory_ids")
    if not isinstance(probe2, list):
        return False
    return bool(
        len(probe2) == 2
        and set(probe2).issubset(canary_trajectory_ids)
        and not (canary_trajectory_ids & optimizer_trajectory_ids)
        and contract.get("trajectory_intersection_count") == 0
        and contract.get("row_intersection_count") == 0
        and contract.get("ranking")
        == [
            "sqlite_exact_sole_hero_completion_desc",
            "joint_hero_open_and_chosen_desc",
            "full_ordered_action_chain_desc",
            "parent_drift_asc",
            "trajectory_id_asc",
        ]
        and contract.get("optimizer_trajectory_ids_sha256")
        == hashlib.sha256(_canonical(sorted(optimizer_trajectory_ids))).hexdigest()
        and contract.get("canary_trajectory_ids_sha256")
        == hashlib.sha256(_canonical(sorted(canary_trajectory_ids))).hexdigest()
    )


def trajectory_normalization(
    rows: Sequence[Mapping[str, Any]],
    *,
    total_mass: Fraction,
) -> dict[str, Fraction]:
    """Equal trajectory mass, then equal subgoal mass, then equal state mass."""

    grouped: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[str(row["trajectory_id"])][str(row["subgoal_id"])].append(
            str(row["transition_id"])
        )
    if not grouped:
        raise Step35TrajectoryAdapterError("trajectory-normalized source is empty")
    weights: dict[str, Fraction] = {}
    trajectory_mass = total_mass / len(grouped)
    for subgoals in grouped.values():
        subgoal_mass = trajectory_mass / len(subgoals)
        for transition_ids in subgoals.values():
            state_mass = subgoal_mass / len(transition_ids)
            for transition_id in transition_ids:
                if transition_id in weights:
                    raise Step35TrajectoryAdapterError("transition identity overlaps trajectories")
                weights[transition_id] = state_mass
    if sum(weights.values(), Fraction()) != total_mass:
        raise Step35TrajectoryAdapterError("trajectory normalization mass drifted")
    return weights


def validate_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise Step35TrajectoryAdapterError("trajectory manifest is absent or unsafe")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    body_field = "manifest_sha256"
    source_counts = manifest.get("source_split_counts") or {}
    if (
        manifest.get("schema") != MANIFEST_SCHEMA
        or manifest.get("status") != "complete"
        or source_counts.get("evaluation") != 0
        or source_counts.get("heldout") != 0
        or not _is_hex64(manifest.get(body_field))
        or _body_sha(manifest, body_field) != manifest.get(body_field)
    ):
        raise Step35TrajectoryAdapterError("trajectory manifest header drifted")
    rows_path, rows = _descriptor_file(manifest_path, manifest, "training_rows")
    canary_rows_path, canary_rows = _descriptor_file(
        manifest_path, manifest, "canary_rows"
    )
    receipts_path, receipts = _descriptor_file(
        manifest_path, manifest, "trajectory_receipts"
    )
    all_rows = rows + canary_rows
    if (
        not rows
        or len(canary_rows) < 2
        or source_counts.get("train") != len(all_rows)
        or any(not _row_valid(row) for row in all_rows)
    ):
        raise Step35TrajectoryAdapterError("trajectory row inventory failed validation")
    transition_ids = [str(row["transition_id"]) for row in all_rows]
    if len(set(transition_ids)) != len(transition_ids):
        raise Step35TrajectoryAdapterError("trajectory transition IDs are not unique")
    by_trajectory: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in all_rows:
        by_trajectory[str(row["trajectory_id"])].append(row)
    receipt_by_id = {str(item.get("trajectory_id")): item for item in receipts}
    if set(receipt_by_id) != set(by_trajectory):
        raise Step35TrajectoryAdapterError("trajectory receipt inventory drifted")
    for trajectory_id, members in by_trajectory.items():
        ordered = sorted(
            members,
            key=lambda item: (
                int(item["subgoal_ordinal"]),
                int(item["subgoal_attempt"]),
                str(item["transition_id"]),
            ),
        )
        if not _trajectory_valid(
            receipt_by_id[trajectory_id],
            expected_ids=[str(item["transition_id"]) for item in ordered],
        ):
            raise Step35TrajectoryAdapterError("trajectory outcome receipt drifted")
    optimizer_ids = {str(row["trajectory_id"]) for row in rows}
    canary_ids = {str(row["trajectory_id"]) for row in canary_rows}
    if (
        optimizer_ids != set(manifest.get("optimizer_trajectories") or [])
        or canary_ids != set(manifest.get("canary_trajectories") or [])
        or any(row.get("trajectory_role") != "optimizer" for row in rows)
        or any(row.get("trajectory_role") != "canary" for row in canary_rows)
    ):
        raise Step35TrajectoryAdapterError("optimizer/canary role inventory drifted")
    inventory = manifest.get("candidate_inventory_contract")
    canary = manifest.get("canary_selection_contract")
    probe2 = canary.get("probe2_trajectory_ids") if isinstance(canary, Mapping) else None
    if (
        not isinstance(inventory, Mapping)
        or inventory.get("strong_proximal") != CANDIDATE_INVENTORY["strong_proximal"]
        or inventory.get("medium_proximal") != CANDIDATE_INVENTORY["medium_proximal"]
        or inventory.get("checkpoint_dependent_data_selection") is not False
        or inventory.get("shared_immutable_optimizer_trajectory_ids_sha256")
        != hashlib.sha256(_canonical(sorted(optimizer_ids))).hexdigest()
        or inventory.get("shared_immutable_canary_trajectory_ids_sha256")
        != hashlib.sha256(_canonical(sorted(canary_ids))).hexdigest()
        or not isinstance(probe2, list)
        or inventory.get("shared_immutable_probe2_trajectory_ids_sha256")
        != hashlib.sha256(_canonical(sorted(probe2))).hexdigest()
        or manifest.get("normalization_contract")
        != {
            "first": "equal_mass_per_optimizer_trajectory",
            "second": "equal_mass_per_observed_subgoal_within_trajectory",
            "objective_kind": "normalize_within_trajectory_subgoal",
        }
        or manifest.get("training_contract")
        != {
            "mask_policy": MASK_POLICY,
            "rationale_memory_goal_supervision": False,
            "paired_only_same_method_stable_relative_target_mismatch": True,
            "generic_dom_index_or_method_unlikelihood": False,
            "trajectory_atomic_admission": True,
            "sqlite_exact_sole_hero_order_required": True,
        }
    ):
        raise Step35TrajectoryAdapterError("candidate inventory contract drifted")
    if not isinstance(canary, Mapping) or not _canary_valid(
        canary,
        optimizer_trajectory_ids=optimizer_ids,
        canary_trajectory_ids=canary_ids,
    ):
        raise Step35TrajectoryAdapterError("TRAIN-only canary selection contract drifted")
    masses = trajectory_normalization(rows, total_mass=FRESH_TRAJECTORY_MASS)
    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "manifest_file_sha256": _sha(manifest_path),
        "rows_path": str(rows_path),
        "rows_sha256": _sha(rows_path),
        "canary_rows_path": str(canary_rows_path),
        "canary_rows_sha256": _sha(canary_rows_path),
        "canary_rows": canary_rows,
        "receipts_path": str(receipts_path),
        "receipts_sha256": _sha(receipts_path),
        "rows": rows,
        "receipts": receipts,
        "transition_masses": masses,
    }


def _assistant_actions(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if not isinstance(content, str):
        raise Step35TrajectoryAdapterError("assistant action content is absent")
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.I | re.S)
    if fenced:
        text = fenced.group(1)
    try:
        value, _end = json.JSONDecoder().raw_decode(text.lstrip())
    except json.JSONDecodeError as exc:
        raise Step35TrajectoryAdapterError("assistant action content is not JSON") from exc
    actions = value.get("action") if isinstance(value, Mapping) else None
    if (
        not isinstance(actions, list)
        or not actions
        or any(not isinstance(action, dict) or len(action) != 1 for action in actions)
    ):
        raise Step35TrajectoryAdapterError("assistant has no structured BrowserUse actions")
    return deepcopy(actions)


def _assistant_action(message: Mapping[str, Any]) -> dict[str, Any]:
    actions = _assistant_actions(message)
    if len(actions) != 1:
        raise Step35TrajectoryAdapterError("trajectory transition is not one action")
    return actions[0]


def _relative_target(action: Mapping[str, Any]) -> tuple[str, str] | None:
    navigate = action.get("navigate")
    if not isinstance(navigate, Mapping) or navigate.get("new_tab") is not False:
        return None
    parsed = urlparse(str(navigate.get("url", "")))
    if not parsed.scheme or not parsed.netloc or not parsed.path.startswith("/"):
        return None
    return (
        f"{parsed.scheme}://{parsed.netloc}",
        parsed.path + (f"?{parsed.query}" if parsed.query else ""),
    )


def _parent_action_rows(parent_pairs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in parent_pairs:
        chosen = deepcopy(source["chosen"])
        rejected_source = source.get("rejected")
        chosen_actions = _assistant_actions(chosen)
        rejected_actions = (
            _assistant_actions(rejected_source)
            if isinstance(rejected_source, Mapping)
            else None
        )
        chosen_action = chosen_actions[0] if len(chosen_actions) == 1 else None
        rejected_action = (
            rejected_actions[0]
            if isinstance(rejected_actions, list) and len(rejected_actions) == 1
            else None
        )
        chosen_relative = (
            _relative_target(chosen_action)
            if isinstance(chosen_action, Mapping)
            else None
        )
        rejected_relative = (
            _relative_target(rejected_action)
            if isinstance(rejected_action, Mapping)
            else None
        )
        stable_pair = bool(
            chosen_relative is not None
            and rejected_relative is not None
            and chosen_relative[0] == rejected_relative[0]
            and chosen_relative[1] != rejected_relative[1]
            and _same_action_schema(chosen_action, rejected_action or {})
        )
        source_bucket = str(
            source.get("source_training_bucket")
            or source.get("training_group")
            or source.get("training_bucket")
        )
        identity_body = {
            "source": "sealed_step32_action_replay",
            "source_row_id": source["row_id"],
            "mask_policy": MASK_POLICY,
            "objective_kind": "paired" if stable_pair else "chosen_only",
        }
        row_id = hashlib.sha256(_canonical(identity_body)).hexdigest()
        rows.append(
            {
                "row_id": row_id,
                "state_id": row_id,
                "source_state_id": source.get("state_id"),
                "source_row_id": source.get("row_id"),
                "trajectory_id": "sealed-step32-parent-action-replay",
                "transition_id": row_id,
                "subgoal_id": source_bucket,
                "subgoal_ordinal": 0,
                "subgoal_attempt": 0,
                "run_id": "sealed-step32-parent-action-replay",
                "variant": source["variant"],
                "source_split": "train",
                "source_kind": "sealed_step32_action_only_replay",
                "training_bucket": f"state::{row_id}",
                "messages_before_action": deepcopy(source["messages_before_action"]),
                "tools": deepcopy(source["tools"]),
                "chosen": chosen,
                "chosen_action": chosen_actions,
                "chosen_relative_target": (
                    chosen_relative[1] if chosen_relative is not None else None
                ),
                "objective_kind": "paired" if stable_pair else "chosen_only",
                "rejected": deepcopy(rejected_source) if stable_pair else None,
                "rejected_action": deepcopy(rejected_action) if stable_pair else None,
                "rejected_relative_target": (
                    rejected_relative[1] if stable_pair and rejected_relative else None
                ),
                "mask_policy": MASK_POLICY,
                "reasoning_training_enabled": False,
                "thinking_training_enabled": False,
                "evaluation_previous_goal_training_enabled": False,
                "memory_training_enabled": False,
                "next_goal_training_enabled": False,
                "phase": source_bucket,
                "trigger_kind": source_bucket,
            }
        )
    if len(rows) != 45 or len({row["row_id"] for row in rows}) != 45:
        raise Step35TrajectoryAdapterError("Step32 action replay identity drifted")
    return rows


def _fresh_action_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in rows:
        chosen_action = _assistant_action(source["chosen"])
        rejected = source.get("rejected")
        rejected_action = (
            _assistant_action(rejected) if isinstance(rejected, Mapping) else None
        )
        chosen_relative = _relative_target(chosen_action)
        rejected_relative = (
            _relative_target(rejected_action)
            if isinstance(rejected_action, Mapping)
            else None
        )
        row = deepcopy(source)
        row.update(
            {
                "source_kind": "step32_on_policy_sequential_trajectory",
                "state_id": source["row_id"],
                "training_bucket": f"state::{source['row_id']}",
                "chosen_relative_target": (
                    chosen_relative[1] if chosen_relative is not None else None
                ),
                "rejected_relative_target": (
                    rejected_relative[1] if rejected_relative is not None else None
                ),
                "phase": source["subgoal_id"],
                "trigger_kind": source["subgoal_id"],
            }
        )
        result.append(row)
    return result


def _descriptor(path: Path, rows: int) -> dict[str, Any]:
    return {
        "path": path.name,
        "rows": rows,
        "bytes": path.stat().st_size,
        "sha256": _sha(path),
    }


def _corpus_rows(states: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for state in states:
        members = [("chosen", "chosen")]
        if state["objective_kind"] == "paired":
            members.append(("rejected", "rejected"))
        for kind, message_key in members:
            result.append(
                {
                    "kind": kind,
                    "row_id": f"{state['row_id']}:{kind}",
                    "pair_row_id": state["row_id"],
                    "state_id": state["state_id"],
                    "trajectory_id": state["trajectory_id"],
                    "subgoal_id": state["subgoal_id"],
                    "variant": state["variant"],
                    "phase": state["phase"],
                    "trigger_kind": state["trigger_kind"],
                    "training_bucket": f"state::{state['row_id']}",
                    "objective_kind": state["objective_kind"],
                    "messages_before_action": deepcopy(
                        state["messages_before_action"]
                    ),
                    "assistant_message": deepcopy(state[message_key]),
                    "tools": deepcopy(state["tools"]),
                    "chosen_relative_target": state.get("chosen_relative_target"),
                    "rejected_relative_target": state.get("rejected_relative_target"),
                }
            )
    return result


def materialize_adapter(
    *,
    manifest_path: str | Path,
    parent_adapter_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Project the sealed trajectories and Step32 replay into one shared corpus."""

    output = Path(output_dir).resolve()
    if output.exists() or output.is_symlink():
        raise Step35TrajectoryAdapterError("Step35 adapter output must be fresh")
    dynamic = validate_manifest(manifest_path)
    parent = step34._parent(parent_adapter_path)
    fresh = _fresh_action_rows(dynamic["rows"])
    replay = _parent_action_rows(parent["pairs"])
    fresh_masses = trajectory_normalization(
        fresh, total_mass=FRESH_TRAJECTORY_MASS
    )
    replay_masses = trajectory_normalization(
        replay, total_mass=STEP32_ACTION_REPLAY_MASS
    )
    masses = {**fresh_masses, **replay_masses}
    states = fresh + replay
    if len(masses) != len(states) or sum(masses.values(), Fraction()) != 1:
        raise Step35TrajectoryAdapterError("shared action-distillation mass drifted")
    category_mix: dict[str, Fraction] = {}
    objectives: dict[str, dict[str, Fraction]] = {
        "chosen_tail": {},
        "chosen_action": {},
        "rejected_unlikelihood": {},
    }
    for row in states:
        category = f"state::{row['row_id']}"
        state_mass = masses[str(row["transition_id"])]
        category_mix[category] = state_mass
        if row["objective_kind"] == "paired":
            objectives["chosen_action"][category] = (
                state_mass * SEMANTIC_PAIRED_SPLIT["chosen_action_ce"]
            )
            objectives["rejected_unlikelihood"][category] = (
                state_mass * SEMANTIC_PAIRED_SPLIT[
                    "rejected_semantic_unlikelihood"
                ]
            )
        else:
            objectives["chosen_action"][category] = state_mass
    output.mkdir(parents=True, mode=0o700)
    # These exact filenames are the reviewed paired trainer's create-once
    # frozen-copy interface. The logical descriptors below retain Step35 names.
    fresh_path = output / "fresh_hero50_pairs.jsonl"
    replay_path = output / "shortcut_retention_pairs.jsonl"
    corpus_path = output / "paired_corpus.jsonl"
    _write_new(fresh_path, b"".join(_canonical(row) + b"\n" for row in fresh))
    _write_new(replay_path, b"".join(_canonical(row) + b"\n" for row in replay))
    corpus = _corpus_rows(states)
    _write_new(corpus_path, b"".join(_canonical(row) + b"\n" for row in corpus))
    body: dict[str, Any] = {
        "schema": ADAPTER_SCHEMA,
        "status": "complete",
        "scientific_label": (
            "step32_anchored_trajectory_level_on_policy_action_distillation"
        ),
        "source_step": SOURCE_STEP,
        "profiles": {
            name: {**profile, "candidate_steps": list(PROFILE_STEPS[name])}
            for name, profile in PROFILES.items()
        },
        "source_manifest": {
            "path": dynamic["manifest_path"],
            "sha256": dynamic["manifest_file_sha256"],
            "manifest_sha256": dynamic["manifest"]["manifest_sha256"],
        },
        "source_step32_adapter": {
            "path": parent["adapter_path"],
            "sha256": parent["adapter_file_sha256"],
            "adapter_sha256": parent["adapter"]["adapter_sha256"],
        },
        "state_counts": {
            "states": len(states),
            "fresh": len(fresh),
            "step32_action_replay": len(replay),
            "paired": sum(row["objective_kind"] == "paired" for row in states),
            "chosen_only": sum(
                row["objective_kind"] == "chosen_only" for row in states
            ),
            "samples": len(corpus),
            "evaluation": 0,
            "heldout": 0,
        },
        "source_group_mass": {
            "fresh_trajectory_actions": str(FRESH_TRAJECTORY_MASS),
            "step32_action_replay": str(STEP32_ACTION_REPLAY_MASS),
        },
        "normalization": (
            "source_group_then_equal_trajectory_then_equal_subgoal_then_"
            "equal_state_then_equal_action_token"
        ),
        "category_mix": {
            key: str(value) for key, value in category_mix.items()
        },
        "objective_category_masses": {
            name: {key: str(value) for key, value in categories.items()}
            for name, categories in objectives.items()
        },
        "mask_policy": {
            "chosen": "entire_structured_browseruse_action_json_only",
            "rejected": "stable_same_method_wrong_relative_target_only",
            "excluded": [
                "thinking",
                "reasoning",
                "evaluation_previous_goal",
                "memory",
                "next_goal",
            ],
        },
        "canary_selection_contract": dynamic["manifest"][
            "canary_selection_contract"
        ],
        "candidate_inventory_contract": dynamic["manifest"][
            "candidate_inventory_contract"
        ],
        "materialized_fresh": _descriptor(fresh_path, len(fresh)),
        "materialized_replay": _descriptor(replay_path, len(replay)),
        "corpus": _descriptor(corpus_path, len(corpus)),
    }
    value = {**body, "adapter_sha256": hashlib.sha256(_canonical(body)).hexdigest()}
    _write_new(output / "adapter.json", value)
    return value


def _adapter_rows(
    adapter_path: Path, adapter: Mapping[str, Any], name: str
) -> tuple[Path, list[dict[str, Any]]]:
    descriptor = adapter.get(name)
    if not isinstance(descriptor, Mapping):
        raise Step35TrajectoryAdapterError(f"adapter lacks {name}")
    relative = descriptor.get("path")
    path = adapter_path.parent / str(relative)
    if (
        not isinstance(relative, str)
        or Path(relative).name != relative
        or not path.is_file()
        or path.is_symlink()
        or descriptor.get("sha256") != _sha(path)
        or descriptor.get("bytes") != path.stat().st_size
    ):
        raise Step35TrajectoryAdapterError(f"adapter {name} bytes drifted")
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    if descriptor.get("rows") != len(rows):
        raise Step35TrajectoryAdapterError(f"adapter {name} count drifted")
    return path.resolve(), rows


def validate_trainer_adapter(path: str | Path) -> dict[str, Any]:
    adapter_path = Path(path).resolve()
    if not adapter_path.is_file() or adapter_path.is_symlink():
        raise Step35TrajectoryAdapterError("Step35 trainer adapter is absent or unsafe")
    value = json.loads(adapter_path.read_text())
    body = {key: item for key, item in value.items() if key != "adapter_sha256"}
    profiles = {
        name: {**profile, "candidate_steps": list(PROFILE_STEPS[name])}
        for name, profile in PROFILES.items()
    }
    if (
        value.get("schema") != ADAPTER_SCHEMA
        or value.get("status") != "complete"
        or value.get("adapter_sha256")
        != hashlib.sha256(_canonical(body)).hexdigest()
        or value.get("source_step") != SOURCE_STEP
        or value.get("profiles") != profiles
        or (value.get("source_step32_adapter") or {}).get("sha256")
        != PARENT_ADAPTER_FILE_SHA256
        or (value.get("source_step32_adapter") or {}).get("adapter_sha256")
        != PARENT_ADAPTER_BODY_SHA256
        or value.get("source_group_mass")
        != {
            "fresh_trajectory_actions": str(FRESH_TRAJECTORY_MASS),
            "step32_action_replay": str(STEP32_ACTION_REPLAY_MASS),
        }
    ):
        raise Step35TrajectoryAdapterError("Step35 trainer adapter header drifted")
    fresh_path, fresh = _adapter_rows(adapter_path, value, "materialized_fresh")
    replay_path, replay = _adapter_rows(adapter_path, value, "materialized_replay")
    corpus_path, corpus = _adapter_rows(adapter_path, value, "corpus")
    states = fresh + replay
    counts = value.get("state_counts") or {}
    categories = value.get("category_mix") or {}
    objectives = value.get("objective_category_masses") or {}
    if (
        not fresh
        or len(replay) != 45
        or len({row.get("row_id") for row in states}) != len(states)
        or counts.get("states") != len(states)
        or counts.get("fresh") != len(fresh)
        or counts.get("step32_action_replay") != len(replay)
        or counts.get("samples") != len(corpus)
        or counts.get("evaluation") != 0
        or counts.get("heldout") != 0
        or corpus != _corpus_rows(states)
        or set(categories) != {f"state::{row['row_id']}" for row in states}
        or sum((Fraction(item) for item in categories.values()), Fraction()) != 1
        or set(objectives) != {
            "chosen_tail",
            "chosen_action",
            "rejected_unlikelihood",
        }
        or objectives["chosen_tail"] != {}
        or sum(
            (
                Fraction(item)
                for category in ("chosen_action", "rejected_unlikelihood")
                for item in objectives[category].values()
            ),
            Fraction(),
        )
        != 1
    ):
        raise Step35TrajectoryAdapterError("Step35 trainer adapter rows drifted")
    return {
        "adapter": value,
        "adapter_path": str(adapter_path),
        "adapter_file_sha256": _sha(adapter_path),
        "fresh_path": str(fresh_path),
        "retention_path": str(replay_path),
        "corpus_path": str(corpus_path),
        "pairs": states,
    }


def stage_inert(*, parent_adapter: Path, output_path: Path) -> dict[str, Any]:
    """Seal the no-launch contract while trajectory collection is running."""

    parent = step34._parent(parent_adapter)
    parent_pairs = parent["pairs"]
    if len(parent_pairs) != 45:
        raise Step35TrajectoryAdapterError("Step32 parent action-replay inventory drifted")
    body: dict[str, Any] = {
        "schema": STAGE_SCHEMA,
        "status": "waiting_for_complete_train_only_trajectory_manifest",
        "launch_authorized": False,
        "scientific_label": "step32_anchored_trajectory_level_on_policy_action_distillation",
        "target_asin": TARGET_ASIN,
        "source_step": SOURCE_STEP,
        "profiles": {
            name: {**profile, "candidate_steps": list(PROFILE_STEPS[name])}
            for name, profile in PROFILES.items()
        },
        "parent_receipt": PARENT_RECEIPT,
        "source_step32_adapter": {
            "path": str(Path(parent_adapter).resolve()),
            "sha256": PARENT_ADAPTER_FILE_SHA256,
            "adapter_sha256": PARENT_ADAPTER_BODY_SHA256,
            "action_replay_states": len(parent_pairs),
        },
        "source_mass": {
            "fresh_trajectory_actions": str(FRESH_TRAJECTORY_MASS),
            "step32_action_replay": str(STEP32_ACTION_REPLAY_MASS),
        },
        "normalization": (
            "equal_trajectory_then_equal_subgoal_then_equal_state_"
            "then_equal_action_token"
        ),
        "mask": {
            "chosen": "entire_structured_browseruse_action_json_only",
            "excluded": ["thinking", "evaluation_previous_goal", "memory", "next_goal"],
            "rejected": "stable_semantic_wrong_target_tokens_only",
            "generic_click_or_index_rejected_unlikelihood": False,
        },
        "trust_region": {
            "type": "step32_lora_shard_proximal_plus_hard_delta_rms",
            "profiles": PROFILES,
            "prime_patch_output_sha256": OUTPUT_SHA256,
            "full_vocab_reference_kl_claimed": False,
            "sampled_reference_logprobs_present": False,
        },
        "candidate_inventory": {
            "profiles": {
                name: list(PROFILE_STEPS[name]) for name in sorted(PROFILE_STEPS)
            },
            "save_after_every_update": True,
            "selection_before_frozen_eval": "fresh_train_only_canary_contract",
            "selector_keys": list(CANARY_SELECTOR_KEYS),
        },
        "required_dynamic_manifest": {
            "schema": MANIFEST_SCHEMA,
            "row_schema": ROW_SCHEMA,
            "trajectory_schema": TRAJECTORY_SCHEMA,
            "counts_are_dynamic": True,
            "source_split": "train",
            "eval": 0,
            "heldout": 0,
        },
        "process_policy": {
            "trainer_only": True,
            "gpu_launch": False,
            "candidate_reservation": False,
            "frozen_evaluation": False,
        },
    }
    value = {
        **body,
        "stage_body_sha256": hashlib.sha256(_canonical(body)).hexdigest(),
    }
    _write_new(output_path, value)
    return value


def validate_stage(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    body = {key: item for key, item in value.items() if key != "stage_body_sha256"}
    source = value.get("source_step32_adapter") or {}
    if (
        value.get("schema") != STAGE_SCHEMA
        or value.get("status") != "waiting_for_complete_train_only_trajectory_manifest"
        or value.get("launch_authorized") is not False
        or value.get("source_step") != SOURCE_STEP
        or value.get("profiles")
        != {
            name: {**profile, "candidate_steps": list(PROFILE_STEPS[name])}
            for name, profile in PROFILES.items()
        }
        or value.get("stage_body_sha256") != hashlib.sha256(_canonical(body)).hexdigest()
        or source.get("sha256") != PARENT_ADAPTER_FILE_SHA256
        or source.get("adapter_sha256") != PARENT_ADAPTER_BODY_SHA256
        or value.get("process_policy", {}).get("gpu_launch") is not False
    ):
        raise Step35TrajectoryAdapterError("Step35 inert stage contract drifted")
    step34._parent(str(source["path"]))
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage-inert")
    stage.add_argument("--parent-adapter", type=Path, required=True)
    stage.add_argument("--output", type=Path, required=True)
    check_stage = commands.add_parser("validate-stage")
    check_stage.add_argument("--stage", type=Path, required=True)
    check_manifest = commands.add_parser("validate-manifest")
    check_manifest.add_argument("--manifest", type=Path, required=True)
    materialize = commands.add_parser("materialize")
    materialize.add_argument("--manifest", type=Path, required=True)
    materialize.add_argument("--parent-adapter", type=Path, required=True)
    materialize.add_argument("--output-dir", type=Path, required=True)
    check_adapter = commands.add_parser("validate-adapter")
    check_adapter.add_argument("--adapter", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "stage-inert":
        result = stage_inert(parent_adapter=args.parent_adapter, output_path=args.output)
    elif args.command == "validate-stage":
        result = validate_stage(args.stage)
    elif args.command == "validate-manifest":
        result = validate_manifest(args.manifest)
        result = {
            key: item
            for key, item in result.items()
            if key not in {"rows", "canary_rows", "receipts"}
        }
        result["transition_masses"] = {
            key: str(item) for key, item in result["transition_masses"].items()
        }
    elif args.command == "materialize":
        result = materialize_adapter(
            manifest_path=args.manifest,
            parent_adapter_path=args.parent_adapter,
            output_dir=args.output_dir,
        )
    else:
        result = validate_trainer_adapter(args.adapter)
        result = {key: item for key, item in result.items() if key != "pairs"}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "ADAPTER_SCHEMA",
    "CANDIDATE_INVENTORY",
    "CANARY_SELECTOR_KEYS",
    "MANIFEST_SCHEMA",
    "MASK_POLICY",
    "PROFILE_STEPS",
    "ROW_SCHEMA",
    "STAGE_SCHEMA",
    "Step35TrajectoryAdapterError",
    "TRAJECTORY_SCHEMA",
    "UPDATE_STEPS",
    "stage_inert",
    "trajectory_normalization",
    "materialize_adapter",
    "validate_trainer_adapter",
    "validate_manifest",
    "validate_stage",
]
