#!/usr/bin/env python3
"""Wait for complete Step34 receipt, then publish, serve, and evaluate."""

from pathlib import Path

import activate_c2_paired_fastpath as activation

WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
CAMPAIGN_ROOT = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step34-rollback-training-w1-20260815"
)


def main() -> None:
    activation.PUBLISHER = WORKSPACE / "scripts/release_c2_step34_rollback_candidate.py"
    activation.LAUNCHER = WORKSPACE / "scripts/launch_c2_step34_rollback_fast_eval.py"
    activation.HANDOFF = (
        WORKSPACE
        / "results/harness_posttrain_campaign2_20260814/orchestration/"
        "c2_step34_rollback_candidate_release_handoff_w1.json"
    )
    activation.POD = "t-yuxuanli-hpt-c2-step34-rollback-candidate-w1-master-0"
    activation.RECEIPT = (
        CAMPAIGN_ROOT / "step34_training_run_r1/training/training_receipt.json"
    )
    activation.main()


if __name__ == "__main__":
    main()
