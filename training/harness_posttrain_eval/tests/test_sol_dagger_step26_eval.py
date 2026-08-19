from __future__ import annotations

from pathlib import Path

from harness_posttrain_eval import sol_dagger_laptop_eval as shared
from harness_posttrain_eval import sol_dagger_step26_candidate_serve as serve
from harness_posttrain_eval import sol_dagger_step26_laptop_eval as evaluator

ROOT = Path(__file__).resolve().parents[3]
R4 = ROOT / "results/harness_posttrain_fixed_v7_grpo_fast_laptop_eval_20260814_r4"
REPAIR = ROOT / "results/harness_posttrain_fixed_v7_repair_step24_candidate_eval_20260813"


def test_step26_reuses_exact_sealed_r4_projection() -> None:
    launch, _frozen, _r4_report, _repair_report = evaluator._source_inputs(
        R4 / "bundle/launch_manifest.json",
        R4 / "bundle/frozen_manifest.json",
        R4 / "report/report.json",
        REPAIR / "reports/laptop/report.json",
    )
    rows = evaluator._source_rows(launch)
    assert [cell for _row, _config, cell in rows] == [
        (variant, repetition)
        for variant in evaluator.VARIANTS
        for repetition in evaluator.REPETITIONS
    ]
    for _row, config, _cell in rows:
        assert evaluator.assert_r4_config_invariance(config, config)


def test_step26_configuration_changes_only_candidate_metadata(monkeypatch) -> None:
    fields = (
        "PREREG_SCHEMA",
        "CANDIDATE_NAME",
        "CANDIDATE_RESULT_KEY",
        "SOURCE_STEP",
        "FINAL_STEP",
        "validate_endpoint",
        "validate_lineage_attestation",
    )
    for field in fields:
        monkeypatch.setattr(shared, field, getattr(shared, field))
    evaluator._configure()
    assert shared.CANDIDATE_NAME == "step26-sol-dagger-cleanup-sft"
    assert shared.CANDIDATE_RESULT_KEY == "sol_dagger_cleanup_step26"
    assert (shared.SOURCE_STEP, shared.FINAL_STEP) == (25, 26)
    assert shared.validate_endpoint is serve.validate_endpoint
    assert shared.assert_r4_config_invariance is evaluator.assert_r4_config_invariance


def test_direct_cleanup_mass_counts_only_executed_repair_rows() -> None:
    by_role = {
        "repair_cart_cleanup": 40,
        "repair_cart_navigation": 10,
        "repair_clean_checkout": 10,
        "repair_place_order": 10,
        "sol_discovery_retention": 20,
        "sol_focused_cart_cleanup": 10,
    }
    value = {
        "by_role": by_role,
        "total": 100,
        "direct_dirty_cart": 40,
        "direct_dirty_cart_fraction": 0.4,
        "gate": {"minimum": 0.35, "maximum": 0.45},
    }
    assert serve._mass_valid(value)
    value["direct_dirty_cart"] = 50
    value["direct_dirty_cart_fraction"] = 0.5
    assert not serve._mass_valid(value)


def test_step26_validator_binds_executed_trainer_source() -> None:
    assert serve.TRAINER_GIT_SHA == "295f1fbc60b49195a467c8e7e2313cf665247915"
