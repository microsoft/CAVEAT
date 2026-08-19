#!/usr/bin/env python3
"""Launch frozen macro4 and exact8 suites against the HERO50 candidate."""

from pathlib import Path

import launch_c2_paired_fast_eval as launcher


RESULTS = Path(
    "/home/t-yuxuanli/preference-fidelity/"
    "results/harness_posttrain_campaign2_20260814"
)


def main() -> None:
    launcher.ENDPOINT_ROOT = RESULTS / "hero50_candidate_endpoint_w1"
    launcher.MACRO_ROOT = RESULTS / "hero50_candidate_macro4_r1"
    launcher.EXACT_ROOT = RESULTS / "hero50_candidate_exact8_r1"
    launcher.LOCAL_PORT = 18548
    launcher.MACRO_PORT = 51600
    launcher.EXACT_PORT = 51700
    launcher.SERVE_JOB = "t-yuxuanli-hpt-c2-hero50-candidate-w1"
    launcher.SERVE_POD = launcher.SERVE_JOB + "-master-0"
    launcher.SERVE_POD_UID = "9b6ebef6-40bd-4638-9f63-00a2954ca7a6"
    launcher.HANDOFF_SCHEMA = "c2-hero50-candidate-release-handoff.v1"
    launcher.ALIAS_MARKER = "step30-hero50-paired"
    launcher.main()


if __name__ == "__main__":
    main()
