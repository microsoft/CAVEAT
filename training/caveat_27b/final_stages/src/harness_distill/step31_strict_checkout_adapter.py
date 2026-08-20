"""Dynamic mixed-objective adapter for the step-30 -> step-31 update.

Fresh evidence is never duplicated to meet a quota.  Real Add/Delete failures
remain paired preferences; real Proceed/Place self-corrections remain
chosen-only CE.  The sealed HERO D/C/R and shortcut rows are replay retention.
Category mass is normalized independently of the observed row counts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import action_weighted_ce_training as base
from . import hero50_paired_adapter as sealed
from .hero50_contingency import HERO_ID
from .precommit_any_dagger_ops import _canonical, _write_new

STAGE_SCHEMA = "harness-distill.step31-strict-checkout-stage-plan.v2"
ADAPTER_SCHEMA = "harness-distill.step31-strict-checkout-mixed-adapter.v2"
STRICT_MANIFEST_SCHEMA = "harness-distill.hero50-strict-dynamic-materialization.v1"
STRICT_ROW_SCHEMA = "harness-distill.hero50-strict-training-row.v1"

SOURCE_STEP = 30
UPDATE_STEPS = (31,)
FINAL_STEP = 31
OPTIMIZER_UPDATES = 1
LEARNING_RATE = 3.0e-6
VARIANTS = ("graded", "graded3", "graded4", "mixed")
ADD_BUCKET = "hero_pdp_add"
DELETE_BUCKET = "dirty_cart_delete_addon"
PROCEED_BUCKET = "clean_cart_proceed"
PLACE_BUCKET = "checkout_place"
STRICT_BUCKETS = (ADD_BUCKET, DELETE_BUCKET, PROCEED_BUCKET, PLACE_BUCKET)
PAIRED_BUCKETS = (ADD_BUCKET, DELETE_BUCKET)
CHOSEN_ONLY_BUCKETS = (PROCEED_BUCKET, PLACE_BUCKET)

RETAINED_HERO_STATE_COUNT = 8
SHORTCUT_RETENTION_STATE_COUNT = 4
RETAINED_STATE_COUNT = RETAINED_HERO_STATE_COUNT + SHORTCUT_RETENTION_STATE_COUNT
RETAINED_CATEGORY_COUNTS = {
    "hero_frontier_discovery": 4,
    "hero_checkpoint_repair": 2,
    "hero_approved_rebind": 2,
    "shortcut_retention": 4,
}
SOURCE_GROUP_MASS = {
    "add_preference": Fraction(3, 10),
    "delete_preference": Fraction(3, 10),
    "executed_self_correct_chosen_ce": Fraction(1, 5),
    "sealed_dcr_shortcut_retention": Fraction(1, 5),
}
# The 20% retention group preserves the prior adapter's internal 40/25/15/20
# balance.  The chosen-only group is phase-balanced 50/50.
CATEGORY_COEFFICIENTS = {
    ADD_BUCKET: Fraction(3, 10),
    DELETE_BUCKET: Fraction(3, 10),
    PROCEED_BUCKET: Fraction(1, 10),
    PLACE_BUCKET: Fraction(1, 10),
    "hero_frontier_discovery": Fraction(2, 25),
    "hero_checkpoint_repair": Fraction(1, 20),
    "hero_approved_rebind": Fraction(3, 100),
    "shortcut_retention": Fraction(1, 25),
}
PAIRED_SEMANTIC_SPLIT = {
    "chosen_tail": Fraction(2, 5),
    "chosen_action": Fraction(3, 10),
    "rejected_unlikelihood": Fraction(3, 10),
}
CHOSEN_ONLY_SEMANTIC_SPLIT = {
    "chosen_tail": Fraction(4, 7),
    "chosen_action": Fraction(3, 7),
}

SEALED_RETENTION_ADAPTER_FILE_SHA256 = (
    "cc844c3d3a82eb703b635d8d59502aef7574915722357aac0b118f6f56ffae0e"
)
SEALED_HERO_PAIR_SHA256 = (
    "f7bec99e0629a7220f13083bedb93303c569794a0f55f582eb5cc6ab69998726"
)
SEALED_SHORTCUT_PAIR_SHA256 = (
    "d37391ceb394e7859e184177fe226ddc9fc7f63708beac39eb0923ec963e78e5"
)
PINNED_HANDOFF_FILE_SHA256 = (
    "93a447c34ee2a31feb4b45a3dc990f14b08ceb62bb3466d63d080d7e992cb626"
)
PINNED_HANDOFF = {
    "alias": "qwen35-browser-action-step30-hero50-paired-070976f9e976-exact-lora",
    "candidate_composite_sha256": (
        "3fe7f20e68ddd8ca0b4b08319190b650a9b232821e5c2efa1765bc0dd2f93615"
    ),
    "candidate_tree_sha256": (
        "070976f9e9765783568cc432e5ee40f2c0d51f874637a66fc9857efb5fdf3b7e"
    ),
    "local_base_url": "http://127.0.0.1:18548/v1",
    "receipt_body_sha256": (
        "1049b78c2cc13d98f47da51af26addc31a760d9517ea79b052ca7ce8b96cf847"
    ),
    "receipt_file_sha256": (
        "0eb83b3d78d7b3ae8c060e874806e498d825ad0bc3d53d64b2b49d0b68d2d975"
    ),
    "receipt_path": (
        "/data/runs/t-yuxuanli/t-yuxuanli-hpt-c2-hero50-paired-w1-20260815/"
        "training_r1/training/training_receipt.json"
    ),
    "release_body_sha256": (
        "92460d62b01fbebef26ece9f3d2ad38a1c1dfda0d7a322f72a21ae8742e7f936"
    ),
    "release_path": (
        "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
        "browser_action_next_iteration_reservations/20260813T1108Z/"
        "c2_hero50_candidate_serve_release_w1.json"
    ),
    "schema": "c2-hero50-candidate-release-handoff.v1",
    "serve_job": "t-yuxuanli-hpt-c2-hero50-candidate-w1",
    "serve_pod": "t-yuxuanli-hpt-c2-hero50-candidate-w1-master-0",
    "serve_pod_uid": "9b6ebef6-40bd-4638-9f63-00a2954ca7a6",
    "status": "published_after_successful_receipt",
}


class Step31AdapterError(RuntimeError):
    """A sealed source, real-evidence, or mixed-objective invariant failed."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _self_hash(value: Mapping[str, Any], field: str) -> str:
    body = {key: item for key, item in value.items() if key != field}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _fraction_map(values: Mapping[str, Fraction]) -> dict[str, str]:
    return {
        key: f"{value.numerator}/{value.denominator}" for key, value in values.items()
    }


