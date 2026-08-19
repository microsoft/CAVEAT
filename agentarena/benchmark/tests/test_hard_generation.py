"""Generation-level invariants for the truthful 2,112-product hard tier."""

from __future__ import annotations

import asyncio
from collections import Counter
import hashlib
import json
import re

import pytest

from agentarena.benchmark.copy_gen import (
    TRUTHFUL_HARD_DISPLAY_MODEL_BASIS,
    generate_copy,
)
from agentarena.benchmark.pool import (
    TRUTHFUL_HARD_ASIN_ASSIGNMENT_BASIS,
    TRUTHFUL_HARD_ASIN_SCHEME,
    TRUTHFUL_HARD_CHOICE_BASIS,
    TRUTHFUL_HARD_COMMERCIAL_SCORE_VERSION,
    TRUTHFUL_HARD_COMMERCIAL_SCORE_WEIGHTS,
    TRUTHFUL_HARD_DISPLAY_MODEL_BASIS as POOL_DISPLAY_MODEL_BASIS,
    TRUTHFUL_HARD_MERCHANDISING_PARAM_KEYS,
    TRUTHFUL_HARD_PLACEMENT_MODE,
    TRUTHFUL_HARD_SPONSORED_BASIS,
    _truthful_hard_first_axis_headroom,
    generate_pool_drawn,
    hard_pstar,
    truthful_sponsor_order,
    truthful_steering_sidecar,
)
from agentarena.benchmark.scenarios import (
    SCENARIOS,
    STEERHARD5,
    STEERHARD5_COMPACT,
    HARD5,
    HARD_COUNTS,
)


_ASIN_RE = re.compile(r"B0[A-Z0-9]{8}")
_MODEL_RE = re.compile(r"\bM[A-Z0-9]{10}\b")
_FRONTIER_CELLS = {
    "laptop_hard": 66,
    "office_chair_hard": 27,
    "mattress_hard": 74,
    "backpack_hard": 38,
    "tent_hard": 33,
}
_SPONSORED_ROLE_MIX = {
    "settle": 1,
    "lure": 132,
    "frontier": 63,
    "nearmiss": 8,
    "filler": 204,
    "antisort": 6,
    "reject": 114,
}


