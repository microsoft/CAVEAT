from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import clone8_leaderboard_monitor as monitor


NOW = dt.datetime(2026, 8, 1, 0, 0, tzinfo=dt.timezone.utc)


def _write_campaign(tmp_path: Path, **status_overrides) -> Path:
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    manifest = {
        "schedule": {
            "default_jobs": 2,
            "host_browser_root_ceiling": 3,
            "reserved_external_browser_roots": 1,
        },
        "runs": [{"port": 20000}, {"port": 20001}],
    }
    status = {
        "updated_utc": "2026-08-01T00:00:00Z",
        "launcher_pid": 100,
        "state": "running",
        "jobs": 2,
        "total_runs": 2,
        "final_count": 0,
        "pending_primary": 1,
        "pending_refill": 0,
        "running_count": 1,
        "running": [{
            "run_id": "airbnb_r1/run",
            "attempt": 1,
            "pid": 200,
            "port": 20000,
            "started_utc": "2026-07-31T23:55:00Z",
        }],
        "final_classes": {},
    }
    status.update(status_overrides)
    (campaign / "campaign_manifest.json").write_text(json.dumps(manifest))
    (campaign / "status.json").write_text(json.dumps(status))
    (campaign / "launcher.pid").write_text("100\n")
    return campaign


def _processes(campaign: Path) -> dict[int, dict]:
    return {
        1: {"ppid": 0, "argv": ["/sbin/init"]},
        100: {
            "ppid": 1,
            "argv": [
                "python",
                "scripts/clone8_leaderboard_campaign.py",
                "--campaign",
                str(campaign),
                "launch",
            ],
        },
        200: {
            "ppid": 100,
            "argv": ["python", "-m", "agentarena.run_cell"],
        },
        201: {"ppid": 200, "argv": ["python", "-m", "backend.app"]},
        202: {
            "ppid": 200,
            "argv": ["/opt/chrome", "--remote-debugging-port=45000"],
        },
    }


def _inspect(campaign: Path, **overrides) -> dict:
    values = {
        "now": NOW,
        "proc_records": _processes(campaign),
        "listeners": {20000: {201}},
        "disk_free_bytes": 30 * monitor.GIB,
    }
    values.update(overrides)
    return monitor.inspect_campaign(campaign, **values)


def _codes(snapshot: dict) -> set[str]:
    return {record["code"] for record in snapshot["alerts"]}


def test_healthy_active_campaign_is_green(tmp_path: Path) -> None:
    campaign = _write_campaign(tmp_path)
    snapshot = _inspect(campaign)
    assert snapshot["ok"] is True
    assert snapshot["alerts"] == []
    assert snapshot["host_processes"]["campaign_workers"] == [200]
    assert snapshot["ports"]["active"] == [20000]


def test_stale_status_and_bad_launcher_alert(tmp_path: Path) -> None:
    campaign = _write_campaign(
        tmp_path, updated_utc="2026-07-31T23:50:00Z"
    )
    processes = _processes(campaign)
    processes[100]["argv"][-1] = "status"
    codes = _codes(_inspect(campaign, proc_records=processes))
    assert {"stale_status", "launcher_identity_mismatch"} <= codes


def test_host_admission_wait_is_active_and_zero_progress_alerts(
    tmp_path: Path,
) -> None:
    campaign = _write_campaign(
        tmp_path,
        state="host_admission_wait",
        last_progress_utc="2026-07-31T22:00:00Z",
        host_admission={"schema": "agentarena.clone8-host-admission.v1"},
    )
    snapshot = _inspect(campaign, zero_progress_seconds=3600)
    assert snapshot["state"] == "host_admission_wait"
    assert snapshot["status"]["host_admission"] == {
        "schema": "agentarena.clone8-host-admission.v1"
    }
    assert "zero_progress" in _codes(snapshot)


def test_dead_launcher_and_pid_file_mismatch_alert(tmp_path: Path) -> None:
    campaign = _write_campaign(tmp_path)
    (campaign / "launcher.pid").write_text("999\n")
    processes = _processes(campaign)
    del processes[100]
    codes = _codes(_inspect(campaign, proc_records=processes))
    assert {"launcher_dead", "launcher_pid_file_mismatch"} <= codes


