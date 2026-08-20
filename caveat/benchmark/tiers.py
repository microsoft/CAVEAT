"""Canonical CAVEAT tier definitions.

The two tiers intentionally have different matrices.  CAVEAT-Standard combines
the five CAVEAT-Shop scenarios with the other eight coequal environments.  The
truthful, large-catalog CAVEAT-Hard tier uses only its five dedicated scenarios.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..core.task import TaskSpec
from .registry import benchmark_tasks

CAVEAT_STANDARD = "CAVEAT-Standard"
CAVEAT_HARD = "CAVEAT-Hard"
TIER_NAMES = (CAVEAT_STANDARD, CAVEAT_HARD)

SHOP_STANDARD_SCENARIOS = ("laptop", "office_chair", "mattress", "backpack", "tent")
SHOP_HARD_SCENARIOS = tuple(f"{scenario}_hard" for scenario in SHOP_STANDARD_SCENARIOS)
STOREFRONT_ENVIRONMENTS = (
    "caveat_stay",
    "caveat_food",
    "caveat_market",
    "caveat_craft",
    "caveat_services",
    "caveat_grocery",
    "caveat_sport",
    "caveat_kicks",
)
ALL_ENVIRONMENTS = ("caveat_shop", *STOREFRONT_ENVIRONMENTS)

STANDARD_SHOP_VARIANTS = ("mixed", "graded", "graded3", "graded4")
STANDARD_STOREFRONT_VARIANTS = ("thresholded", "mixed", "graded", "graded3", "graded4")
HARD_VARIANTS = ("graded",)

# These repetitions reproduce the released evaluation protocols.  A smaller
# number is useful for endpoint smoke tests and can be requested explicitly.
DEFAULT_REPEATS = {CAVEAT_STANDARD: 3, CAVEAT_HARD: 2}


@dataclass(frozen=True)
class TierGroup:
    """A homogeneous task/condition block within a benchmark tier."""

    name: str
    tasks: tuple[TaskSpec, ...]
    conditions: tuple[str, ...]


def _storefront_tasks(environments: Iterable[str]) -> tuple[TaskSpec, ...]:
    import caveat.envs

    selected = set(environments)
    return tuple(
        task
        for task in caveat.envs.ALL_TASKS
        if task.env in selected
        and task.metadata.get("variant") in STANDARD_STOREFRONT_VARIANTS
    )


def tier_groups(
    tier: str, environments: Iterable[str] | None = None
) -> tuple[TierGroup, ...]:
    """Return the exact task/condition blocks for ``tier``.

    ``environments`` may select a subset for development runs without changing
    the definition recorded in the output manifest.
    """
    if tier not in TIER_NAMES:
        raise ValueError(
            f"unknown tier {tier!r}; choose one of {', '.join(TIER_NAMES)}"
        )
    allowed = set(ALL_ENVIRONMENTS if tier == CAVEAT_STANDARD else ("caveat_shop",))
    selected = set(environments or allowed)
    unknown = selected - allowed
    if unknown:
        raise ValueError(f"{tier} does not contain: {', '.join(sorted(unknown))}")

    groups: list[TierGroup] = []
    if "caveat_shop" in selected:
        scenarios = (
            SHOP_STANDARD_SCENARIOS if tier == CAVEAT_STANDARD else SHOP_HARD_SCENARIOS
        )
        variants = STANDARD_SHOP_VARIANTS if tier == CAVEAT_STANDARD else HARD_VARIANTS
        tasks = tuple(
            task
            for scenario in scenarios
            for task in benchmark_tasks(scenario, variants=variants)
        )
        groups.append(
            TierGroup(
                "shop",
                tasks,
                ("combined",),
            )
        )
    if tier == CAVEAT_STANDARD:
        storefronts = tuple(env for env in STOREFRONT_ENVIRONMENTS if env in selected)
        if storefronts:
            groups.append(
                TierGroup(
                    "storefronts", _storefront_tasks(storefronts), ("clean", "steered")
                )
            )
    return tuple(groups)


def expected_runs(
    tier: str, *, repeats: int | None = None, environments: Iterable[str] | None = None
) -> int:
    count = sum(
        len(group.tasks) * len(group.conditions)
        for group in tier_groups(tier, environments)
    )
    return count * (DEFAULT_REPEATS[tier] if repeats is None else repeats)
