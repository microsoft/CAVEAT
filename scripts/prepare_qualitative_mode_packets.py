#!/usr/bin/env python3
"""Prepare blinded mode-evidence packets and merge isolated AI coding passes.

This script is deliberately analysis-only.  Its sampler derives balancing
metadata from run-directory names and does not open any outcome-bearing file
until after the sample has been fixed.  The coder-facing packets and the
provenance/outcome mapping are emitted into separate directories.

See ``analysis/mode_evidence/QUALITATIVE_TOOLING.md`` for the workflow and the
blinding boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import stat
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT / "analysis" / "mode_evidence" / "qualitative_sampling_spec_v1.json"
DEFAULT_SCHEMA = ROOT / "analysis" / "mode_evidence" / "coding_record.schema.json"
DEFAULT_CODEBOOK = ROOT / "analysis" / "mode_evidence" / "sensitizing_codebook.json"
DEFAULT_AMAZON_ROOT = ROOT / "results" / "overhaul_lb"
DEFAULT_EXTERNAL_ROOT = ROOT / "results" / "clone8_hardened_targeted"

PASS_ROLES = ("inductive", "deductive", "skeptical")
EPISODE_DEFINITIONS = {
    "contract": "Instruction interpretation before search.",
    "discovery": "Construction of the candidate set.",
    "resolution": "Collection and reconciliation of preference-relevant facts.",
    "choice": "Stopping claim, comparison, and proposed identity.",
    "transaction": "Cart mutation through order or terminal no-order.",
}
EVIDENCE_HIERARCHY = {
    "state": "Evaluator/catalog/order state or exact action result (strongest).",
    "behavior": "URL, browser action, visible observation, request/result, or tool error.",
    "justification_only": "Model reasoning, TODO, memory, or final prose only.",
}


class ToolingError(RuntimeError):
    """A fail-closed qualitative-tooling error."""


@dataclass(frozen=True)
class Candidate:
    """Path-derived run metadata; constructing this never opens a run file."""

    trajectory_path: Path
    source_root: Path
    relative_path: str
    env: str
    scaffold: str
    model: str
    task_id: str
    condition: str
    scenario: str
    variant: str
    repetition: str
    stratum: str | None = None

    @property
    def stable_key(self) -> str:
        return self.relative_path


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolingError(f"cannot read JSON {path}: {exc}") from exc


def _write_json(path: Path, payload: Any, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if mode is not None:
        path.chmod(mode)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rank(seed: str, *parts: object) -> int:
    material = "|".join([seed, *(str(part) for part in parts)])
    return int(hashlib.sha256(material.encode("utf-8")).hexdigest(), 16)


def _parse_run_name(name: str) -> tuple[str, str, str, str, str]:
    parts = name.split("__")
    if len(parts) != 5 or any(not part for part in parts):
        raise ToolingError(f"run directory does not match env__scaffold__model__task__condition: {name}")
    return tuple(parts)  # type: ignore[return-value]


def _split_task(task_id: str, allowed_variants: set[str]) -> tuple[str, str]:
    if "-" not in task_id:
        raise ToolingError(f"task has no variant suffix: {task_id}")
    scenario, variant = task_id.rsplit("-", 1)
    if variant not in allowed_variants:
        raise ToolingError(f"unexpected task variant {variant!r}: {task_id}")
    return scenario, variant


def discover_candidates(
    source_root: Path,
    *,
    allowed_variants: set[str],
    allowed_conditions: set[str],
    allowed_envs: set[str],
    model_strata: Mapping[str, Sequence[str]] | None = None,
) -> list[Candidate]:
    """Discover candidates using paths only, preserving outcome blinding."""

    root = source_root.resolve()
    if not root.is_dir():
        raise ToolingError(f"source root is not a directory: {root}")
    model_to_stratum: dict[str, str] = {}
    if model_strata:
        for stratum, models in model_strata.items():
            for model in models:
                if model in model_to_stratum:
                    raise ToolingError(f"model {model!r} occurs in multiple strata")
                model_to_stratum[model] = stratum

    candidates: list[Candidate] = []
    for trajectory_path in sorted(root.rglob("trajectory.json")):
        try:
            env, scaffold, model, task_id, condition = _parse_run_name(trajectory_path.parent.name)
        except ToolingError:
            # Other nested artifacts are not silently made eligible.
            continue
        if env not in allowed_envs or condition not in allowed_conditions:
            continue
        try:
            scenario, variant = _split_task(task_id, allowed_variants)
        except ToolingError:
            continue
        if model_strata and model not in model_to_stratum:
            raise ToolingError(f"eligible model is absent from frozen strata: {model}")
        candidates.append(
            Candidate(
                trajectory_path=trajectory_path,
                source_root=root,
                relative_path=trajectory_path.relative_to(root).as_posix(),
                env=env,
                scaffold=scaffold,
                model=model,
                task_id=task_id,
                condition=condition,
                scenario=scenario,
                variant=variant,
                repetition=trajectory_path.parent.parent.name,
                stratum=model_to_stratum.get(model),
            )
        )
    return candidates


def _balanced_quotas(labels: Sequence[str], total: int, seed: str, key: str) -> dict[str, int]:
    if not labels:
        raise ToolingError("cannot balance over an empty label set")
    base, extra = divmod(total, len(labels))
    ranked = sorted(labels, key=lambda label: _rank(seed, "quota", key, label))
    return {label: base + int(index < extra) for index, label in enumerate(ranked)}


def select_amazon_holdout(candidates: Sequence[Candidate], spec: Mapping[str, Any]) -> list[Candidate]:
    """Select 64 Amazon runs with exact frozen condition/variant quotas."""

    seed = str(spec["sampling_seed_sha256"])
    amazon = spec["amazon"]
    variants = tuple(amazon["variants"])
    scenarios = tuple(amazon["scenarios"])
    strata = tuple(amazon["model_strata"].keys())
    conditions = tuple(amazon["conditions"].keys())
    per_bucket = int(amazon["per_condition_variant_quota"])
    eligible = [
        candidate
        for candidate in candidates
        if candidate.env == "amazon"
        and candidate.variant in variants
        and candidate.scenario in scenarios
        and candidate.condition in conditions
        and candidate.stratum in strata
    ]
    by_bucket: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for candidate in eligible:
        by_bucket[(candidate.condition, candidate.variant)].append(candidate)
    for condition in conditions:
        for variant in variants:
            if len(by_bucket[(condition, variant)]) < per_bucket:
                raise ToolingError(
                    f"Amazon bucket {condition}/{variant} has {len(by_bucket[(condition, variant)])} "
                    f"candidates; needs {per_bucket}"
                )

    chosen: list[Candidate] = []
    global_model_counts: Counter[str] = Counter()
    global_repeat_counts: Counter[str] = Counter()
    for condition in conditions:
        condition_total = int(amazon["conditions"][condition])
        if condition_total != per_bucket * len(variants):
            raise ToolingError(f"condition quota and variant quota disagree for {condition}")
        scenario_remaining = _balanced_quotas(scenarios, condition_total, seed, f"{condition}:scenario")
        stratum_remaining = _balanced_quotas(strata, condition_total, seed, f"{condition}:stratum")
        variant_scenario: Counter[tuple[str, str]] = Counter()
        variant_stratum: Counter[tuple[str, str]] = Counter()
        condition_model: Counter[str] = Counter()

        for round_index in range(per_bucket):
            round_variants = sorted(
                variants,
                key=lambda value: _rank(seed, "bucket-order", condition, round_index, value),
            )
            for variant in round_variants:
                pool = [
                    candidate
                    for candidate in by_bucket[(condition, variant)]
                    if scenario_remaining[candidate.scenario] > 0
                    and stratum_remaining[str(candidate.stratum)] > 0
                ]
                if not pool:
                    raise ToolingError(
                        f"quota dead-end selecting Amazon {condition}/{variant}; "
                        f"scenario_remaining={scenario_remaining}, stratum_remaining={stratum_remaining}"
                    )
                selected = min(
                    pool,
                    key=lambda candidate: (
                        variant_scenario[(variant, candidate.scenario)],
                        variant_stratum[(variant, str(candidate.stratum))],
                        condition_model[candidate.model],
                        global_model_counts[candidate.model],
                        global_repeat_counts[candidate.repetition],
                        _rank(seed, "amazon", candidate.stable_key),
                    ),
                )
                chosen.append(selected)
                by_bucket[(condition, variant)].remove(selected)
                scenario_remaining[selected.scenario] -= 1
                stratum_remaining[str(selected.stratum)] -= 1
                variant_scenario[(variant, selected.scenario)] += 1
                variant_stratum[(variant, str(selected.stratum))] += 1
                condition_model[selected.model] += 1
                global_model_counts[selected.model] += 1
                global_repeat_counts[selected.repetition] += 1
        if any(scenario_remaining.values()) or any(stratum_remaining.values()):
            raise ToolingError(f"Amazon quotas were not exhausted for {condition}")

    expected = int(amazon["sample_size"])
    if len(chosen) != expected or len({item.stable_key for item in chosen}) != expected:
        raise ToolingError(f"Amazon selection produced {len(chosen)} rows, expected {expected}")
    return chosen


def select_external_transfer(candidates: Sequence[Candidate], spec: Mapping[str, Any]) -> list[Candidate]:
    """Select four runs per external environment, two per condition."""

    seed = str(spec["sampling_seed_sha256"])
    external = spec["external_transfer"]
    environments = tuple(external["environments"])
    condition_quota = {str(k): int(v) for k, v in external["per_environment_condition"].items()}
    allowed_conditions = tuple(condition_quota)
    eligible = [
        candidate
        for candidate in candidates
        if candidate.env in environments and candidate.condition in allowed_conditions
    ]
    by_env_condition: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for candidate in eligible:
        by_env_condition[(candidate.env, candidate.condition)].append(candidate)

    chosen: list[Candidate] = []
    for environment in environments:
        for condition, quota in condition_quota.items():
            if len(by_env_condition[(environment, condition)]) < quota:
                raise ToolingError(
                    f"external bucket {environment}/{condition} has "
                    f"{len(by_env_condition[(environment, condition)])}; needs {quota}"
                )
        used_models: set[str] = set()
        used_variants: set[str] = set()
        used_pairs: set[tuple[str, str]] = set()
        condition_remaining = dict(condition_quota)
        slot_conditions: list[str] = []
        for slot in range(sum(condition_quota.values())):
            available = [name for name, remaining in condition_remaining.items() if remaining]
            condition = min(
                available,
                key=lambda name: (
                    -condition_remaining[name],
                    _rank(seed, "external-condition", environment, slot, name),
                ),
            )
            slot_conditions.append(condition)
            condition_remaining[condition] -= 1

        for slot, condition in enumerate(slot_conditions):
            pool = [
                candidate
                for candidate in by_env_condition[(environment, condition)]
                if (candidate.model, candidate.variant) not in used_pairs
            ]
            both_new = [
                candidate
                for candidate in pool
                if candidate.model not in used_models and candidate.variant not in used_variants
            ]
            if both_new:
                pool = both_new
            else:
                model_new = [candidate for candidate in pool if candidate.model not in used_models]
                if model_new:
                    pool = model_new
            if not pool:
                raise ToolingError(f"no distinct external candidate for {environment}/{condition}")
            selected = min(
                pool,
                key=lambda candidate: (
                    int(candidate.model in used_models) + int(candidate.variant in used_variants),
                    int(candidate.model in used_models),
                    int(candidate.variant in used_variants),
                    _rank(seed, "external", environment, slot, candidate.stable_key),
                ),
            )
            chosen.append(selected)
            used_models.add(selected.model)
            used_variants.add(selected.variant)
            used_pairs.add((selected.model, selected.variant))

        if len(used_pairs) != int(external["per_environment"]):
            raise ToolingError(f"external model/variant pairs are not distinct in {environment}")

    expected = int(external["sample_size"])
    if len(chosen) != expected or len({(item.source_root, item.stable_key) for item in chosen}) != expected:
        raise ToolingError(f"external selection produced {len(chosen)} rows, expected {expected}")
    return chosen


def _packet_id(candidate: Candidate, seed: str) -> str:
    digest = hashlib.sha256(f"{seed}|packet|{candidate.stable_key}".encode("utf-8")).hexdigest()
    return f"Q{digest[:20]}"


def _packet_from_trajectory(packet_id: str, sample_set: str, trajectory: Mapping[str, Any]) -> dict[str, Any]:
    instruction = trajectory.get("instruction")
    steps = trajectory.get("steps")
    if not isinstance(instruction, str) or not isinstance(steps, list):
        raise ToolingError(f"trajectory for {packet_id} lacks a string instruction or step list")
    raw_trace: list[dict[str, Any]] = []
    allowed_step_fields = ("index", "action", "reasoning", "url", "note", "has_image")
    for ordinal, step in enumerate(steps):
        if not isinstance(step, Mapping):
            raise ToolingError(f"trajectory step {ordinal} for {packet_id} is not an object")
        raw_trace.append({field: step.get(field) for field in allowed_step_fields})
    return {
        "packet_schema_version": 1,
        "packet_id": packet_id,
        "sample_set": sample_set,
        "status": "outcome-blinded",
        "blinding_notice": (
            "Top-level answer, evaluator fields, scores, selected/hero identities, model, condition, "
            "and source path were intentionally withheld. Product identities naturally spoken or acted "
            "on inside the raw trace were not rewritten."
        ),
        "evidence_hierarchy": EVIDENCE_HIERARCHY,
        "episode_definitions": EPISODE_DEFINITIONS,
        "instruction": instruction,
        "trace": raw_trace,
    }


def _extract_sealed_outcomes(summary: Mapping[str, Any], trajectory: Mapping[str, Any]) -> dict[str, Any]:
    outcome_keys = (
        "outcome",
        "success",
        "took_bait",
        "error",
        "num_steps",
        "seconds",
        "preservation",
        "preservation_strict",
        "preservation_cont",
        "strict_binary",
        "literal_hero",
        "resistance_margin",
    )
    identity_keys = ("chosen", "chosen_label", "hero_identity")
    evaluation = trajectory.get("evaluation")
    if not isinstance(evaluation, Mapping):
        evaluation = {}
    outcomes = {key: summary.get(key, evaluation.get(key)) for key in outcome_keys}
    identities = {key: summary.get(key, evaluation.get(key)) for key in identity_keys}
    details = evaluation.get("details")
    evaluator_role = details.get("role") if isinstance(details, Mapping) else None
    return {"outcomes": outcomes, "withheld_identities": identities, "evaluator_role": evaluator_role}


def _selection_balance(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    dimensions = ("sample_set", "env", "condition", "variant", "scenario", "stratum", "model", "repetition")
    return {
        dimension: dict(sorted(Counter(str(row.get(dimension)) for row in rows).items()))
        for dimension in dimensions
    }


def prepare_packets(
    amazon_root: Path,
    external_root: Path,
    output: Path,
    spec_path: Path = DEFAULT_SPEC,
    codebook_path: Path = DEFAULT_CODEBOOK,
) -> dict[str, Any]:
    """Select runs, then materialize blinded packets and a separate sealed map."""

    spec = _read_json(spec_path)
    if _sha256_file(codebook_path) != spec.get("frozen_codebook_sha256"):
        raise ToolingError("frozen codebook hash does not match qualitative sampling spec")
    amazon_spec = spec["amazon"]
    external_spec = spec["external_transfer"]
    amazon_candidates = discover_candidates(
        amazon_root,
        allowed_variants=set(amazon_spec["variants"]),
        allowed_conditions=set(amazon_spec["conditions"]),
        allowed_envs={"amazon"},
        model_strata=amazon_spec["model_strata"],
    )
    external_candidates = discover_candidates(
        external_root,
        allowed_variants={"mixed", "graded", "graded3", "graded4"},
        allowed_conditions=set(external_spec["per_environment_condition"]),
        allowed_envs=set(external_spec["environments"]),
    )
    # These two selectors operate only on path-derived Candidate objects.
    amazon_selected = select_amazon_holdout(amazon_candidates, spec)
    external_selected = select_external_transfer(external_candidates, spec)
    selected = [("amazon_holdout", item) for item in amazon_selected] + [
        ("external_transfer", item) for item in external_selected
    ]

    output = output.resolve()
    if output.exists():
        raise ToolingError(f"refusing to overwrite existing output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / f".{output.name}.incomplete-{os.getpid()}"
    if stage.exists():
        raise ToolingError(f"temporary output already exists: {stage}")
    stage.mkdir()
    seed = str(spec["sampling_seed_sha256"])
    mapping_rows: list[dict[str, Any]] = []
    public_rows: list[dict[str, Any]] = []
    seen_packet_ids: set[str] = set()
    try:
        for sample_set, candidate in selected:
            packet_id = _packet_id(candidate, seed)
            if packet_id in seen_packet_ids:
                raise ToolingError(f"opaque packet-id collision: {packet_id}")
            seen_packet_ids.add(packet_id)
            trajectory = _read_json(candidate.trajectory_path)
            if not isinstance(trajectory, Mapping):
                raise ToolingError(f"trajectory is not an object: {candidate.trajectory_path}")
            packet = _packet_from_trajectory(packet_id, sample_set, trajectory)
            packet_path = stage / "coder_packets" / sample_set / f"{packet_id}.json"
            _write_json(packet_path, packet)
            packet_sha = _sha256_file(packet_path)

            summary_path = candidate.trajectory_path.with_name("summary.json")
            summary: Mapping[str, Any] = {}
            if summary_path.exists():
                loaded_summary = _read_json(summary_path)
                if isinstance(loaded_summary, Mapping):
                    summary = loaded_summary
            sealed = _extract_sealed_outcomes(summary, trajectory)
            source_entry = {
                "packet_id": packet_id,
                "sample_set": sample_set,
                "source_root": str(candidate.source_root),
                "trajectory_path": str(candidate.trajectory_path.resolve()),
                "summary_path": str(summary_path.resolve()) if summary_path.exists() else None,
                "trajectory_sha256": _sha256_file(candidate.trajectory_path),
                "summary_sha256": _sha256_file(summary_path) if summary_path.exists() else None,
                "packet_sha256": packet_sha,
                "env": candidate.env,
                "scaffold": candidate.scaffold,
                "model": candidate.model,
                "task_id": candidate.task_id,
                "condition": candidate.condition,
                "scenario": candidate.scenario,
                "variant": candidate.variant,
                "repetition": candidate.repetition,
                "stratum": candidate.stratum,
                **sealed,
            }
            mapping_rows.append(source_entry)
            public_rows.append(
                {
                    "packet_id": packet_id,
                    "sample_set": sample_set,
                    "packet_path": packet_path.relative_to(stage).as_posix(),
                    "packet_sha256": packet_sha,
                }
            )

        coder_manifest = {
            "schema_version": 1,
            "status": "outcome-blinded",
            "label": "AI-coded; researcher audit pending",
            "sampling_spec_sha256": _sha256_file(spec_path),
            "frozen_codebook_sha256": _sha256_file(codebook_path),
            "packet_count": len(public_rows),
            "sample_counts": dict(sorted(Counter(row["sample_set"] for row in public_rows).items())),
            "packets": sorted(public_rows, key=lambda row: row["packet_id"]),
        }
        manifest_path = stage / "coder_manifest.json"
        _write_json(manifest_path, coder_manifest)
        sealed_mapping = {
            "schema_version": 1,
            "access": "RESEARCHER ONLY — DO NOT PROVIDE TO CODERS BEFORE CODING FREEZE",
            "selection_used_run_outcomes": False,
            "sampling_spec_sha256": _sha256_file(spec_path),
            "frozen_codebook_sha256": _sha256_file(codebook_path),
            "balance": _selection_balance(mapping_rows),
            "runs": sorted(mapping_rows, key=lambda row: row["packet_id"]),
        }
        sealed_path = stage / "sealed_researcher_only" / "provenance_and_outcomes.json"
        _write_json(sealed_path, sealed_mapping, mode=stat.S_IRUSR | stat.S_IWUSR)
        integrity = {
            "schema_version": 1,
            "coder_manifest_sha256": _sha256_file(manifest_path),
            "sealed_mapping_sha256": _sha256_file(sealed_path),
            "packet_tree_sha256": _sha256_bytes(
                "\n".join(
                    f"{row['packet_sha256']}  {row['packet_path']}" for row in sorted(public_rows, key=lambda r: r["packet_path"])
                ).encode("utf-8")
            ),
        }
        _write_json(stage / "integrity.json", integrity)
        os.replace(stage, output)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return {
        "output": str(output),
        "packets": len(public_rows),
        "amazon_holdout": len(amazon_selected),
        "external_transfer": len(external_selected),
        "selection_used_run_outcomes": False,
    }


def _json_type_matches(instance: Any, expected: str) -> bool:
    return {
        "object": isinstance(instance, dict),
        "array": isinstance(instance, list),
        "string": isinstance(instance, str),
        "boolean": isinstance(instance, bool),
        "integer": isinstance(instance, int) and not isinstance(instance, bool),
        "number": isinstance(instance, (int, float)) and not isinstance(instance, bool),
        "null": instance is None,
    }.get(expected, False)


def validate_against_schema(instance: Any, schema: Mapping[str, Any], path: str = "$.") -> list[str]:
    """Validate the JSON-Schema subset used by coding_record.schema.json."""

    errors: list[str] = []
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not one of {schema['enum']!r}")
    expected_type = schema.get("type")
    if expected_type and not _json_type_matches(instance, expected_type):
        errors.append(f"{path}: expected {expected_type}, got {type(instance).__name__}")
        return errors
    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in properties:
                    errors.append(f"{path}: unexpected property {key!r}")
        for key, value in instance.items():
            if key in properties:
                errors.extend(validate_against_schema(value, properties[key], f"{path}{key}."))
    elif isinstance(instance, list):
        item_schema = schema.get("items")
        if item_schema:
            for index, value in enumerate(instance):
                errors.extend(validate_against_schema(value, item_schema, f"{path}[{index}]."))
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in instance]
            if len(serialized) != len(set(serialized)):
                errors.append(f"{path}: array items are not unique")
    elif isinstance(instance, str):
        if len(instance) < int(schema.get("minLength", 0)):
            errors.append(f"{path}: string is shorter than minLength={schema['minLength']}")
    elif isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: value is below minimum={schema['minimum']}")
    return errors


def validate_coding_record(
    record: Any,
    schema: Mapping[str, Any],
    codebook: Mapping[str, Any],
    *,
    require_complete_codebook: bool = True,
    allowed_packet_ids: set[str] | None = None,
) -> list[str]:
    errors = validate_against_schema(record, schema)
    if not isinstance(record, dict):
        return errors
    packet_id = record.get("packet_id")
    if allowed_packet_ids is not None and packet_id not in allowed_packet_ids:
        errors.append(f"$.: packet_id {packet_id!r} is not in the blinded packet manifest")
    codes = record.get("codes")
    if not isinstance(codes, list):
        return errors
    code_ids = [entry.get("code_id") for entry in codes if isinstance(entry, dict)]
    duplicates = sorted(code_id for code_id, count in Counter(code_ids).items() if count > 1)
    if duplicates:
        errors.append(f"$.: duplicate code decisions: {duplicates}")
    frozen_ids = {entry["id"] for entry in codebook.get("codes", [])}
    role = record.get("coder_role")
    if require_complete_codebook and role != "user_review":
        missing = sorted(frozen_ids - set(code_ids))
        extra = sorted(set(code_ids) - frozen_ids)
        if missing:
            errors.append(f"$.: missing frozen code decisions: {missing}")
        if extra:
            errors.append(f"$.: unexpected code_id values (use candidate_new_codes): {extra}")
    for index, entry in enumerate(codes):
        if not isinstance(entry, dict):
            continue
        if entry.get("present") and not str(entry.get("excerpt", "")).strip():
            errors.append(f"$.codes[{index}]: present code needs a grounded excerpt")
    return errors


def _load_packet_map(packets_dir: Path | None) -> dict[str, dict[str, Any]]:
    if packets_dir is None:
        return {}
    packet_map: dict[str, dict[str, Any]] = {}
    for path in sorted(packets_dir.rglob("*.json")):
        payload = _read_json(path)
        if not isinstance(payload, dict) or "packet_id" not in payload or "trace" not in payload:
            continue
        packet_id = str(payload["packet_id"])
        if packet_id in packet_map:
            raise ToolingError(f"duplicate packet_id in packet tree: {packet_id}")
        packet_map[packet_id] = payload
    return packet_map


def _load_coding_records(
    records_dir: Path,
    schema: Mapping[str, Any],
    codebook: Mapping[str, Any],
    *,
    allowed_packet_ids: set[str] | None,
    require_complete_codebook: bool,
) -> dict[tuple[str, str], dict[str, Any]]:
    records: dict[tuple[str, str], dict[str, Any]] = {}
    validation_errors: list[str] = []
    for path in sorted(records_dir.rglob("*.json")):
        payload = _read_json(path)
        if not isinstance(payload, dict) or not {"packet_id", "coder_role", "codes"}.issubset(payload):
            continue
        errors = validate_coding_record(
            payload,
            schema,
            codebook,
            require_complete_codebook=require_complete_codebook,
            allowed_packet_ids=allowed_packet_ids,
        )
        validation_errors.extend(f"{path}: {error}" for error in errors)
        key = (str(payload.get("packet_id")), str(payload.get("coder_role")))
        if key in records:
            validation_errors.append(f"{path}: duplicate packet/role record {key}")
        else:
            annotated = dict(payload)
            annotated["_record_path"] = str(path.resolve())
            records[key] = annotated
    if validation_errors:
        preview = "\n".join(validation_errors[:50])
        suffix = "" if len(validation_errors) <= 50 else f"\n... {len(validation_errors) - 50} more"
        raise ToolingError(f"coding-record validation failed:\n{preview}{suffix}")
    if not records:
        raise ToolingError(f"no coding records found under {records_dir}")
    return records


def _decision_map(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["code_id"]): dict(item) for item in record["codes"]}


def _krippendorff_nominal_binary(units: Sequence[Sequence[bool]]) -> float | None:
    complete = [list(unit) for unit in units if len(unit) >= 2]
    if not complete:
        return None
    observed_disagreements = 0
    observed_pairs = 0
    all_values: list[bool] = []
    for values in complete:
        for left in range(len(values)):
            for right in range(len(values)):
                if left == right:
                    continue
                observed_pairs += 1
                observed_disagreements += int(values[left] != values[right])
        all_values.extend(values)
    do = observed_disagreements / observed_pairs if observed_pairs else 0.0
    counts = Counter(all_values)
    total = len(all_values)
    if total < 2:
        return None
    expected_disagreement = 1.0 - sum(count * (count - 1) for count in counts.values()) / (total * (total - 1))
    if expected_disagreement == 0:
        return 1.0 if do == 0 else None
    return 1.0 - do / expected_disagreement


def _repeatability_diagnostics(
    packet_ids: Sequence[str],
    code_ids: Sequence[str],
    records: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    agreements: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    alpha_units: list[list[bool]] = []
    pair_counts: dict[tuple[str, str], list[int]] = {pair: [0, 0] for pair in combinations(PASS_ROLES, 2)}
    incomplete_units = 0
    for packet_id in packet_ids:
        role_maps = {
            role: _decision_map(records[(packet_id, role)])
            for role in PASS_ROLES
            if (packet_id, role) in records
        }
        for code_id in code_ids:
            values = {
                role: role_maps[role][code_id]["present"]
                for role in role_maps
                if code_id in role_maps[role]
            }
            unit = {
                "unit_id": f"{packet_id}|{code_id}",
                "packet_id": packet_id,
                "code_id": code_id,
                "decisions": values,
            }
            if len(values) != len(PASS_ROLES):
                incomplete_units += 1
                continue
            bool_values = [bool(values[role]) for role in PASS_ROLES]
            alpha_units.append(bool_values)
            for left, right in combinations(PASS_ROLES, 2):
                pair_counts[(left, right)][1] += 1
                pair_counts[(left, right)][0] += int(values[left] == values[right])
            if len(set(bool_values)) == 1:
                agreements.append(unit)
            else:
                disagreements.append(unit)
    complete_units = len(agreements) + len(disagreements)
    diagnostics = {
        "label": "AI coding repeatability diagnostic — not human inter-rater reliability",
        "coder_roles": list(PASS_ROLES),
        "complete_units": complete_units,
        "incomplete_units": incomplete_units,
        "unanimous_agreement_units": len(agreements),
        "disagreement_units": len(disagreements),
        "unanimous_agreement_rate": len(agreements) / complete_units if complete_units else None,
        "krippendorff_alpha_nominal": _krippendorff_nominal_binary(alpha_units),
        "pairwise_agreement": {
            f"{left}__{right}": matches / total if total else None
            for (left, right), (matches, total) in pair_counts.items()
        },
    }
    return diagnostics, agreements, disagreements


def _strip_internal_record_fields(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if not key.startswith("_")}


def merge_coding_passes(
    records_dir: Path,
    output: Path,
    *,
    packets_dir: Path | None = None,
    schema_path: Path = DEFAULT_SCHEMA,
    codebook_path: Path = DEFAULT_CODEBOOK,
    spec_path: Path = DEFAULT_SPEC,
    require_all_packets: bool = True,
) -> dict[str, Any]:
    """Validate/merge three isolated passes and emit adjudication/review input."""

    schema = _read_json(schema_path)
    codebook = _read_json(codebook_path)
    spec = _read_json(spec_path)
    if _sha256_file(codebook_path) != spec.get("frozen_codebook_sha256"):
        raise ToolingError("codebook hash does not match frozen qualitative spec")
    packet_map = _load_packet_map(packets_dir)
    allowed_packet_ids = set(packet_map) if packet_map else None
    records = _load_coding_records(
        records_dir,
        schema,
        codebook,
        allowed_packet_ids=allowed_packet_ids,
        require_complete_codebook=True,
    )
    record_packet_ids = {packet_id for packet_id, _ in records}
    packet_ids = sorted(set(packet_map) if packet_map else record_packet_ids)
    if require_all_packets and packet_map:
        absent = sorted(set(packet_map) - record_packet_ids)
        if absent:
            raise ToolingError(f"{len(absent)} blinded packets have no coding record; first={absent[:5]}")
    missing_passes = [
        (packet_id, role)
        for packet_id in packet_ids
        for role in PASS_ROLES
        if (packet_id, role) not in records
    ]
    if missing_passes:
        raise ToolingError(f"missing required isolated passes; first={missing_passes[:10]}")

    code_ids = [entry["id"] for entry in codebook["codes"]]
    diagnostics, agreements, disagreements = _repeatability_diagnostics(packet_ids, code_ids, records)
    review_seed = str(spec["agreement_review_seed_sha256"])
    sample_size = math.ceil(0.20 * len(agreements))
    sampled_agreements = sorted(
        agreements,
        key=lambda unit: _rank(review_seed, "agreement-review", unit["unit_id"]),
    )[:sample_size]

    output = output.resolve()
    if output.exists():
        raise ToolingError(f"refusing to overwrite existing merge output: {output}")
    output.mkdir(parents=True)
    adjudication_manifest: list[dict[str, Any]] = []
    for packet_id in packet_ids:
        per_code: list[dict[str, Any]] = []
        for code_id in code_ids:
            decisions = {
                role: _decision_map(records[(packet_id, role)])[code_id]
                for role in PASS_ROLES
            }
            per_code.append(
                {
                    "code_id": code_id,
                    "requires_adjudication": len({decision["present"] for decision in decisions.values()}) > 1,
                    "pass_decisions": decisions,
                }
            )
        adjudication_input = {
            "schema_version": 1,
            "status": "outcome-blinded adjudication input",
            "packet_id": packet_id,
            "instruction": (
                "Adjudicate every frozen code against the evidence hierarchy. Resolve disagreements explicitly; "
                "do not infer evaluator outcomes or latent cognition. Return a coding_record.schema.json record "
                "with coder_role='adjudicator'."
            ),
            "evidence_hierarchy": EVIDENCE_HIERARCHY,
            "packet": packet_map.get(packet_id),
            "codes": per_code,
            "pass_memos": {
                role: {
                    "memo": records[(packet_id, role)]["memo"],
                    "candidate_new_codes": records[(packet_id, role)].get("candidate_new_codes", []),
                    "negative_cases": records[(packet_id, role)].get("negative_cases", []),
                }
                for role in PASS_ROLES
            },
        }
        path = output / "adjudication_inputs" / f"{packet_id}.json"
        _write_json(path, adjudication_input)
        adjudication_manifest.append(
            {
                "packet_id": packet_id,
                "path": path.relative_to(output).as_posix(),
                "sha256": _sha256_file(path),
                "disagreement_count": sum(item["requires_adjudication"] for item in per_code),
            }
        )

    # "Exemplar" is not a schema flag. Preserve every grounded present-code
    # decision as an exemplar candidate so the researcher, not this script,
    # decides which excerpts become published exemplars.
    adjudicator_records = {
        packet_id: records[(packet_id, "adjudicator")]
        for packet_id in packet_ids
        if (packet_id, "adjudicator") in records
    }
    exemplar_records: list[dict[str, Any]] = []
    negative_cases: list[dict[str, Any]] = []
    exemplar_roles = ("adjudicator",) if len(adjudicator_records) == len(packet_ids) else PASS_ROLES
    for packet_id in packet_ids:
        for role in exemplar_roles:
            record = records.get((packet_id, role))
            if not record:
                continue
            for decision in record["codes"]:
                if decision["present"] and str(decision["excerpt"]).strip():
                    exemplar_records.append(
                        {
                            "packet_id": packet_id,
                            "coder_role": role,
                            "code_id": decision["code_id"],
                            "episode": decision["episode"],
                            "evidence_level": decision["evidence_level"],
                            "step_indices": decision["step_indices"],
                            "excerpt": decision["excerpt"],
                            "interpretation": decision["interpretation"],
                            "confidence": decision["confidence"],
                        }
                    )
        # Preserve explicit negative cases from every pass even when a complete
        # adjudicator set exists; adjudication must not erase dissenting cases.
        for role in (*PASS_ROLES, "adjudicator"):
            record = records.get((packet_id, role))
            if not record:
                continue
            for note in record.get("negative_cases", []):
                negative_cases.append({"packet_id": packet_id, "coder_role": role, "text": note})

    def enrich_units(units: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for unit in units:
            packet_id = str(unit["packet_id"])
            code_id = str(unit["code_id"])
            entry = dict(unit)
            entry["pass_details"] = {
                role: _decision_map(records[(packet_id, role)])[code_id]
                for role in PASS_ROLES
            }
            enriched.append(entry)
        return enriched

    review_packet_ids = {
        unit["packet_id"] for unit in [*disagreements, *sampled_agreements]
    } | {item["packet_id"] for item in exemplar_records} | {item["packet_id"] for item in negative_cases}
    review_packet = {
        "schema_version": 1,
        "status": "AI-coded; researcher audit pending",
        "repeatability_label": "AI repeatability only; not human inter-rater reliability",
        "theme_definitions": codebook["codes"],
        "all_disagreements": enrich_units(disagreements),
        "seeded_agreement_review": {
            "seed_sha256": review_seed,
            "sampling_fraction": 0.20,
            "population": len(agreements),
            "selected": len(sampled_agreements),
            "units": enrich_units(sampled_agreements),
        },
        "all_exemplar_candidates": exemplar_records,
        "all_explicit_negative_cases": negative_cases,
        "blinded_packets": {
            packet_id: packet_map[packet_id]
            for packet_id in sorted(review_packet_ids)
            if packet_id in packet_map
        },
        "researcher_tasks": [
            "Review every disagreement against the raw trace and evidence hierarchy.",
            "Review the seeded 20% agreement sample for systematic shared miscoding.",
            "Approve, reject, or revise every proposed exemplar and negative case.",
            "Record changes as coder_role='user_review' records; do not call this human inter-rater reliability.",
        ],
    }
    _write_json(output / "researcher_review_packet.json", review_packet)
    merged = {
        "schema_version": 1,
        "status": "AI-coded; researcher audit pending",
        "frozen_codebook_sha256": _sha256_file(codebook_path),
        "coding_schema_sha256": _sha256_file(schema_path),
        "packet_count": len(packet_ids),
        "pass_roles": list(PASS_ROLES),
        "repeatability": diagnostics,
        "adjudication_inputs": adjudication_manifest,
        "records": [
            _strip_internal_record_fields(record)
            for _, record in sorted(records.items())
        ],
        "review_packet": "researcher_review_packet.json",
    }
    _write_json(output / "merged_coding_passes.json", merged)
    return {
        "output": str(output),
        "packets": len(packet_ids),
        "records": len(records),
        "disagreements": len(disagreements),
        "agreement_review": len(sampled_agreements),
        "repeatability": diagnostics,
    }


def validate_record_tree(
    records_dir: Path,
    *,
    packets_dir: Path | None = None,
    schema_path: Path = DEFAULT_SCHEMA,
    codebook_path: Path = DEFAULT_CODEBOOK,
    require_complete_codebook: bool = True,
) -> dict[str, Any]:
    schema = _read_json(schema_path)
    codebook = _read_json(codebook_path)
    packet_map = _load_packet_map(packets_dir)
    records = _load_coding_records(
        records_dir,
        schema,
        codebook,
        allowed_packet_ids=set(packet_map) if packet_map else None,
        require_complete_codebook=require_complete_codebook,
    )
    return {
        "valid": True,
        "records": len(records),
        "packets": len({packet for packet, _ in records}),
        "roles": dict(sorted(Counter(role for _, role in records).items())),
        "complete_codebook_required": require_complete_codebook,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="select and emit blinded trajectory packets")
    prepare.add_argument("--amazon-root", type=Path, default=DEFAULT_AMAZON_ROOT)
    prepare.add_argument("--external-root", type=Path, default=DEFAULT_EXTERNAL_ROOT)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    prepare.add_argument("--codebook", type=Path, default=DEFAULT_CODEBOOK)

    validate = subparsers.add_parser("validate", help="validate coding records against the frozen schema/codebook")
    validate.add_argument("--records", type=Path, required=True)
    validate.add_argument("--packets", type=Path)
    validate.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    validate.add_argument("--codebook", type=Path, default=DEFAULT_CODEBOOK)
    validate.add_argument("--allow-incomplete-codebook", action="store_true")

    merge = subparsers.add_parser("merge", help="merge three isolated passes and build adjudication/review packets")
    merge.add_argument("--records", type=Path, required=True)
    merge.add_argument("--packets", type=Path)
    merge.add_argument("--output", type=Path, required=True)
    merge.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    merge.add_argument("--codebook", type=Path, default=DEFAULT_CODEBOOK)
    merge.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    merge.add_argument("--allow-partial-packet-set", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_packets(args.amazon_root, args.external_root, args.output, args.spec, args.codebook)
        elif args.command == "validate":
            result = validate_record_tree(
                args.records,
                packets_dir=args.packets,
                schema_path=args.schema,
                codebook_path=args.codebook,
                require_complete_codebook=not args.allow_incomplete_codebook,
            )
        elif args.command == "merge":
            result = merge_coding_passes(
                args.records,
                args.output,
                packets_dir=args.packets,
                schema_path=args.schema,
                codebook_path=args.codebook,
                spec_path=args.spec,
                require_all_packets=not args.allow_partial_packet_set,
            )
        else:  # pragma: no cover - argparse enforces this
            raise ToolingError(f"unknown command: {args.command}")
    except ToolingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
