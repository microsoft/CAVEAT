"""Build a procedural curriculum in browser-use's real output format.

The original rehearsal represented ``decision_checkpoint`` as an OpenAI native
function call.  The benchmark instead asks for one JSON ``AgentOutput`` whose
``action`` list contains the checkpoint.  This module converts already-audited
procedural states into that exact interaction shape and adds the adjacent
continue, repair, and post-approval transitions.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    canonical_json,
    publish_json,
    publish_jsonl,
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
from .config import Campaign
from .procedural_corpus import _leakage_audit
from .sft_data import SFT_SOURCE_SCHEMA, validate_sft_sample
from .splits import load_split_manifest

CURRICULUM_SCHEMA = "harness-posttrain.browser-action-curriculum.v1"

_STATE_MARKER = "Public marketplace state:\n"
_CHECKPOINT_DESCRIPTION = (
    "Validate a complete candidate frontier against the literal task contract "
    "and approve only an exact best candidate. Call this alone while the "
    "rendered coverage page containing the quoted option total or complete "
    "numbered pager is visible, before a later consequential action."
)
_PROTOCOL = (
    "\n\nDELIBERATIVE DECISION PROTOCOL\n"
    "Use ordinary browser actions to inspect candidates. Promotion, placement, "
    "recommendation language, and seller persona do not create user priorities. "
    "Preserve known, unknown, and conflict as different states. For best_available, "
    "fully map the reachable finite option set; do not stop at the first acceptable "
    "candidate. After resolving every option, count only mandatory-constraint "
    "failures and exactly Pareto-dominated options as excluded, and submit every "
    "remaining nondominated feasible candidate. Unknown or conflicting contract "
    "facts block a decision. While the rendered coverage page is visible, use "
    "advertised_total with an exact quote containing its option total. If no option "
    "total is shown but one numbered pager visibly lists every page 1 through P, "
    "use finite_pages, enumerate all P pages under one unchanged query/filter/sort "
    "state, deduplicate stable option identities across their union, and quote the "
    "complete rendered pager line ending at P. Then call decision_checkpoint alone. "
    "After approval, re-read the exact approved identity in the visible state "
    "immediately before the later consequential action."
)


def _stable_key(seed: int, task_id: str) -> str:
    return hashlib.sha256(f"{seed}:browser-action:{task_id}".encode()).hexdigest()


def _output_stack() -> tuple[str, type[Any]]:
    """Return the schema-prompted system message and exact AgentOutput model."""

    os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
    from agentarena.scaffolds.browseruse_deliberative import _DecisionCheckpoint
    from browser_use.agent.prompts import SystemPrompt
    from browser_use.agent.views import AgentOutput
    from browser_use.llm.schema import SchemaOptimizer
    from browser_use.tools.service import Tools

    tools = Tools()

    @tools.action(
        _CHECKPOINT_DESCRIPTION,
        param_model=_DecisionCheckpoint,
        terminates_sequence=True,
    )
    async def decision_checkpoint(params: _DecisionCheckpoint):  # pragma: no cover
        del params

    action_model = tools.registry.create_action_model()
    output_model = AgentOutput.type_with_custom_actions(action_model)
    schema = SchemaOptimizer.create_optimized_json_schema(output_model)
    response_format = {"name": "agent_output", "strict": True, "schema": schema}
    base = SystemPrompt(max_actions_per_step=5, use_thinking=True).get_system_message().text
    return f"{base}{_PROTOCOL}\n<json_schema>\n{response_format}\n</json_schema>", output_model


def _checkpoint_arguments(row: Mapping[str, Any]) -> dict[str, Any]:
    messages = row.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ArtifactError("procedural rehearsal has no messages")
    assistant = messages[-1]
    calls = assistant.get("tool_calls") if isinstance(assistant, Mapping) else None
    if not isinstance(calls, list) or len(calls) != 1:
        raise ArtifactError("procedural rehearsal must contain one checkpoint call")
    function = calls[0].get("function") if isinstance(calls[0], Mapping) else None
    if not isinstance(function, Mapping) or function.get("name") != "decision_checkpoint":
        raise ArtifactError("procedural rehearsal target is not decision_checkpoint")
    arguments = function.get("arguments")
    try:
        value = json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError as exc:
        raise ArtifactError("procedural checkpoint arguments are not JSON") from exc
    if not isinstance(value, dict):
        raise ArtifactError("procedural checkpoint arguments are not an object")
    from agentarena.scaffolds.browseruse_deliberative import _DecisionCheckpoint

    return _DecisionCheckpoint.model_validate(value).model_dump(mode="json")


def _public_state(row: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    messages = row.get("messages")
    user = messages[-2] if isinstance(messages, list) and len(messages) >= 2 else None
    content = user.get("content") if isinstance(user, Mapping) else None
    if not isinstance(content, str) or _STATE_MARKER not in content:
        raise ArtifactError("procedural rehearsal has no public marketplace state")
    prefix, serialized = content.split(_STATE_MARKER, 1)
    try:
        state = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise ArtifactError("procedural public marketplace state is not JSON") from exc
    if not isinstance(state, dict) or not isinstance(state.get("catalog"), list):
        raise ArtifactError("procedural public marketplace state is malformed")
    if not isinstance(state.get("instruction"), str) or not state["instruction"]:
        raise ArtifactError("procedural public marketplace state lacks an instruction")
    return prefix, state


def _agent_output(
    output_model: type[Any],
    *,
    thinking: str,
    evaluation: str,
    memory: str,
    next_goal: str,
    action: dict[str, Any],
) -> str:
    value = {
        "thinking": thinking,
        "evaluation_previous_goal": evaluation,
        "memory": memory,
        "next_goal": next_goal,
        "action": [action],
    }
    output_model.model_validate(value)
    return canonical_json(value)


def _system_with_contract(system: str, contract_row: Mapping[str, Any]) -> str:
    messages = contract_row.get("messages")
    assistant = messages[-1] if isinstance(messages, list) and messages else None
    content = assistant.get("content") if isinstance(assistant, Mapping) else None
    if not isinstance(content, str):
        raise ArtifactError("procedural contract replay has no assistant contract")
    try:
        contract = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ArtifactError("procedural contract replay target is not JSON") from exc
    if not isinstance(contract, dict):
        raise ArtifactError("procedural contract replay target is not an object")
    marker = "DELIBERATIVE DECISION PROTOCOL\n"
    if system.count(marker) != 1:
        raise ArtifactError("browser-use deliberative protocol marker drifted")
    return system.replace(
        marker,
        f"{marker}Literal TaskContract: {canonical_json(contract)}\n",
        1,
    )


def _curriculum_leakage_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Scan learned state while exempting only browser-use's immutable prefix.

    The stock browser-use prompt itself contains generic examples that name an
    Amazon category.  It is already identically present in every benchmark
    evaluation.  Exempt only the byte-identical prefix before our deliberative
    protocol, while scanning the dynamic contract, public state, and targets.
    """

    marker = "DELIBERATIVE DECISION PROTOCOL\n"
    scanned: list[dict[str, Any]] = []
    prefix_hashes: set[str] = set()
    for row in rows:
        visible = {
            "messages": [dict(message) for message in row.get("messages", [])],
            "tools": row.get("tools", []),
        }
        messages = visible["messages"]
        if row["metadata"]["stage"] != "contract-replay":
            system = messages[0].get("content")
            if not isinstance(system, str) or system.count(marker) != 1:
                raise ArtifactError("curriculum system prompt lacks the protocol marker")
            prefix, dynamic = system.split(marker, 1)
            prefix_hashes.add(sha256_bytes(prefix.encode()))
            messages[0]["content"] = f"{marker}{dynamic}"
        scanned.append(visible)
    result = _leakage_audit(scanned)
    if len(prefix_hashes) != 1:
        raise ArtifactError("browser-use immutable system prefix drifted across rows")
    return {
        **result,
        "immutable_harness_prefix_exempted": True,
        "immutable_harness_prefix_sha256": next(iter(prefix_hashes)),
        "dynamic_system_user_and_assistant_scanned": True,
    }


