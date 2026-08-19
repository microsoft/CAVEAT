from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from harness_posttrain import exact_lora
from harness_posttrain.artifacts import ArtifactError, read_json
from harness_posttrain.config import Campaign
from harness_posttrain.exact_lora import (
    component_identity,
    create_zero_adapter,
    exact_lora_composite_sha256,
    finalize_exact_lora,
    tokenizer_semantic_equivalence,
    zero_adapter_config,
)
from harness_posttrain.receipts import write_refinement_receipt


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _refinement_tree(tmp_path: Path, campaign: Campaign) -> Path:
    root = tmp_path / "campaign"
    parent = root / "selected/merged"
    parent.mkdir(parents=True)
    (parent / "merge_provenance.json").write_text('{"status":"ok"}\n', encoding="utf-8")
    (parent / "config.json").write_text("{}\n", encoding="utf-8")
    (parent / "model.safetensors").write_bytes(b"selected-parent")
    (parent / "tokenizer.json").write_text("{}\n", encoding="utf-8")
    dataset = root / "refinement/prime/manifest.json"
    dataset.parent.mkdir(parents=True)
    dataset.write_text('{"stage":"refinement"}\n', encoding="utf-8")
    output = root / "refinement/config/prime_output"
    config = root / "refinement/config/refinement.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        "\n".join(
            [
                "max_steps = 20",
                f'output_dir = "{output.resolve()}"',
                "[deployment]",
                "num_gpus = 4",
                "gpus_per_node = 4",
                "[model]",
                f'name = "{parent.resolve()}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    candidates = []
    for update in (5, 10, 20):
        adapter = output / f"weights/step_{update}/lora_adapters"
        adapter.mkdir(parents=True)
        (adapter / "adapter_config.json").write_text(
            json.dumps(
                {
                    "base_model_name_or_path": str(parent.resolve()),
                    "r": 64,
                    "lora_alpha": 128,
                    "target_modules": ["q_proj"],
                    "nested": {"use_dora": False},
                }
            ),
            encoding="utf-8",
        )
        (adapter / "adapter_model.safetensors").write_bytes(f"step-{update}".encode())
        (adapter.parent / "STABLE").write_bytes(b"")
        candidates.append({"update": update, "adapter": str(adapter.resolve())})
    plan = {
        "schema": "harness-posttrain.train-plan.v1",
        "stage": "refinement",
        "candidate": None,
        "campaign_digest": campaign.digest,
        "model": str(parent.resolve()),
        "model_revision": campaign.model["revision"],
        "optimizer_updates": 20,
        "config_sha256": _sha(config),
        "dataset_manifest_sha256": _sha(dataset),
        "candidates": candidates,
    }
    (root / "refinement/config/plan.json").write_text(json.dumps(plan), encoding="utf-8")
    post = {
        "schema": "harness-posttrain.post-sft-receipt.v1",
        "status": "ok",
        "campaign_digest": campaign.digest,
        "merge_provenance_sha256": _sha(parent / "merge_provenance.json"),
        "refinement_config_sha256": _sha(config),
        "prime_manifest_sha256": _sha(dataset),
    }
    (root / "post_sft_receipt.json").write_text(json.dumps(post), encoding="utf-8")
    write_refinement_receipt(campaign, root)
    return root


def _raw_base(root: Path, campaign: Campaign) -> Path:
    raw = root / "raw" / campaign.model["revision"]
    raw.mkdir(parents=True)
    (raw / "config.json").write_text("{}\n", encoding="utf-8")
    (raw / "model.safetensors").write_bytes(b"raw-base")
    (raw / "tokenizer.json").write_text("{}\n", encoding="utf-8")
    smoke = root / "smoke/smoke_report.json"
    smoke.parent.mkdir(parents=True)
    smoke.write_text(
        json.dumps(
            {
                "status": "ok",
                "resolved_snapshot": str(raw.resolve()),
                "snapshot": {
                    "model_id": campaign.model["model_id"],
                    "revision": campaign.model["revision"],
                },
            }
        ),
        encoding="utf-8",
    )
    return raw


def _inventory(*, all_zero: bool) -> dict[str, Any]:
    tensors = {
        "base_model.model.q_proj.lora_A.weight": {
            "shape": [64, 32],
            "dtype": "bfloat16",
            "elements": 2048,
            "bytes": 4096,
        },
        "base_model.model.q_proj.lora_B.weight": {
            "shape": [32, 64],
            "dtype": "bfloat16",
            "elements": 2048,
            "bytes": 4096,
        },
    }
    return {
        "tensors": tensors,
        "tensor_count": 2,
        "element_count": 4096,
        "byte_count": 8192,
        "nonzero_elements": 0 if all_zero else 17,
        "all_zero": all_zero,
        "metadata": {"format": "pt"},
        "inventory_sha256": "a" * 64,
    }


def _fake_tensor_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def inventory(path: Path) -> dict[str, Any]:
        return _inventory(all_zero=path.read_bytes() == b"zero")

    def save_zero(_source: Path, output: Path) -> None:
        output.write_bytes(b"zero")

    monkeypatch.setattr(exact_lora, "_tensor_inventory", inventory)
    monkeypatch.setattr(exact_lora, "_save_zero_weights", save_zero)


def _fake_zero_builder(
    source_adapter: Path, raw_base: Path, output_dir: Path
) -> dict[str, Any]:
    source = Path(source_adapter)
    raw = Path(raw_base)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    source_config = read_json(source / "adapter_config.json")
    derived = zero_adapter_config(source_config, raw)
    (output / "adapter_config.json").write_text(json.dumps(derived), encoding="utf-8")
    (output / "adapter_model.safetensors").write_bytes(b"zero")
    return {
        "schema": exact_lora.ZERO_ADAPTER_SCHEMA,
        "status": "ok",
        "source_config": source_config,
        "zero_config": derived,
        "source_tensor_inventory": _inventory(all_zero=False),
        "zero_tensor_inventory": _inventory(all_zero=True),
    }


def _behavior() -> dict[str, Any]:
    return {
        "status": "ok",
        "input_ids_sha256": "1" * 64,
        "chat_template_sha256": "2" * 64,
        "tokenizer_json_sha256": "3" * 64,
        "tokenizer_semantic_equivalence": {
            "status": "ok",
            "exact_loaded_semantics_equal": True,
            "raw_tokenizer_json_sha256": "6" * 64,
            "shared_tokenizer_json_sha256": "3" * 64,
            "serialized_bytes_equal": False,
            "chat_template_sha256": "2" * 64,
            "semantic_sha256": "7" * 64,
        },
        "processor_class": "transformers.AutoProcessor",
        "zero_noop": {
            "exact_logits_equal": True,
            "maximum_absolute_logit_difference": 0.0,
        },
        "trained_nonnoop": {
            "allclose": False,
            "maximum_absolute_logit_difference": 0.25,
        },
        "merge_failure_diagnostic": {
            "status": "rejected",
            "checks": {
                "relative_l2_error_ratio": {"passed": False},
                "adapter_merged_relative_l2": {"passed": True},
            },
        },
    }


class _FakeBackend:
    def __init__(self, value: dict[str, Any]) -> None:
        self.value = value

    def to_str(self, *, pretty: bool = False) -> str:
        assert pretty is False
        return json.dumps(self.value)


class _FakeTokenizer:
    vocab_size = 2
    all_special_ids = [1]
    all_special_tokens = ["<special>"]
    special_tokens_map = {"eos_token": "<special>"}
    chat_template = "{{ messages }}"

    def __init__(
        self,
        *,
        backend: dict[str, Any] | None = None,
        name: str,
        setting: str = "same",
    ) -> None:
        self.backend_tokenizer = _FakeBackend(
            backend or {"model": {"vocab": {"a": 0, "<special>": 1}}}
        )
        self.init_kwargs = {
            "name_or_path": name,
            "vocab_file": f"{name}/vocab.json",
            "merges_file": f"{name}/merges.txt",
            "setting": setting,
        }

    def __len__(self) -> int:
        return 2

    @staticmethod
    def get_vocab() -> dict[str, int]:
        return {"a": 0, "<special>": 1}

    @staticmethod
    def get_added_vocab() -> dict[str, int]:
        return {"<special>": 1}


def test_tokenizer_semantic_gate_accepts_exact_runtime_after_serialization_drift(
    tmp_path: Path,
) -> None:
    raw_file = tmp_path / "raw-tokenizer.json"
    shared_file = tmp_path / "shared-tokenizer.json"
    raw_file.write_text('{"old":"serialization"}\n', encoding="utf-8")
    shared_file.write_text('{"new":"serialization"}\n', encoding="utf-8")
    result = tokenizer_semantic_equivalence(
        _FakeTokenizer(name="/raw"),
        _FakeTokenizer(name="/selected"),
        raw_tokenizer_json=raw_file,
        shared_tokenizer_json=shared_file,
    )
    assert result["status"] == "ok"
    assert result["exact_loaded_semantics_equal"] is True
    assert result["serialized_bytes_equal"] is False
    assert result["raw_tokenizer_json_sha256"] == _sha(raw_file)
    assert result["shared_tokenizer_json_sha256"] == _sha(shared_file)
    assert len(result["semantic_sha256"]) == 64


def test_tokenizer_semantic_gate_rejects_backend_or_wrapper_drift(tmp_path: Path) -> None:
    raw_file = tmp_path / "raw-tokenizer.json"
    shared_file = tmp_path / "shared-tokenizer.json"
    raw_file.write_text("{}\n", encoding="utf-8")
    shared_file.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match="backend graph"):
        tokenizer_semantic_equivalence(
            _FakeTokenizer(name="/raw"),
            _FakeTokenizer(name="/selected", backend={"model": {"vocab": {"b": 0}}}),
            raw_tokenizer_json=raw_file,
            shared_tokenizer_json=shared_file,
        )
    with pytest.raises(ArtifactError, match="wrapper settings"):
        tokenizer_semantic_equivalence(
            _FakeTokenizer(name="/raw"),
            _FakeTokenizer(name="/selected", setting="changed"),
            raw_tokenizer_json=raw_file,
            shared_tokenizer_json=shared_file,
        )


