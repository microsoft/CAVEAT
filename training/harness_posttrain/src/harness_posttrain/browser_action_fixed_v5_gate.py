"""Pre-registered one-candidate procedural gate for fixed-v5 correction.

The gate never selects among checkpoints and never reads Amazon data.  It
compares the single, predeclared step-26 adapter with the already frozen
step-20 procedural baseline.  All decision thresholds are materialized before
the first candidate inference, and only observable response fields are used.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import stat
import urllib.request
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
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
from .browser_action_continuation import _legacy_tree_digest, _tree_identity
from .browser_action_curriculum import _output_stack
from .browser_action_selection import (
    _SINGLE_ATTEMPT_EXECUTION,
    _evidence_key,
    _expected_specs,
    _metrics_from_evidence,
    _score_action_content,
    _selection_tasks,
)
from .browser_action_selection_v4 import (
    ADAPTIVE_SELECTION_SCHEMA,
    _load_tokenizer,
    _rendered_prompt_tokens,
    _validate_adaptive_selection_artifacts,
)
from .config import Campaign
from .contract_refinement import _score
from .selection import MultiLoraServer, _content

FIXED_V5_TRAINING_RECEIPT_SCHEMA = (
    "harness-posttrain.browser-action-fixed-v5-training-receipt.v1"
)
FIXED_V5_GATE_POLICY_SCHEMA = "harness-posttrain.browser-action-fixed-v5-gate-policy.v1"
FIXED_V5_GATE_EVIDENCE_SCHEMA = (
    "harness-posttrain.browser-action-fixed-v5-gate-evidence.v1"
)
FIXED_V5_GATE_ATTEMPT_SCHEMA = (
    "harness-posttrain.browser-action-fixed-v5-gate-attempt.v1"
)
FIXED_V5_GATE_MANIFEST_SCHEMA = (
    "harness-posttrain.browser-action-fixed-v5-gate-manifest.v1"
)
FIXED_V5_OBSERVABLE_FIELD_POLICY_SCHEMA = (
    "harness-posttrain.browser-action-fixed-v5-observable-field-policy.v1"
)

_BASELINE = "step20"
_CANDIDATE = "step26-fixed-v5"
_NATIVE_MAX_MODEL_LEN = 262_144
_NATIVE_CONTEXT_RESERVE_TOKENS = 1
_CONTRACT_MAX_TOKENS = 4096
_REQUEST_TIMEOUT_SECONDS = 14_400
_REQUEST_EXECUTION = {
    **_SINGLE_ATTEMPT_EXECUTION,
    "transport_timeout_seconds": _REQUEST_TIMEOUT_SECONDS,
    "automatic_request_retries": 0,
    "same_process_request_resubmissions": 0,
}

# Frozen before candidate inference.  These criteria deliberately test the
# corrected action-state behavior without demanding saturation of the sealed
# suite.  The actual Amazon laptop arm remains the scientific development gate.
_GATE_CRITERIA = {
    "minimum_action_transition_exact_count_delta": 2,
    "minimum_complete_plus_repair_exact_count_delta": 2,
    "minimum_strictly_improved_target_transition_count": 1,
    "maximum_incomplete_continue_exact_count_regression": 1,
    "maximum_postapproval_action_exact_count_regression": 1,
    "maximum_contract_syntax_valid_count_regression": 2,
    "maximum_contract_semantic_exact_count_regression": 0,
    "maximum_premature_checkpoint_or_purchase_count_delta": 0,
    "require_distinct_candidate_adapter": True,
    "require_all_candidate_responses_natural_stop": True,
}


def _body_hash(value: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(value).encode())


def _safe_file(path: Path) -> Path:
    if path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.lstat().st_mode):
        raise ArtifactError(f"fixed-v5 gate artifact is not a regular file: {path}")
    return path


def _bound_record(path: Path, *, schema: str, hash_field: str) -> dict[str, Any]:
    _safe_file(path)
    record = read_json(path)
    if not isinstance(record, dict):
        raise ArtifactError(f"{schema} record is not an object")
    body = dict(record)
    digest = body.pop(hash_field, None)
    if record.get("schema") != schema or digest != _body_hash(body):
        raise ArtifactError(f"{schema} body binding changed")
    return record


def _same_path(value: Any, expected: Path, *, label: str) -> None:
    if not isinstance(value, str) or Path(value).resolve() != expected:
        raise ArtifactError(f"{label} path is not the bounded campaign artifact")


def _training_inventory(
    campaign: Campaign, root: Path, receipt_path: Path
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Validate the fixed-v5 receipt and return its sole candidate."""

    receipt = _bound_record(
        receipt_path,
        schema=FIXED_V5_TRAINING_RECEIPT_SCHEMA,
        hash_field="receipt_body_sha256",
    )
    try:
        relative = receipt_path.resolve().relative_to(root)
    except ValueError as exc:
        raise ArtifactError("fixed-v5 training receipt is outside the campaign") from exc
    parts = relative.parts
    if (
        len(parts) != 4
        or parts[0] != "browser_action_fixed_v5"
        or len(parts[1]) != 40
        or any(character not in "0123456789abcdef" for character in parts[1])
        or parts[2:] != ("training", "training_receipt.json")
    ):
        raise ArtifactError("fixed-v5 training receipt has an invalid stage layout")
    stage = root / parts[0] / parts[1]
    parent = (root / "selected/merged").resolve()
    provenance = parent / "merge_provenance.json"
    plan = stage / "training/plan.json"
    candidate_path = (
        stage / "training/prime_output/weights/step_26/lora_adapters"
    ).resolve()
    candidate = receipt.get("candidate")
    if (
        receipt.get("status") != "ok"
        or receipt.get("stage") != "browser_action_fixed_v5"
        or receipt.get("campaign_digest") != campaign.digest
        or receipt.get("artifact_source_git_sha") != parts[1]
        or receipt.get("optimizer_updates") != 26
        or receipt.get("new_optimizer_updates") != 6
        or receipt.get("checkpoint_updates") != [26]
        or receipt.get("selection_performed") is not False
        or not isinstance(candidate, Mapping)
        or candidate.get("name") != "step26"
        or candidate.get("update") != 26
    ):
        raise ArtifactError("fixed-v5 training receipt is incompatible")
    _same_path(receipt.get("parent_model"), parent, label="fixed-v5 parent")
    if receipt.get("parent_merge_provenance_sha256") != sha256_file(provenance):
        raise ArtifactError("fixed-v5 parent merge provenance changed")
    if receipt.get("plan_sha256") != sha256_file(plan):
        raise ArtifactError("fixed-v5 training plan changed")
    _same_path(candidate.get("path"), candidate_path, label="fixed-v5 candidate")

    identity = _tree_identity(candidate_path)
    stable = candidate_path.parent / "STABLE"
    config_path = candidate_path / "adapter_config.json"
    config = read_json(config_path)
    weights = list(candidate_path.glob("adapter_model*.safetensors")) + list(
        candidate_path.glob("adapter_model*.bin")
    )
    try:
        shape_ok = config.get("r") == 64 and float(config.get("lora_alpha", -1)) == 128.0
    except (AttributeError, TypeError, ValueError):
        shape_ok = False
    if (
        candidate.get("files") != identity["files"]
        or candidate.get("bytes") != identity["bytes"]
        or candidate.get("tree_sha256") != identity["tree_sha256"]
        or candidate.get("sha256") != _legacy_tree_digest(candidate_path)
        or candidate.get("adapter_config_sha256") != sha256_file(config_path)
        or candidate.get("stable_marker_sha256") != sha256_file(stable)
        or not shape_ok
        or Path(str(config.get("base_model_name_or_path", ""))).resolve() != parent
        or len(weights) != 1
        or weights[0].stat().st_size == 0
    ):
        raise ArtifactError("fixed-v5 candidate differs from its training receipt")
    stable_mtime = stable.stat().st_mtime_ns
    if any(
        item.stat().st_mtime_ns > stable_mtime
        for item in candidate_path.rglob("*")
        if item.is_file()
    ):
        raise ArtifactError("fixed-v5 candidate changed after its STABLE marker")
    return parent, {
        "name": _CANDIDATE,
        "update": 26,
        "path": str(candidate_path),
        "files": identity["files"],
        "bytes": identity["bytes"],
        "tree_sha256": identity["tree_sha256"],
        "legacy_tree_sha256": candidate["sha256"],
        "adapter_config_sha256": sha256_file(config_path),
        "stable_marker_sha256": sha256_file(stable),
    }, receipt


