"""Bounded ReST/DAgger-style refinement for the contract compiler.

This is supervised refinement over samples from the selected SFT policy.  It is
not policy-gradient RL: exact successful samples are retained and each failed
sample receives one deterministic gold correction derived from the public task
instruction's authored contract.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    canonical_json,
    publish_json,
    publish_jsonl,
    read_json,
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
from .config import Campaign
from .quick_data import CONTRACT_SYSTEM_PROMPT
from .sft_data import SFT_SOURCE_SCHEMA, _scan_visible, _select_mixture, validate_sft_sample
from .splits import load_split_manifest, require_training_membership

TASK_SCHEMA = "harness-posttrain.contract-shadow-task.v1"
ROLLOUT_SCHEMA = "harness-posttrain.contract-rollout.v1"
ROLLOUT_MANIFEST_SCHEMA = "harness-posttrain.contract-rollouts.v1"
REFINEMENT_MANIFEST_SCHEMA = "harness-posttrain.contract-refinement.v1"
OPERATIONAL_SEMANTIC_SCORING_SCHEMA = (
    "harness-posttrain.contract-operational-equivalence.v1"
)

_LABEL_STOPWORDS = {
    "a",
    "an",
    "be",
    "from",
    "higher",
    "is",
    "level",
    "lower",
    "maximize",
    "minimize",
    "must",
    "of",
    "prefer",
    "preferred",
    "set",
    "should",
    "the",
    "to",
    "total",
}
_LABEL_ALIASES = {"cost": "price"}
_NUMERIC_OPERATORS = {"lt", "le", "gt", "ge"}


def _draft_types() -> tuple[Any, Any]:
    module = importlib.import_module("agentarena.scaffolds.browseruse_deliberative")
    return module._DraftContract, module._DeliberativeExtension  # noqa: SLF001


def browseruse_contract_system_prompt(output_format: type[Any]) -> str:
    """Render the schema exactly as the unchanged browser-use OpenAI adapter does.

    The benchmark configures ``dont_force_structured_output=True`` together with
    ``add_schema_to_system_prompt=True``.  Consequently its request contains no
    OpenAI ``response_format`` field: browser-use optimizes the Pydantic schema,
    wraps it in its ``agent_output`` envelope, and appends the Python mapping
    representation inside ``<json_schema>`` tags.  Keeping that wire behavior
    here prevents the training collector from exercising a stricter interface
    than the model sees at evaluation (and avoids vLLM rejecting regex schemas).
    """

    schema_module = importlib.import_module("browser_use.llm.schema")
    optimized = schema_module.SchemaOptimizer.create_optimized_json_schema(output_format)
    response_format = {
        "name": "agent_output",
        "strict": True,
        "schema": optimized,
    }
    return f"{CONTRACT_SYSTEM_PROMPT}\n<json_schema>\n{response_format}\n</json_schema>"


def contract_request_body(
    *,
    model: str,
    instruction: str,
    output_format: type[Any],
    temperature: float,
    max_tokens: int,
    top_p: float | None = None,
    top_k: int | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Build a schema-prompted request without provider-enforced JSON output."""

    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": browseruse_contract_system_prompt(output_format),
            },
            {"role": "user", "content": instruction},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if top_p is not None:
        body["top_p"] = top_p
    if top_k is not None:
        body["top_k"] = top_k
    if seed is not None:
        body["seed"] = seed
    return body


