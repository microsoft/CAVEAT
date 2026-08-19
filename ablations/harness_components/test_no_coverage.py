from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

import no_coverage_scaffold as no_coverage
from agentarena.core.models import ModelSpec
from agentarena.core.scaffold import RawTrajectory, RunContext, SCAFFOLDS
from agentarena.core.task import TaskSpec
from agentarena.scaffolds._deliberative_core import (
    Candidate,
    CandidateFact,
    Constraint,
    ConstraintOperator,
    FactState,
    Objective,
    ObjectiveDirection,
    SearchMode,
    TaskContract,
)
from agentarena.scaffolds.browseruse_deliberative import _DeliberativeExtension


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PROMPT_ONLY_PATH = ROOT / "ablations" / "prompt_only" / "prompt_only_scaffold.py"


def _context() -> RunContext:
    task = TaskSpec(
        task_id="local-choice-task",
        env="example",
        instruction=(
            "Choose a laptop with at least 16 GB RAM; minimize price and "
            "maximize battery life."
        ),
        preferences={"ram__min": 16},
        catalog="catalog-a",
        condition="combined",
        start_path="/start",
    )
    model = ModelSpec(
        name="test-model",
        provider="openai",
        base_url="http://127.0.0.1:1/v1",
        api_key="unused",
    )
    return RunContext(
        task=task,
        start_url="http://127.0.0.1:9999/start",
        model=model,
        work_dir=Path("/tmp/no-coverage-test"),
        max_steps=1234,
        headless=True,
    )


def _contract() -> TaskContract:
    return TaskContract(
        instruction=_context().task.instruction,
        constraints=(
            Constraint(
                criterion_id="ram",
                description="RAM is at least 16 GB",
                operator=ConstraintOperator.GE,
                expected=16,
                unit="GB",
            ),
        ),
        objectives=(
            Objective(
                criterion_id="price",
                description="minimize price",
                direction=ObjectiveDirection.MINIMIZE,
                unit="USD",
            ),
            Objective(
                criterion_id="battery",
                description="maximize battery life",
                direction=ObjectiveDirection.MAXIMIZE,
                unit="h",
            ),
        ),
        search_mode=SearchMode.BEST_AVAILABLE,
    )


def _candidate(
    candidate_id: str,
    *,
    ram=16,
    price=900,
    battery=10,
    battery_state: FactState = FactState.KNOWN,
) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        label=f"Laptop {candidate_id}",
        source_url=f"http://127.0.0.1:9999/items/{candidate_id}",
        facts=(
            CandidateFact("ram", FactState.KNOWN, ram, "GB"),
            CandidateFact("price", FactState.KNOWN, price, "USD"),
            CandidateFact(
                "battery",
                battery_state,
                battery if battery_state is FactState.KNOWN else None,
                "h" if battery_state is FactState.KNOWN else None,
            ),
        ),
    )


def _input_candidate(candidate: Candidate) -> dict:
    return {
        "id": candidate.candidate_id,
        "label": candidate.label,
        "source_url": candidate.source_url,
        "facts": [
            {
                "criterion_id": fact.criterion_id,
                "state": fact.state.value,
                "value": fact.value,
                "unit": fact.unit,
            }
            for fact in candidate.facts
        ],
    }


def test_scaffold_is_registered_only_by_component_hook() -> None:
    assert SCAFFOLDS.get(no_coverage.SCAFFOLD_NAME) is (
        no_coverage.BrowserUseDeliberativeNoCoverageScaffold
    )

    check = (
        "import json, agentarena.scaffolds;"
        "from agentarena.core.scaffold import SCAFFOLDS;"
        "print(json.dumps(SCAFFOLDS.names()))"
    )
    clean_env = dict(os.environ)
    clean_env.pop("PYTHONPATH", None)
    clean = subprocess.run(
        [sys.executable, "-c", check],
        cwd=ROOT,
        env=clean_env,
        check=True,
        capture_output=True,
        text=True,
    )
    clean_names = json.loads(clean.stdout)
    assert no_coverage.SCAFFOLD_NAME not in clean_names
    assert "browseruse-prompt-only" not in clean_names

    component_env = dict(clean_env)
    component_env["PYTHONPATH"] = str(HERE)
    component = subprocess.run(
        [sys.executable, "-c", check],
        cwd=ROOT,
        env=component_env,
        check=True,
        capture_output=True,
        text=True,
    )
    component_names = json.loads(component.stdout)
    assert no_coverage.SCAFFOLD_NAME in component_names
    assert "browseruse-prompt-only" in component_names


