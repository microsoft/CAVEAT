"""Dynamic mixed-objective adapter for the step-31 -> step-32 correction.

The collector controls the number of real TRAIN states and whether each state
is paired or honestly chosen-only.  The adapter never duplicates a state to
meet a quota.  It combines those fresh states with sealed, target-aligned
step-31 retention and normalizes the result to four logical source groups:

* 45% visible-HERO discovery and approved-HERO rebind;
* 30% approved-HERO Add/post-Add-to-cart bridge;
* 20% dirty-cart cleanup, including multiple non-HERO lines;
* 5% clean-cart Proceed and checkout Place retention.

Physical training buckets are split by objective kind.  This keeps paired
chosen CE + rejected unlikelihood separate from honest chosen-only CE while
preserving exact logical-group mass under dynamic collector counts.
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
from urllib.parse import urlsplit

from . import action_weighted_ce_training as base
from . import hero50_paired_adapter as sealed
from . import step31_strict_checkout_adapter as previous
from .hero50_contingency import HERO_ID
from .precommit_any_dagger_ops import _canonical, _write_new

STAGE_SCHEMA = "harness-distill.step32-rebind-stage-plan.v1"
ADAPTER_SCHEMA = "harness-distill.step32-rebind-mixed-adapter.v1"
DYNAMIC_MANIFEST_SCHEMA = "harness-distill.step32-rebind-dynamic-materialization.v1"
DYNAMIC_ROW_SCHEMA = "harness-distill.step32-rebind-training-row.v1"

SOURCE_STEP = 31
UPDATE_STEPS = (32,)
FINAL_STEP = 32
OPTIMIZER_UPDATES = 1
LEARNING_RATE = 1.0e-6
VARIANTS = ("graded", "graded3", "graded4", "mixed")

UPSTREAM_GROUP = "upstream_visible_hero_discovery_rebind"
BRIDGE_GROUP = "post_hero_add_view_cart_bridge"
CLEANUP_GROUP = "multiline_dirty_cart_cleanup"
RETENTION_GROUP = "proceed_place_retention"
TRAINING_GROUPS = (UPSTREAM_GROUP, BRIDGE_GROUP, CLEANUP_GROUP, RETENTION_GROUP)
SOURCE_GROUP_MASS = {
    UPSTREAM_GROUP: Fraction(9, 20),
    BRIDGE_GROUP: Fraction(3, 10),
    CLEANUP_GROUP: Fraction(1, 5),
    RETENTION_GROUP: Fraction(1, 20),
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

SEALED_STEP31_ADAPTER_FILE_SHA256 = (
    "faee4132f8923004914feb0f124a7ba48b7caa2b581182524b5c7adc762f5a05"
)
SEALED_STEP31_TARGET_ROWS_SHA256 = (
    "dfef430f3148f4ec6dd107a6e5b5a7187cd0f006ff66935bf6314d8f51e2d0a4"
)
SEALED_STEP31_TARGET_SOURCE_COUNTS = {
    "hero_frontier_discovery": 4,
    "hero_checkpoint_repair": 2,
    "hero_approved_rebind": 2,
    "hero_pdp_add": 2,
    "dirty_cart_delete_addon": 2,
    "clean_cart_proceed": 1,
    "checkout_place": 1,
}
SEALED_RETENTION_SOURCE_COUNTS = {
    key: count
    for key, count in SEALED_STEP31_TARGET_SOURCE_COUNTS.items()
    if key not in {"clean_cart_proceed", "checkout_place"}
}
SEALED_GROUP_BY_BUCKET = {
    "hero_frontier_discovery": UPSTREAM_GROUP,
    "hero_checkpoint_repair": UPSTREAM_GROUP,
    "hero_approved_rebind": UPSTREAM_GROUP,
    "hero_pdp_add": BRIDGE_GROUP,
    "dirty_cart_delete_addon": CLEANUP_GROUP,
    "clean_cart_proceed": RETENTION_GROUP,
    "checkout_place": RETENTION_GROUP,
}
SEALED_STEP31_TARGET_STATE_COUNT = sum(SEALED_STEP31_TARGET_SOURCE_COUNTS.values())
SEALED_RETENTION_STATE_COUNT = sum(SEALED_RETENTION_SOURCE_COUNTS.values())

PINNED_HANDOFF_FILE_SHA256 = "b12996ba317a5073a2b110ee77a96c9825beaee507a807676cabfb8bade7312b"
PINNED_HANDOFF = {
    "alias": "qwen35-browser-action-step31-strict-checkout-paired-8e16ff76193b-exact-lora",
    "candidate_composite_sha256": (
        "1aec96e11a64f3cbb9427bd810277bf2649abce275c72aa254940d1140ee65fa"
    ),
    "candidate_tree_sha256": ("8e16ff76193bfb0e440ad8a49aab4987d5276775875e9cab9391c008d5a5dc70"),
    "local_base_url": "http://127.0.0.1:18549/v1",
    "receipt_body_sha256": ("ef43d2c854f4b828c795ec30ec269dfc6a1749c59ef2662eef6988ae0b902ff0"),
    "receipt_file_sha256": ("c21ce3c6f38a4d6c0e4b87a9c43b954f1c223d422b359e6ccff8d8139a0b11b9"),
    "receipt_path": (
        "/data/runs/t-yuxuanli/t-yuxuanli-hpt-c2-hero-strict-training-w1-20260815/"
        "training_r1/training/training_receipt.json"
    ),
    "release_body_sha256": ("0849299c32724ffac091a079cc4c4d02a4e9531d332f2b3b9e30bb7ed8570430"),
    "release_path": (
        "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
        "browser_action_next_iteration_reservations/20260813T1108Z/"
        "c2_hero_strict_candidate_serve_release_w1.json"
    ),
    "schema": "c2-hero-strict-candidate-release-handoff.v1",
    "serve_job": "t-yuxuanli-hpt-c2-hero-strict-candidate-w1",
    "serve_pod": "t-yuxuanli-hpt-c2-hero-strict-candidate-w1-master-0",
    "serve_pod_uid": "fc32233f-3123-429e-a610-b8f4b97828d0",
    "status": "published_after_successful_receipt",
}


class Step32AdapterError(RuntimeError):
    """A sealed lineage, evidence, or dynamic-mass invariant failed."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _self_hash(value: Mapping[str, Any], field: str) -> str:
    body = {key: item for key, item in value.items() if key != field}
    return hashlib.sha256(_canonical(body)).hexdigest()


