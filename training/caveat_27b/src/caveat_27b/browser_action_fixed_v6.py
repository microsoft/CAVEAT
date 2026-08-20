"""Fast, fixed corrective continuation for real browser search and checkout actions.

Unlike fixed-v5, this curriculum never presents an omniscient catalog as the
dominant training state.  It teaches short transitions that occur in the live
browser loop: continue a paginated search, open an unresolved literal href,
clean a cart, recover the recorded local URL, checkpoint a compact evidence
ledger, and act only after approval.  CAVEAT-Shop data and outcomes are excluded.
"""

from __future__ import annotations

import copy
import os
import re
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
from .browser_action_continuation import _tree_identity, _verified_source
from .browser_action_continuation_receipt import _verify_adapter
from .browser_action_curriculum import (
    _agent_output,
    _checkpoint_arguments,
    _curriculum_leakage_audit,
    _output_stack,
    _public_state,
    _source_row,
    _stable_key,
    _system_with_contract,
)
from .config import Campaign
from .prime_data import materialize_prime_dataset
from .sft_data import validate_sft_sample
from .splits import load_split_manifest
from .train_configs import _array, _quoted, _smoke_targets

FIXED_V6_CURRICULUM_SCHEMA = "caveat-27b.browser-action-fixed-v6-curriculum.v1"
FIXED_V6_PLAN_SCHEMA = "caveat-27b.browser-action-fixed-v6-plan.v1"
FIXED_V6_RECEIPT_SCHEMA = "caveat-27b.browser-action-fixed-v6-training-receipt.v1"

_SOURCE_STEP = 20
_FINAL_STEP = 24
_NEW_UPDATES = 4
_LEARNING_RATE = 3.0e-6
_TASKS = 128
_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_KINDS = (
    "multi-page-continue",
    "resolve-literal-identity",
    "dirty-cart-cleanup",
    "checkpoint-grounded",
    "recover-local-origin",
    "ordinary-replay",
)
_ROW_QUOTAS = {
    "multi-page-continue": 128,
    "dirty-cart-cleanup": 80,
    "resolve-literal-identity": 24,
    "checkpoint-grounded": 24,
    "recover-local-origin": 32,
    "ordinary-replay": 32,
}


def _git_sha(value: str | None, label: str) -> str:
    if value is None or _GIT_SHA.fullmatch(value) is None:
        raise ArtifactError(f"{label} must be a 40-character lowercase Git SHA")
    return value


def _compact_items(state: Mapping[str, Any], *, count: int = 6) -> list[dict[str, Any]]:
    catalog = state.get("catalog")
    if not isinstance(catalog, list) or len(catalog) < 2:
        raise ArtifactError("fixed-v6 procedural state needs at least two items")
    result: list[dict[str, Any]] = []
    for item in catalog[:count]:
        if not isinstance(item, Mapping) or not isinstance(item.get("item_id"), str):
            raise ArtifactError("fixed-v6 catalog item lacks a literal identity")
        value = copy.deepcopy(dict(item))
        value["source_url"] = f"https://shop.local/products/{value['item_id']}"
        result.append(value)
    return result


