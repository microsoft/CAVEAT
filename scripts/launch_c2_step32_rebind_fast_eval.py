#!/usr/bin/env python3
"""Launch frozen macro4 and exact8 suites against the step32 rebind candidate."""

from pathlib import Path

import launch_c2_paired_fast_eval as launcher

RESULTS = Path(
    "/home/t-yuxuanli/preference-fidelity/"
    "results/harness_posttrain_campaign2_20260814"
)


def main() -> None:
    launcher.ENDPOINT_ROOT = RESULTS / "step32_rebind_candidate_endpoint_w2"
    launcher.MACRO_ROOT = RESULTS / "step32_rebind_candidate_macro4_r2"
    launcher.EXACT_ROOT = RESULTS / "step32_rebind_candidate_exact8_r2"
    launcher.LOCAL_PORT = 18550
    launcher.MACRO_PORT = 52000
    launcher.EXACT_PORT = 52100
    launcher.SERVE_JOB = "t-yuxuanli-hpt-c2-step32-rebind-candidate-w2"
    launcher.SERVE_POD = launcher.SERVE_JOB + "-master-0"
    launcher.SERVE_POD_UID = "adf37d82-0c57-4138-b13e-001e7f971bc3"
    launcher.HANDOFF_SCHEMA = "c2-step32-rebind-candidate-release-handoff.v1"
    launcher.ALIAS_MARKER = "step32-rebind-bridge-cleanup-paired"
    launcher.main()


if __name__ == "__main__":
    main()
