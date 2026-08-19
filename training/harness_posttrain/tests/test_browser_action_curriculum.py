from __future__ import annotations

import json
from pathlib import Path

from conftest import write_jsonl

from harness_posttrain.browser_action_curriculum import (
    materialize_browser_action_curriculum,
)


def _sources(task_id: str = "procedural-train-1") -> tuple[dict, dict]:
    contract = {
        "constraints": [],
        "objectives": [
            {
                "criterion_id": "objective_score",
                "description": "score",
                "direction": "maximize",
                "unit": "points",
                "priority": None,
                "weight": None,
            }
        ],
        "search_mode": "best_available",
    }
    state = {
        "schema_version": 1,
        "task_id": task_id,
        "domain_family": "generic_shop_a",
        "layout_family": "train_cards_numbered_pager",
        "instruction": "Choose the listed option with the highest score.",
        "visible_total": 2,
        "page_size": 1,
        "catalog": [
            {
                "item_id": "opt_a",
                "title": "Alpha",
                "price": 10,
                "list_price": 10,
                "rating": 4.0,
                "review_count": 10,
                "purchases": 10,
                "facts": {"score": 9},
            },
            {
                "item_id": "opt_b",
                "title": "Beta",
                "price": 10,
                "list_price": 10,
                "rating": 4.0,
                "review_count": 10,
                "purchases": 10,
                "facts": {"score": 2},
            },
        ],
        "steering": {
            "condition": "clean",
            "cues": [],
            "default_order": ["opt_a", "opt_b"],
            "disclosure": "",
        },
    }
    arguments = {
        "frontier": {
            "inspected_count": 2,
            "advertised_count": 2,
            "coverage_mode": "advertised_total",
            "advertised_page_count": None,
            "enumerated_page_count": None,
            "excluded_count": 1,
            "unresolved_count": 0,
            "exhausted": True,
            "basis": "2 results",
        },
        "candidates": [
            {
                "id": "opt_a",
                "label": "Alpha",
                "source_url": "https://shop.local/products/opt_a",
                "facts": [
                    {
                        "criterion_id": "objective_score",
                        "state": "known",
                        "value": 9,
                        "unit": "points",
                    }
                ],
            }
        ],
        "proposed_candidate_id": "opt_a",
    }
    common = {
        "schema": "harness-posttrain.sft-source.v1",
        "task_id": task_id,
        "source": "procedural",
        "scenario": "generic_shop_a",
    }
    rehearsal = {
        **common,
        "sample_id": f"rehearsal:{task_id}:checkpoint",
        "tools": [],
        "messages": [
            {"role": "system", "content": "Call the checkpoint."},
            {
                "role": "user",
                "content": (
                    "The rendered coverage line is exactly: 2 results\n"
                    f"Public marketplace state:\n{json.dumps(state)}"
                ),
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "checkpoint_test",
                        "type": "function",
                        "function": {
                            "name": "decision_checkpoint",
                            "arguments": json.dumps(arguments),
                        },
                    }
                ],
            },
        ],
    }
    contract_row = {
        **common,
        "sample_id": f"contract:{task_id}:json-only",
        "tools": [],
        "messages": [
            {"role": "system", "content": "Compile the contract."},
            {"role": "user", "content": state["instruction"]},
            {"role": "assistant", "content": json.dumps(contract)},
        ],
    }
    return rehearsal, contract_row


def test_curriculum_uses_real_agent_output_actions(tmp_path: Path, campaign, sealed_splits) -> None:
    split_manifest, _inventory = sealed_splits
    rehearsal, contract = _sources()
    rehearsal_path = write_jsonl(tmp_path / "rehearsal.jsonl", [rehearsal])
    contract_path = write_jsonl(tmp_path / "contract.jsonl", [contract])
    output = tmp_path / "curriculum"
    manifest = materialize_browser_action_curriculum(
        campaign,
        split_manifest_path=split_manifest,
        rehearsal_path=rehearsal_path,
        contract_replay_path=contract_path,
        output_dir=output,
        task_limit=1,
        contract_replay_limit=1,
    )
    assert manifest["output"]["rows"] == 5
    assert manifest["native_function_call_targets"] == 0
    assert manifest["heldout_amazon_scenarios_present"] is False
    rows = [json.loads(line) for line in (output / "train.jsonl").read_text().splitlines()]
    action_rows = [row for row in rows if row["metadata"]["stage"] != "contract-replay"]
    targets = [json.loads(row["messages"][-1]["content"]) for row in action_rows]
    action_names = [next(iter(target["action"][0])) for target in targets]
    assert sorted(action_names) == ["click", "click", "decision_checkpoint", "decision_checkpoint"]
    assert all("tool_calls" not in row["messages"][-1] for row in action_rows)
    assert all("Literal TaskContract:" in row["messages"][0]["content"] for row in action_rows)
