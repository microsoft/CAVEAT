"""Sequence-level laptop adaptation in browser-use's real action interface.

Fixed-v6 learned isolated next actions but did not enter those behaviors during
long CAVEAT-Shop runs.  Fixed-v7 therefore trains complete short histories: persist
through pagination into a literal PDP and checkpoint, reconcile a dirty cart
before ordering, and recover the exact assigned localhost after a bad external
navigation.  Released laptop development histories are used and explicitly
attested; later laptop results are same-task adaptation evidence, not held-out
generalization.  No other CAVEAT-Shop category is consulted.
"""

from __future__ import annotations

import copy
import json
import math
import os
import re
import tomllib
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    canonical_json,
    publish_bytes,
    publish_json,
    publish_jsonl,
    read_json,
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
from .browser_action_continuation import _tree_identity, _verified_source
from .browser_action_continuation_receipt import _verify_adapter
from .browser_action_curriculum import (
    _agent_output,
    _checkpoint_arguments,
    _output_stack,
    _public_state,
    _stable_key,
    _system_with_contract,
)
from .config import Campaign
from .prime_data import materialize_prime_dataset
from .sft_data import SFT_SOURCE_SCHEMA, _scan_visible, validate_sft_sample
from .splits import load_split_manifest
from .train_configs import _array, _quoted, _smoke_targets

FIXED_V7_CURRICULUM_SCHEMA = "caveat-27b.browser-action-fixed-v7-curriculum.v1"
FIXED_V7_PLAN_SCHEMA = "caveat-27b.browser-action-fixed-v7-plan.v1"
FIXED_V7_RECEIPT_SCHEMA = "caveat-27b.browser-action-fixed-v7-training-receipt.v1"

_SOURCE_STEP = 20
_FINAL_STEP = 23
_NEW_UPDATES = 3
_LEARNING_RATE = 2.0e-6
_PROCEDURAL_TASKS = 32
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_KINDS = (
    "caveat_shop-discovery-sequence",
    "caveat_shop-cart-sequence",
    "caveat_shop-recovery-sequence",
    "caveat_shop-preservation-sequence",
    "procedural-discovery-sequence",
    "procedural-cart-sequence",
    "procedural-recovery-sequence",
    "procedural-preservation-sequence",
)
_ROW_QUOTAS = {
    "caveat_shop-discovery-sequence": 24,
    "caveat_shop-cart-sequence": 24,
    "caveat_shop-recovery-sequence": 12,
    "caveat_shop-preservation-sequence": 8,
    "procedural-discovery-sequence": 20,
    "procedural-cart-sequence": 16,
    "procedural-recovery-sequence": 12,
    "procedural-preservation-sequence": 12,
}
_CAVEAT_SHOP_SOURCE = Path(__file__).resolve().parents[2] / "configs/fixed_v7_laptop_adaptation.json"


def _git_sha(value: str | None, label: str) -> str:
    if value is None or _GIT_SHA.fullmatch(value) is None:
        raise ArtifactError(f"{label} must be a 40-character lowercase Git SHA")
    return value


def _sequence_row(
    *,
    sample_id: str,
    task_id: str,
    source: str,
    scenario: str,
    kind: str,
    system: str,
    turns: list[tuple[str, str]],
) -> dict[str, Any]:
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for user, assistant in turns:
        messages.extend(
            [
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            ]
        )
    return {
        "schema": SFT_SOURCE_SCHEMA,
        "sample_id": sample_id,
        "task_id": task_id,
        "source": source,
        "scenario": scenario,
        "messages": messages,
        "tools": [],
        "kind": kind,
    }


def _result_user(
    *,
    instruction: str,
    origin: str,
    page: int,
    total: int,
    page_size: int,
    cards: list[Mapping[str, Any]],
    complete: bool,
    arguments: Mapping[str, Any] | None = None,
) -> str:
    seen = min(total, page * page_size)
    lines = [
        f'[{page * 1000 + offset}]<a href="/dp/{card["id"]}">{card["title"]}</a>'
        f" — ${float(card.get('price', 0)):.2f} — rating {card.get('rating', 'not shown')}"
        for offset, card in enumerate(cards)
    ]
    if len({str(card["id"]) for card in cards}) != len(cards):
        raise ArtifactError("fixed-v7 result page repeats a literal card identity")
    tail = (
        "Coverage is complete; every advertised card has a literal href in this browser "
        "history. The proposed candidate's PDP receipt is retained in history. "
        f"Grounded candidate frontier: {canonical_json(arguments['candidates'])}."
        if complete and arguments is not None
        else f"[{page * 1000 + 999}]<button>Next</button>"
    )
    return (
        f"<user_request>{instruction}</user_request>\n"
        f"Current URL: {origin}/s?q=laptop&min_rating=4&max_price=1000&page={page}\n"
        f"Rendered result count: {total} results. Page {page}/{math.ceil(total / page_size)}; "
        f"page_size={page_size}.\n"
        f"Literal-card ledger: cards_seen={seen}/{total}; cards_remaining={total - seen}.\n"
        + "\n".join(lines)
        + "\n"
        + tail
    )


