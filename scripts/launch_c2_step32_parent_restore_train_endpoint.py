#!/usr/bin/env python3
"""Launch the sealed Step32 parent as a TRAIN-only collection endpoint."""

from pathlib import Path

import launch_c2_train_endpoint as launcher

WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
RESULTS = WORKSPACE / "results/harness_posttrain_campaign2_20260814"


def main() -> None:
    job = "t-yuxuanli-hpt-c2-step32-parent-restore-train-w1"
    launcher.main(
        [
            "--handoff",
            str(
                RESULTS
                / "orchestration/"
                "c2_step32_parent_restore_train_release_handoff_w1.json"
            ),
            "--endpoint-root",
            str(RESULTS / "step32_parent_restore_train_endpoint_w1"),
            "--serve-job",
            job,
            "--serve-pod",
            job + "-master-0",
            "--serve-pod-uid",
            "14ef016e-a049-4ad9-b256-f707c550577b",
            "--local-port",
            "18553",
            "--handoff-schema",
            "c2-step32-parent-restore-train-release-handoff.v1",
            "--alias-marker",
            "step32-rebind-bridge-cleanup-paired",
        ]
    )


if __name__ == "__main__":
    main()
