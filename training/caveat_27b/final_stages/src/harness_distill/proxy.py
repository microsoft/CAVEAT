"""Audited OpenAI-compatible proxy for Qwen agent rollouts.

Browser-use 0.13.6 does not expose arbitrary ``extra_body`` parameters and
does not preserve a provider's separate ``reasoning_content`` field.  This
small compatibility layer does exactly three model-family operations, applied
identically to base and trained checkpoints:

* installs the vendor-recommended Qwen3.6 sampling/chat-template options;
* restores prior ``reasoning_content`` on later requests in the same session;
* records request/response pairs for exact-token materialization offline.

It does not change the system prompt, add tools, inspect a storefront, or know
anything about a benchmark task.  Authorization headers are never persisted.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

_ROLE_HEADER = "x-harness-distill-role"
_SESSION_HEADER = "x-harness-distill-session"
SESSION_ID_ENV = "HARNESS_DISTILL_SESSION_ID"
_NULLABLE_MESSAGE_ENVELOPE_FIELDS = frozenset(
    {"refusal", "annotations", "audio", "function_call"}
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_qwen_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the reviewed vLLM 0.24 Qwen message wire dialect.

    vLLM names the parser-separated hidden field ``reasoning`` and serializes
    several nullable OpenAI envelope fields.  The pinned renderer and offline
    replay schema use ``reasoning_content``.  Only that exact rename and null
    elision are allowed; a non-null unsupported envelope fails closed.
    """

    try:
        normalized = json.loads(json.dumps(dict(message), ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("Qwen response message must be finite JSON") from exc
    wire_reasoning = normalized.pop("reasoning", None)
    canonical_reasoning = normalized.get("reasoning_content")
    for field_name, value in (
        ("reasoning", wire_reasoning),
        ("reasoning_content", canonical_reasoning),
    ):
        if value is not None and not isinstance(value, str):
            raise ValueError(f"Qwen response {field_name} must be text or null")
    if wire_reasoning not in {None, ""}:
        if canonical_reasoning not in {None, "", wire_reasoning}:
            raise ValueError("Qwen response has conflicting reasoning fields")
        normalized["reasoning_content"] = wire_reasoning
    elif canonical_reasoning == "":
        normalized.pop("reasoning_content", None)
    for field_name in _NULLABLE_MESSAGE_ENVELOPE_FIELDS:
        if field_name not in normalized:
            continue
        if normalized[field_name] is not None:
            raise ValueError(
                f"Qwen response has unsupported non-null message field {field_name!r}"
            )
        normalized.pop(field_name)
    return normalized


def canonical_qwen_response(response: Mapping[str, Any]) -> dict[str, Any]:
    """Apply :func:`canonical_qwen_message` to each response choice."""

    try:
        normalized = json.loads(json.dumps(dict(response), ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("Qwen response must be finite JSON") from exc
    choices = normalized.get("choices")
    if not isinstance(choices, list):
        return normalized
    for index, choice in enumerate(choices):
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), Mapping):
            raise ValueError(f"Qwen response choices[{index}] has no message object")
        choice["message"] = canonical_qwen_message(choice["message"])
    return normalized


def _assistant_state_key(messages: list[dict[str, Any]], index: int) -> str:
    """Fingerprint the complete provider-visible prefix for one assistant turn.

    Qwen's separate reasoning field is intentionally omitted: it is precisely
    the state being restored.  Content alone is not an identity because tool
    calls commonly have null/repeated content.  Binding the complete causal
    prefix also disambiguates otherwise identical assistant messages at
    different positions in a session.
    """

    if type(index) is not int or index < 0 or index >= len(messages):
        raise ValueError("assistant message index is out of range")
    if not isinstance(messages[index], Mapping) or messages[index].get("role") != "assistant":
        raise ValueError("reasoning state key requires an assistant message")
    prefix: list[dict[str, Any]] = []
    for position, message in enumerate(messages[: index + 1]):
        if not isinstance(message, Mapping):
            raise ValueError(f"messages[{position}] must be an object")
        visible = {key: value for key, value in message.items() if key != "reasoning_content"}
        prefix.append(visible)
    return sha256_json(prefix)


def _default_session(messages: list[dict[str, Any]]) -> str:
    """Derive a stable per-run key without relying on a provider extension."""

    anchors: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") in {"system", "user"}:
            anchors.append({"role": message.get("role"), "content": message.get("content")})
        if len(anchors) >= 2:
            break
    return sha256_json(anchors)


def qwen_request(
    body: Mapping[str, Any],
    reasoning_by_assistant_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the sole permitted Qwen compatibility transformation."""

    outgoing = json.loads(json.dumps(dict(body), ensure_ascii=False))
    messages = outgoing.get("messages")
    if not isinstance(messages, list):
        raise ValueError("chat request must contain a messages list")
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        key = _assistant_state_key(messages, index)
        prior = reasoning_by_assistant_state.get(key)
        if prior not in {None, ""} and not message.get("reasoning_content"):
            message["reasoning_content"] = prior

    # Browser-use sets temperature/frequency_penalty for generic non-OpenAI
    # models. Qwen3.6's model card specifies this thinking-mode configuration.
    outgoing["temperature"] = 1.0
    outgoing["top_p"] = 0.95
    outgoing["presence_penalty"] = 0.0
    outgoing["repetition_penalty"] = 1.0
    outgoing.pop("frequency_penalty", None)
    # ``extra_body`` is an OpenAI *client* argument, not an OpenAI wire field.
    # Browser-use has already gone through its SDK when it reaches this proxy,
    # while our hop to vLLM is raw HTTP.  Expand a caller-supplied mapping and
    # put the Qwen extensions at the top level so they actually reach vLLM.
    client_extra = outgoing.pop("extra_body", {})
    if not isinstance(client_extra, dict):
        raise ValueError("extra_body must be an object when supplied")
    for key, value in client_extra.items():
        outgoing.setdefault(key, value)
    template_kwargs = outgoing.get("chat_template_kwargs", {})
    if not isinstance(template_kwargs, dict):
        raise ValueError("chat_template_kwargs must be an object when supplied")
    outgoing.update({"top_k": 20, "min_p": 0.0})
    outgoing["chat_template_kwargs"] = {
        **template_kwargs,
        "preserve_thinking": True,
    }
    return outgoing


@dataclass
class ProxyState:
    upstream_base_url: str
    upstream_api_key: str = ""
    trace_path: Path | None = None
    client: httpx.AsyncClient | None = None
    rollout_session_id: str | None = None
    reasoning: dict[str, dict[str, Any]] = field(default_factory=dict)
    session_sequences: dict[str, int] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _sequence: int = 0

    async def append_trace(self, record: dict[str, Any]) -> None:
        if self.trace_path is None:
            return
        async with self._lock:
            self._sequence += 1
            session = record.get("session_sha256")
            if not isinstance(session, str) or not session:
                raise ValueError("proxy trace requires a session identity")
            session_sequence = self.session_sequences.get(session, 0) + 1
            self.session_sequences[session] = session_sequence
            record = {
                "sequence": self._sequence,
                "session_sequence": session_sequence,
                **record,
            }
            self.trace_path.parent.mkdir(parents=True, exist_ok=True)
            with self.trace_path.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json(record) + "\n")

    def http(self) -> httpx.AsyncClient:
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=None)
        return self.client


def create_app(state: ProxyState | None = None) -> FastAPI:
    if state is None:
        upstream = os.environ.get("HARNESS_DISTILL_UPSTREAM_BASE_URL", "").rstrip("/")
        if not upstream:
            raise RuntimeError("HARNESS_DISTILL_UPSTREAM_BASE_URL is required")
        trace = os.environ.get("HARNESS_DISTILL_TRACE_JSONL", "").strip()
        state = ProxyState(
            upstream_base_url=upstream,
            upstream_api_key=os.environ.get("HARNESS_DISTILL_UPSTREAM_API_KEY", ""),
            trace_path=Path(trace) if trace else None,
            rollout_session_id=os.environ.get(SESSION_ID_ENV, "").strip() or None,
        )

    app = FastAPI(title="harness-distill-qwen-proxy")
    app.state.proxy = state

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "schema": "harness-distill.proxy-health.v1",
            "sampling": {
                "temperature": 1.0,
                "top_p": 0.95,
                "top_k": 20,
                "presence_penalty": 0.0,
                "preserve_thinking": True,
            },
        }

    @app.api_route("/v1/{path:path}", methods=["GET", "POST"])
    async def forward(
        path: str,
        request: Request,
        x_harness_distill_role: str = Header(default="student"),
        x_harness_distill_session: str | None = Header(default=None),
    ) -> Response:
        if path != "chat/completions":
            return await _passthrough(state, path, request)
        if request.method != "POST":
            raise HTTPException(status_code=405, detail="chat completions requires POST")
        try:
            body = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="invalid JSON") from exc
        if body.get("stream"):
            raise HTTPException(
                status_code=400,
                detail="streaming is not supported by the audited proxy",
            )
        messages = body.get("messages")
        if not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="messages must be a list")
        # A rollout worker owns one isolated proxy process and supplies one
        # fresh episode identity through process environment. The header stays
        # available for callers which intentionally multiplex a proxy. The
        # message-derived fallback is not used by campaign workers because a
        # browser-use state message changes at every decision.
        session = (
            x_harness_distill_session
            or state.rollout_session_id
            or _default_session(messages)
        )
        session_reasoning = state.reasoning.setdefault(session, {})
        try:
            outgoing = qwen_request(body, session_reasoning)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        started = time.time()
        upstream = await state.http().post(
            f"{state.upstream_base_url}/v1/chat/completions",
            headers=_upstream_headers(state),
            json=outgoing,
        )
        elapsed = time.time() - started
        try:
            response_body = upstream.json()
        except ValueError:
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type"),
            )

        if upstream.is_success:
            try:
                response_body = canonical_qwen_response(response_body)
            except ValueError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"unsupported upstream Qwen response: {exc}",
                ) from exc

        if upstream.is_success:
            for choice in response_body.get("choices", []):
                message = choice.get("message") or {}
                reasoning_content = message.get("reasoning_content")
                if reasoning_content not in {None, ""}:
                    history = [*outgoing["messages"], message]
                    session_reasoning[_assistant_state_key(history, len(history) - 1)] = (
                        reasoning_content
                    )

        await state.append_trace(
            {
                "schema": "harness-distill.proxy-trace.v1",
                "timestamp": time.time(),
                "elapsed_seconds": elapsed,
                "session_sha256": hashlib.sha256(session.encode("utf-8")).hexdigest(),
                "role": x_harness_distill_role,
                "request_sha256": sha256_json(body),
                "effective_request_sha256": sha256_json(outgoing),
                "request": body,
                "effective_request": outgoing,
                "response": response_body,
                "status_code": upstream.status_code,
            }
        )
        return JSONResponse(content=response_body, status_code=upstream.status_code)

    return app


async def _passthrough(state: ProxyState, path: str, request: Request) -> Response:
    method = request.method.upper()
    response = await state.http().request(
        method,
        f"{state.upstream_base_url}/v1/{path}",
        headers=_upstream_headers(state),
        content=await request.body(),
        params=request.query_params,
    )
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type"),
    )


def _upstream_headers(state: ProxyState) -> dict[str, str]:
    headers = {"content-type": "application/json"}
    if state.upstream_api_key:
        headers["authorization"] = f"Bearer {state.upstream_api_key}"
    return headers


def app_from_environment() -> FastAPI:
    """Uvicorn factory entrypoint."""

    return create_app()
