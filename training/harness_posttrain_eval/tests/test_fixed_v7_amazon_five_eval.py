from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from harness_posttrain_eval.common import IntegrityError, canonical_bytes
from harness_posttrain_eval.fixed_v7_amazon_five_eval import (
    ANALYSIS_SPEC,
    CONDITIONS,
    CONTROL_RUN_COUNT,
    PAIR_COUNT,
    SCENARIOS,
    VARIANTS,
    _candidate_id,
    _freeze_core,
    _pairs,
    _status,
    audit_bundle,
    audit_freeze,
    parser,
)


def _source_rows() -> SimpleNamespace:
    rows = []
    for scenario in SCENARIOS:
        for variant in VARIANTS:
            for condition, repetitions in CONDITIONS.items():
                for repetition in range(repetitions):
                    pair = (
                        f"final::{scenario}::{variant}::{condition}::r{repetition:02d}"
                    )
                    for arm in ("base", "trained"):
                        rows.append(
                            {
                                "run_id": f"{pair}::{arm}",
                                "pair_id": pair,
                                "arm": arm,
                                "scenario": scenario,
                                "variant": variant,
                                "condition": condition,
                                "repetition": repetition,
                                "block_seed": repetition + 100,
                                "task_id": f"{scenario}-{variant}",
                            }
                        )
    return SimpleNamespace(matrix={"runs": rows})


def test_canonical_pair_inventory_is_exact_and_balanced() -> None:
    pairs = _pairs(_source_rows())
    assert len(pairs) == PAIR_COUNT
    assert 2 * len(pairs) == CONTROL_RUN_COUNT
    assert {
        (raw["scenario"], raw["variant"], raw["condition"]) for raw, _ in pairs
    } == {
        (scenario, variant, condition)
        for scenario in SCENARIOS
        for variant in VARIANTS
        for condition in CONDITIONS
    }


def test_pair_inventory_rejects_a_missing_control() -> None:
    source = _source_rows()
    source.matrix["runs"].pop()
    with pytest.raises(IntegrityError, match="incomplete"):
        _pairs(source)


def test_candidate_ids_preserve_the_exact_cell() -> None:
    run_id, pair_id = _candidate_id(
        {
            "scenario": "office_chair",
            "variant": "mixed",
            "condition": "combined",
            "repetition": 4,
        }
    )
    assert pair_id == "fixed_v7_final::office_chair::mixed::combined::r04"
    assert run_id == f"{pair_id}::fixed_v7"


def test_analysis_has_no_score_gate_or_retuning() -> None:
    assert ANALYSIS_SPEC["decision_policy"] == {
        "score_gate": None,
        "candidate_selection_from_scores": False,
        "retuning_from_scores": False,
        "retraining_from_scores": False,
        "early_stopping_from_scores": False,
        "launch_all_160_if_infrastructure_valid": True,
    }
    assert ANALYSIS_SPEC["canonical_cells"]["pair_count"] == 160
    assert ANALYSIS_SPEC["canonical_cells"]["control_run_count"] == 320


def test_cli_has_no_development_report_or_score_gate_argument() -> None:
    value = parser()
    help_text = value.format_help()
    assert "gate-report" not in help_text
    render = value.parse_args(
        [
            "render",
            "--repository-root",
            "/repo",
            "--source-preparation",
            "/controls",
            "--endpoint-receipt",
            "/endpoint",
            "--evaluation-root",
            "/evaluation",
            "--evaluator-git-sha",
            "a" * 40,
            "--freeze",
            "/freeze",
            "--base-port",
            "40000",
            "--python-executable",
            "/python",
        ]
    )
    assert not hasattr(render, "gate_report")


def test_executor_status_requires_all_160_clean_runs(tmp_path: Path) -> None:
    launches = [{"run_id": f"candidate-{index}"} for index in range(PAIR_COUNT)]
    launch = {"launch_manifest_sha256": "1" * 64, "launches": launches}
    status = {
        "launch_manifest_sha256": "1" * 64,
        "success": True,
        "counts": {"complete": PAIR_COUNT},
        "runs": {
            row["run_id"]: {
                "state": "complete",
                "exit_code": 0,
                "reason": None,
            }
            for row in launches
        },
    }
    path = tmp_path / "status.json"
    path.write_bytes(canonical_bytes(status) + b"\n")
    _status(path, launch)
    status["runs"]["candidate-42"]["reason"] = "infra"
    path.write_bytes(canonical_bytes(status) + b"\n")
    with pytest.raises(IntegrityError, match="160 clean runs"):
        _status(path, launch)


