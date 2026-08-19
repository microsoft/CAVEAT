from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_posttrain_eval import sol_dagger_candidate_serve as serve
from harness_posttrain_eval.common import (
    IntegrityError,
    canonical_bytes,
    sha256_bytes,
    sha256_file,
)
from harness_posttrain_eval.launcher import exact_lora_composite_sha256


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value) + b"\n")
    return path


def _self(value: dict, field: str) -> dict:
    return {
        **value,
        field: sha256_bytes(canonical_bytes(value)),
    }


def _valid_lineage(tmp_path: Path) -> Path:
    adapter_tree = "1" * 64
    adapter_config = "2" * 64
    stable = "3" * 64
    root = Path("/data/runs/t-yuxuanli/evaluator/source_dir")
    campaign = (
        serve.RUNS_ROOT
        / "t-yuxuanli-hpt-q35-sol-dagger-sft-a2935fa-w3-20260814"
    )
    collection_campaign = (
        serve.RUNS_ROOT
        / "t-yuxuanli-hpt-q35-sol-dagger-sft-66ec60e-r2-20260814"
    )
    artifacts = {
        "repair_parent_receipt": {
            "path": str(serve.REPAIR_RECEIPT_PATH),
            "file_sha256": serve.REPAIR_RECEIPT_FILE,
            "body_sha256": serve.REPAIR_RECEIPT_BODY,
        },
        "collection_manifest": {
            "path": str(collection_campaign / "collection_r1/collection_manifest.json"),
            "file_sha256": serve.COLLECTION_MANIFEST_FILE,
            "body_sha256": serve.COLLECTION_MANIFEST_BODY,
        },
        "training_plan": {
            "path": str(campaign / "training_r1/training/plan.json"),
            "file_sha256": "6" * 64,
            "body_sha256": "7" * 64,
        },
        "training_receipt": {
            "path": str(campaign / "training_r1/training/training_receipt.json"),
            "file_sha256": "8" * 64,
            "body_sha256": "9" * 64,
        },
    }
    candidate = {
        "name": "step25-sol-dagger-sft",
        "update": 25,
        "adapter_path": str(
            campaign
            / "training_r1/training/prime_output/weights/step_25/lora_adapters"
        ),
        "adapter_files": 2,
        "adapter_bytes": 100,
        "adapter_tree_sha256": adapter_tree,
        "adapter_config_sha256": adapter_config,
        "stable_marker_sha256": stable,
        "parent_path": str(serve.PARENT_PATH),
        "parent_tree_sha256": serve.PARENT_TREE,
        "tokenizer_json_sha256": serve.TOKENIZER_JSON,
        "chat_template_sha256": serve.CHAT_TEMPLATE,
        "dtype": "bfloat16",
        "composite_sha256": exact_lora_composite_sha256(
            parent_tree_sha256=serve.PARENT_TREE,
            adapter_tree_sha256=adapter_tree,
            adapter_config_sha256=adapter_config,
            tokenizer_json_sha256=serve.TOKENIZER_JSON,
            chat_template_sha256=serve.CHAT_TEMPLATE,
            dtype="bfloat16",
        ),
    }
    final_tree = "a" * 64
    training = {
        "scientific_label": "sol_to_qwen_receipt_bound_dagger_sft",
        "collection_source_git_sha": serve.COLLECTION_SOURCE_GIT,
        "trainer_source_git_sha": "a2935fa" + "c" * 33,
        "teacher_model": serve.TEACHER_MODEL,
        "teacher_reasoning_effort": serve.TEACHER_EFFORT,
        "source_step": 24,
        "final_step": 25,
        "optimizer_updates": 1,
        "fresh_optimizer": True,
        "optimizer_continuation": False,
        "assistant_tokens_only": True,
        "topology": {"dp_shards": 1, "cp": 4, "gpus": 4},
        "active_token_loss_mass": {"corrective": 200, "retention": 100},
        "source_dcp": {
            "path": str(serve.REPAIR_DCP_PATH),
            "files": 9,
            "bytes": 1000,
            "tree_sha256": serve.REPAIR_DCP_TREE,
        },
        "final_dcp": {
            "path": str(
                campaign
                / "training_r1/training/prime_output/checkpoints/step_25/trainer"
            ),
            "files": 9,
            "bytes": 1001,
            "tree_sha256": final_tree,
        },
        "final_dcp_tree_sha256": final_tree,
        "checkpoint_bridge": {
            "source_tree_sha256": serve.REPAIR_DCP_TREE,
            "destination_tree_sha256": serve.REPAIR_DCP_TREE,
            "hardlinked_files": 9,
            "all_files_hardlinked": True,
        },
        "candidate_tree_sha256": adapter_tree,
        "adapter_config_sha256": adapter_config,
        "stable_marker_sha256": stable,
        "training_plan_file_sha256": artifacts["training_plan"]["file_sha256"],
        "training_plan_body_sha256": artifacts["training_plan"]["body_sha256"],
        "training_receipt_file_sha256": artifacts["training_receipt"]["file_sha256"],
        "training_receipt_body_sha256": artifacts["training_receipt"]["body_sha256"],
    }
    collection = {
        "source_git_sha": training["collection_source_git_sha"],
        "teacher_model": serve.TEACHER_MODEL,
        "teacher_reasoning_effort": serve.TEACHER_EFFORT,
        "train_corrective_count": 64,
        "retention_count": 32,
        "heldout_count": 24,
        "harness_fingerprint_sha256": serve.COLLECTION_HARNESS_FINGERPRINT,
        "evaluation_fingerprint_sha256": serve.COLLECTION_EVALUATION_FINGERPRINT,
        "parent_final_dcp_tree_sha256": serve.REPAIR_DCP_TREE,
        "manifest_file_sha256": artifacts["collection_manifest"]["file_sha256"],
        "manifest_body_sha256": artifacts["collection_manifest"]["body_sha256"],
    }
    validator = {
        "source": {
            "root": str(root),
            "git_sha": "f" * 40,
            "files": 66,
            "bytes": 10000,
            "tree_sha256": "f" * 64,
        },
        "module": {
            "path": str(root / "src/harness_posttrain_eval/sol_dagger_candidate_serve.py"),
            "sha256": sha256_file(Path(serve.__file__).resolve()),
        },
        "bound_files": serve._bound_source_files(Path(serve.__file__).resolve().parents[2]),
    }
    runtime = {
        "job": serve.DEFAULT_SERVE_JOB,
        "job_uid": "job-uid",
        "job_spec_sha256": "1" * 64,
        "job_labels_sha256": "2" * 64,
        "pod": serve.DEFAULT_SERVE_JOB + "-master-0",
        "pod_uid": "pod-uid",
        "node": "b200-node",
        "image_id": "registry.invalid/image@" + serve.IMAGE_DIGEST,
        "restart_count": 0,
        "executed_in_bound_pod": True,
    }
    core = {
        "schema": serve.LINEAGE_SCHEMA,
        "status": "verified_on_pvc",
        "outcome_blind": True,
        "evaluation_harness_modified": False,
        "large_artifacts_rehashed_on_pvc": True,
        "validator": validator,
        "artifacts": artifacts,
        "candidate": candidate,
        "training": training,
        "collection": collection,
        "runtime": runtime,
        "lineage_binding_sha256": serve._lineage_binding(
            validator=validator,
            artifacts=artifacts,
            candidate=candidate,
            training=training,
            collection=collection,
            runtime=runtime,
        ),
    }
    return _write(tmp_path / "lineage.json", _self(core, "attestation_sha256"))


