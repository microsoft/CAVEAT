"""Same-state Sol correction when Qwen proposes the visible ``Buy Now`` control.

This is a deliberately narrow, train-only DAgger proxy.  Qwen rolls in
normally.  A BrowserUse response is rejected only when its structured click
index resolves to the visible ``Buy Now`` button in the exact current browser
state.  Sol-low receives the same effective request and its answer is executed
instead.  The next BrowserUse request is retained as immediate-successor
evidence for the correction pair.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .interactive_sol_dagger import (
    _qwen_call,
    _remember_qwen_reasoning,
    _teacher_wire_response,
    is_browseruse_request,
)
from .proxy import (
    SESSION_ID_ENV,
    ProxyState,
    _default_session,
    _passthrough,
    qwen_request,
    sha256_json,
)
from .sol_dagger_collection import (
    SOL_LOGICAL_MODEL,
    SOL_MODEL_SPEC,
    SOL_REASONING_EFFORT,
    AgentArenaSolLowTeacher,
    SolLowTeacher,
    make_sol_low_request,
    strip_provider_reasoning,
)

TRACE_SCHEMA = "harness-distill.adaptive-buy-now-dagger-trace.v1"
HEALTH_SCHEMA = "harness-distill.adaptive-buy-now-dagger-health.v1"


class AdaptiveBuyNowError(RuntimeError):
    """The semantic intervention could not be established safely."""


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _latest_browser_state(request: Mapping[str, Any]) -> str:
    messages = request.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, Mapping) or message.get("role") not in {"user", "tool"}:
            continue
        content = message.get("content")
        if not isinstance(content, str) or "<browser_state>" not in content:
            continue
        return content.rsplit("<browser_state>", 1)[-1].split("</browser_state>", 1)[0]
    return ""


def _response_content(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        return ""
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    return content if isinstance(content, str) else ""


def _click_indices(response: Mapping[str, Any]) -> set[int]:
    """Read only structured click members, ignoring prose mentions."""

    content = _response_content(response)
    return {int(match) for match in re.findall(r'"click"\s*:\s*\{\s*"index"\s*:\s*(\d+)', content)}


def _control_label(browser_state: str, index: int) -> str:
    lines = browser_state.splitlines()
    anchor = re.compile(rf"\*?\[{index}\]<(?:button|a)\b[^>]*?/?>", re.IGNORECASE)
    indexed = re.compile(r"\*?\[\d+\]<")
    for position, line in enumerate(lines):
        match = anchor.search(line)
        if match is None:
            continue
        pieces = [line[match.end() :].strip()]
        for following in lines[position + 1 : position + 4]:
            if indexed.search(following):
                break
            pieces.append(following.strip())
        visible = " ".join(piece for piece in pieces if piece)
        visible = re.sub(r"<!--.*?-->", " ", visible)
        visible = re.sub(r"</?[^>]+>", " ", visible)
        return " ".join(visible.split())
    return ""


def buy_now_click(
    response: Mapping[str, Any], request: Mapping[str, Any]
) -> tuple[bool, list[int]]:
    """Return exact click indices that bind to a visible Buy Now control."""

    state = _latest_browser_state(request)
    matched = sorted(
        index
        for index in _click_indices(response)
        if re.fullmatch(r"buy\s+now(?:\s*!+)?", _control_label(state, index), re.IGNORECASE)
    )
    return bool(matched), matched


def _url_path(browser_state: str) -> str:
    match = re.search(r"(?mi)^Current URL:\s*(\S+)", browser_state)
    return urlparse(match.group(1)).path if match else ""


@dataclass(slots=True)
class PendingPair:
    state_id: str
    request_sha256: str
    browser_state_sha256: str
    before_url_path: str
    teacher_completion_sha256: str
    teacher_action_click_indices: tuple[int, ...]


@dataclass
class AdaptiveBuyNowState(ProxyState):
    rollout_id: str = ""
    variant: str = ""
    campaign_id: str = "campaign2-step26-adaptive-buy-now-dagger-r2"
    teacher: SolLowTeacher | None = None
    pending: dict[str, PendingPair] = field(default_factory=dict)
    completed: set[str] = field(default_factory=set)


def _successor_evidence(pending: PendingPair, request: Mapping[str, Any]) -> dict[str, Any]:
    after = _latest_browser_state(request)
    after_path = _url_path(after)
    request_sha = sha256_json(request)
    changed = request_sha != pending.request_sha256
    stayed_out_of_direct_checkout = after_path != "/gp/buy/spc"
    no_order_confirmation = (
        "/gp/buy/thankyou" not in after_path and "order placed" not in after.casefold()
    )
    valid = bool(after and changed and stayed_out_of_direct_checkout and no_order_confirmation)
    return {
        "state_id": pending.state_id,
        "successor_validated": valid,
        "request_changed": changed,
        "before_url_path": pending.before_url_path,
        "after_url_path": after_path,
        "before_browser_state_sha256": pending.browser_state_sha256,
        "after_browser_state_sha256": hashlib.sha256(after.encode()).hexdigest(),
        "direct_checkout_avoided": stayed_out_of_direct_checkout,
        "order_not_placed": no_order_confirmation,
        "teacher_action_click_indices": list(pending.teacher_action_click_indices),
        "teacher_completion_sha256": pending.teacher_completion_sha256,
    }


def create_adaptive_app(state: AdaptiveBuyNowState) -> FastAPI:
    if not state.rollout_id or not state.variant:
        raise AdaptiveBuyNowError("rollout_id and variant are required")
    app = FastAPI(title="harness-distill-adaptive-buy-now-dagger")
    app.state.proxy = state

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "schema": HEALTH_SCHEMA,
            "campaign": state.campaign_id,
            "rollout_id": state.rollout_id,
            "variant": state.variant,
            "trigger": "structured_click_index_resolves_to_visible_buy_now",
            "teacher": "gpt-5.6-sol#low",
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
        if not isinstance(body, Mapping) or body.get("stream"):
            raise HTTPException(status_code=400, detail="non-streaming object required")
        messages = body.get("messages")
        if not isinstance(messages, list):
            raise HTTPException(status_code=400, detail="messages must be a list")
        session = (
            x_harness_distill_session or state.rollout_session_id or _default_session(messages)
        )
        try:
            outgoing = qwen_request(body, state.reasoning.setdefault(session, {}))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        is_browser = is_browseruse_request(outgoing)
        if is_browser and session in state.pending:
            pending = state.pending.pop(session)
            successor = _successor_evidence(pending, outgoing)
            await state.append_trace(
                {
                    "schema": TRACE_SCHEMA,
                    "timestamp": time.time(),
                    "session_sha256": hashlib.sha256(session.encode()).hexdigest(),
                    "role": x_harness_distill_role,
                    "route": "successor",
                    "is_browseruse": True,
                    "policy": "same_state_buy_now_intervention",
                    "request_sha256": sha256_json(body),
                    "effective_request_sha256": sha256_json(outgoing),
                    "effective_request": _clone(outgoing),
                    **successor,
                }
            )
            if successor["successor_validated"]:
                state.completed.add(session)

        started = time.time()
        try:
            qwen_upstream, qwen_response = await _qwen_call(state, outgoing)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"candidate failure: {exc}") from exc
        if not qwen_upstream.is_success:
            await state.append_trace(
                {
                    "schema": TRACE_SCHEMA,
                    "timestamp": time.time(),
                    "elapsed_seconds": time.time() - started,
                    "session_sha256": hashlib.sha256(session.encode()).hexdigest(),
                    "role": x_harness_distill_role,
                    "route": "student",
                    "is_browseruse": is_browser,
                    "request_sha256": sha256_json(body),
                    "effective_request_sha256": sha256_json(outgoing),
                    "status_code": qwen_upstream.status_code,
                    "response": qwen_response,
                }
            )
            return JSONResponse(qwen_response, status_code=qwen_upstream.status_code)
        _remember_qwen_reasoning(state, session, outgoing, qwen_response)

        buy_now_triggered, trigger_indices = (
            buy_now_click(qwen_response, outgoing) if is_browser else (False, [])
        )
        trigger_kind = "visible_pdp_buy_now_click" if buy_now_triggered else None
        if trigger_kind is None:
            await state.append_trace(
                {
                    "schema": TRACE_SCHEMA,
                    "timestamp": time.time(),
                    "elapsed_seconds": time.time() - started,
                    "session_sha256": hashlib.sha256(session.encode()).hexdigest(),
                    "role": x_harness_distill_role,
                    "route": "student",
                    "is_browseruse": is_browser,
                    "semantic_trigger": False,
                    "request_sha256": sha256_json(body),
                    "effective_request_sha256": sha256_json(outgoing),
                    "response": qwen_response,
                    "response_sha256": sha256_json(qwen_response),
                    "status_code": 200,
                }
            )
            return JSONResponse(qwen_response, status_code=200)

        teacher = state.teacher or AgentArenaSolLowTeacher()
        state.teacher = teacher
        try:
            descriptor = dict(teacher.descriptor())
            expected = {
                "provider": "trapi",
                "model_spec": SOL_MODEL_SPEC,
                "logical_model": SOL_LOGICAL_MODEL,
                "wire_model": teacher.wire_model,
                "reasoning_effort": SOL_REASONING_EFFORT,
            }
            if descriptor != expected:
                raise AdaptiveBuyNowError("teacher is not exactly gpt-5.6-sol#low")
            teacher_request, invariance = make_sol_low_request(
                outgoing, wire_model=teacher.wire_model, max_completion_tokens=8192
            )
            raw_teacher = await teacher.complete(teacher_request)
            choices = raw_teacher.get("choices") if isinstance(raw_teacher, Mapping) else None
            message = (
                choices[0].get("message")
                if isinstance(choices, list)
                and len(choices) == 1
                and isinstance(choices[0], Mapping)
                else None
            )
            if not isinstance(message, Mapping):
                raise AdaptiveBuyNowError("Sol teacher response has no unique assistant message")
            completion = strip_provider_reasoning(message, baseline_request=outgoing)
            served = _teacher_wire_response(raw_teacher, completion)
            repeated_buy_now, repeated_indices = buy_now_click(served, outgoing)
            if repeated_buy_now:
                raise AdaptiveBuyNowError(
                    f"Sol repeated prohibited Buy Now click at {repeated_indices}"
                )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"adaptive teacher failure: {exc}") from exc

        before = _latest_browser_state(outgoing)
        state_id = hashlib.sha256(
            f"{state.rollout_id}:{sha256_json(outgoing)}".encode()
        ).hexdigest()
        pending = PendingPair(
            state_id=state_id,
            request_sha256=sha256_json(outgoing),
            browser_state_sha256=hashlib.sha256(before.encode()).hexdigest(),
            before_url_path=_url_path(before),
            teacher_completion_sha256=sha256_json(completion),
            teacher_action_click_indices=tuple(sorted(_click_indices(served))),
        )
        await state.append_trace(
            {
                "schema": TRACE_SCHEMA,
                "timestamp": time.time(),
                "elapsed_seconds": time.time() - started,
                "session_sha256": hashlib.sha256(session.encode()).hexdigest(),
                "role": x_harness_distill_role,
                "route": "teacher_intervention",
                "is_browseruse": True,
                "semantic_trigger": True,
                "trigger_kind": trigger_kind,
                "trigger_click_indices": trigger_indices,
                "state_id": state_id,
                "request_sha256": sha256_json(body),
                "effective_request_sha256": sha256_json(outgoing),
                "effective_request": _clone(outgoing),
                "qwen_rejected_response": qwen_response,
                "qwen_rejected_response_sha256": sha256_json(qwen_response),
                "teacher": descriptor,
                "teacher_request": _clone(teacher_request),
                "teacher_request_sha256": sha256_json(teacher_request),
                "teacher_completion": completion,
                "teacher_completion_sha256": sha256_json(completion),
                "teacher_transport": _clone(raw_teacher.get("_harness_distill_transport")),
                "invariance_audit": invariance,
                "response": served,
                "response_sha256": sha256_json(served),
                "status_code": 200,
            }
        )
        state.pending[session] = pending
        return JSONResponse(served, status_code=200)

    return app


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def app_from_environment() -> FastAPI:
    rollout_id = _required_env("HARNESS_DISTILL_ROLLOUT_ID")
    state = AdaptiveBuyNowState(
        upstream_base_url=_required_env("HARNESS_DISTILL_UPSTREAM_BASE_URL").rstrip("/"),
        upstream_api_key=os.environ.get("HARNESS_DISTILL_UPSTREAM_API_KEY", ""),
        trace_path=Path(_required_env("HARNESS_DISTILL_TRACE_JSONL")),
        rollout_session_id=os.environ.get(SESSION_ID_ENV, "").strip() or rollout_id,
        rollout_id=rollout_id,
        variant=_required_env("HARNESS_DISTILL_VARIANT"),
        campaign_id=os.environ.get(
            "HARNESS_DISTILL_CAMPAIGN_ID", "campaign2-step26-adaptive-buy-now-dagger-r2"
        ),
    )
    return create_adaptive_app(state)


__all__ = [
    "AdaptiveBuyNowState",
    "TRACE_SCHEMA",
    "app_from_environment",
    "buy_now_click",
    "create_adaptive_app",
]