def _fixed_specs(
    *,
    contracts: list[dict[str, Any]],
    checkpoints: list[dict[str, Any]],
    system: str,
    output_model: type[Any],
    tokenizer: Any,
) -> list[dict[str, Any]]:
    specs = _expected_specs(
        candidate_names={_CANDIDATE},
        contracts=contracts,
        checkpoints=checkpoints,
        system=system,
        output_model=output_model,
    )
    for spec in specs:
        request = dict(spec["request"])
        prompt_tokens = _rendered_prompt_tokens(tokenizer, request)
        if spec["kind"] == "browser_action":
            max_tokens = _NATIVE_MAX_MODEL_LEN - prompt_tokens - (
                _NATIVE_CONTEXT_RESERVE_TOKENS
            )
        else:
            max_tokens = _CONTRACT_MAX_TOKENS
        if max_tokens < 1 or prompt_tokens + max_tokens > _NATIVE_MAX_MODEL_LEN:
            raise ArtifactError("fixed-v5 request exceeds native context")
        request["max_tokens"] = max_tokens
        spec["request"] = request
        spec["prompt_token_audit"] = {
            "rendered_prompt_tokens": prompt_tokens,
            "max_tokens": max_tokens,
            "native_max_model_len": _NATIVE_MAX_MODEL_LEN,
            "native_context_reserve_tokens": (
                _NATIVE_MAX_MODEL_LEN - prompt_tokens - max_tokens
            ),
        }
        if spec["kind"] == "browser_action":
            spec["observable_field_policy"] = _observable_field_policy(spec)
    return specs


