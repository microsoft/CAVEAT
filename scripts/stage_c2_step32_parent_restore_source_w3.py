#!/usr/bin/env python3
"""Stage the UID-bound Step32 TRAIN restore w3 source create-once."""

from pathlib import Path

import stage_c2_step32_parent_restore_source as stage

stage.ARCHIVE = Path("/tmp/c2_step32_restore_w3_source.tar.gz")
stage.TARGET = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step32-rebind-training-w1-20260815/"
    "source_eval_step32_parent_restore_train_w3"
)
stage.STAGING = stage.TARGET.with_name(f".{stage.TARGET.name}.staging")
stage.DESCRIPTOR = stage.TARGET.with_name(f"{stage.TARGET.name}.identity.json")
stage.ARCHIVE_SHA256 = (
    "f9a5c6494f4a58877dd3ed80e5ccbb1b089ee14a12acd1387dae481c4b118c86"
)
stage.ARCHIVE_BYTES = 359_192
stage.GIT_SHA = "1ca7bb53b3ab76246ddb3a8c7054334f12e9102c"
stage.FILES = 92
stage.DIRECTORIES = 7
stage.BYTES = 2_184_320
stage.TREE_SHA256 = (
    "c0288c34cf47031775fffc2c30ff93df2673740d25c12f478b6c331b13e6cf3c"
)
stage.MODE_TREE_SHA256 = (
    "dff9db6365db297e2ac476dd726a7771a7c850de418789892a79debd2b0bd0e3"
)
stage.REQUIRED = {
    "scripts/run_paired_buy_now_candidate_serve.sh": (
        "9cd9e94e045dd88fdbe28ce2d3a8431c41fbb2eac4bd2fe2bfdeac168fc2d016"
    )
}


if __name__ == "__main__":
    stage.main()
