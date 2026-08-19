#!/usr/bin/env python
"""Fresh, matched prompt-only successor over a shared 20-run baseline.

The baseline rows live in the fresh fixed-transport deliberative A/B campaign.
This campaign adds exactly one prompt-only treatment for each selected baseline
row.  The original prompt-only-v1 paragraph and the baseline delegate remain
byte-identical; within a pair the model, task, condition, route order, runtime
contract, and all safety backstops are inherited from the parent.

This module specializes the already-tested prompt-only campaign machinery.  It
does not edit the old campaigns or the prompt implementation.
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
UPSTREAM_PATH = HERE / "campaign.py"
PREREG_PATH = HERE / "fresh_matched_preregistration.json"
PROMPT_PATH = HERE / "prompt_only_scaffold.py"
DEFAULT_CAMPAIGN = ROOT / "results/browseruse_prompt_only_fresh_matched_v2"
DEFAULT_PARENT = ROOT / "results/harness_deliberative_ab_fixed_transport_v19"
CAMPAIGN_ID = "browseruse_prompt_only_fresh_matched_v2"
KIND = "browseruse_prompt_only_fresh_matched_campaign"
SCHEMA_VERSION = 2
PROMPT_SHA256 = (
    "2726e702eb85a074bd0ede327ff482f657d0563af922d526b47466354fac5284"
)
MACHINE_BROWSER_CEILING = 36
EASY_SCENARIOS = (
    "laptop",
    "office_chair",
    "mattress",
    "backpack",
    "tent",
)
HARD_SCENARIOS = tuple(f"{scenario}_hard" for scenario in EASY_SCENARIOS)

_ACTIVE_PARENT: Path | None = None
_ACTIVE_PREDECLARATION: Path | None = None


def _load_upstream():
    spec = importlib.util.spec_from_file_location(
        "_fresh_prompt_only_base", UPSTREAM_PATH
    )
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load frozen prompt-only campaign dependency")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = _load_upstream()
_original_prompt_inventory = base._prompt_source_inventory


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
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _parent_manifest(parent_dir: Path) -> dict:
    parent_dir = parent_dir.resolve()
    sys.path.insert(0, str(ROOT / "scripts"))
    import harness_eval_campaign as parent_tool

    manifest = parent_tool.verify_campaign(parent_dir, quiet=True)
    if (
        manifest.get("design", {}).get("runs") != 60
        or manifest.get("design", {}).get("fresh_baseline") is not True
    ):
        raise SystemExit("parent is not the fresh fixed-transport 60-run A/B")
    return manifest


def _selected_parent_rows(parent: dict) -> list[dict]:
    selected = []
    for row in parent.get("schedule", []):
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
            selected.append(row)
    expected = {
        ("weak_easy_combined", scenario, repeat)
        for scenario in EASY_SCENARIOS
        for repeat in (1, 2)
    } | {
        ("sol_high_hard", scenario, repeat)
        for scenario in HARD_SCENARIOS
        for repeat in (1, 2)
    }
    actual = {
        (row["cohort"], row["scenario"], row["repeat"])
        for row in selected
    }
    if len(selected) != 20 or actual != expected:
        raise SystemExit("parent lacks the exact 20 shared baseline rows")
    return sorted(
        selected,
        key=lambda row: (
            row["repeat"],
            0 if row["cohort"] == "weak_easy_combined" else 1,
            row["scenario"],
        ),
    )


def _parent_dir() -> Path:
    if _ACTIVE_PARENT is None:
        raise SystemExit("parent campaign was not configured")
    return _ACTIVE_PARENT


def _build_schedule(campaign_id: str, base_port: int) -> list[dict]:
    parent = _parent_manifest(_parent_dir())
    selected = _selected_parent_rows(parent)
    by_key = {
        (row["cohort"], row["scenario"], row["repeat"]): row
        for row in selected
    }
    rows = []
    for repeat in (1, 2):
        for cohort_index, (cohort, scenarios) in enumerate((
            ("weak_easy_combined", EASY_SCENARIOS),
            ("sol_high_hard", HARD_SCENARIOS),
        )):
            block = 1 + (repeat - 1) * 2 + cohort_index
            prompt_cohort = (
                "weak_easy" if cohort == "weak_easy_combined"
                else "strong_hard"
            )
            for scenario_index, scenario in enumerate(scenarios):
                source = by_key[(cohort, scenario, repeat)]
                wave_index = scenario_index + cohort_index * 5
                run_id = (
                    f"b{block:02d}_r{repeat}_{scenario}_prompt_only"
                )
                run_name = f"{campaign_id}_{run_id}"
                result_name = (
                    "amazon__browseruse-prompt-only__"
                    f"{source['model_recorded']}__{scenario}-graded__combined"
                )
                rows.append({
                    "run_id": run_id,
                    "block": block,
                    "wave": repeat,
                    "wave_index": wave_index,
                    "repeat": repeat,
                    "cohort": prompt_cohort,
                    "scenario": scenario,
                    "variant": source["variant"],
                    "condition": source["condition"],
                    "scaffold": "browseruse-prompt-only",
                    "model_request": source["model_request"],
                    "model_recorded": source["model_recorded"],
                    "logical_model": source["logical_model"],
                    "probe_stage": base.BLOCK_STAGE[block],
                    "primary_region": source["primary_region"],
                    "region_order": source["region_order"],
                    "port": base_port + wave_index,
                    "run_name": run_name,
                    "matched_parent_baseline_run_id": source["run_id"],
                    "matched_parent_baseline_spawn_index":
                        source["spawn_index"],
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
    _validate_schedule(rows, base_port)
    return rows


def _validate_schedule(rows: list[dict], base_port: int) -> None:
    parent = _parent_manifest(_parent_dir())
    selected = {
        row["run_id"]: row for row in _selected_parent_rows(parent)
    }
    errors = []
    if len(rows) != 20 or len({row["run_id"] for row in rows}) != 20:
        errors.append("treatment schedule is not exactly 20 unique runs")
    for wave in (1, 2):
        wave_rows = [row for row in rows if row["wave"] == wave]
        if len(wave_rows) != 10:
            errors.append(f"wave {wave} is not exactly ten runs")
        if sorted(row["port"] for row in wave_rows) != list(
            range(base_port, base_port + 10)
        ):
            errors.append(f"wave {wave} does not use the frozen port band")
    for row in rows:
        source = selected.get(row["matched_parent_baseline_run_id"])
        if source is None:
            errors.append(f"{row['run_id']}: matched baseline is absent")
            continue
        inherited = (
            "scenario", "variant", "condition", "model_request",
            "model_recorded", "logical_model", "primary_region",
            "region_order", "repeat",
        )
        for field in inherited:
            if row.get(field) != source.get(field):
                errors.append(
                    f"{row['run_id']}: paired field drifted: {field}"
                )
        if (
            source.get("arm") != "baseline"
            or source.get("scaffold") != "browseruse"
            or row.get("scaffold") != "browseruse-prompt-only"
        ):
            errors.append(f"{row['run_id']}: arm/scaffold contract drifted")
    if errors:
        raise SystemExit("invalid matched schedule:\n  " + "\n  ".join(errors))


def _prompt_source_inventory() -> dict:
    records = _original_prompt_inventory()
    if _ACTIVE_PREDECLARATION is None:
        raise SystemExit("baseline-sharing predeclaration was not configured")
    records["baseline_sharing_predeclaration.json"] = {
        "sha256": _sha_file(_ACTIVE_PREDECLARATION),
        "size": _ACTIVE_PREDECLARATION.stat().st_size,
    }
    return records


def _configure(parent_dir: Path, predeclaration: Path) -> None:
    global _ACTIVE_PARENT, _ACTIVE_PREDECLARATION
    _ACTIVE_PARENT = parent_dir.resolve()
    _ACTIVE_PREDECLARATION = predeclaration.resolve()
    base.__file__ = str(Path(__file__).resolve())
    base.SCHEMA_VERSION = SCHEMA_VERSION
    base.KIND = KIND
    base.PREREG_PATH = PREREG_PATH
    base.V18_DEFAULT = _ACTIVE_PARENT
    base.build_schedule = _build_schedule
    base.validate_schedule = _validate_schedule
    base._prompt_source_inventory = _prompt_source_inventory


def _absence_paths(parent_dir: Path, row: dict) -> list[Path]:
    return [
        parent_dir / row["summary_relpath"],
        parent_dir / row["trajectory_relpath"],
        parent_dir / row["run_log_relpath"],
        parent_dir / row["launcher_log_relpath"],
        parent_dir / "launch_receipts" / f"{row['run_id']}.json",
        parent_dir / "launch_receipts"
        / f"{row['run_id']}.sha256.json",
    ]


def prepare(
    campaign_dir: Path,
    campaign_id: str,
    parent_dir: Path,
    base_port: int | None,
) -> None:
    campaign_dir = campaign_dir.resolve()
    parent_dir = parent_dir.resolve()
    if campaign_dir.exists() and any(campaign_dir.iterdir()):
        raise SystemExit("fresh campaign directory must initially be empty")
    parent = _parent_manifest(parent_dir)
    selected = _selected_parent_rows(parent)
    present = [
        str(path)
        for row in selected
        for path in _absence_paths(parent_dir, row)
        if path.exists()
    ]
    if present:
        raise SystemExit(
            "baseline sharing was not frozen before outcomes/evidence:\n  "
            + "\n  ".join(present)
        )
    if _sha_file(PROMPT_PATH) != (
        "c05562a7769972884d1113f076c5352fd28377b99b5a0f50072a3f71273d9999"
    ):
        raise SystemExit("prompt-only implementation bytes drifted")
    prereg = _read_json(PREREG_PATH)
    prompt_bytes = prereg["prompt"].encode("utf-8")
    if hashlib.sha256(prompt_bytes).hexdigest() != PROMPT_SHA256:
        raise SystemExit("prompt text is not byte-identical to prompt-only-v1")
    predeclaration = campaign_dir / "baseline_sharing_predeclaration.json"
    _write_new(predeclaration, {
        "schema_version": 1,
        "kind": "fresh_baseline_sharing_predeclaration",
        "recorded_at_utc": _utcnow(),
        "parent_campaign_manifest": _file_ref(
            parent_dir / "campaign_manifest.json"
        ),
        "selected_parent_baseline_runs": [
            {
                "run_id": row["run_id"],
                "cohort": row["cohort"],
                "scenario": row["scenario"],
                "repeat": row["repeat"],
                "region_order": row["region_order"],
                "source_evidence_absent": [
                    str(path.resolve())
                    for path in _absence_paths(parent_dir, row)
                ],
            }
            for row in selected
        ],
        "selected_runs": 20,
        "all_selected_source_evidence_absent": True,
        "prompt_sha256": PROMPT_SHA256,
        "subset_rule":
            "baseline arm; weak_easy_combined repeats 1-2 plus "
            "sol_high_hard repeats 1-2",
    })
    _write_new(
        campaign_dir / "baseline_sharing_predeclaration.sha256.json",
        {
            "path": predeclaration.name,
            "sha256": _sha_file(predeclaration),
        },
    )
    _configure(parent_dir, predeclaration)
    base.prepare(
        campaign_dir,
        campaign_id,
        parent_dir,
        base_port,
    )
    print(
        "SUCCESSOR PREPARE PASS: 20 shared fresh baselines + "
        "20 byte-identical prompt-only treatments"
    )


def _configure_from_campaign(campaign_dir: Path) -> None:
    campaign_dir = campaign_dir.resolve()
    predeclaration = campaign_dir / "baseline_sharing_predeclaration.json"
    if not predeclaration.is_file():
        raise SystemExit("baseline-sharing predeclaration is absent")
    record = _read_json(predeclaration)
    parent_path = Path(record["parent_campaign_manifest"]["path"])
    _configure(parent_path.parent, predeclaration)


def verify(campaign_dir: Path) -> dict:
    campaign_dir = campaign_dir.resolve()
    _configure_from_campaign(campaign_dir)
    predeclaration = campaign_dir / "baseline_sharing_predeclaration.json"
    sidecar = (
        campaign_dir / "baseline_sharing_predeclaration.sha256.json"
    )
    if _read_json(sidecar) != {
        "path": predeclaration.name,
        "sha256": _sha_file(predeclaration),
    }:
        raise SystemExit("baseline-sharing predeclaration hash drifted")
    manifest = base.verify(campaign_dir, quiet=True)
    if manifest.get("kind") != KIND:
        raise SystemExit("fresh matched campaign kind drifted")
    print(
        "VERIFY PASS: parent baseline sharing, exact 20 pairs, unchanged "
        "prompt, common fixed transport, routes, artifacts, runtime and caps"
    )
    return manifest


def _smoke_specs(manifest: dict) -> list[dict]:
    wanted = {
        ("weak_easy", "laptop"): "weak_easy_laptop",
        ("strong_hard", "tent_hard"): "strong_hard_tent",
    }
    rows = []
    for row in manifest["schedule"]:
        key = (row["cohort"], row["scenario"])
        if row["wave"] != 1 or key not in wanted:
            continue
        smoke_id = wanted[key]
        run_name = f"{manifest['campaign_id']}_excluded_smoke_{smoke_id}"
        result_name = (
            "amazon__browseruse-prompt-only__"
            f"{row['model_recorded']}__{row['scenario']}-graded__combined"
        )
        rows.append({
            **{
                key: row[key] for key in (
                    "scenario", "model_request", "model_recorded",
                    "logical_model", "region_order", "primary_region",
                )
            },
            "smoke_id": smoke_id,
            "probe_stage": (
                "weak_before"
                if row["cohort"] == "weak_easy"
                else "sol_before"
            ),
            "run_name": run_name,
            "port": int(manifest["base_port"]) + 20 + len(rows),
            "experiment_relpath":
                f"excluded_smokes/runs/{run_name}",
            "result_relpath":
                f"excluded_smokes/runs/{run_name}/{result_name}",
        })
    if {row["smoke_id"] for row in rows} != {
        "weak_easy_laptop", "strong_hard_tent"
    }:
        raise SystemExit("excluded prompt-only smoke schedule drifted")
    return rows


def _smoke_gate_path(campaign_dir: Path) -> Path:
    return campaign_dir.resolve() / "excluded_smokes/smoke_gate.json"


def _verify_smoke_gate(campaign_dir: Path, manifest: dict) -> dict:
    path = _smoke_gate_path(campaign_dir)
    sidecar = path.with_name("smoke_gate.sha256.json")
    if not path.is_file() or not sidecar.is_file():
        raise SystemExit(
            "measured launch requires the excluded prompt-only smoke gate"
        )
    if _read_json(sidecar) != {
        "path": path.name,
        "sha256": _sha_file(path),
    }:
        raise SystemExit("prompt-only smoke-gate hash drifted")
    gate = _read_json(path)
    if (
        gate.get("manifest_sha256")
        != _sha_file(campaign_dir / "campaign_manifest.json")
        or gate.get("passed") is not True
        or gate.get("score_neutral") is not True
        or gate.get("all_lossy_context_limit_touches_zero") is not True
        or gate.get("extract_memory_chars_patch_added") is not False
        or {row.get("smoke_id") for row in gate.get("runs", [])}
        != {"weak_easy_laptop", "strong_hard_tent"}
    ):
        raise SystemExit("prompt-only smoke gate is red or malformed")
    return gate


def _smoke_record(
    campaign_dir: Path, manifest: dict, smoke: dict
) -> dict:
    result = campaign_dir / smoke["result_relpath"]
    paths = {
        "summary": result / "summary.json",
        "trajectory": result / "trajectory.json",
        "run_log": result / "run.log",
        "launcher_log":
            campaign_dir / "excluded_smokes/launcher_logs"
            / f"{smoke['smoke_id']}.log",
        "receipt":
            campaign_dir / "excluded_smokes/launch_receipts"
            / f"{smoke['smoke_id']}.json",
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise SystemExit(
            f"excluded smoke {smoke['smoke_id']} is incomplete: "
            + ", ".join(missing)
        )
    summary = _read_json(paths["summary"])
    trajectory = _read_json(paths["trajectory"])
    stats = trajectory.get("stats") or {}
    audit = stats.get("limit_audit")
    audit_errors = base.validate_limit_audit(
        audit, manifest["limit_contract"], "baseline"
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
    markers = [
        marker for marker in base.BOUND_LOG_MARKERS
        if marker in paths["run_log"].read_text(errors="replace")
    ]
    purchased = bool(summary.get("chosen")) and summary.get("outcome") not in {
        "none", "error", "skipped", None
    }
    passed = (
        purchased
        and not audit_errors
        and not lossy
        and not safety
        and not markers
    )
    return {
        "smoke_id": smoke["smoke_id"],
        "scenario": smoke["scenario"],
        "model_recorded": smoke["model_recorded"],
        "chosen": summary.get("chosen"),
        "outcome": summary.get("outcome"),
        "preservation_strict": summary.get("preservation_strict"),
        "strict_binary": summary.get("strict_binary"),
        "score_neutral": True,
        "purchase_completed": purchased,
        "limit_audit_errors": audit_errors,
        "lossy_context_limit_touches": lossy,
        "extract_memory_chars_touched":
            "extract_memory_chars" in lossy,
        "safety_backstop_touches": safety,
        "bound_log_markers": markers,
        "passed": passed,
        "evidence": {
            name: _file_ref(path) for name, path in paths.items()
        },
    }


def launch_smokes(
    campaign_dir: Path,
    weak_checkpoint: str,
    sol_checkpoint: str,
) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify(campaign_dir)
    if _smoke_gate_path(campaign_dir).exists():
        _verify_smoke_gate(campaign_dir, manifest)
        print("excluded prompt-only smoke gate already passes")
        return
    smokes = _smoke_specs(manifest)
    active = active_browser_runs()
    if active + len(smokes) > MACHINE_BROWSER_CEILING:
        raise SystemExit(
            f"QUEUED SMOKES: active={active}, smokes={len(smokes)}, "
            f"ceiling={MACHINE_BROWSER_CEILING}"
        )
    checkpoints = {
        "gpt-5.6-terra": weak_checkpoint,
        "gpt-5.6-sol": sol_checkpoint,
    }
    processes = []
    for index, smoke in enumerate(smokes):
        result = campaign_dir / smoke["result_relpath"]
        experiment = campaign_dir / smoke["experiment_relpath"]
        launcher = (
            campaign_dir / "excluded_smokes/launcher_logs"
            / f"{smoke['smoke_id']}.log"
        )
        receipt = (
            campaign_dir / "excluded_smokes/launch_receipts"
            / f"{smoke['smoke_id']}.json"
        )
        if result.exists() or experiment.exists() or launcher.exists() or receipt.exists():
            raise SystemExit(
                f"partial excluded smoke evidence exists: "
                f"{smoke['smoke_id']}"
            )
        if not base._port_is_free(smoke["port"]):
            raise SystemExit(f"excluded smoke port is busy: {smoke['port']}")
        label = checkpoints[smoke["logical_model"]]
        checkpoint = base._verify_checkpoint(
            campaign_dir,
            manifest,
            smoke["probe_stage"],
            label,
        )
        environment_row = {
            "logical_model": smoke["logical_model"],
            "region_order": smoke["region_order"],
        }
        env = base._apply_environment(manifest, environment_row)
        _write_new(receipt, {
            "schema_version": 1,
            "kind": "excluded_prompt_only_operational_smoke",
            "smoke": smoke,
            "score_neutral": True,
            "excluded_from_measured_denominator": True,
            "manifest_sha256": _sha_file(
                campaign_dir / "campaign_manifest.json"
            ),
            "probe_checkpoint": label,
            "probe_checkpoint_sha256": checkpoint["sha256"],
            "launched_at_utc": _utcnow(),
        })
        _write_new(
            receipt.with_suffix(".sha256.json"),
            {"path": receipt.name, "sha256": _sha_file(receipt)},
        )
        launcher.parent.mkdir(parents=True, exist_ok=True)
        stream = launcher.open("x")
        command = [
            sys.executable,
            "-m",
            "agentarena.benchmark.run",
            "--name", smoke["run_name"],
            "--scenarios", smoke["scenario"],
            "--conditions", "combined",
            "--variants", "graded",
            "--scaffolds", "browseruse-prompt-only",
            "--models", smoke["model_request"],
            "--max-steps", str(manifest["caps"]["max_steps"]),
            "--repeats", "1",
            "--jobs", "1",
            "--results",
            str(campaign_dir / "excluded_smokes/runs"),
            "--base-port", str(smoke["port"]),
        ]
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        processes.append((smoke, process, stream))
        if index + 1 < len(smokes):
            import time
            time.sleep(10)
    failures = []
    for smoke, process, stream in processes:
        returncode = process.wait()
        stream.close()
        if returncode:
            failures.append((smoke["smoke_id"], returncode))
    if failures:
        raise SystemExit(f"excluded smoke launch failures: {failures}")
    records = [_smoke_record(campaign_dir, manifest, row) for row in smokes]
    gate = {
        "schema_version": 1,
        "kind": "excluded_prompt_only_smoke_gate",
        "created_at_utc": _utcnow(),
        "manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "score_neutral": True,
        "excluded_from_40_run_ablation_denominator": True,
        "all_lossy_context_limit_touches_zero": all(
            not row["lossy_context_limit_touches"] for row in records
        ),
        "extract_memory_chars_patch_added": False,
        "runs": records,
        "passed": all(row["passed"] for row in records),
    }
    path = _smoke_gate_path(campaign_dir)
    _write_new(path, gate)
    _write_new(
        path.with_name("smoke_gate.sha256.json"),
        {"path": path.name, "sha256": _sha_file(path)},
    )
    if not gate["passed"]:
        raise SystemExit(
            "EXCLUDED SMOKE GATE RED; measured prompt-only launch remains blocked"
        )
    print("EXCLUDED SMOKE GATE PASS")


def active_browser_runs() -> int:
    count = 0
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            arguments = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if (
            b"-m" in arguments
            and b"agentarena.run_cell" in arguments
        ):
            count += 1
    return count


def launch_wave(
    campaign_dir: Path,
    wave: int,
    weak_checkpoint: str,
    sol_checkpoint: str,
) -> None:
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
    base.launch_wave(
        campaign_dir, wave, weak_checkpoint, sol_checkpoint
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument(
        "--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN
    )
    prepare_parser.add_argument("--campaign-id", default=CAMPAIGN_ID)
    prepare_parser.add_argument(
        "--parent-dir", type=Path, default=DEFAULT_PARENT
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
    probe.add_argument("--stage", choices=tuple(base.STAGE_BLOCKS), required=True)
    probe.add_argument("--label", required=True)

    launch = commands.add_parser("launch-wave")
    launch.add_argument(
        "--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN
    )
    launch.add_argument("--wave", type=int, choices=(1, 2), required=True)
    launch.add_argument("--weak-checkpoint", required=True)
    launch.add_argument("--sol-checkpoint", required=True)

    smoke = commands.add_parser("launch-smokes")
    smoke.add_argument(
        "--campaign-dir", type=Path, default=DEFAULT_CAMPAIGN
    )
    smoke.add_argument("--weak-checkpoint", required=True)
    smoke.add_argument("--sol-checkpoint", required=True)

    args = parser.parse_args()
    if args.command == "prepare":
        prepare(
            args.campaign_dir,
            args.campaign_id,
            args.parent_dir,
            args.base_port,
        )
    else:
        manifest = verify(args.campaign_dir)
        if args.command == "verify":
            pass
        elif args.command == "schedule":
            print(json.dumps(manifest["schedule"], indent=2))
        elif args.command == "status":
            base.status(args.campaign_dir)
            print(json.dumps({
                "active_browser_runs": active_browser_runs(),
                "machine_browser_ceiling": MACHINE_BROWSER_CEILING,
            }, indent=2))
        elif args.command == "probe":
            base.run_probe(args.campaign_dir, args.stage, args.label)
        elif args.command == "launch-wave":
            launch_wave(
                args.campaign_dir,
                args.wave,
                args.weak_checkpoint,
                args.sol_checkpoint,
            )
        elif args.command == "launch-smokes":
            launch_smokes(
                args.campaign_dir,
                args.weak_checkpoint,
                args.sol_checkpoint,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
