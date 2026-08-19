from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from harness_posttrain.artifacts import ArtifactError, sha256_file
from harness_posttrain.browser_action_continuation import _tree_identity
from harness_posttrain.browser_action_fixed_v6 import (
    _fixed_toml,
    _hardlink_tree,
    _token_mix_audit,
    write_browser_action_fixed_v6_receipt,
)


def _settings() -> dict[str, int | float]:
    return {
        "sequence_length": 32768,
        "global_batch_size": 16,
        "lora_rank": 64,
        "lora_alpha": 128,
        "lora_dropout": 0,
        "maximum_gradient_norm": 1,
    }


def test_fixed_v6_config_is_single_candidate_lr3e6_step24(tmp_path: Path) -> None:
    payload = _fixed_toml(
        model=tmp_path / "model",
        dataset=tmp_path / "data",
        output=tmp_path / "output",
        targets=["^q_proj$"],
        settings=_settings(),
        seed=17,
        num_gpus=4,
    )
    config = tomllib.loads(payload.decode())

    assert config["max_steps"] == 24
    assert config["optim"]["lr"] == pytest.approx(3e-6)
    assert config["scheduler"] == {
        "type": "cosine",
        "warmup_steps": 0,
        "min_lr": pytest.approx(3e-6),
    }
    assert config["ckpt"]["resume_step"] == 20
    assert config["ckpt"]["interval"] == 4
    assert config["ckpt"]["keep_last"] == 2
    assert config["ckpt"]["skip_optimizer"] is True
    assert config["ckpt"]["skip_scheduler"] is True
    assert config["ckpt"]["skip_dataloader"] is True
    assert config["ckpt"]["skip_progress"] is False


def _write_token_audit(stage: Path, token_counts: list[tuple[str, int]]) -> None:
    (stage / "curriculum").mkdir(parents=True)
    (stage / "prime").mkdir(parents=True)
    rows = [
        {"metadata": {"stage": stage_name}, "messages": []} for stage_name, _tokens in token_counts
    ]
    audits = [
        {"line": index, "rendered_tokens": tokens}
        for index, (_stage_name, tokens) in enumerate(token_counts, 1)
    ]
    (stage / "curriculum/train.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    (stage / "prime/token_audit_rows.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in audits), encoding="utf-8"
    )


def test_fixed_v6_exact_token_mix_enforces_each_stage_fraction(tmp_path: Path) -> None:
    # Exact rendered-token fractions match the frozen 40/25/15/10/10 groups.
    # The gate must use rendered tokens, not row counts.
    _write_token_audit(
        tmp_path,
        [
            ("multi-page-continue", 400),
            ("resolve-literal-identity", 75),
            ("dirty-cart-cleanup", 250),
            ("checkpoint-grounded", 75),
            ("recover-local-origin", 100),
            ("ordinary-replay", 100),
        ],
    )
    audit = _token_mix_audit(tmp_path)
    assert audit["gate_passed"] is True
    assert audit["rendered_tokens"] == 1_000
    assert audit["fractions"] == pytest.approx(
        {
            "multi-page-continue": 0.40,
            "resolve-literal-identity": 0.075,
            "dirty-cart-cleanup": 0.25,
            "checkpoint-grounded": 0.075,
            "recover-local-origin": 0.10,
            "ordinary-replay": 0.10,
        }
    )
    bounds = audit["frozen_bounds"]
    assert bounds["multi_page_discovery"] == [0.32, 0.48]
    assert bounds["dirty_cart_cleanup"] == [0.18, 0.32]
    assert bounds["checkpoint_grounded_maximum"] == 0.14

    # Preserve row count but make rendered tokens overwhelmingly one stage.
    (tmp_path / "prime/token_audit_rows.jsonl").write_text(
        "".join(
            json.dumps({"line": index, "rendered_tokens": tokens}) + "\n"
            for index, tokens in enumerate((40, 40, 10, 4_000, 40, 40), 1)
        ),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactError, match="outside frozen bounds"):
        _token_mix_audit(tmp_path)


def _make_source_tree(root: Path) -> Path:
    source = root / "source"
    (source / "metadata").mkdir(parents=True)
    (source / "rank0.distcp").write_bytes(b"rank-zero\0payload")
    (source / "metadata/state.json").write_text('{"step":20}\n', encoding="utf-8")
    return source


def test_hardlink_tree_is_exact_same_device_inode_identity(tmp_path: Path) -> None:
    source = _make_source_tree(tmp_path)
    destination = tmp_path / "destination"
    result = _hardlink_tree(source, destination)

    source_identity = _tree_identity(source)
    destination_identity = _tree_identity(destination)
    assert result["tree_sha256"] == destination_identity["tree_sha256"]
    assert result["hardlink_verified"] is True
    assert len(result["hardlink_inventory"]) == source_identity["files"]
    assert source_identity["tree_sha256"] == destination_identity["tree_sha256"]
    for source_file in sorted(path for path in source.rglob("*") if path.is_file()):
        relative = source_file.relative_to(source)
        destination_file = destination / relative
        source_stat = source_file.stat()
        destination_stat = destination_file.stat()
        assert source_stat.st_dev == destination_stat.st_dev
        assert source_stat.st_ino == destination_stat.st_ino
        assert source_stat.st_nlink >= 2


def test_hardlink_tree_refuses_existing_destination_and_symlink(tmp_path: Path) -> None:
    source = _make_source_tree(tmp_path)
    destination = tmp_path / "destination"
    destination.mkdir()
    with pytest.raises(ArtifactError):
        _hardlink_tree(source, destination)

    destination.rmdir()
    (source / "unsafe").symlink_to(source / "rank0.distcp")
    with pytest.raises(ArtifactError):
        _hardlink_tree(source, destination)


