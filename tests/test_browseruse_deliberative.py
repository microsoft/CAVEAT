from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from agentarena.scaffolds import browseruse_deliberative as module
from agentarena.scaffolds._deliberative_core import (
    Constraint,
    Objective,
    TaskContract,
)


def _extension(tmp_path, instruction="Choose the lightest option."):
    ctx = SimpleNamespace(
        task=SimpleNamespace(instruction=instruction),
        start_url="https://example.test/start",
        work_dir=tmp_path,
    )
    return module._DeliberativeExtension(ctx)


def _contract(instruction="Choose the lightest option."):
    return TaskContract(
        instruction,
        constraints=(
            Constraint("budget", "at most 100 USD", "le", 100, "USD"),
        ),
        objectives=(
            Objective("mass", "minimize mass", "minimize", "kg"),
        ),
    )


def _checkpoint(*, proposed="a", unresolved=0):
    advertised = 2 + unresolved
    return module._DecisionCheckpoint(
        frontier=module._FrontierInput(
            inspected_count=advertised,
            advertised_count=advertised,
            excluded_count=1,
            unresolved_count=unresolved,
            exhausted=True,
            basis=f"Showing all {advertised:,} results",
        ),
        candidates=[
            module._CandidateInput(
                id="a",
                label="A",
                source_url="https://example.test/a",
                facts=[
                    module._FactInput(
                        criterion_id="budget",
                        state="known",
                        value=80,
                        unit="USD",
                    ),
                    module._FactInput(
                        criterion_id="mass",
                        state="known",
                        value=1,
                        unit="kg",
                    ),
                ],
            ),
        ],
        proposed_candidate_id=proposed,
    )


def _finite_checkpoint(*, proposed="a"):
    params = _checkpoint(proposed=proposed)
    return params.model_copy(
        update={
            "frontier": module._FrontierInput(
                inspected_count=2,
                advertised_count=None,
                coverage_mode="finite_pages",
                advertised_page_count=2,
                enumerated_page_count=2,
                excluded_count=1,
                unresolved_count=0,
                exhausted=True,
                basis="Page: 1 2",
            )
        }
    )


class _FakeTools:
    def __init__(self):
        self.actions = []

    def action(self, description, **kwargs):
        def decorate(function):
            self.actions.append((description, kwargs, function))
            return function

        return decorate


class _FakePage:
    def __init__(self, text):
        self.text = text
        self.expressions = []

    async def evaluate(self, expression):
        self.expressions.append(expression)
        return self.text


class _FakeBrowserSession:
    def __init__(self, url, text):
        self.url = url
        self.page = _FakePage(text)
        self.page_calls = 0

    async def get_current_page_url(self):
        return self.url

    async def must_get_current_page(self):
        self.page_calls += 1
        return self.page


def _run_checkpoint(extension, params):
    return extension._checkpoint(
        params,
        current_url="https://example.test/search",
        rendered_page_text=params.frontier.basis,
    )


def test_source_is_domain_neutral_and_defines_exactly_one_tool():
    source = inspect.getsource(module).casefold()
    forbidden = (
        "checkout",
        "cart",
        "purchase",
        "confirmation",
        "asin",
        "hero",
        "sf-client",
        "storefront",
        "/api/",
        "requestwillbesent",
        "_wrap_builtin_actions",
        "_fetch_public",
    )
    assert not [token for token in forbidden if token in source]
    assert source.count("@tools.action(") == 1
    for removed_name in (
        "archive_current_page",
        "inspect_evidence",
        "record_candidates",
        "set_coverage",
        "decision_status",
        "review_choice",
    ):
        assert removed_name not in source