def _load_tasks(
    campaign: Campaign,
    *,
    split_manifest_path: str | Path,
    tasks_path: str | Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    _manifest, membership = load_split_manifest(campaign, split_manifest_path)
    rows = read_jsonl(tasks_path)
    plan = campaign.campaign["refinement"]["rollout_plan"]
    if len(rows) != int(plan["fresh_tasks"]):
        raise ArtifactError(
            f"contract shadow task file must contain exactly {plan['fresh_tasks']} rows"
        )
    tasks: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows, 1):
        if row.get("schema") != TASK_SCHEMA:
            raise ArtifactError(f"contract shadow task {index} has an unsupported schema")
        required = {}
        for key in ("task_id", "source", "scenario", "condition", "instruction"):
            value = row.get(key)
            if not isinstance(value, str) or not value:
                raise ArtifactError(f"contract shadow task {index}.{key} must be nonempty")
            required[key] = value
        if required["source"] != "procedural":
            raise ArtifactError("contract refinement accepts procedural tasks only")
        require_training_membership(
            membership,
            task_id=required["task_id"],
            source=required["source"],
            scenario=required["scenario"],
        )
        gold = row.get("gold_contract")
        if not isinstance(gold, Mapping):
            raise ArtifactError(f"contract shadow task {index}.gold_contract must be an object")
        _scan_visible(gold)
        if required["task_id"] in tasks:
            raise ArtifactError(f"duplicate contract shadow task: {required['task_id']}")
        tasks[required["task_id"]] = dict(row)
    expected = {
        "truthful_steered": round(len(rows) * float(plan["conditions"]["truthful_steered"])),
        "clean": round(len(rows) * float(plan["conditions"]["clean"])),
    }
    observed = Counter(row["condition"] for row in rows)
    if dict(observed) != expected:
        raise ArtifactError(
            f"contract shadow condition mix drifted: {dict(observed)} != {expected}"
        )
    return rows, tasks


