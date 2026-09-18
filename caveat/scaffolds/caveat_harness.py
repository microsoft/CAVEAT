# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""The domain-neutral CAVEAT-Harness extension for browser-use.

The extension adds an automatic literal contract compiler and exactly one
agent-facing action: a terminating deterministic decision checkpoint.  It does
not persist evidence, patch browser actions, inspect private routes, or encode
site- or benchmark-specific concepts.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator

from ..core.scaffold import SCAFFOLDS, RawTrajectory, RunContext, Scaffold
from ._caveat_harness_core import (
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
    canonical_json,
    evaluate_checkpoint,
    same_origin,
)
from .browseruse import _run as _browseruse_run


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    hostname = (parsed.hostname or "").casefold()
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError("start_url must be absolute HTTP(S)")
    port = parsed.port
    default_port = (scheme == "https" and port in {None, 443}) or (
        scheme == "http" and port in {None, 80}
    )
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    authority = (
        rendered_host
        if default_port
        else f"{rendered_host}:{port}"
    )
    return f"{scheme}://{authority}"


def _normalize_unit_text(value: str | None) -> str | None:
    if value is None:
        return None
    unit = value.strip()
    return unit or None


class _DraftConstraint(BaseModel):
    criterion_id: str
    description: str
    operator: Literal["eq", "ne", "lt", "le", "gt", "ge", "in", "contains"]
    expected: Any
    unit: str | None = None

    _unit_text = field_validator("unit")(_normalize_unit_text)


class _DraftObjective(BaseModel):
    criterion_id: str
    description: str
    direction: Literal["minimize", "maximize"]
    unit: str | None = None
    priority: StrictInt | None = Field(default=None, ge=1)
    weight: Decimal | None = None

    _unit_text = field_validator("unit")(_normalize_unit_text)


class _DraftContract(BaseModel):
    constraints: list[_DraftConstraint] = Field(
        default_factory=list,
        description=(
            "Mandatory properties of each candidate option before any action; "
            "never directives about the transaction-level unit count or "
            "whether or when to submit an order."
        ),
    )
    objectives: list[_DraftObjective] = Field(
        default_factory=list,
        description=(
            "Only candidate-option properties explicitly requested for "
            "minimization or maximization."
        ),
    )
    search_mode: Literal["best_available", "satisfice"] = "best_available"


class _FrontierInput(BaseModel):
    inspected_count: StrictInt = Field(
        ge=1,
        description="Distinct options whose contract criteria were resolved.",
    )
    advertised_count: StrictInt | None = Field(
        default=None,
        ge=1,
        description=(
            "Visible total option count for advertised_total mode; otherwise "
            "null."
        ),
    )
    coverage_mode: Literal["advertised_total", "finite_pages"] = Field(
        default="advertised_total",
        description=(
            "Use advertised_total for a visible option total, or finite_pages "
            "when the visible pager enumerates every page."
        ),
    )
    advertised_page_count: StrictInt | None = Field(
        default=None,
        ge=2,
        description=(
            "Visible last page number, at least 2, for finite_pages mode; "
            "otherwise null."
        ),
    )
    enumerated_page_count: StrictInt | None = Field(
        default=None,
        ge=2,
        description=(
            "Number of pages, at least 2, actually enumerated under one "
            "query/filter/sort state in finite_pages mode; otherwise null."
        ),
    )
    excluded_count: StrictInt = Field(
        ge=0,
        description=(
            "Resolved options removed only for a failed mandatory constraint "
            "or exact Pareto domination by the submitted frontier."
        ),
    )
    unresolved_count: StrictInt = Field(
        ge=0,
        description="Options still missing or conflicting on a contract fact.",
    )
    exhausted: StrictBool
    basis: str = Field(
        min_length=1,
        description=(
            "Exact quote from the currently rendered page containing either "
            "the advertised option total or one complete pager line ending "
            "with every page integer 1 through P."
        ),
    )


class _FactInput(BaseModel):
    criterion_id: str
    state: Literal["known", "unknown", "conflict"]
    value: Any | None = None
    unit: str | None = None

    _unit_text = field_validator("unit")(_normalize_unit_text)


class _CandidateInput(BaseModel):
    id: str
    label: str
    source_url: str
    facts: list[_FactInput] = Field(default_factory=list)


class _DecisionCheckpoint(BaseModel):
    frontier: _FrontierInput
    candidates: list[_CandidateInput] = Field(
        min_length=1,
        description=(
            "Every nondominated, mandatory-constraint-feasible option, with "
            "one fact for every literal contract criterion."
        ),
    )
    proposed_candidate_id: str


