"""Fail-closed phase receipts for the CAVEAT-27B cluster campaign."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from .artifacts import ArtifactError, publish_json, read_json, sha256_file, tree_digest
from .config import Campaign


def _tree(path: Path) -> dict[str, Any]:
    files = [item for item in path.rglob("*") if item.is_file()]
    if not files:
        raise ArtifactError(f"checkpoint tree is empty: {path}")
    return {
        "path": str(path),
        "files": len(files),
        "sha256": tree_digest(files, path),
    }


def write_prep_receipt(campaign: Campaign, target: str | Path) -> dict[str, Any]:
    root = Path(target).resolve()
    smoke_path = root / "smoke/smoke_report.json"
    smoke = read_json(smoke_path)
    if (
        not isinstance(smoke, dict)
        or smoke.get("status") != "ok"
        or smoke.get("snapshot", {}).get("model_id") != campaign.model["model_id"]
        or smoke.get("snapshot", {}).get("revision") != campaign.model["revision"]
    ):
        raise ArtifactError("prep smoke report is absent or incompatible")
    corpus_path = root / "corpus/manifest.json"
    corpus = read_json(corpus_path)
    if corpus.get("campaign_digest") != campaign.digest or corpus.get("candidate_rows") != {
        "balanced": 4800,
        "protocol-heavy": 4800,
        "recovery-heavy": 4800,
    }:
        raise ArtifactError("prep corpus is absent or incompatible")
    candidates: dict[str, Any] = {}
    for name in ("balanced", "protocol-heavy", "recovery-heavy"):
        prime = root / "prime" / name / "manifest.json"
        plan = root / "configs" / name / "plan.json"
        prime_value = read_json(prime)
        plan_value = read_json(plan)
        if (
            prime_value.get("stage") != "targeted_sft"
            or prime_value.get("candidate") != name
            or prime_value.get("row_count") != 4800
            or plan_value.get("candidate") != name
            or plan_value.get("optimizer_updates") != 20
        ):
            raise ArtifactError(f"prep PRIME/config artifact drifted: {name}")
        candidates[name] = {
            "prime_manifest_sha256": sha256_file(prime),
            "plan_sha256": sha256_file(plan),
            "config_sha256": plan_value["config_sha256"],
        }
    receipt = {
        "schema": "caveat-27b.prep-receipt.v1",
        "status": "ok",
        "campaign_digest": campaign.digest,
        "source_git_sha": os.environ.get("LAST_SOURCE_GIT_SHA"),
        "container_image": os.environ.get("LAST_CONTAINER_IMAGE_DIGEST"),
        "smoke_report_sha256": sha256_file(smoke_path),
        "corpus_manifest_sha256": sha256_file(corpus_path),
        "candidates": candidates,
    }
    publish_json(root / "prep_receipt.json", receipt)
    return receipt


def write_candidate_receipt(
    campaign: Campaign, target: str | Path, candidate: str
) -> dict[str, Any]:
    if candidate not in {"balanced", "protocol-heavy", "recovery-heavy"}:
        raise ArtifactError(f"unknown SFT candidate: {candidate}")
    root = Path(target).resolve()
    prep = read_json(root / "prep_receipt.json")
    if prep.get("status") != "ok" or prep.get("campaign_digest") != campaign.digest:
        raise ArtifactError("candidate training requires the exact successful prep receipt")
    plan = read_json(root / "configs" / candidate / "plan.json")
    checkpoints = []
    for descriptor in plan["candidates"]:
        adapter = Path(descriptor["adapter"]).resolve()
        if not (adapter / "adapter_config.json").is_file():
            raise ArtifactError(f"candidate checkpoint is incomplete: {adapter}")
        checkpoints.append({"update": descriptor["update"], **_tree(adapter)})
    receipt = {
        "schema": "caveat-27b.sft-candidate-receipt.v1",
        "status": "ok",
        "campaign_digest": campaign.digest,
        "candidate": candidate,
        "prep_receipt_sha256": sha256_file(root / "prep_receipt.json"),
        "plan_sha256": sha256_file(root / "configs" / candidate / "plan.json"),
        "checkpoints": checkpoints,
    }
    publish_json(root / "receipts" / f"{candidate}.json", receipt)
    return receipt


def _refinement_adapter_parent(adapter: Path, parent: Path) -> None:
    config = read_json(adapter / "adapter_config.json")
    if not isinstance(config, dict):
        raise ArtifactError("refinement adapter configuration is not an object")
    declared = str(config.get("base_model_name_or_path", ""))
    if declared and Path(declared).resolve() != parent:
        raise ArtifactError("refinement adapter was not trained against the selected SFT parent")
    try:
        correct_lora_shape = config.get("r") == 64 and float(
            config.get("lora_alpha", -1)
        ) == 128.0
    except (TypeError, ValueError):
        correct_lora_shape = False
    targets = config.get("target_modules")
    if (
        not correct_lora_shape
        or not isinstance(targets, list)
        or not targets
        or not all(isinstance(target, str) and target for target in targets)
    ):
        raise ArtifactError("refinement adapter rank, alpha, or target modules drifted")
    weights = list(adapter.glob("adapter_model*.safetensors")) + list(
        adapter.glob("adapter_model*.bin")
    )
    if len(weights) != 1 or weights[0].stat().st_size == 0:
        raise ArtifactError(f"refinement adapter has missing or ambiguous weights: {adapter}")


def write_refinement_receipt(campaign: Campaign, target: str | Path) -> dict[str, Any]:
    """Attest the completed, predeclared refinement checkpoints without mutation."""

    root = Path(target).resolve()
    post_path = root / "post_sft_receipt.json"
    post = read_json(post_path)
    if (
        not isinstance(post, dict)
        or post.get("schema") != "caveat-27b.post-sft-receipt.v1"
        or post.get("status") != "ok"
        or post.get("campaign_digest") != campaign.digest
    ):
        raise ArtifactError("refinement training requires the exact successful post-SFT receipt")

    parent = (root / "selected/merged").resolve()
    parent_provenance = parent / "merge_provenance.json"
    if (
        not parent.is_dir()
        or post.get("merge_provenance_sha256") != sha256_file(parent_provenance)
    ):
        raise ArtifactError("selected SFT parent differs from the post-SFT receipt")

    plan_path = root / "refinement/config/plan.json"
    config_path = root / "refinement/config/refinement.toml"
    plan = read_json(plan_path)
    if (
        not isinstance(plan, dict)
        or plan.get("schema") != "caveat-27b.train-plan.v1"
        or plan.get("stage") != "refinement"
        or plan.get("candidate") is not None
        or plan.get("campaign_digest") != campaign.digest
        or Path(str(plan.get("model", ""))).resolve() != parent
        or plan.get("model_revision") != campaign.model["revision"]
        or plan.get("optimizer_updates") != 20
        or plan.get("config_sha256") != sha256_file(config_path)
        or post.get("refinement_config_sha256") != sha256_file(config_path)
    ):
        raise ArtifactError("refinement training plan/config is absent, incompatible, or drifted")
    try:
        parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ArtifactError("refinement training configuration is invalid") from exc
    expected_output = (root / "refinement/config/prime_output").resolve()
    if (
        parsed.get("max_steps") != 20
        or Path(str(parsed.get("output_dir", ""))).resolve() != expected_output
        or Path(str(parsed.get("model", {}).get("name", ""))).resolve() != parent
        or parsed.get("deployment", {}).get("num_gpus") not in {4, 8}
        or parsed.get("deployment", {}).get("gpus_per_node")
        != parsed.get("deployment", {}).get("num_gpus")
    ):
        raise ArtifactError("refinement execution topology or parent drifted")

    dataset_manifest = root / "refinement/prime/manifest.json"
    if (
        plan.get("dataset_manifest_sha256") != sha256_file(dataset_manifest)
        or post.get("prime_manifest_sha256") != sha256_file(dataset_manifest)
    ):
        raise ArtifactError("refinement dataset differs from the post-SFT receipt")

    descriptors = plan.get("candidates")
    if not isinstance(descriptors, list) or [row.get("update") for row in descriptors] != [
        5,
        10,
        20,
    ]:
        raise ArtifactError("refinement checkpoint schedule drifted")
    checkpoints = []
    for descriptor in descriptors:
        update = int(descriptor["update"])
        adapter = Path(str(descriptor.get("adapter", ""))).resolve()
        expected_adapter = expected_output / f"weights/step_{update}/lora_adapters"
        if adapter != expected_adapter or not adapter.is_dir():
            raise ArtifactError(f"refinement checkpoint is absent or misplaced: step {update}")
        _refinement_adapter_parent(adapter, parent)
        stable = adapter.parent / "STABLE"
        stable_sha256 = sha256_file(stable)
        stable_mtime = stable.stat().st_mtime_ns
        adapter_files = [item for item in adapter.rglob("*") if item.is_file()]
        if any(item.stat().st_mtime_ns > stable_mtime for item in adapter_files):
            raise ArtifactError(f"refinement checkpoint changed after STABLE: step {update}")
        checkpoints.append(
            {"update": update, "stable_marker_sha256": stable_sha256, **_tree(adapter)}
        )

    receipt = {
        "schema": "caveat-27b.refinement-training-receipt.v1",
        "status": "ok",
        "campaign_digest": campaign.digest,
        "optimizer_updates": 20,
        "num_gpus": parsed["deployment"]["num_gpus"],
        "parent_model": str(parent),
        "parent_merge_provenance_sha256": sha256_file(parent_provenance),
        "post_sft_receipt_sha256": sha256_file(post_path),
        "plan_sha256": sha256_file(plan_path),
        "config_sha256": sha256_file(config_path),
        "dataset_manifest_sha256": sha256_file(dataset_manifest),
        "execution_source_git_sha": os.environ.get("CAVEAT_27B_EXECUTION_SOURCE_GIT_SHA"),
        "checkpoints": checkpoints,
    }
    publish_json(root / "refinement/training_receipt.json", receipt)
    return receipt
