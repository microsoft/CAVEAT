#!/usr/bin/env python3
"""Atomically release the HERO50 candidate after its exact receipt is sealed."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import release_c2_paired_candidate as base


NAMESPACE = "bonete61"
JOB = "t-yuxuanli-hpt-c2-hero50-candidate-w1"
POD = JOB + "-master-0"
POD_UID = "9b6ebef6-40bd-4638-9f63-00a2954ca7a6"
CAMPAIGN_ROOT = Path(
    "/data/runs/t-yuxuanli/t-yuxuanli-hpt-c2-hero50-paired-w1-20260815"
)
RECEIPT = CAMPAIGN_ROOT / "training_r1/training/training_receipt.json"
PLAN = RECEIPT.with_name("plan.json")
SOURCE = CAMPAIGN_ROOT / "source_eval_hero50_serve_v3"
ENTRYPOINT = SOURCE / "scripts/run_paired_buy_now_candidate_serve.sh"
ENTRYPOINT_SHA256 = "7db3f5a28e8f0f455899ba16c5e6879f512bb1cbbb99f39d0a40b240e28afe10"
SOURCE_GIT_SHA = "234616d0d02fee3f1ea22a271ec00af1db34e53c"
SOURCE_IDENTITY = {
    "files": 92,
    "bytes": 2_182_078,
    "tree_sha256": "b4bdca18bcd4474d6ee285c429a7d8a477722af6a238d70096446ed0cc082975",
}
TRAINER = CAMPAIGN_ROOT / "source_distill_1f0a5576"
TRAINER_GIT_SHA = "1f0a557677601ef8ba1ef652e882a499f80aad76"
TRAINER_TREE = "d5f80c4489efa251299c955504b8629bbf8247c15255c9c3a96d1a5e57ac5fff"
RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_hero50_candidate_serve_release_w1.json"
)
RECEIPT_SCHEMA = "harness-distill.hero50-paired-receipt.v1"
PLAN_SCHEMA = "harness-distill.hero50-paired-plan.v1"
PLAN_FILE_SHA256 = "f804a7e8cdbfc41bbfc410d363ec274f0e17ffa7470f72351d41babf801c8e1c"
PLAN_BODY_SHA256 = "e3677e05a3bc7bdea3d82a0bb973c10b398e4d99e2a647703edaa0a4b9e57758"
SCIENTIFIC_LABEL = "step29_hero50_paired_semantic_update"
OBJECTIVE = "paired_chosen_ce_plus_bounded_rejected_token_unlikelihood"
OBJECTIVE_COEFFICIENTS = {
    "chosen_pre_action_tail_ce": "40/100",
    "chosen_action_ce": "30/100",
    "rejected_semantic_unlikelihood": "30/100",
}
CATEGORY_COEFFICIENTS = {
    "hero_frontier_discovery": "2/5",
    "hero_checkpoint_repair": "1/4",
    "hero_approved_rebind": "3/20",
    "shortcut_retention": "1/5",
}
CUSTOM_LOSS = {
    "import_path": "harness_distill.paired_buy_now_loss.rejected_token_unlikelihood_loss",
    "formula": "-log(1-probability_cap*p_theta(rejected_token|rejected_prefix))",
    "probability_cap": 0.95,
    "precision": "float32",
}
PAIR_COUNTS = {
    "states": 12,
    "chosen": 12,
    "rejected": 12,
    "per_update": 12,
    "updates": 1,
    "evaluation": 0,
    "heldout": 0,
    "variants": {"graded": 3, "graded3": 3, "graded4": 3, "mixed": 3},
    "categories": {
        "hero_frontier_discovery": 4,
        "hero_checkpoint_repair": 2,
        "hero_approved_rebind": 2,
        "shortcut_retention": 4,
    },
}
PARENT_RECEIPT_SHA256 = (
    "1ddc197bf6780fab605857c72dbb2f199212c60f723e6aa8bbb9616af11426f2"
)
PARENT_RECEIPT_BODY_SHA256 = (
    "f80e2a72dea8ad7ec90632186bdcf2bde30304b6016453f2ad7febdebf28a413"
)
PARENT_ADAPTER_TREE_SHA256 = (
    "9a73894a3055c71a421238e5f88249792e3dbc030482bf554e7404d374e54858"
)
PARENT_DCP = {
    "files": 5,
    "bytes": 57_532_880_918,
    "tree_sha256": "c6e61903097d1f919cc59770ee7e9c0b5d4173745690a585b0861704e5c9a5cb",
}
CANDIDATE_NAME = "step30-hero50-paired"
CANDIDATE_PATH = RECEIPT.parent / "prime_output/weights/step_30/lora_adapters"
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
        and plan.get("source_step") == 29
        and plan.get("update_steps") == [30]
        and plan.get("final_step") == 30
        and plan.get("optimizer_updates") == 1
        and plan.get("learning_rate") == 5e-6
        and plan.get("objective") == OBJECTIVE
        and plan.get("objective_coefficients") == OBJECTIVE_COEFFICIENTS
        and plan.get("category_coefficients") == CATEGORY_COEFFICIENTS
        and plan.get("custom_loss") == CUSTOM_LOSS
        and plan.get("parent_receipt_sha256") == PARENT_RECEIPT_SHA256
        and plan.get("parent_receipt_body_sha256") == PARENT_RECEIPT_BODY_SHA256
        and parent_candidate.get("tree_sha256") == PARENT_ADAPTER_TREE_SHA256
        and all(source_dcp.get(key) == value for key, value in PARENT_DCP.items())
        and plan.get("pair_source_kind")
        == "fresh_hero50_exact8_plus_sealed_shortcut_retention4"
        and descriptor(plan.get("frozen_adapter"))
        and descriptor(plan.get("frozen_hero50_pair_file"), rows=8)
        and descriptor(plan.get("frozen_retention_pair_file"), rows=4)
        and descriptor(plan.get("corpus"), rows=24)
        and plan.get("pair_counts") == PAIR_COUNTS
        and plan.get("on_policy") is True
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
        receipt, "receipt_body_sha256", "HERO50 training receipt"
    )
    plan_bytes = base.remote_bytes(PLAN)
    plan = json.loads(plan_bytes)
    plan_body = base.validate_canonical_body(
        plan, "plan_body_sha256", "HERO50 training plan"
    )
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("status") != "ok"
        or receipt.get("scientific_label") != SCIENTIFIC_LABEL
        or receipt.get("artifact_source_git_sha") != TRAINER_GIT_SHA
        or receipt.get("execution_source_git_sha") != TRAINER_GIT_SHA
        or receipt.get("prime_commit") != PRIME_COMMIT
        or receipt.get("source_step") != 29
        or receipt.get("update_steps") != [30]
        or receipt.get("final_step") != 30
        or receipt.get("optimizer_updates") != 1
        or receipt.get("learning_rate") != 5e-6
        or receipt.get("objective") != OBJECTIVE
        or receipt.get("objective_coefficients") != OBJECTIVE_COEFFICIENTS
        or receipt.get("category_coefficients") != CATEGORY_COEFFICIENTS
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
        or candidate.get("update") != 30
        or Path(str(candidate.get("path", ""))).resolve() != CANDIDATE_PATH
        or not isinstance(candidate.get("files"), int)
        or candidate.get("files", 0) <= 0
        or not isinstance(candidate.get("bytes"), int)
        or candidate.get("bytes", 0) <= 0
    ):
        parser.error("HERO50 receipt is not the expected successful artifact")

    candidate_tree = base.sha(candidate.get("tree_sha256"), "candidate tree")
    adapter_config = base.sha(candidate.get("adapter_config_sha256"), "adapter config")
    stable_marker = base.sha(candidate.get("stable_marker_sha256"), "stable marker")
    alias = f"qwen35-browser-action-step30-hero50-paired-{candidate_tree[:12]}-exact-lora"
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
        "schema": "c2-hero50-candidate-release-handoff.v1",
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
        "local_base_url": "http://127.0.0.1:18548/v1",
    }
    handoff_payload = json.dumps(handoff, sort_keys=True, indent=2).encode() + b"\n"
    base.atomic_local_write(arguments.handoff_output, handoff_payload)
    print(json.dumps(handoff, sort_keys=True))


if __name__ == "__main__":
    main()
