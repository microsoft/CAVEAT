#!/usr/bin/env python
"""Frozen launcher and reporter for the prompt-only browser-use ablation.

This lives outside the measured package inventory.  It loads the isolated
scaffold only through a command-local ``PYTHONPATH`` and never edits the
baseline or deliberative harnesses.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path


ABLATION_DIR = Path(__file__).resolve().parent
ROOT = ABLATION_DIR.parents[1]
SCRIPT_DIR = ROOT / "scripts"
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
from harness_eval_campaign import validate_probe_evidence  # noqa: E402


SCHEMA_VERSION = 1
KIND = "browseruse_prompt_only_ablation_campaign"
SCAFFOLD = "browseruse-prompt-only"
BASELINE_SCAFFOLD = "browseruse"
VARIANT = "graded"
CONDITION = "combined"
EASY_SCENARIOS = (
    "laptop",
    "office_chair",
    "mattress",
    "backpack",
    "tent",
)
HARD_SCENARIOS = tuple(f"{name}_hard" for name in EASY_SCENARIOS)
REGIONS = (
    "gcr/shared",
    "msraif/shared",
    "redmond/interactive",
)
WEAK_REQUEST = "gpt-5.6-terra#low"
WEAK_RECORDED = "gpt-5.6-terra-low"
WEAK_LOGICAL = "gpt-5.6-terra"
SOL_REQUEST = "gpt-5.6-sol#high"
SOL_RECORDED = "gpt-5.6-sol-high"
SOL_LOGICAL = "gpt-5.6-sol"
V18_DEFAULT = ROOT / "results/harness_deliberative_ab_confirmatory_v18"
PREREG_PATH = ABLATION_DIR / "preregistration.json"
PROMPT_SOURCE_NAMES = ("prompt_only_scaffold.py", "sitecustomize.py")
PROBE_MAX_AGE_SECONDS = 3600
PROTECTED_PORT_LOW = 13200
PROTECTED_PORT_HIGH = 13299
PORT_BAND_SIZE = 10
LIMIT_NEAR_FRACTION = 0.25
BLOCK_STAGE = {
    1: "weak_before",
    2: "sol_before",
    3: "weak_mid",
    4: "sol_mid",
}
STAGE_BLOCKS = {stage: [block] for block, stage in BLOCK_STAGE.items()}
STAGE_PREDECESSORS = {
    "weak_before": [],
    "sol_before": [],
    "weak_mid": [1, 2],
    "sol_mid": [1, 2],
}
BOUND_LOG_MARKERS = (
    "AGENTARENA_CELL_TIMEOUT_BOUND",
    "Stopping due to 1000 consecutive failures",
    "ModelOutputTruncatedError",
    "AGENTARENA_EVALUATE_STORE_INTEGRITY_FAILURE",
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


def _file_ref(path: Path) -> dict:
    return {
        "path": str(path.resolve()),
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
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        raise


def _safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise SystemExit(f"unsafe identifier: {value!r}")
    return value


def _last_json(path: Path, expected: type) -> object:
    for line in reversed(path.read_text(errors="replace").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, expected):
            return value
    raise SystemExit(f"no terminal {expected.__name__} JSON in {path}")


def _ablation_pythonpath() -> str:
    existing = os.environ.get("PYTHONPATH")
    return str(ABLATION_DIR) + (os.pathsep + existing if existing else "")


def _prompt_contract() -> dict:
    code = (
        "import hashlib,json,prompt_only_scaffold as m;"
        "from agentarena.core.scaffold import SCAFFOLDS;"
        "print(json.dumps({'scaffold':m.SCAFFOLD_NAME,"
        "'version':m.PROMPT_ONLY_VERSION,'prompt':m.PROMPT_ONLY_GUIDANCE,"
        "'declared_sha256':m.PROMPT_ONLY_SHA256,"
        "'computed_sha256':hashlib.sha256("
        "m.PROMPT_ONLY_GUIDANCE.encode('utf-8')).hexdigest(),"
        "'registered':m.SCAFFOLD_NAME in SCAFFOLDS},sort_keys=True))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = _ablation_pythonpath()
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise SystemExit(
            "prompt-only registration failed:\n"
            + completed.stdout
            + completed.stderr
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise SystemExit("prompt-only registration emitted no contract")
    contract = json.loads(lines[-1])
    prereg = _read_json(PREREG_PATH)
    if (
        contract.get("registered") is not True
        or contract.get("scaffold") != SCAFFOLD
        or contract.get("prompt") != prereg.get("prompt")
        or contract.get("version") != prereg.get("prompt_version")
        or contract.get("declared_sha256")
        != contract.get("computed_sha256")
    ):
        raise SystemExit("prompt implementation differs from preregistration")
    return contract


def _prompt_source_inventory() -> dict:
    records = {}
    for name in PROMPT_SOURCE_NAMES:
        path = ABLATION_DIR / name
        if not path.is_file():
            raise SystemExit(f"missing prompt-only source: {path}")
        records[name] = {
            "sha256": _sha_file(path),
            "size": path.stat().st_size,
        }
    return records


def _v18_manifest(v18_dir: Path) -> tuple[dict, dict]:
    path = v18_dir.resolve() / "campaign_manifest.json"
    sidecar = path.with_name("campaign_manifest.sha256.json")
    if not path.is_file() or not sidecar.is_file():
        raise SystemExit(f"V18 manifest/sidecar missing under {v18_dir}")
    record = _read_json(sidecar)
    if record.get("sha256") != _sha_file(path):
        raise SystemExit("V18 manifest hash sidecar is invalid")
    return _read_json(path), _file_ref(path)


def _comparators(v18: dict, v18_dir: Path) -> list[dict]:
    selected = []
    for row in v18.get("schedule", []):
        weak = (
            row.get("arm") == "baseline"
            and row.get("cohort") == "weak_easy_combined"
            and row.get("repeat") in (1, 2)
        )
        strong = (
            row.get("arm") == "baseline"
            and row.get("cohort") == "sol_high_hard"
            and row.get("repeat") in (1, 2)
        )
        if weak or strong:
            selected.append({
                "cohort": "weak_easy" if weak else "strong_hard",
                "run_id": row["run_id"],
                "scenario": row["scenario"],
                "repeat": row["repeat"],
                "summary_path": str(
                    (v18_dir.resolve() / row["summary_relpath"]).resolve()
                ),
            })
    expected = {
        ("weak_easy", scenario, repeat)
        for scenario in EASY_SCENARIOS
        for repeat in (1, 2)
    } | {
        ("strong_hard", scenario, repeat)
        for scenario in HARD_SCENARIOS
        for repeat in (1, 2)
    }
    actual = {
        (row["cohort"], row["scenario"], row["repeat"])
        for row in selected
    }
    if len(selected) != 20 or actual != expected:
        raise SystemExit("V18 exact baseline comparator matrix is unavailable")
    return sorted(selected, key=lambda row: row["run_id"])


def _port_is_free(port: int) -> bool:
    for family, address in (
        (socket.AF_INET, ("127.0.0.1", port)),
        (socket.AF_INET6, ("::1", port)),
    ):
        sock = socket.socket(family, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            sock.bind(address)
        except OSError:
            return False
        finally:
            sock.close()
    return True


def _validate_port_band(base_port: int, *, require_free: bool) -> None:
    high = base_port + PORT_BAND_SIZE - 1
    if base_port < 1024 or high > 65535:
        raise SystemExit(f"invalid port band {base_port}-{high}")
    if base_port <= PROTECTED_PORT_HIGH and high >= PROTECTED_PORT_LOW:
        raise SystemExit("port band intersects protected 132xx refill lanes")
    if require_free:
        busy = [
            port for port in range(base_port, high + 1)
            if not _port_is_free(port)
        ]
        if busy:
            raise SystemExit(f"port band is not free: {busy}")


def select_free_band() -> int:
    for base in range(17600, 32000, 20):
        if base <= PROTECTED_PORT_HIGH and base + 9 >= PROTECTED_PORT_LOW:
            continue
        try:
            _validate_port_band(base, require_free=True)
        except SystemExit:
            continue
        return base
    raise SystemExit("no free non-132xx ten-port band found")


def build_schedule(campaign_id: str, base_port: int) -> list[dict]:
    _safe_id(campaign_id)
    rows = []
    specs = (
        (1, 1, "weak_easy", EASY_SCENARIOS, WEAK_REQUEST,
         WEAK_RECORDED, WEAK_LOGICAL, 0),
        (2, 1, "strong_hard", HARD_SCENARIOS, SOL_REQUEST,
         SOL_RECORDED, SOL_LOGICAL, 1),
        (3, 2, "weak_easy", EASY_SCENARIOS, WEAK_REQUEST,
         WEAK_RECORDED, WEAK_LOGICAL, 0),
        (4, 2, "strong_hard", HARD_SCENARIOS, SOL_REQUEST,
         SOL_RECORDED, SOL_LOGICAL, 1),
    )
    for block, repeat, cohort, scenarios, request, recorded, logical, offset in specs:
        wave = 1 if repeat == 1 else 2
        for index, scenario in enumerate(scenarios):
            primary_index = (index + repeat - 1 + offset) % len(REGIONS)
            region_order = list(
                REGIONS[primary_index:] + REGIONS[:primary_index]
            )
            wave_index = index + offset * 5
            run_id = f"b{block:02d}_r{repeat}_{scenario}_prompt_only"
            run_name = f"{campaign_id}_{run_id}"
            result_name = (
                f"amazon__{SCAFFOLD}__{recorded}__"
                f"{scenario}-{VARIANT}__{CONDITION}"
            )
            rows.append({
                "run_id": run_id,
                "block": block,
                "wave": wave,
                "wave_index": wave_index,
                "repeat": repeat,
                "cohort": cohort,
                "scenario": scenario,
                "variant": VARIANT,
                "condition": CONDITION,
                "scaffold": SCAFFOLD,
                "model_request": request,
                "model_recorded": recorded,
                "logical_model": logical,
                "probe_stage": BLOCK_STAGE[block],
                "primary_region": region_order[0],
                "region_order": region_order,
                "port": base_port + wave_index,
                "run_name": run_name,
                "experiment_relpath": f"runs/{run_name}",
                "browser_run_relpath": f"runs/{run_name}/{result_name}",
                "summary_relpath": (
                    f"runs/{run_name}/{result_name}/summary.json"
                ),
                "trajectory_relpath": (
                    f"runs/{run_name}/{result_name}/trajectory.json"
                ),
                "run_log_relpath": (
                    f"runs/{run_name}/{result_name}/run.log"
                ),
                "launcher_log_relpath": f"launcher_logs/{run_id}.log",
            })
    validate_schedule(rows, base_port)
    return rows


def validate_schedule(rows: list[dict], base_port: int) -> None:
    expected = {
        (cohort, scenario, repeat)
        for cohort, scenarios in (
            ("weak_easy", EASY_SCENARIOS),
            ("strong_hard", HARD_SCENARIOS),
        )
        for scenario in scenarios
        for repeat in (1, 2)
    }
    actual = {
        (row["cohort"], row["scenario"], row["repeat"]) for row in rows
    }
    errors = []
    if len(rows) != 20 or actual != expected:
        errors.append("schedule is not the exact 20-run n=2 matrix")
    if len({row["run_id"] for row in rows}) != 20:
        errors.append("run IDs are not unique")
    for wave in (1, 2):
        wave_rows = [row for row in rows if row["wave"] == wave]
        if len(wave_rows) != 10:
            errors.append(f"wave {wave} does not contain ten runs")
        if sorted(row["port"] for row in wave_rows) != list(
            range(base_port, base_port + 10)
        ):
            errors.append(f"wave {wave} does not use the exact ten-port band")
        loads = Counter(row["primary_region"] for row in wave_rows)
        if max(loads.values(), default=0) - min(
            loads.values(), default=0
        ) > 1:
            errors.append(f"wave {wave} route primaries are unbalanced")
    for row in rows:
        if (
            row["scaffold"] != SCAFFOLD
            or row["variant"] != VARIANT
            or row["condition"] != CONDITION
            or set(row["region_order"]) != set(REGIONS)
            or row["region_order"][0] != row["primary_region"]
            or row["probe_stage"] != BLOCK_STAGE[row["block"]]
        ):
            errors.append(f"{row['run_id']}: row contract drifted")
    if errors:
        raise SystemExit("invalid schedule:\n  " + "\n  ".join(errors))


def _tree_inventory(root: Path) -> dict:
    records = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            records[str(path.relative_to(root))] = {
                "sha256": _sha_file(path),
                "size": path.stat().st_size,
            }
    return records


def _runtime_policy(
    runtime: dict, source_sha: str, cert_sha: str, limit_contract: dict
) -> dict:
    base = _runtime_environment_policy(runtime)
    values = dict(base["set"])
    values.update({
        "AGENTARENA_LIMIT_CONTRACT_JSON": json.dumps(
            limit_contract, sort_keys=True, separators=(",", ":")
        ),
        "AGENTARENA_RUNTIME_SOURCE_ATTESTATION": source_sha,
        "AGENTARENA_EVALUATION_INPUT_ATTESTATION": cert_sha,
    })
    payload = {
        **{key: value for key, value in base.items() if key != "sha256"},
        "set": dict(sorted(values.items())),
        "command_local_pythonpath_prefix": str(ABLATION_DIR),
    }
    return {**payload, "sha256": _sha_bytes(_json_bytes(payload))}


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
        print("campaign already frozen; nothing replaced")
        return
    campaign_id = _safe_id(campaign_id)
    base_port = select_free_band() if base_port is None else base_port
    _validate_port_band(base_port, require_free=True)
    _validate_caps(CAPS)
    prereg = _read_json(PREREG_PATH)
    prompt = _prompt_contract()
    prompt_sources = _prompt_source_inventory()
    v18, v18_ref = _v18_manifest(v18_dir)
    sources = code_inventory()
    if (
        sources != v18.get("source_inventory")
        or _sha_bytes(_json_bytes(sources))
        != v18.get("source_inventory_sha256")
    ):
        raise SystemExit("current measured source differs from frozen V18")
    baseline = sources.get("agentarena/scaffolds/browseruse.py")
    if not baseline:
        raise SystemExit("baseline browser-use source is absent from inventory")
    artifacts = {}
    for scenario in (*EASY_SCENARIOS, *HARD_SCENARIOS):
        root = ROOT / "benchmark_data/amazon" / scenario
        actual = _tree_inventory(root)
        expected = v18.get("artifacts", {}).get(scenario, {}).get("files")
        if actual != expected:
            raise SystemExit(f"{scenario} differs from frozen V18 artifacts")
        artifacts[scenario] = {
            "files_sha256": _sha_bytes(_json_bytes(actual)),
            "file_count": len(actual),
        }
    runtime = runtime_dependency_manifest()
    if runtime != v18.get("runtime_dependencies"):
        raise SystemExit("runtime dependencies differ from frozen V18")
    limit_contract = runtime_limit_contract(
        near_fraction=LIMIT_NEAR_FRACTION
    )
    cert_sha = v18["certification"]["frozen_sha256"]
    policy = _runtime_policy(
        runtime, v18["source_inventory_sha256"], cert_sha, limit_contract
    )
    schedule = build_schedule(campaign_id, base_port)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "campaign_id": campaign_id,
        "frozen_at_utc": _utcnow(),
        "design": prereg["design"],
        "analysis": prereg["analysis"],
        "prompt_contract": prompt,
        "prompt_sources": prompt_sources,
        "preregistration": _file_ref(PREREG_PATH),
        "campaign_source": _file_ref(Path(__file__)),
        "baseline_source": baseline,
        "baseline_unchanged_from_v18": True,
        "measured_source_inventory_sha256": v18[
            "source_inventory_sha256"
        ],
        "measured_source_inventory": sources,
        "runtime_dependencies_sha256": runtime["sha256"],
        "runtime_environment_policy": policy,
        "caps": CAPS,
        "limit_contract": limit_contract,
        "limit_near_fraction": LIMIT_NEAR_FRACTION,
        "certification_sha256": cert_sha,
        "v18_manifest": v18_ref,
        "v18_baseline_comparators": _comparators(v18, v18_dir),
        "artifacts": artifacts,
        "base_port": base_port,
        "port_count": PORT_BAND_SIZE,
        "schedule": schedule,
        "probe_policy": {
            "stages": STAGE_BLOCKS,
            "predecessors": STAGE_PREDECESSORS,
            "scheduled_regions": {
                WEAK_LOGICAL: list(REGIONS),
                SOL_LOGICAL: list(REGIONS),
            },
            "small_and_concurrency_required": True,
            "sol_large_request_required": True,
            "max_checkpoint_age_seconds": PROBE_MAX_AGE_SECONDS,
            "before_and_mid_model_checks": True,
        },
        "launch_policy": {
            "command_local_pythonpath_only": True,
            "spawn_stagger_seconds": 10,
            "runs_per_wave": 10,
            "create_only_receipts": True,
            "never_mass_kill": True,
            "never_silently_relaunch": True,
            "protected_port_range": [13200, 13299],
        },
        "metric_policy": {
            "headline": "preservation_strict",
            "formula": "P*=G*O",
            "secondary": "strict_binary",
            "legacy_preservation": "diagnostic_only",
        },
    }
    _write_new(manifest_path, manifest)
    _write_new(
        campaign_dir / "campaign_manifest.sha256.json",
        {"path": manifest_path.name, "sha256": _sha_file(manifest_path)},
    )
    verify(campaign_dir)
    print(
        f"PREPARE PASS: {campaign_id}; 20 runs; two ten-run waves; "
        f"ports {base_port}-{base_port + 9}"
    )


def verify(campaign_dir: Path, *, quiet: bool = False) -> dict:
    campaign_dir = campaign_dir.resolve()
    path = campaign_dir / "campaign_manifest.json"
    sidecar = campaign_dir / "campaign_manifest.sha256.json"
    if not path.is_file() or not sidecar.is_file():
        raise SystemExit("campaign is not prepared")
    if _read_json(sidecar) != {
        "path": path.name,
        "sha256": _sha_file(path),
    }:
        raise SystemExit("campaign manifest hash mismatch")
    manifest = _read_json(path)
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("kind") != KIND
    ):
        raise SystemExit("campaign kind/schema mismatch")
    if manifest.get("campaign_source") != _file_ref(Path(__file__)):
        raise SystemExit("campaign launcher source drifted")
    if manifest.get("preregistration") != _file_ref(PREREG_PATH):
        raise SystemExit("preregistration drifted")
    prompt = _prompt_contract()
    if (
        manifest.get("prompt_contract") != prompt
        or manifest.get("prompt_sources") != _prompt_source_inventory()
    ):
        raise SystemExit("prompt-only source/contract drifted")
    sources = code_inventory()
    if (
        sources != manifest.get("measured_source_inventory")
        or _sha_bytes(_json_bytes(sources))
        != manifest.get("measured_source_inventory_sha256")
    ):
        raise SystemExit("measured source inventory drifted")
    if (
        sources.get("agentarena/scaffolds/browseruse.py")
        != manifest.get("baseline_source")
    ):
        raise SystemExit("baseline browser-use scaffold drifted")
    v18_path = Path(manifest["v18_manifest"]["path"])
    if manifest["v18_manifest"] != _file_ref(v18_path):
        raise SystemExit("V18 reference manifest drifted")
    v18 = _read_json(v18_path)
    if _comparators(v18, v18_path.parent) != manifest.get(
        "v18_baseline_comparators"
    ):
        raise SystemExit("pre-registered V18 comparators drifted")
    runtime = runtime_dependency_manifest()
    if runtime.get("sha256") != manifest.get(
        "runtime_dependencies_sha256"
    ):
        raise SystemExit("runtime dependency inventory drifted")
    _validate_caps(manifest.get("caps"))
    contract = runtime_limit_contract(
        near_fraction=LIMIT_NEAR_FRACTION
    )
    policy = _runtime_policy(
        runtime,
        manifest["measured_source_inventory_sha256"],
        manifest["certification_sha256"],
        contract,
    )
    if (
        manifest.get("limit_contract") != contract
        or manifest.get("runtime_environment_policy") != policy
    ):
        raise SystemExit("runtime cap/environment contract drifted")
    base = int(manifest["base_port"])
    _validate_port_band(base, require_free=False)
    if manifest.get("schedule") != build_schedule(
        manifest["campaign_id"], base
    ):
        raise SystemExit("schedule drifted")
    v18_artifacts = v18.get("artifacts", {})
    for scenario, record in manifest["artifacts"].items():
        actual = _tree_inventory(ROOT / "benchmark_data/amazon" / scenario)
        if (
            actual != v18_artifacts.get(scenario, {}).get("files")
            or record != {
                "files_sha256": _sha_bytes(_json_bytes(actual)),
                "file_count": len(actual),
            }
        ):
            raise SystemExit(f"{scenario} benchmark artifacts drifted")
    if not quiet:
        print(
            "VERIFY PASS: preregistration, prompt, unchanged baseline, "
            "measured source, V18 inputs, schedule, runtime, and caps"
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
    env["PYTHONPATH"] = _ablation_pythonpath()
    if row:
        env["TRAPI_REGIONS_OVERRIDE"] = json.dumps(
            {row["logical_model"]: row["region_order"]},
            separators=(",", ":"),
        )
    if "STOREFRONT_OPS_TOKEN" in env:
        raise SystemExit("STOREFRONT_OPS_TOKEN survived sanitization")
    return env


def _block_completion(campaign_dir: Path, manifest: dict, block: int) -> dict:
    rows = [row for row in manifest["schedule"] if row["block"] == block]
    refs = []
    for row in rows:
        path = campaign_dir / row["summary_relpath"]
        if not path.is_file():
            raise SystemExit(
                f"probe requires completed predecessor block {block}: "
                f"missing {row['run_id']}"
            )
        refs.append({
            "run_id": row["run_id"],
            "sha256": _sha_file(path),
            "size": path.stat().st_size,
        })
    return {"block": block, "summaries": refs}


def run_probe(campaign_dir: Path, stage: str, label: str) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify(campaign_dir, quiet=True)
    label = _safe_id(label)
    if stage not in STAGE_BLOCKS:
        raise SystemExit(f"unknown probe stage: {stage}")
    predecessor = [
        _block_completion(campaign_dir, manifest, block)
        for block in STAGE_PREDECESSORS[stage]
    ]
    checkpoint = campaign_dir / "probes" / f"checkpoint_{label}.json"
    if checkpoint.exists():
        raise SystemExit(f"refusing to replace checkpoint: {checkpoint}")
    block = STAGE_BLOCKS[stage][0]
    logical = next(
        row["logical_model"] for row in manifest["schedule"]
        if row["block"] == block
    )
    env = _apply_environment(manifest)
    env["TRAPI_REGIONS_OVERRIDE"] = json.dumps(
        {logical: list(REGIONS)}, separators=(",", ":")
    )
    env["AGENTARENA_PROBE_LIVE_ONLY"] = "1"
    env["AGENTARENA_PROBE_INCLUDE_REDMOND"] = "1"
    probe_dir = campaign_dir / "probes"
    probe_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "small": probe_dir / f"small_{label}.log",
        "concurrency": probe_dir / f"concurrency_{label}.log",
    }
    if logical == SOL_LOGICAL:
        paths["large"] = probe_dir / f"large_{label}.log"
    commands = {
        "small": [
            sys.executable, str(SCRIPT_DIR / "probe_regions.py"), logical
        ],
        "concurrency": [
            sys.executable, str(SCRIPT_DIR / "probe_concurrency.py"), logical
        ],
    }
    if logical == SOL_LOGICAL:
        commands["large"] = [
            sys.executable, str(SCRIPT_DIR / "probe_sol_large.py")
        ]
    for name in ("small", "large", "concurrency"):
        if name not in commands:
            continue
        if paths[name].exists():
            raise SystemExit(f"refusing to replace probe log: {paths[name]}")
        print(f"probe {stage}/{name}", flush=True)
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
        manifest, stage, paths["small"], paths["concurrency"],
        paths.get("large")
    )
    record = {
        **evidence,
        "label": label,
        "published_at_utc": _utcnow(),
        "predecessor_evidence": predecessor,
        "manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "prompt_sha256": manifest["prompt_contract"]["computed_sha256"],
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
    path = campaign_dir / "probes" / f"checkpoint_{_safe_id(label)}.json"
    if not path.is_file():
        raise SystemExit(f"missing probe checkpoint: {path}")
    record = _read_json(path)
    if (
        record.get("stage") != stage
        or record.get("blocks") != STAGE_BLOCKS[stage]
        or record.get("probe_candidate_regions") != list(REGIONS)
        or record.get("small_routes") != list(REGIONS)
        or record.get("prompt_sha256")
        != manifest["prompt_contract"]["computed_sha256"]
        or record.get("manifest_sha256")
        != _sha_file(campaign_dir / "campaign_manifest.json")
    ):
        raise SystemExit(f"probe checkpoint contract drifted: {label}")
    published = dt.datetime.fromisoformat(
        record["published_at_utc"].replace("Z", "+00:00")
    )
    age = (dt.datetime.now(dt.timezone.utc) - published).total_seconds()
    if age < 0 or age > PROBE_MAX_AGE_SECONDS:
        raise SystemExit(f"probe checkpoint is stale ({age:.1f}s): {label}")
    expected_predecessor = [
        _block_completion(campaign_dir, manifest, block)
        for block in STAGE_PREDECESSORS[stage]
    ]
    if record.get("predecessor_evidence") != expected_predecessor:
        raise SystemExit(f"probe predecessor evidence drifted: {label}")
    log_paths = {}
    for name, ref in record.get("logs", {}).items():
        log = campaign_dir / ref["path"]
        if (
            not log.is_file()
            or _sha_file(log) != ref.get("sha256")
            or log.stat().st_size != ref.get("size")
        ):
            raise SystemExit(f"probe log drifted: {log}")
        log_paths[name] = log
    if set(log_paths) != (
        {"small", "concurrency", "large"}
        if record.get("logical_model") == SOL_LOGICAL
        else {"small", "concurrency"}
    ):
        raise SystemExit(f"probe log inventory drifted: {label}")
    evidence = validate_probe_evidence(
        manifest,
        stage,
        log_paths["small"],
        log_paths["concurrency"],
        log_paths.get("large"),
    )
    if any(record.get(key) != value for key, value in evidence.items()):
        raise SystemExit(f"probe evidence no longer validates: {label}")
    return {"path": str(path), "sha256": _sha_file(path)}


def _receipt(
    campaign_dir: Path,
    manifest: dict,
    row: dict,
    checkpoint_label: str,
    checkpoint: dict,
    env: dict,
) -> None:
    path = campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
    expected_override = json.dumps(
        {row["logical_model"]: row["region_order"]},
        separators=(",", ":"),
    )
    record = {
        "run_id": row["run_id"],
        "row": row,
        "launched_at_utc": _utcnow(),
        "manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "checkpoint_label": checkpoint_label,
        "checkpoint_sha256": checkpoint["sha256"],
        "prompt_sha256": manifest["prompt_contract"]["computed_sha256"],
        "baseline_source_sha256": manifest["baseline_source"]["sha256"],
        "trapi_regions_override": expected_override,
        "pythonpath_prefix": str(ABLATION_DIR),
        "caps": manifest["caps"],
    }
    if env.get("TRAPI_REGIONS_OVERRIDE") != expected_override:
        raise SystemExit("per-run route override differs from schedule")
    _write_new(path, record)
    _write_new(
        path.with_suffix(".sha256.json"),
        {"path": path.name, "sha256": _sha_file(path)},
    )


def launch_wave(
    campaign_dir: Path,
    wave: int,
    weak_checkpoint: str,
    sol_checkpoint: str,
) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify(campaign_dir, quiet=True)
    if wave not in (1, 2):
        raise SystemExit("wave must be 1 or 2")
    rows = sorted(
        (row for row in manifest["schedule"] if row["wave"] == wave),
        key=lambda row: row["wave_index"],
    )
    if len(rows) != 10:
        raise SystemExit("wave is not an exact ten-run wave")
    _validate_port_band(int(manifest["base_port"]), require_free=True)
    checkpoints = {
        WEAK_LOGICAL: _verify_checkpoint(
            campaign_dir, manifest,
            "weak_before" if wave == 1 else "weak_mid",
            weak_checkpoint,
        ),
        SOL_LOGICAL: _verify_checkpoint(
            campaign_dir, manifest,
            "sol_before" if wave == 1 else "sol_mid",
            sol_checkpoint,
        ),
    }
    pending = []
    for row in rows:
        summary = campaign_dir / row["summary_relpath"]
        experiment = campaign_dir / row["experiment_relpath"]
        receipt = (
            campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
        )
        launcher = campaign_dir / row["launcher_log_relpath"]
        if summary.is_file():
            print(f"already complete, preserving: {row['run_id']}")
            continue
        if experiment.exists() or receipt.exists() or launcher.exists():
            raise SystemExit(
                f"partial evidence exists for {row['run_id']}; "
                "never silently relaunch"
            )
        pending.append(row)
    if not pending:
        print(f"WAVE {wave}: already complete")
        return
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
        for index, row in enumerate(pending):
            if interrupted:
                break
            stage = row["probe_stage"]
            label = (
                weak_checkpoint
                if row["logical_model"] == WEAK_LOGICAL
                else sol_checkpoint
            )
            checkpoint = checkpoints[row["logical_model"]]
            env = _apply_environment(manifest, row)
            _receipt(
                campaign_dir, manifest, row, label, checkpoint, env
            )
            log = campaign_dir / row["launcher_log_relpath"]
            log.parent.mkdir(parents=True, exist_ok=True)
            stream = log.open("x")
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
            print(
                f"launch {row['run_id']} stage={stage} "
                f"primary={row['primary_region']} port={row['port']}",
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
            if index + 1 < len(pending):
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
        row["run_id"] for row, _process, _stream in processes
        if not (campaign_dir / row["summary_relpath"]).is_file()
    ]
    unlaunched = [
        row["run_id"] for row in pending if row["run_id"] not in launched
    ]
    if interrupted or failures or missing or unlaunched:
        raise SystemExit(
            "wave ended with preserved evidence; no runs were killed or "
            f"relaunched: interrupted={interrupted}, failures={failures}, "
            f"missing={missing}, unlaunched={unlaunched}"
        )
    print(f"WAVE PASS: {wave} ({len(processes)} fresh runs)")


def _score_value(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _run_record(
    summary_path: Path,
    trajectory_path: Path,
    log_path: Path,
    identity: dict,
    contract: dict,
) -> dict:
    summary = _read_json(summary_path)
    trajectory = _read_json(trajectory_path)
    for field in ("preservation_strict", "strict_binary"):
        if field not in summary:
            raise SystemExit(f"{summary_path} lacks fresh strict score {field}")
    stats = trajectory.get("stats") or {}
    audit_errors = validate_limit_audit(
        stats.get("limit_audit"), contract, "baseline"
    )
    safety = (
        (stats.get("limit_audit") or {})
        .get("categories", {})
        .get("safety_backstops", {})
    )
    touched = {
        name: item.get("touched_count")
        for name, item in safety.items()
        if item.get("touched_count")
    }
    log_text = log_path.read_text(errors="replace")
    markers = [marker for marker in BOUND_LOG_MARKERS if marker in log_text]
    if audit_errors or touched or markers:
        raise SystemExit(
            f"{identity['run_id']} touched/invalid backstop: "
            f"audit={audit_errors}, touched={touched}, markers={markers}"
        )
    return {
        **identity,
        "chosen": summary.get("chosen"),
        "outcome": summary.get("outcome"),
        "preservation_strict": summary.get("preservation_strict"),
        "strict_binary": summary.get("strict_binary"),
        "analysis_preservation_strict": _score_value(
            summary.get("preservation_strict")
        ),
        "analysis_strict_binary": _score_value(
            summary.get("strict_binary")
        ),
        "steps": summary.get("num_steps"),
        "seconds": summary.get("seconds"),
        "backstop_touched": False,
    }


def report(campaign_dir: Path) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify(campaign_dir, quiet=True)
    env = _apply_environment(manifest)
    for row in manifest["schedule"]:
        experiment = campaign_dir / row["experiment_relpath"]
        result = subprocess.run(
            [
                sys.executable, "-m", "agentarena.scoring.rescore",
                "--glob", str(experiment), "--strict",
            ],
            cwd=ROOT,
            env=env,
        )
        if result.returncode:
            raise SystemExit(f"strict rescore failed for {row['run_id']}")
    measured = []
    for row in manifest["schedule"]:
        measured.append(_run_record(
            campaign_dir / row["summary_relpath"],
            campaign_dir / row["trajectory_relpath"],
            campaign_dir / row["run_log_relpath"],
            {
                "source": "prompt_only",
                "run_id": row["run_id"],
                "cohort": row["cohort"],
                "scenario": row["scenario"],
                "repeat": row["repeat"],
            },
            manifest["limit_contract"],
        ))
    baseline = []
    for comparator in manifest["v18_baseline_comparators"]:
        summary_path = Path(comparator["summary_path"])
        result_dir = summary_path.parent
        baseline.append(_run_record(
            summary_path,
            result_dir / "trajectory.json",
            result_dir / "run.log",
            {
                "source": "v18_baseline",
                "run_id": comparator["run_id"],
                "cohort": comparator["cohort"],
                "scenario": comparator["scenario"],
                "repeat": comparator["repeat"],
            },
            manifest["limit_contract"],
        ))
    aggregates = {}
    for cohort in ("weak_easy", "strong_hard"):
        prompt_rows = [row for row in measured if row["cohort"] == cohort]
        base_rows = [row for row in baseline if row["cohort"] == cohort]
        if len(prompt_rows) != 10 or len(base_rows) != 10:
            raise SystemExit(f"{cohort}: incomplete fixed denominator")

        def mean(rows: list[dict], key: str) -> float:
            return sum(row[key] for row in rows) / len(rows)

        prompt_p = mean(prompt_rows, "analysis_preservation_strict")
        base_p = mean(base_rows, "analysis_preservation_strict")
        prompt_b = mean(prompt_rows, "analysis_strict_binary")
        base_b = mean(base_rows, "analysis_strict_binary")
        aggregates[cohort] = {
            "n_prompt_only": 10,
            "n_v18_baseline": 10,
            "prompt_only_mean_preservation_strict": prompt_p,
            "v18_baseline_mean_preservation_strict": base_p,
            "delta_preservation_strict": prompt_p - base_p,
            "prompt_only_mean_strict_binary": prompt_b,
            "v18_baseline_mean_strict_binary": base_b,
            "delta_strict_binary": prompt_b - base_b,
        }
    report_value = {
        "schema_version": 1,
        "kind": "browseruse_prompt_only_ablation_report",
        "reported_at_utc": _utcnow(),
        "manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "prompt_sha256": manifest["prompt_contract"]["computed_sha256"],
        "headline": "preservation_strict",
        "secondary": "strict_binary",
        "all_scheduled_runs_in_denominator": True,
        "all_backstops_untouched": True,
        "aggregates": aggregates,
        "prompt_only_runs": measured,
        "v18_baseline_runs": baseline,
    }
    reports = campaign_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    for number in range(1, 1000):
        path = reports / f"report_{number:03d}.json"
        if not path.exists():
            _write_new(path, report_value)
            _write_new(
                path.with_suffix(".sha256.json"),
                {"path": path.name, "sha256": _sha_file(path)},
            )
            print(json.dumps(aggregates, indent=2, sort_keys=True))
            print(f"REPORT PASS: {path}")
            return
    raise SystemExit("no create-only report number remains")


def status(campaign_dir: Path) -> None:
    manifest = verify(campaign_dir, quiet=True)
    campaign_dir = campaign_dir.resolve()
    rows = []
    for row in manifest["schedule"]:
        summary = campaign_dir / row["summary_relpath"]
        value = None
        if summary.is_file():
            value = _read_json(summary).get("preservation_strict")
        rows.append({
            "run_id": row["run_id"],
            "wave": row["wave"],
            "complete": summary.is_file(),
            "preservation_strict": value,
        })
    print(json.dumps({
        "complete": sum(row["complete"] for row in rows),
        "scheduled": len(rows),
        "rows": rows,
    }, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers(dest="command", required=True)

    select = subs.add_parser("select-band")

    prepare_parser = subs.add_parser("prepare")
    prepare_parser.add_argument("--campaign-dir", type=Path, required=True)
    prepare_parser.add_argument("--campaign-id", required=True)
    prepare_parser.add_argument("--v18-dir", type=Path, default=V18_DEFAULT)
    prepare_parser.add_argument("--base-port", type=int)

    verify_parser = subs.add_parser("verify")
    verify_parser.add_argument("--campaign-dir", type=Path, required=True)

    schedule_parser = subs.add_parser("schedule")
    schedule_parser.add_argument("--campaign-dir", type=Path, required=True)

    probe_parser = subs.add_parser("probe")
    probe_parser.add_argument("--campaign-dir", type=Path, required=True)
    probe_parser.add_argument("--stage", choices=tuple(STAGE_BLOCKS), required=True)
    probe_parser.add_argument("--label", required=True)

    launch_parser = subs.add_parser("launch-wave")
    launch_parser.add_argument("--campaign-dir", type=Path, required=True)
    launch_parser.add_argument("--wave", type=int, choices=(1, 2), required=True)
    launch_parser.add_argument("--weak-checkpoint", required=True)
    launch_parser.add_argument("--sol-checkpoint", required=True)

    status_parser = subs.add_parser("status")
    status_parser.add_argument("--campaign-dir", type=Path, required=True)

    report_parser = subs.add_parser("report")
    report_parser.add_argument("--campaign-dir", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "select-band":
        print(select_free_band())
    elif args.command == "prepare":
        prepare(
            args.campaign_dir, args.campaign_id, args.v18_dir,
            args.base_port
        )
    elif args.command == "verify":
        verify(args.campaign_dir)
    elif args.command == "schedule":
        manifest = verify(args.campaign_dir, quiet=True)
        print(json.dumps(manifest["schedule"], indent=2))
    elif args.command == "probe":
        run_probe(args.campaign_dir, args.stage, args.label)
    elif args.command == "launch-wave":
        launch_wave(
            args.campaign_dir, args.wave,
            args.weak_checkpoint, args.sol_checkpoint
        )
    elif args.command == "status":
        status(args.campaign_dir)
    elif args.command == "report":
        report(args.campaign_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