def _browser_rows(
    source: Mapping[str, Any], system: str, output_model: type[Any]
) -> list[tuple[str, dict[str, Any]]]:
    """Create six live-shaped, one-action targets from one procedural task."""

    _, state = _public_state(source)
    arguments = _checkpoint_arguments(source)
    items = _compact_items(state)
    catalog = state.get("catalog")
    if not isinstance(catalog, list):
        raise ArtifactError("fixed-v6 procedural state lacks a catalog")
    by_id = {
        str(item["item_id"]): copy.deepcopy(dict(item))
        for item in catalog
        if isinstance(item, Mapping) and isinstance(item.get("item_id"), str)
    }
    selected_id = str(arguments.get("proposed_candidate_id"))
    selected_item = by_id.get(selected_id)
    if selected_item is None:
        raise ArtifactError("fixed-v6 approved candidate is absent from the public catalog")
    selected_item["source_url"] = f"https://shop.local/products/{selected_id}"
    total = int(state.get("visible_total") or len(state.get("catalog", [])))
    page_size = max(2, min(len(items), int(state.get("page_size") or len(items))))
    first = items[0]
    second = next((item for item in items if str(item["item_id"]) != selected_id), None)
    if second is None:
        raise ArtifactError("fixed-v6 cart cleanup needs a distinct extra item")
    first_id = str(first["item_id"])
    second_id = str(second["item_id"])
    instruction = str(state["instruction"])

    partial_state = {
        "instruction": instruction,
        "advertised_total": total,
        "page": 1,
        "page_size": page_size,
        "seen_count": page_size,
        "resolved_count": 0,
        "unresolved_ids": [str(item["item_id"]) for item in items],
        "visible_cards": items,
    }
    continue_user = (
        f"<user_request>{instruction}</user_request>\n"
        f"Current URL: https://shop.local/search?page=1\n"
        f"Rendered result count: {total} results. Page 1.\n"
        f"[900]<button>Next</button>\n"
        "The durable ledger contains only the visible cards below; "
        "no later page has been visited.\n"
        f"Public browser ledger:\n{canonical_json(partial_state)}"
    )
    continue_target = _agent_output(
        output_model,
        thinking=(
            f"Only {page_size}/{total} advertised cards are seen and every visible card still "
            "has unresolved detail facts. I must paginate rather than call a checkpoint."
        ),
        evaluation="Coverage is incomplete; the current visible subset is not the catalog.",
        memory=f"Seen {page_size}/{total}; page 2 remains unvisited; preserve literal href IDs.",
        next_goal="Open the next page and reset to its top before enumerating cards.",
        action={"click": {"index": 900}},
    )

    resolve_user = (
        f"<user_request>{instruction}</user_request>\n"
        f"Current URL: https://shop.local/search?page=1\n"
        f"Seen {page_size}/{total}; unresolved contract facts remain for {first_id}.\n"
        f'[901]<a href="/products/{first_id}">{first.get("title", first_id)}</a>\n'
        "Copy the literal href identity; do not invent an ordinal product ID."
    )
    resolve_target = _agent_output(
        output_model,
        thinking=(
            f"The literal visible href binds this card to {first_id}. Its required facts are "
            "unresolved, so the next action is to open that exact product, not select yet."
        ),
        evaluation="Card coverage is not fact resolution.",
        memory=f"Unresolved literal ID {first_id}; href /products/{first_id}.",
        next_goal="Open the exact product detail and record its contract facts by literal ID.",
        action={"click": {"index": 901}},
    )

    cart_user = (
        f"<user_request>{instruction}</user_request>\n"
        "Current URL: https://shop.local/checkout\n"
        "Review your order before placing it.\n"
        f"Requested product: {selected_item.get('title', selected_id)} "
        f"({selected_id}) — $380.00\n"
        "[910]<button>Remove</button> Optional protection plan — $120.00\n"
        f"[911]<button>Remove</button> Accidental extra item {second_id} — $240.00\n"
        "Current subtotal: $740.00. One requested unit only. [912]<button>Place order</button>"
    )
    cart_target = _agent_output(
        output_model,
        thinking=(
            "The cart contains an unrequested protection plan and an accidental second item. "
            "Placing the order now would violate the requested unit and all-in constraints."
        ),
        evaluation="The cart is dirty; checkout is not authorized.",
        memory=(f"Keep one {selected_id}; remove protection and extra {second_id}; then re-total."),
        next_goal="Remove the optional protection plan first, then inspect the cart again.",
        action={"click": {"index": 910}},
    )

    checkpoint_arguments = copy.deepcopy(arguments)
    candidates = checkpoint_arguments.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ArtifactError("fixed-v6 checkpoint lacks candidates")
    submitted = candidates
    literal_ids = {str(item["item_id"]) for item in state["catalog"]}
    if any(str(item.get("id")) not in literal_ids for item in submitted):
        raise ArtifactError("fixed-v6 checkpoint identity is not in public catalog")
    proposed = str(checkpoint_arguments.get("proposed_candidate_id"))
    if proposed != selected_id or proposed not in {str(item.get("id")) for item in submitted}:
        raise ArtifactError("fixed-v6 accepted checkpoint candidate identity drifted")
    frontier = checkpoint_arguments.get("frontier")
    if not isinstance(frontier, Mapping):
        raise ArtifactError("fixed-v6 accepted checkpoint frontier is malformed")
    checkpoint_ledger = canonical_json(
        {
            "seen": total,
            "resolved": total,
            "unresolved": 0,
            "frontier": submitted,
        }
    )
    checkpoint_user = (
        f"<user_request>{instruction}</user_request>\n"
        "Current URL: https://shop.local/search?page=final\n"
        f"Rendered coverage line: {total} results\n"
        f"Identity-keyed ledger: {checkpoint_ledger}\n"
        "Every frontier ID and source URL above was copied literally from a visited href."
    )
    checkpoint_target = _agent_output(
        output_model,
        thinking=(
            "The literal ledger has complete card coverage, complete fact resolution, and "
            "exact href identities; checkpoint once and stop this action sequence."
        ),
        evaluation="Coverage and identities are grounded in the visible ledger.",
        memory=f"Resolved {total}/{total}; unresolved 0; literal frontier ready.",
        next_goal="Submit one concise decision checkpoint alone.",
        action={"decision_checkpoint": checkpoint_arguments},
    )

    recover_user = (
        f"<user_request>{instruction}</user_request>\n"
        "Current URL: about:blank\nOpen tabs: one empty tab.\n"
        f"Durable memory records the assigned local origin https://shop.local and product href "
        f"https://shop.local/products/{selected_id}. Do not invent an external brand URL."
    )
    recover_target = _agent_output(
        output_model,
        thinking=(
            "The current tab is blank, but durable memory contains an exact same-origin "
            "product URL. I should recover that literal URL rather than navigate to an "
            "invented public site."
        ),
        evaluation="Navigation lost the assigned storefront; the recorded local URL is available.",
        memory=f"Recovery URL: https://shop.local/products/{selected_id}.",
        next_goal="Return to the exact recorded local product page.",
        action={
            "navigate": {
                "url": f"https://shop.local/products/{selected_id}",
                "new_tab": False,
            }
        },
    )

    approved_user = (
        f"<user_request>{instruction}</user_request>\n"
        f"Decision checkpoint approved candidate {selected_id}.\n"
        f"Current URL: https://shop.local/products/{selected_id}\n"
        f"[920]<button>Add approved {selected_id} to cart</button>"
    )
    approved_target = _agent_output(
        output_model,
        thinking=(
            f"The approved literal identity {selected_id} exactly matches the visible href; "
            "I may add that one item, then must audit the cart before ordering."
        ),
        evaluation="Approved and visible product identities match.",
        memory=f"Approved exact ID {selected_id}; next phase includes cart reconciliation.",
        next_goal="Add exactly the approved item, then review every cart line and total.",
        action={"click": {"index": 920}},
    )

    payloads = {
        "multi-page-continue": (continue_user, continue_target),
        "resolve-literal-identity": (resolve_user, resolve_target),
        "dirty-cart-cleanup": (cart_user, cart_target),
        "checkpoint-grounded": (checkpoint_user, checkpoint_target),
        "recover-local-origin": (recover_user, recover_target),
        "ordinary-replay": (approved_user, approved_target),
    }
    return [
        (
            kind,
            _source_row(
                source,
                kind=kind,
                system=system,
                user=payloads[kind][0],
                assistant=payloads[kind][1],
            ),
        )
        for kind in _KINDS
    ]


