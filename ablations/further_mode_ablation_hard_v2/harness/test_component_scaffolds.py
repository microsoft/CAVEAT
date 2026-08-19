from __future__ import annotations

import asyncio
import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import arm_registry
import component_scaffolds as components
import no_coverage_scaffold
from agentarena.core.models import ModelSpec
from agentarena.core.scaffold import RawTrajectory, RunContext, SCAFFOLDS
from agentarena.core.task import TaskSpec
from agentarena.scaffolds._deliberative_core import (
    Candidate,
    CandidateFact,
    Constraint,
    ConstraintOperator,
    CoverageMode,
    FactState,
    Frontier,
    Objective,
    ObjectiveDirection,
    SearchMode,
    TaskContract,
)
from agentarena.scaffolds.browseruse_deliberative import (
    _DecisionCheckpoint,
    _DeliberativeExtension,
)
from prompt_only_scaffold import PROMPT_ONLY_GUIDANCE


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PRODUCTION_HASHES = {
    "agentarena/scaffolds/browseruse.py": (
        "b53496d49ef6e198df14d61ba3dc61c1ac0ace6785559a8fa37d36e2641a4390"
    ),
    "agentarena/scaffolds/browseruse_deliberative.py": (
        "6336e052bd94617dabea9ea0a7fb4b3c23923728b90f09abab4040d5146ad3ed"
    ),
    "agentarena/scaffolds/_deliberative_core.py": (
        "9800f3a408c5c966b5bd01b3453ea7ab60e41c80b7420531aa1cf68211885cbb"
    ),
    "ablations/prompt_only/prompt_only_scaffold.py": (
        "c05562a7769972884d1113f076c5352fd28377b99b5a0f50072a3f71273d9999"
    ),
    "ablations/harness_components/no_coverage_scaffold.py": (
        "b0baa1ad01512ea297072976d63386e22924eeb5613c40e2d036229ca2cae115"
    ),
}


def _context() -> RunContext:
    task = TaskSpec(
        task_id="component-test",
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
        work_dir=Path("/tmp/further-harness-component-test"),
        max_steps=12_000,
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
    ram: int = 16,
    price: int = 900,
    battery: int = 10,
    price_unit: str = "USD",
    battery_state: FactState = FactState.KNOWN,
) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        label=f"Laptop {candidate_id}",
        source_url=f"http://127.0.0.1:9999/items/{candidate_id}",
        facts=(
            CandidateFact("ram", FactState.KNOWN, ram, "GB"),
            CandidateFact("price", FactState.KNOWN, price, price_unit),
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


def _decision_params(
    candidates: list[Candidate],
    proposed: str,
    *,
    inspected: int,
    advertised: int | None,
    basis: str,
    excluded: int = 0,
) -> _DecisionCheckpoint:
    return _DecisionCheckpoint.model_validate(
        {
            "frontier": {
                "inspected_count": inspected,
                "advertised_count": advertised,
                "coverage_mode": "advertised_total",
                "advertised_page_count": None,
                "enumerated_page_count": None,
                "excluded_count": excluded,
                "unresolved_count": 0,
                "exhausted": True,
                "basis": basis,
            },
            "candidates": [_input_candidate(item) for item in candidates],
            "proposed_candidate_id": proposed,
        }
    )


def test_source_bound_existing_profiles_are_unchanged() -> None:
    for relative, expected in PRODUCTION_HASHES.items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert actual == expected, relative


def test_all_eight_profiles_resolve_to_exact_registered_classes() -> None:
    record = arm_registry.validate_command_local_registration()
    assert record["arm_order"] == ["B", "P", "K", "E", "D", "C", "A", "F"]
    assert record["scaffolds"] == {
        "B": "browseruse",
        "P": "browseruse-prompt-only",
        "K": "browseruse-deliberative-contract-only",
        "E": "browseruse-deliberative-feasibility",
        "D": "browseruse-deliberative-no-coverage",
        "C": "browseruse-deliberative-coverage-only",
        "A": "browseruse-deliberative-coverage-advisory",
        "F": "browseruse-deliberative",
    }
    assert SCAFFOLDS.get(record["scaffolds"]["D"]) is (
        no_coverage_scaffold.BrowserUseDeliberativeNoCoverageScaffold
    )


def test_unknown_arm_fails_closed() -> None:
    with pytest.raises(KeyError, match="unknown harness ablation arm"):
        arm_registry.scaffold_for_arm("unknown")


def test_metadata_freezes_the_prespecified_incremental_profiles() -> None:
    metadata = arm_registry.arm_metadata()
    components_by_arm = {
        arm: row["components"] for arm, row in metadata.items()
    }

    def changed(left: str, right: str) -> set[str]:
        return {
            key
            for key, value in components_by_arm[left].items()
            if components_by_arm[right][key] != value
        }

    assert changed("P", "B") == {"general_deliberative_guidance"}
    assert changed("K", "P") == {"same_model_contract_compilation"}
    assert changed("E", "K") == {"fact_and_feasibility_gate"}
    assert changed("D", "E") == {"local_exact_choice_gate"}
    assert changed("C", "P") == {
        "global_coverage_payload",
        "global_coverage_enforced",
    }
    assert changed("A", "D") == {
        "global_coverage_payload",
        "global_coverage_shadowed",
    }
    assert changed("F", "A") == {
        "global_coverage_enforced",
        "global_coverage_shadowed",
    }
    assert "not a token-identical" in metadata["D"]["contrast_scope"]


def test_registration_is_strictly_command_local() -> None:
    code = (
        "import json,agentarena.scaffolds;"
        "from agentarena.core.scaffold import SCAFFOLDS;"
        "print(json.dumps(SCAFFOLDS.names()))"
    )
    clean_env = dict(os.environ)
    clean_env.pop("PYTHONPATH", None)
    clean = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=clean_env,
        check=True,
        capture_output=True,
        text=True,
    )
    clean_names = set(json.loads(clean.stdout))
    for arm in ("P", "K", "E", "D", "C", "A"):
        assert arm_registry.scaffold_for_arm(arm) not in clean_names

    local_env = dict(clean_env)
    local_env["PYTHONPATH"] = str(HERE)
    local = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=local_env,
        check=True,
        capture_output=True,
        text=True,
    )
    local_names = set(json.loads(local.stdout))
    assert {
        arm_registry.scaffold_for_arm(arm)
        for arm in arm_registry.ARM_ORDER
    }.issubset(local_names)