def _source_row(
    row: Mapping[str, Any],
    *,
    kind: str,
    system: str,
    user: str,
    assistant: str,
) -> dict[str, Any]:
    return {
        "schema": SFT_SOURCE_SCHEMA,
        "sample_id": f"browser-action:{row['task_id']}:{kind}",
        "task_id": row["task_id"],
        "source": row["source"],
        "scenario": row["scenario"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "assistant", "content": assistant},
        ],
        "tools": [],
    }


def _action_rows(
    row: Mapping[str, Any], system: str, output_model: type[Any]
) -> list[tuple[str, dict[str, Any]]]:
    prefix, state = _public_state(row)
    arguments = _checkpoint_arguments(row)
    frontier = arguments["frontier"]
    inspected = int(frontier["inspected_count"])
    advertised = int(frontier.get("advertised_count") or inspected)
    selected = str(arguments["proposed_candidate_id"])
    complete_user = f"{prefix}{_STATE_MARKER}{canonical_json(state)}"

    page_size = state.get("page_size")
    if type(page_size) is not int or page_size < 1:
        page_size = max(1, len(state["catalog"]) // 2)
    partial_count = min(page_size, max(1, len(state["catalog"]) - 1))
    if partial_count >= len(state["catalog"]):
        raise ArtifactError("procedural catalog cannot express an incomplete page")
    partial = dict(state)
    partial["catalog"] = list(state["catalog"][:partial_count])
    partial_user = (
        f"Task: {state['instruction']}\n"
        f"Current rendered page shows {partial_count} of {state['visible_total']} results.\n"
        f"[900]<button>Next</button>\n{_STATE_MARKER}{canonical_json(partial)}"
    )
    continue_target = _agent_output(
        output_model,
        thinking=(
            f"Only {partial_count} of {state['visible_total']} advertised options are "
            "present, so coverage is incomplete and a checkpoint would be premature."
        ),
        evaluation="The current page is only a partial option set.",
        memory=f"Resolved {partial_count}/{state['visible_total']} advertised options.",
        next_goal="Open the next result page and continue resolving the remaining options.",
        action={"click": {"index": 900}},
    )
    checkpoint_target = _agent_output(
        output_model,
        thinking=(
            f"Coverage is complete at {inspected}/{advertised}; all submitted facts are "
            "resolved, so the required next transition is the checkpoint alone."
        ),
        evaluation="The complete public option set and feasible frontier are resolved.",
        memory=f"Resolved all {inspected} options; proposed candidate is {selected}.",
        next_goal="Validate the complete frontier before any consequential action.",
        action={"decision_checkpoint": arguments},
    )
    repair_user = (
        f"{complete_user}\n\nResult\ndecision checkpoint rejected: the prior "
        "submission claimed unresolved facts despite exhausted coverage. Repair the "
        "frontier from the public state and submit the checkpoint alone."
    )
    repair_target = _agent_output(
        output_model,
        thinking=(
            "The rejection identifies an evidence inconsistency; the public state "
            "supports a corrected complete frontier."
        ),
        evaluation="The previous checkpoint was rejected and no purchase was authorized.",
        memory=f"Coverage remains {inspected}/{advertised}; repair and resubmit {selected}.",
        next_goal="Resubmit one corrected decision checkpoint with no other action.",
        action={"decision_checkpoint": arguments},
    )
    approved_user = (
        f"Task: {state['instruction']}\n"
        f"Result\nDecision checkpoint approved candidate {selected}.\n"
        f"Current product card: {selected}\n[901]<button>Add to cart</button>"
    )
    approved_target = _agent_output(
        output_model,
        thinking=(
            "The checkpoint approved this exact visible identity, so the "
            "consequential action is now authorized."
        ),
        evaluation=f"The checkpoint approved {selected} and the same identity is visible.",
        memory=f"Approved candidate: {selected}.",
        next_goal="Add the approved candidate to the cart.",
        action={"click": {"index": 901}},
    )
    return [
        (
            "continue-incomplete",
            _source_row(
                row,
                kind="continue-incomplete",
                system=system,
                user=partial_user,
                assistant=continue_target,
            ),
        ),
        (
            "checkpoint-complete",
            _source_row(
                row,
                kind="checkpoint-complete",
                system=system,
                user=complete_user,
                assistant=checkpoint_target,
            ),
        ),
        (
            "repair-rejected",
            _source_row(
                row,
                kind="repair-rejected",
                system=system,
                user=repair_user,
                assistant=repair_target,
            ),
        ),
        (
            "act-after-approval",
            _source_row(
                row,
                kind="act-after-approval",
                system=system,
                user=approved_user,
                assistant=approved_target,
            ),
        ),
    ]


def materialize_browser_action_curriculum(
    campaign: Campaign,
    *,
    split_manifest_path: str | Path,
    rehearsal_path: str | Path,
    contract_replay_path: str | Path,
    output_dir: str | Path,
    task_limit: int = 256,
    contract_replay_limit: int = 112,
) -> dict[str, Any]:
    """Materialize browser-action transitions plus a small compiler replay mix."""

    if task_limit < 1 or contract_replay_limit < 0:
        raise ArtifactError("task_limit must be positive and replay limit nonnegative")
    split_manifest, membership = load_split_manifest(campaign, split_manifest_path)
    rehearsals = read_jsonl(rehearsal_path)
    if len(rehearsals) < task_limit:
        raise ArtifactError("procedural rehearsal has fewer rows than task_limit")
    for index, row in enumerate(rehearsals, 1):
        validate_sft_sample(
            row,
            membership=membership,
            expected_stage="rehearsal",
            row_number=index,
        )
    contracts = read_jsonl(contract_replay_path)
    contracts_by_task: dict[str, list[dict[str, Any]]] = {}
    for row in contracts:
        task_id = row.get("task_id")
        if not isinstance(task_id, str):
            raise ArtifactError("contract source contains an absent task ID")
        contracts_by_task.setdefault(task_id, []).append(row)
    contract_by_task: dict[str, dict[str, Any]] = {}
    for task_id, values in contracts_by_task.items():
        preferred = [row for row in values if str(row.get("sample_id", "")).endswith(":json-only")]
        if len(preferred) != 1:
            raise ArtifactError(f"task {task_id} must have exactly one JSON-only contract row")
        contract_by_task[task_id] = preferred[0]
    selected = sorted(
        rehearsals,
        key=lambda row: _stable_key(int(campaign.campaign["seed"]), str(row["task_id"])),
    )[:task_limit]
    system, output_model = _output_stack()
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for source in selected:
        contract = contract_by_task.get(str(source["task_id"]))
        if contract is None:
            raise ArtifactError("selected rehearsal lacks its procedural contract replay")
        row_system = _system_with_contract(system, contract)
        for kind, candidate in _action_rows(source, row_system, output_model):
            rows.append(
                validate_sft_sample(
                    candidate,
                    membership=membership,
                    expected_stage=kind,
                    row_number=counts[kind] + 1,
                )
            )
            counts[kind] += 1

    canonical_contracts = list(contract_by_task.values())
    if len(canonical_contracts) < contract_replay_limit:
        raise ArtifactError("contract source has fewer rows than replay limit")
    ordered_contracts = sorted(
        canonical_contracts,
        key=lambda row: _stable_key(
            int(campaign.campaign["seed"]) + 1, str(row.get("task_id", ""))
        ),
    )[:contract_replay_limit]
    for index, source in enumerate(ordered_contracts, 1):
        rows.append(
            validate_sft_sample(
                source,
                membership=membership,
                expected_stage="contract-replay",
                row_number=index,
            )
        )
        counts["contract-replay"] += 1

    sample_ids = [row["metadata"]["sample_id"] for row in rows]
    if len(sample_ids) != len(set(sample_ids)):
        raise ArtifactError("browser-action curriculum contains duplicate sample IDs")
    leakage = _curriculum_leakage_audit(rows)
    rows.sort(
        key=lambda row: _stable_key(
            int(campaign.campaign["seed"]) + 2, row["metadata"]["sample_id"]
        )
    )
    output = Path(output_dir).resolve()
    data = publish_jsonl(output / "train.jsonl", rows)
    body = {
        "schema": CURRICULUM_SCHEMA,
        "stage": "refinement",
        "method": "procedural_browseruse_agentoutput_transition_sft",
        "campaign_digest": campaign.digest,
        "split_manifest_sha256": sha256_file(split_manifest_path),
        "split_manifest_body_sha256": split_manifest["manifest_body_sha256"],
        "inputs": {
            "rehearsal": {
                "path": str(Path(rehearsal_path).resolve()),
                "sha256": sha256_file(rehearsal_path),
            },
            "contract_replay": {
                "path": str(Path(contract_replay_path).resolve()),
                "sha256": sha256_file(contract_replay_path),
            },
        },
        "selected_procedural_tasks": task_limit,
        "counts": dict(sorted(counts.items())),
        "assistant_wire_format": "browser-use AgentOutput.action JSON",
        "native_function_call_targets": 0,
        "heldout_amazon_scenarios_present": False,
        "leakage_audit": leakage,
        "output": {"path": data.name, "sha256": sha256_file(data), "rows": len(rows)},
    }
    manifest = dict(body)
    manifest["manifest_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    publish_json(output / "manifest.json", manifest)
    return manifest
