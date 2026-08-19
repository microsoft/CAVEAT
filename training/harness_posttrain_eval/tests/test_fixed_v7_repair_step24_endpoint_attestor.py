from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_posttrain_eval import fixed_v7_repair_step24_endpoint_attestor as attestor


def _fanout() -> dict:
    inventory = []
    excluded = []
    for offset in range(16):
        variant = "graded" if offset < 8 else "mixed"
        ordinal = offset if offset < 8 else offset - 8
        sequence = f"amazon-r00-repair-repair-v2-7ceed-r1-{variant}-{ordinal}"
        row = {
            "sequence_id": sequence,
            "environment_nonce": f"amazon-r00-repair/repair-v2-7ceed-r1/{variant}/{ordinal}",
            "proxy_port": 34500 + offset,
        }
        reason = attestor.EXPECTED_EXCLUSIONS.get(sequence)
        if reason is None:
            inventory.append({**row, "task_variant": variant})
        else:
            excluded.append({**row, "variant": variant, "reason": reason})
    return {
        "status": "complete",
        "scientific_label": "same_task_laptop_r00_pooled_exact_target_repair_fanout",
        "run_id": "repair-v2-7ceed-r1",
        "executor_git_sha": attestor.REPLAY_EXECUTOR_GIT_SHA,
        "validator_git_sha": attestor.REPLAY_VALIDATOR_GIT_SHA,
        "max_concurrency": 16,
        "retry_count": 0,
        "topup_count": 0,
        "execution_count": 16,
        "valid_replay_count": 13,
        "excluded_execution_count": 3,
        "execution_variant_counts": {"graded": 8, "mixed": 8},
        "valid_variant_counts": {"graded": 7, "mixed": 6},
        "excluded_sequence_reasons": dict(attestor.EXPECTED_EXCLUSIONS),
        "critical_role_variant_counts": attestor.EXPECTED_ROLE_COUNTS,
        "critical_role_variant_unique_target_counts": attestor.EXPECTED_ROLE_COUNTS,
        "targets_per_variant_role_required": 2,
        "base_port": 34500,
        "ports": list(range(34500, 34516)),
        "inventory": inventory,
        "excluded_executions": excluded,
        "laptop_r00_used": True,
        "laptop_r01_used": False,
        "office_chair_used": False,
    }


def test_exact_lora_composite_is_deterministic_and_field_sensitive() -> None:
    candidate = {
        "parent_tree_sha256": "1" * 64,
        "adapter_tree_sha256": "2" * 64,
        "adapter_config_sha256": "3" * 64,
        "tokenizer_json_sha256": "4" * 64,
        "chat_template_sha256": "5" * 64,
        "dtype": "bfloat16",
    }
    first = attestor.exact_lora_composite(candidate)
    assert first == attestor.exact_lora_composite(candidate)
    candidate["adapter_tree_sha256"] = "6" * 64
    assert first != attestor.exact_lora_composite(candidate)


def test_descriptor_rejects_semantic_or_self_hash_tamper(tmp_path: Path) -> None:
    core = {"schema": attestor.FANOUT_SCHEMA, "status": "complete"}
    value = {**core, "receipt_body_sha256": attestor.digest(core)}
    path = tmp_path / "fanout.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert (
        attestor.descriptor(
            path, attestor.FANOUT_SCHEMA, "receipt_body_sha256", "fanout"
        )["status"]
        == "complete"
    )
    value["status"] = "failed"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(attestor.IntegrityError):
        attestor.descriptor(
            path, attestor.FANOUT_SCHEMA, "receipt_body_sha256", "fanout"
        )


def test_valid_repair_fanout_is_exact_and_rejects_excluded_target_tamper() -> None:
    value = _fanout()
    assert attestor.valid_repair_fanout(value)
    value["excluded_executions"][2]["reason"] = "no_exact_wire_critical_target"
    assert not attestor.valid_repair_fanout(value)


def test_create_only_refuses_nonidentical_receipt(tmp_path: Path) -> None:
    path = tmp_path / "endpoint.json"
    first = {"schema": attestor.ENDPOINT_SCHEMA, "status": "ok"}
    attestor.write_create_only(path, first)
    attestor.write_create_only(path, first)
    with pytest.raises(attestor.IntegrityError):
        attestor.write_create_only(path, {**first, "status": "changed"})
