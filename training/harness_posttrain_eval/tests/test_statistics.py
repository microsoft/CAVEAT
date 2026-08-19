from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path

import pytest
from agentarena.benchmark import registry

from harness_posttrain_eval.common import (
    IntegrityError,
    canonical_bytes,
    sha256_bytes,
    sha256_file,
)
from harness_posttrain_eval.matrix import final_matrix
from harness_posttrain_eval.report import render_markdown
from harness_posttrain_eval.statistics import analyze_final


def _observations(matrix, *, bind_limit=False):
    rows = []
    for matrix_row in matrix["runs"]:
        arm = matrix_row["arm"]
        combined = matrix_row["condition"] == "combined"
        if combined and arm == "base":
            pstar = 0.20
            hero = 1.0 if matrix_row["repetition"] == 0 else 0.0
        elif combined:
            pstar = 0.80
            hero = 1.0
        else:
            pstar = 1.0
            hero = 1.0
        rows.append(
            {
                "run_id": matrix_row["run_id"],
                "arm": arm,
                "preservation_strict": pstar,
                "strict_binary": hero,
                "valid_transaction": True,
                "compiler_pass": arm != "base" or matrix_row["repetition"] % 2 == 0,
                "infrastructure_valid": True,
                "audit": {
                    "safety_backstop_bound": bind_limit and len(rows) == 0,
                    "lossy_context_bound": False,
                    "runtime_drift": False,
                },
            }
        )
    return rows


def _fast_config(config):
    config = copy.deepcopy(config)
    config["success"]["bootstrap_samples"] = 1000
    config["success"]["permutation_samples"] = 1000
    return config


def _digest_observations(rows: list[dict]) -> str:
    lines = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
    return sha256_bytes(lines.encode("utf-8"))


