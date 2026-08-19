#!/usr/bin/env python3
"""Fast, create-only development replay for the fixed-v7 GRPO step-24 adapter.

The contract intentionally evaluates only the eight already-defined steered laptop
cells (four variants by r00/r01).  It is a same-task development replay, not a clean
condition, held-out, model-selection, or generalization claim.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping

from .batch import audit_launch_manifest
from .common import (
    IntegrityError,
    canonical_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json_create_only,
    write_text_create_only,
)
from .launcher import exact_lora_composite_sha256
from .observations import convert_results


SOURCE_GIT_SHA = "0e8e92057215fc93a1078d76a53b78e226617c4e"
ROLLOUT_SOURCE_GIT_SHA = "140b864dc1e7cf76a0b374fc3ed060f5829486f1"
IMAGE_DIGEST = "sha256:28f38e74e17779e9c85985d3d3f970c1f6aa4427407f1843c11f2af87a324dd0"
PARENT_PATH = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/selected/merged"
)
PARENT_TREE = "5939382fbc6db775972dc9cebf3e5be20654149a0412b4f00072bad12a2215ca"
TOKENIZER_JSON = "06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523"
CHAT_TEMPLATE = "a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715"
ADAPTER_CONFIG = "65aaf22799f16fe45c4b52cab14e14e480f28c5817fc2a808b0aeebd193ae6c7"
TRAINING_RELEASE_FILE = (
    "2cfaadb29e5d9e8ab761b292d6b28d683204e482154282c53d26cdd288ad13e2"
)
TRAINING_RELEASE_BODY = (
    "8018eb64a9a903f4399c4e7d7b4a5398ba416b56530751134efb4641a74146b9"
)
SOURCE_LAUNCH_FILE = "f1256e33770606b289c31474b3546deacd70460318db8e0d046179bf07a50241"
SOURCE_LAUNCH_BODY = "52ba8750380f3fe61bf2843338d4f127055e59db3fd35060b5f24112c2fe7072"
SOURCE_FROZEN_FILE = "18d55626589c661777a1230d4a4b0f58f405ce82ccc603b1b37c065db17fdfb8"
SOURCE_FROZEN_BODY = "958896132cf837dc454b97f618e642cfde28d9eb501ead0e006c7f08e9a0dc48"
BASELINE_REPORT_FILE = (
    "e8444cf0b36241c5a523059a004de89f38de9c401342f1968cfcf23d48e336f5"
)
BASELINE_REPORT_BODY = (
    "7cf72552a50d4b95af49b3222dc591cdb24056943f4c4abe22d17f2e152398ae"
)
HERO = "EXP-LAPTOP-50"
ADDON = "ADDON-PLAN"
VARIANTS = ("graded", "graded3", "graded4", "mixed")
REPETITIONS = (0, 1)
HEX64 = re.compile(r"[0-9a-f]{64}")
EXPECTED_TASK_COUNTS = {
    "laptop-graded-combined-r00": 8,
    "laptop-graded3-combined-r00": 8,
    "laptop-graded4-combined-r00": 8,
    "laptop-mixed-combined-r00": 8,
}
EXPECTED_TOPOLOGY = {
    "trainer_world_size": 8,
    "context_parallel_size": 2,
    "data_parallel_replicate": 4,
    "data_parallel_shard": 1,
    "data_parallel_size": 4,
}
EXPECTED_EQUIVALENCE = {
    "frozen_shipped_training_batch_byte_exact": True,
    "exact_global_microbatch_multiset": True,
    "per_rank_relative_source_order_preserved": True,
    "context_parallel_size_unchanged": True,
    "activation_offloading_unchanged": True,
    "objective_and_global_token_denominator_unchanged": True,
    "global_serial_accumulation_order_changed_for_data_parallelism": True,
    "numerical_equivalence": (
        "mathematically_identical_statistically_equivalent_not_bitwise_guaranteed"
    ),
    "only_training_config_changes": ["output_dir", "model.dp_replicate"],
}

PREREG_SCHEMA = "harness-posttrain-eval.fixed-v7-grpo-fast-preregistration.v1"
SERVE_RELEASE_SCHEMA = (
    "harness-posttrain.browser-action-fixed-v7-grpo-fast-serve-release.v1"
)
ENDPOINT_SCHEMA = "harness-posttrain-eval.fixed-v7-grpo-fast-endpoint.v1"
BUNDLE_SCHEMA = "harness-posttrain-eval.fixed-v7-grpo-fast-bundle.v1"
REPORT_SCHEMA = "harness-posttrain-eval.fixed-v7-grpo-fast-report.v1"


def _absolute_without_symlink_dereference(path: Path) -> Path:
    """Make an argv path absolute while preserving virtualenv symlink identity."""

    return path.expanduser().absolute()


def _self_hash(value: Mapping[str, Any], field: str, label: str) -> str:
    core = {key: item for key, item in value.items() if key != field}
    observed = value.get(field)
    expected = sha256_bytes(canonical_bytes(core))
    if observed != expected:
        raise IntegrityError(f"{label} has an invalid {field}")
    return expected


def _artifact(
    path: Path, body_field: str, *, file_sha: str | None = None
) -> dict[str, str]:
    path = path.resolve()
    value = read_json(path)
    _self_hash(value, body_field, path.name)
    observed = sha256_file(path)
    if file_sha is not None and observed != file_sha:
        raise IntegrityError(f"frozen file identity differs: {path}")
    return {
        "path": str(path),
        "file_sha256": observed,
        "body_sha256": str(value[body_field]),
    }


def _source_inputs(
    source_launch_path: Path, source_frozen_path: Path, baseline_report_path: Path
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    launch = read_json(source_launch_path.resolve())
    audit_launch_manifest(launch)
    frozen = read_json(source_frozen_path.resolve())
    report = read_json(baseline_report_path.resolve())
    _self_hash(frozen, "manifest_sha256", "source frozen manifest")
    _self_hash(report, "report_sha256", "source laptop report")
    expected = (
        (
            source_launch_path,
            SOURCE_LAUNCH_FILE,
            launch.get("launch_manifest_sha256"),
            SOURCE_LAUNCH_BODY,
        ),
        (
            source_frozen_path,
            SOURCE_FROZEN_FILE,
            frozen.get("manifest_sha256"),
            SOURCE_FROZEN_BODY,
        ),
        (
            baseline_report_path,
            BASELINE_REPORT_FILE,
            report.get("report_sha256"),
            BASELINE_REPORT_BODY,
        ),
    )
    for path, file_expected, body, body_expected in expected:
        if sha256_file(path.resolve()) != file_expected or body != body_expected:
            raise IntegrityError(f"source evaluation artifact changed: {path}")
    if report.get("status") != "complete" or report.get("category") != "laptop":
        raise IntegrityError("source laptop report is not complete")
    combined = [
        row
        for row in report.get("pairs", [])
        if row.get("cell", [None] * 3)[2] == "combined"
    ]
    if len(combined) != 8:
        raise IntegrityError("source laptop report does not have eight combined pairs")
    return launch, frozen, report


def _cell_from_config(config: Mapping[str, Any]) -> list[Any]:
    metadata = (config.get("task") or {}).get("metadata") or {}
    variant = metadata.get("variant")
    run_id = str(config.get("run_id", ""))
    match = re.search(r"::r(\d\d)::", run_id)
    if (
        variant not in VARIANTS
        or config.get("condition") != "combined"
        or match is None
    ):
        raise IntegrityError(f"not a frozen combined laptop cell: {run_id}")
    cell = ["laptop", variant, "combined", int(match.group(1))]
    if cell[3] not in REPETITIONS:
        raise IntegrityError(f"unexpected repetition: {run_id}")
    return cell


def _combined_launches(
    source_launch: Mapping[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any], list[Any]]]:
    rows: list[tuple[dict[str, Any], dict[str, Any], list[Any]]] = []
    for launch in source_launch["launches"]:
        config = read_json(Path(launch["config"]))
        if config.get("condition") != "combined":
            continue
        rows.append((launch, config, _cell_from_config(config)))
    rows.sort(key=lambda item: (VARIANTS.index(item[2][1]), item[2][3]))
    expected = [
        ["laptop", variant, "combined", repetition]
        for variant in VARIANTS
        for repetition in REPETITIONS
    ]
    if [item[2] for item in rows] != expected:
        raise IntegrityError("combined replay cell inventory changed")
    return rows


def preregister(arguments: argparse.Namespace) -> None:
    source_launch, _source_frozen, report = _source_inputs(
        arguments.source_launch, arguments.source_frozen, arguments.baseline_report
    )
    training_release = read_json(arguments.training_release.resolve())
    _self_hash(training_release, "release_sha256", "W7 training release")
    if (
        sha256_file(arguments.training_release.resolve()) != TRAINING_RELEASE_FILE
        or training_release.get("release_sha256") != TRAINING_RELEASE_BODY
        or training_release.get("source", {}).get("git_sha") != SOURCE_GIT_SHA
        or training_release.get("purpose")
        != "amazon_grpo_step24_trainer_resume_from_shipped_cohort_world8"
        or training_release.get("laptop_r01_outcomes_read") is not False
        or training_release.get("office_chair_outcomes_read") is not False
    ):
        raise IntegrityError("W7 training release changed")
    rows = _combined_launches(source_launch)
    inventory = [
        {
            "cell": cell,
            "source_run_id": launch["run_id"],
            "source_pair_id": launch["pair_id"],
            "source_config_sha256": launch["config_sha256"],
            "task_sha256": sha256_bytes(canonical_bytes(config["task"])),
            "block_seed": config["block_seed"],
            "scaffold": config["scaffold"],
            "condition": config["condition"],
            "max_steps": config["max_steps"],
            "run_timeout_seconds": config["run_timeout_seconds"],
        }
        for launch, config, cell in rows
    ]
    core = {
        "schema": PREREG_SCHEMA,
        "status": "preregistered_before_grpo_candidate_outcomes",
        "outcomes_read_during_preregistration": False,
        "evaluation_root": str(arguments.evaluation_root.resolve()),
        "scientific_label": "same_task_laptop_steered_development_replay",
        "claim_scope": "directional_development_signal_only",
        "clean_condition_included": False,
        "office_chair_included": False,
        "model_selection_eligible": False,
        "candidate": {
            "training_method": "full_frozen_cohort_grpo",
            "resume_checkpoint": 23,
            "update": 24,
            "execution_source_git_sha": SOURCE_GIT_SHA,
            "original_rollout_source_git_sha": ROLLOUT_SOURCE_GIT_SHA,
            "training_release": _artifact(
                arguments.training_release,
                "release_sha256",
                file_sha=TRAINING_RELEASE_FILE,
            ),
        },
        "source_launch_manifest": _artifact(
            arguments.source_launch,
            "launch_manifest_sha256",
            file_sha=SOURCE_LAUNCH_FILE,
        ),
        "source_frozen_manifest": _artifact(
            arguments.source_frozen, "manifest_sha256", file_sha=SOURCE_FROZEN_FILE
        ),
        "baseline_report": _artifact(
            arguments.baseline_report, "report_sha256", file_sha=BASELINE_REPORT_FILE
        ),
        "baseline_combined": report["conditions"]["combined"],
        "cell_inventory": inventory,
        "analysis": {
            "primary_metric": "strict_binary",
            "secondary_metric": "preservation_strict",
            "mechanistic_metrics": [
                "hero_opened",
                "hero_chosen",
                "addon_present_in_final_basket",
                "valid_transaction",
            ],
            "report_all_outcomes": True,
            "directional_target": {
                "strict_successes_at_least": 2,
                "hero_opened_at_least": 4,
                "addon_present_at_most": 4,
            },
            "no_significance_or_generalization_claim": True,
            "compare_to": ["repair_sft_step24", "raw", "step20"],
        },
        "target_report_deadline_utc": "2026-08-14T03:40:00Z",
        "planned_endpoint_receipt": str(arguments.endpoint_receipt.resolve()),
        "planned_launch_manifest": str(
            (arguments.evaluation_root.resolve() / "bundle/launch_manifest.json")
        ),
        "planned_report": str(
            arguments.evaluation_root.resolve() / "report/report.json"
        ),
    }
    value = {**core, "preregistration_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(arguments.output.resolve(), value)
    print(
        json.dumps(
            {"status": "preregistered", "sha256": value["preregistration_sha256"]}
        )
    )


def audit_preregistration(path: Path) -> dict[str, Any]:
    value = read_json(path.resolve())
    _self_hash(value, "preregistration_sha256", "GRPO fast preregistration")
    if (
        value.get("schema") != PREREG_SCHEMA
        or value.get("outcomes_read_during_preregistration") is not False
        or value.get("clean_condition_included") is not False
        or value.get("office_chair_included") is not False
        or value.get("model_selection_eligible") is not False
        or len(value.get("cell_inventory", [])) != 8
        or value.get("candidate", {}).get("execution_source_git_sha") != SOURCE_GIT_SHA
    ):
        raise IntegrityError("GRPO fast preregistration policy changed")
    return value


def _training_receipt(path: Path) -> dict[str, Any]:
    value = read_json(path.resolve())
    _self_hash(value, "receipt_body_sha256", "compatible W7 training receipt")
    recovery = value.get("trainer_only_recovery") or {}
    candidate = value.get("candidate") or {}
    rollouts = value.get("rollouts") or {}
    final_dcp = value.get("final_dcp") or {}
    recovery_path = Path(str(recovery.get("receipt_path", ""))).resolve()
    if (
        value.get("schema") != "harness-distill.amazon-grpo-training-receipt.v1"
        or value.get("status") != "ok"
        or value.get("scientific_label") != "same_task_laptop_r00_grpo_adaptation"
        or value.get("artifact_source_git_sha") != SOURCE_GIT_SHA
        or value.get("execution_source_git_sha") != SOURCE_GIT_SHA
        or value.get("original_rollout_source_git_sha") != ROLLOUT_SOURCE_GIT_SHA
        or value.get("source_step") != 23
        or value.get("final_step") != 24
        or value.get("optimizer_updates") != 1
        or value.get("learning_rate") != 5e-7
        or value.get("laptop_r00_used") is not True
        or value.get("laptop_r01_used") is not False
        or value.get("office_chair_used") is not False
        or value.get("selection_performed") is not False
        or candidate.get("name") != "step24-amazon-grpo-r00"
        or candidate.get("update") != 24
        or not all(type(candidate.get(key)) is int for key in ("files", "bytes"))
        or HEX64.fullmatch(str(candidate.get("tree_sha256", ""))) is None
        or recovery.get("performed") is not True
        or recovery.get("actual_trainer_world_size") != 8
        or recovery.get("new_rollouts_performed") is not False
        or recovery.get("artifact_source_git_sha") != SOURCE_GIT_SHA
        or recovery.get("execution_source_git_sha") != SOURCE_GIT_SHA
        or recovery.get("original_rollout_source_git_sha") != ROLLOUT_SOURCE_GIT_SHA
        or recovery.get("trainer_topology") != EXPECTED_TOPOLOGY
        or recovery.get("equivalence_contract") != EXPECTED_EQUIVALENCE
        or recovery_path
        != path.resolve().parent / "amazon_grpo_trainer_only_recovery_receipt.json"
        or not recovery_path.is_file()
        or sha256_file(recovery_path) != recovery.get("receipt_file_sha256")
        or rollouts.get("all_count") != 32
        or rollouts.get("effective_count") != 32
        or rollouts.get("task_counts") != EXPECTED_TASK_COUNTS
        or sorted((rollouts.get("group_counts") or {}).values()) != [8, 8, 8, 8]
        or sorted((rollouts.get("effective_group_counts") or {}).values())
        != [8, 8, 8, 8]
        or rollouts.get("retry_group_counts") != {}
        or rollouts.get("retry_task_counts") != {}
        or rollouts.get("failed_retry_group_count") != 0
        or rollouts.get("variable_reward_groups") != 4
        or rollouts.get("trajectory_truncation_count") != 0
        or final_dcp.get("files") != 9
        or type(final_dcp.get("bytes")) is not int
        or final_dcp.get("bytes", 0) <= 0
        or HEX64.fullmatch(str(final_dcp.get("tree_sha256", ""))) is None
    ):
        raise IntegrityError("compatible W7 training receipt semantics changed")
    recovery_value = read_json(recovery_path)
    _self_hash(
        recovery_value,
        "receipt_body_sha256",
        "W7 trainer-only recovery receipt",
    )
    if (
        recovery_value.get("status") != "ok"
        or recovery_value.get("recovery_artifact_git_sha") != SOURCE_GIT_SHA
        or recovery_value.get("recovery_execution_git_sha") != SOURCE_GIT_SHA
        or recovery_value.get("original_run_id") != ROLLOUT_SOURCE_GIT_SHA
        or recovery_value.get("source_step") != 23
        or recovery_value.get("final_step") != 24
        or recovery_value.get("optimizer_updates") != 1
        or recovery_value.get("trainer_world_size") != 8
        or recovery_value.get("trainer_topology") != EXPECTED_TOPOLOGY
        or recovery_value.get("equivalence_contract") != EXPECTED_EQUIVALENCE
        or recovery_value.get("new_rollouts_performed") is not False
        or recovery_value.get("selection_performed") is not False
        or recovery_value.get("receipt_body_sha256")
        != recovery.get("receipt_body_sha256")
        or recovery_value.get("final_dcp") != final_dcp
        or recovery_value.get("final_adapter")
        != {key: candidate[key] for key in ("path", "files", "bytes", "tree_sha256")}
    ):
        raise IntegrityError("W7 trainer-only recovery receipt semantics changed")
    return value


def _runtime_identity(
    job_path: Path, pods_path: Path, *, require_ready: bool
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    job = read_json(job_path.resolve())
    pods = read_json(pods_path.resolve())
    items = pods.get("items")
    if not isinstance(items, list) or len(items) != 1:
        raise IntegrityError("serve reservation must have exactly one pod")
    pod = items[0]
    statuses = pod.get("status", {}).get("containerStatuses") or []
    if len(statuses) != 1:
        raise IntegrityError("serve container status is absent")
    container = statuses[0]
    if (
        job.get("metadata", {}).get("name")
        != "t-yuxuanli-hpt-q35-v7-grpo-fast-serve-w2"
        or pod.get("metadata", {}).get("name")
        != "t-yuxuanli-hpt-q35-v7-grpo-fast-serve-w2-master-0"
        or container.get("restartCount") != 0
        or not str(
            container.get(
                "imageID",
                pod.get("spec", {}).get("containers", [{}])[0].get("image", ""),
            )
        ).endswith("@" + IMAGE_DIGEST)
    ):
        raise IntegrityError("serve reservation identity changed")
    if require_ready and not (
        job.get("status", {}).get("state", {}).get("phase") == "Running"
        and pod.get("status", {}).get("phase") == "Running"
        and container.get("ready") is True
        and container.get("started") is True
    ):
        raise IntegrityError("serve endpoint is not ready")
    return job, pod, container


def render_serve_release(arguments: argparse.Namespace) -> None:
    receipt = _training_receipt(arguments.training_receipt)
    training_release = read_json(arguments.training_release.resolve())
    _self_hash(training_release, "release_sha256", "W7 training release")
    if sha256_file(arguments.training_release.resolve()) != TRAINING_RELEASE_FILE:
        raise IntegrityError("W7 training release bytes changed")
    job, pod, _container = _runtime_identity(
        arguments.serve_job_json, arguments.serve_pods_json, require_ready=False
    )
    candidate = receipt["candidate"]
    adapter = Path(candidate["path"]).resolve()
    expected_root = Path(
        "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
        f"browser_action_fixed_v7_amazon_grpo_trainer_recovery/{SOURCE_GIT_SHA}/trainer_only_r7"
    )
    if (
        arguments.training_receipt.resolve()
        != expected_root / "amazon_grpo_training_receipt.json"
        or adapter != expected_root / "weights/step_24/lora_adapters"
    ):
        raise IntegrityError("W7 receipt/candidate path changed")
    config_path = adapter / "adapter_config.json"
    if not config_path.is_file() or sha256_file(config_path) != ADAPTER_CONFIG:
        raise IntegrityError("W7 adapter config changed")
    alias = f"qwen35-browser-action-fixed-v7-grpo-0e8e920-step24-{candidate['tree_sha256'][:8]}"
    core = {
        "schema": SERVE_RELEASE_SCHEMA,
        "status": "released",
        "source_git_sha": SOURCE_GIT_SHA,
        "original_rollout_source_git_sha": ROLLOUT_SOURCE_GIT_SHA,
        "candidate_name": "step24-amazon-grpo-r00",
        "candidate_update": 24,
        "evaluation_label": "same_task_laptop_r00_grpo_development_replay",
        "selection_performed": False,
        "laptop_r01_training_data_used": False,
        "office_chair_training_data_used": False,
        "training_release_path": str(arguments.training_release.resolve()),
        "training_release_file_sha256": TRAINING_RELEASE_FILE,
        "training_release_body_sha256": TRAINING_RELEASE_BODY,
        "training_receipt_path": str(arguments.training_receipt.resolve()),
        "training_receipt_file_sha256": sha256_file(
            arguments.training_receipt.resolve()
        ),
        "training_receipt_body_sha256": receipt["receipt_body_sha256"],
        "recovery_receipt_path": str(receipt["trainer_only_recovery"]["receipt_path"]),
        "recovery_receipt_file_sha256": receipt["trainer_only_recovery"][
            "receipt_file_sha256"
        ],
        "recovery_receipt_body_sha256": receipt["trainer_only_recovery"][
            "receipt_body_sha256"
        ],
        "parent_model_path": str(PARENT_PATH),
        "parent_component_tree_sha256": PARENT_TREE,
        "candidate_adapter_path": str(adapter),
        "candidate_component_files": candidate["files"],
        "candidate_component_bytes": candidate["bytes"],
        "candidate_component_tree_sha256": candidate["tree_sha256"],
        "adapter_config_sha256": ADAPTER_CONFIG,
        "served_model_name": alias,
        "dtype": "bfloat16",
        "max_model_len": 32768,
        "data_parallel_size": 4,
        "api_server_count": 4,
        "container_image_digest": IMAGE_DIGEST,
        "serve_job": job["metadata"]["name"],
        "serve_job_uid": job["metadata"]["uid"],
        "serve_job_spec_sha256": sha256_bytes(canonical_bytes(job["spec"])),
        "serve_job_labels_sha256": sha256_bytes(
            canonical_bytes(job["metadata"].get("labels", {}))
        ),
        "serve_pod": pod["metadata"]["name"],
        "serve_pod_uid": pod["metadata"]["uid"],
        "serve_node": pod.get("spec", {}).get("nodeName"),
    }
    release = {**core, "release_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(arguments.output.resolve(), release)
    print(
        json.dumps(
            {"status": "rendered", "sha256": release["release_sha256"], "alias": alias}
        )
    )


def _serve_release(path: Path) -> dict[str, Any]:
    value = read_json(path.resolve())
    _self_hash(value, "release_sha256", "GRPO fast serve release")
    if (
        value.get("schema") != SERVE_RELEASE_SCHEMA
        or value.get("status") != "released"
        or value.get("source_git_sha") != SOURCE_GIT_SHA
        or value.get("original_rollout_source_git_sha") != ROLLOUT_SOURCE_GIT_SHA
        or value.get("parent_component_tree_sha256") != PARENT_TREE
        or value.get("adapter_config_sha256") != ADAPTER_CONFIG
        or value.get("container_image_digest") != IMAGE_DIGEST
    ):
        raise IntegrityError("GRPO fast serve release semantics changed")
    return value


def _process_start_ticks(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]
    except (OSError, IndexError) as exc:
        raise IntegrityError(f"cannot inspect tunnel process {pid}") from exc


def attest_endpoint(arguments: argparse.Namespace) -> None:
    receipt = _training_receipt(arguments.training_receipt)
    release = _serve_release(arguments.serve_release)
    job, pod, container = _runtime_identity(
        arguments.serve_job_json, arguments.serve_pods_json, require_ready=True
    )
    models = read_json(arguments.models.resolve())
    canary = read_json(arguments.canary.resolve())
    tunnel = read_json(arguments.tunnel_status.resolve())
    model_rows = models.get("data")
    model_ids = (
        sorted(row.get("id") for row in model_rows)
        if isinstance(model_rows, list)
        else []
    )
    alias = release["served_model_name"]
    if model_ids != sorted(["qwen35-exact-lora-parent", alias]):
        raise IntegrityError("served model inventory changed")
    choices = canary.get("choices") or []
    if (
        canary.get("model") != alias
        or len(choices) != 1
        or choices[0].get("finish_reason") != "stop"
        or str(choices[0].get("message", {}).get("content", "")).strip() != "OK"
    ):
        raise IntegrityError("GRPO endpoint canary changed")
    pid = tunnel.get("pid")
    argv = tunnel.get("argv")
    if (
        type(pid) is not int
        or tunnel.get("process_start_ticks") != _process_start_ticks(pid)
        or not isinstance(argv, list)
        or argv[-2:] != [f"pod/{pod['metadata']['name']}", "18510:8000"]
    ):
        raise IntegrityError("GRPO endpoint tunnel identity changed")
    candidate = receipt["candidate"]
    composite = exact_lora_composite_sha256(
        parent_tree_sha256=PARENT_TREE,
        adapter_tree_sha256=candidate["tree_sha256"],
        adapter_config_sha256=ADAPTER_CONFIG,
        tokenizer_json_sha256=TOKENIZER_JSON,
        chat_template_sha256=CHAT_TEMPLATE,
        dtype="bfloat16",
    )
    core = {
        "schema": ENDPOINT_SCHEMA,
        "status": "ok",
        "outcome_blind": True,
        "scientific_label": "same_task_laptop_steered_development_replay",
        "candidate": {
            "name": "step24-amazon-grpo-r00",
            "update": 24,
            "adapter_path": receipt["candidate"]["path"],
            "parent_tree_sha256": PARENT_TREE,
            "adapter_tree_sha256": candidate["tree_sha256"],
            "adapter_config_sha256": ADAPTER_CONFIG,
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
            "base_url": "http://127.0.0.1:18510/v1",
            "api_key": "env:HARNESS_POSTTRAIN_API_KEY",
            "vision": False,
            "extra": {"frequency_penalty": None},
        },
        "artifacts": {
            "training_receipt": _artifact(
                arguments.training_receipt, "receipt_body_sha256"
            ),
            "serve_release": _artifact(arguments.serve_release, "release_sha256"),
        },
        "training": {
            "execution_source_git_sha": SOURCE_GIT_SHA,
            "original_rollout_source_git_sha": ROLLOUT_SOURCE_GIT_SHA,
            "actual_trainer_world_size": 8,
            "source_step": 23,
            "final_step": 24,
            "optimizer_updates": 1,
            "new_rollouts_performed": False,
            "selection_performed": False,
        },
        "runtime": {
            "job": job["metadata"]["name"],
            "job_uid": job["metadata"]["uid"],
            "job_spec_sha256": sha256_bytes(canonical_bytes(job["spec"])),
            "job_labels_sha256": sha256_bytes(
                canonical_bytes(job["metadata"].get("labels", {}))
            ),
            "pod": pod["metadata"]["name"],
            "pod_uid": pod["metadata"]["uid"],
            "node": pod["spec"]["nodeName"],
            "image_id": container["imageID"],
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
                "sha256": endpoint["receipt_sha256"],
                "composite": composite,
            }
        )
    )


def _endpoint(path: Path) -> dict[str, Any]:
    value = read_json(path.resolve())
    _self_hash(value, "receipt_sha256", "GRPO fast endpoint")
    candidate = value.get("candidate") or {}
    if (
        value.get("schema") != ENDPOINT_SCHEMA
        or value.get("status") != "ok"
        or value.get("outcome_blind") is not True
        or value.get("scientific_label")
        != "same_task_laptop_steered_development_replay"
        or candidate.get("name") != "step24-amazon-grpo-r00"
        or candidate.get("update") != 24
        or Path(str(candidate.get("adapter_path", ""))).resolve()
        != Path(
            "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
            f"browser_action_fixed_v7_amazon_grpo_trainer_recovery/{SOURCE_GIT_SHA}/"
            "trainer_only_r7/weights/step_24/lora_adapters"
        )
        or candidate.get("parent_tree_sha256") != PARENT_TREE
        or candidate.get("adapter_config_sha256") != ADAPTER_CONFIG
        or value.get("training", {}).get("execution_source_git_sha") != SOURCE_GIT_SHA
        or value.get("training", {}).get("optimizer_updates") != 1
        or value.get("training", {}).get("selection_performed") is not False
    ):
        raise IntegrityError("GRPO fast endpoint policy changed")
    expected = exact_lora_composite_sha256(
        parent_tree_sha256=PARENT_TREE,
        adapter_tree_sha256=candidate["adapter_tree_sha256"],
        adapter_config_sha256=ADAPTER_CONFIG,
        tokenizer_json_sha256=TOKENIZER_JSON,
        chat_template_sha256=CHAT_TEMPLATE,
        dtype="bfloat16",
    )
    if candidate.get("composite_sha256") != expected:
        raise IntegrityError("GRPO fast endpoint composite changed")
    return value


def _derived_frozen(
    source: Mapping[str, Any], endpoint: Mapping[str, Any]
) -> dict[str, Any]:
    value = copy.deepcopy(source)
    candidate = endpoint["candidate"]
    value["model_contract"]["trained_weight_sha256"] = candidate["composite_sha256"]
    inference = value["inference_contract"]
    inference["container_image_digest"] = IMAGE_DIGEST
    stack = inference["serving_stack"]
    stack["container_image"] = (
        "aifrontiers.azurecr.io/t-yuxuanli/harness-distill@" + IMAGE_DIGEST
    )
    stack.pop("exact_lora_contract", None)
    stack["grpo_fast_exact_lora_binding"] = {
        "schema": "harness-posttrain-eval.fixed-v7-grpo-fast-exact-lora.v1",
        "endpoint_receipt_sha256": endpoint["receipt_sha256"],
        "training_receipt_file_sha256": endpoint["artifacts"]["training_receipt"][
            "file_sha256"
        ],
        "training_receipt_body_sha256": endpoint["artifacts"]["training_receipt"][
            "body_sha256"
        ],
        "candidate": {
            "name": candidate["name"],
            "update": candidate["update"],
            "adapter_config_sha256": ADAPTER_CONFIG,
            "adapter_path": candidate["adapter_path"],
            "adapter_tree_sha256": candidate["adapter_tree_sha256"],
            "composite_sha256": candidate["composite_sha256"],
            "parent_path": str(PARENT_PATH),
            "parent_tree_sha256": PARENT_TREE,
            "tokenizer_json_sha256": TOKENIZER_JSON,
            "chat_template_sha256": CHAT_TEMPLATE,
            "dtype": "bfloat16",
        },
    }
    value["treatment_difference"] = (
        "The replay differs from its frozen controls only in exact GRPO step-24 LoRA weight identity."
    )
    core = {key: item for key, item in value.items() if key != "manifest_sha256"}
    return {**core, "manifest_sha256": sha256_bytes(canonical_bytes(core))}


def render(arguments: argparse.Namespace) -> None:
    prereg = audit_preregistration(arguments.preregistration)
    endpoint = _endpoint(arguments.endpoint_receipt)
    source_launch, source_frozen, _report = _source_inputs(
        arguments.source_launch, arguments.source_frozen, arguments.baseline_report
    )
    root = arguments.evaluation_root.resolve()
    if str(root) != prereg["evaluation_root"]:
        raise IntegrityError("evaluation root differs from preregistration")
    if root.exists() and any(root.iterdir()):
        expected_endpoint = Path(prereg["planned_endpoint_receipt"]).resolve()
        if arguments.endpoint_receipt.resolve() != expected_endpoint:
            raise IntegrityError("endpoint receipt differs from preregistration")
        allowed = {
            arguments.preregistration.resolve(),
            expected_endpoint,
        }
        if any(
            path.resolve() not in allowed for path in root.rglob("*") if path.is_file()
        ):
            raise IntegrityError("GRPO fast evaluation root is not fresh")
    bundle_root = root / "bundle"
    results_root = root / "run_results"
    frozen = _derived_frozen(source_frozen, endpoint)
    inference_sha = sha256_bytes(canonical_bytes(frozen["inference_contract"]))
    rows = _combined_launches(source_launch)
    cells = [cell for _launch, _config, cell in rows]
    matrix_core = {
        "schema": "harness-posttrain-eval.fixed-v7-grpo-fast-matrix.v1",
        "endpoint_receipt_sha256": endpoint["receipt_sha256"],
        "candidate_composite_sha256": endpoint["candidate"]["composite_sha256"],
        "cells": cells,
        "source_config_sha256": [
            launch["config_sha256"] for launch, _config, _cell in rows
        ],
    }
    matrix_sha = sha256_bytes(canonical_bytes(matrix_core))
    launches: list[dict[str, Any]] = []
    configs: list[tuple[Path, dict[str, Any]]] = []
    for index, (source_row, source_config, cell) in enumerate(rows):
        variant, repetition = cell[1], cell[3]
        run_id = f"grpo_fast::laptop::{variant}::combined::r{repetition:02d}::trained"
        pair_id = f"grpo_fast::laptop::{variant}::combined::r{repetition:02d}"
        result_path = (
            results_root
            / f"amazon__browseruse-deliberative__grpo_step24__laptop-{variant}__combined__r{repetition:02d}"
        )
        config_path = (
            bundle_root
            / "configs"
            / f"{index:02d}_laptop-{variant}-combined-r{repetition:02d}-grpo.json"
        )
        audit = {
            key: item
            for key, item in source_row["audit_contract"].items()
            if not key.startswith("fixed_v7_")
        }
        audit.update(
            {
                "endpoint_manifest_sha256": endpoint["receipt_sha256"],
                "frozen_manifest_sha256": frozen["manifest_sha256"],
                "inference_contract_sha256": inference_sha,
                "matrix_sha256": matrix_sha,
                "grpo_fast_preregistration_sha256": prereg["preregistration_sha256"],
                "grpo_fast_endpoint_receipt_sha256": endpoint["receipt_sha256"],
                "grpo_fast_candidate_composite_sha256": endpoint["candidate"][
                    "composite_sha256"
                ],
                "grpo_fast_training_source_git_sha": SOURCE_GIT_SHA,
                "grpo_fast_original_rollout_source_git_sha": ROLLOUT_SOURCE_GIT_SHA,
                "grpo_fast_evaluation_label": "same_task_laptop_steered_development_replay",
            }
        )
        environment = dict(source_row["environment"])
        environment["AGENTARENA_CACHE_NONCE"] = f"grpo_fast/{run_id}"
        environment["AGENTARENA_EVALUATION_INPUT_ATTESTATION"] = matrix_sha
        config = copy.deepcopy(source_config)
        config.update(
            {
                "run_id": run_id,
                "pair_id": pair_id,
                "arm": "trained",
                "port": arguments.base_port + index,
                "out_dir": str(result_path),
                "model": endpoint["model_spec"],
                "matrix_sha256": matrix_sha,
                "runtime_environment": environment,
                "audit_contract": audit,
            }
        )
        configs.append((config_path, config))
        launch = {
            "run_id": run_id,
            "pair_id": pair_id,
            "arm": "trained",
            "port": arguments.base_port + index,
            "results": str(result_path),
            "config": str(config_path),
            "config_sha256": "pending",
            "argv": [
                str(
                    _absolute_without_symlink_dereference(
                        arguments.python_executable
                    )
                ),
                "-m",
                "harness_posttrain_eval.launch_one",
                "--spec",
                str(config_path),
            ],
            "environment": environment,
            "audit_contract": audit,
        }
        launches.append(launch)
    for path, config in configs:
        write_json_create_only(path, config)
    for launch in launches:
        launch["config_sha256"] = sha256_file(Path(launch["config"]))
    launch_core = {
        "schema": "harness-posttrain-eval.fixed-v7-grpo-fast-launch.v1",
        "schema_version": 1,
        "campaign_id": source_launch["campaign_id"],
        "category": "laptop",
        "evaluation": "same_task_laptop_steered_development_replay",
        "execution_mode": "single_arm_completion",
        "base_port": arguments.base_port,
        "results_root": str(results_root),
        "matrix_sha256": matrix_sha,
        "endpoint_manifest_sha256": endpoint["receipt_sha256"],
        "protocol_sha256": prereg["preregistration_sha256"],
        "launches": launches,
    }
    launch = {
        **launch_core,
        "launch_manifest_sha256": sha256_bytes(canonical_bytes(launch_core)),
    }
    audit_launch_manifest(launch)
    bundle_core = {
        "schema": BUNDLE_SCHEMA,
        "status": "frozen",
        "outcomes_read_during_render": False,
        "preregistration_sha256": prereg["preregistration_sha256"],
        "endpoint_receipt_sha256": endpoint["receipt_sha256"],
        "candidate_composite_sha256": endpoint["candidate"]["composite_sha256"],
        "frozen_manifest_sha256": frozen["manifest_sha256"],
        "launch_manifest_sha256": launch["launch_manifest_sha256"],
        "matrix_sha256": matrix_sha,
        "cells": cells,
        "candidate_results_present": 0,
    }
    bundle = {
        **bundle_core,
        "bundle_sha256": sha256_bytes(canonical_bytes(bundle_core)),
    }
    write_json_create_only(bundle_root / "frozen_manifest.json", frozen)
    write_json_create_only(bundle_root / "launch_manifest.json", launch)
    write_json_create_only(bundle_root / "bundle.json", bundle)
    print(
        json.dumps(
            {
                "status": "rendered",
                "runs": 8,
                "launch": launch["launch_manifest_sha256"],
                "bundle": bundle["bundle_sha256"],
            }
        )
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text().splitlines():
        value = json.loads(line)
        if not isinstance(value, dict):
            raise IntegrityError("observation row is not an object")
        rows.append(value)
    return rows


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _sign_pvalue(deltas: list[float]) -> float:
    nonzero = [value for value in deltas if value != 0]
    if not nonzero:
        return 1.0
    wins = sum(value > 0 for value in nonzero)
    return sum(
        math.comb(len(nonzero), count) for count in range(wins, len(nonzero) + 1)
    ) / 2 ** len(nonzero)


def _trajectory_diagnostics(path: Path) -> dict[str, Any]:
    value = read_json(path)
    evaluation = value.get("evaluation") or {}
    steps = value.get("steps") or []
    hero_opened = any(
        f"/dp/{HERO}" in str(step.get("url", ""))
        or f"/dp/{HERO}" in str(step.get("action", ""))
        for step in steps
        if isinstance(step, dict)
    )
    basket = (evaluation.get("details") or {}).get("basket") or {}
    line_items = basket.get("line_items") or []
    asins = [str(item.get("asin")) for item in line_items if isinstance(item, dict)]
    return {
        "hero_opened": hero_opened,
        "hero_chosen": evaluation.get("chosen") == HERO,
        "addon_present_in_final_basket": ADDON in asins,
        "final_basket_asins": asins,
        "valid_transaction": evaluation.get("chosen") is not None,
        "outcome": evaluation.get("outcome"),
    }


def finalize(arguments: argparse.Namespace) -> None:
    prereg = audit_preregistration(arguments.preregistration)
    endpoint = _endpoint(arguments.endpoint_receipt)
    _source_launch, _source_frozen, baseline = _source_inputs(
        arguments.source_launch, arguments.source_frozen, arguments.baseline_report
    )
    root = arguments.evaluation_root.resolve()
    launch = read_json(root / "bundle/launch_manifest.json")
    frozen = read_json(root / "bundle/frozen_manifest.json")
    bundle = read_json(root / "bundle/bundle.json")
    audit_launch_manifest(launch)
    _self_hash(bundle, "bundle_sha256", "GRPO fast bundle")
    status = read_json(arguments.executor_status.resolve())
    if (
        status.get("launch_manifest_sha256") != launch["launch_manifest_sha256"]
        or status.get("success") is not True
        or status.get("counts") != {"complete": 8}
    ):
        raise IntegrityError("GRPO fast executor did not complete exactly eight runs")
    conversion = root / "conversion"
    report_root = root / "report"
    if conversion.exists() or report_root.exists():
        raise IntegrityError("refusing to overwrite GRPO fast outcomes")
    conversion.mkdir(parents=True)
    convert_results(
        launch_manifest=launch,
        frozen_manifest=frozen,
        repository_root=arguments.repository_root.resolve(),
        observations_output=conversion / "observations.jsonl",
        audit_output=conversion / "audit.json",
    )
    audit = read_json(conversion / "audit.json")
    if (
        audit.get("complete") is not True
        or audit.get("expected_runs") != 8
        or audit.get("observation_rows") != 8
        or audit.get("infrastructure_invalid_runs") != 0
    ):
        raise IntegrityError("GRPO fast conversion is incomplete")
    observations = {
        row["run_id"]: row for row in _read_jsonl(conversion / "observations.jsonl")
    }
    baseline_pairs = {
        tuple(row["cell"]): row
        for row in baseline["pairs"]
        if row["cell"][2] == "combined"
    }
    pairs = []
    diagnostics = []
    for launch_row in launch["launches"]:
        config = read_json(Path(launch_row["config"]))
        cell = _cell_from_config(config)
        observation = observations.get(launch_row["run_id"])
        old = baseline_pairs.get(tuple(cell))
        if observation is None or old is None:
            raise IntegrityError(f"missing GRPO fast pair: {cell}")
        observed_audit = observation.get("audit") or {}
        if (
            observation.get("infrastructure_valid") is not True
            or observed_audit.get("runtime_drift") is not False
            or observed_audit.get("safety_backstop_bound") is not False
            or observed_audit.get("lossy_context_bound") is not False
            or observed_audit.get("recorded_scores_match_fresh") is not True
            or observed_audit.get("launch_config_sha256") != launch_row["config_sha256"]
        ):
            raise IntegrityError(f"invalid GRPO fast observation: {cell}")
        diag = _trajectory_diagnostics(Path(launch_row["results"]) / "trajectory.json")
        diagnostics.append(diag)
        pairs.append(
            {
                "cell": cell,
                "raw": old["raw"],
                "step20": old["step20"],
                "repair_sft_step24": old["fixed_v7"],
                "grpo_step24": {
                    "strict_binary": float(observation["strict_binary"]),
                    "preservation_strict": float(observation["preservation_strict"]),
                    "valid_transaction": bool(observation["valid_transaction"]),
                    "result": observation.get("result"),
                    "diagnostics": diag,
                },
            }
        )
    metric_summary: dict[str, Any] = {}
    for metric in ("strict_binary", "preservation_strict"):
        arms = {
            arm: [float(row[arm][metric]) for row in pairs]
            for arm in ("raw", "step20", "repair_sft_step24", "grpo_step24")
        }
        metric_summary[metric] = {
            **{f"{arm}_mean": _mean(values) for arm, values in arms.items()},
            "grpo_minus_repair_sft": _mean(
                [
                    new - old
                    for old, new in zip(
                        arms["repair_sft_step24"], arms["grpo_step24"], strict=True
                    )
                ]
            ),
            "grpo_minus_raw": _mean(
                [
                    new - old
                    for old, new in zip(arms["raw"], arms["grpo_step24"], strict=True)
                ]
            ),
            "grpo_minus_step20": _mean(
                [
                    new - old
                    for old, new in zip(
                        arms["step20"], arms["grpo_step24"], strict=True
                    )
                ]
            ),
            "grpo_vs_repair_sft_one_sided_sign_p_descriptive": _sign_pvalue(
                [
                    new - old
                    for old, new in zip(
                        arms["repair_sft_step24"], arms["grpo_step24"], strict=True
                    )
                ]
            ),
        }
    mechanism = {
        "hero_opened": sum(row["hero_opened"] for row in diagnostics),
        "hero_chosen": sum(row["hero_chosen"] for row in diagnostics),
        "addon_present_in_final_basket": sum(
            row["addon_present_in_final_basket"] for row in diagnostics
        ),
        "valid_transaction": sum(row["valid_transaction"] for row in diagnostics),
        "strict_successes": sum(
            row["grpo_step24"]["strict_binary"] == 1 for row in pairs
        ),
    }
    targets = prereg["analysis"]["directional_target"]
    target_met = {
        "strict_successes": mechanism["strict_successes"]
        >= targets["strict_successes_at_least"],
        "hero_opened": mechanism["hero_opened"] >= targets["hero_opened_at_least"],
        "addon_present": mechanism["addon_present_in_final_basket"]
        <= targets["addon_present_at_most"],
    }
    core = {
        "schema": REPORT_SCHEMA,
        "status": "complete",
        "scientific_label": "same_task_laptop_steered_development_replay",
        "claim_scope": "directional_development_signal_only",
        "candidate_selection_eligible": False,
        "no_significance_or_generalization_claim": True,
        "preregistration_sha256": prereg["preregistration_sha256"],
        "endpoint_receipt_sha256": endpoint["receipt_sha256"],
        "candidate_composite_sha256": endpoint["candidate"]["composite_sha256"],
        "launch_manifest_sha256": launch["launch_manifest_sha256"],
        "conversion_audit_sha256": audit["conversion_audit_sha256"],
        "n": 8,
        "condition": "combined",
        "metrics": metric_summary,
        "mechanistic": mechanism,
        "directional_targets": {
            "definition": targets,
            "met": target_met,
            "all_met": all(target_met.values()),
        },
        "pairs": pairs,
        "invariants": {
            "candidate_runs": 8,
            "clean_runs": 0,
            "infrastructure_invalid_runs": 0,
            "fresh_scores_recomputed": True,
            "identical_task_seed_causal_harness_and_limits": True,
            "office_chair_outcomes_or_training_data_used": False,
            "report_created_regardless_of_score": True,
        },
    }
    report = {**core, "report_sha256": sha256_bytes(canonical_bytes(core))}
    report_root.mkdir(parents=True)
    write_json_create_only(report_root / "report.json", report)
    strict = metric_summary["strict_binary"]
    lines = [
        "# Fixed-v7 GRPO fast laptop replay",
        "",
        "Eight steered laptop development cells; no clean or held-out claim.",
        "",
        "| Metric | GRPO step24 | Repair SFT step24 | Raw | Step20 |",
        "|---|---:|---:|---:|---:|",
        f"| Strict success | {strict['grpo_step24_mean']:.1%} | {strict['repair_sft_step24_mean']:.1%} | {strict['raw_mean']:.1%} | {strict['step20_mean']:.1%} |",
        f"| Hero opened | {mechanism['hero_opened']}/8 | 1/8 | — | — |",
        f"| Add-on retained | {mechanism['addon_present_in_final_basket']}/8 | 7/8 | — | — |",
        "",
        f"Directional target met: `{all(target_met.values())}`.",
    ]
    write_text_create_only(report_root / "report.md", "\n".join(lines) + "\n")
    print(
        json.dumps(
            {
                "status": "complete",
                "sha256": report["report_sha256"],
                "strict": strict["grpo_step24_mean"],
                "targets_met": all(target_met.values()),
            }
        )
    )


def audit_command(arguments: argparse.Namespace) -> None:
    if arguments.kind == "preregistration":
        value = audit_preregistration(arguments.path)
        print(json.dumps({"valid": True, "sha256": value["preregistration_sha256"]}))
    elif arguments.kind == "endpoint":
        value = _endpoint(arguments.path)
        print(json.dumps({"valid": True, "sha256": value["receipt_sha256"]}))
    elif arguments.kind == "serve-release":
        value = _serve_release(arguments.path)
        print(json.dumps({"valid": True, "sha256": value["release_sha256"]}))
    else:
        value = read_json(arguments.path.resolve())
        _self_hash(value, "report_sha256", "GRPO fast report")
        if value.get("schema") != REPORT_SCHEMA or value.get("n") != 8:
            raise IntegrityError("GRPO fast report changed")
        print(json.dumps({"valid": True, "sha256": value["report_sha256"]}))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    prereg = commands.add_parser("preregister")
    prereg.add_argument("--source-launch", type=Path, required=True)
    prereg.add_argument("--source-frozen", type=Path, required=True)
    prereg.add_argument("--baseline-report", type=Path, required=True)
    prereg.add_argument("--training-release", type=Path, required=True)
    prereg.add_argument("--evaluation-root", type=Path, required=True)
    prereg.add_argument("--endpoint-receipt", type=Path, required=True)
    prereg.add_argument("--output", type=Path, required=True)

    release = commands.add_parser("render-serve-release")
    release.add_argument("--training-receipt", type=Path, required=True)
    release.add_argument("--training-release", type=Path, required=True)
    release.add_argument("--serve-job-json", type=Path, required=True)
    release.add_argument("--serve-pods-json", type=Path, required=True)
    release.add_argument("--output", type=Path, required=True)

    attest = commands.add_parser("attest-endpoint")
    attest.add_argument("--training-receipt", type=Path, required=True)
    attest.add_argument("--serve-release", type=Path, required=True)
    attest.add_argument("--serve-job-json", type=Path, required=True)
    attest.add_argument("--serve-pods-json", type=Path, required=True)
    attest.add_argument("--models", type=Path, required=True)
    attest.add_argument("--canary", type=Path, required=True)
    attest.add_argument("--tunnel-status", type=Path, required=True)
    attest.add_argument("--output", type=Path, required=True)

    render_parser = commands.add_parser("render")
    for value in (render_parser,):
        value.add_argument("--preregistration", type=Path, required=True)
        value.add_argument("--endpoint-receipt", type=Path, required=True)
        value.add_argument("--source-launch", type=Path, required=True)
        value.add_argument("--source-frozen", type=Path, required=True)
        value.add_argument("--baseline-report", type=Path, required=True)
        value.add_argument("--evaluation-root", type=Path, required=True)
    render_parser.add_argument("--base-port", type=int, required=True)
    render_parser.add_argument("--python-executable", type=Path, required=True)

    finish = commands.add_parser("finalize")
    finish.add_argument("--preregistration", type=Path, required=True)
    finish.add_argument("--endpoint-receipt", type=Path, required=True)
    finish.add_argument("--source-launch", type=Path, required=True)
    finish.add_argument("--source-frozen", type=Path, required=True)
    finish.add_argument("--baseline-report", type=Path, required=True)
    finish.add_argument("--evaluation-root", type=Path, required=True)
    finish.add_argument("--executor-status", type=Path, required=True)
    finish.add_argument("--repository-root", type=Path, required=True)

    audit = commands.add_parser("audit")
    audit.add_argument(
        "kind", choices=("preregistration", "serve-release", "endpoint", "report")
    )
    audit.add_argument("path", type=Path)
    return root


def main() -> None:
    arguments = parser().parse_args()
    functions = {
        "preregister": preregister,
        "render-serve-release": render_serve_release,
        "attest-endpoint": attest_endpoint,
        "render": render,
        "finalize": finalize,
        "audit": audit_command,
    }
    try:
        functions[arguments.command](arguments)
    except IntegrityError as exc:
        raise SystemExit(f"GRPOFastEvalError: {exc}") from exc


if __name__ == "__main__":
    main()
