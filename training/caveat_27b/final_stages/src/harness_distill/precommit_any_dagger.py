"""Train-only same-state correction at unsafe precommit decisions.

Qwen rolls in on the unmodified BrowserUse request.  The proxy replaces the
first unsafe precommit response in each correction bucket with Sol-low on the
identical request, then records the immediate BrowserUse successor.  It also
corrects the first post-rejection checkpoint decision and winner/current-PDP
identity mismatch.  No prompt or browser state is synthesized.
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

from agentarena.scaffolds._deliberative_core import (
    Candidate,
    CandidateFact,
    Constraint,
    Frontier,
    Objective,
    TaskContract,
    evaluate_checkpoint,
)
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from .adaptive_buy_now_dagger import (
    _click_indices,
    _control_label,
    _latest_browser_state,
)
from .hero50_contingency import (
    HERO_BRIDGE,
    HERO_BUCKETS,
    HERO_ID,
    HERO_PROFILE,
    HERO_TITLE,
    hero_trigger,
    validate_teacher_transition,
)
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

TRACE_SCHEMA = "harness-distill.precommit-any-dagger-trace.v1"
HEALTH_SCHEMA = "harness-distill.precommit-any-dagger-health.v1"
BUCKETS = (
    "exploration_checkpoint",
    "winner_current_pdp_rebind",
    "shortcut_cart",
)
ALL_BUCKETS = (*BUCKETS, *HERO_BUCKETS, HERO_PROFILE)
EXPERT_POLICY = "live_state_rule_expert"
CHECKPOINT_EVIDENCE_TAG = "hero50_checkpoint_evidence"
CHECKPOINT_SOURCE_SCHEMA = "harness-distill.hero50-checkpoint-source.v1"
STAGE_RECEIPT_SCHEMA = "harness-distill.hero50-curriculum-stage-receipt.v2"
PRODUCT_ID = re.compile(r"EXP-LAPTOP-\d+", re.IGNORECASE)
APPROVAL = re.compile(
    r"Decision checkpoint approved candidate\s+(EXP-LAPTOP-\d+)\s+under contract\s+[0-9a-f]{64}",
    re.IGNORECASE,
)
REJECTION = re.compile(r"decision checkpoint rejected\s*:", re.IGNORECASE)


class PrecommitAnyError(RuntimeError):
    pass


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _request_text(request: Mapping[str, Any]) -> str:
    messages = request.get("messages")
    if not isinstance(messages, list):
        return ""
    return "\n".join(
        str(message.get("content"))
        for message in messages
        if isinstance(message, Mapping) and isinstance(message.get("content"), str)
    )


def current_tab_url(browser_state: str) -> str:
    """Resolve the actual current tab, never an arbitrary tab or empty path."""

    current = re.search(r"(?mi)^Current tab:\s*([^\s]+)\s*$", browser_state)
    if current:
        tab = re.escape(current.group(1))
        matches = re.findall(rf"(?mi)^Tab\s+{tab}:\s*(https?://\S+)", browser_state)
        return matches[0] if len(matches) == 1 else ""
    direct = re.search(r"(?mi)^Current URL:\s*(https?://\S+)", browser_state)
    if direct:
        return direct.group(1)
    tabs = re.findall(r"(?mi)^Tab\s+[^:]+:\s*(https?://\S+)", browser_state)
    return tabs[0] if len(tabs) == 1 else ""


def current_pdp_id(browser_state: str) -> str | None:
    path = urlparse(current_tab_url(browser_state)).path
    match = re.fullmatch(r"/dp/(EXP-LAPTOP-\d+)/?", path, re.IGNORECASE)
    return match.group(1).upper() if match else None


def authoritative_checkpoint(request: Mapping[str, Any]) -> tuple[str | None, bool, str | None]:
    """Return latest exact harness approval, and whether latest event rejected."""

    text = _request_text(request)
    events: list[tuple[int, str, str | None]] = [
        (match.start(), "approved", match.group(1).upper()) for match in APPROVAL.finditer(text)
    ]
    events.extend((match.start(), "rejected", None) for match in REJECTION.finditer(text))
    if not events:
        return None, False, None
    position, kind, candidate = max(events, key=lambda item: item[0])
    event_payload = f"{position}:{kind}:{candidate}:{text[position:]}".encode()
    event_id = hashlib.sha256(event_payload).hexdigest()
    return candidate, kind == "rejected", event_id


def _response_object(response: Mapping[str, Any]) -> Mapping[str, Any] | None:
    content = _response_content(response).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, re.IGNORECASE | re.DOTALL)
    if fenced:
        content = fenced.group(1)
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, Mapping) else None


def _title_key(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def observed_pdp_titles(browser_state: str) -> tuple[str, ...]:
    """Return conservative product-title aliases from an observed current PDP."""

    if current_pdp_id(browser_state) is None:
        return ()
    match = re.search(
        r"(?mi)^\s*Home\s*$\n\s*([^\n]{3,160}\bLaptop(?:\s*,[^\n]*)?)\s*$",
        browser_state,
    )
    if not match:
        return ()
    full = re.sub(r"\s+", " ", match.group(1)).strip()
    base = full.split(",", 1)[0].strip()
    short = re.sub(r"\s+(?:Gaming\s+)?Laptop$", "", base, flags=re.IGNORECASE).strip()
    return tuple(dict.fromkeys(value for value in (full, base, short) if value))


def winner_memory_id(
    response: Mapping[str, Any], title_bindings: Mapping[str, set[str]] | None = None
) -> str | None:
    """Resolve one winner from the current Qwen JSON memory, failing closed."""

    parsed = _response_object(response)
    memory = parsed.get("memory") if parsed is not None else None
    if not isinstance(memory, str):
        return None
    marker = (
        r"(?:winner|selected|chosen|final choice|clear best|"
        r"best qualifying(?: option| laptop)?)"
    )
    if not re.search(rf"\b{marker}\b", memory, re.IGNORECASE):
        return None
    identities = {match.group(0).upper() for match in PRODUCT_ID.finditer(memory)}
    if identities:
        return next(iter(identities)) if len(identities) == 1 else None
    candidates: set[str] = set()
    for title, product_ids in (title_bindings or {}).items():
        escaped = re.escape(title)
        near = bool(
            re.search(rf"\b{marker}\b[^.\n]{{0,80}}\b{escaped}\b", memory, re.IGNORECASE)
            or re.search(rf"\b{escaped}\b[^.\n]{{0,80}}\b{marker}\b", memory, re.IGNORECASE)
        )
        if near:
            candidates.update(product_ids)
    return next(iter(candidates)) if len(candidates) == 1 else None


def _response_content(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        return ""
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    return content if isinstance(content, str) else ""


def visible_commit_controls(
    response: Mapping[str, Any], request: Mapping[str, Any]
) -> list[dict[str, Any]]:
    state = _latest_browser_state(request)
    controls: list[dict[str, Any]] = []
    for index in sorted(_click_indices(response)):
        label = _control_label(state, index)
        if re.fullmatch(r"add\s+to\s+cart(?:\s*!+)?", label, re.IGNORECASE):
            controls.append({"index": index, "label": "Add to Cart"})
        elif re.fullmatch(r"buy\s+now(?:\s*!+)?", label, re.IGNORECASE):
            controls.append({"index": index, "label": "Buy Now"})
    return controls


def response_has_checkpoint(response: Mapping[str, Any]) -> bool:
    content = _response_content(response)
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return False
    actions = parsed.get("action") if isinstance(parsed, Mapping) else None
    return isinstance(actions, list) and any(
        isinstance(action, Mapping) and set(action) == {"decision_checkpoint"}
        for action in actions
    )


def _decision_checkpoint_payload(response: Mapping[str, Any]) -> dict[str, Any] | None:
    parsed = _response_object(response)
    actions = parsed.get("action") if parsed is not None else None
    if (
        not isinstance(actions, list)
        or len(actions) != 1
        or not isinstance(actions[0], Mapping)
        or set(actions[0]) != {"decision_checkpoint"}
        or not isinstance(actions[0].get("decision_checkpoint"), Mapping)
    ):
        return None
    return _clone(actions[0]["decision_checkpoint"])


def _literal_contract(text: str) -> TaskContract:
    marker = "Literal TaskContract:"
    matches: list[dict[str, Any]] = []
    for offset in (match.end() for match in re.finditer(re.escape(marker), text)):
        try:
            value, _end = json.JSONDecoder().raw_decode(text[offset:].lstrip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            matches.append(value)
    if len(matches) != 1:
        raise PrecommitAnyError("current request has no unique literal task contract")
    value = matches[0]
    return TaskContract(
        instruction=str(value["instruction"]),
        constraints=tuple(Constraint(**item) for item in value.get("constraints", [])),
        objectives=tuple(Objective(**item) for item in value.get("objectives", [])),
        search_mode=value.get("search_mode", "best_available"),
    )


def _checkpoint_semantic_assessment(
    outgoing: Mapping[str, Any], response: Mapping[str, Any]
) -> dict[str, Any]:
    """Mirror the real checkpoint evaluator and fail open on unseen DOM text.

    The curriculum receipt exposes one hash-bound exact rendered quote rather
    than the whole DOM.  A candidate basis absent from that quote may still be
    another real substring of the page, so that reason alone is deliberately
    not used as negative supervision.
    """

    payload = _decision_checkpoint_payload(response)
    if payload is None:
        return {
            "would_approve_hero": False,
            "provably_invalid": True,
            "reasons": ["decision_checkpoint was not called alone with a payload"],
        }
    try:
        expected = _checkpoint_from_live_evidence(outgoing)
        contract = _literal_contract(_request_text(outgoing))
        frontier_value = payload["frontier"]
        candidates_value = payload["candidates"]
        proposed = str(payload["proposed_candidate_id"])
        if not isinstance(frontier_value, Mapping) or not isinstance(
            candidates_value, list
        ):
            raise ValueError("checkpoint frontier/candidates shape is invalid")
        frontier = Frontier(**frontier_value)
        candidates = tuple(
            Candidate(
                candidate_id=str(item["id"]),
                label=str(item["label"]),
                source_url=str(item["source_url"]),
                facts=tuple(CandidateFact(**fact) for fact in item["facts"]),
            )
            for item in candidates_value
        )
        current_url = current_tab_url(_latest_browser_state(outgoing))
        current = urlparse(current_url)
        if current.scheme not in {"http", "https"} or not current.netloc:
            raise ValueError("current browser origin is invalid")
        result = evaluate_checkpoint(
            contract=contract,
            start_origin=f"{current.scheme}://{current.netloc}",
            current_url=current_url,
            rendered_page_text=str(expected["frontier"]["basis"]),
            frontier=frontier,
            candidates=candidates,
            proposed_candidate_id=proposed,
        )
    except Exception as exc:  # The live harness would reject malformed arguments too.
        return {
            "would_approve_hero": False,
            "provably_invalid": True,
            "reasons": [f"{type(exc).__name__}: {exc}"],
        }
    reasons = list(result.reasons)
    unobservable_basis_reason = (
        "frontier basis must be an exact quote from the current rendered page"
    )
    uncertain_only = bool(reasons) and set(reasons) == {unobservable_basis_reason}
    approved_hero = bool(
        result.approved
        and result.selected_candidate_id == HERO_ID
        and proposed == HERO_ID
    )
    return {
        "would_approve_hero": approved_hero,
        "provably_invalid": not approved_hero and not uncertain_only,
        "reasons": reasons,
        "selected_candidate_id": result.selected_candidate_id,
        "proposed_candidate_id": proposed,
    }


@dataclass(slots=True)
class PendingPair:
    state_id: str
    training_bucket: str
    trigger_kind: str
    request_sha256: str
    browser_state_sha256: str
    before_url: str
    target_id: str | None
    target_titles: tuple[str, ...]
    r_stage: int
    teacher_completion_sha256: str


@dataclass
class PrecommitAnyState(ProxyState):
    rollout_id: str = ""
    variant: str = ""
    campaign_id: str = "campaign2-step29-precommit-any-r2"
    target_bucket: str = "exploration_checkpoint"
    teacher_policy: str = "sol_low"
    teacher: SolLowTeacher | None = None
    pending: dict[str, PendingPair] = field(default_factory=dict)
    completed_buckets: dict[str, set[str]] = field(default_factory=dict)
    seen_rejections: dict[str, set[str]] = field(default_factory=dict)
    observed_title_ids: dict[str, dict[str, set[str]]] = field(default_factory=dict)
    r_retry_target: dict[str, str] = field(default_factory=dict)
    r_exhausted_targets: dict[str, set[str]] = field(default_factory=dict)
    seen_pdp_ids: dict[str, set[str]] = field(default_factory=dict)


def _remember_observed_pdp(
    state: PrecommitAnyState, session: str, request: Mapping[str, Any]
) -> None:
    browser = _latest_browser_state(request)
    product_id = current_pdp_id(browser)
    if product_id is None:
        return
    state.seen_pdp_ids.setdefault(session, set()).add(product_id)
    bindings = state.observed_title_ids.setdefault(session, {})
    for title in observed_pdp_titles(browser):
        bindings.setdefault(_title_key(title), set()).add(product_id)


def _successor_evidence(pending: PendingPair, request: Mapping[str, Any]) -> dict[str, Any]:
    after_state = _latest_browser_state(request)
    after_url = current_tab_url(after_state)
    after_path = urlparse(after_url).path if after_url else ""
    after_pdp = current_pdp_id(after_state)
    changed = sha256_json(request) != pending.request_sha256
    safe = bool(
        pending.before_url
        and after_url
        and changed
        and not after_path.startswith("/gp/buy")
        and "/gp/buy/thankyou" not in after_path
        and "order placed" not in after_state.casefold()
    )
    normalized_state = _title_key(after_state)
    list_page = after_path == "/s" or after_path.startswith("/s/")
    target_visible = bool(
        pending.target_id
        and pending.r_stage == 1
        and list_page
        and any(
            re.search(rf"(?<!\w){re.escape(title)}(?!\w)", normalized_state)
            for title in pending.target_titles
        )
    )
    rebound = (
        pending.target_id is None
        or after_pdp == pending.target_id
        or target_visible
    )
    approved_id, rejected, _event_id = authoritative_checkpoint(request)
    if pending.training_bucket in {
        "hero_frontier_discovery",
        "hero_approved_rebind",
    }:
        hero_transition_validated = after_pdp == HERO_ID
    elif pending.training_bucket == "hero_checkpoint_repair":
        hero_transition_validated = approved_id == HERO_ID and not rejected
    else:
        hero_transition_validated = None
    valid = safe and (
        hero_transition_validated
        if hero_transition_validated is not None
        else rebound
    )
    return {
        "state_id": pending.state_id,
        "training_bucket": pending.training_bucket,
        "trigger_kind": pending.trigger_kind,
        "successor_validated": valid,
        "request_changed": changed,
        "before_url": pending.before_url,
        "after_url": after_url,
        "before_path": urlparse(pending.before_url).path if pending.before_url else "",
        "after_path": after_path,
        "before_browser_state_sha256": pending.browser_state_sha256,
        "after_browser_state_sha256": hashlib.sha256(after_state.encode()).hexdigest(),
        "direct_checkout_avoided": bool(after_url and not after_path.startswith("/gp/buy")),
        "order_not_placed": bool(
            after_url
            and "/gp/buy/thankyou" not in after_path
            and "order placed" not in after_state.casefold()
        ),
        "target_id": pending.target_id,
        "after_pdp_id": after_pdp,
        "target_rebound": rebound,
        "target_visible_on_list": target_visible,
        "r_stage": pending.r_stage,
        "hero_transition_validated": hero_transition_validated,
        "approved_target_id": approved_id,
        "teacher_completion_sha256": pending.teacher_completion_sha256,
    }


async def _sol_completion(
    state: PrecommitAnyState, outgoing: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    teacher = state.teacher or AgentArenaSolLowTeacher()
    state.teacher = teacher
    descriptor = dict(teacher.descriptor())
    expected = {
        "provider": "trapi",
        "model_spec": SOL_MODEL_SPEC,
        "logical_model": SOL_LOGICAL_MODEL,
        "wire_model": teacher.wire_model,
        "reasoning_effort": SOL_REASONING_EFFORT,
    }
    if descriptor != expected:
        raise PrecommitAnyError("teacher is not exactly gpt-5.6-sol#low")
    teacher_request, invariance = make_sol_low_request(
        outgoing, wire_model=teacher.wire_model, max_completion_tokens=8192
    )
    raw = await teacher.complete(teacher_request)
    choices = raw.get("choices") if isinstance(raw, Mapping) else None
    message = (
        choices[0].get("message")
        if isinstance(choices, list)
        and len(choices) == 1
        and isinstance(choices[0], Mapping)
        else None
    )
    if not isinstance(message, Mapping):
        raise PrecommitAnyError("Sol teacher response has no unique assistant message")
    completion = strip_provider_reasoning(message, baseline_request=outgoing)
    served = _teacher_wire_response(raw, completion)
    if visible_commit_controls(served, outgoing):
        raise PrecommitAnyError("Sol repeated an unsafe precommit control")
    return completion, served, descriptor, {
        "request": teacher_request,
        "invariance": invariance,
        "raw": raw,
    }


def _rule_expert_completion(
    *,
    trigger: Mapping[str, Any],
    outgoing: Mapping[str, Any],
    qwen_response: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Bind one live-DOM HERO transition without fabricating an outcome."""

    bucket = trigger.get("training_bucket")
    if bucket not in {
        "hero_frontier_discovery",
        "hero_checkpoint_repair",
        "hero_approved_rebind",
    }:
        raise PrecommitAnyError("live-state expert has no policy for this trigger")
    content = _response_content(qwen_response).strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, re.I | re.S)
    if fenced:
        content = fenced.group(1)
    try:
        parsed, _end = json.JSONDecoder().raw_decode(content.lstrip())
    except json.JSONDecodeError as exc:
        raise PrecommitAnyError("candidate response cannot seed expert envelope") from exc
    if not isinstance(parsed, Mapping):
        raise PrecommitAnyError("candidate response is not a structured action object")
    payload = _clone(parsed)
    links = [value for value in trigger.get("hero_link_indices", []) if type(value) is int]
    if bucket == "hero_checkpoint_repair":
        checkpoint = _checkpoint_from_live_evidence(outgoing)
        action = [{"decision_checkpoint": checkpoint}]
        policy_action = "repair_checkpoint_from_bound_rejection_and_live_coverage"
    elif links:
        action = [{"click": {"index": min(links)}}]
        policy_action = "click_exact_live_hero_anchor"
    else:
        origin = str(trigger.get("exact_navigation_origin") or "").rstrip("/")
        parsed_origin = urlparse(origin)
        if (
            bucket != "hero_approved_rebind"
            or parsed_origin.scheme not in {"http", "https"}
            or not parsed_origin.netloc
        ):
            raise PrecommitAnyError("expert cannot bind HERO50 from this live state")
        action = [
            {
                "navigate": {
                    "url": f"{origin}/dp/{HERO_ID}",
                    "new_tab": False,
                }
            }
        ]
        policy_action = "navigate_same_origin_exact_hero_pdp"
    payload["action"] = action
    if isinstance(payload.get("thinking"), str):
        payload["thinking"] = (
            f"The live state and public rejection bind {HERO_ID}; perform the exact "
            f"{policy_action} transition now."
        )
    if isinstance(payload.get("memory"), str):
        payload["memory"] = (
            f"The selected/approved target is {HERO_ID} ({HERO_TITLE}); bind the "
            "current page to that exact identity before continuing."
        )
    if isinstance(payload.get("next_goal"), str):
        payload["next_goal"] = f"Open the exact {HERO_ID} product detail page."
    completion = {
        "role": "assistant",
        "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }
    raw = {
        "id": "hero50-live-state-rule-" + sha256_json(completion)[:24],
        "model": "harness-hero50-live-state-rule-expert",
        "usage": None,
        "_harness_distill_transport": {
            "provider": "local_harness_rule",
            "network_call": False,
            "policy_action": policy_action,
            "source": "live_train_dom_public_memory_and_action_schema",
        },
    }
    served = _teacher_wire_response(raw, completion)
    descriptor = {
        "provider": "local_harness_rule",
        "model_spec": "hero50-live-state-rule-expert",
        "logical_model": "hero50-live-state-rule-expert",
        "wire_model": "none",
        "reasoning_effort": "deterministic",
    }
    request = _clone(outgoing)
    invariance = {
        "policy": "same_exact_candidate_request_no_model_requery",
        "candidate_request_sha256": sha256_json(outgoing),
        "expert_request_sha256": sha256_json(request),
        "messages_unchanged": request.get("messages") == outgoing.get("messages"),
        "tools_unchanged": request.get("tools") == outgoing.get("tools"),
    }
    return completion, served, descriptor, {
        "request": request,
        "invariance": invariance,
        "raw": raw,
    }


def _checkpoint_from_live_evidence(outgoing: Mapping[str, Any]) -> dict[str, Any]:
    text = _request_text(outgoing)
    matches = re.findall(
        rf"<{CHECKPOINT_EVIDENCE_TAG}>\s*(.*?)\s*</{CHECKPOINT_EVIDENCE_TAG}>",
        text,
        re.DOTALL,
    )
    if len(matches) != 1:
        raise PrecommitAnyError("current request lacks unique checkpoint evidence")
    try:
        exposed = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise PrecommitAnyError("checkpoint evidence is not JSON") from exc
    source = exposed.get("checkpoint_source") if isinstance(exposed, Mapping) else None
    stage = exposed.get("live_stage") if isinstance(exposed, Mapping) else None
    if not isinstance(source, Mapping) or not isinstance(stage, Mapping):
        raise PrecommitAnyError("checkpoint evidence shape is invalid")
    source_body = {key: value for key, value in source.items() if key != "body_sha256"}
    stage_body = {key: value for key, value in stage.items() if key != "receipt_body_sha256"}
    submission = source.get("submission")
    rejection = source.get("rejection")
    coverage = stage.get("coverage")
    hero_visit = stage.get("hero_pdp_visit")
    if (
        source.get("schema") != CHECKPOINT_SOURCE_SCHEMA
        or source.get("body_sha256") != sha256_json(source_body)
        or source.get("submission_sha256") != sha256_json(submission)
        or source.get("rejection_sha256") != sha256_json(rejection)
        or stage.get("schema") != STAGE_RECEIPT_SCHEMA
        or stage.get("receipt_body_sha256") != sha256_json(stage_body)
        or stage.get("target_bucket") != "hero_checkpoint_repair"
        or stage.get("current_path") != "/s"
        or not isinstance(submission, Mapping)
        or not isinstance(rejection, Mapping)
        or not isinstance(coverage, Mapping)
        or not isinstance(hero_visit, Mapping)
        or hero_visit.get("current_path") != f"/dp/{HERO_ID}"
        or hero_visit.get("exact_identity_visible") is not True
        or rejection.get("approved") is not False
        or rejection.get("selected_candidate_id") != HERO_ID
        or HERO_ID not in rejection.get("tied_candidate_ids", [])
    ):
        raise PrecommitAnyError("checkpoint evidence failed its hashes or HERO binding")
    browser = _latest_browser_state(outgoing)
    current_url = current_tab_url(browser)
    current = urlparse(current_url)
    staged = urlparse(str(stage.get("current_url") or ""))
    visited = urlparse(str(hero_visit.get("current_url") or ""))
    quote = coverage.get("quote")
    advertised = coverage.get("advertised_count")
    normalized_browser = re.sub(r"\s+", " ", browser).strip()
    normalized_quote = re.sub(r"\s+", " ", quote).strip() if isinstance(quote, str) else ""
    if (
        current.scheme not in {"http", "https"}
        or current.path != "/s"
        or (current.scheme, current.netloc) != (staged.scheme, staged.netloc)
        or (current.scheme, current.netloc) != (visited.scheme, visited.netloc)
        or type(advertised) is not int
        or advertised < 1
        or not normalized_quote
        or not re.search(
            rf"\bof\s+{advertised}\s+results\s+for\b", normalized_browser
        )
        or not re.search(rf"\bof\s+{advertised}\s+results\s+for\b", normalized_quote)
    ):
        raise PrecommitAnyError("live coverage evidence does not match the current request")
    source_candidates = submission.get("candidates")
    frontier_ids = rejection.get("pareto_frontier_ids")
    if not isinstance(source_candidates, list) or not isinstance(frontier_ids, list):
        raise PrecommitAnyError("checkpoint source has no exact rejected frontier")
    by_id = {
        str(candidate.get("id", "")).upper(): _clone(candidate)
        for candidate in source_candidates
        if isinstance(candidate, Mapping)
    }
    frontier = [str(value).upper() for value in frontier_ids]
    if (
        not frontier
        or len(frontier) != len(set(frontier))
        or HERO_ID not in frontier
        or any(candidate_id not in by_id for candidate_id in frontier)
    ):
        raise PrecommitAnyError("rejected frontier cannot be repaired exactly")
    candidates = [by_id[candidate_id] for candidate_id in frontier]
    marker = "Literal TaskContract:"
    contract_matches: list[dict[str, Any]] = []
    for offset in (match.end() for match in re.finditer(re.escape(marker), text)):
        try:
            contract, _end = json.JSONDecoder().raw_decode(text[offset:].lstrip())
        except json.JSONDecodeError:
            continue
        if isinstance(contract, dict):
            contract_matches.append(contract)
    if len(contract_matches) != 1:
        raise PrecommitAnyError("current request has no unique literal task contract")
    contract_facts = [
        item
        for section in ("constraints", "objectives")
        for item in contract_matches[0].get(section, [])
        if isinstance(item, Mapping)
    ]

    def criterion_key(value: Any) -> str:
        key = re.sub(r"^(?:constraint|objective)_", "", str(value).casefold())
        return "laptop_type" if key in {
            "is_gaming_laptop",
            "gaming_laptop",
            "laptop_type",
        } else key

    contract_by_key = {
        criterion_key(item.get("criterion_id")): item for item in contract_facts
    }
    if len(contract_by_key) != len(contract_facts) or not contract_by_key:
        raise PrecommitAnyError("literal task contract criteria are ambiguous")
    origin = f"{current.scheme}://{current.netloc}"
    for candidate in candidates:
        candidate_id = str(candidate.get("id", "")).upper()
        source_url = urlparse(str(candidate.get("source_url") or ""))
        source_path = source_url.path.rstrip("/").upper()
        if source_path not in {f"/DP/{candidate_id}", f"/PRODUCT/{candidate_id}"}:
            raise PrecommitAnyError("checkpoint candidate source URL is not target-bound")
        candidate["id"] = candidate_id
        candidate["source_url"] = f"{origin}/dp/{candidate_id}"
        facts = candidate.get("facts")
        source_by_key = {
            criterion_key(fact.get("criterion_id")): _clone(fact)
            for fact in facts or []
            if isinstance(fact, Mapping)
        }
        if set(source_by_key) != set(contract_by_key):
            raise PrecommitAnyError("source facts do not exactly cover the current contract")
        repaired_facts: list[dict[str, Any]] = []
        for key, contract_fact in contract_by_key.items():
            fact = source_by_key[key]
            fact["criterion_id"] = contract_fact["criterion_id"]
            if key == "laptop_type":
                source_value = fact.get("value")
                if source_value is not False and str(source_value).casefold() not in {
                    "false",
                    "non-gaming",
                    "non gaming",
                }:
                    raise PrecommitAnyError("source does not prove non-gaming status")
                fact["value"] = (
                    "non-gaming"
                    if isinstance(contract_fact.get("expected"), str)
                    else False
                )
            repaired_facts.append(fact)
        candidate["facts"] = repaired_facts
    return {
        "frontier": {
            "inspected_count": advertised,
            "advertised_count": advertised,
            "coverage_mode": "advertised_total",
            "advertised_page_count": None,
            "enumerated_page_count": None,
            "excluded_count": advertised - len(candidates),
            "unresolved_count": 0,
            "exhausted": True,
            "basis": quote,
        },
        "candidates": candidates,
        "proposed_candidate_id": HERO_ID,
    }


def _trigger(
    state: PrecommitAnyState,
    session: str,
    outgoing: Mapping[str, Any],
    qwen_response: Mapping[str, Any],
) -> dict[str, Any] | None:
    browser = _latest_browser_state(outgoing)
    current_id = current_pdp_id(browser)
    approved_id, rejected, rejection_id = authoritative_checkpoint(outgoing)
    winner_id = winner_memory_id(qwen_response, state.observed_title_ids.get(session))
    retry_target = state.r_retry_target.get(session)
    target_id = retry_target or approved_id or winner_id
    completed = state.completed_buckets.setdefault(session, set())

    if state.target_bucket in (*HERO_BUCKETS, HERO_PROFILE):
        targets = (
            (
                "hero_approved_rebind",
                "hero_checkpoint_repair",
                "hero_frontier_discovery",
            )
            if state.target_bucket == HERO_PROFILE
            else (state.target_bucket,)
        )
        for target in targets:
            if target in completed:
                continue
            if (
                target == "hero_checkpoint_repair"
                and state.teacher_policy == EXPERT_POLICY
                and rejected
            ):
                assessment = _checkpoint_semantic_assessment(outgoing, qwen_response)
                if assessment["would_approve_hero"] or not assessment["provably_invalid"]:
                    continue
                return {
                    "training_bucket": target,
                    "trigger_kind": "post_rejection_live_coverage_checkpoint_repair",
                    "target_id": HERO_ID,
                    "hero_link_indices": [],
                    "current_pdp_id": current_id,
                    "approved_id": approved_id,
                    "winner_id": winner_id,
                    "controls": visible_commit_controls(qwen_response, outgoing),
                    "checkpoint_semantic_assessment": assessment,
                }
            trigger = hero_trigger(
                target_bucket=target,
                browser_state=browser,
                current_pdp_id=current_id,
                approved_id=approved_id,
                latest_checkpoint_rejected=rejected,
                hero_pdp_seen=HERO_ID in state.seen_pdp_ids.get(session, set()),
                qwen_response=qwen_response,
            )
            if trigger is not None:
                return {
                    **trigger,
                    "current_pdp_id": current_id,
                    "approved_id": approved_id,
                    "winner_id": winner_id,
                    "controls": visible_commit_controls(qwen_response, outgoing),
                }
        controls = visible_commit_controls(qwen_response, outgoing)
        if (
            state.target_bucket == HERO_PROFILE
            and HERO_ID in state.seen_pdp_ids.get(session, set())
            and approved_id != HERO_ID
            and HERO_BRIDGE not in completed
            and controls
        ):
            return {
                "training_bucket": HERO_BRIDGE,
                "trigger_kind": "curriculum_bridge_blocks_precheckpoint_commit",
                "current_pdp_id": current_id,
                "approved_id": approved_id,
                "winner_id": winner_id,
                "target_id": None,
                "controls": controls,
            }
        return None

    seen = state.seen_rejections.setdefault(session, set())
    if (
        rejected
        and rejection_id
        and rejection_id not in seen
        and "exploration_checkpoint" not in completed
    ):
        seen.add(rejection_id)
        return {
            "training_bucket": "exploration_checkpoint",
            "trigger_kind": "post_rejected_decision_checkpoint",
            "current_pdp_id": current_id,
            "approved_id": approved_id,
            "winner_id": winner_id,
            "target_id": None,
            "controls": visible_commit_controls(qwen_response, outgoing),
        }

    exhausted = state.r_exhausted_targets.setdefault(session, set())
    if (
        target_id
        and current_id != target_id
        and "winner_current_pdp_rebind" not in completed
        and target_id not in exhausted
    ):
        r_stage = 2 if retry_target == target_id else 1
        return {
            "training_bucket": "winner_current_pdp_rebind",
            "trigger_kind": (
                "approved_or_winner_current_pdp_mismatch"
                if r_stage == 1
                else "bounded_rebind_retry"
            ),
            "current_pdp_id": current_id,
            "approved_id": approved_id,
            "winner_id": winner_id,
            "target_id": target_id,
            "r_stage": r_stage,
            "controls": visible_commit_controls(qwen_response, outgoing),
        }

    controls = visible_commit_controls(qwen_response, outgoing)
    if not controls or (approved_id and current_id == approved_id):
        return None
    has_buy_now = any(control["label"] == "Buy Now" for control in controls)
    bucket = (
        "shortcut_cart"
        if state.target_bucket == "shortcut_cart" and has_buy_now
        else "exploration_checkpoint"
    )
    return {
        "training_bucket": bucket,
        "trigger_kind": (
            "visible_unapproved_buy_now_click"
            if has_buy_now
            else "visible_unapproved_add_to_cart_click"
        ),
        "current_pdp_id": current_id,
        "approved_id": approved_id,
        "winner_id": winner_id,
        "target_id": None,
        "controls": controls,
    }


def create_precommit_any_app(state: PrecommitAnyState) -> FastAPI:
    if not state.rollout_id or not state.variant or state.target_bucket not in ALL_BUCKETS:
        raise PrecommitAnyError("rollout, variant, and target bucket are required")
    app = FastAPI(title="harness-distill-precommit-any-dagger")
    app.state.proxy = state

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "schema": HEALTH_SCHEMA,
            "campaign": state.campaign_id,
            "rollout_id": state.rollout_id,
            "variant": state.variant,
            "target_bucket": state.target_bucket,
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
            x_harness_distill_session
            or state.rollout_session_id
            or _default_session(messages)
        )
        session_sha256 = hashlib.sha256(session.encode("utf-8")).hexdigest()
        try:
            outgoing = qwen_request(body, state.reasoning.setdefault(session, {}))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        is_browser = is_browseruse_request(outgoing)
        if is_browser:
            _remember_observed_pdp(state, session, outgoing)
        if is_browser and session in state.pending:
            pending = state.pending.pop(session)
            successor = _successor_evidence(pending, outgoing)
            await state.append_trace(
                {
                    "schema": TRACE_SCHEMA,
                    "timestamp": time.time(),
                    "session_sha256": session_sha256,
                    "role": x_harness_distill_role,
                    "route": "successor",
                    "is_browseruse": True,
                    "request_sha256": sha256_json(body),
                    "effective_request": _clone(outgoing),
                    "effective_request_sha256": sha256_json(outgoing),
                    **successor,
                }
            )
            if successor["successor_validated"]:
                state.completed_buckets.setdefault(session, set()).add(
                    pending.training_bucket
                )
                if pending.training_bucket == "winner_current_pdp_rebind":
                    state.r_retry_target.pop(session, None)
            elif (
                pending.training_bucket == "winner_current_pdp_rebind"
                and pending.target_id
            ):
                if pending.r_stage == 1:
                    state.r_retry_target[session] = pending.target_id
                else:
                    state.r_retry_target.pop(session, None)
                    state.r_exhausted_targets.setdefault(session, set()).add(
                        pending.target_id
                    )

        started = time.time()
        try:
            qwen_upstream, qwen_response = await _qwen_call(state, outgoing)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"candidate failure: {exc}") from exc
        if not qwen_upstream.is_success:
            return JSONResponse(qwen_response, status_code=qwen_upstream.status_code)
        _remember_qwen_reasoning(state, session, outgoing, qwen_response)
        trigger = _trigger(state, session, outgoing, qwen_response) if is_browser else None
        if trigger is None:
            await state.append_trace(
                {
                    "schema": TRACE_SCHEMA,
                    "timestamp": time.time(),
                    "elapsed_seconds": time.time() - started,
                    "session_sha256": session_sha256,
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

        try:
            if state.teacher_policy == EXPERT_POLICY and trigger["training_bucket"] in {
                "hero_frontier_discovery",
                "hero_checkpoint_repair",
                "hero_approved_rebind",
            }:
                completion, served, descriptor, teacher = _rule_expert_completion(
                    trigger=trigger,
                    outgoing=outgoing,
                    qwen_response=qwen_response,
                )
            else:
                completion, served, descriptor, teacher = await _sol_completion(
                    state, outgoing
                )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=502, detail=f"precommit teacher failure: {exc}"
            ) from exc
        hero_teacher_validation = None
        if trigger["training_bucket"] in HERO_BUCKETS:
            hero_teacher_validation = validate_teacher_transition(
                trigger=trigger,
                qwen_response=qwen_response,
                teacher_response=served,
            )
            if (
                trigger["training_bucket"] == "hero_checkpoint_repair"
                and state.teacher_policy == EXPERT_POLICY
            ):
                rejected_assessment = _checkpoint_semantic_assessment(
                    outgoing, qwen_response
                )
                chosen_assessment = _checkpoint_semantic_assessment(outgoing, served)
                hero_teacher_validation = {
                    "valid": bool(
                        rejected_assessment["provably_invalid"]
                        and chosen_assessment["would_approve_hero"]
                    ),
                    "policy": "real_checkpoint_semantic_approval_repair",
                    "rejected_assessment": rejected_assessment,
                    "chosen_assessment": chosen_assessment,
                }
            if hero_teacher_validation["valid"] is not True:
                await state.append_trace(
                    {
                        "schema": TRACE_SCHEMA,
                        "timestamp": time.time(),
                        "elapsed_seconds": time.time() - started,
                        "session_sha256": session_sha256,
                        "role": x_harness_distill_role,
                        "route": "teacher_abstention",
                        "is_browseruse": True,
                        "semantic_trigger": True,
                        "training_bucket": trigger["training_bucket"],
                        "trigger_kind": trigger["trigger_kind"],
                        "request_sha256": sha256_json(body),
                        "effective_request_sha256": sha256_json(outgoing),
                        "qwen_rejected_response_sha256": sha256_json(qwen_response),
                        "teacher_completion_sha256": sha256_json(completion),
                        "hero_teacher_validation": hero_teacher_validation,
                        "status_code": 200,
                    }
                )
                return JSONResponse(qwen_response, status_code=200)
        browser = _latest_browser_state(outgoing)
        before_url = current_tab_url(browser)
        if not before_url:
            raise HTTPException(status_code=502, detail="current browser tab URL is unresolved")
        state_id = hashlib.sha256(
            f"{state.rollout_id}:{trigger['training_bucket']}:{sha256_json(outgoing)}".encode()
        ).hexdigest()
        pending = PendingPair(
            state_id=state_id,
            training_bucket=trigger["training_bucket"],
            trigger_kind=trigger["trigger_kind"],
            request_sha256=sha256_json(outgoing),
            browser_state_sha256=hashlib.sha256(browser.encode()).hexdigest(),
            before_url=before_url,
            target_id=trigger["target_id"],
            target_titles=tuple(
                (HERO_TITLE.casefold(),)
                if trigger["training_bucket"] in HERO_BUCKETS
                else sorted(
                    title
                    for title, identities in state.observed_title_ids.get(
                        session, {}
                    ).items()
                    if trigger["target_id"] and identities == {trigger["target_id"]}
                )
            ),
            r_stage=int(trigger.get("r_stage", 1)),
            teacher_completion_sha256=sha256_json(completion),
        )
        await state.append_trace(
            {
                "schema": TRACE_SCHEMA,
                "timestamp": time.time(),
                "elapsed_seconds": time.time() - started,
                "session_sha256": session_sha256,
                "role": x_harness_distill_role,
                "route": "teacher_intervention",
                "is_browseruse": True,
                "semantic_trigger": True,
                "state_id": state_id,
                "request_sha256": sha256_json(body),
                **trigger,
                "effective_request": _clone(outgoing),
                "effective_request_sha256": sha256_json(outgoing),
                "qwen_rejected_response": qwen_response,
                "qwen_rejected_response_sha256": sha256_json(qwen_response),
                "qwen_proposed_decision_checkpoint": response_has_checkpoint(qwen_response),
                "teacher": descriptor,
                "teacher_request": _clone(teacher["request"]),
                "teacher_request_sha256": sha256_json(teacher["request"]),
                "teacher_completion": completion,
                "teacher_completion_sha256": sha256_json(completion),
                "hero_teacher_validation": hero_teacher_validation,
                "teacher_transport": _clone(teacher["raw"].get("_harness_distill_transport")),
                "invariance_audit": teacher["invariance"],
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
    state = PrecommitAnyState(
        upstream_base_url=_required_env("HARNESS_DISTILL_UPSTREAM_BASE_URL").rstrip("/"),
        upstream_api_key=os.environ.get("HARNESS_DISTILL_UPSTREAM_API_KEY", ""),
        trace_path=Path(_required_env("HARNESS_DISTILL_TRACE_JSONL")),
        rollout_session_id=os.environ.get(SESSION_ID_ENV, "").strip() or rollout_id,
        rollout_id=rollout_id,
        variant=_required_env("HARNESS_DISTILL_VARIANT"),
        campaign_id=os.environ.get(
            "HARNESS_DISTILL_CAMPAIGN_ID", "campaign2-step29-precommit-any-r2"
        ),
        target_bucket=_required_env("HARNESS_DISTILL_TARGET_BUCKET"),
        teacher_policy=os.environ.get("HARNESS_DISTILL_TEACHER_POLICY", "sol_low"),
    )
    if state.teacher_policy not in {"sol_low", EXPERT_POLICY}:
        raise RuntimeError("HARNESS_DISTILL_TEACHER_POLICY is invalid")
    return create_precommit_any_app(state)


__all__ = [
    "ALL_BUCKETS",
    "BUCKETS",
    "PrecommitAnyState",
    "authoritative_checkpoint",
    "create_precommit_any_app",
    "current_pdp_id",
    "current_tab_url",
    "visible_commit_controls",
    "winner_memory_id",
]
