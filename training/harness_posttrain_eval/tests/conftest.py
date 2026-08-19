from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACKAGE_ROOT.parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from harness_posttrain_eval.common import canonical_bytes, sha256_bytes


@pytest.fixture
def config() -> dict:
    return json.loads((PACKAGE_ROOT / "configs/campaign.json").read_text(encoding="utf-8"))


@pytest.fixture
def frozen_manifest(config) -> dict:
    core = {
        "schema_version": 1,
        "campaign": config,
        "source_contract": {"harness_sha256": "a" * 64},
        "inference_contract": {
            "engine": "test",
            "temperature": 0,
            "container_image_digest": "sha256:" + "d" * 64,
        },
        "model_contract": {
            "base_weight_sha256": "b" * 64,
            "trained_weight_sha256": "c" * 64,
        },
    }
    return {**core, "manifest_sha256": sha256_bytes(canonical_bytes(core))}
