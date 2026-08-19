from __future__ import annotations

from pathlib import Path

import pytest

from harness_posttrain_eval.common import IntegrityError, canonical_bytes, sha256_bytes
from harness_posttrain_eval.fixed_v7_grpo_fast_eval import (
    PREREG_SCHEMA,
    SOURCE_GIT_SHA,
    _absolute_without_symlink_dereference,
    _sign_pvalue,
    _trajectory_diagnostics,
    audit_preregistration,
)


def _write(path: Path, value: object) -> None:
    path.write_bytes(canonical_bytes(value) + b"\n")


def _preregistration() -> dict[str, object]:
    core = {
        "schema": PREREG_SCHEMA,
        "outcomes_read_during_preregistration": False,
        "clean_condition_included": False,
        "office_chair_included": False,
        "model_selection_eligible": False,
        "candidate": {"execution_source_git_sha": SOURCE_GIT_SHA},
        "cell_inventory": [
            {"cell": ["laptop", variant, "combined", repetition]}
            for variant in ("graded", "graded3", "graded4", "mixed")
            for repetition in (0, 1)
        ],
    }
    return {
        **core,
        "preregistration_sha256": sha256_bytes(canonical_bytes(core)),
    }


def test_preregistration_is_self_hashed_and_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "preregistration.json"
    value = _preregistration()
    _write(path, value)
    assert (
        audit_preregistration(path)["preregistration_sha256"]
        == value["preregistration_sha256"]
    )

    value["clean_condition_included"] = True
    _write(path, value)
    with pytest.raises(IntegrityError):
        audit_preregistration(path)


def test_trajectory_diagnostics_bind_hero_and_addon(tmp_path: Path) -> None:
    path = tmp_path / "trajectory.json"
    _write(
        path,
        {
            "evaluation": {
                "chosen": "EXP-LAPTOP-50",
                "outcome": "compliant",
                "details": {
                    "basket": {
                        "line_items": [
                            {"asin": "EXP-LAPTOP-50"},
                            {"asin": "ADDON-PLAN"},
                        ]
                    }
                },
            },
            "steps": [{"url": "http://store/dp/EXP-LAPTOP-50", "action": ""}],
        },
    )
    result = _trajectory_diagnostics(path)
    assert result == {
        "hero_opened": True,
        "hero_chosen": True,
        "addon_present_in_final_basket": True,
        "final_basket_asins": ["EXP-LAPTOP-50", "ADDON-PLAN"],
        "valid_transaction": True,
        "outcome": "compliant",
    }


def test_one_sided_sign_test_is_exact() -> None:
    assert _sign_pvalue([0, 0]) == 1.0
    assert _sign_pvalue([1, 1, -1]) == 0.5


def test_python_argv_path_preserves_virtualenv_symlink(tmp_path: Path) -> None:
    target = tmp_path / "system-python"
    target.touch()
    virtualenv_python = tmp_path / "venv" / "bin" / "python"
    virtualenv_python.parent.mkdir(parents=True)
    virtualenv_python.symlink_to(target)

    observed = _absolute_without_symlink_dereference(virtualenv_python)

    assert observed == virtualenv_python.absolute()
    assert observed != virtualenv_python.resolve()
