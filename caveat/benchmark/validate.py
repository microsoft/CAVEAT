"""Validate the committed CAVEAT benchmark and its binary scoring contract.

The validator is intentionally artifact-based: the shipped data is the benchmark.
It validates both canonical tiers without regenerating catalogs.
"""

from __future__ import annotations

import importlib
import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..scoring.optimal_selection import optimal_indices
from . import serialize
from .tiers import (
    CAVEAT_HARD,
    CAVEAT_STANDARD,
    HARD_VARIANTS,
    SHOP_HARD_SCENARIOS,
    SHOP_STANDARD_SCENARIOS,
    STANDARD_SHOP_VARIANTS,
    STOREFRONT_ENVIRONMENTS,
    TIER_NAMES,
)

REQUIRED_FILES = {
    "attribute_schema.json",
    "catalog.json",
    "instructions.json",
    "meta.json",
    "pool.json",
    "preferences.json",
    "steering.json",
}


def shipped_scenarios() -> tuple[str, ...]:
    return tuple(
        sorted(
            path.name
            for path in serialize.DATA_ROOT.iterdir()
            if path.is_dir() and (path / "catalog.json").is_file()
        )
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name}: invalid JSON: {exc}") from exc


def _product_id(product: dict) -> str | None:
    return product.get("asin") or product.get("sku") or product.get("id")


def _steering_ids(raw: dict) -> set[str]:
    identifiers: set[str] = set()
    for spec in raw.values():
        if not isinstance(spec, dict):
            continue
        for key in ("decoy_skus", "bury_skus", "pin_skus", "repeat_skus"):
            value = spec.get(key)
            if isinstance(value, list):
                identifiers.update(str(item) for item in value)
        params = spec.get("params") or {}
        if isinstance(params, dict):
            for key in ("best_seller_sku", "choice_sku", "promo_sku"):
                if params.get(key):
                    identifiers.add(str(params[key]))
            for key in ("decoy_skus", "bury_skus", "repeat_skus"):
                value = params.get(key)
                if isinstance(value, list):
                    identifiers.update(str(item) for item in value)
    return identifiers


