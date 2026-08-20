"""Outcome-blind native-context recovery for the failed v3 action selector.

v3 proved that its 65,536-token action ceiling bound on exactly ten frozen
requests while durably publishing 470 natural ``stop`` responses.  This module
imports every valid v3 stop response without reading its score, freezes the
exact complement, and gives only that complement one native-context attempt.
"""

from __future__ import annotations

import json
import stat
import urllib.request
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
from .browser_action_curriculum import _output_stack
from .browser_action_selection import (
    _ACTION_MAX_TOKENS,
    _COMPLETION_BUDGET,
    _HARD_GATES,
    _RANK_ORDER,
    _SELECTOR_TRANSPORT_POLICY,
    _SINGLE_ATTEMPT_EXECUTION,
    _candidate_inventory,
    _choose,
    _evidence_key,
    _expected_specs,
    _metrics_from_evidence,
    _score_action_content,
    _selection_lock,
    _selection_tasks,
)
from .config import Campaign
from .contract_refinement import _score
from .selection import MultiLoraServer, _content

ADAPTIVE_SELECTION_SCHEMA = "caveat-27b.browser-action-adaptive-selection.v1"
ADAPTIVE_EVIDENCE_SCHEMA = "caveat-27b.browser-action-adaptive-evidence.v1"
ADAPTIVE_PROVENANCE_SCHEMA = "caveat-27b.browser-action-v3-import.v1"
ADAPTIVE_ALLOWLIST_SCHEMA = "caveat-27b.browser-action-escalation-allowlist.v1"
ADAPTIVE_PROCESS_ATTEMPT_SCHEMA = "caveat-27b.browser-action-escalation-attempt.v1"

_NATIVE_MAX_MODEL_LEN = 262144
_NATIVE_CONTEXT_RESERVE_TOKENS = 1
_ADAPTIVE_REQUEST_TIMEOUT_SECONDS = 14400
_ADAPTIVE_COUNTS = {
    "imported_natural_stop_responses": 470,
    "escalated_natural_stop_responses": 10,
    "accepted_responses": 480,
    "total_generation_attempts": 490,
}
_ADAPTIVE_TRANSPORT_POLICY = {
    "request_timeout_seconds": _ADAPTIVE_REQUEST_TIMEOUT_SECONDS,
    "maximum_attempts_per_escalated_request": 1,
    "retry_count_per_escalated_request": 0,
    "client_resubmit_on_transport_failure": False,
}


def _body_hash(payload: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(payload).encode())


def _bound_record(path: Path, schema: str) -> dict[str, Any]:
    if path.is_symlink():
        raise ArtifactError(f"{schema} artifact cannot be a symlink")
    record = read_json(path)
    if not isinstance(record, dict):
        raise ArtifactError(f"{schema} artifact is not an object")
    body = dict(record)
    digest = body.pop("body_sha256", None)
    if record.get("schema") != schema or digest != _body_hash(body):
        raise ArtifactError(f"{schema} artifact binding changed")
    return record


def _safe_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ArtifactError("adaptive provenance artifact must be a regular non-symlink file")
    return sha256_file(path)


def _publish_record(path: Path, body: Mapping[str, Any]) -> dict[str, Any]:
    record = {**body, "body_sha256": _body_hash(body)}
    publish_json(path, record)
    return record


def _load_tokenizer(parent: Path) -> Any:
    try:
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(parent, local_files_only=True, trust_remote_code=False)
    except (OSError, TypeError, ValueError) as exc:
        raise ArtifactError("selected-parent tokenizer cannot be loaded offline") from exc


def _rendered_prompt_tokens(tokenizer: Any, request: Mapping[str, Any]) -> int:
    try:
        rendered = tokenizer.apply_chat_template(
            request["messages"], tokenize=True, add_generation_prompt=True
        )
        token_ids = rendered["input_ids"] if isinstance(rendered, Mapping) else rendered
        result = len(token_ids)
    except (KeyError, TypeError, ValueError) as exc:
        raise ArtifactError("frozen selector prompt cannot be tokenized exactly") from exc
    if result < 1:
        raise ArtifactError("frozen selector prompt token count is invalid")
    return result


def _natural_spec_sha(spec: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(spec["request"]).encode())


