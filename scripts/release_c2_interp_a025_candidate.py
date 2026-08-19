#!/usr/bin/env python3
"""Publish the sealed alpha-0.25 LoRA interpolation into reservation w3."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import release_c2_paired_candidate as base

NAMESPACE = "bonete61"
JOB = "t-yuxuanli-hpt-c2-interp-a025-candidate-w3"
POD = JOB + "-master-0"
POD_UID = "fb1adbb0-0659-445e-b582-7f38e8f373b3"
ROOT = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step32-step33-interpolation-w1-20260815"
)
RECEIPT = ROOT / "alpha_0p25/interpolation_receipt.json"
CANDIDATE = ROOT / "alpha_0p25/lora_adapters"
SOURCE = ROOT / "source_eval_interpolation_a025_w3"
ENTRYPOINT = SOURCE / "scripts/run_interpolation_candidate_serve.sh"
ENTRYPOINT_SHA256 = "37dcd71a7d4914ca051dcc9dabda6434cee4557e302ec84e6d6b1a738fa69f9c"
SOURCE_GIT_SHA = "abf0c6365b79005d79c8923247cb890cc9d2336d"
SOURCE_IDENTITY = {
    "files": 94,
    "bytes": 2_196_724,
    "tree_sha256": "b8743c9eaa599c626ab4a9d13368fd75269e9a5f83fcfcaf50da65d197958c01",
}
RELEASE = Path(
    "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/"
    "browser_action_next_iteration_reservations/20260813T1108Z/"
    "c2_step32_step33_interp_a025_candidate_serve_release_w3.json"
)
RECEIPT_SCHEMA = "harness-posttrain.step32-step33-lora-interpolation-receipt.v1"
RECEIPT_FILE_SHA256 = "3e56dc4711c99ea78d25f4e160e6756c8f203c85d29aa77a14999c922e4e6d24"
RECEIPT_BODY_SHA256 = "f64e07122620203b38c246999c4f19b75abb28284e83dbe679e81086d682d142"
CANDIDATE_TREE = "7c6bc5c2d6e6d3202fd53d2a946c060fed2409f1815d1e5df6f154e7801bd315"
CONFIG_SHA256 = "65aaf22799f16fe45c4b52cab14e14e480f28c5817fc2a808b0aeebd193ae6c7"
STABLE_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
PARENTS = {
    "step32": {
        "path": (
            "/data/runs/t-yuxuanli/"
            "t-yuxuanli-hpt-c2-step32-rebind-training-w1-20260815/"
            "step32_training_run_r2/training/prime_output/weights/step_32/"
            "lora_adapters"
        ),
        "files": 2,
        "bytes": 933_975_509,
        "tree_sha256": "a2c2fe90c23bece6ba793d07d461304676d4bff8826e5d1464afd6bf8192dece",
        "receipt_file_sha256": "40e8f39c9550c03c08d58edccab649ed39b5e3e5ccb1a458d5888e4138889671",
    },
    "step33": {
        "path": (
            "/data/runs/t-yuxuanli/"
            "t-yuxuanli-hpt-c2-step33-upstream-training-w1-20260815/"
            "step33_training_run_r3/training/training/prime_output/weights/"
            "step_33/lora_adapters"
        ),
        "files": 2,
        "bytes": 933_975_509,
        "tree_sha256": "d8045f96205b360229c1a320933946994c96ee8878852b08ea01f5e065b6fc65",
        "receipt_file_sha256": "51d205e8e6dd0ee346546a4ade4fae3334bf289266e11f0e3b19f19175f50c40",
    },
}
TENSOR_CONTRACT = {
    "count": 992,
    "keys_shapes_dtypes_sha256": "fbdf4b956efd4e76cb70ad4ed2d0d95410923635cfff7db0eb59a980e2eab870",
    "metadata_sha256": "4284105d58c01b44e8c1e37cc90ed3a873e625d8b74bcebe9e529448fc08a0fd",
}
ALPHA = {
    "fraction": "1/4",
    "float": 0.25,
    "formula": "(1-alpha)*step32 + alpha*step33",
}
ALIAS_MARKER = "step32-step33-interp-a025"
HANDOFF_SCHEMA = "c2-step32-step33-interp-a025-candidate-handoff.v1"
LOCAL_PORT = 18554

REMOTE_TREE_PROGRAM = r"""
import hashlib,json,pathlib,sys
root=pathlib.Path(sys.argv[1]).resolve()
def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def sha(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''):
            value.update(block)
    return value.hexdigest()
assert root.is_dir() and not root.is_symlink()
entries={}
for path in sorted(root.rglob('*')):
    assert not path.is_symlink()
    if path.is_file():
        entries[path.relative_to(root).as_posix()]={'size':path.stat().st_size,'sha256':sha(path)}
    else:
        assert path.is_dir()
