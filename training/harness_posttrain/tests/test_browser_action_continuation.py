from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from harness_posttrain.artifacts import (
    ArtifactError,
    canonical_json,
    sha256_bytes,
    sha256_file,
    tree_digest,
)
from harness_posttrain.browser_action_continuation import (
    CONTINUATION_PLAN_SCHEMA,
    prepare_browser_action_continuation,
)
from harness_posttrain.browser_action_continuation_receipt import (
    CONTINUATION_RECEIPT_SCHEMA,
    write_browser_action_continuation_receipt,
)
from harness_posttrain.browser_action_curriculum import CURRICULUM_SCHEMA
from harness_posttrain.config import Campaign


def _write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _source_campaign(tmp_path: Path, campaign: Campaign) -> tuple[Path, Path]:
    root = tmp_path / ("a" * 40)
    parent = root / "selected/merged"
    parent.mkdir(parents=True)
    provenance_path = _write_json(
        parent / "merge_provenance.json",
        {"status": "ok", "output_dir": str(parent.resolve())},
    )

    source_output = root / "refinement/config/prime_output"
    adapter = source_output / "weights/step_20/lora_adapters"
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
    (adapter / "adapter_model.safetensors").write_bytes(b"step-20-adapter")
    stable = adapter.parent / "STABLE"
    stable.write_bytes(b"")

    dcp = source_output / "checkpoints/step_20"
    (dcp / "trainer").mkdir(parents=True)
    (dcp / "trainer/rank_0.pt").write_bytes(b"model-progress-state")
    _write_json(dcp / ".metadata", {"step": 20})

    original_dataset_manifest = _write_json(
        root / "refinement/prime/manifest.json", {"stage": "refinement"}
    )
    config_path = root / "refinement/config/refinement.toml"
    config_path.write_text(
        "\n".join(
            [
                "max_steps = 20",
                f'output_dir = "{source_output.resolve()}"',
                "[model]",
                f'name = "{parent.resolve()}"',
                "[model.lora]",
                "rank = 64",
                "alpha = 128.0",
                "[ckpt]",
                "resume_step = -1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    plan = {
        "schema": "harness-posttrain.train-plan.v1",
        "stage": "refinement",
        "candidate": None,
        "campaign_digest": campaign.digest,
        "optimizer_updates": 20,
        "model": str(parent.resolve()),
        "config_sha256": sha256_file(config_path),
        "dataset_manifest_sha256": sha256_file(original_dataset_manifest),
    }
    plan_path = _write_json(root / "refinement/config/plan.json", plan)
    post = {
        "schema": "harness-posttrain.post-sft-receipt.v1",
        "status": "ok",
        "campaign_digest": campaign.digest,
        "merge_provenance_sha256": sha256_file(provenance_path),
        "refinement_config_sha256": sha256_file(config_path),
        "prime_manifest_sha256": sha256_file(original_dataset_manifest),
        "artifact_source_git_sha": root.name,
        "execution_source_git_sha": "d" * 40,
    }
    post_path = _write_json(root / "post_sft_receipt.json", post)
    adapter_files = [item for item in adapter.rglob("*") if item.is_file()]
    receipt = {
        "schema": "harness-posttrain.refinement-training-receipt.v1",
        "status": "ok",
        "campaign_digest": campaign.digest,
        "optimizer_updates": 20,
        "parent_model": str(parent.resolve()),
        "parent_merge_provenance_sha256": sha256_file(provenance_path),
        "post_sft_receipt_sha256": sha256_file(post_path),
        "plan_sha256": sha256_file(plan_path),
        "config_sha256": sha256_file(config_path),
        "dataset_manifest_sha256": sha256_file(original_dataset_manifest),
        "execution_source_git_sha": "e" * 40,
        "checkpoints": [
            {
                "update": 20,
                "path": str(adapter.resolve()),
                "files": len(adapter_files),
                "sha256": tree_digest(adapter_files, adapter),
                "stable_marker_sha256": sha256_file(stable),
            }
        ],
    }
    _write_json(root / "refinement/training_receipt.json", receipt)
    _write_json(
        root / "prep_receipt.json",
        {
            "schema": "harness-posttrain.prep-receipt.v1",
            "status": "ok",
            "campaign_digest": campaign.digest,
            "source_git_sha": root.name,
        },
    )
    return root, dcp


def _curriculum_and_prime(
    tmp_path: Path, campaign: Campaign
) -> tuple[Path, Path, Path]:
    curriculum_dir = tmp_path / "curriculum"
    source = curriculum_dir / "train.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text('{"messages":[]}\n', encoding="utf-8")
    body = {
        "schema": CURRICULUM_SCHEMA,
        "stage": "refinement",
        "method": "procedural_browseruse_agentoutput_transition_sft",
        "campaign_digest": campaign.digest,
        "assistant_wire_format": "browser-use AgentOutput.action JSON",
        "native_function_call_targets": 0,
        "heldout_amazon_scenarios_present": False,
        "output": {"path": source.name, "sha256": sha256_file(source), "rows": 1},
    }
    curriculum = dict(body)
    curriculum["manifest_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    curriculum_manifest = _write_json(curriculum_dir / "manifest.json", curriculum)

    model = tmp_path / "smoke-model"
    model.mkdir()
    smoke = _write_json(
        tmp_path / "smoke.json",
        {
            "status": "ok",
            "resolved_snapshot": str(model.resolve()),
            "snapshot": {
                "model_id": campaign.model["model_id"],
                "revision": campaign.model["revision"],
            },
            "target_selection": {
                "targets": [
                    "model.language_model.layers.0.self_attn.q_proj",
                    "model.language_model.layers.0.mlp.gate_proj",
                ]
            },
        },
    )
    prime = tmp_path / "prime"
    prime.mkdir()
    parquet = prime / "train.parquet"
    parquet.write_bytes(b"fixture-parquet")
    _write_json(
        prime / "manifest.json",
        {
            "schema": "harness-posttrain.prime-sft-dataset.v1",
            "stage": "refinement",
            "candidate": None,
            "campaign_digest": campaign.digest,
            "model_revision": campaign.model["revision"],
            "source": str(source.resolve()),
            "source_sha256": sha256_file(source),
            "source_manifest_sha256": sha256_file(curriculum_manifest),
            "parquet_sha256": sha256_file(parquet),
            "row_count": 1,
            "token_audit": {"seq_len": 32768, "global_batch_size": 16},
        },
    )
    return curriculum_manifest, prime, smoke


def test_prepare_copies_step20_and_emits_exact_resume_policy(
    tmp_path: Path, campaign: Campaign
) -> None:
    root, source_dcp = _source_campaign(tmp_path, campaign)
    curriculum, prime, smoke = _curriculum_and_prime(tmp_path, campaign)
    source_file = source_dcp / "trainer/rank_0.pt"
    source_bytes = source_file.read_bytes()
    source_mtime = source_file.stat().st_mtime_ns
    output = tmp_path / "continuation"

    result = prepare_browser_action_continuation(
        campaign,
        campaign_root=root,
        curriculum_manifest=curriculum,
        dataset_dir=prime,
        smoke_report=smoke,
        output_dir=output,
        num_gpus=4,
    )

    assert result["schema"] == CONTINUATION_PLAN_SCHEMA
    assert result["training"]["new_optimizer_updates"] == 8
    assert result["training"]["checkpoint_updates"] == [22, 24, 26, 28]
    assert result["source"]["step_20_dcp"]["tree_sha256"] == result[
        "copied_step_20_dcp"
    ]["tree_sha256"]
    assert source_file.read_bytes() == source_bytes
    assert source_file.stat().st_mtime_ns == source_mtime

    config = tomllib.loads(
        (output / "browser_action_continuation.toml").read_text(encoding="utf-8")
    )
    assert config["max_steps"] == 28
    assert config["optim"]["lr"] == pytest.approx(2e-6)
    assert config["scheduler"]["warmup_steps"] == 1
    assert config["ckpt"] == {
        "interval": 2,
        "resume_step": 20,
        "keep_last": 10,
        "skip_optimizer": True,
        "skip_scheduler": True,
        "skip_dataloader": True,
        "skip_progress": False,
        "weights": {
            "save_sharded": True,
            "save_format": "safetensors",
            "save_adapter_separately": True,
        },
    }
    assert (
        output / "prime_output/checkpoints/step_20/trainer/rank_0.pt"
    ).read_bytes() == source_bytes


def test_prepare_rejects_tampered_bound_adapter(
    tmp_path: Path, campaign: Campaign
) -> None:
    root, _source_dcp = _source_campaign(tmp_path, campaign)
    curriculum, prime, smoke = _curriculum_and_prime(tmp_path, campaign)
    adapter = root / "refinement/config/prime_output/weights/step_20/lora_adapters"
    (adapter / "adapter_model.safetensors").write_bytes(b"tampered")

    with pytest.raises(ArtifactError, match="adapter bytes changed"):
        prepare_browser_action_continuation(
            campaign,
            campaign_root=root,
            curriculum_manifest=curriculum,
            dataset_dir=prime,
            smoke_report=smoke,
            output_dir=tmp_path / "continuation",
        )


def test_prepare_rejects_drifted_curriculum_and_nonempty_output(
    tmp_path: Path, campaign: Campaign
) -> None:
    root, _source_dcp = _source_campaign(tmp_path, campaign)
    curriculum, prime, smoke = _curriculum_and_prime(tmp_path, campaign)
    value = json.loads(curriculum.read_text(encoding="utf-8"))
    value["native_function_call_targets"] = 1
    curriculum.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ArtifactError, match="curriculum manifest"):
        prepare_browser_action_continuation(
            campaign,
            campaign_root=root,
            curriculum_manifest=curriculum,
            dataset_dir=prime,
            smoke_report=smoke,
            output_dir=tmp_path / "continuation",
        )

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "existing").write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(ArtifactError, match="new empty directory"):
        prepare_browser_action_continuation(
            campaign,
            campaign_root=root,
            curriculum_manifest=curriculum,
            dataset_dir=prime,
            smoke_report=smoke,
            output_dir=occupied,
        )


def _completed_continuation(
    tmp_path: Path, campaign: Campaign
) -> tuple[Path, Path, str, str]:
    root, _source_dcp = _source_campaign(tmp_path, campaign)
    artifact_source = "b" * 40
    execution_source = "c" * 40
    stage = root / "browser_action_correction" / artifact_source
    curriculum, prime, smoke = _curriculum_and_prime(stage, campaign)
    expected_smoke = root / "smoke/smoke_report.json"
    expected_smoke.parent.mkdir(parents=True)
    expected_smoke.write_bytes(smoke.read_bytes())
    continuation = stage / "training"
    prepare_browser_action_continuation(
        campaign,
        campaign_root=root,
        curriculum_manifest=curriculum,
        dataset_dir=prime,
        smoke_report=expected_smoke,
        output_dir=continuation,
        num_gpus=4,
    )
    parent = (root / "selected/merged").resolve()
    targets = tomllib.loads(
        (continuation / "browser_action_continuation.toml").read_text(encoding="utf-8")
    )["model"]["lora"]["target_modules"]
    serialized_target_names = sorted(
        {target.removesuffix("$").rsplit("\\.", 1)[-1] for target in targets}
    )
    for update in (22, 24, 26, 28):
        adapter = continuation / f"prime_output/weights/step_{update}/lora_adapters"
        adapter.mkdir(parents=True)
        _write_json(
            adapter / "adapter_config.json",
            {
                "base_model_name_or_path": str(parent),
                "r": 64,
                "lora_alpha": 128,
                "target_modules": serialized_target_names,
            },
        )
        (adapter / "adapter_model.safetensors").write_bytes(
            f"adapter-{update}".encode()
        )
        (adapter.parent / "STABLE").write_bytes(b"")
    return root, continuation, artifact_source, execution_source


def test_continuation_receipt_attests_exact_resume_and_stable_adapters(
    tmp_path: Path, campaign: Campaign
) -> None:
    root, continuation, artifact_source, execution_source = _completed_continuation(
        tmp_path, campaign
    )
    original_adapter = (
        root
        / "refinement/config/prime_output/weights/step_20/lora_adapters/adapter_model.safetensors"
    )
    original_dcp = (
        root / "refinement/config/prime_output/checkpoints/step_20/trainer/rank_0.pt"
    )
    before = {
        "adapter": (sha256_file(original_adapter), original_adapter.stat().st_mtime_ns),
        "dcp": (sha256_file(original_dcp), original_dcp.stat().st_mtime_ns),
    }

    receipt = write_browser_action_continuation_receipt(
        campaign,
        campaign_root=root,
        continuation_dir=continuation,
        artifact_source_git_sha=artifact_source,
        execution_source_git_sha=execution_source,
    )

    assert receipt["schema"] == CONTINUATION_RECEIPT_SCHEMA
    assert receipt["artifact_source_git_sha"] == artifact_source
    assert receipt["execution_source_git_sha"] == execution_source
    assert receipt["checkpoint_updates"] == [22, 24, 26, 28]
    assert [row["update"] for row in receipt["checkpoints"]] == [22, 24, 26, 28]
    assert all(row["files"] == 2 for row in receipt["checkpoints"])
    assert all(row["bytes"] > 0 for row in receipt["checkpoints"])
    assert receipt["restore_policy"] == {
        "resume_step": 20,
        "restore_model": True,
        "restore_progress": True,
        "restore_optimizer": False,
        "restore_scheduler": False,
        "restore_dataloader": False,
        "skip_optimizer": True,
        "skip_scheduler": True,
        "skip_dataloader": True,
        "skip_progress": False,
    }
    assert receipt["source"]["verified_unchanged_before_and_after"] is True
    assert before == {
        "adapter": (sha256_file(original_adapter), original_adapter.stat().st_mtime_ns),
        "dcp": (sha256_file(original_dcp), original_dcp.stat().st_mtime_ns),
    }
    assert json.loads(
        (continuation / "training_receipt.json").read_text(encoding="utf-8")
    ) == receipt
    # Create-only publication permits a byte-identical retry.
    assert (
        write_browser_action_continuation_receipt(
            campaign,
            campaign_root=root,
            continuation_dir=continuation,
            artifact_source_git_sha=artifact_source,
            execution_source_git_sha=execution_source,
        )
        == receipt
    )


def test_continuation_receipt_rejects_restore_drift_and_wrong_parent(
    tmp_path: Path, campaign: Campaign
) -> None:
    root, continuation, artifact_source, execution_source = _completed_continuation(
        tmp_path, campaign
    )
    config_path = continuation / "browser_action_continuation.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "skip_progress = false", "skip_progress = true"
        ),
        encoding="utf-8",
    )
    plan_path = continuation / "plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["config_sha256"] = sha256_file(config_path)
    _write_json(plan_path, plan)
    with pytest.raises(ArtifactError, match="restore policy"):
        write_browser_action_continuation_receipt(
            campaign,
            campaign_root=root,
            continuation_dir=continuation,
            artifact_source_git_sha=artifact_source,
            execution_source_git_sha=execution_source,
        )

    root2, continuation2, artifact_source2, execution_source2 = _completed_continuation(
        tmp_path / "second", campaign
    )
    adapter = continuation2 / "prime_output/weights/step_24/lora_adapters"
    value = json.loads((adapter / "adapter_config.json").read_text(encoding="utf-8"))
    value["base_model_name_or_path"] = str(tmp_path / "wrong-parent")
    _write_json(adapter / "adapter_config.json", value)
    with pytest.raises(ArtifactError, match="parent or LoRA shape"):
        write_browser_action_continuation_receipt(
            campaign,
            campaign_root=root2,
            continuation_dir=continuation2,
            artifact_source_git_sha=artifact_source2,
            execution_source_git_sha=execution_source2,
        )


def test_continuation_receipt_rejects_source_or_artifact_identity_drift(
    tmp_path: Path, campaign: Campaign
) -> None:
    root, continuation, artifact_source, execution_source = _completed_continuation(
        tmp_path, campaign
    )
    source_dcp = (
        root / "refinement/config/prime_output/checkpoints/step_20/trainer/rank_0.pt"
    )
    source_dcp.write_bytes(b"mutated-after-preparation")
    with pytest.raises(ArtifactError, match="source step-20 DCP"):
        write_browser_action_continuation_receipt(
            campaign,
            campaign_root=root,
            continuation_dir=continuation,
            artifact_source_git_sha=artifact_source,
            execution_source_git_sha=execution_source,
        )

    root2, continuation2, _artifact_source2, execution_source2 = _completed_continuation(
        tmp_path / "second", campaign
    )
    with pytest.raises(ArtifactError, match="stage path"):
        write_browser_action_continuation_receipt(
            campaign,
            campaign_root=root2,
            continuation_dir=continuation2,
            artifact_source_git_sha="f" * 40,
            execution_source_git_sha=execution_source2,
        )
