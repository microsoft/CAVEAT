#!/usr/bin/env python
"""Create and verify a fail-closed continuation amendment for clone8.

The original campaign manifest and every existing launch/completion receipt are
immutable.  A continuation is represented by a create-only, hash-linked bundle
which:

* checkpoints the byte identity of every already measured result;
* authorizes only explicitly named interrupted attempts to be superseded;
* binds the old manifest/source/limit/scheduler state to the continuation
  source/limit/scheduler state; and
* leaves the denominator equal to the original manifest row count.

This module deliberately contains no launch loop.  The clone8 controller may
consume a verified :class:`VerifiedAmendment`, but cannot manufacture retry
authority from a classification alone.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import os
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping


AMENDMENT_SCHEMA = "agentarena.clone8-continuation-amendment.v1"
CHECKPOINT_SCHEMA = "agentarena.clone8-continuation-checkpoint.v1"
BASE_SCHEDULER_SCHEMA = "agentarena.clone8-base-scheduler-snapshot.v1"
CONTINUATION_SCHEDULER_SCHEMA = "agentarena.clone8-continuation-scheduler.v1"
AMENDMENT_DIRECTORY = "continuation_001"
SUCCESSOR_AMENDMENT_SCHEMA = "agentarena.clone8-continuation-amendment.v2"
SUCCESSOR_CHECKPOINT_SCHEMA = "agentarena.clone8-continuation-checkpoint.v2"
SUCCESSOR_AMENDMENT_DIRECTORY = "continuation_002"
RECOVERY_AMENDMENT_SCHEMA = "agentarena.clone8-continuation-amendment.v3"
RECOVERY_CHECKPOINT_SCHEMA = "agentarena.clone8-continuation-checkpoint.v3"
RECOVERY_AMENDMENT_DIRECTORY = "continuation_003"
RECOVERY_RUN_INDEX = 368
POSTMORTEM_PROOF_SCHEMA = "agentarena.clone8-postmortem-proof.v1"
VERIFIER_CORRECTION_AMENDMENT_SCHEMA = (
    "agentarena.clone8-verifier-correction-amendment.v1"
)
VERIFIER_CORRECTION_CHECKPOINT_SCHEMA = (
    "agentarena.clone8-verifier-correction-checkpoint.v1"
)
VERIFIER_CORRECTION_PROOF_SCHEMA = (
    "agentarena.clone8-verifier-correction-proof.v1"
)
VERIFIER_CORRECTION_AMENDMENT_DIRECTORY = "verifier_correction_001"
PROTOCOL_RECOVERY_AMENDMENT_SCHEMA = (
    "agentarena.clone8-protocol-recovery-amendment.v1"
)
PROTOCOL_RECOVERY_CHECKPOINT_SCHEMA = (
    "agentarena.clone8-protocol-recovery-checkpoint.v1"
)
LOSSLESS_READ_STATE_PROOF_SCHEMA = (
    "agentarena.clone8-lossless-read-state-proof.v1"
)
PROTOCOL_RECOVERY_AMENDMENT_DIRECTORY = "protocol_recovery_002"
PROTOCOL_RECOVERY_RUN_INDEX = 598
PROTOCOL_RECOVERY_CORRECTION_INDEX = 648
PROTOCOL_RECOVERY_MANIFEST_ROWS = 960
PROTOCOL_RECOVERY_FRONTIER_ROWS = 603
PROTOCOL_RECOVERY_PENDING_ROWS = 357
PROTOCOL_RECOVERY_INHERITED_CORRECTION_INDICES = frozenset({322, 324})
PROTOCOL_RECOVERY_RAW_INVALID_INDICES = frozenset({322, 324, 598, 648})
PROTOCOL_RECOVERY_EFFECTIVE_INVALID_INDICES = frozenset({598, 648})
PROTOCOL_POSTMORTEM_AMENDMENT_SCHEMA = (
    "agentarena.clone8-protocol-postmortem-recovery-amendment.v1"
)
PROTOCOL_POSTMORTEM_CHECKPOINT_SCHEMA = (
    "agentarena.clone8-protocol-postmortem-recovery-checkpoint.v1"
)
PROTOCOL_POSTMORTEM_AMENDMENT_DIRECTORY = "protocol_recovery_003"
PROTOCOL_POSTMORTEM_RUN_INDEX = 642
PROTOCOL_POSTMORTEM_MANIFEST_ROWS = 960
PROTOCOL_POSTMORTEM_FRONTIER_ROWS = 945
PROTOCOL_POSTMORTEM_PENDING_PRIMARY_ROWS = 15
PROTOCOL_POSTMORTEM_CORRECTION_INDICES = frozenset({322, 324, 648})
PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES = frozenset({598})
PROTOCOL_POSTMORTEM_RAW_INVALID_INDICES = frozenset({322, 324, 598, 642, 648})
PROTOCOL_POSTMORTEM_EFFECTIVE_INVALID_INDICES = frozenset({598, 642})
PROTOCOL_POSTMORTEM_UNRESOLVED_INVALID_INDICES = frozenset({642})
PROTOCOL_POSTMORTEM_UNCERTIFIED_SCOPE_PIDS = (
    2109759, 2109760, 2109782, 2109784, 2109791, 2109875, 2109876,
)
PROTOCOL_POSTMORTEM_SCRATCH_TREE_SHA256 = (
    "aab4c3fe55f3444619413ee838bf85e88d880d38079fbf981721ac0c3a1c29ab"
)
PROTOCOL_POSTMORTEM_COMPLETION_SHA256 = (
    "d2d794efb176d35eeb226045faea75b689198b1a818f6c15312ad0a7afeb7c53"
)
PROTOCOL_POSTMORTEM_MISSING_PRIMARY_INDICES = frozenset({
    849, 861, 878, 890, 907, 911, 915, 924, 928, 932, 936, 940, 949, 953, 957,
})
PROTOCOL_POSTMORTEM_LAUNCH_RECEIPT_AGGREGATE_SHA256 = (
    "630e399061f1f5e316d537c45317fbcc21dff6f66c7337f622b8c06662354e59"
)
PROTOCOL_POSTMORTEM_COMPLETION_RECEIPT_AGGREGATE_SHA256 = (
    "7813c3fbca451e282fcb50b0168a8a4b7e21db290fefc049ed08b7586bc8e8ae"
)
PROTOCOL_POSTMORTEM_SCRATCH_RECEIPT_AGGREGATE_SHA256 = (
    "4a131a56afea3bb39bff25b9bc67f621c5454271097376b62e37fdf6f6d6f258"
)
PROTOCOL_POSTMORTEM_RECEIPT_AGGREGATE_ROOT = Path(__file__).resolve().parents[1]
AMENDMENT_ROOT = "continuation_amendments"
AMENDMENT_FILES = frozenset({
    "amendment.json",
    "amendment.sha256",
    "base_checkpoint.json",
    "base_scheduler_contract.json",
    "continuation_source_inventory.json",
    "continuation_limit_contract.json",
    "continuation_scheduler_contract.json",
})
SUCCESSOR_AMENDMENT_FILES = frozenset({
    "amendment.json",
    "amendment.sha256",
    "successor_checkpoint.json",
    "continuation_source_inventory.json",
    "continuation_limit_contract.json",
    "continuation_scheduler_contract.json",
})
RECOVERY_AMENDMENT_FILES = frozenset({
    "amendment.json",
    "amendment.sha256",
    "recovery_checkpoint.json",
    "continuation_source_inventory.json",
    "continuation_limit_contract.json",
    "continuation_scheduler_contract.json",
})
VERIFIER_CORRECTION_AMENDMENT_FILES = frozenset({
    "amendment.json",
    "amendment.sha256",
    "correction_checkpoint.json",
    "base_scheduler_contract.json",
    "continuation_source_inventory.json",
    "continuation_limit_contract.json",
    "continuation_scheduler_contract.json",
})
PROTOCOL_RECOVERY_AMENDMENT_FILES = frozenset({
    "amendment.json",
    "amendment.sha256",
    "recovery_checkpoint.json",
    "continuation_source_inventory.json",
    "continuation_limit_contract.json",
    "continuation_scheduler_contract.json",
})
PROTOCOL_POSTMORTEM_AMENDMENT_FILES = frozenset({
    "amendment.json",
    "amendment.sha256",
    "recovery_checkpoint.json",
    "continuation_source_inventory.json",
    "continuation_limit_contract.json",
    "continuation_scheduler_contract.json",
})
RECOVERY_ALLOWED_SOURCE_CHANGES = frozenset({
    "scripts/clone8_continuation_amendment.py",
    "scripts/clone8_leaderboard_campaign.py",
    "tests/test_clone8_continuation_amendment.py",
    "tests/test_clone8_leaderboard_campaign.py",
})
VERIFIER_CORRECTION_ALLOWED_SOURCE_CHANGES = frozenset({
    "scripts/clone8_continuation_amendment.py",
    "scripts/clone8_leaderboard_campaign.py",
    "tests/test_clone8_continuation_amendment.py",
    "tests/test_clone8_leaderboard_campaign.py",
})
PROTOCOL_RECOVERY_ALLOWED_SOURCE_CHANGES = frozenset({
    "agentarena/scaffolds/browseruse.py",
    "agentarena/scaffolds/test_browseruse_trace.py",
    "scripts/clone8_continuation_amendment.py",
    "scripts/clone8_leaderboard_campaign.py",
    "tests/test_clone8_continuation_amendment.py",
    "tests/test_clone8_leaderboard_campaign.py",
})
PROTOCOL_POSTMORTEM_ALLOWED_SOURCE_CHANGES = frozenset({
    "scripts/clone8_continuation_amendment.py",
    "scripts/clone8_leaderboard_campaign.py",
    "tests/test_clone8_continuation_amendment.py",
    "tests/test_clone8_leaderboard_campaign.py",
})

STATUS_AMENDMENT_FIELD = "continuation_amendment_sha256"
STATUS_SCHEDULER_FIELD = "active_scheduler_sha256"
DENOMINATOR_COUNTING_RULE = (
    "each manifest run index contributes exactly one selected terminal result; "
    "for each authorized index immutable attempt 1 is superseded by attempt 2, "
    "or by attempt 2's terminal successor under the unchanged positive-"
    "infrastructure retry policy"
)
VERIFIER_CORRECTION_COUNTING_RULE = (
    "each manifest run index contributes exactly one selected terminal result; "
    "an exact hash-bound verifier correction changes only the interpretation "
    "of the retained attempt, never its artifacts, row identity, or attempt; "
    "no row is added, removed, redrawn, or retried"
)
PROTOCOL_RECOVERY_COUNTING_RULE = (
    "each of the 960 immutable manifest run indices contributes exactly one "
    "selected terminal result; verifier-corrected attempts remain byte-identical, "
    "only hash-bound run 598 attempt 1 may be superseded by attempt 2 or its "
    "terminal positive-infrastructure successor, and no row is added, removed, "
    "redrawn, or reweighted"
)
PROTOCOL_POSTMORTEM_COUNTING_RULE = (
    "each of the 960 immutable manifest run indices contributes exactly one "
    "selected terminal result; the three verifier-corrected attempts remain "
    "byte-identical, exact hash-bound runs 598 and 642 attempt 1 may each be "
    "superseded by attempt 2 or its terminal positive-infrastructure successor, "
    "and no row is added, removed, redrawn, or reweighted"
)


class ContinuationAmendmentError(RuntimeError):
    """The continuation cannot be trusted and must not launch work."""


@dataclasses.dataclass(frozen=True)
class InterruptedAttempt:
    run_index: int
    run_id: str
    pid: int
    launch_receipt: str
    launch_sha256: str
    completion_receipt: str
    completion_sha256: str
    finished_utc: str


@dataclasses.dataclass(frozen=True)
class AmendmentAuthorization:
    campaign_uuid: str
    base_manifest_sha256: str
    operator_stop_receipt: str
    operator_stop_sha256: str
    operator_stop_requested_utc: str
    preserved_classes: tuple[tuple[str, int], ...]
    interrupted: tuple[InterruptedAttempt, ...]

    @property
    def preserved_count(self) -> int:
        return sum(count for _name, count in self.preserved_classes)

    @property
    def checkpoint_count(self) -> int:
        return self.preserved_count + len(self.interrupted)


CheckoutProof = Callable[[Path, Mapping[str, Any]], Mapping[str, Any]]
PostmortemProof = Callable[
    [
        Path,
        Mapping[str, Any],
        Path,
        Mapping[str, Any],
        Mapping[str, Any],
        Mapping[str, Any],
    ],
    Mapping[str, Any],
]
VerifierCorrectionProof = Callable[
    [
        Path,
        Mapping[str, Any],
        Mapping[str, Any],
        int,
        Path,
        Mapping[str, Any],
        Mapping[str, Any],
    ],
    Mapping[str, Any],
]
LosslessReadStateProof = Callable[
    [
        Path,
        Mapping[str, Any],
        Mapping[str, Any],
        int,
        Path,
        Mapping[str, Any],
        Mapping[str, Any],
    ],
    Mapping[str, Any],
]


@dataclasses.dataclass(frozen=True)
class VerifiedAmendment:
    campaign: Path
    directory: Path
    amendment: Mapping[str, Any]
    checkpoint: Mapping[str, Any]
    scheduler: Mapping[str, Any]
    amendment_sha256: str
    scheduler_sha256: str

    @property
    def retry_by_index(self) -> dict[int, Mapping[str, Any]]:
        attempts = self.checkpoint.get(
            "authorized_retry_attempts",
            self.checkpoint.get("interrupted_attempts", []),
        )
        return {
            int(item["run_index"]): item
            for item in attempts
        }

    @property
    def correction_by_index(self) -> dict[int, Mapping[str, Any]]:
        return {
            int(item["run_index"]): item
            for item in self.checkpoint.get("verifier_corrections", [])
        }

    @property
    def active_limits(self) -> Mapping[str, Any]:
        return self.scheduler["active_limits"]


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_payload(value: Any) -> str:
    return _sha_bytes(_canonical_bytes(value))


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except Exception as exc:
        raise ContinuationAmendmentError(
            f"cannot read JSON object {path}: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ContinuationAmendmentError(f"JSON is not an object: {path}")
    return value


def _parse_utc(value: Any) -> dt.datetime:
    if not isinstance(value, str):
        raise ContinuationAmendmentError("timestamp is not a string")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContinuationAmendmentError(f"invalid UTC timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ContinuationAmendmentError("timestamp has no timezone")
    return parsed.astimezone(dt.timezone.utc)


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _safe_path(root: Path, relative: str, *, regular: bool = True) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise ContinuationAmendmentError(f"unsafe relative path: {relative!r}")
    root = root.resolve()
    candidate = root.joinpath(*pure.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ContinuationAmendmentError(f"required path is absent: {relative}") from exc
    if resolved != root and root not in resolved.parents:
        raise ContinuationAmendmentError(f"path escapes campaign: {relative}")
    current = candidate
    while current != root:
        if current.is_symlink():
            raise ContinuationAmendmentError(f"symlink is forbidden: {relative}")
        current = current.parent
    if regular and not candidate.is_file():
        raise ContinuationAmendmentError(f"path is not a regular file: {relative}")
    return candidate


def _relative(campaign: Path, path: Path) -> str:
    campaign = campaign.resolve()
    try:
        return path.resolve().relative_to(campaign).as_posix()
    except ValueError as exc:
        raise ContinuationAmendmentError(f"path is outside campaign: {path}") from exc


def _read_manifest_hash(campaign: Path) -> str:
    path = _safe_path(campaign, "campaign_manifest.sha256")
    fields = path.read_text().split()
    if len(fields) != 2 or fields[1] != "campaign_manifest.json" or not _is_sha256(fields[0]):
        raise ContinuationAmendmentError("campaign_manifest.sha256 is malformed")
    return fields[0]


def _validate_source_inventory(value: Any) -> None:
    if not isinstance(value, dict) or not value:
        raise ContinuationAmendmentError("source inventory is not a nonempty object")
    for path, record in value.items():
        pure = PurePosixPath(path) if isinstance(path, str) else None
        if (
            pure is None
            or pure.is_absolute()
            or not pure.parts
            or ".." in pure.parts
            or not isinstance(record, dict)
            or set(record) != {"sha256", "size"}
            or not _is_sha256(record.get("sha256"))
            or type(record.get("size")) is not int
            or record["size"] < 0
        ):
            raise ContinuationAmendmentError("source inventory entry is malformed")


def _validate_recovery_source_delta(
    predecessor: Mapping[str, Any], current: Mapping[str, Any]
) -> None:
    """Allow only the complete, explicit C3 recovery implementation delta."""

    _validate_source_inventory(predecessor)
    _validate_source_inventory(current)
    if set(current) != set(predecessor):
        raise ContinuationAmendmentError(
            "recovery source inventory key set differs from C2"
        )
    changed = frozenset(
        path for path in current if current[path] != predecessor[path]
    )
    if changed != RECOVERY_ALLOWED_SOURCE_CHANGES:
        raise ContinuationAmendmentError(
            "recovery source delta is not the exact C3 implementation allowlist"
        )


def _verifier_correction_source_delta(
    predecessor: Mapping[str, Any], current: Mapping[str, Any]
) -> list[str]:
    """Return the exact evaluator/correction implementation delta.

    A verifier correction may not be used as a back door to change catalogs,
    tasks, the browser harness, or launch policy.  The complete source trees
    must have the same key inventory and only the correction implementation
    and its focused tests may differ.  Their *new* hashes are then sealed into
    the amendment rather than written back into the base freeze.
    """

    _validate_source_inventory(predecessor)
    _validate_source_inventory(current)
    if set(current) != set(predecessor):
        raise ContinuationAmendmentError(
            "verifier-correction source inventory key set differs from base"
        )
    changed = sorted(
        path for path in current if current[path] != predecessor[path]
    )
    if frozenset(changed) != VERIFIER_CORRECTION_ALLOWED_SOURCE_CHANGES:
        raise ContinuationAmendmentError(
            "verifier-correction source delta is not the exact implementation "
            "allowlist"
        )
    return changed


def _protocol_recovery_source_delta(
    predecessor: Mapping[str, Any], current: Mapping[str, Any]
) -> list[str]:
    """Return the complete, exact implementation delta for recovery 002.

    The protocol successor is deliberately incapable of changing a catalog,
    task, steering surface, model configuration, or generic launch policy.
    Its six-file delta is the instance-local read-state repair, the controller
    binding, this protocol implementation, and their focused tests.
    """

    _validate_source_inventory(predecessor)
    _validate_source_inventory(current)
    if set(current) != set(predecessor):
        raise ContinuationAmendmentError(
            "protocol-recovery source inventory key set differs from predecessor"
        )
    changed = sorted(
        path for path in current if current[path] != predecessor[path]
    )
    if frozenset(changed) != PROTOCOL_RECOVERY_ALLOWED_SOURCE_CHANGES:
        raise ContinuationAmendmentError(
            "protocol-recovery source delta is not the exact six-file allowlist"
        )
    return changed


def _protocol_postmortem_source_delta(
    predecessor: Mapping[str, Any], current: Mapping[str, Any]
) -> list[str]:
    """Return the exact four-file, amendment-side recovery delta."""

    _validate_source_inventory(predecessor)
    _validate_source_inventory(current)
    if set(current) != set(predecessor):
        raise ContinuationAmendmentError(
            "protocol-postmortem source inventory key set differs from predecessor"
        )
    changed = sorted(
        path for path in current if current[path] != predecessor[path]
    )
    if frozenset(changed) != PROTOCOL_POSTMORTEM_ALLOWED_SOURCE_CHANGES:
        raise ContinuationAmendmentError(
            "protocol-postmortem source delta is not the exact four-file allowlist"
        )
    return changed


def _validate_limit_contract(value: Any) -> None:
    if not isinstance(value, dict) or set(value) < {"schema_version", "categories", "sha256"}:
        raise ContinuationAmendmentError("limit contract is malformed")
    payload = {key: item for key, item in value.items() if key != "sha256"}
    if value.get("sha256") != _sha_payload(payload):
        raise ContinuationAmendmentError("limit contract internal hash mismatch")


def base_scheduler_contract(manifest: Mapping[str, Any], limit_contract: Mapping[str, Any]) -> dict:
    try:
        launch = limit_contract["categories"]["launch_only"]["campaign_launch"]
    except (KeyError, TypeError) as exc:
        raise ContinuationAmendmentError(
            "limit contract lacks the campaign launch scheduler record"
        ) from exc
    return {
        "schema": BASE_SCHEDULER_SCHEMA,
        "manifest_schedule": manifest.get("schedule"),
        "limit_contract_campaign_launch": launch,
    }


def _unchanged_correction_scheduler(
    manifest: Mapping[str, Any], limit_contract: Mapping[str, Any]
) -> dict[str, Any]:
    """Reconstruct the base campaign's active scheduler without policy drift."""

    schedule = manifest.get("schedule") or {}
    regions = list(schedule.get("regions") or [])
    logicals = sorted({
        str(item["logical"])
        for item in (manifest.get("models") or {}).values()
    })
    try:
        admission = limit_contract["categories"]["launch_only"][
            "campaign_launch"
        ]["configured"]["block_admission_policy"]
    except (KeyError, TypeError) as exc:
        raise ContinuationAmendmentError(
            "base limit contract lacks block admission policy"
        ) from exc
    required = (
        "default_jobs",
        "global_spawn_stagger_seconds",
        "host_browser_root_ceiling",
        "reserved_external_browser_roots",
        "primary_region_deployment_cap",
        "logical_deployment_cap",
    )
    if any(name not in schedule for name in required):
        raise ContinuationAmendmentError(
            "base schedule lacks an unchanged correction limit"
        )
    return make_scheduler_contract(
        manifest,
        jobs=schedule["default_jobs"],
        spawn_stagger_seconds=schedule["global_spawn_stagger_seconds"],
        host_browser_root_ceiling=schedule["host_browser_root_ceiling"],
        reserved_external_browser_roots=schedule[
            "reserved_external_browser_roots"
        ],
        primary_region_deployment_caps={
            logical: {
                region: schedule["primary_region_deployment_cap"]
                for region in regions
            }
            for logical in logicals
        },
        logical_deployment_caps={
            logical: schedule["logical_deployment_cap"]
            for logical in logicals
        },
        admission_policy=admission,
        scratch_policy=manifest.get("scratch"),
    )


def _authorization_payload(value: AmendmentAuthorization) -> dict[str, Any]:
    return {
        "campaign_uuid": value.campaign_uuid,
        "base_manifest_sha256": value.base_manifest_sha256,
        "operator_stop_receipt": value.operator_stop_receipt,
        "operator_stop_sha256": value.operator_stop_sha256,
        "operator_stop_requested_utc": value.operator_stop_requested_utc,
        "preserved_classes": dict(value.preserved_classes),
        "interrupted": [dataclasses.asdict(item) for item in value.interrupted],
    }


def _authorization_from_payload(value: Any) -> AmendmentAuthorization:
    exact = {
        "campaign_uuid",
        "base_manifest_sha256",
        "operator_stop_receipt",
        "operator_stop_sha256",
        "operator_stop_requested_utc",
        "preserved_classes",
        "interrupted",
    }
    if not isinstance(value, dict) or set(value) != exact:
        raise ContinuationAmendmentError("authorization schema is not exact")
    if not _is_sha256(value.get("base_manifest_sha256")) or not _is_sha256(
        value.get("operator_stop_sha256")
    ):
        raise ContinuationAmendmentError("authorization hash is malformed")
    _parse_utc(value.get("operator_stop_requested_utc"))
    classes = value.get("preserved_classes")
    if (
        not isinstance(classes, dict)
        or any(
            not isinstance(name, str) or type(count) is not int or count < 0
            for name, count in classes.items()
        )
    ):
        raise ContinuationAmendmentError("authorization class inventory is malformed")
    raw_interrupted = value.get("interrupted")
    if not isinstance(raw_interrupted, list) or not raw_interrupted:
        raise ContinuationAmendmentError("authorization interruption list is empty")
    fields = {field.name for field in dataclasses.fields(InterruptedAttempt)}
    interrupted: list[InterruptedAttempt] = []
    for raw in raw_interrupted:
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ContinuationAmendmentError("interrupted-attempt schema is not exact")
        try:
            item = InterruptedAttempt(**raw)
        except TypeError as exc:
            raise ContinuationAmendmentError("interrupted attempt is malformed") from exc
        if (
            type(item.run_index) is not int
            or item.run_index < 0
            or type(item.pid) is not int
            or item.pid <= 0
            or not item.run_id
            or not _is_sha256(item.launch_sha256)
            or not _is_sha256(item.completion_sha256)
        ):
            raise ContinuationAmendmentError("interrupted attempt field is malformed")
        _parse_utc(item.finished_utc)
        interrupted.append(item)
    if len({item.run_index for item in interrupted}) != len(interrupted):
        raise ContinuationAmendmentError("authorization has duplicate run indices")
    return AmendmentAuthorization(
        campaign_uuid=str(value["campaign_uuid"]),
        base_manifest_sha256=value["base_manifest_sha256"],
        operator_stop_receipt=str(value["operator_stop_receipt"]),
        operator_stop_sha256=value["operator_stop_sha256"],
        operator_stop_requested_utc=value["operator_stop_requested_utc"],
        preserved_classes=tuple(sorted(classes.items())),
        interrupted=tuple(interrupted),
    )


def make_scheduler_contract(
    manifest: Mapping[str, Any],
    *,
    jobs: int,
    spawn_stagger_seconds: float,
    host_browser_root_ceiling: int,
    reserved_external_browser_roots: int,
    primary_region_deployment_caps: Mapping[str, Mapping[str, int]],
    logical_deployment_caps: Mapping[str, int],
    admission_policy: Mapping[str, Any],
    scratch_policy: Mapping[str, Any] | None = None,
) -> dict:
    """Build the exact scheduler object consumed by launcher and monitor."""
    regions = list((manifest.get("schedule") or {}).get("regions") or [])
    logicals = sorted({
        str(item["logical"])
        for item in (manifest.get("models") or {}).values()
    })
    contract = {
        "schema": CONTINUATION_SCHEDULER_SCHEMA,
        "base_manifest_schedule_sha256": _sha_payload(manifest.get("schedule")),
        "manifest_total_runs": manifest.get("total_runs"),
        "regions": regions,
        "logical_deployments": logicals,
        "active_limits": {
            "jobs": jobs,
            "spawn_stagger_seconds": spawn_stagger_seconds,
            "host_browser_root_ceiling": host_browser_root_ceiling,
            "reserved_external_browser_roots": reserved_external_browser_roots,
            "primary_region_deployment_caps": {
                logical: dict(region_caps)
                for logical, region_caps in primary_region_deployment_caps.items()
            },
            "logical_deployment_caps": dict(logical_deployment_caps),
        },
        "admission_policy": dict(admission_policy),
        "scratch_policy": dict(scratch_policy) if scratch_policy is not None else None,
    }
    validate_scheduler_contract(contract, manifest)
    return contract


def validate_scheduler_contract(
    value: Any, manifest: Mapping[str, Any]
) -> None:
    exact = {
        "schema",
        "base_manifest_schedule_sha256",
        "manifest_total_runs",
        "regions",
        "logical_deployments",
        "active_limits",
        "admission_policy",
        "scratch_policy",
    }
    if not isinstance(value, dict) or set(value) != exact:
        raise ContinuationAmendmentError("continuation scheduler schema is not exact")
    if value.get("schema") != CONTINUATION_SCHEDULER_SCHEMA:
        raise ContinuationAmendmentError("continuation scheduler schema is unsupported")
    if value.get("base_manifest_schedule_sha256") != _sha_payload(manifest.get("schedule")):
        raise ContinuationAmendmentError("scheduler is not bound to the base schedule")
    if value.get("manifest_total_runs") != manifest.get("total_runs"):
        raise ContinuationAmendmentError("scheduler denominator differs from manifest")
    regions = list((manifest.get("schedule") or {}).get("regions") or [])
    logicals = sorted({
        str(item["logical"])
        for item in (manifest.get("models") or {}).values()
    })
    if value.get("regions") != regions or value.get("logical_deployments") != logicals:
        raise ContinuationAmendmentError("scheduler region/model inventory drifted")
    limits = value.get("active_limits")
    limit_keys = {
        "jobs",
        "spawn_stagger_seconds",
        "host_browser_root_ceiling",
        "reserved_external_browser_roots",
        "primary_region_deployment_caps",
        "logical_deployment_caps",
    }
    if not isinstance(limits, dict) or set(limits) != limit_keys:
        raise ContinuationAmendmentError("scheduler active-limit schema is not exact")
    scalar_names = (
        "jobs",
        "host_browser_root_ceiling",
        "reserved_external_browser_roots",
    )
    if any(type(limits.get(name)) is not int for name in scalar_names):
        raise ContinuationAmendmentError("scheduler integer limit is malformed")
    jobs = limits["jobs"]
    ceiling = limits["host_browser_root_ceiling"]
    reserve = limits["reserved_external_browser_roots"]
    stagger = limits.get("spawn_stagger_seconds")
    if (
        jobs <= 0
        or ceiling <= 0
        or reserve < 0
        or jobs > ceiling - reserve
        or not isinstance(stagger, (int, float))
        or isinstance(stagger, bool)
        or stagger <= 0
    ):
        raise ContinuationAmendmentError("scheduler host/stagger limits are invalid")
    regional = limits.get("primary_region_deployment_caps")
    logical_caps = limits.get("logical_deployment_caps")
    if not isinstance(regional, dict) or set(regional) != set(logicals):
        raise ContinuationAmendmentError("scheduler regional logical inventory is not exact")
    if not isinstance(logical_caps, dict) or set(logical_caps) != set(logicals):
        raise ContinuationAmendmentError("scheduler logical-cap inventory is not exact")
    for logical in logicals:
        by_region = regional[logical]
        logical_cap = logical_caps[logical]
        if not isinstance(by_region, dict) or set(by_region) != set(regions):
            raise ContinuationAmendmentError(
                f"scheduler region inventory is not exact for {logical}"
            )
        if any(type(cap) is not int or cap < 0 for cap in by_region.values()):
            raise ContinuationAmendmentError(f"invalid regional cap for {logical}")
        if type(logical_cap) is not int or logical_cap <= 0:
            raise ContinuationAmendmentError(f"invalid logical cap for {logical}")
        if logical_cap > sum(by_region.values()) or max(by_region.values()) > logical_cap:
            raise ContinuationAmendmentError(f"incoherent caps for {logical}")
    if not isinstance(value.get("admission_policy"), dict) or not value["admission_policy"]:
        raise ContinuationAmendmentError("scheduler admission policy is empty")
    scratch = value.get("scratch_policy")
    if scratch is not None and (
        not isinstance(scratch, dict)
        or not isinstance(scratch.get("schema"), str)
        or not isinstance(scratch.get("root"), str)
    ):
        raise ContinuationAmendmentError("scheduler scratch policy is malformed")