def _valid_release_and_endpoint(tmp_path: Path) -> tuple[Path, Path, Path]:
    lineage_path = _valid_lineage(tmp_path)
    lineage = serve.validate_lineage_attestation(lineage_path)
    runtime = lineage["runtime"]
    entrypoint = {
        "path": str(Path(lineage["validator"]["source"]["root"]) / serve.ENTRYPOINT_RELATIVE),
        "sha256": "a" * 64,
    }
    partial_release = {"entrypoint": entrypoint}
    release_core = {
        "schema": serve.SERVE_RELEASE_SCHEMA,
        "status": "released",
        "purpose": serve.PURPOSE,
        "laptop_r01_outcomes_read": False,
        "office_chair_outcomes_read": False,
        "reservation": {
            "job_name": runtime["job"],
            "pod_name": runtime["pod"],
            "pod_uid": runtime["pod_uid"],
        },
        "source": lineage["validator"]["source"],
        "entrypoint": entrypoint,
        "argv": serve._release_argv_from_lineage(
            partial_release, lineage, pod_uid=runtime["pod_uid"]
        ),
    }
    release_path = _write(
        tmp_path / "release.json", _self(release_core, "release_sha256")
    )
    candidate = lineage["candidate"]
    training = lineage["training"]
    alias = (
        "qwen35-browser-action-sol-dagger-step25-"
        f"{candidate['adapter_tree_sha256'][:12]}-exact-lora"
    )
    endpoint_core = {
        "schema": serve.ENDPOINT_SCHEMA,
        "status": "ok",
        "outcome_blind": True,
        "evaluation_harness_modified": False,
        "candidate": {
            "name": "step25-sol-dagger-sft",
            "update": 25,
            "adapter_path": candidate["adapter_path"],
            "parent_path": str(serve.PARENT_PATH),
            "parent_tree_sha256": serve.PARENT_TREE,
            "adapter_tree_sha256": candidate["adapter_tree_sha256"],
            "adapter_config_sha256": candidate["adapter_config_sha256"],
            "stable_marker_sha256": candidate["stable_marker_sha256"],
            "tokenizer_json_sha256": serve.TOKENIZER_JSON,
            "chat_template_sha256": serve.CHAT_TEMPLATE,
            "dtype": "bfloat16",
            "composite_sha256": candidate["composite_sha256"],
            "served_model_name": alias,
        },
        "model_spec": {
            "provider": "openai",
            "name": alias,
            "deployment": alias,
            "base_url": serve.LOCAL_BASE_URL,
            "api_key": "env:HARNESS_POSTTRAIN_API_KEY",
            "vision": False,
            "extra": {"frequency_penalty": None},
        },
        "artifacts": {
            "pvc_lineage_attestation": {
                "path": str(lineage_path),
                "file_sha256": sha256_file(lineage_path),
                "body_sha256": lineage["attestation_sha256"],
            },
            **lineage["artifacts"],
            "serve_release": {
                "path": str(release_path),
                "file_sha256": sha256_file(release_path),
                "body_sha256": json.loads(release_path.read_text())["release_sha256"],
            },
        },
        "training": {
            "lineage_attestation_sha256": lineage["attestation_sha256"],
            "lineage_binding_sha256": lineage["lineage_binding_sha256"],
            "collection_source_git_sha": training["collection_source_git_sha"],
            "trainer_source_git_sha": training["trainer_source_git_sha"],
            "teacher_model": serve.TEACHER_MODEL,
            "teacher_reasoning_effort": serve.TEACHER_EFFORT,
            "source_step": 24,
            "final_step": 25,
            "optimizer_updates": 1,
            "fresh_optimizer": True,
            "optimizer_continuation": False,
            "assistant_tokens_only": True,
            "topology": training["topology"],
            "active_token_loss_mass": training["active_token_loss_mass"],
            "source_dcp_tree_sha256": training["source_dcp"]["tree_sha256"],
            "final_dcp_tree_sha256": training["final_dcp"]["tree_sha256"],
        },
        "runtime": {
            "job": runtime["job"],
            "job_uid": runtime["job_uid"],
            "job_spec_sha256": runtime["job_spec_sha256"],
            "job_labels_sha256": runtime["job_labels_sha256"],
            "pod": runtime["pod"],
            "pod_uid": runtime["pod_uid"],
            "node": runtime["node"],
            "image_id": runtime["image_id"],
        },
    }
    endpoint_path = _write(
        tmp_path / "endpoint-full.json", _self(endpoint_core, "receipt_sha256")
    )
    return lineage_path, release_path, endpoint_path


