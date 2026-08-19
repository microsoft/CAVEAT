from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

import pytest

from harness_posttrain_eval.batch import audit_launch_manifest
from harness_posttrain_eval.common import (
    IntegrityError,
    canonical_bytes,
    sha256_bytes,
    sha256_file,
)
from harness_posttrain_eval.restart_recovery import (
    CLASSIFIER_REASON,
    PROFILE_MARKER,
    RESTART_ERROR,
    recover_restart_artifacts,
)


def _fixture(tmp_path: Path, *, bad_marker: bool = False) -> dict[str, Path]:
    results_root = (tmp_path / "live-results").resolve()
    configs = (tmp_path / "configs").resolve()
    state_dir = (tmp_path / "state").resolve()
    results_root.mkdir()
    configs.mkdir()
    state_dir.mkdir()
    (state_dir / "executor.lock").touch()
    launches = []
    statuses = {}
    for index, arm in enumerate(("base", "trained")):
        run_id = f"final::laptop::graded::combined::r0{index}::{arm}"
        result = results_root / f"r0{index}-{arm}"
        config = configs / f"{index}.json"
        spec = {
            "run_id": run_id,
            "pair_id": f"pair-{index}",
            "arm": arm,
            "port": 24000 + index,
            "out_dir": str(result),
            "runtime_environment": {"TEST": run_id},
            "audit_contract": {
                "matrix_sha256": "b" * 64,
                "endpoint_manifest_sha256": "c" * 64,
            },
            "env": "amazon",
            "scaffold": "browseruse-deliberative",
            "model": f"model-{arm}",
            "task": {"task_id": "laptop-graded"},
            "condition": "combined",
        }
        config.write_bytes(canonical_bytes(spec) + b"\n")
        launches.append(
            {
                "argv": [
                    "python",
                    "-m",
                    "harness_posttrain_eval.launch_one",
                    "--spec",
                    str(config),
                ],
                "arm": arm,
                "audit_contract": spec["audit_contract"],
                "config": str(config),
                "config_sha256": sha256_file(config),
                "environment": spec["runtime_environment"],
                "pair_id": spec["pair_id"],
                "port": spec["port"],
                "results": str(result),
                "run_id": run_id,
            }
        )
        result.mkdir()
        evaluate_store = result / "_work" / "evaluate_results"
        evaluate_store.mkdir(parents=True)
        os.utime(evaluate_store, ns=(1_000_000_000, 1_000_000_000))
        summary = {
            "schema": 2,
            "env": spec["env"],
            "scaffold": spec["scaffold"],
            "model": spec["model"],
            "task_id": spec["task"]["task_id"],
            "condition": spec["condition"],
            "num_steps": 0,
            "outcome": "none",
            "success": False,
            "took_bait": False,
            "chosen": None,
            "chosen_label": None,
            "seconds": 0.0,
            "error": RESTART_ERROR,
        }
        trajectory = {
            "schema": 2,
            "env": spec["env"],
            "scaffold": spec["scaffold"],
            "model": spec["model"],
            "task_id": spec["task"]["task_id"],
            "condition": spec["condition"],
            "steps": [],
            "evaluation": {
                "outcome": "none",
                "chosen": None,
                "chosen_label": None,
                "success": False,
                "took_bait": False,
            },
            "stats": {
                "num_steps": 0,
                "decision_steps": 0,
                "tool_actions": 0,
                "seconds": 0.0,
                "error": RESTART_ERROR,
            },
        }
        (result / "summary.json").write_bytes(canonical_bytes(summary) + b"\n")
        (result / "trajectory.json").write_bytes(canonical_bytes(trajectory) + b"\n")
        markers = 1 if bad_marker and index == 0 else 2
        (result / "run.log").write_bytes((PROFILE_MARKER + b"\n") * markers)
        statuses[run_id] = {
            "arm": arm,
            "exit_code": 0,
            "reason": CLASSIFIER_REASON,
            "state": "failed",
        }
    core = {
        "schema_version": 1,
        "campaign_id": "test",
        "matrix_sha256": "b" * 64,
        "endpoint_manifest_sha256": "c" * 64,
        "base_port": 24000,
        "results_root": str(results_root),
        "launches": launches,
    }
    manifest = {**core, "launch_manifest_sha256": sha256_bytes(canonical_bytes(core))}
    manifest_path = tmp_path / "launch_manifest.json"
    manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")
    status = {
        "schema_version": 1,
        "launch_manifest_sha256": manifest["launch_manifest_sha256"],
        "counts": {"failed": 2},
        "runs": statuses,
    }
    (state_dir / "batch_status.json").write_bytes(canonical_bytes(status) + b"\n")
    return {
        "archive": tmp_path / "archive",
        "manifest": manifest_path,
        "results": results_root,
        "retry": tmp_path / "retry.json",
        "state": state_dir,
    }


def _recover(paths: dict[str, Path]) -> dict:
    return recover_restart_artifacts(
        launch_manifest_path=paths["manifest"],
        results_root=paths["results"],
        executor_state_dir=paths["state"],
        archive_root=paths["archive"],
        retry_manifest_path=paths["retry"],
        expected_count=2,
    )


def test_recovery_archives_exact_artifacts_and_preserves_launch_specs(tmp_path):
    paths = _fixture(tmp_path)
    original = json.loads(paths["manifest"].read_text(encoding="utf-8"))

    receipt = _recover(paths)

    retry = json.loads(paths["retry"].read_text(encoding="utf-8"))
    assert audit_launch_manifest(retry) == {
        "arms": ["base", "trained"],
        "runs": 2,
        "valid": True,
    }
    assert retry["launches"] == original["launches"]
    assert receipt["retry_manifest_sha256"] == retry["launch_manifest_sha256"]
    assert len(receipt["moved"]) == 2
    for launch in original["launches"]:
        assert not Path(launch["results"]).exists()
        archived = (
            paths["archive"]
            / "results"
            / Path(launch["results"]).relative_to(paths["results"])
        )
        assert archived.is_dir()
        assert (archived / "_work" / "evaluate_results").is_dir()
    assert (paths["archive"] / "recovery_plan.json").is_file()
    assert (paths["archive"] / "recovery_receipt.json").is_file()


def test_recovery_refuses_signature_drift_before_moving_anything(tmp_path):
    paths = _fixture(tmp_path, bad_marker=True)
    sources = list(paths["results"].iterdir())

    with pytest.raises(IntegrityError, match="exactly two launch-attempt markers"):
        _recover(paths)

    assert all(source.is_dir() for source in sources)
    assert not paths["archive"].exists()
    assert not paths["retry"].exists()


def test_recovery_refuses_while_executor_lock_is_held(tmp_path):
    paths = _fixture(tmp_path)
    with (paths["state"] / "executor.lock").open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(IntegrityError, match="still owns the executor lock"):
            _recover(paths)

    assert len(list(paths["results"].iterdir())) == 2
    assert not paths["archive"].exists()
    assert not paths["retry"].exists()
