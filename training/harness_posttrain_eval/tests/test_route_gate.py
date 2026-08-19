from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import harness_posttrain_eval.route_gate as route_gate
from harness_posttrain_eval.common import canonical_bytes, sha256_bytes


class _Draft:
    @classmethod
    def model_validate(cls, value: Any) -> Any:
        if not isinstance(value, dict) or value.get("valid") is not True:
            raise ValueError("invalid draft")
        return value


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    rows = [
        {
            "schema": "harness-posttrain.contract-shadow-task.v1",
            "task_id": f"task-{index}",
            "source": "procedural",
            "scenario": "generic_shop",
            "condition": "clean",
            "instruction": f"Choose task {index}.",
            "gold_contract": {"expected": index},
        }
        for index in range(4)
    ]
    data = b"".join(canonical_bytes(row) + b"\n" for row in rows)
    path = tmp_path / "tasks.jsonl"
    path.write_bytes(data)
    monkeypatch.setattr(route_gate, "SEALED_TASKS_SHA256", sha256_bytes(data))
    monkeypatch.setattr(
        route_gate, "SEALED_TASK_IDS", tuple(row["task_id"] for row in rows)
    )
    return path


def _stack() -> route_gate.ContractStack:
    def request_body(**kwargs: Any) -> dict[str, Any]:
        return {"messages": [{"content": "schema"}, {"content": kwargs["instruction"]}]}

    def score(
        instruction: str, content: str, gold: dict[str, Any]
    ) -> tuple[bool, bool, str | None, str]:
        del instruction
        parsed = json.loads(content)
        valid = parsed.get("expected") == gold["expected"]
        return True, valid, content, "ok" if valid else "wrong"

    return route_gate.ContractStack(
        draft=_Draft,
        request_body=request_body,
        decode=json.loads,
        score=score,
        source=Path("/fixed/contract_refinement.py"),
        source_sha256="a" * 64,
    )


def test_reports_four_of_four_without_amazon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(route_gate, "_load_contract_stack", _stack)

    def post(**kwargs: Any) -> tuple[dict[str, Any], float]:
        index = int(kwargs["body"]["messages"][1]["content"].split()[2].rstrip("."))
        content = json.dumps({"valid": True, "expected": index})
        return {"choices": [{"message": {"content": content}}]}, 0.1

    monkeypatch.setattr(route_gate, "_post", post)
    result = route_gate.run_route_gate(
        arm="trained",
        base_url="http://127.0.0.1:18100/v1",
        model="trained-exact-lora",
        tasks_path=tasks,
        api_key="secret",
    )
    assert result["amazon_tasks_used"] == []
    assert result["semantic_pass_fraction"] == "4/4"
    assert result["passed"] is True


def test_semantic_failure_is_not_infrastructure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(route_gate, "_load_contract_stack", _stack)
    monkeypatch.setattr(
        route_gate,
        "_post",
        lambda **_kwargs: (
            {"choices": [{"message": {"content": '{"valid":true,"expected":999}'}}]},
            0.1,
        ),
    )
    result = route_gate.run_route_gate(
        arm="base",
        base_url="http://127.0.0.1:18000/v1",
        model="base-exact-lora",
        tasks_path=tasks,
        api_key="secret",
    )
    assert result["semantic_pass_fraction"] == "0/4"
    assert result["infrastructure_failures"] == 0
    assert result["passed"] is False


def test_budget_exhaustion_without_answer_is_behavioral_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tasks = _fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(route_gate, "_load_contract_stack", _stack)
    monkeypatch.setattr(
        route_gate,
        "_post",
        lambda **_kwargs: (
            {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": None, "reasoning_content": "thinking"},
                    }
                ]
            },
            0.1,
        ),
    )
    result = route_gate.run_route_gate(
        arm="base",
        base_url="http://127.0.0.1:18000/v1",
        model="base-exact-lora",
        tasks_path=tasks,
        api_key="secret",
    )
    assert result["semantic_pass_fraction"] == "0/4"
    assert result["infrastructure_failures"] == 0
    assert result["passed"] is False


def test_rejects_amazon_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tasks = _fixture(tmp_path, monkeypatch)
    data = tasks.read_text().replace(
        '"scenario":"generic_shop"', '"scenario":"laptop"', 1
    )
    tasks.write_text(data)
    monkeypatch.setattr(route_gate, "SEALED_TASKS_SHA256", sha256_bytes(data.encode()))
    with pytest.raises(route_gate.IntegrityError, match="not non-Amazon"):
        route_gate._load_tasks(tasks)  # noqa: SLF001