def _provenance_inputs(
    *, tmp_path: Path, config: dict, matrix: dict, observations: list[dict]
) -> dict:
    frozen_core = {
        "schema_version": 1,
        "campaign": config,
        "source_contract": {"harness_sha256": "a" * 64},
        "inference_contract": {
            "engine": "test",
            "temperature": 0,
            "container_image_digest": "sha256:" + "d" * 64,
        },
        "model_contract": {
            "base_weight_sha256": "b" * 64,
            "trained_weight_sha256": "c" * 64,
        },
    }
    frozen_manifest = {
        **frozen_core,
        "manifest_sha256": sha256_bytes(canonical_bytes(frozen_core)),
    }
    inference_sha = sha256_bytes(canonical_bytes(frozen_manifest["inference_contract"]))
    endpoint_sha = "e" * 64
    results_root = tmp_path / "results"
    launches = []
    launch_by_run = {}
    task_cache = {}
    for index, matrix_row in enumerate(matrix["runs"]):
        run_id = matrix_row["run_id"]
        result_dir = results_root / f"run-{index:04d}"
        audit_contract = {
            "frozen_manifest_sha256": frozen_manifest["manifest_sha256"],
            "harness_sha256": frozen_manifest["source_contract"]["harness_sha256"],
            "inference_contract_sha256": inference_sha,
            "matrix_sha256": matrix["matrix_sha256"],
            "limit_contract_sha256": "f" * 64,
            "endpoint_manifest_sha256": endpoint_sha,
        }
        task_key = (matrix_row["scenario"], matrix_row["variant"])
        if task_key not in task_cache:
            task_cache[task_key] = asdict(
                registry.benchmark_tasks(
                    matrix_row["scenario"], variants=[matrix_row["variant"]]
                )[0]
            )
        spec = {
            "run_id": run_id,
            "pair_id": matrix_row["pair_id"],
            "arm": matrix_row["arm"],
            "port": 20000 + index,
            "out_dir": str(result_dir),
            "env": "amazon",
            "scaffold": "browseruse-deliberative",
            "model": {"name": matrix_row["arm"]},
            "task": task_cache[task_key],
            "condition": matrix_row["condition"],
            "block_seed": matrix_row["block_seed"],
            "campaign_id": config["campaign_id"],
            "matrix_sha256": matrix["matrix_sha256"],
            "runtime_environment": {"TEST_RUN_ID": run_id},
            "audit_contract": audit_contract,
        }
        config_path = tmp_path / "configs" / f"run-{index:04d}.json"
        config_path.parent.mkdir(exist_ok=True)
        config_path.write_bytes(canonical_bytes(spec) + b"\n")
        launch = {
            "run_id": run_id,
            "pair_id": matrix_row["pair_id"],
            "arm": matrix_row["arm"],
            "port": 20000 + index,
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
            "environment": spec["runtime_environment"],
            "audit_contract": audit_contract,
        }
        launches.append(launch)
        launch_by_run[run_id] = launch
    launch_core = {
        "schema_version": 1,
        "campaign_id": config["campaign_id"],
        "matrix_sha256": matrix["matrix_sha256"],
        "endpoint_manifest_sha256": endpoint_sha,
        "base_port": 20000,
        "results_root": str(results_root),
        "launches": launches,
    }
    launch_manifest = {
        **launch_core,
        "launch_manifest_sha256": sha256_bytes(canonical_bytes(launch_core)),
    }

    for observation in observations:
        arm = observation["arm"]
        observation["audit"].update(
            {
                "harness_sha256": frozen_manifest["source_contract"]["harness_sha256"],
                "inference_contract_sha256": inference_sha,
                "frozen_manifest_sha256": frozen_manifest["manifest_sha256"],
                "matrix_sha256": matrix["matrix_sha256"],
                "endpoint_manifest_sha256": endpoint_sha,
                "launch_manifest_sha256": launch_manifest["launch_manifest_sha256"],
                "launch_config_sha256": launch_by_run[observation["run_id"]][
                    "config_sha256"
                ],
                "summary_sha256": "1" * 64,
                "trajectory_sha256": "2" * 64,
                "arm_weight_sha256": frozen_manifest["model_contract"][
                    "base_weight_sha256" if arm == "base" else "trained_weight_sha256"
                ],
                "recorded_scores_match_fresh": True,
            }
        )
    observations_sha = _digest_observations(observations)
    conversion_core = {
        "schema_version": 1,
        "campaign_id": config["campaign_id"],
        "launch_manifest_sha256": launch_manifest["launch_manifest_sha256"],
        "frozen_manifest_sha256": frozen_manifest["manifest_sha256"],
        "frozen_campaign_contract_sha256": sha256_bytes(canonical_bytes(config)),
        "matrix_sha256": matrix["matrix_sha256"],
        "endpoint_manifest_sha256": endpoint_sha,
        "observations_sha256": observations_sha,
        "expected_runs": len(matrix["runs"]),
        "observation_rows": len(matrix["runs"]),
        "infrastructure_invalid_runs": 0,
        "complete": True,
        "runs": [
            {
                "run_id": row["run_id"],
                "arm": row["arm"],
                "infrastructure_valid": True,
                "classification": "behavioral_purchase",
            }
            for row in matrix["runs"]
        ],
    }
    conversion_audit = {
        **conversion_core,
        "conversion_audit_sha256": sha256_bytes(canonical_bytes(conversion_core)),
    }
    return {
        "frozen_manifest": frozen_manifest,
        "launch_manifest": launch_manifest,
        "conversion_audit": conversion_audit,
        "observations_sha256": observations_sha,
    }


def test_strong_matched_improvement_passes(config, tmp_path):
    config = _fast_config(config)
    matrix = final_matrix(config, selected_arm="trained")
    observations = _observations(matrix)
    provenance = _provenance_inputs(
        tmp_path=tmp_path, config=config, matrix=matrix, observations=observations
    )
    report = analyze_final(
        matrix,
        observations,
        config,
        selected_arm="trained",
        **provenance,
    )
    assert report["success"] is True
    assert report["headline"]["hero_rate_difference"] > 0.15
    assert report["mandatory_secondary"]["pstar_difference"] == pytest.approx(0.60)
    assert report["held_out"]["positive_scenarios"] == 4
    assert report["compiler_decomposition"]["base"]["pass_rate"] < 1.0
    assert report["provenance"]["observations_sha256"] == provenance[
        "observations_sha256"
    ]
    markdown = render_markdown(report)
    assert markdown.index("Optimal-product selection") < markdown.index(
        "Mandatory secondary: preservation strict"
    )


