"""Executed-transition validation and exact campaign-2 data materialization.

Only public model requests, the action actually returned to BrowserUse, its
real successor request, trajectory action records, HTTP logs, and final SQLite
transaction state are used.  Evaluator/reward fields are never read.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .interactive_sol_dagger import TRACE_SCHEMA, VARIANTS
from .interactive_sol_dagger_ops import audit_bundle, canonical_bytes, sha256_file
from .sol_dagger_collection import (
    SOL_LOGICAL_MODEL,
    SOL_MODEL_SPEC,
    SOL_REASONING_EFFORT,
    _assert_unmodified_harness,
)
from .sol_dagger_collection import sha256_json as collection_sha256_json

TRANSITION_SCHEMA = "harness-distill.c2-executed-transition.v1"
EXECUTION_AUDIT_SCHEMA = "harness-distill.c2-execution-audit.v1"
VALIDATION_SCHEMA = "harness-distill.c2-execution-validation.v1"
COLLECTION_SCHEMA = "harness-distill.c2-action-collection.v1"
RETENTION_SCHEMA = "harness-distill.c2-retention.v1"
HELDOUT_PREFLIGHT_SCHEMA = "harness-distill.c2-heldout-action-preflight.v1"

PHASES = (
    "frontier_exploration",
    "checkpoint_grounding",
    "approved_cart_entry",
    "dirty_cart_cleanup",
    "clean_checkout_order",
)
PHASE_QUOTAS = {
    "frontier_exploration": 16,
    "checkpoint_grounding": 16,
    "approved_cart_entry": 16,
    "dirty_cart_cleanup": 16,
    "clean_checkout_order": 8,
}
PHASE_SUBTYPE_QUOTAS = {
    "approved_cart_entry/add_to_cart": 8,
    "approved_cart_entry/open_cart": 8,
    "clean_checkout_order/cart_to_checkout": 4,
    "clean_checkout_order/place_order": 4,
}
RETENTION_PHASE = "generic_retention"
RETENTION_ROWS = 24
RETENTION_PER_VARIANT = 6
RETENTION_SOURCE_PHASES = (
    "constraints_query",
    "pagination_exploration",
    "pdp_evidence_selection",
)
EXPECTED_HERO_ID = "EXP-LAPTOP-50"
LIVE_COLLECTOR_COMMIT = "3a018046f11b3b0b86fec2b0029466d0b5f9fad2"
HELDOUT_TARGET_QUOTAS = {
    "frontier_exploration": 6,
    "checkpoint_grounding": 5,
    "approved_cart_entry": 5,
    "dirty_cart_cleanup": 5,
    "clean_checkout_order": 3,
}

_URL = re.compile(r"Current URL:\s*([^\n]+)")
_INDEX = re.compile(r"\[(\d+)\]")
_HTTP = re.compile(r'"(GET|POST|PUT|PATCH|DELETE) ([^ ]+) HTTP/1\.1" (\d{3})')


class InteractiveDataError(RuntimeError):
    """Trace execution, successor proof, or deterministic selection failed."""


class EpisodeAttritionError(InteractiveDataError):
    """A rollout did not yield a complete alignable teacher suffix."""


class AmbiguousAlignmentError(EpisodeAttritionError):
    """Multiple trace subsequences could explain the executed trajectory."""


class _RetentionExcluded(InteractiveDataError):
    """A valid step25 row is deliberately outside the retention stratum."""


def canonical_json(value: Any) -> str:
    return canonical_bytes(value).decode()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise InteractiveDataError(f"unsafe or absent JSON: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise InteractiveDataError(f"JSON must be an object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise InteractiveDataError(f"unsafe or absent JSONL: {path}")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise InteractiveDataError(f"JSONL row {number} is not an object: {path}")
        rows.append(value)
    if not rows:
        raise InteractiveDataError(f"JSONL is empty: {path}")
    return rows


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError as exc:
        raise InteractiveDataError(f"create-only target exists: {path}") from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    payload = b"".join(canonical_bytes(row) + b"\n" for row in rows)
    _write_new(path, payload)
    return {
        "relative_path": path.name,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "rows": len(rows),
    }


def _write_manifest(path: Path, body: Mapping[str, Any], hash_name: str) -> dict[str, Any]:
    value = {**body, hash_name: hashlib.sha256(canonical_bytes(body)).hexdigest()}
    _write_new(path, canonical_bytes(value) + b"\n")
    return value


def _execution_provenance(
    *,
    collector_commit: str,
    validator_commit: str,
    validator_data_sha256: str,
    validator_cli_sha256: str,
) -> dict[str, Any]:
    if collector_commit != LIVE_COLLECTOR_COMMIT or not re.fullmatch(
        r"[0-9a-f]{40}", validator_commit
    ):
        raise InteractiveDataError("collector/validator commit provenance is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", validator_data_sha256) or not re.fullmatch(
        r"[0-9a-f]{64}", validator_cli_sha256
    ):
        raise InteractiveDataError("validator source SHA provenance is invalid")
    data_path = Path(__file__).resolve()
    project_root = data_path.parents[2]
    cli_path = project_root / "scripts" / "prepare_interactive_sol_dagger.py"
    actual_data_sha256 = sha256_file(data_path)
    actual_cli_sha256 = sha256_file(cli_path)
    if validator_data_sha256 != actual_data_sha256 or validator_cli_sha256 != actual_cli_sha256:
        raise InteractiveDataError("executing validator/CLI bytes do not match explicit provenance")
    committed_paths = {
        "validator_data_sha256": "src/harness_distill/interactive_sol_dagger_data.py",
        "validator_cli_sha256": "scripts/prepare_interactive_sol_dagger.py",
    }
    expected = {
        "validator_data_sha256": validator_data_sha256,
        "validator_cli_sha256": validator_cli_sha256,
    }
    for key, relative_path in committed_paths.items():
        result = subprocess.run(
            ["git", "show", f"{validator_commit}:{relative_path}"],
            cwd=project_root,
            capture_output=True,
            check=False,
            timeout=60,
        )
        if result.returncode != 0 or hashlib.sha256(result.stdout).hexdigest() != expected[key]:
            raise InteractiveDataError(f"validator commit does not bind executing {relative_path}")
    return {
        "live_collector_commit": collector_commit,
        "validator_materializer_commit": validator_commit,
        "validator_data_path": committed_paths["validator_data_sha256"],
        "validator_data_sha256": validator_data_sha256,
        "validator_cli_path": committed_paths["validator_cli_sha256"],
        "validator_cli_sha256": validator_cli_sha256,
    }


def _audit_provenance_record(value: Any) -> dict[str, Any]:
    expected_keys = {
        "live_collector_commit",
        "validator_materializer_commit",
        "validator_data_path",
        "validator_data_sha256",
        "validator_cli_path",
        "validator_cli_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != expected_keys:
        raise InteractiveDataError("validation provenance schema changed")
    if (
        value.get("live_collector_commit") != LIVE_COLLECTOR_COMMIT
        or not re.fullmatch(r"[0-9a-f]{40}", str(value.get("validator_materializer_commit")))
        or value.get("validator_data_path") != "src/harness_distill/interactive_sol_dagger_data.py"
        or value.get("validator_cli_path") != "scripts/prepare_interactive_sol_dagger.py"
        or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("validator_data_sha256")))
        or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("validator_cli_sha256")))
    ):
        raise InteractiveDataError("validation provenance is malformed")
    return dict(value)


def _response_message(response: Mapping[str, Any], label: str) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise InteractiveDataError(f"{label} has no unique choice")
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, Mapping) else None
    if not isinstance(message, Mapping):
        raise InteractiveDataError(f"{label} has no assistant message")
    return json.loads(canonical_json(message))


def _assistant_actions(
    message: Mapping[str, Any], *, strict_training_target: bool = True
) -> list[dict[str, Any]]:
    content = message.get("content")
    if message.get("role") != "assistant" or not isinstance(content, str) or not content:
        raise InteractiveDataError("teacher completion has no BrowserUse JSON content")
    if strict_training_target and (
        content != content.strip()
        or message.get("tool_calls") not in (None, [])
        or message.get("reasoning_content") not in (None, "")
    ):
        raise InteractiveDataError("teacher completion leaks non-target assistant fields")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise InteractiveDataError("teacher completion is not JSON") from exc
    if (
        not isinstance(payload, Mapping)
        or not payload
        or (strict_training_target and next(reversed(payload)) != "action")
    ):
        raise InteractiveDataError("teacher completion is not action-final JSON")
    actions = payload.get("action")
    if not isinstance(actions, list) or not actions:
        raise InteractiveDataError("teacher completion has no action list")
    if any(not isinstance(action, Mapping) or not action for action in actions):
        raise InteractiveDataError("teacher action list is malformed")
    return [dict(action) for action in actions]


def _action_core(action: Mapping[str, Any]) -> tuple[str, Any]:
    keys = [key for key in action if key != "interacted_element"]
    if len(keys) != 1:
        raise InteractiveDataError("BrowserUse action has ambiguous names")
    name = keys[0]
    return name, action[name]


def _action_signature(actions: Sequence[Mapping[str, Any]]) -> list[tuple[str, Any]]:
    return [_action_core(action) for action in actions]


def _trajectory_actions(step: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = step.get("action")
    if not isinstance(raw, str):
        raise InteractiveDataError("trajectory step lacks serialized action")
    value = json.loads(raw)
    if not isinstance(value, list):
        raise InteractiveDataError("trajectory step action is not a list")
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _visible(request: Mapping[str, Any]) -> str:
    messages = request.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, Mapping) and message.get("role") in {"user", "tool"}:
            content = message.get("content")
            if isinstance(content, str):
                return content
    return ""


def _url(visible: str) -> str:
    matches = _URL.findall(visible)
    if matches:
        return matches[-1].strip()
    tab = re.findall(r"Tab [^:]+:\s*(http://[^\s]+)", visible)
    return tab[-1].strip() if tab else ""


def _browser_state(visible: str) -> str:
    match = re.search(r"<browser_state>(.*?)</browser_state>", visible, flags=re.DOTALL)
    return match.group(1) if match else visible


def _approval_visible(visible: str, candidate_id: str) -> bool:
    folded = visible.casefold()
    candidate = candidate_id.casefold()
    return (
        f"decision checkpoint approved candidate {candidate}" in folded
        or f"decision checkpoint approved {candidate}" in folded
    )


def _index_window(visible: str, index: int) -> str:
    marker = f"[{index}]"
    position = visible.find(marker)
    if position < 0:
        return ""
    window = visible[position : position + 400]
    next_index = _INDEX.search(window, len(marker))
    return window[: next_index.start()] if next_index else window


def _index_context(visible: str, index: int) -> str:
    marker = f"[{index}]"
    position = visible.find(marker)
    if position < 0:
        return ""
    return visible[max(0, position - 800) : position + 400]


def _clicked_windows(actions: Sequence[Mapping[str, Any]], visible: str) -> list[str]:
    visible = _browser_state(visible)
    windows: list[str] = []
    for action in actions:
        name, payload = _action_core(action)
        if name not in {"click", "input", "select_dropdown"} or not isinstance(payload, Mapping):
            continue
        index = payload.get("index")
        if type(index) is int:
            windows.append(_index_window(visible, index))
    return windows


def _clicked_context(actions: Sequence[Mapping[str, Any]], visible: str) -> str:
    visible = _browser_state(visible)
    contexts: list[str] = []
    for action in actions:
        name, payload = _action_core(action)
        if name != "click" or not isinstance(payload, Mapping):
            continue
        index = payload.get("index")
        if type(index) is int:
            contexts.append(_index_context(visible, index))
    return "\n".join(contexts)


def _unwanted_cart_item(
    clicked_context: str,
    *,
    clicked_index: int,
    approved_candidate: str,
    approved_title: str,
) -> tuple[str | None, str]:
    folded = clicked_context.casefold()
    marker_position = folded.find(f"[{clicked_index}]")
    if marker_position < 0:
        return None, ""
    candidates: list[tuple[int, str, str]] = []
    for match in re.finditer(r"ADDON-[A-Z0-9-]+", clicked_context, flags=re.IGNORECASE):
        candidates.append((match.start(), "addon", match.group(0).casefold()))
    for match in re.finditer(r"[^\n]*(?:protection plan|addon-plan)[^\n]*", folded):
        if "addon-" not in match.group(0):
            candidates.append((match.start(), "addon", "protection plan"))
    for match in re.finditer(r"EXP-LAPTOP-\d+", clicked_context, flags=re.IGNORECASE):
        identity = match.group(0).casefold()
        if identity != approved_candidate.casefold():
            candidates.append((match.start(), "stale_extra", identity))
    approved_markers = {approved_candidate.casefold(), approved_title.casefold()}
    offset = 0
    for line in clicked_context.splitlines(keepends=True):
        line_folded = line.casefold()
        if (
            "laptop" in line_folded
            and "user_request" not in line_folded
            and not any(marker and marker in line_folded for marker in approved_markers)
            and not re.search(r"EXP-LAPTOP-\d+", line, flags=re.IGNORECASE)
        ):
            identity = re.sub(r"^\s*\[\d+\][^>]*>\s*", "", line_folded).strip()
            if identity:
                candidates.append((offset, "stale_extra", identity))
        offset += len(line)
    if candidates:
        preceding = [candidate for candidate in candidates if candidate[0] < marker_position]
        chosen = (
            max(preceding, key=lambda item: item[0])
            if preceding
            else min(candidates, key=lambda item: abs(item[0] - marker_position))
        )
        return chosen[1], chosen[2]
    return None, ""


def _grounded(actions: Sequence[Mapping[str, Any]], visible: str) -> bool:
    visible = _browser_state(visible)
    for action in actions:
        name, payload = _action_core(action)
        if name not in {"click", "input", "select_dropdown"}:
            continue
        if not isinstance(payload, Mapping) or type(payload.get("index")) is not int:
            return False
        if f"[{payload['index']}]" not in visible:
            return False
    return True


def _phase_and_postcondition(
    *,
    actions: Sequence[Mapping[str, Any]],
    before: str,
    after: str,
    terminal_url: str,
    approved_candidate: str,
    approved_title: str,
) -> tuple[str | None, bool, dict[str, Any]]:
    names = [name for name, _payload in _action_signature(actions)]
    windows = "\n".join(_clicked_windows(actions, before)).casefold()
    clicked_context = _clicked_context(actions, before).casefold()
    before_state = _browser_state(before)
    after_state = _browser_state(after)
    before_fold = before_state.casefold()
    after_fold = after_state.casefold()
    before_path = urlparse(_url(before_state)).path
    after_path = urlparse(_url(after_state) or terminal_url).path
    evidence = {
        "action_names": names,
        "before_url_path": before_path,
        "after_url_path": after_path,
        "clicked_windows_sha256": hashlib.sha256(windows.encode()).hexdigest(),
        "clicked_context_sha256": hashlib.sha256(clicked_context.encode()).hexdigest(),
    }
    if "buy now" in windows:
        return None, False, {**evidence, "reason": "buy_now_forbidden"}
    if "decision_checkpoint" in names:
        approved = _approval_visible(after, approved_candidate)
        return (
            "checkpoint_grounding",
            approved,
            {
                **evidence,
                "phase_subtype": "decision_checkpoint",
                "checkpoint_approved": approved,
            },
        )
    cleanup_clicks: list[tuple[int, str]] = []
    for action in actions:
        name, payload = _action_core(action)
        if name != "click" or not isinstance(payload, Mapping):
            continue
        index = payload.get("index")
        if type(index) is not int:
            continue
        window = _index_window(before_state, index).casefold()
        if "delete" in window:
            cleanup_clicks.append((index, _index_context(before_state, index).casefold()))
    cleanup_subtype: str | None = None
    unwanted_item = ""
    if len(cleanup_clicks) == 1:
        cleanup_index, cleanup_context = cleanup_clicks[0]
        cleanup_subtype, unwanted_item = _unwanted_cart_item(
            cleanup_context,
            clicked_index=cleanup_index,
            approved_candidate=approved_candidate,
            approved_title=approved_title,
        )
    if (
        before_path == "/gp/cart"
        and len(cleanup_clicks) == 1
        and cleanup_subtype in {"addon", "stale_extra"}
    ):
        absent = unwanted_item.casefold() not in after_fold
        stayed_in_cart = after_path == "/gp/cart"
        hero_remains = approved_candidate.casefold() in after_fold or (
            bool(approved_title) and approved_title.casefold() in after_fold
        )
        return (
            "dirty_cart_cleanup",
            bool(absent and stayed_in_cart and hero_remains),
            {
                **evidence,
                "phase_subtype": cleanup_subtype,
                "unwanted_item_sha256": hashlib.sha256(unwanted_item.encode()).hexdigest(),
                "unwanted_item_absent": absent,
                "approved_hero_remains": hero_remains,
                "successor_cart_visible": stayed_in_cart,
            },
        )
    if "add to cart" in windows:
        approval = _approval_visible(before, approved_candidate)
        hero_visible = approved_candidate.casefold() in clicked_context or (
            bool(approved_title) and approved_title.casefold() in clicked_context
        )
        confirmed = (
            "added to cart" in after_fold
            or after_path == "/gp/cart"
            or re.search(r"\b[1-9]\d*\s+cart\b", after_fold) is not None
        )
        successor_hero = approved_candidate.casefold() in after_fold or (
            bool(approved_title) and approved_title.casefold() in after_fold
        )
        return (
            "approved_cart_entry",
            bool(approval and hero_visible and confirmed and successor_hero),
            {
                **evidence,
                "phase_subtype": "add_to_cart",
                "approval_visible": approval,
                "approved_hero_visible": hero_visible,
                "cart_confirmation_visible": bool(confirmed),
                "successor_hero_visible": successor_hero,
            },
        )
    if "cart" in windows and "add to cart" not in windows:
        approval = _approval_visible(before, approved_candidate)
        hero_before = approved_candidate.casefold() in before_fold or (
            bool(approved_title) and approved_title.casefold() in before_fold
        )
        hero_after = approved_candidate.casefold() in after_fold or (
            bool(approved_title) and approved_title.casefold() in after_fold
        )
        already_added = bool(
            "added to cart" in before_fold
            or re.search(r"\bcart\s*\(?\s*[1-9]\d*\b", before_fold) is not None
            or re.search(r"\b[1-9]\d*\s+(?:items?\s+in\s+)?cart\b", before_fold) is not None
        )
        addon_after = "addon-plan" in after_fold or "protection plan" in after_fold
        successor_cart = after_path == "/gp/cart"
        return (
            "approved_cart_entry",
            bool(
                approval
                and hero_before
                and already_added
                and successor_cart
                and hero_after
                and addon_after
            ),
            {
                **evidence,
                "phase_subtype": "open_cart",
                "approval_visible": approval,
                "approved_hero_visible_before": hero_before,
                "hero_already_added_visible": already_added,
                "successor_cart_visible": successor_cart,
                "successor_hero_visible": hero_after,
                "successor_addon_visible": addon_after,
            },
        )
    if "proceed to checkout" in windows:
        hero_before = approved_candidate.casefold() in before_fold or (
            bool(approved_title) and approved_title.casefold() in before_fold
        )
        visible_product_ids = {
            value.casefold()
            for value in re.findall(r"EXP-LAPTOP-\d+", before_state, flags=re.IGNORECASE)
        }
        stale_product_ids = visible_product_ids - {approved_candidate.casefold()}
        clean_before = (
            "addon-plan" not in before_fold
            and "protection plan" not in before_fold
            and "accident protection" not in before_fold
            and not stale_product_ids
        )
        checkout_after = after_path.startswith("/gp/buy/")
        hero_after = approved_candidate.casefold() in after_fold or (
            bool(approved_title) and approved_title.casefold() in after_fold
        )
        return (
            "clean_checkout_order",
            bool(
                before_path == "/gp/cart"
                and hero_before
                and clean_before
                and checkout_after
                and hero_after
            ),
            {
                **evidence,
                "phase_subtype": "cart_to_checkout",
                "clean_cart_before": clean_before,
                "visible_product_ids": sorted(visible_product_ids),
                "visible_stale_product_ids": sorted(stale_product_ids),
                "approved_hero_visible_before": hero_before,
                "successor_checkout_visible": checkout_after,
                "successor_hero_visible": hero_after,
            },
        )
    if "place your order" in windows:
        confirmed = "thankyou" in after_path or "order placed" in after_fold
        checkout_before = before_path.startswith("/gp/buy/")
        hero_before = approved_candidate.casefold() in before_fold or (
            bool(approved_title) and approved_title.casefold() in before_fold
        )
        visible_product_ids = {
            value.casefold()
            for value in re.findall(r"EXP-LAPTOP-\d+", before_state, flags=re.IGNORECASE)
        }
        stale_product_ids = visible_product_ids - {approved_candidate.casefold()}
        clean_checkout_before = (
            "addon-plan" not in before_fold
            and "protection plan" not in before_fold
            and "accident protection" not in before_fold
            and not stale_product_ids
        )
        return (
            "clean_checkout_order",
            bool(checkout_before and hero_before and clean_checkout_before and confirmed),
            {
                **evidence,
                "phase_subtype": "place_order",
                "checkout_before": checkout_before,
                "approved_hero_visible_before": hero_before,
                "clean_checkout_before": clean_checkout_before,
                "visible_product_ids": sorted(visible_product_ids),
                "visible_stale_product_ids": sorted(stale_product_ids),
                "confirmation_visible": confirmed,
            },
        )
    transaction_labels = ("proceed to checkout", "place your order", "add to cart", "delete")
    browser_actions = {
        "click",
        "input",
        "select_dropdown",
        "go_back",
        "scroll",
        "search_page",
        "find_elements",
        "find_text",
        "extract",
    }
    nontransaction = not any(label in windows for label in transaction_labels)
    changed = (
        bool(after_state)
        and hashlib.sha256(before_state.encode()).digest()
        != hashlib.sha256(after_state.encode()).digest()
    )
    before_approval = _approval_visible(before, approved_candidate)
    discovery_action = bool(set(names) & browser_actions)
    return (
        "frontier_exploration",
        bool(nontransaction and discovery_action and changed and not before_approval),
        {
            **evidence,
            "phase_subtype": "exploration",
            "nontransaction": nontransaction,
            "discovery_action": discovery_action,
            "successor_changed": changed,
            "before_approval": before_approval,
        },
    )


def _reasoning_compatible(message: Mapping[str, Any], step: Mapping[str, Any]) -> bool | None:
    content = message.get("content")
    discriminators = [
        value
        for key in ("reasoning", "raw_output", "model_output", "agent_output")
        if isinstance((value := step.get(key)), str) and value
    ]
    if not isinstance(content, str) or not discriminators:
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, Mapping):
        return None
    fields = [
        str(payload.get(name) or "")
        for name in ("thinking", "evaluation_previous_goal", "memory", "next_goal")
    ]
    message_tokens = set(re.findall(r"[a-z0-9]+", " ".join(fields).casefold()))
    if len(message_tokens) < 8:
        return None
    useful_discriminator_seen = False
    for discriminator in discriminators:
        trajectory_tokens = set(re.findall(r"[a-z0-9]+", discriminator.casefold()))
        if len(trajectory_tokens) < 8:
            continue
        useful_discriminator_seen = True
        if len(message_tokens & trajectory_tokens) / len(message_tokens) >= 0.8:
            return True
    return False if useful_discriminator_seen else None


def _align_browser_steps(
    records: Sequence[Mapping[str, Any]], trajectory: Mapping[str, Any]
) -> tuple[dict[int, tuple[int, Mapping[str, Any]]], list[dict[str, Any]]]:
    steps = trajectory.get("steps")
    if not isinstance(steps, list) or any(not isinstance(step, Mapping) for step in steps):
        raise InteractiveDataError("trajectory has malformed steps")
    browser_records = [record for record in records if record.get("is_browseruse") is True]
    prepared_records: list[dict[str, Any]] = []
    for record in browser_records:
        try:
            message = (
                record.get("teacher_completion")
                if record.get("route") == "teacher"
                else _response_message(record.get("response", {}), "Qwen response")
            )
            if not isinstance(message, Mapping):
                raise InteractiveDataError("BrowserUse response has no assistant message")
            actions = _assistant_actions(
                message, strict_training_target=record.get("route") == "teacher"
            )
            error = None
        except (InteractiveDataError, json.JSONDecodeError) as exc:
            message = {}
            actions = []
            error = hashlib.sha256(str(exc).encode()).hexdigest()
        prepared_records.append(
            {
                "record": record,
                "message": message,
                "actions": actions,
                "trace_url": _url(_browser_state(_visible(record["effective_request"]))),
                "error": error,
            }
        )
    prepared_steps: list[dict[str, Any]] = []
    for step in steps:
        try:
            actions = _trajectory_actions(step)
        except (InteractiveDataError, json.JSONDecodeError) as exc:
            raise EpisodeAttritionError("trajectory action cannot be decoded") from exc
        prepared_steps.append(
            {"step": step, "actions": actions, "step_url": str(step.get("url") or "")}
        )

    def base_compatible(record_index: int, step_index: int) -> bool:
        prepared_record = prepared_records[record_index]
        prepared_step = prepared_steps[step_index]
        if prepared_record["error"] is not None or _action_signature(
            prepared_record["actions"]
        ) != _action_signature(prepared_step["actions"]):
            return False
        trace_url = str(prepared_record["trace_url"])
        step_url = str(prepared_step["step_url"])
        trace_parsed = urlparse(trace_url)
        step_parsed = urlparse(step_url)
        url_matches = (
            not trace_url
            or not step_url
            or (
                (trace_parsed.scheme, trace_parsed.netloc, trace_parsed.path)
                == (step_parsed.scheme, step_parsed.netloc, step_parsed.path)
            )
        )
        return url_matches

    record_count = len(prepared_records)
    step_count = len(prepared_steps)

    def alignment_ways(compatibility: Callable[[int, int], bool]) -> list[list[int]]:
        result = [[0] * (step_count + 1) for _ in range(record_count + 1)]
        result[0][0] = 1
        for record_index in range(1, record_count + 1):
            result[record_index][0] = 1
            for step_index in range(1, min(record_index, step_count) + 1):
                total = result[record_index - 1][step_index]
                if compatibility(record_index - 1, step_index - 1):
                    total += result[record_index - 1][step_index - 1]
                result[record_index][step_index] = min(2, total)
        return result

    ways = alignment_ways(base_compatible)
    if ways[record_count][step_count] == 0:
        raise EpisodeAttritionError(
            "zero ordered mappings cover "
            f"{step_count} trajectory steps with {record_count} responses"
        )
    if ways[record_count][step_count] > 1:

        def discriminated_compatible(record_index: int, step_index: int) -> bool:
            return (
                base_compatible(record_index, step_index)
                and _reasoning_compatible(
                    prepared_records[record_index]["message"],
                    prepared_steps[step_index]["step"],
                )
                is not False
            )

        ways = alignment_ways(discriminated_compatible)
        if ways[record_count][step_count] == 0:
            raise EpisodeAttritionError(
                "all otherwise-valid repeated mappings conflict with trajectory reasoning"
            )
        if ways[record_count][step_count] > 1:
            raise AmbiguousAlignmentError(
                "multiple ordered mappings survive action, URL, and reasoning discrimination"
            )
        compatible = discriminated_compatible
    else:
        compatible = base_compatible

    matched_record_to_step: dict[int, int] = {}
    record_index = record_count
    step_index = step_count
    while record_index:
        can_skip = ways[record_index - 1][step_index] > 0
        can_match = bool(
            step_index
            and compatible(record_index - 1, step_index - 1)
            and ways[record_index - 1][step_index - 1] > 0
        )
        if can_match and not can_skip:
            matched_record_to_step[record_index - 1] = step_index - 1
            step_index -= 1
        elif not can_skip:
            raise InteractiveDataError("unique alignment reconstruction failed")
        record_index -= 1
    if step_index != 0:
        raise InteractiveDataError("unique alignment did not consume every trajectory step")

    aligned: dict[int, tuple[int, Mapping[str, Any]]] = {}
    unmatched: list[dict[str, Any]] = []
    for index, prepared_record in enumerate(prepared_records):
        record = prepared_record["record"]
        sequence = int(record["sequence"])
        if index in matched_record_to_step:
            position = matched_record_to_step[index]
            aligned[sequence] = (position, steps[position])
            continue
        evidence = {
            "sequence": sequence,
            "route": record.get("route"),
            "reason": (
                "response_not_executable"
                if prepared_record["error"] is not None
                else "not_in_unique_executed_alignment"
            ),
            "response_sha256": record.get("response_sha256"),
        }
        if prepared_record["error"] is not None:
            evidence["detail_sha256"] = prepared_record["error"]
        unmatched.append(evidence)
    return aligned, unmatched


def _http_postcondition(server_log: str) -> dict[str, Any]:
    requests = [
        (index, method, target, int(status))
        for index, (method, target, status) in enumerate(_HTTP.findall(server_log))
    ]
    cleanup = [
        row
        for row in requests
        if row[1] in {"DELETE", "PUT"} and row[2].startswith("/api/cart/items/") and row[3] == 200
    ]
    checkouts = [
        row
        for row in requests
        if row[1] == "POST" and row[2].startswith("/api/checkout/start") and row[3] == 200
    ]
    orders = [
        row
        for row in requests
        if row[1] == "POST" and row[2].startswith("/api/checkout/place-order") and row[3] == 200
    ]
    ordered = bool(
        cleanup and checkouts and orders and cleanup[-1][0] < checkouts[-1][0] < orders[-1][0]
    )
    return {
        "cleanup_requests": len(cleanup),
        "checkout_requests": len(checkouts),
        "order_requests": len(orders),
        "cleanup_before_checkout_before_order": ordered,
    }


def _database_postcondition(path: Path, approved_candidate: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise InteractiveDataError(f"unsafe or absent transaction database: {path}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        orders = connection.execute('SELECT id FROM "order" WHERE id > 5 ORDER BY id').fetchall()
        items = (
            [
                dict(row)
                for row in connection.execute(
                    "SELECT p.asin, p.title, oi.quantity FROM orderitem oi "
                    "JOIN product p ON p.id=oi.product_id WHERE oi.order_id=? ORDER BY oi.id",
                    (int(orders[-1]["id"]),),
                ).fetchall()
            ]
            if orders
            else []
        )
    finally:
        connection.close()
    valid = (
        len(orders) == 1
        and len(items) == 1
        and items[0].get("asin") == approved_candidate
        and int(items[0].get("quantity") or 0) == 1
        and not str(items[0].get("asin", "")).startswith("ADDON-")
    )
    return {
        "valid": valid,
        "database_path": str(path.resolve()),
        "database_sha256": sha256_file(path),
        "new_order_count": len(orders),
        "ordered_items": items,
        "approved_candidate": approved_candidate,
    }


def _episode_gate(
    *,
    row: Mapping[str, Any],
    trajectory: Mapping[str, Any],
    result_path: Path,
    first_teacher_step: int,
) -> dict[str, Any]:
    stats = trajectory.get("stats")
    deliberative = stats.get("deliberative") if isinstance(stats, Mapping) else None
    approved = (
        deliberative.get("approved_candidate_id") if isinstance(deliberative, Mapping) else None
    )
    steps = trajectory.get("steps")
    if not isinstance(steps, list) or not isinstance(approved, str) or not approved:
        return {"valid": False, "reason": "missing_public_checkpoint_approval"}
    if not 0 <= first_teacher_step < len(steps):
        return {"valid": False, "reason": "teacher_suffix_boundary_invalid"}
    pre_steps = [step for step in steps[:first_teacher_step] if isinstance(step, Mapping)]
    suffix_steps = [step for step in steps[first_teacher_step:] if isinstance(step, Mapping)]
    pre_action_text = "\n".join(str(step.get("action", "")) for step in pre_steps).casefold()
    suffix_action_text = "\n".join(str(step.get("action", "")) for step in suffix_steps).casefold()
    suffix_urls = [str(step.get("url", "")) for step in suffix_steps]
    suffix_mechanisms = {
        "checkpoint": "decision_checkpoint" in suffix_action_text,
        "add_to_cart": "add to cart" in suffix_action_text,
        "cart_visited": any(urlparse(url).path == "/gp/cart" for url in suffix_urls),
        "delete": "delete" in suffix_action_text,
        "buy_now_absent": "buy now" not in suffix_action_text,
        "order_confirmation": any("thankyou" in urlparse(url).path for url in suffix_urls),
    }
    pre_takeover = {
        "steps": len(pre_steps),
        "buy_now_seen": "buy now" in pre_action_text,
        "add_to_cart_seen": "add to cart" in pre_action_text,
        "delete_seen": "delete" in pre_action_text,
    }
    log_path = result_path / "environment_server.log"
    if not log_path.is_file() or log_path.is_symlink():
        return {
            "valid": False,
            "reason": "server_log_absent",
            "teacher_suffix": suffix_mechanisms,
            "pre_takeover": pre_takeover,
        }
    http = _http_postcondition(log_path.read_text(encoding="utf-8", errors="replace"))
    databases = sorted(result_path.glob("amazon_*.db"))
    if len(databases) != 1 or databases[0].is_symlink():
        return {
            "valid": False,
            "reason": "transaction_database_not_unique",
            "teacher_suffix": suffix_mechanisms,
            "pre_takeover": pre_takeover,
        }
    database = _database_postcondition(databases[0], EXPECTED_HERO_ID)
    valid = (
        row.get("split") in {"train", "holdout"}
        and approved == EXPECTED_HERO_ID
        and suffix_mechanisms["buy_now_absent"]
        and http["order_requests"] >= 1
        and database["valid"]
    )
    return {
        "valid": valid,
        "approved_candidate": approved,
        "required_hero": EXPECTED_HERO_ID,
        "teacher_suffix": suffix_mechanisms,
        "pre_takeover": pre_takeover,
        "http": http,
        "environment_log_sha256": sha256_file(log_path),
        "database": database,
        "evaluator_read": False,
        "environment_transaction_db_read": True,
        "scorer_reward_read": False,
        "selection_filter": "successful_executed_hero50_trajectory",
    }


def _validate_episode(
    row: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    trace_path = Path(str(row["trace_path"]))
    result_path = Path(str(row["result_path"]))
    trajectory_path = result_path / "trajectory.json"
    try:
        records = _read_jsonl(trace_path)
        trajectory = _read_json(trajectory_path)
    except (InteractiveDataError, json.JSONDecodeError) as exc:
        raise EpisodeAttritionError("rollout trace or trajectory is absent/incomplete") from exc
    last_sequence = 0
    session: str | None = None
    teacher_seen = False
    for record in records:
        sequence = record.get("sequence")
        status_code = record.get("status_code")
        if (
            record.get("schema") != TRACE_SCHEMA
            or type(sequence) is not int
            or sequence <= last_sequence
            or record.get("route") not in {"student", "teacher"}
            or type(status_code) is not int
            or not 100 <= status_code <= 599
            or not isinstance(record.get("effective_request"), Mapping)
            or record.get("effective_request_sha256")
            != collection_sha256_json(record["effective_request"])
            or record.get("response_sha256") != collection_sha256_json(record["response"])
        ):
            raise InteractiveDataError("interactive proxy trace integrity failed")
        last_sequence = sequence
        if session is None:
            session = str(record.get("session_sha256"))
        elif record.get("session_sha256") != session:
            raise InteractiveDataError("one rollout trace contains multiple sessions")
        if record.get("route") == "teacher":
            if (
                status_code != 200
                or not record.get("is_browseruse")
                or (teacher_seen and record.get("takeover"))
            ):
                raise InteractiveDataError("teacher routing is not one sticky takeover")
            teacher_seen = True
            teacher_request = record.get("teacher_request")
            descriptor = record.get("teacher")
            if (
                not isinstance(teacher_request, Mapping)
                or not isinstance(descriptor, Mapping)
                or descriptor.get("provider") != "trapi"
                or descriptor.get("model_spec") != SOL_MODEL_SPEC
                or descriptor.get("logical_model") != SOL_LOGICAL_MODEL
                or descriptor.get("reasoning_effort") != SOL_REASONING_EFFORT
                or record.get("teacher_request_sha256") != collection_sha256_json(teacher_request)
                or _assert_unmodified_harness(record["effective_request"], teacher_request)
                != record["invariance_audit"]["qwen_harness_projection_sha256"]
                or record["invariance_audit"].get("unchanged") is not True
                or record.get("teacher_completion_sha256")
                != collection_sha256_json(record["teacher_completion"])
                or record.get("qwen_shadow_response_sha256")
                != collection_sha256_json(record["qwen_shadow_response"])
                or _response_message(record["response"], "served teacher response")
                != record["teacher_completion"]
            ):
                raise InteractiveDataError("teacher identity/request invariance failed")
        elif teacher_seen and record.get("is_browseruse"):
            raise InteractiveDataError("student BrowserUse route resumed after takeover")
    teachers = [record for record in records if record.get("route") == "teacher"]
    if not teachers or teachers[0].get("takeover") is not True:
        raise EpisodeAttritionError("rollout has no unique teacher takeover")
    aligned, unmatched = _align_browser_steps(records, trajectory)
    aligned_teachers = [record for record in teachers if int(record["sequence"]) in aligned]
    if not aligned_teachers:
        raise EpisodeAttritionError("rollout has no browser-executed teacher response")
    first_teacher_step = min(aligned[int(record["sequence"])][0] for record in aligned_teachers)
    episode = _episode_gate(
        row=row,
        trajectory=trajectory,
        result_path=result_path,
        first_teacher_step=first_teacher_step,
    )
    browser_records = [record for record in records if record.get("is_browseruse")]
    browser_positions = {
        int(record["sequence"]): index for index, record in enumerate(browser_records)
    }
    transitions: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    terminal_url = str((trajectory.get("steps") or [{}])[-1].get("url", ""))
    unmatched_by_sequence = {int(row["sequence"]): row for row in unmatched}
    for record in teachers:
        state_id = str(record.get("state_id"))
        sequence = int(record["sequence"])
        if sequence not in aligned:
            row_id = hashlib.sha256(
                f"{state_id}:{record['teacher_completion_sha256']}:unexecuted".encode()
            ).hexdigest()
            unmatched_evidence = unmatched_by_sequence.get(
                sequence,
                {"sequence": sequence, "route": "teacher", "reason": "unmapped"},
            )
            audits.append(
                {
                    "schema": EXECUTION_AUDIT_SCHEMA,
                    "row_id": row_id,
                    "state_id": state_id,
                    "rollout_id": row["run_id"],
                    "source_split": row["split"],
                    "phase": None,
                    "status": "rejected",
                    "accepted": False,
                    "browser_executed": False,
                    "executed": False,
                    "schema_valid": False,
                    "grounded": False,
                    "successor_observed": False,
                    "postcondition_verified": False,
                    "teacher_completion_sha256": record["teacher_completion_sha256"],
                    "unmatched_response": unmatched_evidence,
                    "evidence_sha256": hashlib.sha256(
                        canonical_bytes(unmatched_evidence)
                    ).hexdigest(),
                    "evaluator_read": False,
                    "environment_transaction_db_read": True,
                    "scorer_reward_read": False,
                }
            )
            continue
        position, step = aligned[sequence]
        browser_position = browser_positions[sequence]
        successor = (
            browser_records[browser_position + 1]
            if browser_position + 1 < len(browser_records)
            else None
        )
        before = _visible(record["effective_request"])
        after = _visible(successor["effective_request"]) if successor is not None else ""
        teacher_message = dict(record["teacher_completion"])
        actions = _assistant_actions(teacher_message)
        grounded = _grounded(actions, before)
        phase, phase_ok, phase_evidence = _phase_and_postcondition(
            actions=actions,
            before=before,
            after=after,
            terminal_url=terminal_url,
            approved_candidate=EXPECTED_HERO_ID,
            approved_title=str(
                ((episode.get("database") or {}).get("ordered_items") or [{}])[0].get("title", "")
            ),
        )
        phase_subtype = str(phase_evidence.get("phase_subtype") or "")
        http = episode.get("http") if isinstance(episode.get("http"), Mapping) else {}
        subtype_http_verified = True
        if phase_subtype in {"addon", "stale_extra"}:
            subtype_http_verified = int(http.get("cleanup_requests") or 0) >= 1
        elif phase_subtype == "cart_to_checkout":
            subtype_http_verified = int(http.get("checkout_requests") or 0) >= 1
        elif phase_subtype == "place_order":
            subtype_http_verified = int(http.get("order_requests") or 0) >= 1
        phase_evidence["subtype_http_verified"] = subtype_http_verified
        phase_ok = bool(phase_ok and subtype_http_verified)
        note = str(step.get("note") or "").casefold()
        no_error = not any(marker in note for marker in ("error", "failed", "timeout"))
        successor_observed = successor is not None or "done" in {
            name for name, _payload in _action_signature(actions)
        }
        effective = record["effective_request"]
        messages = effective.get("messages")
        try:
            qwen_message = _response_message(record["qwen_shadow_response"], "Qwen shadow")
        except InteractiveDataError:
            qwen_message = {}
        qwen_shape_valid = bool(
            qwen_message.get("role") == "assistant" and isinstance(qwen_message.get("content"), str)
        )
        training_shape_valid = bool(
            isinstance(messages, list)
            and messages
            and any(
                isinstance(message, Mapping) and message.get("role") == "user"
                for message in messages
            )
            and isinstance(messages[-1], Mapping)
            and messages[-1].get("role") != "assistant"
            and isinstance(effective.get("tools", []), list)
            and qwen_shape_valid
        )
        accepted = bool(
            episode.get("valid")
            and phase in PHASES
            and grounded
            and no_error
            and successor_observed
            and phase_ok
            and training_shape_valid
        )
        row_id = hashlib.sha256(
            f"{state_id}:{record['teacher_completion_sha256']}:{phase}:{phase_subtype}".encode()
        ).hexdigest()
        audit = {
            "schema": EXECUTION_AUDIT_SCHEMA,
            "row_id": row_id,
            "state_id": state_id,
            "rollout_id": row["run_id"],
            "source_split": row["split"],
            "phase": phase,
            "phase_subtype": phase_subtype,
            "status": "accepted" if accepted else "rejected",
            "accepted": accepted,
            "browser_executed": True,
            "executed": True,
            "schema_valid": True,
            "training_shape_valid": training_shape_valid,
            "qwen_shape_valid": qwen_shape_valid,
            "grounded": grounded,
            "successor_observed": successor_observed,
            "postcondition_verified": bool(phase_ok and episode.get("valid")),
            "trajectory_step": position + 1,
            "trajectory_step_sha256": hashlib.sha256(canonical_bytes(step)).hexdigest(),
            "pre_request_sha256": record["effective_request_sha256"],
            "successor_request_sha256": (
                successor.get("effective_request_sha256") if successor is not None else None
            ),
            "teacher_completion_sha256": record["teacher_completion_sha256"],
            "phase_evidence": phase_evidence,
            "episode_postcondition": episode,
            "evaluator_read": False,
            "environment_transaction_db_read": True,
            "scorer_reward_read": False,
        }
        audit["evidence_sha256"] = hashlib.sha256(
            canonical_bytes(
                {
                    "trajectory_step_sha256": audit["trajectory_step_sha256"],
                    "pre_request_sha256": audit["pre_request_sha256"],
                    "successor_request_sha256": audit["successor_request_sha256"],
                    "teacher_completion_sha256": audit["teacher_completion_sha256"],
                    "phase_evidence": audit["phase_evidence"],
                    "episode_postcondition": audit["episode_postcondition"],
                }
            )
        ).hexdigest()
        audits.append(audit)
        if not accepted:
            continue
        transition = {
            "schema": TRANSITION_SCHEMA,
            "schema_version": 1,
            "row_id": row_id,
            "state_id": state_id,
            "phase": phase,
            "phase_subtype": phase_subtype,
            "trajectory_id": row["run_id"],
            "task_id": trajectory.get("task_id"),
            "variant": row["variant"],
            "horizon": row["horizon"],
            "source_split": row["split"],
            "source_sequence": sequence,
            "is_takeover_state": record.get("takeover") is True,
            "messages_before_action": json.loads(canonical_json(effective["messages"])),
            "tools": json.loads(canonical_json(effective.get("tools", []))),
            "tool_choice": json.loads(canonical_json(effective.get("tool_choice"))),
            "parallel_tool_calls": effective.get("parallel_tool_calls"),
            "response_format": json.loads(canonical_json(effective.get("response_format"))),
            "teacher_message": teacher_message,
            "qwen_message": qwen_message,
            "chosen_by_executor": True,
            "execution": {
                "attempted": True,
                "valid": True,
                "objective_success": True,
                "successor_observed": True,
                "postcondition_verified": True,
            },
            "source": {
                "trace_path": str(trace_path.resolve()),
                "trace_sha256": sha256_file(trace_path),
                "trajectory_path": str(trajectory_path.resolve()),
                "trajectory_sha256": sha256_file(trajectory_path),
                "request_sha256": record["request_sha256"],
                "effective_request_sha256": record["effective_request_sha256"],
                "successor_request_sha256": audit["successor_request_sha256"],
                "teacher_request_sha256": record["teacher_request_sha256"],
                "teacher_completion_sha256": record["teacher_completion_sha256"],
                "execution_audit_sha256": hashlib.sha256(canonical_bytes(audit)).hexdigest(),
            },
        }
        transitions.append(transition)
    episode_audit = {
        "rollout_id": row["run_id"],
        "variant": row["variant"],
        "split": row["split"],
        "horizon": row["horizon"],
        "trace_sha256": sha256_file(trace_path),
        "trajectory_sha256": sha256_file(trajectory_path),
        "teacher_calls": len(teachers),
        "aligned_teacher_calls": len(aligned_teachers),
        "unmatched_browser_responses": unmatched,
        "accepted_transitions": len(transitions),
        "postcondition": episode,
    }
    return transitions, audits, episode_audit


def validate_bundle(
    *,
    bundle_path: Path,
    output_root: Path,
    collector_commit: str,
    validator_commit: str,
    validator_data_sha256: str,
    validator_cli_sha256: str,
) -> dict[str, Any]:
    bundle = audit_bundle(bundle_path)
    provenance = _execution_provenance(
        collector_commit=collector_commit,
        validator_commit=validator_commit,
        validator_data_sha256=validator_data_sha256,
        validator_cli_sha256=validator_cli_sha256,
    )
    output_root = output_root.resolve()
    if output_root.exists() or output_root.is_symlink():
        raise InteractiveDataError("validation output root must be fresh")
    output_root.mkdir(parents=True)
    transitions: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    for row in bundle["rows"]:
        try:
            episode_transitions, episode_audits, episode = _validate_episode(row)
        except EpisodeAttritionError as exc:
            episode_transitions = []
            episode_audits = []
            trace_path = Path(str(row["trace_path"]))
            episode = {
                "rollout_id": row["run_id"],
                "variant": row["variant"],
                "split": row["split"],
                "horizon": row["horizon"],
                "trace_sha256": (
                    sha256_file(trace_path)
                    if trace_path.is_file() and not trace_path.is_symlink()
                    else None
                ),
                "teacher_calls": 0,
                "accepted_transitions": 0,
                "postcondition": {
                    "valid": False,
                    "reason": (
                        "ambiguous_trace_trajectory_alignment"
                        if isinstance(exc, AmbiguousAlignmentError)
                        else "structural_rollout_attrition"
                    ),
                    "alignment_ambiguity_rejected": isinstance(exc, AmbiguousAlignmentError),
                    "detail_sha256": hashlib.sha256(str(exc).encode()).hexdigest(),
                    "evaluator_read": False,
                    "environment_transaction_db_read": False,
                    "scorer_reward_read": False,
                },
            }
        transitions.extend(episode_transitions)
        audits.extend(episode_audits)
        episodes.append(episode)
    files = {
        "transitions": _write_jsonl(output_root / "validated_transitions.jsonl", transitions),
        "execution_audits": _write_jsonl(output_root / "execution_audits.jsonl", audits),
        "episodes": _write_jsonl(output_root / "episode_audits.jsonl", episodes),
    }
    body = {
        "schema": VALIDATION_SCHEMA,
        "status": "complete",
        "bundle_path": str(bundle_path.resolve()),
        "bundle_sha256": sha256_file(bundle_path),
        "teacher_model": SOL_MODEL_SPEC,
        "teacher_reasoning_effort": SOL_REASONING_EFFORT,
        "provenance": provenance,
        "files": files,
        "accepted_transitions": len(transitions),
        "execution_audits": len(audits),
        "episode_counts": dict(Counter(str(row["split"]) for row in episodes)),
        "valid_episode_counts": dict(
            Counter(str(row["split"]) for row in episodes if row["postcondition"].get("valid"))
        ),
        "phase_counts": dict(Counter(str(row["phase"]) for row in transitions)),
        "phase_subtype_counts": dict(
            Counter(
                f"{row['phase']}/{row.get('phase_subtype')}"
                for row in transitions
                if row.get("phase_subtype")
            )
        ),
        "evaluator_rows": 0,
        "scorer_reward_rows": 0,
        "environment_transaction_db_rows": sum(
            row["postcondition"].get("environment_transaction_db_read") is True for row in episodes
        ),
        "successful_executed_trajectory_filter": True,
    }
    return _write_manifest(output_root / "manifest.json", body, "manifest_sha256")


def _audit_validation(
    path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    manifest = _read_json(path.resolve())
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    _audit_provenance_record(manifest.get("provenance"))
    if (
        manifest.get("schema") != VALIDATION_SCHEMA
        or manifest.get("status") != "complete"
        or manifest.get("manifest_sha256") != hashlib.sha256(canonical_bytes(body)).hexdigest()
        or manifest.get("teacher_model") != SOL_MODEL_SPEC
        or manifest.get("evaluator_rows") != 0
        or manifest.get("scorer_reward_rows") != 0
        or manifest.get("successful_executed_trajectory_filter") is not True
    ):
        raise InteractiveDataError("validation manifest policy changed")
    root = path.resolve().parent
    descriptors = manifest.get("files")
    expected_files = {
        "transitions": "validated_transitions.jsonl",
        "execution_audits": "execution_audits.jsonl",
        "episodes": "episode_audits.jsonl",
    }
    if not isinstance(descriptors, Mapping) or set(descriptors) != set(expected_files):
        raise InteractiveDataError("validation manifest has no file inventory")
    loaded: dict[str, list[dict[str, Any]]] = {}
    for name, descriptor in descriptors.items():
        if not isinstance(descriptor, Mapping):
            raise InteractiveDataError("validation descriptor is invalid")
        relative_path = descriptor.get("relative_path")
        if relative_path != expected_files[str(name)]:
            raise InteractiveDataError("validation artifact path changed")
        artifact = (root / str(relative_path)).resolve()
        if artifact.parent != root:
            raise InteractiveDataError("validation artifact escaped its sealed root")
        rows = _read_jsonl(artifact)
        if (
            sha256_file(artifact) != descriptor.get("sha256")
            or artifact.stat().st_size != descriptor.get("bytes")
            or len(rows) != descriptor.get("rows")
        ):
            raise InteractiveDataError("validation artifact inventory changed")
        loaded[str(name)] = rows
    if manifest.get("phase_counts") != dict(
        Counter(str(row.get("phase")) for row in loaded["transitions"])
    ) or manifest.get("phase_subtype_counts") != dict(
        Counter(
            f"{row.get('phase')}/{row.get('phase_subtype')}"
            for row in loaded["transitions"]
            if row.get("phase_subtype")
        )
    ):
        raise InteractiveDataError("validation transition phase inventory changed")
    return manifest, loaded["transitions"], loaded["execution_audits"]


def _normalize_retention(row: Mapping[str, Any], *, audit: Mapping[str, Any]) -> dict[str, Any]:
    messages = row.get("messages")
    if (
        not isinstance(messages, list)
        or len(messages) < 2
        or not isinstance(messages[-1], Mapping)
        or messages[-1].get("role") != "assistant"
        or not any(
            isinstance(message, Mapping) and message.get("role") == "user"
            for message in messages[:-1]
        )
        or not isinstance(row.get("tools", []), list)
    ):
        raise InteractiveDataError("step25 retention row has no valid assistant target")
    before = messages[:-1]
    target = messages[-1]
    state_id = str(row.get("state_id"))
    variant = audit.get("variant")
    source_phase = audit.get("phase")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", state_id)
        or variant not in VARIANTS
        or source_phase not in RETENTION_SOURCE_PHASES
        or audit.get("split") != "train"
        or ((audit.get("action_validation") or {}).get("status") != "accepted")
        or audit.get("teacher_completion_sha256") != collection_sha256_json(target)
        or (audit.get("teacher") or {}).get("model_spec") != SOL_MODEL_SPEC
        or (audit.get("teacher") or {}).get("reasoning_effort") != SOL_REASONING_EFFORT
    ):
        raise InteractiveDataError("step25 retention row/audit join is not accepted train data")
    latest = _visible({"messages": before})
    actions = _assistant_actions(target, strict_training_target=False)
    clicked = "\n".join(_clicked_windows(actions, latest)).casefold()
    current_path = urlparse(_url(_browser_state(latest))).path
    if (
        current_path == "/gp/cart"
        or current_path.startswith("/gp/buy/")
        or any(
            marker in clicked
            for marker in ("buy now", "add to cart", "protection plan", "place your order")
        )
    ):
        raise _RetentionExcluded("step25 discovery retention row enters a purchase/addon path")
    row_id = hashlib.sha256(
        f"retention:step25:{state_id}:{collection_sha256_json(target)}".encode()
    ).hexdigest()
    return {
        "schema": RETENTION_SCHEMA,
        "schema_version": 1,
        "row_id": row_id,
        "state_id": state_id,
        "phase": RETENTION_PHASE,
        "variant": variant,
        "source_split": "train",
        "messages_before_action": json.loads(canonical_json(before)),
        "tools": json.loads(canonical_json(row.get("tools", []))),
        "teacher_message": json.loads(canonical_json(target)),
        "retention": True,
        "source": {
            "kind": "step25_discovery",
            "source_phase": source_phase,
            "source_row_sha256": hashlib.sha256(canonical_bytes(row)).hexdigest(),
            "source_audit_sha256": hashlib.sha256(canonical_bytes(audit)).hexdigest(),
            "teacher_completion_sha256": audit["teacher_completion_sha256"],
        },
    }


def _retention_pool(train_path: Path, audits_path: Path) -> list[dict[str, Any]]:
    audits = _read_jsonl(audits_path)
    audit_by_state: dict[str, dict[str, Any]] = {}
    for audit in audits:
        state_id = str(audit.get("state_id"))
        if (
            audit.get("split") == "train"
            and audit.get("phase") in RETENTION_SOURCE_PHASES
            and ((audit.get("action_validation") or {}).get("status") == "accepted")
        ):
            if state_id in audit_by_state:
                raise InteractiveDataError("step25 accepted train audit repeats a state_id")
            audit_by_state[state_id] = audit
    pools: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    seen_sft: set[str] = set()
    for row in _read_jsonl(train_path):
        state_id = str(row.get("state_id"))
        if state_id in seen_sft:
            raise InteractiveDataError("step25 train SFT repeats a state_id")
        seen_sft.add(state_id)
        audit = audit_by_state.get(state_id)
        if audit is None:
            continue
        try:
            normalized = _normalize_retention(row, audit=audit)
        except _RetentionExcluded:
            continue
        pools[(str(normalized["variant"]), str(normalized["source"]["source_phase"]))].append(
            normalized
        )
    selected: list[dict[str, Any]] = []
    for variant in VARIANTS:
        for source_phase in RETENTION_SOURCE_PHASES:
            pool = sorted(
                pools[(variant, source_phase)],
                key=lambda item: hashlib.sha256(
                    f"retention-choice:{item['state_id']}".encode()
                ).hexdigest(),
            )
            if len(pool) < 2:
                raise InteractiveDataError(
                    f"step25 retention needs two {variant}/{source_phase} states"
                )
            selected.extend(pool[:2])
    if len(selected) != RETENTION_ROWS or Counter(row["variant"] for row in selected) != {
        variant: RETENTION_PER_VARIANT for variant in VARIANTS
    }:
        raise InteractiveDataError("step25 retention is not exact24 balanced 6/variant")
    return selected


def _balanced_phase_pick(
    pool: Sequence[dict[str, Any]],
    *,
    count: int,
    phase: str,
    variant: str,
    prefer_stale_extra: bool = False,
) -> list[dict[str, Any]]:
    ordered = sorted(
        pool,
        key=lambda row: hashlib.sha256(
            (
                f"phase-choice:{phase}:{variant}:{row.get('phase_subtype')}:"
                f"{row['trajectory_id']}:{row['state_id']}"
            ).encode()
        ).hexdigest(),
    )
    if len(ordered) < count:
        raise InteractiveDataError(
            f"phase {phase}/{variant} has {len(ordered)} rows; needs {count}"
        )
    chosen: list[dict[str, Any]] = []
    if prefer_stale_extra:
        stale = [row for row in ordered if row.get("phase_subtype") == "stale_extra"]
        if stale:
            chosen.append(stale[0])
    chosen_ids = {str(row["row_id"]) for row in chosen}
    used_horizons = {int(row["horizon"]) for row in chosen}
    for horizon in (2, 8, 16, 24):
        if len(chosen) >= count or horizon in used_horizons:
            continue
        horizon_rows = [
            row
            for row in ordered
            if row.get("horizon") == horizon and str(row["row_id"]) not in chosen_ids
        ]
        if horizon_rows:
            chosen.append(horizon_rows[0])
            chosen_ids.add(str(horizon_rows[0]["row_id"]))
            used_horizons.add(horizon)
    if len(chosen) < count:
        chosen.extend(row for row in ordered if str(row["row_id"]) not in chosen_ids)
    return chosen[:count]


def materialize_exact_collection(
    *,
    validation_report: Path,
    step25_train_sft: Path,
    step25_label_audits: Path,
    output_root: Path,
    collector_commit: str,
    validator_commit: str,
    validator_data_sha256: str,
    validator_cli_sha256: str,
) -> dict[str, Any]:
    validation, transitions, audits = _audit_validation(validation_report)
    provenance = _execution_provenance(
        collector_commit=collector_commit,
        validator_commit=validator_commit,
        validator_data_sha256=validator_data_sha256,
        validator_cli_sha256=validator_cli_sha256,
    )
    if validation.get("provenance") != provenance:
        raise InteractiveDataError("materializer provenance differs from validation provenance")
    audit_by_row = {str(row.get("row_id")): row for row in audits}
    if len(audit_by_row) != len(audits):
        raise InteractiveDataError("validation execution audits repeat a row_id")
    candidates = [
        row
        for row in transitions
        if row.get("source_split") == "train"
        and row.get("phase") in PHASES
        and row.get("chosen_by_executor") is True
        and isinstance(row.get("execution"), Mapping)
        and row["execution"].get("valid") is True
        and row["execution"].get("objective_success") is True
        and isinstance(audit_by_row.get(str(row.get("row_id"))), Mapping)
        and audit_by_row[str(row["row_id"])].get("accepted") is True
        and audit_by_row[str(row["row_id"])].get("state_id") == row.get("state_id")
        and audit_by_row[str(row["row_id"])].get("phase") == row.get("phase")
        and audit_by_row[str(row["row_id"])].get("phase_subtype") == row.get("phase_subtype")
    ]
    by_rollout_phase: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        key = (
            str(row["trajectory_id"]),
            str(row["phase"]),
            str(row.get("phase_subtype") or ""),
        )
        by_rollout_phase[key].append(row)
    by_phase_variant: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for rows in by_rollout_phase.values():
        rows.sort(
            key=lambda row: (
                not bool(row.get("is_takeover_state")),
                int(row["source_sequence"]),
                hashlib.sha256(
                    f"executed-state:{row['trajectory_id']}:{row['state_id']}".encode()
                ).hexdigest(),
            )
        )
        row = rows[0]
        by_phase_variant[(str(row["phase"]), str(row["variant"]))].append(row)
    selected: list[dict[str, Any]] = []
    for phase, quota in PHASE_QUOTAS.items():
        per_variant = quota // 4
        for variant in VARIANTS:
            pool = by_phase_variant[(phase, variant)]
            if phase == "approved_cart_entry":
                for subtype in ("add_to_cart", "open_cart"):
                    subtype_pool = [row for row in pool if row.get("phase_subtype") == subtype]
                    selected.extend(
                        _balanced_phase_pick(
                            subtype_pool,
                            count=2,
                            phase=f"{phase}/{subtype}",
                            variant=variant,
                        )
                    )
                continue
            if phase == "clean_checkout_order":
                for subtype in ("cart_to_checkout", "place_order"):
                    subtype_pool = [row for row in pool if row.get("phase_subtype") == subtype]
                    selected.extend(
                        _balanced_phase_pick(
                            subtype_pool,
                            count=1,
                            phase=f"{phase}/{subtype}",
                            variant=variant,
                        )
                    )
                continue
            selected.extend(
                _balanced_phase_pick(
                    pool,
                    count=per_variant,
                    phase=phase,
                    variant=variant,
                    prefer_stale_extra=phase == "dirty_cart_cleanup",
                )
            )
    if len(selected) != 72 or Counter(row["phase"] for row in selected) != Counter(PHASE_QUOTAS):
        raise InteractiveDataError("deterministic executed selection is not exact72")
    subtype_counts = Counter(
        f"{row['phase']}/{row.get('phase_subtype')}"
        for row in selected
        if row["phase"] in {"approved_cart_entry", "clean_checkout_order"}
    )
    if dict(subtype_counts) != PHASE_SUBTYPE_QUOTAS:
        raise InteractiveDataError(f"transactional phase subtype counts drifted: {subtype_counts}")
    stale_available_variants = {
        str(row["variant"])
        for row in candidates
        if row["phase"] == "dirty_cart_cleanup" and row.get("phase_subtype") == "stale_extra"
    }
    stale_selected_variants = {
        str(row["variant"])
        for row in selected
        if row["phase"] == "dirty_cart_cleanup" and row.get("phase_subtype") == "stale_extra"
    }
    if not stale_available_variants.issubset(stale_selected_variants):
        raise InteractiveDataError("available stale-extra cleanup diversity was not selected")
    dirty_available_subtype_counts = dict(
        Counter(
            str(row.get("phase_subtype"))
            for row in candidates
            if row["phase"] == "dirty_cart_cleanup"
        )
    )
    dirty_selected_subtype_counts = dict(
        Counter(
            str(row.get("phase_subtype"))
            for row in selected
            if row["phase"] == "dirty_cart_cleanup"
        )
    )
    selected_ids = [str(row["state_id"]) for row in selected]
    if len(set(selected_ids)) != 72 or any(
        not re.fullmatch(r"[0-9a-f]{64}", state_id) for state_id in selected_ids
    ):
        raise InteractiveDataError("executed selection has invalid/repeated state identities")

    retention = _retention_pool(step25_train_sft, step25_label_audits)
    retention_ids = [str(row["state_id"]) for row in retention]
    if len(retention) != 24 or len(set(retention_ids)) != 24:
        raise InteractiveDataError("retention selection is not exact24 unique states")
    if set(selected_ids) & set(retention_ids):
        raise InteractiveDataError("selection and retention state identities overlap")
    selected.sort(
        key=lambda row: (PHASES.index(str(row["phase"])), str(row["variant"]), str(row["state_id"]))
    )
    retention.sort(
        key=lambda row: (
            str(row["variant"]),
            str(row["source"]["source_phase"]),
            str(row["state_id"]),
        )
    )
    selected_audits = [audit_by_row[str(row["row_id"])] for row in selected]
    if any(
        audit.get("status") != "accepted"
        or audit.get("accepted") is not True
        or audit.get("browser_executed") is not True
        or audit.get("executed") is not True
        or audit.get("successor_observed") is not True
        or audit.get("postcondition_verified") is not True
        for audit in selected_audits
    ):
        raise InteractiveDataError("selected execution audit is not fully accepted")

    output_root = output_root.resolve()
    if output_root.exists() or output_root.is_symlink():
        raise InteractiveDataError("collection output root must be fresh")
    output_root.mkdir(parents=True)
    files = {
        "selection.jsonl": _write_jsonl(output_root / "selection.jsonl", selected),
        "retention.jsonl": _write_jsonl(output_root / "retention.jsonl", retention),
        "execution_audits.jsonl": _write_jsonl(
            output_root / "execution_audits.jsonl", selected_audits
        ),
    }
    phase_counts = dict(Counter(str(row["phase"]) for row in selected))
    phase_subtype_counts = dict(
        Counter(f"{row['phase']}/{row.get('phase_subtype')}" for row in selected)
    )
    body = {
        "schema": COLLECTION_SCHEMA,
        "status": "ok",
        "teacher_model": SOL_LOGICAL_MODEL,
        "teacher_reasoning_effort": SOL_REASONING_EFFORT,
        "provenance": provenance,
        "renderer": {"name": "qwen3.5", "enable_thinking": True, "assistant_only": True},
        "selection_rows": 72,
        "retention_rows": 24,
        "phase_counts": phase_counts,
        "phase_subtype_counts": phase_subtype_counts,
        "dirty_cleanup_diversity": {
            "available_subtype_counts": dirty_available_subtype_counts,
            "selected_subtype_counts": dirty_selected_subtype_counts,
            "stale_extra_available_variants": sorted(stale_available_variants),
            "stale_extra_selected_variants": sorted(stale_selected_variants),
            "availability_floor_satisfied": True,
        },
        "evaluation_rows": 0,
        "heldout_rows": 0,
        "state_overlap": 0,
        "files": files,
        "source": {
            "validation_manifest_path": str(validation_report.resolve()),
            "validation_manifest_sha256": sha256_file(validation_report),
            "validation_manifest_body_sha256": validation["manifest_sha256"],
            "step25_train_sft_path": str(step25_train_sft.resolve()),
            "step25_train_sft_sha256": sha256_file(step25_train_sft),
            "step25_label_audits_path": str(step25_label_audits.resolve()),
            "step25_label_audits_sha256": sha256_file(step25_label_audits),
            "selection_rule": (
                "one_executed_state_per_rollout_phase;distinct_horizons_greedy;"
                "deterministic_sha_replica_fill"
            ),
            "retention_rule": "step25_train_discovery_2_per_source_phase_6_per_variant",
            "unique_states": 96,
            "successful_executed_hero50_filter": True,
            "scorer_reward_or_evaluator_selection": False,
        },
    }
    return _write_manifest(output_root / "manifest.json", body, "manifest_sha256")


def materialize_heldout_preflight(
    *,
    validation_report: Path,
    output_root: Path,
    collector_commit: str,
    validator_commit: str,
    validator_data_sha256: str,
    validator_cli_sha256: str,
) -> dict[str, Any]:
    """Seal heldout executed actions separately; never substitute train rows."""

    validation, transitions, audits = _audit_validation(validation_report)
    provenance = _execution_provenance(
        collector_commit=collector_commit,
        validator_commit=validator_commit,
        validator_data_sha256=validator_data_sha256,
        validator_cli_sha256=validator_cli_sha256,
    )
    if validation.get("provenance") != provenance:
        raise InteractiveDataError("materializer provenance differs from validation provenance")
    audit_by_row = {str(row.get("row_id")): row for row in audits}
    if len(audit_by_row) != len(audits):
        raise InteractiveDataError("validation execution audits repeat a row_id")
    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in transitions:
        audit = audit_by_row.get(str(row.get("row_id")))
        if (
            row.get("source_split") == "holdout"
            and row.get("phase") in HELDOUT_TARGET_QUOTAS
            and row.get("chosen_by_executor") is True
            and isinstance(audit, Mapping)
            and audit.get("accepted") is True
            and audit.get("browser_executed") is True
            and audit.get("successor_observed") is True
            and audit.get("postcondition_verified") is True
            and audit.get("state_id") == row.get("state_id")
            and audit.get("phase") == row.get("phase")
            and audit.get("phase_subtype") == row.get("phase_subtype")
        ):
            pools[str(row["phase"])].append(row)
    selected: list[dict[str, Any]] = []
    available_counts = {phase: len(pools[phase]) for phase in HELDOUT_TARGET_QUOTAS}
    for phase, quota in HELDOUT_TARGET_QUOTAS.items():
        pool = sorted(
            pools[phase],
            key=lambda row: hashlib.sha256(
                f"heldout:{phase}:{row['variant']}:{row['trajectory_id']}:{row['state_id']}".encode()
            ).hexdigest(),
        )
        chosen: list[dict[str, Any]] = []
        for variant in VARIANTS:
            variant_rows = [row for row in pool if row.get("variant") == variant]
            if variant_rows and len(chosen) < quota:
                chosen.append(variant_rows[0])
        chosen_ids = {str(row["row_id"]) for row in chosen}
        chosen.extend(row for row in pool if str(row["row_id"]) not in chosen_ids)
        selected.extend(chosen[:quota])
    selected.sort(
        key=lambda row: (
            PHASES.index(str(row["phase"])),
            str(row["variant"]),
            str(row["state_id"]),
        )
    )
    selected_ids = [str(row["state_id"]) for row in selected]
    if len(selected_ids) != len(set(selected_ids)):
        raise InteractiveDataError("heldout preflight repeats a state identity")
    selected_audits = [audit_by_row[str(row["row_id"])] for row in selected]
    actual_counts = dict(Counter(str(row["phase"]) for row in selected))
    available_subtype_counts = dict(
        Counter(
            f"{row['phase']}/{row.get('phase_subtype')}"
            for rows in pools.values()
            for row in rows
            if row.get("phase_subtype")
        )
    )
    actual_subtype_counts = dict(
        Counter(
            f"{row['phase']}/{row.get('phase_subtype')}"
            for row in selected
            if row.get("phase_subtype")
        )
    )
    exact = len(selected) == 24 and actual_counts == HELDOUT_TARGET_QUOTAS

    output_root = output_root.resolve()
    if output_root.exists() or output_root.is_symlink():
        raise InteractiveDataError("heldout preflight output root must be fresh")
    output_root.mkdir(parents=True)
    files = {
        "heldout_preflight.jsonl": _write_jsonl(output_root / "heldout_preflight.jsonl", selected),
        "heldout_execution_audits.jsonl": _write_jsonl(
            output_root / "heldout_execution_audits.jsonl", selected_audits
        ),
    }
    shortfalls = {
        phase: max(0, quota - actual_counts.get(phase, 0))
        for phase, quota in HELDOUT_TARGET_QUOTAS.items()
    }
    body = {
        "schema": HELDOUT_PREFLIGHT_SCHEMA,
        "status": "ok" if exact else "quota_limited",
        "teacher_model": SOL_LOGICAL_MODEL,
        "teacher_reasoning_effort": SOL_REASONING_EFFORT,
        "provenance": provenance,
        "target_rows": 24,
        "quota_derivation": (
            "training_phase_ratio_scaled_to_24_largest_remainder_phase_order_tiebreak"
        ),
        "materialized_rows": len(selected),
        "target_phase_counts": HELDOUT_TARGET_QUOTAS,
        "phase_counts": actual_counts,
        "available_phase_counts": available_counts,
        "phase_subtype_counts": actual_subtype_counts,
        "available_phase_subtype_counts": available_subtype_counts,
        "phase_shortfalls": shortfalls,
        "train_rows": 0,
        "heldout_only": True,
        "used_for_training": False,
        "files": files,
        "source": {
            "validation_manifest_path": str(validation_report.resolve()),
            "validation_manifest_sha256": sha256_file(validation_report),
            "validation_manifest_body_sha256": validation["manifest_sha256"],
            "selection_rule": "heldout_only_phase_quota_variant_first_then_sha_no_backfill",
            "train_substitution": False,
        },
    }
    return _write_manifest(output_root / "manifest.json", body, "manifest_sha256")


def audit_collection_manifest(path: Path) -> dict[str, Any]:
    """Run the exact frozen trainer-side collection validator read-only."""

    from .action_weighted_ce_training import validate_collection_manifest

    validated = validate_collection_manifest(path)
    return {
        "status": "ok",
        "schema": COLLECTION_SCHEMA,
        "manifest_path": validated["manifest_path"],
        "manifest_sha256": validated["manifest_sha256"],
        "manifest_body_sha256": validated["manifest_body_sha256"],
        "selection_rows": len(validated["selection"]),
        "retention_rows": len(validated["retention"]),
        "state_count": validated["state_count"],
    }


__all__ = [
    "AmbiguousAlignmentError",
    "COLLECTION_SCHEMA",
    "EpisodeAttritionError",
    "EXECUTION_AUDIT_SCHEMA",
    "InteractiveDataError",
    "PHASES",
    "PHASE_QUOTAS",
    "PHASE_SUBTYPE_QUOTAS",
    "TRANSITION_SCHEMA",
    "VALIDATION_SCHEMA",
    "audit_collection_manifest",
    "materialize_heldout_preflight",
    "materialize_exact_collection",
    "validate_bundle",
]
