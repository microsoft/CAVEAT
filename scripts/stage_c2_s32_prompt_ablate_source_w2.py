#!/usr/bin/env python3
"""Create-once staging for the UID-bound S32 prompt-ablation serve source."""

from pathlib import Path

import stage_c2_step32_parent_restore_source as stage

stage.ARCHIVE = Path("/tmp/c2_s32_prompt_ablate_serve_w2_source.tar.gz")
stage.TARGET = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step32-rebind-training-w1-20260815/"
    "source_eval_s32_prompt_ablate_serve_w2"
)
stage.STAGING = stage.TARGET.with_name(f".{stage.TARGET.name}.staging")
stage.DESCRIPTOR = stage.TARGET.with_name(f"{stage.TARGET.name}.identity.json")
stage.ARCHIVE_SHA256 = (
    "c77c14ebe0883cf6ff23a15d510d078182765409cd70d9ff93cb4a067d122b29"
)
stage.ARCHIVE_BYTES = 359_186
stage.GIT_SHA = "66320cae0bd429ed593d3095d13e72b9a389726b"
stage.FILES = 92
stage.DIRECTORIES = 7
stage.BYTES = 2_184_312
stage.TREE_SHA256 = (
    "82c87b905be8f9f58d54197a6a70ea78c434cf581f737a81980dceee3204d351"
)
stage.MODE_TREE_SHA256 = (
    "dff9db6365db297e2ac476dd726a7771a7c850de418789892a79debd2b0bd0e3"
)
stage.REQUIRED = {
    "scripts/run_paired_buy_now_candidate_serve.sh": (
        "04bf20b956f7e13667629c225fc59b19695dcdcc7c8395d618e4637bc61f6bf7"
    ),
    "src/harness_posttrain_eval/paired_buy_now_candidate_serve.py": (
        "f8b5037de8f816eb304606b7ad5406b949307d6064e2729a4fc52ac13757b396"
    ),
}


if __name__ == "__main__":
    stage.main()
