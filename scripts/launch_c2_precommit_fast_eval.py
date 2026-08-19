#!/usr/bin/env python3
"""Launch the frozen macro4 and exact8 suites against the step-30 candidate."""

from pathlib import Path

import launch_c2_paired_fast_eval as launcher


RESULTS = Path(
    "/home/t-yuxuanli/preference-fidelity/"
    "results/harness_posttrain_campaign2_20260814"
)


def main() -> None:
    launcher.ENDPOINT_ROOT = RESULTS / "precommit_candidate_endpoint_w1"
    launcher.MACRO_ROOT = RESULTS / "precommit_candidate_macro4_r1"
    launcher.EXACT_ROOT = RESULTS / "precommit_candidate_exact8_r1"
    launcher.LOCAL_PORT = 18547
    launcher.MACRO_PORT = 51400
    launcher.EXACT_PORT = 51500
    launcher.SERVE_JOB = "t-yuxuanli-hpt-c2-precommit-candidate-w1"
    launcher.SERVE_POD = launcher.SERVE_JOB + "-master-0"
    launcher.SERVE_POD_UID = "0a165abc-084c-434d-8abf-370791f8f986"
    launcher.HANDOFF_SCHEMA = "c2-precommit-candidate-release-handoff.v1"
    launcher.ALIAS_MARKER = "step30-precommit-any-paired"
    launcher.main()


if __name__ == "__main__":
    main()
