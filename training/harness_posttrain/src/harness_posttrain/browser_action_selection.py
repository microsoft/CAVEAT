"""Select a browser-action continuation checkpoint on sealed procedural tasks.

This selector deliberately exercises the wire interface used by the unchanged
browser-use harness.  It never opens an Amazon task: candidates compile 64
procedural contracts and then respond to four adjacent browser-action states
for each of eight matched procedural checkpoint tasks.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import urllib.request
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    canonical_json,
    publish_json,
    publish_jsonl,
    read_json,
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
from .browser_action_continuation import (
    CONTINUATION_PLAN_SCHEMA,
    _legacy_tree_digest,
    _tree_identity,
)
from .browser_action_continuation_receipt import CONTINUATION_RECEIPT_SCHEMA
from .browser_action_curriculum import (
    _action_rows,
    _output_stack,
    _system_with_contract,
)
from .config import Campaign
from .contract_refinement import TASK_SCHEMA, _draft_types, _score, contract_request_body
from .procedural_corpus import PROCEDURAL_CORPUS_SCHEMA
from .selection import MultiLoraServer, _content
from .splits import load_split_manifest

SELECTION_SCHEMA = "harness-posttrain.browser-action-checkpoint-selection.v1"
EVIDENCE_SCHEMA = "harness-posttrain.browser-action-selection-evidence.v1"
CACHE_INDEX_SCHEMA = "harness-posttrain.browser-action-selection-cache-index.v1"
ATTEMPT_RECEIPT_SCHEMA = "harness-posttrain.browser-action-selection-attempt.v1"

_CHECKPOINT_SCHEMA = "harness-posttrain.checkpoint-shadow-task.v1"
_UPDATES = (20, 22, 24, 26, 28)
_TRANSITIONS = (
    "continue-incomplete",
    "checkpoint-complete",
    "repair-rejected",
    "act-after-approval",
)
_HARD_GATES = {
    "minimum_contract_syntax_rate": 0.95,
    "minimum_contract_semantic_rate": 0.95,
    "minimum_incomplete_continue_rate": 1.0,
    "maximum_premature_checkpoint_or_purchase_rate": 0.0,
    "minimum_complete_checkpoint_rate": 1.0,
    "minimum_repair_checkpoint_rate": 0.875,
    "minimum_postapproval_action_rate": 0.875,
}
_RANK_ORDER = (
    "action_transition_exact_count",
    "minimum_action_transition_exact_count",
    "repair_checkpoint_exact_count",
    "postapproval_action_exact_count",
    "complete_checkpoint_exact_count",
    "incomplete_continue_exact_count",
    "contract_semantic_exact_count",
    "contract_syntax_valid_count",
    "earliest_update",
)

# The frozen selector prompts were tokenized with the exact selected-parent
# tokenizer: the longest is 19,568 tokens.  The old 32,768-token selector
# server therefore left only 13,200 output tokens and a 12,288-token request
# ceiling bound once.  Keep this selector-only context well below the model's
# attested 262,144-token native context while leaving a 45,968-token reserve
# after the longest prompt and the nonbinding action completion ceiling.
_SELECTOR_MAX_MODEL_LEN = 131072
_ACTION_MAX_TOKENS = 65536
_CONTRACT_MAX_TOKENS = 4096
_FROZEN_MAX_PROMPT_TOKENS = 19568
_SELECTOR_REQUEST_TIMEOUT_SECONDS = 7200
_SELECTOR_TRANSPORT_POLICY = {
    "request_timeout_seconds": _SELECTOR_REQUEST_TIMEOUT_SECONDS,
    "maximum_attempts_per_request": 1,
    "retry_count_per_request": 0,
    "client_resubmit_on_transport_failure": False,
    "generation_reset_policy": "forbid_client_resubmission",
}
_SINGLE_ATTEMPT_EXECUTION = {
    "attempt_count": 1,
    "retry_count": 0,
    "client_generation_reset_count": 0,
    "completed_single_attempt": True,
}
_COMPLETION_BUDGET = {
    "selector_max_model_len": _SELECTOR_MAX_MODEL_LEN,
    "action_max_tokens": _ACTION_MAX_TOKENS,
    "contract_max_tokens": _CONTRACT_MAX_TOKENS,
    "frozen_max_prompt_tokens": _FROZEN_MAX_PROMPT_TOKENS,
    "worst_case_prompt_plus_completion_tokens": (_FROZEN_MAX_PROMPT_TOKENS + _ACTION_MAX_TOKENS),
    "context_reserve_tokens": (
        _SELECTOR_MAX_MODEL_LEN - _FROZEN_MAX_PROMPT_TOKENS - _ACTION_MAX_TOKENS
    ),
    "request_transport": _SELECTOR_TRANSPORT_POLICY,
}
if _COMPLETION_BUDGET["context_reserve_tokens"] <= 0:  # pragma: no cover
    raise AssertionError("browser-action selector completion budget exceeds its context")


def _selection_tasks(
    campaign: Campaign, root: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Load and verify only the frozen procedural selection split."""

    raw = root / "corpus/raw"
    raw_manifest_path = raw / "manifest.json"
    raw_manifest = read_json(raw_manifest_path)
    contract_path = raw / "selection_contract_tasks.jsonl"
    checkpoint_path = raw / "selection_checkpoint_tasks.jsonl"
    split_path = root / "corpus/splits/manifest.json"
    if (
        not isinstance(raw_manifest, dict)
        or raw_manifest.get("schema") != PROCEDURAL_CORPUS_SCHEMA
        or raw_manifest.get("campaign_digest") != campaign.digest
        or raw_manifest.get("selection_contracts_sha256") != sha256_file(contract_path)
        or raw_manifest.get("selection_checkpoint_tasks_sha256") != sha256_file(checkpoint_path)
    ):
        raise ArtifactError("sealed procedural selection inputs changed")
    split_manifest, membership = load_split_manifest(campaign, split_path)
    contracts = read_jsonl(contract_path)
    checkpoints = read_jsonl(checkpoint_path)
    if len(contracts) != 64 or len(checkpoints) != 8:
        raise ArtifactError("sealed procedural selection denominators must be 64 and 8")

    by_id: dict[str, dict[str, Any]] = {}
    draft, _extension = _draft_types()
    for index, task in enumerate(contracts, 1):
        task_id = task.get("task_id")
        source = task.get("source")
        scenario = task.get("scenario")
        instruction = task.get("instruction")
        member = membership.get(task_id) if isinstance(task_id, str) else None
        try:
            draft.model_validate(task.get("gold_contract"))
        except (TypeError, ValueError) as exc:
            raise ArtifactError(f"selection contract {index} has invalid gold") from exc
        if (
            task.get("schema") != TASK_SCHEMA
            or not isinstance(task_id, str)
            or task_id in by_id
            or source != "procedural"
            or not isinstance(scenario, str)
            or not isinstance(instruction, str)
            or not instruction
            or not isinstance(member, Mapping)
            or member.get("split") != "selection"
            or member.get("source") != source
            or member.get("scenario") != scenario
        ):
            raise ArtifactError(f"selection contract {index} is not a sealed procedural task")
        by_id[task_id] = task

    from agentarena.scaffolds.browseruse_deliberative import _DecisionCheckpoint

    seen: set[str] = set()
    for index, task in enumerate(checkpoints, 1):
        task_id = task.get("task_id")
        contract = by_id.get(task_id) if isinstance(task_id, str) else None
        state = task.get("public_state")
        catalog = state.get("catalog") if isinstance(state, Mapping) else None
        try:
            arguments = _DecisionCheckpoint.model_validate(task.get("expected_arguments"))
        except (TypeError, ValueError) as exc:
            raise ArtifactError(f"selection checkpoint task {index} has invalid arguments") from exc
        if (
            task.get("schema") != _CHECKPOINT_SCHEMA
            or not isinstance(task_id, str)
            or task_id in seen
            or not isinstance(contract, Mapping)
            or task.get("source") != "procedural"
            or task.get("source") != contract.get("source")
            or task.get("scenario") != contract.get("scenario")
            or task.get("instruction") != contract.get("instruction")
            or not isinstance(state, Mapping)
            or not isinstance(catalog, list)
            or state.get("task_id") != task_id
            or state.get("instruction") != task.get("instruction")
            or state.get("visible_total") != len(catalog)
            or arguments.frontier.inspected_count != state.get("visible_total")
        ):
            raise ArtifactError(
                f"selection checkpoint task {index} does not match its gold contract"
            )
        seen.add(task_id)

    return (
        contracts,
        checkpoints,
        {
            "raw_manifest": str(raw_manifest_path.resolve()),
            "raw_manifest_sha256": sha256_file(raw_manifest_path),
            "split_manifest": str(split_path.resolve()),
            "split_manifest_sha256": sha256_file(split_path),
            "split_manifest_body_sha256": split_manifest["manifest_body_sha256"],
            "selection_contract_tasks": str(contract_path.resolve()),
            "selection_contract_tasks_sha256": sha256_file(contract_path),
            "selection_checkpoint_tasks": str(checkpoint_path.resolve()),
            "selection_checkpoint_tasks_sha256": sha256_file(checkpoint_path),
            "contract_tasks": len(contracts),
            "checkpoint_tasks": len(checkpoints),
            "source": "procedural",
            "split": "selection",
            "amazon_tasks": 0,
            "leakage_audit": raw_manifest.get("leakage_audit"),
        },
    )


