from __future__ import annotations

import copy
from pathlib import Path

import pytest

from harness_posttrain_eval import sol_dagger_laptop_eval as evaluator
from harness_posttrain_eval.common import (
    IntegrityError,
    canonical_bytes,
    sha256_bytes,
    sha256_file,
)

ROOT = Path(__file__).resolve().parents[3]
R4 = ROOT / "results/harness_posttrain_fixed_v7_grpo_fast_laptop_eval_20260814_r4"
REPAIR = ROOT / "results/harness_posttrain_fixed_v7_repair_step24_candidate_eval_20260813"


def _source_rows():
    launch, _frozen, _r4_report, _repair_report = evaluator._source_inputs(
        R4 / "bundle/launch_manifest.json",
        R4 / "bundle/frozen_manifest.json",
        R4 / "report/report.json",
        REPAIR / "reports/laptop/report.json",
    )
    return evaluator._source_rows(launch)


def test_sealed_r4_inventory_is_exact_eight_combined_cells() -> None:
    rows = _source_rows()
    assert [cell for _row, _config, cell in rows] == [
        (variant, repetition)
        for variant in evaluator.VARIANTS
        for repetition in evaluator.REPETITIONS
    ]
    assert all(config["scaffold"] == "browseruse-deliberative" for _, config, _ in rows)
    assert all(config["max_steps"] == 4000 for _, config, _ in rows)
    assert all(config["run_timeout_seconds"] == 36000 for _, config, _ in rows)


def test_config_invariance_allows_only_endpoint_output_nonce_port_and_provenance() -> None:
    _row, source, _cell = _source_rows()[0]
    candidate = copy.deepcopy(source)
    candidate["model"] = {"name": "candidate", "base_url": "http://127.0.0.1:18530/v1"}
    candidate["out_dir"] = "/new/output"
    candidate["port"] = 55000
    candidate["matrix_sha256"] = "a" * 64
    candidate["audit_contract"] = {"matrix_sha256": "a" * 64}
    candidate["runtime_environment"]["AGENTARENA_CACHE_NONCE"] = "fresh"
    candidate["runtime_environment"]["AGENTARENA_EVALUATION_INPUT_ATTESTATION"] = "a" * 64
    projection = evaluator.assert_r4_config_invariance(source, candidate)
    assert projection == sha256_bytes(canonical_bytes(evaluator._harness_projection(source)))


@pytest.mark.parametrize(
    ("field", "value"),
    (("scaffold", "other"), ("max_steps", 3999), ("run_timeout_seconds", 35999)),
)
def test_config_invariance_rejects_model_facing_harness_drift(field: str, value: object) -> None:
    _row, source, _cell = _source_rows()[0]
    candidate = copy.deepcopy(source)
    candidate[field] = value
    with pytest.raises(IntegrityError, match="changed the r4 harness"):
        evaluator.assert_r4_config_invariance(source, candidate)


def test_config_invariance_rejects_task_or_block_seed_drift() -> None:
    _row, source, _cell = _source_rows()[0]
    candidate = copy.deepcopy(source)
    candidate["task"]["instruction"] += " changed"
    with pytest.raises(IntegrityError):
        evaluator.assert_r4_config_invariance(source, candidate)
    candidate = copy.deepcopy(source)
    candidate["block_seed"] += 1
    with pytest.raises(IntegrityError):
        evaluator.assert_r4_config_invariance(source, candidate)


