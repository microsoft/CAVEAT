#!/usr/bin/env python3
"""Create-only repeat-2 continuation for the prompt-only ablation.

The parent campaign froze three sol-high routes.  After repeat 1, gcr could
answer isolated requests but failed the campaign's required four-request load
with HTTP 429.  This continuation leaves the parent campaign incomplete,
derives ten new repeat-2 run identities, and removes only gcr from sol-high's
route order.  Every scientific input is inherited from and bound to the frozen
parent manifest.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path


ABLATION_DIR = Path(__file__).resolve().parent
ROOT = ABLATION_DIR.parents[1]
sys.path.insert(0, str(ABLATION_DIR))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import campaign as parent  # noqa: E402
from harness_eval_campaign import validate_probe_evidence  # noqa: E402


SCHEMA_VERSION = 1
KIND = "browseruse_prompt_only_route_amended_continuation"
REPORT_KIND = "browseruse_prompt_only_route_amended_report"
DEFAULT_PARENT = ROOT / "results/browseruse_prompt_only_ablation_v1"
DEFAULT_CONTINUATION = (
    ROOT / "results/browseruse_prompt_only_ablation_v1_route_amended_r2"
)
CONTINUATION_ID = "browseruse_prompt_only_ablation_v1_route_amended_r2"
FAILED_ROUTE = "gcr/shared"
SOL_ROUTES = ("msraif/shared", "redmond/interactive")
SOL_STAGE = "sol_route_amended_mid"
FRESH_SECONDS = 3600
FAILED_PROBE_LABELS = (
    "sol_mid_after_wave1_20260729T1010Z",
    "sol_mid_retry_after_wave1_20260729T1033Z",
)
SOL_EVIDENCE_LABEL = "sol_mid_retry_after_wave1_20260729T1033Z"
WEAK_CHECKPOINT_LABEL = "weak_mid_refresh_after_wave1_20260729T1044Z"


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _manifest_path(continuation_dir: Path) -> Path:
    return continuation_dir.resolve() / "continuation_manifest.json"


def _sidecar_path(continuation_dir: Path) -> Path:
    return continuation_dir.resolve() / "continuation_manifest.sha256.json"


def _file_ref(path: Path) -> dict:
    return parent._file_ref(path.resolve())


def _assert_ref(record: dict) -> Path:
    path = Path(record["path"])
    if not path.is_file() or _file_ref(path) != record:
        raise SystemExit(f"bound file drifted: {path}")
    return path


def _parent_manifest_ref(parent_dir: Path) -> dict:
    path = parent_dir.resolve() / "campaign_manifest.json"
    return _file_ref(path)


def _parent_sidecar_ref(parent_dir: Path) -> dict:
    path = parent_dir.resolve() / "campaign_manifest.sha256.json"
    return _file_ref(path)


def _parent_wave2_absence(parent_dir: Path, manifest: dict) -> None:
    found = []
    for row in manifest["schedule"]:
        if row["wave"] != 2:
            continue
        paths = (
            parent_dir / row["experiment_relpath"],
            parent_dir / row["launcher_log_relpath"],
            parent_dir / "launch_receipts" / f"{row['run_id']}.json",
            parent_dir / "launch_receipts"
            / f"{row['run_id']}.sha256.json",
        )
        found.extend(str(path) for path in paths if path.exists())
    if found:
        raise SystemExit(
            "parent repeat-2 evidence exists; route-amended continuation "
            "refuses to overlap it:\n  " + "\n  ".join(found)
        )


def _wave1_refs(parent_dir: Path, manifest: dict) -> list[dict]:
    refs = []
    for row in manifest["schedule"]:
        if row["wave"] != 1:
            continue
        summary = parent_dir / row["summary_relpath"]
        trajectory = parent_dir / row["trajectory_relpath"]
        run_log = parent_dir / row["run_log_relpath"]
        if not all(path.is_file() for path in (summary, trajectory, run_log)):
            raise SystemExit(f"parent wave 1 is incomplete: {row['run_id']}")
        refs.append({
            "run_id": row["run_id"],
            "summary": _file_ref(summary),
            "trajectory": _file_ref(trajectory),
            "run_log": _file_ref(run_log),
        })
    if len(refs) != 10:
        raise SystemExit("parent wave 1 is not the exact ten-run wave")
    return refs


def _result_name(row: dict) -> str:
    return (
        f"amazon__{row['scaffold']}__{row['model_recorded']}__"
        f"{row['scenario']}-{row['variant']}__{row['condition']}"
    )


def _derived_schedule(parent_manifest: dict) -> list[dict]:
    rows = []
    for source in parent_manifest["schedule"]:
        if source["wave"] != 2:
            continue
        row = copy.deepcopy(source)
        parent_run_id = source["run_id"]
        row["parent_run_id"] = parent_run_id
        row["run_id"] = f"{parent_run_id}_route_amended"
        row["run_name"] = f"{CONTINUATION_ID}_{row['run_id']}"
        if row["logical_model"] == parent.SOL_LOGICAL:
            row["region_order"] = [
                region
                for region in source["region_order"]
                if region != FAILED_ROUTE
            ]
            row["primary_region"] = row["region_order"][0]
            row["route_amendment"] = {
                "operation": "delete_failed_route_only",
                "deleted_route": FAILED_ROUTE,
                "parent_region_order": source["region_order"],
            }
        else:
            row["route_amendment"] = None
        result_name = _result_name(row)
        row["experiment_relpath"] = f"runs/{row['run_name']}"
        row["browser_run_relpath"] = (
            f"runs/{row['run_name']}/{result_name}"
        )
        row["summary_relpath"] = (
            f"runs/{row['run_name']}/{result_name}/summary.json"
        )
        row["trajectory_relpath"] = (
            f"runs/{row['run_name']}/{result_name}/trajectory.json"
        )
        row["run_log_relpath"] = (
            f"runs/{row['run_name']}/{result_name}/run.log"
        )
        row["launcher_log_relpath"] = (
            f"launcher_logs/{row['run_id']}.log"
        )
        rows.append(row)
    rows.sort(key=lambda item: item["wave_index"])
    _validate_derived_schedule(parent_manifest, rows)
    return rows


def _validate_derived_schedule(
    parent_manifest: dict, rows: list[dict]
) -> None:
    parent_rows = {
        row["run_id"]: row
        for row in parent_manifest["schedule"]
        if row["wave"] == 2
    }
    errors = []
    if len(rows) != 10 or len({row["run_id"] for row in rows}) != 10:
        errors.append("continuation is not ten uniquely identified runs")
    if {row["parent_run_id"] for row in rows} != set(parent_rows):
        errors.append("continuation does not map exactly onto parent wave 2")
    for row in rows:
        source = parent_rows[row["parent_run_id"]]
        expected = copy.deepcopy(source)
        expected["parent_run_id"] = source["run_id"]
        expected["run_id"] = f"{source['run_id']}_route_amended"
        expected["run_name"] = f"{CONTINUATION_ID}_{expected['run_id']}"
        if source["logical_model"] == parent.SOL_LOGICAL:
            expected["region_order"] = [
                region
                for region in source["region_order"]
                if region != FAILED_ROUTE
            ]
            expected["primary_region"] = expected["region_order"][0]
            expected["route_amendment"] = {
                "operation": "delete_failed_route_only",
                "deleted_route": FAILED_ROUTE,
                "parent_region_order": source["region_order"],
            }
            if set(expected["region_order"]) != set(SOL_ROUTES):
                errors.append(
                    f"{source['run_id']}: non-gcr route set drifted"
                )
        else:
            expected["route_amendment"] = None
            if (
                expected["region_order"] != source["region_order"]
                or expected["primary_region"] != source["primary_region"]
            ):
                errors.append(f"{source['run_id']}: weak route changed")
        result_name = _result_name(expected)
        expected["experiment_relpath"] = f"runs/{expected['run_name']}"
        expected["browser_run_relpath"] = (
            f"runs/{expected['run_name']}/{result_name}"
        )
        expected["summary_relpath"] = (
            f"runs/{expected['run_name']}/{result_name}/summary.json"
        )
        expected["trajectory_relpath"] = (
            f"runs/{expected['run_name']}/{result_name}/trajectory.json"
        )
        expected["run_log_relpath"] = (
            f"runs/{expected['run_name']}/{result_name}/run.log"
        )
        expected["launcher_log_relpath"] = (
            f"launcher_logs/{expected['run_id']}.log"
        )
        if row != expected:
            errors.append(f"{source['run_id']}: derivation is not mechanical")
    if errors:
        raise SystemExit(
            "invalid route-amended schedule:\n  " + "\n  ".join(errors)
        )


def _probe_log_refs(parent_dir: Path) -> dict:
    records = {}
    for label in FAILED_PROBE_LABELS:
        records[label] = {}
        for kind in ("small", "large", "concurrency"):
            path = parent_dir / "probes" / f"{kind}_{label}.log"
            if not path.is_file():
                raise SystemExit(f"missing failed-probe evidence: {path}")
            records[label][kind] = _file_ref(path)
    return records


def _sol_evidence(
    parent_dir: Path, parent_manifest: dict, rows: list[dict]
) -> tuple[dict, dict]:
    paths = {
        kind: (
            parent_dir
            / "probes"
            / f"{kind}_{SOL_EVIDENCE_LABEL}.log"
        )
        for kind in ("small", "large", "concurrency")
    }
    oldest = min(
        dt.datetime.fromtimestamp(
            path.stat().st_mtime, tz=dt.timezone.utc
        )
        for path in paths.values()
    )
    age = (dt.datetime.now(dt.timezone.utc) - oldest).total_seconds()
    if age < 0 or age > FRESH_SECONDS:
        raise SystemExit(
            f"two-route sol evidence is stale ({age:.1f}s)"
        )
    probe_manifest = copy.deepcopy(parent_manifest)
    probe_manifest["schedule"] = rows
    probe_manifest["probe_policy"]["stages"] = {SOL_STAGE: [4]}
    probe_manifest["probe_policy"]["scheduled_regions"][
        parent.SOL_LOGICAL
    ] = list(SOL_ROUTES)
    evidence = validate_probe_evidence(
        probe_manifest,
        SOL_STAGE,
        paths["small"],
        paths["concurrency"],
        paths["large"],
    )
    if (
        evidence["probe_candidate_regions"] != list(SOL_ROUTES)
        or evidence["small_routes"] != list(SOL_ROUTES)
        or any(
            evidence["concurrency_capacity"].get(region, 0) < 5
            for region in SOL_ROUTES
        )
        or any(
            region not in evidence["large_healthy_regions"]
            for region in SOL_ROUTES
        )
    ):
        raise SystemExit("two-route sol evidence contract failed")
    return evidence, {
        "label": SOL_EVIDENCE_LABEL,
        "oldest_log_mtime_utc": oldest.isoformat().replace("+00:00", "Z"),
        "fresh_until_utc": (
            oldest + dt.timedelta(seconds=FRESH_SECONDS)
        ).isoformat().replace("+00:00", "Z"),
        "logs": {kind: _file_ref(path) for kind, path in paths.items()},
    }


def _weak_evidence(
    parent_dir: Path, parent_manifest: dict
) -> dict:
    verified = parent._verify_checkpoint(
        parent_dir,
        parent_manifest,
        "weak_mid",
        WEAK_CHECKPOINT_LABEL,
    )
    path = Path(verified["path"])
    record = parent._read_json(path)
    published = _parse_utc(record["published_at_utc"])
    return {
        "label": WEAK_CHECKPOINT_LABEL,
        "checkpoint": _file_ref(path),
        "published_at_utc": record["published_at_utc"],
        "fresh_until_utc": (
            published + dt.timedelta(seconds=FRESH_SECONDS)
        ).isoformat().replace("+00:00", "Z"),
    }


def prepare(parent_dir: Path, continuation_dir: Path) -> None:
    parent_dir = parent_dir.resolve()
    continuation_dir = continuation_dir.resolve()
    manifest_path = _manifest_path(continuation_dir)
    if manifest_path.exists():
        verify(parent_dir, continuation_dir)
        print("continuation already frozen; nothing replaced")
        return
    parent_manifest = parent.verify(parent_dir, quiet=True)
    _parent_wave2_absence(parent_dir, parent_manifest)
    rows = _derived_schedule(parent_manifest)
    weak = _weak_evidence(parent_dir, parent_manifest)
    sol, sol_source = _sol_evidence(parent_dir, parent_manifest, rows)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "campaign_id": CONTINUATION_ID,
        "frozen_at_utc": _utcnow(),
        "designation": "descriptive_infrastructure_amended_ablation",
        "parent_status": {
            "exact_preregistration_complete": 10,
            "exact_preregistration_scheduled": 20,
            "exact_parent_remains_incomplete": True,
        },
        "parent_manifest": _parent_manifest_ref(parent_dir),
        "parent_manifest_sidecar": _parent_sidecar_ref(parent_dir),
        "continuation_source": _file_ref(Path(__file__)),
        "prompt_contract": parent_manifest["prompt_contract"],
        "prompt_sources": parent_manifest["prompt_sources"],
        "baseline_source": parent_manifest["baseline_source"],
        "measured_source_inventory_sha256": parent_manifest[
            "measured_source_inventory_sha256"
        ],
        "runtime_dependencies_sha256": parent_manifest[
            "runtime_dependencies_sha256"
        ],
        "runtime_environment_policy": parent_manifest[
            "runtime_environment_policy"
        ],
        "caps": parent_manifest["caps"],
        "limit_contract": parent_manifest["limit_contract"],
        "certification_sha256": parent_manifest[
            "certification_sha256"
        ],
        "v18_manifest": parent_manifest["v18_manifest"],
        "v18_baseline_comparators": parent_manifest[
            "v18_baseline_comparators"
        ],
        "artifacts": parent_manifest["artifacts"],
        "base_port": parent_manifest["base_port"],
        "schedule": rows,
        "wave1_evidence": _wave1_refs(parent_dir, parent_manifest),
        "failed_route": {
            "route": FAILED_ROUTE,
            "reason": (
                "two formal mid probes found gcr unavailable under load; "
                "the retry measured 0/4 HTTP 429 while both retained routes "
                "reached 32/32 and passed realistic large requests"
            ),
            "all_failed_probe_logs": _probe_log_refs(parent_dir),
        },
        "weak_checkpoint": weak,
        "sol_route_checkpoint": {
            "evidence": sol,
            "source": sol_source,
        },
        "amendment_contract": {
            "weak_routes_unchanged": True,
            "sol_operation": "delete_only_gcr_from_each_parent_order",
            "all_other_run_fields_inherited": True,
            "new_run_ids_and_sibling_result_tree": True,
            "no_parent_wave2_artifacts": True,
            "no_relaunch_or_replacement": True,
        },
        "metric_policy": parent_manifest["metric_policy"],
    }
    parent._write_new(manifest_path, manifest)
    parent._write_new(
        _sidecar_path(continuation_dir),
        {"path": manifest_path.name, "sha256": parent._sha_file(manifest_path)},
    )
    verify(parent_dir, continuation_dir)
    print(
        "PREPARE PASS: route-amended repeat 2 frozen; "
        "weak routes unchanged; sol routes msraif/redmond"
    )


def _verify_bound_evidence(manifest: dict) -> None:
    for record in manifest["wave1_evidence"]:
        for key in ("summary", "trajectory", "run_log"):
            _assert_ref(record[key])
    for attempt in manifest["failed_route"]["all_failed_probe_logs"].values():
        for ref in attempt.values():
            _assert_ref(ref)
    _assert_ref(manifest["weak_checkpoint"]["checkpoint"])
    sol_source = manifest["sol_route_checkpoint"]["source"]
    log_paths = {
        kind: _assert_ref(ref)
        for kind, ref in sol_source["logs"].items()
    }
    parent_manifest = parent._read_json(
        Path(manifest["parent_manifest"]["path"])
    )
    probe_manifest = copy.deepcopy(parent_manifest)
    probe_manifest["schedule"] = manifest["schedule"]
    probe_manifest["probe_policy"]["stages"] = {SOL_STAGE: [4]}
    probe_manifest["probe_policy"]["scheduled_regions"][
        parent.SOL_LOGICAL
    ] = list(SOL_ROUTES)
    evidence = validate_probe_evidence(
        probe_manifest,
        SOL_STAGE,
        log_paths["small"],
        log_paths["concurrency"],
        log_paths["large"],
    )
    if evidence != manifest["sol_route_checkpoint"]["evidence"]:
        raise SystemExit("bound sol route evidence no longer validates")


def verify(
    parent_dir: Path,
    continuation_dir: Path,
    *,
    quiet: bool = False,
) -> dict:
    parent_dir = parent_dir.resolve()
    continuation_dir = continuation_dir.resolve()
    path = _manifest_path(continuation_dir)
    sidecar = _sidecar_path(continuation_dir)
    if not path.is_file() or not sidecar.is_file():
        raise SystemExit("continuation is not prepared")
    if parent._read_json(sidecar) != {
        "path": path.name,
        "sha256": parent._sha_file(path),
    }:
        raise SystemExit("continuation manifest hash mismatch")
    manifest = parent._read_json(path)
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("kind") != KIND
        or manifest.get("campaign_id") != CONTINUATION_ID
    ):
        raise SystemExit("continuation kind/schema/id drifted")
    parent_manifest = parent.verify(parent_dir, quiet=True)
    if (
        manifest.get("parent_manifest") != _parent_manifest_ref(parent_dir)
        or manifest.get("parent_manifest_sidecar")
        != _parent_sidecar_ref(parent_dir)
        or manifest.get("continuation_source") != _file_ref(Path(__file__))
    ):
        raise SystemExit("continuation source/parent binding drifted")
    for key in (
        "prompt_contract",
        "prompt_sources",
        "baseline_source",
        "measured_source_inventory_sha256",
        "runtime_dependencies_sha256",
        "runtime_environment_policy",
        "caps",
        "limit_contract",
        "certification_sha256",
        "v18_manifest",
        "v18_baseline_comparators",
        "artifacts",
        "base_port",
        "metric_policy",
    ):
        if manifest.get(key) != parent_manifest.get(key):
            raise SystemExit(f"inherited contract drifted: {key}")
    if manifest.get("schedule") != _derived_schedule(parent_manifest):
        raise SystemExit("derived continuation schedule drifted")
    _parent_wave2_absence(parent_dir, parent_manifest)
    if manifest.get("wave1_evidence") != _wave1_refs(
        parent_dir, parent_manifest
    ):
        raise SystemExit("parent wave-1 evidence drifted")
    _verify_bound_evidence(manifest)
    if not quiet:
        print(
            "VERIFY PASS: parent untouched, prompt/baseline/runtime bound, "
            "mechanical schedule, failed-route evidence, and inputs unchanged"
        )
    return manifest


def _fresh_for_launch(manifest: dict) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    expiries = {
        "weak": manifest["weak_checkpoint"]["fresh_until_utc"],
        "sol": manifest["sol_route_checkpoint"]["source"][
            "fresh_until_utc"
        ],
    }
    stale = [
        f"{name} ({value})"
        for name, value in expiries.items()
        if now > _parse_utc(value)
    ]
    if stale:
        raise SystemExit(
            "route checkpoint stale before launch: " + ", ".join(stale)
        )


def _command(
    continuation_dir: Path, manifest: dict, row: dict
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "agentarena.benchmark.run",
        "--name",
        row["run_name"],
        "--scenarios",
        row["scenario"],
        "--conditions",
        row["condition"],
        "--variants",
        row["variant"],
        "--scaffolds",
        row["scaffold"],
        "--models",
        row["model_request"],
        "--max-steps",
        str(manifest["caps"]["max_steps"]),
        "--repeats",
        "1",
        "--jobs",
        "1",
        "--results",
        str(continuation_dir / "runs"),
        "--base-port",
        str(row["port"]),
    ]


def _continuation_artifacts(
    continuation_dir: Path, row: dict
) -> list[Path]:
    return [
        continuation_dir / row["experiment_relpath"],
        continuation_dir / row["launcher_log_relpath"],
        continuation_dir / "launch_receipts" / f"{row['run_id']}.json",
        continuation_dir
        / "launch_receipts"
        / f"{row['run_id']}.sha256.json",
    ]


def _write_receipt(
    continuation_dir: Path,
    manifest: dict,
    row: dict,
    env: dict,
    command: list[str],
) -> None:
    expected_override = json.dumps(
        {row["logical_model"]: row["region_order"]},
        separators=(",", ":"),
    )
    if env.get("TRAPI_REGIONS_OVERRIDE") != expected_override:
        raise SystemExit("route override differs from continuation schedule")
    path = (
        continuation_dir
        / "launch_receipts"
        / f"{row['run_id']}.json"
    )
    record = {
        "run_id": row["run_id"],
        "parent_run_id": row["parent_run_id"],
        "launched_at_utc": _utcnow(),
        "continuation_manifest_sha256": parent._sha_file(
            _manifest_path(continuation_dir)
        ),
        "parent_manifest_sha256": manifest["parent_manifest"]["sha256"],
        "prompt_sha256": manifest["prompt_contract"]["computed_sha256"],
        "baseline_source_sha256": manifest["baseline_source"]["sha256"],
        "weak_checkpoint_sha256": manifest["weak_checkpoint"][
            "checkpoint"
        ]["sha256"],
        "sol_checkpoint_log_sha256": {
            kind: ref["sha256"]
            for kind, ref in manifest["sol_route_checkpoint"]["source"][
                "logs"
            ].items()
        },
        "row": row,
        "command": command,
        "trapi_regions_override": expected_override,
        "pythonpath_prefix": str(ABLATION_DIR),
        "caps": manifest["caps"],
    }
    parent._write_new(path, record)
    parent._write_new(
        path.with_suffix(".sha256.json"),
        {"path": path.name, "sha256": parent._sha_file(path)},
    )


def launch(parent_dir: Path, continuation_dir: Path) -> None:
    parent_dir = parent_dir.resolve()
    continuation_dir = continuation_dir.resolve()
    manifest = verify(parent_dir, continuation_dir, quiet=True)
    _fresh_for_launch(manifest)
    parent._validate_port_band(
        int(manifest["base_port"]), require_free=True
    )
    existing = [
        str(path)
        for row in manifest["schedule"]
        for path in _continuation_artifacts(continuation_dir, row)
        if path.exists()
    ]
    if existing:
        raise SystemExit(
            "continuation launch is create-only; evidence already exists:\n  "
            + "\n  ".join(existing)
        )
    launch_inputs = []
    for row in manifest["schedule"]:
        env = parent._apply_environment(
            parent._read_json(Path(manifest["parent_manifest"]["path"])),
            row,
        )
        command = _command(continuation_dir, manifest, row)
        _write_receipt(
            continuation_dir, manifest, row, env, command
        )
        receipt = (
            continuation_dir
            / "launch_receipts"
            / f"{row['run_id']}.json"
        )
        sidecar = receipt.with_suffix(".sha256.json")
        if parent._read_json(sidecar) != {
            "path": receipt.name,
            "sha256": parent._sha_file(receipt),
        }:
            raise SystemExit(f"launch receipt failed to freeze: {receipt}")
        launch_inputs.append((row, env, command))

    interrupted = False

    def defer_interrupt(signum, _frame):
        nonlocal interrupted
        interrupted = True
        print(
            f"signal {signum}: no new spawns; waiting for active runs",
            file=sys.stderr,
            flush=True,
        )

    old_int = signal.signal(signal.SIGINT, defer_interrupt)
    old_term = signal.signal(signal.SIGTERM, defer_interrupt)
    processes = []
    try:
        for index, (row, env, command) in enumerate(launch_inputs):
            if interrupted:
                break
            log = continuation_dir / row["launcher_log_relpath"]
            log.parent.mkdir(parents=True, exist_ok=True)
            stream = log.open("x")
            print(
                f"launch {row['run_id']} "
                f"primary={row['primary_region']} "
                f"routes={row['region_order']} port={row['port']}",
                flush=True,
            )
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            processes.append((row, process, stream))
            if index + 1 < len(launch_inputs):
                time.sleep(10)
        failures = []
        for row, process, stream in processes:
            code = process.wait()
            stream.close()
            if code:
                failures.append((row["run_id"], code))
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
        for _row, process, stream in processes:
            if process.poll() is None:
                process.wait()
            if not stream.closed:
                stream.close()
    launched = {row["run_id"] for row, _process, _stream in processes}
    missing = [
        row["run_id"]
        for row, _process, _stream in processes
        if not (continuation_dir / row["summary_relpath"]).is_file()
    ]
    unlaunched = [
        row["run_id"]
        for row in manifest["schedule"]
        if row["run_id"] not in launched
    ]
    if interrupted or failures or missing or unlaunched:
        raise SystemExit(
            "continuation ended with preserved evidence; no run was killed "
            f"or replaced: interrupted={interrupted}, failures={failures}, "
            f"missing={missing}, unlaunched={unlaunched}"
        )
    print("LAUNCH PASS: ten fresh route-amended repeat-2 runs complete")


def status(parent_dir: Path, continuation_dir: Path) -> None:
    parent_dir = parent_dir.resolve()
    continuation_dir = continuation_dir.resolve()
    manifest = verify(parent_dir, continuation_dir, quiet=True)
    parent_manifest = parent._read_json(
        Path(manifest["parent_manifest"]["path"])
    )
    parent_complete = sum(
        (parent_dir / row["summary_relpath"]).is_file()
        for row in parent_manifest["schedule"]
    )
    rows = []
    for row in manifest["schedule"]:
        path = continuation_dir / row["summary_relpath"]
        summary = parent._read_json(path) if path.is_file() else {}
        rows.append({
            "run_id": row["run_id"],
            "parent_run_id": row["parent_run_id"],
            "cohort": row["cohort"],
            "scenario": row["scenario"],
            "complete": path.is_file(),
            "preservation_strict": summary.get("preservation_strict"),
            "strict_binary": summary.get("strict_binary"),
        })
    print(json.dumps({
        "exact_parent": {
            "complete": parent_complete,
            "scheduled": len(parent_manifest["schedule"]),
        },
        "route_amended_continuation": {
            "complete": sum(row["complete"] for row in rows),
            "scheduled": len(rows),
        },
        "rows": rows,
    }, indent=2))


def _analysis_value(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _cap_audit(trajectory: dict) -> dict:
    stats = trajectory.get("stats") or {}
    categories = (
        (stats.get("limit_audit") or {}).get("categories") or {}
    )
    safety = categories.get("safety_backstops") or {}
    lossy = categories.get("lossy_context_limits") or {}
    safety_touched = {
        key: record.get("touched_count")
        for key, record in safety.items()
        if record.get("touched_count")
    }
    lossy_touched = {
        key: {
            "touched_count": record.get("touched_count"),
            "configured": record.get("configured"),
            "max_observed": (
                (record.get("observations") or {}).get("max_observed")
            ),
        }
        for key, record in lossy.items()
        if record.get("touched_count")
    }
    return {
        "safety_backstop_touches": safety_touched,
        "lossy_context_cap_touches": lossy_touched,
        "context_cap_audit": stats.get("context_cap_audit"),
    }


def _record(
    summary_path: Path,
    trajectory_path: Path,
    identity: dict,
) -> dict:
    if not summary_path.is_file() or not trajectory_path.is_file():
        raise SystemExit(
            f"fixed denominator artifact missing: {summary_path.parent}"
        )
    summary = parent._read_json(summary_path)
    trajectory = parent._read_json(trajectory_path)
    if "preservation_strict" not in summary or "strict_binary" not in summary:
        raise SystemExit(f"fresh strict scores missing: {summary_path}")
    caps = _cap_audit(trajectory)
    return {
        **identity,
        "chosen": summary.get("chosen"),
        "outcome": summary.get("outcome"),
        "preservation_strict": summary.get("preservation_strict"),
        "strict_binary": summary.get("strict_binary"),
        "analysis_preservation_strict": _analysis_value(
            summary.get("preservation_strict")
        ),
        "analysis_strict_binary": _analysis_value(
            summary.get("strict_binary")
        ),
        "steps": summary.get("num_steps"),
        "duration_seconds": summary.get("seconds"),
        **caps,
    }


def _aggregate(treatment: list[dict], baseline: list[dict]) -> dict:
    if len(treatment) != len(baseline) or not treatment:
        raise SystemExit("aggregate fixed denominator is incomplete")

    def mean(rows: list[dict], key: str) -> float:
        return sum(row[key] for row in rows) / len(rows)

    treatment_p = mean(treatment, "analysis_preservation_strict")
    baseline_p = mean(baseline, "analysis_preservation_strict")
    treatment_b = mean(treatment, "analysis_strict_binary")
    baseline_b = mean(baseline, "analysis_strict_binary")
    return {
        "n_treatment": len(treatment),
        "n_v18_baseline": len(baseline),
        "treatment_mean_preservation_strict": treatment_p,
        "v18_baseline_mean_preservation_strict": baseline_p,
        "delta_preservation_strict": treatment_p - baseline_p,
        "treatment_mean_strict_binary": treatment_b,
        "v18_baseline_mean_strict_binary": baseline_b,
        "delta_strict_binary": treatment_b - baseline_b,
    }


def report(parent_dir: Path, continuation_dir: Path) -> None:
    parent_dir = parent_dir.resolve()
    continuation_dir = continuation_dir.resolve()
    manifest = verify(parent_dir, continuation_dir, quiet=True)
    parent_manifest = parent._read_json(
        Path(manifest["parent_manifest"]["path"])
    )
    env = parent._apply_environment(parent_manifest)
    for row in manifest["schedule"]:
        experiment = continuation_dir / row["experiment_relpath"]
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "agentarena.scoring.rescore",
                "--glob",
                str(experiment),
                "--strict",
            ],
            cwd=ROOT,
            env=env,
        )
        if completed.returncode:
            raise SystemExit(f"strict rescore failed: {row['run_id']}")

    treatment = []
    for row in parent_manifest["schedule"]:
        if row["wave"] != 1:
            continue
        treatment.append(_record(
            parent_dir / row["summary_relpath"],
            parent_dir / row["trajectory_relpath"],
            {
                "source": "parent_wave1",
                "logical_run_id": row["run_id"],
                "cohort": row["cohort"],
                "scenario": row["scenario"],
                "repeat": row["repeat"],
                "primary_region": row["primary_region"],
                "region_order": row["region_order"],
            },
        ))
    for row in manifest["schedule"]:
        treatment.append(_record(
            continuation_dir / row["summary_relpath"],
            continuation_dir / row["trajectory_relpath"],
            {
                "source": "route_amended_repeat2",
                "logical_run_id": row["parent_run_id"],
                "continuation_run_id": row["run_id"],
                "cohort": row["cohort"],
                "scenario": row["scenario"],
                "repeat": row["repeat"],
                "primary_region": row["primary_region"],
                "region_order": row["region_order"],
            },
        ))

    baseline = []
    for comparator in parent_manifest["v18_baseline_comparators"]:
        summary_path = Path(comparator["summary_path"])
        baseline.append(_record(
            summary_path,
            summary_path.parent / "trajectory.json",
            {
                "source": "v18_baseline",
                "logical_run_id": comparator["run_id"],
                "cohort": comparator["cohort"],
                "scenario": comparator["scenario"],
                "repeat": comparator["repeat"],
            },
        ))
    expected_keys = {
        (row["cohort"], row["scenario"], row["repeat"])
        for row in treatment
    }
    baseline_keys = {
        (row["cohort"], row["scenario"], row["repeat"])
        for row in baseline
    }
    if len(treatment) != 20 or len(baseline) != 20:
        raise SystemExit("report does not contain exact 20+20 denominators")
    if expected_keys != baseline_keys or len(expected_keys) != 20:
        raise SystemExit("treatment/baseline matrices do not match")

    aggregates = {}
    for cohort in ("weak_easy", "strong_hard"):
        cohort_t = [row for row in treatment if row["cohort"] == cohort]
        cohort_b = [row for row in baseline if row["cohort"] == cohort]
        by_repeat = {}
        for repeat in (1, 2):
            by_repeat[str(repeat)] = _aggregate(
                [row for row in cohort_t if row["repeat"] == repeat],
                [row for row in cohort_b if row["repeat"] == repeat],
            )
        aggregates[cohort] = {
            "overall": _aggregate(cohort_t, cohort_b),
            "by_repeat": by_repeat,
        }

    contrasts = []
    treatment_index = {
        (row["cohort"], row["scenario"], row["repeat"]): row
        for row in treatment
    }
    baseline_index = {
        (row["cohort"], row["scenario"], row["repeat"]): row
        for row in baseline
    }
    for key in sorted(treatment_index):
        treatment_row = treatment_index[key]
        baseline_row = baseline_index[key]
        contrasts.append({
            "cohort": key[0],
            "scenario": key[1],
            "repeat": key[2],
            "treatment_preservation_strict": treatment_row[
                "analysis_preservation_strict"
            ],
            "baseline_preservation_strict": baseline_row[
                "analysis_preservation_strict"
            ],
            "delta_preservation_strict": (
                treatment_row["analysis_preservation_strict"]
                - baseline_row["analysis_preservation_strict"]
            ),
            "treatment_strict_binary": treatment_row[
                "analysis_strict_binary"
            ],
            "baseline_strict_binary": baseline_row[
                "analysis_strict_binary"
            ],
        })

    all_rows = treatment + baseline
    safety_exposed = [
        row["logical_run_id"]
        for row in all_rows
        if row["safety_backstop_touches"]
    ]
    lossy_exposed = [
        {
            "source": row["source"],
            "logical_run_id": row["logical_run_id"],
            "touches": row["lossy_context_cap_touches"],
        }
        for row in all_rows
        if row["lossy_context_cap_touches"]
    ]
    value = {
        "schema_version": 1,
        "kind": REPORT_KIND,
        "designation": "descriptive_infrastructure_amended_ablation",
        "reported_at_utc": _utcnow(),
        "continuation_manifest_sha256": parent._sha_file(
            _manifest_path(continuation_dir)
        ),
        "parent_manifest_sha256": manifest["parent_manifest"]["sha256"],
        "prompt_sha256": manifest["prompt_contract"]["computed_sha256"],
        "baseline_source_sha256": manifest["baseline_source"]["sha256"],
        "headline": "preservation_strict",
        "secondary": "strict_binary",
        "exact_parent_preregistration": {
            "complete": 10,
            "scheduled": 20,
            "status": "incomplete_due_to_gcr_route_degradation",
        },
        "route_amended_continuation": {
            "complete": 10,
            "scheduled": 10,
            "status": "complete",
        },
        "analysis_policy": {
            "completed_null_or_none_scores_as_zero": True,
            "missing_artifact_is_malformed_not_silently_zero": True,
            "no_replacements": True,
            "descriptive_not_confirmatory": True,
        },
        "validity": {
            "matrix_complete": True,
            "safety_backstops_clear": not safety_exposed,
            "lossy_context_caps_clear": not lossy_exposed,
            "route_protocol_as_originally_preregistered": False,
            "clean_original_routing_protocol_claim_available": False,
            "descriptive_infrastructure_amended_aggregate_available": True,
            "safety_exposed_runs": safety_exposed,
            "lossy_context_exposed_runs": lossy_exposed,
        },
        "aggregates": aggregates,
        "scenario_repeat_contrasts": contrasts,
        "treatment_runs": treatment,
        "v18_baseline_runs": baseline,
    }
    reports = continuation_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    for number in range(1, 1000):
        path = reports / f"report_{number:03d}.json"
        if path.exists():
            continue
        parent._write_new(path, value)
        parent._write_new(
            path.with_suffix(".sha256.json"),
            {"path": path.name, "sha256": parent._sha_file(path)},
        )
        print(json.dumps(aggregates, indent=2, sort_keys=True))
        print(f"REPORT PASS: {path}")
        return
    raise SystemExit("no create-only report number remains")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent-dir", type=Path, default=DEFAULT_PARENT)
    parser.add_argument(
        "--continuation-dir", type=Path, default=DEFAULT_CONTINUATION
    )
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("prepare")
    subs.add_parser("verify")
    subs.add_parser("launch")
    subs.add_parser("status")
    subs.add_parser("report")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.parent_dir, args.continuation_dir)
    elif args.command == "verify":
        verify(args.parent_dir, args.continuation_dir)
    elif args.command == "launch":
        launch(args.parent_dir, args.continuation_dir)
    elif args.command == "status":
        status(args.parent_dir, args.continuation_dir)
    elif args.command == "report":
        report(args.parent_dir, args.continuation_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
