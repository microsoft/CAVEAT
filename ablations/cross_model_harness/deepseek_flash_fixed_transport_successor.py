#!/usr/bin/env python
"""Full five-run DeepSeek-Flash successor under fixed shared transport.

The prior DeepSeek supplement is preserved.  This successor reruns all five
scenarios—not only the previously affected backpack row—and changes no
decision logic.  It specializes the frozen supplement launcher with the fresh
common-runtime parent, an explicit two-region route contract, a fail-closed
excluded backpack smoke, and a machine-wide browser-cap guard.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LEGACY_PATH = HERE / "supplement_deepseek_v4_flash.py"
UPSTREAM_PATH = HERE / "supplement_gpt4o.py"
PREREG_PATH = HERE / "deepseek_flash_fixed_transport_preregistration.json"
DEFAULT_CAMPAIGN = (
    ROOT
    / "results/harness_deliberative_deepseek_v4_flash_easy_fixed_transport_v2"
)
DEFAULT_RUNTIME_PARENT = (
    ROOT / "results/harness_deliberative_ab_fixed_transport_v19"
)
OLD_DEEPSEEK_CAMPAIGN = (
    ROOT
    / "results/harness_deliberative_deepseek_v4_flash_easy_supplement_v1"
)
PREFREEZE_ROUTE_PROBE = (
    ROOT
    / "results/harness_successor_prefreeze_probes/deepseek_flash/"
    "route_selection_before_freeze.log"
)
CAMPAIGN_ID = (
    "harness_deliberative_deepseek_v4_flash_easy_fixed_transport_v2"
)
KIND = (
    "browseruse_deliberative_deepseek_v4_flash_fixed_transport_"
    "successor_campaign"
)
REPORT_KIND = (
    "browseruse_deliberative_deepseek_v4_flash_fixed_transport_"
    "successor_report"
)
SCHEMA_VERSION = 2
REGIONS = ("gcr/shared", "msraif/shared")
SCENARIOS = ("laptop", "office_chair", "mattress", "backpack", "tent")
WAVE_SCENARIOS = {
    1: ("laptop", "mattress"),
    2: ("office_chair", "backpack", "tent"),
}
MACHINE_BROWSER_CEILING = 36


def _load_legacy():
    spec = importlib.util.spec_from_file_location(
        "_deepseek_fixed_transport_base", LEGACY_PATH
    )
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load the frozen DeepSeek launcher")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


legacy = _load_legacy()


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


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


def _file_ref(path: Path) -> dict:
    return {
        "path": str(path.resolve()),
        "sha256": _sha_file(path),
        "size": path.stat().st_size,
    }


def _write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _preregistration() -> dict:
    prereg = _read_json(PREREG_PATH)
    scope = prereg.get("scope", {})
    model = scope.get("model", {})
    design = prereg.get("design", {})
    relationship = prereg.get("relationship_to_parent", {})
    if (
        prereg.get("schema_version") != 2
        or prereg.get("kind") != (
            "browseruse_deliberative_deepseek_v4_flash_fixed_"
            "transport_successor_preregistration"
        )
        or scope.get("scaffold") != "browseruse-deliberative"
        or scope.get("variant") != "graded"
        or scope.get("condition") != "combined"
        or scope.get("scenarios") != list(SCENARIOS)
        or model.get("request") != "DeepSeek-V4-Flash"
        or model.get("deployment")
        != "DeepSeek-V4-Flash_2026-04-23"
        or model.get("regions") != list(REGIONS)
        or design.get("measured_runs") != 5
        or design.get("full_five_scenario_successor") is not True
        or design.get("selective_backpack_refill") is not False
        or relationship.get("replacement_or_exclusion") is not False
        or relationship.get("pooled_substitution") is not False
    ):
        raise SystemExit("fixed-transport DeepSeek preregistration malformed")
    parent_path = legacy.PARENT_DEFAULT / "campaign_manifest.json"
    gpt4o_path = legacy.GPT4O_DEFAULT / "campaign_manifest.json"
    old_path = OLD_DEEPSEEK_CAMPAIGN / "campaign_manifest.json"
    for path in (parent_path, gpt4o_path, old_path, UPSTREAM_PATH):
        if not path.is_file():
            raise SystemExit(f"required preserved evidence is absent: {path}")
    prereg["relationship_to_parent"] = {
        **relationship,
        "parent_campaign_manifest_ref": _file_ref(parent_path),
        "gpt4o_supplement_manifest_ref": _file_ref(gpt4o_path),
        "old_deepseek_campaign_manifest_ref": _file_ref(old_path),
        "launcher_dependency_ref": _file_ref(UPSTREAM_PATH),
        "model_registry_ref": _file_ref(legacy.REGISTRY_PATH),
    }
    prereg["historical_context"] = {
        **prereg["historical_context"],
        "frozen_browseruse_baseline_evidence":
            legacy._baseline_evidence(),
        "prefreeze_all_three_region_small_probe":
            _file_ref(PREFREEZE_ROUTE_PROBE),
        "route_selection_note":
            "All three regions answered a small request; the frozen measured "
            "route set remains gcr+msraif because the contemporaneous "
            "capacity probe assigned redmond zero safe concurrent load. "
            "The two scheduled routes are re-probed formally before launch.",
    }
    return prereg


def _verify_preserved_cross_model_parent(
    parent_dir: Path, *, quiet: bool = False
) -> dict:
    """Verify the historical parent by its frozen manifest, not current policy.

    The old cross-model campaign correctly binds V18's then-current
    coexistence policy.  V19 intentionally changed that launch-only policy to
    pair-atomic four-run replenishment.  Re-running the old campaign verifier
    against current policy would therefore reject preserved historical
    evidence even though its manifest and result bytes are unchanged.
    """

    parent_dir = parent_dir.resolve()
    path = parent_dir / "campaign_manifest.json"
    sidecar = parent_dir / "campaign_manifest.sha256.json"
    if not path.is_file() or not sidecar.is_file():
        raise SystemExit("preserved cross-model parent manifest is absent")
    record = _read_json(sidecar)
    if record.get("sha256") != _sha_file(path):
        raise SystemExit("preserved cross-model parent hash drifted")
    manifest = _read_json(path)
    if (
        manifest.get("kind")
        != "browseruse_deliberative_cross_model_easy_campaign"
        or len(manifest.get("schedule", [])) != 10
    ):
        raise SystemExit("preserved cross-model parent identity drifted")
    if not quiet:
        print("PRESERVED PARENT VERIFY PASS")
    return manifest


def build_schedule(campaign_id: str, base_port: int) -> list[dict]:
    campaign_id = legacy.base._safe_id(campaign_id)
    rows = []
    route_index = 0
    for wave in (1, 2):
        for wave_index, scenario in enumerate(WAVE_SCENARIOS[wave]):
            primary = route_index % len(REGIONS)
            region_order = list(
                REGIONS[primary:] + REGIONS[:primary]
            )
            route_index += 1
            run_id = f"w{wave}_deepseek_{scenario}"
            run_name = f"{campaign_id}_{run_id}"
            result_name = (
                "amazon__browseruse-deliberative__DeepSeek-V4-Flash__"
                f"{scenario}-graded__combined"
            )
            rows.append({
                "run_id": run_id,
                "wave": wave,
                "wave_index": wave_index,
                "block": wave,
                "scenario": scenario,
                "variant": "graded",
                "condition": "combined",
                "scaffold": "browseruse-deliberative",
                "arm": "deliberative",
                "model_request": "DeepSeek-V4-Flash",
                "model_recorded": "DeepSeek-V4-Flash",
                "logical_model": "DeepSeek-V4-Flash",
                "deployment": "DeepSeek-V4-Flash_2026-04-23",
                "probe_stage":
                    "deepseek_before" if wave == 1 else "deepseek_mid",
                "primary_region": region_order[0],
                "region_order": region_order,
                "port": base_port + wave_index,
                "run_name": run_name,
                "experiment_relpath": f"runs/{run_name}",
                "browser_run_relpath":
                    f"runs/{run_name}/{result_name}",
                "summary_relpath":
                    f"runs/{run_name}/{result_name}/summary.json",
                "trajectory_relpath":
                    f"runs/{run_name}/{result_name}/trajectory.json",
                "run_log_relpath":
                    f"runs/{run_name}/{result_name}/run.log",
                "launcher_log_relpath":
                    f"launcher_logs/{run_id}.log",
            })
    validate_schedule(rows, base_port)
    return rows


def validate_schedule(rows: list[dict], base_port: int) -> None:
    errors = []
    if len(rows) != 5 or len({row["run_id"] for row in rows}) != 5:
        errors.append("schedule is not exactly five unique runs")
    if {row["scenario"] for row in rows} != set(SCENARIOS):
        errors.append("all five scenarios are not scheduled exactly once")
    for wave in (1, 2):
        selected = [row for row in rows if row["wave"] == wave]
        if [row["scenario"] for row in selected] != list(
            WAVE_SCENARIOS[wave]
        ):
            errors.append(f"wave {wave} scenario order drifted")
        if [row["port"] for row in selected] != [
            base_port + index for index in range(len(selected))
        ]:
            errors.append(f"wave {wave} port allocation drifted")
    if any(
        row["scaffold"] != "browseruse-deliberative"
        or row["model_request"] != "DeepSeek-V4-Flash"
        or row["variant"] != "graded"
        or row["condition"] != "combined"
        or set(row["region_order"]) != set(REGIONS)
        or row["primary_region"] != row["region_order"][0]
        for row in rows
    ):
        errors.append("one or more fixed treatment fields drifted")
    if errors:
        raise SystemExit(
            "invalid fixed-transport DeepSeek schedule:\n  "
            + "\n  ".join(errors)
        )


def _configure(runtime_parent: Path) -> None:
    runtime_parent = runtime_parent.resolve()
    legacy.__file__ = str(Path(__file__).resolve())
    legacy.base.__file__ = str(Path(__file__).resolve())
    legacy.KIND = KIND
    legacy.REPORT_KIND = REPORT_KIND
    legacy.PREREG_PATH = PREREG_PATH
    legacy.V18_DEFAULT = runtime_parent
    legacy.REGIONS = REGIONS
    legacy.base.SCHEMA_VERSION = SCHEMA_VERSION
    legacy.base.KIND = KIND
    legacy.base.REPORT_KIND = REPORT_KIND
    legacy.base.PREREG_PATH = PREREG_PATH
    legacy.base.V18_DEFAULT = runtime_parent
    legacy.base.REGIONS = REGIONS
    legacy.base._preregistration = _preregistration
    legacy.base.parent_tool.verify = _verify_preserved_cross_model_parent
    legacy.base.build_schedule = build_schedule
    legacy.base.validate_schedule = validate_schedule
    legacy._preregistration = _preregistration
    legacy.build_schedule = build_schedule
    legacy.validate_schedule = validate_schedule


def _runtime_parent_from_campaign(campaign_dir: Path) -> Path:
    manifest = _read_json(campaign_dir / "campaign_manifest.json")
    return Path(manifest["v18_manifest"]["path"]).parent


def prepare(
    campaign_dir: Path,
    campaign_id: str,
    runtime_parent: Path,
    base_port: int | None,
) -> None:
    campaign_dir = campaign_dir.resolve()
    _configure(runtime_parent)
    legacy.prepare(
        campaign_dir,
        campaign_id,
        legacy.PARENT_DEFAULT,
        runtime_parent,
        base_port,
    )
    print(
        "SUCCESSOR PREPARE PASS: full five-scenario DeepSeek denominator; "
        "old campaign preserved; no extract-specific patch"
    )


def verify(campaign_dir: Path) -> dict:
    campaign_dir = campaign_dir.resolve()
    _configure(_runtime_parent_from_campaign(campaign_dir))
    manifest = legacy.verify(campaign_dir, quiet=True)
    if (
        manifest.get("kind") != KIND
        or len(manifest.get("schedule", [])) != 5
        or manifest["relationship_to_parent"].get(
            "old_deepseek_campaign_manifest_ref"
        ) != _file_ref(
            OLD_DEEPSEEK_CAMPAIGN / "campaign_manifest.json"
        )
    ):
        raise SystemExit("fixed-transport successor binding drifted")
    print(
        "VERIFY PASS: complete five-run schedule, preserved old evidence, "
        "fresh common runtime, fixed transport, routes, artifacts and caps"
    )
    return manifest


def active_browser_runs() -> int:
    count = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            arguments = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if b"-m" in arguments and b"agentarena.run_cell" in arguments:
            count += 1
    return count


def _smoke_paths(campaign_dir: Path, manifest: dict) -> dict[str, Path]:
    run_name = f"{manifest['campaign_id']}_excluded_smoke_backpack"
    result_name = (
        "amazon__browseruse-deliberative__DeepSeek-V4-Flash__"
        "backpack-graded__combined"
    )
    root = campaign_dir / "excluded_smoke"
    return {
        "experiment": root / "runs" / run_name,
        "result": root / "runs" / run_name / result_name,
        "launcher_log": root / "launcher.log",
        "receipt": root / "launch_receipt.json",
        "gate": root / "smoke_gate.json",
    }


def _verify_smoke_gate(campaign_dir: Path, manifest: dict) -> dict:
    paths = _smoke_paths(campaign_dir, manifest)
    gate_path = paths["gate"]
    sidecar = gate_path.with_suffix(".sha256.json")
    if not gate_path.is_file() or not sidecar.is_file():
        raise SystemExit(
            "measured DeepSeek launch requires the excluded backpack smoke"
        )
    if _read_json(sidecar) != {
        "path": gate_path.name,
        "sha256": _sha_file(gate_path),
    }:
        raise SystemExit("DeepSeek smoke gate hash drifted")
    gate = _read_json(gate_path)
    if (
        gate.get("passed") is not True
        or gate.get("manifest_sha256")
        != _sha_file(campaign_dir / "campaign_manifest.json")
        or gate.get("all_lossy_context_limit_touches_zero") is not True
        or gate.get("extract_memory_chars_patch_added") is not False
        or gate.get("excluded_from_five_run_denominator") is not True
    ):
        raise SystemExit("DeepSeek excluded smoke gate is red or malformed")
    return gate


def launch_smoke(
    campaign_dir: Path, checkpoint_label: str
) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify(campaign_dir)
    paths = _smoke_paths(campaign_dir, manifest)
    if paths["gate"].exists():
        _verify_smoke_gate(campaign_dir, manifest)
        print("DeepSeek excluded smoke already passes")
        return
    if active_browser_runs() + 1 > MACHINE_BROWSER_CEILING:
        raise SystemExit("QUEUED SMOKE: machine browser ceiling has no slot")
    if any(path.exists() for path in (
        paths["experiment"], paths["launcher_log"], paths["receipt"]
    )):
        raise SystemExit("partial DeepSeek excluded-smoke evidence exists")
    source = next(
        row for row in manifest["schedule"]
        if row["scenario"] == "backpack"
    )
    checkpoint = legacy.base._verify_checkpoint(
        campaign_dir, manifest, "deepseek_before", checkpoint_label
    )
    port = int(manifest["base_port"]) + 10
    if not legacy.base._port_is_free(port):
        raise SystemExit(f"DeepSeek smoke port is busy: {port}")
    env = legacy.base._apply_environment(manifest, source)
    run_name = f"{manifest['campaign_id']}_excluded_smoke_backpack"
    command = [
        sys.executable,
        "-m",
        "agentarena.benchmark.run",
        "--name", run_name,
        "--scenarios", "backpack",
        "--conditions", "combined",
        "--variants", "graded",
        "--scaffolds", "browseruse-deliberative",
        "--models", "DeepSeek-V4-Flash",
        "--max-steps", str(manifest["caps"]["max_steps"]),
        "--repeats", "1",
        "--jobs", "1",
        "--results", str(campaign_dir / "excluded_smoke/runs"),
        "--base-port", str(port),
    ]
    _write_new(paths["receipt"], {
        "schema_version": 1,
        "kind": "excluded_deepseek_fixed_transport_smoke",
        "score_neutral": True,
        "excluded_from_five_run_denominator": True,
        "manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "checkpoint": checkpoint,
        "route": source["region_order"],
        "command": command,
        "launched_at_utc": _utcnow(),
    })
    _write_new(
        paths["receipt"].with_suffix(".sha256.json"),
        {
            "path": paths["receipt"].name,
            "sha256": _sha_file(paths["receipt"]),
        },
    )
    paths["launcher_log"].parent.mkdir(parents=True, exist_ok=True)
    with paths["launcher_log"].open("x") as stream:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
        )
    if completed.returncode:
        raise SystemExit(
            f"DeepSeek excluded smoke exited {completed.returncode}"
        )
    result = paths["result"]
    summary_path = result / "summary.json"
    trajectory_path = result / "trajectory.json"
    run_log_path = result / "run.log"
    if not all(path.is_file() for path in (
        summary_path, trajectory_path, run_log_path
    )):
        raise SystemExit("DeepSeek excluded smoke evidence is incomplete")
    summary = _read_json(summary_path)
    trajectory = _read_json(trajectory_path)
    audit = (trajectory.get("stats") or {}).get("limit_audit")
    errors = legacy.base.validate_limit_audit(
        audit, manifest["limit_contract"], "deliberative"
    )
    categories = (audit or {}).get("categories") or {}
    lossy = {
        name: record.get("touched_count")
        for name, record in (
            categories.get("lossy_context_limits") or {}
        ).items()
        if record.get("touched_count")
    }
    safety = {
        name: record.get("touched_count")
        for name, record in (
            categories.get("safety_backstops") or {}
        ).items()
        if record.get("touched_count")
    }
    purchased = bool(summary.get("chosen")) and summary.get("outcome") not in {
        "none", "error", "skipped", None
    }
    passed = purchased and not errors and not lossy and not safety
    gate = {
        "schema_version": 1,
        "kind": "excluded_deepseek_fixed_transport_smoke_gate",
        "created_at_utc": _utcnow(),
        "manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "score_neutral": True,
        "excluded_from_five_run_denominator": True,
        "purchase_completed": purchased,
        "chosen": summary.get("chosen"),
        "outcome": summary.get("outcome"),
        "preservation_strict": summary.get("preservation_strict"),
        "strict_binary": summary.get("strict_binary"),
        "limit_audit_errors": errors,
        "lossy_context_limit_touches": lossy,
        "extract_memory_chars_touched":
            "extract_memory_chars" in lossy,
        "safety_backstop_touches": safety,
        "all_lossy_context_limit_touches_zero": not lossy,
        "extract_memory_chars_patch_added": False,
        "evidence": {
            "summary": _file_ref(summary_path),
            "trajectory": _file_ref(trajectory_path),
            "run_log": _file_ref(run_log_path),
            "launcher_log": _file_ref(paths["launcher_log"]),
            "receipt": _file_ref(paths["receipt"]),
        },
        "passed": passed,
    }
    _write_new(paths["gate"], gate)
    _write_new(
        paths["gate"].with_suffix(".sha256.json"),
        {"path": paths["gate"].name, "sha256": _sha_file(paths["gate"])},
    )
    if not passed:
        raise SystemExit(
            "DEEPSEEK EXCLUDED SMOKE RED; measured launch remains blocked"
        )
    print("DEEPSEEK EXCLUDED SMOKE PASS")


def launch_wave(
    campaign_dir: Path, wave: int, checkpoint: str
) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify(campaign_dir)
    _verify_smoke_gate(campaign_dir, manifest)
    pending = sum(
        row["wave"] == wave
        and not (campaign_dir / row["summary_relpath"]).is_file()
        for row in manifest["schedule"]
    )
    active = active_browser_runs()
    if active + pending > MACHINE_BROWSER_CEILING:
        raise SystemExit(
            f"QUEUED: active={active}, pending_wave={pending}, "
            f"ceiling={MACHINE_BROWSER_CEILING}; no launch evidence created"
        )
    legacy.launch_wave(campaign_dir, wave, checkpoint)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument(
        "--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN
    )
    prepare_parser.add_argument("--campaign-id", default=CAMPAIGN_ID)
    prepare_parser.add_argument(
        "--runtime-parent", type=Path, default=DEFAULT_RUNTIME_PARENT
    )
    prepare_parser.add_argument("--base-port", type=int)

    for name in ("verify", "schedule", "status"):
        command = commands.add_parser(name)
        command.add_argument(
            "--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN
        )

    probe = commands.add_parser("probe")
    probe.add_argument(
        "--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN
    )
    probe.add_argument(
        "--stage", choices=("deepseek_before", "deepseek_mid"),
        required=True,
    )
    probe.add_argument("--label", required=True)

    smoke = commands.add_parser("launch-smoke")
    smoke.add_argument(
        "--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN
    )
    smoke.add_argument("--checkpoint", required=True)

    launch = commands.add_parser("launch-wave")
    launch.add_argument(
        "--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN
    )
    launch.add_argument("--wave", type=int, choices=(1, 2), required=True)
    launch.add_argument("--checkpoint", required=True)

    args = parser.parse_args()
    if args.command == "prepare":
        prepare(
            args.campaign_dir,
            args.campaign_id,
            args.runtime_parent,
            args.base_port,
        )
    else:
        manifest = verify(args.campaign_dir)
        if args.command == "verify":
            pass
        elif args.command == "schedule":
            print(json.dumps(manifest["schedule"], indent=2))
        elif args.command == "status":
            legacy.status(args.campaign_dir)
            print(json.dumps({
                "active_browser_runs": active_browser_runs(),
                "machine_browser_ceiling": MACHINE_BROWSER_CEILING,
            }, indent=2))
        elif args.command == "probe":
            legacy.base.run_probe(
                args.campaign_dir, args.stage, args.label
            )
        elif args.command == "launch-smoke":
            launch_smoke(args.campaign_dir, args.checkpoint)
        elif args.command == "launch-wave":
            launch_wave(args.campaign_dir, args.wave, args.checkpoint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
