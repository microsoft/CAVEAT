#!/usr/bin/env python3
"""Publish the exact sealed S32 adapter into its UID-bound prompt-ablation holder."""

from pathlib import Path

import release_c2_step32_rebind_candidate as publisher

publisher.JOB = "t-yuxuanli-hpt-c2-s32-prompt-ablate-serve-w1"
publisher.POD = publisher.JOB + "-master-0"
publisher.POD_UID = "033a5efd-cb23-4e84-94b7-3e41685b372c"
publisher.SOURCE = (
    publisher.CAMPAIGN_ROOT / "source_eval_step32_parent_restore_train_w4"
)
publisher.ENTRYPOINT = (
    publisher.SOURCE / "scripts/run_paired_buy_now_candidate_serve.sh"
)
publisher.ENTRYPOINT_SHA256 = (
    "40c2b899dae268dadb287a748f97e9c1eb47ee71379d6a2ae1c077e97b54fe82"
)
publisher.SOURCE_GIT_SHA = "931bd7b7bdc1ef4941a6d84c3c7a260d396b3fb6"
publisher.SOURCE_IDENTITY = {
    "files": 92,
    "bytes": 2_184_320,
    "tree_sha256": "e416558fc4f8cfa6785e329c9203a96cd1f490a6648d7ef81035ca0824887fd2",
}
publisher.RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_s32_prompt_ablate_serve_release_w1.json"
)
publisher.HANDOFF_SCHEMA = "c2-s32-prompt-ablate-candidate-release-handoff.v1"
publisher.LOCAL_PORT = 19320


if __name__ == "__main__":
    publisher.main()