def test_receipt_uses_canonical_source_not_prunable_staged_step20(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Receipt creation must survive PRIME's keep-last pruning of staged step20."""

    from harness_posttrain import browser_action_fixed_v6 as fixed

    campaign_root = tmp_path / ("a" * 40)
    artifact_sha = "b" * 40
    execution_sha = "c" * 40
    stage = campaign_root / "browser_action_fixed_v6" / artifact_sha
    training = stage / "training"
    canonical_source = campaign_root / "refinement/config/prime_output/checkpoints/step_20"
    canonical_source.mkdir(parents=True)
    (canonical_source / "rank0.distcp").write_bytes(b"canonical-step20")
    source_identity = _tree_identity(canonical_source)
    parent = campaign_root / "selected/merged"
    parent.mkdir(parents=True)
    (parent / "config.json").write_text("{}\n", encoding="utf-8")

    candidate = training / "prime_output/weights/step_24/lora_adapters"
    candidate.mkdir(parents=True)
    (candidate / "adapter_model.safetensors").write_bytes(b"fixed-v6")
    (candidate / "adapter_config.json").write_text("{}\n", encoding="utf-8")
    # The staged resume target has already been pruned by keep_last=2.
    staged_step20 = training / "prime_output/checkpoints/step_20"
    assert not staged_step20.exists()

    for relative, data in (
        ("curriculum/manifest.json", b"{}\n"),
        ("curriculum/train.jsonl", b"{}\n"),
        ("prime/manifest.json", b"{}\n"),
        ("prime/train.parquet", b"parquet"),
    ):
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    config_path = training / "browser_action_fixed_v6.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_bytes(
        _fixed_toml(
            model=parent,
            dataset=stage / "prime",
            output=training / "prime_output",
            targets=["^q_proj$"],
            settings=_settings(),
            seed=17,
            num_gpus=4,
        )
    )

    campaign = type(
        "CampaignStub",
        (),
        {"digest": "campaign-digest", "campaign": {"refinement": _settings()}},
    )()
    source = {
        "parent": parent.resolve(),
        "parent_merge_provenance_sha256": "1" * 64,
        "adapter_identity": {"files": 2, "bytes": 10, "tree_sha256": "2" * 64},
        "adapter_receipt_tree_sha256": "3" * 64,
        "source_dcp": canonical_source.resolve(),
        "source_dcp_identity": source_identity,
    }
    monkeypatch.setattr(fixed, "_verified_source", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(
        fixed,
        "_verify_adapter",
        lambda **_kwargs: {
            "path": str(candidate.resolve()),
            "update": 24,
            "files": 2,
            "bytes": 16,
            "sha256": "4" * 64,
            "stable_marker_sha256": "5" * 64,
            "adapter_config_sha256": "6" * 64,
        },
    )

    plan = {
        "schema": fixed.FIXED_V6_PLAN_SCHEMA,
        "status": "prepared",
        "stage": "browser_action_fixed_v6",
        "campaign_digest": campaign.digest,
        "campaign_artifact_source_git_sha": campaign_root.name,
        "artifact_source_git_sha": artifact_sha,
        "parent_model": str(parent.resolve()),
        "parent_merge_provenance_sha256": source["parent_merge_provenance_sha256"],
        "source_step20_adapter": source["adapter_identity"],
        "source_step20_adapter_receipt_tree_sha256": source["adapter_receipt_tree_sha256"],
        "source_step20_dcp": source_identity,
        "linked_step20_dcp": {
            **source_identity,
            "path": str(staged_step20.resolve()),
            "hardlink_verified": True,
            "hardlink_inventory": [
                {
                    "relative_path": "rank0.distcp",
                    "device": 1,
                    "inode": 2,
                    "bytes": len(b"canonical-step20"),
                    "link_count_at_preparation": 2,
                }
            ],
        },
        "config_sha256": sha256_file(config_path),
        "curriculum_manifest_sha256": sha256_file(stage / "curriculum/manifest.json"),
        "curriculum_data_sha256": sha256_file(stage / "curriculum/train.jsonl"),
        "prime_manifest_sha256": sha256_file(stage / "prime/manifest.json"),
        "prime_parquet_sha256": sha256_file(stage / "prime/train.parquet"),
        "selection_performed": False,
        "amazon_outcomes_consulted": False,
        "candidate": {"name": "step24", "update": 24, "path": str(candidate.resolve())},
        "training_policy": {
            "optimizer": "adamw",
            "learning_rate": 3e-6,
            "scheduler": "constant_cosine_floor",
            "warmup_steps": 0,
            "resume_step": 20,
            "optimizer_updates": 24,
            "new_optimizer_updates": 4,
            "checkpoint_updates": [24],
            "num_gpus": 4,
            "global_batch_size": 16,
            "sequence_length": 32768,
            "lora_rank": 64,
            "lora_alpha": 128.0,
            "restore_model": True,
            "restore_progress": True,
            "restore_optimizer": False,
            "restore_scheduler": False,
            "restore_dataloader": False,
        },
    }
    (training / "plan.json").write_text(json.dumps(plan) + "\n", encoding="utf-8")

    receipt = write_browser_action_fixed_v6_receipt(
        campaign,
        campaign_root=campaign_root,
        stage_dir=stage,
        artifact_source_git_sha=artifact_sha,
        execution_source_git_sha=execution_sha,
    )
    assert receipt["status"] == "ok"
    assert receipt["candidate"]["update"] == 24
    assert receipt["source_step20_dcp"]["tree_sha256"] == source_identity["tree_sha256"]
    assert receipt["linked_resume_may_be_pruned"] is True
    assert "copied_step20_dcp" not in receipt
    assert not staged_step20.exists()