def test_scientific_invalid_and_running_over_frozen_jobs_alert(
    tmp_path: Path,
) -> None:
    running = [
        {
            "pid": pid,
            "port": 20000 + offset,
            "started_utc": "2026-07-31T23:55:00Z",
        }
        for offset, pid in enumerate((200, 210, 220))
    ]
    campaign = _write_campaign(
        tmp_path,
        running_count=3,
        running=running,
        final_classes={"scientific_invalid": 1},
    )
    codes = _codes(_inspect(campaign))
    assert {"scientific_invalid", "running_over_frozen_jobs"} <= codes


def test_external_worker_and_browser_limits_alert(tmp_path: Path) -> None:
    campaign = _write_campaign(tmp_path)
    processes = _processes(campaign)
    processes.update({
        300: {
            "ppid": 1,
            "argv": ["python", "-m", "agentarena.run_cell"],
        },
        301: {
            "ppid": 1,
            "argv": ["/opt/chrome", "--remote-debugging-port=46001"],
        },
        302: {
            "ppid": 1,
            "argv": ["/opt/chrome", "--remote-debugging-port=46002"],
        },
        303: {
            "ppid": 1,
            "argv": ["/opt/chrome", "--remote-debugging-port=46003"],
        },
    })
    codes = _codes(_inspect(campaign, proc_records=processes))
    assert {
        "external_run_cells",
        "external_browser_reserve_exceeded",
        "browser_ceiling_exceeded",
    } <= codes


def test_active_and_inactive_port_faults_alert(tmp_path: Path) -> None:
    campaign = _write_campaign(tmp_path)
    processes = _processes(campaign)
    processes[999] = {"ppid": 1, "argv": ["foreign-server"]}
    codes = _codes(_inspect(
        campaign,
        proc_records=processes,
        listeners={20000: {999}, 20001: {999}},
    ))
    assert {
        "active_port_owner_mismatch",
        "inactive_campaign_port_collision",
    } <= codes


def test_missing_mature_active_port_and_low_disk_alert(tmp_path: Path) -> None:
    campaign = _write_campaign(tmp_path)
    codes = _codes(_inspect(
        campaign,
        listeners={},
        disk_free_bytes=24 * monitor.GIB,
    ))
    assert {"active_port_not_listening", "low_disk"} <= codes


def test_once_appends_snapshot_and_returns_health(tmp_path: Path) -> None:
    campaign = _write_campaign(tmp_path)
    samples = iter([
        {"observed_utc": "2026-08-01T00:00:00Z", "state": "running", "ok": True, "alerts": []},
        {"observed_utc": "2026-08-01T00:30:00Z", "state": "running", "ok": False,
         "alerts": [{"code": "stale_status", "message": "stale"}]},
    ])

    def inspect(*_args, **_kwargs):
        return next(samples)

    assert monitor.run_monitor(
        campaign,
        once=True,
        interval_seconds=1800,
        stale_seconds=120,
        disk_min_bytes=25 * monitor.GIB,
        port_grace_seconds=120,
        inspect=inspect,
    ) == 0
    assert monitor.run_monitor(
        campaign,
        once=True,
        interval_seconds=1800,
        stale_seconds=120,
        disk_min_bytes=25 * monitor.GIB,
        port_grace_seconds=120,
        inspect=inspect,
    ) == 1
    records = [json.loads(line) for line in (campaign / "monitor.jsonl").read_text().splitlines()]
    assert [record["ok"] for record in records] == [True, False]


def test_once_cli_certifies_terminal_campaign(tmp_path: Path) -> None:
    campaign = _write_campaign(tmp_path)
    status_path = campaign / "status.json"
    status = json.loads(status_path.read_text())
    status.update({
        "updated_utc": monitor._utc(),
        "state": "complete",
        "final_count": 2,
        "pending_primary": 0,
        "running_count": 0,
        "running": [],
    })
    status_path.write_text(json.dumps(status))
    assert monitor.main([
        "--campaign",
        str(campaign),
        "--once",
        "--disk-min-gib",
        "0",
    ]) == 0
    record = json.loads((campaign / "monitor.jsonl").read_text().splitlines()[-1])
    assert record["state"] == "complete"
    assert record["ok"] is True