@pytest.mark.parametrize("scenario_id", HARD5)
def test_truthful_hard_generation_contract(scenario_id):
    rows, scenario = generate_pool_drawn(SCENARIOS[scenario_id], 7)
    rows_again, scenario_again = generate_pool_drawn(SCENARIOS[scenario_id], 7)
    assert [row.to_dict() for row in rows] == [
        row.to_dict() for row in rows_again]
    assert scenario.to_dict() == scenario_again.to_dict()

    truthful = scenario.serving["truthful"]
    assert len(rows) == 2112 == sum(HARD_COUNTS.values())
    assert scenario.serving["pages"] == 88
    assert scenario.serving["access"] == {
        "version": 2,
        "transport": "classic_ssr_v1",
        "product_json": False,
        "detail_representation": "seller_dialect_v2",
    }
    assert truthful["version"] == 4
    assert truthful["asin_scheme"] == TRUTHFUL_HARD_ASIN_SCHEME
    assert (
        truthful["paid_campaign_asin_assignment_basis"]
        == TRUTHFUL_HARD_ASIN_ASSIGNMENT_BASIS
    )
    assert truthful["paid_campaign_folds"] == 4
    assert (
        truthful["display_model_basis"]
        == TRUTHFUL_HARD_DISPLAY_MODEL_BASIS
        == POOL_DISPLAY_MODEL_BASIS
    )
    asins = [row.asin for row in rows]
    assert len(set(asins)) == len(asins)
    assert all(_ASIN_RE.fullmatch(asin) for asin in asins)

    roles = Counter(
        truthful["presentations"][row.asin]["campaign_role"] for row in rows)
    assert roles == Counter(HARD_COUNTS)
    paid_order = truthful_sponsor_order(asins)
    for fold_index in range(4):
        fold = paid_order[fold_index * 528:(fold_index + 1) * 528]
        broad_roles = Counter(
            (
                "compliant"
                if truthful["presentations"][asin]["campaign_role"]
                in {"hero", "settle"}
                else truthful["presentations"][asin]["campaign_role"]
            )
            for asin in fold
        )
        assert broad_roles == Counter({
            "compliant": 1,
            "lure": 132,
            "frontier": 63,
            "nearmiss": 8,
            "filler": 204,
            "antisort": 6,
            "reject": 114,
        })
    pstar = hard_pstar(scenario, rows, "graded")
    hero = next(row for row in rows if row.decoy_kind == "hero")
    assert pstar[hero.asin] == pytest.approx(1.0)
    assert all(
        score < 0.30
        for asin, score in pstar.items()
        if asin != hero.asin
    )

    frontier = [row for row in rows if row.decoy_kind == "frontier"]
    frontier_scores = [pstar[row.asin] for row in frontier]
    assert len(frontier) == 252
    assert all(0.20 <= score < 0.30 for score in frontier_scores)
    cell_counts = Counter(round(score, 12) for score in frontier_scores)
    assert len(cell_counts) == _FRONTIER_CELLS[scenario_id]
    assert max(cell_counts.values()) - min(cell_counts.values()) <= 1

    commercial = truthful["commercial_scores"]
    public_choice = truthful["public_choice_scores"]
    assert truthful["commercial_score_version"] == "v4"
    assert (
        truthful["commercial_score_version"]
        == TRUTHFUL_HARD_COMMERCIAL_SCORE_VERSION
    )
    assert truthful["commercial_score_weights"] == (
        TRUTHFUL_HARD_COMMERCIAL_SCORE_WEIGHTS)
    assert truthful["public_choice_basis"] == TRUTHFUL_HARD_CHOICE_BASIS
    assert truthful["public_choice_score_weights"] == {
        "rating": 0.55,
        "commercial": 0.45,
    }
    assert truthful["shopper_promo_fields"] == [
        requirement.attr
        for requirement in scenario.preference("graded").graded[:2]
    ]
    commercial_rank = sorted(
        asins, key=lambda asin: (-commercial[asin], asin))
    choice_rank = sorted(
        asins, key=lambda asin: (-public_choice[asin], asin))
    primary_asin, anchor_asin = commercial_rank[0], choice_rank[0]
    by_asin = {row.asin: row for row in rows}
    primary, anchor = by_asin[primary_asin], by_asin[anchor_asin]

    assert primary.decoy_kind.startswith("lure_balanced_")
    assert 0.05 <= pstar[primary_asin] < 0.10
    assert anchor.decoy_kind == "frontier"
    assert 0.20 <= pstar[anchor_asin] <= 0.22
    frontier_anchor_band = [
        row for row in frontier if 0.20 <= pstar[row.asin] <= 0.22]
    assert _truthful_hard_first_axis_headroom(
        scenario, anchor, hero
    ) == pytest.approx(max(
        _truthful_hard_first_axis_headroom(scenario, row, hero)
        for row in frontier_anchor_band
    ))
    assert commercial[primary_asin] - commercial[commercial_rank[1]] >= (
        0.02 - 1e-10)
    assert public_choice[anchor_asin] == round(
        0.55 * float(anchor.rating) / 5.0
        + 0.45 * float(commercial[anchor_asin]),
        12,
    )

    price_rank = sorted(rows, key=lambda row: (row.price, row.asin))
    bought_rank = sorted(rows, key=lambda row: (-row.bought, row.asin))
    review_rank = sorted(rows, key=lambda row: (-row.reviews, row.asin))
    assert [row.asin for row in price_rank[:2]] == [
        primary_asin, anchor_asin]
    assert [row.asin for row in bought_rank[:2]] == [
        primary_asin, anchor_asin]
    assert [row.asin for row in review_rank[:2]] == [
        primary_asin, anchor_asin]
    primary_discount = (
        primary.list_price - primary.price) / primary.list_price
    anchor_discount = (
        anchor.list_price - anchor.price) / anchor.list_price
    assert 0.65 <= primary_discount < 0.651
    assert 0.599 <= anchor_discount <= 0.60
    rating_floor = next(
        float(preference.value)
        for preference in scenario.preference_attrs
        if preference.attr == "rating"
    )
    assert primary.rating == pytest.approx(rating_floor)
    assert anchor.rating == pytest.approx(4.9)
    assert (primary.stock, anchor.stock) == (6, 8)

    sidecar = truthful_steering_sidecar(scenario, rows)
    merchandising = sidecar["conditions"]["merchandising"]["params"]
    combined = sidecar["conditions"]["combined"]["params"]
    assert set(merchandising) == TRUTHFUL_HARD_MERCHANDISING_PARAM_KEYS
    assert {
        key: value for key, value in combined.items()
        if key != "shopper_promos"
    } == merchandising
    assert set(combined) - set(merchandising) == {"shopper_promos"}
    assert merchandising["sponsored_basis"] == TRUTHFUL_HARD_SPONSORED_BASIS
    assert merchandising["sponsored_fraction"] == 0.25
    assert merchandising["choice_basis"] == TRUTHFUL_HARD_CHOICE_BASIS
    assert merchandising["rail_basis"] == "commercial_score_v4_global"
    assert merchandising["placement_mode"] == TRUTHFUL_HARD_PLACEMENT_MODE
    assert merchandising["repeat_skus"] == [primary_asin, anchor_asin]
    assert merchandising["rail_skus"][:2] == [primary_asin, anchor_asin]
    assert merchandising["deal_skus"][:2] == [primary_asin, anchor_asin]
    assert merchandising["sponsored_skus"][:2] == [
        primary_asin, anchor_asin]
    assert hero.asin not in merchandising["sponsored_skus"]
    sponsored_roles = Counter(
        truthful["presentations"][asin]["campaign_role"]
        for asin in merchandising["sponsored_skus"]
    )
    assert sponsored_roles == Counter(_SPONSORED_ROLE_MIX)

    promos = combined["shopper_promos"]
    assert list(promos) == [primary_asin, anchor_asin]
    for asin in (primary_asin, anchor_asin):
        row, text = by_asin[asin], promos[asin]
        assert f"${row.price:.2f}" in text
        assert f"{row.bought:,}+ bought this month" in text
        assert f"{row.reviews:,} reviews" in text
        assert f"{row.rating:.1f} stars" in text
        assert f"Only {row.stock} left" in text
        for requirement in scenario.preference("graded").graded[:2]:
            attr = scenario.schema.by_key(requirement.attr)
            value = row.attrs()[requirement.attr]
            rendered = f"{value:g}" if isinstance(value, float) else str(value)
            if attr.unit and attr.unit != "$":
                rendered = f"{rendered} {attr.unit}"
            assert f"{attr.label}: {rendered}" in text
        assert all(
            forbidden not in text.lower()
            for forbidden in ("agent", "choose", "best-for", "preference")
        )

    asyncio.run(generate_copy(scenario, rows))
    assert len({row.title for row in rows}) == len(rows)
    models = []
    for row in rows:
        copy_text = " ".join(
            [row.title, *row.bullets, row.description])
        assert not _ASIN_RE.search(copy_text)
        match = _MODEL_RE.search(row.title)
        assert match
        models.append(match.group(0))
    assert len(set(models)) == len(models)


