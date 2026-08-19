#!/usr/bin/env python3
"""Launch frozen macro4 and exact8 suites against the HERO strict candidate."""

from pathlib import Path

import launch_c2_paired_fast_eval as launcher


RESULTS = Path(
    "/home/t-yuxuanli/preference-fidelity/"
    "results/harness_posttrain_campaign2_20260814"
)


def main() -> None:
    launcher.ENDPOINT_ROOT = RESULTS / "hero_strict_candidate_endpoint_w1"
    launcher.MACRO_ROOT = RESULTS / "hero_strict_candidate_macro4_r1"
    launcher.EXACT_ROOT = RESULTS / "hero_strict_candidate_exact8_r1"
    launcher.LOCAL_PORT = 18549
    launcher.MACRO_PORT = 51800
    launcher.EXACT_PORT = 51900
    launcher.SERVE_JOB = "t-yuxuanli-hpt-c2-hero-strict-candidate-w1"
    launcher.SERVE_POD = launcher.SERVE_JOB + "-master-0"
    launcher.SERVE_POD_UID = "fc32233f-3123-429e-a610-b8f4b97828d0"
    launcher.HANDOFF_SCHEMA = "c2-hero-strict-candidate-release-handoff.v1"
    launcher.ALIAS_MARKER = "step31-strict-checkout-paired"
    launcher.main()


if __name__ == "__main__":
    main()
