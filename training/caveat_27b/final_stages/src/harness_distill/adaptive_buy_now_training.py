"""Eight-state chosen-CE correction from the sealed step-25 checkpoint.

This module deliberately reuses the already executed numeric-CE renderer,
checkpoint bridge, PRIME patches, plan validation, and receipt validation from
``action_weighted_ce_training``.  It changes only the frozen corpus profile:
eight train-only same-state Buy-Now corrections, no generic retention rows,
and one step-25 -> step-26 update at 2e-6.  Rejected Qwen turns are retained as
provenance but are not used in the loss; adding a preference loss would be a
different training method and is intentionally outside this fast successor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import action_weighted_ce_training as base

COLLECTION_SCHEMA = "harness-distill.adaptive-buy-now-training-collection.v1"
PLAN_SCHEMA = "harness-distill.adaptive-buy-now-chosen-ce-plan.v1"
RECEIPT_SCHEMA = "harness-distill.adaptive-buy-now-chosen-ce-receipt.v1"
RENDER_AUDIT_SCHEMA = "harness-distill.adaptive-buy-now-chosen-ce-render-audit.v1"
PHASE = "premature_buy_now_correction"
PHASE_COUNTS = {PHASE: 8}
VARIANT_COUNTS = {"graded": 2, "graded3": 2, "graded4": 2, "mixed": 2}
LEARNING_RATE = 2.0e-6
ACTION_FRACTION = Fraction(15, 100)
RATIONALE_FRACTION = Fraction(85, 100)
if RATIONALE_FRACTION <= ACTION_FRACTION:  # pragma: no cover
    raise RuntimeError("adaptive chosen rationale mass must exceed action mass")


def _configure_profile() -> None:
    """Install the narrow immutable profile into the reviewed CE machinery."""

    base.COLLECTION_SCHEMA = COLLECTION_SCHEMA
    base.PLAN_SCHEMA = PLAN_SCHEMA
    base.RECEIPT_SCHEMA = RECEIPT_SCHEMA
    base.RENDER_AUDIT_SCHEMA = RENDER_AUDIT_SCHEMA
    base.SCIENTIFIC_LABEL = "adaptive_same_state_buy_now_chosen_ce_from_sealed_step25"
    base.EXECUTED_PHASE_COUNTS = dict(PHASE_COUNTS)
    base.EXPECTED_EXECUTED_ROWS = 8
    base.EXPECTED_RETENTION_ROWS = 0
    base.EXPECTED_TOTAL_ROWS = 8
    base.MAX_STATE_FRACTION = Fraction(1, 8)
    base.BUCKET_FRACTIONS = {
        f"{PHASE}/action": ACTION_FRACTION,
        f"{PHASE}/nonaction": RATIONALE_FRACTION,
    }
    base.LEARNING_RATE = LEARNING_RATE
    base._run_prime_child = _run_prime_child


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise base.ActionWeightedCEError(f"unsafe or absent JSON: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise base.ActionWeightedCEError(f"JSON is not an object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise base.ActionWeightedCEError(f"unsafe or absent JSONL: {path}")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = json.loads(line)
        if not isinstance(value, dict):
            raise base.ActionWeightedCEError(f"JSONL row {number} is not an object")
        rows.append(value)
    return rows


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    base._write_new(path, payload)


def _source_interventions(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    interventions: dict[str, dict[str, Any]] = {}
    successors: set[str] = set()
    sources = manifest.get("source_bundles")
    if not isinstance(sources, list) or not sources:
        raise base.ActionWeightedCEError("exact8 manifest has no source bundles")
    for descriptor in sources:
        if not isinstance(descriptor, Mapping):
            raise base.ActionWeightedCEError("source bundle descriptor is malformed")
        path = Path(str(descriptor.get("path"))).resolve()
        if descriptor.get("sha256") != _sha256(path):
            raise base.ActionWeightedCEError("source bundle identity drifted")
        bundle = _read_json(path)
        for source_row in bundle.get("rows", []):
            trace = Path(str(source_row.get("trace_path"))).resolve()
            if not trace.is_file() or trace.is_symlink():
                continue
            for record in _jsonl(trace):
                state_id = record.get("state_id")
                if record.get("route") == "teacher_intervention":
                    if (
                        record.get("trigger_kind") != "visible_pdp_buy_now_click"
                        or not isinstance(state_id, str)
                        or state_id in interventions
                    ):
                        continue
                    interventions[state_id] = record
                elif (
                    record.get("route") == "successor"
                    and record.get("successor_validated") is True
                    and isinstance(state_id, str)
                ):
                    successors.add(state_id)
    return {
        state_id: record for state_id, record in interventions.items() if state_id in successors
    }


def build_training_collection(
    *, exact8_manifest_path: str | Path, output_root: str | Path
) -> dict[str, Any]:
    """Convert the sealed preference artifact into the reviewed CE row dialect."""

    _configure_profile()
    manifest_path = Path(exact8_manifest_path).resolve()
    exact = _read_json(manifest_path)
    body = {key: value for key, value in exact.items() if key != "manifest_sha256"}
    if (
        exact.get("schema") != "harness-distill.adaptive-buy-now-materialization.v1"
        or exact.get("status") != "complete"
        or exact.get("manifest_sha256") != hashlib.sha256(_canonical(body)).hexdigest()
        or exact.get("rows") != 8
        or exact.get("variant_counts") != VARIANT_COUNTS
        or exact.get("split_counts") != {"train": 8, "heldout": 0, "evaluation": 0}
    ):
        raise base.ActionWeightedCEError("exact8 materialization policy or hash drifted")
    descriptor = exact.get("files", {}).get("chosen_rejected.jsonl")
    pair_path = manifest_path.parent / "chosen_rejected.jsonl"
    if (
        not isinstance(descriptor, Mapping)
        or descriptor.get("rows") != 8
        or descriptor.get("bytes") != pair_path.stat().st_size
        or descriptor.get("sha256") != _sha256(pair_path)
    ):
        raise base.ActionWeightedCEError("chosen/rejected pair file identity drifted")
    pairs = _jsonl(pair_path)
    sources = _source_interventions(exact)
    if len(pairs) != 8 or Counter(row.get("variant") for row in pairs) != Counter(VARIANT_COUNTS):
        raise base.ActionWeightedCEError("exact8 pair balance drifted")

    selection: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    for row in pairs:
        state_id = row.get("state_id")
        intervention = sources.get(str(state_id))
        chosen = row.get("chosen")
        rejected = row.get("rejected")
        successor = row.get("successor")
        if (
            row.get("source_split") != "train"
            or row.get("phase") != PHASE
            or not isinstance(intervention, Mapping)
            or intervention.get("teacher_completion") != chosen
            or not isinstance(chosen, Mapping)
            or not isinstance(rejected, Mapping)
            or not isinstance(successor, Mapping)
            or successor.get("successor_validated") is not True
            or successor.get("request_changed") is not True
            or successor.get("direct_checkout_avoided") is not True
            or successor.get("order_not_placed") is not True
        ):
            raise base.ActionWeightedCEError("same-state BuyNow pair provenance drifted")
        chosen_content = chosen.get("content")
        rejected_content = rejected.get("content")
        if not isinstance(chosen_content, str) or not isinstance(rejected_content, str):
            raise base.ActionWeightedCEError("chosen/rejected assistant content is absent")
        base.final_action_member_start(chosen_content)
        if "buy now" not in rejected_content.casefold():
            raise base.ActionWeightedCEError("rejected action has no Buy Now semantics")
        training_row = {
            "schema": "harness-distill.adaptive-buy-now-chosen-ce-row.v1",
            "row_id": row["row_id"],
            "state_id": state_id,
            "variant": row["variant"],
            "source_split": "train",
            "phase": PHASE,
            "messages_before_action": row["messages_before_action"],
            "teacher_message": chosen,
            "qwen_message": rejected,
            "tools": row["tools"],
            "tool_choice": row.get("tool_choice"),
            "parallel_tool_calls": row.get("parallel_tool_calls"),
            "response_format": row.get("response_format"),
        }
        selection.append(training_row)
        audits.append(
            {
                "schema": "harness-distill.adaptive-buy-now-execution-audit.v1",
                "row_id": row["row_id"],
                "state_id": state_id,
                "phase": PHASE,
                "status": "accepted",
                "accepted": True,
                "browser_executed": True,
                "executed": True,
                "successor_observed": True,
                "postcondition_verified": True,
                "before_browser_state_sha256": successor["before_browser_state_sha256"],
                "after_browser_state_sha256": successor["after_browser_state_sha256"],
                "chosen_sha256": row["chosen_sha256"],
                "rejected_response_sha256": row["rejected_response_sha256"],
            }
        )
    selection.sort(key=lambda row: (row["variant"], row["row_id"]))
    audits.sort(key=lambda row: row["row_id"])
    output = Path(output_root).resolve()
    if output.exists() or output.is_symlink():
        raise base.ActionWeightedCEError("training collection output must be fresh")
    output.mkdir(parents=True, mode=0o700)
    payloads = {
        "selection.jsonl": b"".join(_canonical(row) + b"\n" for row in selection),
        "retention.jsonl": b"",
        "execution_audits.jsonl": b"".join(_canonical(row) + b"\n" for row in audits),
    }
    for name, payload in payloads.items():
        _write_new(output / name, payload)
    files = {
        name: {
            "relative_path": name,
            "rows": 0 if name == "retention.jsonl" else 8,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        for name, payload in payloads.items()
    }
    collection_body = {
        "schema": COLLECTION_SCHEMA,
        "status": "ok",
        "selection_rows": 8,
        "retention_rows": 0,
        "evaluation_rows": 0,
        "heldout_rows": 0,
        "phase_counts": PHASE_COUNTS,
        "variant_counts": VARIANT_COUNTS,
        "teacher_model": "gpt-5.6-sol",
        "teacher_reasoning_effort": "low",
        "renderer": {"name": "qwen3.5", "enable_thinking": True, "assistant_only": True},
        "state_overlap": 0,
        "objective": "chosen_ce_only_rejected_retained_as_provenance",
        "loss_mass": {
            "action": f"{ACTION_FRACTION.numerator}/{ACTION_FRACTION.denominator}",
            "rationale": f"{RATIONALE_FRACTION.numerator}/{RATIONALE_FRACTION.denominator}",
            "retention": "0/1",
            "rejected_unlikelihood": "0/1",
        },
        "source_exact8_manifest": str(manifest_path),
        "source_exact8_manifest_sha256": _sha256(manifest_path),
        "files": files,
    }
    collection = {
        **collection_body,
        "manifest_sha256": hashlib.sha256(
            base.canonical_json(collection_body).encode()
        ).hexdigest(),
    }
    _write_new(output / "manifest.json", (base.canonical_json(collection) + "\n").encode())
    validated = base.validate_collection_manifest(output / "manifest.json")
    return {
        "manifest_path": validated["manifest_path"],
        "manifest_file_sha256": validated["manifest_sha256"],
        "manifest_body_sha256": validated["manifest_body_sha256"],
        "selection_rows": 8,
        "retention_rows": 0,
        "variant_counts": VARIANT_COUNTS,
        "learning_rate": LEARNING_RATE,
        "loss_mass": collection_body["loss_mass"],
    }


def _run_prime_child(
    prime_root: Path, arguments: Sequence[str], *, timeout: float = 3600
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(base._prime_python(prime_root)),
            "-m",
            "harness_distill.adaptive_buy_now_training",
            *arguments,
        ],
        text=True,
        capture_output=True,
        env=base._prime_environment(prime_root),
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-8000:]
        raise base.ActionWeightedCEError(f"adaptive PRIME child failed: {detail}")
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise base.ActionWeightedCEError("adaptive PRIME child returned malformed output") from exc
    if not isinstance(result, dict):
        raise base.ActionWeightedCEError("adaptive PRIME child result is not an object")
    return result


def prepare_adaptive_buy_now_training(**kwargs: Any) -> dict[str, Any]:
    _configure_profile()
    return base.prepare_action_weighted_ce_training(**kwargs)


def validate_training_collection(path: str | Path) -> dict[str, Any]:
    _configure_profile()
    return base.validate_collection_manifest(path)


def validate_training_plan(path: str | Path, *, require_launch_patches: bool = False) -> dict:
    _configure_profile()
    return base.validate_action_weighted_ce_plan(
        path, require_launch_patches=require_launch_patches
    )


def write_training_receipt(*, plan_path: str | Path, executor_git_sha: str) -> dict[str, Any]:
    _configure_profile()
    return base.write_action_weighted_ce_receipt(
        plan_path=plan_path, executor_git_sha=executor_git_sha
    )


def validate_training_receipt(path: str | Path) -> dict[str, Any]:
    _configure_profile()
    return base.validate_action_weighted_ce_receipt(path)


def _main(argv: Sequence[str] | None = None) -> int:
    _configure_profile()
    args = list(argv) if argv is not None else None
    if args is None:
        import sys

        args = sys.argv[1:]
    if args and args[0] in {"_prime-materialize", "_prime-audit"}:
        return base._prime_main(args)
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build-collection")
    build.add_argument("--exact8-manifest", type=Path, required=True)
    build.add_argument("--output-root", type=Path, required=True)
    validate = commands.add_parser("validate-collection")
    validate.add_argument("--manifest", type=Path, required=True)
    parsed = parser.parse_args(args)
    if parsed.command == "build-collection":
        result = build_training_collection(
            exact8_manifest_path=parsed.exact8_manifest, output_root=parsed.output_root
        )
    else:
        validated = validate_training_collection(parsed.manifest)
        result = {
            key: value for key, value in validated.items() if key not in {"selection", "retention"}
        }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
