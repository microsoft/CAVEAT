from __future__ import annotations

from pathlib import Path
import json

import pytest

from harness_posttrain_eval import fixed_v7_grpo_g0_fast_eval as g0
from harness_posttrain_eval.common import IntegrityError, canonical_bytes, sha256_bytes


def test_g0_specialization_is_posthoc_and_disjoint() -> None:
    assert g0.SOURCE_GIT_SHA == g0.G0_SOURCE
    assert g0.G0_SOURCE != g0._full.SOURCE_GIT_SHA
    assert "g0-fast-serve" in repr(g0._runtime_identity.__code__.co_consts)
    assert g0.SERVE_RELEASE_SCHEMA.endswith("grpo-g0-fast-serve-release.v1")
    assert g0.G0_ADAPTER != g0.PARENT_PATH
    assert g0.G0_RECEIPT.name == "amazon_grpo_g0_exploratory_training_receipt_v2.json"


def test_g0_receipt_validator_rejects_noncanonical_path(tmp_path: Path) -> None:
    path = tmp_path / g0.G0_RECEIPT.name
    path.write_text("{}")
    with pytest.raises(IntegrityError):
        g0._training_receipt(path)


def test_g0_receipt_validator_accepts_full_recovered_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "trainer_only_r1"
    receipt = root / "amazon_grpo_g0_exploratory_training_receipt_v2.json"
    adapter = root / "weights/step_24/lora_adapters"
    erratum_path = tmp_path / "erratum.json"
    adapter.mkdir(parents=True)
    monkeypatch.setattr(g0, "G0_ROOT", root)
    monkeypatch.setattr(g0, "G0_RECEIPT", receipt)
    monkeypatch.setattr(g0, "G0_ADAPTER", adapter)
    stamps = {
        "run_id": "run_default",
        "run_step": 24,
        "exact_on_every_microbatch": True,
    }
    aggregate = {
        "microbatches": 258,
        "padded_tokens": 5_570_360,
        "loss_mask_tokens": 230_712,
        "model_cost": 341_343_311_160_672_256,
        "canonical_stamped_multiset_sha256": g0.STAMPED_MULTISET,
        "transport_stamps_stripped_multiset_sha256": g0.UNSTAMPED_MULTISET,
    }
    rank_files = {
        name: {
            "path": str(root / "rollouts/step_24" / name),
            **identity,
            "run_id": "run_default",
            "run_step": 24,
        }
        for name, identity in g0.ACTUAL_RANKS.items()
    }
    body = {
        "schema": "harness-distill.amazon-grpo-g0-exploratory-training-receipt.v1",
        "status": "ok",
        "scientific_label": g0.G0_LABEL,
        "disclosures": {
            "selected_after_outcome_inspection": True,
            "single_variant": True,
            "not_preregistered_full_cohort": True,
            "not_selection_eligible": True,
            "development_eval_only": True,
        },
        "artifact_source_git_sha": g0.G0_SOURCE,
        "execution_source_git_sha": g0.G0_SOURCE,
        "original_trainer_source_git_sha": g0.G0_TRAINER_SOURCE,
        "original_rollout_source_git_sha": g0.ROLLOUT_SOURCE_GIT_SHA,
        "source_step": 23,
        "final_step": 24,
        "optimizer_updates": 1,
        "learning_rate": 5e-7,
        "selection_performed": True,
        "selection_timing": "post_hoc_after_outcome_inspection",
        "not_selection_eligible": True,
        "development_eval_only": True,
        "new_rollouts_performed": False,
        "source_on_policy_data": True,
        "receipt_recovery_only": True,
        "optimizer_rerun": False,
        "optimizer_rerun_performed": False,
        "training_artifacts_produced_by_original_trainer": True,
        "erratum_path": str(erratum_path),
        "selected_training_batch": {
            "path": str(root / "run_default/rollouts/step_24/train_rollouts.bin"),
            "size": 169_846_624,
            "sha256": "ef8eb63e8d14b22df869bc5553e7ad9527194bed5f8b69d947dbc746407ff091",
            "samples": 290,
            "token_ids": 5_570_236,
            "loss_mask_tokens": 230_712,
            "sample_multiset_sha256": "553b8b2791ef4c093cf500f86e07fec96568c8624e748276467826c9b95ce6cd",
            "advantage_histogram": {
                "-5.008173942565918": 29,
                "-2.031419515609741": 21,
                "-1.7322604656219482": 52,
                "-1.5616824626922607": 20,
                "-1.4616825580596924": 56,
                "-1.3116824626922607": 46,
                "14.568580627441406": 66,
            },
            "step": 24,
            "run_idx": None,
        },
        "packed_microbatches": {
            "erratum_evidence": {
                "path": str(erratum_path),
                "size": 2413,
                "sha256": "274d371688e0e9ade1686bbe6aab675cde1e04eece41cf867baafbeacb6746bf",
            },
            "topology": {
                "trainer_world_size": 4,
                "context_parallel_size": 2,
                "data_parallel_replicate": 2,
                "data_parallel_shard": 1,
                "data_parallel_size": 2,
            },
            "rank_files": rank_files,
            "aggregate": aggregate,
            "transport_stamps": stamps,
            "objective_content_exact": True,
            "optimizer_rerun": False,
            "exact_stamped_pack": True,
        },
        "packed_microbatch_erratum": {
            "erratum_body_sha256": "2347c7bf341d1af54770f0b9ac90e8cb5c9c13258624b9e3898c3eb688096576",
            "reason": "pinned PRIME SinglePacker stamps run_id and run_step after prepare_batch and before FileSystemMicroBatchSender serialization",
            "original_unstamped_expectation": {
                "canonical_multiset_sha256": g0.UNSTAMPED_MULTISET,
                "rank_0.bin": {
                    "size": 174_213_232,
                    "sha256": "3c804885145186f2f5d6a38e2b218f7f96fcf3338025f762dffae9011c0353cb",
                },
                "rank_1.bin": {
                    "size": 173_769_146,
                    "sha256": "e8ff86dbfcbbe94b581e75862048033b2aa6b391c7b21fd894b02608fd685ab5",
                },
            },
            "actual_stamped_output": g0.ACTUAL_RANKS,
            "actual_aggregate": aggregate,
            "transport_stamps": stamps,
            "objective_content_exact": True,
            "optimizer_rerun": False,
        },
        "candidate": {
            "name": "step24-amazon-grpo-g0-exploratory",
            "update": 24,
            "path": str(adapter),
            "files": 2,
            "bytes": 3,
            "tree_sha256": "a" * 64,
        },
    }
    value = {**body, "receipt_body_sha256": sha256_bytes(canonical_bytes(body))}
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    assert g0._training_receipt(receipt) == value


