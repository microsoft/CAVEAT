"""One-candidate corrective continuation from the exact refinement step 20.

This stage is deliberately outcome blind: it uses only the frozen procedural
training split, produces exactly one step-26 adapter, and performs no Amazon
evaluation or checkpoint selection.  The curriculum targets two observable
weaknesses found before this stage was designed: browser checkpoints were rare,
and source URLs/objective units were not consistently present in model-visible
supervision.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import tomllib
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    canonical_json,
    publish_bytes,
    publish_json,
    publish_jsonl,
    read_json,
    read_jsonl,
    sha256_bytes,
    sha256_file,
)
from .browser_action_continuation import (
    _tree_identity,
    _verified_source,
)
from .browser_action_continuation_receipt import _verify_adapter
from .browser_action_curriculum import (
    _STATE_MARKER,
    _action_rows,
    _curriculum_leakage_audit,
    _output_stack,
    _public_state,
    _stable_key,
    _system_with_contract,
)
from .config import Campaign
from .prime_data import materialize_prime_dataset
from .sft_data import validate_sft_sample
from .splits import load_split_manifest
from .train_configs import _array, _quoted, _smoke_targets

FIXED_V5_CURRICULUM_SCHEMA = "harness-posttrain.browser-action-fixed-v5-curriculum.v1"
FIXED_V5_PLAN_SCHEMA = "harness-posttrain.browser-action-fixed-v5-plan.v1"
FIXED_V5_RECEIPT_SCHEMA = "harness-posttrain.browser-action-fixed-v5-training-receipt.v1"

_SOURCE_STEP = 20
_FINAL_STEP = 26
_NEW_UPDATES = 6
_LEARNING_RATE = 1.0e-5
_ACTION_TASKS = 128
_RETENTION_PER_KIND = 14
_CONTRACT_TASKS = 256
_CONTRACT_REPEATS = 4
_GIT_SHA = re.compile(r"[0-9a-f]{40}")


def _with_visible_source_urls(row: Mapping[str, Any]) -> dict[str, Any]:
    """Add plausible visible product URLs before action examples are rendered."""

    result = copy.deepcopy(dict(row))
    prefix, state = _public_state(result)
    catalog = state["catalog"]
    for item in catalog:
        if not isinstance(item, dict) or not isinstance(item.get("item_id"), str):
            raise ArtifactError("procedural catalog item lacks a stable item ID")
        item["source_url"] = f"https://shop.local/products/{item['item_id']}"
    user = result["messages"][-2]
    user["content"] = f"{prefix}{_STATE_MARKER}{canonical_json(state)}"
    return result


def _contract_has_explicit_units(row: Mapping[str, Any]) -> bool:
    messages = row.get("messages")
    assistant = messages[-1] if isinstance(messages, list) and messages else None
    content = assistant.get("content") if isinstance(assistant, Mapping) else None
    try:
        contract = json.loads(content) if isinstance(content, str) else None
    except json.JSONDecodeError:
        return False
    objectives = contract.get("objectives") if isinstance(contract, dict) else None
    return bool(objectives) and all(
        isinstance(value, dict)
        and isinstance(value.get("unit"), str)
        and bool(value["unit"].strip())
        for value in objectives
    )


def _materialize_curriculum(
    campaign: Campaign, *, campaign_root: Path, output: Path
) -> dict[str, Any]:
    split_path = campaign_root / "corpus/splits/manifest.json"
    rehearsal_path = campaign_root / "corpus/raw/rehearsal.jsonl"
    contract_path = campaign_root / "corpus/raw/contract.jsonl"
    split_manifest, membership = load_split_manifest(campaign, split_path)

    rehearsals = read_jsonl(rehearsal_path)
    for index, row in enumerate(rehearsals, 1):
        validate_sft_sample(
            row, membership=membership, expected_stage="rehearsal", row_number=index
        )
    if len(rehearsals) < _ACTION_TASKS:
        raise ArtifactError("not enough procedural rehearsal tasks for fixed-v5")

    contracts = read_jsonl(contract_path)
    canonical_contracts: dict[str, dict[str, Any]] = {}
    for row in contracts:
        task_id = row.get("task_id")
        if not isinstance(task_id, str):
            raise ArtifactError("contract replay lacks a task ID")
        if str(row.get("sample_id", "")).endswith(":json-only"):
            if task_id in canonical_contracts:
                raise ArtifactError("duplicate JSON-only contract replay")
            canonical_contracts[task_id] = row

    seed = int(campaign.campaign["seed"])
    selected = sorted(rehearsals, key=lambda row: _stable_key(seed + 31, str(row["task_id"])))[
        :_ACTION_TASKS
    ]
    system, output_model = _output_stack()
    by_kind: dict[str, list[dict[str, Any]]] = {
        "checkpoint-complete": [],
        "repair-rejected": [],
        "continue-incomplete": [],
        "act-after-approval": [],
    }
    for source in selected:
        contract = canonical_contracts.get(str(source["task_id"]))
        if contract is None:
            raise ArtifactError("selected rehearsal lacks a contract replay")
        source = _with_visible_source_urls(source)
        row_system = _system_with_contract(system, contract)
        for kind, candidate in _action_rows(source, row_system, output_model):
            by_kind[kind].append(
                validate_sft_sample(
                    candidate,
                    membership=membership,
                    expected_stage=kind,
                    row_number=len(by_kind[kind]) + 1,
                )
            )

    rows = [*by_kind["checkpoint-complete"], *by_kind["repair-rejected"]]
    for kind in ("continue-incomplete", "act-after-approval"):
        rows.extend(
            sorted(
                by_kind[kind],
                key=lambda row: _stable_key(seed + 32, str(row["metadata"]["sample_id"])),
            )[:_RETENTION_PER_KIND]
        )

    unit_contracts = [
        row for row in canonical_contracts.values() if _contract_has_explicit_units(row)
    ]
    if len(unit_contracts) < _CONTRACT_TASKS:
        raise ArtifactError("not enough explicit-unit contracts for fixed-v5 replay")
    unit_contracts = sorted(
        unit_contracts, key=lambda row: _stable_key(seed + 33, str(row["task_id"]))
    )[:_CONTRACT_TASKS]
    for source in unit_contracts:
        normalized = validate_sft_sample(
            source,
            membership=membership,
            expected_stage="contract-replay",
            row_number=len(rows) + 1,
        )
        for repeat in range(_CONTRACT_REPEATS):
            replay = copy.deepcopy(normalized)
            replay["metadata"]["sample_id"] = (
                f"{normalized['metadata']['sample_id']}:fixed-v5-repeat-{repeat + 1}"
            )
            rows.append(replay)

    sample_ids = [str(row["metadata"]["sample_id"]) for row in rows]
    if len(sample_ids) != len(set(sample_ids)):
        raise ArtifactError("fixed-v5 curriculum contains duplicate sample IDs")
    leakage = _curriculum_leakage_audit(rows)
    rows.sort(key=lambda row: _stable_key(seed + 34, str(row["metadata"]["sample_id"])))
    counts = Counter(str(row["metadata"]["stage"]) for row in rows)
    data_path = publish_jsonl(output / "train.jsonl", rows)
    body = {
        "schema": FIXED_V5_CURRICULUM_SCHEMA,
        "stage": "refinement",
        "method": "outcome_blind_observable_field_corrective_sft",
        "campaign_digest": campaign.digest,
        "split_manifest_sha256": sha256_file(split_path),
        "split_manifest_body_sha256": split_manifest["manifest_body_sha256"],
        "inputs": {
            "rehearsal": {
                "path": str(rehearsal_path.resolve()),
                "sha256": sha256_file(rehearsal_path),
            },
            "contract_replay": {
                "path": str(contract_path.resolve()),
                "sha256": sha256_file(contract_path),
            },
        },
        "policy": {
            "action_tasks": _ACTION_TASKS,
            "checkpoint_complete_rows": _ACTION_TASKS,
            "repair_rejected_rows": _ACTION_TASKS,
            "retention_rows_per_kind": _RETENTION_PER_KIND,
            "contract_tasks_with_explicit_units": _CONTRACT_TASKS,
            "contract_repeats": _CONTRACT_REPEATS,
            "model_visible_source_urls": True,
            "amazon_outcomes_consulted": False,
            "candidate_sweep": False,
        },
        "counts": dict(sorted(counts.items())),
        "assistant_wire_format": "browser-use AgentOutput.action JSON",
        "native_function_call_targets": 0,
        "heldout_amazon_scenarios_present": False,
        "leakage_audit": leakage,
        "output": {"path": data_path.name, "sha256": sha256_file(data_path), "rows": len(rows)},
    }
    manifest = dict(body)
    manifest["manifest_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    publish_json(output / "manifest.json", manifest)
    return manifest


def _fixed_toml(
    *,
    model: Path,
    dataset: Path,
    output: Path,
    targets: list[str],
    settings: Mapping[str, Any],
    seed: int,
    num_gpus: int,
) -> bytes:
    lines = [
        "# PRIME-RL 0.7.0; generated by harness-posttrain",
        "# stage = browser_action_fixed_v5",
        "# exactly one predeclared candidate: step_26",
        f"max_steps = {_FINAL_STEP}",
        f"output_dir = {_quoted(output)}",
        "clean_output_dir = false",
        'matmul_precision = "high"',
        'loss_impl = "liger_fused"',
        "",
        "[env_vars]",
        'FLA_TILELANG = "0"',
        'WANDB_MODE = "disabled"',
        "",
        "[deployment]",
        'type = "single_node"',
        f"num_gpus = {num_gpus}",
        f"gpus_per_node = {num_gpus}",
        "",
        "[model]",
        f"name = {_quoted(model)}",
        f"seq_len = {int(settings['sequence_length'])}",
        'impl = "hf"',
        'attn = "flash_attention_2"',
        'optimization_dtype = "bfloat16"',
        'reduce_dtype = "bfloat16"',
        "cp = 2",
        'cp_style = "ulysses"',
        "",
        "[model.ac]",
        'mode = "full"',
        "freq = 1",
        "",
        "[model.lora]",
        f"rank = {int(settings['lora_rank'])}",
        f"alpha = {float(settings['lora_alpha'])}",
        f"dropout = {float(settings['lora_dropout'])}",
        f"target_modules = {_array(targets)}",
        "modules_to_save = []",
        "",
        "[renderer]",
        'name = "qwen3.5"',
        "enable_thinking = true",
        "",
        "[data]",
        'type = "sft"',
        f"name = {_quoted(dataset)}",
        f"batch_size = {int(settings['global_batch_size'])}",
        f"seq_len = {int(settings['sequence_length'])}",
        "micro_batch_size = 1",
        'pack_function = "cat"',
        "shuffle = true",
        f"seed = {seed}",
        "",
        "[data.loss_mask]",
        "system = false",
        "user = false",
        "assistant = true",
        "tool = false",
        "",
        "[optim]",
        'type = "adamw"',
        f"lr = {_LEARNING_RATE}",
        "weight_decay = 0.01",
        f"max_norm = {float(settings.get('maximum_gradient_norm', 1.0))}",
        "",
        "[scheduler]",
        # The reviewed PRIME scheduler is cosine; making its floor equal to
        # its base LR realizes an exactly constant schedule over all six steps.
        'type = "cosine"',
        "warmup_steps = 0",
        "min_lr = 1.0e-5",
        "",
        "[ckpt]",
        f"interval = {_NEW_UPDATES}",
        f"resume_step = {_SOURCE_STEP}",
        "keep_last = 2",
        "skip_optimizer = true",
        "skip_scheduler = true",
        "skip_dataloader = true",
        "skip_progress = false",
        "",
        "[ckpt.weights]",
        "save_sharded = true",
        'save_format = "safetensors"',
        "save_adapter_separately = true",
    ]
    payload = ("\n".join(lines).rstrip() + "\n").encode()
    parsed = tomllib.loads(payload.decode())
    if parsed["scheduler"] != {"type": "cosine", "warmup_steps": 0, "min_lr": _LEARNING_RATE}:
        raise AssertionError("fixed-v5 constant learning-rate policy drifted")
    return payload


def _token_mix_audit(stage: Path) -> dict[str, Any]:
    """Bind and gate the exact rendered-token mixture, not misleading row counts."""

    rows = read_jsonl(stage / "curriculum/train.jsonl")
    audit_rows = read_jsonl(stage / "prime/token_audit_rows.jsonl")
    if len(rows) != len(audit_rows):
        raise ArtifactError("fixed-v5 token audit row count differs from curriculum")
    totals: Counter[str] = Counter()
    for index, (row, audit) in enumerate(zip(rows, audit_rows, strict=True), 1):
        if audit.get("line") != index or type(audit.get("rendered_tokens")) is not int:
            raise ArtifactError("fixed-v5 PRIME token audit ordering drifted")
        stage_name = row.get("metadata", {}).get("stage")
        if not isinstance(stage_name, str):
            raise ArtifactError("fixed-v5 row lacks stage metadata")
        totals[stage_name] += int(audit["rendered_tokens"])
    total = sum(totals.values())
    targeted = totals["checkpoint-complete"] + totals["repair-rejected"]
    retention = totals["continue-incomplete"] + totals["act-after-approval"]
    contract = totals["contract-replay"]
    fractions = {
        "checkpoint_and_repair": targeted / total,
        "retention": retention / total,
        "explicit_unit_contract_replay": contract / total,
    }
    if (
        fractions["checkpoint_and_repair"] < 0.65
        or fractions["retention"] < 0.01
        or not 0.03 <= fractions["explicit_unit_contract_replay"] <= 0.25
    ):
        raise ArtifactError("fixed-v5 exact rendered-token mixture is outside frozen bounds")
    return {
        "rendered_tokens": total,
        "rendered_tokens_by_stage": dict(sorted(totals.items())),
        "fractions": fractions,
        "frozen_bounds": {
            "checkpoint_and_repair_minimum": 0.65,
            "retention_minimum": 0.01,
            "explicit_unit_contract_replay_minimum": 0.03,
            "explicit_unit_contract_replay_maximum": 0.25,
        },
        "gate_passed": True,
    }


def prepare_browser_action_fixed_v5(
    campaign: Campaign, *, campaign_root: str | Path, output_dir: str | Path, num_gpus: int = 4
) -> dict[str, Any]:
    """Create the frozen one-candidate curriculum, PRIME data, and training plan."""

    if num_gpus != 4:
        raise ArtifactError("fixed-v5 uses the reviewed four-GPU topology")
    root = Path(campaign_root).resolve()
    campaign_artifact_sha = _git_sha(root.name, "campaign artifact source Git SHA")
    output = Path(output_dir).absolute()
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise ArtifactError("fixed-v5 output must be a new empty non-symlink directory")
    output = output.resolve()
    if output.parent != root / "browser_action_fixed_v5" or _GIT_SHA.fullmatch(output.name) is None:
        raise ArtifactError("fixed-v5 output must be browser_action_fixed_v5/<artifact SHA>")

    curriculum = _materialize_curriculum(campaign, campaign_root=root, output=output / "curriculum")
    prime = materialize_prime_dataset(
        campaign,
        source_jsonl=output / "curriculum/train.jsonl",
        source_manifest=output / "curriculum/manifest.json",
        smoke_report=root / "smoke/smoke_report.json",
        stage="refinement",
        output_dir=output / "prime",
        prime_root=os.environ.get("HPT_PRIME_ROOT", "/opt/prime-rl"),
    )
    token_mix = _token_mix_audit(output)
    source = _verified_source(campaign, root)
    prep = read_json(root / "prep_receipt.json")
    post = read_json(root / "post_sft_receipt.json")
    if (
        not isinstance(prep, dict)
        or prep.get("source_git_sha") != campaign_artifact_sha
        or not isinstance(post, dict)
        or post.get("artifact_source_git_sha") != campaign_artifact_sha
    ):
        raise ArtifactError("fixed-v5 original campaign source identity drifted")
    _snapshot, targets, revision = _smoke_targets(root / "smoke/smoke_report.json", campaign)
    if revision and revision != campaign.model["revision"]:
        raise ArtifactError("smoke report revision differs from pinned model")

    training = output / "training"
    destination = training / "prime_output/checkpoints/step_20"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source["source_dcp"], destination, copy_function=shutil.copy2)
    copied = _tree_identity(destination)
    source_after = _tree_identity(source["source_dcp"])
    for key in ("files", "bytes", "tree_sha256"):
        if (
            copied[key] != source["source_dcp_identity"][key]
            or source_after[key] != source["source_dcp_identity"][key]
        ):
            raise ArtifactError("step-20 DCP changed during fixed-v5 copy")

    config_path = publish_bytes(
        training / "browser_action_fixed_v5.toml",
        _fixed_toml(
            model=source["parent"],
            dataset=(output / "prime").resolve(),
            output=(training / "prime_output").resolve(),
            targets=targets,
            settings=campaign.campaign["refinement"],
            seed=int(campaign.campaign["seed"]) + 35,
            num_gpus=num_gpus,
        ),
    )
    plan = {
        "schema": FIXED_V5_PLAN_SCHEMA,
        "status": "prepared",
        "stage": "browser_action_fixed_v5",
        "campaign_digest": campaign.digest,
        "campaign_artifact_source_git_sha": campaign_artifact_sha,
        "artifact_source_git_sha": output.name,
        "parent_model": str(source["parent"]),
        "parent_merge_provenance_sha256": source["parent_merge_provenance_sha256"],
        "curriculum_manifest_sha256": sha256_file(output / "curriculum/manifest.json"),
        "curriculum_data_sha256": sha256_file(output / "curriculum/train.jsonl"),
        "prime_manifest_sha256": sha256_file(output / "prime/manifest.json"),
        "prime_parquet_sha256": sha256_file(output / "prime/train.parquet"),
        "source_step20_adapter": source["adapter_identity"],
        "source_step20_adapter_receipt_tree_sha256": source["adapter_receipt_tree_sha256"],
        "source_step20_dcp": source["source_dcp_identity"],
        "copied_step20_dcp": copied,
        "config": str(config_path),
        "config_sha256": sha256_file(config_path),
        "candidate": {
            "name": "step26",
            "update": _FINAL_STEP,
            "path": str((training / "prime_output/weights/step_26/lora_adapters").resolve()),
        },
        "selection_performed": False,
        "amazon_outcomes_consulted": False,
        "training_policy": {
            "optimizer": "adamw",
            "learning_rate": _LEARNING_RATE,
            "scheduler": "cosine_with_floor_equal_to_base_lr",
            "warmup_steps": 0,
            "weight_decay": 0.01,
            "maximum_gradient_norm": float(
                campaign.campaign["refinement"].get("maximum_gradient_norm", 1.0)
            ),
            "seed": int(campaign.campaign["seed"]) + 35,
            "sequence_length": int(campaign.campaign["refinement"]["sequence_length"]),
            "global_batch_size": int(campaign.campaign["refinement"]["global_batch_size"]),
            "resume_step": _SOURCE_STEP,
            "optimizer_updates": _FINAL_STEP,
            "new_optimizer_updates": _NEW_UPDATES,
            "checkpoint_updates": [_FINAL_STEP],
            "restore_model": True,
            "restore_progress": True,
            "restore_optimizer": False,
            "restore_scheduler": False,
            "restore_dataloader": False,
            "num_gpus": num_gpus,
            "lora_rank": int(campaign.campaign["refinement"]["lora_rank"]),
            "lora_alpha": float(campaign.campaign["refinement"]["lora_alpha"]),
            "lora_dropout": float(campaign.campaign["refinement"]["lora_dropout"]),
        },
        "curriculum": curriculum,
        "prime": {"rows": prime["row_count"], "token_audit": prime["token_audit"]},
        "token_mix_audit": token_mix,
        "original_refinement_mutated": False,
    }
    plan_path = publish_json(training / "plan.json", plan)
    return {**plan, "plan_sha256": sha256_file(plan_path)}


def _git_sha(value: str | None, label: str) -> str:
    if value is None or _GIT_SHA.fullmatch(value) is None:
        raise ArtifactError(f"{label} must be a 40-character lowercase Git SHA")
    return value


def write_browser_action_fixed_v5_receipt(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    stage_dir: str | Path,
    artifact_source_git_sha: str | None,
    execution_source_git_sha: str | None,
) -> dict[str, Any]:
    """Attest the sole trained candidate and recheck every frozen source input."""

    root = Path(campaign_root).resolve()
    stage = Path(stage_dir).resolve()
    artifact_sha = _git_sha(artifact_source_git_sha, "artifact source Git SHA")
    execution_sha = _git_sha(execution_source_git_sha, "execution source Git SHA")
    campaign_artifact_sha = _git_sha(root.name, "campaign artifact source Git SHA")
    if stage != root / "browser_action_fixed_v5" / artifact_sha:
        raise ArtifactError("fixed-v5 stage path differs from artifact source SHA")
    training = stage / "training"
    plan_path = training / "plan.json"
    config_path = training / "browser_action_fixed_v5.toml"
    plan = read_json(plan_path)
    if (
        not isinstance(plan, dict)
        or plan.get("schema") != FIXED_V5_PLAN_SCHEMA
        or plan.get("status") != "prepared"
    ):
        raise ArtifactError("fixed-v5 plan is absent or incompatible")
    if (
        plan.get("campaign_digest") != campaign.digest
        or plan.get("campaign_artifact_source_git_sha") != campaign_artifact_sha
        or plan.get("artifact_source_git_sha") != artifact_sha
    ):
        raise ArtifactError("fixed-v5 plan source binding drifted")
    if (
        plan.get("config_sha256") != sha256_file(config_path)
        or plan.get("selection_performed") is not False
    ):
        raise ArtifactError("fixed-v5 plan/config or no-selection policy drifted")

    source = _verified_source(campaign, root)
    for field, value in (
        ("parent_model", str(source["parent"])),
        ("parent_merge_provenance_sha256", source["parent_merge_provenance_sha256"]),
        ("source_step20_adapter_receipt_tree_sha256", source["adapter_receipt_tree_sha256"]),
    ):
        if plan.get(field) != value:
            raise ArtifactError(f"fixed-v5 plan source binding drifted: {field}")
    for name, actual, expected in (
        ("source adapter", source["adapter_identity"], plan.get("source_step20_adapter")),
        ("source DCP", source["source_dcp_identity"], plan.get("source_step20_dcp")),
        (
            "copied DCP",
            _tree_identity(training / "prime_output/checkpoints/step_20"),
            plan.get("copied_step20_dcp"),
        ),
    ):
        if not isinstance(expected, Mapping) or any(
            actual.get(key) != expected.get(key) for key in ("files", "bytes", "tree_sha256")
        ):
            raise ArtifactError(f"fixed-v5 {name} identity changed")

    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    policy = plan.get("training_policy")
    if (
        not isinstance(policy, dict)
        or config.get("max_steps") != _FINAL_STEP
        or config.get("optim", {}).get("lr") != _LEARNING_RATE
        or config.get("scheduler")
        != {"type": "cosine", "warmup_steps": 0, "min_lr": _LEARNING_RATE}
    ):
        raise ArtifactError("fixed-v5 training policy drifted")
    for path, expected_hash, label in (
        (
            stage / "curriculum/manifest.json",
            plan.get("curriculum_manifest_sha256"),
            "curriculum manifest",
        ),
        (stage / "curriculum/train.jsonl", plan.get("curriculum_data_sha256"), "curriculum data"),
        (stage / "prime/manifest.json", plan.get("prime_manifest_sha256"), "PRIME manifest"),
        (stage / "prime/train.parquet", plan.get("prime_parquet_sha256"), "PRIME parquet"),
    ):
        if expected_hash != sha256_file(path):
            raise ArtifactError(f"fixed-v5 {label} changed")

    adapter = training / "prime_output/weights/step_26/lora_adapters"
    verified_adapter = _verify_adapter(
        adapter=adapter,
        parent=source["parent"],
        targets=list(config["model"]["lora"]["target_modules"]),
        update=_FINAL_STEP,
    )
    candidate = {"name": "step26", **verified_adapter}
    body = {
        "schema": FIXED_V5_RECEIPT_SCHEMA,
        "status": "ok",
        "stage": "browser_action_fixed_v5",
        "campaign_digest": campaign.digest,
        "campaign_artifact_source_git_sha": campaign_artifact_sha,
        "artifact_source_git_sha": artifact_sha,
        "execution_source_git_sha": execution_sha,
        "parent_model": str(source["parent"]),
        "parent_merge_provenance_sha256": source["parent_merge_provenance_sha256"],
        "plan_path": str(plan_path),
        "plan_sha256": sha256_file(plan_path),
        "config_sha256": sha256_file(config_path),
        "curriculum_manifest_sha256": sha256_file(stage / "curriculum/manifest.json"),
        "curriculum_data_sha256": sha256_file(stage / "curriculum/train.jsonl"),
        "prime_manifest_sha256": sha256_file(stage / "prime/manifest.json"),
        "prime_parquet_sha256": sha256_file(stage / "prime/train.parquet"),
        "source_step20_adapter": source["adapter_identity"],
        "source_step20_adapter_receipt_tree_sha256": source["adapter_receipt_tree_sha256"],
        "source_step20_dcp": source["source_dcp_identity"],
        "copied_step20_dcp": _tree_identity(training / "prime_output/checkpoints/step_20"),
        "optimizer_updates": _FINAL_STEP,
        "new_optimizer_updates": _NEW_UPDATES,
        "checkpoint_updates": [_FINAL_STEP],
        "training_policy": policy,
        "candidate": candidate,
        "selection_performed": False,
        "amazon_outcomes_consulted": False,
        "original_refinement_unchanged_after_training": True,
    }
    receipt = dict(body)
    receipt["receipt_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    publish_json(training / "training_receipt.json", receipt)
    return receipt
