from __future__ import annotations

import json
import math
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .batch import audit_launch_manifest
from .common import (
    IntegrityError,
    canonical_bytes,
    classify_marketplace_result,
    read_json,
    sha256_bytes,
    sha256_file,
    structured_attempt_exhausted,
    write_json_create_only,
    write_text_create_only,
)
from .manifest import verify_manifest


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _frozen_identity(manifest: dict[str, Any], launch_manifest: dict[str, Any]) -> dict[str, str]:
    core = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if manifest.get("manifest_sha256") != sha256_bytes(canonical_bytes(core)):
        raise IntegrityError("frozen manifest has an invalid manifest_sha256")
    campaign_id = manifest.get("campaign", {}).get("campaign_id")
    if campaign_id != launch_manifest.get("campaign_id"):
        raise IntegrityError("frozen and launch manifest campaign identities differ")
    source = manifest.get("source_contract")
    inference = manifest.get("inference_contract")
    if not isinstance(source, dict) or not isinstance(inference, dict):
        raise IntegrityError("frozen manifest lacks source/inference contracts")
    return {
        "frozen_manifest_sha256": manifest["manifest_sha256"],
        "harness_sha256": str(source.get("harness_sha256", "")),
        "inference_contract_sha256": sha256_bytes(canonical_bytes(inference)),
        "matrix_sha256": str(launch_manifest["matrix_sha256"]),
        "endpoint_manifest_sha256": str(
            launch_manifest.get("endpoint_manifest_sha256") or ""
        ),
    }


def _touched(
    limit_audit: dict[str, Any], category: str, *, ignore: frozenset[str] = frozenset()
) -> bool:
    records = (limit_audit.get("categories") or {}).get(category)
    if not isinstance(records, dict):
        return True
    return any(
        not isinstance(record, dict)
        or type(record.get("touched_count")) is not int
        or record["touched_count"] > 0
        for name, record in records.items()
        if name not in ignore
    )


def _weight_sha256(manifest: dict[str, Any], arm: str) -> str | None:
    contract = manifest.get("model_contract") or {}
    if arm == "base":
        return contract.get("base_weight_sha256")
    if arm in {"trained", "corrected", "post_sft"}:
        return contract.get("trained_weight_sha256")
    return None


def _identity_errors(
    summary: dict[str, Any], trajectory: dict[str, Any], spec: dict[str, Any]
) -> list[str]:
    model_spec = spec.get("model")
    model_name = model_spec if isinstance(model_spec, str) else (model_spec or {}).get("name")
    expected = {
        "env": spec.get("env"),
        "scaffold": spec.get("scaffold"),
        "model": model_name,
        "task_id": (spec.get("task") or {}).get("task_id"),
        "condition": spec.get("condition"),
    }
    errors = []
    for field, value in expected.items():
        if summary.get(field) != value:
            errors.append(f"summary.{field} differs from launch spec")
        if trajectory.get(field) != value:
            errors.append(f"trajectory.{field} differs from launch spec")
    evaluation = trajectory.get("evaluation")
    if not isinstance(evaluation, dict):
        errors.append("trajectory.evaluation is absent")
    elif summary.get("outcome") != evaluation.get("outcome"):
        errors.append("summary and trajectory outcomes differ")
    return errors