def test_specialized_source_discloses_posthoc_policy() -> None:
    source = g0._specialized_source()
    assert g0.G0_EVAL_LABEL in source
    assert '"post_hoc_after_outcome_inspection": True' in source
    assert '"not_selection_eligible": True' in source
    assert '"development_eval_only": True' in source
    assert "18520:8000" in source
    assert "47000" not in source


def test_w4_runtime_identity_is_exact(tmp_path: Path) -> None:
    job_path = tmp_path / "job.json"
    pods_path = tmp_path / "pods.json"
    job = {
        "metadata": {
            "name": "t-yuxuanli-hpt-q35-v7-grpo-g0-fast-serve-w4",
            "uid": "job-uid",
        },
        "spec": {},
        "status": {"state": {"phase": "Running"}},
    }
    pod = {
        "metadata": {
            "name": "t-yuxuanli-hpt-q35-v7-grpo-g0-fast-serve-w4-master-0",
            "uid": "pod-uid",
        },
        "spec": {"nodeName": "node"},
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "restartCount": 0,
                    "ready": True,
                    "started": True,
                    "imageID": "registry/repo@" + g0.IMAGE_DIGEST,
                }
            ],
        },
    }
    job_path.write_text(json.dumps(job))
    pods_path.write_text(json.dumps({"items": [pod]}))
    observed_job, observed_pod, container = g0._runtime_identity(
        job_path, pods_path, require_ready=True
    )
    assert observed_job["metadata"]["uid"] == "job-uid"
    assert observed_pod["metadata"]["uid"] == "pod-uid"
    assert container["restartCount"] == 0


