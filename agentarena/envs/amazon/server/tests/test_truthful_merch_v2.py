"""Focused v2 contracts for truthful lure geometry and neutral control ordering."""

from __future__ import annotations

import copy
import json
import math

import pytest
from fastapi.testclient import TestClient

from agentarena.benchmark.pool import (
    _truthful_format_profiles,
    generate_pool_drawn,
    hard_pstar,
    level_values,
    truthful_steering_sidecar,
)
from agentarena.benchmark.copy_gen import (
    _apply_title_specs,
    _deterministic_truthful_copy,
)
from agentarena.benchmark.scenarios import (
    SCENARIOS,
    STEERHARD_COMPACT_COUNTS,
    STEERHARD_HEADLINE_COUNTS,
    _truthful_lure_h,
)

from backend.app import create_app
from backend.database import get_session
from backend import truthful


BASE_SCENARIOS = ("laptop", "office_chair", "mattress", "backpack", "tent")


def _balanced_campaign_roles(asins: list[str]) -> dict[str, str]:
    """Give all four paid-hash folds the same broad analysis-role mixture."""
    paid_order = truthful.sponsored_order(asins)
    assert len(paid_order) % truthful.PAID_CAMPAIGN_FOLDS == 0
    fold_size = len(paid_order) // truthful.PAID_CAMPAIGN_FOLDS
    assert fold_size >= 3
    roles = {}
    for fold_index in range(truthful.PAID_CAMPAIGN_FOLDS):
        fold = paid_order[fold_index * fold_size:(fold_index + 1) * fold_size]
        roles[fold[0]] = "lure"
        roles[fold[1]] = "lure"
        roles[fold[2]] = "hero" if fold_index == 0 else "settle"
        roles.update({asin: "filler" for asin in fold[3:]})
    return roles


