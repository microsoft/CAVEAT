"""Fail-closed one-update Sol-to-Qwen DAgger SFT preparation.

This is deliberately a standalone sidecar: it consumes a verified collection
and the completed r00 repair step-24 receipt, but never changes the browser
harness or evaluator.  The collection supplies raw Sol completions; this
module builds assistant-only Qwen chat rows, verifies their *rendered* active
token mass, and produces a one-update continuation from the step-24 DCP.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .amazon_grpo import (
    _adapter_targets as _prime_adapter_targets,
)
from .amazon_grpo import (
    _link_tree,
    _require_dcp_inventory,
    _tree_identity,
)
from .prime_training import PRIME_COMMIT, PRIME_VERSION, materialize_sft_jsonl_to_parquet

COLLECTION_SCHEMA = "harness-distill.sol-dagger-collection-manifest.v1"
PLAN_SCHEMA = "harness-distill.sol-dagger-sft-plan.v1"
RECEIPT_SCHEMA = "harness-distill.sol-dagger-sft-receipt.v1"
PARENT_CANDIDATE = "step24-amazon-r00-repair-sft"
SOL_MODEL = "gpt-5.6-sol"
SEQ_LEN = 32_768
LEARNING_RATE = 5.0e-7
LORA_RANK = 64
LORA_ALPHA = 128.0
NUM_GPUS = 4
CONTEXT_PARALLEL_SIZE = 4
DATA_PARALLEL_SHARDS = 1
GLOBAL_BATCH_SIZE = 96
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
TEACHER_DESCRIPTOR = {
    "model": SOL_MODEL,
    "effort": "low",
    "provider": "trapi",
    "model_spec": "gpt-5.6-sol#low",
}
PROVIDER_ONLY_DIFFERENCES = ["model", "reasoning_effort", "max_completion_tokens", "n", "stream"]
VARIANTS = ("graded", "graded3", "graded4", "mixed")
PHASES = (
    "constraints_query",
    "pagination_exploration",
    "pdp_evidence_selection",
    "checkpoint_grounding",
    "cart_cleanup_recheck",
    "checkout_order",
)
MIN_TRAIN_CORRECTIVE = 64
TARGET_TRAIN_CORRECTIVE = 64
MIN_HELDOUT = 24
TARGET_HELDOUT = 24
IMMUTABLE_REPAIR_ROWS = 32


class SolDaggerTrainingError(RuntimeError):
    """A collection, parent, generated plan, or training output drifted."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise SolDaggerTrainingError(f"required JSON is absent or a symlink: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SolDaggerTrainingError(f"invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise SolDaggerTrainingError(f"JSON must be an object: {path}")
    return value


def _self_hashed(path: Path, *, schema: str, hash_field: str) -> dict[str, Any]:
    value = _read_json(path)
    body = {key: item for key, item in value.items() if key != hash_field}
    if (
        value.get("schema") != schema
        or value.get(hash_field) != hashlib.sha256(canonical_json(body).encode()).hexdigest()
    ):
        raise SolDaggerTrainingError(f"self-hashed {schema} bytes drifted: {path}")
    return value


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise SolDaggerTrainingError(f"create-only target already exists: {path}")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        descriptor = os.open(path, flags, 0o644)
    except FileExistsError as exc:  # pragma: no cover - race protection
        raise SolDaggerTrainingError(f"create-only target already exists: {path}") from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _publish(path: Path, body: Mapping[str, Any], *, hash_field: str) -> dict[str, Any]:
    value = dict(body)
    value[hash_field] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    _write_new(path, (canonical_json(value) + "\n").encode())
    return value


def _adapter_targets(adapter: Path) -> list[str]:
    try:
        return sorted(_prime_adapter_targets(adapter))
    except Exception as exc:
        raise SolDaggerTrainingError("adapter has no valid concrete target_modules list") from exc


def validate_repair_step24_parent(path: str | Path) -> dict[str, Any]:
    """Validate the immutable repair SFT receipt used as the DAgger parent."""

    receipt_path = Path(path).resolve()
    value = _self_hashed(
        receipt_path,
        schema="harness-distill.amazon-r00-repair-sft-training-receipt.v1",
        hash_field="receipt_body_sha256",
    )
    candidate = value.get("candidate")
    if (
        value.get("status") != "ok"
        or value.get("final_step") != 24
        or value.get("optimizer_updates") != 1
        or not isinstance(candidate, Mapping)
        or candidate.get("name") != PARENT_CANDIDATE
        or candidate.get("update") != 24
    ):
        raise SolDaggerTrainingError("parent is not the completed repair step-24 SFT receipt")
    adapter = Path(str(candidate.get("path", ""))).resolve()
    identity = _tree_identity(adapter)
    if any(identity[key] != candidate.get(key) for key in ("files", "bytes", "tree_sha256")):
        raise SolDaggerTrainingError("repair step-24 adapter tree changed after receipt")
    if sha256_file(adapter / "adapter_config.json") != candidate.get("adapter_config_sha256"):
        raise SolDaggerTrainingError("repair step-24 adapter configuration changed")
    plan_path = Path(str(value.get("plan_path", ""))).resolve()
    if sha256_file(plan_path) != value.get("plan_file_sha256"):
        raise SolDaggerTrainingError("repair step-24 receipt plan bytes changed")
    repair_plan = _self_hashed(
        plan_path,
        schema="harness-distill.amazon-r00-repair-sft-plan.v1",
        hash_field="plan_body_sha256",
    )
    if repair_plan.get("plan_body_sha256") != value.get("plan_body_sha256"):
        raise SolDaggerTrainingError("repair step-24 receipt plan body binding drifted")
    repair_config_path = Path(str(repair_plan.get("config_path", ""))).resolve()
    if sha256_file(repair_config_path) != repair_plan.get("config_sha256") or sha256_file(
        repair_config_path
    ) != value.get("config_sha256"):
        raise SolDaggerTrainingError("repair step-24 config bytes changed")
    try:
        repair_config = tomllib.loads(repair_config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SolDaggerTrainingError("repair step-24 config is invalid") from exc
    parent_model_raw = repair_plan.get("parent_model")
    parent_model = Path(str(parent_model_raw or "")).resolve()
    if not parent_model.is_dir() or parent_model.is_symlink():
        raise SolDaggerTrainingError("repair step-24 plan has no safe selected/merged parent model")
    if (
        not parent_model_raw
        or Path(str(repair_config.get("model", {}).get("name", ""))).resolve() != parent_model
    ):
        raise SolDaggerTrainingError("repair step-24 plan/config parent model binding drifted")
    final_dcp = value.get("final_dcp")
    if not isinstance(final_dcp, Mapping):
        raise SolDaggerTrainingError("repair step-24 receipt lacks its final trainer DCP")
    final_dcp_path = Path(str(final_dcp.get("path", ""))).resolve()
    try:
        final_dcp_identity = _require_dcp_inventory(
            final_dcp_path, expected_identity=final_dcp, trainer_world_size=NUM_GPUS
        )
    except Exception as exc:
        raise SolDaggerTrainingError("repair step-24 final trainer DCP drifted") from exc
    return {
        **value,
        "receipt_path": str(receipt_path),
        "receipt_sha256": sha256_file(receipt_path),
        "parent_model": str(parent_model),
        "parent_model_path": str(parent_model),
        "repair_plan_path": str(plan_path),
        "repair_plan_sha256": sha256_file(plan_path),
        "repair_plan_body_sha256": repair_plan["plan_body_sha256"],
        "final_dcp_path": str(final_dcp_path),
        "final_dcp_identity": final_dcp_identity,
    }


def validate_collection_manifest(path: str | Path) -> dict[str, Any]:
    """Validate a verified collection before exposing any row to training."""

    manifest_path = Path(path).resolve()
    value = _self_hashed(manifest_path, schema=COLLECTION_SCHEMA, hash_field="manifest_body_sha256")
    records = Path(str(value.get("records_path", ""))).resolve()
    teacher = value.get("teacher")
    invariance = value.get("invariance")
    if (
        value.get("status") != "verified"
        or value.get("teacher_model") != SOL_MODEL
        or value.get("teacher_reasoning_effort") != "low"
        or teacher != TEACHER_DESCRIPTOR
        or value.get("assistant_tokens_only") is not True
        or value.get("qwen_rendering") != {"assistant_only": True, "seq_len": SEQ_LEN}
        or not records.is_file()
        or records.is_symlink()
        or sha256_file(records) != value.get("records_sha256")
        or not isinstance(value.get("row_count"), int)
        or value["row_count"] < 2
        or not isinstance(value.get("train_corrective_count"), int)
        or value["train_corrective_count"] < 1
        or not isinstance(value.get("heldout_count"), int)
        or value["heldout_count"] < 1
        or _HEX40.fullmatch(str(value.get("source_git_sha"))) is None
        or not isinstance(value.get("row_balance"), Mapping)
        or not isinstance(invariance, Mapping)
    ):
        raise SolDaggerTrainingError("collection manifest policy or bound artifact drifted")
    expected_invariance = {
        "student_harness_unchanged": True,
        "teacher_harness_projection_byte_identical": True,
        "teacher_saw_student_action": False,
        "teacher_saw_student_response": False,
        "teacher_saw_hidden_database": False,
        "teacher_saw_outcomes": False,
        "teacher_saw_sidecar_or_evaluator": False,
        "evaluation_harness_unchanged": True,
        "model_visible_sidecar_fields": 0,
        "provider_reasoning_targets": 0,
        "harness_id": "browseruse-deliberative",
        "provider_only_differences": PROVIDER_ONLY_DIFFERENCES,
    }
    if any(invariance.get(key) != expected for key, expected in expected_invariance.items()) or any(
        _HEX64.fullmatch(str(invariance.get(key))) is None
        for key in ("harness_fingerprint_sha256", "evaluation_fingerprint_sha256")
    ):
        raise SolDaggerTrainingError("collection harness/evaluator invariance gate drifted")
    parent_path = Path(str(value.get("parent_receipt_path", ""))).resolve()
    if sha256_file(parent_path) != value.get("parent_receipt_sha256"):
        raise SolDaggerTrainingError("collection parent receipt bytes changed")
    parent = validate_repair_step24_parent(parent_path)
    if parent["receipt_body_sha256"] != value.get("parent_receipt_body_sha256"):
        raise SolDaggerTrainingError("collection parent receipt body binding drifted")
    if value.get("parent_model") != parent.get("parent_model"):
        raise SolDaggerTrainingError("collection parent model is not receipt-bound")
    if value.get("parent_final_dcp") != parent["final_dcp_identity"]:
        raise SolDaggerTrainingError("collection parent final DCP is not receipt-bound")
    rows = _records(
        records,
        int(value["row_count"]),
        expected_teacher=TEACHER_DESCRIPTOR,
        required_kinds={"corrective", "retention"},
    )
    heldout_path = Path(str(value.get("heldout_path", ""))).resolve()
    if (
        not heldout_path.is_file()
        or heldout_path.is_symlink()
        or sha256_file(heldout_path) != value.get("heldout_sha256")
    ):
        raise SolDaggerTrainingError("collection heldout artifact drifted")
    heldout = _records(
        heldout_path,
        int(value["heldout_count"]),
        expected_teacher=TEACHER_DESCRIPTOR,
        required_kinds={"corrective"},
    )
    _validate_campaign(value, rows, heldout)
    observed_balance = {
        "corrective": sum(row["metadata"]["kind"] == "corrective" for row in rows),
        "retention": sum(row["metadata"]["kind"] == "retention" for row in rows),
    }
    if (
        dict(value["row_balance"]) != observed_balance
        or value["train_corrective_count"] != observed_balance["corrective"]
        or sum(observed_balance.values()) != value["row_count"]
    ):
        raise SolDaggerTrainingError("collection row-count or corrective/retention balance drifted")
    return {
        **value,
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "records_path": str(records),
        "heldout_path": str(heldout_path),
        "heldout_sha256": value["heldout_sha256"],
        "parent": parent,
    }


def _validate_campaign(
    manifest: Mapping[str, Any], rows: Sequence[dict[str, Any]], heldout: Sequence[dict[str, Any]]
) -> None:
    """Make the collection's coverage and frozen-retention claims executable."""

    campaign = manifest.get("campaign")
    if not isinstance(campaign, Mapping):
        raise SolDaggerTrainingError("collection lacks a campaign coverage gate")
    if (
        campaign.get("target_train_corrective") != TARGET_TRAIN_CORRECTIVE
        or campaign.get("target_heldout") != TARGET_HELDOUT
        or campaign.get("min_train_corrective") != MIN_TRAIN_CORRECTIVE
        or campaign.get("min_heldout") != MIN_HELDOUT
        or not isinstance(campaign.get("retention"), Mapping)
    ):
        raise SolDaggerTrainingError(
            "collection campaign target or frozen retention binding drifted"
        )
    retention_path = Path(str(manifest.get("retention_source_path", ""))).resolve()
    retention_sha256 = "4d614d80ae4db704b68118da00942b1584805e3e59a4a2cf0e1cee79117adc1c"
    if (
        not retention_path.is_file()
        or retention_path.is_symlink()
        or manifest.get("retention_source_sha256") != retention_sha256
        or sha256_file(retention_path) != retention_sha256
        or manifest.get("retention_source_row_count") != IMMUTABLE_REPAIR_ROWS
        or manifest.get("retention_provenance")
        != {
            "source_path": str(retention_path),
            "source_sha256": retention_sha256,
            "row_count": IMMUTABLE_REPAIR_ROWS,
            "immutable": True,
        }
    ):
        raise SolDaggerTrainingError("collection immutable repair retention binding drifted")
    variants = campaign.get("variants")
    phases = campaign.get("phases")
    exclusions = campaign.get("exclusions")
    if (
        not isinstance(variants, Mapping)
        or set(variants) != set(VARIANTS)
        or not isinstance(phases, Mapping)
    ):
        raise SolDaggerTrainingError("collection campaign variant/phase coverage is absent")
    if set(phases) != set(PHASES) or not isinstance(exclusions, Mapping):
        raise SolDaggerTrainingError("collection campaign phase or exclusion schema drifted")
    excluded = exclusions.get("count")
    attempted = exclusions.get("total")
    rate = exclusions.get("rate")
    if (
        type(excluded) is not int
        or type(attempted) is not int
        or attempted < 1
        or excluded < 0
        or excluded > attempted
        or not isinstance(rate, (int, float))
        or not 0 <= rate <= 0.25
        or abs(rate - excluded / attempted) > 1e-12
    ):
        raise SolDaggerTrainingError("collection exclusion rate exceeds the 25% campaign gate")

    corrective_train = [row for row in rows if row["metadata"]["kind"] == "corrective"]
    corrective_heldout = list(heldout)
    retention = [row for row in rows if row["metadata"]["kind"] == "retention"]
    if (
        len(corrective_train) != TARGET_TRAIN_CORRECTIVE
        or len(corrective_heldout) != TARGET_HELDOUT
        or len(retention) != IMMUTABLE_REPAIR_ROWS
    ):
        raise SolDaggerTrainingError("collection campaign row counts do not meet the exact gates")
    if any(row["metadata"].get("split") != "train" for row in corrective_train) or any(
        row["metadata"].get("split") != "heldout" for row in corrective_heldout
    ):
        raise SolDaggerTrainingError("collection train/heldout split labels drifted")
    source_rows = _source_rows(retention_path)
    if any(
        row["metadata"].get("retention_source_line") != index
        or row["metadata"].get("retention_source_sha256") != retention_sha256
        or _row_signature(row) != _row_signature(source_rows[index - 1])
        for index, row in enumerate(retention, 1)
    ):
        raise SolDaggerTrainingError(
            "collection retention rows are not the immutable repair corpus"
        )
    all_corrective = corrective_train + corrective_heldout
    if len({_row_signature(row) for row in all_corrective}) != len(all_corrective):
        raise SolDaggerTrainingError("collection silently duplicates a Sol corrective row")
    for variant, counts in variants.items():
        if not isinstance(variant, str) or not isinstance(counts, Mapping):
            raise SolDaggerTrainingError("collection campaign variant descriptor is invalid")
        observed = {
            "train": sum(
                row["metadata"].get("source_variant") == variant for row in corrective_train
            ),
            "heldout": sum(
                row["metadata"].get("source_variant") == variant for row in corrective_heldout
            ),
        }
        if dict(counts) != observed or observed != {"train": 16, "heldout": 6}:
            raise SolDaggerTrainingError("collection campaign is not balanced across variants")
    for phase, counts in phases.items():
        if not isinstance(counts, Mapping):
            raise SolDaggerTrainingError("collection campaign phase descriptor is invalid")
        observed = {
            "train": sum(row["metadata"].get("phase") == phase for row in corrective_train),
            "heldout": sum(row["metadata"].get("phase") == phase for row in corrective_heldout),
        }
        if dict(counts) != observed or observed["train"] < 10 or observed["heldout"] < 4:
            raise SolDaggerTrainingError("collection campaign phase coverage is inadequate")


def _records(
    path: Path,
    expected_count: int,
    *,
    expected_teacher: Mapping[str, Any] | None = None,
    required_kinds: set[str] | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SolDaggerTrainingError(
                    f"collection line {line_number} is invalid JSON"
                ) from exc
            if not isinstance(row, dict):
                raise SolDaggerTrainingError(f"collection line {line_number} is not an object")
            kind = row.get("kind")
            if kind not in {"corrective", "retention"}:
                raise SolDaggerTrainingError(f"collection line {line_number} has invalid kind")
            if kind == "corrective":
                teacher = row.get("teacher")
                if not isinstance(teacher, Mapping) or teacher.get("model") != SOL_MODEL:
                    raise SolDaggerTrainingError(
                        f"corrective collection line {line_number} lacks the Sol teacher descriptor"
                    )
                if expected_teacher is not None and dict(teacher) != dict(expected_teacher):
                    raise SolDaggerTrainingError(
                        f"corrective collection line {line_number} teacher effort/provider drifted"
                    )
                messages = row.get("messages")
                if messages is None:
                    request = row.get("request_messages")
                    completion = row.get("sol_completion")
                    if isinstance(request, list) and isinstance(completion, dict):
                        messages = [*request, completion]
                if not isinstance(messages, list) or not messages:
                    raise SolDaggerTrainingError(
                        "corrective collection line "
                        f"{line_number} lacks a recorded Sol assistant completion"
                    )
                if (
                    row.get("split") not in {"train", "heldout"}
                    or not isinstance(row.get("source_variant"), str)
                    or not isinstance(row.get("phase"), str)
                ):
                    raise SolDaggerTrainingError(
                        f"corrective collection line {line_number} lacks campaign coordinates"
                    )
            else:
                messages = row.get("messages")
                if not isinstance(messages, list) or not messages:
                    raise SolDaggerTrainingError(
                        f"retention collection line {line_number} lacks messages"
                    )
            if (
                not all(isinstance(message, dict) for message in messages)
                or messages[-1].get("role") != "assistant"
            ):
                raise SolDaggerTrainingError(
                    f"collection line {line_number} does not end in assistant output"
                )
            output = {
                "messages": messages,
                "metadata": {
                    "kind": kind,
                    "source_line": line_number,
                    **{
                        key: row[key]
                        for key in (
                            "split",
                            "source_variant",
                            "phase",
                            "retention_source_line",
                            "retention_source_sha256",
                        )
                        if key in row
                    },
                },
            }
            if "tools" in row:
                if not isinstance(row["tools"], list):
                    raise SolDaggerTrainingError(f"collection line {line_number} has invalid tools")
                output["tools"] = row["tools"]
            rows.append(output)
    if len(rows) != expected_count:
        raise SolDaggerTrainingError("collection row_count does not match its verified manifest")
    if required_kinds is not None and {row["metadata"]["kind"] for row in rows} != required_kinds:
        raise SolDaggerTrainingError("collection row kinds do not match the bound artifact role")
    return rows


def _row_signature(row: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json({key: row.get(key) for key in ("messages", "tools")}).encode()
    ).hexdigest()


def _source_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SolDaggerTrainingError(
                    f"immutable repair line {line_number} is invalid JSON"
                ) from exc
            if not isinstance(row, dict) or not isinstance(row.get("messages"), list):
                raise SolDaggerTrainingError(f"immutable repair line {line_number} lacks messages")
            rows.append({key: row.get(key) for key in ("messages", "tools")})
    if len(rows) != IMMUTABLE_REPAIR_ROWS:
        raise SolDaggerTrainingError("immutable repair source has the wrong row count")
    return rows


def _quoted(value: str | Path) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _array(values: Sequence[str]) -> str:
    return "[" + ", ".join(_quoted(value) for value in values) + "]"


def _render_config(
    *, parent_model: Path, dataset: Path, output: Path, targets: Sequence[str], renderer: str
) -> bytes:
    lines = [
        f"# PRIME-RL {PRIME_VERSION} ({PRIME_COMMIT}); Sol-to-Qwen DAgger corrective SFT",
        "# Exact continuation from the receipt-bound repair-step24 trainer DCP.",
        "# Topology is DP-shard=1 x CP=4 (four GPUs).",
        "max_steps = 25",
        f"output_dir = {_quoted(output)}",
        "clean_output_dir = false",
        'matmul_precision = "high"',
        'loss_impl = "liger_fused"',
        "",
        "[env_vars]",
        'FLA_TILELANG = "0"',
        'WANDB_MODE = "disabled"',
        "",
        "[deployment]",
        'type = "single_node"',
        "num_gpus = 4",
        "gpus_per_node = 4",
        "",
        "[model]",
        f"name = {_quoted(parent_model)}",
        f"seq_len = {SEQ_LEN}",
        'impl = "hf"',
        'attn = "flash_attention_2"',
        'optimization_dtype = "bfloat16"',
        'reduce_dtype = "bfloat16"',
        f"cp = {CONTEXT_PARALLEL_SIZE}",
        'cp_style = "ulysses"',
        "",
        "[model.ac]",
        'mode = "full"',
        "freq = 1",
        "",
        "[model.lora]",
        "rank = 64",
        "alpha = 128.0",
        "dropout = 0.0",
        f"target_modules = {_array(targets)}",
        "modules_to_save = []",
        "",
        "[renderer]",
        f"name = {_quoted(renderer)}",
        "enable_thinking = true",
        *(["preserve_thinking = true"] if renderer == "qwen3.6" else []),
        "",
        "[data]",
        'type = "sft"',
        f"name = {_quoted(dataset)}",
        f"batch_size = {GLOBAL_BATCH_SIZE}",
        f"seq_len = {SEQ_LEN}",
        "micro_batch_size = 1",
        'pack_function = "cat"',
        "shuffle = false",
        "seed = 56036027",
        "",
        "[data.loss_mask]",
        "system = false",
        "user = false",
        "assistant = true",
        "tool = false",
        "",
        "[optim]",
        'type = "adamw"',
        "lr = 5e-07",
        "weight_decay = 0.01",
        "max_norm = 1.0",
        "",
        "[scheduler]",
        'type = "constant"',
        "",
        "[ckpt]",
        "interval = 1",
        "resume_step = 24",
        "keep_last = 2",
        "skip_optimizer = true",
        "skip_scheduler = true",
        "skip_dataloader = true",
        "skip_progress = false",
        "",
        "[ckpt.weights]",
        "save_sharded = true",
        'save_format = "safetensors"',
        "save_adapter_separately = true",
    ]
    return ("\n".join(lines).rstrip() + "\n").encode()


def _validate_config(config: Mapping[str, Any], *, dataset: Path, snapshot: Path) -> None:
    if (
        config.get("max_steps") != 25
        or config.get("deployment") != {"type": "single_node", "num_gpus": 4, "gpus_per_node": 4}
        or Path(str(config.get("model", {}).get("name", ""))).resolve() != snapshot
        or config.get("model", {}).get("seq_len") != SEQ_LEN
        or config.get("model", {}).get("cp") != CONTEXT_PARALLEL_SIZE
        or config.get("model", {}).get("lora", {}).get("rank") != LORA_RANK
        or config.get("model", {}).get("lora", {}).get("alpha") != LORA_ALPHA
        or Path(str(config.get("data", {}).get("name", ""))).resolve() != dataset
        or config.get("data", {}).get("batch_size") != GLOBAL_BATCH_SIZE
        or config.get("data", {}).get("loss_mask")
        != {"system": False, "user": False, "assistant": True, "tool": False}
        or config.get("optim", {}).get("lr") != LEARNING_RATE
        or config.get("ckpt", {}).get("resume_step") != 24
        or config.get("ckpt", {}).get("skip_optimizer") is not True
        or config.get("ckpt", {}).get("skip_scheduler") is not True
        or config.get("ckpt", {}).get("skip_dataloader") is not True
        or config.get("ckpt", {}).get("skip_progress") is not False
    ):
        raise SolDaggerTrainingError("generated Sol DAgger SFT config drifted")


def _active_mass(rows: Sequence[dict[str, Any]], audit: Mapping[str, Any]) -> dict[str, int]:
    audited_rows = audit.get("rows")
    if not isinstance(audited_rows, list) or len(audited_rows) != len(rows):
        raise SolDaggerTrainingError(
            "Qwen rendered-token audit does not cover every collection row"
        )
    mass = {"corrective": 0, "retention": 0}
    for row, audited in zip(rows, audited_rows):
        tokens = audited.get("trainable_tokens") if isinstance(audited, Mapping) else None
        if type(tokens) is not int or tokens < 1:
            raise SolDaggerTrainingError(
                "Qwen rendered-token audit lacks positive assistant token mass"
            )
        mass[str(row["metadata"]["kind"])] += tokens
    ratio = mass["corrective"] / mass["retention"] if mass["retention"] else 0.0
    if not 1.5 <= ratio <= 2.5:
        raise SolDaggerTrainingError(
            "corrective:retention active-token loss mass must be within [1.5, 2.5]"
        )
    return mass


def _valid_active_mass(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    corrective = value.get("corrective")
    retention = value.get("retention")
    return (
        type(corrective) is int
        and type(retention) is int
        and corrective > 0
        and retention > 0
        and 1.5 <= corrective / retention <= 2.5
    )


def _audit_trainer_log(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise SolDaggerTrainingError("Sol DAgger step-25 trainer log is absent or unsafe")
    text = path.read_text(encoding="utf-8", errors="replace")
    required = (
        "Starting from step 25",
        "Step 25 |",
        "Writing final checkpoint",
        "Writing final weight checkpoint",
        "SFT trainer finished!",
    )
    forbidden = ("Step 26 |", "Traceback (most recent call last)", "RuntimeError:")
    if any(marker not in text for marker in required) or any(
        marker in text for marker in forbidden
    ):
        raise SolDaggerTrainingError("Sol DAgger trainer log does not prove one clean step 25")
    if text.count("Step 25 |") != 1:
        raise SolDaggerTrainingError("Sol DAgger trainer log has an unexpected step-25 count")
    return {
        "source_step": 24,
        "final_step": 25,
        "step25_records": 1,
        "final_checkpoint_written": True,
        "final_weight_checkpoint_written": True,
        "trainer_finished": True,
        "tracebacks": 0,
    }


def prepare_sol_dagger_training(
    *,
    collection_manifest_path: str | Path,
    smoke_report_path: str | Path,
    output_dir: str | Path,
    prime_root: str | Path = "/opt/prime-rl",
) -> dict[str, Any]:
    """Materialize receipt-bound rows and create a step-24-to-step-25 plan."""

    output = Path(output_dir).resolve()
    if output.exists() or output.is_symlink():
        raise SolDaggerTrainingError("Sol DAgger output must be a new create-only directory")
    collection = validate_collection_manifest(collection_manifest_path)
    rows = _records(
        Path(collection["records_path"]),
        int(collection["row_count"]),
        expected_teacher=TEACHER_DESCRIPTOR,
        required_kinds={"corrective", "retention"},
    )
    output.mkdir(parents=True)
    source = output / "materialized" / "train.jsonl"
    _write_new(source, b"".join((canonical_json(row) + "\n").encode() for row in rows))
    prime = materialize_sft_jsonl_to_parquet(
        source,
        output / "prime",
        smoke_report=smoke_report_path,
        seq_len=SEQ_LEN,
        required_seq_len=SEQ_LEN,
        global_batch_size=GLOBAL_BATCH_SIZE,
        prime_root=prime_root,
    )
    if prime.optimizer_updates != 1 or prime.prime_max_steps != 1:
        raise SolDaggerTrainingError(
            "Sol DAgger rendered data must fit exactly one optimizer update"
        )
    manifest = _read_json(prime.manifest_path)
    mass = _active_mass(rows, manifest.get("token_audit", {}))
    adapter = Path(str(collection["parent"]["candidate"]["path"])).resolve()
    targets = _adapter_targets(adapter)
    config_path = output / "training" / "sol_dagger_sft.toml"
    training_output = output / "training" / "prime_output"
    bridge = _link_tree(
        Path(collection["parent"]["final_dcp_path"]),
        # PRIME resumes a trainer DCP from this nested location.  Linking the
        # DCP directly under step_24 creates a structurally valid tree that
        # PRIME nevertheless cannot discover at resume_step=24.
        training_output / "checkpoints" / "step_24" / "trainer",
    )
    _write_new(
        config_path,
        _render_config(
            parent_model=Path(collection["parent"]["parent_model_path"]),
            dataset=prime.manifest_path.parent,
            output=training_output,
            targets=targets,
            renderer=prime.renderer,
        ),
    )
    _validate_config(
        tomllib.loads(config_path.read_text(encoding="utf-8")),
        dataset=prime.manifest_path.parent,
        snapshot=Path(collection["parent"]["parent_model_path"]),
    )
    body = {
        "schema": PLAN_SCHEMA,
        "status": "prepared",
        "scientific_label": "sol_to_qwen_receipt_bound_dagger_sft",
        "prime_version": PRIME_VERSION,
        "prime_commit": PRIME_COMMIT,
        "collection_manifest_path": collection["manifest_path"],
        "collection_manifest_sha256": collection["manifest_sha256"],
        "collection_manifest_body_sha256": collection["manifest_body_sha256"],
        "heldout_path": collection["heldout_path"],
        "heldout_sha256": collection["heldout_sha256"],
        "parent_receipt_path": collection["parent"]["receipt_path"],
        "parent_receipt_sha256": collection["parent"]["receipt_sha256"],
        "parent_receipt_body_sha256": collection["parent"]["receipt_body_sha256"],
        "parent_candidate": PARENT_CANDIDATE,
        "parent_model": collection["parent"]["parent_model"],
        "source_dcp": collection["parent"]["final_dcp_identity"],
        "checkpoint_bridge": bridge,
        "source_jsonl": str(source),
        "source_jsonl_sha256": sha256_file(source),
        "prime_manifest_path": str(prime.manifest_path),
        "prime_manifest_sha256": sha256_file(prime.manifest_path),
        "prime_parquet_path": str(prime.train_parquet),
        "prime_parquet_sha256": sha256_file(prime.train_parquet),
        "config_path": str(config_path),
        "config_sha256": sha256_file(config_path),
        "training_output": str(training_output),
        "candidate_path": str(training_output / "weights/step_25/lora_adapters"),
        "active_token_loss_mass": mass,
        "assistant_tokens_only": True,
        "teacher_model": SOL_MODEL,
        "seq_len": SEQ_LEN,
        "topology": {
            "dp_shards": DATA_PARALLEL_SHARDS,
            "cp": CONTEXT_PARALLEL_SIZE,
            "gpus": NUM_GPUS,
        },
        "runtime_recovery": {
            "reason": "cp2_long_context_first_backward_cuda_oom",
            "failed_optimizer_updates": 0,
            "rows_changed": False,
            "loss_changed": False,
            "learning_rate_changed": False,
        },
        "lora": {"rank": LORA_RANK, "alpha": LORA_ALPHA},
        "source_step": 24,
        "final_step": 25,
        "optimizer_updates": 1,
        "learning_rate": LEARNING_RATE,
        "fresh_optimizer": True,
        "optimizer_continuation": False,
        "launch_authorized": True,
    }
    return _publish(output / "training" / "plan.json", body, hash_field="plan_body_sha256")


def validate_sol_dagger_plan(path: str | Path) -> dict[str, Any]:
    plan_path = Path(path).resolve()
    plan = _self_hashed(plan_path, schema=PLAN_SCHEMA, hash_field="plan_body_sha256")
    if (
        plan.get("status") != "prepared"
        or plan.get("parent_candidate") != PARENT_CANDIDATE
        or plan.get("teacher_model") != SOL_MODEL
        or plan.get("assistant_tokens_only") is not True
        or plan.get("seq_len") != SEQ_LEN
        or plan.get("topology")
        != {
            "dp_shards": DATA_PARALLEL_SHARDS,
            "cp": CONTEXT_PARALLEL_SIZE,
            "gpus": NUM_GPUS,
        }
        or plan.get("runtime_recovery")
        != {
            "reason": "cp2_long_context_first_backward_cuda_oom",
            "failed_optimizer_updates": 0,
            "rows_changed": False,
            "loss_changed": False,
            "learning_rate_changed": False,
        }
        or plan.get("lora") != {"rank": LORA_RANK, "alpha": LORA_ALPHA}
        or plan.get("source_step") != 24
        or plan.get("final_step") != 25
        or plan.get("optimizer_updates") != 1
        or plan.get("learning_rate") != LEARNING_RATE
        or plan.get("fresh_optimizer") is not True
        or plan.get("optimizer_continuation") is not False
        or plan.get("launch_authorized") is not True
        or not _valid_active_mass(plan.get("active_token_loss_mass"))
    ):
        raise SolDaggerTrainingError("Sol DAgger plan policy drifted")
    for raw_path, expected in (
        (plan["collection_manifest_path"], plan["collection_manifest_sha256"]),
        (plan["parent_receipt_path"], plan["parent_receipt_sha256"]),
        (plan["source_jsonl"], plan["source_jsonl_sha256"]),
        (plan["prime_manifest_path"], plan["prime_manifest_sha256"]),
        (plan["prime_parquet_path"], plan["prime_parquet_sha256"]),
        (plan["config_path"], plan["config_sha256"]),
    ):
        if sha256_file(Path(str(raw_path)).resolve()) != expected:
            raise SolDaggerTrainingError("Sol DAgger plan-bound artifact changed")
    collection = validate_collection_manifest(plan["collection_manifest_path"])
    if collection["manifest_body_sha256"] != plan["collection_manifest_body_sha256"]:
        raise SolDaggerTrainingError("Sol DAgger collection manifest body binding drifted")
    if (
        plan.get("heldout_path") != collection["heldout_path"]
        or plan.get("heldout_sha256") != collection["heldout_sha256"]
    ):
        raise SolDaggerTrainingError("Sol DAgger heldout binding drifted")
    parent = validate_repair_step24_parent(plan["parent_receipt_path"])
    if parent["receipt_body_sha256"] != plan["parent_receipt_body_sha256"]:
        raise SolDaggerTrainingError("Sol DAgger parent receipt body binding drifted")
    if (
        plan.get("parent_model") != parent["parent_model"]
        or plan.get("source_dcp") != parent["final_dcp_identity"]
    ):
        raise SolDaggerTrainingError("Sol DAgger parent model/DCP binding drifted")
    try:
        source_dcp = _require_dcp_inventory(
            Path(str(plan["source_dcp"]["path"])),
            expected_identity=plan["source_dcp"],
            trainer_world_size=NUM_GPUS,
        )
        bridged_dcp = _require_dcp_inventory(
            Path(str(plan["checkpoint_bridge"]["destination"]["path"])),
            expected_identity=plan["checkpoint_bridge"]["destination"],
            trainer_world_size=NUM_GPUS,
        )
    except Exception as exc:
        raise SolDaggerTrainingError("Sol DAgger step-24 checkpoint bridge drifted") from exc
    if any(source_dcp[key] != bridged_dcp[key] for key in ("files", "bytes", "tree_sha256")):
        raise SolDaggerTrainingError("Sol DAgger bridged DCP no longer matches step-24 parent")
    _validate_config(
        tomllib.loads(Path(plan["config_path"]).read_text(encoding="utf-8")),
        dataset=Path(plan["prime_manifest_path"]).parent.resolve(),
        snapshot=Path(str(collection["parent"]["parent_model_path"])).resolve(),
    )
    return plan


def write_sol_dagger_receipt(*, plan_path: str | Path, executor_git_sha: str) -> dict[str, Any]:
    """Create a receipt only after one successful step-24 to step-25 PRIME update."""

    if _HEX40.fullmatch(executor_git_sha) is None:
        raise SolDaggerTrainingError("executor git SHA must be 40 lowercase hexadecimal characters")
    plan_path = Path(plan_path).resolve()
    plan = validate_sol_dagger_plan(plan_path)
    output = Path(str(plan["training_output"])).resolve()
    candidate = Path(str(plan["candidate_path"])).resolve()
    identity = _tree_identity(candidate)
    stable = candidate.parent / "STABLE"
    if not stable.is_file() or stable.is_symlink():
        raise SolDaggerTrainingError("one-update Sol DAgger adapter lacks STABLE marker")
    if (output / "weights/step_26").exists() or (output / "checkpoints/step_26").exists():
        raise SolDaggerTrainingError("Sol DAgger training produced an unauthorized extra update")
    config = tomllib.loads(Path(plan["config_path"]).read_text(encoding="utf-8"))
    targets = sorted(config["model"]["lora"]["target_modules"])
    if _adapter_targets(candidate) != targets:
        raise SolDaggerTrainingError(
            "trained adapter target modules differ from the receipt-bound config"
        )
    try:
        source_dcp = _require_dcp_inventory(
            Path(str(plan["source_dcp"]["path"])),
            expected_identity=plan["source_dcp"],
            trainer_world_size=NUM_GPUS,
        )
        final_dcp = _require_dcp_inventory(
            output / "checkpoints/step_25/trainer", trainer_world_size=NUM_GPUS
        )
    except Exception as exc:
        raise SolDaggerTrainingError("Sol DAgger final step-25 trainer DCP is invalid") from exc
    logs: dict[str, dict[str, Any]] = {}
    trainer_log_audit = _audit_trainer_log(output / "logs/trainer.log")
    for label, path in (
        ("trainer", output / "logs/trainer.log"),
        ("torchrun_stderr", output / "logs/trainer/torchrun"),
    ):
        if path.is_file() and not path.is_symlink():
            logs[label] = {
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        elif path.is_dir() and not path.is_symlink():
            logs[label] = _tree_identity(path)
    if "trainer" not in logs:
        raise SolDaggerTrainingError("Sol DAgger step-25 trainer log is absent")
    body = {
        "schema": RECEIPT_SCHEMA,
        "status": "ok",
        "executor_git_sha": executor_git_sha,
        "plan_path": str(plan_path),
        "plan_sha256": sha256_file(plan_path),
        "plan_body_sha256": plan["plan_body_sha256"],
        "candidate": {
            "name": "step25-sol-dagger-sft",
            "update": 25,
            **identity,
            "adapter_config_sha256": sha256_file(candidate / "adapter_config.json"),
            "stable_marker_sha256": sha256_file(stable),
        },
        "parent_candidate": PARENT_CANDIDATE,
        "source_step": 24,
        "final_step": 25,
        "optimizer_updates": 1,
        "fresh_optimizer": True,
        "optimizer_continuation": False,
        "source_dcp": source_dcp,
        "final_dcp": final_dcp,
        "active_token_loss_mass": plan["active_token_loss_mass"],
        "config_path": plan["config_path"],
        "config_sha256": plan["config_sha256"],
        "trainer_log_audit": trainer_log_audit,
        "logs": logs,
    }
    return _publish(
        plan_path.with_name("training_receipt.json"), body, hash_field="receipt_body_sha256"
    )


def validate_sol_dagger_receipt(path: str | Path) -> dict[str, Any]:
    """Re-audit all step-25 evidence after the create-only receipt is written."""

    receipt_path = Path(path).resolve()
    receipt = _self_hashed(receipt_path, schema=RECEIPT_SCHEMA, hash_field="receipt_body_sha256")
    candidate = receipt.get("candidate")
    if (
        receipt.get("status") != "ok"
        or _HEX40.fullmatch(str(receipt.get("executor_git_sha"))) is None
        or receipt.get("parent_candidate") != PARENT_CANDIDATE
        or receipt.get("source_step") != 24
        or receipt.get("final_step") != 25
        or receipt.get("optimizer_updates") != 1
        or receipt.get("fresh_optimizer") is not True
        or receipt.get("optimizer_continuation") is not False
        or not isinstance(candidate, Mapping)
        or candidate.get("name") != "step25-sol-dagger-sft"
        or candidate.get("update") != 25
    ):
        raise SolDaggerTrainingError("Sol DAgger training receipt policy drifted")
    plan_path = Path(str(receipt.get("plan_path", ""))).resolve()
    if sha256_file(plan_path) != receipt.get("plan_sha256"):
        raise SolDaggerTrainingError("Sol DAgger training receipt plan bytes changed")
    plan = validate_sol_dagger_plan(plan_path)
    if plan["plan_body_sha256"] != receipt.get("plan_body_sha256"):
        raise SolDaggerTrainingError("Sol DAgger training receipt plan body changed")
    config_path = Path(str(receipt.get("config_path", ""))).resolve()
    if (
        config_path != Path(str(plan["config_path"])).resolve()
        or sha256_file(config_path) != receipt.get("config_sha256")
        or receipt.get("config_sha256") != plan["config_sha256"]
    ):
        raise SolDaggerTrainingError("Sol DAgger training receipt config changed")
    try:
        source_dcp = _require_dcp_inventory(
            Path(str(receipt["source_dcp"]["path"])),
            expected_identity=receipt["source_dcp"],
            trainer_world_size=NUM_GPUS,
        )
        final_dcp = _require_dcp_inventory(
            Path(str(receipt["final_dcp"]["path"])),
            expected_identity=receipt["final_dcp"],
            trainer_world_size=NUM_GPUS,
        )
    except Exception as exc:
        raise SolDaggerTrainingError("Sol DAgger training receipt DCP inventory drifted") from exc
    if source_dcp != plan["source_dcp"] or final_dcp["path"] != str(
        (Path(plan["training_output"]) / "checkpoints/step_25/trainer").resolve()
    ):
        raise SolDaggerTrainingError("Sol DAgger training receipt checkpoint lineage drifted")
    candidate_path = Path(str(candidate.get("path", ""))).resolve()
    if candidate_path != Path(str(plan["candidate_path"])).resolve():
        raise SolDaggerTrainingError("Sol DAgger training receipt candidate path drifted")
    identity = _tree_identity(candidate_path)
    if any(identity[key] != candidate.get(key) for key in ("files", "bytes", "tree_sha256")):
        raise SolDaggerTrainingError("Sol DAgger training receipt adapter tree changed")
    stable = candidate_path.parent / "STABLE"
    if sha256_file(candidate_path / "adapter_config.json") != candidate.get(
        "adapter_config_sha256"
    ) or sha256_file(stable) != candidate.get("stable_marker_sha256"):
        raise SolDaggerTrainingError("Sol DAgger training receipt adapter bytes changed")
    logs = receipt.get("logs")
    if (
        not isinstance(logs, Mapping)
        or "trainer" not in logs
        or receipt.get("trainer_log_audit")
        != _audit_trainer_log(Path(str(logs.get("trainer", {}).get("path", ""))).resolve())
    ):
        raise SolDaggerTrainingError("Sol DAgger training receipt lacks trainer log evidence")
    for descriptor in logs.values():
        if not isinstance(descriptor, Mapping):
            raise SolDaggerTrainingError("Sol DAgger training receipt log descriptor is invalid")
        log_path = Path(str(descriptor.get("path", ""))).resolve()
        if log_path.is_file():
            if sha256_file(log_path) != descriptor.get("sha256"):
                raise SolDaggerTrainingError("Sol DAgger training receipt log changed")
        else:
            identity = _tree_identity(log_path)
            if any(
                identity[key] != descriptor.get(key) for key in ("files", "bytes", "tree_sha256")
            ):
                raise SolDaggerTrainingError("Sol DAgger training receipt log tree changed")
    return receipt


__all__ = [
    "COLLECTION_SCHEMA",
    "LEARNING_RATE",
    "LORA_ALPHA",
    "LORA_RANK",
    "PARENT_CANDIDATE",
    "PLAN_SCHEMA",
    "RECEIPT_SCHEMA",
    "SEQ_LEN",
    "SOL_MODEL",
    "SolDaggerTrainingError",
    "prepare_sol_dagger_training",
    "validate_sol_dagger_receipt",
    "validate_collection_manifest",
    "validate_repair_step24_parent",
    "validate_sol_dagger_plan",
    "write_sol_dagger_receipt",
]