def test_hook_reuses_existing_prompt_only_module() -> None:
    check = (
        "import json, pathlib, prompt_only_scaffold as p;"
        "print(json.dumps(str(pathlib.Path(p.__file__).resolve())))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(HERE)
    completed = subprocess.run(
        [sys.executable, "-c", check],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert Path(json.loads(completed.stdout)) == PROMPT_ONLY_PATH.resolve()


def test_production_compiler_and_common_core_are_reused() -> None:
    assert "_compile_contract" not in no_coverage._NoCoverageExtension.__dict__
    assert no_coverage._NoCoverageExtension._compile_contract is (
        _DeliberativeExtension._compile_contract
    )
    assert no_coverage.select_candidate.__module__ == (
        "agentarena.scaffolds._deliberative_core"
    )


def test_checkpoint_schema_has_no_global_coverage_channel() -> None:
    schema = no_coverage._LocalDecisionCheckpoint.model_json_schema()
    assert set(schema["properties"]) == {
        "candidates",
        "proposed_candidate_id",
    }
    serialized = json.dumps(schema).casefold()
    for forbidden in (
        "advertised_count",
        "advertised_page_count",
        "enumerated_page_count",
        "excluded_count",
        "exhausted",
        "finite_pages",
        "inspected_count",
        "pager",
        "unresolved_count",
    ):
        assert forbidden not in serialized
    with pytest.raises(ValidationError):
        no_coverage._LocalDecisionCheckpoint.model_validate(
            {"candidates": [], "proposed_candidate_id": "a"}
        )


def test_local_choice_approves_dominant_candidate_without_closure_evidence() -> None:
    winner = _candidate("winner", price=900, battery=10)
    dominated = _candidate("dominated", price=1000, battery=8)
    result = no_coverage.evaluate_local_choice(
        contract=_contract(),
        start_origin="http://127.0.0.1:9999",
        current_url="http://127.0.0.1:9999/items/winner",
        candidates=(dominated, winner),
        proposed_candidate_id="winner",
    )
    assert result.approved is True
    assert result.selected_candidate_id == "winner"
    assert result.method == "pareto_dominant"
    assert result.feasible_candidate_ids == ("dominated", "winner")
    assert result.pareto_frontier_ids == ("winner",)


def test_local_choice_rejects_wrong_criterion_choice() -> None:
    result = no_coverage.evaluate_local_choice(
        contract=_contract(),
        start_origin="http://127.0.0.1:9999",
        current_url="http://127.0.0.1:9999/compare",
        candidates=(
            _candidate("winner", price=900, battery=10),
            _candidate("dominated", price=1000, battery=8),
        ),
        proposed_candidate_id="dominated",
    )
    assert result.approved is False
    assert any("exact best candidates" in reason for reason in result.reasons)


@pytest.mark.parametrize(
    "bad_candidate",
    [
        _candidate("unknown", battery_state=FactState.UNKNOWN),
        _candidate("constraint-failure", ram=8),
    ],
    ids=["unknown-fact", "hard-constraint-failure"],
)
def test_local_choice_keeps_known_fact_and_hard_constraint_guards(
    bad_candidate: Candidate,
) -> None:
    result = no_coverage.evaluate_local_choice(
        contract=_contract(),
        start_origin="http://127.0.0.1:9999",
        current_url="http://127.0.0.1:9999/compare",
        candidates=(bad_candidate,),
        proposed_candidate_id=bad_candidate.candidate_id,
    )
    assert result.approved is False
    assert bad_candidate.candidate_id in result.rejected_candidates
    assert any("complete known" in reason for reason in result.reasons)


def test_prepare_uses_local_protocol_and_emits_no_coverage_requirements(
    monkeypatch,
) -> None:
    extension = no_coverage._NoCoverageExtension(_context())
    captured = {}

    class FakeTools:
        def action(self, description, *, param_model, terminates_sequence):
            captured["description"] = description
            captured["param_model"] = param_model
            captured["terminates_sequence"] = terminates_sequence

            def decorate(function):
                captured["function"] = function
                return function

            return decorate

    async def fake_compile():
        return _contract()

    monkeypatch.setattr(extension, "_compile_contract", fake_compile)
    task_text, tools, kwargs = asyncio.run(
        extension.prepare(
            ctx=_context(),
            llm=object(),
            browser_session=object(),
            tools=FakeTools(),
            task_text="unchanged task text",
        )
    )

    assert task_text == "unchanged task text"
    assert tools is extension.tools
    assert captured["param_model"] is no_coverage._LocalDecisionCheckpoint
    assert captured["terminates_sequence"] is True
    protocol = kwargs["extend_system_message"]
    assert "Literal TaskContract:" in protocol
    assert "submitted local set" in protocol
    for forbidden in (
        "advertised_total",
        "finite_pages",
        "advertised option total",
        "pager",
        "exhausted",
        "inspected_count",
        "excluded_count",
        "unresolved_count",
    ):
        assert forbidden not in protocol


def test_stats_explicitly_identify_ablation_and_omit_frontier_metrics() -> None:
    extension = no_coverage._NoCoverageExtension(_context())
    extension.contract = _contract()
    stats = extension.stats_snapshot()
    assert stats["component_version"] == "no-coverage-v1"
    assert stats["global_coverage_required"] is False
    assert stats["hard_constraints_enforced"] is True
    assert stats["ranking_policy"] == "literal_contract_criteria_only"
    assert not any(name.startswith("frontier_") for name in stats)


def test_scaffold_uses_production_browseruse_transport(
    monkeypatch,
) -> None:
    sentinel = RawTrajectory(answer="common transport")
    captured = {}

    async def fake_run(ctx, *, extension):
        captured["ctx"] = ctx
        captured["extension"] = extension
        return sentinel

    monkeypatch.setattr(no_coverage, "_browseruse_run", fake_run)
    ctx = _context()
    result = no_coverage.BrowserUseDeliberativeNoCoverageScaffold().run(ctx)
    assert result is sentinel
    assert captured["ctx"] is ctx
    assert isinstance(captured["extension"], no_coverage._NoCoverageExtension)
