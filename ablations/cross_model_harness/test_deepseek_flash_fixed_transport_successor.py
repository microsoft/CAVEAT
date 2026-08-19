from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent


def _load():
    path = HERE / "deepseek_flash_fixed_transport_successor.py"
    spec = importlib.util.spec_from_file_location(
        "_test_deepseek_fixed_transport", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_schedule_is_full_five_not_selective_refill() -> None:
    module = _load()
    rows = module.build_schedule("deepseek_successor_test", 21100)
    assert len(rows) == 5
    assert {row["scenario"] for row in rows} == set(module.SCENARIOS)
    assert {row["primary_region"] for row in rows} == set(module.REGIONS)
    assert all(
        set(row["region_order"]) == set(module.REGIONS) for row in rows
    )


def test_preregistration_keeps_extract_as_fail_closed_audit() -> None:
    module = _load()
    prereg = json.loads(module.PREREG_PATH.read_text())
    assert prereg["design"]["full_five_scenario_successor"] is True
    assert prereg["design"]["selective_backpack_refill"] is False
    assert prereg["interpretation"]["extract_specific_patch"] is False
    assert (
        prereg["interpretation"][
            "extract_memory_chars_remains_a_fail_closed_audit"
        ]
        is True
    )
    assert (
        prereg["excluded_smoke_policy"][
            "all_lossy_context_limit_touches_must_be_zero"
        ]
        is True
    )
