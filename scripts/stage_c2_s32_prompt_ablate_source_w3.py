#!/usr/bin/env python3
"""Create-once staging for the fully UID-bound S32 ablation serve source."""

from pathlib import Path

import stage_c2_step32_parent_restore_source as stage

stage.ARCHIVE = Path("/tmp/c2_s32_prompt_ablate_serve_w3_source.tar.gz")
stage.TARGET = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step32-rebind-training-w1-20260815/"
    "source_eval_s32_prompt_ablate_serve_w3"
)
stage.STAGING = stage.TARGET.with_name(f".{stage.TARGET.name}.staging")
stage.DESCRIPTOR = stage.TARGET.with_name(f"{stage.TARGET.name}.identity.json")
stage.ARCHIVE_SHA256 = (
    "d6861059c74f118f7cb4e26a01aaac37b413c8b3acc70d315b786a3ca3b0ba6d"
)
stage.ARCHIVE_BYTES = 359_184
stage.GIT_SHA = "cd23285e0101d4ae1278363cb7d3f99b1ecd5515"
stage.FILES = 92
stage.DIRECTORIES = 7
stage.BYTES = 2_184_308
stage.TREE_SHA256 = (
    "66ce2dbac383deada8bef16680aa7aedb0350b49d2a7be55aa3fd8a69b127928"
)
stage.MODE_TREE_SHA256 = (
    "dff9db6365db297e2ac476dd726a7771a7c850de418789892a79debd2b0bd0e3"
)
stage.REQUIRED = {
    "scripts/run_paired_buy_now_candidate_serve.sh": (
        "d6c168c85cf1d2bf74afc2823c8158b763d8eef1475c6b8afe0af742717f1f5e"
    ),
    "src/harness_posttrain_eval/paired_buy_now_candidate_serve.py": (
        "5de6778d720fed30fc59d84a3f409975faf46f875d259e6c945c068451887604"
    ),
}


if __name__ == "__main__":
    stage.main()
