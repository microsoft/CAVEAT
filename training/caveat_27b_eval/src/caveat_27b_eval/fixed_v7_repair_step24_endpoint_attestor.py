#!/usr/bin/env python3
"""Outcome-blind attestation for the real-r00 repair-SFT step24 endpoint."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping


ENDPOINT_SCHEMA = "caveat-27b-eval.fixed-v7-repair-step24-endpoint.v1"
RECEIPT_SCHEMA = "harness-distill.caveat_shop-r00-repair-sft-training-receipt.v1"
FANOUT_SCHEMA = "harness-distill.caveat_shop-r00-repair-fanout-receipt.v4"
TRAINING_RELEASE_SCHEMA = (
    "caveat-27b.browser-action-next-iteration.one-update-sft-release.v1"
)
SERVE_RELEASE_SCHEMA = (
    "caveat-27b.browser-action-fixed-v7-repair-step24-serve-release.v1"
)
IMAGE_DIGEST = "sha256:28f38e74e17779e9c85985d3d3f970c1f6aa4427407f1843c11f2af87a324dd0"
PARENT_TREE = "5939382fbc6db775972dc9cebf3e5be20654149a0412b4f00072bad12a2215ca"
TOKENIZER_JSON = "06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523"
CHAT_TEMPLATE = "a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715"
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
REPAIR_EXECUTOR_GIT_SHA = "6cf2d5a0154ff644f6766b03648663bdbed10ec8"
REPLAY_EXECUTOR_GIT_SHA = "7ceed1104fea9807a8acd3d3cc0503dfb208c498"
REPLAY_VALIDATOR_GIT_SHA = "86e7f2cab46dedcb2b44b7fe0931d1ed60fc8dc1"
FANOUT_FILE_SHA256 = "a560ee78f2c3f433501b3ce9bd2902894329f41c7732d7192c0bb471e9657383"
FANOUT_BODY_SHA256 = "368e67b55cd2fe8eb5379f4af1366125fcc6204d0b9107644a23c8477bc1f0f1"
TRAINING_RECEIPT_FILE_SHA256 = (
    "85d5c779c8fb1ab4a1a87d4e4f4ab9bd7809675622bfe845a0632b2ab6ae4bf1"
)
TRAINING_RECEIPT_BODY_SHA256 = (
    "aa60d14b36e2bd5ae3cdab34cc74665ed883a03e59384f1174a17c624235fb1e"
)
EXPECTED_EXCLUSIONS = {
    "caveat_shop-r00-repair-repair-v2-7ceed-r1-graded-6": "truncated_proxy_completion",
    "caveat_shop-r00-repair-repair-v2-7ceed-r1-mixed-1": "no_exact_wire_critical_target",
    "caveat_shop-r00-repair-repair-v2-7ceed-r1-mixed-2": "terminal_policy_violation",
}
EXPECTED_ROLE_COUNTS = {
    "graded": {
        "cart_cleanup": 3,
        "cart_navigation": 6,
        "checkout": 5,
        "place_order": 4,
    },
    "mixed": {
        "cart_cleanup": 3,
        "cart_navigation": 5,
        "checkout": 3,
        "place_order": 5,
    },
}


class IntegrityError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def safe_file(path: Path) -> None:
    if (
        path.is_symlink()
        or not path.is_file()
        or not stat.S_ISREG(path.lstat().st_mode)
    ):
        raise IntegrityError(f"not a safe regular file: {path}")


def file_sha(path: Path) -> str:
    safe_file(path)
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            result.update(block)
    return result.hexdigest()


def read(path: Path) -> dict[str, Any]:
    safe_file(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IntegrityError(f"invalid JSON: {path}") from error
    if not isinstance(value, dict):
        raise IntegrityError(f"JSON is not an object: {path}")
    return value


def self_hash(value: Mapping[str, Any], field: str, label: str) -> None:
    body = {key: item for key, item in value.items() if key != field}
    if value.get(field) != digest(body):
        raise IntegrityError(f"{label} has an invalid {field}")


def sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise IntegrityError(f"{label} is not a SHA-256")
    return value


def descriptor(path: Path, schema: str, field: str, label: str) -> dict[str, Any]:
    value = read(path)
    if value.get("schema") != schema:
        raise IntegrityError(f"{label} schema changed")
    self_hash(value, field, label)
    return value


def process_identity(pid: Any) -> tuple[list[str], str]:
    if type(pid) is not int or pid <= 1:
        raise IntegrityError("process PID is invalid")
    root = Path("/proc") / str(pid)
    try:
        argv = [
            item.decode("utf-8", "strict")
            for item in (root / "cmdline").read_bytes().split(b"\0")
            if item
        ]
        fields = (root / "stat").read_text(encoding="utf-8").rsplit(") ", 1)[1].split()
    except (OSError, UnicodeDecodeError, IndexError) as error:
        raise IntegrityError(f"cannot inspect process {pid}") from error
    return argv, fields[19]


def job_runtime(
    job_path: Path,
    pods_path: Path,
    expected: Mapping[str, Any],
    *,
    terminal: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    job = read(job_path)
    pod_list = read(pods_path)
    if (
        job.get("metadata", {}).get("name") != expected.get("job_name")
        or job.get("metadata", {}).get("uid") != expected.get("job_uid")
        or digest(job.get("spec", {})) != expected.get("job_spec_sha256")
        or digest(job.get("metadata", {}).get("labels", {}))
        != expected.get("job_labels_sha256")
    ):
        raise IntegrityError("job identity changed")
    items = pod_list.get("items")
    if not isinstance(items, list) or len(items) != 1:
        raise IntegrityError("job must have exactly one pod")
    pod = items[0]
    statuses = pod.get("status", {}).get("containerStatuses", [])
    if len(statuses) != 1:
        raise IntegrityError("container identity is absent")
    container = statuses[0]
    common = (
        pod.get("metadata", {}).get("name") == expected.get("pod_name")
        and pod.get("metadata", {}).get("uid") == expected.get("pod_uid")
        and pod.get("spec", {}).get("nodeName") == expected.get("node")
        and container.get("restartCount") == 0
        and str(container.get("imageID", "")).endswith("@" + IMAGE_DIGEST)
    )
    if terminal:
        terminated = container.get("state", {}).get("terminated", {})
        valid = (
            common
            and job.get("status", {}).get("state", {}).get("phase")
            in {"Completed", "Succeeded"}
            and pod.get("status", {}).get("phase") == "Succeeded"
            and terminated.get("exitCode") == 0
        )
    else:
        valid = (
            common
            and job.get("status", {}).get("state", {}).get("phase") == "Running"
            and pod.get("status", {}).get("phase") == "Running"
            and container.get("ready") is True
            and container.get("started") is True
        )
    if not valid:
        raise IntegrityError("job runtime state changed")
    return job, pod, container


def exact_lora_composite(candidate: Mapping[str, Any]) -> str:
    return digest(
        {
            "schema": "caveat-27b.exact-lora-composite.v1",
            "execution": "peft_unmerged_exact_lora",
            "parent_tree_sha256": candidate["parent_tree_sha256"],
            "adapter_tree_sha256": candidate["adapter_tree_sha256"],
            "adapter_config_sha256": candidate["adapter_config_sha256"],
            "tokenizer_json_sha256": candidate["tokenizer_json_sha256"],
            "chat_template_sha256": candidate["chat_template_sha256"],
            "dtype": candidate["dtype"],
        }
    )


def valid_repair_fanout(value: Mapping[str, Any]) -> bool:
    """Fail closed on anything except the frozen pooled v4 cohort."""

    inventory = value.get("inventory")
    excluded = value.get("excluded_executions")
    if not isinstance(inventory, list) or not isinstance(excluded, list):
        return False
    rows = [*inventory, *excluded]
    sequences = [str(row.get("sequence_id") or "") for row in rows]
    nonces = [str(row.get("environment_nonce") or "") for row in rows]
    ports = [row.get("proxy_port") for row in rows]
    return (
        value.get("status") == "complete"
        and value.get("scientific_label")
        == "same_task_laptop_r00_pooled_exact_target_repair_fanout"
        and value.get("run_id") == "repair-v2-7ceed-r1"
        and value.get("executor_git_sha") == REPLAY_EXECUTOR_GIT_SHA
        and value.get("validator_git_sha") == REPLAY_VALIDATOR_GIT_SHA
        and value.get("max_concurrency") == 16
        and value.get("retry_count") == 0
        and value.get("topup_count") == 0
        and value.get("execution_count") == 16
        and value.get("valid_replay_count") == len(inventory) == 13
        and value.get("excluded_execution_count") == len(excluded) == 3
        and value.get("execution_variant_counts") == {"graded": 8, "mixed": 8}
        and value.get("valid_variant_counts") == {"graded": 7, "mixed": 6}
        and value.get("excluded_sequence_reasons") == EXPECTED_EXCLUSIONS
        and {
            str(row.get("sequence_id") or ""): str(row.get("reason") or "")
            for row in excluded
        }
        == EXPECTED_EXCLUSIONS
        and value.get("critical_role_variant_counts") == EXPECTED_ROLE_COUNTS
        and value.get("critical_role_variant_unique_target_counts")
        == EXPECTED_ROLE_COUNTS
        and value.get("targets_per_variant_role_required") == 2
        and value.get("base_port") == 34500
        and value.get("ports") == list(range(34500, 34516))
        and len(sequences) == len(set(sequences)) == 16
        and len(nonces) == len(set(nonces)) == 16
        and len(ports) == len(set(ports)) == 16
        and set(ports) == set(range(34500, 34516))
        and value.get("laptop_r00_used") is True
        and value.get("laptop_r01_used") is False
        and value.get("office_chair_used") is False
    )


def artifact_record(path: Path, body: str) -> dict[str, str]:
    return {
        "path": str(path.resolve()),
        "file_sha256": file_sha(path),
        "body_sha256": body,
    }


def write_create_only(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
            raise IntegrityError(f"refusing endpoint receipt collision: {path}")
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def attest(args: argparse.Namespace) -> None:
    training_release = descriptor(
        args.training_release,
        TRAINING_RELEASE_SCHEMA,
        "release_sha256",
        "training release",
    )
    receipt = descriptor(
        args.training_receipt, RECEIPT_SCHEMA, "receipt_body_sha256", "training receipt"
    )
    fanout = descriptor(
        args.fanout_receipt, FANOUT_SCHEMA, "receipt_body_sha256", "fanout receipt"
    )
    serve_release = descriptor(
        args.serve_release, SERVE_RELEASE_SCHEMA, "release_sha256", "serve release"
    )
    candidate_receipt = receipt.get("candidate") or {}
    candidate_release = serve_release.get("candidate") or {}
    source_sha = receipt.get("executor_git_sha")
    alias = f"caveat-27b-fixed-v7-repair-{str(source_sha)[:12]}-exact-lora"
    candidate = {
        "name": "step24-caveat_shop-r00-repair-sft",
        "update": 24,
        "parent_tree_sha256": candidate_release.get("parent_tree_sha256"),
        "adapter_tree_sha256": candidate_release.get("adapter_tree_sha256"),
        "adapter_config_sha256": candidate_release.get("adapter_config_sha256"),
        "tokenizer_json_sha256": candidate_release.get("tokenizer_json_sha256"),
        "chat_template_sha256": candidate_release.get("chat_template_sha256"),
        "dtype": candidate_release.get("dtype"),
        "composite_sha256": candidate_release.get("composite_sha256"),
        "served_model_name": candidate_release.get("served_model_name"),
    }
    for field in (
        "parent_tree_sha256",
        "adapter_tree_sha256",
        "adapter_config_sha256",
        "tokenizer_json_sha256",
        "chat_template_sha256",
        "composite_sha256",
    ):
        sha(candidate[field], f"candidate.{field}")
    if (
        receipt.get("status") != "ok"
        or source_sha != REPAIR_EXECUTOR_GIT_SHA
        or receipt.get("replay_executor_git_sha") != REPLAY_EXECUTOR_GIT_SHA
        or receipt.get("replay_validator_git_sha") != REPLAY_VALIDATOR_GIT_SHA
        or receipt.get("fanout_receipt_sha256") != FANOUT_FILE_SHA256
        or receipt.get("fanout_receipt_body_sha256") != FANOUT_BODY_SHA256
        or receipt.get("dataset_manifest_sha256")
        != "2d2c9b1eac32fea137cec1852b854a8d34a4edcfa8982ac07c5a4d58a7987230"
        or receipt.get("dataset_sha256")
        != "4d614d80ae4db704b68118da00942b1584805e3e59a4a2cf0e1cee79117adc1c"
        or receipt.get("plan_file_sha256")
        != "9dae94e2081d37e74ed89be7a06c8c2a054d09ff37cb139b12d232a0c8aae6e2"
        or receipt.get("plan_body_sha256")
        != "fd5ffde22c4a6b587022f032cff629351ff88c0a930999d040a1802e964712fe"
        or receipt.get("scientific_label")
        != "same_task_laptop_r00_real_state_cart_repair_sft"
        or receipt.get("source_step") != 23
        or receipt.get("final_step") != 24
        or receipt.get("optimizer_updates") != 1
        or receipt.get("learning_rate") != 5e-7
        or receipt.get("assistant_tokens_only") is not True
        or receipt.get("laptop_r00_used") is not True
        or receipt.get("laptop_r01_used") is not False
        or receipt.get("office_chair_used") is not False
        or candidate_receipt.get("name") != candidate["name"]
        or candidate_receipt.get("update") != 24
        or candidate_receipt.get("tree_sha256")
        != "49e66d189603142232d0e4f1b3549d32b783ebed5fa4d3efd0e04af3197a5508"
        or receipt.get("final_dcp", {}).get("tree_sha256")
        != "018e4589edb0ee7dd95eb94511c6411458ba3d0c274fa8284fa17eaae10c2b35"
        or training_release.get("status") != "released"
        or training_release.get("purpose")
        != "one_update_sft_from_r00_correction_replay"
        or training_release.get("laptop_r01_outcomes_read") is not False
        or training_release.get("office_chair_outcomes_read") is not False
        or training_release.get("source", {}).get("git_sha") != source_sha
        or not valid_repair_fanout(fanout)
        or serve_release.get("status") != "released"
        or serve_release.get("outcome_blind") is not True
        or serve_release.get("executor_git_sha") != source_sha
        or serve_release.get("scientific_label")
        != "same_task_laptop_r00_real_state_cart_repair_sft"
        or serve_release.get("evaluation_label") != "same_task_adaptation_replay"
        or serve_release.get("training_receipt", {}).get("file_sha256")
        != file_sha(args.training_receipt)
        or serve_release.get("training_receipt", {}).get("body_sha256")
        != receipt.get("receipt_body_sha256")
        or serve_release.get("training_release", {}).get("file_sha256")
        != file_sha(args.training_release)
        or serve_release.get("training_release", {}).get("body_sha256")
        != training_release.get("release_sha256")
        or serve_release.get("fanout_receipt", {}).get("file_sha256")
        != file_sha(args.fanout_receipt)
        or serve_release.get("fanout_receipt", {}).get("body_sha256")
        != fanout.get("receipt_body_sha256")
        or serve_release.get("fanout_receipt", {}).get("file_sha256")
        != FANOUT_FILE_SHA256
        or serve_release.get("fanout_receipt", {}).get("body_sha256")
        != FANOUT_BODY_SHA256
        or serve_release.get("fanout_receipt", {}).get("executor_git_sha")
        != REPLAY_EXECUTOR_GIT_SHA
        or serve_release.get("fanout_receipt", {}).get("validator_git_sha")
        != REPLAY_VALIDATOR_GIT_SHA
        or serve_release.get("fanout_receipt", {}).get("execution_count") != 16
        or serve_release.get("fanout_receipt", {}).get("valid_replay_count") != 13
        or serve_release.get("fanout_receipt", {}).get("excluded_execution_count") != 3
        or file_sha(args.training_receipt) != TRAINING_RECEIPT_FILE_SHA256
        or receipt.get("receipt_body_sha256") != TRAINING_RECEIPT_BODY_SHA256
        or file_sha(args.fanout_receipt) != FANOUT_FILE_SHA256
        or fanout.get("receipt_body_sha256") != FANOUT_BODY_SHA256
        or serve_release.get("training_plan", {}).get("file_sha256")
        != receipt.get("plan_file_sha256")
        or serve_release.get("training_plan", {}).get("body_sha256")
        != receipt.get("plan_body_sha256")
        or serve_release.get("dataset", {}).get("manifest_file_sha256")
        != receipt.get("dataset_manifest_sha256")
        or serve_release.get("dataset", {}).get("train_sha256")
        != receipt.get("dataset_sha256")
        or serve_release.get("dataset", {}).get("row_count") != 32
        or serve_release.get("dataset", {}).get("real_repair_rows") != 16
        or serve_release.get("dataset", {}).get("retention_rows") != 16
        or serve_release.get("final_dcp") != receipt.get("final_dcp")
        or candidate["parent_tree_sha256"] != PARENT_TREE
        or candidate["adapter_tree_sha256"] != candidate_receipt.get("tree_sha256")
        or candidate["adapter_config_sha256"]
        != candidate_receipt.get("adapter_config_sha256")
        or candidate["tokenizer_json_sha256"] != TOKENIZER_JSON
        or candidate["chat_template_sha256"] != CHAT_TEMPLATE
        or candidate["dtype"] != "bfloat16"
        or candidate["served_model_name"] != alias
        or candidate["composite_sha256"] != exact_lora_composite(candidate)
    ):
        raise IntegrityError("repair step24 scientific artifact semantics differ")

    training_expected = serve_release.get("training_runtime") or {}
    serve_expected = serve_release.get("serve_runtime") or {}
    training_job, training_pod, training_container = job_runtime(
        args.training_job, args.training_pods, training_expected, terminal=True
    )
    serve_job, serve_pod, serve_container = job_runtime(
        args.serve_job, args.serve_pods, serve_expected, terminal=False
    )

    expected_tunnel = json.loads(args.expected_tunnel_argv.read_text(encoding="utf-8"))
    if not isinstance(expected_tunnel, list) or not all(
        isinstance(item, str) for item in expected_tunnel
    ):
        raise IntegrityError("expected tunnel argv is invalid")
    tunnel = read(args.tunnel_status)
    self_hash(tunnel, "status_sha256", "tunnel status")
    child_argv, child_start = process_identity(tunnel.get("child_pid"))
    _, supervisor_start = process_identity(tunnel.get("supervisor_pid"))
    if (
        tunnel.get("state") != "running"
        or child_argv != expected_tunnel
        or child_start != tunnel.get("child_process_start_ticks")
        or supervisor_start != tunnel.get("supervisor_process_start_ticks")
    ):
        raise IntegrityError("live tunnel identity changed")

    models, canary = read(args.models), read(args.canary)
    model_ids = {
        row.get("id") for row in models.get("data", []) if isinstance(row, dict)
    }
    choices = canary.get("choices")
    choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else {}
    content = (
        choice.get("message", {}).get("content") if isinstance(choice, dict) else None
    )
    if (
        alias not in model_ids
        or canary.get("model") != alias
        or choice.get("finish_reason") != "stop"
        or not isinstance(content, str)
        or not content.strip()
    ):
        raise IntegrityError("repair step24 API canary failed")

    def runtime_record(
        expected: Mapping[str, Any],
        job: Mapping[str, Any],
        pod: Mapping[str, Any],
        container: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "job": expected["job_name"],
            "job_uid": expected["job_uid"],
            "job_spec_sha256": digest(job["spec"]),
            "job_labels_sha256": digest(job.get("metadata", {}).get("labels", {})),
            "pod": pod["metadata"]["name"],
            "pod_uid": pod["metadata"]["uid"],
            "node": pod["spec"]["nodeName"],
            "image_id": container["imageID"],
        }

    training_runtime = runtime_record(
        training_expected, training_job, training_pod, training_container
    )
    serve_runtime = runtime_record(
        serve_expected, serve_job, serve_pod, serve_container
    )
    serve_runtime.update(
        {
            "container_command": serve_job["spec"]["tasks"][0]["template"]["spec"][
                "containers"
            ][0]["command"],
            "container_args": serve_job["spec"]["tasks"][0]["template"]["spec"][
                "containers"
            ][0]["args"],
            "tunnel_argv": expected_tunnel,
            "tunnel_pid": tunnel["child_pid"],
            "tunnel_process_start_ticks": child_start,
            "tunnel_supervisor_pid": tunnel["supervisor_pid"],
            "tunnel_supervisor_process_start_ticks": supervisor_start,
            "tunnel_status_sha256": tunnel["status_sha256"],
        }
    )
    core = {
        "schema": ENDPOINT_SCHEMA,
        "status": "ok",
        "outcome_blind": True,
        "artifacts": {
            "training_release_descriptor": artifact_record(
                args.training_release, training_release["release_sha256"]
            ),
            "training_receipt": artifact_record(
                args.training_receipt, receipt["receipt_body_sha256"]
            ),
            "fanout_receipt": artifact_record(
                args.fanout_receipt, fanout["receipt_body_sha256"]
            ),
            "serve_release_descriptor": artifact_record(
                args.serve_release, serve_release["release_sha256"]
            ),
        },
        "candidate": candidate,
        "model_spec": {
            "name": alias,
            "provider": "openai",
            "base_url": args.base_url,
            "api_key": "env:CAVEAT_27B_API_KEY",
            "deployment": alias,
            "vision": False,
            "extra": {"frequency_penalty": None},
        },
        "labels": {
            "laptop": "same_task_adaptation_replay",
            "office_chair": "untouched_category_generalization_confirmation",
        },
        "training": training_runtime,
        "runtime": serve_runtime,
        "api_evidence": {
            "models_file_sha256": file_sha(args.models),
            "canary_file_sha256": file_sha(args.canary),
            "served_models": sorted(model_ids),
            "finish_reason": "stop",
        },
    }
    write_create_only(args.output, {**core, "receipt_sha256": digest(core)})


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    for name in (
        "training-release",
        "training-receipt",
        "fanout-receipt",
        "serve-release",
        "training-job",
        "training-pods",
        "serve-job",
        "serve-pods",
        "expected-tunnel-argv",
        "tunnel-status",
        "models",
        "canary",
        "output",
    ):
        value.add_argument(f"--{name}", type=Path, required=True)
    value.add_argument("--base-url", required=True)
    return value


def main() -> None:
    attest(parser().parse_args())


if __name__ == "__main__":
    main()
