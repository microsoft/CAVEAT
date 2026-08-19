#!/usr/bin/env python3
"""Publish sealed Step32 adapter into the UID-bound TRAIN-only w3 reservation."""

from pathlib import Path

import release_c2_step32_rebind_candidate as publisher

publisher.JOB = "t-yuxuanli-hpt-c2-step32-parent-restore-train-w3"
publisher.POD = publisher.JOB + "-master-0"
publisher.POD_UID = "b3f531e9-6216-4b56-bb47-9d2db93ae738"
publisher.SOURCE = (
    publisher.CAMPAIGN_ROOT / "source_eval_step32_parent_restore_train_w3"
)
publisher.ENTRYPOINT = publisher.SOURCE / "scripts/run_paired_buy_now_candidate_serve.sh"
publisher.ENTRYPOINT_SHA256 = (
    "9cd9e94e045dd88fdbe28ce2d3a8431c41fbb2eac4bd2fe2bfdeac168fc2d016"
)
publisher.SOURCE_GIT_SHA = "1ca7bb53b3ab76246ddb3a8c7054334f12e9102c"
publisher.SOURCE_IDENTITY = {
    "files": 92,
    "bytes": 2_184_320,
    "tree_sha256": "c0288c34cf47031775fffc2c30ff93df2673740d25c12f478b6c331b13e6cf3c",
}
publisher.RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_step32_parent_restore_train_serve_release_w3.json"
)
publisher.HANDOFF_SCHEMA = "c2-step32-parent-restore-train-release-handoff.v1"
publisher.LOCAL_PORT = 18558


if __name__ == "__main__":
    publisher.main()
