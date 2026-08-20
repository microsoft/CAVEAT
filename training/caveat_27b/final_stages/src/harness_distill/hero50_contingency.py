"""Narrow step-30 contingency policy for the laptop HERO50 bottleneck.

This module is deliberately policy-only.  It identifies three same-request
corrections from an on-policy TRAIN rollout and validates that the teacher's
replacement actually performs the missing semantic transition:

* open the visible HERO50 card;
* repair a rejected checkpoint with HERO50 in the submitted frontier; or
* rebind an approved HERO50 winner to its exact current PDP.

The executed successor remains the authority for accepting a pair.  No
product facts, browser states, or responses are synthesized here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

HERO_ID = "EXP-LAPTOP-50"
HERO_TITLE = "Exora Pulse M01 Laptop, 16GB RAM, 1024GB SSD"
HERO_TITLE_PREFIX = "Exora Pulse M01 Laptop"
HERO_BUCKETS = (
    "hero_frontier_discovery",
    "hero_checkpoint_repair",
    "hero_approved_rebind",
)
HERO_PROFILE = "hero_curriculum"
HERO_BRIDGE = "hero_curriculum_bridge"

_INDEXED_ANCHOR = re.compile(r"^\s*\*?\[(\d+)]<a(?:\s[^>]*)?\s*/>\s*$")


def _response_object(response: Mapping[str, Any]) -> Mapping[str, Any] | None:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        return None
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, str):
        return None
    text = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.I | re.S)
    if fenced:
        text = fenced.group(1)
    try:
        parsed, _end = json.JSONDecoder().raw_decode(text.lstrip())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def actions(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    parsed = _response_object(response)
    value = parsed.get("action") if parsed is not None else None
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def clicked_indices(response: Mapping[str, Any]) -> set[int]:
    result: set[int] = set()
    for action in actions(response):
        click = action.get("click")
        index = click.get("index") if isinstance(click, Mapping) else None
        if type(index) is int:
            result.add(index)
    return result


def _current_tab_url(browser_state: str) -> str:
    current = re.search(r"(?mi)^Current tab:\s*([^\s]+)\s*$", browser_state)
    if current:
        tab = re.escape(current.group(1))
        matches = re.findall(rf"(?mi)^Tab\s+{tab}:\s*(https?://\S+)", browser_state)
        return matches[0] if len(matches) == 1 else ""
    direct = re.search(r"(?mi)^Current URL:\s*(https?://\S+)", browser_state)
    return direct.group(1) if direct else ""


def navigates_to_exact_hero(
    response: Mapping[str, Any], *, expected_origin: str
) -> bool:
    """Accept only an explicit same-origin navigation to the exact HERO PDP."""

    expected = urlparse(expected_origin)
    if expected.scheme not in {"http", "https"} or not expected.netloc:
        return False
    for action in actions(response):
        navigate = action.get("navigate")
        target = navigate.get("url") if isinstance(navigate, Mapping) else None
        if not isinstance(target, str):
            continue
        parsed = urlparse(target)
        if (
            parsed.scheme.casefold() == expected.scheme.casefold()
            and parsed.netloc.casefold() == expected.netloc.casefold()
            and parsed.path.rstrip("/").upper() == f"/DP/{HERO_ID}"
            and not parsed.params
            and not parsed.query
            and not parsed.fragment
        ):
            return True
    return False


def visible_hero_link_indices(browser_state: str) -> tuple[int, ...]:
    """Return only anchors whose rendered label is the exact HERO50 title."""

    lines = browser_state.splitlines()
    found: list[int] = []
    for position, line in enumerate(lines):
        match = _INDEXED_ANCHOR.match(line)
        if not match:
            continue
        labels: list[str] = []
        for following in lines[position + 1 : position + 5]:
            if _INDEXED_ANCHOR.match(following) or re.match(
                r"^\s*\*?\[\d+]", following
            ):
                break
            label = re.sub(r"\s+", " ", following).strip()
            if label:
                labels.append(label)
        rendered = " ".join(labels)
        if rendered == HERO_TITLE or rendered.startswith(HERO_TITLE_PREFIX):
            found.append(int(match.group(1)))
    return tuple(dict.fromkeys(found))


def _checkpoint_payload(response: Mapping[str, Any]) -> Mapping[str, Any] | None:
    members = actions(response)
    if len(members) != 1 or set(members[0]) != {"decision_checkpoint"}:
        return None
    payload = members[0].get("decision_checkpoint")
    return payload if isinstance(payload, Mapping) else None


def checkpoint_submits_hero(response: Mapping[str, Any]) -> bool:
    payload = _checkpoint_payload(response)
    if payload is None or str(payload.get("proposed_candidate_id", "")).upper() != HERO_ID:
        return False
    candidates = payload.get("candidates")
    return isinstance(candidates, list) and any(
        isinstance(candidate, Mapping)
        and str(candidate.get("id", "")).upper() == HERO_ID
        for candidate in candidates
    )


def checkpoint_has_complete_coverage_shape(response: Mapping[str, Any]) -> bool:
    """Check only public structural invariants that make a repeat submission viable."""

    payload = _checkpoint_payload(response)
    frontier = payload.get("frontier") if payload is not None else None
    candidates = payload.get("candidates") if payload is not None else None
    if not isinstance(frontier, Mapping) or not isinstance(candidates, list):
        return False
    inspected = frontier.get("inspected_count")
    advertised = frontier.get("advertised_count")
    excluded = frontier.get("excluded_count")
    unresolved = frontier.get("unresolved_count")
    if (
        type(inspected) is not int
        or type(excluded) is not int
        or type(unresolved) is not int
        or min(inspected, excluded, unresolved) < 0
        or frontier.get("exhausted") is not True
        or unresolved != 0
        or inspected != len(candidates) + excluded + unresolved
    ):
        return False
    mode = frontier.get("coverage_mode")
    if mode == "advertised_total":
        return (
            type(advertised) is int
            and inspected == advertised
            and frontier.get("advertised_page_count") is None
            and frontier.get("enumerated_page_count") is None
        )
    if mode == "finite_pages":
        pages = frontier.get("advertised_page_count")
        enumerated = frontier.get("enumerated_page_count")
        return type(pages) is int and pages > 0 and enumerated == pages
    return False


def hero_trigger(
    *,
    target_bucket: str,
    browser_state: str,
    current_pdp_id: str | None,
    approved_id: str | None,
    latest_checkpoint_rejected: bool,
    hero_pdp_seen: bool,
    qwen_response: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Classify one high-specificity HERO50 failure, otherwise abstain."""

    if target_bucket not in HERO_BUCKETS:
        return None
    links = visible_hero_link_indices(browser_state)
    qwen_actions = actions(qwen_response)
    qwen_clicks = clicked_indices(qwen_response)
    clicked_hero = bool(qwen_clicks.intersection(links))
    before_url = _current_tab_url(browser_state)
    parsed_before = urlparse(before_url)
    before_origin = (
        f"{parsed_before.scheme}://{parsed_before.netloc}"
        if parsed_before.scheme in {"http", "https"} and parsed_before.netloc
        else ""
    )
    navigated_hero = navigates_to_exact_hero(
        qwen_response, expected_origin=before_origin
    )

    if target_bucket == "hero_frontier_discovery":
        if not hero_pdp_seen and links and qwen_actions and not clicked_hero:
            return {
                "training_bucket": target_bucket,
                "trigger_kind": "visible_hero50_not_opened",
                "target_id": HERO_ID,
                "hero_link_indices": list(links),
            }
        return None

    if target_bucket == "hero_checkpoint_repair":
        checkpoint = _checkpoint_payload(qwen_response)
        if (
            hero_pdp_seen
            and checkpoint is not None
            and not (
                checkpoint_submits_hero(qwen_response)
                and checkpoint_has_complete_coverage_shape(qwen_response)
            )
        ):
            return {
                "training_bucket": target_bucket,
                "trigger_kind": (
                    "post_rejection_checkpoint_coverage_repair"
                    if latest_checkpoint_rejected
                    else "structurally_rejected_checkpoint_coverage_repair"
                ),
                "target_id": HERO_ID,
                "hero_link_indices": list(links),
            }
        return None

    if (
        approved_id == HERO_ID
        and not latest_checkpoint_rejected
        and current_pdp_id != HERO_ID
        and (links or current_pdp_id is not None)
        and qwen_actions
        and not clicked_hero
        and not navigated_hero
    ):
        return {
            "training_bucket": target_bucket,
            "trigger_kind": "approved_hero50_not_current_pdp",
            "target_id": HERO_ID,
            "hero_link_indices": list(links),
            "exact_navigation_origin": before_origin,
        }
    return None


