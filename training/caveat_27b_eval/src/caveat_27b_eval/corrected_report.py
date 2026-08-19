"""Mixed-provenance observation binding and reports for CAVEAT-27B evaluation."""

from __future__ import annotations

import json
import math
from pathlib import Path
from statistics import mean
from typing import Any

from .batch import audit_launch_manifest
from .common import (
    IntegrityError,
    canonical_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json_create_only,
    write_text_create_only,
)
from .corrected_eval import (
    DEVELOPMENT_CRITERIA,
    DEVELOPMENT_EVALUATION,
    FINAL_EVALUATION,
    ManifestValidator,
    _causal_spec,
    _self_hash,
    _successful_gate_report,
    audit_corrected_matrix,
    audit_corrected_preparation,
)
from .statistics import (
    _arm_mean,
    _paired_rows,
    exact_mcnemar_one_sided,
    hierarchical_bootstrap_lower_bound,
    load_observations,
    sign_randomization_pvalue,
)

BOUND_OBSERVATIONS_SCHEMA = "caveat-27b-eval.corrected-bound-observations.v1"
DEVELOPMENT_REPORT_SCHEMA = (
    "caveat-27b-eval.corrected-development-gate-report.v1"
)
FINAL_REPORT_SCHEMA = "caveat-27b-eval.corrected-final-report.v1"