def _seed(root_seed: int, task_id: str, sample_index: int) -> int:
    digest = hashlib.sha256(f"{root_seed}:{task_id}:{sample_index}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def _request_once(
    *,
    url: str,
    api_key: str,
    model: str,
    instruction: str,
    output_format: type[Any],
    seed: int,
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    body = contract_request_body(
        model=model,
        instruction=instruction,
        output_format=output_format,
        temperature=0.7,
        top_p=0.95,
        top_k=20,
        max_tokens=2048,
        seed=seed,
    )
    request = urllib.request.Request(
        url,
        method="POST",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
        payload = json.loads(response.read().decode())
    return body, payload


def _sample(
    *,
    base_url: str,
    api_key: str,
    model: str,
    task: Mapping[str, Any],
    sample_index: int,
    output_format: type[Any],
    root_seed: int,
    timeout_seconds: float,
) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/chat/completions"
    last_error = ""
    for attempt in range(1, 4):
        try:
            request, response = _request_once(
                url=url,
                api_key=api_key,
                model=model,
                instruction=str(task["instruction"]),
                output_format=output_format,
                seed=_seed(root_seed, str(task["task_id"]), sample_index),
                timeout_seconds=timeout_seconds,
            )
            message = response["choices"][0]["message"]
            content = message.get("content")
            # A completed request that spends its budget in reasoning can
            # legitimately carry null/empty final content.  That is a policy
            # miss for reward filtering, not an infrastructure failure: keep
            # it in the rollout denominator so the authored correction can
            # supervise it.  Malformed non-string content still retries and
            # ultimately fails closed.
            if content is None:
                content = ""
            if not isinstance(content, str):
                raise ValueError("response assistant content is not text")
            return {
                "schema": ROLLOUT_SCHEMA,
                "episode_id": f"{task['task_id']}:{sample_index}",
                "task_id": task["task_id"],
                "source": task["source"],
                "scenario": task["scenario"],
                "condition": task["condition"],
                "sample_index": sample_index,
                "infrastructure_complete": True,
                "request_sha256": sha256_bytes(canonical_json(request).encode()),
                "assistant_content": content,
                "response_id": response.get("id"),
                "usage": response.get("usage"),
            }
        except (KeyError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < 3:
                time.sleep(attempt)
    raise ArtifactError(
        f"contract rollout failed after three infrastructure attempts for "
        f"{task['task_id']}:{sample_index}: {last_error}"
    )


def collect_contract_rollouts(
    campaign: Campaign,
    *,
    split_manifest_path: str | Path,
    tasks_path: str | Path,
    selected_checkpoint_manifest: str | Path,
    base_url: str,
    model: str,
    output_dir: str | Path,
    api_key_env: str = "OPENAI_API_KEY",
    concurrency: int = 32,
    timeout_seconds: float = 600.0,
) -> dict[str, Any]:
    """Sample the selected SFT policy four times on each of 64 fresh prompts."""

    if not 1 <= concurrency <= 64:
        raise ArtifactError("contract rollout concurrency must be in [1, 64]")
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise ArtifactError(f"contract rollout API key is absent: {api_key_env}")
    tasks, _by_id = _load_tasks(
        campaign, split_manifest_path=split_manifest_path, tasks_path=tasks_path
    )
    checkpoint = read_json(selected_checkpoint_manifest)
    if not isinstance(checkpoint, Mapping):
        raise ArtifactError("selected checkpoint manifest must be an object")
    output = Path(output_dir).resolve()
    completed_manifest = output / "manifest.json"
    if completed_manifest.is_file():
        manifest = read_json(completed_manifest)
        expected_sampling = {
            "temperature": 0.7,
            "top_p": 0.95,
            "top_k": 20,
            "max_tokens": 2048,
        }
        expected_episodes = int(
            campaign.campaign["refinement"]["rollout_plan"]["total_episodes"]
        )
        expected_per_task = int(
            campaign.campaign["refinement"]["rollout_plan"]["episodes_per_task"]
        )
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema") != ROLLOUT_MANIFEST_SCHEMA
            or manifest.get("status") != "complete"
            or manifest.get("campaign_digest") != campaign.digest
            or manifest.get("selected_checkpoint_manifest")
            != str(Path(selected_checkpoint_manifest).resolve())
            or manifest.get("selected_checkpoint_manifest_sha256")
            != sha256_file(selected_checkpoint_manifest)
            or manifest.get("split_manifest_sha256") != sha256_file(split_manifest_path)
            or manifest.get("tasks_sha256") != sha256_file(tasks_path)
            or manifest.get("model") != model
            or manifest.get("endpoint") != base_url
            or manifest.get("episodes") != expected_episodes
            or manifest.get("fresh_tasks") != len(tasks)
            or manifest.get("episodes_per_task") != expected_per_task
            or manifest.get("sampling") != expected_sampling
        ):
            raise ArtifactError("completed contract rollout manifest is incompatible")
        output_metadata = manifest.get("output")
        if not isinstance(output_metadata, Mapping):
            raise ArtifactError("completed contract rollout output metadata is malformed")
        data_path = output / str(output_metadata.get("path"))
        if (
            output_metadata.get("path") != "rollouts.jsonl"
            or output_metadata.get("sha256") != sha256_file(data_path)
            or len(read_jsonl(data_path)) != expected_episodes
        ):
            raise ArtifactError("completed contract rollout bytes are incomplete or drifted")
        return manifest
    draft, _extension = _draft_types()
    per_task = int(campaign.campaign["refinement"]["rollout_plan"]["episodes_per_task"])
    futures = []
    rows = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for task in tasks:
            for sample_index in range(per_task):
                futures.append(
                    pool.submit(
                        _sample,
                        base_url=base_url,
                        api_key=api_key,
                        model=model,
                        task=task,
                        sample_index=sample_index,
                        output_format=draft,
                        root_seed=int(campaign.campaign["seed"]) + 2,
                        timeout_seconds=timeout_seconds,
                    )
                )
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: (row["task_id"], row["sample_index"]))
    expected = int(campaign.campaign["refinement"]["rollout_plan"]["total_episodes"])
    if len(rows) != expected:
        raise ArtifactError(f"contract rollout count drifted: {len(rows)} != {expected}")
    data = publish_jsonl(output / "rollouts.jsonl", rows)
    manifest = {
        "schema": ROLLOUT_MANIFEST_SCHEMA,
        "campaign_digest": campaign.digest,
        "selected_checkpoint_manifest": str(Path(selected_checkpoint_manifest).resolve()),
        "selected_checkpoint_manifest_sha256": sha256_file(selected_checkpoint_manifest),
        "split_manifest_sha256": sha256_file(split_manifest_path),
        "tasks_sha256": sha256_file(tasks_path),
        "model": model,
        "endpoint": base_url,
        "episodes": len(rows),
        "fresh_tasks": len(tasks),
        "episodes_per_task": per_task,
        "output": {"path": data.name, "sha256": sha256_file(data)},
        "sampling": {"temperature": 0.7, "top_p": 0.95, "top_k": 20, "max_tokens": 2048},
        "status": "complete",
    }
    publish_json(output / "manifest.json", manifest)
    return manifest


def _decode_content(content: str) -> Any:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1])
            if stripped.lstrip().startswith("json"):
                stripped = stripped.lstrip()[4:].lstrip()
    return json.loads(stripped)


