from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .common import (
    IntegrityError,
    canonical_bytes,
    normalize_token,
    read_json,
    sha256_bytes,
    stable_int,
    write_json_create_only,
)


def _task(
    *,
    seed: int,
    split: str,
    family: str,
    variant: str,
    condition: str,
    repetition: int,
    role: str,
    catalog_sizes: list[int],
) -> dict[str, Any]:
    task_seed = stable_int(seed, split, family, variant, condition, repetition)
    catalog_seed = stable_int(seed, "catalog", split, family, repetition)
    task_key = f"shadow-{split}-{family}-{variant}-{condition}-r{repetition:02d}"
    return {
        "task_key": task_key,
        "split": split,
        "validation_role": role,
        "family": family,
        "variant": variant,
        "condition": condition,
        "repetition": repetition,
        "task_seed": task_seed,
        "catalog_seed": catalog_seed,
        "catalog_size": catalog_sizes[catalog_seed % len(catalog_sizes)],
        "product_id_prefix": f"SHADOW-{split.upper()}-{family.upper().replace('_', '-')}-",
        "materialized": None,
    }


def generate_split(config: dict[str, Any]) -> dict[str, Any]:
    seed = int(config["seed"])
    shadow = config["shadow"]
    variants = list(config["caveat_shop"]["variants"])
    conditions = list(config["caveat_shop"]["conditions"])
    catalog_sizes = [int(value) for value in shadow["catalog_sizes"]]
    if len(variants) != 4 or conditions != ["clean", "combined"]:
        raise IntegrityError("the frozen shadow balance requires four variants and clean/combined")
    if not catalog_sizes or any(value < 1 for value in catalog_sizes):
        raise IntegrityError("catalog_sizes must contain positive integers")

    tasks: list[dict[str, Any]] = []
    cells_per_repetition = len(variants) * len(conditions)
    train_per_family = int(shadow["train_tasks_per_family"])
    validation_per_family = int(shadow["validation_tasks_per_family"])
    if train_per_family % cells_per_repetition or validation_per_family % cells_per_repetition:
        raise IntegrityError("per-family task counts must be divisible by variant-condition cells")

    for family in shadow["train_families"]:
        for repetition in range(train_per_family // cells_per_repetition):
            for variant in variants:
                for condition in conditions:
                    tasks.append(
                        _task(
                            seed=seed,
                            split="train",
                            family=family,
                            variant=variant,
                            condition=condition,
                            repetition=repetition,
                            role="train",
                            catalog_sizes=catalog_sizes,
                        )
                    )

    role_counts = shadow["validation_roles_per_family"]
    role_repetitions: list[str] = []
    for role in ("selection", "gate", "reserve"):
        count = int(role_counts[role])
        if count % cells_per_repetition:
            raise IntegrityError(f"validation role {role} must be balanced over all task cells")
        role_repetitions.extend([role] * (count // cells_per_repetition))
    if len(role_repetitions) * cells_per_repetition != validation_per_family:
        raise IntegrityError("validation role counts do not sum to validation_tasks_per_family")

    for family in shadow["validation_families"]:
        for repetition, role in enumerate(role_repetitions):
            for variant in variants:
                for condition in conditions:
                    tasks.append(
                        _task(
                            seed=seed,
                            split="validation",
                            family=family,
                            variant=variant,
                            condition=condition,
                            repetition=repetition,
                            role=role,
                            catalog_sizes=catalog_sizes,
                        )
                    )

    tasks.sort(key=lambda row: row["task_key"])
    core = {
        "schema_version": 1,
        "campaign_id": config["campaign_id"],
        "seed": seed,
        "tasks": tasks,
    }
    return {**core, "split_sha256": sha256_bytes(canonical_bytes(core))}


def audit_split(
    split: dict[str, Any],
    config: dict[str, Any],
    *,
    require_materialized: bool = False,
    original_benchmark_lock: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tasks = split.get("tasks")
    if not isinstance(tasks, list):
        raise IntegrityError("split tasks must be a list")
    core = {key: value for key, value in split.items() if key != "split_sha256"}
    expected_hash = sha256_bytes(canonical_bytes(core))
    if split.get("split_sha256") != expected_hash:
        raise IntegrityError("split_sha256 does not match canonical split bytes")

    shadow = config["shadow"]
    train_families = set(shadow["train_families"])
    validation_families = set(shadow["validation_families"])
    forbidden = set(shadow["forbidden_original_categories"])
    if train_families & validation_families:
        raise IntegrityError("train and validation category/schema families overlap")
    normalized_forbidden = {normalize_token(value) for value in forbidden}
    for family in train_families | validation_families:
        normalized = normalize_token(family)
        if normalized in normalized_forbidden:
            raise IntegrityError(f"shadow family overlaps original CAVEAT-Shop category: {family}")

    keys = [str(row.get("task_key")) for row in tasks]
    if len(keys) != len(set(keys)):
        raise IntegrityError("duplicate shadow task_key")
    seeds = [(row.get("task_seed"), row.get("catalog_seed")) for row in tasks]
    if len(seeds) != len(set(seeds)):
        raise IntegrityError("duplicate task/catalog seed pair")

    split_counts = Counter(str(row.get("split")) for row in tasks)
    expected_train = len(train_families) * int(shadow["train_tasks_per_family"])
    expected_validation = len(validation_families) * int(shadow["validation_tasks_per_family"])
    if split_counts != Counter(train=expected_train, validation=expected_validation):
        raise IntegrityError(f"unexpected split counts: {dict(split_counts)}")

    valid_variants = set(config["caveat_shop"]["variants"])
    valid_conditions = set(config["caveat_shop"]["conditions"])
    product_ids: dict[str, str] = {}
    materialized_count = 0
    original_benchmark_lock = original_benchmark_lock or {}
    original_ids = {
        str(product_id)
        for ids in original_benchmark_lock.get("original_catalog_product_ids", {}).values()
        for product_id in ids
    }
    original_hashes = {
        str(digest) for digest in original_benchmark_lock.get("files", {}).values()
    }
    frontend_hash = original_benchmark_lock.get("frontend_dist_sha256")
    if frontend_hash:
        original_hashes.add(str(frontend_hash))
    for row in tasks:
        expected_families = train_families if row["split"] == "train" else validation_families
        if row.get("family") not in expected_families:
            raise IntegrityError(f"task assigned to wrong family split: {row.get('task_key')}")
        if row.get("variant") not in valid_variants or row.get("condition") not in valid_conditions:
            raise IntegrityError(f"invalid task cell: {row.get('task_key')}")
        materialized = row.get("materialized")
        if materialized is None:
            if require_materialized:
                raise IntegrityError(f"task is not materialized: {row.get('task_key')}")
            continue
        materialized_count += 1
        if not isinstance(materialized, dict):
            raise IntegrityError(f"malformed materialization: {row.get('task_key')}")
        instruction = normalize_token(str(materialized.get("instruction", "")))
        if not instruction:
            raise IntegrityError(f"materialized task lacks instruction: {row.get('task_key')}")
        for forbidden_token in normalized_forbidden:
            if forbidden_token and forbidden_token in instruction:
                raise IntegrityError(
                    f"original category token appears in shadow instruction: {row.get('task_key')}"
                )
        catalog_hash = str(materialized.get("catalog_sha256", ""))
        instruction_hash = str(materialized.get("instruction_sha256", ""))
        if len(catalog_hash) != 64 or len(instruction_hash) != 64:
            raise IntegrityError(f"materialization lacks SHA-256 identities: {row.get('task_key')}")
        if catalog_hash in original_hashes or instruction_hash in original_hashes:
            raise IntegrityError(f"materialization matches original benchmark bytes: {row.get('task_key')}")
        ids = materialized.get("product_ids")
        if not isinstance(ids, list) or len(ids) != int(row["catalog_size"]):
            raise IntegrityError(f"materialized product IDs/count mismatch: {row.get('task_key')}")
        prefix = str(row["product_id_prefix"])
        for product_id in ids:
            product_id = str(product_id)
            if not product_id.startswith(prefix):
                raise IntegrityError(f"product ID violates shadow namespace: {product_id}")
            if product_id in original_ids:
                raise IntegrityError(f"product ID overlaps original benchmark: {product_id}")
            prior = product_ids.setdefault(product_id, str(row["task_key"]))
            if prior != row["task_key"]:
                raise IntegrityError(f"product ID reused across tasks: {product_id}")

    balance: dict[str, dict[str, int]] = {}
    for split_name in ("train", "validation"):
        rows = [row for row in tasks if row["split"] == split_name]
        counts = Counter(f"{row['variant']}::{row['condition']}" for row in rows)
        if len(set(counts.values())) != 1:
            raise IntegrityError(f"{split_name} is not balanced by variant and condition")
        balance[split_name] = dict(sorted(counts.items()))

    return {
        "valid": True,
        "split_sha256": expected_hash,
        "task_counts": dict(split_counts),
        "validation_roles": dict(
            Counter(
                str(row["validation_role"])
                for row in tasks
                if row["split"] == "validation"
            )
        ),
        "materialized_tasks": materialized_count,
        "balance": balance,
    }


def generate_to_path(config_path: Path, output_path: Path) -> dict[str, Any]:
    config = read_json(config_path)
    split = generate_split(config)
    audit_split(split, config)
    write_json_create_only(output_path, split)
    return split