def test_zero_config_changes_only_declared_parent(tmp_path: Path) -> None:
    source = {
        "base_model_name_or_path": "/selected/merged",
        "r": 64,
        "lora_alpha": 128,
        "target_modules": ["q_proj", "k_proj"],
        "nested": {"enabled": True, "scale": 1.0, "value": None},
    }
    raw = tmp_path / "raw"
    result = zero_adapter_config(source, raw)
    assert result["base_model_name_or_path"] == str(raw.resolve())
    assert {key: value for key, value in result.items() if key != "base_model_name_or_path"} == {
        key: value for key, value in source.items() if key != "base_model_name_or_path"
    }
    assert source["base_model_name_or_path"] == "/selected/merged"


def test_zero_adapter_is_atomic_exact_shape_and_source_preserving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_tensor_io(monkeypatch)
    source = tmp_path / "trained"
    source.mkdir()
    config = {
        "base_model_name_or_path": "/selected/merged",
        "r": 64,
        "lora_alpha": 128,
        "target_modules": ["q_proj"],
    }
    (source / "adapter_config.json").write_text(json.dumps(config), encoding="utf-8")
    weights = source / "adapter_model.safetensors"
    weights.write_bytes(b"trained")
    raw = tmp_path / "raw"
    raw.mkdir()
    output = tmp_path / "published-zero"
    source_before = {item.name: item.read_bytes() for item in source.iterdir()}

    report = create_zero_adapter(source, raw, output)
    assert report["status"] == "ok"
    assert report["zero_tensor_inventory"]["all_zero"] is True
    assert report["source_tensor_inventory"]["tensors"] == report["zero_tensor_inventory"][
        "tensors"
    ]
    assert read_json(output / "adapter_config.json")["base_model_name_or_path"] == str(
        raw.resolve()
    )
    assert weights.read_bytes() == b"trained"
    assert {item.name: item.read_bytes() for item in source.iterdir()} == source_before
    assert create_zero_adapter(source, raw, output)["status"] == "ok"


