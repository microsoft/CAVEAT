#!/usr/bin/env python3
"""Atomically release step32 rebind candidate after its receipt seals."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import release_c2_paired_candidate as base

NAMESPACE = "bonete61"
JOB = "t-yuxuanli-hpt-c2-step32-rebind-candidate-w2"
POD = JOB + "-master-0"
POD_UID = "adf37d82-0c57-4138-b13e-001e7f971bc3"
CAMPAIGN_ROOT = Path(
    "/data/runs/t-yuxuanli/t-yuxuanli-hpt-c2-step32-rebind-training-w1-20260815"
)
RECEIPT = CAMPAIGN_ROOT / "step32_training_run_r2/training/training_receipt.json"
PLAN = RECEIPT.with_name("plan.json")
SOURCE = CAMPAIGN_ROOT / "source_eval_step32_rebind_serve_v3"
ENTRYPOINT = SOURCE / "scripts/run_paired_buy_now_candidate_serve.sh"
ENTRYPOINT_SHA256 = "e2c22c20f8c9a8c883f46f0770c777b89d67d64feccfce5f20f9fe74f38a1045"
SOURCE_GIT_SHA = "fe4a2a3d87bbedc98d607de94d05600cb40199e4"
SOURCE_IDENTITY = {
    "files": 92,
    "bytes": 2_184_300,
    "tree_sha256": "72451df78333f41eff01a8579bbdaa4ae7033f46f637286f780feb9e2f13589e",
}
TRAINER = CAMPAIGN_ROOT / "source_distill_5b1567f4"
TRAINER_GIT_SHA = "5b1567f4338fb6c60d5c76b32b11f933f3bd882b"
TRAINER_TREE = "fb4ac62ee2faa75228f5bb48ddfee5c3ae41c73d80d9ddecfd214603d5b61648"
RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_step32_rebind_candidate_serve_release_w2.json"
)
RECEIPT_SCHEMA = "harness-distill.step32-rebind-paired-receipt.v1"
PLAN_SCHEMA = "harness-distill.step32-rebind-paired-plan.v1"
PLAN_FILE_SHA256 = "f3cdca9edefd6e81e84c358dcd4036f3af63456f7adf100c965db3e634c38c25"
PLAN_BODY_SHA256 = "f1c2746bf0e7bf006c167a4a277d3577334cca20b1d6025181139727c50b61f5"
SCIENTIFIC_LABEL = "step31_rebind_bridge_cleanup_paired_semantic_update"
OBJECTIVE = "paired_chosen_ce_plus_bounded_rejected_token_unlikelihood"
OBJECTIVE_COEFFICIENTS = {
    "chosen_pre_action_tail_ce": "9071/19250",
    "chosen_action_ce": "27213/77000",
    "rejected_semantic_unlikelihood": "1929/11000",
}
CATEGORY_COEFFICIENTS = {
    "multiline_dirty_cart_cleanup__chosen_only": "3/25",
    "multiline_dirty_cart_cleanup__paired": "2/25",
    "post_hero_add_view_cart_bridge__chosen_only": "27/110",
    "post_hero_add_view_cart_bridge__paired": "3/55",
    "proceed_place_retention__chosen_only": "1/20",
    "upstream_visible_hero_discovery_rebind__paired": "9/20",
}
SOURCE_GROUP_MASS = {
    "multiline_dirty_cart_cleanup": "1/5",
    "post_hero_add_view_cart_bridge": "3/10",
    "proceed_place_retention": "1/20",
    "upstream_visible_hero_discovery_rebind": "9/20",
}
CUSTOM_LOSS = {
    "import_path": "harness_distill.paired_buy_now_loss.rejected_token_unlikelihood_loss",
    "formula": "-log(1-probability_cap*p_theta(rejected_token|rejected_prefix))",
    "probability_cap": 0.95,
    "precision": "float32",
}
PAIR_COUNTS = {
    "states": 45,
    "chosen": 45,
    "rejected": 25,
    "chosen_only": 20,
    "per_update": 45,
    "updates": 1,
    "evaluation": 0,
    "heldout": 0,
    "variants": {"graded": 12, "graded3": 13, "graded4": 10, "mixed": 10},
    "categories": {
        "multiline_dirty_cart_cleanup__chosen_only": 9,
        "multiline_dirty_cart_cleanup__paired": 6,
        "post_hero_add_view_cart_bridge__chosen_only": 9,
        "post_hero_add_view_cart_bridge__paired": 2,
        "proceed_place_retention__chosen_only": 2,
        "upstream_visible_hero_discovery_rebind__paired": 17,
    },
    "sealed_target_retention_states": 12,
    "fresh_dynamic_states": 33,
    "fresh_paired_states": 13,
    "fresh_chosen_only_states": 20,
    "logical_groups": {
        "multiline_dirty_cart_cleanup": 15,
        "post_hero_add_view_cart_bridge": 11,
        "proceed_place_retention": 2,
        "upstream_visible_hero_discovery_rebind": 17,
    },
}
PAIR_SOURCE_KIND = (
    "sealed_step31_target_retention_plus_dynamic_train_only_"
    "rebind_bridge_multiline_cleanup"
)
PAIR_POLICY_AUDIT = {
    "classification": "fresh_step31_on_policy_train_plus_sealed_step31_retention",
    "fresh_train_states": 33,
    "sealed_target_retention_states": 12,
    "evaluation_states": 0,
    "heldout_states": 0,
}
PARENT_RECEIPT_SHA256 = (
    "c21ce3c6f38a4d6c0e4b87a9c43b954f1c223d422b359e6ccff8d8139a0b11b9"
)
PARENT_RECEIPT_BODY_SHA256 = (
    "ef43d2c854f4b828c795ec30ec269dfc6a1749c59ef2662eef6988ae0b902ff0"
)
PARENT_ADAPTER_TREE_SHA256 = (
    "8e16ff76193bfb0e440ad8a49aab4987d5276775875e9cab9391c008d5a5dc70"
)
PARENT_DCP = {
    "files": 5,
    "bytes": 57_532_880_927,
    "tree_sha256": "2b98190d3b589be89db0d7da914226f70513d38bff4a5ceb55ce5cd9c6dea017",
}
CANDIDATE_NAME = "step32-rebind-bridge-cleanup-paired"
CANDIDATE_PATH = RECEIPT.parent / "prime_output/weights/step_32/lora_adapters"
SOURCE_STEP = 31
UPDATE_STEPS = [32]
FINAL_STEP = 32
LEARNING_RATE = 1e-6
FRESH_PAIR_ROWS = 33
RETENTION_PAIR_ROWS = 12
CORPUS_ROWS = 70
ALIAS_MARKER = "step32-rebind-bridge-cleanup-paired"
HANDOFF_SCHEMA = "c2-step32-rebind-candidate-release-handoff.v1"
LOCAL_PORT = 18550
EXTRA_PLAN_FIELDS: dict[str, Any] = {}
PROCESS_POLICY = {
    "trainer_only": True,
    "orchestrator": False,
    "inference": False,
    "environment": False,
    "new_rollouts": False,
}
PRIME_COMMIT = "d334ea52940b47f426293a7d146239e3fbf91caa"


def descriptor(value: Any, *, rows: int | None = None) -> bool:
    if not isinstance(value, dict):
        return False
    if not isinstance(value.get("path"), str) or not Path(value["path"]).is_absolute():
        return False
    if not isinstance(value.get("bytes"), int) or value["bytes"] <= 0:
        return False
    try:
        base.sha(value.get("sha256"), "descriptor SHA-256")
    except ValueError:
        return False
    return rows is None or value.get("rows") == rows


def plan_matches(plan: dict[str, Any]) -> bool:
    source_dcp = plan.get("source_dcp") or {}
    parent_candidate = plan.get("parent_candidate") or {}
    return (
        plan.get("schema") == PLAN_SCHEMA
        and plan.get("status") == "prepared"
        and plan.get("scientific_label") == SCIENTIFIC_LABEL
        and plan.get("artifact_git_sha") == TRAINER_GIT_SHA
        and plan.get("source_step") == SOURCE_STEP
        and plan.get("update_steps") == UPDATE_STEPS
        and plan.get("final_step") == FINAL_STEP
        and plan.get("optimizer_updates") == 1
        and plan.get("learning_rate") == LEARNING_RATE
        and plan.get("objective") == OBJECTIVE
        and plan.get("objective_coefficients") == OBJECTIVE_COEFFICIENTS
        and plan.get("category_coefficients") == CATEGORY_COEFFICIENTS
        and plan.get("source_group_mass") == SOURCE_GROUP_MASS
        and plan.get("custom_loss") == CUSTOM_LOSS
        and plan.get("parent_receipt_sha256") == PARENT_RECEIPT_SHA256
        and plan.get("parent_receipt_body_sha256") == PARENT_RECEIPT_BODY_SHA256
        and parent_candidate.get("tree_sha256") == PARENT_ADAPTER_TREE_SHA256
        and all(source_dcp.get(key) == value for key, value in PARENT_DCP.items())
        and plan.get("pair_source_kind") == PAIR_SOURCE_KIND
        and plan.get("pair_policy_audit") == PAIR_POLICY_AUDIT
        and all(plan.get(key) == value for key, value in EXTRA_PLAN_FIELDS.items())
        and descriptor(plan.get("frozen_adapter"))
        and descriptor(plan.get("frozen_hero50_pair_file"), rows=FRESH_PAIR_ROWS)
        and descriptor(plan.get("frozen_retention_pair_file"), rows=RETENTION_PAIR_ROWS)
        and descriptor(plan.get("corpus"), rows=CORPUS_ROWS)
        and plan.get("pair_counts") == PAIR_COUNTS
        and plan.get("on_policy") is False
        and plan.get("policy_gradient") is False
        and plan.get("reference_logprobs") is False
        and plan.get("fresh_optimizer") is True
        and plan.get("fresh_scheduler") is True
        and plan.get("fresh_dataloader") is True
        and plan.get("process_policy") == PROCESS_POLICY
        and plan.get("launch_authorized") is True
        and Path(str(plan.get("candidate_path", ""))).resolve() == CANDIDATE_PATH
        and plan.get("candidate_name") == CANDIDATE_NAME
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", required=True)
    parser.add_argument("--handoff-output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.handoff_output.exists():
        parser.error(f"handoff already exists: {arguments.handoff_output}")
    try:
        base.sha(PLAN_FILE_SHA256, "sealed plan file")
        base.sha(PLAN_BODY_SHA256, "sealed plan body")
    except ValueError as error:
        parser.error(f"publisher is not armed with sealed plan identity: {error}")

    base.NAMESPACE = NAMESPACE
    base.JOB = JOB
    base.POD = POD
    base.POD_UID = POD_UID
    receipt_bytes = base.remote_bytes(RECEIPT)
    receipt = json.loads(receipt_bytes)
    candidate = receipt.get("candidate") or {}
    receipt_file = hashlib.sha256(receipt_bytes).hexdigest()
    receipt_body = base.validate_canonical_body(
        receipt, "receipt_body_sha256", "HERO strict training receipt"
    )
    plan_bytes = base.remote_bytes(PLAN)
    plan = json.loads(plan_bytes)
    plan_body = base.validate_canonical_body(
        plan, "plan_body_sha256", "HERO strict training plan"
    )
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("status") != "ok"
        or receipt.get("scientific_label") != SCIENTIFIC_LABEL
        or receipt.get("artifact_source_git_sha") != TRAINER_GIT_SHA
        or receipt.get("execution_source_git_sha") != TRAINER_GIT_SHA
        or receipt.get("prime_commit") != PRIME_COMMIT
        or receipt.get("source_step") != SOURCE_STEP
        or receipt.get("update_steps") != UPDATE_STEPS
        or receipt.get("final_step") != FINAL_STEP
        or receipt.get("optimizer_updates") != 1
        or receipt.get("learning_rate") != LEARNING_RATE
        or receipt.get("objective") != OBJECTIVE
        or receipt.get("objective_coefficients") != OBJECTIVE_COEFFICIENTS
        or receipt.get("category_coefficients") != CATEGORY_COEFFICIENTS
        or receipt.get("source_group_mass") != SOURCE_GROUP_MASS
        or receipt.get("custom_loss") != CUSTOM_LOSS
        or receipt.get("source_dcp") != plan.get("source_dcp")
        or receipt.get("weight_audit") != plan.get("weight_audit")
        or receipt.get("training_batches") != plan.get("training_batches")
        or Path(str(receipt.get("plan_path", ""))).resolve() != PLAN
        or hashlib.sha256(plan_bytes).hexdigest() != PLAN_FILE_SHA256
        or receipt.get("plan_sha256") != PLAN_FILE_SHA256
        or plan_body != PLAN_BODY_SHA256
        or receipt.get("plan_body_sha256") != PLAN_BODY_SHA256
        or not plan_matches(plan)
        or candidate.get("name") != CANDIDATE_NAME
        or candidate.get("update") != FINAL_STEP
        or Path(str(candidate.get("path", ""))).resolve() != CANDIDATE_PATH
        or not isinstance(candidate.get("files"), int)
        or candidate.get("files", 0) <= 0
        or not isinstance(candidate.get("bytes"), int)
        or candidate.get("bytes", 0) <= 0
    ):
        parser.error("HERO strict receipt is not the expected successful artifact")

    candidate_tree = base.sha(candidate.get("tree_sha256"), "candidate tree")
    adapter_config = base.sha(candidate.get("adapter_config_sha256"), "adapter config")
    stable_marker = base.sha(candidate.get("stable_marker_sha256"), "stable marker")
    alias = f"qwen35-browser-action-{ALIAS_MARKER}-{candidate_tree[:12]}-exact-lora"
    composite = base.digest(
        {
            "schema": "harness-posttrain.exact-lora-composite.v1",
            "execution": "peft_unmerged_exact_lora",
            "parent_tree_sha256": base.PARENT_TREE,
            "adapter_tree_sha256": candidate_tree,
            "adapter_config_sha256": adapter_config,
            "tokenizer_json_sha256": base.TOKENIZER,
            "chat_template_sha256": base.CHAT_TEMPLATE,
            "dtype": "bfloat16",
        }
    )
    core = {
        "schema": "harness-posttrain.browser-action-next-iteration.c2-candidate-serve-release.v1",
        "status": "released",
        "purpose": "c2_candidate_serve",
        "laptop_r01_outcomes_read": False,
        "office_chair_outcomes_read": False,
        "reservation": {"job_name": JOB, "pod_name": POD, "pod_uid": POD_UID},
        "source": {"root": str(SOURCE), "git_sha": SOURCE_GIT_SHA, **SOURCE_IDENTITY},
        "entrypoint": {"path": str(ENTRYPOINT), "sha256": ENTRYPOINT_SHA256},
        "argv": [
            ENTRYPOINT_SHA256,
            str(RECEIPT),
            receipt_file,
            receipt_body,
            candidate_tree,
            adapter_config,
            stable_marker,
            str(TRAINER),
            TRAINER_GIT_SHA,
            TRAINER_TREE,
            JOB,
            POD,
            POD_UID,
        ],
    }
    release = {**core, "release_sha256": base.digest(core)}
    payload = json.dumps(release, sort_keys=True, indent=2).encode() + b"\n"
    base.atomic_remote_write(RELEASE, payload)
    handoff = {
        "schema": HANDOFF_SCHEMA,
        "status": "published_after_successful_receipt",
        "receipt_path": str(RECEIPT),
        "receipt_file_sha256": receipt_file,
        "receipt_body_sha256": receipt_body,
        "candidate_tree_sha256": candidate_tree,
        "candidate_composite_sha256": composite,
        "alias": alias,
        "release_path": str(RELEASE),
        "release_body_sha256": release["release_sha256"],
        "serve_job": JOB,
        "serve_pod": POD,
        "serve_pod_uid": POD_UID,
        "local_base_url": f"http://127.0.0.1:{LOCAL_PORT}/v1",
    }
    handoff_payload = json.dumps(handoff, sort_keys=True, indent=2).encode() + b"\n"
    base.atomic_local_write(arguments.handoff_output, handoff_payload)
    print(json.dumps(handoff, sort_keys=True))


if __name__ == "__main__":
    main()