def test_binding_backstop_fails_claim(config, tmp_path):
    config = _fast_config(config)
    matrix = final_matrix(config, selected_arm="trained")
    observations = _observations(matrix, bind_limit=True)
    provenance = _provenance_inputs(
        tmp_path=tmp_path, config=config, matrix=matrix, observations=observations
    )
    report = analyze_final(
        matrix,
        observations,
        config,
        selected_arm="trained",
        **provenance,
    )
    assert report["success"] is False
    assert report["gates"]["confound_audit_green"] is False


def test_missing_run_fails_closed(config, tmp_path):
    config = _fast_config(config)
    matrix = final_matrix(config, selected_arm="trained")
    observations = _observations(matrix)
    provenance = _provenance_inputs(
        tmp_path=tmp_path, config=config, matrix=matrix, observations=observations
    )
    observations.pop()
    with pytest.raises(IntegrityError, match="observation/matrix mismatch"):
        analyze_final(
            matrix,
            observations,
            config,
            selected_arm="trained",
            **provenance,
        )


def test_mutated_analysis_config_fails_closed(config, tmp_path):
    config = _fast_config(config)
    matrix = final_matrix(config, selected_arm="trained")
    observations = _observations(matrix)
    provenance = _provenance_inputs(
        tmp_path=tmp_path, config=config, matrix=matrix, observations=observations
    )
    mutated = copy.deepcopy(config)
    mutated["success"]["minimum_full_five_hero_rate_improvement"] = 0.0
    with pytest.raises(IntegrityError, match="analysis config differs"):
        analyze_final(
            matrix,
            observations,
            mutated,
            selected_arm="trained",
            **provenance,
        )


def test_observation_provenance_drift_fails_closed(config, tmp_path):
    config = _fast_config(config)
    matrix = final_matrix(config, selected_arm="trained")
    observations = _observations(matrix)
    provenance = _provenance_inputs(
        tmp_path=tmp_path, config=config, matrix=matrix, observations=observations
    )
    observations[0]["audit"]["matrix_sha256"] = "0" * 64
    with pytest.raises(IntegrityError, match="differs from frozen provenance"):
        analyze_final(
            matrix,
            observations,
            config,
            selected_arm="trained",
            **provenance,
        )


def test_observation_file_hash_mismatch_fails_closed(config, tmp_path):
    config = _fast_config(config)
    matrix = final_matrix(config, selected_arm="trained")
    observations = _observations(matrix)
    provenance = _provenance_inputs(
        tmp_path=tmp_path, config=config, matrix=matrix, observations=observations
    )
    provenance["observations_sha256"] = "0" * 64
    with pytest.raises(IntegrityError, match="conversion audit observations_sha256"):
        analyze_final(
            matrix,
            observations,
            config,
            selected_arm="trained",
            **provenance,
        )


def test_launch_task_content_must_match_frozen_benchmark(config, tmp_path):
    config = _fast_config(config)
    matrix = final_matrix(config, selected_arm="trained")
    observations = _observations(matrix)
    provenance = _provenance_inputs(
        tmp_path=tmp_path, config=config, matrix=matrix, observations=observations
    )
    launch = provenance["launch_manifest"]["launches"][0]
    config_path = Path(launch["config"])
    spec = json.loads(config_path.read_text(encoding="utf-8"))
    spec["task"]["instruction"] += " Hidden mutation."
    config_path.write_bytes(canonical_bytes(spec) + b"\n")
    launch["config_sha256"] = sha256_file(config_path)
    core = {
        key: value
        for key, value in provenance["launch_manifest"].items()
        if key != "launch_manifest_sha256"
    }
    provenance["launch_manifest"]["launch_manifest_sha256"] = sha256_bytes(
        canonical_bytes(core)
    )
    # Rebind the downstream self-hashed audit so the failure is specifically the
    # launch task's mismatch with the locked benchmark, not a stale outer hash.
    conversion = provenance["conversion_audit"]
    conversion["launch_manifest_sha256"] = provenance["launch_manifest"][
        "launch_manifest_sha256"
    ]
    conversion_core = {
        key: value for key, value in conversion.items() if key != "conversion_audit_sha256"
    }
    conversion["conversion_audit_sha256"] = sha256_bytes(
        canonical_bytes(conversion_core)
    )
    with pytest.raises(IntegrityError, match="launch task differs"):
        analyze_final(
            matrix,
            observations,
            config,
            selected_arm="trained",
            **provenance,
        )
