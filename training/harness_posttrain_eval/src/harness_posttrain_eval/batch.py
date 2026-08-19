from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import time
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import (
    IntegrityError,
    canonical_bytes,
    classify_marketplace_result,
    read_json,
    sha256_bytes,
    sha256_file,
)


def audit_launch_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    core = {
        key: value for key, value in manifest.items() if key != "launch_manifest_sha256"
    }
    if manifest.get("launch_manifest_sha256") != sha256_bytes(canonical_bytes(core)):
        raise IntegrityError("launch_manifest_sha256 mismatch")
    launches = manifest.get("launches")
    if not isinstance(launches, list) or not launches:
        raise IntegrityError("launch manifest has no launches")
    run_ids: set[str] = set()
    ports: set[int] = set()
    results: set[Path] = set()
    results_root = Path(str(manifest.get("results_root", ""))).resolve()
    matrix_sha256 = manifest.get("matrix_sha256")
    endpoint_sha256 = manifest.get("endpoint_manifest_sha256")
    for index, launch in enumerate(launches):
        if not isinstance(launch, dict):
            raise IntegrityError(f"launch {index} is not an object")
        run_id = launch.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise IntegrityError(f"launch {index} has no run_id")
        if run_id in run_ids:
            raise IntegrityError(f"duplicate launch run_id: {run_id}")
        run_ids.add(run_id)
        port = launch.get("port")
        if type(port) is not int or not 1 <= port <= 65535:
            raise IntegrityError(f"{run_id} has an invalid port")
        if port in ports:
            raise IntegrityError(f"duplicate launch port: {port}")
        ports.add(port)
        result_path = Path(str(launch.get("results", ""))).resolve()
        try:
            result_path.relative_to(results_root)
        except ValueError as exc:
            raise IntegrityError(f"{run_id} result directory escapes results_root") from exc
        if result_path in results:
            raise IntegrityError(f"duplicate launch result directory: {result_path}")
        results.add(result_path)
        config_path = Path(str(launch.get("config", "")))
        if not config_path.is_file():
            raise IntegrityError(f"{run_id} launch config is absent: {config_path}")
        if launch.get("config_sha256") != sha256_file(config_path):
            raise IntegrityError(f"{run_id} launch config hash mismatch")
        spec = read_json(config_path)
        expected = {
            "run_id": run_id,
            "pair_id": launch.get("pair_id"),
            "arm": launch.get("arm"),
            "port": port,
            "out_dir": launch.get("results"),
            "runtime_environment": launch.get("environment"),
            "audit_contract": launch.get("audit_contract"),
        }
        for field, value in expected.items():
            if spec.get(field) != value:
                raise IntegrityError(f"{run_id} launch/spec {field} mismatch")
        argv = launch.get("argv")
        if not isinstance(argv, list) or not argv or not all(
            isinstance(value, str) and value for value in argv
        ):
            raise IntegrityError(f"{run_id} argv is invalid")
        if argv[1:3] != ["-m", "harness_posttrain_eval.launch_one"] or argv[3:] != [
            "--spec",
            str(config_path),
        ]:
            raise IntegrityError(f"{run_id} argv is not the frozen launch-one command")
        audit_contract = launch.get("audit_contract") or {}
        if (
            audit_contract.get("matrix_sha256") != matrix_sha256
            or audit_contract.get("endpoint_manifest_sha256") != endpoint_sha256
        ):
            raise IntegrityError(f"{run_id} audit contract differs from launch manifest")
    arms = sorted({str(launch.get("arm", "")) for launch in launches})
    execution_mode = manifest.get("execution_mode", "balanced_two_arm")
    if execution_mode == "balanced_two_arm" and len(arms) != 2:
        raise IntegrityError(
            f"balanced executor requires exactly two arms, found {arms}"
        )
    if execution_mode in {"single_arm_completion", "reuse_only"} and len(arms) != 1:
        raise IntegrityError(
            f"{execution_mode} manifest requires exactly one arm, found {arms}"
        )
    if execution_mode not in {
        "balanced_two_arm",
        "single_arm_completion",
        "reuse_only",
    }:
        raise IntegrityError(f"unsupported launch execution mode: {execution_mode}")
    return {"valid": True, "runs": len(launches), "arms": arms}