def _baseline_evidence(
    *,
    campaign: Campaign,
    root: Path,
    selection_dir: Path,
    contracts: list[dict[str, Any]],
    checkpoints: list[dict[str, Any]],
    system: str,
    output_model: type[Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest_path = selection_dir / "selected_checkpoint.json"
    evidence_path = selection_dir / "raw_evidence.jsonl"
    manifest = read_json(manifest_path)
    if (
        selection_dir.is_symlink()
        or not selection_dir.is_dir()
        or selection_dir.name != "selection_nonbinding_v4"
        or not isinstance(manifest, Mapping)
        or manifest.get("schema") != ADAPTIVE_SELECTION_SCHEMA
        or manifest.get("campaign_digest") != campaign.digest
    ):
        raise ArtifactError("fixed-v5 baseline is not the reviewed adaptive-v4 selection")
    old_specs = _expected_specs(
        candidate_names={"step20", "step22", "step24", "step26", "step28"},
        contracts=contracts,
        checkpoints=checkpoints,
        system=system,
        output_model=output_model,
    )
    execution = _validate_adaptive_selection_artifacts(
        selection_dir, manifest, old_specs, output_model
    )
    inventory = manifest.get("candidate_inventory")
    baseline_candidate = inventory.get(_BASELINE) if isinstance(inventory, Mapping) else None
    if not isinstance(baseline_candidate, Mapping):
        raise ArtifactError("fixed-v5 baseline adapter identity is absent")
    source_rows = [
        row for row in read_jsonl(evidence_path) if row.get("candidate") == _BASELINE
    ]
    rows = [_gate_scored_baseline_row(row, output_model) for row in source_rows]
    if len(rows) != 96:
        raise ArtifactError("fixed-v5 baseline step20 denominator drifted")
    return rows, {
        "selection_dir": str(selection_dir.resolve()),
        "selection_manifest": str(manifest_path.resolve()),
        "selection_manifest_sha256": sha256_file(manifest_path),
        "selection_manifest_body_sha256": manifest.get("manifest_body_sha256"),
        "raw_evidence": str(evidence_path.resolve()),
        "raw_evidence_sha256": sha256_file(evidence_path),
        "adaptive_execution": execution,
        "candidate": _BASELINE,
        "candidate_inventory": dict(baseline_candidate),
        "candidate_tree_sha256": baseline_candidate.get("tree_sha256"),
        "rows": 96,
        "response_sha256": sorted(str(row["response_sha256"]) for row in rows),
    }


def _policy_body(
    *,
    campaign: Campaign,
    inputs: Mapping[str, Any],
    training_receipt_path: Path,
    training_receipt: Mapping[str, Any],
    candidate: Mapping[str, Any],
    baseline: Mapping[str, Any],
    candidate_specs: list[dict[str, Any]],
    num_gpus: int,
) -> dict[str, Any]:
    return {
        "schema": FIXED_V5_GATE_POLICY_SCHEMA,
        "status": "frozen_before_candidate_inference",
        "campaign_digest": campaign.digest,
        "selection_performed": False,
        "amazon_data_used": False,
        "evaluation_split": "procedural_validation",
        "baseline": dict(baseline),
        "candidate": dict(candidate),
        "training_receipt": str(training_receipt_path.resolve()),
        "training_receipt_sha256": sha256_file(training_receipt_path),
        "training_receipt_body_sha256": training_receipt["receipt_body_sha256"],
        "sealed_inputs": dict(inputs),
        "criteria": dict(_GATE_CRITERIA),
        "observable_action_scoring": {
            "schema": FIXED_V5_OBSERVABLE_FIELD_POLICY_SCHEMA,
            "rule": (
                "Compare the exact action count, action name, and every action "
                "parameter. For each decision-checkpoint candidate, compare "
                "source_url exactly iff that exact expected URL occurs in a user "
                "message for this request; otherwise omit only source_url."
            ),
            "messages_considered": ["user"],
            "contract_objective_units": "literal_exact",
            "per_request": [
                {
                    "task_id": spec["task_id"],
                    "transition": spec["transition"],
                    "field_inclusion": spec["observable_field_policy"],
                }
                for spec in candidate_specs
                if spec["kind"] == "browser_action"
            ],
        },
        "request_counts": {"contract": 64, "browser_action": 32, "total": 96},
        "candidate_request_policy": {
            "temperature": 0.0,
            "native_max_model_len": _NATIVE_MAX_MODEL_LEN,
            "action_completion": "all_remaining_native_context_minus_one_token",
            "contract_max_tokens": _CONTRACT_MAX_TOKENS,
            "request_timeout_seconds": _REQUEST_TIMEOUT_SECONDS,
            "maximum_http_attempts_per_request": 1,
            "automatic_request_retries": 0,
            "same_process_request_resubmissions": 0,
        },
        "num_gpus": num_gpus,
    }


def _publish_bound_record(
    path: Path, body: Mapping[str, Any], *, hash_field: str
) -> dict[str, Any]:
    record = {**body, hash_field: _body_hash(body)}
    publish_json(path, record)
    return record


def _checkpoint_candidates(action: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    checkpoint = action.get("decision_checkpoint")
    candidates = checkpoint.get("candidates") if isinstance(checkpoint, Mapping) else None
    if not isinstance(candidates, list):
        return []
    return [candidate for candidate in candidates if isinstance(candidate, Mapping)]


def _observable_field_policy(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze which source URLs were actually available to this request."""

    request = spec.get("request")
    messages = request.get("messages") if isinstance(request, Mapping) else None
    user_text = "\n".join(
        str(message.get("content"))
        for message in messages or []
        if isinstance(message, Mapping)
        and message.get("role") == "user"
        and isinstance(message.get("content"), str)
    )
    expected_actions = [spec.get("expected_action"), spec.get("approved_action")]
    candidates: dict[str, dict[str, Any]] = {}
    for action in expected_actions:
        if not isinstance(action, Mapping):
            continue
        for candidate in _checkpoint_candidates(action):
            candidate_id = candidate.get("id")
            source_url = candidate.get("source_url")
            if not isinstance(candidate_id, str) or not isinstance(source_url, str):
                raise ArtifactError("fixed-v5 expected checkpoint candidate is malformed")
            visible = source_url in user_text
            prior = candidates.get(candidate_id)
            entry = {
                "candidate_id": candidate_id,
                "source_url_expected_sha256": sha256_bytes(source_url.encode()),
                "source_url_present_in_user_messages": visible,
                "compare_source_url": visible,
            }
            if prior is not None and prior != entry:
                raise ArtifactError("fixed-v5 source URL visibility is ambiguous")
            candidates[candidate_id] = entry
    return {
        "schema": FIXED_V5_OBSERVABLE_FIELD_POLICY_SCHEMA,
        "action_count": "exact",
        "action_name": "exact",
        "all_action_parameters_except_source_url": "exact",
        "source_url_rule": "exact_if_expected_value_present_in_user_messages_else_omit",
        "user_message_sha256": sha256_bytes(user_text.encode()),
        "checkpoint_candidates": [candidates[key] for key in sorted(candidates)],
    }


def _observable_action_content(
    content: str | None,
    *,
    policy: Mapping[str, Any],
    reference_actions: list[Mapping[str, Any]],
) -> tuple[str | None, int]:
    """Normalize only source URLs that were not visible to the model."""

    if content is None:
        return None, 0
    try:
        from agentarena.scaffolds.browseruse import _strip_fences

        value = json.loads(_strip_fences(content))
    except (TypeError, json.JSONDecodeError):
        return content, 0
    if not isinstance(value, dict) or not isinstance(value.get("action"), list):
        return content, 0
    expected_urls = {
        str(candidate["id"]): str(candidate["source_url"])
        for action in reference_actions
        for candidate in _checkpoint_candidates(action)
    }
    compare = {
        str(entry["candidate_id"]): bool(entry["compare_source_url"])
        for entry in policy.get("checkpoint_candidates", [])
        if isinstance(entry, Mapping)
    }
    normalized = copy.deepcopy(value)
    substitutions = 0
    for action in normalized["action"]:
        if not isinstance(action, dict):
            continue
        for candidate in _checkpoint_candidates(action):
            candidate_id = candidate.get("id")
            if (
                isinstance(candidate, dict)
                and isinstance(candidate_id, str)
                and candidate_id in expected_urls
                and compare.get(candidate_id) is False
            ):
                if candidate.get("source_url") != expected_urls[candidate_id]:
                    substitutions += 1
                candidate["source_url"] = expected_urls[candidate_id]
    return canonical_json(normalized), substitutions


def _response_score(
    spec: Mapping[str, Any], content: str | None, output_model: type[Any]
) -> dict[str, Any]:
    if spec["kind"] == "contract":
        if content is None:
            return {
                "syntax_valid": False,
                "semantic_exact": False,
                "normalized_contract": None,
                "error": "empty assistant content",
            }
        syntax, semantic, normalized, error = _score(
            str(spec["instruction"]), content, spec["gold_contract"]
        )
        return {
            "syntax_valid": syntax,
            "semantic_exact": semantic,
            "normalized_contract": normalized,
            "error": error,
        }
    policy = spec.get("observable_field_policy")
    expected_action = spec["expected_action"]
    if not isinstance(policy, Mapping):
        policy = _observable_field_policy(spec)
    normalized, substitutions = _observable_action_content(
        content,
        policy=policy,
        reference_actions=[expected_action, spec["approved_action"]],
    )
    score = _score_action_content(
        normalized,
        output_model=output_model,
        transition=str(spec["transition"]),
        expected_action=expected_action,
        approved_action=spec["approved_action"],
    )
    return {
        **score,
        "observable_field_policy": dict(policy),
        "hidden_source_url_values_substituted_for_scoring": substitutions,
    }


def _gate_scored_baseline_row(
    row: Mapping[str, Any], output_model: type[Any]
) -> dict[str, Any]:
    """Rescore frozen step-20 evidence with the same observable-field policy."""

    result = copy.deepcopy(dict(row))
    if result.get("kind") == "browser_action":
        policy = _observable_field_policy(result)
        result["observable_field_policy"] = policy
        result["scoring"] = _response_score(
            result, result.get("assistant_content"), output_model
        )
    return result


def _post_once(base_url: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        method="POST",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "authorization": "Bearer EMPTY"},
    )
    with urllib.request.urlopen(  # noqa: S310
        request, timeout=_REQUEST_TIMEOUT_SECONDS
    ) as response:
        result = json.loads(response.read().decode())
    if not isinstance(result, dict):
        raise ArtifactError("fixed-v5 gate response is not an object")
    return result


def _execute_candidate_spec(
    base_url: str,
    spec: Mapping[str, Any],
    output_model: type[Any],
    process_attempt_id: int,
) -> dict[str, Any]:
    request = dict(spec["request"])
    response = _post_once(base_url, request)
    try:
        choice = response["choices"][0]
        finish_reason = choice.get("finish_reason")
        prompt_tokens = response["usage"]["prompt_tokens"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ArtifactError("fixed-v5 response lacks finish or usage identity") from exc
    if finish_reason != "stop":
        raise ArtifactError(f"fixed-v5 response did not stop naturally: {finish_reason!r}")
    audit = spec["prompt_token_audit"]
    if prompt_tokens != audit["rendered_prompt_tokens"]:
        raise ArtifactError("fixed-v5 server prompt token count differs from local audit")
    content = _content(response)
    public = {
        key: value
        for key, value in spec.items()
        if key not in {"request", "prompt_token_audit"}
    }
    return {
        "schema": FIXED_V5_GATE_EVIDENCE_SCHEMA,
        **public,
        "request": request,
        "request_sha256": sha256_bytes(canonical_json(request).encode()),
        "response": response,
        "response_sha256": sha256_bytes(canonical_json(response).encode()),
        "assistant_content": content,
        "finish_reason": finish_reason,
        "completion_ceiling_bound": False,
        "prompt_token_audit": dict(audit),
        "request_execution": dict(_REQUEST_EXECUTION),
        "gate_process_attempt_id": process_attempt_id,
        "scoring": _response_score(spec, content, output_model),
    }


def _cache_name(spec: Mapping[str, Any]) -> str:
    identity = {
        "evidence_key": list(_evidence_key(spec)),
        "request_sha256": sha256_bytes(canonical_json(spec["request"]).encode()),
        "prompt_token_audit": spec["prompt_token_audit"],
        "request_execution": _REQUEST_EXECUTION,
    }
    return sha256_bytes(canonical_json(identity).encode()) + ".json"


def _validate_candidate_row(
    row: Mapping[str, Any], spec: Mapping[str, Any], output_model: type[Any]
) -> None:
    response = row.get("response")
    try:
        content = _content(response) if isinstance(response, Mapping) else None
        finish = response["choices"][0].get("finish_reason")
        prompt_tokens = response["usage"]["prompt_tokens"]
    except (ArtifactError, KeyError, IndexError, TypeError) as exc:
        raise ArtifactError("fixed-v5 cached response is malformed") from exc
    public = {
        key: value
        for key, value in spec.items()
        if key not in {"request", "prompt_token_audit"}
    }
    if (
        row.get("schema") != FIXED_V5_GATE_EVIDENCE_SCHEMA
        or any(row.get(key) != value for key, value in public.items())
        or row.get("request") != spec["request"]
        or row.get("request_sha256")
        != sha256_bytes(canonical_json(spec["request"]).encode())
        or row.get("response_sha256")
        != sha256_bytes(canonical_json(response).encode())
        or row.get("assistant_content") != content
        or row.get("finish_reason") != "stop"
        or finish != "stop"
        or row.get("completion_ceiling_bound") is not False
        or row.get("prompt_token_audit") != spec["prompt_token_audit"]
        or prompt_tokens != spec["prompt_token_audit"]["rendered_prompt_tokens"]
        or row.get("request_execution") != _REQUEST_EXECUTION
        or type(row.get("gate_process_attempt_id")) is not int
        or row["gate_process_attempt_id"] < 1
        or row.get("scoring") != _response_score(spec, content, output_model)
    ):
        raise ArtifactError("fixed-v5 cached evidence binding changed")


def _load_cache(
    cache_dir: Path, specs: list[dict[str, Any]], output_model: type[Any]
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    expected = {_evidence_key(spec): spec for spec in specs}
    if cache_dir.is_symlink():
        raise ArtifactError("fixed-v5 response cache cannot be a symlink")
    cache_dir.mkdir(parents=True, exist_ok=True)
    observed: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for path in cache_dir.iterdir():
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode) or path.suffix != ".json":
            raise ArtifactError("fixed-v5 response cache contains an unsafe entry")
        row = read_json(path)
        if not isinstance(row, dict):
            raise ArtifactError("fixed-v5 cached row is not an object")
        key = _evidence_key(row)
        spec = expected.get(key)
        if spec is None or key in observed or path.name != _cache_name(spec):
            raise ArtifactError("fixed-v5 cached response identity changed")
        _validate_candidate_row(row, spec, output_model)
        observed[key] = row
    return observed


def _attempt_body(
    *,
    identifier: int,
    status: str,
    cached_before: int,
    completed: int,
    external_interruption: bool,
    failure: BaseException | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": FIXED_V5_GATE_ATTEMPT_SCHEMA,
        "status": status,
        "attempt_id": identifier,
        "cached_before_attempt": cached_before,
        "completed_in_attempt": completed,
        "cached_after_attempt": cached_before + completed,
        "expected_total": 96,
        "automatic_request_retries": 0,
        "same_process_request_resubmissions": 0,
        "external_process_interruption": external_interruption,
        "uncached_inflight_requests_may_have_been_reexecuted": external_interruption,
        "aggregate_zero_generation_resets_claimed": not external_interruption,
    }
    if failure is not None:
        body["failure"] = {"type": type(failure).__name__, "message": str(failure)}
    return body


def _attempts(output: Path, *, cached: int) -> tuple[list[dict[str, Any]], int]:
    root = output / "attempts"
    if root.is_symlink():
        raise ArtifactError("fixed-v5 attempt directory cannot be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    result: list[dict[str, Any]] = []
    previous = 0
    paths = sorted(root.iterdir())
    for identifier, path in enumerate(paths, 1):
        if path.is_symlink() or not path.is_dir() or path.name != f"attempt-{identifier:04d}":
            raise ArtifactError("fixed-v5 attempt inventory is unsafe")
        start = _bound_record(
            path / "start.json",
            schema=FIXED_V5_GATE_ATTEMPT_SCHEMA,
            hash_field="body_sha256",
        )
        receipt_path = path / "receipt.json"
        if receipt_path.exists() or receipt_path.is_symlink():
            receipt = _bound_record(
                receipt_path,
                schema=FIXED_V5_GATE_ATTEMPT_SCHEMA,
                hash_field="body_sha256",
            )
        else:
            completed = cached - previous
            if completed < 0:
                raise ArtifactError("fixed-v5 cache regressed after interruption")
            receipt = _publish_bound_record(
                receipt_path,
                _attempt_body(
                    identifier=identifier,
                    status="externally_interrupted",
                    cached_before=previous,
                    completed=completed,
                    external_interruption=True,
                ),
                hash_field="body_sha256",
            )
        if (
            start.get("status") != "started"
            or start.get("attempt_id") != identifier
            or start.get("cached_before_attempt") != previous
            or start.get("expected_total") != 96
            or receipt.get("attempt_id") != identifier
            or receipt.get("cached_before_attempt") != previous
            or receipt.get("cached_after_attempt")
            != previous + receipt.get("completed_in_attempt", -1)
            or receipt.get("status") not in {"complete", "externally_interrupted"}
        ):
            raise ArtifactError("fixed-v5 attempt lineage changed")
        previous = int(receipt["cached_after_attempt"])
        result.append(
            {
                "attempt_id": identifier,
                "status": receipt["status"],
                "start": str((path / "start.json").resolve()),
                "start_sha256": sha256_file(path / "start.json"),
                "receipt": str(receipt_path.resolve()),
                "receipt_sha256": sha256_file(receipt_path),
                "cached_before_attempt": receipt["cached_before_attempt"],
                "completed_in_attempt": receipt["completed_in_attempt"],
                "cached_after_attempt": receipt["cached_after_attempt"],
                "external_process_interruption": receipt[
                    "external_process_interruption"
                ],
                **(
                    {
                        "server_command": str((path / "server/command.json").resolve()),
                        "server_command_sha256": sha256_file(path / "server/command.json"),
                        "server_log": str((path / "server/vllm.log").resolve()),
                        "server_log_sha256": sha256_file(path / "server/vllm.log"),
                    }
                    if (path / "server/command.json").is_file()
                    and (path / "server/vllm.log").is_file()
                    else {
                        "server_command": None,
                        "server_command_sha256": None,
                        "server_log": None,
                        "server_log_sha256": None,
                    }
                ),
            }
        )
    if previous != cached:
        raise ArtifactError("fixed-v5 cache differs from its attempt lineage")
    return result, len(paths) + 1


@contextmanager
def _gate_lock(output: Path):
    if output.is_symlink():
        raise ArtifactError("fixed-v5 gate output cannot be a symlink")
    output.mkdir(parents=True, exist_ok=True)
    path = output / "gate.lock"
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise ArtifactError("another process owns the fixed-v5 gate") from exc
    finally:
        os.close(descriptor)


def _gate_checks(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any], *, distinct: bool
) -> dict[str, bool]:
    target_baseline = int(baseline["complete_checkpoint_exact_count"]) + int(
        baseline["repair_checkpoint_exact_count"]
    )
    target_candidate = int(candidate["complete_checkpoint_exact_count"]) + int(
        candidate["repair_checkpoint_exact_count"]
    )
    improved_targets = sum(
        int(candidate[field]) > int(baseline[field])
        for field in ("complete_checkpoint_exact_count", "repair_checkpoint_exact_count")
    )
    criteria = _GATE_CRITERIA
    return {
        "candidate_adapter_is_distinct": distinct,
        "action_transition_improves": int(candidate["action_transition_exact_count"])
        - int(baseline["action_transition_exact_count"])
        >= criteria["minimum_action_transition_exact_count_delta"],
        "complete_plus_repair_improves": target_candidate - target_baseline
        >= criteria["minimum_complete_plus_repair_exact_count_delta"],
        "at_least_one_target_transition_improves": improved_targets
        >= criteria["minimum_strictly_improved_target_transition_count"],
        "incomplete_continue_preserved": int(baseline["incomplete_continue_exact_count"])
        - int(candidate["incomplete_continue_exact_count"])
        <= criteria["maximum_incomplete_continue_exact_count_regression"],
        "postapproval_action_preserved": int(baseline["postapproval_action_exact_count"])
        - int(candidate["postapproval_action_exact_count"])
        <= criteria["maximum_postapproval_action_exact_count_regression"],
        "contract_syntax_preserved": int(baseline["contract_syntax_valid_count"])
        - int(candidate["contract_syntax_valid_count"])
        <= criteria["maximum_contract_syntax_valid_count_regression"],
        "contract_semantics_preserved": int(baseline["contract_semantic_exact_count"])
        - int(candidate["contract_semantic_exact_count"])
        <= criteria["maximum_contract_semantic_exact_count_regression"],
        "no_extra_premature_checkpoint_or_purchase": int(
            candidate["premature_checkpoint_or_purchase_count"]
        )
        - int(baseline["premature_checkpoint_or_purchase_count"])
        <= criteria["maximum_premature_checkpoint_or_purchase_count_delta"],
    }


def _gate_locked(
    campaign: Campaign,
    *,
    campaign_root: Path,
    training_receipt_path: Path,
    baseline_selection_dir: Path,
    output: Path,
    concurrency: int,
    num_gpus: int,
    port: int,
) -> dict[str, Any]:
    contracts, checkpoints, inputs = _selection_tasks(campaign, campaign_root)
    parent, candidate, receipt = _training_inventory(
        campaign, campaign_root, training_receipt_path
    )
    system, output_model = _output_stack()
    tokenizer = _load_tokenizer(parent)
    candidate_specs = _fixed_specs(
        contracts=contracts,
        checkpoints=checkpoints,
        system=system,
        output_model=output_model,
        tokenizer=tokenizer,
    )
    baseline_rows, baseline_provenance = _baseline_evidence(
        campaign=campaign,
        root=campaign_root,
        selection_dir=baseline_selection_dir,
        contracts=contracts,
        checkpoints=checkpoints,
        system=system,
        output_model=output_model,
    )
    policy_body = _policy_body(
        campaign=campaign,
        inputs=inputs,
        training_receipt_path=training_receipt_path,
        training_receipt=receipt,
        candidate=candidate,
        baseline=baseline_provenance,
        candidate_specs=candidate_specs,
        num_gpus=num_gpus,
    )
    policy = _publish_bound_record(
        output / "gate_policy.json", policy_body, hash_field="policy_body_sha256"
    )

    manifest_path = output / "gate_manifest.json"
    cache_dir = output / "response_cache"
    cache = _load_cache(cache_dir, candidate_specs, output_model)
    attempts, next_attempt = _attempts(output, cached=len(cache))
    if not manifest_path.is_file():
        missing = [spec for spec in candidate_specs if _evidence_key(spec) not in cache]
        if missing:
            attempt_dir = output / "attempts" / f"attempt-{next_attempt:04d}"
            attempt_dir.mkdir()
            start = _publish_bound_record(
                attempt_dir / "start.json",
                {
                    "schema": FIXED_V5_GATE_ATTEMPT_SCHEMA,
                    "status": "started",
                    "attempt_id": next_attempt,
                    "cached_before_attempt": len(cache),
                    "expected_total": 96,
                    "policy_body_sha256": policy["policy_body_sha256"],
                    "process_pid": os.getpid(),
                },
                hash_field="body_sha256",
            )
            del start
            failure: BaseException | None = None
            completed = 0
            with MultiLoraServer(
                base_model=parent,
                adapters={_CANDIDATE: Path(candidate["path"])},
                output_dir=attempt_dir / "server",
                port=port,
                gpu_ids=tuple(range(num_gpus)),
                max_model_len=_NATIVE_MAX_MODEL_LEN,
            ) as server:
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    futures = {
                        pool.submit(
                            _execute_candidate_spec,
                            server.base_url,
                            spec,
                            output_model,
                            next_attempt,
                        ): spec
                        for spec in missing
                    }
                    for future in as_completed(futures):
                        spec = futures[future]
                        try:
                            row = future.result()
                            publish_json(cache_dir / _cache_name(spec), row)
                            cache[_evidence_key(spec)] = row
                            completed += 1
                        except BaseException as exc:  # drain every already-submitted request
                            if failure is None:
                                failure = exc
            status = "complete" if failure is None and len(cache) == 96 else "failed"
            _publish_bound_record(
                attempt_dir / "receipt.json",
                _attempt_body(
                    identifier=next_attempt,
                    status=status,
                    cached_before=96 - len(missing),
                    completed=completed,
                    external_interruption=False,
                    failure=failure,
                ),
                hash_field="body_sha256",
            )
            if failure is not None:
                raise ArtifactError(f"fixed-v5 gate request failed: {failure}") from failure
        if len(cache) != 96:
            raise ArtifactError("fixed-v5 gate candidate evidence is incomplete")

        candidate_rows = [cache[_evidence_key(spec)] for spec in candidate_specs]
        evidence = sorted(
            [*baseline_rows, *candidate_rows], key=lambda row: _evidence_key(row)
        )
        evidence_path = output / "raw_evidence.jsonl"
        publish_jsonl(evidence_path, evidence)
        metrics = _metrics_from_evidence(evidence, {_BASELINE, _CANDIDATE})
        distinct = candidate["tree_sha256"] != receipt.get(
            "source_step20_adapter", {}
        ).get("tree_sha256")
        checks = _gate_checks(metrics[_BASELINE], metrics[_CANDIDATE], distinct=distinct)
        attempts, _unused = _attempts(output, cached=96)
        manifest_body = {
            "schema": FIXED_V5_GATE_MANIFEST_SCHEMA,
            "status": "complete",
            "campaign_digest": campaign.digest,
            "selection_performed": False,
            "fixed_candidate": "step26",
            "amazon_data_used": False,
            "evaluation_split": "procedural_validation",
            "gate_policy": str((output / "gate_policy.json").resolve()),
            "gate_policy_sha256": sha256_file(output / "gate_policy.json"),
            "gate_policy_body_sha256": policy["policy_body_sha256"],
            "training_receipt": str(training_receipt_path.resolve()),
            "training_receipt_sha256": sha256_file(training_receipt_path),
            "candidate": candidate,
            "baseline_provenance": baseline_provenance,
            "metrics": metrics,
            "gate_checks": checks,
            "passed": all(checks.values()),
            "raw_evidence": {
                "path": str(evidence_path.resolve()),
                "sha256": sha256_file(evidence_path),
                "rows": 192,
                "baseline_rows": 96,
                "candidate_rows": 96,
            },
            "candidate_server_attempts": attempts,
            "candidate_request_execution": {
                "accepted_responses": 96,
                "accepted_response_http_attempts": 96,
                "accepted_response_retries": 0,
                "same_process_request_resubmissions": 0,
                "externally_interrupted_process_attempts": sum(
                    row["external_process_interruption"] for row in attempts
                ),
                "aggregate_zero_generation_resets_claimed": all(
                    not row["external_process_interruption"] for row in attempts
                ),
            },
        }
        return _publish_bound_record(
            manifest_path, manifest_body, hash_field="manifest_body_sha256"
        )

    manifest = _bound_record(
        manifest_path,
        schema=FIXED_V5_GATE_MANIFEST_SCHEMA,
        hash_field="manifest_body_sha256",
    )
    evidence = read_jsonl(output / "raw_evidence.jsonl")
    if len(evidence) != 192:
        raise ArtifactError("fixed-v5 published gate evidence denominator changed")
    expected_evidence = sorted(
        [
            *baseline_rows,
            *(cache[_evidence_key(spec)] for spec in candidate_specs),
        ],
        key=lambda row: _evidence_key(row),
    )
    if evidence != expected_evidence:
        raise ArtifactError("fixed-v5 published gate evidence differs from bound sources")
    metrics = _metrics_from_evidence(evidence, {_BASELINE, _CANDIDATE})
    checks = _gate_checks(
        metrics[_BASELINE],
        metrics[_CANDIDATE],
        distinct=candidate["tree_sha256"]
        != receipt.get("source_step20_adapter", {}).get("tree_sha256"),
    )
    if (
        manifest.get("gate_policy_sha256") != sha256_file(output / "gate_policy.json")
        or manifest.get("training_receipt_sha256") != sha256_file(training_receipt_path)
        or manifest.get("candidate") != candidate
        or manifest.get("metrics") != metrics
        or manifest.get("gate_checks") != checks
        or manifest.get("passed") is not all(checks.values())
        or manifest.get("raw_evidence", {}).get("sha256")
        != sha256_file(output / "raw_evidence.jsonl")
    ):
        raise ArtifactError("fixed-v5 published gate binding changed")
    return manifest


def run_fixed_v5_gate(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    training_receipt: str | Path,
    baseline_selection_dir: str | Path,
    output_dir: str | Path,
    concurrency: int = 32,
    num_gpus: int = 4,
    port: int = 8000,
) -> dict[str, Any]:
    """Run or audit the fixed step-26 procedural gate."""

    if not 1 <= concurrency <= 96:
        raise ArtifactError("fixed-v5 gate concurrency must be in [1, 96]")
    if num_gpus not in {4, 8}:
        raise ArtifactError("fixed-v5 gate requires four or eight GPUs")
    root = Path(campaign_root).resolve()
    receipt = Path(training_receipt).resolve()
    baseline = Path(baseline_selection_dir).resolve()
    output = Path(output_dir).resolve()
    with _gate_lock(output):
        return _gate_locked(
            campaign,
            campaign_root=root,
            training_receipt_path=receipt,
            baseline_selection_dir=baseline,
            output=output,
            concurrency=concurrency,
            num_gpus=num_gpus,
            port=port,
        )
