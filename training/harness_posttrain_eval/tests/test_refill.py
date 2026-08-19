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
from harness_posttrain_eval.refill import prepare_refill


def _write_complete(spec: dict) -> None:
    result = Path(spec["out_dir"])
    result.mkdir(parents=True, exist_ok=True)
    identity = {
        "env": spec["env"],
        "scaffold": spec["scaffold"],
        "model": spec["model"]["name"],
        "task_id": spec["task"]["task_id"],
        "condition": spec["condition"],
    }
    (result / "summary.json").write_bytes(
        canonical_bytes(
            {
                **identity,
                "outcome": "none",
                "chosen": None,
                "num_steps": 1,
                "error": None,
            }
        )
        + b"\n"
    )
    (result / "trajectory.json").write_bytes(
        canonical_bytes(
            {
                **identity,
                "evaluation": {"outcome": "none", "chosen": None},
                "steps": [{"index": 1}],
                "stats": {"error": None},
            }
        )
        + b"\n"
    )


def _fixture(tmp_path: Path) -> dict[str, object]:
    results_root = (tmp_path / "results").resolve()
    config_root = (tmp_path / "configs").resolve()
    state_dir = (tmp_path / "state").resolve()
    results_root.mkdir()
    config_root.mkdir()
    state_dir.mkdir()
    (state_dir / "executor.lock").touch()
    launches: list[dict] = []
    specs: dict[str, dict] = {}
    # Within each arm: one complete result, one partial/invalid directory, and
    # one genuinely absent result path.
    for index, (arm, state) in enumerate(
        (
            ("base", "complete"),
            ("base", "invalid"),
            ("base", "absent"),
            ("trained", "complete"),
            ("trained", "invalid"),
            ("trained", "absent"),
        )
    ):
        run_id = f"run-{index}-{arm}-{state}"
        result = results_root / run_id
        spec = {
            "run_id": run_id,
            "pair_id": f"pair-{index}",
            "arm": arm,
            "port": 25000 + index,
            "out_dir": str(result),
            "env": "amazon",
            "scaffold": "browseruse-deliberative",
            "model": {"name": f"model-{arm}"},
            "task": {"task_id": "laptop-graded"},
            "condition": "combined",
            "runtime_environment": {"RUN": run_id},
            "audit_contract": {
                "matrix_sha256": "b" * 64,
                "endpoint_manifest_sha256": "c" * 64,
            },
        }
        config = config_root / f"{run_id}.json"
        config.write_bytes(canonical_bytes(spec) + b"\n")
        launch = {
            "argv": [
                "python3",
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
        launches.append(launch)
        specs[run_id] = spec
        if state == "complete":
            _write_complete(spec)
        elif state == "invalid":
            result.mkdir()
            (result / "partial.txt").write_text(f"preserve {run_id}\n", encoding="utf-8")

    core = {
        "schema_version": 1,
        "campaign_id": "refill-test",
        "matrix_sha256": "b" * 64,
        "endpoint_manifest_sha256": "c" * 64,
        "base_port": 25000,
        "results_root": str(results_root),
        "launches": launches,
    }
    manifest = {**core, "launch_manifest_sha256": sha256_bytes(canonical_bytes(core))}
    manifest_path = tmp_path / "launch_manifest.json"
    manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")
    status = {
        "schema_version": 1,
        "launch_manifest_sha256": manifest["launch_manifest_sha256"],
        "counts": {"interrupted": len(launches)},
        "runs": {
            launch["run_id"]: {"arm": launch["arm"], "state": "interrupted"}
            for launch in launches
        },
    }
    (state_dir / "batch_status.json").write_bytes(canonical_bytes(status) + b"\n")
    return {
        "archive": tmp_path / "archive",
        "manifest": manifest,
        "manifest_path": manifest_path,
        "results": results_root,
        "retry": tmp_path / "retry.json",
        "specs": specs,
        "state": state_dir,
    }


def _prepare(paths: dict[str, object], **kwargs) -> dict:
    return prepare_refill(
        launch_manifest_path=paths["manifest_path"],
        executor_state_dir=paths["state"],
        archive_root=paths["archive"],
        retry_manifest_path=paths["retry"],
        arms=kwargs.pop("arms", ("base",)),
        run_ids=kwargs.pop("run_ids", None),
        **kwargs,
    )


def test_refill_preserves_complete_archives_partial_and_retries_absent(tmp_path):
    paths = _fixture(tmp_path)
    manifest = paths["manifest"]
    by_state = {
        launch["run_id"].rsplit("-", 1)[1]: launch
        for launch in manifest["launches"]
        if launch["arm"] == "base"
    }
    complete = Path(by_state["complete"]["results"])
    complete_before = {
        path.relative_to(complete).as_posix(): path.read_bytes()
        for path in complete.rglob("*")
        if path.is_file()
    }

    receipt = _prepare(paths)

    retry = json.loads(Path(paths["retry"]).read_text(encoding="utf-8"))
    assert audit_launch_manifest(retry) == {
        "arms": ["base"],
        "runs": 2,
        "valid": True,
    }
    expected_retry = [by_state["invalid"], by_state["absent"]]
    assert retry["launches"] == expected_retry
    assert retry["execution_mode"] == "single_arm_completion"
    assert retry["retry_provenance"]["original_launch_manifest_sha256"] == manifest[
        "launch_manifest_sha256"
    ]

    assert complete.is_dir()
    assert {
        path.relative_to(complete).as_posix(): path.read_bytes()
        for path in complete.rglob("*")
        if path.is_file()
    } == complete_before
    assert not Path(by_state["invalid"]["results"]).exists()
    assert not Path(by_state["absent"]["results"]).exists()
    archived = (
        Path(paths["archive"])
        / "results"
        / Path(by_state["invalid"]["results"]).relative_to(paths["results"])
    )
    assert (archived / "partial.txt").read_text(encoding="utf-8").startswith("preserve")
    assert len(receipt["archived"]) == 1
    assert receipt["archived"][0]["tree_inventory"]
    core = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    assert receipt["receipt_sha256"] == sha256_bytes(canonical_bytes(core))
    assert (Path(paths["archive"]) / "refill_plan.json").is_file()
    assert (Path(paths["archive"]) / "refill_receipt.json").is_file()

    # The unselected arm is not classified or changed, including its partial dir.
    trained_invalid = next(
        launch
        for launch in manifest["launches"]
        if launch["arm"] == "trained" and launch["run_id"].endswith("invalid")
    )
    assert (Path(trained_invalid["results"]) / "partial.txt").is_file()


def test_refill_can_limit_selection_to_explicit_run_ids(tmp_path):
    paths = _fixture(tmp_path)
    manifest = paths["manifest"]
    chosen = next(
        launch
        for launch in manifest["launches"]
        if launch["arm"] == "trained" and launch["run_id"].endswith("absent")
    )

    receipt = _prepare(paths, arms=("trained",), run_ids=(chosen["run_id"],))

    retry = json.loads(Path(paths["retry"]).read_text(encoding="utf-8"))
    assert retry["launches"] == [chosen]
    assert receipt["classification_counts"] == {"absent": 1}
    assert receipt["archived"] == []


def test_refill_emits_balanced_exact_subset_for_two_arms(tmp_path):
    paths = _fixture(tmp_path)
    manifest = paths["manifest"]

    _prepare(paths, arms=("base", "trained"))

    retry = json.loads(Path(paths["retry"]).read_text(encoding="utf-8"))
    expected = [
        launch
        for launch in manifest["launches"]
        if not launch["run_id"].endswith("complete")
    ]
    assert retry["launches"] == expected
    assert retry["execution_mode"] == "balanced_two_arm"
    assert audit_launch_manifest(retry)["arms"] == ["base", "trained"]


def test_refill_refuses_when_all_selected_cells_are_complete(tmp_path):
    paths = _fixture(tmp_path)
    complete = next(
        launch
        for launch in paths["manifest"]["launches"]
        if launch["arm"] == "base" and launch["run_id"].endswith("complete")
    )

    with pytest.raises(IntegrityError, match="all selected cells are complete"):
        _prepare(paths, run_ids=(complete["run_id"],))

    assert not Path(paths["archive"]).exists()
    assert not Path(paths["retry"]).exists()


def test_refill_requires_exclusive_executor_lock(tmp_path):
    paths = _fixture(tmp_path)
    with (Path(paths["state"]) / "executor.lock").open("r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(IntegrityError, match="still owns the executor lock"):
            _prepare(paths)

    assert not Path(paths["archive"]).exists()


def test_refill_refuses_live_recorded_child(tmp_path):
    paths = _fixture(tmp_path)
    status_path = Path(paths["state"]) / "batch_status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    run_id = next(iter(status["runs"]))
    fields = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="utf-8").rsplit(") ", 1)[1].split()
    status["runs"][run_id].update(
        pid=os.getpid(), process_start_ticks=fields[19], state="running"
    )
    status_path.write_bytes(canonical_bytes(status) + b"\n")

    with pytest.raises(IntegrityError, match="children are still live"):
        _prepare(paths)

    assert not Path(paths["archive"]).exists()


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_refill_refuses_unsafe_tree_entry_before_mutation(tmp_path, kind):
    paths = _fixture(tmp_path)
    invalid = next(
        launch
        for launch in paths["manifest"]["launches"]
        if launch["arm"] == "base" and launch["run_id"].endswith("invalid")
    )
    unsafe = Path(invalid["results"]) / "unsafe"
    if kind == "symlink":
        unsafe.symlink_to(Path(invalid["results"]) / "partial.txt")
    else:
        os.mkfifo(unsafe)

    with pytest.raises(IntegrityError, match="symlink|special entry"):
        _prepare(paths)

    assert Path(invalid["results"]).is_dir()
    assert not Path(paths["archive"]).exists()
    assert not Path(paths["retry"]).exists()


def test_refill_refuses_result_path_equal_to_results_root(tmp_path):
    paths = _fixture(tmp_path)
    manifest = paths["manifest"]
    launch = manifest["launches"][1]
    spec_path = Path(launch["config"])
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    launch["results"] = str(paths["results"])
    spec["out_dir"] = str(paths["results"])
    spec_path.write_bytes(canonical_bytes(spec) + b"\n")
    launch["config_sha256"] = sha256_file(spec_path)
    core = {key: value for key, value in manifest.items() if key != "launch_manifest_sha256"}
    manifest["launch_manifest_sha256"] = sha256_bytes(canonical_bytes(core))
    Path(paths["manifest_path"]).write_bytes(canonical_bytes(manifest) + b"\n")
    status_path = Path(paths["state"]) / "batch_status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["launch_manifest_sha256"] = manifest["launch_manifest_sha256"]
    status_path.write_bytes(canonical_bytes(status) + b"\n")

    with pytest.raises(IntegrityError, match="equals results root"):
        _prepare(paths)


def test_refill_refuses_cross_filesystem_archive_before_mutation(tmp_path, monkeypatch):
    paths = _fixture(tmp_path)
    real_nearest = __import__(
        "harness_posttrain_eval.refill", fromlist=["_nearest_existing_directory"]
    )._nearest_existing_directory

    class OtherDevice:
        st_dev = Path(paths["results"]).stat().st_dev + 1

    class FakeAncestor:
        def stat(self):
            return OtherDevice()

    def different_device(path, *, label):
        if label == "archive":
            return FakeAncestor()
        return real_nearest(path, label=label)

    monkeypatch.setattr(
        "harness_posttrain_eval.refill._nearest_existing_directory", different_device
    )
    with pytest.raises(IntegrityError, match="different filesystems"):
        _prepare(paths)

    assert not Path(paths["archive"]).exists()
    assert not Path(paths["retry"]).exists()
