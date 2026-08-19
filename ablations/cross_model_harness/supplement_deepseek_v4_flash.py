#!/usr/bin/env python
"""Frozen additive DeepSeek-V4-Flash easy-harness supplement.

This launcher reuses the already-frozen GPT-4o supplement machinery without
changing the measured harness or environment.  DeepSeek, GPT-4o, Qwen, and
Kimi retain separate five-run denominators and are always reported separately.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
UPSTREAM_PATH = HERE / "supplement_gpt4o.py"
PREREG_PATH = (
    HERE / "supplement_deepseek_v4_flash_preregistration.json"
)
PARENT_DEFAULT = ROOT / "results/harness_deliberative_cross_model_easy_v1"
GPT4O_DEFAULT = (
    ROOT / "results/harness_deliberative_gpt4o_easy_supplement_v1"
)
V18_DEFAULT = ROOT / "results/harness_deliberative_ab_confirmatory_v18"
BASELINE_ROOT = ROOT / "results/overhaul_lb"
REGISTRY_PATH = ROOT / "agentarena/llm_client.py"

MODEL_REQUEST = "DeepSeek-V4-Flash"
MODEL_RECORDED = "DeepSeek-V4-Flash"
MODEL_LOGICAL = "DeepSeek-V4-Flash"
MODEL_DEPLOYMENT = "DeepSeek-V4-Flash_2026-04-23"
REGIONS = ("gcr/shared", "msraif/shared")
SCENARIOS = ("laptop", "office_chair", "mattress", "backpack", "tent")
WAVE_SCENARIOS = {
    1: ("laptop", "mattress"),
    2: ("office_chair", "backpack", "tent"),
}
STAGE_BLOCKS = {
    "deepseek_before": [1],
    "deepseek_mid": [2],
}
STAGE_PREDECESSORS = {
    "deepseek_before": [],
    "deepseek_mid": [1],
}
KIND = "browseruse_deliberative_deepseek_v4_flash_easy_supplement_campaign"
REPORT_KIND = (
    "browseruse_deliberative_deepseek_v4_flash_easy_supplement_report"
)


def _load_isolated_upstream():
    spec = importlib.util.spec_from_file_location(
        "_deepseek_v4_flash_supplement_base", UPSTREAM_PATH
    )
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load the frozen supplement dependency")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base = _load_isolated_upstream()


def _registry_values() -> tuple[dict, dict]:
    tree = ast.parse(REGISTRY_PATH.read_text())
    values = {}
    for node in tree.body:
        name = None
        value = None
        if isinstance(node, ast.AnnAssign) and isinstance(
            node.target, ast.Name
        ):
            name, value = node.target.id, node.value
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            name, value = node.targets[0].id, node.value
        if name in {"TRAPI_DEPLOY", "TRAPI_MODEL_REGIONS"}:
            try:
                values[name] = ast.literal_eval(value)
            except (TypeError, ValueError) as exc:
                raise SystemExit(
                    f"cannot freeze {name} from the model registry: {exc}"
                ) from exc
    if set(values) != {"TRAPI_DEPLOY", "TRAPI_MODEL_REGIONS"}:
        raise SystemExit("model deployment/route registry is incomplete")
    return values["TRAPI_DEPLOY"], values["TRAPI_MODEL_REGIONS"]


def _baseline_evidence() -> dict:
    records = []
    for repeat in (1, 2, 3):
        for scenario in SCENARIOS:
            path = (
                BASELINE_ROOT
                / f"overhaul_lb_r{repeat}"
                / (
                    "amazon__browseruse__DeepSeek-V4-Flash__"
                    f"{scenario}-graded__combined"
                )
                / "summary.json"
            )
            if not path.is_file():
                raise SystemExit(f"DeepSeek baseline evidence is absent: {path}")
            summary = base._read_json(path)
            expected = {
                "env": "amazon",
                "scaffold": "browseruse",
                "model": MODEL_RECORDED,
                "task_id": f"{scenario}-graded",
                "condition": "combined",
            }
            if any(summary.get(key) != value for key, value in expected.items()):
                raise SystemExit(f"DeepSeek baseline identity drifted: {path}")
            pstar = base._number(summary.get("preservation_strict"))
            binary = base._number(summary.get("strict_binary"))
            if pstar is None or binary not in (0.0, 1.0):
                raise SystemExit(f"DeepSeek baseline strict scores absent: {path}")
            records.append({
                "repeat": repeat,
                "scenario": scenario,
                "preservation_strict": pstar,
                "strict_binary": binary,
                "chosen": summary.get("chosen"),
                "outcome": summary.get("outcome"),
                "summary": base._file_ref(path),
            })
    aggregate = {
        "runs": len(records),
        "sum_preservation_strict": sum(
            row["preservation_strict"] for row in records
        ),
        "mean_preservation_strict": sum(
            row["preservation_strict"] for row in records
        ) / len(records),
        "strict_successes": int(sum(
            row["strict_binary"] for row in records
        )),
        "orders": sum(bool(row["chosen"]) for row in records),
        "no_orders": sum(not bool(row["chosen"]) for row in records),
        "compliant_outcomes": sum(
            row["outcome"] == "compliant" for row in records
        ),
        "decoy_outcomes": sum(
            row["outcome"] == "decoy" for row in records
        ),
    }
    expected_aggregate = {
        "runs": 15,
        "sum_preservation_strict": 1.7625,
        "mean_preservation_strict": 0.1175,
        "strict_successes": 0,
        "orders": 15,
        "no_orders": 0,
        "compliant_outcomes": 12,
        "decoy_outcomes": 3,
    }
    if aggregate != expected_aggregate:
        raise SystemExit(
            "DeepSeek exact-slice baseline aggregate drifted: "
            + json.dumps(aggregate, sort_keys=True)
        )
    return {
        "kind": "frozen_exact_browseruse_baseline_evidence",
        "aggregate": aggregate,
        "records_sha256": base._sha_bytes(base._json_bytes(records)),
        "records": records,
    }


def _preregistration() -> dict:
    prereg = base._read_json(PREREG_PATH)
    relationship = prereg.get("relationship_to_existing_cohorts", {})
    scope = prereg.get("scope", {})
    model = scope.get("model", {})
    design = prereg.get("design", {})
    if (
        prereg.get("schema_version") != 1
        or prereg.get("kind")
        != (
            "browseruse_deliberative_deepseek_v4_flash_easy_"
            "supplement_preregistration"
        )
        or relationship.get("additive_only") is not True
        or relationship.get("replacement_or_exclusion") is not False
        or relationship.get("pooled_substitution") is not False
        or relationship.get(
            "all_qwen_kimi_gpt4o_runs_and_zeros_preserved"
        ) is not True
        or relationship.get("report_all_four_models_separately") is not True
        or relationship.get("deepseek_outcomes_unknown_at_freeze") is not True
        or scope.get("environment") != "amazon"
        or scope.get("scaffold") != "browseruse-deliberative"
        or scope.get("variant") != "graded"
        or scope.get("condition") != "combined"
        or scope.get("scenarios") != list(SCENARIOS)
        or model.get("request") != MODEL_REQUEST
        or model.get("recorded") != MODEL_RECORDED
        or model.get("logical") != MODEL_LOGICAL
        or model.get("deployment") != MODEL_DEPLOYMENT
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
        raise SystemExit("DeepSeek supplement preregistration is malformed")

    deployments, region_map = _registry_values()
    if (
        deployments.get(MODEL_LOGICAL) != MODEL_DEPLOYMENT
        or region_map.get(MODEL_LOGICAL) != list(REGIONS)
    ):
        raise SystemExit("DeepSeek deployment or frozen route order drifted")

    parent_path = PARENT_DEFAULT / "campaign_manifest.json"
    gpt4o_path = GPT4O_DEFAULT / "campaign_manifest.json"
    if not parent_path.is_file() or not gpt4o_path.is_file():
        raise SystemExit("an existing cohort manifest is absent")
    prereg["relationship_to_parent"] = {
        **relationship,
        "parent_campaign_manifest_ref": base._file_ref(parent_path),
        "gpt4o_supplement_manifest_ref": base._file_ref(gpt4o_path),
        "launcher_dependency_ref": base._file_ref(UPSTREAM_PATH),
        "model_registry_ref": base._file_ref(REGISTRY_PATH),
    }
    prereg["historical_context"]["frozen_evidence"] = _baseline_evidence()
    return prereg


def build_schedule(campaign_id: str, base_port: int) -> list[dict]:
    campaign_id = base._safe_id(campaign_id)
    rows = []
    for wave in (1, 2):
        for wave_index, scenario in enumerate(WAVE_SCENARIOS[wave]):
            run_id = f"w{wave}_deepseek_{scenario}"
            run_name = f"{campaign_id}_{run_id}"
            result_dir = (
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
                "model_request": MODEL_REQUEST,
                "model_recorded": MODEL_RECORDED,
                "logical_model": MODEL_LOGICAL,
                "deployment": MODEL_DEPLOYMENT,
                "probe_stage": (
                    "deepseek_before" if wave == 1 else "deepseek_mid"
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
        row.get("scaffold") != "browseruse-deliberative"
        or row.get("model_request") != MODEL_REQUEST
        or row.get("model_recorded") != MODEL_RECORDED
        or row.get("logical_model") != MODEL_LOGICAL
        or row.get("deployment") != MODEL_DEPLOYMENT
        or row.get("variant") != "graded"
        or row.get("condition") != "combined"
        or row.get("region_order") != list(REGIONS)
        for row in rows
    ):
        errors.append("one or more frozen treatment fields drifted")
    if errors:
        raise SystemExit(
            "invalid DeepSeek supplement schedule:\n  "
            + "\n  ".join(errors)
        )


def select_free_band() -> int:
    for candidate in range(18_100, 30_000, 10):
        if 13_200 <= candidate <= 13_299:
            continue
        try:
            base._validate_port_band(candidate, require_free=True)
        except SystemExit:
            continue
        return candidate
    raise SystemExit("no free three-port DeepSeek supplement band was found")


# Specialize an isolated copy of the already-frozen launcher.  The imported
# source is itself hash-bound through the preregistration and manifest.
base.__file__ = str(Path(__file__).resolve())
base.KIND = KIND
base.REPORT_KIND = REPORT_KIND
base.PREREG_PATH = PREREG_PATH
base.PARENT_DEFAULT = PARENT_DEFAULT
base.V18_DEFAULT = V18_DEFAULT
base.MODEL_REQUEST = MODEL_REQUEST
base.MODEL_RECORDED = MODEL_RECORDED
base.MODEL_LOGICAL = MODEL_LOGICAL
base.REGIONS = REGIONS
base.SCENARIOS = SCENARIOS
base.WAVE_SCENARIOS = WAVE_SCENARIOS
base.STAGE_BLOCKS = STAGE_BLOCKS
base.STAGE_PREDECESSORS = STAGE_PREDECESSORS
base._preregistration = _preregistration
base.build_schedule = build_schedule
base.validate_schedule = validate_schedule
base.select_free_band = select_free_band


def prepare(
    campaign_dir: Path,
    campaign_id: str,
    parent_dir: Path,
    v18_dir: Path,
    base_port: int | None,
) -> None:
    if parent_dir.resolve() != PARENT_DEFAULT.resolve():
        raise SystemExit("DeepSeek supplement parent is frozen and immutable")
    with contextlib.redirect_stdout(io.StringIO()):
        base.prepare(
            campaign_dir, campaign_id, parent_dir, v18_dir, base_port
        )
    verify(campaign_dir, quiet=True)
    manifest = base._read_json(
        campaign_dir.resolve() / "campaign_manifest.json"
    )
    print(
        f"PREPARE PASS: {campaign_id}; five additive DeepSeek runs; "
        f"ports {manifest['base_port']}-{manifest['base_port'] + 2}; "
        "Qwen, Kimi, and GPT-4o unchanged"
    )


def verify(campaign_dir: Path, *, quiet: bool = False) -> dict:
    manifest = base.verify(campaign_dir, quiet=True)
    relationship = manifest.get("relationship_to_parent", {})
    if (
        relationship.get("replacement_or_exclusion") is not False
        or relationship.get("pooled_substitution") is not False
        or relationship.get(
            "all_qwen_kimi_gpt4o_runs_and_zeros_preserved"
        ) is not True
        or relationship.get("report_all_four_models_separately") is not True
    ):
        raise SystemExit("additive cohort-preservation contract drifted")
    expected_refs = _preregistration()["relationship_to_parent"]
    if relationship != expected_refs:
        raise SystemExit("existing-cohort/dependency binding drifted")
    if any(
        row.get("deployment") != MODEL_DEPLOYMENT
        for row in manifest["schedule"]
    ):
        raise SystemExit("DeepSeek deployment binding drifted")
    if not quiet:
        print(
            "VERIFY PASS: additive-only four-model separation, exact "
            "DeepSeek deployment/routes/schedule, V18 harness/runtime/caps, "
            "historical headroom evidence, and free non-refill port policy"
        )
    return manifest


def launch_wave(
    campaign_dir: Path, wave: int, checkpoint_label: str
) -> None:
    # The upstream launcher predates this supplement and names its two stage
    # slots gpt4o_before/gpt4o_mid internally.  Map those two local aliases to
    # this campaign's explicit DeepSeek checkpoint names without changing any
    # launch, harness, cap, receipt, or evidence behavior.
    original = base._verify_checkpoint

    def mapped(
        target_dir: Path, manifest: dict, stage: str, label: str
    ) -> dict:
        stage = {
            "gpt4o_before": "deepseek_before",
            "gpt4o_mid": "deepseek_mid",
        }.get(stage, stage)
        return original(target_dir, manifest, stage, label)

    base._verify_checkpoint = mapped
    try:
        base.launch_wave(campaign_dir, wave, checkpoint_label)
    finally:
        base._verify_checkpoint = original


def _external_cohort(
    manifest: dict, model: str, source: str
) -> dict:
    if source == "gpt4o_supplement":
        manifest_ref = manifest["relationship_to_parent"][
            "gpt4o_supplement_manifest_ref"
        ]
        campaign_path = Path(manifest_ref["path"]).parent.resolve()
        external_manifest = base._read_json(
            campaign_path / "campaign_manifest.json"
        )
    else:
        campaign_path = Path(
            manifest["parent_campaign_manifest"]["path"]
        ).parent.resolve()
        external_manifest = base.parent_tool.verify(
            campaign_path, quiet=True
        )
    rows = [
        row for row in external_manifest["schedule"]
        if row.get("model_recorded") == model
    ]
    records = []
    for row in rows:
        summary_path = campaign_path / row["summary_relpath"]
        if not summary_path.is_file():
            continue
        summary = base._read_json(summary_path)
        chosen = summary.get("chosen")
        outcome = summary.get("outcome")
        no_order = not chosen or outcome in {
            "none", "error", "skipped", None
        }
        pstar = base._number(summary.get("preservation_strict"))
        binary = base._number(summary.get("strict_binary"))
        if no_order and pstar is None:
            pstar = 0.0
        if no_order and binary is None:
            binary = 0.0
        records.append({
            "source": source,
            "run_id": row["run_id"],
            "model": model,
            "scenario": row["scenario"],
            "chosen": chosen,
            "outcome": outcome,
            "preservation_strict": pstar if pstar is not None else 0.0,
            "strict_binary": binary if binary in (0.0, 1.0) else 0.0,
            "steps": summary.get("num_steps") or 0,
            "seconds": summary.get("seconds") or 0.0,
            "included_in_deepseek_denominator": False,
            "excluded_or_replaced": False,
        })
    return {
        "expected_runs": 5,
        "completed_runs": len(records),
        "complete": len(records) == 5,
        "records": records,
    }


def _report_markdown(value: dict) -> str:
    names = (
        ("deepseek_supplement", "DeepSeek-V4-Flash supplement"),
        ("parent_qwen_preserved", "Parent Qwen (preserved)"),
        ("parent_kimi_preserved", "Parent Kimi (preserved)"),
        ("gpt4o_supplement_preserved", "GPT-4o supplement (preserved)"),
    )
    lines = [
        "# DeepSeek-V4-Flash additive harness supplement",
        "",
        "Every cohort remains separate; no existing run or zero is excluded, "
        "replaced, repaired, or pooled into another model's denominator.",
        "",
        "| Cohort | Runs | Mean P* | Heroes | Orders |",
        "|---|---:|---:|---:|---:|",
    ]
    for key, label in names:
        aggregate = value["aggregates"][key]
        mean = aggregate["mean_preservation_strict"]
        mean_text = "n/a" if mean is None else f"{mean:.3f}"
        lines.append(
            f"| {label} | {aggregate['runs']} | {mean_text} | "
            f"{aggregate['strict_successes']}/{aggregate['runs']} | "
            f"{aggregate['orders']}/{aggregate['runs']} |"
        )
    lines.extend([
        "",
        "The historical DeepSeek browser-use slice is descriptive headroom, "
        "not a randomized concurrent control.",
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
            "cannot report incomplete DeepSeek fixed denominator: "
            + ", ".join(missing)
        )
    rescore = base._fresh_rescore(campaign_dir, manifest)
    deepseek_rows = []
    for row in manifest["schedule"]:
        record = base._supplement_run_record(campaign_dir, manifest, row)
        record["source"] = "deepseek_supplement"
        record["included_in_deepseek_denominator"] = True
        record["excluded_or_replaced"] = False
        deepseek_rows.append(record)
    qwen = _external_cohort(
        manifest, "Qwen3.5-122B", "parent_qwen_preserved"
    )
    kimi = _external_cohort(
        manifest, "Kimi-K2.6", "parent_kimi_preserved"
    )
    gpt4o = _external_cohort(
        manifest, "gpt-4o", "gpt4o_supplement"
    )
    validation_errors = {
        row["run_id"]: row["validation_errors"]
        for row in deepseek_rows if row["validation_errors"]
    }
    safety = any(
        row["safety_backstop_touches"] or row["bound_log_markers"]
        for row in deepseek_rows
    )
    context = any(
        row["lossy_context_cap_touches"] for row in deepseek_rows
    )
    cohorts = {
        "parent_qwen_preserved": qwen,
        "parent_kimi_preserved": kimi,
        "gpt4o_supplement_preserved": gpt4o,
    }
    preserved = all(
        record["excluded_or_replaced"] is False
        and record["included_in_deepseek_denominator"] is False
        for cohort in cohorts.values()
        for record in cohort["records"]
    )
    report_value = {
        "schema_version": 1,
        "kind": REPORT_KIND,
        "reported_at_utc": base._utcnow(),
        "campaign_manifest_sha256": base._sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "relationship_to_existing_cohorts": {
            "additive_only": True,
            "replacement_or_exclusion": False,
            "pooled_substitution": False,
        },
        "headline": "preservation_strict",
        "secondary": "strict_binary",
        "rescore_audit": rescore,
        "existing_cohorts": cohorts,
        "aggregates": {
            "deepseek_supplement": base._aggregate(deepseek_rows),
            "parent_qwen_preserved": base._aggregate(qwen["records"]),
            "parent_kimi_preserved": base._aggregate(kimi["records"]),
            "gpt4o_supplement_preserved":
                base._aggregate(gpt4o["records"]),
        },
        "validity": {
            "deepseek_fixed_denominator_complete":
                len(deepseek_rows) == 5,
            "fresh_strict_rescore": rescore["fresh_complete"] is True,
            "all_existing_results_preserved_separately": preserved,
            "deepseek_validation_errors": validation_errors,
            "safety_backstop_exposed": safety,
            "lossy_context_cap_exposed": context,
            "all_deepseek_invariants_green": (
                len(deepseek_rows) == 5
                and rescore["fresh_complete"] is True
                and preserved
                and not validation_errors
                and not safety
                and not context
            ),
        },
        "deepseek_runs": deepseek_rows,
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
        base._write_new(json_path, report_value)
        base._write_new_text(
            markdown_path, _report_markdown(report_value)
        )
        base._write_new(hash_path, {
            "path": json_path.name,
            "sha256": base._sha_file(json_path),
            "markdown_path": markdown_path.name,
            "markdown_sha256": base._sha_file(markdown_path),
        })
        print(json.dumps(
            report_value["aggregates"], indent=2, sort_keys=True
        ))
        print(f"REPORT PASS: {json_path}")
        return
    raise SystemExit("no create-only DeepSeek report number remains")


def status(campaign_dir: Path) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest = verify(campaign_dir, quiet=True)
    waves = {}
    for wave in (1, 2):
        rows = [
            row for row in manifest["schedule"] if row["wave"] == wave
        ]
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
    existing = {
        "qwen": _external_cohort(
            manifest, "Qwen3.5-122B", "parent_qwen_preserved"
        )["completed_runs"],
        "kimi": _external_cohort(
            manifest, "Kimi-K2.6", "parent_kimi_preserved"
        )["completed_runs"],
        "gpt4o": _external_cohort(
            manifest, "gpt-4o", "gpt4o_supplement"
        )["completed_runs"],
    }
    checkpoints = sorted(
        path.name for path in (campaign_dir / "probes").glob(
            "checkpoint_*.json"
        )
    ) if (campaign_dir / "probes").is_dir() else []
    print(json.dumps({
        "campaign_id": manifest["campaign_id"],
        "relationship":
            "additive_only_four_model_results_always_separate",
        "expected_deepseek_runs": 5,
        "completed_deepseek_runs": sum(
            item["complete"] for item in waves.values()
        ),
        "existing_completed_runs": existing,
        "waves": waves,
        "probe_checkpoints": checkpoints,
    }, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("select-band")

    prepare_parser = commands.add_parser("prepare")
    prepare_parser.add_argument("--campaign-dir", type=Path, required=True)
    prepare_parser.add_argument("--campaign-id", required=True)
    prepare_parser.add_argument(
        "--parent-dir", type=Path, default=PARENT_DEFAULT
    )
    prepare_parser.add_argument(
        "--v18-dir", type=Path, default=V18_DEFAULT
    )
    prepare_parser.add_argument("--base-port", type=int)

    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--campaign-dir", type=Path, required=True)

    schedule_parser = commands.add_parser("schedule")
    schedule_parser.add_argument("--campaign-dir", type=Path, required=True)

    probe_parser = commands.add_parser("probe")
    probe_parser.add_argument("--campaign-dir", type=Path, required=True)
    probe_parser.add_argument(
        "--stage", choices=tuple(STAGE_BLOCKS), required=True
    )
    probe_parser.add_argument("--label", required=True)

    launch_parser = commands.add_parser("launch-wave")
    launch_parser.add_argument("--campaign-dir", type=Path, required=True)
    launch_parser.add_argument("--wave", type=int, choices=(1, 2), required=True)
    launch_parser.add_argument("--checkpoint", required=True)

    status_parser = commands.add_parser("status")
    status_parser.add_argument("--campaign-dir", type=Path, required=True)
    report_parser = commands.add_parser("report")
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
        print(json.dumps(
            verify(args.campaign_dir, quiet=True)["schedule"],
            indent=2,
            sort_keys=True,
        ))
    elif args.command == "probe":
        verify(args.campaign_dir, quiet=True)
        base.run_probe(args.campaign_dir, args.stage, args.label)
    elif args.command == "launch-wave":
        verify(args.campaign_dir, quiet=True)
        launch_wave(args.campaign_dir, args.wave, args.checkpoint)
    elif args.command == "status":
        status(args.campaign_dir)
    elif args.command == "report":
        report(args.campaign_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
