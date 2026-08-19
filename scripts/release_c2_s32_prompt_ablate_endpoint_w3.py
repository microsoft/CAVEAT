#!/usr/bin/env python3
"""Publish exact S32 into the fully prevalidated UID-bound W3 holder."""

from pathlib import Path

import release_c2_step32_rebind_candidate as publisher

publisher.JOB = "t-yuxuanli-hpt-c2-s32-prompt-ablate-serve-w3"
publisher.POD = publisher.JOB + "-master-0"
publisher.POD_UID = "1a35d617-8d9e-4e6e-87c8-ea831f557eb2"
publisher.SOURCE = (
    publisher.CAMPAIGN_ROOT / "source_eval_s32_prompt_ablate_serve_w3"
)
publisher.ENTRYPOINT = (
    publisher.SOURCE / "scripts/run_paired_buy_now_candidate_serve.sh"
)
publisher.ENTRYPOINT_SHA256 = (
    "d6c168c85cf1d2bf74afc2823c8158b763d8eef1475c6b8afe0af742717f1f5e"
)
publisher.SOURCE_GIT_SHA = "cd23285e0101d4ae1278363cb7d3f99b1ecd5515"
publisher.SOURCE_IDENTITY = {
    "files": 92,
    "bytes": 2_184_308,
    "tree_sha256": "66ce2dbac383deada8bef16680aa7aedb0350b49d2a7be55aa3fd8a69b127928",
}
publisher.RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_s32_prompt_ablate_serve_release_w3.json"
)
publisher.HANDOFF_SCHEMA = "c2-s32-prompt-ablate-candidate-release-handoff.v3"
publisher.LOCAL_PORT = 19320


if __name__ == "__main__":
    publisher.main()