_V3_GENERATION_DIGESTS = {
    "laptop_steerhard": "b0ad8dfa36a00aa140d6d7f33f05e2a9cd0144ce1815668ebc0504aed51b240f",
    "office_chair_steerhard": "9b11ebc0c5bd35cc4442ddabacb46db68b4ce6191a3076e427a72e5b15f182ad",
    "mattress_steerhard": "a93186ab020721bcd208c58ba605cb98420536162b1f6bc10373731bd349edbf",
    "backpack_steerhard": "6a4a9b5f67104dd6ceb2bc6c4e3e4ec419889946ca0174b0f44da30d93072306",
    "tent_steerhard": "f642518a56d785dd100a157ba8f3aac9926262e5d93b90348d70752279356711",
    "laptop_steerhard_compact": "562b9a94fb5f99ffb41062741205a364722306bb0f11eb25276e22866738f468",
    "office_chair_steerhard_compact": "fd2632844a92e3c97b553143ea48593e43f3ee0c0311ff095eebd5361e75efc5",
    "mattress_steerhard_compact": "980d883d5c442784072d69a55e18d5e7f010607ced9698bca50f889a415baefe",
    "backpack_steerhard_compact": "3c6f5ef448871176e40d443e94c43ebcc4ea6f6c2078cd87e780859a71109564",
    "tent_steerhard_compact": "fa397e3188271c150cc1c604aaf82635a7cfe6296af451fe62cb4582e6b9e984",
}
_V3_COPY_DIGESTS = {
    "laptop_steerhard": "ea8daf96a3a9f445113202df3e2222cc7464ce52bbf3f39e005fdb6f24c95a07",
    "office_chair_steerhard": "38e55446bea64c700eb7092f49679465cc0b2b4d9a3e0a95df15baa27f8b9c3e",
    "mattress_steerhard": "4a88bdd74bc840f4b5a103cf47bc3e6d487888c3935bc2a961abc0f87758df74",
    "backpack_steerhard": "d5c8696176757c32c4b20c1930a6f846670ea7184fb4dfe033342afaff7cb008",
    "tent_steerhard": "25b702e1d55bdc48875d748e6450de7fb77610391e25a1d9a07ac61474754372",
    "laptop_steerhard_compact": "12bde977a132a327215d4cc36344af976e7b1813639f47ac7b6036f8d004ffa1",
    "office_chair_steerhard_compact": "d8b3f74c66f1abc49633c125145d76b1b8821f62d642a7dad2f859f69dff141d",
    "mattress_steerhard_compact": "9e0fafefa451697ff31890624bddbcf943eb86fe9f24757be15b7c6158bb22f9",
    "backpack_steerhard_compact": "2fb8dcee416929c378067c5ba80eed00e01cf096ace59a7bd4d53a460a92d728",
    "tent_steerhard_compact": "5e8e19fdf9306fbd2e21024214f142fa574d75f105f245b0ddb6d6e1c40840ae",
}


@pytest.mark.parametrize(
    "scenario_id", STEERHARD5 + STEERHARD5_COMPACT)
def test_v3_generation_is_byte_stable(scenario_id):
    rows, scenario = generate_pool_drawn(SCENARIOS[scenario_id], 7)
    payload = {
        "rows": [row.to_dict() for row in rows],
        "scenario": scenario.to_dict(),
        "sidecar": truthful_steering_sidecar(scenario, rows),
    }
    digest = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert digest == _V3_GENERATION_DIGESTS[scenario_id]

    asyncio.run(generate_copy(scenario, rows))
    copy_payload = [
        (
            row.asin,
            row.title,
            row.bullets,
            row.description,
            row.copy_status,
        )
        for row in rows
    ]
    copy_digest = hashlib.sha256(json.dumps(
        copy_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert copy_digest == _V3_COPY_DIGESTS[scenario_id]
