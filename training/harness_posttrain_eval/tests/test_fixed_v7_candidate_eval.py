from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from harness_posttrain_eval.common import (
    IntegrityError,
    canonical_bytes,
    sha256_bytes,
    sha256_file,
)
from harness_posttrain_eval.launcher import exact_lora_composite_sha256

from harness_posttrain_eval import fixed_v7_candidate_eval as evaluator


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SOURCE = ROOT / "results/harness_posttrain_final_eval_corrected_20260812/preparation"
PROTOCOL = (
    ROOT
    / "results/harness_posttrain_fixed_v7_eval_20260813/orchestration"
    / "fixed_v7_repair_step24_candidate_protocol.json"
)


def _write(path: Path, value: dict) -> Path:
    path.write_bytes(canonical_bytes(value) + b"\n")
    return path


def _self(value: dict, field: str) -> dict:
    core = {key: item for key, item in value.items() if key != field}
    return {**core, field: sha256_bytes(canonical_bytes(core))}


def _artifact(tmp_path: Path, name: str, value: dict, body_field: str) -> dict:
    value = _self(value, body_field)
    path = _write(tmp_path / f"{name}.json", value)
    return {
        "path": str(path),
        "file_sha256": sha256_file(path),
        "body_sha256": value[body_field],
    }


def _fanout() -> dict:
    inventory = []
    excluded = []
    for offset in range(16):
        variant = "graded" if offset < 8 else "mixed"
        ordinal = offset if offset < 8 else offset - 8
        sequence = f"amazon-r00-repair-repair-v2-7ceed-r1-{variant}-{ordinal}"
        row = {
            "sequence_id": sequence,
            "environment_nonce": f"amazon-r00-repair/repair-v2-7ceed-r1/{variant}/{ordinal}",
            "proxy_port": 34500 + offset,
        }
        reason = evaluator.EXPECTED_EXCLUDED_SEQUENCE_REASONS.get(sequence)
        if reason is None:
            inventory.append({**row, "task_variant": variant})
        else:
            excluded.append({**row, "variant": variant, "reason": reason})
    return {
        "schema": "harness-distill.amazon-r00-repair-fanout-receipt.v4",
        "status": "complete",
        "scientific_label": "same_task_laptop_r00_pooled_exact_target_repair_fanout",
        "run_id": "repair-v2-7ceed-r1",
        "executor_git_sha": evaluator.REPLAY_EXECUTOR_GIT_SHA,
        "validator_git_sha": evaluator.REPLAY_VALIDATOR_GIT_SHA,
        "max_concurrency": 16,
        "retry_count": 0,
        "topup_count": 0,
        "execution_count": 16,
        "valid_replay_count": 13,
        "excluded_execution_count": 3,
        "execution_variant_counts": dict(evaluator.EXPECTED_EXECUTION_VARIANT_COUNTS),
        "valid_variant_counts": dict(evaluator.EXPECTED_VALID_VARIANT_COUNTS),
        "excluded_sequence_reasons": dict(evaluator.EXPECTED_EXCLUDED_SEQUENCE_REASONS),
        "targets_per_variant_role_required": 2,
        "critical_role_variant_counts": evaluator.EXPECTED_ROLE_VARIANT_COUNTS,
        "critical_role_variant_unique_target_counts": evaluator.EXPECTED_ROLE_VARIANT_COUNTS,
        "base_port": 34500,
        "ports": list(range(34500, 34516)),
        "inventory": inventory,
        "excluded_executions": excluded,
        "laptop_r00_used": True,
        "laptop_r01_used": False,
        "office_chair_used": False,
    }


