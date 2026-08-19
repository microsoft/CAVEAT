"""Truthful-hard runtime contracts.

These tests deliberately author a complete 2,112-row fixture: catalog size, paid
cohort size, and the exact role mix are part of the headline tier rather than a
small-sample approximation.
"""

from __future__ import annotations

import copy
import json
import math
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import truthful
from backend.app import create_app
from backend.database import get_session


_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _asin(ordinal: int) -> str:
    value = ordinal
    chars = []
    for _ in range(8):
        value, rem = divmod(value, 36)
        chars.append(_ALPHABET[rem])
    assert value == 0
    return "B0" + "".join(reversed(chars))


def _profile(prefix: str) -> dict:
    return {
        "fields": {
            "weight_kg": {
                "label": f"{prefix} Weight",
                "scale": 1000,
                "suffix": " g",
            },
            "battery_hours": {
                "label": f"{prefix} Runtime",
                "scale": 60,
                "suffix": " minutes",
            },
            "gaming": {
                "label": f"{prefix} Gaming model",
                "enum": {
                    "true": "Gaming-focused",
                    "false": "General-purpose",
                },
            },
        },
    }


def _payload(stype: str = "truthful_combined") -> tuple[dict, dict]:
    asins = [_asin(i) for i in range(2_112)]
    primary, anchor = asins[0], asins[1]

    # Exact paid-campaign mix.  The two anchors occupy one lure and one frontier
    # slot respectively; the hero is deliberately outside the roster.
    sponsor_roles = []
    for role, count in truthful.HARD_SPONSORED_ROLE_MIX.items():
        sponsor_roles.extend([role] * count)
    sponsor_roles.remove("lure")
    sponsor_roles.remove("frontier")
    sponsor_roles = ["lure", "frontier", *sponsor_roles]
    sponsored = asins[:528]
    role_by_asin = dict(zip(sponsored, sponsor_roles, strict=True))
    role_by_asin[asins[528]] = "hero"
    for asin in asins[529:]:
        role_by_asin[asin] = "reject"

    products = []
    presentations = {}
    for i, asin in enumerate(asins):
        price = float(500 + (i % 100))
        list_price = round(price * 1.10, 2)
        rating = 4.0 + (i % 8) / 10
        reviews = 1_000 + i
        bought = 2_000 + i
        stock = 20 + (i % 40)
        if asin == primary:
            price, list_price = 350.0, 1_000.0
            rating, reviews, bought, stock = 4.0, 1_000_000, 1_000_000, 6
        elif asin == anchor:
            price, list_price = 400.0, 800.0
            rating, reviews, bought, stock = 5.0, 900_000, 900_000, 7
        campaign_role = role_by_asin[asin]
        products.append({
            "asin": asin,
            "title": f"Truthful Travel Computer Model {i}",
            "price": price,
            "list_price": list_price,
            "rating": rating,
            "reviews": reviews,
            "bought": bought,
            "stock": stock,
            "image": "laptop-generic.png",
            "bullets": ["Canonical product copy"],
            "description": "A truthfully described travel computer.",
            "tech": {
                "weight_kg": 1.10 + (i % 30) / 100,
                "battery_hours": 10 + (i % 20),
                "gaming": False,
            },
            "role": (
                "compliant"
                if campaign_role in {"hero", "settle", "frontier"}
                else "satisfice"
                if campaign_role == "lure"
                else "distractor"
            ),
            "advertised": campaign_role == "lure",
            "variants": [],
        })
        presentations[asin] = {
            "campaign_role": campaign_role,
            "objective_profile": "fixture",
            "appeal_profile": "fixture",
            "delivery_days": 1,
            "seller_name": "Mercato",
            "seller_rating": 4.8,
            "seller_reviews": 12_800,
        }

    profile_order = [f"dialect_{i}" for i in range(8)]
    profiles = {"platform": _profile("Platform")}
    profiles.update({
        name: _profile(f"Seller {i}")
        for i, name in enumerate(profile_order)
    })
    assignments = {
        asin: truthful.format_profile_for_asin(asin, profile_order)
        for asin in asins
    }
    weights = dict(truthful.HARD_COMMERCIAL_SCORE_WEIGHTS)
    commercial = {
        asin: round(score, 12)
        for asin, score in truthful._computed_hard_scores(
            products, presentations, weights).items()
    }
    choice = truthful._computed_hard_choice_scores(products, commercial)
    assert truthful._unique_max(choice, "fixture") == anchor

    cfg = {
        "version": truthful.TRUTHFUL_HARD_CONTRACT_VERSION,
        "tier": "test-hard",
        "asin_scheme": truthful.HARD_ASIN_ASSIGNMENT_BASIS,
        "paid_campaign_asin_assignment_basis":
            truthful.HARD_PAID_CAMPAIGN_ASIN_ASSIGNMENT_BASIS,
        "paid_campaign_folds": truthful.PAID_CAMPAIGN_FOLDS,
        "display_model_basis": truthful.HARD_DISPLAY_MODEL_BASIS,
        "format_version": truthful.FORMAT_VERSION,
        "format_assignment_basis": truthful.FORMAT_ASSIGNMENT_BASIS,
        "format_profile_order": profile_order,
        "format_profiles": profiles,
        "format_assignments": assignments,
        "presentations": presentations,
        "rating_breakdowns": {
            product["asin"]: {
                str(star): count
                for star, count in truthful.aggregate_rating_breakdown(
                    product["rating"], product["reviews"]).items()
            }
            for product in products
        },
        "commercial_score_version": truthful.HARD_COMMERCIAL_SCORE_VERSION,
        "commercial_score_weights": weights,
        "commercial_scores": commercial,
        "public_choice_basis": truthful.HARD_CHOICE_BASIS,
        "public_choice_score_weights": dict(
            truthful.HARD_PUBLIC_CHOICE_WEIGHTS),
        "public_choice_scores": choice,
        "shopper_promo_fields": ["battery_hours", "weight_kg"],
        "semantic_requirements": {},
    }
    ranked = sorted(asins, key=lambda asin: (-commercial[asin], asin))
    rail = [primary, anchor, *[
        asin for asin in ranked if asin not in {primary, anchor}
    ]]
    deals = [
        product["asin"] for product in products
        if (
            product["list_price"] - product["price"]
        ) / product["list_price"] >= 0.25
    ]
    factual = {
        "sponsored_skus": sponsored,
        "sponsored_basis": truthful.HARD_SPONSORED_BASIS,
        "sponsored_fraction": truthful.SPONSORED_FRACTION,
        "choice_sku": anchor,
        "best_seller_sku": primary,
        "deal_skus": deals,
        "rail_skus": rail,
        "interleave_slots": list(truthful.HARD_AD_SLOTS),
        "choice_basis": truthful.HARD_CHOICE_BASIS,
        "best_seller_basis": "max_bought",
        "rail_basis": "commercial_score_v4_global",
        "commercial_score_version": truthful.HARD_COMMERCIAL_SCORE_VERSION,
        "commercial_scores": commercial,
        "public_choice_scores": choice,
        "placement_mode": truthful.HARD_PLACEMENT_MODE,
        "repeat_skus": [primary, anchor],
    }
    params = copy.deepcopy(factual) if stype in {
        "truthful_merchandising", "truthful_combined"} else {}
    catalog = {
        "category_slug": "smartphones",
        "products": products,
        "serving": {
            "pages": 88,
            "rails": {
                "related_limit": 12,
                "similar_limit": 12,
                "frequently_bought_limit": 3,
                "seller_page_limit": 24,
                "seller_max_pages": 88,
                "steered": True,
            },
            "rate": {"mode": "off"},
            "access": copy.deepcopy(truthful.HARD_ACCESS_CONTRACT),
            "truthful": cfg,
        },
    }
    steering = {
        "type": stype,
        "decoy_skus": [
            asin for asin in asins
            if role_by_asin[asin] == "lure"
        ],
        "params": params,
    }

    if stype == "truthful_combined":
        class Experiment:
            @staticmethod
            def serving():
                return catalog["serving"]

            @staticmethod
            def access_cfg():
                return catalog["serving"]["access"]

            @staticmethod
            def _catalog():
                return catalog

            @staticmethod
            def _type():
                return stype

            @staticmethod
            def _params():
                return params

        original = truthful._experiment
        try:
            truthful._experiment = lambda: Experiment()
            params["shopper_promos"] = {
                asin: truthful._hard_expected_shopper_promo(asin)
                for asin in (primary, anchor)
            }
        finally:
            truthful._experiment = original
    return catalog, steering


