"""Static heldout next-action gate for the campaign-2 candidate.

This development-only probe sends the 24 already-sealed holdout request
states directly to the candidate chat endpoint.  It never launches a browser,
opens a training row, or participates in the sealed-r4 evaluation.  The raw
responses and the recomputable exact/semantic action comparisons are written
create-only and bound to both the endpoint receipt and heldout manifest.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
from collections import Counter, defaultdict
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
from .interactive_sol_dagger_candidate_serve import (
    ALLOWED_SOURCE_ROOTS,
    COLLECTION_PROVENANCE,
    LOCAL_BASE_URL,
    _is_within,
    _safe_regular,
    _self_hash,
    validate_endpoint,
)

HELDOUT_SCHEMA = "harness-distill.c2-heldout-action-preflight.v1"
TRANSITION_SCHEMA = "harness-distill.c2-executed-transition.v1"
EXECUTION_AUDIT_SCHEMA = "harness-distill.c2-execution-audit.v1"
RESPONSE_SCHEMA = (
    "harness-posttrain-eval.interactive-sol-dagger-heldout-next-action-response.v1"
)
REPORT_SCHEMA = (
    "harness-posttrain-eval.interactive-sol-dagger-heldout-next-action-probe.v1"
)
MODULE_RELATIVE = Path(
    "src/harness_posttrain_eval/interactive_sol_dagger_heldout_probe.py"
)
ENTRYPOINT_RELATIVE = Path("scripts/run_interactive_sol_dagger_heldout_probe.sh")

PHASES = (
    "frontier_exploration",
    "checkpoint_grounding",
    "approved_cart_entry",
    "dirty_cart_cleanup",
    "clean_checkout_order",
)
PHASE_COUNTS = {
    "frontier_exploration": 6,
    "checkpoint_grounding": 5,
    "approved_cart_entry": 5,
    "dirty_cart_cleanup": 5,
    "clean_checkout_order": 3,
}
VARIANTS = ("graded", "graded3", "graded4", "mixed")
TRANSACTIONAL_PHASES = (
    "approved_cart_entry",
    "dirty_cart_cleanup",
    "clean_checkout_order",
)
REQUEST_POLICY = {
    "temperature": 1.0,
    "top_p": 0.95,
    "presence_penalty": 0.0,
    "repetition_penalty": 1.0,
    "top_k": 20,
    "min_p": 0.0,
    "chat_template_kwargs": {"preserve_thinking": True},
}
GATE_POLICY = {
    "rows": 24,
    "candidate_valid_responses_at_least": 24,
    "candidate_exact_matches_at_least": 12,
    "candidate_semantic_matches_at_least": 20,
    "transactional_semantic_match_required_for_every_row": True,
    "forbidden_buy_now_actions_at_most": 0,
}
HEX64 = re.compile(r"[0-9a-f]{64}")
_INDEX = re.compile(r"\[(\d+)\]")


def _strict_json(text: str, label: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise IntegrityError(f"{label} contains duplicate key {key!r}")
            value[key] = item
        return value

    try:
        return json.loads(
            text,
            object_pairs_hook=pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                IntegrityError(f"{label} contains non-finite {token}")
            ),
        )
    except json.JSONDecodeError as exc:
        raise IntegrityError(f"{label} is not valid JSON") from exc


def _read_jsonl(path: Path, *, rows: int, label: str) -> list[dict[str, Any]]:
    path = _safe_regular(path.resolve(), label)
    result: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            raise IntegrityError(f"{label} contains blank line {number}")
        value = _strict_json(line, f"{label} row {number}")
        if not isinstance(value, dict):
            raise IntegrityError(f"{label} row {number} is not an object")
        result.append(value)
    if len(result) != rows:
        raise IntegrityError(f"{label} expected {rows} rows, found {len(result)}")
    return result


def _file_rows(
    manifest_path: Path, manifest: Mapping[str, Any], name: str, rows: int
) -> tuple[Path, list[dict[str, Any]]]:
    descriptor = (manifest.get("files") or {}).get(name) or {}
    path = (manifest_path.parent / name).resolve()
    if (
        path.parent != manifest_path.parent
        or descriptor.get("relative_path") != name
        or descriptor.get("rows") != rows
        or descriptor.get("bytes") != _safe_regular(path, name).stat().st_size
        or descriptor.get("sha256") != sha256_file(path)
    ):
        raise IntegrityError(f"heldout artifact changed: {name}")
    return path, _read_jsonl(path, rows=rows, label=name)


def _message_actions(message: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(message, Mapping) or message.get("role") != "assistant":
        raise IntegrityError(f"{label} is not an assistant message")
    if message.get("tool_calls") not in (None, []):
        raise IntegrityError(f"{label} contains tool calls outside action-final JSON")
    content = message.get("content")
    if not isinstance(content, str) or not content or content != content.strip():
        raise IntegrityError(f"{label} content is empty or has outer whitespace")
    payload = _strict_json(content, f"{label} content")
    if not isinstance(payload, dict) or not payload or next(reversed(payload)) != "action":
        raise IntegrityError(f"{label} is not action-final JSON")
    actions = payload.get("action")
    if not isinstance(actions, list) or not actions:
        raise IntegrityError(f"{label} has no actions")
    result: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        if not isinstance(action, Mapping) or not action:
            raise IntegrityError(f"{label} action {index} is malformed")
        keys = [key for key in action if key != "interacted_element"]
        if len(keys) != 1 or not isinstance(keys[0], str) or not keys[0]:
            raise IntegrityError(f"{label} action {index} has an ambiguous operation")
        result.append(dict(action))
    return result


def _action_core(action: Mapping[str, Any]) -> tuple[str, Any]:
    keys = [key for key in action if key != "interacted_element"]
    if len(keys) != 1:
        raise IntegrityError("BrowserUse action has an ambiguous operation")
    return keys[0], action[keys[0]]


def exact_signature(actions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{"name": name, "payload": payload} for name, payload in map(_action_core, actions)]


def _visible_text(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, Mapping) or message.get("role") not in {"user", "tool"}:
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if content is not None:
            return canonical_bytes(content).decode("utf-8")
    return ""


def _target_text(visible: str, index: int) -> str:
    marker = f"[{index}]"
    position = visible.find(marker)
    if position < 0:
        return f"index:{index}"
    tail = visible[position : position + 500]
    next_match = _INDEX.search(tail, len(marker))
    window = tail[: next_match.start()] if next_match else tail
    window = re.sub(r"\[\d+\]", "", window)
    window = re.sub(r"\s+", " ", window).strip().casefold()
    return window[:320] or f"index:{index}"


def _semantic_payload(value: Any, *, visible: str) -> tuple[Any, str | None]:
    if not isinstance(value, Mapping):
        return value, None
    target: str | None = None
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"interacted_element", "element"}:
            continue
        if key == "index" and type(item) is int:
            target = _target_text(visible, item)
            continue
        if isinstance(item, Mapping):
            nested, nested_target = _semantic_payload(item, visible=visible)
            result[key] = nested
            target = target or nested_target
        elif isinstance(item, list):
            result[key] = [
                _semantic_payload(child, visible=visible)[0]
                if isinstance(child, Mapping)
                else child
                for child in item
            ]
        else:
            result[key] = item
    return result, target


def semantic_signature(
    actions: Sequence[Mapping[str, Any]], messages: Any
) -> list[dict[str, Any]]:
    visible = _visible_text(messages)
    result: list[dict[str, Any]] = []
    for name, payload in map(_action_core, actions):
        semantic_payload, target = _semantic_payload(payload, visible=visible)
        item: dict[str, Any] = {"name": name, "payload_without_dom_index": semantic_payload}
        if target is not None:
            item["visible_target"] = target
        result.append(item)
    return result


def _buy_now(actions: Sequence[Mapping[str, Any]], messages: Any) -> bool:
    signature = semantic_signature(actions, messages)
    return "buy now" in canonical_bytes(signature).decode("utf-8").casefold()


def _request_state(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "messages": row.get("messages_before_action"),
        "tools": row.get("tools"),
        "tool_choice": row.get("tool_choice"),
        "parallel_tool_calls": row.get("parallel_tool_calls"),
        "response_format": row.get("response_format"),
    }


def request_body(row: Mapping[str, Any], alias: str) -> dict[str, Any]:
    state = _request_state(row)
    body: dict[str, Any] = {
        "model": alias,
        "messages": state["messages"],
        **REQUEST_POLICY,
    }
    if state["tools"]:
        body["tools"] = state["tools"]
    for key in ("tool_choice", "parallel_tool_calls", "response_format"):
        if state[key] is not None:
            body[key] = state[key]
    return body


def validate_heldout_manifest(
    path: Path, *, expected_file_sha256: str, expected_body_sha256: str
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    path = _safe_regular(path.resolve(), "heldout manifest")
    if not _is_within(path, ALLOWED_SOURCE_ROOTS):
        raise IntegrityError("heldout manifest is outside an approved artifact root")
    manifest = read_json(path)
    if not isinstance(manifest, dict) or manifest.get("schema") != HELDOUT_SCHEMA:
        raise IntegrityError("heldout manifest schema changed")
    _self_hash(manifest, "manifest_sha256", "heldout manifest")
    if (
        HEX64.fullmatch(expected_file_sha256) is None
        or HEX64.fullmatch(expected_body_sha256) is None
        or sha256_file(path) != expected_file_sha256
        or manifest.get("manifest_sha256") != expected_body_sha256
        or manifest.get("status") != "ok"
        or manifest.get("teacher_model") != "gpt-5.6-sol"
        or manifest.get("teacher_reasoning_effort") != "low"
        or manifest.get("provenance") != COLLECTION_PROVENANCE
        or manifest.get("target_rows") != 24
        or manifest.get("materialized_rows") != 24
        or manifest.get("target_phase_counts") != PHASE_COUNTS
        or manifest.get("phase_counts") != PHASE_COUNTS
        or any(manifest.get("available_phase_counts", {}).get(phase, 0) < count for phase, count in PHASE_COUNTS.items())
        or manifest.get("phase_shortfalls") != {phase: 0 for phase in PHASES}
        or manifest.get("train_rows") != 0
        or manifest.get("heldout_only") is not True
        or manifest.get("used_for_training") is not False
        or (manifest.get("source") or {}).get("train_substitution") is not False
        or set(manifest.get("files") or {})
        != {"heldout_preflight.jsonl", "heldout_execution_audits.jsonl"}
    ):
        raise IntegrityError("heldout manifest identity or policy changed")
    _rows_path, rows = _file_rows(path, manifest, "heldout_preflight.jsonl", 24)
    _audits_path, audits = _file_rows(
        path, manifest, "heldout_execution_audits.jsonl", 24
    )
    audit_by_row = {str(audit.get("row_id")): audit for audit in audits}
    if len(audit_by_row) != 24:
        raise IntegrityError("heldout audits repeat a row identity")
    state_ids: set[str] = set()
    row_ids: set[str] = set()
    phases: Counter[str] = Counter()
    subtypes: Counter[str] = Counter()
    for index, row in enumerate(rows):
        row_id = row.get("row_id")
        state_id = row.get("state_id")
        phase = row.get("phase")
        subtype = row.get("phase_subtype")
        request_state = _request_state(row)
        execution = row.get("execution") or {}
        audit = audit_by_row.get(str(row_id)) or {}
        try:
            _message_actions(row.get("teacher_message"), label=f"heldout teacher {index}")
        except IntegrityError as exc:
            raise IntegrityError(f"heldout row {index} teacher target is invalid: {exc}") from exc
        if (
            row.get("schema") != TRANSITION_SCHEMA
            or row.get("schema_version") != 1
            or not isinstance(row_id, str)
            or HEX64.fullmatch(row_id) is None
            or not isinstance(state_id, str)
            or HEX64.fullmatch(state_id) is None
            or row_id in row_ids
            or state_id in state_ids
            or phase not in PHASES
            or not isinstance(subtype, str)
            or not subtype
            or row.get("variant") not in VARIANTS
            or row.get("horizon") != 12
            or row.get("source_split") != "holdout"
            or row.get("chosen_by_executor") is not True
            or execution
            != {
                "attempted": True,
                "valid": True,
                "objective_success": True,
                "successor_observed": True,
                "postcondition_verified": True,
            }
            or not isinstance(request_state["messages"], list)
            or not request_state["messages"]
            or not any(
                isinstance(message, Mapping) and message.get("role") == "user"
                for message in request_state["messages"]
            )
            or not isinstance(request_state["messages"][-1], Mapping)
            or request_state["messages"][-1].get("role") == "assistant"
            or not isinstance(request_state["tools"], list)
            or not isinstance(row.get("qwen_message"), Mapping)
            or row["qwen_message"].get("role") != "assistant"
            or not isinstance(row["qwen_message"].get("content"), str)
            or audit.get("schema") != EXECUTION_AUDIT_SCHEMA
            or audit.get("row_id") != row_id
            or audit.get("state_id") != state_id
            or audit.get("source_split") != "holdout"
            or audit.get("phase") != phase
            or audit.get("phase_subtype") != subtype
            or audit.get("status") != "accepted"
            or audit.get("accepted") is not True
            or audit.get("browser_executed") is not True
            or audit.get("executed") is not True
            or audit.get("successor_observed") is not True
            or audit.get("postcondition_verified") is not True
            or audit.get("evaluator_read") is not False
            or audit.get("scorer_reward_read") is not False
            or (row.get("source") or {}).get("execution_audit_sha256")
            != sha256_bytes(canonical_bytes(audit))
        ):
            raise IntegrityError(f"heldout row/audit contract changed at index {index}")
        row_ids.add(row_id)
        state_ids.add(state_id)
        phases[str(phase)] += 1
        subtypes[f"{phase}/{subtype}"] += 1
    if dict(phases) != PHASE_COUNTS or dict(subtypes) != manifest.get(
        "phase_subtype_counts"
    ):
        raise IntegrityError("heldout row phase/subtype counts changed")
    available_subtypes = manifest.get("available_phase_subtype_counts") or {}
    if not isinstance(available_subtypes, Mapping) or any(
        type(value) is not int or value < subtypes.get(label, 0)
        for label, value in available_subtypes.items()
    ):
        raise IntegrityError("heldout subtype availability is malformed")
    return manifest, rows, audits


def _candidate_message(response: Any, alias: str) -> dict[str, Any]:
    choices = response.get("choices") if isinstance(response, Mapping) else None
    choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else None
    message = choice.get("message") if isinstance(choice, Mapping) else None
    if (
        not isinstance(message, Mapping)
        or response.get("model") != alias
        or choice.get("finish_reason") != "stop"
    ):
        raise IntegrityError("candidate response lacks one stopped choice from the bound model")
    return dict(message)


def _post(body: Mapping[str, Any], *, api_key: str, timeout: int) -> tuple[int, dict[str, Any]]:
    connection = http.client.HTTPConnection("127.0.0.1", 18541, timeout=timeout)
    headers = {"Content-Type": "application/json", "Connection": "close"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        connection.request(
            "POST", "/v1/chat/completions", body=canonical_bytes(body), headers=headers
        )
        response = connection.getresponse()
        payload = response.read(16 * 1024 * 1024 + 1)
    except (OSError, http.client.HTTPException) as exc:
        raise IntegrityError(f"candidate next-action request failed: {exc}") from exc
    finally:
        connection.close()
    if len(payload) > 16 * 1024 * 1024:
        raise IntegrityError("candidate response exceeds 16 MiB")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IntegrityError("candidate response is not UTF-8") from exc
    value = _strict_json(text, "candidate HTTP response")
    if not isinstance(value, dict):
        raise IntegrityError("candidate HTTP response is not an object")
    return response.status, value


def _comparison(
    *, row: Mapping[str, Any], response: Mapping[str, Any], alias: str
) -> dict[str, Any]:
    teacher_actions = _message_actions(row["teacher_message"], label="teacher message")
    candidate_valid = True
    candidate_error_sha256: str | None = None
    try:
        candidate_message = _candidate_message(response, alias)
        candidate_actions = _message_actions(candidate_message, label="candidate message")
    except IntegrityError as exc:
        candidate_valid = False
        candidate_error_sha256 = sha256_bytes(str(exc).encode("utf-8"))
        choices = response.get("choices") if isinstance(response, Mapping) else None
        choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else None
        raw_message = choice.get("message") if isinstance(choice, Mapping) else None
        candidate_message = dict(raw_message) if isinstance(raw_message, Mapping) else {}
        candidate_actions = []
    baseline_valid = True
    try:
        baseline_actions = _message_actions(row["qwen_message"], label="baseline Qwen message")
    except IntegrityError:
        baseline_valid = False
        baseline_actions = []
    messages = row["messages_before_action"]
    teacher_exact = exact_signature(teacher_actions)
    candidate_exact = exact_signature(candidate_actions)
    baseline_exact = exact_signature(baseline_actions)
    teacher_semantic = semantic_signature(teacher_actions, messages)
    candidate_semantic = semantic_signature(candidate_actions, messages)
    baseline_semantic = semantic_signature(baseline_actions, messages)
    return {
        "candidate_valid": candidate_valid,
        "candidate_error_sha256": candidate_error_sha256,
        "baseline_valid": baseline_valid,
        "exact_match": candidate_valid and candidate_exact == teacher_exact,
        "semantic_match": candidate_valid and candidate_semantic == teacher_semantic,
        "baseline_exact_match": baseline_valid and baseline_exact == teacher_exact,
        "baseline_semantic_match": baseline_valid and baseline_semantic == teacher_semantic,
        "forbidden_buy_now": candidate_valid and _buy_now(candidate_actions, messages),
        "signatures": {
            "teacher_exact": teacher_exact,
            "candidate_exact": candidate_exact,
            "baseline_exact": baseline_exact,
            "teacher_semantic": teacher_semantic,
            "candidate_semantic": candidate_semantic,
            "baseline_semantic": baseline_semantic,
        },
        "candidate_message": candidate_message,
    }


def _breakdown(rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(str(row[field]) for field in fields)].append(row)
    result: list[dict[str, Any]] = []
    for key in sorted(groups):
        items = groups[key]
        result.append(
            {
                **dict(zip(fields, key, strict=True)),
                "rows": len(items),
                "candidate_valid": sum(bool(item["candidate_valid"]) for item in items),
                "exact_matches": sum(bool(item["exact_match"]) for item in items),
                "semantic_matches": sum(bool(item["semantic_match"]) for item in items),
                "baseline_valid": sum(bool(item["baseline_valid"]) for item in items),
                "baseline_exact_matches": sum(
                    bool(item["baseline_exact_match"]) for item in items
                ),
                "baseline_semantic_matches": sum(
                    bool(item["baseline_semantic_match"]) for item in items
                ),
                "forbidden_buy_now": sum(
                    bool(item["forbidden_buy_now"]) for item in items
                ),
            }
        )
    return result


def _summaries(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    overall = _breakdown(rows, ())[0]
    transactional = [row for row in rows if row["phase"] in TRANSACTIONAL_PHASES]
    gate_checks = {
        "row_inventory": overall["rows"] == GATE_POLICY["rows"],
        "candidate_valid": overall["candidate_valid"]
        >= GATE_POLICY["candidate_valid_responses_at_least"],
        "exact_match": overall["exact_matches"]
        >= GATE_POLICY["candidate_exact_matches_at_least"],
        "semantic_match": overall["semantic_matches"]
        >= GATE_POLICY["candidate_semantic_matches_at_least"],
        "transactional_semantic_match": all(
            bool(row["semantic_match"]) for row in transactional
        ),
        "forbidden_buy_now": overall["forbidden_buy_now"]
        <= GATE_POLICY["forbidden_buy_now_actions_at_most"],
    }
    return {
        "overall": overall,
        "by_phase": _breakdown(rows, ("phase",)),
        "by_phase_subtype": _breakdown(rows, ("phase", "phase_subtype")),
        "gate": {
            "policy": GATE_POLICY,
            "checks": gate_checks,
            "passed": all(gate_checks.values()),
        },
    }


def _write_jsonl_create_only(path: Path, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    payload = b"".join(canonical_bytes(row) + b"\n" for row in rows)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError as exc:
        raise IntegrityError(f"create-only response target exists: {path}") from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    return {
        "relative_path": path.name,
        "sha256": sha256_bytes(payload),
        "bytes": len(payload),
        "rows": len(rows),
    }


def run_probe(arguments: argparse.Namespace) -> None:
    endpoint_path = arguments.endpoint_receipt.resolve()
    heldout_path = arguments.heldout_manifest.resolve()
    endpoint = validate_endpoint(endpoint_path)
    manifest, heldout_rows, _audits = validate_heldout_manifest(
        heldout_path,
        expected_file_sha256=arguments.expected_heldout_file_sha256,
        expected_body_sha256=arguments.expected_heldout_body_sha256,
    )
    if (
        sha256_file(endpoint_path) != arguments.expected_endpoint_file_sha256
        or endpoint.get("receipt_sha256") != arguments.expected_endpoint_body_sha256
        or endpoint.get("model_spec", {}).get("base_url") != LOCAL_BASE_URL
    ):
        raise IntegrityError("next-action probe endpoint identity changed")
    alias = str(endpoint["candidate"]["served_model_name"])
    api_key = os.environ.get("HARNESS_POSTTRAIN_API_KEY", "")
    response_rows: list[dict[str, Any]] = []
    for index, row in enumerate(heldout_rows):
        request = request_body(row, alias)
        status, response = _post(request, api_key=api_key, timeout=arguments.timeout_seconds)
        if status != 200:
            raise IntegrityError(f"candidate returned HTTP {status} for heldout row {index}")
        comparison = _comparison(row=row, response=response, alias=alias)
        response_rows.append(
            {
                "schema": RESPONSE_SCHEMA,
                "index": index,
                "row_id": row["row_id"],
                "state_id": row["state_id"],
                "phase": row["phase"],
                "phase_subtype": row["phase_subtype"],
                "variant": row["variant"],
                "source_split": "holdout",
                "request_state_sha256": sha256_bytes(canonical_bytes(_request_state(row))),
                "request_sha256": sha256_bytes(canonical_bytes(request)),
                "http_status": status,
                "response_sha256": sha256_bytes(canonical_bytes(response)),
                "response": response,
                **comparison,
            }
        )
    summaries = _summaries(response_rows)
    output_root = arguments.output_root.resolve()
    if output_root.exists() or output_root.is_symlink():
        raise IntegrityError("next-action probe output root must be fresh")
    output_root.mkdir(parents=True, mode=0o700)
    responses = _write_jsonl_create_only(output_root / "responses.jsonl", response_rows)
    lineage = endpoint["artifacts"]["pvc_lineage_attestation"]
    core = {
        "schema": REPORT_SCHEMA,
        "status": "complete",
        "development_gate_only": True,
        "part_of_sealed_final_evaluation": False,
        "browser_executed": False,
        "training_rows_read": 0,
        "heldout_teacher_manifest_validated": True,
        "heldout_teacher_targets_read_for_comparison": 24,
        "heldout_teacher_targets_used_for_training": False,
        "heldout_rows_read": 24,
        "request_policy": REQUEST_POLICY,
        "endpoint": {
            "path": str(endpoint_path),
            "file_sha256": sha256_file(endpoint_path),
            "body_sha256": endpoint["receipt_sha256"],
            "lineage": lineage,
            "candidate_tree_sha256": endpoint["candidate"]["adapter_tree_sha256"],
            "candidate_composite_sha256": endpoint["candidate"]["composite_sha256"],
            "served_model_name": alias,
        },
        "heldout": {
            "path": str(heldout_path),
            "file_sha256": sha256_file(heldout_path),
            "body_sha256": manifest["manifest_sha256"],
            "provenance": COLLECTION_PROVENANCE,
            "phase_counts": PHASE_COUNTS,
            "phase_subtype_counts": manifest["phase_subtype_counts"],
        },
        "responses": responses,
        **summaries,
    }
    report = {**core, "report_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(output_root / "report.json", report)
    print(
        json.dumps(
            {
                "status": "complete",
                "report_sha256": report["report_sha256"],
                "gate_passed": report["gate"]["passed"],
                "overall": report["overall"],
            },
            sort_keys=True,
        )
    )


def audit_report(path: Path) -> dict[str, Any]:
    path = _safe_regular(path.resolve(), "next-action report")
    value = read_json(path)
    if not isinstance(value, dict) or value.get("schema") != REPORT_SCHEMA:
        raise IntegrityError("next-action report schema changed")
    _self_hash(value, "report_sha256", "next-action report")
    endpoint_record = value.get("endpoint") or {}
    heldout_record = value.get("heldout") or {}
    endpoint_path = Path(str(endpoint_record.get("path", ""))).resolve()
    heldout_path = Path(str(heldout_record.get("path", ""))).resolve()
    endpoint = validate_endpoint(endpoint_path)
    manifest, heldout_rows, _audits = validate_heldout_manifest(
        heldout_path,
        expected_file_sha256=str(heldout_record.get("file_sha256", "")),
        expected_body_sha256=str(heldout_record.get("body_sha256", "")),
    )
    if (
        value.get("status") != "complete"
        or value.get("development_gate_only") is not True
        or value.get("part_of_sealed_final_evaluation") is not False
        or value.get("browser_executed") is not False
        or value.get("training_rows_read") != 0
        or value.get("heldout_teacher_manifest_validated") is not True
        or value.get("heldout_teacher_targets_read_for_comparison") != 24
        or value.get("heldout_teacher_targets_used_for_training") is not False
        or value.get("heldout_rows_read") != 24
        or value.get("request_policy") != REQUEST_POLICY
        or endpoint_record.get("file_sha256") != sha256_file(endpoint_path)
        or endpoint_record.get("body_sha256") != endpoint["receipt_sha256"]
        or endpoint_record.get("lineage")
        != endpoint["artifacts"]["pvc_lineage_attestation"]
        or endpoint_record.get("candidate_tree_sha256")
        != endpoint["candidate"]["adapter_tree_sha256"]
        or endpoint_record.get("candidate_composite_sha256")
        != endpoint["candidate"]["composite_sha256"]
        or endpoint_record.get("served_model_name")
        != endpoint["candidate"]["served_model_name"]
        or heldout_record.get("provenance") != COLLECTION_PROVENANCE
        or heldout_record.get("phase_counts") != PHASE_COUNTS
        or heldout_record.get("phase_subtype_counts")
        != manifest["phase_subtype_counts"]
    ):
        raise IntegrityError("next-action report endpoint/heldout policy changed")
    response_record = value.get("responses") or {}
    response_path = (path.parent / "responses.jsonl").resolve()
    if (
        response_path.parent != path.parent
        or response_record.get("relative_path") != "responses.jsonl"
        or response_record.get("rows") != 24
        or response_record.get("bytes") != _safe_regular(response_path, "responses").stat().st_size
        or response_record.get("sha256") != sha256_file(response_path)
    ):
        raise IntegrityError("next-action response inventory changed")
    raw_rows = _read_jsonl(response_path, rows=24, label="next-action responses")
    alias = str(endpoint["candidate"]["served_model_name"])
    computed: list[dict[str, Any]] = []
    for index, (source, observed) in enumerate(zip(heldout_rows, raw_rows, strict=True)):
        request = request_body(source, alias)
        response = observed.get("response")
        comparison = _comparison(row=source, response=response, alias=alias)
        expected = {
            "schema": RESPONSE_SCHEMA,
            "index": index,
            "row_id": source["row_id"],
            "state_id": source["state_id"],
            "phase": source["phase"],
            "phase_subtype": source["phase_subtype"],
            "variant": source["variant"],
            "source_split": "holdout",
            "request_state_sha256": sha256_bytes(canonical_bytes(_request_state(source))),
            "request_sha256": sha256_bytes(canonical_bytes(request)),
            "http_status": 200,
            "response_sha256": sha256_bytes(canonical_bytes(response)),
            "response": response,
            **comparison,
        }
        if observed != expected:
            raise IntegrityError(f"next-action response row {index} changed")
        computed.append(expected)
    summaries = _summaries(computed)
    if any(value.get(key) != item for key, item in summaries.items()):
        raise IntegrityError("next-action aggregate report changed")
    return value


def audit_command(arguments: argparse.Namespace) -> None:
    value = audit_report(arguments.path)
    if arguments.require_pass and value["gate"]["passed"] is not True:
        raise IntegrityError("next-action development gate did not pass")
    print(
        json.dumps(
            {
                "valid": True,
                "report_sha256": value["report_sha256"],
                "gate_passed": value["gate"]["passed"],
            },
            sort_keys=True,
        )
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--endpoint-receipt", type=Path, required=True)
    run.add_argument("--expected-endpoint-file-sha256", required=True)
    run.add_argument("--expected-endpoint-body-sha256", required=True)
    run.add_argument("--heldout-manifest", type=Path, required=True)
    run.add_argument("--expected-heldout-file-sha256", required=True)
    run.add_argument("--expected-heldout-body-sha256", required=True)
    run.add_argument("--output-root", type=Path, required=True)
    run.add_argument("--timeout-seconds", type=int, default=900, choices=(900,))
    audit = commands.add_parser("audit")
    audit.add_argument("--path", type=Path, required=True)
    audit.add_argument("--require-pass", action="store_true")
    return root


def main() -> None:
    arguments = parser().parse_args()
    try:
        {"run": run_probe, "audit": audit_command}[arguments.command](arguments)
    except (IntegrityError, KeyError, OSError) as exc:
        raise SystemExit(f"InteractiveSolDaggerHeldoutProbeError: {exc}") from exc


if __name__ == "__main__":
    main()