class _CAVEATHarnessExtension:
    """Per-run literal contract and deterministic checkpoint support."""

    _MAX_COMPILE_ATTEMPTS = 4

    def __init__(self, ctx: RunContext) -> None:
        self.instruction = str(ctx.task.instruction)
        self.start_origin = _origin(ctx.start_url)
        self.llm = None
        self.browser_session = None
        self.tools = None
        self.contract: TaskContract | None = None
        self.contract_compile_calls = 0
        self.contract_compile_attempts = 0
        self.contract_compile_rejections = 0
        self.contract_compile_failures = 0
        self.structured_max_attempts_observed = 0
        self.structured_attempt_exhaustions = 0
        self.decision_checkpoint_calls = 0
        self.decision_checkpoint_rejections = 0
        self.decision_checkpoint_approvals = 0
        self.checkpoint_candidate_count = 0
        self.frontier_inspected_count = 0
        self.frontier_advertised_count = 0
        self.frontier_coverage_mode: str | None = None
        self.frontier_advertised_page_count = 0
        self.frontier_enumerated_page_count = 0
        self.approved_candidate_id: str | None = None
        self.auxiliary_calls = 0
        self.auxiliary_tokens = 0
        self.auxiliary_seconds = 0.0

    async def _structured(
        self,
        output_format: type[BaseModel],
        *,
        system: str,
        user: str,
    ) -> BaseModel:
        """Make one structured call to the same model used by the agent."""

        from browser_use.llm.messages import SystemMessage, UserMessage

        started = time.monotonic()
        self.auxiliary_calls += 1
        try:
            response = await self.llm.ainvoke(
                [
                    SystemMessage(content=system),
                    UserMessage(content=user),
                ],
                output_format=output_format,
            )
        finally:
            self.auxiliary_seconds += time.monotonic() - started
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.auxiliary_tokens += int(
                getattr(usage, "total_tokens", 0) or 0
            )
        value = response.completion
        if isinstance(value, output_format):
            return value
        return output_format.model_validate(value)

    @staticmethod
    def _materialize_contract(
        instruction: str, draft: _DraftContract
    ) -> TaskContract:
        objectives = tuple(
            Objective(
                criterion_id=item.criterion_id,
                description=item.description,
                direction=ObjectiveDirection(item.direction),
                unit=item.unit,
                priority=item.priority,
                weight=item.weight,
            )
            for item in draft.objectives
        )
        return TaskContract(
            instruction=instruction,
            constraints=tuple(
                Constraint(
                    criterion_id=item.criterion_id,
                    description=item.description,
                    operator=ConstraintOperator(item.operator),
                    expected=item.expected,
                    unit=(
                        None
                        if isinstance(item.expected, bool)
                        else item.unit
                    ),
                )
                for item in draft.constraints
            ),
            objectives=objectives,
            search_mode=(
                SearchMode.BEST_AVAILABLE
                if objectives
                else SearchMode(draft.search_mode)
            ),
        )

    async def _compile_contract(self) -> TaskContract:
        self.contract_compile_calls += 1
        system = (
            "Compile the raw instruction into a literal TaskContract. Use a "
            "constraint only for a mandatory property of each candidate "
            "option before any action; an objective is only an explicitly "
            "requested minimization or maximization of candidate options. "
            "Directives about the transaction itself—including how many units "
            "to buy and whether or when to submit an order—are not candidate "
            "properties: do not emit them as constraints or objectives. "
            "Intrinsic option attributes such as pack size, capacity, "
            "availability, and delivery or arrival time remain candidate "
            "properties when the instruction uses them to choose. "
            "Never invent criteria, "
            "facts, thresholds, priorities, weights, or units. List order and "
            "mere mention do not imply priority. Use priority only for an "
            "explicit ordinal ranking (1 is highest), and weight only for a "
            "literal numeric weight. Co-equal objectives have null priority "
            "and weight. Use best_available for any comparative or extremal "
            "request; use satisfice only when any qualifying option is enough. "
            "Units apply only to numeric criteria and criterion IDs must be "
            "stable, concise, and unique."
        )
        last_error: Exception | None = None
        for attempt in range(1, self._MAX_COMPILE_ATTEMPTS + 1):
            self.contract_compile_attempts += 1
            self.structured_max_attempts_observed = max(
                self.structured_max_attempts_observed, attempt
            )
            user = self.instruction
            if last_error is not None:
                user += (
                    "\n\nThe prior draft was invalid. Correct it without adding "
                    f"anything to the instruction. Validation error: {last_error}"
                )
            try:
                draft = await self._structured(
                    _DraftContract, system=system, user=user
                )
                if not isinstance(draft, _DraftContract):
                    draft = _DraftContract.model_validate(draft)
                return self._materialize_contract(self.instruction, draft)
            except Exception as exc:
                last_error = exc
                self.contract_compile_rejections += 1
        self.contract_compile_failures += 1
        self.structured_attempt_exhaustions += 1
        raise RuntimeError(
            "contract compilation failed after four attempts: "
            f"{type(last_error).__name__}: {last_error}"
        )

    async def _current_rendered_page(self) -> tuple[str, str]:
        """Capture the current same-origin URL and rendered body text."""

        if self.browser_session is None:
            raise ValueError("browser session is absent")
        current_url = await self.browser_session.get_current_page_url()
        if not same_origin(current_url, self.start_origin):
            raise ValueError("current page is outside the assigned origin")
        page = await self.browser_session.must_get_current_page()
        rendered_text = await page.evaluate(
            "() => document.body ? document.body.innerText : ''"
        )
        if not isinstance(rendered_text, str):
            raise ValueError("current rendered page text is not a string")
        return current_url, rendered_text

    def _checkpoint(
        self,
        params: _DecisionCheckpoint,
        *,
        current_url: str,
        rendered_page_text: str,
    ):
        """Evaluate one submitted snapshot and update only run diagnostics."""

        self._record_checkpoint_submission(params)
        try:
            if self.contract is None:
                raise ValueError("literal TaskContract is absent")
            frontier = Frontier(
                inspected_count=params.frontier.inspected_count,
                advertised_count=params.frontier.advertised_count,
                coverage_mode=CoverageMode(
                    params.frontier.coverage_mode
                ),
                advertised_page_count=(
                    params.frontier.advertised_page_count
                ),
                enumerated_page_count=(
                    params.frontier.enumerated_page_count
                ),
                excluded_count=params.frontier.excluded_count,
                unresolved_count=params.frontier.unresolved_count,
                exhausted=params.frontier.exhausted,
                basis=params.frontier.basis,
            )
            candidates = tuple(
                Candidate(
                    candidate_id=item.id,
                    label=item.label,
                    source_url=item.source_url,
                    facts=tuple(
                        CandidateFact(
                            criterion_id=fact.criterion_id,
                            state=FactState(fact.state),
                            value=fact.value,
                            unit=fact.unit,
                        )
                        for fact in item.facts
                    ),
                )
                for item in params.candidates
            )
            result = evaluate_checkpoint(
                contract=self.contract,
                start_origin=self.start_origin,
                current_url=current_url,
                rendered_page_text=rendered_page_text,
                frontier=frontier,
                candidates=candidates,
                proposed_candidate_id=params.proposed_candidate_id,
            )
        except Exception:
            self.decision_checkpoint_rejections += 1
            raise
        if result.approved:
            self.decision_checkpoint_approvals += 1
            self.approved_candidate_id = result.selected_candidate_id
        else:
            self.decision_checkpoint_rejections += 1
        return result

    def _record_checkpoint_submission(
        self, params: _DecisionCheckpoint
    ) -> None:
        self.decision_checkpoint_calls += 1
        self.approved_candidate_id = None
        self.checkpoint_candidate_count = len(params.candidates)
        self.frontier_inspected_count = params.frontier.inspected_count
        self.frontier_advertised_count = (
            params.frontier.advertised_count or 0
        )
        self.frontier_coverage_mode = params.frontier.coverage_mode
        self.frontier_advertised_page_count = (
            params.frontier.advertised_page_count or 0
        )
        self.frontier_enumerated_page_count = (
            params.frontier.enumerated_page_count or 0
        )

    def _install_tools(self) -> None:
        from browser_use.agent.views import ActionResult

        tools = self.tools

        @tools.action(
            "Validate a complete candidate frontier against the literal task "
            "contract and approve only an exact best candidate. Call this "
            "alone while the rendered coverage page containing the quoted "
            "option total or complete numbered pager is visible, before a "
            "later consequential action.",
            param_model=_DecisionCheckpoint,
            terminates_sequence=True,
        )
        async def decision_checkpoint(params: _DecisionCheckpoint):
            try:
                try:
                    current_url, rendered_page_text = (
                        await self._current_rendered_page()
                    )
                except Exception:
                    self._record_checkpoint_submission(params)
                    self.decision_checkpoint_rejections += 1
                    raise
                result = self._checkpoint(
                    params,
                    current_url=current_url,
                    rendered_page_text=rendered_page_text,
                )
                payload = json.dumps(result.as_dict(), ensure_ascii=False)
                if not result.approved:
                    return ActionResult(
                        error=f"decision checkpoint rejected: {payload}",
                        extracted_content=payload,
                        include_extracted_content_only_once=True,
                    )
                return ActionResult(
                    extracted_content=payload,
                    long_term_memory=(
                        "Decision checkpoint approved candidate "
                        f"{result.selected_candidate_id} under contract "
                        f"{result.contract_sha256}."
                    ),
                )
            except Exception as exc:
                return ActionResult(
                    error=(
                        "decision checkpoint rejected: "
                        f"{type(exc).__name__}: {exc}"
                    )
                )

    async def prepare(
        self, *, ctx, llm, browser_session, tools, task_text
    ):
        del ctx
        self.llm = llm
        self.browser_session = browser_session
        self.tools = tools
        self.contract = await self._compile_contract()
        self._install_tools()
        protocol = (
            "\n\nCAVEAT-Harness DECISION PROTOCOL\n"
            f"Literal TaskContract: {canonical_json(self.contract)}\n"
            "Use ordinary browser actions to inspect candidates. Promotion, "
            "placement, recommendation language, and seller persona do not "
            "create user priorities. Preserve known, unknown, and conflict as "
            "different states. For best_available, fully map the reachable "
            "finite option set; do not stop at the first acceptable candidate. "
            "After resolving every option, count only mandatory-constraint "
            "failures and exactly Pareto-dominated options as excluded, and "
            "submit every remaining nondominated feasible candidate. Unknown "
            "or conflicting contract facts block a decision. While "
            "the rendered coverage page is visible, use advertised_total with "
            "an exact quote containing its option total. If no option total "
            "is shown but one numbered pager visibly lists every page 1 "
            "through P, use finite_pages, enumerate all P pages under one "
            "unchanged query/filter/sort state, deduplicate stable option "
            "identities across their union, and quote the complete rendered "
            "pager line ending at P. "
            "Then call "
            "decision_checkpoint alone. After approval, re-read the exact "
            "approved identity in the visible state immediately before the "
            "later consequential action."
        )
        return task_text, tools, {"extend_system_message": protocol}

    def stats_snapshot(self) -> dict[str, Any]:
        contract = self.contract
        return {
            "contract_compile_calls": self.contract_compile_calls,
            "contract_compile_attempts": self.contract_compile_attempts,
            "contract_compile_rejections": self.contract_compile_rejections,
            "contract_compile_failures": self.contract_compile_failures,
            "contract_sha256": (
                contract.fingerprint if contract is not None else None
            ),
            "contract_canonical_json": (
                canonical_json(contract) if contract is not None else None
            ),
            "constraint_count": (
                len(contract.constraints) if contract is not None else None
            ),
            "objective_count": (
                len(contract.objectives) if contract is not None else None
            ),
            "search_mode": (
                contract.search_mode.value if contract is not None else None
            ),
            "structured_max_attempts_observed":
                self.structured_max_attempts_observed,
            "structured_attempt_exhaustions":
                self.structured_attempt_exhaustions,
            "decision_checkpoint_calls": self.decision_checkpoint_calls,
            "decision_checkpoint_rejections":
                self.decision_checkpoint_rejections,
            "decision_checkpoint_approvals":
                self.decision_checkpoint_approvals,
            "checkpoint_candidate_count": self.checkpoint_candidate_count,
            "frontier_inspected_count": self.frontier_inspected_count,
            "frontier_advertised_count":
                self.frontier_advertised_count,
            "frontier_coverage_mode": self.frontier_coverage_mode,
            "frontier_advertised_page_count":
                self.frontier_advertised_page_count,
            "frontier_enumerated_page_count":
                self.frontier_enumerated_page_count,
            "approved_candidate_id": self.approved_candidate_id,
            "auxiliary_calls": self.auxiliary_calls,
            "auxiliary_tokens": self.auxiliary_tokens,
            "auxiliary_seconds": round(self.auxiliary_seconds, 3),
            "limit_observations": {
                "structured_response_attempts": {
                    # Reaching the final configured attempt is a ceiling touch
                    # even when that attempt succeeds.  Exhausting all attempts
                    # remains a separate behavioral observation below.
                    "touched_count": int(
                        self.structured_max_attempts_observed
                        >= self._MAX_COMPILE_ATTEMPTS
                    ),
                    "observations": {
                        "max_attempts": self._MAX_COMPILE_ATTEMPTS,
                        "attempts": self.contract_compile_attempts,
                        "rejected_attempts":
                            self.contract_compile_rejections,
                        "exhaustions": self.contract_compile_failures,
                    },
                },
            },
            "runtime_source_attestation": os.environ.get(
                "CAVEAT_RUNTIME_SOURCE_ATTESTATION"
            ),
            "evaluation_input_attestation": os.environ.get(
                "CAVEAT_EVALUATION_INPUT_ATTESTATION"
            ),
        }


@SCAFFOLDS.register("caveat-harness")
class CAVEATHarnessScaffold(Scaffold):
    name = "caveat-harness"

    def supports(self, model) -> tuple[bool, str]:
        return True, ""

    def run(self, ctx: RunContext) -> RawTrajectory:
        extension = _CAVEATHarnessExtension(ctx)
        return asyncio.run(_browseruse_run(ctx, extension=extension))


__all__ = ["CAVEATHarnessScaffold"]
