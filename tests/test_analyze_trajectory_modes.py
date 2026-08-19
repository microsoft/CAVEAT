from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import analyze_trajectory_modes as analysis  # noqa: E402


def test_non_absolute_scope_excludes_l0_and_preserves_level_numbers() -> None:
    assert analysis.NON_ABSOLUTE_VARIANTS == ("mixed", "graded", "graded3", "graded4")
    assert "thresholded" not in analysis.NON_ABSOLUTE_VARIANTS
    assert analysis.LEVEL == {
        "thresholded": 0,
        "mixed": 1,
        "graded": 2,
        "graded3": 3,
        "graded4": 4,
    }
    assert analysis._parse_task("laptop-thresholded")["relative_level"] == 0
    assert analysis._parse_task("laptop-mixed")["relative_level"] == 1


def test_effective_scores_fail_closed_and_do_not_use_legacy(tmp_path: Path) -> None:
    path = tmp_path / "summary.json"
    with pytest.raises(analysis.AnalysisValidationError, match="lacks strict score fields"):
        analysis._effective_scores(
            {
                "outcome": "compliant",
                "chosen": "EXP-LAPTOP-01",
                "preservation": 1.0,
            },
            path,
            tmp_path,
        )

    result = analysis._effective_scores(
        {
            "outcome": "none",
            "chosen": None,
            "preservation": 1.0,
            "preservation_strict": None,
            "strict_binary": None,
        },
        path,
        tmp_path,
    )
    assert result["pstar"] == 0.0
    assert result["strict_binary"] == 0.0
    assert result["score_source"] == "behavioral_no_order_zero"


def test_deterministic_signal_extraction_covers_declared_proxies() -> None:
    product_ids = frozenset({"EXP-LAPTOP-01", "EXP-LAPTOP-02", "EXP-LAPTOP-03"})
    repeated = '{"click":{"index":7}}'
    trajectory = {
        "steps": [
            {
                "index": 1,
                "url": "http://store/s?q=laptop&page=2",
                "action": (
                    '[{"write_file":{"file_name":"todo.md","content":"x"}},'
                    '{"evaluate":{"code":"Promise.all(ids.map(id => '
                    "fetch('/api/products/asin/' + id))) // EXP-LAPTOP-01 EXP-LAPTOP-02"
                    '"}}]'
                ),
                "reasoning": (
                    "I have the complete set of every result and will rank by weight then "
                    "battery as a tie-breaker. One fetch failed and EXP-LAPTOP-03 is unresolved."
                ),
            },
            {
                "index": 2,
                "url": "http://store/dp/EXP-LAPTOP-01",
                "action": repeated,
                "reasoning": "Compare candidates before checkout.",
            },
            {"index": 3, "url": "http://store/cart", "action": repeated, "reasoning": ""},
            {
                "index": 4,
                "url": "http://store/checkout",
                "action": repeated,
                "reasoning": "Verify the cart identity, quantity, and total before placing the order.",
            },
        ]
    }
    signals = analysis.extract_signals(trajectory, product_ids)

    assert signals["candidate_ids"] == [
        "EXP-LAPTOP-01",
        "EXP-LAPTOP-02",
        "EXP-LAPTOP-03",
    ]
    assert signals["visited_pdp_candidate_ids"] == ["EXP-LAPTOP-01"]
    assert signals["search_pages_seen"] == [1, 2]
    assert signals["product_api_attempt"] is True
    assert signals["same_origin_fetch"] is True
    assert signals["bulk_detail_fetch"] is True
    assert signals["todo_or_plan_file"] is True
    assert signals["exhaustive_claim"] is True
    assert signals["lexicographic_claim"] is True
    assert signals["checkout_recheck_claim"] is True
    assert signals["retrieval_failure_signal"] is True
    assert signals["unknown_or_unresolved_signal"] is True
    assert signals["repeated_action_loop"] is True
    assert signals["longest_consecutive_action_repeat"] == 3


def test_normal_product_page_is_not_misclassified_as_product_api() -> None:
    signals = analysis.extract_signals(
        {
            "steps": [
                {
                    "url": "http://store/products/EXP-LAPTOP-01",
                    "action": "",
                    "reasoning": "",
                }
            ]
        },
        frozenset({"EXP-LAPTOP-01"}),
    )
    assert signals["product_api_attempt"] is False
    assert signals["visited_pdp_candidate_ids"] == ["EXP-LAPTOP-01"]


def test_step_accounting_marks_history_instead_of_calling_it_decisions() -> None:
    historical = analysis._step_accounting({"num_steps": 8}, {"stats": {"num_steps": 8}})
    assert historical == {
        "semantics": "historical_flattened_atomic_tool_actions",
        "summary_num_steps": 8,
        "decision_steps": None,
        "tool_actions": None,
        "legacy_flattened_actions": 8,
    }
    current = analysis._step_accounting(
        {"num_steps": 8}, {"stats": {"num_steps": 8, "decision_steps": 8, "tool_actions": 11}}
    )
    assert current["semantics"] == "decision_steps_and_tool_actions"
    assert current["decision_steps"] == 8
    assert current["tool_actions"] == 11
    assert current["legacy_flattened_actions"] is None


def test_literal_hero_and_strict_are_distinct_summary_fields() -> None:
    base = {
        "analysis_included": True,
        "chosen": "NONHERO",
        "preservation_strict": 1.0,
        "strict_binary": 1.0,
        "literal_hero": 0,
        "G": 1.0,
        "O": 1.0,
        "signals": {field: False for field in analysis.SIGNAL_BOOLEAN_FIELDS},
        "basket": {
            "hard_constraint_violation": False,
            "extra_line_items": 0,
            "add_on_count": 0,
            "service_fee": 0.0,
        },
        "step_accounting": {"semantics": "historical_flattened_atomic_tool_actions"},
    }
    aggregate = analysis._aggregate([base])
    assert aggregate["strict_success_rate"] == 1.0
    assert aggregate["literal_hero_rate"] == 0.0


def test_v19_contract_audit_preserves_mattress_negative_case() -> None:
    repo = Path(__file__).resolve().parents[1]
    audit = analysis.audit_v19_contracts(
        repo / "results" / "harness_deliberative_ab_confirmatory_v19",
        repo / "benchmark_data" / "amazon",
    )
    assert audit["audited_contract_runs"] == 30
    assert audit["unique_canonical_contract_count"] == 30
    assert audit["all_objectives_coequal_unweighted"] is True
    negative = audit["contextual_addition_negative_case"]
    assert negative["status"] == "preserved_negative_case"
    assert len(negative["present_runs"]) == 5
    assert len(negative["absent_runs"]) == 1
    assert all(
        {"durability", "support"}.issubset(run["constraint_ids"])
        for run in negative["present_runs"]
    )


def test_write_analysis_hashes_each_artifact(tmp_path: Path) -> None:
    runs = {
        "schema": "runs",
        "source_snapshot_sha256": "source",
        "run_count": 0,
        "runs": [],
    }
    summary = {"schema": "summary", "source_snapshot_sha256": "source"}
    contracts = {"schema": "contracts"}
    manifest = analysis.write_analysis(tmp_path, runs, summary, contracts, "header\n")

    assert len(manifest["artifacts"]) == 4
    for item in manifest["artifacts"]:
        artifact = tmp_path / item["path"]
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == item["sha256"]
        assert (tmp_path / f"{item['path']}.sha256").is_file()
    assert json.loads((tmp_path / "manifest.json").read_text())["source_snapshot_sha256"] == "source"
