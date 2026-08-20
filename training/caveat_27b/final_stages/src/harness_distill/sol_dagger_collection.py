"""Student-state DAgger labels from ``gpt-5.6-sol#low`` without harness drift.

This module is intentionally separate from :mod:`harness_distill.teacher_labels`.
That older path gives a same-base teacher a private phase prefix.  Cross-model
DAgger has a different contract: Sol sees exactly the model-visible state that
Qwen saw, modulo provider-owned sampling fields and the unavoidable model name.
Phase, evidence, split, and execution validation remain in hash-bound sidecars.

The pipeline has three trust boundaries:

* :func:`capture_qwen_states` joins successful Qwen proxy calls to collection
  sidecars and proves that every evidence reference names an existing message;
* :func:`label_qwen_states` calls the fixed TRAPI model and strips provider-only
  reasoning from its assistant turn;
* :func:`materialize_verified_sft` accepts only externally executed, grounded
  actions and emits the original Qwen prompt plus one Sol assistant turn.

No function in this module adds a prompt, tool, ledger, phase name, reference
action, evaluator value, or sidecar field to a model request.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .materialize import (
    PRIME_CONTROLLED_REQUEST_FIELDS,
    PROXY_TRACE_SCHEMA,
    canonical_json,
    infer_turn_index,
    replay_request,
    sha256_json,
)

SOL_MODEL_SPEC = "gpt-5.6-sol#low"
SOL_LOGICAL_MODEL = "gpt-5.6-sol"
SOL_REASONING_EFFORT = "low"

CAPTURE_SIDECAR_SCHEMA = "harness-distill.sol-dagger-capture-sidecar.v1"
CAPTURE_SCHEDULE_SCHEMA = "harness-distill.sol-dagger-capture-schedule.v1"
CAPTURED_STATE_SCHEMA = "harness-distill.sol-dagger-captured-state.v1"
SHADOW_LABEL_SCHEMA = "harness-distill.sol-dagger-shadow.v1"
INTERVENTION_LABEL_SCHEMA = "harness-distill.sol-dagger-intervention.v1"
ACTION_VALIDATION_SCHEMA = "harness-distill.sol-dagger-action-validation.v1"
STATIC_VISIBLE_VALIDATION_SCHEMA = "harness-distill.sol-dagger-static-visible-validation.v1"
CORRECTIVE_SFT_SCHEMA = "harness-distill.sol-dagger-corrective-sft.v1"
LABEL_AUDIT_SCHEMA = "harness-distill.sol-dagger-label-audit.v1"
RAW_BUNDLE_SCHEMA = "harness-distill.sol-dagger-raw-bundle.v1"
FINAL_BUNDLE_SCHEMA = "harness-distill.sol-dagger-final-bundle.v1"
COLLECTION_MANIFEST_SCHEMA = "harness-distill.sol-dagger-collection-manifest.v1"
ROLLOUT_BUNDLE_SCHEMA = "harness-distill.sol-dagger-rollout-bundle.v1"
STRUCTURAL_RESERVE_AUDIT_SCHEMA = "harness-distill.sol-dagger-structural-reserve-audit.v1"
CLEANUP_SCHEDULE_AUDIT_SCHEMA = "harness-distill.sol-dagger-cleanup-schedule-audit.v1"
RESERVE_NONTRAINING_BUNDLE_SCHEMA = "harness-distill.sol-dagger-reserve-nontraining-bundle.v1"
RETENTION_SOURCE_SHA256 = "4d614d80ae4db704b68118da00942b1584805e3e59a4a2cf0e1cee79117adc1c"

LAPTOP_VARIANTS = ("graded", "graded3", "graded4", "mixed")
DECISION_PHASES = (
    "constraints_query",
    "pagination_exploration",
    "pdp_evidence_selection",
    "checkpoint_grounding",
    "cart_cleanup_recheck",
    "checkout_order",
)
COLLECTION_MODES = ("shadow", "intervention")
SPLITS = ("train", "holdout")

_PHASE_QUANTILES = {
    "constraints_query": 0.05,
    "pagination_exploration": 0.25,
    "pdp_evidence_selection": 0.45,
    "checkpoint_grounding": 0.60,
    "cart_cleanup_recheck": 0.78,
    "checkout_order": 0.95,
}

_HEX64 = frozenset("0123456789abcdef")
_PROVIDER_ONLY_FIELDS = frozenset(
    set(PRIME_CONTROLLED_REQUEST_FIELDS)
    | {
        # Qwen/vLLM renderer configuration is provider state, not a harness
        # observation.  Sending it to TRAPI would be both meaningless and a
        # possible incompatibility.
        "chat_template_kwargs",
        "guided_json",
        "guided_regex",
        "guided_choice",
    }
)
_SIDECAR_TOP_LEVEL_FIELDS = frozenset(
    {
        "campaign_id",
        "collection_nonce",
        "source_kind",
        "harness_id",
        "harness_fingerprint_sha256",
        "rollout_id",
        "variant",
        "phase",
        "mode",
        "split",
        "captured_at_unix",
        "evidence_refs",
        "action_validation",
        "validator_sha256",
    }
)
_PROVIDER_REASONING_FIELDS = frozenset(
    {
        "reasoning",
        "reasoning_content",
        "reasoning_details",
        "analysis",
        "encrypted_content",
    }
)
_DROPPABLE_ENVELOPE_FIELDS = frozenset({"annotations", "audio", "function_call", "refusal"})


class SolDaggerError(RuntimeError):
    """The DAgger boundary could not be proven without model-visible drift."""


def _json_clone(value: Any, *, label: str) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise SolDaggerError(f"{label} must contain finite JSON") from exc


def _require_text(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SolDaggerError(f"{label} must be non-empty text")
    return value


def _require_hex64(value: Any, *, label: str) -> str:
    text = _require_text(value, label=label)
    if len(text) != 64 or any(character not in _HEX64 for character in text):
        raise SolDaggerError(f"{label} must be a lowercase SHA-256")
    return text


def _require_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SolDaggerError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise SolDaggerError(f"{label} must be a finite number")
    return result


def _harness_projection(request: Mapping[str, Any]) -> dict[str, Any]:
    """Return all model-facing request fields except provider controls."""

    cloned = _json_clone(dict(request), label="model request")
    return {key: value for key, value in cloned.items() if key not in _PROVIDER_ONLY_FIELDS}


def _assert_unmodified_harness(
    qwen_request: Mapping[str, Any],
    teacher_request: Mapping[str, Any],
) -> str:
    qwen_projection = _harness_projection(qwen_request)
    teacher_projection = _harness_projection(teacher_request)
    if teacher_projection != qwen_projection:
        raise SolDaggerError("Sol request changes a model-visible harness field")
    for name in ("messages", "tools", "tool_choice", "parallel_tool_calls", "response_format"):
        if name in qwen_request and teacher_request.get(name) != qwen_request[name]:
            raise SolDaggerError(f"Sol request changes exact Qwen {name}")
    leaked = sorted(_SIDECAR_TOP_LEVEL_FIELDS & set(teacher_request))
    if leaked:
        raise SolDaggerError(f"collection sidecar fields entered Sol request: {leaked}")
    return sha256_json(qwen_projection)


def _response_message(response: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], Mapping):
        raise SolDaggerError(f"{label} must contain exactly one choice")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise SolDaggerError(f"{label} choice has no assistant message")
    return _json_clone(dict(message), label=f"{label} assistant message")


def _allowed_tool_names(request: Mapping[str, Any]) -> set[str]:
    tools = request.get("tools", [])
    if tools is None:
        return set()
    if not isinstance(tools, list):
        raise SolDaggerError("captured tools must be a list")
    names: set[str] = set()
    for index, tool in enumerate(tools):
        if not isinstance(tool, Mapping) or tool.get("type") != "function":
            raise SolDaggerError(f"captured tools[{index}] is not a function tool")
        function = tool.get("function")
        if not isinstance(function, Mapping):
            raise SolDaggerError(f"captured tools[{index}] has no function schema")
        name = _require_text(function.get("name"), label=f"captured tools[{index}].name")
        if name in names:
            raise SolDaggerError(f"captured tools contain duplicate name {name!r}")
        names.add(name)
    return names


def _canonical_tool_calls(value: Any, *, allowed_names: set[str]) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or not value:
        raise SolDaggerError("Sol tool_calls must be a non-empty list when present")
    calls: list[dict[str, Any]] = []
    for index, call in enumerate(value):
        if not isinstance(call, Mapping):
            raise SolDaggerError(f"Sol tool_calls[{index}] must be an object")
        function = call.get("function")
        if call.get("type") != "function" or not isinstance(function, Mapping):
            raise SolDaggerError(f"Sol tool_calls[{index}] is not a function call")
        name = _require_text(function.get("name"), label=f"Sol tool_calls[{index}].name")
        if name not in allowed_names:
            raise SolDaggerError(f"Sol called tool absent from Qwen schema: {name!r}")
        arguments = function.get("arguments")
        if isinstance(arguments, Mapping):
            arguments = canonical_json(arguments)
        if not isinstance(arguments, str):
            raise SolDaggerError(f"Sol tool_calls[{index}].arguments must be JSON text")
        try:
            decoded = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise SolDaggerError(f"Sol tool_calls[{index}] arguments are not JSON") from exc
        if not isinstance(decoded, Mapping):
            raise SolDaggerError(f"Sol tool_calls[{index}] arguments must decode to an object")
        identifier = call.get("id")
        if not isinstance(identifier, str) or not identifier:
            # Chat providers occasionally omit a tool-call id.  A stable id is
            # derived from visible call bytes; no hidden provider state enters.
            identifier = "call_" + sha256_json({"name": name, "arguments": arguments})[:24]
        calls.append(
            {
                "id": identifier,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        )
    return calls


def strip_provider_reasoning(
    message: Mapping[str, Any],
    *,
    baseline_request: Mapping[str, Any],
) -> dict[str, Any]:
    """Return one replayable assistant turn with provider reasoning removed.

    A BrowserUse action's visible JSON may itself contain a ``thinking`` field;
    that content is retained.  Only top-level provider envelope fields are
    stripped.
    """

    raw = _json_clone(dict(message), label="Sol assistant message")
    role = raw.pop("role", "assistant")
    if role != "assistant":
        raise SolDaggerError("Sol completion role must be assistant")
    for name in _PROVIDER_REASONING_FIELDS:
        raw.pop(name, None)
    refusal = raw.get("refusal")
    if refusal is not None and refusal != "":
        raise SolDaggerError("Sol refused instead of producing a corrective action")
    audio = raw.get("audio")
    function_call = raw.get("function_call")
    if (audio is not None and audio != "") or (function_call is not None and function_call != ""):
        raise SolDaggerError("Sol returned an unsupported assistant envelope")
    for name in _DROPPABLE_ENVELOPE_FIELDS:
        raw.pop(name, None)

    allowed = {"content", "tool_calls"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise SolDaggerError(f"Sol assistant message has unsupported fields: {unknown}")
    content = raw.get("content")
    if content is not None and (not isinstance(content, str) or not content.strip()):
        raise SolDaggerError("Sol assistant content must be non-empty text or null")
    calls = _canonical_tool_calls(
        raw.get("tool_calls"), allowed_names=_allowed_tool_names(baseline_request)
    )
    if content in {None, ""} and not calls:
        raise SolDaggerError("Sol returned an empty corrective action")
    completion: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        completion["tool_calls"] = calls
    # Reuse the PRIME replay dialect validator.  It rejects fields that would
    # be silently normalized by the Qwen renderer.
    try:
        infer_turn_index([completion])
    except (TypeError, ValueError) as exc:
        raise SolDaggerError(f"Sol completion is not replayable: {exc}") from exc
    return completion


def make_sol_low_request(
    qwen_request: Mapping[str, Any],
    *,
    wire_model: str,
    max_completion_tokens: int = 8192,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create the sole allowed cross-provider transformation.

    ``messages``, tools, response contracts, and all other harness fields are
    unchanged.  Only model/provider controls are replaced.
    """

    _require_text(wire_model, label="Sol wire model")
    if type(max_completion_tokens) is not int or max_completion_tokens < 1:
        raise SolDaggerError("max_completion_tokens must be a positive integer")
    teacher = _harness_projection(qwen_request)
    teacher.update(
        {
            "model": wire_model,
            "reasoning_effort": SOL_REASONING_EFFORT,
            "max_completion_tokens": max_completion_tokens,
            "n": 1,
            "stream": False,
        }
    )
    projection_sha = _assert_unmodified_harness(qwen_request, teacher)
    audit = {
        "model_spec": SOL_MODEL_SPEC,
        "logical_model": SOL_LOGICAL_MODEL,
        "reasoning_effort": SOL_REASONING_EFFORT,
        "qwen_harness_projection_sha256": projection_sha,
        "teacher_harness_projection_sha256": sha256_json(_harness_projection(teacher)),
        "messages_sha256": sha256_json(qwen_request.get("messages")),
        "tools_sha256": sha256_json(qwen_request.get("tools", [])),
        "unchanged": True,
    }
    return teacher, audit


def _responses_content(content: Any, *, output: bool) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "output_text" if output else "input_text", "text": content}]
    if not isinstance(content, list):
        raise SolDaggerError("Responses message content must be text or content parts")
    parts: list[dict[str, Any]] = []
    for index, part in enumerate(content):
        if not isinstance(part, Mapping):
            raise SolDaggerError(f"Responses content part {index} must be an object")
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            parts.append(
                {
                    "type": "output_text" if output else "input_text",
                    "text": part["text"],
                }
            )
        elif not output and part.get("type") == "image_url":
            image = part.get("image_url")
            if not isinstance(image, Mapping) or not isinstance(image.get("url"), str):
                raise SolDaggerError(f"Responses image part {index} is invalid")
            parts.append({"type": "input_image", "image_url": image["url"]})
        else:
            raise SolDaggerError(f"Responses content part {index} is unsupported")
    return parts


def _responses_input(messages: Any) -> list[dict[str, Any]]:
    if not isinstance(messages, list) or not messages:
        raise SolDaggerError("Sol Responses request requires messages")
    items: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise SolDaggerError(f"Sol message {index} must be an object")
        role = message.get("role")
        if role in {"system", "developer", "user"}:
            items.append(
                {
                    "role": role,
                    "content": _responses_content(message.get("content"), output=False),
                }
            )
        elif role == "assistant":
            content = message.get("content")
            if content is not None and content != "":
                items.append(
                    {
                        "role": "assistant",
                        "content": _responses_content(content, output=True),
                    }
                )
            # ``reasoning_content`` is a Qwen/vLLM provider envelope, not a
            # browser-harness observation.  It is deliberately not translated
            # into an OpenAI output_text item.
            for call in message.get("tool_calls") or []:
                if not isinstance(call, Mapping) or not isinstance(call.get("function"), Mapping):
                    raise SolDaggerError("historical assistant tool call is invalid")
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.get("id"),
                        "name": call["function"].get("name"),
                        "arguments": call["function"].get("arguments"),
                    }
                )
        elif role == "tool":
            content = message.get("content")
            if not isinstance(content, str):
                content = canonical_json(content)
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id"),
                    "output": content,
                }
            )
        else:
            raise SolDaggerError(f"Sol message {index} has unsupported role {role!r}")
    return items


def _responses_tools(tools: Any) -> list[dict[str, Any]]:
    if tools is None or tools == ():
        return []
    if not isinstance(tools, list):
        raise SolDaggerError("Sol Responses tools must be a list")
    converted: list[dict[str, Any]] = []
    for index, tool in enumerate(tools):
        if not isinstance(tool, Mapping) or tool.get("type") != "function":
            raise SolDaggerError(f"Sol Responses tool {index} is not a function")
        function = tool.get("function")
        if not isinstance(function, Mapping):
            raise SolDaggerError(f"Sol Responses tool {index} has no function schema")
        converted_tool = {
            "type": "function",
            "name": function.get("name"),
            "description": function.get("description", ""),
            "parameters": function.get("parameters", {}),
        }
        if "strict" in function:
            converted_tool["strict"] = function["strict"]
        converted.append(converted_tool)
    return converted


def _responses_text(response_format: Any) -> dict[str, Any] | None:
    if response_format is None or response_format == {}:
        return None
    if not isinstance(response_format, Mapping):
        raise SolDaggerError("Sol response_format must be an object")
    kind = response_format.get("type")
    if kind == "text":
        return None
    if kind == "json_object":
        return {"format": {"type": "json_object"}}
    if kind == "json_schema":
        schema = response_format.get("json_schema")
        if not isinstance(schema, Mapping):
            raise SolDaggerError("Sol json_schema response format is invalid")
        return {
            "format": {
                "type": "json_schema",
                "name": schema.get("name"),
                "schema": schema.get("schema"),
                "strict": schema.get("strict", False),
            }
        }
    raise SolDaggerError(f"unsupported Sol response_format type {kind!r}")