def test_zero_adapter_rejects_inventory_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "trained"
    source.mkdir()
    (source / "adapter_config.json").write_text(
        json.dumps({"base_model_name_or_path": "/parent", "r": 64}), encoding="utf-8"
    )
    (source / "adapter_model.safetensors").write_bytes(b"trained")
    raw = tmp_path / "raw"
    raw.mkdir()

    def inventory(path: Path) -> dict[str, Any]:
        result = _inventory(all_zero=path.read_bytes() == b"zero")
        if result["all_zero"]:
            result["tensors"] = {"wrong.key": next(iter(result["tensors"].values()))}
        return result

    monkeypatch.setattr(exact_lora, "_tensor_inventory", inventory)
    monkeypatch.setattr(
        exact_lora, "_save_zero_weights", lambda _source, output: output.write_bytes(b"zero")
    )
    with pytest.raises(ArtifactError, match="tensor inventory differs"):
        create_zero_adapter(source, raw, tmp_path / "zero")
    assert not (tmp_path / "zero").exists()


def test_component_identity_uses_logical_bytes_for_file_symlinks(tmp_path: Path) -> None:
    blob = tmp_path / "blob"
    blob.write_bytes(b"same-content")
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "weights.bin").symlink_to(blob)
    copied = tmp_path / "copied"
    copied.mkdir()
    (copied / "weights.bin").write_bytes(b"same-content")
    assert component_identity(linked)["tree_sha256"] == component_identity(copied)[
        "tree_sha256"
    ]


def test_component_identity_rejects_directory_symlink(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file").write_bytes(b"x")
    component = tmp_path / "component"
    component.mkdir()
    (component / "linked-directory").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ArtifactError, match="directory symlink"):
        component_identity(component)


