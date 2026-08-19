from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_fixed_v6_training_shell_delegates_preparation_and_receipt() -> None:
    root = Path(__file__).parents[1]
    script = root / "scripts/train_browser_action_fixed_v6.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)

    text = script.read_text(encoding="utf-8")
    assert os.access(script, os.X_OK)
    assert 'stage="$target/browser_action_fixed_v6/$artifact_sha"' in text
    assert "prepare-browser-action-fixed-v6" in text
    assert '--output "$stage"' in text
    assert "--num-gpus 4" in text
    assert 'config="$stage/training/browser_action_fixed_v6.toml"' in text
    assert 'sft @ "$config"' in text
    assert "weights/step_24/lora_adapters" in text
    assert "adapter_model.safetensors" in text
    assert "write-browser-action-fixed-v6-receipt" in text
    assert '--artifact-source-git-sha "$artifact_sha"' in text
    assert '--execution-source-git-sha "$source_sha"' in text
    assert 'receipt="$stage/training/training_receipt.json"' in text
    # The preparation module owns the safe hardlink construction and audit.
    assert "cp -" not in text
    assert "ln " not in text


def test_fixed_v6_launcher_is_immutable_and_schedulable() -> None:
    root = Path(__file__).parents[1]
    script = root / "scripts/launch_browser_action_fixed_v6.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)

    text = script.read_text(encoding="utf-8")
    assert os.access(script, os.X_OK)
    assert "@sha256:" in text
    assert '[[ -z "$(git -C "$root" status --porcelain --untracked-files=all)" ]]' in text
    assert 'source_sha="$(git -C "$root" rev-parse HEAD)"' in text
    assert 'artifact_sha="${HPT_ACTION_ARTIFACT_SOURCE_SHA:-$source_sha}"' in text
    assert "NODES=1 GPUS_PER_NODE=4 NPROC_PER_NODE=4" in text
    assert "CPU_REQUESTS=8 MEMORY_REQUESTS=256Gi RDMA_REQUESTS=4" in text
    assert "B200_PRIORITY=p0 B200_WORKSTREAM=socialreasoning B200_MAX_GPUS=64" in text
    assert 'CONTAINER_IMAGE_PATH="$image"' in text
    assert 'HPT_EXECUTION_SOURCE_GIT_SHA="$source_sha"' in text
    assert 'HPT_ARTIFACT_SOURCE_GIT_SHA="$artifact_sha"' in text
    assert 'bash scripts/train_browser_action_fixed_v6.sh "$phase" "$target"' in text
