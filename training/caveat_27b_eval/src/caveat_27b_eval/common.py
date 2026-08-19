from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any


class IntegrityError(RuntimeError):
    """Raised when a frozen scientific contract is missing or inconsistent."""


FIXED_CONTRACT_COMPILER_ATTEMPTS = 4
_CONTRACT_COMPILER_ERROR_PREFIX = (
    "RuntimeError: contract compilation failed after four attempts: "
)
_CONTRACT_COMPILER_POLICY_MARKERS = (
    "modelprovidererror: failed to parse structured output from model response",
    "validation error for _draftcontract",
    "contracterror:",
    "unitnormalizationerror:",
    "validationerror:",
    "jsondecodeerror:",
    "json_invalid",
)
_CONTRACT_COMPILER_INFRASTRUCTURE_MARKERS = (
    "all-backends-unhealthy",
    "apiconnectionerror",
    "apitimeouterror",
    "authenticationerror",
    "badrequesterror",
    "browser error",
    "browsererror",
    "connection refused",
    "deployment_not_found",
    "error code: 4",
    "error code: 5",
    "httpx.",
    "internalservererror",
    "modelprovidererror: upstream request timeout",
    "navigate",
    "navigation",
    "net::err_",
    "playwright",
    "rate limit exceeded",
    "server unavailable",
    "serviceunavailableerror",
    "targetclosederror",
    "timeout",
    "timed out",
    "trapi:",
    "unauthorized",
)


def _structured_attempt_record(stats: dict[str, Any]) -> dict[str, Any] | None:
    limit_audit = stats.get("limit_audit")
    categories = limit_audit.get("categories") if isinstance(limit_audit, dict) else None
    if not isinstance(categories, dict):
        return None
    matches = [
        records.get("structured_response_attempts")
        for records in categories.values()
        if isinstance(records, dict) and "structured_response_attempts" in records
    ]
    return matches[0] if len(matches) == 1 and isinstance(matches[0], dict) else None


def structured_attempt_exhausted(stats: dict[str, Any]) -> bool:
    record = _structured_attempt_record(stats)
    observations = record.get("observations") if record else None
    return bool(
        isinstance(record, dict)
        and record.get("configured") == FIXED_CONTRACT_COMPILER_ATTEMPTS
        and isinstance(observations, dict)
        and observations.get("exhaustions", 0) > 0
    )


def fixed_contract_compiler_policy_failure(
    summary: dict[str, Any], trajectory: dict[str, Any]
) -> bool:
    """Recognize only the frozen compiler's exhausted parse/semantic repair path.

    The compiler is part of the measured policy.  Failing to produce its required
    contract after all four fixed repair attempts is therefore a behavioral failure,
    not missing infrastructure.  Provider, authentication, rate, deployment, timeout,
    navigation, and browser failures intentionally do not match this predicate.
    """

    steps = trajectory.get("steps")
    stats = trajectory.get("stats")
    if not isinstance(steps, list) or steps or not isinstance(stats, dict):
        return False
    if summary.get("num_steps") != 0:
        return False
    summary_error = summary.get("error")
    stats_error = stats.get("error")
    if not isinstance(summary_error, str) or summary_error != stats_error:
        return False
    if not summary_error.startswith(_CONTRACT_COMPILER_ERROR_PREFIX):
        return False
    normalized_error = summary_error.casefold()
    if any(
        marker in normalized_error
        for marker in _CONTRACT_COMPILER_INFRASTRUCTURE_MARKERS
    ):
        return False
    if not any(marker in normalized_error for marker in _CONTRACT_COMPILER_POLICY_MARKERS):
        return False

    caveat_harness = stats.get("caveat_harness")
    expected = {
        "contract_compile_calls": 1,
        "contract_compile_attempts": FIXED_CONTRACT_COMPILER_ATTEMPTS,
        "contract_compile_rejections": FIXED_CONTRACT_COMPILER_ATTEMPTS,
        "contract_compile_failures": 1,
        "structured_max_attempts_observed": FIXED_CONTRACT_COMPILER_ATTEMPTS,
        "structured_attempt_exhaustions": 1,
        "decision_checkpoint_calls": 0,
    }
    if not isinstance(caveat_harness, dict) or any(
        caveat_harness.get(name) != value for name, value in expected.items()
    ):
        return False
    if caveat_harness.get("contract_sha256") is not None:
        return False

    limit_audit = stats.get("limit_audit")
    attempt_record = _structured_attempt_record(stats)
    observations = attempt_record.get("observations") if attempt_record else None
    return bool(
        isinstance(limit_audit, dict)
        and limit_audit.get("complete") is True
        and isinstance(attempt_record, dict)
        and attempt_record.get("configured") == FIXED_CONTRACT_COMPILER_ATTEMPTS
        and attempt_record.get("touched_count") == 1
        and isinstance(observations, dict)
        and observations.get("max_attempts") == FIXED_CONTRACT_COMPILER_ATTEMPTS
        and observations.get("attempts") == FIXED_CONTRACT_COMPILER_ATTEMPTS
        and observations.get("rejected_attempts") == FIXED_CONTRACT_COMPILER_ATTEMPTS
        and observations.get("exhaustions") == 1
    )