def test_contract_compiler_materializes_only_literal_fields(tmp_path):
    extension = _extension(
        tmp_path,
        "Choose the lightest option under 100 USD with feature X.",
    )

    async def structured(*args, **kwargs):
        return module._DraftContract(
            constraints=[
                module._DraftConstraint(
                    criterion_id="budget",
                    description="at most 100 USD",
                    operator="le",
                    expected=100,
                    unit="USD",
                ),
                module._DraftConstraint(
                    criterion_id="feature_x",
                    description="must have feature X",
                    operator="eq",
                    expected=True,
                    unit="boolean",
                ),
            ],
            objectives=[
                module._DraftObjective(
                    criterion_id="mass",
                    description="minimize mass",
                    direction="minimize",
                    unit="kg",
                )
            ],
        )

    extension._structured = structured
    contract = asyncio.run(extension._compile_contract())
    assert contract.objectives[0].criterion_id == "mass"
    assert contract.objectives[0].priority is None
    assert contract.objectives[0].weight is None
    assert contract.constraints[1].unit is None
    assert extension.contract_compile_calls == 1
    assert extension.contract_compile_attempts == 1
    extension.contract = contract
    stats = extension.stats_snapshot()
    assert stats["contract_sha256"] == contract.fingerprint
    assert stats["constraint_count"] == 2
    assert stats["objective_count"] == 1
    assert stats["search_mode"] == "best_available"


def test_contract_compiler_prompt_separates_options_from_execution(tmp_path):
    extension = _extension(
        tmp_path,
        "Order two items now; choose the lightest qualifying option.",
    )
    captured = {}

    async def structured(*args, **kwargs):
        captured.update(kwargs)
        return module._DraftContract(
            objectives=[
                module._DraftObjective(
                    criterion_id="mass",
                    description="minimize mass",
                    direction="minimize",
                    unit="kg",
                )
            ]
        )

    extension._structured = structured
    contract = asyncio.run(extension._compile_contract())
    prompt = captured["system"]
    assert "property of each candidate option before any action" in prompt
    assert "how many units to buy" in prompt
    assert "whether or when to submit an order" in prompt
    assert "pack size, capacity" in prompt
    assert "delivery or arrival time" in prompt
    assert contract.criterion_ids == ("mass",)


