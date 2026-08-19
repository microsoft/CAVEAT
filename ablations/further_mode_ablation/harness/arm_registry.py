"""Fail-closed arm metadata for the command-local harness ablation."""

from __future__ import annotations

import copy
from typing import Any

from agentarena.core.scaffold import SCAFFOLDS
from agentarena.scaffolds.browseruse import BrowserUseScaffold
from agentarena.scaffolds.browseruse_deliberative import (
    BrowserUseDeliberativeScaffold,
)
from component_scaffolds import (
    BrowserUseDeliberativeContractOnlyScaffold,
    BrowserUseDeliberativeCoverageAdvisoryScaffold,
    BrowserUseDeliberativeCoverageOnlyScaffold,
    BrowserUseDeliberativeFeasibilityScaffold,
    COMPONENT_VERSION,
)
from no_coverage_scaffold import (
    BrowserUseDeliberativeNoCoverageScaffold,
    COMPONENT_VERSION as NO_COVERAGE_VERSION,
)
from prompt_only_scaffold import (
    BrowserUsePromptOnlyScaffold,
    PROMPT_ONLY_SHA256,
    PROMPT_ONLY_VERSION,
)


ARM_ORDER = ("B", "P", "K", "E", "D", "C", "A", "F")


def _components(
    *,
    prompt: bool,
    contract: bool,
    facts: bool,
    local_choice: bool,
    coverage_payload: bool,
    coverage_enforced: bool,
    coverage_shadowed: bool,
) -> dict[str, bool]:
    return {
        "general_deliberative_guidance": prompt,
        "same_model_contract_compilation": contract,
        "fact_and_feasibility_gate": facts,
        "local_exact_choice_gate": local_choice,
        "global_coverage_payload": coverage_payload,
        "global_coverage_enforced": coverage_enforced,
        "global_coverage_shadowed": coverage_shadowed,
    }


ARM_METADATA: dict[str, dict[str, Any]] = {
    "B": {
        "profile": "B_baseline",
        "scaffold": "browseruse",
        "implementation": "unchanged_production",
        "components": _components(
            prompt=False,
            contract=False,
            facts=False,
            local_choice=False,
            coverage_payload=False,
            coverage_enforced=False,
            coverage_shadowed=False,
        ),
    },
    "P": {
        "profile": "P_prompt_only",
        "scaffold": "browseruse-prompt-only",
        "implementation": "unchanged_existing_command_local",
        "version": PROMPT_ONLY_VERSION,
        "prompt_sha256": PROMPT_ONLY_SHA256,
        "increment_from": "B",
        "contrast_scope": "general prompt guidance package",
        "components": _components(
            prompt=True,
            contract=False,
            facts=False,
            local_choice=False,
            coverage_payload=False,
            coverage_enforced=False,
            coverage_shadowed=False,
        ),
    },
    "K": {
        "profile": "K_contract_only",
        "scaffold": "browseruse-deliberative-contract-only",
        "implementation": "new_command_local",
        "version": COMPONENT_VERSION,
        "increment_from": "P",
        "contrast_scope": "same-model contract compilation and display",
        "components": _components(
            prompt=True,
            contract=True,
            facts=False,
            local_choice=False,
            coverage_payload=False,
            coverage_enforced=False,
            coverage_shadowed=False,
        ),
    },
    "E": {
        "profile": "E_fact_and_feasibility",
        "scaffold": "browseruse-deliberative-feasibility",
        "implementation": "new_command_local",
        "version": COMPONENT_VERSION,
        "increment_from": "K",
        "contrast_scope": "fact-state, compatibility, and feasibility gate",
        "components": _components(
            prompt=True,
            contract=True,
            facts=True,
            local_choice=False,
            coverage_payload=False,
            coverage_enforced=False,
            coverage_shadowed=False,
        ),
    },
    "D": {
        "profile": "D_local_exact_choice",
        "scaffold": "browseruse-deliberative-no-coverage",
        "implementation": "unchanged_existing_command_local",
        "version": NO_COVERAGE_VERSION,
        "semantic_identity": (
            "existing browseruse-deliberative-no-coverage implementation"
        ),
        "increment_from": "E",
        "contrast_scope": (
            "local exact-choice package; existing profile is semantically "
            "aligned, not a token-identical one-line layer over E"
        ),
        "components": _components(
            prompt=True,
            contract=True,
            facts=True,
            local_choice=True,
            coverage_payload=False,
            coverage_enforced=False,
            coverage_shadowed=False,
        ),
    },
    "C": {
        "profile": "C_coverage_only",
        "scaffold": "browseruse-deliberative-coverage-only",
        "implementation": "new_command_local",
        "version": COMPONENT_VERSION,
        "increment_from": "P",
        "contrast_scope": "global coverage package in isolation",
        "components": _components(
            prompt=True,
            contract=False,
            facts=False,
            local_choice=False,
            coverage_payload=True,
            coverage_enforced=True,
            coverage_shadowed=False,
        ),
    },
    "A": {
        "profile": "A_coverage_advisory",
        "scaffold": "browseruse-deliberative-coverage-advisory",
        "implementation": "new_command_local",
        "version": COMPONENT_VERSION,
        "increment_from": "D",
        "contrast_scope": (
            "full-shaped coverage cue and payload with shadow-only verdict"
        ),
        "components": _components(
            prompt=True,
            contract=True,
            facts=True,
            local_choice=True,
            coverage_payload=True,
            coverage_enforced=False,
            coverage_shadowed=True,
        ),
    },
    "F": {
        "profile": "F_full",
        "scaffold": "browseruse-deliberative",
        "implementation": "unchanged_production",
        "increment_from": "A",
        "contrast_scope": "global coverage enforcement and rejection feedback",
        "components": _components(
            prompt=True,
            contract=True,
            facts=True,
            local_choice=True,
            coverage_payload=True,
            coverage_enforced=True,
            coverage_shadowed=False,
        ),
    },
}


