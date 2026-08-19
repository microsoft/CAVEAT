#!/usr/bin/env python
"""Frozen 120-run mixed mode-evidence ablation campaign.

This analysis-only launcher leaves production tasks, catalogs, and scaffolds
untouched.  It binds V19's runtime/cap contract, uses the command-local harness
components package, injects objective-order text from a hash-bound sidecar, and
preserves every launch artifact create-only.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRIPT_DIR = ROOT / "scripts"
COMPONENT_DIR = ROOT / "ablations" / "harness_components"
PROMPT_ONLY_SOURCE = ROOT / "ablations" / "prompt_only" / "prompt_only_scaffold.py"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from hard_campaign_runtime import (  # noqa: E402
    CAPS,
    _runtime_environment_policy,
    _validate_caps,
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
KIND = "mode_evidence_ablation_campaign"
REPORT_KIND = "mode_evidence_ablation_report"
DEFAULT_CAMPAIGN = ROOT / "results" / "mode_evidence_ablation_v1"
V19_MANIFEST = (
    ROOT
    / "results"
    / "harness_deliberative_ab_confirmatory_v19"
    / "campaign_manifest.json"
)
PREREG_PATH = HERE / "preregistration.json"
SIDECAR_PATH = HERE / "objective_order_sidecar.json"
PROTOCOL_FREEZE_PATH = ROOT / "analysis" / "mode_evidence" / "FROZEN_V1.json"
CODEBOOK_PATH = ROOT / "analysis" / "mode_evidence" / "sensitizing_codebook.json"
CODEBOOK_SHA256 = "d32b8812e276bece5c9d0e53e05437a55bcc8aff135fd1ae8b577fa712a8ad34"
PROTOCOL_FREEZE_SHA256 = "e7398c84dc20724537502922767df01af5a1dc110da068513777da0209ff4975"

VARIANT = "graded"
HARD_SCENARIOS = (
    "laptop_hard",
    "office_chair_hard",
    "mattress_hard",
    "backpack_hard",
    "tent_hard",
)
FRONTIER_SCENARIOS = ("laptop_hard", "tent_hard")
STEERING_CONDITIONS = ("clean", "sponsored", "ranking", "addon", "drip")
FROZEN_SCENARIOS = (*HARD_SCENARIOS, "laptop")
REGIONS = (
    "gcr/shared",
    "msraif/shared",
    "redmond/interactive",
)
SOL_REQUEST = "gpt-5.6-sol#high"
SOL_RECORDED = "gpt-5.6-sol-high"
SOL_LOGICAL = "gpt-5.6-sol"
TERRA_REQUEST = "gpt-5.6-terra#low"
TERRA_RECORDED = "gpt-5.6-terra-low"
TERRA_LOGICAL = "gpt-5.6-terra"
SCAFFOLDS = (
    "browseruse",
    "browseruse-prompt-only",
    "browseruse-deliberative-no-coverage",
    "browseruse-deliberative",
)
SCHEDULE_SEED = "mode-evidence-v1-20260805"
LIMIT_NEAR_FRACTION = 0.25
PROBE_MAX_AGE_SECONDS = 3600
# V19's global browser backstop is 15.  This campaign may fill that already
# frozen machine ceiling, but never exceed it; the all-three-region concurrency
# probe must independently prove each block's primary/failover load.
MAX_PARALLEL_RUNS = 15
MAX_BLOCK_SIZE = 30
LAUNCH_CONFIRMATION = "LAUNCH-FROZEN-MODE-EVIDENCE-V1"
CAMPAIGN_SOURCE_NAMES = (
    "campaign.py",
    "objective_order_sidecar.json",
    "preregistration.json",
)


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
    return {
        str(path.relative_to(root)): {
            "sha256": _sha_file(path),
            "size": path.stat().st_size,
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def _inventory_sha(value: dict) -> str:
    return _sha_bytes(_json_bytes(value))


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise SystemExit(f"unsafe identifier: {value!r}")
    return value


def _deterministic_order(values, namespace: str) -> list:
    return sorted(
        values,
        key=lambda value: hashlib.sha256(
            f"{SCHEDULE_SEED}\0{namespace}\0{value}".encode()
        ).digest(),
    )


def _region_order(regions: tuple[str, ...], primary: str) -> list[str]:
    index = regions.index(primary)
    return [regions[(index + offset) % len(regions)] for offset in range(len(regions))]


def _validate_regions(regions, label: str) -> tuple[str, ...]:
    value = tuple(regions)
    if len(value) != 3 or set(value) != set(REGIONS):
        raise SystemExit(f"{label} must explicitly order all three TRAPI regions")
    if len(value) != len(set(value)):
        raise SystemExit(f"{label} contains duplicate regions")
    return value


def _row(
    *,
    campaign_id: str,
    base_port: int,
    block: int,
    spawn_index: int,
    study: str,
    repeat: int,
    scenario: str,
    condition: str,
    arm: str,
    scaffold: str,
    model_request: str,
    model_recorded: str,
    logical_model: str,
    objective_order: str,
    primary_region: str,
    region_order: list[str],
) -> dict:
    arm_id = arm.replace("browseruse-", "").replace("_", "-")
    run_id = f"b{block:02d}_r{repeat}_{scenario}_{arm_id}"
    run_name = f"{campaign_id}_{run_id}"
    result_dir = (
        f"amazon__{scaffold}__{model_recorded}__"
        f"{scenario}-{VARIANT}__{condition}"
    )
    return {
        "run_id": run_id,
        "block": block,
        "spawn_index": spawn_index,
        "study": study,
        "repeat": repeat,
        "scenario": scenario,
        "task_id": f"{scenario}-{VARIANT}",
        "variant": VARIANT,
        "condition": condition,
        "arm": arm,
        "scaffold": scaffold,
        "model_request": model_request,
        "model_recorded": model_recorded,
        "logical_model": logical_model,
        "objective_order": objective_order,
        "probe_stage": f"{'sol' if logical_model == SOL_LOGICAL else 'terra'}_b{block:02d}",
        "primary_region": primary_region,
        "region_order": region_order,
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
    sol_regions: tuple[str, ...] = REGIONS,
    terra_regions: tuple[str, ...] = REGIONS,
) -> list[dict]:
    """Return the exact preregistered 50 + 30 + 40 schedule."""

    campaign_id = _safe_id(campaign_id)
    sol_regions = _validate_regions(sol_regions, "Sol route")
    terra_regions = _validate_regions(terra_regions, "Terra route")
    rows: list[dict] = []

    block_spawns: Counter = Counter()

    # 50: five hard scenarios x original/reversed x five repetitions.  The
    # first three repeats form a 30-row block and the final two a 20-row block.
    for repeat in range(1, 6):
        block = 1 if repeat <= 3 else 2
        scenarios = _deterministic_order(HARD_SCENARIOS, f"order:{repeat}:scenarios")
        for pair_index, scenario in enumerate(scenarios):
            primary = sol_regions[(pair_index + repeat - 1) % 3]
            route = _region_order(sol_regions, primary)
            orders = _deterministic_order(("original", "reversed"), f"order:{repeat}:{scenario}")
            for objective_order in orders:
                spawn = block_spawns[block]
                rows.append(_row(
                    campaign_id=campaign_id,
                    base_port=base_port,
                    block=block,
                    spawn_index=spawn,
                    study="objective_order",
                    repeat=repeat,
                    scenario=scenario,
                    condition="combined",
                    arm=f"order_{objective_order}",
                    scaffold="browseruse",
                    model_request=SOL_REQUEST,
                    model_recorded=SOL_RECORDED,
                    logical_model=SOL_LOGICAL,
                    objective_order=objective_order,
                    primary_region=primary,
                    region_order=route,
                ))
                block_spawns[block] += 1

    # 30 additional: prompt-only / no-coverage / full on laptop and tent.
    frontier_arms = (
        ("prompt_only", "browseruse-prompt-only"),
        ("no_coverage", "browseruse-deliberative-no-coverage"),
        ("full", "browseruse-deliberative"),
    )
    for repeat in range(1, 6):
        block = 3
        scenarios = _deterministic_order(FRONTIER_SCENARIOS, f"frontier:{repeat}:scenarios")
        for scenario_index, scenario in enumerate(scenarios):
            primary = sol_regions[(scenario_index + repeat) % 3]
            route = _region_order(sol_regions, primary)
            arms = _deterministic_order(frontier_arms, f"frontier:{repeat}:{scenario}")
            for arm, scaffold in arms:
                spawn = block_spawns[block]
                rows.append(_row(
                    campaign_id=campaign_id,
                    base_port=base_port,
                    block=block,
                    spawn_index=spawn,
                    study="frontier_component",
                    repeat=repeat,
                    scenario=scenario,
                    condition="combined",
                    arm=arm,
                    scaffold=scaffold,
                    model_request=SOL_REQUEST,
                    model_recorded=SOL_RECORDED,
                    logical_model=SOL_LOGICAL,
                    objective_order="original",
                    primary_region=primary,
                    region_order=route,
                ))
                block_spawns[block] += 1

    # 40: standard laptop x five isolated conditions x eight repetitions.
    for repeat in range(1, 9):
        block = 4 if repeat <= 4 else 5
        conditions = _deterministic_order(STEERING_CONDITIONS, f"steering:{repeat}")
        for condition_index, condition in enumerate(conditions):
            primary = terra_regions[(condition_index + repeat - 1) % 3]
            route = _region_order(terra_regions, primary)
            spawn = block_spawns[block]
            rows.append(_row(
                campaign_id=campaign_id,
                base_port=base_port,
                block=block,
                spawn_index=spawn,
                study="isolated_steering",
                repeat=repeat,
                scenario="laptop",
                condition=condition,
                arm=condition,
                scaffold="browseruse",
                model_request=TERRA_REQUEST,
                model_recorded=TERRA_RECORDED,
                logical_model=TERRA_LOGICAL,
                objective_order="not_applicable",
                primary_region=primary,
                region_order=route,
            ))
            block_spawns[block] += 1

    validate_schedule(rows, base_port, sol_regions=sol_regions, terra_regions=terra_regions)
    return rows


def validate_schedule(
    rows: list[dict],
    base_port: int,
    *,
    sol_regions: tuple[str, ...] = REGIONS,
    terra_regions: tuple[str, ...] = REGIONS,
) -> None:
    sol_regions = _validate_regions(sol_regions, "Sol route")
    terra_regions = _validate_regions(terra_regions, "Terra route")
    errors: list[str] = []
    if len(rows) != 120 or len({row.get("run_id") for row in rows}) != 120:
        errors.append("schedule is not 120 unique runs")
    if Counter(row.get("study") for row in rows) != {
        "objective_order": 50,
        "frontier_component": 30,
        "isolated_steering": 40,
    }:
        errors.append("study denominators differ from 50/30/40")
    if Counter(row["model_recorded"] for row in rows) != {
        SOL_RECORDED: 80,
        TERRA_RECORDED: 40,
    }:
        errors.append("model denominators differ from 80/40")
    expected_scaffolds = {
        "browseruse": 90,
        "browseruse-prompt-only": 10,
        "browseruse-deliberative-no-coverage": 10,
        "browseruse-deliberative": 10,
    }
    if Counter(row["scaffold"] for row in rows) != expected_scaffolds:
        errors.append("scaffold denominators differ")
    order_rows = [row for row in rows if row["study"] == "objective_order"]
    if Counter(row["objective_order"] for row in order_rows) != {"original": 25, "reversed": 25}:
        errors.append("objective-order arms are not 25/25")
    frontier_rows = [row for row in rows if row["study"] == "frontier_component"]
    if Counter(row["arm"] for row in frontier_rows) != {
        "prompt_only": 10, "no_coverage": 10, "full": 10
    }:
        errors.append("frontier arms are not 10/10/10")
    steering_rows = [row for row in rows if row["study"] == "isolated_steering"]
    if Counter(row["condition"] for row in steering_rows) != {name: 8 for name in STEERING_CONDITIONS}:
        errors.append("isolated steering conditions are not eight each")
    expected_block_sizes = {1: 30, 2: 20, 3: 30, 4: 20, 5: 20}
    for block, expected in expected_block_sizes.items():
        block_rows = [row for row in rows if row["block"] == block]
        if len(block_rows) != expected:
            errors.append(f"block {block} has {len(block_rows)} rows, expected {expected}")
            continue
        ports = {row["port"] for row in block_rows}
        if ports != set(range(base_port, base_port + expected)):
            errors.append(f"block {block} port allocation differs")
        if {row["spawn_index"] for row in block_rows} != set(range(expected)):
            errors.append(f"block {block} spawn indices differ")
        logicals = {row["logical_model"] for row in block_rows}
        if len(logicals) != 1:
            errors.append(f"block {block} spans models")
        expected_regions = sol_regions if block <= 3 else terra_regions
        if any(set(row["region_order"]) != set(expected_regions) for row in block_rows):
            errors.append(f"block {block} route inventory differs")
    for repeat in range(1, 6):
        for scenario in HARD_SCENARIOS:
            pair = [row for row in order_rows if row["repeat"] == repeat and row["scenario"] == scenario]
            if len(pair) != 2 or {row["objective_order"] for row in pair} != {"original", "reversed"}:
                errors.append(f"objective pair missing: {repeat}/{scenario}")
            elif any(pair[0][key] != pair[1][key] for key in ("condition", "scaffold", "model_request", "region_order", "primary_region")):
                errors.append(f"objective pair is not route/model matched: {repeat}/{scenario}")
        for scenario in FRONTIER_SCENARIOS:
            triple = [row for row in frontier_rows if row["repeat"] == repeat and row["scenario"] == scenario]
            if len(triple) != 3 or {row["arm"] for row in triple} != {"prompt_only", "no_coverage", "full"}:
                errors.append(f"frontier triple missing: {repeat}/{scenario}")
            elif any(triple[0][key] != row[key] for row in triple[1:] for key in ("condition", "model_request", "region_order", "primary_region")):
                errors.append(f"frontier triple is not route/model matched: {repeat}/{scenario}")
    if any(
        row["variant"] != VARIANT
        or row["scaffold"] not in SCAFFOLDS
        or row["task_id"] != f"{row['scenario']}-{VARIANT}"
        or not 1 <= row["port"] <= 65535
        for row in rows
    ):
        errors.append("one or more row identities are invalid")
    if errors:
        raise SystemExit("invalid 120-run schedule:\n  " + "\n  ".join(errors))


def _campaign_sources() -> dict:
    records = {}
    for name in CAMPAIGN_SOURCE_NAMES:
        path = HERE / name
        if not path.is_file():
            raise SystemExit(f"missing campaign source: {path}")
        records[name] = {"sha256": _sha_file(path), "size": path.stat().st_size}
    return records


def _component_sources() -> dict:
    records = {}
    for path in (
        COMPONENT_DIR / "no_coverage_scaffold.py",
        COMPONENT_DIR / "sitecustomize.py",
        PROMPT_ONLY_SOURCE,
    ):
        if not path.is_file():
            raise SystemExit(f"missing command-local component source: {path}")
        records[str(path.relative_to(ROOT))] = {
            "sha256": _sha_file(path), "size": path.stat().st_size
        }
    return records


def _verify_protocol_freeze() -> dict:
    if _sha_file(PROTOCOL_FREEZE_PATH) != PROTOCOL_FREEZE_SHA256:
        raise SystemExit("FROZEN_V1 protocol binding drifted")
    frozen = _read_json(PROTOCOL_FREEZE_PATH)
    if (
        frozen.get("kind") != "mode_evidence_protocol_freeze"
        or frozen.get("new_mode_evidence_ablation_outcomes_observed") is not False
        or frozen.get("files", {}).get("analysis/mode_evidence/sensitizing_codebook.json") != CODEBOOK_SHA256
        or _sha_file(CODEBOOK_PATH) != CODEBOOK_SHA256
    ):
        raise SystemExit("qualitative protocol/codebook freeze is invalid")
    return {"freeze": _file_ref(PROTOCOL_FREEZE_PATH), "codebook": _file_ref(CODEBOOK_PATH)}


def _validate_sidecar() -> dict:
    sidecar = _read_json(SIDECAR_PATH)
    if sidecar.get("kind") != "mode_evidence_objective_order_sidecar" or sidecar.get("variant") != VARIANT:
        raise SystemExit("objective-order sidecar kind/variant is invalid")
    entries = sidecar.get("entries")
    if not isinstance(entries, dict) or set(entries) != set(HARD_SCENARIOS):
        raise SystemExit("objective-order sidecar scenario inventory is not exact")
    for scenario, entry in entries.items():
        source = ROOT / entry.get("source_path", "")
        canonical = _read_json(source).get(VARIANT, {}).get("text")
        if canonical != entry.get("original"):
            raise SystemExit(f"{scenario}: sidecar original differs from canonical instruction")
        if entry.get("task_id") != f"{scenario}-{VARIANT}":
            raise SystemExit(f"{scenario}: sidecar task id differs")
        original_objectives = entry.get("objectives_original")
        reversed_objectives = entry.get("objectives_reversed")
        if (
            not isinstance(original_objectives, list)
            or len(original_objectives) != 2
            or reversed_objectives != list(reversed(original_objectives))
            or entry["original"].split("Among the options", 1)[0]
            != entry["reversed"].split("Among the options", 1)[0]
            or any(text not in entry["original"] for text in original_objectives)
            or any(text not in entry["reversed"] for text in reversed_objectives)
        ):
            raise SystemExit(f"{scenario}: reversal is not the declared objective-only swap")
    return _file_ref(SIDECAR_PATH)


def _component_registration_gate() -> dict:
    code = (
        "import json,agentarena.scaffolds;"
        "from agentarena.core.scaffold import SCAFFOLDS;"
        "import no_coverage_scaffold as n,prompt_only_scaffold as p;"
        "print(json.dumps({'names':SCAFFOLDS.names(),"
        "'no_coverage':n.COMPONENT_VERSION,'prompt':p.PROMPT_ONLY_SHA256},sort_keys=True))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(COMPONENT_DIR), str(ROOT)))
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=60,
    )
    if completed.returncode:
        raise SystemExit("command-local scaffold registration failed:\n" + completed.stdout + completed.stderr)
    record = json.loads(completed.stdout.splitlines()[-1])
    if not set(SCAFFOLDS).issubset(record.get("names", [])) or record.get("no_coverage") != "no-coverage-v1":
        raise SystemExit("command-local scaffold contract differs")
    return record


def static_verify() -> dict:
    prereg = _read_json(PREREG_PATH)
    if (
        prereg.get("kind") != "mode_evidence_ablation_preregistration"
        or prereg.get("frozen_before_new_outcomes") is not True
        or prereg.get("design", {}).get("total_runs") != 120
        or prereg.get("analysis_protocol", {}).get("codebook_sha256") != CODEBOOK_SHA256
    ):
        raise SystemExit("preregistration identity/design is invalid")
    protocol = _verify_protocol_freeze()
    sidecar = _validate_sidecar()
    registration = _component_registration_gate()
    _validate_caps(CAPS)
    v19 = _read_json(V19_MANIFEST)
    current_limit = runtime_limit_contract(near_fraction=LIMIT_NEAR_FRACTION)
    if v19.get("caps") != CAPS:
        raise SystemExit("runtime safety/backstop caps differ from V19")
    schedule = build_schedule("mode_evidence_static", 17000)
    return {
        "runs": len(schedule),
        "protocol": protocol,
        "sidecar": sidecar,
        "registration": registration,
        "v19_manifest": _file_ref(V19_MANIFEST),
    }


def _ports_free(ports) -> bool:
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


def _runtime_policy(runtime: dict, source_sha: str, cert_sha: str, limit_contract: dict) -> dict:
    base = _runtime_environment_policy(runtime)
    values = dict(base["set"])
    values.update({
        "AGENTARENA_LIMIT_CONTRACT_JSON": json.dumps(limit_contract, sort_keys=True, separators=(",", ":")),
        "AGENTARENA_RUNTIME_SOURCE_ATTESTATION": source_sha,
        "AGENTARENA_EVALUATION_INPUT_ATTESTATION": cert_sha,
    })
    payload = {
        **{key: value for key, value in base.items() if key != "sha256"},
        "set": dict(sorted(values.items())),
        "per_run_set": {
            "TRAPI_REGIONS_OVERRIDE": "schedule.region_order",
            "MODE_EVIDENCE_OBJECTIVE_ORDER": "schedule.objective_order",
            "MODE_EVIDENCE_RUN_ID": "schedule.run_id",
        },
        "command_local_pythonpath": [str(COMPONENT_DIR), str(ROOT)],
    }
    return {**payload, "sha256": _sha_bytes(_json_bytes(payload))}


def prepare_campaign(
    campaign_dir: Path,
    campaign_id: str,
    base_port: int,
    cert_report: Path,
    lockdiff_report: Path,
    sol_regions: tuple[str, ...],
    terra_regions: tuple[str, ...],
) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest_path = campaign_dir / "campaign_manifest.json"
    if manifest_path.exists():
        verify_campaign(campaign_dir)
        print("campaign already frozen; no file was replaced")
        return
    if campaign_dir.exists() and any(campaign_dir.iterdir()):
        raise SystemExit("partial campaign directory exists; preserve it and choose a new path")
    if not 1024 <= base_port <= 65526 or not _ports_free(range(base_port, base_port + MAX_BLOCK_SIZE)):
        raise SystemExit("ten-port campaign band is invalid or busy")
    static_verify()
    sol_regions = _validate_regions(sol_regions, "Sol route")
    terra_regions = _validate_regions(terra_regions, "Terra route")
    cert_ref = _validate_certification(cert_report.resolve())
    lockdiff = _validate_lockdiff(lockdiff_report.resolve())
    schedule = build_schedule(
        campaign_id, base_port, sol_regions=sol_regions, terra_regions=terra_regions
    )
    production_sources = code_inventory()
    production_source_sha = _inventory_sha(production_sources)
    campaign_sources = _campaign_sources()
    component_sources = _component_sources()
    runtime = runtime_dependency_manifest()
    limit_contract = runtime_limit_contract(near_fraction=LIMIT_NEAR_FRACTION)
    policy = _runtime_policy(runtime, production_source_sha, cert_ref["sha256"], limit_contract)

    frozen_root = campaign_dir / "frozen_inputs" / "benchmark_data" / "amazon"
    artifacts = {}
    for scenario in FROZEN_SCENARIOS:
        source = ROOT / "benchmark_data" / "amazon" / scenario
        inventory = _tree_inventory(source)
        artifacts[scenario] = {"files": inventory, "files_sha256": _inventory_sha(inventory)}
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

    stages = {row["probe_stage"]: [row["block"]] for row in schedule}
    predecessors = {stage: list(range(1, blocks[0])) for stage, blocks in stages.items()}
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "campaign_id": _safe_id(campaign_id),
        "frozen_at_utc": _utcnow(),
        "source_root": str(ROOT),
        "preregistration": _file_ref(PREREG_PATH),
        "analysis_protocol": _verify_protocol_freeze(),
        "objective_order_sidecar": _validate_sidecar(),
        "design": {
            "total_runs": 120,
            "objective_order_runs": 50,
            "frontier_component_additional_runs": 30,
            "isolated_steering_runs": 40,
            "blocks": 5,
            "max_block_size": MAX_BLOCK_SIZE,
            "max_parallel_runs": MAX_PARALLEL_RUNS,
            "schedule_seed": SCHEDULE_SEED,
            "new_outcomes_observed_before_freeze": False,
            "launch_concurrency_override": {
                "v19_launch_only_max_parallel_runs": 4,
                "campaign_max_parallel_runs": MAX_PARALLEL_RUNS,
                "reason": "prospective operational speed on an idle machine",
                "estimand_or_per_run_behavior_change": False,
                "per_run_v19_safety_context_time_and_step_caps_unchanged": True,
                "global_max_concurrent_browsers": CAPS["max_concurrent_browsers"],
                "global_machine_cap_respected": MAX_PARALLEL_RUNS
                <= CAPS["max_concurrent_browsers"],
                "spawn_stagger_seconds": CAPS["spawn_stagger_seconds"],
            },
        },
        "base_port": base_port,
        "schedule": schedule,
        "caps": CAPS,
        "limit_near_fraction": LIMIT_NEAR_FRACTION,
        "limit_contract": limit_contract,
        "runtime_dependencies": runtime,
        "runtime_environment_policy": policy,
        "v19_cap_reference": _file_ref(V19_MANIFEST),
        "v19_limit_contract_sha256": _read_json(V19_MANIFEST)["limit_contract"]["sha256"],
        "production_source_inventory": production_sources,
        "production_source_inventory_sha256": production_source_sha,
        "campaign_source_inventory": campaign_sources,
        "campaign_source_inventory_sha256": _inventory_sha(campaign_sources),
        "component_source_inventory": component_sources,
        "component_source_inventory_sha256": _inventory_sha(component_sources),
        "artifacts": artifacts,
        "frozen_artifact_root": "frozen_inputs/benchmark_data/amazon",
        "frozen_artifact_inventory": _tree_inventory(frozen_root),
        "certification": {
            "source": cert_ref,
            "frozen_path": "frozen_inputs/certification_report.json",
            "frozen_sha256": _sha_file(frozen_cert),
        },
        "lockdiff": {
            "source_validation": lockdiff,
            "frozen_before_path": "frozen_inputs/lockdiff/lockdiff_before.json",
            "frozen_after_path": "frozen_inputs/lockdiff/lockdiff_after.json",
            "frozen_before_sha256": _sha_file(frozen_before),
            "frozen_after_sha256": _sha_file(frozen_after),
        },
        "probe_policy": {
            "stages": stages,
            "predecessor_blocks": predecessors,
            "scheduled_regions": {
                SOL_LOGICAL: list(sol_regions), TERRA_LOGICAL: list(terra_regions)
            },
            "all_three_regions_required": True,
            "concurrency_probe": True,
            "sol_large_request_probe": True,
            "max_checkpoint_age_seconds": PROBE_MAX_AGE_SECONDS,
            "unhealthy_action": "pause_never_kill_active_runs",
        },
        "attempt_policy": {
            "create_only_receipts": True,
            "create_only_launcher_logs": True,
            "create_only_numbered_reports": True,
            "never_silently_relaunch": True,
            "behavioral_failures_remain_in_denominator": True,
            "missing_runs_fail_closed": True,
        },
        "metric_policy": {
            "headline": "preservation_strict",
            "formula": "P*=G*O",
            "strict_binary_secondary": True,
            "legacy_preservation": "diagnostic_only_never_substituted",
            "scheduled_denominator": 120,
        },
        "result_discovery": {
            "policy": "manifest_paths_only",
            "expected_runs": 120,
            "expected_summaries": [row["summary_relpath"] for row in schedule],
        },
    }
    _write_new(manifest_path, manifest)
    _write_new(
        campaign_dir / "campaign_manifest.sha256.json",
        {"path": manifest_path.name, "sha256": _sha_file(manifest_path)},
    )
    verify_campaign(campaign_dir)
    print(f"PREPARE PASS: {campaign_id}; frozen 120 runs in 5 blocks")


def _verify_inventory(root: Path, expected: dict, label: str) -> None:
    actual = _tree_inventory(root)
    if actual != expected:
        changed = sorted(key for key in set(actual) | set(expected) if actual.get(key) != expected.get(key))
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
    sol_regions = _validate_regions(
        manifest.get("probe_policy", {}).get("scheduled_regions", {}).get(SOL_LOGICAL, ()),
        "frozen Sol route",
    )
    terra_regions = _validate_regions(
        manifest.get("probe_policy", {}).get("scheduled_regions", {}).get(TERRA_LOGICAL, ()),
        "frozen Terra route",
    )
    expected_schedule = build_schedule(
        manifest["campaign_id"], int(manifest["base_port"]),
        sol_regions=sol_regions, terra_regions=terra_regions,
    )
    if manifest.get("schedule") != expected_schedule:
        raise SystemExit("schedule differs from frozen campaign code")
    if manifest.get("preregistration") != _file_ref(PREREG_PATH):
        raise SystemExit("preregistration drifted")
    if manifest.get("analysis_protocol") != _verify_protocol_freeze():
        raise SystemExit("analysis protocol binding drifted")
    if manifest.get("objective_order_sidecar") != _validate_sidecar():
        raise SystemExit("objective-order sidecar binding drifted")
    _component_registration_gate()
    _validate_caps(manifest.get("caps"))
    production_sources = code_inventory()
    if (
        manifest.get("production_source_inventory") != production_sources
        or manifest.get("production_source_inventory_sha256") != _inventory_sha(production_sources)
    ):
        raise SystemExit("production measured-source inventory drifted")
    campaign_sources = _campaign_sources()
    component_sources = _component_sources()
    if (
        manifest.get("campaign_source_inventory") != campaign_sources
        or manifest.get("campaign_source_inventory_sha256") != _inventory_sha(campaign_sources)
        or manifest.get("component_source_inventory") != component_sources
        or manifest.get("component_source_inventory_sha256") != _inventory_sha(component_sources)
    ):
        raise SystemExit("analysis-only campaign/component source inventory drifted")
    runtime = runtime_dependency_manifest()
    limit = runtime_limit_contract(near_fraction=LIMIT_NEAR_FRACTION)
    cert = manifest["certification"]
    source_cert = _validate_certification(Path(cert["source"]["path"]))
    policy = _runtime_policy(runtime, _inventory_sha(production_sources), source_cert["sha256"], limit)
    if (
        manifest.get("runtime_dependencies") != runtime
        or manifest.get("limit_contract") != limit
        or manifest.get("runtime_environment_policy") != policy
        or manifest.get("v19_cap_reference") != _file_ref(V19_MANIFEST)
        or _read_json(V19_MANIFEST).get("caps") != CAPS
        or manifest.get("v19_limit_contract_sha256")
        != _read_json(V19_MANIFEST).get("limit_contract", {}).get("sha256")
    ):
        raise SystemExit("V19 runtime/cap/environment contract drifted")
    frozen_root = campaign_dir / manifest["frozen_artifact_root"]
    _verify_inventory(frozen_root, manifest["frozen_artifact_inventory"], "frozen artifact tree")
    for scenario, record in manifest["artifacts"].items():
        _verify_inventory(ROOT / "benchmark_data" / "amazon" / scenario, record["files"], f"runtime {scenario}")
        _verify_inventory(frozen_root / scenario, record["files"], f"frozen {scenario}")
    frozen_cert = campaign_dir / cert["frozen_path"]
    if source_cert != cert["source"] or _sha_file(frozen_cert) != cert["frozen_sha256"]:
        raise SystemExit("certification source/frozen copy drifted")
    lock = manifest["lockdiff"]
    current_lock = _validate_lockdiff(Path(lock["source_validation"]["after"]["path"]))
    frozen_before = campaign_dir / lock["frozen_before_path"]
    frozen_after = campaign_dir / lock["frozen_after_path"]
    if (
        current_lock != lock["source_validation"]
        or _sha_file(frozen_before) != lock["frozen_before_sha256"]
        or _sha_file(frozen_after) != lock["frozen_after_sha256"]
    ):
        raise SystemExit("original-five lockdiff evidence drifted")
    if not quiet:
        print("VERIFY PASS: protocol, exact 120 schedule, source hashes, artifacts, routes, runtime, caps, certification, and lockdiff")
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
    env["PYTHONPATH"] = os.pathsep.join(policy["command_local_pythonpath"])
    if row is not None:
        env["TRAPI_REGIONS_OVERRIDE"] = json.dumps(
            {row["logical_model"]: row["region_order"]}, separators=(",", ":")
        )
        env["MODE_EVIDENCE_OBJECTIVE_ORDER"] = row["objective_order"]
        env["MODE_EVIDENCE_RUN_ID"] = row["run_id"]
    if "STOREFRONT_OPS_TOKEN" in env:
        raise SystemExit("STOREFRONT_OPS_TOKEN survived environment sanitization")
    return env


def _predecessor_evidence(campaign_dir: Path, manifest: dict, stage: str) -> list[dict]:
    blocks = manifest["probe_policy"]["predecessor_blocks"].get(stage)
    if not isinstance(blocks, list):
        raise SystemExit(f"unknown probe stage: {stage}")
    records = []
    for row in manifest["schedule"]:
        if row["block"] not in blocks:
            continue
        paths = {
            "summary": campaign_dir / row["summary_relpath"],
            "trajectory": campaign_dir / row["trajectory_relpath"],
            "run_log": campaign_dir / row["run_log_relpath"],
            "launcher_log": campaign_dir / row["launcher_log_relpath"],
            "receipt": campaign_dir / "launch_receipts" / f"{row['run_id']}.json",
            "receipt_hash": campaign_dir / "launch_receipts" / f"{row['run_id']}.sha256.json",
        }
        missing = [name for name, path in paths.items() if not path.is_file()]
        if missing:
            raise SystemExit(f"probe {stage} precedes incomplete {row['run_id']}: {', '.join(missing)}")
        if _read_json(paths["receipt_hash"]) != {"path": paths["receipt"].name, "sha256": _sha_file(paths["receipt"])}:
            raise SystemExit(f"predecessor receipt hash drifted: {row['run_id']}")
        records.append({
            "run_id": row["run_id"],
            "block": row["block"],
            "files": {
                name: {"path": str(path.relative_to(campaign_dir)), "sha256": _sha_file(path), "size": path.stat().st_size}
                for name, path in paths.items()
            },
        })
    return records


def run_probe(campaign_dir: Path, stage: str, label: str) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify_campaign(campaign_dir, quiet=True)
    label = _safe_id(label)
    blocks = manifest["probe_policy"]["stages"].get(stage)
    if not blocks:
        raise SystemExit(f"unknown probe stage: {stage}")
    predecessor = _predecessor_evidence(campaign_dir, manifest, stage)
    logicals = {row["logical_model"] for row in manifest["schedule"] if row["block"] in blocks}
    if len(logicals) != 1:
        raise SystemExit("probe stage spans logical models")
    logical = next(iter(logicals))
    checkpoint = campaign_dir / "probes" / f"checkpoint_{label}.json"
    if checkpoint.exists():
        raise SystemExit(f"probe checkpoint already exists: {checkpoint}")
    audit_refill_coexistence(int(manifest["base_port"]))
    env = _apply_environment(manifest)
    env["TRAPI_REGIONS_OVERRIDE"] = json.dumps(
        {logical: manifest["probe_policy"]["scheduled_regions"][logical]}, separators=(",", ":")
    )
    env["AGENTARENA_PROBE_LIVE_ONLY"] = "1"
    env["AGENTARENA_PROBE_INCLUDE_REDMOND"] = "1"
    probe_dir = campaign_dir / "probes"
    probe_dir.mkdir(parents=True, exist_ok=True)
    commands = {
        "small": [sys.executable, str(SCRIPT_DIR / "probe_regions.py"), logical],
        "concurrency": [sys.executable, str(SCRIPT_DIR / "probe_concurrency.py"), logical],
    }
    if logical == SOL_LOGICAL:
        commands["large"] = [sys.executable, str(SCRIPT_DIR / "probe_sol_large.py")]
    paths = {name: probe_dir / f"{name}_{label}.log" for name in commands}
    if any(path.exists() for path in paths.values()):
        raise SystemExit("one or more create-only probe logs already exist")
    for name in ("small", "large", "concurrency"):
        if name not in commands:
            continue
        with paths[name].open("x") as stream:
            completed = subprocess.run(commands[name], cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT, text=True)
        if completed.returncode:
            raise SystemExit(f"{name} probe failed; preserved log: {paths[name]}")
    evidence = validate_probe_evidence(
        manifest, stage, paths["small"], paths["concurrency"], paths.get("large")
    )
    record = {
        **evidence,
        "label": label,
        "published_at_utc": _utcnow(),
        "predecessor_evidence": predecessor,
        "logs": {
            name: {"path": str(path.relative_to(campaign_dir)), "sha256": _sha_file(path), "size": path.stat().st_size}
            for name, path in paths.items()
        },
    }
    _write_new(checkpoint, record)
    print(f"PROBE PASS: {label} ({stage})")


def _parse_utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp lacks timezone")
    return parsed.astimezone(dt.timezone.utc)


def verify_probe_checkpoint(
    campaign_dir: Path, manifest: dict, label: str, row: dict, *, at_utc: str | None = None
) -> dict:
    label = _safe_id(label)
    path = campaign_dir / "probes" / f"checkpoint_{label}.json"
    if not path.is_file():
        raise ValueError("probe checkpoint is missing")
    record = _read_json(path)
    if (
        record.get("label") != label
        or record.get("stage") != row["probe_stage"]
        or record.get("blocks") != [row["block"]]
        or record.get("logical_model") != row["logical_model"]
        or record.get("predecessor_evidence") != _predecessor_evidence(campaign_dir, manifest, row["probe_stage"])
    ):
        raise ValueError("probe checkpoint identity/predecessor contract drifted")
    logs = record.get("logs") or {}
    expected_logs = {"small", "concurrency", *( ("large",) if row["logical_model"] == SOL_LOGICAL else () )}
    if set(logs) != expected_logs:
        raise ValueError("probe checkpoint log inventory differs")
    paths = {}
    for name, ref in logs.items():
        log = (campaign_dir / ref["path"]).resolve()
        if campaign_dir.resolve() not in log.parents or not log.is_file() or ref != {
            "path": str(log.relative_to(campaign_dir)), "sha256": _sha_file(log), "size": log.stat().st_size
        }:
            raise ValueError(f"probe {name} log drifted")
        paths[name] = log
    evidence = validate_probe_evidence(manifest, row["probe_stage"], paths["small"], paths["concurrency"], paths.get("large"))
    if any(record.get(key) != value for key, value in evidence.items()):
        raise ValueError("probe evidence differs from preserved logs")
    reference = _parse_utc(at_utc or _utcnow())
    age = (reference - _parse_utc(record["published_at_utc"])).total_seconds()
    if age < 0 or age > PROBE_MAX_AGE_SECONDS:
        raise ValueError(f"probe checkpoint is stale or post-dates launch ({age:.1f}s)")
    return {"path": path, "sha256": _sha_file(path), "record": record}


def _expected_instruction(row: dict, manifest: dict) -> str:
    if row["objective_order"] == "not_applicable":
        source = ROOT / "benchmark_data" / "amazon" / row["scenario"] / "instructions.json"
        return _read_json(source)[VARIANT]["text"]
    sidecar = _read_json(Path(manifest["objective_order_sidecar"]["path"]))
    return sidecar["entries"][row["scenario"]][row["objective_order"]]


def run_one(campaign_dir: Path, run_id: str) -> None:
    """Internal one-cell entry point used only by create-only launch receipts."""
    campaign_dir = campaign_dir.resolve()
    manifest = verify_campaign(campaign_dir, quiet=True)
    matches = [row for row in manifest["schedule"] if row["run_id"] == run_id]
    if len(matches) != 1:
        raise SystemExit("run id is not unique in frozen schedule")
    row = matches[0]
    if os.environ.get("MODE_EVIDENCE_RUN_ID") != run_id:
        raise SystemExit("run environment is not bound to the frozen row")
    expected_override = json.dumps({row["logical_model"]: row["region_order"]}, separators=(",", ":"))
    if os.environ.get("TRAPI_REGIONS_OVERRIDE") != expected_override:
        raise SystemExit("run TRAPI route differs from frozen row")
    from agentarena.benchmark import registry
    from agentarena.core.experiment import Experiment, Runner
    tasks = registry.benchmark_tasks(row["scenario"], variants=[VARIANT])
    if len(tasks) != 1 or tasks[0].task_id != row["task_id"]:
        raise SystemExit("benchmark registry did not produce the exact frozen task")
    expected = _expected_instruction(row, manifest)
    canonical = _read_json(
        ROOT / "benchmark_data" / "amazon" / row["scenario"] / "instructions.json"
    )[VARIANT]["text"]
    if tasks[0].instruction != canonical:
        raise SystemExit("runtime canonical task differs before sidecar application")
    if expected != canonical:
        tasks = [dataclasses.replace(tasks[0], instruction=expected)]
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
    write_preservation(campaign_dir / row["experiment_relpath"])
    write_strict(campaign_dir / row["experiment_relpath"])
    trajectory = _read_json(campaign_dir / row["trajectory_relpath"])
    if trajectory.get("instruction") != expected:
        raise SystemExit("saved trajectory instruction differs from sidecar arm")


def _write_launch_receipt(
    campaign_dir: Path, manifest: dict, row: dict, checkpoint_label: str, env: dict
) -> None:
    launched_at = _utcnow()
    checkpoint = verify_probe_checkpoint(campaign_dir, manifest, checkpoint_label, row, at_utc=launched_at)
    expected_override = json.dumps({row["logical_model"]: row["region_order"]}, separators=(",", ":"))
    expected_pythonpath = os.pathsep.join(manifest["runtime_environment_policy"]["command_local_pythonpath"])
    if env.get("TRAPI_REGIONS_OVERRIDE") != expected_override or env.get("PYTHONPATH") != expected_pythonpath:
        raise SystemExit("launch environment differs from frozen row")
    contract = {
        "manifest_sha256": _sha_file(campaign_dir / "campaign_manifest.json"),
        "production_source_inventory_sha256": manifest["production_source_inventory_sha256"],
        "campaign_source_inventory_sha256": manifest["campaign_source_inventory_sha256"],
        "component_source_inventory_sha256": manifest["component_source_inventory_sha256"],
        "runtime_dependencies_sha256": manifest["runtime_dependencies"]["sha256"],
        "runtime_environment_policy_sha256": manifest["runtime_environment_policy"]["sha256"],
        "objective_order_sidecar_sha256": manifest["objective_order_sidecar"]["sha256"],
        "protocol_freeze_sha256": manifest["analysis_protocol"]["freeze"]["sha256"],
        "caps": manifest["caps"],
        "limit_contract_sha256": manifest["limit_contract"]["sha256"],
    }
    receipt = {
        "schema_version": 1,
        "kind": "mode_evidence_launch_receipt",
        "run_id": row["run_id"],
        "row": row,
        "launched_at_utc": launched_at,
        "probe_checkpoint": checkpoint_label,
        "probe_checkpoint_sha256": checkpoint["sha256"],
        "trapi_regions_override": expected_override,
        "pythonpath": expected_pythonpath,
        "expected_instruction_sha256": _sha_bytes(_expected_instruction(row, manifest).encode()),
        "runtime_contract": {**contract, "contract_sha256": _sha_bytes(_json_bytes(contract))},
    }
    path = campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
    _write_new(path, receipt)
    _write_new(path.with_suffix(".sha256.json"), {"path": path.name, "sha256": _sha_file(path)})


def verify_launch_receipt(campaign_dir: Path, manifest: dict, row: dict) -> dict:
    path = campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
    sidecar = path.with_suffix(".sha256.json")
    if not path.is_file() or not sidecar.is_file() or _read_json(sidecar) != {"path": path.name, "sha256": _sha_file(path)}:
        raise ValueError("create-only launch receipt/hash is missing or drifted")
    record = _read_json(path)
    if record.get("row") != row or record.get("run_id") != row["run_id"]:
        raise ValueError("launch receipt row identity drifted")
    if record.get("expected_instruction_sha256") != _sha_bytes(_expected_instruction(row, manifest).encode()):
        raise ValueError("launch receipt instruction binding drifted")
    verify_probe_checkpoint(campaign_dir, manifest, record["probe_checkpoint"], row, at_utc=record["launched_at_utc"])
    return record


def _active_run_cells() -> int:
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


def launch_block(
    campaign_dir: Path, block: int, checkpoint_label: str, confirmation: str
) -> None:
    if confirmation != LAUNCH_CONFIRMATION:
        raise SystemExit(f"launch requires --confirm {LAUNCH_CONFIRMATION}")
    campaign_dir = campaign_dir.resolve()
    manifest = verify_campaign(campaign_dir, quiet=True)
    rows = sorted(
        (row for row in manifest["schedule"] if row["block"] == block),
        key=lambda row: row["spawn_index"],
    )
    if not rows:
        raise SystemExit(f"unknown block: {block}")
    if not _ports_free(row["port"] for row in rows):
        raise SystemExit("scheduled block ports are busy")
    for row in rows:
        verify_probe_checkpoint(campaign_dir, manifest, checkpoint_label, row)
    pending = []
    for row in rows:
        summary = campaign_dir / row["summary_relpath"]
        evidence = (
            campaign_dir / row["experiment_relpath"],
            campaign_dir / row["launcher_log_relpath"],
            campaign_dir / "launch_receipts" / f"{row['run_id']}.json",
            campaign_dir / "launch_receipts" / f"{row['run_id']}.sha256.json",
        )
        if summary.is_file():
            verify_launch_receipt(campaign_dir, manifest, row)
            print(f"already complete, preserving: {row['run_id']}")
            continue
        if any(path.exists() for path in evidence):
            raise SystemExit(f"partial evidence exists for {row['run_id']}; silent relaunch prohibited")
        pending.append(row)
    if not pending:
        print(f"block {block}: already complete")
        return
    if _active_run_cells() + min(len(pending), MAX_PARALLEL_RUNS) > CAPS["max_concurrent_browsers"]:
        raise SystemExit("machine browser-capacity gate is red")
    interrupted = False

    def defer_interrupt(signum, _frame):
        nonlocal interrupted
        interrupted = True
        print(f"signal {signum}: waiting for active runs; no new spawns", file=sys.stderr, flush=True)

    old_int = signal.signal(signal.SIGINT, defer_interrupt)
    old_term = signal.signal(signal.SIGTERM, defer_interrupt)
    active = []
    queue = list(pending)
    failures = []
    try:
        while queue or active:
            while queue and len(active) < MAX_PARALLEL_RUNS and not interrupted:
                row = queue.pop(0)
                env = _apply_environment(manifest, row)
                _write_launch_receipt(campaign_dir, manifest, row, checkpoint_label, env)
                log = campaign_dir / row["launcher_log_relpath"]
                log.parent.mkdir(parents=True, exist_ok=True)
                stream = log.open("x")
                command = [
                    sys.executable, str(Path(__file__).resolve()), "run-one",
                    "--campaign-dir", str(campaign_dir), "--run-id", row["run_id"],
                ]
                process = subprocess.Popen(
                    command, cwd=ROOT, env=env, stdout=stream,
                    stderr=subprocess.STDOUT, text=True, start_new_session=True,
                )
                active.append((row, process, stream))
                print(f"launch {row['run_id']} primary={row['primary_region']} port={row['port']}", flush=True)
                if queue:
                    time.sleep(CAPS["spawn_stagger_seconds"])
            still = []
            for row, process, stream in active:
                code = process.poll()
                if code is None:
                    still.append((row, process, stream))
                    continue
                stream.close()
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
        for _row, process, stream in active:
            if process.poll() is None:
                process.wait()
            if not stream.closed:
                stream.close()
    if failures or queue:
        raise SystemExit(f"block {block} incomplete; evidence preserved; failures={failures}, unlaunched={[row['run_id'] for row in queue]}")
    print(f"BLOCK {block} COMPLETE")


def status(campaign_dir: Path) -> dict:
    campaign_dir = campaign_dir.resolve()
    manifest = verify_campaign(campaign_dir, quiet=True)
    blocks = []
    block_ids = sorted({int(row["block"]) for row in manifest["schedule"]})
    for block in block_ids:
        rows = [row for row in manifest["schedule"] if row["block"] == block]
        complete = sum((campaign_dir / row["summary_relpath"]).is_file() for row in rows)
        launched = sum((campaign_dir / "launch_receipts" / f"{row['run_id']}.json").is_file() for row in rows)
        blocks.append({"block": block, "scheduled": len(rows), "launched": launched, "complete": complete})
    record = {
        "campaign_id": manifest["campaign_id"],
        "scheduled": 120,
        "launched": sum(item["launched"] for item in blocks),
        "complete": sum(item["complete"] for item in blocks),
        "blocks": blocks,
    }
    print(json.dumps(record, indent=2, sort_keys=True))
    return record


def _arm_limit_kind(row: dict) -> str:
    return "deliberative" if row["scaffold"] in {
        "browseruse-deliberative", "browseruse-deliberative-no-coverage"
    } else "baseline"


def _validated_result(campaign_dir: Path, manifest: dict, row: dict) -> dict:
    verify_launch_receipt(campaign_dir, manifest, row)
    paths = {
        "summary": campaign_dir / row["summary_relpath"],
        "trajectory": campaign_dir / row["trajectory_relpath"],
        "run_log": campaign_dir / row["run_log_relpath"],
        "launcher_log": campaign_dir / row["launcher_log_relpath"],
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise ValueError(f"{row['run_id']}: missing terminal evidence: {', '.join(missing)}")
    summary = _read_json(paths["summary"])
    trajectory = _read_json(paths["trajectory"])
    pstar = summary.get("preservation_strict")
    strict = summary.get("strict_binary")
    if not isinstance(pstar, (int, float)) or isinstance(pstar, bool) or not 0 <= pstar <= 1:
        raise ValueError(f"{row['run_id']}: preservation_strict is absent/malformed")
    if not isinstance(strict, (int, float)) or isinstance(strict, bool) or not 0 <= strict <= 1:
        raise ValueError(f"{row['run_id']}: strict_binary is absent/malformed")
    expected_instruction = _expected_instruction(row, manifest)
    if (
        trajectory.get("scaffold") != row["scaffold"]
        or trajectory.get("model") != row["model_recorded"]
        or trajectory.get("task_id") != row["task_id"]
        or trajectory.get("condition") != row["condition"]
        or trajectory.get("instruction") != expected_instruction
    ):
        raise ValueError(f"{row['run_id']}: trajectory identity/instruction drifted")
    stats = trajectory.get("stats") or {}
    audit = stats.get("limit_audit")
    audit_errors = validate_limit_audit(audit, manifest["limit_contract"], _arm_limit_kind(row))
    if audit_errors:
        raise ValueError(f"{row['run_id']}: limit audit invalid: {'; '.join(audit_errors)}")
    touched = []
    for category in ("safety_backstops", "lossy_context_limits"):
        for name, record in (audit.get("categories", {}).get(category, {}) or {}).items():
            if record.get("touched_count"):
                touched.append(f"{category}.{name}")
    if touched:
        raise ValueError(f"{row['run_id']}: outcome-censoring/lossy limits touched: {', '.join(touched)}")
    store = stats.get("evaluate_result_store")
    near_policy = manifest["limit_contract"].get("near_policy") or {}
    if not isinstance(store, dict):
        raise ValueError(f"{row['run_id']}: evaluate-result store audit is absent")
    try:
        utilizations = {
            "single_chars": store["max_serialized_chars"] / store["single_max_chars"],
            "store_bytes": store["bytes"] / store["max_bytes"],
            "store_responses": store["responses"] / store["max_responses"],
        }
        near_thresholds = {
            "single_chars": float(near_policy["evaluate_result_single_fraction"]),
            "store_bytes": float(near_policy["evaluate_result_store_fraction"]),
            "store_responses": float(near_policy["evaluate_result_store_fraction"]),
        }
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{row['run_id']}: evaluate-result near audit is malformed: {exc}") from exc
    near = [name for name, value in utilizations.items() if value >= near_thresholds[name]]
    if near:
        raise ValueError(f"{row['run_id']}: evaluate-result safety bound is near: {', '.join(near)}")
    return {
        "run_id": row["run_id"],
        "study": row["study"],
        "repeat": row["repeat"],
        "scenario": row["scenario"],
        "condition": row["condition"],
        "arm": row["arm"],
        "objective_order": row["objective_order"],
        "scaffold": row["scaffold"],
        "outcome": summary.get("outcome"),
        "chosen": summary.get("chosen"),
        "preservation_strict": float(pstar),
        "strict_binary": float(strict),
        "evaluate_result_utilization": utilizations,
        "summary_sha256": _sha_file(paths["summary"]),
        "trajectory_sha256": _sha_file(paths["trajectory"]),
    }


def _next_report_paths(campaign_dir: Path) -> tuple[Path, Path, Path]:
    root = campaign_dir / "reports"
    for number in range(1, 10000):
        stem = root / f"report_{number:04d}"
        paths = (stem.with_suffix(".json"), stem.with_suffix(".md"), stem.with_suffix(".sha256.json"))
        if not any(path.exists() for path in paths):
            return paths
    raise SystemExit("no create-only report number remains")


def build_report(campaign_dir: Path) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify_campaign(campaign_dir, quiet=True)
    results = []
    errors = []
    for row in manifest["schedule"]:
        try:
            results.append(_validated_result(campaign_dir, manifest, row))
        except (ValueError, SystemExit) as exc:
            errors.append(str(exc))
    if errors or len(results) != 120:
        raise SystemExit(
            f"REPORT FAIL CLOSED: {len(results)}/120 valid scheduled runs\n  "
            + "\n  ".join(errors[:30])
        )
    groups = defaultdict(list)
    for result in results:
        groups[(result["study"], result["arm"])].append(result)
    aggregates = [
        {
            "study": study,
            "arm": arm,
            "n": len(items),
            "mean_preservation_strict": sum(item["preservation_strict"] for item in items) / len(items),
            "mean_strict_binary": sum(item["strict_binary"] for item in items) / len(items),
        }
        for (study, arm), items in sorted(groups.items())
    ]
    report = {
        "schema_version": 1,
        "kind": REPORT_KIND,
        "created_at_utc": _utcnow(),
        "campaign_id": manifest["campaign_id"],
        "manifest_sha256": _sha_file(campaign_dir / "campaign_manifest.json"),
        "scheduled_denominator": 120,
        "valid_runs": 120,
        "fail_closed": True,
        "all_safety_and_lossy_limit_touches_zero": True,
        "per_run_v19_safety_context_time_and_step_caps_reused": True,
        "launch_concurrency": {
            "v19_launch_only_max_parallel_runs": 4,
            "campaign_max_parallel_runs": MAX_PARALLEL_RUNS,
            "prospectively_declared_operational_override": True,
            "estimand_or_per_run_behavior_change": False,
            "global_machine_cap_respected": True,
        },
        "metric": "preservation_strict",
        "aggregates": aggregates,
        "runs": results,
    }
    json_path, markdown_path, hash_path = _next_report_paths(campaign_dir)
    lines = [
        f"# Mode-evidence ablation report: {manifest['campaign_id']}",
        "",
        "All 120 preregistered runs validated with zero safety/lossy limit touches.",
        "",
        "| Study | Arm | n | Mean P* | Strict rate |",
        "|---|---:|---:|---:|---:|",
    ]
    for item in aggregates:
        lines.append(
            f"| {item['study']} | {item['arm']} | {item['n']} | "
            f"{item['mean_preservation_strict']:.4f} | {item['mean_strict_binary']:.4f} |"
        )
    _write_new(json_path, report)
    _write_new_text(markdown_path, "\n".join(lines) + "\n")
    _write_new(hash_path, {
        "json": {"path": json_path.name, "sha256": _sha_file(json_path)},
        "markdown": {"path": markdown_path.name, "sha256": _sha_file(markdown_path)},
    })
    print(f"REPORT PASS: {json_path}")


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
    prepare.add_argument("--sol-regions", type=_parse_regions, required=True)
    prepare.add_argument("--terra-regions", type=_parse_regions, required=True)

    for name in ("verify", "status", "report"):
        command = sub.add_parser(name)
        command.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    probe = sub.add_parser("probe")
    probe.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    probe.add_argument("--stage", required=True)
    probe.add_argument("--label", required=True)
    launch = sub.add_parser("launch-block")
    launch.add_argument("--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN)
    launch.add_argument("--block", type=int, required=True)
    launch.add_argument("--checkpoint", required=True)
    launch.add_argument("--confirm", required=True)
    run = sub.add_parser("run-one", help=argparse.SUPPRESS)
    run.add_argument("--campaign-dir", type=Path, required=True)
    run.add_argument("--run-id", required=True)

    args = parser.parse_args()
    if args.command == "verify-static":
        print(json.dumps(static_verify(), indent=2, sort_keys=True))
    elif args.command == "prepare":
        prepare_campaign(
            args.campaign_dir, args.campaign_id, args.base_port,
            args.cert_report, args.lockdiff_report,
            args.sol_regions, args.terra_regions,
        )
    elif args.command == "verify":
        verify_campaign(args.campaign_dir)
    elif args.command == "probe":
        run_probe(args.campaign_dir, args.stage, args.label)
    elif args.command == "launch-block":
        launch_block(args.campaign_dir, args.block, args.checkpoint, args.confirm)
    elif args.command == "run-one":
        run_one(args.campaign_dir, args.run_id)
    elif args.command == "status":
        status(args.campaign_dir)
    elif args.command == "report":
        build_report(args.campaign_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
