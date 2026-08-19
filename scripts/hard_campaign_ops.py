#!/usr/bin/env python
"""Create-only operational evidence for the truthful-hard campaign harness."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import shutil
import sys
from collections import Counter
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from freeze_hard_campaign import (  # noqa: E402
    REGIONS,
    _json_bytes,
    _read_json,
    _sha_bytes,
    _sha_file,
    _write_new,
    verify_campaign,
)


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _campaign_path(campaign_dir: Path, path: Path) -> Path:
    campaign = campaign_dir.resolve()
    resolved = path.resolve()
    if resolved != campaign and campaign not in resolved.parents:
        raise SystemExit(f"path escapes campaign directory: {resolved}")
    return resolved


def _log_ref(campaign_dir: Path, path: Path) -> dict:
    path = _campaign_path(campaign_dir, path)
    if not path.is_file():
        raise SystemExit(f"probe log missing: {path}")
    return {
        "path": os.path.relpath(path, campaign_dir.resolve()),
        "sha256": _sha_file(path),
        "size": path.stat().st_size,
    }


def _parse_large(path: Path) -> list[str]:
    lines = [
        line.strip() for line in path.read_text(errors="replace").splitlines()
        if line.strip()
    ]
    try:
        healthy = json.loads(lines[-1])
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"large probe has no terminal healthy JSON: {exc}")
    if (
        not isinstance(healthy, list)
        or set(healthy) != set(REGIONS)
        or len(healthy) != len(REGIONS)
    ):
        raise SystemExit(
            f"all exact regions must pass the large probe: {healthy!r}"
        )
    return healthy


def _parse_concurrency(path: Path) -> dict[str, int]:
    text = path.read_text(errors="replace")
    marker = "===== MAX SAFE CONCURRENCY (>=90% ok) ====="
    try:
        tail = text.split(marker, 1)[1]
        start = tail.index("{")
        value, _ = json.JSONDecoder().raw_decode(tail[start:])
        capacity = value["gpt-5.6-sol"]
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"invalid concurrency probe summary: {exc}")
    if (
        not isinstance(capacity, dict)
        or set(capacity) != set(REGIONS)
        or any(type(capacity[region]) is not int for region in REGIONS)
    ):
        raise SystemExit(f"concurrency capacity region/schema drift: {capacity}")
    return capacity


def publish_probe(
    campaign_dir: Path,
    label: str,
    block: int | None,
    run_ids: list[str],
    small: Path,
    large: Path,
    concurrency: Path,
) -> None:
    manifest = verify_campaign(campaign_dir, quiet=True)
    if (block is None) == (not run_ids):
        raise SystemExit("probe scope must be exactly one block or run-id list")
    by_id = {row["run_id"]: row for row in manifest["schedule"]}
    if block is not None:
        rows = [row for row in manifest["schedule"] if row["block"] == block]
        scope = {"block": block}
    else:
        if len(set(run_ids)) != len(run_ids) or not set(run_ids).issubset(by_id):
            raise SystemExit("refill probe run ids are invalid or duplicated")
        rows = [by_id[run_id] for run_id in run_ids]
        scope = {"refill_run_ids": sorted(run_ids)}
    if not rows:
        raise SystemExit("probe scope has no runs")
    small = _campaign_path(campaign_dir, small)
    large = _campaign_path(campaign_dir, large)
    concurrency = _campaign_path(campaign_dir, concurrency)
    if not small.is_file() or not small.read_text(errors="replace").strip():
        raise SystemExit("small probe log is missing or empty")
    healthy = _parse_large(large)
    capacity = _parse_concurrency(concurrency)
    load = dict(Counter(row["primary_region"] for row in rows))
    insufficient = {
        region: {
            "capacity": capacity[region],
            "scheduled_primary_load": load.get(region, 0),
        }
        for region in REGIONS
        if capacity[region] < load.get(region, 0)
    }
    if insufficient:
        raise SystemExit(
            "probe capacity cannot carry launch scope: "
            + json.dumps(insufficient, sort_keys=True)
        )
    target = campaign_dir.resolve() / "probes" / f"checkpoint_{label}.json"
    record = {
        "checkpoint": label,
        "published_at_utc": _utcnow(),
        "launch_scope": scope,
        "healthy_regions": healthy,
        "region_override_set": list(REGIONS),
        "concurrency_capacity": capacity,
        "scheduled_primary_load": load,
        "small_log": _log_ref(campaign_dir, small),
        "large_log": _log_ref(campaign_dir, large),
        "concurrency_log": _log_ref(campaign_dir, concurrency),
    }
    _write_new(target, record)
    print(label)


def _spec(manifest: dict, run_id: str) -> dict:
    matches = [row for row in manifest["schedule"] if row["run_id"] == run_id]
    if len(matches) != 1:
        raise SystemExit(f"run id is not exact manifest member: {run_id}")
    return matches[0]


def _attempt_number(campaign_dir: Path, run_id: str) -> int:
    root = campaign_dir / "excluded_attempts" / run_id
    attempts = []
    if root.is_dir():
        for path in root.glob("attempt_*"):
            try:
                attempts.append(int(path.name.split("_", 1)[1]))
            except (ValueError, IndexError):
                raise SystemExit(f"malformed archived attempt directory: {path}")
    if attempts and sorted(attempts) != list(range(1, max(attempts) + 1)):
        raise SystemExit(f"archived attempt numbering has gaps: {run_id}")
    return max(attempts, default=0) + 1


def write_receipt(
    campaign_dir: Path,
    run_id: str,
    probe_checkpoint: str,
) -> None:
    manifest = verify_campaign(campaign_dir, quiet=True)
    spec = _spec(manifest, run_id)
    checkpoint = (
        campaign_dir.resolve()
        / "probes"
        / f"checkpoint_{probe_checkpoint}.json"
    )
    if not checkpoint.is_file():
        raise SystemExit(f"probe checkpoint missing: {probe_checkpoint}")
    policy = manifest["runtime_environment_policy"]
    observed_set = {
        name: os.environ.get(name) for name in policy["set"]
    }
    if observed_set != policy["set"]:
        wrong = {
            name: {
                "actual": observed_set[name],
                "expected": expected,
            }
            for name, expected in policy["set"].items()
            if observed_set[name] != expected
        }
        raise SystemExit(f"frozen runtime environment not exact: {wrong}")
    per_run = set(policy["per_run_set"])
    expected_absent = [
        name for name in policy["required_absent_after_apply"]
        if name not in per_run
    ]
    actually_absent = [
        name for name in expected_absent if name not in os.environ
    ]
    if actually_absent != expected_absent:
        present = sorted(set(expected_absent) - set(actually_absent))
        raise SystemExit(f"sanitized names survived: {present}")
    allowed = set(policy["set"]) | per_run
    leaks = sorted(
        name for name in os.environ
        if any(
            name.startswith(prefix)
            for prefix in policy["sanitize_prefixes"]
        )
        and name not in allowed
    )
    if leaks:
        raise SystemExit(f"sanitized namespace leaks: {leaks}")
    expected_regions = json.dumps(
        {"gpt-5.6-sol": spec["region_order"]},
        separators=(",", ":"),
    )
    if os.environ.get("TRAPI_REGIONS_OVERRIDE") != expected_regions:
        raise SystemExit("TRAPI_REGIONS_OVERRIDE differs from manifest schedule")
    contract = {
        "caps": manifest["caps"],
        "runtime_dependencies_sha256": manifest[
            "runtime_dependencies"
        ]["sha256"],
        "runtime_environment_policy_sha256": policy["sha256"],
        "source_inventory_sha256": manifest["source_inventory_sha256"],
        "frozen_artifact_inventory_sha256": manifest[
            "frozen_artifact_inventory_sha256"
        ],
        "certification_sha256": manifest[
            "certification"
        ]["frozen_sha256"],
    }
    record = {
        "run_id": run_id,
        "block": spec["block"],
        "scenario": spec["scenario"],
        "condition": spec["condition"],
        "port": spec["port"],
        "primary_region": spec["primary_region"],
        "region_order": spec["region_order"],
        "attempt": _attempt_number(campaign_dir.resolve(), run_id),
        "probe_checkpoint": probe_checkpoint,
        "launched_at_utc": _utcnow(),
        "runtime_contract": {
            **contract,
            "observed_set": observed_set,
            "observed_absent": actually_absent,
            "sanitized_namespace_leaks": leaks,
            "trapi_regions_override": os.environ.get(
                "TRAPI_REGIONS_OVERRIDE"
            ),
            "cli_max_steps": manifest["caps"]["max_steps"],
            "contract_sha256": _sha_bytes(_json_bytes(contract)),
        },
    }
    target = campaign_dir.resolve() / "launch_receipts" / f"{run_id}.json"
    _write_new(target, record)
    print(f"receipt: {target}")


def _tree_manifest(root: Path) -> dict[str, dict]:
    if not root.exists():
        return {}
    if root.is_file():
        return {
            root.name: {
                "sha256": _sha_file(root),
                "size": root.stat().st_size,
            }
        }
    records = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        records[str(path.relative_to(root))] = {
            "sha256": _sha_file(path),
            "size": path.stat().st_size,
        }
    return records


def archive_refills(campaign_dir: Path, report_path: Path) -> None:
    manifest = verify_campaign(campaign_dir, quiet=True)
    report_path = _campaign_path(campaign_dir, report_path)
    report = _read_json(report_path)
    validity = report.get("validity") or {}
    if not validity.get("refillable"):
        raise SystemExit("report is not structurally safe/refillable")
    run_ids = validity.get("refill_run_ids") or []
    if not run_ids:
        raise SystemExit("refillable report has no run ids")
    by_id = {row["run_id"]: row for row in manifest["schedule"]}
    if not set(run_ids).issubset(by_id):
        raise SystemExit("report asks to refill a non-manifest run")
    excluded = report.get("validity", {}).get("excluded_runs") or {}
    for run_id in run_ids:
        spec = by_id[run_id]
        attempt = _attempt_number(campaign_dir.resolve(), run_id)
        target = (
            campaign_dir.resolve()
            / "excluded_attempts"
            / run_id
            / f"attempt_{attempt}"
        )
        if target.exists():
            raise SystemExit(f"refusing to replace archived attempt: {target}")
        target.mkdir(parents=True)
        sources = {
            "experiment": campaign_dir / spec["experiment_relpath"],
            "receipt.json": campaign_dir / "launch_receipts" / f"{run_id}.json",
            "launcher.log": campaign_dir / spec["launcher_log_relpath"],
        }
        before = {
            label: _tree_manifest(path)
            for label, path in sources.items()
            if path.exists()
        }
        for label, source in sources.items():
            if source.exists():
                destination = target / label
                destination.parent.mkdir(parents=True, exist_ok=True)
                source.rename(destination)
        shutil.copy2(report_path, target / "screening_report.json")
        record = {
            "run_id": run_id,
            "attempt": attempt,
            "archived_at_utc": _utcnow(),
            "reasons": excluded.get(run_id) or ["expected summary missing"],
            "recoverable": True,
            "moved_inputs": sorted(before),
            "pre_move_inventory": before,
            "screening_report_sha256": _sha_file(
                target / "screening_report.json"
            ),
        }
        _write_new(target / "archive_record.json", record)
        print(f"archived excluded attempt: {target}")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("publish-probe")
    probe.add_argument("--campaign-dir", required=True, type=Path)
    probe.add_argument("--label", required=True)
    scope = probe.add_mutually_exclusive_group(required=True)
    scope.add_argument("--block", type=int, choices=(1, 2))
    scope.add_argument("--run-id", action="append")
    probe.add_argument("--small", required=True, type=Path)
    probe.add_argument("--large", required=True, type=Path)
    probe.add_argument("--concurrency", required=True, type=Path)

    receipt = sub.add_parser("write-receipt")
    receipt.add_argument("--campaign-dir", required=True, type=Path)
    receipt.add_argument("--run-id", required=True)
    receipt.add_argument("--probe-checkpoint", required=True)

    archive = sub.add_parser("archive-refills")
    archive.add_argument("--campaign-dir", required=True, type=Path)
    archive.add_argument("--report", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "publish-probe":
        publish_probe(
            args.campaign_dir,
            args.label,
            args.block,
            args.run_id or [],
            args.small,
            args.large,
            args.concurrency,
        )
    elif args.command == "write-receipt":
        write_receipt(
            args.campaign_dir,
            args.run_id,
            args.probe_checkpoint,
        )
    elif args.command == "archive-refills":
        archive_refills(args.campaign_dir, args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
