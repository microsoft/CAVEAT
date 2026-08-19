from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness_posttrain import finalize, parent_merge
from harness_posttrain.artifacts import ArtifactError
from harness_posttrain.config import Campaign
from harness_posttrain.finalize import finalize_refinement
from harness_posttrain.parent_merge import (
    PARENT_MERGE_SCHEMA,
    ParentLogitMetrics,
    checkpoint_manifest,
    validate_parent_logit_metrics,
)
from harness_posttrain.receipts import write_refinement_receipt


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _refinement_tree(tmp_path: Path, campaign: Campaign) -> Path:
    root = tmp_path / "campaign"
    parent = root / "selected/merged"
    parent.mkdir(parents=True)
    (parent / "merge_provenance.json").write_text('{"status":"ok"}\n', encoding="utf-8")
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
    plan_path = root / "refinement/config/plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    post = {
        "schema": "harness-posttrain.post-sft-receipt.v1",
        "status": "ok",
        "campaign_digest": campaign.digest,
        "merge_provenance_sha256": _sha(parent / "merge_provenance.json"),
        "refinement_config_sha256": _sha(config),
        "prime_manifest_sha256": _sha(dataset),
    }
    (root / "post_sft_receipt.json").write_text(json.dumps(post), encoding="utf-8")
    return root


def test_refinement_receipt_attests_step_20_and_parent(tmp_path: Path, campaign: Campaign) -> None:
    root = _refinement_tree(tmp_path, campaign)
    receipt = write_refinement_receipt(campaign, root)
    assert receipt["status"] == "ok"
    assert receipt["optimizer_updates"] == 20
    assert receipt["num_gpus"] == 4
    assert [row["update"] for row in receipt["checkpoints"]] == [5, 10, 20]
    assert Path(receipt["checkpoints"][-1]["path"]).name == "lora_adapters"


def test_refinement_receipt_rejects_raw_base_parent(tmp_path: Path, campaign: Campaign) -> None:
    root = _refinement_tree(tmp_path, campaign)
    adapter_config = (
        root / "refinement/config/prime_output/weights/step_20/lora_adapters/adapter_config.json"
    )
    adapter_config.write_text(
        json.dumps(
            {
                "base_model_name_or_path": campaign.model["model_id"],
                "r": 64,
                "lora_alpha": 128,
                "target_modules": ["q_proj"],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactError, match="selected SFT parent"):
        write_refinement_receipt(campaign, root)


def test_finalize_emits_eval_manifest_bound_to_parent(
    tmp_path: Path, campaign: Campaign, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _refinement_tree(tmp_path, campaign)
    receipt = write_refinement_receipt(campaign, root)
    parent = (root / "selected/merged").resolve()
    adapter = Path(receipt["checkpoints"][-1]["path"]).resolve()

    def fake_merge(
        parent_model: Path, adapter_path: Path, output_dir: Path, *, device: str
    ) -> dict[str, object]:
        assert Path(parent_model).resolve() == parent
        assert Path(adapter_path).resolve() == adapter
        assert device == "cuda:0"
        output = Path(output_dir)
        output.mkdir(parents=True)
        (output / "config.json").write_text("{}\n", encoding="utf-8")
        (output / "model.safetensors").write_bytes(b"final")
        manifest = checkpoint_manifest(output)
        provenance = {
            "schema": PARENT_MERGE_SCHEMA,
            "status": "ok",
            "parent_model": str(parent),
            "adapter_path": str(adapter),
            "merged_manifest": manifest,
        }
        (output / "merge_provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
        return provenance

    monkeypatch.setattr(finalize, "verify_parent_aware_merge", fake_merge)
    result = finalize_refinement(campaign, campaign_root=root)
    assert result["status"] == "ok"
    assert result["parent_model"] == str(parent)
    assert result["refinement_update"] == 20
    assert result["inference"]["reasoning_parser"] == "qwen3"
    assert (root / "final/inference_manifest.json").is_file()


def test_parent_metric_guard_uses_parent_not_raw_base() -> None:
    validate_parent_logit_metrics(
        ParentLogitMetrics(
            parent_adapter_max_abs=0.2,
            parent_merged_max_abs=0.2,
            adapter_merged_max_abs=0.001,
            merged_reload_max_abs=0.001,
            adapter_abs_max=10.0,
            merged_abs_max=10.0,
        ),
        nonnoop_atol=1e-7,
        reload_atol=0.05,
        reload_rtol=0.005,
    )
    with pytest.raises(ArtifactError, match="relative to its parent"):
        validate_parent_logit_metrics(
            ParentLogitMetrics(0.0, 0.2, 0.0, 0.0, 10.0, 10.0),
            nonnoop_atol=1e-7,
            reload_atol=0.05,
            reload_rtol=0.005,
        )


def test_parent_merge_uses_shared_bf16_behavioral_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    evidence = object()

    class FakeVerifier:
        class MergeVerificationError(RuntimeError):
            pass

        @staticmethod
        def behavioral_equivalence_metrics(parent, adapter, merged):
            calls.append(("metrics", parent, adapter, merged))
            return evidence

        @staticmethod
        def validate_behavioral_equivalence(metrics, **thresholds):
            calls.append(("validate", metrics, thresholds))

    monkeypatch.setattr(
        parent_merge.importlib,
        "import_module",
        lambda name: FakeVerifier if name == "harness_distill.model_merge" else None,
    )
    result = parent_merge._validate_behavioral_merge(  # noqa: SLF001
        "selected-parent",
        "refinement-adapter",
        "final-merged",
        max_relative_l2=0.10,
        min_cosine=0.99,
        max_symmetric_kl=0.01,
        min_top5_overlap=0.8,
        max_error_ratio=0.5,
    )
    assert result is evidence
    assert calls[0] == (
        "metrics",
        "selected-parent",
        "refinement-adapter",
        "final-merged",
    )
    assert calls[1][2] == {
        "max_relative_l2": 0.10,
        "min_cosine": 0.99,
        "max_symmetric_kl": 0.01,
        "min_top5_overlap": 0.8,
        "max_error_ratio": 0.5,
    }


def test_parent_merge_translates_shared_behavioral_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RejectingVerifier:
        class MergeVerificationError(RuntimeError):
            pass

        @staticmethod
        def behavioral_equivalence_metrics(*_values):
            return object()

        @classmethod
        def validate_behavioral_equivalence(cls, *_args, **_kwargs):
            raise cls.MergeVerificationError("ratio failed")

    monkeypatch.setattr(parent_merge.importlib, "import_module", lambda _name: RejectingVerifier)
    with pytest.raises(ArtifactError, match="changed adapter behavior: ratio failed"):
        parent_merge._validate_behavioral_merge(  # noqa: SLF001
            object(),
            object(),
            object(),
            max_relative_l2=0.10,
            min_cosine=0.99,
            max_symmetric_kl=0.01,
            min_top5_overlap=0.8,
            max_error_ratio=0.5,
        )
