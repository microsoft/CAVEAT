from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from harness_posttrain import browser_action_finalization as generic
from harness_posttrain import browser_action_fixed_v5_finalization as fixed
from harness_posttrain.artifacts import (
    ArtifactError,
    canonical_json,
    sha256_bytes,
    sha256_file,
)
from harness_posttrain.browser_action_fixed_v5_gate import (
    FIXED_V5_GATE_MANIFEST_SCHEMA,
    FIXED_V5_GATE_POLICY_SCHEMA,
)


def _bound(path: Path, body: dict[str, Any], field: str) -> dict[str, Any]:
    value = {**body, field: sha256_bytes(canonical_json(body).encode())}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    return value


def test_fixed_stage_requires_one_shared_source_sha_stage(tmp_path: Path) -> None:
    root = tmp_path / ("a" * 40)
    stage = root / "browser_action_fixed_v5" / ("b" * 40)
    receipt = stage / "training/training_receipt.json"
    gate = stage / "procedural_gate/gate_manifest.json"
    assert fixed._fixed_stage(root, receipt, gate) == (stage.resolve(), "b" * 40)  # noqa: SLF001
    with pytest.raises(ArtifactError, match="outside"):
        fixed._fixed_stage(  # noqa: SLF001
            root, receipt, root / "browser_action_fixed_v5" / ("c" * 40) / gate.name
        )


def test_gate_descriptor_rehashes_policy_and_evidence(tmp_path: Path) -> None:
    output = tmp_path / "procedural_gate"
    evidence = output / "raw_evidence.jsonl"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("{}\n", encoding="utf-8")
    policy = _bound(
        output / "gate_policy.json",
        {
            "schema": FIXED_V5_GATE_POLICY_SCHEMA,
            "status": "frozen_before_candidate_inference",
            "selection_performed": False,
            "amazon_data_used": False,
        },
        "policy_body_sha256",
    )
    gate = _bound(
        output / "gate_manifest.json",
        {
            "schema": FIXED_V5_GATE_MANIFEST_SCHEMA,
            "status": "complete",
            "selection_performed": False,
            "fixed_candidate": "step26",
            "amazon_data_used": False,
            "passed": True,
            "gate_checks": {"fixed": True},
            "candidate": {"name": "step26-fixed-v5", "update": 26},
            "raw_evidence": {
                "path": str(evidence.resolve()),
                "sha256": sha256_file(evidence),
                "rows": 192,
                "baseline_rows": 96,
                "candidate_rows": 96,
            },
            "gate_policy": str((output / "gate_policy.json").resolve()),
            "gate_policy_sha256": sha256_file(output / "gate_policy.json"),
            "gate_policy_body_sha256": policy["policy_body_sha256"],
        },
        "manifest_body_sha256",
    )
    assert fixed._gate_descriptor(output / "gate_manifest.json") == gate  # noqa: SLF001
    evidence.write_text("changed\n", encoding="utf-8")
    with pytest.raises(ArtifactError, match="frozen checks"):
        fixed._gate_descriptor(output / "gate_manifest.json")  # noqa: SLF001


def test_generic_validator_dispatches_fixed_v5_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = (
        tmp_path
        / "browser_action_fixed_v5"
        / ("b" * 40)
        / "final/exact_lora_manifest.json"
    )
    expected = {"manifest": str(manifest.resolve()), "arm": "trained"}
    monkeypatch.setattr(
        fixed,
        "validate_fixed_v5_serving_manifest",
        lambda path, *, arm: {"manifest": str(Path(path).resolve()), "arm": arm},
    )
    assert generic.validate_browser_action_serving_manifest(
        manifest, arm="trained"
    ) == expected