def _score(
    instruction: str, content: str, gold: Mapping[str, Any]
) -> tuple[bool, bool, str | None, str]:
    draft_type, extension = _draft_types()
    try:
        decoded = _decode_content(content)
        draft = draft_type.model_validate(decoded)
        candidate = extension._materialize_contract(instruction, draft)  # noqa: SLF001
        candidate_json = canonical_json(draft.model_dump(mode="json"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return False, False, None, f"invalid contract JSON/schema: {type(exc).__name__}: {exc}"
    try:
        gold_draft = draft_type.model_validate(dict(gold))
        expected = extension._materialize_contract(instruction, gold_draft)  # noqa: SLF001
    except (TypeError, ValueError) as exc:  # authored source corruption
        raise ArtifactError(f"authored gold contract is invalid: {exc}") from exc
    semantic = candidate.fingerprint == expected.fingerprint
    return True, semantic, candidate_json, (
        "ok" if semantic else "contract differs from the literal authored criteria"
    )


def _label_tokens(*values: str) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        for token in re.findall(r"[a-z0-9]+", value.casefold().replace("_", " ")):
            token = _LABEL_ALIASES.get(token, token)
            if token not in _LABEL_STOPWORDS and not token.isdigit():
                tokens.add(token)
    return tokens


def _labels_match(candidate: Any, expected: Any) -> bool:
    candidate_tokens = _label_tokens(candidate.criterion_id, candidate.description)
    # The gold ID comes from the procedural generator and is deliberately not
    # visible in the instruction.  Its human-readable description is visible.
    expected_tokens = _label_tokens(expected.description)
    # Authored descriptions name the literal criterion.  Generated IDs and
    # prose are free-form, so require that name to be present without requiring
    # byte-identical internal labels.
    return bool(expected_tokens) and expected_tokens <= candidate_tokens


def _values_match(candidate: Any, expected: Any) -> bool:
    if isinstance(candidate, bool) or isinstance(expected, bool):
        return type(candidate) is type(expected) and candidate == expected
    if isinstance(candidate, (int, float)) and isinstance(expected, (int, float)):
        try:
            from decimal import Decimal

            return Decimal(str(candidate)) == Decimal(str(expected))
        except ArithmeticError:
            return False
    if isinstance(candidate, str) and isinstance(expected, str):
        return candidate.strip().casefold() == expected.strip().casefold()
    return canonical_json(candidate) == canonical_json(expected)


def _units_match(candidate: str | None, expected: str | None, *, optional: bool) -> bool:
    if candidate is None:
        return optional or expected is None
    if expected is None:
        return False
    if candidate.strip().casefold() == expected.strip().casefold():
        return True
    core = importlib.import_module("agentarena.scaffolds._deliberative_core")
    try:
        left = core._normalize_value(0, candidate, allow_opaque=True)  # noqa: SLF001
        right = core._normalize_value(0, expected, allow_opaque=True)  # noqa: SLF001
    except (TypeError, ValueError):
        return False
    return left.dimension == right.dimension and left.unit == right.unit


def _constraint_matches(candidate: Any, expected: Any) -> bool:
    if not _labels_match(candidate, expected) or candidate.operator != expected.operator:
        return False
    if not _values_match(candidate.expected, expected.expected):
        return False
    # Threshold units affect feasibility and therefore must be preserved.
    numeric = candidate.operator.value in _NUMERIC_OPERATORS
    return _units_match(candidate.unit, expected.unit, optional=not numeric)


def _objective_matches(candidate: Any, expected: Any) -> bool:
    if (
        not _labels_match(candidate, expected)
        or candidate.direction != expected.direction
        or candidate.priority != expected.priority
        or candidate.weight != expected.weight
    ):
        return False
    # An objective without a unit remains operational: the checkpoint requires
    # all candidate facts to use mutually compatible units.  An explicit but
    # incompatible generated unit, however, changes the comparison and fails.
    return _units_match(candidate.unit, expected.unit, optional=True)


def _perfect_match(
    candidates: list[Any], expected: list[Any], predicate: Any
) -> bool:
    if len(candidates) != len(expected):
        return False
    edges = [
        [index for index, item in enumerate(expected) if predicate(candidate, item)]
        for candidate in candidates
    ]
    if any(not options for options in edges):
        return False

    def assign(index: int, used: set[int]) -> bool:
        if index == len(edges):
            return True
        return any(
            option not in used and assign(index + 1, {*used, option})
            for option in edges[index]
        )

    return assign(0, set())


def _score_operational(
    instruction: str, content: str, gold: Mapping[str, Any]
) -> tuple[bool, bool, str | None, str]:
    """Score literal decision semantics while ignoring arbitrary internal prose.

    Criterion IDs and descriptions are model-authored handles.  Requiring them
    to reproduce a hidden generator's spelling falsely marks behaviorally
    identical contracts as failures.  This scorer instead checks a one-to-one
    match of the named criteria and every value that can affect feasibility or
    ranking.  It is used only for on-policy reward filtering; the historical
    sealed checkpoint-selection receipt remains untouched.
    """

    draft_type, extension = _draft_types()
    try:
        decoded = _decode_content(content)
        draft = draft_type.model_validate(decoded)
        candidate = extension._materialize_contract(instruction, draft)  # noqa: SLF001
        candidate_json = canonical_json(draft.model_dump(mode="json"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return False, False, None, f"invalid contract JSON/schema: {type(exc).__name__}: {exc}"
    try:
        gold_draft = draft_type.model_validate(dict(gold))
        expected = extension._materialize_contract(instruction, gold_draft)  # noqa: SLF001
    except (TypeError, ValueError) as exc:
        raise ArtifactError(f"authored gold contract is invalid: {exc}") from exc
    semantic = (
        candidate.search_mode == expected.search_mode
        and _perfect_match(
            list(candidate.constraints), list(expected.constraints), _constraint_matches
        )
        and _perfect_match(
            list(candidate.objectives), list(expected.objectives), _objective_matches
        )
    )
    return True, semantic, candidate_json, (
        "ok"
        if semantic
        else "contract changes a literal criterion or decision-relevant value"
    )


def materialize_contract_refinement(
    campaign: Campaign,
    *,
    split_manifest_path: str | Path,
    tasks_path: str | Path,
    rollout_manifest_path: str | Path,
    rehearsal_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Apply deterministic reward filtering and one correction per failed sample."""

    _tasks, tasks = _load_tasks(
        campaign, split_manifest_path=split_manifest_path, tasks_path=tasks_path
    )
    rollout_manifest = read_json(rollout_manifest_path)
    if (
        not isinstance(rollout_manifest, Mapping)
        or rollout_manifest.get("schema") != ROLLOUT_MANIFEST_SCHEMA
        or rollout_manifest.get("campaign_digest") != campaign.digest
        or rollout_manifest.get("tasks_sha256") != sha256_file(tasks_path)
    ):
        raise ArtifactError("contract rollout manifest is incompatible")
    rollouts_path = Path(rollout_manifest_path).resolve().parent / str(
        rollout_manifest.get("output", {}).get("path")
    )
    if sha256_file(rollouts_path) != rollout_manifest.get("output", {}).get("sha256"):
        raise ArtifactError("contract rollout bytes drifted")
    rollouts = read_jsonl(rollouts_path)
    expected = int(campaign.campaign["refinement"]["rollout_plan"]["total_episodes"])
    if len(rollouts) != expected:
        raise ArtifactError("contract rollout set is incomplete")
    groups: dict[str, list[dict[str, Any]]] = {"correction": [], "success": [], "rehearsal": []}
    seen: set[str] = set()
    syntax_valid = 0
    semantic_valid = 0
    _split, membership = load_split_manifest(campaign, split_manifest_path)
    for index, rollout in enumerate(rollouts, 1):
        if (
            rollout.get("schema") != ROLLOUT_SCHEMA
            or rollout.get("infrastructure_complete") is not True
        ):
            raise ArtifactError(f"contract rollout {index} is malformed or incomplete")
        episode_id = rollout.get("episode_id")
        task_id = rollout.get("task_id")
        if not isinstance(episode_id, str) or episode_id in seen or task_id not in tasks:
            raise ArtifactError(f"contract rollout {index} has invalid episode/task identity")
        seen.add(episode_id)
        task = tasks[str(task_id)]
        for key in ("source", "scenario", "condition"):
            if rollout.get(key) != task.get(key):
                raise ArtifactError(f"contract rollout {episode_id} conflicts on {key}")
        content = rollout.get("assistant_content")
        if not isinstance(content, str):
            raise ArtifactError(f"contract rollout {episode_id} has no assistant content")
        syntax, semantic, normalized, error = _score_operational(
            str(task["instruction"]), content, task["gold_contract"]
        )
        syntax_valid += int(syntax)
        semantic_valid += int(semantic)
        base = {
            "schema": SFT_SOURCE_SCHEMA,
            "sample_id": f"{'success' if semantic else 'correction'}:{episode_id}",
            "task_id": task_id,
            "source": task["source"],
            "scenario": task["scenario"],
            "tools": [],
        }
        if semantic:
            assistant = str(normalized)
            group = "success"
            user = str(task["instruction"])
        else:
            assistant = canonical_json(task["gold_contract"])
            group = "correction"
            user = (
                f"{task['instruction']}\n\nThe prior draft was invalid. Correct it without "
                f"adding anything to the instruction. Validation error: {error}"
            )
        source_row = {
            **base,
            "messages": [
                {"role": "system", "content": CONTRACT_SYSTEM_PROMPT},
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            ],
        }
        groups[group].append(
            validate_sft_sample(
                source_row,
                membership=membership,
                expected_stage=group,
                row_number=len(groups[group]) + 1,
            )
        )
    for row_number, row in enumerate(read_jsonl(rehearsal_path), 1):
        groups["rehearsal"].append(
            validate_sft_sample(
                row,
                membership=membership,
                expected_stage="rehearsal",
                row_number=row_number,
            )
        )
    config = campaign.campaign["refinement"]
    maximum_balanced = min(
        int(config["maximum_rows"]),
        *(math.floor(len(groups[name]) / float(config["mixture"][name])) for name in groups),
    )
    balanced_limit = maximum_balanced - (maximum_balanced % 10)
    if balanced_limit < 10:
        raise ArtifactError(
            "on-policy samples cannot realize the frozen 60/30/10 refinement mixture"
        )
    rows, counts = _select_mixture(
        groups,
        weights=config["mixture"],
        maximum_rows=balanced_limit,
        seed=int(campaign.campaign["seed"]) + 3,
    )
    output = Path(output_dir).resolve()
    data = publish_jsonl(output / "train.jsonl", rows)
    body = {
        "schema": REFINEMENT_MANIFEST_SCHEMA,
        "stage": "refinement",
        "method": "reward_filtered_on_policy_contract_sft",
        "is_policy_gradient_rl": False,
        "campaign_digest": campaign.digest,
        "split_manifest_sha256": sha256_file(split_manifest_path),
        "tasks_sha256": sha256_file(tasks_path),
        "rollout_manifest_sha256": sha256_file(rollout_manifest_path),
        "rehearsal_sha256": sha256_file(rehearsal_path),
        "episodes": len(rollouts),
        "syntax_valid": syntax_valid,
        "semantic_valid": semantic_valid,
        "semantic_scoring": OPERATIONAL_SEMANTIC_SCORING_SCHEMA,
        "reward": {
            "syntax_valid_weight": config["contract_reward"]["syntax_valid"],
            "semantic_exact_weight": config["contract_reward"]["semantic_exact"],
            "retain_threshold": config["contract_reward"]["retain_threshold"],
        },
        "candidate_counts": {name: len(values) for name, values in groups.items()},
        "configured_mixture": config["mixture"],
        "realized_counts": counts,
        "balanced_row_limit": balanced_limit,
        "one_gold_correction_per_failed_sample": True,
        "public_authored_contract_only": True,
        "output": {"path": data.name, "sha256": sha256_file(data), "rows": len(rows)},
    }
    manifest = dict(body)
    manifest["manifest_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    publish_json(output / "manifest.json", manifest)
    return manifest
