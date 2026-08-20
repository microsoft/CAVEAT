#!/usr/bin/env python
"""Frozen, resumable A/B campaign for the CAVEAT-Harness scaffold.

The measured matrix is intentionally exact:

* gpt-5.6-terra-low, five original CAVEAT-Shop scenarios, graded/combined, n=3;
* gpt-5.6-terra-low, the same scenarios, graded/clean, n=1;
* GPT-5.6-sol high, five canonical hard scenarios, graded/combined, n=2;
* fresh ``browseruse`` and ``caveat-harness`` arms in every stratum.

Every CLI launch contains one run.  This makes the manifest the only source of
truth for pairing and permits create-only receipts and attempt preservation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent
CAVEAT_SHOP_DATA_RELATIVE = Path("caveat") / "envs" / "caveat_shop" / "data"
CAVEAT_SHOP_DATA_ROOT = ROOT / CAVEAT_SHOP_DATA_RELATIVE
FROZEN_CAVEAT_SHOP_DATA_RELATIVE = Path("frozen_inputs") / CAVEAT_SHOP_DATA_RELATIVE
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from freeze_hard_campaign import _validate_certification  # noqa: E402
from hard_campaign_runtime import (  # noqa: E402
    AGENT_OUTPUT_VALIDATION_FEEDBACK_RENDERING_CONFIGURATION,
    CAPS,
    _runtime_environment_policy,
    _validate_caps,
    code_inventory,
    runtime_dependency_manifest,
    runtime_limit_contract,
    validate_limit_audit,
)


SCHEMA_VERSION = 3
KIND = "caveat_harness_ab_campaign"
EASY_SCENARIOS = (
    "laptop",
    "office_chair",
    "mattress",
    "backpack",
    "tent",
)
HARD_SCENARIOS = tuple(f"{scenario}_hard" for scenario in EASY_SCENARIOS)
ARMS = (
    ("baseline", "browseruse"),
    ("caveat_harness", "caveat-harness"),
)
VARIANT = "graded"
WEAK_REQUEST = "gpt-5.6-terra#low"
WEAK_RECORDED = "gpt-5.6-terra-low"
WEAK_LOGICAL = "gpt-5.6-terra"
SOL_REQUEST = "gpt-5.6-sol#high"
SOL_RECORDED = "gpt-5.6-sol-high"
SOL_LOGICAL = "gpt-5.6-sol"
SUPPORTED_TRAPI_REGIONS = (
    "gcr/shared",
    "msraif/shared",
    "redmond/interactive",
)
# These are only the library/API defaults used by tests and schedule
# inspection. ``prepare`` requires an explicit subset/order for each model,
# selected from fresh pre-freeze health probes, then stores both immutable
# routing contracts in the manifest. This avoids baking a stale one-day health
# observation into source.
WEAK_REGIONS = SUPPORTED_TRAPI_REGIONS
SOL_REGIONS = SUPPORTED_TRAPI_REGIONS
PROTECTED_PORT_LOW = 13200
PROTECTED_PORT_HIGH = 13299
REFILL_MODELS = ("Qwen3.5-122B", "Kimi-K2.6")
REFILL_PROFILE_MATRIX = "overhaul_lb_matrix"
REFILL_PROFILE_QWEN_CAPFREE = "qwen_capfree_topup_20260728"
REFILL_MAX_TOTAL_JOBS = 22
REFILL_MAX_LISTENERS = 22
MACHINE_BROWSER_FLOOR = 32
CAMPAIGN_BLOCK_RUNS = 10
CAMPAIGN_MAX_PARALLEL_RUNS = 4
SCHEDULE_RANDOMIZATION_SEED = "caveat-harness-ab-v8-20260728"
LIMIT_NEAR_FRACTION = 0.25
CAVEAT_HARNESS_DIAGNOSTIC_FIELDS = (
    "contract_compile_calls",
    "contract_sha256",
    "contract_canonical_json",
    "constraint_count",
    "objective_count",
    "search_mode",
    "decision_checkpoint_calls",
    "decision_checkpoint_rejections",
    "decision_checkpoint_approvals",
    "checkpoint_candidate_count",
    "frontier_inspected_count",
    "frontier_advertised_count",
    "frontier_coverage_mode",
    "frontier_advertised_page_count",
    "frontier_enumerated_page_count",
    "approved_candidate_id",
    "structured_max_attempts_observed",
    "structured_attempt_exhaustions",
    "auxiliary_calls",
    "auxiliary_tokens",
    "auxiliary_seconds",
    "limit_observations",
)
PROBE_MAX_AGE_SECONDS = 3_600
PROBE_PREDECESSOR_BLOCKS = {
    "weak_before": [],
    "weak_mid": [1, 2],
    "sol_before": [1, 2, 3, 4],
    "sol_mid": [1, 2, 3, 4, 5],
}
LOCKDIFF_CONDITIONS = (
    "clean",
    "sponsored",
    "ranking",
    "drip",
    "promo",
    "addon",
    "scarcity",
    "trust",
    "friction",
    "combined",
)
SMOKE_SPECS = {
    "weak_easy": {
        "scenario": "laptop",
        "condition": "combined",
        "variant": VARIANT,
        "scaffold": "caveat-harness",
        "model_request": WEAK_REQUEST,
        "model_recorded": WEAK_RECORDED,
        "logical_model": WEAK_LOGICAL,
        "quantity": 1,
    },
    "sol_high_hard": {
        "scenario": "laptop_hard",
        "condition": "combined",
        "variant": VARIANT,
        "scaffold": "caveat-harness",
        "model_request": SOL_REQUEST,
        "model_recorded": SOL_RECORDED,
        "logical_model": SOL_LOGICAL,
        "quantity": 1,
    },
}
SMOKE_POLICY = {
    "gate_required_before_confirmatory_launch": True,
    "create_only_launcher_required": True,
    "smokes": SMOKE_SPECS,
    # The smokes are operational integration checks. A completed wrong order
    # is valid smoke evidence and must not be filtered by its optimal-selection value.
    "optimal_selection_gate": None,
    "operational_gate": {
        "optimal_selection_value_neutral": True,
        "nonempty_choice_required": True,
        "required_evaluation_fields": ["optimal_selection"],
        # Purchase completion is established out of band from evaluator
        # state.  The gate intentionally knows nothing about scaffold action
        # names, checkout controls, receipt wording, or agent-side
        # transaction diagnostics.
        # This is an operational check, not a hidden metric threshold. A
        # steered add-on or another extra line item is behavioral evidence and
        # remains part of the measured basket; it must not make a completed
        # smoke look like an infrastructure failure.  Independently require
        # only that the evaluator-selected item was actually placed once at
        # the requested quantity in one unambiguous new order.
        "evaluator_selected_item_exact_quantity_required": True,
        "additional_line_items_do_not_affect_operational_gate": True,
    },
    "purpose": "excluded operational integration checks, never measured runs",
}
ABANDONMENT_MARKER_NAME = "ABANDONED.json"
ABANDONMENT_ATTESTATION_NAME = "ABANDONED.attestation.json"
ABANDONMENT_ATTESTATION_HASH_NAME = (
    "ABANDONED.attestation.sha256.json"
)
PRELAUNCH_SUPERSESSION_MARKER_NAME = "SUPERSEDED_PRELAUNCH.json"
PRELAUNCH_SUPERSESSION_ATTESTATION_NAME = (
    "SUPERSEDED_PRELAUNCH.attestation.json"
)
PRELAUNCH_SUPERSESSION_ATTESTATION_HASH_NAME = (
    "SUPERSEDED_PRELAUNCH.attestation.sha256.json"
)
RETIREMENT_POLICY = {
    "all_scheduled_runs_excluded_from_confirmatory_effect_estimates": True,
    "entire_campaign_pilot_only": True,
    "completed_runs_retained_as_pilot_evidence": True,
    "partial_runs_retained_as_failure_evidence": True,
    "unlaunched_runs_retained_as_unlaunched_evidence": True,
    "selective_redraw_prohibited": True,
    "all_later_launches_prohibited": True,
    "reporting_and_reevaluation_prohibited": True,
    "refill_archival_and_relaunch_prohibited": True,
    "successor_requires_new_source_freeze_and_fresh_smokes": True,
}
PRELAUNCH_SUPERSESSION_POLICY = {
    "all_scheduled_runs_verified_unlaunched": True,
    "all_scheduled_runs_excluded_from_confirmatory_effect_estimates": True,
    "any_excluded_smokes_retained_as_pilot_failure_evidence": True,
    "selective_redraw_prohibited": True,
    "all_later_launches_prohibited": True,
    "reporting_and_reevaluation_prohibited": True,
    "refill_archival_and_relaunch_prohibited": True,
    "successor_requires_new_source_freeze_and_fresh_smokes": True,
}


def _utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is missing/malformed")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is missing/malformed") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a UTC offset")
    return parsed.astimezone(dt.timezone.utc)


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


_ACTION_ERROR_FIXED_RECORD = (
    "agent_output_validation_feedback_rendering"
)
_ACTION_ERROR_BUCKETS = (
    "all",
    "agent_output_validation",
    "other_or_unknown",
)


def validate_action_error_partition(
    trajectory_stats: object,
    context_cap_audit: object,
    limit_categories: object,
    frozen_limit_contract: object,
) -> tuple[dict, list[str]]:
    """Validate the prospective provenance split for action-error rendering.

    Older frozen contracts do not declare the fixed rendering record and keep
    their original all-errors-are-lossy interpretation.  Once the record is
    declared, every raw count must reconcile with the detailed provenance
    audit and with both the fixed and residual-lossy limit records.  Anything
    absent, malformed, or not provenance-known therefore remains fail-closed.
    """

    frozen_categories = (
        frozen_limit_contract.get("categories")
        if isinstance(frozen_limit_contract, dict) else None
    )
    frozen_fixed = (
        frozen_categories.get("fixed_architecture")
        if isinstance(frozen_categories, dict) else None
    )
    declaration = (
        frozen_fixed.get(_ACTION_ERROR_FIXED_RECORD)
        if isinstance(frozen_fixed, dict) else None
    )
    if declaration is None:
        return {}, []

    errors: list[str] = []
    configured = (
        declaration.get("configured")
        if isinstance(declaration, dict) else None
    )
    if (
        configured
        != AGENT_OUTPUT_VALIDATION_FEEDBACK_RENDERING_CONFIGURATION
    ):
        errors.append(
            "frozen action-error rendering configuration is not exact"
        )
    trigger = (
        configured.get("trigger_chars")
        if isinstance(configured, dict) else None
    )
    if type(trigger) is not int or trigger < 0:
        errors.append("frozen action-error trigger is malformed")
        trigger = 20_000
    if (
        not isinstance(configured, dict)
        or configured.get("comparison") != ">"
    ):
        errors.append("frozen action-error comparison is not exact")

    stats = trajectory_stats if isinstance(trajectory_stats, dict) else {}
    audit = stats.get("action_error_audit")
    expected_top = {
        "schema_version", "complete", "error", *_ACTION_ERROR_BUCKETS,
    }
    expected_bucket = {"count", "over_cap_count", "max_chars"}
    if not isinstance(audit, dict) or set(audit) != expected_top:
        return {}, errors + [
            "action-error provenance audit top-level schema is not exact"
        ]
    if audit.get("schema_version") != 1:
        errors.append("action-error provenance schema_version is not 1")
    if audit.get("complete") is not True:
        errors.append("action-error provenance audit is incomplete")
    audit_error = audit.get("error")
    if audit_error is not None and not isinstance(audit_error, str):
        errors.append("action-error provenance error field is malformed")
    if audit.get("complete") is True and audit_error is not None:
        errors.append(
            "complete action-error provenance audit has a non-null error"
        )

    buckets: dict[str, dict[str, int]] = {}
    for name in _ACTION_ERROR_BUCKETS:
        bucket = audit.get(name)
        if not isinstance(bucket, dict) or set(bucket) != expected_bucket:
            errors.append(
                f"action-error provenance bucket is malformed: {name}"
            )
            continue
        if any(
            type(bucket.get(key)) is not int or bucket[key] < 0
            for key in expected_bucket
        ):
            errors.append(
                f"action-error provenance counters are malformed: {name}"
            )
            continue
        count = bucket["count"]
        over = bucket["over_cap_count"]
        maximum = bucket["max_chars"]
        if over > count:
            errors.append(
                f"action-error over-cap count exceeds count: {name}"
            )
        if count == 0 and maximum != 0:
            errors.append(
                f"empty action-error bucket has nonzero maximum: {name}"
            )
        if (
            (over == 0 and maximum > trigger)
            or (over > 0 and maximum <= trigger)
        ):
            errors.append(
                f"action-error boundary counters disagree: {name}"
            )
        buckets[name] = dict(bucket)

    if len(buckets) != len(_ACTION_ERROR_BUCKETS):
        return dict(audit), errors
    all_errors = buckets["all"]
    known = buckets["agent_output_validation"]
    unknown = buckets["other_or_unknown"]
    if all_errors["count"] != known["count"] + unknown["count"]:
        errors.append("action-error counts do not partition all errors")
    if (
        all_errors["over_cap_count"]
        != known["over_cap_count"] + unknown["over_cap_count"]
    ):
        errors.append(
            "action-error over-cap counts do not partition all errors"
        )
    if all_errors["max_chars"] != max(
        known["max_chars"], unknown["max_chars"]
    ):
        errors.append("action-error maxima do not partition all errors")

    context_limits = (
        context_cap_audit.get("limits")
        if isinstance(context_cap_audit, dict) else None
    )
    raw = (
        context_limits.get("action_error_chars")
        if isinstance(context_limits, dict) else None
    )
    if (
        not isinstance(raw, dict)
        or set(raw) != {
            "configured", "touched_count", "max_observed",
        }
    ):
        errors.append(
            "raw action_error_chars audit record is absent or malformed"
        )
        raw = {}
    if raw.get("configured") != trigger:
        errors.append("raw action-error configuration disagrees")
    if raw.get("touched_count") != all_errors["over_cap_count"]:
        errors.append("raw and detailed action-error touches disagree")
    if raw.get("max_observed") != all_errors["max_chars"]:
        errors.append("raw and detailed action-error maxima disagree")

    categories = limit_categories if isinstance(limit_categories, dict) else {}
    observed_fixed = categories.get("fixed_architecture")
    fixed = (
        observed_fixed.get(_ACTION_ERROR_FIXED_RECORD)
        if isinstance(observed_fixed, dict) else None
    )
    observed_lossy = categories.get("lossy_context_limits")
    lossy = (
        observed_lossy.get("action_error_chars")
        if isinstance(observed_lossy, dict) else None
    )
    if not isinstance(fixed, dict):
        errors.append("fixed action-error rendering record is absent")
        fixed = {}
    if not isinstance(lossy, dict):
        errors.append("residual lossy action-error record is absent")
        lossy = {}

    expected_fixed_observations = {
        "runtime_configured": configured,
        "classification_complete": True,
        "raw_context_audit_name": "action_error_chars",
        "all_error_count": all_errors["count"],
        "all_over_cap_count": all_errors["over_cap_count"],
        "all_max_chars": all_errors["max_chars"],
        "agent_output_validation_count": known["count"],
        "agent_output_validation_over_cap_count": known[
            "over_cap_count"
        ],
        "agent_output_validation_max_chars": known["max_chars"],
        "other_or_unknown_count": unknown["count"],
        "other_or_unknown_over_cap_count": unknown["over_cap_count"],
        "other_or_unknown_max_chars": unknown["max_chars"],
    }
    expected_lossy_observations = {
        "classification_complete": True,
        "raw_touched_count": all_errors["over_cap_count"],
        "raw_max_observed": all_errors["max_chars"],
        "other_or_unknown_count": unknown["count"],
        "other_or_unknown_touched_count": unknown["over_cap_count"],
        "max_observed": unknown["max_chars"],
        "classified_agent_output_validation_count": known["count"],
        "classified_agent_output_validation_touched_count": known[
            "over_cap_count"
        ],
        "classified_agent_output_validation_max_observed": known[
            "max_chars"
        ],
    }
    if fixed.get("configured") != configured:
        errors.append("fixed action-error configuration disagrees")
    if fixed.get("touched_count") != known["over_cap_count"]:
        errors.append("fixed and detailed action-error touches disagree")
    if fixed.get("observations") != expected_fixed_observations:
        errors.append("fixed action-error observations are not cross-bound")
    if lossy.get("configured") != trigger:
        errors.append("lossy action-error configuration disagrees")
    if lossy.get("touched_count") != unknown["over_cap_count"]:
        errors.append("lossy and detailed action-error touches disagree")
    if lossy.get("observations") != expected_lossy_observations:
        errors.append("lossy action-error observations are not cross-bound")
    return dict(audit), errors


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _contract_diagnostic_error(diagnostics: object) -> str | None:
    """Cross-check the persisted literal contract against its diagnostics."""

    if not isinstance(diagnostics, dict):
        return "diagnostics are not an object"
    raw = diagnostics.get("contract_canonical_json")
    if not isinstance(raw, str) or not raw:
        return "contract_canonical_json is missing"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return "contract_canonical_json is not JSON"
    if (
        not isinstance(parsed, dict)
        or set(parsed) != {
            "constraints", "instruction", "objectives", "search_mode",
        }
        or not isinstance(parsed["instruction"], str)
        or not parsed["instruction"].strip()
        or not isinstance(parsed["constraints"], list)
        or not isinstance(parsed["objectives"], list)
        or parsed["search_mode"] not in {"best_available", "satisfice"}
    ):
        return "contract_canonical_json has an invalid TaskContract shape"
    encoded = _json_bytes(parsed)
    if raw.encode("utf-8") != encoded:
        return "contract_canonical_json is not canonical"
    fingerprint = hashlib.sha256(
        len(encoded).to_bytes(8, "big") + encoded
    ).hexdigest()
    if fingerprint != diagnostics.get("contract_sha256"):
        return "contract_canonical_json fingerprint disagrees"
    if (
        len(parsed["constraints"]) != diagnostics.get("constraint_count")
        or len(parsed["objectives"]) != diagnostics.get("objective_count")
        or parsed["search_mode"] != diagnostics.get("search_mode")
    ):
        return "contract_canonical_json summary disagrees"
    return None


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path):
    return json.loads(path.read_text())


def _write_new(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise SystemExit(f"refusing to replace create-only file: {path}") from exc


def _safe_id(raw: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-")
    if not value:
        raise SystemExit("campaign id is empty after sanitization")
    return value


def _tree_inventory(root: Path) -> dict[str, dict]:
    if not root.is_dir():
        raise SystemExit(f"inventory root missing: {root}")
    records = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        records[str(path.relative_to(root))] = {
            "sha256": _sha_file(path),
            "size": path.stat().st_size,
        }
    return records


def _inventory_sha(value: dict) -> str:
    return _sha_bytes(_json_bytes(value))


def _file_ref(path: Path) -> dict:
    return {
        "path": str(path.resolve()),
        "sha256": _sha_file(path),
        "size": path.stat().st_size,
    }


def _campaign_file_ref(campaign_dir: Path, path: Path) -> dict:
    return {
        "path": str(path.resolve().relative_to(campaign_dir.resolve())),
        "sha256": _sha_file(path),
        "size": path.stat().st_size,
    }


def _load_bound_manifest(campaign_dir: Path) -> dict:
    """Load only the immutable manifest binding, without current-source checks."""

    campaign_dir = campaign_dir.resolve()
    manifest_path = campaign_dir / "campaign_manifest.json"
    sha_path = campaign_dir / "campaign_manifest.sha256.json"
    if not manifest_path.is_file() or not sha_path.is_file():
        raise ValueError("campaign is not frozen")
    expected = {
        "path": "campaign_manifest.json",
        "sha256": _sha_file(manifest_path),
    }
    try:
        sha_record = _read_json(sha_path)
        manifest = _read_json(manifest_path)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"campaign manifest binding is unreadable: {exc}") from exc
    if sha_record != expected:
        raise ValueError("campaign manifest hash binding is invalid")
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("kind") != KIND
        or manifest.get("campaign_id") != _safe_id(
            str(manifest.get("campaign_id") or "")
        )
        or not isinstance(manifest.get("schedule"), list)
    ):
        raise ValueError("campaign manifest identity/schema is invalid")
    return manifest


def _abandonment_paths(campaign_dir: Path) -> tuple[Path, Path, Path]:
    campaign_dir = campaign_dir.resolve()
    return (
        campaign_dir / ABANDONMENT_MARKER_NAME,
        campaign_dir / ABANDONMENT_ATTESTATION_NAME,
        campaign_dir / ABANDONMENT_ATTESTATION_HASH_NAME,
    )


def _prelaunch_supersession_paths(
    campaign_dir: Path,
) -> tuple[Path, Path, Path]:
    campaign_dir = campaign_dir.resolve()
    return (
        campaign_dir / PRELAUNCH_SUPERSESSION_MARKER_NAME,
        campaign_dir / PRELAUNCH_SUPERSESSION_ATTESTATION_NAME,
        campaign_dir / PRELAUNCH_SUPERSESSION_ATTESTATION_HASH_NAME,
    )


def _disposition_paths(
    campaign_dir: Path,
) -> tuple[Path, Path, Path]:
    """Select exactly one published administrative disposition."""

    abandonment = _abandonment_paths(campaign_dir)
    prelaunch = _prelaunch_supersession_paths(campaign_dir)
    has_abandonment = abandonment[0].exists()
    has_prelaunch = prelaunch[0].exists()
    if has_abandonment and has_prelaunch:
        raise ValueError(
            "campaign has conflicting retirement and prelaunch markers"
        )
    return prelaunch if has_prelaunch else abandonment


def reject_abandoned(campaign_dir: Path, action: str) -> None:
    """Fail closed before confirmatory/report work on a retired freeze."""

    abandonment_paths = _abandonment_paths(campaign_dir)
    prelaunch_paths = _prelaunch_supersession_paths(campaign_dir)
    marker = next(
        (
            path
            for path in (*abandonment_paths, *prelaunch_paths)
            if path.exists()
        ),
        None,
    )
    if marker is None:
        return
    campaign_id = Path(campaign_dir).resolve().name
    status = "unknown"
    try:
        record = _read_json(marker)
        campaign_id = str(record.get("campaign_id") or campaign_id)
        status = str(record.get("status") or status)
    except Exception:  # noqa: BLE001
        pass
    disposition = (
        "ABANDONED"
        if marker in abandonment_paths
        else "SUPERSEDED PRELAUNCH"
    )
    raise SystemExit(
        f"campaign {campaign_id} is {disposition} ({status}); "
        f"{action} is prohibited"
    )


def _validate_legacy_abandonment_marker(
    campaign_dir: Path,
    manifest: dict,
) -> dict:
    marker, _, _ = _abandonment_paths(campaign_dir)
    if not marker.is_file():
        raise ValueError("ABANDONED marker is missing")
    try:
        record = _read_json(marker)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"ABANDONED marker is unreadable: {exc}") from exc
    required_policy = {
        "all_v4_runs_excluded_from_confirmatory_effect_estimates": True,
        "completed_runs_retained_as_pilot_evidence": True,
        "partial_runs_retained_as_failure_evidence": True,
        "selective_redraw_prohibited": True,
        "later_blocks_prohibited": True,
        "successor_requires_new_source_freeze_and_fresh_smokes": True,
    }
    if (
        record.get("schema_version") != 1
        or record.get("kind") != "campaign_abandonment"
        or record.get("campaign_id") != manifest["campaign_id"]
        or record.get("manifest_sha256") != _sha_file(
            campaign_dir / "campaign_manifest.json"
        )
        or record.get("status") != "pilot_only_never_confirmatory"
        or record.get("policy") != required_policy
        or not isinstance(record.get("reason"), str)
        or not record["reason"].strip()
    ):
        raise ValueError("ABANDONED marker contract/binding is invalid")
    _parse_utc(record.get("abandoned_at_utc"), "abandoned_at_utc")
    observed = record.get("observed_block_1")
    if not isinstance(observed, dict):
        raise ValueError("ABANDONED marker lacks block-1 evidence")
    partial = observed.get("terminated_partial_runs")
    if (
        observed.get("scheduled_runs") != 10
        or type(observed.get("completed_summaries")) is not int
        or not isinstance(partial, list)
        or any(
            not isinstance(item, dict)
            or set(item) != {"run_id", "reason"}
            or not isinstance(item["run_id"], str)
            or not isinstance(item["reason"], str)
            or not item["reason"].strip()
            for item in partial
        )
        or len({item["run_id"] for item in partial}) != len(partial)
    ):
        raise ValueError("ABANDONED block-1 disposition is malformed")
    return record


def _validate_retirement_marker(
    campaign_dir: Path,
    manifest: dict,
) -> dict:
    marker, _, _ = _abandonment_paths(campaign_dir)
    if not marker.is_file():
        raise ValueError("ABANDONED marker is missing")
    try:
        record = _read_json(marker)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"ABANDONED marker is unreadable: {exc}") from exc
    manifest_path = campaign_dir / "campaign_manifest.json"
    if (
        record.get("schema_version") != 2
        or record.get("kind") != "campaign_retirement"
        or record.get("campaign_id") != manifest["campaign_id"]
        or record.get("manifest") != _campaign_file_ref(
            campaign_dir, manifest_path
        )
        or record.get("status") != "pilot_only_never_confirmatory"
        or record.get("confirmatory_eligible") is not False
        or record.get("redraw_eligible") is not False
        or record.get("policy") != RETIREMENT_POLICY
        or not isinstance(record.get("reason"), str)
        or not record["reason"].strip()
    ):
        raise ValueError("ABANDONED retirement marker contract/binding is invalid")
    _parse_utc(record.get("retired_at_utc"), "retired_at_utc")
    return record


def _validate_abandonment_marker(
    campaign_dir: Path,
    manifest: dict,
) -> dict:
    """Validate either the historical V4 marker or a generic retirement."""

    marker, _, _ = _abandonment_paths(campaign_dir)
    if not marker.is_file():
        raise ValueError("ABANDONED marker is missing")
    try:
        record = _read_json(marker)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"ABANDONED marker is unreadable: {exc}") from exc
    if (
        record.get("schema_version") == 1
        and record.get("kind") == "campaign_abandonment"
    ):
        return _validate_legacy_abandonment_marker(campaign_dir, manifest)
    if (
        record.get("schema_version") == 2
        and record.get("kind") == "campaign_retirement"
    ):
        return _validate_retirement_marker(campaign_dir, manifest)
    raise ValueError("ABANDONED marker schema/kind is unsupported")


def _validate_receipt_hash(receipt: Path, sidecar: Path) -> None:
    if not receipt.is_file() or not sidecar.is_file():
        raise ValueError("launched run lacks a receipt/hash pair")
    expected = {"path": receipt.name, "sha256": _sha_file(receipt)}
    try:
        actual = _read_json(sidecar)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"receipt hash sidecar is unreadable: {exc}") from exc
    if actual != expected:
        raise ValueError("receipt hash sidecar is invalid")


def _build_legacy_abandonment_attestation(campaign_dir: Path) -> dict:
    """Derive an immutable V4 pilot disposition from manifest-named evidence."""

    campaign_dir = campaign_dir.resolve()
    manifest = _load_bound_manifest(campaign_dir)
    marker = _validate_legacy_abandonment_marker(campaign_dir, manifest)
    block_rows = [
        row for row in manifest["schedule"] if row.get("block") == 1
    ]
    if len(block_rows) != 10:
        raise ValueError("abandoned campaign block 1 is not ten exact runs")
    partial_reasons = {
        item["run_id"]: item["reason"]
        for item in marker["observed_block_1"]["terminated_partial_runs"]
    }
    completed_ids = {
        row["run_id"]
        for row in block_rows
        if (campaign_dir / row["summary_relpath"]).is_file()
    }
    partial_ids = {row["run_id"] for row in block_rows} - completed_ids
    if (
        len(completed_ids)
        != marker["observed_block_1"]["completed_summaries"]
        or partial_ids != set(partial_reasons)
        or len(completed_ids) != 7
        or len(partial_ids) != 3
    ):
        raise ValueError(
            "ABANDONED completed/partial disposition differs from evidence"
        )
    run_records = []
    for row in block_rows:
        experiment = campaign_dir / row["experiment_relpath"]
        launcher = campaign_dir / row["launcher_log_relpath"]
        receipt = (
            campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
        )
        receipt_hash = (
            campaign_dir
            / "launch_receipts"
            / f"{row['run_id']}.sha256.json"
        )
        if not experiment.is_dir() or not launcher.is_file():
            raise ValueError(f"{row['run_id']}: launched evidence is incomplete")
        _validate_receipt_hash(receipt, receipt_hash)
        try:
            receipt_record = _read_json(receipt)
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"{row['run_id']}: receipt is unreadable: {exc}"
            ) from exc
        for key in ("run_id", "block", "scenario", "arm"):
            if receipt_record.get(key) != row[key]:
                raise ValueError(
                    f"{row['run_id']}: receipt {key} differs from schedule"
                )
        trajectory = campaign_dir / row["trajectory_relpath"]
        if row["run_id"] in completed_ids and not trajectory.is_file():
            raise ValueError(f"{row['run_id']}: completed run lacks trajectory")
        if row["run_id"] in partial_ids and (
            (campaign_dir / row["summary_relpath"]).exists()
            or trajectory.exists()
        ):
            raise ValueError(
                f"{row['run_id']}: partial disposition has terminal artifacts"
            )
        inventory = _tree_inventory(experiment)
        run_records.append({
            "run_id": row["run_id"],
            "block": row["block"],
            "scenario": row["scenario"],
            "arm": row["arm"],
            "disposition": (
                "completed_pilot"
                if row["run_id"] in completed_ids
                else "terminated_partial_pilot"
            ),
            "reason": partial_reasons.get(row["run_id"]),
            "experiment": {
                "path": row["experiment_relpath"],
                "files": inventory,
                "files_sha256": _inventory_sha(inventory),
            },
            "launcher_log": _campaign_file_ref(
                campaign_dir, launcher
            ),
            "launch_receipt": _campaign_file_ref(
                campaign_dir, receipt
            ),
            "launch_receipt_hash": _campaign_file_ref(
                campaign_dir, receipt_hash
            ),
        })
    later_material = []
    for row in manifest["schedule"]:
        if row.get("block") == 1:
            continue
        candidates = (
            campaign_dir / row["experiment_relpath"],
            campaign_dir / row["launcher_log_relpath"],
            campaign_dir / "launch_receipts" / f"{row['run_id']}.json",
            campaign_dir / "launch_receipts"
            / f"{row['run_id']}.sha256.json",
        )
        later_material.extend(
            str(path.relative_to(campaign_dir))
            for path in candidates if path.exists()
        )
    if later_material:
        raise ValueError(
            "later-block evidence exists after abandonment: "
            + ", ".join(sorted(later_material)[:10])
        )
    marker_path, _, _ = _abandonment_paths(campaign_dir)
    return {
        "schema_version": 1,
        "kind": "campaign_abandonment_attestation",
        "campaign_id": manifest["campaign_id"],
        "manifest": _campaign_file_ref(
            campaign_dir, campaign_dir / "campaign_manifest.json"
        ),
        "abandonment_marker": _campaign_file_ref(
            campaign_dir, marker_path
        ),
        "status": marker["status"],
        "confirmatory_eligible": False,
        "scheduled_runs": len(manifest["schedule"]),
        "launched_runs": len(block_rows),
        "completed_pilot_runs": len(completed_ids),
        "partial_pilot_runs": len(partial_ids),
        "unlaunched_later_runs": len(manifest["schedule"]) - len(block_rows),
        "run_artifacts": run_records,
        "run_artifacts_sha256": _sha_bytes(_json_bytes(run_records)),
    }


def _retirement_child(
    campaign_dir: Path,
    relative: object,
    label: str,
) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{label} path is missing/malformed")
    candidate = Path(relative)
    if candidate.is_absolute():
        raise ValueError(f"{label} path must be campaign-relative")
    root = campaign_dir.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} path escapes campaign directory") from exc
    return resolved


def _retirement_artifact_ref(
    campaign_dir: Path,
    path: Path,
    label: str,
) -> dict | None:
    if path.is_symlink():
        raise ValueError(f"{label} may not be a symbolic link")
    if not path.exists():
        return None
    if path.is_file():
        return {
            "kind": "file",
            **_campaign_file_ref(campaign_dir, path),
        }
    if path.is_dir():
        inventory = _tree_inventory(path)
        return {
            "kind": "directory",
            "path": str(path.resolve().relative_to(campaign_dir.resolve())),
            "files": inventory,
            "files_sha256": _inventory_sha(inventory),
        }
    raise ValueError(f"{label} has an unsupported filesystem type")


def _retirement_run_inventory(
    campaign_dir: Path,
    manifest: dict,
) -> tuple[list[dict], dict[str, list[str]]]:
    """Classify every scheduled ID without assigning redraw eligibility."""

    schedule = manifest["schedule"]
    run_ids = [
        row.get("run_id") if isinstance(row, dict) else None
        for row in schedule
    ]
    if (
        any(not isinstance(run_id, str) or not run_id for run_id in run_ids)
        or len(run_ids) != len(set(run_ids))
    ):
        raise ValueError("retired schedule run ids are malformed or duplicated")
    classifications = {
        "completed_summary": [],
        "partial_artifacts": [],
        "unlaunched": [],
    }
    records = []
    for row in schedule:
        run_id = row["run_id"]
        experiment = _retirement_child(
            campaign_dir,
            row.get("experiment_relpath"),
            f"{run_id} experiment",
        )
        summary = _retirement_child(
            campaign_dir,
            row.get("summary_relpath"),
            f"{run_id} summary",
        )
        launcher = _retirement_child(
            campaign_dir,
            row.get("launcher_log_relpath"),
            f"{run_id} launcher log",
        )
        receipt = _retirement_child(
            campaign_dir,
            f"launch_receipts/{run_id}.json",
            f"{run_id} launch receipt",
        )
        receipt_hash = _retirement_child(
            campaign_dir,
            f"launch_receipts/{run_id}.sha256.json",
            f"{run_id} launch receipt hash",
        )
        excluded_attempts = _retirement_child(
            campaign_dir,
            f"excluded_attempts/{run_id}",
            f"{run_id} excluded attempts",
        )
        launch_failure = _retirement_child(
            campaign_dir,
            f"launch_failures/{run_id}.json",
            f"{run_id} launch failure",
        )
        artifact_paths = {
            "experiment": experiment,
            "launcher_log": launcher,
            "launch_receipt": receipt,
            "launch_receipt_hash": receipt_hash,
            "launch_failure": launch_failure,
            "excluded_attempts": excluded_attempts,
        }
        artifacts = {}
        for name, path in artifact_paths.items():
            reference = _retirement_artifact_ref(
                campaign_dir, path, f"{run_id} {name}"
            )
            if reference is not None:
                artifacts[name] = reference
        if summary.is_symlink():
            raise ValueError(f"{run_id} summary may not be a symbolic link")
        if summary.is_file():
            disposition = "completed_summary"
            summary_ref = _campaign_file_ref(campaign_dir, summary)
        elif summary.exists():
            raise ValueError(f"{run_id} summary is not a regular file")
        elif artifacts:
            disposition = "partial_artifacts"
            summary_ref = None
        else:
            disposition = "unlaunched"
            summary_ref = None
        classifications[disposition].append(run_id)
        records.append({
            "run_id": run_id,
            "block": row.get("block"),
            "cohort": row.get("cohort"),
            "repeat": row.get("repeat"),
            "scenario": row.get("scenario"),
            "condition": row.get("condition"),
            "arm": row.get("arm"),
            "scaffold": row.get("scaffold"),
            "disposition": disposition,
            "confirmatory_eligible": False,
            "redraw_eligible": False,
            "summary": summary_ref,
            "artifacts": artifacts,
            "artifacts_sha256": _sha_bytes(_json_bytes(artifacts)),
        })
    if (
        set().union(*(set(ids) for ids in classifications.values()))
        != set(run_ids)
        or sum(len(ids) for ids in classifications.values()) != len(run_ids)
    ):
        raise ValueError("retirement classifications are not exhaustive/disjoint")
    return records, classifications


def _prelaunch_unlaunched_inventory(
    campaign_dir: Path,
    manifest: dict,
) -> tuple[list[dict], dict[str, list[str]]]:
    """Prove that every one of the 60 frozen runs is still unlaunched."""

    run_records, classifications = _retirement_run_inventory(
        campaign_dir, manifest
    )
    scheduled_ids = [row["run_id"] for row in manifest["schedule"]]
    counts = {
        name: len(run_ids)
        for name, run_ids in classifications.items()
    }
    if (
        len(scheduled_ids) != 60
        or len(run_records) != 60
        or counts != {
            "completed_summary": 0,
            "partial_artifacts": 0,
            "unlaunched": 60,
        }
        or classifications["unlaunched"] != scheduled_ids
        or any(
            record["disposition"] != "unlaunched"
            or record["summary"] is not None
            or record["artifacts"]
            for record in run_records
        )
    ):
        raise ValueError(
            "prelaunch supersession requires exactly 60 frozen scheduled "
            "runs with zero summaries, receipts, launch failures, excluded "
            "attempts, launcher logs, or other partial run artifacts"
        )
    return run_records, classifications


def _require_unpublished_smoke_gate(campaign_dir: Path) -> None:
    material = [
        path
        for path in (
            campaign_dir / "smoke_gate.json",
            campaign_dir / "smoke_inputs",
        )
        if path.exists()
    ]
    if material:
        raise ValueError(
            "prelaunch supersession requires no published smoke gate/input "
            "snapshot"
        )


def _validate_smoke_failure_evidence(
    campaign_dir: Path,
    manifest: dict,
    reference: object,
) -> dict | None:
    if reference is None:
        return None
    if (
        not isinstance(reference, dict)
        or set(reference) != {"path", "sha256", "size"}
    ):
        raise ValueError("prelaunch smoke-failure reference is malformed")
    path = _retirement_child(
        campaign_dir,
        reference.get("path"),
        "prelaunch smoke-failure evidence",
    )
    if path.is_symlink() or not path.is_file():
        raise ValueError(
            "prelaunch smoke-failure evidence must be a regular file"
        )
    if _campaign_file_ref(campaign_dir, path) != reference:
        raise ValueError("prelaunch smoke-failure evidence hash drifted")
    try:
        evidence = _read_json(path)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"prelaunch smoke-failure evidence is unreadable: {exc}"
        ) from exc
    if (
        not isinstance(evidence, dict)
        or evidence.get("schema_version") != 1
        or evidence.get("kind") != "excluded_smoke_gate_failure"
        or evidence.get("campaign_id") != manifest["campaign_id"]
        or evidence.get("confirmatory_runs_launched") != 0
        or evidence.get("confirmatory_eligible") is not False
        or not isinstance(evidence.get("reason"), str)
        or not evidence["reason"].strip()
    ):
        raise ValueError(
            "prelaunch smoke-failure evidence contract is invalid"
        )
    return evidence


def _validate_prelaunch_supersession_marker(
    campaign_dir: Path,
    manifest: dict,
) -> dict:
    marker_path, _, _ = _prelaunch_supersession_paths(campaign_dir)
    if not marker_path.is_file() or marker_path.is_symlink():
        raise ValueError("prelaunch supersession marker is missing/invalid")
    try:
        marker = _read_json(marker_path)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"prelaunch supersession marker is unreadable: {exc}"
        ) from exc
    expected_keys = {
        "schema_version",
        "kind",
        "campaign_id",
        "manifest",
        "manifest_hash",
        "superseded_at_utc",
        "status",
        "measured_runs_launched",
        "confirmatory_eligible",
        "redraw_eligible",
        "smoke_gate_published",
        "reason",
        "failure_evidence",
        "policy",
    }
    manifest_path = campaign_dir / "campaign_manifest.json"
    manifest_hash_path = campaign_dir / "campaign_manifest.sha256.json"
    if (
        not isinstance(marker, dict)
        or set(marker) != expected_keys
        or marker.get("schema_version") != 2
        or marker.get("kind") != "prelaunch_campaign_supersession"
        or marker.get("campaign_id") != manifest["campaign_id"]
        or marker.get("manifest") != _campaign_file_ref(
            campaign_dir, manifest_path
        )
        or marker.get("manifest_hash") != _campaign_file_ref(
            campaign_dir, manifest_hash_path
        )
        or marker.get("status") != "never_launched_never_confirmatory"
        or marker.get("measured_runs_launched") != 0
        or marker.get("confirmatory_eligible") is not False
        or marker.get("redraw_eligible") is not False
        or marker.get("smoke_gate_published") is not False
        or not isinstance(marker.get("reason"), str)
        or not marker["reason"].strip()
        or marker.get("policy") != PRELAUNCH_SUPERSESSION_POLICY
    ):
        raise ValueError(
            "prelaunch supersession marker contract/binding is invalid"
        )
    _parse_utc(
        marker.get("superseded_at_utc"),
        "prelaunch superseded_at_utc",
    )
    evidence = _validate_smoke_failure_evidence(
        campaign_dir, manifest, marker.get("failure_evidence")
    )
    if (
        evidence is not None
        and marker["reason"].strip() != evidence["reason"].strip()
    ):
        raise ValueError(
            "prelaunch reason differs from smoke-failure evidence"
        )
    _require_unpublished_smoke_gate(campaign_dir)
    _prelaunch_unlaunched_inventory(campaign_dir, manifest)
    return marker


def _build_prelaunch_supersession_attestation(
    campaign_dir: Path,
) -> dict:
    campaign_dir = campaign_dir.resolve()
    manifest = _load_bound_manifest(campaign_dir)
    marker = _validate_prelaunch_supersession_marker(
        campaign_dir, manifest
    )
    run_records, classifications = _prelaunch_unlaunched_inventory(
        campaign_dir, manifest
    )
    marker_path, _, _ = _prelaunch_supersession_paths(campaign_dir)
    counts = {
        name: len(run_ids)
        for name, run_ids in classifications.items()
    }
    return {
        "schema_version": 1,
        "kind": "campaign_prelaunch_supersession_attestation",
        "campaign_id": manifest["campaign_id"],
        "manifest": _campaign_file_ref(
            campaign_dir, campaign_dir / "campaign_manifest.json"
        ),
        "manifest_hash": _campaign_file_ref(
            campaign_dir, campaign_dir / "campaign_manifest.sha256.json"
        ),
        "supersession_marker": _campaign_file_ref(
            campaign_dir, marker_path
        ),
        "failure_evidence": marker["failure_evidence"],
        "status": marker["status"],
        "confirmatory_eligible": False,
        "redraw_eligible": False,
        "scheduled_runs": len(manifest["schedule"]),
        "classification_counts": counts,
        "scheduled_run_ids": [row["run_id"] for row in run_records],
        "classifications": classifications,
        "run_artifacts": run_records,
        "run_artifacts_sha256": _sha_bytes(_json_bytes(run_records)),
    }


def _build_retirement_attestation(campaign_dir: Path) -> dict:
    campaign_dir = campaign_dir.resolve()
    manifest = _load_bound_manifest(campaign_dir)
    marker = _validate_retirement_marker(campaign_dir, manifest)
    run_records, classifications = _retirement_run_inventory(
        campaign_dir, manifest
    )
    marker_path, _, _ = _abandonment_paths(campaign_dir)
    counts = {
        name: len(run_ids)
        for name, run_ids in classifications.items()
    }
    return {
        "schema_version": 2,
        "kind": "campaign_retirement_attestation",
        "campaign_id": manifest["campaign_id"],
        "manifest": _campaign_file_ref(
            campaign_dir, campaign_dir / "campaign_manifest.json"
        ),
        "manifest_hash": _campaign_file_ref(
            campaign_dir, campaign_dir / "campaign_manifest.sha256.json"
        ),
        "retirement_marker": _campaign_file_ref(
            campaign_dir, marker_path
        ),
        "status": marker["status"],
        "confirmatory_eligible": False,
        "redraw_eligible": False,
        "scheduled_runs": len(manifest["schedule"]),
        "classification_counts": counts,
        "scheduled_run_ids": [row["run_id"] for row in run_records],
        "classifications": classifications,
        "run_artifacts": run_records,
        "run_artifacts_sha256": _sha_bytes(_json_bytes(run_records)),
    }


def build_abandonment_attestation(campaign_dir: Path) -> dict:
    """Build the deterministic attestation for a legacy or generic marker."""

    campaign_dir = campaign_dir.resolve()
    manifest = _load_bound_manifest(campaign_dir)
    prelaunch_marker, _, _ = _prelaunch_supersession_paths(campaign_dir)
    abandonment_marker, _, _ = _abandonment_paths(campaign_dir)
    if prelaunch_marker.exists():
        if abandonment_marker.exists():
            raise ValueError(
                "campaign has conflicting retirement and prelaunch markers"
            )
        return _build_prelaunch_supersession_attestation(campaign_dir)
    marker = _validate_abandonment_marker(campaign_dir, manifest)
    if marker["schema_version"] == 1:
        return _build_legacy_abandonment_attestation(campaign_dir)
    return _build_retirement_attestation(campaign_dir)


def _attestation_hash_record(
    attestation_path: Path,
    attestation: dict,
) -> dict:
    kind = attestation.get("kind")
    if kind == "campaign_abandonment_attestation":
        hash_kind = "campaign_abandonment_attestation_hash"
        marker_key = "abandonment_marker"
        marker_hash_key = "abandonment_marker_sha256"
    elif kind == "campaign_retirement_attestation":
        hash_kind = "campaign_retirement_attestation_hash"
        marker_key = "retirement_marker"
        marker_hash_key = "retirement_marker_sha256"
    elif kind == "campaign_prelaunch_supersession_attestation":
        hash_kind = "campaign_prelaunch_supersession_attestation_hash"
        marker_key = "supersession_marker"
        marker_hash_key = "supersession_marker_sha256"
    else:
        raise ValueError("administrative attestation kind is unsupported")
    record = {
        "schema_version": attestation["schema_version"],
        "kind": hash_kind,
        "path": attestation_path.name,
        "sha256": _sha_file(attestation_path),
        "campaign_id": attestation["campaign_id"],
        "manifest_sha256": attestation["manifest"]["sha256"],
    }
    record[marker_hash_key] = attestation[marker_key]["sha256"]
    return record


def attest_abandonment(campaign_dir: Path) -> dict:
    campaign_dir = campaign_dir.resolve()
    _, attestation_path, hash_path = _disposition_paths(campaign_dir)
    if attestation_path.exists() or hash_path.exists():
        raise SystemExit("abandonment attestation already exists")
    attestation = build_abandonment_attestation(campaign_dir)
    _write_new(attestation_path, attestation)
    _write_new(
        hash_path,
        _attestation_hash_record(attestation_path, attestation),
    )
    return verify_abandonment(campaign_dir)


def supersede_prelaunch(
    campaign_dir: Path,
    failure_evidence: Path | None,
    reason: str | None = None,
) -> dict:
    """Irrevocably supersede a frozen campaign with zero launched runs."""

    campaign_dir = campaign_dir.resolve()
    abandonment_paths = _abandonment_paths(campaign_dir)
    prelaunch_paths = _prelaunch_supersession_paths(campaign_dir)
    if any(
        path.exists()
        for path in (*abandonment_paths, *prelaunch_paths)
    ):
        raise SystemExit(
            "campaign already has retirement/supersession material"
        )
    try:
        manifest = _load_bound_manifest(campaign_dir)
        _require_unpublished_smoke_gate(campaign_dir)
        _prelaunch_unlaunched_inventory(campaign_dir, manifest)
    except ValueError as exc:
        raise SystemExit(f"prelaunch supersession refused: {exc}") from exc

    failure_reference = None
    evidence = None
    if failure_evidence is not None:
        supplied_evidence = (
            failure_evidence
            if failure_evidence.is_absolute()
            else Path.cwd() / failure_evidence
        )
        if supplied_evidence.is_symlink():
            raise SystemExit(
                "prelaunch smoke-failure evidence may not be a symbolic link"
            )
        failure_evidence = supplied_evidence.resolve()
        try:
            failure_evidence.relative_to(campaign_dir)
        except ValueError as exc:
            raise SystemExit(
                "prelaunch smoke-failure evidence must be inside the "
                "campaign directory"
            ) from exc
        if failure_evidence.is_symlink() or not failure_evidence.is_file():
            raise SystemExit(
                "prelaunch smoke-failure evidence must be a regular file"
            )
        failure_reference = _campaign_file_ref(
            campaign_dir, failure_evidence
        )
        try:
            evidence = _validate_smoke_failure_evidence(
                campaign_dir, manifest, failure_reference
            )
        except ValueError as exc:
            raise SystemExit(
                f"prelaunch smoke-failure evidence is invalid: {exc}"
            ) from exc

    effective_reason = (
        evidence["reason"].strip()
        if evidence is not None
        else (reason or "").strip()
    )
    if not effective_reason:
        raise SystemExit(
            "prelaunch supersession needs failure evidence or a reason"
        )
    if (
        evidence is not None
        and reason is not None
        and reason.strip() != effective_reason
    ):
        raise SystemExit(
            "explicit prelaunch reason differs from failure evidence"
        )
    marker_path, _, _ = prelaunch_paths
    _write_new(marker_path, {
        "schema_version": 2,
        "kind": "prelaunch_campaign_supersession",
        "campaign_id": manifest["campaign_id"],
        "manifest": _campaign_file_ref(
            campaign_dir, campaign_dir / "campaign_manifest.json"
        ),
        "manifest_hash": _campaign_file_ref(
            campaign_dir, campaign_dir / "campaign_manifest.sha256.json"
        ),
        "superseded_at_utc": _utcnow(),
        "status": "never_launched_never_confirmatory",
        "measured_runs_launched": 0,
        "confirmatory_eligible": False,
        "redraw_eligible": False,
        "smoke_gate_published": False,
        "reason": effective_reason,
        "failure_evidence": failure_reference,
        "policy": PRELAUNCH_SUPERSESSION_POLICY,
    })
    return attest_abandonment(campaign_dir)


def retire_campaign(campaign_dir: Path, reason: str) -> dict:
    """Irrevocably retire one launched freeze and attest all scheduled IDs."""

    campaign_dir = campaign_dir.resolve()
    marker_path, attestation_path, hash_path = _abandonment_paths(
        campaign_dir
    )
    prelaunch_paths = _prelaunch_supersession_paths(campaign_dir)
    if any(
        path.exists()
        for path in (
            marker_path,
            attestation_path,
            hash_path,
            *prelaunch_paths,
        )
    ):
        raise SystemExit("campaign already has retirement/supersession material")
    if not isinstance(reason, str) or not reason.strip():
        raise SystemExit("retirement reason must be non-empty")
    manifest = _load_bound_manifest(campaign_dir)
    run_records, classifications = _retirement_run_inventory(
        campaign_dir, manifest
    )
    if not (
        classifications["completed_summary"]
        or classifications["partial_artifacts"]
    ):
        raise SystemExit(
            "post-launch retirement requires completed or partial run evidence"
        )
    if len(run_records) != len(manifest["schedule"]):
        raise SystemExit("retirement inventory is not schedule-complete")
    _write_new(marker_path, {
        "schema_version": 2,
        "kind": "campaign_retirement",
        "campaign_id": manifest["campaign_id"],
        "manifest": _campaign_file_ref(
            campaign_dir, campaign_dir / "campaign_manifest.json"
        ),
        "retired_at_utc": _utcnow(),
        "status": "pilot_only_never_confirmatory",
        "confirmatory_eligible": False,
        "redraw_eligible": False,
        "reason": reason.strip(),
        "policy": RETIREMENT_POLICY,
    })
    return attest_abandonment(campaign_dir)


def verify_abandonment(campaign_dir: Path) -> dict:
    campaign_dir = campaign_dir.resolve()
    marker_path, attestation_path, hash_path = _disposition_paths(
        campaign_dir
    )
    if not attestation_path.is_file() or not hash_path.is_file():
        raise ValueError("abandonment attestation/hash is missing")
    expected = build_abandonment_attestation(campaign_dir)
    try:
        actual = _read_json(attestation_path)
        hash_record = _read_json(hash_path)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"abandonment attestation is unreadable: {exc}") from exc
    if actual != expected:
        raise ValueError("abandonment attestation/evidence drifted")
    expected_hash = _attestation_hash_record(attestation_path, actual)
    if hash_record != expected_hash:
        raise ValueError("abandonment attestation hash binding is invalid")
    record = {
        "campaign_id": actual["campaign_id"],
        "campaign_dir": str(campaign_dir),
        "manifest": _file_ref(campaign_dir / "campaign_manifest.json"),
        "manifest_hash": _file_ref(
            campaign_dir / "campaign_manifest.sha256.json"
        ),
        "abandonment_marker": _file_ref(marker_path),
        "attestation": _file_ref(attestation_path),
        "attestation_hash": _file_ref(hash_path),
    }
    failure_reference = actual.get("failure_evidence")
    if failure_reference is not None:
        record["failure_evidence"] = _file_ref(_retirement_child(
            campaign_dir,
            failure_reference.get("path")
            if isinstance(failure_reference, dict)
            else None,
            "prelaunch smoke-failure evidence",
        ))
    return record


def _freeze_superseded_lineage(
    campaign_dir: Path,
    superseded_campaign_dir: Path,
) -> dict:
    """Freeze the exact administrative record that authorizes a successor."""

    campaign_dir = campaign_dir.resolve()
    superseded_campaign_dir = superseded_campaign_dir.resolve()
    if campaign_dir == superseded_campaign_dir:
        raise ValueError("successor cannot supersede itself")
    source = verify_abandonment(superseded_campaign_dir)
    frozen_root = (
        campaign_dir
        / "frozen_inputs"
        / "lineage"
        / _safe_id(source["campaign_id"])
    )
    if frozen_root.exists():
        raise ValueError("partial frozen successor lineage already exists")
    frozen_root.mkdir(parents=True)
    file_keys = {
        "manifest",
        "manifest_hash",
        "abandonment_marker",
        "attestation",
        "attestation_hash",
        "failure_evidence",
    }
    if set(source) - {"campaign_id", "campaign_dir"} - file_keys:
        raise ValueError("superseded source binding has unknown fields")
    source_paths = tuple(
        Path(record["path"])
        for key, record in source.items()
        if key in file_keys
    )
    if (
        len(source_paths) not in {5, 6}
        or len({path.name for path in source_paths}) != len(source_paths)
        or any(not path.is_file() or path.is_symlink() for path in source_paths)
    ):
        raise ValueError("superseded source file binding is malformed")
    for source_path in source_paths:
        shutil.copy2(source_path, frozen_root / source_path.name)
    inventory = _tree_inventory(frozen_root)
    return {
        "kind": "superseded_abandoned_campaign",
        "source_campaign_dir": str(superseded_campaign_dir),
        "source_binding": source,
        "frozen_root": str(frozen_root.relative_to(campaign_dir)),
        "frozen_inventory": inventory,
        "frozen_inventory_sha256": _inventory_sha(inventory),
    }


def _verify_superseded_lineage(
    campaign_dir: Path,
    lineage: object,
) -> None:
    if lineage is None:
        return
    if (
        not isinstance(lineage, dict)
        or lineage.get("kind") != "superseded_abandoned_campaign"
        or not isinstance(lineage.get("source_campaign_dir"), str)
        or not isinstance(lineage.get("source_binding"), dict)
        or not isinstance(lineage.get("frozen_root"), str)
        or not isinstance(lineage.get("frozen_inventory"), dict)
    ):
        raise ValueError("successor lineage schema is invalid")
    source = verify_abandonment(
        Path(lineage["source_campaign_dir"])
    )
    if source != lineage["source_binding"]:
        raise ValueError("source abandonment lineage drifted")
    frozen_root = _campaign_child(
        campaign_dir, lineage["frozen_root"], "frozen successor lineage"
    )
    inventory = _tree_inventory(frozen_root)
    if (
        inventory != lineage["frozen_inventory"]
        or _inventory_sha(inventory)
        != lineage.get("frozen_inventory_sha256")
    ):
        raise ValueError("frozen successor lineage drifted")
    source_by_name = {
        Path(record["path"]).name: record
        for key, record in source.items()
        if key not in {"campaign_id", "campaign_dir"}
    }
    expected_names = set(source_by_name)
    if set(inventory) != expected_names:
        raise ValueError("frozen successor lineage inventory is not exact")
    for name in expected_names:
        record = inventory[name]
        source_record = source_by_name.get(name)
        if (
            source_record is None
            or record["sha256"] != source_record["sha256"]
            or record["size"] != source_record["size"]
        ):
            raise ValueError(
                f"frozen/source successor lineage differs: {name}"
            )


def _original_artifact_manifest(repo_root: Path) -> dict[str, dict]:
    paths: list[Path] = []
    catalogs = (
        repo_root
        / "caveat"
        / "envs"
        / "caveat_shop"
        / "server"
        / "_catalogs"
    )
    for scenario in EASY_SCENARIOS:
        source = repo_root / CAVEAT_SHOP_DATA_RELATIVE / scenario
        if not source.is_dir():
            raise ValueError(
                f"original benchmark artifact root is missing: {source}"
            )
        paths.extend(
            path for path in source.rglob("*") if path.is_file()
        )
        paths.append(catalogs / f"{scenario}.json")
        paths.extend(
            catalogs / f"{scenario}.{condition}.steering.json"
            for condition in LOCKDIFF_CONDITIONS
        )
    manifest = {}
    for path in sorted(set(paths)):
        if not path.is_file():
            raise ValueError(f"original benchmark artifact is missing: {path}")
        manifest[str(path.relative_to(repo_root))] = {
            "sha256": _sha_file(path),
            "size": path.stat().st_size,
        }
    return manifest


def _validate_lockdiff(
    after_path: Path,
    *,
    repo_root: Path = ROOT,
) -> dict:
    """Validate and inventory the authoritative original-five lock proof."""
    from lockdiff_capture import ENGINE_FILES, FRONTEND_SHA256

    after_path = after_path.resolve()
    before_path = after_path.with_name("lockdiff_before.json")
    if not after_path.is_file() or not before_path.is_file():
        raise ValueError(
            "lockdiff requires lockdiff_after.json and sibling "
            "lockdiff_before.json"
        )
    try:
        before = _read_json(before_path)
        after = _read_json(after_path)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"lockdiff report is unreadable: {exc}") from exc
    expected_captures = {
        f"{scenario}/{condition}"
        for scenario in EASY_SCENARIOS
        for condition in LOCKDIFF_CONDITIONS
    }
    for label, report in (("before", before), ("after", after)):
        if not isinstance(report, dict):
            raise ValueError(f"lockdiff {label} report is not an object")
        actual_captures = set(report) - {"__meta__"}
        if actual_captures != expected_captures:
            raise ValueError(
                f"lockdiff {label} condition inventory is not exact"
            )
        meta = report.get("__meta__")
        if (
            not isinstance(meta, dict)
            or not isinstance(meta.get("captured_at"), (int, float))
            or not isinstance(meta.get("files"), dict)
            or not isinstance(meta.get("original_artifacts"), dict)
        ):
            raise ValueError(f"lockdiff {label} metadata schema is invalid")
    changed_captures = sorted(
        capture for capture in expected_captures
        if before[capture] != after[capture]
    )
    if changed_captures:
        raise ValueError(
            "original storefront captures changed: "
            + ", ".join(changed_captures[:10])
        )
    before_artifacts = before["__meta__"]["original_artifacts"]
    after_artifacts = after["__meta__"]["original_artifacts"]
    current_artifacts = _original_artifact_manifest(repo_root)
    if before_artifacts != after_artifacts:
        raise ValueError("original benchmark artifact bytes changed")
    if after_artifacts != current_artifacts:
        raise ValueError(
            "lockdiff original artifact inventory is stale or incomplete"
        )
    if len(current_artifacts) != 105:
        raise ValueError(
            "lockdiff original artifact inventory must contain 105 files"
        )
    catalog_count = sum(
        "/_catalogs/" in path for path in current_artifacts
    )
    benchmark_count = sum(
        path.startswith(f"{CAVEAT_SHOP_DATA_RELATIVE.as_posix()}/")
        for path in current_artifacts
    )
    if (catalog_count, benchmark_count) != (55, 50):
        raise ValueError(
            "lockdiff artifact inventory breakdown is not 55 catalogs + "
            "50 benchmark files"
        )
    expected_engine = set(ENGINE_FILES)
    after_engine = after["__meta__"]["files"]
    before_engine = before["__meta__"]["files"]
    if set(after_engine) != expected_engine:
        raise ValueError("lockdiff after engine inventory is not exact")
    if not before_engine or not set(before_engine).issubset(expected_engine):
        raise ValueError("lockdiff before engine inventory is malformed")
    current_engine = {}
    for relative in ENGINE_FILES:
        path = repo_root / relative
        current_engine[relative] = (
            _sha_file(path)[:16] if path.is_file() else None
        )
        record = after_engine.get(relative)
        if (
            not isinstance(record, dict)
            or record.get("sha256") != current_engine[relative]
            or set(record) != {"sha256", "mtime"}
        ):
            raise ValueError(
                f"lockdiff after engine hash is stale: {relative}"
            )
    changed_engine = sorted(
        relative for relative in ENGINE_FILES
        if (before_engine.get(relative) or {}).get("sha256")
        != after_engine[relative]["sha256"]
    )
    if not changed_engine:
        raise ValueError(
            "lockdiff is inconclusive: no engine file changed between "
            "captures"
        )
    frontend_root = (
        repo_root
        / "caveat"
        / "envs"
        / "caveat_shop"
        / "server"
        / "frontend"
        / "dist"
    )
    frontend = {
        str(path.relative_to(frontend_root)): _sha_file(path)
        for path in sorted(frontend_root.rglob("*"))
        if path.is_file()
    } if frontend_root.is_dir() else {}
    if frontend != FRONTEND_SHA256:
        raise ValueError("shared prebuilt frontend bundle bytes drifted")
    return {
        "schema_version": 1,
        "verdict": "pass",
        "before": _file_ref(before_path),
        "after": _file_ref(after_path),
        "condition_captures": len(expected_captures),
        "measured_condition_captures": (
            len(EASY_SCENARIOS) * (len(LOCKDIFF_CONDITIONS) - 2)
        ),
        "control_condition_captures": len(EASY_SCENARIOS) * 2,
        "original_artifacts": {
            "count": len(current_artifacts),
            "catalog_count": catalog_count,
            "benchmark_data_count": benchmark_count,
            "inventory_sha256": _inventory_sha(current_artifacts),
        },
        "engine": {
            "after_file_count": len(after_engine),
            "changed_files": changed_engine,
            "current_hashes_sha256": _inventory_sha(current_engine),
        },
        "frontend": {
            "files": frontend,
            "inventory_sha256": _inventory_sha(frontend),
        },
    }


def _region_order(regions: tuple[str, ...], primary: str) -> list[str]:
    index = regions.index(primary)
    return [
        regions[(index + offset) % len(regions)]
        for offset in range(len(regions))
    ]


def _deterministic_order(
    values: tuple[str, ...] | list[str],
    *,
    namespace: str,
) -> list[str]:
    """Return a source-frozen pseudorandom order without runtime RNG state."""

    return sorted(
        values,
        key=lambda value: hashlib.sha256(
            (
                f"{SCHEDULE_RANDOMIZATION_SEED}\0"
                f"{namespace}\0{value}"
            ).encode("utf-8")
        ).digest(),
    )


def _block_pair_plan(block_spec: dict) -> list[tuple[str, bool]]:
    """Constrained randomization for five adjacent, route-matched A/B pairs.

    Scenario order is hash-randomized once per cohort.  Exactly two of five
    pairs use baseline-first in odd repeats and three use baseline-first in
    even repeats.  Reversing the order on repeat two gives the two-repeat hard
    cohort an exact within-scenario crossover; repeat three in the weak model
    combined cohort returns to the predeclared odd-repeat assignment.
    """

    cohort = str(block_spec["cohort"])
    scenarios = tuple(str(value) for value in block_spec["scenarios"])
    ordered = _deterministic_order(
        scenarios,
        namespace=f"{cohort}:scenario-pair-order",
    )
    baseline_first_base = set(_deterministic_order(
        scenarios,
        namespace=f"{cohort}:arm-order",
    )[:2])
    flip = int(block_spec["repeat"]) % 2 == 0
    return [
        (
            scenario,
            (scenario in baseline_first_base) ^ flip,
        )
        for scenario in ordered
    ]


def _validate_region_contract(
    regions: tuple[str, ...] | list[str],
    *,
    label: str,
) -> tuple[str, ...]:
    value = tuple(regions)
    if not value:
        raise ValueError(f"{label} routing contract is empty")
    if len(value) != len(set(value)):
        raise ValueError(f"{label} routing contract contains duplicates")
    unsupported = sorted(set(value) - set(SUPPORTED_TRAPI_REGIONS))
    if unsupported:
        raise ValueError(
            f"{label} routing contract contains unsupported regions: "
            + ", ".join(unsupported)
        )
    return value


def _validate_weak_campaign_regions(
    regions: tuple[str, ...] | list[str],
    *,
    label: str,
) -> tuple[str, ...]:
    """Require an explicit ordering of all three freshly probed Terra routes."""

    value = _validate_region_contract(regions, label=label)
    if len(value) != len(SUPPORTED_TRAPI_REGIONS) or set(value) != set(
        SUPPORTED_TRAPI_REGIONS
    ):
        raise ValueError(
            f"{label} must explicitly order all three supported regions"
        )
    return value


def _parse_region_argument(raw: str) -> tuple[str, ...]:
    try:
        return _validate_region_contract(
            tuple(part.strip() for part in raw.split(",") if part.strip()),
            label="model",
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _block_specs(
    weak_regions: tuple[str, ...] = WEAK_REGIONS,
    sol_regions: tuple[str, ...] = SOL_REGIONS,
) -> tuple[dict, ...]:
    weak_regions = _validate_region_contract(
        weak_regions, label="weak model"
    )
    sol_regions = _validate_region_contract(
        sol_regions, label="sol-high"
    )
    return (
        {
            "block": 1,
            "cohort": "weak_easy_combined",
            "model_request": WEAK_REQUEST,
            "model_recorded": WEAK_RECORDED,
            "logical_model": WEAK_LOGICAL,
            "scenarios": EASY_SCENARIOS,
            "condition": "combined",
            "repeat": 1,
            "regions": weak_regions,
            "probe_stage": "weak_before",
        },
        {
            "block": 2,
            "cohort": "weak_easy_combined",
            "model_request": WEAK_REQUEST,
            "model_recorded": WEAK_RECORDED,
            "logical_model": WEAK_LOGICAL,
            "scenarios": EASY_SCENARIOS,
            "condition": "combined",
            "repeat": 2,
            "regions": weak_regions,
            "probe_stage": "weak_before",
        },
        {
            "block": 3,
            "cohort": "weak_easy_combined",
            "model_request": WEAK_REQUEST,
            "model_recorded": WEAK_RECORDED,
            "logical_model": WEAK_LOGICAL,
            "scenarios": EASY_SCENARIOS,
            "condition": "combined",
            "repeat": 3,
            "regions": weak_regions,
            "probe_stage": "weak_mid",
        },
        {
            "block": 4,
            "cohort": "weak_easy_clean",
            "model_request": WEAK_REQUEST,
            "model_recorded": WEAK_RECORDED,
            "logical_model": WEAK_LOGICAL,
            "scenarios": EASY_SCENARIOS,
            "condition": "clean",
            "repeat": 1,
            "regions": weak_regions,
            "probe_stage": "weak_mid",
        },
        {
            "block": 5,
            "cohort": "sol_high_hard",
            "model_request": SOL_REQUEST,
            "model_recorded": SOL_RECORDED,
            "logical_model": SOL_LOGICAL,
            "scenarios": HARD_SCENARIOS,
            "condition": "combined",
            "repeat": 1,
            "regions": sol_regions,
            "probe_stage": "sol_before",
        },
        {
            "block": 6,
            "cohort": "sol_high_hard",
            "model_request": SOL_REQUEST,
            "model_recorded": SOL_RECORDED,
            "logical_model": SOL_LOGICAL,
            "scenarios": HARD_SCENARIOS,
            "condition": "combined",
            "repeat": 2,
            "regions": sol_regions,
            "probe_stage": "sol_mid",
        },
    )


def build_schedule(
    campaign_id: str,
    base_port: int,
    *,
    weak_regions: tuple[str, ...] = WEAK_REGIONS,
    sol_regions: tuple[str, ...] = SOL_REGIONS,
) -> list[dict]:
    """Build a balanced, pair-interleaved 60-run schedule."""
    campaign_id = _safe_id(campaign_id)
    rows: list[dict] = []
    weak_regions = _validate_region_contract(
        weak_regions, label="weak model"
    )
    sol_regions = _validate_region_contract(
        sol_regions, label="sol-high"
    )
    for block_spec in _block_specs(weak_regions, sol_regions):
        block = block_spec["block"]
        spawn_index = 0
        pair_plan = _block_pair_plan(block_spec)
        for pair_index, (scenario, baseline_first) in enumerate(pair_plan):
            pair_order = list(ARMS)
            if not baseline_first:
                pair_order.reverse()
            regions = tuple(block_spec["regions"])
            # A paired comparison must differ only by scaffold.  Both arms
            # therefore share the exact primary and failover order.  Rotate
            # pair assignments across blocks so route load remains balanced
            # without making route an arm-level nuisance.
            primary = regions[
                (pair_index + block - 1) % len(regions)
            ]
            region_order = _region_order(regions, primary)
            for arm, scaffold in pair_order:
                run_id = (
                    f"b{block:02d}_r{block_spec['repeat']}_"
                    f"{scenario}_{arm}"
                )
                run_name = f"{campaign_id}_{run_id}"
                result_dir = (
                    f"caveat_shop__{scaffold}__{block_spec['model_recorded']}__"
                    f"{scenario}-{VARIANT}__{block_spec['condition']}"
                )
                rows.append({
                    "run_id": run_id,
                    "block": block,
                    "spawn_index": spawn_index,
                    "cohort": block_spec["cohort"],
                    "repeat": block_spec["repeat"],
                    "scenario": scenario,
                    "variant": VARIANT,
                    "condition": block_spec["condition"],
                    "arm": arm,
                    "scaffold": scaffold,
                    "model_request": block_spec["model_request"],
                    "model_recorded": block_spec["model_recorded"],
                    "logical_model": block_spec["logical_model"],
                    "probe_stage": block_spec["probe_stage"],
                    "primary_region": primary,
                    "region_order": region_order,
                    "port": base_port + spawn_index,
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
                spawn_index += 1
    validate_schedule(
        rows,
        base_port,
        weak_regions=weak_regions,
        sol_regions=sol_regions,
    )
    return rows


def validate_schedule(
    schedule: list[dict],
    base_port: int,
    *,
    weak_regions: tuple[str, ...] = WEAK_REGIONS,
    sol_regions: tuple[str, ...] = SOL_REGIONS,
) -> None:
    weak_regions = _validate_region_contract(
        weak_regions, label="weak model"
    )
    sol_regions = _validate_region_contract(
        sol_regions, label="sol-high"
    )
    errors: list[str] = []
    if len(schedule) != 60:
        errors.append(f"schedule has {len(schedule)} runs, expected 60")
    if len({row["run_id"] for row in schedule}) != len(schedule):
        errors.append("run ids are not unique")
    if len({row["run_name"] for row in schedule}) != len(schedule):
        errors.append("run names are not unique")
    if {row["variant"] for row in schedule} != {VARIANT}:
        errors.append("variant is not graded-only")
    if {row["arm"] for row in schedule} != {"baseline", "caveat_harness"}:
        errors.append("arm set differs from the exact A/B design")
    expected = {
        ("weak_easy_combined", scenario, repeat, arm)
        for scenario in EASY_SCENARIOS
        for repeat in (1, 2, 3)
        for arm, _ in ARMS
    } | {
        ("weak_easy_clean", scenario, 1, arm)
        for scenario in EASY_SCENARIOS
        for arm, _ in ARMS
    } | {
        ("sol_high_hard", scenario, repeat, arm)
        for scenario in HARD_SCENARIOS
        for repeat in (1, 2)
        for arm, _ in ARMS
    }
    actual = {
        (row["cohort"], row["scenario"], row["repeat"], row["arm"])
        for row in schedule
    }
    if actual != expected:
        errors.append("cohort/scenario/repeat/arm matrix is not exact")
    for block_spec in _block_specs(weak_regions, sol_regions):
        block = block_spec["block"]
        rows = [row for row in schedule if row["block"] == block]
        if len(rows) != 10:
            errors.append(f"block {block} does not contain ten runs")
            continue
        if Counter(row["arm"] for row in rows) != {
            "baseline": 5, "caveat_harness": 5
        }:
            errors.append(f"block {block} arms are not balanced")
        if sorted(row["spawn_index"] for row in rows) != list(range(10)):
            errors.append(f"block {block} spawn indexes are not 0..9")
        pairs = Counter((row["scenario"], row["arm"]) for row in rows)
        if any(value != 1 for value in pairs.values()) or len(pairs) != 10:
            errors.append(f"block {block} scenario/arm pairs are not exact")
        regions = tuple(block_spec["regions"])
        loads = Counter(row["primary_region"] for row in rows)
        expected_loads = Counter(
            regions[
                (pair_index + block - 1) % len(regions)
            ]
            for pair_index in range(5)
            for _ in ARMS
        )
        if loads != expected_loads:
            errors.append(
                f"block {block} primary-region allocation drifted: "
                f"{dict(loads)} != {dict(expected_loads)}"
            )
        for row in rows:
            if row["region_order"][0] != row["primary_region"]:
                errors.append(f"{row['run_id']}: primary is not first")
            if set(row["region_order"]) != set(regions):
                errors.append(f"{row['run_id']}: region set drifted")
            if row["port"] != base_port + row["spawn_index"]:
                errors.append(f"{row['run_id']}: port is not block-local")
            expected_scaffold = dict(ARMS)[row["arm"]]
            if row["scaffold"] != expected_scaffold:
                errors.append(f"{row['run_id']}: scaffold/arm mismatch")
        by_spawn = sorted(rows, key=lambda row: row["spawn_index"])
        baseline_first_count = 0
        for index in range(0, 10, 2):
            pair = by_spawn[index:index + 2]
            if (
                len(pair) != 2
                or pair[0]["scenario"] != pair[1]["scenario"]
                or {row["arm"] for row in pair}
                != {"baseline", "caveat_harness"}
            ):
                errors.append(
                    f"block {block} spawn positions {index}/{index + 1} "
                    "are not one adjacent A/B pair"
                )
                continue
            if (
                pair[0]["primary_region"] != pair[1]["primary_region"]
                or pair[0]["region_order"] != pair[1]["region_order"]
            ):
                errors.append(
                    f"block {block} {pair[0]['scenario']} pair has a "
                    "route mismatch"
                )
            baseline_first_count += int(pair[0]["arm"] == "baseline")
        if baseline_first_count not in {2, 3}:
            errors.append(
                f"block {block} arm-order randomization is not 2/3 balanced"
            )
    if errors:
        raise ValueError(
            "invalid harness-evaluation schedule:\n  " + "\n  ".join(errors)
        )


def validate_port_band(base_port: int, *, require_free: bool) -> None:
    if base_port <= 0 or base_port + 99 > 65535:
        raise SystemExit("base port must reserve a valid 100-port band")
    if (
        base_port <= PROTECTED_PORT_HIGH
        and base_port + 99 >= PROTECTED_PORT_LOW
    ):
        raise SystemExit(
            f"port band intersects protected {PROTECTED_PORT_LOW}-"
            f"{PROTECTED_PORT_HIGH} refill lanes"
        )
    if require_free:
        listeners = listening_ports()
        conflicts = sorted(
            port for port in listeners
            if base_port <= port <= base_port + 99
        )
        if conflicts:
            raise SystemExit(
                "campaign port band has active listeners: "
                + ", ".join(str(port) for port in conflicts[:20])
            )


def listening_ports() -> set[int]:
    """Return TCP listening ports without contacting any listener."""
    try:
        completed = subprocess.run(
            ["ss", "-H", "-ltn"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"cannot inventory listening TCP ports: {exc}") from exc
    out = set()
    for line in completed.stdout.splitlines():
        address = line.split()[3] if len(line.split()) >= 4 else ""
        raw = address.rsplit(":", 1)[-1]
        if raw.isdigit():
            out.add(int(raw))
    return out


def select_free_band() -> int:
    listeners = listening_ports()
    for base in (
        15000, 16000, 17000, 18000, 19000, 20000, 21000,
        22000, 23000, 24000, 25000, 26000, 27000, 29000,
        30000, 31000, 32000, 33000, 34000, 35000,
    ):
        if all(port not in listeners for port in range(base, base + 100)):
            validate_port_band(base, require_free=False)
            return base
    raise SystemExit("no free non-132xx 100-port band is available")


def _system_process_table() -> dict[int, dict]:
    try:
        completed = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,args="],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"cannot inventory refill processes: {exc}") from exc
    processes = {}
    for line in completed.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        try:
            cwd = str(Path(f"/proc/{pid}/cwd").resolve())
        except OSError:
            cwd = None
        processes[pid] = {
            "pid": pid,
            "ppid": ppid,
            "command": parts[2],
            "cwd": cwd,
        }
    return processes


def active_refill_processes() -> list[str]:
    pattern = re.compile(
        r"--base-port(?:=|\s+)(132[0-9]{2})(?:\s|$)"
    )
    return [
        process["command"]
        for process in _system_process_table().values()
        if pattern.search(process["command"])
    ]


def _protected_listener_records() -> list[dict]:
    try:
        completed = subprocess.run(
            ["ss", "-H", "-ltnp"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(
            f"cannot inventory protected TCP listeners: {exc}"
        ) from exc
    records = []
    for line in completed.stdout.splitlines():
        parts = line.split()
        address = parts[3] if len(parts) >= 4 else ""
        raw_port = address.rsplit(":", 1)[-1]
        if not raw_port.isdigit():
            continue
        port = int(raw_port)
        if not PROTECTED_PORT_LOW <= port <= PROTECTED_PORT_HIGH:
            continue
        pids = sorted({int(value) for value in re.findall(
            r"\bpid=(\d+)\b", line
        )})
        records.append({
            "port": port,
            "pids": pids,
            "socket_record_sha256": _sha_bytes(line.encode("utf-8")),
        })
    return sorted(records, key=lambda record: record["port"])


def _option_values(tokens: list[str], name: str) -> list[str] | None:
    values = []
    occurrences = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == name:
            occurrences += 1
            index += 1
            current = []
            while index < len(tokens) and not tokens[index].startswith("--"):
                current.append(tokens[index])
                index += 1
            values = current
            continue
        if token.startswith(f"{name}="):
            occurrences += 1
            values = [token.split("=", 1)[1]]
        index += 1
    return values if occurrences == 1 else None


def _recognized_refill_master(process: dict) -> dict | None:
    try:
        tokens = shlex.split(process["command"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        len(tokens) < 3
        or tokens[1:3] != ["-m", "caveat.benchmark.run"]
    ):
        return None
    allowed_options = {
        "--name", "--scenarios", "--conditions", "--variants",
        "--scaffolds", "--models", "--repeats", "--jobs", "--results",
        "--base-port", "--max-steps",
    }
    observed_options = {
        token.split("=", 1)[0]
        for token in tokens if token.startswith("--")
    }
    if observed_options != allowed_options:
        return None
    options = {
        name: _option_values(tokens, name) for name in allowed_options
    }
    if any(value is None for value in options.values()):
        return None
    if (
        len(options["--name"]) != 1
        or len(options["--models"]) != 1
        or len(options["--jobs"]) != 1
        or len(options["--base-port"]) != 1
        or len(options["--results"]) != 1
    ):
        return None
    try:
        jobs = int(options["--jobs"][0])
        base_port = int(options["--base-port"][0])
    except ValueError:
        return None

    profile: str
    expected_results: Path
    profile_max_jobs: int
    if options["--name"] == ["overhaul_lb"]:
        profile = REFILL_PROFILE_MATRIX
        expected_lists = {
            "--scenarios": list(EASY_SCENARIOS),
            "--conditions": ["clean", "combined"],
            "--variants": [
                "thresholded", "mixed", "graded", "graded3", "graded4",
            ],
            "--scaffolds": ["browseruse"],
            "--repeats": ["3"],
            "--max-steps": ["2000"],
        }
        if (
            options["--models"][0] not in REFILL_MODELS
            or any(
                options[name] != value
                for name, value in expected_lists.items()
            )
        ):
            return None
        expected_results = ROOT / "results" / "overhaul_lb"
        profile_max_jobs = 16
    elif options["--name"] == [REFILL_PROFILE_QWEN_CAPFREE]:
        profile = REFILL_PROFILE_QWEN_CAPFREE
        expected_lists = {
            "--scenarios": ["backpack"],
            "--conditions": ["clean"],
            "--variants": ["graded"],
            "--scaffolds": ["browseruse"],
            "--models": ["Qwen3.5-122B"],
            "--repeats": ["1"],
            "--jobs": ["1"],
            "--base-port": ["13280"],
            "--max-steps": ["12000"],
        }
        if any(
            options[name] != value
            for name, value in expected_lists.items()
        ):
            return None
        expected_results = (
            ROOT / "results" / "overhaul_lb_refill_staging"
        )
        profile_max_jobs = 1
    else:
        return None

    # Experiment.cells() assigns one stable port per task x scaffold x model x
    # condition, independent of the worker-pool width. ``jobs`` is therefore
    # the number of simultaneously live browsers, not the size of the refill's
    # port allocation. Repeats reuse the same ports.
    cell_port_count = (
        len(options["--scenarios"])
        * len(options["--variants"])
        * len(options["--scaffolds"])
        * len(options["--models"])
        * len(options["--conditions"])
    )
    port_high = base_port + cell_port_count - 1
    if (
        not 1 <= jobs <= profile_max_jobs
        or not PROTECTED_PORT_LOW <= base_port <= PROTECTED_PORT_HIGH
        or port_high > PROTECTED_PORT_HIGH
        or process.get("cwd") != str(ROOT.resolve())
    ):
        return None
    results = Path(options["--results"][0])
    if not results.is_absolute():
        results = Path(process["cwd"]) / results
    if results.resolve() != expected_results.resolve():
        return None
    return {
        "pid": process["pid"],
        "ppid": process["ppid"],
        "profile": profile,
        "model": options["--models"][0],
        "jobs": jobs,
        "base_port": base_port,
        "cell_port_count": cell_port_count,
        "port_low": base_port,
        "port_high": port_high,
        "cwd": process["cwd"],
        "command": process["command"],
        "command_sha256": _sha_bytes(
            process["command"].encode("utf-8")
        ),
    }


def _is_descendant(
    pid: int,
    ancestors: set[int],
    processes: dict[int, dict],
) -> bool:
    seen = set()
    while pid > 1 and pid not in seen:
        if pid in ancestors:
            return True
        seen.add(pid)
        process = processes.get(pid)
        if process is None:
            return False
        pid = process["ppid"]
    return pid in ancestors


def audit_refill_coexistence(campaign_base_port: int) -> dict:
    validate_port_band(campaign_base_port, require_free=False)
    processes = _system_process_table()
    protected_pattern = re.compile(
        r"--base-port(?:=|\s+)(132[0-9]{2})(?:\s|$)"
    )
    candidates = [
        process for process in processes.values()
        if protected_pattern.search(process["command"])
    ]
    masters = []
    for process in candidates:
        recognized = _recognized_refill_master(process)
        if recognized is None:
            raise SystemExit(
                "unrecognized process owns a protected 132xx base port: "
                + process["command"]
            )
        masters.append(recognized)
    listeners = _protected_listener_records()
    if listeners and not masters:
        raise SystemExit(
            "protected 132xx listeners exist without a recognized refill "
            "master"
        )
    if len(listeners) > REFILL_MAX_LISTENERS:
        raise SystemExit(
            f"protected listener count {len(listeners)} exceeds "
            f"{REFILL_MAX_LISTENERS}"
        )
    master_pids = {master["pid"] for master in masters}
    for listener in listeners:
        if len(listener["pids"]) != 1 or not _is_descendant(
            listener["pids"][0], master_pids, processes
        ):
            raise SystemExit(
                f"protected listener {listener['port']} is not owned by "
                "exactly one recognized refill-master descendant"
            )
    refill_jobs = sum(master["jobs"] for master in masters)
    if refill_jobs > REFILL_MAX_TOTAL_JOBS:
        raise SystemExit(
            f"recognized refill jobs sum {refill_jobs} exceeds "
            f"{REFILL_MAX_TOTAL_JOBS}"
        )
    refill_ranges = sorted(
        (
            master["port_low"],
            master["port_high"],
            master["pid"],
        )
        for master in masters
    )
    if any(
        high > PROTECTED_PORT_HIGH
        for _, high, _ in refill_ranges
    ) or any(
        refill_ranges[index][0] <= refill_ranges[index - 1][1]
        for index in range(1, len(refill_ranges))
    ):
        raise SystemExit(
            "recognized refill masters have overlapping or out-of-band "
            "browser-port allocations"
        )
    projected = refill_jobs + CAMPAIGN_MAX_PARALLEL_RUNS
    if projected > MACHINE_BROWSER_FLOOR:
        raise SystemExit(
            f"projected browsers {projected} exceed frozen machine floor "
            f"{MACHINE_BROWSER_FLOOR}"
        )
    payload = {
        "kind": "protected_refill_coexistence_audit",
        "captured_at_utc": _utcnow(),
        "campaign_port_band": [
            campaign_base_port, campaign_base_port + 99,
        ],
        "protected_port_band": [
            PROTECTED_PORT_LOW, PROTECTED_PORT_HIGH,
        ],
        "recognized_models": list(REFILL_MODELS),
        "recognized_masters": sorted(
            masters, key=lambda record: record["pid"]
        ),
        "protected_listeners": listeners,
        "refill_jobs_sum": refill_jobs,
        "refill_port_ranges": [
            {"low": low, "high": high, "master_pid": pid}
            for low, high, pid in refill_ranges
        ],
        "campaign_block_runs": CAMPAIGN_BLOCK_RUNS,
        "campaign_max_parallel_runs": CAMPAIGN_MAX_PARALLEL_RUNS,
        "projected_browser_max": projected,
        "machine_browser_floor": MACHINE_BROWSER_FLOOR,
        "max_protected_listeners": REFILL_MAX_LISTENERS,
        "ports_non_overlapping": True,
        "verdict": "pass",
    }
    return {
        **payload,
        "snapshot_sha256": _sha_bytes(_json_bytes(payload)),
    }


def _coexistence_policy() -> dict:
    return {
        "protected_port_band": [
            PROTECTED_PORT_LOW, PROTECTED_PORT_HIGH,
        ],
        "recognized_refill_models": list(REFILL_MODELS),
        "recognized_profiles": {
            REFILL_PROFILE_MATRIX: {
                "campaign_name": "overhaul_lb",
                "results_root": "results/overhaul_lb",
                "models": list(REFILL_MODELS),
                "max_jobs": 16,
                "cell_port_count": 50,
            },
            REFILL_PROFILE_QWEN_CAPFREE: {
                "campaign_name": REFILL_PROFILE_QWEN_CAPFREE,
                "results_root": "results/overhaul_lb_refill_staging",
                "models": ["Qwen3.5-122B"],
                "jobs": 1,
                "base_port": 13280,
                "cell_port_count": 1,
                "max_steps": 12000,
            },
        },
        "max_refill_jobs_sum": REFILL_MAX_TOTAL_JOBS,
        "max_protected_listeners": REFILL_MAX_LISTENERS,
        "campaign_block_runs": CAMPAIGN_BLOCK_RUNS,
        "campaign_max_parallel_runs": CAMPAIGN_MAX_PARALLEL_RUNS,
        "machine_browser_floor": MACHINE_BROWSER_FLOOR,
        "projected_max_formula":
            "refill_jobs_sum + campaign_max_parallel_runs",
        "pair_atomic_replenishment": True,
        "never_signal_or_relaunch_refills": True,
        "pair_interleaved_background_load_shared": True,
        "unrecognized_state_action": "fail_closed",
    }


def _validate_refill_coexistence_record(
    record: object,
    campaign_base_port: int,
) -> None:
    if not isinstance(record, dict):
        raise ValueError("refill coexistence snapshot is missing")
    payload = {
        key: value for key, value in record.items()
        if key != "snapshot_sha256"
    }
    if (
        record.get("snapshot_sha256")
        != _sha_bytes(_json_bytes(payload))
        or record.get("kind")
        != "protected_refill_coexistence_audit"
        or record.get("verdict") != "pass"
        or record.get("campaign_port_band")
        != [campaign_base_port, campaign_base_port + 99]
        or record.get("protected_port_band")
        != [PROTECTED_PORT_LOW, PROTECTED_PORT_HIGH]
        or record.get("recognized_models") != list(REFILL_MODELS)
        or record.get("campaign_block_runs") != CAMPAIGN_BLOCK_RUNS
        or record.get("campaign_max_parallel_runs")
        != CAMPAIGN_MAX_PARALLEL_RUNS
        or record.get("machine_browser_floor") != MACHINE_BROWSER_FLOOR
        or record.get("max_protected_listeners")
        != REFILL_MAX_LISTENERS
        or record.get("ports_non_overlapping") is not True
    ):
        raise ValueError("refill coexistence snapshot contract drifted")
    try:
        _parse_utc(
            record.get("captured_at_utc"),
            "refill coexistence captured_at_utc",
        )
        masters = record["recognized_masters"]
        listeners = record["protected_listeners"]
        refill_jobs = sum(master["jobs"] for master in masters)
        reparsed_masters = [
            _recognized_refill_master(master) for master in masters
        ]
        sorted_masters = sorted(
            masters, key=lambda item: item["base_port"]
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"refill coexistence snapshot is malformed: {exc}"
        ) from exc
    if (
        not isinstance(masters, list)
        or not isinstance(listeners, list)
        or any(master is None for master in reparsed_masters)
        or reparsed_masters != masters
        or refill_jobs != record.get("refill_jobs_sum")
        or refill_jobs > REFILL_MAX_TOTAL_JOBS
        or len(listeners) > REFILL_MAX_LISTENERS
        or record.get("projected_browser_max")
        != refill_jobs + CAMPAIGN_MAX_PARALLEL_RUNS
        or record["projected_browser_max"] > MACHINE_BROWSER_FLOOR
        or any(
            not isinstance(master, dict)
            or master.get("model") not in REFILL_MODELS
            or type(master.get("jobs")) is not int
            or not 1 <= master["jobs"] <= REFILL_MAX_TOTAL_JOBS
            or not PROTECTED_PORT_LOW
            <= master.get("base_port", -1)
            <= PROTECTED_PORT_HIGH
            or type(master.get("cell_port_count")) is not int
            or master["cell_port_count"] <= 0
            or master.get("port_low") != master.get("base_port")
            or master.get("port_high")
            != master["base_port"] + master["cell_port_count"] - 1
            or not PROTECTED_PORT_LOW
            <= master["port_low"]
            <= master["port_high"]
            <= PROTECTED_PORT_HIGH
            for master in masters
        )
        or record.get("refill_port_ranges") != [
            {
                "low": master["port_low"],
                "high": master["port_high"],
                "master_pid": master["pid"],
            }
            for master in sorted_masters
        ]
        or any(
            sorted_masters[index]["port_low"]
            <= sorted_masters[index - 1]["port_high"]
            for index in range(1, len(sorted_masters))
        )
        or any(
            not isinstance(listener, dict)
            or type(listener.get("port")) is not int
            or not PROTECTED_PORT_LOW
            <= listener["port"]
            <= PROTECTED_PORT_HIGH
            or not isinstance(listener.get("pids"), list)
            or len(listener["pids"]) != 1
            for listener in listeners
        )
    ):
        raise ValueError("refill coexistence capacity record is invalid")


def _artifact_roots() -> tuple[Path, ...]:
    return tuple(
        CAVEAT_SHOP_DATA_ROOT / scenario
        for scenario in (*EASY_SCENARIOS, *HARD_SCENARIOS)
    )


def _runtime_policy(
    runtime: dict,
    *,
    source_inventory_sha256: str,
    certification_sha256: str,
    limit_contract: dict | None = None,
) -> dict:
    base = _runtime_environment_policy(runtime)
    values = dict(base["set"])
    if limit_contract is None:
        limit_contract = runtime_limit_contract(
            near_fraction=LIMIT_NEAR_FRACTION,
        )
    values.update({
        "CAVEAT_LIMIT_CONTRACT_JSON": json.dumps(
            limit_contract,
            sort_keys=True,
            separators=(",", ":"),
        ),
        # Recorded by caveat-harness diagnostics.  These immutable
        # stamps make an excluded smoke prove which frozen source and hard
        # certificate actually produced it, rather than merely proving which
        # campaign happened to publish an arbitrary older run directory.
        "CAVEAT_RUNTIME_SOURCE_ATTESTATION":
            source_inventory_sha256,
        "CAVEAT_EVALUATION_INPUT_ATTESTATION":
            certification_sha256,
    })
    payload = {
        **{key: value for key, value in base.items() if key != "sha256"},
        "set": dict(sorted(values.items())),
        "per_run_set": {
            "TRAPI_REGIONS_OVERRIDE": "schedule.region_order",
        },
    }
    return {**payload, "sha256": _sha_bytes(_json_bytes(payload))}


def prepare_campaign(
    campaign_dir: Path,
    campaign_id: str,
    base_port: int,
    cert_report: Path,
    lockdiff_report: Path,
    weak_regions: tuple[str, ...],
    sol_regions: tuple[str, ...],
    superseded_campaign_dir: Path | None = None,
) -> None:
    campaign_dir = campaign_dir.resolve()
    manifest_path = campaign_dir / "campaign_manifest.json"
    if manifest_path.exists():
        verify_campaign(campaign_dir)
        print("campaign already frozen; no file was replaced")
        return
    validate_port_band(base_port, require_free=True)
    _validate_caps(CAPS)
    if CAPS["max_concurrent_browsers"] < CAMPAIGN_MAX_PARALLEL_RUNS:
        raise SystemExit(
            "frozen browser concurrency is below campaign launch parallelism"
        )
    # Import-time registration is the integration gate.  It deliberately runs
    # only at prepare time so the schedule/unit tests remain independent.
    import caveat.scaffolds  # noqa: F401
    from caveat.core.scaffold import SCAFFOLDS
    if "caveat-harness" not in SCAFFOLDS:
        raise SystemExit("caveat-harness scaffold is not registered")
    cert_ref = _validate_certification(cert_report)
    try:
        lockdiff_validation = _validate_lockdiff(lockdiff_report)
    except ValueError as exc:
        raise SystemExit(f"original-five lockdiff is invalid: {exc}") from exc
    try:
        weak_regions = _validate_weak_campaign_regions(
            weak_regions, label="weak model"
        )
        sol_regions = _validate_region_contract(
            sol_regions, label="sol-high"
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if superseded_campaign_dir is not None:
        try:
            verify_abandonment(superseded_campaign_dir)
        except ValueError as exc:
            raise SystemExit(
                f"superseded campaign disposition is invalid: {exc}"
            ) from exc
    schedule = build_schedule(
        campaign_id,
        base_port,
        weak_regions=weak_regions,
        sol_regions=sol_regions,
    )
    source_inventory = code_inventory()
    source_inventory_sha256 = _inventory_sha(source_inventory)
    runtime = runtime_dependency_manifest()
    limit_contract = runtime_limit_contract(
        near_fraction=LIMIT_NEAR_FRACTION,
    )
    policy = _runtime_policy(
        runtime,
        source_inventory_sha256=source_inventory_sha256,
        certification_sha256=cert_ref["sha256"],
        limit_contract=limit_contract,
    )
    artifacts = {}
    frozen_root = campaign_dir / FROZEN_CAVEAT_SHOP_DATA_RELATIVE
    if frozen_root.exists():
        raise SystemExit(
            "partial frozen input tree exists; preserve it and choose a new "
            "campaign directory"
        )
    for source in _artifact_roots():
        scenario = source.name
        inventory = _tree_inventory(source)
        artifacts[scenario] = {
            "files": inventory,
            "files_sha256": _inventory_sha(inventory),
        }
        target = frozen_root / scenario
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
    frozen_cert = campaign_dir / "frozen_inputs" / "certification_report.json"
    frozen_cert.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cert_report.resolve(), frozen_cert)
    frozen_lockdiff = campaign_dir / "frozen_inputs" / "lockdiff"
    if frozen_lockdiff.exists():
        raise SystemExit(
            "partial frozen lockdiff exists; preserve it and choose a new "
            "campaign directory"
        )
    frozen_lockdiff.mkdir(parents=True)
    frozen_lockdiff_before = frozen_lockdiff / "lockdiff_before.json"
    frozen_lockdiff_after = frozen_lockdiff / "lockdiff_after.json"
    shutil.copy2(
        Path(lockdiff_validation["before"]["path"]),
        frozen_lockdiff_before,
    )
    shutil.copy2(
        Path(lockdiff_validation["after"]["path"]),
        frozen_lockdiff_after,
    )
    try:
        frozen_lockdiff_validation = _validate_lockdiff(
            frozen_lockdiff_after
        )
    except ValueError as exc:
        raise SystemExit(
            f"frozen original-five lockdiff is invalid: {exc}"
        ) from exc
    lineage = None
    if superseded_campaign_dir is not None:
        try:
            lineage = _freeze_superseded_lineage(
                campaign_dir, superseded_campaign_dir
            )
        except ValueError as exc:
            raise SystemExit(
                f"cannot freeze superseded campaign lineage: {exc}"
            ) from exc
    frozen_inventory = _tree_inventory(frozen_root)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "campaign_id": _safe_id(campaign_id),
        "frozen_at_utc": _utcnow(),
        "source_root": str(ROOT),
        "design": {
            "variant": VARIANT,
            "arms": [
                {"arm": arm, "scaffold": scaffold}
                for arm, scaffold in ARMS
            ],
            "easy_combined_repeats": 3,
            "easy_clean_repeats": 1,
            "hard_combined_repeats": 2,
            "runs": 60,
            "runs_per_block": 10,
            "interleaved_and_arm_balanced": True,
            "launch_parallelism": {
                "max_active_runs": CAMPAIGN_MAX_PARALLEL_RUNS,
                "max_active_pairs": CAMPAIGN_MAX_PARALLEL_RUNS // 2,
                "pair_atomic_replenishment": True,
                "post_freeze_override_permitted": False,
            },
            "assignment": {
                "method": "constrained_sha256_deterministic_randomization",
                "seed": SCHEDULE_RANDOMIZATION_SEED,
                "scenario_pair_order": "hash_order_within_cohort",
                "arm_order": (
                    "two_of_five_hash_selected_baseline_first_on_odd_"
                    "repeats_then_exact_crossover_on_even_repeats"
                ),
                "route_order": (
                    "same_within_pair_then_pair_level_rotation_across_blocks"
                ),
                "within_pair_same_primary_and_failover_order": True,
            },
            "fresh_baseline": True,
        },
        "caps": CAPS,
        "limit_contract": limit_contract,
        "limit_near_fraction": LIMIT_NEAR_FRACTION,
        "runtime_dependencies": runtime,
        "runtime_environment_policy": policy,
        "base_port": base_port,
        "schedule": schedule,
        "metric_policy": {
            "headline": "optimal_selection_rate",
            "per_run": "optimal_selection",
            "complete_paired_denominators_required": True,
        },
        "probe_policy": {
            "stages": {
                "weak_before": [1, 2],
                "weak_mid": [3, 4],
                "sol_before": [5],
                "sol_mid": [6],
            },
            "scheduled_regions": {
                WEAK_LOGICAL: list(weak_regions),
                SOL_LOGICAL: list(sol_regions),
            },
            "weak_routes_explicitly_selected_at_freeze": True,
            "sol_routes_explicitly_selected_at_freeze": True,
            "concurrency_probe": True,
            "sol_large_request_probe": True,
            "max_checkpoint_age_seconds": PROBE_MAX_AGE_SECONDS,
            "predecessor_blocks": PROBE_PREDECESSOR_BLOCKS,
            "unhealthy_action": "pause_never_kill_active_runs",
        },
        "refill_coexistence_policy": _coexistence_policy(),
        "smoke_policy": SMOKE_POLICY,
        "attempt_policy": {
            "create_only_receipts": True,
            "create_only_numbered_report_snapshots": True,
            "never_mass_kill": True,
            "never_silently_relaunch": True,
            "behavioral_failures_are_never_refilled": True,
            "pre_agent_process_spawn_failures_are_refillable": True,
            "excluded_attempts_are_preserved": True,
        },
        "artifacts": artifacts,
        "frozen_artifact_root": (
            FROZEN_CAVEAT_SHOP_DATA_RELATIVE.as_posix()
        ),
        "frozen_artifact_inventory": frozen_inventory,
        "frozen_artifact_inventory_sha256": _inventory_sha(
            frozen_inventory
        ),
        "certification": {
            "source": cert_ref,
            "frozen_path": "frozen_inputs/certification_report.json",
            "frozen_sha256": _sha_file(frozen_cert),
        },
        "lockdiff": {
            "source_validation": lockdiff_validation,
            "frozen_validation": frozen_lockdiff_validation,
            "frozen_before_path":
                "frozen_inputs/lockdiff/lockdiff_before.json",
            "frozen_after_path":
                "frozen_inputs/lockdiff/lockdiff_after.json",
        },
        "lineage": lineage,
        "source_inventory": source_inventory,
        "source_inventory_sha256": source_inventory_sha256,
        "result_discovery": {
            "policy": "exact_manifest_paths_only",
            "expected_runs": 60,
            "expected_summaries": [
                row["summary_relpath"] for row in schedule
            ],
        },
    }
    _write_new(manifest_path, manifest)
    _write_new(
        campaign_dir / "campaign_manifest.sha256.json",
        {"path": "campaign_manifest.json", "sha256": _sha_file(manifest_path)},
    )
    verify_campaign(campaign_dir)
    print(
        f"FREEZE PASS: {campaign_id}; 60 paired runs; ports "
        f"{base_port}-{base_port + 99}"
    )


def _verify_inventory(root: Path, expected: dict, label: str) -> None:
    actual = _tree_inventory(root)
    if actual != expected:
        changed = sorted(
            key for key in set(actual) | set(expected)
            if actual.get(key) != expected.get(key)
        )
        raise SystemExit(
            f"{label} hash drift ({len(changed)} paths): "
            + ", ".join(changed[:20])
        )


def verify_campaign(campaign_dir: Path, *, quiet: bool = False) -> dict:
    campaign_dir = campaign_dir.resolve()
    manifest_path = campaign_dir / "campaign_manifest.json"
    sha_path = campaign_dir / "campaign_manifest.sha256.json"
    if not manifest_path.is_file() or not sha_path.is_file():
        raise SystemExit("campaign is not frozen")
    if _read_json(sha_path).get("sha256") != _sha_file(manifest_path):
        raise SystemExit("campaign manifest hash mismatch")
    manifest = _read_json(manifest_path)
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("kind") != KIND
    ):
        raise SystemExit("campaign kind/schema mismatch")
    validate_port_band(int(manifest["base_port"]), require_free=False)
    scheduled_regions = (
        manifest.get("probe_policy", {}).get("scheduled_regions", {})
    )
    try:
        weak_regions = _validate_weak_campaign_regions(
            scheduled_regions.get(WEAK_LOGICAL) or (),
            label="frozen weak model",
        )
        sol_regions = _validate_region_contract(
            scheduled_regions.get(SOL_LOGICAL) or (),
            label="frozen sol-high",
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if manifest["probe_policy"].get(
        "weak_routes_explicitly_selected_at_freeze"
    ) is not True:
        raise SystemExit("weak model frozen route-selection contract is missing")
    if manifest["probe_policy"].get(
        "sol_routes_explicitly_selected_at_freeze"
    ) is not True:
        raise SystemExit("sol-high frozen route-selection contract is missing")
    if (
        manifest["probe_policy"].get("max_checkpoint_age_seconds")
        != PROBE_MAX_AGE_SECONDS
        or manifest["probe_policy"].get("predecessor_blocks")
        != PROBE_PREDECESSOR_BLOCKS
    ):
        raise SystemExit("probe freshness/order policy drifted")
    if manifest.get(
        "refill_coexistence_policy"
    ) != _coexistence_policy():
        raise SystemExit("refill coexistence policy drifted")
    if manifest.get("design", {}).get("launch_parallelism") != {
        "max_active_runs": CAMPAIGN_MAX_PARALLEL_RUNS,
        "max_active_pairs": CAMPAIGN_MAX_PARALLEL_RUNS // 2,
        "pair_atomic_replenishment": True,
        "post_freeze_override_permitted": False,
    }:
        raise SystemExit("campaign launch parallelism policy drifted")
    expected_schedule = build_schedule(
        manifest["campaign_id"],
        int(manifest["base_port"]),
        weak_regions=weak_regions,
        sol_regions=sol_regions,
    )
    if manifest.get("schedule") != expected_schedule:
        raise SystemExit("campaign schedule differs from frozen code")
    _validate_caps(CAPS)
    sources = code_inventory()
    cert = manifest["certification"]
    source_cert = Path(cert["source"]["path"])
    source_cert_ref = _validate_certification(source_cert)
    if source_cert_ref != cert["source"]:
        raise SystemExit("source hard certification drifted")
    runtime = runtime_dependency_manifest()
    limit_contract = runtime_limit_contract(
        near_fraction=LIMIT_NEAR_FRACTION,
    )
    policy = _runtime_policy(
        runtime,
        source_inventory_sha256=_inventory_sha(sources),
        certification_sha256=source_cert_ref["sha256"],
        limit_contract=limit_contract,
    )
    if (
        manifest.get("caps") != CAPS
        or manifest.get("limit_contract") != limit_contract
        or manifest.get("runtime_dependencies") != runtime
        or manifest.get("runtime_environment_policy") != policy
    ):
        raise SystemExit("runtime dependency/cap/environment contract drifted")
    if (
        sources != manifest.get("source_inventory")
        or _inventory_sha(sources)
        != manifest.get("source_inventory_sha256")
    ):
        raise SystemExit("campaign source inventory drifted")
    frozen_root = campaign_dir / manifest["frozen_artifact_root"]
    _verify_inventory(
        frozen_root,
        manifest["frozen_artifact_inventory"],
        "frozen artifact snapshot",
    )
    for scenario, record in manifest["artifacts"].items():
        current = CAVEAT_SHOP_DATA_ROOT / scenario
        _verify_inventory(current, record["files"], f"runtime {scenario}")
        _verify_inventory(
            frozen_root / scenario,
            record["files"],
            f"frozen {scenario}",
        )
    frozen_cert = campaign_dir / cert["frozen_path"]
    if (
        not frozen_cert.is_file()
        or _sha_file(frozen_cert) != cert["frozen_sha256"]
        or _read_json(frozen_cert) != _read_json(source_cert)
    ):
        raise SystemExit("frozen hard certification drifted")
    lockdiff = manifest.get("lockdiff")
    if not isinstance(lockdiff, dict):
        raise SystemExit("frozen original-five lockdiff contract is missing")
    try:
        source_lockdiff = _validate_lockdiff(
            Path(lockdiff["source_validation"]["after"]["path"])
        )
        frozen_after = _campaign_child(
            campaign_dir,
            lockdiff["frozen_after_path"],
            "frozen lockdiff after",
        )
        frozen_before = _campaign_child(
            campaign_dir,
            lockdiff["frozen_before_path"],
            "frozen lockdiff before",
        )
        if frozen_before != frozen_after.with_name(
            "lockdiff_before.json"
        ):
            raise ValueError("frozen lockdiff paths are not a sibling pair")
        frozen_lockdiff = _validate_lockdiff(frozen_after)
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(
            f"original-five lockdiff verification failed: {exc}"
        ) from exc
    if (
        source_lockdiff != lockdiff.get("source_validation")
        or frozen_lockdiff != lockdiff.get("frozen_validation")
        or source_lockdiff["before"]["sha256"]
        != frozen_lockdiff["before"]["sha256"]
        or source_lockdiff["after"]["sha256"]
        != frozen_lockdiff["after"]["sha256"]
    ):
        raise SystemExit("original-five lockdiff hash/inventory drifted")
    try:
        _verify_superseded_lineage(
            campaign_dir, manifest.get("lineage")
        )
    except ValueError as exc:
        raise SystemExit(
            f"superseded campaign lineage verification failed: {exc}"
        ) from exc
    if not quiet:
        print(
            "VERIFY PASS: exact schedule, sources, artifacts, runtime, caps, "
            "certification, original-five lockdiff, and lineage unchanged"
        )
    return manifest


def _apply_environment_policy(manifest: dict, row: dict | None = None) -> dict:
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
    return env


def _parse_last_json(path: Path, expected_type: type):
    lines = [
        line.strip() for line in path.read_text(errors="replace").splitlines()
        if line.strip()
    ]
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, expected_type):
            return value
    raise SystemExit(f"no terminal {expected_type.__name__} JSON in {path}")


def _parse_concurrency(path: Path, logical_model: str) -> dict[str, int]:
    text = path.read_text(errors="replace")
    marker = "===== MAX SAFE CONCURRENCY (>=90% ok) ====="
    try:
        tail = text.split(marker, 1)[1]
        start = tail.index("{")
        value, _ = json.JSONDecoder().raw_decode(tail[start:])
        result = value[logical_model]
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"malformed concurrency probe {path}: {exc}") from exc
    if not isinstance(result, dict) or any(
        type(value) is not int for value in result.values()
    ):
        raise SystemExit("concurrency probe capacity schema is invalid")
    return result


def _scheduled_probe_regions(manifest: dict, logical_model: str) -> list[str]:
    configured = (
        manifest.get("probe_policy", {})
        .get("scheduled_regions", {})
        .get(logical_model)
    )
    if (
        not isinstance(configured, list)
        or not configured
        or any(not isinstance(region, str) or not region for region in configured)
        or len(configured) != len(set(configured))
    ):
        raise SystemExit(
            f"probe candidates are missing/malformed for {logical_model}"
        )
    mismatched = [
        row["run_id"]
        for row in manifest["schedule"]
        if row["logical_model"] == logical_model
        and (
            len(row["region_order"]) != len(configured)
            or set(row["region_order"]) != set(configured)
        )
    ]
    if mismatched:
        raise SystemExit(
            f"probe candidates differ from scheduled routes for "
            f"{logical_model}: {', '.join(mismatched[:10])}"
        )
    return configured


def validate_probe_evidence(
    manifest: dict,
    stage: str,
    small_log: Path,
    concurrency_log: Path,
    large_log: Path | None,
) -> dict:
    allowed_blocks = manifest["probe_policy"]["stages"].get(stage)
    if not allowed_blocks:
        raise SystemExit(f"unknown probe stage: {stage}")
    rows = [
        row for row in manifest["schedule"]
        if row["block"] in allowed_blocks
    ]
    logical_models = {row["logical_model"] for row in rows}
    if len(logical_models) != 1:
        raise SystemExit("probe stage spans more than one logical model")
    logical = next(iter(logical_models))
    probe_candidates = _scheduled_probe_regions(manifest, logical)
    small = _parse_last_json(small_log, dict)
    if logical not in small or not small[logical]:
        raise SystemExit(f"small probe found no live route for {logical}")
    if small[logical] != probe_candidates:
        raise SystemExit(
            "small probe live routes differ from the exact frozen order: "
            f"{small[logical]!r} != {probe_candidates!r}"
        )
    capacity = _parse_concurrency(concurrency_log, logical)
    max_primary_load = Counter()
    max_failover_load = Counter()
    for block in allowed_blocks:
        block_rows = [row for row in rows if row["block"] == block]
        primary_load = Counter(
            row["primary_region"] for row in block_rows
        )
        for region in probe_candidates:
            max_primary_load[region] = max(
                max_primary_load[region], primary_load[region]
            )
            # browser-use has one TRAPI fallback.  Prove that each route can
            # carry its normal primaries plus every run whose primary is one
            # other failed route and whose first fallback is this route.
            peak = primary_load[region]
            for failed_region in probe_candidates:
                if failed_region == region:
                    continue
                shifted = sum(
                    row["primary_region"] == failed_region
                    and len(row["region_order"]) >= 2
                    and row["region_order"][1] == region
                    for row in block_rows
                )
                peak = max(peak, primary_load[region] + shifted)
            max_failover_load[region] = max(
                max_failover_load[region], peak
            )
    insufficient = {
        region: {
            "capacity": capacity.get(region, 0),
            "scheduled_primary_load": max_primary_load[region],
            "scheduled_single_primary_failure_load": count,
        }
        for region, count in max_failover_load.items()
        if capacity.get(region, 0) < count
    }
    if insufficient:
        raise SystemExit(
            "probe capacity cannot carry scheduled load: "
            + json.dumps(insufficient, sort_keys=True)
        )
    large_healthy = None
    if logical == SOL_LOGICAL:
        if large_log is None:
            raise SystemExit("sol-high stage requires a large-request probe")
        large_healthy = _parse_last_json(large_log, list)
        if (
            any(
                not isinstance(region, str)
                or region not in SUPPORTED_TRAPI_REGIONS
                for region in large_healthy
            )
            or len(large_healthy) != len(set(large_healthy))
        ):
            raise SystemExit(
                "sol-high large-request probe returned malformed routes"
            )
        missing_large = [
            region
            for region in probe_candidates
            if region not in large_healthy
        ]
        if missing_large:
            raise SystemExit(
                "every frozen sol-high route must pass the large-request "
                "probe; missing: "
                + ", ".join(missing_large)
            )
    elif large_log is not None:
        raise SystemExit("weak-model stage must not attach a sol-large probe")
    return {
        "stage": stage,
        "blocks": allowed_blocks,
        "logical_model": logical,
        "probe_candidate_regions": probe_candidates,
        "small_routes": small[logical],
        "concurrency_capacity": capacity,
        "max_scheduled_primary_load": dict(max_primary_load),
        "max_scheduled_single_primary_failure_load": dict(
            max_failover_load
        ),
        "large_healthy_regions": large_healthy,
    }


def _probe_predecessor_evidence(
    campaign_dir: Path,
    manifest: dict,
    stage: str,
) -> list[dict]:
    policy = manifest.get("probe_policy", {})
    predecessor_map = policy.get("predecessor_blocks")
    if predecessor_map != PROBE_PREDECESSOR_BLOCKS:
        raise ValueError("probe predecessor policy is missing or drifted")
    blocks = predecessor_map.get(stage)
    if not isinstance(blocks, list):
        raise ValueError(f"unknown probe predecessor stage: {stage}")
    evidence = []
    for row in manifest["schedule"]:
        if row["block"] not in blocks:
            continue
        summary = campaign_dir / row["summary_relpath"]
        paths = {
            "trajectory": campaign_dir / row["trajectory_relpath"],
            "run_log": campaign_dir / row["run_log_relpath"],
            "launcher_log": campaign_dir / row["launcher_log_relpath"],
            "launch_receipt": (
                campaign_dir
                / "launch_receipts"
                / f"{row['run_id']}.json"
            ),
            "launch_receipt_hash": (
                campaign_dir
                / "launch_receipts"
                / f"{row['run_id']}.sha256.json"
            ),
        }
        missing = [
            label for label, path in {
                "summary": summary,
                **paths,
            }.items() if not path.is_file()
        ]
        if missing:
            raise ValueError(
                f"probe {stage} precedes completed block evidence for "
                f"{row['run_id']}: {', '.join(missing)}"
            )
        try:
            receipt_hash = _read_json(paths["launch_receipt_hash"])
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"predecessor receipt hash is unreadable for "
                f"{row['run_id']}: {exc}"
            ) from exc
        if receipt_hash != {
            "path": paths["launch_receipt"].name,
            "sha256": _sha_file(paths["launch_receipt"]),
        }:
            raise ValueError(
                f"predecessor receipt hash drifted for {row['run_id']}"
            )
        evidence.append({
            "run_id": row["run_id"],
            "block": row["block"],
            # Fresh optimal-selection evaluation may legitimately rewrite summary.json after a
            # probe. Its presence proves completion; the immutable trajectory,
            # logs and create-only launch receipt carry the hash evidence.
            "completion_summary_path": row["summary_relpath"],
            "immutable_files": {
                label: {
                    "path": str(path.relative_to(campaign_dir)),
                    "sha256": _sha_file(path),
                    "size": path.stat().st_size,
                }
                for label, path in paths.items()
            },
        })
    expected = len(blocks) * 10
    if len(evidence) != expected:
        raise ValueError(
            f"probe {stage} predecessor evidence is not exact: "
            f"{len(evidence)} != {expected}"
        )
    return evidence


def run_probe(campaign_dir: Path, stage: str, label: str) -> None:
    reject_abandoned(campaign_dir, "probe/launch")
    manifest = verify_campaign(campaign_dir, quiet=True)
    try:
        predecessor_evidence = _probe_predecessor_evidence(
            campaign_dir, manifest, stage
        )
    except ValueError as exc:
        raise SystemExit(f"probe stage order is invalid: {exc}") from exc
    audit_refill_coexistence(int(manifest["base_port"]))
    label = _safe_id(label)
    checkpoint = campaign_dir / "probes" / f"checkpoint_{label}.json"
    if checkpoint.exists():
        raise SystemExit(f"probe checkpoint already exists: {checkpoint}")
    blocks = manifest["probe_policy"]["stages"].get(stage)
    if not blocks:
        raise SystemExit(f"unknown probe stage: {stage}")
    logical = next(
        row["logical_model"] for row in manifest["schedule"]
        if row["block"] == blocks[0]
    )
    env = _apply_environment_policy(manifest)
    # probe_regions.py otherwise consults the repository's dynamic/default
    # routing table. Pin its candidate inventory to the exact frozen campaign
    # routes so the health gate tests what these runs will actually use.
    env["TRAPI_REGIONS_OVERRIDE"] = json.dumps(
        {logical: _scheduled_probe_regions(manifest, logical)},
        separators=(",", ":"),
    )
    # The generic routing helper normally retains dead routes as last-resort
    # fallbacks and historically omits Redmond.  Campaign checkpoints need
    # health evidence, not a fallback routing proposal, and may legitimately
    # freeze Redmond for weak model, so opt into its exact live-only mode.
    env["CAVEAT_PROBE_LIVE_ONLY"] = "1"
    env["CAVEAT_PROBE_INCLUDE_REDMOND"] = "1"
    probe_dir = campaign_dir / "probes"
    probe_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "small": probe_dir / f"small_{label}.log",
        "concurrency": probe_dir / f"concurrency_{label}.log",
    }
    if logical == SOL_LOGICAL:
        paths["large"] = probe_dir / f"large_{label}.log"
    if any(path.exists() for path in paths.values()):
        raise SystemExit("refusing to replace one or more probe logs")
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
        print(f"probe {stage}/{name}: {' '.join(commands[name])}", flush=True)
        with paths[name].open("x") as stream:
            completed = subprocess.run(
                commands[name],
                cwd=ROOT,
                env=env,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
            )
        print(paths[name].read_text(errors="replace"), end="", flush=True)
        if completed.returncode:
            raise SystemExit(
                f"{name} probe failed with exit {completed.returncode}; "
                "logs were preserved"
            )
    evidence = validate_probe_evidence(
        manifest,
        stage,
        paths["small"],
        paths["concurrency"],
        paths.get("large"),
    )
    coexistence = audit_refill_coexistence(int(manifest["base_port"]))
    record = {
        **evidence,
        "label": label,
        "published_at_utc": _utcnow(),
        "predecessor_evidence": predecessor_evidence,
        "refill_coexistence": coexistence,
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
    print(f"PROBE PASS: {label}")


def _campaign_child(campaign_dir: Path, relative: str, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{label} path is missing")
    campaign = campaign_dir.resolve()
    path = (campaign / relative).resolve()
    if campaign not in path.parents:
        raise ValueError(f"{label} path escapes campaign directory")
    return path


def verify_probe_checkpoint(
    campaign_dir: Path,
    manifest: dict,
    label: str,
    *,
    expected_stage: str | None = None,
    expected_block: int | None = None,
    expected_logical_model: str | None = None,
    freshness_at_utc: str | dt.datetime | None = None,
) -> dict:
    label = _safe_id(label)
    path = campaign_dir / "probes" / f"checkpoint_{label}.json"
    if not path.is_file():
        raise ValueError(f"probe checkpoint is missing: {label}")
    try:
        record = _read_json(path)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"probe checkpoint is unreadable: {exc}") from exc
    stage = record.get("stage")
    allowed_blocks = manifest["probe_policy"]["stages"].get(stage)
    if (
        not allowed_blocks
        or record.get("blocks") != allowed_blocks
        or record.get("label") != label
    ):
        raise ValueError("probe checkpoint stage/block/label contract drifted")
    published = _parse_utc(
        record.get("published_at_utc"),
        "probe checkpoint published_at_utc",
    )
    max_age = manifest.get("probe_policy", {}).get(
        "max_checkpoint_age_seconds"
    )
    if type(max_age) is not int or max_age <= 0:
        raise ValueError("probe checkpoint freshness policy is malformed")
    if freshness_at_utc is not None:
        if isinstance(freshness_at_utc, dt.datetime):
            at = freshness_at_utc
            if at.tzinfo is None:
                raise ValueError("probe freshness reference must be timezone-aware")
            at = at.astimezone(dt.timezone.utc)
        else:
            at = _parse_utc(
                freshness_at_utc, "probe freshness reference"
            )
        age = (at - published).total_seconds()
        if age < 0:
            raise ValueError("probe checkpoint post-dates launch")
        if age > max_age:
            raise ValueError(
                f"probe checkpoint is stale ({age:.1f}s > {max_age}s)"
            )
    if expected_stage is not None and stage != expected_stage:
        raise ValueError("probe checkpoint stage differs from run schedule")
    if expected_block is not None and expected_block not in allowed_blocks:
        raise ValueError("probe checkpoint does not cover the run block")
    rows = [
        row for row in manifest["schedule"]
        if row["block"] in allowed_blocks
    ]
    logical_models = {row["logical_model"] for row in rows}
    if len(logical_models) != 1:
        raise ValueError("probe checkpoint stage spans multiple models")
    logical = next(iter(logical_models))
    if (
        record.get("logical_model") != logical
        or (
            expected_logical_model is not None
            and logical != expected_logical_model
        )
    ):
        raise ValueError("probe checkpoint logical model drifted")
    expected_logs = {"small", "concurrency"}
    if logical == SOL_LOGICAL:
        expected_logs.add("large")
    logs = record.get("logs")
    if not isinstance(logs, dict) or set(logs) != expected_logs:
        raise ValueError("probe checkpoint log inventory is not exact")
    paths = {}
    for name in expected_logs:
        ref = logs[name]
        if not isinstance(ref, dict):
            raise ValueError(f"probe {name} log reference is malformed")
        log_path = _campaign_child(
            campaign_dir, ref.get("path"), f"probe {name}"
        )
        probes_root = (campaign_dir / "probes").resolve()
        if probes_root not in log_path.parents or not log_path.is_file():
            raise ValueError(f"probe {name} log is missing/outside probes")
        if (
            ref.get("sha256") != _sha_file(log_path)
            or ref.get("size") != log_path.stat().st_size
        ):
            raise ValueError(f"probe {name} log hash/size drifted")
        paths[name] = log_path
    try:
        evidence = validate_probe_evidence(
            manifest,
            stage,
            paths["small"],
            paths["concurrency"],
            paths.get("large"),
        )
    except SystemExit as exc:
        raise ValueError(f"probe evidence no longer validates: {exc}") from exc
    for key, expected in evidence.items():
        if record.get(key) != expected:
            raise ValueError(f"probe checkpoint {key} differs from logs")
    try:
        predecessor_evidence = _probe_predecessor_evidence(
            campaign_dir, manifest, stage
        )
    except ValueError as exc:
        raise ValueError(
            f"probe predecessor evidence no longer validates: {exc}"
        ) from exc
    if record.get("predecessor_evidence") != predecessor_evidence:
        raise ValueError("probe checkpoint predecessor evidence drifted")
    try:
        _validate_refill_coexistence_record(
            record.get("refill_coexistence"),
            int(manifest["base_port"]),
        )
    except ValueError as exc:
        raise ValueError(
            f"probe refill-coexistence evidence is invalid: {exc}"
        ) from exc
    return {
        "path": path,
        "sha256": _sha_file(path),
        "record": record,
    }


def _schedule_rows(
    manifest: dict,
    *,
    block: int | None = None,
    run_ids: set[str] | None = None,
) -> list[dict]:
    return [
        row for row in manifest["schedule"]
        if (block is None or row["block"] == block)
        and (run_ids is None or row["run_id"] in run_ids)
    ]


def _attempt_number(campaign_dir: Path, run_id: str) -> int:
    root = campaign_dir / "excluded_attempts" / run_id
    numbers = []
    if root.is_dir():
        for path in root.glob("attempt_*"):
            try:
                numbers.append(int(path.name.split("_", 1)[1]))
            except (ValueError, IndexError):
                raise SystemExit(f"malformed attempt directory: {path}")
    if numbers and sorted(numbers) != list(range(1, max(numbers) + 1)):
        raise SystemExit(f"attempt numbering has gaps for {run_id}")
    return max(numbers, default=0) + 1


def _write_receipt(
    campaign_dir: Path,
    manifest: dict,
    row: dict,
    checkpoint_label: str,
    env: dict,
) -> None:
    coexistence = audit_refill_coexistence(int(manifest["base_port"]))
    launched_at_utc = _utcnow()
    try:
        smoke_gate = verify_smoke_gate(campaign_dir, manifest)
    except ValueError as exc:
        raise SystemExit(f"launch smoke gate is invalid: {exc}") from exc
    try:
        checkpoint = verify_probe_checkpoint(
            campaign_dir,
            manifest,
            checkpoint_label,
            expected_stage=row["probe_stage"],
            expected_block=row["block"],
            expected_logical_model=row["logical_model"],
            freshness_at_utc=launched_at_utc,
        )
    except ValueError as exc:
        raise SystemExit(
            f"checkpoint {checkpoint_label} is invalid for "
            f"{row['run_id']}: {exc}"
        ) from exc
    policy = manifest["runtime_environment_policy"]
    observed = {key: env.get(key) for key in policy["set"]}
    if observed != {
        key: str(value) for key, value in policy["set"].items()
    }:
        raise SystemExit("launch environment differs from frozen policy")
    expected_override = json.dumps(
        {row["logical_model"]: row["region_order"]},
        separators=(",", ":"),
    )
    if env.get("TRAPI_REGIONS_OVERRIDE") != expected_override:
        raise SystemExit("TRAPI region override differs from schedule")
    if "STOREFRONT_OPS_TOKEN" in env:
        raise SystemExit("STOREFRONT_OPS_TOKEN survived launch sanitization")
    contract = {
        "manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "source_inventory_sha256": manifest[
            "source_inventory_sha256"
        ],
        "runtime_dependencies_sha256": manifest[
            "runtime_dependencies"
        ]["sha256"],
        "runtime_environment_policy_sha256": policy["sha256"],
        "caps": manifest["caps"],
        "limit_near_fraction": manifest["limit_near_fraction"],
        "smoke_gate_sha256": smoke_gate["sha256"],
    }
    receipt = {
        "run_id": row["run_id"],
        "block": row["block"],
        "cohort": row["cohort"],
        "scenario": row["scenario"],
        "condition": row["condition"],
        "variant": row["variant"],
        "arm": row["arm"],
        "scaffold": row["scaffold"],
        "model_request": row["model_request"],
        "model_recorded": row["model_recorded"],
        "repeat": row["repeat"],
        "run_name": row["run_name"],
        "port": row["port"],
        "primary_region": row["primary_region"],
        "region_order": row["region_order"],
        "attempt": _attempt_number(campaign_dir, row["run_id"]),
        "probe_checkpoint": checkpoint_label,
        "probe_checkpoint_sha256": checkpoint["sha256"],
        "launched_at_utc": launched_at_utc,
        "refill_coexistence": coexistence,
        "runtime_contract": {
            **contract,
            "contract_sha256": _sha_bytes(_json_bytes(contract)),
            "trapi_regions_override": expected_override,
            "cli_max_steps": manifest["caps"]["max_steps"],
        },
    }
    receipt_path = (
        campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
    )
    _write_new(receipt_path, receipt)
    _write_new(
        campaign_dir / "launch_receipts"
        / f"{row['run_id']}.sha256.json",
        {
            "path": receipt_path.name,
            "sha256": _sha_file(receipt_path),
        },
    )


def verify_launch_receipt(
    campaign_dir: Path,
    manifest: dict,
    row: dict,
) -> dict:
    path = campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
    sha_path = (
        campaign_dir / "launch_receipts"
        / f"{row['run_id']}.sha256.json"
    )
    if not path.is_file() or not sha_path.is_file():
        raise ValueError("create-only launch receipt is missing")
    try:
        sha_record = _read_json(sha_path)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"launch receipt hash record is unreadable: {exc}") from exc
    if sha_record != {"path": path.name, "sha256": _sha_file(path)}:
        raise ValueError("create-only launch receipt hash drifted")
    try:
        receipt = _read_json(path)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"launch receipt is unreadable: {exc}") from exc
    expected_fields = {
        key: row[key] for key in (
            "run_id", "block", "cohort", "scenario", "condition", "variant",
            "arm", "scaffold", "model_request", "model_recorded", "repeat",
            "run_name", "port", "primary_region", "region_order",
        )
    }
    wrong = {
        key: {"actual": receipt.get(key), "expected": expected}
        for key, expected in expected_fields.items()
        if receipt.get(key) != expected
    }
    if wrong:
        raise ValueError(
            "launch receipt run identity drifted: "
            + json.dumps(wrong, sort_keys=True)
        )
    if (
        type(receipt.get("attempt")) is not int
        or receipt["attempt"] < 1
        or not isinstance(receipt.get("launched_at_utc"), str)
    ):
        raise ValueError("launch receipt attempt/time is malformed")
    checkpoint_label = receipt.get("probe_checkpoint")
    if not isinstance(checkpoint_label, str) or not checkpoint_label:
        raise ValueError("launch receipt has no probe checkpoint")
    checkpoint = verify_probe_checkpoint(
        campaign_dir,
        manifest,
        checkpoint_label,
        expected_stage=row["probe_stage"],
        expected_block=row["block"],
        expected_logical_model=row["logical_model"],
        freshness_at_utc=receipt["launched_at_utc"],
    )
    if receipt.get("probe_checkpoint_sha256") != checkpoint["sha256"]:
        raise ValueError("launch receipt checkpoint hash drifted")
    _validate_refill_coexistence_record(
        receipt.get("refill_coexistence"),
        int(manifest["base_port"]),
    )
    smoke_gate = verify_smoke_gate(campaign_dir, manifest)
    policy = manifest["runtime_environment_policy"]
    contract = {
        "manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "source_inventory_sha256": manifest[
            "source_inventory_sha256"
        ],
        "runtime_dependencies_sha256": manifest[
            "runtime_dependencies"
        ]["sha256"],
        "runtime_environment_policy_sha256": policy["sha256"],
        "caps": manifest["caps"],
        "limit_near_fraction": manifest["limit_near_fraction"],
        "smoke_gate_sha256": smoke_gate["sha256"],
    }
    expected_override = json.dumps(
        {row["logical_model"]: row["region_order"]},
        separators=(",", ":"),
    )
    expected_runtime = {
        **contract,
        "contract_sha256": _sha_bytes(_json_bytes(contract)),
        "trapi_regions_override": expected_override,
        "cli_max_steps": manifest["caps"]["max_steps"],
    }
    if receipt.get("runtime_contract") != expected_runtime:
        raise ValueError("launch receipt runtime contract drifted")
    launcher = campaign_dir / row["launcher_log_relpath"]
    if not launcher.is_file():
        raise ValueError("create-only launcher log is missing")
    return {
        "path": path,
        "sha256": _sha_file(path),
        "receipt": receipt,
        "checkpoint_sha256": checkpoint["sha256"],
        "refill_coexistence": receipt["refill_coexistence"],
    }


def _smoke_order_evidence(
    run_dir: Path,
    chosen: str,
    quantity: int,
) -> dict:
    """Independently prove the selected item in one exact placed order."""

    databases = sorted(run_dir.glob("caveat_shop_*.db"))
    if (
        len(databases) != 1
        or databases[0].is_symlink()
        or not databases[0].is_file()
        or re.fullmatch(r"caveat_shop_[0-9]+\.db", databases[0].name) is None
    ):
        raise ValueError(
            "smoke must contain exactly one regular caveat_shop_<port>.db"
        )
    database = databases[0]
    required_columns = {
        "order": {
            "id", "order_number", "status", "placed_at",
        },
        "orderitem": {
            "id", "order_id", "product_id", "quantity",
            "status", "unit_price", "total_price",
        },
        "product": {"id", "asin"},
    }
    try:
        with sqlite3.connect(
            database.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=5,
        ) as connection:
            connection.row_factory = sqlite3.Row
            quick_check = [
                tuple(row)
                for row in connection.execute("PRAGMA quick_check")
            ]
            if quick_check != [("ok",)]:
                raise ValueError("smoke evaluator database fails quick_check")
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if not set(required_columns).issubset(tables):
                raise ValueError(
                    "smoke evaluator database lacks order/product tables"
                )
            for table, expected_columns in required_columns.items():
                observed_columns = {
                    str(row[1])
                    for row in connection.execute(
                        f'PRAGMA table_info("{table}")'
                    )
                }
                if not expected_columns.issubset(observed_columns):
                    raise ValueError(
                        f"smoke evaluator database {table} schema drifted"
                    )
            item_count = int(connection.execute(
                "SELECT COUNT(*) FROM orderitem"
            ).fetchone()[0])
            active_orders = [
                int(row[0])
                for row in connection.execute(
                    'SELECT id FROM "order" '
                    "WHERE status = 'processing' ORDER BY id"
                )
            ]
            rows = list(connection.execute(
                """
                SELECT
                    o.id AS order_id,
                    o.order_number AS order_number,
                    o.status AS order_status,
                    o.placed_at AS placed_at,
                    oi.id AS order_item_id,
                    oi.quantity AS quantity,
                    oi.status AS item_status,
                    oi.unit_price AS unit_price,
                    oi.total_price AS total_price,
                    p.asin AS asin
                FROM orderitem AS oi
                JOIN "order" AS o ON o.id = oi.order_id
                JOIN product AS p ON p.id = oi.product_id
                ORDER BY o.id, oi.id
                """
            ))
    except sqlite3.Error as exc:
        raise ValueError(
            f"smoke evaluator database is unreadable: {exc}"
        ) from exc
    if item_count != len(rows):
        raise ValueError(
            "smoke evaluator database contains unresolvable purchased "
            "line items"
        )
    if len(active_orders) != 1:
        raise ValueError(
            "smoke evaluator database does not contain exactly one "
            "unambiguous newly placed order"
        )
    active_order_id = active_orders[0]
    active_rows = [
        row for row in rows
        if int(row["order_id"]) == active_order_id
    ]
    chosen_rows = [
        row for row in active_rows
        if row["asin"] == chosen
    ]
    if len(chosen_rows) != 1:
        raise ValueError(
            "smoke evaluator order does not contain the selected product "
            "exactly once"
        )
    row = chosen_rows[0]
    if (
        row["order_status"] != "processing"
        or row["item_status"] != "pending"
        or type(row["quantity"]) is not int
        or row["quantity"] != quantity
        or not isinstance(row["order_number"], str)
        or not row["order_number"].strip()
        or not isinstance(row["placed_at"], str)
        or not row["placed_at"].strip()
    ):
        raise ValueError(
            "smoke evaluator order does not bind the chosen product, "
            "requested quantity, and placed-order state"
        )
    for item in active_rows:
        if (
            item["order_status"] != "processing"
            or item["item_status"] != "pending"
            or type(item["quantity"]) is not int
            or item["quantity"] <= 0
        ):
            raise ValueError(
                "smoke evaluator order contains a malformed purchased "
                "line item"
            )
        for name in ("unit_price", "total_price"):
            value = item[name]
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or float(value) < 0
            ):
                raise ValueError(
                    f"smoke evaluator order {name} is malformed"
                )
    extra_asins = [
        str(item["asin"])
        for item in active_rows
        if int(item["order_item_id"]) != int(row["order_item_id"])
    ]
    return {
        "database": database.name,
        "database_sha256": _sha_file(database),
        "order_id": int(row["order_id"]),
        "order_number": row["order_number"],
        "order_status": row["order_status"],
        "order_item_id": int(row["order_item_id"]),
        "item_status": row["item_status"],
        "asin": row["asin"],
        "quantity": int(row["quantity"]),
        "placed_line_item_count": len(active_rows),
        "additional_line_item_count": len(extra_asins),
        "additional_asins": extra_asins,
    }


def _validate_smoke_run(
    run_dir: Path,
    spec: dict,
    manifest: dict,
) -> dict:
    run_dir = run_dir.resolve()
    required = {
        name: run_dir / name
        for name in ("summary.json", "trajectory.json", "run.log")
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise ValueError("smoke run lacks: " + ", ".join(missing))
    try:
        summary = _read_json(required["summary.json"])
        trajectory = _read_json(required["trajectory.json"])
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"smoke JSON is unreadable: {exc}") from exc
    expected = {
        "env": "caveat_shop",
        "scaffold": spec["scaffold"],
        "model": spec["model_recorded"],
        "task_id": f"{spec['scenario']}-{spec['variant']}",
        "condition": spec["condition"],
    }
    for key, value in expected.items():
        if summary.get(key) != value or trajectory.get(key) != value:
            raise ValueError(f"smoke {key} identity differs from policy")
    outcome = summary.get("outcome")
    chosen = summary.get("chosen")
    success = summary.get("success")
    if (
        not isinstance(outcome, str)
        or outcome in {"none", "error", "skipped"}
        or not outcome.strip()
        or not isinstance(chosen, str)
        or not chosen.strip()
        or type(success) is not bool
        or summary.get("error") not in (None, "")
    ):
        raise ValueError(
            "smoke lacks a completed benchmark outcome and nonempty choice"
        )
    evaluation = trajectory.get("evaluation")
    if (
        not isinstance(evaluation, dict)
        or evaluation.get("outcome") != outcome
        or evaluation.get("chosen") != chosen
        or evaluation.get("success") is not success
    ):
        raise ValueError(
            "smoke summary outcome identity differs from trajectory "
            "evaluation"
        )
    optimal_selection_values = {}
    for field in ("optimal_selection",):
        value = summary.get(field)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or not 0 <= float(value) <= 1
        ):
            raise ValueError(
                f"smoke optimal-selection value is missing or malformed: {field}"
            )
        optimal_selection_values[field] = float(value)
    steps = summary.get("num_steps")
    seconds = summary.get("seconds")
    if (
        type(steps) is not int
        or not 0 < steps < manifest["caps"]["max_steps"]
    ):
        raise ValueError("smoke did not complete a non-bound agent step")
    if (
        not isinstance(seconds, (int, float))
        or isinstance(seconds, bool)
        or not 0 <= float(seconds)
        < manifest["caps"]["cell_timeout_seconds"] * 0.99
    ):
        raise ValueError("smoke duration is missing or near its backstop")
    stats = trajectory.get("stats")
    if (
        not isinstance(stats, dict)
        or stats.get("error")
        or stats.get("caveat_harness_stats_error")
    ):
        raise ValueError("smoke trajectory contains a scaffold/stats error")
    diagnostics = stats.get("caveat_harness")
    if not isinstance(diagnostics, dict):
        raise ValueError("smoke lacks CAVEAT-Harness instrumentation")
    missing_diagnostics = [
        name for name in CAVEAT_HARNESS_DIAGNOSTIC_FIELDS
        if name not in diagnostics
    ]
    if missing_diagnostics:
        raise ValueError(
            "smoke lacks CAVEAT-Harness diagnostic fields: "
            + ", ".join(missing_diagnostics)
        )
    integer_diagnostics = (
        "contract_compile_calls",
        "constraint_count",
        "objective_count",
        "decision_checkpoint_calls",
        "decision_checkpoint_rejections",
        "decision_checkpoint_approvals",
        "checkpoint_candidate_count",
        "frontier_inspected_count",
        "frontier_advertised_count",
        "frontier_advertised_page_count",
        "frontier_enumerated_page_count",
        "structured_max_attempts_observed",
        "structured_attempt_exhaustions",
        "auxiliary_calls",
        "auxiliary_tokens",
    )
    if any(
        type(diagnostics.get(name)) is not int
        or diagnostics[name] < 0
        for name in integer_diagnostics
    ):
        raise ValueError("smoke CAVEAT-Harness counters are malformed")
    if (
        diagnostics["contract_compile_calls"] != 1
        or (
            diagnostics["decision_checkpoint_approvals"]
            + diagnostics["decision_checkpoint_rejections"]
            != diagnostics["decision_checkpoint_calls"]
        )
        or diagnostics["structured_max_attempts_observed"] > 4
        or diagnostics["structured_attempt_exhaustions"] not in {0, 1}
        or (
            diagnostics["decision_checkpoint_calls"] == 0
            and diagnostics.get("frontier_coverage_mode") is not None
        )
        or (
            diagnostics["decision_checkpoint_calls"] > 0
            and diagnostics.get("frontier_coverage_mode")
            not in {"advertised_total", "finite_pages"}
        )
        or not isinstance(diagnostics.get("contract_sha256"), str)
        or re.fullmatch(
            r"[0-9a-f]{64}", diagnostics["contract_sha256"]
        ) is None
        or _contract_diagnostic_error(diagnostics) is not None
        or diagnostics.get("search_mode")
        not in {"best_available", "satisfice"}
        or not isinstance(diagnostics.get("auxiliary_seconds"), (int, float))
        or isinstance(diagnostics.get("auxiliary_seconds"), bool)
        or not math.isfinite(float(diagnostics["auxiliary_seconds"]))
        or diagnostics["auxiliary_seconds"] < 0
        or (
            diagnostics.get("approved_candidate_id") is not None
            and (
                not isinstance(
                    diagnostics.get("approved_candidate_id"), str
                )
                or not diagnostics["approved_candidate_id"]
            )
        )
        or not isinstance(diagnostics.get("limit_observations"), dict)
        or set(diagnostics["limit_observations"])
        != {"structured_response_attempts"}
    ):
        raise ValueError(
            "smoke CAVEAT-Harness diagnostic relationships are malformed"
        )
    structured_observation = diagnostics["limit_observations"][
        "structured_response_attempts"
    ]
    structured_details = (
        structured_observation.get("observations")
        if isinstance(structured_observation, dict) else None
    )
    expected_structured_touch = int(
        diagnostics["structured_max_attempts_observed"] >= 4
    )
    if (
        not isinstance(structured_observation, dict)
        or type(structured_observation.get("touched_count")) is not int
        or structured_observation["touched_count"] < 0
        or structured_observation["touched_count"]
        != expected_structured_touch
        or not isinstance(structured_details, dict)
        or set(structured_details) != {
            "max_attempts",
            "attempts",
            "rejected_attempts",
            "exhaustions",
        }
        or structured_details.get("max_attempts") != 4
        or type(structured_details.get("attempts")) is not int
        or structured_details["attempts"]
        != diagnostics["structured_max_attempts_observed"]
        or type(structured_details.get("rejected_attempts")) is not int
        or not 0 <= structured_details["rejected_attempts"] <= (
            structured_details["attempts"]
        )
        or structured_details.get("exhaustions")
        != diagnostics["structured_attempt_exhaustions"]
    ):
        raise ValueError(
            "smoke structured-response limit observation is malformed"
        )
    # CAVEAT-Harness instrumentation below is restricted to general provenance
    # and resource-limit fields.  Whether a real purchase completed is proved
    # independently from evaluator-owned state, not an agent-authored flag.
    order_evidence = _smoke_order_evidence(
        run_dir, chosen, spec["quantity"]
    )
    limit_errors = validate_limit_audit(
        stats.get("limit_audit"),
        manifest.get("limit_contract"),
        (
            "caveat_harness"
            if spec["scaffold"] == "caveat-harness"
            else "baseline"
        ),
    )
    if limit_errors:
        raise ValueError(
            "smoke lacks a complete exact limit audit: "
            + "; ".join(limit_errors)
        )
    limit_categories = stats["limit_audit"]["categories"]
    touched_limits = [
        f"{category}.{name}"
        for category in ("safety_backstops", "lossy_context_limits")
        for name, record in limit_categories.get(category, {}).items()
        if record["touched_count"] > 0
    ]
    if touched_limits:
        raise ValueError(
            "smoke touched a declared safety limit: "
            + ", ".join(touched_limits)
        )
    evaluate_store = stats.get("evaluate_result_store")
    evaluate_store_fields = {
        "schema_version",
        "inline_chars",
        "single_max_chars",
        "max_serialized_chars",
        "single_bound_touched_count",
        "integrity_failure_count",
        "max_bytes",
        "bytes",
        "byte_bound_touched_count",
        "max_responses",
        "responses",
        "response_bound_touched_count",
        "records",
    }
    if (
        not isinstance(evaluate_store, dict)
        or set(evaluate_store) != evaluate_store_fields
        or evaluate_store.get("schema_version") != 1
    ):
        raise ValueError(
            "smoke lacks an exact common evaluate-result store audit"
        )
    if any(
        type(evaluate_store.get(name)) is not int
        or evaluate_store[name] < 0
        for name in evaluate_store_fields - {"schema_version"}
    ):
        raise ValueError("smoke evaluate-result store counters are malformed")
    frozen_safety = manifest["limit_contract"]["categories"][
        "safety_backstops"
    ]
    if (
        evaluate_store["inline_chars"] != 9999
        or evaluate_store["single_max_chars"]
        != frozen_safety["evaluate_result_single_chars"]["configured"]
        or evaluate_store["max_bytes"]
        != frozen_safety["evaluate_result_store_bytes"]["configured"]
        or evaluate_store["max_responses"]
        != frozen_safety[
            "evaluate_result_store_responses"
        ]["configured"]
        or evaluate_store["records"] > evaluate_store["responses"]
        or (
            evaluate_store["responses"] == 0
            and (
                evaluate_store["records"] != 0
                or evaluate_store["bytes"] != 0
            )
        )
        or (
            evaluate_store["responses"] > 0
            and (
                evaluate_store["records"] == 0
                or evaluate_store["max_serialized_chars"]
                <= evaluate_store["inline_chars"]
                or evaluate_store["bytes"]
                < evaluate_store["records"]
                * (evaluate_store["inline_chars"] + 1)
            )
        )
        or evaluate_store["max_serialized_chars"]
        > evaluate_store["single_max_chars"]
        or evaluate_store["integrity_failure_count"] != 0
        or evaluate_store["single_bound_touched_count"] != 0
        or evaluate_store["byte_bound_touched_count"] != 0
        or evaluate_store["response_bound_touched_count"] != 0
    ):
        raise ValueError(
            "smoke evaluate-result store differs from the campaign "
            "or touched a bound"
        )
    expected_store_audit = {
        "evaluate_result_single_chars": {
            "record": limit_categories["safety_backstops"][
                "evaluate_result_single_chars"
            ],
            "touched_count": 0,
            "observations": {
                "max_serialized_chars":
                    evaluate_store["max_serialized_chars"],
                "maximum": evaluate_store["single_max_chars"],
            },
        },
        "evaluate_result_store_bytes": {
            "record": limit_categories["safety_backstops"][
                "evaluate_result_store_bytes"
            ],
            "touched_count": 0,
            "observations": {
                "used": evaluate_store["bytes"],
                "maximum": evaluate_store["max_bytes"],
                "utilization": (
                    evaluate_store["bytes"]
                    / evaluate_store["max_bytes"]
                ),
                "records": evaluate_store["records"],
            },
        },
        "evaluate_result_store_responses": {
            "record": limit_categories["safety_backstops"][
                "evaluate_result_store_responses"
            ],
            "touched_count": 0,
            "observations": {
                "used": evaluate_store["responses"],
                "maximum": evaluate_store["max_responses"],
                "utilization": (
                    evaluate_store["responses"]
                    / evaluate_store["max_responses"]
                ),
                "records": evaluate_store["records"],
            },
        },
        "evaluate_result_spill": {
            "record": limit_categories["fixed_architecture"][
                "evaluate_result_spill"
            ],
            "touched_count": evaluate_store["responses"],
            "observations": {
                "spilled_responses": evaluate_store["responses"],
                "unique_records": evaluate_store["records"],
                "stored_bytes": evaluate_store["bytes"],
                "max_serialized_chars":
                    evaluate_store["max_serialized_chars"],
            },
        },
    }
    for name, expected_audit in expected_store_audit.items():
        record = expected_audit["record"]
        if (
            record.get("touched_count")
            != expected_audit["touched_count"]
            or not isinstance(record.get("observations"), dict)
            or any(
                record["observations"].get(key) != value
                for key, value in expected_audit[
                    "observations"
                ].items()
            )
        ):
            raise ValueError(
                "smoke evaluate-result store and limit audit disagree: "
                f"{name}"
            )
    near_policy = manifest["limit_contract"].get("near_policy")
    named_near = {}
    for policy_name in (
        "evaluate_result_single_fraction",
        "evaluate_result_store_fraction",
    ):
        value = (
            near_policy.get(policy_name)
            if isinstance(near_policy, dict) else None
        )
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or not 0 < float(value) <= 1
            or float(value)
            != float(manifest["limit_near_fraction"])
        ):
            raise ValueError(
                "smoke evaluate-result named near policy is malformed "
                f"or differs from campaign policy: {policy_name}"
            )
        named_near[policy_name] = float(value)
    for name, used_key, max_key, near_fraction in (
        (
            "single result",
            "max_serialized_chars",
            "single_max_chars",
            named_near["evaluate_result_single_fraction"],
        ),
        (
            "bytes",
            "bytes",
            "max_bytes",
            named_near["evaluate_result_store_fraction"],
        ),
        (
            "responses",
            "responses",
            "max_responses",
            named_near["evaluate_result_store_fraction"],
        ),
    ):
        utilization = (
            evaluate_store[used_key] / evaluate_store[max_key]
        )
        if utilization >= near_fraction:
            raise ValueError(
                "smoke evaluate-result store "
                f"{name} utilization is near its backstop: "
                f"{utilization:.6f}"
            )
    context_audit = stats.get("context_cap_audit")
    frozen_context = manifest["runtime_dependencies"][
        "agent_behavior_limits"
    ]["context_limits"]
    if (
        not isinstance(context_audit, dict)
        or context_audit.get("schema_version") != 1
        or context_audit.get("complete") is not True
        or type(context_audit.get("history_items")) is not int
        or context_audit["history_items"] < 0
        or not isinstance(context_audit.get("limits"), dict)
        or set(context_audit["limits"]) != set(frozen_context)
    ):
        raise ValueError("smoke lacks a complete common context-cap audit")
    lossy_context_names = set(
        manifest["limit_contract"]["categories"][
            "lossy_context_limits"
        ]
    )
    action_partition_declared = (
        manifest["limit_contract"]["categories"].get(
            "fixed_architecture", {}
        ).get(_ACTION_ERROR_FIXED_RECORD)
        is not None
    )
    raw_context_touched = []
    for name, configured in frozen_context.items():
        record = context_audit["limits"][name]
        if (
            not isinstance(record, dict)
            or record.get("configured") != configured
            or type(record.get("touched_count")) is not int
            or record["touched_count"] < 0
            or type(record.get("max_observed")) is not int
            or record["max_observed"] < 0
        ):
            raise ValueError(
                f"smoke context-cap audit is invalid: {name}"
            )
        if record["touched_count"]:
            raw_context_touched.append(name)
        if (
            name in lossy_context_names
            and record["touched_count"] != 0
            and not (
                name == "action_error_chars"
                and action_partition_declared
            )
        ):
            raise ValueError(
                f"smoke context-cap audit touched a lossy limit: {name}"
            )
    action_error_audit, action_error_errors = (
        validate_action_error_partition(
            stats,
            context_audit,
            limit_categories,
            manifest["limit_contract"],
        )
    )
    if action_error_errors:
        raise ValueError(
            "smoke action-error audit is not exactly cross-bound: "
            + "; ".join(action_error_errors)
        )
    extract_raw = context_audit["limits"].get("extract_memory_chars")
    extract_fixed = limit_categories.get("fixed_architecture", {}).get(
        "extract_result_file_externalization"
    )
    fixed_extract_declared = (
        manifest["limit_contract"]["categories"].get(
            "fixed_architecture", {}
        ).get("extract_result_file_externalization")
    )
    if fixed_extract_declared is not None:
        if (
            not isinstance(extract_raw, dict)
            or not isinstance(extract_fixed, dict)
            or extract_fixed.get("touched_count")
            != extract_raw.get("touched_count")
            or extract_fixed.get("observations", {}).get(
                "externalized_results"
            ) != extract_raw.get("touched_count")
            or extract_fixed.get("observations", {}).get(
                "max_result_chars"
            ) != extract_raw.get("max_observed")
            or extract_fixed.get("observations", {}).get(
                "threshold_chars"
            ) != extract_raw.get("configured")
        ):
            raise ValueError(
                "smoke extraction externalization audit is not cross-bound"
            )
    elif "extract_memory_chars" not in lossy_context_names:
        raise ValueError(
            "smoke frozen contract classifies extraction routing nowhere"
        )
    if diagnostics.get(
        "runtime_source_attestation"
    ) != manifest["source_inventory_sha256"]:
        raise ValueError(
            "smoke does not attest the frozen source inventory"
        )
    if diagnostics.get(
        "evaluation_input_attestation"
    ) != manifest["certification"]["frozen_sha256"]:
        raise ValueError(
            "smoke does not attest the frozen hard certification"
        )
    log_text = required["run.log"].read_text(errors="replace")
    forbidden = (
        "CAVEAT_CELL_TIMEOUT_BOUND",
        "Stopping due to 1000 consecutive failures",
        "ModelOutputTruncatedError",
        "[Truncated after 20000 characters]",
        "[Truncated after 67108864 characters]",
        "CAVEAT_EVALUATE_STORE_INTEGRITY_FAILURE",
        "[Content truncated at 60k characters]",
    )
    hit = next((marker for marker in forbidden if marker in log_text), None)
    if hit:
        raise ValueError(f"smoke touched a harness bound: {hit}")
    return {
        "identity": expected,
        "steps": steps,
        "seconds": float(seconds),
        "outcome": outcome,
        "success": success,
        "chosen": chosen,
        "optimal_selection_values": optimal_selection_values,
        "evaluator_order_proven": True,
        "order_evidence": order_evidence,
        "diagnostics_present": True,
        "context_cap_audit_complete": True,
        "action_error_audit": action_error_audit,
        "action_error_audit_complete": not action_error_errors,
        "context_caps_untouched": not raw_context_touched,
        "lossy_context_caps_untouched": True,
        "fixed_context_architecture_touches": sorted(
            (set(raw_context_touched) - lossy_context_names)
            | (
                {"action_error_chars"}
                if (
                    action_partition_declared
                    and action_error_audit.get(
                        "agent_output_validation", {}
                    ).get("over_cap_count", 0) > 0
                ) else set()
            )
        ),
        "limit_audit_complete": True,
        "declared_safety_limits_untouched": True,
        "evaluate_result_store": evaluate_store,
        "evaluate_result_store_below_near_threshold": True,
        "runtime_source_attestation": diagnostics[
            "runtime_source_attestation"
        ],
        "evaluation_input_attestation": diagnostics[
            "evaluation_input_attestation"
        ],
    }


def _verify_smoke_launch_evidence(
    campaign_dir: Path,
    manifest: dict,
    smoke_name: str,
    evidence_dir: Path,
    *,
    require_original_path: bool,
) -> dict:
    """Bind a smoke snapshot to the one-shot frozen launcher receipt."""

    spec = SMOKE_SPECS[smoke_name]
    paths = _smoke_launch_paths(campaign_dir, smoke_name)
    if paths["failure"].exists():
        raise ValueError(f"{smoke_name} has preserved launch-failure evidence")
    for name in ("receipt", "receipt_hash", "launcher_log", "completion"):
        path = paths[name]
        if path.is_symlink() or not path.is_file():
            raise ValueError(
                f"{smoke_name} create-only launch {name} is missing/invalid"
            )
    try:
        hash_record = _read_json(paths["receipt_hash"])
        receipt = _read_json(paths["receipt"])
        completion = _read_json(paths["completion"])
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"{smoke_name} launch evidence is unreadable: {exc}"
        ) from exc
    if hash_record != {
        "path": paths["receipt"].name,
        "sha256": _sha_file(paths["receipt"]),
    }:
        raise ValueError(f"{smoke_name} launch receipt hash drifted")
    run_name = _safe_id(
        f"{manifest['campaign_id']}_excluded_smoke_{smoke_name}"
    )
    try:
        results_root = Path(receipt["results_root"]).resolve()
        experiment_dir = Path(receipt["experiment_dir"]).resolve()
        original_run_dir = Path(receipt["run_dir"]).resolve()
        port = receipt["port"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            f"{smoke_name} launch paths/port are malformed"
        ) from exc
    result_name = (
        f"caveat_shop__{spec['scaffold']}__{spec['model_recorded']}__"
        f"{spec['scenario']}-{spec['variant']}__{spec['condition']}"
    )
    if (
        experiment_dir != results_root / run_name
        or original_run_dir != experiment_dir / result_name
    ):
        raise ValueError(f"{smoke_name} launch output identity drifted")
    try:
        _validate_smoke_port(
            campaign_dir,
            manifest,
            smoke_name,
            port,
            allow_own_reservation=True,
            require_free=False,
        )
    except SystemExit as exc:
        raise ValueError(
            f"{smoke_name} launch port reservation is invalid: {exc}"
        ) from exc
    regions = _scheduled_probe_regions(
        manifest, spec["logical_model"]
    )
    override = json.dumps(
        {spec["logical_model"]: regions}, separators=(",", ":")
    )
    expected_contract = {
        "manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "source_inventory_sha256": manifest[
            "source_inventory_sha256"
        ],
        "runtime_dependencies_sha256": manifest[
            "runtime_dependencies"
        ]["sha256"],
        "runtime_environment_policy_sha256": manifest[
            "runtime_environment_policy"
        ]["sha256"],
        "evaluation_input_attestation": manifest[
            "certification"
        ]["frozen_sha256"],
        "trapi_regions_override": override,
        "max_steps": 12_000,
        "jobs": 1,
        "repeats": 1,
    }
    expected_command = [
        sys.executable,
        "-m",
        "caveat.benchmark.run",
        "--name",
        run_name,
        "--scenarios",
        spec["scenario"],
        "--conditions",
        spec["condition"],
        "--variants",
        spec["variant"],
        "--scaffolds",
        spec["scaffold"],
        "--models",
        spec["model_request"],
        "--max-steps",
        "12000",
        "--repeats",
        "1",
        "--jobs",
        "1",
        "--results",
        str(results_root),
        "--base-port",
        str(port),
    ]
    if (
        receipt.get("schema_version") != 1
        or receipt.get("kind") != "excluded_smoke_launch_receipt"
        or receipt.get("campaign_id") != manifest["campaign_id"]
        or receipt.get("manifest_sha256")
        != expected_contract["manifest_sha256"]
        or receipt.get("smoke") != smoke_name
        or receipt.get("spec") != spec
        or receipt.get("run_name") != run_name
        or receipt.get("region_order") != regions
        or receipt.get("command") != expected_command
        or receipt.get("runtime_contract") != expected_contract
        or receipt.get("confirmatory_eligible") is not False
        or receipt.get("relaunch_into_same_path_prohibited") is not True
    ):
        raise ValueError(f"{smoke_name} launch receipt contract drifted")
    _parse_utc(
        receipt.get("launched_at_utc"),
        f"{smoke_name} launched_at_utc",
    )
    evidence_dir = evidence_dir.resolve()
    if require_original_path and evidence_dir != original_run_dir:
        raise ValueError(
            f"{smoke_name} publication source differs from launch receipt"
        )
    inventory = _tree_inventory(evidence_dir)
    validation = _validate_smoke_run(evidence_dir, spec, manifest)
    if (
        completion.get("schema_version") != 1
        or completion.get("kind")
        != "excluded_smoke_launch_completion"
        or completion.get("campaign_id") != manifest["campaign_id"]
        or completion.get("smoke") != smoke_name
        or completion.get("returncode") != 0
        or completion.get("launch_receipt")
        != _campaign_file_ref(campaign_dir, paths["receipt"])
        or completion.get("launch_receipt_hash")
        != _campaign_file_ref(campaign_dir, paths["receipt_hash"])
        or completion.get("launcher_log")
        != _campaign_file_ref(campaign_dir, paths["launcher_log"])
        or completion.get("run_dir") != str(original_run_dir)
        or completion.get("run_files") != inventory
        or completion.get("run_files_sha256")
        != _inventory_sha(inventory)
        or completion.get("validation") != validation
        or completion.get("confirmatory_eligible") is not False
    ):
        raise ValueError(
            f"{smoke_name} launch completion/run evidence drifted"
        )
    _parse_utc(
        completion.get("completed_at_utc"),
        f"{smoke_name} completed_at_utc",
    )
    return {
        "port": port,
        "run_name": run_name,
        "original_run_dir": str(original_run_dir),
        "runtime_contract": expected_contract,
        "receipt": _campaign_file_ref(
            campaign_dir, paths["receipt"]
        ),
        "receipt_hash": _campaign_file_ref(
            campaign_dir, paths["receipt_hash"]
        ),
        "launcher_log": _campaign_file_ref(
            campaign_dir, paths["launcher_log"]
        ),
        "completion": _campaign_file_ref(
            campaign_dir, paths["completion"]
        ),
    }


def publish_smoke_gate(
    campaign_dir: Path,
    smoke_dirs: dict[str, Path],
) -> None:
    reject_abandoned(campaign_dir, "smoke-gate publication")
    manifest = verify_campaign(campaign_dir, quiet=True)
    if set(smoke_dirs) != set(SMOKE_SPECS):
        raise SystemExit("smoke inputs are not the exact required pair")
    gate_path = campaign_dir / "smoke_gate.json"
    frozen_root = campaign_dir / "smoke_inputs"
    if gate_path.exists() or frozen_root.exists():
        raise SystemExit("smoke gate/input snapshot already exists")
    validations = {}
    launches = {}
    inventories = {}
    resolved_sources = {}
    for name, spec in SMOKE_SPECS.items():
        source = smoke_dirs[name].resolve()
        try:
            validations[name] = _validate_smoke_run(
                source, spec, manifest
            )
            launches[name] = _verify_smoke_launch_evidence(
                campaign_dir,
                manifest,
                name,
                source,
                require_original_path=True,
            )
        except ValueError as exc:
            raise SystemExit(f"{name} smoke failed validation: {exc}") from exc
        resolved_sources[name] = source
    # Validate the complete pair before the first write.  An invalid second
    # smoke must not strand a partial create-only snapshot of the first.
    for name in SMOKE_SPECS:
        source = resolved_sources[name]
        target = frozen_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
        inventory = _tree_inventory(target)
        inventories[name] = {
            "root": str(target.relative_to(campaign_dir)),
            "files": inventory,
            "files_sha256": _inventory_sha(inventory),
        }
    record = {
        "schema_version": 1,
        "kind": "caveat_harness_smoke_gate",
        "verdict": "pass",
        "published_at_utc": _utcnow(),
        "campaign_manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "source_inventory_sha256": manifest[
            "source_inventory_sha256"
        ],
        "runtime_dependencies_sha256": manifest[
            "runtime_dependencies"
        ]["sha256"],
        "policy": manifest["smoke_policy"],
        "validations": validations,
        "launches": launches,
        "snapshots": inventories,
    }
    _write_new(gate_path, record)
    verify_smoke_gate(campaign_dir, manifest)
    print("SMOKE GATE PASS: excluded weak-easy and sol-high-hard smokes")


def print_smoke_environment(campaign_dir: Path, smoke_name: str) -> None:
    """Emit the exact non-secret environment delta a smoke must inherit."""
    manifest = verify_campaign(campaign_dir, quiet=True)
    if smoke_name not in SMOKE_SPECS:
        raise SystemExit(f"unknown smoke name: {smoke_name}")
    logical = SMOKE_SPECS[smoke_name]["logical_model"]
    regions = _scheduled_probe_regions(manifest, logical)
    payload = {
        "smoke": smoke_name,
        "logical_model": logical,
        "unset_prefixes": manifest["runtime_environment_policy"][
            "sanitize_prefixes"
        ],
        "unset_exact": manifest["runtime_environment_policy"][
            "sanitize_exact"
        ],
        "set": {
            **manifest["runtime_environment_policy"]["set"],
            "TRAPI_REGIONS_OVERRIDE": json.dumps(
                {logical: regions}, separators=(",", ":")
            ),
        },
        "required_diagnostic_attestations": {
            "runtime_source_attestation": manifest[
                "source_inventory_sha256"
            ],
            "evaluation_input_attestation": manifest[
                "certification"
            ]["frozen_sha256"],
        },
    }
    if "STOREFRONT_OPS_TOKEN" in payload["set"]:
        raise SystemExit("smoke environment unexpectedly contains ops token")
    print(json.dumps(payload, indent=2, sort_keys=True))


def _smoke_environment(
    manifest: dict,
    spec: dict,
) -> tuple[dict, list[str], str]:
    """Apply and audit the exact frozen, non-ops smoke environment."""

    logical = spec["logical_model"]
    regions = _scheduled_probe_regions(manifest, logical)
    override = json.dumps(
        {logical: regions}, separators=(",", ":")
    )
    env = _apply_environment_policy(manifest)
    env["TRAPI_REGIONS_OVERRIDE"] = override
    policy = manifest["runtime_environment_policy"]
    expected_set = {
        key: str(value) for key, value in policy["set"].items()
    }
    if {key: env.get(key) for key in expected_set} != expected_set:
        raise SystemExit("smoke environment differs from frozen policy")
    allowed = set(expected_set) | {"TRAPI_REGIONS_OVERRIDE"}
    leaked = sorted({
        key
        for key in env
        if key not in allowed
        and (
            key in policy["sanitize_exact"]
            or any(
                key.startswith(prefix)
                for prefix in policy["sanitize_prefixes"]
            )
        )
    })
    required_absent = set(policy.get("required_absent_after_apply", ()))
    leaked.extend(sorted(
        key for key in required_absent
        if key in env and key not in allowed
    ))
    if leaked:
        raise SystemExit(
            "smoke environment retained sanitized variables: "
            + ", ".join(sorted(set(leaked)))
        )
    if (
        "STOREFRONT_OPS_TOKEN" in env
        or "STOREFRONT_OPS_TOKEN" in expected_set
    ):
        raise SystemExit(
            "STOREFRONT_OPS_TOKEN survived smoke launch sanitization"
        )
    if env.get("TRAPI_REGIONS_OVERRIDE") != override:
        raise SystemExit("smoke TRAPI route order differs from frozen policy")
    return env, regions, override


def _smoke_launch_paths(
    campaign_dir: Path,
    smoke_name: str,
) -> dict[str, Path]:
    root = campaign_dir.resolve() / "excluded_smoke_launches"
    return {
        "root": root,
        "receipt": root / f"{smoke_name}.json",
        "receipt_hash": root / f"{smoke_name}.sha256.json",
        "launcher_log": root / f"{smoke_name}.log",
        "completion": root / f"{smoke_name}.completion.json",
        "failure": root / f"{smoke_name}.failure.json",
    }


def _validate_smoke_port(
    campaign_dir: Path,
    manifest: dict,
    smoke_name: str,
    port: int,
    *,
    allow_own_reservation: bool,
    require_free: bool = True,
) -> None:
    if type(port) is not int or not 0 < port <= 65535:
        raise SystemExit("smoke port must be an integer in 1..65535")
    if PROTECTED_PORT_LOW <= port <= PROTECTED_PORT_HIGH:
        raise SystemExit(
            f"smoke port intersects protected {PROTECTED_PORT_LOW}-"
            f"{PROTECTED_PORT_HIGH} refill lanes"
        )
    campaign_base = int(manifest["base_port"])
    if campaign_base <= port <= campaign_base + 99:
        raise SystemExit(
            "smoke port intersects the campaign's reserved 100-port band"
        )
    reservation = (
        campaign_dir.resolve()
        / "excluded_smoke_launches"
        / f"port_{port}.reservation.json"
    )
    if reservation.exists():
        if not allow_own_reservation:
            raise SystemExit(f"smoke port is already reserved: {port}")
        try:
            record = _read_json(reservation)
        except Exception as exc:  # noqa: BLE001
            raise SystemExit(
                f"smoke port reservation is unreadable: {reservation}"
            ) from exc
        if (
            record.get("kind") != "excluded_smoke_port_reservation"
            or record.get("smoke") != smoke_name
            or record.get("port") != port
        ):
            raise SystemExit(f"smoke port is reserved by another run: {port}")
    if require_free and port in listening_ports():
        raise SystemExit(f"smoke port has an active listener: {port}")


def _write_smoke_launch_failure(
    path: Path,
    *,
    smoke_name: str,
    code: str,
    message: str,
    exception_type: str | None = None,
) -> None:
    _write_new(path, {
        "schema_version": 1,
        "kind": "excluded_smoke_launch_failure",
        "smoke": smoke_name,
        "code": code,
        "failed_at_utc": _utcnow(),
        "exception_type": exception_type,
        "message": message[:2_000],
        "confirmatory_eligible": False,
        "relaunch_into_same_path_prohibited": True,
    })


def launch_smoke(
    campaign_dir: Path,
    smoke_name: str,
    results_root: Path,
    port: int,
) -> Path:
    """Launch one excluded smoke once, preserving every partial attempt."""

    reject_abandoned(campaign_dir, "excluded smoke launch")
    manifest = verify_campaign(campaign_dir, quiet=True)
    if smoke_name not in SMOKE_SPECS:
        raise SystemExit(f"unknown smoke name: {smoke_name}")
    if manifest.get("smoke_policy") != SMOKE_POLICY:
        raise SystemExit("frozen smoke policy is missing or drifted")
    if (
        manifest.get("caps", {}).get("max_steps") != 12_000
        or CAPS["max_steps"] != 12_000
    ):
        raise SystemExit("smoke launch requires the frozen 12000-step cap")
    campaign_dir = campaign_dir.resolve()
    results_root = results_root.resolve()
    if (
        results_root == campaign_dir
        or campaign_dir in results_root.parents
    ):
        raise SystemExit(
            "excluded smoke results root must be outside the campaign"
        )
    if (
        (campaign_dir / "smoke_gate.json").exists()
        or (campaign_dir / "smoke_inputs").exists()
    ):
        raise SystemExit("smoke gate/input snapshot already exists")
    spec = SMOKE_SPECS[smoke_name]
    run_name = _safe_id(
        f"{manifest['campaign_id']}_excluded_smoke_{smoke_name}"
    )
    experiment_dir = results_root / run_name
    result_name = (
        f"caveat_shop__{spec['scaffold']}__{spec['model_recorded']}__"
        f"{spec['scenario']}-{spec['variant']}__{spec['condition']}"
    )
    run_dir = experiment_dir / result_name
    paths = _smoke_launch_paths(campaign_dir, smoke_name)
    existing = [
        path for name, path in paths.items()
        if name != "root" and path.exists()
    ]
    if experiment_dir.exists() or existing:
        material = [experiment_dir] if experiment_dir.exists() else []
        material.extend(existing)
        raise SystemExit(
            "refusing to replace existing smoke evidence: "
            + ", ".join(str(path) for path in material)
        )
    _validate_smoke_port(
        campaign_dir,
        manifest,
        smoke_name,
        port,
        allow_own_reservation=False,
    )
    env, regions, override = _smoke_environment(manifest, spec)
    command = [
        sys.executable,
        "-m",
        "caveat.benchmark.run",
        "--name",
        run_name,
        "--scenarios",
        spec["scenario"],
        "--conditions",
        spec["condition"],
        "--variants",
        spec["variant"],
        "--scaffolds",
        spec["scaffold"],
        "--models",
        spec["model_request"],
        "--max-steps",
        "12000",
        "--repeats",
        "1",
        "--jobs",
        "1",
        "--results",
        str(results_root),
        "--base-port",
        str(port),
    ]
    results_root.mkdir(parents=True, exist_ok=True)
    try:
        experiment_dir.mkdir()
    except FileExistsError as exc:
        raise SystemExit(
            f"smoke experiment was concurrently reserved: {experiment_dir}"
        ) from exc
    paths["root"].mkdir(parents=True, exist_ok=True)
    port_reservation = (
        paths["root"] / f"port_{port}.reservation.json"
    )
    _write_new(port_reservation, {
        "schema_version": 1,
        "kind": "excluded_smoke_port_reservation",
        "campaign_id": manifest["campaign_id"],
        "smoke": smoke_name,
        "port": port,
        "experiment_dir": str(experiment_dir),
        "reserved_at_utc": _utcnow(),
        "reuse_prohibited": True,
    })
    runtime_contract = {
        "manifest_sha256": _sha_file(
            campaign_dir / "campaign_manifest.json"
        ),
        "source_inventory_sha256": manifest[
            "source_inventory_sha256"
        ],
        "runtime_dependencies_sha256": manifest[
            "runtime_dependencies"
        ]["sha256"],
        "runtime_environment_policy_sha256": manifest[
            "runtime_environment_policy"
        ]["sha256"],
        "evaluation_input_attestation": manifest[
            "certification"
        ]["frozen_sha256"],
        "trapi_regions_override": override,
        "max_steps": 12_000,
        "jobs": 1,
        "repeats": 1,
    }
    receipt = {
        "schema_version": 1,
        "kind": "excluded_smoke_launch_receipt",
        "campaign_id": manifest["campaign_id"],
        "manifest_sha256": runtime_contract["manifest_sha256"],
        "smoke": smoke_name,
        "spec": spec,
        "run_name": run_name,
        "results_root": str(results_root),
        "experiment_dir": str(experiment_dir),
        "run_dir": str(run_dir),
        "port": port,
        "region_order": regions,
        "launched_at_utc": _utcnow(),
        "command": command,
        "runtime_contract": runtime_contract,
        "confirmatory_eligible": False,
        "relaunch_into_same_path_prohibited": True,
    }
    _write_new(paths["receipt"], receipt)
    _write_new(paths["receipt_hash"], {
        "path": paths["receipt"].name,
        "sha256": _sha_file(paths["receipt"]),
    })
    try:
        _validate_smoke_port(
            campaign_dir,
            manifest,
            smoke_name,
            port,
            allow_own_reservation=True,
        )
    except SystemExit as exc:
        _write_smoke_launch_failure(
            paths["failure"],
            smoke_name=smoke_name,
            code="SMOKE_PORT_RECHECK_FAILED",
            message=str(exc),
        )
        raise
    try:
        stream = paths["launcher_log"].open("x")
    except Exception as exc:  # noqa: BLE001
        _write_smoke_launch_failure(
            paths["failure"],
            smoke_name=smoke_name,
            code="SMOKE_LAUNCHER_LOG_CREATE_FAILED",
            message=str(exc),
            exception_type=type(exc).__name__,
        )
        raise SystemExit(
            f"smoke launcher log creation failed; evidence preserved: {exc}"
        ) from exc
    interrupted = False

    def _defer_interrupt(signum, _frame):
        nonlocal interrupted
        interrupted = True
        print(
            f"signal {signum} received; waiting for the active smoke "
            "without terminating it",
            file=sys.stderr,
            flush=True,
        )

    old_int = signal.signal(signal.SIGINT, _defer_interrupt)
    old_term = signal.signal(signal.SIGTERM, _defer_interrupt)
    process = None
    try:
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
        except Exception as exc:  # noqa: BLE001
            stream.write(
                "CAVEAT_SMOKE_PROCESS_SPAWN_FAILED "
                f"exception_type={type(exc).__name__} "
                f"message={str(exc)[:1000]}\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
            _write_smoke_launch_failure(
                paths["failure"],
                smoke_name=smoke_name,
                code="CAVEAT_SMOKE_PROCESS_SPAWN_FAILED",
                message=str(exc),
                exception_type=type(exc).__name__,
            )
            raise SystemExit(
                f"smoke process spawn failed; evidence preserved: {exc}"
            ) from exc
        returncode = process.wait()
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
        if process is not None and process.poll() is None:
            process.wait()
        stream.close()
    if returncode:
        _write_smoke_launch_failure(
            paths["failure"],
            smoke_name=smoke_name,
            code="CAVEAT_SMOKE_PROCESS_NONZERO",
            message=f"benchmark runner returned {returncode}",
        )
        raise SystemExit(
            "smoke process failed; partial evidence was preserved: "
            f"returncode={returncode}"
        )
    try:
        validation = _validate_smoke_run(run_dir, spec, manifest)
        inventory = _tree_inventory(run_dir)
    except Exception as exc:  # noqa: BLE001
        _write_smoke_launch_failure(
            paths["failure"],
            smoke_name=smoke_name,
            code="CAVEAT_SMOKE_VALIDATION_FAILED",
            message=str(exc),
            exception_type=type(exc).__name__,
        )
        raise SystemExit(
            f"smoke validation failed; evidence preserved: {exc}"
        ) from exc
    _write_new(paths["completion"], {
        "schema_version": 1,
        "kind": "excluded_smoke_launch_completion",
        "campaign_id": manifest["campaign_id"],
        "smoke": smoke_name,
        "completed_at_utc": _utcnow(),
        "returncode": 0,
        "launch_receipt": _campaign_file_ref(
            campaign_dir, paths["receipt"]
        ),
        "launch_receipt_hash": _campaign_file_ref(
            campaign_dir, paths["receipt_hash"]
        ),
        "launcher_log": _campaign_file_ref(
            campaign_dir, paths["launcher_log"]
        ),
        "run_dir": str(run_dir),
        "run_files": inventory,
        "run_files_sha256": _inventory_sha(inventory),
        "validation": validation,
        "confirmatory_eligible": False,
    })
    if interrupted:
        raise SystemExit(
            "operator interrupt honored after the smoke completed; "
            "the completed evidence was preserved"
        )
    print(f"SMOKE LAUNCH PASS: {smoke_name} -> {run_dir}")
    return run_dir


def verify_smoke_gate(
    campaign_dir: Path,
    manifest: dict | None = None,
) -> dict:
    manifest = manifest or verify_campaign(campaign_dir, quiet=True)
    gate_path = campaign_dir / "smoke_gate.json"
    if not gate_path.is_file():
        raise ValueError(
            "passed smoke gate is missing; publish the two excluded smokes "
            "before confirmatory launch"
        )
    try:
        record = _read_json(gate_path)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"smoke gate is unreadable: {exc}") from exc
    expected_top = {
        "schema_version", "kind", "verdict", "published_at_utc",
        "campaign_manifest_sha256", "source_inventory_sha256",
        "runtime_dependencies_sha256", "policy", "validations",
        "launches", "snapshots",
    }
    if set(record) != expected_top:
        raise ValueError("smoke gate schema is not exact")
    if (
        record["schema_version"] != 1
        or record["kind"] != "caveat_harness_smoke_gate"
        or record["verdict"] != "pass"
        or record["campaign_manifest_sha256"] != _sha_file(
            campaign_dir / "campaign_manifest.json"
        )
        or record["source_inventory_sha256"]
        != manifest["source_inventory_sha256"]
        or record["runtime_dependencies_sha256"]
        != manifest["runtime_dependencies"]["sha256"]
        or record["policy"] != manifest["smoke_policy"]
        or set(record["snapshots"]) != set(SMOKE_SPECS)
        or set(record["validations"]) != set(SMOKE_SPECS)
        or set(record["launches"]) != set(SMOKE_SPECS)
    ):
        raise ValueError("smoke gate is not bound to the frozen campaign")
    for name, spec in SMOKE_SPECS.items():
        snapshot = record["snapshots"][name]
        root = _campaign_child(
            campaign_dir, snapshot.get("root"), f"{name} smoke"
        )
        if not root.is_dir():
            raise ValueError(f"{name} frozen smoke is missing")
        inventory = _tree_inventory(root)
        if (
            inventory != snapshot.get("files")
            or _inventory_sha(inventory) != snapshot.get("files_sha256")
        ):
            raise ValueError(f"{name} frozen smoke snapshot drifted")
        validation = _validate_smoke_run(root, spec, manifest)
        if validation != record["validations"][name]:
            raise ValueError(f"{name} smoke validation record drifted")
        launch = _verify_smoke_launch_evidence(
            campaign_dir,
            manifest,
            name,
            root,
            require_original_path=False,
        )
        if launch != record["launches"][name]:
            raise ValueError(f"{name} smoke launch evidence drifted")
    return {
        "path": gate_path,
        "sha256": _sha_file(gate_path),
        "record": record,
    }


def pending_count(campaign_dir: Path, blocks: set[int]) -> int:
    reject_abandoned(campaign_dir, "pending/confirmatory inspection")
    manifest = verify_campaign(campaign_dir, quiet=True)
    return sum(
        not (campaign_dir / row["summary_relpath"]).is_file()
        for row in manifest["schedule"]
        if row["block"] in blocks
    )


def next_report_paths(campaign_dir: Path) -> dict[str, str]:
    """Select the next create-only, campaign-local report snapshot paths."""

    campaign_dir = campaign_dir.resolve()
    report_root = campaign_dir / "reports"
    for index in range(1, 1_000_000):
        stem = f"report_{index:04d}"
        json_path = report_root / f"{stem}.json"
        markdown_path = report_root / f"{stem}.md"
        hash_path = json_path.with_name(f"{json_path.name}.sha256.json")
        if not any(
            path.exists()
            for path in (json_path, markdown_path, hash_path)
        ):
            return {
                "json": str(json_path),
                "markdown": str(markdown_path),
                "hash": str(hash_path),
            }
    raise SystemExit("no free create-only numbered report path remains")


def launch_block(
    campaign_dir: Path,
    block: int,
    checkpoint_label: str,
) -> None:
    reject_abandoned(campaign_dir, "confirmatory launch")
    manifest = verify_campaign(campaign_dir, quiet=True)
    try:
        verify_smoke_gate(campaign_dir, manifest)
    except ValueError as exc:
        raise SystemExit(f"confirmatory launch blocked: {exc}") from exc
    rows = _schedule_rows(manifest, block=block)
    if len(rows) != 10:
        raise SystemExit(f"block {block} is not an exact ten-run block")
    validate_port_band(int(manifest["base_port"]), require_free=True)
    pending: list[dict] = []
    for row in rows:
        summary = campaign_dir / row["summary_relpath"]
        experiment = campaign_dir / row["experiment_relpath"]
        receipt = (
            campaign_dir / "launch_receipts" / f"{row['run_id']}.json"
        )
        receipt_sha = (
            campaign_dir / "launch_receipts"
            / f"{row['run_id']}.sha256.json"
        )
        launcher = campaign_dir / row["launcher_log_relpath"]
        launch_failure = (
            campaign_dir / "launch_failures" / f"{row['run_id']}.json"
        )
        if summary.is_file():
            print(f"already complete, preserving: {row['run_id']}")
            continue
        if (
            experiment.exists()
            or receipt.exists()
            or receipt_sha.exists()
            or launcher.exists()
            or launch_failure.exists()
        ):
            raise SystemExit(
                f"partial evidence exists for {row['run_id']}; run report "
                "and archive-refills before relaunching"
            )
        pending.append(row)
    if not pending:
        print(f"block {block}: already complete")
        return
    for row in pending:
        try:
            verify_probe_checkpoint(
                campaign_dir,
                manifest,
                checkpoint_label,
                expected_stage=row["probe_stage"],
                expected_block=row["block"],
                expected_logical_model=row["logical_model"],
                freshness_at_utc=_utcnow(),
            )
        except ValueError as exc:
            raise SystemExit(
                f"checkpoint {checkpoint_label} cannot launch block "
                f"{block}: {exc}"
            ) from exc
    # The probes can take minutes. Re-check immediately before the first
    # browser spawn so a refill that began after the probe cannot be contended.
    audit_refill_coexistence(int(manifest["base_port"]))
    validate_port_band(int(manifest["base_port"]), require_free=True)
    interrupted = False

    def _defer_interrupt(signum, _frame):
        nonlocal interrupted
        interrupted = True
        print(
            f"signal {signum} received; waiting for active runs without "
            "terminating them",
            file=sys.stderr,
            flush=True,
        )

    old_int = signal.signal(signal.SIGINT, _defer_interrupt)
    old_term = signal.signal(signal.SIGTERM, _defer_interrupt)
    processes: list[tuple[dict, subprocess.Popen, object]] = []
    pending_ids = {row["run_id"] for row in pending}
    pending_pairs: list[list[dict]] = []
    for index in range(0, len(rows), 2):
        pair = rows[index:index + 2]
        if (
            len(pair) != 2
            or {row["arm"] for row in pair}
            != {"baseline", "caveat_harness"}
            or any(
                row[key] != pair[0][key]
                for row in pair[1:]
                for key in (
                    "block", "cohort", "repeat", "scenario", "condition",
                    "variant", "model_request", "logical_model",
                    "primary_region", "region_order",
                )
            )
        ):
            raise SystemExit(
                f"block {block} schedule is not adjacent matched A/B pairs"
            )
        pending_pair = [
            row for row in pair if row["run_id"] in pending_ids
        ]
        if pending_pair:
            pending_pairs.append(pending_pair)
    failed = []
    launch_error = None

    def spawn_row(row: dict) -> tuple[dict, subprocess.Popen, object] | None:
        nonlocal launch_error
        env = _apply_environment_policy(manifest, row)
        _write_receipt(
            campaign_dir, manifest, row, checkpoint_label, env
        )
        launcher = campaign_dir / row["launcher_log_relpath"]
        launcher.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "caveat.benchmark.run",
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
            str(campaign_dir / "runs"),
            "--base-port",
            str(row["port"]),
        ]
        try:
            stream = launcher.open("x")
        except Exception as exc:  # noqa: BLE001
            marker = (
                campaign_dir
                / "launch_failures"
                / f"{row['run_id']}.json"
            )
            _write_new(marker, {
                "schema_version": 1,
                "kind": "confirmatory_launch_infrastructure_failure",
                "run_id": row["run_id"],
                "code": "CAVEAT_LAUNCHER_LOG_CREATE_FAILED",
                "failed_at_utc": _utcnow(),
                "exception_type": type(exc).__name__,
                "message": str(exc)[:1_000],
            })
            launch_error = f"{row['run_id']}: {type(exc).__name__}: {exc}"
            return None
        print(
            f"launch {row['run_id']} arm={row['arm']} "
            f"primary={row['primary_region']} port={row['port']}",
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
        except Exception as exc:  # noqa: BLE001
            stream.write(
                "CAVEAT_RUN_PROCESS_SPAWN_FAILED "
                f"exception_type={type(exc).__name__} "
                f"message={str(exc)[:1000]}\n"
            )
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            marker = (
                campaign_dir
                / "launch_failures"
                / f"{row['run_id']}.json"
            )
            _write_new(marker, {
                "schema_version": 1,
                "kind": "confirmatory_launch_infrastructure_failure",
                "run_id": row["run_id"],
                "code": "CAVEAT_RUN_PROCESS_SPAWN_FAILED",
                "failed_at_utc": _utcnow(),
                "exception_type": type(exc).__name__,
                "message": str(exc)[:1_000],
            })
            launch_error = f"{row['run_id']}: {type(exc).__name__}: {exc}"
            return None
        record = (row, process, stream)
        processes.append(record)
        return record

    try:
        active_pairs: list[
            list[tuple[dict, subprocess.Popen, object]]
        ] = []
        pair_index = 0
        while pair_index < len(pending_pairs) or active_pairs:
            active_runs = sum(len(pair) for pair in active_pairs)
            while (
                not interrupted
                and launch_error is None
                and pair_index < len(pending_pairs)
                and active_runs + len(pending_pairs[pair_index])
                <= CAMPAIGN_MAX_PARALLEL_RUNS
            ):
                pair_records = []
                for row_index, row in enumerate(pending_pairs[pair_index]):
                    record = spawn_row(row)
                    if record is None:
                        break
                    pair_records.append(record)
                    active_runs += 1
                    if (
                        row_index + 1
                        < len(pending_pairs[pair_index])
                    ):
                        time.sleep(CAPS["spawn_stagger_seconds"])
                if pair_records:
                    active_pairs.append(pair_records)
                pair_index += 1
                if launch_error is not None:
                    break
                if (
                    pair_index < len(pending_pairs)
                    and active_runs < CAMPAIGN_MAX_PARALLEL_RUNS
                ):
                    time.sleep(CAPS["spawn_stagger_seconds"])

            if not active_pairs:
                break

            completed_pair = next(
                (
                    pair for pair in active_pairs
                    if all(
                        process.poll() is not None
                        for _, process, _ in pair
                    )
                ),
                None,
            )
            if completed_pair is None:
                time.sleep(1)
                continue
            for row, process, stream in completed_pair:
                returncode = process.wait()
                stream.close()
                if returncode:
                    failed.append((row["run_id"], returncode))
            active_pairs.remove(completed_pair)
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
        for _, process, stream in processes:
            if process.poll() is None:
                process.wait()
            if not stream.closed:
                stream.close()
    launched_ids = {row["run_id"] for row, _, _ in processes}
    unlaunched = [
        row["run_id"] for row in pending
        if row["run_id"] not in launched_ids
    ]
    missing = [
        row["run_id"] for row, _, _ in processes
        if not (campaign_dir / row["summary_relpath"]).is_file()
    ]
    if interrupted:
        raise SystemExit(
            "operator interrupt honored after all active runs completed; "
            "no process was killed"
        )
    if failed or missing or unlaunched or launch_error:
        raise SystemExit(
            "block completed with preserved failures; no relaunch occurred: "
            f"process_failures={failed}, missing_summaries={missing}, "
            f"unlaunched={unlaunched}, launch_error={launch_error}"
        )
    print(f"BLOCK PASS: {block} ({len(pending)} fresh runs)")


def archive_refills(campaign_dir: Path, report_path: Path) -> None:
    reject_abandoned(campaign_dir, "refill archival/relaunch")
    manifest = verify_campaign(campaign_dir, quiet=True)
    report_path = report_path.resolve()
    if campaign_dir.resolve() not in report_path.parents:
        raise SystemExit("report path must be inside the campaign directory")
    hash_path = report_path.with_name(f"{report_path.name}.sha256.json")
    if not report_path.is_file() or not hash_path.is_file():
        raise SystemExit(
            "report and its create-only hash sidecar are both required"
        )
    try:
        report = _read_json(report_path)
        hash_record = _read_json(hash_path)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"report/hash sidecar is unreadable: {exc}") from exc
    manifest_sha256 = _sha_file(
        campaign_dir / "campaign_manifest.json"
    )
    expected_hash_record = {
        "schema_version": 1,
        "kind": "caveat_harness_ab_report_hash",
        "path": report_path.name,
        "sha256": _sha_file(report_path),
        "campaign_id": manifest["campaign_id"],
        "manifest_sha256": manifest_sha256,
    }
    if hash_record != expected_hash_record:
        raise SystemExit("report hash sidecar/campaign binding is invalid")
    if (
        report.get("schema_version") != 2
        or report.get("kind") != "caveat_harness_ab_report"
        or report.get("campaign_id") != manifest["campaign_id"]
        or report.get("manifest_sha256") != manifest_sha256
    ):
        raise SystemExit("report kind/schema/campaign binding is invalid")
    # A self-consistent but stale report is not authority to redraw a run.
    # Reclassify current evidence without mutating optimal-selection values and require the
    # archived decision and all run records to be identical.
    from report_caveat_harness_eval import build_report
    current = build_report(campaign_dir, evaluate=False)
    for field in ("refill_run_ids", "excluded_runs"):
        if (
            report.get("validity", {}).get(field)
            != current.get("validity", {}).get(field)
        ):
            raise SystemExit(
                f"report {field} is stale or differs from current evidence"
            )
    if report.get("runs") != current.get("runs"):
        raise SystemExit("report run evidence is stale or tampered")
    run_ids = report.get("validity", {}).get("refill_run_ids") or []
    if not run_ids:
        raise SystemExit("report contains no refillable run ids")
    by_id = {row["run_id"]: row for row in manifest["schedule"]}
    if (
        any(not isinstance(run_id, str) for run_id in run_ids)
        or len(run_ids) != len(set(run_ids))
        or not set(run_ids).issubset(by_id)
    ):
        raise SystemExit("report names a run outside the frozen schedule")
    plans = []
    for run_id in run_ids:
        row = by_id[run_id]
        attempt = _attempt_number(campaign_dir, run_id)
        target = (
            campaign_dir / "excluded_attempts" / run_id
            / f"attempt_{attempt}"
        )
        sources = (
            (
                campaign_dir / row["experiment_relpath"],
                target / "experiment",
            ),
            (
                campaign_dir / row["launcher_log_relpath"],
                target / "launcher.log",
            ),
            (
                campaign_dir / "launch_receipts" / f"{run_id}.json",
                target / "receipt.json",
            ),
            (
                campaign_dir / "launch_receipts"
                / f"{run_id}.sha256.json",
                target / "receipt.sha256.json",
            ),
            (
                campaign_dir / "launch_failures" / f"{run_id}.json",
                target / "launch_failure.json",
            ),
        )
        existing_sources = [
            (source, destination)
            for source, destination in sources if source.exists()
        ]
        if not existing_sources:
            raise SystemExit(
                f"no attempt evidence exists to archive: {run_id}"
            )
        if target.exists() or any(
            destination.exists()
            for _, destination in existing_sources
        ):
            raise SystemExit(
                f"archive target already exists for {run_id}: {target}"
            )
        plans.append((run_id, attempt, target, existing_sources))
    report_sha256 = _sha_file(report_path)
    hash_sidecar_sha256 = _sha_file(hash_path)
    for run_id, attempt, target, sources in plans:
        target.mkdir(parents=True)
        moved = []
        for source, destination in sources:
            shutil.move(str(source), str(destination))
            moved.append(destination.name)
        _write_new(
            target / "archive_record.json",
            {
                "run_id": run_id,
                "attempt": attempt,
                "archived_at_utc": _utcnow(),
                "source_report": {
                    "path": str(report_path.relative_to(campaign_dir)),
                    "sha256": report_sha256,
                    "hash_sidecar_path": str(
                        hash_path.relative_to(campaign_dir)
                    ),
                    "hash_sidecar_sha256": hash_sidecar_sha256,
                },
                "moved": moved,
                "reason": (
                    report["validity"].get("excluded_runs", {})
                    .get(run_id, {})
                ),
            },
        )
        print(f"archived {run_id} attempt {attempt}: {', '.join(moved)}")


def print_schedule(
    campaign_dir: Path,
    block: int | None,
    output_format: str,
) -> None:
    marker, _, _ = _abandonment_paths(campaign_dir)
    if marker.is_file():
        try:
            verify_abandonment(campaign_dir)
            manifest = _load_bound_manifest(campaign_dir)
        except ValueError as exc:
            raise SystemExit(
                f"abandoned campaign evidence is invalid: {exc}"
            ) from exc
    else:
        manifest = verify_campaign(campaign_dir, quiet=True)
    rows = _schedule_rows(manifest, block=block)
    if output_format == "json":
        print(json.dumps(rows, indent=2, sort_keys=True))
        return
    fields = (
        "run_id", "block", "cohort", "repeat", "scenario", "condition",
        "arm", "scaffold", "model_request", "port", "primary_region",
        "summary_relpath",
    )
    for row in rows:
        print("\t".join(str(row[field]) for field in fields))


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--campaign-dir", required=True, type=Path)
    prepare.add_argument("--campaign-id", required=True)
    prepare.add_argument("--base-port", required=True, type=int)
    prepare.add_argument("--cert-report", required=True, type=Path)
    prepare.add_argument(
        "--lockdiff-report",
        required=True,
        type=Path,
        help=(
            "authoritative lockdiff_after.json; the sibling "
            "lockdiff_before.json is also required and frozen"
        ),
    )
    prepare.add_argument(
        "--weak-regions",
        required=True,
        type=_parse_region_argument,
        help=(
            "comma-separated, ordered live weak model routes selected by a fresh "
            "pre-freeze probe; only gcr/shared, msraif/shared, and "
            "redmond/interactive are allowed"
        ),
    )
    prepare.add_argument(
        "--sol-regions",
        required=True,
        type=_parse_region_argument,
        help=(
            "comma-separated, ordered live sol-high routes selected by a "
            "fresh pre-freeze probe; only gcr/shared, msraif/shared, and "
            "redmond/interactive are allowed"
        ),
    )
    prepare.add_argument(
        "--superseded-campaign-dir",
        type=Path,
        help=(
            "explicit retired or prelaunch-superseded predecessor; its "
            "marker and deterministic attestation are verified and frozen "
            "into successor lineage"
        ),
    )

    verify = subparsers.add_parser("verify")
    verify.add_argument("--campaign-dir", required=True, type=Path)

    schedule = subparsers.add_parser("schedule")
    schedule.add_argument("--campaign-dir", required=True, type=Path)
    schedule.add_argument("--block", type=int, choices=range(1, 7))
    schedule.add_argument("--format", choices=("tsv", "json"), default="tsv")

    select = subparsers.add_parser("select-band")

    pending = subparsers.add_parser("pending")
    pending.add_argument("--campaign-dir", required=True, type=Path)
    pending.add_argument(
        "--block", action="append", required=True, type=int, choices=range(1, 7)
    )

    next_report = subparsers.add_parser("next-report-paths")
    next_report.add_argument("--campaign-dir", required=True, type=Path)

    probe = subparsers.add_parser("probe")
    probe.add_argument("--campaign-dir", required=True, type=Path)
    probe.add_argument(
        "--stage",
        required=True,
        choices=("weak_before", "weak_mid", "sol_before", "sol_mid"),
    )
    probe.add_argument("--label", required=True)

    launch = subparsers.add_parser("launch-block")
    launch.add_argument("--campaign-dir", required=True, type=Path)
    launch.add_argument("--block", required=True, type=int, choices=range(1, 7))
    launch.add_argument("--checkpoint", required=True)

    smoke_launch = subparsers.add_parser("launch-smoke")
    smoke_launch.add_argument(
        "--campaign-dir", required=True, type=Path
    )
    smoke_launch.add_argument(
        "--smoke", required=True, choices=tuple(SMOKE_SPECS)
    )
    smoke_launch.add_argument(
        "--results-root", required=True, type=Path
    )
    smoke_launch.add_argument("--port", required=True, type=int)

    smoke = subparsers.add_parser("publish-smoke-gate")
    smoke.add_argument("--campaign-dir", required=True, type=Path)
    smoke.add_argument("--weak-run-dir", required=True, type=Path)
    smoke.add_argument("--sol-run-dir", required=True, type=Path)

    smoke_env = subparsers.add_parser("smoke-environment")
    smoke_env.add_argument("--campaign-dir", required=True, type=Path)
    smoke_env.add_argument(
        "--smoke", required=True, choices=tuple(SMOKE_SPECS)
    )

    smoke_verify = subparsers.add_parser("verify-smoke-gate")
    smoke_verify.add_argument("--campaign-dir", required=True, type=Path)

    archive = subparsers.add_parser("archive-refills")
    archive.add_argument("--campaign-dir", required=True, type=Path)
    archive.add_argument("--report", required=True, type=Path)

    supersede = subparsers.add_parser(
        "supersede-prelaunch",
        help=(
            "create-only supersession plus deterministic attestation for a "
            "60-run freeze with no scheduled launch artifacts"
        ),
    )
    supersede.add_argument(
        "--campaign-dir", required=True, type=Path
    )
    supersede.add_argument(
        "--failure-evidence",
        type=Path,
        help=(
            "optional campaign-local excluded_smoke_gate_failure JSON; its "
            "content hash and reason are bound into the disposition"
        ),
    )
    supersede.add_argument(
        "--reason",
        help=(
            "required only when --failure-evidence is omitted; if both are "
            "provided, the reasons must match exactly"
        ),
    )

    retire = subparsers.add_parser(
        "retire-campaign",
        help=(
            "create-only post-launch retirement plus a deterministic "
            "all-scheduled-run attestation"
        ),
    )
    retire.add_argument("--campaign-dir", required=True, type=Path)
    retire.add_argument("--reason", required=True)

    attest = subparsers.add_parser(
        "attest-abandonment",
        help=(
            "attest a pre-existing marker (including historical V4, generic "
            "retirement, or interrupted prelaunch supersession publication)"
        ),
    )
    attest.add_argument("--campaign-dir", required=True, type=Path)

    verify_abandoned = subparsers.add_parser("verify-abandonment")
    verify_abandoned.add_argument(
        "--campaign-dir", required=True, type=Path
    )

    args = parser.parse_args()
    if args.command == "prepare":
        prepare_campaign(
            args.campaign_dir,
            args.campaign_id,
            args.base_port,
            args.cert_report,
            args.lockdiff_report,
            args.weak_regions,
            args.sol_regions,
            args.superseded_campaign_dir,
        )
    elif args.command == "verify":
        verify_campaign(args.campaign_dir)
    elif args.command == "schedule":
        print_schedule(args.campaign_dir, args.block, args.format)
    elif args.command == "select-band":
        print(select_free_band())
    elif args.command == "pending":
        print(pending_count(args.campaign_dir, set(args.block)))
    elif args.command == "next-report-paths":
        print(json.dumps(
            next_report_paths(args.campaign_dir),
            sort_keys=True,
        ))
    elif args.command == "probe":
        run_probe(args.campaign_dir, args.stage, args.label)
    elif args.command == "launch-block":
        launch_block(args.campaign_dir, args.block, args.checkpoint)
    elif args.command == "launch-smoke":
        launch_smoke(
            args.campaign_dir,
            args.smoke,
            args.results_root,
            args.port,
        )
    elif args.command == "publish-smoke-gate":
        publish_smoke_gate(args.campaign_dir, {
            "weak_easy": args.weak_run_dir,
            "sol_high_hard": args.sol_run_dir,
        })
    elif args.command == "smoke-environment":
        print_smoke_environment(args.campaign_dir, args.smoke)
    elif args.command == "verify-smoke-gate":
        try:
            verify_smoke_gate(args.campaign_dir)
        except ValueError as exc:
            raise SystemExit(f"SMOKE GATE FAIL: {exc}") from exc
        print("SMOKE GATE VERIFY PASS")
    elif args.command == "archive-refills":
        archive_refills(args.campaign_dir, args.report)
    elif args.command == "supersede-prelaunch":
        record = supersede_prelaunch(
            args.campaign_dir,
            args.failure_evidence,
            args.reason,
        )
        print(
            "PRELAUNCH SUPERSESSION PASS: "
            f"{record['campaign_id']} was never launched/never confirmatory"
        )
    elif args.command == "retire-campaign":
        record = retire_campaign(args.campaign_dir, args.reason)
        print(
            "CAMPAIGN RETIREMENT PASS: "
            f"{record['campaign_id']} is pilot-only/never confirmatory"
        )
    elif args.command == "attest-abandonment":
        record = attest_abandonment(args.campaign_dir)
        print(
            "ABANDONMENT ATTESTATION PASS: "
            f"{record['campaign_id']}"
        )
    elif args.command == "verify-abandonment":
        try:
            record = verify_abandonment(args.campaign_dir)
        except ValueError as exc:
            raise SystemExit(
                f"ABANDONMENT VERIFY FAIL: {exc}"
            ) from exc
        print(
            "ABANDONMENT VERIFY PASS: "
            f"{record['campaign_id']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