def _endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    alias = "qwen35-browser-action-fixed-v7-test-exact-lora"
    parent, adapter, config, tokenizer, template = (
        "1" * 64,
        "49e66d189603142232d0e4f1b3549d32b783ebed5fa4d3efd0e04af3197a5508",
        "3" * 64,
        "4" * 64,
        "5" * 64,
    )
    composite = exact_lora_composite_sha256(
        parent_tree_sha256=parent,
        adapter_tree_sha256=adapter,
        adapter_config_sha256=config,
        tokenizer_json_sha256=tokenizer,
        chat_template_sha256=template,
        dtype="bfloat16",
    )
    training_release = _artifact(
        tmp_path,
        "training_release",
        {
            "schema": "harness-posttrain.browser-action-next-iteration.one-update-sft-release.v1",
            "status": "released",
            "purpose": "one_update_sft_from_r00_correction_replay",
            "source": {"git_sha": evaluator.REPAIR_EXECUTOR_GIT_SHA},
            "laptop_r01_outcomes_read": False,
            "office_chair_outcomes_read": False,
        },
        "release_sha256",
    )
    training_receipt = _artifact(
        tmp_path,
        "training_receipt",
        {
            "schema": "harness-distill.amazon-r00-repair-sft-training-receipt.v1",
            "status": "ok",
            "executor_git_sha": evaluator.REPAIR_EXECUTOR_GIT_SHA,
            "replay_executor_git_sha": evaluator.REPLAY_EXECUTOR_GIT_SHA,
            "replay_validator_git_sha": evaluator.REPLAY_VALIDATOR_GIT_SHA,
            "fanout_receipt_sha256": evaluator.REPAIR_FANOUT_FILE_SHA256,
            "fanout_receipt_body_sha256": evaluator.REPAIR_FANOUT_BODY_SHA256,
            "dataset_manifest_sha256": "2d2c9b1eac32fea137cec1852b854a8d34a4edcfa8982ac07c5a4d58a7987230",
            "dataset_sha256": "4d614d80ae4db704b68118da00942b1584805e3e59a4a2cf0e1cee79117adc1c",
            "plan_file_sha256": "9dae94e2081d37e74ed89be7a06c8c2a054d09ff37cb139b12d232a0c8aae6e2",
            "plan_body_sha256": "fd5ffde22c4a6b587022f032cff629351ff88c0a930999d040a1802e964712fe",
            "scientific_label": "same_task_laptop_r00_real_state_cart_repair_sft",
            "source_step": 23,
            "final_step": 24,
            "optimizer_updates": 1,
            "learning_rate": 5e-7,
            "assistant_tokens_only": True,
            "candidate": {
                "name": "step24-amazon-r00-repair-sft",
                "update": 24,
                "tree_sha256": adapter,
                "adapter_config_sha256": config,
            },
            "final_dcp": {
                "tree_sha256": "018e4589edb0ee7dd95eb94511c6411458ba3d0c274fa8284fa17eaae10c2b35"
            },
            "laptop_r00_used": True,
            "laptop_r01_used": False,
            "office_chair_used": False,
        },
        "receipt_body_sha256",
    )
    fanout_receipt = _artifact(
        tmp_path,
        "fanout_receipt",
        _fanout(),
        "receipt_body_sha256",
    )
    training_receipt_path = Path(training_receipt["path"])
    training_receipt_value = json.loads(training_receipt_path.read_text())
    training_receipt_value["fanout_receipt_sha256"] = fanout_receipt["file_sha256"]
    training_receipt_value["fanout_receipt_body_sha256"] = fanout_receipt["body_sha256"]
    training_receipt_value = _self(
        {
            key: item
            for key, item in training_receipt_value.items()
            if key != "receipt_body_sha256"
        },
        "receipt_body_sha256",
    )
    _write(training_receipt_path, training_receipt_value)
    training_receipt["file_sha256"] = sha256_file(training_receipt_path)
    training_receipt["body_sha256"] = training_receipt_value["receipt_body_sha256"]
    serve_release = _artifact(
        tmp_path,
        "serve_release",
        {
            "schema": "harness-posttrain.browser-action-fixed-v7-repair-step24-serve-release.v1",
            "status": "released",
            "outcome_blind": True,
            "executor_git_sha": evaluator.REPAIR_EXECUTOR_GIT_SHA,
            "scientific_label": "same_task_laptop_r00_real_state_cart_repair_sft",
            "evaluation_label": "same_task_adaptation_replay",
            "training_receipt": {
                "file_sha256": training_receipt["file_sha256"],
                "body_sha256": training_receipt["body_sha256"],
            },
            "fanout_receipt": {
                "file_sha256": fanout_receipt["file_sha256"],
                "body_sha256": fanout_receipt["body_sha256"],
                "executor_git_sha": evaluator.REPLAY_EXECUTOR_GIT_SHA,
                "validator_git_sha": evaluator.REPLAY_VALIDATOR_GIT_SHA,
                "execution_count": 16,
                "valid_replay_count": 13,
                "excluded_execution_count": 3,
            },
            "candidate": {
                "name": "step24-amazon-r00-repair-sft",
                "update": 24,
                "adapter_config_sha256": config,
                "parent_tree_sha256": parent,
                "adapter_tree_sha256": adapter,
                "served_model_name": alias,
            },
            "scientific_flags": {
                "laptop_r00_used": True,
                "laptop_r01_used": False,
                "office_chair_used": False,
            },
        },
        "release_sha256",
    )
    core = {
        "schema": evaluator.ENDPOINT_SCHEMA,
        "status": "ok",
        "outcome_blind": True,
        "artifacts": {
            "training_release_descriptor": training_release,
            "training_receipt": training_receipt,
            "fanout_receipt": fanout_receipt,
            "serve_release_descriptor": serve_release,
        },
        "candidate": {
            "name": "step24-amazon-r00-repair-sft",
            "update": 24,
            "parent_tree_sha256": parent,
            "adapter_tree_sha256": adapter,
            "adapter_config_sha256": config,
            "tokenizer_json_sha256": tokenizer,
            "chat_template_sha256": template,
            "dtype": "bfloat16",
            "composite_sha256": composite,
            "served_model_name": alias,
        },
        "model_spec": {
            "name": alias,
            "deployment": alias,
            "provider": "openai",
            "api_key": evaluator.EXPECTED_MODEL_API_KEY,
            "base_url": evaluator.EXPECTED_MODEL_BASE_URL,
            "vision": False,
            "extra": {"frequency_penalty": None},
        },
        "training": {
            "job": "test-training",
            "job_uid": "training-uid",
            "job_spec_sha256": "6" * 64,
            "job_labels_sha256": "7" * 64,
            "node": "node-training",
            "image_id": "image-training",
            "pod": "test-training-master-0",
            "pod_uid": "training-pod-uid",
        },
        "runtime": {
            "job": "test-serve",
            "job_uid": "serve-job-uid",
            "job_spec_sha256": "8" * 64,
            "job_labels_sha256": "9" * 64,
            "pod": "test-serve-master-0",
            "pod_uid": "serve-pod-uid",
            "node": "node-serve",
            "image_id": "image-serve",
            "container_command": ["bash"],
            "container_args": ["serve"],
            "tunnel_argv": ["kubectl", "port-forward"],
            "tunnel_pid": 100,
            "tunnel_process_start_ticks": "101",
            "tunnel_supervisor_pid": 102,
            "tunnel_supervisor_process_start_ticks": "103",
            "tunnel_status_sha256": "a" * 64,
        },
        "api_evidence": {
            "models_file_sha256": "b" * 64,
            "canary_file_sha256": "c" * 64,
            "served_models": [alias],
            "finish_reason": "stop",
        },
        "labels": evaluator.LABELS,
    }
    endpoint = _self(core, "receipt_sha256")
    monkeypatch.setattr(
        evaluator,
        "REPAIR_TRAINING_RECEIPT_FILE_SHA256",
        training_receipt["file_sha256"],
    )
    monkeypatch.setattr(
        evaluator,
        "REPAIR_TRAINING_RECEIPT_BODY_SHA256",
        training_receipt["body_sha256"],
    )
    monkeypatch.setattr(
        evaluator, "REPAIR_FANOUT_FILE_SHA256", fanout_receipt["file_sha256"]
    )
    monkeypatch.setattr(
        evaluator, "REPAIR_FANOUT_BODY_SHA256", fanout_receipt["body_sha256"]
    )
    return _write(tmp_path / "endpoint_receipt.json", endpoint)


