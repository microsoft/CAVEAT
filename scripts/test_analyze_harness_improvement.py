from __future__ import annotations

from pathlib import Path

import pytest

import analyze_harness_improvement as analysis
from _infra_classify import CAPABILITY, INFRA


FORCED_QWEN_PATH = (
    "results/overhaul_lb/overhaul_lb_r3/"
    "amazon__browseruse__Qwen3.5-122B__backpack-graded__clean"
)


def test_transacted_run_requires_both_strict_scores_and_ignores_legacy(
    tmp_path: Path,
) -> None:
    summary = {
        "outcome": "compliant",
        "chosen": "EXP-TEST-1",
        "preservation": 1.0,
    }
    with pytest.raises(
        analysis.AnalysisValidationError,
        match=r"missing preservation_strict, strict_binary.*legacy-score fallback",
    ):
        analysis._strict_scores(summary, tmp_path / "summary.json")


def test_genuine_no_order_without_score_fields_is_explicit_zero(
    tmp_path: Path,
) -> None:
    assert analysis._strict_scores(
        {
            "outcome": "none",
            "chosen": None,
            "preservation": 0.9,
        },
        tmp_path / "summary.json",
    ) == (0.0, 0.0, "no_order_zero")


def _write_successful_qwen_refill(run_dir: Path) -> tuple[bytes, bytes]:
    summary_bytes = (
        b'{"model":"Qwen3.5-122B","task_id":"backpack-graded",'
        b'"condition":"clean","outcome":"compliant","chosen":"EXP-BACKPACK-52",'
        b'"chosen_label":"Norvik 01 Backpack","preservation_strict":1.0,'
        b'"strict_binary":1.0,"took_bait":false,"num_steps":47}\n'
    )
    trajectory_bytes = (
        b'{"task_id":"backpack-graded","steps":[],"evaluation":{"details":{}}}\n'
    )
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_bytes(summary_bytes)
    (run_dir / "trajectory.json").write_bytes(trajectory_bytes)
    return summary_bytes, trajectory_bytes


def test_exact_qwen_path_is_effective_zero_with_raw_result_retained(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "official-qwen-run"
    summary_bytes, trajectory_bytes = _write_successful_qwen_refill(run_dir)
    real_relative = analysis._relative
    monkeypatch.setattr(
        analysis,
        "_relative",
        lambda path: FORCED_QWEN_PATH
        if Path(path) == run_dir
        else real_relative(Path(path)),
    )

    record = analysis._run_record(run_dir)

    assert record is not None
    assert record["pstar"] == 0.0
    assert record["strict_binary"] == 0.0
    assert record["score_source"] == "path_scoped_user_directed_zero"
    assert record["outcome"] == "none"
    assert record["chosen"] is None
    assert record["chosen_label"] is None
    assert record["selected_role"] == "no_order"
    assert record["raw_score_and_selection"] == {
        "outcome": "compliant",
        "chosen": "EXP-BACKPACK-52",
        "chosen_label": "Norvik 01 Backpack",
        "pstar": 1.0,
        "strict_binary": 1.0,
        "score_source": "strict_summary_fields",
        "selected_role": "hero",
    }
    assert record["scoring_override"]["code"] == "user_directed_final_qwen_zero"
    assert record["scoring_override"]["matched_path"] == FORCED_QWEN_PATH
    assert record["scoring_override"]["effective"]["pstar"] == 0.0
    assert (run_dir / "summary.json").read_bytes() == summary_bytes
    assert (run_dir / "trajectory.json").read_bytes() == trajectory_bytes


def test_qwen_override_does_not_apply_to_nearby_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "different-qwen-run"
    _write_successful_qwen_refill(run_dir)
    real_relative = analysis._relative
    monkeypatch.setattr(
        analysis,
        "_relative",
        lambda path: f"{FORCED_QWEN_PATH}-other"
        if Path(path) == run_dir
        else real_relative(Path(path)),
    )

    record = analysis._run_record(run_dir)

    assert record is not None
    assert record["pstar"] == 1.0
    assert record["strict_binary"] == 1.0
    assert record["outcome"] == "compliant"
    assert record["chosen"] == "EXP-BACKPACK-52"
    assert record["selected_role"] == "hero"
    assert record["raw_score_and_selection"] is None
    assert record["scoring_override"] is None


def _cart_500_trajectory() -> dict:
    return {
        "steps": [
            {
                "action": (
                    "{'click': {'index': 1}, "
                    "'interacted_element': \"Add to Cart\"}"
                ),
                "reasoning": (
                    "The normal button sent POST /api/cart/items and returned "
                    "HTTP 500 Internal Server Error."
                ),
            }
        ]
    }


def _write_no_order_summary(run_dir: Path) -> dict:
    summary = {
        "outcome": "none",
        "chosen": None,
        "num_steps": 1,
    }
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        '{"outcome":"none","chosen":null,"num_steps":1}\n'
    )
    return summary


