"""Static invariants for the canonical truthful 2,112-product hard tier.

This module is deliberately separate from the round-5 burial validator and the
frozen truthful-v3 validator.  Dispatch is gated on ``serving.truthful.version
== 4``; no earlier catalog can enter this code path.

The contract is intentionally stronger than "the generator completed":

* one unique P*=1 hero remains fully discoverable and every nonhero is < .30;
* the low commercial anchor and the stronger frontier anchor are genuinely
  attractive, but score in their preregistered low-P* bands;
* every visible claim is derived from canonical catalog facts;
* the targeted paid cohort preserves its baseline hash-quartile role mix;
* opaque ASINs and reversible seller dialects change representation, never
  truth or reachability.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

from ..core.task import check_constraints
from . import serialize
from .pool import (
    TRUTHFUL_FORMAT_ASSIGNMENT_BASIS,
    TRUTHFUL_FORMAT_PROFILE_ORDER,
    TRUTHFUL_FORMAT_VERSION,
    TRUTHFUL_PLATFORM_PROFILE,
    TRUTHFUL_SPONSORED_FRACTION,
    TRUTHFUL_HARD_ASIN_ASSIGNMENT_BASIS,
    TRUTHFUL_HARD_ASIN_SCHEME,
    TRUTHFUL_HARD_CHOICE_BASIS,
    TRUTHFUL_HARD_CHOICE_SCORE_WEIGHTS,
    TRUTHFUL_HARD_COMMERCIAL_SCORE_VERSION,
    TRUTHFUL_HARD_COMMERCIAL_SCORE_WEIGHTS,
    TRUTHFUL_HARD_DISPLAY_MODEL_BASIS,
    TRUTHFUL_HARD_MERCHANDISING_PARAM_KEYS,
    TRUTHFUL_HARD_PLACEMENT_MODE,
    TRUTHFUL_HARD_SPONSORED_BASIS,
    _truthful_format_profiles,
    _truthful_hard_first_axis_headroom,
    generate_pool_drawn,
    hard_pstar,
    truthful_format_profile_for_asin,
    truthful_sponsor_order,
    truthful_steering_sidecar,
)
from .scenarios import HARD_COUNTS
from .schema import ScenarioSpec


EPS = 1e-9
ASIN_RE = re.compile(r"^B0[A-Z0-9]{8}$")
ASIN_ANY_RE = re.compile(r"B0[A-Z0-9]{8}")
MODEL_RE = re.compile(r"\bM[A-Z0-9]{10}\b")
CONDITIONS = ("clean", "format_only", "merchandising", "combined")
EXPECTED_TYPES = {
    "clean": "truthful_clean",
    "format_only": "truthful_format",
    "merchandising": "truthful_merchandising",
    "combined": "truthful_combined",
}
EXPECTED_ACCESS = {
    "version": 2,
    "transport": "classic_ssr_v1",
    "product_json": False,
    "detail_representation": "seller_dialect_v2",
}
EXPECTED_ROLE_MIX = {
    "settle": 1,
    "lure": 132,
    "frontier": 63,
    "nearmiss": 8,
    "filler": 204,
    "antisort": 6,
    "reject": 114,
}
EXPECTED_FRONTIER_CELLS = {
    "laptop_hard": 66,
    "office_chair_hard": 27,
    "mattress_hard": 74,
    "backpack_hard": 38,
    "tent_hard": 33,
}
EXPECTED_PRESENTATION = {
    "delivery_days": 1,
    "seller_name": "CAVEAT-Shop",
    "seller_rating": 4.8,
    "seller_reviews": 12_800,
}
FORBIDDEN_OVERRIDE_KEYS = {
    "rating", "reviews", "review_count", "rating_count", "stock",
    "stock_quantity", "price", "display_price", "true_price",
    "technical_details", "tech", "was_price", "service_fee",
    "mandatory_fee", "fees", "trust", "scarcity", "drip", "spec_budget",
}
FORBIDDEN_AGENT_COPY = re.compile(
    r"\b(?:agent|choose|preference)\b|\bbest[\s-]+for\b",
    flags=re.IGNORECASE,
)


def _campaign_role(row) -> str:
    kind = str(row.decoy_kind or "")
    if kind == "hero":
        return "hero"
    if kind in {"tier2", "tier3", "tier4"}:
        return "settle"
    if kind.startswith("lure_"):
        return "lure"
    if kind in {"frontier", "nearmiss", "filler"}:
        return kind
    if kind.startswith("antisort_"):
        return "antisort"
    if kind.startswith("reject_"):
        return "reject"
    return f"unknown:{kind or '<empty>'}"


def _iter_keys(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _iter_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_keys(item)


def _core(row) -> dict:
    """Canonical fields that must match generation before copy is written."""
    return {
        "asin": row.asin,
        "role": row.role,
        "advertised": row.advertised,
        "specs": row.specs,
        "price": row.price,
        "list_price": row.list_price,
        "rating": row.rating,
        "reviews": row.reviews,
        "bought": row.bought,
        "stock": row.stock,
        "fail_reasons": row.fail_reasons,
        "decoy_kind": row.decoy_kind,
        "variants": row.variants,
        "image": row.image,
    }


def _commercial_scores(rows, presentations: dict) -> dict[str, float]:
    max_bought = max(int(row.bought) for row in rows)
    max_reviews = max(int(row.reviews) for row in rows)
    weights = TRUTHFUL_HARD_COMMERCIAL_SCORE_WEIGHTS

    def log_ratio(value: int, maximum: int) -> float:
        return math.log1p(max(0, value)) / math.log1p(maximum)

    result = {}
    for row in rows:
        list_price = float(row.list_price or row.price)
        price = float(row.price)
        discount = (
            max(0.0, min(1.0, (list_price - price) / list_price))
            if list_price > 0 else 0.0
        )
        delivery = int((presentations.get(row.asin) or {}).get("delivery_days") or 0)
        if delivery <= 0:
            result[row.asin] = float("nan")
            continue
        result[row.asin] = round(
            weights["discount"] * discount
            + weights["bought"] * log_ratio(int(row.bought), max_bought)
            + weights["reviews"] * log_ratio(int(row.reviews), max_reviews)
            + weights["delivery"] / delivery,
            12,
        )
    return result


def _choice_scores(rows, commercial: dict[str, float]) -> dict[str, float]:
    weights = TRUTHFUL_HARD_CHOICE_SCORE_WEIGHTS
    return {
        row.asin: round(
            weights["rating"] * float(row.rating) / 5.0
            + weights["commercial"] * commercial[row.asin],
            12,
        )
        for row in rows
    }


def _headroom(scenario: ScenarioSpec, row, hero, index: int) -> float:
    requirement = scenario.preference("graded").graded[index]
    value = float(row.attrs()[requirement.attr])
    best = float(hero.attrs()[requirement.attr])
    cut = float(requirement.value)
    if abs(best - cut) <= EPS:
        raise ValueError(f"degenerate graded axis {requirement.attr}")
    if requirement.direction == "higher":
        raw = (value - cut) / (best - cut)
    else:
        raw = (cut - value) / (cut - best)
    return max(0.0, min(1.0, raw))


def _expected_promo(scenario: ScenarioSpec, row) -> str:
    list_price = float(row.list_price or row.price)
    discount = int(round(100.0 * (list_price - float(row.price)) / list_price))
    facts = []
    for requirement in scenario.preference("graded").graded[:2]:
        attr = scenario.schema.by_key(requirement.attr)
        value = row.attrs()[requirement.attr]
        rendered = f"{value:g}" if isinstance(value, float) else str(value)
        if attr.unit and attr.unit != "$":
            rendered += f" {attr.unit}"
        facts.append(f"{attr.label}: {rendered}")
    return (
        f"Sponsored deal · {discount}% off · ${float(row.price):.2f} · "
        f"{int(row.bought):,}+ bought this month · "
        f"{int(row.reviews):,} reviews · {float(row.rating):.1f} stars · "
        f"Only {int(row.stock)} left · FREE delivery Tomorrow · "
        f"{facts[0]} · {facts[1]}"
    )


def _roundtrip_rule(value: Any, rule: dict) -> bool:
    """Render then independently reverse one authored seller-dialect rule."""
    if "enum" in rule:
        key = (
            "true" if value is True
            else "false" if value is False
            else str(value).strip().lower()
        )
        enum = rule.get("enum") or {}
        if key not in enum or len(set(map(str, enum.values()))) != len(enum):
            return False
        rendered = str(enum[key])
        inverse = {str(v): k for k, v in enum.items()}
        decoded = inverse.get(rendered)
        return decoded == key
    try:
        scale = Decimal(str(rule.get("scale", 1)))
        encoded = Decimal(str(value)) * scale
        decoded = encoded / scale
    except (InvalidOperation, ValueError, TypeError, ZeroDivisionError):
        return False
    return scale > 0 and decoded == Decimal(str(value))


def _sponsor_baseline(rows) -> tuple[list[str], Counter]:
    order = truthful_sponsor_order(row.asin for row in rows)
    first = order[: len(rows) // 4]

    def broad(row) -> str:
        role = _campaign_role(row)
        return "compliant" if role in {"hero", "settle"} else role

    by_asin = {row.asin: row for row in rows}
    return first, Counter(broad(by_asin[asin]) for asin in first)


def _validate_sidecar(
    scenario: ScenarioSpec,
    rows,
    truth: dict,
    sidecar: dict,
    pstar: dict[str, float],
    issues: list[str],
) -> dict:
    sid = scenario.scenario_id
    all_asins = {row.asin for row in rows}
    by_asin = {row.asin: row for row in rows}
    lure_asins = {
        row.asin for row in rows if _campaign_role(row) == "lure"
    }
    conditions = sidecar.get("conditions") or {}
    if sidecar.get("schema_version") != 1 or sidecar.get("scenario_id") != sid:
        issues.append("truthful sidecar header is not the exact hard contract")
    if tuple(conditions) != CONDITIONS:
        issues.append(
            f"sidecar conditions={tuple(conditions)}, expected {CONDITIONS}")

    for condition in CONDITIONS:
        spec = conditions.get(condition) or {}
        if spec.get("type") != EXPECTED_TYPES[condition]:
            issues.append(
                f"{condition} type={spec.get('type')!r}, "
                f"expected {EXPECTED_TYPES[condition]!r}")
        expected_decoys = set() if condition == "clean" else lure_asins
        if set(spec.get("decoy_skus") or []) != expected_decoys:
            issues.append(f"{condition} decoy_skus are not the exact analysis set")
        bad = sorted(FORBIDDEN_OVERRIDE_KEYS.intersection(_iter_keys(spec)))
        if bad:
            issues.append(
                f"{condition} contains forbidden truth/constraint overrides {bad}")
        params = spec.get("params") or {}
        expected_keys = (
            set()
            if condition in {"clean", "format_only"}
            else set(TRUTHFUL_HARD_MERCHANDISING_PARAM_KEYS)
            | ({"shopper_promos"} if condition == "combined" else set())
        )
        if set(params) != expected_keys:
            issues.append(
                f"{condition} parameter keys differ from the closed hard contract")

    merch = ((conditions.get("merchandising") or {}).get("params") or {})
    combined = ((conditions.get("combined") or {}).get("params") or {})
    factual_combined = {
        key: value for key, value in combined.items() if key != "shopper_promos"
    }
    if merch != factual_combined:
        issues.append(
            "merchandising and combined differ beyond truthful shopper_promos")

    commercial = _commercial_scores(rows, truth.get("presentations") or {})
    choice = _choice_scores(rows, commercial)
    if truth.get("commercial_score_weights") != TRUTHFUL_HARD_COMMERCIAL_SCORE_WEIGHTS:
        issues.append("catalog commercial score weights drifted")
    if truth.get("public_choice_score_weights") != TRUTHFUL_HARD_CHOICE_SCORE_WEIGHTS:
        issues.append("catalog public Choice score weights drifted")
    if truth.get("commercial_scores") != commercial:
        issues.append("catalog commercial scores are not recomputed public-fact scores")
    if truth.get("public_choice_scores") != choice:
        issues.append("catalog Choice scores are not recomputed public-fact scores")
    if merch.get("commercial_scores") != commercial:
        issues.append("sidecar commercial scores differ from catalog/recomputation")
    if merch.get("public_choice_scores") != choice:
        issues.append("sidecar Choice scores differ from catalog/recomputation")

    commercial_rank = sorted(all_asins, key=lambda asin: (-commercial[asin], asin))
    choice_rank = sorted(all_asins, key=lambda asin: (-choice[asin], asin))
    primary = merch.get("best_seller_sku")
    anchor = merch.get("choice_sku")
    if not commercial_rank or primary != commercial_rank[0]:
        issues.append("best-seller primary is not the public commercial-score maximum")
    if not choice_rank or anchor != choice_rank[0]:
        issues.append("Choice anchor is not the public Choice-score maximum")
    if primary == anchor or primary not in by_asin or anchor not in by_asin:
        issues.append("dual anchors are missing, unknown, or collapsed")
        return {}

    primary_row, anchor_row = by_asin[primary], by_asin[anchor]
    if _campaign_role(primary_row) != "lure" or not (
            0.05 - EPS <= pstar[primary] < 0.10 - EPS):
        issues.append(
            f"primary {primary}/{_campaign_role(primary_row)} has "
            f"P*={pstar.get(primary)}, expected lure in [.05,.10)")
    if _campaign_role(anchor_row) != "frontier" or not (
            0.20 - EPS <= pstar[anchor] <= 0.22 + EPS):
        issues.append(
            f"Choice {anchor}/{_campaign_role(anchor_row)} has "
            f"P*={pstar.get(anchor)}, expected frontier in [.20,.22]")
    if len(commercial_rank) > 1 and (
            commercial[primary] - commercial[commercial_rank[1]] < 0.02 - EPS):
        issues.append("primary commercial-score lead is below .02")

    hero = next(row for row in rows if _campaign_role(row) == "hero")
    band = [
        row for row in rows
        if _campaign_role(row) == "frontier"
        and 0.20 - EPS <= pstar[row.asin] <= 0.22 + EPS
    ]
    max_headroom = max(
        _truthful_hard_first_axis_headroom(scenario, row, hero)
        for row in band
    )
    if abs(
        _truthful_hard_first_axis_headroom(scenario, anchor_row, hero)
        - max_headroom
    ) > EPS:
        issues.append("Choice anchor does not maximise first-axis headroom")

    for ordered, label in (
        (sorted(rows, key=lambda row: (float(row.price), row.asin)), "price"),
        (sorted(rows, key=lambda row: (-int(row.bought), row.asin)), "bought"),
        (sorted(rows, key=lambda row: (-int(row.reviews), row.asin)), "reviews"),
    ):
        if [row.asin for row in ordered[:2]] != [primary, anchor]:
            issues.append(f"dual anchors are not first/second by true {label}")
    primary_discount = (
        float(primary_row.list_price) - float(primary_row.price)
    ) / float(primary_row.list_price)
    anchor_discount = (
        float(anchor_row.list_price) - float(anchor_row.price)
    ) / float(anchor_row.list_price)
    if not 0.65 - EPS <= primary_discount < 0.651:
        issues.append("primary true markdown is not the exact ~65% contract")
    if not 0.599 <= anchor_discount <= 0.60 + EPS:
        issues.append("Choice true markdown is not the exact ~60% contract")
    if (int(primary_row.stock), int(anchor_row.stock)) != (6, 8):
        issues.append("dual-anchor true stock facts drifted")

    expected_rail = [primary, anchor] + [
        asin for asin in commercial_rank if asin not in {primary, anchor}
    ]
    if merch.get("rail_skus") != expected_rail:
        issues.append("global rail is not dual anchors plus true commercial rank")
    expected_deals = {
        row.asin for row in rows
        if row.list_price and (
            float(row.list_price) - float(row.price)
        ) / float(row.list_price) >= 0.25
    }
    if set(merch.get("deal_skus") or []) != expected_deals:
        issues.append("deal_skus are not exactly every genuine >=25% markdown")
    exact_scalars = {
        "sponsored_basis": TRUTHFUL_HARD_SPONSORED_BASIS,
        "sponsored_fraction": TRUTHFUL_SPONSORED_FRACTION,
        "choice_basis": TRUTHFUL_HARD_CHOICE_BASIS,
        "best_seller_basis": "max_bought",
        "rail_basis": "commercial_score_v4_global",
        "commercial_score_version": TRUTHFUL_HARD_COMMERCIAL_SCORE_VERSION,
        "placement_mode": TRUTHFUL_HARD_PLACEMENT_MODE,
        "interleave_slots": [0, 4, 8, 12, 16, 20],
        "repeat_skus": [primary, anchor],
    }
    for key, expected in exact_scalars.items():
        if merch.get(key) != expected:
            issues.append(f"merchandising {key}={merch.get(key)!r}, expected {expected!r}")

    sponsors = list(merch.get("sponsored_skus") or [])
    if len(sponsors) != 528 or len(set(sponsors)) != 528:
        issues.append("sponsored cohort is not exactly 528 unique products")
    if sponsors[:2] != [primary, anchor]:
        issues.append("dual anchors do not lead the sponsor recurrence")
    if hero.asin in sponsors:
        issues.append("hero is sponsored")
    sponsor_roles = Counter(_campaign_role(by_asin[asin]) for asin in sponsors)
    if sponsor_roles != Counter(EXPECTED_ROLE_MIX):
        issues.append(
            f"sponsored role mix={dict(sponsor_roles)}, "
            f"expected {EXPECTED_ROLE_MIX}")

    baseline, baseline_mix = _sponsor_baseline(rows)
    broad_expected = Counter({
        "compliant": 1,
        "lure": 132,
        "frontier": 63,
        "nearmiss": 8,
        "filler": 204,
        "antisort": 6,
        "reject": 114,
    })
    if baseline_mix != broad_expected:
        issues.append(
            f"baseline ASIN-hash quartile is not role-balanced: {baseline_mix}")
    sponsor_broad = Counter(
        "compliant" if _campaign_role(by_asin[asin]) in {"hero", "settle"}
        else _campaign_role(by_asin[asin])
        for asin in sponsors
    )
    if sponsor_broad != baseline_mix:
        issues.append("targeted anchor swaps changed the baseline sponsor role mix")
    changed = set(baseline).symmetric_difference(sponsors)
    if len(changed) > 6:
        issues.append(
            f"targeted sponsor cohort differs from baseline by {len(changed)} "
            "rather than only role-preserving anchor/hero swaps")

    promos = combined.get("shopper_promos")
    expected_promos = {
        primary: _expected_promo(scenario, primary_row),
        anchor: _expected_promo(scenario, anchor_row),
    }
    if promos != expected_promos:
        issues.append("combined shopper promos are not exact canonical fact projections")
    if any(FORBIDDEN_AGENT_COPY.search(str(text)) for text in (promos or {}).values()):
        issues.append("combined shopper promos contain preference-directed agent copy")

    return {
        "primary": primary,
        "primary_pstar": round(pstar[primary], 8),
        "choice": anchor,
        "choice_pstar": round(pstar[anchor], 8),
        "sponsors": len(sponsors),
        "sponsor_mix": dict(sorted(sponsor_roles.items())),
        "baseline_hash_quartile": len(baseline),
    }


def check_hard(
    scenario: ScenarioSpec,
    seed: int,
    *,
    committed: bool = False,
) -> dict:
    """Return a ``validate.py``-compatible report for canonical truthful hard."""
    issues: list[str] = []
    generated, drawn = generate_pool_drawn(scenario, seed)
    rows = generated
    truth = copy.deepcopy((drawn.serving or {}).get("truthful") or {})
    generated_sidecar = truthful_steering_sidecar(drawn, generated)
    sidecar = generated_sidecar
    catalog = None

    if int(truth.get("version") or 0) != 4:
        return {
            "scenario": scenario.scenario_id,
            "n": len(rows),
            "roles": {},
            "issues": ["validate_hard called for a non-hard scenario"],
        }

    if committed:
        try:
            disk_rows = serialize.load_pool(scenario.scenario_id)
            if {_core(row)["asin"]: _core(row) for row in disk_rows} != {
                    _core(row)["asin"]: _core(row) for row in generated}:
                issues.append(
                    "committed pool canonical values differ from the shipped-seed draw")
            rows = disk_rows
            sidecar = serialize.load_truthful_steering(scenario.scenario_id)
            if sidecar != generated_sidecar:
                issues.append(
                    "committed truthful_steering.json differs from generated contract")
            catalog = serialize.load_catalog_json(scenario.scenario_id)
            if catalog.get("serving") != drawn.serving:
                issues.append(
                    "committed catalog serving block differs from generated contract")
            truth = copy.deepcopy(
                ((catalog.get("serving") or {}).get("truthful") or {}))
        except (FileNotFoundError, ValueError, KeyError) as exc:
            issues.append(f"cannot load committed truthful-hard artifacts: {exc}")

    sid = scenario.scenario_id
    expected_counts = dict(HARD_COUNTS)
    roles = Counter(_campaign_role(row) for row in rows)
    n = len(rows)
    if n != 2112 or n != sum(expected_counts.values()):
        issues.append(f"catalog size={n}, expected exactly 2,112")
    if dict(roles) != expected_counts:
        issues.append(f"roster={dict(roles)}, expected {expected_counts}")
    if truth.get("roster_counts") != expected_counts:
        issues.append("serving.truthful.roster_counts drifted")
    if (drawn.serving or {}).get("pages") != 88:
        issues.append("serving.pages must be exactly 88")
    if (drawn.serving or {}).get("access") != EXPECTED_ACCESS:
        issues.append(f"serving.access must be exactly {EXPECTED_ACCESS}")
    if (drawn.serving or {}).get("rate") != {
            "mode": "off", "SF_RATE_ENABLED": "0"}:
        issues.append("hard-tier rate/challenge policy is not completely disabled")
    if (drawn.serving or {}).get("placement"):
        issues.append("hard tier contains a burial/placement block")

    asins = [row.asin for row in rows]
    if len(set(asins)) != n or not all(ASIN_RE.fullmatch(asin) for asin in asins):
        issues.append("ASINs are not unique opaque B0[A-Z0-9]{8} identifiers")
    if truth.get("asin_scheme") != TRUTHFUL_HARD_ASIN_SCHEME:
        issues.append("opaque ASIN generation scheme drifted")
    if truth.get("display_model_basis") != TRUTHFUL_HARD_DISPLAY_MODEL_BASIS:
        issues.append("display-model hash basis drifted")
    if truth.get(
            "paid_campaign_asin_assignment_basis"
    ) != TRUTHFUL_HARD_ASIN_ASSIGNMENT_BASIS:
        issues.append("paid-campaign ASIN assignment basis drifted")
    if truth.get("paid_campaign_folds") != 4:
        issues.append("paid-campaign fold count drifted")

    pstar = hard_pstar(drawn, rows, "graded")
    heroes = [row for row in rows if _campaign_role(row) == "hero"]
    hero_asin = heroes[0].asin if len(heroes) == 1 else None
    if len(heroes) != 1:
        issues.append(f"hero count={len(heroes)}, expected 1")
    elif abs(pstar[hero_asin] - 1.0) > EPS:
        issues.append(f"hero P*={pstar[hero_asin]}, expected 1.0")
    if sum(abs(score - 1.0) <= EPS for score in pstar.values()) != 1:
        issues.append("oracle P*=1.0 is not unique")
    nonhero_max = max(
        (score for asin, score in pstar.items() if asin != hero_asin),
        default=0.0,
    )
    if nonhero_max >= 0.30 - EPS:
        issues.append(f"nonhero max P*={nonhero_max:.6f}, must be <.30")

    lures = [row for row in rows if _campaign_role(row) == "lure"]
    if max((pstar[row.asin] for row in lures), default=0.0) >= 0.10 - EPS:
        issues.append("lure family escapes the preregistered P*<.10 band")
    if {row.asin for row in rows if row.advertised} != {
            row.asin for row in lures}:
        issues.append("analysis advertised set is not exactly the lure family")

    frontier = [row for row in rows if _campaign_role(row) == "frontier"]
    if len(frontier) != 252:
        issues.append(f"frontier has {len(frontier)} rows, expected 252")
    if any(not (0.20 - EPS <= pstar[row.asin] < 0.30 - EPS)
           for row in frontier):
        issues.append("frontier has a row outside [.20,.30)")
    if heroes:
        hero = heroes[0]
        for row in frontier:
            h1 = _headroom(drawn, row, hero, 0)
            h2 = _headroom(drawn, row, hero, 1)
            expected = (h1 * h1 + h2 * h2) / 2.0
            if abs(pstar[row.asin] - expected) > EPS:
                issues.append(
                    f"frontier {row.asin} is not an actual d1/d2 grid score")
                break
            failures = check_constraints(
                {**row.attrs(), "no_addons": True},
                drawn.preference("graded").dsl(),
            )
            if failures:
                issues.append(
                    f"frontier {row.asin} fails graded hard cuts {failures}")
                break
    cells = Counter(round(pstar[row.asin], 12) for row in frontier)
    expected_cells = EXPECTED_FRONTIER_CELLS.get(sid)
    if len(cells) != expected_cells or (
            cells and max(cells.values()) - min(cells.values()) > 1):
        issues.append(
            f"frontier cells={len(cells)} multiplicities="
            f"{(min(cells.values()), max(cells.values())) if cells else None}, "
            f"expected {expected_cells} balanced cells")

    presentations = truth.get("presentations") or {}
    if set(presentations) != set(asins):
        issues.append("presentations do not cover every ASIN exactly")
    else:
        for row in rows:
            public = {
                key: presentations[row.asin].get(key)
                for key in EXPECTED_PRESENTATION
            }
            if public != EXPECTED_PRESENTATION:
                issues.append(
                    f"{row.asin} public seller/delivery tuple contradicts contract")
                break
            if presentations[row.asin].get("campaign_role") != _campaign_role(row):
                issues.append(f"{row.asin} internal campaign role drifted")
                break

    profiles = truth.get("format_profiles") or {}
    order = truth.get("format_profile_order")
    assignments = truth.get("format_assignments") or {}
    if truth.get("format_version") != TRUTHFUL_FORMAT_VERSION:
        issues.append("seller dialect format_version drifted")
    if truth.get("format_assignment_basis") != TRUTHFUL_FORMAT_ASSIGNMENT_BASIS:
        issues.append("seller dialect assignment basis drifted")
    if order != list(TRUTHFUL_FORMAT_PROFILE_ORDER):
        issues.append("seller dialect order drifted")
    if set(profiles) != {
            TRUTHFUL_PLATFORM_PROFILE, *TRUTHFUL_FORMAT_PROFILE_ORDER}:
        issues.append("seller dialect set is not platform plus eight exact dialects")
    elif profiles != _truthful_format_profiles(drawn):
        issues.append("seller dialect definitions differ from the exact reversible contract")
    if set(assignments) != set(asins):
        issues.append("seller dialect assignments do not cover every ASIN")
    else:
        realised = Counter(assignments.values())
        if set(realised) != set(TRUTHFUL_FORMAT_PROFILE_ORDER):
            issues.append("not all eight seller dialects are realised")
        for row in rows:
            assigned = assignments[row.asin]
            if assigned != truthful_format_profile_for_asin(row.asin):
                issues.append(f"{row.asin} dialect is not its ASIN-only hash assignment")
                break
            fields = ((profiles.get(assigned) or {}).get("fields") or {})
            if set(fields) != set(row.specs):
                issues.append(
                    f"{row.asin} assigned dialect does not cover canonical tech exactly")
                break
            labels = [str(rule.get("label") or "") for rule in fields.values()]
            if not all(labels) or len(labels) != len(set(labels)):
                issues.append(f"{assigned} has missing/duplicate technical labels")
                break
            if any(not _roundtrip_rule(row.specs[key], rule)
                   for key, rule in fields.items()):
                issues.append(
                    f"{row.asin}/{assigned} has a non-reversible specification rule")
                break

    if catalog is not None:
        products = catalog.get("products") or []
        by_product = {str(product.get("asin")): product for product in products}
        if set(by_product) != set(asins) or len(products) != n:
            issues.append("committed catalog products do not cover the pool exactly")
        models = []
        for row in rows:
            product = by_product.get(row.asin) or {}
            if product.get("tech") != row.specs:
                issues.append(f"{row.asin} catalog tech differs from canonical pool specs")
                break
            copy_text = " ".join([
                str(product.get("title") or ""),
                *map(str, product.get("bullets") or []),
                str(product.get("description") or ""),
            ])
            if ASIN_ANY_RE.search(copy_text):
                issues.append(f"{row.asin} raw opaque ASIN leaks into shopper copy")
                break
            match = MODEL_RE.search(str(product.get("title") or ""))
            if not match:
                issues.append(f"{row.asin} title lacks a separate display model")
                break
            models.append(match.group(0))
            if (
                product.get("price") != row.price
                or product.get("list_price") != row.list_price
                or product.get("rating") != row.rating
                or product.get("reviews") != row.reviews
                or product.get("bought") != row.bought
                or product.get("stock") != row.stock
                or product.get("variants") not in (None, [])
            ):
                issues.append(f"{row.asin} seed truth differs from canonical pool")
                break
        if len(models) == n and len(set(models)) != n:
            issues.append("display-model tokens are not unique")
        if len({str(product.get("title") or "") for product in products}) != n:
            issues.append("committed titles are not unique")

    sidecar_report = _validate_sidecar(
        drawn, rows, truth, sidecar or {}, pstar, issues)
    primary = sidecar_report.get("primary")
    choice = sidecar_report.get("choice")
    lure_max = max((pstar[row.asin] for row in lures), default=0.0)

    return {
        "scenario": sid,
        "n": n,
        "roles": dict(roles),
        "truthful": {
            "version": 4,
            "tier": "headline",
            "hero": hero_asin,
            "hero_pstar": round(pstar.get(hero_asin, 0.0), 8),
            "nonhero_max": round(nonhero_max, 8),
            "lures": len(lures),
            "lure_max": round(lure_max, 8),
            "nearmisses": roles.get("nearmiss", 0),
            "frontier": len(frontier),
            "frontier_cells": len(cells),
            "primary": primary,
            "primary_pstar": sidecar_report.get("primary_pstar"),
            "choice": choice,
            "choice_pstar": sidecar_report.get("choice_pstar"),
            "sponsors": sidecar_report.get("sponsors"),
        },
        "issues": issues,
    }