def _protocol(tmp_path: Path, endpoint: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    value = json.loads(PROTOCOL.read_text())
    value["endpoint_contract"]["path"] = str(endpoint)
    value = _self(
        {key: item for key, item in value.items() if key != "protocol_sha256"},
        "protocol_sha256",
    )
    path = _write(tmp_path / "protocol.json", value)
    monkeypatch.setattr(evaluator, "FROZEN_PROTOCOL_FILE_SHA256", sha256_file(path))
    return path


def test_frozen_protocol_is_exact_and_outcome_blind():
    value = evaluator.audit_protocol(PROTOCOL)
    assert value["outcome_blind_freeze"] is True
    assert value["outcomes_read_during_freeze"] is False
    assert len(value["mappings"]) == 24
    assert value["common_contract"]["max_concurrency"] == 12


def test_missing_controls_are_presence_only_and_exactly_identified(tmp_path: Path):
    value = evaluator.audit_protocol(PROTOCOL)
    missing = evaluator.missing_controls(value)
    assert [row["run_id"] for row in missing] == [
        "final::office_chair::graded3::combined::r01::trained",
        "final::office_chair::graded4::combined::r01::trained",
    ]
    assert all(
        row["missing_files"] == ["summary.json", "trajectory.json"] for row in missing
    )
    receipt = evaluator.write_missing_controls_receipt(
        protocol=value,
        output=tmp_path / "missing_controls.json",
        phase="test",
    )
    assert receipt["status"] == "blocked"
    assert receipt["outcome_content_read"] is False
    assert receipt["presence_only_audit"] is True


def test_endpoint_strict_identity_accepts_and_semantic_tamper_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    endpoint = _endpoint(tmp_path, monkeypatch)
    value = evaluator.audit_endpoint(endpoint)
    assert value["candidate"]["update"] == 24

    tampered = json.loads(endpoint.read_text())
    tampered["api_evidence"]["finish_reason"] = "length"
    tampered = _self(
        {key: item for key, item in tampered.items() if key != "receipt_sha256"},
        "receipt_sha256",
    )
    _write(endpoint, tampered)
    with pytest.raises(IntegrityError):
        evaluator.audit_endpoint(endpoint)


def test_repair_fanout_requires_exact_pooled_v4_cohort() -> None:
    receipt = _fanout()
    assert evaluator.valid_repair_fanout(receipt)
    receipt["excluded_executions"][0]["reason"] = "wrong"
    assert not evaluator.valid_repair_fanout(receipt)
    receipt = _fanout()
    receipt["inventory"][0]["environment_nonce"] = receipt["inventory"][1][
        "environment_nonce"
    ]
    assert not evaluator.valid_repair_fanout(receipt)


def test_joint_render_freezes_exact_laptop_and_office_chair_bundles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    endpoint = _endpoint(tmp_path, monkeypatch)
    protocol = _protocol(tmp_path, endpoint, monkeypatch)
    output = tmp_path / "evaluation"
    # Construction-unit test: exercise the joint renderer without depending on
    # whether the operator has restored every historical control result yet.
    # The public renderer itself calls this same auditor with
    # require_control_results=True and therefore still fails closed in reality.
    source_protocol = evaluator._source_protocol
    monkeypatch.setattr(
        evaluator,
        "_source_protocol",
        lambda path, require_control_results: source_protocol(
            path, require_control_results=False
        ),
    )
    evaluator.render(
        argparse.Namespace(
            repository_root=ROOT,
            source_preparation=SOURCE,
            protocol=protocol,
            endpoint_receipt=endpoint,
            output_root=output,
            laptop_base_port=31000,
            office_chair_base_port=31012,
            python_executable=str(ROOT / ".venv/bin/python"),
        )
    )
    joint = evaluator.audit_evaluation(output, protocol, endpoint)
    assert joint["office_chair_candidate_fixed_before_laptop_outcomes"] is True
    assert joint["candidate_selection_or_retuning_after_laptop_forbidden"] is True
    for category in evaluator.CATEGORIES:
        launch = json.loads(
            (output / "bundles" / category / "launch_manifest.json").read_text()
        )
        controls = json.loads(
            (output / "bundles" / category / "control_launch_manifest.json").read_text()
        )
        assert len(launch["launches"]) == 12
        assert len(controls["launches"]) == 24
        assert controls["reuse_only_evidence"] is True
        assert (
            sum(
                row["condition"] == "clean"
                for row in map(
                    lambda item: json.loads(Path(item["config"]).read_text()),
                    launch["launches"],
                )
            )
            == 4
        )
        assert not (output / "run_results" / category).exists()


def test_joint_render_rejects_existing_output_without_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    endpoint = _endpoint(tmp_path, monkeypatch)
    protocol = _protocol(tmp_path, endpoint, monkeypatch)
    output = tmp_path / "evaluation"
    output.mkdir()
    with pytest.raises(IntegrityError):
        evaluator.render(
            argparse.Namespace(
                repository_root=ROOT,
                source_preparation=SOURCE,
                protocol=protocol,
                endpoint_receipt=endpoint,
                output_root=output,
                laptop_base_port=31000,
                office_chair_base_port=31012,
                python_executable=str(ROOT / ".venv/bin/python"),
            )
        )


def _render_for_synthesis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path]:
    endpoint = _endpoint(tmp_path, monkeypatch)
    protocol = _protocol(tmp_path, endpoint, monkeypatch)
    output = tmp_path / "evaluation"
    source_protocol = evaluator._source_protocol
    monkeypatch.setattr(
        evaluator,
        "_source_protocol",
        lambda path, require_control_results: source_protocol(
            path, require_control_results=False
        ),
    )
    evaluator.render(
        argparse.Namespace(
            repository_root=ROOT,
            source_preparation=SOURCE,
            protocol=protocol,
            endpoint_receipt=endpoint,
            output_root=output,
            laptop_base_port=31000,
            office_chair_base_port=31012,
            python_executable=str(ROOT / ".venv/bin/python"),
        )
    )
    return output, protocol, endpoint