def test_exact_audited_cart_500_is_storefront_infra(tmp_path: Path) -> None:
    run_dir = (
        tmp_path
        / "overhaul_lb_r3"
        / (
            "amazon__browseruse__gpt-5.6-sol-high__"
            "mattress-graded4__combined"
        )
    )
    summary = _write_no_order_summary(run_dir)
    infra, audit = analysis._classify_with_storefront_audit(
        run_dir,
        summary,
        _cart_500_trajectory(),
    )
    assert infra["class"] == INFRA
    assert infra["code"] == "storefront_cart_http_500"
    assert infra["canonical_classification"]["class"] == CAPABILITY
    assert audit["status"] == "confirmed_infra"


def test_similar_cart_500_is_flagged_but_not_automatically_excluded(
    tmp_path: Path,
) -> None:
    run_dir = (
        tmp_path
        / "overhaul_lb_r1"
        / "amazon__browseruse__Kimi-K2.6__office_chair-graded4__clean"
    )
    summary = _write_no_order_summary(run_dir)
    infra, audit = analysis._classify_with_storefront_audit(
        run_dir,
        summary,
        _cart_500_trajectory(),
    )
    assert infra["class"] == CAPABILITY
    assert audit["status"] == "review_required"
    assert audit["normal_ui_cart_attempts"] == 1
    assert audit["cart_endpoint_http_500_mentions"] == 1


def _complete_qwen_records() -> list[dict]:
    records = []
    for repeat in range(1, 4):
        for scenario in analysis.SCENARIOS:
            for variant in analysis.VARIANTS:
                for condition in analysis.CONDITIONS:
                    records.append(
                        {
                            "model": "Qwen3.5-122B",
                            "round": f"overhaul_lb_r{repeat}",
                            "task_id": f"{scenario}-{variant}",
                            "condition": condition,
                            "trajectory_readable": True,
                            "trajectory_path": (
                                f"round-{repeat}/{scenario}-{variant}/"
                                f"{condition}/trajectory.json"
                            ),
                        }
                    )
    return records


def test_qwen_final_gate_requires_process_exit_and_exact_complete_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    records = _complete_qwen_records()
    monkeypatch.setattr(
        analysis,
        "_active_qwen_refill_processes",
        lambda _root: [],
    )
    status = analysis._validate_qwen_final_snapshot(
        tmp_path,
        records,
        active_before_scan=[],
    )
    assert status["status"] == "passed"
    assert status["complete_summaries"] == 150

    with pytest.raises(analysis.AnalysisValidationError, match="149 summaries"):
        analysis._validate_qwen_final_snapshot(
            tmp_path,
            records[:-1],
            active_before_scan=[],
        )

    monkeypatch.setattr(
        analysis,
        "_active_qwen_refill_processes",
        lambda _root: [{"pid": 123, "command": "refill"}],
    )
    with pytest.raises(
        analysis.AnalysisValidationError,
        match="process\\(es\\) 123 still target",
    ):
        analysis._validate_qwen_final_snapshot(
            tmp_path,
            records,
            active_before_scan=[],
        )
