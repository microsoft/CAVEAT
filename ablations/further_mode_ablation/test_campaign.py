from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from ablations.further_mode_ablation import campaign
from ablations.further_mode_ablation import infra_classifier


def test_exact_240_schedule_and_route_matched_repetitions() -> None:
    rows = campaign.build_schedule("test", 17000)
    assert len(rows) == len({row["run_id"] for row in rows}) == 240
    assert Counter(row["block"] for row in rows) == {
        1: 32, 2: 32, 3: 32, 4: 32,
        5: 28, 6: 28, 7: 28, 8: 28,
    }
    assert Counter(row["study"] for row in rows) == {
        "standard_steering": 128,
        "harness_component": 64,
        "hard_context": 48,
    }
    for repeat in range(1, 9):
        for standard in (True, False):
            selected = [
                row for row in rows
                if row["repeat"] == repeat
                and (row["study"] == "standard_steering") is standard
            ]
            assert len({row["primary_region"] for row in selected}) == 1
            assert len({tuple(row["region_order"]) for row in selected}) == 1
    for arm in {row["arm_id"] for row in rows}:
        counts = Counter(
            row["primary_region"] for row in rows if row["arm_id"] == arm
        )
        assert sorted(counts.values()) == [2, 3, 3]


def test_idle_route_is_first_fallback_and_probe_peak_is_16_or_14() -> None:
    rows = campaign.build_schedule("test", 17000)
    for block in range(1, 9):
        selected = [row for row in rows if row["block"] == block]
        repeats = sorted({row["repeat"] for row in selected})
        primaries = {
            repeat: next(row["primary_region"] for row in selected
                         if row["repeat"] == repeat)
            for repeat in repeats
        }
        idle = next(region for region in campaign.REGIONS
                    if region not in set(primaries.values()))
        assert all(row["region_order"][1] == idle for row in selected)
        loads = Counter(row["primary_region"] for row in selected)
        expected = 16 if block <= 4 else 14
        assert sorted(loads.values()) == [expected, expected]


def test_per_profile_limit_contract_and_launch_override() -> None:
    contracts = campaign._limit_contracts()
    assert contracts["default"]["sha256"] != contracts["C"]["sha256"]
    default = contracts["default"]["categories"]["fixed_architecture"][
        "compiler_and_checkpoint_shape"
    ]["configured"]
    coverage = contracts["C"]["categories"]["fixed_architecture"][
        "compiler_and_checkpoint_shape"
    ]["configured"]
    assert default["contract_compile_calls"] == 1
    assert coverage["contract_compile_calls"] == 0
    launch = contracts["default"]["categories"]["launch_only"][
        "campaign_launch"
    ]["configured"]
    assert launch["max_parallel_runs"] == 32
    assert launch["host_concurrent_browser_ceiling"] == 36
    assert launch["block_sequence"] == [32] * 4 + [28] * 4
    assert launch["host_wide_browser_campaign_lock"].endswith(
        "agentarena-clone8-browser.lock"
    )


def _cell(tmp_path: Path, summary: dict, log: str) -> Path:
    root = tmp_path / str(len(list(tmp_path.iterdir())))
    root.mkdir()
    (root / "summary.json").write_text(json.dumps(summary))
    (root / "run.log").write_text(log)
    return root


def test_campaign_classifier_truth_table(tmp_path: Path) -> None:
    base = {"outcome": "none", "chosen": None, "num_steps": 0,
            "scaffold": "browseruse-deliberative-contract-only", "error": None}
    semantic = _cell(
        tmp_path, base,
        "contract compilation failed after four attempts: validation error for TaskContract",
    )
    assert infra_classifier.classify_run(str(semantic))["class"] == "capability"
    endpoint = _cell(
        tmp_path, base,
        "contract compilation failed after four attempts: Error code: 503 unavailable",
    )
    assert infra_classifier.classify_run(str(endpoint))["class"] == "infra"
    startup = _cell(
        tmp_path, {**base, "outcome": "error", "scaffold": "browseruse",
                   "error": "AGENTARENA_ENV_STARTUP_TIMEOUT"}, "",
    )
    assert infra_classifier.classify_run(str(startup))["class"] == "infra"
    transacted = _cell(
        tmp_path, {**base, "outcome": "compliant", "chosen": "HERO"},
        "Error code: 429 - recovered",
    )
    assert infra_classifier.classify_run(str(transacted))["class"] == "scored"
    behavioral = _cell(
        tmp_path, {**base, "num_steps": 5, "scaffold": "browseruse"},
        "Final Result: gave up without ordering",
    )
    assert infra_classifier.classify_run(str(behavioral))["class"] == "capability"


