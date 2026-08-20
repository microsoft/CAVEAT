"""Build the real procedural-only targeted-SFT corpora.

The generator's oracle is used only offline to reject examples whose generated
winner and unchanged harness decision kernel disagree.  It is never serialized
into a model-visible row.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import subprocess
import sys
import zipfile
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    canonical_json,
    publish_json,
    publish_jsonl,
    sha256_file,
)
from .config import Campaign
from .quick_data import CONTRACT_SYSTEM_PROMPT
from .sft_data import SFT_SOURCE_SCHEMA, _scan_visible, materialize_targeted_sft
from .splits import freeze_splits

EXPECTED_GENERATOR_COMMIT = "b04186068e15544f3a5c1a0461f7c39afed29a3a"
EXPECTED_GENERATOR_HASHES = {
    "src/harness_distill/procedural.py": (
        "ed443c1d572d66fb45c716a61108f988dbb614079bc17dcc781349874134d55e"
    ),
    "src/harness_distill/schemas.py": (
        "6567ec9aab7165aa859698d7fea5fe713e354d864af27d3ef3f686de5fcf1fba"
    ),
    "src/harness_distill/split.py": (
        "0431ded23ca09b6e5f8f20874e55e0909a9ae11531d05db91a2391e1f9556319"
    ),
}
EXPECTED_GENERATOR_WHEEL_SHA256 = (
    "239100a9a6296187abaee4835c42ba8e3954332d52691c848d41738c4774d8e1"
)
FORBIDDEN_CAVEAT_SHOP_DOMAINS = (
    "laptop",
    "office_chair",
    "office chair",
    "mattress",
    "backpack",
    "tent",
)
PROCEDURAL_CORPUS_SCHEMA = "caveat-27b.procedural-corpus.v1"


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise ArtifactError(f"generator Git check failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _load_generator(generator_root: str | Path) -> tuple[Path, Any, Any, Any]:
    root = Path(generator_root).resolve()
    if _git(root, "rev-parse", "HEAD") != EXPECTED_GENERATOR_COMMIT:
        raise ArtifactError("procedural generator commit differs from the audited source")
    if _git(root, "status", "--porcelain"):
        raise ArtifactError("procedural generator worktree must be clean")
    for relative, expected in EXPECTED_GENERATOR_HASHES.items():
        if sha256_file(root / relative) != expected:
            raise ArtifactError(f"procedural generator source drifted: {relative}")
    for source in (root / "src", root.parent.parent):
        value = str(source)
        if value not in sys.path:
            sys.path.insert(0, value)
    procedural = importlib.import_module("harness_distill.procedural")
    schemas = importlib.import_module("harness_distill.schemas")
    split = importlib.import_module("harness_distill.split")
    loaded = Path(procedural.__file__).resolve()
    if root not in loaded.parents:
        raise ArtifactError(f"loaded procedural generator from unexpected path: {loaded}")
    return root, procedural, schemas, split


def _load_generator_wheel(generator_wheel: str | Path) -> tuple[Path, Any, Any, Any]:
    wheel = Path(generator_wheel).resolve()
    if sha256_file(wheel) != EXPECTED_GENERATOR_WHEEL_SHA256:
        raise ArtifactError("vendored procedural generator wheel drifted")
    with zipfile.ZipFile(wheel) as archive:
        for relative, expected in EXPECTED_GENERATOR_HASHES.items():
            member = relative.removeprefix("src/")
            try:
                payload = archive.read(member)
            except KeyError as exc:
                raise ArtifactError(f"generator wheel omits {member}") from exc
            if hashlib.sha256(payload).hexdigest() != expected:
                raise ArtifactError(f"generator wheel source drifted: {member}")
    value = str(wheel)
    if value not in sys.path:
        sys.path.insert(0, value)
    procedural = importlib.import_module("harness_distill.procedural")
    schemas = importlib.import_module("harness_distill.schemas")
    split = importlib.import_module("harness_distill.split")
    return wheel, procedural, schemas, split


def _core() -> Any:
    return importlib.import_module("caveat.scaffolds._caveat_harness_core")


def _tool_schema() -> dict[str, Any]:
    module = importlib.import_module("caveat.scaffolds.caveat_harness")
    model = module._DecisionCheckpoint  # noqa: SLF001 - exact unchanged harness schema
    return {
        "type": "function",
        "function": {
            "name": "decision_checkpoint",
            "description": (
                "Validate a complete candidate frontier against the literal task contract "
                "and approve only an exact best candidate."
            ),
            "parameters": model.model_json_schema(),
        },
    }


def _criterion_id(kind: str, field: str) -> str:
    return f"{kind}_{field}"


def _contract_payload(task: Any) -> dict[str, Any]:
    return {
        "constraints": [
            {
                "criterion_id": _criterion_id("constraint", item.field),
                "description": item.label,
                "operator": item.operator,
                "expected": item.value,
                "unit": item.unit or None,
            }
            for item in task.hard_constraints
        ],
        "objectives": [
            {
                "criterion_id": _criterion_id("objective", item.field),
                "description": item.label,
                "direction": "maximize" if item.direction == "higher" else "minimize",
                "unit": item.unit or None,
                "priority": item.priority,
                "weight": item.weight,
            }
            for item in task.objectives
        ],
        "search_mode": "best_available",
    }


def _materialize_contract(task: Any, payload: Mapping[str, Any], core: Any) -> Any:
    constraints = tuple(
        core.Constraint(
            criterion_id=row["criterion_id"],
            description=row["description"],
            operator=row["operator"],
            expected=row["expected"],
            unit=row["unit"],
        )
        for row in payload["constraints"]
    )
    objectives = tuple(
        core.Objective(
            criterion_id=row["criterion_id"],
            description=row["description"],
            direction=row["direction"],
            unit=row["unit"],
            priority=row["priority"],
            weight=row["weight"],
        )
        for row in payload["objectives"]
    )
    return core.TaskContract(
        instruction=task.instruction,
        constraints=constraints,
        objectives=objectives,
        search_mode="best_available",
    )


def _candidate(item: Any, task: Any, core: Any) -> Any:
    facts = []
    for criterion in task.hard_constraints:
        facts.append(
            core.CandidateFact(
                criterion_id=_criterion_id("constraint", criterion.field),
                state="known",
                value=item.value(criterion.field),
                unit=criterion.unit or None,
            )
        )
    for objective in task.objectives:
        facts.append(
            core.CandidateFact(
                criterion_id=_criterion_id("objective", objective.field),
                state="known",
                value=item.value(objective.field),
                unit=objective.unit or None,
            )
        )
    return core.Candidate(
        candidate_id=item.item_id,
        label=item.title,
        source_url=f"https://shop.local/products/{item.item_id}",
        facts=tuple(facts),
    )


def _candidate_wire(candidate: Any) -> dict[str, Any]:
    return {
        "id": candidate.candidate_id,
        "label": candidate.label,
        "source_url": candidate.source_url,
        "facts": [
            {
                "criterion_id": fact.criterion_id,
                "state": fact.state.value,
                "value": fact.value,
                "unit": fact.unit,
            }
            for fact in candidate.facts
        ],
    }


def _checkpoint(task: Any, core: Any) -> tuple[dict[str, Any], dict[str, Any]] | None:
    payload = _contract_payload(task)
    contract = _materialize_contract(task, payload, core)
    candidates = tuple(_candidate(item, task, core) for item in task.catalog)
    selection = core.select_candidate(contract, candidates, start_origin="https://shop.local")
    # Only train examples on which the unchanged harness and generator agree.
    # This guards against synthesizing a protocol-valid but reward-wrong target.
    if selection.selected_candidate_id != task.oracle.optimal_item_id:
        return None
    by_id = {candidate.candidate_id: candidate for candidate in candidates}
    frontier = tuple(by_id[item_id] for item_id in selection.pareto_frontier_ids)
    basis = f"{task.visible_total} results"
    frontier_value = core.Frontier(
        inspected_count=task.visible_total,
        advertised_count=task.visible_total,
        coverage_mode="advertised_total",
        excluded_count=task.visible_total - len(frontier),
        unresolved_count=0,
        exhausted=True,
        basis=basis,
    )
    result = core.evaluate_checkpoint(
        contract=contract,
        start_origin="https://shop.local",
        current_url="https://shop.local/search",
        rendered_page_text=f"Search\n{basis}\nAll options inspected",
        frontier=frontier_value,
        candidates=frontier,
        proposed_candidate_id=str(selection.selected_candidate_id),
    )
    if not result.approved:
        raise ArtifactError(f"generated rehearsal failed unchanged checkpoint: {result.reasons}")
    arguments = {
        "frontier": {
            "inspected_count": frontier_value.inspected_count,
            "advertised_count": frontier_value.advertised_count,
            "coverage_mode": frontier_value.coverage_mode.value,
            "advertised_page_count": None,
            "enumerated_page_count": None,
            "excluded_count": frontier_value.excluded_count,
            "unresolved_count": 0,
            "exhausted": True,
            "basis": basis,
        },
        "candidates": [_candidate_wire(candidate) for candidate in frontier],
        "proposed_candidate_id": selection.selected_candidate_id,
    }
    return payload, arguments


def _generate_compatible(
    procedural: Any,
    core: Any,
    *,
    split: str,
    count: int,
    seed: int,
    oversample: int,
    catalog_sizes: Sequence[int],
) -> list[tuple[Any, dict[str, Any], dict[str, Any]]]:
    tasks = procedural.generate_dataset(
        split,
        oversample,
        seed=seed,
        objective_pair_count=0,
        catalog_sizes=tuple(catalog_sizes),
    )
    procedural.validate_dataset(tasks, expected_split=split)
    compatible: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
    for task in tasks:
        if task.mode != "best_available":
            continue
        checkpoint = _checkpoint(task, core)
        if checkpoint is None:
            continue
        contract, arguments = checkpoint
        compatible.append((task, contract, arguments))
        if len(compatible) == count:
            break
    if len(compatible) != count:
        raise ArtifactError(
            f"generator yielded only {len(compatible)} compatible {split} tasks; expected {count}"
        )
    return compatible


def _load_compatible_bank(
    schemas: Any,
    core: Any,
    *,
    path: Path,
    expected_split: str,
    count: int,
    maximum_catalog_size: int = 64,
) -> list[tuple[Any, dict[str, Any], dict[str, Any]]]:
    compatible: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            try:
                task = schemas.TrainingTaskSpec.from_json(raw)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ArtifactError(f"invalid procedural bank row {path}:{line_number}") from exc
            if (
                task.split != expected_split
                or task.mode != "best_available"
                or task.visible_total > maximum_catalog_size
            ):
                continue
            checkpoint = _checkpoint(task, core)
            if checkpoint is None:
                continue
            contract, arguments = checkpoint
            compatible.append((task, contract, arguments))
            if len(compatible) == count:
                break
    if len(compatible) != count:
        raise ArtifactError(
            f"bank yielded only {len(compatible)} compatible {expected_split} tasks; "
            f"expected {count}"
        )
    return compatible


def _base(task: Any) -> dict[str, Any]:
    return {
        "schema": SFT_SOURCE_SCHEMA,
        "task_id": task.task_id,
        "source": "procedural",
        "scenario": task.domain_family,
    }


def _sft_rows(
    rows: Sequence[tuple[Any, dict[str, Any], dict[str, Any]]], tool: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    contracts: list[dict[str, Any]] = []
    interactions: list[dict[str, Any]] = []
    rehearsals: list[dict[str, Any]] = []
    for task, contract, arguments in rows:
        target = canonical_json(contract)
        base = _base(task)
        contracts.extend(
            [
                {
                    **base,
                    "sample_id": f"contract:{task.task_id}:literal",
                    "tools": [],
                    "messages": [
                        {"role": "system", "content": CONTRACT_SYSTEM_PROMPT},
                        {"role": "user", "content": task.instruction},
                        {"role": "assistant", "content": target},
                    ],
                },
                {
                    **base,
                    "sample_id": f"contract:{task.task_id}:json-only",
                    "tools": [],
                    "messages": [
                        {"role": "system", "content": CONTRACT_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"Instruction:\n{task.instruction}\n\n"
                                "Return only the contract JSON."
                            ),
                        },
                        {"role": "assistant", "content": target},
                    ],
                },
            ]
        )
        interactions.extend(
            [
                {
                    **base,
                    "sample_id": f"interaction:{task.task_id}:malformed",
                    "tools": [],
                    "messages": [
                        {"role": "system", "content": CONTRACT_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"{task.instruction}\n\nThe prior draft was invalid. Correct it "
                                "without adding anything. Validation error: malformed JSON."
                            ),
                        },
                        {"role": "assistant", "content": target},
                    ],
                },
                {
                    **base,
                    "sample_id": f"interaction:{task.task_id}:semantic",
                    "tools": [],
                    "messages": [
                        {"role": "system", "content": CONTRACT_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"{task.instruction}\n\nThe prior draft was rejected. Correct it "
                                "without inventing criteria. Validation error: comparative "
                                "objectives require best_available."
                            ),
                        },
                        {"role": "assistant", "content": target},
                    ],
                },
            ]
        )
        public_state = task.agent_view()
        basis = arguments["frontier"]["basis"]
        rehearsals.append(
            {
                **base,
                "sample_id": f"rehearsal:{task.task_id}:checkpoint",
                "tools": [tool],
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Follow the literal task contract. Inspect the complete public option "
                            "set, resolve every criterion, submit exactly the feasible Pareto "
                            "frontier, and call decision_checkpoint alone before acting."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"The rendered coverage line is exactly: {basis}\n"
                            f"Public marketplace state:\n{canonical_json(public_state)}"
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": f"checkpoint_{task.task_id}",
                                "type": "function",
                                "function": {
                                    "name": "decision_checkpoint",
                                    "arguments": canonical_json(arguments),
                                },
                            }
                        ],
                    },
                ],
            }
        )
    return contracts, interactions, rehearsals


def _leakage_audit(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    hits: Counter[str] = Counter()
    for row in rows:
        visible = {"messages": row.get("messages"), "tools": row.get("tools")}
        _scan_visible(visible)
        text = canonical_json(visible).casefold()
        for term in FORBIDDEN_CAVEAT_SHOP_DOMAINS:
            pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
            if re.search(pattern, text):
                hits[term] += 1
    if hits:
        raise ArtifactError(f"sealed CAVEAT-Shop category text entered SFT rows: {dict(hits)}")
    return {
        "rows_scanned": len(rows),
        "forbidden_private_keys_found": 0,
        "sealed_caveat_shop_category_terms_found": 0,
    }


def build_procedural_corpora(
    campaign: Campaign,
    *,
    generator_root: str | Path | None = None,
    generator_wheel: str | Path | None = None,
    task_bank: str | Path | None = None,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Generate, verify, and freeze all three 4,800-row SFT candidates."""

    if (generator_root is None) == (generator_wheel is None):
        raise ArtifactError("supply exactly one of generator_root or generator_wheel")
    if generator_root is not None:
        root, procedural, schemas, split_module = _load_generator(generator_root)
        generator_kind = "clean_git_worktree"
    else:
        root, procedural, schemas, split_module = _load_generator_wheel(generator_wheel)
        generator_kind = "audited_vendored_wheel"
    core = _core()
    task_count = int(campaign.campaign["targeted_sft"]["raw_pool"]["tasks"])
    seed = int(campaign.campaign["seed"])
    bank_root = Path(task_bank).resolve() if task_bank is not None else None
    if bank_root is not None:
        bank_manifest = json.loads((bank_root / "manifest.json").read_text(encoding="utf-8"))
        if (
            bank_manifest.get("schema") != "harness-distill.procedural-manifest.v1"
            or bank_manifest.get("split_fingerprint") != split_module.split_fingerprint()
        ):
            raise ArtifactError("procedural task bank manifest is incompatible")
        bank_files = {
            "train": (bank_root / "train.jsonl", "train"),
            "validation": (bank_root / "validation.jsonl", "validation"),
            "test": (bank_root / "procedural_test.jsonl", "test"),
        }
        for configured, (path, _logical) in bank_files.items():
            descriptor_name = "procedural_test" if configured == "test" else configured
            descriptor = bank_manifest["splits"][descriptor_name]
            if sha256_file(path) != descriptor["sha256"]:
                raise ArtifactError(f"procedural task bank bytes drifted: {configured}")
        train = _load_compatible_bank(
            schemas,
            core,
            path=bank_files["train"][0],
            expected_split="train",
            count=task_count + 128,
        )
        selection = _load_compatible_bank(
            schemas,
            core,
            path=bank_files["validation"][0],
            expected_split="validation",
            count=64,
        )
        final = _load_compatible_bank(
            schemas,
            core,
            path=bank_files["test"][0],
            expected_split="test",
            count=64,
        )
    else:
        bank_manifest = None
        train = _generate_compatible(
            procedural,
            core,
            split="train",
            count=task_count + 128,
            seed=seed,
            oversample=3400,
            catalog_sizes=(16, 24, 32, 48, 64),
        )
        selection = _generate_compatible(
            procedural,
            core,
            split="validation",
            count=64,
            seed=seed + 1,
            oversample=160,
            catalog_sizes=(16, 24, 32, 48, 64),
        )
        final = _generate_compatible(
            procedural,
            core,
            split="test",
            count=64,
            seed=seed + 2,
            oversample=160,
            catalog_sizes=(16, 24, 32, 48, 64),
        )
    sft_tasks = train[:task_count]
    refinement_candidates = train[task_count:]
    steered = [row for row in refinement_candidates if row[0].condition == "truthful_steered"]
    clean = [row for row in refinement_candidates if row[0].condition == "clean"]
    if len(steered) < 48 or len(clean) < 16:
        raise ArtifactError("fresh refinement pool cannot satisfy the frozen 75/25 condition mix")
    refinement_tasks = [*steered[:48], *clean[:16]]
    output = Path(output_dir).resolve()
    inventory_rows = []
    for split, role_rows in (
        ("train", train),
        ("selection", selection),
        ("final", final),
    ):
        for task, _contract, _arguments in role_rows:
            inventory_rows.append(
                {
                    "task_id": task.task_id,
                    "source": "procedural",
                    "scenario": task.domain_family,
                    "domain_family": task.domain_family,
                    "split": split,
                }
            )
    inventory = publish_jsonl(output / "inventory.jsonl", inventory_rows)
    split_manifest = freeze_splits(
        campaign, inventory_path=inventory, output_dir=output / "splits"
    )
    tool = _tool_schema()
    contract_rows, interaction_rows, rehearsal_rows = _sft_rows(sft_tasks, tool)
    all_rows = [*contract_rows, *interaction_rows, *rehearsal_rows]
    audit = _leakage_audit(all_rows)
    raw = output / "raw"
    files = {
        "contract": publish_jsonl(raw / "contract.jsonl", contract_rows),
        "interaction": publish_jsonl(raw / "interaction.jsonl", interaction_rows),
        "rehearsal": publish_jsonl(raw / "rehearsal.jsonl", rehearsal_rows),
    }
    refinement_inventory = publish_jsonl(
        raw / "refinement_task_ids.jsonl",
        [
            {
                "task_id": task.task_id,
                "source": "procedural",
                "scenario": task.domain_family,
                "condition": task.condition,
            }
            for task, _contract, _arguments in refinement_tasks
        ],
    )
    refinement_contracts = publish_jsonl(
        raw / "refinement_contract_tasks.jsonl",
        [
            {
                "schema": "caveat-27b.contract-shadow-task.v1",
                "task_id": task.task_id,
                "source": "procedural",
                "scenario": task.domain_family,
                "condition": task.condition,
                "instruction": task.instruction,
                "gold_contract": contract,
            }
            for task, contract, _arguments in refinement_tasks
        ],
    )
    selection_inventory = publish_jsonl(
        raw / "selection_task_ids.jsonl",
        [
            {
                "task_id": task.task_id,
                "source": "procedural",
                "scenario": task.domain_family,
                "condition": task.condition,
            }
            for task, _contract, _arguments in selection
        ],
    )
    selection_contracts = publish_jsonl(
        raw / "selection_contract_tasks.jsonl",
        [
            {
                "schema": "caveat-27b.contract-shadow-task.v1",
                "task_id": task.task_id,
                "source": "procedural",
                "scenario": task.domain_family,
                "condition": task.condition,
                "instruction": task.instruction,
                "gold_contract": contract,
            }
            for task, contract, _arguments in selection
        ],
    )
    selection_rehearsals = publish_jsonl(
        raw / "selection_checkpoint_tasks.jsonl",
        [
            {
                "schema": "caveat-27b.checkpoint-shadow-task.v1",
                "task_id": task.task_id,
                "source": "procedural",
                "scenario": task.domain_family,
                "instruction": task.instruction,
                "rendered_basis": arguments["frontier"]["basis"],
                "public_state": task.agent_view(),
                "expected_arguments": arguments,
            }
            for task, _contract, arguments in selection[:8]
        ],
    )
    raw_manifest = {
        "schema": PROCEDURAL_CORPUS_SCHEMA,
        "campaign_digest": campaign.digest,
        "generator": {
            "root": str(root),
            "kind": generator_kind,
            "commit": EXPECTED_GENERATOR_COMMIT,
            "files": EXPECTED_GENERATOR_HASHES,
            "split_fingerprint": split_module.split_fingerprint(),
        },
        "task_bank": (
            None
            if bank_root is None
            else {
                "root": str(bank_root),
                "manifest_sha256": sha256_file(bank_root / "manifest.json"),
                "train_sha256": sha256_file(bank_root / "train.jsonl"),
                "validation_sha256": sha256_file(bank_root / "validation.jsonl"),
                "test_sha256": sha256_file(bank_root / "procedural_test.jsonl"),
            }
        ),
        "tasks": {
            "sft": len(sft_tasks),
            "fresh_refinement": len(refinement_tasks),
            "selection_shadow": len(selection),
            "procedural_test": len(final),
        },
        "raw_rows": {
            name: {"path": str(path), "sha256": sha256_file(path), "rows": len(value)}
            for (name, path), value in zip(
                files.items(), (contract_rows, interaction_rows, rehearsal_rows), strict=True
            )
        },
        "split_manifest_body_sha256": split_manifest["manifest_body_sha256"],
        "refinement_inventory_sha256": sha256_file(refinement_inventory),
        "refinement_contracts_sha256": sha256_file(refinement_contracts),
        "selection_inventory_sha256": sha256_file(selection_inventory),
        "selection_contracts_sha256": sha256_file(selection_contracts),
        "selection_checkpoint_tasks_sha256": sha256_file(selection_rehearsals),
        "verified_checkpoint_rehearsals": len(rehearsal_rows),
        "generator_oracle_serialized_into_model_visible_rows": False,
        "leakage_audit": audit,
    }
    raw_manifest_path = publish_json(raw / "manifest.json", raw_manifest)
    candidates: dict[str, Any] = {}
    for candidate in ("balanced", "protocol-heavy", "recovery-heavy"):
        candidate_manifest = materialize_targeted_sft(
            campaign,
            candidate=candidate,
            split_manifest_path=output / "splits/manifest.json",
            contract_path=files["contract"],
            interaction_path=files["interaction"],
            rehearsal_path=files["rehearsal"],
            output_dir=output / "candidates" / candidate,
        )
        if candidate_manifest["output"]["rows"] != 4800:
            raise ArtifactError(f"targeted SFT candidate is not exactly 4,800 rows: {candidate}")
        candidates[candidate] = candidate_manifest
    result = {
        "schema": "caveat-27b.procedural-campaign-materialization.v1",
        "campaign_digest": campaign.digest,
        "raw_manifest": str(raw_manifest_path),
        "raw_manifest_sha256": sha256_file(raw_manifest_path),
        "candidate_rows": {
            name: value["output"]["rows"] for name, value in candidates.items()
        },
        "candidate_counts": {
            name: value["realized_counts"] for name, value in candidates.items()
        },
        "status": "ready_for_exact_prime_rendering_audit",
    }
    publish_json(output / "manifest.json", result)
    return result