def test_contract_only_reuses_compiler_and_installs_no_checkpoint(
    monkeypatch,
) -> None:
    extension = components._ContractOnlyExtension(_context())
    assert extension._compile_contract.__func__ is (
        _DeliberativeExtension._compile_contract
    )

    async def fake_compile():
        return _contract()

    monkeypatch.setattr(extension, "_compile_contract", fake_compile)
    tools = object()
    task, returned_tools, kwargs = asyncio.run(
        extension.prepare(
            ctx=_context(),
            llm=object(),
            browser_session=object(),
            tools=tools,
            task_text="original task",
        )
    )
    assert task == f"original task\n\n{PROMPT_ONLY_GUIDANCE}"
    assert returned_tools is tools
    assert "Literal TaskContract:" in kwargs["extend_system_message"]
    stats = extension.stats_snapshot()
    assert stats["checkpoint_enabled"] is False
    assert stats["decision_checkpoint_calls"] == 0


@pytest.mark.parametrize(
    ("candidate", "reason_fragment", "rejection_fragment"),
    [
        (
            _candidate("unknown", battery_state=FactState.UNKNOWN),
            "unknown",
            "criterion battery is unknown",
        ),
        (
            _candidate("conflict", battery_state=FactState.CONFLICT),
            "conflicting",
            "criterion battery is conflict",
        ),
        (
            _candidate("incompatible", price_unit="h"),
            "incompatible",
            "objective price is incompatible",
        ),
        (
            _candidate("infeasible", ram=8),
            "hard-infeasible",
            "constraint ram is not satisfied",
        ),
    ],
    ids=["unknown", "conflict", "incompatible", "infeasible"],
)
def test_feasibility_gate_rejects_invalid_proposed_item(
    candidate: Candidate,
    reason_fragment: str,
    rejection_fragment: str,
) -> None:
    result = components.evaluate_feasibility(
        contract=_contract(),
        start_origin="http://127.0.0.1:9999",
        current_url="http://127.0.0.1:9999/compare",
        proposed_candidate=candidate,
    )
    assert result.approved is False
    assert candidate.candidate_id in result.rejected_candidates
    assert any(reason_fragment in reason for reason in result.reasons)
    assert any(
        rejection_fragment in reason
        for reason in result.rejected_candidates[candidate.candidate_id]
    )


def test_feasibility_gate_approves_feasible_nonbest_but_D_rejects_it() -> None:
    best = _candidate("best", price=800, battery=12)
    nonbest = _candidate("nonbest", price=1200, battery=7)
    feasibility = components.evaluate_feasibility(
        contract=_contract(),
        start_origin="http://127.0.0.1:9999",
        current_url="http://127.0.0.1:9999/compare",
        proposed_candidate=nonbest,
    )
    assert feasibility.approved is True
    local_choice = no_coverage_scaffold.evaluate_local_choice(
        contract=_contract(),
        start_origin="http://127.0.0.1:9999",
        current_url="http://127.0.0.1:9999/compare",
        candidates=(best, nonbest),
        proposed_candidate_id="nonbest",
    )
    assert local_choice.approved is False
    assert any("exact best" in reason for reason in local_choice.reasons)


