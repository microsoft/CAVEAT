"""Fail-closed step-26 cleanup SFT continuation from the sealed step-25 DCP."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from .amazon_grpo import _link_tree, _require_dcp_inventory, _tree_identity
from .prime_training import PRIME_COMMIT, PRIME_VERSION, materialize_sft_jsonl_to_parquet
from .sol_dagger_collection import (
    ACTION_VALIDATION_SCHEMA,
    RAW_BUNDLE_SCHEMA,
    SOL_MODEL_SPEC,
    SOL_REASONING_EFFORT,
    make_cleanup_semantic_validations,
)
from .sol_dagger_training import (
    SEQ_LEN,
    TEACHER_DESCRIPTOR,
    VARIANTS,
    _adapter_targets,
    _publish,
    _read_json,
    _records,
    _self_hashed,
    _write_new,
    canonical_json,
    sha256_file,
    validate_collection_manifest,
    validate_sol_dagger_plan,
    validate_sol_dagger_receipt,
)

PLAN_SCHEMA = "harness-distill.sol-dagger-cleanup-sft-plan.v1"
RECEIPT_SCHEMA = "harness-distill.sol-dagger-cleanup-sft-receipt.v1"
PARENT_RECEIPT_SHA256 = "a2d303c2b7b9bb456f07e02049cf5b8b3a437a4a4f2f2c611a1e6826421fe4e3"
PARENT_RECEIPT_BODY_SHA256 = "c34a82821f9098dd76dcf2f3d11919fe456d646f2aaea8826a3e08d40347ed9c"
PARENT_ADAPTER_TREE_SHA256 = "6b4f66832c2e11acbdb03413898999143f70bd60d3d020dd27aa7705617dbdba"
PARENT_DCP_TREE_SHA256 = "ff2df73f0948d93f5b51380cc615fd3fcad2d263e3e2661f230e87568fe0ea2c"
PARENT_DCP_FILES = 9
PARENT_DCP_BYTES = 57_547_695_888
ORIGINAL_COLLECTION_SHA256 = "eb4635dfd0254136b74d9c1c199bb81f05f09ff428e1c5c5f0bbad4c7bb5f2c2"
ORIGINAL_COLLECTION_BODY_SHA256 = "9a4755bfae9248e5477bdf869583b70aff76a39bf9fcbb0294c0cec583f98759"
REPAIR_SOURCE_SHA256 = "4d614d80ae4db704b68118da00942b1584805e3e59a4a2cf0e1cee79117adc1c"
CLEANUP_SCHEDULE_SHA256 = "d88d95eec0b32ca4f719ad35a6d729d9949ed4fafb4164a6cfe5672410ea0cff"
CLEANUP_AUDIT_SHA256 = "00214c56800fde0e8052f7e13691760de1ce1dda41933134653554020edd1fd4"
CLEANUP_SIDECARS_SHA256 = "80a520dfaccf14bbc2610358d6e6a261cd39f0640ad37e7472fb9dd81c582bf3"
CLEANUP_RAW_MANIFEST_SHA256 = "caf235cb0798529fe63d20d138e584e5d3a507002d42e1b9a64a2e780ab60801"
CLEANUP_RAW_LABELS_SHA256 = "d8cc6e733a28c25b4185f57e9c310ae9c650b0a03a4c98b03322c88374aaae80"
CLEANUP_VALIDATIONS_SHA256 = "f0c7c01f89152cb182e688db943e13764fe1437fb4389cfcd51fef108d5fd5f1"
COLLECTION_GIT_SHA = "12d755b9e495ab06c9c76223184ecd18ae804c89"

LEARNING_RATE = 2.0e-7
LORA_RANK = 64
LORA_ALPHA = 128.0
CP = 4
DP_SHARDS = 1
SOURCE_STEP = 25
FINAL_STEP = 26
REPAIR_MULTIPLICITY = {
    "cart_cleanup": 7,
    "cart_navigation": 2,
    "checkout": 2,
    "place_order": 1,
}
SOL_RETENTION_PER_VARIANT_PHASE = 2
SOL_RETENTION_PHASES = ("pagination_exploration", "pdp_evidence_selection")
EXPECTED_COUNTS = {
    "repair_cart_cleanup": 28,
    "repair_cart_navigation": 8,
    "repair_clean_checkout": 8,
    "repair_place_order": 4,
    "sol_discovery_retention": 16,
    "sol_focused_cart_cleanup": 3,
}
EXPECTED_TOTAL_ROWS = 67
DIRECT_CLEANUP_MIN = 0.35
DIRECT_CLEANUP_MAX = 0.45
_HEX40 = re.compile(r"[0-9a-f]{40}")


class CleanupTrainingError(RuntimeError):
    """A frozen cleanup source, mixture, parent, or output drifted."""


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise CleanupTrainingError(f"required JSONL is absent or unsafe: {path}")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CleanupTrainingError(f"invalid JSONL line {number}: {path}") from exc
        if not isinstance(row, dict):
            raise CleanupTrainingError(f"JSONL line {number} is not an object: {path}")
        rows.append(row)
    return rows


def _exact_file(path: Path, expected: str, label: str) -> Path:
    path = path.resolve()
    if sha256_file(path) != expected:
        raise CleanupTrainingError(f"{label} bytes differ from the frozen artifact")
    return path


def validate_step25_parent(path: str | Path) -> dict[str, Any]:
    receipt_path = _exact_file(Path(path), PARENT_RECEIPT_SHA256, "step-25 receipt")
    receipt = validate_sol_dagger_receipt(receipt_path)
    candidate = receipt["candidate"]
    dcp = receipt["final_dcp"]
    if (
        receipt.get("receipt_body_sha256") != PARENT_RECEIPT_BODY_SHA256
        or receipt.get("final_step") != SOURCE_STEP
        or receipt.get("optimizer_updates") != 1
        or candidate.get("tree_sha256") != PARENT_ADAPTER_TREE_SHA256
        or dcp.get("tree_sha256") != PARENT_DCP_TREE_SHA256
        or dcp.get("files") != PARENT_DCP_FILES
        or dcp.get("bytes") != PARENT_DCP_BYTES
    ):
        raise CleanupTrainingError("step-25 receipt lineage differs from terminal W4")
    plan = validate_sol_dagger_plan(receipt["plan_path"])
    return {"receipt": receipt, "plan": plan, "receipt_path": str(receipt_path)}


def _validate_cleanup_sources(
    *,
    schedule_path: Path,
    audit_path: Path,
    sidecars_path: Path,
    raw_bundle_path: Path,
    validations_path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    schedule_path = _exact_file(schedule_path, CLEANUP_SCHEDULE_SHA256, "cleanup schedule")
    _exact_file(audit_path, CLEANUP_AUDIT_SHA256, "cleanup schedule audit")
    _exact_file(sidecars_path, CLEANUP_SIDECARS_SHA256, "cleanup sidecars")
    raw_root = raw_bundle_path.resolve()
    manifest_path = _exact_file(
        raw_root / "manifest.json", CLEANUP_RAW_MANIFEST_SHA256, "raw manifest"
    )
    labels_path = _exact_file(
        raw_root / "sol_labels.jsonl", CLEANUP_RAW_LABELS_SHA256, "raw labels"
    )
    validations_path = _exact_file(
        validations_path, CLEANUP_VALIDATIONS_SHA256, "semantic validations"
    )
    manifest = _read_json(manifest_path)
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if (
        manifest.get("schema") != RAW_BUNDLE_SCHEMA
        or manifest.get("manifest_sha256")
        != hashlib.sha256(canonical_json(body).encode()).hexdigest()
        or manifest.get("files", {}).get("sol_labels.jsonl", {}).get("sha256")
        != CLEANUP_RAW_LABELS_SHA256
        or manifest.get("metadata")
        != {
            "teacher_calls": 14,
            "teacher_model_spec": SOL_MODEL_SPEC,
            "reasoning_effort": SOL_REASONING_EFFORT,
        }
    ):
        raise CleanupTrainingError("focused Sol raw bundle manifest drifted")
    schedule = _jsonl(schedule_path)
    labels = _jsonl(labels_path)
    validations = _jsonl(validations_path)
    sidecars = _jsonl(sidecars_path.resolve())
    if not all(len(rows) == 14 for rows in (schedule, labels, validations, sidecars)):
        raise CleanupTrainingError("focused cleanup artifacts must account for all 14 attempts")
    timestamp = validations[0].get("validated_at_unix")
    recomputed = make_cleanup_semantic_validations(
        labels,
        schedule,
        validated_at_unix=timestamp,
        validator_sha256=validations[0].get("validator_sha256"),
    )
    if canonical_json(recomputed) != canonical_json(validations):
        raise CleanupTrainingError("cleanup semantic validations are not reproducible")
    indexed_labels = {row.get("state_id"): row for row in labels}
    accepted = [row for row in validations if row.get("status") == "accepted"]
    rejected = [row for row in validations if row.get("status") == "rejected"]
    if (
        len(indexed_labels) != 14
        or len(accepted) != 3
        or len(rejected) != 11
        or any(row.get("split") != "train" for row in validations)
        or any(row.get("schema") != ACTION_VALIDATION_SCHEMA for row in validations)
        or any(
            row.get("cleanup_semantic_audit", {}).get("semantic_target") != "visible_addon_delete"
            for row in accepted
        )
        or any(
            row.get("cleanup_semantic_audit", {}).get("structural_kind") != "cart_addon"
            for row in accepted
        )
    ):
        raise CleanupTrainingError("focused semantic gate is not exact 3 Delete / 11 rejected")
    focused: list[dict[str, Any]] = []
    for validation in accepted:
        label = indexed_labels.get(validation["state_id"])
        if not isinstance(label, Mapping):
            raise CleanupTrainingError("accepted semantic validation lacks its raw Sol label")
        capture = label.get("capture")
        request = label.get("qwen_request")
        completion = label.get("teacher_completion")
        teacher = label.get("teacher")
        if (
            not all(isinstance(item, Mapping) for item in (capture, request, completion, teacher))
            or teacher.get("provider") != "trapi"
            or teacher.get("logical_model") != "gpt-5.6-sol"
            or teacher.get("model_spec") != "gpt-5.6-sol#low"
            or teacher.get("reasoning_effort") != "low"
            or capture.get("split") != "train"
            or capture.get("phase") != "cart_cleanup_recheck"
            or label.get("teacher_completion_sha256") != validation.get("teacher_completion_sha256")
            or not isinstance(request.get("messages"), list)
        ):
            raise CleanupTrainingError("accepted focused Sol label provenance drifted")
        focused.append(
            {
                "messages": [*deepcopy(request["messages"]), deepcopy(dict(completion))],
                "tools": deepcopy(request.get("tools", [])),
                "metadata": {
                    "mixture_role": "sol_focused_cart_cleanup",
                    "state_id": validation["state_id"],
                    "variant": validation["variant"],
                    "semantic_target": "visible_addon_delete",
                    "source_split": "train",
                },
            }
        )
    audit = {
        "attempts": 14,
        "accepted": 3,
        "rejected": 11,
        "accepted_state_ids": sorted(row["state_id"] for row in accepted),
        "rejected_state_ids": sorted(row["state_id"] for row in rejected),
        "selection_policy": "frozen_cleanup_semantics_v1",
        "reward_or_evaluator_read": False,
        "task_outcome_read": False,
    }
    return focused, validations, audit


def _repair_mixture(path: Path) -> list[dict[str, Any]]:
    _exact_file(path, REPAIR_SOURCE_SHA256, "immutable repair32 source")
    raw = _jsonl(path)
    if len(raw) != 32:
        raise CleanupTrainingError("immutable repair source must contain 32 rows")
    selected = raw[:16]
    observed = Counter(row.get("metadata", {}).get("critical_role") for row in selected)
    if observed != {"cart_navigation": 4, "cart_cleanup": 4, "checkout": 4, "place_order": 4}:
        raise CleanupTrainingError("first 16 repair rows no longer have the four exact real roles")
    output: list[dict[str, Any]] = []
    role_name = {
        "cart_navigation": "repair_cart_navigation",
        "cart_cleanup": "repair_cart_cleanup",
        "checkout": "repair_clean_checkout",
        "place_order": "repair_place_order",
    }
    for source_line, row in enumerate(selected, 1):
        metadata = row["metadata"]
        role = metadata["critical_role"]
        evidence = metadata.get("target_local_evidence", {})
        clicked = " ".join(evidence.get("clicked_labels", []))
        if (
            metadata.get("stage") != "amazon-r00-real-state-repair"
            or metadata.get("target_turn_sequence") != evidence.get("sequence")
            or (role == "cart_cleanup" and "Delete" not in clicked)
            or (role == "cart_navigation" and "Cart" not in clicked)
            or (
                role == "checkout"
                and (
                    "Proceed to checkout" not in clicked
                    or evidence.get("clean_cart_state", {}).get("known_extras_absent") is not True
                )
            )
            or (role == "place_order" and "Place your order" not in clicked)
        ):
            raise CleanupTrainingError(f"repair row {source_line} action semantics drifted")
        base = {key: deepcopy(row[key]) for key in ("messages", "tools") if key in row}
        for replica in range(REPAIR_MULTIPLICITY[role]):
            output.append(
                {
                    **base,
                    "metadata": {
                        "mixture_role": role_name[role],
                        "repair_source_line": source_line,
                        "replica": replica + 1,
                        "executed_repair": True,
                    },
                }
            )
    return output


def _sol_retention(collection: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = _records(
        Path(collection["records_path"]),
        int(collection["row_count"]),
        expected_teacher=TEACHER_DESCRIPTOR,
        required_kinds={"corrective", "retention"},
    )
    heldout = _records(
        Path(collection["heldout_path"]),
        int(collection["heldout_count"]),
        expected_teacher=TEACHER_DESCRIPTOR,
        required_kinds={"corrective"},
    )
    heldout_signatures = {
        hashlib.sha256(
            canonical_json({k: row.get(k) for k in ("messages", "tools")}).encode()
        ).hexdigest()
        for row in heldout
    }
    selected: list[dict[str, Any]] = []
    for variant in VARIANTS:
        for phase in SOL_RETENTION_PHASES:
            candidates = [
                row
                for row in rows
                if row["metadata"].get("kind") == "corrective"
                and row["metadata"].get("split") == "train"
                and row["metadata"].get("source_variant") == variant
                and row["metadata"].get("phase") == phase
            ]
            candidates.sort(key=lambda row: row["metadata"]["source_line"])
            if len(candidates) < SOL_RETENTION_PER_VARIANT_PHASE:
                raise CleanupTrainingError("original Sol train corpus lacks balanced retention")
            for row in candidates[:SOL_RETENTION_PER_VARIANT_PHASE]:
                signature = hashlib.sha256(
                    canonical_json({k: row.get(k) for k in ("messages", "tools")}).encode()
                ).hexdigest()
                if signature in heldout_signatures:
                    raise CleanupTrainingError("held-out Sol row entered prefix retention")
                selected.append(
                    {
                        **{key: deepcopy(row[key]) for key in ("messages", "tools") if key in row},
                        "metadata": {
                            "mixture_role": "sol_discovery_retention",
                            "variant": variant,
                            "phase": phase,
                            "source_line": row["metadata"]["source_line"],
                            "source_split": "train",
                        },
                    }
                )
    return selected


def _render_config(
    *,
    parent_model: Path,
    dataset: Path,
    output: Path,
    targets: Sequence[str],
    renderer: str,
    batch_size: int,
) -> bytes:
    array = "[" + ", ".join(json.dumps(item) for item in targets) + "]"
    return (
        f"""# PRIME-RL {PRIME_VERSION} ({PRIME_COMMIT}); focused step-26 cleanup SFT