def validate_scenario(
    scenario_id: str, *, measured_variants: Iterable[str] | None = None
) -> dict[str, Any]:
    root = serialize.scenario_dir(scenario_id)
    issues: list[str] = []
    if not root.is_dir():
        return {"scenario": scenario_id, "issues": ["scenario directory is missing"]}

    missing = sorted(name for name in REQUIRED_FILES if not (root / name).is_file())
    if missing:
        issues.append(f"missing required files: {missing}")
        return {"scenario": scenario_id, "issues": issues}

    try:
        catalog = _read_json(root / "catalog.json")
        pool_raw = _read_json(root / "pool.json")
        preferences = serialize.load_preferences(scenario_id)
        instructions = serialize.load_instructions(scenario_id)
        meta = serialize.load_meta(scenario_id)
        steering = _read_json(root / "steering.json")
        schema = _read_json(root / "attribute_schema.json")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        issues.append(str(exc))
        return {"scenario": scenario_id, "issues": issues}

    products = catalog.get("products") or []
    if not isinstance(products, list) or not products:
        issues.append("catalog has no products")
        products = []
    if not isinstance(pool_raw, list) or not pool_raw:
        issues.append("pool has no rows")
        pool_raw = []

    catalog_ids = [_product_id(product) for product in products]
    pool_ids = [row.get("asin") for row in pool_raw]
    if None in catalog_ids or len(set(catalog_ids)) != len(catalog_ids):
        issues.append("catalog product identifiers are missing or duplicated")
    if None in pool_ids or len(set(pool_ids)) != len(pool_ids):
        issues.append("pool product identifiers are missing or duplicated")
    if set(catalog_ids) != set(pool_ids):
        issues.append("catalog and pool identifier sets differ")

    if meta.get("scenario_id") != scenario_id:
        issues.append("meta scenario_id differs from directory name")
    if meta.get("n_products") != len(products):
        issues.append("meta n_products differs from catalog size")
    if schema.get("scenario_id") not in {
        scenario_id,
        scenario_id.removesuffix("_hard"),
    }:
        issues.append("attribute schema scenario_id is inconsistent")

    variants = set(preferences)
    if not variants:
        issues.append("no task variants are defined")
    if set(instructions) != variants:
        issues.append("instruction and preference variant sets differ")
    for variant, instruction in instructions.items():
        if not str(instruction.text).strip():
            issues.append(f"{variant}: instruction is empty")

    try:
        rows = serialize.load_pool(scenario_id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        rows = []
        issues.append(f"pool schema error: {exc}")
    candidates = [{**row.attrs(), "no_addons": True} for row in rows]
    optimal_by_variant: dict[str, list[str]] = {}
    relative_winners: list[str] = []
    tagged = [row.asin for row in rows if row.decoy_kind == "hero"]

    selected_variants = set(measured_variants or preferences)
    unknown_variants = selected_variants - set(preferences)
    if unknown_variants:
        issues.append(f"unknown measured variants: {sorted(unknown_variants)}")
    for variant, preference in preferences.items():
        if variant not in selected_variants:
            continue
        winners = optimal_indices(candidates, preference.dsl(), preference.graded_map())
        winner_ids = sorted(rows[index].asin for index in winners)
        optimal_by_variant[variant] = winner_ids
        if not winner_ids:
            issues.append(f"{variant}: no optimal catalog item exists")
        if preference.graded_map():
            if len(winner_ids) != 1:
                issues.append(
                    f"{variant}: expected one optimal item, found {len(winner_ids)}"
                )
            elif winner_ids:
                relative_winners.append(winner_ids[0])

    if relative_winners and len(set(relative_winners)) != 1:
        issues.append("relative variants do not share one optimal item")
    if tagged and len(tagged) != 1:
        issues.append(f"expected at most one hero-tagged item, found {len(tagged)}")
    if tagged and relative_winners and tagged[0] != relative_winners[0]:
        issues.append("hero-tagged item differs from the binary optimum")

    advertised = {row.asin for row in rows if row.advertised}
    relative_optimal = set(relative_winners)
    promoted_optimal = sorted(advertised & relative_optimal)
    if promoted_optimal:
        issues.append(f"promoted items are optimal: {promoted_optimal}")

    steering_ids = _steering_ids(steering)
    unknown_steering = sorted(steering_ids - set(pool_ids))
    if unknown_steering:
        issues.append(f"steering references unknown products: {unknown_steering[:10]}")

    roles = Counter(str(row.get("role") or "") for row in pool_raw)
    return {
        "scenario": scenario_id,
        "products": len(products),
        "variants": sorted(variants),
        "optimal_by_variant": optimal_by_variant,
        "roles": dict(sorted(roles.items())),
        "issues": issues,
    }


def validate(scenarios: Iterable[str] | None = None) -> list[dict[str, Any]]:
    selected = tuple(scenarios or SHOP_STANDARD_SCENARIOS)
    return [validate_scenario(scenario_id) for scenario_id in selected]


def validate_storefront_environment(environment: str) -> dict[str, Any]:
    """Validate one non-shop environment's binary optimal-selection oracle."""
    from ..envs._storefront.tasks7 import VARIANTS7, project

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
    items = list(getattr(catalog, "items", None) or catalog.listings)
    items = [item for item in items if getattr(item, "role", "") != "addon"]
    candidates = [item.attrs() for item in items]
    heroes = {index for index, item in enumerate(items) if item.role == "hero"}
    advertised = {index for index, item in enumerate(items) if item.advertised}
    issues: list[str] = []
    optimal_by_variant: dict[str, list[str]] = {}
    shared_relative_winner: set[int] | None = None

    for variant in VARIANTS7:
        preferences, graded = project(spec, variant)
        winners = optimal_indices(candidates, preferences, graded)
        optimal_by_variant[variant] = sorted(
            str(
                getattr(items[index], "sku", None)
                or getattr(items[index], "listing_id", None)
                or getattr(items[index], "title", "?")
            )
            for index in winners
        )
        if not winners:
            issues.append(f"{variant}: no optimal selection")
        if graded and len(winners) != 1:
            issues.append(
                f"{variant}: expected one unique optimum, found {len(winners)}"
            )
        if graded and len(winners) == 1:
            if shared_relative_winner is None:
                shared_relative_winner = winners
            elif winners != shared_relative_winner:
                issues.append(f"{variant}: relative optimum differs across variants")
        if heroes and not heroes.issubset(winners):
            issues.append(f"{variant}: authored reference item is not optimal")
        if advertised & winners:
            issues.append(f"{variant}: an advertised item is optimal")
    return {
        "environment": environment,
        "products": len(items),
        "optimal_by_variant": optimal_by_variant,
        "issues": issues,
    }


def validate_tier(tier: str) -> list[dict[str, Any]]:
    """Validate every oracle used by one canonical tier."""
    if tier == CAVEAT_STANDARD:
        reports = [
            validate_scenario(scenario, measured_variants=STANDARD_SHOP_VARIANTS)
            for scenario in SHOP_STANDARD_SCENARIOS
        ]
        reports.extend(
            validate_storefront_environment(env) for env in STOREFRONT_ENVIRONMENTS
        )
        return reports
    if tier == CAVEAT_HARD:
        return [
            validate_scenario(scenario, measured_variants=HARD_VARIANTS)
            for scenario in SHOP_HARD_SCENARIOS
        ]
    raise ValueError(f"unknown tier {tier!r}")


def print_validation(tiers: Iterable[str]) -> bool:
    passed = True
    for tier in tiers:
        print(f"{tier}:")
        for report in validate_tier(tier):
            label = report.get("scenario") or report.get("environment") or "?"
            ok = not report["issues"]
            passed &= ok
            print(
                f"  {label}: {'OK' if ok else 'FAIL'} products={report.get('products', 0)}"
            )
            for issue in report["issues"]:
                print(f"    ISSUE: {issue}")
    print("ALL OK" if passed else "FAILURES PRESENT")
    return passed


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=TIER_NAMES, action="append")
    args = parser.parse_args(argv)
    return 0 if print_validation(args.tier or TIER_NAMES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
