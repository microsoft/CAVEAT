#!/usr/bin/env python
"""Create-only cross-model evaluation of the frozen deliberative harness.

This campaign adds no scaffold, prompt, browser action, or benchmark behavior.
It binds the exact V18 measured source/runtime/artifacts and evaluates only the
already-registered ``browseruse-deliberative`` scaffold on two additional weak
model families.
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

from hard_campaign_runtime import (  # noqa: E402
    CAPS,
    _validate_caps,
    code_inventory,
    runtime_dependency_manifest,
    runtime_limit_contract,
    validate_limit_audit,
)
from harness_eval_campaign import (  # noqa: E402
    _contract_diagnostic_error,
    audit_refill_coexistence,
    validate_probe_evidence,
    verify_campaign as verify_v18_campaign,
    verify_smoke_gate as verify_v18_smoke_gate,
)


SCHEMA_VERSION = 1
KIND = "browseruse_deliberative_cross_model_easy_campaign"
REPORT_KIND = "browseruse_deliberative_cross_model_easy_report"
PREREG_PATH = HERE / "preregistration.json"
V18_DEFAULT = ROOT / "results/harness_deliberative_ab_confirmatory_v18"
SCAFFOLD = "browseruse-deliberative"
VARIANT = "graded"
CONDITION = "combined"
SCENARIOS = ("laptop", "office_chair", "mattress", "backpack", "tent")
PROBE_MAX_AGE_SECONDS = 3_600
PORT_COUNT = 5
PROTECTED_PORT_LOW = 13_200
PROTECTED_PORT_HIGH = 13_299
LIMIT_NEAR_FRACTION = 0.25

MODEL_SPECS = {
    "qwen": {
        "request": "Qwen3.5-122B",
        "recorded": "Qwen3.5-122B",
        "logical": "Qwen3.5-122B",
        "regions": ("gcr/shared", "redmond/interactive"),
    },
    "kimi": {
        "request": "Kimi-K2.6",
        "recorded": "Kimi-K2.6",
        "logical": "Kimi-K2.6",
        "regions": ("msraif/shared", "gcr/shared"),
    },
}

# Complementary assignments put every scenario in each wave across models.
WAVE_ASSIGNMENTS = {
    1: {
        "qwen": ("laptop", "mattress", "tent"),
        "kimi": ("office_chair", "backpack"),
    },
    2: {
        "qwen": ("office_chair", "backpack"),
        "kimi": ("laptop", "mattress", "tent"),
    },
}
BLOCKS = {
    (1, "qwen"): 1,
    (1, "kimi"): 2,
    (2, "qwen"): 3,
    (2, "kimi"): 4,
}
STAGE_BLOCKS = {
    "qwen_before": [1],
    "kimi_before": [2],
    "qwen_mid": [3],
    "kimi_mid": [4],
}
STAGE_PREDECESSOR_BLOCKS = {
    "qwen_before": [],
    "kimi_before": [],
    "qwen_mid": [1, 2],
    "kimi_mid": [1, 2],
}
MODEL_STAGE = {
    (1, "qwen"): "qwen_before",
    (1, "kimi"): "kimi_before",
    (2, "qwen"): "qwen_mid",
    (2, "kimi"): "kimi_mid",
}
BOUND_LOG_MARKERS = (
    "AGENTARENA_CELL_TIMEOUT_BOUND",
    "Stopping due to 1000 consecutive failures",
    "ModelOutputTruncatedError",
    "AGENTARENA_EVALUATE_STORE_INTEGRITY_FAILURE",
)


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
        raise SystemExit(f"expected a JSON object: {path}")
    return value


def _write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise SystemExit(f"refusing to replace create-only file: {path}") from exc
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_new_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
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
        raise SystemExit("campaign port band exceeds 65535")
    if not (high < PROTECTED_PORT_LOW or base_port > PROTECTED_PORT_HIGH):
        raise SystemExit("campaign port band overlaps protected refill ports")
    if require_free:
        occupied = [
            port for port in range(base_port, high + 1)
            if not _port_is_free(port)
        ]
        if occupied:
            raise SystemExit(
                "campaign port band is not free: "
                + ", ".join(str(port) for port in occupied)
            )


def select_free_band() -> int:
    for base in range(17_800, 30_000, 10):
        try:
            _validate_port_band(base, require_free=True)
        except SystemExit:
            continue
        return base
    raise SystemExit("no free five-port campaign band was found")


def _preregistration() -> dict:
    prereg = _read_json(PREREG_PATH)
    expected_models = [
        {
            "request": model["request"],
            "recorded": model["recorded"],
            "logical": model["logical"],
            "family": family,
            "regions": list(model["regions"]),
        }
        for model, family in (
            (MODEL_SPECS["qwen"], "Alibaba Qwen"),
            (MODEL_SPECS["kimi"], "Moonshot Kimi"),
        )
    ]
    expected_waves = [
        {
            "wave": wave,
            "assignments": {
                MODEL_SPECS[key]["recorded"]: list(
                    WAVE_ASSIGNMENTS[wave][key]
                )
                for key in ("qwen", "kimi")
            },
        }
        for wave in (1, 2)
    ]
    scope = prereg.get("scope", {})
    design = prereg.get("design", {})
    if (
        prereg.get("schema_version") != 1
        or prereg.get("kind")
        != "browseruse_deliberative_cross_model_easy_preregistration"
        or scope.get("environment") != "amazon"
        or scope.get("scaffold") != SCAFFOLD
        or scope.get("variant") != VARIANT
        or scope.get("condition") != CONDITION
        or scope.get("scenarios") != list(SCENARIOS)
        or scope.get("models") != expected_models
        or design.get("measured_runs") != 10
        or design.get("runs_per_wave") != 5
        or design.get("repeats_per_model_scenario") != 1
        or design.get("waves") != expected_waves
        or design.get("fixed_denominator") is not True
        or design.get("no_outcome_driven_iteration") is not True
        or design.get("no_outcome_driven_topup") is not True
        or prereg.get("metrics", {}).get("headline")
        != "preservation_strict"
        or prereg.get("metrics", {}).get("secondary") != "strict_binary"
    ):
        raise SystemExit("cross-model preregistration is malformed")
    return prereg


def build_schedule(campaign_id: str, base_port: int) -> list[dict]:
    campaign_id = _safe_id(campaign_id)
    rows: list[dict] = []
    for wave in (1, 2):
        wave_index = 0
        for model_key in ("qwen", "kimi"):
            model = MODEL_SPECS[model_key]
            block = BLOCKS[(wave, model_key)]
            stage = MODEL_STAGE[(wave, model_key)]
            for scenario in WAVE_ASSIGNMENTS[wave][model_key]:
                run_id = f"w{wave}_{model_key}_{scenario}"
                run_name = f"{campaign_id}_{run_id}"
                result_dir = (
                    f"amazon__{SCAFFOLD}__{model['recorded']}__"
                    f"{scenario}-{VARIANT}__{CONDITION}"
                )
                rows.append({
                    "run_id": run_id,
                    "wave": wave,
                    "wave_index": wave_index,
                    "block": block,
                    "model_key": model_key,
                    "model_request": model["request"],
                    "model_recorded": model["recorded"],
                    "logical_model": model["logical"],
                    "scenario": scenario,
                    "variant": VARIANT,
                    "condition": CONDITION,
                    "scaffold": SCAFFOLD,
                    "arm": "deliberative",
                    "probe_stage": stage,
                    "primary_region": model["regions"][0],
                    "region_order": list(model["regions"]),
                    "port": base_port + wave_index,
                    "run_name": run_name,
                    "experiment_relpath": f"runs/{run_name}",
                    "browser_run_relpath": (
                        f"runs/{run_name}/{result_dir}"
                    ),
                    "summary_relpath": (
                        f"runs/{run_name}/{result_dir}/summary.json"
                    ),
                    "trajectory_relpath": (
                        f"runs/{run_name}/{result_dir}/trajectory.json"
                    ),
                    "run_log_relpath": (
                        f"runs/{run_name}/{result_dir}/run.log"
                    ),
                    "launcher_log_relpath": (
                        f"launcher_logs/{run_id}.log"
                    ),
                })
                wave_index += 1
    validate_schedule(rows, base_port)
    return rows


def validate_schedule(rows: list[dict], base_port: int) -> None:
    errors: list[str] = []
    if len(rows) != 10:
        errors.append(f"schedule has {len(rows)} runs, expected 10")
    if len({row.get("run_id") for row in rows}) != len(rows):
        errors.append("run IDs are not unique")
    if len({row.get("run_name") for row in rows}) != len(rows):
        errors.append("run names are not unique")
    expected = {
        (wave, model_key, scenario)
        for wave, assignments in WAVE_ASSIGNMENTS.items()
        for model_key, scenarios in assignments.items()
        for scenario in scenarios
    }
    actual = {
        (row.get("wave"), row.get("model_key"), row.get("scenario"))
        for row in rows
    }
    if actual != expected:
        errors.append("wave/model/scenario assignments differ from preregistration")
    for wave in (1, 2):
        wave_rows = [row for row in rows if row.get("wave") == wave]
        if len(wave_rows) != 5:
            errors.append(f"wave {wave} does not contain exactly five runs")
        if sorted(row.get("wave_index") for row in wave_rows) != list(range(5)):
            errors.append(f"wave {wave} indices are not exactly 0..4")
        if sorted(row.get("port") for row in wave_rows) != list(
            range(base_port, base_port + 5)
        ):
            errors.append(f"wave {wave} ports are not the frozen five-port band")
    for model_key, model in MODEL_SPECS.items():
        model_rows = [row for row in rows if row.get("model_key") == model_key]
        if (
            len(model_rows) != 5
            or {row.get("scenario") for row in model_rows} != set(SCENARIOS)
            or any(row.get("region_order") != list(model["regions"])
                   for row in model_rows)
        ):
            errors.append(f"{model_key} schedule/model routing is malformed")
    if any(
        row.get("variant") != VARIANT
        or row.get("condition") != CONDITION
        or row.get("scaffold") != SCAFFOLD
        or row.get("arm") != "deliberative"
        for row in rows
    ):
        errors.append("one or more treatment fields drifted")
    if errors:
        raise SystemExit("invalid cross-model schedule:\n  " + "\n  ".join(errors))


def _v18_binding(v18_dir: Path) -> tuple[dict, dict, dict]:
    v18_dir = v18_dir.resolve()
    v18 = verify_v18_campaign(v18_dir, quiet=True)
    try:
        smoke = verify_v18_smoke_gate(v18_dir, v18)
    except ValueError as exc:
        raise SystemExit(f"V18 smoke gate is invalid: {exc}") from exc
    manifest_path = v18_dir / "campaign_manifest.json"
    return v18, _file_ref(manifest_path), _file_ref(Path(smoke["path"]))


def _assert_v18_runtime(v18: dict) -> None:
    _validate_caps(CAPS)
    sources = code_inventory()
    source_sha = _sha_bytes(_json_bytes(sources))
    if (
        sources != v18.get("source_inventory")
        or source_sha != v18.get("source_inventory_sha256")
    ):
        raise SystemExit("current measured source differs from frozen V18")
    runtime = runtime_dependency_manifest()
    contract = runtime_limit_contract(
        near_fraction=LIMIT_NEAR_FRACTION
    )
    if (
        CAPS != v18.get("caps")
        or runtime != v18.get("runtime_dependencies")
        or contract != v18.get("limit_contract")
    ):
        raise SystemExit("current runtime/caps/limit contract differs from V18")
    harness = sources.get("agentarena/scaffolds/browseruse_deliberative.py")
    if (
        not isinstance(harness, dict)
        or harness.get("sha256")
        != "6336e052bd94617dabea9ea0a7fb4b3c23923728b90f09abab4040d5146ad3ed"
    ):
        raise SystemExit("frozen deliberative scaffold hash is unexpected")
    for scenario in SCENARIOS:
        actual = _tree_inventory(ROOT / "benchmark_data/amazon" / scenario)
        expected = v18.get("artifacts", {}).get(scenario, {}).get("files")
        if actual != expected:
            raise SystemExit(f"{scenario} artifacts differ from frozen V18")


def prepare(
    campaign_dir: Path,
    campaign_id: str,
    v18_dir: Path,
    base_port: int | None,
) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest_path = campaign_dir / "campaign_manifest.json"
    if manifest_path.exists():
        verify(campaign_dir)
        print("campaign already frozen; nothing was replaced")
        return
    if campaign_dir.exists() and any(campaign_dir.iterdir()):
        raise SystemExit(
            "campaign directory is nonempty without a frozen manifest; "
            "preserve it and choose a new directory"
        )
    campaign_id = _safe_id(campaign_id)
    base_port = select_free_band() if base_port is None else base_port
    _validate_port_band(base_port, require_free=True)
    prereg = _preregistration()
    v18, v18_ref, smoke_ref = _v18_binding(v18_dir)
    _assert_v18_runtime(v18)
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
        "design": prereg["design"],
        "metrics": prereg["metrics"],
        "interpretation": prereg["interpretation"],
        "historical_context": prereg["historical_context"],
        "v18_manifest": v18_ref,
        "v18_smoke_gate": smoke_ref,
        "measured_source_inventory": v18["source_inventory"],
        "measured_source_inventory_sha256":
            v18["source_inventory_sha256"],
        "deliberative_source":
            v18["source_inventory"][
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
            "predecessor_blocks": STAGE_PREDECESSOR_BLOCKS,
            "scheduled_regions": {
                model["logical"]: list(model["regions"])
                for model in MODEL_SPECS.values()
            },
            "small_and_concurrency_required": True,
            "max_checkpoint_age_seconds": PROBE_MAX_AGE_SECONDS,
            "before_and_mid_model_checks": True,
            "unhealthy_action": "pause_never_kill_active_runs",
        },
        "launch_policy": {
            "runs_per_wave": 5,
            "spawn_stagger_seconds": CAPS["spawn_stagger_seconds"],
            "create_only_receipts": True,
            "create_only_launcher_logs": True,
            "never_mass_kill": True,
            "never_silently_relaunch": True,
            "behavioral_failures_are_never_refilled": True,
            "protected_port_range": [
                PROTECTED_PORT_LOW, PROTECTED_PORT_HIGH
            ],
        },
        "result_discovery": {
            "policy": "exact_manifest_paths_only",
            "expected_runs": 10,
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
        f"PREPARE PASS: {campaign_id}; 10 treatment runs; "
        f"ports {base_port}-{base_port + 4}"
    )


def verify(campaign_dir: Path, *, quiet: bool = False) -> dict:
    campaign_dir = campaign_dir.resolve()
    manifest_path = campaign_dir / "campaign_manifest.json"
    sidecar_path = campaign_dir / "campaign_manifest.sha256.json"
    if not manifest_path.is_file() or not sidecar_path.is_file():
        raise SystemExit("campaign is not prepared")
    if _read_json(sidecar_path) != {
        "path": manifest_path.name,
        "sha256": _sha_file(manifest_path),
    }:
        raise SystemExit("campaign manifest hash mismatch")
    manifest = _read_json(manifest_path)
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("kind") != KIND
    ):
        raise SystemExit("campaign kind/schema mismatch")
    if manifest.get("campaign_source") != _file_ref(Path(__file__)):
        raise SystemExit("campaign launcher source drifted")
    if manifest.get("preregistration") != _file_ref(PREREG_PATH):
        raise SystemExit("preregistration drifted")
    prereg = _preregistration()
    if (
        manifest.get("design") != prereg["design"]
        or manifest.get("metrics") != prereg["metrics"]
        or manifest.get("interpretation") != prereg["interpretation"]
        or manifest.get("historical_context") != prereg["historical_context"]
    ):
        raise SystemExit("manifest differs from preregistration")
    v18_path = Path(manifest["v18_manifest"]["path"])
    v18, v18_ref, smoke_ref = _v18_binding(v18_path.parent)
    if (
        manifest.get("v18_manifest") != v18_ref
        or manifest.get("v18_smoke_gate") != smoke_ref
    ):
        raise SystemExit("V18 manifest/smoke binding drifted")
    _assert_v18_runtime(v18)
    if (
        manifest.get("measured_source_inventory")
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
    expected_schedule = build_schedule(
        manifest["campaign_id"], int(manifest["base_port"])
    )
    if manifest.get("schedule") != expected_schedule:
        raise SystemExit("schedule differs from frozen campaign source")
    expected_artifacts = {
        scenario: {
            "files_sha256": _sha_bytes(_json_bytes(
                v18["artifacts"][scenario]["files"]
            )),
            "file_count": len(v18["artifacts"][scenario]["files"]),
        }
        for scenario in SCENARIOS
    }
    if manifest.get("artifacts") != expected_artifacts:
        raise SystemExit("artifact binding drifted")
    expected_probe = {
        "stages": STAGE_BLOCKS,
        "predecessor_blocks": STAGE_PREDECESSOR_BLOCKS,
        "scheduled_regions": {
            model["logical"]: list(model["regions"])
            for model in MODEL_SPECS.values()
        },
        "small_and_concurrency_required": True,
        "max_checkpoint_age_seconds": PROBE_MAX_AGE_SECONDS,
        "before_and_mid_model_checks": True,
        "unhealthy_action": "pause_never_kill_active_runs",
    }
    if manifest.get("probe_policy") != expected_probe:
        raise SystemExit("probe policy drifted")
    expected_launch = {
        "runs_per_wave": 5,
        "spawn_stagger_seconds": CAPS["spawn_stagger_seconds"],
        "create_only_receipts": True,
        "create_only_launcher_logs": True,
        "never_mass_kill": True,
        "never_silently_relaunch": True,
        "behavioral_failures_are_never_refilled": True,
        "protected_port_range": [
            PROTECTED_PORT_LOW, PROTECTED_PORT_HIGH
        ],
    }
    if (
        manifest.get("port_count") != PORT_COUNT
        or manifest.get("launch_policy") != expected_launch
        or manifest.get("result_discovery") != {
            "policy": "exact_manifest_paths_only",
            "expected_runs": 10,
            "expected_summaries": [
                row["summary_relpath"] for row in expected_schedule
            ],
        }
    ):
        raise SystemExit("launch/result-discovery policy drifted")
    if not quiet:
        print(
            "VERIFY PASS: preregistration, exact schedule, frozen "
            "deliberative source, V18 runtime/caps/smoke, and original-five "
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
            {row["logical_model"]: row["region_order"]},
            separators=(",", ":"),
        )
    if "STOREFRONT_OPS_TOKEN" in env:
        raise SystemExit("STOREFRONT_OPS_TOKEN survived environment sanitization")
    return env


def _predecessor_evidence(
    campaign_dir: Path, manifest: dict, stage: str
) -> list[dict]:
    blocks = STAGE_PREDECESSOR_BLOCKS[stage]
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
                campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
            ),
            "launch_receipt_hash": (
                campaign_dir / "launch_receipts"
                / f"{row['run_id']}.sha256.json"
            ),
        }
        missing = [
            label for label, path in {
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
                label: {
                    "path": str(path.relative_to(campaign_dir)),
                    "sha256": _sha_file(path),
                    "size": path.stat().st_size,
                }
                for label, path in immutable.items()
            },
        })
    expected = sum(
        1 for row in manifest["schedule"] if row["block"] in blocks
    )
    if len(evidence) != expected:
        raise SystemExit(
            f"{stage} predecessor evidence is incomplete: "
            f"{len(evidence)} != {expected}"
        )
    return evidence


def run_probe(campaign_dir: Path, stage: str, label: str) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify(campaign_dir, quiet=True)
    if stage not in STAGE_BLOCKS:
        raise SystemExit(f"unknown probe stage: {stage}")
    label = _safe_id(label)
    checkpoint = campaign_dir / "probes" / f"checkpoint_{label}.json"
    if checkpoint.exists():
        raise SystemExit(f"refusing to replace checkpoint: {checkpoint}")
    predecessor = _predecessor_evidence(campaign_dir, manifest, stage)
    blocks = STAGE_BLOCKS[stage]
    logicals = {
        row["logical_model"]
        for row in manifest["schedule"] if row["block"] in blocks
    }
    if len(logicals) != 1:
        raise SystemExit("probe stage does not resolve to one model")
    logical = next(iter(logicals))
    regions = manifest["probe_policy"]["scheduled_regions"][logical]
    audit_refill_coexistence(int(manifest["base_port"]))
    env = _apply_environment(manifest)
    env["TRAPI_REGIONS_OVERRIDE"] = json.dumps(
        {logical: regions}, separators=(",", ":")
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
            sys.executable, str(SCRIPT_DIR / "probe_regions.py"), logical
        ],
        "concurrency": [
            sys.executable, str(SCRIPT_DIR / "probe_concurrency.py"), logical
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
                "logs were preserved"
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
    campaign_dir: Path,
    manifest: dict,
    stage: str,
    label: str,
) -> dict:
    label = _safe_id(label)
    path = campaign_dir / "probes" / f"checkpoint_{label}.json"
    if not path.is_file():
        raise SystemExit(f"missing checkpoint: {path}")
    record = _read_json(path)
    if (
        record.get("label") != label
        or record.get("stage") != stage
        or record.get("blocks") != STAGE_BLOCKS[stage]
        or record.get("manifest_sha256")
        != _sha_file(campaign_dir / "campaign_manifest.json")
    ):
        raise SystemExit(f"checkpoint identity/binding drifted: {label}")
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
            f"checkpoint {label} is outside its one-hour launch window "
            f"(age={age:.1f}s)"
        )
    expected_predecessor = _predecessor_evidence(
        campaign_dir, manifest, stage
    )
    if record.get("predecessor_evidence") != expected_predecessor:
        raise SystemExit(f"checkpoint predecessor evidence drifted: {label}")
    logs = record.get("logs")
    if not isinstance(logs, dict) or set(logs) != {"small", "concurrency"}:
        raise SystemExit(f"checkpoint log inventory is malformed: {label}")
    resolved = {}
    for name, ref in logs.items():
        candidate = (campaign_dir / ref["path"]).resolve()
        if campaign_dir not in candidate.parents or not candidate.is_file():
            raise SystemExit(f"checkpoint log is absent/unsafe: {name}")
        if (
            ref.get("sha256") != _sha_file(candidate)
            or ref.get("size") != candidate.stat().st_size
        ):
            raise SystemExit(f"checkpoint log drifted: {name}")
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
        "record": record,
    }


def _verify_receipt(
    campaign_dir: Path, manifest: dict, row: dict
) -> dict:
    path = campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
    sidecar = path.with_suffix(".sha256.json")
    if not path.is_file() or not sidecar.is_file():
        raise ValueError(f"launch receipt is absent for {row['run_id']}")
    if _read_json(sidecar) != {
        "path": path.name, "sha256": _sha_file(path)
    }:
        raise ValueError(f"launch receipt hash drifted for {row['run_id']}")
    record = _read_json(path)
    expected_override = json.dumps(
        {row["logical_model"]: row["region_order"]},
        separators=(",", ":"),
    )
    if (
        record.get("run_id") != row["run_id"]
        or record.get("row") != row
        or record.get("manifest_sha256")
        != _sha_file(campaign_dir / "campaign_manifest.json")
        or record.get("trapi_regions_override") != expected_override
        or record.get("measured_source_inventory_sha256")
        != manifest["measured_source_inventory_sha256"]
        or record.get("certification_sha256")
        != manifest["certification_sha256"]
        or record.get("caps") != manifest["caps"]
    ):
        raise ValueError(f"launch receipt binding drifted for {row['run_id']}")
    checkpoint_relative = record.get("checkpoint_path")
    if not isinstance(checkpoint_relative, str) or not checkpoint_relative:
        raise ValueError(
            f"launch checkpoint path is malformed for {row['run_id']}"
        )
    checkpoint = (campaign_dir / checkpoint_relative).resolve()
    if (
        campaign_dir not in checkpoint.parents
        or not checkpoint.is_file()
        or record.get("checkpoint_sha256") != _sha_file(checkpoint)
    ):
        raise ValueError(f"launch checkpoint drifted for {row['run_id']}")
    return {
        "path": str(path.relative_to(campaign_dir)),
        "sha256": _sha_file(path),
        "record": record,
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
        {row["logical_model"]: row["region_order"]},
        separators=(",", ":"),
    )
    if env.get("TRAPI_REGIONS_OVERRIDE") != expected_override:
        raise SystemExit("per-run route override differs from schedule")
    record = {
        "run_id": row["run_id"],
        "row": row,
        "launched_at_utc": _utcnow(),
        "manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "campaign_source_sha256": manifest["campaign_source"]["sha256"],
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


def launch_wave(
    campaign_dir: Path,
    wave: int,
    qwen_checkpoint: str,
    kimi_checkpoint: str,
) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify(campaign_dir, quiet=True)
    if wave not in (1, 2):
        raise SystemExit("wave must be 1 or 2")
    rows = sorted(
        (row for row in manifest["schedule"] if row["wave"] == wave),
        key=lambda row: row["wave_index"],
    )
    if len(rows) != 5:
        raise SystemExit("wave does not contain exactly five runs")
    stages = {
        "qwen": "qwen_before" if wave == 1 else "qwen_mid",
        "kimi": "kimi_before" if wave == 1 else "kimi_mid",
    }
    labels = {"qwen": qwen_checkpoint, "kimi": kimi_checkpoint}
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
                    f"completed run has incomplete immutable evidence: "
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
                f"partial evidence exists for {row['run_id']}; "
                "never silently relaunch"
            )
        pending.append(row)
    if not pending:
        print(f"WAVE {wave}: already complete")
        return
    checkpoints = {
        key: _verify_checkpoint(
            campaign_dir, manifest, stages[key], labels[key]
        )
        for key in ("qwen", "kimi")
    }
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
                campaign_dir,
                manifest,
                row,
                checkpoints[row["model_key"]],
                command,
                env,
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
            "wave ended with preserved evidence; no process was killed or "
            f"relaunched: interrupted={interrupted}, failures={failures}, "
            f"missing={missing}, unlaunched={unlaunched}"
        )
    print(f"WAVE PASS: {wave} ({len(processes)} fresh runs)")


def _fresh_rescore(campaign_dir: Path, manifest: dict) -> dict:
    from agentarena.scoring.rescore import write_strict

    started = _utcnow()
    updated = 0
    for row in manifest["schedule"]:
        updated += write_strict(str(campaign_dir / row["experiment_relpath"]))
    required = {
        "preservation_strict",
        "preservation_cont",
        "strict_binary",
        "resistance_margin",
    }
    outputs = []
    for row in manifest["schedule"]:
        summary_path = campaign_dir / row["summary_relpath"]
        trajectory_path = campaign_dir / row["trajectory_relpath"]
        summary = _read_json(summary_path)
        outputs.append({
            "run_id": row["run_id"],
            "summary_sha256": _sha_file(summary_path),
            "trajectory_sha256": _sha_file(trajectory_path),
            "strict_keys_present": sorted(required.intersection(summary)),
            "strict_values": {
                key: summary.get(key) for key in sorted(required)
            },
        })
    scorer = ROOT / "agentarena/scoring/rescore.py"
    complete = (
        updated == 10
        and len(outputs) == 10
        and all(set(item["strict_keys_present"]) == required
                for item in outputs)
    )
    return {
        "kind": "fresh_strict_rescore_audit",
        "performed": True,
        "started_at_utc": started,
        "completed_at_utc": _utcnow(),
        "scorer": {
            "path": str(scorer.relative_to(ROOT)),
            "sha256": _sha_file(scorer),
        },
        "expected_summary_count": 10,
        "updated_summary_count": updated,
        "fresh_complete": complete,
        "outputs": outputs,
    }


def _number(value: object) -> float | None:
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    ):
        return float(value)
    return None


def _run_record(
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
            raise SystemExit(f"measured artifact is absent: {path}")
    summary = _read_json(summary_path)
    trajectory = _read_json(trajectory_path)
    receipt = _verify_receipt(campaign_dir, manifest, row)
    errors = []
    expected = {
        "env": "amazon",
        "scaffold": SCAFFOLD,
        "model": row["model_recorded"],
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
    behavioral_no_order = (
        not chosen or outcome in {"none", "error", "skipped", None}
    )
    pstar = _number(summary.get("preservation_strict"))
    binary = _number(summary.get("strict_binary"))
    if behavioral_no_order and pstar is None:
        pstar = 0.0
    if behavioral_no_order and binary is None:
        binary = 0.0
    if pstar is None or not 0 <= pstar <= 1:
        errors.append("preservation_strict is absent/outside [0,1]")
        pstar = 0.0
    if binary not in (0.0, 1.0):
        errors.append("strict_binary is absent/not binary")
        binary = 0.0
    steps_raw = summary.get("num_steps")
    seconds_raw = summary.get("seconds")
    steps = (
        int(steps_raw)
        if isinstance(steps_raw, int) and not isinstance(steps_raw, bool)
        and steps_raw >= 0 else 0
    )
    seconds = _number(seconds_raw)
    if seconds is None or seconds < 0:
        errors.append("seconds is absent/invalid")
        seconds = 0.0
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
    limit_errors = validate_limit_audit(
        limit_audit, manifest["limit_contract"], "deliberative"
    )
    errors.extend(f"limit audit: {item}" for item in limit_errors)
    categories = (
        limit_audit.get("categories", {})
        if isinstance(limit_audit, dict) else {}
    )
    safety_records = categories.get("safety_backstops", {})
    safety_touches = {
        name: record.get("touched_count")
        for name, record in safety_records.items()
        if isinstance(record, dict) and record.get("touched_count")
    }
    context_audit = stats.get("context_cap_audit")
    if (
        not isinstance(context_audit, dict)
        or context_audit.get("complete") is not True
    ):
        errors.append("context-cap audit is absent/incomplete")
    context_limits = (
        context_audit.get("limits", {})
        if isinstance(context_audit, dict) else {}
    )
    context_touches = {
        name: record.get("touched_count")
        for name, record in context_limits.items()
        if isinstance(record, dict) and record.get("touched_count")
    }
    fixed_records = categories.get("fixed_architecture", {})
    fallback_record = (
        fixed_records.get("fallback_llm_depth", {})
        if isinstance(fixed_records, dict) else {}
    )
    fallback_used = bool(
        isinstance(fallback_record, dict)
        and fallback_record.get("touched_count")
    )
    log_text = (
        run_log_path.read_text(errors="replace")
        + "\n"
        + launcher_path.read_text(errors="replace")
    )
    bound_markers = [
        marker for marker in BOUND_LOG_MARKERS if marker in log_text
    ]
    role = (
        "hero" if binary == 1.0
        else "no_order" if not chosen
        else "nonhero"
    )
    return {
        "run_id": row["run_id"],
        "wave": row["wave"],
        "model": row["model_recorded"],
        "scenario": row["scenario"],
        "chosen": chosen,
        "chosen_label": summary.get("chosen_label"),
        "role": role,
        "outcome": outcome,
        "preservation_strict": pstar,
        "strict_binary": binary,
        "steps": steps,
        "seconds": seconds,
        "contract_compile_calls":
            diagnostics.get("contract_compile_calls"),
        "contract_compile_attempts":
            diagnostics.get("contract_compile_attempts"),
        "contract_compile_failures":
            diagnostics.get("contract_compile_failures"),
        "decision_checkpoint_calls":
            diagnostics.get("decision_checkpoint_calls"),
        "decision_checkpoint_approvals":
            diagnostics.get("decision_checkpoint_approvals"),
        "approved_candidate_id":
            diagnostics.get("approved_candidate_id"),
        "safety_backstop_touches": safety_touches,
        "lossy_context_cap_touches": context_touches,
        "same_model_route_fallback_used": fallback_used,
        "bound_log_markers": bound_markers,
        "validation_errors": errors,
        "launch_receipt": {
            "path": receipt["path"],
            "sha256": receipt["sha256"],
        },
        "artifacts": {
            "summary": {
                "path": row["summary_relpath"],
                "sha256": _sha_file(summary_path),
            },
            "trajectory": {
                "path": row["trajectory_relpath"],
                "sha256": _sha_file(trajectory_path),
            },
            "run_log": {
                "path": row["run_log_relpath"],
                "sha256": _sha_file(run_log_path),
            },
        },
    }


def _mean(rows: list[dict], key: str) -> float:
    return sum(float(row[key]) for row in rows) / len(rows)


def _aggregate(rows: list[dict]) -> dict:
    return {
        "runs": len(rows),
        "mean_preservation_strict": _mean(rows, "preservation_strict"),
        "strict_successes": int(sum(row["strict_binary"] for row in rows)),
        "strict_rate": _mean(rows, "strict_binary"),
        "orders": sum(bool(row["chosen"]) for row in rows),
        "no_orders": sum(not bool(row["chosen"]) for row in rows),
        "max_steps": max(row["steps"] for row in rows),
        "max_seconds": max(row["seconds"] for row in rows),
    }


def _report_markdown(report_value: dict) -> str:
    lines = [
        "# Deliberative harness: cross-model easy evaluation",
        "",
        "Headline: `preservation_strict` (P*=G·O). "
        "Secondary: `strict_binary`.",
        "",
        "| Model | Runs | Mean P* | Heroes | Orders |",
        "|---|---:|---:|---:|---:|",
    ]
    for model in ("Qwen3.5-122B", "Kimi-K2.6"):
        item = report_value["aggregates"]["by_model"][model]
        lines.append(
            f"| {model} | {item['runs']} | "
            f"{item['mean_preservation_strict']:.3f} | "
            f"{item['strict_successes']}/{item['runs']} | "
            f"{item['orders']}/{item['runs']} |"
        )
    pooled = report_value["aggregates"]["pooled"]
    lines.extend([
        "",
        f"Pooled: P*={pooled['mean_preservation_strict']:.3f}, "
        f"strict={pooled['strict_successes']}/{pooled['runs']}.",
        "",
        "| Model | Scenario | Choice role | P* | Strict | Steps | Seconds |",
        "|---|---|---|---:|---:|---:|---:|",
    ])
    for row in report_value["runs"]:
        lines.append(
            f"| {row['model']} | {row['scenario']} | {row['role']} | "
            f"{row['preservation_strict']:.3f} | "
            f"{int(row['strict_binary'])} | {row['steps']} | "
            f"{row['seconds']:.1f} |"
        )
    validity = report_value["validity"]
    lines.extend([
        "",
        "## Validity",
        "",
        f"- Complete fixed denominator: "
        f"{'yes' if validity['complete_fixed_denominator'] else 'no'}",
        f"- Fresh strict rescore: "
        f"{'yes' if validity['fresh_strict_rescore'] else 'no'}",
        f"- Safety backstop exposed: "
        f"{'yes' if validity['safety_backstop_exposed'] else 'no'}",
        f"- Lossy context cap exposed: "
        f"{'yes' if validity['lossy_context_cap_exposed'] else 'no'}",
        "",
        "Historical browser-use values are descriptive headroom only, not "
        "a contemporaneous causal control.",
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
            "cannot report an incomplete fixed denominator: "
            + ", ".join(missing)
        )
    rescore = _fresh_rescore(campaign_dir, manifest)
    rows = [
        _run_record(campaign_dir, manifest, row)
        for row in manifest["schedule"]
    ]
    by_model = {
        model["recorded"]: _aggregate([
            row for row in rows if row["model"] == model["recorded"]
        ])
        for model in MODEL_SPECS.values()
    }
    historical = manifest["historical_context"]["models"]
    for model, item in by_model.items():
        item["historical_context"] = historical[model]
        item["descriptive_difference_from_historical_mean"] = (
            item["mean_preservation_strict"]
            - historical[model]["mean_preservation_strict"]
        )
        item["difference_is_causal"] = False
    safety_exposed = any(
        row["safety_backstop_touches"] or row["bound_log_markers"]
        for row in rows
    )
    context_exposed = any(row["lossy_context_cap_touches"] for row in rows)
    validation_errors = {
        row["run_id"]: row["validation_errors"]
        for row in rows if row["validation_errors"]
    }
    report_value = {
        "schema_version": 1,
        "kind": REPORT_KIND,
        "reported_at_utc": _utcnow(),
        "campaign_manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "headline": "preservation_strict",
        "formula": "P*=G*O",
        "secondary": "strict_binary",
        "all_scheduled_runs_in_denominator": True,
        "rescore_audit": rescore,
        "aggregates": {
            "by_model": by_model,
            "pooled": _aggregate(rows),
        },
        "historical_context_policy":
            manifest["historical_context"]["role"],
        "validity": {
            "complete_fixed_denominator": len(rows) == 10,
            "fresh_strict_rescore": rescore["fresh_complete"] is True,
            "source_runtime_artifacts_v18_bound": True,
            "receipts_verified": True,
            "run_validation_errors": validation_errors,
            "safety_backstop_exposed": safety_exposed,
            "lossy_context_cap_exposed": context_exposed,
            "all_invariants_green": (
                len(rows) == 10
                and rescore["fresh_complete"] is True
                and not validation_errors
                and not safety_exposed
                and not context_exposed
            ),
        },
        "runs": rows,
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
    raise SystemExit("no create-only report number remains")


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
            if row["run_id"] not in complete
            and (
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
    checkpoints = sorted(
        path.name for path in (campaign_dir / "probes").glob(
            "checkpoint_*.json"
        )
    ) if (campaign_dir / "probes").is_dir() else []
    reports = sorted(
        path.name for path in (campaign_dir / "reports").glob(
            "report_*.json"
        )
    ) if (campaign_dir / "reports").is_dir() else []
    print(json.dumps({
        "campaign_id": manifest["campaign_id"],
        "expected_runs": 10,
        "completed_runs": sum(item["complete"] for item in waves.values()),
        "waves": waves,
        "probe_checkpoints": checkpoints,
        "reports": reports,
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
    prepare_parser.add_argument("--v18-dir", type=Path, default=V18_DEFAULT)
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
    launch_parser.add_argument("--qwen-checkpoint", required=True)
    launch_parser.add_argument("--kimi-checkpoint", required=True)

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
        launch_wave(
            args.campaign_dir,
            args.wave,
            args.qwen_checkpoint,
            args.kimi_checkpoint,
        )
    elif args.command == "status":
        status(args.campaign_dir)
    elif args.command == "report":
        report(args.campaign_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
