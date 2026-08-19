"""Reward-filtered on-policy refinement with verified first-error corrections."""

from __future__ import annotations

import statistics
from collections.abc import Mapping
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
from .sft_data import SFT_SOURCE_SCHEMA, _scan_visible, _select_mixture, validate_sft_sample
from .splits import load_split_manifest, require_training_membership

EPISODE_SCHEMA = "caveat-27b.on-policy-episode.v1"
CORRECTION_SCHEMA = "caveat-27b.verified-correction.v1"
REFINEMENT_MANIFEST_SCHEMA = "caveat-27b.refinement-manifest.v1"
REWARD_KEYS = (
    "contract_valid",
    "contract_semantic",
    "tool_valid",
    "frontier_complete",
    "no_loop",
    "optimal",
)


def _required_string(value: Mapping[str, Any], key: str, label: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ArtifactError(f"{label}.{key} must be a nonempty string")
    return item


def _decision_reward(
    checks: Mapping[str, Any], *, optimal: bool, weights: Mapping[str, Any]
) -> float:
    signals: dict[str, bool] = {"optimal": optimal}
    for key in REWARD_KEYS[:-1]:
        value = checks.get(key)
        if not isinstance(value, bool):
            raise ArtifactError(f"decision check must be Boolean: {key}")
        signals[key] = value
    return sum(float(weights[key]) * float(signals[key]) for key in REWARD_KEYS)


def _selected_ordinals(decisions: list[dict[str, Any]], maximum: int) -> list[int]:
    if maximum < 1 or not decisions:
        return []
    if len(decisions) <= maximum:
        return list(range(len(decisions)))
    if maximum == 1:
        return [len(decisions) - 1]
    return [index * (len(decisions) - 1) // (maximum - 1) for index in range(maximum)]


def _first_decisive_error(decisions: list[dict[str, Any]], *, optimal: bool) -> int | None:
    for index, decision in enumerate(decisions):
        checks = decision["checks"]
        if any(checks[key] is False for key in REWARD_KEYS[:-1]):
            return index
    if not optimal and decisions:
        return len(decisions) - 1
    return None


def _as_sft_source(
    *,
    sample_id: str,
    task_id: str,
    source: str,
    scenario: str,
    messages: Any,
    tools: Any,
) -> dict[str, Any]:
    return {
        "schema": SFT_SOURCE_SCHEMA,
        "sample_id": sample_id,
        "task_id": task_id,
        "source": source,
        "scenario": scenario,
        "messages": messages,
        "tools": tools,
    }


def materialize_refinement(
    campaign: Campaign,
    *,
    split_manifest_path: str | Path,
    episodes_path: str | Path,
    corrections_path: str | Path,
    rehearsal_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    split_manifest, membership = load_split_manifest(campaign, split_manifest_path)
    config = campaign.campaign["refinement"]
    weights = config["reward_weights"]
    episode_rows = read_jsonl(episodes_path)
    correction_rows = read_jsonl(corrections_path)
    if not episode_rows:
        raise ArtifactError("on-policy episode input is empty")

    episodes: dict[str, dict[str, Any]] = {}
    episode_rewards: list[float] = []
    for row_number, raw in enumerate(episode_rows, 1):
        label = f"episode row {row_number}"
        if raw.get("schema") != EPISODE_SCHEMA:
            raise ArtifactError(f"{label}: unsupported schema")
        episode_id = _required_string(raw, "episode_id", label)
        task_id = _required_string(raw, "task_id", label)
        source = _required_string(raw, "source", label)
        scenario = _required_string(raw, "scenario", label)
        if episode_id in episodes:
            raise ArtifactError(f"duplicate episode_id: {episode_id}")
        require_training_membership(
            membership, task_id=task_id, source=source, scenario=scenario
        )
        if raw.get("infrastructure_complete") is not True:
            # Infrastructure failures are never negative training examples.
            continue
        if not isinstance(raw.get("binding_backstop"), bool):
            raise ArtifactError(f"{label}.binding_backstop must be Boolean")
        outcome = raw.get("outcome")
        if not isinstance(outcome, Mapping) or not isinstance(outcome.get("optimal"), bool):
            raise ArtifactError(f"{label}.outcome.optimal must be Boolean")
        decisions_raw = raw.get("decisions")
        if not isinstance(decisions_raw, list) or not decisions_raw:
            raise ArtifactError(f"{label}.decisions must be nonempty")
        decisions: list[dict[str, Any]] = []
        for decision_index, decision_raw in enumerate(decisions_raw):
            if not isinstance(decision_raw, Mapping):
                raise ArtifactError(f"{label}.decisions[{decision_index}] must be an object")
            ordinal = decision_raw.get("ordinal")
            if ordinal != decision_index:
                raise ArtifactError(f"{label} decision ordinals must be contiguous from zero")
            public_state_sha256 = _required_string(
                decision_raw, "public_state_sha256", f"{label}.decisions[{decision_index}]"
            )
            if len(public_state_sha256) != 64 or any(
                character not in "0123456789abcdef" for character in public_state_sha256
            ):
                raise ArtifactError(f"{label} public_state_sha256 must be lowercase SHA-256")
            checks = decision_raw.get("checks")
            if not isinstance(checks, Mapping):
                raise ArtifactError(f"{label}.decisions[{decision_index}].checks is absent")
            reward = _decision_reward(
                checks, optimal=bool(outcome["optimal"]), weights=weights
            )
            messages = decision_raw.get("messages")
            tools = decision_raw.get("tools", [])
            _scan_visible({"messages": messages, "tools": tools})
            decisions.append(
                {
                    "ordinal": ordinal,
                    "public_state_sha256": public_state_sha256,
                    "checks": dict(checks),
                    "messages": messages,
                    "tools": tools,
                    "reward": reward,
                }
            )
            episode_rewards.append(reward)
        episodes[episode_id] = {
            "episode_id": episode_id,
            "task_id": task_id,
            "source": source,
            "scenario": scenario,
            "optimal": bool(outcome["optimal"]),
            "binding_backstop": bool(raw["binding_backstop"]),
            "decisions": decisions,
        }

    corrections: dict[tuple[str, int], dict[str, Any]] = {}
    for row_number, raw in enumerate(correction_rows, 1):
        label = f"correction row {row_number}"
        if raw.get("schema") != CORRECTION_SCHEMA:
            raise ArtifactError(f"{label}: unsupported schema")
        episode_id = _required_string(raw, "episode_id", label)
        ordinal = raw.get("decision_ordinal")
        if episode_id not in episodes or not isinstance(ordinal, int) or ordinal < 0:
            raise ArtifactError(f"{label} references an unknown episode or invalid ordinal")
        key = (episode_id, ordinal)
        if key in corrections:
            raise ArtifactError(f"duplicate correction: {episode_id}/{ordinal}")
        if raw.get("public_evidence_only") is not True or raw.get("verifier_passed") is not True:
            raise ArtifactError(f"{label} must be public-only and verifier-passed")
        decisions = episodes[episode_id]["decisions"]
        if ordinal >= len(decisions) or raw.get("public_state_sha256") != decisions[ordinal][
            "public_state_sha256"
        ]:
            raise ArtifactError(f"{label} public-state binding drifted")
        _scan_visible({"messages": raw.get("messages"), "tools": raw.get("tools", [])})
        corrections[key] = dict(raw)

    groups: dict[str, list[dict[str, Any]]] = {"correction": [], "success": [], "rehearsal": []}
    successful_episodes = 0
    failed_episodes = 0
    missing_corrections = 0
    threshold = float(config["successful_reward_threshold"])
    for episode_id, episode in sorted(episodes.items()):
        decisions = episode["decisions"]
        success = (
            episode["optimal"]
            and not episode["binding_backstop"]
            and all(decision["reward"] >= threshold - 1e-12 for decision in decisions)
        )
        common = {
            "task_id": episode["task_id"],
            "source": episode["source"],
            "scenario": episode["scenario"],
        }
        if success:
            successful_episodes += 1
            ordinals = _selected_ordinals(
                decisions, int(config["maximum_decisions_per_successful_episode"])
            )
            for ordinal in ordinals:
                decision = decisions[ordinal]
                source_row = _as_sft_source(
                    sample_id=f"success:{episode_id}:{ordinal}",
                    messages=decision["messages"],
                    tools=decision["tools"],
                    **common,
                )
                groups["success"].append(
                    validate_sft_sample(
                        source_row,
                        membership=membership,
                        expected_stage="success",
                        row_number=len(groups["success"]) + 1,
                    )
                )
            continue
        failed_episodes += 1
        ordinal = _first_decisive_error(decisions, optimal=episode["optimal"])
        correction = corrections.get((episode_id, ordinal)) if ordinal is not None else None
        if correction is None:
            missing_corrections += 1
            continue
        source_row = _as_sft_source(
            sample_id=f"correction:{episode_id}:{ordinal}",
            messages=correction.get("messages"),
            tools=correction.get("tools", []),
            **common,
        )
        groups["correction"].append(
            validate_sft_sample(
                source_row,
                membership=membership,
                expected_stage="correction",
                row_number=len(groups["correction"]) + 1,
            )
        )

    for row_number, row in enumerate(read_jsonl(rehearsal_path), 1):
        groups["rehearsal"].append(
            validate_sft_sample(
                row,
                membership=membership,
                expected_stage="rehearsal",
                row_number=row_number,
            )
        )
    all_sample_ids = [
        row["metadata"]["sample_id"] for values in groups.values() for row in values
    ]
    if len(all_sample_ids) != len(set(all_sample_ids)):
        raise ArtifactError("duplicate sample_id in refinement candidates")
    rows, counts = _select_mixture(
        groups,
        weights=config["mixture"],
        maximum_rows=int(config["maximum_rows"]),
        seed=int(campaign.campaign["seed"]) + 1,
    )
    output = Path(output_dir).resolve()
    data_path = publish_jsonl(output / "train.jsonl", rows)
    reward_summary = {
        "count": len(episode_rewards),
        "mean": statistics.fmean(episode_rewards) if episode_rewards else 0.0,
        "minimum": min(episode_rewards, default=0.0),
        "maximum": max(episode_rewards, default=0.0),
    }
    body = {
        "schema": REFINEMENT_MANIFEST_SCHEMA,
        "stage": "reward_filtered_on_policy_sft",
        "campaign_digest": campaign.digest,
        "split_manifest_sha256": sha256_file(split_manifest_path),
        "split_manifest_body_sha256": split_manifest["manifest_body_sha256"],
        "inputs": {
            "episodes": {
                "path": str(Path(episodes_path).resolve()),
                "sha256": sha256_file(episodes_path),
            },
            "corrections": {
                "path": str(Path(corrections_path).resolve()),
                "sha256": sha256_file(corrections_path),
            },
            "rehearsal": {
                "path": str(Path(rehearsal_path).resolve()),
                "sha256": sha256_file(rehearsal_path),
            },
        },
        "episodes": {
            "eligible": len(episodes),
            "successful": successful_episodes,
            "failed": failed_episodes,
            "failed_without_verified_correction": missing_corrections,
        },
        "reward": {"weights": weights, "summary": reward_summary},
        "candidate_counts": {name: len(values) for name, values in groups.items()},
        "realized_counts": counts,
        "output": {"path": data_path.name, "sha256": sha256_file(data_path), "rows": len(rows)},
        "assistant_tokens_only": True,
        "maximum_corrections_per_failed_episode": 1,
        "heldout_scenarios_present": False,
    }
    manifest = dict(body)
    manifest["manifest_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    publish_json(output / "manifest.json", manifest)
    return manifest