def _fraction_map(values: Mapping[str, Fraction]) -> dict[str, str]:
    return {key: f"{value.numerator}/{value.denominator}" for key, value in values.items()}


def _fraction(value: str) -> Fraction:
    numerator, denominator = value.split("/", 1)
    return Fraction(int(numerator), int(denominator))


def _physical_bucket(group: str, kind: str) -> str:
    return f"{group}__{kind}"


def validate_parent_handoff(path: str | Path) -> dict[str, Any]:
    handoff_path = Path(path).resolve()
    if (
        not handoff_path.is_file()
        or handoff_path.is_symlink()
        or _sha256(handoff_path) != PINNED_HANDOFF_FILE_SHA256
    ):
        raise Step32AdapterError("sealed step-31 handoff bytes drifted")
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    if handoff != PINNED_HANDOFF:
        raise Step32AdapterError("sealed step-31 receipt/candidate identity drifted")
    return {"handoff": handoff, "path": str(handoff_path), "sha256": _sha256(handoff_path)}


def validate_retention_adapter(path: str | Path) -> dict[str, Any]:
    adapter_path = Path(path).resolve()
    if (
        not adapter_path.is_file()
        or adapter_path.is_symlink()
        or _sha256(adapter_path) != SEALED_STEP31_ADAPTER_FILE_SHA256
    ):
        raise Step32AdapterError("sealed step-31 adapter bytes drifted")
    result = previous.validate_trainer_adapter(adapter_path)
    target_path = Path(result["fresh_path"])
    target_pairs = [
        row for row in result["pairs"] if row.get("training_bucket") != "shortcut_retention"
    ]
    pairs = [
        row for row in target_pairs if row.get("training_bucket") in SEALED_RETENTION_SOURCE_COUNTS
    ]
    if (
        not target_path.is_file()
        or target_path.is_symlink()
        or _sha256(target_path) != SEALED_STEP31_TARGET_ROWS_SHA256
        or len(target_pairs) != SEALED_STEP31_TARGET_STATE_COUNT
        or Counter(row.get("training_bucket") for row in target_pairs)
        != Counter(SEALED_STEP31_TARGET_SOURCE_COUNTS)
        or len(pairs) != SEALED_RETENTION_STATE_COUNT
        or Counter(row.get("training_bucket") for row in pairs)
        != Counter(SEALED_RETENTION_SOURCE_COUNTS)
    ):
        raise Step32AdapterError("sealed target-aligned step-31 retention drifted")
    return {
        **result,
        "retention_pairs": pairs,
        "target_rows_path": str(target_path.resolve()),
        "target_rows_sha256": _sha256(target_path),
    }


def _mapped_retention_rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in result["retention_pairs"]:
        source_bucket = str(source["training_bucket"])
        kind = str(source.get("objective_kind") or "paired")
        if kind not in {"paired", "chosen_only"}:
            raise Step32AdapterError("sealed retention objective kind drifted")
        row = deepcopy(source)
        group = SEALED_GROUP_BY_BUCKET[source_bucket]
        row["source_training_bucket"] = source_bucket
        row["training_group"] = group
        row["training_bucket"] = _physical_bucket(group, kind)
        row["objective_kind"] = kind
        row["retention_kind"] = "sealed_step31_target_aligned"
        rows.append(row)
    return rows


