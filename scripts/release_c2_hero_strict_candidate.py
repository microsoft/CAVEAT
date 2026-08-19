#!/usr/bin/env python3
"""Atomically release the dynamic HERO strict candidate after its receipt seals."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import release_c2_paired_candidate as base


NAMESPACE = "bonete61"
JOB = "t-yuxuanli-hpt-c2-hero-strict-candidate-w1"
POD = JOB + "-master-0"
POD_UID = "fc32233f-3123-429e-a610-b8f4b97828d0"
CAMPAIGN_ROOT = Path(
    "/data/runs/t-yuxuanli/t-yuxuanli-hpt-c2-hero-strict-training-w1-20260815"
)
RECEIPT = CAMPAIGN_ROOT / "training_r1/training/training_receipt.json"
PLAN = RECEIPT.with_name("plan.json")
SOURCE = CAMPAIGN_ROOT / "source_eval_hero_strict_serve_dynamic_v2"
ENTRYPOINT = SOURCE / "scripts/run_paired_buy_now_candidate_serve.sh"
ENTRYPOINT_SHA256 = "ce946baea04e7e53af71b757f8cb8769da80e7a32ed750784c24fdd4d50056ff"
SOURCE_GIT_SHA = "74b5cf5c903a7956bf913375c38a627ad42c6e7b"
SOURCE_IDENTITY = {
    "files": 92,
    "bytes": 2_183_797,
    "tree_sha256": "54bfad2c63bdc181aea6c21afc67b2d92ff6f200a3853ca2eb2691008c509bf6",
}
TRAINER = CAMPAIGN_ROOT / "source_distill_85e687ae"
TRAINER_GIT_SHA = "85e687aee2a18fce17ca1489efd9d1f71ed4c387"
TRAINER_TREE = "df6a6205a6d5df6e712d04f5517fb4f425ff946fa4119301ead0fd5ff4cbf690"
RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_hero_strict_candidate_serve_release_w1.json"
)
RECEIPT_SCHEMA = "harness-distill.step31-strict-checkout-paired-receipt.v1"
PLAN_SCHEMA = "harness-distill.step31-strict-checkout-paired-plan.v1"
PLAN_FILE_SHA256 = "7eef2b7d220813c1451b365a4f3f54e0a7f2cb38351d1619ce6a3a257c0b33c2"
PLAN_BODY_SHA256 = "0180931d0546a78847ba01d56f0a8a84fd4eebaf618a90f99aac696f7940e4f5"
SCIENTIFIC_LABEL = "step30_strict_checkout_paired_semantic_update"
OBJECTIVE = "paired_chosen_ce_plus_bounded_rejected_token_unlikelihood"
OBJECTIVE_COEFFICIENTS = {
    "chosen_pre_action_tail_ce": "76/175",
    "chosen_action_ce": "57/175",
    "rejected_semantic_unlikelihood": "6/25",
}
CATEGORY_COEFFICIENTS = {
    "hero_pdp_add": "3/10",
    "dirty_cart_delete_addon": "3/10",
    "clean_cart_proceed": "1/10",
    "checkout_place": "1/10",
    "hero_frontier_discovery": "2/25",
    "hero_checkpoint_repair": "1/20",
    "hero_approved_rebind": "3/100",
    "shortcut_retention": "1/25",
}
SOURCE_GROUP_MASS = {
    "add_preference": "3/10",
    "delete_preference": "3/10",
    "executed_self_correct_chosen_ce": "1/5",
    "sealed_dcr_shortcut_retention": "1/5",
}
CUSTOM_LOSS = {
    "import_path": "harness_distill.paired_buy_now_loss.rejected_token_unlikelihood_loss",
    "formula": "-log(1-probability_cap*p_theta(rejected_token|rejected_prefix))",
    "probability_cap": 0.95,
    "precision": "float32",
}
PAIR_COUNTS = {
    "states": 18,
    "chosen": 18,
    "rejected": 16,
    "chosen_only": 2,
    "per_update": 18,
    "updates": 1,
    "evaluation": 0,
    "heldout": 0,
    "variants": {"graded": 5, "graded3": 6, "graded4": 4, "mixed": 3},
    "categories": {
        "hero_frontier_discovery": 4,
        "hero_checkpoint_repair": 2,
        "hero_approved_rebind": 2,
        "shortcut_retention": 4,
        "hero_pdp_add": 2,
        "dirty_cart_delete_addon": 2,
        "clean_cart_proceed": 1,
        "checkout_place": 1,
    },
    "retained_hero_states": 8,
    "sealed_shortcut_retention_states": 4,
    "fresh_dynamic_states": 6,
    "fresh_paired_states": 4,
    "fresh_chosen_only_states": 2,
}
PAIR_SOURCE_KIND = (
    "sealed_hero_dcr8_plus_step29_shortcut_retention4_plus_"
    "dynamic_strict_paired_and_chosen_only6"
)
PAIR_POLICY_AUDIT = {
    "classification": "mixed_step30_on_policy_strict_plus_sealed_retention_replay",
    "step30_on_policy_strict_states": 6,
    "sealed_hero_dcr_replay_states": 8,
    "sealed_shortcut_replay_states": 4,
}
PARENT_RECEIPT_SHA256 = (
    "0eb83b3d78d7b3ae8c060e874806e498d825ad0bc3d53d64b2b49d0b68d2d975"
)
PARENT_RECEIPT_BODY_SHA256 = (
    "1049b78c2cc13d98f47da51af26addc31a760d9517ea79b052ca7ce8b96cf847"
)
PARENT_ADAPTER_TREE_SHA256 = (
    "070976f9e9765783568cc432e5ee40f2c0d51f874637a66fc9857efb5fdf3b7e"
)
PARENT_DCP = {
    "files": 5,
    "bytes": 57_532_880_920,
    "tree_sha256": "bb17e334d066e56c91085709040319c808555f602148ca2d6d272c8b20e96776",
}
CANDIDATE_NAME = "step31-strict-checkout-paired"
CANDIDATE_PATH = RECEIPT.parent / "prime_output/weights/step_31/lora_adapters"
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
        and plan.get("source_step") == 30
        and plan.get("update_steps") == [31]
        and plan.get("final_step") == 31
        and plan.get("optimizer_updates") == 1
        and plan.get("learning_rate") == 3e-6
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
        and descriptor(plan.get("frozen_adapter"))
        and descriptor(plan.get("frozen_hero50_pair_file"), rows=14)
        and descriptor(plan.get("frozen_retention_pair_file"), rows=4)
        and descriptor(plan.get("corpus"), rows=34)
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
        or receipt.get("source_step") != 30
        or receipt.get("update_steps") != [31]
        or receipt.get("final_step") != 31
        or receipt.get("optimizer_updates") != 1
        or receipt.get("learning_rate") != 3e-6
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
        or candidate.get("update") != 31
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
    alias = (
        "qwen35-browser-action-step31-strict-checkout-paired-"
        f"{candidate_tree[:12]}-exact-lora"
    )
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
        "schema": "c2-hero-strict-candidate-release-handoff.v1",
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
        "local_base_url": "http://127.0.0.1:18549/v1",
    }
    handoff_payload = json.dumps(handoff, sort_keys=True, indent=2).encode() + b"\n"
    base.atomic_local_write(arguments.handoff_output, handoff_payload)
    print(json.dumps(handoff, sort_keys=True))


if __name__ == "__main__":
    main()
