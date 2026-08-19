#!/usr/bin/env python3
"""Publish the sealed three-microcheckpoint Step35 A development candidate."""

from pathlib import Path

import release_c2_step35_b_profile_candidate_w2 as shared

shared.JOB = "t-yuxuanli-hpt-c2-step35-micro-candidate-w3"
shared.POD = shared.JOB + "-master-0"
shared.PLAN = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step35-proximal-a-training-w1-20260815/"
    "step35_proximal_a_run_r3/training/plan.json"
)
shared.PLAN_FILE_SHA256 = (
    "6667e885a7d4cfded801b640563e3782662948bce349ab4f96ad52c0015100e3"
)
shared.PLAN_BODY_SHA256 = (
    "f265b1e331a438d29bb536c923a60bce6532d8a156b8b395c77288c25feca391"
)
shared.TRAINER_SOURCE = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step35-proximal-a-training-w1-20260815/"
    "staged_source_da796b4199bc52f4a281e56bc71a922399f1230f"
)
shared.RECEIPT = shared.PLAN.parent / "training_receipt.json"
shared.RECEIPT_FILE_SHA256 = (
    "e369377b9dbc865358c644daac960cbdd41ecbd7abe769705fb22f45135c5e54"
)
shared.RECEIPT_BODY_SHA256 = (
    "faef5e6e66cf9f7ab959b0dbd5eb7b5824473fd84eb913969f2acb2bdc27572f"
)
shared.EXPECTED_ADAPTERS = (
    {
        "step": 33,
        "tree_sha256": "e164bf30719dac07e024a89d147b9b22a87a81d646f1a3f9a0cc4fcb53840081",
        "alias": (
            "qwen35-browser-action-step35-trajectory-proximal-a-step33-"
            "e164bf30719d-exact-lora"
        ),
    },
    {
        "step": 34,
        "tree_sha256": "187cad1ad61b7872a75954ab24761a5827e45921a17d046217cb32e18f5aec49",
        "alias": (
            "qwen35-browser-action-step35-trajectory-proximal-a-step34-"
            "187cad1ad61b-exact-lora"
        ),
    },
    {
        "step": 35,
        "tree_sha256": "4802c7cbda29fabb1f950e9f59b6c37cef649d39524bc58e08f4007617a51f9c",
        "alias": (
            "qwen35-browser-action-step35-trajectory-proximal-a-step35-"
            "4802c7cbda29-exact-lora"
        ),
    },
)
shared.RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_step35_micro_candidate_serve_release_w3.json"
)
shared.LOCAL_PORT = 18557
shared.PROFILE = "A"
shared.BINDING_ARGS = [
    "run-server",
    "--artifact-mode",
    "final_receipt",
    "--usage-class",
    "development_probe_inference_only",
    "--profile",
    "A",
    "--receipt",
    str(shared.RECEIPT),
    "--receipt-file-sha256",
    shared.RECEIPT_FILE_SHA256,
    "--receipt-body-sha256",
    shared.RECEIPT_BODY_SHA256,
    "--plan",
    str(shared.PLAN),
    "--plan-file-sha256",
    shared.PLAN_FILE_SHA256,
    "--plan-body-sha256",
    shared.PLAN_BODY_SHA256,
    "--trainer-source",
    str(shared.TRAINER_SOURCE),
    "--trainer-git-sha",
    shared.TRAINER_GIT_SHA,
    "--trainer-tree-sha256",
    shared.TRAINER_TREE_SHA256,
    "--trainer-files",
    str(shared.TRAINER_FILES),
    "--trainer-bytes",
    str(shared.TRAINER_BYTES),
]

if __name__ == "__main__":
    shared.main()
