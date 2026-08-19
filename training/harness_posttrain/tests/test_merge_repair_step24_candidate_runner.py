from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/merge_repair_step24_candidate.sh"


def test_repair_merge_runner_is_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(RUNNER)], check=True)


def test_repair_merge_runner_is_fail_closed_and_exact() -> None:
    text = RUNNER.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert '[[ "$#" -eq 12 ]]' in text
    assert '[[ "$python_bin" == "/opt/prime-rl/.venv/bin/python" && -x "$python_bin" ]]' in text
    assert 'resolved_python="$(realpath -e -- "$python_bin")"' in text
    assert 'export LAST_CONTAINER_IMAGE_DIGEST="$container_image_digest"' in text
    assert 'export LAST_SOURCE_GIT_SHA="$executor_git_sha"' in text
    assert "export LAST_ALLOCATED_GPUS=1" in text
    assert 'export PYTHONPATH="$source_root/src${PYTHONPATH:+:$PYTHONPATH}"' in text
    assert 'exec "$python_bin" -m harness_posttrain.repair_step24_candidate_merge' in text
    assert "--device cuda:0" in text


def test_repair_merge_runner_rejects_wrong_arity_without_mutation(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", str(RUNNER), str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert list(tmp_path.iterdir()) == []