def _write_category_report(output: Path, category: str) -> Path:
    joint = json.loads((output / "evaluation_freeze.json").read_text())
    binding = json.loads((output / "bindings" / category / "binding.json").read_text())
    pairs = []
    for index, mapping in enumerate(binding["mappings"]):
        pair = {"cell": mapping["cell"]}
        for arm, strict, preservation in (
            ("raw", float(index % 2), 0.1),
            ("step20", float((index + 1) % 2), 0.2),
            ("fixed_v7", 1.0, 0.3),
        ):
            pair[arm] = {
                "strict_binary": strict,
                "preservation_strict": preservation,
                "valid_transaction": True,
                "result": {"arm": arm, "index": index},
            }
        pairs.append(pair)
    conditions = {
        condition: evaluator._summarize(
            [pair for pair in pairs if pair["cell"][2] == condition]
        )
        for condition in ("combined", "clean")
    }
    core = {
        "schema": evaluator.REPORT_SCHEMA,
        "status": "complete",
        "protocol_sha256": joint["protocol_body_sha256"],
        "evaluation_freeze_sha256": joint["evaluation_freeze_sha256"],
        "binding_sha256": binding["binding_sha256"],
        "endpoint_receipt_sha256": joint["endpoint_receipt_body_sha256"],
        "candidate_composite_sha256": joint["candidate_composite_sha256"],
        "category": category,
        "label": evaluator.LABELS[category],
        "headline_metric": "strict_binary",
        "secondary_metric": "preservation_strict",
        "conditions": conditions,
        "pairs": pairs,
        "invariants": {
            "candidate_runs": 12,
            "reused_raw_runs": 12,
            "reused_step20_runs": 12,
            "clean_runs_per_arm": 4,
            "combined_runs_per_arm": 8,
            "infrastructure_invalid_runs": 0,
            "bound_runs": 0,
            "fresh_scores_recomputed": True,
            "identical_task_seed_causal_harness_and_limits": True,
            "both_candidate_categories_frozen_before_laptop_execution": True,
            "candidate_selection_or_retuning_from_laptop_score": False,
            "office_chair_training_contamination": False,
        },
        "conversion_audits": {"controls": "c" * 64, "candidate": "d" * 64},
    }
    report = {**core, "report_sha256": sha256_bytes(canonical_bytes(core))}
    path = output / "reports" / category / "report.json"
    path.parent.mkdir(parents=True)
    return _write(path, report)


