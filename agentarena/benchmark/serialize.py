"""Read/write the versioned per-scenario artifact tree under ``benchmark_data/amazon/``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .schema import (SCHEMA_VERSION, GeneratedInstruction, PreferenceSpec, ProductRow,
                     ScenarioSpec, SteeringSpec)

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = REPO_ROOT / "benchmark_data" / "amazon"


def scenario_dir(scenario_id: str, root: Optional[Path] = None) -> Path:
    return (Path(root) if root else DATA_ROOT) / scenario_id


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2))


def catalog_seed_json(scenario: ScenarioSpec, rows: list[ProductRow]) -> dict:
    """The seed JSON the Amazon server reads (shape of Catalog.to_seed_json()).

    ``serving`` (the HARD tier's pagination/placement/rails/rate policy) is emitted only
    when the scenario carries a non-empty one. The five original scenarios have none, so
    their ``catalog.json`` regenerates byte-identically and every serving-layer branch in
    the backend stays provably dead for them.
    """
    out = {"category_slug": scenario.category_slug, "bury_index": scenario.bury_index,
           "products": [r.to_seed_dict() for r in rows]}
    serving = getattr(scenario, "serving", None)
    if serving:                       # omit-if-empty
        out["serving"] = serving
    return out


def write_artifacts(scenario: ScenarioSpec, rows: list[ProductRow],
                    prefs: dict[str, PreferenceSpec],
                    instructions: dict[str, GeneratedInstruction],
                    steering: dict[str, SteeringSpec], *, seed: int,
                    root: Optional[Path] = None, extra_meta: Optional[dict] = None,
                    truthful_steering: Optional[dict] = None) -> Path:
    d = scenario_dir(scenario.scenario_id, root)
    _write(d / "attribute_schema.json", scenario.schema.to_dict())
    _write(d / "scenario.json", scenario.to_dict())
    _write(d / "catalog.json", catalog_seed_json(scenario, rows))
    _write(d / "pool.json", [r.to_dict() for r in rows])  # full rows incl. fail_reasons/role
    _write(d / "preferences.json", {v: p.to_dict() for v, p in prefs.items()})
    _write(d / "instructions.json", {v: gi.to_dict() for v, gi in instructions.items()})
    _write(d / "steering.json", {k: s.to_dict() for k, s in steering.items()})
    if truthful_steering is not None:
        _write(d / "truthful_steering.json", truthful_steering)
    meta = {
        "scenario_id": scenario.scenario_id, "schema_version": SCHEMA_VERSION, "seed": seed,
        "category_slug": scenario.category_slug, "n_products": len(rows),
        "roles": {r: sum(1 for x in rows if x.role == r)
                  for r in ("compliant", "decoy", "satisfice", "distractor")},
        "instruction_status": {v: gi.status for v, gi in instructions.items()},
        "copy_status": {r.asin: r.copy_status for r in rows},
        "images": {r.asin: r.image for r in rows},
    }
    if extra_meta:
        meta.update(extra_meta)
    truthful = (scenario.serving or {}).get("truthful") or {}
    if truthful:
        meta["truthful"] = {
            "tier": truthful.get("tier"),
            "roster_counts": truthful.get("roster_counts"),
            "conditions": list((truthful_steering or {}).get("conditions") or []),
            "commercial_score_version": truthful.get("commercial_score_version"),
        }
        if truthful.get("semantic_requirements"):
            meta["truthful"]["semantic_requirements"] = dict(
                truthful["semantic_requirements"])
    _write(d / "meta.json", meta)
    return d


# ---- loaders ----
def _read(p: Path):
    return json.loads(Path(p).read_text())


def load_catalog_json(scenario_id: str, root: Optional[Path] = None) -> dict:
    return _read(scenario_dir(scenario_id, root) / "catalog.json")


def load_pool(scenario_id: str, root: Optional[Path] = None) -> list[ProductRow]:
    return [ProductRow.from_dict(d) for d in _read(scenario_dir(scenario_id, root) / "pool.json")]


def load_preferences(scenario_id: str, root: Optional[Path] = None) -> dict[str, PreferenceSpec]:
    raw = _read(scenario_dir(scenario_id, root) / "preferences.json")
    return {v: PreferenceSpec.from_dict(p) for v, p in raw.items()}


def load_instructions(scenario_id: str, root: Optional[Path] = None) -> dict[str, GeneratedInstruction]:
    raw = _read(scenario_dir(scenario_id, root) / "instructions.json")
    return {v: GeneratedInstruction.from_dict(g) for v, g in raw.items()}


def load_steering(scenario_id: str, root: Optional[Path] = None) -> dict[str, SteeringSpec]:
    raw = _read(scenario_dir(scenario_id, root) / "steering.json")
    return {k: SteeringSpec.from_dict(s) for k, s in raw.items()}


def load_truthful_steering(scenario_id: str, root: Optional[Path] = None) -> dict:
    return _read(scenario_dir(scenario_id, root) / "truthful_steering.json")


def load_meta(scenario_id: str, root: Optional[Path] = None) -> dict:
    return _read(scenario_dir(scenario_id, root) / "meta.json")


def exists(scenario_id: str, root: Optional[Path] = None) -> bool:
    return (scenario_dir(scenario_id, root) / "catalog.json").exists()