def test_generic_release_is_self_hashed_source_bound_and_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    monkeypatch.setattr(serve, "ALLOWED_SOURCE_ROOTS", (tmp_path,))
    entrypoint = source / serve.ENTRYPOINT_RELATIVE
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    identity = serve._source_identity(source)
    job_name, pod_name = serve._serve_names(serve.DEFAULT_SERVE_JOB)
    pod_uid = "pod-uid"
    argv = [
        sha256_file(entrypoint),
        "/data/parent.json",
        "1" * 64,
        "2" * 64,
        "/data/collection.json",
        "3" * 64,
        "4" * 64,
        "/data/training.json",
        "5" * 64,
        "6" * 64,
        "7" * 64,
        "8" * 64,
        "9" * 64,
        job_name,
        pod_name,
        pod_uid,
    ]
    core = {
        "schema": serve.SERVE_RELEASE_SCHEMA,
        "status": "released",
        "purpose": serve.PURPOSE,
        "laptop_r01_outcomes_read": False,
        "office_chair_outcomes_read": False,
        "reservation": {
            "job_name": job_name,
            "pod_name": pod_name,
            "pod_uid": pod_uid,
        },
        "source": {"root": str(source), "git_sha": "a" * 40, **identity},
        "entrypoint": {"path": str(entrypoint), "sha256": sha256_file(entrypoint)},
        "argv": argv,
    }
    path = _write(tmp_path / "release.json", _self(core, "release_sha256"))
    assert serve.audit_release(path)["argv"] == argv

    tampered = json.loads(path.read_text())
    tampered["purpose"] = "other"
    _write(path, tampered)
    with pytest.raises(IntegrityError):
        serve.audit_release(path)


