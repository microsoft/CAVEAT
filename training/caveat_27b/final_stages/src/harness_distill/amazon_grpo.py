"""Fail-closed PRIME-RL GRPO bridge for Amazon BrowserUse adaptation.

This module deliberately owns only the checkpoint/config bridge.  The actual
browser rollout and DB-backed reward live in the separately audited
``amazon_browser_rl_v1`` Verifiers plugin.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tomllib
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

PRIME_VERSION = "0.7.0"
PRIME_COMMIT = "d334ea52940b47f426293a7d146239e3fbf91caa"
VERIFIERS_COMMIT = "6c64ce6a3a01e8edde7c3c0e8e5315fb236e9faa"
TASKSET_SCHEMA = "harness-distill.amazon-browser-rl-taskset.v1"
BRIDGE_SCHEMA = "harness-distill.amazon-grpo-bridge.v1"
PLAN_SCHEMA = "harness-distill.amazon-grpo-plan.v1"
SOURCE_STEP = 23
FINAL_STEP = 24
GROUP_SIZE = 8
BATCH_SIZE = 32
MAX_BROWSER_WORKERS = 16
TRAIN_GROUP_LIMIT = 4
TRAIN_GROUP_RETRY_LIMIT = 1
LEARNING_RATE = 5.0e-7
SEQ_LEN = 32_768
ALLOWED_DEPLOYMENTS = {(4, 4), (2, 2)}
EXPECTED_V7_SOURCE_DCP = {
    "files": 9,
    "bytes": 57_547_695_910,
    "tree_sha256": "7e5a928e3003ece97ddb5e57b1d1296c2b948c2fb8919f8e90fe0f83d8e7cf7c",
}
_HEX64 = re.compile(r"[0-9a-f]{64}")


class AmazonGRPOError(RuntimeError):
    """Raised when the exact SFT-to-GRPO contract is violated."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise AmazonGRPOError(f"required regular JSON file is absent or unsafe: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AmazonGRPOError(f"JSON root is not an object: {path}")
    return value


def _tree_identity(path: Path) -> dict[str, Any]:
    if not path.is_dir() or path.is_symlink():
        raise AmazonGRPOError(f"required regular directory is absent or unsafe: {path}")
    # This encoding intentionally matches harness_posttrain's receipt identity:
    # a canonical JSON object keyed by relative path, with ``size`` and file
    # SHA-256 as values.  A superficially equivalent list encoding would have a
    # different tree digest and could not be compared to the SFT receipt.
    entries: dict[str, dict[str, Any]] = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise AmazonGRPOError(f"tree contains a symlink: {item}")
        if item.is_file():
            size = item.stat().st_size
            entries[item.relative_to(path).as_posix()] = {
                "size": size,
                "sha256": sha256_file(item),
            }
        elif not item.is_dir():
            raise AmazonGRPOError(f"tree contains a special entry: {item}")
    if not entries:
        raise AmazonGRPOError(f"tree is empty: {path}")
    return {
        "path": str(path.resolve()),
        "files": len(entries),
        "bytes": sum(int(row["size"]) for row in entries.values()),
        "tree_sha256": sha256_bytes(canonical_json(entries).encode()),
    }