def _sequence_rows(
    *,
    task_id: str,
    source: str,
    scenario: str,
    system: str,
    instruction: str,
    origin: str,
    total: int,
    page_size: int,
    selected: Mapping[str, Any],
    extra: Mapping[str, Any],
    search_cards: list[Mapping[str, Any]],
    arguments: Mapping[str, Any],
    prefix: str,
    output_model: type[Any],
) -> dict[str, dict[str, Any]]:
    selected_id, title = str(selected["id"]), str(selected["title"])
    extra_id = str(extra["id"])
    price = float(selected.get("price", 380.0))
    if total != len(search_cards) or total <= page_size:
        raise ArtifactError("fixed-v7 search-card evidence does not match advertised coverage")
    card_ids = [str(card["id"]) for card in search_cards]
    if len(set(card_ids)) != total or card_ids.count(selected_id) != 1:
        raise ArtifactError("fixed-v7 search-card identities are incomplete or ambiguous")
    pages = [search_cards[offset : offset + page_size] for offset in range(0, total, page_size)]
    selected_position = card_ids.index(selected_id)
    selected_page = selected_position // page_size + 1
    selected_offset = selected_position % page_size

    def page_state(page: int) -> str:
        return _result_user(
            instruction=instruction,
            origin=origin,
            page=page,
            total=total,
            page_size=page_size,
            cards=pages[page - 1],
            complete=page == len(pages),
            arguments=arguments if page == len(pages) else None,
        )

    pdp = (
        f"<user_request>{instruction}</user_request>\nCurrent URL: {origin}/dp/{selected_id}\n"
        f"Product ID {selected_id}; {title}; public details: {canonical_json(dict(selected))}.\n"
        "The literal ID matches the href just opened."
    )
    discovery_turns: list[tuple[str, str]] = []
    for page in range(1, len(pages) + 1):
        state = page_state(page)
        if page == selected_page:
            discovery_turns.append(
                (
                    state,
                    _agent_output(
                        output_model,
                        thinking=f"The literal href identifies promising {selected_id}.",
                        evaluation="The candidate needs PDP evidence, but coverage is incomplete.",
                        memory=(
                            f"Open exact /dp/{selected_id}; still continue through page "
                            f"{len(pages)}."
                        ),
                        next_goal=(
                            "Inspect the promising PDP without treating it as search completion."
                        ),
                        action={"click": {"index": page * 1000 + selected_offset}},
                    ),
                )
            )
            discovery_turns.append(
                (
                    pdp,
                    _agent_output(
                        output_model,
                        thinking="Record the PDP facts, then resume the same paginated frontier.",
                        evaluation=(
                            "Candidate evidence is grounded; advertised coverage is not done."
                        ),
                        memory=f"Preserve exact {selected_id}; continue through page {len(pages)}.",
                        next_goal="Return to the result page and continue the literal-card ledger.",
                        action={"go_back": {"description": "Return to the same result page"}},
                    ),
                )
            )
            state = page_state(page)
        if page < len(pages):
            seen = min(total, page * page_size)
            discovery_turns.append(
                (
                    state,
                    _agent_output(
                        output_model,
                        thinking=f"Only {seen}/{total} literal cards are evidenced; continue.",
                        evaluation=f"Page {page}/{len(pages)} is not exhaustive.",
                        memory=(
                            f"Literal cards evidenced {seen}/{total}; preserve {selected_id} "
                            "if seen."
                        ),
                        next_goal=(
                            f"Open page {page + 1} and account for every newly rendered href."
                        ),
                        action={"click": {"index": page * 1000 + 999}},
                    ),
                )
            )
        else:
            discovery_turns.append(
                (
                    state,
                    _agent_output(
                        output_model,
                        thinking=(
                            "Every advertised card is now literally evidenced; checkpoint now."
                        ),
                        evaluation=(
                            f"All {total}/{total} href identities were rendered before deciding."
                        ),
                        memory=f"Complete coverage; grounded proposed candidate {selected_id}.",
                        next_goal="Submit one decision checkpoint before any purchase.",
                        action={"decision_checkpoint": copy.deepcopy(dict(arguments))},
                    ),
                )
            )

    approved_pdp = (
        f"<user_request>{instruction}</user_request>\nDecision checkpoint approved {selected_id}.\n"
        f"Current URL: {origin}/dp/{selected_id}\n"
        f"[2100]<button>Buy Now — {title} ({selected_id})</button>"
    )
    checkout_dirty = (
        f"<user_request>{instruction}</user_request>\nCurrent URL: {origin}/gp/buy/spc\n"
        f"Review order: {title} ({selected_id}) ${price:.2f}; ADDON-PLAN $120.00; "
        f"accidental extra {extra_id} $240.00. Subtotal ${price + 360:.2f}.\n"
        '[2200]<a href="/gp/cart">Review cart</a> [2201]<button>Place your order</button>'
    )
    cart_dirty = (
        f"<user_request>{instruction}</user_request>\nCurrent URL: {origin}/gp/cart\n"
        f"Requested line {selected_id} quantity 1.\n"
        "[2300]<button>Remove ADDON-PLAN</button> Unrequested protection plan $120.00.\n"
        f"[2301]<button>Remove {extra_id}</button> Accidental extra product $240.00."
    )
    cart_extra = (
        f"<user_request>{instruction}</user_request>\nCurrent URL: {origin}/gp/cart\n"
        f"Requested line {selected_id} quantity 1. ADDON-PLAN removed.\n"
        f"[2301]<button>Remove {extra_id}</button> Accidental extra product $240.00."
    )
    cart_clean = (
        f"<user_request>{instruction}</user_request>\nCurrent URL: {origin}/gp/cart\n"
        f"Only requested line {selected_id}; quantity 1; subtotal ${price:.2f}; "
        "no plan, add-on, or extra item. [2400]<button>Proceed to checkout</button>"
    )
    checkout_clean = (
        f"<user_request>{instruction}</user_request>\nCurrent URL: {origin}/gp/buy/spc\n"
        f"Final review: only {selected_id}; quantity 1; subtotal ${price:.2f}; no add-ons. "
        "[2500]<button>Place your order</button>"
    )
    cart_turns = [
        (
            approved_pdp,
            _agent_output(
                output_model,
                thinking=(
                    "The approved literal identity is visible; Buy Now is allowed, but checkout "
                    "still needs reconciliation."
                ),
                evaluation="Checkpoint approval and PDP identity match.",
                memory=f"Approved {selected_id}; audit every checkout/cart line before ordering.",
                next_goal="Open checkout, then inspect all items and total.",
                action={"click": {"index": 2100}},
            ),
        ),
        (
            checkout_dirty,
            _agent_output(
                output_model,
                thinking="The total exposes an add-on and extra item; placing now is unsafe.",
                evaluation="Checkout is dirty and not authorized for final order.",
                memory=f"Keep one {selected_id}; remove ADDON-PLAN and {extra_id}.",
                next_goal="Open the cart and reconcile every line.",
                action={"click": {"index": 2200}},
            ),
        ),
        (
            cart_dirty,
            _agent_output(
                output_model,
                thinking="Remove the unrequested protection plan first and re-read the cart.",
                evaluation="Two unrequested lines remain.",
                memory=f"Keep {selected_id}; remove plan, then extra {extra_id}.",
                next_goal="Remove ADDON-PLAN and verify the resulting cart.",
                action={"click": {"index": 2300}},
            ),
        ),
        (
            cart_extra,
            _agent_output(
                output_model,
                thinking="The plan is gone but the accidental second product remains.",
                evaluation="Quantity and line-item audit still fails.",
                memory=f"Keep one {selected_id}; remove remaining extra {extra_id}.",
                next_goal="Remove the extra product and inspect the cart again.",
                action={"click": {"index": 2301}},
            ),
        ),
        (
            cart_clean,
            _agent_output(
                output_model,
                thinking=(
                    "The cart now contains exactly one approved item and the subtotal reconciles."
                ),
                evaluation="Cart line, quantity, add-on, and subtotal checks pass.",
                memory=f"Clean cart: one {selected_id}, no add-ons or extras.",
                next_goal="Return to checkout and perform one final review.",
                action={"click": {"index": 2400}},
            ),
        ),
        (
            checkout_clean,
            _agent_output(
                output_model,
                thinking="The final checkout exactly matches the approved single-item request.",
                evaluation="Final identity, quantity, lines, and subtotal are reconciled.",
                memory=f"Authorized final order: one {selected_id} only.",
                next_goal="Place the verified order.",
                action={"click": {"index": 2500}},
            ),
        ),
    ]

    recovery_url = f"{origin}/s?q=laptop&min_rating=4&max_price=1000&page=1"
    recovery_turns = [
        (
            f"<user_request>{instruction}</user_request>\nCurrent URL: about:blank after an "
            f"external search. Durable assigned origin: {origin}; exact recovery URL: "
            f"{recovery_url}. Do not invent a public hostname or port.",
            _agent_output(
                output_model,
                thinking=(
                    "The external/blank state is wrong; durable memory has the exact assigned "
                    "localhost URL."
                ),
                evaluation="The assigned storefront was lost, but recovery evidence is exact.",
                memory=f"Recover only {recovery_url}.",
                next_goal="Return to the exact assigned result page.",
                action={
                    "navigate": {
                        "url": recovery_url,
                        "new_tab": False,
                    }
                },
            ),
        ),
    ] + discovery_turns
    preservation_turns = [
        discovery_turns[-1],
        cart_turns[0],
        (
            f"<user_request>{instruction}</user_request>\nCurrent URL: {origin}/gp/buy/spc\n"
            f"Final review: only {selected_id}; quantity 1; subtotal ${price:.2f}; no add-ons. "
            "[2500]<button>Place your order</button>",
            cart_turns[-1][1],
        ),
    ]
    result: dict[str, dict[str, Any]] = {}
    for suffix, turns in (
        ("discovery-sequence", discovery_turns),
        ("cart-sequence", cart_turns),
        ("recovery-sequence", recovery_turns),
        ("preservation-sequence", preservation_turns),
    ):
        kind = f"{prefix}-{suffix}"
        result[kind] = _sequence_row(
            sample_id=f"fixed-v7:{source}:{task_id}:{kind}",
            task_id=task_id,
            source=source,
            scenario=scenario,
            kind=kind,
            system=system,
            turns=turns,
        )
    return result


