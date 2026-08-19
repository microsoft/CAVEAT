#!/usr/bin/env python3
"""Frozen, create-only 112-run hard-only mode-evidence recovery campaign.

The launcher deliberately keeps all treatments command-local.  It does not
edit production tasks, catalogs, steering files, frontend assets, or scaffold
registrations.  ``prepare`` binds the exact schedule and every measured code
surface; later commands fail closed on drift.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRIPT_DIR = ROOT / "scripts"
HARNESS_DIR = HERE / "harness"
LEGACY_COMPONENT_DIR = ROOT / "ablations" / "harness_components"
PROMPT_ONLY_DIR = ROOT / "ablations" / "prompt_only"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from hard_campaign_runtime import (  # noqa: E402
    CAPS,
    _runtime_environment_policy,
    _validate_caps,
    _find_browser_executable,
    code_inventory,
    runtime_dependency_manifest,
    runtime_limit_contract,
    validate_limit_audit,
)
from harness_eval_campaign import (  # noqa: E402
    _validate_certification,
    _validate_lockdiff,
    audit_refill_coexistence,
    validate_probe_evidence,
)


SCHEMA_VERSION = 1
KIND = "further_mode_ablation_hard_v2_campaign"
REPORT_KIND = "further_mode_ablation_hard_v2_run_report"
DEFAULT_CAMPAIGN = ROOT / "results" / "further_mode_ablation_hard_v2"
PREREG_PATH = HERE / "preregistration.json"
SIDECAR_PATH = HERE / "objective_order_sidecar.json"
GOLD_CONTRACT_PATH = HERE / "gold_task_contract.json"
RECOVERY_AMENDMENT_PATH = HERE / "recovery_amendment.json"
V19_MANIFEST = (
    ROOT / "results" / "harness_deliberative_ab_confirmatory_v19"
    / "campaign_manifest.json"
)
V1_MANIFEST = (
    ROOT / "results" / "further_mode_ablation_v1"
    / "campaign_manifest.json"
)

REGIONS = ("gcr/shared", "msraif/shared", "redmond/interactive")
LOGICAL_MODEL = "gpt-5.6-sol"
LOW_REQUEST = "gpt-5.6-sol#low"
LOW_RECORDED = "gpt-5.6-sol-low"
HIGH_REQUEST = "gpt-5.6-sol#high"
HIGH_RECORDED = "gpt-5.6-sol-high"
HARD_SCENARIO = "laptop_hard"
HARD_VARIANT = "graded"
REPETITIONS = tuple(range(1, 9))
SCHEDULE_SEED = "further-mode-ablation-hard-v2-20260805"
MAX_PARALLEL_RUNS = 32
MAX_BLOCK_SIZE = 28
PROBE_MAX_AGE_SECONDS = 3600
HOST_CAPACITY_RECEIPT_MAX_AGE_SECONDS = 3600
LIMIT_NEAR_FRACTION = 0.25
HOST_BROWSER_CEILING = 36
REFILL_BROWSER_RESERVE = 4
LAUNCH_CONFIRMATION = "LAUNCH-FROZEN-FURTHER-MODE-ABLATION-HARD-V2"
MAX_ATTEMPTS = 3
INFRA_CLASSIFIER_PATH = HERE / "infra_classifier.py"
UPSTREAM_INFRA_CLASSIFIER_PATH = SCRIPT_DIR / "_infra_classify.py"
PROTECTED_PORT_RANGE = range(13200, 13300)
AMAZON_SEEDED_ORDER_ROWS = 5
AMAZON_SEEDED_ORDERITEM_ROWS = 0

HARNESS_SCAFFOLDS = {
    "B": "browseruse",
    "P": "browseruse-prompt-only",
    "K": "browseruse-deliberative-contract-only",
    "E": "browseruse-deliberative-feasibility",
    "D": "browseruse-deliberative-no-coverage",
    "C": "browseruse-deliberative-coverage-only",
    "A": "browseruse-deliberative-coverage-advisory",
    "F": "browseruse-deliberative",
}
HARD_ARM_SPECS = (
    *(dict(arm_id=f"{code}_combined_original", harness=code,
           condition="combined", objective_order="original",
           study="harness_component") for code in HARNESS_SCAFFOLDS),
    dict(arm_id="B_clean_original", harness="B", condition="clean",
         objective_order="original", study="hard_context"),
    dict(arm_id="F_clean_original", harness="F", condition="clean",
         objective_order="original", study="hard_context"),
    dict(arm_id="B_format_only_original", harness="B",
         condition="format_only", objective_order="original",
         study="hard_context"),
    dict(arm_id="B_merchandising_original", harness="B",
         condition="merchandising", objective_order="original",
         study="hard_context"),
    dict(arm_id="B_combined_reversed", harness="B", condition="combined",
         objective_order="reversed", study="hard_context"),
    dict(arm_id="F_combined_reversed", harness="F", condition="combined",
         objective_order="reversed", study="hard_context"),
)
PROBE_STAGES = {
    "sol_high_before": [1, 2],
    "sol_high_mid": [3, 4],
}
PROBE_PREDECESSORS = {
    "sol_high_before": [],
    "sol_high_mid": [1, 2],
}


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def _write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise SystemExit(f"refusing to replace create-only file: {path}") from exc
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_new_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise SystemExit(f"refusing to replace create-only file: {path}") from exc
    with os.fdopen(descriptor, "w") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _file_ref(path: Path) -> dict:
    path = path.resolve()
    return {"path": str(path), "sha256": _sha_file(path), "size": path.stat().st_size}


def _tree_inventory(root: Path) -> dict:
    if not root.is_dir():
        raise SystemExit(f"inventory root missing: {root}")
    return {
        str(path.relative_to(root)): {
            "sha256": _sha_file(path), "size": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def _inventory_sha(value: dict) -> str:
    return _sha_bytes(_json_bytes(value))


def _campaign_limit_contract(*, compiler_calls: int = 1) -> dict:
    """Keep every per-run V19 bound; supersede only launch orchestration."""
    contract = json.loads(json.dumps(
        runtime_limit_contract(near_fraction=LIMIT_NEAR_FRACTION)
    ))
    contract.pop("sha256", None)
    launch = contract["categories"]["launch_only"]["campaign_launch"]
    launch["configured"] = {
        "block_runs_by_study": {"hard": 28},
        "block_sequence": [28, 28, 28, 28],
        "max_parallel_runs": MAX_PARALLEL_RUNS,
        "pair_atomic_replenishment": False,
        "host_concurrent_browser_ceiling": HOST_BROWSER_CEILING,
        "excluded_host_capacity_probe_roots": 32,
        "host_capacity_receipt_required": True,
        "host_wide_browser_campaign_lock": "/tmp/agentarena-clone8-browser.lock",
        "active_root_recheck_before_every_spawn": True,
        "spawn_stagger_seconds": 10,
        "probe_freshness_seconds": PROBE_MAX_AGE_SECONDS,
        "probe_process_timeout": None,
        "run_process_timeout": None,
        "inventory_subprocess_timeout_seconds": 20,
    }
    launch["source"] = (
        "ablations/further_mode_ablation_hard_v2/campaign.py"
    )
    compiler = contract["categories"]["fixed_architecture"][
        "compiler_and_checkpoint_shape"
    ]
    compiler["configured"]["contract_compile_calls"] = compiler_calls
    compiler["source"] = (
        "ablations/further_mode_ablation_hard_v2/harness/"
        "component_scaffolds.py"
        if compiler_calls == 0 else compiler["source"]
    )
    return {**contract, "sha256": _sha_bytes(_json_bytes(contract))}


def _limit_contracts() -> dict:
    return {
        "default": _campaign_limit_contract(compiler_calls=1),
        "C": _campaign_limit_contract(compiler_calls=0),
    }


def _limit_contract_for_row(manifest: dict, row: dict) -> dict:
    key = "C" if row.get("harness_arm") == "C" else "default"
    try:
        return manifest["limit_contracts"][key]
    except (KeyError, TypeError) as exc:
        raise SystemExit(f"missing per-profile limit contract: {key}") from exc


def _memory_snapshot() -> dict:
    values = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, raw = line.split(":", 1)
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            values[f"{key}_kib"] = int(raw.strip().split()[0])
    return values


def _active_browser_roots() -> int:
    count = 0
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            args = [part.decode(errors="replace") for part in
                    (path / "cmdline").read_bytes().split(b"\0") if part]
        except OSError:
            continue
        if not args:
            continue
        joined = "\0".join(args)
        executable = args[0].lower()
        if (
            ("chrome" in executable or "chromium" in executable)
            and "--user-data-dir=" in joined
            and "--type=" not in joined
        ):
            count += 1
    return count


def _cdp_ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=0.5
        ) as response:
            value = json.loads(response.read())
        return isinstance(value, dict) and bool(value.get("webSocketDebuggerUrl"))
    except Exception:  # noqa: BLE001 - a poll miss is expected while starting
        return False


def probe_host_capacity(output: Path, base_port: int) -> dict:
    lock = _acquire_host_browser_lock(output.parent)
    try:
        return _probe_host_capacity_locked(output, base_port)
    finally:
        lock.close()


def _probe_host_capacity_locked(output: Path, base_port: int) -> dict:
    """Excluded 32-root Chromium/CDP host-capacity check; no task/model call."""
    output = output.resolve()
    hash_path = output.with_suffix(output.suffix + ".sha256.json")
    if output.exists() or hash_path.exists():
        raise SystemExit("host-capacity receipt path already exists")
    _validate_port_band(base_port, require_free=True)
    coexistence = _audit_campaign_refill_coexistence(base_port)
    executable = _find_browser_executable()
    version = subprocess.run(
        [str(executable), "--version"], capture_output=True, text=True,
        timeout=20, check=False,
    )
    before_roots = _active_browser_roots()
    if before_roots + 32 > HOST_BROWSER_CEILING:
        raise SystemExit(
            f"excluded probe would exceed host ceiling: {before_roots}+32>"
            f"{HOST_BROWSER_CEILING}"
        )
    before_memory = _memory_snapshot()
    disk_before = shutil.disk_usage(ROOT)
    records = []
    processes = []
    started = time.monotonic()
    all_graceful = True
    with tempfile.TemporaryDirectory(prefix="further-mode-host-capacity-") as raw:
        temp_root = Path(raw)
        try:
            for index in range(32):
                port = base_port + index
                profile = temp_root / f"profile_{index:02d}"
                profile.mkdir()
                command = [
                    str(executable), "--headless=new", "--no-sandbox",
                    "--disable-gpu", "--disable-dev-shm-usage",
                    "--disable-background-networking", "--disable-extensions",
                    "--no-first-run", "--no-default-browser-check",
                    "--remote-debugging-address=127.0.0.1",
                    f"--remote-debugging-port={port}",
                    f"--user-data-dir={profile}", "about:blank",
                ]
                process = subprocess.Popen(
                    command, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True,
                )
                record = {
                    "index": index, "port": port, "pid": process.pid,
                    "cdp_ready": False, "cdp_ready_seconds": None,
                    "graceful_termination": False,
                }
                records.append(record)
                processes.append(process)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                for record, process in zip(records, processes):
                    if not record["cdp_ready"] and process.poll() is None \
                            and _cdp_ready(record["port"]):
                        record["cdp_ready"] = True
                        record["cdp_ready_seconds"] = round(
                            time.monotonic() - started, 3
                        )
                if all(record["cdp_ready"] for record in records):
                    break
                time.sleep(0.25)
            peak_roots = _active_browser_roots()
            peak_memory = _memory_snapshot()
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            for record, process in zip(records, processes):
                try:
                    process.wait(timeout=30)
                    record["graceful_termination"] = True
                    record["exit_code"] = process.returncode
                except subprocess.TimeoutExpired:
                    all_graceful = False
                    process.kill()
                    process.wait(timeout=10)
                    record["exit_code"] = process.returncode
    after_roots = _active_browser_roots()
    after_memory = _memory_snapshot()
    disk_after = shutil.disk_usage(ROOT)
    ready = sum(record["cdp_ready"] for record in records)
    graceful = sum(record["graceful_termination"] for record in records)
    passed = (
        ready == 32 and graceful == 32 and all_graceful
        and peak_roots >= before_roots + 32
        and after_roots <= before_roots
    )
    receipt = {
        "schema_version": 1,
        "kind": "further_mode_ablation_hard_v2_excluded_host_capacity_receipt",
        "created_at_utc": _utcnow(),
        "excluded_from_measured_runs": True,
        "model_calls": 0,
        "benchmark_tasks": 0,
        "target_browser_roots": 32,
        "base_port": base_port,
        "cdp_ready_roots": ready,
        "gracefully_terminated_roots": graceful,
        "host_browser_ceiling": HOST_BROWSER_CEILING,
        "passed": passed,
        "probe_implementation": {
            "path": str(Path(__file__).resolve()),
            "sha256": _sha_file(Path(__file__).resolve()),
            "detector_version": "chromium-root-cmdline-v2",
        },
        "browser_executable": {
            "path": str(executable), "sha256": _sha_file(executable),
            "size": executable.stat().st_size,
            "version_stdout": version.stdout.strip(),
            "version_stderr": version.stderr.strip(),
            "version_exit_code": version.returncode,
        },
        "host": {
            "browser_roots_before": before_roots,
            "browser_roots_peak": peak_roots,
            "browser_roots_after": after_roots,
            "memory_before": before_memory,
            "memory_peak": peak_memory,
            "memory_after": after_memory,
            "disk_before": {
                "total": disk_before.total, "used": disk_before.used,
                "free": disk_before.free,
            },
            "disk_after": {
                "total": disk_after.total, "used": disk_after.used,
                "free": disk_after.free,
            },
        },
        "refill_coexistence_postcondition": coexistence,
        "roots": records,
    }
    _write_new(output, receipt)
    _write_new(hash_path, {"path": output.name, "sha256": _sha_file(output)})
    if not passed:
        raise SystemExit(
            f"HOST CAPACITY FAIL: ready={ready}/32 graceful={graceful}/32; "
            f"receipt preserved at {output}"
        )
    print(f"HOST CAPACITY PASS: 32/32 CDP-ready and gracefully terminated: {output}")
    return receipt


def _validate_host_capacity_receipt(path: Path, *, require_fresh: bool) -> dict:
    path = path.resolve()
    sidecar = path.with_suffix(path.suffix + ".sha256.json")
    if (
        not path.is_file() or not sidecar.is_file()
        or _read_json(sidecar) != {"path": path.name, "sha256": _sha_file(path)}
    ):
        raise SystemExit("host-capacity receipt/hash binding is invalid")
    receipt = _read_json(path)
    try:
        _validate_campaign_coexistence_record(
            receipt.get("refill_coexistence_postcondition"),
            int(receipt.get("base_port", 0)),
        )
    except (ValueError, TypeError, IndexError) as exc:
        raise SystemExit(
            f"host-capacity refill reservation is invalid: {exc}"
        ) from exc
    executable = _find_browser_executable()
    if (
        receipt.get("kind")
        != "further_mode_ablation_hard_v2_excluded_host_capacity_receipt"
        or receipt.get("excluded_from_measured_runs") is not True
        or receipt.get("model_calls") != 0
        or receipt.get("benchmark_tasks") != 0
        or receipt.get("target_browser_roots") != 32
        or not isinstance(receipt.get("base_port"), int)
        or receipt.get("cdp_ready_roots") != 32
        or receipt.get("gracefully_terminated_roots") != 32
        or receipt.get("host_browser_ceiling") != HOST_BROWSER_CEILING
        or receipt.get("passed") is not True
        or receipt.get("probe_implementation") != {
            "path": str(Path(__file__).resolve()),
            "sha256": _sha_file(Path(__file__).resolve()),
            "detector_version": "chromium-root-cmdline-v2",
        }
        or len(receipt.get("roots") or []) != 32
        or any(
            record.get("cdp_ready") is not True
            or record.get("graceful_termination") is not True
            for record in receipt.get("roots") or []
        )
        or receipt.get("browser_executable", {}).get("path") != str(executable)
        or receipt.get("browser_executable", {}).get("sha256") != _sha_file(executable)
    ):
        raise SystemExit("host-capacity receipt content is not an exact pass")
    if require_fresh:
        age = (_parse_utc(_utcnow()) - _parse_utc(receipt["created_at_utc"])).total_seconds()
        if age < 0 or age > HOST_CAPACITY_RECEIPT_MAX_AGE_SECONDS:
            raise SystemExit(f"host-capacity receipt is stale/post-dated: {age:.1f}s")
    return _file_ref(path)


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise SystemExit(f"unsafe identifier: {value!r}")
    return value


def _deterministic_order(values: Iterable, namespace: str) -> list:
    return sorted(
        values,
        key=lambda value: hashlib.sha256(
            f"{SCHEDULE_SEED}\0{namespace}\0{value}".encode()
        ).digest(),
    )


def _validate_regions(regions: Iterable[str], label: str) -> tuple[str, ...]:
    value = tuple(regions)
    if len(value) != 3 or set(value) != set(REGIONS) or len(set(value)) != 3:
        raise SystemExit(f"{label} must explicitly order the exact three regions")
    return value


def _region_order(regions: tuple[str, ...], primary: str) -> list[str]:
    index = regions.index(primary)
    return [regions[(index + offset) % len(regions)] for offset in range(3)]


def _probe_stage(block: int) -> str:
    return next(stage for stage, blocks in PROBE_STAGES.items() if block in blocks)


def _make_row(
    campaign_id: str,
    base_port: int,
    block: int,
    spawn_index: int,
    repeat: int,
    study: str,
    arm_id: str,
    harness: str,
    condition: str,
    objective_order: str,
    route: list[str],
) -> dict:
    scenario = HARD_SCENARIO
    variant = HARD_VARIANT
    model_request = HIGH_REQUEST
    model_recorded = HIGH_RECORDED
    scaffold = HARNESS_SCAFFOLDS[harness]
    safe_arm = arm_id.replace("_", "-")
    run_id = f"b{block:02d}_r{repeat}_{safe_arm}"
    run_name = f"{campaign_id}_{run_id}"
    result_dir = (
        f"amazon__{scaffold}__{model_recorded}__"
        f"{scenario}-{variant}__{condition}"
    )
    return {
        "run_id": run_id,
        "block": block,
        "spawn_index": spawn_index,
        "repeat": repeat,
        "study": study,
        "arm_id": arm_id,
        "harness_arm": harness,
        "steering_profile": condition,
        "scenario": scenario,
        "variant": variant,
        "task_id": f"{scenario}-{variant}",
        "condition": condition,
        "objective_order": objective_order,
        "scaffold": scaffold,
        "model_request": model_request,
        "model_recorded": model_recorded,
        "logical_model": LOGICAL_MODEL,
        "probe_stage": _probe_stage(block),
        "primary_region": route[0],
        "region_order": route,
        "port": base_port + spawn_index,
        "run_name": run_name,
        "experiment_relpath": f"runs/{run_name}",
        "browser_run_relpath": f"runs/{run_name}/{result_dir}",
        "summary_relpath": f"runs/{run_name}/{result_dir}/summary.json",
        "trajectory_relpath": f"runs/{run_name}/{result_dir}/trajectory.json",
        "run_log_relpath": f"runs/{run_name}/{result_dir}/run.log",
        "launcher_log_relpath": f"launcher_logs/{run_id}.log",
    }


def build_schedule(
    campaign_id: str,
    base_port: int,
    *,
    regions: tuple[str, ...] = REGIONS,
) -> list[dict]:
    """Build four exact 28-run, route-matched hard randomized blocks."""

    campaign_id = _safe_id(campaign_id)
    regions = _validate_regions(regions, "campaign route")
    rows: list[dict] = []
    for block in range(1, 5):
        repeats = (2 * block - 1, 2 * block)
        pending = []
        repeat_primaries = {
            repeat: regions[(repeat - 1) % 3] for repeat in repeats
        }
        idle = next(
            region for region in regions
            if region not in set(repeat_primaries.values())
        )
        repeat_routes = {}
        for repeat in repeats:
            primary = repeat_primaries[repeat]
            other = next(
                value for key, value in repeat_primaries.items()
                if key != repeat
            )
            # The idle route is the first fallback for both repeat primaries.
            # A single-primary failure therefore adds only one repeat's load
            # (14 hard runs), never two repeats to one active route.
            repeat_routes[repeat] = [primary, idle, other]
        for repeat in repeats:
            # Every within-repeat contrast is route matched. Repetition
            # primaries rotate 3/3/2 across the three routes.
            for spec in HARD_ARM_SPECS:
                arm = spec["arm_id"]
                pending.append((repeat, arm, spec["study"], spec["harness"],
                                spec["condition"], spec["objective_order"],
                                repeat_routes[repeat]))
        ordered = _deterministic_order(
            pending, f"block:{block}:spawn-order"
        )
        for spawn_index, item in enumerate(ordered):
            repeat, arm, study, harness, condition, order, route = item
            rows.append(_make_row(
                campaign_id, base_port, block, spawn_index, repeat, study,
                arm, harness, condition, order, route,
            ))
    validate_schedule(rows, base_port, regions=regions)
    return rows


def validate_schedule(
    rows: list[dict],
    base_port: int,
    *,
    regions: tuple[str, ...] = REGIONS,
) -> None:
    regions = _validate_regions(regions, "frozen campaign route")
    errors: list[str] = []
    if len(rows) != 112 or len({row.get("run_id") for row in rows}) != 112:
        errors.append("schedule is not 112 unique hard runs")
    if Counter(row.get("study") for row in rows) != {
        "harness_component": 64,
        "hard_context": 48,
    }:
        errors.append("hard study denominators differ from 64/48")
    hard = list(rows)
    if Counter(row.get("arm_id") for row in hard) != {
        spec["arm_id"]: 8 for spec in HARD_ARM_SPECS
    }:
        errors.append("hard arms are not exact n=8")
    if any(
        row.get("task_id") != f"{HARD_SCENARIO}-{HARD_VARIANT}"
        or row.get("variant") != HARD_VARIANT
        or row.get("model_request") != HIGH_REQUEST
        or row.get("scaffold") != HARNESS_SCAFFOLDS.get(row.get("harness_arm"))
        for row in hard
    ):
        errors.append("hard task/model/scaffold identity drifted")
    for block in range(1, 5):
        block_rows = [row for row in rows if row.get("block") == block]
        expected = 28
        if len(block_rows) != expected:
            errors.append(f"block {block} is not {expected} runs")
            continue
        if {row["spawn_index"] for row in block_rows} != set(range(expected)):
            errors.append(f"block {block} spawn indices drifted")
        if {row["port"] for row in block_rows} != set(
            range(base_port, base_port + expected)
        ):
            errors.append(f"block {block} port band drifted")
        repeats = sorted({row["repeat"] for row in block_rows})
        expected_repeats = [2 * block - 1, 2 * block]
        if repeats != expected_repeats:
            errors.append(f"block {block} repetition pair drifted")
    for repeat in REPETITIONS:
        hard_repeat = [row for row in hard if row["repeat"] == repeat]
        if {row["arm_id"] for row in hard_repeat} != {
            spec["arm_id"] for spec in HARD_ARM_SPECS
        }:
            errors.append(f"hard repeat {repeat} is incomplete")
        if (
            len({row["primary_region"] for row in hard_repeat}) != 1
            or len({tuple(row["region_order"]) for row in hard_repeat}) != 1
        ):
            errors.append(f"hard repeat {repeat} is not exactly route matched")
    for block in range(1, 5):
        block_rows = [row for row in rows if row["block"] == block]
        repeats = sorted({row["repeat"] for row in block_rows})
        primary_by_repeat = {
            repeat: next(
                row["primary_region"] for row in block_rows
                if row["repeat"] == repeat
            )
            for repeat in repeats
        }
        idle = next(
            region for region in regions
            if region not in set(primary_by_repeat.values())
        )
        for repeat in repeats:
            expected_route = [
                primary_by_repeat[repeat], idle,
                next(value for key, value in primary_by_repeat.items()
                     if key != repeat),
            ]
            if any(
                row["region_order"] != expected_route
                for row in block_rows if row["repeat"] == repeat
            ):
                errors.append(
                    f"block {block} repeat {repeat} does not use idle-first fallback"
                )
    for arm in (spec["arm_id"] for spec in HARD_ARM_SPECS):
        selected = [row for row in rows if row["arm_id"] == arm]
        route_counts = Counter(row["primary_region"] for row in selected)
        if sorted(route_counts.values()) != [2, 3, 3]:
            errors.append(f"arm {arm} is not route-balanced 3/3/2")
    if any(
        row["region_order"][0] != row["primary_region"]
        or set(row["region_order"]) != set(regions)
        or row["probe_stage"] not in PROBE_STAGES
        for row in rows
    ):
        errors.append("one or more routing/probe identities drifted")
    if errors:
        raise SystemExit("invalid 112-run hard schedule:\n  " + "\n  ".join(errors))


def _validate_sidecar() -> dict:
    sidecar = _read_json(SIDECAR_PATH)
    canonical = _read_json(
        ROOT / sidecar.get("source_path", "")
    )[HARD_VARIANT]["text"]
    objectives = sidecar.get("objectives_original") or []
    exact_swap = None
    if (
        len(objectives) == 2
        and sidecar.get("original", "").count(objectives[0]) == 1
        and sidecar.get("original", "").count(objectives[1]) == 1
        and sidecar["original"].index(objectives[0])
        < sidecar["original"].index(objectives[1])
    ):
        placeholder = "__FURTHER_MODE_OBJECTIVE_SWAP__"
        exact_swap = (
            sidecar["original"].replace(objectives[0], placeholder, 1)
            .replace(objectives[1], objectives[0], 1)
            .replace(placeholder, objectives[1], 1)
        )
    if (
        sidecar.get("kind") != "further_mode_ablation_objective_order_sidecar"
        or sidecar.get("task_id") != f"{HARD_SCENARIO}-{HARD_VARIANT}"
        or sidecar.get("original") != canonical
        or sidecar.get("objectives_reversed")
        != list(reversed(sidecar.get("objectives_original") or []))
        or len(sidecar.get("objectives_original") or []) != 2
        or sidecar.get("reversed") != exact_swap
    ):
        raise SystemExit("objective-order sidecar is invalid or drifted")
    return _file_ref(SIDECAR_PATH)


def _validate_gold_contract() -> dict:
    value = _read_json(GOLD_CONTRACT_PATH)
    if (
        value.get("kind")
        != "further_mode_ablation_gold_task_contract_semantics"
        or value.get("task_id") != f"{HARD_SCENARIO}-{HARD_VARIANT}"
        or len(value.get("constraints") or []) != 5
        or len(value.get("objectives") or []) != 2
        or value.get("search_mode") != "best_available"
        or value.get("coequal_objectives") is not True
    ):
        raise SystemExit("gold TaskContract semantics are malformed")
    return _file_ref(GOLD_CONTRACT_PATH)


def _campaign_sources() -> dict:
    records = {}
    for path in sorted(HERE.iterdir()):
        if path.is_file() and path.suffix in {".py", ".json", ".md"}:
            records[path.name] = {
                "sha256": _sha_file(path), "size": path.stat().st_size,
            }
    required = {"campaign.py", "analyze_results.py", "preregistration.json",
                "objective_order_sidecar.json", "gold_task_contract.json",
                "infra_classifier.py", "recovery_amendment.json"}
    if not required.issubset(records):
        raise SystemExit("campaign source set is incomplete")
    return records


def _component_sources() -> dict:
    records = {}
    for root in (HARNESS_DIR, LEGACY_COMPONENT_DIR, PROMPT_ONLY_DIR):
        inventory = _tree_inventory(root)
        for relative, record in inventory.items():
            records[str((root.relative_to(ROOT) / relative))] = record
    return records


def _command_local_paths(profile: str) -> list[str]:
    if profile == "hard":
        return [
            str(HARNESS_DIR), str(LEGACY_COMPONENT_DIR),
            str(PROMPT_ONLY_DIR), str(ROOT),
        ]
    raise SystemExit(f"unknown command-local profile: {profile}")


def _component_registration_gate() -> dict:
    hard_code = (
        "import json,agentarena.scaffolds; import arm_registry;"
        "from agentarena.core.scaffold import SCAFFOLDS;"
        "arm_registry.validate_command_local_registration();"
        "print(json.dumps({'names':SCAFFOLDS.names(),'arms':arm_registry.ARM_METADATA},sort_keys=True))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(_command_local_paths("hard"))
    completed = subprocess.run(
        [sys.executable, "-c", hard_code], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=60,
    )
    if completed.returncode:
        raise SystemExit(
            "hard command-local registration failed:\n"
            + completed.stdout + completed.stderr
        )
    outputs = {"hard": json.loads(completed.stdout.splitlines()[-1])}
    names = set(outputs["hard"].get("names") or [])
    if not set(HARNESS_SCAFFOLDS.values()).issubset(names):
        raise SystemExit("hard component scaffold registration differs")
    return outputs


def _excluded_predecessor_pilot() -> dict:
    """Bind the immutable v1 block-5 assignment, never its outcomes."""

    manifest = _read_json(V1_MANIFEST)
    rows = [
        row for row in manifest.get("schedule", [])
        if isinstance(row, dict) and row.get("block") == 5
    ]
    if (
        manifest.get("kind") != "further_mode_ablation_campaign"
        or len(rows) != 28
        or len({row.get("run_id") for row in rows}) != 28
        or {row.get("repeat") for row in rows} != {1, 2}
    ):
        raise SystemExit("v1 block-5 pilot assignment is absent or malformed")
    return {
        "campaign_manifest": _file_ref(V1_MANIFEST),
        "block": 5,
        "disposition": "excluded_pilot_never_reuse_or_retry",
        "run_ids": sorted(row["run_id"] for row in rows),
        "rows_sha256": _sha_bytes(_json_bytes(rows)),
        "outcomes_read": False,
    }


def static_verify() -> dict:
    prereg = _read_json(PREREG_PATH)
    if (
        prereg.get("kind")
        != "further_mode_ablation_hard_v2_preregistration"
        or prereg.get("frozen_before_measured_outcomes") is not True
        or prereg.get("design", {}).get("total_runs") != 112
        or prereg.get("runtime", {}).get("max_steps") != 12000
        or prereg.get("runtime", {}).get("whole_run_timeout_seconds") != 172800
        or prereg.get("runtime", {}).get("launcher_max_parallel_runs") != 32
        or prereg.get("runtime", {}).get("host_browser_ceiling")
        != HOST_BROWSER_CEILING
        or prereg.get("runtime", {}).get("protected_refill_browser_reserve")
        != REFILL_BROWSER_RESERVE
        or prereg.get("runtime", {}).get("maximum_attempts_per_frozen_row")
        != MAX_ATTEMPTS
    ):
        raise SystemExit("preregistration identity/runtime differs")
    if CAPS.get("max_steps") != 12000 or CAPS.get("cell_timeout_seconds") != 172800:
        raise SystemExit("inherited per-run step/time safety contract drifted")
    _validate_caps(CAPS)
    schedule = build_schedule("further_mode_hard_v2_static", 17000)
    campaign_limits = _limit_contracts()
    campaign_limit = campaign_limits["default"]
    inherited_limit = runtime_limit_contract(near_fraction=LIMIT_NEAR_FRACTION)
    if (
        campaign_limit["categories"]["launch_only"]["campaign_launch"]
        == inherited_limit["categories"]["launch_only"]["campaign_launch"]
    ):
        raise SystemExit("campaign launch-only contract was not superseded")
    return {
        "runs": len(schedule),
        "blocks": dict(Counter(row["block"] for row in schedule)),
        "preregistration": _file_ref(PREREG_PATH),
        "objective_order_sidecar": _validate_sidecar(),
        "gold_task_contract": _validate_gold_contract(),
        "recovery_amendment": _file_ref(RECOVERY_AMENDMENT_PATH),
        "excluded_predecessor_pilot": _excluded_predecessor_pilot(),
        "registration": _component_registration_gate(),
        "v19_manifest": _file_ref(V19_MANIFEST),
        "inherited_limit_contract_sha256": inherited_limit["sha256"],
        "campaign_limit_contract_sha256": campaign_limit["sha256"],
        "coverage_only_limit_contract_sha256": campaign_limits["C"]["sha256"],
    }


def _ports_free(ports: Iterable[int]) -> bool:
    sockets = []
    try:
        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
            sockets.append(sock)
        return True
    except OSError:
        return False
    finally:
        for sock in sockets:
            sock.close()


def _validate_port_band(base_port: int, require_free: bool) -> None:
    # The measured blocks need 28 ports; the excluded host-capacity gate uses
    # the full 32-root operational ceiling and therefore owns four more.
    port_band_size = max(MAX_BLOCK_SIZE, MAX_PARALLEL_RUNS)
    ports = range(base_port, base_port + port_band_size)
    if base_port < 1024 or base_port + port_band_size - 1 > 65535:
        raise SystemExit("campaign port band is outside the valid range")
    if set(ports) & set(PROTECTED_PORT_RANGE):
        raise SystemExit("campaign port band intersects protected 132xx refill lanes")
    if require_free and not _ports_free(ports):
        raise SystemExit("campaign port band is busy")


def _audit_campaign_refill_coexistence(campaign_base_port: int) -> dict:
    """Strengthen the inherited refill audit for this campaign's 32 runs.

    The inherited helper recognizes and authenticates protected 132xx refill
    masters, but its arithmetic is frozen to an older four-run launcher.  This
    postcondition reserves four browser roots even when no refill is currently
    active and fails closed if recognized refill jobs exceed that reservation.
    """
    inherited = audit_refill_coexistence(campaign_base_port)
    refill_jobs = inherited.get("refill_jobs_sum")
    active_roots = _active_browser_roots()
    if (
        not isinstance(refill_jobs, int)
        or refill_jobs < 0
        or refill_jobs > REFILL_BROWSER_RESERVE
        or MAX_PARALLEL_RUNS + REFILL_BROWSER_RESERVE
        > HOST_BROWSER_CEILING
        or active_roots > HOST_BROWSER_CEILING - REFILL_BROWSER_RESERVE
    ):
        raise SystemExit(
            "campaign-specific refill reservation cannot be established: "
            f"recognized_refill_jobs={refill_jobs}, active_browser_roots="
            f"{active_roots}, campaign_max={MAX_PARALLEL_RUNS}, "
            f"reserve={REFILL_BROWSER_RESERVE}, ceiling={HOST_BROWSER_CEILING}"
        )
    payload = {
        "kind": "further_mode_ablation_hard_v2_refill_coexistence_postcondition",
        "captured_at_utc": _utcnow(),
        "campaign_base_port": campaign_base_port,
        "campaign_max_parallel_runs": MAX_PARALLEL_RUNS,
        "host_browser_ceiling": HOST_BROWSER_CEILING,
        "protected_refill_browser_reserve": REFILL_BROWSER_RESERVE,
        "recognized_refill_jobs": refill_jobs,
        "active_browser_roots": active_roots,
        "inherited_audit": inherited,
        "verdict": "pass",
    }
    return {**payload, "snapshot_sha256": _sha_bytes(_json_bytes(payload))}


def _validate_campaign_coexistence_record(record: object,
                                          campaign_base_port: int) -> None:
    if not isinstance(record, dict):
        raise ValueError("campaign-specific refill coexistence record is absent")
    payload = {key: value for key, value in record.items()
               if key != "snapshot_sha256"}
    inherited = record.get("inherited_audit") or {}
    if (
        record.get("kind")
        != "further_mode_ablation_hard_v2_refill_coexistence_postcondition"
        or record.get("campaign_base_port") != campaign_base_port
        or record.get("campaign_max_parallel_runs") != MAX_PARALLEL_RUNS
        or record.get("host_browser_ceiling") != HOST_BROWSER_CEILING
        or record.get("protected_refill_browser_reserve")
        != REFILL_BROWSER_RESERVE
        or record.get("verdict") != "pass"
        or record.get("snapshot_sha256") != _sha_bytes(_json_bytes(payload))
        or not isinstance(record.get("recognized_refill_jobs"), int)
        or not 0 <= record["recognized_refill_jobs"] <= REFILL_BROWSER_RESERVE
        or not isinstance(record.get("active_browser_roots"), int)
        or not 0 <= record["active_browser_roots"] <= (
            HOST_BROWSER_CEILING - REFILL_BROWSER_RESERVE
        )
        or inherited.get("verdict") != "pass"
        or inherited.get("refill_jobs_sum") != record["recognized_refill_jobs"]
    ):
        raise ValueError("campaign-specific refill coexistence record is invalid")


def _runtime_policy(runtime: dict, production_sha: str, cert_sha: str,
                    limit_contracts: dict) -> dict:
    base = _runtime_environment_policy(runtime)
    values = dict(base["set"])
    values.update({
        "AGENTARENA_LIMIT_CONTRACT_JSON": json.dumps(
            limit_contracts["default"], sort_keys=True, separators=(",", ":")
        ),
        "AGENTARENA_RUNTIME_SOURCE_ATTESTATION": production_sha,
        "AGENTARENA_EVALUATION_INPUT_ATTESTATION": cert_sha,
    })
    payload = {
        **{key: value for key, value in base.items() if key != "sha256"},
        "set": dict(sorted(values.items())),
        "per_run_set": {
            "TRAPI_REGIONS_OVERRIDE": "schedule.region_order",
            "FURTHER_MODE_ABLATION_RUN_ID": "schedule.run_id",
            "FURTHER_MODE_ABLATION_STEERING_ARM": "schedule.steering_profile",
            "FURTHER_MODE_ABLATION_HARNESS_ARM": "schedule.harness_arm",
            "AGENTARENA_LIMIT_CONTRACT_JSON": "schedule.harness_arm profile",
        },
        "command_local_pythonpath_profiles": {
            "hard": _command_local_paths("hard"),
        },
    }
    return {**payload, "sha256": _sha_bytes(_json_bytes(payload))}


def prepare_campaign(
    campaign_dir: Path,
    campaign_id: str,
    base_port: int,
    cert_report: Path,
    lockdiff_report: Path,
    host_capacity_receipt: Path,
    regions: tuple[str, ...],
) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest_path = campaign_dir / "campaign_manifest.json"
    if manifest_path.exists():
        verify_campaign(campaign_dir)
        print("campaign already frozen; no file was replaced")
        return
    if campaign_dir.exists() and any(campaign_dir.iterdir()):
        raise SystemExit("partial campaign directory exists; choose a new path")
    _validate_port_band(base_port, require_free=True)
    static_verify()
    regions = _validate_regions(regions, "campaign route")
    cert_ref = _validate_certification(cert_report.resolve())
    lockdiff = _validate_lockdiff(lockdiff_report.resolve())
    host_capacity_ref = _validate_host_capacity_receipt(
        host_capacity_receipt, require_fresh=True
    )
    if _active_browser_roots() > HOST_BROWSER_CEILING:
        raise SystemExit("host browser-root ceiling is already exceeded")
    schedule = build_schedule(campaign_id, base_port, regions=regions)
    production_sources = code_inventory()
    production_sha = _inventory_sha(production_sources)
    campaign_sources = _campaign_sources()
    component_sources = _component_sources()
    runtime = runtime_dependency_manifest()
    inherited_limit_contract = runtime_limit_contract(
        near_fraction=LIMIT_NEAR_FRACTION
    )
    limit_contracts = _limit_contracts()
    limit_contract = limit_contracts["default"]
    policy = _runtime_policy(runtime, production_sha, cert_ref["sha256"], limit_contracts)

    frozen_root = campaign_dir / "frozen_inputs" / "benchmark_data" / "amazon"
    artifacts = {}
    for scenario in (HARD_SCENARIO,):
        source = ROOT / "benchmark_data" / "amazon" / scenario
        inventory = _tree_inventory(source)
        artifacts[scenario] = {
            "files": inventory, "files_sha256": _inventory_sha(inventory),
        }
        target = frozen_root / scenario
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
    frozen_cert = campaign_dir / "frozen_inputs" / "certification_report.json"
    frozen_cert.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cert_report.resolve(), frozen_cert)
    lock_root = campaign_dir / "frozen_inputs" / "lockdiff"
    lock_root.mkdir(parents=True)
    frozen_before = lock_root / "lockdiff_before.json"
    frozen_after = lock_root / "lockdiff_after.json"
    shutil.copy2(Path(lockdiff["before"]["path"]), frozen_before)
    shutil.copy2(Path(lockdiff["after"]["path"]), frozen_after)
    frozen_host_capacity = (
        campaign_dir / "frozen_inputs" / "host_capacity_receipt.json"
    )
    shutil.copy2(host_capacity_receipt.resolve(), frozen_host_capacity)

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "campaign_id": _safe_id(campaign_id),
        "frozen_at_utc": _utcnow(),
        "source_root": str(ROOT),
        "base_port": base_port,
        "schedule": schedule,
        "preregistration": _file_ref(PREREG_PATH),
        "objective_order_sidecar": _validate_sidecar(),
        "gold_task_contract": _validate_gold_contract(),
        "recovery_amendment": _file_ref(RECOVERY_AMENDMENT_PATH),
        "excluded_predecessor_pilot": _excluded_predecessor_pilot(),
        "design": {
            "total_runs": 112,
            "hard_runs": 112,
            "blocks": 4,
            "block_sizes": [28, 28, 28, 28],
            "repetitions_per_arm": 8,
            "schedule_seed": SCHEDULE_SEED,
            "launcher_max_parallel_runs": MAX_PARALLEL_RUNS,
            "launcher_concurrency_is_operational_not_estimand": True,
            "inherited_v19_launch_only_max_concurrent_browsers": CAPS[
                "max_concurrent_browsers"
            ],
            "v19_launch_ceiling_superseded_after_excluded_host_probe": True,
            "spawn_stagger_seconds": CAPS["spawn_stagger_seconds"],
            "host_browser_ceiling": HOST_BROWSER_CEILING,
            "protected_refill_browser_reserve": REFILL_BROWSER_RESERVE,
            "campaign_specific_refill_postcondition_required_before_every_spawn": True,
            "excluded_host_capacity_probe_roots": 32,
        },
        "caps": CAPS,
        "limit_near_fraction": LIMIT_NEAR_FRACTION,
        "limit_contract": limit_contract,
        "limit_contracts": limit_contracts,
        "inherited_v19_limit_contract_sha256": inherited_limit_contract["sha256"],
        "launch_only_contract_superseded": True,
        "runtime_dependencies": runtime,
        "runtime_environment_policy": policy,
        "v19_cap_reference": _file_ref(V19_MANIFEST),
        "production_source_inventory": production_sources,
        "production_source_inventory_sha256": production_sha,
        "campaign_source_inventory": campaign_sources,
        "campaign_source_inventory_sha256": _inventory_sha(campaign_sources),
        "component_source_inventory": component_sources,
        "component_source_inventory_sha256": _inventory_sha(component_sources),
        "component_registration": _component_registration_gate(),
        "infrastructure_classifier": _file_ref(INFRA_CLASSIFIER_PATH),
        "upstream_infrastructure_classifier": _file_ref(
            UPSTREAM_INFRA_CLASSIFIER_PATH
        ),
        "artifacts": artifacts,
        "frozen_artifact_root": "frozen_inputs/benchmark_data/amazon",
        "frozen_artifact_inventory": _tree_inventory(frozen_root),
        "certification": {
            "source": cert_ref,
            "frozen_path": "frozen_inputs/certification_report.json",
            "frozen_sha256": _sha_file(frozen_cert),
        },
        "host_capacity": {
            "source": host_capacity_ref,
            "frozen_path": "frozen_inputs/host_capacity_receipt.json",
            "frozen_sha256": _sha_file(frozen_host_capacity),
            "required_before_freeze": True,
            "excluded_from_measured_runs": True,
        },
        "lockdiff": {
            "source_validation": lockdiff,
            "frozen_before_path": "frozen_inputs/lockdiff/lockdiff_before.json",
            "frozen_after_path": "frozen_inputs/lockdiff/lockdiff_after.json",
            "frozen_before_sha256": _sha_file(frozen_before),
            "frozen_after_sha256": _sha_file(frozen_after),
        },
        "probe_policy": {
            "stages": PROBE_STAGES,
            "predecessor_blocks": PROBE_PREDECESSORS,
            "scheduled_regions": {LOGICAL_MODEL: list(regions)},
            "max_checkpoint_age_seconds": PROBE_MAX_AGE_SECONDS,
            "pre_and_mid_model_probes_required": True,
            "unhealthy_action": "pause_never_kill_active_runs",
        },
        "attempt_policy": {
            "create_only_receipts_logs_reports": True,
            "silent_relaunch_prohibited": True,
            "behavioral_failures_remain_in_denominator": True,
            "adaptive_stopping_prohibited": True,
            "maximum_attempts_per_frozen_row": MAX_ATTEMPTS,
            "replacement_eligibility": "only frozen classifier class=infra",
            "partial_replacement_eligibility": (
                "only controller-proven ended, no-summary, no-order, "
                "zero-agent-behavior attempts with an exact external "
                "termination marker; all ambiguous partials block"
            ),
            "classifier_reads_no_preference_score": True,
            "same_frozen_row_and_route_required": True,
            "every_attempt_preserved": True,
            "ambiguous_and_capability_classes_score_zero": True,
            "generic_early_failure_without_limit_telemetry": (
                "fail_report_for_manual_adjudication"
            ),
            "compiler_semantic_pre_agent_exception": (
                "exact zero-step capability classification may score zero; "
                "missing audits are disclosed, never imputed as untouched"
            ),
        },
        "metric_policy": {
            "headline": "preservation_strict",
            "formula": "P*=G*O",
            "mechanism_primary": "literal_hero",
            "secondary": "strict_binary",
            "legacy_preservation": "diagnostic_only",
            "scheduled_denominator": 112,
            "behavioral_no_order_scores": 0,
        },
    }
    _write_new(manifest_path, manifest)
    _write_new(
        campaign_dir / "campaign_manifest.sha256.json",
        {"path": manifest_path.name, "sha256": _sha_file(manifest_path)},
    )
    verify_campaign(campaign_dir)
    print("PREPARE PASS: exact 112-run hard campaign frozen in four blocks")


def _verify_inventory(root: Path, expected: dict, label: str) -> None:
    actual = _tree_inventory(root)
    if actual != expected:
        changed = sorted(
            key for key in set(actual) | set(expected)
            if actual.get(key) != expected.get(key)
        )
        raise SystemExit(f"{label} hash drift: " + ", ".join(changed[:20]))


def verify_campaign(campaign_dir: Path, *, quiet: bool = False) -> dict:
    campaign_dir = campaign_dir.resolve()
    path = campaign_dir / "campaign_manifest.json"
    sidecar = campaign_dir / "campaign_manifest.sha256.json"
    if not path.is_file() or not sidecar.is_file():
        raise SystemExit("campaign is not frozen")
    if _read_json(sidecar) != {"path": path.name, "sha256": _sha_file(path)}:
        raise SystemExit("campaign manifest hash binding is invalid")
    manifest = _read_json(path)
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("kind") != KIND:
        raise SystemExit("campaign kind/schema differs")
    design = manifest.get("design") or {}
    if (
        design.get("total_runs") != 112
        or design.get("hard_runs") != 112
        or design.get("blocks") != 4
        or design.get("block_sizes") != [28, 28, 28, 28]
        or design.get("repetitions_per_arm") != 8
        or design.get("schedule_seed") != SCHEDULE_SEED
        or (manifest.get("metric_policy") or {}).get(
            "scheduled_denominator"
        ) != 112
    ):
        raise SystemExit("hard-only design/denominator differs")
    regions = _validate_regions(
        manifest.get("probe_policy", {}).get("scheduled_regions", {}).get(
            LOGICAL_MODEL, ()
        ),
        "frozen campaign route",
    )
    expected = build_schedule(
        manifest["campaign_id"], int(manifest["base_port"]), regions=regions
    )
    if manifest.get("schedule") != expected:
        raise SystemExit("schedule differs from frozen campaign code")
    if manifest.get("preregistration") != _file_ref(PREREG_PATH):
        raise SystemExit("preregistration drifted")
    if manifest.get("objective_order_sidecar") != _validate_sidecar():
        raise SystemExit("objective-order sidecar drifted")
    if manifest.get("gold_task_contract") != _validate_gold_contract():
        raise SystemExit("gold TaskContract semantics drifted")
    if manifest.get("recovery_amendment") != \
            _file_ref(RECOVERY_AMENDMENT_PATH):
        raise SystemExit("recovery amendment drifted")
    if manifest.get("excluded_predecessor_pilot") != \
            _excluded_predecessor_pilot():
        raise SystemExit("excluded v1 block-5 pilot binding drifted")
    if set(row["run_id"] for row in manifest["schedule"]) & set(
        manifest["excluded_predecessor_pilot"]["run_ids"]
    ):
        raise SystemExit("recovery schedule reuses a v1 pilot run ID")
    if manifest.get("infrastructure_classifier") != _file_ref(INFRA_CLASSIFIER_PATH):
        raise SystemExit("frozen infrastructure classifier drifted")
    if manifest.get("upstream_infrastructure_classifier") != _file_ref(
        UPSTREAM_INFRA_CLASSIFIER_PATH
    ):
        raise SystemExit("upstream infrastructure classifier drifted")
    production = code_inventory()
    campaign_sources = _campaign_sources()
    components = _component_sources()
    if (
        manifest.get("production_source_inventory") != production
        or manifest.get("production_source_inventory_sha256") != _inventory_sha(production)
        or manifest.get("campaign_source_inventory") != campaign_sources
        or manifest.get("campaign_source_inventory_sha256") != _inventory_sha(campaign_sources)
        or manifest.get("component_source_inventory") != components
        or manifest.get("component_source_inventory_sha256") != _inventory_sha(components)
        or manifest.get("component_registration") != _component_registration_gate()
    ):
        raise SystemExit("measured source/component inventory drifted")
    runtime = runtime_dependency_manifest()
    limit_contracts = _limit_contracts()
    limit = limit_contracts["default"]
    inherited_limit = runtime_limit_contract(near_fraction=LIMIT_NEAR_FRACTION)
    cert = manifest["certification"]
    source_cert = _validate_certification(Path(cert["source"]["path"]))
    policy = _runtime_policy(
        runtime, _inventory_sha(production), source_cert["sha256"],
        limit_contracts,
    )
    if (
        manifest.get("caps") != CAPS
        or manifest.get("runtime_dependencies") != runtime
        or manifest.get("limit_contract") != limit
        or manifest.get("limit_contracts") != limit_contracts
        or manifest.get("inherited_v19_limit_contract_sha256")
        != inherited_limit["sha256"]
        or manifest.get("launch_only_contract_superseded") is not True
        or manifest.get("runtime_environment_policy") != policy
        or manifest.get("v19_cap_reference") != _file_ref(V19_MANIFEST)
    ):
        raise SystemExit("runtime/cap/environment contract drifted")
    host = manifest.get("host_capacity") or {}
    source_host = _validate_host_capacity_receipt(
        Path(host.get("source", {}).get("path", "")), require_fresh=False
    )
    frozen_host = campaign_dir / host.get("frozen_path", "")
    if (
        source_host != host.get("source")
        or not frozen_host.is_file()
        or _sha_file(frozen_host) != host.get("frozen_sha256")
        or json.loads(frozen_host.read_text())
        != json.loads(Path(source_host["path"]).read_text())
    ):
        raise SystemExit("host-capacity receipt source/frozen binding drifted")
    if _active_browser_roots() > HOST_BROWSER_CEILING:
        raise SystemExit("host browser-root ceiling exceeded")
    frozen_root = campaign_dir / manifest["frozen_artifact_root"]
    _verify_inventory(frozen_root, manifest["frozen_artifact_inventory"], "frozen artifact tree")
    for scenario, record in manifest["artifacts"].items():
        _verify_inventory(ROOT / "benchmark_data" / "amazon" / scenario,
                          record["files"], f"runtime {scenario}")
        _verify_inventory(frozen_root / scenario, record["files"], f"frozen {scenario}")
    frozen_cert = campaign_dir / cert["frozen_path"]
    if source_cert != cert["source"] or _sha_file(frozen_cert) != cert["frozen_sha256"]:
        raise SystemExit("certification source/frozen copy drifted")
    lock = manifest["lockdiff"]
    current_lock = _validate_lockdiff(Path(lock["source_validation"]["after"]["path"]))
    if (
        current_lock != lock["source_validation"]
        or _sha_file(campaign_dir / lock["frozen_before_path"])
        != lock["frozen_before_sha256"]
        or _sha_file(campaign_dir / lock["frozen_after_path"])
        != lock["frozen_after_sha256"]
    ):
        raise SystemExit("original-condition lockdiff evidence drifted")
    if not quiet:
        print("VERIFY PASS: exact schedule, sources, artifacts, routes, caps, certification, and lockdiff")
    return manifest


def _profile_for_row(row: dict) -> str:
    del row
    return "hard"


def _apply_environment(manifest: dict, row: dict | None = None) -> dict:
    policy = manifest["runtime_environment_policy"]
    env = dict(os.environ)
    for prefix in policy["sanitize_prefixes"]:
        for key in list(env):
            if key.startswith(prefix):
                env.pop(key, None)
    for key in policy["sanitize_exact"]:
        env.pop(key, None)
    env.update({key: str(value) for key, value in policy["set"].items()})
    if row is not None:
        profile = _profile_for_row(row)
        env["PYTHONPATH"] = os.pathsep.join(
            policy["command_local_pythonpath_profiles"][profile]
        )
        env["TRAPI_REGIONS_OVERRIDE"] = json.dumps(
            {LOGICAL_MODEL: row["region_order"]}, separators=(",", ":")
        )
        env["FURTHER_MODE_ABLATION_RUN_ID"] = row["run_id"]
        env["FURTHER_MODE_ABLATION_STEERING_ARM"] = row["steering_profile"]
        env["FURTHER_MODE_ABLATION_HARNESS_ARM"] = row["harness_arm"]
        env["AGENTARENA_LIMIT_CONTRACT_JSON"] = json.dumps(
            _limit_contract_for_row(manifest, row),
            sort_keys=True, separators=(",", ":"),
        )
    else:
        env["PYTHONPATH"] = str(ROOT)
    if "STOREFRONT_OPS_TOKEN" in env:
        raise SystemExit("STOREFRONT_OPS_TOKEN survived environment sanitization")
    return env


def _predecessor_evidence(campaign_dir: Path, manifest: dict, stage: str) -> list[dict]:
    blocks = manifest["probe_policy"]["predecessor_blocks"].get(stage)
    if blocks is None:
        raise SystemExit(f"unknown probe stage: {stage}")
    evidence = []
    for row in manifest["schedule"]:
        if row["block"] not in blocks:
            continue
        attempt = _current_attempt(campaign_dir, row["run_id"])
        terminal, terminal_hash = _terminal_paths(
            campaign_dir, row["run_id"], attempt
        )
        paths = {
            "summary": campaign_dir / row["summary_relpath"],
            "trajectory": campaign_dir / row["trajectory_relpath"],
            "run_log": campaign_dir / row["run_log_relpath"],
            "launcher_log": campaign_dir / row["launcher_log_relpath"],
            "receipt": campaign_dir / "launch_receipts" / f"{row['run_id']}.json",
            "receipt_hash": campaign_dir / "launch_receipts" / f"{row['run_id']}.sha256.json",
            "terminal_receipt": terminal,
            "terminal_receipt_hash": terminal_hash,
        }
        missing = [name for name, path in paths.items() if not path.is_file()]
        if missing:
            raise SystemExit(
                f"probe {stage} precedes incomplete {row['run_id']}: "
                + ", ".join(missing)
            )
        evidence.append({
            "run_id": row["run_id"],
            "files": {
                name: {"path": str(path.relative_to(campaign_dir)),
                       "sha256": _sha_file(path), "size": path.stat().st_size}
                for name, path in paths.items()
            },
        })
    return evidence


def run_probe(campaign_dir: Path, stage: str, label: str) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify_campaign(campaign_dir, quiet=True)
    label = _safe_id(label)
    blocks = manifest["probe_policy"]["stages"].get(stage)
    if not blocks:
        raise SystemExit(f"unknown probe stage: {stage}")
    predecessor = _predecessor_evidence(campaign_dir, manifest, stage)
    coexistence = _audit_campaign_refill_coexistence(int(manifest["base_port"]))
    checkpoint = campaign_dir / "probes" / f"checkpoint_{label}.json"
    if checkpoint.exists():
        raise SystemExit("probe checkpoint already exists")
    env = _apply_environment(manifest)
    env["TRAPI_REGIONS_OVERRIDE"] = json.dumps(
        {LOGICAL_MODEL: manifest["probe_policy"]["scheduled_regions"][LOGICAL_MODEL]},
        separators=(",", ":"),
    )
    env["AGENTARENA_PROBE_LIVE_ONLY"] = "1"
    env["AGENTARENA_PROBE_INCLUDE_REDMOND"] = "1"
    env["AGENTARENA_PROBE_ALL_REGIONS"] = "1"
    probe_root = campaign_dir / "probes"
    probe_root.mkdir(parents=True, exist_ok=True)
    commands = {
        "small": [sys.executable, str(SCRIPT_DIR / "probe_regions.py"), LOGICAL_MODEL],
        "large": [sys.executable, str(SCRIPT_DIR / "probe_sol_large.py")],
        "concurrency": [sys.executable, str(SCRIPT_DIR / "probe_concurrency.py"), LOGICAL_MODEL],
    }
    paths = {name: probe_root / f"{name}_{label}.log" for name in commands}
    if any(path.exists() for path in paths.values()):
        raise SystemExit("one or more create-only probe logs exist")
    for name in ("small", "large", "concurrency"):
        with paths[name].open("x") as stream:
            completed = subprocess.run(
                commands[name], cwd=ROOT, env=env, stdout=stream,
                stderr=subprocess.STDOUT, text=True,
            )
        if completed.returncode:
            raise SystemExit(f"{name} probe failed; log preserved")
    evidence = validate_probe_evidence(
        manifest, stage, paths["small"], paths["concurrency"], paths["large"]
    )
    _write_new(checkpoint, {
        **evidence,
        "label": label,
        "published_at_utc": _utcnow(),
        "predecessor_evidence": predecessor,
        "refill_coexistence_postcondition": coexistence,
        "logs": {
            name: {"path": str(path.relative_to(campaign_dir)),
                   "sha256": _sha_file(path), "size": path.stat().st_size}
            for name, path in paths.items()
        },
    })
    print(f"PROBE PASS: {label} ({stage})")


def _parse_utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp lacks timezone")
    return parsed.astimezone(dt.timezone.utc)


def verify_probe_checkpoint(campaign_dir: Path, manifest: dict, label: str,
                            row: dict, *, at_utc: str | None = None) -> dict:
    path = campaign_dir / "probes" / f"checkpoint_{_safe_id(label)}.json"
    if not path.is_file():
        raise ValueError("probe checkpoint is missing")
    record = _read_json(path)
    try:
        _validate_campaign_coexistence_record(
            record.get("refill_coexistence_postcondition"),
            int(manifest["base_port"]),
        )
    except ValueError as exc:
        raise ValueError(f"probe refill-reservation evidence drifted: {exc}") from exc
    if (
        record.get("label") != label
        or record.get("stage") != row["probe_stage"]
        or row["block"] not in (record.get("blocks") or [])
        or record.get("predecessor_evidence")
        != _predecessor_evidence(campaign_dir, manifest, row["probe_stage"])
    ):
        raise ValueError("probe checkpoint identity/predecessor drifted")
    paths = {}
    for name in ("small", "large", "concurrency"):
        ref = (record.get("logs") or {}).get(name) or {}
        log = (campaign_dir / ref.get("path", "")).resolve()
        if (
            campaign_dir.resolve() not in log.parents
            or not log.is_file()
            or ref != {"path": str(log.relative_to(campaign_dir)),
                       "sha256": _sha_file(log), "size": log.stat().st_size}
        ):
            raise ValueError(f"probe {name} log drifted")
        paths[name] = log
    expected = validate_probe_evidence(
        manifest, row["probe_stage"], paths["small"], paths["concurrency"],
        paths["large"],
    )
    if any(record.get(key) != value for key, value in expected.items()):
        raise ValueError("probe evidence differs from preserved logs")
    reference = _parse_utc(at_utc or _utcnow())
    age = (reference - _parse_utc(record["published_at_utc"])).total_seconds()
    if age < 0 or age > PROBE_MAX_AGE_SECONDS:
        raise ValueError(f"probe checkpoint is stale/post-dated: {age:.1f}s")
    return {"path": path, "sha256": _sha_file(path), "record": record}


def _expected_instruction(row: dict, manifest: dict) -> str:
    source = ROOT / "benchmark_data" / "amazon" / row["scenario"] / "instructions.json"
    canonical = _read_json(source)[row["variant"]]["text"]
    if row["objective_order"] in ("not_applicable", "original"):
        return canonical
    sidecar = _read_json(Path(manifest["objective_order_sidecar"]["path"]))
    return sidecar[row["objective_order"]]


def run_one(campaign_dir: Path, run_id: str) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify_campaign(campaign_dir, quiet=True)
    _audit_campaign_refill_coexistence(int(manifest["base_port"]))
    matches = [row for row in manifest["schedule"] if row["run_id"] == run_id]
    if len(matches) != 1:
        raise SystemExit("run id is not one exact frozen row")
    row = matches[0]
    if os.environ.get("FURTHER_MODE_ABLATION_RUN_ID") != run_id:
        raise SystemExit("run environment is not bound to frozen row")
    expected_route = json.dumps(
        {LOGICAL_MODEL: row["region_order"]}, separators=(",", ":")
    )
    if os.environ.get("TRAPI_REGIONS_OVERRIDE") != expected_route:
        raise SystemExit("TRAPI route differs from frozen row")
    from agentarena.benchmark import registry
    from agentarena.core.experiment import Experiment, Runner
    tasks = registry.benchmark_tasks(row["scenario"], variants=[row["variant"]])
    if len(tasks) != 1 or tasks[0].task_id != row["task_id"]:
        raise SystemExit("registry did not produce the exact frozen task")
    expected_instruction = _expected_instruction(row, manifest)
    canonical = _read_json(
        ROOT / "benchmark_data" / "amazon" / row["scenario"] / "instructions.json"
    )[row["variant"]]["text"]
    if tasks[0].instruction != canonical:
        raise SystemExit("runtime canonical task instruction drifted")
    if expected_instruction != canonical:
        tasks = [dataclasses.replace(tasks[0], instruction=expected_instruction)]
    experiment = Experiment(
        name=row["run_name"],
        scaffolds=[row["scaffold"]],
        models=[row["model_request"]],
        tasks=tasks,
        conditions=[row["condition"]],
        max_steps={row["scaffold"]: int(manifest["caps"]["max_steps"])},
        base_port=int(row["port"]),
    )
    Runner(results_dir=campaign_dir / "runs", headless=True).run(experiment, jobs=1)
    from agentarena.scoring.rescore import write_preservation, write_strict
    write_preservation(str(campaign_dir / row["experiment_relpath"]))
    write_strict(str(campaign_dir / row["experiment_relpath"]))
    trajectory = _read_json(campaign_dir / row["trajectory_relpath"])
    if trajectory.get("instruction") != expected_instruction:
        raise SystemExit("saved trajectory instruction differs from frozen arm")


def _archived_attempt_numbers(campaign_dir: Path, run_id: str) -> list[int]:
    root = campaign_dir / "excluded_attempts" / run_id
    numbers = []
    if root.is_dir():
        for path in root.glob("attempt_*"):
            try:
                numbers.append(int(path.name.split("_", 1)[1]))
            except (ValueError, IndexError) as exc:
                raise ValueError(f"malformed attempt archive: {path}") from exc
    numbers.sort()
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(f"archived attempt sequence has gaps: {run_id}")
    return numbers


def _current_attempt(campaign_dir: Path, run_id: str) -> int:
    return len(_archived_attempt_numbers(campaign_dir, run_id)) + 1


def _classification_paths(campaign_dir: Path, run_id: str,
                          attempt: int) -> tuple[Path, Path]:
    path = (
        campaign_dir / "attempt_classifications"
        / f"{run_id}_attempt_{attempt}.json"
    )
    return path, path.with_suffix(".sha256.json")


def classify_attempt(campaign_dir: Path, run_id: str) -> dict:
    campaign_dir = campaign_dir.resolve()
    manifest = verify_campaign(campaign_dir, quiet=True)
    matches = [row for row in manifest["schedule"] if row["run_id"] == run_id]
    if len(matches) != 1:
        raise SystemExit("run id is not one frozen row")
    row = matches[0]
    attempt = _current_attempt(campaign_dir, run_id)
    if attempt > MAX_ATTEMPTS:
        raise SystemExit("attempt count exceeds frozen maximum")
    verify_launch_receipt(campaign_dir, manifest, row)
    browser_dir = campaign_dir / row["browser_run_relpath"]
    summary = browser_dir / "summary.json"
    run_log = browser_dir / "run.log"
    launcher = campaign_dir / row["launcher_log_relpath"]
    terminal_path, _ = _terminal_paths(campaign_dir, run_id, attempt)
    terminal = _load_terminal_receipt(campaign_dir, row, attempt)
    if not launcher.is_file():
        raise SystemExit("attempt lacks create-only launcher evidence")
    from ablations.further_mode_ablation_hard_v2.infra_classifier import (
        classify_partial_attempt,
        classify_run,
    )
    if summary.is_file() and run_log.is_file():
        input_mode = "complete_summary_and_run_log"
        classification = classify_run(str(browser_dir))
        inputs = {
            "summary": {"path": row["summary_relpath"],
                        "sha256": _sha_file(summary)},
            "run_log": {"path": row["run_log_relpath"],
                        "sha256": _sha_file(run_log)},
        }
    elif not summary.exists():
        input_mode = "controller_terminal_without_summary"
        classifier_text = launcher.read_text(errors="replace")
        if run_log.is_file():
            classifier_text += "\n" + run_log.read_text(errors="replace")
        classification = classify_partial_attempt(
            terminal, classifier_text
        )
        inputs = ({
            "run_log": {
                "path": row["run_log_relpath"],
                "sha256": _sha_file(run_log),
            }
        } if run_log.is_file() else {})
    else:
        raise SystemExit(
            "attempt has a summary but lacks run.log; classification is ambiguous"
        )
    inputs.update({
        "launcher_log": {
            "path": row["launcher_log_relpath"],
            "sha256": _sha_file(launcher),
        },
        "terminal_receipt": {
            "path": str(terminal_path.relative_to(campaign_dir)),
            "sha256": _sha_file(terminal_path),
        },
    })
    path, hash_path = _classification_paths(campaign_dir, run_id, attempt)
    record = {
        "schema_version": 1,
        "kind": "further_mode_ablation_hard_v2_attempt_classification",
        "created_at_utc": _utcnow(),
        "run_id": run_id,
        "attempt": attempt,
        "row_sha256": _sha_bytes(_json_bytes(row)),
        "classifier": manifest["infrastructure_classifier"],
        "classification": classification,
        "input_mode": input_mode,
        "preference_scores_read": False,
        "inputs": inputs,
        "replacement_eligible": classification.get("class") == "infra",
    }
    _write_new(path, record)
    _write_new(hash_path, {"path": path.name, "sha256": _sha_file(path)})
    print(json.dumps(record, indent=2, sort_keys=True))
    return record


def _load_classification(campaign_dir: Path, manifest: dict, row: dict,
                         attempt: int) -> dict:
    path, hash_path = _classification_paths(
        campaign_dir, row["run_id"], attempt
    )
    if (
        not path.is_file() or not hash_path.is_file()
        or _read_json(hash_path) != {"path": path.name, "sha256": _sha_file(path)}
    ):
        raise ValueError("attempt classification/hash is absent or drifted")
    record = _read_json(path)
    if (
        record.get("schema_version") != 1
        or record.get("kind")
        != "further_mode_ablation_hard_v2_attempt_classification"
        or record.get("run_id") != row["run_id"]
        or record.get("attempt") != attempt
        or record.get("row_sha256") != _sha_bytes(_json_bytes(row))
        or record.get("classifier") != manifest["infrastructure_classifier"]
        or record.get("preference_scores_read") is not False
        or record.get("input_mode") not in {
            "complete_summary_and_run_log",
            "controller_terminal_without_summary",
        }
        or record.get("replacement_eligible") is not (
            record.get("classification", {}).get("class") == "infra"
        )
    ):
        raise ValueError("attempt classification binding drifted")
    return record


def archive_infrastructure_attempt(campaign_dir: Path, run_id: str) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify_campaign(campaign_dir, quiet=True)
    matches = [row for row in manifest["schedule"] if row["run_id"] == run_id]
    if len(matches) != 1:
        raise SystemExit("run id is not one frozen row")
    row = matches[0]
    attempt = _current_attempt(campaign_dir, run_id)
    if attempt >= MAX_ATTEMPTS:
        raise SystemExit("frozen maximum attempts reached; no replacement authorized")
    classification = _load_classification(
        campaign_dir, manifest, row, attempt
    )
    if (
        classification.get("classification", {}).get("class") != "infra"
        or classification.get("replacement_eligible") is not True
    ):
        raise SystemExit("attempt is not deterministically infrastructure-eligible")
    expected_inputs = classification.get("inputs") or {}
    for name, ref in expected_inputs.items():
        source = (campaign_dir / ref.get("path", "")).resolve()
        if (
            campaign_dir not in source.parents
            or not source.is_file()
            or ref.get("sha256") != _sha_file(source)
        ):
            raise SystemExit(
                f"attempt {name} evidence changed after infrastructure classification"
            )
    target = (
        campaign_dir / "excluded_attempts" / run_id / f"attempt_{attempt}"
    )
    terminal_path, terminal_hash_path = _terminal_paths(
        campaign_dir, run_id, attempt
    )
    required_sources = [
        (campaign_dir / row["launcher_log_relpath"], target / "launcher.log"),
        (campaign_dir / "launch_receipts" / f"{run_id}.json",
         target / "launch_receipt.json"),
        (campaign_dir / "launch_receipts" / f"{run_id}.sha256.json",
         target / "launch_receipt.sha256.json"),
        (terminal_path, target / "terminal_receipt.json"),
        (terminal_hash_path, target / "terminal_receipt.sha256.json"),
    ]
    experiment = campaign_dir / row["experiment_relpath"]
    sources = list(required_sources)
    if experiment.exists():
        sources.insert(0, (experiment, target / "experiment"))
    if (
        classification.get("input_mode") == "complete_summary_and_run_log"
        and not experiment.is_dir()
    ):
        raise SystemExit("complete classifier inputs lack experiment directory")
    if target.exists() or any(not source.exists() for source, _ in sources):
        raise SystemExit("attempt archive source/target state is not exact")
    target.mkdir(parents=True)
    moved = []
    for source, destination in sources:
        shutil.move(str(source), str(destination))
        moved.append(destination.name)
    browser_relative = Path(row["browser_run_relpath"]).relative_to(
        row["experiment_relpath"]
    )
    archived_input_paths = {
        "launcher_log": target / "launcher.log",
        "terminal_receipt": target / "terminal_receipt.json",
    }
    archived_browser = target / "experiment" / browser_relative
    if "run_log" in expected_inputs:
        archived_input_paths["run_log"] = archived_browser / "run.log"
    if "summary" in expected_inputs:
        archived_input_paths.update({
            "summary": archived_browser / "summary.json",
        })
    archived_input_refs = {
        name: {
            "path": str(path.relative_to(target)),
            "sha256": _sha_file(path),
        }
        for name, path in sorted(archived_input_paths.items())
    }
    classification_path, _ = _classification_paths(
        campaign_dir, run_id, attempt
    )
    _write_new(target / "archive_record.json", {
        "schema_version": 1,
        "kind": "further_mode_ablation_hard_v2_excluded_infrastructure_attempt",
        "archived_at_utc": _utcnow(),
        "run_id": run_id,
        "attempt": attempt,
        "classification_path": str(classification_path.relative_to(campaign_dir)),
        "classification_sha256": _sha_file(classification_path),
        "classifier": manifest["infrastructure_classifier"],
        "same_frozen_row_and_route": row,
        "moved": moved,
        "classified_input_archive_refs": archived_input_refs,
        "next_attempt": attempt + 1,
        "maximum_attempts": MAX_ATTEMPTS,
    })
    print(f"ARCHIVED INFRA ATTEMPT: {run_id} attempt {attempt}; next={attempt + 1}")


def _receipt_runtime_contract(campaign_dir: Path, manifest: dict,
                              row: dict) -> dict:
    contract = {
        "manifest_sha256": _sha_file(campaign_dir / "campaign_manifest.json"),
        "production_source_inventory_sha256": manifest["production_source_inventory_sha256"],
        "campaign_source_inventory_sha256": manifest["campaign_source_inventory_sha256"],
        "component_source_inventory_sha256": manifest["component_source_inventory_sha256"],
        "runtime_dependencies_sha256": manifest["runtime_dependencies"]["sha256"],
        "runtime_environment_policy_sha256": manifest["runtime_environment_policy"]["sha256"],
        "limit_contract_sha256": _limit_contract_for_row(manifest, row)["sha256"],
        "caps": manifest["caps"],
    }
    return {**contract, "contract_sha256": _sha_bytes(_json_bytes(contract))}


def _write_launch_receipt(campaign_dir: Path, manifest: dict, row: dict,
                          checkpoint_label: str, env: dict,
                          coexistence: dict) -> None:
    launched_at = _utcnow()
    attempt = _current_attempt(campaign_dir, row["run_id"])
    if attempt > MAX_ATTEMPTS:
        raise SystemExit("frozen maximum attempts exceeded")
    checkpoint = verify_probe_checkpoint(
        campaign_dir, manifest, checkpoint_label, row, at_utc=launched_at
    )
    profile = _profile_for_row(row)
    expected_pythonpath = os.pathsep.join(
        manifest["runtime_environment_policy"]["command_local_pythonpath_profiles"][profile]
    )
    expected_route = json.dumps(
        {LOGICAL_MODEL: row["region_order"]}, separators=(",", ":")
    )
    if env.get("PYTHONPATH") != expected_pythonpath or env.get("TRAPI_REGIONS_OVERRIDE") != expected_route:
        raise SystemExit("launch environment differs from frozen row")
    row_limit_contract = _limit_contract_for_row(manifest, row)
    try:
        observed_limit_contract = json.loads(
            env["AGENTARENA_LIMIT_CONTRACT_JSON"]
        )
    except (KeyError, json.JSONDecodeError) as exc:
        raise SystemExit("per-row limit contract is absent/malformed") from exc
    if observed_limit_contract != row_limit_contract:
        raise SystemExit("per-row limit contract differs from frozen profile")
    _validate_campaign_coexistence_record(
        coexistence, int(manifest["base_port"])
    )
    runtime_contract = _receipt_runtime_contract(campaign_dir, manifest, row)
    receipt = {
        "schema_version": 1,
        "kind": "further_mode_ablation_hard_v2_launch_receipt",
        "run_id": row["run_id"],
        "attempt": attempt,
        "row": row,
        "launched_at_utc": launched_at,
        "probe_checkpoint": checkpoint_label,
        "probe_checkpoint_sha256": checkpoint["sha256"],
        "pythonpath_profile": profile,
        "pythonpath": expected_pythonpath,
        "trapi_regions_override": expected_route,
        "limit_contract_json_sha256": _sha_bytes(
            env["AGENTARENA_LIMIT_CONTRACT_JSON"].encode()
        ),
        "expected_instruction_sha256": _sha_bytes(
            _expected_instruction(row, manifest).encode()
        ),
        "runtime_contract": runtime_contract,
        "refill_coexistence_postcondition": coexistence,
    }
    path = campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
    _write_new(path, receipt)
    _write_new(path.with_suffix(".sha256.json"), {
        "path": path.name, "sha256": _sha_file(path),
    })


def verify_launch_receipt(campaign_dir: Path, manifest: dict, row: dict) -> dict:
    path = campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
    sidecar = path.with_suffix(".sha256.json")
    if (
        not path.is_file() or not sidecar.is_file()
        or _read_json(sidecar) != {"path": path.name, "sha256": _sha_file(path)}
    ):
        raise ValueError("create-only launch receipt/hash is missing or drifted")
    record = _read_json(path)
    if (
        record.get("schema_version") != 1
        or record.get("kind")
        != "further_mode_ablation_hard_v2_launch_receipt"
        or record.get("row") != row
        or record.get("run_id") != row["run_id"]
    ):
        raise ValueError("launch receipt row identity drifted")
    _validate_campaign_coexistence_record(
        record.get("refill_coexistence_postcondition"),
        int(manifest["base_port"]),
    )
    if record.get("attempt") != _current_attempt(campaign_dir, row["run_id"]):
        raise ValueError("launch receipt attempt identity drifted")
    expected_limit_json = json.dumps(
        _limit_contract_for_row(manifest, row),
        sort_keys=True, separators=(",", ":"),
    )
    if (
        record.get("attempt") != _current_attempt(campaign_dir, row["run_id"])
        or record.get("pythonpath_profile") != _profile_for_row(row)
        or record.get("pythonpath") != os.pathsep.join(
            manifest["runtime_environment_policy"]
            ["command_local_pythonpath_profiles"][_profile_for_row(row)]
        )
        or record.get("trapi_regions_override") != json.dumps(
            {LOGICAL_MODEL: row["region_order"]}, separators=(",", ":")
        )
        or record.get("expected_instruction_sha256") != _sha_bytes(
            _expected_instruction(row, manifest).encode()
        )
        or record.get("runtime_contract")
        != _receipt_runtime_contract(campaign_dir, manifest, row)
        or
        record.get("runtime_contract", {}).get("limit_contract_sha256")
        != _limit_contract_for_row(manifest, row)["sha256"]
        or record.get("limit_contract_json_sha256")
        != _sha_bytes(expected_limit_json.encode())
    ):
        raise ValueError("launch receipt per-row limit contract drifted")
    checkpoint = verify_probe_checkpoint(
        campaign_dir, manifest, record["probe_checkpoint"], row,
        at_utc=record["launched_at_utc"],
    )
    if record.get("probe_checkpoint_sha256") != checkpoint["sha256"]:
        raise ValueError("launch receipt probe-checkpoint hash drifted")
    return record


def _database_order_count(experiment: Path) -> int:
    """Return committed checkout rows, or -1 when absence cannot be proved."""
    total = 0
    databases = list(experiment.rglob("amazon_*.db")) \
        if experiment.is_dir() else []
    if not databases:
        return -1
    for path in databases:
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                tables = {
                    value[0] for value in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if "order" not in tables or "orderitem" not in tables:
                    return -1
                order_rows = int(connection.execute(
                    'SELECT COUNT(*) FROM "order"'
                ).fetchone()[0])
                orderitem_rows = int(connection.execute(
                    "SELECT COUNT(*) FROM orderitem"
                ).fetchone()[0])
                if (
                    order_rows < AMAZON_SEEDED_ORDER_ROWS
                    or orderitem_rows < AMAZON_SEEDED_ORDERITEM_ROWS
                ):
                    return -1
                total += order_rows - AMAZON_SEEDED_ORDER_ROWS
                total += orderitem_rows - AMAZON_SEEDED_ORDERITEM_ROWS
            finally:
                connection.close()
        except sqlite3.Error:
            return -1
    return total


def _terminal_paths(campaign_dir: Path, run_id: str,
                    attempt: int) -> tuple[Path, Path]:
    path = (
        campaign_dir / "attempt_terminals"
        / f"{run_id}_attempt_{attempt}.json"
    )
    return path, path.with_suffix(".sha256.json")


def _write_terminal_receipt(campaign_dir: Path, row: dict, attempt: int,
                            *, pid: int | None, returncode: int | None,
                            spawn_exception: BaseException | None = None) -> None:
    launcher = campaign_dir / row["launcher_log_relpath"]
    experiment = campaign_dir / row["experiment_relpath"]
    summary = campaign_dir / row["summary_relpath"]
    trajectory = campaign_dir / row["trajectory_relpath"]
    agent_log = campaign_dir / row["run_log_relpath"]
    launch_receipt = (
        campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
    )
    launcher_text = launcher.read_text(errors="replace") if launcher.is_file() else ""
    agent_text = agent_log.read_text(errors="replace") if agent_log.is_file() else ""
    behavior_text = f"{launcher_text}\n{agent_text}"
    step_evidence = bool(
        re.search(
            r"(?:^|\n).{0,80}(?:Step\s+\d+|Result failed \d+/\d+|"
            r"browser\.act|ActionResult|AgentHistoryList)",
            behavior_text,
            re.IGNORECASE,
        )
        or trajectory.is_file()
    )
    record = {
        "schema_version": 1,
        "kind": "further_mode_ablation_hard_v2_attempt_terminal",
        "run_id": row["run_id"],
        "attempt": attempt,
        "row_sha256": _sha_bytes(_json_bytes(row)),
        "finished_at_utc": _utcnow(),
        "process_pid": pid,
        "process_ended": True,
        "returncode": returncode,
        "spawn_exception_type": (
            type(spawn_exception).__name__ if spawn_exception else None
        ),
        "spawn_exception_message": (
            str(spawn_exception)[:1000] if spawn_exception else None
        ),
        "summary_exists": summary.is_file(),
        "trajectory_exists": trajectory.is_file(),
        "agent_run_log_exists": agent_log.is_file(),
        "agent_run_log_sha256": _sha_file(agent_log) if agent_log.is_file() else None,
        "logged_agent_step_evidence": step_evidence,
        "database_order_count": _database_order_count(experiment),
        "launcher_log_sha256": _sha_file(launcher) if launcher.is_file() else None,
        "launch_receipt_sha256": (
            _sha_file(launch_receipt) if launch_receipt.is_file() else None
        ),
        "experiment_inventory": (
            _tree_inventory(experiment) if experiment.is_dir() else {}
        ),
    }
    path, hash_path = _terminal_paths(campaign_dir, row["run_id"], attempt)
    _write_new(path, record)
    _write_new(hash_path, {"path": path.name, "sha256": _sha_file(path)})


def _load_terminal_receipt(campaign_dir: Path, row: dict,
                           attempt: int) -> dict:
    path, hash_path = _terminal_paths(campaign_dir, row["run_id"], attempt)
    if (
        not path.is_file() or not hash_path.is_file()
        or _read_json(hash_path) != {"path": path.name, "sha256": _sha_file(path)}
    ):
        raise ValueError("attempt terminal receipt/hash is absent or drifted")
    record = _read_json(path)
    if (
        record.get("kind")
        != "further_mode_ablation_hard_v2_attempt_terminal"
        or record.get("run_id") != row["run_id"]
        or record.get("attempt") != attempt
        or record.get("row_sha256") != _sha_bytes(_json_bytes(row))
        or record.get("process_ended") is not True
    ):
        raise ValueError("attempt terminal receipt identity drifted")
    launcher = campaign_dir / row["launcher_log_relpath"]
    launch_receipt = (
        campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
    )
    if (
        launcher.is_file()
        and record.get("launcher_log_sha256") != _sha_file(launcher)
    ):
        raise ValueError("terminal receipt launcher hash drifted")
    if (
        not launch_receipt.is_file()
        or record.get("launch_receipt_sha256") != _sha_file(launch_receipt)
    ):
        raise ValueError("terminal receipt launch-receipt hash drifted")
    experiment = campaign_dir / row["experiment_relpath"]
    if record.get("experiment_inventory") != (
        _tree_inventory(experiment) if experiment.is_dir() else {}
    ):
        raise ValueError("terminal receipt experiment inventory drifted")
    agent_log = campaign_dir / row["run_log_relpath"]
    if record.get("agent_run_log_exists") is not agent_log.is_file():
        raise ValueError("terminal receipt run-log existence drifted")
    if agent_log.is_file() and record.get("agent_run_log_sha256") != _sha_file(agent_log):
        raise ValueError("terminal receipt run-log hash drifted")
    return record


def _active_run_processes() -> int:
    count = 0
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            args = (path / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if b"-m" in args and b"agentarena.run_cell" in args:
            count += 1
    return count


def _acquire_host_browser_lock(campaign_dir: Path):
    path = Path("/tmp/agentarena-clone8-browser.lock")
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.seek(0)
        owner = handle.read().strip() or "unknown"
        handle.close()
        raise SystemExit(f"host browser campaign lock is held by {owner}") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()} campaign={campaign_dir.resolve()}\n")
    handle.flush()
    os.fsync(handle.fileno())
    return handle


def launch_block(campaign_dir: Path, block: int, checkpoint: str,
                 confirmation: str) -> None:
    lock = _acquire_host_browser_lock(campaign_dir)
    try:
        _launch_block_locked(campaign_dir, block, checkpoint, confirmation)
    finally:
        lock.close()


def _launch_block_locked(campaign_dir: Path, block: int, checkpoint: str,
                         confirmation: str) -> None:
    if confirmation != LAUNCH_CONFIRMATION:
        raise SystemExit(f"launch requires --confirm {LAUNCH_CONFIRMATION}")
    campaign_dir = campaign_dir.resolve()
    manifest = verify_campaign(campaign_dir, quiet=True)
    initial_coexistence = _audit_campaign_refill_coexistence(
        int(manifest["base_port"])
    )
    rows = sorted(
        (row for row in manifest["schedule"] if row["block"] == block),
        key=lambda row: row["spawn_index"],
    )
    if not rows:
        raise SystemExit(f"unknown block: {block}")
    if not _ports_free(row["port"] for row in rows):
        raise SystemExit("scheduled block port band is busy")
    for row in rows:
        verify_probe_checkpoint(campaign_dir, manifest, checkpoint, row)
    pending = []
    for row in rows:
        summary = campaign_dir / row["summary_relpath"]
        evidence = (
            campaign_dir / row["experiment_relpath"],
            campaign_dir / row["launcher_log_relpath"],
            campaign_dir / "launch_receipts" / f"{row['run_id']}.json",
            *_terminal_paths(
                campaign_dir, row["run_id"],
                _current_attempt(campaign_dir, row["run_id"]),
            ),
        )
        if summary.is_file():
            verify_launch_receipt(campaign_dir, manifest, row)
            print(f"already complete, preserving: {row['run_id']}")
            continue
        if any(path.exists() for path in evidence):
            raise SystemExit(f"partial evidence exists for {row['run_id']}; relaunch prohibited")
        pending.append(row)
    if not pending:
        print(f"block {block}: already complete")
        return
    external = initial_coexistence["active_browser_roots"]
    available = min(
        MAX_PARALLEL_RUNS,
        HOST_BROWSER_CEILING - REFILL_BROWSER_RESERVE - external,
    )
    if available <= 0:
        raise SystemExit(
            f"host {HOST_BROWSER_CEILING}-browser gate is full ({external} active)"
        )
    max_parallel = available
    interrupted = False

    def defer_interrupt(signum, _frame):
        nonlocal interrupted
        interrupted = True
        print(f"signal {signum}: waiting for active runs; stopping new spawns",
              file=sys.stderr, flush=True)

    old_int = signal.signal(signal.SIGINT, defer_interrupt)
    old_term = signal.signal(signal.SIGTERM, defer_interrupt)
    active = []
    queue = list(pending)
    failures = []
    try:
        while queue or active:
            while queue and len(active) < max_parallel and not interrupted:
                coexistence = _audit_campaign_refill_coexistence(
                    int(manifest["base_port"])
                )
                current_roots = coexistence["active_browser_roots"]
                if current_roots >= HOST_BROWSER_CEILING - REFILL_BROWSER_RESERVE:
                    if not active:
                        raise SystemExit(
                            "host browser-root ceiling reached before next spawn"
                        )
                    break
                row = queue.pop(0)
                env = _apply_environment(manifest, row)
                _write_launch_receipt(
                    campaign_dir, manifest, row, checkpoint, env, coexistence
                )
                log = campaign_dir / row["launcher_log_relpath"]
                log.parent.mkdir(parents=True, exist_ok=True)
                stream = log.open("x")
                command = [
                    sys.executable, str(Path(__file__).resolve()), "run-one",
                    "--campaign-dir", str(campaign_dir), "--run-id", row["run_id"],
                ]
                try:
                    process = subprocess.Popen(
                        command, cwd=ROOT, env=env, stdout=stream,
                        stderr=subprocess.STDOUT, text=True, start_new_session=True,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    stream.write(
                        "AGENTARENA_RUN_PROCESS_SPAWN_FAILED "
                        f"{type(exc).__name__}: {exc}\n"
                    )
                    stream.flush()
                    os.fsync(stream.fileno())
                    stream.close()
                    _write_terminal_receipt(
                        campaign_dir, row,
                        _current_attempt(campaign_dir, row["run_id"]),
                        pid=None, returncode=None, spawn_exception=exc,
                    )
                    failures.append((row["run_id"], "spawn_exception"))
                    interrupted = True
                    break
                active.append((row, process, stream))
                print(
                    f"launch {row['run_id']} primary={row['primary_region']} "
                    f"port={row['port']}", flush=True,
                )
                if queue:
                    time.sleep(CAPS["spawn_stagger_seconds"])
            still = []
            for row, process, stream in active:
                code = process.poll()
                if code is None:
                    still.append((row, process, stream))
                    continue
                stream.flush()
                os.fsync(stream.fileno())
                stream.close()
                _write_terminal_receipt(
                    campaign_dir, row,
                    _current_attempt(campaign_dir, row["run_id"]),
                    pid=process.pid, returncode=code,
                )
                if code or not (campaign_dir / row["summary_relpath"]).is_file():
                    failures.append((row["run_id"], code))
            active = still
            if active or queue:
                time.sleep(1.5)
            if interrupted and not active:
                break
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
        for active_row, process, stream in active:
            if process.poll() is None:
                process.wait()
            if not stream.closed:
                stream.flush()
                os.fsync(stream.fileno())
                stream.close()
            terminal_path, _ = _terminal_paths(
                campaign_dir, active_row["run_id"],
                _current_attempt(campaign_dir, active_row["run_id"]),
            )
            if not terminal_path.exists():
                _write_terminal_receipt(
                    campaign_dir, active_row,
                    _current_attempt(campaign_dir, active_row["run_id"]),
                    pid=process.pid, returncode=process.returncode,
                )
    if failures or queue:
        raise SystemExit(
            f"block {block} incomplete; evidence preserved; failures={failures}, "
            f"unlaunched={[row['run_id'] for row in queue]}"
        )
    print(f"BLOCK {block} COMPLETE ({len(rows)} runs)")


def status(campaign_dir: Path) -> dict:
    campaign_dir = campaign_dir.resolve()
    manifest = verify_campaign(campaign_dir, quiet=True)
    blocks = []
    for block in range(1, 5):
        rows = [row for row in manifest["schedule"] if row["block"] == block]
        blocks.append({
            "block": block,
            "scheduled": len(rows),
            "launched": sum(
                (campaign_dir / "launch_receipts" / f"{row['run_id']}.json").is_file()
                for row in rows
            ),
            "complete": sum(
                (campaign_dir / row["summary_relpath"]).is_file() for row in rows
            ),
        })
    record = {
        "campaign_id": manifest["campaign_id"],
        "scheduled": 112,
        "launched": sum(item["launched"] for item in blocks),
        "complete": sum(item["complete"] for item in blocks),
        "blocks": blocks,
        "active_browser_roots": _active_browser_roots(),
        "host_browser_ceiling": HOST_BROWSER_CEILING,
        "host_capacity_green": _active_browser_roots() <= HOST_BROWSER_CEILING,
    }
    print(json.dumps(record, indent=2, sort_keys=True))
    return record


def _hero_and_choice(manifest: dict, campaign_dir: Path, row: dict,
                     chosen: str | None) -> dict:
    pool_path = (
        campaign_dir / manifest["frozen_artifact_root"]
        / row["scenario"] / "pool.json"
    )
    pool = json.loads(pool_path.read_text())
    if not isinstance(pool, list):
        raise ValueError(f"{row['run_id']}: frozen pool is malformed")
    heroes = [
        product for product in pool
        if product.get("decoy_kind") == "hero" or product.get("role") == "hero"
    ]
    if len(heroes) != 1:
        raise ValueError(f"{row['run_id']}: frozen pool lacks one literal hero")
    by_asin = {product.get("asin"): product for product in pool}
    product = by_asin.get(chosen) if chosen is not None else None
    return {
        "hero_asin": heroes[0]["asin"],
        "hero": int(chosen == heroes[0]["asin"]),
        "chosen_role": product.get("role") if product else None,
        "chosen_decoy_kind": product.get("decoy_kind") if product else None,
        "chosen_in_frozen_catalog": chosen is None or product is not None,
    }


def _trajectory_navigation(trajectory: dict) -> dict:
    dp = set()
    search_pages = set()
    for step in trajectory.get("steps") or []:
        raw = str(step.get("url") or "")
        match = re.search(r"/dp/([A-Za-z0-9_-]+)", raw)
        if match:
            dp.add(match.group(1))
        if "/s" in raw:
            page_match = re.search(r"(?:[?&]page=|/page/)(\d+)", raw)
            search_pages.add(int(page_match.group(1)) if page_match else 1)
    return {
        "unique_pdp_asins": sorted(dp),
        "unique_pdp_count": len(dp),
        "search_pages": sorted(search_pages),
        "search_page_count": len(search_pages),
    }


def _contract_semantically_correct(raw: object, gold: dict) -> bool | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        contract = json.loads(raw)
    except json.JSONDecodeError:
        return False
    constraints = contract.get("constraints")
    objectives = contract.get("objectives")
    if (
        contract.get("search_mode") != gold["search_mode"]
        or not isinstance(constraints, list)
        or not isinstance(objectives, list)
        or len(constraints) != len(gold["constraints"])
        or len(objectives) != len(gold["objectives"])
    ):
        return False
    unmatched = list(constraints)
    for expected in gold["constraints"]:
        found = None
        for item in unmatched:
            text = " ".join(str(item.get(key) or "").lower()
                            for key in ("criterion_id", "description"))
            direct_value_match = (
                item.get("operator") == expected["operator"]
                and item.get("expected") == expected["expected"]
            )
            nongaming_equivalent = (
                expected.get("field") == "gaming"
                and expected.get("operator") == "eq"
                and expected.get("expected") is False
                and item.get("operator") == "ne"
                and str(item.get("expected") or "").strip().lower()
                in {"gaming", "gaming laptop"}
                and ("gaming" in text or "laptop_type" in text)
            )
            if (
                (any(alias in text for alias in expected["aliases"])
                 or nongaming_equivalent)
                and (direct_value_match or nongaming_equivalent)
            ):
                found = item
                break
        if found is None:
            return False
        unmatched.remove(found)
    unmatched_objectives = list(objectives)
    for expected in gold["objectives"]:
        found = None
        for item in unmatched_objectives:
            text = " ".join(str(item.get(key) or "").lower()
                            for key in ("criterion_id", "description"))
            if (
                any(alias in text for alias in expected["aliases"])
                and item.get("direction") == expected["direction"]
                and item.get("priority") is None
                and item.get("weight") is None
            ):
                found = item
                break
        if found is None:
            return False
        unmatched_objectives.remove(found)
    return True


def _process_endpoints(row: dict, deliberative: dict, chosen: str | None,
                       navigation: dict, gold: dict) -> dict:
    compile_expected = row["harness_arm"] in {"K", "E", "D", "A", "F"}
    compile_calls = deliberative.get("contract_compile_calls")
    contract_correct = _contract_semantically_correct(
        deliberative.get("contract_canonical_json"), gold
    ) if compile_expected else None
    approved = deliberative.get("approved_candidate_id")
    inspected = deliberative.get("frontier_inspected_count")
    advertised = deliberative.get("frontier_advertised_count")
    unresolved = deliberative.get("frontier_unresolved_count")
    exhausted = deliberative.get("frontier_exhausted")
    return {
        "compiler_expected": compile_expected,
        "contract_compile_calls": compile_calls,
        "contract_compile_rejections": deliberative.get("contract_compile_rejections"),
        "contract_compile_failures": deliberative.get("contract_compile_failures"),
        "contract_semantically_correct_vs_gold": (
            int(contract_correct) if contract_correct is not None else None
        ),
        "decision_checkpoint_calls": deliberative.get("decision_checkpoint_calls"),
        "decision_checkpoint_rejections": deliberative.get("decision_checkpoint_rejections"),
        "decision_checkpoint_approvals": deliberative.get("decision_checkpoint_approvals"),
        "first_submission_would_pass_full": deliberative.get("first_submission_would_pass_full"),
        "last_submission_would_pass_full": deliberative.get("last_submission_would_pass_full"),
        "frontier_inspected_count": inspected,
        "frontier_advertised_count": advertised,
        "frontier_advertised_page_count": deliberative.get("frontier_advertised_page_count"),
        "frontier_enumerated_page_count": deliberative.get("frontier_enumerated_page_count"),
        "frontier_unresolved_count": unresolved,
        "frontier_exhausted": exhausted,
        "frontier_resolved_and_exhausted": (
            int(unresolved == 0 and exhausted is True)
            if unresolved is not None and exhausted is not None else None
        ),
        "approved_candidate_id": approved,
        "approved_matches_final_choice": (
            int(approved == chosen) if approved is not None and chosen is not None
            else None
        ),
        "auxiliary_calls": deliberative.get("auxiliary_calls"),
        "auxiliary_tokens": deliberative.get("auxiliary_tokens"),
        "auxiliary_seconds": deliberative.get("auxiliary_seconds"),
        "unique_pdp_count": navigation["unique_pdp_count"],
        "search_page_count": navigation["search_page_count"],
        "pdp_to_frontier_ratio": (
            navigation["unique_pdp_count"] / inspected
            if isinstance(inspected, int) and inspected > 0 else None
        ),
    }


def _limit_kind(row: dict) -> str:
    return "deliberative" if row["harness_arm"] in {
        "K", "E", "D", "C", "A", "F"
    } else "baseline"


def _attempt_chain(campaign_dir: Path, manifest: dict, row: dict) -> list[dict]:
    chain = []
    for attempt in _archived_attempt_numbers(campaign_dir, row["run_id"]):
        root = (
            campaign_dir / "excluded_attempts" / row["run_id"]
            / f"attempt_{attempt}"
        )
        archive_path = root / "archive_record.json"
        if not archive_path.is_file():
            raise ValueError(f"{row['run_id']}: archived attempt lacks record")
        archive = _read_json(archive_path)
        classification = _load_classification(
            campaign_dir, manifest, row, attempt
        )
        browser_relative = Path(row["browser_run_relpath"]).relative_to(
            row["experiment_relpath"]
        )
        archived_browser = root / "experiment" / browser_relative
        input_mode = classification.get("input_mode")
        expected_moved = [
            *( ["experiment"] if (root / "experiment").exists() else [] ),
            "launcher.log", "launch_receipt.json",
            "launch_receipt.sha256.json", "terminal_receipt.json",
            "terminal_receipt.sha256.json",
        ]
        expected_input_paths = {
            "launcher_log": root / "launcher.log",
            "terminal_receipt": root / "terminal_receipt.json",
        }
        classified_input_names = set(classification.get("inputs") or {})
        if "run_log" in classified_input_names:
            expected_input_paths["run_log"] = archived_browser / "run.log"
        if "summary" in classified_input_names:
            expected_input_paths.update({
                "summary": archived_browser / "summary.json",
            })
        expected_archive_refs = {
            name: {
                "path": str(path.relative_to(root)),
                "sha256": _sha_file(path),
            }
            for name, path in sorted(expected_input_paths.items())
            if path.is_file()
        }
        terminal = _read_json(root / "terminal_receipt.json") \
            if (root / "terminal_receipt.json").is_file() else {}
        terminal_sidecar = root / "terminal_receipt.sha256.json"
        if (
            archive.get("schema_version") != 1
            or archive.get("kind")
            != "further_mode_ablation_hard_v2_excluded_infrastructure_attempt"
            or archive.get("run_id") != row["run_id"]
            or archive.get("attempt") != attempt
            or archive.get("classifier") != manifest["infrastructure_classifier"]
            or archive.get("same_frozen_row_and_route") != row
            or archive.get("next_attempt") != attempt + 1
            or archive.get("maximum_attempts") != MAX_ATTEMPTS
            or archive.get("moved") != expected_moved
            or archive.get("classified_input_archive_refs")
            != expected_archive_refs
            or archive.get("classification_sha256")
            != _sha_file(_classification_paths(
                campaign_dir, row["run_id"], attempt
            )[0])
            or classification.get("classification", {}).get("class") != "infra"
            or set(classification.get("inputs") or {})
            != set(expected_archive_refs)
            or any(
                classification["inputs"][name].get("sha256")
                != ref["sha256"]
                for name, ref in expected_archive_refs.items()
            )
            or terminal.get("run_id") != row["run_id"]
            or terminal.get("attempt") != attempt
            or terminal.get("row_sha256") != _sha_bytes(_json_bytes(row))
            or terminal.get("process_ended") is not True
            or terminal.get("launcher_log_sha256")
            != _sha_file(root / "launcher.log")
            or terminal.get("launch_receipt_sha256")
            != _sha_file(root / "launch_receipt.json")
            or not terminal_sidecar.is_file()
            or _read_json(terminal_sidecar) != {
                "path": _terminal_paths(
                    campaign_dir, row["run_id"], attempt
                )[0].name,
                "sha256": _sha_file(root / "terminal_receipt.json"),
            }
            or terminal.get("experiment_inventory") != (
                _tree_inventory(root / "experiment")
                if (root / "experiment").is_dir() else {}
            )
            or input_mode not in {
                "complete_summary_and_run_log",
                "controller_terminal_without_summary",
            }
            or (
                input_mode == "complete_summary_and_run_log"
                and classified_input_names != {
                    "summary", "run_log", "launcher_log", "terminal_receipt"
                }
            )
            or (
                input_mode == "controller_terminal_without_summary"
                and classified_input_names not in (
                    {"launcher_log", "terminal_receipt"},
                    {"run_log", "launcher_log", "terminal_receipt"},
                )
            )
            or (
                input_mode == "complete_summary_and_run_log"
                and (
                    not (archived_browser / "summary.json").is_file()
                    or not (archived_browser / "run.log").is_file()
                )
            )
            or (
                input_mode == "controller_terminal_without_summary"
                and (archived_browser / "summary.json").exists()
            )
        ):
            raise ValueError(f"{row['run_id']}: archived attempt binding drifted")
        inventory = _tree_inventory(root)
        chain.append({
            "attempt": attempt,
            "disposition": "excluded_infrastructure_replacement",
            "classification": classification["classification"],
            "archive_record_sha256": _sha_file(archive_path),
            "archive_inventory_sha256": _inventory_sha(inventory),
        })
    current = _current_attempt(campaign_dir, row["run_id"])
    if current > MAX_ATTEMPTS:
        raise ValueError(f"{row['run_id']}: attempt chain exceeds maximum")
    return chain


def _resolve_terminal_scores(summary: dict, classification: dict,
                             attempt: int, run_id: str) -> tuple[float, float, bool]:
    if classification.get("class") == "infra":
        disposition = (
            "replacement_eligible" if attempt < MAX_ATTEMPTS
            else "infrastructure_exhausted"
        )
        raise ValueError(
            f"{run_id}: terminal attempt is infrastructure ({disposition}); "
            "complete report prohibited"
        )
    pstar = summary.get("preservation_strict")
    strict = summary.get("strict_binary")
    numeric_scores = all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(float(value)) and 0 <= float(value) <= 1
        for value in (pstar, strict)
    )
    behavioral_failure_zero = not numeric_scores
    if behavioral_failure_zero:
        if classification.get("class") not in {"capability", "ambiguous"}:
            raise ValueError(
                f"{run_id}: missing scores lack behavioral classifier basis"
            )
        return 0.0, 0.0, True
    pstar, strict = float(pstar), float(strict)
    if strict not in {0.0, 1.0}:
        raise ValueError(f"{run_id}: strict_binary is not exactly binary")
    return pstar, strict, False


def _validated_result(campaign_dir: Path, manifest: dict, row: dict) -> dict:
    receipt = verify_launch_receipt(campaign_dir, manifest, row)
    attempt_chain = _attempt_chain(campaign_dir, manifest, row)
    terminal_path, terminal_hash_path = _terminal_paths(
        campaign_dir, row["run_id"], receipt["attempt"]
    )
    paths = {
        "summary": campaign_dir / row["summary_relpath"],
        "trajectory": campaign_dir / row["trajectory_relpath"],
        "run_log": campaign_dir / row["run_log_relpath"],
        "launcher_log": campaign_dir / row["launcher_log_relpath"],
        "terminal_receipt": terminal_path,
        "terminal_receipt_hash": terminal_hash_path,
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise ValueError(
            f"{row['run_id']}: missing terminal evidence: {', '.join(missing)}"
        )
    summary = _read_json(paths["summary"])
    trajectory = _read_json(paths["trajectory"])
    terminal = _load_terminal_receipt(
        campaign_dir, row, receipt["attempt"]
    )
    if (
        summary.get("scaffold") != row["scaffold"]
        or summary.get("model") != row["model_recorded"]
        or summary.get("task_id") != row["task_id"]
        or summary.get("condition") != row["condition"]
        or trajectory.get("scaffold") != row["scaffold"]
        or trajectory.get("model") != row["model_recorded"]
        or trajectory.get("task_id") != row["task_id"]
        or trajectory.get("condition") != row["condition"]
        or trajectory.get("instruction") != _expected_instruction(row, manifest)
        or terminal.get("summary_exists") is not True
    ):
        raise ValueError(f"{row['run_id']}: summary/trajectory identity drifted")
    chosen = summary.get("chosen")
    from ablations.further_mode_ablation_hard_v2.infra_classifier import (
        classify_run,
    )
    terminal_classification = classify_run(str(campaign_dir / row["browser_run_relpath"]))
    pstar, strict, behavioral_failure_zero = _resolve_terminal_scores(
        summary, terminal_classification, receipt["attempt"], row["run_id"]
    )
    choice = _hero_and_choice(manifest, campaign_dir, row, chosen)
    if chosen is not None and not choice["chosen_in_frozen_catalog"]:
        raise ValueError(f"{row['run_id']}: chosen item is outside frozen catalog")
    stats = trajectory.get("stats") or {}
    steps = int(summary.get("num_steps", stats.get("num_steps", 0)))
    audit = stats.get("limit_audit")
    store = stats.get("evaluate_result_store")
    pre_agent_compiler_exception = (
        terminal_classification.get("class") == "capability"
        and terminal_classification.get("code")
        == "compiler_semantic_or_output_failure"
        and behavioral_failure_zero
        and chosen is None
        and steps == 0
        and audit is None
        and store is None
    )
    if pre_agent_compiler_exception:
        touched = None
        utilization = None
        limit_audit_status = (
            "not_initialized_pre_agent_compiler_capability_failure"
        )
    else:
        audit_errors = validate_limit_audit(
            audit, _limit_contract_for_row(manifest, row), _limit_kind(row)
        )
        if audit_errors:
            raise ValueError(
                f"{row['run_id']}: limit audit invalid: "
                + "; ".join(audit_errors)
            )
        touched = []
        for category in ("safety_backstops", "lossy_context_limits"):
            for name, record in (
                (audit.get("categories", {}).get(category, {}) or {}).items()
            ):
                if record.get("touched_count"):
                    touched.append(f"{category}.{name}")
        if touched:
            raise ValueError(
                f"{row['run_id']}: a backstop/lossy bound touched: "
                + ", ".join(touched)
            )
        if not isinstance(store, dict):
            raise ValueError(f"{row['run_id']}: evaluate-result-store audit absent")
        try:
            utilization = {
                "single_chars": store["max_serialized_chars"] / store["single_max_chars"],
                "store_bytes": store["bytes"] / store["max_bytes"],
                "store_responses": store["responses"] / store["max_responses"],
            }
            near_policy = _limit_contract_for_row(manifest, row)["near_policy"]
            thresholds = {
                "single_chars": near_policy["evaluate_result_single_fraction"],
                "store_bytes": near_policy["evaluate_result_store_fraction"],
                "store_responses": near_policy["evaluate_result_store_fraction"],
            }
        except (KeyError, TypeError, ZeroDivisionError) as exc:
            raise ValueError(
                f"{row['run_id']}: evaluate-result audit malformed: {exc}"
            ) from exc
        near = [
            name for name, value in utilization.items()
            if value >= thresholds[name]
        ]
        if near:
            raise ValueError(
                f"{row['run_id']}: evaluate-result safety bound is near: "
                + ", ".join(near)
            )
        limit_audit_status = "complete_and_untouched"
    deliberative = stats.get("deliberative") or {}
    navigation = _trajectory_navigation(trajectory)
    gold_contract = _read_json(Path(manifest["gold_task_contract"]["path"]))
    seconds = float(summary.get("seconds", stats.get("seconds", 0.0)))
    backstop_utilization = {
        "steps": steps / manifest["caps"]["max_steps"],
        "whole_run_seconds": seconds / manifest["caps"]["cell_timeout_seconds"],
    }
    if any(
        value >= manifest["limit_contract"]["near_policy"]["whole_run_fraction"]
        for value in backstop_utilization.values()
    ):
        raise ValueError(f"{row['run_id']}: run is near a step/time backstop")
    return {
        "run_id": row["run_id"],
        "block": row["block"],
        "repeat": row["repeat"],
        "study": row["study"],
        "arm_id": row["arm_id"],
        "harness_arm": row["harness_arm"],
        "scenario": row["scenario"],
        "variant": row["variant"],
        "condition": row["condition"],
        "objective_order": row["objective_order"],
        "scaffold": row["scaffold"],
        "primary_region": row["primary_region"],
        "outcome": summary.get("outcome"),
        "chosen": chosen,
        **choice,
        "behavioral_no_order": chosen is None,
        "behavioral_failure_zero": behavioral_failure_zero,
        "terminal_infrastructure_classification": terminal_classification,
        "attempt": receipt["attempt"],
        "attempt_chain": [
            *attempt_chain,
            {
                "attempt": receipt["attempt"],
                "disposition": "measured_terminal",
                "classification": terminal_classification,
            },
        ],
        "preservation_strict": pstar,
        "strict_binary": strict,
        "num_steps": steps,
        "seconds": seconds,
        "backstop_utilization": backstop_utilization,
        "navigation": navigation,
        "deliberative_telemetry": deliberative,
        "process_endpoints": _process_endpoints(
            row, deliberative, chosen, navigation, gold_contract
        ),
        "evaluate_result_utilization": utilization,
        "limit_touches": touched,
        "limit_audit_status": limit_audit_status,
        "launch_receipt_sha256": _sha_file(
            campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
        ),
        "summary_sha256": _sha_file(paths["summary"]),
        "trajectory_sha256": _sha_file(paths["trajectory"]),
        "run_log_sha256": _sha_file(paths["run_log"]),
        "launcher_log_sha256": _sha_file(paths["launcher_log"]),
        "terminal_receipt_sha256": _sha_file(paths["terminal_receipt"]),
        "terminal_receipt_hash_sha256": _sha_file(
            paths["terminal_receipt_hash"]
        ),
        "probe_checkpoint": receipt["probe_checkpoint"],
    }


def _next_report_paths(campaign_dir: Path) -> tuple[Path, Path, Path]:
    root = campaign_dir / "reports"
    for number in range(1, 10000):
        stem = root / f"report_{number:04d}"
        paths = (
            stem.with_suffix(".json"), stem.with_suffix(".md"),
            stem.with_suffix(".sha256.json"),
        )
        if not any(path.exists() for path in paths):
            return paths
    raise SystemExit("no create-only report number remains")


def build_report(campaign_dir: Path) -> dict:
    campaign_dir = campaign_dir.resolve()
    manifest = verify_campaign(campaign_dir, quiet=True)
    results = []
    errors = []
    for row in manifest["schedule"]:
        try:
            results.append(_validated_result(campaign_dir, manifest, row))
        except (ValueError, SystemExit) as exc:
            errors.append(str(exc))
    if errors or len(results) != 112:
        raise SystemExit(
            f"REPORT FAIL CLOSED: {len(results)}/112 valid scheduled runs\n  "
            + "\n  ".join(errors[:40])
        )
    groups = defaultdict(list)
    for result in results:
        groups[(result["study"], result["arm_id"])].append(result)
    aggregates = []
    for (study, arm), items in sorted(groups.items()):
        aggregates.append({
            "study": study,
            "arm_id": arm,
            "n": len(items),
            "mean_preservation_strict": sum(
                item["preservation_strict"] for item in items
            ) / len(items),
            "literal_hero_rate": sum(item["hero"] for item in items) / len(items),
            "strict_binary_rate": sum(item["strict_binary"] for item in items) / len(items),
        })
    process_aggregates = []
    for (study, arm), items in sorted(groups.items()):
        endpoints = defaultdict(list)
        for item in items:
            process = {
                **item["process_endpoints"],
                "num_steps": item["num_steps"],
                "seconds": item["seconds"],
            }
            for name, value in process.items():
                if isinstance(value, (int, float, bool)) and not isinstance(value, str):
                    endpoints[name].append(float(value))
        process_aggregates.append({
            "study": study,
            "arm_id": arm,
            "n_runs": len(items),
            "endpoints": {
                name: {"n_observed": len(values), "mean": sum(values) / len(values)}
                for name, values in sorted(endpoints.items()) if values
            },
            "interpretation": "treatment uptake/process description; not causal mediation",
        })
    report = {
        "schema_version": 1,
        "kind": REPORT_KIND,
        "created_at_utc": _utcnow(),
        "campaign_id": manifest["campaign_id"],
        "manifest_sha256": _sha_file(campaign_dir / "campaign_manifest.json"),
        "scheduled_denominator": 112,
        "valid_runs": 112,
        "all_applicable_safety_and_lossy_limit_touches_zero": True,
        "all_applicable_evaluate_result_bounds_not_near": True,
        "pre_agent_compiler_capability_audit_exceptions": sum(
            result["limit_audit_status"]
            == "not_initialized_pre_agent_compiler_capability_failure"
            for result in results
        ),
        "metric_policy": manifest["metric_policy"],
        "host_capacity": manifest["host_capacity"],
        "host_browser_roots_at_report": _active_browser_roots(),
        "aggregates": aggregates,
        "process_aggregates": process_aggregates,
        "runs": results,
    }
    json_path, markdown_path, hash_path = _next_report_paths(campaign_dir)
    lines = [
        f"# Further mode ablation run report: {manifest['campaign_id']}", "",
        (
            "All 112 scheduled hard runs validated. Every initialized limit audit "
            "had zero safety/lossy-limit touches and was not near an evaluate-"
            "result bound. Exact pre-agent compiler capability failures without "
            "initialized audits are counted and disclosed, never imputed."
        ),
        "",
        "| Study | Arm | n | Mean P* | Literal hero rate | Strict-binary rate |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for item in aggregates:
        lines.append(
            f"| {item['study']} | {item['arm_id']} | {item['n']} | "
            f"{item['mean_preservation_strict']:.4f} | "
            f"{item['literal_hero_rate']:.4f} | "
            f"{item['strict_binary_rate']:.4f} |"
        )
    _write_new(json_path, report)
    _write_new_text(markdown_path, "\n".join(lines) + "\n")
    _write_new(hash_path, {
        "json": {"path": json_path.name, "sha256": _sha_file(json_path)},
        "markdown": {"path": markdown_path.name, "sha256": _sha_file(markdown_path)},
    })
    print(f"REPORT PASS: {json_path}")
    return report


def export_study_bundle(
    campaign_dir: Path, report_path: Path, output: Path,
) -> dict:
    """Export the validated hard rows without relabeling or synthesis."""

    campaign_dir = campaign_dir.resolve()
    manifest = verify_campaign(campaign_dir, quiet=True)
    report_path = report_path.resolve()
    if report_path.parent != campaign_dir / "reports":
        raise SystemExit("bundle report must belong to the frozen campaign")
    report = _read_json(report_path)
    markdown_path = report_path.with_suffix(".md")
    report_sidecar_path = report_path.with_suffix(".sha256.json")
    expected_sidecar = {
        "json": {"path": report_path.name, "sha256": _sha_file(report_path)},
        "markdown": {
            "path": markdown_path.name,
            "sha256": _sha_file(markdown_path),
        },
    }
    if (
        not markdown_path.is_file()
        or not report_sidecar_path.is_file()
        or _read_json(report_sidecar_path) != expected_sidecar
        or report.get("kind") != REPORT_KIND
        or report.get("campaign_id") != manifest["campaign_id"]
        or report.get("manifest_sha256")
        != _sha_file(campaign_dir / "campaign_manifest.json")
        or report.get("scheduled_denominator") != 112
        or report.get("valid_runs") != 112
        or not isinstance(report.get("runs"), list)
        or len(report["runs"]) != 112
    ):
        raise SystemExit("bundle input is not one exact validated hard report")
    expected_ids = [row["run_id"] for row in manifest["schedule"]]
    if [row.get("run_id") for row in report["runs"]] != expected_ids:
        raise SystemExit("bundle rows differ from frozen schedule order")
    frozen_root = campaign_dir / manifest["frozen_artifact_root"]
    rank_inputs = {}
    for name in ("pool.json", "preferences.json"):
        path = frozen_root / HARD_SCENARIO / name
        relative = f"{HARD_SCENARIO}/{name}"
        ref = {"sha256": _sha_file(path), "size": path.stat().st_size}
        if manifest["frozen_artifact_inventory"].get(relative) != ref:
            raise SystemExit(f"frozen rank input drifted: {relative}")
        rank_inputs[name.removesuffix(".json")] = {
            "path": str(path), **ref,
        }
    output = output.resolve()
    bundle = {
        "schema_version": 1,
        "kind": "further_mode_ablation_study_bundle",
        "study_id": "further_mode_ablation_hard_v2",
        "study": "hard",
        "source_campaign": {
            "path": str(campaign_dir),
            "campaign_id": manifest["campaign_id"],
            "manifest_sha256": _sha_file(
                campaign_dir / "campaign_manifest.json"
            ),
            "source_inventory_sha256": manifest[
                "campaign_source_inventory_sha256"
            ],
        },
        "source_report": {
            "path": str(report_path),
            "sha256": _sha_file(report_path),
            "sidecar_sha256": _sha_file(report_sidecar_path),
        },
        "recovery_amendment_sha256": manifest["recovery_amendment"][
            "sha256"
        ],
        "rank_endpoint_inputs": rank_inputs,
        "expected_run_ids": expected_ids,
        "rows": report["runs"],
    }
    _write_new(output, bundle)
    _write_new(
        output.with_suffix(output.suffix + ".sha256.json"),
        {"path": output.name, "sha256": _sha_file(output)},
    )
    validate_study_bundle(output)
    print(f"BUNDLE PASS: {output}")
    return bundle


def validate_study_bundle(bundle_path: Path) -> dict:
    """Independently revalidate a hard-v2 bridge bundle from its sources."""

    bundle_path = bundle_path.resolve()
    bundle = _read_json(bundle_path)
    sidecar_path = bundle_path.with_suffix(bundle_path.suffix + ".sha256.json")
    if (
        not sidecar_path.is_file()
        or _read_json(sidecar_path)
        != {"path": bundle_path.name, "sha256": _sha_file(bundle_path)}
        or bundle.get("schema_version") != 1
        or bundle.get("kind") != "further_mode_ablation_study_bundle"
        or bundle.get("study_id") != "further_mode_ablation_hard_v2"
        or bundle.get("study") != "hard"
    ):
        raise ValueError("hard-v2 study bundle identity/hash is invalid")
    source = bundle.get("source_campaign") or {}
    campaign_dir = Path(source.get("path", "")).resolve()
    manifest = verify_campaign(campaign_dir, quiet=True)
    expected_source = {
        "path": str(campaign_dir),
        "campaign_id": manifest["campaign_id"],
        "manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "source_inventory_sha256": manifest[
            "campaign_source_inventory_sha256"
        ],
    }
    report_ref = bundle.get("source_report") or {}
    report_path = Path(report_ref.get("path", "")).resolve()
    report_sidecar_path = report_path.with_suffix(".sha256.json")
    report = _read_json(report_path)
    if (
        source != expected_source
        or report_ref != {
            "path": str(report_path),
            "sha256": _sha_file(report_path),
            "sidecar_sha256": _sha_file(report_sidecar_path),
        }
        or report.get("kind") != REPORT_KIND
        or report.get("campaign_id") != manifest["campaign_id"]
        or report.get("scheduled_denominator") != 112
        or report.get("valid_runs") != 112
        or report.get("manifest_sha256") != expected_source["manifest_sha256"]
        or bundle.get("recovery_amendment_sha256")
        != manifest["recovery_amendment"]["sha256"]
    ):
        raise ValueError("hard-v2 bundle source campaign/report binding is invalid")
    report_md = report_path.with_suffix(".md")
    expected_report_sidecar = {
        "json": {"path": report_path.name, "sha256": _sha_file(report_path)},
        "markdown": {
            "path": report_md.name, "sha256": _sha_file(report_md),
        },
    }
    if _read_json(report_sidecar_path) != expected_report_sidecar:
        raise ValueError("hard-v2 source report bundle is invalid")
    expected_ids = [row["run_id"] for row in manifest["schedule"]]
    if (
        bundle.get("expected_run_ids") != expected_ids
        or bundle.get("rows") != report.get("runs")
        or [row.get("run_id") for row in bundle.get("rows", [])]
        != expected_ids
    ):
        raise ValueError("hard-v2 bundle rows differ from exact source report")
    frozen_root = campaign_dir / manifest["frozen_artifact_root"]
    expected_rank_inputs = {}
    for name in ("pool.json", "preferences.json"):
        path = frozen_root / HARD_SCENARIO / name
        ref = {"sha256": _sha_file(path), "size": path.stat().st_size}
        expected_rank_inputs[name.removesuffix(".json")] = {
            "path": str(path), **ref,
        }
        if manifest["frozen_artifact_inventory"].get(
            f"{HARD_SCENARIO}/{name}"
        ) != ref:
            raise ValueError("hard-v2 rank input differs from manifest")
    if bundle.get("rank_endpoint_inputs") != expected_rank_inputs:
        raise ValueError("hard-v2 rank input bundle refs are invalid")
    return bundle


def _parse_regions(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-static")
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    prepare.add_argument("--campaign-id", default=DEFAULT_CAMPAIGN.name)
    prepare.add_argument("--base-port", type=int, required=True)
    prepare.add_argument("--cert-report", type=Path, required=True)
    prepare.add_argument("--lockdiff-report", type=Path, required=True)
    prepare.add_argument("--host-capacity-receipt", type=Path, required=True)
    prepare.add_argument("--regions", type=_parse_regions, required=True)
    for name in ("verify", "status", "report"):
        command = sub.add_parser(name)
        command.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    probe = sub.add_parser("probe")
    probe.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    probe.add_argument("--stage", choices=tuple(PROBE_STAGES), required=True)
    probe.add_argument("--label", required=True)
    launch = sub.add_parser("launch-block")
    launch.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    launch.add_argument("--block", type=int, choices=range(1, 5), required=True)
    launch.add_argument("--checkpoint", required=True)
    launch.add_argument("--confirm", required=True)
    run = sub.add_parser("run-one", help=argparse.SUPPRESS)
    run.add_argument("--campaign-dir", type=Path, required=True)
    run.add_argument("--run-id", required=True)
    host_probe = sub.add_parser("probe-host-capacity")
    host_probe.add_argument("--output", type=Path, required=True)
    host_probe.add_argument("--base-port", type=int, required=True)
    classify = sub.add_parser("classify-attempt")
    classify.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    classify.add_argument("--run-id", required=True)
    archive = sub.add_parser("archive-infra-attempt")
    archive.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    archive.add_argument("--run-id", required=True)
    bundle = sub.add_parser("export-bundle")
    bundle.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    bundle.add_argument("--report", type=Path, required=True)
    bundle.add_argument("--output", type=Path, required=True)
    validate_bundle = sub.add_parser("validate-bundle")
    validate_bundle.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "verify-static":
        print(json.dumps(static_verify(), indent=2, sort_keys=True))
    elif args.command == "prepare":
        prepare_campaign(
            args.campaign_dir, args.campaign_id, args.base_port,
            args.cert_report, args.lockdiff_report,
            args.host_capacity_receipt, args.regions,
        )
    elif args.command == "verify":
        verify_campaign(args.campaign_dir)
    elif args.command == "status":
        status(args.campaign_dir)
    elif args.command == "report":
        build_report(args.campaign_dir)
    elif args.command == "probe":
        run_probe(args.campaign_dir, args.stage, args.label)
    elif args.command == "launch-block":
        launch_block(args.campaign_dir, args.block, args.checkpoint, args.confirm)
    elif args.command == "run-one":
        run_one(args.campaign_dir, args.run_id)
    elif args.command == "probe-host-capacity":
        probe_host_capacity(args.output, args.base_port)
    elif args.command == "classify-attempt":
        classify_attempt(args.campaign_dir, args.run_id)
    elif args.command == "archive-infra-attempt":
        archive_infrastructure_attempt(args.campaign_dir, args.run_id)
    elif args.command == "export-bundle":
        export_study_bundle(args.campaign_dir, args.report, args.output)
    elif args.command == "validate-bundle":
        validate_study_bundle(args.bundle)
        print(f"BUNDLE VERIFY PASS: {args.bundle.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
