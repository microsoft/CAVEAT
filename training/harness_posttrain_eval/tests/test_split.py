from __future__ import annotations

import pytest

from harness_posttrain_eval.common import IntegrityError, canonical_bytes, sha256_bytes
from harness_posttrain_eval.split import audit_split, generate_split


def test_split_is_deterministic_balanced_and_disjoint(config):
    first = generate_split(config)
    second = generate_split(config)
    assert first == second
    audit = audit_split(first, config)
    assert audit["task_counts"] == {"train": 768, "validation": 192}
    assert audit["validation_roles"] == {"selection": 32, "gate": 64, "reserve": 96}
    assert set(config["shadow"]["train_families"]).isdisjoint(
        config["shadow"]["validation_families"]
    )
    assert len({task["task_key"] for task in first["tasks"]}) == 960


def test_split_rejects_original_category_family(config):
    config["shadow"]["train_families"][0] = "laptop"
    split = generate_split(config)
    with pytest.raises(IntegrityError, match="overlaps original Amazon category"):
        audit_split(split, config)


def test_materialized_split_rejects_original_product_id(config):
    split = generate_split(config)
    task = split["tasks"][0]
    task["materialized"] = {
        "instruction": "Choose the best camera.",
        "catalog_sha256": "1" * 64,
        "instruction_sha256": "2" * 64,
        "product_ids": ["EXP-LAPTOP-01"] * task["catalog_size"],
    }
    core = {key: value for key, value in split.items() if key != "split_sha256"}
    split["split_sha256"] = sha256_bytes(canonical_bytes(core))
    lock = {"original_catalog_product_ids": {"laptop": ["EXP-LAPTOP-01"]}, "files": {}}
    with pytest.raises(IntegrityError, match="shadow namespace"):
        audit_split(split, config, original_benchmark_lock=lock)
