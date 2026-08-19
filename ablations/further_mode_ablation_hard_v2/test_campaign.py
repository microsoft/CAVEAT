from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ablations.further_mode_ablation import campaign as v1_campaign
from ablations.further_mode_ablation_hard_v2 import campaign


def test_exact_112_hard_schedule_and_route_matched_repetitions() -> None:
    rows = campaign.build_schedule("test-hard-v2", 18000)
    assert len(rows) == len({row["run_id"] for row in rows}) == 112
    assert Counter(row["block"] for row in rows) == {
        1: 28, 2: 28, 3: 28, 4: 28,
    }
    assert Counter(row["study"] for row in rows) == {
        "harness_component": 64,
        "hard_context": 48,
    }
    assert Counter(row["arm_id"] for row in rows) == {
        spec["arm_id"]: 8 for spec in campaign.HARD_ARM_SPECS
    }
    for repeat in campaign.REPETITIONS:
        selected = [row for row in rows if row["repeat"] == repeat]
        assert len(selected) == 14
        assert len({row["primary_region"] for row in selected}) == 1
        assert len({tuple(row["region_order"]) for row in selected}) == 1
    for arm in {row["arm_id"] for row in rows}:
        counts = Counter(
            row["primary_region"] for row in rows if row["arm_id"] == arm
        )
        assert sorted(counts.values()) == [2, 3, 3]


def test_recovery_preserves_exact_v1_hard_estimand_and_runtime() -> None:
    assert campaign.HARD_ARM_SPECS == v1_campaign.HARD_ARM_SPECS
    assert campaign.HARNESS_SCAFFOLDS == v1_campaign.HARNESS_SCAFFOLDS
    assert campaign.HIGH_REQUEST == v1_campaign.HIGH_REQUEST
    assert campaign.HARD_SCENARIO == v1_campaign.HARD_SCENARIO
    assert campaign.HARD_VARIANT == v1_campaign.HARD_VARIANT
    assert campaign.REGIONS == v1_campaign.REGIONS
    assert campaign.CAPS == v1_campaign.CAPS
    assert campaign.MAX_ATTEMPTS == v1_campaign.MAX_ATTEMPTS == 3
    contracts = campaign._limit_contracts()
    assert contracts["C"]["categories"]["fixed_architecture"][
        "compiler_and_checkpoint_shape"
    ]["configured"]["contract_compile_calls"] == 0
    launch = contracts["default"]["categories"]["launch_only"][
        "campaign_launch"
    ]["configured"]
    assert launch["block_sequence"] == [28, 28, 28, 28]
    assert launch["max_parallel_runs"] == 32
    assert launch["host_concurrent_browser_ceiling"] == 36


def test_v1_block5_is_bound_as_excluded_pilot_and_never_reused() -> None:
    pilot = campaign._excluded_predecessor_pilot()
    recovery_ids = {
        row["run_id"] for row in campaign.build_schedule("test-hard-v2", 18000)
    }
    assert pilot["block"] == 5
    assert pilot["disposition"] == "excluded_pilot_never_reuse_or_retry"
    assert pilot["outcomes_read"] is False
    assert len(pilot["run_ids"]) == 28
    assert recovery_ids.isdisjoint(pilot["run_ids"])


def test_report_is_exact_hard_only_112_denominator(
    tmp_path: Path, monkeypatch,
) -> None:
    schedule = campaign.build_schedule("synthetic-hard-v2", 18000)
    manifest_path = tmp_path / "campaign_manifest.json"
    manifest_path.write_text("{}\n")
    manifest = {
        "campaign_id": "synthetic-hard-v2",
        "schedule": schedule,
        "metric_policy": {"scheduled_denominator": 112},
        "host_capacity": {"excluded": True},
    }

    def fake_result(_campaign_dir, _manifest, row):
        return {
            **row,
            "preservation_strict": 0.0,
            "hero": 0,
            "strict_binary": 0.0,
            "limit_audit_status": "complete_and_untouched",
            "process_endpoints": {},
            "num_steps": 1,
            "seconds": 1.0,
        }

    reports = tmp_path / "reports"
    monkeypatch.setattr(campaign, "verify_campaign", lambda *a, **k: manifest)
    monkeypatch.setattr(campaign, "_validated_result", fake_result)
    monkeypatch.setattr(campaign, "_active_browser_roots", lambda: 0)
    monkeypatch.setattr(
        campaign,
        "_next_report_paths",
        lambda _root: (
            reports / "report_0001.json",
            reports / "report_0001.md",
            reports / "report_0001.sha256.json",
        ),
    )
    report = campaign.build_report(tmp_path)
    assert report["kind"] == campaign.REPORT_KIND
    assert report["scheduled_denominator"] == 112
    assert report["valid_runs"] == 112
    assert len(report["runs"]) == 112
    assert {row["scenario"] for row in report["runs"]} == {"laptop_hard"}
    assert sum(item["n"] for item in report["aggregates"]) == 112
    sidecar = json.loads((reports / "report_0001.sha256.json").read_text())
    assert set(sidecar) == {"json", "markdown"}
