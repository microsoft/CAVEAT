from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

import c2_step36_outcome_candidate_contract as contract
import publish_c2_step36_outcome_candidate as publish
import seal_c2_step36_outcome_microcheckpoint as seal


def _self_hashed(body: dict, field: str) -> bytes:
    value = {**body, field: contract.digest(body)}
    return json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"


def test_exact_proven_source_and_fresh_generation_reservations() -> None:
    source = contract.WORKSPACE / ".worktrees/c2_step35_micro_serve_source"
    assert (
        contract.file_sha256(source / "scripts/run_step35_micro_profile_candidate_serve.sh")
        == contract.SERVE_ENTRYPOINT_SHA256
    )
    assert (
        contract.file_sha256(
            source / "src/harness_posttrain_eval/step35_micro_profile_candidate_serve.py"
        )
        == contract.SERVE_MODULE_SHA256
    )
    for stage in contract.STEPS:
        value = yaml.safe_load(contract.RESERVATION_BY_STAGE[stage].read_text())
        container = value["spec"]["tasks"][0]["template"]["spec"]["containers"][0]
        assert value["metadata"]["name"] == contract.JOB_BY_STAGE[stage]
        assert container["image"] == contract.RESERVATION_IMAGE
        assert container["args"] == [
            contract.RESERVATION_ENTRYPOINT,
            contract.RESERVATION_ENTRYPOINT_SHA256,
            str(contract.RELEASE_BY_STAGE[stage]),
            contract.RELEASE_SCHEMA,
            "c2_candidate_serve",
            contract.JOB_BY_STAGE[stage],
            str(contract.RESERVATION_TIMEOUT_SECONDS),
        ]
    successor = yaml.safe_load(contract.RESERVATION_BY_STAGE[33].read_text())
    expressions = successor["spec"]["tasks"][0]["template"]["spec"][
        "affinity"
    ]["nodeAffinity"]["requiredDuringSchedulingIgnoredDuringExecution"][
        "nodeSelectorTerms"
    ][0]["matchExpressions"]
    excluded = next(row["values"] for row in expressions if row["key"] == "kubernetes.io/hostname")
    assert excluded == ["slc01-cl02-hgx-0148", "slc01-cl02-hgx-0407"]
    failed_w1 = contract.ORCHESTRATION / (
        "c2_step36_outcome_candidate_serve_reservation_s33_w1.yaml"
    )
    assert failed_w1.is_file()
    assert failed_w1 != contract.RESERVATION_BY_STAGE[33]
    assert contract.JOB_BY_STAGE[33].endswith("s33-w2")
    assert contract.RELEASE_BY_STAGE[33].name.endswith("s33_w2.json")
    assert contract.HANDOFF_BY_STAGE[33].name.endswith("s33_w2.json")
    assert contract.ENDPOINT_ROOT_BY_STAGE[33].name.endswith("s33_w2")
    assert contract.LOCAL_PORT_BY_STAGE[33] == 18_562


def test_source_and_adapter_identity_encodings_are_deliberately_distinct(
    tmp_path: Path,
) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    (root / "a").write_bytes(b"a")
    (root / "b").write_bytes(b"bb")
    source = seal.source_identity(root)
    adapter = seal.tree_identity(root)
    rows = [
        {"path": "a", "size": 1, "sha256": hashlib.sha256(b"a").hexdigest()},
        {"path": "b", "size": 2, "sha256": hashlib.sha256(b"bb").hexdigest()},
    ]
    entries = {row["path"]: {"size": row["size"], "sha256": row["sha256"]} for row in rows}
    assert source["tree_sha256"] == hashlib.sha256(seal.canonical(rows)).hexdigest()
    assert adapter["tree_sha256"] == hashlib.sha256(seal.canonical(entries)).hexdigest()
    assert source["tree_sha256"] != adapter["tree_sha256"]


