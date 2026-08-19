from __future__ import annotations

from pathlib import Path

from harness_posttrain_eval import interactive_sol_dagger_laptop_eval as evaluator
from harness_posttrain_eval import sol_dagger_laptop_eval as sealed_r4

ROOT = Path(__file__).resolve().parents[3]
R4 = ROOT / "results/harness_posttrain_fixed_v7_grpo_fast_laptop_eval_20260814_r4"
REPAIR = ROOT / "results/harness_posttrain_fixed_v7_repair_step24_candidate_eval_20260813"


def test_campaign2_reuses_exact_sealed_r4_projection() -> None:
    assert evaluator.EXPECTED_CELLS is sealed_r4.EXPECTED_CELLS
    assert evaluator.ALLOWED_CONFIG_DIFFERENCES is sealed_r4.ALLOWED_CONFIG_DIFFERENCES
    assert evaluator.assert_r4_config_invariance is sealed_r4.assert_r4_config_invariance
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


def test_campaign2_candidate_identity_and_user_promotion_thresholds() -> None:
    assert evaluator.CANDIDATE_NAME == "step26-action-weighted-ce"
    assert evaluator.CANDIDATE_RESULT_KEY == "interactive_sol_dagger_r1"
    assert evaluator.DIRECTIONAL_TARGET == {
        "strict_successes_at_least": 4,
        "hero_opened_at_least": 6,
        "hero_chosen_at_least": 6,
        "addon_present_at_most": 2,
        "valid_transaction_at_least": 8,
    }
    assert evaluator.ACTION_PROMOTION_GATE == {
        "buy_now_actions_at_most": 0,
        "cart_entered_at_least": 6,
        "delete_for_every_dirty_cart": True,
    }
