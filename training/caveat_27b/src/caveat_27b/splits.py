"""Freeze scenario-cluster split membership before any training outcome exists."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    canonical_json,
    publish_json,
    publish_jsonl,
    read_json,
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
from .config import Campaign

SPLIT_SCHEMA = "caveat-27b.split-manifest.v1"
MEMBERSHIP_SCHEMA = "caveat-27b.split-membership.v1"


def _string(row: Mapping[str, Any], key: str, label: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ArtifactError(f"{label}.{key} must be a nonempty string")
    return value


def _amazon_split(campaign: Campaign, scenario: str) -> str:
    matches = [
        split
        for split in ("train", "selection", "development", "final")
        if scenario in campaign.split_scenarios(split)
    ]
    if len(matches) != 1:
        raise ArtifactError(
            f"Amazon scenario is outside or ambiguous across frozen splits: {scenario}"
        )
    return matches[0]


def freeze_splits(
    campaign: Campaign,
    *,
    inventory_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    rows = read_jsonl(inventory_path)
    if not rows:
        raise ArtifactError("task inventory is empty")
    seen: set[str] = set()
    memberships: dict[str, list[dict[str, Any]]] = {
        key: [] for key in ("train", "selection", "development", "final")
    }
    for index, row in enumerate(rows, 1):
        label = f"inventory row {index}"
        task_id = _string(row, "task_id", label)
        source = _string(row, "source", label)
        scenario = _string(row, "scenario", label)
        if task_id in seen:
            raise ArtifactError(f"duplicate task_id in inventory: {task_id}")
        seen.add(task_id)
        if source == "amazon":
            split = _amazon_split(campaign, scenario)
        elif source == "procedural":
            split = _string(row, "split", label)
            if split not in {"train", "selection", "final"}:
                raise ArtifactError(f"{label}.split must be train, selection, or final")
        else:
            raise ArtifactError(f"{label}.source must be amazon or procedural")
        membership = {
            "schema": MEMBERSHIP_SCHEMA,
            "task_id": task_id,
            "source": source,
            "scenario": scenario,
            "split": split,
        }
        if source == "procedural" and isinstance(row.get("domain_family"), str):
            membership["domain_family"] = row["domain_family"]
        memberships[split].append(membership)

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, Any]] = {}
    for split, values in memberships.items():
        ordered = sorted(values, key=lambda row: row["task_id"])
        path = publish_jsonl(output / f"{split}.jsonl", ordered)
        files[split] = {
            "path": path.name,
            "sha256": sha256_file(path),
            "rows": len(ordered),
        }
    pairwise = [
        set(row["task_id"] for row in memberships[split])
        for split in ("train", "selection", "development", "final")
    ]
    if any(left & right for index, left in enumerate(pairwise) for right in pairwise[index + 1 :]):
        raise AssertionError("internal split overlap")
    source_counts = Counter(row["source"] for values in memberships.values() for row in values)
    manifest_body = {
        "schema": SPLIT_SCHEMA,
        "campaign_digest": campaign.digest,
        "inventory": {
            "path": str(Path(inventory_path).resolve()),
            "sha256": sha256_file(inventory_path),
            "rows": len(rows),
        },
        "files": files,
        "source_counts": dict(sorted(source_counts.items())),
        "leakage_policy": {
            "training_accepts_only": "train",
            "checkpoint_selection_accepts_only": "selection",
            "development_transfer_accepts_only": "development",
            "confirmatory_accepts_only": "final",
            "scenario_cluster_disjoint": True,
        },
    }
    manifest = dict(manifest_body)
    manifest["manifest_body_sha256"] = sha256_bytes(canonical_json(manifest_body).encode())
    publish_json(output / "manifest.json", manifest)
    return manifest


def load_split_manifest(
    campaign: Campaign, manifest_path: str | Path
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    path = Path(manifest_path).resolve()
    value = read_json(path)
    if not isinstance(value, dict) or value.get("schema") != SPLIT_SCHEMA:
        raise ArtifactError("unsupported split manifest")
    body = dict(value)
    declared = body.pop("manifest_body_sha256", None)
    if declared != sha256_bytes(canonical_json(body).encode()):
        raise ArtifactError("split manifest self-hash drifted")
    if value.get("campaign_digest") != campaign.digest:
        raise ArtifactError("split manifest belongs to a different campaign")
    membership: dict[str, dict[str, Any]] = {}
    files = value.get("files")
    if not isinstance(files, dict):
        raise ArtifactError("split manifest files mapping is absent")
    for split in ("train", "selection", "development", "final"):
        descriptor = files.get(split)
        if not isinstance(descriptor, dict):
            raise ArtifactError(f"split descriptor is absent: {split}")
        member_path = path.parent / str(descriptor.get("path"))
        if sha256_file(member_path) != descriptor.get("sha256"):
            raise ArtifactError(f"split membership bytes drifted: {split}")
        rows = read_jsonl(member_path)
        if len(rows) != descriptor.get("rows"):
            raise ArtifactError(f"split membership count drifted: {split}")
        for row in rows:
            if row.get("schema") != MEMBERSHIP_SCHEMA or row.get("split") != split:
                raise ArtifactError(f"malformed split membership row: {split}")
            task_id = row.get("task_id")
            if not isinstance(task_id, str) or task_id in membership:
                raise ArtifactError(f"duplicate or invalid split task_id: {task_id!r}")
            membership[task_id] = row
    return value, membership


def require_training_membership(
    membership: Mapping[str, Mapping[str, Any]], *, task_id: str, source: str, scenario: str
) -> None:
    row = membership.get(task_id)
    if row is None:
        raise ArtifactError(f"training row references an unfrozen task: {task_id}")
    expected = {"split": "train", "source": source, "scenario": scenario}
    for key, value in expected.items():
        if row.get(key) != value:
            raise ArtifactError(
                f"training row conflicts with frozen membership for {task_id}: {key}"
            )
