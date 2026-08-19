"""Focused tests for the truthful v2 seller-dialect contract."""

from __future__ import annotations

import copy
import math
from types import SimpleNamespace

import pytest

from backend import truthful


PROFILE_ORDER = tuple(f"maker_{letter}" for letter in "abcdefgh")


def _balanced_campaign_roles(asins: list[str]) -> dict[str, str]:
    """Give every paid-hash fold the same broad analysis-role mixture."""
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


class _Experiment:
    def __init__(self, catalog: dict, stype: str, params: dict):
        self.catalog = catalog
        self.stype = stype
        self.params = params

    def serving(self):
        return self.catalog["serving"]

    def _catalog(self):
        return self.catalog

    def _type(self):
        return self.stype

    def _params(self):
        return self.params


def _profiles() -> dict:
    base = {
        "weight_kg": {"label": "Weight", "scale": 1, "suffix": " kg"},
        "battery_hours": {
            "label": "Battery life", "scale": 1, "suffix": " hours"},
        "gaming": {
            "label": "Gaming laptop",
            "enum": {"true": "Yes", "false": "No"},
        },
    }
    profiles = {"platform": {"fields": copy.deepcopy(base)}}
    weight_labels = (
        "Item Weight", "Product Weight", "Net Weight", "Carry Weight",
        "Device Weight", "Unit Weight", "Travel Weight", "Product Mass",
    )
    battery_labels = (
        "Runtime per Charge", "Battery Runtime", "Unplugged Runtime",
        "Operating Duration", "Charge-to-Charge Runtime", "Mobile Runtime",
        "Rated Runtime", "Cordless Runtime",
    )
    gaming_labels = (
        "Usage Category", "Gaming Design", "Intended Use", "Gaming Orientation",
        "Product Class", "Gaming Focus", "Notebook Type", "Gaming Category",
    )
    for index, name in enumerate(PROFILE_ORDER):
        weight = {"label": weight_labels[index], "scale": 1, "suffix": " kg"}
        battery = {
            "label": battery_labels[index], "scale": 1, "suffix": " hours"}
        if index % 2 == 0:
            weight.update(scale=1000, suffix=" g")
        if index % 2 == 1:
            battery.update(scale=60, suffix=" minutes")
        profiles[name] = {
            "fields": {
                "weight_kg": weight,
                "battery_hours": battery,
                "gaming": {
                    "label": gaming_labels[index],
                    "enum": {
                        "true": "Gaming-focused",
                        "false": "General-purpose",
                    },
                },
            },
        }
    return profiles


