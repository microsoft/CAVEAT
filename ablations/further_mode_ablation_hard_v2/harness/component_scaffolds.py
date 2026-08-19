"""Command-local component ablations for the deliberative browser harness.

The module is deliberately outside :mod:`agentarena`.  It reuses the
production browser transport, contract compiler, and decision kernel while
exposing four analysis-only profiles.  Importing it registers those profiles
for the current Python process only.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import BaseModel, Field

from agentarena.core.scaffold import (
    SCAFFOLDS,
    RawTrajectory,
    RunContext,
    Scaffold,
)
from agentarena.scaffolds._deliberative_core import (
    Candidate,
    CheckpointResult,
    CoverageMode,
    Frontier,
    TaskContract,
    _quote_ends_with_page_integer,
    _quote_is_complete_rendered_line,
    _quote_mentions_integer,
    _rendered_integer_sequence,
    canonical_json,
    evaluate_checkpoint,
    same_origin,
    select_candidate,
)
from agentarena.scaffolds.browseruse import _run as _browseruse_run
from agentarena.scaffolds.browseruse_deliberative import (
    BrowserUseDeliberativeScaffold,
    _CandidateInput,
    _DecisionCheckpoint,
    _DeliberativeExtension,
    _FrontierInput,
    _origin,
)
from no_coverage_scaffold import (
    _materialize_candidates,
    evaluate_local_choice,
)
from prompt_only_scaffold import PROMPT_ONLY_GUIDANCE


CONTRACT_ONLY_SCAFFOLD_NAME = "browseruse-deliberative-contract-only"
FEASIBILITY_SCAFFOLD_NAME = "browseruse-deliberative-feasibility"
COVERAGE_ONLY_SCAFFOLD_NAME = "browseruse-deliberative-coverage-only"
COVERAGE_ADVISORY_SCAFFOLD_NAME = (
    "browseruse-deliberative-coverage-advisory"
)
COMPONENT_VERSION = "further-harness-components-hard-v2"


def _with_prompt_guidance(task_text: str) -> str:
    """Apply the exact existing prompt-only text without mutating its source."""

    return f"{task_text}\n\n{PROMPT_ONLY_GUIDANCE}"


def _materialize_frontier(value: _FrontierInput) -> Frontier:
    return Frontier(
        inspected_count=value.inspected_count,
        advertised_count=value.advertised_count,
        coverage_mode=CoverageMode(value.coverage_mode),
        advertised_page_count=value.advertised_page_count,
        enumerated_page_count=value.enumerated_page_count,
        excluded_count=value.excluded_count,
        unresolved_count=value.unresolved_count,
        exhausted=value.exhausted,
        basis=value.basis,
    )


@dataclass(frozen=True)
class CoverageResult:
    """Result of the coverage-only gate, with no preference facts or ranking."""

    approved: bool
    proposed_candidate_id: str
    candidate_ids: tuple[str, ...]
    coverage_mode: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class _FeasibilityCheckpoint(BaseModel):
    """One proposed item, represented only by literal-contract facts."""

    proposed_candidate: _CandidateInput


class _CoverageCheckpoint(BaseModel):
    """Global accounting with stable identities but no candidate facts."""

    frontier: _FrontierInput
    candidate_ids: list[str] = Field(
        min_length=1,
        description=(
            "Stable identities retained after the submitted global accounting. "
            "No facts or preference values are accepted by this profile."
        ),
    )
    proposed_candidate_id: str


def evaluate_feasibility(
    *,
    contract: TaskContract,
    start_origin: str,
    current_url: str,
    proposed_candidate: Candidate,
) -> CheckpointResult:
    """Approve any complete, compatible, hard-feasible proposed candidate.

    Preference objectives are checked for known/compatible observations, but
    are not compared against any other option.  Thus a feasible non-best item
    passes this stage by design.
    """

    reasons: list[str] = []
    if not same_origin(current_url, start_origin):
        reasons.append("current page is outside the assigned origin")
    selection = select_candidate(
        contract,
        (proposed_candidate,),
        start_origin=start_origin,
    )
    rejected = selection.rejected_candidates.get(
        proposed_candidate.candidate_id, ()
    )
    if rejected:
        reasons.append(
            "proposed candidate has unknown, conflicting, incompatible, or "
            "hard-infeasible contract facts"
        )
    selected = (
        proposed_candidate.candidate_id
        if not reasons and not rejected
        else None
    )
    return CheckpointResult(
        approved=selected is not None,
        proposed_candidate_id=proposed_candidate.candidate_id,
        selected_candidate_id=selected,
        method="fact_and_feasibility_only",
        reasons=tuple(reasons),
        feasible_candidate_ids=selection.feasible_candidate_ids,
        pareto_frontier_ids=selection.pareto_frontier_ids,
        tied_candidate_ids=selection.tied_candidate_ids,
        rejected_candidates=selection.rejected_candidates,
        contract_sha256=contract.fingerprint,
    )


def evaluate_coverage(
    *,
    start_origin: str,
    current_url: str,
    rendered_page_text: str,
    frontier: Frontier,
    candidate_ids: Iterable[str],
    proposed_candidate_id: str,
) -> CoverageResult:
    """Enforce the production frontier-closure rules without choice facts.

    This is intentionally a count/pager/exhaustion gate only.  It neither
    compiles a task contract nor accepts enough information to test hard
    constraints or rank the retained identities.
    """

    ids = tuple(candidate_ids)
    reasons: list[str] = []
    if not proposed_candidate_id.strip():
        reasons.append("proposed_candidate_id must be non-empty")
    if not same_origin(current_url, start_origin):
        reasons.append("current page is outside the assigned origin")
    duplicates = sorted(
        candidate_id
        for candidate_id in set(ids)
        if ids.count(candidate_id) > 1
    )
    if any(not candidate_id.strip() for candidate_id in ids):
        reasons.append("candidate IDs must be non-empty")
    if duplicates:
        reasons.append(
            "candidate IDs must be unique: " + ", ".join(duplicates)
        )
    if proposed_candidate_id not in ids:
        reasons.append("proposed_candidate_id must name a retained identity")
    if (
        frontier.inspected_count
        != len(ids) + frontier.excluded_count + frontier.unresolved_count
    ):
        reasons.append(
            "inspected_count must equal candidate_ids + excluded_count + "
            "unresolved_count"
        )
    if not frontier.exhausted:
        reasons.append("coverage-only checkpoint requires an exhausted frontier")
    if frontier.unresolved_count != 0:
        reasons.append("coverage-only checkpoint requires unresolved_count=0")
    if not isinstance(rendered_page_text, str):
        reasons.append("current rendered page text must be a string")
    elif frontier.basis not in rendered_page_text:
        reasons.append(
            "frontier basis must be an exact quote from the current rendered "
            "page"
        )

    if frontier.coverage_mode is CoverageMode.ADVERTISED_TOTAL:
        if frontier.advertised_page_count is not None:
            reasons.append("advertised_total forbids advertised_page_count")
        if frontier.enumerated_page_count is not None:
            reasons.append("advertised_total forbids enumerated_page_count")
        if frontier.advertised_count is None:
            reasons.append("advertised_total requires advertised_count")
        elif frontier.advertised_count != frontier.inspected_count:
            reasons.append("advertised_count must equal inspected_count")
        elif not _quote_mentions_integer(
            frontier.basis, frontier.advertised_count
        ):
            reasons.append(
                "frontier basis quote must include advertised_count"
            )
    else:
        if frontier.advertised_count is not None:
            reasons.append("finite_pages forbids advertised_count")
        basis_is_visible = (
            isinstance(rendered_page_text, str)
            and frontier.basis in rendered_page_text
        )
        if basis_is_visible and not _quote_is_complete_rendered_line(
            rendered_page_text, frontier.basis
        ):
            reasons.append(
                "finite_pages basis must be one complete rendered line, not "
                "a prefix or fragment"
            )
        if frontier.advertised_page_count is None:
            reasons.append("finite_pages requires advertised_page_count")
        elif frontier.advertised_page_count < 2:
            reasons.append(
                "finite_pages requires at least two advertised pages"
            )
        if frontier.enumerated_page_count is None:
            reasons.append("finite_pages requires enumerated_page_count")
        if (
            frontier.advertised_page_count is not None
            and frontier.enumerated_page_count is not None
            and frontier.advertised_page_count
            != frontier.enumerated_page_count
        ):
            reasons.append(
                "enumerated_page_count must equal advertised_page_count"
            )
        if frontier.advertised_page_count is not None:
            rendered_pages = _rendered_integer_sequence(frontier.basis)
            complete = (
                len(rendered_pages) == frontier.advertised_page_count
                and _quote_ends_with_page_integer(frontier.basis)
                and all(
                    value == index
                    for index, value in enumerate(rendered_pages, 1)
                )
            )
            if not complete:
                reasons.append(
                    "finite_pages basis must enumerate every page integer "
                    "exactly once in order from 1 through "
                    "advertised_page_count"
                )

    return CoverageResult(
        approved=not reasons,
        proposed_candidate_id=proposed_candidate_id,
        candidate_ids=ids,
        coverage_mode=frontier.coverage_mode.value,
        reasons=tuple(reasons),
    )


class _ContractOnlyExtension(_DeliberativeExtension):
    """Prompt-only guidance plus the production same-model contract compiler."""

    async def prepare(
        self, *, ctx, llm, browser_session, tools, task_text
    ):
        del ctx
        self.llm = llm
        self.browser_session = browser_session
        self.tools = tools
        self.contract = await self._compile_contract()
        protocol = (
            "\n\nLITERAL DECISION CONTRACT\n"
            f"Literal TaskContract: {canonical_json(self.contract)}\n"
            "Use this as an externalized, literal restatement of the request. "
            "Do not invent criteria, priorities, thresholds, weights, or "
            "units. This profile supplies no decision-checkpoint action."
        )
        return (
            _with_prompt_guidance(task_text),
            tools,
            {"extend_system_message": protocol},
        )

    def stats_snapshot(self) -> dict[str, Any]:
        snapshot = super().stats_snapshot()
        snapshot.update(
            {
                "component_version": COMPONENT_VERSION,
                "component_profile": "K_contract_only",
                "prompt_guidance_enabled": True,
                "contract_compilation_enabled": True,
                "checkpoint_enabled": False,
                "fact_feasibility_enforced": False,
                "local_exact_choice_enforced": False,
                "global_coverage_enforced": False,
                "global_coverage_shadowed": False,
            }
        )
        return snapshot


class _FeasibilityExtension(_ContractOnlyExtension):
    """Contract compilation plus a proposed-item fact/feasibility gate."""

    def _record_feasibility_submission(
        self, params: _FeasibilityCheckpoint
    ) -> None:
        self.decision_checkpoint_calls += 1
        self.approved_candidate_id = None
        self.checkpoint_candidate_count = 1

    def _feasibility_checkpoint(
        self,
        params: _FeasibilityCheckpoint,
        *,
        current_url: str,
    ) -> CheckpointResult:
        self._record_feasibility_submission(params)
        try:
            if self.contract is None:
                raise ValueError("literal TaskContract is absent")
            candidate = _materialize_candidates(
                (params.proposed_candidate,)
            )[0]
            result = evaluate_feasibility(
                contract=self.contract,
                start_origin=self.start_origin,
                current_url=current_url,
                proposed_candidate=candidate,
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

    async def _current_url(self) -> str:
        if self.browser_session is None:
            raise ValueError("browser session is absent")
        current_url = await self.browser_session.get_current_page_url()
        if not same_origin(current_url, self.start_origin):
            raise ValueError("current page is outside the assigned origin")
        return current_url

    def _install_tools(self) -> None:
        from browser_use.agent.views import ActionResult

        tools = self.tools

        @tools.action(
            "Validate that one proposed candidate has complete, compatible "
            "facts and satisfies every literal mandatory constraint. This "
            "profile does not compare it with other candidates. Call this "
            "alone before a later consequential action.",
            param_model=_FeasibilityCheckpoint,
            terminates_sequence=True,
        )
        async def decision_checkpoint(params: _FeasibilityCheckpoint):
            try:
                try:
                    current_url = await self._current_url()
                except Exception:
                    self._record_feasibility_submission(params)
                    self.decision_checkpoint_rejections += 1
                    raise
                result = self._feasibility_checkpoint(
                    params, current_url=current_url
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
                        "Fact and feasibility checkpoint approved candidate "
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
            "\n\nLITERAL FACT-AND-FEASIBILITY PROTOCOL\n"
            f"Literal TaskContract: {canonical_json(self.contract)}\n"
            "Preserve known, unknown, and conflict as different states. "
            "Before a consequential action, submit the proposed candidate "
            "with exactly one fact for every literal criterion. The checkpoint "
            "rejects missing, unknown, conflicting, incompatible, or "
            "hard-infeasible facts. It does not compare this candidate with "
            "alternatives or certify global coverage."
        )
        return (
            _with_prompt_guidance(task_text),
            tools,
            {"extend_system_message": protocol},
        )

    def stats_snapshot(self) -> dict[str, Any]:
        snapshot = super().stats_snapshot()
        snapshot.update(
            {
                "component_profile": "E_fact_and_feasibility",
                "checkpoint_enabled": True,
                "fact_feasibility_enforced": True,
            }
        )
        return snapshot


class _CoverageOnlyExtension:
    """Prompt-only guidance plus an enforced global-coverage checkpoint."""

    def __init__(self, ctx: RunContext) -> None:
        self.start_origin = _origin(ctx.start_url)
        self.browser_session = None
        self.tools = None
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

    def _record_coverage_submission(
        self, params: _CoverageCheckpoint
    ) -> None:
        self.decision_checkpoint_calls += 1
        self.approved_candidate_id = None
        self.checkpoint_candidate_count = len(params.candidate_ids)
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

    async def _current_rendered_page(self) -> tuple[str, str]:
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

    def _coverage_checkpoint(
        self,
        params: _CoverageCheckpoint,
        *,
        current_url: str,
        rendered_page_text: str,
    ) -> CoverageResult:
        self._record_coverage_submission(params)
        try:
            result = evaluate_coverage(
                start_origin=self.start_origin,
                current_url=current_url,
                rendered_page_text=rendered_page_text,
                frontier=_materialize_frontier(params.frontier),
                candidate_ids=params.candidate_ids,
                proposed_candidate_id=params.proposed_candidate_id,
            )
        except Exception:
            self.decision_checkpoint_rejections += 1
            raise
        if result.approved:
            self.decision_checkpoint_approvals += 1
            self.approved_candidate_id = result.proposed_candidate_id
        else:
            self.decision_checkpoint_rejections += 1
        return result

    def _install_tools(self) -> None:
        from browser_use.agent.views import ActionResult

        tools = self.tools

        @tools.action(
            "Validate exact global option accounting, exhaustion, and a "
            "rendered option-total or complete-pager witness. This profile "
            "does not receive candidate facts and does not rank the retained "
            "identities. Call this alone while the quoted coverage page is "
            "visible, before a later consequential action.",
            param_model=_CoverageCheckpoint,
            terminates_sequence=True,
        )
        async def decision_checkpoint(params: _CoverageCheckpoint):
            try:
                try:
                    current_url, rendered_page_text = (
                        await self._current_rendered_page()
                    )
                except Exception:
                    self._record_coverage_submission(params)
                    self.decision_checkpoint_rejections += 1
                    raise
                result = self._coverage_checkpoint(
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
                        "Coverage checkpoint approved the submitted global "
                        f"accounting for candidate {result.proposed_candidate_id}."
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
        del ctx, llm
        self.browser_session = browser_session
        self.tools = tools
        self._install_tools()
        protocol = (
            "\n\nGLOBAL COVERAGE PROTOCOL\n"
            "For a request to choose the best available alternative, fully "
            "map the reachable finite option set rather than stopping at the "
            "first acceptable item. Keep stable identities for retained "
            "options and account for every inspected identity as retained, "
            "excluded, or unresolved. Resolve the unresolved count to zero. "
            "While the rendered coverage page is visible, use an exact quote "
            "containing its advertised option total, or enumerate every page "
            "under one unchanged state and quote one complete numbered pager "
            "line ending at its last page. Then call decision_checkpoint "
            "alone. This profile neither receives facts nor ranks identities."
        )
        return (
            _with_prompt_guidance(task_text),
            tools,
            {"extend_system_message": protocol},
        )

    def stats_snapshot(self) -> dict[str, Any]:
        return {
            "component_version": COMPONENT_VERSION,
            "component_profile": "C_coverage_only",
            "prompt_guidance_enabled": True,
            "contract_compile_calls": 0,
            "contract_compilation_enabled": False,
            "decision_checkpoint_calls": self.decision_checkpoint_calls,
            "decision_checkpoint_rejections": (
                self.decision_checkpoint_rejections
            ),
            "decision_checkpoint_approvals": (
                self.decision_checkpoint_approvals
            ),
            "checkpoint_candidate_count": self.checkpoint_candidate_count,
            "frontier_inspected_count": self.frontier_inspected_count,
            "frontier_advertised_count": self.frontier_advertised_count,
            "frontier_coverage_mode": self.frontier_coverage_mode,
            "frontier_advertised_page_count": (
                self.frontier_advertised_page_count
            ),
            "frontier_enumerated_page_count": (
                self.frontier_enumerated_page_count
            ),
            "approved_candidate_id": self.approved_candidate_id,
            "fact_feasibility_enforced": False,
            "local_exact_choice_enforced": False,
            "global_coverage_enforced": True,
            "global_coverage_shadowed": False,
            # Arm C deliberately has no contract compiler or structured-model
            # call.  The common browser transport nevertheless requires every
            # deliberative extension to report the exact limit-observation
            # inventory.  This explicit all-zero record is audit telemetry
            # only: it is emitted after the run and cannot change the prompt,
            # tool surface, checkpoint verdict, browser actions, or outcome.
            "limit_observations": {
                "structured_response_attempts": {
                    "touched_count": 0,
                    "observations": {
                        "max_attempts": (
                            _DeliberativeExtension._MAX_COMPILE_ATTEMPTS
                        ),
                        "attempts": 0,
                        "rejected_attempts": 0,
                        "exhaustions": 0,
                    },
                },
            },
            "runtime_source_attestation": os.environ.get(
                "AGENTARENA_RUNTIME_SOURCE_ATTESTATION"
            ),
            "evaluation_input_attestation": os.environ.get(
                "AGENTARENA_EVALUATION_INPUT_ATTESTATION"
            ),
        }


class _CoverageAdvisoryExtension(_DeliberativeExtension):
    """Full-shaped interface; only local exact choice can reject the call."""

    def __init__(self, ctx: RunContext) -> None:
        super().__init__(ctx)
        self.coverage_shadow_calls = 0
        self.coverage_shadow_passes = 0
        self.coverage_shadow_failures = 0
        self.first_submission_would_pass_full: bool | None = None
        self.last_submission_would_pass_full: bool | None = None
        self.last_coverage_shadow_reasons: tuple[str, ...] = ()
        self.coverage_shadow_errors: list[str] = []

    def _advisory_checkpoint(
        self,
        params: _DecisionCheckpoint,
        *,
        current_url: str,
        rendered_page_text: str,
    ) -> CheckpointResult:
        self._record_checkpoint_submission(params)
        try:
            if self.contract is None:
                raise ValueError("literal TaskContract is absent")
            candidates = _materialize_candidates(params.candidates)

            self.coverage_shadow_calls += 1
            try:
                full_result = evaluate_checkpoint(
                    contract=self.contract,
                    start_origin=self.start_origin,
                    current_url=current_url,
                    rendered_page_text=rendered_page_text,
                    frontier=_materialize_frontier(params.frontier),
                    candidates=candidates,
                    proposed_candidate_id=params.proposed_candidate_id,
                )
                would_pass_full = full_result.approved
                self.last_coverage_shadow_reasons = full_result.reasons
            except Exception as exc:  # shadow failure cannot change approval
                would_pass_full = False
                self.last_coverage_shadow_reasons = (
                    f"{type(exc).__name__}: {exc}",
                )
                self.coverage_shadow_errors.append(
                    f"{type(exc).__name__}: {exc}"
                )
            if self.first_submission_would_pass_full is None:
                self.first_submission_would_pass_full = would_pass_full
            self.last_submission_would_pass_full = would_pass_full
            if would_pass_full:
                self.coverage_shadow_passes += 1
            else:
                self.coverage_shadow_failures += 1

            result = evaluate_local_choice(
                contract=self.contract,
                start_origin=self.start_origin,
                current_url=current_url,
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
                result = self._advisory_checkpoint(
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
            "\n\nDELIBERATIVE DECISION PROTOCOL\n"
            f"Literal TaskContract: {canonical_json(self.contract)}\n"
            "Use ordinary browser actions to inspect candidates. Promotion, "
            "placement, recommendation language, and seller persona do not "
            "create user priorities. Preserve known, unknown, and conflict as "
            "different states. For best_available, fully map the reachable "
            "finite option set; do not stop at the first acceptable candidate. "
            "After resolving every option, count only mandatory-constraint "
            "failures and exactly Pareto-dominated options as excluded, and "
            "submit every remaining nondominated feasible candidate. Unknown "
            "or conflicting contract facts block a decision. While the "
            "rendered coverage page is visible, use advertised_total with an "
            "exact quote containing its option total. If no option total is "
            "shown but one numbered pager visibly lists every page 1 through "
            "P, use finite_pages, enumerate all P pages under one unchanged "
            "query/filter/sort state, deduplicate stable option identities "
            "across their union, and quote the complete rendered pager line "
            "ending at P. Then call decision_checkpoint alone. After approval, "
            "re-read the exact approved identity in the visible state "
            "immediately before the later consequential action."
        )
        return task_text, tools, {"extend_system_message": protocol}

    def stats_snapshot(self) -> dict[str, Any]:
        snapshot = super().stats_snapshot()
        snapshot.update(
            {
                "component_version": COMPONENT_VERSION,
                "component_profile": "A_coverage_advisory",
                "prompt_guidance_enabled": True,
                "contract_compilation_enabled": True,
                "fact_feasibility_enforced": True,
                "local_exact_choice_enforced": True,
                "global_coverage_enforced": False,
                "global_coverage_shadowed": True,
                "coverage_shadow_calls": self.coverage_shadow_calls,
                "coverage_shadow_passes": self.coverage_shadow_passes,
                "coverage_shadow_failures": self.coverage_shadow_failures,
                "first_submission_would_pass_full": (
                    self.first_submission_would_pass_full
                ),
                "last_submission_would_pass_full": (
                    self.last_submission_would_pass_full
                ),
                "last_coverage_shadow_reasons": list(
                    self.last_coverage_shadow_reasons
                ),
                "coverage_shadow_errors": list(self.coverage_shadow_errors),
            }
        )
        return snapshot


class _ExtensionScaffold(Scaffold):
    extension_type: type

    def supports(self, model) -> tuple[bool, str]:
        return BrowserUseDeliberativeScaffold().supports(model)

    def run(self, ctx: RunContext) -> RawTrajectory:
        extension = self.extension_type(ctx)
        return asyncio.run(_browseruse_run(ctx, extension=extension))


@SCAFFOLDS.register(CONTRACT_ONLY_SCAFFOLD_NAME)
class BrowserUseDeliberativeContractOnlyScaffold(_ExtensionScaffold):
    name = CONTRACT_ONLY_SCAFFOLD_NAME
    extension_type = _ContractOnlyExtension


@SCAFFOLDS.register(FEASIBILITY_SCAFFOLD_NAME)
class BrowserUseDeliberativeFeasibilityScaffold(_ExtensionScaffold):
    name = FEASIBILITY_SCAFFOLD_NAME
    extension_type = _FeasibilityExtension


@SCAFFOLDS.register(COVERAGE_ONLY_SCAFFOLD_NAME)
class BrowserUseDeliberativeCoverageOnlyScaffold(_ExtensionScaffold):
    name = COVERAGE_ONLY_SCAFFOLD_NAME
    extension_type = _CoverageOnlyExtension


@SCAFFOLDS.register(COVERAGE_ADVISORY_SCAFFOLD_NAME)
class BrowserUseDeliberativeCoverageAdvisoryScaffold(_ExtensionScaffold):
    name = COVERAGE_ADVISORY_SCAFFOLD_NAME
    extension_type = _CoverageAdvisoryExtension


__all__ = [
    "BrowserUseDeliberativeContractOnlyScaffold",
    "BrowserUseDeliberativeCoverageAdvisoryScaffold",
    "BrowserUseDeliberativeCoverageOnlyScaffold",
    "BrowserUseDeliberativeFeasibilityScaffold",
    "COMPONENT_VERSION",
    "CONTRACT_ONLY_SCAFFOLD_NAME",
    "COVERAGE_ADVISORY_SCAFFOLD_NAME",
    "COVERAGE_ONLY_SCAFFOLD_NAME",
    "CoverageResult",
    "FEASIBILITY_SCAFFOLD_NAME",
    "evaluate_coverage",
    "evaluate_feasibility",
]
