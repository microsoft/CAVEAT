"""One-update Step34 rollback trainer anchored on the successful Step32 DCP."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import action_weighted_ce_training as base
from . import hero50_paired_training as core
from . import step32_rebind_training as prior
from . import step34_rollback_adapter as adapter
from .sol_dagger_training import sha256_file

PLAN_SCHEMA = "harness-distill.step34-rollback-action-mixed-plan.v1"
RECEIPT_SCHEMA = "harness-distill.step34-rollback-action-mixed-receipt.v1"
RENDER_AUDIT_SCHEMA = "harness-distill.step34-rollback-action-render-audit.v1"
SCIENTIFIC_LABEL = "step32_rollback_stable_navigation_action_plus_full_downstream_retention"
CANDIDATE_NAME = "step34-rollback-step32-stable-action-mixed"
CONTROL_LORA_NAME = "c2-step34-rollback-stable-action"
PRIME_CHILD_MODULE = "harness_distill.step34_rollback_training"
PARENT_RECEIPT_SCHEMA = "harness-distill.step32-rebind-paired-receipt.v1"


class Step34TrainingError(core.PairedBuyNowError):
    pass


def _canonical(value: Any) -> bytes:
    return base.canonical_json(value).encode()


def _positive_dcp(value: Mapping[str, Any]) -> bool:
    digest = value.get("tree_sha256")
    return bool(
        isinstance(value.get("path"), str)
        and isinstance(digest, str)
        and len(digest) == 64
        and type(value.get("files")) is int
        and value["files"] > 0
        and type(value.get("bytes")) is int
        and value["bytes"] > 0
    )


def validate_step32_parent_fast(path: str | Path, *, handoff: Mapping[str, Any]) -> dict[str, Any]:
    if dict(handoff) != adapter.PINNED_HANDOFF:
        raise Step34TrainingError("step32 parent handoff identity drifted")
    receipt_path = Path(path).resolve()
    if (
        not receipt_path.is_file()
        or receipt_path.is_symlink()
        or sha256_file(receipt_path) != handoff["receipt_file_sha256"]
    ):
        raise Step34TrainingError("step32 parent receipt bytes drifted")
    receipt = json.loads(receipt_path.read_text())
    body = {key: value for key, value in receipt.items() if key != "receipt_body_sha256"}
    candidate = receipt.get("candidate")
    dcp = receipt.get("final_dcp")
    if (
        receipt.get("schema") != PARENT_RECEIPT_SCHEMA
        or receipt.get("status") != "ok"
        or receipt.get("receipt_body_sha256") != handoff["receipt_body_sha256"]
        or hashlib.sha256(_canonical(body)).hexdigest() != handoff["receipt_body_sha256"]
        or receipt.get("source_step") != 31
        or receipt.get("update_steps") != [32]
        or receipt.get("final_step") != adapter.SOURCE_STEP
        or receipt.get("optimizer_updates") != 1
        or receipt.get("learning_rate") != 1.0e-6
        or not isinstance(candidate, Mapping)
        or candidate.get("name") != "step32-rebind-bridge-cleanup-paired"
        or candidate.get("update") != adapter.SOURCE_STEP
        or candidate.get("tree_sha256") != handoff["candidate_tree_sha256"]
        or not isinstance(dcp, Mapping)
        or not _positive_dcp(dcp)
        or dcp.get("tree_sha256") != handoff["final_dcp_tree_sha256"]
    ):
        raise Step34TrainingError("step32 terminal receipt lineage drifted")
    plan_path = Path(str(receipt.get("plan_path", ""))).resolve()
    if (
        not plan_path.is_file()
        or plan_path.is_symlink()
        or sha256_file(plan_path) != receipt.get("plan_sha256")
    ):
        raise Step34TrainingError("step32 parent plan bytes drifted")
    plan = json.loads(plan_path.read_text())
    plan_body = {key: value for key, value in plan.items() if key != "plan_body_sha256"}
    if (
        plan.get("plan_body_sha256") != receipt.get("plan_body_sha256")
        or hashlib.sha256(_canonical(plan_body)).hexdigest() != receipt.get("plan_body_sha256")
        or not isinstance(plan.get("parent_model"), str)
    ):
        raise Step34TrainingError("step32 parent plan self-binding drifted")
    candidate_path = Path(str(candidate.get("path", ""))).resolve()
    dcp_path = Path(str(dcp.get("path", ""))).resolve()
    stable = candidate_path.parent / "STABLE"
    if (
        not candidate_path.is_dir()
        or candidate_path.is_symlink()
        or not (candidate_path / "adapter_config.json").is_file()
        or sha256_file(candidate_path / "adapter_config.json")
        != candidate.get("adapter_config_sha256")
        or not stable.is_file()
        or stable.is_symlink()
        or sha256_file(stable) != candidate.get("stable_marker_sha256")
        or not dcp_path.is_dir()
        or dcp_path.is_symlink()
        or not (dcp_path / ".metadata").is_file()
        or (dcp_path / ".metadata").is_symlink()
    ):
        raise Step34TrainingError("step32 terminal adapter/DCP is absent or unsafe")
    core.PARENT_DCP_TREE_SHA256 = str(dcp["tree_sha256"])
    core.PARENT_DCP_FILES = int(dcp["files"])
    core.PARENT_DCP_BYTES = int(dcp["bytes"])
    return {"receipt": receipt, "plan": plan, "receipt_path": str(receipt_path)}


def _origin_invariant_spans(
    content: str,
    *,
    expected_relative: str,
    include_method: bool,
) -> list[tuple[int, int]]:
    """Select ``navigate`` and a relative path/query, never origin or port."""

    lexemes = core._string_lexemes(content)
    method = [
        (int(item["start"]), int(item["end"]))
        for item in lexemes
        if item.get("is_key") is True and item.get("decoded") == "navigate"
    ]
    urls = []
    for item in lexemes:
        if item.get("is_key") is True or not isinstance(item.get("decoded"), str):
            continue
        try:
            _, relative = adapter.navigation_action(
                {"content": json.dumps({"action": [{"navigate": {"url": item["decoded"]}}]})}
            )
        except adapter.Step34AdapterError:
            continue
        if relative == expected_relative:
            urls.append(item)
    if len(method) != 1 or len(urls) != 1:
        raise Step34TrainingError("origin-invariant navigate lexemes are not unique")
    url_item = urls[0]
    raw = content[int(url_item["start"]) : int(url_item["end"])]
    offset = raw.find(expected_relative)
    if offset < 0 or raw.find(expected_relative, offset + 1) >= 0:
        raise Step34TrainingError("relative target is not one exact raw URL substring")
    target = (
        int(url_item["start"]) + offset,
        int(url_item["start"]) + offset + len(expected_relative),
    )
    return ([method[0]] if include_method else []) + [target]


def _render_pair_rows(corpus_path: Path, model_path: Path) -> tuple[list[dict[str, Any]], Any]:
    try:
        from renderers.base import create_renderer, load_tokenizer
        from renderers.configs import Qwen35RendererConfig
    except ImportError as exc:  # pragma: no cover - PRIME runtime only.
        raise Step34TrainingError("pinned Qwen renderer runtime is unavailable") from exc

    tokenizer = load_tokenizer(str(model_path))
    renderer = create_renderer(tokenizer, Qwen35RendererConfig(enable_thinking=True))
    corpus = core._jsonl(corpus_path)
    if len(corpus) != core.SAMPLES_PER_STEP:
        raise Step34TrainingError("Step34 corpus cardinality drifted")
    rendered_rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(corpus):
        kind = row.get("kind")
        if kind not in {"chosen", "rejected"}:
            raise Step34TrainingError("Step34 corpus kind drifted")
        layout = core._render_layout(renderer=renderer, tokenizer=tokenizer, row=row)
        content = layout["content"]
        lexemes = core._string_lexemes(content)
        fresh = row.get("mask_policy") == adapter.FRESH_MASK_POLICY
        if kind == "chosen":
            if fresh:
                if row.get("chosen_action_method") != "navigate":
                    raise Step34TrainingError("fresh chosen method is not navigate")
                spans = _origin_invariant_spans(
                    content,
                    expected_relative=str(row["chosen_relative_target"]),
                    include_method=True,
                )
                action_start = core._action_start_loose(content, lexemes)
                rationale: list[int] = []
                action = core._semantic_positions(
                    layout["content_offsets"], layout["content_positions"], spans
                )
            else:
                action_start = (
                    core._action_start_loose(content, lexemes)
                    if core.ALLOW_LOOSE_CHOSEN_JSON
                    and row.get("objective_kind") == "chosen_only"
                    else base.final_action_member_start(content)
                )
                rationale_all = core._semantic_positions(
                    layout["content_offsets"],
                    layout["content_positions"],
                    core._value_spans(lexemes, before=action_start),
                )
                rationale = rationale_all[-core.CHOSEN_TAIL_TOKENS :]
                action = core._semantic_positions(
                    layout["content_offsets"],
                    layout["content_positions"],
                    core._action_semantic_spans(content, lexemes, action_start),
                )
                if len(rationale) != core.CHOSEN_TAIL_TOKENS:
                    raise Step34TrainingError("sealed downstream reasoning-tail mask drifted")
            rejected: list[int] = []
            unsafe_anchor = None
            if not action:
                raise Step34TrainingError("chosen action-only semantic mask is empty")
        else:
            action_start = core._action_start_loose(content, lexemes)
            if fresh:
                if row.get("rejected_semantic_policy") != "stable_wrong_relative_target_only":
                    raise Step34TrainingError("generic rejected action reached unlikelihood")
                spans = _origin_invariant_spans(
                    content,
                    expected_relative=str(row["rejected_relative_target"]),
                    include_method=False,
                )
                unsafe_anchor = spans[0][0]
            else:
                spans, unsafe_anchor = core._rejected_unsafe_spans(
                    content, lexemes, action_start
                )
            rejected = core._semantic_positions(
                layout["content_offsets"], layout["content_positions"], spans
            )
            rationale = []
            action = []
            if not rejected:
                raise Step34TrainingError("stable rejected semantic mask is empty")
        buckets = {
            "chosen_tail": rationale,
            "chosen_action": action,
            "rejected_unlikelihood": rejected,
        }
        live = sorted({position for members in buckets.values() for position in members})
        if sum(len(members) for members in buckets.values()) != len(live):
            raise Step34TrainingError("Step34 semantic token buckets overlap")
        if any(position not in layout["full_assistant_positions"] for position in live):
            raise Step34TrainingError("Step34 semantic mask escaped assistant completion")
        rendered_rows.append(
            {
                "row_index": row_index,
                "row_id": row["row_id"],
                "state_id": row["state_id"],
                "variant": row["variant"],
                "training_bucket": row["training_bucket"],
                "kind": kind,
                "env_name": (
                    f"c2_step34/{row_index:02d}/{kind}/"
                    f"{hashlib.sha256(row['state_id'].encode()).hexdigest()[:12]}"
                ),
                "token_ids": layout["token_ids"],
                "mask_positions": live,
                "bucket_positions": buckets,
                "unsafe_anchor_char": unsafe_anchor,
                "action_start_char": action_start,
            }
        )
    kinds = Counter(row["kind"] for row in rendered_rows)
    states = Counter(row["state_id"] for row in rendered_rows)
    if (
        kinds
        != {"chosen": core.CHOSEN_SAMPLE_COUNT, "rejected": core.REJECTED_SAMPLE_COUNT}
        or Counter(states.values())
        != Counter({2: core.REJECTED_SAMPLE_COUNT, 1: core.CHOSEN_ONLY_STATE_COUNT})
        or Counter(
            row["training_bucket"] for row in rendered_rows if row["kind"] == "chosen"
        )
        != Counter(core.COMBINED_BUCKET_COUNTS)
        or Counter(
            row["training_bucket"] for row in rendered_rows if row["kind"] == "rejected"
        )
        != Counter(core.REJECTED_BUCKET_COUNTS)
    ):
        raise Step34TrainingError("rendered Step34 cardinality drifted")
    return rendered_rows, tokenizer


def _validate_render_audit(path: Path, training_output: Path) -> dict[str, Any]:
    audit = json.loads(path.read_text(encoding="utf-8"))
    body = {key: value for key, value in audit.items() if key != "audit_body_sha256"}
    weights = audit.get("weight_audit") or {}
    if (
        audit.get("schema") != RENDER_AUDIT_SCHEMA
        or audit.get("status") != "ok"
        or audit.get("audit_body_sha256") != hashlib.sha256(_canonical(body)).hexdigest()
        or audit.get("source_step") != adapter.SOURCE_STEP
        or audit.get("update_steps") != list(adapter.UPDATE_STEPS)
        or audit.get("optimizer_updates") != 1
        or audit.get("pairs_per_step") != core.PAIR_COUNT
        or audit.get("samples_per_step") != core.SAMPLES_PER_STEP
        or audit.get("chosen_samples_per_step") != core.CHOSEN_SAMPLE_COUNT
        or audit.get("rejected_samples_per_step") != core.REJECTED_SAMPLE_COUNT
        or audit.get("chosen_only_states_per_step") != core.CHOSEN_ONLY_STATE_COUNT
        or weights.get("prompt_tokens_weighted") != 0
    ):
        raise Step34TrainingError("Step34 render audit header drifted")
    for objective, categories in core.OBJECTIVE_CATEGORY_MASSES.items():
        expected_total = sum(categories.values(), Fraction())
        item = weights.get("buckets", {}).get(objective, {})
        if item.get("coefficient") != (
            f"{expected_total.numerator}/{expected_total.denominator}"
        ) or not math.isclose(
            item.get("normalized_coefficient", -1.0), float(expected_total), abs_tol=1e-12
        ):
            raise Step34TrainingError(f"Step34 {objective} mass drifted")
        expected_counts = (
            core.REJECTED_BUCKET_COUNTS
            if objective == "rejected_unlikelihood"
            else core.COMBINED_BUCKET_COUNTS
        )
        for category, mass in categories.items():
            category_item = item.get("categories", {}).get(category, {})
            if (
                category_item.get("coefficient")
                != f"{mass.numerator}/{mass.denominator}"
                or category_item.get("states") != expected_counts[category]
                or not math.isclose(
                    category_item.get("normalized_mass", -1.0), float(mass), abs_tol=1e-12
                )
            ):
                raise Step34TrainingError(f"Step34 {objective}/{category} mass drifted")
    rows = audit.get("row_audits")
    if not isinstance(rows, list) or len(rows) != core.SAMPLES_PER_STEP:
        raise Step34TrainingError("Step34 render row inventory drifted")
    for row in rows:
        fresh = str(row.get("training_bucket", "")).startswith(adapter.UPSTREAM_GROUP)
        if row.get("kind") == "chosen":
            expected_tail = 0 if fresh else core.CHOSEN_TAIL_TOKENS
            if (
                row.get("chosen_tail_tokens") != expected_tail
                or row.get("chosen_action_tokens", 0) <= 0
                or row.get("rejected_unlikelihood_tokens") != 0
                or row.get("rl_weight_mass") != 0.0
            ):
                raise Step34TrainingError("Step34 chosen action mask drifted")
        elif row.get("kind") == "rejected":
            if (
                row.get("chosen_tail_tokens") != 0
                or row.get("chosen_action_tokens") != 0
                or row.get("rejected_unlikelihood_tokens", 0) <= 0
                or row.get("ce_weight_mass") != 0.0
                or not isinstance(row.get("unsafe_anchor_char"), int)
            ):
                raise Step34TrainingError("Step34 rejected semantic mask drifted")
        else:
            raise Step34TrainingError("Step34 render kind drifted")
    core._audit_prime_batches(training_output=training_output, audit_path=path)
    return audit


@contextmanager
def _profile(contract: Mapping[str, Any]) -> Iterator[None]:
    fields = prior._PROFILE_FIELDS
    saved = {name: getattr(core, name) for name in fields}
    saved_validator = core.hero.validate_trainer_adapter
    saved_parent = core.validate_step29_parent_fast
    saved_render = core._render_pair_rows
    saved_render_validator = core._validate_render_audit
    saved_publish = core._publish
    value = contract["adapter"]
    counts = value["state_counts"]
    pairs = contract["pairs"]
    fresh = [row for row in pairs if row.get("retention_kind") is None]
    retained = [row for row in pairs if row.get("retention_kind") is not None]
    coefficients = {key: Fraction(item) for key, item in value["objective"]["category_mix"].items()}
    masses = {
        name: {key: Fraction(item) for key, item in category.items()}
        for name, category in value["objective"]["objective_category_masses"].items()
    }
    objective_names = {
        "chosen_tail": "chosen_pre_action_tail_ce",
        "chosen_action": "chosen_action_ce",
        "rejected_unlikelihood": "rejected_semantic_unlikelihood",
    }
    objective_text = {
        objective_names[name]: (
            f"{sum(category.values(), Fraction()).numerator}/"
            f"{sum(category.values(), Fraction()).denominator}"
        )
        for name, category in masses.items()
    }
    rejected = Counter(row["training_bucket"] for row in pairs if row["objective_kind"] == "paired")
    replacements = {
        "PLAN_SCHEMA": PLAN_SCHEMA,
        "RECEIPT_SCHEMA": RECEIPT_SCHEMA,
        "RENDER_AUDIT_SCHEMA": RENDER_AUDIT_SCHEMA,
        "SCIENTIFIC_LABEL": SCIENTIFIC_LABEL,
        "FRESH_VARIANT_COUNTS": dict(Counter(row["variant"] for row in fresh)),
        "VARIANT_COUNTS": dict(Counter(row["variant"] for row in pairs)),
        "FRESH_BUCKET_COUNTS": dict(Counter(row["training_bucket"] for row in fresh)),
        "COMBINED_BUCKET_COUNTS": dict(counts["physical_categories"]),
        "CATEGORY_COEFFICIENTS": coefficients,
        "FRESH_PAIR_COUNT": len(fresh),
        "RETENTION_PAIR_COUNT": len(retained),
        "PAIR_COUNT": counts["states"],
        "SAMPLES_PER_STEP": counts["samples"],
        "CHOSEN_SAMPLE_COUNT": counts["chosen_samples"],
        "REJECTED_SAMPLE_COUNT": counts["rejected_samples"],
        "CHOSEN_ONLY_STATE_COUNT": counts["chosen_only_states"],
        "REJECTED_BUCKET_COUNTS": dict(rejected),
        "OBJECTIVE_COEFFICIENT_TEXT": objective_text,
        "OBJECTIVE_CATEGORY_MASSES": masses,
        "SOURCE_STEP": adapter.SOURCE_STEP,
        "UPDATE_STEPS": adapter.UPDATE_STEPS,
        "FINAL_STEP": adapter.FINAL_STEP,
        "OPTIMIZER_UPDATES": adapter.OPTIMIZER_UPDATES,
        "LEARNING_RATE": adapter.LEARNING_RATE,
        "PARENT_RECEIPT_SHA256": adapter.PINNED_HANDOFF["receipt_file_sha256"],
        "PARENT_RECEIPT_BODY_SHA256": adapter.PINNED_HANDOFF["receipt_body_sha256"],
        "PARENT_ADAPTER_TREE_SHA256": adapter.PINNED_HANDOFF["candidate_tree_sha256"],
        "PRIME_CHILD_MODULE": PRIME_CHILD_MODULE,
        "PRIME_CHILD_EXTRA_ARGS": ("--adapter", contract["adapter_path"]),
        "CANDIDATE_NAME": CANDIDATE_NAME,
        "CONTROL_LORA_NAME": CONTROL_LORA_NAME,
        "PAIR_SOURCE_KIND": (
            "step33_train_only_stable_navigation_action_corrections_plus_"
            "sealed_step32_full_downstream_chain"
        ),
        "PAIR_COUNT_BREAKDOWN": {
            "fresh_upstream_states": len(fresh),
            "fresh_upstream_paired_states": counts["fresh_upstream_paired_states"],
            "fresh_upstream_chosen_only_states": counts[
                "fresh_upstream_chosen_only_states"
            ],
            "sealed_downstream_retention_states": counts["sealed_downstream_retention_states"],
            "sealed_downstream_source_counts": counts[
                "sealed_downstream_source_counts"
            ],
            "logical_groups": dict(counts["logical_groups"]),
        },
        "ON_POLICY": False,
        "PAIR_POLICY_AUDIT": {
            "classification": (
                "step33_candidate_train_corrections_anchored_on_exact_successful_step32_"
                "plus_sealed_step32_train_retention"
            ),
            "fresh_train_states": len(fresh),
            "sealed_train_retention_states": len(retained),
            "fresh_reasoning_tail_ce": False,
            "fresh_origin_or_port_ce": False,
            "generic_click_or_index_rejected_unlikelihood": False,
            "evaluation_states": 0,
            "heldout_states": 0,
        },
        "SOURCE_GROUP_MASS": dict(value["source_group_mass"]),
        "ALLOW_LOOSE_CHOSEN_JSON": True,
    }
    for name, replacement in replacements.items():
        setattr(core, name, replacement)
    core.hero.validate_trainer_adapter = adapter.validate_trainer_adapter
    core.validate_step29_parent_fast = lambda path: validate_step32_parent_fast(
        path, handoff=adapter.PINNED_HANDOFF
    )
    core._render_pair_rows = _render_pair_rows
    core._validate_render_audit = _validate_render_audit

    def publish_with_step34_mask(
        path: Path, body: Mapping[str, Any], *, hash_field: str
    ) -> dict[str, Any]:
        updated = dict(body)
        if updated.get("schema") == PLAN_SCHEMA:
            updated["semantic_mask"] = {
                "fresh_chosen": "navigate_method_plus_relative_path_query_only",
                "fresh_excluded": [
                    "reasoning_tail",
                    "origin",
                    "host",
                    "port",
                    "json_syntax",
                ],
                "fresh_rejected": "stable_wrong_relative_target_only",
                "generic_click_or_index_rejected_unlikelihood": False,
                "sealed_downstream": "exact_step32_semantic_objective",
            }
            updated["scientific_iteration"] = 34
            updated["physical_optimizer_output_step"] = adapter.FINAL_STEP
        return saved_publish(path, updated, hash_field=hash_field)

    core._publish = publish_with_step34_mask
    try:
        yield
    finally:
        core.hero.validate_trainer_adapter = saved_validator
        core.validate_step29_parent_fast = saved_parent
        core._render_pair_rows = saved_render
        core._validate_render_audit = saved_render_validator
        core._publish = saved_publish
        for name, original in saved.items():
            setattr(core, name, original)


def _validated(path: str | Path) -> dict[str, Any]:
    return adapter.validate_trainer_adapter(path)


def prepare_training(**kwargs: Any) -> dict[str, Any]:
    contract = _validated(kwargs["adapter_path"])
    with _profile(contract):
        return core.prepare_paired_buy_now_training(**kwargs)


def validate_parent(*, receipt_path: str | Path, adapter_path: str | Path) -> dict[str, Any]:
    contract = _validated(adapter_path)
    with _profile(contract):
        return validate_step32_parent_fast(receipt_path, handoff=adapter.PINNED_HANDOFF)


def _adapter_path_from_plan(path: str | Path) -> Path:
    return Path(json.loads(Path(path).read_text())["frozen_adapter"]["path"])


def validate_training_plan(
    path: str | Path, *, require_launch_patches: bool = False
) -> dict[str, Any]:
    contract = _validated(_adapter_path_from_plan(path))
    with _profile(contract):
        result = core.validate_training_plan(
            path, require_launch_patches=require_launch_patches
        )
    if (
        result.get("semantic_mask") != contract["adapter"]["semantic_mask"]
        or result.get("scientific_iteration") != 34
        or result.get("physical_optimizer_output_step") != adapter.FINAL_STEP
    ):
        raise Step34TrainingError("Step34 action-mask plan contract drifted")
    return result


def write_training_receipt(*, plan_path: str | Path, executor_git_sha: str) -> dict[str, Any]:
    contract = _validated(_adapter_path_from_plan(plan_path))
    with _profile(contract):
        return core.write_training_receipt(plan_path=plan_path, executor_git_sha=executor_git_sha)


def validate_training_receipt(path: str | Path) -> dict[str, Any]:
    receipt = json.loads(Path(path).read_text())
    contract = _validated(_adapter_path_from_plan(receipt["plan_path"]))
    with _profile(contract):
        result = core.validate_training_receipt(path)
    plan = json.loads(Path(receipt["plan_path"]).read_text(encoding="utf-8"))
    if plan.get("semantic_mask") != contract["adapter"]["semantic_mask"]:
        raise Step34TrainingError("Step34 receipt-bound action mask drifted")
    return result


def trainer_command(prime_root: str | Path, training_output: str | Path) -> list[str]:
    return core.trainer_command(prime_root, training_output)


def _prime_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--adapter", type=Path, required=True)
    args, remaining = parser.parse_known_args(argv)
    contract = _validated(args.adapter)
    with _profile(contract):
        return core._prime_main(remaining)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("validate-adapter")
    check.add_argument("--adapter", type=Path, required=True)
    parent = commands.add_parser("validate-parent")
    parent.add_argument("--receipt", type=Path, required=True)
    parent.add_argument("--adapter", type=Path, required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--adapter", type=Path, required=True)
    prepare.add_argument("--parent-receipt", type=Path, required=True)
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--prime-root", type=Path, default=Path("/opt/prime-rl"))
    prepare.add_argument("--artifact-git-sha", required=True)
    prepare.add_argument("--topology", choices=("cp2_dp2", "cp4_dp1"), default="cp2_dp2")
    prepare.add_argument("--fallback-reason")
    plan = commands.add_parser("validate-plan")
    plan.add_argument("--plan", type=Path, required=True)
    plan.add_argument("--require-launch-patches", action="store_true")
    write = commands.add_parser("write-receipt")
    write.add_argument("--plan", type=Path, required=True)
    write.add_argument("--executor-git-sha", required=True)
    receipt = commands.add_parser("validate-receipt")
    receipt.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "validate-adapter":
        result = adapter.validate_trainer_adapter(args.adapter)
    elif args.command == "validate-parent":
        result = validate_parent(receipt_path=args.receipt, adapter_path=args.adapter)
    elif args.command == "prepare":
        result = prepare_training(
            adapter_path=args.adapter,
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
    print(json.dumps({key: item for key, item in result.items() if key != "pairs"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    entrypoint = _prime_main if any(arg.startswith("_prime-") for arg in sys.argv[1:]) else main
    raise SystemExit(entrypoint())