def make_sol_low_responses_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Translate the audited chat projection onto TRAPI's Responses API.

    This is a transport conversion only.  The messages, function schemas,
    tool-choice policy, and response format are mapped one-for-one; no prompt
    or sidecar content is added.
    """

    if request.get("reasoning_effort") != SOL_REASONING_EFFORT:
        raise SolDaggerError("Sol Responses request must use low reasoning effort")
    response_request: dict[str, Any] = {
        "model": request.get("model"),
        "input": _responses_input(request.get("messages")),
        "reasoning": {"effort": SOL_REASONING_EFFORT},
        "max_output_tokens": request.get("max_completion_tokens"),
        "stream": False,
    }
    tools = _responses_tools(request.get("tools", []))
    if tools:
        response_request["tools"] = tools
        response_request["tool_choice"] = request.get("tool_choice", "auto")
        if "parallel_tool_calls" in request:
            response_request["parallel_tool_calls"] = request["parallel_tool_calls"]
    text = _responses_text(request.get("response_format"))
    if text is not None:
        response_request["text"] = text
    return response_request


def _adapt_responses_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    output = payload.get("output")
    if not isinstance(output, list):
        raise SolDaggerError("TRAPI Responses payload has no output list")
    texts: list[str] = []
    calls: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, Mapping):
            raise SolDaggerError("TRAPI Responses output item is not an object")
        kind = item.get("type")
        if kind == "function_call":
            calls.append(
                {
                    "id": item.get("call_id") or item.get("id"),
                    "type": "function",
                    "function": {
                        "name": item.get("name"),
                        "arguments": item.get("arguments"),
                    },
                }
            )
        elif kind == "message":
            content = item.get("content")
            if not isinstance(content, list):
                raise SolDaggerError("TRAPI Responses message has no content list")
            for part in content:
                if isinstance(part, Mapping) and part.get("type") == "output_text":
                    text = part.get("text")
                    if not isinstance(text, str):
                        raise SolDaggerError("TRAPI Responses output_text is not text")
                    texts.append(text)
        # Reasoning output items are intentionally neither persisted nor
        # converted into the assistant training target.
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "\n".join(texts) or None,
    }
    if calls:
        message["tool_calls"] = calls
    usage = payload.get("usage")
    normalized_usage = None
    if isinstance(usage, Mapping):
        normalized_usage = {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }
    return {
        "id": payload.get("id"),
        "model": payload.get("model"),
        "choices": [
            {
                "finish_reason": "tool_calls" if calls else "stop",
                "message": message,
            }
        ],
        "usage": normalized_usage,
        "_harness_distill_transport": {
            "api": "responses",
            "provider_reasoning_persisted": False,
        },
    }


def _evidence_refs(sidecar: Mapping[str, Any], messages: Sequence[Mapping[str, Any]]) -> list[dict]:
    refs = sidecar.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise SolDaggerError("capture sidecar requires model-visible evidence_refs")
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, ref in enumerate(refs):
        if not isinstance(ref, Mapping) or set(ref) != {"message_index", "message_sha256"}:
            raise SolDaggerError(
                f"evidence_refs[{index}] requires exactly message_index and message_sha256"
            )
        message_index = ref.get("message_index")
        if type(message_index) is not int or not 0 <= message_index < len(messages):
            raise SolDaggerError(f"evidence_refs[{index}] message index is out of range")
        if message_index in seen:
            raise SolDaggerError("capture sidecar contains duplicate evidence message")
        seen.add(message_index)
        message = messages[message_index]
        if message.get("role") not in {"user", "tool"}:
            raise SolDaggerError("evidence_refs may cite only model-visible user/tool messages")
        expected = sha256_json(message)
        if ref.get("message_sha256") != expected:
            raise SolDaggerError("evidence_ref hash does not match captured Qwen message")
        normalized.append({"message_index": message_index, "message_sha256": expected})
    return normalized


def _capture_key(record: Mapping[str, Any]) -> tuple[str, str, str, int]:
    session = _require_text(record.get("session_sha256"), label="proxy session_sha256")
    request_sha = _require_hex64(record.get("request_sha256"), label="proxy request_sha256")
    effective_sha = _require_hex64(
        record.get("effective_request_sha256"), label="proxy effective_request_sha256"
    )
    sequence = record.get("sequence")
    if type(sequence) is not int or sequence < 0:
        raise SolDaggerError("proxy sequence must be a non-negative integer")
    return session, request_sha, effective_sha, sequence


def _sidecar_key(sidecar: Mapping[str, Any]) -> tuple[str, str, str, int]:
    session = _require_text(sidecar.get("session_sha256"), label="sidecar session_sha256")
    request_sha = _require_hex64(sidecar.get("request_sha256"), label="sidecar request_sha256")
    effective_sha = _require_hex64(
        sidecar.get("effective_request_sha256"), label="sidecar effective_request_sha256"
    )
    sequence = sidecar.get("source_sequence")
    if type(sequence) is not int or sequence < 0:
        raise SolDaggerError("sidecar source_sequence must be a non-negative integer")
    return session, request_sha, effective_sha, sequence


def make_capture_sidecars(
    proxy_records: Iterable[Mapping[str, Any]],
    schedule_records: Iterable[Mapping[str, Any]],
    *,
    campaign_id: str,
    collection_nonce: str,
    harness_fingerprint_sha256: str,
) -> list[dict[str, Any]]:
    """Materialize deterministic capture sidecars from an explicit schedule.

    The schedule selects exact ``(session_sha256, source_sequence)`` pairs and
    supplies phase labels; this function never inspects model output, reward,
    task outcome, hidden catalog data, or evaluator state to choose a turn.
    ``evidence_message_indexes`` must explicitly name user/tool messages from
    the Qwen request.
    """

    campaign = _require_text(campaign_id, label="campaign_id")
    nonce = _require_text(collection_nonce, label="collection_nonce")
    fingerprint = _require_hex64(harness_fingerprint_sha256, label="harness_fingerprint_sha256")
    schedule: dict[tuple[str, int], Mapping[str, Any]] = {}
    for index, row in enumerate(schedule_records):
        if not isinstance(row, Mapping) or row.get("schema") != CAPTURE_SCHEDULE_SCHEMA:
            raise SolDaggerError(f"schedule row {index} has unsupported schema")
        session = _require_text(row.get("session_sha256"), label="schedule session_sha256")
        sequence = row.get("source_sequence")
        if type(sequence) is not int or sequence < 0:
            raise SolDaggerError("schedule source_sequence must be a non-negative integer")
        key = (session, sequence)
        if key in schedule:
            raise SolDaggerError("duplicate capture schedule key")
        schedule[key] = row

    output: list[dict[str, Any]] = []
    used: set[tuple[str, int]] = set()
    for index, proxy in enumerate(proxy_records):
        if not isinstance(proxy, Mapping) or proxy.get("schema") != PROXY_TRACE_SCHEMA:
            raise SolDaggerError(f"proxy row {index} has unsupported schema")
        if proxy.get("role", "student") != "student":
            continue
        session = proxy.get("session_sha256")
        sequence = proxy.get("sequence")
        if not isinstance(session, str) or type(sequence) is not int:
            raise SolDaggerError("proxy row lacks schedule identity")
        key = (session, sequence)
        item = schedule.get(key)
        if item is None:
            continue
        used.add(key)
        status = proxy.get("status_code")
        if type(status) is not int or not 200 <= status < 300:
            raise SolDaggerError("scheduled proxy call was not successful")
        original = proxy.get("request")
        effective = proxy.get("effective_request")
        if not isinstance(original, Mapping) or not isinstance(effective, Mapping):
            raise SolDaggerError("scheduled proxy call lacks exact requests")
        request_sha = sha256_json(original)
        effective_sha = sha256_json(effective)
        if request_sha != proxy.get("request_sha256") or effective_sha != proxy.get(
            "effective_request_sha256"
        ):
            raise SolDaggerError("scheduled proxy request hashes drifted")
        qwen_request = replay_request(effective)
        indexes = item.get("evidence_message_indexes")
        if not isinstance(indexes, list) or not indexes:
            raise SolDaggerError("schedule requires evidence_message_indexes")
        refs: list[dict[str, Any]] = []
        for message_index in indexes:
            if type(message_index) is not int or not 0 <= message_index < len(
                qwen_request["messages"]
            ):
                raise SolDaggerError("scheduled evidence message index is out of range")
            message = qwen_request["messages"][message_index]
            refs.append(
                {
                    "message_index": message_index,
                    "message_sha256": sha256_json(message),
                }
            )
        sidecar = {
            "schema": CAPTURE_SIDECAR_SCHEMA,
            "campaign_id": campaign,
            "collection_nonce": nonce,
            "source_kind": "qwen_on_policy_rollin",
            "harness_id": "browseruse-deliberative",
            "harness_fingerprint_sha256": fingerprint,
            "session_sha256": session,
            "request_sha256": request_sha,
            "effective_request_sha256": effective_sha,
            "source_sequence": sequence,
            "rollout_id": item.get("rollout_id"),
            "split": item.get("split"),
            "variant": item.get("variant"),
            "phase": item.get("phase"),
            "mode": item.get("mode"),
            "captured_at_unix": item.get("captured_at_unix"),
            "evidence_refs": refs,
            **{
                name: item[name]
                for name in (
                    "selection_basis",
                    "selection_quantile",
                    "rollout_bundle_sha256",
                )
                if name in item
            },
        }
        output.append(_validated_capture_sidecar(sidecar, messages=qwen_request["messages"]))
    unused = sorted(set(schedule) - used)
    if unused:
        raise SolDaggerError(f"{len(unused)} schedule rows have no matching proxy call")
    if not output:
        raise SolDaggerError("capture schedule selected no successful Qwen states")
    return output


def make_cleanup_correction_schedule(
    proxy_records: Iterable[Mapping[str, Any]],
    original_schedule_records: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Freeze exact pre-evaluation purchase states for a focused successor.

    Selection is deliberately structural and happens before the new teacher is
    queried.  It uses only train-session identity, the current model-visible
    ``<browser_state>``, and (for PDP states) whether Qwen's rejected proposal
    clicked the *visible* ``Buy Now`` control.  Rewards, task outcomes, hidden
    catalog state, evaluator output, and held-out sessions are never read.

    The frozen pre-evaluation pool is expected to contain eleven PDP ``Buy Now``
    proposals and three carts that visibly contain an accident-protection
    add-on.  Exact counts are a fail-closed guard against silently changing the
    campaign population.
    """

    session_coordinates: dict[str, tuple[str, str, str]] = {}
    for index, row in enumerate(original_schedule_records):
        if not isinstance(row, Mapping) or row.get("schema") != CAPTURE_SCHEDULE_SCHEMA:
            raise SolDaggerError(f"original schedule row {index} has unsupported schema")
        session = _require_text(row.get("session_sha256"), label="schedule session_sha256")
        coordinate = (
            _require_text(row.get("split"), label="schedule split"),
            _require_text(row.get("variant"), label="schedule variant"),
            _require_text(row.get("rollout_id"), label="schedule rollout_id"),
        )
        previous = session_coordinates.setdefault(session, coordinate)
        if previous != coordinate:
            raise SolDaggerError("one proxy session maps to multiple campaign coordinates")

    selected: list[dict[str, Any]] = []
    seen_sessions: set[str] = set()
    kind_counts = {"pdp_buy_now": 0, "cart_addon": 0}
    variant_counts = {variant: 0 for variant in LAPTOP_VARIANTS}
    for index, record in enumerate(proxy_records):
        if not isinstance(record, Mapping) or record.get("schema") != PROXY_TRACE_SCHEMA:
            raise SolDaggerError(f"proxy row {index} has unsupported schema")
        if record.get("role", "student") != "student":
            continue
        status = record.get("status_code")
        if type(status) is not int or not 200 <= status < 300:
            continue
        session = _require_text(record.get("session_sha256"), label="proxy session_sha256")
        coordinate = session_coordinates.get(session)
        if coordinate is None or coordinate[0] != "train":
            continue
        effective = record.get("effective_request")
        response = record.get("response")
        if not isinstance(effective, Mapping) or not isinstance(response, Mapping):
            raise SolDaggerError("successful student proxy row lacks request or response")
        messages = effective.get("messages")
        if not isinstance(messages, list) or not messages:
            raise SolDaggerError("successful student proxy request lacks messages")
        evidence_indexes = [
            message_index
            for message_index, message in enumerate(messages)
            if isinstance(message, Mapping)
            and message.get("role") in {"user", "tool"}
            and isinstance(message.get("content"), str)
            and "<browser_state>" in message["content"]
        ]
        if not evidence_indexes:
            continue
        evidence_index = evidence_indexes[-1]
        content = str(messages[evidence_index]["content"])
        browser_state = content.rsplit("<browser_state>", 1)[-1].split(
            "</browser_state>", 1
        )[0]
        browser_header = browser_state[:600]
        is_cart_addon = (
            "/gp/cart" in browser_header
            and "3-Year Accident Protection Plan" in browser_state
            and re.search(r"\*?\[\d+\]<button[^>]*?/?>\s*Delete", browser_state) is not None
        )

        is_pdp_buy_now = False
        if "/dp/" in browser_header:
            try:
                proposal = _response_message(response, label="Qwen response")
                proposal_content = str(proposal.get("content", ""))
            except SolDaggerError:
                proposal_content = ""
            # Some executed Qwen proposals have one trailing brace after the
            # otherwise-complete action envelope.  Match only the explicit
            # structured click member rather than inferring intent from prose.
            # The selected index must still resolve to the visible Buy Now
            # control in the exact current browser state.
            click_indexes = {
                int(match)
                for match in re.findall(
                    r'"click"\s*:\s*\{\s*"index"\s*:\s*(\d+)', proposal_content
                )
            }
            is_pdp_buy_now = any(
                re.search(rf"\*?\[{click_index}\]<button[^>]*?/?>\s*Buy Now", browser_state)
                for click_index in click_indexes
            )
        if is_cart_addon == is_pdp_buy_now:
            continue
        if session in seen_sessions:
            raise SolDaggerError("cleanup schedule selected multiple states from one session")
        seen_sessions.add(session)
        kind = "cart_addon" if is_cart_addon else "pdp_buy_now"
        split, variant, rollout_id = coordinate
        sequence = record.get("sequence")
        timestamp = record.get("timestamp")
        if type(sequence) is not int or sequence < 0:
            raise SolDaggerError("selected cleanup state lacks a valid sequence")
        captured_at = _require_number(timestamp, label="selected cleanup state timestamp")
        selected.append(
            {
                "schema": CAPTURE_SCHEDULE_SCHEMA,
                "session_sha256": session,
                "source_sequence": sequence,
                "rollout_id": rollout_id,
                "split": split,
                "variant": variant,
                "phase": (
                    "cart_cleanup_recheck" if kind == "cart_addon" else "pdp_evidence_selection"
                ),
                "mode": "shadow",
                "captured_at_unix": captured_at,
                "evidence_message_indexes": [evidence_index],
                "selection_basis": "deterministic_visible_purchase_structure_v1",
                "structural_kind": kind,
            }
        )
        kind_counts[kind] += 1
        variant_counts[variant] += 1

    selected.sort(key=lambda row: (row["variant"], row["rollout_id"], row["source_sequence"]))
    expected_kind_counts = {"pdp_buy_now": 11, "cart_addon": 3}
    expected_variant_counts = {"graded": 4, "graded3": 4, "graded4": 4, "mixed": 2}
    if kind_counts != expected_kind_counts or variant_counts != expected_variant_counts:
        raise SolDaggerError(
            "cleanup structural population drifted: "
            f"kinds={kind_counts}, variants={variant_counts}"
        )
    body = {
        "schema": CLEANUP_SCHEDULE_AUDIT_SCHEMA,
        "status": "frozen",
        "selection_basis": "deterministic_visible_purchase_structure_v1",
        "schedule_rows": len(selected),
        "kind_counts": kind_counts,
        "variant_counts": variant_counts,
        "split_counts": {"train": len(selected), "heldout": 0},
        "selection_inputs": {
            "model_visible_current_browser_state": True,
            "student_proposed_action_for_pdp_only": True,
            "student_proposal_shown_to_teacher": False,
            "reward_or_evaluator": False,
            "task_outcome": False,
            "hidden_catalog_or_database": False,
            "heldout_sessions": False,
        },
        "schedule_sha256": hashlib.sha256(_canonical_jsonl(selected)).hexdigest(),
    }
    return selected, {**body, "audit_sha256": sha256_json(body)}


def _read_jsonl_regular(path: Path, *, label: str) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise SolDaggerError(f"{label} must be a regular non-symlink JSONL: {path}")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SolDaggerError(f"{label} line {line_number} is invalid JSON") from exc
            if not isinstance(value, dict):
                raise SolDaggerError(f"{label} line {line_number} is not an object")
            rows.append(value)
    if not rows:
        raise SolDaggerError(f"{label} is empty")
    return rows


def _is_browseruse_request(record: Mapping[str, Any]) -> bool:
    if record.get("role", "student") != "student":
        return False
    status = record.get("status_code")
    if type(status) is not int or not 200 <= status < 300:
        return False
    request = record.get("effective_request")
    if not isinstance(request, Mapping):
        return False
    messages = request.get("messages")
    if not isinstance(messages, list):
        return False
    return any(
        isinstance(message, Mapping)
        and message.get("role") == "system"
        and isinstance(message.get("content"), str)
        and "<json_schema>" in message["content"]
        and "agent_output" in message["content"]
        for message in messages
    )


def _nearest_unused_position(*, count: int, quantile: float, used: set[int]) -> int:
    target = round((count - 1) * quantile)
    available = [position for position in range(count) if position not in used]
    if not available:
        raise SolDaggerError("rollout has too few distinct BrowserUse states")
    return min(available, key=lambda position: (abs(position - target), position))