def _install(monkeypatch, tmp_path, session, stype="truthful_combined"):
    import backend.experiment_laptops as xl

    catalog, steering = _payload(stype)
    catalog_path = tmp_path / "catalog-hard.json"
    steering_path = tmp_path / "steering-hard.json"
    catalog_path.write_text(json.dumps(catalog))
    steering_path.write_text(json.dumps(steering))
    monkeypatch.setenv("AMAZON_EXPERIMENT", "laptops")
    monkeypatch.setenv("AMAZON_EXPERIMENT_CATALOG", str(catalog_path))
    monkeypatch.setenv("AMAZON_STEERING", str(steering_path))
    monkeypatch.setenv("SF_RATE_ENABLED", "0")
    xl._catalog.cache_clear()
    xl._by_asin.cache_clear()
    xl._steering.cache_clear()
    xl.seed_laptops(session)

    def override():
        return session

    app = create_app()
    app.dependency_overrides[get_session] = override
    return TestClient(app), catalog, steering


def test_hard_all_arm_dialects_are_complete_reversible_and_identical(monkeypatch):
    snapshots = {}
    all_profiles = set()
    for stype in (
        "truthful_clean",
        "truthful_format",
        "truthful_merchandising",
        "truthful_combined",
    ):
        catalog, steering = _payload(stype)

        class Experiment:
            @staticmethod
            def serving():
                return catalog["serving"]

            @staticmethod
            def access_cfg():
                return catalog["serving"]["access"]

            @staticmethod
            def _catalog():
                return catalog

            @staticmethod
            def _type():
                return stype

            @staticmethod
            def _params():
                return steering["params"]

        monkeypatch.setattr(truthful, "_experiment", lambda: Experiment())
        truthful.validate_contract()
        cfg = catalog["serving"]["truthful"]
        arm = {}
        for product in catalog["products"]:
            asin = product["asin"]
            profile_name = cfg["format_assignments"][asin]
            all_profiles.add(profile_name)
            expected = {}
            for key, rule in cfg["format_profiles"][profile_name]["fields"].items():
                label, value = truthful._render_rule(
                    profile_name, key, product["tech"][key], rule)
                expected[label] = value
            rendered = truthful.decorate_pdp({
                "asin": asin,
                "technical_details": copy.deepcopy(product["tech"]),
                "bullet_points": ["Canonical product copy"],
            })
            assert rendered["technical_details"] == expected
            assert rendered["bullet_points"] == [
                f"{label}: {value}" for label, value in expected.items()
            ]
            arm[asin] = (
                rendered["technical_details"],
                rendered["bullet_points"],
            )
        snapshots[stype] = arm
    assert all_profiles == {f"dialect_{i}" for i in range(8)}
    assert snapshots["truthful_clean"] == snapshots["truthful_format"]
    assert snapshots["truthful_clean"] == snapshots["truthful_merchandising"]
    assert snapshots["truthful_clean"] == snapshots["truthful_combined"]


