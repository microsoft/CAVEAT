#!/usr/bin/env python3
"""Publish the sealed Step32 adapter into its TRAIN-only restore reservation."""

from pathlib import Path

import release_c2_step32_rebind_candidate as publisher

publisher.JOB = "t-yuxuanli-hpt-c2-step32-parent-restore-train-w1"
publisher.POD = publisher.JOB + "-master-0"
publisher.POD_UID = "14ef016e-a049-4ad9-b256-f707c550577b"
publisher.SOURCE = (
    publisher.CAMPAIGN_ROOT / "source_eval_step32_parent_restore_train_v1"
)
publisher.ENTRYPOINT = publisher.SOURCE / "scripts/run_paired_buy_now_candidate_serve.sh"
publisher.ENTRYPOINT_SHA256 = (
    "1d99ad0292a59a165d2211cf0ee126f8794b703ad76c7f7f5fd3dba6efdffc84"
)
publisher.SOURCE_GIT_SHA = "370684080e4966a8aed882768ba5327aa33fb275"
publisher.SOURCE_IDENTITY = {
    "files": 92,
    "bytes": 2_184_320,
    "tree_sha256": "fcb004663452955c409a5d68aa5fe6672f9603f3377b45cdc676c94594ababfe",
}
publisher.RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_step32_parent_restore_train_serve_release_w1.json"
)
publisher.HANDOFF_SCHEMA = "c2-step32-parent-restore-train-release-handoff.v1"
publisher.LOCAL_PORT = 18553


if __name__ == "__main__":
    publisher.main()
