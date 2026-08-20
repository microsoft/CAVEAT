"""Verify and freeze the repair step-24 adapter against its exact trained parent.

This is an outcome-independent candidate operation.  It consumes only the sealed
training receipt and immutable model artifacts; CAVEAT-Shop evaluation results are
neither accepted nor inspected.  The resulting merged checkpoint is therefore a
prospective candidate artifact, not evidence that the candidate was selected.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    canonical_json,
    publish_json,
    read_json,
    sha256_bytes,
    sha256_file,
)
from .parent_merge import (
    PARENT_MERGE_SCHEMA,
    checkpoint_manifest,
    verify_parent_aware_merge,
)

TRAINING_RECEIPT_SCHEMA = "harness-distill.caveat_shop-r00-repair-sft-training-receipt.v1"
MERGE_RECEIPT_SCHEMA = "caveat-27b.repair-step24-candidate-merge.v1"
FULL_GIT_SHA = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
IMAGE_DIGEST = re.compile(r".+@sha256:[0-9a-f]{64}")
EXPECTED_TRAINING_RECEIPT_FILE_SHA256 = (
    "85d5c779c8fb1ab4a1a87d4e4f4ab9bd7809675622bfe845a0632b2ab6ae4bf1"
)
EXPECTED_TRAINING_RECEIPT_BODY_SHA256 = (
    "aa60d14b36e2bd5ae3cdab34cc74665ed883a03e59384f1174a17c624235fb1e"
)
EXPECTED_PARENT_TREE_SHA256 = "5939382fbc6db775972dc9cebf3e5be20654149a0412b4f00072bad12a2215ca"
EXPECTED_ADAPTER_TREE_SHA256 = "49e66d189603142232d0e4f1b3549d32b783ebed5fa4d3efd0e04af3197a5508"
EXPECTED_TRAINING_EXECUTOR_GIT_SHA = "6cf2d5a0154ff644f6766b03648663bdbed10ec8"
EXPECTED_REPLAY_EXECUTOR_GIT_SHA = "7ceed1104fea9807a8acd3d3cc0503dfb208c498"
EXPECTED_REPLAY_VALIDATOR_GIT_SHA = "86e7f2cab46dedcb2b44b7fe0931d1ed60fc8dc1"
EXPECTED_SCIENTIFIC_LABEL = "same_task_laptop_r00_real_state_cart_repair_sft"
EXPECTED_CANDIDATE_NAME = "step24-caveat_shop-r00-repair-sft"
EXPECTED_LEARNING_RATE = 5.0e-7


def _self_hash(value: Mapping[str, Any], field: str, *, label: str) -> str:
    expected = value.get(field)
    if not isinstance(expected, str) or SHA256.fullmatch(expected) is None:
        raise ArtifactError(f"{label} has no valid {field}")
    body = {key: item for key, item in value.items() if key != field}
    observed = sha256_bytes(canonical_json(body).encode())
    if observed != expected:
        raise ArtifactError(f"{label} self-hash changed")
    return observed


def _tree_identity(path: Path) -> dict[str, Any]:
    if not path.is_dir() or path.is_symlink():
        raise ArtifactError(f"required immutable tree is absent or unsafe: {path}")
    # Match the training/serving receipt identity exactly.  Unlike
    # ``parent_merge.checkpoint_manifest`` this includes merge_provenance.json;
    # that file is part of the externally frozen parent/output tree even though
    # it must be excluded from the provenance document's own internal manifest.
    manifest: dict[str, dict[str, Any]] = {}
    for candidate in sorted(path.rglob("*")):
        if candidate.is_symlink():
            raise ArtifactError(f"immutable tree contains a symlink: {candidate}")
        if candidate.is_file():
            manifest[candidate.relative_to(path).as_posix()] = {
                "size": candidate.stat().st_size,
                "sha256": sha256_file(candidate),
            }
        elif not candidate.is_dir():
            raise ArtifactError(f"immutable tree contains a special entry: {candidate}")
    if not manifest:
        raise ArtifactError(f"required immutable tree is empty: {path}")
    return {
        "path": str(path),
        "files": len(manifest),
        "bytes": sum(int(row["size"]) for row in manifest.values()),
        "tree_sha256": sha256_bytes(canonical_json(manifest).encode()),
    }


def _expected_sha(value: str, *, label: str, git: bool = False) -> str:
    pattern = FULL_GIT_SHA if git else SHA256
    if pattern.fullmatch(value) is None:
        raise ArtifactError(f"{label} is not an exact lowercase hash")
    return value


def _validate_training_receipt(
    path: Path,
    *,
    expected_file_sha256: str,
    expected_body_sha256: str,
    parent: Path,
    adapter: Path,
    expected_parent_tree_sha256: str,
    expected_adapter_tree_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if (
        expected_file_sha256 != EXPECTED_TRAINING_RECEIPT_FILE_SHA256
        or expected_body_sha256 != EXPECTED_TRAINING_RECEIPT_BODY_SHA256
        or expected_parent_tree_sha256 != EXPECTED_PARENT_TREE_SHA256
        or expected_adapter_tree_sha256 != EXPECTED_ADAPTER_TREE_SHA256
    ):
        raise ArtifactError("caller-supplied repair artifact identities differ from campaign pins")
    if sha256_file(path) != _expected_sha(
        expected_file_sha256, label="training receipt file SHA-256"
    ):
        raise ArtifactError("training receipt file bytes changed")
    receipt = read_json(path)
    if not isinstance(receipt, dict):
        raise ArtifactError("training receipt is not an object")
    _self_hash(receipt, "receipt_body_sha256", label="training receipt")
    if receipt.get("receipt_body_sha256") != _expected_sha(
        expected_body_sha256, label="training receipt body SHA-256"
    ):
        raise ArtifactError("training receipt body binding changed")
    candidate = receipt.get("candidate")
    if (
        receipt.get("schema") != TRAINING_RECEIPT_SCHEMA
        or receipt.get("status") != "ok"
        or receipt.get("source_step") != 23
        or receipt.get("final_step") != 24
        or receipt.get("optimizer_updates") != 1
        or receipt.get("assistant_tokens_only") is not True
        or receipt.get("scientific_label") != EXPECTED_SCIENTIFIC_LABEL
        or receipt.get("executor_git_sha") != EXPECTED_TRAINING_EXECUTOR_GIT_SHA
        or receipt.get("replay_executor_git_sha") != EXPECTED_REPLAY_EXECUTOR_GIT_SHA
        or receipt.get("replay_validator_git_sha") != EXPECTED_REPLAY_VALIDATOR_GIT_SHA
        or receipt.get("learning_rate") != EXPECTED_LEARNING_RATE
        or receipt.get("laptop_r00_used") is not True
        or receipt.get("laptop_r01_used") is not False
        or receipt.get("office_chair_used") is not False
        or not isinstance(candidate, dict)
        or candidate.get("update") != 24
        or candidate.get("name") != EXPECTED_CANDIDATE_NAME
        or Path(str(candidate.get("path", ""))).resolve() != adapter
    ):
        raise ArtifactError("training receipt does not attest the exact repair step-24 adapter")

    adapter_identity = _tree_identity(adapter)
    if (
        adapter_identity["tree_sha256"]
        != _expected_sha(expected_adapter_tree_sha256, label="adapter tree SHA-256")
        or candidate.get("tree_sha256") != adapter_identity["tree_sha256"]
        or candidate.get("files") != adapter_identity["files"]
        or candidate.get("bytes") != adapter_identity["bytes"]
        or candidate.get("adapter_config_sha256") != sha256_file(adapter / "adapter_config.json")
        or candidate.get("stable_marker_sha256") != sha256_file(adapter.parent / "STABLE")
    ):
        raise ArtifactError("repair step-24 adapter differs from its training receipt")

    parent_identity = _tree_identity(parent)
    if parent_identity["tree_sha256"] != _expected_sha(
        expected_parent_tree_sha256, label="parent tree SHA-256"
    ):
        raise ArtifactError("selected parent tree binding changed")
    adapter_config = read_json(adapter / "adapter_config.json")
    if (
        not isinstance(adapter_config, dict)
        or Path(str(adapter_config.get("base_model_name_or_path", ""))).resolve() != parent
    ):
        raise ArtifactError("repair step-24 adapter declares the wrong parent")
    return receipt, parent_identity, adapter_identity


def freeze_repair_step24_candidate_merge(
    *,
    training_receipt_path: str | Path,
    parent_model: str | Path,
    adapter_path: str | Path,
    output_dir: str | Path,
    merge_receipt_path: str | Path,
    expected_training_receipt_file_sha256: str,
    expected_training_receipt_body_sha256: str,
    expected_parent_tree_sha256: str,
    expected_adapter_tree_sha256: str,
    executor_git_sha: str,
    expected_container_image_digest: str,
    device: str = "cuda:0",
) -> dict[str, Any]:
    """Publish an attested prospective merge without consulting eval outcomes."""

    training_receipt_path = Path(training_receipt_path).resolve()
    parent = Path(parent_model).resolve()
    adapter = Path(adapter_path).resolve()
    output = Path(output_dir).resolve()
    merge_receipt_path = Path(merge_receipt_path).resolve()
    executor_git_sha = _expected_sha(executor_git_sha, label="executor git SHA", git=True)
    if IMAGE_DIGEST.fullmatch(expected_container_image_digest) is None:
        raise ArtifactError("container image must be bound by an immutable sha256 digest")
    runtime_contract = {
        "LAST_CONTAINER_IMAGE_DIGEST": expected_container_image_digest,
        "LAST_SOURCE_GIT_SHA": executor_git_sha,
        "LAST_ALLOCATED_GPUS": "1",
    }
    for key, expected in runtime_contract.items():
        if os.environ.get(key) != expected:
            raise ArtifactError(f"merge runtime provenance changed: {key}")
    if merge_receipt_path == output or output in merge_receipt_path.parents:
        raise ArtifactError("merge receipt must be a sibling outside the merged model tree")
    for immutable in (parent, adapter):
        if output == immutable or output in immutable.parents or immutable in output.parents:
            raise ArtifactError("merge output overlaps an immutable input tree")

    training, parent_identity, adapter_identity = _validate_training_receipt(
        training_receipt_path,
        expected_file_sha256=expected_training_receipt_file_sha256,
        expected_body_sha256=expected_training_receipt_body_sha256,
        parent=parent,
        adapter=adapter,
        expected_parent_tree_sha256=expected_parent_tree_sha256,
        expected_adapter_tree_sha256=expected_adapter_tree_sha256,
    )
    merge = verify_parent_aware_merge(parent, adapter, output, device=device)
    # Loading and folding 27B weights is long enough for a concurrent-input
    # mutation to matter.  Recompute every sealed input after the merge before
    # accepting any output or publishing a receipt.
    post_training, post_parent_identity, post_adapter_identity = _validate_training_receipt(
        training_receipt_path,
        expected_file_sha256=expected_training_receipt_file_sha256,
        expected_body_sha256=expected_training_receipt_body_sha256,
        parent=parent,
        adapter=adapter,
        expected_parent_tree_sha256=expected_parent_tree_sha256,
        expected_adapter_tree_sha256=expected_adapter_tree_sha256,
    )
    if (
        post_training != training
        or post_parent_identity != parent_identity
        or post_adapter_identity != adapter_identity
    ):
        raise ArtifactError("immutable repair inputs changed during the parent-aware merge")
    parent_provenance_sha256 = sha256_file(parent / "merge_provenance.json")
    if (
        merge.get("schema") != PARENT_MERGE_SCHEMA
        or merge.get("status") != "ok"
        or Path(str(merge.get("parent_model", ""))).resolve() != parent
        or Path(str(merge.get("adapter_path", ""))).resolve() != adapter
        or Path(str(merge.get("output_dir", ""))).resolve() != output
        or merge.get("parent_manifest") != checkpoint_manifest(parent)
        or merge.get("adapter_manifest") != checkpoint_manifest(adapter)
        or merge.get("merged_manifest") != checkpoint_manifest(output)
        or merge.get("parent_provenance_sha256") != parent_provenance_sha256
    ):
        raise ArtifactError("parent-aware merge provenance is incomplete or changed")

    merged_identity = _tree_identity(output)
    provenance_path = output / "merge_provenance.json"
    core = {
        "schema": MERGE_RECEIPT_SCHEMA,
        "status": "ok",
        "scientific_status": "prospective_candidate_not_selected_by_evaluation",
        "evaluation_outcomes_read": False,
        "candidate_selection_claimed": False,
        "executor_git_sha": executor_git_sha,
        "training_receipt": {
            "path": str(training_receipt_path),
            "file_sha256": sha256_file(training_receipt_path),
            "body_sha256": training["receipt_body_sha256"],
        },
        "source_step": 23,
        "final_step": 24,
        "optimizer_updates": 1,
        "parent": {
            **parent_identity,
            "merge_provenance_sha256": parent_provenance_sha256,
        },
        "adapter": {
            **adapter_identity,
            "adapter_config_sha256": sha256_file(adapter / "adapter_config.json"),
            "stable_marker_sha256": sha256_file(adapter.parent / "STABLE"),
        },
        "merged_model": {
            **merged_identity,
            "merge_provenance_sha256": sha256_file(provenance_path),
        },
        "behavioral_equivalence": merge.get("behavioral_equivalence"),
        "logit_metrics": merge.get("logit_metrics"),
        "runtime": merge.get("runtime"),
        "launcher_provenance": {
            key: os.environ.get(key)
            for key in (
                "LAST_CONTAINER_IMAGE_DIGEST",
                "LAST_SOURCE_GIT_SHA",
                "LAST_CLUSTER_PRIORITY_REQUEST",
                "LAST_CLUSTER_PRIORITY_CLASS",
                "LAST_ALLOCATED_GPUS",
            )
        },
    }
    receipt = {**core, "receipt_body_sha256": sha256_bytes(canonical_json(core).encode())}
    publish_json(merge_receipt_path, receipt)
    persisted = read_json(merge_receipt_path)
    if persisted != receipt:
        raise ArtifactError("published candidate merge receipt differs from verified bytes")
    _self_hash(receipt, "receipt_body_sha256", label="candidate merge receipt")
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--training-receipt", type=Path, required=True)
    parser.add_argument("--parent-model", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--merge-receipt", type=Path, required=True)
    parser.add_argument("--training-receipt-file-sha256", required=True)
    parser.add_argument("--training-receipt-body-sha256", required=True)
    parser.add_argument("--parent-tree-sha256", required=True)
    parser.add_argument("--adapter-tree-sha256", required=True)
    parser.add_argument("--executor-git-sha", required=True)
    parser.add_argument("--container-image-digest", required=True)
    parser.add_argument("--device", default="cuda:0")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        receipt = freeze_repair_step24_candidate_merge(
            training_receipt_path=arguments.training_receipt,
            parent_model=arguments.parent_model,
            adapter_path=arguments.adapter,
            output_dir=arguments.output_dir,
            merge_receipt_path=arguments.merge_receipt,
            expected_training_receipt_file_sha256=arguments.training_receipt_file_sha256,
            expected_training_receipt_body_sha256=arguments.training_receipt_body_sha256,
            expected_parent_tree_sha256=arguments.parent_tree_sha256,
            expected_adapter_tree_sha256=arguments.adapter_tree_sha256,
            executor_git_sha=arguments.executor_git_sha,
            expected_container_image_digest=arguments.container_image_digest,
            device=arguments.device,
        )
    except Exception as error:  # noqa: BLE001 - CLI emits a bounded failure record
        print(json.dumps({"status": "error", "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