def _load_base_unbound(
    campaign: Path,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    campaign = campaign.resolve()
    manifest_path = _safe_path(campaign, "campaign_manifest.json")
    manifest_hash = _sha_file(manifest_path)
    if _read_manifest_hash(campaign) != manifest_hash:
        raise ContinuationAmendmentError("base manifest hash record differs")
    manifest = _read_object(manifest_path)
    if not isinstance(manifest.get("campaign_uuid"), str) or not manifest["campaign_uuid"]:
        raise ContinuationAmendmentError("campaign UUID is absent")
    if Path(str(manifest.get("campaign", ""))).resolve() != campaign:
        raise ContinuationAmendmentError("manifest campaign path differs")
    if type(manifest.get("total_runs")) is not int or manifest["total_runs"] <= 0:
        raise ContinuationAmendmentError("manifest denominator is invalid")
    rows = manifest.get("runs")
    if not isinstance(rows, list) or len(rows) != manifest["total_runs"]:
        raise ContinuationAmendmentError("manifest row inventory differs from denominator")
    if [row.get("index") for row in rows] != list(range(len(rows))):
        raise ContinuationAmendmentError("manifest run indices are not canonical")
    frozen = manifest.get("frozen_artifacts")
    if not isinstance(frozen, dict) or not frozen:
        raise ContinuationAmendmentError("manifest frozen-artifact map is absent")
    for filename, expected in frozen.items():
        if not _is_sha256(expected):
            raise ContinuationAmendmentError("manifest frozen hash is malformed")
        path = _safe_path(campaign, f"frozen_inputs/{filename}")
        if _sha_file(path) != expected:
            raise ContinuationAmendmentError(f"base frozen artifact drifted: {filename}")
    old_source = _read_object(_safe_path(campaign, "frozen_inputs/code_inventory.json"))
    old_limit = _read_object(_safe_path(campaign, "frozen_inputs/limit_contract.json"))
    _validate_source_inventory(old_source)
    _validate_limit_contract(old_limit)
    return manifest, old_limit, manifest_hash


def _validate_base(
    campaign: Path, authorization: AmendmentAuthorization
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest, old_limit, manifest_hash = _load_base_unbound(campaign)
    if manifest_hash != authorization.base_manifest_sha256:
        raise ContinuationAmendmentError("base manifest hash differs from authorization")
    if manifest.get("campaign_uuid") != authorization.campaign_uuid:
        raise ContinuationAmendmentError("campaign UUID differs from authorization")
    return manifest, old_limit


def derive_authorization(
    campaign: Path,
    *,
    operator_stop_receipt: str = "operator_stop_receipt.json",
) -> AmendmentAuthorization:
    """Derive retry authority from the exact operator-stop checkpoint.

    There is intentionally no argument selecting run indices.  The immutable
    stop receipt is the complete allowlist; every listed run must have the
    narrow interrupted scientific-invalid receipt and every unlisted completed
    row must already be scored or behavioral.
    """
    campaign = campaign.resolve()
    manifest, _old_limit, manifest_hash = _load_base_unbound(campaign)
    stop_path = _safe_path(campaign, operator_stop_receipt)
    stop = _read_object(stop_path)
    running = stop.get("running")
    if (
        stop.get("schema") != "agentarena.clone8-operator-stop.v1"
        or stop.get("signal") != 15
        or stop.get("worker_sessions_isolated") is not True
        or stop.get("workers_signaled") is not False
        or not isinstance(running, list)
        or not running
    ):
        raise ContinuationAmendmentError("operator-stop receipt is not retry-eligible")
    requested = stop.get("requested_utc")
    requested_time = _parse_utc(requested)
    rows_by_id = {row.get("run_id"): row for row in manifest["runs"]}
    if len(rows_by_id) != len(manifest["runs"]):
        raise ContinuationAmendmentError("manifest run IDs are not unique")
    interrupted: list[InterruptedAttempt] = []
    stopped_ids: set[str] = set()
    for stopped in running:
        if (
            not isinstance(stopped, dict)
            or set(stopped) != {"attempt", "pid", "run_id"}
            or stopped.get("attempt") != 1
            or type(stopped.get("pid")) is not int
            or stopped.get("run_id") not in rows_by_id
            or stopped["run_id"] in stopped_ids
        ):
            raise ContinuationAmendmentError("operator-stop running entry is malformed")
        stopped_ids.add(stopped["run_id"])
        row = rows_by_id[stopped["run_id"]]
        stem = f"{row['index']:04d}_{row['cell_name']}.attempt1"
        launch_path = _safe_path(campaign, f"launch_receipts/{stem}.launch.json")
        completion_path = _safe_path(
            campaign, f"completion_receipts/{stem}.complete.json"
        )
        launch = _read_object(launch_path)
        completion = _read_object(completion_path)
        finished = completion.get("finished_utc")
        delta = (_parse_utc(finished) - requested_time).total_seconds()
        if (
            launch.get("run_index") != row["index"]
            or launch.get("run_id") != row["run_id"]
            or launch.get("attempt") != 1
            or launch.get("pid") != stopped["pid"]
            or completion.get("run_index") != row["index"]
            or completion.get("run_id") != row["run_id"]
            or completion.get("attempt") != 1
            or completion.get("pid") != stopped["pid"]
            or completion.get("returncode") != 0
            or (completion.get("classification") or {}).get("class")
            != "scientific_invalid"
            or (completion.get("classification") or {}).get("code")
            != "unpersisted_or_incomplete_attempt"
            or not _green_cleanup(completion)
            or delta < 0
            or delta > 10
        ):
            raise ContinuationAmendmentError(
                "operator-stopped attempt lacks the exact interrupted receipt"
            )
        interrupted.append(InterruptedAttempt(
            run_index=row["index"],
            run_id=row["run_id"],
            pid=stopped["pid"],
            launch_receipt=_relative(campaign, launch_path),
            launch_sha256=_sha_file(launch_path),
            completion_receipt=_relative(campaign, completion_path),
            completion_sha256=_sha_file(completion_path),
            finished_utc=finished,
        ))

    preserved_classes: Counter[str] = Counter()
    seen_rows: set[int] = set()
    for path in sorted((campaign / "completion_receipts").glob("*.complete.json")):
        completion = _read_object(path)
        run_index = completion.get("run_index")
        run_id = completion.get("run_id")
        if type(run_index) is not int or run_index in seen_rows:
            raise ContinuationAmendmentError("checkpoint completion rows are not unique")
        seen_rows.add(run_index)
        if run_id in stopped_ids:
            continue
        result_class = (completion.get("classification") or {}).get("class")
        if completion.get("attempt") != 1 or result_class not in {"scored", "behavioral"}:
            raise ContinuationAmendmentError(
                "unlisted checkpoint result is not terminal scored/behavioral attempt 1"
            )
        preserved_classes[result_class] += 1
    if len(seen_rows) != len(interrupted) + sum(preserved_classes.values()):
        raise ContinuationAmendmentError("checkpoint receipt accounting differs")
    return AmendmentAuthorization(
        campaign_uuid=manifest["campaign_uuid"],
        base_manifest_sha256=manifest_hash,
        operator_stop_receipt=operator_stop_receipt,
        operator_stop_sha256=_sha_file(stop_path),
        operator_stop_requested_utc=requested,
        preserved_classes=tuple(sorted(preserved_classes.items())),
        interrupted=tuple(interrupted),
    )


def _receipt_artifact_hashes(out_dir: Path, row: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("summary.json", "trajectory.json"):
        path = out_dir / name
        if path.is_symlink() or not path.is_file():
            raise ContinuationAmendmentError(
                f"preserved terminal artifact is absent: {out_dir / name}"
            )
        result[name] = _sha_file(path)
    db_path = out_dir / f"{row['env']}_{row['port']}.db"
    if db_path.is_symlink() or not db_path.is_file():
        raise ContinuationAmendmentError(f"preserved attempt DB is absent: {db_path}")
    result[db_path.name] = _sha_file(db_path)
    return result


def _green_cleanup(completion: Mapping[str, Any]) -> bool:
    cleanup = completion.get("worker_scope_cleanup")
    return bool(
        isinstance(cleanup, dict)
        and cleanup.get("confirmed_empty") is True
        and cleanup.get("port_released") is True
        and cleanup.get("remaining_scope_pids") == []
        and cleanup.get("uncertified_scope_pids") == []
        and cleanup.get("error") is None
    )


def _verify_stop_receipt(
    campaign: Path, authorization: AmendmentAuthorization
) -> dict[str, Any]:
    stop_path = _safe_path(campaign, authorization.operator_stop_receipt)
    if _sha_file(stop_path) != authorization.operator_stop_sha256:
        raise ContinuationAmendmentError("operator-stop receipt hash differs")
    stop = _read_object(stop_path)
    expected_running = [
        {"attempt": 1, "pid": item.pid, "run_id": item.run_id}
        for item in authorization.interrupted
    ]
    if (
        stop.get("schema") != "agentarena.clone8-operator-stop.v1"
        or stop.get("requested_utc") != authorization.operator_stop_requested_utc
        or stop.get("signal") != 15
        or stop.get("worker_sessions_isolated") is not True
        or stop.get("workers_signaled") is not False
        or stop.get("running") != expected_running
    ):
        raise ContinuationAmendmentError("operator-stop receipt content differs")
    return stop


def _attempt_out_dir(campaign: Path, run_id: str, attempt: int) -> Path:
    relative = f"runs/attempt_{attempt}/{run_id}"
    return _safe_path(campaign, relative, regular=False)


def _collect_checkpoint(
    campaign: Path,
    manifest: Mapping[str, Any],
    authorization: AmendmentAuthorization,
    checkout_proof: CheckoutProof,
) -> dict[str, Any]:
    _verify_stop_receipt(campaign, authorization)
    completion_paths = sorted((campaign / "completion_receipts").glob("*.complete.json"))
    launch_paths = sorted((campaign / "launch_receipts").glob("*.launch.json"))
    if len(completion_paths) != authorization.checkpoint_count:
        raise ContinuationAmendmentError("checkpoint completion-receipt count differs")
    if len(launch_paths) != authorization.checkpoint_count:
        raise ContinuationAmendmentError("checkpoint launch-receipt count differs")

    targets = {item.run_index: item for item in authorization.interrupted}
    if len(targets) != len(authorization.interrupted):
        raise ContinuationAmendmentError("interruption authorization has duplicate indices")
    preserved: list[dict[str, Any]] = []
    interrupted: list[dict[str, Any]] = []
    seen: set[int] = set()
    preserved_classes: Counter[str] = Counter()

    for completion_path in completion_paths:
        completion = _read_object(completion_path)
        run_index = completion.get("run_index")
        if type(run_index) is not int or run_index < 0 or run_index >= len(manifest["runs"]):
            raise ContinuationAmendmentError("completion has invalid run index")
        if run_index in seen:
            raise ContinuationAmendmentError("checkpoint has duplicate run index")
        seen.add(run_index)
        row = manifest["runs"][run_index]
        if (
            completion.get("schema") != "agentarena.clone8-completion-receipt.v3"
            or completion.get("run_id") != row.get("run_id")
            or completion.get("attempt") != 1
        ):
            raise ContinuationAmendmentError("completion identity differs from manifest")
        stem = completion_path.name.removesuffix(".complete.json")
        launch_path = campaign / "launch_receipts" / f"{stem}.launch.json"
        if launch_path.is_symlink() or not launch_path.is_file():
            raise ContinuationAmendmentError("matching launch receipt is absent")
        launch = _read_object(launch_path)
        if (
            launch.get("schema") != "agentarena.clone8-launch-receipt.v3"
            or launch.get("campaign_uuid") != authorization.campaign_uuid
            or launch.get("manifest_sha256") != authorization.base_manifest_sha256
            or launch.get("run_index") != run_index
            or launch.get("run_id") != row.get("run_id")
            or launch.get("attempt") != 1
        ):
            raise ContinuationAmendmentError("launch identity differs from manifest")
        out_dir = _attempt_out_dir(campaign, row["run_id"], 1)
        if Path(str(launch.get("out_dir", ""))).resolve() != out_dir.resolve():
            raise ContinuationAmendmentError("launch attempt directory differs")
        common = {
            "run_index": run_index,
            "run_id": row["run_id"],
            "attempt": 1,
            "launch_receipt": _relative(campaign, launch_path),
            "launch_sha256": _sha_file(launch_path),
            "completion_receipt": _relative(campaign, completion_path),
            "completion_sha256": _sha_file(completion_path),
        }
        target = targets.get(run_index)
        if target is None:
            classification = completion.get("classification") or {}
            result_class = classification.get("class")
            if result_class not in {"scored", "behavioral"}:
                raise ContinuationAmendmentError(
                    "non-authorized checkpoint result is not scored/behavioral"
                )
            preserved_classes[result_class] += 1
            preserved.append({
                **common,
                "classification": classification,
                "authoritative_artifacts": _receipt_artifact_hashes(out_dir, row),
            })
            continue

        if (
            common["launch_receipt"] != target.launch_receipt
            or common["launch_sha256"] != target.launch_sha256
            or common["completion_receipt"] != target.completion_receipt
            or common["completion_sha256"] != target.completion_sha256
            or completion.get("pid") != target.pid
            or launch.get("pid") != target.pid
            or completion.get("finished_utc") != target.finished_utc
            or completion.get("returncode") != 0
            or completion.get("classification") != {
                "class": "scientific_invalid",
                "code": "unpersisted_or_incomplete_attempt",
                "evidence": (
                    "missing terminal artifacts: summary.json, trajectory.json; "
                    "exact SIGKILL plus both-artifacts-absent plus a green "
                    "authoritative no-checkout proof is required for refill"
                ),
            }
            or not _green_cleanup(completion)
        ):
            raise ContinuationAmendmentError("interrupted receipt differs from authorization")
        for terminal in (out_dir / "summary.json", out_dir / "trajectory.json"):
            if terminal.exists() or terminal.is_symlink():
                raise ContinuationAmendmentError(
                    "interrupted attempt unexpectedly has a terminal artifact"
                )
        next_dir = campaign / "runs" / "attempt_2" / row["run_id"]
        if next_dir.exists() or next_dir.is_symlink():
            raise ContinuationAmendmentError("attempt 2 predates its amendment")
        proof = dict(checkout_proof(out_dir, row))
        if (
            proof.get("confirmed_no_checkout") is not True
            or proof.get("hash_stable") is not True
            or proof.get("sidecars_absent") is not True
            or proof.get("error") is not None
        ):
            raise ContinuationAmendmentError("interrupted attempt lacks a green checkout proof")
        interrupted.append({
            **common,
            "pid": target.pid,
            "finished_utc": target.finished_utc,
            "classification": completion["classification"],
            "no_checkout_proof": proof,
            "superseded_by_attempt": 2,
        })

    if preserved_classes != Counter(dict(authorization.preserved_classes)):
        raise ContinuationAmendmentError(
            f"preserved class counts differ: {dict(preserved_classes)}"
        )
    if {item["run_index"] for item in interrupted} != set(targets):
        raise ContinuationAmendmentError("not every authorized interruption was observed")
    return {
        "schema": CHECKPOINT_SCHEMA,
        "campaign_uuid": authorization.campaign_uuid,
        "manifest_sha256": authorization.base_manifest_sha256,
        "operator_stop_receipt": authorization.operator_stop_receipt,
        "operator_stop_sha256": authorization.operator_stop_sha256,
        "operator_stop_requested_utc": authorization.operator_stop_requested_utc,
        "counts": {
            "checkpoint_completion_receipts": authorization.checkpoint_count,
            "preserved_terminal_rows": authorization.preserved_count,
            "authorized_interrupted_rows": len(authorization.interrupted),
            "preserved_classes": dict(sorted(preserved_classes.items())),
        },
        "preserved_terminal_results": sorted(
            preserved, key=lambda item: item["run_index"]
        ),
        "interrupted_attempts": sorted(
            interrupted, key=lambda item: item["run_index"]
        ),
    }


def _validate_continuation_payloads(
    source_inventory: Mapping[str, Any],
    limit_contract: Mapping[str, Any],
    scheduler_contract: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    _validate_source_inventory(source_inventory)
    _validate_limit_contract(limit_contract)
    validate_scheduler_contract(scheduler_contract, manifest)


def _write_exclusive(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def create_amendment(
    campaign: Path,
    *,
    continuation_source_inventory: Mapping[str, Any],
    continuation_limit_contract: Mapping[str, Any],
    continuation_scheduler_contract: Mapping[str, Any],
    checkout_proof: CheckoutProof,
    created_utc: str,
    authorization: AmendmentAuthorization | None = None,
) -> VerifiedAmendment:
    """Publish the single create-only amendment after validating all inputs."""
    campaign = campaign.resolve()
    _parse_utc(created_utc)
    if authorization is None:
        authorization = derive_authorization(campaign)
    manifest, old_limit = _validate_base(campaign, authorization)
    _validate_continuation_payloads(
        continuation_source_inventory,
        continuation_limit_contract,
        continuation_scheduler_contract,
        manifest,
    )
    checkpoint = _collect_checkpoint(
        campaign, manifest, authorization, checkout_proof
    )
    base_scheduler = base_scheduler_contract(manifest, old_limit)
    sidecars = {
        "base_checkpoint.json": checkpoint,
        "base_scheduler_contract.json": base_scheduler,
        "continuation_source_inventory.json": dict(continuation_source_inventory),
        "continuation_limit_contract.json": dict(continuation_limit_contract),
        "continuation_scheduler_contract.json": dict(continuation_scheduler_contract),
    }
    sidecar_hashes = {
        name: _sha_bytes(_json_bytes(payload)) for name, payload in sidecars.items()
    }
    frozen = manifest["frozen_artifacts"]
    amendment = {
        "schema": AMENDMENT_SCHEMA,
        "amendment_id": AMENDMENT_DIRECTORY,
        "created_utc": created_utc,
        "campaign_uuid": authorization.campaign_uuid,
        "authorization": _authorization_payload(authorization),
        "base_manifest": {
            "path": "campaign_manifest.json",
            "sha256": authorization.base_manifest_sha256,
            "total_runs": manifest["total_runs"],
            "frozen_artifacts": frozen,
        },
        "hash_chain": {
            "predecessor_amendment_sha256": None,
            "old": {
                "manifest_sha256": authorization.base_manifest_sha256,
                "source_inventory_sha256": frozen["code_inventory.json"],
                "limit_contract_sha256": frozen["limit_contract.json"],
                "scheduler_contract_sha256": sidecar_hashes[
                    "base_scheduler_contract.json"
                ],
            },
            "new": {
                "source_inventory_sha256": sidecar_hashes[
                    "continuation_source_inventory.json"
                ],
                "limit_contract_sha256": sidecar_hashes[
                    "continuation_limit_contract.json"
                ],
                "scheduler_contract_sha256": sidecar_hashes[
                    "continuation_scheduler_contract.json"
                ],
            },
        },
        "sidecars": sidecar_hashes,
        "retry_authorization": {
            "from_attempt": 1,
            "to_attempt": 2,
            "run_indices": [item.run_index for item in authorization.interrupted],
            "completion_receipt_sha256": {
                str(item.run_index): item.completion_sha256
                for item in authorization.interrupted
            },
            "scope": "exact_hash_allowlist_only",
        },
        "denominator": {
            "manifest_rows": manifest["total_runs"],
            "row_identity": "campaign_manifest.runs[].index",
            "preserved_checkpoint_rows": authorization.preserved_count,
            "superseded_attempts_retained_for_audit": len(authorization.interrupted),
            "added_rows": 0,
            "counting_rule": DENOMINATOR_COUNTING_RULE,
        },
    }
    directory = campaign / AMENDMENT_ROOT / AMENDMENT_DIRECTORY
    directory.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if directory.parent.is_symlink():
        raise ContinuationAmendmentError("amendment root must not be a symlink")
    try:
        directory.mkdir(mode=0o755)
    except FileExistsError as exc:
        raise ContinuationAmendmentError(
            f"create-only amendment already exists: {directory}"
        ) from exc
    try:
        for name, payload in sidecars.items():
            _write_exclusive(directory / name, _json_bytes(payload))
        amendment_raw = _json_bytes(amendment)
        _write_exclusive(directory / "amendment.json", amendment_raw)
        amendment_sha = _sha_bytes(amendment_raw)
        _write_exclusive(
            directory / "amendment.sha256",
            f"{amendment_sha}  amendment.json\n".encode(),
        )
    except Exception:
        # Never delete or overwrite a partially published amendment.  Its
        # missing final checksum makes it visibly invalid and requires operator
        # inspection rather than an automatic redraw.
        raise
    return verify_amendment(
        campaign,
        continuation_source_inventory=continuation_source_inventory,
        continuation_limit_contract=continuation_limit_contract,
        continuation_scheduler_contract=continuation_scheduler_contract,
        checkout_proof=checkout_proof,
        authorization=authorization,
    )


def _read_amendment_sha(directory: Path) -> str:
    fields = (directory / "amendment.sha256").read_text().split()
    if len(fields) != 2 or fields[1] != "amendment.json" or not _is_sha256(fields[0]):
        raise ContinuationAmendmentError("amendment.sha256 is malformed")
    if _sha_file(directory / "amendment.json") != fields[0]:
        raise ContinuationAmendmentError("amendment hash mismatch")
    return fields[0]


def _verify_checkpoint_files(
    campaign: Path,
    checkpoint: Mapping[str, Any],
    authorization: AmendmentAuthorization,
    checkout_proof: CheckoutProof | None,
) -> None:
    exact_checkpoint_keys = {
        "schema",
        "campaign_uuid",
        "manifest_sha256",
        "operator_stop_receipt",
        "operator_stop_sha256",
        "operator_stop_requested_utc",
        "counts",
        "preserved_terminal_results",
        "interrupted_attempts",
    }
    if not isinstance(checkpoint, dict) or set(checkpoint) != exact_checkpoint_keys:
        raise ContinuationAmendmentError("checkpoint schema is not exact")
    if checkpoint.get("schema") != CHECKPOINT_SCHEMA:
        raise ContinuationAmendmentError("checkpoint schema is unsupported")
    if (
        checkpoint.get("campaign_uuid") != authorization.campaign_uuid
        or checkpoint.get("manifest_sha256") != authorization.base_manifest_sha256
        or checkpoint.get("operator_stop_receipt")
        != authorization.operator_stop_receipt
        or checkpoint.get("operator_stop_sha256") != authorization.operator_stop_sha256
        or checkpoint.get("operator_stop_requested_utc")
        != authorization.operator_stop_requested_utc
    ):
        raise ContinuationAmendmentError("checkpoint base binding differs")
    counts = checkpoint.get("counts") or {}
    if counts != {
        "checkpoint_completion_receipts": authorization.checkpoint_count,
        "preserved_terminal_rows": authorization.preserved_count,
        "authorized_interrupted_rows": len(authorization.interrupted),
        "preserved_classes": dict(authorization.preserved_classes),
    }:
        raise ContinuationAmendmentError("checkpoint counts differ")
    preserved = checkpoint.get("preserved_terminal_results")
    interrupted = checkpoint.get("interrupted_attempts")
    if not isinstance(preserved, list) or len(preserved) != authorization.preserved_count:
        raise ContinuationAmendmentError("preserved checkpoint inventory differs")
    if not isinstance(interrupted, list) or len(interrupted) != len(authorization.interrupted):
        raise ContinuationAmendmentError("interrupted checkpoint inventory differs")
    for item in [*preserved, *interrupted]:
        for path_key, hash_key in (
            ("launch_receipt", "launch_sha256"),
            ("completion_receipt", "completion_sha256"),
        ):
            path = _safe_path(campaign, item[path_key])
            if _sha_file(path) != item.get(hash_key):
                raise ContinuationAmendmentError(f"checkpoint file drifted: {item[path_key]}")
    for item in preserved:
        out_dir = _attempt_out_dir(campaign, item["run_id"], item["attempt"])
        artifacts = item.get("authoritative_artifacts")
        if not isinstance(artifacts, dict) or not artifacts:
            raise ContinuationAmendmentError("preserved artifact inventory is absent")
        for name, expected in artifacts.items():
            path = out_dir / name
            if path.is_symlink() or not path.is_file() or _sha_file(path) != expected:
                raise ContinuationAmendmentError(f"preserved artifact drifted: {path}")
    targets = {item.run_index: item for item in authorization.interrupted}
    if {item.get("run_index") for item in interrupted} != set(targets):
        raise ContinuationAmendmentError("interrupted indices differ from authorization")
    for item in interrupted:
        target = targets[item["run_index"]]
        if (
            item.get("run_id") != target.run_id
            or item.get("completion_sha256") != target.completion_sha256
            or item.get("launch_sha256") != target.launch_sha256
            or item.get("superseded_by_attempt") != 2
        ):
            raise ContinuationAmendmentError("interrupted checkpoint binding differs")
        out_dir = _attempt_out_dir(campaign, target.run_id, 1)
        for terminal in (out_dir / "summary.json", out_dir / "trajectory.json"):
            if terminal.exists() or terminal.is_symlink():
                raise ContinuationAmendmentError("superseded attempt acquired terminal artifacts")
        stored_proof = item.get("no_checkout_proof")
        if not isinstance(stored_proof, dict) or stored_proof.get("confirmed_no_checkout") is not True:
            raise ContinuationAmendmentError("stored checkout proof is not green")
        db_path = Path(str(stored_proof.get("db_path", "")))
        if (
            db_path.resolve().parent != out_dir.resolve()
            or
            not db_path.is_file()
            or db_path.is_symlink()
            or _sha_file(db_path) != stored_proof.get("db_sha256")
        ):
            raise ContinuationAmendmentError("interrupted attempt DB drifted")
        if checkout_proof is not None:
            row = {"env": stored_proof.get("env"), "port": int(db_path.stem.rsplit("_", 1)[-1])}
            fresh = dict(checkout_proof(out_dir, row))
            if fresh != stored_proof:
                raise ContinuationAmendmentError("fresh checkout proof differs from checkpoint")


def _load_static(
    campaign: Path,
    *,
    authorization: AmendmentAuthorization | None,
    checkout_proof: CheckoutProof | None,
) -> VerifiedAmendment:
    campaign = campaign.resolve()
    directory = campaign / AMENDMENT_ROOT / AMENDMENT_DIRECTORY
    if directory.is_symlink() or not directory.is_dir():
        raise ContinuationAmendmentError("continuation amendment is absent")
    entries = list(directory.iterdir())
    actual = {path.name for path in entries}
    if (
        actual != AMENDMENT_FILES
        or any(path.is_symlink() or not path.is_file() for path in entries)
    ):
        raise ContinuationAmendmentError("amendment file inventory is not exact")
    amendment_sha = _read_amendment_sha(directory)
    amendment = _read_object(directory / "amendment.json")
    exact_amendment_keys = {
        "schema",
        "amendment_id",
        "created_utc",
        "campaign_uuid",
        "authorization",
        "base_manifest",
        "hash_chain",
        "sidecars",
        "retry_authorization",
        "denominator",
    }
    bundle_authorization = _authorization_from_payload(
        amendment.get("authorization")
    )
    if authorization is not None and bundle_authorization != authorization:
        raise ContinuationAmendmentError("expected authorization differs from bundle")
    authorization = bundle_authorization
    manifest, old_limit = _validate_base(campaign, authorization)
    if (
        set(amendment) != exact_amendment_keys
        or
        amendment.get("schema") != AMENDMENT_SCHEMA
        or amendment.get("amendment_id") != AMENDMENT_DIRECTORY
        or amendment.get("campaign_uuid") != authorization.campaign_uuid
    ):
        raise ContinuationAmendmentError("amendment identity differs")
    _parse_utc(amendment.get("created_utc"))
    sidecars = amendment.get("sidecars")
    expected_sidecars = AMENDMENT_FILES - {"amendment.json", "amendment.sha256"}
    if not isinstance(sidecars, dict) or set(sidecars) != expected_sidecars:
        raise ContinuationAmendmentError("amendment sidecar inventory is not exact")
    for name, expected in sidecars.items():
        if not _is_sha256(expected) or _sha_file(directory / name) != expected:
            raise ContinuationAmendmentError(f"amendment sidecar drifted: {name}")
    base = amendment.get("base_manifest") or {}
    if (
        base.get("path") != "campaign_manifest.json"
        or base.get("sha256") != authorization.base_manifest_sha256
        or base.get("total_runs") != manifest["total_runs"]
        or base.get("frozen_artifacts") != manifest["frozen_artifacts"]
    ):
        raise ContinuationAmendmentError("amendment base-manifest binding differs")
    base_scheduler = _read_object(directory / "base_scheduler_contract.json")
    if base_scheduler != base_scheduler_contract(manifest, old_limit):
        raise ContinuationAmendmentError("base scheduler snapshot differs")
    chain = amendment.get("hash_chain") or {}
    old_chain = chain.get("old") or {}
    new_chain = chain.get("new") or {}
    if chain.get("predecessor_amendment_sha256") is not None:
        raise ContinuationAmendmentError("unexpected predecessor amendment")
    if old_chain != {
        "manifest_sha256": authorization.base_manifest_sha256,
        "source_inventory_sha256": manifest["frozen_artifacts"]["code_inventory.json"],
        "limit_contract_sha256": manifest["frozen_artifacts"]["limit_contract.json"],
        "scheduler_contract_sha256": sidecars["base_scheduler_contract.json"],
    }:
        raise ContinuationAmendmentError("old hash chain differs")
    if new_chain != {
        "source_inventory_sha256": sidecars["continuation_source_inventory.json"],
        "limit_contract_sha256": sidecars["continuation_limit_contract.json"],
        "scheduler_contract_sha256": sidecars["continuation_scheduler_contract.json"],
    }:
        raise ContinuationAmendmentError("new hash chain differs")
    checkpoint = _read_object(directory / "base_checkpoint.json")
    _verify_checkpoint_files(campaign, checkpoint, authorization, checkout_proof)
    retry = amendment.get("retry_authorization") or {}
    if retry != {
        "from_attempt": 1,
        "to_attempt": 2,
        "run_indices": [item.run_index for item in authorization.interrupted],
        "completion_receipt_sha256": {
            str(item.run_index): item.completion_sha256
            for item in authorization.interrupted
        },
        "scope": "exact_hash_allowlist_only",
    }:
        raise ContinuationAmendmentError("retry authorization differs")
    denominator = amendment.get("denominator") or {}
    if (
        denominator.get("manifest_rows") != manifest["total_runs"]
        or denominator.get("added_rows") != 0
        or denominator.get("preserved_checkpoint_rows") != authorization.preserved_count
        or denominator.get("superseded_attempts_retained_for_audit")
        != len(authorization.interrupted)
        or denominator.get("counting_rule") != DENOMINATOR_COUNTING_RULE
    ):
        raise ContinuationAmendmentError("amendment denominator contract differs")
    scheduler = _read_object(directory / "continuation_scheduler_contract.json")
    validate_scheduler_contract(scheduler, manifest)
    return VerifiedAmendment(
        campaign=campaign,
        directory=directory,
        amendment=amendment,
        checkpoint=checkpoint,
        scheduler=scheduler,
        amendment_sha256=amendment_sha,
        scheduler_sha256=sidecars["continuation_scheduler_contract.json"],
    )


def _successor_checkpoint(
    campaign: Path,
    predecessor: VerifiedAmendment,
    checkout_proof: CheckoutProof,
) -> dict[str, Any]:
    """Freeze the quiescent frontier between continuation 001 and 002.

    The first continuation's checkpoint remains the authority for the original
    255 attempts.  This successor adds only attempts that were launched under
    that exact predecessor amendment, and only when they ended as a positively
    identified zero-step infrastructure failure with green cleanup and a fresh
    no-checkout proof.
    """
    campaign = campaign.resolve()
    manifest = _read_object(campaign / "campaign_manifest.json")
    base_items = [
        *predecessor.checkpoint["preserved_terminal_results"],
        *predecessor.checkpoint["interrupted_attempts"],
    ]
    base_keys = {
        (int(item["run_index"]), int(item["attempt"])) for item in base_items
    }
    manifest_sha256 = _sha_file(campaign / "campaign_manifest.json")
    completion_paths = sorted(
        (campaign / "completion_receipts").glob("*.complete.json")
    )
    launch_paths = sorted((campaign / "launch_receipts").glob("*.launch.json"))
    if len(completion_paths) != len(launch_paths):
        raise ContinuationAmendmentError(
            "successor frontier launch/completion counts differ"
        )

    added: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for completion_path in completion_paths:
        completion = _read_object(completion_path)
        index = completion.get("run_index")
        attempt = completion.get("attempt")
        if (
            type(index) is not int
            or type(attempt) is not int
            or index < 0
            or index >= len(manifest["runs"])
            or attempt <= 0
        ):
            raise ContinuationAmendmentError(
                "successor completion identity is malformed"
            )
        key = (index, attempt)
        if key in seen:
            raise ContinuationAmendmentError(
                "successor frontier contains a duplicate attempt"
            )
        seen.add(key)
        if key in base_keys:
            continue
        row = manifest["runs"][index]
        stem = completion_path.name.removesuffix(".complete.json")
        launch_path = campaign / "launch_receipts" / f"{stem}.launch.json"
        if launch_path.is_symlink() or not launch_path.is_file():
            raise ContinuationAmendmentError(
                "successor completion lacks its launch receipt"
            )
        launch = _read_object(launch_path)
        attestation = launch.get("continuation_attestation")
        expected_attestation = {
            "amendment_sha256": predecessor.amendment_sha256,
            "scheduler_sha256": predecessor.scheduler_sha256,
            "base_manifest_sha256": _sha_file(
                campaign / "campaign_manifest.json"
            ),
            "source_inventory_sha256": predecessor.amendment["hash_chain"][
                "new"
            ]["source_inventory_sha256"],
            "limit_contract_sha256": predecessor.amendment["hash_chain"][
                "new"
            ]["limit_contract_sha256"],
        }
        expected_nonce = (
            "eight_env_leaderboard/"
            f"{manifest['campaign_uuid']}/{manifest_sha256}/"
            f"{row['run_id']}/attempt_{attempt}/"
            f"continuation-{predecessor.amendment_sha256}"
        )
        classification = completion.get("classification")
        if (
            launch.get("run_index") != index
            or launch.get("run_id") != row.get("run_id")
            or launch.get("attempt") != attempt
            or completion.get("run_id") != row.get("run_id")
            or attestation != expected_attestation
            or launch.get("cache_nonce") != expected_nonce
            or classification
            != {
                "class": "infra",
                "code": "zero_step",
                "evidence": "steps=0; agent never received an observation",
                "outcome": "none",
                "steps": 0,
            }
            or not _green_cleanup(completion)
        ):
            raise ContinuationAmendmentError(
                "successor attempt is not an exact predecessor zero-step infra result"
            )
        out_dir = _attempt_out_dir(campaign, row["run_id"], attempt)
        summary = _read_object(out_dir / "summary.json")
        trajectory = _read_object(out_dir / "trajectory.json")
        if (
            summary.get("outcome") != "none"
            or summary.get("num_steps") != 0
            or trajectory.get("evaluation", {}).get("outcome") != "none"
            or trajectory.get("stats", {}).get("num_steps") != 0
            or trajectory.get("steps") != []
        ):
            raise ContinuationAmendmentError(
                "successor zero-step terminal artifacts disagree"
            )
        proof = dict(checkout_proof(out_dir, row))
        if (
            proof.get("confirmed_no_checkout") is not True
            or proof.get("hash_stable") is not True
            or proof.get("sidecars_absent") is not True
            or proof.get("error") is not None
        ):
            raise ContinuationAmendmentError(
                "successor attempt lacks an exact green checkout proof"
            )
        scratch_path_value = completion.get("scratch_cleanup_receipt")
        if not isinstance(scratch_path_value, str):
            raise ContinuationAmendmentError(
                "successor attempt lacks a scratch-cleanup receipt"
            )
        scratch_path = Path(scratch_path_value)
        if (
            scratch_path.is_symlink()
            or not scratch_path.is_file()
            or completion.get("scratch_cleanup_receipt_sha256")
            != _sha_file(scratch_path)
            or completion.get("scratch_cleanup") != _read_object(scratch_path)
            or completion["scratch_cleanup"].get("confirmed_absent") is not True
            or completion["scratch_cleanup"].get("worker_scope_green") is not True
            or completion["scratch_cleanup"].get("readable_holder_pids") != []
            or completion["scratch_cleanup"].get("status")
            not in {"removed", "reconciled_absent"}
            or completion["scratch_cleanup"].get("error") is not None
            or completion["worker_scope_cleanup"].get("cache_nonce_sha256")
            != _sha_bytes(expected_nonce.encode())
        ):
            raise ContinuationAmendmentError(
                "successor attempt scratch cleanup is not green"
            )
        added.append({
            "run_index": index,
            "run_id": row["run_id"],
            "attempt": attempt,
            "pid": launch.get("pid"),
            "finished_utc": completion.get("finished_utc"),
            "launch_receipt": _relative(campaign, launch_path),
            "launch_sha256": _sha_file(launch_path),
            "completion_receipt": _relative(campaign, completion_path),
            "completion_sha256": _sha_file(completion_path),
            "classification": classification,
            "no_checkout_proof": proof,
            "authoritative_artifacts": _receipt_artifact_hashes(
                out_dir, row
            ),
            "scratch_cleanup_receipt": _relative(campaign, scratch_path),
            "scratch_cleanup_sha256": _sha_file(scratch_path),
            "cache_nonce": expected_nonce,
            "predecessor_attestation": expected_attestation,
            "superseded_by_attempt": attempt + 1,
        })

    added_keys = {(item["run_index"], item["attempt"]) for item in added}
    if seen != base_keys | added_keys:
        raise ContinuationAmendmentError(
            "successor frontier contains an unaccounted attempt"
        )
    if not added:
        raise ContinuationAmendmentError(
            "successor amendment has no predecessor attempts to checkpoint"
        )
    if any(item["attempt"] != 1 for item in added):
        raise ContinuationAmendmentError(
            "successor recovery only authorizes attempt-1 infrastructure rows"
        )

    original_retry = list(predecessor.checkpoint["interrupted_attempts"])
    authorized = sorted(
        [*original_retry, *added], key=lambda item: item["run_index"]
    )
    if len({item["run_index"] for item in authorized}) != len(authorized):
        raise ContinuationAmendmentError(
            "successor retry authorization has duplicate rows"
        )
    return {
        "schema": SUCCESSOR_CHECKPOINT_SCHEMA,
        "campaign_uuid": manifest["campaign_uuid"],
        "manifest_sha256": _sha_file(campaign / "campaign_manifest.json"),
        "predecessor_amendment_sha256": predecessor.amendment_sha256,
        "counts": {
            "checkpoint_completion_receipts": len(completion_paths),
            "preserved_terminal_rows": len(
                predecessor.checkpoint["preserved_terminal_results"]
            ),
            "authorized_retry_rows": len(authorized),
            "predecessor_infrastructure_rows": len(added),
        },
        "preserved_terminal_results": list(
            predecessor.checkpoint["preserved_terminal_results"]
        ),
        "authorized_retry_attempts": authorized,
    }


def _verify_successor_checkpoint(
    campaign: Path,
    checkpoint: Mapping[str, Any],
    predecessor: VerifiedAmendment,
    checkout_proof: CheckoutProof | None,
) -> None:
    exact = {
        "schema",
        "campaign_uuid",
        "manifest_sha256",
        "predecessor_amendment_sha256",
        "counts",
        "preserved_terminal_results",
        "authorized_retry_attempts",
    }
    manifest = _read_object(campaign / "campaign_manifest.json")
    if not isinstance(checkpoint, dict) or set(checkpoint) != exact:
        raise ContinuationAmendmentError(
            "successor checkpoint schema is not exact"
        )
    if (
        checkpoint.get("schema") != SUCCESSOR_CHECKPOINT_SCHEMA
        or checkpoint.get("campaign_uuid") != manifest.get("campaign_uuid")
        or checkpoint.get("manifest_sha256")
        != _sha_file(campaign / "campaign_manifest.json")
        or checkpoint.get("predecessor_amendment_sha256")
        != predecessor.amendment_sha256
        or checkpoint.get("preserved_terminal_results")
        != predecessor.checkpoint["preserved_terminal_results"]
    ):
        raise ContinuationAmendmentError(
            "successor checkpoint predecessor binding differs"
        )
    preserved = checkpoint["preserved_terminal_results"]
    authorized = checkpoint.get("authorized_retry_attempts")
    if not isinstance(authorized, list):
        raise ContinuationAmendmentError(
            "successor retry checkpoint is malformed"
        )
    original = {
        item["run_index"]: item
        for item in predecessor.checkpoint["interrupted_attempts"]
    }
    counts = checkpoint.get("counts")
    added_count = len(authorized) - len(original)
    if counts != {
        "checkpoint_completion_receipts": len(preserved) + len(authorized),
        "preserved_terminal_rows": len(preserved),
        "authorized_retry_rows": len(authorized),
        "predecessor_infrastructure_rows": added_count,
    }:
        raise ContinuationAmendmentError(
            "successor checkpoint counts differ"
        )
    if len({item.get("run_index") for item in authorized}) != len(authorized):
        raise ContinuationAmendmentError(
            "successor retry rows are not unique"
        )
    for item in authorized:
        index = item.get("run_index")
        if type(index) is not int or index < 0 or index >= len(manifest["runs"]):
            raise ContinuationAmendmentError(
                "successor retry run index is malformed"
            )
        if index in original:
            if item != original[index]:
                raise ContinuationAmendmentError(
                    "successor changed original retry authority"
                )
            continue
        row = manifest["runs"][index]
        expected_nonce = (
            "eight_env_leaderboard/"
            f"{manifest['campaign_uuid']}/"
            f"{_sha_file(campaign / 'campaign_manifest.json')}/"
            f"{row['run_id']}/attempt_{item.get('attempt')}/"
            f"continuation-{predecessor.amendment_sha256}"
        )
        if (
            item.get("run_id") != row.get("run_id")
            or item.get("attempt") != 1
            or item.get("superseded_by_attempt") != 2
            or (item.get("classification") or {}).get("class") != "infra"
            or (item.get("classification") or {}).get("code") != "zero_step"
        ):
            raise ContinuationAmendmentError(
                "successor infrastructure retry identity differs"
            )
        for path_key, hash_key in (
            ("launch_receipt", "launch_sha256"),
            ("completion_receipt", "completion_sha256"),
            ("scratch_cleanup_receipt", "scratch_cleanup_sha256"),
        ):
            path = _safe_path(campaign, item[path_key])
            if _sha_file(path) != item.get(hash_key):
                raise ContinuationAmendmentError(
                    f"successor checkpoint file drifted: {item[path_key]}"
                )
        launch = _read_object(_safe_path(campaign, item["launch_receipt"]))
        if (
            launch.get("continuation_attestation")
            != item.get("predecessor_attestation")
            or launch.get("cache_nonce") != expected_nonce
            or item.get("cache_nonce") != expected_nonce
        ):
            raise ContinuationAmendmentError(
                "successor predecessor launch binding differs"
            )
        out_dir = _attempt_out_dir(campaign, item["run_id"], item["attempt"])
        artifacts = item.get("authoritative_artifacts")
        if not isinstance(artifacts, dict) or not artifacts:
            raise ContinuationAmendmentError(
                "successor authoritative artifacts are absent"
            )
        for name, expected_hash in artifacts.items():
            path = out_dir / name
            if path.is_symlink() or not path.is_file() or _sha_file(path) != expected_hash:
                raise ContinuationAmendmentError(
                    f"successor authoritative artifact drifted: {path}"
                )
        proof = item.get("no_checkout_proof")
        if not isinstance(proof, dict) or proof.get("confirmed_no_checkout") is not True:
            raise ContinuationAmendmentError(
                "successor stored checkout proof is not green"
            )
        if checkout_proof is not None and dict(checkout_proof(out_dir, row)) != proof:
            raise ContinuationAmendmentError(
                "fresh successor checkout proof differs"
            )


def create_successor_amendment(
    campaign: Path,
    *,
    continuation_source_inventory: Mapping[str, Any],
    continuation_limit_contract: Mapping[str, Any],
    continuation_scheduler_contract: Mapping[str, Any],
    checkout_proof: CheckoutProof,
    created_utc: str,
) -> VerifiedAmendment:
    """Publish create-only continuation 002 chained to verified 001."""
    campaign = campaign.resolve()
    _parse_utc(created_utc)
    predecessor = _load_static(
        campaign, authorization=None, checkout_proof=checkout_proof
    )
    manifest = _read_object(campaign / "campaign_manifest.json")
    _validate_continuation_payloads(
        continuation_source_inventory,
        continuation_limit_contract,
        continuation_scheduler_contract,
        manifest,
    )
    checkpoint = _successor_checkpoint(
        campaign, predecessor, checkout_proof
    )
    sidecars = {
        "successor_checkpoint.json": checkpoint,
        "continuation_source_inventory.json": dict(
            continuation_source_inventory
        ),
        "continuation_limit_contract.json": dict(
            continuation_limit_contract
        ),
        "continuation_scheduler_contract.json": dict(
            continuation_scheduler_contract
        ),
    }
    sidecar_hashes = {
        name: _sha_bytes(_json_bytes(payload))
        for name, payload in sidecars.items()
    }
    retry = checkpoint["authorized_retry_attempts"]
    amendment = {
        "schema": SUCCESSOR_AMENDMENT_SCHEMA,
        "amendment_id": SUCCESSOR_AMENDMENT_DIRECTORY,
        "created_utc": created_utc,
        "campaign_uuid": manifest["campaign_uuid"],
        "base_manifest": {
            "path": "campaign_manifest.json",
            "sha256": _sha_file(campaign / "campaign_manifest.json"),
            "total_runs": manifest["total_runs"],
            "frozen_artifacts": manifest["frozen_artifacts"],
        },
        "hash_chain": {
            "predecessor_amendment_sha256": predecessor.amendment_sha256,
            "old": dict(predecessor.amendment["hash_chain"]["new"]),
            "new": {
                "source_inventory_sha256": sidecar_hashes[
                    "continuation_source_inventory.json"
                ],
                "limit_contract_sha256": sidecar_hashes[
                    "continuation_limit_contract.json"
                ],
                "scheduler_contract_sha256": sidecar_hashes[
                    "continuation_scheduler_contract.json"
                ],
            },
        },
        "sidecars": sidecar_hashes,
        "retry_authorization": {
            "from_attempt": 1,
            "to_attempt": 2,
            "run_indices": [item["run_index"] for item in retry],
            "completion_receipt_sha256": {
                str(item["run_index"]): item["completion_sha256"]
                for item in retry
            },
            "scope": "exact_hash_allowlist_only",
        },
        "denominator": {
            "manifest_rows": manifest["total_runs"],
            "row_identity": "campaign_manifest.runs[].index",
            "preserved_checkpoint_rows": len(
                checkpoint["preserved_terminal_results"]
            ),
            "superseded_attempts_retained_for_audit": len(retry),
            "added_rows": 0,
            "counting_rule": DENOMINATOR_COUNTING_RULE,
        },
        "recovery": {
            "cause": "chromium_process_singleton_af_unix_path_overflow",
            "measured_behavior_changed": False,
            "scratch_path_only": True,
            "predecessor_infrastructure_rows": checkpoint["counts"][
                "predecessor_infrastructure_rows"
            ],
        },
    }
    directory = campaign / AMENDMENT_ROOT / SUCCESSOR_AMENDMENT_DIRECTORY
    directory.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if directory.parent.is_symlink():
        raise ContinuationAmendmentError(
            "amendment root must not be a symlink"
        )
    try:
        directory.mkdir(mode=0o755)
    except FileExistsError as exc:
        raise ContinuationAmendmentError(
            f"create-only amendment already exists: {directory}"
        ) from exc
    for name, payload in sidecars.items():
        _write_exclusive(directory / name, _json_bytes(payload))
    amendment_raw = _json_bytes(amendment)
    _write_exclusive(directory / "amendment.json", amendment_raw)
    amendment_sha = _sha_bytes(amendment_raw)
    _write_exclusive(
        directory / "amendment.sha256",
        f"{amendment_sha}  amendment.json\n".encode(),
    )
    return verify_amendment(
        campaign,
        continuation_source_inventory=continuation_source_inventory,
        continuation_limit_contract=continuation_limit_contract,
        continuation_scheduler_contract=continuation_scheduler_contract,
        checkout_proof=checkout_proof,
    )


def _load_successor_static(
    campaign: Path,
    *,
    checkout_proof: CheckoutProof | None,
) -> VerifiedAmendment:
    campaign = campaign.resolve()
    predecessor = _load_static(
        campaign, authorization=None, checkout_proof=checkout_proof
    )
    directory = campaign / AMENDMENT_ROOT / SUCCESSOR_AMENDMENT_DIRECTORY
    if directory.is_symlink() or not directory.is_dir():
        raise ContinuationAmendmentError("successor amendment is absent")
    entries = list(directory.iterdir())
    if (
        {path.name for path in entries} != SUCCESSOR_AMENDMENT_FILES
        or any(path.is_symlink() or not path.is_file() for path in entries)
    ):
        raise ContinuationAmendmentError(
            "successor amendment file inventory is not exact"
        )
    amendment_sha = _read_amendment_sha(directory)
    amendment = _read_object(directory / "amendment.json")
    exact_keys = {
        "schema",
        "amendment_id",
        "created_utc",
        "campaign_uuid",
        "base_manifest",
        "hash_chain",
        "sidecars",
        "retry_authorization",
        "denominator",
        "recovery",
    }
    manifest = _read_object(campaign / "campaign_manifest.json")
    if (
        set(amendment) != exact_keys
        or amendment.get("schema") != SUCCESSOR_AMENDMENT_SCHEMA
        or amendment.get("amendment_id") != SUCCESSOR_AMENDMENT_DIRECTORY
        or amendment.get("campaign_uuid") != manifest.get("campaign_uuid")
    ):
        raise ContinuationAmendmentError("successor amendment identity differs")
    _parse_utc(amendment.get("created_utc"))
    sidecars = amendment.get("sidecars")
    expected_sidecars = SUCCESSOR_AMENDMENT_FILES - {
        "amendment.json", "amendment.sha256"
    }
    if not isinstance(sidecars, dict) or set(sidecars) != expected_sidecars:
        raise ContinuationAmendmentError(
            "successor sidecar inventory is not exact"
        )
    for name, expected in sidecars.items():
        if not _is_sha256(expected) or _sha_file(directory / name) != expected:
            raise ContinuationAmendmentError(
                f"successor sidecar drifted: {name}"
            )
    base = amendment.get("base_manifest") or {}
    if base != {
        "path": "campaign_manifest.json",
        "sha256": _sha_file(campaign / "campaign_manifest.json"),
        "total_runs": manifest["total_runs"],
        "frozen_artifacts": manifest["frozen_artifacts"],
    }:
        raise ContinuationAmendmentError(
            "successor base-manifest binding differs"
        )
    chain = amendment.get("hash_chain") or {}
    if (
        chain.get("predecessor_amendment_sha256")
        != predecessor.amendment_sha256
        or chain.get("old") != predecessor.amendment["hash_chain"]["new"]
        or chain.get("new")
        != {
            "source_inventory_sha256": sidecars[
                "continuation_source_inventory.json"
            ],
            "limit_contract_sha256": sidecars[
                "continuation_limit_contract.json"
            ],
            "scheduler_contract_sha256": sidecars[
                "continuation_scheduler_contract.json"
            ],
        }
    ):
        raise ContinuationAmendmentError("successor hash chain differs")
    checkpoint = _read_object(directory / "successor_checkpoint.json")
    _verify_successor_checkpoint(
        campaign, checkpoint, predecessor, checkout_proof
    )
    retry_items = checkpoint["authorized_retry_attempts"]
    retry = amendment.get("retry_authorization")
    if retry != {
        "from_attempt": 1,
        "to_attempt": 2,
        "run_indices": [item["run_index"] for item in retry_items],
        "completion_receipt_sha256": {
            str(item["run_index"]): item["completion_sha256"]
            for item in retry_items
        },
        "scope": "exact_hash_allowlist_only",
    }:
        raise ContinuationAmendmentError(
            "successor retry authorization differs"
        )
    denominator = amendment.get("denominator") or {}
    if denominator != {
        "manifest_rows": manifest["total_runs"],
        "row_identity": "campaign_manifest.runs[].index",
        "preserved_checkpoint_rows": len(
            checkpoint["preserved_terminal_results"]
        ),
        "superseded_attempts_retained_for_audit": len(retry_items),
        "added_rows": 0,
        "counting_rule": DENOMINATOR_COUNTING_RULE,
    }:
        raise ContinuationAmendmentError(
            "successor denominator contract differs"
        )
    recovery = amendment.get("recovery")
    if recovery != {
        "cause": "chromium_process_singleton_af_unix_path_overflow",
        "measured_behavior_changed": False,
        "scratch_path_only": True,
        "predecessor_infrastructure_rows": checkpoint["counts"][
            "predecessor_infrastructure_rows"
        ],
    }:
        raise ContinuationAmendmentError("successor recovery scope differs")
    scheduler = _read_object(
        directory / "continuation_scheduler_contract.json"
    )
    validate_scheduler_contract(scheduler, manifest)
    return VerifiedAmendment(
        campaign=campaign,
        directory=directory,
        amendment=amendment,
        checkpoint=checkpoint,
        scheduler=scheduler,
        amendment_sha256=amendment_sha,
        scheduler_sha256=sidecars["continuation_scheduler_contract.json"],
    )


def _recovery_artifact_hashes(
    out_dir: Path, row: Mapping[str, Any]
) -> dict[str, str]:
    """Hash the immutable, non-terminal evidence for the C3 recovery row."""

    names = (
        "run.log",
        "environment_server.log",
        f"{row['env']}_{row['port']}.db",
    )
    result: dict[str, str] = {}
    for name in names:
        path = out_dir / name
        if path.is_symlink() or not path.is_file():
            raise ContinuationAmendmentError(
                f"recovery evidence artifact is absent: {path}"
            )
        result[name] = _sha_file(path)
    return result


def _postmortem_proof_is_green(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    no_checkout = value.get("no_checkout")
    absence = value.get("process_absence")
    quarantine = value.get("scratch_quarantine")
    return bool(
        value.get("schema") == POSTMORTEM_PROOF_SCHEMA
        and value.get("confirmed_recoverable") is True
        and value.get("operator_intervention") is True
        and value.get("benchmark_or_harness_changed") is False
        and value.get("inactivity_timeout_policy_created") is False
        and value.get("error") is None
        and isinstance(no_checkout, dict)
        and no_checkout.get("confirmed_no_checkout") is True
        and no_checkout.get("hash_stable") is True
        and no_checkout.get("sidecars_absent") is True
        and no_checkout.get("error") is None
        and isinstance(absence, dict)
        and absence.get("launch_identity_absent") is True
        and absence.get("exact_nonce_pids") == []
        and absence.get("worker_scope_snapshot") == []
        and absence.get("confirmed_absent") is True
        and absence.get("error") is None
        and isinstance(quarantine, dict)
        and quarantine.get("policy")
        == "retain_in_place_until_terminal_audit"
        and quarantine.get("readable_holder_pids") == []
        and quarantine.get("confirmed_quarantined") is True
        and quarantine.get("error") is None
    )


def _predecessor_launch_identity(
    campaign: Path,
    manifest: Mapping[str, Any],
    predecessor: VerifiedAmendment,
    row: Mapping[str, Any],
    attempt: int,
) -> tuple[dict[str, Any], str]:
    manifest_sha256 = _sha_file(campaign / "campaign_manifest.json")
    attestation = {
        "amendment_sha256": predecessor.amendment_sha256,
        "scheduler_sha256": predecessor.scheduler_sha256,
        "base_manifest_sha256": manifest_sha256,
        "source_inventory_sha256": predecessor.amendment["hash_chain"][
            "new"
        ]["source_inventory_sha256"],
        "limit_contract_sha256": predecessor.amendment["hash_chain"][
            "new"
        ]["limit_contract_sha256"],
    }
    nonce = (
        "eight_env_leaderboard/"
        f"{manifest['campaign_uuid']}/{manifest_sha256}/"
        f"{row['run_id']}/attempt_{attempt}/"
        f"continuation-{predecessor.amendment_sha256}"
    )
    return attestation, nonce


def _predecessor_active_manifest(
    manifest: Mapping[str, Any], predecessor: VerifiedAmendment
) -> dict[str, Any]:
    active = dict(manifest)
    scratch_policy = predecessor.scheduler.get("scratch_policy")
    if not isinstance(scratch_policy, dict):
        raise ContinuationAmendmentError(
            "recovery predecessor has no scratch policy"
        )
    active["scratch"] = dict(scratch_policy)
    return active


def _checkpoint_scratch_receipt(
    campaign: Path,
    completion: Mapping[str, Any],
    *,
    require_green: bool,
) -> tuple[str, str, dict[str, Any]]:
    value = completion.get("scratch_cleanup_receipt")
    if not isinstance(value, str):
        raise ContinuationAmendmentError(
            "frontier completion lacks a scratch-cleanup receipt"
        )
    path = Path(value)
    try:
        relative = _relative(campaign, path)
    except ContinuationAmendmentError:
        raise
    if path.is_symlink() or not path.is_file():
        raise ContinuationAmendmentError(
            "frontier scratch-cleanup receipt is absent or unsafe"
        )
    payload = _read_object(path)
    digest = _sha_file(path)
    if (
        completion.get("scratch_cleanup_receipt_sha256") != digest
        or completion.get("scratch_cleanup") != payload
    ):
        raise ContinuationAmendmentError(
            "frontier scratch-cleanup receipt binding differs"
        )
    if require_green and not (
        payload.get("status") in {"removed", "reconciled_absent"}
        and payload.get("worker_scope_green") is True
        and payload.get("readable_holder_pids") == []
        and payload.get("confirmed_absent") is True
        and payload.get("error") is None
    ):
        raise ContinuationAmendmentError(
            "preserved frontier scratch cleanup is not green"
        )
    return relative, digest, payload


def _recovery_checkpoint(
    campaign: Path,
    predecessor: VerifiedAmendment,
    postmortem_proof: PostmortemProof,
) -> dict[str, Any]:
    """Freeze the quiescent C2 frontier and one exact postmortem recovery.

    C3 is deliberately not a general scientific-invalid retry mechanism.  It
    accepts every C2 terminal as measured, carries C2's existing retry
    authority byte-for-byte, and recognizes exactly one additional immutable
    attempt whose postmortem proves that no transaction could have committed.
    """

    campaign = campaign.resolve()
    manifest = _read_object(campaign / "campaign_manifest.json")
    if predecessor.amendment.get("amendment_id") != SUCCESSOR_AMENDMENT_DIRECTORY:
        raise ContinuationAmendmentError("recovery predecessor is not continuation 002")

    predecessor_preserved = list(
        predecessor.checkpoint["preserved_terminal_results"]
    )
    predecessor_retry = list(
        predecessor.checkpoint["authorized_retry_attempts"]
    )
    for item in predecessor_retry:
        row = manifest["runs"][int(item["run_index"])]
        stem = f"{row['index']:04d}_{row['cell_name']}.attempt2"
        forbidden_successor = (
            campaign / "runs" / "attempt_2" / row["run_id"],
            campaign / "launch_receipts" / f"{stem}.launch.json",
            campaign / "completion_receipts" / f"{stem}.complete.json",
            campaign / "scratch_cleanup_receipts" / f"{stem}.scratch.json",
        )
        if any(
            path.exists() or path.is_symlink()
            for path in forbidden_successor
        ):
            raise ContinuationAmendmentError(
                "predecessor-authorized successor attempt predates recovery seal"
            )
    base_items = [*predecessor_preserved, *predecessor_retry]
    base_by_key = {
        (int(item["run_index"]), int(item["attempt"])): item
        for item in base_items
    }
    if len(base_by_key) != len(base_items):
        raise ContinuationAmendmentError(
            "recovery predecessor checkpoint has duplicate attempts"
        )

    completion_paths = sorted(
        (campaign / "completion_receipts").glob("*.complete.json")
    )
    launch_paths = sorted((campaign / "launch_receipts").glob("*.launch.json"))
    if len(completion_paths) != len(launch_paths):
        raise ContinuationAmendmentError(
            "recovery frontier launch/completion counts differ"
        )
    completion_stems = {
        path.name.removesuffix(".complete.json") for path in completion_paths
    }
    launch_stems = {
        path.name.removesuffix(".launch.json") for path in launch_paths
    }
    if completion_stems != launch_stems:
        raise ContinuationAmendmentError(
            "recovery frontier launch/completion identities differ"
        )

    added_preserved: list[dict[str, Any]] = []
    recovery_items: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    preserved_classes: Counter[str] = Counter(
        item["classification"]["class"] for item in predecessor_preserved
    )
    for completion_path in completion_paths:
        completion = _read_object(completion_path)
        index = completion.get("run_index")
        attempt = completion.get("attempt")
        if (
            type(index) is not int
            or type(attempt) is not int
            or index < 0
            or index >= len(manifest["runs"])
            or attempt <= 0
        ):
            raise ContinuationAmendmentError(
                "recovery frontier completion identity is malformed"
            )
        key = (index, attempt)
        if key in seen:
            raise ContinuationAmendmentError(
                "recovery frontier contains a duplicate attempt"
            )
        seen.add(key)
        if key in base_by_key:
            continue
        if attempt != 1:
            raise ContinuationAmendmentError(
                "recovery frontier contains an uncheckpointed successor attempt"
            )
        row = manifest["runs"][index]
        stem = completion_path.name.removesuffix(".complete.json")
        launch_path = campaign / "launch_receipts" / f"{stem}.launch.json"
        if launch_path.is_symlink() or not launch_path.is_file():
            raise ContinuationAmendmentError(
                "recovery frontier completion lacks its launch receipt"
            )
        launch = _read_object(launch_path)
        expected_attestation, expected_nonce = _predecessor_launch_identity(
            campaign, manifest, predecessor, row, attempt
        )
        out_dir = _attempt_out_dir(campaign, row["run_id"], attempt)
        if (
            launch.get("schema") != "agentarena.clone8-launch-receipt.v3"
            or launch.get("campaign_uuid") != manifest.get("campaign_uuid")
            or launch.get("manifest_sha256")
            != _sha_file(campaign / "campaign_manifest.json")
            or launch.get("run_index") != index
            or launch.get("run_id") != row.get("run_id")
            or launch.get("attempt") != attempt
            or Path(str(launch.get("out_dir", ""))).resolve()
            != out_dir.resolve()
            or launch.get("continuation_attestation") != expected_attestation
            or launch.get("cache_nonce") != expected_nonce
            or completion.get("schema")
            != "agentarena.clone8-completion-receipt.v3"
            or completion.get("run_id") != row.get("run_id")
            or completion.get("attempt") != attempt
            or completion.get("pid") != launch.get("pid")
        ):
            raise ContinuationAmendmentError(
                "recovery frontier is not exactly C2-attested"
            )
        common = {
            "run_index": index,
            "run_id": row["run_id"],
            "attempt": attempt,
            "pid": launch.get("pid"),
            "finished_utc": completion.get("finished_utc"),
            "launch_receipt": _relative(campaign, launch_path),
            "launch_sha256": _sha_file(launch_path),
            "completion_receipt": _relative(campaign, completion_path),
            "completion_sha256": _sha_file(completion_path),
            "classification": completion.get("classification"),
            "cache_nonce": expected_nonce,
            "predecessor_attestation": expected_attestation,
        }
        classification = completion.get("classification") or {}
        result_class = classification.get("class")
        if result_class in {"scored", "behavioral"}:
            if not _green_cleanup(completion):
                raise ContinuationAmendmentError(
                    "preserved C2 terminal lacks green worker cleanup"
                )
            scratch_relative, scratch_sha, _scratch = (
                _checkpoint_scratch_receipt(
                    campaign, completion, require_green=True
                )
            )
            preserved_classes[result_class] += 1
            added_preserved.append({
                **common,
                "authoritative_artifacts": _receipt_artifact_hashes(
                    out_dir, row
                ),
                "scratch_cleanup_receipt": scratch_relative,
                "scratch_cleanup_sha256": scratch_sha,
            })
            continue

        if index != RECOVERY_RUN_INDEX:
            raise ContinuationAmendmentError(
                "recovery frontier contains a non-measured result outside the exact recovery row"
            )
        if recovery_items:
            raise ContinuationAmendmentError(
                "recovery frontier contains more than one recovery row"
            )
        raw_cleanup = completion.get("worker_scope_cleanup") or {}
        scratch_relative, scratch_sha, scratch = _checkpoint_scratch_receipt(
            campaign, completion, require_green=False
        )
        if (
            completion.get("returncode") != -9
            or classification.get("class") != "scientific_invalid"
            or classification.get("code") != "worker_scope_cleanup_failed"
            or completion.get("no_checkout_proof") is not None
            or (out_dir / "summary.json").exists()
            or (out_dir / "trajectory.json").exists()
            or raw_cleanup.get("confirmed_empty") is not True
            or raw_cleanup.get("port_released") is not True
            or raw_cleanup.get("remaining_scope_pids") != []
            or not raw_cleanup.get("uncertified_scope_pids")
            or not isinstance(raw_cleanup.get("error"), str)
            or not raw_cleanup.get("error")
            or scratch.get("status") != "skipped_worker_scope_not_green"
            or scratch.get("worker_scope_green") is not False
            or scratch.get("confirmed_absent") is not False
            or not isinstance(scratch.get("error"), str)
            or not scratch.get("error")
        ):
            raise ContinuationAmendmentError(
                "exact recovery completion does not have the frozen failed-cleanup shape"
            )
        attempt_two = campaign / "runs" / "attempt_2" / row["run_id"]
        if any((
            attempt_two.exists(),
            (campaign / "launch_receipts" / (
                f"{row['index']:04d}_{row['cell_name']}.attempt2.launch.json"
            )).exists(),
            (campaign / "completion_receipts" / (
                f"{row['index']:04d}_{row['cell_name']}.attempt2.complete.json"
            )).exists(),
            (campaign / "scratch_cleanup_receipts" / (
                f"{row['index']:04d}_{row['cell_name']}.attempt2.scratch.json"
            )).exists(),
        )):
            raise ContinuationAmendmentError(
                "recovery attempt 2 predates its amendment"
            )
        proof = dict(
            postmortem_proof(
                campaign,
                _predecessor_active_manifest(manifest, predecessor),
                out_dir,
                row,
                launch,
                completion,
            )
        )
        if not _postmortem_proof_is_green(proof):
            raise ContinuationAmendmentError(
                "exact recovery row lacks a green postmortem proof"
            )
        recovery_items.append({
            **common,
            "authorization_basis": "postmortem_operator_sigkill",
            "authoritative_artifacts": _recovery_artifact_hashes(
                out_dir, row
            ),
            "scratch_cleanup_receipt": scratch_relative,
            "scratch_cleanup_sha256": scratch_sha,
            "postmortem_proof": proof,
            "terminal_artifacts_absent": [
                "summary.json", "trajectory.json"
            ],
            "superseded_by_attempt": 2,
        })

    if seen != base_by_key.keys() | {
        (item["run_index"], item["attempt"])
        for item in [*added_preserved, *recovery_items]
    }:
        raise ContinuationAmendmentError(
            "recovery frontier contains an unaccounted attempt"
        )
    if len(recovery_items) != 1:
        raise ContinuationAmendmentError(
            "recovery checkpoint requires exactly one recovery row"
        )
    preserved = sorted(
        [*predecessor_preserved, *added_preserved],
        key=lambda item: item["run_index"],
    )
    authorized = sorted(
        [*predecessor_retry, *recovery_items],
        key=lambda item: item["run_index"],
    )
    if len({item["run_index"] for item in [*preserved, *authorized]}) != (
        len(preserved) + len(authorized)
    ):
        raise ContinuationAmendmentError(
            "recovery checkpoint assigns a manifest row more than once"
        )
    return {
        "schema": RECOVERY_CHECKPOINT_SCHEMA,
        "campaign_uuid": manifest["campaign_uuid"],
        "manifest_sha256": _sha_file(campaign / "campaign_manifest.json"),
        "predecessor_amendment_sha256": predecessor.amendment_sha256,
        "counts": {
            "checkpoint_completion_receipts": len(completion_paths),
            "preserved_terminal_rows": len(preserved),
            "authorized_retry_rows": len(authorized),
            "predecessor_retry_rows": len(predecessor_retry),
            "recovery_rows": len(recovery_items),
            "preserved_classes": dict(sorted(preserved_classes.items())),
        },
        "preserved_terminal_results": preserved,
        "authorized_retry_attempts": authorized,
    }


def _verify_recovery_checkpoint(
    campaign: Path,
    checkpoint: Mapping[str, Any],
    predecessor: VerifiedAmendment,
    postmortem_proof: PostmortemProof | None,
) -> None:
    exact = {
        "schema",
        "campaign_uuid",
        "manifest_sha256",
        "predecessor_amendment_sha256",
        "counts",
        "preserved_terminal_results",
        "authorized_retry_attempts",
    }
    manifest = _read_object(campaign / "campaign_manifest.json")
    if not isinstance(checkpoint, dict) or set(checkpoint) != exact:
        raise ContinuationAmendmentError("recovery checkpoint schema is not exact")
    if (
        checkpoint.get("schema") != RECOVERY_CHECKPOINT_SCHEMA
        or checkpoint.get("campaign_uuid") != manifest.get("campaign_uuid")
        or checkpoint.get("manifest_sha256")
        != _sha_file(campaign / "campaign_manifest.json")
        or checkpoint.get("predecessor_amendment_sha256")
        != predecessor.amendment_sha256
    ):
        raise ContinuationAmendmentError(
            "recovery checkpoint predecessor binding differs"
        )
    preserved = checkpoint.get("preserved_terminal_results")
    authorized = checkpoint.get("authorized_retry_attempts")
    if not isinstance(preserved, list) or not isinstance(authorized, list):
        raise ContinuationAmendmentError("recovery checkpoint rows are malformed")
    predecessor_preserved = {
        item["run_index"]: item
        for item in predecessor.checkpoint["preserved_terminal_results"]
    }
    predecessor_retry = {
        item["run_index"]: item
        for item in predecessor.checkpoint["authorized_retry_attempts"]
    }
    preserved_classes = Counter()
    seen_indices: set[int] = set()
    for item in preserved:
        index = item.get("run_index")
        if type(index) is not int or index in seen_indices:
            raise ContinuationAmendmentError(
                "recovery preserved rows are not unique"
            )
        seen_indices.add(index)
        if index in predecessor_preserved:
            if item != predecessor_preserved[index]:
                raise ContinuationAmendmentError(
                    "recovery changed a predecessor preserved result"
                )
        else:
            _verify_recovery_frontier_item(
                campaign, manifest, predecessor, item,
                expected_classes={"scored", "behavioral"},
            )
        preserved_classes[item["classification"]["class"]] += 1
    if set(predecessor_preserved) - {
        item["run_index"] for item in preserved
    }:
        raise ContinuationAmendmentError(
            "recovery omitted a predecessor preserved result"
        )

    recovery: list[Mapping[str, Any]] = []
    for item in authorized:
        index = item.get("run_index")
        if type(index) is not int or index in seen_indices:
            raise ContinuationAmendmentError(
                "recovery authorized rows are not unique"
            )
        seen_indices.add(index)
        if index in predecessor_retry:
            if item != predecessor_retry[index]:
                raise ContinuationAmendmentError(
                    "recovery changed predecessor retry authority"
                )
            continue
        recovery.append(item)
        _verify_recovery_frontier_item(
            campaign, manifest, predecessor, item,
            expected_classes={"scientific_invalid"},
        )
        if (
            index != RECOVERY_RUN_INDEX
            or item.get("attempt") != 1
            or item.get("superseded_by_attempt") != 2
            or item.get("authorization_basis")
            != "postmortem_operator_sigkill"
            or (item.get("classification") or {}).get("code")
            != "worker_scope_cleanup_failed"
            or item.get("terminal_artifacts_absent")
            != ["summary.json", "trajectory.json"]
            or not _postmortem_proof_is_green(
                item.get("postmortem_proof")
            )
        ):
            raise ContinuationAmendmentError(
                "recovery retry identity differs"
            )
        row = manifest["runs"][index]
        out_dir = _attempt_out_dir(campaign, row["run_id"], 1)
        for name in item["terminal_artifacts_absent"]:
            if (out_dir / name).exists() or (out_dir / name).is_symlink():
                raise ContinuationAmendmentError(
                    "recovery terminal artifact appeared after checkpoint"
                )
        if postmortem_proof is not None:
            launch = _read_object(
                _safe_path(campaign, item["launch_receipt"])
            )
            completion = _read_object(
                _safe_path(campaign, item["completion_receipt"])
            )
            fresh = dict(postmortem_proof(
                campaign,
                _predecessor_active_manifest(manifest, predecessor),
                out_dir,
                row,
                launch,
                completion,
            ))
            if fresh != item.get("postmortem_proof"):
                raise ContinuationAmendmentError(
                    "fresh recovery postmortem proof differs"
                )

    if len(recovery) != 1 or set(predecessor_retry) - {
        item["run_index"] for item in authorized
    }:
        raise ContinuationAmendmentError(
            "recovery authorization scope differs"
        )
    counts = checkpoint.get("counts")
    if counts != {
        "checkpoint_completion_receipts": len(preserved) + len(authorized),
        "preserved_terminal_rows": len(preserved),
        "authorized_retry_rows": len(authorized),
        "predecessor_retry_rows": len(predecessor_retry),
        "recovery_rows": 1,
        "preserved_classes": dict(sorted(preserved_classes.items())),
    }:
        raise ContinuationAmendmentError("recovery checkpoint counts differ")


def _verify_recovery_frontier_item(
    campaign: Path,
    manifest: Mapping[str, Any],
    predecessor: VerifiedAmendment,
    item: Mapping[str, Any],
    *,
    expected_classes: set[str],
) -> None:
    index = item.get("run_index")
    attempt = item.get("attempt")
    if (
        type(index) is not int
        or index < 0
        or index >= len(manifest["runs"])
        or attempt != 1
    ):
        raise ContinuationAmendmentError(
            "recovery frontier item identity is malformed"
        )
    row = manifest["runs"][index]
    launch_path = _safe_path(campaign, item["launch_receipt"])
    completion_path = _safe_path(campaign, item["completion_receipt"])
    if (
        _sha_file(launch_path) != item.get("launch_sha256")
        or _sha_file(completion_path) != item.get("completion_sha256")
    ):
        raise ContinuationAmendmentError(
            "recovery frontier receipt drifted"
        )
    launch = _read_object(launch_path)
    completion = _read_object(completion_path)
    expected_attestation, expected_nonce = _predecessor_launch_identity(
        campaign, manifest, predecessor, row, 1
    )
    if (
        item.get("run_id") != row.get("run_id")
        or launch.get("schema") != "agentarena.clone8-launch-receipt.v3"
        or launch.get("campaign_uuid") != manifest.get("campaign_uuid")
        or launch.get("manifest_sha256")
        != _sha_file(campaign / "campaign_manifest.json")
        or launch.get("run_index") != index
        or launch.get("run_id") != row.get("run_id")
        or launch.get("attempt") != 1
        or launch.get("continuation_attestation") != expected_attestation
        or launch.get("cache_nonce") != expected_nonce
        or item.get("predecessor_attestation") != expected_attestation
        or item.get("cache_nonce") != expected_nonce
        or completion.get("run_index") != index
        or completion.get("run_id") != row.get("run_id")
        or completion.get("attempt") != 1
        or completion.get("pid") != launch.get("pid")
        or item.get("pid") != launch.get("pid")
        or item.get("finished_utc") != completion.get("finished_utc")
        or completion.get("classification") != item.get("classification")
        or (item.get("classification") or {}).get("class")
        not in expected_classes
    ):
        raise ContinuationAmendmentError(
            "recovery frontier receipt identity differs"
        )
    out_dir = _attempt_out_dir(campaign, row["run_id"], 1)
    artifacts = item.get("authoritative_artifacts")
    expected_artifacts = (
        _receipt_artifact_hashes(out_dir, row)
        if expected_classes == {"scored", "behavioral"}
        else _recovery_artifact_hashes(out_dir, row)
    )
    if artifacts != expected_artifacts:
        raise ContinuationAmendmentError(
            "recovery frontier artifact inventory differs"
        )
    for name, expected_hash in artifacts.items():
        path = out_dir / name
        if (
            path.is_symlink()
            or not path.is_file()
            or _sha_file(path) != expected_hash
        ):
            raise ContinuationAmendmentError(
                f"recovery frontier artifact drifted: {path}"
            )
    scratch_path = _safe_path(campaign, item["scratch_cleanup_receipt"])
    scratch = _read_object(scratch_path)
    if (
        _sha_file(scratch_path) != item.get("scratch_cleanup_sha256")
        or completion.get("scratch_cleanup_receipt_sha256")
        != item.get("scratch_cleanup_sha256")
        or Path(str(completion.get("scratch_cleanup_receipt", ""))).resolve()
        != scratch_path.resolve()
        or completion.get("scratch_cleanup") != scratch
    ):
        raise ContinuationAmendmentError(
            "recovery frontier scratch receipt drifted"
        )
    if expected_classes == {"scored", "behavioral"}:
        if (
            not _green_cleanup(completion)
            or scratch.get("status")
            not in {"removed", "reconciled_absent"}
            or scratch.get("worker_scope_green") is not True
            or scratch.get("readable_holder_pids") != []
            or scratch.get("confirmed_absent") is not True
            or scratch.get("error") is not None
        ):
            raise ContinuationAmendmentError(
                "recovery preserved frontier cleanup is not green"
            )
    else:
        cleanup = completion.get("worker_scope_cleanup") or {}
        classification = completion.get("classification") or {}
        if (
            completion.get("returncode") != -9
            or classification.get("class") != "scientific_invalid"
            or classification.get("code") != "worker_scope_cleanup_failed"
            or completion.get("no_checkout_proof") is not None
            or cleanup.get("confirmed_empty") is not True
            or cleanup.get("port_released") is not True
            or cleanup.get("remaining_scope_pids") != []
            or not cleanup.get("uncertified_scope_pids")
            or not isinstance(cleanup.get("error"), str)
            or not cleanup.get("error")
            or scratch.get("status") != "skipped_worker_scope_not_green"
            or scratch.get("worker_scope_green") is not False
            or scratch.get("confirmed_absent") is not False
            or not isinstance(scratch.get("error"), str)
            or not scratch.get("error")
        ):
            raise ContinuationAmendmentError(
                "recovery invalid frontier semantics differ"
            )


def create_recovery_amendment(
    campaign: Path,
    *,
    continuation_source_inventory: Mapping[str, Any],
    continuation_limit_contract: Mapping[str, Any],
    continuation_scheduler_contract: Mapping[str, Any],
    checkout_proof: CheckoutProof,
    postmortem_proof: PostmortemProof,
    created_utc: str,
) -> VerifiedAmendment:
    """Publish create-only continuation 003 chained to verified 002."""

    campaign = campaign.resolve()
    _parse_utc(created_utc)
    predecessor = _load_successor_static(
        campaign, checkout_proof=checkout_proof
    )
    manifest = _read_object(campaign / "campaign_manifest.json")
    _validate_continuation_payloads(
        continuation_source_inventory,
        continuation_limit_contract,
        continuation_scheduler_contract,
        manifest,
    )
    predecessor_limit = _read_object(
        predecessor.directory / "continuation_limit_contract.json"
    )
    predecessor_source = _read_object(
        predecessor.directory / "continuation_source_inventory.json"
    )
    predecessor_scheduler = _read_object(
        predecessor.directory / "continuation_scheduler_contract.json"
    )
    _validate_recovery_source_delta(
        predecessor_source, continuation_source_inventory
    )
    if continuation_limit_contract != predecessor_limit:
        raise ContinuationAmendmentError(
            "recovery continuation may not change the C2 limit contract"
        )
    if continuation_scheduler_contract != predecessor_scheduler:
        raise ContinuationAmendmentError(
            "recovery continuation may not change the C2 scheduler contract"
        )
    checkpoint = _recovery_checkpoint(
        campaign, predecessor, postmortem_proof
    )
    sidecars = {
        "recovery_checkpoint.json": checkpoint,
        "continuation_source_inventory.json": dict(
            continuation_source_inventory
        ),
        "continuation_limit_contract.json": dict(
            continuation_limit_contract
        ),
        "continuation_scheduler_contract.json": dict(
            continuation_scheduler_contract
        ),
    }
    sidecar_hashes = {
        name: _sha_bytes(_json_bytes(payload))
        for name, payload in sidecars.items()
    }
    retry = checkpoint["authorized_retry_attempts"]
    amendment = {
        "schema": RECOVERY_AMENDMENT_SCHEMA,
        "amendment_id": RECOVERY_AMENDMENT_DIRECTORY,
        "created_utc": created_utc,
        "campaign_uuid": manifest["campaign_uuid"],
        "base_manifest": {
            "path": "campaign_manifest.json",
            "sha256": _sha_file(campaign / "campaign_manifest.json"),
            "total_runs": manifest["total_runs"],
            "frozen_artifacts": manifest["frozen_artifacts"],
        },
        "hash_chain": {
            "predecessor_amendment_sha256": predecessor.amendment_sha256,
            "old": dict(predecessor.amendment["hash_chain"]["new"]),
            "new": {
                "source_inventory_sha256": sidecar_hashes[
                    "continuation_source_inventory.json"
                ],
                "limit_contract_sha256": sidecar_hashes[
                    "continuation_limit_contract.json"
                ],
                "scheduler_contract_sha256": sidecar_hashes[
                    "continuation_scheduler_contract.json"
                ],
            },
        },
        "sidecars": sidecar_hashes,
        "retry_authorization": {
            "from_attempt": 1,
            "to_attempt": 2,
            "run_indices": [item["run_index"] for item in retry],
            "completion_receipt_sha256": {
                str(item["run_index"]): item["completion_sha256"]
                for item in retry
            },
            "scope": "exact_hash_allowlist_only",
        },
        "denominator": {
            "manifest_rows": manifest["total_runs"],
            "row_identity": "campaign_manifest.runs[].index",
            "preserved_checkpoint_rows": len(
                checkpoint["preserved_terminal_results"]
            ),
            "superseded_attempts_retained_for_audit": len(retry),
            "added_rows": 0,
            "counting_rule": DENOMINATOR_COUNTING_RULE,
        },
        "recovery": {
            "cause": (
                "operator_initiated_sigkill_during_forensic_stall_investigation"
            ),
            "operator_intervention": True,
            "benchmark_or_harness_changed": False,
            "inactivity_timeout_policy_created": False,
            "measured_behavior_changed": False,
            "scratch_disposition": "retain_in_place_until_terminal_audit",
            "predecessor_retry_rows": checkpoint["counts"][
                "predecessor_retry_rows"
            ],
            "recovery_run_indices": [RECOVERY_RUN_INDEX],
        },
    }
    directory = campaign / AMENDMENT_ROOT / RECOVERY_AMENDMENT_DIRECTORY
    directory.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if directory.parent.is_symlink():
        raise ContinuationAmendmentError("amendment root must not be a symlink")
    try:
        directory.mkdir(mode=0o755)
    except FileExistsError as exc:
        raise ContinuationAmendmentError(
            f"create-only amendment already exists: {directory}"
        ) from exc
    for name, payload in sidecars.items():
        _write_exclusive(directory / name, _json_bytes(payload))
    amendment_raw = _json_bytes(amendment)
    _write_exclusive(directory / "amendment.json", amendment_raw)
    amendment_sha = _sha_bytes(amendment_raw)
    _write_exclusive(
        directory / "amendment.sha256",
        f"{amendment_sha}  amendment.json\n".encode(),
    )
    return verify_amendment(
        campaign,
        continuation_source_inventory=continuation_source_inventory,
        continuation_limit_contract=continuation_limit_contract,
        continuation_scheduler_contract=continuation_scheduler_contract,
        checkout_proof=checkout_proof,
        postmortem_proof=postmortem_proof,
    )


def _load_recovery_static(
    campaign: Path,
    *,
    checkout_proof: CheckoutProof | None,
    postmortem_proof: PostmortemProof | None,
) -> VerifiedAmendment:
    campaign = campaign.resolve()
    predecessor = _load_successor_static(
        campaign, checkout_proof=checkout_proof
    )
    directory = campaign / AMENDMENT_ROOT / RECOVERY_AMENDMENT_DIRECTORY
    if directory.is_symlink() or not directory.is_dir():
        raise ContinuationAmendmentError("recovery amendment is absent")
    entries = list(directory.iterdir())
    if (
        {path.name for path in entries} != RECOVERY_AMENDMENT_FILES
        or any(path.is_symlink() or not path.is_file() for path in entries)
    ):
        raise ContinuationAmendmentError(
            "recovery amendment file inventory is not exact"
        )
    amendment_sha = _read_amendment_sha(directory)
    amendment = _read_object(directory / "amendment.json")
    exact_keys = {
        "schema", "amendment_id", "created_utc", "campaign_uuid",
        "base_manifest", "hash_chain", "sidecars", "retry_authorization",
        "denominator", "recovery",
    }
    manifest = _read_object(campaign / "campaign_manifest.json")
    if (
        set(amendment) != exact_keys
        or amendment.get("schema") != RECOVERY_AMENDMENT_SCHEMA
        or amendment.get("amendment_id") != RECOVERY_AMENDMENT_DIRECTORY
        or amendment.get("campaign_uuid") != manifest.get("campaign_uuid")
    ):
        raise ContinuationAmendmentError("recovery amendment identity differs")
    _parse_utc(amendment.get("created_utc"))
    sidecars = amendment.get("sidecars")
    expected_sidecars = RECOVERY_AMENDMENT_FILES - {
        "amendment.json", "amendment.sha256"
    }
    if not isinstance(sidecars, dict) or set(sidecars) != expected_sidecars:
        raise ContinuationAmendmentError(
            "recovery sidecar inventory is not exact"
        )
    for name, expected in sidecars.items():
        if not _is_sha256(expected) or _sha_file(directory / name) != expected:
            raise ContinuationAmendmentError(
                f"recovery sidecar drifted: {name}"
            )
    base = amendment.get("base_manifest") or {}
    if base != {
        "path": "campaign_manifest.json",
        "sha256": _sha_file(campaign / "campaign_manifest.json"),
        "total_runs": manifest["total_runs"],
        "frozen_artifacts": manifest["frozen_artifacts"],
    }:
        raise ContinuationAmendmentError(
            "recovery base-manifest binding differs"
        )
    chain = amendment.get("hash_chain") or {}
    if (
        chain.get("predecessor_amendment_sha256")
        != predecessor.amendment_sha256
        or chain.get("old") != predecessor.amendment["hash_chain"]["new"]
        or chain.get("new") != {
            "source_inventory_sha256": sidecars[
                "continuation_source_inventory.json"
            ],
            "limit_contract_sha256": sidecars[
                "continuation_limit_contract.json"
            ],
            "scheduler_contract_sha256": sidecars[
                "continuation_scheduler_contract.json"
            ],
        }
    ):
        raise ContinuationAmendmentError("recovery hash chain differs")
    if (
        sidecars["continuation_limit_contract.json"]
        != predecessor.amendment["hash_chain"]["new"][
            "limit_contract_sha256"
        ]
        or sidecars["continuation_scheduler_contract.json"]
        != predecessor.amendment["hash_chain"]["new"][
            "scheduler_contract_sha256"
        ]
        or _read_object(directory / "continuation_limit_contract.json")
        != _read_object(
            predecessor.directory / "continuation_limit_contract.json"
        )
        or _read_object(directory / "continuation_scheduler_contract.json")
        != _read_object(
            predecessor.directory / "continuation_scheduler_contract.json"
        )
    ):
        raise ContinuationAmendmentError(
            "recovery changed the predecessor limit or scheduler contract"
        )
    _validate_recovery_source_delta(
        _read_object(
            predecessor.directory / "continuation_source_inventory.json"
        ),
        _read_object(directory / "continuation_source_inventory.json"),
    )
    checkpoint = _read_object(directory / "recovery_checkpoint.json")
    _verify_recovery_checkpoint(
        campaign, checkpoint, predecessor, postmortem_proof
    )
    retry_items = checkpoint["authorized_retry_attempts"]
    if amendment.get("retry_authorization") != {
        "from_attempt": 1,
        "to_attempt": 2,
        "run_indices": [item["run_index"] for item in retry_items],
        "completion_receipt_sha256": {
            str(item["run_index"]): item["completion_sha256"]
            for item in retry_items
        },
        "scope": "exact_hash_allowlist_only",
    }:
        raise ContinuationAmendmentError(
            "recovery retry authorization differs"
        )
    if amendment.get("denominator") != {
        "manifest_rows": manifest["total_runs"],
        "row_identity": "campaign_manifest.runs[].index",
        "preserved_checkpoint_rows": len(
            checkpoint["preserved_terminal_results"]
        ),
        "superseded_attempts_retained_for_audit": len(retry_items),
        "added_rows": 0,
        "counting_rule": DENOMINATOR_COUNTING_RULE,
    }:
        raise ContinuationAmendmentError(
            "recovery denominator contract differs"
        )
    if amendment.get("recovery") != {
        "cause": (
            "operator_initiated_sigkill_during_forensic_stall_investigation"
        ),
        "operator_intervention": True,
        "benchmark_or_harness_changed": False,
        "inactivity_timeout_policy_created": False,
        "measured_behavior_changed": False,
        "scratch_disposition": "retain_in_place_until_terminal_audit",
        "predecessor_retry_rows": checkpoint["counts"][
            "predecessor_retry_rows"
        ],
        "recovery_run_indices": [RECOVERY_RUN_INDEX],
    }:
        raise ContinuationAmendmentError("recovery scope differs")
    scheduler = _read_object(
        directory / "continuation_scheduler_contract.json"
    )
    validate_scheduler_contract(scheduler, manifest)
    return VerifiedAmendment(
        campaign=campaign,
        directory=directory,
        amendment=amendment,
        checkpoint=checkpoint,
        scheduler=scheduler,
        amendment_sha256=amendment_sha,
        scheduler_sha256=sidecars["continuation_scheduler_contract.json"],
    )


def _correction_artifact_item(
    campaign: Path,
    manifest: Mapping[str, Any],
    row: Mapping[str, Any],
    completion_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Path]:
    """Resolve one base attempt into a complete immutable hash record."""

    completion = _read_object(completion_path)
    index = row["index"]
    attempt = completion.get("attempt")
    if (
        completion.get("schema") != "agentarena.clone8-completion-receipt.v3"
        or completion.get("run_index") != index
        or completion.get("run_id") != row.get("run_id")
        or attempt != 1
    ):
        raise ContinuationAmendmentError(
            "verifier-correction completion identity differs from manifest"
        )
    stem = completion_path.name.removesuffix(".complete.json")
    launch_path = campaign / "launch_receipts" / f"{stem}.launch.json"
    if launch_path.is_symlink() or not launch_path.is_file():
        raise ContinuationAmendmentError(
            "verifier-correction completion lacks its launch receipt"
        )
    launch = _read_object(launch_path)
    manifest_sha256 = _sha_file(campaign / "campaign_manifest.json")
    out_dir = _attempt_out_dir(campaign, row["run_id"], attempt)
    if (
        launch.get("schema") != "agentarena.clone8-launch-receipt.v3"
        or launch.get("campaign_uuid") != manifest.get("campaign_uuid")
        or launch.get("manifest_sha256") != manifest_sha256
        or launch.get("run_index") != index
        or launch.get("run_id") != row.get("run_id")
        or launch.get("attempt") != attempt
        or Path(str(launch.get("out_dir", ""))).resolve()
        != out_dir.resolve()
        or completion.get("pid") != launch.get("pid")
        or not _green_cleanup(completion)
    ):
        raise ContinuationAmendmentError(
            "verifier-correction launch/completion binding is not green"
        )
    scratch_relative = None
    scratch_sha256 = None
    scratch_value = completion.get("scratch_cleanup_receipt")
    if scratch_value is not None:
        if not isinstance(scratch_value, str):
            raise ContinuationAmendmentError(
                "verifier-correction scratch receipt path is malformed"
            )
        scratch_path = Path(scratch_value)
        if not scratch_path.is_absolute():
            scratch_path = campaign / scratch_path
        if scratch_path.is_symlink() or not scratch_path.is_file():
            raise ContinuationAmendmentError(
                "verifier-correction scratch receipt is absent"
            )
        scratch_relative = _relative(campaign, scratch_path)
        scratch_sha256 = _sha_file(scratch_path)
        if completion.get("scratch_cleanup_receipt_sha256") != scratch_sha256:
            raise ContinuationAmendmentError(
                "verifier-correction scratch receipt hash differs"
            )
    item = {
        "run_index": index,
        "run_id": row["run_id"],
        "env": row.get("env"),
        "attempt": attempt,
        "launch_receipt": _relative(campaign, launch_path),
        "launch_sha256": _sha_file(launch_path),
        "completion_receipt": _relative(campaign, completion_path),
        "completion_sha256": _sha_file(completion_path),
        "scratch_cleanup_receipt": scratch_relative,
        "scratch_cleanup_sha256": scratch_sha256,
        "classification": completion.get("classification"),
        "authoritative_artifacts": _receipt_artifact_hashes(out_dir, row),
    }
    return item, launch, completion, out_dir


def _verify_correction_item_hashes(
    campaign: Path, item: Mapping[str, Any]
) -> None:
    for path_key, hash_key in (
        ("launch_receipt", "launch_sha256"),
        ("completion_receipt", "completion_sha256"),
    ):
        path = _safe_path(campaign, str(item.get(path_key, "")))
        if _sha_file(path) != item.get(hash_key):
            raise ContinuationAmendmentError(
                f"verifier-correction checkpoint file drifted: {path_key}"
            )
    scratch_relative = item.get("scratch_cleanup_receipt")
    scratch_sha256 = item.get("scratch_cleanup_sha256")
    if (scratch_relative is None) != (scratch_sha256 is None):
        raise ContinuationAmendmentError(
            "verifier-correction scratch hash binding is malformed"
        )
    if scratch_relative is not None:
        path = _safe_path(campaign, str(scratch_relative))
        if _sha_file(path) != scratch_sha256:
            raise ContinuationAmendmentError(
                "verifier-correction scratch receipt drifted"
            )
    out_dir = _attempt_out_dir(
        campaign, str(item.get("run_id", "")), int(item.get("attempt", 0))
    )
    artifacts = item.get("authoritative_artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ContinuationAmendmentError(
            "verifier-correction artifact inventory is absent"
        )
    for name, expected in artifacts.items():
        path = out_dir / name
        if path.is_symlink() or not path.is_file() or _sha_file(path) != expected:
            raise ContinuationAmendmentError(
                f"verifier-correction result artifact drifted: {path}"
            )


def _validate_correction_proof(value: Any) -> None:
    exact = {
        "schema",
        "verifier",
        "read_only_replay",
        "corrected_classification",
        "derived_summary",
    }
    if not isinstance(value, dict) or set(value) != exact:
        raise ContinuationAmendmentError(
            "verifier-correction proof schema is not exact"
        )
    classification = value.get("corrected_classification") or {}
    derived = value.get("derived_summary") or {}
    if (
        value.get("schema") != VERIFIER_CORRECTION_PROOF_SCHEMA
        or value.get("verifier") != "current_frozen_semantic_classifier"
        or value.get("read_only_replay") is not True
        or classification.get("class") != "scored"
        or classification.get("code") != "measured_transaction"
        or set(derived)
        != {
            "outcome",
            "chosen",
            "chosen_label",
            "preservation",
            "preservation_strict",
            "strict_binary",
            "literal_hero",
            "hero_identity",
        }
        or derived.get("outcome") != classification.get("outcome")
        or isinstance(derived.get("preservation"), bool)
        or not isinstance(derived.get("preservation"), (int, float))
        or not 0 <= float(derived["preservation"]) <= 1
        or isinstance(derived.get("preservation_strict"), bool)
        or not isinstance(derived.get("preservation_strict"), (int, float))
        or not 0 <= float(derived["preservation_strict"]) <= 1
        or derived.get("strict_binary")
        != int(float(derived.get("preservation_strict")) == 1.0)
        or derived.get("literal_hero") not in {0, 1}
    ):
        raise ContinuationAmendmentError(
            "verifier-correction proof is not a scored semantic replay"
        )


def _collect_verifier_correction_checkpoint(
    campaign: Path,
    manifest: Mapping[str, Any],
    proof_callback: VerifierCorrectionProof,
) -> dict[str, Any]:
    """Freeze a drained false-positive frontier without rewriting a byte."""

    status_path = _safe_path(campaign, "status.json")
    status = _read_object(status_path)
    completion_paths = sorted(
        (campaign / "completion_receipts").glob("*.complete.json")
    )
    launch_paths = sorted((campaign / "launch_receipts").glob("*.launch.json"))
    if (
        status.get("state") != "protocol_invalid"
        or status.get("running") != []
        or status.get("running_count") != 0
        or status.get("pending_refill") != 0
        or status.get("total_runs") != manifest.get("total_runs")
        or status.get("final_count") != len(completion_paths)
        or len(completion_paths) != len(launch_paths)
        or not completion_paths
    ):
        raise ContinuationAmendmentError(
            "verifier correction requires the exact drained protocol-invalid frontier"
        )
    rows = manifest["runs"]
    preserved: list[dict[str, Any]] = []
    corrections: list[dict[str, Any]] = []
    classes: Counter[str] = Counter()
    seen: set[int] = set()
    for completion_path in completion_paths:
        if completion_path.is_symlink() or not completion_path.is_file():
            raise ContinuationAmendmentError(
                "verifier-correction completion receipt is unsafe"
            )
        raw = _read_object(completion_path)
        index = raw.get("run_index")
        if (
            type(index) is not int
            or index < 0
            or index >= len(rows)
            or index in seen
        ):
            raise ContinuationAmendmentError(
                "verifier-correction completion rows are not unique"
            )
        seen.add(index)
        row = rows[index]
        item, launch, completion, out_dir = _correction_artifact_item(
            campaign, manifest, row, completion_path
        )
        classification = completion.get("classification") or {}
        result_class = classification.get("class")
        classes[result_class] += 1
        if result_class in {"scored", "behavioral"}:
            preserved.append(item)
            continue
        if (
            result_class != "scientific_invalid"
            or classification.get("code") != "invalid_score_backfill"
            or row.get("env") != "airbnb"
        ):
            raise ContinuationAmendmentError(
                "frontier has a scientific-invalid result outside the exact "
                "Airbnb invalid_score_backfill correction class"
            )
        before = dict(item["authoritative_artifacts"])
        proof = dict(
            proof_callback(
                campaign, manifest, row, 1, out_dir, launch, completion
            )
        )
        _validate_correction_proof(proof)
        _verify_correction_item_hashes(campaign, item)
        if _receipt_artifact_hashes(out_dir, row) != before:
            raise ContinuationAmendmentError(
                "verifier-correction replay changed a result artifact"
            )
        corrected = proof["corrected_classification"]
        if corrected.get("outcome") != completion.get("outcome"):
            raise ContinuationAmendmentError(
                "corrected outcome differs from immutable completion"
            )
        correction = {
            **item,
            "recorded_classification": classification,
            "corrected_classification": corrected,
            "verifier_proof": proof,
            "promotion_basis": "fresh_read_only_semantic_replay",
        }
        corrections.append(correction)
        preserved.append(item)
    if not corrections:
        raise ContinuationAmendmentError(
            "frontier contains no verifier false positive to correct"
        )
    if status.get("final_classes") != dict(classes):
        raise ContinuationAmendmentError(
            "frontier status class inventory differs from completion receipts"
        )
    if status.get("pending_primary") + len(preserved) != manifest["total_runs"]:
        raise ContinuationAmendmentError(
            "frontier pending/completed rows differ from manifest denominator"
        )
    return {
        "schema": VERIFIER_CORRECTION_CHECKPOINT_SCHEMA,
        "campaign_uuid": manifest["campaign_uuid"],
        "manifest_sha256": _sha_file(campaign / "campaign_manifest.json"),
        "frontier_status_snapshot": status,
        "frontier_status_file_sha256": _sha_file(status_path),
        "counts": {
            "checkpoint_completion_receipts": len(preserved),
            "preserved_terminal_rows": len(preserved),
            "corrected_rows": len(corrections),
            "recorded_classes": dict(sorted(classes.items())),
        },
        "preserved_terminal_results": sorted(
            preserved, key=lambda value: value["run_index"]
        ),
        "verifier_corrections": sorted(
            corrections, key=lambda value: value["run_index"]
        ),
    }


def _verify_verifier_correction_checkpoint(
    campaign: Path,
    checkpoint: Mapping[str, Any],
    proof_callback: VerifierCorrectionProof | None,
) -> None:
    exact = {
        "schema",
        "campaign_uuid",
        "manifest_sha256",
        "frontier_status_snapshot",
        "frontier_status_file_sha256",
        "counts",
        "preserved_terminal_results",
        "verifier_corrections",
    }
    manifest = _read_object(campaign / "campaign_manifest.json")
    if (
        not isinstance(checkpoint, dict)
        or set(checkpoint) != exact
        or checkpoint.get("schema") != VERIFIER_CORRECTION_CHECKPOINT_SCHEMA
        or checkpoint.get("campaign_uuid") != manifest.get("campaign_uuid")
        or checkpoint.get("manifest_sha256")
        != _sha_file(campaign / "campaign_manifest.json")
        or not _is_sha256(checkpoint.get("frontier_status_file_sha256"))
    ):
        raise ContinuationAmendmentError(
            "verifier-correction checkpoint identity differs"
        )
    status = checkpoint.get("frontier_status_snapshot") or {}
    preserved = checkpoint.get("preserved_terminal_results")
    corrections = checkpoint.get("verifier_corrections")
    if not isinstance(preserved, list) or not isinstance(corrections, list):
        raise ContinuationAmendmentError(
            "verifier-correction checkpoint inventories are malformed"
        )
    classes = Counter()
    preserved_by_index: dict[int, Mapping[str, Any]] = {}
    for item in preserved:
        index = item.get("run_index") if isinstance(item, dict) else None
        if (
            type(index) is not int
            or index < 0
            or index >= len(manifest["runs"])
            or index in preserved_by_index
        ):
            raise ContinuationAmendmentError(
                "verifier-correction preserved rows are not unique"
            )
        row = manifest["runs"][index]
        if (
            item.get("run_id") != row.get("run_id")
            or item.get("env") != row.get("env")
            or item.get("attempt") != 1
        ):
            raise ContinuationAmendmentError(
                "verifier-correction preserved row identity differs"
            )
        _verify_correction_item_hashes(campaign, item)
        completion = _read_object(
            _safe_path(campaign, item["completion_receipt"])
        )
        if completion.get("classification") != item.get("classification"):
            raise ContinuationAmendmentError(
                "verifier-correction recorded classification drifted"
            )
        classes[(item.get("classification") or {}).get("class")] += 1
        preserved_by_index[index] = item
    correction_by_index: dict[int, Mapping[str, Any]] = {}
    for correction in corrections:
        index = correction.get("run_index") if isinstance(correction, dict) else None
        base = preserved_by_index.get(index)
        if base is None or index in correction_by_index:
            raise ContinuationAmendmentError(
                "verifier-correction promotion rows are not a unique subset"
            )
        common_keys = set(base)
        if any(correction.get(key) != base.get(key) for key in common_keys):
            raise ContinuationAmendmentError(
                "verifier-correction promotion changed its preserved hash record"
            )
        recorded = correction.get("recorded_classification") or {}
        corrected = correction.get("corrected_classification") or {}
        proof = correction.get("verifier_proof")
        _validate_correction_proof(proof)
        if (
            correction.get("promotion_basis")
            != "fresh_read_only_semantic_replay"
            or base.get("env") != "airbnb"
            or recorded != base.get("classification")
            or recorded.get("class") != "scientific_invalid"
            or recorded.get("code") != "invalid_score_backfill"
            or corrected != proof.get("corrected_classification")
        ):
            raise ContinuationAmendmentError(
                "verifier-correction promotion semantics differ"
            )
        if proof_callback is not None:
            row = manifest["runs"][index]
            out_dir = _attempt_out_dir(campaign, row["run_id"], 1)
            launch = _read_object(_safe_path(campaign, base["launch_receipt"]))
            completion = _read_object(
                _safe_path(campaign, base["completion_receipt"])
            )
            before = dict(base["authoritative_artifacts"])
            fresh = dict(
                proof_callback(
                    campaign, manifest, row, 1, out_dir, launch, completion
                )
            )
            if fresh != proof or _receipt_artifact_hashes(out_dir, row) != before:
                raise ContinuationAmendmentError(
                    "fresh verifier-correction replay differs from checkpoint"
                )
            _verify_correction_item_hashes(campaign, base)
        correction_by_index[index] = correction
    invalid_indices = {
        index
        for index, item in preserved_by_index.items()
        if (item.get("classification") or {}).get("class")
        == "scientific_invalid"
    }
    if invalid_indices != set(correction_by_index):
        raise ContinuationAmendmentError(
            "verifier correction does not cover every scientific-invalid row"
        )
    expected_counts = {
        "checkpoint_completion_receipts": len(preserved),
        "preserved_terminal_rows": len(preserved),
        "corrected_rows": len(corrections),
        "recorded_classes": dict(sorted(classes.items())),
    }
    if (
        checkpoint.get("counts") != expected_counts
        or status.get("state") != "protocol_invalid"
        or status.get("running") != []
        or status.get("running_count") != 0
        or status.get("pending_refill") != 0
        or status.get("final_count") != len(preserved)
        or status.get("final_classes") != dict(classes)
        or status.get("pending_primary") + len(preserved)
        != manifest["total_runs"]
    ):
        raise ContinuationAmendmentError(
            "verifier-correction checkpoint frontier differs"
        )


def create_verifier_correction_amendment(
    campaign: Path,
    *,
    continuation_source_inventory: Mapping[str, Any],
    continuation_limit_contract: Mapping[str, Any],
    continuation_scheduler_contract: Mapping[str, Any],
    verifier_correction_proof: VerifierCorrectionProof,
    created_utc: str,
) -> VerifiedAmendment:
    """Publish a no-rerun correction for exact verifier false positives."""

    campaign = campaign.resolve()
    _parse_utc(created_utc)
    manifest, old_limit, manifest_sha256 = _load_base_unbound(campaign)
    _validate_continuation_payloads(
        continuation_source_inventory,
        continuation_limit_contract,
        continuation_scheduler_contract,
        manifest,
    )
    old_source = _read_object(
        _safe_path(campaign, "frozen_inputs/code_inventory.json")
    )
    changed_paths = _verifier_correction_source_delta(
        old_source, continuation_source_inventory
    )
    if continuation_limit_contract != old_limit:
        raise ContinuationAmendmentError(
            "verifier correction may not change the base limit contract"
        )
    if continuation_scheduler_contract != _unchanged_correction_scheduler(
        manifest, old_limit
    ):
        raise ContinuationAmendmentError(
            "verifier correction may not change the base scheduler contract"
        )
    checkpoint = _collect_verifier_correction_checkpoint(
        campaign, manifest, verifier_correction_proof
    )
    base_scheduler = base_scheduler_contract(manifest, old_limit)
    sidecars = {
        "correction_checkpoint.json": checkpoint,
        "base_scheduler_contract.json": base_scheduler,
        "continuation_source_inventory.json": dict(
            continuation_source_inventory
        ),
        "continuation_limit_contract.json": dict(
            continuation_limit_contract
        ),
        "continuation_scheduler_contract.json": dict(
            continuation_scheduler_contract
        ),
    }
    sidecar_hashes = {
        name: _sha_bytes(_json_bytes(payload))
        for name, payload in sidecars.items()
    }
    corrections = checkpoint["verifier_corrections"]
    amendment = {
        "schema": VERIFIER_CORRECTION_AMENDMENT_SCHEMA,
        "amendment_id": VERIFIER_CORRECTION_AMENDMENT_DIRECTORY,
        "created_utc": created_utc,
        "campaign_uuid": manifest["campaign_uuid"],
        "base_manifest": {
            "path": "campaign_manifest.json",
            "sha256": manifest_sha256,
            "total_runs": manifest["total_runs"],
            "frozen_artifacts": manifest["frozen_artifacts"],
        },
        "hash_chain": {
            "predecessor_amendment_sha256": None,
            "old": {
                "manifest_sha256": manifest_sha256,
                "source_inventory_sha256": manifest["frozen_artifacts"][
                    "code_inventory.json"
                ],
                "limit_contract_sha256": manifest["frozen_artifacts"][
                    "limit_contract.json"
                ],
                "scheduler_contract_sha256": sidecar_hashes[
                    "base_scheduler_contract.json"
                ],
            },
            "new": {
                "source_inventory_sha256": sidecar_hashes[
                    "continuation_source_inventory.json"
                ],
                "limit_contract_sha256": sidecar_hashes[
                    "continuation_limit_contract.json"
                ],
                "scheduler_contract_sha256": sidecar_hashes[
                    "continuation_scheduler_contract.json"
                ],
            },
        },
        "sidecars": sidecar_hashes,
        "promotion_authorization": {
            "run_indices": [item["run_index"] for item in corrections],
            "attempts": {
                str(item["run_index"]): item["attempt"] for item in corrections
            },
            "completion_receipt_sha256": {
                str(item["run_index"]): item["completion_sha256"]
                for item in corrections
            },
            "from_class": "scientific_invalid.invalid_score_backfill",
            "to_class": "scored.measured_transaction",
            "scope": "exact_completion_hash_and_fresh_semantic_replay_only",
        },
        "denominator": {
            "manifest_rows": manifest["total_runs"],
            "row_identity": "campaign_manifest.runs[].index",
            "preserved_checkpoint_rows": len(
                checkpoint["preserved_terminal_results"]
            ),
            "promoted_attempts_retained_byte_identical": len(corrections),
            "retried_rows": 0,
            "added_rows": 0,
            "counting_rule": VERIFIER_CORRECTION_COUNTING_RULE,
        },
        "verifier_correction": {
            "cause": "false_positive_in_post_run_semantic_verifier",
            "benchmark_or_harness_changed": False,
            "measured_behavior_changed": False,
            "result_artifacts_changed": False,
            "retry_authorized": False,
            "changed_source_paths": changed_paths,
            "corrected_run_indices": [
                item["run_index"] for item in corrections
            ],
        },
    }
    directory = (
        campaign / AMENDMENT_ROOT / VERIFIER_CORRECTION_AMENDMENT_DIRECTORY
    )
    directory.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if directory.parent.is_symlink():
        raise ContinuationAmendmentError("amendment root must not be a symlink")
    try:
        directory.mkdir(mode=0o755)
    except FileExistsError as exc:
        raise ContinuationAmendmentError(
            f"create-only amendment already exists: {directory}"
        ) from exc
    for name, payload in sidecars.items():
        _write_exclusive(directory / name, _json_bytes(payload))
    amendment_raw = _json_bytes(amendment)
    _write_exclusive(directory / "amendment.json", amendment_raw)
    amendment_sha256 = _sha_bytes(amendment_raw)
    _write_exclusive(
        directory / "amendment.sha256",
        f"{amendment_sha256}  amendment.json\n".encode(),
    )
    return verify_amendment(
        campaign,
        continuation_source_inventory=continuation_source_inventory,
        continuation_limit_contract=continuation_limit_contract,
        continuation_scheduler_contract=continuation_scheduler_contract,
        checkout_proof=lambda *_args: {},
        verifier_correction_proof=verifier_correction_proof,
    )


def _load_verifier_correction_static(
    campaign: Path,
    *,
    proof_callback: VerifierCorrectionProof | None,
) -> VerifiedAmendment:
    campaign = campaign.resolve()
    directory = (
        campaign / AMENDMENT_ROOT / VERIFIER_CORRECTION_AMENDMENT_DIRECTORY
    )
    if directory.is_symlink() or not directory.is_dir():
        raise ContinuationAmendmentError(
            "verifier-correction amendment is absent"
        )
    entries = list(directory.iterdir())
    if (
        {path.name for path in entries}
        != VERIFIER_CORRECTION_AMENDMENT_FILES
        or any(path.is_symlink() or not path.is_file() for path in entries)
    ):
        raise ContinuationAmendmentError(
            "verifier-correction amendment file inventory is not exact"
        )
    amendment_sha256 = _read_amendment_sha(directory)
    amendment = _read_object(directory / "amendment.json")
    exact = {
        "schema",
        "amendment_id",
        "created_utc",
        "campaign_uuid",
        "base_manifest",
        "hash_chain",
        "sidecars",
        "promotion_authorization",
        "denominator",
        "verifier_correction",
    }
    manifest, old_limit, manifest_sha256 = _load_base_unbound(campaign)
    if (
        set(amendment) != exact
        or amendment.get("schema") != VERIFIER_CORRECTION_AMENDMENT_SCHEMA
        or amendment.get("amendment_id")
        != VERIFIER_CORRECTION_AMENDMENT_DIRECTORY
        or amendment.get("campaign_uuid") != manifest.get("campaign_uuid")
    ):
        raise ContinuationAmendmentError(
            "verifier-correction amendment identity differs"
        )
    _parse_utc(amendment.get("created_utc"))
    sidecars = amendment.get("sidecars") or {}
    expected_sidecars = VERIFIER_CORRECTION_AMENDMENT_FILES - {
        "amendment.json", "amendment.sha256"
    }
    if set(sidecars) != expected_sidecars:
        raise ContinuationAmendmentError(
            "verifier-correction sidecar inventory differs"
        )
    for name, expected_hash in sidecars.items():
        if (
            not _is_sha256(expected_hash)
            or _sha_file(directory / name) != expected_hash
        ):
            raise ContinuationAmendmentError(
                f"verifier-correction sidecar drifted: {name}"
            )
    if amendment.get("base_manifest") != {
        "path": "campaign_manifest.json",
        "sha256": manifest_sha256,
        "total_runs": manifest["total_runs"],
        "frozen_artifacts": manifest["frozen_artifacts"],
    }:
        raise ContinuationAmendmentError(
            "verifier-correction base-manifest binding differs"
        )
    base_scheduler = _read_object(directory / "base_scheduler_contract.json")
    if base_scheduler != base_scheduler_contract(manifest, old_limit):
        raise ContinuationAmendmentError(
            "verifier-correction base scheduler differs"
        )
    new_source = _read_object(
        directory / "continuation_source_inventory.json"
    )
    old_source = _read_object(
        _safe_path(campaign, "frozen_inputs/code_inventory.json")
    )
    changed_paths = _verifier_correction_source_delta(old_source, new_source)
    new_limit = _read_object(directory / "continuation_limit_contract.json")
    if new_limit != old_limit:
        raise ContinuationAmendmentError(
            "verifier correction changed the base limit contract"
        )
    chain = amendment.get("hash_chain") or {}
    if (
        chain.get("predecessor_amendment_sha256") is not None
        or chain.get("old")
        != {
            "manifest_sha256": manifest_sha256,
            "source_inventory_sha256": manifest["frozen_artifacts"][
                "code_inventory.json"
            ],
            "limit_contract_sha256": manifest["frozen_artifacts"][
                "limit_contract.json"
            ],
            "scheduler_contract_sha256": sidecars[
                "base_scheduler_contract.json"
            ],
        }
        or chain.get("new")
        != {
            "source_inventory_sha256": sidecars[
                "continuation_source_inventory.json"
            ],
            "limit_contract_sha256": sidecars[
                "continuation_limit_contract.json"
            ],
            "scheduler_contract_sha256": sidecars[
                "continuation_scheduler_contract.json"
            ],
        }
    ):
        raise ContinuationAmendmentError(
            "verifier-correction hash chain differs"
        )
    checkpoint = _read_object(directory / "correction_checkpoint.json")
    _verify_verifier_correction_checkpoint(
        campaign, checkpoint, proof_callback
    )
    corrections = checkpoint["verifier_corrections"]
    if amendment.get("promotion_authorization") != {
        "run_indices": [item["run_index"] for item in corrections],
        "attempts": {
            str(item["run_index"]): item["attempt"] for item in corrections
        },
        "completion_receipt_sha256": {
            str(item["run_index"]): item["completion_sha256"]
            for item in corrections
        },
        "from_class": "scientific_invalid.invalid_score_backfill",
        "to_class": "scored.measured_transaction",
        "scope": "exact_completion_hash_and_fresh_semantic_replay_only",
    }:
        raise ContinuationAmendmentError(
            "verifier-correction promotion authorization differs"
        )
    if amendment.get("denominator") != {
        "manifest_rows": manifest["total_runs"],
        "row_identity": "campaign_manifest.runs[].index",
        "preserved_checkpoint_rows": len(
            checkpoint["preserved_terminal_results"]
        ),
        "promoted_attempts_retained_byte_identical": len(corrections),
        "retried_rows": 0,
        "added_rows": 0,
        "counting_rule": VERIFIER_CORRECTION_COUNTING_RULE,
    }:
        raise ContinuationAmendmentError(
            "verifier-correction denominator contract differs"
        )
    if amendment.get("verifier_correction") != {
        "cause": "false_positive_in_post_run_semantic_verifier",
        "benchmark_or_harness_changed": False,
        "measured_behavior_changed": False,
        "result_artifacts_changed": False,
        "retry_authorized": False,
        "changed_source_paths": changed_paths,
        "corrected_run_indices": [item["run_index"] for item in corrections],
    }:
        raise ContinuationAmendmentError(
            "verifier-correction declared scope differs"
        )
    scheduler = _read_object(
        directory / "continuation_scheduler_contract.json"
    )
    validate_scheduler_contract(scheduler, manifest)
    if scheduler != _unchanged_correction_scheduler(manifest, old_limit):
        raise ContinuationAmendmentError(
            "verifier correction changed the base scheduler contract"
        )
    return VerifiedAmendment(
        campaign=campaign,
        directory=directory,
        amendment=amendment,
        checkpoint=checkpoint,
        scheduler=scheduler,
        amendment_sha256=amendment_sha256,
        scheduler_sha256=sidecars["continuation_scheduler_contract.json"],
    )


def _raw_read_state_audit(
    out_dir: Path, item: Mapping[str, Any]
) -> dict[str, int]:
    """Read the raw, pre-classification BrowserUse context-cap evidence."""

    trajectory_path = out_dir / "trajectory.json"
    trajectory = _read_object(trajectory_path)
    try:
        context = trajectory["stats"]["context_cap_audit"]
        record = context["limits"]["read_state_chars"]
        configured = record["configured"]
        touched = record["touched_count"]
        maximum = record["max_observed"]
    except (KeyError, TypeError) as exc:
        raise ContinuationAmendmentError(
            "raw read-state cap audit is absent"
        ) from exc
    if (
        not isinstance(context, dict)
        or context.get("complete") is not True
        or type(configured) is not int
        or configured <= 0
        or type(touched) is not int
        or touched < 0
        or type(maximum) is not int
        or maximum < 0
        or item.get("authoritative_artifacts", {}).get("trajectory.json")
        != _sha_file(trajectory_path)
    ):
        raise ContinuationAmendmentError(
            "raw read-state cap audit is malformed or unbound"
        )
    return {
        "configured_chars": configured,
        "touched_count": touched,
        "max_observed_chars": maximum,
    }


def _validate_lossless_read_state_proof(
    value: Any,
    *,
    item: Mapping[str, Any],
    raw_audit: Mapping[str, Any],
    limit_evidence: Mapping[str, Any],
    measured_result: Mapping[str, Any],
) -> None:
    exact = {
        "schema",
        "read_only_replay",
        "run_index",
        "run_id",
        "attempt",
        "trajectory_sha256",
        "cause",
        "raw_read_state",
        "raw_limit_audit",
        "measured_result",
        "remedy",
    }
    remedy = value.get("remedy") if isinstance(value, dict) else None
    expected_remedy = {
        "activation_scope": "run_598_attempt_2_only",
        "delivery": "lossless_read_state",
        "action_results_limit": "unchanged",
        "all_other_runs": "unchanged",
        "catalog_tasks_and_steering": "unchanged",
        "upstream_guards": "fail_closed",
    }
    if (
        not isinstance(value, dict)
        or set(value) != exact
        or value.get("schema") != LOSSLESS_READ_STATE_PROOF_SCHEMA
        or value.get("read_only_replay") is not True
        or value.get("run_index") != PROTOCOL_RECOVERY_RUN_INDEX
        or value.get("run_id") != item.get("run_id")
        or value.get("attempt") != 1
        or value.get("trajectory_sha256")
        != item.get("authoritative_artifacts", {}).get("trajectory.json")
        or value.get("cause")
        != "lossy_browser_use_read_state_history_rendering"
        or value.get("raw_read_state") != raw_audit
        or value.get("raw_limit_audit") != limit_evidence
        or value.get("measured_result") != measured_result
        or remedy != expected_remedy
        or raw_audit.get("touched_count") != 1
        or raw_audit.get("max_observed_chars", 0)
        <= raw_audit.get("configured_chars", 0)
    ):
        raise ContinuationAmendmentError(
            "lossless read-state recovery proof differs from the exact defect"
        )


def _protocol_read_state_evidence(
    out_dir: Path, raw_audit: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    trajectory = _read_object(out_dir / "trajectory.json")
    summary = _read_object(out_dir / "summary.json")
    try:
        limit_audit = trajectory["stats"]["limit_audit"]
        lossy = limit_audit["categories"]["lossy_context_limits"]
        safety = limit_audit["categories"]["safety_backstops"]
        read_state = lossy["read_state_chars"]
        observations = read_state["observations"]
    except (KeyError, TypeError) as exc:
        raise ContinuationAmendmentError(
            "lossless recovery lacks its raw limit-contract evidence"
        ) from exc
    if (
        not isinstance(lossy, dict)
        or not lossy
        or not isinstance(safety, dict)
        or not safety
        or any(
            not isinstance(record, dict)
            or type(record.get("touched_count")) is not int
            or record["touched_count"] < 0
            for record in lossy.values()
        )
    ):
        raise ContinuationAmendmentError(
            "lossless recovery limit category inventory is malformed"
        )
    touched_lossy = sorted(
        name
        for name, record in lossy.items()
        if record.get("touched_count")
    )
    if (
        limit_audit.get("complete") is not True
        or limit_audit.get("error") is not None
        or touched_lossy != ["read_state_chars"]
        or read_state.get("configured") != raw_audit["configured_chars"]
        or read_state.get("touched_count") != raw_audit["touched_count"]
        or observations.get("touched_count") != raw_audit["touched_count"]
        or observations.get("max_observed")
        != raw_audit["max_observed_chars"]
        or any(
            not isinstance(record, dict)
            or record.get("touched_count") != 0
            for record in safety.values()
        )
    ):
        raise ContinuationAmendmentError(
            "lossless recovery raw limit-contract evidence differs"
        )
    measured = {
        "outcome": summary.get("outcome"),
        "preservation_strict": summary.get("preservation_strict"),
        "strict_binary": summary.get("strict_binary"),
        "literal_hero": summary.get("literal_hero"),
        "hero_identity": summary.get("hero_identity"),
        "chosen": summary.get("chosen"),
    }
    if (
        measured["outcome"] != "compliant"
        or measured["preservation_strict"] != 1.0
        or measured["strict_binary"] != 1
        or measured["literal_hero"] != 1
        or not isinstance(measured["hero_identity"], str)
        or not measured["hero_identity"]
        or measured["chosen"] != measured["hero_identity"]
    ):
        raise ContinuationAmendmentError(
            "lossless recovery attempt is not the measured P*=1 hero result"
        )
    evidence = {
        "limit_audit_complete": True,
        "only_lossy_limit_touched": "read_state_chars",
        "raw_context_audit_matches": True,
        "all_safety_backstops_untouched": True,
    }
    return evidence, measured


def _protocol_recovery_retry_item(
    campaign: Path,
    manifest: Mapping[str, Any],
    row: Mapping[str, Any],
    completion_path: Path,
    proof_callback: LosslessReadStateProof,
) -> dict[str, Any]:
    item, launch, completion, out_dir = _correction_artifact_item(
        campaign, manifest, row, completion_path
    )
    classification = completion.get("classification") or {}
    if (
        row.get("index") != PROTOCOL_RECOVERY_RUN_INDEX
        or row.get("env") != "instacart"
        or classification.get("class") != "scientific_invalid"
        or classification.get("code") != "limit_touched"
        or classification.get("evidence")
        != "lossy_context_limits.read_state_chars"
    ):
        raise ContinuationAmendmentError(
            "protocol recovery retry is not the exact read-state invalid"
        )
    raw_audit = _raw_read_state_audit(out_dir, item)
    limit_evidence, measured_result = _protocol_read_state_evidence(
        out_dir, raw_audit
    )
    before = dict(item["authoritative_artifacts"])
    proof = dict(
        proof_callback(
            campaign, manifest, row, 1, out_dir, launch, completion
        )
    )
    _validate_lossless_read_state_proof(
        proof,
        item=item,
        raw_audit=raw_audit,
        limit_evidence=limit_evidence,
        measured_result=measured_result,
    )
    _verify_correction_item_hashes(campaign, item)
    if _receipt_artifact_hashes(out_dir, row) != before:
        raise ContinuationAmendmentError(
            "lossless read-state proof changed a result artifact"
        )
    return {
        **item,
        "recorded_classification": classification,
        "raw_read_state_audit": raw_audit,
        "lossless_read_state_proof": proof,
        "retry_basis": "exact_hash_bound_harness_defect_recovery",
    }


def _protocol_attempt_two_absent(campaign: Path, row: Mapping[str, Any]) -> bool:
    target = campaign / "runs" / "attempt_2" / str(row.get("run_id", ""))
    if target.exists() or target.is_symlink():
        return False
    stem = (
        f"{int(row.get('index', -1)):04d}_{row.get('cell_name', '')}.attempt2"
    )
    for root, suffix in (
        (campaign / "launch_receipts", ".launch.json"),
        (campaign / "completion_receipts", ".complete.json"),
        (campaign / "scratch_cleanup_receipts", ".scratch.json"),
    ):
        if not root.exists():
            continue
        exact = root / f"{stem}{suffix}"
        if exact.exists() or exact.is_symlink():
            return False
        for path in root.glob(f"*.attempt2{suffix}"):
            try:
                payload = _read_object(path)
            except ContinuationAmendmentError:
                return False
            if payload.get("run_index") == PROTOCOL_RECOVERY_RUN_INDEX:
                return False
    return True


def _collect_protocol_recovery_checkpoint(
    campaign: Path,
    manifest: Mapping[str, Any],
    predecessor: VerifiedAmendment,
    verifier_correction_proof: VerifierCorrectionProof,
    lossless_read_state_proof: LosslessReadStateProof,
) -> dict[str, Any]:
    """Seal the 603-row drained frontier and its two exact remedies."""

    status_path = _safe_path(campaign, "status.json")
    status = _read_object(status_path)
    completion_paths = sorted(
        (campaign / "completion_receipts").glob("*.complete.json")
    )
    launch_paths = sorted((campaign / "launch_receipts").glob("*.launch.json"))
    if (
        manifest.get("total_runs") != PROTOCOL_RECOVERY_MANIFEST_ROWS
        or status.get("state") != "protocol_invalid"
        or status.get("running") != []
        or status.get("running_count") != 0
        or status.get("pending_refill") != 0
        or status.get("pending_primary") != PROTOCOL_RECOVERY_PENDING_ROWS
        or status.get("final_count") != PROTOCOL_RECOVERY_FRONTIER_ROWS
        or len(completion_paths) != PROTOCOL_RECOVERY_FRONTIER_ROWS
        or len(launch_paths) != PROTOCOL_RECOVERY_FRONTIER_ROWS
    ):
        raise ContinuationAmendmentError(
            "protocol recovery requires the exact drained 603/960 frontier"
        )

    inherited = predecessor.correction_by_index
    if set(inherited) != PROTOCOL_RECOVERY_INHERITED_CORRECTION_INDICES:
        raise ContinuationAmendmentError(
            "protocol recovery predecessor correction inventory differs"
        )
    predecessor_preserved = {
        int(item["run_index"]): item
        for item in predecessor.checkpoint["preserved_terminal_results"]
    }

    preserved: list[dict[str, Any]] = []
    retry_items: list[dict[str, Any]] = []
    raw_classes: Counter[str] = Counter()
    seen: set[int] = set()
    raw_invalid: set[int] = set()
    read_state_zero = 0
    fresh_correction: dict[str, Any] | None = None

    for completion_path in completion_paths:
        if completion_path.is_symlink() or not completion_path.is_file():
            raise ContinuationAmendmentError(
                "protocol-recovery completion receipt is unsafe"
            )
        raw = _read_object(completion_path)
        index = raw.get("run_index")
        if (
            type(index) is not int
            or index < 0
            or index >= len(manifest["runs"])
            or index in seen
        ):
            raise ContinuationAmendmentError(
                "protocol-recovery completion rows are not unique"
            )
        seen.add(index)
        row = manifest["runs"][index]
        item, launch, completion, out_dir = _correction_artifact_item(
            campaign, manifest, row, completion_path
        )
        classification = completion.get("classification") or {}
        result_class = classification.get("class")
        raw_classes[result_class] += 1
        if result_class == "scientific_invalid":
            raw_invalid.add(index)
        elif result_class not in {"scored", "behavioral"}:
            raise ContinuationAmendmentError(
                "protocol-recovery frontier has an unsupported result class"
            )

        if index == PROTOCOL_RECOVERY_RUN_INDEX:
            retry_items.append(
                _protocol_recovery_retry_item(
                    campaign,
                    manifest,
                    row,
                    completion_path,
                    lossless_read_state_proof,
                )
            )
            continue

        audit = _raw_read_state_audit(out_dir, item)
        if audit["touched_count"] != 0:
            raise ContinuationAmendmentError(
                "a preserved row touched the raw read-state limit"
            )
        read_state_zero += 1
        preserved.append(item)

        if index in PROTOCOL_RECOVERY_INHERITED_CORRECTION_INDICES:
            if (
                predecessor_preserved.get(index) != item
                or inherited[index]
                != predecessor.checkpoint["verifier_corrections"][
                    sorted(PROTOCOL_RECOVERY_INHERITED_CORRECTION_INDICES).index(index)
                ]
            ):
                raise ContinuationAmendmentError(
                    "inherited verifier correction or artifact record drifted"
                )
            continue

        if index == PROTOCOL_RECOVERY_CORRECTION_INDEX:
            if (
                row.get("env") != "airbnb"
                or result_class != "scientific_invalid"
                or classification.get("code") != "invalid_score_backfill"
            ):
                raise ContinuationAmendmentError(
                    "fresh protocol correction is not the exact Airbnb invalid"
                )
            before = dict(item["authoritative_artifacts"])
            proof = dict(
                verifier_correction_proof(
                    campaign, manifest, row, 1, out_dir, launch, completion
                )
            )
            _validate_correction_proof(proof)
            _verify_correction_item_hashes(campaign, item)
            if _receipt_artifact_hashes(out_dir, row) != before:
                raise ContinuationAmendmentError(
                    "fresh verifier replay changed a result artifact"
                )
            corrected = proof["corrected_classification"]
            if corrected.get("outcome") != completion.get("outcome"):
                raise ContinuationAmendmentError(
                    "fresh corrected outcome differs from immutable completion"
                )
            fresh_correction = {
                **item,
                "recorded_classification": classification,
                "corrected_classification": corrected,
                "verifier_proof": proof,
                "promotion_basis": "fresh_read_only_semantic_replay",
            }

    if raw_invalid != PROTOCOL_RECOVERY_RAW_INVALID_INDICES:
        raise ContinuationAmendmentError(
            "protocol-recovery raw invalid receipt set differs"
        )
    effective_invalid = raw_invalid - set(inherited)
    if effective_invalid != PROTOCOL_RECOVERY_EFFECTIVE_INVALID_INDICES:
        raise ContinuationAmendmentError(
            "protocol-recovery effective invalid set differs"
        )
    if (
        len(retry_items) != 1
        or fresh_correction is None
        or read_state_zero != PROTOCOL_RECOVERY_FRONTIER_ROWS - 1
        or not _protocol_attempt_two_absent(
            campaign, manifest["runs"][PROTOCOL_RECOVERY_RUN_INDEX]
        )
    ):
        raise ContinuationAmendmentError(
            "protocol-recovery retry frontier is not exact"
        )

    effective_classes = Counter(raw_classes)
    effective_classes["scientific_invalid"] -= len(inherited)
    effective_classes["scored"] += len(inherited)
    effective_classes = +effective_classes
    if status.get("final_classes") != dict(effective_classes):
        raise ContinuationAmendmentError(
            "protocol-recovery status does not reflect predecessor corrections"
        )

    corrections = [
        inherited[index]
        for index in sorted(PROTOCOL_RECOVERY_INHERITED_CORRECTION_INDICES)
    ] + [fresh_correction]
    counts = {
        "checkpoint_completion_receipts": PROTOCOL_RECOVERY_FRONTIER_ROWS,
        "preserved_terminal_rows": PROTOCOL_RECOVERY_FRONTIER_ROWS - 1,
        "authorized_retry_rows": 1,
        "verifier_corrected_rows": 3,
        "inherited_verifier_corrected_rows": 2,
        "fresh_verifier_corrected_rows": 1,
        "raw_recorded_classes": dict(sorted(raw_classes.items())),
        "effective_pre_recovery_classes": dict(sorted(effective_classes.items())),
        "raw_invalid_indices": sorted(raw_invalid),
        "effective_invalid_indices": sorted(effective_invalid),
        "read_state_zero_preserved_rows": read_state_zero,
        "pending_primary_rows": PROTOCOL_RECOVERY_PENDING_ROWS,
    }
    return {
        "schema": PROTOCOL_RECOVERY_CHECKPOINT_SCHEMA,
        "campaign_uuid": manifest["campaign_uuid"],
        "manifest_sha256": _sha_file(campaign / "campaign_manifest.json"),
        "predecessor_amendment_sha256": predecessor.amendment_sha256,
        "frontier_status_snapshot": status,
        "frontier_status_file_sha256": _sha_file(status_path),
        "counts": counts,
        "preserved_terminal_results": sorted(
            preserved, key=lambda value: value["run_index"]
        ),
        "authorized_retry_attempts": retry_items,
        "verifier_corrections": corrections,
    }


_PROTOCOL_BASE_ITEM_KEYS = frozenset({
    "run_index",
    "run_id",
    "env",
    "attempt",
    "launch_receipt",
    "launch_sha256",
    "completion_receipt",
    "completion_sha256",
    "scratch_cleanup_receipt",
    "scratch_cleanup_sha256",
    "classification",
    "authoritative_artifacts",
})


def _protocol_base_item(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or not _PROTOCOL_BASE_ITEM_KEYS <= set(value):
        raise ContinuationAmendmentError(
            "protocol-recovery artifact record is malformed"
        )
    return {key: value[key] for key in _PROTOCOL_BASE_ITEM_KEYS}


def _verify_protocol_recovery_checkpoint(
    campaign: Path,
    checkpoint: Mapping[str, Any],
    predecessor: VerifiedAmendment,
    verifier_correction_proof: VerifierCorrectionProof | None,
    lossless_read_state_proof: LosslessReadStateProof | None,
) -> None:
    exact = {
        "schema",
        "campaign_uuid",
        "manifest_sha256",
        "predecessor_amendment_sha256",
        "frontier_status_snapshot",
        "frontier_status_file_sha256",
        "counts",
        "preserved_terminal_results",
        "authorized_retry_attempts",
        "verifier_corrections",
    }
    manifest = _read_object(campaign / "campaign_manifest.json")
    if (
        not isinstance(checkpoint, dict)
        or set(checkpoint) != exact
        or checkpoint.get("schema") != PROTOCOL_RECOVERY_CHECKPOINT_SCHEMA
        or checkpoint.get("campaign_uuid") != manifest.get("campaign_uuid")
        or checkpoint.get("manifest_sha256")
        != _sha_file(campaign / "campaign_manifest.json")
        or checkpoint.get("predecessor_amendment_sha256")
        != predecessor.amendment_sha256
        or not _is_sha256(checkpoint.get("frontier_status_file_sha256"))
    ):
        raise ContinuationAmendmentError(
            "protocol-recovery checkpoint identity differs"
        )
    preserved = checkpoint.get("preserved_terminal_results")
    retries = checkpoint.get("authorized_retry_attempts")
    corrections = checkpoint.get("verifier_corrections")
    if (
        not isinstance(preserved, list)
        or not isinstance(retries, list)
        or not isinstance(corrections, list)
        or len(retries) != 1
    ):
        raise ContinuationAmendmentError(
            "protocol-recovery checkpoint inventories are malformed"
        )

    preserved_by_index: dict[int, Mapping[str, Any]] = {}
    raw_classes: Counter[str] = Counter()
    raw_invalid: set[int] = set()
    for item in preserved:
        base = _protocol_base_item(item)
        if set(item) != _PROTOCOL_BASE_ITEM_KEYS:
            raise ContinuationAmendmentError(
                "protocol-recovery preserved record schema differs"
            )
        index = base.get("run_index")
        if (
            type(index) is not int
            or index < 0
            or index >= len(manifest["runs"])
            or index in preserved_by_index
            or index == PROTOCOL_RECOVERY_RUN_INDEX
        ):
            raise ContinuationAmendmentError(
                "protocol-recovery preserved rows are not unique"
            )
        row = manifest["runs"][index]
        if (
            base.get("run_id") != row.get("run_id")
            or base.get("env") != row.get("env")
            or base.get("attempt") != 1
        ):
            raise ContinuationAmendmentError(
                "protocol-recovery preserved row identity differs"
            )
        _verify_correction_item_hashes(campaign, base)
        completion = _read_object(
            _safe_path(campaign, str(base["completion_receipt"]))
        )
        if completion.get("classification") != base.get("classification"):
            raise ContinuationAmendmentError(
                "protocol-recovery preserved classification drifted"
            )
        result_class = (base.get("classification") or {}).get("class")
        raw_classes[result_class] += 1
        if result_class == "scientific_invalid":
            raw_invalid.add(index)
        out_dir = _attempt_out_dir(campaign, row["run_id"], 1)
        if _raw_read_state_audit(out_dir, base)["touched_count"] != 0:
            raise ContinuationAmendmentError(
                "a preserved protocol-recovery row touched read-state"
            )
        preserved_by_index[index] = base

    retry = retries[0]
    retry_base = _protocol_base_item(retry)
    retry_extra = {
        "recorded_classification",
        "raw_read_state_audit",
        "lossless_read_state_proof",
        "retry_basis",
    }
    if (
        set(retry) != _PROTOCOL_BASE_ITEM_KEYS | retry_extra
        or retry_base.get("run_index") != PROTOCOL_RECOVERY_RUN_INDEX
        or retry.get("recorded_classification")
        != retry_base.get("classification")
        or retry.get("retry_basis")
        != "exact_hash_bound_harness_defect_recovery"
    ):
        raise ContinuationAmendmentError(
            "protocol-recovery retry record differs"
        )
    retry_row = manifest["runs"][PROTOCOL_RECOVERY_RUN_INDEX]
    if (
        retry_base.get("run_id") != retry_row.get("run_id")
        or retry_base.get("env") != "instacart"
        or retry_base.get("attempt") != 1
    ):
        raise ContinuationAmendmentError(
            "protocol-recovery retry identity differs"
        )
    _verify_correction_item_hashes(campaign, retry_base)
    retry_completion = _read_object(
        _safe_path(campaign, str(retry_base["completion_receipt"]))
    )
    retry_classification = retry_completion.get("classification") or {}
    if (
        retry_classification != retry_base.get("classification")
        or retry_classification.get("class") != "scientific_invalid"
        or retry_classification.get("code") != "limit_touched"
        or retry_classification.get("evidence")
        != "lossy_context_limits.read_state_chars"
    ):
        raise ContinuationAmendmentError(
            "protocol-recovery retry classification drifted"
        )
    retry_out = _attempt_out_dir(campaign, retry_row["run_id"], 1)
    raw_audit = _raw_read_state_audit(retry_out, retry_base)
    limit_evidence, measured_result = _protocol_read_state_evidence(
        retry_out, raw_audit
    )
    if retry.get("raw_read_state_audit") != raw_audit:
        raise ContinuationAmendmentError(
            "protocol-recovery retry raw audit differs"
        )
    _validate_lossless_read_state_proof(
        retry.get("lossless_read_state_proof"),
        item=retry_base,
        raw_audit=raw_audit,
        limit_evidence=limit_evidence,
        measured_result=measured_result,
    )
    if lossless_read_state_proof is not None:
        launch = _read_object(
            _safe_path(campaign, str(retry_base["launch_receipt"]))
        )
        before = dict(retry_base["authoritative_artifacts"])
        fresh = dict(
            lossless_read_state_proof(
                campaign,
                manifest,
                retry_row,
                1,
                retry_out,
                launch,
                retry_completion,
            )
        )
        if (
            fresh != retry.get("lossless_read_state_proof")
            or _receipt_artifact_hashes(retry_out, retry_row) != before
        ):
            raise ContinuationAmendmentError(
                "fresh lossless read-state proof differs from checkpoint"
            )
    raw_classes["scientific_invalid"] += 1
    raw_invalid.add(PROTOCOL_RECOVERY_RUN_INDEX)

    correction_by_index: dict[int, Mapping[str, Any]] = {}
    predecessor_corrections = predecessor.correction_by_index
    for correction in corrections:
        index = correction.get("run_index") if isinstance(correction, dict) else None
        if type(index) is not int or index in correction_by_index:
            raise ContinuationAmendmentError(
                "protocol-recovery correction rows are not unique"
            )
        if index in PROTOCOL_RECOVERY_INHERITED_CORRECTION_INDICES:
            if correction != predecessor_corrections.get(index):
                raise ContinuationAmendmentError(
                    "inherited verifier correction is not byte-identical"
                )
        elif index == PROTOCOL_RECOVERY_CORRECTION_INDEX:
            base = preserved_by_index.get(index)
            if base is None or any(
                correction.get(key) != base.get(key)
                for key in _PROTOCOL_BASE_ITEM_KEYS
            ):
                raise ContinuationAmendmentError(
                    "fresh verifier correction changed its preserved record"
                )
            recorded = correction.get("recorded_classification") or {}
            proof = correction.get("verifier_proof")
            _validate_correction_proof(proof)
            if (
                correction.get("promotion_basis")
                != "fresh_read_only_semantic_replay"
                or base.get("env") != "airbnb"
                or recorded.get("class") != "scientific_invalid"
                or recorded.get("code") != "invalid_score_backfill"
                or correction.get("recorded_classification")
                != base.get("classification")
                or correction.get("corrected_classification")
                != proof.get("corrected_classification")
            ):
                raise ContinuationAmendmentError(
                    "fresh verifier correction semantics differ"
                )
            if verifier_correction_proof is not None:
                row = manifest["runs"][index]
                out_dir = _attempt_out_dir(campaign, row["run_id"], 1)
                launch = _read_object(
                    _safe_path(campaign, str(base["launch_receipt"]))
                )
                completion = _read_object(
                    _safe_path(campaign, str(base["completion_receipt"]))
                )
                before = dict(base["authoritative_artifacts"])
                fresh = dict(
                    verifier_correction_proof(
                        campaign,
                        manifest,
                        row,
                        1,
                        out_dir,
                        launch,
                        completion,
                    )
                )
                if (
                    fresh != proof
                    or _receipt_artifact_hashes(out_dir, row) != before
                ):
                    raise ContinuationAmendmentError(
                        "fresh verifier replay differs from protocol checkpoint"
                    )
        else:
            raise ContinuationAmendmentError(
                "protocol-recovery correction allowlist differs"
            )
        correction_by_index[index] = correction
    if set(correction_by_index) != (
        set(PROTOCOL_RECOVERY_INHERITED_CORRECTION_INDICES)
        | {PROTOCOL_RECOVERY_CORRECTION_INDEX}
    ):
        raise ContinuationAmendmentError(
            "protocol-recovery correction inventory differs"
        )

    if raw_invalid != PROTOCOL_RECOVERY_RAW_INVALID_INDICES:
        raise ContinuationAmendmentError(
            "protocol-recovery raw invalid set differs"
        )
    effective_invalid = raw_invalid - set(predecessor_corrections)
    if effective_invalid != PROTOCOL_RECOVERY_EFFECTIVE_INVALID_INDICES:
        raise ContinuationAmendmentError(
            "protocol-recovery effective invalid set differs"
        )
    effective_classes = Counter(raw_classes)
    effective_classes["scientific_invalid"] -= len(predecessor_corrections)
    effective_classes["scored"] += len(predecessor_corrections)
    effective_classes = +effective_classes
    expected_counts = {
        "checkpoint_completion_receipts": PROTOCOL_RECOVERY_FRONTIER_ROWS,
        "preserved_terminal_rows": PROTOCOL_RECOVERY_FRONTIER_ROWS - 1,
        "authorized_retry_rows": 1,
        "verifier_corrected_rows": 3,
        "inherited_verifier_corrected_rows": 2,
        "fresh_verifier_corrected_rows": 1,
        "raw_recorded_classes": dict(sorted(raw_classes.items())),
        "effective_pre_recovery_classes": dict(sorted(effective_classes.items())),
        "raw_invalid_indices": sorted(raw_invalid),
        "effective_invalid_indices": sorted(effective_invalid),
        "read_state_zero_preserved_rows": len(preserved),
        "pending_primary_rows": PROTOCOL_RECOVERY_PENDING_ROWS,
    }
    status = checkpoint.get("frontier_status_snapshot") or {}
    if (
        checkpoint.get("counts") != expected_counts
        or checkpoint.get("frontier_status_file_sha256")
        != _sha_bytes(_json_bytes(status))
        or len(preserved) != PROTOCOL_RECOVERY_FRONTIER_ROWS - 1
        or len(preserved_by_index) + 1 != PROTOCOL_RECOVERY_FRONTIER_ROWS
        or status.get("state") != "protocol_invalid"
        or status.get("total_runs") != PROTOCOL_RECOVERY_MANIFEST_ROWS
        or status.get("running") != []
        or status.get("running_count") != 0
        or status.get("pending_refill") != 0
        or status.get("pending_primary") != PROTOCOL_RECOVERY_PENDING_ROWS
        or status.get("final_count") != PROTOCOL_RECOVERY_FRONTIER_ROWS
        or status.get("final_classes") != dict(effective_classes)
    ):
        raise ContinuationAmendmentError(
            "protocol-recovery checkpoint frontier differs"
        )


def create_protocol_recovery_amendment(
    campaign: Path,
    *,
    continuation_source_inventory: Mapping[str, Any],
    continuation_limit_contract: Mapping[str, Any],
    continuation_scheduler_contract: Mapping[str, Any],
    verifier_correction_proof: VerifierCorrectionProof,
    lossless_read_state_proof: LosslessReadStateProof,
    created_utc: str,
) -> VerifiedAmendment:
    """Publish the create-only successor to verifier_correction_001."""

    campaign = campaign.resolve()
    _parse_utc(created_utc)
    root = campaign / AMENDMENT_ROOT
    if root.is_symlink() or not root.is_dir() or {
        path.name for path in root.iterdir()
    } != {VERIFIER_CORRECTION_AMENDMENT_DIRECTORY}:
        raise ContinuationAmendmentError(
            "protocol recovery requires only verifier_correction_001"
        )
    predecessor = _load_verifier_correction_static(
        campaign, proof_callback=None
    )
    manifest, _old_limit, manifest_sha256 = _load_base_unbound(campaign)
    _validate_continuation_payloads(
        continuation_source_inventory,
        continuation_limit_contract,
        continuation_scheduler_contract,
        manifest,
    )
    predecessor_source = _read_object(
        predecessor.directory / "continuation_source_inventory.json"
    )
    changed_paths = _protocol_recovery_source_delta(
        predecessor_source, continuation_source_inventory
    )
    predecessor_limit = _read_object(
        predecessor.directory / "continuation_limit_contract.json"
    )
    predecessor_scheduler = _read_object(
        predecessor.directory / "continuation_scheduler_contract.json"
    )
    if continuation_limit_contract != predecessor_limit:
        raise ContinuationAmendmentError(
            "protocol recovery changed the predecessor limit contract"
        )
    if continuation_scheduler_contract != predecessor_scheduler:
        raise ContinuationAmendmentError(
            "protocol recovery changed the predecessor scheduler contract"
        )
    checkpoint = _collect_protocol_recovery_checkpoint(
        campaign,
        manifest,
        predecessor,
        verifier_correction_proof,
        lossless_read_state_proof,
    )
    sidecars = {
        "recovery_checkpoint.json": checkpoint,
        "continuation_source_inventory.json": dict(
            continuation_source_inventory
        ),
        "continuation_limit_contract.json": dict(
            continuation_limit_contract
        ),
        "continuation_scheduler_contract.json": dict(
            continuation_scheduler_contract
        ),
    }
    sidecar_hashes = {
        name: _sha_bytes(_json_bytes(payload))
        for name, payload in sidecars.items()
    }
    if (
        sidecar_hashes["continuation_limit_contract.json"]
        != predecessor.amendment["sidecars"][
            "continuation_limit_contract.json"
        ]
        or sidecar_hashes["continuation_scheduler_contract.json"]
        != predecessor.amendment["sidecars"][
            "continuation_scheduler_contract.json"
        ]
    ):
        raise ContinuationAmendmentError(
            "protocol recovery limit/scheduler bytes differ from predecessor"
        )
    corrections = checkpoint["verifier_corrections"]
    retries = checkpoint["authorized_retry_attempts"]
    amendment = {
        "schema": PROTOCOL_RECOVERY_AMENDMENT_SCHEMA,
        "amendment_id": PROTOCOL_RECOVERY_AMENDMENT_DIRECTORY,
        "created_utc": created_utc,
        "campaign_uuid": manifest["campaign_uuid"],
        "base_manifest": {
            "path": "campaign_manifest.json",
            "sha256": manifest_sha256,
            "total_runs": manifest["total_runs"],
            "frozen_artifacts": manifest["frozen_artifacts"],
        },
        "hash_chain": {
            "predecessor_amendment_sha256": predecessor.amendment_sha256,
            "old": dict(predecessor.amendment["hash_chain"]["new"]),
            "new": {
                "source_inventory_sha256": sidecar_hashes[
                    "continuation_source_inventory.json"
                ],
                "limit_contract_sha256": sidecar_hashes[
                    "continuation_limit_contract.json"
                ],
                "scheduler_contract_sha256": sidecar_hashes[
                    "continuation_scheduler_contract.json"
                ],
            },
        },
        "sidecars": sidecar_hashes,
        "promotion_authorization": {
            "run_indices": [item["run_index"] for item in corrections],
            "inherited_run_indices": sorted(
                PROTOCOL_RECOVERY_INHERITED_CORRECTION_INDICES
            ),
            "fresh_run_indices": [PROTOCOL_RECOVERY_CORRECTION_INDEX],
            "attempts": {
                str(item["run_index"]): item["attempt"]
                for item in corrections
            },
            "completion_receipt_sha256": {
                str(item["run_index"]): item["completion_sha256"]
                for item in corrections
            },
            "scope": "inherited_byte_identical_plus_fresh_hash_bound_replay",
        },
        "retry_authorization": {
            "from_attempt": 1,
            "to_attempt": 2,
            "run_indices": [PROTOCOL_RECOVERY_RUN_INDEX],
            "completion_receipt_sha256": {
                str(PROTOCOL_RECOVERY_RUN_INDEX): retries[0][
                    "completion_sha256"
                ]
            },
            "scope": "exact_hash_allowlist_lossless_read_state_only",
        },
        "denominator": {
            "manifest_rows": manifest["total_runs"],
            "row_identity": "campaign_manifest.runs[].index",
            "preserved_checkpoint_rows": len(
                checkpoint["preserved_terminal_results"]
            ),
            "promoted_attempts_retained_byte_identical": len(corrections),
            "superseded_attempts_retained_for_audit": len(retries),
            "added_rows": 0,
            "counting_rule": PROTOCOL_RECOVERY_COUNTING_RULE,
        },
        "protocol_recovery": {
            "cause": "single_lossy_read_state_delivery_harness_defect",
            "changed_source_paths": changed_paths,
            "raw_invalid_indices": sorted(
                PROTOCOL_RECOVERY_RAW_INVALID_INDICES
            ),
            "effective_invalid_indices": sorted(
                PROTOCOL_RECOVERY_EFFECTIVE_INVALID_INDICES
            ),
            "inherited_correction_indices": sorted(
                PROTOCOL_RECOVERY_INHERITED_CORRECTION_INDICES
            ),
            "fresh_correction_indices": [
                PROTOCOL_RECOVERY_CORRECTION_INDEX
            ],
            "retry_indices": [PROTOCOL_RECOVERY_RUN_INDEX],
            "benchmark_environment_changed": False,
            "catalog_tasks_or_steering_changed": False,
            "non_target_agent_behavior_changed": False,
            "action_results_limit_changed": False,
            "lossless_activation": "run_598_attempt_2_only",
        },
    }
    directory = root / PROTOCOL_RECOVERY_AMENDMENT_DIRECTORY
    try:
        directory.mkdir(mode=0o755)
    except FileExistsError as exc:
        raise ContinuationAmendmentError(
            f"create-only amendment already exists: {directory}"
        ) from exc
    for name, payload in sidecars.items():
        _write_exclusive(directory / name, _json_bytes(payload))
    amendment_raw = _json_bytes(amendment)
    _write_exclusive(directory / "amendment.json", amendment_raw)
    amendment_sha256 = _sha_bytes(amendment_raw)
    _write_exclusive(
        directory / "amendment.sha256",
        f"{amendment_sha256}  amendment.json\n".encode(),
    )
    return verify_amendment(
        campaign,
        continuation_source_inventory=continuation_source_inventory,
        continuation_limit_contract=continuation_limit_contract,
        continuation_scheduler_contract=continuation_scheduler_contract,
        checkout_proof=lambda *_args: {},
        verifier_correction_proof=verifier_correction_proof,
        lossless_read_state_proof=lossless_read_state_proof,
    )


def _load_protocol_recovery_static(
    campaign: Path,
    *,
    verifier_correction_proof: VerifierCorrectionProof | None,
    lossless_read_state_proof: LosslessReadStateProof | None,
) -> VerifiedAmendment:
    campaign = campaign.resolve()
    directory = (
        campaign / AMENDMENT_ROOT / PROTOCOL_RECOVERY_AMENDMENT_DIRECTORY
    )
    if directory.is_symlink() or not directory.is_dir():
        raise ContinuationAmendmentError("protocol-recovery amendment is absent")
    entries = list(directory.iterdir())
    if (
        {path.name for path in entries} != PROTOCOL_RECOVERY_AMENDMENT_FILES
        or any(path.is_symlink() or not path.is_file() for path in entries)
    ):
        raise ContinuationAmendmentError(
            "protocol-recovery amendment file inventory is not exact"
        )
    predecessor = _load_verifier_correction_static(
        campaign, proof_callback=None
    )
    amendment_sha256 = _read_amendment_sha(directory)
    amendment = _read_object(directory / "amendment.json")
    exact = {
        "schema",
        "amendment_id",
        "created_utc",
        "campaign_uuid",
        "base_manifest",
        "hash_chain",
        "sidecars",
        "promotion_authorization",
        "retry_authorization",
        "denominator",
        "protocol_recovery",
    }
    manifest, _old_limit, manifest_sha256 = _load_base_unbound(campaign)
    if (
        set(amendment) != exact
        or amendment.get("schema") != PROTOCOL_RECOVERY_AMENDMENT_SCHEMA
        or amendment.get("amendment_id")
        != PROTOCOL_RECOVERY_AMENDMENT_DIRECTORY
        or amendment.get("campaign_uuid") != manifest.get("campaign_uuid")
    ):
        raise ContinuationAmendmentError(
            "protocol-recovery amendment identity differs"
        )
    _parse_utc(amendment.get("created_utc"))
    sidecars = amendment.get("sidecars") or {}
    expected_sidecars = PROTOCOL_RECOVERY_AMENDMENT_FILES - {
        "amendment.json",
        "amendment.sha256",
    }
    if set(sidecars) != expected_sidecars:
        raise ContinuationAmendmentError(
            "protocol-recovery sidecar inventory differs"
        )
    for name, expected_hash in sidecars.items():
        if (
            not _is_sha256(expected_hash)
            or _sha_file(directory / name) != expected_hash
        ):
            raise ContinuationAmendmentError(
                f"protocol-recovery sidecar drifted: {name}"
            )
    if amendment.get("base_manifest") != {
        "path": "campaign_manifest.json",
        "sha256": manifest_sha256,
        "total_runs": manifest["total_runs"],
        "frozen_artifacts": manifest["frozen_artifacts"],
    }:
        raise ContinuationAmendmentError(
            "protocol-recovery base-manifest binding differs"
        )
    predecessor_source = _read_object(
        predecessor.directory / "continuation_source_inventory.json"
    )
    current_source = _read_object(
        directory / "continuation_source_inventory.json"
    )
    changed_paths = _protocol_recovery_source_delta(
        predecessor_source, current_source
    )
    predecessor_limit = _read_object(
        predecessor.directory / "continuation_limit_contract.json"
    )
    current_limit = _read_object(
        directory / "continuation_limit_contract.json"
    )
    predecessor_scheduler = _read_object(
        predecessor.directory / "continuation_scheduler_contract.json"
    )
    scheduler = _read_object(
        directory / "continuation_scheduler_contract.json"
    )
    if (
        current_limit != predecessor_limit
        or scheduler != predecessor_scheduler
        or sidecars["continuation_limit_contract.json"]
        != predecessor.amendment["sidecars"][
            "continuation_limit_contract.json"
        ]
        or sidecars["continuation_scheduler_contract.json"]
        != predecessor.amendment["sidecars"][
            "continuation_scheduler_contract.json"
        ]
    ):
        raise ContinuationAmendmentError(
            "protocol recovery changed predecessor limit/scheduler bytes"
        )
    validate_scheduler_contract(scheduler, manifest)
    chain = amendment.get("hash_chain") or {}
    if chain != {
        "predecessor_amendment_sha256": predecessor.amendment_sha256,
        "old": dict(predecessor.amendment["hash_chain"]["new"]),
        "new": {
            "source_inventory_sha256": sidecars[
                "continuation_source_inventory.json"
            ],
            "limit_contract_sha256": sidecars[
                "continuation_limit_contract.json"
            ],
            "scheduler_contract_sha256": sidecars[
                "continuation_scheduler_contract.json"
            ],
        },
    }:
        raise ContinuationAmendmentError(
            "protocol-recovery hash chain differs"
        )
    checkpoint = _read_object(directory / "recovery_checkpoint.json")
    _verify_protocol_recovery_checkpoint(
        campaign,
        checkpoint,
        predecessor,
        verifier_correction_proof,
        lossless_read_state_proof,
    )
    corrections = checkpoint["verifier_corrections"]
    retries = checkpoint["authorized_retry_attempts"]
    if amendment.get("promotion_authorization") != {
        "run_indices": [item["run_index"] for item in corrections],
        "inherited_run_indices": sorted(
            PROTOCOL_RECOVERY_INHERITED_CORRECTION_INDICES
        ),
        "fresh_run_indices": [PROTOCOL_RECOVERY_CORRECTION_INDEX],
        "attempts": {
            str(item["run_index"]): item["attempt"]
            for item in corrections
        },
        "completion_receipt_sha256": {
            str(item["run_index"]): item["completion_sha256"]
            for item in corrections
        },
        "scope": "inherited_byte_identical_plus_fresh_hash_bound_replay",
    }:
        raise ContinuationAmendmentError(
            "protocol-recovery promotion authorization differs"
        )
    if amendment.get("retry_authorization") != {
        "from_attempt": 1,
        "to_attempt": 2,
        "run_indices": [PROTOCOL_RECOVERY_RUN_INDEX],
        "completion_receipt_sha256": {
            str(PROTOCOL_RECOVERY_RUN_INDEX): retries[0]["completion_sha256"]
        },
        "scope": "exact_hash_allowlist_lossless_read_state_only",
    }:
        raise ContinuationAmendmentError(
            "protocol-recovery retry authorization differs"
        )
    if amendment.get("denominator") != {
        "manifest_rows": manifest["total_runs"],
        "row_identity": "campaign_manifest.runs[].index",
        "preserved_checkpoint_rows": len(
            checkpoint["preserved_terminal_results"]
        ),
        "promoted_attempts_retained_byte_identical": len(corrections),
        "superseded_attempts_retained_for_audit": len(retries),
        "added_rows": 0,
        "counting_rule": PROTOCOL_RECOVERY_COUNTING_RULE,
    }:
        raise ContinuationAmendmentError(
            "protocol-recovery denominator contract differs"
        )
    if amendment.get("protocol_recovery") != {
        "cause": "single_lossy_read_state_delivery_harness_defect",
        "changed_source_paths": changed_paths,
        "raw_invalid_indices": sorted(PROTOCOL_RECOVERY_RAW_INVALID_INDICES),
        "effective_invalid_indices": sorted(
            PROTOCOL_RECOVERY_EFFECTIVE_INVALID_INDICES
        ),
        "inherited_correction_indices": sorted(
            PROTOCOL_RECOVERY_INHERITED_CORRECTION_INDICES
        ),
        "fresh_correction_indices": [PROTOCOL_RECOVERY_CORRECTION_INDEX],
        "retry_indices": [PROTOCOL_RECOVERY_RUN_INDEX],
        "benchmark_environment_changed": False,
        "catalog_tasks_or_steering_changed": False,
        "non_target_agent_behavior_changed": False,
        "action_results_limit_changed": False,
        "lossless_activation": "run_598_attempt_2_only",
    }:
        raise ContinuationAmendmentError(
            "protocol-recovery declared scope differs"
        )
    return VerifiedAmendment(
        campaign=campaign,
        directory=directory,
        amendment=amendment,
        checkpoint=checkpoint,
        scheduler=scheduler,
        amendment_sha256=amendment_sha256,
        scheduler_sha256=sidecars["continuation_scheduler_contract.json"],
    )


_PROTOCOL_POSTMORTEM_RETRY_ITEM_KEYS = frozenset({
    "run_index",
    "run_id",
    "attempt",
    "pid",
    "finished_utc",
    "launch_receipt",
    "launch_sha256",
    "completion_receipt",
    "completion_sha256",
    "classification",
    "cache_nonce",
    "predecessor_attestation",
    "authorization_basis",
    "authoritative_artifacts",
    "scratch_cleanup_receipt",
    "scratch_cleanup_sha256",
    "postmortem_proof",
    "terminal_artifacts_absent",
    "superseded_by_attempt",
})


def _protocol_postmortem_attempt_two_absent(
    campaign: Path, row: Mapping[str, Any]
) -> bool:
    run_id = str(row.get("run_id", ""))
    for attempt_root in (campaign / "runs").glob("attempt_*"):
        try:
            attempt = int(attempt_root.name.removeprefix("attempt_"))
        except ValueError:
            return False
        if attempt >= 2 and attempt_root.is_symlink():
            return False
        target = attempt_root / run_id
        if attempt >= 2 and (target.exists() or target.is_symlink()):
            return False
    for attempt in range(2, 7):
        stem = (
            f"{int(row.get('index', -1)):04d}_{row.get('cell_name', '')}."
            f"attempt{attempt}"
        )
        for root, suffix in (
            (campaign / "launch_receipts", ".launch.json"),
            (campaign / "completion_receipts", ".complete.json"),
            (campaign / "scratch_cleanup_receipts", ".scratch.json"),
        ):
            exact = root / f"{stem}{suffix}"
            if exact.exists() or exact.is_symlink():
                return False
    attempt_one_stem = (
        f"{int(row.get('index', -1)):04d}_{row.get('cell_name', '')}.attempt1"
    )
    attempt_one_launch = (
        campaign / "launch_receipts" / f"{attempt_one_stem}.launch.json"
    )
    if attempt_one_launch.is_file() and not attempt_one_launch.is_symlink():
        launch = _read_object(attempt_one_launch)
        provenance = launch.get("scratch_provenance")
        if isinstance(provenance, dict) and isinstance(provenance.get("path"), str):
            attempt_one_scratch = Path(provenance["path"])
            if attempt_one_scratch.name != (
                f"r{int(row.get('index', -1)):04d}a1"
            ):
                return False
            prefix = f"r{int(row.get('index', -1)):04d}a"
            for candidate in attempt_one_scratch.parent.glob(f"{prefix}*"):
                suffix = candidate.name.removeprefix(prefix)
                if suffix.isdigit() and int(suffix) >= 2:
                    return False
    for root, suffix in (
        (campaign / "launch_receipts", ".launch.json"),
        (campaign / "completion_receipts", ".complete.json"),
        (campaign / "scratch_cleanup_receipts", ".scratch.json"),
    ):
        if not root.exists():
            continue
        for path in root.glob(f"*.attempt*{suffix}"):
            try:
                payload = _read_object(path)
            except ContinuationAmendmentError:
                return False
            if (
                payload.get("run_index") == row.get("index")
                and type(payload.get("attempt")) is int
                and payload["attempt"] >= 2
            ):
                return False
    return True


def _validate_protocol_postmortem_proof(
    value: Any,
    *,
    campaign: Path,
    row: Mapping[str, Any],
    out_dir: Path,
    launch: Mapping[str, Any],
    completion_sha256: str,
    artifacts: Mapping[str, str],
) -> None:
    """Validate the exact current-state proof without claiming ancestry."""

    exact = {
        "schema", "run_index", "run_id", "attempt",
        "immutable_completion_sha256", "operator_intervention",
        "benchmark_or_harness_changed", "inactivity_timeout_policy_created",
        "trajectory_examined_before_intervention",
        "preconfigured_timeout_did_not_fire", "selection_bias_risk",
        "no_checkout", "process_absence", "scratch_quarantine",
        "confirmed_recoverable", "error",
    }
    no_checkout = value.get("no_checkout") if isinstance(value, dict) else None
    absence = value.get("process_absence") if isinstance(value, dict) else None
    quarantine = (
        value.get("scratch_quarantine") if isinstance(value, dict) else None
    )
    nonce = launch.get("cache_nonce")
    provenance = launch.get("scratch_provenance")
    expected_db = out_dir / f"{row['env']}_{row['port']}.db"
    absence_keys = {
        "worker_pid", "launch_pid_start_ticks", "cache_nonce_sha256",
        "launch_identity_absent", "exact_nonce_pids",
        "worker_scope_snapshot", "confirmed_absent", "error",
    }
    quarantine_keys = {
        "schema", "policy", "path", "provenance_sha256", "marker_sha256",
        "device", "inode", "owner_uid", "mode", "entries", "bytes",
        "tree_sha256", "readable_holder_pids", "confirmed_quarantined",
        "error",
    }
    if (
        not isinstance(value, dict)
        or set(value) != exact
        or value.get("schema") != POSTMORTEM_PROOF_SCHEMA
        or value.get("run_index") != row.get("index")
        or value.get("run_id") != row.get("run_id")
        or value.get("attempt") != 1
        or value.get("immutable_completion_sha256") != completion_sha256
        or value.get("operator_intervention") is not True
        or value.get("benchmark_or_harness_changed") is not False
        or value.get("inactivity_timeout_policy_created") is not False
        or value.get("trajectory_examined_before_intervention") is not True
        or value.get("preconfigured_timeout_did_not_fire") is not True
        or value.get("selection_bias_risk") is not True
        or value.get("confirmed_recoverable") is not True
        or value.get("error") is not None
        or not isinstance(no_checkout, dict)
        or no_checkout.get("schema") != "agentarena.clone8-no-checkout-proof.v1"
        or no_checkout.get("env") != row.get("env")
        or no_checkout.get("db_path") != str(expected_db.resolve())
        or no_checkout.get("db_sha256") != artifacts.get(expected_db.name)
        or no_checkout.get("sidecars_absent") is not True
        or no_checkout.get("hash_stable") is not True
        or no_checkout.get("readable_fd_holder_pids_before") != []
        or no_checkout.get("readable_fd_holder_pids_after") != []
        or no_checkout.get("quick_check") != ["ok"]
        or no_checkout.get("confirmed_no_checkout") is not True
        or no_checkout.get("error") is not None
        or not isinstance(absence, dict)
        or set(absence) != absence_keys
        or absence.get("worker_pid") != launch.get("pid")
        or absence.get("launch_pid_start_ticks")
        != launch.get("pid_start_ticks")
        or absence.get("cache_nonce_sha256")
        != (_sha_bytes(nonce.encode()) if isinstance(nonce, str) else None)
        or absence.get("launch_identity_absent") is not True
        or absence.get("exact_nonce_pids") != []
        or absence.get("worker_scope_snapshot") != []
        or absence.get("confirmed_absent") is not True
        or absence.get("error") is not None
        or not isinstance(provenance, dict)
        or not isinstance(quarantine, dict)
        or set(quarantine) != quarantine_keys
        or quarantine.get("schema")
        != "agentarena.clone8-retained-scratch-quarantine.v1"
        or quarantine.get("policy")
        != "retain_in_place_until_terminal_audit"
        or quarantine.get("path") != provenance.get("path")
        or quarantine.get("provenance_sha256")
        != _sha_bytes(_canonical_bytes(provenance))
        or quarantine.get("device") != provenance.get("device")
        or quarantine.get("inode") != provenance.get("inode")
        or quarantine.get("owner_uid") != provenance.get("owner_uid")
        or quarantine.get("mode") != provenance.get("mode")
        or not _is_sha256(quarantine.get("marker_sha256"))
        or type(quarantine.get("entries")) is not int
        or quarantine.get("entries", -1) < 1
        or type(quarantine.get("bytes")) is not int
        or quarantine.get("bytes", -1) < 0
        or quarantine.get("tree_sha256")
        != PROTOCOL_POSTMORTEM_SCRATCH_TREE_SHA256
        or quarantine.get("readable_holder_pids") != []
        or quarantine.get("confirmed_quarantined") is not True
        or quarantine.get("error") is not None
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem proof differs from the exact quiescent evidence"
        )


def _protocol_postmortem_retry_item(
    campaign: Path,
    manifest: Mapping[str, Any],
    predecessor: VerifiedAmendment,
    row: Mapping[str, Any],
    completion_path: Path,
    proof_callback: PostmortemProof,
) -> dict[str, Any]:
    index = row["index"]
    stem = f"{index:04d}_{row['cell_name']}.attempt1"
    expected_completion = campaign / "completion_receipts" / f"{stem}.complete.json"
    launch_path = campaign / "launch_receipts" / f"{stem}.launch.json"
    completion = _read_object(completion_path)
    launch = _read_object(launch_path)
    out_dir = _attempt_out_dir(campaign, row["run_id"], 1)
    attestation, nonce = _predecessor_launch_identity(
        campaign, manifest, predecessor, row, 1
    )
    classification = completion.get("classification") or {}
    cleanup = completion.get("worker_scope_cleanup") or {}
    evidence = (
        "numeric PGID/SID members lacked exact nonce certification: "
        f"{list(PROTOCOL_POSTMORTEM_UNCERTIFIED_SCOPE_PIDS)}"
    )
    if (
        completion_path.resolve() != expected_completion.resolve()
        or launch_path.is_symlink()
        or not launch_path.is_file()
        or row.get("index") != PROTOCOL_POSTMORTEM_RUN_INDEX
        or row.get("env") != "airbnb"
        or row.get("model") != "Qwen3.5-122B"
        or row.get("condition") != "clean"
        or launch.get("schema") != "agentarena.clone8-launch-receipt.v3"
        or launch.get("campaign_uuid") != manifest.get("campaign_uuid")
        or launch.get("manifest_sha256")
        != _sha_file(campaign / "campaign_manifest.json")
        or launch.get("run_index") != index
        or launch.get("run_id") != row.get("run_id")
        or launch.get("attempt") != 1
        or Path(str(launch.get("out_dir", ""))).resolve() != out_dir.resolve()
        or launch.get("continuation_attestation") != attestation
        or launch.get("cache_nonce") != nonce
        or type(launch.get("pid_start_ticks")) is not int
        or not isinstance(launch.get("scratch_provenance"), dict)
        or completion.get("schema") != "agentarena.clone8-completion-receipt.v3"
        or completion.get("run_index") != index
        or completion.get("run_id") != row.get("run_id")
        or completion.get("attempt") != 1
        or completion.get("pid") != launch.get("pid")
        or completion.get("returncode") != -9
        or completion.get("no_checkout_proof") is not None
        or completion.get("outcome") is not None
        or completion.get("num_steps") is not None
        or completion.get("preservation_strict") is not None
        or completion.get("literal_hero") is not None
        or classification.get("class") != "scientific_invalid"
        or classification.get("code") != "worker_scope_cleanup_failed"
        or classification.get("evidence") != evidence
        or cleanup.get("schema") != "agentarena.clone8-worker-scope-cleanup.v2"
        or cleanup.get("worker_pid") != launch.get("pid")
        or cleanup.get("launch_pid_start_ticks")
        != launch.get("pid_start_ticks")
        or cleanup.get("cache_nonce_sha256") != _sha_bytes(nonce.encode())
        or cleanup.get("mode") != "controller_exit"
        or cleanup.get("signal_membership")
        != "exact_cache_nonce_and_start_ticks_via_pidfd"
        or cleanup.get("confirmed_empty") is not True
        or cleanup.get("port_released") is not True
        or cleanup.get("remaining_scope_pids") != []
        or cleanup.get("kill_signaled_pids") != []
        or cleanup.get("uncertified_scope_pids")
        != list(PROTOCOL_POSTMORTEM_UNCERTIFIED_SCOPE_PIDS)
        or cleanup.get("error") != evidence
        or (out_dir / "summary.json").exists()
        or (out_dir / "summary.json").is_symlink()
        or (out_dir / "trajectory.json").exists()
        or (out_dir / "trajectory.json").is_symlink()
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem completion differs from the frozen intervention"
        )
    scratch_relative, scratch_sha256, scratch = _checkpoint_scratch_receipt(
        campaign, completion, require_green=False
    )
    provenance = launch["scratch_provenance"]
    if (
        scratch.get("schema") != "agentarena.clone8-scratch-cleanup.v1"
        or scratch.get("campaign_uuid") != manifest.get("campaign_uuid")
        or scratch.get("manifest_sha256")
        != _sha_file(campaign / "campaign_manifest.json")
        or scratch.get("run_index") != index
        or scratch.get("run_id") != row.get("run_id")
        or scratch.get("attempt") != 1
        or scratch.get("path") != provenance.get("path")
        or scratch.get("provenance_sha256")
        != _sha_bytes(_canonical_bytes(provenance))
        or scratch.get("status") != "skipped_worker_scope_not_green"
        or scratch.get("worker_scope_green") is not False
        or scratch.get("readable_holder_pids") is not None
        or scratch.get("confirmed_absent") is not False
        or scratch.get("error")
        != "worker scope was not proven empty; scratch retained"
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem scratch receipt differs"
        )
    artifacts = _recovery_artifact_hashes(out_dir, row)
    launch_sha256 = _sha_file(launch_path)
    completion_sha256 = _sha_file(completion_path)
    if completion_sha256 != PROTOCOL_POSTMORTEM_COMPLETION_SHA256:
        raise ContinuationAmendmentError(
            "protocol-postmortem completion hash differs from the frozen receipt"
        )
    proof = dict(proof_callback(
        campaign,
        _predecessor_active_manifest(manifest, predecessor),
        out_dir,
        row,
        launch,
        completion,
    ))
    _validate_protocol_postmortem_proof(
        proof,
        campaign=campaign,
        row=row,
        out_dir=out_dir,
        launch=launch,
        completion_sha256=completion_sha256,
        artifacts=artifacts,
    )
    if (
        _sha_file(launch_path) != launch_sha256
        or _sha_file(completion_path) != completion_sha256
        or _sha_file(_safe_path(campaign, scratch_relative)) != scratch_sha256
        or _recovery_artifact_hashes(out_dir, row) != artifacts
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem proof changed immutable evidence"
        )
    return {
        "run_index": index,
        "run_id": row["run_id"],
        "attempt": 1,
        "pid": launch.get("pid"),
        "finished_utc": completion.get("finished_utc"),
        "launch_receipt": _relative(campaign, launch_path),
        "launch_sha256": launch_sha256,
        "completion_receipt": _relative(campaign, completion_path),
        "completion_sha256": completion_sha256,
        "classification": classification,
        "cache_nonce": nonce,
        "predecessor_attestation": attestation,
        "authorization_basis": "postmortem_operator_sigkill",
        "authoritative_artifacts": artifacts,
        "scratch_cleanup_receipt": scratch_relative,
        "scratch_cleanup_sha256": scratch_sha256,
        "postmortem_proof": proof,
        "terminal_artifacts_absent": ["summary.json", "trajectory.json"],
        "superseded_by_attempt": 2,
    }


def _protocol_postmortem_receipt_aggregate(paths: list[Path]) -> str:
    """Reproduce the frozen repository-relative sha256sum aggregate."""

    root = PROTOCOL_POSTMORTEM_RECEIPT_AGGREGATE_ROOT.resolve()
    records = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ContinuationAmendmentError(
                "protocol-postmortem receipt aggregate contains an unsafe path"
            )
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except ValueError as exc:
            raise ContinuationAmendmentError(
                "protocol-postmortem receipt aggregate path is outside its root"
            ) from exc
        records.append(
            f"{_sha_file(path)}  {relative}\n".encode("utf-8")
        )
    return _sha_bytes(b"".join(sorted(records)))


def _protocol_postmortem_frontier_attestation(
    *,
    present_indices: set[int],
    launch_paths: list[Path],
    completion_paths: list[Path],
    scratch_paths: list[Path],
) -> dict[str, Any]:
    aggregates = {
        "launch_receipts": _protocol_postmortem_receipt_aggregate(launch_paths),
        "completion_receipts": _protocol_postmortem_receipt_aggregate(
            completion_paths
        ),
        "scratch_cleanup_receipts": _protocol_postmortem_receipt_aggregate(
            scratch_paths
        ),
    }
    expected_aggregates = {
        "launch_receipts": PROTOCOL_POSTMORTEM_LAUNCH_RECEIPT_AGGREGATE_SHA256,
        "completion_receipts": (
            PROTOCOL_POSTMORTEM_COMPLETION_RECEIPT_AGGREGATE_SHA256
        ),
        "scratch_cleanup_receipts": (
            PROTOCOL_POSTMORTEM_SCRATCH_RECEIPT_AGGREGATE_SHA256
        ),
    }
    expected_present = set(range(PROTOCOL_POSTMORTEM_MANIFEST_ROWS)) - set(
        PROTOCOL_POSTMORTEM_MISSING_PRIMARY_INDICES
    )
    if (
        present_indices != expected_present
        or aggregates != expected_aggregates
        or len(launch_paths) != PROTOCOL_POSTMORTEM_FRONTIER_ROWS
        or len(completion_paths) != PROTOCOL_POSTMORTEM_FRONTIER_ROWS
        or len(scratch_paths) != PROTOCOL_POSTMORTEM_FRONTIER_ROWS
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem receipt frontier attestation differs"
        )
    return {
        "present_indices": sorted(expected_present),
        "missing_primary_indices": sorted(
            PROTOCOL_POSTMORTEM_MISSING_PRIMARY_INDICES
        ),
        "receipt_aggregate_sha256": aggregates,
        "index_642_completion_sha256": (
            PROTOCOL_POSTMORTEM_COMPLETION_SHA256
        ),
    }


def _protocol_postmortem_counts(
    *,
    raw_classes: Counter[str],
    selected_classes: Counter[str],
    selected_by_condition: Counter[str],
) -> dict[str, Any]:
    effective = Counter(raw_classes)
    effective["scientific_invalid"] -= len(PROTOCOL_POSTMORTEM_CORRECTION_INDICES)
    effective["scored"] += len(PROTOCOL_POSTMORTEM_CORRECTION_INDICES)
    effective = +effective
    return {
        "checkpoint_completion_receipts": PROTOCOL_POSTMORTEM_FRONTIER_ROWS,
        "preserved_terminal_rows": PROTOCOL_POSTMORTEM_FRONTIER_ROWS - 2,
        "authorized_retry_rows": 2,
        "inherited_authorized_retry_rows": 1,
        "fresh_authorized_retry_rows": 1,
        "verifier_corrected_rows": 3,
        "inherited_verifier_corrected_rows": 3,
        "fresh_verifier_corrected_rows": 0,
        "raw_recorded_classes": dict(sorted(raw_classes.items())),
        "effective_pre_retry_classes": dict(sorted(effective.items())),
        "selected_frontier_classes": dict(sorted(selected_classes.items())),
        "selected_frontier_by_condition": dict(
            sorted(selected_by_condition.items())
        ),
        "raw_invalid_indices": sorted(PROTOCOL_POSTMORTEM_RAW_INVALID_INDICES),
        "effective_invalid_indices": sorted(
            PROTOCOL_POSTMORTEM_EFFECTIVE_INVALID_INDICES
        ),
        "unresolved_invalid_indices": sorted(
            PROTOCOL_POSTMORTEM_UNRESOLVED_INVALID_INDICES
        ),
        "missing_primary_indices": sorted(
            PROTOCOL_POSTMORTEM_MISSING_PRIMARY_INDICES
        ),
        "pending_primary_rows": PROTOCOL_POSTMORTEM_PENDING_PRIMARY_ROWS,
    }


def _collect_protocol_postmortem_checkpoint(
    campaign: Path,
    manifest: Mapping[str, Any],
    predecessor: VerifiedAmendment,
    postmortem_proof: PostmortemProof,
) -> dict[str, Any]:
    status_path = _safe_path(campaign, "status.json")
    status = _read_object(status_path)
    completions = sorted((campaign / "completion_receipts").glob("*.complete.json"))
    launches = sorted((campaign / "launch_receipts").glob("*.launch.json"))
    scratches = sorted(
        (campaign / "scratch_cleanup_receipts").glob("*.scratch.json")
    )
    completion_stems = {p.name.removesuffix(".complete.json") for p in completions}
    launch_stems = {p.name.removesuffix(".launch.json") for p in launches}
    scratch_stems = {p.name.removesuffix(".scratch.json") for p in scratches}
    if (
        manifest.get("total_runs") != PROTOCOL_POSTMORTEM_MANIFEST_ROWS
        or status.get("state") != "protocol_invalid"
        or status.get("running") != []
        or status.get("running_count") != 0
        or status.get("pending_primary")
        != PROTOCOL_POSTMORTEM_PENDING_PRIMARY_ROWS
        or status.get("pending_refill") != 1
        or status.get("final_count") != PROTOCOL_POSTMORTEM_FRONTIER_ROWS - 1
        or status.get("total_runs") != PROTOCOL_POSTMORTEM_MANIFEST_ROWS
        or status.get(STATUS_AMENDMENT_FIELD) != predecessor.amendment_sha256
        or status.get(STATUS_SCHEDULER_FIELD) != predecessor.scheduler_sha256
        or len(completions) != PROTOCOL_POSTMORTEM_FRONTIER_ROWS
        or len(launches) != PROTOCOL_POSTMORTEM_FRONTIER_ROWS
        or len(scratches) != PROTOCOL_POSTMORTEM_FRONTIER_ROWS
        or completion_stems != launch_stems
        or completion_stems != scratch_stems
    ):
        raise ContinuationAmendmentError(
            "protocol postmortem recovery requires the exact quiescent frontier"
        )
    predecessor_corrections = predecessor.correction_by_index
    predecessor_retries = predecessor.retry_by_index
    predecessor_preserved = {
        int(item["run_index"]): item
        for item in predecessor.checkpoint["preserved_terminal_results"]
    }
    if (
        frozenset(predecessor_corrections)
        != PROTOCOL_POSTMORTEM_CORRECTION_INDICES
        or frozenset(predecessor_retries)
        != PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem predecessor authority differs"
        )
    for index in (
        PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES
        | {PROTOCOL_POSTMORTEM_RUN_INDEX}
    ):
        if not _protocol_postmortem_attempt_two_absent(
            campaign, manifest["runs"][index]
        ):
            raise ContinuationAmendmentError(
                "protocol-postmortem attempt 2 or later predates its amendment"
            )

    preserved: list[dict[str, Any]] = []
    raw_classes: Counter[str] = Counter()
    raw_invalid: set[int] = set()
    selected_by_condition: Counter[str] = Counter()
    seen: set[int] = set()
    fresh_retry: dict[str, Any] | None = None
    for completion_path in completions:
        raw = _read_object(completion_path)
        index = raw.get("run_index")
        if (
            type(index) is not int
            or index < 0
            or index >= len(manifest["runs"])
            or index in seen
            or raw.get("attempt") != 1
        ):
            raise ContinuationAmendmentError(
                "protocol-postmortem completion rows are not exact and unique"
            )
        seen.add(index)
        row = manifest["runs"][index]
        result_class = (raw.get("classification") or {}).get("class")
        raw_classes[result_class] += 1
        if result_class == "scientific_invalid":
            raw_invalid.add(index)
        elif result_class not in {"scored", "behavioral"}:
            raise ContinuationAmendmentError(
                "protocol-postmortem frontier has an unsupported result class"
            )
        if index == PROTOCOL_POSTMORTEM_RUN_INDEX:
            fresh_retry = _protocol_postmortem_retry_item(
                campaign, manifest, predecessor, row, completion_path,
                postmortem_proof,
            )
            selected_by_condition[str(row.get("condition"))] += 1
            continue
        item, _launch, completion, _out_dir = _correction_artifact_item(
            campaign, manifest, row, completion_path
        )
        scratch_relative, scratch_sha, _scratch = _checkpoint_scratch_receipt(
            campaign, completion, require_green=True
        )
        if (
            item.get("scratch_cleanup_receipt") != scratch_relative
            or item.get("scratch_cleanup_sha256") != scratch_sha
        ):
            raise ContinuationAmendmentError(
                "protocol-postmortem scratch binding differs"
            )
        if index in predecessor_preserved and predecessor_preserved[index] != item:
            raise ContinuationAmendmentError(
                "protocol-postmortem changed a predecessor preserved record"
            )
        if index in PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES:
            if _protocol_base_item(predecessor_retries[index]) != item:
                raise ContinuationAmendmentError(
                    "protocol-postmortem changed predecessor retry evidence"
                )
            continue
        preserved.append(item)
        selected_by_condition[str(row.get("condition"))] += 1
    if (
        raw_invalid != PROTOCOL_POSTMORTEM_RAW_INVALID_INDICES
        or fresh_retry is None
        or set(range(PROTOCOL_POSTMORTEM_MANIFEST_ROWS)) - seen
        != set(PROTOCOL_POSTMORTEM_MISSING_PRIMARY_INDICES)
        or not set(predecessor_preserved) <= {
            item["run_index"] for item in preserved
        }
        or len(preserved) != PROTOCOL_POSTMORTEM_FRONTIER_ROWS - 2
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem frozen frontier inventory differs"
        )
    effective_invalid = raw_invalid - set(predecessor_corrections)
    unresolved = effective_invalid - set(predecessor_retries)
    if (
        effective_invalid != PROTOCOL_POSTMORTEM_EFFECTIVE_INVALID_INDICES
        or unresolved != PROTOCOL_POSTMORTEM_UNRESOLVED_INVALID_INDICES
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem invalid authority composition differs"
        )
    effective_classes = Counter(raw_classes)
    effective_classes["scientific_invalid"] -= len(predecessor_corrections)
    effective_classes["scored"] += len(predecessor_corrections)
    selected_classes = +effective_classes
    for retry in predecessor_retries.values():
        selected_classes[(retry.get("classification") or {}).get("class")] -= 1
    selected_classes = +selected_classes
    if (
        status.get("final_classes") != dict(selected_classes)
        or status.get("final_by_condition") != dict(selected_by_condition)
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem status selection differs from receipts"
        )
    retries = [
        predecessor_retries[index]
        for index in sorted(PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES)
    ] + [fresh_retry]
    frontier_attestation = _protocol_postmortem_frontier_attestation(
        present_indices=seen,
        launch_paths=launches,
        completion_paths=completions,
        scratch_paths=scratches,
    )
    return {
        "schema": PROTOCOL_POSTMORTEM_CHECKPOINT_SCHEMA,
        "campaign_uuid": manifest["campaign_uuid"],
        "manifest_sha256": _sha_file(campaign / "campaign_manifest.json"),
        "predecessor_amendment_sha256": predecessor.amendment_sha256,
        "frontier_status_snapshot": status,
        "frontier_status_file_sha256": _sha_file(status_path),
        "frontier_attestation": frontier_attestation,
        "counts": _protocol_postmortem_counts(
            raw_classes=raw_classes,
            selected_classes=selected_classes,
            selected_by_condition=selected_by_condition,
        ),
        "preserved_terminal_results": sorted(
            preserved, key=lambda item: item["run_index"]
        ),
        "authorized_retry_attempts": retries,
        "verifier_corrections": [
            predecessor_corrections[index]
            for index in sorted(PROTOCOL_POSTMORTEM_CORRECTION_INDICES)
        ],
    }


def _verify_protocol_postmortem_checkpoint(
    campaign: Path,
    checkpoint: Mapping[str, Any],
    predecessor: VerifiedAmendment,
    postmortem_proof: PostmortemProof | None,
) -> None:
    exact = {
        "schema", "campaign_uuid", "manifest_sha256",
        "predecessor_amendment_sha256", "frontier_status_snapshot",
        "frontier_status_file_sha256", "frontier_attestation", "counts",
        "preserved_terminal_results", "authorized_retry_attempts",
        "verifier_corrections",
    }
    manifest = _read_object(campaign / "campaign_manifest.json")
    if (
        not isinstance(checkpoint, dict)
        or set(checkpoint) != exact
        or checkpoint.get("schema") != PROTOCOL_POSTMORTEM_CHECKPOINT_SCHEMA
        or checkpoint.get("campaign_uuid") != manifest.get("campaign_uuid")
        or checkpoint.get("manifest_sha256")
        != _sha_file(campaign / "campaign_manifest.json")
        or checkpoint.get("predecessor_amendment_sha256")
        != predecessor.amendment_sha256
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem checkpoint identity differs"
        )
    preserved = checkpoint.get("preserved_terminal_results")
    retries = checkpoint.get("authorized_retry_attempts")
    corrections = checkpoint.get("verifier_corrections")
    if (
        not isinstance(preserved, list)
        or not isinstance(retries, list)
        or not isinstance(corrections, list)
        or len(retries) != 2
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem checkpoint inventories are malformed"
        )
    predecessor_preserved = {
        int(item["run_index"]): item
        for item in predecessor.checkpoint["preserved_terminal_results"]
    }
    predecessor_retries = predecessor.retry_by_index
    if (
        frozenset(predecessor.correction_by_index)
        != PROTOCOL_POSTMORTEM_CORRECTION_INDICES
        or corrections != [
            predecessor.correction_by_index[index]
            for index in sorted(PROTOCOL_POSTMORTEM_CORRECTION_INDICES)
        ]
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem inherited corrections differ"
        )
    raw_classes: Counter[str] = Counter()
    raw_invalid: set[int] = set()
    selected_by_condition: Counter[str] = Counter()
    seen: set[int] = set()
    for item in preserved:
        if not isinstance(item, dict) or set(item) != _PROTOCOL_BASE_ITEM_KEYS:
            raise ContinuationAmendmentError(
                "protocol-postmortem preserved record schema differs"
            )
        index = item.get("run_index")
        if (
            type(index) is not int
            or index < 0
            or index >= len(manifest["runs"])
            or index in seen
            or index in (
                PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES
                | {PROTOCOL_POSTMORTEM_RUN_INDEX}
            )
        ):
            raise ContinuationAmendmentError(
                "protocol-postmortem preserved rows are not exact and unique"
            )
        seen.add(index)
        row = manifest["runs"][index]
        if (
            item.get("run_id") != row.get("run_id")
            or item.get("env") != row.get("env")
            or item.get("attempt") != 1
            or item.get("scratch_cleanup_receipt") is None
            or index in predecessor_preserved
            and item != predecessor_preserved[index]
        ):
            raise ContinuationAmendmentError(
                "protocol-postmortem preserved identity differs"
            )
        _verify_correction_item_hashes(campaign, item)
        completion = _read_object(
            _safe_path(campaign, str(item["completion_receipt"]))
        )
        if (
            completion.get("classification") != item.get("classification")
            or not _green_cleanup(completion)
        ):
            raise ContinuationAmendmentError(
                "protocol-postmortem preserved completion drifted"
            )
        _checkpoint_scratch_receipt(campaign, completion, require_green=True)
        result_class = (item.get("classification") or {}).get("class")
        if result_class not in {"scored", "behavioral", "scientific_invalid"}:
            raise ContinuationAmendmentError(
                "protocol-postmortem preserved class differs"
            )
        raw_classes[result_class] += 1
        if result_class == "scientific_invalid":
            raw_invalid.add(index)
        selected_by_condition[str(row.get("condition"))] += 1
    if not set(predecessor_preserved) <= seen:
        raise ContinuationAmendmentError(
            "protocol-postmortem omitted a predecessor preserved record"
        )

    checkpointed_indices = set(seen)

    retry_by_index: dict[int, Mapping[str, Any]] = {}
    for item in retries:
        index = item.get("run_index") if isinstance(item, dict) else None
        if type(index) is not int or index in seen or index in retry_by_index:
            raise ContinuationAmendmentError(
                "protocol-postmortem retry rows are not exact and unique"
            )
        retry_by_index[index] = item
        checkpointed_indices.add(index)
        if index in PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES:
            if item != predecessor_retries.get(index):
                raise ContinuationAmendmentError(
                    "protocol-postmortem changed predecessor retry authority"
                )
        elif index == PROTOCOL_POSTMORTEM_RUN_INDEX:
            if set(item) != _PROTOCOL_POSTMORTEM_RETRY_ITEM_KEYS:
                raise ContinuationAmendmentError(
                    "protocol-postmortem fresh retry schema differs"
                )
            row = manifest["runs"][index]
            completion_path = _safe_path(
                campaign, str(item.get("completion_receipt", ""))
            )
            callback = postmortem_proof
            if callback is None:
                frozen_proof = item.get("postmortem_proof")

                def callback(*_args, frozen=frozen_proof):
                    return frozen

            rebuilt = _protocol_postmortem_retry_item(
                campaign, manifest, predecessor, row, completion_path, callback
            )
            if rebuilt != item:
                raise ContinuationAmendmentError(
                    "fresh protocol-postmortem proof or evidence differs"
                )
            selected_by_condition[str(row.get("condition"))] += 1
        else:
            raise ContinuationAmendmentError(
                "protocol-postmortem retry allowlist differs"
            )
        result_class = (item.get("classification") or {}).get("class")
        raw_classes[result_class] += 1
        if result_class == "scientific_invalid":
            raw_invalid.add(index)
    if frozenset(retry_by_index) != (
        PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES
        | {PROTOCOL_POSTMORTEM_RUN_INDEX}
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem retry inventory differs"
        )
    if (
        set(range(PROTOCOL_POSTMORTEM_MANIFEST_ROWS)) - checkpointed_indices
        != set(PROTOCOL_POSTMORTEM_MISSING_PRIMARY_INDICES)
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem missing-primary identity set differs"
        )
    effective_invalid = raw_invalid - set(PROTOCOL_POSTMORTEM_CORRECTION_INDICES)
    unresolved = effective_invalid - set(PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES)
    if (
        raw_invalid != PROTOCOL_POSTMORTEM_RAW_INVALID_INDICES
        or effective_invalid != PROTOCOL_POSTMORTEM_EFFECTIVE_INVALID_INDICES
        or unresolved != PROTOCOL_POSTMORTEM_UNRESOLVED_INVALID_INDICES
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem invalid-set composition differs"
        )
    effective_classes = Counter(raw_classes)
    effective_classes["scientific_invalid"] -= len(
        PROTOCOL_POSTMORTEM_CORRECTION_INDICES
    )
    effective_classes["scored"] += len(PROTOCOL_POSTMORTEM_CORRECTION_INDICES)
    selected_classes = +effective_classes
    for index in PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES:
        selected_classes[
            (retry_by_index[index].get("classification") or {}).get("class")
        ] -= 1
    selected_classes = +selected_classes
    status = checkpoint.get("frontier_status_snapshot") or {}
    expected_counts = _protocol_postmortem_counts(
        raw_classes=raw_classes,
        selected_classes=selected_classes,
        selected_by_condition=selected_by_condition,
    )
    if (
        checkpoint.get("counts") != expected_counts
        or checkpoint.get("frontier_status_file_sha256")
        != _sha_bytes(_json_bytes(status))
        or len(preserved) != PROTOCOL_POSTMORTEM_FRONTIER_ROWS - 2
        or len(preserved) + len(retries) != PROTOCOL_POSTMORTEM_FRONTIER_ROWS
        or status.get("state") != "protocol_invalid"
        or status.get("total_runs") != PROTOCOL_POSTMORTEM_MANIFEST_ROWS
        or status.get("running") != []
        or status.get("running_count") != 0
        or status.get("pending_primary")
        != PROTOCOL_POSTMORTEM_PENDING_PRIMARY_ROWS
        or status.get("pending_refill") != 1
        or status.get("final_count") != PROTOCOL_POSTMORTEM_FRONTIER_ROWS - 1
        or status.get("final_classes") != dict(selected_classes)
        or status.get("final_by_condition") != dict(selected_by_condition)
        or status.get(STATUS_AMENDMENT_FIELD) != predecessor.amendment_sha256
        or status.get(STATUS_SCHEDULER_FIELD) != predecessor.scheduler_sha256
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem checkpoint frontier differs"
        )
    all_items = [*preserved, *retries]
    fresh_attestation = _protocol_postmortem_frontier_attestation(
        present_indices=checkpointed_indices,
        launch_paths=[
            _safe_path(campaign, str(item["launch_receipt"]))
            for item in all_items
        ],
        completion_paths=[
            _safe_path(campaign, str(item["completion_receipt"]))
            for item in all_items
        ],
        scratch_paths=[
            _safe_path(campaign, str(item["scratch_cleanup_receipt"]))
            for item in all_items
        ],
    )
    if checkpoint.get("frontier_attestation") != fresh_attestation:
        raise ContinuationAmendmentError(
            "protocol-postmortem frontier attestation drifted"
        )


def _protocol_postmortem_declared_scope(changed_paths: list[str]) -> dict[str, Any]:
    return {
        "cause": "operator_initiated_sigkill_during_forensic_stall_investigation",
        "operator_intervention": True,
        "benchmark_environment_changed": False,
        "benchmark_or_harness_changed": False,
        "experiment_configuration_changed": False,
        "inactivity_timeout_fired": False,
        "inactivity_timeout_policy_created": False,
        "preconfigured_timeout_did_not_fire": True,
        "exact_descendant_ancestry_claimed": False,
        "trajectory_examined_before_intervention": True,
        "selection_bias_risk": True,
        "changed_source_paths": changed_paths,
        "raw_invalid_indices": sorted(PROTOCOL_POSTMORTEM_RAW_INVALID_INDICES),
        "effective_invalid_indices": sorted(
            PROTOCOL_POSTMORTEM_EFFECTIVE_INVALID_INDICES
        ),
        "unresolved_invalid_indices": sorted(
            PROTOCOL_POSTMORTEM_UNRESOLVED_INVALID_INDICES
        ),
        "inherited_correction_indices": sorted(
            PROTOCOL_POSTMORTEM_CORRECTION_INDICES
        ),
        "fresh_correction_indices": [],
        "inherited_retry_indices": sorted(
            PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES
        ),
        "fresh_retry_indices": [PROTOCOL_POSTMORTEM_RUN_INDEX],
        "scratch_disposition": "retain_in_place_until_terminal_audit",
        "scratch_tree_sha256": PROTOCOL_POSTMORTEM_SCRATCH_TREE_SHA256,
        "catalog_tasks_or_steering_changed": False,
        "lossless_activation": "run_598_attempt_2_only",
    }


def create_protocol_postmortem_recovery_amendment(
    campaign: Path,
    *,
    continuation_source_inventory: Mapping[str, Any],
    continuation_limit_contract: Mapping[str, Any],
    continuation_scheduler_contract: Mapping[str, Any],
    verifier_correction_proof: VerifierCorrectionProof,
    lossless_read_state_proof: LosslessReadStateProof,
    postmortem_proof: PostmortemProof,
    created_utc: str,
) -> VerifiedAmendment:
    """Publish the exact create-only successor to protocol_recovery_002."""

    campaign = campaign.resolve()
    _parse_utc(created_utc)
    root = campaign / AMENDMENT_ROOT
    expected_predecessors = {
        VERIFIER_CORRECTION_AMENDMENT_DIRECTORY,
        PROTOCOL_RECOVERY_AMENDMENT_DIRECTORY,
    }
    if (
        root.is_symlink()
        or not root.is_dir()
        or {path.name for path in root.iterdir()} != expected_predecessors
    ):
        raise ContinuationAmendmentError(
            "protocol postmortem recovery requires the exact protocol_recovery_002 chain"
        )
    predecessor = _load_protocol_recovery_static(
        campaign,
        verifier_correction_proof=verifier_correction_proof,
        lossless_read_state_proof=lossless_read_state_proof,
    )
    manifest, _old_limit, manifest_sha256 = _load_base_unbound(campaign)
    _validate_continuation_payloads(
        continuation_source_inventory,
        continuation_limit_contract,
        continuation_scheduler_contract,
        manifest,
    )
    predecessor_source = _read_object(
        predecessor.directory / "continuation_source_inventory.json"
    )
    changed_paths = _protocol_postmortem_source_delta(
        predecessor_source, continuation_source_inventory
    )
    predecessor_limit = _read_object(
        predecessor.directory / "continuation_limit_contract.json"
    )
    predecessor_scheduler = _read_object(
        predecessor.directory / "continuation_scheduler_contract.json"
    )
    if continuation_limit_contract != predecessor_limit:
        raise ContinuationAmendmentError(
            "protocol postmortem recovery changed the predecessor limit contract"
        )
    if continuation_scheduler_contract != predecessor_scheduler:
        raise ContinuationAmendmentError(
            "protocol postmortem recovery changed the predecessor scheduler contract"
        )
    checkpoint = _collect_protocol_postmortem_checkpoint(
        campaign, manifest, predecessor, postmortem_proof
    )
    sidecars = {
        "recovery_checkpoint.json": checkpoint,
        "continuation_source_inventory.json": dict(
            continuation_source_inventory
        ),
        "continuation_limit_contract.json": dict(
            continuation_limit_contract
        ),
        "continuation_scheduler_contract.json": dict(
            continuation_scheduler_contract
        ),
    }
    sidecar_hashes = {
        name: _sha_bytes(_json_bytes(payload)) for name, payload in sidecars.items()
    }
    if (
        sidecar_hashes["continuation_limit_contract.json"]
        != predecessor.amendment["sidecars"]["continuation_limit_contract.json"]
        or sidecar_hashes["continuation_scheduler_contract.json"]
        != predecessor.amendment["sidecars"][
            "continuation_scheduler_contract.json"
        ]
    ):
        raise ContinuationAmendmentError(
            "protocol postmortem limit/scheduler bytes differ from predecessor"
        )
    corrections = checkpoint["verifier_corrections"]
    retries = checkpoint["authorized_retry_attempts"]
    retry_by_index = {item["run_index"]: item for item in retries}
    amendment = {
        "schema": PROTOCOL_POSTMORTEM_AMENDMENT_SCHEMA,
        "amendment_id": PROTOCOL_POSTMORTEM_AMENDMENT_DIRECTORY,
        "created_utc": created_utc,
        "campaign_uuid": manifest["campaign_uuid"],
        "base_manifest": {
            "path": "campaign_manifest.json",
            "sha256": manifest_sha256,
            "total_runs": manifest["total_runs"],
            "frozen_artifacts": manifest["frozen_artifacts"],
        },
        "hash_chain": {
            "predecessor_amendment_sha256": predecessor.amendment_sha256,
            "old": dict(predecessor.amendment["hash_chain"]["new"]),
            "new": {
                "source_inventory_sha256": sidecar_hashes[
                    "continuation_source_inventory.json"
                ],
                "limit_contract_sha256": sidecar_hashes[
                    "continuation_limit_contract.json"
                ],
                "scheduler_contract_sha256": sidecar_hashes[
                    "continuation_scheduler_contract.json"
                ],
            },
        },
        "sidecars": sidecar_hashes,
        "promotion_authorization": {
            "run_indices": [item["run_index"] for item in corrections],
            "inherited_run_indices": sorted(
                PROTOCOL_POSTMORTEM_CORRECTION_INDICES
            ),
            "fresh_run_indices": [],
            "attempts": {
                str(item["run_index"]): item["attempt"] for item in corrections
            },
            "completion_receipt_sha256": {
                str(item["run_index"]): item["completion_sha256"]
                for item in corrections
            },
            "scope": "inherited_byte_identical_only",
        },
        "retry_authorization": {
            "from_attempt": 1,
            "to_attempt": 2,
            "run_indices": sorted(retry_by_index),
            "inherited_run_indices": sorted(
                PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES
            ),
            "fresh_run_indices": [PROTOCOL_POSTMORTEM_RUN_INDEX],
            "completion_receipt_sha256": {
                str(index): retry_by_index[index]["completion_sha256"]
                for index in sorted(retry_by_index)
            },
            "scope_by_run_index": {
                str(PROTOCOL_RECOVERY_RUN_INDEX): (
                    "exact_hash_allowlist_lossless_read_state_only"
                ),
                str(PROTOCOL_POSTMORTEM_RUN_INDEX): (
                    "exact_hash_allowlist_postmortem_no_checkout_only"
                ),
            },
        },
        "denominator": {
            "manifest_rows": manifest["total_runs"],
            "row_identity": "campaign_manifest.runs[].index",
            "preserved_checkpoint_rows": len(
                checkpoint["preserved_terminal_results"]
            ),
            "promoted_attempts_retained_byte_identical": len(corrections),
            "superseded_attempts_retained_for_audit": len(retries),
            "added_rows": 0,
            "counting_rule": PROTOCOL_POSTMORTEM_COUNTING_RULE,
        },
        "protocol_postmortem_recovery": _protocol_postmortem_declared_scope(
            changed_paths
        ),
    }
    directory = root / PROTOCOL_POSTMORTEM_AMENDMENT_DIRECTORY
    try:
        directory.mkdir(mode=0o755)
    except FileExistsError as exc:
        raise ContinuationAmendmentError(
            f"create-only amendment already exists: {directory}"
        ) from exc
    for name, payload in sidecars.items():
        _write_exclusive(directory / name, _json_bytes(payload))
    amendment_raw = _json_bytes(amendment)
    _write_exclusive(directory / "amendment.json", amendment_raw)
    amendment_sha256 = _sha_bytes(amendment_raw)
    _write_exclusive(
        directory / "amendment.sha256",
        f"{amendment_sha256}  amendment.json\n".encode(),
    )
    return verify_amendment(
        campaign,
        continuation_source_inventory=continuation_source_inventory,
        continuation_limit_contract=continuation_limit_contract,
        continuation_scheduler_contract=continuation_scheduler_contract,
        checkout_proof=lambda *_args: {},
        verifier_correction_proof=verifier_correction_proof,
        lossless_read_state_proof=lossless_read_state_proof,
        postmortem_proof=postmortem_proof,
    )


def _load_protocol_postmortem_static(
    campaign: Path,
    *,
    verifier_correction_proof: VerifierCorrectionProof | None,
    lossless_read_state_proof: LosslessReadStateProof | None,
    postmortem_proof: PostmortemProof | None,
) -> VerifiedAmendment:
    campaign = campaign.resolve()
    predecessor = _load_protocol_recovery_static(
        campaign,
        verifier_correction_proof=verifier_correction_proof,
        lossless_read_state_proof=lossless_read_state_proof,
    )
    directory = (
        campaign / AMENDMENT_ROOT / PROTOCOL_POSTMORTEM_AMENDMENT_DIRECTORY
    )
    if directory.is_symlink() or not directory.is_dir():
        raise ContinuationAmendmentError(
            "protocol-postmortem amendment is absent"
        )
    entries = list(directory.iterdir())
    if (
        {path.name for path in entries} != PROTOCOL_POSTMORTEM_AMENDMENT_FILES
        or any(path.is_symlink() or not path.is_file() for path in entries)
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem amendment file inventory is not exact"
        )
    amendment_sha256 = _read_amendment_sha(directory)
    amendment = _read_object(directory / "amendment.json")
    exact = {
        "schema", "amendment_id", "created_utc", "campaign_uuid",
        "base_manifest", "hash_chain", "sidecars",
        "promotion_authorization", "retry_authorization", "denominator",
        "protocol_postmortem_recovery",
    }
    manifest, _old_limit, manifest_sha256 = _load_base_unbound(campaign)
    if (
        set(amendment) != exact
        or amendment.get("schema") != PROTOCOL_POSTMORTEM_AMENDMENT_SCHEMA
        or amendment.get("amendment_id")
        != PROTOCOL_POSTMORTEM_AMENDMENT_DIRECTORY
        or amendment.get("campaign_uuid") != manifest.get("campaign_uuid")
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem amendment identity differs"
        )
    _parse_utc(amendment.get("created_utc"))
    sidecars = amendment.get("sidecars") or {}
    expected_sidecars = PROTOCOL_POSTMORTEM_AMENDMENT_FILES - {
        "amendment.json", "amendment.sha256",
    }
    if set(sidecars) != expected_sidecars:
        raise ContinuationAmendmentError(
            "protocol-postmortem sidecar inventory differs"
        )
    for name, expected_hash in sidecars.items():
        if (
            not _is_sha256(expected_hash)
            or _sha_file(directory / name) != expected_hash
        ):
            raise ContinuationAmendmentError(
                f"protocol-postmortem sidecar drifted: {name}"
            )
    if amendment.get("base_manifest") != {
        "path": "campaign_manifest.json",
        "sha256": manifest_sha256,
        "total_runs": manifest["total_runs"],
        "frozen_artifacts": manifest["frozen_artifacts"],
    }:
        raise ContinuationAmendmentError(
            "protocol-postmortem base-manifest binding differs"
        )
    predecessor_source = _read_object(
        predecessor.directory / "continuation_source_inventory.json"
    )
    current_source = _read_object(
        directory / "continuation_source_inventory.json"
    )
    changed_paths = _protocol_postmortem_source_delta(
        predecessor_source, current_source
    )
    predecessor_limit = _read_object(
        predecessor.directory / "continuation_limit_contract.json"
    )
    current_limit = _read_object(
        directory / "continuation_limit_contract.json"
    )
    predecessor_scheduler = _read_object(
        predecessor.directory / "continuation_scheduler_contract.json"
    )
    scheduler = _read_object(
        directory / "continuation_scheduler_contract.json"
    )
    if (
        current_limit != predecessor_limit
        or scheduler != predecessor_scheduler
        or sidecars["continuation_limit_contract.json"]
        != predecessor.amendment["sidecars"]["continuation_limit_contract.json"]
        or sidecars["continuation_scheduler_contract.json"]
        != predecessor.amendment["sidecars"][
            "continuation_scheduler_contract.json"
        ]
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem changed predecessor limit/scheduler bytes"
        )
    validate_scheduler_contract(scheduler, manifest)
    if amendment.get("hash_chain") != {
        "predecessor_amendment_sha256": predecessor.amendment_sha256,
        "old": dict(predecessor.amendment["hash_chain"]["new"]),
        "new": {
            "source_inventory_sha256": sidecars[
                "continuation_source_inventory.json"
            ],
            "limit_contract_sha256": sidecars[
                "continuation_limit_contract.json"
            ],
            "scheduler_contract_sha256": sidecars[
                "continuation_scheduler_contract.json"
            ],
        },
    }:
        raise ContinuationAmendmentError(
            "protocol-postmortem hash chain differs"
        )
    checkpoint = _read_object(directory / "recovery_checkpoint.json")
    _verify_protocol_postmortem_checkpoint(
        campaign, checkpoint, predecessor, postmortem_proof
    )
    corrections = checkpoint["verifier_corrections"]
    retries = checkpoint["authorized_retry_attempts"]
    retry_by_index = {item["run_index"]: item for item in retries}
    if amendment.get("promotion_authorization") != {
        "run_indices": [item["run_index"] for item in corrections],
        "inherited_run_indices": sorted(PROTOCOL_POSTMORTEM_CORRECTION_INDICES),
        "fresh_run_indices": [],
        "attempts": {
            str(item["run_index"]): item["attempt"] for item in corrections
        },
        "completion_receipt_sha256": {
            str(item["run_index"]): item["completion_sha256"]
            for item in corrections
        },
        "scope": "inherited_byte_identical_only",
    }:
        raise ContinuationAmendmentError(
            "protocol-postmortem promotion authorization differs"
        )
    if amendment.get("retry_authorization") != {
        "from_attempt": 1,
        "to_attempt": 2,
        "run_indices": sorted(retry_by_index),
        "inherited_run_indices": sorted(
            PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES
        ),
        "fresh_run_indices": [PROTOCOL_POSTMORTEM_RUN_INDEX],
        "completion_receipt_sha256": {
            str(index): retry_by_index[index]["completion_sha256"]
            for index in sorted(retry_by_index)
        },
        "scope_by_run_index": {
            str(PROTOCOL_RECOVERY_RUN_INDEX): (
                "exact_hash_allowlist_lossless_read_state_only"
            ),
            str(PROTOCOL_POSTMORTEM_RUN_INDEX): (
                "exact_hash_allowlist_postmortem_no_checkout_only"
            ),
        },
    }:
        raise ContinuationAmendmentError(
            "protocol-postmortem retry authorization differs"
        )
    if amendment.get("denominator") != {
        "manifest_rows": manifest["total_runs"],
        "row_identity": "campaign_manifest.runs[].index",
        "preserved_checkpoint_rows": len(
            checkpoint["preserved_terminal_results"]
        ),
        "promoted_attempts_retained_byte_identical": len(corrections),
        "superseded_attempts_retained_for_audit": len(retries),
        "added_rows": 0,
        "counting_rule": PROTOCOL_POSTMORTEM_COUNTING_RULE,
    }:
        raise ContinuationAmendmentError(
            "protocol-postmortem denominator contract differs"
        )
    if amendment.get("protocol_postmortem_recovery") != (
        _protocol_postmortem_declared_scope(changed_paths)
    ):
        raise ContinuationAmendmentError(
            "protocol-postmortem declared scope differs"
        )
    return VerifiedAmendment(
        campaign=campaign,
        directory=directory,
        amendment=amendment,
        checkpoint=checkpoint,
        scheduler=scheduler,
        amendment_sha256=amendment_sha256,
        scheduler_sha256=sidecars["continuation_scheduler_contract.json"],
    )


def verify_amendment(
    campaign: Path,
    *,
    continuation_source_inventory: Mapping[str, Any],
    continuation_limit_contract: Mapping[str, Any],
    continuation_scheduler_contract: Mapping[str, Any],
    checkout_proof: CheckoutProof,
    postmortem_proof: PostmortemProof | None = None,
    verifier_correction_proof: VerifierCorrectionProof | None = None,
    lossless_read_state_proof: LosslessReadStateProof | None = None,
    authorization: AmendmentAuthorization | None = None,
) -> VerifiedAmendment:
    """Verify the published bundle and bind it to the current runtime payloads."""
    correction = (
        campaign.resolve()
        / AMENDMENT_ROOT
        / VERIFIER_CORRECTION_AMENDMENT_DIRECTORY
    )
    protocol_recovery = (
        campaign.resolve()
        / AMENDMENT_ROOT
        / PROTOCOL_RECOVERY_AMENDMENT_DIRECTORY
    )
    protocol_postmortem = (
        campaign.resolve()
        / AMENDMENT_ROOT
        / PROTOCOL_POSTMORTEM_AMENDMENT_DIRECTORY
    )
    recovery = (
        campaign.resolve() / AMENDMENT_ROOT / RECOVERY_AMENDMENT_DIRECTORY
    )
    successor = (
        campaign.resolve() / AMENDMENT_ROOT / SUCCESSOR_AMENDMENT_DIRECTORY
    )
    if protocol_postmortem.exists():
        initial = campaign.resolve() / AMENDMENT_ROOT / AMENDMENT_DIRECTORY
        if (
            not correction.exists()
            or not protocol_recovery.exists()
            or any(path.exists() for path in (initial, successor, recovery))
        ):
            raise ContinuationAmendmentError(
                "protocol postmortem recovery requires its exact protocol chain"
            )
        if authorization is not None:
            raise ContinuationAmendmentError(
                "base authorization cannot select protocol postmortem recovery"
            )
        if (
            verifier_correction_proof is None
            or lossless_read_state_proof is None
            or postmortem_proof is None
        ):
            raise ContinuationAmendmentError(
                "runtime protocol postmortem recovery requires all fresh proofs"
            )
        verified = _load_protocol_postmortem_static(
            campaign,
            verifier_correction_proof=verifier_correction_proof,
            lossless_read_state_proof=lossless_read_state_proof,
            postmortem_proof=postmortem_proof,
        )
    elif protocol_recovery.exists():
        initial = campaign.resolve() / AMENDMENT_ROOT / AMENDMENT_DIRECTORY
        if (
            not correction.exists()
            or any(path.exists() for path in (initial, successor, recovery))
        ):
            raise ContinuationAmendmentError(
                "protocol recovery requires only verifier_correction_001 as predecessor"
            )
        if authorization is not None:
            raise ContinuationAmendmentError(
                "base authorization cannot select protocol recovery"
            )
        if verifier_correction_proof is None:
            raise ContinuationAmendmentError(
                "runtime protocol recovery requires a fresh verifier proof"
            )
        if lossless_read_state_proof is None:
            raise ContinuationAmendmentError(
                "runtime protocol recovery requires a lossless read-state proof"
            )
        verified = _load_protocol_recovery_static(
            campaign,
            verifier_correction_proof=verifier_correction_proof,
            lossless_read_state_proof=lossless_read_state_proof,
        )
    elif correction.exists():
        initial = campaign.resolve() / AMENDMENT_ROOT / AMENDMENT_DIRECTORY
        if any(path.exists() for path in (initial, successor, recovery)):
            raise ContinuationAmendmentError(
                "verifier-correction amendment is mutually exclusive with retry chains"
            )
        if authorization is not None:
            raise ContinuationAmendmentError(
                "base authorization cannot select the verifier correction"
            )
        if verifier_correction_proof is None:
            raise ContinuationAmendmentError(
                "runtime verifier-correction verification requires a fresh proof"
            )
        verified = _load_verifier_correction_static(
            campaign, proof_callback=verifier_correction_proof
        )
    elif recovery.exists():
        if authorization is not None:
            raise ContinuationAmendmentError(
                "base authorization cannot select the recovery amendment"
            )
        if postmortem_proof is None:
            raise ContinuationAmendmentError(
                "runtime recovery verification requires a postmortem proof"
            )
        verified = _load_recovery_static(
            campaign,
            checkout_proof=checkout_proof,
            postmortem_proof=postmortem_proof,
        )
    elif successor.exists():
        if authorization is not None:
            raise ContinuationAmendmentError(
                "base authorization cannot select the successor amendment"
            )
        verified = _load_successor_static(
            campaign, checkout_proof=checkout_proof
        )
    else:
        verified = _load_static(
            campaign, authorization=authorization, checkout_proof=checkout_proof
        )
    manifest = _read_object(verified.campaign / "campaign_manifest.json")
    _validate_continuation_payloads(
        continuation_source_inventory,
        continuation_limit_contract,
        continuation_scheduler_contract,
        manifest,
    )
    expected = {
        "continuation_source_inventory.json": continuation_source_inventory,
        "continuation_limit_contract.json": continuation_limit_contract,
        "continuation_scheduler_contract.json": continuation_scheduler_contract,
    }
    for name, payload in expected.items():
        if _read_object(verified.directory / name) != payload:
            raise ContinuationAmendmentError(f"current runtime differs from {name}")
    return verified


def read_monitor_overlay(
    campaign: Path,
) -> dict[str, Any] | None:
    """Return verified active limits for the alert-only monitor.

    Absence means the immutable base scheduler remains active.  Presence with
    any malformed or drifted artifact raises rather than silently falling back.
    """
    root = campaign.resolve() / AMENDMENT_ROOT
    initial = root / AMENDMENT_DIRECTORY
    successor = root / SUCCESSOR_AMENDMENT_DIRECTORY
    recovery = root / RECOVERY_AMENDMENT_DIRECTORY
    correction = root / VERIFIER_CORRECTION_AMENDMENT_DIRECTORY
    protocol_recovery = root / PROTOCOL_RECOVERY_AMENDMENT_DIRECTORY
    protocol_postmortem = root / PROTOCOL_POSTMORTEM_AMENDMENT_DIRECTORY
    if (
        not initial.exists()
        and not successor.exists()
        and not recovery.exists()
        and not correction.exists()
        and not protocol_recovery.exists()
        and not protocol_postmortem.exists()
    ):
        return None
    verified = load_amendment(campaign)
    assert verified is not None
    overlay = {
        "amendment_sha256": verified.amendment_sha256,
        "scheduler_sha256": verified.scheduler_sha256,
        "active_limits": dict(verified.active_limits),
        "scratch_policy": verified.scheduler.get("scratch_policy"),
        "authorized_retry_indices": sorted(verified.retry_by_index),
        "manifest_denominator": verified.amendment["denominator"]["manifest_rows"],
    }
    if verified.correction_by_index:
        overlay["verifier_corrected_indices"] = sorted(
            verified.correction_by_index
        )
    return overlay


def load_amendment(campaign: Path) -> VerifiedAmendment | None:
    """Load a published bundle without asserting a caller's runtime payloads."""
    root = campaign.resolve() / AMENDMENT_ROOT
    initial = root / AMENDMENT_DIRECTORY
    successor = root / SUCCESSOR_AMENDMENT_DIRECTORY
    recovery = root / RECOVERY_AMENDMENT_DIRECTORY
    correction = root / VERIFIER_CORRECTION_AMENDMENT_DIRECTORY
    protocol_recovery = root / PROTOCOL_RECOVERY_AMENDMENT_DIRECTORY
    protocol_postmortem = root / PROTOCOL_POSTMORTEM_AMENDMENT_DIRECTORY
    if (
        not initial.exists()
        and not successor.exists()
        and not recovery.exists()
        and not correction.exists()
        and not protocol_recovery.exists()
        and not protocol_postmortem.exists()
    ):
        return None
    if protocol_postmortem.exists() and (
        not correction.exists()
        or not protocol_recovery.exists()
        or any(path.exists() for path in (initial, successor, recovery))
    ):
        raise ContinuationAmendmentError(
            "protocol postmortem recovery requires its exact protocol chain"
        )
    if protocol_recovery.exists() and not protocol_postmortem.exists() and (
        not correction.exists()
        or any(path.exists() for path in (initial, successor, recovery))
    ):
        raise ContinuationAmendmentError(
            "protocol recovery requires only verifier_correction_001 as predecessor"
        )
    if (
        correction.exists()
        and not protocol_recovery.exists()
        and not protocol_postmortem.exists()
        and any(
        path.exists() for path in (initial, successor, recovery)
        )
    ):
        raise ContinuationAmendmentError(
            "verifier-correction amendment is mutually exclusive with retry chains"
        )
    if recovery.exists() and (not initial.exists() or not successor.exists()):
        raise ContinuationAmendmentError(
            "recovery amendment exists without its predecessor chain"
        )
    if successor.exists() and not initial.exists():
        raise ContinuationAmendmentError(
            "successor amendment exists without its predecessor"
        )
    if root.is_symlink() or not root.is_dir():
        raise ContinuationAmendmentError("amendment root is unsafe")
    actual = {path.name for path in root.iterdir()}
    expected = (
        ({
            VERIFIER_CORRECTION_AMENDMENT_DIRECTORY,
            PROTOCOL_RECOVERY_AMENDMENT_DIRECTORY,
            PROTOCOL_POSTMORTEM_AMENDMENT_DIRECTORY,
        } if protocol_postmortem.exists() else {
            VERIFIER_CORRECTION_AMENDMENT_DIRECTORY,
            PROTOCOL_RECOVERY_AMENDMENT_DIRECTORY,
        })
        if protocol_recovery.exists() or protocol_postmortem.exists()
        else (
            {VERIFIER_CORRECTION_AMENDMENT_DIRECTORY}
            if correction.exists()
            else {AMENDMENT_DIRECTORY}
        )
    )
    if (
        not correction.exists()
        and not protocol_recovery.exists()
        and not protocol_postmortem.exists()
    ):
        if successor.exists():
            expected.add(SUCCESSOR_AMENDMENT_DIRECTORY)
        if recovery.exists():
            expected.add(RECOVERY_AMENDMENT_DIRECTORY)
    if actual != expected:
        raise ContinuationAmendmentError(
            "amendment chain directory inventory is not exact"
        )
    if protocol_postmortem.exists():
        return _load_protocol_postmortem_static(
            campaign,
            verifier_correction_proof=None,
            lossless_read_state_proof=None,
            postmortem_proof=None,
        )
    if protocol_recovery.exists():
        return _load_protocol_recovery_static(
            campaign,
            verifier_correction_proof=None,
            lossless_read_state_proof=None,
        )
    if correction.exists():
        return _load_verifier_correction_static(
            campaign, proof_callback=None
        )
    if recovery.exists():
        return _load_recovery_static(
            campaign, checkout_proof=None, postmortem_proof=None
        )
    if successor.exists():
        return _load_successor_static(campaign, checkout_proof=None)
    return _load_static(campaign, authorization=None, checkout_proof=None)


def authorized_retry_attempt(
    verified: VerifiedAmendment,
    *,
    run_index: int,
    run_id: str,
    prior_attempt: int,
    completion_receipt: Path,
) -> int | None:
    """Return attempt 2 only for an exact checkpointed interruption."""
    item = verified.retry_by_index.get(run_index)
    if item is None:
        return None
    if (
        run_id != item.get("run_id")
        or prior_attempt != 1
        or completion_receipt.resolve()
        != (verified.campaign / item["completion_receipt"]).resolve()
        or _sha_file(completion_receipt) != item.get("completion_sha256")
    ):
        return None
    return 2


def bind_cache_nonce(base_nonce: str, verified: VerifiedAmendment) -> str:
    """Bind continuation workers to both old manifest and amendment hashes."""
    return f"{base_nonce}/continuation-{verified.amendment_sha256}"
