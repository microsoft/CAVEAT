"""Live Qwen roll-in to Sol-low takeover without changing BrowserUse state.

The proxy is the policy switch.  BrowserUse, its message manager, browser,
storefront server, and database remain alive throughout an episode.  The
first ``horizon`` BrowserUse decisions are returned by Qwen.  The next one
(or the first checkout state) and every later decision are sampled from
``gpt-5.6-sol#low`` over TRAPI.  At teacher decisions Qwen is sampled only as
a counterfactual shadow; its response is never shown to Sol or executed.

Contract-compiler calls are deliberately not counted as BrowserUse decisions
and always remain on the student route.  No prompt, tool, response schema, or
browser observation is added by this module.
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

from .proxy import (
    SESSION_ID_ENV,
    ProxyState,
    _assistant_state_key,
    _default_session,
    _passthrough,
    _upstream_headers,
    canonical_qwen_response,
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

TRACE_SCHEMA = "harness-distill.interactive-sol-dagger-proxy-trace.v1"
HEALTH_SCHEMA = "harness-distill.interactive-sol-dagger-proxy-health.v1"
POLICY_SCHEMA = "harness-distill.interactive-sol-dagger-route-policy.v1"
SPLITS = ("train", "holdout")
VARIANTS = ("graded", "graded3", "graded4", "mixed")
TRAIN_HORIZONS = (2, 8, 16, 24)
HOLDOUT_HORIZON = 12


class InteractiveSolDaggerError(RuntimeError):
    """A route, request, response, or immutable policy violated its contract."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _json_clone(value: Any) -> Any:
    return json.loads(canonical_json(value))


def is_browseruse_request(request: Mapping[str, Any]) -> bool:
    """Recognize the structured BrowserUse agent call, excluding the compiler."""

    messages = request.get("messages")
    return isinstance(messages, list) and any(
        isinstance(message, Mapping)
        and message.get("role") == "system"
        and isinstance(message.get("content"), str)
        and message["content"].startswith(
            "You are an AI agent designed to operate in an iterative loop"
        )
        and "<user_request>" in message["content"]
        and "<agent_history>" in message["content"]
        and "<browser_state>" in message["content"]
        and "<json_schema>" in message["content"]
        and "agent_output" in message["content"]
        for message in messages
    )


def _latest_visible_text(request: Mapping[str, Any]) -> str:
    messages = request.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, Mapping) or message.get("role") not in {"user", "tool"}:
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if content is not None:
            return canonical_json(content)
    return ""


def checkout_guard(request: Mapping[str, Any]) -> bool:
    """Outcome-blind guard that switches before an irreversible order click."""

    visible = _latest_visible_text(request).casefold()
    return "/gp/buy/spc" in visible or "place your order" in visible


@dataclass(frozen=True, slots=True)
class RoutePolicy:
    rollout_id: str
    split: str
    variant: str
    horizon: int
    campaign_id: str = "qwen-step25-interactive-sol-dagger-r1"

    def __post_init__(self) -> None:
        if not self.rollout_id:
            raise ValueError("rollout_id must be non-empty")
        if self.split not in SPLITS:
            raise ValueError("route split must be train or holdout")
        if self.variant not in VARIANTS:
            raise ValueError("route variant is unsupported")
        expected = TRAIN_HORIZONS if self.split == "train" else (HOLDOUT_HORIZON,)
        if self.horizon not in expected:
            raise ValueError("route horizon is not valid for its split")
        if not self.campaign_id:
            raise ValueError("campaign_id must be non-empty")

    def as_dict(self) -> dict[str, Any]:
        body = {
            "schema": POLICY_SCHEMA,
            "campaign_id": self.campaign_id,
            "rollout_id": self.rollout_id,
            "split": self.split,
            "variant": self.variant,
            "horizon": self.horizon,
            "compiler_route": "qwen",
            "takeover": "sticky",
            "checkout_guard": True,
            "qwen_shadow_on_teacher_states": True,
        }
        return {**body, "policy_sha256": sha256_json(body)}


@dataclass
class InteractiveProxyState(ProxyState):
    policy: RoutePolicy | None = None
    teacher: SolLowTeacher | None = None
    browser_decisions: dict[str, int] = field(default_factory=dict)
    teacher_sessions: set[str] = field(default_factory=set)

    def route(self, session: str, request: Mapping[str, Any]) -> tuple[str, int, bool, bool]:
        if self.policy is None:
            raise InteractiveSolDaggerError("interactive proxy has no route policy")
        prior = self.browser_decisions.get(session, 0)
        if not is_browseruse_request(request):
            return "student", prior, False, False
        guarded = checkout_guard(request)
        already_teacher = session in self.teacher_sessions
        if already_teacher or prior >= self.policy.horizon or guarded:
            return "teacher", prior, guarded, not already_teacher
        return "student", prior, False, False


