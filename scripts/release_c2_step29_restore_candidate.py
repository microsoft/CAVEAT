#!/usr/bin/env python3
"""Restore the sealed step29 paired candidate on a fresh receipt-bound reservation."""

from __future__ import annotations

from pathlib import Path

import release_c2_paired_candidate as base


base.POD = "t-yuxuanli-hpt-c2-step29-restore-w3-master-0"
base.POD_UID = "a3d9109c-bb81-4a98-9325-f462c0e6df25"
base.JOB = "t-yuxuanli-hpt-c2-step29-restore-w3"
base.SOURCE = (
    base.CAMPAIGN_ROOT / "source_eval_step29_restore_w3_fcc3ff75"
)
base.ENTRYPOINT = base.SOURCE / "scripts/run_paired_buy_now_candidate_serve.sh"
base.ENTRYPOINT_SHA256 = (
    "c949bc3d6e84e32cf8d2c01ed092f4881afcf171f2f8d8cc023568b42a0a415b"
)
base.SOURCE_GIT_SHA = "fcc3ff75f270e50e7b0929039e1f5b741103b43b"
base.SOURCE_IDENTITY = {
    "files": 92,
    "bytes": 2_180_153,
    "tree_sha256": "a318de524bf6343255c52624b98544f12b92f889d367adb207183a318c023048",
}
base.RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_step29_restore_candidate_serve_release_w3.json"
)


if __name__ == "__main__":
    base.main()
