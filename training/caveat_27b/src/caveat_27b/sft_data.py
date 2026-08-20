"""Deterministic targeted-SFT mixture construction."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    canonical_json,
    publish_json,
    publish_jsonl,
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
from .config import Campaign
from .splits import load_split_manifest, require_training_membership

SFT_SOURCE_SCHEMA = "caveat-27b.sft-source.v1"
SFT_MANIFEST_SCHEMA = "caveat-27b.sft-manifest.v1"
FORBIDDEN_VISIBLE_KEYS = {
    "hero_asin",
    "optimal_selection",
    "evaluator_score",
    "evaluator_truth",
    "storefront_ops_token",
    "private_catalog",
    "oracle",
    "optimal_item_id",
    "accepted_item_ids",
    "utility_by_item",
    "failure_reasons",
}


def _stable_key(seed: int, stage: str, sample_id: str) -> str:
    return hashlib.sha256(f"{seed}:{stage}:{sample_id}".encode()).hexdigest()


def _scan_visible(value: Any, path: str = "visible") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_VISIBLE_KEYS:
                raise ArtifactError(
                    "forbidden evaluator/private field in model-visible data: "
                    f"{path}.{key}"
                )
            _scan_visible(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_visible(item, f"{path}[{index}]")


def validate_sft_sample(
    row: Mapping[str, Any],
    *,
    membership: Mapping[str, Mapping[str, Any]],
    expected_stage: str,
    row_number: int,
) -> dict[str, Any]:
    label = f"{expected_stage} row {row_number}"
    if row.get("schema") != SFT_SOURCE_SCHEMA:
        raise ArtifactError(f"{label}: unsupported schema")
    strings: dict[str, str] = {}
    for key in ("sample_id", "task_id", "source", "scenario"):
        value = row.get(key)
        if not isinstance(value, str) or not value:
            raise ArtifactError(f"{label}.{key} must be a nonempty string")
        strings[key] = value
    require_training_membership(
        membership,
        task_id=strings["task_id"],
        source=strings["source"],
        scenario=strings["scenario"],
    )
    messages = row.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ArtifactError(f"{label}.messages must be nonempty")
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping) or message.get("role") not in {
            "system",
            "user",
            "assistant",
            "tool",
        }:
            raise ArtifactError(f"{label}.messages[{index}] is malformed")
    if messages[-1].get("role") != "assistant":
        raise ArtifactError(f"{label} must end with an assistant target")
    if not any(message.get("role") == "user" for message in messages):
        raise ArtifactError(f"{label} has no user input")
    tools = row.get("tools", [])
    if not isinstance(tools, list):
        raise ArtifactError(f"{label}.tools must be a list")
    _scan_visible({"messages": messages, "tools": tools})
    output = {"messages": list(messages)}
    if tools:
        output["tools"] = tools
    output["metadata"] = {
        "schema": "caveat-27b.sft-row-metadata.v1",
        "sample_id": strings["sample_id"],
        "task_id": strings["task_id"],
        "source": strings["source"],
        "scenario": strings["scenario"],
        "stage": expected_stage,
    }
    return output


def _quotas(weights: Mapping[str, Any], limit: int) -> dict[str, int]:
    names = list(weights)
    raw = {name: float(weights[name]) * limit for name in names}
    result = {name: math.floor(raw[name]) for name in names}
    remaining = limit - sum(result.values())
    order = sorted(names, key=lambda name: (-(raw[name] - result[name]), name))
    for name in order[:remaining]:
        result[name] += 1
    return result


def _select_mixture(
    groups: Mapping[str, Sequence[dict[str, Any]]],
    *,
    weights: Mapping[str, Any],
    maximum_rows: int,
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if set(groups) != set(weights):
        raise ArtifactError("mixture input groups do not match the configured weights")
    total_limit = min(maximum_rows, sum(len(rows) for rows in groups.values()))
    if total_limit < 1:
        raise ArtifactError("no eligible SFT rows remain after validation")
    ordered: dict[str, list[dict[str, Any]]] = {}
    for name, rows in groups.items():
        ordered[name] = sorted(
            rows,
            key=lambda row: _stable_key(seed, name, row["metadata"]["sample_id"]),
        )
    quota = _quotas(weights, total_limit)
    selected: dict[str, list[dict[str, Any]]] = {
        name: values[: min(quota[name], len(values))] for name, values in ordered.items()
    }
    missing = total_limit - sum(len(values) for values in selected.values())
    while missing:
        progress = False
        for name in sorted(groups, key=lambda item: (-float(weights[item]), item)):
            offset = len(selected[name])
            if offset < len(ordered[name]):
                selected[name].append(ordered[name][offset])
                missing -= 1
                progress = True
                if not missing:
                    break
        if not progress:
            break
    flattened = [row for name in sorted(selected) for row in selected[name]]
    flattened.sort(
        key=lambda row: _stable_key(seed, "final", row["metadata"]["sample_id"])
    )
    counts = {name: len(values) for name, values in selected.items()}
    return flattened, counts


def materialize_targeted_sft(
    campaign: Campaign,
    *,
    candidate: str,
    split_manifest_path: str | Path,
    contract_path: str | Path,
    interaction_path: str | Path,
    rehearsal_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    split_manifest, membership = load_split_manifest(campaign, split_manifest_path)
    sources = {
        "contract": Path(contract_path).resolve(),
        "interaction": Path(interaction_path).resolve(),
        "rehearsal": Path(rehearsal_path).resolve(),
    }
    groups: dict[str, list[dict[str, Any]]] = {}
    all_ids: set[str] = set()
    for stage, path in sources.items():
        validated: list[dict[str, Any]] = []
        for index, row in enumerate(read_jsonl(path), 1):
            sample = validate_sft_sample(
                row,
                membership=membership,
                expected_stage=stage,
                row_number=index,
            )
            sample_id = sample["metadata"]["sample_id"]
            if sample_id in all_ids:
                raise ArtifactError(f"duplicate sample_id across SFT inputs: {sample_id}")
            all_ids.add(sample_id)
            validated.append(sample)
        groups[stage] = validated

    config = campaign.targeted_sft_candidate(candidate)
    rows, counts = _select_mixture(
        groups,
        weights=config["mixture"],
        maximum_rows=int(config["maximum_rows"]),
        seed=int(campaign.campaign["seed"]),
    )
    output = Path(output_dir).resolve()
    data_path = publish_jsonl(output / "train.jsonl", rows)
    body = {
        "schema": SFT_MANIFEST_SCHEMA,
        "stage": "targeted_sft",
        "candidate": candidate,
        "campaign_digest": campaign.digest,
        "split_manifest_sha256": sha256_file(split_manifest_path),
        "split_manifest_body_sha256": split_manifest["manifest_body_sha256"],
        "inputs": {
            name: {"path": str(path), "sha256": sha256_file(path), "rows": len(groups[name])}
            for name, path in sources.items()
        },
        "output": {"path": data_path.name, "sha256": sha256_file(data_path), "rows": len(rows)},
        "configured_mixture": config["mixture"],
        "realized_counts": counts,
        "assistant_tokens_only": True,
        "heldout_scenarios_present": False,
    }
    manifest = dict(body)
    manifest["manifest_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    publish_json(output / "manifest.json", manifest)
    return manifest