def _load_result(launch: dict[str, Any]) -> tuple[str, str | None]:
    """Return ``complete``, ``absent``, or ``invalid`` without changing a result."""

    result_dir = Path(launch["results"])
    summary_path = result_dir / "summary.json"
    trajectory_path = result_dir / "trajectory.json"
    if not summary_path.exists() and not trajectory_path.exists():
        if result_dir.exists() and not result_dir.is_dir():
            return "invalid", "result path exists but is not a directory"
        try:
            if result_dir.is_dir() and next(result_dir.iterdir(), None) is not None:
                return (
                    "invalid",
                    "partial result directory exists without summary.json and trajectory.json",
                )
        except OSError as exc:
            return "invalid", f"cannot inspect result directory: {exc}"
        return "absent", None
    if not summary_path.is_file() or not trajectory_path.is_file():
        return "invalid", "only one of summary.json and trajectory.json exists"
    try:
        summary = read_json(summary_path)
        trajectory = read_json(trajectory_path)
        spec = read_json(Path(launch["config"]))
    except IntegrityError as exc:
        return "invalid", str(exc)
    model_spec = spec.get("model")
    model_name = model_spec if isinstance(model_spec, str) else (model_spec or {}).get("name")
    identity = {
        "env": spec["env"],
        "scaffold": spec["scaffold"],
        "model": model_name,
        "task_id": spec["task"]["task_id"],
        "condition": spec["condition"],
    }
    for field, expected in identity.items():
        if summary.get(field) != expected or trajectory.get(field) != expected:
            return "invalid", f"recorded {field} does not match launch spec"
    classification, reason = classify_marketplace_result(summary, trajectory)
    if classification.startswith("behavioral_"):
        return "complete", None
    return "invalid", reason or classification


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(canonical_bytes(value) + b"\n")
    os.replace(temporary, path)


@dataclass
class _Running:
    launch: dict[str, Any]
    process: subprocess.Popen[bytes]
    log_handle: Any
    started_at: float


def _sanitized_environment(explicit: dict[str, str]) -> dict[str, str]:
    try:
        from scripts.hard_campaign_runtime import (
            RUNTIME_SANITIZE_EXACT,
            RUNTIME_SANITIZE_PREFIXES,
        )
    except ImportError as exc:  # pragma: no cover - installed outside AgentArena
        raise IntegrityError("cannot load AgentArena's runtime environment policy") from exc
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in RUNTIME_SANITIZE_EXACT
        and not any(key.startswith(prefix) for prefix in RUNTIME_SANITIZE_PREFIXES)
    }
    environment.update(explicit)
    if "STOREFRONT_OPS_TOKEN" in environment:
        raise IntegrityError("STOREFRONT_OPS_TOKEN survived runtime sanitization")
    return environment


def _process_start_ticks(pid: int) -> str | None:
    try:
        # Field 22 follows a parenthesized command which may itself contain spaces.
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(") ", 1)[1].split()
        return fields[19]
    except (OSError, IndexError):
        return None


def _same_live_process(pid: Any, start_ticks: Any) -> bool:
    return type(pid) is int and isinstance(start_ticks, str) and _process_start_ticks(pid) == start_ticks