assert entries
print(json.dumps({'files':len(entries),'bytes':sum(row['size'] for row in entries.values()),'tree_sha256':hashlib.sha256(canonical(entries)).hexdigest()},sort_keys=True))
"""


def _live_pod() -> None:
    value = json.loads(
        subprocess.check_output(
            ["kubectl", "-n", NAMESPACE, "get", "pod", POD, "-o", "json"]
        )
    )
    statuses = value.get("status", {}).get("containerStatuses") or []
    if (
        value.get("metadata", {}).get("uid") != POD_UID
        or value.get("status", {}).get("phase") != "Running"
        or len(statuses) != 1
        or statuses[0].get("restartCount") != 0
    ):
        raise RuntimeError("interpolation candidate pod identity/state changed")


def _source_descriptor() -> None:
    descriptor = json.loads(base.remote_bytes(SOURCE.with_name(SOURCE.name + ".identity.json")))
    if (
        descriptor.get("status") != "staged_read_only"
        or descriptor.get("root") != str(SOURCE)
        or descriptor.get("git_sha") != SOURCE_GIT_SHA
        or any(descriptor.get(key) != value for key, value in SOURCE_IDENTITY.items())
        or (descriptor.get("required_sha256") or {}).get(
            "scripts/run_interpolation_candidate_serve.sh"
        )
        != ENTRYPOINT_SHA256
    ):
        raise RuntimeError("interpolation serving source identity changed")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true", required=True)
    parser.add_argument("--handoff-output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.handoff_output.exists():
        parser.error(f"handoff already exists: {arguments.handoff_output}")

    base.NAMESPACE = NAMESPACE
    base.JOB = JOB
    base.POD = POD
    base.POD_UID = POD_UID
    _live_pod()
    _source_descriptor()
    receipt_bytes = base.remote_bytes(RECEIPT)
    receipt = json.loads(receipt_bytes)
    receipt_body = base.validate_canonical_body(
        receipt, "receipt_body_sha256", "interpolation receipt"
    )
    candidate = receipt.get("candidate") or {}
    if (
        hashlib.sha256(receipt_bytes).hexdigest() != RECEIPT_FILE_SHA256
        or receipt_body != RECEIPT_BODY_SHA256
        or receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("status") != "ok"
        or receipt.get("method")
        != "elementwise_adapter_parameter_linear_interpolation"
        or receipt.get("alpha") != ALPHA
        or receipt.get("parents") != PARENTS
        or receipt.get("adapter_config_sha256") != CONFIG_SHA256
        or receipt.get("tensor_contract") != TENSOR_CONTRACT
        or receipt.get("training_rows") != 0
        or receipt.get("optimizer_updates") != 0
        or receipt.get("evaluation_rows") != 0
        or Path(str(candidate.get("path", ""))).resolve() != CANDIDATE
        or candidate.get("files") != 2
        or candidate.get("bytes") != 933_975_509
        or candidate.get("tree_sha256") != CANDIDATE_TREE
        or candidate.get("adapter_config_sha256") != CONFIG_SHA256
        or candidate.get("stable_marker_sha256") != STABLE_SHA256
    ):
        parser.error("sealed alpha-0.25 interpolation receipt changed")
    observed_tree = json.loads(
        base.kube(
            "/opt/prime-rl/.venv/bin/python",
            "-c",
            REMOTE_TREE_PROGRAM,
            str(CANDIDATE),
        )
    )
    if observed_tree != {
        "files": 2,
        "bytes": 933_975_509,
        "tree_sha256": CANDIDATE_TREE,
    }:
        parser.error("alpha-0.25 interpolation adapter bytes changed")

    alias = (
        f"qwen35-browser-action-{ALIAS_MARKER}-{CANDIDATE_TREE[:12]}-exact-lora"
    )
    composite = base.digest(
        {
            "schema": "harness-posttrain.exact-lora-composite.v1",
            "execution": "peft_unmerged_exact_lora",
            "parent_tree_sha256": base.PARENT_TREE,
            "adapter_tree_sha256": CANDIDATE_TREE,
            "adapter_config_sha256": CONFIG_SHA256,
            "tokenizer_json_sha256": base.TOKENIZER,
            "chat_template_sha256": base.CHAT_TEMPLATE,
            "dtype": "bfloat16",
        }
    )
    core: dict[str, Any] = {
        "schema": "harness-posttrain.browser-action-next-iteration.c2-candidate-serve-release.v1",
        "status": "released",
        "purpose": "c2_candidate_serve",
        "laptop_r01_outcomes_read": False,
        "office_chair_outcomes_read": False,
        "reservation": {"job_name": JOB, "pod_name": POD, "pod_uid": POD_UID},
        "source": {"root": str(SOURCE), "git_sha": SOURCE_GIT_SHA, **SOURCE_IDENTITY},
        "entrypoint": {"path": str(ENTRYPOINT), "sha256": ENTRYPOINT_SHA256},
        "argv": [
            ENTRYPOINT_SHA256,
            str(RECEIPT),
            RECEIPT_FILE_SHA256,
            RECEIPT_BODY_SHA256,
            CANDIDATE_TREE,
            CONFIG_SHA256,
            STABLE_SHA256,
            "1/4",
            JOB,
            POD,
            POD_UID,
        ],
    }
    release = {**core, "release_sha256": base.digest(core)}
    payload = json.dumps(release, sort_keys=True, indent=2).encode() + b"\n"
    base.atomic_remote_write(RELEASE, payload)
    handoff = {
        "schema": HANDOFF_SCHEMA,
        "status": "published_after_successful_receipt",
        "development_probe_only": True,
        "training_eligible": False,
        "interpolation_alpha": "1/4",
        "receipt_path": str(RECEIPT),
        "receipt_file_sha256": RECEIPT_FILE_SHA256,
        "receipt_body_sha256": RECEIPT_BODY_SHA256,
        "candidate_tree_sha256": CANDIDATE_TREE,
        "candidate_composite_sha256": composite,
        "alias": alias,
        "release_path": str(RELEASE),
        "release_body_sha256": release["release_sha256"],
        "serve_job": JOB,
        "serve_pod": POD,
        "serve_pod_uid": POD_UID,
        "local_base_url": f"http://127.0.0.1:{LOCAL_PORT}/v1",
    }
    base.atomic_local_write(
        arguments.handoff_output,
        json.dumps(handoff, sort_keys=True, indent=2).encode() + b"\n",
    )
    print(json.dumps(handoff, sort_keys=True))


if __name__ == "__main__":
    main()
