from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _load():
    path = HERE / "campaign.py"
    spec = importlib.util.spec_from_file_location("_test_mode_evidence_campaign", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_exact_120_run_five_block_schedule() -> None:
    campaign = _load()
    rows = campaign.build_schedule("mode_test", 20000)
    assert len(rows) == 120
    assert len({row["run_id"] for row in rows}) == 120
    assert Counter(row["block"] for row in rows) == {
        1: 30,
        2: 20,
        3: 30,
        4: 20,
        5: 20,
    }
    assert Counter(row["study"] for row in rows) == {
        "objective_order": 50,
        "frontier_component": 30,
        "isolated_steering": 40,
    }
    for block, expected in {1: 30, 2: 20, 3: 30, 4: 20, 5: 20}.items():
        block_rows = [row for row in rows if row["block"] == block]
        assert {row["spawn_index"] for row in block_rows} == set(range(expected))
        assert {row["port"] for row in block_rows} == set(range(20000, 20000 + expected))


def test_matched_pairs_triples_and_denominators() -> None:
    campaign = _load()
    rows = campaign.build_schedule("mode_test", 20000)
    objective = [row for row in rows if row["study"] == "objective_order"]
    assert Counter(row["objective_order"] for row in objective) == {
        "original": 25,
        "reversed": 25,
    }
    for repeat in range(1, 6):
        for scenario in campaign.HARD_SCENARIOS:
            pair = [row for row in objective if row["repeat"] == repeat and row["scenario"] == scenario]
            assert len(pair) == 2
            assert {row["objective_order"] for row in pair} == {"original", "reversed"}
            assert pair[0]["region_order"] == pair[1]["region_order"]
            assert pair[0]["primary_region"] == pair[1]["primary_region"]

    frontier = [row for row in rows if row["study"] == "frontier_component"]
    assert Counter(row["arm"] for row in frontier) == {
        "prompt_only": 10,
        "no_coverage": 10,
        "full": 10,
    }
    for repeat in range(1, 6):
        for scenario in campaign.FRONTIER_SCENARIOS:
            triple = [row for row in frontier if row["repeat"] == repeat and row["scenario"] == scenario]
            assert len(triple) == 3
            assert len({tuple(row["region_order"]) for row in triple}) == 1


def test_terra_conditions_and_routes_are_balanced() -> None:
    campaign = _load()
    rows = campaign.build_schedule("mode_test", 20000)
    steering = [row for row in rows if row["study"] == "isolated_steering"]
    assert Counter(row["condition"] for row in steering) == {
        condition: 8 for condition in campaign.STEERING_CONDITIONS
    }
    for repeat in range(1, 9):
        assert {
            row["condition"] for row in steering if row["repeat"] == repeat
        } == set(campaign.STEERING_CONDITIONS)
    for block in (4, 5):
        loads = Counter(
            row["primary_region"] for row in steering if row["block"] == block
        )
        assert set(loads) == set(campaign.REGIONS)
        assert max(loads.values()) - min(loads.values()) <= 1


def test_sidecar_matches_canonical_without_editing_tasks() -> None:
    campaign = _load()
    ref = campaign._validate_sidecar()
    payload = json.loads(campaign.SIDECAR_PATH.read_text())
    assert ref["sha256"] == campaign._sha_file(campaign.SIDECAR_PATH)
    assert set(payload["entries"]) == set(campaign.HARD_SCENARIOS)
    for scenario, entry in payload["entries"].items():
        canonical = json.loads(
            (ROOT / entry["source_path"]).read_text()
        )["graded"]["text"]
        assert entry["original"] == canonical
        assert entry["reversed"] != canonical
        assert entry["objectives_reversed"] == list(
            reversed(entry["objectives_original"])
        )


def test_static_verification_and_command_local_components() -> None:
    campaign = _load()
    result = campaign.static_verify()
    assert result["runs"] == 120
    assert result["protocol"]["codebook"]["sha256"] == campaign.CODEBOOK_SHA256
    assert result["registration"]["no_coverage"] == "no-coverage-v1"
    assert not (HERE / "no_coverage_scaffold.py").exists()
    assert not (HERE / "sitecustomize.py").exists()


def test_launch_override_respects_global_v19_cap() -> None:
    campaign = _load()
    prereg = json.loads(campaign.PREREG_PATH.read_text())
    policy = prereg["launch_concurrency"]
    assert policy["v19_launch_only_max_parallel_runs"] == 4
    assert campaign.MAX_PARALLEL_RUNS == 15
    assert campaign.MAX_PARALLEL_RUNS == campaign.CAPS["max_concurrent_browsers"]
    assert policy["reuse_every_v19_per_run_safety_context_time_and_step_cap_exactly"] is True
    assert policy["claim_exact_v19_launch_plumbing"] is False


def test_run_one_cli_has_one_run_id_argument() -> None:
    completed = subprocess.run(
        [sys.executable, str(HERE / "campaign.py"), "run-one", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stdout.count("--run-id") == 2  # usage plus option table
    assert (HERE / "campaign.py").read_text().count(
        'run.add_argument("--run-id"'
    ) == 1


def test_static_verification_never_creates_launch_evidence() -> None:
    campaign = _load()
    campaign.static_verify()
    assert not campaign.DEFAULT_CAMPAIGN.exists()
