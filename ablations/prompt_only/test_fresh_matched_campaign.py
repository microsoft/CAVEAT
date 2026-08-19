from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent


def _load():
    path = HERE / "fresh_matched_campaign.py"
    spec = importlib.util.spec_from_file_location(
        "_test_fresh_matched_campaign", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _parent(module) -> dict:
    rows = []
    spawn_index = 0
    for repeat in (1, 2, 3):
        for cohort, scenarios, request, recorded, logical in (
            (
                "weak_easy_combined",
                module.EASY_SCENARIOS,
                "gpt-5.6-terra#low",
                "gpt-5.6-terra-low",
                "gpt-5.6-terra",
            ),
            (
                "sol_high_hard",
                module.HARD_SCENARIOS,
                "gpt-5.6-sol#high",
                "gpt-5.6-sol-high",
                "gpt-5.6-sol",
            ),
        ):
            for index, scenario in enumerate(scenarios):
                offset = index % len(module.base.REGIONS)
                order = list(
                    module.base.REGIONS[offset:]
                    + module.base.REGIONS[:offset]
                )
                rows.append({
                    "arm": "baseline",
                    "scaffold": "browseruse",
                    "cohort": cohort,
                    "repeat": repeat,
                    "scenario": scenario,
                    "variant": "graded",
                    "condition": "combined",
                    "model_request": request,
                    "model_recorded": recorded,
                    "logical_model": logical,
                    "primary_region": order[0],
                    "region_order": order,
                    "run_id": f"{cohort}_{repeat}_{scenario}",
                    "spawn_index": spawn_index,
                })
                spawn_index += 1
    return {"schedule": rows}


def test_prompt_remains_byte_identical() -> None:
    module = _load()
    prereg = json.loads(module.PREREG_PATH.read_text())
    assert hashlib.sha256(
        prereg["prompt"].encode("utf-8")
    ).hexdigest() == module.PROMPT_SHA256


def test_schedule_is_exactly_twenty_pair_matched_rows(monkeypatch) -> None:
    module = _load()
    parent = _parent(module)
    monkeypatch.setattr(module, "_parent_manifest", lambda _path: parent)
    module._ACTIVE_PARENT = Path("/synthetic")
    rows = module._build_schedule("fresh_prompt_test", 21000)
    selected = {
        row["run_id"]: row
        for row in module._selected_parent_rows(parent)
    }
    assert len(rows) == 20
    assert len({row["matched_parent_baseline_run_id"] for row in rows}) == 20
    assert {row["repeat"] for row in rows} == {1, 2}
    assert {row["port"] for row in rows} == set(range(21000, 21010))
    for row in rows:
        source = selected[row["matched_parent_baseline_run_id"]]
        assert row["region_order"] == source["region_order"]
        assert row["model_request"] == source["model_request"]
        assert row["scenario"] == source["scenario"]
        assert row["scaffold"] == "browseruse-prompt-only"


def test_preregistration_excludes_nonmatched_parent_strata() -> None:
    module = _load()
    design = json.loads(module.PREREG_PATH.read_text())["design"]
    assert design["shared_fresh_baseline_runs"] == 20
    assert design["new_prompt_only_runs"] == 20
    assert design["total_ab_runs"] == 40
    assert "weak_easy_clean_repeat_1" in design["excluded_parent_rows"]
    assert "weak_easy_combined_repeat_3" in design["excluded_parent_rows"]