def _verified_adapter(
    *,
    name: str,
    update: int,
    path: Path,
    parent: Path,
    descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    identity = _tree_identity(path)
    config_path = path / "adapter_config.json"
    config = read_json(config_path)
    declared_parent = (
        str(config.get("base_model_name_or_path", "")) if isinstance(config, dict) else ""
    )
    try:
        shape_ok = config.get("r") == 64 and float(config.get("lora_alpha", -1)) == 128.0
    except (AttributeError, TypeError, ValueError):
        shape_ok = False
    weights = list(path.glob("adapter_model*.safetensors")) + list(path.glob("adapter_model*.bin"))
    stable = path.parent / "STABLE"
    stable_sha256 = sha256_file(stable)
    if (
        descriptor.get("update") != update
        or Path(str(descriptor.get("path", ""))).resolve() != path
        or descriptor.get("files") != identity["files"]
        or descriptor.get("sha256") != _legacy_tree_digest(path)
        or descriptor.get("stable_marker_sha256") != stable_sha256
        or (
            "tree_sha256" in descriptor and descriptor.get("tree_sha256") != identity["tree_sha256"]
        )
        or ("bytes" in descriptor and descriptor.get("bytes") != identity["bytes"])
        or not shape_ok
        or not declared_parent
        or Path(declared_parent).resolve() != parent
        or len(weights) != 1
        or weights[0].stat().st_size == 0
    ):
        raise ArtifactError(f"candidate {name} differs from its training receipt")
    stable_mtime = stable.stat().st_mtime_ns
    if any(item.stat().st_mtime_ns > stable_mtime for item in path.rglob("*") if item.is_file()):
        raise ArtifactError(f"candidate {name} changed after its STABLE marker")
    return {
        "name": name,
        "update": update,
        "path": str(path),
        "files": identity["files"],
        "bytes": identity["bytes"],
        "tree_sha256": identity["tree_sha256"],
        "receipt_tree_sha256": descriptor["sha256"],
        "adapter_config_sha256": sha256_file(config_path),
        "stable_marker_sha256": stable_sha256,
    }


def _candidate_inventory(
    campaign: Campaign, root: Path, continuation: Path
) -> tuple[Path, dict[str, Path], dict[str, dict[str, Any]], dict[str, Any]]:
    parent = (root / "selected/merged").resolve()
    provenance = parent / "merge_provenance.json"
    original_receipt_path = root / "refinement/training_receipt.json"
    original = read_json(original_receipt_path)
    if (
        not parent.is_dir()
        or not isinstance(original, dict)
        or original.get("schema") != "harness-posttrain.refinement-training-receipt.v1"
        or original.get("status") != "ok"
        or original.get("campaign_digest") != campaign.digest
        or original.get("optimizer_updates") != 20
        or Path(str(original.get("parent_model", ""))).resolve() != parent
        or original.get("parent_merge_provenance_sha256") != sha256_file(provenance)
    ):
        raise ArtifactError("original step-20 parent receipt is incompatible")
    original_matches = [
        row
        for row in original.get("checkpoints", [])
        if isinstance(row, Mapping) and row.get("update") == 20
    ]
    if len(original_matches) != 1:
        raise ArtifactError("original receipt must attest exactly one step-20 adapter")

    continuation_receipt_path = continuation / "training_receipt.json"
    receipt = read_json(continuation_receipt_path)
    plan_path = continuation / "plan.json"
    plan = read_json(plan_path)
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != CONTINUATION_RECEIPT_SCHEMA
        or receipt.get("status") != "ok"
        or receipt.get("stage") != "browser_action_continuation"
        or receipt.get("campaign_digest") != campaign.digest
        or receipt.get("optimizer_updates") != 28
        or receipt.get("checkpoint_updates") != [22, 24, 26, 28]
        or Path(str(receipt.get("parent_model", ""))).resolve() != parent
        or receipt.get("parent_merge_provenance_sha256") != sha256_file(provenance)
        or receipt.get("plan_sha256") != sha256_file(plan_path)
        or not isinstance(plan, dict)
        or plan.get("schema") != CONTINUATION_PLAN_SCHEMA
        or plan.get("campaign_digest") != campaign.digest
        or Path(str(plan.get("parent_model", ""))).resolve() != parent
    ):
        raise ArtifactError("browser-action continuation receipt is incompatible")
    continuation_rows = receipt.get("checkpoints")
    if not isinstance(continuation_rows, list) or [
        row.get("update") for row in continuation_rows if isinstance(row, Mapping)
    ] != [22, 24, 26, 28]:
        raise ArtifactError("continuation receipt checkpoint schedule drifted")

    descriptors: dict[int, Mapping[str, Any]] = {20: original_matches[0]}
    descriptors.update({int(row["update"]): row for row in continuation_rows})
    paths = {
        "step20": Path(str(descriptors[20]["path"])).resolve(),
        **{f"step{step}": Path(str(descriptors[step]["path"])).resolve() for step in _UPDATES[1:]},
    }
    inventory = {
        name: _verified_adapter(
            name=name,
            update=step,
            path=paths[name],
            parent=parent,
            descriptor=descriptors[step],
        )
        for name, step in ((f"step{value}", value) for value in _UPDATES)
    }
    source_step20 = receipt.get("source", {}).get("step_20_adapter")
    if (
        not isinstance(source_step20, Mapping)
        or source_step20.get("tree_sha256") != inventory["step20"]["tree_sha256"]
    ):
        raise ArtifactError("continuation receipt names a different step-20 parent")
    bindings = {
        "original_refinement_receipt": str(original_receipt_path.resolve()),
        "original_refinement_receipt_sha256": sha256_file(original_receipt_path),
        "continuation_plan": str(plan_path.resolve()),
        "continuation_plan_sha256": sha256_file(plan_path),
        "continuation_training_receipt": str(continuation_receipt_path.resolve()),
        "continuation_training_receipt_sha256": sha256_file(continuation_receipt_path),
        "parent_merge_provenance_sha256": sha256_file(provenance),
    }
    return parent, paths, inventory, bindings


