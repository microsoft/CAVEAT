#!/usr/bin/env python3
"""Publish exact S32 into the corrected UID-bound prompt-ablation holder."""

from pathlib import Path

import release_c2_step32_rebind_candidate as publisher

publisher.JOB = "t-yuxuanli-hpt-c2-s32-prompt-ablate-serve-w2"
publisher.POD = publisher.JOB + "-master-0"
publisher.POD_UID = "7090254f-da1d-4a4c-8af3-1e58b65f3422"
publisher.SOURCE = (
    publisher.CAMPAIGN_ROOT / "source_eval_s32_prompt_ablate_serve_w2"
)
publisher.ENTRYPOINT = (
    publisher.SOURCE / "scripts/run_paired_buy_now_candidate_serve.sh"
)
publisher.ENTRYPOINT_SHA256 = (
    "04bf20b956f7e13667629c225fc59b19695dcdcc7c8395d618e4637bc61f6bf7"
)
publisher.SOURCE_GIT_SHA = "66320cae0bd429ed593d3095d13e72b9a389726b"
publisher.SOURCE_IDENTITY = {
    "files": 92,
    "bytes": 2_184_312,
    "tree_sha256": "82c87b905be8f9f58d54197a6a70ea78c434cf581f737a81980dceee3204d351",
}
publisher.RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_s32_prompt_ablate_serve_release_w2.json"
)
publisher.HANDOFF_SCHEMA = "c2-s32-prompt-ablate-candidate-release-handoff.v2"
publisher.LOCAL_PORT = 19320


if __name__ == "__main__":
    publisher.main()
