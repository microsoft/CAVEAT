"""Receipt-bound serving contract for the campaign-2 weighted-CE candidate.

The model-facing evaluator is not implemented here.  This module only binds
the sealed step-25 parent, the independently audited weighted-CE receipt, the
direct LoRA adapter, and the live four-GPU reservation into an endpoint
receipt consumed by :mod:`interactive_sol_dagger_laptop_eval`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
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
    TOKENIZER_JSON,
    _safe_regular,
    _source_identity,
    _tree_identity,
    build_vllm_argv,
)

RECEIPT_SCHEMA = "harness-distill.action-weighted-ce-receipt.v1"
PLAN_SCHEMA = "harness-distill.action-weighted-ce-plan.v1"
COLLECTION_SCHEMA = "harness-distill.c2-action-collection.v1"
LINEAGE_SCHEMA = (
    "harness-posttrain-eval.interactive-sol-dagger-action-weighted-pvc-lineage.v1"
)
ENDPOINT_SCHEMA = (
    "harness-posttrain-eval.interactive-sol-dagger-action-weighted-endpoint.v1"
)
SERVE_RELEASE_SCHEMA = (
    "harness-posttrain.browser-action-next-iteration.c2-candidate-serve-release.v1"
)
PURPOSE = "c2_candidate_serve"
SCIENTIFIC_LABEL = "executed_same_state_action_weighted_hard_distillation_ce"
PRIME_COMMIT = "d334ea52940b47f426293a7d146239e3fbf91caa"
TRAINER_SOURCE_GIT_SHA = "900dba5898ccbc4775b17ff6cad873a0b3c30c0c"
LIVE_COLLECTOR_GIT_SHA = "3a018046f11b3b0b86fec2b0029466d0b5f9fad2"
COLLECTION_MATERIALIZER_GIT_SHA = "3aba6e2d0f351732f68783bd321f456dba910031"
COLLECTION_VALIDATOR_DATA_SHA256 = (
    "dc5f828d087075739d39d1ddc33d81786022a32bc16c96e330875e1560e59553"
)
COLLECTION_VALIDATOR_CLI_SHA256 = (
    "79a1da7e53f20355e3598078ad4157306c5b1a925e68fd485598d283efc8d5d4"
)
COLLECTION_PROVENANCE = {
    "live_collector_commit": LIVE_COLLECTOR_GIT_SHA,
    "validator_materializer_commit": COLLECTION_MATERIALIZER_GIT_SHA,
    "validator_data_path": "src/harness_distill/interactive_sol_dagger_data.py",
    "validator_data_sha256": COLLECTION_VALIDATOR_DATA_SHA256,
    "validator_cli_path": "scripts/prepare_interactive_sol_dagger.py",
    "validator_cli_sha256": COLLECTION_VALIDATOR_CLI_SHA256,
}

RUNS_ROOT = Path("/data/runs/t-yuxuanli")
DATA_ROOT = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30"
)
TRAINING_ROOT = (
    RUNS_ROOT
    / "t-yuxuanli-hpt-c2-action-weighted-ce-w1-20260814"
    / "training_r1/training"
)
TRAINING_RECEIPT_PATH = TRAINING_ROOT / "training_receipt.json"
CANDIDATE_PATH = TRAINING_ROOT / "prime_output/weights/step_26/lora_adapters"
TRAINER_SOURCE_PATH = (
    RUNS_ROOT
    / "t-yuxuanli-hpt-c2-action-weighted-ce-w1-20260814"
    / "source_distill_900dba58"
)
TRAINER_SOURCE_IDENTITY = {
    "files": 311,
    "bytes": 45_514_043,
    "tree_sha256": "f68ce98efe9bbe107441293139b2f6747bff3b18c55796e74a674af0053d2474",
}
PARENT_DCP_TREE = "ff2df73f0948d93f5b51380cc615fd3fcad2d263e3e2661f230e87568fe0ea2c"
PARENT_DCP_PATH = (
    RUNS_ROOT
    / "t-yuxuanli-hpt-q35-sol-dagger-sft-698a7c6-w4-20260814"
    / "training_r1/training/prime_output/checkpoints/step_25/trainer"
)
PARENT_DCP_FILES = 9
PARENT_DCP_BYTES = 57_547_695_888
PARENT_RECEIPT_PATH = (
    RUNS_ROOT
    / "t-yuxuanli-hpt-q35-sol-dagger-sft-698a7c6-w4-20260814"
    / "training_r1/training/training_receipt.json"
)
PARENT_RECEIPT_FILE = (
    "a2d303c2b7b9bb456f07e02049cf5b8b3a437a4a4f2f2c611a1e6826421fe4e3"
)
PARENT_RECEIPT_BODY = (
    "c34a82821f9098dd76dcf2f3d11919fe456d646f2aaea8826a3e08d40347ed9c"
)

DEFAULT_SERVE_JOB = "t-yuxuanli-hpt-c2-candidate-serve-w1"
DEFAULT_SERVE_POD = DEFAULT_SERVE_JOB + "-master-0"
EXPECTED_JOB_UID = "90b8fdd9-1f59-4b4f-9d97-fee80504f96e"
EXPECTED_POD_UID = "3af8b6ee-8e58-4606-a987-580ff6d28bdb"
EXPECTED_NODE = "slc01-cl02-hgx-0359"
EXPECTED_JOB_SPEC_SHA256 = (
    "92b71a4290de90596feaef40fa4faa7c6a8b1805e752b6eb5257737c3eea13e4"
)
EXPECTED_JOB_LABELS_SHA256 = (
    "d2b19234f733dd03979ed9c33cfd08395f3da8867b1dd3f09d7d4720acbf3286"
)
EXPECTED_POD_SPEC_SHA256 = (
    "9917440effa70a25bada1f56d43eb0cdd5fca7e9be45e2741772a5a95c82b209"
)
EXPECTED_POD_LABELS_SHA256 = (
    "c4ff3f7d4d1096d47e55bd9debabd245222605ee751f62ff05a6361fe26e8148"
)
LOCAL_BASE_URL = "http://127.0.0.1:18541/v1"
LOCAL_TUNNEL = "18541:8000"
MODULE_RELATIVE = Path(
    "src/harness_posttrain_eval/interactive_sol_dagger_candidate_serve.py"
)
ENTRYPOINT_RELATIVE = Path("scripts/run_interactive_sol_dagger_candidate_serve.sh")
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
ALLOWED_SOURCE_ROOTS = (RUNS_ROOT, DATA_ROOT)
COLLECTION_PHASES = {
    "frontier_exploration": 16,
    "checkpoint_grounding": 16,
    "approved_cart_entry": 16,
    "dirty_cart_cleanup": 16,
    "clean_checkout_order": 8,
}
COLLECTION_COUNTS = {
    "executed": 72,
    "retention": 24,
    "total": 96,
    "phases": COLLECTION_PHASES,
    "unique_states": 96,
    "evaluation": 0,
    "heldout": 0,
}
RESUME_ORDER = {
    "run_registration": "before_optimizer_construction_and_dcp_load",
    "optimizer_binding": "exact_registered_run_0_lora_parameter_objects",
    "optimizer_construction": "before_dcp_load",
    "optimizer_state": "fresh_dcp_optimizer_state_skipped",
    "dcp_restore": "in_place_into_registered_run_0_lora",
    "pre_update_guard": "distributed_sampled_signature_equal_and_lora_b_nonzero",
}
TOPOLOGIES = {
    "cp2_dp2": {
        "name": "cp2_dp2",
        "trainer_world_size": 4,
        "context_parallel_size": 2,
        "data_parallel_replicate": 2,
        "data_parallel_shard": 1,
        "data_parallel_size": 2,
        "fallback": False,
        "mesh_log": (
            "Building 3-D device mesh with ['dp_replicate', 'dp_shard', 'cp'], "
            "[2, 1, 2]"
        ),
    },
    "cp4_dp1": {
        "name": "cp4_dp1",
        "trainer_world_size": 4,
        "context_parallel_size": 4,
        "data_parallel_replicate": 1,
        "data_parallel_shard": 1,
        "data_parallel_size": 1,
        "fallback": True,
        "mesh_log": "Building 2-D device mesh with ['dp_shard', 'cp'], [1, 4]",
    },
}
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
CANDIDATE_KEYS = {
    "name",
    "update",
    "adapter_path",
    "adapter_files",
    "adapter_bytes",
    "adapter_tree_sha256",
    "adapter_config_sha256",
    "stable_marker_sha256",
    "parent_path",
    "parent_tree_sha256",
    "tokenizer_json_sha256",
    "chat_template_sha256",
    "dtype",
    "composite_sha256",
}
TRAINING_KEYS = {
    "scientific_label",
    "artifact_source_git_sha",
    "execution_source_git_sha",
    "trainer_source_git_sha",
    "collection_source_git_sha",
    "prime_commit",
    "source_step",
    "final_step",
    "optimizer_updates",
    "learning_rate",
    "fresh_optimizer",
    "fresh_scheduler",
    "fresh_dataloader",
    "objective",
    "on_policy",
    "policy_gradient",
    "native_prime_component",
    "trainer_topology",
    "collection_counts",
    "resume_order",
    "resume_lora_pre_update_audit",
    "source_dcp",
    "final_dcp",
    "training_plan_file_sha256",
    "training_plan_body_sha256",
    "training_receipt_file_sha256",
    "training_receipt_body_sha256",
    "weight_audit",
    "token_execution_audit",
}
COLLECTION_KEYS = {
    "source_git_sha",
    "provenance",
    "teacher_model",
    "teacher_reasoning_effort",
    "train_corrective_count",
    "retention_count",
    "heldout_count",
    "phase_counts",
    "phase_subtype_counts",
    "unique_states",
    "manifest_file_sha256",
    "manifest_body_sha256",
}
RUNTIME_KEYS = {
    "job",
    "job_uid",
    "job_spec_sha256",
    "job_labels_sha256",
    "pod",
    "pod_uid",
    "pod_spec_sha256",
    "pod_labels_sha256",
    "node",
    "image_id",
    "restart_count",
    "executed_in_bound_pod",
}
VALIDATOR_RELATIVES = (
    "src/harness_posttrain_eval/common.py",
    "src/harness_posttrain_eval/launcher.py",
    "src/harness_posttrain_eval/sol_dagger_candidate_serve.py",
    str(MODULE_RELATIVE),
    "src/harness_posttrain_eval/interactive_sol_dagger_laptop_eval.py",
    "src/harness_posttrain_eval/interactive_sol_dagger_action_gate.py",
    "src/harness_posttrain_eval/interactive_sol_dagger_heldout_probe.py",
    "src/harness_posttrain_eval/interactive_sol_dagger_heldout_macro.py",
    str(ENTRYPOINT_RELATIVE),
    "scripts/run_interactive_sol_dagger_heldout_probe.sh",
    "scripts/run_interactive_sol_dagger_heldout_macro.sh",
)


def _relative(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_within(path: Path, roots: Sequence[Path]) -> bool:
    return any(_relative(path, root) for root in roots)


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise IntegrityError(f"{label} is not a lowercase SHA-256")
    return value


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


def _artifact(path: Path, body_field: str) -> dict[str, str]:
    value = read_json(path.resolve())
    _self_hash(value, body_field, path.name)
    return {
        "path": str(path.resolve()),
        "file_sha256": sha256_file(path.resolve()),
        "body_sha256": str(value[body_field]),
    }


def _serve_names(job: Any) -> tuple[str, str]:
    if job != DEFAULT_SERVE_JOB:
        raise IntegrityError("campaign-2 serve job name changed")
    return DEFAULT_SERVE_JOB, DEFAULT_SERVE_POD


def _bound_source_files(root: Path) -> dict[str, str]:
    return {
        relative: sha256_file(_safe_regular(root / relative, f"bound {relative}"))
        for relative in VALIDATOR_RELATIVES
    }


def validate_trainer_source(
    root: Path, *, git_sha: str, tree_sha256: str | None = None
) -> dict[str, Any]:
    root = root.resolve()
    observed = _source_identity(root)
    if (
        root != TRAINER_SOURCE_PATH
        or git_sha != TRAINER_SOURCE_GIT_SHA
        or observed != TRAINER_SOURCE_IDENTITY
        or (tree_sha256 is not None and tree_sha256 != observed["tree_sha256"])
    ):
        raise IntegrityError("trainer source differs from sealed 900dba58 archive")
    return {
        "root": str(root),
        "git_sha": TRAINER_SOURCE_GIT_SHA,
        **observed,
    }


def _pod_document(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("kind") == "Pod":
        return dict(value)
    items = value.get("items")
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise IntegrityError("candidate reservation must contain exactly one pod")
    return items[0]


def _runtime_identity(
    job_path: Path, pods_path: Path, *, require_ready: bool
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    job = read_json(job_path.resolve())
    pod = _pod_document(read_json(pods_path.resolve()))
    statuses = pod.get("status", {}).get("containerStatuses") or []
    container = statuses[0] if len(statuses) == 1 else {}
    pod_containers = pod.get("spec", {}).get("containers") or []
    pod_container = pod_containers[0] if len(pod_containers) == 1 else {}
    tasks = job.get("spec", {}).get("tasks") or []
    task = tasks[0] if len(tasks) == 1 else {}
    job_containers = task.get("template", {}).get("spec", {}).get("containers") or []
    job_container = job_containers[0] if len(job_containers) == 1 else {}
    owners = pod.get("metadata", {}).get("ownerReferences") or []
    owner = owners[0] if len(owners) == 1 else {}
    job_spec = sha256_bytes(canonical_bytes(job.get("spec")))
    job_labels = sha256_bytes(canonical_bytes(job.get("metadata", {}).get("labels", {})))
    pod_spec = sha256_bytes(canonical_bytes(pod.get("spec")))
    pod_labels = sha256_bytes(canonical_bytes(pod.get("metadata", {}).get("labels", {})))
    resources = job_container.get("resources") or {}
    requests = resources.get("requests") or {}
    limits = resources.get("limits") or {}
    selector = task.get("template", {}).get("spec", {}).get("nodeSelector") or {}
    if (
        job.get("metadata", {}).get("name") != DEFAULT_SERVE_JOB
        or job.get("metadata", {}).get("uid") != EXPECTED_JOB_UID
        or pod.get("metadata", {}).get("name") != DEFAULT_SERVE_POD
        or pod.get("metadata", {}).get("uid") != EXPECTED_POD_UID
        or pod.get("spec", {}).get("nodeName") != EXPECTED_NODE
        or job_spec != EXPECTED_JOB_SPEC_SHA256
        or job_labels != EXPECTED_JOB_LABELS_SHA256
        or pod_spec != EXPECTED_POD_SPEC_SHA256
        or pod_labels != EXPECTED_POD_LABELS_SHA256
        or task.get("replicas") != 1
        or str(requests.get("nvidia.com/gpu")) != "4"
        or str(limits.get("nvidia.com/gpu")) != "4"
        or str(requests.get("rdma/rdma_shared_device_a")) != "4"
        or str(limits.get("rdma/rdma_shared_device_a")) != "4"
        or selector
        != {"nvidia.com/gpu.count": "8", "nvidia.com/gpu.product": "NVIDIA-B200"}
        or not str(job_container.get("image", "")).endswith("@" + IMAGE_DIGEST)
        or not str(pod_container.get("image", "")).endswith("@" + IMAGE_DIGEST)
        or owner.get("name") != DEFAULT_SERVE_JOB
        or owner.get("uid") != EXPECTED_JOB_UID
        or owner.get("controller") is not True
        or container.get("restartCount") != 0
        or not str(container.get("imageID", "")).endswith("@" + IMAGE_DIGEST)
    ):
        raise IntegrityError("campaign-2 candidate reservation identity changed")
    if require_ready and not (
        job.get("status", {}).get("state", {}).get("phase") == "Running"
        and pod.get("status", {}).get("phase") == "Running"
        and container.get("ready") is True
        and container.get("started") is True
    ):
        raise IntegrityError("campaign-2 candidate endpoint is not ready")
    return job, pod, container


def _candidate_alias(tree_sha256: str) -> str:
    _sha(tree_sha256, "candidate tree")
    return f"qwen35-browser-action-step26-action-weighted-ce-{tree_sha256[:12]}-exact-lora"


def _candidate_policy(candidate: Mapping[str, Any]) -> None:
    for field in (
        "adapter_tree_sha256",
        "adapter_config_sha256",
        "stable_marker_sha256",
        "parent_tree_sha256",
        "tokenizer_json_sha256",
        "chat_template_sha256",
        "composite_sha256",
    ):
        _sha(candidate.get(field), f"candidate.{field}")
    composite = exact_lora_composite_sha256(
        parent_tree_sha256=PARENT_TREE,
        adapter_tree_sha256=str(candidate["adapter_tree_sha256"]),
        adapter_config_sha256=str(candidate["adapter_config_sha256"]),
        tokenizer_json_sha256=TOKENIZER_JSON,
        chat_template_sha256=CHAT_TEMPLATE,
        dtype="bfloat16",
    )
    if (
        candidate.get("name") != "step26-action-weighted-ce"
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
        or candidate.get("composite_sha256") != composite
    ):
        raise IntegrityError("weighted-CE candidate policy changed")


def _training_policy(training: Mapping[str, Any]) -> None:
    topology = training.get("trainer_topology") or {}
    counts = training.get("collection_counts") or {}
    resume = training.get("resume_lora_pre_update_audit") or {}
    source_dcp = training.get("source_dcp") or {}
    final_dcp = training.get("final_dcp") or {}
    if (
        training.get("scientific_label") != SCIENTIFIC_LABEL
        or training.get("artifact_source_git_sha") != TRAINER_SOURCE_GIT_SHA
        or training.get("execution_source_git_sha") != TRAINER_SOURCE_GIT_SHA
        or training.get("trainer_source_git_sha") != TRAINER_SOURCE_GIT_SHA
        or training.get("collection_source_git_sha")
        != COLLECTION_MATERIALIZER_GIT_SHA
        or training.get("prime_commit") != PRIME_COMMIT
        or training.get("source_step") != 25
        or training.get("final_step") != 26
        or training.get("optimizer_updates") != 1
        or training.get("learning_rate") != 1e-7
        or training.get("fresh_optimizer") is not True
        or training.get("fresh_scheduler") is not True
        or training.get("fresh_dataloader") is not True
        or training.get("objective") != "weighted_behavioral_cloning"
        or training.get("on_policy") is not False
        or training.get("policy_gradient") is not False
        or training.get("native_prime_component") != "ce"
        or topology not in TOPOLOGIES.values()
        or counts != COLLECTION_COUNTS
        or training.get("resume_order") != RESUME_ORDER
        or resume.get("signature_unchanged") is not True
        or type(resume.get("lora_b_nonzero")) is not int
        or resume["lora_b_nonzero"] <= 0
        or source_dcp
        != {
            "path": str(PARENT_DCP_PATH),
            "files": PARENT_DCP_FILES,
            "bytes": PARENT_DCP_BYTES,
            "tree_sha256": PARENT_DCP_TREE,
        }
        or Path(str(final_dcp.get("path", ""))).resolve()
        != TRAINING_ROOT / "prime_output/checkpoints/step_26/trainer"
        or final_dcp.get("tree_sha256") == PARENT_DCP_TREE
    ):
        raise IntegrityError("weighted-CE training policy changed")
    for field in (
        "training_plan_file_sha256",
        "training_plan_body_sha256",
        "training_receipt_file_sha256",
        "training_receipt_body_sha256",
    ):
        _sha(training.get(field), f"training.{field}")
    _sha(final_dcp.get("tree_sha256"), "final DCP tree")


def _exact_validate_receipt(trainer_source: Path, receipt: Path) -> None:
    """Run the source-pinned trainer validator in the candidate container."""

    prime_root = Path("/opt/prime-rl")
    python = prime_root / ".venv/bin/python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise IntegrityError("pinned PRIME interpreter is unavailable")
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "FLA_TILELANG": "0",
            "PYTHONPATH": os.pathsep.join(
                (
                    str(trainer_source / "src"),
                    str(prime_root / "packages/prime-rl-configs/src"),
                    str(prime_root / "src"),
                    str(prime_root / "deps/renderers"),
                )
            ),
        }
    )
    commands = [
        [
            str(python),
            str(trainer_source / "scripts/apply_prime_rl_cross_stage_resume_patch.py"),
            str(prime_root),
        ],
        [
            str(python),
            str(trainer_source / "scripts/apply_prime_grouped_mm_contiguous_grad_patch.py"),
            str(prime_root),
        ],
        [
            str(python),
            str(trainer_source / "scripts/apply_prime_single_run_lora_resume_order_patch.py"),
            str(prime_root),
        ],
        [
            str(python),
            str(trainer_source / "scripts/prepare_action_weighted_ce_training.py"),
            "validate-receipt",
            "--receipt",
            str(receipt),
        ],
    ]
    for command in commands:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=900,
            env=environment,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[-8000:]
            raise IntegrityError(f"source-pinned weighted-CE validation failed: {detail}")


def _phase_subtype_policy(value: Any) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    grouped: dict[str, int] = {phase: 0 for phase in COLLECTION_PHASES}
    for label, count in value.items():
        if (
            not isinstance(label, str)
            or label.count("/") != 1
            or type(count) is not int
            or count <= 0
        ):
            return False
        phase, subtype = label.split("/", 1)
        if phase not in grouped or not subtype:
            return False
        grouped[phase] += count
    required = {
        "approved_cart_entry/add_to_cart": 8,
        "approved_cart_entry/open_cart": 8,
        "clean_checkout_order/cart_to_checkout": 4,
        "clean_checkout_order/place_order": 4,
    }
    return grouped == COLLECTION_PHASES and all(
        value.get(label) == count for label, count in required.items()
    )


def _collection_descriptor(
    path: Path, *, expected_file_sha256: str, expected_body_sha256: str
) -> dict[str, Any]:
    path = path.resolve()
    value = _descriptor(
        path, COLLECTION_SCHEMA, "manifest_sha256", "weighted-CE collection"
    )
    _sha(expected_file_sha256, "collection file")
    _sha(expected_body_sha256, "collection body")
    source = value.get("source") or {}
    if (
        not _is_within(path, ALLOWED_SOURCE_ROOTS)
        or path.name != "manifest.json"
        or sha256_file(path) != expected_file_sha256
        or value.get("manifest_sha256") != expected_body_sha256
        or value.get("status") != "ok"
        or value.get("teacher_model") != "gpt-5.6-sol"
        or value.get("teacher_reasoning_effort") != "low"
        or value.get("provenance") != COLLECTION_PROVENANCE
        or value.get("renderer")
        != {"name": "qwen3.5", "enable_thinking": True, "assistant_only": True}
        or value.get("selection_rows") != 72
        or value.get("retention_rows") != 24
        or value.get("phase_counts") != COLLECTION_PHASES
        or not _phase_subtype_policy(value.get("phase_subtype_counts"))
        or value.get("evaluation_rows") != 0
        or value.get("heldout_rows") != 0
        or value.get("state_overlap") != 0
        or source.get("unique_states") != 96
        or source.get("successful_executed_hero50_filter") is not True
        or source.get("scorer_reward_or_evaluator_selection") is not False
    ):
        raise IntegrityError("dynamic collection identity or policy changed")
    files = value.get("files") or {}
    expected_rows = {
        "selection.jsonl": 72,
        "retention.jsonl": 24,
        "execution_audits.jsonl": 72,
    }
    if not isinstance(files, Mapping) or set(files) != set(expected_rows):
        raise IntegrityError("dynamic collection file inventory changed")
    for name, rows in expected_rows.items():
        item = files.get(name) or {}
        artifact = (path.parent / name).resolve()
        if (
            artifact.parent != path.parent
            or item.get("relative_path") != name
            or item.get("rows") != rows
            or item.get("bytes") != _safe_regular(artifact, name).stat().st_size
            or item.get("sha256") != sha256_file(artifact)
        ):
            raise IntegrityError(f"dynamic collection artifact changed: {name}")
    return value


def _validated_training_projection(
    *,
    receipt_path: Path,
    collection_path: Path,
    trainer_source: Path,
    expected_training_file_sha256: str,
    expected_training_body_sha256: str,
    expected_collection_file_sha256: str,
    expected_collection_body_sha256: str,
    run_exact_validator: bool,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
]:
    trainer = validate_trainer_source(
        trainer_source,
        git_sha=TRAINER_SOURCE_GIT_SHA,
        tree_sha256=TRAINER_SOURCE_IDENTITY["tree_sha256"],
    )
    receipt_path = receipt_path.resolve()
    if receipt_path != TRAINING_RECEIPT_PATH:
        raise IntegrityError("weighted-CE training receipt path changed")
    if run_exact_validator:
        _exact_validate_receipt(trainer_source.resolve(), receipt_path)
    receipt = _descriptor(
        receipt_path, RECEIPT_SCHEMA, "receipt_body_sha256", "weighted-CE receipt"
    )
    _sha(expected_training_file_sha256, "training receipt file")
    _sha(expected_training_body_sha256, "training receipt body")
    if (
        sha256_file(receipt_path) != expected_training_file_sha256
        or receipt.get("receipt_body_sha256") != expected_training_body_sha256
    ):
        raise IntegrityError("dynamic training receipt identity changed")
    plan_path = Path(str(receipt.get("plan_path", ""))).resolve()
    plan = _descriptor(plan_path, PLAN_SCHEMA, "plan_body_sha256", "weighted-CE plan")
    collection = _collection_descriptor(
        collection_path,
        expected_file_sha256=expected_collection_file_sha256,
        expected_body_sha256=expected_collection_body_sha256,
    )
    if (
        plan_path != TRAINING_ROOT / "plan.json"
        or receipt.get("plan_sha256") != sha256_file(plan_path)
        or receipt.get("plan_body_sha256") != plan.get("plan_body_sha256")
        or plan.get("artifact_git_sha") != TRAINER_SOURCE_GIT_SHA
        or Path(str(plan.get("frozen_collection_manifest", ""))).resolve()
        != collection_path.resolve()
        or plan.get("frozen_collection_manifest_sha256")
        != expected_collection_file_sha256
        or plan.get("frozen_collection_manifest_body_sha256")
        != expected_collection_body_sha256
        or plan.get("parent_receipt_path") != str(PARENT_RECEIPT_PATH)
        or plan.get("parent_receipt_sha256") != PARENT_RECEIPT_FILE
        or plan.get("parent_receipt_body_sha256") != PARENT_RECEIPT_BODY
        or plan.get("source_dcp", {}).get("tree_sha256") != PARENT_DCP_TREE
        or plan.get("resume_order") != RESUME_ORDER
        or receipt.get("resume_order") != RESUME_ORDER
    ):
        raise IntegrityError("weighted-CE plan lineage changed")
    parent = _descriptor(
        PARENT_RECEIPT_PATH,
        "harness-distill.sol-dagger-sft-receipt.v1",
        "receipt_body_sha256",
        "sealed step-25 receipt",
    )
    if (
        sha256_file(PARENT_RECEIPT_PATH) != PARENT_RECEIPT_FILE
        or parent.get("receipt_body_sha256") != PARENT_RECEIPT_BODY
        or parent.get("candidate", {}).get("tree_sha256")
        != "6b4f66832c2e11acbdb03413898999143f70bd60d3d020dd27aa7705617dbdba"
        or parent.get("final_dcp", {}).get("tree_sha256") != PARENT_DCP_TREE
        or _tree_identity(PARENT_DCP_PATH)
        != {
            "path": str(PARENT_DCP_PATH),
            "files": PARENT_DCP_FILES,
            "bytes": PARENT_DCP_BYTES,
            "tree_sha256": PARENT_DCP_TREE,
        }
        or _tree_identity(PARENT_PATH).get("tree_sha256") != PARENT_TREE
    ):
        raise IntegrityError("sealed step-25 parent or DCP changed")
    raw_candidate = receipt.get("candidate") or {}
    candidate_identity = _tree_identity(CANDIDATE_PATH)
    final_dcp = receipt.get("final_dcp") or {}
    if (
        raw_candidate.get("name") != "step26-action-weighted-ce"
        or raw_candidate.get("update") != 26
        or Path(str(raw_candidate.get("path", ""))).resolve() != CANDIDATE_PATH
        or any(
            raw_candidate.get(key) != candidate_identity.get(key)
            for key in ("path", "files", "bytes", "tree_sha256")
        )
        or raw_candidate.get("adapter_config_sha256")
        != sha256_file(CANDIDATE_PATH / "adapter_config.json")
        or raw_candidate.get("stable_marker_sha256")
        != sha256_file(CANDIDATE_PATH.parent / "STABLE")
        or _tree_identity(Path(str(final_dcp.get("path", "")))) != final_dcp
        or final_dcp.get("tree_sha256") == PARENT_DCP_TREE
    ):
        raise IntegrityError("weighted-CE final adapter or DCP changed")
    composite = exact_lora_composite_sha256(
        parent_tree_sha256=PARENT_TREE,
        adapter_tree_sha256=str(raw_candidate["tree_sha256"]),
        adapter_config_sha256=str(raw_candidate["adapter_config_sha256"]),
        tokenizer_json_sha256=TOKENIZER_JSON,
        chat_template_sha256=CHAT_TEMPLATE,
        dtype="bfloat16",
    )
    candidate = {
        "name": "step26-action-weighted-ce",
        "update": 26,
        "adapter_path": str(CANDIDATE_PATH),
        "adapter_files": raw_candidate["files"],
        "adapter_bytes": raw_candidate["bytes"],
        "adapter_tree_sha256": raw_candidate["tree_sha256"],
        "adapter_config_sha256": raw_candidate["adapter_config_sha256"],
        "stable_marker_sha256": raw_candidate["stable_marker_sha256"],
        "parent_path": str(PARENT_PATH),
        "parent_tree_sha256": PARENT_TREE,
        "tokenizer_json_sha256": TOKENIZER_JSON,
        "chat_template_sha256": CHAT_TEMPLATE,
        "dtype": "bfloat16",
        "composite_sha256": composite,
    }
    logs = receipt.get("trainer_logs") or {}
    training = {
        "scientific_label": SCIENTIFIC_LABEL,
        "artifact_source_git_sha": receipt.get("artifact_source_git_sha"),
        "execution_source_git_sha": receipt.get("execution_source_git_sha"),
        "trainer_source_git_sha": TRAINER_SOURCE_GIT_SHA,
        "collection_source_git_sha": COLLECTION_MATERIALIZER_GIT_SHA,
        "prime_commit": receipt.get("prime_commit"),
        "source_step": receipt.get("source_step"),
        "final_step": receipt.get("final_step"),
        "optimizer_updates": receipt.get("optimizer_updates"),
        "learning_rate": receipt.get("learning_rate"),
        "fresh_optimizer": receipt.get("fresh_optimizer"),
        "fresh_scheduler": receipt.get("fresh_scheduler"),
        "fresh_dataloader": receipt.get("fresh_dataloader"),
        "objective": receipt.get("objective"),
        "on_policy": receipt.get("on_policy"),
        "policy_gradient": receipt.get("policy_gradient"),
        "native_prime_component": receipt.get("native_prime_component"),
        "trainer_topology": receipt.get("trainer_topology"),
        "collection_counts": receipt.get("collection_counts"),
        "resume_order": receipt.get("resume_order"),
        "resume_lora_pre_update_audit": logs.get("resume_lora_pre_update_audit"),
        "source_dcp": receipt.get("source_dcp"),
        "final_dcp": final_dcp,
        "training_plan_file_sha256": sha256_file(plan_path),
        "training_plan_body_sha256": plan.get("plan_body_sha256"),
        "training_receipt_file_sha256": expected_training_file_sha256,
        "training_receipt_body_sha256": expected_training_body_sha256,
        "weight_audit": receipt.get("weight_audit"),
        "token_execution_audit": receipt.get("token_execution_audit"),
    }
    _candidate_policy(candidate)
    _training_policy(training)
    collection_projection = {
        "source_git_sha": COLLECTION_MATERIALIZER_GIT_SHA,
        "provenance": collection.get("provenance"),
        "teacher_model": collection.get("teacher_model"),
        "teacher_reasoning_effort": collection.get("teacher_reasoning_effort"),
        "train_corrective_count": collection.get("selection_rows"),
        "retention_count": collection.get("retention_rows"),
        "heldout_count": collection.get("heldout_rows"),
        "phase_counts": collection.get("phase_counts"),
        "phase_subtype_counts": collection.get("phase_subtype_counts"),
        "unique_states": collection.get("source", {}).get("unique_states"),
        "manifest_file_sha256": expected_collection_file_sha256,
        "manifest_body_sha256": expected_collection_body_sha256,
    }
    return receipt, plan, candidate, training, {**collection_projection, "trainer": trainer}


def attest_lineage(arguments: argparse.Namespace) -> None:
    source = arguments.source_root.resolve()
    module = (source / MODULE_RELATIVE).resolve()
    if (
        HEX40.fullmatch(arguments.source_git_sha) is None
        or not _is_within(source, ALLOWED_SOURCE_ROOTS)
        or module != Path(__file__).resolve()
    ):
        raise IntegrityError("lineage validator is not the staged source")
    _job, _pod, container = _runtime_identity(
        arguments.serve_job_json, arguments.serve_pods_json, require_ready=False
    )
    if (
        os.environ.get("POD_UID") != EXPECTED_POD_UID
        or os.environ.get("POD_NAME", os.environ.get("HOSTNAME"))
        != DEFAULT_SERVE_POD
        or os.environ.get("HOSTNAME") != DEFAULT_SERVE_POD
    ):
        raise IntegrityError("lineage validator is outside the reserved candidate pod")
    receipt, _plan, candidate, training, collection_with_trainer = (
        _validated_training_projection(
            receipt_path=arguments.training_receipt,
            collection_path=arguments.collection_manifest,
            trainer_source=arguments.trainer_source,
            expected_training_file_sha256=arguments.expected_training_file_sha256,
            expected_training_body_sha256=arguments.expected_training_body_sha256,
            expected_collection_file_sha256=arguments.expected_collection_file_sha256,
            expected_collection_body_sha256=arguments.expected_collection_body_sha256,
            run_exact_validator=True,
        )
    )
    trainer_source = collection_with_trainer.pop("trainer")
    artifacts = {
        # The sealed evaluator retains this historical key; it now points to
        # the exact step-25 continuation parent rather than step 24.
        "repair_parent_receipt": _artifact(PARENT_RECEIPT_PATH, "receipt_body_sha256"),
        "collection_manifest": _artifact(
            arguments.collection_manifest.resolve(), "manifest_sha256"
        ),
        "training_plan": _artifact(Path(receipt["plan_path"]), "plan_body_sha256"),
        "training_receipt": _artifact(
            arguments.training_receipt.resolve(), "receipt_body_sha256"
        ),
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
        "job": DEFAULT_SERVE_JOB,
        "job_uid": EXPECTED_JOB_UID,
        "job_spec_sha256": EXPECTED_JOB_SPEC_SHA256,
        "job_labels_sha256": EXPECTED_JOB_LABELS_SHA256,
        "pod": DEFAULT_SERVE_POD,
        "pod_uid": EXPECTED_POD_UID,
        "pod_spec_sha256": EXPECTED_POD_SPEC_SHA256,
        "pod_labels_sha256": EXPECTED_POD_LABELS_SHA256,
        "node": EXPECTED_NODE,
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
        "exact_trainer_validator_executed": True,
        "validator": validator,
        "trainer_source": trainer_source,
        "artifacts": artifacts,
        "candidate": candidate,
        "training": training,
        "collection": collection_with_trainer,
        "runtime": runtime,
    }
    core["lineage_binding_sha256"] = _lineage_binding(core)
    value = {**core, "attestation_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(arguments.output.resolve(), value)
    print(
        json.dumps(
            {
                "status": "attested",
                "attestation_sha256": value["attestation_sha256"],
                "candidate_tree_sha256": candidate["adapter_tree_sha256"],
            },
            sort_keys=True,
        )
    )


def _lineage_binding(value: Mapping[str, Any]) -> str:
    return sha256_bytes(
        canonical_bytes(
            {
                key: value[key]
                for key in (
                    "validator",
                    "trainer_source",
                    "artifacts",
                    "candidate",
                    "training",
                    "collection",
                    "runtime",
                )
            }
        )
    )


def validate_lineage_attestation(path: Path) -> dict[str, Any]:
    value = _descriptor(path, LINEAGE_SCHEMA, "attestation_sha256", "weighted-CE lineage")
    validator = value.get("validator") or {}
    source = validator.get("source") or {}
    trainer_source = value.get("trainer_source") or {}
    artifacts = value.get("artifacts") or {}
    candidate = value.get("candidate") or {}
    training = value.get("training") or {}
    collection = value.get("collection") or {}
    runtime = value.get("runtime") or {}
    job, pod = _serve_names(runtime.get("job"))
    source_root = Path(str(source.get("root", "")))
    module = Path(str(validator.get("module", {}).get("path", "")))
    expected_artifacts = {
        "repair_parent_receipt",
        "collection_manifest",
        "training_plan",
        "training_receipt",
    }
    if not isinstance(artifacts, Mapping) or set(artifacts) != expected_artifacts:
        raise IntegrityError("weighted-CE lineage artifact inventory changed")
    if (
        set(candidate) != CANDIDATE_KEYS
        or set(training) != TRAINING_KEYS
        or set(collection) != COLLECTION_KEYS
        or set(runtime) != RUNTIME_KEYS
    ):
        raise IntegrityError("weighted-CE lineage projection schema changed")
    for label, record in artifacts.items():
        if not isinstance(record, Mapping) or set(record) != {
            "path",
            "file_sha256",
            "body_sha256",
        }:
            raise IntegrityError(f"lineage {label} descriptor changed")
        _sha(record.get("file_sha256"), f"{label} file")
        _sha(record.get("body_sha256"), f"{label} body")
    if (
        value.get("status") != "verified_on_pvc"
        or value.get("outcome_blind") is not True
        or value.get("evaluation_harness_modified") is not False
        or value.get("large_artifacts_rehashed_on_pvc") is not True
        or value.get("exact_trainer_validator_executed") is not True
        or value.get("lineage_binding_sha256") != _lineage_binding(value)
        or set(validator) != {"source", "module", "bound_files"}
        or set(source) != {"root", "git_sha", "files", "bytes", "tree_sha256"}
        or HEX40.fullmatch(str(source.get("git_sha", ""))) is None
        or not _is_within(source_root, ALLOWED_SOURCE_ROOTS)
        or module != source_root / MODULE_RELATIVE
        or validator.get("module", {}).get("sha256")
        != sha256_file(Path(__file__).resolve())
        or validator.get("bound_files")
        != _bound_source_files(Path(__file__).resolve().parents[2])
        or Path(str(artifacts["repair_parent_receipt"]["path"])).resolve()
        != PARENT_RECEIPT_PATH
        or artifacts["repair_parent_receipt"]["file_sha256"] != PARENT_RECEIPT_FILE
        or artifacts["repair_parent_receipt"]["body_sha256"] != PARENT_RECEIPT_BODY
        or Path(str(artifacts["training_receipt"]["path"])).resolve()
        != TRAINING_RECEIPT_PATH
        or Path(str(artifacts["training_plan"]["path"])).resolve()
        != TRAINING_ROOT / "plan.json"
        or artifacts["training_plan"]["file_sha256"]
        != training.get("training_plan_file_sha256")
        or artifacts["training_plan"]["body_sha256"]
        != training.get("training_plan_body_sha256")
        or artifacts["training_receipt"]["file_sha256"]
        != training.get("training_receipt_file_sha256")
        or artifacts["training_receipt"]["body_sha256"]
        != training.get("training_receipt_body_sha256")
        or Path(str(artifacts["collection_manifest"]["path"])).name != "manifest.json"
        or not _is_within(
            Path(str(artifacts["collection_manifest"]["path"])).resolve(),
            ALLOWED_SOURCE_ROOTS,
        )
        or trainer_source
        != {
            "root": str(TRAINER_SOURCE_PATH),
            "git_sha": TRAINER_SOURCE_GIT_SHA,
            **TRAINER_SOURCE_IDENTITY,
        }
        or runtime.get("job_uid") != EXPECTED_JOB_UID
        or runtime.get("pod") != pod
        or runtime.get("pod_uid") != EXPECTED_POD_UID
        or runtime.get("node") != EXPECTED_NODE
        or runtime.get("job_spec_sha256") != EXPECTED_JOB_SPEC_SHA256
        or runtime.get("job_labels_sha256") != EXPECTED_JOB_LABELS_SHA256
        or runtime.get("pod_spec_sha256") != EXPECTED_POD_SPEC_SHA256
        or runtime.get("pod_labels_sha256") != EXPECTED_POD_LABELS_SHA256
        or not str(runtime.get("image_id", "")).endswith("@" + IMAGE_DIGEST)
        or runtime.get("restart_count") != 0
        or runtime.get("executed_in_bound_pod") is not True
        or runtime.get("job") != job
    ):
        raise IntegrityError("weighted-CE PVC lineage binding changed")
    if type(source.get("files")) is not int or source["files"] <= 0:
        raise IntegrityError("lineage source file count changed")
    if type(source.get("bytes")) is not int or source["bytes"] <= 0:
        raise IntegrityError("lineage source byte count changed")
    _sha(source.get("tree_sha256"), "lineage source tree")
    _candidate_policy(candidate)
    _training_policy(training)
    if (
        collection.get("source_git_sha") != COLLECTION_MATERIALIZER_GIT_SHA
        or collection.get("provenance") != COLLECTION_PROVENANCE
        or collection.get("teacher_model") != "gpt-5.6-sol"
        or collection.get("teacher_reasoning_effort") != "low"
        or collection.get("train_corrective_count") != 72
        or collection.get("retention_count") != 24
        or collection.get("heldout_count") != 0
        or collection.get("phase_counts") != COLLECTION_PHASES
        or not _phase_subtype_policy(collection.get("phase_subtype_counts"))
        or collection.get("unique_states") != 96
        or collection.get("manifest_file_sha256")
        != artifacts["collection_manifest"]["file_sha256"]
        or collection.get("manifest_body_sha256")
        != artifacts["collection_manifest"]["body_sha256"]
    ):
        raise IntegrityError("weighted-CE collection lineage changed")
    return value


def _release_argv(
    release: Mapping[str, Any], lineage: Mapping[str, Any], *, pod_uid: str
) -> list[str]:
    receipt = lineage["artifacts"]["training_receipt"]
    candidate = lineage["candidate"]
    trainer = lineage["trainer_source"]
    return [
        str(release["entrypoint"]["sha256"]),
        str(receipt["path"]),
        str(receipt["file_sha256"]),
        str(receipt["body_sha256"]),
        str(candidate["adapter_tree_sha256"]),
        str(candidate["adapter_config_sha256"]),
        str(candidate["stable_marker_sha256"]),
        str(trainer["root"]),
        str(trainer["git_sha"]),
        str(trainer["tree_sha256"]),
        DEFAULT_SERVE_JOB,
        DEFAULT_SERVE_POD,
        pod_uid,
    ]


def render_release(arguments: argparse.Namespace) -> None:
    lineage = validate_lineage_attestation(arguments.lineage_attestation)
    source = arguments.source_root.resolve()
    entrypoint = (source / ENTRYPOINT_RELATIVE).resolve()
    job, pod, _container = _runtime_identity(
        arguments.serve_job_json, arguments.serve_pods_json, require_ready=False
    )
    source_identity = _source_identity(source)
    if (
        arguments.source_git_sha != lineage["validator"]["source"]["git_sha"]
        or source != Path(lineage["validator"]["source"]["root"])
        or source_identity
        != {
            key: lineage["validator"]["source"][key]
            for key in ("files", "bytes", "tree_sha256")
        }
        or sha256_file(_safe_regular(entrypoint, "candidate serve entrypoint"))
        != lineage["validator"]["bound_files"][str(ENTRYPOINT_RELATIVE)]
        or job["metadata"]["uid"] != lineage["runtime"]["job_uid"]
        or pod["metadata"]["uid"] != lineage["runtime"]["pod_uid"]
    ):
        raise IntegrityError("release source, lineage, or reservation changed")
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
            "job_name": DEFAULT_SERVE_JOB,
            "pod_name": DEFAULT_SERVE_POD,
            "pod_uid": EXPECTED_POD_UID,
        },
        "source": lineage["validator"]["source"],
        "entrypoint": partial["entrypoint"],
        "argv": _release_argv(partial, lineage, pod_uid=EXPECTED_POD_UID),
    }
    value = {**core, "release_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(arguments.output.resolve(), value)
    print(json.dumps({"status": "rendered", "release_sha256": value["release_sha256"]}))


def audit_release(path: Path, *, verify_source: bool = True) -> dict[str, Any]:
    value = _descriptor(path, SERVE_RELEASE_SCHEMA, "release_sha256", "candidate release")
    reservation = value.get("reservation") or {}
    source = value.get("source") or {}
    entrypoint = value.get("entrypoint") or {}
    argv = value.get("argv")
    source_root = Path(str(source.get("root", "")))
    program = Path(str(entrypoint.get("path", "")))
    if (
        set(value) != GENERIC_RELEASE_KEYS
        or value.get("status") != "released"
        or value.get("purpose") != PURPOSE
        or value.get("laptop_r01_outcomes_read") is not False
        or value.get("office_chair_outcomes_read") is not False
        or reservation
        != {
            "job_name": DEFAULT_SERVE_JOB,
            "pod_name": DEFAULT_SERVE_POD,
            "pod_uid": EXPECTED_POD_UID,
        }
        or set(source) != {"root", "git_sha", "files", "bytes", "tree_sha256"}
        or HEX40.fullmatch(str(source.get("git_sha", ""))) is None
        or not _is_within(source_root, ALLOWED_SOURCE_ROOTS)
        or program != source_root / ENTRYPOINT_RELATIVE
        or set(entrypoint) != {"path", "sha256"}
        or not isinstance(argv, list)
        or len(argv) != 13
        or argv[0] != entrypoint.get("sha256")
        or Path(str(argv[1])).resolve() != TRAINING_RECEIPT_PATH
        or argv[7:10]
        != [
            str(TRAINER_SOURCE_PATH),
            TRAINER_SOURCE_GIT_SHA,
            TRAINER_SOURCE_IDENTITY["tree_sha256"],
        ]
        or argv[10:] != [DEFAULT_SERVE_JOB, DEFAULT_SERVE_POD, EXPECTED_POD_UID]
    ):
        raise IntegrityError("generic campaign-2 serve release changed")
    for index in (0, 2, 3, 4, 5, 6, 9):
        _sha(argv[index], f"release argv[{index}]")
    if verify_source and (
        _source_identity(source_root)
        != {key: source[key] for key in ("files", "bytes", "tree_sha256")}
        or sha256_file(_safe_regular(program, "candidate serve entrypoint"))
        != entrypoint["sha256"]
    ):
        raise IntegrityError("released candidate source changed")
    return value


def _process_start_ticks(pid: int) -> str:
    if type(pid) is not int or pid <= 1:
        raise IntegrityError("candidate tunnel PID is invalid")
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
    except (OSError, IndexError) as exc:
        raise IntegrityError("candidate tunnel process cannot be inspected") from exc


def attest_endpoint(arguments: argparse.Namespace) -> None:
    lineage = validate_lineage_attestation(arguments.lineage_attestation)
    release = audit_release(arguments.serve_release, verify_source=False)
    job, pod, container = _runtime_identity(
        arguments.serve_job_json, arguments.serve_pods_json, require_ready=True
    )
    if (
        release["source"] != lineage["validator"]["source"]
        or release["argv"] != _release_argv(release, lineage, pod_uid=EXPECTED_POD_UID)
        or job["metadata"]["uid"] != lineage["runtime"]["job_uid"]
        or pod["metadata"]["uid"] != lineage["runtime"]["pod_uid"]
        or container.get("imageID") != lineage["runtime"]["image_id"]
    ):
        raise IntegrityError("live endpoint differs from released PVC lineage")
    candidate = lineage["candidate"]
    alias = _candidate_alias(candidate["adapter_tree_sha256"])
    models = read_json(arguments.models.resolve())
    canary = read_json(arguments.canary.resolve())
    model_rows = models.get("data") if isinstance(models, Mapping) else None
    model_ids = sorted(
        str(row.get("id")) for row in model_rows or [] if isinstance(row, Mapping)
    )
    choices = canary.get("choices") if isinstance(canary, Mapping) else None
    choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else {}
    if (
        model_ids != sorted(["qwen35-exact-lora-parent", alias])
        or canary.get("model") != alias
        or choice.get("finish_reason") != "stop"
        or str(choice.get("message", {}).get("content", "")).strip() != "OK"
    ):
        raise IntegrityError("candidate endpoint model inventory or canary changed")
    tunnel = read_json(arguments.tunnel_status.resolve())
    pid = tunnel.get("pid")
    tunnel_argv = tunnel.get("argv")
    if (
        tunnel.get("process_start_ticks") != _process_start_ticks(pid)
        or not isinstance(tunnel_argv, list)
        or tunnel_argv[-2:] != [f"pod/{DEFAULT_SERVE_POD}", LOCAL_TUNNEL]
    ):
        raise IntegrityError("candidate endpoint tunnel identity changed")
    endpoint_candidate = {
        **candidate,
        "served_model_name": alias,
    }
    endpoint_training = {
        **lineage["training"],
        "lineage_attestation_sha256": lineage["attestation_sha256"],
        "lineage_binding_sha256": lineage["lineage_binding_sha256"],
    }
    core = {
        "schema": ENDPOINT_SCHEMA,
        "status": "ok",
        "outcome_blind": True,
        "evaluation_harness_modified": False,
        "candidate": endpoint_candidate,
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
                arguments.lineage_attestation.resolve(), "attestation_sha256"
            ),
            **lineage["artifacts"],
            "serve_release": _artifact(arguments.serve_release.resolve(), "release_sha256"),
        },
        "training": endpoint_training,
        "collection": lineage["collection"],
        "runtime": {
            **lineage["runtime"],
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
    print(
        json.dumps(
            {
                "status": "attested",
                "receipt_sha256": value["receipt_sha256"],
                "served_model_name": alias,
            },
            sort_keys=True,
        )
    )


def validate_endpoint(path: Path, *, verify_artifacts: bool = True) -> dict[str, Any]:
    value = _descriptor(path, ENDPOINT_SCHEMA, "receipt_sha256", "weighted-CE endpoint")
    candidate = value.get("candidate") or {}
    runtime = value.get("runtime") or {}
    training = value.get("training") or {}
    collection = value.get("collection") or {}
    if (
        set(candidate) != CANDIDATE_KEYS | {"served_model_name"}
        or set(training)
        != TRAINING_KEYS | {"lineage_attestation_sha256", "lineage_binding_sha256"}
        or set(collection) != COLLECTION_KEYS
        or set(runtime) != RUNTIME_KEYS | {"tunnel"}
    ):
        raise IntegrityError("weighted-CE endpoint projection schema changed")
    job, pod = _serve_names(runtime.get("job"))
    _candidate_policy(candidate)
    _training_policy(training)
    alias = _candidate_alias(str(candidate["adapter_tree_sha256"]))
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
        or candidate.get("served_model_name") != alias
        or value.get("model_spec") != expected_model
        or runtime.get("job") != job
        or runtime.get("pod") != pod
        or runtime.get("job_uid") != EXPECTED_JOB_UID
        or runtime.get("pod_uid") != EXPECTED_POD_UID
        or runtime.get("node") != EXPECTED_NODE
        or runtime.get("job_spec_sha256") != EXPECTED_JOB_SPEC_SHA256
        or runtime.get("job_labels_sha256") != EXPECTED_JOB_LABELS_SHA256
        or runtime.get("pod_spec_sha256") != EXPECTED_POD_SPEC_SHA256
        or runtime.get("pod_labels_sha256") != EXPECTED_POD_LABELS_SHA256
        or not str(runtime.get("image_id", "")).endswith("@" + IMAGE_DIGEST)
        or collection.get("manifest_file_sha256")
        != value.get("artifacts", {}).get("collection_manifest", {}).get("file_sha256")
        or collection.get("manifest_body_sha256")
        != value.get("artifacts", {}).get("collection_manifest", {}).get("body_sha256")
        or HEX64.fullmatch(str(training.get("lineage_attestation_sha256", "")))
        is None
        or HEX64.fullmatch(str(training.get("lineage_binding_sha256", ""))) is None
    ):
        raise IntegrityError("weighted-CE endpoint policy changed")
    if verify_artifacts:
        artifacts = value.get("artifacts") or {}
        remote = {
            "repair_parent_receipt",
            "collection_manifest",
            "training_plan",
            "training_receipt",
        }
        if set(artifacts) != {
            "pvc_lineage_attestation",
            "serve_release",
            *remote,
        }:
            raise IntegrityError("weighted-CE endpoint artifact inventory changed")
        lineage_record = artifacts["pvc_lineage_attestation"]
        release_record = artifacts["serve_release"]
        lineage_path = Path(lineage_record["path"]).resolve()
        release_path = Path(release_record["path"]).resolve()
        lineage = validate_lineage_attestation(lineage_path)
        release = audit_release(release_path, verify_source=False)
        if (
            lineage_record != _artifact(lineage_path, "attestation_sha256")
            or release_record != _artifact(release_path, "release_sha256")
            or candidate.get("adapter_tree_sha256")
            != lineage["candidate"]["adapter_tree_sha256"]
            or training.get("lineage_attestation_sha256")
            != lineage["attestation_sha256"]
            or training.get("lineage_binding_sha256")
            != lineage["lineage_binding_sha256"]
            or any(artifacts[label] != lineage["artifacts"][label] for label in remote)
            or {key: item for key, item in candidate.items() if key != "served_model_name"}
            != lineage["candidate"]
            or {
                key: item
                for key, item in training.items()
                if key not in {"lineage_attestation_sha256", "lineage_binding_sha256"}
            }
            != lineage["training"]
            or collection != lineage["collection"]
            or release.get("source") != lineage["validator"]["source"]
            or release.get("argv")
            != _release_argv(release, lineage, pod_uid=EXPECTED_POD_UID)
            or release.get("reservation")
            != {"job_name": job, "pod_name": pod, "pod_uid": runtime["pod_uid"]}
        ):
            raise IntegrityError("weighted-CE endpoint no longer matches lineage")
    return value


def _validated_server_contract(argv: Sequence[str]) -> tuple[Path, Path, str]:
    if len(argv) != 12:
        raise IntegrityError("candidate runner expected twelve bound arguments")
    (
        receipt_raw,
        receipt_file,
        receipt_body,
        candidate_tree,
        adapter_config,
        stable_marker,
        trainer_source_raw,
        trainer_git_sha,
        trainer_tree,
        job,
        pod,
        pod_uid,
    ) = argv
    receipt_path = Path(receipt_raw).resolve()
    trainer_source = Path(trainer_source_raw).resolve()
    receipt = _descriptor(
        receipt_path, RECEIPT_SCHEMA, "receipt_body_sha256", "weighted-CE receipt"
    )
    candidate = receipt.get("candidate") or {}
    expected_job, expected_pod = _serve_names(job)
    trainer_identity = validate_trainer_source(
        trainer_source, git_sha=trainer_git_sha, tree_sha256=trainer_tree
    )
    if (
        receipt_path != TRAINING_RECEIPT_PATH
        or sha256_file(receipt_path) != receipt_file
        or receipt.get("receipt_body_sha256") != receipt_body
        or receipt.get("status") != "ok"
        or receipt.get("scientific_label") != SCIENTIFIC_LABEL
        or receipt.get("artifact_source_git_sha") != TRAINER_SOURCE_GIT_SHA
        or receipt.get("execution_source_git_sha") != TRAINER_SOURCE_GIT_SHA
        or receipt.get("source_step") != 25
        or receipt.get("final_step") != 26
        or receipt.get("optimizer_updates") != 1
        or receipt.get("objective") != "weighted_behavioral_cloning"
        or candidate.get("name") != "step26-action-weighted-ce"
        or Path(str(candidate.get("path", ""))).resolve() != CANDIDATE_PATH
        or candidate.get("tree_sha256") != candidate_tree
        or candidate.get("adapter_config_sha256") != adapter_config
        or candidate.get("stable_marker_sha256") != stable_marker
        or trainer_identity["tree_sha256"] != trainer_tree
        or os.environ.get("POD_UID") != pod_uid
        or pod_uid != EXPECTED_POD_UID
        or os.environ.get("POD_NAME", os.environ.get("HOSTNAME")) != expected_pod
        or os.environ.get("HOSTNAME") != expected_pod
        or (expected_job, expected_pod) != (job, pod)
        or _tree_identity(PARENT_PATH)["tree_sha256"] != PARENT_TREE
    ):
        raise IntegrityError("candidate receipt, source, model, or pod binding changed")
    observed = _tree_identity(CANDIDATE_PATH)
    if (
        observed["tree_sha256"] != candidate_tree
        or sha256_file(CANDIDATE_PATH / "adapter_config.json") != adapter_config
        or sha256_file(CANDIDATE_PATH.parent / "STABLE") != stable_marker
    ):
        raise IntegrityError("candidate direct-LoRA bytes changed")
    alias = _candidate_alias(candidate_tree)
    return PARENT_PATH, CANDIDATE_PATH, alias


def run_server(arguments: argparse.Namespace) -> None:
    parent, adapter, alias = _validated_server_contract(arguments.lineage)
    environment = dict(os.environ)
    environment.pop("VLLM_ALLOW_RUNTIME_LORA_UPDATING", None)
    environment.pop("VLLM_API_KEY", None)
    environment["FLA_TILELANG"] = "0"
    command = build_vllm_argv(parent, adapter, alias)
    os.execvpe(command[0], command, environment)


def audit_trainer_source_command(arguments: argparse.Namespace) -> None:
    value = validate_trainer_source(
        arguments.root,
        git_sha=arguments.git_sha,
        tree_sha256=arguments.tree_sha256,
    )
    print(json.dumps({"valid": True, **value}, sort_keys=True))


def audit_command(arguments: argparse.Namespace) -> None:
    if arguments.kind == "lineage":
        value = validate_lineage_attestation(arguments.path)
        digest = value["attestation_sha256"]
    elif arguments.kind == "serve-release":
        value = audit_release(arguments.path)
        digest = value["release_sha256"]
    else:
        value = validate_endpoint(arguments.path)
        digest = value["receipt_sha256"]
    print(json.dumps({"valid": True, "sha256": digest}, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)

    lineage = commands.add_parser("attest-lineage")
    lineage.add_argument("--source-root", type=Path, required=True)
    lineage.add_argument("--source-git-sha", required=True)
    lineage.add_argument("--trainer-source", type=Path, required=True)
    lineage.add_argument("--training-receipt", type=Path, required=True)
    lineage.add_argument("--collection-manifest", type=Path, required=True)
    lineage.add_argument("--expected-training-file-sha256", required=True)
    lineage.add_argument("--expected-training-body-sha256", required=True)
    lineage.add_argument("--expected-collection-file-sha256", required=True)
    lineage.add_argument("--expected-collection-body-sha256", required=True)
    lineage.add_argument("--serve-job-json", type=Path, required=True)
    lineage.add_argument("--serve-pods-json", type=Path, required=True)
    lineage.add_argument("--output", type=Path, required=True)

    release = commands.add_parser("render-release")
    release.add_argument("--lineage-attestation", type=Path, required=True)
    release.add_argument("--source-root", type=Path, required=True)
    release.add_argument("--source-git-sha", required=True)
    release.add_argument("--serve-job-json", type=Path, required=True)
    release.add_argument("--serve-pods-json", type=Path, required=True)
    release.add_argument("--output", type=Path, required=True)

    run = commands.add_parser("run-server")
    run.add_argument("lineage", nargs=12)

    endpoint = commands.add_parser("attest-endpoint")
    endpoint.add_argument("--lineage-attestation", type=Path, required=True)
    endpoint.add_argument("--serve-release", type=Path, required=True)
    endpoint.add_argument("--serve-job-json", type=Path, required=True)
    endpoint.add_argument("--serve-pods-json", type=Path, required=True)
    endpoint.add_argument("--models", type=Path, required=True)
    endpoint.add_argument("--canary", type=Path, required=True)
    endpoint.add_argument("--tunnel-status", type=Path, required=True)
    endpoint.add_argument("--output", type=Path, required=True)

    trainer = commands.add_parser("audit-trainer-source")
    trainer.add_argument("--root", type=Path, required=True)
    trainer.add_argument("--git-sha", required=True)
    trainer.add_argument("--tree-sha256", required=True)

    audit = commands.add_parser("audit")
    audit.add_argument("kind", choices=("serve-release", "lineage", "endpoint"))
    audit.add_argument("path", type=Path)
    return root


def main() -> None:
    arguments = parser().parse_args()
    try:
        {
            "attest-lineage": attest_lineage,
            "render-release": render_release,
            "run-server": run_server,
            "attest-endpoint": attest_endpoint,
            "audit-trainer-source": audit_trainer_source_command,
            "audit": audit_command,
        }[arguments.command](arguments)
    except (IntegrityError, KeyError, OSError) as exc:
        raise SystemExit(f"InteractiveSolDaggerServeError: {exc}") from exc


if __name__ == "__main__":
    main()
