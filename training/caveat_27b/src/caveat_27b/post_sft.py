"""Selection, verified merge, and bounded contract refinement preparation."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

from .artifacts import ArtifactError, publish_json, read_json, sha256_file
from .config import Campaign
from .contract_refinement import collect_contract_rollouts, materialize_contract_refinement
from .prime_data import materialize_prime_dataset
from .selection import select_sft_checkpoint
from .train_configs import generate_sft_configs


def prepare_refinement(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    smoke_model_config: str | Path,
    prime_root: str | Path,
    selection_concurrency: int = 32,
    num_gpus: int = 8,
) -> dict[str, Any]:
    """Select an SFT adapter, merge it, collect ReST data, and emit its TOML."""

    root = Path(campaign_root).resolve()
    prep = read_json(root / "prep_receipt.json")
    if (
        not isinstance(prep, dict)
        or prep.get("status") != "ok"
        or prep.get("campaign_digest") != campaign.digest
        or prep.get("source_git_sha") != root.name
    ):
        raise ArtifactError("prep receipt does not attest the reused campaign artifact root")
    for candidate in ("balanced", "protocol-heavy", "recovery-heavy"):
        receipt = read_json(root / "receipts" / f"{candidate}.json")
        if (
            not isinstance(receipt, dict)
            or receipt.get("status") != "ok"
            or receipt.get("campaign_digest") != campaign.digest
        ):
            raise ArtifactError(f"SFT candidate receipt is absent or incompatible: {candidate}")
    selection = select_sft_checkpoint(
        campaign,
        campaign_root=root,
        output_dir=root / "selection",
        concurrency=selection_concurrency,
        num_gpus=num_gpus,
    )
    adapter = Path(selection["selected"]["adapter"]).resolve()
    merged = root / "selected/merged"
    merge_module = importlib.import_module("harness_distill.model_merge")
    merge = merge_module.verify_and_merge(
        smoke_model_config,
        adapter,
        merged,
        role="primary",
        device="cuda:0",
    )
    if merge.get("status") != "ok":
        raise ArtifactError("selected adapter merge did not pass numerical verification")
    rollout_manifest_path = root / "refinement/rollouts/manifest.json"
    if rollout_manifest_path.is_file():
        # The collector's strict resume validation does not need a live server.
        # Keep the endpoint identity identical to the original local server so
        # the manifest remains fully bound, but avoid reloading 54 GB of weights
        # after an unrelated downstream materialization failure.
        os.environ["OPENAI_API_KEY"] = "EMPTY"
        rollout_manifest = collect_contract_rollouts(
            campaign,
            split_manifest_path=root / "corpus/splits/manifest.json",
            tasks_path=root / "corpus/raw/refinement_contract_tasks.jsonl",
            selected_checkpoint_manifest=root / "selection/selected_checkpoint.json",
            base_url="http://127.0.0.1:8000/v1",
            model="caveat-27b-selected-sft",
            output_dir=root / "refinement/rollouts",
            api_key_env="OPENAI_API_KEY",
            concurrency=32,
            timeout_seconds=600,
        )
    else:
        os.environ["OPENAI_API_KEY"] = "EMPTY"
        serving = importlib.import_module("harness_distill.serving")
        with serving.VllmServer(
            model_path=merged,
            served_model_name="caveat-27b-selected-sft",
            output_dir=root / "refinement/server",
            gpu_ids=tuple(range(num_gpus)),
            max_model_len=32768,
            data_parallel=True,
        ) as server:
            rollout_manifest = collect_contract_rollouts(
                campaign,
                split_manifest_path=root / "corpus/splits/manifest.json",
                tasks_path=root / "corpus/raw/refinement_contract_tasks.jsonl",
                selected_checkpoint_manifest=root / "selection/selected_checkpoint.json",
                base_url=server.base_url + "/v1",
                model="caveat-27b-selected-sft",
                output_dir=root / "refinement/rollouts",
                api_key_env="OPENAI_API_KEY",
                concurrency=32,
                timeout_seconds=600,
            )
    refinement = materialize_contract_refinement(
        campaign,
        split_manifest_path=root / "corpus/splits/manifest.json",
        tasks_path=root / "corpus/raw/refinement_contract_tasks.jsonl",
        rollout_manifest_path=root / "refinement/rollouts/manifest.json",
        rehearsal_path=root / "corpus/raw/contract.jsonl",
        output_dir=root / "refinement/data",
    )
    prime = materialize_prime_dataset(
        campaign,
        source_jsonl=root / "refinement/data/train.jsonl",
        source_manifest=root / "refinement/data/manifest.json",
        smoke_report=root / "smoke/smoke_report.json",
        stage="refinement",
        output_dir=root / "refinement/prime",
        prime_root=prime_root,
    )
    plan = generate_sft_configs(
        campaign,
        stage="refinement",
        smoke_report=root / "smoke/smoke_report.json",
        parent_model=merged,
        dataset_dir=root / "refinement/prime",
        output_dir=root / "refinement/config",
        num_gpus=num_gpus,
    )
    result = {
        "schema": "caveat-27b.post-sft-receipt.v1",
        "status": "ok",
        "campaign_digest": campaign.digest,
        "artifact_source_git_sha": prep["source_git_sha"],
        "execution_source_git_sha": os.environ.get("CAVEAT_27B_EXECUTION_SOURCE_GIT_SHA"),
        "num_gpus": num_gpus,
        "selection_manifest_sha256": sha256_file(root / "selection/selected_checkpoint.json"),
        "selected": selection["selected"],
        "merge_provenance_sha256": sha256_file(merged / "merge_provenance.json"),
        "rollout_manifest_sha256": sha256_file(root / "refinement/rollouts/manifest.json"),
        "refinement_manifest_sha256": sha256_file(root / "refinement/data/manifest.json"),
        "prime_manifest_sha256": sha256_file(root / "refinement/prime/manifest.json"),
        "refinement_config_sha256": plan["config_sha256"],
        "episodes": rollout_manifest["episodes"],
        "refinement_rows": refinement["output"]["rows"],
        "prime_rows": prime["row_count"],
    }
    publish_json(root / "post_sft_receipt.json", result)
    return result
