#!/usr/bin/env python
"""Additive GPT-4o compatibility supplement for the frozen harness.

The parent Qwen/Kimi campaign remains untouched. This separately frozen
treatment-only cohort reports GPT-4o beside, never in place of, every parent
Qwen run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRIPT_DIR = ROOT / "scripts"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(HERE))

import campaign as parent_tool  # noqa: E402
from hard_campaign_runtime import validate_limit_audit  # noqa: E402
from harness_eval_campaign import (  # noqa: E402
    _contract_diagnostic_error,
    audit_refill_coexistence,
    validate_probe_evidence,
)


SCHEMA_VERSION = 1
KIND = "browseruse_deliberative_gpt4o_easy_supplement_campaign"
REPORT_KIND = "browseruse_deliberative_gpt4o_easy_supplement_report"
PREREG_PATH = HERE / "supplement_gpt4o_preregistration.json"
PARENT_DEFAULT = (
    ROOT / "results/harness_deliberative_cross_model_easy_v1"
)
V18_DEFAULT = ROOT / "results/harness_deliberative_ab_confirmatory_v18"
SCAFFOLD = "browseruse-deliberative"
MODEL_REQUEST = "gpt-4o"
MODEL_RECORDED = "gpt-4o"
MODEL_LOGICAL = "gpt-4o"
REGIONS = ("gcr/shared", "msraif/shared")
VARIANT = "graded"
CONDITION = "combined"
SCENARIOS = ("laptop", "office_chair", "mattress", "backpack", "tent")
WAVE_SCENARIOS = {
    1: ("laptop", "mattress"),
    2: ("office_chair", "backpack", "tent"),
}
STAGE_BLOCKS = {
    "gpt4o_before": [1],
    "gpt4o_mid": [2],
}
STAGE_PREDECESSORS = {
    "gpt4o_before": [],
    "gpt4o_mid": [1],
}
PROBE_MAX_AGE_SECONDS = 3_600
PORT_COUNT = 3
PROTECTED_PORT_LOW = 13_200
PROTECTED_PORT_HIGH = 13_299
EXPECTED_QWEN_TRIGGER_IDS = (
    "w1_qwen_laptop",
    "w1_qwen_mattress",
    "w1_qwen_tent",
)
BOUND_LOG_MARKERS = parent_tool.BOUND_LOG_MARKERS


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is absent")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{label} lacks a UTC offset")
    return parsed.astimezone(dt.timezone.utc)


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


def _file_ref(path: Path) -> dict:
    path = path.resolve()
    return {
        "path": str(path),
        "sha256": _sha_file(path),
        "size": path.stat().st_size,
    }


def _read_json(path: Path) -> dict:
    with path.open() as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise SystemExit(f"expected JSON object: {path}")
    return value


def _write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise SystemExit(f"refusing to replace create-only file: {path}") from exc
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_new_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise SystemExit(f"refusing to replace create-only file: {path}") from exc
    with os.fdopen(fd, "w") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise SystemExit(f"unsafe identifier: {value!r}")
    return value


def _tree_inventory(root: Path) -> dict:
    records = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            records[str(path.relative_to(root))] = {
                "sha256": _sha_file(path),
                "size": path.stat().st_size,
            }
    return records


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _validate_port_band(base_port: int, *, require_free: bool) -> None:
    if type(base_port) is not int or not 1_024 <= base_port <= 65_535:
        raise SystemExit("base port is outside the allowed user-port range")
    high = base_port + PORT_COUNT - 1
    if high > 65_535:
        raise SystemExit("supplement port band exceeds 65535")
    if not (high < PROTECTED_PORT_LOW or base_port > PROTECTED_PORT_HIGH):
        raise SystemExit("supplement port band overlaps protected refill ports")
    if require_free:
        occupied = [
            port for port in range(base_port, high + 1)
            if not _port_is_free(port)
        ]
        if occupied:
            raise SystemExit(
                "supplement port band is not free: "
                + ", ".join(str(port) for port in occupied)
            )


def select_free_band() -> int:
    for base in range(18_000, 30_000, 10):
        try:
            _validate_port_band(base, require_free=True)
        except SystemExit:
            continue
        return base
    raise SystemExit("no free three-port supplement band was found")


def _preregistration() -> dict:
    prereg = _read_json(PREREG_PATH)
    relationship = prereg.get("relationship_to_parent", {})
    scope = prereg.get("scope", {})
    design = prereg.get("design", {})
    model = scope.get("model", {})
    if (
        prereg.get("schema_version") != 1
        or prereg.get("kind")
        != "browseruse_deliberative_gpt4o_easy_supplement_preregistration"
        or relationship.get("additive_only") is not True
        or relationship.get("replacement_or_exclusion") is not False
        or relationship.get("pooled_substitution") is not False
        or relationship.get("all_qwen_runs_and_zeros_preserved") is not True
        or relationship.get("outcome_informed_followup_disclosed") is not True
        or scope.get("environment") != "amazon"
        or scope.get("scaffold") != SCAFFOLD
        or scope.get("variant") != VARIANT
        or scope.get("condition") != CONDITION
        or scope.get("scenarios") != list(SCENARIOS)
        or model.get("request") != MODEL_REQUEST
        or model.get("recorded") != MODEL_RECORDED
        or model.get("logical") != MODEL_LOGICAL
        or model.get("regions") != list(REGIONS)
        or design.get("measured_runs") != 5
        or design.get("fixed_denominator") is not True
        or design.get("no_outcome_driven_iteration") is not True
        or design.get("no_outcome_driven_topup") is not True
        or design.get("waves") != [
            {"wave": wave, "scenarios": list(WAVE_SCENARIOS[wave])}
            for wave in (1, 2)
        ]
        or prereg.get("metrics", {}).get("headline")
        != "preservation_strict"
        or prereg.get("metrics", {}).get("secondary") != "strict_binary"
    ):
        raise SystemExit("GPT-4o supplement preregistration is malformed")
    return prereg


def build_schedule(campaign_id: str, base_port: int) -> list[dict]:
    campaign_id = _safe_id(campaign_id)
    rows = []
    for wave in (1, 2):
        for wave_index, scenario in enumerate(WAVE_SCENARIOS[wave]):
            run_id = f"w{wave}_gpt4o_{scenario}"
            run_name = f"{campaign_id}_{run_id}"
            result_dir = (
                f"amazon__{SCAFFOLD}__{MODEL_RECORDED}__"
                f"{scenario}-{VARIANT}__{CONDITION}"
            )
            rows.append({
                "run_id": run_id,
                "wave": wave,
                "wave_index": wave_index,
                "block": wave,
                "scenario": scenario,
                "variant": VARIANT,
                "condition": CONDITION,
                "scaffold": SCAFFOLD,
                "arm": "deliberative",
                "model_request": MODEL_REQUEST,
                "model_recorded": MODEL_RECORDED,
                "logical_model": MODEL_LOGICAL,
                "probe_stage": (
                    "gpt4o_before" if wave == 1 else "gpt4o_mid"
                ),
                "primary_region": REGIONS[0],
                "region_order": list(REGIONS),
                "port": base_port + wave_index,
                "run_name": run_name,
                "experiment_relpath": f"runs/{run_name}",
                "browser_run_relpath": f"runs/{run_name}/{result_dir}",
                "summary_relpath":
                    f"runs/{run_name}/{result_dir}/summary.json",
                "trajectory_relpath":
                    f"runs/{run_name}/{result_dir}/trajectory.json",
                "run_log_relpath":
                    f"runs/{run_name}/{result_dir}/run.log",
                "launcher_log_relpath":
                    f"launcher_logs/{run_id}.log",
            })
    validate_schedule(rows, base_port)
    return rows


def validate_schedule(rows: list[dict], base_port: int) -> None:
    errors = []
    if len(rows) != 5:
        errors.append(f"schedule has {len(rows)} runs, expected five")
    if len({row.get("run_id") for row in rows}) != len(rows):
        errors.append("run IDs are not unique")
    if {row.get("scenario") for row in rows} != set(SCENARIOS):
        errors.append("the five exact scenarios are not scheduled once")
    for wave in (1, 2):
        wave_rows = [row for row in rows if row.get("wave") == wave]
        expected = WAVE_SCENARIOS[wave]
        if [row.get("scenario") for row in wave_rows] != list(expected):
            errors.append(f"wave {wave} scenario assignment drifted")
        if [row.get("port") for row in wave_rows] != [
            base_port + index for index in range(len(expected))
        ]:
            errors.append(f"wave {wave} port assignment drifted")
    if any(
        row.get("scaffold") != SCAFFOLD
        or row.get("model_request") != MODEL_REQUEST
        or row.get("model_recorded") != MODEL_RECORDED
        or row.get("logical_model") != MODEL_LOGICAL
        or row.get("variant") != VARIANT
        or row.get("condition") != CONDITION
        or row.get("region_order") != list(REGIONS)
        for row in rows
    ):
        errors.append("one or more frozen treatment fields drifted")
    if errors:
        raise SystemExit(
            "invalid GPT-4o supplement schedule:\n  "
            + "\n  ".join(errors)
        )


def _parent_trigger_evidence(
    parent_dir: Path, parent_manifest: dict
) -> dict:
    rows_by_id = {
        row["run_id"]: row for row in parent_manifest["schedule"]
    }
    records = []
    categories = {}
    for run_id in EXPECTED_QWEN_TRIGGER_IDS:
        row = rows_by_id.get(run_id)
        if (
            not isinstance(row, dict)
            or row.get("model_recorded") != "Qwen3.5-122B"
        ):
            raise SystemExit(f"parent trigger row is absent: {run_id}")
        paths = {
            "summary": parent_dir / row["summary_relpath"],
            "trajectory": parent_dir / row["trajectory_relpath"],
            "run_log": parent_dir / row["run_log_relpath"],
            "launcher_log": parent_dir / row["launcher_log_relpath"],
            "launch_receipt": (
                parent_dir / "launch_receipts" / f"{run_id}.json"
            ),
            "launch_receipt_hash": (
                parent_dir / "launch_receipts" / f"{run_id}.sha256.json"
            ),
        }
        missing = [name for name, path in paths.items() if not path.is_file()]
        if missing:
            raise SystemExit(
                f"parent Qwen trigger evidence is incomplete for {run_id}: "
                + ", ".join(missing)
            )
        summary = _read_json(paths["summary"])
        trajectory = _read_json(paths["trajectory"])
        diagnostics = (trajectory.get("stats") or {}).get("deliberative") or {}
        error = str(summary.get("error") or "")
        category = (
            "invalid_json" if "Invalid JSON" in error
            else "upstream_timeout" if "timeout" in error.casefold()
            else "other"
        )
        categories[category] = categories.get(category, 0) + 1
        if (
            summary.get("model") != "Qwen3.5-122B"
            or summary.get("outcome") != "none"
            or summary.get("chosen") is not None
            or summary.get("num_steps") != 0
            or diagnostics.get("contract_compile_calls") != 1
            or diagnostics.get("contract_compile_attempts") != 4
            or diagnostics.get("contract_compile_failures") != 1
            or diagnostics.get("structured_attempt_exhaustions") != 1
            or (trajectory.get("stats") or {}).get("decision_steps") != 0
        ):
            raise SystemExit(
                f"parent Qwen trigger semantics differ for {run_id}"
            )
        records.append({
            "run_id": run_id,
            "scenario": row["scenario"],
            "outcome": "none",
            "chosen": None,
            "analysis_preservation_strict": 0.0,
            "analysis_strict_binary": 0.0,
            "steps": 0,
            "seconds": summary.get("seconds"),
            "terminal_error_category": category,
            "terminal_error": error,
            "contract_compile_attempts": 4,
            "contract_compile_failures": 1,
            "decision_steps": 0,
            "source_summary_ref_at_freeze": _file_ref(paths["summary"]),
            "immutable_evidence": {
                name: _file_ref(path)
                for name, path in paths.items() if name != "summary"
            },
        })
    if categories != {"invalid_json": 2, "upstream_timeout": 1}:
        raise SystemExit(
            "parent Qwen trigger failure inventory differs from the "
            f"disclosed 2 JSON + 1 timeout pattern: {categories}"
        )
    return {
        "kind": "frozen_parent_qwen_compatibility_trigger",
        "reason_code": "qwen_contract_compiler_exhaustion",
        "frozen_at_utc": _utcnow(),
        "scheduled_parent_runs_preserved": True,
        "replacement_or_exclusion": False,
        "analysis_score_rule": "no-order is score zero",
        "records": records,
        "failure_category_counts": categories,
    }


def _validate_frozen_trigger(
    parent_dir: Path, record: object
) -> None:
    if (
        not isinstance(record, dict)
        or record.get("kind")
        != "frozen_parent_qwen_compatibility_trigger"
        or record.get("reason_code")
        != "qwen_contract_compiler_exhaustion"
        or record.get("scheduled_parent_runs_preserved") is not True
        or record.get("replacement_or_exclusion") is not False
        or record.get("analysis_score_rule") != "no-order is score zero"
    ):
        raise SystemExit("frozen Qwen trigger record is malformed")
    records = record.get("records")
    if (
        not isinstance(records, list)
        or [item.get("run_id") for item in records]
        != list(EXPECTED_QWEN_TRIGGER_IDS)
        or record.get("failure_category_counts")
        != {"invalid_json": 2, "upstream_timeout": 1}
    ):
        raise SystemExit("frozen Qwen trigger inventory drifted")
    for item in records:
        if (
            item.get("outcome") != "none"
            or item.get("chosen") is not None
            or item.get("analysis_preservation_strict") != 0.0
            or item.get("analysis_strict_binary") != 0.0
            or item.get("steps") != 0
            or item.get("contract_compile_attempts") != 4
            or item.get("contract_compile_failures") != 1
            or item.get("decision_steps") != 0
        ):
            raise SystemExit(
                f"frozen Qwen trigger semantics drifted: {item.get('run_id')}"
            )
        immutable = item.get("immutable_evidence")
        if not isinstance(immutable, dict):
            raise SystemExit("frozen Qwen immutable evidence is absent")
        for name, ref in immutable.items():
            path = Path(ref.get("path", "")).resolve()
            if (
                parent_dir not in path.parents
                or not path.is_file()
                or ref != _file_ref(path)
            ):
                raise SystemExit(
                    f"parent Qwen immutable evidence drifted: "
                    f"{item.get('run_id')}/{name}"
                )
        # The parent strict reporter may legitimately rewrite summary.json.
        # Its at-freeze hash remains in the immutable supplement manifest but
        # is intentionally not required to match the later live summary.
        summary_ref = item.get("source_summary_ref_at_freeze")
        summary_path = Path(
            summary_ref.get("path", "")
            if isinstance(summary_ref, dict) else ""
        ).resolve()
        if (
            not isinstance(summary_ref, dict)
            or not isinstance(summary_ref.get("sha256"), str)
            or len(summary_ref["sha256"]) != 64
            or parent_dir not in summary_path.parents
        ):
            raise SystemExit("frozen Qwen summary reference is malformed")


def prepare(
    campaign_dir: Path,
    campaign_id: str,
    parent_dir: Path,
    v18_dir: Path,
    base_port: int | None,
) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest_path = campaign_dir / "campaign_manifest.json"
    if manifest_path.exists():
        verify(campaign_dir)
        print("supplement already frozen; nothing was replaced")
        return
    if campaign_dir.exists() and any(campaign_dir.iterdir()):
        raise SystemExit(
            "supplement directory is nonempty without a frozen manifest"
        )
    campaign_id = _safe_id(campaign_id)
    base_port = select_free_band() if base_port is None else base_port
    _validate_port_band(base_port, require_free=True)
    prereg = _preregistration()
    parent_dir = parent_dir.resolve()
    parent_manifest = parent_tool.verify(parent_dir, quiet=True)
    parent_ref = _file_ref(parent_dir / "campaign_manifest.json")
    trigger = _parent_trigger_evidence(parent_dir, parent_manifest)
    v18, v18_ref, smoke_ref = parent_tool._v18_binding(v18_dir)
    parent_tool._assert_v18_runtime(v18)
    schedule = build_schedule(campaign_id, base_port)
    artifacts = {
        scenario: {
            "files_sha256": _sha_bytes(_json_bytes(
                v18["artifacts"][scenario]["files"]
            )),
            "file_count": len(v18["artifacts"][scenario]["files"]),
        }
        for scenario in SCENARIOS
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "campaign_id": campaign_id,
        "frozen_at_utc": _utcnow(),
        "campaign_source": _file_ref(Path(__file__)),
        "preregistration": _file_ref(PREREG_PATH),
        "relationship_to_parent": prereg["relationship_to_parent"],
        "design": prereg["design"],
        "metrics": prereg["metrics"],
        "interpretation": prereg["interpretation"],
        "historical_context": prereg["historical_context"],
        "parent_campaign_manifest": parent_ref,
        "parent_campaign_source": parent_manifest["campaign_source"],
        "frozen_parent_qwen_trigger": trigger,
        "v18_manifest": v18_ref,
        "v18_smoke_gate": smoke_ref,
        "measured_source_inventory": v18["source_inventory"],
        "measured_source_inventory_sha256":
            v18["source_inventory_sha256"],
        "deliberative_source": v18["source_inventory"][
            "agentarena/scaffolds/browseruse_deliberative.py"
        ],
        "runtime_dependencies": v18["runtime_dependencies"],
        "runtime_environment_policy": v18["runtime_environment_policy"],
        "caps": v18["caps"],
        "limit_contract": v18["limit_contract"],
        "certification_sha256": v18["certification"]["frozen_sha256"],
        "artifacts": artifacts,
        "base_port": base_port,
        "port_count": PORT_COUNT,
        "schedule": schedule,
        "probe_policy": {
            "stages": STAGE_BLOCKS,
            "predecessor_blocks": STAGE_PREDECESSORS,
            "scheduled_regions": {
                MODEL_LOGICAL: list(REGIONS)
            },
            "small_and_concurrency_required": True,
            "max_checkpoint_age_seconds": PROBE_MAX_AGE_SECONDS,
            "before_and_mid_model_checks": True,
            "unhealthy_action": "pause_never_kill_active_runs",
        },
        "launch_policy": {
            "wave_run_counts": {"1": 2, "2": 3},
            "spawn_stagger_seconds": v18["caps"]["spawn_stagger_seconds"],
            "create_only_receipts": True,
            "create_only_launcher_logs": True,
            "never_mass_kill": True,
            "never_silently_relaunch": True,
            "behavioral_failures_are_never_refilled": True,
            "qwen_replacement_prohibited": True,
            "protected_port_range": [
                PROTECTED_PORT_LOW, PROTECTED_PORT_HIGH
            ],
        },
        "result_discovery": {
            "policy": "exact_manifest_paths_only",
            "expected_supplement_runs": 5,
            "expected_summaries": [
                row["summary_relpath"] for row in schedule
            ],
        },
    }
    _write_new(manifest_path, manifest)
    _write_new(
        campaign_dir / "campaign_manifest.sha256.json",
        {"path": manifest_path.name, "sha256": _sha_file(manifest_path)},
    )
    verify(campaign_dir)
    print(
        f"PREPARE PASS: {campaign_id}; five additive GPT-4o runs; "
        f"ports {base_port}-{base_port + 2}; Qwen unchanged"
    )


def verify(campaign_dir: Path, *, quiet: bool = False) -> dict:
    campaign_dir = campaign_dir.resolve()
    manifest_path = campaign_dir / "campaign_manifest.json"
    sidecar = campaign_dir / "campaign_manifest.sha256.json"
    if not manifest_path.is_file() or not sidecar.is_file():
        raise SystemExit("GPT-4o supplement is not prepared")
    if _read_json(sidecar) != {
        "path": manifest_path.name,
        "sha256": _sha_file(manifest_path),
    }:
        raise SystemExit("supplement manifest hash mismatch")
    manifest = _read_json(manifest_path)
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("kind") != KIND
        or manifest.get("campaign_source") != _file_ref(Path(__file__))
        or manifest.get("preregistration") != _file_ref(PREREG_PATH)
    ):
        raise SystemExit("supplement kind/source/prereg binding drifted")
    prereg = _preregistration()
    for key in (
        "relationship_to_parent",
        "design",
        "metrics",
        "interpretation",
        "historical_context",
    ):
        if manifest.get(key) != prereg[key]:
            raise SystemExit(f"supplement differs from preregistration: {key}")
    parent_path = Path(manifest["parent_campaign_manifest"]["path"])
    parent_dir = parent_path.parent.resolve()
    parent_manifest = parent_tool.verify(parent_dir, quiet=True)
    if (
        manifest["parent_campaign_manifest"] != _file_ref(parent_path)
        or manifest.get("parent_campaign_source")
        != parent_manifest["campaign_source"]
    ):
        raise SystemExit("parent campaign binding drifted")
    _validate_frozen_trigger(
        parent_dir, manifest.get("frozen_parent_qwen_trigger")
    )
    v18_path = Path(manifest["v18_manifest"]["path"])
    v18, v18_ref, smoke_ref = parent_tool._v18_binding(v18_path.parent)
    parent_tool._assert_v18_runtime(v18)
    if (
        manifest.get("v18_manifest") != v18_ref
        or manifest.get("v18_smoke_gate") != smoke_ref
        or manifest.get("measured_source_inventory")
        != v18["source_inventory"]
        or manifest.get("measured_source_inventory_sha256")
        != v18["source_inventory_sha256"]
        or manifest.get("deliberative_source")
        != v18["source_inventory"][
            "agentarena/scaffolds/browseruse_deliberative.py"
        ]
        or manifest.get("runtime_dependencies")
        != v18["runtime_dependencies"]
        or manifest.get("runtime_environment_policy")
        != v18["runtime_environment_policy"]
        or manifest.get("caps") != v18["caps"]
        or manifest.get("limit_contract") != v18["limit_contract"]
        or manifest.get("certification_sha256")
        != v18["certification"]["frozen_sha256"]
    ):
        raise SystemExit("V18 source/runtime/cap binding drifted")
    _validate_port_band(int(manifest["base_port"]), require_free=False)
    schedule = build_schedule(
        manifest["campaign_id"], int(manifest["base_port"])
    )
    if manifest.get("schedule") != schedule:
        raise SystemExit("supplement schedule differs from frozen source")
    artifacts = {
        scenario: {
            "files_sha256": _sha_bytes(_json_bytes(
                v18["artifacts"][scenario]["files"]
            )),
            "file_count": len(v18["artifacts"][scenario]["files"]),
        }
        for scenario in SCENARIOS
    }
    if manifest.get("artifacts") != artifacts:
        raise SystemExit("supplement artifact binding drifted")
    expected_probe = {
        "stages": STAGE_BLOCKS,
        "predecessor_blocks": STAGE_PREDECESSORS,
        "scheduled_regions": {MODEL_LOGICAL: list(REGIONS)},
        "small_and_concurrency_required": True,
        "max_checkpoint_age_seconds": PROBE_MAX_AGE_SECONDS,
        "before_and_mid_model_checks": True,
        "unhealthy_action": "pause_never_kill_active_runs",
    }
    if manifest.get("probe_policy") != expected_probe:
        raise SystemExit("supplement probe policy drifted")
    expected_launch = {
        "wave_run_counts": {"1": 2, "2": 3},
        "spawn_stagger_seconds": v18["caps"]["spawn_stagger_seconds"],
        "create_only_receipts": True,
        "create_only_launcher_logs": True,
        "never_mass_kill": True,
        "never_silently_relaunch": True,
        "behavioral_failures_are_never_refilled": True,
        "qwen_replacement_prohibited": True,
        "protected_port_range": [
            PROTECTED_PORT_LOW, PROTECTED_PORT_HIGH
        ],
    }
    if (
        manifest.get("port_count") != PORT_COUNT
        or manifest.get("launch_policy") != expected_launch
        or manifest.get("result_discovery") != {
            "policy": "exact_manifest_paths_only",
            "expected_supplement_runs": 5,
            "expected_summaries": [
                row["summary_relpath"] for row in schedule
            ],
        }
    ):
        raise SystemExit("supplement launch/result-discovery policy drifted")
    if not quiet:
        print(
            "VERIFY PASS: additive-only relation, frozen Qwen trigger, "
            "exact GPT-4o schedule, V18 harness/runtime/caps, and original "
            "artifacts unchanged"
        )
    return manifest


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
        env["TRAPI_REGIONS_OVERRIDE"] = json.dumps(
            {MODEL_LOGICAL: row["region_order"]},
            separators=(",", ":"),
        )
    if "STOREFRONT_OPS_TOKEN" in env:
        raise SystemExit("STOREFRONT_OPS_TOKEN survived sanitization")
    return env


def _predecessor_evidence(
    campaign_dir: Path, manifest: dict, stage: str
) -> list[dict]:
    blocks = STAGE_PREDECESSORS[stage]
    evidence = []
    for row in manifest["schedule"]:
        if row["block"] not in blocks:
            continue
        summary = campaign_dir / row["summary_relpath"]
        immutable = {
            "trajectory": campaign_dir / row["trajectory_relpath"],
            "run_log": campaign_dir / row["run_log_relpath"],
            "launcher_log": campaign_dir / row["launcher_log_relpath"],
            "launch_receipt": (
                campaign_dir / "launch_receipts"
                / f"{row['run_id']}.json"
            ),
            "launch_receipt_hash": (
                campaign_dir / "launch_receipts"
                / f"{row['run_id']}.sha256.json"
            ),
        }
        missing = [
            name for name, path in {
                "summary": summary, **immutable
            }.items() if not path.is_file()
        ]
        if missing:
            raise SystemExit(
                f"{stage} probe precedes complete wave-1 evidence for "
                f"{row['run_id']}: {', '.join(missing)}"
            )
        evidence.append({
            "run_id": row["run_id"],
            "block": row["block"],
            "completion_summary_path": row["summary_relpath"],
            "immutable_files": {
                name: {
                    "path": str(path.relative_to(campaign_dir)),
                    "sha256": _sha_file(path),
                    "size": path.stat().st_size,
                }
                for name, path in immutable.items()
            },
        })
    expected = sum(
        row["block"] in blocks for row in manifest["schedule"]
    )
    if len(evidence) != expected:
        raise SystemExit("supplement predecessor evidence is incomplete")
    return evidence


def run_probe(campaign_dir: Path, stage: str, label: str) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify(campaign_dir, quiet=True)
    if stage not in STAGE_BLOCKS:
        raise SystemExit(f"unknown supplement probe stage: {stage}")
    label = _safe_id(label)
    checkpoint = campaign_dir / "probes" / f"checkpoint_{label}.json"
    if checkpoint.exists():
        raise SystemExit(f"refusing to replace checkpoint: {checkpoint}")
    predecessor = _predecessor_evidence(campaign_dir, manifest, stage)
    audit_refill_coexistence(int(manifest["base_port"]))
    env = _apply_environment(manifest)
    env["TRAPI_REGIONS_OVERRIDE"] = json.dumps(
        {MODEL_LOGICAL: list(REGIONS)}, separators=(",", ":")
    )
    env["AGENTARENA_PROBE_LIVE_ONLY"] = "1"
    env["AGENTARENA_PROBE_INCLUDE_REDMOND"] = "1"
    probe_dir = campaign_dir / "probes"
    probe_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "small": probe_dir / f"small_{label}.log",
        "concurrency": probe_dir / f"concurrency_{label}.log",
    }
    commands = {
        "small": [
            sys.executable, str(SCRIPT_DIR / "probe_regions.py"),
            MODEL_LOGICAL,
        ],
        "concurrency": [
            sys.executable, str(SCRIPT_DIR / "probe_concurrency.py"),
            MODEL_LOGICAL,
        ],
    }
    for name in ("small", "concurrency"):
        if paths[name].exists():
            raise SystemExit(f"refusing to replace probe log: {paths[name]}")
        print(f"probe {stage}/{name}: {' '.join(commands[name])}", flush=True)
        with paths[name].open("x") as stream:
            result = subprocess.run(
                commands[name],
                cwd=ROOT,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
            )
        print(paths[name].read_text(errors="replace"), end="", flush=True)
        if result.returncode:
            raise SystemExit(
                f"{name} probe failed with exit {result.returncode}; "
                "evidence preserved"
            )
    evidence = validate_probe_evidence(
        manifest, stage, paths["small"], paths["concurrency"], None
    )
    record = {
        **evidence,
        "label": label,
        "published_at_utc": _utcnow(),
        "manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "predecessor_evidence": predecessor,
        "refill_coexistence": audit_refill_coexistence(
            int(manifest["base_port"])
        ),
        "logs": {
            name: {
                "path": str(path.relative_to(campaign_dir)),
                "sha256": _sha_file(path),
                "size": path.stat().st_size,
            }
            for name, path in paths.items()
        },
    }
    _write_new(checkpoint, record)
    print(f"PROBE PASS: {stage}/{label}")


def _verify_checkpoint(
    campaign_dir: Path, manifest: dict, stage: str, label: str
) -> dict:
    label = _safe_id(label)
    path = campaign_dir / "probes" / f"checkpoint_{label}.json"
    if not path.is_file():
        raise SystemExit(f"missing supplement checkpoint: {path}")
    record = _read_json(path)
    if (
        record.get("label") != label
        or record.get("stage") != stage
        or record.get("blocks") != STAGE_BLOCKS[stage]
        or record.get("manifest_sha256")
        != _sha_file(campaign_dir / "campaign_manifest.json")
    ):
        raise SystemExit(f"checkpoint identity drifted: {label}")
    try:
        published = _parse_utc(
            record.get("published_at_utc"), "checkpoint publication"
        )
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"checkpoint timestamp is invalid: {exc}") from exc
    age = (
        dt.datetime.now(dt.timezone.utc) - published
    ).total_seconds()
    if age < -60 or age > PROBE_MAX_AGE_SECONDS:
        raise SystemExit(
            f"checkpoint {label} is outside its one-hour window "
            f"(age={age:.1f}s)"
        )
    if record.get("predecessor_evidence") != _predecessor_evidence(
        campaign_dir, manifest, stage
    ):
        raise SystemExit(f"checkpoint predecessor evidence drifted: {label}")
    logs = record.get("logs")
    if not isinstance(logs, dict) or set(logs) != {"small", "concurrency"}:
        raise SystemExit(f"checkpoint logs are malformed: {label}")
    resolved = {}
    for name, ref in logs.items():
        candidate = (campaign_dir / ref.get("path", "")).resolve()
        if (
            campaign_dir not in candidate.parents
            or not candidate.is_file()
            or ref.get("sha256") != _sha_file(candidate)
            or ref.get("size") != candidate.stat().st_size
        ):
            raise SystemExit(f"checkpoint log drifted: {label}/{name}")
        resolved[name] = candidate
    evidence = validate_probe_evidence(
        manifest, stage, resolved["small"], resolved["concurrency"], None
    )
    for key, value in evidence.items():
        if record.get(key) != value:
            raise SystemExit(f"checkpoint evidence drifted: {label}/{key}")
    return {
        "path": str(path.relative_to(campaign_dir)),
        "sha256": _sha_file(path),
    }


def _write_receipt(
    campaign_dir: Path,
    manifest: dict,
    row: dict,
    checkpoint: dict,
    command: list[str],
    env: dict,
) -> None:
    path = campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
    expected_override = json.dumps(
        {MODEL_LOGICAL: row["region_order"]},
        separators=(",", ":"),
    )
    if env.get("TRAPI_REGIONS_OVERRIDE") != expected_override:
        raise SystemExit("supplement route override differs from schedule")
    record = {
        "run_id": row["run_id"],
        "row": row,
        "launched_at_utc": _utcnow(),
        "manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "campaign_source_sha256": manifest["campaign_source"]["sha256"],
        "parent_campaign_manifest_sha256":
            manifest["parent_campaign_manifest"]["sha256"],
        "qwen_replacement_or_exclusion": False,
        "measured_source_inventory_sha256":
            manifest["measured_source_inventory_sha256"],
        "certification_sha256": manifest["certification_sha256"],
        "checkpoint_path": checkpoint["path"],
        "checkpoint_sha256": checkpoint["sha256"],
        "trapi_regions_override": expected_override,
        "caps": manifest["caps"],
        "command": command,
    }
    _write_new(path, record)
    _write_new(
        path.with_suffix(".sha256.json"),
        {"path": path.name, "sha256": _sha_file(path)},
    )


def _verify_receipt(
    campaign_dir: Path, manifest: dict, row: dict
) -> dict:
    path = campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
    sidecar = path.with_suffix(".sha256.json")
    if not path.is_file() or not sidecar.is_file():
        raise ValueError(f"supplement receipt is absent: {row['run_id']}")
    if _read_json(sidecar) != {
        "path": path.name, "sha256": _sha_file(path)
    }:
        raise ValueError(f"supplement receipt hash drifted: {row['run_id']}")
    record = _read_json(path)
    if (
        record.get("run_id") != row["run_id"]
        or record.get("row") != row
        or record.get("manifest_sha256")
        != _sha_file(campaign_dir / "campaign_manifest.json")
        or record.get("qwen_replacement_or_exclusion") is not False
        or record.get("measured_source_inventory_sha256")
        != manifest["measured_source_inventory_sha256"]
        or record.get("certification_sha256")
        != manifest["certification_sha256"]
        or record.get("caps") != manifest["caps"]
    ):
        raise ValueError(f"supplement receipt binding drifted: {row['run_id']}")
    relative = record.get("checkpoint_path")
    if not isinstance(relative, str) or not relative:
        raise ValueError("supplement receipt checkpoint path is malformed")
    checkpoint = (campaign_dir / relative).resolve()
    if (
        campaign_dir not in checkpoint.parents
        or not checkpoint.is_file()
        or record.get("checkpoint_sha256") != _sha_file(checkpoint)
    ):
        raise ValueError(f"supplement checkpoint drifted: {row['run_id']}")
    return {"path": str(path.relative_to(campaign_dir)),
            "sha256": _sha_file(path)}


def launch_wave(
    campaign_dir: Path, wave: int, checkpoint_label: str
) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify(campaign_dir, quiet=True)
    if wave not in (1, 2):
        raise SystemExit("supplement wave must be 1 or 2")
    rows = sorted(
        (row for row in manifest["schedule"] if row["wave"] == wave),
        key=lambda row: row["wave_index"],
    )
    if len(rows) != len(WAVE_SCENARIOS[wave]):
        raise SystemExit("supplement wave size drifted")
    pending = []
    for row in rows:
        experiment = campaign_dir / row["experiment_relpath"]
        summary = campaign_dir / row["summary_relpath"]
        receipt = (
            campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
        )
        receipt_hash = receipt.with_suffix(".sha256.json")
        launcher = campaign_dir / row["launcher_log_relpath"]
        if summary.is_file():
            required = (
                campaign_dir / row["trajectory_relpath"],
                campaign_dir / row["run_log_relpath"],
                launcher,
                receipt,
                receipt_hash,
            )
            if any(not path.is_file() for path in required):
                raise SystemExit(
                    f"completed supplement run has incomplete evidence: "
                    f"{row['run_id']}"
                )
            try:
                _verify_receipt(campaign_dir, manifest, row)
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
            print(f"already complete, preserving: {row['run_id']}")
            continue
        if any(path.exists() for path in (
            experiment, receipt, receipt_hash, launcher
        )):
            raise SystemExit(
                f"partial supplement evidence exists for {row['run_id']}; "
                "never silently relaunch"
            )
        pending.append(row)
    if not pending:
        print(f"SUPPLEMENT WAVE {wave}: already complete")
        return
    stage = "gpt4o_before" if wave == 1 else "gpt4o_mid"
    checkpoint = _verify_checkpoint(
        campaign_dir, manifest, stage, checkpoint_label
    )
    audit_refill_coexistence(int(manifest["base_port"]))
    _validate_port_band(int(manifest["base_port"]), require_free=True)
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
    failures = []
    try:
        for index, row in enumerate(pending):
            if interrupted:
                break
            env = _apply_environment(manifest, row)
            command = [
                sys.executable,
                "-m",
                "agentarena.benchmark.run",
                "--name", row["run_name"],
                "--scenarios", row["scenario"],
                "--conditions", row["condition"],
                "--variants", row["variant"],
                "--scaffolds", row["scaffold"],
                "--models", row["model_request"],
                "--max-steps", str(manifest["caps"]["max_steps"]),
                "--repeats", "1",
                "--jobs", "1",
                "--results", str(campaign_dir / "runs"),
                "--base-port", str(row["port"]),
            ]
            _write_receipt(
                campaign_dir, manifest, row, checkpoint, command, env
            )
            launcher = campaign_dir / row["launcher_log_relpath"]
            launcher.parent.mkdir(parents=True, exist_ok=True)
            stream = launcher.open("x")
            print(
                f"launch {row['run_id']} primary={row['primary_region']} "
                f"port={row['port']}",
                flush=True,
            )
            try:
                process = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
            except BaseException:
                stream.close()
                raise
            processes.append((row, process, stream))
            if index + 1 < len(pending) and not interrupted:
                time.sleep(manifest["caps"]["spawn_stagger_seconds"])
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
        row["run_id"] for row, _process, _stream in processes
        if not (campaign_dir / row["summary_relpath"]).is_file()
    ]
    unlaunched = [
        row["run_id"] for row in pending if row["run_id"] not in launched
    ]
    if interrupted or failures or missing or unlaunched:
        raise SystemExit(
            "supplement wave ended with preserved evidence; no process was "
            f"killed or relaunched: interrupted={interrupted}, "
            f"failures={failures}, missing={missing}, "
            f"unlaunched={unlaunched}"
        )
    print(f"SUPPLEMENT WAVE PASS: {wave} ({len(processes)} fresh runs)")


def _number(value: object) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    ):
        return float(value)
    return None


def _fresh_rescore(campaign_dir: Path, manifest: dict) -> dict:
    from agentarena.scoring.rescore import write_strict

    started = _utcnow()
    updated = sum(
        write_strict(str(campaign_dir / row["experiment_relpath"]))
        for row in manifest["schedule"]
    )
    required = {
        "preservation_strict",
        "preservation_cont",
        "strict_binary",
        "resistance_margin",
    }
    outputs = []
    for row in manifest["schedule"]:
        summary_path = campaign_dir / row["summary_relpath"]
        summary = _read_json(summary_path)
        outputs.append({
            "run_id": row["run_id"],
            "summary_sha256": _sha_file(summary_path),
            "trajectory_sha256": _sha_file(
                campaign_dir / row["trajectory_relpath"]
            ),
            "strict_keys_present": sorted(required.intersection(summary)),
        })
    scorer = ROOT / "agentarena/scoring/rescore.py"
    return {
        "kind": "fresh_strict_rescore_audit",
        "performed": True,
        "started_at_utc": started,
        "completed_at_utc": _utcnow(),
        "scorer": {
            "path": str(scorer.relative_to(ROOT)),
            "sha256": _sha_file(scorer),
        },
        "expected_summary_count": 5,
        "updated_summary_count": updated,
        "fresh_complete": (
            updated == 5
            and len(outputs) == 5
            and all(set(item["strict_keys_present"]) == required
                    for item in outputs)
        ),
        "outputs": outputs,
    }


def _supplement_run_record(
    campaign_dir: Path, manifest: dict, row: dict
) -> dict:
    summary_path = campaign_dir / row["summary_relpath"]
    trajectory_path = campaign_dir / row["trajectory_relpath"]
    run_log_path = campaign_dir / row["run_log_relpath"]
    launcher_path = campaign_dir / row["launcher_log_relpath"]
    for path in (
        summary_path, trajectory_path, run_log_path, launcher_path
    ):
        if not path.is_file():
            raise SystemExit(f"supplement artifact is absent: {path}")
    summary = _read_json(summary_path)
    trajectory = _read_json(trajectory_path)
    receipt = _verify_receipt(campaign_dir, manifest, row)
    errors = []
    expected = {
        "env": "amazon",
        "scaffold": SCAFFOLD,
        "model": MODEL_RECORDED,
        "task_id": f"{row['scenario']}-{VARIANT}",
        "condition": CONDITION,
    }
    for key, wanted in expected.items():
        if summary.get(key) != wanted:
            errors.append(
                f"{key}={summary.get(key)!r}, expected {wanted!r}"
            )
    chosen = summary.get("chosen")
    outcome = summary.get("outcome")
    no_order = not chosen or outcome in {"none", "error", "skipped", None}
    pstar = _number(summary.get("preservation_strict"))
    binary = _number(summary.get("strict_binary"))
    if no_order and pstar is None:
        pstar = 0.0
    if no_order and binary is None:
        binary = 0.0
    if pstar is None or not 0 <= pstar <= 1:
        errors.append("preservation_strict is absent/outside [0,1]")
        pstar = 0.0
    if binary not in (0.0, 1.0):
        errors.append("strict_binary is absent/not binary")
        binary = 0.0
    stats = trajectory.get("stats")
    if not isinstance(stats, dict):
        stats = {}
        errors.append("trajectory stats are absent")
    diagnostics = stats.get("deliberative")
    if not isinstance(diagnostics, dict):
        diagnostics = {}
        errors.append("deliberative diagnostics are absent")
    else:
        diagnostic_error = _contract_diagnostic_error(diagnostics)
        if diagnostic_error:
            errors.append(f"literal contract diagnostic: {diagnostic_error}")
        if diagnostics.get("runtime_source_attestation") != manifest[
            "measured_source_inventory_sha256"
        ]:
            errors.append("runtime source attestation differs")
        if diagnostics.get("evaluation_input_attestation") != manifest[
            "certification_sha256"
        ]:
            errors.append("evaluation input attestation differs")
    limit_audit = stats.get("limit_audit")
    errors.extend(
        f"limit audit: {item}" for item in validate_limit_audit(
            limit_audit, manifest["limit_contract"], "deliberative"
        )
    )
    categories = (
        limit_audit.get("categories", {})
        if isinstance(limit_audit, dict) else {}
    )
    safety = {
        name: record.get("touched_count")
        for name, record in categories.get(
            "safety_backstops", {}
        ).items()
        if isinstance(record, dict) and record.get("touched_count")
    }
    context_audit = stats.get("context_cap_audit")
    if (
        not isinstance(context_audit, dict)
        or context_audit.get("complete") is not True
    ):
        errors.append("context-cap audit is absent/incomplete")
    context = {
        name: record.get("touched_count")
        for name, record in (
            context_audit.get("limits", {})
            if isinstance(context_audit, dict) else {}
        ).items()
        if isinstance(record, dict) and record.get("touched_count")
    }
    fixed = categories.get("fixed_architecture", {})
    fallback = fixed.get("fallback_llm_depth", {}) if isinstance(
        fixed, dict
    ) else {}
    log_text = (
        run_log_path.read_text(errors="replace")
        + "\n"
        + launcher_path.read_text(errors="replace")
    )
    markers = [marker for marker in BOUND_LOG_MARKERS if marker in log_text]
    steps = summary.get("num_steps")
    seconds = _number(summary.get("seconds"))
    return {
        "source": "gpt4o_supplement",
        "run_id": row["run_id"],
        "wave": row["wave"],
        "model": MODEL_RECORDED,
        "scenario": row["scenario"],
        "chosen": chosen,
        "chosen_label": summary.get("chosen_label"),
        "role": (
            "hero" if binary == 1.0
            else "no_order" if not chosen
            else "nonhero"
        ),
        "outcome": outcome,
        "preservation_strict": pstar,
        "strict_binary": binary,
        "steps": (
            steps if isinstance(steps, int)
            and not isinstance(steps, bool) and steps >= 0 else 0
        ),
        "seconds": seconds if seconds is not None and seconds >= 0 else 0.0,
        "contract_compile_attempts":
            diagnostics.get("contract_compile_attempts"),
        "contract_compile_failures":
            diagnostics.get("contract_compile_failures"),
        "decision_checkpoint_calls":
            diagnostics.get("decision_checkpoint_calls"),
        "decision_checkpoint_approvals":
            diagnostics.get("decision_checkpoint_approvals"),
        "safety_backstop_touches": safety,
        "lossy_context_cap_touches": context,
        "same_model_route_fallback_used": bool(
            isinstance(fallback, dict) and fallback.get("touched_count")
        ),
        "bound_log_markers": markers,
        "validation_errors": errors,
        "launch_receipt": receipt,
    }


def _parent_qwen_records(manifest: dict) -> dict:
    parent_path = Path(manifest["parent_campaign_manifest"]["path"])
    parent_dir = parent_path.parent.resolve()
    parent_manifest = parent_tool.verify(parent_dir, quiet=True)
    records = []
    for row in parent_manifest["schedule"]:
        if row.get("model_recorded") != "Qwen3.5-122B":
            continue
        summary_path = parent_dir / row["summary_relpath"]
        trajectory_path = parent_dir / row["trajectory_relpath"]
        if not summary_path.is_file() or not trajectory_path.is_file():
            continue
        summary = _read_json(summary_path)
        chosen = summary.get("chosen")
        outcome = summary.get("outcome")
        no_order = not chosen or outcome in {
            "none", "error", "skipped", None
        }
        pstar = _number(summary.get("preservation_strict"))
        binary = _number(summary.get("strict_binary"))
        if no_order and pstar is None:
            pstar = 0.0
        if no_order and binary is None:
            binary = 0.0
        records.append({
            "source": "parent_qwen_preserved",
            "run_id": row["run_id"],
            "wave": row["wave"],
            "model": "Qwen3.5-122B",
            "scenario": row["scenario"],
            "chosen": chosen,
            "outcome": outcome,
            "preservation_strict": (
                pstar if pstar is not None else 0.0
            ),
            "strict_binary": (
                binary if binary in (0.0, 1.0) else 0.0
            ),
            "steps": summary.get("num_steps") or 0,
            "seconds": summary.get("seconds") or 0.0,
            "included_in_gpt4o_denominator": False,
            "excluded_or_replaced": False,
        })
    return {
        "expected_runs": 5,
        "completed_runs": len(records),
        "complete": len(records) == 5,
        "records": records,
    }


def _aggregate(rows: list[dict]) -> dict:
    if not rows:
        return {
            "runs": 0,
            "mean_preservation_strict": None,
            "strict_successes": 0,
            "strict_rate": None,
            "orders": 0,
            "no_orders": 0,
        }
    return {
        "runs": len(rows),
        "mean_preservation_strict": sum(
            float(row["preservation_strict"]) for row in rows
        ) / len(rows),
        "strict_successes": int(sum(
            float(row["strict_binary"]) for row in rows
        )),
        "strict_rate": sum(
            float(row["strict_binary"]) for row in rows
        ) / len(rows),
        "orders": sum(bool(row["chosen"]) for row in rows),
        "no_orders": sum(not bool(row["chosen"]) for row in rows),
    }


def _report_markdown(report_value: dict) -> str:
    gpt = report_value["aggregates"]["gpt4o_supplement"]
    qwen = report_value["aggregates"]["parent_qwen_preserved"]
    lines = [
        "# GPT-4o compatibility supplement",
        "",
        "This is additive. No Qwen run is excluded, replaced, or rescored "
        "by a GPT-4o result.",
        "",
        "| Cohort | Runs | Mean P* | Heroes | Orders |",
        "|---|---:|---:|---:|---:|",
        f"| GPT-4o supplement | {gpt['runs']} | "
        f"{gpt['mean_preservation_strict']:.3f} | "
        f"{gpt['strict_successes']}/{gpt['runs']} | "
        f"{gpt['orders']}/{gpt['runs']} |",
    ]
    if qwen["runs"]:
        lines.append(
            f"| Parent Qwen (preserved) | {qwen['runs']} | "
            f"{qwen['mean_preservation_strict']:.3f} | "
            f"{qwen['strict_successes']}/{qwen['runs']} | "
            f"{qwen['orders']}/{qwen['runs']} |"
        )
    lines.extend([
        "",
        f"Parent Qwen completion at this snapshot: "
        f"{report_value['parent_qwen']['completed_runs']}/5.",
        "",
        "The supplement was motivated by the frozen three-run Qwen "
        "contract-compiler failure pattern. It is not a replacement draw or "
        "a causal randomized comparison.",
        "",
    ])
    return "\n".join(lines)


def report(campaign_dir: Path) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify(campaign_dir, quiet=True)
    missing = [
        row["run_id"] for row in manifest["schedule"]
        if not (campaign_dir / row["summary_relpath"]).is_file()
    ]
    if missing:
        raise SystemExit(
            "cannot report incomplete GPT-4o fixed denominator: "
            + ", ".join(missing)
        )
    rescore = _fresh_rescore(campaign_dir, manifest)
    gpt_rows = [
        _supplement_run_record(campaign_dir, manifest, row)
        for row in manifest["schedule"]
    ]
    parent_qwen = _parent_qwen_records(manifest)
    qwen_rows = parent_qwen["records"]
    validation_errors = {
        row["run_id"]: row["validation_errors"]
        for row in gpt_rows if row["validation_errors"]
    }
    safety = any(
        row["safety_backstop_touches"] or row["bound_log_markers"]
        for row in gpt_rows
    )
    context = any(row["lossy_context_cap_touches"] for row in gpt_rows)
    report_value = {
        "schema_version": 1,
        "kind": REPORT_KIND,
        "reported_at_utc": _utcnow(),
        "campaign_manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "relationship_to_parent": {
            "additive_only": True,
            "qwen_replacement_or_exclusion": False,
            "pooled_substitution": False,
        },
        "headline": "preservation_strict",
        "secondary": "strict_binary",
        "rescore_audit": rescore,
        "frozen_parent_qwen_trigger":
            manifest["frozen_parent_qwen_trigger"],
        "parent_qwen": parent_qwen,
        "aggregates": {
            "gpt4o_supplement": _aggregate(gpt_rows),
            "parent_qwen_preserved": _aggregate(qwen_rows),
        },
        "validity": {
            "gpt4o_fixed_denominator_complete": len(gpt_rows) == 5,
            "fresh_strict_rescore": rescore["fresh_complete"] is True,
            "qwen_runs_preserved": all(
                row["excluded_or_replaced"] is False
                and row["included_in_gpt4o_denominator"] is False
                for row in qwen_rows
            ),
            "gpt4o_validation_errors": validation_errors,
            "safety_backstop_exposed": safety,
            "lossy_context_cap_exposed": context,
            "all_gpt4o_invariants_green": (
                len(gpt_rows) == 5
                and rescore["fresh_complete"] is True
                and not validation_errors
                and not safety
                and not context
            ),
        },
        "gpt4o_runs": gpt_rows,
    }
    reports = campaign_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    for number in range(1, 1_000_000):
        stem = f"report_{number:04d}"
        json_path = reports / f"{stem}.json"
        markdown_path = reports / f"{stem}.md"
        hash_path = reports / f"{stem}.json.sha256.json"
        if any(path.exists() for path in (
            json_path, markdown_path, hash_path
        )):
            continue
        _write_new(json_path, report_value)
        _write_new_text(markdown_path, _report_markdown(report_value))
        _write_new(
            hash_path,
            {
                "path": json_path.name,
                "sha256": _sha_file(json_path),
                "markdown_path": markdown_path.name,
                "markdown_sha256": _sha_file(markdown_path),
            },
        )
        print(json.dumps(report_value["aggregates"], indent=2, sort_keys=True))
        print(f"REPORT PASS: {json_path}")
        return
    raise SystemExit("no create-only supplement report number remains")


def status(campaign_dir: Path) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify(campaign_dir, quiet=True)
    waves = {}
    for wave in (1, 2):
        rows = [row for row in manifest["schedule"] if row["wave"] == wave]
        complete = [
            row["run_id"] for row in rows
            if (campaign_dir / row["summary_relpath"]).is_file()
        ]
        partial = [
            row["run_id"] for row in rows
            if row["run_id"] not in complete and (
                (campaign_dir / row["experiment_relpath"]).exists()
                or (
                    campaign_dir / "launch_receipts"
                    / f"{row['run_id']}.json"
                ).exists()
                or (
                    campaign_dir / row["launcher_log_relpath"]
                ).exists()
            )
        ]
        waves[str(wave)] = {
            "scheduled": len(rows),
            "complete": len(complete),
            "complete_run_ids": complete,
            "partial_run_ids": partial,
        }
    parent_qwen = _parent_qwen_records(manifest)
    checkpoints = sorted(
        path.name for path in (campaign_dir / "probes").glob(
            "checkpoint_*.json"
        )
    ) if (campaign_dir / "probes").is_dir() else []
    print(json.dumps({
        "campaign_id": manifest["campaign_id"],
        "relationship": "additive_only_never_replaces_qwen",
        "expected_gpt4o_runs": 5,
        "completed_gpt4o_runs": sum(
            item["complete"] for item in waves.values()
        ),
        "parent_qwen_completed_runs": parent_qwen["completed_runs"],
        "parent_qwen_expected_runs": 5,
        "waves": waves,
        "probe_checkpoints": checkpoints,
    }, indent=2, sort_keys=True))


def print_schedule(campaign_dir: Path) -> None:
    manifest = verify(campaign_dir, quiet=True)
    print(json.dumps(manifest["schedule"], indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("select-band")

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--campaign-dir", type=Path, required=True)
    prepare_parser.add_argument("--campaign-id", required=True)
    prepare_parser.add_argument(
        "--parent-dir", type=Path, default=PARENT_DEFAULT
    )
    prepare_parser.add_argument(
        "--v18-dir", type=Path, default=V18_DEFAULT
    )
    prepare_parser.add_argument("--base-port", type=int)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--campaign-dir", type=Path, required=True)

    schedule_parser = subparsers.add_parser("schedule")
    schedule_parser.add_argument("--campaign-dir", type=Path, required=True)

    probe_parser = subparsers.add_parser("probe")
    probe_parser.add_argument("--campaign-dir", type=Path, required=True)
    probe_parser.add_argument(
        "--stage", choices=tuple(STAGE_BLOCKS), required=True
    )
    probe_parser.add_argument("--label", required=True)

    launch_parser = subparsers.add_parser("launch-wave")
    launch_parser.add_argument("--campaign-dir", type=Path, required=True)
    launch_parser.add_argument("--wave", type=int, choices=(1, 2), required=True)
    launch_parser.add_argument("--checkpoint", required=True)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("--campaign-dir", type=Path, required=True)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--campaign-dir", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "select-band":
        print(select_free_band())
    elif args.command == "prepare":
        prepare(
            args.campaign_dir,
            args.campaign_id,
            args.parent_dir,
            args.v18_dir,
            args.base_port,
        )
    elif args.command == "verify":
        verify(args.campaign_dir)
    elif args.command == "schedule":
        print_schedule(args.campaign_dir)
    elif args.command == "probe":
        run_probe(args.campaign_dir, args.stage, args.label)
    elif args.command == "launch-wave":
        launch_wave(args.campaign_dir, args.wave, args.checkpoint)
    elif args.command == "status":
        status(args.campaign_dir)
    elif args.command == "report":
        report(args.campaign_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
