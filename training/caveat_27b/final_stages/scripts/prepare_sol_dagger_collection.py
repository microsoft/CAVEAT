#!/usr/bin/env python3
"""Create-only Sol-low DAgger collection orchestration.

Example schedule JSONL row (selection is explicit and outcome-blind)::

  {"schema":"harness-distill.sol-dagger-capture-schedule.v1",
   "session_sha256":"...","source_sequence":7,"rollout_id":"g-r00-01",
   "split":"train","variant":"graded","phase":"pagination_exploration",
   "mode":"shadow","captured_at_unix":1800000000,
   "evidence_message_indexes":[1]}

``make-sidecars`` resolves that row against exact proxy request bytes.  It does
not infer a phase from model output, rewards, hidden state, or evaluator data.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from harness_distill.materialize import canonical_json, read_jsonl
from harness_distill.sol_dagger_collection import (
    AgentArenaSolLowTeacher,
    CampaignPolicy,
    VerifiedSFTBatch,
    capture_qwen_states,
    label_qwen_states,
    make_bundle_capture_schedule,
    make_capture_sidecars,
    make_cleanup_correction_schedule,
    make_cleanup_semantic_validations,
    make_static_visible_action_validations,
    make_structural_reserve_schedule,
    materialize_structural_topup,
    materialize_verified_sft,
    publish_final_bundle,
    publish_raw_bundle,
    publish_reserve_nontraining_bundle,
    publish_training_collection,
)


def _json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"required regular JSON is absent: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON must be an object: {path}")
    return value


def _policy(path: Path) -> CampaignPolicy:
    value = _json(path)
    if value.get("schema") != "harness-distill.sol-dagger-campaign-policy.v1":
        raise RuntimeError("campaign policy schema is unsupported")
    body = {key: item for key, item in value.items() if key not in {"schema", "policy_sha256"}}
    declared = value.get("policy_sha256")
    if (
        declared is not None
        and declared != hashlib.sha256(canonical_json(body).encode()).hexdigest()
    ):
        raise RuntimeError("campaign policy self-hash drifted")
    for name in ("required_variants", "required_phases"):
        if name in body:
            body[name] = tuple(body[name])
    return CampaignPolicy(**body)


def _write_jsonl_new(
    path: Path, rows: list[dict[str, Any]], *, mode: int = 0o644
) -> dict[str, Any]:
    payload = b"".join((canonical_json(row) + "\n").encode() for row in rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        descriptor = os.open(path, flags, mode)
    except FileExistsError as exc:
        raise RuntimeError(f"refusing to overwrite create-only artifact: {path}") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return {
        "path": str(path.resolve()),
        "rows": len(rows),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _write_json_new(
    path: Path, value: dict[str, Any], *, mode: int = 0o644
) -> dict[str, Any]:
    payload = (canonical_json(value) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        descriptor = os.open(path, flags, mode)
    except FileExistsError as exc:
        raise RuntimeError(f"refusing to overwrite create-only artifact: {path}") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    bundle_schedule = commands.add_parser(
        "schedule-bundle",
        help="merge 24 frozen traces and generate the outcome-blind 64/24 schedule",
    )
    bundle_schedule.add_argument("--rollout-bundle", type=Path, required=True)
    bundle_schedule.add_argument("--proxy-output", type=Path, required=True)
    bundle_schedule.add_argument("--schedule-output", type=Path, required=True)
    bundle_schedule.add_argument("--audit-output", type=Path, required=True)

    policy = commands.add_parser(
        "make-policy",
        help="seal the exact campaign policy from the generated capture schedule",
    )
    policy.add_argument("--schedule", type=Path, required=True)
    policy.add_argument("--campaign-id", required=True)
    policy.add_argument("--collection-nonce", required=True)
    policy.add_argument("--harness-fingerprint-sha256", required=True)
    policy.add_argument("--output", type=Path, required=True)

    sidecars = commands.add_parser(
        "make-sidecars", help="bind an explicit phase schedule to exact Qwen proxy calls"
    )
    sidecars.add_argument("--proxy", type=Path, required=True)
    sidecars.add_argument("--schedule", type=Path, required=True)
    sidecars.add_argument("--output", type=Path, required=True)
    sidecars.add_argument("--campaign-id", required=True)
    sidecars.add_argument("--collection-nonce", required=True)
    sidecars.add_argument("--harness-fingerprint-sha256", required=True)

    cleanup = commands.add_parser(
        "freeze-cleanup-schedule",
        help="freeze exact train-session PDP Buy Now and visible add-on-cart states",
    )
    cleanup.add_argument("--proxy", type=Path, required=True)
    cleanup.add_argument("--original-schedule", type=Path, required=True)
    cleanup.add_argument("--schedule-output", type=Path, required=True)
    cleanup.add_argument("--audit-output", type=Path, required=True)

    label = commands.add_parser(
        "label", help="call gpt-5.6-sol#low over AgentArena TRAPI Responses"
    )
    label.add_argument("--proxy", type=Path, required=True)
    label.add_argument("--capture-sidecars", type=Path, required=True)
    label.add_argument("--output", type=Path, required=True)
    label.add_argument("--concurrency", type=int, default=16)
    label.add_argument("--max-completion-tokens", type=int, default=8192)
    label.add_argument("--timeout-seconds", type=float, default=600.0)

    validate = commands.add_parser(
        "validate-static",
        help=(
            "validate exact BrowserUse schema and visible indices without claiming "
            "browser execution"
        ),
    )
    validate.add_argument("--raw-labels", type=Path, required=True)
    validate.add_argument("--validated-at-unix", type=float, required=True)
    validate.add_argument("--output", type=Path, required=True)

    validate_cleanup = commands.add_parser(
        "validate-cleanup",
        help="apply frozen static and semantic checks to cleanup teacher labels",
    )
    validate_cleanup.add_argument("--raw-labels", type=Path, required=True)
    validate_cleanup.add_argument("--schedule", type=Path, required=True)
    validate_cleanup.add_argument("--validated-at-unix", type=float, required=True)
    validate_cleanup.add_argument("--output", type=Path, required=True)

    reserve = commands.add_parser(
        "freeze-structural-reserve",
        help="freeze 15 unused Qwen train states for seven exact static deficits",
    )
    reserve.add_argument("--proxy", type=Path, required=True)
    reserve.add_argument("--original-schedule", type=Path, required=True)
    reserve.add_argument("--original-raw-labels", type=Path, required=True)
    reserve.add_argument("--original-validations", type=Path, required=True)
    reserve.add_argument("--rollout-bundle", type=Path, required=True)
    reserve.add_argument("--schedule-output", type=Path, required=True)
    reserve.add_argument("--audit-output", type=Path, required=True)

    finalize = commands.add_parser(
        "finalize", help="apply action-validation sidecars and publish train/holdout data"
    )
    finalize.add_argument("--raw-labels", type=Path, required=True)
    finalize.add_argument("--action-validations", type=Path, required=True)
    finalize.add_argument("--policy", type=Path, required=True)
    finalize.add_argument("--now-unix", type=float, required=True)
    finalize.add_argument("--output", type=Path, required=True)

    finalize_topup = commands.add_parser(
        "finalize-structural-topup",
        help="select first accepted frozen reserves and publish exact 64/24 data",
    )
    finalize_topup.add_argument("--original-raw-labels", type=Path, required=True)
    finalize_topup.add_argument("--original-validations", type=Path, required=True)
    finalize_topup.add_argument("--reserve-raw-labels", type=Path, required=True)
    finalize_topup.add_argument("--reserve-validations", type=Path, required=True)
    finalize_topup.add_argument("--reserve-schedule", type=Path, required=True)
    finalize_topup.add_argument("--reserve-audit", type=Path, required=True)
    finalize_topup.add_argument("--policy", type=Path, required=True)
    finalize_topup.add_argument("--now-unix", type=float, required=True)
    finalize_topup.add_argument("--output", type=Path, required=True)
    finalize_topup.add_argument("--nontraining-output", type=Path, required=True)
    finalize_topup.add_argument("--required-reserve-attempts", type=int, default=15)

    publish = commands.add_parser(
        "publish-collection",
        help="bind the exact 64+32 train rows, 24 holdout rows, parent, and evaluator",
    )
    publish.add_argument("--final-bundle", type=Path, required=True)
    publish.add_argument("--policy", type=Path, required=True)
    publish.add_argument("--parent-receipt", type=Path, required=True)
    publish.add_argument("--evaluation-contract", type=Path, required=True)
    publish.add_argument("--evaluation-contract-sha256", required=True)
    publish.add_argument("--source-git-sha", required=True)
    publish.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


async def _label(args: argparse.Namespace) -> dict[str, Any]:
    states = capture_qwen_states(
        read_jsonl(args.proxy),
        read_jsonl(args.capture_sidecars),
    )
    teacher = AgentArenaSolLowTeacher(
        timeout_seconds=args.timeout_seconds,
        max_completion_tokens=args.max_completion_tokens,
    )
    try:
        batch = await label_qwen_states(
            states,
            teacher,
            concurrency=args.concurrency,
            max_completion_tokens=args.max_completion_tokens,
        )
    finally:
        await teacher.close()
    return publish_raw_bundle(args.output, batch)


def _load_final_bundle(path: Path) -> VerifiedSFTBatch:
    manifest = _json(path / "manifest.json")
    if manifest.get("schema") != "harness-distill.sol-dagger-final-bundle.v1":
        raise RuntimeError("final bundle manifest schema is unsupported")
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise RuntimeError("final bundle has no file inventory")
    for name, item in files.items():
        artifact = path / name
        if not artifact.is_file() or artifact.is_symlink():
            raise RuntimeError(f"final bundle artifact is absent: {name}")
        if hashlib.sha256(artifact.read_bytes()).hexdigest() != item.get("sha256"):
            raise RuntimeError(f"final bundle artifact hash drifted: {name}")
    return VerifiedSFTBatch(
        train_labels=tuple(read_jsonl(path / "train_sft.jsonl")),
        holdout_labels=tuple(read_jsonl(path / "holdout_actions.jsonl")),
        audits=tuple(read_jsonl(path / "label_audits.jsonl")),
        report=_json(path / "corpus_audit.json"),
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "schedule-bundle":
        targets = (args.proxy_output, args.schedule_output, args.audit_output)
        if any(path.exists() or path.is_symlink() for path in targets):
            raise RuntimeError("refusing to reuse a bundle-schedule output artifact")
        proxy, schedule, audit = make_bundle_capture_schedule(args.rollout_bundle)
        result = {
            "proxy": _write_jsonl_new(args.proxy_output, proxy),
            "schedule": _write_jsonl_new(args.schedule_output, schedule),
            "audit": _write_json_new(args.audit_output, audit),
        }
    elif args.command == "make-policy":
        schedule = read_jsonl(args.schedule)
        captured = [row.get("captured_at_unix") for row in schedule]
        if not captured or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) for value in captured
        ):
            raise RuntimeError("capture schedule has no valid timestamps")
        body = {
            "campaign_id": args.campaign_id,
            "collection_nonce": args.collection_nonce,
            "harness_fingerprint_sha256": args.harness_fingerprint_sha256,
            "not_before_unix": min(captured) - 1.0,
            "maximum_age_seconds": 28_800.0,
            "target_train_labels": 64,
            "target_holdout_labels": 24,
            "minimum_train_labels": 64,
            "minimum_holdout_labels": 24,
            "maximum_total_labels": 96,
            "minimum_train_per_variant": 16,
            "minimum_holdout_per_variant": 6,
            "minimum_train_per_phase": 10,
            "minimum_holdout_per_phase": 4,
            "maximum_exclusion_fraction": 0.25,
            "minimum_rollouts_per_variant": 6,
            "minimum_shadow_rollouts": 24,
            "minimum_intervention_rollouts": 0,
            "required_variants": ["graded", "graded3", "graded4", "mixed"],
            "required_phases": [
                "constraints_query",
                "pagination_exploration",
                "pdp_evidence_selection",
                "checkpoint_grounding",
                "cart_cleanup_recheck",
                "checkout_order",
            ],
        }
        CampaignPolicy(
            **{
                key: tuple(value) if key in {"required_variants", "required_phases"} else value
                for key, value in body.items()
            }
        )
        value = {
            "schema": "harness-distill.sol-dagger-campaign-policy.v1",
            **body,
            "policy_sha256": hashlib.sha256(canonical_json(body).encode()).hexdigest(),
        }
        result = _write_json_new(args.output, value)
    elif args.command == "make-sidecars":
        rows = make_capture_sidecars(
            read_jsonl(args.proxy),
            read_jsonl(args.schedule),
            campaign_id=args.campaign_id,
            collection_nonce=args.collection_nonce,
            harness_fingerprint_sha256=args.harness_fingerprint_sha256,
        )
        result = _write_jsonl_new(args.output, rows)
    elif args.command == "freeze-cleanup-schedule":
        targets = (args.schedule_output, args.audit_output)
        if any(path.exists() or path.is_symlink() for path in targets):
            raise RuntimeError("refusing to reuse a cleanup-schedule output artifact")
        schedule, audit = make_cleanup_correction_schedule(
            read_jsonl(args.proxy), read_jsonl(args.original_schedule)
        )
        result = {
            "schedule": _write_jsonl_new(args.schedule_output, schedule, mode=0o444),
            "audit": _write_json_new(args.audit_output, audit, mode=0o444),
        }
    elif args.command == "label":
        result = asyncio.run(_label(args))
    elif args.command == "validate-static":
        rows = make_static_visible_action_validations(
            read_jsonl(args.raw_labels),
            validated_at_unix=args.validated_at_unix,
        )
        result = _write_jsonl_new(args.output, rows)
    elif args.command == "validate-cleanup":
        rows = make_cleanup_semantic_validations(
            read_jsonl(args.raw_labels),
            read_jsonl(args.schedule),
            validated_at_unix=args.validated_at_unix,
        )
        result = _write_jsonl_new(args.output, rows, mode=0o444)
    elif args.command == "freeze-structural-reserve":
        targets = (args.schedule_output, args.audit_output)
        if any(path.exists() or path.is_symlink() for path in targets):
            raise RuntimeError("refusing to reuse a structural-reserve artifact")
        bundle = _json(args.rollout_bundle)
        body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
        bundle_sha = bundle.get("bundle_sha256")
        if (
            bundle.get("schema") != "harness-distill.sol-dagger-rollout-bundle.v1"
            or bundle_sha != hashlib.sha256(canonical_json(body).encode()).hexdigest()
        ):
            raise RuntimeError("rollout bundle identity drifted before reserve freeze")
        schedule, audit = make_structural_reserve_schedule(
            read_jsonl(args.proxy),
            read_jsonl(args.original_schedule),
            read_jsonl(args.original_raw_labels),
            read_jsonl(args.original_validations),
            rollout_bundle_sha256=bundle_sha,
            required_deficits=7,
            fixed_redundancy=1,
        )
        if (
            len(schedule) != 15
            or audit.get("deficit_count") != 7
            or audit.get("candidate_count") != 15
            or audit.get("original_status_counts")
            != {"holdout:accepted": 24, "train:accepted": 57, "train:rejected": 7}
        ):
            raise RuntimeError("structural reserve does not match the frozen 57+24 deficit")
        result = {
            "schedule": _write_jsonl_new(args.schedule_output, schedule, mode=0o444),
            "audit": _write_json_new(args.audit_output, audit, mode=0o444),
        }
    elif args.command == "finalize":
        raw = read_jsonl(args.raw_labels)
        verified = materialize_verified_sft(
            raw,
            read_jsonl(args.action_validations),
            _policy(args.policy),
            now_unix=args.now_unix,
        )
        result = publish_final_bundle(args.output, verified)
    elif args.command == "finalize-structural-topup":
        if any(
            path.exists() or path.is_symlink()
            for path in (args.output, args.nontraining_output)
        ):
            raise RuntimeError("refusing to reuse a structural-topup output directory")
        reserve_audit = _json(args.reserve_audit)
        topup = materialize_structural_topup(
            read_jsonl(args.original_raw_labels),
            read_jsonl(args.original_validations),
            read_jsonl(args.reserve_raw_labels),
            read_jsonl(args.reserve_validations),
            read_jsonl(args.reserve_schedule),
            reserve_audit,
            _policy(args.policy),
            now_unix=args.now_unix,
            required_deficits=7,
            required_reserve_attempts=args.required_reserve_attempts,
        )
        nontraining = publish_reserve_nontraining_bundle(
            args.nontraining_output,
            topup,
            reserve_freeze_audit=reserve_audit,
        )
        nontraining_manifest_path = args.nontraining_output / "manifest.json"
        report = dict(topup.batch.report)
        report["reserve_nontraining_bundle"] = {
            "path": str(args.nontraining_output.resolve()),
            "manifest_path": str(nontraining_manifest_path.resolve()),
            "manifest_file_sha256": hashlib.sha256(
                nontraining_manifest_path.read_bytes()
            ).hexdigest(),
            "manifest_body_sha256": nontraining["manifest_sha256"],
            "training_allowed": False,
        }
        final_batch = VerifiedSFTBatch(
            train_labels=topup.batch.train_labels,
            holdout_labels=topup.batch.holdout_labels,
            audits=topup.batch.audits,
            report=report,
        )
        final = publish_final_bundle(args.output, final_batch)
        result = {"final": final, "reserve_nontraining": nontraining}
    else:
        final = _load_final_bundle(args.final_bundle)
        result = publish_training_collection(
            args.output,
            final,
            _policy(args.policy),
            parent_receipt_path=args.parent_receipt,
            evaluation_contract_path=args.evaluation_contract,
            evaluation_contract_sha256=args.evaluation_contract_sha256,
            source_git_sha=args.source_git_sha,
        )
    print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