def classify_marketplace_result(
    summary: dict[str, Any], trajectory: dict[str, Any]
) -> tuple[str, str | None]:
    """Classify one internally identity-checked CAVEAT result.

    Behavioral classes belong in the scientific denominator.  Every ambiguous or
    infrastructure-shaped failure is kept out and requires an explicit preserved retry.
    """

    outcome = summary.get("outcome")
    evaluation = trajectory.get("evaluation")
    if not isinstance(evaluation, dict) or evaluation.get("outcome") != outcome:
        return "malformed", "summary and trajectory outcomes differ"
    if outcome in {None, "error", "skipped"}:
        return "infrastructure_invalid", f"non-authoritative outcome: {outcome!r}"

    chosen = evaluation.get("chosen")
    if summary.get("chosen") != chosen:
        return "malformed", "summary and trajectory chosen identities differ"
    if outcome != "none":
        if chosen is None:
            return "malformed", "purchase outcome has no chosen product"
        return "behavioral_purchase", None
    if chosen is not None:
        return "malformed", "no-purchase outcome records a chosen product"

    steps = trajectory.get("steps")
    stats = trajectory.get("stats")
    if not isinstance(steps, list) or not isinstance(stats, dict):
        return "infrastructure_invalid", "no-purchase lacks step/stat evidence"
    if summary.get("num_steps") != len(steps):
        return "malformed", "summary step count differs from trajectory"
    if fixed_contract_compiler_policy_failure(summary, trajectory):
        return "behavioral_protocol_failure", None
    if not steps:
        return "infrastructure_invalid", "zero-step run is not an attested compiler policy failure"
    if summary.get("error") or stats.get("error"):
        return "infrastructure_invalid", "no-purchase followed a runtime/infrastructure error"
    return "behavioral_no_purchase", None


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hash_tree(root: Path, *, include: Iterable[Path] | None = None) -> str:
    paths = sorted(include if include is not None else (p for p in root.rglob("*") if p.is_file()))
    digest = hashlib.sha256()
    for path in paths:
        if not path.is_file():
            raise IntegrityError(f"tree member is not a file: {path}")
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        data_hash = bytes.fromhex(sha256_file(path))
        digest.update(data_hash)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"cannot read JSON {path}: {exc}") from exc


def write_json_create_only(path: Path, value: Any) -> None:
    """Atomically create canonical JSON; never replace an existing scientific artifact."""

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_bytes(value) + b"\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise IntegrityError(f"refusing to overwrite frozen artifact: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def write_text_create_only(path: Path, text: str) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise IntegrityError(f"refusing to overwrite frozen artifact: {path}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def stable_int(seed: int, *parts: object, bits: int = 63) -> int:
    payload = canonical_bytes([seed, *parts])
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") & ((1 << bits) - 1)


def normalize_token(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def require_keys(mapping: dict[str, Any], keys: Iterable[str], *, where: str) -> None:
    missing = sorted(set(keys) - set(mapping))
    if missing:
        raise IntegrityError(f"{where} missing required keys: {missing}")
