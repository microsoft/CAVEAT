#!/usr/bin/env python3
"""Atomically release the receipt-bound step33 upstream candidate."""

from pathlib import Path

import release_c2_step32_rebind_candidate as publisher

publisher.NAMESPACE = "bonete61"
publisher.JOB = "t-yuxuanli-hpt-c2-step33-upstream-candidate-w2"
publisher.POD = publisher.JOB + "-master-0"
publisher.POD_UID = "85429516-c86a-426c-b9bf-8fda61a3d5ac"
publisher.CAMPAIGN_ROOT = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step33-upstream-training-w1-20260815"
)
publisher.RECEIPT = (
    publisher.CAMPAIGN_ROOT
    / "step33_training_run_r3/training/training/training_receipt.json"
)
publisher.PLAN = publisher.RECEIPT.with_name("plan.json")
publisher.SOURCE = publisher.CAMPAIGN_ROOT / "source_eval_step33_upstream_serve_v1"
publisher.ENTRYPOINT = publisher.SOURCE / "scripts/run_paired_buy_now_candidate_serve.sh"
publisher.ENTRYPOINT_SHA256 = (
    "d66b663e8ab6ccf094da664de6d3885670a98011a46abaa1fe2c3fe6de0c7834"
)
publisher.SOURCE_GIT_SHA = "4bbfa383af66a3a36cf97640bb8af5db0c903120"
publisher.SOURCE_IDENTITY = {
    "files": 92,
    "bytes": 2_183_456,
    "tree_sha256": "ae0eaf25531a37d490e98c9cd3c65127fd8aecaadecdecb91a4d0c9ee4430455",
}
publisher.TRAINER = publisher.CAMPAIGN_ROOT / "source_distill_24b99ca1"
publisher.TRAINER_GIT_SHA = "24b99ca103e10741d6b061a964a6da258bbdbdd2"
publisher.TRAINER_TREE = (
    "604d6862bfde41b8bc09f54d839f807240d9e62ae30fb432405d3e6cb264bf39"
)
publisher.RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_step33_upstream_candidate_serve_release_w2.json"
)
publisher.RECEIPT_SCHEMA = "harness-distill.step33-upstream-paired-receipt.v1"
publisher.PLAN_SCHEMA = "harness-distill.step33-upstream-paired-plan.v1"
publisher.PLAN_FILE_SHA256 = (
    "955ad99f4d80aabe6bcb1e1ac25821e8f13a25ed0df48fd74e6cc07744657eee"
)
publisher.PLAN_BODY_SHA256 = (
    "ac217c19a836df742497febf5e31b93bdbfc3546e72e30b805d59347b2488d3f"
)
publisher.SCIENTIFIC_LABEL = (
    "step32_upstream_hero_discovery_recovery_paired_semantic_update"
)
publisher.OBJECTIVE = "paired_chosen_ce_plus_bounded_rejected_token_unlikelihood"
publisher.OBJECTIVE_COEFFICIENTS = {
    "chosen_action_ce": "219/700",
    "chosen_pre_action_tail_ce": "73/175",
    "rejected_semantic_unlikelihood": "27/100",
}
publisher.CATEGORY_COEFFICIENTS = {
    "hero_downstream_chain_retention__chosen_only": "1/10",
    "hero_downstream_chain_retention__paired": "1/10",
    "hero_upstream_discovery_rebind_checkpoint__paired": "4/5",
}
publisher.SOURCE_GROUP_MASS = {
    "hero_downstream_chain_retention": "1/5",
    "hero_upstream_discovery_rebind_checkpoint": "4/5",
}
publisher.PAIR_COUNTS = {
    "states": 22,
    "chosen": 22,
    "rejected": 19,
    "chosen_only": 3,
    "per_update": 22,
    "updates": 1,
    "evaluation": 0,
    "heldout": 0,
    "variants": {"graded": 6, "graded3": 7, "graded4": 5, "mixed": 4},
    "categories": {
        "hero_downstream_chain_retention__chosen_only": 3,
        "hero_downstream_chain_retention__paired": 3,
        "hero_upstream_discovery_rebind_checkpoint__paired": 16,
    },
    "fresh_upstream_states": 8,
    "sealed_upstream_retention_states": 8,
    "sealed_downstream_retention_states": 6,
    "logical_groups": {
        "hero_downstream_chain_retention": 6,
        "hero_upstream_discovery_rebind_checkpoint": 16,
    },
}
publisher.PAIR_SOURCE_KIND = (
    "fresh_step32_hero_discovery_plus_sealed_upstream_and_chain_retention"
)
publisher.PAIR_POLICY_AUDIT = {
    "classification": "fresh_step32_on_policy_train_plus_sealed_train_retention",
    "fresh_train_states": 8,
    "sealed_train_retention_states": 14,
    "evaluation_states": 0,
    "heldout_states": 0,
}
publisher.PARENT_RECEIPT_SHA256 = (
    "40e8f39c9550c03c08d58edccab649ed39b5e3e5ccb1a458d5888e4138889671"
)
publisher.PARENT_RECEIPT_BODY_SHA256 = (
    "8cf33d0667ac3205140efd5de8d881837ec64c2a3e50493a70b0b179b13f0319"
)
publisher.PARENT_ADAPTER_TREE_SHA256 = (
    "a2c2fe90c23bece6ba793d07d461304676d4bff8826e5d1464afd6bf8192dece"
)
publisher.PARENT_DCP = {
    "files": 5,
    "bytes": 57_532_880_940,
    "tree_sha256": "f0a8d3a2bd779d0920e8c9665f3bf58f0ab807ec25622f9bee41545153432ea1",
}
publisher.CANDIDATE_NAME = "step33-upstream-hero-recovery-paired"
publisher.CANDIDATE_PATH = (
    publisher.RECEIPT.parent / "prime_output/weights/step_33/lora_adapters"
)
publisher.SOURCE_STEP = 32
publisher.UPDATE_STEPS = [33]
publisher.FINAL_STEP = 33
publisher.LEARNING_RATE = 2e-6
publisher.FRESH_PAIR_ROWS = 8
publisher.RETENTION_PAIR_ROWS = 14
publisher.CORPUS_ROWS = 41
publisher.ALIAS_MARKER = "step33-upstream-hero-recovery-paired"
publisher.HANDOFF_SCHEMA = "c2-step33-upstream-candidate-release-handoff.v1"
publisher.LOCAL_PORT = 18551


if __name__ == "__main__":
    publisher.main()