def test_merge_diagnostic_exposes_ratio_failure_without_weakening_absolute_gates() -> None:
    metrics = {
        "adapter_merged_relative_l2": 0.01,
        "adapter_merged_cosine": 0.999,
        "adapter_merged_symmetric_kl": 0.001,
        "adapter_merged_top5_overlap": 1.0,
        "relative_l2_error_ratio": 0.884,
        "symmetric_kl_error_ratio": 0.2,
    }
    checks = exact_lora._merge_gate_checks(metrics)  # noqa: SLF001
    assert checks["adapter_merged_relative_l2"]["passed"] is True
    assert checks["adapter_merged_cosine"]["passed"] is True
    assert checks["relative_l2_error_ratio"]["passed"] is False
    assert checks["relative_l2_error_ratio"]["threshold"] == 0.5


def test_exact_lora_composite_sha256_fixed_vector() -> None:
    assert exact_lora_composite_sha256(
        parent_tree_sha256="1" * 64,
        adapter_tree_sha256="2" * 64,
        adapter_config_sha256="3" * 64,
        tokenizer_json_sha256="4" * 64,
        chat_template_sha256="5" * 64,
        dtype="bfloat16",
    ) == "c1346da9574a5d0a2e5e444f460588cb36f47048c866e96c184ef6d7c3acc85d"


def test_behavior_report_requires_zero_noop_trained_nonnoop_and_merge_rejection() -> None:
    exact_lora._validate_behavior_report(_behavior())  # noqa: SLF001
    invalid = _behavior()
    invalid["trained_nonnoop"]["maximum_absolute_logit_difference"] = 0.0
    with pytest.raises(ArtifactError, match="behavioral attestation"):
        exact_lora._validate_behavior_report(invalid)  # noqa: SLF001
    invalid = _behavior()
    invalid["merge_failure_diagnostic"]["checks"] = {"all": {"passed": True}}
    with pytest.raises(ArtifactError, match="no reproduced merge-gate failure"):
        exact_lora._validate_behavior_report(invalid)  # noqa: SLF001


def test_finalize_exact_lora_publishes_two_hash_bound_composites_without_model(
    tmp_path: Path, campaign: Campaign, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _refinement_tree(tmp_path, campaign)
    raw = _raw_base(root, campaign)
    monkeypatch.setattr(exact_lora, "_validate_parent", lambda _parent: {"status": "ok"})
    monkeypatch.setattr(exact_lora, "create_zero_adapter", _fake_zero_builder)
    monkeypatch.setattr(
        exact_lora,
        "verify_exact_lora_behavior",
        lambda *_args, **_kwargs: _behavior(),
    )

    result = finalize_exact_lora(campaign, campaign_root=root, raw_base=raw)
    assert result["status"] == "ok"
    assert result["serving_mode"] == "exact_peft_lora"
    assert result["final_model_directory_published"] is False
    assert result["arms"]["base"]["composite_sha256"] != result["arms"]["trained"][
        "composite_sha256"
    ]
    assert result["zero_adapter_attestation"]["zero_tensor_inventory"]["all_zero"] is True
    assert result["zero_adapter_attestation"]["source_tensor_inventory"]["all_zero"] is False
    assert result["behavioral_attestation"]["merge_failure_diagnostic"]["status"] == (
        "rejected"
    )
    assert not (root / "final/model").exists()
    assert not (root / "final/inference_manifest.json").exists()
    assert read_json(root / "final/exact_lora_manifest.json") == result
    manifest_receipt = read_json(root / "final/exact_lora_manifest_receipt.json")
    assert manifest_receipt["status"] == "ok"
    assert manifest_receipt["manifest_sha256"] == _sha(root / "final/exact_lora_manifest.json")
    assert manifest_receipt["composite_pair_sha256"] == result["composite_pair_sha256"]


def test_finalize_exact_lora_detects_receipt_mutation_during_attestation(
    tmp_path: Path, campaign: Campaign, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _refinement_tree(tmp_path, campaign)
    raw = _raw_base(root, campaign)
    receipt = root / "refinement/training_receipt.json"
    monkeypatch.setattr(exact_lora, "_validate_parent", lambda _parent: {"status": "ok"})
    monkeypatch.setattr(exact_lora, "create_zero_adapter", _fake_zero_builder)

    def mutate_receipt(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        value = read_json(receipt)
        value["tampered"] = True
        receipt.write_text(json.dumps(value), encoding="utf-8")
        return _behavior()

    monkeypatch.setattr(exact_lora, "verify_exact_lora_behavior", mutate_receipt)
    with pytest.raises(ArtifactError, match="receipt changed"):
        finalize_exact_lora(campaign, campaign_root=root, raw_base=raw)
    assert not (root / "final/exact_lora_manifest.json").exists()


def test_finalize_exact_lora_refuses_ambiguous_merged_publication(
    tmp_path: Path, campaign: Campaign
) -> None:
    root = _refinement_tree(tmp_path, campaign)
    raw = _raw_base(root, campaign)
    (root / "final/model").mkdir(parents=True)
    with pytest.raises(ArtifactError, match="beside a merged final model"):
        finalize_exact_lora(campaign, campaign_root=root, raw_base=raw)
