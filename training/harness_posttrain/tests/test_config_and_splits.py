from __future__ import annotations

from pathlib import Path

import pytest

from harness_posttrain.artifacts import ArtifactError
from harness_posttrain.config import Campaign
from harness_posttrain.splits import load_split_manifest, require_training_membership


def test_campaign_is_pinned_and_bounded(campaign: Campaign) -> None:
    assert campaign.model["model_id"] == "Qwen/Qwen3.5-27B"
    assert campaign.campaign["optional_opd_smoke"]["optimizer_updates"] == 12
    assert campaign.campaign["cluster"]["authorized_aggregate_gpu_cap"] == 64


def test_split_manifest_is_disjoint_and_enforced(
    campaign: Campaign, sealed_splits: tuple[Path, list[dict[str, object]]]
) -> None:
    manifest_path, _inventory = sealed_splits
    _manifest, membership = load_split_manifest(campaign, manifest_path)
    assert membership["dev-laptop-1"]["split"] == "development"
    assert membership["procedural-select-1"]["split"] == "selection"
    assert membership["final-tent-1"]["split"] == "final"
    require_training_membership(
        membership,
        task_id="procedural-train-1",
        source="procedural",
        scenario="generic_shop_a",
    )
    with pytest.raises(ArtifactError, match="conflicts with frozen membership"):
        require_training_membership(
            membership,
            task_id="dev-laptop-1",
            source="amazon",
            scenario="laptop",
        )
