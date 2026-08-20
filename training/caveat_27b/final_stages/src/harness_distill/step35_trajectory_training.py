"""Dual-profile Step32-anchored on-policy BrowserUse action distillation.

The fresh data are sequential on-policy trajectories rolled in by Step32 and
corrected on the same live request.  Only the final structured ``action`` JSON
member receives CE.  Stable same-method wrong relative navigation targets may
receive bounded unlikelihood; generic DOM indices never do.  A shared PRIME
patch adds an exact LoRA proximal trust region around the loaded Step32 DCP.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import action_weighted_ce_training as base
from . import hero50_paired_training as core
from . import step32_rebind_training as prior
from . import step34_rollback_training as parent_training
from . import step35_trajectory_adapter as adapter
from .sol_dagger_training import sha256_file
from .step35_proximal_patch import (
    OUTPUT_SHA256,
    PROFILES,
    SOURCE_STEP,
)
from .step35_proximal_patch import (
    validate_evidence as validate_proximal_evidence,
)

PLAN_SCHEMA = "harness-distill.step35-trajectory-action-proximal-plan.v1"
RECEIPT_SCHEMA = "harness-distill.step35-trajectory-action-proximal-receipt.v1"
RENDER_AUDIT_SCHEMA = "harness-distill.step35-action-only-render-audit.v1"
SCIENTIFIC_LABEL = "step32_anchored_trajectory_level_on_policy_action_distillation"
PRIME_CHILD_MODULE = "harness_distill.step35_trajectory_training"


class Step35TrainingError(core.PairedBuyNowError):
    pass


def _canonical(value: Any) -> bytes:
    return base.canonical_json(value).encode()


def _action_member_span(content: str) -> tuple[int, int]:
    """Return the exact final ``"action": [...]`` member, excluding rationale."""

    lexemes = core._string_lexemes(content)
    keys = [
        item for item in lexemes if item.get("is_key") is True and item.get("decoded") == "action"
    ]
    if len(keys) != 1:
        raise Step35TrainingError("assistant lacks one exact action member")
    key = keys[0]
    start = int(key["start"])
    if start > 0 and content[start - 1] == '"':
        start -= 1
    colon = content.find(":", int(key["end"]))
    if colon < 0:
        raise Step35TrainingError("action member lacks a colon")
    value_start = colon + 1
    while value_start < len(content) and content[value_start].isspace():
        value_start += 1
    try:
        value, relative_end = json.JSONDecoder().raw_decode(content[value_start:])
    except json.JSONDecodeError as exc:
        raise Step35TrainingError("action member value is not structured JSON") from exc
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(action, dict) or len(action) != 1 for action in value)
    ):
        raise Step35TrainingError("action member is not structured BrowserUse actions")
    end = value_start + relative_end
    forbidden = {
        "thinking",
        "reasoning",
        "evaluation_previous_goal",
        "memory",
        "next_goal",
    }
    if any(
        item.get("is_key") is True
        and item.get("decoded") in forbidden
        and int(item["start"]) >= start
        for item in lexemes
    ):
        raise Step35TrainingError("non-action supervision follows the action member")
    if any(
        item.get("is_key") is True and int(item["start"]) > start and int(item["start"]) >= end
        for item in lexemes
    ):
        raise Step35TrainingError("action member is not final")
    return start, end


def _relative_target_span(
    content: str, expected_relative: str, *, action_span: tuple[int, int]
) -> tuple[int, int]:
    matches: list[tuple[int, int]] = []
    action_start, action_end = action_span
    for item in core._string_lexemes(content):
        decoded = item.get("decoded")
        if (
            item.get("is_key") is True
            or not isinstance(decoded, str)
            or int(item["start"]) < action_start
            or int(item["end"]) > action_end
        ):
            continue
        if expected_relative not in decoded:
            continue
        raw = content[int(item["start"]) : int(item["end"])]
        offset = raw.find(expected_relative)
        if offset >= 0 and raw.find(expected_relative, offset + 1) < 0:
            matches.append(
                (
                    int(item["start"]) + offset,
                    int(item["start"]) + offset + len(expected_relative),
                )
            )
    if len(matches) != 1:
        raise Step35TrainingError("stable rejected relative target is not unique")
    return matches[0]


def _render_pair_rows(corpus_path: Path, model_path: Path) -> tuple[list[dict[str, Any]], Any]:
    try:
        from renderers.base import create_renderer, load_tokenizer
        from renderers.configs import Qwen35RendererConfig
    except ImportError as exc:  # pragma: no cover - PRIME runtime only.
        raise Step35TrainingError("pinned Qwen renderer runtime is unavailable") from exc

    tokenizer = load_tokenizer(str(model_path))
    renderer = create_renderer(tokenizer, Qwen35RendererConfig(enable_thinking=True))
    corpus = core._jsonl(corpus_path)
    if len(corpus) != core.SAMPLES_PER_STEP:
        raise Step35TrainingError("Step35 action corpus cardinality drifted")
    rendered: list[dict[str, Any]] = []
    for row_index, row in enumerate(corpus):
        kind = row.get("kind")
        if kind not in {"chosen", "rejected"}:
            raise Step35TrainingError("Step35 corpus kind drifted")
        layout = core._render_layout(renderer=renderer, tokenizer=tokenizer, row=row)
        content = layout["content"]
        action_start, action_end = _action_member_span(content)
        if kind == "chosen":
            action = core._semantic_positions(
                layout["content_offsets"],
                layout["content_positions"],
                [(action_start, action_end)],
            )
            rejected: list[int] = []
            unsafe_anchor = None
            if not action:
                raise Step35TrainingError("chosen structured action mask is empty")
        else:
            expected = row.get("rejected_relative_target")
            if not isinstance(expected, str) or not expected.startswith("/"):
                raise Step35TrainingError("generic rejected action reached Step35 UL")
            span = _relative_target_span(content, expected, action_span=(action_start, action_end))
            rejected = core._semantic_positions(
                layout["content_offsets"], layout["content_positions"], [span]
            )
            action = []
            unsafe_anchor = span[0]
            if not rejected:
                raise Step35TrainingError("stable rejected target mask is empty")
        buckets = {
            "chosen_tail": [],
            "chosen_action": action,
            "rejected_unlikelihood": rejected,
        }
        live = sorted({position for values in buckets.values() for position in values})
        if any(position not in layout["full_assistant_positions"] for position in live):
            raise Step35TrainingError("action-only mask escaped assistant completion")
        rendered.append(
            {
                "row_index": row_index,
                "row_id": row["row_id"],
                "state_id": row["state_id"],
                "variant": row["variant"],
                "training_bucket": row["training_bucket"],
                "kind": kind,
                "env_name": f"c2_step35/{row_index:03d}/{kind}/{row['state_id'][:12]}",
                "token_ids": layout["token_ids"],
                "mask_positions": live,
                "bucket_positions": buckets,
                "unsafe_anchor_char": unsafe_anchor,
                "action_start_char": action_start,
                "action_end_char": action_end,
            }
        )
    kinds = Counter(row["kind"] for row in rendered)
    states = Counter(row["state_id"] for row in rendered)
    if (
        kinds
        != Counter(
            {
                "chosen": core.CHOSEN_SAMPLE_COUNT,
                "rejected": core.REJECTED_SAMPLE_COUNT,
            }
        )
        or Counter(states.values())
        != Counter({2: core.REJECTED_SAMPLE_COUNT, 1: core.CHOSEN_ONLY_STATE_COUNT})
        or Counter(row["training_bucket"] for row in rendered if row["kind"] == "chosen")
        != Counter(core.COMBINED_BUCKET_COUNTS)
        or Counter(row["training_bucket"] for row in rendered if row["kind"] == "rejected")
        != Counter(core.REJECTED_BUCKET_COUNTS)
    ):
        raise Step35TrainingError("rendered Step35 action cardinality drifted")
    return rendered, tokenizer


def _assign_action_weights(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    names = ("chosen_action", "rejected_unlikelihood")
    buckets = {
        name: [(row, position) for row in rows for position in row["bucket_positions"][name]]
        for name in names
    }
    if not buckets["chosen_action"]:
        raise Step35TrainingError("Step35 chosen action objective is empty")
    if bool(buckets["rejected_unlikelihood"]) != bool(core.REJECTED_SAMPLE_COUNT):
        raise Step35TrainingError("Step35 semantic pair inventory drifted")
    ce_tokens = len(buckets["chosen_action"])
    rl_tokens = len(buckets["rejected_unlikelihood"])
    denominators = {"chosen_action": ce_tokens, "rejected_unlikelihood": rl_tokens}
    for row in rows:
        row["ce_weights"] = [0.0] * len(row["token_ids"])
        row["rl_weights"] = [0.0] * len(row["token_ids"])
    audit: dict[str, Any] = {
        "chosen_tail": {
            "coefficient": "0/1",
            "tokens": 0,
            "states": 0,
            "weight_sum": 0.0,
            "normalizer_tokens": ce_tokens,
            "normalized_coefficient": 0.0,
            "categories": {},
        }
    }
    for name in names:
        stream = "rl_weights" if name == "rejected_unlikelihood" else "ce_weights"
        expected_counts = (
            core.REJECTED_BUCKET_COUNTS
            if name == "rejected_unlikelihood"
            else core.COMBINED_BUCKET_COUNTS
        )
        categories: dict[str, Any] = {}
        if not buckets[name]:
            if (
                name != "rejected_unlikelihood"
                or core.REJECTED_SAMPLE_COUNT != 0
                or expected_counts
                or core.OBJECTIVE_CATEGORY_MASSES[name]
            ):
                raise Step35TrainingError(f"{name} unexpectedly has no tokens")
            audit[name] = {
                "coefficient": "0",
                "tokens": 0,
                "states": 0,
                "weight_sum": 0.0,
                "normalizer_tokens": 0,
                "normalized_coefficient": 0.0,
                "categories": {},
            }
            continue
        for category, mass in core.OBJECTIVE_CATEGORY_MASSES[name].items():
            members = [
                (row, position)
                for row, position in buckets[name]
                if row["training_bucket"] == category
            ]
            by_state: dict[str, list[tuple[dict[str, Any], int]]] = {}
            for row, position in members:
                by_state.setdefault(row["state_id"], []).append((row, position))
            if len(by_state) != expected_counts[category]:
                raise Step35TrainingError(f"{name}/{category} state coverage drifted")
            target = float(mass * denominators[name])
            for state_members in by_state.values():
                token_weight = target / len(by_state) / len(state_members)
                for row, position in state_members:
                    row[stream][position] = token_weight
            observed = math.fsum(row[stream][position] for row, position in members)
            if not math.isclose(observed, target, rel_tol=1e-12, abs_tol=1e-8):
                raise Step35TrainingError(f"{name}/{category} weight mass drifted")
            categories[category] = {
                "coefficient": str(mass),
                "states": len(by_state),
                "tokens": len(members),
                "weight_sum": observed,
                "normalized_mass": observed / denominators[name],
            }
        coefficient = sum(core.OBJECTIVE_CATEGORY_MASSES[name].values(), Fraction())
        observed = math.fsum(row[stream][position] for row, position in buckets[name])
        expected = float(coefficient * denominators[name])
        if not math.isclose(observed, expected, rel_tol=1e-12, abs_tol=1e-8):
            raise Step35TrainingError(f"{name} total mass drifted")
        audit[name] = {
            "coefficient": str(coefficient),
            "tokens": len(buckets[name]),
            "states": (
                core.REJECTED_SAMPLE_COUNT
                if name == "rejected_unlikelihood"
                else core.CHOSEN_SAMPLE_COUNT
            ),
            "weight_sum": observed,
            "normalizer_tokens": denominators[name],
            "normalized_coefficient": observed / denominators[name],
            "categories": categories,
        }
    return {
        "objective": "trajectory_normalized_action_ce_plus_semantic_target_ul",
        "chosen_tail_tokens_per_state": 0,
        "ce_nonzero_tokens": ce_tokens,
        "rl_nonzero_tokens": rl_tokens,
        "buckets": audit,
        "state_balanced": True,
        "category_balanced": True,
        "category_coefficients": {
            key: str(value) for key, value in core.CATEGORY_COEFFICIENTS.items()
        },
        "prompt_tokens_weighted": 0,
        "json_syntax_policy": "chosen_entire_action_member_rejected_relative_target_only",
    }


def _validate_render_audit(path: Path, training_output: Path) -> dict[str, Any]:
    audit = json.loads(path.read_text())
    body = {key: value for key, value in audit.items() if key != "audit_body_sha256"}
    weights = audit.get("weight_audit") or {}
    rl_nonzero_tokens = weights.get("rl_nonzero_tokens")
    if (
        audit.get("schema") != RENDER_AUDIT_SCHEMA
        or audit.get("status") != "ok"
        or audit.get("audit_body_sha256") != hashlib.sha256(_canonical(body)).hexdigest()
        or audit.get("source_step") != SOURCE_STEP
        or audit.get("update_steps") != list(core.UPDATE_STEPS)
        or audit.get("optimizer_updates") != core.OPTIMIZER_UPDATES
        or audit.get("pairs_per_step") != core.PAIR_COUNT
        or audit.get("samples_per_step") != core.SAMPLES_PER_STEP
        or audit.get("chosen_samples_per_step") != core.CHOSEN_SAMPLE_COUNT
        or audit.get("rejected_samples_per_step") != core.REJECTED_SAMPLE_COUNT
        or audit.get("chosen_only_states_per_step") != core.CHOSEN_ONLY_STATE_COUNT
        or weights.get("prompt_tokens_weighted") != 0
        or weights.get("chosen_tail_tokens_per_state") != 0
        or (weights.get("buckets") or {}).get("chosen_tail", {}).get("tokens") != 0
        or not isinstance(rl_nonzero_tokens, int)
        or (rl_nonzero_tokens > 0) != (core.REJECTED_SAMPLE_COUNT > 0)
    ):
        raise Step35TrainingError("Step35 render audit header drifted")
    rows = audit.get("row_audits")
    if not isinstance(rows, list) or len(rows) != core.SAMPLES_PER_STEP:
        raise Step35TrainingError("Step35 render row inventory drifted")
    for row in rows:
        if row.get("chosen_tail_tokens") != 0:
            raise Step35TrainingError("reasoning/memory/goal token reached Step35")
        if row.get("kind") == "chosen":
            if (
                row.get("chosen_action_tokens", 0) <= 0
                or row.get("rejected_unlikelihood_tokens") != 0
                or row.get("rl_weight_mass") != 0.0
            ):
                raise Step35TrainingError("chosen structured action mask drifted")
        elif row.get("kind") == "rejected":
            if (
                row.get("chosen_action_tokens") != 0
                or row.get("rejected_unlikelihood_tokens", 0) <= 0
                or row.get("ce_weight_mass") != 0.0
                or not isinstance(row.get("unsafe_anchor_char"), int)
            ):
                raise Step35TrainingError("rejected semantic target mask drifted")
        else:
            raise Step35TrainingError("Step35 render row kind drifted")
    core._audit_prime_batches(training_output=training_output, audit_path=path)
    return audit


def _profile_values(profile_name: str) -> tuple[dict[str, Any], tuple[int, ...]]:
    if profile_name not in PROFILES:
        raise Step35TrainingError("Step35 profile must be A or B")
    profile = dict(PROFILES[profile_name])
    steps = adapter.PROFILE_STEPS[profile_name]
    if len(steps) != profile["optimizer_updates"] or steps[-1] != profile["final_step"]:
        raise Step35TrainingError("Step35 profile step inventory drifted")
    return profile, steps


@contextmanager
def _profile(contract: Mapping[str, Any], profile_name: str) -> Iterator[None]:
    profile, steps = _profile_values(profile_name)
    fields = prior._PROFILE_FIELDS
    saved = {name: getattr(core, name) for name in fields}
    extra_fields = ("CHOSEN_TAIL_TOKENS", "PARENT_DCP_TREE_SHA256")
    saved_extra = {name: getattr(core, name) for name in extra_fields}
    saved_validator = core.hero.validate_trainer_adapter
    saved_parent = core.validate_step29_parent_fast
    saved_render = core._render_pair_rows
    saved_render_validator = core._validate_render_audit
    saved_assign = core.assign_paired_weights
    saved_config = core._render_trainer_config
    saved_log_audit = core._trainer_log_audit
    saved_publish = core._publish
    saved_validate_prime = base.validate_prime_source
    value = contract["adapter"]
    pairs = contract["pairs"]
    counts = value["state_counts"]
    categories = {key: Fraction(item) for key, item in value["category_mix"].items()}
    objective_masses = {
        name: {key: Fraction(item) for key, item in members.items()}
        for name, members in value["objective_category_masses"].items()
    }
    combined = Counter(f"state::{row['row_id']}" for row in pairs)
    fresh_combined = Counter(
        f"state::{row['row_id']}"
        for row in pairs
        if row["source_kind"] == "step32_on_policy_sequential_trajectory"
    )
    rejected = Counter(
        f"state::{row['row_id']}" for row in pairs if row["objective_kind"] == "paired"
    )
    replacements = {
        "PLAN_SCHEMA": PLAN_SCHEMA,
        "RECEIPT_SCHEMA": RECEIPT_SCHEMA,
        "RENDER_AUDIT_SCHEMA": RENDER_AUDIT_SCHEMA,
        "SCIENTIFIC_LABEL": SCIENTIFIC_LABEL,
        "FRESH_PAIR_COUNT": counts["fresh"],
        "RETENTION_PAIR_COUNT": counts["step32_action_replay"],
        "PAIR_COUNT": counts["states"],
        "SAMPLES_PER_STEP": counts["samples"],
        "CHOSEN_SAMPLE_COUNT": counts["states"],
        "REJECTED_SAMPLE_COUNT": counts["paired"],
        "CHOSEN_ONLY_STATE_COUNT": counts["chosen_only"],
        "FRESH_VARIANT_COUNTS": dict(
            Counter(
                row["variant"]
                for row in pairs
                if row["source_kind"] == "step32_on_policy_sequential_trajectory"
            )
        ),
        "VARIANT_COUNTS": dict(Counter(row["variant"] for row in pairs)),
        "FRESH_BUCKET_COUNTS": dict(fresh_combined),
        "COMBINED_BUCKET_COUNTS": dict(combined),
        "REJECTED_BUCKET_COUNTS": dict(rejected),
        "CATEGORY_COEFFICIENTS": categories,
        "OBJECTIVE_CATEGORY_MASSES": objective_masses,
        "OBJECTIVE_COEFFICIENT_TEXT": {
            name: str(sum(members.values(), Fraction()))
            for name, members in objective_masses.items()
        },
        "SOURCE_STEP": SOURCE_STEP,
        "UPDATE_STEPS": steps,
        "FINAL_STEP": profile["final_step"],
        "OPTIMIZER_UPDATES": profile["optimizer_updates"],
        "LEARNING_RATE": profile["learning_rate"],
        "PARENT_RECEIPT_SHA256": adapter.PARENT_RECEIPT["receipt_file_sha256"],
        "PARENT_RECEIPT_BODY_SHA256": adapter.PARENT_RECEIPT["receipt_body_sha256"],
        "PARENT_ADAPTER_TREE_SHA256": adapter.PARENT_RECEIPT["candidate_tree_sha256"],
        "PARENT_DCP_TREE_SHA256": adapter.PARENT_RECEIPT["final_dcp_tree_sha256"],
        "PRIME_CHILD_MODULE": PRIME_CHILD_MODULE,
        "PRIME_CHILD_EXTRA_ARGS": (
            "--adapter",
            contract["adapter_path"],
            "--profile",
            profile_name,
        ),
        "CANDIDATE_NAME": f"step35-trajectory-proximal-{profile_name.lower()}",
        "CONTROL_LORA_NAME": f"c2-step35-trajectory-proximal-{profile_name.lower()}",
        "PAIR_SOURCE_KIND": (
            "step32_on_policy_sequential_actions_plus_sealed_step32_action_replay"
        ),
        "PAIR_COUNT_BREAKDOWN": {
            "fresh_trajectory_states": counts["fresh"],
            "sealed_step32_action_replay_states": counts["step32_action_replay"],
            "paired_semantic_states": counts["paired"],
            "logical_groups": {
                "fresh_trajectory_actions": counts["fresh"],
                "step32_action_replay": counts["step32_action_replay"],
            },
        },
        "ON_POLICY": True,
        "PAIR_POLICY_AUDIT": {
            "classification": "step32_roll_in_same_live_request_action_distillation",
            "trajectory_atomic": True,
            "chosen_supervision": "entire_structured_browseruse_action_json_only",
            "paired_unlikelihood": "same_method_stable_wrong_relative_target_only",
            "generic_dom_index_unlikelihood": False,
            "rationale_memory_next_goal_supervision": False,
            "evaluation_states": 0,
            "heldout_states": 0,
        },
        "SOURCE_GROUP_MASS": dict(value["source_group_mass"]),
        "CHOSEN_TAIL_TOKENS": 0,
        "ALLOW_LOOSE_CHOSEN_JSON": True,
    }
    for name, replacement in replacements.items():
        setattr(core, name, replacement)
    core.hero.validate_trainer_adapter = adapter.validate_trainer_adapter
    core.validate_step29_parent_fast = lambda path: parent_training.validate_step32_parent_fast(
        path, handoff=adapter.PARENT_RECEIPT
    )
    core._render_pair_rows = _render_pair_rows
    core._validate_render_audit = _validate_render_audit
    core.assign_paired_weights = _assign_action_weights

    def render_config(**kwargs: Any) -> bytes:
        payload = saved_config(**kwargs)
        text = payload.decode()
        if text.count("interval = 1000") != 1 or text.count("keep_last = 2") != 1:
            raise Step35TrainingError("PRIME checkpoint config anchor drifted")
        return (
            text.replace("interval = 1000", "interval = 1")
            .replace("keep_last = 2", "keep_last = 4")
            .encode()
        )

    core._render_trainer_config = render_config

    def log_audit(log_root: Path, topology: Mapping[str, Any]) -> dict[str, Any]:
        result = saved_log_audit(log_root, topology)
        joined = "\n".join(
            path.read_text(errors="replace")
            for path in sorted(log_root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        )
        anchor = (
            f"STEP35_PROXIMAL_ANCHOR profile={profile_name} source_step=32 "
            f"final_step={profile['final_step']}"
        )
        if anchor not in joined or joined.count("STEP35_PROXIMAL_AUDIT step=") < len(steps):
            raise Step35TrainingError("proximal trust-region logs are incomplete")
        result["proximal_profile"] = profile_name
        result["proximal_audits"] = len(steps)
        return result

    core._trainer_log_audit = log_audit

    def publish(path: Path, body: Mapping[str, Any], *, hash_field: str) -> dict[str, Any]:
        updated = dict(body)
        if updated.get("schema") == PLAN_SCHEMA:
            training_output = Path(updated["training_output"])
            updated.update(
                {
                    "profile": profile_name,
                    "proximal_trust_region": {
                        **profile,
                        "prime_patch_output_sha256": OUTPUT_SHA256,
                        "anchor": "all_local_step32_lora_shards_after_dcp_load",
                        "projection": "distributed_global_rms_after_every_update",
                        "full_vocab_reference_kl_claimed": False,
                    },
                    "candidate_checkpoints": [
                        {
                            "step": step,
                            "path": str(training_output / f"weights/step_{step}/lora_adapters"),
                            "selection_status": "pending_shared_train_only_probe2",
                        }
                        for step in steps
                    ],
                    "canary_selection_contract": value["canary_selection_contract"],
                    "semantic_mask": value["mask_policy"],
                    "normalization": value["normalization"],
                }
            )
        elif updated.get("schema") == RECEIPT_SCHEMA:
            output = Path(updated["candidate"]["path"]).parents[2]
            micro = []
            for step in steps:
                candidate_path = output / f"weights/step_{step}/lora_adapters"
                stable = candidate_path.parent / "STABLE"
                tree = core._tree_identity(candidate_path)
                micro.append(
                    {
                        "step": step,
                        **tree,
                        "adapter_config_sha256": sha256_file(
                            candidate_path / "adapter_config.json"
                        ),
                        "stable_marker_sha256": sha256_file(stable),
                        "selection_status": "pending_shared_train_only_probe2",
                    }
                )
            updated.update(
                {
                    "profile": profile_name,
                    "proximal_trust_region": {
                        **profile,
                        "prime_patch_output_sha256": OUTPUT_SHA256,
                    },
                    "candidate_checkpoints": micro,
                    "canary_selection_contract": value["canary_selection_contract"],
                }
            )
        return saved_publish(path, updated, hash_field=hash_field)

    core._publish = publish

    def validate_prime(root: str | Path, *, require_launch_patches: bool = False) -> dict[str, Any]:
        target = Path(root).resolve() / "src/prime_rl/trainer/rl/train.py"
        proximal_present = target.is_file() and sha256_file(target) == OUTPUT_SHA256
        if not proximal_present:
            return saved_validate_prime(root, require_launch_patches=require_launch_patches)
        original = base.TRAIN_POSTPATCH_SHA256
        base.TRAIN_POSTPATCH_SHA256 = OUTPUT_SHA256
        try:
            return saved_validate_prime(root, require_launch_patches=True)
        finally:
            base.TRAIN_POSTPATCH_SHA256 = original

    base.validate_prime_source = validate_prime
    try:
        yield
    finally:
        core.hero.validate_trainer_adapter = saved_validator
        core.validate_step29_parent_fast = saved_parent
        core._render_pair_rows = saved_render
        core._validate_render_audit = saved_render_validator
        core.assign_paired_weights = saved_assign
        core._render_trainer_config = saved_config
        core._trainer_log_audit = saved_log_audit
        core._publish = saved_publish
        base.validate_prime_source = saved_validate_prime
        for name, original in saved_extra.items():
            setattr(core, name, original)
        for name, original in saved.items():
            setattr(core, name, original)


def prepare_training(
    *,
    adapter_path: str | Path,
    profile_name: str,
    parent_receipt_path: str | Path,
    output_dir: str | Path,
    prime_root: str | Path,
    artifact_git_sha: str,
    topology_name: str = "cp2_dp2",
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    contract = adapter.validate_trainer_adapter(adapter_path)
    with _profile(contract, profile_name):
        return core.prepare_paired_buy_now_training(
            adapter_path=adapter_path,
            parent_receipt_path=parent_receipt_path,
            output_dir=output_dir,
            prime_root=prime_root,
            artifact_git_sha=artifact_git_sha,
            topology_name=topology_name,
            fallback_reason=fallback_reason,
        )


def validate_training_plan(
    path: str | Path, *, require_launch_patches: bool = False
) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text())
    profile_name = raw.get("profile")
    frozen = raw.get("frozen_adapter") or {}
    contract = adapter.validate_trainer_adapter(str(frozen.get("path", "")))
    profile, steps = _profile_values(str(profile_name))
    with _profile(contract, str(profile_name)):
        plan = core.validate_training_plan(path, require_launch_patches=require_launch_patches)
    if (
        plan.get("profile") != profile_name
        or plan.get("proximal_trust_region")
        != {
            **profile,
            "prime_patch_output_sha256": OUTPUT_SHA256,
            "anchor": "all_local_step32_lora_shards_after_dcp_load",
            "projection": "distributed_global_rms_after_every_update",
            "full_vocab_reference_kl_claimed": False,
        }
        or [item.get("step") for item in plan.get("candidate_checkpoints", [])] != list(steps)
        or plan.get("canary_selection_contract") != contract["adapter"]["canary_selection_contract"]
        or plan.get("semantic_mask") != contract["adapter"]["mask_policy"]
        or plan.get("normalization") != contract["adapter"]["normalization"]
    ):
        raise Step35TrainingError("Step35 plan profile/proximal contract drifted")
    if require_launch_patches:
        evidence = Path(path).resolve().parent / "launch_evidence/step35_proximal_patch.json"
        validate_proximal_evidence(evidence, prime_root=plan["prime"]["root"])
    return plan


def write_training_receipt(*, plan_path: str | Path, executor_git_sha: str) -> dict[str, Any]:
    plan = json.loads(Path(plan_path).read_text())
    contract = adapter.validate_trainer_adapter(plan["frozen_adapter"]["path"])
    with _profile(contract, str(plan["profile"])):
        return core.write_training_receipt(plan_path=plan_path, executor_git_sha=executor_git_sha)


def validate_training_receipt(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text())
    plan = json.loads(Path(raw["plan_path"]).read_text())
    contract = adapter.validate_trainer_adapter(plan["frozen_adapter"]["path"])
    profile, steps = _profile_values(str(raw.get("profile")))
    with _profile(contract, str(raw["profile"])):
        receipt = core.validate_training_receipt(path)
    if (
        receipt.get("profile") != plan.get("profile")
        or receipt.get("proximal_trust_region")
        != {**profile, "prime_patch_output_sha256": OUTPUT_SHA256}
        or [item.get("step") for item in receipt.get("candidate_checkpoints", [])] != list(steps)
        or receipt.get("canary_selection_contract")
        != contract["adapter"]["canary_selection_contract"]
    ):
        raise Step35TrainingError("Step35 receipt profile/candidate contract drifted")
    validate_proximal_evidence(
        Path(raw["plan_path"]).resolve().parent / "launch_evidence/step35_proximal_patch.json",
        prime_root=plan["prime"]["root"],
    )
    for item in receipt["candidate_checkpoints"]:
        candidate = Path(item["path"])
        observed = core._tree_identity(candidate)
        if any(observed[key] != item.get(key) for key in ("tree_sha256", "files", "bytes")):
            raise Step35TrainingError("micro-candidate tree changed after receipt")
    return receipt


def _prime_child(argv: Sequence[str]) -> int:
    child = argparse.ArgumentParser(add_help=False)
    child.add_argument("command", choices=("_prime-materialize", "_prime-audit"))
    child.add_argument("--adapter", required=True)
    child.add_argument("--profile", required=True)
    known, remaining = child.parse_known_args(argv)
    contract = adapter.validate_trainer_adapter(known.adapter)
    with _profile(contract, known.profile):
        return core._prime_main([known.command, *remaining])


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if any(argument.startswith("_prime-") for argument in arguments):
        return _prime_child(arguments)
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--adapter", required=True)
    prepare.add_argument("--profile", choices=sorted(PROFILES), required=True)
    prepare.add_argument("--parent-receipt", required=True)
    prepare.add_argument("--output-dir", required=True)
    prepare.add_argument("--prime-root", required=True)
    prepare.add_argument("--artifact-git-sha", required=True)
    prepare.add_argument("--topology", default="cp2_dp2")
    prepare.add_argument("--fallback-reason")
    check = commands.add_parser("validate-plan")
    check.add_argument("--plan", required=True)
    check.add_argument("--require-launch-patches", action="store_true")
    write = commands.add_parser("write-receipt")
    write.add_argument("--plan", required=True)
    write.add_argument("--executor-git-sha", required=True)
    receipt = commands.add_parser("validate-receipt")
    receipt.add_argument("--receipt", required=True)
    args = parser.parse_args(arguments)
    if args.command == "prepare":
        result = prepare_training(
            adapter_path=args.adapter,
            profile_name=args.profile,
            parent_receipt_path=args.parent_receipt,
            output_dir=args.output_dir,
            prime_root=args.prime_root,
            artifact_git_sha=args.artifact_git_sha,
            topology_name=args.topology,
            fallback_reason=args.fallback_reason,
        )
    elif args.command == "validate-plan":
        result = validate_training_plan(
            args.plan, require_launch_patches=args.require_launch_patches
        )
    elif args.command == "write-receipt":
        result = write_training_receipt(plan_path=args.plan, executor_git_sha=args.executor_git_sha)
    else:
        result = validate_training_receipt(args.receipt)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