async def _qwen_call(
    state: InteractiveProxyState, outgoing: Mapping[str, Any]
) -> tuple[httpx.Response, dict[str, Any]]:
    upstream = await state.http().post(
        f"{state.upstream_base_url}/v1/chat/completions",
        headers=_upstream_headers(state),
        json=dict(outgoing),
    )
    try:
        body = upstream.json()
    except ValueError as exc:
        raise InteractiveSolDaggerError("Qwen upstream returned non-JSON") from exc
    if not isinstance(body, Mapping):
        raise InteractiveSolDaggerError("Qwen upstream returned a non-object")
    normalized = dict(body)
    if upstream.is_success:
        normalized = canonical_qwen_response(normalized)
    return upstream, normalized


def _remember_qwen_reasoning(
    state: InteractiveProxyState,
    session: str,
    outgoing: Mapping[str, Any],
    response: Mapping[str, Any],
) -> None:
    messages = outgoing.get("messages")
    if not isinstance(messages, list):
        return
    for choice in response.get("choices", []):
        if not isinstance(choice, Mapping) or not isinstance(choice.get("message"), Mapping):
            continue
        message = dict(choice["message"])
        reasoning = message.get("reasoning_content")
        if reasoning not in {None, ""}:
            history = [*messages, message]
            state.reasoning.setdefault(session, {})[
                _assistant_state_key(history, len(history) - 1)
            ] = reasoning


