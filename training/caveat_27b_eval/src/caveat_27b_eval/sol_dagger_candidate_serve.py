#!/usr/bin/env python3
"""Fail-closed direct-LoRA serving contract for the Sol-DAgger step-25 model.

The module has three deliberately separate responsibilities:

* render the exact generic reservation release consumed by the already-running
  four-B200 waiter;
* revalidate the complete repair-step24 -> Sol collection -> PRIME step25
  lineage inside that pod immediately before ``vllm serve``; and
* attest the live local endpoint without consulting candidate outcomes.

No browser prompt, tool, scaffold, task, or evaluator setting is defined here.
Those remain owned by the frozen evaluation bundle.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .common import (
    IntegrityError,
    canonical_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json_create_only,
)
from .launcher import exact_lora_composite_sha256

REPAIR_RECEIPT_SCHEMA = "harness-distill.amazon-r00-repair-sft-training-receipt.v1"
COLLECTION_SCHEMA = "harness-distill.sol-dagger-collection-manifest.v1"
PLAN_SCHEMA = "harness-distill.sol-dagger-sft-plan.v1"
TRAINING_RECEIPT_SCHEMA = "harness-distill.sol-dagger-sft-receipt.v1"
SERVE_RELEASE_SCHEMA = (
    "caveat-27b.browser-action-next-iteration.sol-dagger-candidate-serve-release.v1"
)
ENDPOINT_SCHEMA = "caveat-27b-eval.sol-dagger-step25-endpoint.v1"
LINEAGE_SCHEMA = "caveat-27b-eval.sol-dagger-pvc-lineage-attestation.v1"

PURPOSE = "sol_dagger_candidate_serve"
DEFAULT_SERVE_JOB = "caveat-27b-sol-dagger-candidate-serve-w1"
SERVE_JOB_PATTERN = re.compile(
    r"caveat-27b-sol-dagger-candidate-serve-w[1-9][0-9]*"
)
IMAGE_DIGEST = "sha256:28f38e74e17779e9c85985d3d3f970c1f6aa4427407f1843c11f2af87a324dd0"
DATA_ROOT = Path(
    "/data/caveat-27b/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30"
)
RUNS_ROOT = Path("/data/runs/t-yuxuanli")
TRAINING_CAMPAIGN_PATTERN = re.compile(
    r"caveat-27b-sol-dagger-sft-[0-9a-f]{7,40}(?:-[a-z0-9]+)*-20260814"
)
PARENT_PATH = DATA_ROOT / "selected/merged"
PARENT_TREE = "5939382fbc6db775972dc9cebf3e5be20654149a0412b4f00072bad12a2215ca"
REPAIR_ADAPTER_TREE = (
    "49e66d189603142232d0e4f1b3549d32b783ebed5fa4d3efd0e04af3197a5508"
)
REPAIR_DCP_TREE = "018e4589edb0ee7dd95eb94511c6411458ba3d0c274fa8284fa17eaae10c2b35"
REPAIR_RETENTION_FILE = (
    "4d614d80ae4db704b68118da00942b1584805e3e59a4a2cf0e1cee79117adc1c"
)
REPAIR_RECEIPT_FILE = (
    "85d5c779c8fb1ab4a1a87d4e4f4ab9bd7809675622bfe845a0632b2ab6ae4bf1"
)
REPAIR_RECEIPT_BODY = (
    "aa60d14b36e2bd5ae3cdab34cc74665ed883a03e59384f1174a17c624235fb1e"
)
REPAIR_RECEIPT_PATH = (
    DATA_ROOT
    / "browser_action_fixed_v7_amazon_r00_repair"
    / "86e7f2cab46dedcb2b44b7fe0931d1ed60fc8dc1"
    / "training_v4_r3/training/training_receipt.json"
)
REPAIR_DCP_PATH = (
    DATA_ROOT
    / "browser_action_fixed_v7_amazon_r00_repair"
    / "86e7f2cab46dedcb2b44b7fe0931d1ed60fc8dc1"
    / "training_v4_r3/training/prime_output/checkpoints/step_24/trainer"
)
COLLECTION_MANIFEST_PATH = (
    RUNS_ROOT
    / "caveat-27b-sol-dagger-sft-66ec60e-r2-20260814"
    / "collection_r1/collection_manifest.json"
)
COLLECTION_MANIFEST_FILE = (
    "eb4635dfd0254136b74d9c1c199bb81f05f09ff428e1c5c5f0bbad4c7bb5f2c2"
)
COLLECTION_MANIFEST_BODY = (
    "9a4755bfae9248e5477bdf869583b70aff76a39bf9fcbb0294c0cec583f98759"
)
COLLECTION_SOURCE_GIT = "66ec60e3e299384516ce95972345852d8043fa61"
COLLECTION_HARNESS_FINGERPRINT = (
    "d14915ced60a940a27e5d36823acac57762c9754aee71d23b97c40eb506b7da4"
)
COLLECTION_EVALUATION_FINGERPRINT = (
    "e86d213f65759fd5bbd10640c06d46bfce8a7812acc492bb3d277343c09eaf07"
)
TOKENIZER_JSON = "06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523"
CHAT_TEMPLATE = "a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715"
TEACHER_MODEL = "gpt-5.6-sol"
TEACHER_EFFORT = "low"
PRIME_VERSION = "0.7.0"
PRIME_COMMIT = "d334ea52940b47f426293a7d146239e3fbf91caa"
LOCAL_BASE_URL = "http://127.0.0.1:18530/v1"
LOCAL_TUNNEL = "18530:8000"
ENTRYPOINT_RELATIVE = Path("scripts/run_sol_dagger_candidate_serve.sh")
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
ALLOWED_SOURCE_ROOTS = (RUNS_ROOT, DATA_ROOT)
TRAINING_OUTPUT_RELATIVE = Path("training_r1/training/prime_output")
SOURCE_BRIDGE_RELATIVE = Path("checkpoints/step_24/trainer")
FINAL_DCP_RELATIVE = Path("checkpoints/step_25/trainer")
CANDIDATE_RELATIVE = Path("weights/step_25/lora_adapters")

EXPECTED_COLLECTION_INVARIANTS = {
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
}
EXPECTED_TEACHER = {
    "model": TEACHER_MODEL,
    "effort": TEACHER_EFFORT,
    "provider": "trapi",
    "model_spec": "gpt-5.6-sol#low",
}
EXPECTED_PROVIDER_ONLY_DIFFERENCES = [
    "model",
    "reasoning_effort",
    "max_completion_tokens",
    "n",
    "stream",
]
COLLECTION_VARIANTS = ("graded", "graded3", "graded4", "mixed")
COLLECTION_PHASES = (
    "constraints_query",
    "pagination_exploration",
    "pdp_evidence_selection",
    "checkpoint_grounding",
    "cart_cleanup_recheck",
    "checkout_order",
)
GENERIC_RELEASE_KEYS = {
    "schema",
    "status",
    "purpose",
    "laptop_r01_outcomes_read",
    "office_chair_outcomes_read",
    "reservation",
    "source",
    "entrypoint",
    "argv",
    "release_sha256",
}
VALIDATOR_RELATIVES = (
    "src/caveat_27b_eval/common.py",
    "src/caveat_27b_eval/launcher.py",
    "src/caveat_27b_eval/sol_dagger_candidate_serve.py",
    "src/caveat_27b_eval/sol_dagger_laptop_eval.py",
    "scripts/run_sol_dagger_candidate_serve.sh",
)


def _safe_regular(path: Path, label: str) -> Path:
    if (
        path.is_symlink()
        or not path.is_file()
        or not stat.S_ISREG(path.lstat().st_mode)
    ):
        raise IntegrityError(f"{label} is not a safe regular file: {path}")
    return path


def _is_within(path: Path, roots: Sequence[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        return True
    return False


def _self_hash(value: Mapping[str, Any], field: str, label: str) -> str:
    observed = value.get(field)
    expected = sha256_bytes(
        canonical_bytes({key: item for key, item in value.items() if key != field})
    )
    if observed != expected:
        raise IntegrityError(f"{label} has an invalid {field}")
    return expected


def _descriptor(path: Path, schema: str, field: str, label: str) -> dict[str, Any]:
    _safe_regular(path, label)
    value = read_json(path)
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise IntegrityError(f"{label} schema changed")
    _self_hash(value, field, label)
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise IntegrityError(f"{label} is not a lowercase SHA-256")
    return value


def _serve_names(job_name: Any) -> tuple[str, str]:
    if not isinstance(job_name, str) or SERVE_JOB_PATTERN.fullmatch(job_name) is None:
        raise IntegrityError("Sol DAgger candidate serve job name is invalid")
    return job_name, job_name + "-master-0"


def _training_campaign(path: Path) -> tuple[Path, str]:
    """Resolve the task-specific direct campaign root and its source prefix."""

    resolved = path.resolve()
    try:
        relative = resolved.relative_to(RUNS_ROOT)
    except ValueError as exc:
        raise IntegrityError("Sol DAgger artifact escaped the training runs root") from exc
    if not relative.parts:
        raise IntegrityError("Sol DAgger training campaign root is absent")
    name = relative.parts[0]
    match = TRAINING_CAMPAIGN_PATTERN.fullmatch(name)
    if match is None:
        raise IntegrityError("Sol DAgger training campaign name changed")
    prefix = name.removeprefix("caveat-27b-sol-dagger-sft-").split("-", 1)[0]
    return RUNS_ROOT / name, prefix


def _artifact(path: Path, body_field: str) -> dict[str, str]:
    value = read_json(path)
    _self_hash(value, body_field, path.name)
    return {
        "path": str(path.resolve()),
        "file_sha256": sha256_file(path),
        "body_sha256": str(value[body_field]),
    }


def _bound_source_files(root: Path) -> dict[str, str]:
    return {
        relative: sha256_file(_safe_regular(root / relative, f"bound source {relative}"))
        for relative in VALIDATOR_RELATIVES
    }


def _tree_identity(root: Path) -> dict[str, Any]:
    """Use the canonical mapping encoding used by harness_distill receipts."""

    if root.is_symlink() or not root.is_dir() or not stat.S_ISDIR(root.lstat().st_mode):
        raise IntegrityError(f"unsafe component directory: {root}")
    entries: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise IntegrityError(f"component contains a symlink: {path}")
        if path.is_file():
            _safe_regular(path, "component member")
            entries[path.relative_to(root).as_posix()] = {
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        elif not path.is_dir():
            raise IntegrityError(f"component contains a special entry: {path}")
    if not entries:
        raise IntegrityError(f"component tree is empty: {root}")
    return {
        "path": str(root.resolve()),
        "files": len(entries),
        "bytes": sum(row["size"] for row in entries.values()),
        "tree_sha256": sha256_bytes(canonical_bytes(entries)),
    }


def _source_identity(root: Path) -> dict[str, Any]:
    """Use the generic reservation's canonical sorted-list encoding."""

    if root.is_symlink() or not root.is_dir() or not stat.S_ISDIR(root.lstat().st_mode):
        raise IntegrityError(f"unsafe staged source root: {root}")
    rows: list[dict[str, Any]] = []
    total = 0
    for base, directories, files in os.walk(root, followlinks=False):
        directory = Path(base)
        directories.sort()
        files.sort()
        for name in directories:
            child = directory / name
            if child.is_symlink() or not child.is_dir():
                raise IntegrityError(f"unsafe staged source directory: {child}")
        for name in files:
            child = _safe_regular(directory / name, "staged source member")
            size = child.stat().st_size
            rows.append(
                {
                    "path": child.relative_to(root).as_posix(),
                    "size": size,
                    "sha256": sha256_file(child),
                }
            )
            total += size
    rows.sort(key=lambda row: row["path"])
    if not rows:
        raise IntegrityError("staged source tree is empty")
    return {
        "files": len(rows),
        "bytes": total,
        "tree_sha256": sha256_bytes(canonical_bytes(rows)),
    }