max_steps = 26
output_dir = {json.dumps(str(output))}
clean_output_dir = false
matmul_precision = "high"
loss_impl = "liger_fused"

[env_vars]
FLA_TILELANG = "0"
WANDB_MODE = "disabled"

[deployment]
type = "single_node"
num_gpus = 4
gpus_per_node = 4

[model]
name = {json.dumps(str(parent_model))}
seq_len = {SEQ_LEN}
impl = "hf"
attn = "flash_attention_2"
optimization_dtype = "bfloat16"
reduce_dtype = "bfloat16"
cp = 4
cp_style = "ulysses"

[model.ac]
mode = "full"
freq = 1

[model.lora]
rank = 64
alpha = 128.0
dropout = 0.0
target_modules = {array}
modules_to_save = []

[renderer]
name = {json.dumps(renderer)}
enable_thinking = true
"""
        + ("preserve_thinking = true\n" if renderer == "qwen3.6" else "")
        + f"""
[data]
type = "sft"
name = {json.dumps(str(dataset))}
batch_size = {batch_size}
seq_len = {SEQ_LEN}
micro_batch_size = 1
pack_function = "cat"
shuffle = false
seed = 56036026

[data.loss_mask]
system = false
user = false
assistant = true
tool = false

[optim]
type = "adamw"
lr = 2e-07
weight_decay = 0.01
max_norm = 1.0

