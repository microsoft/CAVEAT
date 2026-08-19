from __future__ import annotations

import copy
from collections import Counter, defaultdict

import pytest

from harness_posttrain_eval.common import IntegrityError, canonical_bytes, sha256_bytes
from harness_posttrain_eval.matrix import (
    audit_matrix,
    diagnostic_matrix,
    final_matrix,
    shadow_gate_matrix,
    shadow_selection_matrix,
)
from harness_posttrain_eval.split import generate_split


def test_all_matrix_counts(config):
    split = generate_split(config)
    diagnostic = diagnostic_matrix(config)
    selection = shadow_selection_matrix(
        config, split, candidate_arms=("balanced", "recovery-heavy", "protocol-heavy")
    )
    gate = shadow_gate_matrix(config, split, selected_arm="balanced")
    final = final_matrix(config, selected_arm="trained")
    development_final = final_matrix(
        config, selected_arm="trained", scenario_scope="development"
    )
    heldout_final = final_matrix(config, selected_arm="trained", scenario_scope="heldout")
    final_with_ablation = final_matrix(
        config, selected_arm="post_sft", include_sft_parent=True
    )
    assert audit_matrix(diagnostic)["run_count"] == 16
    assert audit_matrix(selection)["run_count"] == 128
    assert audit_matrix(gate)["run_count"] == 128
    assert audit_matrix(final)["run_count"] == 320
    assert audit_matrix(development_final)["run_count"] == 64
    assert audit_matrix(heldout_final)["run_count"] == 256
    assert {
        row["run_id"] for row in development_final["runs"] + heldout_final["runs"]
    } == {row["run_id"] for row in final["runs"]}
    assert audit_matrix(final_with_ablation)["run_count"] == 400
    assert Counter(row["arm"] for row in final["runs"]) == {"base": 160, "trained": 160}
    assert Counter(row["condition"] for row in final["runs"]) == {
        "combined": 200,
        "clean": 120,
    }
    assert Counter(row["arm"] for row in final_with_ablation["runs"]) == {
        "base": 160,
        "post_sft": 160,
        "sft_parent": 80,
    }


def test_final_pairs_are_exact_and_holdout_is_unambiguous(config):
    final = final_matrix(config, selected_arm="trained")
    pairs = defaultdict(set)
    for row in final["runs"]:
        pairs[row["pair_id"]].add(row["arm"])
        assert row["held_out"] is (row["scenario"] != "laptop")
    assert len(pairs) == 160
    assert all(arms == {"base", "trained"} for arms in pairs.values())


def test_final_pair_cannot_repeat_an_arm_under_a_new_run_id(config):
    matrix = final_matrix(config, selected_arm="trained")
    duplicate = copy.deepcopy(matrix["runs"][0])
    duplicate["run_id"] += "::duplicate"
    matrix["runs"].append(duplicate)
    core = {key: value for key, value in matrix.items() if key != "matrix_sha256"}
    matrix["matrix_sha256"] = sha256_bytes(canonical_bytes(core))
    with pytest.raises(IntegrityError, match="must contain one"):
        audit_matrix(matrix)