def _caveat_shop_contract(variant: Mapping[str, Any]) -> dict[str, Any]:
    descriptions = {
        "price": ("Price under $1000", "lt", 1000, "$"),
        "ssd_storage": ("SSD storage at least 512GB", "ge", 512, "GB"),
        "weight": ("Weight at most 1.45kg", "le", 1.45, "kg"),
        "customer_rating": ("Customer rating at least 4 stars", "ge", 4, "stars"),
        "screen_brightness": ("Screen brightness at least 250nits", "ge", 250, "nits"),
        "laptop_type": ("Must not be a gaming laptop", "eq", False, None),
    }
    objective_units = {
        "weight": "kg",
        "battery_life": "hours",
        "customer_rating": "stars",
        "screen_brightness": "nits",
    }
    constraints = []
    for name in variant["constraints"]:
        description, operator, expected, unit = descriptions[name]
        constraints.append(
            {
                "criterion_id": name,
                "description": description,
                "operator": operator,
                "expected": expected,
                "unit": unit,
            }
        )
    objectives = [
        {
            "criterion_id": name,
            "description": name.replace("_", " "),
            "direction": "minimize" if name == "weight" else "maximize",
            "priority": None,
            "unit": objective_units[name],
            "weight": None,
        }
        for name in variant["objectives"]
    ]
    return {
        "constraints": constraints,
        "objectives": objectives,
        "search_mode": "best_available",
    }


def _caveat_shop_checkpoint(
    variant: Mapping[str, Any],
    selected: Mapping[str, Any],
    *,
    total: int,
    origin: str,
) -> dict[str, Any]:
    contract = _caveat_shop_contract(variant)
    values = {
        "price": selected["price"],
        "ssd_storage": selected["storage_gb"],
        "weight": selected["weight_kg"],
        "customer_rating": selected["rating"],
        "screen_brightness": selected["brightness_nits"],
        "laptop_type": selected["gaming"],
        "battery_life": selected["battery_hours"],
    }
    units = {
        row["criterion_id"]: row.get("unit")
        for row in contract["constraints"] + contract["objectives"]
    }
    facts = [
        {"criterion_id": name, "state": "known", "value": values[name], "unit": units[name]}
        for name in [
            row["criterion_id"] for row in contract["constraints"] + contract["objectives"]
        ]
    ]
    selected_id = str(selected["id"])
    return {
        "frontier": {
            "inspected_count": total,
            "advertised_count": total,
            "coverage_mode": "advertised_total",
            "advertised_page_count": None,
            "enumerated_page_count": None,
            "excluded_count": total - 1,
            "unresolved_count": 0,
            "exhausted": True,
            "basis": f"{total} results",
        },
        "candidates": [
            {
                "id": selected_id,
                "label": selected["title"],
                "source_url": f"{origin}/dp/{selected_id}",
                "facts": facts,
            }
        ],
        "proposed_candidate_id": selected_id,
    }


