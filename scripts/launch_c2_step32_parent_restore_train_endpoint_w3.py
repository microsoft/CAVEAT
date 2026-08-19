#!/usr/bin/env python3
"""Launch the UID-bound Step32 parent TRAIN-only w3 endpoint."""

from pathlib import Path

import launch_c2_train_endpoint as launcher

WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
RESULTS = WORKSPACE / "results/harness_posttrain_campaign2_20260814"


def main() -> None:
    job = "t-yuxuanli-hpt-c2-step32-parent-restore-train-w3"
    launcher.main(
        [
            "--handoff",
            str(
                RESULTS
                / "orchestration/"
                "c2_step32_parent_restore_train_release_handoff_w3.json"
            ),
            "--endpoint-root",
            str(RESULTS / "step32_parent_restore_train_endpoint_w3"),
            "--serve-job",
            job,
            "--serve-pod",
            job + "-master-0",
            "--serve-pod-uid",
            "b3f531e9-6216-4b56-bb47-9d2db93ae738",
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
