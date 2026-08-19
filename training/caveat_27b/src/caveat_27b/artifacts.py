"""Canonical, hash-bound artifact helpers."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


class ArtifactError(RuntimeError):
    """An input or output artifact violated the campaign contract."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    target = require_regular_file(path)
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def require_regular_file(path: str | Path) -> Path:
    target = Path(path).resolve()
    try:
        metadata = target.lstat()
    except FileNotFoundError as exc:
        raise ArtifactError(f"required file is absent: {target}") from exc
    if not stat.S_ISREG(metadata.st_mode) or target.is_symlink():
        raise ArtifactError(f"artifact must be a regular non-symlink file: {target}")
    return target


def read_json(path: str | Path) -> Any:
    target = require_regular_file(path)
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"invalid JSON artifact: {target}") from exc


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    target = require_regular_file(path)
    rows: list[dict[str, Any]] = []
    with target.open(encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ArtifactError(f"{target}:{line_number}: invalid JSON") from exc
            if not isinstance(row, dict):
                raise ArtifactError(f"{target}:{line_number}: row must be an object")
            rows.append(row)
    return rows


def jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    return b"".join((canonical_json(dict(row)) + "\n").encode() for row in rows)


def publish_bytes(path: str | Path, payload: bytes) -> Path:
    """Publish once; accept an already-identical regular file and reject drift."""

    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        existing = require_regular_file(target).read_bytes()
        if existing != payload:
            raise ArtifactError(f"refusing to overwrite nonidentical artifact: {target}")
        return target
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def publish_json(path: str | Path, payload: Any) -> Path:
    return publish_bytes(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode())


def publish_jsonl(path: str | Path, rows: Iterable[Mapping[str, Any]]) -> Path:
    return publish_bytes(path, jsonl_bytes(rows))


def tree_digest(files: Iterable[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((item.resolve() for item in files), key=lambda item: item.as_posix()):
        relative = path.relative_to(root.resolve()).as_posix().encode()
        payload = require_regular_file(path).read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()
