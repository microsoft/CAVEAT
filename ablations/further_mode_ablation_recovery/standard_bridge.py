#!/usr/bin/env python3
"""Freeze and revalidate the 128 completed v1 Standard rows.

This module is deliberately read-only with respect to the source campaign.  It
creates one separately identified study bundle, retaining every original
schedule identity and every row-level evidence hash.  Validation calls the
frozen v1 campaign validator and row validator again; a bundle is never treated
as an attestation that can replace the underlying evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Mapping, Sequence


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_SOURCE = ROOT / "results" / "further_mode_ablation_v1"
DEFAULT_OUTPUT = ROOT / "results" / "further_mode_ablation_standard_preserved_v1"
AMENDMENT_PATH = HERE / "recovery_amendment.json"
V1_DIR = ROOT / "ablations" / "further_mode_ablation"
V1_CAMPAIGN_PATH = V1_DIR / "campaign.py"
V1_ANALYZER_PATH = V1_DIR / "analyze_results.py"
V1_CLASSIFIER_PATH = V1_DIR / "infra_classifier.py"

SCHEMA_VERSION = 1
KIND = "further_mode_ablation_study_bundle"
STUDY_ID = "further_mode_ablation_standard_v1_preserved"
STUDY = "standard"
SOURCE_STUDY = "standard_steering"
EXPECTED_BLOCKS = (1, 2, 3, 4)
EXPECTED_REPEATS = tuple(range(1, 9))
EXPECTED_ARMS = (
    "clean", "abl-substrate", "abl-pin-d0", "abl-bury-d23",
    "abl-place-d23", "sponsored", "ranking", "promo", "trust",
    "scarcity", "drip", "addon", "abl-place-d30", "friction",
    "abl-place-d52", "combined",
)
EXPECTED_DENOMINATOR = 128
EXPECTED_V1_MANIFEST_SHA256 = (
    "167897f154177d8060bd08d3b9b1acdfb652bff8bbd97a228730babdcc30612a"
)


class BridgeError(ValueError):
    """Fail-closed validation error."""


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
    if not path.is_file():
        raise BridgeError(f"required file is absent: {path}")
    return {
        "path": str(path),
        "sha256": _sha_file(path),
        "size": path.stat().st_size,
    }


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError(f"{label} is absent or malformed: {path}") from exc
    if not isinstance(value, dict):
        raise BridgeError(f"{label} is not a JSON object: {path}")
    return value


def _write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise BridgeError(f"refusing to replace create-only artifact: {path}") from exc
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _validate_ref(ref: object, label: str) -> Path:
    if not isinstance(ref, dict):
        raise BridgeError(f"{label} reference is malformed")
    raw = ref.get("path")
    if not isinstance(raw, str) or not raw:
        raise BridgeError(f"{label} path is absent")
    path = Path(raw).resolve()
    if _file_ref(path) != ref:
        raise BridgeError(f"{label} hash/size binding is invalid")
    return path


def _amendment() -> dict:
    amendment = _read_object(AMENDMENT_PATH, "recovery amendment")
    if (
        amendment.get("kind") != "further_mode_ablation_recovery_amendment"
        or amendment.get("schema_version") != 1
        or amendment.get("preserved_study", {}).get("study_id") != STUDY_ID
        or amendment.get("preserved_study", {}).get("scheduled_denominator")
        != EXPECTED_DENOMINATOR
        or amendment.get("source_campaign", {}).get("manifest_sha256")
        != EXPECTED_V1_MANIFEST_SHA256
        or amendment.get("retired_hard_execution", {}).get(
            "analysis_disposition"
        ) != "excluded_from_all_measured_combined_inference"
        or amendment.get("retired_hard_execution", {}).get(
            "block5_disposition"
        ) != "operational_pilot_excluded"
    ):
        raise BridgeError("recovery amendment identity/policy is invalid")
    return amendment


def _load_v1_module():
    # A normal import deliberately reuses the exact implementation bound by the
    # source manifest.  Importing is read-only and does not launch a run.
    from ablations.further_mode_ablation import campaign as campaign_v1
    return campaign_v1


def _source_manifest(source: Path) -> tuple[dict, object]:
    source = source.resolve()
    campaign_v1 = _load_v1_module()
    try:
        campaign_v1.verify_campaign(source, quiet=True)
    except (SystemExit, ValueError) as exc:
        raise BridgeError(f"v1 source campaign validation failed: {exc}") from exc
    manifest_path = source / "campaign_manifest.json"
    sidecar_path = source / "campaign_manifest.sha256.json"
    manifest = _read_object(manifest_path, "v1 campaign manifest")
    sidecar = _read_object(sidecar_path, "v1 manifest sidecar")
    if _sha_file(manifest_path) != EXPECTED_V1_MANIFEST_SHA256:
        raise BridgeError("v1 manifest differs from recovery amendment")
    if sidecar != {
        "path": manifest_path.name,
        "sha256": EXPECTED_V1_MANIFEST_SHA256,
    }:
        raise BridgeError("v1 manifest sidecar is invalid")
    amendment = _amendment()
    if amendment["source_campaign"]["campaign_id"] != manifest.get("campaign_id"):
        raise BridgeError("amendment and source campaign IDs differ")
    return manifest, campaign_v1


def _selected_schedule(manifest: Mapping) -> list[dict]:
    schedule = manifest.get("schedule")
    if not isinstance(schedule, list) or not all(
        isinstance(row, dict) for row in schedule
    ):
        raise BridgeError("v1 schedule is malformed")
    selected = [
        dict(row) for row in schedule
        if row.get("block") in EXPECTED_BLOCKS
    ]
    if (
        len(selected) != EXPECTED_DENOMINATOR
        or any(row.get("study") != SOURCE_STUDY for row in selected)
        or len({row.get("run_id") for row in selected}) != EXPECTED_DENOMINATOR
    ):
        raise BridgeError("source does not contain the exact 128 Standard rows")
    by_arm = defaultdict(list)
    for row in selected:
        by_arm[row.get("arm_id")].append(row)
    if set(by_arm) != set(EXPECTED_ARMS):
        raise BridgeError("Standard arm set differs from the frozen design")
    for arm, rows in by_arm.items():
        if sorted(row.get("repeat") for row in rows) != list(EXPECTED_REPEATS):
            raise BridgeError(f"{arm}: exact repetitions 1..8 are absent")
    block_counts = Counter(row["block"] for row in selected)
    if block_counts != Counter({block: 32 for block in EXPECTED_BLOCKS}):
        raise BridgeError("Standard block sizes differ from the frozen design")
    return selected


def _artifact_record(source: Path, row: Mapping, attempt: int) -> dict:
    run_id = str(row["run_id"])
    experiment = (source / str(row["experiment_relpath"])).resolve()
    if source != experiment and source not in experiment.parents:
        raise BridgeError(f"{run_id}: experiment path escapes source campaign")
    terminal = (
        source / "attempt_terminals" / f"{run_id}_attempt_{attempt}.json"
    )
    terminal_sidecar = terminal.with_suffix(".sha256.json")
    paths = {
        "summary": source / str(row["summary_relpath"]),
        "trajectory": source / str(row["trajectory_relpath"]),
        "run_log": source / str(row["run_log_relpath"]),
        "launcher_log": source / str(row["launcher_log_relpath"]),
        "launch_receipt": source / "launch_receipts" / f"{run_id}.json",
        "terminal_receipt": terminal,
        "terminal_receipt_sidecar": terminal_sidecar,
    }
    refs = {name: _file_ref(path) for name, path in paths.items()}
    terminal_record = _read_object(terminal, f"{run_id} terminal receipt")
    inventory = terminal_record.get("experiment_inventory")
    if not isinstance(inventory, dict) or not inventory:
        raise BridgeError(f"{run_id}: terminal experiment inventory is absent")
    databases = sorted(experiment.rglob("amazon_*.db"))
    if len(databases) != 1:
        raise BridgeError(f"{run_id}: expected exactly one Amazon database")
    return {
        "files": refs,
        "database": _file_ref(databases[0]),
        "experiment_path": str(experiment),
        "experiment_file_count": len(inventory),
        "experiment_inventory_sha256": _sha_bytes(_json_bytes(inventory)),
    }


def _aggregates(rows: Sequence[Mapping]) -> list[dict]:
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["arm_id"]].append(row)
    result = []
    for arm in sorted(grouped):
        values = grouped[arm]
        result.append({
            "arm_id": arm,
            "n": len(values),
            "mean_preservation_strict": sum(
                float(row["preservation_strict"]) for row in values
            ) / len(values),
            "literal_hero_rate": sum(int(row["hero"]) for row in values)
            / len(values),
            "strict_binary_rate": sum(
                float(row["strict_binary"]) for row in values
            ) / len(values),
        })
    return result


def _validate_rows(rows: Sequence[Mapping]) -> None:
    if (
        len(rows) != EXPECTED_DENOMINATOR
        or len({row.get("run_id") for row in rows}) != EXPECTED_DENOMINATOR
    ):
        raise BridgeError("bundle rows do not have the exact denominator")
    allowed_statuses = {
        "complete_and_untouched",
        "not_initialized_pre_agent_compiler_capability_failure",
    }
    for row in rows:
        if row.get("hero") not in {0, 1}:
            raise BridgeError(f"{row.get('run_id')}: hero is not binary")
        strict = row.get("strict_binary")
        pstar = row.get("preservation_strict")
        if strict not in {0, 1, 0.0, 1.0}:
            raise BridgeError(f"{row.get('run_id')}: strict_binary is not binary")
        if (
            isinstance(pstar, bool)
            or not isinstance(pstar, (int, float))
            or not math.isfinite(float(pstar))
            or not 0 <= float(pstar) <= 1
        ):
            raise BridgeError(f"{row.get('run_id')}: P* is invalid")
        if row.get("limit_audit_status") not in allowed_statuses:
            raise BridgeError(f"{row.get('run_id')}: limit status is invalid")
        if row.get("limit_touches") not in ([], None):
            raise BridgeError(f"{row.get('run_id')}: a measured limit was touched")


def _pilot_exclusion(manifest: Mapping) -> dict:
    block5 = [dict(row) for row in manifest["schedule"] if row.get("block") == 5]
    retired = [
        dict(row) for row in manifest["schedule"]
        if row.get("block") in (5, 6, 7, 8)
    ]
    if len(block5) != 28 or len(retired) != 112:
        raise BridgeError("retired v1 hard schedule differs from the amendment")
    return {
        "source_campaign_id": manifest["campaign_id"],
        "disposition": "excluded_from_all_measured_combined_inference",
        "block5_disposition": "operational_pilot_excluded",
        "block5_run_ids": [row["run_id"] for row in block5],
        "block5_schedule_sha256": _sha_bytes(_json_bytes(block5)),
        "all_retired_hard_run_ids": [row["run_id"] for row in retired],
        "all_retired_hard_schedule_sha256": _sha_bytes(_json_bytes(retired)),
        "artifact_inventory_policy": "not_read_or_imported",
    }


def build_standard_bundle(source: Path) -> dict:
    source = source.resolve()
    manifest, campaign_v1 = _source_manifest(source)
    schedule = _selected_schedule(manifest)
    rows = []
    evidence = {}
    for frozen_row in schedule:
        try:
            result = campaign_v1._validated_result(source, manifest, frozen_row)
        except (ValueError, SystemExit) as exc:
            raise BridgeError(
                f"{frozen_row.get('run_id')}: v1 row validation failed: {exc}"
            ) from exc
        rows.append(result)
        evidence[frozen_row["run_id"]] = _artifact_record(
            source, frozen_row, int(result["attempt"])
        )
    _validate_rows(rows)
    manifest_path = source / "campaign_manifest.json"
    manifest_sidecar = source / "campaign_manifest.sha256.json"
    frozen_standard_pool = (
        source / str(manifest["frozen_artifact_root"]) / "laptop" / "pool.json"
    )
    source_refs = {
        "campaign_validator": _file_ref(V1_CAMPAIGN_PATH),
        "frozen_analyzer": _file_ref(V1_ANALYZER_PATH),
        "infrastructure_classifier": _file_ref(V1_CLASSIFIER_PATH),
        "standard_bridge_validator": _file_ref(Path(__file__)),
        "recovery_amendment": _file_ref(AMENDMENT_PATH),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "study_id": STUDY_ID,
        "study": STUDY,
        "source_study": SOURCE_STUDY,
        "scheduled_denominator": EXPECTED_DENOMINATOR,
        "valid_runs": EXPECTED_DENOMINATOR,
        "source_campaign": {
            "path": str(source),
            "campaign_id": manifest["campaign_id"],
            "kind": manifest["kind"],
            "manifest": _file_ref(manifest_path),
            "manifest_sidecar": _file_ref(manifest_sidecar),
            "campaign_source_inventory_sha256": manifest[
                "campaign_source_inventory_sha256"
            ],
            "original_preregistration": dict(manifest["preregistration"]),
        },
        "recovery_amendment": _file_ref(AMENDMENT_PATH),
        "validator_source_inventory": source_refs,
        "validator_source_inventory_sha256": _sha_bytes(_json_bytes(source_refs)),
        "metric_policy": dict(manifest["metric_policy"]),
        "exact_expected_run_ids": [row["run_id"] for row in schedule],
        "source_schedule_rows": schedule,
        "source_schedule_sha256": _sha_bytes(_json_bytes(schedule)),
        "standard_rank_input": _file_ref(frozen_standard_pool),
        "all_applicable_safety_and_lossy_limit_touches_zero": True,
        "all_applicable_evaluate_result_bounds_not_near": True,
        "pilot_exclusion_record": _pilot_exclusion(manifest),
        "aggregates": _aggregates(rows),
        "runs": rows,
        "row_evidence": evidence,
        "canonical_rows_sha256": _sha_bytes(_json_bytes(rows)),
        "row_evidence_sha256": _sha_bytes(_json_bytes(evidence)),
    }


def freeze_standard(source: Path, output_dir: Path) -> Path:
    output_dir = output_dir.resolve()
    bundle_path = output_dir / "study_bundle.json"
    sidecar_path = output_dir / "study_bundle.sha256.json"
    if bundle_path.exists() or sidecar_path.exists():
        raise BridgeError(
            f"refusing to replace create-only artifact: {bundle_path}"
        )
    bundle = build_standard_bundle(source)
    _write_new(bundle_path, bundle)
    _write_new(sidecar_path, {
        "path": bundle_path.name,
        "sha256": _sha_file(bundle_path),
    })
    # Validate the serialized artifact, not just the in-memory object.
    validate_standard_bundle(bundle_path)
    return bundle_path


def validate_standard_bundle(bundle_path: Path) -> dict:
    bundle_path = bundle_path.resolve()
    bundle = _read_object(bundle_path, "Standard study bundle")
    sidecar_path = bundle_path.with_name("study_bundle.sha256.json")
    sidecar = _read_object(sidecar_path, "Standard bundle sidecar")
    if sidecar != {"path": bundle_path.name, "sha256": _sha_file(bundle_path)}:
        raise BridgeError("Standard bundle sidecar is invalid")
    if (
        bundle.get("schema_version") != SCHEMA_VERSION
        or bundle.get("kind") != KIND
        or bundle.get("study_id") != STUDY_ID
        or bundle.get("study") != STUDY
        or bundle.get("source_study") != SOURCE_STUDY
        or bundle.get("scheduled_denominator") != EXPECTED_DENOMINATOR
        or bundle.get("valid_runs") != EXPECTED_DENOMINATOR
        or bundle.get("all_applicable_safety_and_lossy_limit_touches_zero")
        is not True
        or bundle.get("all_applicable_evaluate_result_bounds_not_near") is not True
    ):
        raise BridgeError("Standard bundle identity/denominator contract is invalid")
    _validate_ref(bundle.get("recovery_amendment"), "recovery amendment")
    _amendment()
    inventory = bundle.get("validator_source_inventory")
    if not isinstance(inventory, dict):
        raise BridgeError("validator source inventory is absent")
    if bundle.get("validator_source_inventory_sha256") != _sha_bytes(
        _json_bytes(inventory)
    ):
        raise BridgeError("validator source inventory digest is invalid")
    for name, ref in inventory.items():
        _validate_ref(ref, f"validator source {name}")
    source_campaign = bundle.get("source_campaign")
    if not isinstance(source_campaign, dict):
        raise BridgeError("source campaign identity is absent")
    source_raw = source_campaign.get("path")
    if not isinstance(source_raw, str) or not source_raw:
        raise BridgeError("source campaign path is absent")
    source = Path(source_raw).resolve()
    manifest, campaign_v1 = _source_manifest(source)
    if (
        source_campaign.get("campaign_id") != manifest.get("campaign_id")
        or source_campaign.get("kind") != manifest.get("kind")
        or source_campaign.get("manifest")
        != _file_ref(source / "campaign_manifest.json")
        or source_campaign.get("manifest_sidecar")
        != _file_ref(source / "campaign_manifest.sha256.json")
        or source_campaign.get("campaign_source_inventory_sha256")
        != manifest.get("campaign_source_inventory_sha256")
        or source_campaign.get("original_preregistration")
        != manifest.get("preregistration")
    ):
        raise BridgeError("source campaign provenance differs from the bundle")
    schedule = _selected_schedule(manifest)
    expected_ids = [row["run_id"] for row in schedule]
    if (
        bundle.get("source_schedule_rows") != schedule
        or bundle.get("source_schedule_sha256") != _sha_bytes(_json_bytes(schedule))
        or bundle.get("exact_expected_run_ids") != expected_ids
    ):
        raise BridgeError("bundle schedule differs from the exact source schedule")
    if bundle.get("pilot_exclusion_record") != _pilot_exclusion(manifest):
        raise BridgeError("v1 pilot-exclusion record is invalid")
    rows = bundle.get("runs")
    evidence = bundle.get("row_evidence")
    if not isinstance(rows, list) or not isinstance(evidence, dict):
        raise BridgeError("canonical rows/evidence are malformed")
    _validate_rows(rows)
    if [row.get("run_id") for row in rows] != expected_ids:
        raise BridgeError("canonical row order/identity differs from schedule")
    if bundle.get("canonical_rows_sha256") != _sha_bytes(_json_bytes(rows)):
        raise BridgeError("canonical row digest is invalid")
    if bundle.get("row_evidence_sha256") != _sha_bytes(_json_bytes(evidence)):
        raise BridgeError("row-evidence digest is invalid")
    recomputed_rows = []
    recomputed_evidence = {}
    for frozen_row in schedule:
        run_id = frozen_row["run_id"]
        try:
            result = campaign_v1._validated_result(source, manifest, frozen_row)
        except (ValueError, SystemExit) as exc:
            raise BridgeError(f"{run_id}: source row no longer validates: {exc}") from exc
        recomputed_rows.append(result)
        recomputed_evidence[run_id] = _artifact_record(
            source, frozen_row, int(result["attempt"])
        )
    if rows != recomputed_rows:
        raise BridgeError("canonical row values differ from independent revalidation")
    if evidence != recomputed_evidence:
        raise BridgeError("row-level evidence inventory differs from source artifacts")
    if bundle.get("aggregates") != _aggregates(rows):
        raise BridgeError("reported aggregates differ from canonical rows")
    return bundle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser("freeze-standard")
    freeze.add_argument("--source-campaign", type=Path, default=DEFAULT_SOURCE)
    freeze.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    validate = subparsers.add_parser("validate-standard")
    validate.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "freeze-standard":
            path = freeze_standard(args.source_campaign, args.output_dir)
            print(f"STANDARD FREEZE PASS: {path}")
        else:
            bundle = validate_standard_bundle(args.bundle)
            print(
                "STANDARD VALIDATION PASS: "
                f"{bundle['valid_runs']}/{bundle['scheduled_denominator']} "
                f"rows, study_id={bundle['study_id']}"
            )
    except BridgeError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
