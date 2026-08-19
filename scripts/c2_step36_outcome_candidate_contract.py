#!/usr/bin/env python3
"""Frozen local contract for the Step36 outcome-ranked candidate fastpath.

This module is intentionally inert.  It centralizes the exact trainer, plan,
proven serving source, reservation generations, and development-probe outputs
used by the small operational wrappers beside it.  Importing it performs no
filesystem, Kubernetes, or GPU action.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
RESULTS = WORKSPACE / "results/harness_posttrain_campaign2_20260814"
ORCHESTRATION = RESULTS / "orchestration"

NAMESPACE = "bonete61"
PROFILE = "A"
STEPS = (33, 34, 35)
PLAN_SCHEMA = "harness-distill.step35-trajectory-action-proximal-plan.v1"
RECEIPT_SCHEMA = "harness-distill.step35-trajectory-action-proximal-receipt.v1"
MICRO_INVENTORY_SCHEMA = "harness-posttrain.step35-microcheckpoint-inventory.v1"
SELECTION_STATUS = "pending_shared_train_only_probe2"

CAMPAIGN_ROOT = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step36-outcome-ranked-training-w1-20260816"
)
RUN_ROOT = CAMPAIGN_ROOT / "step36_training_run_r1"
PLAN = RUN_ROOT / "training/plan.json"
PLAN_FILE_SHA256 = "aa7f0b55596df5211c960cc492416ba33d80f74cd1f77f375601a656affb49f2"
PLAN_BODY_SHA256 = "38d996c246298872be208a6a1ab8e2093395cefe33f46979ccb11036216c1176"
TRAINING_OUTPUT = RUN_ROOT / "training/prime_output"
RECEIPT = PLAN.with_name("training_receipt.json")
INVENTORY_ROOT = PLAN.parent / "microcheckpoint_inventories"

TRAINER_SOURCE = CAMPAIGN_ROOT / "source_distill_57688c01"
TRAINER_GIT_SHA = "57688c012f77a07d7cfeaea938e08d1b07d66342"
TRAINER_TREE_SHA256 = "8133bdb472456c73b2f8b1633d219f975551bd6d21f3b1fe94ee83caaf37fde8"
TRAINER_FILES = 372
TRAINER_BYTES = 46_678_220
TRAINER_RUNNER = TRAINER_SOURCE / "scripts/run_step35_trajectory_training.sh"
TRAINER_RUNNER_SHA256 = "01be3138e995908c203ccf8616507a2f8b1e0b996f12f099ff5d5f8afa6a9858"

# This is the already staged, read-only source proven by the Step35 A/B runs.
SERVE_SOURCE = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step35-micro-candidate-w1-20260816/"
    "source_eval_step35_micro_profile_v1"
)
SERVE_SOURCE_GIT_SHA = "479d442e348b732f68b4c83fc28e35c97ee84806"
SERVE_SOURCE_FILES = 95
SERVE_SOURCE_BYTES = 2_217_182
SERVE_SOURCE_TREE_SHA256 = "d7932e3f0535dbed8bb558dee613a422001a9809b2e2e70627252f900fa3eac4"
SERVE_ENTRYPOINT = SERVE_SOURCE / "scripts/run_step35_micro_profile_candidate_serve.sh"
SERVE_ENTRYPOINT_SHA256 = "f9c12e3c7878fd59070c71d4a7812bc16460948e831d0846c0dd26b6eeb0ae9e"
SERVE_MODULE_SHA256 = "e6859973738dbc6efe7014637201a2518cb7eb9bf07942ffde852af9364d3f4b"

DEV_PROBE_LAUNCHER = WORKSPACE / "scripts/launch_c2_step35_micro_probe2.py"
DEV_PROBE_LAUNCHER_SHA256 = "2123c170940c2addc940dcab617ef78235c4ac2f7441427fbcb4709064b081a0"
ENDPOINT_LAUNCHER = WORKSPACE / "scripts/launch_c2_train_endpoint.py"
ENDPOINT_LAUNCHER_SHA256 = "a923ce6ded7db350af3184ee5b7708fbaf3dd1395f24d19cd576976bea4ee87d"

RELEASE_ROOT = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z"
)
RELEASE_SCHEMA = (
    "harness-posttrain.browser-action-next-iteration.c2-candidate-serve-release.v1"
)
HANDOFF_SCHEMA = "c2-step36-outcome-micro-candidate-handoff.v1"
RESERVATION_ENTRYPOINT = "/reservation/next_iteration_reservation_entrypoint.sh"
RESERVATION_ENTRYPOINT_SHA256 = (
    "9b17bc11dc8cf79e4330bafd0f700eb42eebe99d51fa1f6c195c24712e65b42c"
)
RESERVATION_CONFIGMAP = "hpt-next-iteration-reservation-9b17bc11dc8c"
RESERVATION_IMAGE = (
    "aifrontiers.azurecr.io/t-yuxuanli/harness-distill@sha256:"
    "28f38e74e17779e9c85985d3d3f970c1f6aa4427407f1843c11f2af87a324dd0"
)
RESERVATION_IMAGE_DIGEST = (
    "sha256:28f38e74e17779e9c85985d3d3f970c1f6aa4427407f1843c11f2af87a324dd0"
)
RESERVATION_TIMEOUT_SECONDS = 27_000

# The proven server fixes --lora-modules at exec and explicitly disables vLLM
# runtime adapter updates.  Each generation therefore gets an immutable fresh
# reservation.  A generation exposes every sealed step through its stage.
ATTEMPT_BY_STAGE = {33: 2, 34: 2, 35: 3}
JOB_BY_STAGE = {
    step: f"t-yuxuanli-hpt-c2-step36-outcome-candidate-s{step}-w{attempt}"
    for step, attempt in ATTEMPT_BY_STAGE.items()
}
POD_BY_STAGE = {step: f"{job}-master-0" for step, job in JOB_BY_STAGE.items()}
RELEASE_BY_STAGE = {
    step: RELEASE_ROOT
    / f"c2_step36_outcome_candidate_serve_release_s{step}_w{attempt}.json"
    for step, attempt in ATTEMPT_BY_STAGE.items()
}
HANDOFF_BY_STAGE = {
    step: ORCHESTRATION
    / f"c2_step36_outcome_candidate_handoff_s{step}_w{attempt}.json"
    for step, attempt in ATTEMPT_BY_STAGE.items()
}
RESERVATION_BY_STAGE = {
    step: ORCHESTRATION
    / f"c2_step36_outcome_candidate_serve_reservation_s{step}_w{attempt}.yaml"
    for step, attempt in ATTEMPT_BY_STAGE.items()
}
LOCAL_PORT_BY_STAGE = {33: 18_562, 34: 18_560, 35: 18_561}
ENDPOINT_ROOT_BY_STAGE = {
    step: RESULTS
    / f"step36_outcome_candidate_endpoint_s{step}_w{ATTEMPT_BY_STAGE[step]}"
    for step in STEPS
}
PROBE_BASE_PORT_BY_STEP = {33: 52_670, 34: 52_680, 35: 52_690}
PROBE_OUTPUT_ROOT_BY_STEP = {
    step: RESULTS / f"step36_outcome_s{step}_development_probe2_r1"
    for step in STEPS
}

HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or HEX64.fullmatch(value) is None:
        raise ValueError(f"{label} is not a lowercase SHA-256")
    return value


def inventory_path(step: int) -> Path:
    require_step(step)
    return INVENTORY_ROOT / f"step_{step}.json"


def candidate_path(step: int) -> Path:
    require_step(step)
    return TRAINING_OUTPUT / f"weights/step_{step}/lora_adapters"


def served_steps(stage: int) -> tuple[int, ...]:
    require_step(stage)
    return tuple(step for step in STEPS if step <= stage)


def require_step(step: int) -> int:
    if type(step) is not int or step not in STEPS:
        raise ValueError("Step36 candidate step must be exactly 33, 34, or 35")
    return step


def exact_alias(step: int, tree_sha256: str) -> str:
    require_step(step)
    tree = require_sha256(tree_sha256, "candidate tree")
    # The namespace is fixed by the proven source commit.  Freshness comes from
    # the exact Step36 adapter-tree suffix, not from mutable display metadata.
    return (
        "qwen35-browser-action-step35-trajectory-proximal-a-"
        f"step{step}-{tree[:12]}-exact-lora"
    )


def validate_static_contract() -> None:
    for value, label in (
        (PLAN_FILE_SHA256, "plan file"),
        (PLAN_BODY_SHA256, "plan body"),
        (TRAINER_GIT_SHA, "trainer Git"),
        (TRAINER_TREE_SHA256, "trainer tree"),
        (TRAINER_RUNNER_SHA256, "trainer runner"),
        (SERVE_SOURCE_GIT_SHA, "serve Git"),
        (SERVE_SOURCE_TREE_SHA256, "serve tree"),
        (SERVE_ENTRYPOINT_SHA256, "serve entrypoint"),
        (SERVE_MODULE_SHA256, "serve module"),
        (DEV_PROBE_LAUNCHER_SHA256, "development-probe launcher"),
        (ENDPOINT_LAUNCHER_SHA256, "endpoint launcher"),
        (RESERVATION_ENTRYPOINT_SHA256, "reservation entrypoint"),
    ):
        pattern = HEX40 if label.endswith("Git") else HEX64
        if pattern.fullmatch(value) is None:
            raise RuntimeError(f"frozen {label} identity is malformed")
    if (
        PROFILE != "A"
        or STEPS != (33, 34, 35)
        or TRAINER_FILES != 372
        or TRAINER_BYTES != 46_678_220
        or SERVE_SOURCE_FILES != 95
        or SERVE_SOURCE_BYTES != 2_217_182
        or len(set(JOB_BY_STAGE.values())) != len(STEPS)
        or len(set(RELEASE_BY_STAGE.values())) != len(STEPS)
        or len(set(LOCAL_PORT_BY_STAGE.values())) != len(STEPS)
        or len(set(PROBE_BASE_PORT_BY_STEP.values())) != len(STEPS)
    ):
        raise RuntimeError("frozen Step36 candidate contract changed")


validate_static_contract()