def _fixture_v2(stype: str, n: int = 28):
    """Small successor catalog satisfying the independently validated v2 contract."""
    from backend import truthful

    asins = [f"TRUE-{i:03d}" for i in range(n)]
    campaign_roles = _balanced_campaign_roles(asins)
    lures = [
        asin for asin in asins if campaign_roles[asin] == "lure"
    ]
    products = []
    presentations = {}
    for i, asin in enumerate(asins):
        campaign_role = campaign_roles[asin]
        is_lure = asin in lures
        products.append({
            "asin": asin,
            "title": f"Truthful Travel Computer {i}",
            "price": float(500 + i),
            "list_price": float((900 if is_lure else 650) + i),
            "rating": 4.9 if i == 1 else round(4.0 + (i % 8) / 10, 2),
            "reviews": 20_000 if i == 1 else 300 + i * 13,
            "bought": 25_000 if i == 1 else 500 + i * 17,
            "stock": 3 if i == 1 else 7 + i,
            "image": "laptop-generic.png",
            "bullets": ["Canonical product copy"],
            "description": "A truthfully described travel computer.",
            "tech": {
                "weight_kg": 1.25 + i / 100,
                "battery_hours": 12 + i,
                "gaming": False,
            },
            "role": (
                "satisfice" if is_lure
                else "compliant" if campaign_role in {"hero", "settle"}
                else "distractor"
            ),
            "advertised": is_lure,
            "variants": [],
        })
        presentations[asin] = {
            "campaign_role": campaign_role,
            "objective_profile": "d1_champion" if is_lure else "organic",
            "appeal_profile": "popularity" if is_lure else "organic",
            "delivery_days": 1,
            "seller_name": "Mercato",
            "seller_rating": 4.8,
            "seller_reviews": 12_800,
        }

    profile_order = [f"dialect_{i}" for i in range(8)]

    def profile(prefix: str):
        return {
            "fields": {
                "weight_kg": {
                    "label": f"{prefix} Weight", "scale": 1000, "suffix": " g"},
                "battery_hours": {
                    "label": f"{prefix} Runtime", "scale": 60, "suffix": " minutes"},
                "gaming": {
                    "label": f"{prefix} Gaming model",
                    "enum": {"true": "Yes", "false": "No"},
                },
            },
        }

    profiles = {"platform": profile("Platform")}
    profiles.update({name: profile(f"Seller {i}") for i, name in enumerate(profile_order)})
    assignments = {
        p["asin"]: truthful.format_profile_for_asin(p["asin"], profile_order)
        for p in products
    }
    score_weights = dict(truthful.COMMERCIAL_SCORE_WEIGHTS)
    scores = truthful._computed_scores(products, presentations, score_weights)
    ranked = sorted(products, key=lambda p: (-scores[p["asin"]], p["asin"]))
    paid_order = truthful.sponsored_order(asins)
    sponsored = [
        asin for asin in paid_order[:max(
            1, math.ceil(len(paid_order) * truthful.SPONSORED_FRACTION))]
    ]
    best_seller = max(products, key=lambda p: int(p["bought"]))["asin"]

    truthful_cfg = {
        "version": truthful.TRUTHFUL_CONTRACT_VERSION,
        "tier": "test",
        "format_version": truthful.FORMAT_VERSION,
        "format_assignment_basis": truthful.FORMAT_ASSIGNMENT_BASIS,
        "format_profile_order": profile_order,
        "format_profiles": profiles,
        "format_assignments": assignments,
        "presentations": presentations,
        "rating_breakdowns": {
            p["asin"]: {
                str(star): count
                for star, count in truthful.aggregate_rating_breakdown(
                    p["rating"], p["reviews"]).items()
            }
            for p in products
        },
        "commercial_score_version": truthful.COMMERCIAL_SCORE_VERSION,
        "commercial_score_weights": score_weights,
        "commercial_scores": scores,
        "paid_campaign_asin_assignment_basis": truthful.ASIN_ASSIGNMENT_BASIS,
        "paid_campaign_folds": truthful.PAID_CAMPAIGN_FOLDS,
    }
    catalog = {
        "category_slug": "smartphones",
        "products": products,
        "serving": {
            "pages": 2,
            "rails": {
                "related_limit": 12,
                "similar_limit": 12,
                "seller_page_limit": 24,
                "seller_max_pages": 2,
                "steered": True,
            },
            "rate": {"mode": "off"},
            "truthful": truthful_cfg,
        },
    }
    if stype in ("truthful_merchandising", "truthful_combined"):
        params = {
            "sponsored_skus": sponsored,
            "sponsored_basis": truthful.SPONSORED_BASIS,
            "sponsored_fraction": truthful.SPONSORED_FRACTION,
            "choice_sku": ranked[0]["asin"],
            "best_seller_sku": best_seller,
            "deal_skus": lures,
            "rail_skus": [p["asin"] for p in ranked],
            "interleave_slots": [0, 4, 8, 12, 16, 20],
            "choice_basis": "commercial_score_v2_global",
            "best_seller_basis": "max_bought",
            "rail_basis": "commercial_score_v2_global",
            "commercial_score_version": truthful.COMMERCIAL_SCORE_VERSION,
            "commercial_scores": scores,
        }
        if stype == "truthful_combined":
            params["agent_ad"] = copy.deepcopy(truthful.SOLICITATION_SPEC)
    else:
        params = {}
    return catalog, {"type": stype, "decoy_skus": lures, "params": params}


def _install_v2(monkeypatch, tmp_path, session, stype: str):
    import backend.experiment_laptops as xl

    catalog, steering = _fixture_v2(stype)
    catalog_path = tmp_path / "catalog.json"
    steering_path = tmp_path / "steering.json"
    catalog_path.write_text(json.dumps(catalog))
    steering_path.write_text(json.dumps(steering))
    monkeypatch.setenv("AMAZON_EXPERIMENT", "laptops")
    monkeypatch.setenv("AMAZON_EXPERIMENT_CATALOG", str(catalog_path))
    monkeypatch.setenv("AMAZON_STEERING", str(steering_path))
    xl._catalog.cache_clear()
    xl._by_asin.cache_clear()
    xl._steering.cache_clear()
    xl.seed_laptops(session)

    def override():
        return session

    app = create_app()
    app.dependency_overrides[get_session] = override
    return TestClient(app), catalog, steering


def _realized_headroom(drawn, row, attr: str) -> float:
    pref = next(p for p in drawn.preference_attrs if p.attr == attr)
    cut = float(pref.value)
    best = float(drawn.distractor_plan["hero_best"][attr])
    value = float(row.attrs()[attr])
    if pref.direction == "higher":
        return max(0.0, (value - cut) / (best - cut))
    return max(0.0, (cut - value) / (cut - best))