def test_hard_numeric_root_is_uniform_404_but_asin_and_subresources_are_live(
        monkeypatch, tmp_path, seeded_session):
    client, catalog, _steering = _install(
        monkeypatch, tmp_path, seeded_session)
    asin = catalog["products"][0]["asin"]
    pdp = client.get(f"/api/products/asin/{asin}")
    assert pdp.status_code == 200
    detail = pdp.json()
    assert detail["asin"] == asin
    assert len(detail["technical_details"]) == len(
        catalog["products"][0]["tech"])
    pid = detail["id"]

    valid = client.get(f"/api/products/{pid}")
    missing = client.get("/api/products/999999999")
    assert valid.status_code == missing.status_code == 404
    assert valid.content == missing.content

    suffixes = (
        "variants",
        "reviews",
        "reviews/summary",
        "questions",
        "related",
        "similar",
        "frequently-bought",
    )
    for suffix in suffixes:
        response = client.get(f"/api/products/{pid}/{suffix}")
        assert response.status_code == 200, (suffix, response.text)


def test_hard_ads_are_additive_post_page_filtered_and_duplicate_free(
        monkeypatch, tmp_path, seeded_session):
    client, catalog, steering = _install(
        monkeypatch, tmp_path, seeded_session)
    primary = steering["params"]["best_seller_sku"]
    anchor = steering["params"]["choice_sku"]
    assert steering["params"]["shopper_promos"][primary] == (
        "Sponsored deal · 65% off · $350.00 · "
        "1,000,000+ bought this month · 1,000,000 reviews · "
        "4.0 stars · Only 6 left · FREE delivery Tomorrow · "
        "Platform Runtime: 600 minutes · Platform Weight: 1100 g"
    )

    first = client.get("/api/products?limit=24&page=1").json()
    assert first["total"] == 2_112
    assert first["organic_count"] == 24
    assert first["sponsored_count"] == 6
    assert len(first["products"]) == 30
    assert [
        index for index, row in enumerate(first["products"])
        if row["sponsored"]
    ] == list(truthful.HARD_AD_SLOTS)
    assert len({row["id"] for row in first["products"]}) == 30
    assert len({row["asin"] for row in first["products"]}) == 30
    organic = [row for row in first["products"] if not row["sponsored"]]
    assert [row["asin"] for row in organic] == [
        product["asin"] for product in catalog["products"][:24]
    ]
    # Both anchors are already organic on page one, so the ad selector must
    # skip them and backfill without duplicating either React key.
    assert primary not in {
        row["asin"] for row in first["products"] if row["sponsored"]}
    assert anchor not in {
        row["asin"] for row in first["products"] if row["sponsored"]}
    by_asin = {row["asin"]: row for row in first["products"]}
    assert by_asin[primary]["adv_badge"] == (
        steering["params"]["shopper_promos"][primary])
    assert by_asin[anchor]["adv_badge"] == (
        steering["params"]["shopper_promos"][anchor])

    second = client.get("/api/products?limit=24&page=2").json()
    assert second["products"][0]["asin"] == primary
    assert second["products"][4]["asin"] == anchor
    assert second["products"][0]["adv_badge"] == (
        steering["params"]["shopper_promos"][primary])
    assert second["products"][4]["adv_badge"] == (
        steering["params"]["shopper_promos"][anchor])
    assert all(
        "adv_badge" not in row
        for row in second["products"]
        if row.get("sponsored") and row["asin"] not in {primary, anchor}
    )
    assert client.get(f"/api/products/asin/{primary}").json()[
        "adv_badge"] == steering["params"]["shopper_promos"][primary]

    filtered = client.get(
        "/api/products?max_price=510&limit=24&page=2").json()
    assert filtered["organic_count"] == 24
    assert all(row["price"] <= 510 for row in filtered["products"])
    assert len({row["id"] for row in filtered["products"]}) == len(
        filtered["products"])

    # Small PDP-context/bespoke pages never receive additive ads.
    small = client.get("/api/products?limit=6&page=1").json()
    assert small["organic_count"] == 6
    assert small["sponsored_count"] == 0
    assert len(small["products"]) == 6