def validate_parent_handoff(path: str | Path) -> dict[str, Any]:
    handoff_path = Path(path).resolve()
    if (
        not handoff_path.is_file()
        or handoff_path.is_symlink()
        or _sha256(handoff_path) != PINNED_HANDOFF_FILE_SHA256
    ):
        raise Step31AdapterError("sealed step-30 handoff bytes drifted")
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    if handoff != PINNED_HANDOFF:
        raise Step31AdapterError("sealed step-30 receipt/candidate identity drifted")
    return {"handoff": handoff, "path": str(handoff_path), "sha256": _sha256(handoff_path)}


def validate_retained_adapter(path: str | Path) -> dict[str, Any]:
    adapter_path = Path(path).resolve()
    if _sha256(adapter_path) != SEALED_RETENTION_ADAPTER_FILE_SHA256:
        raise Step31AdapterError("sealed HERO/shortcut adapter bytes drifted")
    result = sealed.validate_trainer_adapter(adapter_path)
    pairs = result["pairs"]
    hero = [row for row in pairs if row.get("training_bucket") != "shortcut_retention"]
    shortcut = [row for row in pairs if row.get("training_bucket") == "shortcut_retention"]
    if (
        len(hero) != RETAINED_HERO_STATE_COUNT
        or len(shortcut) != SHORTCUT_RETENTION_STATE_COUNT
        or Counter(row.get("training_bucket") for row in pairs)
        != Counter(RETAINED_CATEGORY_COUNTS)
        or hashlib.sha256(b"".join(_canonical(row) + b"\n" for row in hero)).hexdigest()
        != SEALED_HERO_PAIR_SHA256
        or hashlib.sha256(
            b"".join(_canonical(row) + b"\n" for row in shortcut)
        ).hexdigest()
        != SEALED_SHORTCUT_PAIR_SHA256
    ):
        raise Step31AdapterError("sealed HERO D/C/R or shortcut rows drifted")
    return {**result, "hero_pairs": hero, "shortcut_pairs": shortcut}


