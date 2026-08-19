from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_posttrain_eval import batch
from harness_posttrain_eval.common import (
    IntegrityError,
    canonical_bytes,
    sha256_bytes,
    sha256_file,
)


def _write_result(spec: dict, *, outcome: str = "none") -> None:
    result = Path(spec["out_dir"])
    result.mkdir(parents=True, exist_ok=True)
    identity = {
        "env": spec["env"],
        "scaffold": spec["scaffold"],
        "model": spec["model"]["name"],
        "task_id": spec["task"]["task_id"],
        "condition": spec["condition"],
    }
    (result / "summary.json").write_text(
        json.dumps(
            {**identity, "outcome": outcome, "chosen": None, "num_steps": 1, "error": None}
        ),
        encoding="utf-8",
    )
    (result / "trajectory.json").write_text(
        json.dumps(
            {
                **identity,
                "evaluation": {"outcome": outcome, "chosen": None},
                "steps": [{"index": 1}],
                "stats": {},
            }
        ),
        encoding="utf-8",
    )


def _write_compiler_failure(
    spec: dict, *, provider_failure: bool = False, error: str | None = None
) -> None:
    if error is None:
        error = (
            "RuntimeError: contract compilation failed after four attempts: "
            "ModelProviderError: 1 validation error for _DraftContract "
            "Invalid JSON [type=json_invalid]"
        )
    if provider_failure:
        error = (
            "RuntimeError: contract compilation failed after four attempts: "
            "ModelProviderError: TRAPI: Error code: 429 rate limit exceeded; "
            "1 validation error for _DraftContract [type=json_invalid]"
        )
    result = Path(spec["out_dir"])
    result.mkdir(parents=True, exist_ok=True)
    identity = {
        "env": spec["env"],
        "scaffold": spec["scaffold"],
        "model": spec["model"]["name"],
        "task_id": spec["task"]["task_id"],
        "condition": spec["condition"],
    }
    (result / "summary.json").write_text(
        json.dumps(
            {
                **identity,
                "outcome": "none",
                "chosen": None,
                "num_steps": 0,
                "error": error,
            }
        ),
        encoding="utf-8",
    )
    (result / "trajectory.json").write_text(
        json.dumps(
            {
                **identity,
                "evaluation": {"outcome": "none", "chosen": None},
                "steps": [],
                "stats": {
                    "error": error,
                    "deliberative": {
                        "contract_compile_calls": 1,
                        "contract_compile_attempts": 4,
                        "contract_compile_rejections": 4,
                        "contract_compile_failures": 1,
                        "contract_sha256": None,
                        "structured_max_attempts_observed": 4,
                        "structured_attempt_exhaustions": 1,
                        "decision_checkpoint_calls": 0,
                    },
                    "limit_audit": {
                        "complete": True,
                        "categories": {
                            "fixed_architecture": {
                                "structured_response_attempts": {
                                    "configured": 4,
                                    "touched_count": 1,
                                    "observations": {
                                        "max_attempts": 4,
                                        "attempts": 4,
                                        "rejected_attempts": 4,
                                        "exhaustions": 1,
                                    },
                                }
                            }
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def _manifest(tmp_path: Path, *, runs: int = 6) -> dict:
    launches = []
    for index in range(runs):
        arm = ("base", "trained")[index % 2]
        run_id = f"run-{index}-{arm}"
        result = tmp_path / "results" / run_id
        spec = {
            "run_id": run_id,
            "pair_id": f"pair-{index // 2}",
            "arm": arm,
            "port": 20000 + index,
            "out_dir": str(result),
            "env": "amazon",
            "scaffold": "browseruse-deliberative",
            "model": {"name": arm},
            "task": {"task_id": "laptop-graded"},
            "condition": "combined",
            "runtime_environment": {"TEST_RUN_ID": run_id},
            "audit_contract": {
                "harness_sha256": "a" * 64,
                "matrix_sha256": "b" * 64,
                "endpoint_manifest_sha256": "c" * 64,
            },
        }
        config = tmp_path / "configs" / f"{run_id}.json"
        config.parent.mkdir(exist_ok=True)
        config.write_bytes(canonical_bytes(spec) + b"\n")
        launches.append(
            {
                "run_id": run_id,
                "pair_id": spec["pair_id"],
                "arm": arm,
                "port": spec["port"],
                "config": str(config),
                "config_sha256": sha256_file(config),
                "results": str(result),
                "argv": [
                    "python",
                    "-m",
                    "harness_posttrain_eval.launch_one",
                    "--spec",
                    str(config),
                ],
                "environment": spec["runtime_environment"],
                "audit_contract": spec["audit_contract"],
            }
        )
    core = {
        "schema_version": 1,
        "campaign_id": "test",
        "matrix_sha256": "b" * 64,
        "endpoint_manifest_sha256": "c" * 64,
        "base_port": 20000,
        "results_root": str(tmp_path / "results"),
        "launches": launches,
    }
    return {**core, "launch_manifest_sha256": sha256_bytes(canonical_bytes(core))}


def _one_arm_manifest(manifest: dict, *, execution_mode: str) -> dict:
    launches = [launch for launch in manifest["launches"] if launch["arm"] == "base"]
    core = {
        key: value
        for key, value in manifest.items()
        if key not in {"launch_manifest_sha256", "launches"}
    }
    core.update(execution_mode=execution_mode, launches=launches)
    return {**core, "launch_manifest_sha256": sha256_bytes(canonical_bytes(core))}


def test_batch_is_balanced_and_resume_preserves_completed(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)
    first = manifest["launches"][0]
    _write_result(json.loads(Path(first["config"]).read_text(encoding="utf-8")))
    starts = []

    class FakeProcess:
        next_pid = 10

        def __init__(self, argv, **kwargs):
            del kwargs
            self.pid = self.next_pid
            FakeProcess.next_pid += 1
            spec = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))
            starts.append((spec["run_id"], spec["arm"]))
            _write_result(spec)

        def poll(self):
            return 0

    monkeypatch.setattr(batch.subprocess, "Popen", FakeProcess)
    report = batch.run_launch_manifest(
        manifest=manifest,
        state_dir=tmp_path / "state",
        working_directory=tmp_path,
        jobs=4,
        poll_seconds=0.001,
    )
    assert report["success"] is True
    assert report["counts"] == {"complete": 5, "preserved": 1}
    assert first["run_id"] not in {run_id for run_id, _ in starts}
    first_arms = [arm for _, arm in starts[:4]]
    assert abs(first_arms.count("base") - first_arms.count("trained")) <= 1

    starts.clear()
    resumed = batch.run_launch_manifest(
        manifest=manifest,
        state_dir=tmp_path / "state",
        working_directory=tmp_path,
        jobs=4,
    )
    assert resumed["counts"] == {"preserved": 6}
    assert starts == []


def test_batch_leaves_invalid_existing_result_untouched(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path, runs=2)
    launch = manifest["launches"][0]
    result = Path(launch["results"])
    result.mkdir(parents=True)
    (result / "summary.json").write_text("{}", encoding="utf-8")
    before = (result / "summary.json").read_bytes()

    class FakeProcess:
        def __init__(self, argv, **kwargs):
            del kwargs
            self.pid = 1
            spec = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))
            _write_result(spec)

        def poll(self):
            return 0

    monkeypatch.setattr(batch.subprocess, "Popen", FakeProcess)
    report = batch.run_launch_manifest(
        manifest=manifest,
        state_dir=tmp_path / "state",
        working_directory=tmp_path,
        jobs=2,
    )
    assert report["success"] is False
    assert report["counts"]["invalid"] == 1
    assert (result / "summary.json").read_bytes() == before


def test_batch_never_relaunches_a_nonempty_partial_result(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path, runs=2)
    partial = manifest["launches"][0]
    result = Path(partial["results"])
    evaluate_store = result / "_work" / "evaluate_results"
    evaluate_store.mkdir(parents=True)
    starts = []

    class FakeProcess:
        def __init__(self, argv, **kwargs):
            del kwargs
            self.pid = 1
            spec = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))
            starts.append(spec["run_id"])
            _write_result(spec)

        def poll(self):
            return 0

    monkeypatch.setattr(batch.subprocess, "Popen", FakeProcess)
    report = batch.run_launch_manifest(
        manifest=manifest,
        state_dir=tmp_path / "state",
        working_directory=tmp_path,
        jobs=2,
    )

    assert report["success"] is False
    assert report["counts"] == {"complete": 1, "invalid": 1}
    assert report["runs"][partial["run_id"]]["reason"] == (
        "partial result directory exists without summary.json and trajectory.json"
    )
    assert partial["run_id"] not in starts
    assert evaluate_store.is_dir()
    assert not (result / "summary.json").exists()
    assert not (result / "trajectory.json").exists()


