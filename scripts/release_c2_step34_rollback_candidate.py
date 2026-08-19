#!/usr/bin/env python3
"""Atomically release the receipt-bound Step34 rollback candidate."""

from pathlib import Path

import release_c2_step32_rebind_candidate as publisher

publisher.NAMESPACE = "bonete61"
publisher.JOB = "t-yuxuanli-hpt-c2-step34-rollback-candidate-w1"
publisher.POD = publisher.JOB + "-master-0"
publisher.POD_UID = "e0926e8a-d0d4-4fca-98e7-8737b98cec94"
publisher.CAMPAIGN_ROOT = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step34-rollback-training-w1-20260815"
)
publisher.RECEIPT = (
    publisher.CAMPAIGN_ROOT
    / "step34_training_run_r1/training/training_receipt.json"
)
publisher.PLAN = publisher.RECEIPT.with_name("plan.json")
publisher.SOURCE = publisher.CAMPAIGN_ROOT / "source_eval_step34_rollback_serve_v1"
publisher.ENTRYPOINT = publisher.SOURCE / "scripts/run_paired_buy_now_candidate_serve.sh"
publisher.ENTRYPOINT_SHA256 = (
    "500fb95c3219bc8a702ebce8b9861b1ee3d09a8ceea8cdde0908e5ad8eae5fe9"
)
publisher.SOURCE_GIT_SHA = "2b7bf073ba778bb76e5e3e2f21b5d5c5667e3b35"
publisher.SOURCE_IDENTITY = {
    "files": 92,
    "bytes": 2_184_764,
    "tree_sha256": "ec9869e58407b890ce1e85ca613a1072da50145a3418f842be87cd464b69b066",
}
publisher.TRAINER = (
    publisher.CAMPAIGN_ROOT
    / "source/bb3cc4624c2a09c350c777ef82d54b85ba6b2e94"
)
publisher.TRAINER_GIT_SHA = "bb3cc4624c2a09c350c777ef82d54b85ba6b2e94"
publisher.TRAINER_TREE = (
    "f6c8b9464f5b94e7488a20efa2113592806aef9c3cbfa618c703e0d06a71f829"
)
publisher.RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_step34_rollback_candidate_serve_release_w1.json"
)
publisher.RECEIPT_SCHEMA = "harness-distill.step34-rollback-action-mixed-receipt.v1"
publisher.PLAN_SCHEMA = "harness-distill.step34-rollback-action-mixed-plan.v1"
publisher.PLAN_FILE_SHA256 = (
    "ad4c99bd624e261f4e66660d89285fd3b0d8432b248655c2bf4f79ec48f69c33"
)
publisher.PLAN_BODY_SHA256 = (
    "fdf9d0f6ed2f1de853c38ec903395e316168f2ec05383a0d706eaf82a5f35767"
)
publisher.SCIENTIFIC_LABEL = (
    "step32_rollback_stable_navigation_action_plus_full_downstream_retention"
)
publisher.OBJECTIVE = "paired_chosen_ce_plus_bounded_rejected_token_unlikelihood"
publisher.OBJECTIVE_COEFFICIENTS = {
    "chosen_action_ce": "778/1225",
    "chosen_pre_action_tail_ce": "384/1225",
    "rejected_semantic_unlikelihood": "9/175",
}
publisher.CATEGORY_COEFFICIENTS = {
    "sealed_step32_full_downstream_chain__chosen_only": "3/7",
    "sealed_step32_full_downstream_chain__paired": "6/35",
    "stable_origin_invariant_hero_navigation__chosen_only": "2/5",
}
publisher.SOURCE_GROUP_MASS = {
    "sealed_step32_full_downstream_chain": "3/5",
    "stable_origin_invariant_hero_navigation": "2/5",
}
publisher.PAIR_COUNTS = {
    "states": 36,
    "chosen": 36,
    "rejected": 8,
    "chosen_only": 28,
    "per_update": 36,
    "updates": 1,
    "evaluation": 0,
    "heldout": 0,
    "variants": {"graded": 10, "graded3": 11, "graded4": 8, "mixed": 7},
    "categories": {
        "sealed_step32_full_downstream_chain__chosen_only": 20,
        "sealed_step32_full_downstream_chain__paired": 8,
        "stable_origin_invariant_hero_navigation__chosen_only": 8,
    },
    "fresh_upstream_states": 8,
    "fresh_upstream_paired_states": 0,
    "fresh_upstream_chosen_only_states": 8,
    "sealed_downstream_retention_states": 28,
    "sealed_downstream_source_counts": {
        "checkout_place": 1,
        "clean_cart_proceed": 1,
        "dirty_cart_delete_addon": 8,
        "dirty_cart_delete_wrong_laptop": 7,
        "hero_pdp_add": 7,
        "post_hero_add_view_cart": 4,
    },
    "logical_groups": {
        "sealed_step32_full_downstream_chain": 28,
        "stable_origin_invariant_hero_navigation": 8,
    },
}
publisher.PAIR_SOURCE_KIND = (
    "step33_train_only_stable_navigation_action_corrections_plus_"
    "sealed_step32_full_downstream_chain"
)
publisher.PAIR_POLICY_AUDIT = {
    "classification": (
        "step33_candidate_train_corrections_anchored_on_exact_successful_step32_"
        "plus_sealed_step32_train_retention"
    ),
    "fresh_train_states": 8,
    "sealed_train_retention_states": 28,
    "fresh_reasoning_tail_ce": False,
    "fresh_origin_or_port_ce": False,
    "generic_click_or_index_rejected_unlikelihood": False,
    "evaluation_states": 0,
    "heldout_states": 0,
}
publisher.EXTRA_PLAN_FIELDS = {
    "scientific_iteration": 34,
    "physical_optimizer_output_step": 33,
    "semantic_mask": {
        "fresh_chosen": "navigate_method_plus_relative_path_query_only",
        "fresh_excluded": [
            "reasoning_tail",
            "origin",
            "host",
            "port",
            "json_syntax",
        ],
        "fresh_rejected": "stable_wrong_relative_target_only",
        "generic_click_or_index_rejected_unlikelihood": False,
        "sealed_downstream": "exact_step32_semantic_objective",
    },
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
publisher.CANDIDATE_NAME = "step34-rollback-step32-stable-action-mixed"
publisher.CANDIDATE_PATH = (
    publisher.RECEIPT.parent / "prime_output/weights/step_33/lora_adapters"
)
publisher.SOURCE_STEP = 32
publisher.UPDATE_STEPS = [33]
publisher.FINAL_STEP = 33
publisher.LEARNING_RATE = 5e-7
publisher.FRESH_PAIR_ROWS = 8
publisher.RETENTION_PAIR_ROWS = 28
publisher.CORPUS_ROWS = 44
publisher.ALIAS_MARKER = "step34-rollback-step32-stable-action-mixed"
publisher.HANDOFF_SCHEMA = "c2-step34-rollback-candidate-release-handoff.v1"
publisher.LOCAL_PORT = 18552


if __name__ == "__main__":
    publisher.main()
