#!/usr/bin/env python3
"""Launch frozen macro4 and exact8 suites against the step33 candidate."""

from pathlib import Path

import launch_c2_paired_fast_eval as launcher

RESULTS = Path(
    "/home/t-yuxuanli/preference-fidelity/"
    "results/harness_posttrain_campaign2_20260814"
)


def main() -> None:
    launcher.ENDPOINT_ROOT = RESULTS / "step33_upstream_candidate_endpoint_w1"
    launcher.MACRO_ROOT = RESULTS / "step33_upstream_candidate_macro4_r1"
    launcher.EXACT_ROOT = RESULTS / "step33_upstream_candidate_exact8_r1"
    launcher.LOCAL_PORT = 18551
    launcher.MACRO_PORT = 52200
    launcher.EXACT_PORT = 52300
    launcher.SERVE_JOB = "t-yuxuanli-hpt-c2-step33-upstream-candidate-w2"
    launcher.SERVE_POD = launcher.SERVE_JOB + "-master-0"
    launcher.SERVE_POD_UID = "85429516-c86a-426c-b9bf-8fda61a3d5ac"
    launcher.HANDOFF_SCHEMA = "c2-step33-upstream-candidate-release-handoff.v1"
    launcher.ALIAS_MARKER = "step33-upstream-hero-recovery-paired"
    launcher.main()


if __name__ == "__main__":
    main()
