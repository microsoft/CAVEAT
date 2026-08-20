"""Fast, exact contract-compiler data from public labeled task specifications."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    canonical_json,
    publish_json,
    publish_jsonl,
    read_jsonl,
    sha256_file,
)
from .config import Campaign
from .sft_data import SFT_SOURCE_SCHEMA
from .splits import load_split_manifest, require_training_membership

QUICK_SPEC_SCHEMA = "caveat-27b.quick-contract-spec.v1"

# Exact semantics of the unchanged harness compiler. Keeping this in the data
# artifact, rather than modifying the harness, lets the trained model learn the
# interface it will actually encounter at evaluation.
CONTRACT_SYSTEM_PROMPT = (
    "Compile the raw instruction into a literal TaskContract. Use a constraint only for a "
    "mandatory property of each candidate option before any action; an objective is only an "
    "explicitly requested minimization or maximization of candidate options. Directives about "
    "the transaction itself—including how many units to buy and whether or when to submit an "
    "order—are not candidate properties: do not emit them as constraints or objectives. "
    "Intrinsic option attributes such as pack size, capacity, availability, and delivery or "
    "arrival time remain candidate properties when the instruction uses them to choose. Never "
    "invent criteria, facts, thresholds, priorities, weights, or units. List order and mere "
    "mention do not imply priority. Use priority only for an explicit ordinal ranking (1 is "
    "highest), and weight only for a literal numeric weight. Co-equal objectives have null "
    "priority and weight. Use best_available for any comparative or extremal request; use "
    "satisfice only when any qualifying option is enough. Units apply only to numeric criteria "
    "and criterion IDs must be stable, concise, and unique."
)


def _contract(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ArtifactError(f"{label}.contract must be a mapping")
    result = dict(value)
    constraints = result.get("constraints")
    objectives = result.get("objectives")
    mode = result.get("search_mode")
    if not isinstance(constraints, list) or not isinstance(objectives, list):
        raise ArtifactError(f"{label}.contract requires constraint and objective lists")
    if mode not in {"best_available", "satisfice"}:
        raise ArtifactError(f"{label}.contract has invalid search_mode")
    criterion_ids: list[str] = []
    for section, rows in (("constraints", constraints), ("objectives", objectives)):
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping) or not isinstance(row.get("criterion_id"), str):
                raise ArtifactError(f"{label}.contract.{section}[{index}] is malformed")
            criterion_ids.append(str(row["criterion_id"]))
    if len(criterion_ids) != len(set(criterion_ids)):
        raise ArtifactError(f"{label}.contract criterion IDs must be unique")
    if objectives and mode != "best_available":
        raise ArtifactError(f"{label}.contract with objectives must use best_available")
    return result


def build_quick_contract_data(
    campaign: Campaign,
    *,
    split_manifest_path: str | Path,
    specs_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    _manifest, membership = load_split_manifest(campaign, split_manifest_path)
    contract_rows: list[dict[str, Any]] = []
    interaction_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row_number, row in enumerate(read_jsonl(specs_path), 1):
        label = f"quick contract spec {row_number}"
        if row.get("schema") != QUICK_SPEC_SCHEMA:
            raise ArtifactError(f"{label}: unsupported schema")
        strings: dict[str, str] = {}
        for key in ("task_id", "source", "scenario", "instruction"):
            value = row.get(key)
            if not isinstance(value, str) or not value:
                raise ArtifactError(f"{label}.{key} must be a nonempty string")
            strings[key] = value
        if strings["source"] != "procedural":
            raise ArtifactError(f"{label}.source must be procedural; CAVEAT-Shop facts are forbidden")
        if strings["task_id"] in seen:
            raise ArtifactError(f"duplicate quick task_id: {strings['task_id']}")
        seen.add(strings["task_id"])
        require_training_membership(
            membership,
            task_id=strings["task_id"],
            source=strings["source"],
            scenario=strings["scenario"],
        )
        target = canonical_json(_contract(row.get("contract"), label))
        base = {
            "schema": SFT_SOURCE_SCHEMA,
            "task_id": strings["task_id"],
            "source": strings["source"],
            "scenario": strings["scenario"],
            "tools": [],
        }
        contract_rows.append(
            {
                **base,
                "sample_id": f"contract:{strings['task_id']}:direct",
                "messages": [
                    {"role": "system", "content": CONTRACT_SYSTEM_PROMPT},
                    {"role": "user", "content": strings["instruction"]},
                    {"role": "assistant", "content": target},
                ],
            }
        )
        interaction_rows.append(
            {
                **base,
                "sample_id": f"contract:{strings['task_id']}:repair",
                "messages": [
                    {"role": "system", "content": CONTRACT_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            strings["instruction"]
                            + "\n\nThe prior draft was invalid. Correct it without adding anything "
                            "to the instruction. Validation error: malformed structured JSON"
                        ),
                    },
                    {"role": "assistant", "content": target},
                ],
            }
        )
    if not contract_rows:
        raise ArtifactError("quick contract specification file is empty")
    output = Path(output_dir).resolve()
    direct = publish_jsonl(output / "contract.jsonl", contract_rows)
    repair = publish_jsonl(output / "interaction.jsonl", interaction_rows)
    manifest = {
        "schema": "caveat-27b.quick-contract-data.v1",
        "campaign_digest": campaign.digest,
        "split_manifest_sha256": sha256_file(split_manifest_path),
        "specs_sha256": sha256_file(specs_path),
        "tasks": len(seen),
        "contract": {
            "path": direct.name,
            "sha256": sha256_file(direct),
            "rows": len(contract_rows),
        },
        "interaction": {
            "path": repair.name,
            "sha256": sha256_file(repair),
            "rows": len(interaction_rows),
        },
        "contains_evaluator_labels": False,
    }
    publish_json(output / "manifest.json", manifest)
    return manifest
