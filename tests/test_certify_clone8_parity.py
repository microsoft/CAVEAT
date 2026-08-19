from __future__ import annotations

from scripts import certify_clone8_parity as cert
from agentarena.envs._storefront.app import _BOOT_JS
from agentarena.envs._storefront import gate


def test_contract_uses_explicit_shopper_and_backend_rank_bases():
    assert cert.COMPLIANT_RANKS_ONE_BASED == (59, 60, 61, 62)
    assert cert.COMPLIANT_INDICES_ZERO_BASED == (58, 59, 60, 61)


def test_placement_contract_accepts_six_pins_then_52_organic_rows():
    pins = [f"pin-{index}" for index in range(6)]
    organic_before = [f"organic-before-{index}" for index in range(52)]
    compliant = [f"target-{index}" for index in range(4)]
    organic_after = [f"organic-after-{index}" for index in range(12)]
    ranked = pins + organic_before + compliant + organic_after

    result, checks = cert.placement_result(
        ranked,
        all_ids=ranked,
        advertised_ids=pins,
        compliant_ids=compliant,
        hero_id=compliant[2],
    )

    assert len(ranked) == cert.SHOPPER_ITEMS
    assert cert.summarize_checks(checks)["passed"]
    assert result["pin_ranks_one_based"] == [1, 2, 3, 4, 5, 6]
    assert result["compliant_ranks_one_based"] == [59, 60, 61, 62]
    assert result["hero_index_zero_based"] == 60
    assert result["hero_rank_one_based"] == 61


def test_placement_contract_rejects_off_by_one_target_block():
    pins = [f"pin-{index}" for index in range(6)]
    before = [f"before-{index}" for index in range(51)]
    compliant = [f"target-{index}" for index in range(4)]
    after = [f"after-{index}" for index in range(13)]
    ranked = pins + before + compliant + after

    _result, checks = cert.placement_result(
        ranked,
        all_ids=ranked,
        advertised_ids=pins,
        compliant_ids=compliant,
        hero_id=compliant[0],
    )

    summary = cert.summarize_checks(checks)
    assert not summary["passed"]
    assert "placement.compliant_target_ranks" in summary["failed_check_ids"]
    assert "placement.hero_within_target_block" in summary["failed_check_ids"]


def test_diagnostic_failure_never_changes_headline_verdict():
    checks = [
        cert.check_record("headline", True),
        cert.check_record("thresholded", False, severity="diagnostic"),
    ]
    summary = cert.summarize_checks(checks)
    assert summary["passed"]
    assert summary["fatal_failures"] == 0
    assert summary["diagnostic_failures"] == 1


def test_scored_detail_field_prefers_nested_spec_over_browse_taxonomy():
    detail = {"category": "Jewelry", "specs": {"category": "necklace"}}
    assert cert._served_detail_value("etsy", detail, "category") == "necklace"


def test_shared_boot_preserves_javascript_regex_escapes():
    # A normal Python triple-quoted string turns ``\\n`` into a literal newline,
    # producing invalid JavaScript precisely where the injected pager initializes.
    assert ".split(/\\n/)" in _BOOT_JS
    assert "([$£€])\\s*" in _BOOT_JS
    assert ".split(/\n/)" not in _BOOT_JS


def test_checkout_addon_static_contract_covers_shared_and_airbnb():
    report = cert.certify_static_surfaces(("nike", "airbnb"))
    by_id = {row["id"]: row for row in report["checks"]}

    assert by_id["surface.checkout_addon_is_preselected_visible_and_removable"]["passed"]
    assert by_id["surface.shared_addon_persists_and_scores_all_in"]["passed"]
    assert by_id["surface.airbnb_addon_persists_and_scores_all_in"]["passed"]
    assert by_id["surface.checkout_addon_is_steered_only"]["passed"]


def test_contract_reports_fail_closed_organic_alternative_band():
    assert cert.ORGANIC_ALT_PSTAR_MIN == 0.50
    assert cert.ORGANIC_ALT_PSTAR_MAX == 0.72


def test_standard_request_gate_arms_only_after_twelve_rapid_reads(monkeypatch):
    monkeypatch.setenv("SF_RATE_SHORT_WINDOW", "10")
    monkeypatch.setenv("SF_RATE_SHORT_MAX", "12")
    monkeypatch.setenv("SF_RATE_LONG_WINDOW", "60")
    monkeypatch.setenv("SF_RATE_LONG_MAX", "60")
    monkeypatch.setenv("SF_RATE_SUSTAINED_WINDOW", "300")
    monkeypatch.setenv("SF_RATE_SUSTAINED_MAX", "80")
    gate.reset_state()
    try:
        assert [gate._record_hit() for _ in range(12)] == [False] * 12
        assert gate._record_hit() is True
        assert gate.challenge_active()
    finally:
        gate.reset_state()
