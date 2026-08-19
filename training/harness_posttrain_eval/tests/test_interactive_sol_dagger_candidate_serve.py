from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_posttrain_eval import interactive_sol_dagger_candidate_serve as serve
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
    return {**value, field: sha256_bytes(canonical_bytes(value))}


def _candidate() -> dict:
    tree = "1" * 64
    config = "2" * 64
    return {
        "name": "step26-action-weighted-ce",
        "update": 26,
        "adapter_path": str(serve.CANDIDATE_PATH),
        "adapter_files": 2,
        "adapter_bytes": 100,
        "adapter_tree_sha256": tree,
        "adapter_config_sha256": config,
        "stable_marker_sha256": "3" * 64,
        "parent_path": str(serve.PARENT_PATH),
        "parent_tree_sha256": serve.PARENT_TREE,
        "tokenizer_json_sha256": serve.TOKENIZER_JSON,
        "chat_template_sha256": serve.CHAT_TEMPLATE,
        "dtype": "bfloat16",
        "composite_sha256": exact_lora_composite_sha256(
            parent_tree_sha256=serve.PARENT_TREE,
            adapter_tree_sha256=tree,
            adapter_config_sha256=config,
            tokenizer_json_sha256=serve.TOKENIZER_JSON,
            chat_template_sha256=serve.CHAT_TEMPLATE,
            dtype="bfloat16",
        ),
    }


def _subtypes() -> dict[str, int]:
    return {
        "frontier_exploration/exploration": 16,
        "checkpoint_grounding/decision_checkpoint": 16,
        "approved_cart_entry/add_to_cart": 8,
        "approved_cart_entry/open_cart": 8,
        "dirty_cart_cleanup/addon": 8,
        "dirty_cart_cleanup/stale_extra": 8,
        "clean_checkout_order/cart_to_checkout": 4,
        "clean_checkout_order/place_order": 4,
    }


def _training() -> dict:
    return {
        "scientific_label": serve.SCIENTIFIC_LABEL,
        "artifact_source_git_sha": serve.TRAINER_SOURCE_GIT_SHA,
        "execution_source_git_sha": serve.TRAINER_SOURCE_GIT_SHA,
        "trainer_source_git_sha": serve.TRAINER_SOURCE_GIT_SHA,
        "collection_source_git_sha": serve.COLLECTION_MATERIALIZER_GIT_SHA,
        "prime_commit": serve.PRIME_COMMIT,
        "source_step": 25,
        "final_step": 26,
        "optimizer_updates": 1,
        "learning_rate": 1e-7,
        "fresh_optimizer": True,
        "fresh_scheduler": True,
        "fresh_dataloader": True,
        "objective": "weighted_behavioral_cloning",
        "on_policy": False,
        "policy_gradient": False,
        "native_prime_component": "ce",
        "trainer_topology": serve.TOPOLOGIES["cp2_dp2"],
        "collection_counts": serve.COLLECTION_COUNTS,
        "resume_order": serve.RESUME_ORDER,
        "resume_lora_pre_update_audit": {
            "signature_unchanged": True,
            "lora_b_nonzero": 10,
        },
        "source_dcp": {
            "path": str(serve.PARENT_DCP_PATH),
            "files": serve.PARENT_DCP_FILES,
            "bytes": serve.PARENT_DCP_BYTES,
            "tree_sha256": serve.PARENT_DCP_TREE,
        },
        "final_dcp": {
            "path": str(serve.TRAINING_ROOT / "prime_output/checkpoints/step_26/trainer"),
            "files": 9,
            "bytes": 100,
            "tree_sha256": "4" * 64,
        },
        "training_plan_file_sha256": "7" * 64,
        "training_plan_body_sha256": "8" * 64,
        "training_receipt_file_sha256": "9" * 64,
        "training_receipt_body_sha256": "a" * 64,
        "weight_audit": {"status": "ok"},
        "token_execution_audit": {"status": "ok"},
    }


def _collection() -> dict:
    return {
        "source_git_sha": serve.COLLECTION_MATERIALIZER_GIT_SHA,
        "provenance": serve.COLLECTION_PROVENANCE,
        "teacher_model": "gpt-5.6-sol",
        "teacher_reasoning_effort": "low",
        "train_corrective_count": 72,
        "retention_count": 24,
        "heldout_count": 0,
        "phase_counts": serve.COLLECTION_PHASES,
        "phase_subtype_counts": _subtypes(),
        "unique_states": 96,
        "manifest_file_sha256": "5" * 64,
        "manifest_body_sha256": "6" * 64,
    }