def test_g0_serve_release_requires_recovery_and_posthoc_disclosures(
    tmp_path: Path,
) -> None:
    core = {
        "schema": g0.SERVE_RELEASE_SCHEMA,
        "status": "released",
        "source_git_sha": g0.G0_SOURCE,
        "original_trainer_source_git_sha": g0.G0_TRAINER_SOURCE,
        "original_rollout_source_git_sha": g0.ROLLOUT_SOURCE_GIT_SHA,
        "parent_component_tree_sha256": g0.PARENT_TREE,
        "adapter_config_sha256": g0.ADAPTER_CONFIG,
        "container_image_digest": g0.IMAGE_DIGEST,
        "scientific_label": g0.G0_LABEL,
        "evaluation_label": g0.G0_EVAL_LABEL,
        "candidate_name": "step24-amazon-grpo-g0-exploratory",
        "selection_performed": True,
        "selection_timing": "post_hoc_after_outcome_inspection",
        "not_selection_eligible": True,
        "development_eval_only": True,
        "laptop_r01_training_data_used": False,
        "office_chair_training_data_used": False,
    }
    value = {**core, "release_sha256": sha256_bytes(canonical_bytes(core))}
    path = tmp_path / "release.json"
    path.write_text(json.dumps(value))
    assert g0._serve_release(path) == value
    value["original_trainer_source_git_sha"] = g0.G0_SOURCE
    core = {key: item for key, item in value.items() if key != "release_sha256"}
    value["release_sha256"] = sha256_bytes(canonical_bytes(core))
    path.write_text(json.dumps(value))
    with pytest.raises(IntegrityError, match="disclosure"):
        g0._serve_release(path)


def test_g0_endpoint_separates_trainer_and_receipt_recovery_sources(
    tmp_path: Path,
) -> None:
    adapter_tree = "b" * 64
    composite = g0.exact_lora_composite_sha256(
        parent_tree_sha256=g0.PARENT_TREE,
        adapter_tree_sha256=adapter_tree,
        adapter_config_sha256=g0.ADAPTER_CONFIG,
        tokenizer_json_sha256=g0.TOKENIZER_JSON,
        chat_template_sha256=g0.CHAT_TEMPLATE,
        dtype="bfloat16",
    )
    core = {
        "schema": g0.ENDPOINT_SCHEMA,
        "status": "ok",
        "outcome_blind": True,
        "scientific_label": g0.G0_EVAL_LABEL,
        "candidate": {
            "name": "step24-amazon-grpo-g0-exploratory",
            "update": 24,
            "adapter_path": str(g0.G0_ADAPTER),
            "parent_tree_sha256": g0.PARENT_TREE,
            "adapter_tree_sha256": adapter_tree,
            "adapter_config_sha256": g0.ADAPTER_CONFIG,
            "composite_sha256": composite,
        },
        "training": {
            "execution_source_git_sha": g0.G0_TRAINER_SOURCE,
            "receipt_recovery_source_git_sha": g0.G0_SOURCE,
            "optimizer_updates": 1,
            "selection_performed": True,
        },
    }
    value = {**core, "receipt_sha256": sha256_bytes(canonical_bytes(core))}
    path = tmp_path / "endpoint.json"
    path.write_text(json.dumps(value))
    assert g0._endpoint(path) == value
    value["training"]["receipt_recovery_source_git_sha"] = g0.G0_TRAINER_SOURCE
    core = {key: item for key, item in value.items() if key != "receipt_sha256"}
    value["receipt_sha256"] = sha256_bytes(canonical_bytes(core))
    path.write_text(json.dumps(value))
    with pytest.raises(IntegrityError, match="endpoint policy"):
        g0._endpoint(path)
