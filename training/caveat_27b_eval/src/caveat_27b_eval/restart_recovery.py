from __future__ import annotations

import argparse
import fcntl
import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .batch import audit_launch_manifest
from .common import (
    IntegrityError,
    canonical_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json_create_only,
)


SCHEMA_VERSION = 1
PROFILE_MARKER = b"Created new profile (Default) in temp directory:"
RESTART_ERROR = (
    "_EvaluateResultStoreIntegrityError: evaluate-result store root already exists; "
    "refusing to import prior-run records"
)
CLASSIFIER_REASON = "zero-step run is not an attested compiler policy failure"
ACTIVE_STATES = frozenset({"running", "orphan_running", "interrupted"})


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _strict_tree_inventory(root: Path) -> tuple[list[dict[str, Any]], str]:
    """Hash every regular file and record empty directories; reject special entries."""

    if not root.is_dir() or root.is_symlink():
        raise IntegrityError(f"result path is not a real directory: {root}")
    inventory: list[dict[str, Any]] = []
    for path in sorted(
        root.rglob("*"), key=lambda member: member.relative_to(root).as_posix()
    ):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if stat.S_ISLNK(metadata.st_mode):
            raise IntegrityError(f"result tree contains a symlink: {path}")
        if stat.S_ISDIR(metadata.st_mode):
            inventory.append({"kind": "directory", "mode": mode, "path": relative})
        elif stat.S_ISREG(metadata.st_mode):
            inventory.append(
                {
                    "kind": "file",
                    "mode": mode,
                    "path": relative,
                    "sha256": sha256_file(path),
                    "size": metadata.st_size,
                }
            )
        else:
            raise IntegrityError(f"result tree contains a special entry: {path}")
    return inventory, sha256_bytes(canonical_bytes(inventory))


def _require_identity(
    *, launch: dict[str, Any], summary: dict[str, Any], trajectory: dict[str, Any]
) -> None:
    spec = read_json(Path(str(launch["config"])))
    model_spec = spec.get("model")
    model_name = (
        model_spec if isinstance(model_spec, str) else (model_spec or {}).get("name")
    )
    expected = {
        "env": spec["env"],
        "scaffold": spec["scaffold"],
        "model": model_name,
        "task_id": spec["task"]["task_id"],
        "condition": spec["condition"],
    }
    for field, value in expected.items():
        if summary.get(field) != value or trajectory.get(field) != value:
            raise IntegrityError(
                f"{launch['run_id']} recorded {field} does not match its launch spec"
            )


def _verify_restart_artifact(
    *, launch: dict[str, Any], status: dict[str, Any], results_root: Path
) -> dict[str, Any]:
    run_id = str(launch["run_id"])
    if status.get("state") != "failed":
        raise IntegrityError(f"{run_id} is not recorded as failed")
    if status.get("exit_code") != 0 or status.get("reason") != CLASSIFIER_REASON:
        raise IntegrityError(
            f"{run_id} does not have the audited launcher/classifier state"
        )

    result_dir = Path(str(launch["results"])).resolve()
    try:
        relative = result_dir.relative_to(results_root)
    except ValueError as exc:
        raise IntegrityError(f"{run_id} result directory escapes results root") from exc
    if not relative.parts:
        raise IntegrityError(f"{run_id} result directory equals the results root")

    summary_path = result_dir / "summary.json"
    trajectory_path = result_dir / "trajectory.json"
    run_log_path = result_dir / "run.log"
    evaluate_store = result_dir / "_work" / "evaluate_results"
    for path in (summary_path, trajectory_path, run_log_path):
        if not path.is_file() or path.is_symlink():
            raise IntegrityError(f"{run_id} is missing a regular {path.name}")
    if not evaluate_store.is_dir() or evaluate_store.is_symlink():
        raise IntegrityError(
            f"{run_id} lacks its preexisting evaluate-result work directory"
        )

    summary = read_json(summary_path)
    trajectory = read_json(trajectory_path)
    _require_identity(launch=launch, summary=summary, trajectory=trajectory)
    if summary.get("schema") != 2:
        raise IntegrityError(
            f"{run_id} summary schema differs from the audited artifact"
        )
    expected_summary = {
        "num_steps": 0,
        "outcome": "none",
        "success": False,
        "took_bait": False,
        "chosen": None,
        "chosen_label": None,
        "seconds": 0.0,
        "error": RESTART_ERROR,
    }
    if any(summary.get(field) != value for field, value in expected_summary.items()):
        raise IntegrityError(
            f"{run_id} summary does not match the restart-artifact signature"
        )

    stats = trajectory.get("stats")
    evaluation = trajectory.get("evaluation")
    if trajectory.get("steps") != [] or not isinstance(stats, dict):
        raise IntegrityError(f"{run_id} trajectory is not an exact zero-step artifact")
    expected_stats = {
        "num_steps": 0,
        "decision_steps": 0,
        "tool_actions": 0,
        "seconds": 0.0,
        "error": RESTART_ERROR,
    }
    if any(stats.get(field) != value for field, value in expected_stats.items()):
        raise IntegrityError(
            f"{run_id} trajectory stats differ from the restart signature"
        )
    expected_evaluation = {
        "outcome": "none",
        "chosen": None,
        "chosen_label": None,
        "success": False,
        "took_bait": False,
    }
    if not isinstance(evaluation, dict) or any(
        evaluation.get(field) != value for field, value in expected_evaluation.items()
    ):
        raise IntegrityError(f"{run_id} evaluation differs from the restart signature")

    log_bytes = run_log_path.read_bytes()
    if log_bytes.count(PROFILE_MARKER) != 2:
        raise IntegrityError(
            f"{run_id} does not contain exactly two launch-attempt markers"
        )
    final_mtime = min(
        summary_path.stat().st_mtime_ns, trajectory_path.stat().st_mtime_ns
    )
    if evaluate_store.stat().st_mtime_ns >= final_mtime:
        raise IntegrityError(
            f"{run_id} evaluate-result work directory is not preexisting"
        )

    inventory, tree_sha256 = _strict_tree_inventory(result_dir)
    return {
        "arm": launch["arm"],
        "config": launch["config"],
        "config_sha256": launch["config_sha256"],
        "relative_result_path": relative.as_posix(),
        "run_id": run_id,
        "source": str(result_dir),
        "tree_inventory": inventory,
        "tree_sha256": tree_sha256,
    }


