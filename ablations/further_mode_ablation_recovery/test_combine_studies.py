from __future__ import annotations

import copy

import pytest

from ablations.further_mode_ablation_recovery import combine_studies as combine


def _rows(arms: set[str], prefix: str) -> list[dict]:
    return [
        {
            "run_id": f"{prefix}_{arm}_r{repeat}",
            "arm_id": arm,
            "repeat": repeat,
            "block": ((repeat - 1) // 2) + 1,
            "hero": repeat % 2,
            "strict_binary": float(repeat % 2),
            "preservation_strict": float(repeat % 2),
        }
        for repeat in range(1, 9)
        for arm in sorted(arms)
    ]


def test_cross_study_policy_freezes_counts_analyzer_and_preoutcome_status():
    policy = combine._policy()
    assert policy["recorded_before_hard_v2_outcomes"] is True
    assert policy["combined_denominator"] == 240
    assert policy["analyzer"]["total_effects"] == 102
    assert policy["analyzer"]["sha256"] == combine.EXPECTED_ANALYZER_SHA256


def test_exact_source_partitions_accept_disjoint_128_plus_112():
    standard = _rows(combine.STANDARD_ARMS, "standard")
    hard = _rows(combine.HARD_ARMS, "hard")
    combine._validate_partitions(standard, hard)
    assert len(standard) == 128
    assert len(hard) == 112


def test_overlapping_source_run_identity_fails_closed():
    standard = _rows(combine.STANDARD_ARMS, "standard")
    hard = _rows(combine.HARD_ARMS, "hard")
    hard[0]["run_id"] = standard[0]["run_id"]
    with pytest.raises(combine.CombineError, match="run IDs overlap"):
        combine._validate_partitions(standard, hard)


def test_provenance_addition_does_not_relabel_original_identity_fields():
    row = _rows({"clean"}, "standard")[0]
    before = copy.deepcopy(row)
    bundle_ref = {"path": "/tmp/source.json", "sha256": "a" * 64, "size": 1}
    combined = combine._provenance_row(
        row,
        study_id=combine.STANDARD_STUDY_ID,
        bundle_ref=bundle_ref,
        source_campaign_id="further_mode_ablation_v1",
    )
    assert row == before
    for field, value in before.items():
        assert combined[field] == value
    assert combined["source_provenance"]["original_run_id"] == before["run_id"]
    assert combined["source_provenance"]["identity_fields_relabelled"] is False


def test_missing_repeat_in_either_source_fails_closed():
    standard = _rows(combine.STANDARD_ARMS, "standard")
    hard = _rows(combine.HARD_ARMS, "hard")
    hard[-1]["repeat"] = 7
    with pytest.raises(combine.CombineError, match="repetitions 1..8 absent"):
        combine._validate_partitions(standard, hard)

