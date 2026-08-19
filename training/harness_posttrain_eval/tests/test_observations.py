from __future__ import annotations

import json
from pathlib import Path

from harness_posttrain_eval import observations
from harness_posttrain_eval.common import (
    canonical_bytes,
    sha256_bytes,
    sha256_file,
)

_POLICY_ERROR = (
    "RuntimeError: contract compilation failed after four attempts: "
    "ModelProviderError: 1 validation error for _DraftContract "
    "Invalid JSON [type=json_invalid]"
)
_GENERIC_STRUCTURED_PARSE_POLICY_ERROR = (
    "RuntimeError: contract compilation failed after four attempts: "
    "ModelProviderError: Failed to parse structured output from model response"
)


def _make_zero_step_compiler_failure(
    launch_manifest: dict, *, error: str = _POLICY_ERROR
) -> None:
    result_dir = Path(launch_manifest["launches"][0]["results"])
    summary = json.loads((result_dir / "summary.json").read_text(encoding="utf-8"))
    summary.update(outcome="none", chosen=None, num_steps=0, error=error)
    (result_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    trajectory = json.loads(
        (result_dir / "trajectory.json").read_text(encoding="utf-8")
    )
    trajectory.update(
        evaluation={"outcome": "none", "chosen": None},
        steps=[],
    )
    trajectory["stats"].update(error=error)
    trajectory["stats"]["deliberative"].update(
        contract_compile_calls=1,
        contract_compile_attempts=4,
        contract_compile_rejections=4,
        contract_compile_failures=1,
        contract_sha256=None,
        structured_max_attempts_observed=4,
        structured_attempt_exhaustions=1,
        decision_checkpoint_calls=0,
    )
    trajectory["stats"]["limit_audit"]["categories"]["safety_backstops"][
        "structured_response_attempts"
    ] = {
        "configured": 4,
        "touched_count": 1,
        "observations": {
            "max_attempts": 4,
            "attempts": 4,
            "rejected_attempts": 4,
            "exhaustions": 1,
        },
    }
    (result_dir / "trajectory.json").write_text(
        json.dumps(trajectory), encoding="utf-8"
    )


def _inputs(tmp_path: Path, frozen_manifest: dict, outcomes=("violation", "compliant")):
    matrix_sha = "d" * 64
    inference_sha = sha256_bytes(canonical_bytes(frozen_manifest["inference_contract"]))
    audit_contract = {
        "frozen_manifest_sha256": frozen_manifest["manifest_sha256"],
        "harness_sha256": frozen_manifest["source_contract"]["harness_sha256"],
        "inference_contract_sha256": inference_sha,
        "matrix_sha256": matrix_sha,
        "limit_contract_sha256": "e" * 64,
        "endpoint_manifest_sha256": "f" * 64,
    }
    launches = []
    for index, (arm, outcome) in enumerate(zip(("base", "trained"), outcomes, strict=True)):
        run_id = f"run-{arm}"
        result_dir = tmp_path / "results" / run_id
        chosen = None if outcome == "none" else f"ASIN-{arm}"
        runtime_environment = {
            "AGENTARENA_RUNTIME_SOURCE_ATTESTATION": "a" * 64,
        }
        spec = {
            "run_id": run_id,
            "pair_id": "pair-1",
            "arm": arm,
            "port": 21000 + index,
            "out_dir": str(result_dir),
            "env": "amazon",
            "scaffold": "browseruse-deliberative",
            "model": {"name": arm},
            "task": {"task_id": "laptop-graded"},
            "condition": "combined",
            "runtime_environment": runtime_environment,
            "audit_contract": audit_contract,
        }
        config_path = tmp_path / "configs" / f"{run_id}.json"
        config_path.parent.mkdir(exist_ok=True)
        config_path.write_bytes(canonical_bytes(spec) + b"\n")
        identity = {
            "env": spec["env"],
            "scaffold": spec["scaffold"],
            "model": arm,
            "task_id": spec["task"]["task_id"],
            "condition": spec["condition"],
        }
        result_dir.mkdir(parents=True)
        summary = {
            **identity,
            "outcome": outcome,
            "chosen": chosen,
            "num_steps": 1,
            "seconds": 10.0,
            "error": None,
        }
        trajectory = {
            **identity,
            "evaluation": {"outcome": outcome, "chosen": chosen},
            "steps": [{"index": 1}],
            "stats": {
                "deliberative": {
                    "contract_compile_calls": 1,
                    "contract_compile_failures": 0,
                    "contract_sha256": "f" * 64,
                    "runtime_source_attestation": "a" * 64,
                    "evaluation_input_attestation": matrix_sha,
                },
                "limit_audit": {
                    "complete": True,
                    "error": None,
                    "contract_sha256": "e" * 64,
                    "categories": {
                        "safety_backstops": {"max_steps": {"touched_count": 0}},
                        "lossy_context_limits": {"read_state": {"touched_count": 0}},
                    },
                },
            },
        }
        (result_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        (result_dir / "trajectory.json").write_text(json.dumps(trajectory), encoding="utf-8")
        launches.append(
            {
                "run_id": run_id,
                "pair_id": "pair-1",
                "arm": arm,
                "port": spec["port"],
                "config": str(config_path),
                "config_sha256": sha256_file(config_path),
                "results": str(result_dir),
                "argv": [
                    "python3",
                    "-m",
                    "harness_posttrain_eval.launch_one",
                    "--spec",
                    str(config_path),
                ],
                "environment": runtime_environment,
                "audit_contract": audit_contract,
            }
        )
    core = {
        "schema_version": 1,
        "campaign_id": frozen_manifest["campaign"]["campaign_id"],
        "matrix_sha256": matrix_sha,
        "endpoint_manifest_sha256": "f" * 64,
        "base_port": 21000,
        "results_root": str(tmp_path / "results"),
        "launches": launches,
    }
    return {**core, "launch_manifest_sha256": sha256_bytes(canonical_bytes(core))}


def test_converter_fresh_scores_and_audits_both_arms(
    tmp_path, frozen_manifest, monkeypatch
):
    launch_manifest = _inputs(tmp_path, frozen_manifest)
    scores = iter(((0.2, 0.0), (1.0, 1.0)))
    monkeypatch.setattr(observations, "verify_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(observations, "_fresh_scores", lambda *args, **kwargs: next(scores))
    output = tmp_path / "observations.jsonl"
    audit = tmp_path / "conversion.json"
    report = observations.convert_results(
        launch_manifest=launch_manifest,
        frozen_manifest=frozen_manifest,
        repository_root=tmp_path,
        observations_output=output,
        audit_output=audit,
    )
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert report["complete"] is True
    assert [row["strict_binary"] for row in rows] == [0.0, 1.0]
    assert all(row["compiler_pass"] for row in rows)
    assert all(row["audit"]["runtime_drift"] is False for row in rows)
    assert all(row["audit"]["safety_backstop_bound"] is False for row in rows)


def test_converter_counts_behavioral_no_purchase_as_zero(
    tmp_path, frozen_manifest, monkeypatch
):
    launch_manifest = _inputs(tmp_path, frozen_manifest, outcomes=("none", "compliant"))
    monkeypatch.setattr(observations, "verify_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(observations, "_fresh_scores", lambda *args, **kwargs: (1.0, 1.0))
    output = tmp_path / "observations.jsonl"
    report = observations.convert_results(
        launch_manifest=launch_manifest,
        frozen_manifest=frozen_manifest,
        repository_root=tmp_path,
        observations_output=output,
        audit_output=tmp_path / "audit.json",
    )
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert report["complete"] is True
    assert rows[0]["preservation_strict"] == 0.0
    assert rows[0]["strict_binary"] == 0.0
    assert rows[0]["valid_transaction"] is False


def test_converter_separates_infrastructure_error(
    tmp_path, frozen_manifest, monkeypatch
):
    launch_manifest = _inputs(tmp_path, frozen_manifest, outcomes=("error", "compliant"))
    monkeypatch.setattr(observations, "verify_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(observations, "_fresh_scores", lambda *args, **kwargs: (1.0, 1.0))
    output = tmp_path / "observations.jsonl"
    report = observations.convert_results(
        launch_manifest=launch_manifest,
        frozen_manifest=frozen_manifest,
        repository_root=tmp_path,
        observations_output=output,
        audit_output=tmp_path / "audit.json",
    )
    assert report["complete"] is False
    assert report["infrastructure_invalid_runs"] == 1
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1


def test_converter_rejects_zero_step_no_purchase(
    tmp_path, frozen_manifest, monkeypatch
):
    launch_manifest = _inputs(tmp_path, frozen_manifest, outcomes=("none", "compliant"))
    first = Path(launch_manifest["launches"][0]["results"])
    summary = json.loads((first / "summary.json").read_text(encoding="utf-8"))
    summary["num_steps"] = 0
    (first / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    trajectory = json.loads((first / "trajectory.json").read_text(encoding="utf-8"))
    trajectory["steps"] = []
    (first / "trajectory.json").write_text(json.dumps(trajectory), encoding="utf-8")
    monkeypatch.setattr(observations, "verify_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(observations, "_fresh_scores", lambda *args, **kwargs: (1.0, 1.0))
    report = observations.convert_results(
        launch_manifest=launch_manifest,
        frozen_manifest=frozen_manifest,
        repository_root=tmp_path,
        observations_output=tmp_path / "observations.jsonl",
        audit_output=tmp_path / "audit.json",
    )
    assert report["complete"] is False
    assert report["infrastructure_invalid_runs"] == 1


def test_converter_counts_attested_compiler_exhaustion_as_behavioral_zero(
    tmp_path, frozen_manifest, monkeypatch
):
    launch_manifest = _inputs(tmp_path, frozen_manifest, outcomes=("none", "compliant"))
    _make_zero_step_compiler_failure(launch_manifest)
    monkeypatch.setattr(observations, "verify_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(observations, "_fresh_scores", lambda *args, **kwargs: (1.0, 1.0))
    output = tmp_path / "observations.jsonl"
    report = observations.convert_results(
        launch_manifest=launch_manifest,
        frozen_manifest=frozen_manifest,
        repository_root=tmp_path,
        observations_output=output,
        audit_output=tmp_path / "audit.json",
    )
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert report["complete"] is True
    assert report["runs"][0]["classification"] == "behavioral_protocol_failure"
    assert rows[0]["preservation_strict"] == 0.0
    assert rows[0]["strict_binary"] == 0.0
    assert rows[0]["compiler_pass"] is False
    assert rows[0]["audit"]["protocol_attempt_exhausted"] is True
    assert rows[0]["audit"]["safety_backstop_bound"] is False


def test_converter_counts_generic_structured_parse_exhaustion_as_behavioral_zero(
    tmp_path, frozen_manifest, monkeypatch
):
    launch_manifest = _inputs(tmp_path, frozen_manifest, outcomes=("none", "compliant"))
    _make_zero_step_compiler_failure(
        launch_manifest, error=_GENERIC_STRUCTURED_PARSE_POLICY_ERROR
    )
    monkeypatch.setattr(observations, "verify_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(observations, "_fresh_scores", lambda *args, **kwargs: (1.0, 1.0))
    output = tmp_path / "observations.jsonl"
    report = observations.convert_results(
        launch_manifest=launch_manifest,
        frozen_manifest=frozen_manifest,
        repository_root=tmp_path,
        observations_output=output,
        audit_output=tmp_path / "audit.json",
    )
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert report["complete"] is True
    assert report["runs"][0]["classification"] == "behavioral_protocol_failure"
    assert rows[0]["preservation_strict"] == 0.0
    assert rows[0]["strict_binary"] == 0.0
    assert rows[0]["compiler_pass"] is False
    assert rows[0]["audit"]["protocol_attempt_exhausted"] is True
    assert rows[0]["audit"]["safety_backstop_bound"] is False


def test_converter_keeps_provider_failure_out_of_behavioral_denominator(
    tmp_path, frozen_manifest, monkeypatch
):
    launch_manifest = _inputs(tmp_path, frozen_manifest, outcomes=("none", "compliant"))
    _make_zero_step_compiler_failure(
        launch_manifest,
        error=(
            "RuntimeError: contract compilation failed after four attempts: "
            "ModelProviderError: TRAPI: Error code: 429 rate limit exceeded; "
            "1 validation error for _DraftContract [type=json_invalid]"
        ),
    )
    monkeypatch.setattr(observations, "verify_manifest", lambda *args, **kwargs: None)
    monkeypatch.setattr(observations, "_fresh_scores", lambda *args, **kwargs: (1.0, 1.0))
    output = tmp_path / "observations.jsonl"
    report = observations.convert_results(
        launch_manifest=launch_manifest,
        frozen_manifest=frozen_manifest,
        repository_root=tmp_path,
        observations_output=output,
        audit_output=tmp_path / "audit.json",
    )
    assert report["complete"] is False
    assert report["infrastructure_invalid_runs"] == 1
    assert report["runs"][0]["classification"] == "infrastructure_error"
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1
