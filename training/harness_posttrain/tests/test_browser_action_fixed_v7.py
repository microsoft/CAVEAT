from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from harness_posttrain.artifacts import ArtifactError, sha256_file
from harness_posttrain.browser_action_continuation import _tree_identity
from harness_posttrain.browser_action_fixed_v7 import (
    _audit_coverage,
    _fixed_toml,
    _hardlink_tree,
    _sequence_rows,
    _token_mix_audit,
    write_browser_action_fixed_v7_receipt,
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


def test_fixed_v7_config_is_single_candidate_lr2e6_step23(tmp_path: Path) -> None:
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

    assert config["max_steps"] == 23
    assert config["optim"]["lr"] == pytest.approx(2e-6)
    assert config["scheduler"] == {
        "type": "cosine",
        "warmup_steps": 0,
        "min_lr": pytest.approx(2e-6),
    }
    assert config["ckpt"]["resume_step"] == 20
    assert config["ckpt"]["interval"] == 3
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


def test_fixed_v7_exact_token_mix_requires_both_sources_and_all_sequences(tmp_path: Path) -> None:
    _write_token_audit(
        tmp_path,
        [
            ("amazon-discovery-sequence", 100),
            ("amazon-cart-sequence", 100),
            ("amazon-recovery-sequence", 100),
            ("amazon-preservation-sequence", 100),
            ("procedural-discovery-sequence", 100),
            ("procedural-cart-sequence", 100),
            ("procedural-recovery-sequence", 100),
            ("procedural-preservation-sequence", 100),
        ],
    )
    audit = _token_mix_audit(tmp_path)
    assert audit["gate_passed"] is True
    assert audit["rendered_tokens"] == 800
    assert audit["grouped_fractions"]["amazon_laptop_adaptation"] == pytest.approx(0.5)
    assert audit["grouped_fractions"]["procedural_rehearsal"] == pytest.approx(0.5)

    # Preserve row count but make rendered tokens overwhelmingly one stage.
    (tmp_path / "prime/token_audit_rows.jsonl").write_text(
        "".join(
            json.dumps({"line": index, "rendered_tokens": tokens}) + "\n"
            for index, tokens in enumerate((4_000, 40, 40, 40, 40, 40, 40, 40), 1)
        ),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactError, match="outside frozen bounds"):
        _token_mix_audit(tmp_path)


def test_fixed_v7_rows_are_ordered_multi_turn_browser_histories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from harness_posttrain import browser_action_fixed_v7 as fixed

    monkeypatch.setattr(
        fixed,
        "_agent_output",
        lambda _model, **kwargs: json.dumps(
            {**kwargs, "action": [kwargs["action"]]}, sort_keys=True
        ),
    )
    selected = {"id": "EXP-LAPTOP-50", "title": "Late hero", "price": 919.99}
    extra = {"id": "EXP-LAPTOP-42", "title": "Pinned decoy", "price": 899.99}
    checkpoint = {
        "frontier": {"inspected_count": 37},
        "candidates": [{"id": "EXP-LAPTOP-50"}],
        "proposed_candidate_id": "EXP-LAPTOP-50",
    }
    search_cards = [
        {
            "id": f"EXP-LAPTOP-TEST-{index:02d}",
            "title": f"Public laptop card {index:02d}",
            "price": 500 + index,
            "rating": 4.1,
        }
        for index in range(1, 38)
    ]
    search_cards[24] = {**selected, "rating": 4.7}
    search_cards[-1] = {
        "id": "EXP-LAPTOP-TAIL-37",
        "title": "Final-page tail decoy",
        "price": 589.99,
        "rating": 4.1,
    }
    rows = _sequence_rows(
        task_id="laptop-adapt-r00",
        source="amazon_laptop_development",
        scenario="amazon_laptop_same_task_adaptation",
        system="browser-use system",
        instruction="Buy the best qualifying laptop.",
        origin="http://127.0.0.1:3000",
        total=37,
        page_size=18,
        selected=selected,
        extra=extra,
        search_cards=search_cards,
        arguments=checkpoint,
        prefix="amazon",
        output_model=object,
    )
    discovery = rows["amazon-discovery-sequence"]["messages"]
    cart = rows["amazon-cart-sequence"]["messages"]
    recovery = rows["amazon-recovery-sequence"]["messages"]
    assert sum(message["role"] == "assistant" for message in discovery) == 5
    assert [message["role"] for message in discovery] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    discovery_wire = "\n".join(message["content"] for message in discovery)
    assert discovery_wire.index("page=1") < discovery_wire.index("page=2")
    assert discovery_wire.index("/dp/EXP-LAPTOP-50") < discovery_wire.index("page=3")
    assert discovery_wire.index("page=3") < discovery_wire.rindex("decision_checkpoint")
    assert "cards_seen=18/37" in discovery_wire
    assert "cards_seen=36/37" in discovery_wire
    assert "cards_seen=37/37" in discovery_wire
    audit = _audit_coverage(discovery, kind="amazon-discovery-sequence")
    assert audit == {
        "total": 37,
        "page_size": 18,
        "page_count": 3,
        "evidenced_ids": 37,
        "pdp_candidate": "EXP-LAPTOP-50",
        "origins": ["http://127.0.0.1:3000"],
    }
    cart_wire = "\n".join(message["content"] for message in cart)
    assert cart_wire.index("Buy Now") < cart_wire.index("Review cart")
    assert cart_wire.index("Remove ADDON-PLAN") < cart_wire.index("Remove EXP-LAPTOP-42")
    assert cart_wire.index("no plan, add-on, or extra item") < cart_wire.rindex("Place your order")
    recovery_wire = "\n".join(message["content"] for message in recovery)
    assert recovery_wire.index("about:blank") < recovery_wire.index("127.0.0.1")
    assert recovery_wire.index('"navigate"') < recovery_wire.rindex("decision_checkpoint")
    assert _audit_coverage(recovery, kind="amazon-recovery-sequence")["evidenced_ids"] == 37


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

    from harness_posttrain import browser_action_fixed_v7 as fixed

    campaign_root = tmp_path / ("a" * 40)
    artifact_sha = "b" * 40
    execution_sha = "c" * 40
    stage = campaign_root / "browser_action_fixed_v7" / artifact_sha
    training = stage / "training"
    canonical_source = campaign_root / "refinement/config/prime_output/checkpoints/step_20"
    canonical_source.mkdir(parents=True)
    (canonical_source / "rank0.distcp").write_bytes(b"canonical-step20")
    source_identity = _tree_identity(canonical_source)
    parent = campaign_root / "selected/merged"
    parent.mkdir(parents=True)
    (parent / "config.json").write_text("{}\n", encoding="utf-8")

    candidate = training / "prime_output/weights/step_23/lora_adapters"
    candidate.mkdir(parents=True)
    (candidate / "adapter_model.safetensors").write_bytes(b"fixed-v7")
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
    config_path = training / "browser_action_fixed_v7.toml"
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
            "update": 23,
            "files": 2,
            "bytes": 16,
            "sha256": "4" * 64,
            "stable_marker_sha256": "5" * 64,
            "adapter_config_sha256": "6" * 64,
        },
    )

    plan = {
        "schema": fixed.FIXED_V7_PLAN_SCHEMA,
        "status": "prepared",
        "stage": "browser_action_fixed_v7",
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
        "amazon_outcomes_consulted": True,
        "scientific_label": "same_task_laptop_development_adaptation",
        "candidate": {"name": "step23", "update": 23, "path": str(candidate.resolve())},
        "training_policy": {
            "optimizer": "adamw",
            "learning_rate": 2e-6,
            "scheduler": "constant_cosine_floor",
            "warmup_steps": 0,
            "resume_step": 20,
            "optimizer_updates": 23,
            "new_optimizer_updates": 3,
            "checkpoint_updates": [23],
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

    receipt = write_browser_action_fixed_v7_receipt(
        campaign,
        campaign_root=campaign_root,
        stage_dir=stage,
        artifact_source_git_sha=artifact_sha,
        execution_source_git_sha=execution_sha,
    )
    assert receipt["status"] == "ok"
    assert receipt["candidate"]["update"] == 23
    assert receipt["source_step20_dcp"]["tree_sha256"] == source_identity["tree_sha256"]
    assert receipt["linked_resume_may_be_pruned"] is True
    assert "copied_step20_dcp" not in receipt
    assert not staged_step20.exists()