def test_synthesis_is_preregistered_before_reports_and_binds_both_categories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, protocol, endpoint = _render_for_synthesis(tmp_path, monkeypatch)
    plan_path = output / "synthesis" / "preregistration.json"
    evaluator.preregister_synthesis(
        argparse.Namespace(
            evaluation_root=output,
            protocol=protocol,
            endpoint_receipt=endpoint,
            evaluator_git_sha="e" * 40,
            output=plan_path,
        )
    )
    plan = evaluator.audit_synthesis_plan(
        plan_path,
        evaluation_root=output,
        protocol_path=protocol,
        endpoint_path=endpoint,
        evaluator_git_sha="e" * 40,
    )
    assert plan["outcomes_read_during_preregistration"] is False
    assert plan["category_reports_present_during_preregistration"] is False
    assert set(plan["categories"]) == set(evaluator.CATEGORIES)
    assert all(
        plan["categories"][category]["mapping_count"] == 12
        for category in evaluator.CATEGORIES
    )
    assert plan["analysis_spec"]["pooled_24_cell_summary"] == {
        "role": "descriptive_only",
        "equal_cell_weighting": True,
        "inference_performed": False,
        "selection_or_gate_use": False,
        "substitute_for_office_chair_claim": False,
    }
    assert plan["analysis_spec"]["decision_policy"]["threshold_gate"] is None