_EXPECTED_CLASSES = {
    "B": BrowserUseScaffold,
    "P": BrowserUsePromptOnlyScaffold,
    "K": BrowserUseDeliberativeContractOnlyScaffold,
    "E": BrowserUseDeliberativeFeasibilityScaffold,
    "D": BrowserUseDeliberativeNoCoverageScaffold,
    "C": BrowserUseDeliberativeCoverageOnlyScaffold,
    "A": BrowserUseDeliberativeCoverageAdvisoryScaffold,
    "F": BrowserUseDeliberativeScaffold,
}


def arm_metadata(arm_id: str | None = None) -> dict[str, Any]:
    """Return detached metadata; unknown arm IDs fail closed."""

    if arm_id is None:
        return copy.deepcopy(ARM_METADATA)
    if arm_id not in ARM_METADATA:
        raise KeyError(f"unknown harness ablation arm: {arm_id!r}")
    return copy.deepcopy(ARM_METADATA[arm_id])


def scaffold_for_arm(arm_id: str) -> str:
    """Resolve an exact arm ID to the benchmark scaffold CLI value."""

    return str(arm_metadata(arm_id)["scaffold"])


def validate_command_local_registration() -> dict[str, Any]:
    """Validate every arm against the live scaffold registry."""

    if tuple(ARM_METADATA) != ARM_ORDER:
        raise RuntimeError("harness arm inventory/order drifted")
    observed: dict[str, str] = {}
    for arm_id in ARM_ORDER:
        name = scaffold_for_arm(arm_id)
        expected = _EXPECTED_CLASSES[arm_id]
        actual = SCAFFOLDS.get(name)
        if actual is not expected:
            raise RuntimeError(
                f"arm {arm_id} registry identity differs for {name!r}"
            )
        observed[arm_id] = name
    if len(set(observed.values())) != len(observed):
        raise RuntimeError("two harness arms resolve to the same scaffold")
    return {
        "component_version": COMPONENT_VERSION,
        "arm_order": list(ARM_ORDER),
        "scaffolds": observed,
        "registered": True,
    }


__all__ = [
    "ARM_METADATA",
    "ARM_ORDER",
    "arm_metadata",
    "scaffold_for_arm",
    "validate_command_local_registration",
]
