#!/usr/bin/env python3
"""Combine independently validated Standard-v1 and hard-v2 evidence.

The adapter does not pretend the rows came from one execution campaign.  It
retains each original row identity, adds explicit source provenance, and calls
the exact pre-run-frozen v1 statistical kernels.  Both source campaign row
validators are rerun before any combined artifact or effect is accepted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

POLICY_PATH = HERE / "combined_analysis_policy.json"
V1_ANALYZER_PATH = ROOT / "ablations" / "further_mode_ablation" / "analyze_results.py"
DEFAULT_STANDARD = (
    ROOT / "results" / "further_mode_ablation_standard_preserved_v1"
    / "study_bundle.json"
)
DEFAULT_OUTPUT = ROOT / "results" / "further_mode_ablation_combined_recovery_v1"

SCHEMA_VERSION = 1
COMBINED_KIND = "further_mode_ablation_cross_study_evidence_bundle"
EFFECTS_KIND = "further_mode_ablation_cross_study_exact_effects"
STANDARD_STUDY_ID = "further_mode_ablation_standard_v1_preserved"
HARD_STUDY_ID = "further_mode_ablation_hard_v2"
STANDARD_N = 128
HARD_N = 112
TOTAL_N = 240
EXPECTED_ANALYZER_SHA256 = (
    "1ce48c04fda4bbadd59fa2f1bfa959af400be649c7507292677d61498e13bd0c"
)
STANDARD_ARMS = {
    "clean", "abl-substrate", "abl-pin-d0", "abl-bury-d23",
    "abl-place-d23", "sponsored", "ranking", "promo", "trust",
    "scarcity", "drip", "addon", "abl-place-d30", "friction",
    "abl-place-d52", "combined",
}
HARD_ARMS = {
    *(f"{code}_combined_original" for code in "BPKEDCAF"),
    "B_clean_original", "F_clean_original", "B_format_only_original",
    "B_merchandising_original", "B_combined_reversed",
    "F_combined_reversed",
}


class CombineError(ValueError):
    """Fail-closed cross-study validation error."""


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
        raise CombineError(f"required file is absent: {path}")
    return {
        "path": str(path),
        "sha256": _sha_file(path),
        "size": path.stat().st_size,
    }


def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CombineError(f"{label} is absent or malformed: {path}") from exc
    if not isinstance(value, dict):
        raise CombineError(f"{label} is not a JSON object: {path}")
    return value


def _write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise CombineError(f"refusing to replace create-only artifact: {path}") from exc
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _validate_ref(ref: object, label: str) -> Path:
    if not isinstance(ref, dict):
        raise CombineError(f"{label} reference is malformed")
    raw = ref.get("path")
    if not isinstance(raw, str) or not raw:
        raise CombineError(f"{label} path is absent")
    path = Path(raw).resolve()
    if _file_ref(path) != ref:
        raise CombineError(f"{label} hash/size binding is invalid")
    return path


def _policy() -> dict:
    policy = _read_object(POLICY_PATH, "combined analysis policy")
    if (
        policy.get("schema_version") != 1
        or policy.get("kind")
        != "further_mode_ablation_cross_study_analysis_policy"
        or policy.get("standard_source", {}).get("study_id")
        != STANDARD_STUDY_ID
        or policy.get("hard_source", {}).get("study_id") != HARD_STUDY_ID
        or policy.get("standard_source", {}).get("scheduled_denominator")
        != STANDARD_N
        or policy.get("hard_source", {}).get("scheduled_denominator") != HARD_N
        or policy.get("combined_denominator") != TOTAL_N
        or policy.get("analyzer", {}).get("sha256")
        != EXPECTED_ANALYZER_SHA256
        or policy.get("analyzer", {}).get("total_effects") != 102
        or policy.get("recorded_before_hard_v2_outcomes") is not True
    ):
        raise CombineError("combined analysis policy identity is invalid")
    if _sha_file(V1_ANALYZER_PATH) != EXPECTED_ANALYZER_SHA256:
        raise CombineError("the already-frozen v1 analyzer source has drifted")
    return policy


def _validate_endpoint_rows(rows: Sequence[Mapping], n: int, label: str) -> None:
    if len(rows) != n or len({row.get("run_id") for row in rows}) != n:
        raise CombineError(f"{label} rows do not have the exact denominator")
    for row in rows:
        if row.get("hero") not in {0, 1}:
            raise CombineError(f"{label} {row.get('run_id')}: hero is not binary")
        if row.get("strict_binary") not in {0, 1, 0.0, 1.0}:
            raise CombineError(
                f"{label} {row.get('run_id')}: strict_binary is not binary"
            )
        value = row.get("preservation_strict")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0 <= float(value) <= 1
        ):
            raise CombineError(f"{label} {row.get('run_id')}: P* is invalid")


def _validate_partitions(
    standard_rows: Sequence[Mapping], hard_rows: Sequence[Mapping],
) -> None:
    _validate_endpoint_rows(standard_rows, STANDARD_N, "Standard")
    _validate_endpoint_rows(hard_rows, HARD_N, "hard-v2")
    standard_ids = {row["run_id"] for row in standard_rows}
    hard_ids = {row["run_id"] for row in hard_rows}
    if standard_ids & hard_ids:
        raise CombineError("source study run IDs overlap")
    if {row.get("arm_id") for row in standard_rows} != STANDARD_ARMS:
        raise CombineError("Standard arm partition differs from the frozen design")
    if {row.get("arm_id") for row in hard_rows} != HARD_ARMS:
        raise CombineError("hard-v2 arm partition differs from the frozen design")
    for rows, arms, label in (
        (standard_rows, STANDARD_ARMS, "Standard"),
        (hard_rows, HARD_ARMS, "hard-v2"),
    ):
        for arm in arms:
            repeats = sorted(
                row.get("repeat") for row in rows if row.get("arm_id") == arm
            )
            if repeats != list(range(1, 9)):
                raise CombineError(f"{label} {arm}: exact repetitions 1..8 absent")


def _standard_sidecar(bundle_path: Path) -> Path:
    return bundle_path.with_name("study_bundle.sha256.json")


def _hard_sidecar(bundle_path: Path) -> Path:
    return bundle_path.with_suffix(bundle_path.suffix + ".sha256.json")


def _independently_validate_hard(bundle_path: Path) -> tuple[dict, dict]:
    from ablations.further_mode_ablation_hard_v2 import campaign as hard_campaign

    try:
        bundle = hard_campaign.validate_study_bundle(bundle_path.resolve())
    except (ValueError, SystemExit) as exc:
        raise CombineError(f"hard-v2 bundle validation failed: {exc}") from exc
    source = bundle.get("source_campaign") or {}
    raw = source.get("path")
    if not isinstance(raw, str) or not raw:
        raise CombineError("hard-v2 source campaign path is absent")
    campaign_dir = Path(raw).resolve()
    try:
        manifest = hard_campaign.verify_campaign(campaign_dir, quiet=True)
    except (ValueError, SystemExit) as exc:
        raise CombineError(f"hard-v2 campaign validation failed: {exc}") from exc
    # verify_campaign returns the manifest in hard-v2.  Fail explicitly if a
    # future API changes that contract.
    if not isinstance(manifest, dict) or not isinstance(manifest.get("schedule"), list):
        raise CombineError("hard-v2 validator did not return a frozen manifest")
    recomputed = []
    for frozen_row in manifest["schedule"]:
        try:
            recomputed.append(
                hard_campaign._validated_result(campaign_dir, manifest, frozen_row)
            )
        except (ValueError, SystemExit) as exc:
            raise CombineError(
                f"hard-v2 {frozen_row.get('run_id')}: row evidence failed: {exc}"
            ) from exc
    if recomputed != bundle.get("rows"):
        raise CombineError(
            "hard-v2 canonical metrics differ from independent row revalidation"
        )
    return bundle, manifest


def _independently_validate_sources(
    standard_bundle_path: Path, hard_bundle_path: Path,
) -> tuple[dict, dict, dict]:
    from ablations.further_mode_ablation_recovery.standard_bridge import (
        validate_standard_bundle,
    )

    try:
        standard = validate_standard_bundle(standard_bundle_path.resolve())
    except (ValueError, SystemExit) as exc:
        raise CombineError(f"Standard bundle validation failed: {exc}") from exc
    hard, hard_manifest = _independently_validate_hard(hard_bundle_path)
    _validate_partitions(standard["runs"], hard["rows"])
    if standard.get("study_id") != STANDARD_STUDY_ID:
        raise CombineError("Standard source study identity differs")
    if hard.get("study_id") != HARD_STUDY_ID:
        raise CombineError("hard-v2 source study identity differs")
    if standard.get("pilot_exclusion_record", {}).get("disposition") != (
        "excluded_from_all_measured_combined_inference"
    ):
        raise CombineError("v1 hard pilot exclusion is absent")
    return standard, hard, hard_manifest


def _provenance_row(row: Mapping, *, study_id: str, bundle_ref: Mapping,
                    source_campaign_id: str) -> dict:
    copied = dict(row)
    copied["source_provenance"] = {
        "study_id": study_id,
        "source_campaign_id": source_campaign_id,
        "source_bundle_sha256": bundle_ref["sha256"],
        "original_run_id": row["run_id"],
        "original_row_sha256": _sha_bytes(_json_bytes(row)),
        "identity_fields_relabelled": False,
    }
    return copied


def build_payloads(
    standard_bundle_path: Path, hard_bundle_path: Path,
) -> tuple[dict, dict]:
    policy = _policy()
    standard_bundle_path = standard_bundle_path.resolve()
    hard_bundle_path = hard_bundle_path.resolve()
    standard, hard, hard_manifest = _independently_validate_sources(
        standard_bundle_path, hard_bundle_path
    )
    standard_ref = _file_ref(standard_bundle_path)
    hard_ref = _file_ref(hard_bundle_path)
    standard_sidecar_ref = _file_ref(_standard_sidecar(standard_bundle_path))
    hard_sidecar_ref = _file_ref(_hard_sidecar(hard_bundle_path))
    standard_rows = [
        _provenance_row(
            row,
            study_id=STANDARD_STUDY_ID,
            bundle_ref=standard_ref,
            source_campaign_id=standard["source_campaign"]["campaign_id"],
        )
        for row in standard["runs"]
    ]
    hard_rows = [
        _provenance_row(
            row,
            study_id=HARD_STUDY_ID,
            bundle_ref=hard_ref,
            source_campaign_id=hard["source_campaign"]["campaign_id"],
        )
        for row in hard["rows"]
    ]

    from ablations.further_mode_ablation import analyze_results as frozen_analyzer

    pool_ref = hard["rank_endpoint_inputs"]["pool"]
    preferences_ref = hard["rank_endpoint_inputs"]["preferences"]
    pool_path = _validate_ref(pool_ref, "hard-v2 frozen pool")
    preferences_path = _validate_ref(
        preferences_ref, "hard-v2 frozen preferences"
    )
    pool = json.loads(pool_path.read_text())
    preferences_root = json.loads(preferences_path.read_text())
    if (
        not isinstance(pool, list)
        or not isinstance(preferences_root, dict)
        or not isinstance(preferences_root.get("graded"), dict)
    ):
        raise CombineError("hard-v2 frozen rank inputs are malformed")
    for row in hard_rows:
        row.update(frozen_analyzer._rank_metrics(
            pool, preferences_root["graded"], row.get("chosen")
        ))
    combined_rows = [*standard_rows, *hard_rows]
    if len(combined_rows) != TOTAL_N:
        raise CombineError("combined denominator is not exactly 240")

    hard_amendment = hard_manifest.get("recovery_amendment")
    if not isinstance(hard_amendment, dict):
        raise CombineError("hard-v2 recovery amendment reference is absent")
    _validate_ref(hard_amendment, "hard-v2 recovery amendment")
    source_studies = {
        "standard_v1": {
            "study_id": STANDARD_STUDY_ID,
            "bundle": standard_ref,
            "bundle_sidecar": standard_sidecar_ref,
            "source_campaign": standard["source_campaign"],
            "recovery_amendment": standard["recovery_amendment"],
            "rows": STANDARD_N,
        },
        "hard_v2": {
            "study_id": HARD_STUDY_ID,
            "bundle": hard_ref,
            "bundle_sidecar": hard_sidecar_ref,
            "source_campaign": hard["source_campaign"],
            "source_report": hard["source_report"],
            "recovery_amendment": hard_amendment,
            "rows": HARD_N,
        },
    }
    combined = {
        "schema_version": SCHEMA_VERSION,
        "kind": COMBINED_KIND,
        "scheduled_denominator": TOTAL_N,
        "valid_runs": TOTAL_N,
        "standard_runs": STANDARD_N,
        "hard_runs": HARD_N,
        "source_studies": source_studies,
        "analysis_policy": _file_ref(POLICY_PATH),
        "frozen_analyzer": _file_ref(V1_ANALYZER_PATH),
        "combination_validator": _file_ref(Path(__file__)),
        "rank_endpoint_inputs": {
            "source_study_id": HARD_STUDY_ID,
            "pool": pool_ref,
            "preferences": preferences_ref,
        },
        "all_source_rows_independently_revalidated": True,
        "v1_hard_pilot_rows_included": 0,
        "runs": combined_rows,
        "canonical_rows_sha256": _sha_bytes(_json_bytes(combined_rows)),
    }
    effects = frozen_analyzer.compute_effects(
        combined_rows, include_directional_rank=True
    )
    effects["kind"] = EFFECTS_KIND
    effects["process_descriptives"] = (
        frozen_analyzer.compute_process_descriptives(combined_rows)
    )
    effects["source_studies"] = source_studies
    effects["analysis_policy"] = _file_ref(POLICY_PATH)
    effects["frozen_analyzer"] = _file_ref(V1_ANALYZER_PATH)
    effects["rank_endpoint_inputs"] = combined["rank_endpoint_inputs"]
    if len(effects.get("effects", [])) != 102:
        raise CombineError("frozen analyzer did not produce exactly 102 effects")
    return combined, effects


def freeze_combined(
    standard_bundle_path: Path, hard_bundle_path: Path, output_dir: Path,
) -> tuple[Path, Path]:
    output_dir = output_dir.resolve()
    combined_path = output_dir / "combined_row_bundle.json"
    combined_sidecar = output_dir / "combined_row_bundle.sha256.json"
    effects_path = output_dir / "combined_exact_effects.json"
    effects_sidecar = output_dir / "combined_exact_effects.sha256.json"
    if any(path.exists() for path in (
        combined_path, combined_sidecar, effects_path, effects_sidecar
    )):
        raise CombineError("refusing to replace a create-only combined artifact")
    combined, effects = build_payloads(standard_bundle_path, hard_bundle_path)
    _write_new(combined_path, combined)
    _write_new(combined_sidecar, {
        "path": combined_path.name,
        "sha256": _sha_file(combined_path),
    })
    effects["input_combined_bundle"] = _file_ref(combined_path)
    _write_new(effects_path, effects)
    _write_new(effects_sidecar, {
        "path": effects_path.name,
        "sha256": _sha_file(effects_path),
    })
    validate_combined(combined_path, effects_path)
    return combined_path, effects_path


def validate_combined(combined_path: Path, effects_path: Path) -> tuple[dict, dict]:
    combined_path = combined_path.resolve()
    effects_path = effects_path.resolve()
    combined = _read_object(combined_path, "combined row bundle")
    effects = _read_object(effects_path, "combined effects")
    if _read_object(
        combined_path.with_name("combined_row_bundle.sha256.json"),
        "combined row sidecar",
    ) != {"path": combined_path.name, "sha256": _sha_file(combined_path)}:
        raise CombineError("combined row bundle sidecar is invalid")
    if _read_object(
        effects_path.with_name("combined_exact_effects.sha256.json"),
        "combined effects sidecar",
    ) != {"path": effects_path.name, "sha256": _sha_file(effects_path)}:
        raise CombineError("combined effects sidecar is invalid")
    if (
        combined.get("kind") != COMBINED_KIND
        or combined.get("scheduled_denominator") != TOTAL_N
        or combined.get("valid_runs") != TOTAL_N
        or combined.get("all_source_rows_independently_revalidated") is not True
        or combined.get("v1_hard_pilot_rows_included") != 0
    ):
        raise CombineError("combined bundle identity/denominator is invalid")
    sources = combined.get("source_studies") or {}
    standard_ref = sources.get("standard_v1", {}).get("bundle")
    hard_ref = sources.get("hard_v2", {}).get("bundle")
    standard_path = _validate_ref(standard_ref, "combined Standard source bundle")
    hard_path = _validate_ref(hard_ref, "combined hard-v2 source bundle")
    expected_combined, expected_effects = build_payloads(standard_path, hard_path)
    if combined != expected_combined:
        raise CombineError("combined rows/provenance differ from source revalidation")
    expected_effects["input_combined_bundle"] = _file_ref(combined_path)
    if effects != expected_effects:
        raise CombineError("combined effects differ from frozen analyzer recomputation")
    return combined, effects


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    combine = subparsers.add_parser("combine-studies")
    combine.add_argument("--standard-bundle", type=Path, default=DEFAULT_STANDARD)
    combine.add_argument("--hard-bundle", type=Path, required=True)
    combine.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    validate = subparsers.add_parser("validate-combined")
    validate.add_argument("--combined-bundle", type=Path, required=True)
    validate.add_argument("--effects", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "combine-studies":
            combined, effects = freeze_combined(
                args.standard_bundle, args.hard_bundle, args.output_dir
            )
            print(f"COMBINED FREEZE PASS: {combined}")
            print(f"EXACT EFFECTS PASS: {effects}")
        else:
            combined, effects = validate_combined(
                args.combined_bundle, args.effects
            )
            print(
                "COMBINED VALIDATION PASS: "
                f"{combined['valid_runs']}/{combined['scheduled_denominator']} "
                f"rows, {len(effects['effects'])} exact effects"
            )
    except CombineError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