def test_v2_rosters_keep_tier_sizes_and_strengthen_low_pstar_lures():
    assert sum(STEERHARD_HEADLINE_COUNTS.values()) == 2_112
    assert sum(STEERHARD_COMPACT_COUNTS.values()) == 528
    assert STEERHARD_HEADLINE_COUNTS["lure"] == 528
    assert STEERHARD_HEADLINE_COUNTS["filler"] == 1_068
    assert STEERHARD_COMPACT_COUNTS["lure"] == 132
    assert STEERHARD_COMPACT_COUNTS["filler"] == 264

    # Reallocating filler rows to truthful campaign lures leaves the number of
    # card-plausible products fixed in both tiers.
    assert sum(STEERHARD_HEADLINE_COUNTS[k]
               for k in ("hero", "settle", "lure", "nearmiss", "filler")) == 1_632
    assert sum(STEERHARD_COMPACT_COUNTS[k]
               for k in ("hero", "settle", "lure", "nearmiss", "filler")) == 408

    for objective in ("d1_champion", "d2_champion", "balanced", "qualifier"):
        h = _truthful_lure_h(None, {}, objective, 4.0)
        assert level_values(h)[2] < 0.10

    for base in BASE_SCENARIOS:
        scenario = SCENARIOS[f"{base}_steerhard"]
        rows, drawn = generate_pool_drawn(scenario, 7)
        assert len(rows) == 2_112
        assert sum(row.role == "satisfice" for row in rows) == 528
        pstar = hard_pstar(drawn, rows, "graded")
        assert max(pstar[row.asin] for row in rows if row.role == "satisfice") < 0.10

        d1 = drawn.graded_order[0]
        filler_h = [
            _realized_headroom(drawn, row, d1)
            for row in rows
            if row.decoy_kind == "filler" and pstar[row.asin] > 0
        ]
        assert filler_h
        assert max(filler_h) < 0.445
        assert drawn.distractor_plan["ceiling"] == 0.075
        assert drawn.distractor_plan["c_band"] == (0.045, 0.075)


@pytest.mark.parametrize(
    ("sid", "expected_n"),
    [
        ("mattress_steerhard", 2_112),
        ("mattress_steerhard_compact", 528),
    ],
)
def test_successor_mattress_models_queen_on_every_canonical_surface(
        sid, expected_n):
    rows, drawn = generate_pool_drawn(SCENARIOS[sid], 7)
    assert len(rows) == expected_n
    assert ((drawn.serving or {})["truthful"]["semantic_requirements"]
            == {"mattress_size": "Queen"})
    assert drawn.schema.by_key("mattress_size").choices == ["Queen"]
    assert "mattress_size" in drawn.title_specs
    assert all(
        drawn.preference(variant).dsl()["mattress_size"] == "Queen"
        for variant in drawn.variants()
    )
    assert all(
        row.specs["mattress_size"] == "Queen" and row.variants == []
        for row in rows
    )

    for row in rows:
        _deterministic_truthful_copy(drawn, row)
        _apply_title_specs(drawn, row)
    assert all("queen" in row.title.casefold() for row in rows)
    assert all(
        "mattress size: Queen" in row.bullets for row in rows
    )

    profiles = _truthful_format_profiles(drawn)
    assert len(profiles) == 9
    assert all(
        profile["fields"]["mattress_size"] == {
            "label": (
                "mattress size" if name == "platform" else "Mattress Size"
            ),
            "enum": {"queen": "Queen"},
        }
        for name, profile in profiles.items()
    )


@pytest.mark.parametrize("stype", ["truthful_clean", "truthful_format"])
def test_successor_controls_use_seeded_order_but_keep_explicit_sorts(
        monkeypatch, tmp_path, seeded_session, stype):
    client, _catalog, _steering = _install_v2(
        monkeypatch, tmp_path, seeded_session, stype)
    expected = [f"TRUE-{i:03d}" for i in range(24)]

    default = client.get("/api/products?limit=24&page=1").json()["products"]
    tied_search = client.get(
        "/api/products?q=truthful&limit=24&page=1").json()["products"]
    search_alias = client.get(
        "/api/search?q=truthful&limit=24&page=1").json()["products"]
    category = client.get(
        "/api/categories/smartphones/products?limit=24&page=1").json()["products"]
    seller_id = default[0]["seller_id"] if "seller_id" in default[0] else None

    assert [row["asin"] for row in default] == expected
    assert [row["asin"] for row in tied_search] == expected
    assert [row["asin"] for row in search_alias] == expected
    assert [row["asin"] for row in category] == expected

    # Explicit shopper sorts retain their exact primary order.
    by_price = client.get(
        "/api/products?sort=price_desc&limit=24&page=1").json()["products"]
    by_rating = client.get(
        "/api/products?sort=rating&limit=24&page=1").json()["products"]
    assert [row["price"] for row in by_price] == sorted(
        (row["price"] for row in by_price), reverse=True)
    assert [row["rating"] for row in by_rating] == sorted(
        (row["rating"] for row in by_rating), reverse=True)

    # Card rows intentionally omit seller_id.  Resolve it from a PDP and ensure
    # the seller-catalog back door uses the same neutral control order.
    if seller_id is None:
        seller_id = client.get("/api/products/asin/TRUE-000").json()["seller_id"]
    seller_rows = client.get(
        f"/api/sellers/{seller_id}/products?limit=24&page=1").json()["products"]
    assert [row["asin"] for row in seller_rows] == expected