def make_bundle_capture_schedule(
    rollout_bundle_path: str | Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Merge 24 trace files and preregister an outcome-blind 64/24 schedule.

    Replicas 0--3 are train and replicas 4--5 are held out before any response
    is read.  Each train rollout contributes four states and each held-out
    rollout three.  Phase slots are globally round-robin balanced, while the
    selected request ordinal is the fixed phase quantile.  Selection reads
    request envelopes only; student responses, rewards, results, and evaluator
    artifacts are never inspected.
    """

    bundle_path = Path(rollout_bundle_path).resolve()
    bundle = _regular_json(bundle_path, label="Sol DAgger rollout bundle")
    body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    if (
        bundle.get("schema") != ROLLOUT_BUNDLE_SCHEMA
        or bundle.get("status") != "preregistered"
        or bundle.get("bundle_sha256") != sha256_json(body)
        or bundle.get("selection", {}).get("score_selection") is not False
        or bundle.get("selection", {}).get("teacher_or_outcome_used_for_render") is not False
        or bundle.get("invariance", {}).get("model_facing_harness_unchanged") is not True
    ):
        raise SolDaggerError("rollout bundle policy or self-hash drifted")
    bundle_rows = bundle.get("rows")
    if not isinstance(bundle_rows, list) or len(bundle_rows) != 24:
        raise SolDaggerError("rollout bundle must contain exactly 24 rows")

    trace_root = (bundle_path.parent / "proxy_traces").resolve()
    merged: list[dict[str, Any]] = []
    schedule: list[dict[str, Any]] = []
    train_phase_ordinal = 0
    holdout_phase_ordinal = 0
    coordinates: set[tuple[str, int]] = set()
    matrix: Counter[tuple[str, str]] = Counter()
    phases: Counter[tuple[str, str]] = Counter()
    for bundle_row in sorted(bundle_rows, key=lambda item: item.get("index", -1)):
        if not isinstance(bundle_row, Mapping):
            raise SolDaggerError("rollout bundle contains a non-object row")
        variant = bundle_row.get("variant")
        replica = bundle_row.get("replica")
        if variant not in LAPTOP_VARIANTS or type(replica) is not int or not 0 <= replica < 6:
            raise SolDaggerError("rollout bundle has invalid variant/replica coordinates")
        coordinate = (str(variant), replica)
        if coordinate in coordinates:
            raise SolDaggerError("rollout bundle duplicates variant/replica coordinates")
        coordinates.add(coordinate)
        trace_path = Path(
            _require_text(bundle_row.get("trace_path"), label="bundle trace_path")
        ).resolve()
        if trace_path.parent != trace_root:
            raise SolDaggerError("rollout trace escapes the frozen proxy_traces directory")
        trace = _read_jsonl_regular(trace_path, label=f"rollout trace {variant}/r{replica:02d}")
        merged.extend(_json_clone(trace, label="proxy trace rows"))
        candidates = [row for row in trace if _is_browseruse_request(row)]
        split = "train" if replica < 4 else "holdout"
        selected_count = 4 if split == "train" else 3
        if len(candidates) < selected_count:
            raise SolDaggerError(
                f"rollout {variant}/r{replica:02d} has fewer than {selected_count} "
                "successful BrowserUse requests"
            )
        assigned_phases: list[str] = []
        for _ in range(selected_count):
            if split == "train":
                phase = DECISION_PHASES[train_phase_ordinal % len(DECISION_PHASES)]
                train_phase_ordinal += 1
            else:
                phase = DECISION_PHASES[holdout_phase_ordinal % len(DECISION_PHASES)]
                holdout_phase_ordinal += 1
            assigned_phases.append(phase)
        used_positions: set[int] = set()
        for phase in assigned_phases:
            position = _nearest_unused_position(
                count=len(candidates),
                quantile=_PHASE_QUANTILES[phase],
                used=used_positions,
            )
            used_positions.add(position)
            proxy = candidates[position]
            session = _require_text(
                proxy.get("session_sha256"), label="scheduled proxy session_sha256"
            )
            sequence = proxy.get("sequence")
            timestamp = proxy.get("timestamp")
            if type(sequence) is not int or sequence < 0:
                raise SolDaggerError("scheduled proxy sequence is invalid")
            captured_at = _require_number(timestamp, label="scheduled proxy timestamp")
            qwen_request = replay_request(proxy["effective_request"])
            evidence_indexes = [
                index
                for index, message in enumerate(qwen_request["messages"])
                if message.get("role") in {"user", "tool"}
            ]
            if not evidence_indexes:
                raise SolDaggerError("scheduled BrowserUse request has no visible user/tool state")
            schedule.append(
                {
                    "schema": CAPTURE_SCHEDULE_SCHEMA,
                    "session_sha256": session,
                    "source_sequence": sequence,
                    "rollout_id": bundle_row.get("run_id"),
                    "split": split,
                    "variant": variant,
                    "phase": phase,
                    "mode": "shadow",
                    "captured_at_unix": captured_at,
                    "evidence_message_indexes": [evidence_indexes[-1]],
                    "selection_basis": "preregistered_request_ordinal_quantile_v1",
                    "selection_quantile": _PHASE_QUANTILES[phase],
                    "rollout_bundle_sha256": bundle["bundle_sha256"],
                }
            )
            matrix[(split, str(variant))] += 1
            phases[(split, phase)] += 1

    if coordinates != {(variant, replica) for variant in LAPTOP_VARIANTS for replica in range(6)}:
        raise SolDaggerError("rollout bundle matrix is incomplete")
    if len(schedule) != 88 or any(
        matrix[("train", variant)] != 16 or matrix[("holdout", variant)] != 6
        for variant in LAPTOP_VARIANTS
    ):
        raise SolDaggerError("deterministic schedule does not satisfy exact 64/24 balance")
    if any(
        phases[("train", phase)] < 10 or phases[("holdout", phase)] != 4
        for phase in DECISION_PHASES
    ):
        raise SolDaggerError("deterministic schedule does not satisfy phase coverage")
    audit = {
        "schema": "harness-distill.sol-dagger-bundle-schedule-audit.v1",
        "rollout_bundle_path": str(bundle_path),
        "rollout_bundle_sha256": bundle["bundle_sha256"],
        "proxy_rows": len(merged),
        "scheduled_rows": len(schedule),
        "selection_reads_student_response": False,
        "selection_reads_reward_or_outcome": False,
        "selection_reads_evaluator": False,
        "split_assignment": "replicas_0_to_3_train_4_to_5_holdout",
        "mode": "shadow",
        "variant_counts": {
            variant: {
                "train": matrix[("train", variant)],
                "holdout": matrix[("holdout", variant)],
            }
            for variant in LAPTOP_VARIANTS
        },
        "phase_counts": {
            phase: {
                "train": phases[("train", phase)],
                "holdout": phases[("holdout", phase)],
            }
            for phase in DECISION_PHASES
        },
    }
    return merged, schedule, audit


def make_structural_reserve_schedule(
    proxy_records: Iterable[Mapping[str, Any]],
    original_schedule_records: Iterable[Mapping[str, Any]],
    original_labeled_states: Iterable[Mapping[str, Any]],
    original_action_validations: Iterable[Mapping[str, Any]],
    *,
    rollout_bundle_sha256: str,
    required_deficits: int | None = None,
    fixed_redundancy: int = 1,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Freeze an outcome-blind reserve for structurally rejected train labels.

    Each exact rejected ``(variant, phase)`` coordinate receives two ordered
    candidates: another request from the rejected state's session and one from
    a deterministic train session where that phase was not originally used.
    A fixed extra candidate is assigned to the first canonical deficit with a
    second phase-unused session.  Candidate choice reads only successful Qwen
    request envelopes and request ordinals; response payloads and all outcome
    artifacts are outside this function's selection boundary.
    """

    bundle_sha = _require_hex64(rollout_bundle_sha256, label="reserve rollout_bundle_sha256")
    if required_deficits is not None and (
        type(required_deficits) is not int or required_deficits < 1
    ):
        raise SolDaggerError("required_deficits must be a positive integer")
    if type(fixed_redundancy) is not int or fixed_redundancy not in {0, 1}:
        raise SolDaggerError("fixed_redundancy must be zero or one")

    proxy = list(proxy_records)
    schedule = list(original_schedule_records)
    labeled = list(original_labeled_states)
    validations = _validation_index(original_action_validations)
    if not proxy or not schedule or not labeled:
        raise SolDaggerError("structural reserve inputs must be non-empty")

    schedule_by_key: dict[tuple[str, int], tuple[int, Mapping[str, Any]]] = {}
    original_session_phases: set[tuple[str, str]] = set()
    train_sessions: dict[str, dict[str, str]] = defaultdict(dict)
    for ordinal, row in enumerate(schedule):
        if not isinstance(row, Mapping) or row.get("schema") != CAPTURE_SCHEDULE_SCHEMA:
            raise SolDaggerError(f"original schedule row {ordinal} has unsupported schema")
        session = _require_text(row.get("session_sha256"), label="schedule session_sha256")
        sequence = row.get("source_sequence")
        if type(sequence) is not int or sequence < 0:
            raise SolDaggerError("original schedule sequence is invalid")
        key = (session, sequence)
        if key in schedule_by_key:
            raise SolDaggerError("original schedule duplicates a Qwen state")
        schedule_by_key[key] = (ordinal, row)
        phase = row.get("phase")
        variant = row.get("variant")
        split = row.get("split")
        if phase not in DECISION_PHASES or variant not in LAPTOP_VARIANTS or split not in SPLITS:
            raise SolDaggerError("original schedule has an unsupported coordinate")
        original_session_phases.add((session, str(phase)))
        if split == "train":
            rollout = _require_text(row.get("rollout_id"), label="schedule rollout_id")
            previous = train_sessions[str(variant)].get(session)
            if previous is not None and previous != rollout:
                raise SolDaggerError("train session maps to multiple rollout ids")
            train_sessions[str(variant)][session] = rollout

    labels_by_state: dict[str, Mapping[str, Any]] = {}
    rejected: list[tuple[int, Mapping[str, Any], Mapping[str, Any]]] = []
    original_status_counts: Counter[tuple[str, str]] = Counter()
    for index, row in enumerate(labeled):
        if not isinstance(row, Mapping) or row.get("schema") not in {
            SHADOW_LABEL_SCHEMA,
            INTERVENTION_LABEL_SCHEMA,
        }:
            raise SolDaggerError(f"original label row {index} has unsupported schema")
        state_id = _require_hex64(row.get("state_id"), label="original label state_id")
        if state_id in labels_by_state:
            raise SolDaggerError("original labels duplicate a state_id")
        labels_by_state[state_id] = row
        validation = validations.get(state_id)
        if validation is None:
            raise SolDaggerError("original label has no action validation")
        accepted, _audit = _validate_action_sidecar(validation, row)
        capture = row.get("capture")
        if not isinstance(capture, Mapping):
            raise SolDaggerError("original label lacks capture metadata")
        split = capture.get("split")
        original_status_counts[(str(split), "accepted" if accepted else "rejected")] += 1
        key = (row.get("session_sha256"), row.get("source_sequence"))
        scheduled = schedule_by_key.get(key)  # type: ignore[arg-type]
        if scheduled is None:
            raise SolDaggerError("original label is absent from the frozen schedule")
        ordinal, schedule_row = scheduled
        for name in ("rollout_id", "split", "variant", "phase", "mode"):
            if capture.get(name) != schedule_row.get(name):
                raise SolDaggerError(f"original label {name} differs from schedule")
        if not accepted:
            if split != "train":
                raise SolDaggerError("structural reserve cannot replace a held-out label")
            rejected.append((ordinal, row, validation))
    if set(validations) != set(labels_by_state):
        raise SolDaggerError("original validations and labels do not have one-to-one identity")
    if required_deficits is not None and len(rejected) != required_deficits:
        raise SolDaggerError(
            f"structural reserve expected {required_deficits} deficits, found {len(rejected)}"
        )
    if not rejected:
        raise SolDaggerError("structural reserve has no rejected train coordinates")
    rejected.sort(key=lambda item: (item[0], str(item[1]["state_id"])))

    successful_by_session: dict[str, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for record in proxy:
        if not isinstance(record, Mapping) or record.get("schema") != PROXY_TRACE_SCHEMA:
            raise SolDaggerError("reserve proxy input contains an unsupported row")
        if not _is_browseruse_request(record):
            continue
        session = _require_text(record.get("session_sha256"), label="proxy session_sha256")
        sequence = record.get("sequence")
        if type(sequence) is not int or sequence < 0:
            raise SolDaggerError("successful reserve proxy row has an invalid sequence")
        successful_by_session[session].append((sequence, record))
    for rows in successful_by_session.values():
        rows.sort(key=lambda item: item[0])
        if len({sequence for sequence, _record in rows}) != len(rows):
            raise SolDaggerError("successful proxy rows duplicate a session sequence")

    original_keys = set(schedule_by_key)
    reserve_keys: set[tuple[str, int]] = set()
    reserve_session_phases: set[tuple[str, str]] = set()
    output: list[dict[str, Any]] = []
    deficits: list[dict[str, Any]] = []

    def choose_record(session: str, phase: str) -> Mapping[str, Any]:
        rows = successful_by_session.get(session, [])
        if not rows:
            raise SolDaggerError("reserve session has no successful BrowserUse requests")
        target = round((len(rows) - 1) * _PHASE_QUANTILES[phase])
        available = [
            (position, sequence, row)
            for position, (sequence, row) in enumerate(rows)
            if (session, sequence) not in original_keys and (session, sequence) not in reserve_keys
        ]
        if not available:
            raise SolDaggerError("reserve session has no unused successful Qwen state")
        _position, sequence, row = min(available, key=lambda item: (abs(item[0] - target), item[1]))
        reserve_keys.add((session, sequence))
        return row

    def append_candidate(
        *,
        deficit: dict[str, Any],
        session: str,
        reserve_rank: int,
    ) -> None:
        phase = str(deficit["phase"])
        session_phase = (session, phase)
        if session_phase in reserve_session_phases:
            raise SolDaggerError("reserve duplicates a phase within one Qwen session")
        reserve_session_phases.add(session_phase)
        record = choose_record(session, phase)
        effective = record.get("effective_request")
        if not isinstance(effective, Mapping):
            raise SolDaggerError("reserve candidate lacks an effective request")
        qwen_request = replay_request(effective)
        evidence_indexes = [
            index
            for index, message in enumerate(qwen_request["messages"])
            if message.get("role") in {"user", "tool"}
        ]
        if not evidence_indexes:
            raise SolDaggerError("reserve candidate has no visible user/tool state")
        sequence = record.get("sequence")
        timestamp = _require_number(record.get("timestamp"), label="reserve proxy timestamp")
        output.append(
            {
                "schema": CAPTURE_SCHEDULE_SCHEMA,
                "session_sha256": session,
                "source_sequence": sequence,
                "rollout_id": train_sessions[str(deficit["variant"])][session],
                "split": "train",
                "variant": deficit["variant"],
                "phase": phase,
                "mode": "shadow",
                "captured_at_unix": timestamp,
                "evidence_message_indexes": [evidence_indexes[-1]],
                "selection_basis": "deterministic_structural_deficit_reserve_v1",
                "selection_quantile": _PHASE_QUANTILES[phase],
                "rollout_bundle_sha256": bundle_sha,
                "deficit_id": deficit["deficit_id"],
                "replaces_state_id": deficit["replaces_state_id"],
                "reserve_rank": reserve_rank,
                "reserve_order": len(output),
            }
        )

    for _ordinal, row, _validation in rejected:
        capture = row["capture"]
        variant = str(capture["variant"])
        phase = str(capture["phase"])
        primary_session = str(row["session_sha256"])
        deficit = {
            "deficit_id": hashlib.sha256(f"reserve:{row['state_id']}".encode()).hexdigest(),
            "replaces_state_id": row["state_id"],
            "original_schedule_ordinal": _ordinal,
            "variant": variant,
            "phase": phase,
            "primary_session_sha256": primary_session,
        }
        eligible = [
            session
            for session, rollout in sorted(
                train_sessions[variant].items(), key=lambda item: (item[1], item[0])
            )
            if session != primary_session
            and (session, phase) not in original_session_phases
            and (session, phase) not in reserve_session_phases
        ]
        if not eligible:
            raise SolDaggerError("deficit has no phase-unused alternate train session")
        deficit["alternate_sessions"] = eligible
        append_candidate(deficit=deficit, session=primary_session, reserve_rank=0)
        append_candidate(deficit=deficit, session=eligible[0], reserve_rank=1)
        deficits.append(deficit)

    fixed_deficit_id: str | None = None
    if fixed_redundancy:
        canonical_deficits = sorted(
            deficits,
            key=lambda item: (
                LAPTOP_VARIANTS.index(str(item["variant"])),
                DECISION_PHASES.index(str(item["phase"])),
                str(item["deficit_id"]),
            ),
        )
        for deficit in canonical_deficits:
            remaining = [
                session
                for session in deficit["alternate_sessions"]
                if (session, str(deficit["phase"])) not in reserve_session_phases
            ]
            if not remaining:
                continue
            append_candidate(deficit=deficit, session=remaining[0], reserve_rank=2)
            fixed_deficit_id = str(deficit["deficit_id"])
            break
        if fixed_deficit_id is None:
            raise SolDaggerError("no valid coordinate can receive fixed reserve redundancy")

    expected = 2 * len(deficits) + fixed_redundancy
    if len(output) != expected or len(reserve_keys) != expected:
        raise SolDaggerError("structural reserve candidate matrix is incomplete")
    if len(reserve_session_phases) != expected:
        raise SolDaggerError("structural reserve duplicates a session/phase")
    for deficit in deficits:
        deficit.pop("alternate_sessions", None)
        deficit["candidate_count"] = sum(
            row["deficit_id"] == deficit["deficit_id"] for row in output
        )
    body = {
        "schema": STRUCTURAL_RESERVE_AUDIT_SCHEMA,
        "status": "frozen_before_teacher_calls",
        "rollout_bundle_sha256": bundle_sha,
        "selection_basis": "deterministic_structural_deficit_reserve_v1",
        "selection_reads_qwen_response": False,
        "selection_reads_reward_or_outcome": False,
        "selection_reads_run_result_or_database": False,
        "selection_reads_evaluator": False,
        "same_state_rerolls": 0,
        "split": "train",
        "deficit_count": len(deficits),
        "candidate_count": len(output),
        "candidates_per_deficit": 2,
        "fixed_redundancy": fixed_redundancy,
        "fixed_redundancy_deficit_id": fixed_deficit_id,
        "no_duplicate_phase_per_reserve_session": True,
        "original_status_counts": {
            f"{split}:{status}": count
            for (split, status), count in sorted(original_status_counts.items())
        },
        "deficits": deficits,
        "candidates": [
            {
                name: row[name]
                for name in (
                    "reserve_order",
                    "reserve_rank",
                    "deficit_id",
                    "replaces_state_id",
                    "session_sha256",
                    "source_sequence",
                    "rollout_id",
                    "variant",
                    "phase",
                    "selection_quantile",
                )
            }
            for row in output
        ],
        "schedule_rows": len(output),
        "schedule_sha256": hashlib.sha256(_canonical_jsonl(output)).hexdigest(),
    }
    return output, {**body, "reserve_audit_sha256": sha256_json(body)}


def _validated_capture_sidecar(
    sidecar: Mapping[str, Any],
    *,
    messages: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if sidecar.get("schema") != CAPTURE_SIDECAR_SCHEMA:
        raise SolDaggerError("unsupported Sol DAgger capture sidecar schema")
    normalized = _json_clone(dict(sidecar), label="capture sidecar")
    for name in (
        "campaign_id",
        "collection_nonce",
        "rollout_id",
        "harness_fingerprint_sha256",
    ):
        _require_text(normalized.get(name), label=f"capture {name}")
    _require_hex64(
        normalized["harness_fingerprint_sha256"],
        label="capture harness_fingerprint_sha256",
    )
    if normalized.get("source_kind") != "qwen_on_policy_rollin":
        raise SolDaggerError("capture source_kind must be qwen_on_policy_rollin")
    if normalized.get("harness_id") != "browseruse-deliberative":
        raise SolDaggerError("capture harness_id must be browseruse-deliberative")
    if normalized.get("variant") not in LAPTOP_VARIANTS:
        raise SolDaggerError("capture variant is not a frozen laptop variant")
    if normalized.get("phase") not in DECISION_PHASES:
        raise SolDaggerError("capture phase is unsupported")
    if normalized.get("mode") not in COLLECTION_MODES:
        raise SolDaggerError("capture mode must be shadow or intervention")
    if normalized.get("split") not in SPLITS:
        raise SolDaggerError("capture split must be train or holdout")
    normalized["captured_at_unix"] = _require_number(
        normalized.get("captured_at_unix"), label="capture captured_at_unix"
    )
    normalized["evidence_refs"] = _evidence_refs(normalized, messages)
    return normalized


def capture_qwen_states(
    proxy_records: Iterable[Mapping[str, Any]],
    capture_sidecars: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Join exact Qwen calls to sidecar-only collection metadata."""

    indexed: dict[tuple[str, str, str, int], Mapping[str, Any]] = {}
    for index, sidecar in enumerate(capture_sidecars):
        if not isinstance(sidecar, Mapping):
            raise SolDaggerError(f"capture sidecar row {index} must be an object")
        key = _sidecar_key(sidecar)
        if key in indexed:
            raise SolDaggerError("duplicate Sol DAgger capture sidecar key")
        indexed[key] = sidecar

    states: list[dict[str, Any]] = []
    used: set[tuple[str, str, str, int]] = set()
    last_sequence: dict[str, int] = {}
    phase_per_session: set[tuple[str, str]] = set()
    for index, record in enumerate(proxy_records):
        if not isinstance(record, Mapping):
            raise SolDaggerError(f"proxy row {index} must be an object")
        if record.get("schema") != PROXY_TRACE_SCHEMA:
            raise SolDaggerError(f"proxy row {index} has unsupported schema")
        if record.get("role", "student") != "student":
            continue
        status = record.get("status_code")
        if type(status) is not int or not 200 <= status < 300:
            continue
        key = _capture_key(record)
        if key not in indexed:
            continue
        if key in used:
            raise SolDaggerError("duplicate selected proxy state")
        used.add(key)
        session, request_sha, effective_sha, sequence = key
        if sequence <= last_sequence.get(session, -1):
            raise SolDaggerError("selected states are not causal within a Qwen session")
        last_sequence[session] = sequence

        original = record.get("request")
        effective = record.get("effective_request")
        response = record.get("response")
        if not isinstance(original, Mapping) or not isinstance(effective, Mapping):
            raise SolDaggerError("selected proxy state lacks original/effective request")
        if not isinstance(response, Mapping):
            raise SolDaggerError("selected proxy state lacks Qwen response")
        if sha256_json(original) != request_sha or sha256_json(effective) != effective_sha:
            raise SolDaggerError("selected proxy request bytes changed after capture")
        # Validate both request dialects through the same PRIME boundary used by
        # existing materialization.  Training uses the effective messages Qwen
        # actually received.
        replay_request(original)
        qwen_request = replay_request(effective)
        if infer_turn_index(qwen_request["messages"]) != infer_turn_index(
            replay_request(original)["messages"]
        ):
            raise SolDaggerError("Qwen effective request changes causal turn index")

        sidecar = _validated_capture_sidecar(indexed[key], messages=qwen_request["messages"])
        session_phase = (session, sidecar["phase"])
        if session_phase in phase_per_session:
            raise SolDaggerError("a Qwen episode selected more than one state for the same phase")
        phase_per_session.add(session_phase)

        student_message = _response_message(response, label="Qwen response")
        if any(
            sha256_json(message) == sha256_json(student_message)
            for message in qwen_request["messages"]
        ):
            raise SolDaggerError("current Qwen proposal already appears in teacher prefix")
        state_id = hashlib.sha256(
            f"{session}:{request_sha}:{effective_sha}:{sequence}".encode()
        ).hexdigest()
        states.append(
            {
                "schema": CAPTURED_STATE_SCHEMA,
                "state_id": state_id,
                "session_sha256": session,
                "source_sequence": sequence,
                "source_turn_index": infer_turn_index(qwen_request["messages"]),
                "request_sha256": request_sha,
                "effective_request_sha256": effective_sha,
                "qwen_harness_projection_sha256": sha256_json(_harness_projection(qwen_request)),
                "qwen_request": qwen_request,
                "qwen_completion": student_message,
                "qwen_completion_sha256": sha256_json(student_message),
                "capture": sidecar,
            }
        )
    unused = sorted(set(indexed) - used)
    if unused:
        raise SolDaggerError(f"{len(unused)} capture sidecars do not match a successful Qwen call")
    if not states:
        raise SolDaggerError("no Qwen states were selected for Sol DAgger")
    return states


class SolLowTeacher(Protocol):
    """A structured Sol-low caller; tests may provide an in-memory implementation."""

    @property
    def wire_model(self) -> str: ...

    def descriptor(self) -> Mapping[str, Any]: ...

    async def complete(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(slots=True)
class AgentArenaSolLowTeacher:
    """TRAPI Sol-low caller using AgentArena's deployment and credential path."""

    timeout_seconds: float = 600.0
    max_completion_tokens: int = 8192
    _clients: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if type(self.max_completion_tokens) is not int or self.max_completion_tokens < 1:
            raise ValueError("max_completion_tokens must be a positive integer")

    @property
    def wire_model(self) -> str:
        try:
            from agentarena.llm_client import TRAPI_DEPLOY
        except ImportError as exc:  # pragma: no cover - deployment environment failure
            raise SolDaggerError("AgentArena is required for TRAPI Sol labeling") from exc
        return TRAPI_DEPLOY[SOL_LOGICAL_MODEL]

    def descriptor(self) -> Mapping[str, Any]:
        return {
            "provider": "trapi",
            "model_spec": SOL_MODEL_SPEC,
            "logical_model": SOL_LOGICAL_MODEL,
            "wire_model": self.wire_model,
            "reasoning_effort": SOL_REASONING_EFFORT,
        }

    async def complete(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            from agentarena.llm_client import (
                TRAPI_MODEL_REGIONS,
                _trapi_base_url,
                create_client,
            )
        except ImportError as exc:  # pragma: no cover - deployment environment failure
            raise SolDaggerError("AgentArena is required for TRAPI Sol labeling") from exc

        regions = tuple(TRAPI_MODEL_REGIONS.get(SOL_LOGICAL_MODEL, ()))
        if not regions:
            raise SolDaggerError("AgentArena has no TRAPI regions for gpt-5.6-sol")
        payload = _json_clone(dict(request), label="Sol wire request")
        responses_payload = make_sol_low_responses_request(payload)
        start = int(sha256_json(payload)[:8], 16) % len(regions)
        ordered = (*regions[start:], *regions[:start])
        errors: list[str] = []
        for region in ordered:
            client = self._clients.get(region)
            if client is None:
                client, _model = create_client(
                    model=self.wire_model,
                    base_url=_trapi_base_url(region),
                )
                self._clients[region] = client
            try:
                response = await asyncio.wait_for(
                    client.responses.create(**responses_payload),
                    timeout=self.timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001 - region failover boundary
                errors.append(f"{region}:{type(exc).__name__}:{str(exc)[:200]}")
                continue
            dumped = response.model_dump(mode="json", exclude_none=True)
            if not isinstance(dumped, Mapping):
                raise SolDaggerError("TRAPI Sol response did not serialize to an object")
            adapted = _adapt_responses_payload(dumped)
            adapted["_harness_distill_transport"]["request_sha256"] = sha256_json(responses_payload)
            adapted["_harness_distill_transport"]["region"] = region
            return adapted
        raise SolDaggerError("all TRAPI Sol regions failed: " + " | ".join(errors))

    async def close(self) -> None:
        clients, self._clients = self._clients, {}
        await asyncio.gather(
            *(client.close() for client in clients.values()),
            return_exceptions=True,
        )


def _validated_teacher_descriptor(teacher: SolLowTeacher) -> dict[str, Any]:
    descriptor = _json_clone(dict(teacher.descriptor()), label="teacher descriptor")
    expected = {
        "provider": "trapi",
        "model_spec": SOL_MODEL_SPEC,
        "logical_model": SOL_LOGICAL_MODEL,
        "wire_model": teacher.wire_model,
        "reasoning_effort": SOL_REASONING_EFFORT,
    }
    if descriptor != expected:
        raise SolDaggerError("teacher descriptor is not exactly gpt-5.6-sol#low over TRAPI")
    return descriptor


@dataclass(frozen=True, slots=True)
class SolLabelBatch:
    rows: tuple[dict[str, Any], ...]
    teacher_calls: int


async def label_qwen_states(
    states: Iterable[Mapping[str, Any]],
    teacher: SolLowTeacher,
    *,
    concurrency: int = 16,
    max_completion_tokens: int = 8192,
) -> SolLabelBatch:
    """Query Sol once per Qwen-reached state and retain no provider reasoning."""

    if type(concurrency) is not int or concurrency < 1:
        raise SolDaggerError("teacher concurrency must be a positive integer")
    descriptor = _validated_teacher_descriptor(teacher)
    materialized = list(states)
    ids: set[str] = set()
    for index, state in enumerate(materialized):
        if not isinstance(state, Mapping) or state.get("schema") != CAPTURED_STATE_SCHEMA:
            raise SolDaggerError(f"state row {index} is not a captured Qwen state")
        state_id = _require_hex64(state.get("state_id"), label=f"state row {index} state_id")
        if state_id in ids:
            raise SolDaggerError("duplicate captured Qwen state_id")
        ids.add(state_id)

    semaphore = asyncio.Semaphore(concurrency)

    async def one(state: Mapping[str, Any]) -> dict[str, Any]:
        qwen_request = state.get("qwen_request")
        if not isinstance(qwen_request, Mapping):
            raise SolDaggerError("captured state has no Qwen request")
        teacher_request, invariance = make_sol_low_request(
            qwen_request,
            wire_model=teacher.wire_model,
            max_completion_tokens=max_completion_tokens,
        )
        # The current Qwen action is rejected-state evidence only.  It must not
        # be shown to Sol, which would turn independent supervision into
        # proposal editing.
        qwen_completion = state.get("qwen_completion")
        if isinstance(qwen_completion, Mapping) and any(
            sha256_json(message) == sha256_json(qwen_completion)
            for message in teacher_request["messages"]
        ):
            raise SolDaggerError("current Qwen proposal entered Sol request")
        async with semaphore:
            response = await teacher.complete(teacher_request)
        if not isinstance(response, Mapping):
            raise SolDaggerError("Sol teacher response must be an object")
        completion = strip_provider_reasoning(
            _response_message(response, label="Sol response"),
            baseline_request=qwen_request,
        )
        capture = state.get("capture")
        if not isinstance(capture, Mapping):
            raise SolDaggerError("captured state has no collection sidecar")
        mode = capture.get("mode")
        schema = SHADOW_LABEL_SCHEMA if mode == "shadow" else INTERVENTION_LABEL_SCHEMA
        return {
            "schema": schema,
            "state_id": state["state_id"],
            "session_sha256": state["session_sha256"],
            "source_sequence": state["source_sequence"],
            "source_turn_index": state["source_turn_index"],
            "request_sha256": state["request_sha256"],
            "effective_request_sha256": state["effective_request_sha256"],
            "qwen_harness_projection_sha256": state["qwen_harness_projection_sha256"],
            "qwen_request": deepcopy(dict(qwen_request)),
            "qwen_completion": deepcopy(dict(qwen_completion)),
            "qwen_completion_sha256": state["qwen_completion_sha256"],
            "capture": deepcopy(dict(capture)),
            "teacher": descriptor,
            "teacher_request_sha256": sha256_json(teacher_request),
            "teacher_response_sha256": sha256_json(response),
            "teacher_response_id": response.get("id"),
            "teacher_response_model": response.get("model"),
            "teacher_usage": deepcopy(response.get("usage")),
            "teacher_completion": completion,
            "teacher_completion_sha256": sha256_json(completion),
            "invariance_audit": invariance,
        }

    results = await asyncio.gather(*(one(state) for state in materialized), return_exceptions=True)
    failures = [result for result in results if isinstance(result, BaseException)]
    if failures:
        first = failures[0]
        if isinstance(first, SolDaggerError):
            raise first
        raise SolDaggerError(f"Sol labeling failed: {first}") from first
    rows = tuple(result for result in results if isinstance(result, dict))
    return SolLabelBatch(rows=rows, teacher_calls=len(rows))


@dataclass(frozen=True, slots=True)
class CampaignPolicy:
    """Frozen balance/freshness contract for the eight-hour campaign."""

    campaign_id: str
    collection_nonce: str
    harness_fingerprint_sha256: str
    not_before_unix: float
    maximum_age_seconds: float = 8 * 60 * 60
    target_train_labels: int = 64
    target_holdout_labels: int = 24
    minimum_train_labels: int = 64
    minimum_holdout_labels: int = 24
    maximum_total_labels: int = 96
    minimum_train_per_variant: int = 16
    minimum_holdout_per_variant: int = 6
    minimum_train_per_phase: int = 10
    minimum_holdout_per_phase: int = 4
    maximum_exclusion_fraction: float = 0.25
    minimum_rollouts_per_variant: int = 0
    minimum_shadow_rollouts: int = 8
    minimum_intervention_rollouts: int = 0
    required_variants: tuple[str, ...] = LAPTOP_VARIANTS
    required_phases: tuple[str, ...] = DECISION_PHASES

    def __post_init__(self) -> None:
        _require_text(self.campaign_id, label="policy campaign_id")
        _require_text(self.collection_nonce, label="policy collection_nonce")
        _require_hex64(
            self.harness_fingerprint_sha256,
            label="policy harness_fingerprint_sha256",
        )
        _require_number(self.not_before_unix, label="policy not_before_unix")
        if self.maximum_age_seconds <= 0:
            raise ValueError("maximum_age_seconds must be positive")
        for name in (
            "target_train_labels",
            "target_holdout_labels",
            "minimum_train_labels",
            "minimum_holdout_labels",
            "maximum_total_labels",
            "minimum_train_per_variant",
            "minimum_holdout_per_variant",
            "minimum_train_per_phase",
            "minimum_holdout_per_phase",
            "minimum_rollouts_per_variant",
            "minimum_shadow_rollouts",
            "minimum_intervention_rollouts",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.maximum_total_labels < self.minimum_train_labels + self.minimum_holdout_labels:
            raise ValueError("maximum_total_labels is smaller than the required splits")
        if not 0 <= self.maximum_exclusion_fraction < 1:
            raise ValueError("maximum_exclusion_fraction must be in [0, 1)")
        if set(self.required_variants) - set(LAPTOP_VARIANTS):
            raise ValueError("policy contains unsupported variants")
        if set(self.required_phases) - set(DECISION_PHASES):
            raise ValueError("policy contains unsupported phases")


_JSON_SCHEMA_BLOCK = re.compile(r"<json_schema>\s*(.*?)\s*</json_schema>", re.DOTALL)


def static_visible_validator_sha256() -> str:
    """Return the byte identity of the static validator implementation."""

    return _sha256_file(Path(__file__).resolve())


def _browseruse_output_schema(messages: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    for message in messages:
        if message.get("role") != "system" or not isinstance(message.get("content"), str):
            continue
        match = _JSON_SCHEMA_BLOCK.search(message["content"])
        if match is None:
            continue
        try:
            descriptor = ast.literal_eval(match.group(1))
        except (SyntaxError, ValueError) as exc:
            raise SolDaggerError("frozen BrowserUse output schema is not a literal") from exc
        if not isinstance(descriptor, Mapping) or not isinstance(descriptor.get("schema"), Mapping):
            raise SolDaggerError("frozen BrowserUse output schema descriptor is invalid")
        return _json_clone(dict(descriptor["schema"]), label="BrowserUse output schema")
    raise SolDaggerError("Qwen request has no frozen BrowserUse output schema")


def _schema_errors(instance: Any, schema: Mapping[str, Any]) -> list[str]:
    try:
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError
    except ImportError as exc:  # pragma: no cover - deployment environment failure
        raise SolDaggerError("jsonschema is required for static action validation") from exc
    try:
        validator = Draft202012Validator(dict(schema))
    except SchemaError as exc:
        raise SolDaggerError("frozen BrowserUse JSON schema is invalid") from exc
    return [
        f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(
            validator.iter_errors(instance), key=lambda item: list(item.absolute_path)
        )
    ]


def _action_element_indices(actions: Sequence[Mapping[str, Any]]) -> list[int]:
    indices: list[int] = []

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for name, item in value.items():
                if name == "index" and type(item) is int:
                    indices.append(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(actions)
    return indices


def _visible_evidence_text(
    messages: Sequence[Mapping[str, Any]], evidence_refs: Sequence[Mapping[str, Any]]
) -> str:
    chunks: list[str] = []
    for ref in evidence_refs:
        message = messages[ref["message_index"]]
        content = message.get("content")
        chunks.append(content if isinstance(content, str) else canonical_json(content))
    return "\n".join(chunks)


def make_static_visible_action_validations(
    labeled_states: Iterable[Mapping[str, Any]],
    *,
    validated_at_unix: float,
    validator_sha256: str | None = None,
) -> list[dict[str, Any]]:
    """Validate offline Sol actions without claiming browser execution.

    This tier proves only that the assistant output satisfies the exact schema
    embedded in Qwen's frozen BrowserUse prompt and that every action element
    index exists in the sidecar-bound visible messages.  It never opens a
    browser, checks an evaluator, or infers a postcondition.  Consequently,
    ``execution_verified`` and ``postcondition_verified`` are always false.
    """

    timestamp = _require_number(validated_at_unix, label="validated_at_unix")
    validator = _require_hex64(
        validator_sha256 or static_visible_validator_sha256(),
        label="validator_sha256",
    )
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, labeled in enumerate(labeled_states):
        if not isinstance(labeled, Mapping) or labeled.get("schema") not in {
            SHADOW_LABEL_SCHEMA,
            INTERVENTION_LABEL_SCHEMA,
        }:
            raise SolDaggerError(f"static validation row {index} is not a Sol label")
        state_id = _require_hex64(
            labeled.get("state_id"), label=f"static validation row {index} state_id"
        )
        if state_id in seen:
            raise SolDaggerError("static validation received a duplicate state")
        seen.add(state_id)
        request = labeled.get("qwen_request")
        completion = labeled.get("teacher_completion")
        capture = labeled.get("capture")
        if not all(isinstance(value, Mapping) for value in (request, completion, capture)):
            raise SolDaggerError("static validation label is structurally incomplete")
        if capture.get("mode") != "shadow":
            raise SolDaggerError("offline static validation cannot certify an intervention capture")
        messages = request.get("messages")
        if not isinstance(messages, list):
            raise SolDaggerError("static validation request has no messages")
        refs = _evidence_refs(capture, messages)
        schema = _browseruse_output_schema(messages)
        content = completion.get("content")
        decoded: Any = None
        parse_error: str | None = None
        if completion.get("tool_calls"):
            parse_error = "BrowserUse completion unexpectedly used native tool_calls"
        elif not isinstance(content, str):
            parse_error = "BrowserUse completion content is not text"
        else:
            try:
                decoded = json.loads(content)
            except json.JSONDecodeError as exc:
                parse_error = f"BrowserUse completion is not exact JSON: {exc.msg}"
        errors = [parse_error] if parse_error else _schema_errors(decoded, schema)
        actions = decoded.get("action") if isinstance(decoded, Mapping) else None
        if not isinstance(actions, list) or not actions:
            if not errors:
                errors.append("BrowserUse completion has no actions")
            actions = []
        elif len(actions) > 5:
            errors.append("BrowserUse completion exceeds five actions")
        schema_valid = not errors
        indices = _action_element_indices(actions)
        visible = _visible_evidence_text(messages, refs)
        missing_indices = sorted(
            {
                element_index
                for element_index in indices
                if re.search(rf"(?:\*\[|\[){element_index}\]", visible) is None
            }
        )
        grounded = schema_valid and bool(visible) and not missing_indices
        executable = schema_valid
        accepted = schema_valid and grounded and executable
        reasons = list(errors)
        if missing_indices:
            reasons.append(f"action indices absent from visible state: {missing_indices}")
        if schema_valid and not visible:
            reasons.append("no sidecar-bound visible evidence text")
        output.append(
            {
                "schema": ACTION_VALIDATION_SCHEMA,
                "validator_schema": STATIC_VISIBLE_VALIDATION_SCHEMA,
                "validation_tier": "static_visible_state",
                "state_id": state_id,
                "request_sha256": labeled["request_sha256"],
                "effective_request_sha256": labeled["effective_request_sha256"],
                "teacher_completion_sha256": labeled["teacher_completion_sha256"],
                "mode": capture["mode"],
                "phase": capture["phase"],
                "variant": capture["variant"],
                "split": capture["split"],
                "rollout_id": capture["rollout_id"],
                "validator_sha256": validator,
                "validated_at_unix": timestamp,
                "status": "accepted" if accepted else "rejected",
                "schema_valid": schema_valid,
                "grounded": grounded,
                "executable": executable,
                "execution_verified": False,
                "postcondition_verified": False,
                "execution_scope": "static_visible_state",
                "grounding_standard": (
                    "schema_valid_and_all_action_element_indices_present_in_"
                    "sidecar_bound_visible_messages"
                ),
                "rejection_reason": None if accepted else "; ".join(reasons),
                "evidence_refs": refs,
                "static_audit": {
                    "browseruse_schema_sha256": sha256_json(schema),
                    "visible_evidence_sha256": hashlib.sha256(visible.encode()).hexdigest(),
                    "action_element_indices": indices,
                    "missing_action_element_indices": missing_indices,
                    "browser_executed": False,
                    "evaluator_read": False,
                    "postcondition_observed": False,
                },
            }
        )
    if not output:
        raise SolDaggerError("static validation received no Sol labels")
    return output


def _visible_control_indices(visible: str, label: str, *, tag: str) -> set[int]:
    return {
        int(match)
        for match in re.findall(
            rf"(?:\*\[|\[)(\d+)\]<{tag}[^>]*?/?>[ \t]*\n[ \t]*{re.escape(label)}[ \t]*(?:\n|$)",
            visible,
        )
    }


def make_cleanup_semantic_validations(
    labeled_states: Iterable[Mapping[str, Any]],
    cleanup_schedule: Iterable[Mapping[str, Any]],
    *,
    validated_at_unix: float,
    validator_sha256: str | None = None,
) -> list[dict[str, Any]]:
    """Apply the frozen, outcome-blind semantic policy for cleanup correction.

    PDP labels may take exactly one visible ``Add to Cart``/header ``Cart``
    click, or emit a schema-valid decision checkpoint whose quoted basis is
    literally present in the sidecar-bound current state.  ``Buy Now`` and all
    other consequential controls are rejected.  Dirty-cart labels must take
    exactly one click: the ``Delete`` belonging to the visibly rendered
    accident-protection-plan row.  The policy is intentionally evaluated
    without opening a browser or reading rewards, task outcomes, evaluators, or
    any held-out trace.
    """

    labels = list(labeled_states)
    schedule = list(cleanup_schedule)
    schedule_by_key: dict[tuple[str, int], Mapping[str, Any]] = {}
    kind_counts: Counter[str] = Counter()
    for index, row in enumerate(schedule):
        if not isinstance(row, Mapping) or row.get("schema") != CAPTURE_SCHEDULE_SCHEMA:
            raise SolDaggerError(f"cleanup semantic schedule row {index} is unsupported")
        if row.get("split") != "train" or row.get("selection_basis") != (
            "deterministic_visible_purchase_structure_v1"
        ):
            raise SolDaggerError("cleanup semantic schedule is not the frozen train population")
        kind = row.get("structural_kind")
        if kind not in {"pdp_buy_now", "cart_addon"}:
            raise SolDaggerError("cleanup semantic schedule has an unsupported structural kind")
        session = _require_text(row.get("session_sha256"), label="cleanup schedule session")
        sequence = row.get("source_sequence")
        if type(sequence) is not int or sequence < 0:
            raise SolDaggerError("cleanup semantic schedule has an invalid sequence")
        key = (session, sequence)
        if key in schedule_by_key:
            raise SolDaggerError("cleanup semantic schedule contains a duplicate state")
        schedule_by_key[key] = row
        kind_counts[str(kind)] += 1
    if len(schedule_by_key) != 14 or kind_counts != {"pdp_buy_now": 11, "cart_addon": 3}:
        raise SolDaggerError("cleanup semantic schedule population drifted")

    static_rows = make_static_visible_action_validations(
        labels,
        validated_at_unix=validated_at_unix,
        validator_sha256=validator_sha256,
    )
    if len(static_rows) != len(schedule_by_key):
        raise SolDaggerError("cleanup labels do not cover the exact frozen schedule")
    output: list[dict[str, Any]] = []
    observed: set[tuple[str, int]] = set()
    for labeled, static in zip(labels, static_rows, strict=True):
        capture = labeled.get("capture")
        request = labeled.get("qwen_request")
        completion = labeled.get("teacher_completion")
        if not all(isinstance(value, Mapping) for value in (capture, request, completion)):
            raise SolDaggerError("cleanup semantic label is structurally incomplete")
        key = (str(labeled.get("session_sha256")), capture.get("source_sequence"))
        scheduled = schedule_by_key.get(key)
        if scheduled is None or key in observed:
            raise SolDaggerError("cleanup label is absent or duplicated in the frozen schedule")
        observed.add(key)
        if any(
            capture.get(name) != scheduled.get(name)
            for name in ("rollout_id", "split", "variant", "phase")
        ):
            raise SolDaggerError("cleanup label coordinate drifted from the frozen schedule")
        messages = request.get("messages")
        if not isinstance(messages, list):
            raise SolDaggerError("cleanup semantic label has no request messages")
        refs = _evidence_refs(capture, messages)
        visible = _visible_evidence_text(messages, refs)
        content = completion.get("content")
        try:
            decoded = json.loads(content) if isinstance(content, str) else None
        except json.JSONDecodeError:
            decoded = None
        actions = decoded.get("action") if isinstance(decoded, Mapping) else None
        action = actions[0] if isinstance(actions, list) and len(actions) == 1 else None
        semantic_reason: str | None = None
        semantic_target: str | None = None
        allowed_indices: set[int] = set()
        buy_now_indices = _visible_control_indices(visible, "Buy Now", tag="button")
        clicked_index: int | None = None
        if isinstance(action, Mapping) and set(action) == {"click"}:
            click = action.get("click")
            if (
                isinstance(click, Mapping)
                and set(click) == {"index"}
                and type(click.get("index")) is int
            ):
                clicked_index = int(click["index"])

        kind = str(scheduled["structural_kind"])
        if kind == "pdp_buy_now":
            add_indices = _visible_control_indices(visible, "Add to Cart", tag="button")
            cart_indices = _visible_control_indices(visible, "Cart", tag="a")
            allowed_indices = add_indices | cart_indices
            if clicked_index in buy_now_indices:
                semantic_reason = "PDP teacher action selects prohibited visible Buy Now"
            elif clicked_index in add_indices:
                semantic_target = "visible_add_to_cart"
            elif clicked_index in cart_indices:
                semantic_target = "visible_cart_navigation"
            elif isinstance(action, Mapping) and set(action) == {"decision_checkpoint"}:
                checkpoint = action.get("decision_checkpoint")
                frontier = checkpoint.get("frontier") if isinstance(checkpoint, Mapping) else None
                basis = frontier.get("basis") if isinstance(frontier, Mapping) else None
                if isinstance(basis, str) and basis and basis in visible:
                    semantic_target = "visible_basis_decision_checkpoint"
                else:
                    semantic_reason = (
                        "decision checkpoint basis is absent from current visible state"
                    )
            else:
                semantic_reason = "PDP teacher action is not one approved correction target"
        else:
            addon_match = re.search(
                r"3-Year Accident Protection Plan[\s\S]{0,1200}?"
                r"(?:\*\[|\[)(\d+)\]<button[^>]*?/?>[ \t]*\n[ \t]*Delete(?:[ \t]*\n|$)",
                visible,
            )
            if addon_match is None:
                raise SolDaggerError("frozen add-on cart no longer exposes its Delete control")
            allowed_indices = {int(addon_match.group(1))}
            if clicked_index in allowed_indices:
                semantic_target = "visible_addon_delete"
            else:
                semantic_reason = "dirty-cart teacher action is not the visible add-on Delete"

        static_accepted = static["status"] == "accepted"
        accepted = static_accepted and semantic_target is not None
        rejection_reasons = []
        if not static_accepted:
            rejection_reasons.append(str(static["rejection_reason"]))
        if semantic_reason is not None:
            rejection_reasons.append(semantic_reason)
        revised = dict(static)
        revised.update(
            {
                "status": "accepted" if accepted else "rejected",
                "rejection_reason": None if accepted else "; ".join(rejection_reasons),
                "grounding_standard": (
                    "static_visible_schema_and_indices_plus_frozen_cleanup_semantics_v1"
                ),
                "cleanup_semantic_audit": {
                    "policy": "frozen_cleanup_semantics_v1",
                    "structural_kind": kind,
                    "semantic_target": semantic_target,
                    "allowed_action_indices": sorted(allowed_indices),
                    "visible_buy_now_indices": sorted(buy_now_indices),
                    "teacher_clicked_index": clicked_index,
                    "single_action": isinstance(actions, list) and len(actions) == 1,
                    "reward_or_evaluator_read": False,
                    "task_outcome_read": False,
                    "browser_executed": False,
                },
            }
        )
        output.append(revised)
    if observed != set(schedule_by_key):
        raise SolDaggerError("cleanup semantic validation omitted a frozen schedule state")
    return output


def _validation_index(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    indexed: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or row.get("schema") != ACTION_VALIDATION_SCHEMA:
            raise SolDaggerError(f"action validation row {index} has unsupported schema")
        state_id = _require_hex64(
            row.get("state_id"), label=f"action validation row {index} state_id"
        )
        if state_id in indexed:
            raise SolDaggerError("duplicate action validation state_id")
        indexed[state_id] = row
    return indexed


def _validate_action_sidecar(
    row: Mapping[str, Any],
    labeled: Mapping[str, Any],
) -> tuple[bool, dict[str, Any]]:
    completion_sha = _require_hex64(
        row.get("teacher_completion_sha256"),
        label="validation teacher_completion_sha256",
    )
    if completion_sha != labeled.get("teacher_completion_sha256"):
        raise SolDaggerError("action validation is bound to a different Sol completion")
    if row.get("request_sha256") != labeled.get("request_sha256") or row.get(
        "effective_request_sha256"
    ) != labeled.get("effective_request_sha256"):
        raise SolDaggerError("action validation request identity drifted")
    capture = labeled.get("capture")
    qwen_request = labeled.get("qwen_request")
    if not isinstance(capture, Mapping) or not isinstance(qwen_request, Mapping):
        raise SolDaggerError("labeled state is missing capture/request")
    for name in ("mode", "phase", "variant", "split", "rollout_id"):
        if row.get(name) != capture.get(name):
            raise SolDaggerError(f"action validation {name} does not match capture")
    _require_hex64(row.get("validator_sha256"), label="action validator_sha256")
    _require_number(row.get("validated_at_unix"), label="action validated_at_unix")
    evidence = _evidence_refs(row, qwen_request["messages"])
    capture_evidence = {
        (item["message_index"], item["message_sha256"]) for item in capture["evidence_refs"]
    }
    if not {(item["message_index"], item["message_sha256"]) for item in evidence}.issubset(
        capture_evidence
    ):
        raise SolDaggerError("action validation cites evidence absent from capture sidecar")

    status = row.get("status")
    if status not in {"accepted", "rejected"}:
        raise SolDaggerError("action validation status must be accepted or rejected")
    booleans = {
        name: row.get(name)
        for name in (
            "schema_valid",
            "grounded",
            "executable",
            "execution_verified",
            "postcondition_verified",
        )
    }
    if any(type(value) is not bool for value in booleans.values()):
        raise SolDaggerError("action validation flags must be booleans")
    tier = row.get("validation_tier")
    if tier == "executed":
        expected_scope = "clone" if capture["mode"] == "shadow" else "live"
    elif tier == "static_visible_state":
        if capture["mode"] != "shadow":
            raise SolDaggerError("static validation cannot certify intervention mode")
        if row.get("validator_schema") != STATIC_VISIBLE_VALIDATION_SCHEMA:
            raise SolDaggerError("static validation schema identity drifted")
        static_audit = row.get("static_audit")
        if (
            not isinstance(static_audit, Mapping)
            or static_audit.get("browser_executed") is not False
            or static_audit.get("evaluator_read") is not False
            or static_audit.get("postcondition_observed") is not False
        ):
            raise SolDaggerError("static validation does not prove its no-execution boundary")
        expected_scope = "static_visible_state"
    else:
        raise SolDaggerError("action validation tier must be executed or static_visible_state")
    if row.get("execution_scope") != expected_scope:
        raise SolDaggerError(f"{tier} action requires execution_scope={expected_scope}")
    accepted = status == "accepted"
    if tier == "executed" and accepted and not all(booleans.values()):
        raise SolDaggerError("executed Sol action lacks a positive validation flag")
    if tier == "static_visible_state":
        if booleans["execution_verified"] or booleans["postcondition_verified"]:
            raise SolDaggerError("static validation fabricates execution/postcondition proof")
        if accepted and not all(
            booleans[name] for name in ("schema_valid", "grounded", "executable")
        ):
            raise SolDaggerError("static accepted action lacks schema/grounding executability")
    reason = row.get("rejection_reason")
    if not accepted and (not isinstance(reason, str) or not reason):
        raise SolDaggerError("rejected Sol action requires rejection_reason")
    if accepted and reason not in {None, ""}:
        raise SolDaggerError("accepted Sol action cannot carry a rejection reason")
    return accepted, {
        "validation_sha256": sha256_json(row),
        "validator_sha256": row["validator_sha256"],
        "validated_at_unix": row["validated_at_unix"],
        "validation_tier": tier,
        "execution_scope": expected_scope,
        "flags": booleans,
        "status": status,
        "rejection_reason": reason,
        "evidence_refs": evidence,
        **(
            {
                "validator_schema": row["validator_schema"],
                "grounding_standard": row.get("grounding_standard"),
                "static_audit": _json_clone(dict(row["static_audit"]), label="static action audit"),
            }
            if tier == "static_visible_state"
            else {}
        ),
    }


def _audit_balance_and_freshness(
    rows: Sequence[Mapping[str, Any]],
    *,
    policy: CampaignPolicy,
    now_unix: float,
) -> dict[str, Any]:
    now = _require_number(now_unix, label="now_unix")
    split_counts: Counter[str] = Counter()
    variant_counts: Counter[str] = Counter()
    phase_counts: Counter[str] = Counter()
    split_variant_counts: Counter[tuple[str, str]] = Counter()
    split_phase_counts: Counter[tuple[str, str]] = Counter()
    rollouts_by_variant: dict[str, set[str]] = defaultdict(set)
    rollouts_by_mode: dict[str, set[str]] = defaultdict(set)
    split_sessions: dict[str, set[str]] = defaultdict(set)
    split_rollouts: dict[str, set[str]] = defaultdict(set)
    state_ids: set[str] = set()
    oldest = now
    newest = policy.not_before_unix
    for row in rows:
        state_id = row["state_id"]
        if state_id in state_ids:
            raise SolDaggerError("accepted corpus contains a duplicate state")
        state_ids.add(state_id)
        capture = row["capture"]
        if capture["campaign_id"] != policy.campaign_id:
            raise SolDaggerError("accepted state campaign_id drifted")
        if capture["collection_nonce"] != policy.collection_nonce:
            raise SolDaggerError("accepted state collection_nonce drifted")
        if capture["harness_fingerprint_sha256"] != policy.harness_fingerprint_sha256:
            raise SolDaggerError("accepted state harness fingerprint drifted")
        captured = _require_number(capture["captured_at_unix"], label="captured_at_unix")
        if captured < policy.not_before_unix:
            raise SolDaggerError("accepted state predates the frozen campaign")
        if captured > now + 60:
            raise SolDaggerError("accepted state has a future capture timestamp")
        if now - captured > policy.maximum_age_seconds:
            raise SolDaggerError("accepted state is stale for this campaign")
        oldest = min(oldest, captured)
        newest = max(newest, captured)

        split = capture["split"]
        variant = capture["variant"]
        phase = capture["phase"]
        mode = capture["mode"]
        rollout = capture["rollout_id"]
        session = row["session_sha256"]
        split_counts[split] += 1
        variant_counts[variant] += 1
        phase_counts[phase] += 1
        split_variant_counts[(split, variant)] += 1
        split_phase_counts[(split, phase)] += 1
        rollouts_by_variant[variant].add(rollout)
        rollouts_by_mode[mode].add(rollout)
        split_sessions[split].add(session)
        split_rollouts[split].add(rollout)
    if len(rows) > policy.maximum_total_labels:
        raise SolDaggerError("accepted Sol labels exceed frozen maximum_total_labels")
    if split_counts["train"] < policy.minimum_train_labels:
        raise SolDaggerError("accepted corpus has too few train labels")
    if split_counts["holdout"] < policy.minimum_holdout_labels:
        raise SolDaggerError("accepted corpus has too few holdout labels")
    if split_sessions["train"] & split_sessions["holdout"]:
        raise SolDaggerError("train and holdout share a Qwen session")
    if split_rollouts["train"] & split_rollouts["holdout"]:
        raise SolDaggerError("train and holdout share a rollout_id")
    for variant in policy.required_variants:
        if variant_counts[variant] == 0:
            raise SolDaggerError(f"accepted corpus lacks variant {variant}")
        if split_variant_counts[("train", variant)] < policy.minimum_train_per_variant:
            raise SolDaggerError(f"accepted corpus has too few train labels for variant {variant}")
        if split_variant_counts[("holdout", variant)] < policy.minimum_holdout_per_variant:
            raise SolDaggerError(
                f"accepted corpus has too few holdout labels for variant {variant}"
            )
        if len(rollouts_by_variant[variant]) < policy.minimum_rollouts_per_variant:
            raise SolDaggerError(f"accepted corpus has too few rollouts for variant {variant}")
    for phase in policy.required_phases:
        if phase_counts[phase] == 0:
            raise SolDaggerError(f"accepted corpus lacks phase {phase}")
        if split_phase_counts[("train", phase)] < policy.minimum_train_per_phase:
            raise SolDaggerError(f"accepted corpus has too few train labels for phase {phase}")
        if split_phase_counts[("holdout", phase)] < policy.minimum_holdout_per_phase:
            raise SolDaggerError(f"accepted corpus has too few holdout labels for phase {phase}")
    if len(rollouts_by_mode["shadow"]) < policy.minimum_shadow_rollouts:
        raise SolDaggerError("accepted corpus has too few shadow rollouts")
    if len(rollouts_by_mode["intervention"]) < policy.minimum_intervention_rollouts:
        raise SolDaggerError("accepted corpus has too few intervention rollouts")
    return {
        "rows": len(rows),
        "split_counts": dict(sorted(split_counts.items())),
        "variant_counts": dict(sorted(variant_counts.items())),
        "phase_counts": dict(sorted(phase_counts.items())),
        "split_variant_counts": {
            f"{split}:{variant}": count
            for (split, variant), count in sorted(split_variant_counts.items())
        },
        "split_phase_counts": {
            f"{split}:{phase}": count
            for (split, phase), count in sorted(split_phase_counts.items())
        },
        "rollout_counts_by_variant": {
            key: len(value) for key, value in sorted(rollouts_by_variant.items())
        },
        "rollout_counts_by_mode": {
            key: len(value) for key, value in sorted(rollouts_by_mode.items())
        },
        "oldest_capture_unix": oldest,
        "newest_capture_unix": newest,
        "train_holdout_session_overlap": 0,
        "train_holdout_rollout_overlap": 0,
        "target_train_labels": policy.target_train_labels,
        "target_holdout_labels": policy.target_holdout_labels,
        "target_train_met": split_counts["train"] >= policy.target_train_labels,
        "target_holdout_met": split_counts["holdout"] >= policy.target_holdout_labels,
    }


@dataclass(frozen=True, slots=True)
class VerifiedSFTBatch:
    train_labels: tuple[dict[str, Any], ...]
    holdout_labels: tuple[dict[str, Any], ...]
    audits: tuple[dict[str, Any], ...]
    report: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StructuralTopupResult:
    """Exactly selected replacements plus sealed nontraining reserve rows."""

    batch: VerifiedSFTBatch
    reserve_attempt_audits: tuple[dict[str, Any], ...]
    unused_accepted_reserve_labels: tuple[dict[str, Any], ...]
    rejected_reserve_labels: tuple[dict[str, Any], ...]
    selection_audit: dict[str, Any]


def materialize_verified_sft(
    labeled_states: Iterable[Mapping[str, Any]],
    action_validations: Iterable[Mapping[str, Any]],
    policy: CampaignPolicy,
    *,
    now_unix: float,
) -> VerifiedSFTBatch:
    """Materialize only externally grounded/executed Sol actions.

    Validation metadata is emitted to ``audits`` and never copied into an SFT
    row's messages, tools, or other model input.
    """

    labeled = list(labeled_states)
    validations = _validation_index(action_validations)
    accepted_rows: list[Mapping[str, Any]] = []
    train: list[dict[str, Any]] = []
    holdout: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    seen: set[str] = set()
    rejected = 0
    for index, row in enumerate(labeled):
        if not isinstance(row, Mapping) or row.get("schema") not in {
            SHADOW_LABEL_SCHEMA,
            INTERVENTION_LABEL_SCHEMA,
        }:
            raise SolDaggerError(f"labeled row {index} has unsupported schema")
        state_id = _require_hex64(row.get("state_id"), label=f"labeled row {index} state_id")
        if state_id in seen:
            raise SolDaggerError("duplicate labeled Sol state_id")
        seen.add(state_id)
        validation = validations.get(state_id)
        if validation is None:
            raise SolDaggerError("labeled Sol state has no action validation sidecar")
        qwen_request = row.get("qwen_request")
        completion = row.get("teacher_completion")
        capture = row.get("capture")
        invariance = row.get("invariance_audit")
        if not all(
            isinstance(value, Mapping) for value in (qwen_request, completion, capture, invariance)
        ):
            raise SolDaggerError("labeled Sol state is structurally incomplete")
        if sha256_json(completion) != row.get("teacher_completion_sha256"):
            raise SolDaggerError("Sol completion changed after teacher call")
        if sha256_json(_harness_projection(qwen_request)) != row.get(
            "qwen_harness_projection_sha256"
        ):
            raise SolDaggerError("Qwen model-visible harness changed after teacher call")
        if invariance.get("unchanged") is not True or invariance.get(
            "qwen_harness_projection_sha256"
        ) != row.get("qwen_harness_projection_sha256"):
            raise SolDaggerError("teacher-call invariance audit is invalid")
        accepted, validation_audit = _validate_action_sidecar(validation, row)
        base_audit = {
            "schema": LABEL_AUDIT_SCHEMA,
            "state_id": state_id,
            "session_sha256": row["session_sha256"],
            "request_sha256": row["request_sha256"],
            "effective_request_sha256": row["effective_request_sha256"],
            "teacher_completion_sha256": row["teacher_completion_sha256"],
            "teacher_request_sha256": row["teacher_request_sha256"],
            "teacher_response_sha256": row["teacher_response_sha256"],
            "teacher": deepcopy(dict(row["teacher"])),
            "capture_sidecar_sha256": sha256_json(capture),
            "campaign_id": capture["campaign_id"],
            "rollout_id": capture["rollout_id"],
            "split": capture["split"],
            "variant": capture["variant"],
            "phase": capture["phase"],
            "mode": capture["mode"],
            "invariance_audit": deepcopy(dict(invariance)),
            "action_validation": validation_audit,
        }
        audits.append(base_audit)
        if not accepted:
            rejected += 1
            continue

        accepted_rows.append(row)
        baseline_messages = deepcopy(qwen_request["messages"])
        baseline_tools = deepcopy(qwen_request.get("tools", []))
        messages = [*baseline_messages, deepcopy(dict(completion))]
        # A final fail-closed scan proves that sidecar JSON was not appended to
        # either model prefix or target.
        model_payload = canonical_json({"messages": messages, "tools": baseline_tools})
        if canonical_json(capture) in model_payload or canonical_json(validation) in model_payload:
            raise SolDaggerError("collection/validation sidecar entered verified SFT example")
        label_id = hashlib.sha256(
            f"{state_id}:{row['teacher_completion_sha256']}".encode()
        ).hexdigest()
        label = {
            "schema": CORRECTIVE_SFT_SCHEMA,
            "label_id": label_id,
            "state_id": state_id,
            "session_sha256": row["session_sha256"],
            "source_turn_index": row["source_turn_index"],
            "request_sha256": row["request_sha256"],
            "effective_request_sha256": row["effective_request_sha256"],
            "prompt_sha256": sha256_json({"messages": baseline_messages, "tools": baseline_tools}),
            "messages": messages,
            "tools": baseline_tools,
            "tool_choice": deepcopy(qwen_request.get("tool_choice")),
            "parallel_tool_calls": deepcopy(qwen_request.get("parallel_tool_calls")),
            "response_format": deepcopy(qwen_request.get("response_format")),
            "prompt_message_count": len(baseline_messages),
            "teacher": deepcopy(dict(row["teacher"])),
            "teacher_completion_sha256": row["teacher_completion_sha256"],
            "teacher_request_sha256": row["teacher_request_sha256"],
        }
        if label["messages"][:-1] != qwen_request["messages"]:
            raise SolDaggerError("verified SFT prompt differs from Qwen effective messages")
        if label["tools"] != qwen_request.get("tools", []):
            raise SolDaggerError("verified SFT tools differ from Qwen effective tools")
        destination = train if capture["split"] == "train" else holdout
        destination.append(label)

    unused = sorted(set(validations) - seen)
    if unused:
        raise SolDaggerError(f"{len(unused)} action validations have no Sol-labeled state")
    report = _audit_balance_and_freshness(
        accepted_rows,
        policy=policy,
        now_unix=now_unix,
    )
    attempted_tiers = Counter(audit["action_validation"]["validation_tier"] for audit in audits)
    accepted_audits = [
        audit for audit in audits if audit["action_validation"]["status"] == "accepted"
    ]
    accepted_tiers = Counter(
        audit["action_validation"]["validation_tier"] for audit in accepted_audits
    )
    report.update(
        {
            "schema": "harness-distill.sol-dagger-corpus-audit.v1",
            "teacher": {
                "provider": "trapi",
                "model_spec": SOL_MODEL_SPEC,
                "reasoning_effort": SOL_REASONING_EFFORT,
            },
            "accepted": len(accepted_rows),
            "rejected": rejected,
            "train_sft_rows": len(train),
            "holdout_action_rows": len(holdout),
            "model_visible_sidecar_fields": 0,
            "provider_reasoning_targets": 0,
            "attempted_validation_tier_counts": dict(sorted(attempted_tiers.items())),
            "accepted_validation_tier_counts": dict(sorted(accepted_tiers.items())),
            "execution_verified_actions": sum(
                audit["action_validation"]["flags"]["execution_verified"]
                for audit in accepted_audits
            ),
            "postcondition_verified_actions": sum(
                audit["action_validation"]["flags"]["postcondition_verified"]
                for audit in accepted_audits
            ),
        }
    )
    attempted = len(accepted_rows) + rejected
    exclusion_fraction = rejected / attempted if attempted else 0.0
    if exclusion_fraction > policy.maximum_exclusion_fraction:
        raise SolDaggerError("Sol action exclusions exceed the frozen 25% attrition gate")
    report["exclusion_fraction"] = exclusion_fraction
    report["maximum_exclusion_fraction"] = policy.maximum_exclusion_fraction
    return VerifiedSFTBatch(
        train_labels=tuple(train),
        holdout_labels=tuple(holdout),
        audits=tuple(audits),
        report=report,
    )


def materialize_structural_topup(
    original_labeled_states: Iterable[Mapping[str, Any]],
    original_action_validations: Iterable[Mapping[str, Any]],
    reserve_labeled_states: Iterable[Mapping[str, Any]],
    reserve_action_validations: Iterable[Mapping[str, Any]],
    reserve_schedule_records: Iterable[Mapping[str, Any]],
    reserve_freeze_audit: Mapping[str, Any],
    policy: CampaignPolicy,
    *,
    now_unix: float,
    required_deficits: int | None = None,
    required_reserve_attempts: int | None = None,
) -> StructuralTopupResult:
    """Choose the first statically accepted frozen candidate per deficit.

    Selection is limited to the binary action-validation gate.  No reward,
    task result, evaluator output, or response-quality comparison is used.
    Every unused accepted reserve label is returned for a separate explicitly
    nontraining bundle, and every reserve rejection remains in attrition
    accounting.
    """

    original = list(original_labeled_states)
    original_validation_rows = list(original_action_validations)
    reserve = list(reserve_labeled_states)
    reserve_validation_rows = list(reserve_action_validations)
    reserve_schedule = list(reserve_schedule_records)
    if not all((original, original_validation_rows, reserve, reserve_validation_rows)):
        raise SolDaggerError("structural top-up inputs must be non-empty")

    freeze = _json_clone(dict(reserve_freeze_audit), label="reserve freeze audit")
    freeze_body = {key: value for key, value in freeze.items() if key != "reserve_audit_sha256"}
    if (
        freeze.get("schema") != STRUCTURAL_RESERVE_AUDIT_SCHEMA
        or freeze.get("status")
        not in {
            "frozen_before_teacher_calls",
            "staged_reserve_frozen_before_each_teacher_wave",
        }
        or freeze.get("reserve_audit_sha256") != sha256_json(freeze_body)
        or freeze.get("schedule_sha256")
        != hashlib.sha256(_canonical_jsonl(reserve_schedule)).hexdigest()
        or freeze.get("candidate_count") != len(reserve_schedule)
        or freeze.get("selection_reads_qwen_response") is not False
        or freeze.get("selection_reads_reward_or_outcome") is not False
        or freeze.get("selection_reads_run_result_or_database") is not False
        or freeze.get("selection_reads_evaluator") is not False
    ):
        raise SolDaggerError("reserve freeze audit or schedule identity drifted")

    original_validations = _validation_index(original_validation_rows)
    reserve_validations = _validation_index(reserve_validation_rows)
    original_by_state: dict[str, Mapping[str, Any]] = {}
    original_acceptance: dict[str, bool] = {}
    rejected_original: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(original):
        if not isinstance(row, Mapping) or row.get("schema") not in {
            SHADOW_LABEL_SCHEMA,
            INTERVENTION_LABEL_SCHEMA,
        }:
            raise SolDaggerError(f"original top-up label row {index} has unsupported schema")
        state_id = _require_hex64(row.get("state_id"), label="original top-up state_id")
        if state_id in original_by_state:
            raise SolDaggerError("original top-up labels duplicate state identity")
        validation = original_validations.get(state_id)
        if validation is None:
            raise SolDaggerError("original top-up label has no validation")
        accepted, _audit = _validate_action_sidecar(validation, row)
        original_by_state[state_id] = row
        original_acceptance[state_id] = accepted
        if not accepted:
            capture = row.get("capture")
            if not isinstance(capture, Mapping) or capture.get("split") != "train":
                raise SolDaggerError("top-up can replace only rejected train labels")
            rejected_original[state_id] = row
    if set(original_validations) != set(original_by_state):
        raise SolDaggerError("original top-up labels/validations are not one-to-one")
    if required_deficits is not None and len(rejected_original) != required_deficits:
        raise SolDaggerError("original structural deficit count changed")

    schedule_by_key: dict[tuple[str, int], Mapping[str, Any]] = {}
    for index, row in enumerate(reserve_schedule):
        if not isinstance(row, Mapping) or row.get("schema") != CAPTURE_SCHEDULE_SCHEMA:
            raise SolDaggerError(f"reserve schedule row {index} has unsupported schema")
        session = _require_text(row.get("session_sha256"), label="reserve session_sha256")
        sequence = row.get("source_sequence")
        if type(sequence) is not int or sequence < 0:
            raise SolDaggerError("reserve schedule sequence is invalid")
        if row.get("split") != "train" or row.get("mode") != "shadow":
            raise SolDaggerError("reserve schedule must contain shadow train states only")
        if row.get("selection_basis") not in {
            "deterministic_structural_deficit_reserve_v1",
            "deterministic_structural_deficit_reserve_wave2_v1",
        }:
            raise SolDaggerError("reserve schedule selection basis drifted")
        deficit_id = _require_hex64(row.get("deficit_id"), label="reserve deficit_id")
        replaces = _require_hex64(row.get("replaces_state_id"), label="reserve replaces_state_id")
        if replaces not in rejected_original:
            raise SolDaggerError("reserve candidate does not replace an original rejection")
        expected_deficit = hashlib.sha256(f"reserve:{replaces}".encode()).hexdigest()
        if deficit_id != expected_deficit:
            raise SolDaggerError("reserve deficit identity changed")
        rank = row.get("reserve_rank")
        order = row.get("reserve_order")
        if type(rank) is not int or rank < 0:
            raise SolDaggerError("reserve rank is invalid")
        if type(order) is not int or order < 0:
            raise SolDaggerError("reserve order is invalid")
        key = (session, sequence)
        if key in schedule_by_key:
            raise SolDaggerError("reserve schedule duplicates a state")
        schedule_by_key[key] = row
    if required_reserve_attempts is not None and len(schedule_by_key) != required_reserve_attempts:
        raise SolDaggerError("reserve attempt count changed")
    if sorted(int(row["reserve_order"]) for row in reserve_schedule) != list(
        range(len(reserve_schedule))
    ):
        raise SolDaggerError("reserve order is not contiguous")

    reserve_by_key: dict[tuple[str, int], Mapping[str, Any]] = {}
    reserve_by_state: dict[str, Mapping[str, Any]] = {}
    reserve_acceptance: dict[str, bool] = {}
    reserve_validation_audits: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(reserve):
        if not isinstance(row, Mapping) or row.get("schema") != SHADOW_LABEL_SCHEMA:
            raise SolDaggerError(f"reserve label row {index} has unsupported schema")
        state_id = _require_hex64(row.get("state_id"), label="reserve state_id")
        session = _require_text(row.get("session_sha256"), label="reserve label session")
        sequence = row.get("source_sequence")
        if type(sequence) is not int or sequence < 0:
            raise SolDaggerError("reserve label sequence is invalid")
        key = (session, sequence)
        scheduled = schedule_by_key.get(key)
        if scheduled is None:
            raise SolDaggerError("reserve label is absent from frozen reserve schedule")
        if state_id in reserve_by_state or key in reserve_by_key:
            raise SolDaggerError("reserve labels duplicate a state")
        capture = row.get("capture")
        if not isinstance(capture, Mapping):
            raise SolDaggerError("reserve label lacks capture metadata")
        for name in ("rollout_id", "split", "variant", "phase", "mode"):
            if capture.get(name) != scheduled.get(name):
                raise SolDaggerError(f"reserve label {name} differs from schedule")
        validation = reserve_validations.get(state_id)
        if validation is None:
            raise SolDaggerError("reserve label has no action validation")
        accepted, validation_audit = _validate_action_sidecar(validation, row)
        reserve_by_key[key] = row
        reserve_by_state[state_id] = row
        reserve_acceptance[state_id] = accepted
        reserve_validation_audits[state_id] = validation_audit
    if set(reserve_validations) != set(reserve_by_state):
        raise SolDaggerError("reserve labels/validations are not one-to-one")
    if set(reserve_by_key) != set(schedule_by_key):
        raise SolDaggerError("reserve labels do not cover the frozen schedule exactly")

    candidate_groups: dict[str, list[tuple[Mapping[str, Any], Mapping[str, Any]]]] = defaultdict(
        list
    )
    for row in reserve_schedule:
        key = (str(row["session_sha256"]), int(row["source_sequence"]))
        candidate_groups[str(row["deficit_id"])].append((row, reserve_by_key[key]))
    expected_deficit_ids = {
        hashlib.sha256(f"reserve:{state_id}".encode()).hexdigest() for state_id in rejected_original
    }
    if set(candidate_groups) != expected_deficit_ids:
        raise SolDaggerError("reserve candidate groups do not match original deficits")

    chosen_state_ids: set[str] = set()
    chosen_rows: list[Mapping[str, Any]] = []
    selected_by_deficit: dict[str, str] = {}
    for deficit_id in sorted(candidate_groups):
        candidates = sorted(
            candidate_groups[deficit_id],
            key=lambda item: (int(item[0]["reserve_rank"]), int(item[0]["reserve_order"])),
        )
        ranks = [int(item[0]["reserve_rank"]) for item in candidates]
        if len(ranks) < 2 or ranks[:2] != [0, 1] or len(set(ranks)) != len(ranks):
            raise SolDaggerError("reserve deficit lacks ordered rank-0/rank-1 candidates")
        chosen: Mapping[str, Any] | None = None
        for _schedule_row, label in candidates:
            if reserve_acceptance[str(label["state_id"])]:
                chosen = label
                break
        if chosen is None:
            raise SolDaggerError("a frozen deficit has no accepted reserve candidate")
        state_id = str(chosen["state_id"])
        chosen_state_ids.add(state_id)
        chosen_rows.append(chosen)
        selected_by_deficit[deficit_id] = state_id
    if len(chosen_rows) != len(rejected_original):
        raise SolDaggerError("structural top-up did not select exactly one row per deficit")

    selected_reserve_validations = [
        reserve_validations[str(row["state_id"])] for row in chosen_rows
    ]
    batch = materialize_verified_sft(
        [*original, *chosen_rows],
        [*original_validation_rows, *selected_reserve_validations],
        policy,
        now_unix=now_unix,
    )
    if (
        len(batch.train_labels) != policy.target_train_labels
        or len(batch.holdout_labels) != policy.target_holdout_labels
    ):
        raise SolDaggerError("structural top-up did not meet the exact train/holdout target")

    accepted_raw = [
        row for row in original if original_acceptance[str(row["state_id"])]
    ] + chosen_rows
    session_phases = [
        (str(row["session_sha256"]), str(row["capture"]["phase"])) for row in accepted_raw
    ]
    if len(session_phases) != len(set(session_phases)):
        raise SolDaggerError("accepted top-up corpus duplicates a phase within a session")
    split_variants = Counter(
        (str(row["capture"]["split"]), str(row["capture"]["variant"])) for row in accepted_raw
    )
    if any(
        split_variants[("train", variant)] != policy.minimum_train_per_variant
        or split_variants[("holdout", variant)] != policy.minimum_holdout_per_variant
        for variant in policy.required_variants
    ):
        raise SolDaggerError("structural top-up did not restore exact variant balance")

    unused_accepted = [
        row
        for row in reserve
        if reserve_acceptance[str(row["state_id"])] and str(row["state_id"]) not in chosen_state_ids
    ]
    reserve_rejected = [row for row in reserve if not reserve_acceptance[str(row["state_id"])]]
    original_rejection_count = len(rejected_original)
    reserve_rejection_count = len(reserve_rejected)
    attempted = len(original) + len(reserve)
    rejected_count = original_rejection_count + reserve_rejection_count
    exclusion_fraction = rejected_count / attempted
    if exclusion_fraction > policy.maximum_exclusion_fraction:
        raise SolDaggerError("combined structural reserve exclusions exceed the frozen gate")

    attempt_audits: list[dict[str, Any]] = []
    for schedule_row in sorted(reserve_schedule, key=lambda row: int(row["reserve_order"])):
        key = (str(schedule_row["session_sha256"]), int(schedule_row["source_sequence"]))
        label = reserve_by_key[key]
        state_id = str(label["state_id"])
        accepted = reserve_acceptance[state_id]
        selected = state_id in chosen_state_ids
        attempt_audits.append(
            {
                "schema": "harness-distill.sol-dagger-reserve-attempt-audit.v1",
                "reserve_order": schedule_row["reserve_order"],
                "reserve_rank": schedule_row["reserve_rank"],
                "deficit_id": schedule_row["deficit_id"],
                "replaces_state_id": schedule_row["replaces_state_id"],
                "state_id": state_id,
                "session_sha256": label["session_sha256"],
                "source_sequence": label["source_sequence"],
                "variant": schedule_row["variant"],
                "phase": schedule_row["phase"],
                "status": "accepted" if accepted else "rejected",
                "selected_for_training": selected,
                "nontraining_reason": (
                    None
                    if selected
                    else "unused_accepted_reserve"
                    if accepted
                    else "static_action_validation_rejected"
                ),
                "teacher_completion_sha256": label["teacher_completion_sha256"],
                "validation_sha256": reserve_validation_audits[state_id]["validation_sha256"],
                "selection_uses_response_quality": False,
                "selection_uses_reward_result_or_evaluator": False,
            }
        )

    selection_body = {
        "schema": "harness-distill.sol-dagger-structural-topup-selection-audit.v1",
        "reserve_freeze_audit_sha256": freeze["reserve_audit_sha256"],
        "selection_rule": "first_static_accepted_candidate_in_frozen_rank_order",
        "response_quality_selection": False,
        "reward_result_or_evaluator_selection": False,
        "original_attempts": len(original),
        "original_accepted": len(original) - original_rejection_count,
        "original_rejected": original_rejection_count,
        "reserve_attempts": len(reserve),
        "reserve_accepted": len(reserve) - reserve_rejection_count,
        "reserve_rejected": reserve_rejection_count,
        "reserve_selected_replacements": len(chosen_rows),
        "reserve_unused_accepted": len(unused_accepted),
        "attempted": attempted,
        "rejected": rejected_count,
        "exclusion_fraction": exclusion_fraction,
        "selected_by_deficit": dict(sorted(selected_by_deficit.items())),
    }
    selection_audit = {
        **selection_body,
        "selection_audit_sha256": sha256_json(selection_body),
    }
    attempted_tiers = Counter(
        row.get("validation_tier") for row in [*original_validation_rows, *reserve_validation_rows]
    )
    report = dict(batch.report)
    report.update(
        {
            "rejected": rejected_count,
            "attempted": attempted,
            "exclusion_fraction": exclusion_fraction,
            "maximum_exclusion_fraction": policy.maximum_exclusion_fraction,
            "attempted_validation_tier_counts": dict(sorted(attempted_tiers.items())),
            "structural_topup": {
                "reserve_freeze_audit_sha256": freeze["reserve_audit_sha256"],
                "selection_audit_sha256": selection_audit["selection_audit_sha256"],
                "selection_rule": selection_body["selection_rule"],
                "response_quality_selection": False,
                "reward_result_or_evaluator_selection": False,
                "original_attempts": len(original),
                "reserve_attempts": len(reserve),
                "reserve_selected_replacements": len(chosen_rows),
                "reserve_unused_accepted": len(unused_accepted),
                "reserve_rejected": reserve_rejection_count,
            },
        }
    )
    final_batch = VerifiedSFTBatch(
        train_labels=batch.train_labels,
        holdout_labels=batch.holdout_labels,
        audits=batch.audits,
        report=report,
    )
    return StructuralTopupResult(
        batch=final_batch,
        reserve_attempt_audits=tuple(attempt_audits),
        unused_accepted_reserve_labels=tuple(unused_accepted),
        rejected_reserve_labels=tuple(reserve_rejected),
        selection_audit=selection_audit,
    )


def publish_reserve_nontraining_bundle(
    output_dir: str | Path,
    result: StructuralTopupResult,
    *,
    reserve_freeze_audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Seal unused/rejected reserve labels in an explicit nontraining bundle."""

    freeze = _json_clone(dict(reserve_freeze_audit), label="reserve freeze audit")
    freeze_body = {key: value for key, value in freeze.items() if key != "reserve_audit_sha256"}
    if freeze.get("schema") != STRUCTURAL_RESERVE_AUDIT_SCHEMA or freeze.get(
        "reserve_audit_sha256"
    ) != sha256_json(freeze_body):
        raise SolDaggerError("cannot publish against an invalid reserve freeze audit")
    manifest = _publish_bundle(
        output_dir,
        schema=RESERVE_NONTRAINING_BUNDLE_SCHEMA,
        files={
            "unused_accepted_labels.jsonl": _canonical_jsonl(result.unused_accepted_reserve_labels),
            "rejected_labels.jsonl": _canonical_jsonl(result.rejected_reserve_labels),
            "reserve_attempt_audits.jsonl": _canonical_jsonl(result.reserve_attempt_audits),
            "selection_audit.json": (canonical_json(result.selection_audit) + "\n").encode(),
        },
        metadata={
            "training_allowed": False,
            "status": "sealed_nontraining",
            "teacher_model_spec": SOL_MODEL_SPEC,
            "reasoning_effort": SOL_REASONING_EFFORT,
            "reserve_freeze_audit_sha256": freeze["reserve_audit_sha256"],
            "selection_audit_sha256": result.selection_audit["selection_audit_sha256"],
            "unused_accepted_rows": len(result.unused_accepted_reserve_labels),
            "rejected_rows": len(result.rejected_reserve_labels),
            "attempt_rows": len(result.reserve_attempt_audits),
            "response_quality_selection": False,
            "reward_result_or_evaluator_selection": False,
        },
    )
    root = Path(output_dir)
    for path in root.iterdir():
        if path.is_file() and not path.is_symlink():
            path.chmod(0o444)
    return manifest


def _canonical_jsonl(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join((canonical_json(row) + "\n").encode("utf-8") for row in rows)


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise SolDaggerError(f"refusing to overwrite artifact: {path}") from exc


def _publish_bundle(
    output_dir: str | Path,
    *,
    schema: str,
    files: Mapping[str, bytes],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(output_dir)
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise SolDaggerError(f"refusing to reuse artifact directory: {root}") from exc
    entries: dict[str, dict[str, Any]] = {}
    for name, payload in files.items():
        if Path(name).name != name:
            raise SolDaggerError("bundle file names must be flat and relative")
        _write_new(root / name, payload)
        entries[name] = {
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "rows": payload.count(b"\n") if name.endswith(".jsonl") else None,
        }
    body = {
        "schema": schema,
        "files": entries,
        "metadata": _json_clone(dict(metadata), label="bundle metadata"),
    }
    manifest = {**body, "manifest_sha256": sha256_json(body)}
    _write_new(root / "manifest.json", (canonical_json(manifest) + "\n").encode())
    return manifest


def publish_raw_bundle(
    output_dir: str | Path,
    batch: SolLabelBatch,
) -> dict[str, Any]:
    """Create a raw Sol-label bundle; an existing path is never overwritten."""

    return _publish_bundle(
        output_dir,
        schema=RAW_BUNDLE_SCHEMA,
        files={"sol_labels.jsonl": _canonical_jsonl(batch.rows)},
        metadata={
            "teacher_calls": batch.teacher_calls,
            "teacher_model_spec": SOL_MODEL_SPEC,
            "reasoning_effort": SOL_REASONING_EFFORT,
        },
    )


def publish_final_bundle(
    output_dir: str | Path,
    batch: VerifiedSFTBatch,
) -> dict[str, Any]:
    """Create canonical train/holdout/audit artifacts without overwriting."""

    return _publish_bundle(
        output_dir,
        schema=FINAL_BUNDLE_SCHEMA,
        files={
            "train_sft.jsonl": _canonical_jsonl(batch.train_labels),
            "holdout_actions.jsonl": _canonical_jsonl(batch.holdout_labels),
            "label_audits.jsonl": _canonical_jsonl(batch.audits),
            "corpus_audit.json": (canonical_json(batch.report) + "\n").encode(),
        },
        metadata={
            "train_sft_rows": len(batch.train_labels),
            "holdout_action_rows": len(batch.holdout_labels),
            "audit_rows": len(batch.audits),
            "teacher_model_spec": SOL_MODEL_SPEC,
            "reasoning_effort": SOL_REASONING_EFFORT,
        },
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise SolDaggerError(f"{label} must be a regular non-symlink JSON file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SolDaggerError(f"{label} is invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise SolDaggerError(f"{label} must be a JSON object")
    return value


def _repair_parent_binding(path: Path) -> dict[str, Any]:
    receipt = _regular_json(path, label="repair-step24 receipt")
    body_sha = receipt.get("receipt_body_sha256")
    body = {key: value for key, value in receipt.items() if key != "receipt_body_sha256"}
    if (
        receipt.get("schema") != "harness-distill.amazon-r00-repair-sft-training-receipt.v1"
        or receipt.get("status") != "ok"
        or receipt.get("final_step") != 24
        or receipt.get("optimizer_updates") != 1
        or body_sha != sha256_json(body)
    ):
        raise SolDaggerError("repair-step24 parent receipt identity is invalid")
    candidate = receipt.get("candidate")
    final_dcp = receipt.get("final_dcp")
    if (
        not isinstance(candidate, Mapping)
        or candidate.get("name") != "step24-amazon-r00-repair-sft"
        or candidate.get("update") != 24
        or not isinstance(final_dcp, Mapping)
    ):
        raise SolDaggerError("repair-step24 receipt lacks candidate/final DCP identity")
    for name in ("path", "tree_sha256"):
        _require_text(final_dcp.get(name), label=f"repair final_dcp.{name}")
    _require_hex64(final_dcp["tree_sha256"], label="repair final_dcp.tree_sha256")
    for name in ("files", "bytes"):
        value = final_dcp.get(name)
        if type(value) is not int or value < 1:
            raise SolDaggerError(f"repair final_dcp.{name} must be positive")

    plan_path = Path(_require_text(receipt.get("plan_path"), label="repair plan_path")).resolve()
    plan = _regular_json(plan_path, label="repair-step24 plan")
    if _sha256_file(plan_path) != receipt.get("plan_file_sha256"):
        raise SolDaggerError("repair-step24 plan file changed after receipt")
    plan_body_sha = plan.get("plan_body_sha256")
    plan_body = {key: value for key, value in plan.items() if key != "plan_body_sha256"}
    if (
        plan.get("schema") != "harness-distill.amazon-r00-repair-sft-plan.v1"
        or plan_body_sha != receipt.get("plan_body_sha256")
        or plan_body_sha != sha256_json(plan_body)
    ):
        raise SolDaggerError("repair-step24 plan body is not receipt-bound")
    parent_model = _require_text(plan.get("parent_model"), label="repair parent_model")

    retention_path = Path(
        _require_text(receipt.get("dataset_path"), label="repair dataset_path")
    ).resolve()
    if not retention_path.is_file() or retention_path.is_symlink():
        raise SolDaggerError("immutable repair retention dataset is unavailable")
    retention_sha = _sha256_file(retention_path)
    if retention_sha != receipt.get("dataset_sha256") or retention_sha != RETENTION_SOURCE_SHA256:
        raise SolDaggerError("immutable 32-row repair retention dataset hash drifted")
    return {
        "receipt": receipt,
        "receipt_path": str(path.resolve()),
        "receipt_sha256": _sha256_file(path),
        "receipt_body_sha256": body_sha,
        "parent_model": parent_model,
        "final_dcp": _json_clone(final_dcp, label="repair final DCP"),
        "retention_path": retention_path,
        "retention_sha256": retention_sha,
    }


def _retention_records(binding: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = binding["retention_path"]
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                source = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SolDaggerError(
                    f"immutable retention source line {line_number} is invalid JSON"
                ) from exc
            if not isinstance(source, Mapping):
                raise SolDaggerError("immutable retention source contains a non-object row")
            messages = source.get("messages")
            has_tools = "tools" in source
            tools = source.get("tools") if has_tools else None
            if (
                not isinstance(messages, list)
                or not messages
                or not all(isinstance(message, Mapping) for message in messages)
                or messages[-1].get("role") != "assistant"
                or (has_tools and not isinstance(tools, list))
            ):
                raise SolDaggerError("immutable retention row is not assistant-final chat data")
            record = {
                "kind": "retention",
                "messages": _json_clone(messages, label="retention messages"),
                "retention_source_line": line_number,
                "retention_source_sha256": binding["retention_sha256"],
            }
            if has_tools:
                record["tools"] = _json_clone(tools, label="retention tools")
            rows.append(record)
    if len(rows) != 32 or [row["retention_source_line"] for row in rows] != list(range(1, 33)):
        raise SolDaggerError("immutable repair retention source must contain exactly 32 rows")
    return rows


def _corrective_records(
    labels: Sequence[Mapping[str, Any]],
    audits: Mapping[str, Mapping[str, Any]],
    *,
    split: str,
) -> list[dict[str, Any]]:
    if split not in SPLITS:
        raise SolDaggerError("corrective audit split must be train or holdout")
    # Collection sidecars use ``holdout`` to match the rollout campaign.  The
    # immutable training handoff uses ``heldout`` so it cannot be mistaken for
    # a trainable split by downstream loaders.
    published_split = "heldout" if split == "holdout" else split
    teacher = {
        "model": SOL_LOGICAL_MODEL,
        "effort": SOL_REASONING_EFFORT,
        "provider": "trapi",
        "model_spec": SOL_MODEL_SPEC,
    }
    rows: list[dict[str, Any]] = []
    for label in labels:
        state_id = label.get("state_id")
        audit = audits.get(str(state_id))
        if audit is None or audit.get("split") != split:
            raise SolDaggerError("corrective label has no matching split audit")
        messages = label.get("messages")
        tools = label.get("tools", [])
        if (
            not isinstance(messages, list)
            or not messages
            or not isinstance(messages[-1], Mapping)
            or messages[-1].get("role") != "assistant"
            or not isinstance(messages[-1].get("content"), str)
            or not messages[-1]["content"]
            or not isinstance(tools, list)
        ):
            raise SolDaggerError("corrective row is not content-bearing assistant chat data")
        rows.append(
            {
                "kind": "corrective",
                "state_id": state_id,
                "split": published_split,
                "source_variant": audit["variant"],
                "phase": audit["phase"],
                "messages": _json_clone(messages, label="corrective messages"),
                "tools": _json_clone(tools, label="corrective tools"),
                "teacher": teacher,
                "request_sha256": label["request_sha256"],
                "effective_request_sha256": label["effective_request_sha256"],
                "prompt_sha256": label["prompt_sha256"],
                "teacher_request_sha256": label["teacher_request_sha256"],
                "teacher_completion_sha256": label["teacher_completion_sha256"],
                "teacher_harness_projection_sha256": audit["invariance_audit"][
                    "teacher_harness_projection_sha256"
                ],
            }
        )
    return rows


def publish_training_collection(
    output_dir: str | Path,
    batch: VerifiedSFTBatch,
    policy: CampaignPolicy,
    *,
    parent_receipt_path: str | Path,
    evaluation_contract_path: str | Path,
    evaluation_contract_sha256: str,
    source_git_sha: str,
) -> dict[str, Any]:
    """Publish the exact 64+32 train / 24 held-out collection contract."""

    if len(source_git_sha) != 40 or any(character not in _HEX64 for character in source_git_sha):
        raise SolDaggerError("source_git_sha must be 40 lowercase hexadecimal characters")
    expected_eval_sha = _require_hex64(
        evaluation_contract_sha256, label="evaluation_contract_sha256"
    )
    evaluation_path = Path(evaluation_contract_path).resolve()
    if (
        not evaluation_path.is_file()
        or evaluation_path.is_symlink()
        or _sha256_file(evaluation_path) != expected_eval_sha
    ):
        raise SolDaggerError("frozen evaluation contract path/hash changed")
    if len(batch.train_labels) != 64 or len(batch.holdout_labels) != 24:
        raise SolDaggerError("training handoff requires exactly 64 train and 24 held-out labels")
    if batch.report.get("accepted") != 88:
        raise SolDaggerError("training handoff corpus audit does not prove 88 accepted labels")

    audits_by_state = {
        str(row.get("state_id")): row
        for row in batch.audits
        if row.get("action_validation", {}).get("status") == "accepted"
    }
    if len(audits_by_state) != 88:
        raise SolDaggerError("training handoff lacks 88 accepted action audits")
    train_rows = _corrective_records(batch.train_labels, audits_by_state, split="train")
    holdout_rows = _corrective_records(batch.holdout_labels, audits_by_state, split="holdout")
    variant_counts = Counter(
        (row["split"], row["source_variant"]) for row in [*train_rows, *holdout_rows]
    )
    phase_counts = Counter((row["split"], row["phase"]) for row in [*train_rows, *holdout_rows])
    for variant in LAPTOP_VARIANTS:
        if variant_counts[("train", variant)] != 16 or variant_counts[("heldout", variant)] != 6:
            raise SolDaggerError("final corrective variant balance is not exactly 16/6")
    for phase in DECISION_PHASES:
        if phase_counts[("train", phase)] < 10 or phase_counts[("heldout", phase)] < 4:
            raise SolDaggerError("final corrective phase coverage is below 10/4")

    all_corrective = [*train_rows, *holdout_rows]
    state_ids = [row["state_id"] for row in all_corrective]
    if len(set(state_ids)) != 88:
        raise SolDaggerError("corrective train/held-out rows contain duplicate states")
    example_signatures = [
        sha256_json({"messages": row["messages"], "tools": row["tools"]}) for row in all_corrective
    ]
    if len(set(example_signatures)) != 88:
        raise SolDaggerError("corrective train/held-out rows silently duplicate an example")

    rejected = batch.report.get("rejected")
    if type(rejected) is not int or rejected < 0:
        raise SolDaggerError("training handoff corpus audit has invalid rejection count")
    attempted = batch.report.get("attempted", 88 + rejected)
    if type(attempted) is not int or attempted < 88 + rejected:
        raise SolDaggerError("training handoff corpus audit has invalid attempt count")
    exclusion_rate = rejected / attempted
    if exclusion_rate > 0.25:
        raise SolDaggerError("training handoff exclusions exceed the 25% campaign gate")
    reported_exclusion = batch.report.get("exclusion_fraction")
    if reported_exclusion is not None and (
        isinstance(reported_exclusion, bool)
        or not isinstance(reported_exclusion, (int, float))
        or not math.isclose(float(reported_exclusion), exclusion_rate, rel_tol=0.0, abs_tol=1e-12)
    ):
        raise SolDaggerError("training handoff exclusion fraction drifted")
    if (
        batch.report.get("model_visible_sidecar_fields") != 0
        or batch.report.get("provider_reasoning_targets") != 0
    ):
        raise SolDaggerError("training handoff does not prove a clean model-visible corpus")
    accepted_action_audits = [
        audits_by_state[state_id]["action_validation"] for state_id in state_ids
    ]
    if any(not isinstance(audit, Mapping) for audit in accepted_action_audits):
        raise SolDaggerError("training handoff lacks structured action-validation audits")
    validation_tiers = Counter(audit.get("validation_tier") for audit in accepted_action_audits)
    if set(validation_tiers) - {"executed", "static_visible_state"}:
        raise SolDaggerError("training handoff has an unsupported action-validation tier")
    for audit in accepted_action_audits:
        flags = audit.get("flags")
        if not isinstance(flags, Mapping):
            raise SolDaggerError("training handoff action audit lacks validation flags")
        if audit.get("validation_tier") == "static_visible_state" and (
            flags.get("execution_verified") is not False
            or flags.get("postcondition_verified") is not False
        ):
            raise SolDaggerError("static validation fabricates execution/postcondition proof")
    execution_verified_actions = sum(
        audit["flags"].get("execution_verified") is True for audit in accepted_action_audits
    )
    postcondition_verified_actions = sum(
        audit["flags"].get("postcondition_verified") is True for audit in accepted_action_audits
    )
    if (
        batch.report.get("accepted_validation_tier_counts")
        != dict(sorted(validation_tiers.items()))
        or batch.report.get("execution_verified_actions") != execution_verified_actions
        or batch.report.get("postcondition_verified_actions") != postcondition_verified_actions
    ):
        raise SolDaggerError("training handoff action-validation disclosure drifted")

    parent = _repair_parent_binding(Path(parent_receipt_path).resolve())
    retention = _retention_records(parent)
    records = [*train_rows, *retention]
    if len(records) != 96:
        raise SolDaggerError("training records must be exactly 64 corrective + 32 retention")
    root = Path(output_dir).resolve()
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise SolDaggerError(f"refusing to reuse artifact directory: {root}") from exc
    records_path = root / "records.jsonl"
    heldout_path = root / "heldout_actions.jsonl"
    audits_path = root / "label_audits.jsonl"
    corpus_path = root / "corpus_audit.json"
    _write_new(records_path, _canonical_jsonl(records))
    _write_new(heldout_path, _canonical_jsonl(holdout_rows))
    _write_new(audits_path, _canonical_jsonl(batch.audits))
    _write_new(corpus_path, (canonical_json(batch.report) + "\n").encode())

    excluded = rejected
    teacher = {
        "model": SOL_LOGICAL_MODEL,
        "effort": SOL_REASONING_EFFORT,
        "provider": "trapi",
        "model_spec": SOL_MODEL_SPEC,
    }
    campaign = {
        "target_train_corrective": 64,
        "target_heldout": 24,
        "min_train_corrective": 64,
        "min_heldout": 24,
        "variants": {
            variant: {
                "train": variant_counts[("train", variant)],
                "heldout": variant_counts[("heldout", variant)],
            }
            for variant in LAPTOP_VARIANTS
        },
        "phases": {
            phase: {
                "train": phase_counts[("train", phase)],
                "heldout": phase_counts[("heldout", phase)],
            }
            for phase in DECISION_PHASES
        },
        "exclusions": {
            "count": excluded,
            "total": attempted,
            "rate": exclusion_rate,
        },
        "retention": {"immutable_repair_rows": 32},
    }
    invariance = {
        "student_harness_unchanged": True,
        "teacher_harness_projection_byte_identical": True,
        "teacher_saw_student_action": False,
        "teacher_saw_student_response": False,
        "teacher_saw_hidden_database": False,
        "teacher_saw_outcomes": False,
        "teacher_saw_sidecar_or_evaluator": False,
        "evaluation_harness_unchanged": True,
        "model_visible_sidecar_fields": 0,
        "provider_reasoning_targets": 0,
        "harness_id": "browseruse-deliberative",
        "harness_fingerprint_sha256": policy.harness_fingerprint_sha256,
        "evaluation_fingerprint_sha256": expected_eval_sha,
        "provider_only_differences": [
            "model",
            "reasoning_effort",
            "max_completion_tokens",
            "n",
            "stream",
        ],
    }
    body = {
        "schema": COLLECTION_MANIFEST_SCHEMA,
        "status": "verified",
        "source_git_sha": source_git_sha,
        "teacher_model": SOL_LOGICAL_MODEL,
        "teacher_reasoning_effort": SOL_REASONING_EFFORT,
        "teacher": teacher,
        "assistant_tokens_only": True,
        "qwen_rendering": {"assistant_only": True, "seq_len": 32_768},
        "records_path": str(records_path),
        "records_sha256": _sha256_file(records_path),
        "row_count": 96,
        "row_balance": {"corrective": 64, "retention": 32},
        "train_corrective_count": 64,
        "heldout_path": str(heldout_path),
        "heldout_sha256": _sha256_file(heldout_path),
        "heldout_count": 24,
        "audit_path": str(audits_path),
        "audit_sha256": _sha256_file(audits_path),
        "corpus_audit_path": str(corpus_path),
        "corpus_audit_sha256": _sha256_file(corpus_path),
        "campaign": campaign,
        "action_validation": {
            "accepted_tiers": dict(sorted(validation_tiers.items())),
            "execution_verified_actions": execution_verified_actions,
            "postcondition_verified_actions": postcondition_verified_actions,
            "offline_static_actions_executed": False,
        },
        "invariance": invariance,
        "parent_receipt_path": parent["receipt_path"],
        "parent_receipt_sha256": parent["receipt_sha256"],
        "parent_receipt_body_sha256": parent["receipt_body_sha256"],
        "parent_model": parent["parent_model"],
        "parent_final_dcp": parent["final_dcp"],
        "retention_source_path": str(parent["retention_path"]),
        "retention_source_sha256": parent["retention_sha256"],
        "retention_source_row_count": 32,
        "retention_provenance": {
            "source_path": str(parent["retention_path"]),
            "source_sha256": parent["retention_sha256"],
            "row_count": 32,
            "immutable": True,
        },
        "evaluation_contract_path": str(evaluation_path),
        "evaluation_contract_sha256": expected_eval_sha,
    }
    manifest = {**body, "manifest_body_sha256": sha256_json(body)}
    _write_new(
        root / "collection_manifest.json",
        (canonical_json(manifest) + "\n").encode(),
    )
    return manifest


__all__ = [
    "ACTION_VALIDATION_SCHEMA",
    "CAPTURE_SCHEDULE_SCHEMA",
    "CAPTURE_SIDECAR_SCHEMA",
    "COLLECTION_MANIFEST_SCHEMA",
    "CORRECTIVE_SFT_SCHEMA",
    "DECISION_PHASES",
    "INTERVENTION_LABEL_SCHEMA",
    "LAPTOP_VARIANTS",
    "RESERVE_NONTRAINING_BUNDLE_SCHEMA",
    "SHADOW_LABEL_SCHEMA",
    "SOL_MODEL_SPEC",
    "STATIC_VISIBLE_VALIDATION_SCHEMA",
    "STRUCTURAL_RESERVE_AUDIT_SCHEMA",
    "AgentArenaSolLowTeacher",
    "CampaignPolicy",
    "SolDaggerError",
    "SolLabelBatch",
    "StructuralTopupResult",
    "VerifiedSFTBatch",
    "capture_qwen_states",
    "label_qwen_states",
    "make_capture_sidecars",
    "make_bundle_capture_schedule",
    "make_structural_reserve_schedule",
    "make_sol_low_request",
    "make_sol_low_responses_request",
    "make_static_visible_action_validations",
    "materialize_verified_sft",
    "materialize_structural_topup",
    "publish_final_bundle",
    "publish_raw_bundle",
    "publish_reserve_nontraining_bundle",
    "publish_training_collection",
    "strip_provider_reasoning",
    "static_visible_validator_sha256",
]