def _lineage(tmp_path: Path) -> Path:
    source_root = Path("/data/runs/t-yuxuanli/c2-eval-source")
    candidate = _candidate()
    training = _training()
    collection = _collection()
    artifacts = {
        "repair_parent_receipt": {
            "path": str(serve.PARENT_RECEIPT_PATH),
            "file_sha256": serve.PARENT_RECEIPT_FILE,
            "body_sha256": serve.PARENT_RECEIPT_BODY,
        },
        "collection_manifest": {
            "path": "/data/runs/t-yuxuanli/c2-collection/manifest.json",
            "file_sha256": collection["manifest_file_sha256"],
            "body_sha256": collection["manifest_body_sha256"],
        },
        "training_plan": {
            "path": str(serve.TRAINING_ROOT / "plan.json"),
            "file_sha256": "7" * 64,
            "body_sha256": "8" * 64,
        },
        "training_receipt": {
            "path": str(serve.TRAINING_RECEIPT_PATH),
            "file_sha256": "9" * 64,
            "body_sha256": "a" * 64,
        },
    }
    validator = {
        "source": {
            "root": str(source_root),
            "git_sha": "b" * 40,
            "files": 10,
            "bytes": 1000,
            "tree_sha256": "c" * 64,
        },
        "module": {
            "path": str(source_root / serve.MODULE_RELATIVE),
            "sha256": sha256_file(Path(serve.__file__).resolve()),
        },
        "bound_files": serve._bound_source_files(Path(serve.__file__).resolve().parents[2]),
    }
    runtime = {
        "job": serve.DEFAULT_SERVE_JOB,
        "job_uid": serve.EXPECTED_JOB_UID,
        "job_spec_sha256": serve.EXPECTED_JOB_SPEC_SHA256,
        "job_labels_sha256": serve.EXPECTED_JOB_LABELS_SHA256,
        "pod": serve.DEFAULT_SERVE_POD,
        "pod_uid": serve.EXPECTED_POD_UID,
        "pod_spec_sha256": serve.EXPECTED_POD_SPEC_SHA256,
        "pod_labels_sha256": serve.EXPECTED_POD_LABELS_SHA256,
        "node": serve.EXPECTED_NODE,
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
        "exact_trainer_validator_executed": True,
        "validator": validator,
        "trainer_source": {
            "root": str(serve.TRAINER_SOURCE_PATH),
            "git_sha": serve.TRAINER_SOURCE_GIT_SHA,
            **serve.TRAINER_SOURCE_IDENTITY,
        },
        "artifacts": artifacts,
        "candidate": candidate,
        "training": training,
        "collection": collection,
        "runtime": runtime,
    }
    core["lineage_binding_sha256"] = serve._lineage_binding(core)
    return _write(tmp_path / "lineage.json", _self(core, "attestation_sha256"))


def _rewrite(path: Path, value: dict, body_field: str) -> None:
    core = {key: item for key, item in value.items() if key != body_field}
    path.write_bytes(canonical_bytes(_self(core, body_field)) + b"\n")


def test_lineage_binds_900d_trainer_final_collection_provenance_and_w1(
    tmp_path: Path,
) -> None:
    value = serve.validate_lineage_attestation(_lineage(tmp_path))
    assert value["trainer_source"]["git_sha"] == serve.TRAINER_SOURCE_GIT_SHA
    assert value["collection"]["provenance"] == serve.COLLECTION_PROVENANCE
    assert value["training"]["collection_source_git_sha"] == (
        serve.COLLECTION_MATERIALIZER_GIT_SHA
    )
    assert value["runtime"]["pod_uid"] == serve.EXPECTED_POD_UID
    assert value["candidate"]["adapter_path"] == str(serve.CANDIDATE_PATH)


@pytest.mark.parametrize("mutation", ("trainer", "collection", "pod", "parent"))
def test_lineage_rejects_rehashed_substitutions(tmp_path: Path, mutation: str) -> None:
    path = _lineage(tmp_path)
    value = json.loads(path.read_text())
    if mutation == "trainer":
        value["trainer_source"]["git_sha"] = "0" * 40
    elif mutation == "collection":
        value["collection"]["provenance"]["validator_materializer_commit"] = "0" * 40
    elif mutation == "pod":
        value["runtime"]["pod_uid"] = "other"
    else:
        value["artifacts"]["repair_parent_receipt"]["file_sha256"] = "0" * 64
    value["lineage_binding_sha256"] = serve._lineage_binding(value)
    _rewrite(path, value, "attestation_sha256")
    with pytest.raises(IntegrityError):
        serve.validate_lineage_attestation(path)


