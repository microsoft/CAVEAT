#!/usr/bin/env python3
"""Wait for complete step33 receipt, then publish, serve, and evaluate."""

from pathlib import Path

import activate_c2_paired_fastpath as activation

WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
CAMPAIGN_ROOT = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step33-upstream-training-w1-20260815"
)


def main() -> None:
    activation.PUBLISHER = WORKSPACE / "scripts/release_c2_step33_upstream_candidate.py"
    activation.LAUNCHER = WORKSPACE / "scripts/launch_c2_step33_upstream_fast_eval.py"
    activation.HANDOFF = (
        WORKSPACE
        / "results/harness_posttrain_campaign2_20260814/orchestration/"
        "c2_step33_upstream_candidate_release_handoff_w2.json"
    )
    activation.POD = "t-yuxuanli-hpt-c2-step33-upstream-candidate-w2-master-0"
    activation.RECEIPT = (
        CAMPAIGN_ROOT
        / "step33_training_run_r3/training/training/training_receipt.json"
    )
    activation.main()


if __name__ == "__main__":
    main()
