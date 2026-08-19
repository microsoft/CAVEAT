from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from conftest import sft_source, write_jsonl

from harness_posttrain import contract_refinement
from harness_posttrain.artifacts import sha256_file
from harness_posttrain.config import Campaign
from harness_posttrain.contract_refinement import materialize_contract_refinement
from harness_posttrain.quick_data import CONTRACT_SYSTEM_PROMPT
from harness_posttrain.splits import freeze_splits


def test_contract_requests_match_browseruse_schema_prompt_without_response_format(
    monkeypatch: Any,
) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"choices":[{"message":{"content":"{}"}}]}'

    def fake_urlopen(request: Any, *, timeout: float) -> FakeResponse:
        captured["request"] = json.loads(request.data.decode())
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(contract_refinement.urllib.request, "urlopen", fake_urlopen)
    draft, _extension = contract_refinement._draft_types()  # noqa: SLF001
    body, _response = contract_refinement._request_once(  # noqa: SLF001
        url="http://127.0.0.1:8000/v1/chat/completions",
        api_key="test-key",
        model="adapter-u5",
        instruction="Choose the cheapest qualifying option.",
        output_format=draft,
        seed=7,
        timeout_seconds=12,
    )

    assert captured["request"] == body
    assert captured["timeout"] == 12
    assert "response_format" not in body
    system_prompt = body["messages"][0]["content"]
    assert system_prompt.startswith(CONTRACT_SYSTEM_PROMPT + "\n<json_schema>\n")
    suffix = system_prompt.removeprefix(CONTRACT_SYSTEM_PROMPT)
    assert hashlib.sha256(suffix.encode()).hexdigest() == (
        "5316dbfd9682a90f073fc50407ffff05e6442c2f2e8058fbe2d078b0edd96e45"
    )
    assert "'name': 'agent_output'" in suffix
    assert '"name": "agent_output"' not in suffix


def test_empty_completed_rollout_is_retained_as_behavioral_failure(
    monkeypatch: Any,
) -> None:
    draft, _extension = contract_refinement._draft_types()  # noqa: SLF001
    monkeypatch.setattr(
        contract_refinement,
        "_request_once",
        lambda **_kwargs: (
            {"model": "selected"},
            {"id": "response-1", "choices": [{"message": {"content": None}}]},
        ),
    )

    result = contract_refinement._sample(  # noqa: SLF001
        base_url="http://127.0.0.1:8000/v1",
        api_key="test-key",
        model="selected",
        task={
            "task_id": "shadow-1",
            "source": "procedural",
            "scenario": "camera",
            "condition": "truthful_steered",
            "instruction": "Choose the best qualifying option.",
        },
        sample_index=0,
        output_format=draft,
        root_seed=7,
        timeout_seconds=12,
    )

    assert result["infrastructure_complete"] is True
    assert result["assistant_content"] == ""
    assert result["response_id"] == "response-1"


def test_operational_score_ignores_free_form_handles_and_implicit_objective_units() -> None:
    gold = {
        "constraints": [
            {
                "criterion_id": "constraint_price",
                "description": "total price",
                "operator": "le",
                "expected": 500,
                "unit": "$",
            }
        ],
        "objectives": [
            {
                "criterion_id": "objective_weight_kg",
                "description": "weight",
                "direction": "minimize",
                "unit": "kg",
                "priority": 1,
                "weight": None,
            }
        ],
        "search_mode": "best_available",
    }
    candidate = {
        "constraints": [
            {
                "criterion_id": "cost",
                "description": "Cost must not exceed $500",
                "operator": "le",
                "expected": 500.0,
                "unit": "$",
            }
        ],
        "objectives": [
            {
                "criterion_id": "item_weight",
                "description": "Prefer lower item weight",
                "direction": "minimize",
                "unit": None,
                "priority": 1,
                "weight": None,
            }
        ],
        "search_mode": "best_available",
    }

    syntax, semantic, normalized, error = contract_refinement._score_operational(  # noqa: SLF001
        "Spend at most $500 and prefer lower weight in explicit priority order.",
        json.dumps(candidate),
        gold,
    )

    assert syntax is True
    assert semantic is True
    assert normalized is not None
    assert error == "ok"


