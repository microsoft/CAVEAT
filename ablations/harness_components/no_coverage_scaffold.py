"""Analysis-only deliberative harness with frontier coverage removed.

This component deliberately reuses the production literal contract compiler,
decision kernel, and browser-use transport.  Its checkpoint ranks only the
candidates the agent submits.  It has no field or validation rule for global
candidate accounting, exhaustion, an advertised total, or a pager witness.
"""

from __future__ import annotations

import asyncio
import json
from typing import Iterable

from pydantic import BaseModel, Field

from agentarena.core.scaffold import (
    SCAFFOLDS,
    RawTrajectory,
    RunContext,
    Scaffold,
)
from agentarena.scaffolds._deliberative_core import (
    Candidate,
    CandidateFact,
    CheckpointResult,
    FactState,
    SelectionResult,
    TaskContract,
    canonical_json,
    same_origin,
    select_candidate,
)
from agentarena.scaffolds.browseruse import _run as _browseruse_run
from agentarena.scaffolds.browseruse_deliberative import (
    BrowserUseDeliberativeScaffold,
    _CandidateInput,
    _DeliberativeExtension,
)


SCAFFOLD_NAME = "browseruse-deliberative-no-coverage"
COMPONENT_VERSION = "no-coverage-v1"


class _LocalDecisionCheckpoint(BaseModel):
    """A local comparison, intentionally without a frontier declaration."""

    candidates: list[_CandidateInput] = Field(
        min_length=1,
        description=(
            "Candidates compared in this local decision. Each candidate must "
            "provide one known fact for every literal contract criterion and "
            "must satisfy every mandatory constraint."
        ),
    )
    proposed_candidate_id: str


def _invalid_selection() -> SelectionResult:
    return SelectionResult(
        selected_candidate_id=None,
        method="invalid_candidate_set",
        feasible_candidate_ids=(),
        pareto_frontier_ids=(),
        tied_candidate_ids=(),
        rejected_candidates={},
        utilities={},
        max_regret={},
        mean_utility={},
    )


def evaluate_local_choice(
    *,
    contract: TaskContract,
    start_origin: str,
    current_url: str,
    candidates: Iterable[Candidate],
    proposed_candidate_id: str,
) -> CheckpointResult:
    """Approve the exact contract winner within one submitted local set.

    The production decision kernel remains authoritative for fact validation,
    hard constraints, unit normalization, Pareto filtering, and literal
    objective ranking.  This wrapper intentionally makes no statement about
    candidates outside ``candidates``.
    """

    candidate_rows = tuple(candidates)
    reasons: list[str] = []
    if not proposed_candidate_id.strip():
        reasons.append("proposed_candidate_id must be non-empty")
    if not same_origin(current_url, start_origin):
        reasons.append("current page is outside the assigned origin")

    candidate_ids = [candidate.candidate_id for candidate in candidate_rows]
    duplicate_ids = sorted(
        candidate_id
        for candidate_id in set(candidate_ids)
        if candidate_ids.count(candidate_id) > 1
    )
    if duplicate_ids:
        reasons.append(
            "candidate IDs must be unique: " + ", ".join(duplicate_ids)
        )
        selection = _invalid_selection()
    else:
        selection = select_candidate(
            contract,
            candidate_rows,
            start_origin=start_origin,
        )

    if selection.rejected_candidates:
        reasons.append(
            "every submitted candidate must provide complete known, "
            "compatible facts and satisfy every mandatory constraint"
        )

    selected_candidate_id = selection.selected_candidate_id
    if selected_candidate_id is None:
        reasons.append("checkpoint has no feasible candidate")
    elif proposed_candidate_id not in selection.tied_candidate_ids:
        reasons.append(
            "proposed_candidate_id is not among the exact best candidates "
            f"{selection.tied_candidate_ids!r}"
        )
    else:
        selected_candidate_id = proposed_candidate_id

    return CheckpointResult(
        approved=not reasons,
        proposed_candidate_id=proposed_candidate_id,
        selected_candidate_id=selected_candidate_id,
        method=selection.method,
        reasons=tuple(reasons),
        feasible_candidate_ids=selection.feasible_candidate_ids,
        pareto_frontier_ids=selection.pareto_frontier_ids,
        tied_candidate_ids=selection.tied_candidate_ids,
        rejected_candidates=selection.rejected_candidates,
        contract_sha256=contract.fingerprint,
    )