def _validate_sources(sources: object) -> None:
    if not isinstance(sources, Mapping) or set(sources) != {
        "add_manifest",
        "place_bundle",
        "proceed_bundle",
        "r3_bundle",
    }:
        raise Step31AdapterError("dynamic source inventory is absent")
    for name, descriptor in sources.items():
        path_value = descriptor.get("path") if isinstance(descriptor, Mapping) else None
        if not isinstance(path_value, str) or not _is_hex64(descriptor.get("sha256")):
            raise Step31AdapterError(f"dynamic source {name} identity is malformed")
        path = Path(path_value).resolve()
        if not path.is_file() or path.is_symlink() or _sha256(path) != descriptor["sha256"]:
            raise Step31AdapterError(f"dynamic source {name} bytes drifted")


def _validate_db_proof(row: Mapping[str, Any]) -> bool:
    successor = row.get("successor")
    if (
        not isinstance(successor, Mapping)
        or successor.get("successor_validated") is not True
        or successor.get("chosen_executed") is not True
        or successor.get("request_changed") is not True
        or not isinstance(successor.get("before_path"), str)
        or not isinstance(successor.get("after_path"), str)
        or not _is_hex64(successor.get("before_browser_state_sha256"))
        or not _is_hex64(successor.get("after_browser_state_sha256"))
    ):
        return False
    proof = successor.get("db_proof")
    return bool(
        isinstance(proof, Mapping)
        and proof.get("schema") == "harness-distill.hero50-strict-db-proof.v1"
        and proof.get("status") == "valid"
        and proof.get("bucket") == row.get("training_bucket")
        and proof.get("chosen_executed") is True
        and isinstance(proof.get("after_path"), str)
        and isinstance(proof.get("cart"), list)
        and isinstance(proof.get("new_order_ids"), list)
        and isinstance(proof.get("new_order_items"), list)
        and isinstance(proof.get("baseline_order_ids"), list)
        and all(
            _is_hex64(proof.get(key))
            for key in (
                "database_snapshot_sha256",
                "database_file_sha256",
                "stage_receipt_sha256",
                "stage_receipt_body_sha256",
                "proof_sha256",
            )
        )
        and _self_hash(proof, "proof_sha256") == proof.get("proof_sha256")
    )


def _base_row_valid(row: Mapping[str, Any]) -> bool:
    chosen = row.get("chosen")
    request = row.get("effective_request")
    if (
        row.get("schema") != STRICT_ROW_SCHEMA
        or row.get("source_split") != "train"
        or row.get("variant") not in VARIANTS
        or row.get("training_bucket") not in STRICT_BUCKETS
        or not isinstance(row.get("run_id"), str)
        or not isinstance(row.get("row_id"), str)
        or not isinstance(row.get("state_id"), str)
        or not isinstance(row.get("phase"), str)
        or not isinstance(row.get("trigger_kind", row.get("phase")), str)
        or not isinstance(request, Mapping)
        or not _is_hex64(row.get("effective_request_sha256"))
        or hashlib.sha256(_canonical(request)).hexdigest()
        != row.get("effective_request_sha256")
        or row.get("messages_before_action") != request.get("messages")
        or row.get("tools") != request.get("tools", [])
        or row.get("tool_choice") != request.get("tool_choice")
        or row.get("parallel_tool_calls") != request.get("parallel_tool_calls")
        or row.get("response_format") != request.get("response_format")
        or not isinstance(row.get("messages_before_action"), list)
        or not isinstance(row.get("tools"), list)
        or not isinstance(chosen, Mapping)
        or chosen.get("role") != "assistant"
        or not isinstance(chosen.get("content"), str)
        or not _is_hex64(row.get("chosen_sha256"))
        or hashlib.sha256(_canonical(chosen)).hexdigest() != row.get("chosen_sha256")
        or not _validate_db_proof(row)
    ):
        return False
    try:
        content = str(chosen["content"])
        if row.get("objective_kind") == "chosen_only":
            sealed._action_start_loose(content, sealed._string_lexemes(content))
        else:
            base.final_action_member_start(content)
    except (ValueError, base.ActionWeightedCEError):
        return False
    return True