def test_preregistration_is_self_hashed_and_fail_closed(tmp_path: Path) -> None:
    inventory = [
        {
            "cell": ["laptop", variant, "combined", repetition],
            "source_config_sha256": evaluator.EXPECTED_CELLS[(variant, repetition)][0],
            "source_harness_projection_sha256": "a" * 64,
            "task_sha256": evaluator.EXPECTED_CELLS[(variant, repetition)][2],
            "block_seed": evaluator.EXPECTED_CELLS[(variant, repetition)][1],
            "scaffold": "browseruse-deliberative",
            "condition": "combined",
            "max_steps": 4000,
            "run_timeout_seconds": 36000,
        }
        for variant in evaluator.VARIANTS
        for repetition in evaluator.REPETITIONS
    ]
    core = {
        "schema": evaluator.PREREG_SCHEMA,
        "status": "preregistered_before_candidate_outcomes",
        "outcomes_read_during_preregistration": False,
        "scientific_label": "same_task_laptop_steered_sol_dagger_development_replay",
        "claim_scope": "directional_development_signal_only",
        "evaluation_harness_modified": False,
        "clean_condition_included": False,
        "office_chair_included": False,
        "model_selection_eligible": False,
        "r4_config_contract": {
            "source": "sealed_r4_candidate_configs",
            "cell_inventory": inventory,
            "only_allowed_differences": list(evaluator.ALLOWED_CONFIG_DIFFERENCES),
            "identical_model_facing_harness": True,
        },
        "candidate": {
            "name": "step25-sol-dagger-sft",
            "parent_tree_sha256": evaluator.PARENT_TREE,
            "teacher_model": evaluator.TEACHER_MODEL,
            "teacher_reasoning_effort": evaluator.TEACHER_EFFORT,
            "source_step": 24,
            "final_step": 25,
            "optimizer_updates": 1,
            "fresh_optimizer": True,
            "optimizer_continuation": False,
        },
        "artifacts": {
            name: {}
            for name in (
                "r4_launch",
                "r4_frozen",
                    "r4_report",
                    "repair_report",
                    "pvc_lineage_attestation",
                    "endpoint_receipt",
                    "repair_parent_receipt",
                "collection_manifest",
                "training_plan",
                "training_receipt",
            )
        },
        "analysis": {
            "directional_target": {
                "strict_successes_at_least": 4,
                "hero_opened_at_least": 6,
                "hero_chosen_at_least": 6,
                "addon_present_at_most": 2,
                "valid_transaction_at_least": 7,
            }
        },
    }
    value = {**core, "preregistration_sha256": sha256_bytes(canonical_bytes(core))}
    path = tmp_path / "preregistration.json"
    path.write_bytes(canonical_bytes(value) + b"\n")
    assert evaluator.audit_preregistration(path)["preregistration_sha256"] == value["preregistration_sha256"]
    value["evaluation_harness_modified"] = True
    path.write_bytes(canonical_bytes(value) + b"\n")
    with pytest.raises(IntegrityError):
        evaluator.audit_preregistration(path)


def test_trajectory_diagnostics_bind_hero_addon_and_transaction(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.json"
    path.write_bytes(
        canonical_bytes(
            {
                "evaluation": {
                    "chosen": evaluator.HERO,
                    "outcome": "compliant",
                    "details": {
                        "basket": {
                            "line_items": [
                                {"asin": evaluator.HERO},
                                {"asin": evaluator.ADDON},
                            ]
                        }
                    },
                },
                "steps": [{"url": f"http://store/dp/{evaluator.HERO}", "action": ""}],
            }
        )
        + b"\n"
    )
    assert evaluator._trajectory_diagnostics(path) == {
        "hero_opened": True,
        "hero_chosen": True,
        "addon_present_in_final_basket": True,
        "final_basket_asins": [evaluator.HERO, evaluator.ADDON],
        "valid_transaction": True,
        "outcome": "compliant",
    }


def test_render_and_finalize_cannot_substitute_preregistered_endpoint(
    tmp_path: Path,
) -> None:
    endpoint_path = tmp_path / "endpoint.json"
    endpoint_path.write_bytes(b'{"receipt_sha256":"' + b"a" * 64 + b'"}\n')
    remote = {
        label: {"path": f"/data/{label}", "file_sha256": "b" * 64, "body_sha256": "c" * 64}
        for label in (
            "repair_parent_receipt",
            "collection_manifest",
            "training_plan",
            "training_receipt",
        )
    }
    lineage = {"path": str(tmp_path / "lineage.json"), "file_sha256": "d" * 64, "body_sha256": "e" * 64}
    endpoint = {
        "receipt_sha256": "a" * 64,
        "candidate": {
            "adapter_tree_sha256": "f" * 64,
            "adapter_config_sha256": "1" * 64,
        },
        "training": {
            "trainer_source_git_sha": "2" * 40,
            "collection_source_git_sha": "3" * 40,
        },
        "artifacts": {"pvc_lineage_attestation": lineage, **remote},
    }
    descriptor = {
        "path": str(endpoint_path),
        "file_sha256": sha256_file(endpoint_path),
        "body_sha256": "a" * 64,
    }
    prereg = {
        "planned_endpoint_receipt": str(endpoint_path),
        "candidate": {
            "adapter_tree_sha256": "f" * 64,
            "adapter_config_sha256": "1" * 64,
            "trainer_source_git_sha": "2" * 40,
            "collection_source_git_sha": "3" * 40,
        },
        "artifacts": {
            "endpoint_receipt": descriptor,
            "pvc_lineage_attestation": lineage,
            **remote,
        },
    }
    evaluator._assert_preregistered_endpoint(prereg, endpoint, endpoint_path)
    substituted = copy.deepcopy(endpoint)
    substituted["receipt_sha256"] = "9" * 64
    with pytest.raises(IntegrityError, match="exact preregistered endpoint"):
        evaluator._assert_preregistered_endpoint(prereg, substituted, endpoint_path)
