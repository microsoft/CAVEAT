from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness_posttrain import browser_action_fixed_v6_serving as serving
from harness_posttrain.artifacts import ArtifactError, sha256_bytes, sha256_file


def _identity(path: Path, value: str) -> dict[str, Any]:
    return {"path": str(path.resolve()), "files": 2, "bytes": 7, "tree_sha256": value}


def test_pair_manifest_is_pre_gate_and_binds_distinct_exact_loras(
    tmp_path: Path, monkeypatch: Any
) -> None:
    campaign_sha, artifact_sha, execution_sha = "a" * 40, "b" * 40, "c" * 40
    root = tmp_path / campaign_sha
    stage = root / "browser_action_fixed_v6" / artifact_sha
    receipt_path = stage / "training/training_receipt.json"
    parent, step20, candidate = root / "parent", root / "step20", root / "step24"
    for path in (parent, step20, candidate):
        path.mkdir(parents=True)
    for path in (step20, candidate):
        (path / "adapter_config.json").write_text("{}\n", encoding="utf-8")
        (path / "adapter_model.safetensors").write_bytes(b"weights")
    source = _identity(step20, "1" * 64)
    trained = {
        **_identity(candidate, "2" * 64),
        "name": "step24",
        "update": 24,
        "adapter_config_sha256": sha256_file(candidate / "adapter_config.json"),
    }
    body = {
        "schema": serving.FIXED_V6_RECEIPT_SCHEMA,
        "status": "ok",
        "artifact_source_git_sha": artifact_sha,
        "execution_source_git_sha": artifact_sha,
        "parent_model": str(parent),
        "source_step20_adapter": source,
        "candidate": trained,
        "optimizer_updates": 24,
        "new_optimizer_updates": 4,
        "checkpoint_updates": [24],
        "selection_performed": False,
        "amazon_outcomes_consulted": False,
    }
    receipt = {
        **body,
        "receipt_body_sha256": sha256_bytes(
            serving.canonical_json(body).encode()
        ),
    }
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")

    receipt_before = receipt_path.read_bytes()
    writer_calls: list[dict[str, Any]] = []

    def validate_receipt(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        writer_calls.append(kwargs)
        return receipt

    monkeypatch.setattr(serving, "write_browser_action_fixed_v6_receipt", validate_receipt)
    monkeypatch.setattr(serving, "_verified_raw_base", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        serving,
        "component_identity",
        lambda path: {
            str(step20.resolve()): {key: value for key, value in source.items() if key != "bytes"},
            str(candidate.resolve()): {
                key: value for key, value in trained.items() if key != "bytes"
            },
            str(parent.resolve()): {
                key: value
                for key, value in _identity(parent, "3" * 64).items()
                if key != "bytes"
            },
        }[str(path.resolve())],
    )
    monkeypatch.setattr(
        serving,
        "_tree_identity",
        lambda path: {
            str(step20.resolve()): source,
            str(candidate.resolve()): trained,
        }[str(path.resolve())],
    )
    monkeypatch.setattr(
        serving,
        "_tokenizer_attestation",
        lambda *_args, **_kwargs: {
            "semantic": {
                "shared_tokenizer_json_sha256": "4" * 64,
                "chat_template_sha256": "5" * 64,
            }
        },
    )
    campaign = type("Campaign", (), {"digest": "campaign-digest"})()
    result = serving.prepare_fixed_v6_pair_manifest(
        campaign,
        campaign_root=root,
        training_receipt=receipt_path,
        raw_base=root / "raw",
        artifact_source_git_sha=artifact_sha,
        execution_source_git_sha=execution_sha,
        output_dir=stage / "pre_gate",
    )

    assert result["status"] == "pre_gate_only"
    assert receipt_path.read_bytes() == receipt_before
    assert len(writer_calls) == 1
    assert writer_calls[0]["artifact_source_git_sha"] == artifact_sha
    assert writer_calls[0]["execution_source_git_sha"] == artifact_sha
    assert result["artifact_source_git_sha"] == artifact_sha
    assert result["execution_source_git_sha"] == execution_sha
    assert result["science_policy"] == {
        "outcome_blind": True,
        "selection_performed": False,
        "amazon_outcomes_consulted": False,
        "behavioral_gate_pass_required_before_marketplace_evaluation": True,
        "not_a_final_publication": True,
    }
    assert set(result["arms"]) == {"step20", "fixed_v6"}
    assert result["arms"]["step20"]["composite_sha256"] != result["arms"]["fixed_v6"][
        "composite_sha256"
    ]
    manifest_path = stage / "pre_gate/exact_lora_manifest.json"
    manifest_receipt = json.loads(
        (stage / "pre_gate/exact_lora_manifest_receipt.json").read_text()
    )
    assert manifest_receipt["manifest_sha256"] == sha256_file(manifest_path)
    assert manifest_receipt["behavioral_gate_passed"] is False
    assert manifest_receipt["marketplace_evaluation_authorized"] is False


def test_receipt_identity_uses_byte_complete_tree_not_logical_component(
    tmp_path: Path,
) -> None:
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text("{}\n", encoding="utf-8")
    (adapter / "adapter_model.safetensors").write_bytes(b"weights")

    logical = serving.component_identity(adapter)
    receipt_identity = serving._tree_identity(adapter)
    assert "bytes" not in logical
    assert receipt_identity["bytes"] == sum(
        path.stat().st_size for path in adapter.iterdir()
    )
    with pytest.raises(ArtifactError, match="identity changed: bytes"):
        serving._same_identity(logical, receipt_identity, "adapter")
    serving._same_identity(receipt_identity, receipt_identity, "adapter")


def test_fixed_v6_serve_shell_is_waf_safe_and_paired() -> None:
    root = Path(__file__).parents[1]
    script = root / "scripts/serve_browser_action_fixed_v6.sh"
    text = script.read_text(encoding="utf-8")
    assert "python3 -m harness_posttrain.browser_action_fixed_v6_serving" in text
    assert "--max-loras 2" in text
    assert '"$step20_name=$step20_adapter"' in text
    assert '"$candidate_name=$candidate_adapter"' in text
    assert "--data-parallel-size 4" in text
    assert "eval " not in text
    assert "bash -c" not in text
