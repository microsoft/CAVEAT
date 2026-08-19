from __future__ import annotations

import json

import pytest
from conftest import REPOSITORY_ROOT

from harness_posttrain_eval.common import IntegrityError
from harness_posttrain_eval.manifest import (
    ORIGINAL_SCENARIOS,
    SCENARIO_DATA_FILES,
    benchmark_lock,
    freeze_manifest,
    verify_manifest,
)
from harness_posttrain_eval.split import generate_split


def test_manifest_is_create_only_and_detects_source_drift(config, tmp_path):
    split_path = tmp_path / "split.json"
    split_path.write_text(json.dumps(generate_split(config)), encoding="utf-8")
    output = tmp_path / "manifest.json"
    manifest = freeze_manifest(
        root=REPOSITORY_ROOT,
        config_path=REPOSITORY_ROOT
        / "training/harness_posttrain_eval/configs/campaign.json",
        split_path=split_path,
        output_path=output,
        base_weight_sha256="1" * 64,
        trained_weight_sha256="2" * 64,
        tokenizer_sha256="3" * 64,
        chat_template_sha256="4" * 64,
        container_image_digest="sha256:" + "5" * 64,
        serving_stack={"engine": "vllm", "version": "test"},
    )
    verify_manifest(manifest, root=REPOSITORY_ROOT)
    with pytest.raises(IntegrityError, match="refusing to overwrite"):
        freeze_manifest(
            root=REPOSITORY_ROOT,
            config_path=REPOSITORY_ROOT
            / "training/harness_posttrain_eval/configs/campaign.json",
            split_path=split_path,
            output_path=output,
            base_weight_sha256="1" * 64,
            trained_weight_sha256="2" * 64,
            tokenizer_sha256="3" * 64,
            chat_template_sha256="4" * 64,
            container_image_digest="sha256:" + "5" * 64,
            serving_stack={"engine": "vllm", "version": "test"},
        )


def test_benchmark_lock_uses_authoritative_scenario_artifacts():
    lock = benchmark_lock(REPOSITORY_ROOT)
    files = set(lock["files"])
    for scenario in ORIGINAL_SCENARIOS:
        for name in SCENARIO_DATA_FILES:
            assert f"benchmark_data/amazon/{scenario}/{name}" in files
        assert f"agentarena/envs/amazon/server/_catalogs/{scenario}.json" not in files
    assert "benchmark_data/amazon/laptop/ai_injection.json" in files
    assert "agentarena/envs/amazon/server/backend/routes.py" in files
    assert "agentarena/scoring/rescore.py" in files
