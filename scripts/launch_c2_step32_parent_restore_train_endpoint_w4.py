#!/usr/bin/env python3
"""Launch the fully UID-bound Step32 parent TRAIN-only w4 endpoint."""

from pathlib import Path

import launch_c2_train_endpoint as launcher

WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
RESULTS = WORKSPACE / "results/harness_posttrain_campaign2_20260814"


def main() -> None:
    job = "t-yuxuanli-hpt-c2-step32-parent-restore-train-w4"
    launcher.main(
        [
            "--handoff",
            str(
                RESULTS
                / "orchestration/"
                "c2_step32_parent_restore_train_release_handoff_w4.json"
            ),
            "--endpoint-root",
            str(RESULTS / "step32_parent_restore_train_endpoint_w4"),
            "--serve-job",
            job,
            "--serve-pod",
            job + "-master-0",
            "--serve-pod-uid",
            "daf38b8b-7e6c-409d-af24-7d94ec8a3f66",
            "--local-port",
            "18558",
            "--handoff-schema",
            "c2-step32-parent-restore-train-release-handoff.v1",
            "--alias-marker",
            "step32-rebind-bridge-cleanup-paired",
        ]
    )


if __name__ == "__main__":
    main()
