#!/usr/bin/env python3
"""Wait for a complete HERO strict receipt, publish, serve, and evaluate."""

from pathlib import Path

import activate_c2_paired_fastpath as activation


WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
CAMPAIGN_ROOT = Path(
    "/data/runs/t-yuxuanli/t-yuxuanli-hpt-c2-hero-strict-training-w1-20260815"
)


def main() -> None:
    activation.PUBLISHER = WORKSPACE / "scripts/release_c2_hero_strict_candidate.py"
    activation.LAUNCHER = WORKSPACE / "scripts/launch_c2_hero_strict_fast_eval.py"
    activation.HANDOFF = (
        WORKSPACE
        / "results/harness_posttrain_campaign2_20260814/orchestration/"
        "c2_hero_strict_candidate_release_handoff_w1.json"
    )
    activation.POD = "t-yuxuanli-hpt-c2-hero-strict-candidate-w1-master-0"
    activation.RECEIPT = CAMPAIGN_ROOT / "training_r1/training/training_receipt.json"
    activation.main()


if __name__ == "__main__":
    main()
