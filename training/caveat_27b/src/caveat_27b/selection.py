"""Select one of nine SFT adapters on sealed procedural shadow prompts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    canonical_json,
    publish_json,
    read_json,
    read_jsonl,
    sha256_file,
)
from .config import Campaign
from .contract_refinement import (
    TASK_SCHEMA,
    _draft_types,
    _score,
    contract_request_body,
)
from .procedural_corpus import _tool_schema

SELECTION_SCHEMA = "caveat-27b.selected-sft-checkpoint.v1"
PROFILES = ("balanced", "protocol-heavy", "recovery-heavy")
UPDATES = (5, 10, 20)


class MultiLoraServer(AbstractContextManager["MultiLoraServer"]):
    def __init__(
        self,
        *,
        base_model: Path,
        adapters: Mapping[str, Path],
        output_dir: Path,
        port: int = 8000,
        gpu_ids: tuple[int, ...] = tuple(range(8)),
        max_model_len: int = 32768,
    ) -> None:
        if len(gpu_ids) not in {4, 8} or len(gpu_ids) != len(set(gpu_ids)) or min(gpu_ids) < 0:
            raise ArtifactError("selection topology requires four or eight distinct GPU IDs")
        self.base_model = base_model
        self.adapters = dict(adapters)
        self.output_dir = output_dir
        self.port = port
        self.gpu_ids = gpu_ids
        if max_model_len < 1:
            raise ArtifactError("selection server context length must be positive")
        self.max_model_len = max_model_len
        self.process: subprocess.Popen[Any] | None = None
        self.log: Any = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def command(self) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model",
            str(self.base_model),
            "--served-model-name",
            "qwen35-frozen-base",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--dtype",
            "bfloat16",
            "--language-model-only",
            "--max-model-len",
            str(self.max_model_len),
            "--reasoning-parser",
            "qwen3",
            "--tool-call-parser",
            "qwen3_coder",
            "--enable-auto-tool-choice",
            "--no-enable-prefix-caching",
            "--no-enable-log-requests",
            "--data-parallel-size",
            str(len(self.gpu_ids)),
            "--enable-lora",
            "--max-loras",
            str(len(self.adapters)),
            "--max-cpu-loras",
            str(len(self.adapters)),
            "--max-lora-rank",
            "64",
            "--lora-modules",
            *[f"{name}={path}" for name, path in sorted(self.adapters.items())],
        ]
        return command

    def __enter__(self) -> MultiLoraServer:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        command = self.command()
        publish_json(self.output_dir / "command.json", command)
        self.log = (self.output_dir / "vllm.log").open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            command,
            env={
                **os.environ,
                "CUDA_VISIBLE_DEVICES": ",".join(str(gpu) for gpu in self.gpu_ids),
                "FLA_TILELANG": "0",
            },
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise ArtifactError(
                    f"multi-LoRA vLLM exited with {self.process.returncode}; see {self.output_dir}"
                )
            try:
                with urllib.request.urlopen(  # noqa: S310
                    f"http://127.0.0.1:{self.port}/health", timeout=3
                ) as response:
                    if response.status == 200:
                        return self
            except urllib.error.URLError:
                time.sleep(2)
        raise ArtifactError("multi-LoRA vLLM did not become ready within one hour")

    def __exit__(self, *exc: object) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=120)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=30)
        if self.log is not None:
            self.log.close()


def _post(base_url: str, payload: Mapping[str, Any], timeout: float = 600) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        method="POST",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "authorization": "Bearer EMPTY"},
    )
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                result = json.loads(response.read().decode())
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in {408, 429, 500, 502, 503, 504} or attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 3:
                raise
        time.sleep(attempt)
    if not isinstance(result, dict):
        raise ArtifactError("selection inference response is not an object")
    return result


def _content(response: Mapping[str, Any]) -> str | None:
    try:
        value = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ArtifactError("selection response has no assistant message") from exc
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise ArtifactError("selection response has no assistant content")
    return value


def _contract_query(
    base_url: str, model: str, task: Mapping[str, Any], output_format: type[Any]
) -> tuple[str, bool, bool]:
    response = _post(
        base_url,
        contract_request_body(
            model=model,
            instruction=str(task["instruction"]),
            output_format=output_format,
            temperature=0.0,
            max_tokens=2048,
        ),
    )
    content = _content(response)
    if content is None:
        return str(task["task_id"]), False, False
    syntax, semantic, _normalized, _error = _score(
        str(task["instruction"]), content, task["gold_contract"]
    )
    return str(task["task_id"]), syntax, semantic


def _tool_query(base_url: str, model: str, task: Mapping[str, Any]) -> tuple[str, bool]:
    tool = _tool_schema()
    response = _post(
        base_url,
        {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Submit the complete verified frontier with decision_checkpoint alone."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"The rendered coverage line is exactly: {task['rendered_basis']}\n"
                        f"Public marketplace state:\n{canonical_json(task['public_state'])}"
                    ),
                },
            ],
            "tools": [tool],
            "tool_choice": {"type": "function", "function": {"name": "decision_checkpoint"}},
            "parallel_tool_calls": False,
            "temperature": 0.0,
            "max_tokens": 4096,
        },
    )
    try:
        calls = response["choices"][0]["message"]["tool_calls"]
        valid = len(calls) == 1 and calls[0]["function"]["name"] == "decision_checkpoint"
        raw_arguments = calls[0]["function"]["arguments"]
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        valid = valid and canonical_json(arguments) == canonical_json(task["expected_arguments"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        valid = False
    return str(task["task_id"]), valid


def _adapter_inventory(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for profile in PROFILES:
        plan_path = root / "configs" / profile / "plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        by_update = {
            int(row["update"]): Path(row["adapter"]).resolve()
            for row in plan["candidates"]
        }
        for update in UPDATES:
            name = f"{profile}-u{update}"
            path = by_update[update]
            if not path.is_dir() or not (path / "adapter_config.json").is_file():
                raise ArtifactError(f"candidate adapter is absent or incomplete: {name}: {path}")
            result[name] = path
    return result


def _selection_rank(name: str, metrics: Mapping[str, Mapping[str, Any]]) -> tuple[Any, ...]:
    profile, update = name.rsplit("-u", 1)
    values = metrics[name]
    profile_order = {value: index for index, value in enumerate(PROFILES)}
    return (
        float(values["semantic_exact_rate"]),
        float(values["syntax_valid_rate"]),
        float(values["checkpoint_tool_valid_rate"]),
        -profile_order[profile],
        -UPDATES.index(int(update)),
    )


def _validate_completed_selection(
    manifest: Any,
    *,
    campaign: Campaign,
    adapters: Mapping[str, Path],
    num_gpus: int,
) -> dict[str, Any]:
    """Validate and reuse immutable selection evidence after a downstream retry.

    Checkpoint selection is expensive but deterministic.  A merge or rollout failure
    after selection must not launch the same 648 sealed inferences again.  Reuse is
    allowed only when the published manifest still binds the current campaign, exact
    adapter inventory, metric denominators, ranking rule, and selected adapter bytes.
    """

    if not isinstance(manifest, dict):
        raise ArtifactError("published selection manifest is not an object")
    expected_names = set(adapters)
    metrics = manifest.get("candidates")
    if (
        manifest.get("schema") != SELECTION_SCHEMA
        or manifest.get("campaign_digest") != campaign.digest
        or manifest.get("selection_split") != "procedural_validation"
        or manifest.get("caveat_shop_scenarios_used") != []
        or manifest.get("num_gpus") != num_gpus
        or manifest.get("candidate_count") != len(adapters)
        or manifest.get("rank_order")
        != campaign.campaign["selection_gate"]["rank_order"]
        or not isinstance(metrics, dict)
        or set(metrics) != expected_names
    ):
        raise ArtifactError("published selection manifest differs from the current campaign")

    count_fields = {
        "syntax_valid": 64,
        "semantic_exact": 64,
        "checkpoint_tool_valid": 8,
    }
    for name in sorted(expected_names):
        values = metrics.get(name)
        if not isinstance(values, dict):
            raise ArtifactError(f"published selection metrics are malformed: {name}")
        if values.get("contract_tasks") != 64 or values.get("checkpoint_tool_tasks") != 8:
            raise ArtifactError(f"published selection task denominators drifted: {name}")
        for field, denominator in count_fields.items():
            count = values.get(field)
            rate = values.get(f"{field}_rate")
            if (
                type(count) is not int
                or not 0 <= count <= denominator
                or not isinstance(rate, (int, float))
                or float(rate) != count / denominator
            ):
                raise ArtifactError(f"published selection metric is inconsistent: {name}.{field}")

    selected_name = max(sorted(expected_names), key=lambda name: _selection_rank(name, metrics))
    selected = manifest.get("selected")
    profile, update = selected_name.rsplit("-u", 1)
    adapter = adapters[selected_name].resolve()
    if (
        not isinstance(selected, dict)
        or selected.get("name") != selected_name
        or selected.get("profile") != profile
        or selected.get("update") != int(update)
        or selected.get("adapter") != str(adapter)
        or selected.get("adapter_config_sha256")
        != sha256_file(adapter / "adapter_config.json")
        or selected.get("metrics") != metrics[selected_name]
    ):
        raise ArtifactError("published selected checkpoint does not follow the frozen ranking")
    return manifest


def select_sft_checkpoint(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    output_dir: str | Path,
    concurrency: int = 32,
    num_gpus: int = 8,
) -> dict[str, Any]:
    """Serve all nine adapters together and select only on procedural validation."""

    if not 1 <= concurrency <= 64:
        raise ArtifactError("selection concurrency must be in [1, 64]")
    root = Path(campaign_root).resolve()
    smoke = json.loads((root / "smoke/smoke_report.json").read_text(encoding="utf-8"))
    base = Path(smoke["resolved_snapshot"]).resolve()
    if not base.is_dir() or smoke.get("snapshot", {}).get("model_id") != campaign.model["model_id"]:
        raise ArtifactError("selection base model is absent or differs from the campaign")
    contract_tasks = read_jsonl(root / "corpus/raw/selection_contract_tasks.jsonl")
    checkpoint_tasks = read_jsonl(root / "corpus/raw/selection_checkpoint_tasks.jsonl")
    if len(contract_tasks) != 64 or len(checkpoint_tasks) != 8:
        raise ArtifactError("sealed procedural selection task counts drifted")
    if any(row.get("schema") != TASK_SCHEMA for row in contract_tasks):
        raise ArtifactError("contract selection task schema drifted")
    if any(
        row.get("schema") != "caveat-27b.checkpoint-shadow-task.v1"
        for row in checkpoint_tasks
    ):
        raise ArtifactError("checkpoint selection task schema drifted")
    adapters = _adapter_inventory(root)
    draft, _extension = _draft_types()
    output = Path(output_dir).resolve()
    completed_manifest = output / "selected_checkpoint.json"
    if completed_manifest.is_file():
        return _validate_completed_selection(
            read_json(completed_manifest),
            campaign=campaign,
            adapters=adapters,
            num_gpus=num_gpus,
        )
    metrics: dict[str, dict[str, Any]] = {}
    with MultiLoraServer(
        base_model=base,
        adapters=adapters,
        output_dir=output / "server",
        gpu_ids=tuple(range(num_gpus)),
    ) as server:
        for model in sorted(adapters):
            contract_results = []
            tool_results = []
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [
                    pool.submit(_contract_query, server.base_url, model, task, draft)
                    for task in contract_tasks
                ]
                futures += [
                    pool.submit(_tool_query, server.base_url, model, task)
                    for task in checkpoint_tasks
                ]
                for future in as_completed(futures):
                    result = future.result()
                    if len(result) == 3:
                        contract_results.append(result)
                    else:
                        tool_results.append(result)
            metrics[model] = {
                "syntax_valid": sum(row[1] for row in contract_results),
                "semantic_exact": sum(row[2] for row in contract_results),
                "checkpoint_tool_valid": sum(row[1] for row in tool_results),
                "contract_tasks": len(contract_results),
                "checkpoint_tool_tasks": len(tool_results),
                "syntax_valid_rate": sum(row[1] for row in contract_results) / 64,
                "semantic_exact_rate": sum(row[2] for row in contract_results) / 64,
                "checkpoint_tool_valid_rate": sum(row[1] for row in tool_results) / 8,
            }
    selected = max(sorted(adapters), key=lambda name: _selection_rank(name, metrics))
    profile, update = selected.rsplit("-u", 1)
    manifest = {
        "schema": SELECTION_SCHEMA,
        "campaign_digest": campaign.digest,
        "selection_split": "procedural_validation",
        "caveat_shop_scenarios_used": [],
        "num_gpus": num_gpus,
        "candidate_count": len(adapters),
        "rank_order": campaign.campaign["selection_gate"]["rank_order"],
        "selected": {
            "name": selected,
            "profile": profile,
            "update": int(update),
            "adapter": str(adapters[selected]),
            "adapter_config_sha256": sha256_file(adapters[selected] / "adapter_config.json"),
            "metrics": metrics[selected],
        },
        "candidates": metrics,
    }
    publish_json(output / "selected_checkpoint.json", manifest)
    return manifest
