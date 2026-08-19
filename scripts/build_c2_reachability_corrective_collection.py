#!/usr/bin/env python3
"""Build the campaign-2 reachability-corrective corpus from sealed local rows.

The builder is deliberately create-only.  It copies complete source rows,
adds provenance needed by the corrective trainer, and never reads evaluation
or holdout outcomes.
"""

from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = "harness-distill.c2-reachability-corrective-collection.v1"
VARIANTS = ("graded", "graded3", "graded4", "mixed")
DOWNSTREAM_PHASES = (
    "add_to_cart",
    "open_cart",
    "delete_extra",
    "clean_cart_to_checkout",
    "place_order",
)
EXPECTED_PHASE_COUNTS = {
    "frontier_exploration": 24,
    "checkpoint_grounding": 6,
    **{phase: 6 for phase in DOWNSTREAM_PHASES},
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_descriptor(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "relative_path": path.name,
        "rows": sum(1 for line in data.splitlines() if line.strip()),
        "bytes": len(data),
        "sha256": sha256_bytes(data),
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise TypeError(f"{path}:{line_number}: row is not an object")
            rows.append(row)
    return rows


def verified_manifest(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = path.read_bytes()
    manifest = json.loads(raw)
    claimed = manifest.get("manifest_sha256")
    body = dict(manifest)
    body.pop("manifest_sha256", None)
    body_sha256 = sha256_bytes(canonical_bytes(body))
    if claimed != body_sha256:
        raise ValueError(
            f"source manifest body hash mismatch: {path}: {claimed} != {body_sha256}"
        )
    return manifest, {
        "path": str(path.resolve()),
        "file_sha256": sha256_bytes(raw),
        "body_sha256": body_sha256,
        "schema": manifest.get("schema"),
    }


def verify_source_file(
    root: Path, manifest: dict[str, Any], relative_path: str
) -> tuple[Path, dict[str, Any]]:
    descriptor = manifest["files"].get(relative_path)
    if descriptor is None:
        # The older validation manifest keys descriptors by logical name.
        descriptor = next(
            (
                value
                for value in manifest["files"].values()
                if value.get("relative_path") == relative_path
            ),
            None,
        )
    if descriptor is None:
        raise ValueError(f"missing source descriptor for {relative_path}")
    path = root / relative_path
    observed = file_descriptor(path)
    for key in ("rows", "bytes", "sha256"):
        if observed[key] != descriptor[key]:
            raise ValueError(
                f"source {relative_path} {key} mismatch: "
                f"{observed[key]} != {descriptor[key]}"
            )
    return path, observed


def teacher_action_is_valid(row: dict[str, Any]) -> bool:
    message = row.get("teacher_message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return False
    content = message.get("content")
    if not isinstance(content, str) or not content:
        return False
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return False
    return (
        isinstance(parsed, dict)
        and list(parsed)[-1:] == ["action"]
        and isinstance(parsed.get("action"), list)
        and bool(parsed["action"])
    )


def frontier_selection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Choose six rows per variant while maximizing trajectory/time coverage.

    One representative is first chosen per available (horizon, trajectory):
    the exact takeover state when present, otherwise the temporal midpoint.
    Remaining slots are filled by farthest-point sampling in source-sequence
    space while preferring the least represented trajectory.
    """

    result: list[dict[str, Any]] = []
    for variant in VARIANTS:
        candidates = [
            row
            for row in rows
            if row.get("source_split") == "train"
            and row.get("phase") == "frontier_exploration"
            and row.get("variant") == variant
            and row.get("chosen_by_executor") is True
            and row.get("execution", {}).get("valid") is True
            and teacher_action_is_valid(row)
        ]
        groups: dict[tuple[int, str], list[dict[str, Any]]] = collections.defaultdict(
            list
        )
        for row in candidates:
            groups[(int(row["horizon"]), row["trajectory_id"])].append(row)
        if not groups:
            raise ValueError(f"no valid frontier rows for {variant}")
        for group in groups.values():
            group.sort(key=lambda row: (int(row["source_sequence"]), row["row_id"]))

        selected: list[dict[str, Any]] = []
        for key in sorted(groups):
            group = groups[key]
            takeover = [row for row in group if row.get("is_takeover_state") is True]
            representative = takeover[0] if takeover else group[(len(group) - 1) // 2]
            if len(selected) < 6:
                selected.append(representative)

        while len(selected) < 6:
            group_counts = collections.Counter(
                (int(row["horizon"]), row["trajectory_id"]) for row in selected
            )
            options: list[tuple[Any, dict[str, Any]]] = []
            for key, group in groups.items():
                selected_in_group = [
                    row
                    for row in selected
                    if (int(row["horizon"]), row["trajectory_id"]) == key
                ]
                for row in group:
                    if row in selected:
                        continue
                    distance = min(
                        abs(int(row["source_sequence"]) - int(other["source_sequence"]))
                        for other in selected_in_group
                    )
                    score = (
                        -group_counts[key],
                        distance,
                        -int(row["source_sequence"]),
                        row["row_id"],
                    )
                    options.append((score, row))
            if not options:
                raise ValueError(
                    f"fewer than six selectable frontier rows for {variant}"
                )
            selected.append(max(options, key=lambda item: item[0])[1])

        result.extend(
            sorted(
                selected,
                key=lambda row: (
                    VARIANTS.index(row["variant"]),
                    int(row["horizon"]),
                    row["trajectory_id"],
                    int(row["source_sequence"]),
                    row["row_id"],
                ),
            )
        )
    return result


def checkpoint_selection(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = [
        row
        for row in rows
        if row.get("source_split") == "train"
        and row.get("phase") == "checkpoint_grounding"
        and row.get("chosen_by_executor") is True
        and row.get("execution", {}).get("valid") is True
        and teacher_action_is_valid(row)
    ]
    selected.sort(
        key=lambda row: (
            VARIANTS.index(row["variant"]),
            int(row["horizon"]),
            row["trajectory_id"],
            int(row["source_sequence"]),
            row["row_id"],
        )
    )
    if len(selected) != 6:
        raise ValueError(
            f"expected exactly 6 valid TRAIN checkpoint rows, got {len(selected)}"
        )
    return selected


def normalize_validation_row(row: dict[str, Any]) -> dict[str, Any]:
    source_row_sha256 = sha256_bytes(canonical_bytes(row))
    normalized = copy.deepcopy(row)
    normalized["source_row_sha256"] = source_row_sha256
    normalized["source_family"] = "validation_7567180_r1"
    normalized["teacher_route"] = "on_policy_sol_correction"
    normalized["teacher_model_spec"] = "gpt-5.6-sol#low"
    normalized["episode_id"] = row["trajectory_id"]
    normalized["source_phase"] = row["phase"]
    normalized["source_phase_subtype"] = row["phase_subtype"]
    return normalized


def normalize_recovery_row(row: dict[str, Any]) -> dict[str, Any]:
    source_row_sha256 = sha256_bytes(canonical_bytes(row))
    normalized = copy.deepcopy(row)
    normalized["source_row_sha256"] = source_row_sha256
    normalized["source_family"] = "pure_sol_recovery_r3"
    normalized["teacher_route"] = "pure_sol"
    normalized["teacher_model_spec"] = "gpt-5.6-sol#low"
    normalized["trajectory_id"] = row["episode_id"]
    normalized["source_phase"] = row["phase"]
    normalized["source_phase_subtype"] = row["phase_subtype"]
    return normalized


def counter(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    return dict(sorted(collections.Counter(str(row[key]) for row in rows).items()))


def build(args: argparse.Namespace) -> tuple[Path, str, str]:
    validation_root = args.validation_root.resolve()
    recovery_root = args.recovery_root.resolve()
    output_root = args.output_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"create-only output already exists: {output_root}")

    validation_manifest, validation_manifest_identity = verified_manifest(
        validation_root / "manifest.json"
    )
    validation_path, validation_rows_identity = verify_source_file(
        validation_root, validation_manifest, "validated_transitions.jsonl"
    )
    recovery_manifest, recovery_manifest_identity = verified_manifest(
        recovery_root / "manifest.json"
    )
    recovery_path, recovery_rows_identity = verify_source_file(
        recovery_root, recovery_manifest, "selection.jsonl"
    )

    validation_rows = load_jsonl(validation_path)
    recovery_rows = load_jsonl(recovery_path)
    frontier = frontier_selection(validation_rows)
    checkpoints = checkpoint_selection(validation_rows)

    downstream = [
        row
        for row in recovery_rows
        if row.get("source_split") == "train"
        and row.get("phase") in DOWNSTREAM_PHASES
        and teacher_action_is_valid(row)
    ]
    downstream.sort(
        key=lambda row: (
            DOWNSTREAM_PHASES.index(row["phase"]),
            VARIANTS.index(row["variant"]),
            row["episode_id"],
            int(row["source_sequence"]),
            row["row_id"],
        )
    )
    downstream_counts = collections.Counter(row["phase"] for row in downstream)
    if downstream_counts != collections.Counter(
        {phase: 6 for phase in DOWNSTREAM_PHASES}
    ):
        raise ValueError(f"unexpected downstream phase counts: {downstream_counts}")

    rows = [
        *(normalize_validation_row(row) for row in frontier),
        *(normalize_validation_row(row) for row in checkpoints),
        *(normalize_recovery_row(row) for row in downstream),
    ]
    if len(rows) != 60:
        raise ValueError(f"expected 60 rows, got {len(rows)}")
    if len({row["row_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate row_id in output")
    if len({row["state_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate state_id in output")
    if any(row.get("source_split") != "train" for row in rows):
        raise ValueError("non-TRAIN row reached output")
    if any(not teacher_action_is_valid(row) for row in rows):
        raise ValueError("invalid teacher action reached output")

    phase_counts = collections.Counter(row["phase"] for row in rows)
    if phase_counts != collections.Counter(EXPECTED_PHASE_COUNTS):
        raise ValueError(f"unexpected phase counts: {phase_counts}")
    frontier_variant_counts = collections.Counter(row["variant"] for row in rows[:24])
    if frontier_variant_counts != collections.Counter(
        {variant: 6 for variant in VARIANTS}
    ):
        raise ValueError(
            f"unexpected frontier variant counts: {frontier_variant_counts}"
        )

    output_root.mkdir(parents=True, exist_ok=False)
    rows_path = output_root / "rows.jsonl"
    with rows_path.open("xb") as handle:
        for row in rows:
            handle.write(canonical_bytes(row) + b"\n")
    rows_descriptor = file_descriptor(rows_path)

    frontier_trajectories: dict[str, int] = {}
    frontier_horizons: dict[str, list[int]] = {}
    for variant in VARIANTS:
        selected = rows[:24]
        selected = [row for row in selected if row["variant"] == variant]
        frontier_trajectories[variant] = len({row["trajectory_id"] for row in selected})
        frontier_horizons[variant] = sorted({int(row["horizon"]) for row in selected})

    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "ok",
        "rows": 60,
        "source_split": "train",
        "heldout_rows": 0,
        "evaluation_rows": 0,
        "scorer_reward_rows": 0,
        "teacher_model": "gpt-5.6-sol",
        "teacher_reasoning_effort": "low",
        "existing_artifacts_only": True,
        "new_teacher_calls": 0,
        "replaces_step25_retention": True,
        "phase_counts": dict(sorted(phase_counts.items())),
        "phase_subtype_counts": counter(rows, "phase_subtype"),
        "variant_counts": counter(rows, "variant"),
        "frontier_variant_counts": dict(sorted(frontier_variant_counts.items())),
        "source_family_counts": counter(rows, "source_family"),
        "teacher_route_counts": counter(rows, "teacher_route"),
        "selection_audit": {
            "frontier_rows": 24,
            "frontier_takeover_states": sum(
                row.get("is_takeover_state") is True for row in rows[:24]
            ),
            "frontier_unique_trajectories": frontier_trajectories,
            "frontier_distinct_horizons": frontier_horizons,
            "checkpoint_rows": 6,
            "checkpoint_same_state_execution_valid": 6,
            "downstream_rows": 30,
            "unique_row_ids": len({row["row_id"] for row in rows}),
            "unique_state_ids": len({row["state_id"] for row in rows}),
            "unique_source_row_sha256": len({row["source_row_sha256"] for row in rows}),
        },
        "selection": {
            "frontier": (
                "TRAIN+teacher-executed+execution-valid only; exactly six per "
                "variant; one takeover-or-midpoint representative per available "
                "(horizon,trajectory), then balanced farthest-source-sequence fill"
            ),
            "checkpoint": (
                "all six TRAIN checkpoint_grounding rows with chosen_by_executor, "
                "valid verified execution, and action-final teacher JSON"
            ),
            "downstream": (
                "all recovery selection rows in add_to_cart/open_cart/delete_extra/"
                "clean_cart_to_checkout/place_order; recovery checkpoint omitted"
            ),
            "ordering": (
                "frontier variant/horizon/trajectory/sequence; checkpoint same; "
                "downstream phase/variant/episode/sequence"
            ),
            "source_row_sha256": "sha256(canonical JSON of the unmodified source row)",
        },
        "sources": {
            "validation_7567180_r1": {
                "manifest": validation_manifest_identity,
                "rows": validation_rows_identity,
            },
            "pure_sol_recovery_r3": {
                "manifest": recovery_manifest_identity,
                "rows": recovery_rows_identity,
            },
        },
        "files": {"rows.jsonl": rows_descriptor},
    }
    manifest["manifest_sha256"] = sha256_bytes(canonical_bytes(manifest))
    manifest_path = output_root / "manifest.json"
    with manifest_path.open("xb") as handle:
        handle.write(canonical_bytes(manifest) + b"\n")

    # Re-read the materialized files before returning their public identities.
    emitted_rows = load_jsonl(rows_path)
    emitted_manifest = json.loads(manifest_path.read_bytes())
    body = dict(emitted_manifest)
    claimed_body_sha256 = body.pop("manifest_sha256")
    if sha256_bytes(canonical_bytes(body)) != claimed_body_sha256:
        raise ValueError("emitted manifest body hash mismatch")
    if len(emitted_rows) != emitted_manifest["rows"]:
        raise ValueError("emitted row count mismatch")
    if file_descriptor(rows_path) != emitted_manifest["files"]["rows.jsonl"]:
        raise ValueError("emitted rows descriptor mismatch")
    return (
        output_root,
        sha256_bytes(manifest_path.read_bytes()),
        claimed_body_sha256,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validation-root", type=Path, required=True)
    parser.add_argument("--recovery-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    output_root, manifest_file_sha256, manifest_body_sha256 = build(parse_args())
    print(
        json.dumps(
            {
                "output_root": str(output_root),
                "manifest_file_sha256": manifest_file_sha256,
                "manifest_body_sha256": manifest_body_sha256,
                "status": "ok",
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
