from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_posttrain import repair_step24_candidate_merge as candidate_merge
from harness_posttrain.artifacts import ArtifactError, canonical_json, sha256_bytes, sha256_file
from harness_posttrain.parent_merge import PARENT_MERGE_SCHEMA, checkpoint_manifest

GIT_SHA = "a" * 40
IMAGE = "registry.invalid/campaign@sha256:" + "b" * 64


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _tree(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    parent = tmp_path / "selected/merged"
    parent.mkdir(parents=True)
    _write_json(parent / "config.json", {"architectures": ["Qwen3_5ForConditionalGeneration"]})
    (parent / "model.safetensors").write_bytes(b"parent")
    _write_json(parent / "merge_provenance.json", {"status": "ok"})
    adapter = tmp_path / "repair/weights/step_24/lora_adapters"
    adapter.mkdir(parents=True)
    _write_json(
        adapter / "adapter_config.json",
        {
            "base_model_name_or_path": str(parent.resolve()),
            "r": 64,
            "lora_alpha": 128,
            "target_modules": ["q_proj"],
        },
    )
    (adapter / "adapter_model.safetensors").write_bytes(b"adapter")
    (adapter.parent / "STABLE").write_bytes(b"")
    adapter_identity = candidate_merge._tree_identity(adapter)  # noqa: SLF001
    receipt_core = {
        "schema": candidate_merge.TRAINING_RECEIPT_SCHEMA,
        "status": "ok",
        "source_step": 23,
        "final_step": 24,
        "optimizer_updates": 1,
        "assistant_tokens_only": True,
        "scientific_label": candidate_merge.EXPECTED_SCIENTIFIC_LABEL,
        "executor_git_sha": candidate_merge.EXPECTED_TRAINING_EXECUTOR_GIT_SHA,
        "replay_executor_git_sha": candidate_merge.EXPECTED_REPLAY_EXECUTOR_GIT_SHA,
        "replay_validator_git_sha": candidate_merge.EXPECTED_REPLAY_VALIDATOR_GIT_SHA,
        "learning_rate": candidate_merge.EXPECTED_LEARNING_RATE,
        "laptop_r00_used": True,
        "laptop_r01_used": False,
        "office_chair_used": False,
        "candidate": {
            "name": candidate_merge.EXPECTED_CANDIDATE_NAME,
            "path": str(adapter.resolve()),
            "update": 24,
            "files": adapter_identity["files"],
            "bytes": adapter_identity["bytes"],
            "tree_sha256": adapter_identity["tree_sha256"],
            "adapter_config_sha256": sha256_file(adapter / "adapter_config.json"),
            "stable_marker_sha256": sha256_file(adapter.parent / "STABLE"),
        },
    }
    receipt = {
        **receipt_core,
        "receipt_body_sha256": sha256_bytes(canonical_json(receipt_core).encode()),
    }
    receipt_path = tmp_path / "repair/training_receipt.json"
    _write_json(receipt_path, receipt)
    return parent, adapter, receipt_path, tmp_path / "repair/candidate_merge/receipt.json"


def _fake_merge(parent: Path, adapter: Path, output: Path, *, device: str) -> dict[str, object]:
    assert device == "cuda:0"
    output.mkdir(parents=True)
    _write_json(output / "config.json", {"architectures": ["Qwen3_5ForConditionalGeneration"]})
    (output / "model.safetensors").write_bytes(b"merged")
    provenance: dict[str, object] = {
        "schema": PARENT_MERGE_SCHEMA,
        "status": "ok",
        "parent_model": str(parent),
        "adapter_path": str(adapter),
        "output_dir": str(output),
        "parent_manifest": checkpoint_manifest(parent),
        "adapter_manifest": checkpoint_manifest(adapter),
        "parent_provenance_sha256": sha256_file(parent / "merge_provenance.json"),
        "behavioral_equivalence": {"metrics": {"adapter_merged_cosine": 1.0}},
        "logit_metrics": {"adapter_merged_max_abs": 0.0},
        "runtime": {"cuda_devices": 1},
    }
    _write_json(output / "merge_provenance.json", provenance)
    provenance["merged_manifest"] = checkpoint_manifest(output)
    _write_json(output / "merge_provenance.json", provenance)
    return provenance


def _pin_fixture(
    monkeypatch: pytest.MonkeyPatch,
    parent: Path,
    adapter: Path,
    training_receipt: Path,
    *,
    body_sha256: str | None = None,
) -> dict[str, str]:
    receipt = json.loads(training_receipt.read_text())
    pins = {
        "training_file": sha256_file(training_receipt),
        "training_body": body_sha256 or str(receipt["receipt_body_sha256"]),
        "parent_tree": str(candidate_merge._tree_identity(parent)["tree_sha256"]),  # noqa: SLF001
        "adapter_tree": str(candidate_merge._tree_identity(adapter)["tree_sha256"]),  # noqa: SLF001
    }
    monkeypatch.setattr(
        candidate_merge, "EXPECTED_TRAINING_RECEIPT_FILE_SHA256", pins["training_file"]
    )
    monkeypatch.setattr(
        candidate_merge, "EXPECTED_TRAINING_RECEIPT_BODY_SHA256", pins["training_body"]
    )
    monkeypatch.setattr(candidate_merge, "EXPECTED_PARENT_TREE_SHA256", pins["parent_tree"])
    monkeypatch.setattr(candidate_merge, "EXPECTED_ADAPTER_TREE_SHA256", pins["adapter_tree"])
    return pins


def _invoke(
    tmp_path: Path,
    parent: Path,
    adapter: Path,
    training_receipt: Path,
    merge_receipt: Path,
    pins: dict[str, str],
) -> dict[str, object]:
    return candidate_merge.freeze_repair_step24_candidate_merge(
        training_receipt_path=training_receipt,
        parent_model=parent,
        adapter_path=adapter,
        output_dir=tmp_path / "repair/candidate_merge/model",
        merge_receipt_path=merge_receipt,
        expected_training_receipt_file_sha256=pins["training_file"],
        expected_training_receipt_body_sha256=pins["training_body"],
        expected_parent_tree_sha256=pins["parent_tree"],
        expected_adapter_tree_sha256=pins["adapter_tree"],
        executor_git_sha=GIT_SHA,
        expected_container_image_digest=IMAGE,
    )


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    parent, adapter, training_receipt, merge_receipt = _tree(tmp_path)
    pins = _pin_fixture(monkeypatch, parent, adapter, training_receipt)
    monkeypatch.setattr(candidate_merge, "verify_parent_aware_merge", _fake_merge)
    monkeypatch.setenv("LAST_CONTAINER_IMAGE_DIGEST", IMAGE)
    monkeypatch.setenv("LAST_SOURCE_GIT_SHA", GIT_SHA)
    monkeypatch.setenv("LAST_ALLOCATED_GPUS", "1")
    return _invoke(tmp_path, parent, adapter, training_receipt, merge_receipt, pins)


def test_campaign_artifact_pins_are_exact() -> None:
    assert candidate_merge.EXPECTED_TRAINING_RECEIPT_FILE_SHA256 == (
        "85d5c779c8fb1ab4a1a87d4e4f4ab9bd7809675622bfe845a0632b2ab6ae4bf1"
    )
    assert candidate_merge.EXPECTED_TRAINING_RECEIPT_BODY_SHA256 == (
        "aa60d14b36e2bd5ae3cdab34cc74665ed883a03e59384f1174a17c624235fb1e"
    )
    assert candidate_merge.EXPECTED_PARENT_TREE_SHA256 == (
        "5939382fbc6db775972dc9cebf3e5be20654149a0412b4f00072bad12a2215ca"
    )
    assert candidate_merge.EXPECTED_ADAPTER_TREE_SHA256 == (
        "49e66d189603142232d0e4f1b3549d32b783ebed5fa4d3efd0e04af3197a5508"
    )


def test_freezes_prospective_correct_parent_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt = _run(tmp_path, monkeypatch)
    assert receipt["status"] == "ok"
    assert receipt["evaluation_outcomes_read"] is False
    assert receipt["candidate_selection_claimed"] is False
    assert receipt["source_step"] == 23
    assert receipt["final_step"] == 24
    assert receipt["optimizer_updates"] == 1
    candidate_merge._self_hash(  # noqa: SLF001
        receipt, "receipt_body_sha256", label="candidate merge receipt"
    )


def test_rejects_wrong_declared_parent_before_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, adapter, training_receipt, merge_receipt = _tree(tmp_path)
    config = json.loads((adapter / "adapter_config.json").read_text())
    config["base_model_name_or_path"] = str((tmp_path / "wrong-parent").resolve())
    _write_json(adapter / "adapter_config.json", config)
    pins = _pin_fixture(monkeypatch, parent, adapter, training_receipt)
    called = False

    def should_not_merge(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal called
        called = True
        raise AssertionError

    monkeypatch.setattr(candidate_merge, "verify_parent_aware_merge", should_not_merge)
    monkeypatch.setenv("LAST_CONTAINER_IMAGE_DIGEST", IMAGE)
    monkeypatch.setenv("LAST_SOURCE_GIT_SHA", GIT_SHA)
    monkeypatch.setenv("LAST_ALLOCATED_GPUS", "1")
    with pytest.raises(ArtifactError, match="adapter differs|wrong parent"):
        _invoke(tmp_path, parent, adapter, training_receipt, merge_receipt, pins)
    assert called is False


def test_rejects_training_receipt_body_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, adapter, training_receipt, merge_receipt = _tree(tmp_path)
    value = json.loads(training_receipt.read_text())
    expected_body = value["receipt_body_sha256"]
    value["optimizer_updates"] = 2
    _write_json(training_receipt, value)
    pins = _pin_fixture(monkeypatch, parent, adapter, training_receipt, body_sha256=expected_body)
    monkeypatch.setattr(candidate_merge, "verify_parent_aware_merge", _fake_merge)
    monkeypatch.setenv("LAST_CONTAINER_IMAGE_DIGEST", IMAGE)
    monkeypatch.setenv("LAST_SOURCE_GIT_SHA", GIT_SHA)
    monkeypatch.setenv("LAST_ALLOCATED_GPUS", "1")
    with pytest.raises(ArtifactError, match="self-hash"):
        _invoke(tmp_path, parent, adapter, training_receipt, merge_receipt, pins)


def test_rejects_unbound_runtime_before_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, adapter, training_receipt, merge_receipt = _tree(tmp_path)
    pins = _pin_fixture(monkeypatch, parent, adapter, training_receipt)
    called = False

    def should_not_merge(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal called
        called = True
        raise AssertionError

    monkeypatch.setattr(candidate_merge, "verify_parent_aware_merge", should_not_merge)
    monkeypatch.setenv("LAST_CONTAINER_IMAGE_DIGEST", IMAGE)
    monkeypatch.setenv("LAST_SOURCE_GIT_SHA", "f" * 40)
    monkeypatch.setenv("LAST_ALLOCATED_GPUS", "1")
    with pytest.raises(ArtifactError, match="runtime provenance"):
        _invoke(tmp_path, parent, adapter, training_receipt, merge_receipt, pins)
    assert called is False


def test_rejects_caller_supplied_artifact_identity_drift_before_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, adapter, training_receipt, merge_receipt = _tree(tmp_path)
    pins = _pin_fixture(monkeypatch, parent, adapter, training_receipt)
    monkeypatch.setattr(candidate_merge, "verify_parent_aware_merge", _fake_merge)
    monkeypatch.setenv("LAST_CONTAINER_IMAGE_DIGEST", IMAGE)
    monkeypatch.setenv("LAST_SOURCE_GIT_SHA", GIT_SHA)
    monkeypatch.setenv("LAST_ALLOCATED_GPUS", "1")
    pins["adapter_tree"] = "0" * 64
    with pytest.raises(ArtifactError, match="differ from campaign pins"):
        _invoke(tmp_path, parent, adapter, training_receipt, merge_receipt, pins)


def test_rejects_wrong_pinned_scientific_identity_before_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, adapter, training_receipt, merge_receipt = _tree(tmp_path)
    value = json.loads(training_receipt.read_text())
    core = {key: item for key, item in value.items() if key != "receipt_body_sha256"}
    core["scientific_label"] = "different-campaign"
    _write_json(
        training_receipt,
        {**core, "receipt_body_sha256": sha256_bytes(canonical_json(core).encode())},
    )
    pins = _pin_fixture(monkeypatch, parent, adapter, training_receipt)
    called = False

    def should_not_merge(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal called
        called = True
        raise AssertionError

    monkeypatch.setattr(candidate_merge, "verify_parent_aware_merge", should_not_merge)
    monkeypatch.setenv("LAST_CONTAINER_IMAGE_DIGEST", IMAGE)
    monkeypatch.setenv("LAST_SOURCE_GIT_SHA", GIT_SHA)
    monkeypatch.setenv("LAST_ALLOCATED_GPUS", "1")
    with pytest.raises(ArtifactError, match="exact repair step-24 adapter"):
        _invoke(tmp_path, parent, adapter, training_receipt, merge_receipt, pins)
    assert called is False


def test_rejects_parent_mutation_during_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, adapter, training_receipt, merge_receipt = _tree(tmp_path)
    pins = _pin_fixture(monkeypatch, parent, adapter, training_receipt)

    def mutating_merge(
        parent_path: Path, adapter_path: Path, output: Path, *, device: str
    ) -> dict[str, object]:
        result = _fake_merge(parent_path, adapter_path, output, device=device)
        (parent_path / "model.safetensors").write_bytes(b"mutated-parent")
        return result

    monkeypatch.setattr(candidate_merge, "verify_parent_aware_merge", mutating_merge)
    monkeypatch.setenv("LAST_CONTAINER_IMAGE_DIGEST", IMAGE)
    monkeypatch.setenv("LAST_SOURCE_GIT_SHA", GIT_SHA)
    monkeypatch.setenv("LAST_ALLOCATED_GPUS", "1")
    with pytest.raises(ArtifactError, match="parent tree binding changed"):
        _invoke(tmp_path, parent, adapter, training_receipt, merge_receipt, pins)
    assert not merge_receipt.exists()


def test_rejects_incomplete_parent_provenance_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, adapter, training_receipt, merge_receipt = _tree(tmp_path)
    pins = _pin_fixture(monkeypatch, parent, adapter, training_receipt)

    def incomplete_merge(
        parent_path: Path, adapter_path: Path, output: Path, *, device: str
    ) -> dict[str, object]:
        result = _fake_merge(parent_path, adapter_path, output, device=device)
        del result["parent_provenance_sha256"]
        return result

    monkeypatch.setattr(candidate_merge, "verify_parent_aware_merge", incomplete_merge)
    monkeypatch.setenv("LAST_CONTAINER_IMAGE_DIGEST", IMAGE)
    monkeypatch.setenv("LAST_SOURCE_GIT_SHA", GIT_SHA)
    monkeypatch.setenv("LAST_ALLOCATED_GPUS", "1")
    with pytest.raises(ArtifactError, match="provenance is incomplete"):
        _invoke(tmp_path, parent, adapter, training_receipt, merge_receipt, pins)
    assert not merge_receipt.exists()


@pytest.mark.parametrize("relation", ["ancestor", "descendant"])
def test_rejects_output_overlap_with_immutable_inputs_before_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relation: str
) -> None:
    parent, adapter, training_receipt, merge_receipt = _tree(tmp_path)
    pins = _pin_fixture(monkeypatch, parent, adapter, training_receipt)
    called = False

    def should_not_merge(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal called
        called = True
        raise AssertionError

    monkeypatch.setattr(candidate_merge, "verify_parent_aware_merge", should_not_merge)
    monkeypatch.setenv("LAST_CONTAINER_IMAGE_DIGEST", IMAGE)
    monkeypatch.setenv("LAST_SOURCE_GIT_SHA", GIT_SHA)
    monkeypatch.setenv("LAST_ALLOCATED_GPUS", "1")
    output = parent.parent if relation == "ancestor" else parent / "nested-output"
    with pytest.raises(ArtifactError, match="overlaps an immutable input tree"):
        candidate_merge.freeze_repair_step24_candidate_merge(
            training_receipt_path=training_receipt,
            parent_model=parent,
            adapter_path=adapter,
            output_dir=output,
            merge_receipt_path=merge_receipt,
            expected_training_receipt_file_sha256=pins["training_file"],
            expected_training_receipt_body_sha256=pins["training_body"],
            expected_parent_tree_sha256=pins["parent_tree"],
            expected_adapter_tree_sha256=pins["adapter_tree"],
            executor_git_sha=GIT_SHA,
            expected_container_image_digest=IMAGE,
        )
    assert called is False
