#!/usr/bin/env python3
"""Recover mis-routed Campaign 9 proxy traces without inventing model data.

Two rollout workers reached an already-running localhost proxy after their own
proxy failed to bind.  The successful OpenAI request/response records therefore
exist byte-for-byte in traces owned by failed, quota-ineligible attempts.  This
tool filters those exact records by the complete user instruction and publishes
them at the selected run's absent trace path.  It never edits or removes a raw
capture and uses link-based no-replace publication.

The default command is an audit.  ``execute`` additionally requires a canonical
authorization document which binds every source and destination byte identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


AUTH_SCHEMA = "harness-distill.seed-trace-recovery-authorization.v1"
MANIFEST_SCHEMA = "harness-distill.seed-trace-recovery.v1"
TRACE_SCHEMA = "harness-distill.proxy-trace.v1"


class RecoveryError(RuntimeError):
    """An evidence or publication invariant failed."""


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_canonical(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict) or raw != canonical_bytes(value):
        raise RecoveryError(f"JSON is not a canonical object: {path}")
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryError(f"cannot read JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RecoveryError(f"JSON is not an object: {path}")
    return value


def _safe_regular(path: Path, *, must_exist: bool = True) -> os.stat_result | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if must_exist:
            raise RecoveryError(f"required file is absent: {path}") from None
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RecoveryError(f"path is not a non-symlink regular file: {path}")
    return info


def _under(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or relative.startswith("/"):
        raise RecoveryError("authorization paths must be non-empty relative paths")
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RecoveryError(f"authorization path escapes target: {relative}") from exc
    return candidate


def _instruction_present(record: Mapping[str, Any], instruction: str) -> bool:
    request = record.get("request")
    if not isinstance(request, Mapping):
        return False
    messages = request.get("messages")
    if not isinstance(messages, list):
        return False
    needle = f"\n{instruction}\n</user_request>"
    for message in messages:
        if not isinstance(message, Mapping) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str) and needle in content:
            return True
    return False


def _extract(entry: Mapping[str, Any], root: Path) -> tuple[bytes, dict[str, Any]]:
    source = _under(root, str(entry.get("source")))
    destination = _under(root, str(entry.get("destination")))
    summary_path = destination.parent / "summary.json"
    trajectory_path = destination.parent / "trajectory.json"
    _safe_regular(source)
    _safe_regular(summary_path)
    _safe_regular(trajectory_path)
    if sha256_file(source) != entry.get("source_sha256"):
        raise RecoveryError(f"source trace drifted: {source}")
    if sha256_file(summary_path) != entry.get("summary_sha256"):
        raise RecoveryError(f"destination summary drifted: {summary_path}")
    if sha256_file(trajectory_path) != entry.get("trajectory_sha256"):
        raise RecoveryError(f"destination trajectory drifted: {trajectory_path}")

    summary = load_json(summary_path)
    trajectory = load_json(trajectory_path)
    instruction = entry.get("instruction")
    if not isinstance(instruction, str) or trajectory.get("instruction") != instruction:
        raise RecoveryError(f"destination instruction differs: {destination.parent}")
    expected_task = entry.get("task_id")
    if (
        summary.get("task_id") != expected_task
        or trajectory.get("task_id") != expected_task
        or summary.get("success") is not True
        or summary.get("outcome") != entry.get("outcome")
        or summary.get("num_steps") != entry.get("summary_num_steps")
    ):
        raise RecoveryError(f"destination success evidence differs: {destination.parent}")

    selected_raw: list[bytes] = []
    selected: list[dict[str, Any]] = []
    with source.open("rb") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RecoveryError(f"invalid source JSONL at {source}:{line_number}") from exc
            if not isinstance(value, dict):
                raise RecoveryError(f"non-object source row at {source}:{line_number}")
            sequence = value.get("sequence")
            if (
                type(sequence) is not int
                or sequence < int(entry["first_sequence"])
                or sequence > int(entry["last_sequence"])
            ):
                continue
            if raw != canonical_bytes(value):
                raise RecoveryError(f"source row is not canonical at {source}:{line_number}")
            if (
                value.get("schema") != TRACE_SCHEMA
                or value.get("role", "student") != "student"
                or type(value.get("status_code")) is not int
                or not 200 <= value["status_code"] < 300
            ):
                raise RecoveryError(f"matched source row is not successful: {source}:{line_number}")
            request = value.get("request")
            effective = value.get("effective_request")
            if not isinstance(request, Mapping) or not isinstance(effective, Mapping):
                raise RecoveryError(f"matched source row lacks requests: {source}:{line_number}")
            if value.get("request_sha256") != sha256_bytes(
                json.dumps(request, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
            ):
                raise RecoveryError(f"original request hash mismatch: {source}:{line_number}")
            if value.get("effective_request_sha256") != sha256_bytes(
                json.dumps(effective, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
            ):
                raise RecoveryError(f"effective request hash mismatch: {source}:{line_number}")
            selected_raw.append(raw)
            selected.append(value)

    expected_count = entry.get("record_count")
    if len(selected) != expected_count:
        raise RecoveryError(
            f"matched {len(selected)} records instead of {expected_count}: {source}"
        )
    sequences = [row.get("sequence") for row in selected]
    session_sequences = [row.get("session_sequence") for row in selected]
    sessions = {row.get("session_sha256") for row in selected}
    if (
        sequences != list(range(int(entry["first_sequence"]), int(entry["last_sequence"]) + 1))
        or session_sequences
        != list(
            range(
                int(entry["first_session_sequence"]),
                int(entry["last_session_sequence"]) + 1,
            )
        )
        or sessions != {entry.get("session_sha256")}
    ):
        raise RecoveryError(f"matched source sequence/session identity differs: {source}")
    instruction_matches = sum(
        _instruction_present(row, instruction) for row in selected
    )
    if instruction_matches != entry.get("summary_num_steps"):
        raise RecoveryError(
            f"primary task decisions differ from summary steps: {source}"
        )
    recovered = b"".join(selected_raw)
    expected_recovered = entry.get("recovered_sha256")
    if expected_recovered is not None and sha256_bytes(recovered) != expected_recovered:
        raise RecoveryError(f"filtered recovery bytes drifted: {source}")
    report = {
        "task_id": expected_task,
        "source": str(source),
        "source_sha256": sha256_file(source),
        "destination": str(destination),
        "recovered_sha256": sha256_bytes(recovered),
        "record_count": len(selected),
        "primary_instruction_records": instruction_matches,
        "summary_num_steps": summary.get("num_steps"),
        "first_sequence": sequences[0],
        "last_sequence": sequences[-1],
        "first_session_sequence": session_sequences[0],
        "last_session_sequence": session_sequences[-1],
        "session_sha256": next(iter(sessions)),
        "summary_sha256": sha256_file(summary_path),
        "trajectory_sha256": sha256_file(trajectory_path),
        "raw_source_preserved": True,
    }
    return recovered, report


def _publish_no_replace(path: Path, raw: bytes) -> str:
    expected = sha256_bytes(raw)
    if path.exists() or path.is_symlink():
        _safe_regular(path)
        if sha256_file(path) != expected or path.read_bytes() != raw:
            raise RecoveryError(f"existing publication conflicts: {path}")
        return "already-identical"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.recovery-{os.getpid()}"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    _safe_regular(path)
    if sha256_file(path) != expected or path.read_bytes() != raw:
        raise RecoveryError(f"publication failed: {path}")
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return "published"


def recover(authorization_path: Path, *, execute: bool) -> dict[str, Any]:
    authorization_path = authorization_path.resolve()
    authorization = load_canonical(authorization_path)
    authorization_raw = authorization_path.read_bytes()
    if authorization.get("schema") != AUTH_SCHEMA:
        raise RecoveryError("unsupported authorization schema")
    root = Path(str(authorization.get("target"))).resolve()
    if not root.is_dir() or root.is_symlink():
        raise RecoveryError("authorized target is not a real directory")
    identity = root / "campaign_identity.json"
    result_path = root / "rollouts/seed/shadow/result.json"
    _safe_regular(identity)
    _safe_regular(result_path)
    if sha256_file(identity) != authorization.get("target_identity_sha256"):
        raise RecoveryError("campaign identity drifted")
    if sha256_file(result_path) != authorization.get("shadow_result_sha256"):
        raise RecoveryError("sealed shadow result drifted")
    result = load_json(result_path)
    selected = set(result.get("selected_run_dirs", []))
    usable = set(result.get("usable_run_dirs", []))

    entries = authorization.get("entries")
    if not isinstance(entries, list) or not entries:
        raise RecoveryError("authorization entries must be a non-empty list")
    prepared: list[tuple[Path, bytes, dict[str, Any]]] = []
    for raw_entry in entries:
        if not isinstance(raw_entry, Mapping):
            raise RecoveryError("authorization entry is not an object")
        source = _under(root, str(raw_entry.get("source"))).parent
        destination = _under(root, str(raw_entry.get("destination"))).parent
        if str(destination) not in selected or str(destination) not in usable:
            raise RecoveryError(f"destination is not a sealed selected+usable run: {destination}")
        if str(source) in selected or str(source) in usable:
            raise RecoveryError(f"source capture is quota-eligible and cannot be reused: {source}")
        recovered, report = _extract(raw_entry, root)
        prepared.append((_under(root, str(raw_entry["destination"])), recovered, report))

    reports = [report for _, _, report in prepared]
    authorization_copy = _under(root, str(authorization.get("authorization_copy")))
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "target": str(root),
        "target_identity_sha256": sha256_file(identity),
        "shadow_result_sha256": sha256_file(result_path),
        "authorization_path": str(authorization_copy),
        "authorization_sha256": sha256_bytes(authorization_raw),
        "entries": reports,
        "properties": {
            "model_records_copied_byte_for_byte": True,
            "raw_sources_preserved": True,
            "source_runs_quota_ineligible": True,
            "destination_runs_selected_successes": True,
            "fabricated_model_content": False,
            "publication": "atomic-link-no-replace",
        },
    }
    manifest["manifest_sha256"] = sha256_bytes(
        json.dumps(manifest, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()
    )
    if not execute:
        return {**manifest, "mode": "audit", "would_publish": len(prepared)}

    authorization_state = _publish_no_replace(authorization_copy, authorization_raw)
    states = []
    for destination, recovered, _ in prepared:
        states.append(
            {
                "destination": str(destination),
                "state": _publish_no_replace(destination, recovered),
            }
        )
    manifest_path = _under(root, str(authorization.get("manifest")))
    manifest_state = _publish_no_replace(manifest_path, canonical_bytes(manifest))
    # Re-audit all output bytes only after the completion manifest exists.
    for destination, recovered, _ in prepared:
        if sha256_file(destination) != sha256_bytes(recovered):
            raise RecoveryError(f"post-publication trace drifted: {destination}")
    if load_canonical(manifest_path) != manifest:
        raise RecoveryError("recovery manifest drifted after publication")
    return {
        **manifest,
        "mode": "execute",
        "publication_states": states,
        "authorization_state": authorization_state,
        "manifest_state": manifest_state,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=("audit", "execute"), default="audit")
    parser.add_argument("--authorization", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = recover(args.authorization, execute=args.command == "execute")
    except (RecoveryError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"campaign9-seed-trace-recovery: blocked: {exc}", file=sys.stderr)
        return 73
    sys.stdout.buffer.write(canonical_bytes(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