def test_coverage_only_accepts_exact_advertised_total_without_facts() -> None:
    frontier = Frontier(
        inspected_count=2,
        advertised_count=2,
        coverage_mode=CoverageMode.ADVERTISED_TOTAL,
        excluded_count=0,
        unresolved_count=0,
        exhausted=True,
        basis="2 results",
    )
    result = components.evaluate_coverage(
        start_origin="http://127.0.0.1:9999",
        current_url="http://127.0.0.1:9999/results",
        rendered_page_text="Laptops\n2 results\n",
        frontier=frontier,
        candidate_ids=("a", "b"),
        proposed_candidate_id="b",
    )
    assert result.approved is True


def test_coverage_only_rejects_incomplete_pager() -> None:
    frontier = Frontier(
        inspected_count=2,
        advertised_count=None,
        coverage_mode=CoverageMode.FINITE_PAGES,
        advertised_page_count=3,
        enumerated_page_count=3,
        excluded_count=0,
        unresolved_count=0,
        exhausted=True,
        basis="Pages 1 2",
    )
    result = components.evaluate_coverage(
        start_origin="http://127.0.0.1:9999",
        current_url="http://127.0.0.1:9999/results?page=2",
        rendered_page_text="Laptops\nPages 1 2\n",
        frontier=frontier,
        candidate_ids=("a", "b"),
        proposed_candidate_id="a",
    )
    assert result.approved is False
    assert any("every page integer" in reason for reason in result.reasons)


def test_coverage_only_schema_has_no_fact_or_contract_channel() -> None:
    schema = components._CoverageCheckpoint.model_json_schema()
    assert set(schema["properties"]) == {
        "frontier",
        "candidate_ids",
        "proposed_candidate_id",
    }
    assert schema["properties"]["candidate_ids"]["items"] == {
        "type": "string"
    }
    definition_names = set(schema.get("$defs", {}))
    assert not any("candidate" in name.casefold() for name in definition_names)


def test_coverage_only_reports_exact_zero_structured_attempt_inventory() -> None:
    """The hard-v2 fix is complete audit telemetry, not behavior."""

    extension = components._CoverageOnlyExtension(_context())
    stats = extension.stats_snapshot()
    assert stats["contract_compile_calls"] == 0
    assert stats["limit_observations"] == {
        "structured_response_attempts": {
            "touched_count": 0,
            "observations": {
                "max_attempts": 4,
                "attempts": 0,
                "rejected_attempts": 0,
                "exhaustions": 0,
            },
        },
    }


def test_component_source_delta_is_only_version_and_c_audit_dict_entry() -> None:
    """Prove the recovery did not patch an agent-visible behavior path."""

    old_path = ROOT / "ablations/further_mode_ablation/harness/component_scaffolds.py"
    new_path = HERE / "component_scaffolds.py"
    old = ast.parse(old_path.read_text())
    new = ast.parse(new_path.read_text())

    def normalize(tree: ast.Module) -> str:
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign)
                    else [node.target]
                )
                if any(
                    isinstance(target, ast.Name)
                    and target.id == "COMPONENT_VERSION"
                    for target in targets
                ):
                    node.value = ast.Constant(value="NORMALIZED_VERSION")
            if isinstance(node, ast.ClassDef) and node.name == \
                    "_CoverageOnlyExtension":
                stats = next(
                    item for item in node.body
                    if isinstance(item, ast.FunctionDef)
                    and item.name == "stats_snapshot"
                )
                returned = next(
                    item.value for item in ast.walk(stats)
                    if isinstance(item, ast.Return)
                )
                assert isinstance(returned, ast.Dict)
                retained = [
                    (key, value) for key, value in zip(
                        returned.keys, returned.values
                    )
                    if not (
                        isinstance(key, ast.Constant)
                        and key.value == "limit_observations"
                    )
                ]
                returned.keys = [key for key, _ in retained]
                returned.values = [value for _, value in retained]
        return ast.dump(tree, include_attributes=False)

    assert normalize(new) == normalize(old)