def test_candidate_release_rejects_parent_or_student_reservations() -> None:
    assert serve._serve_names(serve.DEFAULT_SERVE_JOB) == (
        serve.DEFAULT_SERVE_JOB,
        serve.DEFAULT_SERVE_JOB + "-master-0",
    )
    with pytest.raises(IntegrityError):
        serve._serve_names("t-yuxuanli-hpt-q35-sol-dagger-student-serve-w1")
    with pytest.raises(IntegrityError):
        serve._serve_names("t-yuxuanli-hpt-q35-sol-dagger-parent-serve-w2")


def test_collection_invariance_contract_uses_projection_not_raw_wire_equality() -> None:
    assert "teacher_harness_projection_byte_identical" in serve.EXPECTED_COLLECTION_INVARIANTS
    assert "teacher_request_byte_identical_to_student_request" not in serve.EXPECTED_COLLECTION_INVARIANTS
    assert serve.EXPECTED_TEACHER == {
        "model": "gpt-5.6-sol",
        "effort": "low",
        "provider": "trapi",
        "model_spec": "gpt-5.6-sol#low",
    }
    assert serve.EXPECTED_PROVIDER_ONLY_DIFFERENCES == [
        "model",
        "reasoning_effort",
        "max_completion_tokens",
        "n",
        "stream",
    ]


def test_direct_lora_server_argv_is_exact() -> None:
    argv = serve.build_vllm_argv(Path("/data/parent"), Path("/data/adapter"), "alias")
    assert argv[:3] == ["vllm", "serve", "/data/parent"]
    assert argv[argv.index("--max-model-len") + 1] == "32768"
    assert argv[argv.index("--max-lora-rank") + 1] == "64"
    assert argv[argv.index("--lora-modules") + 1] == "alias=/data/adapter"
    assert argv[argv.index("--data-parallel-size") + 1] == "4"
    assert "--no-enable-prefix-caching" in argv
    assert "--enable-auto-tool-choice" in argv


def test_runtime_and_one_update_evidence_are_fail_closed(tmp_path: Path) -> None:
    resources = {
        "requests": {"nvidia.com/gpu": "4", "rdma/rdma_shared_device_a": "4"},
        "limits": {"nvidia.com/gpu": "4", "rdma/rdma_shared_device_a": "4"},
    }
    assert serve._four_b200_resources(
        {"resources": resources}, {"nvidia.com/gpu.product": "NVIDIA-B200"}
    )
    assert not serve._four_b200_resources(
        {"resources": resources}, {"nvidia.com/gpu.product": "NVIDIA-H100"}
    )

    log = tmp_path / "trainer.log"
    log.write_text(
        "Starting from step 25\n"
        "Step 25 | loss=1\n"
        "Writing final checkpoint\n"
        "Writing final weight checkpoint\n"
        "SFT trainer finished!\n",
        encoding="utf-8",
    )
    assert serve._trainer_log_audit(log)["step25_records"] == 1
    log.write_text(log.read_text() + "Step 26 | loss=1\n", encoding="utf-8")
    with pytest.raises(IntegrityError):
        serve._trainer_log_audit(log)