def _write_new(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _write_json_new(path: Path, value: Mapping[str, Any]) -> Path:
    return _write_new(path, (canonical_json(dict(value)) + "\n").encode())


def _link_tree(source: Path, destination: Path) -> dict[str, Any]:
    if destination.exists() or destination.is_symlink():
        raise AmazonGRPOError(f"GRPO bridge destination already exists: {destination}")
    source_identity = _tree_identity(source)
    destination.mkdir(parents=True)
    linked_files = 0
    for item in sorted(source.rglob("*")):
        relative = item.relative_to(source)
        target = destination / relative
        if item.is_dir():
            target.mkdir(exist_ok=True)
        elif item.is_file() and not item.is_symlink():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(item, target)
            source_stat, target_stat = item.stat(), target.stat()
            if source_stat.st_dev != target_stat.st_dev or source_stat.st_ino != target_stat.st_ino:
                raise AmazonGRPOError(f"bridge file is not an exact hardlink: {relative}")
            linked_files += 1
        else:
            raise AmazonGRPOError(f"unsafe source entry: {item}")
    destination_identity = _tree_identity(destination)
    if any(
        destination_identity[key] != source_identity[key]
        for key in ("files", "bytes", "tree_sha256")
    ):
        raise AmazonGRPOError("hardlinked GRPO checkpoint identity drifted")
    return {
        "source": source_identity,
        "destination": destination_identity,
        "hardlinked_files": linked_files,
        "all_files_hardlinked": linked_files == source_identity["files"],
    }


def _require_dcp_inventory(
    path: Path,
    *,
    expected_identity: Mapping[str, Any] | None = None,
    trainer_world_size: int = 4,
    includes_dataloader: bool = True,
) -> dict[str, Any]:
    """Validate the exact PRIME trainer-DCP shape and optional frozen identity."""

    identity = _tree_identity(path)
    names = {
        item.relative_to(path).as_posix()
        for item in path.rglob("*")
        if item.is_file() and not item.is_symlink()
    }
    dataloaders = (
        {f"dataloader/rank_{rank}.pt" for rank in range(trainer_world_size)}
        if includes_dataloader
        else set()
    )
    distcp = {f"__{rank}_0.distcp" for rank in range(trainer_world_size)}
    expected_names = {".metadata", *dataloaders, *distcp}
    if names != expected_names:
        raise AmazonGRPOError("PRIME trainer DCP inventory is incomplete or unexpected")
    if expected_identity is not None and any(
        identity.get(key) != expected_identity.get(key) for key in ("files", "bytes", "tree_sha256")
    ):
        raise AmazonGRPOError("PRIME trainer DCP does not match the frozen source identity")
    return identity


def _adapter_targets(path: Path, *, expected_sha256: str | None = None) -> list[str]:
    config_path = path / "adapter_config.json"
    if expected_sha256 is not None and sha256_file(config_path) != expected_sha256:
        raise AmazonGRPOError("source adapter config identity drifted")
    config = _read_json(config_path)
    targets = config.get("target_modules")
    if (
        not isinstance(targets, list)
        or not targets
        or any(not isinstance(value, str) or not value for value in targets)
        or len(targets) != len(set(targets))
    ):
        raise AmazonGRPOError("source adapter target_modules are invalid")
    return list(targets)


def _validate_v7_receipt(receipt_path: Path) -> dict[str, Any]:
    receipt = _read_json(receipt_path)
    body_hash = receipt.get("receipt_body_sha256")
    body = {key: value for key, value in receipt.items() if key != "receipt_body_sha256"}
    if not isinstance(body_hash, str) or sha256_bytes(canonical_json(body).encode()) != body_hash:
        raise AmazonGRPOError("fixed-v7 receipt self-hash is invalid")
    policy = receipt.get("training_policy")
    candidate = receipt.get("candidate")
    if (
        receipt.get("schema") != "harness-posttrain.browser-action-fixed-v7-training-receipt.v1"
        or receipt.get("status") != "ok"
        or receipt.get("scientific_label") != "same_task_laptop_development_adaptation"
        or receipt.get("amazon_outcomes_consulted") is not True
        or receipt.get("office_chair_or_other_amazon_categories_consulted") is not False
        or receipt.get("optimizer_updates") != SOURCE_STEP
        or receipt.get("new_optimizer_updates") != 3
        or not isinstance(policy, Mapping)
        or policy.get("resume_step") != 20
        or policy.get("learning_rate") != 2.0e-6
        or not isinstance(candidate, Mapping)
        or candidate.get("name") != "step23"
        or candidate.get("update") != SOURCE_STEP
    ):
        raise AmazonGRPOError("fixed-v7 receipt policy or scientific label drifted")
    for field in ("artifact_source_git_sha", "execution_source_git_sha"):
        value = receipt.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
            raise AmazonGRPOError(f"fixed-v7 receipt has invalid {field}")
    return receipt


def _validate_taskset(path: Path) -> dict[str, Any]:
    manifest = _read_json(path)
    body_hash = manifest.get("manifest_body_sha256")
    body = {key: value for key, value in manifest.items() if key != "manifest_body_sha256"}
    if body_hash is not None and (
        not isinstance(body_hash, str) or sha256_bytes(canonical_json(body).encode()) != body_hash
    ):
        raise AmazonGRPOError("Amazon GRPO task manifest self-hash is invalid")
    tasks = manifest.get("tasks")
    variants = {"graded", "graded3", "graded4", "mixed"}
    if (
        manifest.get("schema") != TASKSET_SCHEMA
        or manifest.get("plugin_id") != "amazon-browser-rl-v1"
        or manifest.get("split") != "train"
        or manifest.get("scientific_label") != "same_task_laptop_r00_adaptation"
        or manifest.get("scenario") != "laptop"
        or manifest.get("conditions") != ["combined"]
        or manifest.get("repetitions") != [0]
        or manifest.get("laptop_r01_used") is not False
        or manifest.get("task_count") != 4
        or manifest.get("group_size_owner") != "prime_grpo_config"
        or not isinstance(tasks, list)
        or len(tasks) != 4
        or {task.get("variant") for task in tasks if isinstance(task, Mapping)} != variants
        or any(
            not isinstance(task, Mapping)
            or task.get("name") != f"laptop-{task.get('variant')}-combined-r00"
            or task.get("condition") != "combined"
            or task.get("repetition") != 0
            or type(task.get("block_seed")) is not int
            or any(
                _HEX64.fullmatch(str(task.get(field))) is None
                for field in (
                    "source_config_sha256",
                    "task_sha256",
                    "causal_config_sha256",
                    "mapping_sha256",
                )
            )
            for task in tasks
        )
    ):
        raise AmazonGRPOError("Amazon GRPO task manifest split or identity drifted")
    reward = manifest.get("reward_contract")
    source = manifest.get("source_protocol")
    if (
        not isinstance(reward, Mapping)
        or not isinstance(source, Mapping)
        or any(
            _HEX64.fullmatch(str(source.get(field))) is None
            for field in ("file_sha256", "body_sha256")
        )
        or reward.get("schema") != "agentarena.amazon-browser-native-grpo-reward.v1"
        or reward.get("formula") != "R = T + clip(P, -3.0, +3.0)"
        or reward.get("terminal_formula")
        != (
            "T = 10*strict_binary + preservation_strict - 3*transaction_violation "
            "- 3*decoy_purchase - 2*no_purchase"
        )
        or reward.get("terminal_hero_dominates") is not True
        or any(
            _HEX64.fullmatch(str(reward.get(field))) is None
            for field in (
                "source_file_sha256",
                "source_body_sha256",
                "contract_sha256",
            )
        )
    ):
        raise AmazonGRPOError("Amazon GRPO reward contract drifted")
    return manifest


def _quoted(value: str | Path) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _array(values: list[str]) -> str:
    return "[" + ", ".join(_quoted(value) for value in values) + "]"


def render_grpo_toml(
    *,
    parent_model: Path,
    output_dir: Path,
    taskset_manifest: Path,
    target_modules: list[str],
    lora_name: str,
    num_train_gpus: int = 4,
    num_infer_gpus: int = 4,
) -> bytes:
    if not target_modules or len(target_modules) != len(set(target_modules)):
        raise AmazonGRPOError("LoRA targets must be nonempty and unique")
    if (num_train_gpus, num_infer_gpus) not in ALLOWED_DEPLOYMENTS:
        raise AmazonGRPOError("GRPO deployment must be reviewed 4+4 or fallback 2+2")
    lines = [
        f"# PRIME-RL {PRIME_VERSION} ({PRIME_COMMIT}); Amazon BrowserUse GRPO",
        f"# Verifiers commit {VERIFIERS_COMMIT}",
        f"max_steps = {FINAL_STEP}",
        f"seq_len = {SEQ_LEN}",
        f"output_dir = {_quoted(output_dir)}",
        "clean_output_dir = false",
        "",
        "[env_vars]",
        'FLA_TILELANG = "0"',
        'WANDB_MODE = "disabled"',
        'AGENTARENA_LLM_CACHE = "0"',
        'AGENTARENA_NO_VISION = "1"',
        'ANONYMIZED_TELEMETRY = "false"',
        'BROWSER_USE_CLOUD_SYNC = "false"',
        f'PRIME_RL_TRAIN_GROUP_LIMIT = "{TRAIN_GROUP_LIMIT}"',
        f'PRIME_RL_TRAIN_GROUP_RETRY_LIMIT = "{TRAIN_GROUP_RETRY_LIMIT}"',
        "",
        "[deployment]",
        'type = "single_node"',
        f"num_train_gpus = {num_train_gpus}",
        f"num_infer_gpus = {num_infer_gpus}",
        f"gpus_per_node = {num_train_gpus + num_infer_gpus}",
        "",
        "[weight_broadcast]",
        'type = "filesystem"',
        "",
        "[model]",
        f"name = {_quoted(parent_model)}",
        "",
        "[ckpt]",
        "interval = 1",
        f"resume_step = {SOURCE_STEP}",
        "keep_last = 2",
        "",
        "[wandb]",
        'project = "harness-posttrain-amazon-adaptation"',
        'name = "fixed-v7-amazon-grpo-r00"',
        "",
        "[trainer]",
        "",
        "[trainer.model]",
        f"seq_len = {SEQ_LEN}",
        'impl = "hf"',
        'attn = "flash_attention_2"',
        'optimization_dtype = "bfloat16"',
        'reduce_dtype = "bfloat16"',
        "cp = 2",
        'cp_style = "ulysses"',
        "",
        "[trainer.model.ac]",
        'mode = "full"',
        "freq = 1",
        "",
        "[trainer.model.lora]",
        "rank = 64",
        "alpha = 128.0",
        "dropout = 0.0",
        f"target_modules = {_array(target_modules)}",
        "modules_to_save = []",
        "",
        "[trainer.optim]",
        'type = "adamw"',
        f"lr = {LEARNING_RATE}",
        "weight_decay = 0.01",
        "max_norm = 1.0",
        "",
        "[trainer.scheduler]",
        'type = "constant"',
        "",
        "[trainer.loss]",
        'type = "default"',
        "dppo_mask_low = 0.2",
        "dppo_mask_high = 0.2",
        "adv_tau = 1.0",
        "kl_tau = 0.001",
        "",
        "[trainer.ckpt]",
        "skip_optimizer = true",
        "skip_scheduler = true",
        "skip_dataloader = true",
        "skip_progress = false",
        "",
        "[trainer.ckpt.weights]",
        "save_sharded = true",
        'save_format = "safetensors"',
        "save_adapter_separately = true",
        "",
        "[orchestrator]",
        f"batch_size = {BATCH_SIZE}",
        f"group_size = {GROUP_SIZE}",
        f"max_inflight_rollouts = {MAX_BROWSER_WORKERS}",
        "max_off_policy_steps = 0",
        f"pool_size = {MAX_BROWSER_WORKERS}",
        "tasks_per_minute = 120",
        "",
        "[orchestrator.model.lora]",
        f"name = {_quoted(lora_name)}",
        "rank = 64",
        "alpha = 128.0",
        "",
        "[orchestrator.model.client]",
        'base_url = ["http://localhost:18500/v1"]',
        f"dp_rank_count = {num_infer_gpus}",
        "",
        "[orchestrator.renderer]",
        'name = "qwen3.5"',
        "enable_thinking = true",
        "",
        "[orchestrator.algo]",
        'type = "grpo"',
        "",
        "[orchestrator.train.sampling]",
        "temperature = 0.7",
        "top_p = 0.95",
        "max_completion_tokens = 4096",
        "",
        "[orchestrator.train.sampling.extra_body]",
        "top_k = 20",
        "min_p = 0.0",
        "presence_penalty = 0.0",
        "",
        "[[orchestrator.train.env]]",
        'name = "amazon-laptop-r00-adaptation"',
        (
            f'taskset = {{ id = "amazon-browser-rl-v1", path = {_quoted(taskset_manifest)}, '
            'split = "train" }'
        ),
        'harness = { id = "amazon-browser-rl-v1", runtime = { type = "subprocess" } }',
        f"group_size = {GROUP_SIZE}",
        "max_turns = 512",
        "max_output_tokens = 131072",
        "timeout = { setup = 300.0, rollout = 7200.0, finalize = 300.0, scoring = 600.0 }",
        f'pool = {{ type = "static", num_workers = {MAX_BROWSER_WORKERS} }}',
        "",
        "[orchestrator.ckpt]",
        "skip_progress = true",
        "wait_for_weights_timeout = 1800",
        "",
        "[inference]",
        "enable_lora = true",
        "api_server_count = 1",
        "gpu_memory_utilization = 0.85",
        "data_parallel_rpc_port = 15345",
        "enable_prefix_caching = false",
        f"lora_target_modules = {_array(target_modules)}",
        "",
        "[inference.model]",
        f"max_model_len = {SEQ_LEN}",
        'dtype = "bfloat16"',
        'tool_call_parser = "qwen3_coder"',
        'reasoning_parser = "qwen3"',
        "",
        "[inference.server]",
        "port = 18500",
        "",
        "[inference.parallel]",
        f"dp = {num_infer_gpus}",
        "tp = 1",
        "",
        "[inference.vllm_extra]",
        "language_model_only = true",
    ]
    payload = ("\n".join(lines).rstrip() + "\n").encode()
    parsed = tomllib.loads(payload.decode())
    validate_grpo_config(parsed, expected_taskset=taskset_manifest)
    return payload


def validate_grpo_config(config: Mapping[str, Any], *, expected_taskset: Path) -> None:
    trainer = config.get("trainer")
    orchestrator = config.get("orchestrator")
    envs = orchestrator.get("train", {}).get("env") if isinstance(orchestrator, Mapping) else None
    deployment = config.get("deployment")
    train_gpus = deployment.get("num_train_gpus") if isinstance(deployment, Mapping) else None
    infer_gpus = deployment.get("num_infer_gpus") if isinstance(deployment, Mapping) else None
    if (
        config.get("max_steps") != FINAL_STEP
        or config.get("seq_len") != SEQ_LEN
        or config.get("ckpt", {}).get("resume_step") != SOURCE_STEP
        or (train_gpus, infer_gpus) not in ALLOWED_DEPLOYMENTS
        or deployment.get("type") != "single_node"
        or deployment.get("gpus_per_node") != train_gpus + infer_gpus
        or not isinstance(trainer, Mapping)
        or trainer.get("optim", {}).get("lr") != LEARNING_RATE
        or trainer.get("scheduler") != {"type": "constant"}
        or trainer.get("ckpt", {}).get("skip_optimizer") is not True
        or trainer.get("ckpt", {}).get("skip_scheduler") is not True
        or trainer.get("ckpt", {}).get("skip_dataloader") is not True
        or trainer.get("ckpt", {}).get("skip_progress") is not False
        or not isinstance(orchestrator, Mapping)
        or orchestrator.get("batch_size") != BATCH_SIZE
        or orchestrator.get("group_size") != GROUP_SIZE
        or orchestrator.get("max_inflight_rollouts") != MAX_BROWSER_WORKERS
        or orchestrator.get("pool_size") != MAX_BROWSER_WORKERS
        or orchestrator.get("max_off_policy_steps") != 0
        or orchestrator.get("algo") != {"type": "grpo"}
        or orchestrator.get("model", {}).get("client", {}).get("base_url")
        != ["http://localhost:18500/v1"]
        or orchestrator.get("model", {}).get("client", {}).get("dp_rank_count") != infer_gpus
        or orchestrator.get("ckpt", {}).get("skip_progress") is not True
        or not isinstance(envs, list)
        or len(envs) != 1
        or envs[0].get("group_size") != GROUP_SIZE
        or envs[0].get("pool") != {"type": "static", "num_workers": MAX_BROWSER_WORKERS}
        or envs[0].get("taskset", {}).get("id") != "amazon-browser-rl-v1"
        or Path(envs[0].get("taskset", {}).get("path", "")).resolve() != expected_taskset.resolve()
        or envs[0].get("taskset", {}).get("split") != "train"
        or envs[0].get("harness", {}).get("id") != "amazon-browser-rl-v1"
        or config.get("inference", {}).get("enable_lora") is not True
        or config.get("inference", {}).get("api_server_count") != 1
        or config.get("inference", {}).get("parallel", {}).get("dp") != infer_gpus
        or config.get("env_vars", {}).get("PRIME_RL_TRAIN_GROUP_LIMIT") != str(TRAIN_GROUP_LIMIT)
        or config.get("env_vars", {}).get("PRIME_RL_TRAIN_GROUP_RETRY_LIMIT")
        != str(TRAIN_GROUP_RETRY_LIMIT)
    ):
        raise AmazonGRPOError("PRIME Amazon GRPO configuration drifted")


def _source_sft_dcp_path(candidate_path: Path) -> Path:
    """Map the exported SFT adapter back to PRIME SFT's root-level DCP."""
    return candidate_path.resolve().parents[2] / f"checkpoints/step_{SOURCE_STEP}"


def prepare_grpo_bridge(
    *,
    v7_receipt_path: str | Path,
    taskset_manifest_path: str | Path,
    output_dir: str | Path,
    target_modules: list[str],
    num_train_gpus: int = 4,
    num_infer_gpus: int = 4,
) -> dict[str, Any]:
    receipt_path = Path(v7_receipt_path).resolve()
    taskset_path = Path(taskset_manifest_path).resolve()
    output = Path(output_dir).resolve()
    if output.exists() or output.is_symlink():
        raise AmazonGRPOError("Amazon GRPO output directory must be new")
    receipt = _validate_v7_receipt(receipt_path)
    taskset = _validate_taskset(taskset_path)
    candidate_path = Path(str(receipt["candidate"]["path"])).resolve()
    candidate_identity = _tree_identity(candidate_path)
    expected_tree = receipt["candidate"].get("tree_sha256")
    if candidate_identity["tree_sha256"] != expected_tree:
        raise AmazonGRPOError("fixed-v7 candidate adapter identity drifted")
    source_targets = _adapter_targets(
        candidate_path,
        expected_sha256=str(receipt["candidate"].get("adapter_config_sha256") or ""),
    )
    if target_modules != source_targets:
        raise AmazonGRPOError("GRPO LoRA target modules differ from the source adapter")
    # The frozen SFT identity is rooted at ``checkpoints/step_N`` and thus
    # includes the ``trainer/`` path prefix.  PRIME's RL CheckpointManager
    # loads the *contents* of that child at its own ``.../trainer`` path.
    # Attest the root, validate the child layout, and link only the child so
    # the destination never becomes ``trainer/trainer``.
    source_step = _source_sft_dcp_path(candidate_path)
    # PRIME SFT trainer DCPs intentionally have no STABLE marker.  Bind the
    # exact terminally audited step-23 bytes instead of weakening that into a
    # shape-only check or inventing a marker in the source tree.
    source_root_identity = _tree_identity(source_step)
    if any(
        source_root_identity.get(key) != EXPECTED_V7_SOURCE_DCP[key]
        for key in ("files", "bytes", "tree_sha256")
    ):
        raise AmazonGRPOError("fixed-v7 source DCP root identity drifted")
    source_trainer = source_step / "trainer"
    source_identity = _require_dcp_inventory(source_trainer)
    bridge = _link_tree(source_trainer, output / f"checkpoints/step_{SOURCE_STEP}/trainer")
    if bridge["source"] != source_identity:
        raise AmazonGRPOError("fixed-v7 source DCP changed while bridging")
    _write_new(output / f"checkpoints/step_{SOURCE_STEP}/STABLE", b"")
    # PRIME requires the path to exist even when skip_progress=true.  Its bytes
    # are deliberately opaque and never loaded.
    progress_path = output / f"run_default/checkpoints/step_{SOURCE_STEP}/orchestrator/progress.pt"
    _write_new(progress_path, b"skip\n")
    lora_name = "fixed-v7-amazon-grpo-r00-r64-a128"
    config_path = output / "amazon_grpo.toml"
    _write_new(
        config_path,
        render_grpo_toml(
            parent_model=Path(str(receipt["parent_model"])).resolve(),
            output_dir=output,
            taskset_manifest=taskset_path,
            target_modules=target_modules,
            lora_name=lora_name,
            num_train_gpus=num_train_gpus,
            num_infer_gpus=num_infer_gpus,
        ),
    )
    body = {
        "schema": BRIDGE_SCHEMA,
        "status": "prepared",
        "scientific_label": "same_task_laptop_r00_grpo_adaptation",
        "prime_version": PRIME_VERSION,
        "prime_commit": PRIME_COMMIT,
        "verifiers_commit": VERIFIERS_COMMIT,
        "v7_receipt_path": str(receipt_path),
        "v7_receipt_file_sha256": sha256_file(receipt_path),
        "v7_receipt_body_sha256": receipt["receipt_body_sha256"],
        "v7_candidate_adapter": candidate_identity,
        "v7_candidate_adapter_config_sha256": receipt["candidate"]["adapter_config_sha256"],
        "source_adapter_target_modules": source_targets,
        "v7_source_dcp": source_root_identity,
        "v7_source_trainer_dcp": source_identity,
        "source_step": SOURCE_STEP,
        "final_step": FINAL_STEP,
        "checkpoint_bridge": bridge,
        "taskset_manifest_path": str(taskset_path),
        "taskset_manifest_file_sha256": sha256_file(taskset_path),
        "taskset_manifest_body_sha256": taskset.get("manifest_body_sha256"),
        "laptop_r00_used": True,
        "laptop_r01_used": False,
        "office_chair_used": False,
        "optimizer_updates": 1,
        "group_size": GROUP_SIZE,
        "batch_size": BATCH_SIZE,
        "rollouts": 32,
        "max_browser_workers": MAX_BROWSER_WORKERS,
        "train_group_limit": TRAIN_GROUP_LIMIT,
        "train_group_retry_limit": TRAIN_GROUP_RETRY_LIMIT,
        "learning_rate": LEARNING_RATE,
        "deployment": {
            "type": "single_node",
            "num_train_gpus": num_train_gpus,
            "num_infer_gpus": num_infer_gpus,
            "gpus_per_node": num_train_gpus + num_infer_gpus,
        },
        "config_path": str(config_path),
        "config_sha256": sha256_file(config_path),
        "parent_model": receipt["parent_model"],
        "lora_name": lora_name,
    }
    plan = dict(body)
    plan["plan_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    plan_path = output / "bridge_plan.json"
    _write_new(plan_path, (canonical_json(plan) + "\n").encode())
    return {**plan, "plan_path": str(plan_path), "plan_file_sha256": sha256_file(plan_path)}


def validate_grpo_bridge(plan_path: str | Path) -> dict[str, Any]:
    path = Path(plan_path).resolve()
    plan = _read_json(path)
    body_hash = plan.get("plan_body_sha256")
    body = {key: value for key, value in plan.items() if key != "plan_body_sha256"}
    if (
        plan.get("schema") != BRIDGE_SCHEMA
        or plan.get("status") != "prepared"
        or not isinstance(body_hash, str)
        or sha256_bytes(canonical_json(body).encode()) != body_hash
    ):
        raise AmazonGRPOError("Amazon GRPO bridge plan is incompatible")
    receipt_path = Path(str(plan["v7_receipt_path"]))
    taskset_path = Path(str(plan["taskset_manifest_path"]))
    config_path = Path(str(plan["config_path"]))
    if (
        sha256_file(receipt_path) != plan["v7_receipt_file_sha256"]
        or sha256_file(taskset_path) != plan["taskset_manifest_file_sha256"]
        or sha256_file(config_path) != plan["config_sha256"]
    ):
        raise AmazonGRPOError("Amazon GRPO frozen bridge input changed")
    _validate_v7_receipt(receipt_path)
    _validate_taskset(taskset_path)
    parsed_config = tomllib.loads(config_path.read_text())
    validate_grpo_config(parsed_config, expected_taskset=taskset_path)
    if parsed_config.get("deployment") != plan.get("deployment"):
        raise AmazonGRPOError("Amazon GRPO deployment changed after bridge preparation")
    source_root = plan.get("v7_source_dcp", {})
    actual_root = _tree_identity(Path(str(source_root.get("path", ""))))
    if any(
        actual_root.get(key) != EXPECTED_V7_SOURCE_DCP[key]
        or actual_root.get(key) != source_root.get(key)
        for key in ("files", "bytes", "tree_sha256")
    ):
        raise AmazonGRPOError("Amazon GRPO frozen source DCP root changed")
    checkpoint_bridge = plan.get("checkpoint_bridge", {})
    source_child = checkpoint_bridge.get("source", {})
    destination = checkpoint_bridge.get("destination", {})
    actual = _require_dcp_inventory(Path(str(destination.get("path", ""))))
    if any(actual.get(key) != destination.get(key) for key in ("files", "bytes", "tree_sha256")):
        raise AmazonGRPOError("Amazon GRPO bridged DCP changed")
    if any(actual.get(key) != source_child.get(key) for key in ("files", "bytes", "tree_sha256")):
        raise AmazonGRPOError("Amazon GRPO bridged trainer DCP differs from its frozen child")
    source_adapter = Path(str(plan["v7_candidate_adapter"]["path"]))
    targets = _adapter_targets(
        source_adapter,
        expected_sha256=str(plan["v7_candidate_adapter_config_sha256"]),
    )
    if targets != plan.get("source_adapter_target_modules"):
        raise AmazonGRPOError("Amazon GRPO adapter target module binding changed")
    return plan


def _read_trace_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise AmazonGRPOError(f"GRPO trace file is absent or unsafe: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AmazonGRPOError(
                    f"GRPO trace line {line_number} is invalid JSON: {path}"
                ) from exc
            if not isinstance(value, dict):
                raise AmazonGRPOError("GRPO trace row is not an object")
            rows.append(value)
    return rows


def _trace_graph_audit(
    row: Mapping[str, Any],
    *,
    allowed_length_nodes: Mapping[int, int] | None = None,
) -> dict[str, Any]:
    if row.get("stop_condition") in {
        "max_turns",
        "max_input_tokens",
        "max_output_tokens",
        "max_total_tokens",
        "context_length",
    }:
        raise AmazonGRPOError("GRPO trace terminated on a truncation bound")
    nodes = row.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise AmazonGRPOError("GRPO trace has no serialized graph nodes")
    parents: list[int | None] = []
    token_counts: list[int] = []
    any_trainable = False
    expected_length_nodes = dict(allowed_length_nodes or {})
    if any(
        type(index) is not int or index < 0 or type(masked_tokens) is not int or masked_tokens <= 0
        for index, masked_tokens in expected_length_nodes.items()
    ):
        raise AmazonGRPOError("GRPO allowed per-turn length map is invalid")
    observed_length_nodes: dict[int, int] = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            raise AmazonGRPOError("GRPO trace node is not an object")
        parent = node.get("parent")
        if parent is not None and (type(parent) is not int or parent < 0 or parent >= index):
            raise AmazonGRPOError("GRPO trace graph parent is invalid")
        token_ids, mask = node.get("token_ids"), node.get("mask")
        if (
            not isinstance(token_ids, list)
            or not isinstance(mask, list)
            or len(token_ids) != len(mask)
            or any(type(token) is not int or token < 0 for token in token_ids)
            or any(type(value) is not bool for value in mask)
        ):
            raise AmazonGRPOError("GRPO trace token/mask serialization is invalid")
        is_content, logprobs = node.get("is_content"), node.get("logprobs")
        if is_content not in (None, []) and (
            not isinstance(is_content, list)
            or len(is_content) != len(token_ids)
            or any(type(value) is not bool for value in is_content)
        ):
            raise AmazonGRPOError("GRPO trace content mask serialization is invalid")
        if node.get("sampled") is True and (
            not isinstance(logprobs, list)
            or len(logprobs) != sum(mask)
            or any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                for value in logprobs
            )
        ):
            raise AmazonGRPOError("GRPO sampled-node logprobs are invalid")
        if node.get("sampled") is not True and any(mask):
            raise AmazonGRPOError("GRPO nonsampled node has trainable tokens")
        if node.get("sampled") is True and node.get("finish_reason") == "length":
            masked_tokens = sum(mask)
            if expected_length_nodes.get(index) != masked_tokens:
                raise AmazonGRPOError("GRPO sampled generation terminated at length")
            observed_length_nodes[index] = masked_tokens
        parents.append(parent)
        token_counts.append(len(token_ids))
        any_trainable = any_trainable or any(mask)
    children = {parent for parent in parents if parent is not None}
    leaves = [index for index in range(len(nodes)) if index not in children]
    maximum = 0
    for leaf in leaves:
        length, cursor, seen = 0, leaf, set()
        while cursor is not None:
            if cursor in seen:
                raise AmazonGRPOError("GRPO trace graph contains a cycle")
            seen.add(cursor)
            length += token_counts[cursor]
            cursor = parents[cursor]
        maximum = max(maximum, length)
    if maximum > SEQ_LEN:
        raise AmazonGRPOError(
            f"GRPO trace branch would be silently prefix-truncated: {maximum}>{SEQ_LEN}"
        )
    if not any_trainable:
        raise AmazonGRPOError("GRPO effective trace has no trainable tokens")
    if observed_length_nodes != expected_length_nodes:
        raise AmazonGRPOError("GRPO allowed per-turn length map was not observed exactly")
    if observed_length_nodes:
        rewards = row.get("rewards")
        reward = rewards.get("amazon_browser_native_grpo") if isinstance(rewards, Mapping) else None
        if (
            row.get("stop_condition") != "agent_completed"
            or row.get("errors") != []
            or not isinstance(reward, (int, float))
            or isinstance(reward, bool)
            or not math.isfinite(reward)
        ):
            raise AmazonGRPOError(
                "GRPO allowed per-turn cap did not recover to a finite completed trajectory"
            )
    return {
        "nodes": len(nodes),
        "max_branch_tokens": maximum,
        "per_turn_length_nodes": observed_length_nodes,
    }


def _rollout_audit(
    *,
    all_path: Path,
    effective_path: Path,
    taskset_path: Path,
    allowed_length_nodes: Mapping[str, Mapping[int, int]] | None = None,
) -> dict[str, Any]:
    manifest = _validate_taskset(taskset_path)
    identity_fields = (
        "mapping_sha256",
        "source_config_sha256",
        "task_sha256",
        "causal_config_sha256",
        "block_seed",
        "variant",
    )
    expected = {
        str(task["name"]): {field: task[field] for field in identity_fields}
        for task in manifest["tasks"]
    }
    expected_by_idx = {index: str(task["name"]) for index, task in enumerate(manifest["tasks"])}
    all_rows = _read_trace_rows(all_path)
    effective_rows = _read_trace_rows(effective_path)
    if (
        len(effective_rows) != BATCH_SIZE
        or len(all_rows) < BATCH_SIZE
        or len(all_rows) > BATCH_SIZE * (TRAIN_GROUP_RETRY_LIMIT + 1)
        or len(all_rows) % GROUP_SIZE != 0
    ):
        raise AmazonGRPOError(
            "GRPO must contain exactly 32 trained rollouts and only bounded whole-group retries"
        )

    reward_contract_sha = manifest["reward_contract"]["contract_sha256"]
    expected_length_nodes = {
        trace_id: dict(nodes) for trace_id, nodes in (allowed_length_nodes or {}).items()
    }
    if any(not isinstance(trace_id, str) or not trace_id for trace_id in expected_length_nodes):
        raise AmazonGRPOError("GRPO allowed per-turn trace map is invalid")

    def audit_rows(rows: list[dict[str, Any]], label: str, *, allow_errors: bool) -> dict[str, Any]:
        ids: set[str] = set()
        task_counts: Counter[str] = Counter()
        group_tasks: dict[str, set[str]] = defaultdict(set)
        group_counts: Counter[str] = Counter()
        group_rewards: dict[str, list[float]] = defaultdict(list)
        group_error_counts: Counter[str] = Counter()
        trace_identity: dict[str, tuple[str, str]] = {}
        per_turn_length_nodes: dict[str, dict[str, int]] = {}
        max_branch = 0
        for row in rows:
            trace_id, group_id = row.get("id"), row.get("group_id")
            task = row.get("task")
            data = task.get("data") if isinstance(task, Mapping) else None
            if (
                not isinstance(trace_id, str)
                or not trace_id
                or trace_id in ids
                or not isinstance(group_id, str)
                or not group_id
                or row.get("policy_version") != SOURCE_STEP
                or row.get("kind") != "train"
                or row.get("env_name") != "amazon-laptop-r00-adaptation"
                or row.get("eval_step") is not None
                or row.get("is_completed") is not True
                or not isinstance(data, Mapping)
            ):
                raise AmazonGRPOError(f"{label} GRPO trace identity/status is invalid")
            errors = row.get("errors")
            has_error = isinstance(errors, list) and bool(errors)
            if errors not in ([], None) and not has_error:
                raise AmazonGRPOError(f"{label} GRPO trace error payload is invalid")
            if has_error and any(
                not isinstance(error, Mapping)
                or not isinstance(error.get("type"), str)
                or not error.get("type")
                or not isinstance(error.get("message"), str)
                for error in errors
            ):
                raise AmazonGRPOError(f"{label} GRPO trace error payload is invalid")
            task_idx = data.get("idx")
            name = data.get("name")
            resolved_name = expected_by_idx.get(task_idx) if type(task_idx) is int else None
            synthetic_data = {
                "idx": task_idx,
                "name": None,
                "description": None,
                "prompt": None,
                "system_prompt": None,
                "image": None,
                "workdir": None,
                "timeout": {
                    "setup": None,
                    "harness": None,
                    "finalize": None,
                    "scoring": None,
                },
                "resources": {"cpu": None, "memory": None, "gpu": None, "disk": None},
            }
            is_synthetic_error = (
                has_error
                and task.get("type") == "Task"
                and dict(data) == synthetic_data
                and row.get("stop_condition") == "error"
                and row.get("nodes") == []
                and row.get("rewards") == {}
            )
            if resolved_name is None or (
                not is_synthetic_error
                and (
                    task.get("type") != "AmazonBrowserRLTask"
                    or name != resolved_name
                    or name not in expected
                    or any(data.get(field) != expected[name][field] for field in identity_fields)
                    or data.get("split") != "train"
                    or data.get("scientific_label") != "same_task_laptop_r00_adaptation"
                    or data.get("scenario") != "laptop"
                    or data.get("condition") != "combined"
                    or data.get("repetition") != 0
                    or data.get("laptop_r01_used") is not False
                    or data.get("reward_contract_sha256") != reward_contract_sha
                )
            ):
                raise AmazonGRPOError(f"{label} GRPO trace task identity drifted")
            name = resolved_name
            if has_error:
                if not allow_errors:
                    raise AmazonGRPOError(f"{label} GRPO trace unexpectedly errored")
                group_error_counts[group_id] += 1
                ids.add(trace_id)
                trace_identity[trace_id] = (group_id, name)
                task_counts[name] += 1
                group_tasks[group_id].add(name)
                group_counts[group_id] += 1
                continue
            rewards = row.get("rewards")
            if not isinstance(rewards, Mapping) or set(rewards) != {"amazon_browser_native_grpo"}:
                raise AmazonGRPOError(f"{label} GRPO trace reward keys drifted")
            reward = rewards.get("amazon_browser_native_grpo")
            if (
                not isinstance(reward, (int, float))
                or isinstance(reward, bool)
                or not math.isfinite(reward)
            ):
                raise AmazonGRPOError(f"{label} GRPO trace reward is invalid")
            graph = _trace_graph_audit(
                row,
                allowed_length_nodes=expected_length_nodes.get(trace_id),
            )
            if graph["per_turn_length_nodes"]:
                per_turn_length_nodes[trace_id] = {
                    str(index): masked_tokens
                    for index, masked_tokens in sorted(graph["per_turn_length_nodes"].items())
                }
            max_branch = max(max_branch, int(graph["max_branch_tokens"]))
            ids.add(trace_id)
            trace_identity[trace_id] = (group_id, name)
            task_counts[name] += 1
            group_tasks[group_id].add(name)
            group_counts[group_id] += 1
            group_rewards[group_id].append(float(reward))
        if any(len(names) != 1 for names in group_tasks.values()):
            raise AmazonGRPOError(f"{label} GRPO group mixes task identities")
        if not allow_errors and (
            task_counts != Counter({name: GROUP_SIZE for name in expected})
            or len(group_counts) != TRAIN_GROUP_LIMIT
            or any(count != GROUP_SIZE for count in group_counts.values())
            or len({next(iter(names)) for names in group_tasks.values()}) != TRAIN_GROUP_LIMIT
        ):
            raise AmazonGRPOError(f"{label} GRPO cohort is not exact 4 tasks x 8 samples")
        normalized_expected_length_nodes = {
            trace_id: {str(index): masked_tokens for index, masked_tokens in sorted(nodes.items())}
            for trace_id, nodes in sorted(expected_length_nodes.items())
        }
        if per_turn_length_nodes != normalized_expected_length_nodes:
            raise AmazonGRPOError(
                f"{label} GRPO per-turn length recovery map was not observed exactly"
            )
        return {
            "ids": ids,
            "task_counts": dict(sorted(task_counts.items())),
            "group_counts": dict(sorted(group_counts.items())),
            "group_tasks": group_tasks,
            "group_rewards": group_rewards,
            "group_error_counts": dict(sorted(group_error_counts.items())),
            "trace_identity": trace_identity,
            "max_branch_tokens": max_branch,
            "per_turn_length_nodes": per_turn_length_nodes,
        }

    all_audit = audit_rows(all_rows, "all", allow_errors=True)
    effective_audit = audit_rows(effective_rows, "effective", allow_errors=False)
    if not effective_audit["ids"] <= all_audit["ids"] or any(
        effective_audit["trace_identity"][trace_id] != all_audit["trace_identity"][trace_id]
        for trace_id in effective_audit["ids"]
    ):
        raise AmazonGRPOError("effective GRPO traces are not an identity-preserving subset")
    effective_groups = set(effective_audit["group_counts"])
    all_groups = set(all_audit["group_counts"])
    retry_groups = all_groups - effective_groups
    group_task = {
        group_id: next(iter(names)) for group_id, names in all_audit["group_tasks"].items()
    }
    effective_task_groups = Counter(group_task[group_id] for group_id in effective_groups)
    retry_task_groups = Counter(group_task[group_id] for group_id in retry_groups)
    if (
        not effective_groups <= all_groups
        or any(count != GROUP_SIZE for count in all_audit["group_counts"].values())
        or len(retry_groups) > TRAIN_GROUP_LIMIT * TRAIN_GROUP_RETRY_LIMIT
        or effective_task_groups != Counter({name: 1 for name in expected})
        or any(count > TRAIN_GROUP_RETRY_LIMIT for count in retry_task_groups.values())
        or any(all_audit["group_error_counts"].get(group_id, 0) < 1 for group_id in retry_groups)
        or any(
            all_audit["group_error_counts"].get(group_id, 0) != 0 for group_id in effective_groups
        )
    ):
        raise AmazonGRPOError("GRPO retry groups are not bounded failed same-task groups")
    variable_groups = sum(
        len(set(rewards)) > 1
        for group_id, rewards in effective_audit["group_rewards"].items()
        if group_id in effective_groups
    )
    if variable_groups < 1:
        raise AmazonGRPOError("all GRPO groups have zero reward variance; update has no signal")
    return {
        "all_count": len(all_rows),
        "effective_count": len(effective_rows),
        "task_counts": all_audit["task_counts"],
        "group_counts": all_audit["group_counts"],
        "effective_group_counts": effective_audit["group_counts"],
        "retry_group_counts": {
            group_id: all_audit["group_counts"][group_id] for group_id in sorted(retry_groups)
        },
        "retry_task_counts": dict(sorted(retry_task_groups.items())),
        "failed_retry_group_count": len(retry_groups),
        "variable_reward_groups": variable_groups,
        "max_branch_tokens": max(
            all_audit["max_branch_tokens"], effective_audit["max_branch_tokens"]
        ),
        "per_turn_length_nodes": effective_audit["per_turn_length_nodes"],
        "per_turn_length_node_count": sum(
            len(nodes) for nodes in effective_audit["per_turn_length_nodes"].values()
        ),
        "trajectory_truncation_count": 0,
    }


def write_grpo_receipt(
    *,
    plan_path: str | Path,
    artifact_source_git_sha: str,
    execution_source_git_sha: str,
) -> dict[str, Any]:
    """Publish the immutable one-update GRPO receipt after PRIME terminates."""

    plan = validate_grpo_bridge(plan_path)
    for label, value in (
        ("artifact_source_git_sha", artifact_source_git_sha),
        ("execution_source_git_sha", execution_source_git_sha),
    ):
        if re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise AmazonGRPOError(f"invalid {label}")
    output = Path(str(plan["checkpoint_bridge"]["destination"]["path"])).parents[2]
    candidate_path = output / f"weights/step_{FINAL_STEP}/lora_adapters"
    candidate = _tree_identity(candidate_path)
    source_targets = list(plan["source_adapter_target_modules"])
    if _adapter_targets(candidate_path) != source_targets:
        raise AmazonGRPOError("final GRPO adapter target modules drifted")
    # PRIME single-run trainer checkpoints do not use a DCP STABLE marker.
    # This receipt runs only after the RL process exits zero, validates every
    # final DCP byte structurally, and separately requires the weight export's
    # official STABLE marker.
    final_dcp_path = output / f"checkpoints/step_{FINAL_STEP}/trainer"
    final_dcp = _require_dcp_inventory(
        final_dcp_path,
        trainer_world_size=int(plan["deployment"]["num_train_gpus"]),
        includes_dataloader=False,
    )
    weight_stable = output / f"weights/step_{FINAL_STEP}/STABLE"
    if not weight_stable.is_file() or weight_stable.is_symlink():
        raise AmazonGRPOError("final GRPO adapter export is not stable")
    rollout_root = output / "run_default" / f"rollouts/step_{FINAL_STEP}/train"
    all_traces = rollout_root / "all/traces.jsonl"
    effective_traces = rollout_root / "effective/traces.jsonl"
    rollout_audit = _rollout_audit(
        all_path=all_traces,
        effective_path=effective_traces,
        taskset_path=Path(str(plan["taskset_manifest_path"])),
    )
    body = {
        "schema": "harness-distill.amazon-grpo-training-receipt.v1",
        "status": "ok",
        "scientific_label": "same_task_laptop_r00_grpo_adaptation",
        "artifact_source_git_sha": artifact_source_git_sha,
        "execution_source_git_sha": execution_source_git_sha,
        "prime_version": PRIME_VERSION,
        "prime_commit": PRIME_COMMIT,
        "verifiers_commit": VERIFIERS_COMMIT,
        "bridge_plan_path": str(Path(plan_path).resolve()),
        "bridge_plan_file_sha256": sha256_file(Path(plan_path).resolve()),
        "bridge_plan_body_sha256": plan["plan_body_sha256"],
        "source_step": SOURCE_STEP,
        "final_step": FINAL_STEP,
        "optimizer_updates": 1,
        "group_size": GROUP_SIZE,
        "batch_size": BATCH_SIZE,
        "max_browser_workers": MAX_BROWSER_WORKERS,
        "train_group_limit": TRAIN_GROUP_LIMIT,
        "train_group_retry_limit": TRAIN_GROUP_RETRY_LIMIT,
        "learning_rate": LEARNING_RATE,
        "deployment": plan["deployment"],
        "laptop_r00_used": True,
        "laptop_r01_used": False,
        "office_chair_used": False,
        "final_dcp": final_dcp,
        "weight_stable_marker_sha256": sha256_file(weight_stable),
        "candidate": {"name": "step24-amazon-grpo-r00", "update": FINAL_STEP, **candidate},
        "rollouts": {
            "all_path": str(all_traces),
            "all_file_sha256": sha256_file(all_traces),
            "all_count": rollout_audit["all_count"],
            "effective_path": str(effective_traces),
            "effective_file_sha256": sha256_file(effective_traces),
            "effective_count": rollout_audit["effective_count"],
            "task_counts": rollout_audit["task_counts"],
            "group_counts": rollout_audit["group_counts"],
            "effective_group_counts": rollout_audit["effective_group_counts"],
            "retry_group_counts": rollout_audit["retry_group_counts"],
            "retry_task_counts": rollout_audit["retry_task_counts"],
            "failed_retry_group_count": rollout_audit["failed_retry_group_count"],
            "variable_reward_groups": rollout_audit["variable_reward_groups"],
            "max_branch_tokens": rollout_audit["max_branch_tokens"],
        },
        "selection_performed": False,
    }
    receipt = {**body, "receipt_body_sha256": sha256_bytes(canonical_json(body).encode())}
    receipt_path = output / "amazon_grpo_training_receipt.json"
    _write_json_new(receipt_path, receipt)
    return {
        **receipt,
        "receipt_path": str(receipt_path),
        "receipt_file_sha256": sha256_file(receipt_path),
    }