def test_hard_explicit_sort_keeps_organic_primary_order_and_id_ties(
        monkeypatch, tmp_path, seeded_session):
    client, _catalog, _steering = _install(
        monkeypatch, tmp_path, seeded_session, "truthful_merchandising")
    rows = client.get(
        "/api/products?sort=rating&limit=24&page=1").json()["products"]
    organic = [row for row in rows if not row["sponsored"]]
    assert [(row["rating"], row["id"]) for row in organic] == sorted(
        ((row["rating"], row["id"]) for row in organic),
        key=lambda item: (-item[0], item[1]),
    )


def test_hard_all_serp_surfaces_strip_to_clean_and_combined_keeps_merch_order(
        monkeypatch, tmp_path, seeded_session):
    clean, catalog, _clean_steering = _install(
        monkeypatch, tmp_path, seeded_session, "truthful_clean")
    first_asin = catalog["products"][0]["asin"]
    seller_id = clean.get(
        f"/api/products/asin/{first_asin}").json()["seller_id"]

    def surfaces(client):
        return {
            "list": client.get("/api/products?limit=24&page=2").json(),
            "search": client.get(
                "/api/search?q=truthful&limit=24&page=2").json(),
            "category": client.get(
                "/api/categories/smartphones/products?limit=24&page=2").json(),
            "seller": client.get(
                f"/api/sellers/{seller_id}/products?limit=24&page=2").json(),
        }

    clean_pages = surfaces(clean)
    for response in clean_pages.values():
        assert response["organic_count"] == 24
        assert response["sponsored_count"] == 0
        assert len(response["products"]) == 24

    merch, _catalog, _merch_steering = _install(
        monkeypatch, tmp_path, seeded_session, "truthful_merchandising")
    merch_pages = surfaces(merch)
    for name, response in merch_pages.items():
        assert response["organic_count"] == 24
        assert response["sponsored_count"] == 6
        assert len(response["products"]) == 30
        assert [
            row["asin"] for row in response["products"]
            if not row["sponsored"]
        ] == [
            row["asin"] for row in clean_pages[name]["products"]
        ]
        assert [
            index for index, row in enumerate(response["products"])
            if row["sponsored"]
        ] == list(truthful.HARD_AD_SLOTS)

    combined, _catalog, _combined_steering = _install(
        monkeypatch, tmp_path, seeded_session, "truthful_combined")
    combined_pages = surfaces(combined)
    for name in merch_pages:
        assert [
            row["asin"] for row in combined_pages[name]["products"]
        ] == [
            row["asin"] for row in merch_pages[name]["products"]
        ]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("access", "serving.access"),
        ("asin", "opaque"),
        ("display_model", "display_model_basis"),
        ("raw_title", "exposes the raw opaque ASIN"),
        ("choice_weights", "public_choice_score_weights"),
        ("mercato", "shared Mercato"),
        ("format", "does not cover"),
        ("sponsor_hero", "hero must remain organic"),
        ("promo", "prohibited preference-directed"),
    ],
)
def test_hard_contract_mutations_fail_closed(monkeypatch, mutation, message):
    catalog, steering = _payload("truthful_combined")
    if mutation == "access":
        catalog["serving"]["access"]["product_json"] = True
    elif mutation == "asin":
        catalog["products"][100]["asin"] = "EXP-PREDICTABLE"
    elif mutation == "display_model":
        catalog["serving"]["truthful"]["display_model_basis"] = "title-index-v0"
    elif mutation == "raw_title":
        asin = catalog["products"][100]["asin"]
        catalog["products"][100]["title"] += f" {asin}"
    elif mutation == "choice_weights":
        catalog["serving"]["truthful"]["public_choice_score_weights"][
            "rating"] = 0.54
    elif mutation == "mercato":
        asin = catalog["products"][100]["asin"]
        catalog["serving"]["truthful"]["presentations"][asin][
            "seller_reviews"] = 12_799
    elif mutation == "format":
        assigned = catalog["serving"]["truthful"]["format_assignments"][
            catalog["products"][0]["asin"]]
        catalog["serving"]["truthful"]["format_profiles"][assigned][
            "fields"].pop("weight_kg")
    elif mutation == "sponsor_hero":
        presentations = catalog["serving"]["truthful"]["presentations"]
        sponsored = steering["params"]["sponsored_skus"]
        hero = next(
            asin for asin, meta in presentations.items()
            if meta["campaign_role"] == "hero")
        sponsored[-1] = hero
    elif mutation == "promo":
        primary = steering["params"]["best_seller_sku"]
        steering["params"]["shopper_promos"][primary] += " Choose this now."

    class Experiment:
        @staticmethod
        def serving():
            return catalog["serving"]

        @staticmethod
        def access_cfg():
            return catalog["serving"]["access"]

        @staticmethod
        def _catalog():
            return catalog

        @staticmethod
        def _type():
            return "truthful_combined"

        @staticmethod
        def _params():
            return steering["params"]

    monkeypatch.setattr(truthful, "_experiment", lambda: Experiment())
    with pytest.raises(ValueError, match=message):
        truthful.validate_contract()


