"""Frozen, analysis-only Standard Amazon steering-ablation registry.

The production adapter knows nothing about these conditions.  A measured command opts in by
putting this directory first on ``PYTHONPATH``; :mod:`sitecustomize` then installs the narrow
resolver/runtime hooks.  Canonical conditions are read verbatim from the committed laptop
``steering.json``.  The five ``abl-*`` controls contain placement instructions only: no product
fact, price, rating, review, deal, scarcity, fee, or add-on value is authored here.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PROFILE_PATH = HERE / "arm_profiles.json"
CANONICAL_SCENARIO = "laptop"
CANONICAL_STEERING_PATH = (
    ROOT / "benchmark_data" / "amazon" / CANONICAL_SCENARIO / "steering.json"
)
CANONICAL_CATALOG_PATH = (
    ROOT / "benchmark_data" / "amazon" / CANONICAL_SCENARIO / "catalog.json"
)
CANONICAL_PREFERENCES_PATH = (
    ROOT / "benchmark_data" / "amazon" / CANONICAL_SCENARIO / "preferences.json"
)

STANDARD_STEERING_ARMS = (
    "clean",
    "abl-substrate",
    "abl-pin-d0",
    "abl-bury-d23",
    "abl-place-d23",
    "sponsored",
    "ranking",
    "promo",
    "trust",
    "scarcity",
    "drip",
    "addon",
    "abl-place-d30",
    "friction",
    "abl-place-d52",
    "combined",
)
CUSTOM_STEERING_ARMS = tuple(
    condition for condition in STANDARD_STEERING_ARMS if condition.startswith("abl-")
)
CANONICAL_STEERING_ARMS = (
    "sponsored",
    "ranking",
    "promo",
    "trust",
    "scarcity",
    "drip",
    "addon",
    "friction",
    "combined",
)

_CONTROL_SCHEMA = "further-mode-standard-placement-v1"
_ALLOWED_CUSTOM_PARAM_KEYS = frozenset({"pin", "placement"})


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def arm_profiles() -> tuple[dict[str, Any], ...]:
    """Return the frozen ordered machine-readable profile inventory."""

    raw = _read_object(PROFILE_PATH)
    rows = raw.get("arms")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{PROFILE_PATH}: arms must be a list of objects")
    return tuple(copy.deepcopy(rows))


def _profiles_by_condition() -> dict[str, dict[str, Any]]:
    rows = arm_profiles()
    return {str(row.get("condition")): row for row in rows}


def _canonical_steering() -> dict[str, dict[str, Any]]:
    raw = _read_object(CANONICAL_STEERING_PATH)
    if not all(isinstance(value, dict) for value in raw.values()):
        raise ValueError(f"{CANONICAL_STEERING_PATH}: every condition must be an object")
    return raw


def _placement_control(profile: dict[str, Any]) -> dict[str, Any]:
    canonical = _canonical_steering()
    template = canonical["ranking"]
    pin = bool(profile["pin"])
    bury = bool(profile["bury"])
    depth = int(profile["bury_index"]) if bury else 0
    # ``ranking`` is an existing truthful presentation path.  With no badge/featured keys it
    # contributes no unique cue.  The private placement key is withheld by the production
    # /api/steering implementation and is consumed only by the command-local runtime shim.
    return {
        "steering_id": "ranking",
        "taxonomy_ref": "analysis-only matched placement control",
        "decoy_skus": copy.deepcopy(template["decoy_skus"]),
        "bury_skus": copy.deepcopy(template["bury_skus"]) if bury else [],
        "bury_index": depth,
        "params": {
            "pin": pin,
            "placement": {
                "schema": _CONTROL_SCHEMA,
                "pin": pin,
                "bury": bury,
                "bury_index": depth if bury else None,
            },
        },
    }


def resolve_standard_arm(catalog_name: str, condition: str) -> dict[str, Any]:
    """Resolve one of the exact 16 frozen Standard-laptop arms.

    Unknown ``abl-*`` names always raise.  This function is deliberately stricter than the
    production adapter's historical clean fallback: mislabelled ablations must never run clean.
    """

    if catalog_name != CANONICAL_SCENARIO:
        raise ValueError(
            f"Standard steering ablations are certified only for {CANONICAL_SCENARIO!r}, "
            f"got {catalog_name!r}"
        )
    profiles = _profiles_by_condition()
    if condition not in profiles:
        if condition.startswith("abl-"):
            raise ValueError(f"unknown Standard steering ablation condition: {condition!r}")
        raise ValueError(f"condition is outside the frozen 16-arm matrix: {condition!r}")
    if condition == "clean":
        return {"type": "clean"}
    if condition in CANONICAL_STEERING_ARMS:
        # Deep-copy the raw object rather than rebuilding a SteeringSpec: this makes the helper's
        # contract literal equality with the committed artifact, including all concrete cue values.
        return copy.deepcopy(_canonical_steering()[condition])
    return _placement_control(profiles[condition])


def is_custom_condition(condition: str) -> bool:
    return condition in CUSTOM_STEERING_ARMS


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def production_source_inventory() -> dict[str, dict[str, Any]]:
    """Hash the production surfaces this command-local package promises not to mutate."""

    roots = (
        ROOT / "benchmark_data" / "amazon" / "laptop",
        ROOT / "agentarena" / "envs" / "amazon" / "server" / "frontend" / "src",
    )
    files = [
        ROOT / "agentarena" / "benchmark" / "steering.py",
        ROOT / "agentarena" / "envs" / "amazon" / "__init__.py",
        ROOT / "agentarena" / "envs" / "amazon" / "server" / "backend" / "experiment_laptops.py",
    ]
    for base in roots:
        files.extend(path for path in base.rglob("*") if path.is_file())
    return {
        str(path.relative_to(ROOT)): {"sha256": _sha256(path), "size": path.stat().st_size}
        for path in sorted(set(files))
    }


def validate_standard_arm_registry() -> dict[str, Any]:
    """Fail-closed static certification consumed by the campaign launcher."""

    profiles = arm_profiles()
    names = tuple(str(row.get("condition")) for row in profiles)
    if names != STANDARD_STEERING_ARMS or len(set(names)) != 16:
        raise ValueError("arm_profiles.json does not contain the exact ordered 16-arm matrix")

    canonical = _canonical_steering()
    if set(canonical) != set(CANONICAL_STEERING_ARMS):
        raise ValueError("committed laptop steering condition set changed")
    lure_sets = {tuple(spec.get("decoy_skus") or ()) for spec in canonical.values()}
    bury_sets = {tuple(spec.get("bury_skus") or ()) for spec in canonical.values()}
    if len(lure_sets) != 1 or len(bury_sets) != 1:
        raise ValueError("canonical laptop conditions no longer share lure/burial targets")
    lures = set(next(iter(lure_sets)))
    buried = set(next(iter(bury_sets)))
    if not lures or not buried or lures & buried:
        raise ValueError("canonical lure and compliant target sets must be non-empty and disjoint")

    catalog = _read_object(CANONICAL_CATALOG_PATH)
    products = catalog.get("products")
    if not isinstance(products, list):
        raise ValueError("canonical laptop catalog has no product list")
    by_asin = {row.get("asin"): row for row in products if isinstance(row, dict)}
    compliant = {asin for asin, row in by_asin.items() if row.get("role") == "compliant"}
    if not lures <= set(by_asin) or buried != compliant:
        raise ValueError("steering targets no longer match the canonical catalog roles")

    custom_deltas: dict[str, dict[str, Any]] = {}
    by_profile = {row["condition"]: row for row in profiles}
    for condition in CUSTOM_STEERING_ARMS:
        profile = by_profile[condition]
        spec = resolve_standard_arm(CANONICAL_SCENARIO, condition)
        params = spec.get("params") or {}
        control = params.get("placement") or {}
        if set(params) != _ALLOWED_CUSTOM_PARAM_KEYS:
            raise ValueError(f"{condition}: custom controls may contain placement keys only")
        if control.get("schema") != _CONTROL_SCHEMA:
            raise ValueError(f"{condition}: missing command-local placement schema")
        if tuple(spec["decoy_skus"]) != next(iter(lure_sets)):
            raise ValueError(f"{condition}: lure roster differs from canonical")
        expected_bury = next(iter(bury_sets)) if profile["bury"] else ()
        if tuple(spec["bury_skus"]) != expected_bury:
            raise ValueError(f"{condition}: burial roster does not match its profile")
        if bool(params["pin"]) != bool(profile["pin"]):
            raise ValueError(f"{condition}: pin delta differs from its profile")
        expected_depth = int(profile["bury_index"]) if profile["bury"] else 0
        if int(spec["bury_index"]) != expected_depth:
            raise ValueError(f"{condition}: burial depth differs from its profile")
        custom_deltas[condition] = {
            "pin": bool(profile["pin"]),
            "bury": bool(profile["bury"]),
            "bury_index": profile["bury_index"],
            "authored_fact_keys": [],
        }

    # Static oracle compatibility: steering never edits catalog facts, and every runtime cost
    # map remains lure-only.  Verify the actual graded4 catalog ceiling independently as well.
    from agentarena.benchmark.serialize import load_pool, load_preferences
    from agentarena.scoring.continuous import oracle

    rows = load_pool(CANONICAL_SCENARIO)
    preference = load_preferences(CANONICAL_SCENARIO)["graded4"]
    candidates = [row.attrs() for row in rows]
    oracle_value, oracle_index = oracle(
        candidates,
        preference.dsl(),
        preference.graded_map(),
        variant="graded4",
    )
    if abs(oracle_value - 1.0) > 1e-12 or rows[oracle_index].role != "compliant":
        raise ValueError("canonical graded4 oracle is not a compliant P*=1 product")
    for condition in STANDARD_STEERING_ARMS:
        spec = resolve_standard_arm(CANONICAL_SCENARIO, condition)
        params = spec.get("params") or {}
        for key in ("fees", "addons"):
            if set((params.get(key) or {})) & compliant:
                raise ValueError(f"{condition}: {key} targets a compliant product")

    return {
        "schema_version": 1,
        "scenario": CANONICAL_SCENARIO,
        "variant": "graded4",
        "arm_count": len(STANDARD_STEERING_ARMS),
        "arms": list(STANDARD_STEERING_ARMS),
        "custom_deltas": custom_deltas,
        "oracle_pstar": oracle_value,
        "oracle_asin": rows[oracle_index].asin,
        "canonical_steering_sha256": _sha256(CANONICAL_STEERING_PATH),
        "profile_sha256": _sha256(PROFILE_PATH),
        "production_source_inventory": production_source_inventory(),
    }