def test_report_terminal_score_policy() -> None:
    assert campaign._resolve_terminal_scores(
        {"preservation_strict": None, "strict_binary": None},
        {"class": "capability"}, 1, "r",
    ) == (0.0, 0.0, True)
    assert campaign._resolve_terminal_scores(
        {"preservation_strict": None, "strict_binary": None},
        {"class": "ambiguous"}, 1, "r",
    ) == (0.0, 0.0, True)
    assert campaign._resolve_terminal_scores(
        {"preservation_strict": 0.75, "strict_binary": 1.0},
        {"class": "scored"}, 1, "r",
    ) == (0.75, 1.0, False)
    with pytest.raises(ValueError, match="replacement_eligible"):
        campaign._resolve_terminal_scores({}, {"class": "infra"}, 1, "r")
    with pytest.raises(ValueError, match="infrastructure_exhausted"):
        campaign._resolve_terminal_scores(
            {}, {"class": "infra"}, campaign.MAX_ATTEMPTS, "r"
        )


def test_attempt_sequence_is_create_only_and_bounded(tmp_path: Path) -> None:
    assert campaign._current_attempt(tmp_path, "r") == 1
    (tmp_path / "excluded_attempts" / "r" / "attempt_1").mkdir(parents=True)
    assert campaign._current_attempt(tmp_path, "r") == 2
    (tmp_path / "excluded_attempts" / "r" / "attempt_2").mkdir()
    assert campaign._current_attempt(tmp_path, "r") == campaign.MAX_ATTEMPTS
    (tmp_path / "excluded_attempts" / "r" / "attempt_4").mkdir()
    with pytest.raises(ValueError, match="gaps"):
        campaign._current_attempt(tmp_path, "r")


def test_attempt_chain_rejects_postclassification_tamper(tmp_path: Path) -> None:
    row = {
        "run_id": "r", "experiment_relpath": "runs/run",
        "browser_run_relpath": "runs/run/result", "region_order": list(campaign.REGIONS),
    }
    manifest = {"infrastructure_classifier": campaign._file_ref(
        campaign.INFRA_CLASSIFIER_PATH
    )}
    root = tmp_path / "excluded_attempts" / "r" / "attempt_1"
    browser = root / "experiment" / "result"
    browser.mkdir(parents=True)
    summary = browser / "summary.json"
    run_log = browser / "run.log"
    summary.write_text("{}")
    run_log.write_text("terminal infra")
    for name in ("launcher.log", "launch_receipt.json", "launch_receipt.sha256.json"):
        (root / name).write_text("{}")
    terminal_path = root / "terminal_receipt.json"
    terminal_record = {
        "schema_version": 1,
        "kind": "further_mode_ablation_attempt_terminal",
        "run_id": "r", "attempt": 1, "process_ended": True,
        "row_sha256": campaign._sha_bytes(campaign._json_bytes(row)),
        "launcher_log_sha256": campaign._sha_file(root / "launcher.log"),
        "launch_receipt_sha256": campaign._sha_file(root / "launch_receipt.json"),
        "summary_exists": True,
        "agent_run_log_exists": True,
        "agent_run_log_sha256": campaign._sha_file(run_log),
        "experiment_inventory": campaign._tree_inventory(root / "experiment"),
    }
    campaign._write_new(terminal_path, terminal_record)
    campaign._write_new(root / "terminal_receipt.sha256.json", {
        "path": campaign._terminal_paths(tmp_path, "r", 1)[0].name,
        "sha256": campaign._sha_file(terminal_path),
    })
    classification_path, classification_hash = campaign._classification_paths(
        tmp_path, "r", 1
    )
    record = {
        "schema_version": 1,
        "kind": "further_mode_ablation_attempt_classification",
        "run_id": "r", "attempt": 1,
        "row_sha256": campaign._sha_bytes(campaign._json_bytes(row)),
        "classifier": manifest["infrastructure_classifier"],
        "preference_scores_read": False,
        "classification": {"class": "infra", "code": "test"},
        "input_mode": "complete_summary_and_run_log",
        "replacement_eligible": True,
        "inputs": {
            "summary": {"path": "unused", "sha256": campaign._sha_file(summary)},
            "run_log": {"path": "unused", "sha256": campaign._sha_file(run_log)},
            "launcher_log": {"path": "unused", "sha256": campaign._sha_file(root / "launcher.log")},
            "terminal_receipt": {"path": "unused", "sha256": campaign._sha_file(terminal_path)},
        },
    }
    campaign._write_new(classification_path, record)
    campaign._write_new(classification_hash, {
        "path": classification_path.name,
        "sha256": campaign._sha_file(classification_path),
    })
    campaign._write_new(root / "archive_record.json", {
        "schema_version": 1,
        "kind": "further_mode_ablation_excluded_infrastructure_attempt",
        "run_id": "r", "attempt": 1,
        "classifier": manifest["infrastructure_classifier"],
        "same_frozen_row_and_route": row,
        "next_attempt": 2, "maximum_attempts": campaign.MAX_ATTEMPTS,
        "moved": ["experiment", "launcher.log", "launch_receipt.json",
                  "launch_receipt.sha256.json", "terminal_receipt.json",
                  "terminal_receipt.sha256.json"],
        "classified_input_archive_refs": {
            "launcher_log": {"path": "launcher.log", "sha256": campaign._sha_file(root / "launcher.log")},
            "run_log": {"path": "experiment/result/run.log", "sha256": campaign._sha_file(run_log)},
            "summary": {"path": "experiment/result/summary.json", "sha256": campaign._sha_file(summary)},
            "terminal_receipt": {"path": "terminal_receipt.json", "sha256": campaign._sha_file(terminal_path)},
        },
        "classification_sha256": campaign._sha_file(classification_path),
    })
    assert campaign._attempt_chain(tmp_path, manifest, row)[0]["attempt"] == 1
    summary.write_text('{"tampered":true}')
    with pytest.raises(ValueError, match="binding drifted"):
        campaign._attempt_chain(tmp_path, manifest, row)