def _action_specs(
    *,
    model: str,
    task: Mapping[str, Any],
    contract: Mapping[str, Any],
    system: str,
    output_model: type[Any],
) -> list[dict[str, Any]]:
    """Construct the four exact browser-use requests from one frozen task."""

    synthetic_contract = {
        "messages": [{"role": "assistant", "content": canonical_json(contract["gold_contract"])}]
    }
    task_system = _system_with_contract(system, synthetic_contract)
    synthetic_rehearsal = {
        "task_id": task["task_id"],
        "source": task["source"],
        "scenario": task["scenario"],
        "messages": [
            {"role": "system", "content": "unused"},
            {
                "role": "user",
                "content": (
                    f"The rendered coverage line is exactly: {task['rendered_basis']}\n"
                    f"Public marketplace state:\n{canonical_json(task['public_state'])}"
                ),
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {
                            "name": "decision_checkpoint",
                            "arguments": canonical_json(task["expected_arguments"]),
                        },
                    }
                ],
            },
        ],
    }
    rows = _action_rows(synthetic_rehearsal, task_system, output_model)
    if tuple(kind for kind, _row in rows) != _TRANSITIONS:
        raise AssertionError("browser-action transition order drifted")
    specs = []
    for transition, row in rows:
        assistant = row["messages"][-1]["content"]
        expected = json.loads(assistant)
        request = {
            "model": model,
            "messages": row["messages"][:-1],
            "temperature": 0.0,
            "max_tokens": _ACTION_MAX_TOKENS,
        }
        specs.append(
            {
                "candidate": model,
                "kind": "browser_action",
                "task_id": task["task_id"],
                "source": "procedural",
                "scenario": task["scenario"],
                "transition": transition,
                "expected_action": expected["action"][0],
                "approved_action": json.loads(rows[-1][1]["messages"][-1]["content"])["action"][0],
                "request": request,
            }
        )
    return specs


def _contract_spec(model: str, task: Mapping[str, Any], output_format: type[Any]) -> dict[str, Any]:
    return {
        "candidate": model,
        "kind": "contract",
        "task_id": task["task_id"],
        "source": "procedural",
        "scenario": task["scenario"],
        "gold_contract": task["gold_contract"],
        "instruction": task["instruction"],
        "request": contract_request_body(
            model=model,
            instruction=str(task["instruction"]),
            output_format=output_format,
            temperature=0.0,
            max_tokens=_CONTRACT_MAX_TOKENS,
        ),
    }


def _normalized_action(action: Mapping[str, Any]) -> dict[str, Any]:
    if len(action) != 1:
        return dict(action)
    name, value = next(iter(action.items()))
    if name != "decision_checkpoint":
        return {name: value}
    from agentarena.scaffolds.browseruse_deliberative import _DecisionCheckpoint

    checkpoint = _DecisionCheckpoint.model_validate(value)
    return {name: checkpoint.model_dump(mode="json")}


def _score_action_content(
    content: str | None,
    *,
    output_model: type[Any],
    transition: str,
    expected_action: Mapping[str, Any],
    approved_action: Mapping[str, Any],
) -> dict[str, Any]:
    parse_error: str | None = None
    parsed_output: dict[str, Any] | None = None
    actual_actions: list[dict[str, Any]] = []
    if content is not None:
        try:
            from agentarena.scaffolds.browseruse import _strip_fences

            parsed = output_model.model_validate_json(_strip_fences(content))
            parsed_output = parsed.model_dump(mode="json", exclude_none=True)
            actual_actions = [
                _normalized_action(action) for action in parsed_output.get("action", [])
            ]
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            parse_error = f"{type(exc).__name__}: {exc}"
    expected = _normalized_action(expected_action)
    approved = _normalized_action(approved_action)
    exact = len(actual_actions) == 1 and canonical_json(actual_actions[0]) == canonical_json(
        expected
    )
    premature = False
    if transition == "continue-incomplete":
        premature = any(
            "decision_checkpoint" in action or canonical_json(action) == canonical_json(approved)
            for action in actual_actions
        )
    return {
        "agent_output_schema_valid": parsed_output is not None,
        "parsed_output": parsed_output,
        "parse_error": parse_error,
        "action_count": len(actual_actions),
        "action_names": [next(iter(action), "") for action in actual_actions],
        "transition_exact": exact,
        "premature_checkpoint_or_purchase": premature,
    }