def _dcp_identity(root: Path) -> dict[str, Any]:
    identity = _tree_identity(root)
    names = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    expected = {
        ".metadata",
        *(f"__{rank}_0.distcp" for rank in range(4)),
        *(f"dataloader/rank_{rank}.pt" for rank in range(4)),
    }
    if names != expected:
        raise IntegrityError("Sol DAgger trainer DCP inventory changed")
    return identity


def _trainer_log_audit(path: Path) -> dict[str, Any]:
    text = _safe_regular(path, "Sol DAgger trainer log").read_text(
        encoding="utf-8", errors="replace"
    )
    required = (
        "Starting from step 25",
        "Step 25 |",
        "Writing final checkpoint",
        "Writing final weight checkpoint",
        "SFT trainer finished!",
    )
    forbidden = ("Step 26 |", "Traceback (most recent call last)", "RuntimeError:")
    if (
        any(marker not in text for marker in required)
        or any(marker in text for marker in forbidden)
        or text.count("Step 25 |") != 1
    ):
        raise IntegrityError("trainer log does not prove exactly one clean step-25 update")
    return {
        "source_step": 24,
        "final_step": 25,
        "step25_records": 1,
        "final_checkpoint_written": True,
        "final_weight_checkpoint_written": True,
        "trainer_finished": True,
        "tracebacks": 0,
    }


def _audit_log_descriptor(descriptor: Mapping[str, Any]) -> None:
    path = Path(str(descriptor.get("path", ""))).resolve()
    if path.is_file() and not path.is_symlink():
        if (
            set(descriptor) != {"path", "bytes", "sha256"}
            or descriptor.get("bytes") != path.stat().st_size
            or descriptor.get("sha256") != sha256_file(path)
        ):
            raise IntegrityError("Sol DAgger training log descriptor changed")
        return
    identity = _tree_identity(path)
    if any(
        descriptor.get(key) != identity[key]
        for key in ("path", "files", "bytes", "tree_sha256")
    ):
        raise IntegrityError("Sol DAgger training log tree changed")


def _four_b200_resources(container: Mapping[str, Any], selector: Mapping[str, Any]) -> bool:
    resources = container.get("resources")
    if not isinstance(resources, Mapping) or not isinstance(selector, Mapping):
        return False
    for field in ("requests", "limits"):
        values = resources.get(field)
        if not isinstance(values, Mapping) or str(values.get("nvidia.com/gpu")) != "4":
            return False
        rdma = [value for key, value in values.items() if "rdma" in str(key).lower()]
        if len(rdma) != 1 or str(rdma[0]) != "4":
            return False
    return "b200" in " ".join(
        f"{key}={value}" for key, value in sorted(selector.items())
    ).lower()


def validate_repair_parent(path: Path) -> dict[str, Any]:
    value = _descriptor(
        path.resolve(), REPAIR_RECEIPT_SCHEMA, "receipt_body_sha256", "repair parent"
    )
    candidate = value.get("candidate") or {}
    final_dcp = value.get("final_dcp") or {}
    plan_path = Path(str(value.get("plan_path", ""))).resolve()
    plan = _descriptor(
        plan_path,
        "harness-distill.amazon-r00-repair-sft-plan.v1",
        "plan_body_sha256",
        "repair parent plan",
    )
    parent_model = Path(str(plan.get("parent_model", ""))).resolve()
    if (
        sha256_file(path.resolve()) != REPAIR_RECEIPT_FILE
        or value.get("receipt_body_sha256") != REPAIR_RECEIPT_BODY
        or value.get("status") != "ok"
        or value.get("source_step") != 23
        or value.get("final_step") != 24
        or value.get("optimizer_updates") != 1
        or value.get("learning_rate") != 5e-7
        or value.get("assistant_tokens_only") is not True
        or candidate.get("name") != "step24-amazon-r00-repair-sft"
        or candidate.get("update") != 24
        or candidate.get("tree_sha256") != REPAIR_ADAPTER_TREE
        or final_dcp.get("tree_sha256") != REPAIR_DCP_TREE
        or value.get("plan_file_sha256") != sha256_file(plan_path)
        or value.get("plan_body_sha256") != plan.get("plan_body_sha256")
        or parent_model != PARENT_PATH
    ):
        raise IntegrityError("repair step24 parent receipt changed")
    return {**value, "validated_parent_model": str(parent_model)}


def _jsonl_objects(path: Path, expected: int, label: str) -> list[dict[str, Any]]:
    _safe_regular(path, label)
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise IntegrityError(f"{label} line {line_number} is not an object")
                rows.append(row)
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"cannot parse {label}") from exc
    if len(rows) != expected:
        raise IntegrityError(f"{label} row count changed")
    return rows


def _example_signature(row: Mapping[str, Any]) -> str:
    return sha256_bytes(
        canonical_bytes({"messages": row.get("messages"), "tools": row.get("tools")})
    )


def _validate_collection_rows(
    value: Mapping[str, Any], records: Path, heldout: Path, retention: Path
) -> None:
    train_rows = _jsonl_objects(records, 96, "Sol DAgger training records")
    heldout_rows = _jsonl_objects(heldout, 24, "Sol DAgger heldout records")
    retention_rows = _jsonl_objects(retention, 32, "repair retention source")
    corrective = [row for row in train_rows if row.get("kind") == "corrective"]
    preserved = [row for row in train_rows if row.get("kind") == "retention"]
    if len(corrective) != 64 or len(preserved) != 32:
        raise IntegrityError("Sol DAgger corrective/retention balance changed")
    all_corrective = [*corrective, *heldout_rows]
    for row, split in [
        *((item, "train") for item in corrective),
        *((item, "heldout") for item in heldout_rows),
    ]:
        messages = row.get("messages")
        if (
            row.get("kind") != "corrective"
            or row.get("split") != split
            or row.get("teacher") != EXPECTED_TEACHER
            or row.get("source_variant") not in COLLECTION_VARIANTS
            or row.get("phase") not in COLLECTION_PHASES
            or not isinstance(messages, list)
            or not messages
            or not isinstance(messages[-1], Mapping)
            or messages[-1].get("role") != "assistant"
            or not isinstance(row.get("tools"), list)
            or any(
                HEX64.fullmatch(str(row.get(field, ""))) is None
                for field in (
                    "state_id",
                    "request_sha256",
                    "effective_request_sha256",
                    "prompt_sha256",
                    "teacher_request_sha256",
                    "teacher_completion_sha256",
                    "teacher_harness_projection_sha256",
                )
            )
        ):
            raise IntegrityError("Sol DAgger corrective example contract changed")
    if (
        len({str(row["state_id"]) for row in all_corrective}) != 88
        or len({_example_signature(row) for row in all_corrective}) != 88
    ):
        raise IntegrityError("Sol DAgger corrective examples are duplicated")
    for index, (row, source) in enumerate(
        zip(preserved, retention_rows, strict=True), 1
    ):
        messages = row.get("messages")
        if (
            row.get("retention_source_line") != index
            or row.get("retention_source_sha256") != REPAIR_RETENTION_FILE
            or not isinstance(messages, list)
            or not messages
            or not isinstance(messages[-1], Mapping)
            or messages[-1].get("role") != "assistant"
            or _example_signature(row) != _example_signature(source)
        ):
            raise IntegrityError("Sol DAgger retention example changed")
    campaign = value.get("campaign")
    if not isinstance(campaign, Mapping):
        raise IntegrityError("Sol DAgger campaign gate is absent")
    expected_variants = {
        variant: {
            "train": sum(row.get("source_variant") == variant for row in corrective),
            "heldout": sum(row.get("source_variant") == variant for row in heldout_rows),
        }
        for variant in COLLECTION_VARIANTS
    }
    expected_phases = {
        phase: {
            "train": sum(row.get("phase") == phase for row in corrective),
            "heldout": sum(row.get("phase") == phase for row in heldout_rows),
        }
        for phase in COLLECTION_PHASES
    }
    exclusions = campaign.get("exclusions") or {}
    excluded, attempted, rate = (
        exclusions.get("count"),
        exclusions.get("total"),
        exclusions.get("rate"),
    )
    if (
        campaign.get("target_train_corrective") != 64
        or campaign.get("target_heldout") != 24
        or campaign.get("min_train_corrective") != 64
        or campaign.get("min_heldout") != 24
        or campaign.get("retention") != {"immutable_repair_rows": 32}
        or campaign.get("variants") != expected_variants
        or any(counts != {"train": 16, "heldout": 6} for counts in expected_variants.values())
        or campaign.get("phases") != expected_phases
        or any(
            counts["train"] < 10 or counts["heldout"] < 4
            for counts in expected_phases.values()
        )
        or type(excluded) is not int
        or type(attempted) is not int
        or attempted < 1
        or not 0 <= excluded <= attempted
        or not isinstance(rate, (int, float))
        or not 0 <= rate <= 0.25
        or abs(rate - excluded / attempted) > 1e-12
    ):
        raise IntegrityError("Sol DAgger campaign coverage gate changed")