def test_prefinal_publisher_discovers_only_the_exact_sealed_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_body = {
        "schema": contract.PLAN_SCHEMA,
        "status": "prepared",
        "profile": contract.PROFILE,
        "artifact_git_sha": contract.TRAINER_GIT_SHA,
        "training_output": str(contract.TRAINING_OUTPUT),
    }
    plan_payload = _self_hashed(plan_body, "plan_body_sha256")
    monkeypatch.setattr(
        contract, "PLAN_FILE_SHA256", hashlib.sha256(plan_payload).hexdigest()
    )
    monkeypatch.setattr(contract, "PLAN_BODY_SHA256", contract.digest(plan_body))
    trainer = publish._trainer_descriptor()
    inventory_body = {
        "schema": contract.MICRO_INVENTORY_SCHEMA,
        "status": "sealed",
        "profile": contract.PROFILE,
        "step": 33,
        "development_probe_only": True,
        "training_eligible": False,
        "plan_file_sha256": contract.PLAN_FILE_SHA256,
        "plan_body_sha256": contract.PLAN_BODY_SHA256,
        "step36_trainer_source": trainer,
    }
    inventory_payload = _self_hashed(inventory_body, "inventory_body_sha256")
    payloads = {
        contract.PLAN: plan_payload,
        contract.inventory_path(33): inventory_payload,
    }
    monkeypatch.setattr(publish.base, "remote_bytes", lambda path: payloads[path])

    arguments, artifacts = publish._binding(33)

    assert arguments[arguments.index("--artifact-mode") + 1] == "pre_final_inventory"
    assert arguments.count("--micro-inventory") == 1
    assert "--receipt" not in arguments
    assert artifacts == [
        {
            "step": 33,
            "kind": "microcheckpoint_inventory",
            "path": str(contract.inventory_path(33)),
            "file_sha256": hashlib.sha256(inventory_payload).hexdigest(),
            "body_sha256": contract.digest(inventory_body),
        }
    ]


def test_final_generation_requires_the_complete_exact_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_body = {
        "schema": contract.PLAN_SCHEMA,
        "status": "prepared",
        "profile": contract.PROFILE,
        "artifact_git_sha": contract.TRAINER_GIT_SHA,
        "training_output": str(contract.TRAINING_OUTPUT),
    }
    plan_payload = _self_hashed(plan_body, "plan_body_sha256")
    monkeypatch.setattr(
        contract, "PLAN_FILE_SHA256", hashlib.sha256(plan_payload).hexdigest()
    )
    monkeypatch.setattr(contract, "PLAN_BODY_SHA256", contract.digest(plan_body))
    receipt_body = {
        "schema": contract.RECEIPT_SCHEMA,
        "status": "ok",
        "profile": contract.PROFILE,
        "artifact_source_git_sha": contract.TRAINER_GIT_SHA,
        "execution_source_git_sha": contract.TRAINER_GIT_SHA,
        "plan_path": str(contract.PLAN),
        "plan_sha256": contract.PLAN_FILE_SHA256,
        "plan_body_sha256": contract.PLAN_BODY_SHA256,
        "update_steps": list(contract.STEPS),
    }
    receipt_payload = _self_hashed(receipt_body, "receipt_body_sha256")
    payloads = {contract.PLAN: plan_payload, contract.RECEIPT: receipt_payload}
    monkeypatch.setattr(publish.base, "remote_bytes", lambda path: payloads[path])

    arguments, artifacts = publish._binding(35)

    assert arguments[arguments.index("--artifact-mode") + 1] == "final_receipt"
    assert arguments.count("--receipt") == 1
    assert "--micro-inventory" not in arguments
    assert artifacts[0]["kind"] == "training_receipt"


def test_planned_probe_is_dev_only_and_reuses_frozen_two_case_launcher() -> None:
    probe = (contract.WORKSPACE / "scripts/launch_c2_step36_outcome_micro_probe2.py").read_text()
    endpoint = (
        contract.WORKSPACE / "scripts/launch_c2_step36_outcome_candidate_endpoint.py"
    ).read_text()
    assert "launch_c2_step35_micro_probe2.py" in contract.DEV_PROBE_LAUNCHER.name
    assert "--artifact-kind" in probe
    assert "full_evaluation_authorized" in probe
    assert '"development_probe"' in endpoint
    assert "run-bundle" not in endpoint
