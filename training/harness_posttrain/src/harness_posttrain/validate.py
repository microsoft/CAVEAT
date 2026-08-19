"""Cross-artifact validation used before cluster submission."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .artifacts import ArtifactError, read_json, sha256_file
from .config import Campaign
from .prime_data import PRIME_DATASET_SCHEMA
from .splits import load_split_manifest
from .train_configs import TRAIN_PLAN_SCHEMA


def validate_artifacts(
    campaign: Campaign,
    *,
    split_manifest: str | Path | None = None,
    dataset: str | Path | None = None,
    train_plan: str | Path | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": "harness-posttrain.validation-report.v1",
        "status": "ok",
        "campaign_digest": campaign.digest,
        "checks": ["configuration"],
    }
    if split_manifest is not None:
        manifest, membership = load_split_manifest(campaign, split_manifest)
        report["checks"].append("sealed_splits")
        report["split_manifest_sha256"] = sha256_file(split_manifest)
        report["task_count"] = len(membership)
        if manifest.get("leakage_policy", {}).get("scenario_cluster_disjoint") is not True:
            raise ArtifactError("split leakage policy is absent")
    if dataset is not None:
        root = Path(dataset).resolve()
        value = read_json(root / "manifest.json")
        if (
            not isinstance(value, dict)
            or value.get("schema") != PRIME_DATASET_SCHEMA
            or value.get("campaign_digest") != campaign.digest
            or value.get("parquet_sha256") != sha256_file(root / "train.parquet")
        ):
            raise ArtifactError("PRIME dataset is incompatible or drifted")
        report["checks"].append("prime_dataset")
        report["dataset_manifest_sha256"] = sha256_file(root / "manifest.json")
    if train_plan is not None:
        value = read_json(train_plan)
        if (
            not isinstance(value, dict)
            or value.get("schema") != TRAIN_PLAN_SCHEMA
            or value.get("campaign_digest") != campaign.digest
            or value.get("config_sha256") != sha256_file(value.get("config"))
        ):
            raise ArtifactError("training plan is incompatible or drifted")
        report["checks"].append("training_plan")
        report["train_plan_sha256"] = sha256_file(train_plan)
    return report