def validate_collection(path: Path, parent_path: Path) -> dict[str, Any]:
    value = _descriptor(
        path.resolve(), COLLECTION_SCHEMA, "manifest_body_sha256", "Sol collection"
    )
    if (
        path.resolve() != COLLECTION_MANIFEST_PATH
        or sha256_file(path.resolve()) != COLLECTION_MANIFEST_FILE
        or value.get("manifest_body_sha256") != COLLECTION_MANIFEST_BODY
    ):
        raise IntegrityError("Sol collection manifest identity changed")
    parent = validate_repair_parent(parent_path)
    records = Path(str(value.get("records_path", ""))).resolve()
    heldout = Path(str(value.get("heldout_path", ""))).resolve()
    retention = Path(str(value.get("retention_source_path", ""))).resolve()
    audit = Path(str(value.get("audit_path", ""))).resolve()
    corpus_audit = Path(str(value.get("corpus_audit_path", ""))).resolve()
    evaluation_contract = Path(str(value.get("evaluation_contract_path", ""))).resolve()
    source = value.get("source_git_sha")
    invariants = value.get("invariance")
    action_validation = value.get("action_validation")
    accepted_tiers = (
        action_validation.get("accepted_tiers") or {}
        if isinstance(action_validation, Mapping)
        else {}
    )
    if (
        value.get("status") != "verified"
        or value.get("teacher_model") != TEACHER_MODEL
        or value.get("teacher_reasoning_effort") != TEACHER_EFFORT
        or value.get("teacher") != EXPECTED_TEACHER
        or value.get("assistant_tokens_only") is not True
        or value.get("qwen_rendering")
        != {"assistant_only": True, "seq_len": 32768}
        or type(value.get("row_count")) is not int
        or value.get("row_count") != 96
        or type(value.get("train_corrective_count")) is not int
        or value.get("train_corrective_count") != 64
        or type(value.get("heldout_count")) is not int
        or value.get("heldout_count") != 24
        or not isinstance(invariants, dict)
        or any(
            invariants.get(key) != expected
            for key, expected in EXPECTED_COLLECTION_INVARIANTS.items()
        )
        or invariants.get("harness_id") != "caveat-harness"
        or HEX64.fullmatch(str(invariants.get("harness_fingerprint_sha256", "")))
        is None
        or HEX64.fullmatch(str(invariants.get("evaluation_fingerprint_sha256", "")))
        is None
        or invariants.get("provider_only_differences")
        != EXPECTED_PROVIDER_ONLY_DIFFERENCES
        or not isinstance(source, str)
        or HEX40.fullmatch(source) is None
        or Path(str(value.get("parent_receipt_path", ""))).resolve()
        != parent_path.resolve()
        or value.get("parent_receipt_sha256") != sha256_file(parent_path.resolve())
        or value.get("parent_receipt_body_sha256")
        != parent["receipt_body_sha256"]
        or Path(str(value.get("parent_model", ""))).resolve() != PARENT_PATH
        or (value.get("row_balance") or {})
        != {"corrective": 64, "retention": 32}
        or not records.is_file()
        or records.is_symlink()
        or sha256_file(records) != value.get("records_sha256")
        or not heldout.is_file()
        or heldout.is_symlink()
        or sha256_file(heldout) != value.get("heldout_sha256")
        or not retention.is_file()
        or retention.is_symlink()
        or sha256_file(retention) != REPAIR_RETENTION_FILE
        or value.get("retention_source_sha256") != REPAIR_RETENTION_FILE
        or value.get("retention_source_row_count") != 32
        or value.get("retention_provenance")
        != {
            "source_path": str(retention),
            "source_sha256": REPAIR_RETENTION_FILE,
            "row_count": 32,
            "immutable": True,
        }
        or value.get("parent_final_dcp") != parent.get("final_dcp")
        or sha256_file(_safe_regular(audit, "Sol DAgger label audit"))
        != value.get("audit_sha256")
        or sha256_file(_safe_regular(corpus_audit, "Sol DAgger corpus audit"))
        != value.get("corpus_audit_sha256")
        or sha256_file(
            _safe_regular(evaluation_contract, "Sol DAgger evaluation contract")
        )
        != value.get("evaluation_contract_sha256")
        or value.get("evaluation_contract_sha256")
        != invariants.get("evaluation_fingerprint_sha256")
        or not isinstance(action_validation, Mapping)
        or not isinstance(accepted_tiers, Mapping)
        or set(accepted_tiers) - {"executed", "static_visible_state"}
        or any(type(count) is not int or count < 0 for count in accepted_tiers.values())
        or sum(accepted_tiers.values()) != 88
        or type(action_validation.get("execution_verified_actions")) is not int
        or not 0 <= action_validation["execution_verified_actions"] <= 88
        or type(action_validation.get("postcondition_verified_actions")) is not int
        or not 0 <= action_validation["postcondition_verified_actions"] <= 88
        or action_validation.get("offline_static_actions_executed") is not False
    ):
        raise IntegrityError("Sol collection manifest policy or artifact binding changed")
    _validate_collection_rows(value, records, heldout, retention)
    return value


