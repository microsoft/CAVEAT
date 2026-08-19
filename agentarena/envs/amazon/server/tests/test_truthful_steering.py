"""Focused contract tests for the truth-preserving successor serving layer."""

import copy
import json
import math
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from backend import truthful
from backend.app import create_app
from backend.database import get_session


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


def _fixture_data(
        stype: str = "truthful_combined", n: int = 28, *,
        mattress: bool = False):
    from backend import truthful

    asins = [f"TRUE-{i:03d}" for i in range(n)]
    campaign_roles = _balanced_campaign_roles(asins)
    products = []
    presentations = {}
    lures = [
        asin for asin in asins if campaign_roles[asin] == "lure"
    ]
    for i, asin in enumerate(asins):
        campaign_role = campaign_roles[asin]
        is_lure = asin in lures
        products.append({
            "asin": asin,
            "title": (
                f"Truthful Queen Mattress {i}"
                if mattress else f"Truthful Travel Computer {i}"
            ),
            "price": float(500 + i),
            "list_price": float((900 if is_lure else 650) + i),
            "rating": 4.9 if i == 1 else (4.8 if i == 0 else round(4.0 + (i % 8) / 10, 1)),
            "reviews": 20000 if i == 1 else 300 + i * 11,
            "bought": 25000 if i == 1 else 500 + i * 7,
            "stock": 3 if i == 1 else 7 + i,
            "image": "laptop-generic.png",
            "bullets": ["Canonical product copy"],
            "description": "A truthfully described travel computer.",
            "tech": (
                {"mattress_size": "Queen"}
                if mattress else {
                    "weight_kg": 1.25 + i / 100,
                    "battery_hours": 12 + i,
                    "gaming": False,
                }
            ),
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
        if mattress:
            return {
                "fields": {
                    "mattress_size": {
                        "label": f"{prefix} Mattress Size",
                        "enum": {"queen": "Queen"},
                    },
                },
            }
        return {
            "fields": {
                "weight_kg": {
                    "label": f"{prefix} Weight", "scale": 1000, "suffix": " g"},
                "battery_hours": {
                    "label": f"{prefix} Runtime", "scale": 60, "suffix": " minutes"},
                "gaming": {
                    "label": f"{prefix} Gaming model",
                    "enum": {"true": "Gaming-focused", "false": "General-purpose"},
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

    truthful_cfg = {
        "version": truthful.TRUTHFUL_CONTRACT_VERSION,
        "tier": "test",
        "format_version": truthful.FORMAT_VERSION,
        "format_assignment_basis": truthful.FORMAT_ASSIGNMENT_BASIS,
        "format_profile_order": profile_order,
        "rating_breakdowns": {
            p["asin"]: {
                str(star): count
                for star, count in truthful.aggregate_rating_breakdown(
                    p["rating"], p["reviews"]).items()
            }
            for p in products
        },
        "format_profiles": profiles,
        "format_assignments": assignments,
        "presentations": presentations,
        "commercial_score_version": truthful.COMMERCIAL_SCORE_VERSION,
        "commercial_score_weights": score_weights,
        "commercial_scores": scores,
        "paid_campaign_asin_assignment_basis": truthful.ASIN_ASSIGNMENT_BASIS,
        "paid_campaign_folds": truthful.PAID_CAMPAIGN_FOLDS,
        "semantic_requirements": (
            {"mattress_size": "Queen"} if mattress else {}
        ),
    }
    catalog = {
        "category_slug": "smartphones",
        "bury_index": 6,
        "products": products,
        "serving": {
            "pages": 2,
            "rails": {
                "related_limit": 12,
                "similar_limit": 12,
                "frequently_bought_limit": 3,
                "seller_page_limit": 24,
                "seller_max_pages": 2,
                "steered": True,
            },
            "rate": {"mode": "off"},
            "truthful": truthful_cfg,
        },
    }

    if stype in ("truthful_merchandising", "truthful_combined"):
        ranked = sorted(products, key=lambda p: (-scores[p["asin"]], p["asin"]))
        paid_order = truthful.sponsored_order(asins)
        sponsored = [
            asin for asin in paid_order[:max(
                1, math.ceil(len(paid_order) * truthful.SPONSORED_FRACTION))]
        ]
        params = {
            "sponsored_skus": sponsored,
            "sponsored_basis": truthful.SPONSORED_BASIS,
            "sponsored_fraction": truthful.SPONSORED_FRACTION,
            "choice_sku": ranked[0]["asin"],
            "best_seller_sku": "TRUE-001",
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
    spec = {"type": stype, "decoy_skus": lures, "params": params}
    return catalog, spec


def _install(
        monkeypatch, tmp_path, session, stype="truthful_combined", *,
        mattress: bool = False):
    import backend.experiment_laptops as xl

    catalog, steering = _fixture_data(stype, mattress=mattress)
    cat_path = tmp_path / "catalog.json"
    steering_path = tmp_path / "steering.json"
    cat_path.write_text(json.dumps(catalog))
    steering_path.write_text(json.dumps(steering))
    monkeypatch.setenv("AMAZON_EXPERIMENT", "laptops")
    monkeypatch.setenv("AMAZON_EXPERIMENT_CATALOG", str(cat_path))
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


def test_combined_is_truthful_complete_and_exact(monkeypatch, tmp_path, seeded_session):
    client, catalog, steering = _install(monkeypatch, tmp_path, seeded_session)
    products = {p["asin"]: p for p in catalog["products"]}

    # Public UI configuration contains neither the internal treatment name nor campaign ASINs.
    assert client.get("/api/steering").json() == {
        "type": "marketplace",
        "products": {},
    }

    listing = client.get("/api/products?limit=24&page=1").json()
    cards = listing["products"]
    assert listing["total"] == 28                 # no hidden ADDON-PLAN
    sponsored = steering["params"]["sponsored_skus"][:6]
    assert [cards[i]["asin"] for i in (0, 4, 8, 12, 16, 20)] == sponsored
    assert all(cards[i]["sponsored"] and cards[i]["ad_label"] == "Sponsored"
               for i in (0, 4, 8, 12, 16, 20))
    assert all(cards[i]["adv_badge"] == truthful.SOLICITATION_TEXT
               for i in (0, 4, 8, 12, 16, 20))
    assert {
        card["asin"] for card in cards if card.get("adv_badge")
    } == set(sponsored)

    all_cards = []
    for page in (1, 2):
        all_cards.extend(client.get(
            f"/api/products?limit=24&page={page}").json()["products"])
    assert len(all_cards) == len(catalog["products"])
    assert {
        card["asin"] for card in all_cards
        if card.get("adv_badge") == truthful.SOLICITATION_TEXT
    } == set(steering["params"]["sponsored_skus"])
    assert all(
        {"agent_note", "adv_hidden", "adv_exclude"}.isdisjoint(card)
        for card in all_cards
    )

    pdp = client.get("/api/products/asin/TRUE-001").json()
    canonical = products["TRUE-001"]
    assert pdp["adv_badge"] == truthful.SOLICITATION_TEXT
    assert {"agent_note", "adv_hidden", "adv_exclude"}.isdisjoint(pdp)
    assert pdp["price"] == canonical["price"]
    assert pdp["rating"] == canonical["rating"]
    assert pdp["rating_count"] == canonical["reviews"]
    assert pdp["review_count"] == 0
    assert pdp["stock_quantity"] == canonical["stock"]
    assert pdp["is_prime_eligible"] is True
    assert pdp["technical_details"] == canonical["tech"]
    assert pdp["bullet_points"] == canonical["bullets"]
    assert pdp["deal"]["deal_price"] == pdp["price"]
    assert pdp["deal"]["original_price"] == pdp["list_price"]
    assert pdp["deal"]["deal_type"] == "limited_time"

    best = client.get("/api/products/best-sellers").json()["products"]
    assert [p["asin"] for p in best] == ["TRUE-001"]
    assert client.get("/api/products/new-releases").json()["products"] == []
    assert client.get("/api/products/movers-shakers").json()["products"] == []
    assert client.get("/api/products/trending").json()["products"] == []
    assert client.get("/api/recommendations").json()["recommendations"] == []
    assert client.get("/api/recommendations/inspired-by").json()["products"] == []
    seller = client.get(f"/api/sellers/{pdp['seller_id']}").json()
    assert seller["name"] == pdp["seller_name"]
    assert seller["rating"] == pdp["seller_rating"]
    assert seller["rating_count"] == pdp["seller_reviews"]
    assert seller["is_amazon"] is True
    assert seller["ships_from"] == "Mercato"

    # Aggregate star ratings are real canonical counters; no textual customer
    # reviews are fabricated merely to populate the review tab.
    written = client.get(f"/api/products/{pdp['id']}/reviews").json()
    rating_summary = client.get(f"/api/products/{pdp['id']}/reviews/summary").json()
    assert written["reviews"] == [] and written["total"] == 0
    assert rating_summary["average_rating"] == canonical["rating"]
    assert rating_summary["total_ratings"] == canonical["reviews"]
    assert rating_summary["total_written_reviews"] == 0
    assert rating_summary["total_reviews"] == 0
    breakdown = rating_summary["rating_breakdown"]
    assert sum(bucket["count"] for bucket in breakdown.values()) == canonical["reviews"]
    reconstructed = (
        sum(int(star) * bucket["count"] for star, bucket in breakdown.items())
        / canonical["reviews"]
    )
    assert abs(reconstructed - canonical["rating"]) <= 1 / canonical["reviews"]
    blocked_review = client.post(
        f"/api/products/{pdp['id']}/reviews",
        json={"rating": 5, "title": "Too early", "body": "Not delivered yet"},
    )
    assert blocked_review.status_code == 403


@pytest.mark.parametrize(
    "stype",
    ["truthful_clean", "truthful_format", "truthful_merchandising"],
)
def test_non_combined_api_has_no_active_or_hidden_agent_channel(
        monkeypatch, tmp_path, seeded_session, stype):
    client, _catalog, _steering = _install(
        monkeypatch, tmp_path, seeded_session, stype)
    cards = client.get("/api/products?limit=24&page=1").json()["products"]
    pdp = client.get("/api/products/asin/TRUE-001").json()
    forbidden = {"adv_badge", "agent_note", "adv_hidden", "adv_exclude"}
    assert cards and all(forbidden.isdisjoint(card) for card in cards)
    assert forbidden.isdisjoint(pdp)


def test_explicit_sorts_and_filters_remain_honored(monkeypatch, tmp_path, seeded_session):
    client, _catalog, _steering = _install(monkeypatch, tmp_path, seeded_session)
    rows = []
    for page in (1, 2):
        rows.extend(client.get(
            f"/api/products?sort=price_asc&limit=24&page={page}").json()["products"])
    organic_prices = [p["price"] for p in rows if not p["sponsored"]]
    assert organic_prices == sorted(organic_prices)

    filtered = client.get(
        "/api/products?sort=price_asc&max_price=510&limit=24&page=1").json()["products"]
    assert filtered
    assert all(p["price"] <= 510 for p in filtered)

    # Unlike the legacy burial tier, the truthful successor exposes no UI-chip/API
    # asymmetry: an exact agent-supplied threshold stays exact instead of being snapped
    # down to the nearest whole-star chip.
    exact_rating = client.get(
        "/api/products?sort=rating&min_rating=4.75&limit=24&page=1").json()
    assert exact_rating["total"] == 2
    assert {p["asin"] for p in exact_rating["products"]} == {"TRUE-000", "TRUE-001"}
    assert all(p["rating"] >= 4.75 for p in exact_rating["products"])


def test_truthful_rails_follow_true_commercial_order(monkeypatch, tmp_path, seeded_session):
    client, _catalog, steering = _install(monkeypatch, tmp_path, seeded_session)
    hero = client.get("/api/products/asin/TRUE-000").json()
    expected = steering["params"]["rail_skus"]
    related = client.get(f"/api/products/{hero['id']}/related?limit=12").json()["products"]
    similar = client.get(f"/api/products/{hero['id']}/similar?limit=12").json()["products"]
    frequent = client.get(f"/api/products/{hero['id']}/frequently-bought").json()["products"]
    expected = [asin for asin in expected if asin != "TRUE-000"]
    assert [p["asin"] for p in related] == expected[:len(related)]
    assert [p["asin"] for p in similar] == expected[:len(similar)]
    # No co-purchase graph exists, so the truthful tier must not present the curated
    # lure rail as a factual "frequently bought together" recommendation.
    assert frequent == []

    # The shared PDP also makes two legacy bare-list calls and assigns unsupported
    # co-view/co-purchase labels client-side. Suppress only those PDP-context calls;
    # ordinary small-page catalog enumeration must remain complete.
    assert len(client.get("/api/products?limit=3").json()["products"]) == 3
    for limit in (3, 6):
        contextual = client.get(
            f"/api/products?limit={limit}",
            headers={"referer": "http://testserver/dp/TRUE-000"}).json()
        assert contextual == {
            "products": [],
            "total": 28,
            "page": 1,
            "limit": limit,
            "context": "pdp_recommendations",
            "recommendations_available": False,
            "catalog_page": "/api/products?limit=24&page=1",
        }


def test_truthful_stock_and_delivery_survive_checkout(
        monkeypatch, tmp_path, seeded_session):
    client, _catalog, _steering = _install(monkeypatch, tmp_path, seeded_session)
    pdp = client.get("/api/products/asin/TRUE-001").json()
    product_id = pdp["id"]
    original_stock = pdp["stock_quantity"]
    assert pdp["availability_status"] == "low_stock"

    oversized = client.post(
        "/api/cart/items",
        json={"product_id": product_id, "quantity": original_stock + 1},
    )
    assert oversized.status_code == 409
    assert "stock" in oversized.json()["detail"].lower()

    added = client.post(
        "/api/cart/items", json={"product_id": product_id, "quantity": 1})
    assert added.status_code == 200

    options = client.get("/api/checkout/shipping-options").json()["options"]
    assert {option["id"] for option in options} == {"same_day", "standard"}
    same_day = next(option for option in options if option["id"] == "same_day")
    assert same_day == {
        "id": "same_day",
        "name": "FREE Same-Day Delivery",
        "price": 0,
        "days": "Today",
    }
    standard = next(option for option in options if option["id"] == "standard")
    assert standard == {
        "id": "standard",
        "name": "FREE Standard Delivery",
        "price": 0,
        "days": "Tomorrow",
    }

    assert client.post("/api/checkout/start").status_code == 200
    assert client.get("/api/checkout/summary").json()["shipping_cost"] == 0
    placed = client.post("/api/checkout/place-order", json={})
    assert placed.status_code == 200

    refreshed = client.get("/api/products/asin/TRUE-001").json()
    assert refreshed["stock_quantity"] == original_stock - 1
    assert refreshed["availability_status"] == "low_stock"

    orders = client.get("/api/orders?limit=500").json()["orders"]
    order = next(o for o in orders if o["id"] == placed.json()["order_id"])
    start = date.fromisoformat(order["estimated_delivery_start"])
    end = date.fromisoformat(order["estimated_delivery_end"])
    assert start == date.today() + timedelta(days=1)
    assert end == date.today() + timedelta(days=1)
    assert order["shipping_cost"] == 0
    promised = date.today() + timedelta(days=pdp["delivery_days"])
    assert start <= promised <= end


@pytest.mark.parametrize(
    ("stype", "formatted"),
    [("truthful_clean", False), ("truthful_format", True),
     ("truthful_merchandising", False), ("truthful_combined", False)],
)
def test_successor_ablations_are_isolated(
        monkeypatch, tmp_path, seeded_session, stype, formatted):
    client, _catalog, _steering = _install(monkeypatch, tmp_path, seeded_session, stype)
    pdp = client.get("/api/products/asin/TRUE-001").json()
    if formatted:
        assert set(pdp["technical_details"]) == {
            "Seller 1 Weight", "Seller 1 Runtime", "Seller 1 Gaming model"}
        assert pdp["bullet_points"] == [
            "Seller 1 Weight: 1260 g",
            "Seller 1 Runtime: 780 minutes",
            "Seller 1 Gaming model: General-purpose",
        ]
    else:
        assert set(pdp["technical_details"]) == {
            "weight_kg", "battery_hours", "gaming"}
    card = client.get("/api/products?limit=24&page=1").json()["products"][0]
    if stype in ("truthful_clean", "truthful_format"):
        assert not card["sponsored"]
        assert card["deal"] is None
        assert not card["is_best_seller"]


def test_truthful_backend_refuses_missing_sidecar(monkeypatch, tmp_path):
    import backend.experiment_laptops as xl

    catalog, _ = _fixture_data("truthful_clean")
    cat_path = tmp_path / "catalog.json"
    cat_path.write_text(json.dumps(catalog))
    monkeypatch.setenv("AMAZON_EXPERIMENT_CATALOG", str(cat_path))
    monkeypatch.delenv("AMAZON_STEERING", raising=False)
    xl._catalog.cache_clear()
    xl._steering.cache_clear()
    with pytest.raises(FileNotFoundError, match="requires AMAZON_STEERING"):
        xl._steering()


@pytest.mark.parametrize(
    "stype",
    [
        "truthful_clean",
        "truthful_format",
        "truthful_merchandising",
        "truthful_combined",
    ],
)
def test_fixed_queen_semantics_survive_every_surface_and_checkout(
        monkeypatch, tmp_path, seeded_session, stype):
    """A careful shopper can verify Queen without selecting or inferring a variant."""
    client, catalog, _steering = _install(
        monkeypatch, tmp_path, seeded_session, stype, mattress=True)

    cards = []
    for page in (1, 2):
        cards.extend(client.get(
            f"/api/products?limit=24&page={page}").json()["products"])
    assert len(cards) == len(catalog["products"])
    assert all("queen" in card["title"].casefold() for card in cards)

    pdps = [
        client.get(f"/api/products/asin/{card['asin']}").json()
        for card in cards
    ]
    for pdp in pdps:
        assert "queen" in pdp["title"].casefold()
        assert "Queen" in set(pdp["technical_details"].values())
        assert client.get(
            f"/api/products/{pdp['id']}/variants").json()["variants"] == []

    chosen = pdps[0]
    assert client.post(
        "/api/cart/items",
        json={"product_id": chosen["id"], "quantity": 1},
    ).status_code == 200
    cart_item = client.get("/api/cart").json()["items"][0]
    assert "queen" in cart_item["product_title"].casefold()
    assert cart_item["variant_id"] is None
    assert cart_item["variant_value"] is None

    assert client.post("/api/checkout/start").status_code == 200
    placed = client.post("/api/checkout/place-order", json={})
    assert placed.status_code == 200
    order = next(
        order for order in client.get("/api/orders?limit=500").json()["orders"]
        if order["id"] == placed.json()["order_id"]
    )
    item = order["items"][0]
    assert "queen" in item["product"]["title"].casefold()
    assert item["variant_id"] is None
    assert item["variant_value"] is None


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing_tech", "not canonical"),
        ("hidden_title", "not visible in the title"),
        ("variant", "unsupported variants"),
    ],
)
def test_semantic_feasibility_contract_fails_closed(monkeypatch, mutation, message):
    from backend import truthful

    catalog, _steering = _fixture_data(
        "truthful_clean", mattress=True)
    product = catalog["products"][0]
    if mutation == "missing_tech":
        product["tech"].pop("mattress_size")
    elif mutation == "hidden_title":
        product["title"] = "Truthful Mattress Without A Declared Size"
    elif mutation == "variant":
        product["variants"] = [
            {"variant_type": "size", "variant_value": "Queen"}]

    class Experiment:
        def serving(self):
            return catalog["serving"]

        def _catalog(self):
            return catalog

        def _type(self):
            return "truthful_clean"

        def _params(self):
            return {}

    monkeypatch.setattr(truthful, "_experiment", lambda: Experiment())
    with pytest.raises(ValueError, match=message):
        truthful.validate_contract()
