from __future__ import annotations

import pytest

from ablations.further_mode_ablation import analyze_results as v1_analysis
from ablations.further_mode_ablation_hard_v2 import analyze_results as analysis
from ablations.further_mode_ablation_hard_v2 import campaign


def _rows() -> list[dict]:
    rows = []
    for frozen in campaign.build_schedule("synthetic-hard-v2", 18000):
        row = {
            **frozen,
            "preservation_strict": 0.2,
            "hero": 0,
            "strict_binary": 0.0,
            "chosen": None,
            "num_steps": 20,
            "seconds": 100.0,
            "process_endpoints": {
                name: 0 for name in analysis.PROCESS_ENDPOINTS
                if name not in {"num_steps", "seconds"}
            },
            "axis1_rank_fraction": 0.1,
            "axis2_rank_fraction": 0.5,
            "axis1_rank_penalized": 2,
            "axis2_rank_penalized": 6,
        }
        if row["arm_id"] == "F_combined_original":
            row.update(
                preservation_strict=1.0, hero=1, strict_binary=1.0
            )
        if row["arm_id"] == "B_combined_reversed":
            row.update(axis1_rank_fraction=0.5, axis2_rank_fraction=0.1)
        rows.append(row)
    return rows


def test_exact_hard_effect_families_are_frozen_without_standard_effects() -> None:
    result = analysis.compute_effects(_rows(), include_directional_rank=True)
    # 18 hard score contrasts * three endpoints + three directional ranks.
    assert len(result["effects"]) == 57
    assert result["scheduled_denominator"] == 112
    assert not any(
        effect["family"].startswith("standard_")
        for effect in result["effects"]
    )
    assert {
        effect["family"] for effect in result["effects"]
    } == set(analysis.HARD_CONTRAST_FAMILIES) | {
        "hard_objective_order_directional_rank"
    }
    assert all(
        "holm_p_within_frozen_family_and_endpoint" in effect
        for effect in result["effects"]
    )


def test_hard_estimands_and_process_endpoints_are_identical_to_v1() -> None:
    assert analysis.ENDPOINTS == v1_analysis.ENDPOINTS
    assert analysis.PROCESS_ENDPOINTS == v1_analysis.PROCESS_ENDPOINTS
    assert analysis.PROCESS_PAIRS == v1_analysis.PROCESS_PAIRS
    assert {
        family: analysis.CONTRASTS[family]
        for family in analysis.HARD_CONTRAST_FAMILIES
    } == {
        family: v1_analysis.CONTRASTS[family]
        for family in analysis.HARD_CONTRAST_FAMILIES
    }


def test_hard_process_descriptives_have_14_exact_arms() -> None:
    result = analysis.compute_process_descriptives(_rows())
    assert len(result["arm_aggregates"]) == 14
    assert all(item["n_runs"] == 8 for item in result["arm_aggregates"])
    assert any(
        item["contrast"] == "C-P" and item["endpoint"] == "num_steps"
        for item in result["paired_stage_descriptives"]
    )


def test_analysis_rejects_any_nonexact_denominator_or_binary_endpoint() -> None:
    rows = _rows()
    with pytest.raises(analysis.AnalysisError, match="112-run"):
        analysis.compute_effects(rows[:-1])
    rows[0]["strict_binary"] = 0.5
    with pytest.raises(analysis.AnalysisError, match="exactly binary"):
        analysis.compute_effects(rows)