def _fresh_row_valid(row: dict[str, Any]) -> bool:
    if not _base_row_valid(row):
        return False
    kind = row.get("objective_kind")
    bucket = row.get("training_bucket")
    rejected = row.get("rejected")
    if kind == "paired":
        teacher = row.get("strict_teacher_validation")
        invariance = row.get("invariance_audit")
        if (
            bucket not in PAIRED_BUCKETS
            or not isinstance(rejected, Mapping)
            or rejected.get("role") != "assistant"
            or not isinstance(rejected.get("content"), str)
            or rejected.get("content") == row["chosen"].get("content")
            or not _is_hex64(row.get("rejected_response_sha256"))
            or not isinstance(teacher, Mapping)
            or teacher.get("valid") is not True
            or teacher.get("policy") != "single_exact_visible_strict_checkout_control"
            or teacher.get("chosen_exact_target") is not True
            or teacher.get("rejected_exact_target") is not False
            or not isinstance(teacher.get("target_control_indices"), list)
            or not teacher["target_control_indices"]
            or not isinstance(invariance, Mapping)
            or invariance.get("same_exact_candidate_request") is not True
        ):
            return False
        try:
            sealed.rejected_action_spans(str(rejected["content"]))
        except sealed.Hero50AdapterError:
            return False
        return True
    provenance = row.get("chosen_provenance")
    expected_provenance = {
        PROCEED_BUCKET: "live_state_rule_expert_executed",
        PLACE_BUCKET: "candidate_self_correct",
    }
    teacher = row.get("strict_teacher_validation")
    return bool(
        kind == "chosen_only"
        and bucket in CHOSEN_ONLY_BUCKETS
        and provenance == expected_provenance[bucket]
        and rejected is None
        and row.get("rejected_response_sha256") is None
        and isinstance(teacher, Mapping)
        and teacher.get("valid") is True
    )


def validate_dynamic_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise Step31AdapterError("dynamic materialization manifest is absent")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    descriptor = manifest.get("training_rows")
    relative = descriptor.get("path") if isinstance(descriptor, Mapping) else None
    row_count = descriptor.get("rows") if isinstance(descriptor, Mapping) else None
    rows_path = manifest_path.parent / str(relative)
    if (
        manifest.get("schema") != STRICT_MANIFEST_SCHEMA
        or manifest.get("status") != "complete"
        or manifest.get("manifest_sha256") != _self_hash(manifest, "manifest_sha256")
        or type(row_count) is not int
        or row_count < 6
        or manifest.get("source_split") != "train"
        or manifest.get("split_counts")
        != {"train": row_count, "heldout": 0, "evaluation": 0}
        or not isinstance(descriptor, Mapping)
        or not isinstance(relative, str)
        or Path(relative).name != relative
        or descriptor.get("rows") != row_count
        or not rows_path.is_file()
        or rows_path.is_symlink()
        or descriptor.get("bytes") != rows_path.stat().st_size
        or descriptor.get("sha256") != _sha256(rows_path)
    ):
        raise Step31AdapterError("dynamic strict manifest or row bytes drifted")
    _validate_sources(manifest.get("sources"))
    rows = [
        json.loads(line)
        for line in rows_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    buckets = Counter(row.get("training_bucket") for row in rows)
    kinds = Counter(row.get("objective_kind") for row in rows)
    add_variants = {row.get("variant") for row in rows if row.get("training_bucket") == ADD_BUCKET}
    delete_variants = {
        row.get("variant") for row in rows if row.get("training_bucket") == DELETE_BUCKET
    }
    self_rows = [row for row in rows if row.get("objective_kind") == "chosen_only"]
    if (
        len(rows) != row_count
        or len({row.get("run_id") for row in rows}) != row_count
        or len({row.get("state_id") for row in rows}) != row_count
        or len({row.get("row_id") for row in rows}) != row_count
        or kinds.get("paired", 0) < 4
        or kinds.get("chosen_only", 0) < 2
        or buckets.get(ADD_BUCKET, 0) < 2
        or len(add_variants) < 2
        or buckets.get(DELETE_BUCKET, 0) < 2
        or len(delete_variants) < 2
        or buckets.get(PROCEED_BUCKET, 0) < 1
        or buckets.get(PLACE_BUCKET, 0) < 1
        or any(row.get("objective_kind") != "chosen_only" for row in self_rows)
        or not all(_fresh_row_valid(row) for row in rows)
        or manifest.get("bucket_counts") != dict(buckets)
        or manifest.get("objective_counts") != dict(kinds)
        or manifest.get("category_training_mass")
        != {
            "add_paired": 0.3,
            "delete_paired": 0.3,
            "strict_chosen_only": 0.2,
            "sealed_dcr_shortcut_retention": 0.2,
        }
    ):
        raise Step31AdapterError("dynamic strict minimums or real proof drifted")
    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "manifest_file_sha256": _sha256(manifest_path),
        "rows_path": str(rows_path.resolve()),
        "rows_sha256": _sha256(rows_path),
        "rows": rows,
    }