def _materialize_curriculum(
    campaign: Campaign, *, campaign_root: Path, output: Path
) -> dict[str, Any]:
    split_path = campaign_root / "corpus/splits/manifest.json"
    rehearsal_path = campaign_root / "corpus/raw/rehearsal.jsonl"
    contract_path = campaign_root / "corpus/raw/contract.jsonl"
    split_manifest, membership = load_split_manifest(campaign, split_path)
    rehearsals = read_jsonl(rehearsal_path)
    contracts = read_jsonl(contract_path)
    contract_by_task: dict[str, dict[str, Any]] = {}
    for row in contracts:
        task_id = row.get("task_id")
        if isinstance(task_id, str) and str(row.get("sample_id", "")).endswith(":json-only"):
            contract_by_task[task_id] = row
    seed = int(campaign.campaign["seed"])
    selected = sorted(rehearsals, key=lambda row: _stable_key(seed + 61, str(row["task_id"])))[
        :_TASKS
    ]
    if len(selected) != _TASKS:
        raise ArtifactError("not enough procedural tasks for fixed-v6")
    selected_kinds: dict[str, set[str]] = {str(source["task_id"]): set() for source in selected}
    for kind_index, kind in enumerate(_KINDS):
        ranked = sorted(
            selected,
            key=lambda row: _stable_key(seed + 70 + kind_index, f"{kind}:{row['task_id']}"),
        )
        for source in ranked[: _ROW_QUOTAS[kind]]:
            selected_kinds[str(source["task_id"])].add(kind)
    system, output_model = _output_stack()
    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for source in selected:
        task_id = str(source.get("task_id"))
        contract = contract_by_task.get(task_id)
        if contract is None:
            raise ArtifactError("fixed-v6 task lacks contract replay")
        row_system = _system_with_contract(system, contract)
        for kind, row in _browser_rows(source, row_system, output_model):
            if kind not in selected_kinds[task_id]:
                continue
            rows.append(
                validate_sft_sample(
                    row,
                    membership=membership,
                    expected_stage=kind,
                    row_number=counts[kind] + 1,
                )
            )
            counts[kind] += 1
    if dict(counts) != _ROW_QUOTAS:
        raise ArtifactError("fixed-v6 curriculum row quotas drifted")
    rows.sort(key=lambda row: _stable_key(seed + 62, str(row["metadata"]["sample_id"])))
    leakage = _curriculum_leakage_audit(rows)
    data = publish_jsonl(output / "train.jsonl", rows)
    body = {
        "schema": FIXED_V6_CURRICULUM_SCHEMA,
        "stage": "refinement",
        "method": "browser_history_search_identity_cart_recovery_sft",
        "campaign_digest": campaign.digest,
        "split_manifest_sha256": sha256_file(split_path),
        "split_manifest_body_sha256": split_manifest["manifest_body_sha256"],
        "inputs": {
            "rehearsal": {"path": str(rehearsal_path), "sha256": sha256_file(rehearsal_path)},
            "contract": {"path": str(contract_path), "sha256": sha256_file(contract_path)},
        },
        "counts": dict(sorted(counts.items())),
        "policy": {
            "procedural_train_tasks": _TASKS,
            "row_quotas": _ROW_QUOTAS,
            "row_mix": {
                "multi_page_discovery": 0.40,
                "dirty_cart_cleanup": 0.25,
                "literal_identity_plus_checkpoint": 0.15,
                "local_origin_recovery": 0.10,
                "approved_action_replay": 0.10,
            },
            "browser_history_shaped": True,
            "full_catalog_checkpoint_dominance": False,
            "literal_identity_required": True,
            "dirty_cart_cleanup_required": True,
            "caveat_shop_outcomes_consulted": False,
            "candidate_sweep": False,
        },
        "assistant_wire_format": "browser-use AgentOutput.action JSON",
        "heldout_caveat_shop_scenarios_present": False,
        "leakage_audit": leakage,
        "output": {"path": data.name, "sha256": sha256_file(data), "rows": len(rows)},
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
        "# PRIME-RL 0.7.0; fixed-v6 one-candidate continuation",
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
        'type = "cosine"',
        "warmup_steps = 0",
        f"min_lr = {_LEARNING_RATE}",
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
        raise AssertionError("fixed-v6 constant LR policy drifted")
    return payload


def _token_mix_audit(stage: Path) -> dict[str, Any]:
    rows = read_jsonl(stage / "curriculum/train.jsonl")
    audit_rows = read_jsonl(stage / "prime/token_audit_rows.jsonl")
    if len(rows) != len(audit_rows):
        raise ArtifactError("fixed-v6 token audit row count differs")
    totals: Counter[str] = Counter()
    for index, (row, audit) in enumerate(zip(rows, audit_rows, strict=True), 1):
        if audit.get("line") != index or type(audit.get("rendered_tokens")) is not int:
            raise ArtifactError("fixed-v6 token audit ordering drifted")
        kind = row.get("metadata", {}).get("stage")
        if kind not in _KINDS:
            raise ArtifactError("fixed-v6 row stage drifted")
        totals[str(kind)] += int(audit["rendered_tokens"])
    total = sum(totals.values())
    if total <= 0:
        raise ArtifactError("fixed-v6 token audit is empty")
    fractions = {kind: totals[kind] / total for kind in _KINDS}
    grouped = {
        "multi_page_discovery": fractions["multi-page-continue"],
        "dirty_cart_cleanup": fractions["dirty-cart-cleanup"],
        "literal_identity_plus_checkpoint": (
            fractions["resolve-literal-identity"] + fractions["checkpoint-grounded"]
        ),
        "local_origin_recovery": fractions["recover-local-origin"],
        "approved_action_replay": fractions["ordinary-replay"],
    }
    bounds = {
        "multi_page_discovery": [0.32, 0.48],
        "dirty_cart_cleanup": [0.18, 0.32],
        "literal_identity_plus_checkpoint": [0.10, 0.24],
        "local_origin_recovery": [0.06, 0.16],
        "approved_action_replay": [0.06, 0.16],
    }
    if (
        any(not (bounds[name][0] <= value <= bounds[name][1]) for name, value in grouped.items())
        or fractions["checkpoint-grounded"] > 0.14
    ):
        raise ArtifactError("fixed-v6 exact rendered-token mixture is outside frozen bounds")
    return {
        "rendered_tokens": total,
        "rendered_tokens_by_stage": dict(sorted(totals.items())),
        "fractions": fractions,
        "grouped_fractions": grouped,
        "frozen_bounds": {**bounds, "checkpoint_grounded_maximum": 0.14},
        "gate_passed": True,
    }


def _hardlink_tree(source: Path, destination: Path) -> dict[str, Any]:
    if destination.exists() or destination.is_symlink():
        raise ArtifactError("fixed-v6 DCP destination already exists")
    source_identity = _tree_identity(source)
    destination.mkdir(parents=True)
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        target = destination / rel
        if path.is_symlink():
            raise ArtifactError("fixed-v6 DCP source contains symlink")
        if path.is_dir():
            target.mkdir(exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(path, target)
        else:
            raise ArtifactError("fixed-v6 DCP source contains special entry")
    copied = _tree_identity(destination)
    if any(copied[k] != source_identity[k] for k in ("files", "bytes", "tree_sha256")):
        raise ArtifactError("fixed-v6 hardlinked DCP identity drifted")
    inventory: list[dict[str, Any]] = []
    for path in sorted(source.rglob("*")):
        if path.is_file():
            target = destination / path.relative_to(source)
            a, b = path.stat(), target.stat()
            if a.st_dev != b.st_dev or a.st_ino != b.st_ino:
                raise ArtifactError("fixed-v6 DCP file is not an exact hardlink")
            inventory.append(
                {
                    "relative_path": path.relative_to(source).as_posix(),
                    "device": a.st_dev,
                    "inode": a.st_ino,
                    "bytes": a.st_size,
                    "link_count_at_preparation": a.st_nlink,
                }
            )
    return {
        **copied,
        "hardlink_verified": True,
        "hardlink_inventory": inventory,
    }


def prepare_browser_action_fixed_v6(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    output_dir: str | Path,
    num_gpus: int = 4,
) -> dict[str, Any]:
    if num_gpus != 4:
        raise ArtifactError("fixed-v6 uses four GPUs")
    root = Path(campaign_root).resolve()
    campaign_sha = _git_sha(root.name, "campaign artifact source Git SHA")
    output = Path(output_dir).absolute()
    if output.is_symlink() or (output.exists() and (not output.is_dir() or any(output.iterdir()))):
        raise ArtifactError("fixed-v6 output must be new and empty")
    output = output.resolve()
    if output.parent != root / "browser_action_fixed_v6" or _GIT_SHA.fullmatch(output.name) is None:
        raise ArtifactError("fixed-v6 output path is invalid")
    curriculum = _materialize_curriculum(campaign, campaign_root=root, output=output / "curriculum")
    prime = materialize_prime_dataset(
        campaign,
        source_jsonl=output / "curriculum/train.jsonl",
        source_manifest=output / "curriculum/manifest.json",
        smoke_report=root / "smoke/smoke_report.json",
        stage="refinement",
        output_dir=output / "prime",
        prime_root=os.environ.get("CAVEAT_27B_PRIME_ROOT", "/opt/prime-rl"),
    )
    mix = _token_mix_audit(output)
    source = _verified_source(campaign, root)
    prep, post = read_json(root / "prep_receipt.json"), read_json(root / "post_sft_receipt.json")
    if (
        not isinstance(prep, dict)
        or prep.get("source_git_sha") != campaign_sha
        or not isinstance(post, dict)
        or post.get("artifact_source_git_sha") != campaign_sha
    ):
        raise ArtifactError("fixed-v6 campaign identity drifted")
    _, targets, revision = _smoke_targets(root / "smoke/smoke_report.json", campaign)
    if revision and revision != campaign.model["revision"]:
        raise ArtifactError("fixed-v6 model revision drifted")
    training = output / "training"
    linked = _hardlink_tree(source["source_dcp"], training / "prime_output/checkpoints/step_20")
    config_path = publish_bytes(
        training / "browser_action_fixed_v6.toml",
        _fixed_toml(
            model=source["parent"],
            dataset=(output / "prime").resolve(),
            output=(training / "prime_output").resolve(),
            targets=targets,
            settings=campaign.campaign["refinement"],
            seed=int(campaign.campaign["seed"]) + 63,
            num_gpus=num_gpus,
        ),
    )
    plan = {
        "schema": FIXED_V6_PLAN_SCHEMA,
        "status": "prepared",
        "stage": "browser_action_fixed_v6",
        "campaign_digest": campaign.digest,
        "campaign_artifact_source_git_sha": campaign_sha,
        "artifact_source_git_sha": output.name,
        "parent_model": str(source["parent"]),
        "parent_merge_provenance_sha256": source["parent_merge_provenance_sha256"],
        "source_step20_adapter": source["adapter_identity"],
        "source_step20_adapter_receipt_tree_sha256": source["adapter_receipt_tree_sha256"],
        "source_step20_dcp": source["source_dcp_identity"],
        "linked_step20_dcp": linked,
        "curriculum_manifest_sha256": sha256_file(output / "curriculum/manifest.json"),
        "curriculum_data_sha256": sha256_file(output / "curriculum/train.jsonl"),
        "prime_manifest_sha256": sha256_file(output / "prime/manifest.json"),
        "prime_parquet_sha256": sha256_file(output / "prime/train.parquet"),
        "config": str(config_path),
        "config_sha256": sha256_file(config_path),
        "candidate": {
            "name": "step24",
            "update": _FINAL_STEP,
            "path": str((training / "prime_output/weights/step_24/lora_adapters").resolve()),
        },
        "selection_performed": False,
        "caveat_shop_outcomes_consulted": False,
        "training_policy": {
            "optimizer": "adamw",
            "learning_rate": _LEARNING_RATE,
            "scheduler": "constant_cosine_floor",
            "warmup_steps": 0,
            "resume_step": _SOURCE_STEP,
            "new_optimizer_updates": _NEW_UPDATES,
            "optimizer_updates": _FINAL_STEP,
            "checkpoint_updates": [_FINAL_STEP],
            "num_gpus": num_gpus,
            "global_batch_size": int(campaign.campaign["refinement"]["global_batch_size"]),
            "sequence_length": int(campaign.campaign["refinement"]["sequence_length"]),
            "lora_rank": int(campaign.campaign["refinement"]["lora_rank"]),
            "lora_alpha": float(campaign.campaign["refinement"]["lora_alpha"]),
            "restore_model": True,
            "restore_progress": True,
            "restore_optimizer": False,
            "restore_scheduler": False,
            "restore_dataloader": False,
        },
        "curriculum": curriculum,
        "prime": {"rows": prime["row_count"], "token_audit": prime["token_audit"]},
        "token_mix_audit": mix,
        "original_refinement_mutated": False,
    }
    path = publish_json(training / "plan.json", plan)
    return {**plan, "plan_sha256": sha256_file(path)}


def write_browser_action_fixed_v6_receipt(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    stage_dir: str | Path,
    artifact_source_git_sha: str | None,
    execution_source_git_sha: str | None,
) -> dict[str, Any]:
    root, stage = Path(campaign_root).resolve(), Path(stage_dir).resolve()
    artifact_sha = _git_sha(artifact_source_git_sha, "artifact source Git SHA")
    execution_sha = _git_sha(execution_source_git_sha, "execution source Git SHA")
    if stage != root / "browser_action_fixed_v6" / artifact_sha:
        raise ArtifactError("fixed-v6 stage path drifted")
    training = stage / "training"
    plan_path, config_path = training / "plan.json", training / "browser_action_fixed_v6.toml"
    plan, source = read_json(plan_path), _verified_source(campaign, root)
    if (
        not isinstance(plan, dict)
        or plan.get("schema") != FIXED_V6_PLAN_SCHEMA
        or plan.get("status") != "prepared"
    ):
        raise ArtifactError("fixed-v6 plan is incompatible")
    if (
        plan.get("artifact_source_git_sha") != artifact_sha
        or plan.get("campaign_digest") != campaign.digest
        or plan.get("campaign_artifact_source_git_sha") != root.name
        or plan.get("parent_model") != str(source["parent"])
        or plan.get("parent_merge_provenance_sha256") != source["parent_merge_provenance_sha256"]
        or plan.get("source_step20_adapter_receipt_tree_sha256")
        != source["adapter_receipt_tree_sha256"]
        or plan.get("selection_performed") is not False
        or plan.get("caveat_shop_outcomes_consulted") is not False
    ):
        raise ArtifactError("fixed-v6 plan binding drifted")
    if (
        plan.get("source_step20_dcp") != source["source_dcp_identity"]
        or plan.get("source_step20_adapter") != source["adapter_identity"]
    ):
        raise ArtifactError("fixed-v6 canonical step20 source drifted")
    linked = plan.get("linked_step20_dcp")
    inventory = linked.get("hardlink_inventory") if isinstance(linked, Mapping) else None
    if (
        not isinstance(linked, Mapping)
        or linked.get("hardlink_verified") is not True
        or linked.get("tree_sha256") != source["source_dcp_identity"]["tree_sha256"]
        or not isinstance(inventory, list)
        or len(inventory) != source["source_dcp_identity"]["files"]
        or any(
            not isinstance(item, Mapping)
            or not isinstance(item.get("relative_path"), str)
            or type(item.get("device")) is not int
            or type(item.get("inode")) is not int
            or type(item.get("bytes")) is not int
            or type(item.get("link_count_at_preparation")) is not int
            or item["link_count_at_preparation"] < 2
            for item in inventory
        )
        or len({str(item["relative_path"]) for item in inventory}) != len(inventory)
    ):
        raise ArtifactError("fixed-v6 hardlink preparation attestation drifted")
    staged_resume = training / "prime_output/checkpoints/step_20"
    if staged_resume.exists():
        if staged_resume.is_symlink() or _tree_identity(staged_resume) != {
            **source["source_dcp_identity"],
            "path": str(staged_resume.resolve()),
        }:
            raise ArtifactError("fixed-v6 retained resume DCP identity drifted")
        for item in inventory:
            source_path = source["source_dcp"] / str(item["relative_path"])
            staged_path = staged_resume / str(item["relative_path"])
            source_stat, staged_stat = source_path.stat(), staged_path.stat()
            if source_stat.st_dev != staged_stat.st_dev or source_stat.st_ino != staged_stat.st_ino:
                raise ArtifactError("fixed-v6 retained resume DCP is no longer hardlinked")
    # PRIME may prune the linked resume checkpoint. The immutable canonical source,
    # frozen plan identity, final adapter, and trainer config are authoritative.
    for path, expected in (
        (config_path, plan.get("config_sha256")),
        (stage / "curriculum/manifest.json", plan.get("curriculum_manifest_sha256")),
        (stage / "curriculum/train.jsonl", plan.get("curriculum_data_sha256")),
        (stage / "prime/manifest.json", plan.get("prime_manifest_sha256")),
        (stage / "prime/train.parquet", plan.get("prime_parquet_sha256")),
    ):
        if sha256_file(path) != expected:
            raise ArtifactError("fixed-v6 frozen training input drifted")
    config = tomllib.loads(config_path.read_text())
    expected_policy = {
        "optimizer": "adamw",
        "learning_rate": _LEARNING_RATE,
        "scheduler": "constant_cosine_floor",
        "warmup_steps": 0,
        "resume_step": _SOURCE_STEP,
        "new_optimizer_updates": _NEW_UPDATES,
        "optimizer_updates": _FINAL_STEP,
        "checkpoint_updates": [_FINAL_STEP],
        "num_gpus": 4,
        "global_batch_size": int(campaign.campaign["refinement"]["global_batch_size"]),
        "sequence_length": int(campaign.campaign["refinement"]["sequence_length"]),
        "lora_rank": int(campaign.campaign["refinement"]["lora_rank"]),
        "lora_alpha": float(campaign.campaign["refinement"]["lora_alpha"]),
        "restore_model": True,
        "restore_progress": True,
        "restore_optimizer": False,
        "restore_scheduler": False,
        "restore_dataloader": False,
    }
    if (
        config.get("max_steps") != _FINAL_STEP
        or config.get("deployment")
        != {
            "type": "single_node",
            "num_gpus": 4,
            "gpus_per_node": 4,
        }
        or config.get("scheduler")
        != {"type": "cosine", "warmup_steps": 0, "min_lr": _LEARNING_RATE}
        or config.get("ckpt", {}).get("interval") != _NEW_UPDATES
        or config.get("ckpt", {}).get("resume_step") != _SOURCE_STEP
        or config.get("ckpt", {}).get("keep_last") != 2
        or config.get("ckpt", {}).get("skip_optimizer") is not True
        or config.get("ckpt", {}).get("skip_scheduler") is not True
        or config.get("ckpt", {}).get("skip_dataloader") is not True
        or config.get("ckpt", {}).get("skip_progress") is not False
        or config.get("optim", {}).get("type") != "adamw"
        or config.get("optim", {}).get("lr") != _LEARNING_RATE
        or plan.get("training_policy") != expected_policy
    ):
        raise ArtifactError("fixed-v6 config drifted")
    candidate = _verify_adapter(
        adapter=training / "prime_output/weights/step_24/lora_adapters",
        parent=source["parent"],
        targets=list(config["model"]["lora"]["target_modules"]),
        update=_FINAL_STEP,
    )
    body = {
        "schema": FIXED_V6_RECEIPT_SCHEMA,
        "status": "ok",
        "stage": "browser_action_fixed_v6",
        "campaign_digest": campaign.digest,
        "campaign_artifact_source_git_sha": root.name,
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
        "source_step20_dcp": source["source_dcp_identity"],
        "linked_step20_dcp_initial": plan["linked_step20_dcp"],
        "linked_resume_may_be_pruned": True,
        "optimizer_updates": _FINAL_STEP,
        "new_optimizer_updates": _NEW_UPDATES,
        "checkpoint_updates": [_FINAL_STEP],
        "training_policy": plan["training_policy"],
        "candidate": {"name": "step24", **candidate},
        "selection_performed": False,
        "caveat_shop_outcomes_consulted": False,
        "original_refinement_unchanged_after_training": True,
    }
    receipt = dict(body)
    receipt["receipt_body_sha256"] = sha256_bytes(canonical_json(body).encode())
    publish_json(training / "training_receipt.json", receipt)
    return receipt