def validate_training(
    path: Path, collection_path: Path, parent_path: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    training_path = path.resolve()
    collection_path = collection_path.resolve()
    parent_path = parent_path.resolve()
    campaign, campaign_source_prefix = _training_campaign(training_path)
    if (
        parent_path != REPAIR_RECEIPT_PATH
        or collection_path != COLLECTION_MANIFEST_PATH
        or training_path
        != campaign / "training_r1/training/training_receipt.json"
    ):
        raise IntegrityError("Sol DAgger receipt paths changed")
    receipt = _descriptor(
        training_path,
        TRAINING_RECEIPT_SCHEMA,
        "receipt_body_sha256",
        "Sol DAgger training receipt",
    )
    plan_path = Path(str(receipt.get("plan_path", ""))).resolve()
    plan = _descriptor(plan_path, PLAN_SCHEMA, "plan_body_sha256", "Sol DAgger plan")
    collection = validate_collection(collection_path, parent_path)
    candidate = receipt.get("candidate") or {}
    candidate_path = Path(str(candidate.get("path", ""))).resolve()
    source_dcp = receipt.get("source_dcp") or {}
    final_dcp = receipt.get("final_dcp") or {}
    mass = receipt.get("active_token_loss_mass") or {}
    corrective = mass.get("corrective")
    retention = mass.get("retention")
    valid_mass = (
        type(corrective) is int
        and type(retention) is int
        and retention > 0
        and 1.5 <= corrective / retention <= 2.5
    )
    plan_mass = plan.get("active_token_loss_mass")
    training_output = Path(str(plan.get("training_output", ""))).resolve()
    heldout_path = Path(str(collection.get("heldout_path", ""))).resolve()
    if (
        receipt.get("status") != "ok"
        or not isinstance(receipt.get("executor_git_sha"), str)
        or HEX40.fullmatch(receipt["executor_git_sha"]) is None
        or receipt.get("plan_sha256") != sha256_file(plan_path)
        or receipt.get("plan_body_sha256") != plan.get("plan_body_sha256")
        or plan.get("status") != "prepared"
        or plan.get("scientific_label")
        != "sol_to_qwen_receipt_bound_dagger_sft"
        or plan.get("prime_version") != PRIME_VERSION
        or plan.get("prime_commit") != PRIME_COMMIT
        or receipt.get("executor_git_sha", "")[: len(campaign_source_prefix)]
        != campaign_source_prefix
        or collection.get("source_git_sha") != COLLECTION_SOURCE_GIT
        or plan_path != campaign / "training_r1/training/plan.json"
        or Path(str(plan.get("collection_manifest_path", ""))).resolve()
        != collection_path
        or plan.get("collection_manifest_sha256")
        != sha256_file(collection_path)
        or plan.get("collection_manifest_body_sha256")
        != collection.get("manifest_body_sha256")
        or Path(str(plan.get("heldout_path", ""))).resolve() != heldout_path
        or plan.get("heldout_sha256") != collection.get("heldout_sha256")
        or Path(str(plan.get("parent_receipt_path", ""))).resolve()
        != parent_path
        or plan.get("parent_receipt_sha256") != sha256_file(parent_path)
        or plan.get("parent_receipt_body_sha256") != REPAIR_RECEIPT_BODY
        or plan.get("parent_candidate") != "step24-amazon-r00-repair-sft"
        or Path(str(plan.get("parent_model", ""))).resolve() != PARENT_PATH
        or plan.get("source_dcp") != collection.get("parent_final_dcp")
        or plan.get("teacher_model") != TEACHER_MODEL
        or plan.get("assistant_tokens_only") is not True
        or plan.get("seq_len") != 32768
        or plan.get("topology") != {"dp_shards": 1, "cp": 4, "gpus": 4}
        or plan.get("lora") != {"rank": 64, "alpha": 128.0}
        or plan.get("source_step") != 24
        or plan.get("final_step") != 25
        or plan.get("optimizer_updates") != 1
        or plan.get("learning_rate") != 5e-7
        or plan.get("fresh_optimizer") is not True
        or plan.get("optimizer_continuation") is not False
        or plan.get("launch_authorized") is not True
        or plan_mass != mass
        or receipt.get("parent_candidate") != "step24-amazon-r00-repair-sft"
        or receipt.get("source_step") != 24
        or receipt.get("final_step") != 25
        or receipt.get("optimizer_updates") != 1
        or receipt.get("fresh_optimizer") is not True
        or receipt.get("optimizer_continuation") is not False
        or candidate.get("name") != "step25-sol-dagger-sft"
        or candidate.get("update") != 25
        or Path(str(plan.get("candidate_path", ""))).resolve() != candidate_path
        or training_output != campaign / TRAINING_OUTPUT_RELATIVE
        or candidate_path != training_output / CANDIDATE_RELATIVE
        or receipt.get("source_dcp") != plan.get("source_dcp")
        or receipt.get("config_path") != plan.get("config_path")
        or receipt.get("config_sha256") != plan.get("config_sha256")
        or sha256_file(
            _safe_regular(
                Path(str(plan.get("config_path", ""))).resolve(),
                "Sol DAgger training config",
            )
        )
        != plan.get("config_sha256")
        or candidate_path.parent.name != "step_25"
        or candidate_path.name != "lora_adapters"
        or source_dcp.get("tree_sha256") != REPAIR_DCP_TREE
        or final_dcp.get("tree_sha256") == REPAIR_DCP_TREE
        or not valid_mass
        or not isinstance(receipt.get("logs"), dict)
        or "trainer" not in receipt["logs"]
    ):
        raise IntegrityError("Sol DAgger step25 training semantics changed")
    for raw_path, expected, label in (
        (plan.get("source_jsonl"), plan.get("source_jsonl_sha256"), "materialized JSONL"),
        (
            plan.get("prime_manifest_path"),
            plan.get("prime_manifest_sha256"),
            "PRIME manifest",
        ),
        (
            plan.get("prime_parquet_path"),
            plan.get("prime_parquet_sha256"),
            "PRIME parquet",
        ),
    ):
        artifact = _safe_regular(Path(str(raw_path or "")).resolve(), label)
        if sha256_file(artifact) != expected:
            raise IntegrityError(f"Sol DAgger {label} bytes changed")
    for field in ("tree_sha256", "adapter_config_sha256", "stable_marker_sha256"):
        _sha(candidate.get(field), f"candidate.{field}")
    identity = _tree_identity(candidate_path)
    if any(identity[key] != candidate.get(key) for key in ("files", "bytes", "tree_sha256")):
        raise IntegrityError("Sol DAgger candidate adapter tree changed")
    if (
        sha256_file(candidate_path / "adapter_config.json")
        != candidate["adapter_config_sha256"]
        or sha256_file(candidate_path.parent / "STABLE")
        != candidate["stable_marker_sha256"]
    ):
        raise IntegrityError("Sol DAgger adapter marker/configuration changed")
    config_path = Path(str(plan["config_path"])).resolve()
    config = tomllib.loads(_safe_regular(config_path, "Sol DAgger config").read_text())
    adapter_config = read_json(candidate_path / "adapter_config.json")
    targets = sorted(config.get("model", {}).get("lora", {}).get("target_modules") or [])
    if (
        config.get("max_steps") != 25
        or config.get("clean_output_dir") is not False
        or config.get("deployment")
        != {"type": "single_node", "num_gpus": 4, "gpus_per_node": 4}
        or Path(str(config.get("model", {}).get("name", ""))).resolve() != PARENT_PATH
        or config.get("model", {}).get("seq_len") != 32768
        or config.get("model", {}).get("cp") != 4
        or config.get("model", {}).get("lora", {}).get("rank") != 64
        or config.get("model", {}).get("lora", {}).get("alpha") != 128.0
        or Path(str(config.get("data", {}).get("name", ""))).resolve()
        != Path(str(plan["prime_manifest_path"])).resolve().parent
        or config.get("data", {}).get("batch_size") != 96
        or config.get("data", {}).get("seq_len") != 32768
        or config.get("data", {}).get("shuffle") is not False
        or config.get("data", {}).get("loss_mask")
        != {"system": False, "user": False, "assistant": True, "tool": False}
        or config.get("optim", {}).get("lr") != 5e-7
        or config.get("ckpt", {}).get("resume_step") != 24
        or config.get("ckpt", {}).get("skip_optimizer") is not True
        or config.get("ckpt", {}).get("skip_scheduler") is not True
        or config.get("ckpt", {}).get("skip_dataloader") is not True
        or config.get("ckpt", {}).get("skip_progress") is not False
        or adapter_config.get("r") != 64
        or adapter_config.get("lora_alpha") != 128.0
        or sorted(adapter_config.get("target_modules") or []) != targets
    ):
        raise IntegrityError("Sol DAgger one-update SFT configuration changed")
    source_identity = _dcp_identity(Path(str(source_dcp.get("path", ""))).resolve())
    final_identity = _dcp_identity(Path(str(final_dcp.get("path", ""))).resolve())
    if any(
        identity[key] != descriptor.get(key)
        for identity, descriptor in (
            (source_identity, source_dcp),
            (final_identity, final_dcp),
        )
        for key in ("path", "files", "bytes", "tree_sha256")
    ):
        raise IntegrityError("Sol DAgger training DCP bytes changed")
    bridge = plan.get("checkpoint_bridge")
    if not isinstance(bridge, Mapping):
        raise IntegrityError("Sol DAgger step-24 checkpoint bridge is absent")
    bridge_source = bridge.get("source") or {}
    bridge_destination = bridge.get("destination") or {}
    bridge_root = Path(str(bridge_destination.get("path", ""))).resolve()
    bridge_identity = _dcp_identity(bridge_root)
    source_root = Path(str(source_identity["path"]))
    source_files = sorted(path for path in source_root.rglob("*") if path.is_file())
    if (
        bridge_source != source_dcp
        or bridge_root != training_output / SOURCE_BRIDGE_RELATIVE
        or any(
            bridge_identity[key] != bridge_destination.get(key)
            or bridge_identity[key] != source_identity[key]
            for key in ("files", "bytes", "tree_sha256")
        )
        or bridge.get("hardlinked_files") != source_identity["files"]
        or bridge.get("all_files_hardlinked") is not True
        or any(
            source.stat().st_dev != (bridge_root / source.relative_to(source_root)).stat().st_dev
            or source.stat().st_ino
            != (bridge_root / source.relative_to(source_root)).stat().st_ino
            for source in source_files
        )
        or Path(str(final_identity["path"]))
        != training_output / FINAL_DCP_RELATIVE
    ):
        raise IntegrityError("Sol DAgger checkpoint continuation bridge changed")
    logs = receipt["logs"]
    trainer = logs.get("trainer")
    if (
        not isinstance(trainer, Mapping)
        or receipt.get("trainer_log_audit")
        != _trainer_log_audit(Path(str(trainer.get("path", ""))).resolve())
    ):
        raise IntegrityError("Sol DAgger training receipt lacks one-update log evidence")
    for descriptor in logs.values():
        if not isinstance(descriptor, Mapping):
            raise IntegrityError("Sol DAgger training log descriptor is invalid")
        _audit_log_descriptor(descriptor)
    return receipt, plan, collection


def _runtime_identity(
    job_path: Path, pods_path: Path, *, expected_job: str, require_ready: bool
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    job_name, pod_name = _serve_names(expected_job)
    job = read_json(job_path.resolve())
    pods = read_json(pods_path.resolve())
    items = pods.get("items") if isinstance(pods, dict) else None
    if not isinstance(items, list) or len(items) != 1:
        raise IntegrityError("serve reservation must have exactly one pod")
    pod = items[0]
    statuses = pod.get("status", {}).get("containerStatuses") or []
    container = statuses[0] if len(statuses) == 1 else {}
    pod_containers = pod.get("spec", {}).get("containers") or []
    pod_container = pod_containers[0] if len(pod_containers) == 1 else {}
    tasks = job.get("spec", {}).get("tasks") or []
    task = tasks[0] if len(tasks) == 1 else {}
    job_containers = task.get("template", {}).get("spec", {}).get("containers") or []
    job_container = job_containers[0] if len(job_containers) == 1 else {}
    job_selector = task.get("template", {}).get("spec", {}).get("nodeSelector") or {}
    pod_selector = pod.get("spec", {}).get("nodeSelector") or {}
    owners = pod.get("metadata", {}).get("ownerReferences") or []
    owner = owners[0] if len(owners) == 1 else {}
    image_id = str(
        container.get(
            "imageID", pod.get("spec", {}).get("containers", [{}])[0].get("image", "")
        )
    )
    if (
        job.get("metadata", {}).get("name") != job_name
        or pod.get("metadata", {}).get("name") != pod_name
        or not job.get("metadata", {}).get("uid")
        or not pod.get("metadata", {}).get("uid")
        or task.get("replicas") != 1
        or not _four_b200_resources(job_container, job_selector)
        or not _four_b200_resources(pod_container, pod_selector)
        or not str(job_container.get("image", "")).endswith("@" + IMAGE_DIGEST)
        or not str(pod_container.get("image", "")).endswith("@" + IMAGE_DIGEST)
        or owner.get("name") != job_name
        or owner.get("uid") != job.get("metadata", {}).get("uid")
        or owner.get("controller") is not True
        or not pod.get("spec", {}).get("nodeName")
        or container.get("restartCount") != 0
        or not image_id.endswith("@" + IMAGE_DIGEST)
    ):
        raise IntegrityError("Sol DAgger serve reservation identity changed")
    if require_ready and not (
        job.get("status", {}).get("state", {}).get("phase") == "Running"
        and pod.get("status", {}).get("phase") == "Running"
        and container.get("ready") is True
        and container.get("started") is True
    ):
        raise IntegrityError("Sol DAgger serve endpoint is not ready")
    return job, pod, container


def _lineage_binding(
    *,
    validator: Mapping[str, Any],
    artifacts: Mapping[str, Any],
    candidate: Mapping[str, Any],
    training: Mapping[str, Any],
    collection: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> str:
    """Bind the complete PVC validation projection independently of its envelope."""

    return sha256_bytes(
        canonical_bytes(
            {
                "validator": validator,
                "artifacts": artifacts,
                "candidate": candidate,
                "training": training,
                "collection": collection,
                "runtime": runtime,
            }
        )
    )


def attest_lineage(arguments: argparse.Namespace) -> None:
    """Validate all large training artifacts on the PVC and seal a host-verifiable proof."""

    source = arguments.source_root.resolve()
    if (
        HEX40.fullmatch(arguments.source_git_sha) is None
        or not _is_within(source, ALLOWED_SOURCE_ROOTS)
    ):
        raise IntegrityError("lineage validator source identity/path is invalid")
    module = (source / "src/caveat_27b_eval/sol_dagger_candidate_serve.py").resolve()
    try:
        module.relative_to(source)
    except ValueError as exc:
        raise IntegrityError("lineage validator module escaped staged source") from exc
    _safe_regular(module, "lineage validator module")
    if Path(__file__).resolve() != module:
        raise IntegrityError("lineage validator is not executing from the staged source")
    job_name, pod_name = _serve_names(arguments.expected_job)
    job, pod, container = _runtime_identity(
        arguments.serve_job_json,
        arguments.serve_pods_json,
        expected_job=job_name,
        require_ready=False,
    )
    if (
        os.environ.get("POD_UID") != pod["metadata"]["uid"]
        or os.environ.get("POD_NAME", os.environ.get("HOSTNAME")) != pod_name
        or os.environ.get("HOSTNAME") != pod_name
    ):
        raise IntegrityError("lineage validator is not executing in the bound serving pod")
    parent = validate_repair_parent(arguments.parent_receipt)
    receipt, plan, collection_value = validate_training(
        arguments.training_receipt,
        arguments.collection_manifest,
        arguments.parent_receipt,
    )
    trained = receipt["candidate"]
    composite = exact_lora_composite_sha256(
        parent_tree_sha256=PARENT_TREE,
        adapter_tree_sha256=trained["tree_sha256"],
        adapter_config_sha256=trained["adapter_config_sha256"],
        tokenizer_json_sha256=TOKENIZER_JSON,
        chat_template_sha256=CHAT_TEMPLATE,
        dtype="bfloat16",
    )
    artifacts = {
        "repair_parent_receipt": _artifact(
            arguments.parent_receipt.resolve(), "receipt_body_sha256"
        ),
        "collection_manifest": _artifact(
            arguments.collection_manifest.resolve(), "manifest_body_sha256"
        ),
        "training_plan": _artifact(
            Path(receipt["plan_path"]).resolve(), "plan_body_sha256"
        ),
        "training_receipt": _artifact(
            arguments.training_receipt.resolve(), "receipt_body_sha256"
        ),
    }
    candidate = {
        "name": "step25-sol-dagger-sft",
        "update": 25,
        "adapter_path": trained["path"],
        "adapter_files": trained["files"],
        "adapter_bytes": trained["bytes"],
        "adapter_tree_sha256": trained["tree_sha256"],
        "adapter_config_sha256": trained["adapter_config_sha256"],
        "stable_marker_sha256": trained["stable_marker_sha256"],
        "parent_path": str(PARENT_PATH),
        "parent_tree_sha256": PARENT_TREE,
        "tokenizer_json_sha256": TOKENIZER_JSON,
        "chat_template_sha256": CHAT_TEMPLATE,
        "dtype": "bfloat16",
        "composite_sha256": composite,
    }
    training = {
        "scientific_label": plan["scientific_label"],
        "collection_source_git_sha": collection_value["source_git_sha"],
        "trainer_source_git_sha": receipt["executor_git_sha"],
        "teacher_model": TEACHER_MODEL,
        "teacher_reasoning_effort": TEACHER_EFFORT,
        "source_step": 24,
        "final_step": 25,
        "optimizer_updates": 1,
        "fresh_optimizer": True,
        "optimizer_continuation": False,
        "assistant_tokens_only": True,
        "topology": plan["topology"],
        "active_token_loss_mass": receipt["active_token_loss_mass"],
        "source_dcp": receipt["source_dcp"],
        "final_dcp": receipt["final_dcp"],
        "final_dcp_tree_sha256": receipt["final_dcp"]["tree_sha256"],
        "checkpoint_bridge": {
            "source_tree_sha256": plan["checkpoint_bridge"]["source"]["tree_sha256"],
            "destination_tree_sha256": plan["checkpoint_bridge"]["destination"][
                "tree_sha256"
            ],
            "hardlinked_files": plan["checkpoint_bridge"]["hardlinked_files"],
            "all_files_hardlinked": plan["checkpoint_bridge"]["all_files_hardlinked"],
        },
        "candidate_tree_sha256": trained["tree_sha256"],
        "adapter_config_sha256": trained["adapter_config_sha256"],
        "stable_marker_sha256": trained["stable_marker_sha256"],
        "training_plan_file_sha256": artifacts["training_plan"]["file_sha256"],
        "training_plan_body_sha256": artifacts["training_plan"]["body_sha256"],
        "training_receipt_file_sha256": artifacts["training_receipt"]["file_sha256"],
        "training_receipt_body_sha256": artifacts["training_receipt"]["body_sha256"],
    }
    collection = {
        "source_git_sha": collection_value["source_git_sha"],
        "teacher_model": collection_value["teacher_model"],
        "teacher_reasoning_effort": collection_value["teacher_reasoning_effort"],
        "train_corrective_count": collection_value["train_corrective_count"],
        "retention_count": collection_value["row_balance"]["retention"],
        "heldout_count": collection_value["heldout_count"],
        "harness_fingerprint_sha256": collection_value["invariance"][
            "harness_fingerprint_sha256"
        ],
        "evaluation_fingerprint_sha256": collection_value["invariance"][
            "evaluation_fingerprint_sha256"
        ],
        "parent_final_dcp_tree_sha256": parent["final_dcp"]["tree_sha256"],
        "manifest_file_sha256": artifacts["collection_manifest"]["file_sha256"],
        "manifest_body_sha256": artifacts["collection_manifest"]["body_sha256"],
    }
    source_identity = _source_identity(source)
    validator = {
        "source": {
            "root": str(source),
            "git_sha": arguments.source_git_sha,
            **source_identity,
        },
        "module": {"path": str(module), "sha256": sha256_file(module)},
        "bound_files": _bound_source_files(source),
    }
    runtime = {
        "job": job_name,
        "job_uid": job["metadata"]["uid"],
        "job_spec_sha256": sha256_bytes(canonical_bytes(job["spec"])),
        "job_labels_sha256": sha256_bytes(
            canonical_bytes(job["metadata"].get("labels", {}))
        ),
        "pod": pod_name,
        "pod_uid": pod["metadata"]["uid"],
        "node": pod["spec"]["nodeName"],
        "image_id": container.get("imageID"),
        "restart_count": container.get("restartCount"),
        "executed_in_bound_pod": True,
    }
    core = {
        "schema": LINEAGE_SCHEMA,
        "status": "verified_on_pvc",
        "outcome_blind": True,
        "evaluation_harness_modified": False,
        "large_artifacts_rehashed_on_pvc": True,
        "validator": validator,
        "artifacts": artifacts,
        "candidate": candidate,
        "training": training,
        "collection": collection,
        "runtime": runtime,
        "lineage_binding_sha256": _lineage_binding(
            validator=validator,
            artifacts=artifacts,
            candidate=candidate,
            training=training,
            collection=collection,
            runtime=runtime,
        ),
    }
    value = {**core, "attestation_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(arguments.output.resolve(), value)
    print(
        json.dumps(
            {
                "status": "attested",
                "attestation_sha256": value["attestation_sha256"],
                "candidate_tree_sha256": candidate["adapter_tree_sha256"],
                "final_dcp_tree_sha256": training["final_dcp"]["tree_sha256"],
            }
        )
    )


def validate_lineage_attestation(path: Path) -> dict[str, Any]:
    """Validate a copied PVC attestation without dereferencing its remote paths."""

    value = _descriptor(
        path.resolve(), LINEAGE_SCHEMA, "attestation_sha256", "PVC lineage attestation"
    )
    validator = value.get("validator") or {}
    source = validator.get("source") or {}
    module = validator.get("module") or {}
    bound_files = validator.get("bound_files") or {}
    artifacts = value.get("artifacts") or {}
    candidate = value.get("candidate") or {}
    training = value.get("training") or {}
    collection = value.get("collection") or {}
    runtime = value.get("runtime") or {}
    job_name, pod_name = _serve_names(runtime.get("job"))
    expected_artifacts = {
        "repair_parent_receipt",
        "collection_manifest",
        "training_plan",
        "training_receipt",
    }
    if not isinstance(artifacts, Mapping) or set(artifacts) != expected_artifacts:
        raise IntegrityError("PVC lineage artifact inventory changed")
    for label, descriptor in artifacts.items():
        if (
            not isinstance(descriptor, Mapping)
            or set(descriptor) != {"path", "file_sha256", "body_sha256"}
        ):
            raise IntegrityError(f"PVC lineage {label} descriptor changed")
        _sha(descriptor.get("file_sha256"), f"PVC lineage {label} file")
        _sha(descriptor.get("body_sha256"), f"PVC lineage {label} body")
    artifact_paths = {
        label: Path(str(descriptor["path"])).resolve()
        for label, descriptor in artifacts.items()
    }
    campaign, campaign_source_prefix = _training_campaign(
        artifact_paths["training_receipt"]
    )
    if artifact_paths != {
        "repair_parent_receipt": REPAIR_RECEIPT_PATH,
        "collection_manifest": COLLECTION_MANIFEST_PATH,
        "training_plan": campaign / "training_r1/training/plan.json",
        "training_receipt": campaign / "training_r1/training/training_receipt.json",
    }:
        raise IntegrityError("PVC lineage artifact paths changed")
    for field in (
        "adapter_tree_sha256",
        "adapter_config_sha256",
        "stable_marker_sha256",
        "parent_tree_sha256",
        "tokenizer_json_sha256",
        "chat_template_sha256",
        "composite_sha256",
    ):
        _sha(candidate.get(field), f"PVC lineage candidate.{field}")
    source_dcp = training.get("source_dcp") or {}
    final_dcp = training.get("final_dcp") or {}
    for label, descriptor in (("source DCP", source_dcp), ("final DCP", final_dcp)):
        if (
            not isinstance(descriptor, Mapping)
            or set(descriptor) != {"path", "files", "bytes", "tree_sha256"}
            or type(descriptor.get("files")) is not int
            or descriptor["files"] != 9
            or type(descriptor.get("bytes")) is not int
            or descriptor["bytes"] <= 0
        ):
            raise IntegrityError(f"PVC lineage {label} descriptor changed")
        _sha(descriptor.get("tree_sha256"), f"PVC lineage {label} tree")
    bridge = training.get("checkpoint_bridge") or {}
    mass = training.get("active_token_loss_mass") or {}
    corrective_mass = mass.get("corrective")
    retention_mass = mass.get("retention")
    valid_mass = (
        type(corrective_mass) is int
        and type(retention_mass) is int
        and retention_mass > 0
        and 1.5 <= corrective_mass / retention_mass <= 2.5
    )
    expected_composite = exact_lora_composite_sha256(
        parent_tree_sha256=PARENT_TREE,
        adapter_tree_sha256=str(candidate.get("adapter_tree_sha256")),
        adapter_config_sha256=str(candidate.get("adapter_config_sha256")),
        tokenizer_json_sha256=TOKENIZER_JSON,
        chat_template_sha256=CHAT_TEMPLATE,
        dtype="bfloat16",
    )
    binding = _lineage_binding(
        validator=validator,
        artifacts=artifacts,
        candidate=candidate,
        training=training,
        collection=collection,
        runtime=runtime,
    )
    source_root = Path(str(source.get("root", "")))
    module_path = Path(str(module.get("path", "")))
    expected_module = source_root / "src/caveat_27b_eval/sol_dagger_candidate_serve.py"
    if (
        value.get("status") != "verified_on_pvc"
        or value.get("outcome_blind") is not True
        or value.get("evaluation_harness_modified") is not False
        or value.get("large_artifacts_rehashed_on_pvc") is not True
        or value.get("lineage_binding_sha256") != binding
        or not isinstance(validator, Mapping)
        or set(validator) != {"source", "module", "bound_files"}
        or not isinstance(source, Mapping)
        or set(source) != {"root", "git_sha", "files", "bytes", "tree_sha256"}
        or HEX40.fullmatch(str(source.get("git_sha", ""))) is None
        or source_root.resolve() != source_root
        or not _is_within(source_root, ALLOWED_SOURCE_ROOTS)
        or type(source.get("files")) is not int
        or source["files"] <= 0
        or type(source.get("bytes")) is not int
        or source["bytes"] <= 0
        or HEX64.fullmatch(str(source.get("tree_sha256", ""))) is None
        or not isinstance(module, Mapping)
        or set(module) != {"path", "sha256"}
        or module_path.resolve() != module_path
        or module_path != expected_module
        or module.get("sha256") != sha256_file(Path(__file__).resolve())
        or bound_files != _bound_source_files(Path(__file__).resolve().parents[2])
        or artifacts["repair_parent_receipt"]["file_sha256"] != REPAIR_RECEIPT_FILE
        or artifacts["repair_parent_receipt"]["body_sha256"] != REPAIR_RECEIPT_BODY
        or artifacts["collection_manifest"]["file_sha256"]
        != COLLECTION_MANIFEST_FILE
        or artifacts["collection_manifest"]["body_sha256"]
        != COLLECTION_MANIFEST_BODY
        or candidate.get("name") != "step25-sol-dagger-sft"
        or candidate.get("update") != 25
        or Path(str(candidate.get("adapter_path", ""))).resolve()
        != campaign / TRAINING_OUTPUT_RELATIVE / CANDIDATE_RELATIVE
        or type(candidate.get("adapter_files")) is not int
        or candidate["adapter_files"] <= 0
        or type(candidate.get("adapter_bytes")) is not int
        or candidate["adapter_bytes"] <= 0
        or Path(str(candidate.get("parent_path", ""))).resolve() != PARENT_PATH
        or candidate.get("parent_tree_sha256") != PARENT_TREE
        or candidate.get("tokenizer_json_sha256") != TOKENIZER_JSON
        or candidate.get("chat_template_sha256") != CHAT_TEMPLATE
        or candidate.get("dtype") != "bfloat16"
        or candidate.get("composite_sha256") != expected_composite
        or training.get("scientific_label") != "sol_to_qwen_receipt_bound_dagger_sft"
        or HEX40.fullmatch(str(training.get("collection_source_git_sha", ""))) is None
        or HEX40.fullmatch(str(training.get("trainer_source_git_sha", ""))) is None
        or str(training.get("trainer_source_git_sha", ""))[
            : len(campaign_source_prefix)
        ]
        != campaign_source_prefix
        or training.get("teacher_model") != TEACHER_MODEL
        or training.get("teacher_reasoning_effort") != TEACHER_EFFORT
        or training.get("source_step") != 24
        or training.get("final_step") != 25
        or training.get("optimizer_updates") != 1
        or training.get("fresh_optimizer") is not True
        or training.get("optimizer_continuation") is not False
        or training.get("assistant_tokens_only") is not True
        or training.get("topology") != {"dp_shards": 1, "cp": 4, "gpus": 4}
        or not valid_mass
        or source_dcp.get("tree_sha256") != REPAIR_DCP_TREE
        or Path(str(source_dcp.get("path", ""))).resolve() != REPAIR_DCP_PATH
        or final_dcp.get("tree_sha256") == REPAIR_DCP_TREE
        or training.get("final_dcp_tree_sha256") != final_dcp.get("tree_sha256")
        or Path(str(final_dcp.get("path", ""))).resolve()
        != campaign / TRAINING_OUTPUT_RELATIVE / FINAL_DCP_RELATIVE
        or bridge
        != {
            "source_tree_sha256": REPAIR_DCP_TREE,
            "destination_tree_sha256": REPAIR_DCP_TREE,
            "hardlinked_files": 9,
            "all_files_hardlinked": True,
        }
        or training.get("candidate_tree_sha256") != candidate.get("adapter_tree_sha256")
        or training.get("adapter_config_sha256") != candidate.get("adapter_config_sha256")
        or training.get("stable_marker_sha256") != candidate.get("stable_marker_sha256")
        or training.get("training_plan_file_sha256")
        != artifacts["training_plan"]["file_sha256"]
        or training.get("training_plan_body_sha256")
        != artifacts["training_plan"]["body_sha256"]
        or training.get("training_receipt_file_sha256")
        != artifacts["training_receipt"]["file_sha256"]
        or training.get("training_receipt_body_sha256")
        != artifacts["training_receipt"]["body_sha256"]
        or collection.get("source_git_sha") != training.get("collection_source_git_sha")
        or collection.get("source_git_sha") != COLLECTION_SOURCE_GIT
        or collection.get("teacher_model") != TEACHER_MODEL
        or collection.get("teacher_reasoning_effort") != TEACHER_EFFORT
        or collection.get("train_corrective_count") != 64
        or collection.get("retention_count") != 32
        or collection.get("heldout_count") != 24
        or collection.get("parent_final_dcp_tree_sha256") != REPAIR_DCP_TREE
        or collection.get("manifest_file_sha256")
        != artifacts["collection_manifest"]["file_sha256"]
        or collection.get("manifest_body_sha256")
        != artifacts["collection_manifest"]["body_sha256"]
        or collection.get("harness_fingerprint_sha256")
        != COLLECTION_HARNESS_FINGERPRINT
        or collection.get("evaluation_fingerprint_sha256")
        != COLLECTION_EVALUATION_FINGERPRINT
        or runtime.get("job") != job_name
        or runtime.get("pod") != pod_name
        or not runtime.get("job_uid")
        or not runtime.get("pod_uid")
        or not runtime.get("node")
        or not str(runtime.get("image_id", "")).endswith("@" + IMAGE_DIGEST)
        or runtime.get("restart_count") != 0
        or runtime.get("executed_in_bound_pod") is not True
    ):
        raise IntegrityError("PVC lineage attestation policy or binding changed")
    return value


def _release_argv_from_lineage(
    release: Mapping[str, Any], lineage: Mapping[str, Any], *, pod_uid: str
) -> list[str]:
    artifacts = lineage["artifacts"]
    candidate = lineage["candidate"]
    job_name, pod_name = _serve_names(lineage["runtime"]["job"])
    return [
        str(release["entrypoint"]["sha256"]),
        str(artifacts["repair_parent_receipt"]["path"]),
        str(artifacts["repair_parent_receipt"]["file_sha256"]),
        str(artifacts["repair_parent_receipt"]["body_sha256"]),
        str(artifacts["collection_manifest"]["path"]),
        str(artifacts["collection_manifest"]["file_sha256"]),
        str(artifacts["collection_manifest"]["body_sha256"]),
        str(artifacts["training_receipt"]["path"]),
        str(artifacts["training_receipt"]["file_sha256"]),
        str(artifacts["training_receipt"]["body_sha256"]),
        str(candidate["adapter_tree_sha256"]),
        str(candidate["adapter_config_sha256"]),
        str(candidate["stable_marker_sha256"]),
        job_name,
        pod_name,
        pod_uid,
    ]


def _release_argv(
    *,
    entrypoint: Path,
    parent_path: Path,
    collection_path: Path,
    training_path: Path,
    parent: Mapping[str, Any],
    collection: Mapping[str, Any],
    receipt: Mapping[str, Any],
    job_name: str,
    pod_name: str,
    pod_uid: str,
) -> list[str]:
    candidate = receipt["candidate"]
    return [
        sha256_file(entrypoint),
        str(parent_path.resolve()),
        sha256_file(parent_path.resolve()),
        str(parent["receipt_body_sha256"]),
        str(collection_path.resolve()),
        sha256_file(collection_path.resolve()),
        str(collection["manifest_body_sha256"]),
        str(training_path.resolve()),
        sha256_file(training_path.resolve()),
        str(receipt["receipt_body_sha256"]),
        str(candidate["tree_sha256"]),
        str(candidate["adapter_config_sha256"]),
        str(candidate["stable_marker_sha256"]),
        job_name,
        pod_name,
        pod_uid,
    ]


def render_release(arguments: argparse.Namespace) -> None:
    job_name, pod_name = _serve_names(arguments.expected_job)
    source = arguments.source_root.resolve()
    if (
        HEX40.fullmatch(arguments.source_git_sha) is None
        or not _is_within(source, ALLOWED_SOURCE_ROOTS)
    ):
        raise IntegrityError("staged source identity/path is invalid")
    entrypoint = (source / ENTRYPOINT_RELATIVE).resolve()
    try:
        entrypoint.relative_to(source)
    except ValueError as exc:
        raise IntegrityError("serve entrypoint escaped staged source") from exc
    _safe_regular(entrypoint, "serve entrypoint")
    parent = validate_repair_parent(arguments.parent_receipt)
    receipt, _plan, collection = validate_training(
        arguments.training_receipt,
        arguments.collection_manifest,
        arguments.parent_receipt,
    )
    job, pod, _container = _runtime_identity(
        arguments.serve_job_json,
        arguments.serve_pods_json,
        expected_job=job_name,
        require_ready=False,
    )
    identity = _source_identity(source)
    pod_uid = str(pod["metadata"]["uid"])
    core = {
        "schema": SERVE_RELEASE_SCHEMA,
        "status": "released",
        "purpose": PURPOSE,
        "laptop_r01_outcomes_read": False,
        "office_chair_outcomes_read": False,
        "reservation": {
            "job_name": job_name,
            "pod_name": pod_name,
            "pod_uid": pod_uid,
        },
        "source": {
            "root": str(source),
            "git_sha": arguments.source_git_sha,
            **identity,
        },
        "entrypoint": {"path": str(entrypoint), "sha256": sha256_file(entrypoint)},
        "argv": _release_argv(
            entrypoint=entrypoint,
            parent_path=arguments.parent_receipt,
            collection_path=arguments.collection_manifest,
            training_path=arguments.training_receipt,
            parent=parent,
            collection=collection,
            receipt=receipt,
            job_name=job_name,
            pod_name=pod_name,
            pod_uid=pod_uid,
        ),
    }
    release = {**core, "release_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(arguments.output.resolve(), release)
    print(
        json.dumps(
            {
                "status": "rendered",
                "release_sha256": release["release_sha256"],
                "candidate_tree_sha256": receipt["candidate"]["tree_sha256"],
                "serve_job_uid": job["metadata"]["uid"],
            }
        )
    )


def audit_release(path: Path, *, verify_source: bool = True) -> dict[str, Any]:
    value = _descriptor(path.resolve(), SERVE_RELEASE_SCHEMA, "release_sha256", "serve release")
    if set(value) != GENERIC_RELEASE_KEYS:
        raise IntegrityError("generic serve release fields changed")
    reservation = value.get("reservation")
    source = value.get("source")
    entrypoint = value.get("entrypoint")
    argv = value.get("argv")
    if not isinstance(reservation, dict):
        raise IntegrityError("generic Sol DAgger serve reservation changed")
    job_name, pod_name = _serve_names(reservation.get("job_name"))
    source_root = Path(str(source.get("root", ""))) if isinstance(source, dict) else Path("")
    program = (
        Path(str(entrypoint.get("path", "")))
        if isinstance(entrypoint, dict)
        else Path("")
    )
    if (
        value.get("status") != "released"
        or value.get("purpose") != PURPOSE
        or value.get("laptop_r01_outcomes_read") is not False
        or value.get("office_chair_outcomes_read") is not False
        or reservation.get("pod_name") != pod_name
        or not reservation.get("pod_uid")
        or not isinstance(source, dict)
        or set(source) != {"root", "git_sha", "files", "bytes", "tree_sha256"}
        or HEX40.fullmatch(str(source.get("git_sha", ""))) is None
        or HEX64.fullmatch(str(source.get("tree_sha256", ""))) is None
        or source_root.resolve() != source_root
        or not _is_within(source_root, ALLOWED_SOURCE_ROOTS)
        or not isinstance(entrypoint, dict)
        or set(entrypoint) != {"path", "sha256"}
        or HEX64.fullmatch(str(entrypoint.get("sha256", ""))) is None
        or program.resolve() != program
        or program != source_root / ENTRYPOINT_RELATIVE
        or not isinstance(argv, list)
        or len(argv) != 16
        or not all(isinstance(item, str) and item and "\x00" not in item for item in argv)
        or argv[13:] != [job_name, pod_name, reservation.get("pod_uid")]
        or argv[0] != entrypoint.get("sha256")
        or argv[1] == argv[4]
        or argv[4] == argv[7]
    ):
        raise IntegrityError("generic Sol DAgger serve release policy changed")
    for index in (2, 3, 5, 6, 8, 9, 10, 11, 12):
        _sha(argv[index], f"serve release argv[{index}]")
    if verify_source and (
        _source_identity(source_root)
        != {
            key: source[key] for key in ("files", "bytes", "tree_sha256")
        }
        or sha256_file(_safe_regular(program, "serve entrypoint"))
        != entrypoint["sha256"]
    ):
        raise IntegrityError("serve release staged source identity changed")
    return value


def _validated_server_contract(argv: Sequence[str]) -> tuple[Path, Path, str]:
    if len(argv) != 15:
        raise IntegrityError("serve runner expected fifteen lineage/runtime arguments")
    (
        parent_raw,
        parent_file,
        parent_body,
        collection_raw,
        collection_file,
        collection_body,
        training_raw,
        training_file,
        training_body,
        candidate_tree,
        adapter_config,
        stable_marker,
        job_name,
        pod_name,
        pod_uid,
    ) = argv
    expected_job, expected_pod = _serve_names(job_name)
    parent_path = Path(parent_raw).resolve()
    collection_path = Path(collection_raw).resolve()
    training_path = Path(training_raw).resolve()
    campaign, _source_prefix = _training_campaign(training_path)
    collection_campaign, _collection_source_prefix = _training_campaign(collection_path)
    if (
        parent_path != REPAIR_RECEIPT_PATH
        or collection_path
        != collection_campaign / "collection_r1/collection_manifest.json"
        or training_path
        != campaign / "training_r1/training/training_receipt.json"
    ):
        raise IntegrityError("serve runner lineage paths changed")
    parent = validate_repair_parent(parent_path)
    receipt, _plan, collection = validate_training(
        training_path, collection_path, parent_path
    )
    candidate = receipt["candidate"]
    expected = (
        sha256_file(parent_path),
        parent["receipt_body_sha256"],
        sha256_file(collection_path),
        collection["manifest_body_sha256"],
        sha256_file(training_path),
        receipt["receipt_body_sha256"],
        candidate["tree_sha256"],
        candidate["adapter_config_sha256"],
        candidate["stable_marker_sha256"],
        expected_job,
        expected_pod,
        os.environ.get("POD_UID"),
    )
    observed = (
        parent_file,
        parent_body,
        collection_file,
        collection_body,
        training_file,
        training_body,
        candidate_tree,
        adapter_config,
        stable_marker,
        job_name,
        pod_name,
        pod_uid,
    )
    if (
        observed != expected
        or os.environ.get("POD_NAME", os.environ.get("HOSTNAME")) != expected_pod
        or os.environ.get("HOSTNAME") != expected_pod
    ):
        raise IntegrityError("serve runner receipt or pod binding changed")
    parent_identity = _tree_identity(PARENT_PATH)
    if parent_identity["tree_sha256"] != PARENT_TREE:
        raise IntegrityError("selected/merged parent model tree changed")
    adapter = Path(str(candidate["path"])).resolve()
    alias = f"caveat-27b-sol-dagger-step25-{candidate_tree[:12]}-exact-lora"
    return PARENT_PATH, adapter, alias


def build_vllm_argv(parent: Path, adapter: Path, alias: str) -> list[str]:
    return [
        "vllm",
        "serve",
        str(parent),
        "--tokenizer",
        str(parent),
        "--served-model-name",
        "caveat-27b-parent-exact-lora",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--dtype",
        "bfloat16",
        "--generation-config",
        "vllm",
        "--language-model-only",
        "--max-model-len",
        "32768",
        "--reasoning-parser",
        "qwen3",
        "--tool-call-parser",
        "qwen3_coder",
        "--enable-auto-tool-choice",
        "--no-enable-prefix-caching",
        "--no-enable-log-requests",
        "--enable-lora",
        "--max-loras",
        "1",
        "--max-cpu-loras",
        "1",
        "--max-lora-rank",
        "64",
        "--lora-dtype",
        "bfloat16",
        "--lora-modules",
        f"{alias}={adapter}",
        "--data-parallel-size",
        "4",
        "--api-server-count",
        "4",
    ]


def run_server(arguments: argparse.Namespace) -> None:
    parent, adapter, alias = _validated_server_contract(arguments.lineage)
    environment = dict(os.environ)
    environment.pop("VLLM_ALLOW_RUNTIME_LORA_UPDATING", None)
    environment.pop("VLLM_API_KEY", None)
    environment["FLA_TILELANG"] = "0"
    command = build_vllm_argv(parent, adapter, alias)
    os.execvpe(command[0], command, environment)


def _process_start_ticks(pid: int) -> str:
    if type(pid) is not int or pid <= 1:
        raise IntegrityError("tunnel PID is invalid")
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
    except (OSError, IndexError) as exc:
        raise IntegrityError(f"cannot inspect tunnel process {pid}") from exc


def attest_endpoint(arguments: argparse.Namespace) -> None:
    lineage = validate_lineage_attestation(arguments.lineage_attestation)
    release = audit_release(arguments.serve_release, verify_source=False)
    job_name, pod_name = _serve_names(release["reservation"]["job_name"])
    job, pod, container = _runtime_identity(
        arguments.serve_job_json,
        arguments.serve_pods_json,
        expected_job=job_name,
        require_ready=True,
    )
    if (
        release["reservation"]
        != {
            "job_name": job_name,
            "pod_name": pod_name,
            "pod_uid": pod["metadata"]["uid"],
        }
        or lineage["runtime"]["job"] != job_name
        or lineage["runtime"]["job_uid"] != job["metadata"]["uid"]
        or lineage["runtime"]["job_spec_sha256"]
        != sha256_bytes(canonical_bytes(job["spec"]))
        or lineage["runtime"]["job_labels_sha256"]
        != sha256_bytes(canonical_bytes(job["metadata"].get("labels", {})))
        or lineage["runtime"]["pod"] != pod_name
        or lineage["runtime"]["pod_uid"] != pod["metadata"]["uid"]
        or lineage["runtime"]["node"] != pod["spec"]["nodeName"]
        or lineage["runtime"]["image_id"] != container.get("imageID")
        or lineage["runtime"]["restart_count"] != container.get("restartCount")
        or release["source"] != lineage["validator"]["source"]
        or release["argv"]
        != _release_argv_from_lineage(
            release, lineage, pod_uid=str(pod["metadata"]["uid"])
        )
    ):
        raise IntegrityError("serve release, PVC lineage, and live pod do not match")
    candidate = lineage["candidate"]
    training = lineage["training"]
    alias = (
        "caveat-27b-sol-dagger-step25-"
        f"{candidate['adapter_tree_sha256'][:12]}-exact-lora"
    )
    models = read_json(arguments.models.resolve())
    canary = read_json(arguments.canary.resolve())
    model_rows = models.get("data") if isinstance(models, dict) else None
    model_ids = sorted(
        str(row.get("id")) for row in model_rows or [] if isinstance(row, dict)
    )
    choices = canary.get("choices") if isinstance(canary, dict) else None
    choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else {}
    if (
        model_ids != sorted(["caveat-27b-parent-exact-lora", alias])
        or canary.get("model") != alias
        or choice.get("finish_reason") != "stop"
        or str(choice.get("message", {}).get("content", "")).strip() != "OK"
    ):
        raise IntegrityError("Sol DAgger endpoint API canary changed")
    tunnel = read_json(arguments.tunnel_status.resolve())
    pid = tunnel.get("pid")
    tunnel_argv = tunnel.get("argv")
    if (
        tunnel.get("process_start_ticks") != _process_start_ticks(pid)
        or not isinstance(tunnel_argv, list)
        or tunnel_argv[-2:] != [f"pod/{pod_name}", LOCAL_TUNNEL]
    ):
        raise IntegrityError("Sol DAgger endpoint tunnel identity changed")
    composite = exact_lora_composite_sha256(
        parent_tree_sha256=PARENT_TREE,
        adapter_tree_sha256=candidate["adapter_tree_sha256"],
        adapter_config_sha256=candidate["adapter_config_sha256"],
        tokenizer_json_sha256=TOKENIZER_JSON,
        chat_template_sha256=CHAT_TEMPLATE,
        dtype="bfloat16",
    )
    core = {
        "schema": ENDPOINT_SCHEMA,
        "status": "ok",
        "outcome_blind": True,
        "evaluation_harness_modified": False,
        "candidate": {
            "name": "step25-sol-dagger-sft",
            "update": 25,
            "adapter_path": candidate["adapter_path"],
            "parent_path": str(PARENT_PATH),
            "parent_tree_sha256": PARENT_TREE,
            "adapter_tree_sha256": candidate["adapter_tree_sha256"],
            "adapter_config_sha256": candidate["adapter_config_sha256"],
            "stable_marker_sha256": candidate["stable_marker_sha256"],
            "tokenizer_json_sha256": TOKENIZER_JSON,
            "chat_template_sha256": CHAT_TEMPLATE,
            "dtype": "bfloat16",
            "composite_sha256": composite,
            "served_model_name": alias,
        },
        "model_spec": {
            "provider": "openai",
            "name": alias,
            "deployment": alias,
            "base_url": LOCAL_BASE_URL,
            "api_key": "env:CAVEAT_27B_API_KEY",
            "vision": False,
            "extra": {"frequency_penalty": None},
        },
        "artifacts": {
            "pvc_lineage_attestation": _artifact(
                arguments.lineage_attestation.resolve(), "attestation_sha256"
            ),
            **lineage["artifacts"],
            "serve_release": _artifact(
                arguments.serve_release.resolve(), "release_sha256"
            ),
        },
        "training": {
            "lineage_attestation_sha256": lineage["attestation_sha256"],
            "lineage_binding_sha256": lineage["lineage_binding_sha256"],
            "collection_source_git_sha": training["collection_source_git_sha"],
            "trainer_source_git_sha": training["trainer_source_git_sha"],
            "teacher_model": TEACHER_MODEL,
            "teacher_reasoning_effort": TEACHER_EFFORT,
            "source_step": 24,
            "final_step": 25,
            "optimizer_updates": 1,
            "fresh_optimizer": True,
            "optimizer_continuation": False,
            "assistant_tokens_only": True,
            "topology": training["topology"],
            "active_token_loss_mass": training["active_token_loss_mass"],
            "source_dcp_tree_sha256": training["source_dcp"]["tree_sha256"],
            "final_dcp_tree_sha256": training["final_dcp"]["tree_sha256"],
        },
        "runtime": {
            "job": job_name,
            "job_uid": job["metadata"]["uid"],
            "job_spec_sha256": sha256_bytes(canonical_bytes(job["spec"])),
            "job_labels_sha256": sha256_bytes(
                canonical_bytes(job["metadata"].get("labels", {}))
            ),
            "pod": pod_name,
            "pod_uid": pod["metadata"]["uid"],
            "node": pod["spec"]["nodeName"],
            "image_id": container.get("imageID"),
            "tunnel": tunnel,
        },
        "api_evidence": {
            "models_file_sha256": sha256_file(arguments.models.resolve()),
            "canary_file_sha256": sha256_file(arguments.canary.resolve()),
            "served_models": model_ids,
            "finish_reason": "stop",
        },
    }
    endpoint = {**core, "receipt_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(arguments.output.resolve(), endpoint)
    print(
        json.dumps(
            {
                "status": "attested",
                "receipt_sha256": endpoint["receipt_sha256"],
                "candidate_composite_sha256": composite,
                "served_model_name": alias,
            }
        )
    )


def validate_endpoint(path: Path, *, verify_artifacts: bool = True) -> dict[str, Any]:
    value = _descriptor(path.resolve(), ENDPOINT_SCHEMA, "receipt_sha256", "endpoint")
    candidate = value.get("candidate") or {}
    training = value.get("training") or {}
    model = value.get("model_spec") or {}
    runtime = value.get("runtime") or {}
    job_name, pod_name = _serve_names(runtime.get("job"))
    for field in (
        "parent_tree_sha256",
        "adapter_tree_sha256",
        "adapter_config_sha256",
        "stable_marker_sha256",
        "tokenizer_json_sha256",
        "chat_template_sha256",
        "composite_sha256",
    ):
        _sha(candidate.get(field), f"endpoint candidate.{field}")
    expected = exact_lora_composite_sha256(
        parent_tree_sha256=PARENT_TREE,
        adapter_tree_sha256=candidate["adapter_tree_sha256"],
        adapter_config_sha256=candidate["adapter_config_sha256"],
        tokenizer_json_sha256=TOKENIZER_JSON,
        chat_template_sha256=CHAT_TEMPLATE,
        dtype="bfloat16",
    )
    expected_alias = (
        "caveat-27b-sol-dagger-step25-"
        f"{candidate['adapter_tree_sha256'][:12]}-exact-lora"
    )
    expected_model = {
        "provider": "openai",
        "name": expected_alias,
        "deployment": expected_alias,
        "base_url": LOCAL_BASE_URL,
        "api_key": "env:CAVEAT_27B_API_KEY",
        "vision": False,
        "extra": {"frequency_penalty": None},
    }
    if (
        value.get("status") != "ok"
        or value.get("outcome_blind") is not True
        or value.get("evaluation_harness_modified") is not False
        or candidate.get("name") != "step25-sol-dagger-sft"
        or candidate.get("update") != 25
        or Path(str(candidate.get("parent_path", ""))).resolve() != PARENT_PATH
        or candidate.get("parent_tree_sha256") != PARENT_TREE
        or candidate.get("tokenizer_json_sha256") != TOKENIZER_JSON
        or candidate.get("chat_template_sha256") != CHAT_TEMPLATE
        or candidate.get("dtype") != "bfloat16"
        or candidate.get("composite_sha256") != expected
        or candidate.get("served_model_name") != expected_alias
        or model != expected_model
        or training.get("teacher_model") != TEACHER_MODEL
        or training.get("teacher_reasoning_effort") != TEACHER_EFFORT
        or training.get("source_step") != 24
        or training.get("final_step") != 25
        or training.get("optimizer_updates") != 1
        or training.get("fresh_optimizer") is not True
        or training.get("optimizer_continuation") is not False
        or training.get("assistant_tokens_only") is not True
        or training.get("topology") != {"dp_shards": 1, "cp": 4, "gpus": 4}
        or training.get("source_dcp_tree_sha256") != REPAIR_DCP_TREE
        or HEX64.fullmatch(str(training.get("lineage_attestation_sha256", ""))) is None
        or HEX64.fullmatch(str(training.get("lineage_binding_sha256", ""))) is None
        or runtime.get("job") != job_name
        or runtime.get("pod") != pod_name
        or not runtime.get("job_uid")
        or not runtime.get("pod_uid")
        or HEX64.fullmatch(str(runtime.get("job_spec_sha256", ""))) is None
        or HEX64.fullmatch(str(runtime.get("job_labels_sha256", ""))) is None
        or not runtime.get("node")
        or not str(runtime.get("image_id", "")).endswith("@" + IMAGE_DIGEST)
    ):
        raise IntegrityError("Sol DAgger endpoint policy changed")
    if verify_artifacts:
        artifacts = value.get("artifacts") or {}
        remote_fields = {
            "repair_parent_receipt": "receipt_body_sha256",
            "collection_manifest": "manifest_body_sha256",
            "training_plan": "plan_body_sha256",
            "training_receipt": "receipt_body_sha256",
        }
        local_fields = {
            "pvc_lineage_attestation": "attestation_sha256",
            "serve_release": "release_sha256",
        }
        expected_fields = {**remote_fields, **local_fields}
        if not isinstance(artifacts, Mapping) or set(artifacts) != set(expected_fields):
            raise IntegrityError("endpoint artifact inventory changed")
        for label, body_field in local_fields.items():
            record = artifacts.get(label) or {}
            artifact_path = Path(str(record.get("path", ""))).resolve()
            if (
                sha256_file(_safe_regular(artifact_path, label))
                != record.get("file_sha256")
                or read_json(artifact_path).get(body_field) != record.get("body_sha256")
            ):
                raise IntegrityError(f"endpoint-bound {label} changed")
        lineage = validate_lineage_attestation(
            Path(artifacts["pvc_lineage_attestation"]["path"]).resolve()
        )
        release = audit_release(
            Path(artifacts["serve_release"]["path"]).resolve(), verify_source=False
        )
        trained = lineage["candidate"]
        lineage_training = lineage["training"]
        if (
            artifacts["pvc_lineage_attestation"]["body_sha256"]
            != lineage["attestation_sha256"]
            or any(
                artifacts[label] != lineage["artifacts"][label]
                for label in remote_fields
            )
            or candidate.get("adapter_path") != trained.get("adapter_path")
            or any(
                candidate.get(endpoint_field) != trained.get(lineage_field)
                for endpoint_field, lineage_field in (
                    ("adapter_tree_sha256", "adapter_tree_sha256"),
                    ("adapter_config_sha256", "adapter_config_sha256"),
                    ("stable_marker_sha256", "stable_marker_sha256"),
                )
            )
            or training.get("lineage_attestation_sha256")
            != lineage.get("attestation_sha256")
            or training.get("lineage_binding_sha256")
            != lineage.get("lineage_binding_sha256")
            or training.get("collection_source_git_sha")
            != lineage_training.get("collection_source_git_sha")
            or training.get("trainer_source_git_sha")
            != lineage_training.get("trainer_source_git_sha")
            or training.get("active_token_loss_mass")
            != lineage_training.get("active_token_loss_mass")
            or training.get("topology") != lineage_training.get("topology")
            or training.get("source_dcp_tree_sha256")
            != lineage_training.get("source_dcp", {}).get("tree_sha256")
            or training.get("final_dcp_tree_sha256")
            != lineage_training.get("final_dcp", {}).get("tree_sha256")
            or release.get("source") != lineage.get("validator", {}).get("source")
            or any(
                runtime.get(field) != lineage.get("runtime", {}).get(field)
                for field in (
                    "job",
                    "job_uid",
                    "job_spec_sha256",
                    "job_labels_sha256",
                    "pod",
                    "pod_uid",
                    "node",
                    "image_id",
                )
            )
            or release.get("reservation")
            != {
                "job_name": job_name,
                "pod_name": pod_name,
                "pod_uid": runtime.get("pod_uid"),
            }
            or release.get("argv")
            != _release_argv_from_lineage(
                release, lineage, pod_uid=str(runtime["pod_uid"])
            )
        ):
            raise IntegrityError("endpoint lineage no longer matches its bound artifacts")
    return value


def audit_command(arguments: argparse.Namespace) -> None:
    if arguments.kind == "serve-release":
        value = audit_release(arguments.path)
        digest = value["release_sha256"]
    elif arguments.kind == "lineage":
        value = validate_lineage_attestation(arguments.path)
        digest = value["attestation_sha256"]
    else:
        value = validate_endpoint(arguments.path)
        digest = value["receipt_sha256"]
    print(json.dumps({"valid": True, "sha256": digest}))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)

    release = commands.add_parser("render-release")
    release.add_argument("--source-root", type=Path, required=True)
    release.add_argument("--source-git-sha", required=True)
    release.add_argument("--parent-receipt", type=Path, required=True)
    release.add_argument("--collection-manifest", type=Path, required=True)
    release.add_argument("--training-receipt", type=Path, required=True)
    release.add_argument("--expected-job", default=DEFAULT_SERVE_JOB)
    release.add_argument("--serve-job-json", type=Path, required=True)
    release.add_argument("--serve-pods-json", type=Path, required=True)
    release.add_argument("--output", type=Path, required=True)

    run = commands.add_parser("run-server")
    run.add_argument("lineage", nargs=15)

    lineage = commands.add_parser("attest-lineage")
    lineage.add_argument("--source-root", type=Path, required=True)
    lineage.add_argument("--source-git-sha", required=True)
    lineage.add_argument("--parent-receipt", type=Path, required=True)
    lineage.add_argument("--collection-manifest", type=Path, required=True)
    lineage.add_argument("--training-receipt", type=Path, required=True)
    lineage.add_argument("--expected-job", default=DEFAULT_SERVE_JOB)
    lineage.add_argument("--serve-job-json", type=Path, required=True)
    lineage.add_argument("--serve-pods-json", type=Path, required=True)
    lineage.add_argument("--output", type=Path, required=True)

    attest = commands.add_parser("attest-endpoint")
    attest.add_argument("--lineage-attestation", type=Path, required=True)
    attest.add_argument("--serve-release", type=Path, required=True)
    attest.add_argument("--serve-job-json", type=Path, required=True)
    attest.add_argument("--serve-pods-json", type=Path, required=True)
    attest.add_argument("--models", type=Path, required=True)
    attest.add_argument("--canary", type=Path, required=True)
    attest.add_argument("--tunnel-status", type=Path, required=True)
    attest.add_argument("--output", type=Path, required=True)

    audit = commands.add_parser("audit")
    audit.add_argument("kind", choices=("serve-release", "lineage", "endpoint"))
    audit.add_argument("path", type=Path)
    return root


def main() -> None:
    arguments = parser().parse_args()
    functions = {
        "render-release": render_release,
        "run-server": run_server,
        "attest-lineage": attest_lineage,
        "attest-endpoint": attest_endpoint,
        "audit": audit_command,
    }
    try:
        functions[arguments.command](arguments)
    except IntegrityError as exc:
        raise SystemExit(f"SolDaggerServeError: {exc}") from exc


if __name__ == "__main__":
    main()
