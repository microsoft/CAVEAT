#!/usr/bin/env python3
"""Validate binary optimal-selection behavior for storefront environments.

For each task variant, this read-only check requires at least one optimal catalog
selection, requires the authored reference item to be optimal, and ensures no
advertised steering item is optimal. Variants with relative preferences must have
one unique optimum.

Usage:
    python scripts/validate7.py caveat_sport caveat_grocery
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from caveat.envs._storefront.tasks7 import VARIANTS7, project  # noqa: E402
from caveat.scoring.optimal_selection import optimal_indices  # noqa: E402


DEFAULT_ENVIRONMENTS = (
    "caveat_stay",
    "caveat_food",
    "caveat_market",
    "caveat_craft",
    "caveat_services",
    "caveat_grocery",
    "caveat_sport",
    "caveat_kicks",
)


def _identifier(item: object) -> str:
    return str(
        getattr(item, "sku", None)
        or getattr(item, "listing_id", None)
        or getattr(item, "title", "?")
    )


def validate(environment: str) -> bool:
    spec = importlib.import_module(f"caveat.envs.{environment}.tasks").PREF7
    catalog_module = importlib.import_module(f"caveat.envs.{environment}.catalog")
    catalogs = [
        value
        for value in vars(catalog_module).values()
        if hasattr(value, "name")
        and (hasattr(value, "items") or hasattr(value, "listings"))
    ]
    catalog = next(
        (value for value in catalogs if getattr(value, "name", None) == spec.catalog),
        catalogs[0],
    )
    items = list(getattr(catalog, "items", None) or getattr(catalog, "listings"))
    items = [item for item in items if getattr(item, "role", "") != "addon"]
    candidates = [item.attrs() for item in items]
    references = {index for index, item in enumerate(items) if item.role == "hero"}
    advertised = {index for index, item in enumerate(items) if item.advertised}

    errors: list[str] = []
    print(f"== {environment}: {len(items)} selectable items")
    shared_relative_winner: set[int] | None = None

    for variant in VARIANTS7:
        preferences, graded = project(spec, variant)
        winners = optimal_indices(candidates, preferences, graded)
        winner_ids = [_identifier(items[index]) for index in sorted(winners)]
        print(f"   {variant:11} optimal={winner_ids}")
        if not winners:
            errors.append(f"{variant}: no optimal selection")
        if graded and len(winners) != 1:
            errors.append(f"{variant}: expected one unique relative optimum, found {len(winners)}")
        if graded and len(winners) == 1:
            if shared_relative_winner is None:
                shared_relative_winner = winners
            elif winners != shared_relative_winner:
                errors.append(f"{variant}: relative optimum differs across variants")
        if references and not references.issubset(winners):
            errors.append(f"{variant}: authored reference item is not optimal")
        promoted_winners = advertised & winners
        if promoted_winners:
            ids = [_identifier(items[index]) for index in sorted(promoted_winners)]
            errors.append(f"{variant}: advertised item is optimal: {ids}")

    for error in errors:
        print(f"   FAIL: {error}")
    print(f"   -> {'PASS' if not errors else 'FAIL'}")
    return not errors


def main() -> int:
    environments = tuple(sys.argv[1:]) or DEFAULT_ENVIRONMENTS
    return 0 if all(validate(environment) for environment in environments) else 1


if __name__ == "__main__":
    raise SystemExit(main())