def _terminate_running(running: list[_Running], statuses: dict[str, dict[str, Any]]) -> None:
    for item in running:
        if item.process.poll() is None:
            try:
                os.killpg(item.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and any(item.process.poll() is None for item in running):
        time.sleep(0.1)
    for item in running:
        if item.process.poll() is None:
            try:
                os.killpg(item.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        item.log_handle.close()
        statuses[item.launch["run_id"]].update(
            state="interrupted",
            reason="executor interrupted; child process group terminated",
            exit_code=item.process.poll(),
        )


def _choose_arm(
    arms: tuple[str, ...],
    queues: dict[str, deque[dict[str, Any]]],
    running: list[_Running],
    started: Counter[str],
    jobs: int,
) -> str | None:
    running_counts = Counter(item.launch["arm"] for item in running)
    active_arms = [arm for arm in arms if queues[arm] or running_counts[arm]]
    candidates = [arm for arm in arms if queues[arm]]
    if not candidates:
        return None
    if len(active_arms) == 2:
        per_arm_cap = (jobs + 1) // 2
        candidates = [arm for arm in candidates if running_counts[arm] < per_arm_cap]
        if not candidates:
            return None
    return min(
        candidates,
        key=lambda arm: (running_counts[arm], started[arm], arms.index(arm)),
    )


def _run_launch_manifest_locked(
    *,
    manifest: dict[str, Any],
    state_dir: Path,
    working_directory: Path,
    jobs: int,
    spawn_stagger_seconds: float = 0.0,
    poll_seconds: float = 1.0,
) -> dict[str, Any]:
    """Run a two-arm launch bundle while never replacing an existing result.

    A resume skips every complete behavioral result.  Partial, malformed, errored,
    or skipped result directories are reported and left untouched so an operator can
    preserve the failed attempt before deliberately constructing a retry.
    """

    audit = audit_launch_manifest(manifest)
    execution_mode = manifest.get("execution_mode", "balanced_two_arm")
    if execution_mode == "reuse_only":
        raise IntegrityError(
            "reuse-only manifests are evidence inputs and cannot be executed"
        )
    minimum_jobs = 1 if execution_mode == "single_arm_completion" else 2
    if jobs < minimum_jobs:
        raise IntegrityError(
            f"{execution_mode} execution requires --jobs >= {minimum_jobs}"
        )
    if spawn_stagger_seconds < 0 or poll_seconds <= 0:
        raise IntegrityError("spawn/poll intervals must be nonnegative/positive")
    if not working_directory.is_dir():
        raise IntegrityError(f"working directory is absent: {working_directory}")
    status_path = state_dir / "batch_status.json"
    previous_status = read_json(status_path) if status_path.is_file() else {}
    if previous_status and previous_status.get("launch_manifest_sha256") != manifest.get(
        "launch_manifest_sha256"
    ):
        raise IntegrityError("state directory belongs to a different launch manifest")
    previous_runs = previous_status.get("runs", {}) if isinstance(previous_status, dict) else {}

    arms = tuple(audit["arms"])
    queues: dict[str, deque[dict[str, Any]]] = {arm: deque() for arm in arms}
    statuses: dict[str, dict[str, Any]] = {}
    for launch in manifest["launches"]:
        result_state, reason = _load_result(launch)
        previous = previous_runs.get(launch["run_id"], {})
        if (
            result_state == "complete"
            and isinstance(previous, dict)
            and previous.get("exit_code") not in {None, 0}
        ):
            result_state = "invalid"
            reason = f"prior launcher exited {previous['exit_code']}"
        statuses[launch["run_id"]] = {
            "arm": launch["arm"],
            "state": "preserved" if result_state == "complete" else result_state,
            "reason": reason,
            "exit_code": None,
        }
        if result_state == "absent":
            if isinstance(previous, dict) and _same_live_process(
                previous.get("pid"), previous.get("process_start_ticks")
            ):
                statuses[launch["run_id"]].update(
                    state="orphan_running",
                    reason="a prior executor's exact child process is still alive",
                    pid=previous["pid"],
                    process_start_ticks=previous["process_start_ticks"],
                )
            else:
                queues[launch["arm"]].append(launch)

    running: list[_Running] = []
    started: Counter[str] = Counter()

    def publish() -> None:
        counts = Counter(record["state"] for record in statuses.values())
        _atomic_write_json(
            status_path,
            {
                "schema_version": 1,
                "launch_manifest_sha256": manifest["launch_manifest_sha256"],
                "counts": dict(sorted(counts.items())),
                "running": [item.launch["run_id"] for item in running],
                "runs": statuses,
            },
        )

    publish()
    try:
        while any(queues.values()) or running:
            launched = False
            while len(running) < jobs:
                arm = _choose_arm(arms, queues, running, started, jobs)
                if arm is None:
                    break
                launch = queues[arm].popleft()
                run_id = launch["run_id"]
                log_path = Path(launch["results"]) / "run.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                # Append on an interrupted resume: operational logs are evidence too and
                # must not be replaced merely because the result directory is still absent.
                log_handle = log_path.open("ab")
                environment = _sanitized_environment(launch["environment"])
                try:
                    process = subprocess.Popen(
                        launch["argv"],
                        cwd=working_directory,
                        env=environment,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                except OSError as exc:
                    log_handle.close()
                    statuses[run_id].update(
                        state="failed",
                        reason=f"cannot start launcher: {type(exc).__name__}: {exc}",
                        exit_code=None,
                        log=str(log_path.resolve()),
                    )
                    publish()
                    continue
                running.append(_Running(launch, process, log_handle, time.time()))
                started[arm] += 1
                statuses[run_id].update(
                    state="running",
                    reason=None,
                    pid=process.pid,
                    process_start_ticks=_process_start_ticks(process.pid),
                    log=str(log_path.resolve()),
                )
                publish()
                launched = True
                if spawn_stagger_seconds:
                    time.sleep(spawn_stagger_seconds)

            survivors: list[_Running] = []
            for item in running:
                exit_code = item.process.poll()
                if exit_code is None:
                    survivors.append(item)
                    continue
                item.log_handle.close()
                result_state, reason = _load_result(item.launch)
                completed = exit_code == 0 and result_state == "complete"
                statuses[item.launch["run_id"]].update(
                    state="complete" if completed else "failed",
                    reason=(
                        f"launcher exited {exit_code}"
                        if exit_code != 0
                        else reason
                    ),
                    exit_code=exit_code,
                    elapsed_seconds=round(time.time() - item.started_at, 3),
                )
            running = survivors
            publish()
            if (running or any(queues.values())) and not launched:
                time.sleep(poll_seconds)
    except BaseException:
        _terminate_running(running, statuses)
        publish()
        raise

    counts = Counter(record["state"] for record in statuses.values())
    result = {
        "schema_version": 1,
        "launch_manifest_sha256": manifest["launch_manifest_sha256"],
        "counts": dict(sorted(counts.items())),
        "runs": statuses,
        "success": not any(
            state in counts for state in ("invalid", "failed", "orphan_running", "interrupted")
        ),
    }
    _atomic_write_json(status_path, result)
    return result


def run_launch_manifest(
    *,
    manifest: dict[str, Any],
    state_dir: Path,
    working_directory: Path,
    jobs: int,
    spawn_stagger_seconds: float = 0.0,
    poll_seconds: float = 1.0,
) -> dict[str, Any]:
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = (state_dir / "executor.lock").open("a+")
    try:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise IntegrityError("another executor owns this state directory") from exc
        return _run_launch_manifest_locked(
            manifest=manifest,
            state_dir=state_dir,
            working_directory=working_directory,
            jobs=jobs,
            spawn_stagger_seconds=spawn_stagger_seconds,
            poll_seconds=poll_seconds,
        )
    finally:
        lock.close()


def load_and_run(
    *,
    launch_manifest: Path,
    state_dir: Path,
    working_directory: Path,
    jobs: int,
    spawn_stagger_seconds: float,
) -> dict[str, Any]:
    return run_launch_manifest(
        manifest=read_json(launch_manifest),
        state_dir=state_dir,
        working_directory=working_directory,
        jobs=jobs,
        spawn_stagger_seconds=spawn_stagger_seconds,
    )