def _validate_v3_source(
    *, v3: Path, specs: list[dict[str, Any]], tokenizer: Any
) -> tuple[dict[tuple[str, str, str, str], dict[str, Any]], dict[str, Any]]:
    """Validate v3 provenance without reading or branching on cached scores."""

    if v3.is_symlink() or not v3.is_dir() or v3.name != "selection_nonbinding_v3":
        raise ArtifactError("adaptive selector requires the exact failed v3 directory")
    manifest = v3 / "selected_checkpoint.json"
    if manifest.exists() or manifest.is_symlink():
        raise ArtifactError("v3 unexpectedly published a selection manifest")
    start_path = v3 / "attempts/attempt-0001/start.json"
    receipt_path = v3 / "attempts/attempt-0001/receipt.json"
    command_path = v3 / "attempts/attempt-0001/server/command.json"
    log_path = v3 / "attempts/attempt-0001/server/vllm.log"
    # Reject symlink substitution before parsing any imported provenance.
    for path in (start_path, receipt_path, command_path, log_path):
        _safe_sha256(path)
    start = read_json(start_path)
    receipt = read_json(receipt_path)
    if not isinstance(start, dict) or not isinstance(receipt, dict):
        raise ArtifactError("v3 attempt provenance is malformed")
    start_body = dict(start)
    start_sha = start_body.pop("start_body_sha256", None)
    receipt_body = dict(receipt)
    receipt_sha = receipt_body.pop("receipt_body_sha256", None)
    if (
        start_sha != _body_hash(start_body)
        or receipt_sha != _body_hash(receipt_body)
        or start.get("status") != "started"
        or start.get("attempt_id") != 1
        or start.get("cached_before_attempt") != 0
        or start.get("expected_total") != 480
        or start.get("transport_policy") != _SELECTOR_TRANSPORT_POLICY
        or receipt.get("status") != "failed"
        or receipt.get("attempt_id") != 1
        or receipt.get("cached_before_attempt") != 0
        or receipt.get("completed_in_attempt") != 470
        or receipt.get("cached_after_attempt") != 470
        or receipt.get("expected_total") != 480
        or receipt.get("transport_policy") != _SELECTOR_TRANSPORT_POLICY
        or receipt.get("automatic_request_retries") != 0
        or receipt.get("same_process_request_resubmissions") != 0
        or receipt.get("external_process_interruption") is not False
        or receipt.get("failure")
        != {
            "type": "ArtifactError",
            "message": "selection completion ceiling bound; increase it before selection",
        }
    ):
        raise ArtifactError("v3 was not the exact fail-closed length-bound attempt")

    expected = {_evidence_key(spec): spec for spec in specs}
    cache_dir = v3 / "response_cache"
    if cache_dir.is_symlink() or not cache_dir.is_dir():
        raise ArtifactError("v3 response cache is unsafe")
    imported: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    files: list[dict[str, Any]] = []
    for path in sorted(cache_dir.iterdir()):
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode) or path.suffix != ".json":
            raise ArtifactError("v3 response cache contains an unsafe entry")
        row = read_json(path)
        if not isinstance(row, dict):
            raise ArtifactError("v3 cache row is not an object")
        key = _evidence_key(row)
        spec = expected.get(key)
        response = row.get("response")
        try:
            finish_reason = response["choices"][0].get("finish_reason")
            content = _content(response)
            prompt_tokens = response["usage"]["prompt_tokens"]
        except (ArtifactError, KeyError, IndexError, TypeError) as exc:
            raise ArtifactError("v3 cache response identity is malformed") from exc
        if (
            spec is None
            or key in imported
            or row.get("request") != spec["request"]
            or row.get("request_sha256") != _natural_spec_sha(spec)
            or row.get("response_sha256") != sha256_bytes(canonical_json(response).encode())
            or row.get("assistant_content") != content
            or row.get("finish_reason") != "stop"
            or finish_reason != "stop"
            or row.get("completion_ceiling_bound") is not False
            or row.get("completion_budget") != _COMPLETION_BUDGET
            or row.get("request_execution") != _SINGLE_ATTEMPT_EXECUTION
            or row.get("selector_process_attempt_id") != 1
            or prompt_tokens != _rendered_prompt_tokens(tokenizer, spec["request"])
        ):
            raise ArtifactError("v3 cache identity/provenance binding changed")
        # Deliberately do not read ``row['scoring']`` here.  Scores are
        # independently recomputed only after the accepted 480-row union is fixed.
        imported[key] = row
        files.append(
            {
                "name": path.name,
                "sha256": _safe_sha256(path),
                "evidence_key": list(key),
                "natural_request_sha256": _natural_spec_sha(spec),
                "response_sha256": row["response_sha256"],
            }
        )
    if len(imported) != 470:
        raise ArtifactError("v3 import denominator must be exactly 470")
    command = read_json(command_path)
    if (
        not isinstance(command, list)
        or "--max-model-len" not in command
        or command[command.index("--max-model-len") + 1] != "131072"
    ):
        raise ArtifactError("v3 server command did not use its frozen context")
    provenance = {
        "schema": ADAPTIVE_PROVENANCE_SCHEMA,
        "source_directory": str(v3.resolve()),
        "source_start": str(start_path.resolve()),
        "source_start_sha256": _safe_sha256(start_path),
        "source_failed_receipt": str(receipt_path.resolve()),
        "source_failed_receipt_sha256": _safe_sha256(receipt_path),
        "source_server_command": str(command_path.resolve()),
        "source_server_command_sha256": _safe_sha256(command_path),
        "source_server_log": str(log_path.resolve()),
        "source_server_log_sha256": _safe_sha256(log_path),
        "natural_context": _COMPLETION_BUDGET,
        "imported_rows": len(imported),
        "import_rule": "all_hash_valid_finish_reason_stop_rows_without_reading_scores",
        "files": files,
    }
    return imported, provenance