def test_host_validates_pvc_lineage_without_dereferencing_remote_paths(
    tmp_path: Path,
) -> None:
    path = _valid_lineage(tmp_path)
    value = serve.validate_lineage_attestation(path)
    assert value["large_artifacts_rehashed_on_pvc"] is True
    assert value["training"]["source_dcp"]["tree_sha256"] == serve.REPAIR_DCP_TREE
    assert value["candidate"]["adapter_tree_sha256"] == "1" * 64
    assert Path(value["artifacts"]["collection_manifest"]["path"]).parts[-3:] == (
        "t-yuxuanli-hpt-q35-sol-dagger-sft-66ec60e-r2-20260814",
        "collection_r1",
        "collection_manifest.json",
    )
    assert Path(value["artifacts"]["training_receipt"]["path"]).parts[-4:] == (
        "t-yuxuanli-hpt-q35-sol-dagger-sft-a2935fa-w3-20260814",
        "training_r1",
        "training",
        "training_receipt.json",
    )
    assert serve.SOURCE_BRIDGE_RELATIVE == Path("checkpoints/step_24/trainer")


@pytest.mark.parametrize(
    "mutation",
    ("receipt", "adapter", "source_dcp", "final_dcp"),
)
def test_host_rejects_rehashed_lineage_substitutions(
    tmp_path: Path, mutation: str
) -> None:
    path = _valid_lineage(tmp_path)
    value = json.loads(path.read_text())
    if mutation == "receipt":
        value["artifacts"]["training_receipt"]["file_sha256"] = "0" * 64
    elif mutation == "adapter":
        value["candidate"]["adapter_tree_sha256"] = "0" * 64
    elif mutation == "source_dcp":
        value["training"]["source_dcp"]["tree_sha256"] = "0" * 64
    else:
        value["training"]["final_dcp"]["tree_sha256"] = "0" * 64
    value["lineage_binding_sha256"] = serve._lineage_binding(
        validator=value["validator"],
        artifacts=value["artifacts"],
        candidate=value["candidate"],
        training=value["training"],
        collection=value["collection"],
        runtime=value["runtime"],
    )
    core = {key: item for key, item in value.items() if key != "attestation_sha256"}
    _write(path, _self(core, "attestation_sha256"))
    with pytest.raises(IntegrityError):
        serve.validate_lineage_attestation(path)


def test_endpoint_consumes_copied_lineage_without_remote_pvc_mount(tmp_path: Path) -> None:
    _lineage, _release, endpoint = _valid_release_and_endpoint(tmp_path)
    value = serve.validate_endpoint(endpoint)
    assert value["training"]["lineage_attestation_sha256"]
    assert value["candidate"]["adapter_tree_sha256"] == "1" * 64


@pytest.mark.parametrize("mutation", ("served_alias", "model_extra", "deployment"))
def test_endpoint_rejects_rehashed_model_substitution(
    tmp_path: Path, mutation: str
) -> None:
    _lineage, _release, endpoint = _valid_release_and_endpoint(tmp_path)
    value = json.loads(endpoint.read_text())
    if mutation == "served_alias":
        value["candidate"]["served_model_name"] = "qwen35-exact-lora-parent"
    elif mutation == "model_extra":
        value["model_spec"]["extra"]["temperature"] = 1
    else:
        value["model_spec"]["deployment"] = "qwen35-exact-lora-parent"
    core = {key: item for key, item in value.items() if key != "receipt_sha256"}
    _write(endpoint, _self(core, "receipt_sha256"))
    with pytest.raises(IntegrityError):
        serve.validate_endpoint(endpoint)