def _finite_score(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or not 0 <= result <= 1:
        return None
    return result


def _fresh_scores(trajectory_path: Path, *, repository_root: Path) -> tuple[float | None, float | None]:
    from caveat.scoring.rescore import cell_binary, cell_strict

    with _working_directory(repository_root):
        return cell_strict(str(trajectory_path)), cell_binary(str(trajectory_path))


def _convert_one(
    launch: dict[str, Any],
    *,
    frozen_manifest: dict[str, Any],
    frozen_identity: dict[str, str],
    launch_manifest_sha256: str,
    repository_root: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    run_id = launch["run_id"]
    result_dir = Path(launch["results"])
    summary_path = result_dir / "summary.json"
    trajectory_path = result_dir / "trajectory.json"
    classification: dict[str, Any] = {
        "run_id": run_id,
        "arm": launch["arm"],
        "result_dir": str(result_dir),
        "infrastructure_valid": False,
        "classification": "missing",
        "reasons": [],
    }
    if not summary_path.is_file() or not trajectory_path.is_file():
        classification["reasons"] = ["summary.json or trajectory.json is absent"]
        return None, classification
    try:
        summary = read_json(summary_path)
        trajectory = read_json(trajectory_path)
        spec_path = Path(launch["config"])
        spec = read_json(spec_path)
    except IntegrityError as exc:
        classification.update(classification="malformed", reasons=[str(exc)])
        return None, classification

    identity_errors = _identity_errors(summary, trajectory, spec)
    if identity_errors:
        classification.update(classification="identity_mismatch", reasons=identity_errors)
        return None, classification
    result_class, result_reason = classify_marketplace_result(summary, trajectory)
    if not result_class.startswith("behavioral_"):
        classification.update(
            classification=(
                "infrastructure_error"
                if result_class == "infrastructure_invalid"
                else result_class
            ),
            reasons=[str(result_reason or result_class)],
        )
        return None, classification
    outcome = summary.get("outcome")
    evaluation = trajectory["evaluation"]
    chosen = evaluation.get("chosen")
    no_purchase = result_class in {
        "behavioral_no_purchase",
        "behavioral_protocol_failure",
    }

    if no_purchase:
        preservation_strict, strict_binary = 0.0, 0.0
    else:
        preservation_strict, strict_binary = _fresh_scores(
            trajectory_path, repository_root=repository_root
        )
        preservation_strict = _finite_score(preservation_strict)
        strict_binary = _finite_score(strict_binary)
        if preservation_strict is None or strict_binary not in {0.0, 1.0}:
            classification.update(
                classification="unscorable",
                reasons=["fresh strict rescoring did not produce P* and a binary hero score"],
            )
            return None, classification

    stats = trajectory.get("stats") or {}
    caveat_harness = stats.get("caveat_harness") or {}
    compiler_pass = bool(
        caveat_harness.get("contract_compile_calls") == 1
        and caveat_harness.get("contract_compile_failures") == 0
        and isinstance(caveat_harness.get("contract_sha256"), str)
        and len(caveat_harness["contract_sha256"]) == 64
    )
    limit_audit = stats.get("limit_audit") or {}
    audit_contract = spec.get("audit_contract") or {}
    runtime_reasons = []
    if audit_contract != launch.get("audit_contract"):
        runtime_reasons.append("launch and launch-spec audit contracts differ")
    for field, value in frozen_identity.items():
        if audit_contract.get(field) != value:
            runtime_reasons.append(f"audit contract {field} differs from frozen inputs")
    if limit_audit.get("complete") is not True:
        runtime_reasons.append(f"limit audit is incomplete: {limit_audit.get('error')}")
    if limit_audit.get("contract_sha256") != audit_contract.get("limit_contract_sha256"):
        runtime_reasons.append("runtime limit-contract hash differs from launch spec")
    if caveat_harness.get("runtime_source_attestation") != frozen_identity["harness_sha256"]:
        runtime_reasons.append("runtime source attestation differs from frozen harness")
    if caveat_harness.get("evaluation_input_attestation") != frozen_identity["matrix_sha256"]:
        runtime_reasons.append("evaluation-input attestation differs from launch matrix")

    recorded_pstar = _finite_score(summary.get("preservation_strict"))
    recorded_binary = _finite_score(summary.get("strict_binary"))
    score_audit = {
        "recorded_pstar": recorded_pstar,
        "recorded_strict_binary": recorded_binary,
        "recorded_scores_match_fresh": (
            (recorded_pstar is None or recorded_pstar == preservation_strict)
            and (recorded_binary is None or recorded_binary == strict_binary)
        ),
    }
    observation = {
        "run_id": run_id,
        "arm": launch["arm"],
        "preservation_strict": preservation_strict,
        "strict_binary": strict_binary,
        "valid_transaction": chosen is not None,
        "compiler_pass": compiler_pass,
        "infrastructure_valid": True,
        "audit": {
            "harness_sha256": frozen_identity["harness_sha256"],
            "inference_contract_sha256": frozen_identity[
                "inference_contract_sha256"
            ],
            "safety_backstop_bound": _touched(
                limit_audit,
                "safety_backstops",
                ignore=frozenset({"structured_response_attempts"}),
            ),
            "lossy_context_bound": _touched(limit_audit, "lossy_context_limits"),
            "protocol_attempt_exhausted": structured_attempt_exhausted(stats),
            "runtime_drift": bool(runtime_reasons),
            "runtime_drift_reasons": runtime_reasons,
            "frozen_manifest_sha256": frozen_identity["frozen_manifest_sha256"],
            "matrix_sha256": frozen_identity["matrix_sha256"],
            "endpoint_manifest_sha256": frozen_identity[
                "endpoint_manifest_sha256"
            ],
            "launch_manifest_sha256": launch_manifest_sha256,
            "launch_config_sha256": sha256_file(spec_path),
            "summary_sha256": sha256_file(summary_path),
            "trajectory_sha256": sha256_file(trajectory_path),
            "limit_contract_sha256": limit_audit.get("contract_sha256"),
            "arm_weight_sha256": _weight_sha256(frozen_manifest, launch["arm"]),
            **score_audit,
        },
        "result": {
            "outcome": outcome,
            "chosen": chosen,
            "num_steps": summary.get("num_steps"),
            "seconds": summary.get("seconds"),
        },
    }
    classification.update(
        infrastructure_valid=True,
        classification=result_class,
        reasons=[],
        runtime_drift=bool(runtime_reasons),
        safety_backstop_bound=observation["audit"]["safety_backstop_bound"],
        lossy_context_bound=observation["audit"]["lossy_context_bound"],
    )
    return observation, classification


def convert_results(
    *,
    launch_manifest: dict[str, Any],
    frozen_manifest: dict[str, Any],
    repository_root: Path,
    observations_output: Path,
    audit_output: Path,
) -> dict[str, Any]:
    audit_launch_manifest(launch_manifest)
    if not repository_root.is_dir():
        raise IntegrityError(f"repository root is absent: {repository_root}")
    verify_manifest(frozen_manifest, root=repository_root)
    frozen_identity = _frozen_identity(frozen_manifest, launch_manifest)
    observations = []
    classifications = []
    for launch in launch_manifest["launches"]:
        observation, classification = _convert_one(
            launch,
            frozen_manifest=frozen_manifest,
            frozen_identity=frozen_identity,
            launch_manifest_sha256=launch_manifest["launch_manifest_sha256"],
            repository_root=repository_root,
        )
        classifications.append(classification)
        if observation is not None:
            observations.append(observation)
    lines = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in observations
    )
    write_text_create_only(observations_output, lines)
    invalid = [row for row in classifications if not row["infrastructure_valid"]]
    core = {
        "schema_version": 1,
        "campaign_id": launch_manifest["campaign_id"],
        "launch_manifest_sha256": launch_manifest["launch_manifest_sha256"],
        "frozen_manifest_sha256": frozen_manifest["manifest_sha256"],
        "frozen_campaign_contract_sha256": sha256_bytes(
            canonical_bytes(frozen_manifest["campaign"])
        ),
        "matrix_sha256": launch_manifest["matrix_sha256"],
        "endpoint_manifest_sha256": launch_manifest.get("endpoint_manifest_sha256"),
        "observations_sha256": sha256_file(observations_output),
        "expected_runs": len(launch_manifest["launches"]),
        "observation_rows": len(observations),
        "infrastructure_invalid_runs": len(invalid),
        "complete": not invalid and len(observations) == len(launch_manifest["launches"]),
        "runs": classifications,
    }
    report = {
        **core,
        "conversion_audit_sha256": sha256_bytes(canonical_bytes(core)),
    }
    write_json_create_only(audit_output, report)
    return report
