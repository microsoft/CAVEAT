from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from harness_posttrain.artifacts import ArtifactError
from harness_posttrain.browser_action_fixed_v5 import (
    _fixed_toml,
    _token_mix_audit,
    _with_visible_source_urls,
)


def test_fixed_config_is_one_candidate_constant_lr(tmp_path: Path) -> None:
    payload = _fixed_toml(
        model=tmp_path / "model",
        dataset=tmp_path / "data",
        output=tmp_path / "output",
        targets=["^q_proj$"],
        settings={
            "sequence_length": 32768,
            "global_batch_size": 16,
            "lora_rank": 64,
            "lora_alpha": 128,
            "lora_dropout": 0,
            "maximum_gradient_norm": 1,
        },
        seed=7,
        num_gpus=4,
    )
    config = tomllib.loads(payload.decode())
    assert config["max_steps"] == 26
    assert config["optim"]["lr"] == pytest.approx(1e-5)
    assert config["scheduler"] == {
        "type": "cosine",
        "warmup_steps": 0,
        "min_lr": pytest.approx(1e-5),
    }
    assert config["ckpt"]["resume_step"] == 20
    assert config["ckpt"]["interval"] == 6
    assert config["ckpt"]["skip_optimizer"] is True
    assert config["ckpt"]["skip_scheduler"] is True
    assert config["ckpt"]["skip_dataloader"] is True
    assert config["ckpt"]["skip_progress"] is False


def test_visible_source_urls_are_added_to_every_catalog_item() -> None:
    state = {
        "instruction": "Choose the best item.",
        "catalog": [{"item_id": "a"}, {"item_id": "b"}],
    }
    row = {
        "messages": [
            {"role": "system", "content": "x"},
            {
                "role": "user",
                "content": "prefix\nPublic marketplace state:\n" + json.dumps(state),
            },
            {"role": "assistant", "content": "target"},
        ]
    }
    result = _with_visible_source_urls(row)
    serialized = result["messages"][-2]["content"].split("Public marketplace state:\n", 1)[1]
    visible = json.loads(serialized)
    assert [item["source_url"] for item in visible["catalog"]] == [
        "https://shop.local/products/a",
        "https://shop.local/products/b",
    ]
    assert "source_url" not in row["messages"][-2]["content"]


def test_exact_token_mix_gate_uses_rendered_tokens_not_rows(tmp_path: Path) -> None:
    stages = {
        "checkpoint-complete": 400,
        "repair-rejected": 400,
        "continue-incomplete": 50,
        "act-after-approval": 50,
        "contract-replay": 100,
    }
    rows = []
    audit = []
    for index, (stage, tokens) in enumerate(stages.items(), 1):
        rows.append({"metadata": {"stage": stage}})
        audit.append({"line": index, "rendered_tokens": tokens})
    (tmp_path / "curriculum").mkdir()
    (tmp_path / "prime").mkdir()
    (tmp_path / "curriculum/train.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    (tmp_path / "prime/token_audit_rows.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in audit), encoding="utf-8"
    )
    result = _token_mix_audit(tmp_path)
    assert result["gate_passed"] is True
    assert result["fractions"]["checkpoint_and_repair"] == pytest.approx(0.8)

    audit[-1]["rendered_tokens"] = 10_000
    (tmp_path / "prime/token_audit_rows.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in audit), encoding="utf-8"
    )
    with pytest.raises(ArtifactError, match="outside frozen bounds"):
        _token_mix_audit(tmp_path)