def test_advisory_logs_failed_full_shadow_without_rejecting_local_choice() -> None:
    winner = _candidate("winner", price=800, battery=12)
    dominated = _candidate("dominated", price=1200, battery=7)
    params = _decision_params(
        [winner, dominated],
        "winner",
        inspected=2,
        advertised=3,
        basis="3 results",
    )
    extension = components._CoverageAdvisoryExtension(_context())
    extension.contract = _contract()
    result = extension._advisory_checkpoint(
        params,
        current_url="http://127.0.0.1:9999/results",
        rendered_page_text="Laptops\n3 results\n",
    )
    assert result.approved is True
    assert result.reasons == ()
    stats = extension.stats_snapshot()
    assert stats["first_submission_would_pass_full"] is False
    assert stats["last_submission_would_pass_full"] is False
    assert stats["coverage_shadow_failures"] == 1
    assert stats["decision_checkpoint_approvals"] == 1


def test_advisory_exposes_the_exact_full_prompt_and_payload_shape(
    monkeypatch,
) -> None:
    class FakeTools:
        def __init__(self):
            self.registrations = []

        def action(self, description, *, param_model, terminates_sequence):
            self.registrations.append(
                (description, param_model, terminates_sequence)
            )

            def decorate(function):
                return function

            return decorate

    advisory = components._CoverageAdvisoryExtension(_context())
    full = _DeliberativeExtension(_context())

    async def advisory_compile():
        return _contract()

    async def full_compile():
        return _contract()

    monkeypatch.setattr(advisory, "_compile_contract", advisory_compile)
    monkeypatch.setattr(full, "_compile_contract", full_compile)
    advisory_tools = FakeTools()
    full_tools = FakeTools()
    advisory_result = asyncio.run(
        advisory.prepare(
            ctx=_context(),
            llm=object(),
            browser_session=object(),
            tools=advisory_tools,
            task_text="task",
        )
    )
    full_result = asyncio.run(
        full.prepare(
            ctx=_context(),
            llm=object(),
            browser_session=object(),
            tools=full_tools,
            task_text="task",
        )
    )
    assert advisory_result[0] == full_result[0] == "task"
    assert advisory_result[2] == full_result[2]
    assert advisory_tools.registrations == full_tools.registrations
    assert advisory_tools.registrations[0][1] is _DecisionCheckpoint


def test_advisory_preserves_first_shadow_verdict_and_enforces_local_ranking() -> None:
    winner = _candidate("winner", price=800, battery=12)
    dominated = _candidate("dominated", price=1200, battery=7)
    extension = components._CoverageAdvisoryExtension(_context())
    extension.contract = _contract()

    bad_first = _decision_params(
        [winner, dominated],
        "dominated",
        inspected=2,
        advertised=3,
        basis="3 results",
    )
    rejected = extension._advisory_checkpoint(
        bad_first,
        current_url="http://127.0.0.1:9999/results",
        rendered_page_text="3 results",
    )
    assert rejected.approved is False

    valid_full = _decision_params(
        [winner],
        "winner",
        inspected=2,
        advertised=2,
        basis="2 results",
        excluded=1,
    )
    approved = extension._advisory_checkpoint(
        valid_full,
        current_url="http://127.0.0.1:9999/results",
        rendered_page_text="2 results",
    )
    assert approved.approved is True
    stats = extension.stats_snapshot()
    assert stats["first_submission_would_pass_full"] is False
    assert stats["last_submission_would_pass_full"] is True
    assert stats["coverage_shadow_calls"] == 2
    assert stats["coverage_shadow_passes"] == 1
    assert stats["coverage_shadow_failures"] == 1


@pytest.mark.parametrize(
    "scaffold_type,extension_type",
    [
        (
            components.BrowserUseDeliberativeContractOnlyScaffold,
            components._ContractOnlyExtension,
        ),
        (
            components.BrowserUseDeliberativeFeasibilityScaffold,
            components._FeasibilityExtension,
        ),
        (
            components.BrowserUseDeliberativeCoverageOnlyScaffold,
            components._CoverageOnlyExtension,
        ),
        (
            components.BrowserUseDeliberativeCoverageAdvisoryScaffold,
            components._CoverageAdvisoryExtension,
        ),
    ],
)
def test_new_profiles_use_unchanged_browseruse_transport(
    monkeypatch, scaffold_type, extension_type
) -> None:
    sentinel = RawTrajectory(answer="common transport")
    captured = {}

    async def fake_run(ctx, *, extension):
        captured["ctx"] = ctx
        captured["extension"] = extension
        return sentinel

    monkeypatch.setattr(components, "_browseruse_run", fake_run)
    ctx = _context()
    result = scaffold_type().run(ctx)
    assert result is sentinel
    assert captured["ctx"] is ctx
    assert isinstance(captured["extension"], extension_type)