def test_operational_score_rejects_decision_relevant_drift() -> None:
    gold = {
        "constraints": [
            {
                "criterion_id": "constraint_price",
                "description": "total price",
                "operator": "le",
                "expected": 500,
                "unit": "$",
            }
        ],
        "objectives": [
            {
                "criterion_id": "objective_weight_kg",
                "description": "weight",
                "direction": "minimize",
                "unit": "kg",
                "priority": 1,
                "weight": None,
            }
        ],
        "search_mode": "best_available",
    }
    candidate = {
        "constraints": [
            {
                "criterion_id": "price",
                "description": "price",
                "operator": "le",
                "expected": 500,
                "unit": "$",
            }
        ],
        "objectives": [
            {
                "criterion_id": "weight",
                "description": "weight",
                "direction": "maximize",
                "unit": "kg",
                "priority": None,
                "weight": None,
            }
        ],
        "search_mode": "best_available",
    }

    syntax, semantic, _normalized, error = contract_refinement._score_operational(  # noqa: SLF001
        "Spend at most $500 and prefer lower weight in explicit priority order.",
        json.dumps(candidate),
        gold,
    )

    assert syntax is True
    assert semantic is False
    assert "decision-relevant" in error


def test_contract_refinement_filters_samples_and_realizes_exact_mix(
    tmp_path: Path, campaign: Campaign
) -> None:
    tasks = []
    inventory = []
    gold = {
        "constraints": [
            {
                "criterion_id": "constraint_capacity",
                "description": "capacity",
                "operator": "ge",
                "expected": 10,
                "unit": None,
            }
        ],
        "objectives": [
            {
                "criterion_id": "objective_price",
                "description": "price",
                "direction": "minimize",
                "unit": None,
                "priority": None,
                "weight": None,
            }
        ],
        "search_mode": "best_available",
    }
    for index in range(64):
        task_id = f"shadow-{index:02d}"
        scenario = f"domain-{index % 8}"
        condition = "truthful_steered" if index < 48 else "clean"
        inventory.append(
            {
                "task_id": task_id,
                "source": "procedural",
                "scenario": scenario,
                "split": "train",
            }
        )
        tasks.append(
            {
                "schema": "harness-posttrain.contract-shadow-task.v1",
                "task_id": task_id,
                "source": "procedural",
                "scenario": scenario,
                "condition": condition,
                "instruction": (
                    "Choose the lowest-price option with capacity at least 10. "
                    "Buy one option."
                ),
                "gold_contract": gold,
            }
        )
    inventory_path = write_jsonl(tmp_path / "inventory.jsonl", inventory)
    split_root = tmp_path / "splits"
    freeze_splits(campaign, inventory_path=inventory_path, output_dir=split_root)
    tasks_path = write_jsonl(tmp_path / "tasks.jsonl", tasks)
    rollouts = []
    for task_index, task in enumerate(tasks):
        for sample in range(4):
            successful = (task_index * 4 + sample) % 2 == 0
            rollouts.append(
                {
                    "schema": "harness-posttrain.contract-rollout.v1",
                    "episode_id": f"{task['task_id']}:{sample}",
                    "task_id": task["task_id"],
                    "source": "procedural",
                    "scenario": task["scenario"],
                    "condition": task["condition"],
                    "sample_index": sample,
                    "infrastructure_complete": True,
                    "assistant_content": json.dumps(gold if successful else {}),
                }
            )
    rollout_root = tmp_path / "rollouts"
    rollout_path = write_jsonl(rollout_root / "rollouts.jsonl", rollouts)
    manifest = {
        "schema": "harness-posttrain.contract-rollouts.v1",
        "campaign_digest": campaign.digest,
        "tasks_sha256": sha256_file(tasks_path),
        "output": {"path": rollout_path.name, "sha256": sha256_file(rollout_path)},
    }
    manifest_path = rollout_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    rehearsal = []
    for index in range(50):
        task = tasks[index % len(tasks)]
        rehearsal.append(
            sft_source(
                f"rehearsal-{index}",
                task_id=str(task["task_id"]),
                source="procedural",
                scenario=str(task["scenario"]),
            )
        )
    result = materialize_contract_refinement(
        campaign,
        split_manifest_path=split_root / "manifest.json",
        tasks_path=tasks_path,
        rollout_manifest_path=manifest_path,
        rehearsal_path=write_jsonl(tmp_path / "rehearsal.jsonl", rehearsal),
        output_dir=tmp_path / "refinement",
    )
    assert result["syntax_valid"] == 128
    assert result["semantic_valid"] == 128
    assert result["output"]["rows"] == 210
    assert result["realized_counts"] == {
        "correction": 126,
        "success": 63,
        "rehearsal": 21,
    }
