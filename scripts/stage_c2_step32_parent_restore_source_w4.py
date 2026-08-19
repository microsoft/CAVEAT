#!/usr/bin/env python3
"""Stage the fully UID-bound Step32 TRAIN restore w4 source create-once."""

from pathlib import Path

import stage_c2_step32_parent_restore_source as stage

stage.ARCHIVE = Path("/tmp/c2_step32_restore_w4_source.tar.gz")
stage.TARGET = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step32-rebind-training-w1-20260815/"
    "source_eval_step32_parent_restore_train_w4"
)
stage.STAGING = stage.TARGET.with_name(f".{stage.TARGET.name}.staging")
stage.DESCRIPTOR = stage.TARGET.with_name(f"{stage.TARGET.name}.identity.json")
stage.ARCHIVE_SHA256 = (
    "debc41607ff7204566cc868bdcb535702451318b82d0175e3f73985e2f2b9794"
)
stage.ARCHIVE_BYTES = 359_205
stage.GIT_SHA = "931bd7b7bdc1ef4941a6d84c3c7a260d396b3fb6"
stage.FILES = 92
stage.DIRECTORIES = 7
stage.BYTES = 2_184_320
stage.TREE_SHA256 = (
    "e416558fc4f8cfa6785e329c9203a96cd1f490a6648d7ef81035ca0824887fd2"
)
stage.MODE_TREE_SHA256 = (
    "dff9db6365db297e2ac476dd726a7771a7c850de418789892a79debd2b0bd0e3"
)
stage.REQUIRED = {
    "scripts/run_paired_buy_now_candidate_serve.sh": (
        "40c2b899dae268dadb287a748f97e9c1eb47ee71379d6a2ae1c077e97b54fe82"
    ),
    "src/harness_posttrain_eval/paired_buy_now_candidate_serve.py": (
        "f8b5037de8f816eb304606b7ad5406b949307d6064e2729a4fc52ac13757b396"
    ),
}


if __name__ == "__main__":
    stage.main()
