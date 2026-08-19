from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

import pytest
from conftest import write_jsonl

from harness_posttrain.artifacts import ArtifactError
from harness_posttrain.config import Campaign
from harness_posttrain.opd import generate_optional_opd
from harness_posttrain.selection import MultiLoraServer
from harness_posttrain.train_configs import generate_sft_configs


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _smoke(path: Path, model: Path, campaign: Campaign) -> Path:
    path.write_text(
        json.dumps(
            {
                "status": "ok",
                "resolved_snapshot": str(model),
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
            }
        ),
        encoding="utf-8",
    )
    return path


def test_targeted_training_config_is_short_and_assistant_only(
    tmp_path: Path, campaign: Campaign
) -> None:
    model = tmp_path / "model"
    model.mkdir()
    smoke = _smoke(tmp_path / "smoke.json", model, campaign)
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    parquet = dataset / "train.parquet"
    parquet.write_bytes(b"fixture")
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "harness-posttrain.prime-sft-dataset.v1",
                "stage": "targeted_sft",
                "candidate": "balanced",
                "campaign_digest": campaign.digest,
                "model_revision": campaign.model["revision"],
                "parquet_sha256": _sha(parquet),
                "token_audit": {"seq_len": 32768, "global_batch_size": 16},
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "configs"
    plan = generate_sft_configs(
        campaign,
        stage="targeted_sft",
        candidate="balanced",
        smoke_report=smoke,
        parent_model=model,
        dataset_dir=dataset,
        output_dir=output,
    )
    parsed = tomllib.loads((output / "balanced.toml").read_text(encoding="utf-8"))
    assert parsed["max_steps"] == 20
    assert parsed["data"]["loss_mask"]["assistant"] is True
    assert parsed["data"]["loss_mask"]["tool"] is False
    assert [row["update"] for row in plan["candidates"]] == [5, 10, 20]


def test_refinement_and_selection_support_four_gpu_topology(
    tmp_path: Path, campaign: Campaign
) -> None:
    model = tmp_path / "base"
    model.mkdir()
    smoke = _smoke(tmp_path / "smoke.json", model, campaign)
    parent = tmp_path / "selected-merged"
    parent.mkdir()
    (parent / "config.json").write_text("{}", encoding="utf-8")
    (parent / "merge_provenance.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "output_dir": str(parent.resolve()),
                "snapshot": {
                    "model_id": campaign.model["model_id"],
                    "revision": campaign.model["revision"],
                },
                "logit_metrics": {"maximum_absolute_error": 0.0},
            }
        ),
        encoding="utf-8",
    )
    dataset = tmp_path / "refinement-dataset"
    dataset.mkdir()
    parquet = dataset / "train.parquet"
    parquet.write_bytes(b"fixture")
    settings = campaign.campaign["refinement"]
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "harness-posttrain.prime-sft-dataset.v1",
                "stage": "refinement",
                "campaign_digest": campaign.digest,
                "model_revision": campaign.model["revision"],
                "parquet_sha256": _sha(parquet),
                "token_audit": {
                    "seq_len": settings["sequence_length"],
                    "global_batch_size": settings["global_batch_size"],
                },
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "refinement-config"
    generate_sft_configs(
        campaign,
        stage="refinement",
        smoke_report=smoke,
        parent_model=parent,
        dataset_dir=dataset,
        output_dir=output,
        num_gpus=4,
    )
    parsed = tomllib.loads((output / "refinement.toml").read_text(encoding="utf-8"))
    assert parsed["deployment"] == {
        "type": "single_node",
        "num_gpus": 4,
        "gpus_per_node": 4,
    }

    server = MultiLoraServer(
        base_model=model,
        adapters={"candidate": parent},
        output_dir=tmp_path / "server",
        gpu_ids=(0, 1, 2, 3),
    )
    command = server.command()
    assert command[command.index("--data-parallel-size") + 1] == "4"


def test_targeted_sft_rejects_four_gpu_topology(
    tmp_path: Path, campaign: Campaign
) -> None:
    model = tmp_path / "model"
    model.mkdir()
    smoke = _smoke(tmp_path / "smoke.json", model, campaign)
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    parquet = dataset / "train.parquet"
    parquet.write_bytes(b"fixture")
    (dataset / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "harness-posttrain.prime-sft-dataset.v1",
                "stage": "targeted_sft",
                "candidate": "balanced",
                "campaign_digest": campaign.digest,
                "model_revision": campaign.model["revision"],
                "parquet_sha256": _sha(parquet),
                "token_audit": {"seq_len": 32768, "global_batch_size": 16},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactError, match="targeted SFT topology"):
        generate_sft_configs(
            campaign,
            stage="targeted_sft",
            candidate="balanced",
            smoke_report=smoke,
            parent_model=model,
            dataset_dir=dataset,
            output_dir=tmp_path / "configs",
            num_gpus=4,
        )


def test_optional_opd_requires_opt_in_and_stays_bounded(
    tmp_path: Path,
    campaign: Campaign,
    sealed_splits: tuple[Path, list[dict[str, object]]],
) -> None:
    manifest_path, _ = sealed_splits
    model = tmp_path / "selected-sft"
    model.mkdir()
    smoke = _smoke(tmp_path / "smoke.json", model, campaign)
    replay = write_jsonl(
        tmp_path / "replay.jsonl",
        [
            {
                "schema": "harness-posttrain.opd-replay-source.v1",
                "replay_id": "replay-1",
                "episode_id": "episode-1",
                "task_id": "procedural-train-1",
                "source": "procedural",
                "scenario": "generic_shop_a",
                "request": {
                    "messages": [
                        {"role": "user", "content": "Continue or commit?"}
                    ]
                },
                "privileged_context": {
                    "frontier_complete": False,
                    "remaining_work": ["page 2"],
                },
            }
        ],
    )
    arguments = dict(
        campaign=campaign,
        split_manifest_path=manifest_path,
        smoke_report=smoke,
        student_model=model,
        teacher_model=model,
        replay_path=replay,
        output_dir=tmp_path / "opd",
    )
    with pytest.raises(ArtifactError, match="explicit opt-in"):
        generate_optional_opd(enabled=False, **arguments)
    plan = generate_optional_opd(enabled=True, **arguments)
    parsed = tomllib.loads((tmp_path / "opd/opd_smoke.toml").read_text(encoding="utf-8"))
    assert parsed["max_steps"] == 12
    assert parsed["orchestrator"]["max_inflight_rollouts"] == 8
    assert parsed["inference"]["vllm_extra"]["max_num_seqs"] == 16
    assert plan["resource_contract"]["minimum_host_memory_gib"] == 1024
    assert plan["safe_fallback"].startswith("retain_reward_filtered_parent")