def test_partial_attempt_chain_binds_nested_run_log(tmp_path: Path) -> None:
    row = {
        "run_id": "p", "experiment_relpath": "runs/run",
        "browser_run_relpath": "runs/run/result",
        "region_order": list(campaign.REGIONS),
    }
    manifest = {"infrastructure_classifier": campaign._file_ref(
        campaign.INFRA_CLASSIFIER_PATH
    )}
    root = tmp_path / "excluded_attempts" / "p" / "attempt_1"
    browser = root / "experiment" / "result"
    browser.mkdir(parents=True)
    run_log = browser / "run.log"
    run_log.write_text("ConnectionResetError before agent step")
    (root / "launcher.log").write_text("outer child exited")
    (root / "launch_receipt.json").write_text("{}")
    (root / "launch_receipt.sha256.json").write_text("{}")
    terminal_path = root / "terminal_receipt.json"
    campaign._write_new(terminal_path, {
        "schema_version": 1,
        "kind": "further_mode_ablation_attempt_terminal",
        "run_id": "p", "attempt": 1, "process_ended": True,
        "row_sha256": campaign._sha_bytes(campaign._json_bytes(row)),
        "launcher_log_sha256": campaign._sha_file(root / "launcher.log"),
        "launch_receipt_sha256": campaign._sha_file(root / "launch_receipt.json"),
        "summary_exists": False,
        "agent_run_log_exists": True,
        "agent_run_log_sha256": campaign._sha_file(run_log),
        "experiment_inventory": campaign._tree_inventory(root / "experiment"),
    })
    campaign._write_new(root / "terminal_receipt.sha256.json", {
        "path": campaign._terminal_paths(tmp_path, "p", 1)[0].name,
        "sha256": campaign._sha_file(terminal_path),
    })
    classification_path, classification_hash = campaign._classification_paths(
        tmp_path, "p", 1
    )
    classification = {
        "schema_version": 1,
        "kind": "further_mode_ablation_attempt_classification",
        "run_id": "p", "attempt": 1,
        "row_sha256": campaign._sha_bytes(campaign._json_bytes(row)),
        "classifier": manifest["infrastructure_classifier"],
        "preference_scores_read": False,
        "classification": {"class": "infra", "code": "partial_external_termination"},
        "input_mode": "controller_terminal_without_summary",
        "replacement_eligible": True,
        "inputs": {
            "run_log": {"sha256": campaign._sha_file(run_log)},
            "launcher_log": {"sha256": campaign._sha_file(root / "launcher.log")},
            "terminal_receipt": {"sha256": campaign._sha_file(terminal_path)},
        },
    }
    campaign._write_new(classification_path, classification)
    campaign._write_new(classification_hash, {
        "path": classification_path.name,
        "sha256": campaign._sha_file(classification_path),
    })
    campaign._write_new(root / "archive_record.json", {
        "schema_version": 1,
        "kind": "further_mode_ablation_excluded_infrastructure_attempt",
        "run_id": "p", "attempt": 1,
        "classifier": manifest["infrastructure_classifier"],
        "same_frozen_row_and_route": row,
        "next_attempt": 2, "maximum_attempts": campaign.MAX_ATTEMPTS,
        "moved": ["experiment", "launcher.log", "launch_receipt.json",
                  "launch_receipt.sha256.json", "terminal_receipt.json",
                  "terminal_receipt.sha256.json"],
        "classified_input_archive_refs": {
            "launcher_log": {"path": "launcher.log", "sha256": campaign._sha_file(root / "launcher.log")},
            "run_log": {"path": "experiment/result/run.log", "sha256": campaign._sha_file(run_log)},
            "terminal_receipt": {"path": "terminal_receipt.json", "sha256": campaign._sha_file(terminal_path)},
        },
        "classification_sha256": campaign._sha_file(classification_path),
    })
    assert campaign._attempt_chain(tmp_path, manifest, row)[0]["attempt"] == 1
    run_log.write_text("Step 1 then ConnectionResetError")
    with pytest.raises(ValueError, match="binding drifted"):
        campaign._attempt_chain(tmp_path, manifest, row)