def test_synthesis_finalizer_keeps_category_inference_primary_and_pool_descriptive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, protocol, endpoint = _render_for_synthesis(tmp_path, monkeypatch)
    plan_path = output / "synthesis" / "preregistration.json"
    evaluator.preregister_synthesis(
        argparse.Namespace(
            evaluation_root=output,
            protocol=protocol,
            endpoint_receipt=endpoint,
            evaluator_git_sha="e" * 40,
            output=plan_path,
        )
    )
    for category in evaluator.CATEGORIES:
        _write_category_report(output, category)
    report_path = output / "reports" / "synthesis" / "report.json"
    evaluator.finalize_synthesis(
        argparse.Namespace(
            evaluation_root=output,
            protocol=protocol,
            endpoint_receipt=endpoint,
            synthesis_plan=plan_path,
            evaluator_git_sha="e" * 40,
            output=report_path,
        )
    )
    report = evaluator.audit_synthesis_report(
        report_path,
        plan_path=plan_path,
        evaluation_root=output,
        protocol_path=protocol,
        endpoint_path=endpoint,
        evaluator_git_sha="e" * 40,
    )
    assert set(report["primary_category_claims"]) == set(evaluator.CATEGORIES)
    assert all(
        "fixed_v7_vs_raw_one_sided_sign_p"
        in report["primary_category_claims"][category]["conditions"]["combined"][
            "strict_binary"
        ]
        for category in evaluator.CATEGORIES
    )
    pooled = report["pooled_24_cell_descriptive"]
    assert pooled["pair_count"] == 24
    assert pooled["inference_performed"] is False
    assert pooled["selection_or_gate_use"] is False
    assert "fixed_v7_vs_raw_one_sided_sign_p" not in pooled["summary"]["strict_binary"]
    assert report["invariants"]["candidate_selection_from_scores"] is False
    assert report["invariants"]["retuning_from_scores"] is False
    assert report["invariants"]["retraining_from_scores"] is False


def test_synthesis_preregistration_refuses_existing_category_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, protocol, endpoint = _render_for_synthesis(tmp_path, monkeypatch)
    _write_category_report(output, "laptop")
    with pytest.raises(IntegrityError, match="before any category report"):
        evaluator.preregister_synthesis(
            argparse.Namespace(
                evaluation_root=output,
                protocol=protocol,
                endpoint_receipt=endpoint,
                evaluator_git_sha="e" * 40,
                output=output / "synthesis" / "preregistration.json",
            )
        )
