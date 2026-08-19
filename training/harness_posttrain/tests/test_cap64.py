from __future__ import annotations

from pathlib import Path

from harness_posttrain.cap64 import check_cap64_helper, install_cap64_helper


def test_cap64_helper_is_exact_two_change_transform(tmp_path: Path) -> None:
    package_root = Path(__file__).resolve().parents[1]
    shared = package_root.parents[1] / "cluster/b200/b200"
    destination = tmp_path / "private/b200"
    manifest = install_cap64_helper(source=shared, destination=destination)
    assert manifest["hard_gpu_cap"] == 64
    assert manifest["reverse_transform_equals_source"] is True
    assert destination.read_bytes().count(b"HARD_GPU_CAP=64\n") == 1
    assert destination.read_bytes().count(b"acquire_submission_lock()") == 1
    assert check_cap64_helper(source=shared, destination=destination) == manifest
