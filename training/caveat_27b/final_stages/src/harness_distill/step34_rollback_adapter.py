"""Build the one-update Step34 rollback trainer adapter from proven Step32."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import step32_rebind_adapter as s32
from .precommit_any_dagger_ops import _canonical

STAGE_SCHEMA = "harness-distill.step34-rollback-stage-plan.v1"
ADAPTER_SCHEMA = "harness-distill.step34-rollback-action-mixed-adapter.v1"
DYNAMIC_SCHEMA = "harness-distill.step34-stable-semantic-materialization.v1"
DYNAMIC_ROW_SCHEMA = "harness-distill.step34-stable-semantic-training-row.v1"
SOURCE_STEP = 32
UPDATE_STEPS = (33,)
FINAL_STEP = 33
OPTIMIZER_UPDATES = 1
LEARNING_RATE = 5.0e-7
UPSTREAM_GROUP = "stable_origin_invariant_hero_navigation"
DOWNSTREAM_GROUP = "sealed_step32_full_downstream_chain"
SOURCE_GROUP_MASS = {UPSTREAM_GROUP: Fraction(2, 5), DOWNSTREAM_GROUP: Fraction(3, 5)}
UPSTREAM_PAIRED_SPLIT = {
    "chosen_action": Fraction(1, 2),
    "rejected_unlikelihood": Fraction(1, 2),
}
UPSTREAM_CHOSEN_ONLY_SPLIT = {"chosen_action": Fraction(1, 1)}
DOWNSTREAM_PAIRED_SPLIT = s32.PAIRED_SEMANTIC_SPLIT
DOWNSTREAM_CHOSEN_ONLY_SPLIT = s32.CHOSEN_ONLY_SEMANTIC_SPLIT

PARENT_ADAPTER_FILE_SHA256 = "e5bde4e98f9eb383c02a34da1b734763df9b32eb3f370a010f3d1707ac4c1f19"
PARENT_ADAPTER_BODY_SHA256 = "2c1ab1bd22f2d53f8a78a37115efcabb729f0bb76e23fb59cda54c7145d53ad4"

PARENT_RECEIPT = {
    "path": (
        "/data/runs/t-yuxuanli/"
        "t-yuxuanli-hpt-c2-step32-rebind-training-w1-20260815/"
        "step32_training_run_r2/training/training_receipt.json"
    ),
    "receipt_file_sha256": "40e8f39c9550c03c08d58edccab649ed39b5e3e5ccb1a458d5888e4138889671",
    "receipt_body_sha256": "8cf33d0667ac3205140efd5de8d881837ec64c2a3e50493a70b0b179b13f0319",
    "candidate_tree_sha256": "a2c2fe90c23bece6ba793d07d461304676d4bff8826e5d1464afd6bf8192dece",
    "final_dcp_tree_sha256": "f0a8d3a2bd779d0920e8c9665f3bf58f0ab807ec25622f9bee41545153432ea1",
}
PINNED_HANDOFF = PARENT_RECEIPT

FRESH_BUCKETS = {"hero_frontier_reach", "hero_frontier_discovery"}
DOWNSTREAM_BUCKETS = {
    "hero_pdp_add",
    "post_hero_add_view_cart",
    "dirty_cart_delete_wrong_laptop",
    "dirty_cart_delete_addon",
    "clean_cart_proceed",
    "checkout_place",
}
DOWNSTREAM_SOURCE_COUNTS = {
    "hero_pdp_add": 7,
    "post_hero_add_view_cart": 4,
    "dirty_cart_delete_wrong_laptop": 7,
    "dirty_cart_delete_addon": 8,
    "clean_cart_proceed": 1,
    "checkout_place": 1,
}
DOWNSTREAM_STATE_COUNT = sum(DOWNSTREAM_SOURCE_COUNTS.values())
FRESH_MASK_POLICY = "origin_invariant_action_only"
RETENTION_MASK_POLICY = "sealed_step32_semantic_objective"


class Step34AdapterError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _self_hash(value: Mapping[str, Any], field: str) -> str:
    return hashlib.sha256(
        _canonical({key: item for key, item in value.items() if key != field})
    ).hexdigest()


def _write_new(path: Path, value: Mapping[str, Any] | bytes) -> None:
    payload = value if isinstance(value, bytes) else _canonical(value) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)


def _fraction_map(values: Mapping[str, Fraction]) -> dict[str, str]:
    return {key: f"{value.numerator}/{value.denominator}" for key, value in values.items()}


def _physical(group: str, kind: str) -> str:
    return f"{group}__{kind}"


def _parent(path: str | Path) -> dict[str, Any]:
    resolved = Path(path).resolve()
    if (
        not resolved.is_file()
        or resolved.is_symlink()
        or _sha(resolved) != PARENT_ADAPTER_FILE_SHA256
    ):
        raise Step34AdapterError("sealed step32 adapter bytes drifted")
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if (
        value.get("schema") != s32.ADAPTER_SCHEMA
        or value.get("adapter_sha256") != PARENT_ADAPTER_BODY_SHA256
        or _self_hash(value, "adapter_sha256") != PARENT_ADAPTER_BODY_SHA256
        or value.get("source_step") != 31
        or value.get("update_steps") != [32]
        or value.get("final_step") != 32
        or value.get("optimizer_updates") != 1
        or value.get("learning_rate") != 1.0e-6
        or (value.get("state_counts") or {}).get("states") != 45
        or (value.get("state_counts") or {}).get("evaluation") != 0
        or (value.get("state_counts") or {}).get("heldout") != 0
    ):
        raise Step34AdapterError("sealed step32 adapter body drifted")
    rows: dict[str, list[dict[str, Any]]] = {}
    paths: dict[str, str] = {}
    for key in ("materialized_retention", "materialized_fresh", "corpus"):
        descriptor = value.get(key) or {}
        relative = descriptor.get("path")
        item = resolved.parent / str(relative)
        if (
            not isinstance(relative, str)
            or Path(relative).name != relative
            or not item.is_file()
            or item.is_symlink()
            or descriptor.get("bytes") != item.stat().st_size
            or descriptor.get("sha256") != _sha(item)
        ):
            raise Step34AdapterError(f"sealed step32 {key} bytes drifted")
        content = [json.loads(line) for line in item.read_text().splitlines() if line]
        if descriptor.get("rows") != len(content):
            raise Step34AdapterError(f"sealed step32 {key} rows drifted")
        rows[key] = content
        paths[key] = str(item.resolve())
    pairs = [*rows["materialized_retention"], *rows["materialized_fresh"]]
    if len(pairs) != 45 or len({row.get("row_id") for row in pairs}) != 45:
        raise Step34AdapterError("sealed step32 state inventory drifted")
    return {
        "adapter": value,
        "adapter_path": str(resolved),
        "adapter_file_sha256": _sha(resolved),
        "retention_path": paths["materialized_retention"],
        "fresh_path": paths["materialized_fresh"],
        "corpus_path": paths["corpus"],
        "pairs": pairs,
    }


def _is_hex64(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _same_request(row: Mapping[str, Any]) -> bool:
    audit = row.get("invariance_audit") or {}
    request_sha = row.get("effective_request_sha256")
    return bool(
        isinstance(audit, Mapping)
        and (
            audit.get("same_exact_candidate_request") is True
            or (
                audit.get("policy") == "same_exact_candidate_request_no_model_requery"
                and audit.get("candidate_request_sha256")
                == request_sha
                == audit.get("expert_request_sha256")
                and audit.get("messages_unchanged") is True
                and audit.get("tools_unchanged") is True
            )
        )
    )


def navigation_action(message: Mapping[str, Any]) -> tuple[str, str]:
    """Return the stable method and relative path/query of one navigate action."""

    content = message.get("content")
    if not isinstance(content, str):
        raise Step34AdapterError("navigation completion is not text")
    try:
        value = json.loads(content.strip())
        actions = value["action"]
        navigate = actions[0]["navigate"]
        url = str(navigate["url"])
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise Step34AdapterError("fresh completion is not one exact navigate action") from exc
    if (
        not isinstance(actions, list)
        or len(actions) != 1
        or not isinstance(actions[0], Mapping)
        or set(actions[0]) != {"navigate"}
        or not isinstance(navigate, Mapping)
        or set(navigate) - {"url", "new_tab"}
        or navigate.get("new_tab", False) is not False
    ):
        raise Step34AdapterError("fresh completion contains a non-stable action")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise Step34AdapterError("fresh navigate target is not an exact loopback origin URL")
    relative = parsed.path + (f"?{parsed.query}" if parsed.query else "")
    return "navigate", relative


def _fresh_row_valid(row: Mapping[str, Any]) -> bool:
    chosen = row.get("chosen")
    rejected = row.get("rejected")
    proof = row.get("proof") or {}
    request = row.get("effective_request")
    successor = row.get("successor") or {}
    validation = row.get("hero_teacher_validation") or {}
    source_trace = proof.get("source_trace") or {}
    kind = row.get("objective_kind")
    body = {key: item for key, item in row.items() if key != "materialized_row_sha256"}
    try:
        chosen_method, chosen_relative = navigation_action(chosen)  # type: ignore[arg-type]
    except Step34AdapterError:
        return False
    common = bool(
        row.get("schema") == DYNAMIC_ROW_SCHEMA
        and row.get("source_split") == "train"
        and row.get("training_bucket") in FRESH_BUCKETS
        and kind in {"paired", "chosen_only"}
        and row.get("variant") in s32.VARIANTS
        and _is_hex64(row.get("row_id"))
        and _is_hex64(row.get("state_id"))
        and row.get("state_id") == row.get("row_id")
        and _is_hex64(row.get("source_state_id"))
        and isinstance(row.get("run_id"), str)
        and isinstance(request, Mapping)
        and _is_hex64(row.get("effective_request_sha256"))
        and hashlib.sha256(_canonical(request)).hexdigest()
        == row.get("effective_request_sha256")
        and row.get("messages_before_action") == request.get("messages")
        and row.get("tools") == request.get("tools", [])
        and isinstance(chosen, Mapping)
        and chosen.get("role") == "assistant"
        and _is_hex64(row.get("chosen_sha256"))
        and hashlib.sha256(_canonical(chosen)).hexdigest() == row.get("chosen_sha256")
        and row.get("target_identity") == "EXP-LAPTOP-50"
        and row.get("action_method") == "navigate"
        and row.get("mask_policy") == FRESH_MASK_POLICY
        and row.get("reasoning_tail_training_enabled") is False
        and row.get("origin_port_training_enabled") is False
        and row.get("rejected_training_enabled") is False
        and _is_hex64(row.get("materialized_row_sha256"))
        and hashlib.sha256(_canonical(body)).hexdigest()
        == row.get("materialized_row_sha256")
        and proof.get("same_exact_candidate_request") is True
        and _is_hex64(source_trace.get("effective_request_sha256"))
        and _is_hex64(source_trace.get("sha256"))
        and isinstance(source_trace.get("path"), str)
        and validation.get("valid") is True
        and successor.get("successor_validated") is True
        and successor.get("request_changed") is True
        and successor.get("target_id") == "EXP-LAPTOP-50"
        and chosen_method == "navigate"
        and row.get("chosen_relative_target") == chosen_relative
    )
    if not common:
        return False
    if row.get("training_bucket") == "hero_frontier_discovery":
        if not (
            chosen_relative == "/dp/EXP-LAPTOP-50"
            and validation.get("chosen_exact_hero_navigation") is True
            and successor.get("hero_transition_validated") is True
            and successor.get("after_pdp_id") == "EXP-LAPTOP-50"
            and successor.get("after_path") == "/dp/EXP-LAPTOP-50"
        ):
            return False
    elif not (
        chosen_relative.startswith("/s?")
        and "q=M01" in chosen_relative.split("?", 1)[1].split("&")
        and validation.get("chosen_exact_frontier_navigation") is True
        and successor.get("after_path") == "/s"
        and successor.get("target_visible_on_list") is True
    ):
        return False
    if kind == "chosen_only":
        return bool(
            rejected is None
            and row.get("rejected_response_sha256") is None
            and row.get("rejected_semantic_policy") == "none_generic_action"
            and row.get("rejected_relative_target") is None
        )
    try:
        rejected_method, rejected_relative = navigation_action(rejected)  # type: ignore[arg-type]
    except Step34AdapterError:
        return False
    return bool(
        _same_request(row)
        and rejected_method == "navigate"
        and rejected_relative != chosen_relative
        and row.get("rejected_semantic_policy") == "stable_wrong_relative_target_only"
        and row.get("rejected_relative_target") == rejected_relative
    )


def _fresh(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise Step34AdapterError("fresh step34 manifest bytes drifted")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    descriptor = manifest.get("training_rows") or {}
    rows_path = manifest_path.parent / str(descriptor.get("path"))
    row_count = descriptor.get("rows")
    objective_counts = manifest.get("objective_counts") or {}
    if (
        manifest.get("schema") != DYNAMIC_SCHEMA
        or manifest.get("status") != "complete"
        or not _is_hex64(manifest.get("manifest_sha256"))
        or _self_hash(manifest, "manifest_sha256") != manifest.get("manifest_sha256")
        or not isinstance(row_count, int)
        or row_count <= 0
        or manifest.get("split_counts")
        != {"train": row_count, "heldout": 0, "evaluation": 0}
        or descriptor.get("path") != "training_rows.jsonl"
        or not _is_hex64(descriptor.get("sha256"))
        or not rows_path.is_file()
        or rows_path.is_symlink()
        or _sha(rows_path) != descriptor.get("sha256")
        or manifest.get("mask_contract")
        != {
            "chosen": "navigate method plus stable relative target tokens only",
            "excluded": ["scheme", "origin", "host", "port", "reasoning_tail"],
            "generic_click_scroll_extract_wait_rejected_ul": False,
            "stable_wrong_target_rejected_ul": True,
        }
    ):
        raise Step34AdapterError("fresh step34 manifest contract drifted")
    rows = [json.loads(line) for line in rows_path.read_text().splitlines() if line]
    kinds = Counter(row.get("objective_kind") for row in rows)
    if (
        len(rows) != row_count
        or objective_counts != {key: kinds[key] for key in ("paired", "chosen_only")}
        or not all(_fresh_row_valid(row) for row in rows)
        or len({row["row_id"] for row in rows}) != row_count
        or len({row["state_id"] for row in rows}) != row_count
    ):
        raise Step34AdapterError("fresh step34 identities are not unique")
    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "manifest_file_sha256": _sha(manifest_path),
        "rows_path": str(rows_path),
        "rows_sha256": _sha(rows_path),
        "rows": rows,
    }


def _selected_parent_rows(parent: Mapping[str, Any]) -> list[dict[str, Any]]:
    pairs = [deepcopy(row) for row in parent["pairs"]]
    downstream = [
        row
        for row in pairs
        if row.get("training_group")
        in {s32.BRIDGE_GROUP, s32.CLEANUP_GROUP, s32.RETENTION_GROUP}
        and row.get("source_training_bucket") in DOWNSTREAM_BUCKETS
    ]
    if (
        len(downstream) != DOWNSTREAM_STATE_COUNT
        or Counter(row.get("source_training_bucket") for row in downstream)
        != Counter(DOWNSTREAM_SOURCE_COUNTS)
        or Counter(row.get("objective_kind") for row in downstream)
        != {"paired": 8, "chosen_only": 20}
    ):
        raise Step34AdapterError("sealed full Step32 downstream chain drifted")
    result: list[dict[str, Any]] = []
    for source in downstream:
        row = deepcopy(source)
        row["source_step32_training_group"] = source["training_group"]
        row["source_step32_training_bucket"] = source["training_bucket"]
        row["training_group"] = DOWNSTREAM_GROUP
        row["training_bucket"] = _physical(DOWNSTREAM_GROUP, str(row["objective_kind"]))
        row["retention_kind"] = "sealed_step32_full_downstream_chain"
        row["mask_policy"] = RETENTION_MASK_POLICY
        result.append(row)
    return sorted(result, key=lambda row: row["row_id"])


def _mapped_fresh(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in rows:
        row = deepcopy(source)
        method, relative = navigation_action(source["chosen"])
        row["source_materialized_state_id"] = source["state_id"]
        row["source_step34_training_bucket"] = source["training_bucket"]
        row["state_id"] = source["row_id"]
        row["training_group"] = UPSTREAM_GROUP
        row["training_bucket"] = _physical(UPSTREAM_GROUP, str(source["objective_kind"]))
        row["retention_kind"] = None
        row["phase"] = source["training_bucket"]
        row["trigger_kind"] = source["training_bucket"]
        row["mask_policy"] = FRESH_MASK_POLICY
        row["chosen_action_method"] = method
        row["chosen_relative_target"] = relative
        result.append(row)
    return result


def _objective(
    states: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Fraction], dict[str, dict[str, Fraction]]]:
    counts = Counter((row["training_group"], row["objective_kind"]) for row in states)
    mix: dict[str, Fraction] = {}
    masses: dict[str, dict[str, Fraction]] = {
        "chosen_tail": {},
        "chosen_action": {},
        "rejected_unlikelihood": {},
    }
    for group, mass in SOURCE_GROUP_MASS.items():
        total = sum(counts[(group, kind)] for kind in ("paired", "chosen_only"))
        if total == 0:
            raise Step34AdapterError(f"training group {group} is empty")
        for kind in ("paired", "chosen_only"):
            count = counts[(group, kind)]
            if count == 0:
                continue
            bucket = _physical(group, kind)
            coefficient = mass * Fraction(count, total)
            mix[bucket] = coefficient
            if group == UPSTREAM_GROUP:
                split = (
                    UPSTREAM_PAIRED_SPLIT
                    if kind == "paired"
                    else UPSTREAM_CHOSEN_ONLY_SPLIT
                )
            else:
                split = (
                    DOWNSTREAM_PAIRED_SPLIT
                    if kind == "paired"
                    else DOWNSTREAM_CHOSEN_ONLY_SPLIT
                )
            for objective in ("chosen_tail", "chosen_action"):
                if objective in split:
                    masses[objective][bucket] = coefficient * split[objective]
            if "rejected_unlikelihood" in split:
                masses["rejected_unlikelihood"][bucket] = (
                    coefficient * split["rejected_unlikelihood"]
                )
    if sum(mix.values(), Fraction()) != 1:
        raise Step34AdapterError("step34 category mass does not sum to one")
    return mix, masses


def _descriptor(path: Path, rows: int) -> dict[str, Any]:
    return {"path": path.name, "rows": rows, "bytes": path.stat().st_size, "sha256": _sha(path)}


def _corpus(states: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = s32.corpus_rows(states)
    by_id = {str(state["row_id"]): state for state in states}
    for row in rows:
        source = by_id[str(row["pair_row_id"])]
        row["mask_policy"] = source["mask_policy"]
        if source["mask_policy"] == FRESH_MASK_POLICY:
            row["chosen_action_method"] = source["chosen_action_method"]
            row["chosen_relative_target"] = source["chosen_relative_target"]
            row["rejected_semantic_policy"] = source.get("rejected_semantic_policy")
            row["rejected_relative_target"] = source.get("rejected_relative_target")
    return rows


def stage_inert(*, parent_adapter: Path, output_path: Path) -> dict[str, Any]:
    parent = _parent(parent_adapter)
    selected = _selected_parent_rows(parent)
    if output_path.exists() or output_path.is_symlink():
        raise Step34AdapterError("Step34 inert stage destination must be fresh")
    body = {
        "schema": STAGE_SCHEMA,
        "status": "waiting_for_complete_train_only_manifest",
        "launch_authorized": False,
        "target_identity": "EXP-LAPTOP-50",
        "source_step": SOURCE_STEP,
        "update_steps": list(UPDATE_STEPS),
        "final_step": FINAL_STEP,
        "optimizer_updates": OPTIMIZER_UPDATES,
        "learning_rate": LEARNING_RATE,
        "parent_receipt": PARENT_RECEIPT,
        "source_step32_adapter": {
            "path": str(Path(parent_adapter).resolve()),
            "sha256": PARENT_ADAPTER_FILE_SHA256,
            "adapter_sha256": PARENT_ADAPTER_BODY_SHA256,
        },
        "sealed_downstream_retention": {
            "states": len(selected),
            "source_counts": DOWNSTREAM_SOURCE_COUNTS,
            "mass": "3/5",
            "required_chain": [
                "hero_pdp_add",
                "post_hero_add_view_cart",
                "dirty_cart_delete_wrong_laptop",
                "dirty_cart_delete_addon",
                "clean_cart_proceed",
                "checkout_place",
            ],
        },
        "required_dynamic_manifest": {
            "schema": DYNAMIC_SCHEMA,
            "row_schema": DYNAMIC_ROW_SCHEMA,
            "counts_are_dynamic": True,
            "source_split": "train",
            "evaluation": 0,
            "heldout": 0,
            "fresh_mass_maximum": "2/5",
            "generic_click_or_index_rejected_unlikelihood": False,
        },
        "semantic_mask": {
            "fresh_chosen": "navigate_method_plus_relative_path_query_only",
            "fresh_excluded": ["reasoning_tail", "origin", "host", "port", "json_syntax"],
            "fresh_rejected": "stable_wrong_relative_target_only",
        },
        "source_group_mass": _fraction_map(SOURCE_GROUP_MASS),
        "process_policy": {
            "trainer_only": True,
            "gpu_launch": False,
            "candidate_reservation": False,
            "evaluation": False,
        },
    }
    value = {**body, "stage_body_sha256": hashlib.sha256(_canonical(body)).hexdigest()}
    _write_new(output_path, value)
    return value


def validate_stage(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    source = value.get("source_step32_adapter") or {}
    if (
        value.get("schema") != STAGE_SCHEMA
        or value.get("status") != "waiting_for_complete_train_only_manifest"
        or value.get("launch_authorized") is not False
        or value.get("source_step") != SOURCE_STEP
        or value.get("update_steps") != list(UPDATE_STEPS)
        or value.get("optimizer_updates") != 1
        or value.get("learning_rate") != LEARNING_RATE
        or value.get("source_group_mass") != _fraction_map(SOURCE_GROUP_MASS)
        or value.get("stage_body_sha256") != _self_hash(value, "stage_body_sha256")
        or source.get("sha256") != PARENT_ADAPTER_FILE_SHA256
        or source.get("adapter_sha256") != PARENT_ADAPTER_BODY_SHA256
        or value.get("process_policy", {}).get("gpu_launch") is not False
    ):
        raise Step34AdapterError("Step34 inert stage plan drifted")
    _parent(str(source["path"]))
    return value


def materialize(*, parent_adapter: Path, fresh_manifest: Path, output_root: Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    if output_root.exists() or output_root.is_symlink():
        raise Step34AdapterError("adapter output root must be fresh")
    parent = _parent(parent_adapter)
    fresh_source = _fresh(fresh_manifest)
    retention = _selected_parent_rows(parent)
    fresh = _mapped_fresh(fresh_source["rows"])
    states = [*retention, *fresh]
    if len({row["row_id"] for row in states}) != len(states) or len(
        {row["state_id"] for row in states}
    ) != len(states):
        raise Step34AdapterError("step34 row/state identities overlap")
    mix, masses = _objective(states)
    output_root.mkdir(parents=True)
    # These filenames are the proven core's portable frozen-copy contract.
    retention_path = output_root / "shortcut_retention_pairs.jsonl"
    fresh_path = output_root / "fresh_hero50_pairs.jsonl"
    corpus_path = output_root / "paired_corpus.jsonl"
    _write_new(retention_path, b"".join(_canonical(row) + b"\n" for row in retention))
    _write_new(fresh_path, b"".join(_canonical(row) + b"\n" for row in fresh))
    corpus = _corpus(states)
    _write_new(corpus_path, b"".join(_canonical(row) + b"\n" for row in corpus))
    kinds = Counter(row["objective_kind"] for row in states)
    body = {
        "schema": ADAPTER_SCHEMA,
        "status": "ready_for_parent_receipt_binding",
        "launch_authorized": False,
        "target_identity": "EXP-LAPTOP-50",
        "source_step": SOURCE_STEP,
        "update_steps": list(UPDATE_STEPS),
        "final_step": FINAL_STEP,
        "optimizer_updates": OPTIMIZER_UPDATES,
        "learning_rate": LEARNING_RATE,
        "fresh_optimizer": True,
        "fresh_scheduler": True,
        "fresh_dataloader": True,
        "parent_receipt": PARENT_RECEIPT,
        "parent_handoff": {
            "sha256": hashlib.sha256(_canonical(PARENT_RECEIPT)).hexdigest(),
            "value": PARENT_RECEIPT,
        },
        "source_step32_adapter": {
            "path": str(Path(parent_adapter).resolve()),
            "sha256": PARENT_ADAPTER_FILE_SHA256,
            "adapter_sha256": PARENT_ADAPTER_BODY_SHA256,
        },
        "source_dynamic_manifest": {
            "path": fresh_source["manifest_path"],
            "sha256": fresh_source["manifest_file_sha256"],
            "manifest_sha256": fresh_source["manifest"]["manifest_sha256"],
            "rows_sha256": fresh_source["rows_sha256"],
        },
        "source_group_mass": _fraction_map(SOURCE_GROUP_MASS),
        "state_counts": {
            "states": len(states),
            "chosen_samples": len(states),
            "rejected_samples": kinds["paired"],
            "chosen_only_states": kinds["chosen_only"],
            "samples": len(corpus),
            "fresh_upstream_states": len(fresh),
            "fresh_upstream_paired_states": Counter(
                row["objective_kind"] for row in fresh
            )["paired"],
            "fresh_upstream_chosen_only_states": Counter(
                row["objective_kind"] for row in fresh
            )["chosen_only"],
            "sealed_downstream_retention_states": DOWNSTREAM_STATE_COUNT,
            "sealed_downstream_source_counts": DOWNSTREAM_SOURCE_COUNTS,
            "unique_state_ids": len({row["state_id"] for row in states}),
            "unique_source_state_ids": len(
                {row.get("source_state_id", row["state_id"]) for row in states}
            ),
            "evaluation": 0,
            "heldout": 0,
            "logical_groups": dict(Counter(row["training_group"] for row in states)),
            "objective_kinds": dict(kinds),
            "physical_categories": dict(Counter(row["training_bucket"] for row in states)),
        },
        "objective": {
            "category_mix": _fraction_map(mix),
            "upstream_paired_semantic_split": _fraction_map(UPSTREAM_PAIRED_SPLIT),
            "upstream_chosen_only_semantic_split": _fraction_map(
                UPSTREAM_CHOSEN_ONLY_SPLIT
            ),
            "downstream_paired_semantic_split": _fraction_map(
                DOWNSTREAM_PAIRED_SPLIT
            ),
            "downstream_chosen_only_semantic_split": _fraction_map(
                DOWNSTREAM_CHOSEN_ONLY_SPLIT
            ),
            "objective_category_masses": {
                name: _fraction_map(value) for name, value in masses.items()
            },
            "normalization": "logical_group_then_objective_kind_then_state_then_semantic_token",
        },
        "semantic_mask": {
            "fresh_chosen": "navigate_method_plus_relative_path_query_only",
            "fresh_excluded": ["reasoning_tail", "origin", "host", "port", "json_syntax"],
            "fresh_rejected": "stable_wrong_relative_target_only",
            "generic_click_or_index_rejected_unlikelihood": False,
            "sealed_downstream": "exact_step32_semantic_objective",
        },
        "custom_loss": {
            "import_path": s32.sealed.CUSTOM_LOSS_IMPORT,
            "probability_cap": s32.sealed.PROBABILITY_CAP,
            "precision": "float32",
        },
        "materialized_retention": _descriptor(retention_path, len(retention)),
        "materialized_fresh": _descriptor(fresh_path, len(fresh)),
        "corpus": _descriptor(corpus_path, len(corpus)),
    }
    adapter = {**body, "adapter_sha256": hashlib.sha256(_canonical(body)).hexdigest()}
    _write_new(output_root / "adapter.json", adapter)
    return adapter


def _read_descriptor(
    adapter_path: Path, value: Mapping[str, Any], key: str
) -> list[dict[str, Any]]:
    descriptor = value.get(key) or {}
    relative = descriptor.get("path")
    path = adapter_path.parent / str(relative)
    if (
        not isinstance(relative, str)
        or Path(relative).name != relative
        or not path.is_file()
        or path.is_symlink()
        or descriptor.get("bytes") != path.stat().st_size
        or descriptor.get("sha256") != _sha(path)
    ):
        raise Step34AdapterError(f"portable {key} bytes drifted")
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    if descriptor.get("rows") != len(rows):
        raise Step34AdapterError(f"portable {key} row count drifted")
    return rows


def _portable_fresh_valid(row: Mapping[str, Any]) -> bool:
    source = deepcopy(row)
    source["state_id"] = source.pop("source_materialized_state_id", None)
    source["training_bucket"] = source.pop("source_step34_training_bucket", None)
    source.pop("training_group", None)
    source.pop("retention_kind", None)
    source.pop("phase", None)
    source.pop("trigger_kind", None)
    for key in ("chosen_action_method",):
        source.pop(key, None)
    # ``chosen_relative_target`` is collector-sealed when present.  The
    # adapter merely verifies it and therefore leaves it in the raw row.
    return _fresh_row_valid(source)


def _portable_retention_valid(row: Mapping[str, Any]) -> bool:
    group = row.get("training_group")
    kind = row.get("objective_kind")
    previous_group = row.get("source_step32_training_group")
    previous = deepcopy(row)
    previous["training_bucket"] = previous_group
    validation = row.get("hero_teacher_validation") or row.get("strict_teacher_validation") or {}
    if (
        row.get("source_split") != "train"
        or group != DOWNSTREAM_GROUP
        or kind not in {"paired", "chosen_only"}
        or row.get("training_bucket") != _physical(str(group), str(kind))
        or row.get("mask_policy") != RETENTION_MASK_POLICY
        or not isinstance(row.get("chosen"), Mapping)
        or validation.get("valid") is not True
        or not s32._validate_successor(previous)
    ):
        return False
    if kind == "chosen_only":
        return "rejected" not in row
    invariance = row.get("invariance_audit") or {}
    return bool(
        isinstance(row.get("rejected"), Mapping)
        and (
            invariance.get("same_exact_candidate_request") is True
            or (
                invariance.get("policy") == "same_exact_candidate_request_no_model_requery"
                and invariance.get("candidate_request_sha256")
                == row.get("effective_request_sha256")
                == invariance.get("expert_request_sha256")
                and invariance.get("messages_unchanged") is True
                and invariance.get("tools_unchanged") is True
            )
        )
    )


def validate(path: str | Path) -> dict[str, Any]:
    adapter_path = Path(path).resolve()
    value = json.loads(adapter_path.read_text())
    if (
        value.get("schema") != ADAPTER_SCHEMA
        or value.get("status") != "ready_for_parent_receipt_binding"
        or value.get("launch_authorized") is not False
        or value.get("source_step") != SOURCE_STEP
        or value.get("update_steps") != list(UPDATE_STEPS)
        or value.get("learning_rate") != LEARNING_RATE
        or value.get("adapter_sha256") != _self_hash(value, "adapter_sha256")
        or value.get("parent_receipt") != PARENT_RECEIPT
        or value.get("parent_handoff")
        != {
            "sha256": hashlib.sha256(_canonical(PARENT_RECEIPT)).hexdigest(),
            "value": PARENT_RECEIPT,
        }
        or value.get("source_group_mass") != _fraction_map(SOURCE_GROUP_MASS)
    ):
        raise Step34AdapterError("portable step34 adapter policy drifted")
    parent_source = value.get("source_step32_adapter") or {}
    dynamic_source = value.get("source_dynamic_manifest") or {}
    if parent_source != {
        "path": parent_source.get("path"),
        "sha256": PARENT_ADAPTER_FILE_SHA256,
        "adapter_sha256": PARENT_ADAPTER_BODY_SHA256,
    } or set(dynamic_source) != {"path", "sha256", "manifest_sha256", "rows_sha256"}:
        raise Step34AdapterError("portable step34 source identity drifted")
    fresh_source = _fresh(str(dynamic_source["path"]))
    if dynamic_source != {
        "path": fresh_source["manifest_path"],
        "sha256": fresh_source["manifest_file_sha256"],
        "manifest_sha256": fresh_source["manifest"]["manifest_sha256"],
        "rows_sha256": fresh_source["rows_sha256"],
    }:
        raise Step34AdapterError("portable step34 dynamic source identity drifted")
    retention = _read_descriptor(adapter_path, value, "materialized_retention")
    fresh = _read_descriptor(adapter_path, value, "materialized_fresh")
    expected_retention = _selected_parent_rows(_parent(str(parent_source["path"])))
    if (
        retention != expected_retention
        or not all(_portable_retention_valid(row) for row in retention)
        or len(fresh) != len(fresh_source["rows"])
        or not all(_portable_fresh_valid(row) for row in fresh)
    ):
        raise Step34AdapterError("portable step34 source projection drifted")
    states = [*retention, *fresh]
    mix, masses = _objective(states)
    kinds = Counter(row["objective_kind"] for row in states)
    fresh_kinds = Counter(row["objective_kind"] for row in fresh)
    counts = value.get("state_counts") or {}
    expected_counts = {
        "states": len(states),
        "chosen_samples": len(states),
        "rejected_samples": kinds["paired"],
        "chosen_only_states": kinds["chosen_only"],
        "samples": len(states) + kinds["paired"],
        "fresh_upstream_states": len(fresh),
        "fresh_upstream_paired_states": fresh_kinds["paired"],
        "fresh_upstream_chosen_only_states": fresh_kinds["chosen_only"],
        "sealed_downstream_retention_states": DOWNSTREAM_STATE_COUNT,
        "sealed_downstream_source_counts": DOWNSTREAM_SOURCE_COUNTS,
        "unique_state_ids": len({row["state_id"] for row in states}),
        "unique_source_state_ids": len(
            {row.get("source_state_id", row["state_id"]) for row in states}
        ),
        "evaluation": 0,
        "heldout": 0,
        "logical_groups": dict(Counter(row["training_group"] for row in states)),
        "objective_kinds": dict(kinds),
        "physical_categories": dict(Counter(row["training_bucket"] for row in states)),
    }
    corpus = _read_descriptor(adapter_path, value, "corpus")
    if (
        counts != expected_counts
        or value["objective"]["category_mix"] != _fraction_map(mix)
        or value["objective"]["objective_category_masses"]
        != {name: _fraction_map(item) for name, item in masses.items()}
        or value.get("semantic_mask")
        != {
            "fresh_chosen": "navigate_method_plus_relative_path_query_only",
            "fresh_excluded": ["reasoning_tail", "origin", "host", "port", "json_syntax"],
            "fresh_rejected": "stable_wrong_relative_target_only",
            "generic_click_or_index_rejected_unlikelihood": False,
            "sealed_downstream": "exact_step32_semantic_objective",
        }
        or corpus != _corpus(states)
    ):
        raise Step34AdapterError("portable step34 counts/objective/corpus drifted")
    return {
        "adapter": value,
        "adapter_path": str(adapter_path),
        "adapter_file_sha256": _sha(adapter_path),
        "retention_path": str(
            (adapter_path.parent / value["materialized_retention"]["path"]).resolve()
        ),
        "fresh_path": str((adapter_path.parent / value["materialized_fresh"]["path"]).resolve()),
        "corpus_path": str((adapter_path.parent / value["corpus"]["path"]).resolve()),
        "pairs": states,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage-inert")
    stage.add_argument("--parent-adapter", type=Path, required=True)
    stage.add_argument("--output", type=Path, required=True)
    check_stage = commands.add_parser("validate-stage")
    check_stage.add_argument("--stage", type=Path, required=True)
    build = commands.add_parser("materialize")
    build.add_argument("--parent-adapter", type=Path, required=True)
    build.add_argument("--fresh-manifest", type=Path, required=True)
    build.add_argument("--output-root", type=Path, required=True)
    check = commands.add_parser("validate")
    check.add_argument("--adapter", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "stage-inert":
        result = stage_inert(parent_adapter=args.parent_adapter, output_path=args.output)
    elif args.command == "validate-stage":
        result = validate_stage(args.stage)
    elif args.command == "materialize":
        result = materialize(
            parent_adapter=args.parent_adapter,
            fresh_manifest=args.fresh_manifest,
            output_root=args.output_root,
        )
    else:
        result = validate(args.adapter)
    print(json.dumps({key: item for key, item in result.items() if key != "pairs"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


validate_trainer_adapter = validate