def _allowlist(
    *, specs: list[dict[str, Any]], imported: Mapping[Any, Any], tokenizer: Any
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    missing = [spec for spec in specs if _evidence_key(spec) not in imported]
    if len(missing) != 10 or any(spec.get("kind") != "browser_action" for spec in missing):
        raise ArtifactError("v3 objective complement is not the expected ten action requests")
    rows = []
    escalated = []
    for spec in sorted(missing, key=_evidence_key):
        prompt_tokens = _rendered_prompt_tokens(tokenizer, spec["request"])
        max_tokens = _NATIVE_MAX_MODEL_LEN - prompt_tokens - _NATIVE_CONTEXT_RESERVE_TOKENS
        if max_tokens <= _ACTION_MAX_TOKENS:
            raise ArtifactError("native escalation does not enlarge the natural ceiling")
        request = dict(spec["request"])
        request["max_tokens"] = max_tokens
        escalated_spec = {**spec, "request": request}
        escalated.append(escalated_spec)
        rows.append(
            {
                "evidence_key": list(_evidence_key(spec)),
                "natural_request_sha256": _natural_spec_sha(spec),
                "natural_max_tokens": spec["request"]["max_tokens"],
                "rendered_prompt_tokens": prompt_tokens,
                "native_max_model_len": _NATIVE_MAX_MODEL_LEN,
                "native_context_reserve_tokens": _NATIVE_CONTEXT_RESERVE_TOKENS,
                "escalated_max_tokens": max_tokens,
                "escalated_request_sha256": sha256_bytes(canonical_json(request).encode()),
            }
        )
    body = {
        "schema": ADAPTIVE_ALLOWLIST_SCHEMA,
        "derivation": "exact_frozen_request_inventory_minus_all_valid_v3_stop_rows",
        "outcome_fields_read_before_freeze": [
            "identity",
            "request",
            "response_hash",
            "finish_reason",
            "provenance",
        ],
        "score_fields_read_before_freeze": [],
        "rows": rows,
    }
    return escalated, body


def _adaptive_post(base_url: str, request_body: Mapping[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        method="POST",
        data=json.dumps(request_body).encode(),
        headers={"content-type": "application/json", "authorization": "Bearer EMPTY"},
    )
    with urllib.request.urlopen(  # noqa: S310
        request, timeout=_ADAPTIVE_REQUEST_TIMEOUT_SECONDS
    ) as response:
        result = json.loads(response.read().decode())
    if not isinstance(result, dict):
        raise ArtifactError("adaptive selection response is not an object")
    return result


def _score_response(
    *, spec: Mapping[str, Any], content: str | None, output_model: type[Any]
) -> dict[str, Any]:
    if spec["kind"] == "contract":
        if content is None:
            syntax, semantic, normalized, error = False, False, None, "empty assistant content"
        else:
            syntax, semantic, normalized, error = _score(
                str(spec["instruction"]), content, spec["gold_contract"]
            )
        return {
            "syntax_valid": syntax,
            "semantic_exact": semantic,
            "normalized_contract": normalized,
            "error": error,
        }
    return _score_action_content(
        content,
        output_model=output_model,
        transition=str(spec["transition"]),
        expected_action=spec["expected_action"],
        approved_action=spec["approved_action"],
    )


def _execute_escalated(
    base_url: str,
    spec: Mapping[str, Any],
    output_model: type[Any],
    expected_prompt_tokens: int,
    process_attempt_id: int = 1,
) -> dict[str, Any]:
    request = dict(spec["request"])
    response = _adaptive_post(base_url, request)
    try:
        finish_reason = response["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError) as exc:
        raise ArtifactError("adaptive response has no finish reason") from exc
    if finish_reason == "length":
        raise ArtifactError("native context ceiling bound during adaptive selection")
    if finish_reason != "stop":
        raise ArtifactError("adaptive response did not finish naturally")
    content = _content(response)
    public = {key: value for key, value in spec.items() if key != "request"}
    natural = dict(request)
    natural["max_tokens"] = _ACTION_MAX_TOKENS
    prompt_tokens = int(response.get("usage", {}).get("prompt_tokens", -1))
    expected_max_tokens = (
        _NATIVE_MAX_MODEL_LEN - expected_prompt_tokens - _NATIVE_CONTEXT_RESERVE_TOKENS
    )
    if prompt_tokens != expected_prompt_tokens or request.get("max_tokens") != expected_max_tokens:
        raise ArtifactError("adaptive runtime prompt-token budget differs from its allowlist")
    budget = {
        "tier": "native_context_escalation",
        "native_max_model_len": _NATIVE_MAX_MODEL_LEN,
        "rendered_prompt_tokens": prompt_tokens,
        "native_context_reserve_tokens": _NATIVE_CONTEXT_RESERVE_TOKENS,
        "max_tokens": request["max_tokens"],
        "request_transport": _ADAPTIVE_TRANSPORT_POLICY,
    }
    return {
        "schema": ADAPTIVE_EVIDENCE_SCHEMA,
        **public,
        "provenance_tier": "native_context_escalation",
        "natural_request_sha256": sha256_bytes(canonical_json(natural).encode()),
        "request": request,
        "request_sha256": sha256_bytes(canonical_json(request).encode()),
        "response": response,
        "response_sha256": sha256_bytes(canonical_json(response).encode()),
        "assistant_content": content,
        "finish_reason": finish_reason,
        "completion_ceiling_bound": False,
        "completion_budget": budget,
        "request_execution": _SINGLE_ATTEMPT_EXECUTION,
        "adaptive_process_attempt_id": process_attempt_id,
        "scoring": _score_response(spec=spec, content=content, output_model=output_model),
    }


def _imported_evidence(
    *, row: Mapping[str, Any], spec: Mapping[str, Any], output_model: type[Any]
) -> dict[str, Any]:
    content = row.get("assistant_content")
    public = {key: value for key, value in spec.items() if key != "request"}
    return {
        "schema": ADAPTIVE_EVIDENCE_SCHEMA,
        **public,
        "provenance_tier": "v3_natural_stop_import",
        "natural_request_sha256": _natural_spec_sha(spec),
        "request": row["request"],
        "request_sha256": row["request_sha256"],
        "response": row["response"],
        "response_sha256": row["response_sha256"],
        "assistant_content": content,
        "finish_reason": "stop",
        "completion_ceiling_bound": False,
        "completion_budget": {"tier": "v3_natural", **_COMPLETION_BUDGET},
        "request_execution": _SINGLE_ATTEMPT_EXECUTION,
        "adaptive_process_attempt_id": 0,
        "scoring": _score_response(spec=spec, content=content, output_model=output_model),
    }


def _adaptive_cache_name(row: Mapping[str, Any]) -> str:
    identity = {
        "evidence_key": list(_evidence_key(row)),
        "provenance_tier": row.get("provenance_tier"),
        "natural_request_sha256": row.get("natural_request_sha256"),
        "request_sha256": row.get("request_sha256"),
    }
    return sha256_bytes(canonical_json(identity).encode()) + ".json"


def _adaptive_cache_inventory(cache: Path, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    if cache.is_symlink() or not cache.is_dir():
        raise ArtifactError("adaptive response cache is unsafe")
    if any(
        path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode) or path.suffix != ".json"
        for path in cache.iterdir()
    ):
        raise ArtifactError("adaptive response cache contains an unsafe entry")
    expected = sorted((_adaptive_cache_name(row), row) for row in evidence)
    files = sorted(path.name for path in cache.iterdir())
    if files != [name for name, _row in expected]:
        raise ArtifactError("adaptive response cache inventory drifted")
    for name, row in expected:
        if read_json(cache / name) != row:
            raise ArtifactError("adaptive cached response differs from accepted evidence")
    return {
        "directory": str(cache.resolve()),
        "rows": len(expected),
        "files": [
            {
                "name": name,
                "sha256": _safe_sha256(cache / name),
                "evidence_key": list(_evidence_key(row)),
                "provenance_tier": row["provenance_tier"],
                "request_sha256": row["request_sha256"],
                "response_sha256": row["response_sha256"],
                "adaptive_process_attempt_id": row["adaptive_process_attempt_id"],
            }
            for name, row in expected
        ],
    }


def _attempt_record(path: Path, status: str) -> dict[str, Any]:
    record = _bound_record(path, ADAPTIVE_PROCESS_ATTEMPT_SCHEMA)
    if record.get("status") != status:
        raise ArtifactError("adaptive process-attempt status drifted")
    return record


def _prepare_process_attempts(
    output: Path,
    completed: list[dict[str, Any]],
    allowed_keys: set[tuple[str, ...]],
    *,
    recover_interrupted: bool = False,
) -> list[dict[str, Any]]:
    root = output / "escalation_attempts"
    if root.is_symlink():
        raise ArtifactError("adaptive attempt directory is unsafe")
    root.mkdir(exist_ok=True)
    paths = sorted(root.iterdir())
    result: list[dict[str, Any]] = []
    for identifier, path in enumerate(paths, 1):
        if path.is_symlink() or not path.is_dir() or path.name != f"attempt-{identifier:04d}":
            raise ArtifactError("adaptive attempt inventory is unsafe")
        start_path = path / "start.json"
        start = _attempt_record(start_path, "started")
        submitted_rows = start.get("submitted_keys")
        if (
            start.get("attempt_id") != identifier
            or not isinstance(submitted_rows, list)
            or len(submitted_rows) != start.get("submitted")
            or len({tuple(row) for row in submitted_rows}) != len(submitted_rows)
            or not {tuple(row) for row in submitted_rows}.issubset(allowed_keys)
        ):
            raise ArtifactError("adaptive attempt start binding drifted")
        accepted = sum(row.get("adaptive_process_attempt_id") == identifier for row in completed)
        receipt_path = path / "receipt.json"
        if receipt_path.exists() or receipt_path.is_symlink():
            receipt = _bound_record(receipt_path, ADAPTIVE_PROCESS_ATTEMPT_SCHEMA)
            if receipt.get("status") == "failed":
                raise ArtifactError("prior adaptive attempt failed; output is fail-closed")
        else:
            if not recover_interrupted:
                raise ArtifactError("adaptive attempt has no terminal receipt")
            if identifier != len(paths):
                raise ArtifactError("only latest adaptive attempt may be externally interrupted")
            receipt = _publish_record(
                receipt_path,
                {
                    "schema": ADAPTIVE_PROCESS_ATTEMPT_SCHEMA,
                    "status": "externally_interrupted",
                    "attempt_id": identifier,
                    "submitted": len(submitted_rows),
                    "accepted": accepted,
                    "automatic_request_retries": 0,
                    "same_attempt_request_resubmissions": 0,
                    "possible_abandoned_inflight_generations": True,
                },
            )
        if (
            receipt.get("attempt_id") != identifier
            or receipt.get("submitted") != len(submitted_rows)
            or receipt.get("accepted") != accepted
            or receipt.get("automatic_request_retries") != 0
            or receipt.get("same_attempt_request_resubmissions") != 0
            or receipt.get("status") not in {"complete", "externally_interrupted"}
        ):
            raise ArtifactError("adaptive attempt receipt binding drifted")
        if receipt.get("status") == "complete" and receipt.get("accepted") != len(submitted_rows):
            raise ArtifactError("complete adaptive attempt did not accept all submissions")
        server = path / "server"
        descriptor = {
            "attempt_id": identifier,
            "status": receipt["status"],
            "submitted": len(submitted_rows),
            "accepted": accepted,
            "start": str(start_path.resolve()),
            "start_sha256": _safe_sha256(start_path),
            "receipt": str(receipt_path.resolve()),
            "receipt_sha256": _safe_sha256(receipt_path),
            "server_command": None,
            "server_command_sha256": None,
            "server_log": None,
            "server_log_sha256": None,
        }
        for name in ("command.json", "vllm.log"):
            artifact = server / name
            if artifact.is_file() and not artifact.is_symlink():
                field = "server_command" if name == "command.json" else "server_log"
                descriptor[field] = str(artifact.resolve())
                descriptor[f"{field}_sha256"] = _safe_sha256(artifact)
        if receipt["status"] == "complete" and (
            descriptor["server_command"] is None or descriptor["server_log"] is None
        ):
            raise ArtifactError("complete adaptive attempt has incomplete server provenance")
        result.append(descriptor)
    return result


def _new_process_attempt(
    output: Path, missing: list[dict[str, Any]], prior: list[dict[str, Any]]
) -> tuple[int, Path, Path]:
    identifier = len(prior) + 1
    attempt = output / "escalation_attempts" / f"attempt-{identifier:04d}"
    attempt.mkdir()
    _publish_record(
        attempt / "start.json",
        {
            "schema": ADAPTIVE_PROCESS_ATTEMPT_SCHEMA,
            "status": "started",
            "attempt_id": identifier,
            "submitted": len(missing),
            "submitted_keys": [list(_evidence_key(spec)) for spec in missing],
            "transport_policy": _ADAPTIVE_TRANSPORT_POLICY,
        },
    )
    return identifier, attempt / "server", attempt / "receipt.json"


def _adaptive_summary(
    evidence: list[dict[str, Any]], *, attempts: list[dict[str, Any]]
) -> dict[str, Any]:
    tiers = {
        tier: sum(row["provenance_tier"] == tier for row in evidence)
        for tier in {"v3_natural_stop_import", "native_context_escalation"}
    }
    if tiers != {"v3_natural_stop_import": 470, "native_context_escalation": 10}:
        raise ArtifactError("adaptive evidence tier denominators drifted")
    interrupted = sum(row["status"] == "externally_interrupted" for row in attempts)
    submitted = sum(int(row["submitted"]) for row in attempts)
    exact_total = 480 + submitted if interrupted == 0 else None
    return {
        **{
            key: value
            for key, value in _ADAPTIVE_COUNTS.items()
            if key != "total_generation_attempts"
        },
        "total_generation_attempts": exact_total,
        "generation_attempts_lower_bound": 490,
        "maximum_client_request_submissions": 480 + submitted,
        "accepted_by_tier": tiers,
        "first_tier_unaccepted_or_censored_responses": 10,
        "confirmed_first_tier_length_bound_responses_at_least": 1,
        "adaptive_escalation_attempts": 10,
        "automatic_request_retries": 0,
        "same_attempt_request_resubmissions": 0,
        "homogeneous_completion_budget": False,
        "aggregate_zero_generation_resets_claimed": False,
        "outcome_blind_escalation": True,
        "adaptive_process_attempts": len(attempts),
        "externally_interrupted_adaptive_process_attempts": interrupted,
        "possible_abandoned_adaptive_generations": interrupted > 0,
    }


def _validate_adaptive_selection_artifacts(
    selection_dir: Path,
    manifest: Mapping[str, Any],
    specs: list[dict[str, Any]],
    output_model: type[Any],
) -> dict[str, Any]:
    """Recompute all v4 provenance and return its canonical execution summary."""

    evidence = read_jsonl(selection_dir / "raw_evidence.jsonl")
    expected = {_evidence_key(spec): spec for spec in specs}
    allowlist = _bound_record(
        selection_dir / "escalation_allowlist.json", ADAPTIVE_ALLOWLIST_SCHEMA
    )
    allowlist_rows = allowlist.get("rows")
    if not isinstance(allowlist_rows, list):
        raise ArtifactError("adaptive escalation allowlist is malformed")
    allowed = {
        tuple(row.get("evidence_key", [])): row
        for row in allowlist_rows
        if isinstance(row, Mapping)
    }
    if (
        len(allowed) != 10
        or allowlist.get("score_fields_read_before_freeze") != []
        or allowlist.get("derivation")
        != "exact_frozen_request_inventory_minus_all_valid_v3_stop_rows"
    ):
        raise ArtifactError("adaptive escalation allowlist binding drifted")
    if len(evidence) != 480 or {_evidence_key(row) for row in evidence} != set(expected):
        raise ArtifactError("adaptive evidence inventory drifted")
    for row in evidence:
        key = _evidence_key(row)
        spec = expected[key]
        response = row.get("response")
        try:
            content = _content(response) if isinstance(response, Mapping) else None
            response_finish = response["choices"][0].get("finish_reason")
        except (ArtifactError, KeyError, IndexError, TypeError) as exc:
            raise ArtifactError("adaptive evidence response is malformed") from exc
        tier = row.get("provenance_tier")
        request = row.get("request")
        budget = row.get("completion_budget")
        tier_valid = False
        if tier == "v3_natural_stop_import":
            tier_valid = (
                key not in allowed
                and request == spec["request"]
                and row.get("request_sha256") == _natural_spec_sha(spec)
                and budget == {"tier": "v3_natural", **_COMPLETION_BUDGET}
                and row.get("adaptive_process_attempt_id") == 0
            )
        else:
            entry = allowed.get(key)
            natural = _natural_spec_sha(spec)
            tier_valid = (
                isinstance(entry, Mapping)
                and isinstance(request, Mapping)
                and request.get("max_tokens") == entry.get("escalated_max_tokens")
                and row.get("request_sha256") == entry.get("escalated_request_sha256")
                and entry.get("natural_request_sha256") == natural
                and isinstance(budget, Mapping)
                and budget.get("tier") == "native_context_escalation"
                and budget.get("native_max_model_len") == _NATIVE_MAX_MODEL_LEN
                and budget.get("rendered_prompt_tokens") == entry.get("rendered_prompt_tokens")
                and budget.get("native_context_reserve_tokens") == _NATIVE_CONTEXT_RESERVE_TOKENS
                and budget.get("max_tokens") == entry.get("escalated_max_tokens")
                and budget.get("request_transport") == _ADAPTIVE_TRANSPORT_POLICY
                and type(row.get("adaptive_process_attempt_id")) is int
                and row["adaptive_process_attempt_id"] >= 1
            )
        if (
            row.get("schema") != ADAPTIVE_EVIDENCE_SCHEMA
            or tier not in {"v3_natural_stop_import", "native_context_escalation"}
            or not tier_valid
            or row.get("natural_request_sha256") != _natural_spec_sha(spec)
            or row.get("response_sha256") != sha256_bytes(canonical_json(response).encode())
            or row.get("assistant_content") != content
            or row.get("finish_reason") != "stop"
            or response_finish != "stop"
            or row.get("completion_ceiling_bound") is not False
            or row.get("request_execution") != _SINGLE_ATTEMPT_EXECUTION
            or row.get("scoring")
            != _score_response(spec=spec, content=content, output_model=output_model)
        ):
            raise ArtifactError("adaptive evidence binding drifted")
    allowed_keys = {tuple(row["evidence_key"]) for row in allowlist_rows}
    escalated_rows = [
        row for row in evidence if row.get("provenance_tier") == "native_context_escalation"
    ]
    attempts = _prepare_process_attempts(selection_dir, escalated_rows, allowed_keys)
    summary = _adaptive_summary(evidence, attempts=attempts)
    if manifest.get("adaptive_execution") != summary:
        raise ArtifactError("adaptive execution summary drifted")
    if manifest.get("response_cache") != _adaptive_cache_inventory(
        selection_dir / "response_cache", evidence
    ):
        raise ArtifactError("adaptive response cache binding drifted")
    for field, name, schema in (
        ("v3_import", "v3_import_provenance.json", ADAPTIVE_PROVENANCE_SCHEMA),
        ("escalation_allowlist", "escalation_allowlist.json", ADAPTIVE_ALLOWLIST_SCHEMA),
    ):
        record = _bound_record(selection_dir / name, schema)
        descriptor = manifest.get(field)
        if not isinstance(descriptor, Mapping) or descriptor != {
            "path": str((selection_dir / name).resolve()),
            "sha256": _safe_sha256(selection_dir / name),
            "body_sha256": record["body_sha256"],
        }:
            raise ArtifactError(f"adaptive {field} descriptor drifted")
    provenance = _bound_record(
        selection_dir / "v3_import_provenance.json", ADAPTIVE_PROVENANCE_SCHEMA
    )
    source_files = [
        ("source_start", "source_start_sha256"),
        ("source_failed_receipt", "source_failed_receipt_sha256"),
        ("source_server_command", "source_server_command_sha256"),
        ("source_server_log", "source_server_log_sha256"),
    ]
    if provenance.get("imported_rows") != 470 or not isinstance(provenance.get("files"), list):
        raise ArtifactError("adaptive v3 import provenance denominator drifted")
    for path_field, hash_field in source_files:
        path = Path(str(provenance.get(path_field, "")))
        if _safe_sha256(path) != provenance.get(hash_field):
            raise ArtifactError("adaptive v3 source provenance hash changed")
    source_command = read_json(Path(str(provenance["source_server_command"])))
    inventory = manifest.get("candidate_inventory")
    if not isinstance(source_command, list) or not isinstance(inventory, Mapping):
        raise ArtifactError("adaptive v3 server command provenance is malformed")
    try:
        served_model = source_command[source_command.index("--model") + 1]
        context = source_command[source_command.index("--max-model-len") + 1]
        modules = source_command[source_command.index("--lora-modules") + 1 :]
    except (ValueError, IndexError) as exc:
        raise ArtifactError("adaptive v3 server command flags drifted") from exc
    expected_modules = sorted(
        f"{name}={descriptor['path']}"
        for name, descriptor in inventory.items()
        if isinstance(descriptor, Mapping)
    )
    if (
        served_model != manifest.get("base_model")
        or context != "131072"
        or modules != expected_modules
    ):
        raise ArtifactError("adaptive v3 server command model/adapters drifted")
    source_cache = Path(str(provenance.get("source_directory", ""))) / "response_cache"
    source_descriptors = provenance["files"]
    if source_cache.is_symlink() or not source_cache.is_dir() or len(source_descriptors) != 470:
        raise ArtifactError("adaptive v3 source cache denominator drifted")
    if any(
        path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode) or path.suffix != ".json"
        for path in source_cache.iterdir()
    ):
        raise ArtifactError("adaptive v3 source cache inventory is unsafe")
    source_names = sorted(path.name for path in source_cache.iterdir())
    described_names = sorted(
        descriptor.get("name")
        for descriptor in source_descriptors
        if isinstance(descriptor, Mapping)
    )
    if source_names != described_names:
        raise ArtifactError("adaptive v3 source cache inventory changed")
    for descriptor in source_descriptors:
        if not isinstance(descriptor, Mapping) or _safe_sha256(
            source_cache / str(descriptor.get("name", ""))
        ) != descriptor.get("sha256"):
            raise ArtifactError("adaptive v3 source cache hash changed")
    if manifest.get("escalation_attempts") != attempts or not attempts:
        raise ArtifactError("adaptive escalation attempt inventory drifted")
    for attempt in attempts:
        command_path = attempt.get("server_command")
        log_path = attempt.get("server_log")
        if command_path is None:
            if attempt.get("status") != "externally_interrupted":
                raise ArtifactError("completed adaptive attempt has no server command")
            continue
        command_file = Path(str(command_path))
        if (
            _safe_sha256(command_file) != attempt.get("server_command_sha256")
            or log_path is None
            or _safe_sha256(Path(str(log_path))) != attempt.get("server_log_sha256")
        ):
            raise ArtifactError("adaptive server provenance hash changed")
        command = read_json(command_file)
        if not isinstance(command, list):
            raise ArtifactError("adaptive server command is malformed")
        try:
            adaptive_model = command[command.index("--model") + 1]
            adaptive_context = command[command.index("--max-model-len") + 1]
            adaptive_gpus = command[command.index("--data-parallel-size") + 1]
            adaptive_modules = command[command.index("--lora-modules") + 1 :]
        except (ValueError, IndexError) as exc:
            raise ArtifactError("adaptive server command flags drifted") from exc
        if (
            adaptive_model != manifest.get("base_model")
            or adaptive_context != str(_NATIVE_MAX_MODEL_LEN)
            or adaptive_gpus != str(manifest.get("num_gpus"))
            or adaptive_modules != expected_modules
        ):
            raise ArtifactError("adaptive server command model/context/adapters drifted")
    return summary


def select_browser_action_checkpoint_v4(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    continuation_dir: str | Path,
    v3_selection_dir: str | Path,
    output_dir: str | Path,
    concurrency: int = 10,
    num_gpus: int = 4,
    port: int = 8000,
) -> dict[str, Any]:
    if not 1 <= concurrency <= 10 or num_gpus not in {4, 8}:
        raise ArtifactError("adaptive selector concurrency/topology is invalid")
    root = Path(campaign_root).resolve()
    output = Path(output_dir)
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        raise ArtifactError("adaptive selector output is unsafe")
    output = output.resolve()
    with _selection_lock(output):
        contracts, checkpoints, inputs = _selection_tasks(campaign, root)
        parent, paths, inventory, bindings = _candidate_inventory(
            campaign, root, Path(continuation_dir).resolve()
        )
        system, output_model = _output_stack()
        specs = _expected_specs(
            candidate_names=set(paths),
            contracts=contracts,
            checkpoints=checkpoints,
            system=system,
            output_model=output_model,
        )
        tokenizer = _load_tokenizer(parent)
        imported, provenance = _validate_v3_source(
            v3=Path(v3_selection_dir).resolve(), specs=specs, tokenizer=tokenizer
        )
        escalated_specs, allowlist = _allowlist(specs=specs, imported=imported, tokenizer=tokenizer)
        provenance_record = _publish_record(output / "v3_import_provenance.json", provenance)
        allowlist_record = _publish_record(output / "escalation_allowlist.json", allowlist)
        manifest_path = output / "selected_checkpoint.json"
        if manifest_path.is_file():
            manifest = read_json(manifest_path)
            if not isinstance(manifest, dict):
                raise ArtifactError("adaptive selection manifest is malformed")
            body = dict(manifest)
            digest = body.pop("manifest_body_sha256", None)
            if digest != _body_hash(body):
                raise ArtifactError("adaptive selection manifest body changed")
            _validate_adaptive_selection_artifacts(output, manifest, specs, output_model)
            return manifest
        cache = output / "response_cache"
        cache.mkdir(exist_ok=True)
        by_spec = {_evidence_key(spec): spec for spec in specs}
        accepted = [
            _imported_evidence(row=row, spec=by_spec[key], output_model=output_model)
            for key, row in imported.items()
        ]
        for row in accepted:
            publish_json(cache / _adaptive_cache_name(row), row)

        cached_rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        for path in cache.iterdir():
            row = read_json(path)
            if not isinstance(row, dict):
                raise ArtifactError("adaptive cache row is malformed")
            if path.name != _adaptive_cache_name(row):
                raise ArtifactError("adaptive cache filename binding drifted")
            key = _evidence_key(row)
            if key in cached_rows:
                raise ArtifactError("adaptive cache has a duplicate key")
            cached_rows[key] = row
        completed: list[dict[str, Any]] = [
            row for key, row in cached_rows.items() if key not in imported
        ]
        allowed_keys = {tuple(row["evidence_key"]) for row in allowlist["rows"]}
        allowlist_by_key = {tuple(row["evidence_key"]): row for row in allowlist["rows"]}
        for row in completed:
            key = _evidence_key(row)
            entry = allowlist_by_key.get(key)
            response = row.get("response")
            try:
                content = _content(response) if isinstance(response, Mapping) else None
                response_finish = response["choices"][0].get("finish_reason")
            except (ArtifactError, KeyError, IndexError, TypeError) as exc:
                raise ArtifactError("cached adaptive response is malformed") from exc
            spec = by_spec.get(key)
            request = row.get("request")
            budget = row.get("completion_budget")
            if (
                not isinstance(entry, Mapping)
                or not isinstance(spec, Mapping)
                or row.get("schema") != ADAPTIVE_EVIDENCE_SCHEMA
                or row.get("provenance_tier") != "native_context_escalation"
                or row.get("natural_request_sha256") != entry.get("natural_request_sha256")
                or row.get("request_sha256") != entry.get("escalated_request_sha256")
                or row.get("response_sha256") != sha256_bytes(canonical_json(response).encode())
                or row.get("assistant_content") != content
                or row.get("finish_reason") != "stop"
                or response_finish != "stop"
                or row.get("completion_ceiling_bound") is not False
                or not isinstance(request, Mapping)
                or request.get("max_tokens") != entry.get("escalated_max_tokens")
                or not isinstance(budget, Mapping)
                or budget.get("tier") != "native_context_escalation"
                or budget.get("rendered_prompt_tokens") != entry.get("rendered_prompt_tokens")
                or budget.get("max_tokens") != entry.get("escalated_max_tokens")
                or budget.get("request_transport") != _ADAPTIVE_TRANSPORT_POLICY
                or row.get("request_execution") != _SINGLE_ATTEMPT_EXECUTION
                or row.get("scoring")
                != _score_response(spec=spec, content=content, output_model=output_model)
                or type(row.get("adaptive_process_attempt_id")) is not int
                or row["adaptive_process_attempt_id"] < 1
            ):
                raise ArtifactError("cached adaptive response binding drifted")
        prior_attempts = _prepare_process_attempts(
            output, completed, allowed_keys, recover_interrupted=True
        )
        completed_keys = {_evidence_key(row) for row in completed}
        missing = [spec for spec in escalated_specs if _evidence_key(spec) not in completed_keys]
        if not missing:
            evidence = sorted(accepted + completed, key=_evidence_key)
            if len(evidence) != 480:
                raise ArtifactError("adaptive cache is complete at an invalid denominator")
        else:
            evidence = []
        failure: BaseException | None = None
        attempt_id = 0
        server_dir = output / "unused"
        attempt_receipt = output / "unused-receipt.json"
        if missing:
            attempt_id, server_dir, attempt_receipt = _new_process_attempt(
                output, missing, prior_attempts
            )
            try:
                with MultiLoraServer(
                    base_model=parent,
                    adapters=paths,
                    output_dir=server_dir,
                    port=port,
                    gpu_ids=tuple(range(num_gpus)),
                    max_model_len=_NATIVE_MAX_MODEL_LEN,
                ) as server:
                    with ThreadPoolExecutor(max_workers=concurrency) as pool:
                        futures = {
                            pool.submit(
                                _execute_escalated,
                                server.base_url,
                                spec,
                                output_model,
                                allowlist_by_key[_evidence_key(spec)]["rendered_prompt_tokens"],
                                attempt_id,
                            ): spec
                            for spec in missing
                        }
                        for future in as_completed(futures):
                            try:
                                row = future.result()
                            except BaseException as exc:
                                failure = failure or exc
                                continue
                            publish_json(cache / _adaptive_cache_name(row), row)
                            completed.append(row)
            except BaseException as exc:
                failure = failure or exc
            receipt_body: dict[str, Any] = {
                "schema": ADAPTIVE_PROCESS_ATTEMPT_SCHEMA,
                "status": "complete" if failure is None else "failed",
                "attempt_id": attempt_id,
                "submitted": len(missing),
                "accepted": sum(
                    row.get("adaptive_process_attempt_id") == attempt_id for row in completed
                ),
                "automatic_request_retries": 0,
                "same_attempt_request_resubmissions": 0,
                "possible_abandoned_inflight_generations": False,
            }
            if failure is not None:
                receipt_body["failure"] = {
                    "type": type(failure).__name__,
                    "message": str(failure),
                }
            _publish_record(attempt_receipt, receipt_body)
            if failure is not None:
                raise ArtifactError("adaptive escalation failed closed") from failure

            evidence = sorted(accepted + completed, key=_evidence_key)
        attempts = _prepare_process_attempts(output, completed, allowed_keys)
        publish_jsonl(output / "raw_evidence.jsonl", evidence)
        metrics = _metrics_from_evidence(evidence, set(paths))
        selected, reason = _choose(metrics)

        def descriptor(name: str, record: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "path": str((output / name).resolve()),
                "sha256": sha256_file(output / name),
                "body_sha256": record["body_sha256"],
            }

        body = {
            "schema": ADAPTIVE_SELECTION_SCHEMA,
            "status": "complete",
            "campaign_digest": campaign.digest,
            "selection_split": "procedural_validation",
            "caveat_shop_data_used": False,
            "inputs": inputs,
            "training_bindings": bindings,
            "base_model": str(parent),
            "num_gpus": num_gpus,
            "candidate_count": len(inventory),
            "candidate_inventory": inventory,
            "hard_gates": _HARD_GATES,
            "rank_order": list(_RANK_ORDER),
            "passing_candidates": sorted(
                name for name, value in metrics.items() if value["passes_hard_gates"]
            ),
            "candidates": metrics,
            "selected": {
                "name": selected,
                "update": int(selected.removeprefix("step")),
                "adapter": inventory[selected]["path"],
                "adapter_tree_sha256": inventory[selected]["tree_sha256"],
                "reason": reason,
                "metrics": metrics[selected],
            },
            "fallback": "step20",
            "raw_evidence": {
                "path": str((output / "raw_evidence.jsonl").resolve()),
                "sha256": sha256_file(output / "raw_evidence.jsonl"),
                "rows": 480,
            },
            "request_counts_per_candidate": {
                "contract": 64,
                "continue-incomplete": 8,
                "checkpoint-complete": 8,
                "repair-rejected": 8,
                "act-after-approval": 8,
                "total": 96,
            },
            "v3_import": descriptor("v3_import_provenance.json", provenance_record),
            "escalation_allowlist": descriptor("escalation_allowlist.json", allowlist_record),
            "escalation_attempts": attempts,
            "response_cache": _adaptive_cache_inventory(cache, evidence),
            "adaptive_execution": _adaptive_summary(evidence, attempts=attempts),
            "accepted_response_completion_ceiling_bound": False,
            "first_tier_completion_ceiling_bound_observed": True,
        }
        manifest = {**body, "manifest_body_sha256": _body_hash(body)}
        _validate_adaptive_selection_artifacts(output, manifest, specs, output_model)
        publish_json(output / "selected_checkpoint.json", manifest)
        return manifest