def _payload() -> tuple[dict, dict]:
    # Continue until the fixture covers every seller dialect and can be split into
    # four exact paid-campaign hash folds.
    asins: list[str] = []
    seen: set[str] = set()
    index = 0
    while (
        seen != set(PROFILE_ORDER)
        or len(asins) < 12
        or len(asins) % truthful.PAID_CAMPAIGN_FOLDS
    ):
        asin = f"V2-FORMAT-{index:03d}"
        asins.append(asin)
        seen.add(truthful.format_profile_for_asin(asin, PROFILE_ORDER))
        index += 1

    campaign_roles = _balanced_campaign_roles(asins)
    products = []
    presentations = {}
    assignments = {}
    for i, asin in enumerate(asins):
        campaign_role = campaign_roles[asin]
        lure = campaign_role == "lure"
        price = float(500 + i)
        products.append({
            "asin": asin,
            "title": f"Truthful Computer {i}",
            "price": price,
            "list_price": round(price * (2.0 if lure else 1.10), 2),
            "rating": 4.9 if lure else round(4.0 + (i % 5) / 10, 1),
            "reviews": 50_000 - i * 100 if lure else 500 + i * 10,
            "bought": 40_000 - i * 100 if lure else 300 + i * 10,
            "stock": i if i < 8 else 25,
            "tech": {
                "weight_kg": round(1.20 + i / 100, 2),
                "battery_hours": float(12 + i),
                "gaming": bool(i % 2),
            },
        })
        assignments[asin] = truthful.format_profile_for_asin(asin, PROFILE_ORDER)
        presentations[asin] = {
            "campaign_role": campaign_role,
            "objective_profile": "commercial" if lure else "organic",
            "appeal_profile": "value" if lure else "organic",
            "delivery_days": 1,
            "seller_name": "Mercato",
            "seller_rating": 4.8,
            "seller_reviews": 12_800,
        }

    scores = {
        asin: round(score, 12)
        for asin, score in truthful._computed_scores(
            products, presentations, truthful.COMMERCIAL_SCORE_WEIGHTS).items()
    }
    ranked = sorted(asins, key=lambda asin: (-scores[asin], asin))
    paid_order = truthful.sponsored_order(asins)
    sponsored = paid_order[:max(
        1, math.ceil(len(paid_order) * truthful.SPONSORED_FRACTION))]
    deals = [
        product["asin"] for product in products
        if (product["list_price"] - product["price"]) / product["list_price"] >= 0.25
    ]
    best_seller = max(
        products, key=lambda product: (product["bought"], product["asin"]))["asin"]

    cfg = {
        "version": truthful.TRUTHFUL_CONTRACT_VERSION,
        "format_version": 2,
        "format_assignment_basis": "sha256_asin_u64be_mod8_v1",
        "format_profile_order": list(PROFILE_ORDER),
        "format_profiles": _profiles(),
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
        "commercial_score_version": "v2",
        "commercial_score_weights": dict(truthful.COMMERCIAL_SCORE_WEIGHTS),
        "commercial_scores": scores,
        "paid_campaign_asin_assignment_basis": truthful.ASIN_ASSIGNMENT_BASIS,
        "paid_campaign_folds": truthful.PAID_CAMPAIGN_FOLDS,
    }
    params = {
        "sponsored_skus": sponsored,
        "sponsored_basis": truthful.SPONSORED_BASIS,
        "sponsored_fraction": truthful.SPONSORED_FRACTION,
        "choice_sku": ranked[0],
        "best_seller_sku": best_seller,
        "deal_skus": deals,
        "rail_skus": ranked,
        "interleave_slots": [0, 4, 8, 12, 16, 20],
        "choice_basis": "commercial_score_v2_global",
        "best_seller_basis": "max_bought",
        "rail_basis": "commercial_score_v2_global",
        "commercial_score_version": "v2",
        "commercial_scores": scores,
    }
    catalog = {
        "products": products,
        "serving": {"truthful": cfg},
    }
    return catalog, params


def _install(monkeypatch, stype: str = "truthful_combined"):
    catalog, combined = _payload()
    params = combined if stype in {
        "truthful_merchandising", "truthful_combined"} else {}
    if stype == "truthful_combined":
        params = {
            **copy.deepcopy(params),
            "agent_ad": copy.deepcopy(truthful.SOLICITATION_SPEC),
        }
    experiment = _Experiment(catalog, stype, params)
    monkeypatch.setattr(truthful, "_experiment", lambda: experiment)
    return catalog, params


def _pdp(product: dict) -> dict:
    return {
        "asin": product["asin"],
        "technical_details": copy.deepcopy(product["tech"]),
        "bullet_points": ["Seeded canonical copy"],
    }


def test_format_only_hashes_every_asin_and_uses_one_entries_path(monkeypatch):
    catalog, _params = _install(monkeypatch, "truthful_format")
    truthful.validate_contract()
    cfg = catalog["serving"]["truthful"]

    assert set(cfg["format_assignments"].values()) == set(PROFILE_ORDER)
    for product in catalog["products"]:
        asin = product["asin"]
        expected_profile = truthful.format_profile_for_asin(asin, PROFILE_ORDER)
        assert cfg["format_assignments"][asin] == expected_profile
        result = truthful.decorate_pdp(_pdp(product))
        table = result["technical_details"]
        assert list(table) == [
            rule["label"] for rule in
            cfg["format_profiles"][expected_profile]["fields"].values()]
        assert result["bullet_points"] == [
            f"{label}: {value}" for label, value in table.items()]
        assert set(table) != set(
            cfg["format_profiles"]["platform"]["fields"])


