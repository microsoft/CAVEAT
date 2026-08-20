"""Generate the optional, strictly bounded reverse-KL OPD smoke."""

from __future__ import annotations

import json
import re
import tomllib
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    publish_bytes,
    publish_json,
    publish_jsonl,
    read_json,
    read_jsonl,
    sha256_file,
)
from .config import Campaign
from .sft_data import _scan_visible
from .splits import load_split_manifest, require_training_membership

OPD_REPLAY_SCHEMA = "caveat-27b.opd-replay-source.v1"
OPD_PLAN_SCHEMA = "caveat-27b.optional-opd-plan.v1"
FORBIDDEN_PRIVILEGED_KEYS = {
    "hero_asin",
    "reward",
    "optimal_selection",
    "evaluator",
    "evaluator_truth",
    "private_catalog",
    "storefront_ops_token",
    "outcome",
}


def _scan_privileged(value: Any, path: str = "privileged_context") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).strip().lower() in FORBIDDEN_PRIVILEGED_KEYS:
                raise ArtifactError(f"forbidden field in public-only OPD context: {path}.{key}")
            _scan_privileged(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_privileged(item, f"{path}[{index}]")


def _quoted(value: str | Path) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _array(values: list[str]) -> str:
    return "[\n" + "".join(f"  {_quoted(value)},\n" for value in values) + "]"


def generate_optional_opd(
    campaign: Campaign,
    *,
    enabled: bool,
    split_manifest_path: str | Path,
    smoke_report: str | Path,
    student_model: str | Path,
    teacher_model: str | Path,
    replay_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    if not enabled:
        raise ArtifactError("optional OPD is disabled; pass the explicit opt-in flag")
    settings = campaign.campaign["optional_opd_smoke"]
    _split_manifest, membership = load_split_manifest(campaign, split_manifest_path)
    report = read_json(smoke_report)
    if (
        not isinstance(report, dict)
        or report.get("status") != "ok"
        or report.get("snapshot", {}).get("model_id") != campaign.model["model_id"]
    ):
        raise ArtifactError("OPD requires the successful pinned-model smoke report")
    raw_targets = report.get("target_selection", {}).get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ArtifactError("OPD smoke report has no audited LoRA targets")
    target_patterns = [f"^{re.escape(str(item))}$" for item in raw_targets]
    target_leaves = sorted({str(item).rsplit(".", 1)[-1] for item in raw_targets})
    student = Path(student_model).resolve()
    teacher = Path(teacher_model).resolve()
    if not student.is_dir() or not teacher.is_dir():
        raise ArtifactError("OPD student and frozen teacher model directories must exist")
    # The revised treatment intentionally uses the selected SFT checkpoint on
    # both sides; only the teacher receives the public-derived phase context.
    if student != teacher:
        raise ArtifactError("bounded OPD requires one frozen selected-SFT identity on both sides")

    rows: list[dict[str, Any]] = []
    per_episode: Counter[str] = Counter()
    seen_ids: set[str] = set()
    for row_number, raw in enumerate(read_jsonl(replay_path), 1):
        label = f"OPD replay row {row_number}"
        if raw.get("schema") != OPD_REPLAY_SCHEMA:
            raise ArtifactError(f"{label}: unsupported schema")
        required: dict[str, str] = {}
        for key in ("replay_id", "episode_id", "task_id", "source", "scenario"):
            value = raw.get(key)
            if not isinstance(value, str) or not value:
                raise ArtifactError(f"{label}.{key} must be a nonempty string")
            required[key] = value
        if required["replay_id"] in seen_ids:
            raise ArtifactError(f"duplicate OPD replay_id: {required['replay_id']}")
        seen_ids.add(required["replay_id"])
        per_episode[required["episode_id"]] += 1
        if per_episode[required["episode_id"]] > settings["maximum_decisions_per_episode"]:
            raise ArtifactError(f"{label} exceeds the per-episode OPD replay bound")
        require_training_membership(
            membership,
            task_id=required["task_id"],
            source=required["source"],
            scenario=required["scenario"],
        )
        request = raw.get("request")
        if not isinstance(request, Mapping):
            raise ArtifactError(f"{label}.request must be a mapping")
        _scan_visible(request)
        context = raw.get("privileged_context")
        if isinstance(context, str):
            try:
                context_value = json.loads(context)
            except json.JSONDecodeError as exc:
                raise ArtifactError(f"{label}.privileged_context is invalid JSON") from exc
        elif isinstance(context, Mapping):
            context_value = dict(context)
        else:
            raise ArtifactError(f"{label}.privileged_context must be JSON object text or mapping")
        _scan_privileged(context_value)
        rows.append(
            {
                "request": dict(request),
                "privileged_context": json.dumps(
                    context_value, sort_keys=True, separators=(",", ":")
                ),
                "metadata": {
                    "replay_id": required["replay_id"],
                    "episode_id": required["episode_id"],
                    "task_id": required["task_id"],
                },
            }
        )
    if not rows or len(rows) > settings["maximum_replay_rows"]:
        raise ArtifactError("OPD replay must contain between 1 and 128 bounded rows")
    if len(per_episode) > settings["episodes"]:
        raise ArtifactError("OPD replay exceeds the 64-episode bound")

    output = Path(output_dir).resolve()
    canonical_replay = publish_jsonl(output / "replay.jsonl", rows)
    context_template = (
        "Use this audited decision state, derived only from public evidence already visible "
        "to the agent:\n<decision_state>\n{context}\n</decision_state>"
    )
    config_output = output / "prime_output"
    lines = [
        "# OPTIONAL bounded OPD smoke; never a required campaign stage",
        f"max_steps = {int(settings['optimizer_updates'])}",
        f"seq_len = {int(settings['sequence_length'])}",
        f"output_dir = {_quoted(config_output)}",
        "clean_output_dir = false",
        "",
        "[env_vars]",
        'FLA_TILELANG = "0"',
        'WANDB_MODE = "disabled"',
        'VLLM_API_KEY = "EMPTY"',
        "",
        "[deployment]",
        'type = "single_node"',
        f"num_train_gpus = {int(settings['trainer_gpus'])}",
        f"num_infer_gpus = {int(settings['inference_gpus'])}",
        f"gpus_per_node = {int(settings['total_gpus'])}",
        "",
        "[weight_broadcast]",
        'type = "filesystem"',
        "",
        "[model]",
        f"name = {_quoted(student)}",
        "",
        "[ckpt]",
        "interval = 4",
        "resume_step = -1",
        "keep_last = 3",
        "",
        "[trainer.model]",
        f"seq_len = {int(settings['sequence_length'])}",
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
        f"target_modules = {_array(target_patterns)}",
        "modules_to_save = []",
        "",
        "[trainer.optim]",
        'type = "adamw"',
        f"lr = {float(settings['learning_rate'])}",
        "weight_decay = 0.01",
        "max_norm = 1.0",
        "",
        "[trainer.ckpt.weights]",
        "save_sharded = true",
        'save_format = "safetensors"',
        "save_adapter_separately = true",
        "",
        "[orchestrator]",
        f"batch_size = {int(settings['global_batch_size'])}",
        "group_size = 1",
        f"max_inflight_rollouts = {int(settings['maximum_inflight_rollouts'])}",
        "max_off_policy_steps = 1",
        f"pool_size = {int(settings['worker_pool_size'])}",
        "",
        "[orchestrator.model.lora]",
        'name = "caveat-27b-opd-smoke-r64-a128"',
        "rank = 64",
        "alpha = 128.0",
        "",
        "[orchestrator.renderer]",
        'name = "qwen3.5"',
        "enable_thinking = true",
        "",
        "[orchestrator.algo]",
        'type = "opd"',
        'context_key = "privileged_context"',
        f"context_template = {_quoted(context_template)}",
        "",
        "[orchestrator.algo.teacher]",
        f"name = {_quoted(teacher)}",
        'base_url = ["http://localhost:8001/v1"]',
        "",
        "[orchestrator.algo.renderer]",
        'name = "qwen3.5"',
        "enable_thinking = true",
        "",
        "[orchestrator.train.sampling]",
        "temperature = 1.0",
        "top_p = 0.95",
        f"max_completion_tokens = {int(settings['maximum_completion_tokens'])}",
        "",
        "[orchestrator.train.sampling.extra_body]",
        "top_k = 20",
        "presence_penalty = 0.0",
        "",
        "[[orchestrator.train.env]]",
        'name = "caveat-27b-opd-smoke"',
        (
            "taskset = { id = \"decision-replay-v1\", path = "
            f"{_quoted(canonical_replay)}, split = \"train\" }}"
        ),
        'harness = { id = "decision-replay-v1", runtime = { type = "subprocess" } }',
        "group_size = 1",
        "max_turns = 1",
        'timeout = { setup = 300.0, rollout = 900.0, finalize = 300.0, scoring = 300.0 }',
        f"pool = {{ type = \"static\", num_workers = {int(settings['worker_pool_size'])} }}",
        "",
        "[inference]",
        f"gpu_memory_utilization = {float(settings['gpu_memory_utilization_per_server'])}",
        "data_parallel_rpc_port = 13345",
        "enable_prefix_caching = false",
        f"lora_target_modules = {_array(target_leaves)}",
        "",
        "[inference.model]",
        f"max_model_len = {int(settings['sequence_length'])}",
        'dtype = "bfloat16"',
        'tool_call_parser = "qwen3_coder"',
        'reasoning_parser = "qwen3"',
        "",
        "[inference.server]",
        "port = 8000",
        "",
        "[inference.parallel]",
        "dp = 4",
        "tp = 1",
        "",
        "[inference.vllm_extra]",
        "language_model_only = true",
        f"max_num_seqs = {int(settings['vllm_max_num_seqs'])}",
    ]
    payload = ("\n".join(lines).rstrip() + "\n").encode()
    try:
        parsed = tomllib.loads(payload.decode())
    except tomllib.TOMLDecodeError as exc:
        raise ArtifactError("generated OPD TOML is invalid") from exc
    if parsed["orchestrator"]["algo"]["type"] != "opd":
        raise AssertionError("OPD algorithm drifted")
    config_path = publish_bytes(output / "opd_smoke.toml", payload)
    plan = {
        "schema": OPD_PLAN_SCHEMA,
        "scientific_role": "optional_nonblocking_smoke",
        "campaign_digest": campaign.digest,
        "student_teacher_identity": str(student),
        "smoke_report_sha256": sha256_file(smoke_report),
        "split_manifest_sha256": sha256_file(split_manifest_path),
        "source_replay_sha256": sha256_file(replay_path),
        "canonical_replay_sha256": sha256_file(canonical_replay),
        "replay_rows": len(rows),
        "episodes": len(per_episode),
        "config": str(config_path),
        "config_sha256": sha256_file(config_path),
        "resource_contract": {
            "gpus": settings["total_gpus"],
            "minimum_host_memory_gib": settings["minimum_host_memory_gib"],
            "wall_clock_kill_seconds": settings["wall_clock_kill_seconds"],
        },
        "loss": "pure_reverse_kl",
        "simultaneous_corrective_ce": False,
        "safe_fallback": "retain_reward_filtered_parent_on_any_failure_or_gate_miss",
    }
    publish_json(output / "plan.json", plan)
    return plan