def test_endpoint_rejects_substituted_valid_lineage(tmp_path: Path) -> None:
    lineage_path, _release, endpoint_path = _valid_release_and_endpoint(tmp_path)
    lineage = json.loads(lineage_path.read_text())
    lineage["validator"]["source"]["git_sha"] = "0" * 40
    lineage["lineage_binding_sha256"] = serve._lineage_binding(
        validator=lineage["validator"],
        artifacts=lineage["artifacts"],
        candidate=lineage["candidate"],
        training=lineage["training"],
        collection=lineage["collection"],
        runtime=lineage["runtime"],
    )
    lineage_core = {
        key: item for key, item in lineage.items() if key != "attestation_sha256"
    }
    _write(lineage_path, _self(lineage_core, "attestation_sha256"))
    substituted = serve.validate_lineage_attestation(lineage_path)
    endpoint = json.loads(endpoint_path.read_text())
    endpoint["artifacts"]["pvc_lineage_attestation"] = {
        "path": str(lineage_path),
        "file_sha256": sha256_file(lineage_path),
        "body_sha256": substituted["attestation_sha256"],
    }
    endpoint["training"]["lineage_attestation_sha256"] = substituted[
        "attestation_sha256"
    ]
    endpoint["training"]["lineage_binding_sha256"] = substituted[
        "lineage_binding_sha256"
    ]
    endpoint_core = {
        key: item for key, item in endpoint.items() if key != "receipt_sha256"
    }
    _write(endpoint_path, _self(endpoint_core, "receipt_sha256"))
    with pytest.raises(IntegrityError, match="lineage"):
        serve.validate_endpoint(endpoint_path)


def test_endpoint_validator_binds_exact_lora_and_training_policy(tmp_path: Path) -> None:
    adapter_tree = "1" * 64
    adapter_config = "2" * 64
    stable = "3" * 64
    alias = f"qwen35-browser-action-sol-dagger-step25-{adapter_tree[:12]}-exact-lora"
    composite = exact_lora_composite_sha256(
        parent_tree_sha256=serve.PARENT_TREE,
        adapter_tree_sha256=adapter_tree,
        adapter_config_sha256=adapter_config,
        tokenizer_json_sha256=serve.TOKENIZER_JSON,
        chat_template_sha256=serve.CHAT_TEMPLATE,
        dtype="bfloat16",
    )
    core = {
        "schema": serve.ENDPOINT_SCHEMA,
        "status": "ok",
        "outcome_blind": True,
        "evaluation_harness_modified": False,
        "candidate": {
            "name": "step25-sol-dagger-sft",
            "update": 25,
            "adapter_path": "/data/adapter",
            "parent_path": str(serve.PARENT_PATH),
            "parent_tree_sha256": serve.PARENT_TREE,
            "adapter_tree_sha256": adapter_tree,
            "adapter_config_sha256": adapter_config,
            "stable_marker_sha256": stable,
            "tokenizer_json_sha256": serve.TOKENIZER_JSON,
            "chat_template_sha256": serve.CHAT_TEMPLATE,
            "dtype": "bfloat16",
            "composite_sha256": composite,
            "served_model_name": alias,
        },
        "model_spec": {
            "provider": "openai",
            "name": alias,
            "deployment": alias,
            "base_url": serve.LOCAL_BASE_URL,
            "api_key": "env:HARNESS_POSTTRAIN_API_KEY",
            "vision": False,
            "extra": {"frequency_penalty": None},
        },
        "training": {
            "lineage_attestation_sha256": "4" * 64,
            "lineage_binding_sha256": "5" * 64,
            "teacher_model": serve.TEACHER_MODEL,
            "teacher_reasoning_effort": serve.TEACHER_EFFORT,
            "source_step": 24,
            "final_step": 25,
            "optimizer_updates": 1,
            "fresh_optimizer": True,
            "optimizer_continuation": False,
            "assistant_tokens_only": True,
            "topology": {"dp_shards": 1, "cp": 4, "gpus": 4},
            "source_dcp_tree_sha256": serve.REPAIR_DCP_TREE,
        },
        "runtime": {
            "job": serve.DEFAULT_SERVE_JOB,
            "job_uid": "job-uid",
            "job_spec_sha256": "6" * 64,
            "job_labels_sha256": "7" * 64,
            "pod": serve.DEFAULT_SERVE_JOB + "-master-0",
            "pod_uid": "pod-uid",
            "node": "b200-node",
            "image_id": "registry.invalid/image@" + serve.IMAGE_DIGEST,
        },
    }
    path = _write(tmp_path / "endpoint.json", _self(core, "receipt_sha256"))
    assert serve.validate_endpoint(path, verify_artifacts=False)["candidate"]["composite_sha256"] == composite

    value = json.loads(path.read_text())
    value["evaluation_harness_modified"] = True
    core = {key: item for key, item in value.items() if key != "receipt_sha256"}
    _write(path, _self(core, "receipt_sha256"))
    with pytest.raises(IntegrityError):
        serve.validate_endpoint(path, verify_artifacts=False)