def _same_live_process(pid: Any, start_ticks: Any) -> bool:
    if type(pid) is not int or not isinstance(start_ticks, str):
        return False
    try:
        fields = (
            Path(f"/proc/{pid}/stat")
            .read_text(encoding="utf-8")
            .rsplit(") ", 1)[1]
            .split()
        )
    except (OSError, IndexError):
        return False
    return fields[19] == start_ticks


@contextmanager
def _exclusive_executor_lock(state_dir: Path) -> Iterator[dict[str, Any]]:
    if not state_dir.is_dir():
        raise IntegrityError(f"executor state directory is absent: {state_dir}")
    lock_path = state_dir / "executor.lock"
    status_path = state_dir / "batch_status.json"
    if not lock_path.is_file() or lock_path.is_symlink():
        raise IntegrityError(f"executor lock file is absent or unsafe: {lock_path}")
    status = read_json(status_path)
    lock = lock_path.open("r+")
    try:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise IntegrityError("main evaluator still owns the executor lock") from exc
        live = [
            run_id
            for run_id, record in (status.get("runs") or {}).items()
            if isinstance(record, dict)
            and _same_live_process(record.get("pid"), record.get("process_start_ticks"))
        ]
        if live:
            raise IntegrityError(
                f"recorded evaluator children are still live: {sorted(live)}"
            )
        active = [
            run_id
            for run_id, record in (status.get("runs") or {}).items()
            if isinstance(record, dict) and record.get("state") in ACTIVE_STATES
        ]
        if active:
            raise IntegrityError(f"executor state is not terminal: {sorted(active)}")
        yield status
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def recover_restart_artifacts(
    *,
    launch_manifest_path: Path,
    results_root: Path,
    executor_state_dir: Path,
    archive_root: Path,
    retry_manifest_path: Path,
    expected_count: int,
) -> dict[str, Any]:
    if type(expected_count) is not int or expected_count <= 0:
        raise IntegrityError("expected_count must be a positive integer")
    launch_manifest_path = launch_manifest_path.resolve()
    results_root = results_root.resolve()
    executor_state_dir = executor_state_dir.resolve()
    archive_root = archive_root.resolve()
    retry_manifest_path = retry_manifest_path.resolve()
    manifest = read_json(launch_manifest_path)
    audit_launch_manifest(manifest)
    if Path(str(manifest["results_root"])).resolve() != results_root:
        raise IntegrityError("explicit results root differs from the launch manifest")
    if not results_root.is_dir():
        raise IntegrityError(f"results root is absent: {results_root}")
    if archive_root.exists():
        raise IntegrityError(f"archive root already exists: {archive_root}")
    if retry_manifest_path.exists():
        raise IntegrityError(f"retry manifest already exists: {retry_manifest_path}")
    if _is_within(archive_root, results_root) or _is_within(results_root, archive_root):
        raise IntegrityError(
            "archive root and live results root must not contain each other"
        )
    if _is_within(retry_manifest_path, results_root) or _is_within(
        retry_manifest_path, archive_root
    ):
        raise IntegrityError(
            "retry manifest must be outside live results and archive roots"
        )

    with _exclusive_executor_lock(executor_state_dir) as status:
        if status.get("launch_manifest_sha256") != manifest["launch_manifest_sha256"]:
            raise IntegrityError(
                "executor state belongs to a different launch manifest"
            )
        status_runs = status.get("runs")
        if not isinstance(status_runs, dict):
            raise IntegrityError("executor status has no run records")
        launch_by_id = {launch["run_id"]: launch for launch in manifest["launches"]}
        candidate_ids = [
            launch["run_id"]
            for launch in manifest["launches"]
            if isinstance(status_runs.get(launch["run_id"]), dict)
            and status_runs[launch["run_id"]].get("state") == "failed"
            and status_runs[launch["run_id"]].get("exit_code") == 0
            and status_runs[launch["run_id"]].get("reason") == CLASSIFIER_REASON
        ]
        if len(candidate_ids) != expected_count:
            raise IntegrityError(
                f"restart-artifact candidate count {len(candidate_ids)} != expected {expected_count}"
            )
        records = [
            _verify_restart_artifact(
                launch=launch_by_id[run_id],
                status=status_runs[run_id],
                results_root=results_root,
            )
            for run_id in candidate_ids
        ]
        if {record["arm"] for record in records} != {"base", "trained"}:
            raise IntegrityError(
                "recovery subset does not contain both evaluation arms"
            )

        retry_core = {
            key: value
            for key, value in manifest.items()
            if key not in {"launch_manifest_sha256", "launches"}
        }
        retry_core["launches"] = [launch_by_id[run_id] for run_id in candidate_ids]
        retry_manifest = {
            **retry_core,
            "launch_manifest_sha256": sha256_bytes(canonical_bytes(retry_core)),
        }
        audit_launch_manifest(retry_manifest)

        archive_root.parent.mkdir(parents=True, exist_ok=True)
        archive_root.mkdir(mode=0o700)
        if archive_root.stat().st_dev != results_root.stat().st_dev:
            raise IntegrityError(
                "archive and result roots are on different filesystems"
            )
        plan = {
            "schema_version": SCHEMA_VERSION,
            "expected_count": expected_count,
            "original_launch_manifest": str(launch_manifest_path),
            "original_launch_manifest_file_sha256": sha256_file(launch_manifest_path),
            "original_launch_manifest_sha256": manifest["launch_manifest_sha256"],
            "results_root": str(results_root),
            "retry_manifest": str(retry_manifest_path),
            "retry_manifest_sha256": retry_manifest["launch_manifest_sha256"],
            "runs": records,
        }
        write_json_create_only(archive_root / "recovery_plan.json", plan)

        moved: list[dict[str, Any]] = []
        for record in records:
            source = Path(record["source"])
            destination = archive_root / "results" / record["relative_result_path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise IntegrityError(
                    f"archive destination already exists: {destination}"
                )
            if source.stat().st_dev != destination.parent.stat().st_dev:
                raise IntegrityError(
                    f"cannot atomically archive across filesystems: {source}"
                )
            os.rename(source, destination)
            inventory, tree_sha256 = _strict_tree_inventory(destination)
            if (
                inventory != record["tree_inventory"]
                or tree_sha256 != record["tree_sha256"]
            ):
                raise IntegrityError(
                    f"post-move archive verification failed: {record['run_id']}"
                )
            if source.exists():
                raise IntegrityError(f"source survived atomic archive move: {source}")
            moved.append(
                {
                    "archive": str(destination),
                    "run_id": record["run_id"],
                    "tree_sha256": tree_sha256,
                }
            )

        write_json_create_only(retry_manifest_path, retry_manifest)
        if read_json(retry_manifest_path) != retry_manifest:
            raise IntegrityError(
                "written retry manifest differs from the audited object"
            )
        audit_launch_manifest(read_json(retry_manifest_path))
        receipt_core = {
            "schema_version": SCHEMA_VERSION,
            "moved": moved,
            "original_launch_manifest_sha256": manifest["launch_manifest_sha256"],
            "retry_manifest_file_sha256": sha256_file(retry_manifest_path),
            "retry_manifest_sha256": retry_manifest["launch_manifest_sha256"],
        }
        receipt = {
            **receipt_core,
            "receipt_sha256": sha256_bytes(canonical_bytes(receipt_core)),
        }
        write_json_create_only(archive_root / "recovery_receipt.json", receipt)
        return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Archive exact interrupted-resume artifacts and emit a frozen retry subset."
    )
    parser.add_argument("--launch-manifest", required=True, type=Path)
    parser.add_argument("--results-root", required=True, type=Path)
    parser.add_argument("--executor-state-dir", required=True, type=Path)
    parser.add_argument("--archive-root", required=True, type=Path)
    parser.add_argument("--retry-manifest", required=True, type=Path)
    parser.add_argument("--expected-count", required=True, type=int)
    arguments = parser.parse_args(argv)
    try:
        receipt = recover_restart_artifacts(
            launch_manifest_path=arguments.launch_manifest,
            results_root=arguments.results_root,
            executor_state_dir=arguments.executor_state_dir,
            archive_root=arguments.archive_root,
            retry_manifest_path=arguments.retry_manifest,
            expected_count=arguments.expected_count,
        )
    except IntegrityError as exc:
        parser.exit(2, f"recovery refused: {exc}\n")
    sys.stdout.buffer.write(canonical_bytes(receipt) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