[scheduler]
type = "constant"

[ckpt]
interval = 1
resume_step = 25
keep_last = 2
skip_optimizer = true
skip_scheduler = true
skip_dataloader = true
skip_progress = false

[ckpt.weights]
save_sharded = true
save_format = "safetensors"
save_adapter_separately = true
"""
    ).encode()


def _validate_config(
    config: Mapping[str, Any],
    *,
    parent_model: Path,
    dataset: Path,
    output: Path,
    targets: Sequence[str],
) -> None:
    expected = tomllib.loads(
        _render_config(
            parent_model=parent_model,
            dataset=dataset,
            output=output,
            targets=targets,
            renderer="qwen3.5",
            batch_size=EXPECTED_TOTAL_ROWS,
        ).decode()
    )
    if config != expected or (
        config.get("max_steps") != 26
        or Path(str(config.get("output_dir", ""))).resolve() != output.resolve()
        or config.get("clean_output_dir") is not False
        or config.get("deployment") != {"type": "single_node", "num_gpus": 4, "gpus_per_node": 4}
        or Path(str(config.get("model", {}).get("name", ""))).resolve() != parent_model.resolve()
        or config.get("model", {}).get("seq_len") != SEQ_LEN
        or config.get("model", {}).get("impl") != "hf"
        or config.get("model", {}).get("attn") != "flash_attention_2"
        or config.get("model", {}).get("cp") != CP
        or config.get("model", {}).get("cp_style") != "ulysses"
        or config.get("model", {}).get("lora")
        != {
            "rank": LORA_RANK,
            "alpha": LORA_ALPHA,
            "dropout": 0.0,
            "target_modules": list(targets),
            "modules_to_save": [],
        }
        or Path(str(config.get("data", {}).get("name", ""))).resolve() != dataset.resolve()
        or {
            key: config.get("data", {}).get(key)
            for key in (
                "type",
                "batch_size",
                "seq_len",
                "micro_batch_size",
                "pack_function",
                "shuffle",
                "seed",
            )
        }
        != {
            "type": "sft",
            "batch_size": EXPECTED_TOTAL_ROWS,
            "seq_len": SEQ_LEN,
            "micro_batch_size": 1,
            "pack_function": "cat",
            "shuffle": False,
            "seed": 56036026,
        }
        or config.get("data", {}).get("loss_mask")
        != {"system": False, "user": False, "assistant": True, "tool": False}
        or config.get("optim")
        != {"type": "adamw", "lr": LEARNING_RATE, "weight_decay": 0.01, "max_norm": 1.0}
        or config.get("scheduler") != {"type": "constant"}
        or config.get("ckpt", {}).get("interval") != 1
        or config.get("ckpt", {}).get("resume_step") != SOURCE_STEP
        or config.get("ckpt", {}).get("keep_last") != 2
        or config.get("ckpt", {}).get("skip_optimizer") is not True
        or config.get("ckpt", {}).get("skip_scheduler") is not True
        or config.get("ckpt", {}).get("skip_dataloader") is not True
        or config.get("ckpt", {}).get("skip_progress") is not False
        or config.get("ckpt", {}).get("weights")
        != {
            "save_sharded": True,
            "save_format": "safetensors",
            "save_adapter_separately": True,
        }
    ):
        raise CleanupTrainingError("generated step-26 config policy drifted")


def _mass(rows: Sequence[dict[str, Any]], token_audit: Mapping[str, Any]) -> dict[str, Any]:
    audited = token_audit.get("rows")
    if not isinstance(audited, list) or len(audited) != len(rows):
        raise CleanupTrainingError("rendered-token audit does not cover the fixed mixture")
    by_role: Counter[str] = Counter()
    for row, item in zip(rows, audited):
        tokens = item.get("trainable_tokens") if isinstance(item, Mapping) else None
        if type(tokens) is not int or tokens < 1:
            raise CleanupTrainingError("mixture row has no positive assistant-token mass")
        by_role[row["metadata"]["mixture_role"]] += tokens
    total = sum(by_role.values())
    # Only replay-executed repair rows count as direct normalization.  The
    # focused Sol Deletes are statically grounded but were not browser-run.
    direct = by_role["repair_cart_cleanup"]
    fraction = direct / total
    if not DIRECT_CLEANUP_MIN <= fraction <= DIRECT_CLEANUP_MAX:
        raise CleanupTrainingError(
            f"direct dirty-cart active-token fraction {fraction:.6f} is outside [0.35, 0.45]"
        )
    return {
        "by_role": dict(sorted(by_role.items())),
        "total": total,
        "direct_dirty_cart": direct,
        "direct_dirty_cart_fraction": fraction,
        "gate": {"minimum": DIRECT_CLEANUP_MIN, "maximum": DIRECT_CLEANUP_MAX},
    }


def prepare_cleanup_training(
    *,
    parent_receipt_path: str | Path,
    cleanup_schedule_path: str | Path,
    cleanup_audit_path: str | Path,
    cleanup_sidecars_path: str | Path,
    cleanup_raw_bundle_path: str | Path,
    cleanup_validations_path: str | Path,
    smoke_report_path: str | Path,
    output_dir: str | Path,
    prime_root: str | Path = "/opt/prime-rl",
) -> dict[str, Any]:
    output = Path(output_dir).resolve()
    if output.exists() or output.is_symlink():
        raise CleanupTrainingError("cleanup training output must be a new directory")
    parent = validate_step25_parent(parent_receipt_path)
    original_path = Path(parent["plan"]["collection_manifest_path"])
    if sha256_file(original_path) != ORIGINAL_COLLECTION_SHA256:
        raise CleanupTrainingError("step-25 original collection file drifted")
    collection = validate_collection_manifest(original_path)
    if collection["manifest_body_sha256"] != ORIGINAL_COLLECTION_BODY_SHA256:
        raise CleanupTrainingError("step-25 original collection body drifted")
    focused, validations, semantic_audit = _validate_cleanup_sources(
        schedule_path=Path(cleanup_schedule_path),
        audit_path=Path(cleanup_audit_path),
        sidecars_path=Path(cleanup_sidecars_path),
        raw_bundle_path=Path(cleanup_raw_bundle_path),
        validations_path=Path(cleanup_validations_path),
    )
    rows = [
        *_repair_mixture(Path(collection["retention_source_path"])),
        *_sol_retention(collection),
        *focused,
    ]
    counts = Counter(row["metadata"]["mixture_role"] for row in rows)
    if dict(counts) != EXPECTED_COUNTS or len(rows) != EXPECTED_TOTAL_ROWS:
        raise CleanupTrainingError(f"fixed cleanup mixture drifted: {dict(counts)}")
    output.mkdir(parents=True)
    source = output / "materialized/train.jsonl"
    _write_new(source, b"".join((canonical_json(row) + "\n").encode() for row in rows))
    prime = materialize_sft_jsonl_to_parquet(
        source,
        output / "prime",
        smoke_report=smoke_report_path,
        seq_len=SEQ_LEN,
        required_seq_len=SEQ_LEN,
        global_batch_size=len(rows),
        prime_root=prime_root,
    )
    if prime.optimizer_updates != 1 or prime.prime_max_steps != 1:
        raise CleanupTrainingError("focused cleanup mixture must be exactly one update")
    mass = _mass(rows, _read_json(prime.manifest_path).get("token_audit", {}))
    semantic_path = output / "materialized/semantic_gate_audit.json"
    _write_new(
        semantic_path,
        (
            canonical_json(
                {
                    **semantic_audit,
                    "validation_rows_sha256": hashlib.sha256(
                        canonical_json(validations).encode()
                    ).hexdigest(),
                }
            )
            + "\n"
        ).encode(),
    )
    candidate = parent["receipt"]["candidate"]
    targets = _adapter_targets(Path(candidate["path"]))
    training_output = output / "training/prime_output"
    bridge = _link_tree(
        Path(parent["receipt"]["final_dcp"]["path"]),
        training_output / "checkpoints/step_25/trainer",
    )
    config_path = output / "training/sol_dagger_cleanup_sft.toml"
    _write_new(
        config_path,
        _render_config(
            parent_model=Path(parent["plan"]["parent_model"]),
            dataset=prime.manifest_path.parent,
            output=training_output,
            targets=targets,
            renderer=prime.renderer,
            batch_size=len(rows),
        ),
    )
    _validate_config(
        tomllib.loads(config_path.read_text(encoding="utf-8")),
        parent_model=Path(parent["plan"]["parent_model"]),
        dataset=prime.manifest_path.parent,
        output=training_output,
        targets=targets,
    )
    body = {
        "schema": PLAN_SCHEMA,
        "status": "prepared",
        "scientific_label": "prospective_semantic_gate_plus_executed_cleanup_normalization",
        "prime_version": PRIME_VERSION,
        "prime_commit": PRIME_COMMIT,
        "collection_git_sha": COLLECTION_GIT_SHA,
        "parent_receipt_path": parent["receipt_path"],
        "parent_receipt_sha256": PARENT_RECEIPT_SHA256,
        "parent_receipt_body_sha256": PARENT_RECEIPT_BODY_SHA256,
        "parent_model": parent["plan"]["parent_model"],
        "source_dcp": parent["receipt"]["final_dcp"],
        "checkpoint_bridge": bridge,
        "original_collection_path": str(original_path.resolve()),
        "original_collection_sha256": ORIGINAL_COLLECTION_SHA256,
        "cleanup_sources": {
            "schedule": {
                "path": str(Path(cleanup_schedule_path).resolve()),
                "sha256": CLEANUP_SCHEDULE_SHA256,
            },
            "audit": {
                "path": str(Path(cleanup_audit_path).resolve()),
                "sha256": CLEANUP_AUDIT_SHA256,
            },
            "sidecars": {
                "path": str(Path(cleanup_sidecars_path).resolve()),
                "sha256": CLEANUP_SIDECARS_SHA256,
            },
            "raw_manifest": {
                "path": str((Path(cleanup_raw_bundle_path) / "manifest.json").resolve()),
                "sha256": CLEANUP_RAW_MANIFEST_SHA256,
            },
            "raw_labels": {
                "path": str((Path(cleanup_raw_bundle_path) / "sol_labels.jsonl").resolve()),
                "sha256": CLEANUP_RAW_LABELS_SHA256,
            },
            "validations": {
                "path": str(Path(cleanup_validations_path).resolve()),
                "sha256": CLEANUP_VALIDATIONS_SHA256,
            },
        },
        "semantic_gate_audit_path": str(semantic_path),
        "semantic_gate_audit_sha256": sha256_file(semantic_path),
        "source_jsonl": str(source),
        "source_jsonl_sha256": sha256_file(source),
        "prime_manifest_path": str(prime.manifest_path),
        "prime_manifest_sha256": sha256_file(prime.manifest_path),
        "prime_parquet_path": str(prime.train_parquet),
        "prime_parquet_sha256": sha256_file(prime.train_parquet),
        "config_path": str(config_path),
        "config_sha256": sha256_file(config_path),
        "training_output": str(training_output),
        "candidate_path": str(training_output / "weights/step_26/lora_adapters"),
        "mixture_counts": dict(sorted(counts.items())),
        "row_count": len(rows),
        "active_token_loss_mass": mass,
        "assistant_tokens_only": True,
        "evaluation_rows": 0,
        "heldout_rows": 0,
        "topology": {"dp_shards": 1, "cp": 4, "gpus": 4},
        "lora": {"rank": 64, "alpha": 128.0},
        "source_step": 25,
        "final_step": 26,
        "optimizer_updates": 1,
        "learning_rate": LEARNING_RATE,
        "fresh_optimizer": True,
        "optimizer_continuation": False,
        "launch_authorized": True,
    }
    return _publish(output / "training/plan.json", body, hash_field="plan_body_sha256")


def validate_cleanup_plan(path: str | Path) -> dict[str, Any]:
    plan_path = Path(path).resolve()
    plan = _self_hashed(plan_path, schema=PLAN_SCHEMA, hash_field="plan_body_sha256")
    if (
        plan.get("status") != "prepared"
        or plan.get("scientific_label")
        != "prospective_semantic_gate_plus_executed_cleanup_normalization"
        or plan.get("prime_version") != PRIME_VERSION
        or plan.get("prime_commit") != PRIME_COMMIT
        or plan.get("collection_git_sha") != COLLECTION_GIT_SHA
        or plan.get("mixture_counts") != EXPECTED_COUNTS
        or plan.get("row_count") != 67
        or plan.get("assistant_tokens_only") is not True
        or plan.get("evaluation_rows") != 0
        or plan.get("heldout_rows") != 0
        or plan.get("topology") != {"dp_shards": 1, "cp": 4, "gpus": 4}
        or plan.get("lora") != {"rank": LORA_RANK, "alpha": LORA_ALPHA}
        or plan.get("source_step") != 25
        or plan.get("final_step") != 26
        or plan.get("optimizer_updates") != 1
        or plan.get("learning_rate") != LEARNING_RATE
        or plan.get("fresh_optimizer") is not True
        or plan.get("optimizer_continuation") is not False
        or plan.get("launch_authorized") is not True
    ):
        raise CleanupTrainingError("step-26 cleanup plan policy drifted")
    mass = plan.get("active_token_loss_mass", {})
    fraction = mass.get("direct_dirty_cart_fraction") if isinstance(mass, Mapping) else None
    if (
        not isinstance(fraction, (int, float))
        or not DIRECT_CLEANUP_MIN <= fraction <= DIRECT_CLEANUP_MAX
    ):
        raise CleanupTrainingError("step-26 plan active-token gate drifted")
    artifacts = [
        (plan["parent_receipt_path"], PARENT_RECEIPT_SHA256),
        (plan["original_collection_path"], ORIGINAL_COLLECTION_SHA256),
        (plan["semantic_gate_audit_path"], plan["semantic_gate_audit_sha256"]),
        (plan["source_jsonl"], plan["source_jsonl_sha256"]),
        (plan["prime_manifest_path"], plan["prime_manifest_sha256"]),
        (plan["prime_parquet_path"], plan["prime_parquet_sha256"]),
        (plan["config_path"], plan["config_sha256"]),
    ]
    artifacts.extend((item["path"], item["sha256"]) for item in plan["cleanup_sources"].values())
    if any(sha256_file(Path(raw)) != expected for raw, expected in artifacts):
        raise CleanupTrainingError("step-26 plan-bound artifact changed")
    parent = validate_step25_parent(plan["parent_receipt_path"])
    if (
        plan.get("parent_receipt_body_sha256") != PARENT_RECEIPT_BODY_SHA256
        or plan.get("parent_model") != parent["plan"]["parent_model"]
        or plan["source_dcp"] != parent["receipt"]["final_dcp"]
        or Path(plan["candidate_path"]).resolve()
        != (Path(plan["training_output"]) / "weights/step_26/lora_adapters").resolve()
    ):
        raise CleanupTrainingError("step-26 parent/candidate lineage differs from W4")

    sources = plan.get("cleanup_sources")
    if not isinstance(sources, Mapping) or set(sources) != {
        "schedule",
        "audit",
        "sidecars",
        "raw_manifest",
        "raw_labels",
        "validations",
    }:
        raise CleanupTrainingError("step-26 cleanup source inventory drifted")
    focused, validations, semantic_audit = _validate_cleanup_sources(
        schedule_path=Path(sources["schedule"]["path"]),
        audit_path=Path(sources["audit"]["path"]),
        sidecars_path=Path(sources["sidecars"]["path"]),
        raw_bundle_path=Path(sources["raw_manifest"]["path"]).parent,
        validations_path=Path(sources["validations"]["path"]),
    )
    collection = validate_collection_manifest(plan["original_collection_path"])
    rows = [
        *_repair_mixture(Path(collection["retention_source_path"])),
        *_sol_retention(collection),
        *focused,
    ]
    expected_source = b"".join((canonical_json(row) + "\n").encode() for row in rows)
    source_path = Path(plan["source_jsonl"])
    if source_path.read_bytes() != expected_source:
        raise CleanupTrainingError("materialized source is not the reconstructed fixed mixture")
    counts = Counter(row["metadata"]["mixture_role"] for row in rows)
    if dict(counts) != EXPECTED_COUNTS or len(rows) != EXPECTED_TOTAL_ROWS:
        raise CleanupTrainingError("reconstructed fixed mixture counts drifted")
    expected_semantic = {
        **semantic_audit,
        "validation_rows_sha256": hashlib.sha256(canonical_json(validations).encode()).hexdigest(),
    }
    if _read_json(Path(plan["semantic_gate_audit_path"])) != expected_semantic:
        raise CleanupTrainingError("semantic gate accounting differs from the exact 14 attempts")
    prime_manifest = _read_json(Path(plan["prime_manifest_path"]))
    if (
        prime_manifest.get("row_count") != EXPECTED_TOTAL_ROWS
        or prime_manifest.get("source_sha256") != plan["source_jsonl_sha256"]
        or _mass(rows, prime_manifest.get("token_audit", {})) != plan["active_token_loss_mass"]
    ):
        raise CleanupTrainingError("PRIME manifest/token mass differs from the fixed mixture")
    targets = _adapter_targets(Path(parent["receipt"]["candidate"]["path"]))
    _validate_config(
        tomllib.loads(Path(plan["config_path"]).read_text(encoding="utf-8")),
        parent_model=Path(plan["parent_model"]),
        dataset=Path(plan["prime_manifest_path"]).parent,
        output=Path(plan["training_output"]),
        targets=targets,
    )
    source = _require_dcp_inventory(
        Path(plan["source_dcp"]["path"]), expected_identity=plan["source_dcp"], trainer_world_size=4
    )
    bridge = _require_dcp_inventory(
        Path(plan["checkpoint_bridge"]["destination"]["path"]),
        expected_identity=plan["checkpoint_bridge"]["destination"],
        trainer_world_size=4,
    )
    if any(source[key] != bridge[key] for key in ("files", "bytes", "tree_sha256")):
        raise CleanupTrainingError("step-26 DCP bridge differs from W4")
    return plan


def _trainer_log(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise CleanupTrainingError("trainer log is absent or unsafe")
    text = path.read_text(encoding="utf-8", errors="replace")
    required = (
        "Starting from step 26",
        "Step 26 |",
        "Writing final checkpoint",
        "Writing final weight checkpoint",
        "SFT trainer finished!",
    )
    forbidden = ("Step 27 |", "Traceback (most recent call last)", "RuntimeError:")
    if (
        any(item not in text for item in required)
        or any(item in text for item in forbidden)
        or text.count("Step 26 |") != 1
    ):
        raise CleanupTrainingError("trainer log does not prove one clean step 26")
    return {
        "source_step": 25,
        "final_step": 26,
        "step26_records": 1,
        "trainer_finished": True,
        "tracebacks": 0,
    }


def write_cleanup_receipt(*, plan_path: str | Path, executor_git_sha: str) -> dict[str, Any]:
    if _HEX40.fullmatch(executor_git_sha) is None:
        raise CleanupTrainingError("executor git SHA must be 40 lowercase hex characters")
    plan_path = Path(plan_path).resolve()
    plan = validate_cleanup_plan(plan_path)
    output = Path(plan["training_output"])
    candidate = Path(plan["candidate_path"])
    identity = _tree_identity(candidate)
    stable = candidate.parent / "STABLE"
    if (
        not stable.is_file()
        or stable.is_symlink()
        or (output / "weights/step_27").exists()
        or (output / "checkpoints/step_27").exists()
    ):
        raise CleanupTrainingError("step-26 adapter is unstable or an extra update exists")
    config = tomllib.loads(Path(plan["config_path"]).read_text(encoding="utf-8"))
    if _adapter_targets(candidate) != sorted(config["model"]["lora"]["target_modules"]):
        raise CleanupTrainingError("step-26 adapter targets differ from the exact config")
    final_dcp = _require_dcp_inventory(output / "checkpoints/step_26/trainer", trainer_world_size=4)
    log = output / "logs/trainer.log"
    log_audit = _trainer_log(log)
    body = {
        "schema": RECEIPT_SCHEMA,
        "status": "ok",
        "executor_git_sha": executor_git_sha,
        "plan_path": str(plan_path),
        "plan_sha256": sha256_file(plan_path),
        "plan_body_sha256": plan["plan_body_sha256"],
        "candidate": {
            "name": "step26-sol-dagger-cleanup-sft",
            "update": 26,
            **identity,
            "adapter_config_sha256": sha256_file(candidate / "adapter_config.json"),
            "stable_marker_sha256": sha256_file(stable),
        },
        "parent_candidate": "step25-sol-dagger-sft",
        "source_step": 25,
        "final_step": 26,
        "optimizer_updates": 1,
        "fresh_optimizer": True,
        "optimizer_continuation": False,
        "source_dcp": plan["source_dcp"],
        "final_dcp": final_dcp,
        "active_token_loss_mass": plan["active_token_loss_mass"],
        "config_path": plan["config_path"],
        "config_sha256": plan["config_sha256"],
        "trainer_log_audit": log_audit,
        "logs": {
            "trainer": {"path": str(log), "bytes": log.stat().st_size, "sha256": sha256_file(log)}
        },
    }
    return _publish(
        plan_path.with_name("training_receipt.json"), body, hash_field="receipt_body_sha256"
    )


def validate_cleanup_receipt(path: str | Path) -> dict[str, Any]:
    receipt_path = Path(path).resolve()
    receipt = _self_hashed(receipt_path, schema=RECEIPT_SCHEMA, hash_field="receipt_body_sha256")
    if (
        receipt.get("status") != "ok"
        or _HEX40.fullmatch(str(receipt.get("executor_git_sha"))) is None
        or receipt.get("parent_candidate") != "step25-sol-dagger-sft"
        or receipt.get("source_step") != 25
        or receipt.get("final_step") != 26
        or receipt.get("optimizer_updates") != 1
        or receipt.get("fresh_optimizer") is not True
        or receipt.get("optimizer_continuation") is not False
    ):
        raise CleanupTrainingError("step-26 cleanup receipt policy drifted")
    plan_path = Path(receipt["plan_path"])
    if sha256_file(plan_path) != receipt.get("plan_sha256"):
        raise CleanupTrainingError("step-26 receipt plan bytes changed")
    plan = validate_cleanup_plan(plan_path)
    if plan["plan_body_sha256"] != receipt.get("plan_body_sha256"):
        raise CleanupTrainingError("step-26 receipt plan body changed")
    candidate = receipt.get("candidate")
    if (
        not isinstance(candidate, Mapping)
        or candidate.get("name") != "step26-sol-dagger-cleanup-sft"
        or candidate.get("update") != FINAL_STEP
        or Path(str(candidate.get("path", ""))).resolve() != Path(plan["candidate_path"]).resolve()
        or receipt.get("source_dcp") != plan["source_dcp"]
        or receipt.get("active_token_loss_mass") != plan["active_token_loss_mass"]
        or receipt.get("config_path") != plan["config_path"]
        or receipt.get("config_sha256") != plan["config_sha256"]
    ):
        raise CleanupTrainingError("step-26 receipt candidate/config lineage drifted")
    output = Path(plan["training_output"])
    if (output / "weights/step_27").exists() or (output / "checkpoints/step_27").exists():
        raise CleanupTrainingError("step-26 receipt has an unauthorized extra update")
    candidate_path = Path(candidate["path"])
    identity = _tree_identity(candidate_path)
    if any(identity[key] != candidate.get(key) for key in ("files", "bytes", "tree_sha256")):
        raise CleanupTrainingError("step-26 receipt adapter changed")
    stable = candidate_path.parent / "STABLE"
    config = tomllib.loads(Path(plan["config_path"]).read_text(encoding="utf-8"))
    if (
        sha256_file(candidate_path / "adapter_config.json")
        != candidate.get("adapter_config_sha256")
        or not stable.is_file()
        or stable.is_symlink()
        or sha256_file(stable) != candidate.get("stable_marker_sha256")
        or _adapter_targets(candidate_path) != sorted(config["model"]["lora"]["target_modules"])
    ):
        raise CleanupTrainingError("step-26 receipt adapter configuration drifted")
    expected_final_dcp = output / "checkpoints/step_26/trainer"
    if Path(receipt["final_dcp"]["path"]).resolve() != expected_final_dcp.resolve():
        raise CleanupTrainingError("step-26 receipt final DCP path drifted")
    _require_dcp_inventory(
        Path(receipt["final_dcp"]["path"]),
        expected_identity=receipt["final_dcp"],
        trainer_world_size=4,
    )
    logs = receipt.get("logs")
    descriptor = logs.get("trainer") if isinstance(logs, Mapping) else None
    expected_log = output / "logs/trainer.log"
    if (
        not isinstance(descriptor, Mapping)
        or Path(str(descriptor.get("path", ""))).resolve() != expected_log.resolve()
        or descriptor.get("bytes") != expected_log.stat().st_size
        or descriptor.get("sha256") != sha256_file(expected_log)
        or receipt.get("trainer_log_audit") != _trainer_log(expected_log)
    ):
        raise CleanupTrainingError("step-26 receipt trainer evidence changed")
    return receipt