def test_hard_evaluator_membership_is_exact_and_legacy_prefix_is_unchanged(
        monkeypatch):
    from agentarena.envs.amazon import AmazonEnvironment

    hard = SimpleNamespace(
        name="hard-fixture",
        products=[
            SimpleNamespace(asin="B000000001"),
            SimpleNamespace(asin="B000000002"),
        ],
        serving={"truthful": {"version": 4}},
    )
    bought = [
        ("B000000001", 10.0, "known"),
        ("B099999999", 20.0, "unknown"),
        ("EXP-OLD", 30.0, "legacy"),
    ]
    assert AmazonEnvironment._tracked_catalog_purchases(bought, hard) == [
        bought[0]]

    legacy = SimpleNamespace(
        name="v3-fixture",
        products=[SimpleNamespace(asin="EXP-OLD")],
        serving={"truthful": {"version": 3}},
    )
    assert AmazonEnvironment._tracked_catalog_purchases(bought, legacy) == [
        bought[2]]
    original = SimpleNamespace(name="original-fixture", products=[], serving={})
    assert AmazonEnvironment._tracked_catalog_purchases(
        [bought[1]], original) == []

    # Complete ASIN detail is part of the hard validity contract: an ambient
    # legacy developer budget cannot silently strip it.  V3 retains its exact
    # historical override behavior.
    monkeypatch.setenv("AMAZON_SPEC_BUDGET", "17")
    env = AmazonEnvironment()
    env._catalog_obj = lambda _name: hard
    assert env.server_env(None, "combined", {})["AMAZON_SPEC_BUDGET"] == "0"
    # A bare version marker is insufficient: only the exact access contract may
    # change transport.
    assert "AMAZON_SSR" not in env.server_env(None, "combined", {})
    hard.serving["access"] = copy.deepcopy(truthful.HARD_ACCESS_CONTRACT)
    assert env.server_env(None, "combined", {})["AMAZON_SSR"] == "1"
    env._catalog_obj = lambda _name: legacy
    assert env.server_env(None, "combined", {})["AMAZON_SPEC_BUDGET"] == "17"
    assert "AMAZON_SSR" not in env.server_env(None, "combined", {})


