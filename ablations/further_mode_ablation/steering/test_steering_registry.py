from __future__ import annotations

import json

import pytest

from ablations.further_mode_ablation.steering import registry


def test_exact_ordered_16_arm_inventory_and_deltas() -> None:
    before = registry.production_source_inventory()
    receipt = registry.validate_standard_arm_registry()
    assert receipt["arm_count"] == 16
    assert tuple(receipt["arms"]) == registry.STANDARD_STEERING_ARMS
    assert receipt["oracle_pstar"] == 1.0
    assert receipt["custom_deltas"] == {
        "abl-substrate": {"pin": False, "bury": False, "bury_index": None, "authored_fact_keys": []},
        "abl-pin-d0": {"pin": True, "bury": False, "bury_index": None, "authored_fact_keys": []},
        "abl-bury-d23": {"pin": False, "bury": True, "bury_index": 23, "authored_fact_keys": []},
        "abl-place-d23": {"pin": True, "bury": True, "bury_index": 23, "authored_fact_keys": []},
        "abl-place-d30": {"pin": True, "bury": True, "bury_index": 30, "authored_fact_keys": []},
        "abl-place-d52": {"pin": True, "bury": True, "bury_index": 52, "authored_fact_keys": []},
    }
    assert registry.production_source_inventory() == before


def test_clean_and_original_named_conditions_are_literal_canonical_values() -> None:
    raw = json.loads(registry.CANONICAL_STEERING_PATH.read_text())
    assert registry.resolve_standard_arm("laptop", "clean") == {"type": "clean"}
    for condition in registry.CANONICAL_STEERING_ARMS:
        assert registry.resolve_standard_arm("laptop", condition) == raw[condition]


def test_custom_specs_are_placement_only_and_oracle_safe() -> None:
    canonical = json.loads(registry.CANONICAL_STEERING_PATH.read_text())["ranking"]
    compliant = set(canonical["bury_skus"])
    for condition in registry.CUSTOM_STEERING_ARMS:
        spec = registry.resolve_standard_arm("laptop", condition)
        assert spec["steering_id"] == "ranking"
        assert spec["decoy_skus"] == canonical["decoy_skus"]
        assert set(spec["params"]) == {"pin", "placement"}
        assert not ({"fees", "addons", "deals", "trust", "scarcity"} & set(spec["params"]))
        assert not (set(spec["decoy_skus"]) & compliant)


@pytest.mark.parametrize("condition", ["abl-unknown", "abl-place-d31", "abl-"])
def test_unknown_ablation_names_fail_closed(condition: str) -> None:
    with pytest.raises(ValueError, match="unknown Standard steering ablation"):
        registry.resolve_standard_arm("laptop", condition)


def test_custom_controls_fail_closed_on_other_catalogs() -> None:
    with pytest.raises(ValueError, match="certified only"):
        registry.resolve_standard_arm("tent", "abl-place-d23")