def test_static_and_cli_surfaces() -> None:
    result = campaign.static_verify()
    assert result["runs"] == 240
    completed = subprocess.run(
        [sys.executable, str(campaign.__file__), "--help"],
        cwd=campaign.ROOT, capture_output=True, text=True, timeout=60,
    )
    assert completed.returncode == 0
    for command in (
        "prepare", "probe-host-capacity", "probe", "launch-block", "status",
        "classify-attempt", "archive-infra-attempt", "report", "verify",
    ):
        assert command in completed.stdout


def test_probe_environment_contract_is_frozen_in_source() -> None:
    source = Path(campaign.__file__).read_text()
    assert 'env["AGENTARENA_PROBE_ALL_REGIONS"] = "1"' in source
    assert "_acquire_host_browser_lock" in source
    assert "current_roots >= HOST_BROWSER_CEILING - REFILL_BROWSER_RESERVE" in source


def test_seeded_database_checkout_delta(tmp_path: Path) -> None:
    experiment = tmp_path / "run"
    experiment.mkdir()
    assert campaign._database_order_count(experiment) == -1
    database = experiment / "amazon_17000.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        'CREATE TABLE "order" (id INTEGER PRIMARY KEY);'
        'CREATE TABLE orderitem (id INTEGER PRIMARY KEY, order_id INTEGER);'
    )
    connection.executemany(
        'INSERT INTO "order"(id) VALUES (?)', [(value,) for value in range(1, 6)]
    )
    connection.commit()
    connection.close()
    assert campaign._database_order_count(experiment) == 0
    connection = sqlite3.connect(database)
    connection.execute('INSERT INTO "order"(id) VALUES (6)')
    connection.execute("INSERT INTO orderitem(id, order_id) VALUES (1, 6)")
    connection.commit()
    connection.close()
    assert campaign._database_order_count(experiment) == 2


def test_partial_attempt_classifier_is_narrow() -> None:
    terminal = {
        "process_ended": True,
        "summary_exists": False,
        "database_order_count": 0,
        "logged_agent_step_evidence": False,
        "returncode": 1,
        "spawn_exception_type": None,
    }
    assert infra_classifier.classify_partial_attempt(
        terminal, "nested worker: ConnectionResetError"
    )["class"] == "infra"
    assert infra_classifier.classify_partial_attempt(
        {**terminal, "logged_agent_step_evidence": True},
        "Step 1 nested worker: ConnectionResetError",
    )["class"] == "ambiguous"
    assert infra_classifier.classify_partial_attempt(
        terminal, "nested worker returned without a summary"
    )["class"] == "ambiguous"
    no_database_spawn = {
        **terminal,
        "database_order_count": -1,
        "process_pid": None,
        "spawn_exception_type": "FileNotFoundError",
    }
    assert infra_classifier.classify_partial_attempt(
        no_database_spawn, "AGENTARENA_RUN_PROCESS_SPAWN_FAILED"
    )["class"] == "infra"
    assert infra_classifier.classify_partial_attempt(
        {**no_database_spawn, "spawn_exception_type": None, "returncode": -9},
        "SIGKILL",
    )["class"] == "ambiguous"


def test_gold_contract_accepts_nongaming_semantic_equivalence() -> None:
    gold = campaign._read_json(campaign.GOLD_CONTRACT_PATH)
    contract = {
        "search_mode": "best_available",
        "constraints": [
            {"criterion_id": "price", "operator": "lt", "expected": 1000},
            {"criterion_id": "ssd", "operator": "ge", "expected": 512},
            {"criterion_id": "rating", "operator": "ge", "expected": 4},
            {"criterion_id": "brightness", "operator": "ge", "expected": 250},
            {"criterion_id": "laptop_type", "operator": "ne", "expected": "gaming laptop"},
        ],
        "objectives": [
            {"criterion_id": "weight", "direction": "minimize", "priority": None, "weight": None},
            {"criterion_id": "battery", "direction": "maximize", "priority": None, "weight": None},
        ],
    }
    assert campaign._contract_semantically_correct(json.dumps(contract), gold) is True