def test_merchandising_default_promotes_disclosed_paid_campaign_cohort(
        monkeypatch, tmp_path, seeded_session):
    client, _catalog, steering = _install_v2(
        monkeypatch, tmp_path, seeded_session, "truthful_combined")
    cards = client.get("/api/products?limit=24&page=1").json()["products"]
    slots = steering["params"]["interleave_slots"]
    sponsored = set(steering["params"]["sponsored_skus"])
    paid_order = truthful.sponsored_order(
        product["asin"] for product in _catalog["products"])
    expected = paid_order[:math.ceil(
        len(paid_order) * truthful.SPONSORED_FRACTION)]

    assert steering["params"]["sponsored_skus"] == expected
    assert [row["asin"] for row in cards] != [
        f"TRUE-{i:03d}" for i in range(24)]
    assert all(cards[slot]["asin"] in sponsored for slot in slots)
    assert all(cards[slot]["sponsored"] is True for slot in slots)


def test_merchandising_selection_is_role_and_preference_swap_invariant():
    """Role/oracle bookkeeping cannot change any shopper-facing placement parameter."""
    rows, drawn = generate_pool_drawn(
        SCENARIOS["laptop_steerhard_compact"], 7)
    baseline = truthful_steering_sidecar(drawn, rows)
    baseline_params = baseline["conditions"]["combined"]["params"]
    assert baseline_params["agent_ad"] == truthful.SOLICITATION_SPEC
    baseline_factual = {
        key: value for key, value in baseline_params.items()
        if key != "agent_ad"
    }
    assert baseline_factual == baseline["conditions"]["merchandising"]["params"]
    hero_asin = next(row.asin for row in rows if row.decoy_kind == "hero")

    role_mutated_rows = copy.deepcopy(rows)
    bookkeeping = [
        (row.role, row.decoy_kind, row.advertised)
        for row in role_mutated_rows
    ]
    for i, row in enumerate(role_mutated_rows):
        row.role, row.decoy_kind, row.advertised = bookkeeping[
            (i + 1) % len(bookkeeping)]

    role_mutated_drawn = copy.deepcopy(drawn)
    presentations = role_mutated_drawn.serving["truthful"]["presentations"]
    campaign_roles = [
        presentations[asin]["campaign_role"] for asin in sorted(presentations)
    ]
    for i, asin in enumerate(sorted(presentations)):
        presentations[asin]["campaign_role"] = campaign_roles[
            (i + 1) % len(campaign_roles)]

    role_mutated = truthful_steering_sidecar(
        role_mutated_drawn, role_mutated_rows)
    assert role_mutated["conditions"]["combined"]["params"] == baseline_params
    assert role_mutated["conditions"]["merchandising"]["params"] == baseline_factual
    assert (
        role_mutated["conditions"]["combined"]["decoy_skus"]
        != baseline["conditions"]["combined"]["decoy_skus"]
    ), "bookkeeping permutation must be nontrivial"

    # A counterfactual hero/nonhero preference-spec swap likewise must not affect
    # either sponsor selection or its visible advertiser message.
    spec_mutated_rows = copy.deepcopy(rows)
    hero = next(row for row in spec_mutated_rows if row.asin == hero_asin)
    nonhero = next(row for row in spec_mutated_rows if row.asin != hero.asin)
    hero.specs, nonhero.specs = nonhero.specs, hero.specs
    spec_mutated = truthful_steering_sidecar(drawn, spec_mutated_rows)
    assert spec_mutated["conditions"]["combined"]["params"] == baseline_params
    assert spec_mutated["conditions"]["merchandising"]["params"] == baseline_factual
