"""Create an exact CAVEAT-27B retry bundle without replacing completed evaluation evidence.

This module is intentionally outcome blind.  It delegates the only semantic
result inspection to :func:`caveat_27b_eval.batch._load_result`, then
uses only that classifier's ``complete``, ``absent``, or ``invalid`` state.
Existing non-complete result directories are moved to a create-only archive;
complete results are never moved or rewritten.
"""

from __future__ import annotations

import copy
import fcntl
import os
import stat
from collections import Counter
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .batch import _load_result, audit_launch_manifest
from .common import (
    IntegrityError,
    canonical_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json_create_only,
)
from .restart_recovery import _strict_tree_inventory


REFILL_PLAN_SCHEMA = "caveat-27b-eval.refill-plan.v1"
REFILL_RECEIPT_SCHEMA = "caveat-27b-eval.refill-receipt.v1"


def _absolute(path: Path) -> Path:
    """Normalize ``.``/``..`` without following symbolic links."""

    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _require_existing_prefixes_safe(path: Path, *, label: str) -> None:
    """Reject a symlink or non-directory in every existing directory prefix."""

    absolute = _absolute(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise IntegrityError(f"cannot inspect {label} path {current}: {exc}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise IntegrityError(f"{label} path contains a symlink: {current}")
        if current != absolute and not stat.S_ISDIR(metadata.st_mode):
            raise IntegrityError(
                f"{label} path contains a non-directory prefix: {current}"
            )


def _require_real_directory(path: Path, *, label: str) -> None:
    _require_existing_prefixes_safe(path, label=label)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise IntegrityError(f"{label} directory is absent or unreadable: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise IntegrityError(f"{label} is not a real directory: {path}")


def _require_real_file(path: Path, *, label: str) -> None:
    path = _absolute(path)
    _require_existing_prefixes_safe(path, label=label)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise IntegrityError(f"{label} file is absent or unreadable: {path}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise IntegrityError(f"{label} is not a safe regular file: {path}")


def _nearest_existing_directory(path: Path, *, label: str) -> Path:
    candidate = _absolute(path)
    _require_existing_prefixes_safe(candidate, label=label)
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            raise IntegrityError(f"{label} has no existing directory ancestor: {path}")
        candidate = parent
    _require_real_directory(candidate, label=f"{label} ancestor")
    return candidate


def _process_start_ticks(pid: int) -> str | None:
    try:
        fields = (
            Path(f"/proc/{pid}/stat")
            .read_text(encoding="utf-8")
            .rsplit(") ", 1)[1]
            .split()
        )
    except FileNotFoundError:
        return None
    except (OSError, IndexError) as exc:
        raise IntegrityError(f"cannot inspect recorded child PID {pid}: {exc}") from exc
    return fields[19]


def _require_no_live_recorded_children(status: dict[str, Any]) -> None:
    runs = status.get("runs")
    if not isinstance(runs, dict):
        raise IntegrityError("executor status has no run records")
    live: list[str] = []
    for run_id, record in runs.items():
        if not isinstance(run_id, str) or not isinstance(record, dict):
            raise IntegrityError("executor status contains a malformed run record")
        pid = record.get("pid")
        start_ticks = record.get("process_start_ticks")
        if pid is None and start_ticks is None:
            continue
        if type(pid) is not int or pid <= 0 or not isinstance(start_ticks, str):
            raise IntegrityError(f"{run_id} has an unverifiable recorded child PID")
        observed = _process_start_ticks(pid)
        if observed is not None and observed == start_ticks:
            live.append(run_id)
    if live:
        raise IntegrityError(f"recorded evaluator children are still live: {sorted(live)}")


@contextmanager
def _exclusive_executor_state(
    state_dir: Path, *, launch_manifest_sha256: str
) -> Iterator[dict[str, Any]]:
    state_dir = _absolute(state_dir)
    _require_real_directory(state_dir, label="executor state")
    lock_path = state_dir / "executor.lock"
    status_path = state_dir / "batch_status.json"
    for path, label in ((lock_path, "executor lock"), (status_path, "executor status")):
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise IntegrityError(f"{label} is absent or unreadable: {path}") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise IntegrityError(f"{label} is not a safe regular file: {path}")

    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags)
    lock = os.fdopen(descriptor, "r+")
    try:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise IntegrityError("executor lock changed type while it was opened")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise IntegrityError("main evaluator still owns the executor lock") from exc
        status = read_json(status_path)
        if not isinstance(status, dict):
            raise IntegrityError("executor status is not an object")
        if status.get("launch_manifest_sha256") != launch_manifest_sha256:
            raise IntegrityError("executor state belongs to a different launch manifest")
        _require_no_live_recorded_children(status)
        yield status
    finally:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        finally:
            lock.close()


def _result_location(launch: dict[str, Any], *, results_root: Path) -> tuple[Path, Path]:
    result = _absolute(Path(str(launch["results"])))
    try:
        relative = result.relative_to(results_root)
    except ValueError as exc:
        raise IntegrityError(f"{launch['run_id']} result directory escapes results root") from exc
    if not relative.parts:
        raise IntegrityError(f"{launch['run_id']} result directory equals results root")
    _require_existing_prefixes_safe(result, label=f"{launch['run_id']} result")
    try:
        metadata = result.lstat()
    except FileNotFoundError:
        return result, relative
    except OSError as exc:
        raise IntegrityError(f"cannot inspect {launch['run_id']} result path: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise IntegrityError(f"{launch['run_id']} result path is a symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        raise IntegrityError(f"{launch['run_id']} result path is not a directory")
    return result, relative


def _normalize_selection(
    manifest: dict[str, Any],
    *,
    arms: Collection[str],
    run_ids: Collection[str] | None,
) -> tuple[tuple[str, ...], set[str] | None, list[dict[str, Any]]]:
    selected_arms = tuple(dict.fromkeys(arms))
    if not selected_arms or any(not isinstance(arm, str) or not arm for arm in selected_arms):
        raise IntegrityError("at least one non-empty arm must be selected")
    if len(selected_arms) != len(tuple(arms)):
        raise IntegrityError("selected arms contain duplicates")
    launch_by_id = {str(launch["run_id"]): launch for launch in manifest["launches"]}
    available_arms = {str(launch["arm"]) for launch in manifest["launches"]}
    unknown_arms = sorted(set(selected_arms) - available_arms)
    if unknown_arms:
        raise IntegrityError(f"selected arms are absent from the launch manifest: {unknown_arms}")

    selected_ids: set[str] | None = None
    if run_ids is not None:
        requested = tuple(run_ids)
        if not requested or any(not isinstance(run_id, str) or not run_id for run_id in requested):
            raise IntegrityError("explicit run IDs must be a non-empty collection")
        if len(set(requested)) != len(requested):
            raise IntegrityError("explicit run IDs contain duplicates")
        selected_ids = set(requested)
        unknown_ids = sorted(selected_ids - set(launch_by_id))
        if unknown_ids:
            raise IntegrityError(f"explicit run IDs are absent from the launch manifest: {unknown_ids}")
        wrong_arm = sorted(
            run_id
            for run_id in selected_ids
            if str(launch_by_id[run_id]["arm"]) not in selected_arms
        )
        if wrong_arm:
            raise IntegrityError(
                f"explicit run IDs do not belong to the selected arms: {wrong_arm}"
            )

    selected = [
        launch
        for launch in manifest["launches"]
        if str(launch["arm"]) in selected_arms
        and (selected_ids is None or str(launch["run_id"]) in selected_ids)
    ]
    if not selected:
        raise IntegrityError("selection contains no launch objects")
    return selected_arms, selected_ids, selected


def prepare_refill(
    *,
    launch_manifest_path: Path,
    executor_state_dir: Path,
    archive_root: Path,
    retry_manifest_path: Path,
    arms: Collection[str],
    run_ids: Collection[str] | None = None,
) -> dict[str, Any]:
    """Archive selected non-complete attempts and emit their exact retry subset.

    The executor lock remains held from classification through the final receipt.
    A complete result is only validated for safe filesystem types and is otherwise
    untouched.  An absent result path is included in the retry without an archive
    entry.  Every existing absent/invalid directory is atomically renamed into the
    archive after its full tree inventory has been hashed.
    """

    launch_manifest_path = _absolute(launch_manifest_path)
    executor_state_dir = _absolute(executor_state_dir)
    archive_root = _absolute(archive_root)
    retry_manifest_path = _absolute(retry_manifest_path)
    _require_existing_prefixes_safe(launch_manifest_path, label="launch manifest")
    try:
        manifest_metadata = launch_manifest_path.lstat()
    except OSError as exc:
        raise IntegrityError(f"launch manifest is absent or unreadable: {launch_manifest_path}") from exc
    if stat.S_ISLNK(manifest_metadata.st_mode) or not stat.S_ISREG(manifest_metadata.st_mode):
        raise IntegrityError("launch manifest is not a safe regular file")
    manifest = read_json(launch_manifest_path)
    if not isinstance(manifest, dict):
        raise IntegrityError("launch manifest is not an object")
    audit_launch_manifest(manifest)
    if manifest.get("execution_mode", "balanced_two_arm") == "reuse_only":
        raise IntegrityError("reuse-only launch manifests cannot be refilled")

    results_root = _absolute(Path(str(manifest["results_root"])))
    _require_real_directory(results_root, label="results root")
    selected_arms, selected_ids, selected = _normalize_selection(
        manifest, arms=arms, run_ids=run_ids
    )

    if archive_root.exists() or archive_root.is_symlink():
        raise IntegrityError(f"archive root already exists: {archive_root}")
    if retry_manifest_path.exists() or retry_manifest_path.is_symlink():
        raise IntegrityError(f"retry manifest already exists: {retry_manifest_path}")
    _require_existing_prefixes_safe(archive_root, label="archive")
    _require_existing_prefixes_safe(retry_manifest_path, label="retry manifest")
    if _within(archive_root, results_root) or _within(results_root, archive_root):
        raise IntegrityError("archive root and live results root must not contain each other")
    if _within(retry_manifest_path, results_root) or _within(retry_manifest_path, archive_root):
        raise IntegrityError("retry manifest must be outside live results and archive roots")
    archive_ancestor = _nearest_existing_directory(archive_root.parent, label="archive")
    _nearest_existing_directory(retry_manifest_path.parent, label="retry manifest")
    if archive_ancestor.stat().st_dev != results_root.stat().st_dev:
        raise IntegrityError("archive and result roots are on different filesystems")

    with _exclusive_executor_state(
        executor_state_dir,
        launch_manifest_sha256=str(manifest["launch_manifest_sha256"]),
    ):
        classifications: list[dict[str, Any]] = []
        archive_candidates: list[dict[str, Any]] = []
        retry_launches: list[dict[str, Any]] = []
        for launch in selected:
            _require_real_file(
                Path(str(launch["config"])),
                label=f"{launch['run_id']} launch config",
            )
            result, relative = _result_location(launch, results_root=results_root)
            inventory: list[dict[str, Any]] | None = None
            tree_sha256: str | None = None
            result_metadata: os.stat_result | None = None
            if result.exists():
                result_metadata = result.lstat()
                inventory, tree_sha256 = _strict_tree_inventory(result)
            result_state, reason = _load_result(launch)
            if result_state not in {"complete", "absent", "invalid"}:
                raise IntegrityError(
                    f"{launch['run_id']} classifier returned an unknown state: {result_state}"
                )
            record = {
                "arm": launch["arm"],
                "classification": result_state,
                "reason": reason,
                "relative_result_path": relative.as_posix(),
                "run_id": launch["run_id"],
            }
            classifications.append(record)
            if result_state == "complete":
                continue
            retry_launches.append(copy.deepcopy(launch))
            if result.exists():
                if inventory is None or tree_sha256 is None or result_metadata is None:
                    raise IntegrityError(f"{launch['run_id']} result tree was not inventoried")
                archive_candidates.append(
                    {
                        **record,
                        "source": str(result),
                        "source_device": result_metadata.st_dev,
                        "source_inode": result_metadata.st_ino,
                        "tree_inventory": inventory,
                        "tree_sha256": tree_sha256,
                    }
                )

        if not retry_launches:
            raise IntegrityError("all selected cells are complete; no refill is needed")
        for record in archive_candidates:
            source_metadata = Path(str(record["source"])).lstat()
            if (
                not stat.S_ISDIR(source_metadata.st_mode)
                or source_metadata.st_dev != record["source_device"]
                or source_metadata.st_ino != record["source_inode"]
            ):
                raise IntegrityError(
                    f"result directory changed during refill preparation: {record['source']}"
                )
            if source_metadata.st_dev != results_root.stat().st_dev:
                raise IntegrityError(
                    f"cannot atomically archive across filesystems: {record['source']}"
                )
        retry_arms = sorted({str(launch["arm"]) for launch in retry_launches})
        if len(retry_arms) > 2:
            raise IntegrityError(f"retry subset has unsupported arms: {retry_arms}")
        retry_core = {
            key: copy.deepcopy(value)
            for key, value in manifest.items()
            if key not in {"launch_manifest_sha256", "launches", "execution_mode"}
        }
        retry_core.update(
            execution_mode=(
                "single_arm_completion" if len(retry_arms) == 1 else "balanced_two_arm"
            ),
            launches=retry_launches,
            retry_provenance={
                "classifier": "caveat_27b_eval.batch._load_result",
                "explicit_run_ids": sorted(selected_ids) if selected_ids is not None else None,
                "original_launch_manifest_sha256": manifest["launch_manifest_sha256"],
                "selected_arms": list(selected_arms),
            },
        )
        retry_manifest = {
            **retry_core,
            "launch_manifest_sha256": sha256_bytes(canonical_bytes(retry_core)),
        }
        audit_launch_manifest(retry_manifest)
        original_by_id = {str(launch["run_id"]): launch for launch in manifest["launches"]}
        if any(
            launch != original_by_id[str(launch["run_id"])]
            for launch in retry_manifest["launches"]
        ):
            raise IntegrityError("retry launch objects differ from the original manifest")

        archive_root.parent.mkdir(parents=True, exist_ok=True)
        archive_root.mkdir(mode=0o700)
        if archive_root.stat().st_dev != results_root.stat().st_dev:
            raise IntegrityError("archive and result roots are on different filesystems")
        plan_core = {
            "schema": REFILL_PLAN_SCHEMA,
            "archive_root": str(archive_root),
            "classifications": classifications,
            "original_launch_manifest": str(launch_manifest_path),
            "original_launch_manifest_file_sha256": sha256_file(launch_manifest_path),
            "original_launch_manifest_sha256": manifest["launch_manifest_sha256"],
            "retry_manifest": str(retry_manifest_path),
            "retry_manifest_sha256": retry_manifest["launch_manifest_sha256"],
            "runs_to_archive": archive_candidates,
            "runs_to_retry": [str(launch["run_id"]) for launch in retry_launches],
        }
        plan = {
            **plan_core,
            "plan_sha256": sha256_bytes(canonical_bytes(plan_core)),
        }
        write_json_create_only(archive_root / "refill_plan.json", plan)

        archived: list[dict[str, Any]] = []
        for record in archive_candidates:
            source = Path(str(record["source"]))
            destination = archive_root / "results" / str(record["relative_result_path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                raise IntegrityError(f"archive destination already exists: {destination}")
            source_metadata = source.lstat()
            if (
                not stat.S_ISDIR(source_metadata.st_mode)
                or source_metadata.st_dev != record["source_device"]
                or source_metadata.st_ino != record["source_inode"]
            ):
                raise IntegrityError(f"result directory changed before archive: {source}")
            if source_metadata.st_dev != destination.parent.stat().st_dev:
                raise IntegrityError(f"cannot atomically archive across filesystems: {source}")
            os.rename(source, destination)
            destination_metadata = destination.lstat()
            if (
                destination_metadata.st_dev != record["source_device"]
                or destination_metadata.st_ino != record["source_inode"]
            ):
                raise IntegrityError(f"atomic archive identity changed: {record['run_id']}")
            observed_inventory, observed_sha256 = _strict_tree_inventory(destination)
            if (
                observed_inventory != record["tree_inventory"]
                or observed_sha256 != record["tree_sha256"]
            ):
                raise IntegrityError(f"post-move archive verification failed: {record['run_id']}")
            if source.exists() or source.is_symlink():
                raise IntegrityError(f"source survived atomic archive move: {source}")
            archived.append(
                {
                    "archive": str(destination),
                    "classification": record["classification"],
                    "relative_result_path": record["relative_result_path"],
                    "run_id": record["run_id"],
                    "tree_inventory": observed_inventory,
                    "tree_sha256": observed_sha256,
                }
            )

        write_json_create_only(retry_manifest_path, retry_manifest)
        written_retry = read_json(retry_manifest_path)
        if written_retry != retry_manifest:
            raise IntegrityError("written retry manifest differs from its audited object")
        audit_launch_manifest(written_retry)
        receipt_core = {
            "schema": REFILL_RECEIPT_SCHEMA,
            "archive_root": str(archive_root),
            "archived": archived,
            "classification_counts": dict(
                sorted(Counter(record["classification"] for record in classifications).items())
            ),
            "complete_results_preserved": [
                record["run_id"]
                for record in classifications
                if record["classification"] == "complete"
            ],
            "original_launch_manifest_file_sha256": sha256_file(launch_manifest_path),
            "original_launch_manifest_sha256": manifest["launch_manifest_sha256"],
            "plan_file_sha256": sha256_file(archive_root / "refill_plan.json"),
            "plan_sha256": plan["plan_sha256"],
            "retry_manifest": str(retry_manifest_path),
            "retry_manifest_file_sha256": sha256_file(retry_manifest_path),
            "retry_manifest_sha256": retry_manifest["launch_manifest_sha256"],
            "runs_to_retry": [str(launch["run_id"]) for launch in retry_launches],
        }
        receipt = {
            **receipt_core,
            "receipt_sha256": sha256_bytes(canonical_bytes(receipt_core)),
        }
        write_json_create_only(archive_root / "refill_receipt.json", receipt)
        return receipt
