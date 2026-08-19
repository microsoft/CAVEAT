#!/usr/bin/env python3
"""Render the create-once r2 successor using the sealed complete runtime overlay."""

from pathlib import Path

import prepare_c2_s32_prompt_ablate_exact8 as prepare


prepare.SOURCE_ROOT = (
    prepare.WORKSPACE / ".worktrees/c2_s32_precommit_prompt_runtime_r2"
)
prepare.OUTPUT = prepare.CAMPAIGN_ROOT / "s32_prompt_ablate_exact8_r2"
prepare.BASE_PORT = 52800


if __name__ == "__main__":
    prepare.main()
