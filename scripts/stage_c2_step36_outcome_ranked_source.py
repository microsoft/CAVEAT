#!/usr/bin/env python3
"""Stage the sealed Step36 outcome-ranked trainer source create-once."""

from pathlib import Path

import stage_c2_step32_parent_restore_source as stage

stage.ARCHIVE = Path("/tmp/c2_step36_outcome_source.tar.gz")
stage.TARGET = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step36-outcome-ranked-training-w1-20260816/"
    "source_distill_57688c01"
)
stage.STAGING = stage.TARGET.with_name(f".{stage.TARGET.name}.staging")
stage.DESCRIPTOR = stage.TARGET.with_name(f"{stage.TARGET.name}.identity.json")
stage.ARCHIVE_SHA256 = (
    "40530a321dacdf9ab93df188f1386c18e13d07aaf081860b3e5c451107a9d18d"
)
stage.ARCHIVE_BYTES = 41_605_611
stage.GIT_SHA = "57688c012f77a07d7cfeaea938e08d1b07d66342"
stage.FILES = 372
stage.DIRECTORIES = 21
stage.BYTES = 46_678_220
stage.TREE_SHA256 = (
    "8133bdb472456c73b2f8b1633d219f975551bd6d21f3b1fe94ee83caaf37fde8"
)
stage.MODE_TREE_SHA256 = (
    "de21263f79f57c569348f924c13fda414c77309d66e400fb62ec4b386c6a386a"
)
stage.REQUIRED = {
    "scripts/run_step35_trajectory_training.sh": (
        "01be3138e995908c203ccf8616507a2f8b1e0b996f12f099ff5d5f8afa6a9858"
    ),
    "src/harness_distill/verified_outcome_fastpath_projection.py": (
        "290c55210bee8cc25aaf6e736f27d9a9f86a300dd947f4867e8e75824b3df2b9"
    ),
    "src/harness_distill/step35_trajectory_training.py": (
        "fc9da51a436cd57bc74e01148fce6720032971a0e921514247b7d41736ba0a42"
    ),
}


if __name__ == "__main__":
    stage.main()