def test_combined_keeps_canonical_specs_for_sponsors_and_organic(monkeypatch):
    catalog, params = _install(monkeypatch, "truthful_combined")
    truthful.validate_contract()
    by_asin = {product["asin"]: product for product in catalog["products"]}

    sponsored_asin = params["sponsored_skus"][0]
    sponsored = truthful.decorate_pdp(_pdp(by_asin[sponsored_asin]))
    assert sponsored["sponsored"] is True
    assert sponsored["ad_label"] == "Sponsored"
    assert sponsored["adv_badge"] == truthful.SOLICITATION_TEXT
    assert sponsored["technical_details"] == by_asin[sponsored_asin]["tech"]
    assert sponsored["bullet_points"] == ["Seeded canonical copy"]

    organic_product = next(
        product for product in catalog["products"]
        if product["asin"] not in set(params["sponsored_skus"]))
    organic = truthful.decorate_pdp(_pdp(organic_product))
    assert "adv_badge" not in organic
    assert organic["technical_details"] == organic_product["tech"]
    assert organic["bullet_points"] == ["Seeded canonical copy"]


@pytest.mark.parametrize(
    "stype",
    ["truthful_clean", "truthful_merchandising", "truthful_combined"],
)
def test_unformatted_ablations_leave_seeded_specs_and_copy_untouched(monkeypatch, stype):
    catalog, _params = _install(monkeypatch, stype)
    truthful.validate_contract()
    product = catalog["products"][0]
    original = _pdp(product)
    result = truthful.decorate_pdp(original)
    assert result["technical_details"] == product["tech"]
    assert result["bullet_points"] == ["Seeded canonical copy"]


def test_all_measured_conditions_have_identical_spec_shape_for_every_asin(monkeypatch):
    """Treatment metadata cannot alter a preference key, value, unit, or bullet."""
    snapshots = {}
    for stype in (
            "truthful_clean", "truthful_merchandising", "truthful_combined"):
        catalog, _params = _install(monkeypatch, stype)
        truthful.validate_contract()
        snapshots[stype] = {
            product["asin"]: (
                truthful.decorate_pdp(_pdp(product))["technical_details"],
                truthful.decorate_pdp(_pdp(product))["bullet_points"],
            )
            for product in catalog["products"]
        }
    assert snapshots["truthful_clean"] == snapshots["truthful_merchandising"]
    assert snapshots["truthful_clean"] == snapshots["truthful_combined"]


def test_visible_agent_ad_is_exactly_combined_sponsors(monkeypatch):
    catalog, params = _install(monkeypatch, "truthful_combined")
    truthful.validate_contract()
    sponsored = set(params["sponsored_skus"])
    assert params["agent_ad"] == truthful.SOLICITATION_SPEC
    assert truthful.SOLICITATION_TEXT.isascii()
    assert len(truthful.SOLICITATION_TEXT.encode("ascii")) <= 192
    for product in catalog["products"]:
        decorated = truthful.decorate_pdp(_pdp(product))
        if product["asin"] in sponsored:
            assert decorated["adv_badge"] == truthful.SOLICITATION_TEXT
        else:
            assert "adv_badge" not in decorated

    _catalog, merch_params = _install(monkeypatch, "truthful_merchandising")
    assert "agent_ad" not in merch_params
    assert all(
        "adv_badge" not in truthful.decorate_pdp(_pdp(product))
        for product in _catalog["products"]
    )


@pytest.mark.parametrize(
    ("stype", "mutation"),
    [
        ("truthful_combined", "missing"),
        ("truthful_combined", "version"),
        ("truthful_combined", "surface"),
        ("truthful_combined", "target_basis"),
        ("truthful_combined", "copy"),
        ("truthful_combined", "extra_key"),
        ("truthful_merchandising", "moved"),
        ("truthful_clean", "moved"),
        ("truthful_format", "moved"),
    ],
)
def test_agent_ad_contract_rejects_malformed_or_moved_payload(
        monkeypatch, stype, mutation):
    _catalog, params = _install(monkeypatch, stype)
    if mutation == "missing":
        params.pop("agent_ad")
    elif mutation == "moved":
        params["agent_ad"] = copy.deepcopy(truthful.SOLICITATION_SPEC)
    else:
        params["agent_ad"] = copy.deepcopy(truthful.SOLICITATION_SPEC)
        if mutation == "version":
            params["agent_ad"]["version"] += 1
        elif mutation == "surface":
            params["agent_ad"]["surface"] = "agent_note"
        elif mutation == "target_basis":
            params["agent_ad"]["target_basis"] = "campaign_role"
        elif mutation == "copy":
            params["agent_ad"]["copy"] += " Buy it."
        elif mutation == "extra_key":
            params["agent_ad"]["agent_only"] = True
    with pytest.raises(ValueError, match="solicitation"):
        truthful.validate_contract()


