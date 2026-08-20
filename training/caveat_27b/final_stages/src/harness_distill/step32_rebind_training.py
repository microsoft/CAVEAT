"""One-update step-32 trainer over dynamic rebind/bridge/cleanup evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import action_weighted_ce_training as base
from . import hero50_paired_training as core
from . import step32_rebind_adapter as adapter
from .sol_dagger_training import sha256_file

PLAN_SCHEMA = "harness-distill.step32-rebind-paired-plan.v1"
RECEIPT_SCHEMA = "harness-distill.step32-rebind-paired-receipt.v1"
RENDER_AUDIT_SCHEMA = "harness-distill.step32-rebind-render-audit.v1"
SCIENTIFIC_LABEL = "step31_rebind_bridge_cleanup_paired_semantic_update"
CANDIDATE_NAME = "step32-rebind-bridge-cleanup-paired"
CONTROL_LORA_NAME = "c2-rebind-bridge-cleanup-step32"
PRIME_CHILD_MODULE = "harness_distill.step32_rebind_training"
PARENT_RECEIPT_SCHEMA = "harness-distill.step31-strict-checkout-paired-receipt.v1"


class Step32TrainingError(core.PairedBuyNowError):
    """The sealed step-31 lineage or step-32 training contract drifted."""


def _canonical(value: Any) -> bytes:
    return base.canonical_json(value).encode()


def _is_positive_dcp_identity(value: Mapping[str, Any]) -> bool:
    digest = value.get("tree_sha256")
    return bool(
        isinstance(value.get("path"), str)
        and isinstance(digest, str)
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
        and type(value.get("files")) is int
        and value["files"] > 0
        and type(value.get("bytes")) is int
        and value["bytes"] > 0
    )


def validate_step31_parent_fast(path: str | Path, *, handoff: Mapping[str, Any]) -> dict[str, Any]:
    """Bind the exact successful step-31 receipt, adapter, DCP, and base model."""

    if dict(handoff) != adapter.PINNED_HANDOFF:
        raise Step32TrainingError("step-31 parent handoff identity drifted")
    receipt_path = Path(path).resolve()
    if (
        not receipt_path.is_file()
        or receipt_path.is_symlink()
        or sha256_file(receipt_path) != handoff["receipt_file_sha256"]
    ):
        raise Step32TrainingError("step-31 parent receipt bytes are absent or drifted")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    body = {key: value for key, value in receipt.items() if key != "receipt_body_sha256"}
    candidate = receipt.get("candidate")
    dcp = receipt.get("final_dcp")
    if (
        receipt.get("schema") != PARENT_RECEIPT_SCHEMA
        or receipt.get("status") != "ok"
        or receipt.get("receipt_body_sha256") != handoff["receipt_body_sha256"]
        or hashlib.sha256(_canonical(body)).hexdigest() != handoff["receipt_body_sha256"]
        or receipt.get("source_step") != 30
        or receipt.get("update_steps") != [31]
        or receipt.get("final_step") != adapter.SOURCE_STEP
        or receipt.get("optimizer_updates") != 1
        or receipt.get("learning_rate") != 3.0e-6
        or not isinstance(candidate, Mapping)
        or candidate.get("name") != "step31-strict-checkout-paired"
        or candidate.get("update") != adapter.SOURCE_STEP
        or candidate.get("tree_sha256") != handoff["candidate_tree_sha256"]
        or not isinstance(dcp, Mapping)
        or not _is_positive_dcp_identity(dcp)
    ):
        raise Step32TrainingError("step-31 terminal receipt lineage drifted")
    plan_path = Path(str(receipt.get("plan_path", ""))).resolve()
    if (
        not plan_path.is_file()
        or plan_path.is_symlink()
        or sha256_file(plan_path) != receipt.get("plan_sha256")
    ):
        raise Step32TrainingError("step-31 parent plan bytes drifted")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_body = {key: value for key, value in plan.items() if key != "plan_body_sha256"}
    if (
        plan.get("plan_body_sha256") != receipt.get("plan_body_sha256")
        or hashlib.sha256(_canonical(plan_body)).hexdigest() != receipt.get("plan_body_sha256")
        or not isinstance(plan.get("parent_model"), str)
    ):
        raise Step32TrainingError("step-31 parent plan self-binding drifted")
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
        raise Step32TrainingError("step-31 terminal adapter/DCP is absent or unsafe")
    core.PARENT_DCP_TREE_SHA256 = str(dcp["tree_sha256"])
    core.PARENT_DCP_FILES = int(dcp["files"])
    core.PARENT_DCP_BYTES = int(dcp["bytes"])
    return {"receipt": receipt, "plan": plan, "receipt_path": str(receipt_path)}


_PROFILE_FIELDS = (
    "PLAN_SCHEMA",
    "RECEIPT_SCHEMA",
    "RENDER_AUDIT_SCHEMA",
    "SCIENTIFIC_LABEL",
    "FRESH_VARIANT_COUNTS",
    "VARIANT_COUNTS",
    "FRESH_BUCKET_COUNTS",
    "COMBINED_BUCKET_COUNTS",
    "CATEGORY_COEFFICIENTS",
    "FRESH_PAIR_COUNT",
    "RETENTION_PAIR_COUNT",
    "PAIR_COUNT",
    "SAMPLES_PER_STEP",
    "SOURCE_STEP",
    "UPDATE_STEPS",
    "FINAL_STEP",
    "OPTIMIZER_UPDATES",
    "LEARNING_RATE",
    "PARENT_RECEIPT_SHA256",
    "PARENT_RECEIPT_BODY_SHA256",
    "PARENT_ADAPTER_TREE_SHA256",
    "PRIME_CHILD_MODULE",
    "CANDIDATE_NAME",
    "CONTROL_LORA_NAME",
    "PAIR_SOURCE_KIND",
    "PAIR_COUNT_BREAKDOWN",
    "ON_POLICY",
    "PAIR_POLICY_AUDIT",
    "CHOSEN_SAMPLE_COUNT",
    "REJECTED_SAMPLE_COUNT",
    "CHOSEN_ONLY_STATE_COUNT",
    "REJECTED_BUCKET_COUNTS",
    "OBJECTIVE_COEFFICIENT_TEXT",
    "OBJECTIVE_CATEGORY_MASSES",
    "PRIME_CHILD_EXTRA_ARGS",
    "SOURCE_GROUP_MASS",
    "ALLOW_LOOSE_CHOSEN_JSON",
)


def _fraction(value: str) -> Fraction:
    numerator, denominator = value.split("/", 1)
    return Fraction(int(numerator), int(denominator))


@contextmanager
def _profile(handoff: Mapping[str, Any], contract: Mapping[str, Any]) -> Iterator[None]:
    saved = {name: getattr(core, name) for name in _PROFILE_FIELDS}
    saved_validator = core.hero.validate_trainer_adapter
    saved_parent = core.validate_step29_parent_fast
    value = contract["adapter"]
    counts = value["state_counts"]
    pairs = contract["pairs"]
    fresh = [row for row in pairs if row.get("retention_kind") is None]
    retained = [row for row in pairs if row.get("retention_kind") is not None]
    categories = dict(counts["physical_categories"])
    rejected_categories = Counter(
        row["training_bucket"] for row in pairs if row["objective_kind"] == "paired"
    )
    category_coefficients = {
        key: _fraction(item) for key, item in value["objective"]["category_mix"].items()
    }
    objective_masses = {
        name: {key: _fraction(item) for key, item in category.items()}
        for name, category in value["objective"]["objective_category_masses"].items()
    }
    objective_names = {
        "chosen_tail": "chosen_pre_action_tail_ce",
        "chosen_action": "chosen_action_ce",
        "rejected_unlikelihood": "rejected_semantic_unlikelihood",
    }
    objective_text = {
        objective_names[name]: (
            f"{sum(masses.values(), Fraction()).numerator}/"
            f"{sum(masses.values(), Fraction()).denominator}"
        )
        for name, masses in objective_masses.items()
    }
    variants = Counter(row["variant"] for row in pairs)
    fresh_variants = Counter(row["variant"] for row in fresh)
    replacements = {
        "PLAN_SCHEMA": PLAN_SCHEMA,
        "RECEIPT_SCHEMA": RECEIPT_SCHEMA,
        "RENDER_AUDIT_SCHEMA": RENDER_AUDIT_SCHEMA,
        "SCIENTIFIC_LABEL": SCIENTIFIC_LABEL,
        "FRESH_VARIANT_COUNTS": dict(fresh_variants),
        "VARIANT_COUNTS": dict(variants),
        "FRESH_BUCKET_COUNTS": dict(Counter(row["training_bucket"] for row in fresh)),
        "COMBINED_BUCKET_COUNTS": categories,
        "CATEGORY_COEFFICIENTS": category_coefficients,
        "FRESH_PAIR_COUNT": len(fresh),
        "RETENTION_PAIR_COUNT": len(retained),
        "PAIR_COUNT": counts["states"],
        "SAMPLES_PER_STEP": counts["samples"],
        "CHOSEN_SAMPLE_COUNT": counts["chosen_samples"],
        "REJECTED_SAMPLE_COUNT": counts["rejected_samples"],
        "CHOSEN_ONLY_STATE_COUNT": counts["chosen_only_states"],
        "REJECTED_BUCKET_COUNTS": dict(rejected_categories),
        "OBJECTIVE_COEFFICIENT_TEXT": objective_text,
        "OBJECTIVE_CATEGORY_MASSES": objective_masses,
        "SOURCE_STEP": adapter.SOURCE_STEP,
        "UPDATE_STEPS": adapter.UPDATE_STEPS,
        "FINAL_STEP": adapter.FINAL_STEP,
        "OPTIMIZER_UPDATES": adapter.OPTIMIZER_UPDATES,
        "LEARNING_RATE": adapter.LEARNING_RATE,
        "PARENT_RECEIPT_SHA256": handoff["receipt_file_sha256"],
        "PARENT_RECEIPT_BODY_SHA256": handoff["receipt_body_sha256"],
        "PARENT_ADAPTER_TREE_SHA256": handoff["candidate_tree_sha256"],
        "PRIME_CHILD_MODULE": PRIME_CHILD_MODULE,
        "PRIME_CHILD_EXTRA_ARGS": ("--adapter", contract["adapter_path"]),
        "CANDIDATE_NAME": CANDIDATE_NAME,
        "CONTROL_LORA_NAME": CONTROL_LORA_NAME,
        "PAIR_SOURCE_KIND": (
            "sealed_step31_target_retention_plus_dynamic_train_only_rebind_bridge_multiline_cleanup"
        ),
        "PAIR_COUNT_BREAKDOWN": {
            "sealed_target_retention_states": len(retained),
            "fresh_dynamic_states": len(fresh),
            "fresh_paired_states": Counter(row["objective_kind"] for row in fresh)["paired"],
            "fresh_chosen_only_states": Counter(row["objective_kind"] for row in fresh)[
                "chosen_only"
            ],
            "logical_groups": dict(counts["logical_groups"]),
        },
        "ON_POLICY": False,
        "PAIR_POLICY_AUDIT": {
            "classification": "fresh_step31_on_policy_train_plus_sealed_step31_retention",
            "fresh_train_states": len(fresh),
            "sealed_target_retention_states": len(retained),
            "evaluation_states": 0,
            "heldout_states": 0,
        },
        "SOURCE_GROUP_MASS": dict(value["source_group_mass"]),
        "ALLOW_LOOSE_CHOSEN_JSON": True,
    }
    for name, replacement in replacements.items():
        setattr(core, name, replacement)
    core.hero.validate_trainer_adapter = adapter.validate_trainer_adapter
    core.validate_step29_parent_fast = lambda path: validate_step31_parent_fast(
        path, handoff=handoff
    )
    try:
        yield
    finally:
        core.hero.validate_trainer_adapter = saved_validator
        core.validate_step29_parent_fast = saved_parent
        for name, original in saved.items():
            setattr(core, name, original)


def _validated_adapter(path: str | Path) -> dict[str, Any]:
    result = adapter.validate_trainer_adapter(path)
    handoff = result["adapter"].get("parent_handoff", {}).get("value")
    if not isinstance(handoff, Mapping) or dict(handoff) != adapter.PINNED_HANDOFF:
        raise Step32TrainingError("step32 adapter lacks the sealed step-31 handoff")
    return result


def prepare_training(**kwargs: Any) -> dict[str, Any]:
    contract = _validated_adapter(kwargs["adapter_path"])
    handoff = contract["adapter"]["parent_handoff"]["value"]
    with _profile(handoff, contract):
        return core.prepare_paired_buy_now_training(**kwargs)


def validate_parent(*, receipt_path: str | Path, adapter_path: str | Path) -> dict[str, Any]:
    contract = _validated_adapter(adapter_path)
    handoff = contract["adapter"]["parent_handoff"]["value"]
    with _profile(handoff, contract):
        return validate_step31_parent_fast(receipt_path, handoff=handoff)


def _adapter_path_from_plan(path: str | Path) -> Path:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    return Path(value["frozen_adapter"]["path"])


def validate_training_plan(
    path: str | Path, *, require_launch_patches: bool = False
) -> dict[str, Any]:
    contract = _validated_adapter(_adapter_path_from_plan(path))
    handoff = contract["adapter"]["parent_handoff"]["value"]
    with _profile(handoff, contract):
        return core.validate_training_plan(path, require_launch_patches=require_launch_patches)


def write_training_receipt(*, plan_path: str | Path, executor_git_sha: str) -> dict[str, Any]:
    contract = _validated_adapter(_adapter_path_from_plan(plan_path))
    handoff = contract["adapter"]["parent_handoff"]["value"]
    with _profile(handoff, contract):
        return core.write_training_receipt(plan_path=plan_path, executor_git_sha=executor_git_sha)


def validate_training_receipt(path: str | Path) -> dict[str, Any]:
    receipt = json.loads(Path(path).read_text(encoding="utf-8"))
    contract = _validated_adapter(_adapter_path_from_plan(receipt["plan_path"]))
    handoff = contract["adapter"]["parent_handoff"]["value"]
    with _profile(handoff, contract):
        return core.validate_training_receipt(path)


def trainer_command(prime_root: str | Path, training_output: str | Path) -> list[str]:
    return core.trainer_command(prime_root, training_output)


def _prime_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--adapter", type=Path, required=True)
    args, remaining = parser.parse_known_args(argv)
    contract = _validated_adapter(args.adapter)
    with _profile(adapter.PINNED_HANDOFF, contract):
        return core._prime_main(remaining)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    validate_adapter = commands.add_parser("validate-adapter")
    validate_adapter.add_argument("--adapter", type=Path, required=True)
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
    result = {key: value for key, value in result.items() if key != "pairs"}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    entrypoint = _prime_main if any(arg.startswith("_prime-") for arg in sys.argv[1:]) else main
    raise SystemExit(entrypoint())


__all__ = [
    "CANDIDATE_NAME",
    "PLAN_SCHEMA",
    "RECEIPT_SCHEMA",
    "SCIENTIFIC_LABEL",
    "prepare_training",
    "trainer_command",
    "validate_parent",
    "validate_step31_parent_fast",
    "validate_training_plan",
    "validate_training_receipt",
    "write_training_receipt",
]