def _bounded(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        raise IntegrityError(f"{label} is boolean, not numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise IntegrityError(f"{label} is not numeric") from exc
    if not math.isfinite(result) or not 0 <= result <= 1:
        raise IntegrityError(f"{label} is outside [0, 1]")
    return result


def _conversion_bundle(
    *,
    observations_path: Path,
    conversion_audit_path: Path,
    launch_manifest: dict[str, Any],
    frozen_manifest: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    audit_launch_manifest(launch_manifest)
    audit = read_json(conversion_audit_path)
    if not isinstance(audit, dict):
        raise IntegrityError("conversion audit is not an object")
    _self_hash(audit, "conversion_audit_sha256", label="conversion audit")
    expected = {
        "campaign_id": launch_manifest["campaign_id"],
        "launch_manifest_sha256": launch_manifest["launch_manifest_sha256"],
        "frozen_manifest_sha256": frozen_manifest["manifest_sha256"],
        "matrix_sha256": launch_manifest["matrix_sha256"],
        "endpoint_manifest_sha256": launch_manifest.get("endpoint_manifest_sha256"),
        "observations_sha256": sha256_file(observations_path),
        "expected_runs": len(launch_manifest["launches"]),
        "observation_rows": len(launch_manifest["launches"]),
        "infrastructure_invalid_runs": 0,
        "complete": True,
    }
    for field, value in expected.items():
        if audit.get(field) != value:
            raise IntegrityError(f"conversion audit {field} differs from its inputs")
    classifications = audit.get("runs")
    launch_ids = {launch["run_id"] for launch in launch_manifest["launches"]}
    if (
        not isinstance(classifications, list)
        or len(classifications) != len(launch_ids)
        or {row.get("run_id") for row in classifications if isinstance(row, dict)}
        != launch_ids
        or any(
            not isinstance(row, dict)
            or row.get("infrastructure_valid") is not True
            or not str(row.get("classification", "")).startswith("behavioral_")
            for row in classifications
        )
    ):
        raise IntegrityError(
            "conversion audit contains incomplete/nonbehavioral evidence"
        )

    observations = load_observations(observations_path)
    indexed: dict[str, dict[str, Any]] = {}
    launches = {launch["run_id"]: launch for launch in launch_manifest["launches"]}
    for observation in observations:
        if not isinstance(observation, dict):
            raise IntegrityError("converted observation is not an object")
        run_id = str(observation.get("run_id", ""))
        if not run_id or run_id in indexed:
            raise IntegrityError(
                f"converted observation run ID is duplicate/absent: {run_id}"
            )
        indexed[run_id] = observation
    if set(indexed) != launch_ids:
        raise IntegrityError("converted observations differ from their launch subset")

    expected_inference = sha256_bytes(
        canonical_bytes(frozen_manifest["inference_contract"])
    )
    expected_identity = {
        "frozen_manifest_sha256": frozen_manifest["manifest_sha256"],
        "harness_sha256": frozen_manifest["source_contract"]["harness_sha256"],
        "inference_contract_sha256": expected_inference,
        "matrix_sha256": launch_manifest["matrix_sha256"],
        "endpoint_manifest_sha256": launch_manifest["endpoint_manifest_sha256"],
        "launch_manifest_sha256": launch_manifest["launch_manifest_sha256"],
    }
    for run_id, observation in indexed.items():
        launch = launches[run_id]
        if (
            observation.get("arm") != launch["arm"]
            or observation.get("infrastructure_valid") is not True
        ):
            raise IntegrityError(
                f"{run_id} observation arm/validity differs from launch"
            )
        observation["preservation_strict"] = _bounded(
            observation.get("preservation_strict"),
            label=f"{run_id}.preservation_strict",
        )
        binary = _bounded(
            observation.get("strict_binary"), label=f"{run_id}.strict_binary"
        )
        if binary not in {0.0, 1.0}:
            raise IntegrityError(f"{run_id}.strict_binary is not binary")
        observation["strict_binary"] = binary
        if not isinstance(observation.get("valid_transaction"), bool) or not isinstance(
            observation.get("compiler_pass"), bool
        ):
            raise IntegrityError(f"{run_id} converted booleans are malformed")
        evidence = observation.get("audit")
        if not isinstance(evidence, dict):
            raise IntegrityError(f"{run_id} converted audit is absent")
        for field, value in expected_identity.items():
            if evidence.get(field) != value:
                raise IntegrityError(
                    f"{run_id}.audit.{field} differs from conversion inputs"
                )
        if evidence.get("launch_config_sha256") != launch["config_sha256"]:
            raise IntegrityError(f"{run_id} converted launch config hash differs")
        if evidence.get("recorded_scores_match_fresh") is not True:
            raise IntegrityError(f"{run_id} recorded scores differ from fresh scores")
        for field in ("safety_backstop_bound", "lossy_context_bound", "runtime_drift"):
            if not isinstance(evidence.get(field), bool):
                raise IntegrityError(f"{run_id}.audit.{field} is not boolean")
    return indexed


def _read_bundle(bundle_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = bundle_dir.resolve()
    receipt = read_json(root / "bundle.json")
    launch = read_json(root / "launch_manifest.json")
    if not isinstance(receipt, dict) or not isinstance(launch, dict):
        raise IntegrityError("corrected completion bundle is malformed")
    _self_hash(receipt, "bundle_sha256", label="corrected completion bundle")
    audit_launch_manifest(launch)
    if (
        receipt.get("launch_manifest_sha256") != launch["launch_manifest_sha256"]
        or receipt.get("new_run_count") != len(launch["launches"])
        or launch.get("execution_mode") != "single_arm_completion"
    ):
        raise IntegrityError("corrected completion bundle receipt differs from launch")
    return receipt, launch


def bind_evaluation_observations(
    *,
    preparation_dir: Path,
    repository_root: Path,
    evaluation: str,
    reuse_observations_path: Path,
    reuse_conversion_audit_path: Path,
    corrected_bundle_dir: Path,
    corrected_observations_path: Path,
    corrected_conversion_audit_path: Path,
    output_path: Path,
    receipt_path: Path,
    manifest_validator: ManifestValidator | None = None,
) -> dict[str, Any]:
    """Bind reused and new observations into one target-matrix evidence file."""

    if evaluation not in {DEVELOPMENT_EVALUATION, FINAL_EVALUATION}:
        raise IntegrityError(f"unknown corrected evaluation: {evaluation}")
    if output_path.exists() or receipt_path.exists():
        raise IntegrityError("refusing to overwrite bound corrected observations")
    audited = audit_corrected_preparation(
        preparation_dir=preparation_dir,
        repository_root=repository_root,
        manifest_validator=manifest_validator,
    )
    protocol = audited["protocol"]
    if evaluation == DEVELOPMENT_EVALUATION:
        matrix = audited["development_matrix"]
        reuse_launch = audited["development_reuse"]
    else:
        matrix = audited["final_matrix"]
        reuse_launch = audited["final_reuse"]
    audit_corrected_matrix(matrix, config=audited["campaign"])
    source_root = Path(protocol["source"]["preparation_dir"])
    source_frozen = read_json(source_root / "frozen_manifest.json")
    if not isinstance(source_frozen, dict):
        raise IntegrityError("source frozen manifest is absent")
    reuse = _conversion_bundle(
        observations_path=reuse_observations_path,
        conversion_audit_path=reuse_conversion_audit_path,
        launch_manifest=reuse_launch,
        frozen_manifest=source_frozen,
    )
    bundle_receipt, corrected_launch = _read_bundle(corrected_bundle_dir)
    if (
        bundle_receipt.get("evaluation") != evaluation
        or corrected_launch.get("evaluation") != evaluation
        or corrected_launch.get("protocol_sha256") != protocol["protocol_sha256"]
        or corrected_launch.get("reuse_launch_manifest_sha256")
        != reuse_launch["launch_manifest_sha256"]
        or corrected_launch.get("matrix_sha256") != matrix["matrix_sha256"]
    ):
        raise IntegrityError(
            "corrected completion bundle belongs to a different protocol"
        )
    corrected_frozen = read_json(corrected_bundle_dir / "frozen_manifest.json")
    if corrected_frozen != audited["derived_frozen_manifest"]:
        raise IntegrityError("corrected completion bundle frozen manifest changed")
    corrected = _conversion_bundle(
        observations_path=corrected_observations_path,
        conversion_audit_path=corrected_conversion_audit_path,
        launch_manifest=corrected_launch,
        frozen_manifest=corrected_frozen,
    )

    reuse_launches = {launch["run_id"]: launch for launch in reuse_launch["launches"]}
    corrected_launches = {
        launch["run_id"]: launch for launch in corrected_launch["launches"]
    }
    rows_by_id = {row["run_id"]: row for row in matrix["runs"]}
    contracts = protocol["evaluations"][evaluation]["cell_contracts"]
    bound: list[dict[str, Any]] = []
    for target_run_id in sorted(rows_by_id):
        target_row = rows_by_id[target_run_id]
        contract = contracts.get(target_run_id)
        if not isinstance(contract, dict):
            raise IntegrityError(f"target cell contract is absent: {target_run_id}")
        if contract.get("target_row_sha256") != sha256_bytes(
            canonical_bytes(target_row)
        ):
            raise IntegrityError(f"target row hash changed: {target_run_id}")
        evidence_kind = contract["evidence_kind"]
        if evidence_kind == "reuse":
            evidence_run_id = contract["source_run_id"]
            observation = reuse.get(evidence_run_id)
            launch = reuse_launches.get(evidence_run_id)
            evidence_launch_sha256 = reuse_launch["launch_manifest_sha256"]
        elif evidence_kind == "new":
            evidence_run_id = target_run_id
            observation = corrected.get(evidence_run_id)
            launch = corrected_launches.get(evidence_run_id)
            evidence_launch_sha256 = corrected_launch["launch_manifest_sha256"]
        else:
            raise IntegrityError(f"unknown evidence kind for {target_run_id}")
        if observation is None or launch is None:
            raise IntegrityError(f"bound evidence is absent for {target_run_id}")
        spec = read_json(Path(launch["config"]))
        if not isinstance(spec, dict):
            raise IntegrityError(f"evidence launch spec is malformed: {target_run_id}")
        task_sha256 = sha256_bytes(canonical_bytes(spec.get("task")))
        causal_sha256 = sha256_bytes(canonical_bytes(_causal_spec(spec)))
        if (
            task_sha256 != contract["task_sha256"]
            or causal_sha256 != contract["causal_config_sha256"]
            or spec.get("block_seed") != contract["block_seed"]
        ):
            raise IntegrityError(
                f"task/config/seed reuse contract failed: {target_run_id}"
            )
        evidence_audit = observation["audit"]
        if evidence_audit.get("runtime_drift") is not False:
            raise IntegrityError(
                f"runtime-drifted evidence cannot be reused: {target_run_id}"
            )
        if evidence_audit.get("harness_sha256") != contract["harness_sha256"]:
            raise IntegrityError(f"harness reuse contract failed: {target_run_id}")
        if evidence_audit.get("arm_weight_sha256") != contract["arm_weight_sha256"]:
            raise IntegrityError(f"model-weight contract failed: {target_run_id}")
        audit_core = {
            "protocol_sha256": protocol["protocol_sha256"],
            "target_matrix_sha256": matrix["matrix_sha256"],
            "target_row_sha256": contract["target_row_sha256"],
            "cell_contract_sha256": contract["cell_contract_sha256"],
            "evidence_kind": evidence_kind,
            "evidence_run_id": evidence_run_id,
            "evidence_launch_manifest_sha256": evidence_launch_sha256,
            "evidence_launch_config_sha256": launch["config_sha256"],
            "task_sha256": task_sha256,
            "harness_sha256": contract["harness_sha256"],
            "causal_config_sha256": causal_sha256,
            "arm_weight_sha256": contract["arm_weight_sha256"],
            "summary_sha256": evidence_audit["summary_sha256"],
            "trajectory_sha256": evidence_audit["trajectory_sha256"],
            "safety_backstop_bound": evidence_audit["safety_backstop_bound"],
            "lossy_context_bound": evidence_audit["lossy_context_bound"],
            "runtime_drift": evidence_audit["runtime_drift"],
            "recorded_scores_match_fresh": evidence_audit[
                "recorded_scores_match_fresh"
            ],
        }
        bound_audit = {
            **audit_core,
            "binding_sha256": sha256_bytes(canonical_bytes(audit_core)),
        }
        bound.append(
            {
                "run_id": target_run_id,
                "arm": target_row["arm"],
                "preservation_strict": observation["preservation_strict"],
                "strict_binary": observation["strict_binary"],
                "valid_transaction": observation["valid_transaction"],
                "compiler_pass": observation["compiler_pass"],
                "infrastructure_valid": True,
                "audit": bound_audit,
            }
        )
    text = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in bound
    )
    write_text_create_only(output_path, text)
    receipt_core = {
        "schema": BOUND_OBSERVATIONS_SCHEMA,
        "evaluation": evaluation,
        "protocol_sha256": protocol["protocol_sha256"],
        "matrix_sha256": matrix["matrix_sha256"],
        "reuse_launch_manifest_sha256": reuse_launch["launch_manifest_sha256"],
        "reuse_conversion_audit_sha256": read_json(reuse_conversion_audit_path)[
            "conversion_audit_sha256"
        ],
        "corrected_launch_manifest_sha256": corrected_launch["launch_manifest_sha256"],
        "corrected_conversion_audit_sha256": read_json(corrected_conversion_audit_path)[
            "conversion_audit_sha256"
        ],
        "observations_sha256": sha256_file(output_path),
        "observation_rows": len(bound),
        "reuse_rows": sum(row["audit"]["evidence_kind"] == "reuse" for row in bound),
        "new_rows": sum(row["audit"]["evidence_kind"] == "new" for row in bound),
        "complete": len(bound) == len(matrix["runs"]),
        "inputs": {
            "reuse_observations_path": str(reuse_observations_path.resolve()),
            "reuse_conversion_audit_path": str(reuse_conversion_audit_path.resolve()),
            "corrected_bundle_dir": str(corrected_bundle_dir.resolve()),
            "corrected_observations_path": str(corrected_observations_path.resolve()),
            "corrected_conversion_audit_path": str(
                corrected_conversion_audit_path.resolve()
            ),
        },
    }
    receipt = {
        **receipt_core,
        "binding_receipt_sha256": sha256_bytes(canonical_bytes(receipt_core)),
    }
    write_json_create_only(receipt_path, receipt)
    return receipt


def _bound_observations(
    *,
    audited: dict[str, Any],
    evaluation: str,
    observations_path: Path,
    binding_receipt_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if evaluation == DEVELOPMENT_EVALUATION:
        matrix = audited["development_matrix"]
    elif evaluation == FINAL_EVALUATION:
        matrix = audited["final_matrix"]
    else:  # pragma: no cover - callers guard
        raise IntegrityError(f"unknown corrected evaluation: {evaluation}")
    receipt = read_json(binding_receipt_path)
    if not isinstance(receipt, dict):
        raise IntegrityError("binding receipt is not an object")
    _self_hash(receipt, "binding_receipt_sha256", label="binding receipt")
    receipt_fields = {
        "schema",
        "evaluation",
        "protocol_sha256",
        "matrix_sha256",
        "reuse_launch_manifest_sha256",
        "reuse_conversion_audit_sha256",
        "corrected_launch_manifest_sha256",
        "corrected_conversion_audit_sha256",
        "observations_sha256",
        "observation_rows",
        "reuse_rows",
        "new_rows",
        "complete",
        "inputs",
        "binding_receipt_sha256",
    }
    if set(receipt) != receipt_fields:
        raise IntegrityError("binding receipt fields are not exact")
    protocol = audited["protocol"]
    reuse_launch = (
        audited["development_reuse"]
        if evaluation == DEVELOPMENT_EVALUATION
        else audited["final_reuse"]
    )
    expected = {
        "schema": BOUND_OBSERVATIONS_SCHEMA,
        "evaluation": evaluation,
        "protocol_sha256": protocol["protocol_sha256"],
        "matrix_sha256": matrix["matrix_sha256"],
        "reuse_launch_manifest_sha256": reuse_launch["launch_manifest_sha256"],
        "observations_sha256": sha256_file(observations_path),
        "observation_rows": len(matrix["runs"]),
        "reuse_rows": protocol["evaluations"][evaluation]["reuse_run_count"],
        "new_rows": protocol["evaluations"][evaluation]["new_run_count"],
        "complete": True,
    }
    for field, value in expected.items():
        if receipt.get(field) != value:
            raise IntegrityError(f"binding receipt {field} differs from protocol")

    input_fields = {
        "reuse_observations_path",
        "reuse_conversion_audit_path",
        "corrected_bundle_dir",
        "corrected_observations_path",
        "corrected_conversion_audit_path",
    }
    inputs = receipt.get("inputs")
    if (
        not isinstance(inputs, dict)
        or set(inputs) != input_fields
        or not all(
            isinstance(value, str) and Path(value).is_absolute()
            for value in inputs.values()
        )
    ):
        raise IntegrityError("binding receipt input paths are not exact absolute paths")
    source_root = Path(protocol["source"]["preparation_dir"])
    source_frozen = read_json(source_root / "frozen_manifest.json")
    if not isinstance(source_frozen, dict):
        raise IntegrityError("source frozen manifest is absent")
    reuse = _conversion_bundle(
        observations_path=Path(inputs["reuse_observations_path"]),
        conversion_audit_path=Path(inputs["reuse_conversion_audit_path"]),
        launch_manifest=reuse_launch,
        frozen_manifest=source_frozen,
    )
    reuse_audit = read_json(Path(inputs["reuse_conversion_audit_path"]))
    if not isinstance(reuse_audit, dict) or receipt.get(
        "reuse_conversion_audit_sha256"
    ) != reuse_audit.get("conversion_audit_sha256"):
        raise IntegrityError("binding receipt reuse conversion changed")
    corrected_bundle_dir = Path(inputs["corrected_bundle_dir"])
    bundle_receipt, corrected_launch = _read_bundle(corrected_bundle_dir)
    if (
        bundle_receipt.get("evaluation") != evaluation
        or corrected_launch.get("evaluation") != evaluation
        or corrected_launch.get("protocol_sha256") != protocol["protocol_sha256"]
        or corrected_launch.get("reuse_launch_manifest_sha256")
        != reuse_launch["launch_manifest_sha256"]
        or corrected_launch.get("matrix_sha256") != matrix["matrix_sha256"]
        or receipt.get("corrected_launch_manifest_sha256")
        != corrected_launch["launch_manifest_sha256"]
    ):
        raise IntegrityError("binding receipt corrected bundle changed")
    corrected_frozen = read_json(corrected_bundle_dir / "frozen_manifest.json")
    if corrected_frozen != audited["derived_frozen_manifest"]:
        raise IntegrityError("binding receipt corrected frozen manifest changed")
    corrected = _conversion_bundle(
        observations_path=Path(inputs["corrected_observations_path"]),
        conversion_audit_path=Path(inputs["corrected_conversion_audit_path"]),
        launch_manifest=corrected_launch,
        frozen_manifest=corrected_frozen,
    )
    corrected_audit = read_json(Path(inputs["corrected_conversion_audit_path"]))
    if not isinstance(corrected_audit, dict) or receipt.get(
        "corrected_conversion_audit_sha256"
    ) != corrected_audit.get("conversion_audit_sha256"):
        raise IntegrityError("binding receipt corrected conversion changed")

    observations = load_observations(observations_path)
    indexed: dict[str, dict[str, Any]] = {}
    rows = {row["run_id"]: row for row in matrix["runs"]}
    contracts = protocol["evaluations"][evaluation]["cell_contracts"]
    reuse_launches = {launch["run_id"]: launch for launch in reuse_launch["launches"]}
    corrected_launches = {
        launch["run_id"]: launch for launch in corrected_launch["launches"]
    }
    for observation in observations:
        if not isinstance(observation, dict):
            raise IntegrityError("bound observation is not an object")
        run_id = str(observation.get("run_id", ""))
        if run_id in indexed:
            raise IntegrityError(f"duplicate bound observation: {run_id}")
        indexed[run_id] = observation
    if set(indexed) != set(rows):
        raise IntegrityError("bound observations differ from target matrix")
    for run_id, observation in indexed.items():
        row = rows[run_id]
        contract = contracts[run_id]
        if set(observation) != {
            "run_id",
            "arm",
            "preservation_strict",
            "strict_binary",
            "valid_transaction",
            "compiler_pass",
            "infrastructure_valid",
            "audit",
        }:
            raise IntegrityError(f"{run_id} bound observation fields are not exact")
        if (
            observation.get("arm") != row["arm"]
            or observation.get("infrastructure_valid") is not True
        ):
            raise IntegrityError(f"{run_id} bound arm/validity differs from matrix")
        observation["preservation_strict"] = _bounded(
            observation.get("preservation_strict"),
            label=f"{run_id}.preservation_strict",
        )
        binary = _bounded(
            observation.get("strict_binary"), label=f"{run_id}.strict_binary"
        )
        if binary not in {0.0, 1.0}:
            raise IntegrityError(f"{run_id}.strict_binary is not binary")
        observation["strict_binary"] = binary
        if not isinstance(observation.get("valid_transaction"), bool) or not isinstance(
            observation.get("compiler_pass"), bool
        ):
            raise IntegrityError(f"{run_id} bound booleans are malformed")
        evidence = observation.get("audit")
        if not isinstance(evidence, dict):
            raise IntegrityError(f"{run_id} bound audit is absent")
        if contract["evidence_kind"] == "reuse":
            evidence_run_id = contract["source_run_id"]
            source_observation = reuse.get(evidence_run_id)
            source_launch = reuse_launches.get(evidence_run_id)
            evidence_launch_sha256 = reuse_launch["launch_manifest_sha256"]
        else:
            evidence_run_id = run_id
            source_observation = corrected.get(evidence_run_id)
            source_launch = corrected_launches.get(evidence_run_id)
            evidence_launch_sha256 = corrected_launch["launch_manifest_sha256"]
        if source_observation is None or source_launch is None:
            raise IntegrityError(f"{run_id} bound source evidence is absent")
        source_spec = read_json(Path(source_launch["config"]))
        if not isinstance(source_spec, dict):
            raise IntegrityError(f"{run_id} bound source launch spec is malformed")
        if (
            sha256_bytes(canonical_bytes(source_spec.get("task")))
            != contract["task_sha256"]
            or sha256_bytes(canonical_bytes(_causal_spec(source_spec)))
            != contract["causal_config_sha256"]
            or source_spec.get("block_seed") != contract["block_seed"]
        ):
            raise IntegrityError(f"{run_id} bound source task/config/seed changed")
        for field in (
            "preservation_strict",
            "strict_binary",
            "valid_transaction",
            "compiler_pass",
        ):
            if observation.get(field) != source_observation.get(field):
                raise IntegrityError(f"{run_id} bound {field} differs from conversion")
        source_audit = source_observation.get("audit")
        if (
            not isinstance(source_audit, dict)
            or source_audit.get("runtime_drift") is not False
            or source_audit.get("harness_sha256") != contract["harness_sha256"]
            or source_audit.get("arm_weight_sha256") != contract["arm_weight_sha256"]
        ):
            raise IntegrityError(f"{run_id} bound evidence identity/drift changed")
        binding_core = {
            "protocol_sha256": protocol["protocol_sha256"],
            "target_matrix_sha256": matrix["matrix_sha256"],
            "target_row_sha256": contract["target_row_sha256"],
            "cell_contract_sha256": contract["cell_contract_sha256"],
            "evidence_kind": contract["evidence_kind"],
            "evidence_run_id": evidence_run_id,
            "evidence_launch_manifest_sha256": evidence_launch_sha256,
            "evidence_launch_config_sha256": source_launch["config_sha256"],
            "task_sha256": contract["task_sha256"],
            "harness_sha256": contract["harness_sha256"],
            "causal_config_sha256": contract["causal_config_sha256"],
            "arm_weight_sha256": contract["arm_weight_sha256"],
            "summary_sha256": source_audit["summary_sha256"],
            "trajectory_sha256": source_audit["trajectory_sha256"],
            "safety_backstop_bound": source_audit["safety_backstop_bound"],
            "lossy_context_bound": source_audit["lossy_context_bound"],
            "runtime_drift": False,
            "recorded_scores_match_fresh": source_audit["recorded_scores_match_fresh"],
        }
        expected_evidence = {
            **binding_core,
            "binding_sha256": sha256_bytes(canonical_bytes(binding_core)),
        }
        if evidence != expected_evidence:
            raise IntegrityError(f"{run_id} bound audit differs from source evidence")
    return matrix, observations, indexed


def analyze_development_gate(
    *,
    preparation_dir: Path,
    repository_root: Path,
    observations_path: Path,
    binding_receipt_path: Path,
    manifest_validator: ManifestValidator | None = None,
) -> dict[str, Any]:
    """Apply only the preregistered laptop criteria; no held-out input is accepted."""

    audited = audit_corrected_preparation(
        preparation_dir=preparation_dir,
        repository_root=repository_root,
        manifest_validator=manifest_validator,
    )
    matrix, observations, indexed = _bound_observations(
        audited=audited,
        evaluation=DEVELOPMENT_EVALUATION,
        observations_path=observations_path,
        binding_receipt_path=binding_receipt_path,
    )
    if any(
        row.get("scenario") != "laptop" or row.get("held_out") for row in matrix["runs"]
    ):
        raise IntegrityError("development report received non-laptop or held-out rows")
    evaluation = audited["protocol"]["evaluations"][DEVELOPMENT_EVALUATION]
    left_arm = evaluation["left_arm"]
    right_arm = evaluation["right_arm"]
    combined_pairs = _paired_rows(
        matrix,
        indexed,
        left_arm=left_arm,
        right_arm=right_arm,
        predicate=lambda row: row["condition"] == "combined",
        metric="strict_binary",
    )
    clean_pairs = _paired_rows(
        matrix,
        indexed,
        left_arm=left_arm,
        right_arm=right_arm,
        predicate=lambda row: row["condition"] == "clean",
        metric="strict_binary",
    )
    if len(combined_pairs) != 8 or len(clean_pairs) != 4:
        raise IntegrityError("development report is not based on n=2/n=1 per variant")
    combined_delta = mean(pair["difference"] for pair in combined_pairs)
    clean_delta = mean(pair["difference"] for pair in clean_pairs)
    clean_regression = -clean_delta
    variant_deltas = {
        variant: mean(
            pair["difference"] for pair in combined_pairs if pair["variant"] == variant
        )
        for variant in sorted({pair["variant"] for pair in combined_pairs})
    }
    if set(variant_deltas) != set(audited["campaign"]["amazon"]["variants"]):
        raise IntegrityError("development report variant set changed")
    positive_variants = sum(value > 0 for value in variant_deltas.values())
    no_backstop = not any(
        observation["audit"]["safety_backstop_bound"] for observation in observations
    )
    criteria = evaluation["criteria"]
    if criteria != DEVELOPMENT_CRITERIA:
        raise IntegrityError("development report criteria changed")
    gates = {
        "combined_binary_hero_delta": combined_delta
        >= criteria["combined_binary_hero_delta_minimum"],
        "clean_binary_hero_regression": clean_regression
        <= criteria["clean_binary_hero_regression_maximum"],
        "positive_variant_breadth": positive_variants
        >= criteria["minimum_variants_with_positive_combined_binary_hero_delta"],
        "no_safety_backstop_bound": no_backstop
        and criteria["safety_backstop_bound_allowed"] is False,
    }
    binding_receipt = read_json(binding_receipt_path)
    core = {
        "schema": DEVELOPMENT_REPORT_SCHEMA,
        "campaign_id": audited["campaign"]["campaign_id"],
        "protocol_sha256": audited["protocol"]["protocol_sha256"],
        "matrix_sha256": matrix["matrix_sha256"],
        "corrected_exact_lora_manifest_sha256": audited["protocol"][
            "corrected_exact_lora"
        ]["manifest_sha256"],
        "binding_receipt_sha256": binding_receipt["binding_receipt_sha256"],
        "observations_sha256": sha256_file(observations_path),
        "evidence": {
            "observations_path": str(observations_path.resolve()),
            "binding_receipt_path": str(binding_receipt_path.resolve()),
        },
        "selection_inputs": {
            "scenario": "laptop",
            "heldout_rows": 0,
            "combined_pairs": 8,
            "clean_pairs": 4,
            "variants": sorted(variant_deltas),
        },
        "criteria": criteria,
        "rates": {
            "step20_combined_binary_hero": _arm_mean(
                matrix,
                indexed,
                arm=left_arm,
                condition="combined",
                metric="strict_binary",
            ),
            "corrected_combined_binary_hero": _arm_mean(
                matrix,
                indexed,
                arm=right_arm,
                condition="combined",
                metric="strict_binary",
            ),
            "combined_binary_hero_delta": combined_delta,
            "step20_clean_binary_hero": _arm_mean(
                matrix,
                indexed,
                arm=left_arm,
                condition="clean",
                metric="strict_binary",
            ),
            "corrected_clean_binary_hero": _arm_mean(
                matrix,
                indexed,
                arm=right_arm,
                condition="clean",
                metric="strict_binary",
            ),
            "clean_binary_hero_delta": clean_delta,
            "clean_binary_hero_regression": clean_regression,
        },
        "variant_combined_binary_hero_deltas": variant_deltas,
        "positive_variants": positive_variants,
        "safety_backstop_bound_runs": sum(
            observation["audit"]["safety_backstop_bound"]
            for observation in observations
        ),
        "gates": gates,
        "selected": all(gates.values()),
    }
    return {**core, "report_sha256": sha256_bytes(canonical_bytes(core))}


def analyze_corrected_final(
    *,
    preparation_dir: Path,
    repository_root: Path,
    observations_path: Path,
    binding_receipt_path: Path,
    gate_report_path: Path,
    manifest_validator: ManifestValidator | None = None,
) -> dict[str, Any]:
    """Analyze the gated 320-cell raw-versus-corrected final evaluation."""

    audited = audit_corrected_preparation(
        preparation_dir=preparation_dir,
        repository_root=repository_root,
        manifest_validator=manifest_validator,
    )
    protocol = audited["protocol"]
    gate_report = _successful_gate_report(
        gate_report_path,
        audited_preparation=audited,
        repository_root=repository_root,
        manifest_validator=manifest_validator,
    )
    matrix, observations, indexed = _bound_observations(
        audited=audited,
        evaluation=FINAL_EVALUATION,
        observations_path=observations_path,
        binding_receipt_path=binding_receipt_path,
    )
    evaluation = protocol["evaluations"][FINAL_EVALUATION]
    left_arm = evaluation["left_arm"]
    right_arm = evaluation["right_arm"]

    def combined(row: dict[str, Any]) -> bool:
        return row["condition"] == "combined"

    def heldout_combined(row: dict[str, Any]) -> bool:
        return row["condition"] == "combined" and bool(row["held_out"])

    pstar_pairs = _paired_rows(
        matrix,
        indexed,
        left_arm=left_arm,
        right_arm=right_arm,
        predicate=combined,
        metric="preservation_strict",
    )
    hero_pairs = _paired_rows(
        matrix,
        indexed,
        left_arm=left_arm,
        right_arm=right_arm,
        predicate=combined,
        metric="strict_binary",
    )
    heldout_pstar = _paired_rows(
        matrix,
        indexed,
        left_arm=left_arm,
        right_arm=right_arm,
        predicate=heldout_combined,
        metric="preservation_strict",
    )
    heldout_hero = _paired_rows(
        matrix,
        indexed,
        left_arm=left_arm,
        right_arm=right_arm,
        predicate=heldout_combined,
        metric="strict_binary",
    )
    if len(pstar_pairs) != 100 or len(heldout_pstar) != 80:
        raise IntegrityError(
            "corrected final pair counts differ from the 320-cell design"
        )
    success = audited["campaign"]["success"]
    alpha = float(success["alpha"])
    bootstrap_samples = int(success["bootstrap_samples"])
    permutation_samples = int(success["permutation_samples"])
    seed = int(audited["campaign"]["seed"])
    pstar_delta = mean(pair["difference"] for pair in pstar_pairs)
    hero_delta = mean(pair["difference"] for pair in hero_pairs)
    heldout_pstar_delta = mean(pair["difference"] for pair in heldout_pstar)
    heldout_hero_delta = mean(pair["difference"] for pair in heldout_hero)
    pstar_lower = hierarchical_bootstrap_lower_bound(
        pstar_pairs, samples=bootstrap_samples, alpha=alpha, seed=seed + 1
    )
    hero_lower = hierarchical_bootstrap_lower_bound(
        hero_pairs, samples=bootstrap_samples, alpha=alpha, seed=seed + 2
    )
    heldout_pstar_lower = hierarchical_bootstrap_lower_bound(
        heldout_pstar, samples=bootstrap_samples, alpha=alpha, seed=seed + 3
    )
    heldout_hero_lower = hierarchical_bootstrap_lower_bound(
        heldout_hero, samples=bootstrap_samples, alpha=alpha, seed=seed + 4
    )
    pstar_permutation = sign_randomization_pvalue(
        pstar_pairs, samples=permutation_samples, seed=seed + 5
    )
    mcnemar = exact_mcnemar_one_sided(hero_pairs)
    scenario_deltas = {
        scenario: mean(
            pair["difference"] for pair in heldout_hero if pair["scenario"] == scenario
        )
        for scenario in sorted({pair["scenario"] for pair in heldout_hero})
    }
    positive_scenarios = sum(value > 0 for value in scenario_deltas.values())
    clean_pstar_delta = _arm_mean(
        matrix,
        indexed,
        arm=right_arm,
        condition="clean",
        metric="preservation_strict",
    ) - _arm_mean(
        matrix,
        indexed,
        arm=left_arm,
        condition="clean",
        metric="preservation_strict",
    )
    clean_purchase_delta = _arm_mean(
        matrix,
        indexed,
        arm=right_arm,
        condition="clean",
        metric="valid_transaction",
    ) - _arm_mean(
        matrix,
        indexed,
        arm=left_arm,
        condition="clean",
        metric="valid_transaction",
    )
    audits = [observation["audit"] for observation in observations]
    confounds = {
        "harness_hash_identical": len({audit["harness_sha256"] for audit in audits})
        == 1,
        "matched_causal_config": all(
            audit["causal_config_sha256"]
            == evaluation["cell_contracts"][observation["run_id"]][
                "causal_config_sha256"
            ]
            for observation, audit in zip(observations, audits, strict=True)
        ),
        "no_safety_backstop_bound": not any(
            audit["safety_backstop_bound"] for audit in audits
        ),
        "no_lossy_context_bound": not any(
            audit["lossy_context_bound"] for audit in audits
        ),
        "no_runtime_drift": not any(audit["runtime_drift"] for audit in audits),
    }
    gates = {
        "minimum_hero_rate_improvement": hero_delta
        >= float(success["minimum_full_five_hero_rate_improvement"]),
        "hero_lower_bound_positive": hero_lower > 0,
        "hero_mcnemar_significant": mcnemar["pvalue_one_sided"] < alpha,
        "held_out_hero_lower_bound_positive": heldout_hero_lower > 0,
        "held_out_hero_scenario_breadth": positive_scenarios
        >= int(success["minimum_held_out_scenarios_with_positive_delta"]),
        "minimum_pstar_improvement": pstar_delta
        >= float(success["minimum_full_five_pstar_improvement"]),
        "pstar_lower_bound_positive": pstar_lower > 0,
        "pstar_permutation_significant": pstar_permutation < alpha,
        "clean_pstar_guard": clean_pstar_delta
        >= -float(success["maximum_clean_pstar_regression"]),
        "clean_valid_purchase_guard": clean_purchase_delta
        >= -float(success["maximum_clean_valid_purchase_regression"]),
        "confound_audit_green": all(confounds.values()),
    }
    binding_receipt = read_json(binding_receipt_path)
    core = {
        "schema": FINAL_REPORT_SCHEMA,
        "campaign_id": audited["campaign"]["campaign_id"],
        "protocol_sha256": protocol["protocol_sha256"],
        "matrix_sha256": matrix["matrix_sha256"],
        "corrected_exact_lora_manifest_sha256": protocol["corrected_exact_lora"][
            "manifest_sha256"
        ],
        "gate_report_sha256": gate_report["report_sha256"],
        "binding_receipt_sha256": binding_receipt["binding_receipt_sha256"],
        "observations_sha256": sha256_file(observations_path),
        "pair_counts": {
            "combined": len(pstar_pairs),
            "held_out_combined": len(heldout_pstar),
        },
        "headline": {
            "raw_hero_rate": _arm_mean(
                matrix,
                indexed,
                arm=left_arm,
                condition="combined",
                metric="strict_binary",
            ),
            "corrected_hero_rate": _arm_mean(
                matrix,
                indexed,
                arm=right_arm,
                condition="combined",
                metric="strict_binary",
            ),
            "hero_rate_difference": hero_delta,
            "hero_rate_one_sided_95_lower_bound": hero_lower,
            "mcnemar": mcnemar,
        },
        "mandatory_secondary": {
            "raw_mean_pstar": _arm_mean(
                matrix,
                indexed,
                arm=left_arm,
                condition="combined",
                metric="preservation_strict",
            ),
            "corrected_mean_pstar": _arm_mean(
                matrix,
                indexed,
                arm=right_arm,
                condition="combined",
                metric="preservation_strict",
            ),
            "pstar_difference": pstar_delta,
            "pstar_one_sided_95_lower_bound": pstar_lower,
            "pstar_sign_randomization_pvalue": pstar_permutation,
        },
        "held_out": {
            "hero_rate_difference": heldout_hero_delta,
            "hero_rate_one_sided_95_lower_bound": heldout_hero_lower,
            "scenario_hero_rate_differences": scenario_deltas,
            "positive_scenarios": positive_scenarios,
            "pstar_difference": heldout_pstar_delta,
            "pstar_one_sided_95_lower_bound": heldout_pstar_lower,
        },
        "clean_guards": {
            "pstar_difference": clean_pstar_delta,
            "valid_purchase_difference": clean_purchase_delta,
        },
        "confound_audit": confounds,
        "gates": gates,
        "success": all(gates.values()),
    }
    return {**core, "report_sha256": sha256_bytes(canonical_bytes(core))}


def render_corrected_markdown(report: dict[str, Any]) -> str:
    if report.get("schema") == DEVELOPMENT_REPORT_SCHEMA:
        rates = report["rates"]
        lines = [
            "# Corrected laptop development gate",
            "",
            f"Selection: **{'PASS' if report['selected'] else 'FAIL'}**",
            "",
            f"Combined binary hero delta: {rates['combined_binary_hero_delta']:.3f}.",
            f"Clean binary hero regression: {rates['clean_binary_hero_regression']:.3f}.",
            f"Positive variants: {report['positive_variants']}/4.",
            f"Safety-backstop-bound runs: {report['safety_backstop_bound_runs']}.",
            "",
            "## Gates",
            "",
        ]
    elif report.get("schema") == FINAL_REPORT_SCHEMA:
        lines = [
            "# Corrected raw-versus-trained Amazon-five final",
            "",
            f"Outcome: **{'SUCCESS' if report['success'] else 'NOT ESTABLISHED'}**",
            "",
            f"Combined binary hero delta: {report['headline']['hero_rate_difference']:.3f}.",
            (
                "Combined preservation-strict delta: "
                f"{report['mandatory_secondary']['pstar_difference']:.3f}."
            ),
            "",
            "## Gates",
            "",
        ]
    else:
        raise IntegrityError("unknown corrected report schema")
    for name, value in sorted(report["gates"].items()):
        lines.append(f"- {'PASS' if value else 'FAIL'}: {name.replace('_', ' ')}")
    lines.extend(
        [
            "",
            "## Provenance",
            "",
            f"- protocol: `{report['protocol_sha256']}`",
            f"- matrix: `{report['matrix_sha256']}`",
            (
                "- corrected exact-LoRA manifest: "
                f"`{report['corrected_exact_lora_manifest_sha256']}`"
            ),
            f"- observations: `{report['observations_sha256']}`",
        ]
    )
    return "\n".join(lines) + "\n"


def write_corrected_report(
    *, json_path: Path, markdown_path: Path, report: dict[str, Any]
) -> None:
    _self_hash(report, "report_sha256", label="corrected report")
    write_json_create_only(json_path, report)
    write_text_create_only(markdown_path, render_corrected_markdown(report))