def test_contract_compiler_retries_semantically_invalid_draft(tmp_path):
    extension = _extension(tmp_path)
    calls = 0

    async def structured(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return module._DraftContract()
        return module._DraftContract(
            objectives=[
                module._DraftObjective(
                    criterion_id="mass",
                    description="minimize mass",
                    direction="minimize",
                    unit="kg",
                )
            ]
        )

    extension._structured = structured
    contract = asyncio.run(extension._compile_contract())
    assert contract.criterion_ids == ("mass",)
    assert extension.contract_compile_attempts == 2
    assert extension.contract_compile_rejections == 1
    assert extension.contract_compile_failures == 0


def test_contract_compiler_retries_invalid_in_unit_contract(tmp_path):
    extension = _extension(tmp_path)
    calls = 0

    async def structured(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return module._DraftContract(
                constraints=[
                    module._DraftConstraint(
                        criterion_id="color",
                        description="allowed colors",
                        operator="in",
                        expected=["red", "blue"],
                        unit="kg",
                    )
                ],
                search_mode="satisfice",
            )
        return module._DraftContract(
            constraints=[
                module._DraftConstraint(
                    criterion_id="color",
                    description="allowed colors",
                    operator="in",
                    expected=["red", "blue"],
                )
            ],
            search_mode="satisfice",
        )

    extension._structured = structured
    contract = asyncio.run(extension._compile_contract())
    assert contract.criterion_ids == ("color",)
    assert extension.contract_compile_attempts == 2
    assert extension.contract_compile_rejections == 1


def test_success_on_final_compile_attempt_touches_attempt_ceiling(tmp_path):
    extension = _extension(tmp_path)
    calls = 0

    async def structured(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls < extension._MAX_COMPILE_ATTEMPTS:
            raise RuntimeError("transient structured response failure")
        return module._DraftContract(
            objectives=[
                module._DraftObjective(
                    criterion_id="mass",
                    description="minimize mass",
                    direction="minimize",
                    unit="kg",
                )
            ]
        )

    extension._structured = structured
    contract = asyncio.run(extension._compile_contract())
    extension.contract = contract
    stats = extension.stats_snapshot()
    observation = stats["limit_observations"][
        "structured_response_attempts"
    ]

    assert stats["structured_max_attempts_observed"] == 4
    assert stats["structured_attempt_exhaustions"] == 0
    assert observation["touched_count"] == 1
    assert observation["observations"] == {
        "max_attempts": 4,
        "attempts": 4,
        "rejected_attempts": 3,
        "exhaustions": 0,
    }


def test_checkpoint_approves_only_exact_deterministic_winner(tmp_path):
    extension = _extension(tmp_path)
    extension.contract = _contract()
    approved = _run_checkpoint(extension, _checkpoint())
    assert approved.approved
    assert approved.selected_candidate_id == "a"
    assert approved.method == "pareto_dominant"
    assert extension.approved_candidate_id == "a"
    assert extension.decision_checkpoint_calls == 1
    assert extension.decision_checkpoint_approvals == 1
    assert extension.decision_checkpoint_rejections == 0

    rejected = _run_checkpoint(extension, _checkpoint(proposed="b"))
    assert not rejected.approved
    assert rejected.selected_candidate_id == "a"
    assert extension.approved_candidate_id is None
    assert extension.decision_checkpoint_calls == 2
    assert extension.decision_checkpoint_approvals == 1
    assert extension.decision_checkpoint_rejections == 1
    stats = extension.stats_snapshot()
    assert stats["checkpoint_candidate_count"] == 1
    assert stats["frontier_inspected_count"] == 2
    assert stats["frontier_advertised_count"] == 2
    assert stats["frontier_coverage_mode"] == "advertised_total"
    assert stats["frontier_advertised_page_count"] == 0
    assert stats["frontier_enumerated_page_count"] == 0


def test_checkpoint_accepts_and_records_finite_pages(tmp_path):
    extension = _extension(tmp_path)
    extension.contract = _contract()
    result = _run_checkpoint(extension, _finite_checkpoint())
    assert result.approved
    stats = extension.stats_snapshot()
    assert stats["frontier_coverage_mode"] == "finite_pages"
    assert stats["frontier_advertised_count"] == 0
    assert stats["frontier_advertised_page_count"] == 2
    assert stats["frontier_enumerated_page_count"] == 2


def test_checkpoint_unknown_frontier_blocks_best_available(tmp_path):
    extension = _extension(tmp_path)
    extension.contract = _contract()
    result = _run_checkpoint(extension, _checkpoint(unresolved=1))
    assert not result.approved
    assert "unresolved_count=0" in " ".join(result.reasons)
    assert extension.decision_checkpoint_rejections == 1


def test_checkpoint_handler_captures_fresh_same_origin_inner_text(tmp_path):
    extension = _extension(tmp_path)
    extension.contract = _contract()
    params = _checkpoint()
    browser = _FakeBrowserSession(
        "https://example.test/search?page=1",
        f"Search page\n{params.frontier.basis}\nProducts",
    )
    tools = _FakeTools()
    extension.browser_session = browser
    extension.tools = tools
    extension._install_tools()

    _, _, action = tools.actions[0]
    result = asyncio.run(action(params))
    assert result.error is None
    assert extension.approved_candidate_id == "a"
    assert browser.page_calls == 1
    assert browser.page.expressions == [
        "() => document.body ? document.body.innerText : ''"
    ]

    outside = _FakeBrowserSession(
        "https://other.test/search",
        params.frontier.basis,
    )
    extension.browser_session = outside
    result = asyncio.run(action(params))
    assert "outside the assigned origin" in result.error
    assert outside.page_calls == 0
    assert extension.decision_checkpoint_calls == 2
    assert extension.decision_checkpoint_approvals == 1
    assert extension.decision_checkpoint_rejections == 1
    assert extension.approved_candidate_id is None


def test_frontier_inputs_require_strict_integer_counts():
    common = {
        "advertised_count": 1,
        "excluded_count": 0,
        "unresolved_count": 0,
        "exhausted": True,
        "basis": "1 result",
    }
    for value in (True, 1.0, "1"):
        with pytest.raises(ValidationError):
            module._FrontierInput(inspected_count=value, **common)
        for name in (
            "advertised_page_count",
            "enumerated_page_count",
        ):
            fields = {
                "inspected_count": 1,
                "advertised_count": None,
                "coverage_mode": "finite_pages",
                "advertised_page_count": 2,
                "enumerated_page_count": 2,
                "excluded_count": 0,
                "unresolved_count": 0,
                "exhausted": True,
                "basis": "Page: 1 2",
                name: value,
            }
            with pytest.raises(ValidationError):
                module._FrontierInput(**fields)


def test_frontier_exhaustion_and_compiler_priority_are_strict():
    with pytest.raises(ValidationError):
        module._FrontierInput(
            inspected_count=1,
            advertised_count=1,
            excluded_count=0,
            unresolved_count=0,
            exhausted=1,
            basis="1 result",
        )
    with pytest.raises(ValidationError):
        module._DraftObjective(
            criterion_id="quality",
            description="maximize quality",
            direction="maximize",
            priority=True,
        )


def test_origin_renders_ipv6_authority_with_brackets():
    assert module._origin("http://[::1]:8000/start") == (
        "http://[::1]:8000"
    )
    assert module._origin("https://[2001:db8::1]/start") == (
        "https://[2001:db8::1]"
    )


def test_install_registers_one_terminating_checkpoint(tmp_path):
    extension = _extension(tmp_path)
    tools = _FakeTools()
    extension.tools = tools
    extension._install_tools()
    assert len(tools.actions) == 1
    description, options, function = tools.actions[0]
    assert function.__name__ == "decision_checkpoint"
    assert options["param_model"] is module._DecisionCheckpoint
    assert options["terminates_sequence"] is True
    assert "exact best candidate" in description
    assert "rendered coverage page" in description


def test_prepare_adds_concise_general_protocol(tmp_path):
    extension = _extension(tmp_path)
    tools = _FakeTools()

    async def compile_contract():
        return _contract()

    extension._compile_contract = compile_contract
    task, returned_tools, settings = asyncio.run(
        extension.prepare(
            ctx=None,
            llm=object(),
            browser_session=object(),
            tools=tools,
            task_text="raw task",
        )
    )
    protocol = settings["extend_system_message"]
    assert task == "raw task"
    assert returned_tools is tools
    assert len(tools.actions) == 1
    assert "Promotion, placement" in protocol
    assert "seller persona" in protocol
    assert "fully map the reachable finite option set" in protocol
    assert "exactly Pareto-dominated options as excluded" in protocol
    assert "Unknown or conflicting" in protocol
    assert "advertised_total" in protocol
    assert "finite_pages" in protocol
    assert "every page 1 through P" in protocol
    assert "deduplicate stable option identities" in protocol
    assert "call decision_checkpoint alone" in protocol
    assert "After approval, re-read" in protocol
    assert "later consequential action" in protocol


def test_stats_are_small_and_have_no_archive_or_reviewer_layer(tmp_path):
    extension = _extension(tmp_path)
    stats = extension.stats_snapshot()
    assert {
        "contract_compile_calls",
        "contract_compile_attempts",
        "contract_compile_rejections",
        "contract_compile_failures",
        "contract_sha256",
        "constraint_count",
        "objective_count",
        "search_mode",
        "structured_max_attempts_observed",
        "structured_attempt_exhaustions",
        "decision_checkpoint_calls",
        "decision_checkpoint_rejections",
        "decision_checkpoint_approvals",
        "checkpoint_candidate_count",
        "frontier_inspected_count",
        "frontier_advertised_count",
        "frontier_coverage_mode",
        "frontier_advertised_page_count",
        "frontier_enumerated_page_count",
        "approved_candidate_id",
    } <= stats.keys()
    assert not [key for key in stats if "archive" in key or "review" in key]