def test_truthful_arms_never_emit_hidden_agent_channels(monkeypatch):
    forbidden = {"agent_note", "adv_hidden", "adv_exclude"}
    for stype in (
            "truthful_clean", "truthful_format",
            "truthful_merchandising", "truthful_combined"):
        catalog, params = _install(monkeypatch, stype)
        truthful.validate_contract()
        sponsored = set(params.get("sponsored_skus") or [])
        for product in catalog["products"]:
            decorated = truthful.decorate_pdp(_pdp(product))
            assert forbidden.isdisjoint(decorated)
            if stype == "truthful_combined" and product["asin"] in sponsored:
                assert decorated.get("adv_badge") == truthful.SOLICITATION_TEXT
            else:
                assert "adv_badge" not in decorated


def test_merchandising_and_combined_differ_only_by_visible_badge(monkeypatch):
    merch_catalog, _merch_params = _install(
        monkeypatch, "truthful_merchandising")
    merchandising = {
        product["asin"]: truthful.decorate_pdp(_pdp(product))
        for product in merch_catalog["products"]
    }

    combined_catalog, combined_params = _install(
        monkeypatch, "truthful_combined")
    sponsored = set(combined_params["sponsored_skus"])
    combined = {}
    for product in combined_catalog["products"]:
        decorated = truthful.decorate_pdp(_pdp(product))
        badge = decorated.pop("adv_badge", None)
        if product["asin"] in sponsored:
            assert badge == truthful.SOLICITATION_TEXT
        else:
            assert badge is None
        combined[product["asin"]] = decorated

    assert combined == merchandising


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("version", "format_version"),
        ("basis", "format_assignment_basis"),
        ("order", "8 unique dialects"),
        ("profiles", "exactly platform"),
        ("assignment", "ASIN-hash dialect"),
        ("missing_field", "does not cover"),
        ("duplicate_label", "duplicate labels"),
        ("zero_scale", "positive scale"),
        ("wrong_unit", "unapproved unit conversion"),
        ("enum_collision", "exactly reversible"),
    ],
)
def test_v2_format_contract_fails_closed(monkeypatch, mutation, message):
    catalog, params = _install(monkeypatch, "truthful_combined")
    cfg = catalog["serving"]["truthful"]
    first_asin = catalog["products"][0]["asin"]
    assigned = cfg["format_assignments"][first_asin]

    if mutation == "version":
        cfg["format_version"] = 1
    elif mutation == "basis":
        cfg["format_assignment_basis"] = "role_selected"
    elif mutation == "order":
        cfg["format_profile_order"][-1] = cfg["format_profile_order"][0]
    elif mutation == "profiles":
        cfg["format_profiles"].pop("platform")
    elif mutation == "assignment":
        cfg["format_assignments"][first_asin] = "platform"
    elif mutation == "missing_field":
        cfg["format_profiles"][assigned]["fields"].pop("weight_kg")
    elif mutation == "duplicate_label":
        fields = cfg["format_profiles"][assigned]["fields"]
        fields["battery_hours"]["label"] = fields["weight_kg"]["label"]
    elif mutation == "zero_scale":
        cfg["format_profiles"][assigned]["fields"]["weight_kg"]["scale"] = 0
    elif mutation == "wrong_unit":
        rule = cfg["format_profiles"][assigned]["fields"]["weight_kg"]
        rule.update(scale=16, suffix=" oz")
    elif mutation == "enum_collision":
        cfg["format_profiles"][assigned]["fields"]["gaming"]["enum"] = {
            "true": "Same", "false": "same"}
    with pytest.raises(ValueError, match=message):
        truthful.validate_contract()


@pytest.mark.parametrize(
    ("key", "value", "rule"),
    [
        (
            "warranty_years", 5,
            {"label": "Protection Period", "scale": 1, "suffix": " years"},
        ),
        (
            "has_laptop_sleeve", True,
            {
                "label": "Notebook Sleeve",
                "enum": {"true": "Included", "false": "Not included"},
            },
        ),
    ],
)
def test_format_labels_cannot_drop_preference_semantics(key, value, rule):
    with pytest.raises(ValueError, match="loses .*semantics"):
        truthful._render_rule("seller", key, value, rule)


