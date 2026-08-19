#!/usr/bin/env python
"""Alert-only health monitor for a clone-eight leaderboard campaign.

The monitor reads campaign artifacts and host state, then appends one JSON
snapshot to ``monitor.jsonl``.  It never takes the launcher lock, probes model
capacity, signals processes, launches work, or writes inside a run directory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.clone8_continuation_amendment import (
    STATUS_AMENDMENT_FIELD,
    STATUS_SCHEDULER_FIELD,
    ContinuationAmendmentError,
    read_monitor_overlay,
)

DEFAULT_CAMPAIGN = ROOT / "results" / "eight_env_leaderboard"
DEFAULT_INTERVAL_SECONDS = 1800.0
DEFAULT_STALE_SECONDS = 120.0
DEFAULT_DISK_MIN_GIB = 25.0
DEFAULT_PORT_GRACE_SECONDS = 120.0
DEFAULT_ZERO_PROGRESS_SECONDS = 3600.0
ACTIVE_STATES = {
    "running", "availability_wait", "host_admission_wait", "stopping"
}
TERMINAL_STATES = {"complete", "stopped"}
KNOWN_STATES = ACTIVE_STATES | TERMINAL_STATES | {"protocol_invalid"}
GIB = 1024**3


def _utc(now: dt.datetime | None = None) -> str:
    value = now or dt.datetime.now(dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: Any) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp is not a string")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(dt.timezone.utc)


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return value


def _proc_snapshot(proc_root: Path = Path("/proc")) -> dict[int, dict]:
    records: dict[int, dict] = {}
    for proc in proc_root.iterdir():
        if not proc.name.isdigit():
            continue
        try:
            suffix = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            argv = [
                part.decode(errors="replace")
                for part in (proc / "cmdline").read_bytes().split(b"\0")
                if part
            ]
            records[int(proc.name)] = {"ppid": int(suffix[1]), "argv": argv}
        except (OSError, ValueError, IndexError):
            continue
    return records


def _listening_ports() -> dict[int, set[int]]:
    result = subprocess.run(
        ["ss", "-ltnpH"], check=True, capture_output=True, text=True
    )
    listeners: dict[int, set[int]] = {}
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        tail = fields[3].rsplit(":", 1)[-1]
        if not tail.isdigit():
            continue
        listeners.setdefault(int(tail), set()).update(
            int(pid) for pid in re.findall(r"pid=(\d+)", line)
        )
    return listeners


def _is_descendant(
    pid: int, ancestors: set[int], records: dict[int, dict]
) -> bool:
    seen: set[int] = set()
    while pid in records and pid not in seen:
        if pid in ancestors:
            return True
        seen.add(pid)
        pid = records[pid]["ppid"]
    return False


def _is_run_cell(argv: list[str]) -> bool:
    return any(
        argv[index] == "-m" and argv[index + 1] == "agentarena.run_cell"
        for index in range(len(argv) - 1)
    )


def _is_browser_root(argv: list[str]) -> bool:
    if not argv:
        return False
    executable = Path(argv[0]).name.lower()
    return (
        ("chrome" in executable or "chromium" in executable)
        and any(arg.startswith("--remote-debugging-port=") for arg in argv)
        and not any(arg.startswith("--type=") for arg in argv)
    )


def _launcher_matches(argv: list[str], campaign: Path) -> bool:
    if not any(Path(arg).name == "clone8_leaderboard_campaign.py" for arg in argv):
        return False
    if "launch" not in argv:
        return False
    supplied: str | None = None
    for index, arg in enumerate(argv):
        if arg == "--campaign" and index + 1 < len(argv):
            supplied = argv[index + 1]
            break
        if arg.startswith("--campaign="):
            supplied = arg.split("=", 1)[1]
            break
    if supplied is None:
        return False
    return Path(supplied).resolve() == campaign.resolve()


def _alert(alerts: list[dict], code: str, message: str, **evidence: Any) -> None:
    record = {"code": code, "message": message}
    if evidence:
        record["evidence"] = evidence
    alerts.append(record)


def inspect_campaign(
    campaign: Path,
    *,
    stale_seconds: float = DEFAULT_STALE_SECONDS,
    disk_min_bytes: int = int(DEFAULT_DISK_MIN_GIB * GIB),
    port_grace_seconds: float = DEFAULT_PORT_GRACE_SECONDS,
    zero_progress_seconds: float = DEFAULT_ZERO_PROGRESS_SECONDS,
    now: dt.datetime | None = None,
    proc_records: dict[int, dict] | None = None,
    listeners: dict[int, set[int]] | None = None,
    disk_free_bytes: int | None = None,
) -> dict:
    """Collect one read-only host/campaign snapshot."""
    campaign = campaign.resolve()
    observed = now or dt.datetime.now(dt.timezone.utc)
    observed = observed.astimezone(dt.timezone.utc)
    alerts: list[dict] = []
    snapshot: dict[str, Any] = {
        "schema": "agentarena.clone8-monitor-snapshot.v1",
        "observed_utc": _utc(observed),
        "campaign": str(campaign),
        "alerts": alerts,
    }

    try:
        manifest = _read_json(campaign / "campaign_manifest.json")
        status = _read_json(campaign / "status.json")
    except Exception as exc:
        _alert(
            alerts,
            "campaign_state_unreadable",
            "campaign manifest or status cannot be read",
            error=f"{type(exc).__name__}: {exc}",
        )
        snapshot["ok"] = False
        return snapshot

    schedule = manifest.get("schedule") or {}
    try:
        continuation = read_monitor_overlay(campaign)
    except ContinuationAmendmentError as exc:
        continuation = None
        _alert(
            alerts,
            "continuation_amendment_invalid",
            "published continuation amendment failed verification",
            error=f"{type(exc).__name__}: {exc}",
        )
    snapshot["continuation"] = continuation
    state = status.get("state")
    snapshot["state"] = state
    snapshot["status"] = {
        key: status.get(key)
        for key in (
            "updated_utc",
            "launcher_pid",
            "final_count",
            "total_runs",
            "pending_primary",
            "pending_refill",
            "running_count",
            "final_classes",
            "host_admission",
            "host_admission_retry_not_before_utc",
            "last_progress_utc",
        )
    }
    if state not in KNOWN_STATES:
        _alert(alerts, "unknown_campaign_state", "status has an unknown state", state=state)
    if state == "protocol_invalid":
        _alert(alerts, "protocol_invalid", "campaign entered protocol-invalid state")

    try:
        status_age = (observed - _parse_utc(status.get("updated_utc"))).total_seconds()
        snapshot["status_age_seconds"] = round(status_age, 3)
        if status_age < -5:
            _alert(
                alerts,
                "status_timestamp_in_future",
                "status timestamp is materially in the future",
                age_seconds=round(status_age, 3),
            )
        if state in ACTIVE_STATES and status_age > stale_seconds:
            _alert(
                alerts,
                "stale_status",
                "active campaign status is stale",
                age_seconds=round(status_age, 3),
                limit_seconds=stale_seconds,
            )
    except Exception as exc:
        _alert(
            alerts,
            "invalid_status_timestamp",
            "status update timestamp is invalid",
            error=f"{type(exc).__name__}: {exc}",
        )

    if state in ACTIVE_STATES and status.get("last_progress_utc") is not None:
        try:
            progress_age = (
                observed - _parse_utc(status["last_progress_utc"])
            ).total_seconds()
            snapshot["progress_age_seconds"] = round(progress_age, 3)
            unfinished = (
                int(status.get("pending_primary", 0) or 0)
                + int(status.get("pending_refill", 0) or 0)
                + int(status.get("running_count", 0) or 0)
            )
            if unfinished > 0 and progress_age > zero_progress_seconds:
                _alert(
                    alerts,
                    "zero_progress",
                    "active campaign has made no launch/completion progress",
                    age_seconds=round(progress_age, 3),
                    limit_seconds=zero_progress_seconds,
                    state=state,
                    unfinished=unfinished,
                )
        except Exception as exc:
            _alert(
                alerts,
                "invalid_progress_timestamp",
                "last-progress timestamp is invalid",
                error=f"{type(exc).__name__}: {exc}",
            )

    final_classes = status.get("final_classes") or {}
    try:
        invalid_count = int(final_classes.get("scientific_invalid", 0))
    except (TypeError, ValueError):
        invalid_count = -1
        _alert(
            alerts,
            "invalid_classification_count",
            "scientific-invalid count is not an integer",
        )
    if invalid_count > 0:
        _alert(
            alerts,
            "scientific_invalid",
            "campaign contains a scientific-invalid attempt",
            count=invalid_count,
        )

    try:
        frozen_jobs = int(schedule["default_jobs"])
        browser_ceiling = int(schedule["host_browser_root_ceiling"])
        external_browser_reserve = int(schedule["reserved_external_browser_roots"])
    except (KeyError, TypeError, ValueError) as exc:
        _alert(
            alerts,
            "invalid_frozen_limits",
            "manifest lacks valid frozen monitor limits",
            error=f"{type(exc).__name__}: {exc}",
        )
        frozen_jobs = browser_ceiling = external_browser_reserve = 0
    snapshot["frozen_limits"] = {
        "jobs": frozen_jobs,
        "host_browser_roots": browser_ceiling,
        "external_browser_roots": external_browser_reserve,
    }
    active_jobs = frozen_jobs
    active_browser_ceiling = browser_ceiling
    active_external_browser_reserve = external_browser_reserve
    active_regional_caps: dict[str, dict[str, int]] | None = None
    active_logical_caps: dict[str, int] | None = None
    if continuation is not None:
        limits = continuation["active_limits"]
        active_jobs = int(limits["jobs"])
        active_browser_ceiling = int(limits["host_browser_root_ceiling"])
        active_external_browser_reserve = int(
            limits["reserved_external_browser_roots"]
        )
        active_regional_caps = limits["primary_region_deployment_caps"]
        active_logical_caps = limits["logical_deployment_caps"]
        if status.get(STATUS_AMENDMENT_FIELD) != continuation["amendment_sha256"]:
            _alert(
                alerts,
                "continuation_status_binding_mismatch",
                "status is not bound to the published continuation amendment",
                status_sha256=status.get(STATUS_AMENDMENT_FIELD),
                amendment_sha256=continuation["amendment_sha256"],
            )
        if status.get(STATUS_SCHEDULER_FIELD) != continuation["scheduler_sha256"]:
            _alert(
                alerts,
                "continuation_scheduler_binding_mismatch",
                "status is not bound to the active continuation scheduler",
                status_sha256=status.get(STATUS_SCHEDULER_FIELD),
                scheduler_sha256=continuation["scheduler_sha256"],
            )
    snapshot["active_limits"] = {
        "jobs": active_jobs,
        "host_browser_roots": active_browser_ceiling,
        "external_browser_roots": active_external_browser_reserve,
        "primary_region_deployment_caps": active_regional_caps,
        "logical_deployment_caps": active_logical_caps,
    }

    running = status.get("running")
    if not isinstance(running, list):
        running = []
        _alert(alerts, "invalid_running_inventory", "status running inventory is not a list")
    try:
        running_count = int(status.get("running_count", len(running)))
    except (TypeError, ValueError):
        running_count = -1
        _alert(alerts, "invalid_running_count", "running_count is not an integer")
    if running_count != len(running):
        _alert(
            alerts,
            "running_count_mismatch",
            "running_count disagrees with the running inventory",
            count=running_count,
            inventory=len(running),
        )
    if active_jobs >= 0 and running_count > active_jobs:
        _alert(
            alerts,
            (
                "running_over_continuation_jobs"
                if continuation is not None
                else "running_over_frozen_jobs"
            ),
            "running workers exceed the active job count",
            running=running_count,
            active_jobs=active_jobs,
        )
    if status.get("jobs") is not None and status.get("jobs") != active_jobs:
        _alert(
            alerts,
            "status_jobs_drift",
            "status jobs differs from the active scheduler",
            status_jobs=status.get("jobs"),
            active_jobs=active_jobs,
        )

    if active_regional_caps is not None and active_logical_caps is not None:
        logical_counts: dict[str, int] = {}
        regional_counts: dict[tuple[str, str], int] = {}
        for item in running:
            logical = item.get("logical")
            primary = item.get("primary_region")
            if logical not in active_logical_caps or primary not in (
                active_regional_caps.get(logical) or {}
            ):
                _alert(
                    alerts,
                    "running_quota_identity_invalid",
                    "running record lacks an active logical/primary quota identity",
                    run_id=item.get("run_id"),
                    logical=logical,
                    primary_region=primary,
                )
                continue
            logical_counts[logical] = logical_counts.get(logical, 0) + 1
            key = (logical, primary)
            regional_counts[key] = regional_counts.get(key, 0) + 1
        for logical, count in sorted(logical_counts.items()):
            cap = active_logical_caps[logical]
            if count > cap:
                _alert(
                    alerts,
                    "logical_deployment_cap_exceeded",
                    "active logical deployment count exceeds continuation cap",
                    logical=logical,
                    observed=count,
                    cap=cap,
                )
        for (logical, primary), count in sorted(regional_counts.items()):
            cap = active_regional_caps[logical][primary]
            if count > cap:
                _alert(
                    alerts,
                    "primary_region_deployment_cap_exceeded",
                    "active logical/primary count exceeds continuation cap",
                    logical=logical,
                    primary_region=primary,
                    observed=count,
                    cap=cap,
                )

    if disk_free_bytes is None:
        disk_free_bytes = shutil.disk_usage(campaign).free
    snapshot["disk_free_bytes"] = disk_free_bytes
    if disk_free_bytes < disk_min_bytes:
        _alert(
            alerts,
            "low_disk",
            "campaign filesystem is below the free-disk floor",
            free_bytes=disk_free_bytes,
            minimum_bytes=disk_min_bytes,
        )

    if state not in ACTIVE_STATES:
        snapshot["ok"] = not alerts
        return snapshot

    if proc_records is None:
        proc_records = _proc_snapshot()
    if listeners is None:
        listeners = _listening_ports()

    campaign_workers: set[int] = set()
    active_ports: dict[int, dict] = {}
    for item in running:
        try:
            pid = int(item["pid"])
            port = int(item["port"])
        except (KeyError, TypeError, ValueError):
            _alert(
                alerts,
                "invalid_running_record",
                "running record lacks an integer pid or port",
                record=item,
            )
            continue
        campaign_workers.add(pid)
        if port in active_ports:
            _alert(
                alerts,
                "duplicate_active_port",
                "multiple running records claim one port",
                port=port,
            )
        active_ports[port] = item
        if pid not in proc_records:
            _alert(
                alerts,
                "running_worker_missing",
                "a status-listed worker process is absent",
                pid=pid,
                port=port,
            )

    launcher_pid = status.get("launcher_pid")
    try:
        launcher_pid = int(launcher_pid)
    except (TypeError, ValueError):
        launcher_pid = -1
    launcher_record = proc_records.get(launcher_pid)
    if launcher_record is None:
        _alert(
            alerts,
            "launcher_dead",
            "active campaign launcher process is absent",
            pid=launcher_pid,
        )
    elif not _launcher_matches(launcher_record["argv"], campaign):
        _alert(
            alerts,
            "launcher_identity_mismatch",
            "launcher pid belongs to a different command or campaign",
            pid=launcher_pid,
            argv=launcher_record["argv"],
        )
    try:
        recorded_pid = int((campaign / "launcher.pid").read_text().strip())
    except Exception:
        recorded_pid = -1
    if recorded_pid != launcher_pid:
        _alert(
            alerts,
            "launcher_pid_file_mismatch",
            "launcher.pid disagrees with active status",
            status_pid=launcher_pid,
            file_pid=recorded_pid,
        )

    run_cells = {
        pid for pid, record in proc_records.items() if _is_run_cell(record["argv"])
    }
    browser_roots = {
        pid for pid, record in proc_records.items() if _is_browser_root(record["argv"])
    }
    external_run_cells = sorted(run_cells - campaign_workers)
    external_browser_roots = sorted(
        pid
        for pid in browser_roots
        if not _is_descendant(pid, campaign_workers, proc_records)
    )
    snapshot["host_processes"] = {
        "campaign_workers": sorted(campaign_workers),
        "run_cells": sorted(run_cells),
        "external_run_cells": external_run_cells,
        "browser_roots": sorted(browser_roots),
        "external_browser_roots": external_browser_roots,
    }
    if external_run_cells:
        _alert(
            alerts,
            "external_run_cells",
            "another agentarena.run_cell worker is active",
            pids=external_run_cells,
        )
    if len(browser_roots) > active_browser_ceiling:
        _alert(
            alerts,
            "browser_ceiling_exceeded",
            "browser roots exceed the frozen host ceiling",
            observed=len(browser_roots),
            ceiling=active_browser_ceiling,
        )
    if len(external_browser_roots) > active_external_browser_reserve:
        _alert(
            alerts,
            "external_browser_reserve_exceeded",
            "external browser roots exceed the frozen reserve",
            pids=external_browser_roots,
            observed=len(external_browser_roots),
            reserve=active_external_browser_reserve,
        )

    manifest_ports: list[int] = []
    for row in manifest.get("runs") or []:
        try:
            manifest_ports.append(int(row["port"]))
        except (KeyError, TypeError, ValueError):
            _alert(alerts, "invalid_manifest_port", "manifest contains an invalid run port")
            break
    expected_ports = set(manifest_ports)
    if len(expected_ports) != len(manifest_ports):
        _alert(alerts, "duplicate_manifest_port", "manifest run ports are not unique")
    campaign_descendants = {
        pid
        for pid in proc_records
        if _is_descendant(pid, campaign_workers, proc_records)
    }

    for port, item in active_ports.items():
        if port not in expected_ports:
            _alert(
                alerts,
                "active_port_not_frozen",
                "running record uses a port absent from the manifest",
                port=port,
            )
        owners = listeners.get(port)
        try:
            run_age = (observed - _parse_utc(item.get("started_utc"))).total_seconds()
        except Exception:
            run_age = port_grace_seconds + 1
            _alert(
                alerts,
                "invalid_run_start_timestamp",
                "running record has an invalid start timestamp",
                port=port,
            )
        if owners is None:
            if run_age > port_grace_seconds:
                _alert(
                    alerts,
                    "active_port_not_listening",
                    "mature running worker has no listener on its frozen port",
                    port=port,
                    run_age_seconds=round(run_age, 3),
                )
            continue
        worker = {int(item["pid"])}
        if not owners or any(
            not _is_descendant(owner, worker, proc_records) for owner in owners
        ):
            _alert(
                alerts,
                "active_port_owner_mismatch",
                "active run port is owned by an unknown or foreign process",
                port=port,
                owners=sorted(owners),
                worker=next(iter(worker)),
            )

    for port in sorted(expected_ports - set(active_ports)):
        owners = listeners.get(port)
        if owners is None:
            continue
        if not owners or any(owner not in campaign_descendants for owner in owners):
            _alert(
                alerts,
                "inactive_campaign_port_collision",
                "an inactive frozen run port has a foreign listener",
                port=port,
                owners=sorted(owners),
            )

    snapshot["ports"] = {
        "active": sorted(active_ports),
        "listening_frozen": sorted(expected_ports & set(listeners)),
    }
    snapshot["ok"] = not alerts
    return snapshot


def append_snapshot(campaign: Path, snapshot: dict) -> Path:
    """Append exactly one snapshot without touching measured artifacts."""
    path = campaign.resolve() / "monitor.jsonl"
    raw = (
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    return path


def run_monitor(
    campaign: Path,
    *,
    once: bool,
    interval_seconds: float,
    stale_seconds: float,
    disk_min_bytes: int,
    port_grace_seconds: float,
    zero_progress_seconds: float = DEFAULT_ZERO_PROGRESS_SECONDS,
    inspect: Callable[..., dict] = inspect_campaign,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    while True:
        try:
            snapshot = inspect(
                campaign,
                stale_seconds=stale_seconds,
                disk_min_bytes=disk_min_bytes,
                port_grace_seconds=port_grace_seconds,
                zero_progress_seconds=zero_progress_seconds,
            )
        except Exception as exc:
            snapshot = {
                "schema": "agentarena.clone8-monitor-snapshot.v1",
                "observed_utc": _utc(),
                "campaign": str(campaign.resolve()),
                "ok": False,
                "alerts": [{
                    "code": "monitor_inspection_error",
                    "message": "monitor inspection raised unexpectedly",
                    "evidence": {"error": f"{type(exc).__name__}: {exc}"},
                }],
            }
        log_path = append_snapshot(campaign, snapshot)
        stream = sys.stdout if snapshot.get("ok") else sys.stderr
        print(
            json.dumps({
                "observed_utc": snapshot["observed_utc"],
                "state": snapshot.get("state"),
                "ok": snapshot.get("ok", False),
                "alerts": [item["code"] for item in snapshot.get("alerts", [])],
                "log": str(log_path),
            }, sort_keys=True),
            file=stream,
            flush=True,
        )
        if not snapshot.get("ok"):
            return 1
        if once or snapshot.get("state") in TERMINAL_STATES:
            return 0
        sleep(interval_seconds)


def _nonnegative(parser: argparse.ArgumentParser, name: str, value: float) -> float:
    if value < 0:
        parser.error(f"{name} must be nonnegative")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, default=DEFAULT_CAMPAIGN)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--stale-seconds", type=float, default=DEFAULT_STALE_SECONDS)
    parser.add_argument("--disk-min-gib", type=float, default=DEFAULT_DISK_MIN_GIB)
    parser.add_argument(
        "--port-grace-seconds", type=float, default=DEFAULT_PORT_GRACE_SECONDS
    )
    parser.add_argument(
        "--zero-progress-seconds",
        type=float,
        default=DEFAULT_ZERO_PROGRESS_SECONDS,
    )
    args = parser.parse_args(argv)
    if args.interval <= 0:
        parser.error("--interval must be positive")
    _nonnegative(parser, "--stale-seconds", args.stale_seconds)
    _nonnegative(parser, "--disk-min-gib", args.disk_min_gib)
    _nonnegative(parser, "--port-grace-seconds", args.port_grace_seconds)
    _nonnegative(
        parser, "--zero-progress-seconds", args.zero_progress_seconds
    )
    if not args.campaign.is_dir():
        parser.error(f"campaign directory does not exist: {args.campaign}")
    try:
        return run_monitor(
            args.campaign,
            once=args.once,
            interval_seconds=args.interval,
            stale_seconds=args.stale_seconds,
            disk_min_bytes=int(args.disk_min_gib * GIB),
            port_grace_seconds=args.port_grace_seconds,
            zero_progress_seconds=args.zero_progress_seconds,
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
