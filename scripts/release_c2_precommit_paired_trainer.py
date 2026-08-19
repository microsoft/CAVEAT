#!/usr/bin/env python3
"""Atomically bind the inert step29->30 PRECOMMIT-ANY trainer reservation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import release_c2_paired_pref_trainer as publisher


NAMESPACE = "bonete61"
JOB = "t-yuxuanli-hpt-c2-precommit-paired-w1"
JOB_UID = "fe884618-81b6-40e8-9937-acc6c70f99c9"
POD = f"{JOB}-master-0"
CAMPAIGN_ROOT = Path(
    "/data/runs/t-yuxuanli/t-yuxuanli-hpt-c2-precommit-paired-w1-20260815"
)
RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_precommit_paired_training_release_w1.json"
)


def live_pod_uid() -> str:
    raw = subprocess.check_output(
        ["kubectl", "-n", NAMESPACE, "get", "pod", POD, "-o", "json"]
    )
    pod = json.loads(raw)
    owners = pod.get("metadata", {}).get("ownerReferences") or []
    expected_owner = {
        "apiVersion": "batch.volcano.sh/v1alpha1",
        "kind": "Job",
        "name": JOB,
        "uid": JOB_UID,
    }
    if not any(
        all(owner.get(key) == value for key, value in expected_owner.items())
        for owner in owners
    ):
        raise RuntimeError("trainer pod is not owned by the exact reserved VCJob")
    if pod.get("metadata", {}).get("deletionTimestamp") is not None:
        raise RuntimeError("trainer pod is terminating")
    statuses = pod.get("status", {}).get("containerStatuses") or []
    if (
        pod.get("status", {}).get("phase") != "Running"
        or len(statuses) != 1
        or statuses[0].get("name") != "master"
        or statuses[0].get("ready") is not True
        or statuses[0].get("restartCount") != 0
    ):
        raise RuntimeError("trainer reservation is not cleanly Running/Ready")
    uid = pod.get("metadata", {}).get("uid")
    if not isinstance(uid, str) or not uid:
        raise RuntimeError("trainer pod UID is absent")
    return uid


def main() -> None:
    publisher.NAMESPACE = NAMESPACE
    publisher.JOB = JOB
    publisher.POD = POD
    publisher.POD_UID = live_pod_uid()
    publisher.CAMPAIGN_ROOT = CAMPAIGN_ROOT
    publisher.PLAN = CAMPAIGN_ROOT / "training_r1/training/plan.json"
    publisher.RELEASE = RELEASE
    publisher.RELEASE_SCHEMA = (
        "harness-posttrain.browser-action-next-iteration."
        "c2-precommit-paired-training-release.v1"
    )
    publisher.PURPOSE = "c2_precommit_paired_training"
    publisher.EXPECTED_SOURCE_STEP = 29
    publisher.EXPECTED_FINAL_STEP = 30
    publisher.EXPECTED_OPTIMIZER_UPDATES = 1
    publisher.main()


if __name__ == "__main__":
    main()