def test_seed_inventory_status_is_low_iff_canonical_stock_at_most_five(monkeypatch):
    _install(monkeypatch, "truthful_format")
    stocks = (0, 1, 5, 6, 50)
    products = {
        str(stock): SimpleNamespace(
            stock_quantity=stock, availability_status="in_stock")
        for stock in stocks
    }
    truthful.seed_deals(SimpleNamespace(), products)
    assert {
        stock: products[str(stock)].availability_status for stock in stocks
    } == {
        0: "out_of_stock",
        1: "low_stock",
        5: "low_stock",
        6: "in_stock",
        50: "in_stock",
    }


def test_v2_score_weights_and_global_order_fail_closed(monkeypatch):
    catalog, params = _install(monkeypatch, "truthful_combined")
    truthful.validate_contract()

    cfg = catalog["serving"]["truthful"]
    cfg["commercial_score_weights"]["discount"] = 0.20
    with pytest.raises(ValueError, match="commercial_score_weights"):
        truthful.validate_contract()
    cfg["commercial_score_weights"] = dict(truthful.COMMERCIAL_SCORE_WEIGHTS)

    params["rail_skus"][0], params["rail_skus"][1] = (
        params["rail_skus"][1], params["rail_skus"][0])
    with pytest.raises(ValueError, match="score-descending"):
        truthful.validate_contract()


def test_v2_sponsor_selection_fails_closed_on_role_selected_payload(monkeypatch):
    catalog, params = _install(monkeypatch, "truthful_combined")
    truthful.validate_contract()

    params["sponsored_basis"] = "campaign_role"
    with pytest.raises(ValueError, match="sponsored_basis"):
        truthful.validate_contract()
    params["sponsored_basis"] = truthful.SPONSORED_BASIS

    paid_order = truthful.sponsored_order(
        product["asin"] for product in catalog["products"])
    first_not_sponsored = paid_order[len(params["sponsored_skus"])]
    params["sponsored_skus"][-1] = first_not_sponsored
    with pytest.raises(ValueError, match="ASIN-hash paid-campaign quartile"):
        truthful.validate_contract()


def test_v2_paid_campaign_role_balance_fails_closed(monkeypatch):
    catalog, _params = _install(monkeypatch, "truthful_combined")
    truthful.validate_contract()

    presentations = catalog["serving"]["truthful"]["presentations"]
    paid_order = truthful.sponsored_order(presentations)
    fold_size = len(paid_order) // truthful.PAID_CAMPAIGN_FOLDS
    first_fold = paid_order[:fold_size]
    second_fold = paid_order[fold_size:2 * fold_size]
    first_lure = next(
        asin for asin in first_fold
        if presentations[asin]["campaign_role"] == "lure")
    second_filler = next(
        asin for asin in second_fold
        if presentations[asin]["campaign_role"] == "filler")
    (
        presentations[first_lure]["campaign_role"],
        presentations[second_filler]["campaign_role"],
    ) = (
        presentations[second_filler]["campaign_role"],
        presentations[first_lure]["campaign_role"],
    )

    with pytest.raises(ValueError, match="paid-campaign hash fold"):
        truthful.validate_contract()


def test_sponsored_order_ignores_fixed_asin_facts_and_roles():
    catalog, _params = _payload()
    asins = [product["asin"] for product in catalog["products"]]
    baseline = truthful.sponsored_order(asins)

    for index, product in enumerate(catalog["products"]):
        product.update(
            price=float(10_000 - index),
            list_price=float(10_001 - index),
            rating=1.0 + (index % 5),
            reviews=index,
            bought=index * 17,
        )
    presentations = catalog["serving"]["truthful"]["presentations"]
    role_cycle = ("hero", "settle", "lure", "filler")
    for index, asin in enumerate(asins):
        presentations[asin]["campaign_role"] = role_cycle[index % len(role_cycle)]

    assert truthful.sponsored_order(asins) == baseline


def test_decimal_renderer_rejects_lossy_or_nonfinite_scales():
    for scale in (0, -1, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="positive scale"):
            truthful._decimal_text(1.25, scale)
