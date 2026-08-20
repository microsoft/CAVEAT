"""Frozen campaign configuration and artifact-path helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: str | Path) -> dict[str, Any]:
    target = Path(path).resolve()
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError(f"unsupported or malformed configuration: {target}")
    return payload


@dataclass(frozen=True)
class Campaign:
    path: Path
    root: Path
    campaign: dict[str, Any]
    models: dict[str, Any]
    stack: dict[str, Any]
    evaluation: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path) -> Campaign:
        target = Path(path).resolve()
        root = target.parent.parent
        campaign = load_yaml(target)

        def related(key: str) -> dict[str, Any]:
            configured = Path(campaign[key])
            return load_yaml(configured if configured.is_absolute() else root / configured)

        return cls(
            path=target,
            root=root,
            campaign=campaign,
            models=related("model_config"),
            stack=related("stack_config"),
            evaluation=related("evaluation_config"),
        )

    def model(self, role: str) -> dict[str, Any]:
        if role not in {"primary", "fallback"}:
            raise ValueError("model role must be primary or fallback")
        return dict(self.models[role])

    @property
    def digest(self) -> str:
        files = [
            self.path,
            self.root / self.campaign["model_config"],
            self.root / self.campaign["stack_config"],
            self.root / self.campaign["evaluation_config"],
        ]
        digest = hashlib.sha256()
        for path in files:
            relative = path.relative_to(self.root).as_posix().encode()
            data = path.read_bytes()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
        return digest.hexdigest()


def atomic_json(path: str | Path, payload: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target