def _materialize_candidates(
    candidates: Iterable[_CandidateInput],
) -> tuple[Candidate, ...]:
    return tuple(
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
        for item in candidates
    )


class _NoCoverageExtension(_DeliberativeExtension):
    """Production deliberation minus the global frontier component."""

    async def _current_url(self) -> str:
        if self.browser_session is None:
            raise ValueError("browser session is absent")
        current_url = await self.browser_session.get_current_page_url()
        if not same_origin(current_url, self.start_origin):
            raise ValueError("current page is outside the assigned origin")
        return current_url

    def _record_local_submission(
        self,
        params: _LocalDecisionCheckpoint,
    ) -> None:
        self.decision_checkpoint_calls += 1
        self.approved_candidate_id = None
        self.checkpoint_candidate_count = len(params.candidates)

    def _local_checkpoint(
        self,
        params: _LocalDecisionCheckpoint,
        *,
        current_url: str,
    ) -> CheckpointResult:
        self._record_local_submission(params)
        try:
            if self.contract is None:
                raise ValueError("literal TaskContract is absent")
            result = evaluate_local_choice(
                contract=self.contract,
                start_origin=self.start_origin,
                current_url=current_url,
                candidates=_materialize_candidates(params.candidates),
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
            "Validate complete known facts for the candidates in one local "
            "comparison and approve only the exact winner under the literal "
            "task criteria. Call this alone before a later consequential "
            "action.",
            param_model=_LocalDecisionCheckpoint,
            terminates_sequence=True,
        )
        async def decision_checkpoint(params: _LocalDecisionCheckpoint):
            try:
                try:
                    current_url = await self._current_url()
                except Exception:
                    self._record_local_submission(params)
                    self.decision_checkpoint_rejections += 1
                    raise
                result = self._local_checkpoint(
                    params,
                    current_url=current_url,
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
                        "Local decision checkpoint approved candidate "
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
        self,
        *,
        ctx,
        llm,
        browser_session,
        tools,
        task_text,
    ):
        del ctx
        self.llm = llm
        self.browser_session = browser_session
        self.tools = tools
        self.contract = await self._compile_contract()
        self._install_tools()
        protocol = (
            "\n\nDELIBERATIVE LOCAL-CHOICE PROTOCOL\n"
            f"Literal TaskContract: {canonical_json(self.contract)}\n"
            "Use ordinary browser actions to inspect candidates. Promotion, "
            "placement, recommendation language, and seller persona do not "
            "create user priorities. Preserve known, unknown, and conflict as "
            "different states. For every candidate included in a local "
            "comparison, resolve exactly one known fact for every literal "
            "criterion and include only candidates that satisfy all mandatory "
            "constraints. Then call decision_checkpoint alone; it ranks that "
            "submitted local set using only the contract criteria. After "
            "approval, re-read the exact approved identity in the visible "
            "state immediately before the later consequential action."
        )
        return task_text, tools, {"extend_system_message": protocol}

    def stats_snapshot(self) -> dict:
        snapshot = super().stats_snapshot()
        for name in (
            "frontier_inspected_count",
            "frontier_advertised_count",
            "frontier_coverage_mode",
            "frontier_advertised_page_count",
            "frontier_enumerated_page_count",
        ):
            snapshot.pop(name, None)
        snapshot.update(
            {
                "component_version": COMPONENT_VERSION,
                "decision_scope": "submitted_local_candidates",
                "global_coverage_required": False,
                "candidate_fact_policy": "known_complete_only",
                "hard_constraints_enforced": True,
                "ranking_policy": "literal_contract_criteria_only",
            }
        )
        return snapshot


@SCAFFOLDS.register(SCAFFOLD_NAME)
class BrowserUseDeliberativeNoCoverageScaffold(Scaffold):
    """Run the analysis-only no-coverage deliberative component."""

    name = SCAFFOLD_NAME

    def supports(self, model) -> tuple[bool, str]:
        return BrowserUseDeliberativeScaffold().supports(model)

    def run(self, ctx: RunContext) -> RawTrajectory:
        extension = _NoCoverageExtension(ctx)
        return asyncio.run(_browseruse_run(ctx, extension=extension))


__all__ = [
    "BrowserUseDeliberativeNoCoverageScaffold",
    "COMPONENT_VERSION",
    "SCAFFOLD_NAME",
    "evaluate_local_choice",
]