@pytest.mark.parametrize(
    ("artifact", "digest", "training_field"),
    (
        ("training_plan", "file_sha256", "training_plan_file_sha256"),
        ("training_plan", "body_sha256", "training_plan_body_sha256"),
        ("training_receipt", "file_sha256", "training_receipt_file_sha256"),
        ("training_receipt", "body_sha256", "training_receipt_body_sha256"),
    ),
)
def test_lineage_rejects_training_artifact_projection_mismatch(
    tmp_path: Path, artifact: str, digest: str, training_field: str
) -> None:
    path = _lineage(tmp_path)
    value = json.loads(path.read_text())
    value["training"][training_field] = "0" * 64
    value["lineage_binding_sha256"] = serve._lineage_binding(value)
    _rewrite(path, value, "attestation_sha256")
    assert value["artifacts"][artifact][digest] != value["training"][training_field]
    with pytest.raises(IntegrityError):
        serve.validate_lineage_attestation(path)


def test_endpoint_policy_uses_direct_lora_alias_and_tunnel_18541(tmp_path: Path) -> None:
    lineage = serve.validate_lineage_attestation(_lineage(tmp_path))
    candidate = {**lineage["candidate"]}
    alias = serve._candidate_alias(candidate["adapter_tree_sha256"])
    candidate["served_model_name"] = alias
    training = {
        **lineage["training"],
        "lineage_attestation_sha256": lineage["attestation_sha256"],
        "lineage_binding_sha256": lineage["lineage_binding_sha256"],
    }
    core = {
        "schema": serve.ENDPOINT_SCHEMA,
        "status": "ok",
        "outcome_blind": True,
        "evaluation_harness_modified": False,
        "candidate": candidate,
        "model_spec": {
            "provider": "openai",
            "name": alias,
            "deployment": alias,
            "base_url": serve.LOCAL_BASE_URL,
            "api_key": "env:HARNESS_POSTTRAIN_API_KEY",
            "vision": False,
            "extra": {"frequency_penalty": None},
        },
        "artifacts": lineage["artifacts"],
        "training": training,
        "collection": lineage["collection"],
        "runtime": {**lineage["runtime"], "tunnel": {"test": True}},
        "api_evidence": {},
    }
    path = _write(tmp_path / "endpoint.json", _self(core, "receipt_sha256"))
    value = serve.validate_endpoint(path, verify_artifacts=False)
    assert value["model_spec"]["base_url"] == "http://127.0.0.1:18541/v1"
    argv = serve.build_vllm_argv(serve.PARENT_PATH, serve.CANDIDATE_PATH, alias)
    assert argv[argv.index("--lora-modules") + 1] == f"{alias}={serve.CANDIDATE_PATH}"


def test_collection_subtype_policy_requires_exact_transactional_coverage() -> None:
    assert serve._phase_subtype_policy(_subtypes())
    changed = _subtypes()
    changed["approved_cart_entry/add_to_cart"] = 7
    assert not serve._phase_subtype_policy(changed)


def test_generic_release_has_exact_w1_13_argument_contract(tmp_path: Path) -> None:
    lineage = serve.validate_lineage_attestation(_lineage(tmp_path))
    entrypoint = {
        "path": str(
            Path(lineage["validator"]["source"]["root"])
            / serve.ENTRYPOINT_RELATIVE
        ),
        "sha256": "d" * 64,
    }
    partial = {"entrypoint": entrypoint}
    core = {
        "schema": serve.SERVE_RELEASE_SCHEMA,
        "status": "released",
        "purpose": serve.PURPOSE,
        "laptop_r01_outcomes_read": False,
        "office_chair_outcomes_read": False,
        "reservation": {
            "job_name": serve.DEFAULT_SERVE_JOB,
            "pod_name": serve.DEFAULT_SERVE_POD,
            "pod_uid": serve.EXPECTED_POD_UID,
        },
        "source": lineage["validator"]["source"],
        "entrypoint": entrypoint,
        "argv": serve._release_argv(
            partial, lineage, pod_uid=serve.EXPECTED_POD_UID
        ),
    }
    path = _write(tmp_path / "release.json", _self(core, "release_sha256"))
    release = serve.audit_release(path, verify_source=False)
    assert len(release["argv"]) == 13
    assert release["argv"][7:10] == [
        str(serve.TRAINER_SOURCE_PATH),
        serve.TRAINER_SOURCE_GIT_SHA,
        serve.TRAINER_SOURCE_IDENTITY["tree_sha256"],
    ]
    assert release["argv"][10:] == [
        serve.DEFAULT_SERVE_JOB,
        serve.DEFAULT_SERVE_POD,
        serve.EXPECTED_POD_UID,
    ]