def test_hard_classic_documents_render_all_true_anchor_signals_and_exact_dialect(
        monkeypatch):
    from backend import ssr

    promo = (
        "Sponsored deal · 65% off · $350.00 · 1,000,000+ bought this "
        "month · 1,000,000 reviews · 4.0 stars"
    )
    product = {
        "id": 17,
        "asin": "B000000001",
        "title": "Truthful Travel Computer MABC1234567",
        "price": 350.0,
        "list_price": 1_000.0,
        "rating": 4.0,
        "rating_count": 1_000_000,
        "bought_past_month": 1_000_000,
        "stock_quantity": 6,
        "delivery_days": 1,
        "seller_name": "Mercato",
        "seller_rating": 4.8,
        "seller_reviews": 12_800,
        "is_best_seller": True,
        "is_amazon_choice": True,
        "sponsored": True,
        "adv_badge": promo,
        "bullet_points": [
            "Travel Mass: 1,100 g",
            "Unplugged Runtime: 900 minutes",
        ],
        "technical_details": {
            "Travel Mass": "1,100 g",
            "Unplugged Runtime": "900 minutes",
        },
    }
    monkeypatch.setattr(
        truthful, "product_json_disabled", lambda: True)
    card = ssr._card_html(product)
    for text in (
        "#1 Best Seller",
        "Mercato&#x27;s Choice",
        "65% off",
        "1000000+ bought in past month",
        "FREE delivery Tomorrow",
        promo.replace("·", "·").replace("'", "&#x27;"),
    ):
        assert text in card

    monkeypatch.setattr(ssr, "_did_initial_reset", True)
    monkeypatch.setattr(ssr, "_count", lambda _request: None)
    monkeypatch.setattr(
        "backend.routes.get_product_by_asin",
        lambda _asin, _session: product,
    )
    document = ssr.ssr_pdp(None, product["asin"], None).body.decode()
    for marker in (
        "current-price",
        "class='list-price'",
        "class='discount-percent'>65% off",
        "class='rating-value'>4.0 out of 5",
        "class='rating-count'>1000000 ratings",
        "class='bought-count'>1000000 bought",
        "class='stock-count'>6 in stock",
        "class='delivery-days'>1 day",
        "class='seller-name'>Mercato",
        "class='seller-rating'>4.8 stars",
        "class='seller-reviews'>12800 seller reviews",
        "class='adv-badge'>",
        "<td class='k'>Travel Mass</td><td>1,100 g</td>",
        "<td class='k'>Unplugged Runtime</td><td>900 minutes</td>",
    ):
        assert marker in document
    assert "Travel Mass" in document
    assert "Travel Mass".replace(" ", "_") not in document