def test_batch_rejects_shared_result_directory(tmp_path):
    manifest = _manifest(tmp_path, runs=2)
    manifest["launches"][1]["results"] = manifest["launches"][0]["results"]
    core = {
        key: value
        for key, value in manifest.items()
        if key != "launch_manifest_sha256"
    }
    manifest["launch_manifest_sha256"] = sha256_bytes(canonical_bytes(core))
    with pytest.raises(IntegrityError, match="duplicate launch result directory"):
        batch.audit_launch_manifest(manifest)


def test_batch_refuses_state_from_another_launch_manifest(tmp_path):
    manifest = _manifest(tmp_path, runs=2)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "batch_status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "launch_manifest_sha256": "0" * 64,
                "runs": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(IntegrityError, match="different launch manifest"):
        batch.run_launch_manifest(
            manifest=manifest,
            state_dir=state_dir,
            working_directory=tmp_path,
            jobs=2,
        )


def test_batch_preserves_policy_exhaustion_but_rejects_provider_failure(tmp_path):
    manifest = _manifest(tmp_path, runs=2)
    policy_launch, provider_launch = manifest["launches"]
    _write_compiler_failure(
        json.loads(Path(policy_launch["config"]).read_text(encoding="utf-8"))
    )
    _write_compiler_failure(
        json.loads(Path(provider_launch["config"]).read_text(encoding="utf-8")),
        provider_failure=True,
    )
    assert batch._load_result(policy_launch) == ("complete", None)
    state, reason = batch._load_result(provider_launch)
    assert state == "invalid"
    assert "zero-step run" in str(reason)


