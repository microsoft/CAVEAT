from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness_posttrain.config import Campaign
from harness_posttrain.splits import freeze_splits

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def campaign() -> Campaign:
    return Campaign.load(PACKAGE_ROOT / "configs/campaign.yaml")


@pytest.fixture
def sealed_splits(tmp_path: Path, campaign: Campaign) -> tuple[Path, list[dict[str, Any]]]:
    inventory = [
        {"task_id": "dev-laptop-1", "source": "amazon", "scenario": "laptop"},
        {"task_id": "final-chair-1", "source": "amazon", "scenario": "office_chair"},
        {"task_id": "final-tent-1", "source": "amazon", "scenario": "tent"},
        {
            "task_id": "procedural-train-1",
            "source": "procedural",
            "scenario": "generic_shop_a",
            "split": "train",
        },
        {
            "task_id": "procedural-train-2",
            "source": "procedural",
            "scenario": "generic_shop_b",
            "split": "train",
        },
        {
            "task_id": "procedural-select-1",
            "source": "procedural",
            "scenario": "generic_shop_validation",
            "split": "selection",
        },
    ]
    source = write_jsonl(tmp_path / "inventory.jsonl", inventory)
    root = tmp_path / "splits"
    freeze_splits(campaign, inventory_path=source, output_dir=root)
    return root / "manifest.json", inventory


def sft_source(
    sample_id: str,
    *,
    task_id: str = "procedural-train-1",
    source: str = "procedural",
    scenario: str = "generic_shop_a",
    assistant: str = '{"ok":true}',
) -> dict[str, Any]:
    return {
        "schema": "harness-posttrain.sft-source.v1",
        "sample_id": sample_id,
        "task_id": task_id,
        "source": source,
        "scenario": scenario,
        "messages": [
            {"role": "system", "content": "Follow the requested schema."},
            {"role": "user", "content": "Choose carefully."},
            {"role": "assistant", "content": assistant},
        ],
        "tools": [],
    }