def test_freeze_core_declares_outcome_blind_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = SimpleNamespace(
        preparation={"preparation_sha256": "2" * 64},
        frozen_manifest={"manifest_sha256": "3" * 64},
        matrix={"matrix_sha256": "4" * 64, "runs": []},
        launch_manifest={"launch_manifest_sha256": "5" * 64},
        launches={},
    )
    monkeypatch.setattr(
        "harness_posttrain_eval.fixed_v7_amazon_five_eval._source",
        lambda *_: (source, {}),
    )
    monkeypatch.setattr(
        "harness_posttrain_eval.fixed_v7_amazon_five_eval._endpoint",
        lambda *_: {
            "candidate": {"composite_sha256": "6" * 64},
            "model_spec": {"name": "candidate"},
        },
    )
    monkeypatch.setattr(
        "harness_posttrain_eval.fixed_v7_amazon_five_eval._contracts",
        lambda *_: [{"candidate_run_id": "candidate"}],
    )
    value = _freeze_core(
        source_preparation=tmp_path / "source",
        repository_root=tmp_path,
        endpoint_path=tmp_path / "endpoint.json",
        output_path=tmp_path / "freeze.json",
        evaluator_git_sha="a" * 40,
        evaluation_root=tmp_path / "evaluation",
    )
    assert value["outcome_blind"] is True
    assert value["outcome_content_read_during_freeze"] is False
    assert value["development_score_gate"] is None
    assert value["scientific_invariants"]["all_cells_launch_without_score_gate"]
    assert json.loads(canonical_bytes(value))["analysis_spec"] == ANALYSIS_SPEC


def test_real_immutable_320_control_counterfactual(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[3]
    source_path = (
        repository
        / "results/harness_posttrain_final_eval_corrected_20260812/preparation"
    )
    endpoint_path = (
        repository / "results/harness_posttrain_fixed_v7_eval_20260813/orchestration/"
        "runtime_next_iteration/repair_step24/endpoint_receipt.json"
    )
    if not source_path.is_dir() or not endpoint_path.is_file():
        pytest.skip("immutable campaign artifacts are unavailable")
    from harness_posttrain_eval import fixed_v7_amazon_five_eval as campaign

    freeze_path = tmp_path / "freeze.json"
    evaluation_root = tmp_path / "evaluation"
    git_sha = "a" * 40
    campaign.freeze(
        SimpleNamespace(
            repository_root=repository,
            source_preparation=source_path,
            endpoint_receipt=endpoint_path,
            evaluation_root=evaluation_root,
            evaluator_git_sha=git_sha,
            output=freeze_path,
        )
    )
    frozen = audit_freeze(
        freeze_path,
        source_preparation=source_path,
        repository_root=repository,
        endpoint_path=endpoint_path,
        evaluator_git_sha=git_sha,
        evaluation_root=evaluation_root,
    )
    assert len(frozen["cell_contracts"]) == PAIR_COUNT
    campaign.render(
        SimpleNamespace(
            repository_root=repository,
            source_preparation=source_path,
            endpoint_receipt=endpoint_path,
            evaluation_root=evaluation_root,
            evaluator_git_sha=git_sha,
            freeze=freeze_path,
            base_port=45000,
            python_executable=str(repository / ".venv/bin/python"),
        )
    )
    bundle = audit_bundle(
        evaluation_root,
        freeze_path=freeze_path,
        source_preparation=source_path,
        repository_root=repository,
        endpoint_path=endpoint_path,
        evaluator_git_sha=git_sha,
        require_fresh_results=True,
    )
    assert bundle["control_run_count"] == CONTROL_RUN_COUNT
    assert bundle["candidate_run_count"] == PAIR_COUNT
    assert bundle["development_score_gate"] is None