def test_batch_preserves_generic_structured_parse_exhaustion(tmp_path):
    manifest = _manifest(tmp_path, runs=1)
    launch = manifest["launches"][0]
    _write_compiler_failure(
        json.loads(Path(launch["config"]).read_text(encoding="utf-8")),
        error=(
            "RuntimeError: contract compilation failed after four attempts: "
            "ModelProviderError: Failed to parse structured output from model response"
        ),
    )
    assert batch._load_result(launch) == ("complete", None)


def test_nonzero_launcher_exit_cannot_turn_result_into_success(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path, runs=2)

    class FakeProcess:
        next_pid = 100

        def __init__(self, argv, **kwargs):
            del kwargs
            self.pid = self.next_pid
            FakeProcess.next_pid += 1
            spec = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))
            _write_result(spec)

        def poll(self):
            return 124

    monkeypatch.setattr(batch.subprocess, "Popen", FakeProcess)
    report = batch.run_launch_manifest(
        manifest=manifest,
        state_dir=tmp_path / "state",
        working_directory=tmp_path,
        jobs=2,
        poll_seconds=0.001,
    )
    assert report["success"] is False
    assert report["counts"] == {"failed": 2}
    assert all(row["reason"] == "launcher exited 124" for row in report["runs"].values())


def test_single_arm_completion_can_run_with_one_worker(tmp_path, monkeypatch):
    manifest = _one_arm_manifest(
        _manifest(tmp_path, runs=2), execution_mode="single_arm_completion"
    )

    class FakeProcess:
        pid = 200

        def __init__(self, argv, **kwargs):
            del kwargs
            spec = json.loads(Path(argv[-1]).read_text(encoding="utf-8"))
            _write_result(spec)

        def poll(self):
            return 0

    monkeypatch.setattr(batch.subprocess, "Popen", FakeProcess)
    assert batch.audit_launch_manifest(manifest) == {
        "valid": True,
        "runs": 1,
        "arms": ["base"],
    }
    report = batch.run_launch_manifest(
        manifest=manifest,
        state_dir=tmp_path / "single-state",
        working_directory=tmp_path,
        jobs=1,
        poll_seconds=0.001,
    )
    assert report["success"] is True
    assert report["counts"] == {"complete": 1}


def test_reuse_only_manifest_is_auditable_but_not_executable(tmp_path):
    manifest = _one_arm_manifest(
        _manifest(tmp_path, runs=2), execution_mode="reuse_only"
    )
    assert batch.audit_launch_manifest(manifest)["runs"] == 1
    with pytest.raises(IntegrityError, match="cannot be executed"):
        batch.run_launch_manifest(
            manifest=manifest,
            state_dir=tmp_path / "reuse-state",
            working_directory=tmp_path,
            jobs=1,
        )
