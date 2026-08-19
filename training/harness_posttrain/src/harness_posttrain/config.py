"""Campaign configuration loading and fail-closed validation."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .artifacts import ArtifactError, require_regular_file

EXPECTED_MODEL = "Qwen/Qwen3.5-27B"
EXPECTED_REVISION = "fc05daec18b0a78c049392ed2e771dde82bdf654"
EXPECTED_SCAFFOLD = "browseruse-deliberative"


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ArtifactError(f"{label} must be a mapping")
    return dict(value)


def load_yaml(path: str | Path) -> dict[str, Any]:
    target = require_regular_file(path)
    try:
        value = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ArtifactError(f"invalid YAML: {target}") from exc
    payload = _mapping(value, str(target))
    if payload.get("schema_version") != 1:
        raise ArtifactError(f"unsupported schema_version in {target}")
    return payload


def _sum_to_one(value: Mapping[str, Any], label: str) -> None:
    weights = [float(item) for item in value.values()]
    if not weights or any(item <= 0 for item in weights) or abs(sum(weights) - 1.0) > 1e-9:
        raise ArtifactError(f"{label} must contain positive weights summing to one")


@dataclass(frozen=True)
class Campaign:
    path: Path
    root: Path
    campaign: dict[str, Any]
    model: dict[str, Any]
    stack: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> Campaign:
        target = require_regular_file(path)
        root = target.parent.parent.resolve()
        campaign = load_yaml(target)

        def related(key: str) -> dict[str, Any]:
            configured = Path(str(campaign[key]))
            return load_yaml(configured if configured.is_absolute() else root / configured)

        result = cls(
            path=target,
            root=root,
            campaign=campaign,
            model=related("model_config"),
            stack=related("stack_config"),
        )
        result.validate()
        return result

    @property
    def digest(self) -> str:
        digest = hashlib.sha256()
        for path in (
            self.path,
            self.root / str(self.campaign["model_config"]),
            self.root / str(self.campaign["stack_config"]),
        ):
            target = require_regular_file(path)
            relative = target.relative_to(self.root).as_posix().encode()
            payload = target.read_bytes()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
        return digest.hexdigest()

    def split_scenarios(self, split: str) -> set[str]:
        splits = _mapping(self.campaign["splits"], "splits")
        if split not in {"train", "selection", "development", "final"}:
            raise ArtifactError(f"unsupported split: {split}")
        body = _mapping(splits[split], f"splits.{split}")
        result: set[str] = set()
        for source in ("procedural", "amazon"):
            values = body.get(source)
            if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                raise ArtifactError(f"splits.{split}.{source} must be a string list")
            result.update(values)
        return result

    def targeted_sft_candidate(self, name: str) -> dict[str, Any]:
        targeted = _mapping(self.campaign["targeted_sft"], "targeted_sft")
        candidates = _mapping(targeted["candidates"], "targeted_sft.candidates")
        if name not in candidates:
            raise ArtifactError(f"unknown targeted SFT candidate: {name}")
        body = _mapping(candidates[name], f"targeted_sft.candidates.{name}")
        return {**targeted, **body, "candidate": name}

    def validate(self) -> None:
        objective = _mapping(self.campaign.get("objective"), "objective")
        if objective.get("scaffold") != EXPECTED_SCAFFOLD or not objective.get(
            "scaffold_must_be_unchanged"
        ):
            raise ArtifactError(
                "the campaign must use the unchanged browseruse-deliberative scaffold"
            )
        if objective.get("primary_metric") != "strict_binary":
            raise ArtifactError("strict_binary must be the primary metric")
        if self.model.get("model_id") != EXPECTED_MODEL or self.model.get(
            "revision"
        ) != EXPECTED_REVISION:
            raise ArtifactError("model identity drifted from the pinned Qwen3.5-27B snapshot")

        split_sets = {
            name: self.split_scenarios(name)
            for name in ("train", "selection", "development", "final")
        }
        if any(
            split_sets[left] & split_sets[right]
            for index, left in enumerate(split_sets)
            for right in tuple(split_sets)[index + 1 :]
        ):
            raise ArtifactError("scenario-cluster splits must be pairwise disjoint")
        if split_sets["train"] != {"procedural_train"}:
            raise ArtifactError("training must contain only procedural_train")
        if split_sets["selection"] != {"procedural_validation"}:
            raise ArtifactError("checkpoint selection must use only procedural_validation")
        if split_sets["development"] != {"laptop"}:
            raise ArtifactError("Amazon development split drifted")
        if {"procedural_test", "office_chair", "mattress", "backpack", "tent"} != split_sets[
            "final"
        ]:
            raise ArtifactError("Amazon confirmatory split drifted")

        targeted = _mapping(self.campaign.get("targeted_sft"), "targeted_sft")
        refinement = _mapping(self.campaign.get("refinement"), "refinement")
        opd = _mapping(self.campaign.get("optional_opd_smoke"), "optional_opd_smoke")
        candidates = _mapping(targeted.get("candidates"), "targeted_sft.candidates")
        if set(candidates) != {"balanced", "protocol-heavy", "recovery-heavy"}:
            raise ArtifactError("targeted SFT candidate set drifted")
        for name, value in candidates.items():
            body = _mapping(value, f"targeted_sft.candidates.{name}")
            _sum_to_one(
                _mapping(body.get("mixture"), f"targeted_sft.candidates.{name}.mixture"),
                f"targeted_sft.candidates.{name}.mixture",
            )
            if body.get("maximum_rows") != 4800:
                raise ArtifactError(f"targeted SFT row count drifted: {name}")
        _sum_to_one(_mapping(refinement.get("mixture"), "refinement.mixture"), "refinement.mixture")
        _sum_to_one(
            _mapping(refinement.get("reward_weights"), "refinement.reward_weights"),
            "refinement.reward_weights",
        )
        if targeted.get("optimizer_updates") != 20 or targeted.get("checkpoint_updates") != [
            5, 10, 20
        ]:
            raise ArtifactError("targeted SFT candidate schedule drifted")
        raw_pool = _mapping(targeted.get("raw_pool"), "targeted_sft.raw_pool")
        if raw_pool.get("tasks") != 1600 or raw_pool.get("rows_per_task") != {
            "contract": 2,
            "interaction": 2,
            "rehearsal": 1,
        }:
            raise ArtifactError("targeted SFT raw-pool plan drifted")
        if refinement.get("optimizer_updates") not in range(10, 21):
            raise ArtifactError("refinement must stay within 10--20 updates")
        if refinement.get("maximum_corrections_per_failed_episode") != 1:
            raise ArtifactError("only the first decisive failure may be corrected")
        if refinement.get("method") != "reward_filtered_on_policy_contract_sft":
            raise ArtifactError("the default refinement method drifted")
        rollout = _mapping(refinement.get("rollout_plan"), "refinement.rollout_plan")
        if (
            rollout.get("fresh_tasks") != 64
            or rollout.get("episodes_per_task") != 4
            or rollout.get("total_episodes") != 256
            or rollout.get("conditions") != {"truthful_steered": 0.75, "clean": 0.25}
        ):
            raise ArtifactError("refinement rollout plan drifted")
        contract_reward = _mapping(
            refinement.get("contract_reward"), "refinement.contract_reward"
        )
        if contract_reward != {
            "syntax_valid": 0.25,
            "semantic_exact": 0.75,
            "retain_threshold": 1.0,
        }:
            raise ArtifactError("contract refinement reward drifted")

        exact_opd_bounds = {
            "enabled_by_default": False,
            "episodes": 64,
            "maximum_decisions_per_episode": 2,
            "maximum_replay_rows": 128,
            "optimizer_updates": 12,
            "sequence_length": 32768,
            "maximum_completion_tokens": 1024,
            "maximum_inflight_rollouts": 8,
            "worker_pool_size": 8,
            "vllm_max_num_seqs": 16,
            "total_gpus": 8,
            "inference_gpus": 4,
            "trainer_gpus": 4,
            "minimum_host_memory_gib": 1024,
            "wall_clock_kill_seconds": 10800,
            "pure_reverse_kl_only": True,
            "corrective_ce_in_same_update": False,
        }
        for key, expected in exact_opd_bounds.items():
            if opd.get(key) != expected:
                raise ArtifactError(f"optional OPD bound drifted: {key}")

        cluster = _mapping(self.campaign.get("cluster"), "cluster")
        expected_cluster = {
            "namespace": "bonete61",
            "queue": "bonete61",
            "workstream": "socialreasoning",
            "priority": "p0",
        }
        for key, expected in expected_cluster.items():
            if cluster.get(key) != expected:
                raise ArtifactError(f"cluster setting drifted: {key}")
        selection_gate = _mapping(self.campaign.get("selection_gate"), "selection_gate")
        if (
            selection_gate.get("procedural_shadow_tasks") != 64
            or selection_gate.get("episodes_per_task") != 1
            or selection_gate.get("checkpoint_tool_tasks") != 8
            or selection_gate.get("rank_order")
            != ["semantic_exact_rate", "syntax_valid_rate", "checkpoint_tool_valid_rate"]
        ):
            raise ArtifactError("procedural checkpoint-selection plan drifted")
