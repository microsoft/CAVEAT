"""Receipt-bound serving contract for the focused Sol-DAgger step-26 update.

The validator deliberately rehashes the step-25 parent, every frozen cleanup
input, the materialized PRIME dataset, both DCPs, and the final LoRA on the PVC
before starting vLLM.  Evaluation outcomes are neither inputs nor readable
options to this module.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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
from .sol_dagger_candidate_serve import (
    CHAT_TEMPLATE,
    IMAGE_DIGEST,
    PARENT_PATH,
    PARENT_TREE,
    PRIME_COMMIT,
    PRIME_VERSION,
    TOKENIZER_JSON,
    _dcp_identity,
    _four_b200_resources,
    _process_start_ticks,
    _safe_regular,
    _source_identity,
    _tree_identity,
    build_vllm_argv,
)

PLAN_SCHEMA = "harness-distill.sol-dagger-cleanup-sft-plan.v1"
RECEIPT_SCHEMA = "harness-distill.sol-dagger-cleanup-sft-receipt.v1"
PARENT_SCHEMA = "harness-distill.sol-dagger-sft-receipt.v1"
COLLECTION_SCHEMA = "harness-distill.sol-dagger-collection-manifest.v1"
SERVE_RELEASE_SCHEMA = "harness-posttrain.browser-action-next-iteration.sol-dagger-candidate-serve-release.v1"
LINEAGE_SCHEMA = "harness-posttrain-eval.sol-dagger-step26-pvc-lineage.v1"
ENDPOINT_SCHEMA = "harness-posttrain-eval.sol-dagger-step26-endpoint.v1"

PURPOSE = "sol_dagger_candidate_serve"
DEFAULT_SERVE_JOB = "t-yuxuanli-hpt-q35-sol-d26-serve-w2"
SERVE_JOB_PATTERN = re.compile(r"t-yuxuanli-hpt-q35-sol-d26-serve-w[1-9][0-9]*")
DATA_ROOT = Path("/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30")
RUNS_ROOT = Path("/data/runs/t-yuxuanli")
CAMPAIGN = RUNS_ROOT / "t-yuxuanli-hpt-q35-sol-dagger-step26-sft-w5-20260814"
TRAINING_ROOT = CAMPAIGN / "training_r1"
TRAINING_OUTPUT = TRAINING_ROOT / "training/prime_output"
TRAINING_RECEIPT_PATH = TRAINING_ROOT / "training/training_receipt.json"
PLAN_PATH = TRAINING_ROOT / "training/plan.json"
CANDIDATE_PATH = TRAINING_OUTPUT / "weights/step_26/lora_adapters"
FINAL_DCP_PATH = TRAINING_OUTPUT / "checkpoints/step_26/trainer"
BRIDGE_DCP_PATH = TRAINING_OUTPUT / "checkpoints/step_25/trainer"

PARENT_RECEIPT_PATH = (
    RUNS_ROOT
    / "t-yuxuanli-hpt-q35-sol-dagger-sft-698a7c6-w4-20260814"
    / "training_r1/training/training_receipt.json"
)
PARENT_RECEIPT_FILE = "a2d303c2b7b9bb456f07e02049cf5b8b3a437a4a4f2f2c611a1e6826421fe4e3"
PARENT_RECEIPT_BODY = "c34a82821f9098dd76dcf2f3d11919fe456d646f2aaea8826a3e08d40347ed9c"
PARENT_ADAPTER_TREE = "6b4f66832c2e11acbdb03413898999143f70bd60d3d020dd27aa7705617dbdba"
PARENT_DCP_PATH = (
    RUNS_ROOT
    / "t-yuxuanli-hpt-q35-sol-dagger-sft-698a7c6-w4-20260814"
    / "training_r1/training/prime_output/checkpoints/step_25/trainer"
)
PARENT_DCP_TREE = "ff2df73f0948d93f5b51380cc615fd3fcad2d263e3e2661f230e87568fe0ea2c"
PARENT_DCP_BYTES = 57_547_695_888

ORIGINAL_COLLECTION_PATH = (
    RUNS_ROOT
    / "t-yuxuanli-hpt-q35-sol-dagger-sft-66ec60e-r2-20260814"
    / "collection_r1/collection_manifest.json"
)
ORIGINAL_COLLECTION_FILE = (
    "eb4635dfd0254136b74d9c1c199bb81f05f09ff428e1c5c5f0bbad4c7bb5f2c2"
)
ORIGINAL_COLLECTION_BODY = (
    "9a4755bfae9248e5477bdf869583b70aff76a39bf9fcbb0294c0cec583f98759"
)
COLLECTION_GIT_SHA = "12d755b9e495ab06c9c76223184ecd18ae804c89"
TRAINER_GIT_SHA = "295f1fbc60b49195a467c8e7e2313cf665247915"
TEACHER_MODEL = "gpt-5.6-sol"
TEACHER_EFFORT = "low"
EXPECTED_MIXTURE = {
    "repair_cart_cleanup": 28,
    "repair_cart_navigation": 8,
    "repair_clean_checkout": 8,
    "repair_place_order": 4,
    "sol_discovery_retention": 16,
    "sol_focused_cart_cleanup": 3,
}
EXPECTED_CLEANUP_HASHES = {
    "schedule": "d88d95eec0b32ca4f719ad35a6d729d9949ed4fafb4164a6cfe5672410ea0cff",
    "audit": "00214c56800fde0e8052f7e13691760de1ce1dda41933134653554020edd1fd4",
    "sidecars": "80a520dfaccf14bbc2610358d6e6a261cd39f0640ad37e7472fb9dd81c582bf3",
    "raw_manifest": "caf235cb0798529fe63d20d138e584e5d3a507002d42e1b9a64a2e780ab60801",
    "raw_labels": "d8cc6e733a28c25b4185f57e9c310ae9c650b0a03a4c98b03322c88374aaae80",
    "validations": "f0c7c01f89152cb182e688db943e13764fe1437fb4389cfcd51fef108d5fd5f1",
}

LOCAL_BASE_URL = "http://127.0.0.1:18531/v1"
LOCAL_TUNNEL = "18531:8000"
ENTRYPOINT_RELATIVE = Path("scripts/run_sol_dagger_step26_candidate_serve.sh")
MODULE_RELATIVE = Path(
    "src/harness_posttrain_eval/sol_dagger_step26_candidate_serve.py"
)
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
ALLOWED_SOURCE_ROOTS = (RUNS_ROOT, DATA_ROOT)
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
    "src/harness_posttrain_eval/common.py",
    "src/harness_posttrain_eval/launcher.py",
    "src/harness_posttrain_eval/sol_dagger_candidate_serve.py",
    str(MODULE_RELATIVE),
    "src/harness_posttrain_eval/sol_dagger_laptop_eval.py",
    "src/harness_posttrain_eval/sol_dagger_step26_laptop_eval.py",
    str(ENTRYPOINT_RELATIVE),
)


def _is_within(path: Path, roots: Sequence[Path]) -> bool:
    return any(_relative(path, root) for root in roots)


def _relative(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _self_hash(value: Mapping[str, Any], field: str, label: str) -> str:
    expected = sha256_bytes(
        canonical_bytes({key: item for key, item in value.items() if key != field})
    )
    if value.get(field) != expected:
        raise IntegrityError(f"{label} has an invalid {field}")
    return expected


def _descriptor(path: Path, schema: str, field: str, label: str) -> dict[str, Any]:
    value = read_json(_safe_regular(path.resolve(), label))
    if not isinstance(value, dict) or value.get("schema") != schema:
        raise IntegrityError(f"{label} schema changed")
    _self_hash(value, field, label)
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise IntegrityError(f"{label} is not a lowercase SHA-256")
    return value


def _artifact(path: Path, body_field: str) -> dict[str, str]:
    value = read_json(path.resolve())
    _self_hash(value, body_field, path.name)
    return {
        "path": str(path.resolve()),
        "file_sha256": sha256_file(path.resolve()),
        "body_sha256": str(value[body_field]),
    }


def _bound_source_files(root: Path) -> dict[str, str]:
    return {
        relative: sha256_file(
            _safe_regular(root / relative, f"bound source {relative}")
        )
        for relative in VALIDATOR_RELATIVES
    }


def _serve_names(job: Any) -> tuple[str, str]:
    if not isinstance(job, str) or SERVE_JOB_PATTERN.fullmatch(job) is None:
        raise IntegrityError("step-26 serve job name changed")
    return job, job + "-master-0"


def _mass_valid(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    by_role = value.get("by_role")
    total = value.get("total")
    direct = value.get("direct_dirty_cart")
    fraction = value.get("direct_dirty_cart_fraction")
    return not (
        not isinstance(by_role, Mapping)
        or set(by_role) != set(EXPECTED_MIXTURE)
        or any(type(item) is not int or item <= 0 for item in by_role.values())
        or type(total) is not int
        or type(direct) is not int
        or not isinstance(fraction, (int, float))
        or total != sum(by_role.values())
        # Only the four execution-verified repair cleanup rows (replicated x7)
        # count toward this causal gate.  Static Sol labels remain separately
        # visible in ``by_role`` and are never upgraded to executed evidence.
        or direct != by_role["repair_cart_cleanup"]
        or abs(fraction - direct / total) > 1e-12
        or not 0.35 <= fraction <= 0.45
        or value.get("gate") != {"minimum": 0.35, "maximum": 0.45}
    )


def validate_step25_parent(path: Path) -> dict[str, Any]:
    path = path.resolve()
    value = _descriptor(path, PARENT_SCHEMA, "receipt_body_sha256", "step-25 parent")
    candidate = value.get("candidate") or {}
    final_dcp = value.get("final_dcp") or {}
    if (
        path != PARENT_RECEIPT_PATH
        or sha256_file(path) != PARENT_RECEIPT_FILE
        or value.get("receipt_body_sha256") != PARENT_RECEIPT_BODY
        or value.get("status") != "ok"
        or value.get("source_step") != 24
        or value.get("final_step") != 25
        or value.get("optimizer_updates") != 1
        or value.get("fresh_optimizer") is not True
        or value.get("optimizer_continuation") is not False
        or candidate.get("name") != "step25-sol-dagger-sft"
        or candidate.get("update") != 25
        or candidate.get("tree_sha256") != PARENT_ADAPTER_TREE
        or final_dcp
        != {
            "path": str(PARENT_DCP_PATH),
            "files": 9,
            "bytes": PARENT_DCP_BYTES,
            "tree_sha256": PARENT_DCP_TREE,
        }
    ):
        raise IntegrityError("step-25 W4 parent receipt changed")
    return value


def validate_original_collection(path: Path) -> dict[str, Any]:
    path = path.resolve()
    value = _descriptor(
        path, COLLECTION_SCHEMA, "manifest_body_sha256", "original Sol collection"
    )
    if (
        path != ORIGINAL_COLLECTION_PATH
        or sha256_file(path) != ORIGINAL_COLLECTION_FILE
        or value.get("manifest_body_sha256") != ORIGINAL_COLLECTION_BODY
        or value.get("status") != "verified"
        or value.get("teacher_model") != TEACHER_MODEL
        or value.get("teacher_reasoning_effort") != TEACHER_EFFORT
        or value.get("train_corrective_count") != 64
        or value.get("heldout_count") != 24
        or value.get("row_count") != 96
    ):
        raise IntegrityError("original Sol prefix collection changed")
    return value


def _rehash_plan_artifacts(plan: Mapping[str, Any]) -> None:
    pairs = (
        (plan.get("semantic_gate_audit_path"), plan.get("semantic_gate_audit_sha256")),
        (plan.get("source_jsonl"), plan.get("source_jsonl_sha256")),
        (plan.get("prime_manifest_path"), plan.get("prime_manifest_sha256")),
        (plan.get("prime_parquet_path"), plan.get("prime_parquet_sha256")),
        (plan.get("config_path"), plan.get("config_sha256")),
    )
    sources = plan.get("cleanup_sources")
    if not isinstance(sources, Mapping) or set(sources) != set(EXPECTED_CLEANUP_HASHES):
        raise IntegrityError("step-26 frozen cleanup source inventory changed")
    for name, expected in EXPECTED_CLEANUP_HASHES.items():
        record = sources[name]
        if not isinstance(record, Mapping) or record.get("sha256") != expected:
            raise IntegrityError(f"step-26 {name} source descriptor changed")
        pairs = (*pairs, (record.get("path"), expected))
    for raw, expected in pairs:
        artifact = _safe_regular(
            Path(str(raw or "")).resolve(), "step-26 plan artifact"
        )
        if sha256_file(artifact) != expected:
            raise IntegrityError("step-26 plan-bound artifact bytes changed")


def _log_audit(path: Path) -> dict[str, Any]:
    text = _safe_regular(path, "step-26 trainer log").read_text(
        encoding="utf-8", errors="replace"
    )
    required = (
        "Starting from step 26",
        "Step 26 |",
        "Writing final checkpoint",
        "Writing final weight checkpoint",
        "SFT trainer finished!",
    )
    if (
        any(item not in text for item in required)
        or "Step 27 |" in text
        or "Traceback (most recent call last)" in text
        or "RuntimeError:" in text
        or text.count("Step 26 |") != 1
    ):
        raise IntegrityError(
            "trainer log does not prove exactly one clean step-26 update"
        )
    return {
        "source_step": 25,
        "final_step": 26,
        "step26_records": 1,
        "trainer_finished": True,
        "tracebacks": 0,
    }


def validate_training(
    receipt_path: Path, collection_path: Path, parent_path: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    receipt_path = receipt_path.resolve()
    if (
        receipt_path != TRAINING_RECEIPT_PATH
        or parent_path.resolve() != PARENT_RECEIPT_PATH
        or collection_path.resolve() != ORIGINAL_COLLECTION_PATH
    ):
        raise IntegrityError("step-26 receipt lineage paths changed")
    parent = validate_step25_parent(parent_path)
    collection = validate_original_collection(collection_path)
    receipt = _descriptor(
        receipt_path, RECEIPT_SCHEMA, "receipt_body_sha256", "step-26 receipt"
    )
    plan_path = Path(str(receipt.get("plan_path", ""))).resolve()
    plan = _descriptor(plan_path, PLAN_SCHEMA, "plan_body_sha256", "step-26 plan")
    candidate = receipt.get("candidate") or {}
    source_dcp = receipt.get("source_dcp") or {}
    final_dcp = receipt.get("final_dcp") or {}
    if (
        plan_path != PLAN_PATH
        or receipt.get("status") != "ok"
        or receipt.get("executor_git_sha") != TRAINER_GIT_SHA
        or receipt.get("plan_sha256") != sha256_file(plan_path)
        or receipt.get("plan_body_sha256") != plan.get("plan_body_sha256")
        or receipt.get("parent_candidate") != "step25-sol-dagger-sft"
        or receipt.get("source_step") != 25
        or receipt.get("final_step") != 26
        or receipt.get("optimizer_updates") != 1
        or receipt.get("fresh_optimizer") is not True
        or receipt.get("optimizer_continuation") is not False
        or candidate.get("name") != "step26-sol-dagger-cleanup-sft"
        or candidate.get("update") != 26
        or Path(str(candidate.get("path", ""))).resolve() != CANDIDATE_PATH
        or source_dcp != parent.get("final_dcp")
        or Path(str(final_dcp.get("path", ""))).resolve() != FINAL_DCP_PATH
        or final_dcp.get("tree_sha256") == PARENT_DCP_TREE
        or receipt.get("active_token_loss_mass") != plan.get("active_token_loss_mass")
        or not _mass_valid(receipt.get("active_token_loss_mass"))
        or receipt.get("config_path") != plan.get("config_path")
        or receipt.get("config_sha256") != plan.get("config_sha256")
        or receipt.get("trainer_log_audit")
        != _log_audit(
            Path(str((receipt.get("logs") or {}).get("trainer", {}).get("path", "")))
        )
        or plan.get("status") != "prepared"
        or plan.get("scientific_label")
        != "prospective_semantic_gate_plus_executed_cleanup_normalization"
        or plan.get("prime_version") != PRIME_VERSION
        or plan.get("prime_commit") != PRIME_COMMIT
        or plan.get("collection_git_sha") != COLLECTION_GIT_SHA
        or Path(str(plan.get("parent_receipt_path", ""))).resolve()
        != PARENT_RECEIPT_PATH
        or plan.get("parent_receipt_sha256") != PARENT_RECEIPT_FILE
        or plan.get("parent_receipt_body_sha256") != PARENT_RECEIPT_BODY
        or Path(str(plan.get("parent_model", ""))).resolve() != PARENT_PATH
        or plan.get("source_dcp") != parent.get("final_dcp")
        or Path(str(plan.get("original_collection_path", ""))).resolve()
        != ORIGINAL_COLLECTION_PATH
        or plan.get("original_collection_sha256") != ORIGINAL_COLLECTION_FILE
        or Path(str(plan.get("training_output", ""))).resolve() != TRAINING_OUTPUT
        or Path(str(plan.get("candidate_path", ""))).resolve() != CANDIDATE_PATH
        or plan.get("mixture_counts") != EXPECTED_MIXTURE
        or plan.get("row_count") != 67
        or plan.get("assistant_tokens_only") is not True
        or plan.get("evaluation_rows") != 0
        or plan.get("heldout_rows") != 0
        or plan.get("topology") != {"dp_shards": 1, "cp": 4, "gpus": 4}
        or plan.get("lora") != {"rank": 64, "alpha": 128.0}
        or plan.get("source_step") != 25
        or plan.get("final_step") != 26
        or plan.get("optimizer_updates") != 1
        or plan.get("learning_rate") != 2e-7
        or plan.get("fresh_optimizer") is not True
        or plan.get("optimizer_continuation") is not False
        or plan.get("launch_authorized") is not True
    ):
        raise IntegrityError("step-26 one-update training semantics changed")
    _rehash_plan_artifacts(plan)
    config_path = Path(str(plan["config_path"])).resolve()
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    if (
        config.get("max_steps") != 26
        or config.get("clean_output_dir") is not False
        or config.get("deployment")
        != {"type": "single_node", "num_gpus": 4, "gpus_per_node": 4}
        or Path(str(config.get("model", {}).get("name", ""))).resolve() != PARENT_PATH
        or config.get("model", {}).get("seq_len") != 32768
        or config.get("model", {}).get("cp") != 4
        or config.get("model", {}).get("lora", {}).get("rank") != 64
        or config.get("model", {}).get("lora", {}).get("alpha") != 128.0
        or config.get("data", {}).get("batch_size") != 67
        or config.get("data", {}).get("seq_len") != 32768
        or config.get("data", {}).get("shuffle") is not False
        or config.get("data", {}).get("loss_mask")
        != {"system": False, "user": False, "assistant": True, "tool": False}
        or config.get("optim", {}).get("lr") != 2e-7
        or config.get("ckpt", {}).get("resume_step") != 25
        or config.get("ckpt", {}).get("skip_optimizer") is not True
        or config.get("ckpt", {}).get("skip_scheduler") is not True
        or config.get("ckpt", {}).get("skip_dataloader") is not True
        or config.get("ckpt", {}).get("skip_progress") is not False
    ):
        raise IntegrityError("step-26 PRIME configuration changed")
    candidate_identity = _tree_identity(CANDIDATE_PATH)
    if any(
        candidate_identity[key] != candidate.get(key)
        for key in ("path", "files", "bytes", "tree_sha256")
    ):
        raise IntegrityError("step-26 adapter tree changed")
    if sha256_file(CANDIDATE_PATH / "adapter_config.json") != candidate.get(
        "adapter_config_sha256"
    ) or sha256_file(CANDIDATE_PATH.parent / "STABLE") != candidate.get(
        "stable_marker_sha256"
    ):
        raise IntegrityError("step-26 adapter configuration or stable marker changed")
    source_identity = _dcp_identity(PARENT_DCP_PATH)
    bridge_identity = _dcp_identity(BRIDGE_DCP_PATH)
    final_identity = _dcp_identity(FINAL_DCP_PATH)
    for identity, descriptor in (
        (source_identity, source_dcp),
        (final_identity, final_dcp),
    ):
        if any(
            identity[key] != descriptor.get(key)
            for key in ("path", "files", "bytes", "tree_sha256")
        ):
            raise IntegrityError("step-26 DCP bytes changed")
    bridge = plan.get("checkpoint_bridge") or {}
    if (
        bridge.get("source") != source_dcp
        or bridge.get("destination") != bridge_identity
        or any(
            source_identity[key] != bridge_identity[key]
            for key in ("files", "bytes", "tree_sha256")
        )
        or bridge.get("hardlinked_files") != 9
        or bridge.get("all_files_hardlinked") is not True
    ):
        raise IntegrityError("step-25 to step-26 DCP bridge changed")
    log = (receipt.get("logs") or {}).get("trainer") or {}
    log_path = Path(str(log.get("path", ""))).resolve()
    if (
        set(log) != {"path", "bytes", "sha256"}
        or log.get("bytes") != log_path.stat().st_size
        or log.get("sha256") != sha256_file(log_path)
    ):
        raise IntegrityError("step-26 trainer log descriptor changed")
    return receipt, plan, collection


def _runtime_identity(
    job_path: Path, pods_path: Path, *, expected_job: str, require_ready: bool
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    job_name, pod_name = _serve_names(expected_job)
    job = read_json(job_path.resolve())
    pods = read_json(pods_path.resolve())
    items = pods.get("items") if isinstance(pods, Mapping) else None
    if not isinstance(items, list) or len(items) != 1:
        raise IntegrityError("step-26 serve reservation must have exactly one pod")
    pod = items[0]
    statuses = pod.get("status", {}).get("containerStatuses") or []
    container = statuses[0] if len(statuses) == 1 else {}
    tasks = job.get("spec", {}).get("tasks") or []
    task = tasks[0] if len(tasks) == 1 else {}
    job_containers = task.get("template", {}).get("spec", {}).get("containers") or []
    pod_containers = pod.get("spec", {}).get("containers") or []
    job_container = job_containers[0] if len(job_containers) == 1 else {}
    pod_container = pod_containers[0] if len(pod_containers) == 1 else {}
    job_selector = task.get("template", {}).get("spec", {}).get("nodeSelector") or {}
    pod_selector = pod.get("spec", {}).get("nodeSelector") or {}
    owners = pod.get("metadata", {}).get("ownerReferences") or []
    owner = owners[0] if len(owners) == 1 else {}
    image_id = str(container.get("imageID", pod_container.get("image", "")))
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
        raise IntegrityError("step-26 serve reservation identity changed")
    if require_ready and not (
        job.get("status", {}).get("state", {}).get("phase") == "Running"
        and pod.get("status", {}).get("phase") == "Running"
        and container.get("ready") is True
        and container.get("started") is True
    ):
        raise IntegrityError("step-26 endpoint is not ready")
    return job, pod, container


def _lineage_binding(value: Mapping[str, Any]) -> str:
    return sha256_bytes(
        canonical_bytes(
            {
                key: value[key]
                for key in (
                    "validator",
                    "artifacts",
                    "candidate",
                    "training",
                    "collection",
                    "runtime",
                )
            }
        )
    )


def attest_lineage(arguments: argparse.Namespace) -> None:
    source = arguments.source_root.resolve()
    module = (source / MODULE_RELATIVE).resolve()
    if (
        HEX40.fullmatch(arguments.source_git_sha) is None
        or not _is_within(source, ALLOWED_SOURCE_ROOTS)
        or not _relative(module, source)
        or Path(__file__).resolve() != module
    ):
        raise IntegrityError("step-26 validator source identity changed")
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
        raise IntegrityError("lineage validator is not in the bound serve pod")
    receipt, plan, collection_value = validate_training(
        arguments.training_receipt,
        arguments.collection_manifest,
        arguments.parent_receipt,
    )
    trained = receipt["candidate"]
    artifacts = {
        "repair_parent_receipt": _artifact(
            arguments.parent_receipt, "receipt_body_sha256"
        ),
        "collection_manifest": _artifact(
            arguments.collection_manifest, "manifest_body_sha256"
        ),
        "training_plan": _artifact(Path(receipt["plan_path"]), "plan_body_sha256"),
        "training_receipt": _artifact(
            arguments.training_receipt, "receipt_body_sha256"
        ),
    }
    candidate = {
        "name": "step26-sol-dagger-cleanup-sft",
        "update": 26,
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
        "composite_sha256": exact_lora_composite_sha256(
            parent_tree_sha256=PARENT_TREE,
            adapter_tree_sha256=trained["tree_sha256"],
            adapter_config_sha256=trained["adapter_config_sha256"],
            tokenizer_json_sha256=TOKENIZER_JSON,
            chat_template_sha256=CHAT_TEMPLATE,
            dtype="bfloat16",
        ),
    }
    training = {
        "scientific_label": plan["scientific_label"],
        "collection_source_git_sha": plan["collection_git_sha"],
        "trainer_source_git_sha": receipt["executor_git_sha"],
        "teacher_model": TEACHER_MODEL,
        "teacher_reasoning_effort": TEACHER_EFFORT,
        "source_step": 25,
        "final_step": 26,
        "optimizer_updates": 1,
        "fresh_optimizer": True,
        "optimizer_continuation": False,
        "assistant_tokens_only": True,
        "topology": plan["topology"],
        "active_token_loss_mass": receipt["active_token_loss_mass"],
        "source_dcp": receipt["source_dcp"],
        "final_dcp": receipt["final_dcp"],
        "checkpoint_bridge": {
            "source_tree_sha256": plan["checkpoint_bridge"]["source"]["tree_sha256"],
            "destination_tree_sha256": plan["checkpoint_bridge"]["destination"][
                "tree_sha256"
            ],
            "hardlinked_files": plan["checkpoint_bridge"]["hardlinked_files"],
            "all_files_hardlinked": plan["checkpoint_bridge"]["all_files_hardlinked"],
        },
        "training_plan_file_sha256": artifacts["training_plan"]["file_sha256"],
        "training_plan_body_sha256": artifacts["training_plan"]["body_sha256"],
        "training_receipt_file_sha256": artifacts["training_receipt"]["file_sha256"],
        "training_receipt_body_sha256": artifacts["training_receipt"]["body_sha256"],
    }
    collection = {
        "source_git_sha": plan["collection_git_sha"],
        "teacher_model": TEACHER_MODEL,
        "teacher_reasoning_effort": TEACHER_EFFORT,
        "focused_attempts": 14,
        "focused_accepted": 3,
        "focused_rejected": 11,
        "training_rows": 67,
        "evaluation_rows": 0,
        "heldout_rows": 0,
        "mixture_counts": plan["mixture_counts"],
        "cleanup_source_sha256": {
            key: value["sha256"]
            for key, value in sorted(plan["cleanup_sources"].items())
        },
        "original_collection_file_sha256": sha256_file(arguments.collection_manifest),
        "original_collection_body_sha256": collection_value["manifest_body_sha256"],
    }
    identity = _source_identity(source)
    validator = {
        "source": {
            "root": str(source),
            "git_sha": arguments.source_git_sha,
            **identity,
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
    }
    core["lineage_binding_sha256"] = _lineage_binding(core)
    value = {**core, "attestation_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(arguments.output.resolve(), value)
    print(
        json.dumps(
            {"status": "attested", "attestation_sha256": value["attestation_sha256"]}
        )
    )


def validate_lineage_attestation(path: Path) -> dict[str, Any]:
    value = _descriptor(
        path.resolve(), LINEAGE_SCHEMA, "attestation_sha256", "step-26 lineage"
    )
    validator = value.get("validator") or {}
    source = validator.get("source") or {}
    module = validator.get("module") or {}
    artifacts = value.get("artifacts") or {}
    candidate = value.get("candidate") or {}
    training = value.get("training") or {}
    collection = value.get("collection") or {}
    runtime = value.get("runtime") or {}
    job_name, pod_name = _serve_names(runtime.get("job"))
    for field in (
        "adapter_tree_sha256",
        "adapter_config_sha256",
        "stable_marker_sha256",
        "parent_tree_sha256",
        "tokenizer_json_sha256",
        "chat_template_sha256",
        "composite_sha256",
    ):
        _sha(candidate.get(field), f"lineage candidate.{field}")
    source_dcp = training.get("source_dcp") or {}
    final_dcp = training.get("final_dcp") or {}
    for label, descriptor in (("source DCP", source_dcp), ("final DCP", final_dcp)):
        if (
            not isinstance(descriptor, Mapping)
            or set(descriptor) != {"path", "files", "bytes", "tree_sha256"}
            or descriptor.get("files") != 9
            or type(descriptor.get("bytes")) is not int
            or descriptor["bytes"] <= 0
        ):
            raise IntegrityError(f"step-26 lineage {label} descriptor changed")
        _sha(descriptor.get("tree_sha256"), f"lineage {label} tree")
    expected_composite = exact_lora_composite_sha256(
        parent_tree_sha256=PARENT_TREE,
        adapter_tree_sha256=str(candidate.get("adapter_tree_sha256")),
        adapter_config_sha256=str(candidate.get("adapter_config_sha256")),
        tokenizer_json_sha256=TOKENIZER_JSON,
        chat_template_sha256=CHAT_TEMPLATE,
        dtype="bfloat16",
    )
    expected_artifacts = {
        "repair_parent_receipt": (
            PARENT_RECEIPT_PATH,
            PARENT_RECEIPT_FILE,
            PARENT_RECEIPT_BODY,
        ),
        "collection_manifest": (
            ORIGINAL_COLLECTION_PATH,
            ORIGINAL_COLLECTION_FILE,
            ORIGINAL_COLLECTION_BODY,
        ),
        "training_plan": (PLAN_PATH, None, None),
        "training_receipt": (TRAINING_RECEIPT_PATH, None, None),
    }
    if not isinstance(artifacts, Mapping) or set(artifacts) != set(expected_artifacts):
        raise IntegrityError("step-26 lineage artifact inventory changed")
    for label, (
        expected_path,
        expected_file,
        expected_body,
    ) in expected_artifacts.items():
        record = artifacts.get(label) or {}
        if (
            set(record) != {"path", "file_sha256", "body_sha256"}
            or Path(str(record.get("path", ""))).resolve() != expected_path
            or (
                expected_file is not None and record.get("file_sha256") != expected_file
            )
            or (
                expected_body is not None and record.get("body_sha256") != expected_body
            )
        ):
            raise IntegrityError(f"step-26 lineage {label} descriptor changed")
        _sha(record.get("file_sha256"), f"{label} file")
        _sha(record.get("body_sha256"), f"{label} body")
    source_root = Path(str(source.get("root", "")))
    module_path = Path(str(module.get("path", "")))
    if (
        value.get("status") != "verified_on_pvc"
        or value.get("outcome_blind") is not True
        or value.get("evaluation_harness_modified") is not False
        or value.get("large_artifacts_rehashed_on_pvc") is not True
        or value.get("lineage_binding_sha256") != _lineage_binding(value)
        or set(validator) != {"source", "module", "bound_files"}
        or set(source) != {"root", "git_sha", "files", "bytes", "tree_sha256"}
        or HEX40.fullmatch(str(source.get("git_sha", ""))) is None
        or type(source.get("files")) is not int
        or source["files"] <= 0
        or type(source.get("bytes")) is not int
        or source["bytes"] <= 0
        or HEX64.fullmatch(str(source.get("tree_sha256", ""))) is None
        or source_root.resolve() != source_root
        or not _is_within(source_root, ALLOWED_SOURCE_ROOTS)
        or module_path != source_root / MODULE_RELATIVE
        or module.get("sha256") != sha256_file(Path(__file__).resolve())
        or validator.get("bound_files")
        != _bound_source_files(Path(__file__).resolve().parents[2])
        or candidate.get("name") != "step26-sol-dagger-cleanup-sft"
        or candidate.get("update") != 26
        or Path(str(candidate.get("adapter_path", ""))).resolve() != CANDIDATE_PATH
        or type(candidate.get("adapter_files")) is not int
        or candidate["adapter_files"] <= 0
        or type(candidate.get("adapter_bytes")) is not int
        or candidate["adapter_bytes"] <= 0
        or candidate.get("parent_path") != str(PARENT_PATH)
        or candidate.get("parent_tree_sha256") != PARENT_TREE
        or candidate.get("tokenizer_json_sha256") != TOKENIZER_JSON
        or candidate.get("chat_template_sha256") != CHAT_TEMPLATE
        or candidate.get("dtype") != "bfloat16"
        or candidate.get("composite_sha256") != expected_composite
        or training.get("scientific_label")
        != "prospective_semantic_gate_plus_executed_cleanup_normalization"
        or training.get("collection_source_git_sha") != COLLECTION_GIT_SHA
        or training.get("trainer_source_git_sha") != TRAINER_GIT_SHA
        or training.get("teacher_model") != TEACHER_MODEL
        or training.get("teacher_reasoning_effort") != TEACHER_EFFORT
        or training.get("source_step") != 25
        or training.get("final_step") != 26
        or training.get("optimizer_updates") != 1
        or training.get("fresh_optimizer") is not True
        or training.get("optimizer_continuation") is not False
        or training.get("assistant_tokens_only") is not True
        or training.get("topology") != {"dp_shards": 1, "cp": 4, "gpus": 4}
        or not _mass_valid(training.get("active_token_loss_mass"))
        or source_dcp
        != {
            "path": str(PARENT_DCP_PATH),
            "files": 9,
            "bytes": PARENT_DCP_BYTES,
            "tree_sha256": PARENT_DCP_TREE,
        }
        or Path(str(final_dcp.get("path", ""))).resolve() != FINAL_DCP_PATH
        or final_dcp.get("tree_sha256") == PARENT_DCP_TREE
        or training.get("checkpoint_bridge")
        != {
            "source_tree_sha256": PARENT_DCP_TREE,
            "destination_tree_sha256": PARENT_DCP_TREE,
            "hardlinked_files": 9,
            "all_files_hardlinked": True,
        }
        or training.get("training_plan_file_sha256")
        != artifacts["training_plan"]["file_sha256"]
        or training.get("training_plan_body_sha256")
        != artifacts["training_plan"]["body_sha256"]
        or training.get("training_receipt_file_sha256")
        != artifacts["training_receipt"]["file_sha256"]
        or training.get("training_receipt_body_sha256")
        != artifacts["training_receipt"]["body_sha256"]
        or collection.get("source_git_sha") != COLLECTION_GIT_SHA
        or collection.get("teacher_model") != TEACHER_MODEL
        or collection.get("teacher_reasoning_effort") != TEACHER_EFFORT
        or collection.get("focused_attempts") != 14
        or collection.get("focused_accepted") != 3
        or collection.get("focused_rejected") != 11
        or collection.get("training_rows") != 67
        or collection.get("evaluation_rows") != 0
        or collection.get("heldout_rows") != 0
        or collection.get("mixture_counts") != EXPECTED_MIXTURE
        or collection.get("cleanup_source_sha256") != EXPECTED_CLEANUP_HASHES
        or collection.get("original_collection_file_sha256") != ORIGINAL_COLLECTION_FILE
        or collection.get("original_collection_body_sha256") != ORIGINAL_COLLECTION_BODY
        or runtime.get("job") != job_name
        or runtime.get("pod") != pod_name
        or not runtime.get("job_uid")
        or not runtime.get("pod_uid")
        or not runtime.get("node")
        or HEX64.fullmatch(str(runtime.get("job_spec_sha256", ""))) is None
        or HEX64.fullmatch(str(runtime.get("job_labels_sha256", ""))) is None
        or not str(runtime.get("image_id", "")).endswith("@" + IMAGE_DIGEST)
        or runtime.get("restart_count") != 0
        or runtime.get("executed_in_bound_pod") is not True
    ):
        raise IntegrityError("step-26 PVC lineage policy or binding changed")
    return value


def _release_argv(
    release: Mapping[str, Any], lineage: Mapping[str, Any], pod_uid: str
) -> list[str]:
    artifacts = lineage["artifacts"]
    candidate = lineage["candidate"]
    job, pod = _serve_names(lineage["runtime"]["job"])
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
        job,
        pod,
        pod_uid,
    ]


def render_release(arguments: argparse.Namespace) -> None:
    lineage = validate_lineage_attestation(arguments.lineage_attestation)
    job_name, pod_name = _serve_names(arguments.expected_job)
    if lineage["runtime"]["job"] != job_name:
        raise IntegrityError("step-26 lineage belongs to a different reservation")
    source = arguments.source_root.resolve()
    entrypoint = (source / ENTRYPOINT_RELATIVE).resolve()
    job, pod, _container = _runtime_identity(
        arguments.serve_job_json,
        arguments.serve_pods_json,
        expected_job=job_name,
        require_ready=False,
    )
    if (
        arguments.source_git_sha != lineage["validator"]["source"]["git_sha"]
        or source != Path(lineage["validator"]["source"]["root"])
        or _source_identity(source)
        != {
            key: lineage["validator"]["source"][key]
            for key in ("files", "bytes", "tree_sha256")
        }
        or sha256_file(_safe_regular(entrypoint, "step-26 serve entrypoint"))
        != lineage["validator"]["bound_files"][str(ENTRYPOINT_RELATIVE)]
        or pod["metadata"]["uid"] != lineage["runtime"]["pod_uid"]
        or job["metadata"]["uid"] != lineage["runtime"]["job_uid"]
    ):
        raise IntegrityError("step-26 staged source, lineage, or reservation changed")
    partial = {
        "entrypoint": {"path": str(entrypoint), "sha256": sha256_file(entrypoint)}
    }
    core = {
        "schema": SERVE_RELEASE_SCHEMA,
        "status": "released",
        "purpose": PURPOSE,
        "laptop_r01_outcomes_read": False,
        "office_chair_outcomes_read": False,
        "reservation": {
            "job_name": job_name,
            "pod_name": pod_name,
            "pod_uid": pod["metadata"]["uid"],
        },
        "source": lineage["validator"]["source"],
        "entrypoint": partial["entrypoint"],
        "argv": _release_argv(partial, lineage, str(pod["metadata"]["uid"])),
    }
    value = {**core, "release_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(arguments.output.resolve(), value)
    print(json.dumps({"status": "rendered", "release_sha256": value["release_sha256"]}))


def audit_release(path: Path, *, verify_source: bool = True) -> dict[str, Any]:
    value = _descriptor(
        path.resolve(), SERVE_RELEASE_SCHEMA, "release_sha256", "step-26 release"
    )
    reservation = value.get("reservation") or {}
    source = value.get("source") or {}
    entrypoint = value.get("entrypoint") or {}
    argv = value.get("argv")
    job, pod = _serve_names(reservation.get("job_name"))
    source_root = Path(str(source.get("root", "")))
    program = Path(str(entrypoint.get("path", "")))
    if (
        set(value) != GENERIC_RELEASE_KEYS
        or value.get("status") != "released"
        or value.get("purpose") != PURPOSE
        or value.get("laptop_r01_outcomes_read") is not False
        or value.get("office_chair_outcomes_read") is not False
        or reservation.get("pod_name") != pod
        or not reservation.get("pod_uid")
        or set(source) != {"root", "git_sha", "files", "bytes", "tree_sha256"}
        or HEX40.fullmatch(str(source.get("git_sha", ""))) is None
        or not _is_within(source_root, ALLOWED_SOURCE_ROOTS)
        or program != source_root / ENTRYPOINT_RELATIVE
        or not isinstance(argv, list)
        or len(argv) != 16
        or argv[13:] != [job, pod, reservation["pod_uid"]]
        or argv[0] != entrypoint.get("sha256")
    ):
        raise IntegrityError("generic step-26 serve release policy changed")
    for index in (0, 2, 3, 5, 6, 8, 9, 10, 11, 12):
        _sha(argv[index], f"release argv[{index}]")
    if verify_source and (
        _source_identity(source_root)
        != {key: source[key] for key in ("files", "bytes", "tree_sha256")}
        or sha256_file(_safe_regular(program, "step-26 serve entrypoint"))
        != entrypoint["sha256"]
    ):
        raise IntegrityError("step-26 staged serve source changed")
    return value


def _validated_server_contract(argv: Sequence[str]) -> tuple[Path, Path, str]:
    if len(argv) != 15:
        raise IntegrityError(
            "step-26 runner expected fifteen lineage/runtime arguments"
        )
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
        job,
        pod,
        pod_uid,
    ) = argv
    parent = Path(parent_raw).resolve()
    collection = Path(collection_raw).resolve()
    training = Path(training_raw).resolve()
    receipt, _plan, _collection = validate_training(training, collection, parent)
    candidate = receipt["candidate"]
    expected_job, expected_pod = _serve_names(job)
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
        job,
        pod,
        pod_uid,
    )
    expected = (
        PARENT_RECEIPT_FILE,
        PARENT_RECEIPT_BODY,
        ORIGINAL_COLLECTION_FILE,
        ORIGINAL_COLLECTION_BODY,
        sha256_file(training),
        receipt["receipt_body_sha256"],
        candidate["tree_sha256"],
        candidate["adapter_config_sha256"],
        candidate["stable_marker_sha256"],
        expected_job,
        expected_pod,
        os.environ.get("POD_UID"),
    )
    if (
        observed != expected
        or os.environ.get("POD_NAME", os.environ.get("HOSTNAME")) != expected_pod
        or os.environ.get("HOSTNAME") != expected_pod
        or _tree_identity(PARENT_PATH)["tree_sha256"] != PARENT_TREE
    ):
        raise IntegrityError("step-26 runner receipt, model, or pod binding changed")
    alias = f"qwen35-browser-action-sol-dagger-step26-{candidate_tree[:12]}-exact-lora"
    return PARENT_PATH, CANDIDATE_PATH, alias


def run_server(arguments: argparse.Namespace) -> None:
    parent, adapter, alias = _validated_server_contract(arguments.lineage)
    environment = dict(os.environ)
    environment.pop("VLLM_ALLOW_RUNTIME_LORA_UPDATING", None)
    environment.pop("VLLM_API_KEY", None)
    environment["FLA_TILELANG"] = "0"
    command = build_vllm_argv(parent, adapter, alias)
    os.execvpe(command[0], command, environment)


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
        or lineage["runtime"]["job_uid"] != job["metadata"]["uid"]
        or lineage["runtime"]["pod_uid"] != pod["metadata"]["uid"]
        or release["source"] != lineage["validator"]["source"]
        or release["argv"]
        != _release_argv(release, lineage, str(pod["metadata"]["uid"]))
    ):
        raise IntegrityError("step-26 release, lineage, and live pod differ")
    candidate = lineage["candidate"]
    training = lineage["training"]
    alias = f"qwen35-browser-action-sol-dagger-step26-{candidate['adapter_tree_sha256'][:12]}-exact-lora"
    models = read_json(arguments.models.resolve())
    canary = read_json(arguments.canary.resolve())
    model_ids = sorted(
        str(row.get("id")) for row in models.get("data", []) if isinstance(row, Mapping)
    )
    choices = canary.get("choices") or []
    choice = choices[0] if len(choices) == 1 else {}
    if (
        model_ids != sorted(["qwen35-exact-lora-parent", alias])
        or canary.get("model") != alias
        or choice.get("finish_reason") != "stop"
        or str(choice.get("message", {}).get("content", "")).strip() != "OK"
    ):
        raise IntegrityError("step-26 endpoint API canary changed")
    tunnel = read_json(arguments.tunnel_status.resolve())
    if (
        tunnel.get("process_start_ticks") != _process_start_ticks(tunnel.get("pid"))
        or not isinstance(tunnel.get("argv"), list)
        or tunnel["argv"][-2:] != [f"pod/{pod_name}", LOCAL_TUNNEL]
    ):
        raise IntegrityError("step-26 endpoint tunnel identity changed")
    core = {
        "schema": ENDPOINT_SCHEMA,
        "status": "ok",
        "outcome_blind": True,
        "evaluation_harness_modified": False,
        "candidate": {
            **{
                key: candidate[key]
                for key in (
                    "name",
                    "update",
                    "adapter_path",
                    "parent_path",
                    "parent_tree_sha256",
                    "adapter_tree_sha256",
                    "adapter_config_sha256",
                    "stable_marker_sha256",
                    "tokenizer_json_sha256",
                    "chat_template_sha256",
                    "dtype",
                    "composite_sha256",
                )
            },
            "served_model_name": alias,
        },
        "model_spec": {
            "provider": "openai",
            "name": alias,
            "deployment": alias,
            "base_url": LOCAL_BASE_URL,
            "api_key": "env:HARNESS_POSTTRAIN_API_KEY",
            "vision": False,
            "extra": {"frequency_penalty": None},
        },
        "artifacts": {
            "pvc_lineage_attestation": _artifact(
                arguments.lineage_attestation, "attestation_sha256"
            ),
            **lineage["artifacts"],
            "serve_release": _artifact(arguments.serve_release, "release_sha256"),
        },
        "training": {
            "lineage_attestation_sha256": lineage["attestation_sha256"],
            "lineage_binding_sha256": lineage["lineage_binding_sha256"],
            "collection_source_git_sha": training["collection_source_git_sha"],
            "trainer_source_git_sha": training["trainer_source_git_sha"],
            "teacher_model": TEACHER_MODEL,
            "teacher_reasoning_effort": TEACHER_EFFORT,
            "source_step": 25,
            "final_step": 26,
            "optimizer_updates": 1,
            "fresh_optimizer": True,
            "optimizer_continuation": False,
            "assistant_tokens_only": True,
            "topology": training["topology"],
            "active_token_loss_mass": training["active_token_loss_mass"],
            "source_dcp_tree_sha256": PARENT_DCP_TREE,
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
    value = {**core, "receipt_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(arguments.output.resolve(), value)
    print(json.dumps({"status": "attested", "receipt_sha256": value["receipt_sha256"]}))


def validate_endpoint(path: Path, *, verify_artifacts: bool = True) -> dict[str, Any]:
    value = _descriptor(
        path.resolve(), ENDPOINT_SCHEMA, "receipt_sha256", "step-26 endpoint"
    )
    candidate = value.get("candidate") or {}
    training = value.get("training") or {}
    runtime = value.get("runtime") or {}
    job, pod = _serve_names(runtime.get("job"))
    alias = f"qwen35-browser-action-sol-dagger-step26-{candidate.get('adapter_tree_sha256', '')[:12]}-exact-lora"
    expected_composite = exact_lora_composite_sha256(
        parent_tree_sha256=PARENT_TREE,
        adapter_tree_sha256=str(candidate.get("adapter_tree_sha256")),
        adapter_config_sha256=str(candidate.get("adapter_config_sha256")),
        tokenizer_json_sha256=TOKENIZER_JSON,
        chat_template_sha256=CHAT_TEMPLATE,
        dtype="bfloat16",
    )
    expected_model = {
        "provider": "openai",
        "name": alias,
        "deployment": alias,
        "base_url": LOCAL_BASE_URL,
        "api_key": "env:HARNESS_POSTTRAIN_API_KEY",
        "vision": False,
        "extra": {"frequency_penalty": None},
    }
    if (
        value.get("status") != "ok"
        or value.get("outcome_blind") is not True
        or value.get("evaluation_harness_modified") is not False
        or candidate.get("name") != "step26-sol-dagger-cleanup-sft"
        or candidate.get("update") != 26
        or Path(str(candidate.get("adapter_path", ""))).resolve() != CANDIDATE_PATH
        or candidate.get("parent_path") != str(PARENT_PATH)
        or candidate.get("parent_tree_sha256") != PARENT_TREE
        or candidate.get("tokenizer_json_sha256") != TOKENIZER_JSON
        or candidate.get("chat_template_sha256") != CHAT_TEMPLATE
        or candidate.get("dtype") != "bfloat16"
        or candidate.get("composite_sha256") != expected_composite
        or candidate.get("served_model_name") != alias
        or value.get("model_spec") != expected_model
        or training.get("collection_source_git_sha") != COLLECTION_GIT_SHA
        or training.get("teacher_model") != TEACHER_MODEL
        or training.get("teacher_reasoning_effort") != TEACHER_EFFORT
        or training.get("source_step") != 25
        or training.get("final_step") != 26
        or training.get("optimizer_updates") != 1
        or training.get("fresh_optimizer") is not True
        or training.get("optimizer_continuation") is not False
        or training.get("assistant_tokens_only") is not True
        or training.get("topology") != {"dp_shards": 1, "cp": 4, "gpus": 4}
        or training.get("source_dcp_tree_sha256") != PARENT_DCP_TREE
        or not _mass_valid(training.get("active_token_loss_mass"))
        or runtime.get("job") != job
        or runtime.get("pod") != pod
        or not runtime.get("job_uid")
        or not runtime.get("pod_uid")
        or not runtime.get("node")
        or not str(runtime.get("image_id", "")).endswith("@" + IMAGE_DIGEST)
    ):
        raise IntegrityError("step-26 endpoint policy changed")
    if verify_artifacts:
        artifacts = value.get("artifacts") or {}
        if set(artifacts) != {
            "pvc_lineage_attestation",
            "repair_parent_receipt",
            "collection_manifest",
            "training_plan",
            "training_receipt",
            "serve_release",
        }:
            raise IntegrityError("step-26 endpoint artifact inventory changed")
        for label, field in (
            ("pvc_lineage_attestation", "attestation_sha256"),
            ("serve_release", "release_sha256"),
        ):
            record = artifacts[label]
            artifact_path = Path(record["path"]).resolve()
            if (
                sha256_file(_safe_regular(artifact_path, label))
                != record["file_sha256"]
                or read_json(artifact_path).get(field) != record["body_sha256"]
            ):
                raise IntegrityError(f"step-26 endpoint-bound {label} changed")
        lineage = validate_lineage_attestation(
            Path(artifacts["pvc_lineage_attestation"]["path"])
        )
        release = audit_release(
            Path(artifacts["serve_release"]["path"]), verify_source=False
        )
        if (
            any(
                artifacts[label] != lineage["artifacts"][label]
                for label in (
                    "repair_parent_receipt",
                    "collection_manifest",
                    "training_plan",
                    "training_receipt",
                )
            )
            or candidate.get("adapter_tree_sha256")
            != lineage["candidate"].get("adapter_tree_sha256")
            or training.get("lineage_attestation_sha256")
            != lineage.get("attestation_sha256")
            or training.get("lineage_binding_sha256")
            != lineage.get("lineage_binding_sha256")
            or release.get("source") != lineage.get("validator", {}).get("source")
            or release.get("reservation")
            != {"job_name": job, "pod_name": pod, "pod_uid": runtime["pod_uid"]}
            or release.get("argv")
            != _release_argv(release, lineage, runtime["pod_uid"])
        ):
            raise IntegrityError("step-26 endpoint no longer matches its lineage")
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
    release = commands.add_parser("render-release")
    release.add_argument("--lineage-attestation", type=Path, required=True)
    release.add_argument("--source-root", type=Path, required=True)
    release.add_argument("--source-git-sha", required=True)
    release.add_argument("--expected-job", default=DEFAULT_SERVE_JOB)
    release.add_argument("--serve-job-json", type=Path, required=True)
    release.add_argument("--serve-pods-json", type=Path, required=True)
    release.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run-server")
    run.add_argument("lineage", nargs=15)
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
        "attest-lineage": attest_lineage,
        "render-release": render_release,
        "run-server": run_server,
        "attest-endpoint": attest_endpoint,
        "audit": audit_command,
    }
    try:
        functions[arguments.command](arguments)
    except (IntegrityError, KeyError, OSError, tomllib.TOMLDecodeError) as exc:
        raise SystemExit(f"SolDaggerStep26ServeError: {exc}") from exc


if __name__ == "__main__":
    main()