def _teacher_wire_response(
    raw_response: Mapping[str, Any], completion: Mapping[str, Any]
) -> dict[str, Any]:
    calls = completion.get("tool_calls")
    return {
        "id": raw_response.get("id") or "interactive-sol-" + sha256_json(completion)[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": raw_response.get("model") or "gpt-5.6-sol",
        "choices": [
            {
                "index": 0,
                "finish_reason": "tool_calls" if calls else "stop",
                "message": _json_clone(completion),
            }
        ],
        "usage": _json_clone(raw_response.get("usage")),
    }


def create_interactive_app(state: InteractiveProxyState) -> FastAPI:
    if state.policy is None:
        raise InteractiveSolDaggerError("route policy is required")
    app = FastAPI(title="harness-distill-interactive-sol-dagger-proxy")
    app.state.proxy = state

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "schema": HEALTH_SCHEMA,
            "policy": state.policy.as_dict(),
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
            route, prior_decisions, guarded, is_takeover = state.route(session, outgoing)
        except (ValueError, InteractiveSolDaggerError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        started = time.time()
        if route == "student":
            try:
                upstream, response_body = await _qwen_call(state, outgoing)
            except InteractiveSolDaggerError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            if upstream.is_success:
                _remember_qwen_reasoning(state, session, outgoing, response_body)
                if is_browseruse_request(outgoing):
                    # Commit only a decision that BrowserUse will actually
                    # receive.  Failed upstream calls are retryable at the
                    # same horizon position.
                    state.browser_decisions[session] = prior_decisions + 1
            await state.append_trace(
                {
                    "schema": TRACE_SCHEMA,
                    "timestamp": time.time(),
                    "elapsed_seconds": time.time() - started,
                    "session_sha256": hashlib.sha256(session.encode()).hexdigest(),
                    "role": x_harness_distill_role,
                    "route": "student",
                    "is_browseruse": is_browseruse_request(outgoing),
                    "student_decisions_before": prior_decisions,
                    "takeover": False,
                    "checkout_guard_triggered": False,
                    "policy": state.policy.as_dict(),
                    "request_sha256": sha256_json(body),
                    "effective_request_sha256": sha256_json(outgoing),
                    "request": _json_clone(body),
                    "effective_request": _json_clone(outgoing),
                    "response": response_body,
                    "response_sha256": sha256_json(response_body),
                    "status_code": upstream.status_code,
                }
            )
            return JSONResponse(response_body, status_code=upstream.status_code)

        teacher = state.teacher
        if teacher is None:
            teacher = AgentArenaSolLowTeacher()
            state.teacher = teacher
        try:
            descriptor = dict(teacher.descriptor())
            if descriptor != {
                "provider": "trapi",
                "model_spec": SOL_MODEL_SPEC,
                "logical_model": SOL_LOGICAL_MODEL,
                "wire_model": teacher.wire_model,
                "reasoning_effort": SOL_REASONING_EFFORT,
            }:
                raise InteractiveSolDaggerError("teacher is not exactly gpt-5.6-sol#low")
            teacher_request, invariance = make_sol_low_request(
                outgoing,
                wire_model=teacher.wire_model,
                max_completion_tokens=8192,
            )
            qwen_task = asyncio.create_task(_qwen_call(state, outgoing))
            sol_task = asyncio.create_task(teacher.complete(teacher_request))
            qwen_result, raw_teacher = await asyncio.gather(qwen_task, sol_task)
            qwen_upstream, qwen_response = qwen_result
            if not qwen_upstream.is_success:
                raise InteractiveSolDaggerError(
                    f"Qwen shadow failed with HTTP {qwen_upstream.status_code}"
                )
            if not isinstance(raw_teacher, Mapping):
                raise InteractiveSolDaggerError("Sol teacher response is not an object")
            choices = raw_teacher.get("choices")
            if not isinstance(choices, list) or len(choices) != 1:
                raise InteractiveSolDaggerError("Sol teacher response has no unique choice")
            message = choices[0].get("message") if isinstance(choices[0], Mapping) else None
            if not isinstance(message, Mapping):
                raise InteractiveSolDaggerError("Sol teacher response has no assistant message")
            completion = strip_provider_reasoning(message, baseline_request=outgoing)
            if any(
                isinstance(choice, Mapping)
                and isinstance(choice.get("message"), Mapping)
                and sha256_json(choice["message"])
                in {sha256_json(item) for item in teacher_request["messages"]}
                for choice in qwen_response.get("choices", [])
            ):
                raise InteractiveSolDaggerError("Qwen shadow completion entered Sol request")
            served = _teacher_wire_response(raw_teacher, completion)
        except Exception as exc:  # noqa: BLE001 - provider boundary is fail-closed
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(
                status_code=502, detail=f"interactive teacher failure: {exc}"
            ) from exc

        state_id = hashlib.sha256(
            f"{state.policy.rollout_id}:{sha256_json(outgoing)}:{prior_decisions}".encode()
        ).hexdigest()
        await state.append_trace(
            {
                "schema": TRACE_SCHEMA,
                "timestamp": time.time(),
                "elapsed_seconds": time.time() - started,
                "session_sha256": hashlib.sha256(session.encode()).hexdigest(),
                "role": x_harness_distill_role,
                "route": "teacher",
                "is_browseruse": True,
                "student_decisions_before": prior_decisions,
                "takeover": is_takeover,
                "checkout_guard_triggered": guarded,
                "state_id": state_id,
                "policy": state.policy.as_dict(),
                "request_sha256": sha256_json(body),
                "effective_request_sha256": sha256_json(outgoing),
                "request": _json_clone(body),
                "effective_request": _json_clone(outgoing),
                "qwen_shadow_response": qwen_response,
                "qwen_shadow_response_sha256": sha256_json(qwen_response),
                "teacher": descriptor,
                "teacher_request": _json_clone(teacher_request),
                "teacher_request_sha256": sha256_json(teacher_request),
                "teacher_raw_response_sha256": sha256_json(raw_teacher),
                "teacher_completion": completion,
                "teacher_completion_sha256": sha256_json(completion),
                "teacher_transport": _json_clone(raw_teacher.get("_harness_distill_transport")),
                "invariance_audit": invariance,
                "response": served,
                "response_sha256": sha256_json(served),
                "status_code": 200,
            }
        )
        # The takeover is transactional too: a provider/validation/trace
        # failure leaves the retry as the unique first teacher decision.
        state.teacher_sessions.add(session)
        return JSONResponse(served, status_code=200)

    return app


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def app_from_environment() -> FastAPI:
    policy = RoutePolicy(
        rollout_id=_required_env("HARNESS_DISTILL_ROLLOUT_ID"),
        split=_required_env("HARNESS_DISTILL_SPLIT"),
        variant=_required_env("HARNESS_DISTILL_VARIANT"),
        horizon=int(_required_env("HARNESS_DISTILL_HORIZON")),
        campaign_id=os.environ.get(
            "HARNESS_DISTILL_CAMPAIGN_ID", "qwen-step25-interactive-sol-dagger-r1"
        ),
    )
    trace = _required_env("HARNESS_DISTILL_TRACE_JSONL")
    state = InteractiveProxyState(
        upstream_base_url=_required_env("HARNESS_DISTILL_UPSTREAM_BASE_URL").rstrip("/"),
        upstream_api_key=os.environ.get("HARNESS_DISTILL_UPSTREAM_API_KEY", ""),
        trace_path=Path(trace),
        rollout_session_id=os.environ.get(SESSION_ID_ENV, "").strip() or policy.rollout_id,
        policy=policy,
    )
    return create_interactive_app(state)


__all__ = [
    "HEALTH_SCHEMA",
    "HOLDOUT_HORIZON",
    "InteractiveProxyState",
    "InteractiveSolDaggerError",
    "RoutePolicy",
    "TRACE_SCHEMA",
    "TRAIN_HORIZONS",
    "app_from_environment",
    "checkout_guard",
    "create_interactive_app",
    "is_browseruse_request",
]