def _sealed_json(path: Path, self_hash_field: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise Step32AdapterError(f"sealed JSON source is absent: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get(self_hash_field) != _self_hash(value, self_hash_field):
        raise Step32AdapterError(f"sealed JSON source self-hash drifted: {path}")
    return value


def _validate_sources(sources: object) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    if not isinstance(sources, Sequence) or isinstance(sources, (str, bytes)) or not sources:
        raise Step32AdapterError("dynamic source inventory is absent")
    result: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    kinds: set[str] = set()
    for descriptor in sources:
        path_value = descriptor.get("path") if isinstance(descriptor, Mapping) else None
        if (
            not isinstance(path_value, str)
            or not _is_hex64(descriptor.get("sha256"))
            or not _is_hex64(descriptor.get("bundle_sha256"))
            or descriptor.get("kind") not in {"upstream", "recovery"}
        ):
            raise Step32AdapterError("dynamic source identity is malformed")
        path = Path(path_value).resolve()
        if not path.is_file() or path.is_symlink() or _sha256(path) != descriptor["sha256"]:
            raise Step32AdapterError(f"dynamic source bytes drifted: {path}")
        bundle = _sealed_json(path, "bundle_sha256")
        if (
            bundle["bundle_sha256"] != descriptor["bundle_sha256"]
            or (bundle.get("matrix") or {}).get("evaluation_rows") != 0
            or any(row.get("split") != "train" for row in bundle.get("rows", []))
        ):
            raise Step32AdapterError("dynamic bundle is not sealed TRAIN-only evidence")
        key = (
            str(path),
            str(descriptor["sha256"]),
            str(descriptor["bundle_sha256"]),
            str(descriptor["kind"]),
        )
        if key in result:
            raise Step32AdapterError("dynamic source inventory contains a duplicate")
        result[key] = bundle
        kinds.add(str(descriptor["kind"]))
    if kinds != {"upstream", "recovery"}:
        raise Step32AdapterError("dynamic source inventory lacks upstream or recovery evidence")
    return result


def _validate_retention_source(descriptor: object) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(descriptor, Mapping):
        raise Step32AdapterError("dynamic retention source is absent")
    path_value = descriptor.get("path")
    rows_value = descriptor.get("rows_path")
    if (
        not isinstance(path_value, str)
        or not isinstance(rows_value, str)
        or not all(
            _is_hex64(descriptor.get(key)) for key in ("sha256", "manifest_sha256", "rows_sha256")
        )
    ):
        raise Step32AdapterError("dynamic retention source identity is malformed")
    path = Path(path_value).resolve()
    rows_path = Path(rows_value).resolve()
    if (
        not path.is_file()
        or path.is_symlink()
        or _sha256(path) != descriptor["sha256"]
        or not rows_path.is_file()
        or rows_path.is_symlink()
        or _sha256(rows_path) != descriptor["rows_sha256"]
    ):
        raise Step32AdapterError("dynamic retention source bytes drifted")
    manifest = _sealed_json(path, "manifest_sha256")
    rows_descriptor = manifest.get("training_rows")
    if (
        manifest["manifest_sha256"] != descriptor["manifest_sha256"]
        or not isinstance(rows_descriptor, Mapping)
        or rows_descriptor.get("sha256") != descriptor["rows_sha256"]
        or manifest.get("source_split") != "train"
        or manifest.get("split_counts", {}).get("evaluation") != 0
        or manifest.get("split_counts", {}).get("heldout") != 0
    ):
        raise Step32AdapterError("dynamic retention source contract drifted")
    rows = [json.loads(line) for line in rows_path.read_text().splitlines() if line]
    if rows_descriptor.get("rows") != len(rows):
        raise Step32AdapterError("dynamic retention source row count drifted")
    return manifest, rows


def _validate_successor(row: Mapping[str, Any]) -> bool:
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
    group = row.get("training_bucket")
    before = urlsplit(str(successor["before_path"])).path.rstrip("/")
    after = urlsplit(str(successor["after_path"])).path.rstrip("/")
    if group == UPSTREAM_GROUP and (
        after != f"/dp/{HERO_ID}"
        or successor.get("hero_transition_validated") is not True
        or successor.get("target_id") != HERO_ID
        or successor.get("after_pdp_id") != HERO_ID
    ):
        return False
    if group == BRIDGE_GROUP and (
        before != f"/dp/{HERO_ID}" or after not in {f"/dp/{HERO_ID}", "/gp/cart"}
    ):
        return False
    if group == CLEANUP_GROUP and (before != "/gp/cart" or after != "/gp/cart"):
        return False
    if group == RETENTION_GROUP and (before, after) not in {
        ("/gp/cart", "/gp/buy/spc"),
        ("/gp/buy/spc", "/gp/buy/thankyou"),
    }:
        return False
    proof = successor.get("db_proof")
    if group == UPSTREAM_GROUP:
        return True
    if not isinstance(proof, Mapping) or proof.get("status") != "valid":
        return False
    proof_hash = proof.get("proof_sha256")
    return bool(
        proof.get("schema") == "harness-distill.hero50-strict-db-proof.v1"
        and proof.get("chosen_executed") is True
        and isinstance(proof.get("cart"), list)
        and isinstance(proof.get("new_order_ids"), list)
        and isinstance(proof.get("new_order_items"), list)
        and (
            proof_hash is None
            or (_is_hex64(proof_hash) and _self_hash(proof, "proof_sha256") == proof_hash)
        )
    )


def _collector_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Restore the collector-sealed row from a portable physical-bucket copy."""

    source = deepcopy(row)
    group = source.get("training_group")
    if group is None:
        return source
    kind = source.get("objective_kind")
    if (
        group not in TRAINING_GROUPS
        or kind not in {"paired", "chosen_only"}
        or source.get("training_bucket") != _physical_bucket(str(group), str(kind))
        or source.get("retention_kind") is not None
    ):
        return None
    source["training_bucket"] = group
    source["state_id"] = source.pop("source_state_id")
    source.pop("training_group", None)
    source.pop("retention_kind", None)
    return source


def _fresh_row_valid(row: Mapping[str, Any]) -> bool:
    source = _collector_row(row)
    if source is None:
        return False
    chosen = source.get("chosen")
    request = source.get("effective_request")
    group = source.get("training_bucket")
    kind = source.get("objective_kind")
    materialized_body = {
        key: value for key, value in source.items() if key != "materialized_row_sha256"
    }
    if (
        source.get("schema") != DYNAMIC_ROW_SCHEMA
        or source.get("source_split") != "train"
        or source.get("variant") not in VARIANTS
        or group not in TRAINING_GROUPS
        or kind not in {"paired", "chosen_only"}
        or not _is_hex64(source.get("row_id"))
        or not _is_hex64(source.get("state_id"))
        or not isinstance(source.get("run_id"), str)
        or not isinstance(source.get("phase"), str)
        or not isinstance(request, Mapping)
        or not _is_hex64(source.get("effective_request_sha256"))
        or hashlib.sha256(_canonical(request)).hexdigest() != source.get("effective_request_sha256")
        or source.get("messages_before_action") != request.get("messages")
        or source.get("tools") != request.get("tools", [])
        or source.get("tool_choice") != request.get("tool_choice")
        or source.get("parallel_tool_calls") != request.get("parallel_tool_calls")
        or source.get("response_format") != request.get("response_format")
        or not isinstance(source.get("messages_before_action"), list)
        or not isinstance(source.get("tools"), list)
        or not isinstance(chosen, Mapping)
        or chosen.get("role") != "assistant"
        or not isinstance(chosen.get("content"), str)
        or not _is_hex64(source.get("chosen_sha256"))
        or hashlib.sha256(_canonical(chosen)).hexdigest() != source.get("chosen_sha256")
        or not _is_hex64(source.get("materialized_row_sha256"))
        or hashlib.sha256(_canonical(materialized_body)).hexdigest()
        != source.get("materialized_row_sha256")
        or not _validate_successor(source)
    ):
        return False
    teacher = source.get("strict_teacher_validation") or source.get("hero_teacher_validation")
    if not isinstance(teacher, Mapping) or teacher.get("valid") is not True:
        return False
    try:
        if kind == "chosen_only":
            sealed._action_start_loose(
                str(chosen["content"]), sealed._string_lexemes(str(chosen["content"]))
            )
        else:
            base.final_action_member_start(str(chosen["content"]))
    except (ValueError, base.ActionWeightedCEError):
        return False
    rejected = source.get("rejected")
    if kind == "chosen_only":
        expected_row_id = hashlib.sha256(
            _canonical(
                {
                    "state_id": source["state_id"],
                    "request": request,
                    "chosen": chosen,
                    "objective_kind": "chosen_only",
                }
            )
        ).hexdigest()
        return bool(
            rejected is None
            and source.get("rejected_response_sha256") is None
            and isinstance(source.get("chosen_provenance"), str)
            and source.get("row_id") == expected_row_id
        )
    invariance = source.get("invariance_audit")
    same_request = bool(
        isinstance(invariance, Mapping)
        and (
            invariance.get("same_exact_candidate_request") is True
            or (
                invariance.get("policy") == "same_exact_candidate_request_no_model_requery"
                and invariance.get("candidate_request_sha256")
                == source.get("effective_request_sha256")
                == invariance.get("expert_request_sha256")
                and invariance.get("messages_unchanged") is True
                and invariance.get("tools_unchanged") is True
            )
        )
    )
    expected_row_id = hashlib.sha256(
        _canonical(
            {
                "state_id": source["state_id"],
                "request": request,
                "chosen": chosen,
                "rejected": rejected,
            }
        )
    ).hexdigest()
    if (
        not isinstance(rejected, Mapping)
        or rejected.get("role") != "assistant"
        or not isinstance(rejected.get("content"), str)
        or rejected.get("content") == chosen.get("content")
        or not _is_hex64(source.get("rejected_response_sha256"))
        or not same_request
        or source.get("row_id") != expected_row_id
    ):
        return False
    try:
        sealed.rejected_action_spans(str(rejected["content"]))
    except sealed.Hero50AdapterError:
        return False
    return True


def _assistant_message(response: object) -> dict[str, Any] | None:
    if not isinstance(response, Mapping):
        return None
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        return None
    message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
    return deepcopy(message) if isinstance(message, Mapping) else None


def _source_row(row: Mapping[str, Any]) -> dict[str, Any]:
    source = deepcopy(row)
    source["schema"] = source.pop("source_row_schema")
    source["training_bucket"] = source.pop("source_training_bucket")
    source.pop("source_descriptor", None)
    source.pop("materialized_row_sha256", None)
    return source


def _row_source_valid(
    row: Mapping[str, Any],
    *,
    bundles: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
    retention_descriptor: Mapping[str, Any],
    retention_rows: Sequence[Mapping[str, Any]],
) -> bool:
    descriptor = row.get("source_descriptor")
    if not isinstance(descriptor, Mapping):
        return False
    if row.get("training_bucket") == RETENTION_GROUP:
        matches = [item for item in retention_rows if item.get("row_id") == row.get("row_id")]
        return bool(
            dict(descriptor) == dict(retention_descriptor) and matches == [_source_row(row)]
        )
    trace = descriptor.get("trace")
    base_descriptor = {key: value for key, value in descriptor.items() if key != "trace"}
    if (
        not isinstance(trace, Mapping)
        or not isinstance(trace.get("path"), str)
        or not _is_hex64(trace.get("sha256"))
    ):
        return False
    key = (
        str(Path(str(base_descriptor.get("path"))).resolve()),
        str(base_descriptor.get("sha256")),
        str(base_descriptor.get("bundle_sha256")),
        str(base_descriptor.get("kind")),
    )
    bundle = bundles.get(key)
    trace_path = Path(str(trace["path"])).resolve()
    if (
        bundle is None
        or not trace_path.is_file()
        or trace_path.is_symlink()
        or _sha256(trace_path) != trace["sha256"]
        or not any(
            source.get("run_id") == row.get("run_id")
            and source.get("variant") == row.get("variant")
            and Path(str(source.get("trace_path"))).resolve() == trace_path
            for source in bundle.get("rows", [])
        )
    ):
        return False
    records = [json.loads(line) for line in trace_path.read_text().splitlines() if line]
    actions = [
        item
        for item in records
        if item.get("state_id") == row.get("state_id")
        and item.get("effective_request") == row.get("effective_request")
        and item.get("trigger_kind") == row.get("phase")
        and item.get("route") in {"teacher_intervention", "self_correct_action"}
    ]
    action_valid = False
    for action in actions:
        if action.get("route") == "teacher_intervention":
            chosen = action.get("teacher_completion")
            chosen_source_sha = action.get("teacher_completion_sha256")
        else:
            response = action.get("chosen_response")
            chosen = _assistant_message(response)
            chosen_source_sha = action.get("chosen_response_sha256")
            if (
                not isinstance(response, Mapping)
                or chosen_source_sha != hashlib.sha256(_canonical(response)).hexdigest()
            ):
                continue
        if chosen != row.get("chosen") or chosen_source_sha != row.get(
            "source_chosen_response_sha256", row.get("chosen_sha256")
        ):
            continue
        if row.get("objective_kind") == "paired":
            rejected_response = action.get("qwen_rejected_response")
            if (
                _assistant_message(rejected_response) != row.get("rejected")
                or not isinstance(rejected_response, Mapping)
                or hashlib.sha256(_canonical(rejected_response)).hexdigest()
                != row.get("rejected_response_sha256")
            ):
                continue
        action_valid = True
        break
    successors = [
        item
        for item in records
        if item.get("state_id") == row.get("state_id")
        and item.get("route") == "successor"
        and all(item.get(key) == value for key, value in row.get("successor", {}).items())
    ]
    return action_valid and len(successors) == 1


def _state_rows_valid(rows: Sequence[Mapping[str, Any]]) -> bool:
    by_state: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_state.setdefault(str(row.get("state_id")), []).append(row)
    identity_fields = (
        "run_id",
        "variant",
        "phase",
        "effective_request",
        "chosen",
    )
    successor_fields = (
        "before_path",
        "after_path",
        "before_browser_state_sha256",
        "after_browser_state_sha256",
        "chosen_executed",
        "request_changed",
        "db_proof",
    )
    for group in by_state.values():
        if len(group) == 1:
            continue
        if len(group) != 2 or {row.get("objective_kind") for row in group} != {
            "paired",
            "chosen_only",
        }:
            return False
        reference = group[0]
        reference_group = reference.get("training_group", reference.get("training_bucket"))
        if any(
            row.get("training_group", row.get("training_bucket")) != reference_group
            or any(row.get(field) != reference.get(field) for field in identity_fields)
            for row in group[1:]
        ):
            return False
        if any(
            any(
                row.get("successor", {}).get(field) != reference.get("successor", {}).get(field)
                for field in successor_fields
            )
            for row in group[1:]
        ):
            return False
    return True


def validate_dynamic_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise Step32AdapterError("dynamic materialization manifest is absent")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    descriptor = manifest.get("training_rows")
    relative = descriptor.get("path") if isinstance(descriptor, Mapping) else None
    row_count = descriptor.get("rows") if isinstance(descriptor, Mapping) else None
    rows_path = manifest_path.parent / str(relative)
    if (
        manifest.get("schema") != DYNAMIC_MANIFEST_SCHEMA
        or manifest.get("status") != "complete"
        or manifest.get("manifest_sha256") != _self_hash(manifest, "manifest_sha256")
        or type(row_count) is not int
        or row_count <= 0
        or manifest.get("source_split") != "train"
        or manifest.get("split_counts") != {"train": row_count, "heldout": 0, "evaluation": 0}
        or not isinstance(descriptor, Mapping)
        or not isinstance(relative, str)
        or Path(relative).name != relative
        or not rows_path.is_file()
        or rows_path.is_symlink()
        or descriptor.get("bytes") != rows_path.stat().st_size
        or descriptor.get("sha256") != _sha256(rows_path)
        or manifest.get("target_identity") != HERO_ID
        or manifest.get("normalization") != "category_then_state_then_semantic_token"
        or not isinstance(manifest.get("category_training_mass"), Mapping)
        or {key: Fraction(str(value)) for key, value in manifest["category_training_mass"].items()}
        != SOURCE_GROUP_MASS
    ):
        raise Step32AdapterError("dynamic step32 manifest or row bytes drifted")
    sources = _validate_sources(manifest.get("sources"))
    retention_descriptor = manifest.get("retention_source")
    _, retention_rows = _validate_retention_source(retention_descriptor)
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line]
    groups = Counter(row.get("training_bucket") for row in rows)
    kinds = Counter(row.get("objective_kind") for row in rows)
    if (
        len(rows) != row_count
        or len({row.get("row_id") for row in rows}) != row_count
        or not _state_rows_valid(rows)
        or set(groups) - set(TRAINING_GROUPS)
        or not all(_fresh_row_valid(row) for row in rows)
        or not all(
            _row_source_valid(
                row,
                bundles=sources,
                retention_descriptor=retention_descriptor,
                retention_rows=retention_rows,
            )
            for row in rows
        )
        or manifest.get("category_counts") != dict(groups)
        or manifest.get("objective_counts") != dict(kinds)
    ):
        raise Step32AdapterError("dynamic step32 evidence or identity drifted")
    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "manifest_file_sha256": _sha256(manifest_path),
        "rows_path": str(rows_path.resolve()),
        "rows_sha256": _sha256(rows_path),
        "rows": rows,
    }


def _mapped_fresh_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in rows:
        row = deepcopy(source)
        group = str(source["training_bucket"])
        kind = str(source["objective_kind"])
        row["source_state_id"] = source["state_id"]
        row["state_id"] = source["row_id"]
        row["training_group"] = group
        row["training_bucket"] = _physical_bucket(group, kind)
        row["retention_kind"] = None
        result.append(row)
    return result


def corpus_rows(states: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for state in states:
        kind = str(state.get("objective_kind") or "paired")
        message_keys = (
            (("chosen", "chosen"),)
            if kind == "chosen_only"
            else (
                ("chosen", "chosen"),
                ("rejected", "rejected"),
            )
        )
        for sample_kind, key in message_keys:
            rows.append(
                {
                    "kind": sample_kind,
                    "row_id": f"{state['row_id']}:{sample_kind}",
                    "pair_row_id": state["row_id"],
                    # PRIME's state-balancing key must identify this objective
                    # observation, not collapse the collector's paired and
                    # chosen-only views of the same real browser state.
                    "state_id": state["state_id"],
                    "source_state_id": state.get("source_state_id", state["state_id"]),
                    "variant": state["variant"],
                    "training_bucket": state["training_bucket"],
                    "training_group": state["training_group"],
                    "objective_kind": kind,
                    "phase": state["phase"],
                    "trigger_kind": state.get("trigger_kind", state["phase"]),
                    "messages_before_action": deepcopy(state["messages_before_action"]),
                    "assistant_message": deepcopy(state[key]),
                    "tools": deepcopy(state["tools"]),
                }
            )
    return rows


def _objective_contract(
    states: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Fraction], dict[str, dict[str, Fraction]]]:
    group_kind_counts = Counter(
        (str(row["training_group"]), str(row["objective_kind"])) for row in states
    )
    category_mix: dict[str, Fraction] = {}
    objective = {"chosen_tail": {}, "chosen_action": {}, "rejected_unlikelihood": {}}
    for group, mass in SOURCE_GROUP_MASS.items():
        total = sum(group_kind_counts[(group, kind)] for kind in ("paired", "chosen_only"))
        if total <= 0:
            raise Step32AdapterError(f"logical group {group} has no states")
        for kind in ("paired", "chosen_only"):
            count = group_kind_counts[(group, kind)]
            if count <= 0:
                continue
            bucket = _physical_bucket(group, kind)
            coefficient = mass * Fraction(count, total)
            category_mix[bucket] = coefficient
            split = PAIRED_SEMANTIC_SPLIT if kind == "paired" else CHOSEN_ONLY_SEMANTIC_SPLIT
            objective["chosen_tail"][bucket] = coefficient * split["chosen_tail"]
            objective["chosen_action"][bucket] = coefficient * split["chosen_action"]
            if kind == "paired":
                objective["rejected_unlikelihood"][bucket] = (
                    coefficient * split["rejected_unlikelihood"]
                )
    if sum(category_mix.values(), Fraction()) != 1:
        raise Step32AdapterError("physical category mass does not sum to one")
    return category_mix, objective


def stage_inert_plan(
    *, retained_adapter_path: str | Path, handoff_path: str | Path, output_path: Path
) -> dict[str, Any]:
    retained = validate_retention_adapter(retained_adapter_path)
    handoff = validate_parent_handoff(handoff_path)
    if output_path.exists() or output_path.is_symlink():
        raise Step32AdapterError("stage plan destination must be fresh")
    body = {
        "schema": STAGE_SCHEMA,
        "status": "waiting_for_dynamic_step32_manifest",
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
            "target_rows_path": retained["target_rows_path"],
            "target_rows_sha256": retained["target_rows_sha256"],
            "states": SEALED_RETENTION_STATE_COUNT,
            "source_bucket_counts": SEALED_RETENTION_SOURCE_COUNTS,
            "shortcut_rows": 0,
        },
        "required_dynamic_manifest": {
            "schema": DYNAMIC_MANIFEST_SCHEMA,
            "row_schema": DYNAMIC_ROW_SCHEMA,
            "objective_kind_required": True,
            "counts_are_dynamic": True,
            "no_exact_quota": True,
            "no_row_duplication_or_fabrication": True,
            "train_only_real_successor_evidence": True,
            "evaluation_rows": 0,
            "heldout_rows": 0,
        },
        "source_group_mass": _fraction_map(SOURCE_GROUP_MASS),
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
        or plan.get("status") != "waiting_for_dynamic_step32_manifest"
        or plan.get("launch_authorized") is not False
        or plan.get("plan_sha256") != _self_hash(plan, "plan_sha256")
        or plan.get("source_step") != SOURCE_STEP
        or plan.get("update_steps") != list(UPDATE_STEPS)
        or plan.get("learning_rate") != LEARNING_RATE
        or plan.get("source_group_mass") != _fraction_map(SOURCE_GROUP_MASS)
        or not isinstance(retained, Mapping)
        or retained.get("sha256") != SEALED_STEP31_ADAPTER_FILE_SHA256
        or retained.get("target_rows_sha256") != SEALED_STEP31_TARGET_ROWS_SHA256
        or plan.get("parent_handoff")
        != {"sha256": PINNED_HANDOFF_FILE_SHA256, "value": PINNED_HANDOFF}
        or plan.get("process_policy", {}).get("gpu_launch") is not False
    ):
        raise Step32AdapterError("inert step32 stage plan drifted")
    validate_retention_adapter(str(retained["path"]))
    return plan


def _descriptor(path: Path, rows: int) -> dict[str, Any]:
    return {
        "path": path.name,
        "rows": rows,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def materialize_trainer_adapter(
    *, stage_plan_path: str | Path, dynamic_manifest_path: str | Path, output_root: Path
) -> dict[str, Any]:
    stage = validate_stage_plan(stage_plan_path)
    retained_source = validate_retention_adapter(stage["retained_adapter"]["path"])
    fresh_source = validate_dynamic_manifest(dynamic_manifest_path)
    if output_root.exists() or output_root.is_symlink():
        raise Step32AdapterError("step32 trainer adapter output must be fresh")
    retained = _mapped_retention_rows(retained_source)
    raw_fresh = fresh_source["rows"]
    if {row["state_id"] for row in retained} & {
        row["state_id"] for row in raw_fresh
    } or not _state_rows_valid(raw_fresh):
        raise Step32AdapterError("sealed and dynamic source state identities overlap")
    fresh = _mapped_fresh_rows(raw_fresh)
    states = [*retained, *fresh]
    if len({row["row_id"] for row in states}) != len(states) or len(
        {row["state_id"] for row in states}
    ) != len(states):
        raise Step32AdapterError("sealed and dynamic state identities overlap")
    category_mix, objective_masses = _objective_contract(states)
    output_root.mkdir(parents=True)
    # Keep the proven core's frozen-copy filenames.  Their semantic roles are
    # described by the adapter descriptors; the names are part of the portable
    # copy/rehydration contract used inside PRIME.
    retained_path = output_root / "shortcut_retention_pairs.jsonl"
    fresh_path = output_root / "fresh_hero50_pairs.jsonl"
    corpus_path = output_root / "paired_corpus.jsonl"
    base._write_new(retained_path, b"".join(_canonical(row) + b"\n" for row in retained))
    base._write_new(fresh_path, b"".join(_canonical(row) + b"\n" for row in fresh))
    corpus = corpus_rows(states)
    base._write_new(corpus_path, b"".join(_canonical(row) + b"\n" for row in corpus))
    physical_counts = Counter(row["training_bucket"] for row in states)
    logical_counts = Counter(row["training_group"] for row in states)
    kinds = Counter(row["objective_kind"] for row in states)
    fresh_kinds = Counter(row["objective_kind"] for row in fresh)
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
            "path": retained_source["adapter_path"],
            "sha256": retained_source["adapter_file_sha256"],
            "target_rows_path": retained_source["target_rows_path"],
            "target_rows_sha256": retained_source["target_rows_sha256"],
            "source_bucket_counts": SEALED_RETENTION_SOURCE_COUNTS,
            "shortcut_rows": 0,
        },
        "source_dynamic_manifest": {
            "path": fresh_source["manifest_path"],
            "sha256": fresh_source["manifest_file_sha256"],
            "rows_sha256": fresh_source["rows_sha256"],
            "sources": fresh_source["manifest"]["sources"],
        },
        "state_counts": {
            "states": len(states),
            "unique_state_ids": len({row["state_id"] for row in states}),
            "unique_source_state_ids": len(
                {row.get("source_state_id", row["state_id"]) for row in states}
            ),
            "chosen_samples": len(states),
            "rejected_samples": kinds["paired"],
            "chosen_only_states": kinds["chosen_only"],
            "samples": len(corpus),
            "sealed_retention_states": len(retained),
            "fresh_dynamic_states": len(fresh),
            "fresh_unique_state_ids": len({row["source_state_id"] for row in fresh}),
            "fresh_objective_kinds": dict(fresh_kinds),
            "objective_kinds": dict(kinds),
            "logical_groups": dict(logical_counts),
            "physical_categories": dict(physical_counts),
            "evaluation": 0,
            "heldout": 0,
        },
        "source_group_mass": _fraction_map(SOURCE_GROUP_MASS),
        "objective": {
            "category_mix": _fraction_map(category_mix),
            "paired_semantic_split": _fraction_map(PAIRED_SEMANTIC_SPLIT),
            "chosen_only_semantic_split": _fraction_map(CHOSEN_ONLY_SEMANTIC_SPLIT),
            "objective_category_masses": {
                name: _fraction_map(values) for name, values in objective_masses.items()
            },
            "normalization": "logical_group_then_objective_kind_then_state_then_semantic_token",
        },
        "custom_loss": {
            "import_path": sealed.CUSTOM_LOSS_IMPORT,
            "probability_cap": sealed.PROBABILITY_CAP,
            "precision": "float32",
        },
        "materialized_retention": _descriptor(retained_path, len(retained)),
        "materialized_fresh": _descriptor(fresh_path, len(fresh)),
        "corpus": _descriptor(corpus_path, len(corpus)),
    }
    value = {**body, "adapter_sha256": hashlib.sha256(_canonical(body)).hexdigest()}
    _write_new(output_root / "adapter.json", value)
    return value


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


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
        != SEALED_STEP31_ADAPTER_FILE_SHA256
        or value.get("source_group_mass") != _fraction_map(SOURCE_GROUP_MASS)
        or not isinstance(counts, Mapping)
        or counts.get("evaluation") != 0
        or counts.get("heldout") != 0
    ):
        raise Step32AdapterError("portable step32 trainer adapter policy drifted")

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
            raise Step32AdapterError(f"{key} bytes drifted")
        rows = _rows(item)
        if descriptor.get("rows") != len(rows):
            raise Step32AdapterError(f"{key} row count drifted")
        return item, rows

    source_retained = validate_retention_adapter(value["source_retained_adapter"]["path"])
    expected_retained = _mapped_retention_rows(source_retained)
    retained_path, retained = read_descriptor("materialized_retention")
    fresh_path, fresh = read_descriptor("materialized_fresh")
    if retained != expected_retained or not all(_fresh_row_valid(row) for row in fresh):
        raise Step32AdapterError("materialized sealed or dynamic rows drifted")
    raw_fresh = [_collector_row(row) for row in fresh]
    if any(row is None for row in raw_fresh):
        raise Step32AdapterError("portable source-state projection drifted")
    states = [*retained, *fresh]
    if (
        len({row["row_id"] for row in states}) != len(states)
        or len({row["state_id"] for row in states}) != len(states)
        or {row["state_id"] for row in retained}
        & {str(row["state_id"]) for row in raw_fresh if row is not None}
        or not _state_rows_valid([row for row in raw_fresh if row is not None])
    ):
        raise Step32AdapterError("portable state identity drifted")
    category_mix, objective_masses = _objective_contract(states)
    physical = Counter(row["training_bucket"] for row in states)
    logical = Counter(row["training_group"] for row in states)
    kinds = Counter(row["objective_kind"] for row in states)
    fresh_kinds = Counter(row["objective_kind"] for row in fresh)
    expected_counts = {
        "states": len(states),
        "unique_state_ids": len({row["state_id"] for row in states}),
        "unique_source_state_ids": len(
            {row.get("source_state_id", row["state_id"]) for row in states}
        ),
        "chosen_samples": len(states),
        "rejected_samples": kinds["paired"],
        "chosen_only_states": kinds["chosen_only"],
        "samples": len(states) + kinds["paired"],
        "sealed_retention_states": len(retained),
        "fresh_dynamic_states": len(fresh),
        "fresh_unique_state_ids": len({row["source_state_id"] for row in fresh}),
        "fresh_objective_kinds": dict(fresh_kinds),
        "objective_kinds": dict(kinds),
        "logical_groups": dict(logical),
        "physical_categories": dict(physical),
        "evaluation": 0,
        "heldout": 0,
    }
    if (
        dict(counts) != expected_counts
        or value["objective"]["category_mix"] != _fraction_map(category_mix)
        or value["objective"]["objective_category_masses"]
        != {name: _fraction_map(masses) for name, masses in objective_masses.items()}
    ):
        raise Step32AdapterError("portable dynamic mass or count contract drifted")
    corpus_path, corpus = read_descriptor("corpus")
    expected_corpus = corpus_rows(states)
    if corpus != expected_corpus:
        raise Step32AdapterError("portable step32 corpus drifted")
    return {
        "adapter": value,
        "adapter_path": str(adapter_path),
        "adapter_file_sha256": _sha256(adapter_path),
        "retention_path": str(retained_path.resolve()),
        "fresh_path": str(fresh_path.resolve()),
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
    materialize.add_argument("--dynamic-manifest", type=Path, required=True)
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
            dynamic_manifest_path=args.dynamic_manifest,
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
    "DYNAMIC_MANIFEST_SCHEMA",
    "DYNAMIC_ROW_SCHEMA",
    "FINAL_STEP",
    "LEARNING_RATE",
    "SOURCE_GROUP_MASS",
    "SOURCE_STEP",
    "TRAINING_GROUPS",
    "UPDATE_STEPS",
    "materialize_trainer_adapter",
    "stage_inert_plan",
    "validate_dynamic_manifest",
    "validate_parent_handoff",
    "validate_retention_adapter",
    "validate_stage_plan",
    "validate_trainer_adapter",
]