def corpus_rows(states: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for state in states:
        kind = state.get("objective_kind", "paired")
        message_keys = (("chosen", "chosen"),) if kind == "chosen_only" else (
            ("chosen", "chosen"),
            ("rejected", "rejected"),
        )
        for sample_kind, key in message_keys:
            rows.append(
                {
                    "kind": sample_kind,
                    "row_id": f"{state['row_id']}:{sample_kind}",
                    "pair_row_id": state["row_id"],
                    "state_id": state["state_id"],
                    "variant": state["variant"],
                    "training_bucket": state["training_bucket"],
                    "objective_kind": kind,
                    "phase": state["phase"],
                    "trigger_kind": state.get("trigger_kind", state["phase"]),
                    "messages_before_action": deepcopy(state["messages_before_action"]),
                    "assistant_message": deepcopy(state[key]),
                    "tools": deepcopy(state["tools"]),
                }
            )
    return rows


def objective_category_masses() -> dict[str, dict[str, Fraction]]:
    result = {"chosen_tail": {}, "chosen_action": {}, "rejected_unlikelihood": {}}
    for category, mass in CATEGORY_COEFFICIENTS.items():
        if category in CHOSEN_ONLY_BUCKETS:
            result["chosen_tail"][category] = mass * CHOSEN_ONLY_SEMANTIC_SPLIT["chosen_tail"]
            result["chosen_action"][category] = mass * CHOSEN_ONLY_SEMANTIC_SPLIT[
                "chosen_action"
            ]
        else:
            for objective, split in PAIRED_SEMANTIC_SPLIT.items():
                result[objective][category] = mass * split
    return result


def stage_inert_plan(
    *, retained_adapter_path: str | Path, handoff_path: str | Path, output_path: Path
) -> dict[str, Any]:
    retained = validate_retained_adapter(retained_adapter_path)
    handoff = validate_parent_handoff(handoff_path)
    if output_path.exists() or output_path.is_symlink():
        raise Step31AdapterError("stage plan destination must be fresh")
    body = {
        "schema": STAGE_SCHEMA,
        "status": "waiting_for_dynamic_strict_manifest",
        "launch_authorized": False,
        "source_step": SOURCE_STEP,
        "update_steps": list(UPDATE_STEPS),
        "final_step": FINAL_STEP,
        "optimizer_updates": OPTIMIZER_UPDATES,
        "learning_rate": LEARNING_RATE,
        "parent_handoff": {"sha256": handoff["sha256"], "value": handoff["handoff"]},
        "retained_adapter": {
            "path": retained["adapter_path"],
            "sha256": retained["adapter_file_sha256"],
            "states": RETAINED_STATE_COUNT,
            "hero_pair_sha256": SEALED_HERO_PAIR_SHA256,
            "shortcut_pair_sha256": SEALED_SHORTCUT_PAIR_SHA256,
        },
        "required_dynamic_manifest": {
            "schema": STRICT_MANIFEST_SCHEMA,
            "row_schema": STRICT_ROW_SCHEMA,
            "objective_kind_required": True,
            "minimums": {
                ADD_BUCKET: 2,
                DELETE_BUCKET: 2,
                PROCEED_BUCKET: 1,
                PLACE_BUCKET: 1,
            },
            "no_row_duplication_or_fabrication": True,
            "train_only_real_successor_and_db_proof": True,
        },
        "source_group_mass": _fraction_map(SOURCE_GROUP_MASS),
        "category_mix": _fraction_map(CATEGORY_COEFFICIENTS),
        "process_policy": {
            "endpoint_mutation": False,
            "gpu_launch": False,
            "candidate_release": False,
            "materialization_requires_complete_dynamic_manifest": True,
        },
    }
    plan = {**body, "plan_sha256": hashlib.sha256(_canonical(body)).hexdigest()}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_new(output_path, plan)
    return plan


def validate_stage_plan(path: str | Path) -> dict[str, Any]:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    retained = plan.get("retained_adapter")
    if (
        plan.get("schema") != STAGE_SCHEMA
        or plan.get("status") != "waiting_for_dynamic_strict_manifest"
        or plan.get("launch_authorized") is not False
        or plan.get("plan_sha256") != _self_hash(plan, "plan_sha256")
        or plan.get("source_step") != SOURCE_STEP
        or plan.get("update_steps") != list(UPDATE_STEPS)
        or plan.get("learning_rate") != LEARNING_RATE
        or plan.get("source_group_mass") != _fraction_map(SOURCE_GROUP_MASS)
        or not isinstance(retained, Mapping)
        or retained.get("sha256") != SEALED_RETENTION_ADAPTER_FILE_SHA256
        or plan.get("parent_handoff")
        != {"sha256": PINNED_HANDOFF_FILE_SHA256, "value": PINNED_HANDOFF}
        or plan.get("process_policy", {}).get("gpu_launch") is not False
    ):
        raise Step31AdapterError("inert step31 stage plan drifted")
    validate_retained_adapter(str(retained["path"]))
    return plan


def _descriptor(path: Path, rows: int) -> dict[str, Any]:
    return {
        "path": path.name,
        "rows": rows,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def materialize_trainer_adapter(
    *, stage_plan_path: str | Path, strict_manifest_path: str | Path, output_root: Path
) -> dict[str, Any]:
    stage = validate_stage_plan(stage_plan_path)
    retained = validate_retained_adapter(stage["retained_adapter"]["path"])
    fresh = validate_dynamic_manifest(strict_manifest_path)
    if output_root.exists() or output_root.is_symlink():
        raise Step31AdapterError("step31 trainer adapter output must be fresh")
    target_states = [*retained["hero_pairs"], *fresh["rows"]]
    states = [*target_states, *retained["shortcut_pairs"]]
    if (
        len({row["row_id"] for row in states}) != len(states)
        or len({row["state_id"] for row in states}) != len(states)
    ):
        raise Step31AdapterError("retained and dynamic state identities overlap")
    output_root.mkdir(parents=True)
    target_path = output_root / "fresh_hero50_pairs.jsonl"
    shortcut_path = output_root / "shortcut_retention_pairs.jsonl"
    corpus_path = output_root / "paired_corpus.jsonl"
    base._write_new(target_path, b"".join(_canonical(row) + b"\n" for row in target_states))
    base._write_new(
        shortcut_path,
        b"".join(_canonical(row) + b"\n" for row in retained["shortcut_pairs"]),
    )
    corpus = corpus_rows(states)
    base._write_new(corpus_path, b"".join(_canonical(row) + b"\n" for row in corpus))
    fresh_bucket_counts = Counter(row["training_bucket"] for row in fresh["rows"])
    fresh_kind_counts = Counter(row["objective_kind"] for row in fresh["rows"])
    category_counts = {**RETAINED_CATEGORY_COUNTS, **dict(fresh_bucket_counts)}
    state_count = len(states)
    chosen_count = state_count
    rejected_count = RETAINED_STATE_COUNT + fresh_kind_counts["paired"]
    chosen_only_count = fresh_kind_counts["chosen_only"]
    sample_count = chosen_count + rejected_count
    body = {
        "schema": ADAPTER_SCHEMA,
        "status": "ready_for_parent_receipt_binding",
        "launch_authorized": False,
        "target_identity": HERO_ID,
        "source_step": SOURCE_STEP,
        "update_steps": list(UPDATE_STEPS),
        "final_step": FINAL_STEP,
        "optimizer_updates": OPTIMIZER_UPDATES,
        "learning_rate": LEARNING_RATE,
        "fresh_optimizer": True,
        "fresh_scheduler": True,
        "fresh_dataloader": True,
        "parent_handoff": stage["parent_handoff"],
        "source_retained_adapter": {
            "path": retained["adapter_path"],
            "sha256": retained["adapter_file_sha256"],
            "hero_pair_sha256": SEALED_HERO_PAIR_SHA256,
            "shortcut_pair_sha256": SEALED_SHORTCUT_PAIR_SHA256,
        },
        "source_dynamic_manifest": {
            "path": fresh["manifest_path"],
            "sha256": fresh["manifest_file_sha256"],
            "rows_sha256": fresh["rows_sha256"],
            "sources": fresh["manifest"]["sources"],
        },
        "state_counts": {
            "states": state_count,
            "chosen_samples": chosen_count,
            "rejected_samples": rejected_count,
            "chosen_only_states": chosen_only_count,
            "samples": sample_count,
            "retained_hero_states": RETAINED_HERO_STATE_COUNT,
            "sealed_shortcut_retention_states": SHORTCUT_RETENTION_STATE_COUNT,
            "fresh_dynamic_states": len(fresh["rows"]),
            "fresh_objective_kinds": dict(fresh_kind_counts),
            "categories": category_counts,
            "evaluation": 0,
            "heldout": 0,
        },
        "source_group_mass": _fraction_map(SOURCE_GROUP_MASS),
        "objective": {
            "category_mix": _fraction_map(CATEGORY_COEFFICIENTS),
            "paired_semantic_split": _fraction_map(PAIRED_SEMANTIC_SPLIT),
            "chosen_only_semantic_split": _fraction_map(CHOSEN_ONLY_SEMANTIC_SPLIT),
            "objective_category_masses": {
                name: _fraction_map(values)
                for name, values in objective_category_masses().items()
            },
            "normalization": "category_then_state_then_semantic_token",
        },
        "custom_loss": {
            "import_path": sealed.CUSTOM_LOSS_IMPORT,
            "probability_cap": sealed.PROBABILITY_CAP,
            "precision": "float32",
        },
        "materialized_target_pairs": _descriptor(target_path, len(target_states)),
        "materialized_retention_pairs": _descriptor(
            shortcut_path, SHORTCUT_RETENTION_STATE_COUNT
        ),
        "corpus": _descriptor(corpus_path, sample_count),
    }
    value = {**body, "adapter_sha256": hashlib.sha256(_canonical(body)).hexdigest()}
    _write_new(output_root / "adapter.json", value)
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def validate_trainer_adapter(path: str | Path) -> dict[str, Any]:
    adapter_path = Path(path).resolve()
    value = json.loads(adapter_path.read_text(encoding="utf-8"))
    counts = value.get("state_counts")
    if (
        value.get("schema") != ADAPTER_SCHEMA
        or value.get("status") != "ready_for_parent_receipt_binding"
        or value.get("launch_authorized") is not False
        or value.get("target_identity") != HERO_ID
        or value.get("source_step") != SOURCE_STEP
        or value.get("update_steps") != list(UPDATE_STEPS)
        or value.get("final_step") != FINAL_STEP
        or value.get("optimizer_updates") != OPTIMIZER_UPDATES
        or value.get("learning_rate") != LEARNING_RATE
        or value.get("adapter_sha256") != _self_hash(value, "adapter_sha256")
        or value.get("parent_handoff")
        != {"sha256": PINNED_HANDOFF_FILE_SHA256, "value": PINNED_HANDOFF}
        or value.get("source_retained_adapter", {}).get("sha256")
        != SEALED_RETENTION_ADAPTER_FILE_SHA256
        or value.get("source_group_mass") != _fraction_map(SOURCE_GROUP_MASS)
        or not isinstance(counts, Mapping)
        or counts.get("evaluation") != 0
        or counts.get("heldout") != 0
    ):
        raise Step31AdapterError("portable dynamic trainer adapter policy drifted")

    def read_descriptor(key: str) -> tuple[Path, list[dict[str, Any]]]:
        descriptor = value.get(key)
        relative = descriptor.get("path") if isinstance(descriptor, Mapping) else None
        item = adapter_path.parent / str(relative)
        if (
            not isinstance(descriptor, Mapping)
            or not isinstance(relative, str)
            or Path(relative).name != relative
            or not item.is_file()
            or item.is_symlink()
            or descriptor.get("bytes") != item.stat().st_size
            or descriptor.get("sha256") != _sha256(item)
        ):
            raise Step31AdapterError(f"{key} bytes drifted")
        rows = _rows(item)
        if descriptor.get("rows") != len(rows):
            raise Step31AdapterError(f"{key} row count drifted")
        return item, rows

    target_path, target = read_descriptor("materialized_target_pairs")
    shortcut_path, shortcut = read_descriptor("materialized_retention_pairs")
    hero = [row for row in target if row.get("training_bucket") in sealed.BUCKET_TARGET]
    fresh = [row for row in target if row.get("training_bucket") in STRICT_BUCKETS]
    states = [*target, *shortcut]
    kinds = Counter(row.get("objective_kind") for row in fresh)
    categories = Counter(row.get("training_bucket") for row in states)
    if (
        hashlib.sha256(b"".join(_canonical(row) + b"\n" for row in hero)).hexdigest()
        != SEALED_HERO_PAIR_SHA256
        or hashlib.sha256(b"".join(_canonical(row) + b"\n" for row in shortcut)).hexdigest()
        != SEALED_SHORTCUT_PAIR_SHA256
        or not all(_fresh_row_valid(row) for row in fresh)
        or len({row.get("row_id") for row in states}) != len(states)
        or len({row.get("state_id") for row in states}) != len(states)
        or counts.get("states") != len(states)
        or counts.get("chosen_samples") != len(states)
        or counts.get("rejected_samples") != RETAINED_STATE_COUNT + kinds["paired"]
        or counts.get("chosen_only_states") != kinds["chosen_only"]
        or counts.get("categories") != dict(categories)
    ):
        raise Step31AdapterError("portable retained/dynamic state inventory drifted")
    expected_corpus_rows = corpus_rows(states)
    corpus_path, corpus = read_descriptor("corpus")
    if corpus != expected_corpus_rows or counts.get("samples") != len(corpus):
        raise Step31AdapterError("portable mixed-objective corpus drifted")
    return {
        "adapter": value,
        "adapter_path": str(adapter_path),
        "adapter_file_sha256": _sha256(adapter_path),
        "fresh_path": str(target_path.resolve()),
        "retention_path": str(shortcut_path.resolve()),
        "corpus_path": str(corpus_path.resolve()),
        "pairs": states,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    stage.add_argument("--retained-adapter", type=Path, required=True)
    stage.add_argument("--parent-handoff", type=Path, required=True)
    stage.add_argument("--output-plan", type=Path, required=True)
    dynamic = commands.add_parser("validate-dynamic")
    dynamic.add_argument("--manifest", type=Path, required=True)
    materialize = commands.add_parser("materialize")
    materialize.add_argument("--stage-plan", type=Path, required=True)
    materialize.add_argument("--strict-manifest", type=Path, required=True)
    materialize.add_argument("--output-root", type=Path, required=True)
    validate = commands.add_parser("validate-adapter")
    validate.add_argument("--adapter", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "stage":
        result = stage_inert_plan(
            retained_adapter_path=args.retained_adapter,
            handoff_path=args.parent_handoff,
            output_path=args.output_plan,
        )
    elif args.command == "validate-dynamic":
        result = validate_dynamic_manifest(args.manifest)
    elif args.command == "materialize":
        result = materialize_trainer_adapter(
            stage_plan_path=args.stage_plan,
            strict_manifest_path=args.strict_manifest,
            output_root=args.output_root,
        )
    else:
        result = validate_trainer_adapter(args.adapter)
    print(json.dumps({key: item for key, item in result.items() if key != "pairs"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ADAPTER_SCHEMA",
    "CATEGORY_COEFFICIENTS",
    "FINAL_STEP",
    "LEARNING_RATE",
    "SOURCE_GROUP_MASS",
    "SOURCE_STEP",
    "STRICT_MANIFEST_SCHEMA",
    "STRICT_ROW_SCHEMA",
    "UPDATE_STEPS",
    "materialize_trainer_adapter",
    "objective_category_masses",
    "stage_inert_plan",
    "validate_dynamic_manifest",
    "validate_parent_handoff",
    "validate_stage_plan",
    "validate_trainer_adapter",
]