def validate_teacher_transition(
    *,
    trigger: Mapping[str, Any],
    qwen_response: Mapping[str, Any],
    teacher_response: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove that chosen and rejected branches differ on the target behavior."""

    bucket = trigger.get("training_bucket")
    links = {
        value
        for value in trigger.get("hero_link_indices", [])
        if type(value) is int
    }
    if bucket == "hero_frontier_discovery":
        rejected_hits = clicked_indices(qwen_response).intersection(links)
        chosen_hits = clicked_indices(teacher_response).intersection(links)
        valid = bool(links and not rejected_hits and chosen_hits)
        return {
            "valid": valid,
            "policy": "chosen_clicks_exact_visible_hero50_anchor",
            "hero_link_indices": sorted(links),
            "chosen_hero_clicks": sorted(chosen_hits),
            "rejected_hero_clicks": sorted(rejected_hits),
        }
    if bucket == "hero_approved_rebind":
        origin = str(trigger.get("exact_navigation_origin") or "")
        rejected_hits = clicked_indices(qwen_response).intersection(links)
        chosen_hits = clicked_indices(teacher_response).intersection(links)
        rejected_navigation = navigates_to_exact_hero(
            qwen_response, expected_origin=origin
        )
        chosen_navigation = navigates_to_exact_hero(
            teacher_response, expected_origin=origin
        )
        rejected_bind = bool(rejected_hits or rejected_navigation)
        chosen_bind = bool(chosen_hits or chosen_navigation)
        return {
            "valid": bool(not rejected_bind and chosen_bind),
            "policy": "chosen_binds_exact_approved_hero50_pdp",
            "hero_link_indices": sorted(links),
            "chosen_hero_clicks": sorted(chosen_hits),
            "rejected_hero_clicks": sorted(rejected_hits),
            "chosen_exact_hero_navigation": chosen_navigation,
            "rejected_exact_hero_navigation": rejected_navigation,
        }
    if bucket == "hero_checkpoint_repair":
        rejected_good = checkpoint_submits_hero(
            qwen_response
        ) and checkpoint_has_complete_coverage_shape(qwen_response)
        chosen_good = checkpoint_submits_hero(
            teacher_response
        ) and checkpoint_has_complete_coverage_shape(teacher_response)
        return {
            "valid": bool(not rejected_good and chosen_good),
            "policy": "chosen_checkpoint_submits_complete_exact_hero50_frontier",
            "chosen_submits_hero50": chosen_good,
            "rejected_submits_hero50": rejected_good,
        }
    return {"valid": False, "policy": "unknown_hero50_bucket"}


__all__ = [
    "HERO_BUCKETS",
    "HERO_BRIDGE",
    "HERO_ID",
    "HERO_PROFILE",
    "HERO_TITLE",
    "actions",
    "checkpoint_has_complete_coverage_shape",
    "checkpoint_submits_hero",
    "clicked_indices",
    "hero_trigger",
    "navigates_to_exact_hero",
    "validate_teacher_transition",
    "visible_hero_link_indices",
]