def _selector_post(base_url: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Execute one deterministic selector request exactly once.

    The generic checkpoint selector retries transient transport failures.  That
    is useful operationally, but it silently restarts a deterministic
    completion when a long response reaches the HTTP timeout.  This selector
    therefore uses one generous attempt: any transport failure fails the fresh
    create-only selection rather than resetting generation behind the evidence
    boundary.
    """

    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        method="POST",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json", "authorization": "Bearer EMPTY"},
    )
    with urllib.request.urlopen(  # noqa: S310
        request, timeout=_SELECTOR_REQUEST_TIMEOUT_SECONDS
    ) as response:
        result = json.loads(response.read().decode())
    if not isinstance(result, dict):
        raise ArtifactError("selection inference response is not an object")
    return result


def _execute_spec(
    base_url: str,
    spec: Mapping[str, Any],
    output_model: type[Any],
    selector_attempt_id: int = 1,
) -> dict[str, Any]:
    request = dict(spec["request"])
    response = _selector_post(base_url, request)
    try:
        finish_reason = response["choices"][0].get("finish_reason")
    except (KeyError, IndexError, TypeError) as exc:
        raise ArtifactError("selection response has no finish reason") from exc
    if finish_reason == "length":
        raise ArtifactError("selection completion ceiling bound; increase it before selection")
    content = _content(response)
    if spec["kind"] == "contract":
        if content is None:
            syntax, semantic, normalized, error = False, False, None, "empty assistant content"
        else:
            syntax, semantic, normalized, error = _score(
                str(spec["instruction"]), content, spec["gold_contract"]
            )
        scoring = {
            "syntax_valid": syntax,
            "semantic_exact": semantic,
            "normalized_contract": normalized,
            "error": error,
        }
    else:
        scoring = _score_action_content(
            content,
            output_model=output_model,
            transition=str(spec["transition"]),
            expected_action=spec["expected_action"],
            approved_action=spec["approved_action"],
        )
    public = {key: value for key, value in spec.items() if key != "request"}
    return {
        "schema": EVIDENCE_SCHEMA,
        **public,
        "request": request,
        "request_sha256": sha256_bytes(canonical_json(request).encode()),
        "response": response,
        "response_sha256": sha256_bytes(canonical_json(response).encode()),
        "assistant_content": content,
        "finish_reason": finish_reason,
        "completion_ceiling_bound": False,
        "completion_budget": _COMPLETION_BUDGET,
        "request_execution": _SINGLE_ATTEMPT_EXECUTION,
        "selector_process_attempt_id": selector_attempt_id,
        "scoring": scoring,
    }


def _metrics_from_evidence(
    evidence: list[dict[str, Any]], candidate_names: set[str]
) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for candidate in sorted(candidate_names):
        rows = [row for row in evidence if row.get("candidate") == candidate]
        contracts = [row for row in rows if row.get("kind") == "contract"]
        actions = [row for row in rows if row.get("kind") == "browser_action"]
        if len(contracts) != 64 or len(actions) != 32:
            raise ArtifactError(f"selection evidence denominator drifted for {candidate}")
        if len({row.get("task_id") for row in contracts}) != 64:
            raise ArtifactError(f"duplicate contract evidence for {candidate}")
        by_transition = {
            transition: [row for row in actions if row.get("transition") == transition]
            for transition in _TRANSITIONS
        }
        if any(
            len(values) != 8 or len({row.get("task_id") for row in values}) != 8
            for values in by_transition.values()
        ):
            raise ArtifactError(f"browser-action evidence denominator drifted for {candidate}")
        syntax = sum(bool(row["scoring"]["syntax_valid"]) for row in contracts)
        semantic = sum(bool(row["scoring"]["semantic_exact"]) for row in contracts)
        counts = {
            transition: sum(bool(row["scoring"]["transition_exact"]) for row in values)
            for transition, values in by_transition.items()
        }
        premature = sum(
            bool(row["scoring"]["premature_checkpoint_or_purchase"])
            for row in by_transition["continue-incomplete"]
        )
        action_total = sum(counts.values())
        minimum_action = min(counts.values())
        gate_checks = {
            "contract_syntax": syntax / 64 >= _HARD_GATES["minimum_contract_syntax_rate"],
            "contract_semantic": semantic / 64 >= _HARD_GATES["minimum_contract_semantic_rate"],
            "incomplete_continue": counts["continue-incomplete"] / 8
            >= _HARD_GATES["minimum_incomplete_continue_rate"],
            "no_premature_checkpoint_or_purchase": premature / 8
            <= _HARD_GATES["maximum_premature_checkpoint_or_purchase_rate"],
            "complete_checkpoint": counts["checkpoint-complete"] / 8
            >= _HARD_GATES["minimum_complete_checkpoint_rate"],
            "repair_checkpoint": counts["repair-rejected"] / 8
            >= _HARD_GATES["minimum_repair_checkpoint_rate"],
            "postapproval_action": counts["act-after-approval"] / 8
            >= _HARD_GATES["minimum_postapproval_action_rate"],
        }
        metrics[candidate] = {
            "contract_tasks": 64,
            "contract_syntax_valid_count": syntax,
            "contract_syntax_valid_rate": syntax / 64,
            "contract_semantic_exact_count": semantic,
            "contract_semantic_exact_rate": semantic / 64,
            "tasks_per_action_transition": 8,
            "incomplete_continue_exact_count": counts["continue-incomplete"],
            "incomplete_continue_exact_rate": counts["continue-incomplete"] / 8,
            "premature_checkpoint_or_purchase_count": premature,
            "premature_checkpoint_or_purchase_rate": premature / 8,
            "complete_checkpoint_exact_count": counts["checkpoint-complete"],
            "complete_checkpoint_exact_rate": counts["checkpoint-complete"] / 8,
            "repair_checkpoint_exact_count": counts["repair-rejected"],
            "repair_checkpoint_exact_rate": counts["repair-rejected"] / 8,
            "postapproval_action_exact_count": counts["act-after-approval"],
            "postapproval_action_exact_rate": counts["act-after-approval"] / 8,
            "action_transition_exact_count": action_total,
            "action_transition_exact_rate": action_total / 32,
            "minimum_action_transition_exact_count": minimum_action,
            "minimum_action_transition_exact_rate": minimum_action / 8,
            "gate_checks": gate_checks,
            "passes_hard_gates": all(gate_checks.values()),
        }
    unexpected = {str(row.get("candidate")) for row in evidence} - candidate_names
    if unexpected:
        raise ArtifactError(f"selection evidence names unexpected candidates: {unexpected}")
    return metrics


def _selection_rank(update: int, metrics: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        int(metrics["action_transition_exact_count"]),
        int(metrics["minimum_action_transition_exact_count"]),
        int(metrics["repair_checkpoint_exact_count"]),
        int(metrics["postapproval_action_exact_count"]),
        int(metrics["complete_checkpoint_exact_count"]),
        int(metrics["incomplete_continue_exact_count"]),
        int(metrics["contract_semantic_exact_count"]),
        int(metrics["contract_syntax_valid_count"]),
        -update,
    )


def _choose(metrics: Mapping[str, Mapping[str, Any]]) -> tuple[str, str]:
    passing = [name for name, values in metrics.items() if values["passes_hard_gates"]]
    if not passing:
        return "step20", "fallback_step20_no_candidate_passed_all_hard_gates"
    selected = max(
        sorted(passing),
        key=lambda name: _selection_rank(int(name.removeprefix("step")), metrics[name]),
    )
    return selected, "highest_ranked_hard_gate_pass"


def _expected_specs(
    *,
    candidate_names: set[str],
    contracts: list[dict[str, Any]],
    checkpoints: list[dict[str, Any]],
    system: str,
    output_model: type[Any],
) -> list[dict[str, Any]]:
    draft, _extension = _draft_types()
    by_id = {row["task_id"]: row for row in contracts}
    specs: list[dict[str, Any]] = []
    for candidate in sorted(candidate_names):
        specs.extend(_contract_spec(candidate, task, draft) for task in contracts)
        for task in checkpoints:
            specs.extend(
                _action_specs(
                    model=candidate,
                    task=task,
                    contract=by_id[task["task_id"]],
                    system=system,
                    output_model=output_model,
                )
            )
    return specs


def _evidence_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("candidate", "")),
        str(row.get("kind", "")),
        str(row.get("task_id", "")),
        str(row.get("transition", "")),
    )


def _cache_name(row: Mapping[str, Any]) -> str:
    request = row.get("request")
    identity = {
        "evidence_key": list(_evidence_key(row)),
        "request_sha256": sha256_bytes(canonical_json(request).encode()),
        "completion_budget": _COMPLETION_BUDGET,
        "transport_policy": _SELECTOR_TRANSPORT_POLICY,
    }
    return sha256_bytes(canonical_json(identity).encode()) + ".json"


def _cache_rows(
    cache_dir: Path,
    specs: list[dict[str, Any]],
    output_model: type[Any],
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    """Load only immutable, complete, request-bound per-response cache files."""

    expected = {_evidence_key(spec): spec for spec in specs}
    if cache_dir.is_symlink():
        raise ArtifactError("selection response cache cannot be a symlink")
    if not cache_dir.exists():
        cache_dir.mkdir(parents=True)
        return {}
    if not cache_dir.is_dir():
        raise ArtifactError("selection response cache is not a directory")
    observed: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for path in cache_dir.iterdir():
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or path.is_symlink() or path.suffix != ".json":
            raise ArtifactError("selection response cache contains an unsafe entry")
        row = read_json(path)
        if not isinstance(row, dict):
            raise ArtifactError("selection response cache entry is not an object")
        key = _evidence_key(row)
        spec = expected.get(key)
        if (
            spec is None
            or path.name != _cache_name(spec)
            or key in observed
            or row.get("request") != spec["request"]
            or row.get("request_sha256") != sha256_bytes(canonical_json(spec["request"]).encode())
        ):
            raise ArtifactError("selection response cache binding changed")
        observed[key] = row
    if observed:
        _validate_evidence(
            list(observed.values()), [expected[key] for key in observed], output_model
        )
    return observed


def _uncached_specs(
    specs: list[dict[str, Any]],
    cached: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return frozen requests absent from the validated durable cache."""

    return [spec for spec in specs if _evidence_key(spec) not in cached]


def _validated_start(path: Path) -> dict[str, Any]:
    start = read_json(path)
    body = dict(start)
    body_sha = body.pop("start_body_sha256", None) if isinstance(body, dict) else None
    if (
        not isinstance(start, dict)
        or body_sha != sha256_bytes(canonical_json(body).encode())
        or start.get("schema") != ATTEMPT_RECEIPT_SCHEMA
        or start.get("status") != "started"
        or start.get("transport_policy") != _SELECTOR_TRANSPORT_POLICY
    ):
        raise ArtifactError("selection attempt start binding changed")
    return start


def _terminal_attempt_receipt(path: Path) -> dict[str, Any]:
    record = read_json(path)
    body = dict(record)
    body_sha = body.pop("receipt_body_sha256", None) if isinstance(body, dict) else None
    if (
        not isinstance(record, dict)
        or body_sha != sha256_bytes(canonical_json(body).encode())
        or record.get("schema") != ATTEMPT_RECEIPT_SCHEMA
        or record.get("status") not in {"complete", "failed", "externally_interrupted"}
        or record.get("transport_policy") != _SELECTOR_TRANSPORT_POLICY
    ):
        raise ArtifactError("selection attempt receipt body changed")
    return record


def _prepare_attempts(output: Path, *, cached_rows: int, expected: int) -> list[int]:
    attempts = output / "attempts"
    if attempts.is_symlink():
        raise ArtifactError("selection attempt directory cannot be a symlink")
    attempts.mkdir(parents=True, exist_ok=True)
    paths = sorted(attempts.iterdir())
    if cached_rows and not paths:
        raise ArtifactError("selection cache has no process-attempt provenance")
    identifiers: list[int] = []
    previous_cached = 0
    for index, path in enumerate(paths):
        if path.is_symlink() or not path.is_dir() or not path.name.startswith("attempt-"):
            raise ArtifactError("selection attempt inventory contains an unsafe entry")
        try:
            identifier = int(path.name.removeprefix("attempt-"))
        except ValueError as exc:
            raise ArtifactError("selection attempt identifier is malformed") from exc
        start = _validated_start(path / "start.json")
        if (
            start.get("attempt_id") != identifier
            or start.get("expected_total") != expected
            or start.get("cached_before_attempt") != previous_cached
        ):
            raise ArtifactError("selection attempt start inventory drifted")
        receipt = path / "receipt.json"
        if receipt.exists() or receipt.is_symlink():
            if receipt.is_symlink() or not receipt.is_file():
                raise ArtifactError("selection attempt receipt is unsafe")
            terminal = _terminal_attempt_receipt(receipt)
            if (
                terminal.get("attempt_id") != identifier
                or terminal.get("expected_total") != expected
                or terminal.get("cached_before_attempt") != previous_cached
                or terminal.get("cached_after_attempt")
                != previous_cached + terminal.get("completed_in_attempt", -1)
            ):
                raise ArtifactError("selection attempt receipt identifier drifted")
            if terminal.get("status") == "failed":
                raise ArtifactError(
                    "prior selector transport attempt failed; use a fresh create-only output"
                )
        else:
            if index != len(paths) - 1:
                raise ArtifactError("only the latest selector attempt may be interrupted")
            cache_before = int(start["cached_before_attempt"])
            completed = cached_rows - cache_before
            if completed < 0:
                raise ArtifactError("selection cache regressed after interruption")
            publish_json(
                receipt,
                _attempt_receipt(
                    attempt_id=identifier,
                    status="externally_interrupted",
                    cache_before=cache_before,
                    completed=completed,
                    expected=expected,
                    failure=None,
                    external_interruption=True,
                    started_process_pid=int(start["process_pid"]),
                ),
            )
            terminal = _terminal_attempt_receipt(receipt)
        if (
            terminal.get("status") == "complete"
            and terminal.get("cached_after_attempt") != expected
        ):
            raise ArtifactError("complete selector attempt did not finish the inventory")
        previous_cached = int(terminal["cached_after_attempt"])
        identifiers.append(identifier)
    if identifiers != list(range(1, len(identifiers) + 1)):
        raise ArtifactError("selection attempt identifiers are not contiguous")
    if previous_cached != cached_rows:
        raise ArtifactError("selection cache count differs from its attempt lineage")
    return identifiers


def _next_attempt(output: Path, *, cache_before: int, expected: int) -> tuple[int, Path, Path]:
    identifiers = _prepare_attempts(output, cached_rows=cache_before, expected=expected)
    attempts = output / "attempts"
    identifier = len(identifiers) + 1
    attempt = attempts / f"attempt-{identifier:04d}"
    attempt.mkdir()
    start_body = {
        "schema": ATTEMPT_RECEIPT_SCHEMA,
        "status": "started",
        "attempt_id": identifier,
        "process_pid": os.getpid(),
        "cached_before_attempt": cache_before,
        "expected_total": expected,
        "transport_policy": _SELECTOR_TRANSPORT_POLICY,
    }
    publish_json(
        attempt / "start.json",
        {**start_body, "start_body_sha256": sha256_bytes(canonical_json(start_body).encode())},
    )
    receipt = attempt / "receipt.json"
    return identifier, attempt, receipt


def _attempt_receipt(
    *,
    attempt_id: int,
    status: str,
    cache_before: int,
    completed: int,
    expected: int,
    failure: BaseException | None,
    external_interruption: bool = False,
    started_process_pid: int | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": ATTEMPT_RECEIPT_SCHEMA,
        "attempt_id": attempt_id,
        "status": status,
        "started_process_pid": started_process_pid or os.getpid(),
        "terminal_process_pid": os.getpid(),
        "cached_before_attempt": cache_before,
        "completed_in_attempt": completed,
        "cached_after_attempt": cache_before + completed,
        "expected_total": expected,
        "transport_policy": _SELECTOR_TRANSPORT_POLICY,
        "automatic_request_retries": 0,
        "same_process_request_resubmissions": 0,
        "external_process_interruption": external_interruption,
        "uncached_inflight_requests_may_be_reexecuted": external_interruption,
        "aggregate_zero_generation_resets_claimed": not external_interruption,
    }
    if failure is not None:
        body["failure"] = {"type": type(failure).__name__, "message": str(failure)}
    body["receipt_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    return body


def _attempt_inventory(output: Path, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    attempt_root = output / "attempts"
    if attempt_root.is_symlink() or not attempt_root.is_dir():
        raise ArtifactError("selection attempt inventory is unsafe")
    paths = sorted(attempt_root.iterdir())
    previous_cached = 0
    for expected_identifier, path in enumerate(paths, 1):
        if (
            path.is_symlink()
            or not path.is_dir()
            or path.name != f"attempt-{expected_identifier:04d}"
        ):
            raise ArtifactError("selection attempt inventory contains an unsafe entry")
        start = path / "start.json"
        receipt = path / "receipt.json"
        command = path / "server/command.json"
        log = path / "server/vllm.log"
        started = _validated_start(start)
        record = _terminal_attempt_receipt(receipt)
        if record.get("status") not in {"complete", "externally_interrupted"}:
            raise ArtifactError("selection contains a failed or incompatible attempt")
        completed = sum(
            row.get("selector_process_attempt_id") == expected_identifier for row in evidence
        )
        if (
            started.get("attempt_id") != expected_identifier
            or record.get("attempt_id") != expected_identifier
            or started.get("cached_before_attempt") != previous_cached
            or record.get("cached_before_attempt") != previous_cached
            or record.get("completed_in_attempt") != completed
            or record.get("cached_after_attempt") != previous_cached + completed
            or record.get("expected_total") != len(evidence)
            or started.get("expected_total") != len(evidence)
        ):
            raise ArtifactError("selection attempt lineage differs from cached evidence")
        previous_cached += completed
        descriptor = {
            "attempt": path.name,
            "status": record["status"],
            "start": str(start.resolve()),
            "start_sha256": sha256_file(start),
            "receipt": str(receipt.resolve()),
            "receipt_sha256": sha256_file(receipt),
            "cached_before_attempt": record["cached_before_attempt"],
            "completed_in_attempt": record["completed_in_attempt"],
            "cached_after_attempt": record["cached_after_attempt"],
            "external_process_interruption": record["external_process_interruption"],
        }
        for label, artifact in (("server_command", command), ("server_log", log)):
            if artifact.exists() or artifact.is_symlink():
                metadata = artifact.lstat()
                if artifact.is_symlink() or not stat.S_ISREG(metadata.st_mode):
                    raise ArtifactError("selection server-attempt artifact is unsafe")
                descriptor[label] = str(artifact.resolve())
                descriptor[f"{label}_sha256"] = sha256_file(artifact)
            else:
                descriptor[label] = None
                descriptor[f"{label}_sha256"] = None
        result.append(descriptor)
    if previous_cached != len(evidence):
        raise ArtifactError("selection attempt lineage does not cover all evidence")
    return result


def _cache_inventory(cache_dir: Path, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    expected = sorted((_cache_name(row), row) for row in evidence)
    if cache_dir.is_symlink() or not cache_dir.is_dir():
        raise ArtifactError("selection response cache inventory is unsafe")
    paths = list(cache_dir.iterdir())
    if any(
        path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode) or path.suffix != ".json"
        for path in paths
    ):
        raise ArtifactError("selection response cache inventory contains an unsafe entry")
    files = sorted(path.name for path in paths)
    if files != [name for name, _row in expected]:
        raise ArtifactError("selection response cache inventory drifted")
    return {
        "schema": CACHE_INDEX_SCHEMA,
        "directory": str(cache_dir.resolve()),
        "rows": len(expected),
        "files": [
            {
                "name": name,
                "sha256": sha256_file(cache_dir / name),
                "evidence_key": list(_evidence_key(row)),
                "request_sha256": row["request_sha256"],
                "selector_process_attempt_id": row["selector_process_attempt_id"],
            }
            for name, row in expected
        ],
    }


@contextmanager
def _selection_lock(output: Path):
    if output.is_symlink():
        raise ArtifactError("browser-action selection output cannot be a symlink")
    output.mkdir(parents=True, exist_ok=True)
    lock_path = output / "selection.lock"
    if lock_path.is_symlink():
        raise ArtifactError("browser-action selection lock cannot be a symlink")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ArtifactError("another process owns the browser-action selector output") from exc
        yield
    finally:
        os.close(descriptor)


def _validate_evidence(
    evidence: list[dict[str, Any]],
    specs: list[dict[str, Any]],
    output_model: type[Any],
) -> None:
    expected = {_evidence_key(spec): spec for spec in specs}
    observed = {_evidence_key(row): row for row in evidence}
    if (
        len(expected) != len(specs)
        or len(observed) != len(evidence)
        or set(observed) != set(expected)
    ):
        raise ArtifactError("selection evidence request inventory drifted")
    for key, spec in expected.items():
        row = observed[key]
        public = {name: value for name, value in spec.items() if name != "request"}
        response = row.get("response")
        try:
            response_content = _content(response) if isinstance(response, Mapping) else None
            finish_reason = response["choices"][0].get("finish_reason")
        except (ArtifactError, KeyError, IndexError, TypeError) as exc:
            raise ArtifactError(f"selection evidence response is malformed: {key}") from exc
        if (
            row.get("schema") != EVIDENCE_SCHEMA
            or row.get("source") != "procedural"
            or row.get("completion_ceiling_bound") is not False
            or row.get("completion_budget") != _COMPLETION_BUDGET
            or row.get("request_execution") != _SINGLE_ATTEMPT_EXECUTION
            or type(row.get("selector_process_attempt_id")) is not int
            or row["selector_process_attempt_id"] < 1
            or any(row.get(name) != value for name, value in public.items())
            or row.get("request") != spec["request"]
            or row.get("request_sha256") != sha256_bytes(canonical_json(spec["request"]).encode())
            or row.get("response_sha256")
            != sha256_bytes(canonical_json(row.get("response")).encode())
            or row.get("assistant_content") != response_content
            or row.get("finish_reason") != finish_reason
            or finish_reason == "length"
        ):
            raise ArtifactError(f"selection evidence binding drifted: {key}")
        content = row.get("assistant_content")
        if content is not None and not isinstance(content, str):
            raise ArtifactError(f"selection evidence content is malformed: {key}")
        if spec["kind"] == "contract":
            if content is None:
                recomputed = {
                    "syntax_valid": False,
                    "semantic_exact": False,
                    "normalized_contract": None,
                    "error": "empty assistant content",
                }
            else:
                syntax, semantic, normalized, error = _score(
                    str(spec["instruction"]), content, spec["gold_contract"]
                )
                recomputed = {
                    "syntax_valid": syntax,
                    "semantic_exact": semantic,
                    "normalized_contract": normalized,
                    "error": error,
                }
        else:
            recomputed = _score_action_content(
                content,
                output_model=output_model,
                transition=str(spec["transition"]),
                expected_action=spec["expected_action"],
                approved_action=spec["approved_action"],
            )
        if row.get("scoring") != recomputed:
            raise ArtifactError(f"selection evidence score drifted: {key}")


def _request_execution_summary(
    evidence: list[dict[str, Any]], attempts: list[dict[str, Any]]
) -> dict[str, Any]:
    if any(row.get("request_execution") != _SINGLE_ATTEMPT_EXECUTION for row in evidence):
        raise ArtifactError("selection request execution was not single-attempt")
    requests = len(evidence)
    interrupted = sum(row["status"] == "externally_interrupted" for row in attempts)
    return {
        "accepted_response_count": requests,
        "accepted_response_http_attempt_count": requests,
        "accepted_response_retry_count": 0,
        "all_accepted_responses_completed_single_http_attempt": True,
        "process_attempt_count": len(attempts),
        "externally_interrupted_process_attempt_count": interrupted,
        "same_process_request_resubmissions": 0,
        "uncached_inflight_requests_may_have_been_reexecuted": interrupted > 0,
        "aggregate_client_generation_reset_count": 0 if interrupted == 0 else None,
        "aggregate_zero_generation_resets_claimed": interrupted == 0,
        "policy": _SELECTOR_TRANSPORT_POLICY,
    }


def _manifest_body(
    *,
    campaign: Campaign,
    inputs: Mapping[str, Any],
    bindings: Mapping[str, Any],
    parent: Path,
    inventory: Mapping[str, Mapping[str, Any]],
    metrics: Mapping[str, Mapping[str, Any]],
    evidence_path: Path,
    output: Path,
    num_gpus: int,
) -> dict[str, Any]:
    selected, reason = _choose(metrics)
    passing = sorted(name for name, value in metrics.items() if value["passes_hard_gates"])
    evidence = read_jsonl(evidence_path)
    attempts = _attempt_inventory(output, evidence)
    return {
        "schema": SELECTION_SCHEMA,
        "status": "complete",
        "campaign_digest": campaign.digest,
        "selection_split": "procedural_validation",
        "amazon_data_used": False,
        "inputs": dict(inputs),
        "training_bindings": dict(bindings),
        "base_model": str(parent),
        "num_gpus": num_gpus,
        "candidate_count": len(inventory),
        "candidate_inventory": dict(inventory),
        "hard_gates": _HARD_GATES,
        "rank_order": list(_RANK_ORDER),
        "passing_candidates": passing,
        "candidates": dict(metrics),
        "selected": {
            "name": selected,
            "update": int(selected.removeprefix("step")),
            "adapter": inventory[selected]["path"],
            "adapter_tree_sha256": inventory[selected]["tree_sha256"],
            "reason": reason,
            "metrics": metrics[selected],
        },
        "fallback": "step20",
        "raw_evidence": {
            "path": str(evidence_path.resolve()),
            "sha256": sha256_file(evidence_path),
            "rows": len(evidence),
        },
        "request_counts_per_candidate": {
            "contract": 64,
            "continue-incomplete": 8,
            "checkpoint-complete": 8,
            "repair-rejected": 8,
            "act-after-approval": 8,
            "total": 96,
        },
        "server_attempts": attempts,
        "response_cache": _cache_inventory(output / "response_cache", evidence),
        "completion_ceiling_bound": False,
        "completion_budget": _COMPLETION_BUDGET,
        "request_execution_summary": _request_execution_summary(evidence, attempts),
    }


def _select_browser_action_checkpoint_locked(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    continuation_dir: str | Path,
    output_dir: str | Path,
    concurrency: int = 64,
    num_gpus: int = 4,
    port: int = 8000,
) -> dict[str, Any]:
    """Evaluate five LoRA checkpoints and publish an immutable selection."""

    if not 1 <= concurrency <= 64:
        raise ArtifactError("browser-action selection concurrency must be in [1, 64]")
    if num_gpus not in {4, 8}:
        raise ArtifactError("browser-action selection requires four or eight GPUs")
    root = Path(campaign_root).resolve()
    continuation = Path(continuation_dir).resolve()
    requested_output = Path(output_dir)
    if requested_output.is_symlink():
        raise ArtifactError("browser-action selection output cannot be a symlink")
    output = requested_output.resolve()
    contracts, checkpoints, inputs = _selection_tasks(campaign, root)
    parent, paths, inventory, bindings = _candidate_inventory(campaign, root, continuation)
    system, output_model = _output_stack()
    specs = _expected_specs(
        candidate_names=set(paths),
        contracts=contracts,
        checkpoints=checkpoints,
        system=system,
        output_model=output_model,
    )
    evidence_path = output / "raw_evidence.jsonl"
    manifest_path = output / "selected_checkpoint.json"
    cache_dir = output / "response_cache"
    if manifest_path.is_file():
        manifest = read_json(manifest_path)
        evidence = read_jsonl(evidence_path)
        _validate_evidence(evidence, specs, output_model)
        metrics = _metrics_from_evidence(evidence, set(paths))
        body = _manifest_body(
            campaign=campaign,
            inputs=inputs,
            bindings=bindings,
            parent=parent,
            inventory=inventory,
            metrics=metrics,
            evidence_path=evidence_path,
            output=output,
            num_gpus=num_gpus,
        )
        expected = dict(body)
        expected["manifest_body_sha256"] = sha256_bytes(canonical_json(body).encode())
        if manifest != expected:
            raise ArtifactError("published browser-action selection manifest drifted")
        return manifest
    if evidence_path.exists() or evidence_path.is_symlink():
        raise ArtifactError("uncommitted browser-action selection evidence already exists")

    cached = _cache_rows(cache_dir, specs, output_model)
    _prepare_attempts(output, cached_rows=len(cached), expected=len(specs))
    missing = _uncached_specs(specs, cached)
    if missing:
        cache_before = len(cached)
        attempt_id, attempt_dir, receipt_path = _next_attempt(
            output,
            cache_before=cache_before,
            expected=len(specs),
        )
        completed = 0
        failure: BaseException | None = None
        try:
            with MultiLoraServer(
                base_model=parent,
                adapters=paths,
                output_dir=attempt_dir / "server",
                port=port,
                gpu_ids=tuple(range(num_gpus)),
                max_model_len=_SELECTOR_MAX_MODEL_LEN,
            ) as server:
                with ThreadPoolExecutor(max_workers=concurrency) as pool:
                    future_specs = {
                        pool.submit(
                            _execute_spec,
                            server.base_url,
                            spec,
                            output_model,
                            attempt_id,
                        ): spec
                        for spec in missing
                    }
                    for future in as_completed(future_specs):
                        try:
                            row = future.result()
                        except BaseException as exc:
                            failure = failure or exc
                            continue
                        key = _evidence_key(row)
                        publish_json(cache_dir / _cache_name(row), row)
                        cached[key] = row
                        completed += 1
        except BaseException as exc:
            failure = failure or exc
        status = "complete" if failure is None else "failed"
        publish_json(
            receipt_path,
            _attempt_receipt(
                attempt_id=attempt_id,
                status=status,
                cache_before=cache_before,
                completed=completed,
                expected=len(specs),
                failure=failure,
            ),
        )
        if failure is not None:
            raise ArtifactError(
                "selector attempt failed; completed responses were preserved but this "
                "output is fail-closed"
            ) from failure

    evidence = list(cached.values())
    evidence.sort(key=_evidence_key)
    _validate_evidence(evidence, specs, output_model)
    publish_jsonl(evidence_path, evidence)
    metrics = _metrics_from_evidence(evidence, set(paths))
    body = _manifest_body(
        campaign=campaign,
        inputs=inputs,
        bindings=bindings,
        parent=parent,
        inventory=inventory,
        metrics=metrics,
        evidence_path=evidence_path,
        output=output,
        num_gpus=num_gpus,
    )
    manifest = dict(body)
    manifest["manifest_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    publish_json(manifest_path, manifest)
    return manifest


def select_browser_action_checkpoint(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    continuation_dir: str | Path,
    output_dir: str | Path,
    concurrency: int = 64,
    num_gpus: int = 4,
    port: int = 8000,
) -> dict[str, Any]:
    """Evaluate and durably cache a sealed selector under one output-wide lock."""

    output = Path(output_dir).resolve()
    with _selection_lock(output):
        return _select_browser_action_checkpoint_locked(
            campaign,
            campaign_root=campaign_root,
            continuation_dir=continuation_dir,
            output_dir=output,
            concurrency=concurrency,
            num_gpus=num_gpus,
            port=port,
        )