def _validate_adaptation_row(row: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    messages = row.get("messages")
    if row.get("schema") != SFT_SOURCE_SCHEMA or not isinstance(messages, list):
        raise ArtifactError("fixed-v7 CAVEAT-Shop adaptation row is malformed")
    if len(messages) < 7 or messages[-1].get("role") != "assistant":
        raise ArtifactError("fixed-v7 CAVEAT-Shop row is not a multi-turn sequence")
    _scan_visible({"messages": messages, "tools": row.get("tools", [])})
    return {
        "messages": list(messages),
        "metadata": {
            "schema": "caveat-27b.sft-row-metadata.v1",
            "sample_id": str(row["sample_id"]),
            "task_id": str(row["task_id"]),
            "source": str(row["source"]),
            "scenario": str(row["scenario"]),
            "stage": kind,
        },
    }


def _audit_coverage(messages: list[dict[str, Any]], *, kind: str) -> dict[str, Any]:
    page_to_ids: dict[int, tuple[str, ...]] = {}
    seen_ids: set[str] = set()
    pdp_ids: set[str] = set()
    totals: set[int] = set()
    page_sizes: set[int] = set()
    checkpoint_ids: list[str] = []
    origins: set[str] = set()
    recovery_url: str | None = None
    navigate_url: str | None = None
    for index, message in enumerate(messages):
        content = str(message.get("content", ""))
        if message.get("role") == "user":
            for origin in re.findall(r"Current URL: (https?://[^/\s]+)", content):
                origins.add(origin)
            assigned = re.search(
                r"Durable assigned origin: (https?://[^;\s]+); exact recovery URL: ([^\s]+)",
                content,
            )
            if assigned is not None:
                origins.add(assigned.group(1))
                recovery_url = assigned.group(2).rstrip(".")
            pdp = re.search(r"Current URL: https?://[^/\s]+/dp/([^\s]+)", content)
            if pdp is not None:
                if pdp.group(1) not in seen_ids:
                    raise ArtifactError("fixed-v7 PDP was opened before its literal card appeared")
                pdp_ids.add(pdp.group(1))
            page_match = re.search(
                r"Current URL: https?://[^/\s]+/s\?[^\n]*[?&]page=(\d+)", content
            )
            if page_match is None:
                continue
            page = int(page_match.group(1))
            page_line = re.search(
                r"Rendered result count: (\d+) results\. Page (\d+)/(\d+); page_size=(\d+)",
                content,
            )
            ledger = re.search(r"Literal-card ledger: cards_seen=(\d+)/(\d+)", content)
            card_ids = tuple(re.findall(r'<a href="/dp/([^"/]+)">', content))
            if page_line is None or ledger is None or int(page_line.group(2)) != page:
                raise ArtifactError("fixed-v7 coverage state has no exact rendered ledger")
            total, page_count, page_size = (
                int(page_line.group(1)),
                int(page_line.group(3)),
                int(page_line.group(4)),
            )
            if page_count != math.ceil(total / page_size):
                raise ArtifactError("fixed-v7 rendered page count is arithmetically false")
            expected_cards = min(page_size, total - (page - 1) * page_size)
            if len(card_ids) != expected_cards or len(set(card_ids)) != len(card_ids):
                raise ArtifactError("fixed-v7 page does not show every newly counted literal href")
            if page in page_to_ids and page_to_ids[page] != card_ids:
                raise ArtifactError("fixed-v7 repeated page changed its literal identities")
            for old_page, old_ids in page_to_ids.items():
                if old_page != page and set(old_ids).intersection(card_ids):
                    raise ArtifactError("fixed-v7 literal card appears on multiple pages")
            page_to_ids[page] = card_ids
            seen_ids.update(card_ids)
            if int(ledger.group(1)) != len(seen_ids) or int(ledger.group(2)) != total:
                raise ArtifactError("fixed-v7 ledger claims more cards than browser history proves")
            totals.add(total)
            page_sizes.add(page_size)
        elif message.get("role") == "assistant":
            try:
                wire = json.loads(content)
            except json.JSONDecodeError as exc:
                raise ArtifactError(
                    "fixed-v7 assistant target is not exact AgentOutput JSON"
                ) from exc
            actions = wire.get("action") if isinstance(wire, Mapping) else None
            if (
                not isinstance(actions, list)
                or len(actions) != 1
                or not isinstance(actions[0], Mapping)
            ):
                raise ArtifactError("fixed-v7 assistant target must contain exactly one action")
            action = actions[0]
            if isinstance(action.get("navigate"), Mapping):
                navigate_url = str(action["navigate"].get("url", ""))
            checkpoint = action.get("decision_checkpoint")
            if isinstance(checkpoint, Mapping):
                checkpoint_ids.append(str(checkpoint.get("proposed_candidate_id", "")))
                if index != len(messages) - 1:
                    raise ArtifactError("fixed-v7 checkpoint occurs before the final transition")
    if len(totals) != 1 or len(page_sizes) != 1 or len(checkpoint_ids) != 1:
        raise ArtifactError(
            "fixed-v7 coverage sequence has inconsistent totals or checkpoint count"
        )
    total, page_size = next(iter(totals)), next(iter(page_sizes))
    page_count = math.ceil(total / page_size)
    if set(page_to_ids) != set(range(1, page_count + 1)) or len(seen_ids) != total:
        raise ArtifactError("fixed-v7 sequence does not traverse every advertised page")
    proposed = checkpoint_ids[0]
    if proposed not in seen_ids or proposed not in pdp_ids:
        raise ArtifactError("fixed-v7 checkpoint candidate lacks literal card and PDP evidence")
    if kind.startswith("caveat_shop-"):
        selected_page = next(page for page, ids in page_to_ids.items() if proposed in ids)
        if selected_page >= page_count or proposed in page_to_ids[page_count]:
            raise ArtifactError("fixed-v7 CAVEAT-Shop sequence does not continue after finding the hero")
        if len(origins) != 1 or not next(iter(origins)).startswith("http://127.0.0.1:"):
            raise ArtifactError("fixed-v7 CAVEAT-Shop sequence has an ambiguous assigned origin")
    if kind.endswith("recovery-sequence") and navigate_url != recovery_url:
        raise ArtifactError("fixed-v7 recovery action does not copy the exact visible assigned URL")
    return {
        "total": total,
        "page_size": page_size,
        "page_count": page_count,
        "evidenced_ids": len(seen_ids),
        "pdp_candidate": proposed,
        "origins": sorted(origins),
    }


def _v7_curriculum_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    assistant_turns: Counter[str] = Counter()
    coverage_audits: list[dict[str, Any]] = []
    caveat_shop_origins: set[str] = set()
    for row in rows:
        kind = str(row["metadata"]["stage"])
        counts[kind] += 1
        messages = row["messages"]
        roles = [message.get("role") for message in messages]
        if roles[0] != "system" or any(
            roles[index] != ("user" if index % 2 else "assistant") for index in range(1, len(roles))
        ):
            raise ArtifactError("fixed-v7 sequence roles are not alternating")
        assistant_turns[kind] += roles.count("assistant")
        dynamic = canonical_json(messages)
        if kind.endswith(("discovery-sequence", "recovery-sequence")):
            coverage = _audit_coverage(messages, kind=kind)
            coverage_audits.append({"sample_id": row["metadata"]["sample_id"], **coverage})
            if kind.startswith("caveat_shop-"):
                caveat_shop_origins.update(coverage["origins"])
        if kind.endswith("cart-sequence") and not all(
            marker in dynamic
            for marker in ("/gp/buy/spc", "/gp/cart", "ADDON-PLAN", "Place your order")
        ):
            raise ArtifactError("fixed-v7 cart sequence omits reconciliation")
        lowered = dynamic.lower()
        if re.search(r"\boffice[_ ]chair\b|\bmattress\b|\bbackpack\b|\btent\b", lowered) or any(
            value in lowered for value in ("hero_asin", "preservation_strict", "strict_binary")
        ):
            raise ArtifactError("fixed-v7 data contains forbidden category or evaluator leakage")
    if dict(counts) != _ROW_QUOTAS:
        raise ArtifactError("fixed-v7 exact sequence quotas drifted")
    if any(not (counts[kind] * 3 <= assistant_turns[kind] <= counts[kind] * 6) for kind in _KINDS):
        raise ArtifactError("fixed-v7 sequences must contain three to six assistant transitions")
    if len(caveat_shop_origins) < 8:
        raise ArtifactError("fixed-v7 CAVEAT-Shop rows do not diversify assigned localhost ports")
    return {
        "row_counts": dict(sorted(counts.items())),
        "assistant_turns": dict(sorted(assistant_turns.items())),
        "minimum_assistant_turns_per_sequence": 3,
        "maximum_assistant_turns_per_sequence": 6,
        "coverage_audits": coverage_audits,
        "distinct_caveat_shop_origins": sorted(caveat_shop_origins),
        "ledger_counts_derived_from_literal_history": True,
        "pagination_to_pdp_to_checkpoint_required": True,
        "buy_now_to_cart_cleanup_to_order_required": True,
        "exact_localhost_recovery_to_checkpoint_required": True,
        "hidden_target_fields_present": False,
        "non_laptop_caveat_shop_category_tokens_present": False,
        "gate_passed": True,
    }


def _materialize_curriculum(
    campaign: Campaign, *, campaign_root: Path, output: Path
) -> dict[str, Any]:
    split_path = campaign_root / "corpus/splits/manifest.json"
    rehearsal_path = campaign_root / "corpus/raw/rehearsal.jsonl"
    contract_path = campaign_root / "corpus/raw/contract.jsonl"
    split_manifest, membership = load_split_manifest(campaign, split_path)
    rehearsals = read_jsonl(rehearsal_path)
    contracts = read_jsonl(contract_path)
    contract_by_task: dict[str, dict[str, Any]] = {}
    for row in contracts:
        task_id = row.get("task_id")
        if isinstance(task_id, str) and str(row.get("sample_id", "")).endswith(":json-only"):
            contract_by_task[task_id] = row
    seed = int(campaign.campaign["seed"])
    eligible_rehearsals: list[dict[str, Any]] = []
    for row in rehearsals:
        _, public = _public_state(row)
        catalog = public.get("catalog")
        total = int(
            public.get("visible_total") or (len(catalog) if isinstance(catalog, list) else 0)
        )
        page_size = int(public.get("page_size") or total)
        if isinstance(catalog, list) and len(catalog) == total and 0 < page_size < total:
            eligible_rehearsals.append(row)
    selected = sorted(
        eligible_rehearsals,
        key=lambda row: _stable_key(seed + 61, str(row["task_id"])),
    )[:_PROCEDURAL_TASKS]
    if len(selected) != _PROCEDURAL_TASKS:
        raise ArtifactError("not enough procedural tasks for fixed-v7")
    system, output_model = _output_stack()
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    # Procedural/non-CAVEAT-Shop sequences preserve generality and provide a clean
    # contrast to the explicitly labeled laptop adaptation rows.
    procedural_bank: dict[str, list[tuple[Mapping[str, Any], dict[str, Any]]]] = {
        kind: [] for kind in _KINDS if kind.startswith("procedural-")
    }
    for source in selected:
        task_id = str(source.get("task_id"))
        contract = contract_by_task.get(task_id)
        if contract is None:
            raise ArtifactError("fixed-v7 task lacks contract replay")
        row_system = _system_with_contract(system, contract)
        _, state = _public_state(source)
        arguments = _checkpoint_arguments(source)
        catalog = state.get("catalog")
        if not isinstance(catalog, list) or len(catalog) < 2:
            raise ArtifactError("fixed-v7 procedural catalog is too small")
        selected_id = str(arguments["proposed_candidate_id"])
        by_id = {str(item["item_id"]): item for item in catalog}
        selected_item = by_id.get(selected_id)
        extra_item = next((item for item in catalog if str(item["item_id"]) != selected_id), None)
        if not isinstance(selected_item, Mapping) or not isinstance(extra_item, Mapping):
            raise ArtifactError("fixed-v7 procedural sequence lacks selected/extra item")
        procedural_cards = [
            {
                "id": str(item["item_id"]),
                "title": str(item.get("title", item["item_id"])),
                "price": item.get("price", 0),
                "rating": item.get("rating", "not shown"),
            }
            for item in catalog
        ]
        sequences = _sequence_rows(
            task_id=task_id,
            source=str(source["source"]),
            scenario=str(source["scenario"]),
            system=row_system,
            instruction=str(state["instruction"]),
            origin="https://shop.local",
            total=int(state.get("visible_total") or len(catalog)),
            page_size=max(1, int(state.get("page_size") or max(1, len(catalog) // 2))),
            selected={
                "id": selected_id,
                "title": selected_item.get("title", selected_id),
                "price": selected_item.get("price", 380),
            },
            extra={
                "id": str(extra_item["item_id"]),
                "title": extra_item.get("title", extra_item["item_id"]),
                "price": extra_item.get("price", 240),
            },
            search_cards=procedural_cards,
            arguments=arguments,
            prefix="procedural",
            output_model=output_model,
        )
        for kind, row in sequences.items():
            procedural_bank[kind].append((source, row))
    for kind, bank in procedural_bank.items():
        quota = _ROW_QUOTAS[kind]
        ordered = sorted(
            bank,
            key=lambda item: _stable_key(seed + 72, f"{kind}:{item[0]['task_id']}"),
        )[:quota]
        if len(ordered) != quota:
            raise ArtifactError(f"fixed-v7 procedural bank is short for {kind}")
        for _source, row in ordered:
            rows.append(
                validate_sft_sample(
                    row,
                    membership=membership,
                    expected_stage=kind,
                    row_number=counts[kind] + 1,
                )
            )
            counts[kind] += 1

    # Released laptop development histories are intentionally used here.  This
    # is same-task adaptation; the source manifest records every consulted
    # trajectory hash, and held-out category data is prohibited by audit.
    caveat_shop_source = read_json(_CAVEAT_SHOP_SOURCE)
    if (
        not isinstance(caveat_shop_source, Mapping)
        or caveat_shop_source.get("schema") != "caveat-27b.fixed-v7-laptop-development-source.v1"
        or caveat_shop_source.get("scientific_label") != "same_task_laptop_development_adaptation"
        or len(caveat_shop_source.get("trajectory_sha256", [])) != 12
    ):
        raise ArtifactError("fixed-v7 laptop adaptation source is incompatible")
    variants = caveat_shop_source.get("laptop_public_task_variants")
    catalog = caveat_shop_source.get("public_catalog_subset")
    search_cards = caveat_shop_source.get("public_search_cards")
    if (
        not isinstance(variants, list)
        or len(variants) != 4
        or not isinstance(catalog, list)
        or not isinstance(search_cards, list)
        or len(search_cards) != 37
    ):
        raise ArtifactError("fixed-v7 laptop adaptation public source is malformed")
    by_id = {str(item["id"]): item for item in catalog if isinstance(item, Mapping)}
    selected_item = by_id.get("EXP-LAPTOP-50")
    extras = [
        by_id[item_id]
        for item_id in ("EXP-LAPTOP-42", "EXP-LAPTOP-57", "EXP-LAPTOP-67", "EXP-LAPTOP-23")
    ]
    if not isinstance(selected_item, Mapping):
        raise ArtifactError("fixed-v7 laptop public source omits the selected item")
    for kind in (item for item in _KINDS if item.startswith("caveat_shop-")):
        quota = _ROW_QUOTAS[kind]
        for index in range(quota):
            variant = variants[index % len(variants)]
            origin = f"http://127.0.0.1:{30401 + (index % 12)}"
            contract = _caveat_shop_contract(variant)
            contract_row = {
                "messages": [{"role": "assistant", "content": canonical_json(contract)}]
            }
            row_system = _system_with_contract(system, contract_row)
            task_identity = f"{variant['task_id']}-adapt-r{index:02d}"
            sequences = _sequence_rows(
                task_id=task_identity,
                source="caveat_shop_laptop_development",
                scenario="caveat_shop_laptop_same_task_adaptation",
                system=row_system,
                instruction=str(variant["instruction"]),
                origin=origin,
                total=37,
                page_size=18,
                selected=selected_item,
                extra=extras[index % len(extras)],
                search_cards=search_cards,
                arguments=_caveat_shop_checkpoint(variant, selected_item, total=37, origin=origin),
                prefix="caveat_shop",
                output_model=output_model,
            )
            row = sequences[kind]
            rows.append(_validate_adaptation_row(row, kind=kind))
            counts[kind] += 1

    rows.sort(key=lambda row: _stable_key(seed + 62, str(row["metadata"]["sample_id"])))
    audit = _v7_curriculum_audit(rows)
    data = publish_jsonl(output / "train.jsonl", rows)
    body = {
        "schema": FIXED_V7_CURRICULUM_SCHEMA,
        "stage": "refinement",
        "method": "multi_turn_browser_history_laptop_adaptation_sft",
        "scientific_label": "same_task_laptop_development_adaptation",
        "campaign_digest": campaign.digest,
        "split_manifest_sha256": sha256_file(split_path),
        "split_manifest_body_sha256": split_manifest["manifest_body_sha256"],
        "inputs": {
            "rehearsal": {"path": str(rehearsal_path), "sha256": sha256_file(rehearsal_path)},
            "contract": {"path": str(contract_path), "sha256": sha256_file(contract_path)},
            "laptop_development_source": {
                "path": str(_CAVEAT_SHOP_SOURCE),
                "sha256": sha256_file(_CAVEAT_SHOP_SOURCE),
                "trajectory_sha256": caveat_shop_source["trajectory_sha256"],
                "source_report": caveat_shop_source["source_report"],
            },
        },
        "counts": dict(sorted(counts.items())),
        "policy": {
            "procedural_train_tasks": _PROCEDURAL_TASKS,
            "row_quotas": _ROW_QUOTAS,
            "sequence_level_browser_history": True,
            "all_assistant_turns_trainable": True,
            "literal_identity_required": True,
            "dirty_cart_reconciliation_required": True,
            "caveat_shop_laptop_development_trajectories_consulted": True,
            "caveat_shop_laptop_public_histories_used_for_training": True,
            "later_laptop_evaluation_label": "same_task_adaptation_only",
            "office_chair_or_other_caveat_shop_categories_consulted": False,
            "candidate_sweep": False,
        },
        "assistant_wire_format": "browser-use AgentOutput.action JSON",
        "heldout_non_laptop_caveat_shop_scenarios_present": False,
        "curriculum_audit": audit,
        "output": {"path": data.name, "sha256": sha256_file(data), "rows": len(rows)},
    }
    manifest = dict(body)
    manifest["manifest_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    publish_json(output / "manifest.json", manifest)
    return manifest


def _fixed_toml(
    *,
    model: Path,
    dataset: Path,
    output: Path,
    targets: list[str],
    settings: Mapping[str, Any],
    seed: int,
    num_gpus: int,
) -> bytes:
    lines = [
        "# PRIME-RL 0.7.0; fixed-v7 one-candidate continuation",
        f"max_steps = {_FINAL_STEP}",
        f"output_dir = {_quoted(output)}",
        "clean_output_dir = false",
        'matmul_precision = "high"',
        'loss_impl = "liger_fused"',
        "",
        "[env_vars]",
        'FLA_TILELANG = "0"',
        'WANDB_MODE = "disabled"',
        "",
        "[deployment]",
        'type = "single_node"',
        f"num_gpus = {num_gpus}",
        f"gpus_per_node = {num_gpus}",
        "",
        "[model]",
        f"name = {_quoted(model)}",
        f"seq_len = {int(settings['sequence_length'])}",
        'impl = "hf"',
        'attn = "flash_attention_2"',
        'optimization_dtype = "bfloat16"',
        'reduce_dtype = "bfloat16"',
        "cp = 2",
        'cp_style = "ulysses"',
        "",
        "[model.ac]",
        'mode = "full"',
        "freq = 1",
        "",
        "[model.lora]",
        f"rank = {int(settings['lora_rank'])}",
        f"alpha = {float(settings['lora_alpha'])}",
        f"dropout = {float(settings['lora_dropout'])}",
        f"target_modules = {_array(targets)}",
        "modules_to_save = []",
        "",
        "[renderer]",
        'name = "qwen3.5"',
        "enable_thinking = true",
        "",
        "[data]",
        'type = "sft"',
        f"name = {_quoted(dataset)}",
        f"batch_size = {int(settings['global_batch_size'])}",
        f"seq_len = {int(settings['sequence_length'])}",
        "micro_batch_size = 1",
        'pack_function = "cat"',
        "shuffle = true",
        f"seed = {seed}",
        "",
        "[data.loss_mask]",
        "system = false",
        "user = false",
        "assistant = true",
        "tool = false",
        "",
        "[optim]",
        'type = "adamw"',
        f"lr = {_LEARNING_RATE}",
        "weight_decay = 0.01",
        f"max_norm = {float(settings.get('maximum_gradient_norm', 1.0))}",
        "",
        "[scheduler]",
        'type = "cosine"',
        "warmup_steps = 0",
        f"min_lr = {_LEARNING_RATE}",
        "",
        "[ckpt]",
        f"interval = {_NEW_UPDATES}",
        f"resume_step = {_SOURCE_STEP}",
        "keep_last = 2",
        "skip_optimizer = true",
        "skip_scheduler = true",
        "skip_dataloader = true",
        "skip_progress = false",
        "",
        "[ckpt.weights]",
        "save_sharded = true",
        'save_format = "safetensors"',
        "save_adapter_separately = true",
    ]
    payload = ("\n".join(lines).rstrip() + "\n").encode()
    parsed = tomllib.loads(payload.decode())
    if parsed["scheduler"] != {"type": "cosine", "warmup_steps": 0, "min_lr": _LEARNING_RATE}:
        raise AssertionError("fixed-v7 constant LR policy drifted")
    return payload


def _token_mix_audit(stage: Path) -> dict[str, Any]:
    rows = read_jsonl(stage / "curriculum/train.jsonl")
    audit_rows = read_jsonl(stage / "prime/token_audit_rows.jsonl")
    if len(rows) != len(audit_rows):
        raise ArtifactError("fixed-v7 token audit row count differs")
    totals: Counter[str] = Counter()
    for index, (row, audit) in enumerate(zip(rows, audit_rows, strict=True), 1):
        if audit.get("line") != index or type(audit.get("rendered_tokens")) is not int:
            raise ArtifactError("fixed-v7 token audit ordering drifted")
        kind = row.get("metadata", {}).get("stage")
        if kind not in _KINDS:
            raise ArtifactError("fixed-v7 row stage drifted")
        totals[str(kind)] += int(audit["rendered_tokens"])
    total = sum(totals.values())
    if total <= 0:
        raise ArtifactError("fixed-v7 token audit is empty")
    if set(totals) != set(_KINDS) or any(totals[kind] <= 0 for kind in _KINDS):
        raise ArtifactError("fixed-v7 token audit omits a frozen sequence kind")
    fractions = {kind: totals[kind] / total for kind in _KINDS}
    grouped = {
        "caveat_shop_laptop_adaptation": sum(
            fractions[kind] for kind in _KINDS if kind.startswith("caveat_shop-")
        ),
        "procedural_rehearsal": sum(
            fractions[kind] for kind in _KINDS if kind.startswith("procedural-")
        ),
        "discovery": sum(fractions[kind] for kind in _KINDS if "discovery" in kind),
        "cart_reconciliation": sum(fractions[kind] for kind in _KINDS if "cart" in kind),
        "origin_recovery": sum(fractions[kind] for kind in _KINDS if "recovery" in kind),
        "ordinary_preservation": sum(fractions[kind] for kind in _KINDS if "preservation" in kind),
    }
    # Sequence lengths intentionally differ, so this gate prevents source or
    # behavior collapse without pretending row quotas are token quotas.
    bounds = {
        "caveat_shop_laptop_adaptation": [0.35, 0.80],
        "procedural_rehearsal": [0.20, 0.65],
        "discovery": [0.12, 0.42],
        "cart_reconciliation": [0.25, 0.65],
        "origin_recovery": [0.08, 0.32],
        "ordinary_preservation": [0.05, 0.25],
    }
    if any(not (bounds[name][0] <= value <= bounds[name][1]) for name, value in grouped.items()):
        raise ArtifactError("fixed-v7 exact rendered-token mixture is outside frozen bounds")
    return {
        "rendered_tokens": total,
        "rendered_tokens_by_stage": dict(sorted(totals.items())),
        "fractions": fractions,
        "grouped_fractions": grouped,
        "frozen_bounds": bounds,
        "gate_passed": True,
    }


def _hardlink_tree(source: Path, destination: Path) -> dict[str, Any]:
    if destination.exists() or destination.is_symlink():
        raise ArtifactError("fixed-v7 DCP destination already exists")
    source_identity = _tree_identity(source)
    destination.mkdir(parents=True)
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        target = destination / rel
        if path.is_symlink():
            raise ArtifactError("fixed-v7 DCP source contains symlink")
        if path.is_dir():
            target.mkdir(exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(path, target)
        else:
            raise ArtifactError("fixed-v7 DCP source contains special entry")
    copied = _tree_identity(destination)
    if any(copied[k] != source_identity[k] for k in ("files", "bytes", "tree_sha256")):
        raise ArtifactError("fixed-v7 hardlinked DCP identity drifted")
    inventory: list[dict[str, Any]] = []
    for path in sorted(source.rglob("*")):
        if path.is_file():
            target = destination / path.relative_to(source)
            a, b = path.stat(), target.stat()
            if a.st_dev != b.st_dev or a.st_ino != b.st_ino:
                raise ArtifactError("fixed-v7 DCP file is not an exact hardlink")
            inventory.append(
                {
                    "relative_path": path.relative_to(source).as_posix(),
                    "device": a.st_dev,
                    "inode": a.st_ino,
                    "bytes": a.st_size,
                    "link_count_at_preparation": a.st_nlink,
                }
            )
    return {
        **copied,
        "hardlink_verified": True,
        "hardlink_inventory": inventory,
    }


def prepare_browser_action_fixed_v7(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    output_dir: str | Path,
    num_gpus: int = 4,
) -> dict[str, Any]:
    if num_gpus != 4:
        raise ArtifactError("fixed-v7 uses four GPUs")
    root = Path(campaign_root).resolve()
    campaign_sha = _git_sha(root.name, "campaign artifact source Git SHA")
    output = Path(output_dir).absolute()
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise ArtifactError("fixed-v7 output must be new and empty")
    output = output.resolve()
    if output.parent != root / "browser_action_fixed_v7" or _GIT_SHA.fullmatch(output.name) is None:
        raise ArtifactError("fixed-v7 output path is invalid")
    curriculum = _materialize_curriculum(campaign, campaign_root=root, output=output / "curriculum")
    prime = materialize_prime_dataset(
        campaign,
        source_jsonl=output / "curriculum/train.jsonl",
        source_manifest=output / "curriculum/manifest.json",
        smoke_report=root / "smoke/smoke_report.json",
        stage="refinement",
        output_dir=output / "prime",
        prime_root=os.environ.get("CAVEAT_27B_PRIME_ROOT", "/opt/prime-rl"),
    )
    mix = _token_mix_audit(output)
    source = _verified_source(campaign, root)
    prep, post = read_json(root / "prep_receipt.json"), read_json(root / "post_sft_receipt.json")
    if (
        not isinstance(prep, dict)
        or prep.get("source_git_sha") != campaign_sha
        or not isinstance(post, dict)
        or post.get("artifact_source_git_sha") != campaign_sha
    ):
        raise ArtifactError("fixed-v7 campaign identity drifted")
    _, targets, revision = _smoke_targets(root / "smoke/smoke_report.json", campaign)
    if revision and revision != campaign.model["revision"]:
        raise ArtifactError("fixed-v7 model revision drifted")
    training = output / "training"
    linked = _hardlink_tree(source["source_dcp"], training / "prime_output/checkpoints/step_20")
    config_path = publish_bytes(
        training / "browser_action_fixed_v7.toml",
        _fixed_toml(
            model=source["parent"],
            dataset=(output / "prime").resolve(),
            output=(training / "prime_output").resolve(),
            targets=targets,
            settings=campaign.campaign["refinement"],
            seed=int(campaign.campaign["seed"]) + 63,
            num_gpus=num_gpus,
        ),
    )
    plan = {
        "schema": FIXED_V7_PLAN_SCHEMA,
        "status": "prepared",
        "stage": "browser_action_fixed_v7",
        "campaign_digest": campaign.digest,
        "campaign_artifact_source_git_sha": campaign_sha,
        "artifact_source_git_sha": output.name,
        "parent_model": str(source["parent"]),
        "parent_merge_provenance_sha256": source["parent_merge_provenance_sha256"],
        "source_step20_adapter": source["adapter_identity"],
        "source_step20_adapter_receipt_tree_sha256": source["adapter_receipt_tree_sha256"],
        "source_step20_dcp": source["source_dcp_identity"],
        "linked_step20_dcp": linked,
        "curriculum_manifest_sha256": sha256_file(output / "curriculum/manifest.json"),
        "curriculum_data_sha256": sha256_file(output / "curriculum/train.jsonl"),
        "prime_manifest_sha256": sha256_file(output / "prime/manifest.json"),
        "prime_parquet_sha256": sha256_file(output / "prime/train.parquet"),
        "config": str(config_path),
        "config_sha256": sha256_file(config_path),
        "candidate": {
            "name": "step23",
            "update": _FINAL_STEP,
            "path": str((training / "prime_output/weights/step_23/lora_adapters").resolve()),
        },
        "selection_performed": False,
        "caveat_shop_outcomes_consulted": True,
        "scientific_label": "same_task_laptop_development_adaptation",
        "training_policy": {
            "optimizer": "adamw",
            "learning_rate": _LEARNING_RATE,
            "scheduler": "constant_cosine_floor",
            "warmup_steps": 0,
            "resume_step": _SOURCE_STEP,
            "new_optimizer_updates": _NEW_UPDATES,
            "optimizer_updates": _FINAL_STEP,
            "checkpoint_updates": [_FINAL_STEP],
            "num_gpus": num_gpus,
            "global_batch_size": int(campaign.campaign["refinement"]["global_batch_size"]),
            "sequence_length": int(campaign.campaign["refinement"]["sequence_length"]),
            "lora_rank": int(campaign.campaign["refinement"]["lora_rank"]),
            "lora_alpha": float(campaign.campaign["refinement"]["lora_alpha"]),
            "restore_model": True,
            "restore_progress": True,
            "restore_optimizer": False,
            "restore_scheduler": False,
            "restore_dataloader": False,
        },
        "curriculum": curriculum,
        "prime": {"rows": prime["row_count"], "token_audit": prime["token_audit"]},
        "token_mix_audit": mix,
        "original_refinement_mutated": False,
    }
    path = publish_json(training / "plan.json", plan)
    return {**plan, "plan_sha256": sha256_file(path)}


def write_browser_action_fixed_v7_receipt(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    stage_dir: str | Path,
    artifact_source_git_sha: str | None,
    execution_source_git_sha: str | None,
) -> dict[str, Any]:
    root, stage = Path(campaign_root).resolve(), Path(stage_dir).resolve()
    artifact_sha = _git_sha(artifact_source_git_sha, "artifact source Git SHA")
    execution_sha = _git_sha(execution_source_git_sha, "execution source Git SHA")
    if stage != root / "browser_action_fixed_v7" / artifact_sha:
        raise ArtifactError("fixed-v7 stage path drifted")
    training = stage / "training"
    plan_path, config_path = training / "plan.json", training / "browser_action_fixed_v7.toml"
    plan, source = read_json(plan_path), _verified_source(campaign, root)
    if (
        not isinstance(plan, dict)
        or plan.get("schema") != FIXED_V7_PLAN_SCHEMA
        or plan.get("status") != "prepared"
    ):
        raise ArtifactError("fixed-v7 plan is incompatible")
    if (
        plan.get("artifact_source_git_sha") != artifact_sha
        or plan.get("campaign_digest") != campaign.digest
        or plan.get("campaign_artifact_source_git_sha") != root.name
        or plan.get("parent_model") != str(source["parent"])
        or plan.get("parent_merge_provenance_sha256") != source["parent_merge_provenance_sha256"]
        or plan.get("source_step20_adapter_receipt_tree_sha256")
        != source["adapter_receipt_tree_sha256"]
        or plan.get("selection_performed") is not False
        or plan.get("caveat_shop_outcomes_consulted") is not True
        or plan.get("scientific_label") != "same_task_laptop_development_adaptation"
    ):
        raise ArtifactError("fixed-v7 plan binding drifted")
    if (
        plan.get("source_step20_dcp") != source["source_dcp_identity"]
        or plan.get("source_step20_adapter") != source["adapter_identity"]
    ):
        raise ArtifactError("fixed-v7 canonical step20 source drifted")
    linked = plan.get("linked_step20_dcp")
    inventory = linked.get("hardlink_inventory") if isinstance(linked, Mapping) else None
    if (
        not isinstance(linked, Mapping)
        or linked.get("hardlink_verified") is not True
        or linked.get("tree_sha256") != source["source_dcp_identity"]["tree_sha256"]
        or not isinstance(inventory, list)
        or len(inventory) != source["source_dcp_identity"]["files"]
        or any(
            not isinstance(item, Mapping)
            or not isinstance(item.get("relative_path"), str)
            or type(item.get("device")) is not int
            or type(item.get("inode")) is not int
            or type(item.get("bytes")) is not int
            or type(item.get("link_count_at_preparation")) is not int
            or item["link_count_at_preparation"] < 2
            for item in inventory
        )
        or len({str(item["relative_path"]) for item in inventory}) != len(inventory)
    ):
        raise ArtifactError("fixed-v7 hardlink preparation attestation drifted")
    staged_resume = training / "prime_output/checkpoints/step_20"
    if staged_resume.exists():
        if staged_resume.is_symlink() or _tree_identity(staged_resume) != {
            **source["source_dcp_identity"],
            "path": str(staged_resume.resolve()),
        }:
            raise ArtifactError("fixed-v7 retained resume DCP identity drifted")
        for item in inventory:
            source_path = source["source_dcp"] / str(item["relative_path"])
            staged_path = staged_resume / str(item["relative_path"])
            source_stat, staged_stat = source_path.stat(), staged_path.stat()
            if source_stat.st_dev != staged_stat.st_dev or source_stat.st_ino != staged_stat.st_ino:
                raise ArtifactError("fixed-v7 retained resume DCP is no longer hardlinked")
    # PRIME may prune the linked resume checkpoint. The immutable canonical source,
    # frozen plan identity, final adapter, and trainer config are authoritative.
    for path, expected in (
        (config_path, plan.get("config_sha256")),
        (stage / "curriculum/manifest.json", plan.get("curriculum_manifest_sha256")),
        (stage / "curriculum/train.jsonl", plan.get("curriculum_data_sha256")),
        (stage / "prime/manifest.json", plan.get("prime_manifest_sha256")),
        (stage / "prime/train.parquet", plan.get("prime_parquet_sha256")),
    ):
        if sha256_file(path) != expected:
            raise ArtifactError("fixed-v7 frozen training input drifted")
    config = tomllib.loads(config_path.read_text())
    expected_policy = {
        "optimizer": "adamw",
        "learning_rate": _LEARNING_RATE,
        "scheduler": "constant_cosine_floor",
        "warmup_steps": 0,
        "resume_step": _SOURCE_STEP,
        "new_optimizer_updates": _NEW_UPDATES,
        "optimizer_updates": _FINAL_STEP,
        "checkpoint_updates": [_FINAL_STEP],
        "num_gpus": 4,
        "global_batch_size": int(campaign.campaign["refinement"]["global_batch_size"]),
        "sequence_length": int(campaign.campaign["refinement"]["sequence_length"]),
        "lora_rank": int(campaign.campaign["refinement"]["lora_rank"]),
        "lora_alpha": float(campaign.campaign["refinement"]["lora_alpha"]),
        "restore_model": True,
        "restore_progress": True,
        "restore_optimizer": False,
        "restore_scheduler": False,
        "restore_dataloader": False,
    }
    if (
        config.get("max_steps") != _FINAL_STEP
        or config.get("deployment")
        != {
            "type": "single_node",
            "num_gpus": 4,
            "gpus_per_node": 4,
        }
        or config.get("scheduler")
        != {"type": "cosine", "warmup_steps": 0, "min_lr": _LEARNING_RATE}
        or config.get("ckpt", {}).get("interval") != _NEW_UPDATES
        or config.get("ckpt", {}).get("resume_step") != _SOURCE_STEP
        or config.get("ckpt", {}).get("keep_last") != 2
        or config.get("ckpt", {}).get("skip_optimizer") is not True
        or config.get("ckpt", {}).get("skip_scheduler") is not True
        or config.get("ckpt", {}).get("skip_dataloader") is not True
        or config.get("ckpt", {}).get("skip_progress") is not False
        or config.get("optim", {}).get("type") != "adamw"
        or config.get("optim", {}).get("lr") != _LEARNING_RATE
        or plan.get("training_policy") != expected_policy
    ):
        raise ArtifactError("fixed-v7 config drifted")
    candidate = _verify_adapter(
        adapter=training / "prime_output/weights/step_23/lora_adapters",
        parent=source["parent"],
        targets=list(config["model"]["lora"]["target_modules"]),
        update=_FINAL_STEP,
    )
    body = {
        "schema": FIXED_V7_RECEIPT_SCHEMA,
        "status": "ok",
        "stage": "browser_action_fixed_v7",
        "campaign_digest": campaign.digest,
        "campaign_artifact_source_git_sha": root.name,
        "artifact_source_git_sha": artifact_sha,
        "execution_source_git_sha": execution_sha,
        "parent_model": str(source["parent"]),
        "parent_merge_provenance_sha256": source["parent_merge_provenance_sha256"],
        "plan_path": str(plan_path),
        "plan_sha256": sha256_file(plan_path),
        "config_sha256": sha256_file(config_path),
        "curriculum_manifest_sha256": sha256_file(stage / "curriculum/manifest.json"),
        "curriculum_data_sha256": sha256_file(stage / "curriculum/train.jsonl"),
        "prime_manifest_sha256": sha256_file(stage / "prime/manifest.json"),
        "prime_parquet_sha256": sha256_file(stage / "prime/train.parquet"),
        "source_step20_adapter": source["adapter_identity"],
        "source_step20_dcp": source["source_dcp_identity"],
        "linked_step20_dcp_initial": plan["linked_step20_dcp"],
        "linked_resume_may_be_pruned": True,
        "optimizer_updates": _FINAL_STEP,
        "new_optimizer_updates": _NEW_UPDATES,
        "checkpoint_updates": [_FINAL_STEP],
        "training_policy": plan["training_policy"],
        "candidate": {"name": "step23", **candidate},
        "selection_performed": False,
        "caveat_shop_outcomes_consulted": True,
        "scientific_label": "same_task_laptop_development_adaptation",
        "office_chair_or_other_caveat_shop_categories_consulted": False,
        "original_refinement_unchanged_after_training": True,
    }
    receipt = dict(body)
    receipt["receipt_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    publish_json(training / "training_receipt.json", receipt)
    return receipt
