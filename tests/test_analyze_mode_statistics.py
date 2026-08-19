from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import analyze_mode_statistics as stats  # noqa: E402


def _trajectory_row(tmp_path: Path, trajectory: dict) -> dict:
    path = tmp_path / "trajectory.json"
    path.write_text(json.dumps(trajectory))
    return {
        "run_id": "fixture",
        "chosen": "EXP-LAPTOP-02",
        "chosen_label": "Winner Laptop",
        "literal_hero_asin": "EXP-LAPTOP-02",
        "signals": {"candidate_ids": ["EXP-LAPTOP-01", "EXP-LAPTOP-02"]},
        "provenance": {
            "trajectory_path": str(path),
            "trajectory_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    }


def test_preselection_signals_exclude_post_selection_content(tmp_path: Path) -> None:
    row = _trajectory_row(
        tmp_path,
        {
            "steps": [
                {
                    "action": "{'evaluate': {'code': \"fetch('/s?q=laptop&page=2')\"}}",
                    "reasoning": "Compare EXP-LAPTOP-01 and EXP-LAPTOP-02; the second is the final winner.",
                    "url": "/s?q=laptop&page=2",
                },
                {
                    "action": "{'click': {'interacted_element': \"ax_name='Add to Cart'\"}}",
                    "reasoning": "Add Winner Laptop.",
                    "url": "/dp/EXP-LAPTOP-02",
                },
                {
                    "action": "invalid tool action",
                    "reasoning": "A fetch failed and a specification is unresolved.",
                    "url": "/gp/cart",
                },
            ]
        },
    )
    result = stats.derive_preselection_signals(row)
    assert result["selection_step_position"] == 1
    assert result["preselection_step_count"] == 0
    assert result["action_unreliability_proxy"] is False
    assert result["unknown_to_acceptable_proxy"] is False


def test_todo_cart_language_is_not_mistaken_for_cart_commitment(tmp_path: Path) -> None:
    row = _trajectory_row(
        tmp_path,
        {
            "steps": [
                {
                    "action": "{'write_file': {'file_name': 'todo.md', 'content': 'Add to cart later'}}",
                    "reasoning": "I will compare candidates first and verify cart identity and total.",
                    "url": "/s?q=laptop",
                },
                {
                    "action": "{'click': {'interacted_element': \"ax_name='Add to Cart'\"}}",
                    "reasoning": "Winner Laptop is my final choice.",
                    "url": "/dp/EXP-LAPTOP-02",
                },
            ]
        },
    )
    result = stats.derive_preselection_signals(row)
    assert result["selection_step_position"] == 2
    assert result["preselection_step_count"] == 1
    assert result["todo_or_plan_file"] is True
    assert result["transaction_safeguard_intent"] is True
    assert result["transaction_drift_risk_proxy"] is False


def test_lpm_recovers_adjusted_binary_risk_difference() -> None:
    # Balanced strata: signal adds exactly 1.0 to the conditional mean.
    y = np.asarray([0, 0, 1, 1, 0, 0, 1, 1], dtype=float)
    group = np.asarray([0, 0, 0, 0, 1, 1, 1, 1], dtype=float)
    signal = np.asarray([0, 0, 1, 1, 0, 0, 1, 1], dtype=float)
    x = np.column_stack((np.ones(len(y)), group, signal))
    fit = stats.fit_lpm(y, x, ["intercept", "group", "signal"])
    assert fit.beta[-1] == pytest.approx(1.0)
    assert fit.rank == 3


def test_two_way_bootstrap_is_seed_deterministic() -> None:
    y = np.asarray([0, 1, 0, 1, 0, 1, 0, 1], dtype=float)
    signal = np.asarray([0, 1, 0, 1, 0, 1, 0, 1], dtype=float)
    x = np.column_stack((np.ones(len(y)), signal))
    models = ["a", "a", "a", "a", "b", "b", "b", "b"]
    scenarios = ["x", "x", "y", "y", "x", "x", "y", "y"]
    first = stats._cluster_bootstrap_coefficients(
        y, x, ["intercept", "signal"], models, scenarios, [1], reps=100, seed=11
    )
    second = stats._cluster_bootstrap_coefficients(
        y, x, ["intercept", "signal"], models, scenarios, [1], reps=100, seed=11
    )
    assert np.array_equal(first, second)


def test_holm_adjustment_is_monotone_in_sorted_order() -> None:
    adjusted = stats.holm_adjust({"a": 0.01, "b": 0.03, "c": 0.04, "missing": None})
    assert adjusted == {"a": pytest.approx(0.03), "b": pytest.approx(0.06), "c": pytest.approx(0.06), "missing": None}


def test_exact_mcnemar_counts_discordant_pairs() -> None:
    result = stats._exact_mcnemar([0, 0, 1, 1], [1, 1, 1, 0])
    assert result["baseline_0_treatment_1"] == 2
    assert result["baseline_1_treatment_0"] == 1
    assert result["discordant_pairs"] == 3
    assert result["exact_two_sided_p"] == 1.0


def test_full_census_build_has_exact_v19_pairs_and_crossed_cells() -> None:
    repo = Path(__file__).resolve().parents[1]
    report, crossed = stats.build_report(
        repo / "results" / "mixed_method_trajectory_analysis" / "runs.json",
        bootstrap_reps=100,
    )
    assert report["associations"]["n"] == 1677
    assert report["v19"]["pair_count"] == 30
    assert {item["validation_status"] for item in report["associations"]["single_proxy_models"]} == {
        "UNVALIDATED"
    }
    assert len(crossed) == 14 * 5 * 4 * 2
