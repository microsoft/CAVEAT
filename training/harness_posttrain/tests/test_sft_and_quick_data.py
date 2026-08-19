from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import sft_source, write_jsonl

from harness_posttrain.artifacts import ArtifactError, read_jsonl
from harness_posttrain.config import Campaign
from harness_posttrain.quick_data import build_quick_contract_data
from harness_posttrain.sft_data import materialize_targeted_sft


def test_quick_contract_builder_emits_direct_and_retry_rows(
    tmp_path: Path,
    campaign: Campaign,
    sealed_splits: tuple[Path, list[dict[str, object]]],
) -> None:
    manifest_path, _ = sealed_splits
    spec = {
        "schema": "harness-posttrain.quick-contract-spec.v1",
        "task_id": "procedural-train-1",
        "source": "procedural",
        "scenario": "generic_shop_a",
        "instruction": "Buy the cheapest option with at least 16 units of capacity.",
        "contract": {
            "constraints": [
                {
                    "criterion_id": "ram_gb",
                    "description": "RAM in GB",
                    "operator": "ge",
                    "expected": 16,
                    "unit": "GB",
                }
            ],
            "objectives": [
                {
                    "criterion_id": "price",
                    "description": "price",
                    "direction": "minimize",
                    "unit": "USD",
                    "priority": None,
                    "weight": None,
                }
            ],
            "search_mode": "best_available",
        },
    }
    specs = write_jsonl(tmp_path / "specs.jsonl", [spec])
    output = tmp_path / "quick"
    result = build_quick_contract_data(
        campaign,
        split_manifest_path=manifest_path,
        specs_path=specs,
        output_dir=output,
    )
    assert result["tasks"] == 1
    direct = read_jsonl(output / "contract.jsonl")[0]
    retry = read_jsonl(output / "interaction.jsonl")[0]
    assert json.loads(direct["messages"][-1]["content"])["search_mode"] == "best_available"
    assert "prior draft was invalid" in retry["messages"][-2]["content"]


def test_targeted_sft_is_deterministic_and_rejects_hidden_labels(
    tmp_path: Path,
    campaign: Campaign,
    sealed_splits: tuple[Path, list[dict[str, object]]],
) -> None:
    manifest_path, _ = sealed_splits
    paths = {
        "contract": write_jsonl(tmp_path / "contract.jsonl", [sft_source("c1")]),
        "interaction": write_jsonl(tmp_path / "interaction.jsonl", [sft_source("i1")]),
        "rehearsal": write_jsonl(tmp_path / "rehearsal.jsonl", [sft_source("r1")]),
    }
    output = tmp_path / "sft"
    first = materialize_targeted_sft(
        campaign,
        candidate="balanced",
        split_manifest_path=manifest_path,
        contract_path=paths["contract"],
        interaction_path=paths["interaction"],
        rehearsal_path=paths["rehearsal"],
        output_dir=output,
    )
    second = materialize_targeted_sft(
        campaign,
        candidate="balanced",
        split_manifest_path=manifest_path,
        contract_path=paths["contract"],
        interaction_path=paths["interaction"],
        rehearsal_path=paths["rehearsal"],
        output_dir=output,
    )
    assert first == second
    assert first["output"]["rows"] == 3

    hidden = sft_source("bad")
    hidden["messages"][-1]["content"] = {"hero_asin": "secret"}
    bad_path = write_jsonl(tmp_path / "bad.jsonl", [hidden])
    with pytest.raises(ArtifactError, match="forbidden evaluator/private field"):
        materialize_targeted_sft(
            campaign,
            candidate="balanced",
            split_manifest_path=manifest_path,
            contract_path=bad_path,
            interaction_path=paths["interaction"],
            rehearsal_path=paths["rehearsal"],
            output_dir=tmp_path / "bad-output",
        )


def test_targeted_sft_rejects_selection_scenario(
    tmp_path: Path,
    campaign: Campaign,
    sealed_splits: tuple[Path, list[dict[str, object]]],
) -> None:
    manifest_path, _ = sealed_splits
    leaked = sft_source(
        "leak",
        task_id="dev-laptop-1",
        source="amazon",
        scenario="laptop",
    )
    leak_path = write_jsonl(tmp_path / "leak.jsonl", [leaked])
    with pytest.raises(ArtifactError, match="conflicts with frozen membership"):
        materialize_targeted_sft(
            campaign,
            candidate="balanced",
            split_manifest_path=manifest_path,
            contract_path=leak_path,
            interaction_path=leak_path,
            rehearsal_path=leak_path,
            output_dir=tmp_path / "output",
        )
