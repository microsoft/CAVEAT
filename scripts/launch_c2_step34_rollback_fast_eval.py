#!/usr/bin/env python3
"""Launch frozen macro4 and exact8 suites against the step34 rollback candidate."""

from pathlib import Path

import launch_c2_paired_fast_eval as launcher

RESULTS = Path(
    "/home/t-yuxuanli/preference-fidelity/"
    "results/harness_posttrain_campaign2_20260814"
)


def main() -> None:
    launcher.ENDPOINT_ROOT = RESULTS / "step34_rollback_candidate_endpoint_w1"
    launcher.MACRO_ROOT = RESULTS / "step34_rollback_candidate_macro4_r1"
    launcher.EXACT_ROOT = RESULTS / "step34_rollback_candidate_exact8_r1"
    launcher.LOCAL_PORT = 18552
    launcher.MACRO_PORT = 52400
    launcher.EXACT_PORT = 52500
    launcher.SERVE_JOB = "t-yuxuanli-hpt-c2-step34-rollback-candidate-w1"
    launcher.SERVE_POD = launcher.SERVE_JOB + "-master-0"
    launcher.SERVE_POD_UID = "e0926e8a-d0d4-4fca-98e7-8737b98cec94"
    launcher.HANDOFF_SCHEMA = "c2-step34-rollback-candidate-release-handoff.v1"
    launcher.ALIAS_MARKER = "step34-rollback-step32-stable-action-mixed"
    launcher.main()


if __name__ == "__main__":
    main()
