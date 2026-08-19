from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentarena.scaffolds import browseruse as browseruse_runtime
from agentarena.scaffolds.browseruse import (
    _EVALUATE_RESULT_SPILL_CONFIGURATION,
)

import _infra_classify as infra_classifier
import harness_eval_campaign as campaign
import hard_campaign_runtime as runtime
import report_harness_eval as reporter


def _context_limits() -> dict[str, int]:
    return {
        "action_error_chars": 20000,
        "action_results_chars": 60000,
        "evaluate_memory_chars": 10000,
        "extract_already_collected_items": 100,
        "extract_memory_chars": 10000,
        "extract_page_chunk_chars": 100000,
        "max_clickable_elements_chars": 40000,
        "read_state_chars": 60000,
    }


def _complete_context_cap_audit(
    *,
    touched: str | None = None,
    configured_override: tuple[str, int] | None = None,
) -> dict:
    configured = _context_limits()
    if configured_override is not None:
        configured[configured_override[0]] = configured_override[1]
    return {
        "schema_version": 1,
        "complete": True,
        "history_items": 4,
        "limits": {
            name: {
                "configured": value,
                "touched_count": 1 if name == touched else 0,
                "max_observed": (
                    value + 1 if name == touched else 0
                ),
            }
            for name, value in configured.items()
        },
    }


def _pre_agent_context_cap_audit() -> dict:
    audit = _complete_context_cap_audit()
    audit.update({
        "history_items": 0,
        "history_state": "not_created",
        "measurement_basis": "failure_before_agent_construction",
    })
    return audit


def _complete_action_error_audit(
    *,
    agent_output_validation: tuple[int, ...] = (),
    other_or_unknown: tuple[int, ...] = (),
) -> dict:
    def bucket(lengths: tuple[int, ...]) -> dict:
        return {
            "count": len(lengths),
            "over_cap_count": sum(length > 20000 for length in lengths),
            "max_chars": max(lengths, default=0),
        }

    known = bucket(agent_output_validation)
    unknown = bucket(other_or_unknown)
    return {
        "schema_version": 1,
        "complete": True,
        "error": None,
        "all": {
            "count": known["count"] + unknown["count"],
            "over_cap_count": (
                known["over_cap_count"] + unknown["over_cap_count"]
            ),
            "max_chars": max(known["max_chars"], unknown["max_chars"]),
        },
        "agent_output_validation": known,
        "other_or_unknown": unknown,
    }


def _context_audit_for_action_errors(action_error_audit: dict) -> dict:
    audit = _complete_context_cap_audit()
    audit["limits"]["action_error_chars"].update({
        "touched_count": action_error_audit["all"]["over_cap_count"],
        "max_observed": action_error_audit["all"]["max_chars"],
    })
    return audit


def _limit_contract() -> dict:
    return runtime.runtime_limit_contract(
        near_fraction=campaign.LIMIT_NEAR_FRACTION,
    )


def _complete_evaluate_result_store(
    *,
    byte_count: int = 0,
    responses: int = 0,
    records: int | None = None,
    max_serialized_chars: int = 0,
) -> dict:
    return {
        "schema_version": 1,
        "inline_chars": 9999,
        "single_max_chars": 64 * 1024**2,
        "max_serialized_chars": max_serialized_chars,
        "single_bound_touched_count": 0,
        "integrity_failure_count": 0,
        "max_bytes": 8 * 1024**3,
        "bytes": byte_count,
        "byte_bound_touched_count": 0,
        "max_responses": 200000,
        "responses": responses,
        "response_bound_touched_count": 0,
        "records": responses if records is None else records,
    }


def _complete_limit_audit(
    arm: str,
    *,
    touched: tuple[str, str] | None = None,
    evaluate_store: dict | None = None,
    action_error_audit: dict | None = None,
) -> dict:
    contract = _limit_contract()
    categories = {}
    for category, records in runtime.applicable_limit_inventory(
        contract, arm
    ).items():
        categories[category] = {
            name: {
                "configured": record["configured"],
                "touched_count": int(touched == (category, name)),
                "observations": {
                    key: record[key]
                    for key in (
                        "observation_basis",
                        "direct_maximum_observed",
                    )
                    if key in record
                },
            }
            for name, record in records.items()
        }
    store = evaluate_store or _complete_evaluate_result_store()
    safety = categories["safety_backstops"]
    safety["evaluate_result_single_chars"]["touched_count"] = store[
        "single_bound_touched_count"
    ]
    safety["evaluate_result_single_chars"]["observations"].update({
        "max_serialized_chars": store["max_serialized_chars"],
        "maximum": store["single_max_chars"],
    })
    safety["evaluate_result_store_bytes"]["touched_count"] = max(
        store["byte_bound_touched_count"],
        int(store["bytes"] >= store["max_bytes"]),
    )
    safety["evaluate_result_store_bytes"]["observations"].update({
        "used": store["bytes"],
        "maximum": store["max_bytes"],
        "utilization": store["bytes"] / store["max_bytes"],
        "records": store["records"],
    })
    safety["evaluate_result_store_responses"]["touched_count"] = max(
        store["response_bound_touched_count"],
        int(store["responses"] >= store["max_responses"]),
    )
    safety["evaluate_result_store_responses"]["observations"].update({
        "used": store["responses"],
        "maximum": store["max_responses"],
        "utilization": store["responses"] / store["max_responses"],
        "records": store["records"],
    })
    spill = categories["fixed_architecture"]["evaluate_result_spill"]
    spill["touched_count"] = store["responses"]
    spill["observations"].update({
        "spilled_responses": store["responses"],
        "unique_records": store["records"],
        "stored_bytes": store["bytes"],
        "max_serialized_chars": store["max_serialized_chars"],
    })
    extract = categories["fixed_architecture"][
        "extract_result_file_externalization"
    ]
    externalized = int(
        touched
        == ("fixed_architecture", "extract_result_file_externalization")
    )
    extract["touched_count"] = externalized
    extract["observations"].update({
        "raw_context_audit_name": "extract_memory_chars",
        "externalized_results": externalized,
        "max_result_chars": 10001 if externalized else 0,
        "threshold_chars": 10000,
    })
    action = action_error_audit or _complete_action_error_audit()
    all_errors = action["all"]
    known = action["agent_output_validation"]
    unknown = action["other_or_unknown"]
    fixed_action = categories["fixed_architecture"][
        "agent_output_validation_feedback_rendering"
    ]
    fixed_action["touched_count"] = known["over_cap_count"]
    fixed_action["observations"].update({
        "runtime_configured": fixed_action["configured"],
        "classification_complete": True,
        "raw_context_audit_name": "action_error_chars",
        "all_error_count": all_errors["count"],
        "all_over_cap_count": all_errors["over_cap_count"],
        "all_max_chars": all_errors["max_chars"],
        "agent_output_validation_count": known["count"],
        "agent_output_validation_over_cap_count": known[
            "over_cap_count"
        ],
        "agent_output_validation_max_chars": known["max_chars"],
        "other_or_unknown_count": unknown["count"],
        "other_or_unknown_over_cap_count": unknown["over_cap_count"],
        "other_or_unknown_max_chars": unknown["max_chars"],
    })
    lossy_action = categories["lossy_context_limits"][
        "action_error_chars"
    ]
    lossy_action["touched_count"] = unknown["over_cap_count"]
    lossy_action["observations"] = {
        "classification_complete": True,
        "raw_touched_count": all_errors["over_cap_count"],
        "raw_max_observed": all_errors["max_chars"],
        "other_or_unknown_count": unknown["count"],
        "other_or_unknown_touched_count": unknown["over_cap_count"],
        "max_observed": unknown["max_chars"],
        "classified_agent_output_validation_count": known["count"],
        "classified_agent_output_validation_touched_count": known[
            "over_cap_count"
        ],
        "classified_agent_output_validation_max_observed": known[
            "max_chars"
        ],
    }
    return {
        "schema_version": 1,
        "contract_sha256": contract["sha256"],
        "arm": arm,
        "complete": True,
        "error": None,
        "categories": categories,
    }


def test_runtime_attests_effective_action_error_prompt_patch() -> None:
    behavior = runtime._agent_behavior_limits()

    assert behavior["context_limits"]["action_error_chars"] == 20000
    assert behavior["action_error_prompt_cap_patch"] == {
        "upstream_chars": 200,
        "effective_chars": 20000,
        "effective_edge_chars": 10000,
        "upstream_browser_use_version_guard": "0.13.6",
        "upstream_message_manager_service_sha256_guard": (
            "54959a44f45adaae357d52d67d14146553a8a3176b928b70f2a1a8b1f2d75906"
        ),
    }


def test_runtime_limit_contract_and_run_audit_are_exact_and_hash_bound() -> None:
    contract = _limit_contract()
    audit = _complete_limit_audit("deliberative")

    assert runtime.validate_limit_audit(
        audit, contract, "deliberative"
    ) == []
    assert set(contract["categories"]) == {
        "safety_backstops",
        "lossy_context_limits",
        "fixed_architecture",
        "diagnostic_truncations",
        "infrastructure",
        "launch_only",
    }
    assert contract["categories"]["infrastructure"][
        "environment_evaluator_get"
    ]["configured"]["exhaustion_result"] == (
        "raise:AGENTARENA_EVALUATOR_GET_RETRIES_EXHAUSTED"
    )
    safety = contract["categories"]["safety_backstops"]
    assert safety["evaluate_result_single_chars"]["configured"] == 64 * 1024**2
    assert safety["evaluate_result_store_bytes"]["configured"] == 8 * 1024**3
    assert (
        safety["evaluate_result_store_responses"]["configured"] == 200000
    )
    spill = contract["categories"]["fixed_architecture"][
        "evaluate_result_spill"
    ]["configured"]
    assert spill == _EVALUATE_RESULT_SPILL_CONFIGURATION
    assert spill["inline_chars"] == 9999
    assert spill["single_bound_signal_chars"] == 67108865
    assert spill["operations"] == ["stat", "list", "search", "read"]
    assert spill["receipt_head_preview_chars"] == 500
    assert spill["receipt_tail_preview_chars"] == 500
    assert spill["terminates_sequence"] is True
    assert "evaluate_output_chars" not in contract["categories"][
        "lossy_context_limits"
    ]
    assert "extract_memory_chars" not in contract["categories"][
        "lossy_context_limits"
    ]
    extract_route = contract["categories"]["fixed_architecture"][
        "extract_result_file_externalization"
    ]["configured"]
    assert extract_route == runtime.EXTRACT_RESULT_FILE_EXTERNALIZATION_CONFIGURATION
    assert extract_route["externalize_at_chars"] == 10000
    assert extract_route["separate_read_state_limit_chars"] == 60000
    validation_feedback = contract["categories"]["fixed_architecture"][
        "agent_output_validation_feedback_rendering"
    ]["configured"]
    assert validation_feedback == (
        runtime.AGENT_OUTPUT_VALIDATION_FEEDBACK_RENDERING_CONFIGURATION
    )
    assert validation_feedback["trigger_chars"] == 20000
    assert validation_feedback["unknown_errors_fail_closed"] is True
    missing = json.loads(json.dumps(audit))
    missing["categories"]["safety_backstops"].pop("max_steps")
    assert any(
        "name inventory is not exact" in error
        for error in runtime.validate_limit_audit(
            missing, contract, "deliberative"
        )
    )

    extra = json.loads(json.dumps(audit))
    extra["categories"]["fixed_architecture"]["undeclared_limit"] = {
        "configured": 1,
        "touched_count": 0,
        "observations": {},
    }
    assert any(
        "name inventory is not exact" in error
        for error in runtime.validate_limit_audit(
            extra, contract, "deliberative"
        )
    )

    drifted_contract = json.loads(json.dumps(contract))
    drifted_contract["categories"]["safety_backstops"][
        "max_steps"
    ]["configured"] += 1
    assert runtime.validate_limit_audit(
        audit, drifted_contract, "deliberative"
    ) == ["frozen limit contract is malformed or hash-invalid"]


def test_deliberative_limit_audit_fails_closed_without_extension_stats() -> None:
    contract = _limit_contract()
    audit = browseruse_runtime._new_limit_audit(
        contract, "deliberative"
    )

    browseruse_runtime._finalize_limit_audit(
        audit,
        steps=[],
        elapsed_seconds=1.0,
        context_cap_audit=_complete_context_cap_audit(),
        action_error_audit=_complete_action_error_audit(),
        replace_file_safety_audit=(
            browseruse_runtime._ReplaceFileSafetyCollector().snapshot()
        ),
        history_error_text="",
        run_error=None,
        total_timeout_touched=False,
        evaluate_result_store=_complete_evaluate_result_store(),
        extension_stats=None,
        agent=None,
    )

    assert audit["complete"] is False
    assert audit["error"] == "deliberative extension stats are absent"
    assert any(
        "limit_audit is incomplete" in error
        for error in runtime.validate_limit_audit(
            audit, contract, "deliberative"
        )
    )


def test_runtime_limit_contract_excludes_storefront_transaction_patch_surface() -> None:
    contract = _limit_contract()
    safety = contract["categories"]["safety_backstops"]
    fixed = contract["categories"]["fixed_architecture"]
    diagnostics = contract["categories"]["diagnostic_truncations"]

    assert "cart_sanitize_iterations" not in safety
    assert "checkout_transition_polls" not in safety
    assert not {
        "public_json_response_bytes",
        "public_html_response_bytes",
        "public_html_pages",
        "html_parser_nodes",
        "html_parser_depth",
        "public_search_polls",
    } & set(safety)
    assert "click_grounding_repeat_limit" not in fixed
    assert "public_acquisition_shape" not in fixed
    assert "preflight_traversal" not in fixed
    assert "compiler_and_reviewer_shape" not in fixed
    assert fixed["compiler_and_checkpoint_shape"]["configured"] == {
        "contract_compile_calls": 1,
        "decision_checkpoint": "agent_invoked_optional",
    }
    assert safety["structured_response_attempts"]["configured"] == 4
    assert fixed["deliberative_sequence_terminators"][
        "configured"
    ] == ["decision_checkpoint"]
    assert diagnostics == {}


def test_browser_use_dependency_freeze_hashes_the_complete_package_tree() -> None:
    tree = runtime._browser_use_package_tree_manifest()

    assert tree["version"] == "0.13.6"
    assert tree["file_count"] == len(tree["files"])
    assert tree["sha256"] == runtime._sha_bytes(
        runtime._json_bytes(tree["files"])
    )
    # These cap-bearing files were outside the former curated source subset.
    assert {
        "agent/judge.py",
        "browser/profile.py",
        "browser/watchdogs/dom_watchdog.py",
    } <= set(tree["files"])


def test_report_cli_refuses_existing_output_before_rescore(
    tmp_path: Path,
    monkeypatch,
) -> None:
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    (campaign_dir / "report.json").write_text("existing")
    monkeypatch.setattr(
        reporter,
        "build_report",
        lambda *_args, **_kwargs: pytest.fail(
            "rescore/report build ran before create-only collision check"
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["report_harness_eval.py", str(campaign_dir)],
    )
    with pytest.raises(SystemExit, match="refusing to replace"):
        reporter.main()


def test_schedule_is_exact_balanced_interleaved_ab() -> None:
    rows = campaign.build_schedule("eval", 17000)
    assert len(rows) == 60
    assert Counter(row["cohort"] for row in rows) == {
        "weak_easy_combined": 30,
        "weak_easy_clean": 10,
        "sol_high_hard": 20,
    }
    assert Counter(row["arm"] for row in rows) == {
        "baseline": 30,
        "deliberative": 30,
    }
    for block in range(1, 7):
        block_rows = [row for row in rows if row["block"] == block]
        assert len(block_rows) == 10
        assert Counter(row["arm"] for row in block_rows) == {
            "baseline": 5,
            "deliberative": 5,
        }
        assert sorted(row["spawn_index"] for row in block_rows) == list(
            range(10)
        )
        for index in range(0, 10, 2):
            pair = block_rows[index:index + 2]
            assert pair[0]["scenario"] == pair[1]["scenario"]
            assert {row["arm"] for row in pair} == {
                "baseline", "deliberative"
            }
            assert pair[0]["primary_region"] == pair[1]["primary_region"]
            assert pair[0]["region_order"] == pair[1]["region_order"]
        assert Counter(
            row["arm"] for row in block_rows[::2]
        ) in (
            {"baseline": 2, "deliberative": 3},
            {"baseline": 3, "deliberative": 2},
        )
    pairs = Counter(
        (row["cohort"], row["scenario"], row["repeat"], row["arm"])
        for row in rows
    )
    assert set(pairs.values()) == {1}
    assert rows == campaign.build_schedule("eval", 17000)


def test_schedule_arm_order_crosses_over_within_repeated_cohorts() -> None:
    rows = campaign.build_schedule("eval", 17000)
    for cohort in ("weak_easy_combined", "sol_high_hard"):
        repeats = (
            (1, 2, 3)
            if cohort == "weak_easy_combined"
            else (1, 2)
        )
        scenarios = {
            row["scenario"] for row in rows if row["cohort"] == cohort
        }
        for scenario in scenarios:
            first_arm = {}
            for repeat in repeats:
                pair = sorted(
                    (
                        row for row in rows
                        if row["cohort"] == cohort
                        and row["scenario"] == scenario
                        and row["repeat"] == repeat
                    ),
                    key=lambda row: row["spawn_index"],
                )
                first_arm[repeat] = pair[0]["arm"]
            assert first_arm[1] != first_arm[2]
            if len(repeats) == 3:
                assert first_arm[1] == first_arm[3]


def test_schedule_uses_exact_models_conditions_and_scaffolds() -> None:
    rows = campaign.build_schedule("eval", 17000)
    for row in rows:
        assert row["variant"] == "graded"
        assert row["scaffold"] == {
            "baseline": "browseruse",
            "deliberative": "browseruse-deliberative",
        }[row["arm"]]
        if row["cohort"].startswith("weak"):
            assert row["model_request"] == "gpt-5.6-terra#low"
            assert row["model_recorded"] == "gpt-5.6-terra-low"
        else:
            assert row["model_request"] == "gpt-5.6-sol#high"
            assert row["model_recorded"] == "gpt-5.6-sol-high"
            assert row["scenario"].endswith("_hard")


def test_schedule_freezes_an_explicit_current_weak_route_subset() -> None:
    selected = ("redmond/interactive", "gcr/shared")
    rows = campaign.build_schedule(
        "eval", 17000, weak_regions=selected
    )
    weak_rows = [
        row for row in rows
        if row["logical_model"] == campaign.WEAK_LOGICAL
    ]
    assert {
        tuple(row["region_order"]) for row in weak_rows
    } == {
        selected,
        tuple(reversed(selected)),
    }
    assert Counter(row["primary_region"] for row in weak_rows) == {
        "redmond/interactive": 20,
        "gcr/shared": 20,
    }
    with pytest.raises(ValueError, match="duplicates"):
        campaign.build_schedule(
            "eval",
            17000,
            weak_regions=("gcr/shared", "gcr/shared"),
        )
    with pytest.raises(ValueError, match="unsupported"):
        campaign.build_schedule(
            "eval",
            17000,
            weak_regions=("unknown/region",),
        )


def test_confirmatory_weak_freeze_requires_all_three_fresh_routes() -> None:
    selected = (
        "redmond/interactive",
        "gcr/shared",
        "msraif/shared",
    )
    assert campaign._validate_weak_campaign_regions(
        selected, label="weak model"
    ) == selected
    with pytest.raises(ValueError, match="all three"):
        campaign._validate_weak_campaign_regions(
            selected[:2], label="weak model"
        )


def test_schedule_freezes_an_independent_current_sol_route_subset() -> None:
    selected = ("msraif/shared", "redmond/interactive")
    rows = campaign.build_schedule(
        "eval", 17000, sol_regions=selected
    )
    sol_rows = [
        row for row in rows
        if row["logical_model"] == campaign.SOL_LOGICAL
    ]
    weak_rows = [
        row for row in rows
        if row["logical_model"] == campaign.WEAK_LOGICAL
    ]
    assert {
        tuple(row["region_order"]) for row in sol_rows
    } == {
        selected,
        tuple(reversed(selected)),
    }
    assert Counter(row["primary_region"] for row in sol_rows) == {
        "msraif/shared": 10,
        "redmond/interactive": 10,
    }
    assert all(
        set(row["region_order"]) == set(campaign.WEAK_REGIONS)
        for row in weak_rows
    )
    with pytest.raises(ValueError, match="duplicates"):
        campaign.build_schedule(
            "eval",
            17000,
            sol_regions=("msraif/shared", "msraif/shared"),
        )
    with pytest.raises(ValueError, match="unsupported"):
        campaign.build_schedule(
            "eval",
            17000,
            sol_regions=("unknown/region",),
        )


def test_port_guard_rejects_refill_band() -> None:
    with pytest.raises(SystemExit, match="protected"):
        campaign.validate_port_band(13200, require_free=False)
    with pytest.raises(SystemExit, match="protected"):
        campaign.validate_port_band(13150, require_free=False)
    campaign.validate_port_band(17000, require_free=False)


def test_numbered_reports_preserve_prior_refill_and_final_evidence(
    tmp_path: Path,
) -> None:
    first = campaign.next_report_paths(tmp_path)
    assert Path(first["json"]).name == "report_0001.json"
    assert Path(first["markdown"]).name == "report_0001.md"
    assert Path(first["hash"]).name == "report_0001.json.sha256.json"

    Path(first["json"]).parent.mkdir(parents=True)
    Path(first["json"]).write_text("immutable first decision")
    second = campaign.next_report_paths(tmp_path)
    assert Path(second["json"]).name == "report_0002.json"

    # A partial sidecar from an interrupted publication also consumes the
    # number; it must never be paired with newly generated report content.
    Path(second["hash"]).write_text("partial publication")
    third = campaign.next_report_paths(tmp_path)
    assert Path(third["json"]).name == "report_0003.json"
    assert Path(first["json"]).read_text() == "immutable first decision"


def test_lockdiff_validator_binds_exact_current_report_and_inventory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import lockdiff_capture

    engine = {}
    for index, relative in enumerate(lockdiff_capture.ENGINE_FILES):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"engine-{index}")
        engine[relative] = {
            "sha256": campaign._sha_file(path)[:16],
            "mtime": float(index),
        }
    frontend_root = (
        tmp_path
        / "agentarena"
        / "envs"
        / "amazon"
        / "server"
        / "frontend"
        / "dist"
    )
    frontend = {}
    for relative in ("assets/app.css", "assets/app.js", "index.html"):
        path = frontend_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative)
        frontend[relative] = campaign._sha_file(path)
    monkeypatch.setattr(lockdiff_capture, "FRONTEND_SHA256", frontend)
    catalogs = (
        tmp_path
        / "agentarena"
        / "envs"
        / "amazon"
        / "server"
        / "_catalogs"
    )
    for scenario in campaign.EASY_SCENARIOS:
        benchmark = tmp_path / "benchmark_data" / "amazon" / scenario
        benchmark.mkdir(parents=True)
        for index in range(10):
            (benchmark / f"artifact_{index}.json").write_text(
                f"{scenario}-{index}"
            )
        catalogs.mkdir(parents=True, exist_ok=True)
        (catalogs / f"{scenario}.json").write_text(scenario)
        for condition in campaign.LOCKDIFF_CONDITIONS:
            (
                catalogs / f"{scenario}.{condition}.steering.json"
            ).write_text(f"{scenario}-{condition}")
    artifacts = campaign._original_artifact_manifest(tmp_path)
    captures = {
        f"{scenario}/{condition}": {
            "serp": [scenario, condition],
            "steering": {},
        }
        for scenario in campaign.EASY_SCENARIOS
        for condition in campaign.LOCKDIFF_CONDITIONS
    }
    before_engine = json.loads(json.dumps(engine))
    first = lockdiff_capture.ENGINE_FILES[0]
    before_engine[first]["sha256"] = "0" * 16
    before = {
        **captures,
        "__meta__": {
            "captured_at": 1.0,
            "files": before_engine,
            "original_artifacts": artifacts,
        },
    }
    after = {
        **captures,
        "__meta__": {
            "captured_at": 2.0,
            "files": engine,
            "original_artifacts": artifacts,
        },
    }
    output = tmp_path / "proof"
    output.mkdir()
    before_path = output / "lockdiff_before.json"
    after_path = output / "lockdiff_after.json"
    before_path.write_text(json.dumps(before))
    after_path.write_text(json.dumps(after))
    validation = campaign._validate_lockdiff(
        after_path, repo_root=tmp_path
    )
    assert validation["verdict"] == "pass"
    assert validation["condition_captures"] == 50
    assert validation["measured_condition_captures"] == 40
    assert validation["original_artifacts"]["count"] == 105
    (tmp_path / first).write_text("stale-after-capture")
    with pytest.raises(ValueError, match="engine hash is stale"):
        campaign._validate_lockdiff(after_path, repo_root=tmp_path)


def _refill_command(model: str, jobs: int, base_port: int) -> str:
    return (
        ".venv/bin/python -m agentarena.benchmark.run "
        "--name overhaul_lb "
        "--scenarios laptop office_chair mattress backpack tent "
        "--conditions clean combined "
        "--variants thresholded mixed graded graded3 graded4 "
        "--scaffolds browseruse "
        f"--models {model} --repeats 3 --jobs {jobs} "
        "--results results/overhaul_lb "
        f"--base-port {base_port} --max-steps 2000"
    )


def _capfree_topup_command() -> str:
    return (
        ".venv/bin/python -m agentarena.benchmark.run "
        "--name qwen_capfree_topup_20260728 "
        "--scenarios backpack --conditions clean --variants graded "
        "--scaffolds browseruse --models Qwen3.5-122B "
        "--repeats 1 --jobs 1 "
        "--results results/overhaul_lb_refill_staging "
        "--base-port 13280 --max-steps 12000"
    )


def _empty_coexistence(base_port: int = 17000) -> dict:
    payload = {
        "kind": "protected_refill_coexistence_audit",
        "captured_at_utc": campaign._utcnow(),
        "campaign_port_band": [base_port, base_port + 99],
        "protected_port_band": [
            campaign.PROTECTED_PORT_LOW,
            campaign.PROTECTED_PORT_HIGH,
        ],
        "recognized_models": list(campaign.REFILL_MODELS),
        "recognized_masters": [],
        "protected_listeners": [],
        "refill_jobs_sum": 0,
        "refill_port_ranges": [],
        "campaign_block_runs": campaign.CAMPAIGN_BLOCK_RUNS,
        "campaign_max_parallel_runs":
            campaign.CAMPAIGN_MAX_PARALLEL_RUNS,
        "projected_browser_max":
            campaign.CAMPAIGN_MAX_PARALLEL_RUNS,
        "machine_browser_floor": campaign.MACHINE_BROWSER_FLOOR,
        "max_protected_listeners": campaign.REFILL_MAX_LISTENERS,
        "ports_non_overlapping": True,
        "verdict": "pass",
    }
    return {
        **payload,
        "snapshot_sha256": campaign._sha_bytes(
            campaign._json_bytes(payload)
        ),
    }


def test_refill_coexistence_allows_only_exact_bounded_masters(
    monkeypatch,
) -> None:
    master = {
        "pid": 10,
        "ppid": 1,
        "cwd": str(campaign.ROOT.resolve()),
        "command": _refill_command("Qwen3.5-122B", 16, 13200),
    }
    child = {
        "pid": 11,
        "ppid": 10,
        "cwd": str(campaign.ROOT.resolve()),
        "command": "python -m backend.app --port 13241",
    }
    monkeypatch.setattr(
        campaign, "_system_process_table", lambda: {10: master, 11: child}
    )
    monkeypatch.setattr(
        campaign,
        "_protected_listener_records",
        lambda: [{
            "port": 13241,
            "pids": [11],
            "socket_record_sha256": "socket",
        }],
    )
    audit = campaign.audit_refill_coexistence(17000)
    assert audit["refill_jobs_sum"] == 16
    assert audit["projected_browser_max"] == 20
    assert audit["refill_port_ranges"] == [{
        "low": 13200,
        "high": 13249,
        "master_pid": 10,
    }]
    assert audit["recognized_masters"][0]["cell_port_count"] == 50
    campaign._validate_refill_coexistence_record(audit, 17000)
    qwen_fifteen = {
        **master,
        "command": _refill_command(
            "Qwen3.5-122B", 15, 13200
        ),
    }
    kimi_one = {
        "pid": 12,
        "ppid": 1,
        "cwd": str(campaign.ROOT.resolve()),
        "command": _refill_command("Kimi-K2.6", 7, 13250),
    }
    monkeypatch.setattr(
        campaign,
        "_system_process_table",
        lambda: {10: qwen_fifteen, 11: child, 12: kimi_one},
    )
    two_lane = campaign.audit_refill_coexistence(17000)
    assert two_lane["refill_port_ranges"] == [
        {"low": 13200, "high": 13249, "master_pid": 10},
        {"low": 13250, "high": 13299, "master_pid": 12},
    ]
    campaign._validate_refill_coexistence_record(two_lane, 17000)
    assert two_lane["refill_jobs_sum"] == 22
    assert two_lane["projected_browser_max"] == 26

    topup = {
        "pid": 20,
        "ppid": 1,
        "cwd": str(campaign.ROOT.resolve()),
        "command": _capfree_topup_command(),
    }
    topup_child = {
        "pid": 21,
        "ppid": 20,
        "cwd": str(campaign.ROOT.resolve()),
        "command": "python -m backend.app --port 13280",
    }
    monkeypatch.setattr(
        campaign,
        "_system_process_table",
        lambda: {20: topup, 21: topup_child},
    )
    monkeypatch.setattr(
        campaign,
        "_protected_listener_records",
        lambda: [{
            "port": 13280,
            "pids": [21],
            "socket_record_sha256": "topup-socket",
        }],
    )
    capfree = campaign.audit_refill_coexistence(17000)
    assert capfree["refill_jobs_sum"] == 1
    assert capfree["projected_browser_max"] == 5
    assert capfree["recognized_masters"][0]["profile"] == (
        campaign.REFILL_PROFILE_QWEN_CAPFREE
    )
    assert capfree["recognized_masters"][0]["cell_port_count"] == 1
    campaign._validate_refill_coexistence_record(capfree, 17000)
    wrong_topup = {
        **topup,
        "command": _capfree_topup_command().replace(
            "--max-steps 12000", "--max-steps 2000"
        ),
    }
    monkeypatch.setattr(
        campaign,
        "_system_process_table",
        lambda: {20: wrong_topup, 21: topup_child},
    )
    with pytest.raises(SystemExit, match="unrecognized process"):
        campaign.audit_refill_coexistence(17000)

    monkeypatch.setattr(
        campaign,
        "_protected_listener_records",
        lambda: [{
            "port": 13241,
            "pids": [11],
            "socket_record_sha256": "socket",
        }],
    )
    overlapping_kimi = {
        **kimi_one,
        "command": _refill_command("Kimi-K2.6", 1, 13240),
    }
    monkeypatch.setattr(
        campaign,
        "_system_process_table",
        lambda: {
            10: qwen_fifteen,
            11: child,
            12: overlapping_kimi,
        },
    )
    with pytest.raises(SystemExit, match="overlapping"):
        campaign.audit_refill_coexistence(17000)
    second = {
        "pid": 12,
        "ppid": 1,
        "cwd": str(campaign.ROOT.resolve()),
        "command": _refill_command("Kimi-K2.6", 7, 13250),
    }
    monkeypatch.setattr(
        campaign,
        "_system_process_table",
        lambda: {10: master, 11: child, 12: second},
    )
    with pytest.raises(SystemExit, match="jobs sum 23"):
        campaign.audit_refill_coexistence(17000)
    bad = {**master, "command": master["command"].replace(
        "--name overhaul_lb", "--name arbitrary"
    )}
    monkeypatch.setattr(
        campaign, "_system_process_table", lambda: {10: bad}
    )
    monkeypatch.setattr(
        campaign, "_protected_listener_records", lambda: []
    )
    with pytest.raises(SystemExit, match="unrecognized process"):
        campaign.audit_refill_coexistence(17000)


def test_launch_rechecks_refill_lanes_after_checkpoint_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = {
        "schedule": campaign.build_schedule("eval", 17000),
        "base_port": 17000,
    }
    calls: list[str] = []
    monkeypatch.setattr(
        campaign, "verify_campaign", lambda *_args, **_kwargs: manifest
    )
    monkeypatch.setattr(
        campaign,
        "verify_smoke_gate",
        lambda *_args, **_kwargs: {"sha256": "smoke"},
    )
    monkeypatch.setattr(
        campaign, "validate_port_band", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        campaign,
        "verify_probe_checkpoint",
        lambda *_args, **_kwargs: calls.append("checkpoint"),
    )

    def refuse_refill_contention(_base_port: int) -> None:
        calls.append("refill")
        raise SystemExit("unsafe protected refill coexistence")

    monkeypatch.setattr(
        campaign, "audit_refill_coexistence", refuse_refill_contention
    )
    monkeypatch.setattr(
        campaign,
        "_write_receipt",
        lambda *_args, **_kwargs: pytest.fail(
            "receipt was written before the final refill-lane check"
        ),
    )
    with pytest.raises(SystemExit, match="refill coexistence"):
        campaign.launch_block(tmp_path, 1, "probe1")
    assert calls == ["checkpoint"] * 10 + ["refill"]


def test_measured_launch_isolates_each_child_process_session(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = {
        "schedule": campaign.build_schedule("eval", 17000),
        "base_port": 17000,
        "caps": campaign.CAPS,
    }
    rows = [
        row for row in manifest["schedule"] if row["block"] == 1
    ]
    by_name = {row["run_name"]: row for row in rows}
    popen_kwargs = []

    class CompletedProcess:
        @staticmethod
        def wait():
            return 0

        @staticmethod
        def poll():
            return 0

    def fake_popen(command, **kwargs):
        popen_kwargs.append(kwargs)
        run_name = command[command.index("--name") + 1]
        row = by_name[run_name]
        summary = tmp_path / row["summary_relpath"]
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text("{}")
        return CompletedProcess()

    monkeypatch.setattr(
        campaign, "verify_campaign", lambda *_args, **_kwargs: manifest
    )
    monkeypatch.setattr(
        campaign,
        "verify_smoke_gate",
        lambda *_args, **_kwargs: {"sha256": "smoke"},
    )
    monkeypatch.setattr(
        campaign, "validate_port_band", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        campaign,
        "verify_probe_checkpoint",
        lambda *_args, **_kwargs: {"sha256": "probe"},
    )
    monkeypatch.setattr(
        campaign, "audit_refill_coexistence", lambda *_args: {}
    )
    monkeypatch.setattr(
        campaign, "_apply_environment_policy", lambda *_args: {}
    )
    monkeypatch.setattr(
        campaign, "_write_receipt", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(campaign.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(campaign.time, "sleep", lambda *_args: None)

    campaign.launch_block(tmp_path, 1, "probe1")

    assert len(popen_kwargs) == 10
    assert all(
        kwargs.get("start_new_session") is True
        for kwargs in popen_kwargs
    )


def test_measured_launch_replenishes_complete_pairs_with_max_four(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = {
        "schedule": campaign.build_schedule("eval", 17000),
        "base_port": 17000,
        "caps": campaign.CAPS,
    }
    rows = [
        row for row in manifest["schedule"] if row["block"] == 1
    ]
    by_name = {row["run_name"]: row for row in rows}
    pair_number = {
        row["run_id"]: index // 2 for index, row in enumerate(rows)
    }
    active = []
    spawned = []
    maximum_active = [0]

    class Process:
        def __init__(self, row):
            self.row = row
            self.done = False

        def poll(self):
            if self.done:
                return 0
            live_pairs = [
                pair_number[process.row["run_id"]]
                for process in active if not process.done
            ]
            if (
                live_pairs
                and pair_number[self.row["run_id"]] == min(live_pairs)
                and (
                    len(active) >= campaign.CAMPAIGN_MAX_PARALLEL_RUNS
                    or len(spawned) == len(rows)
                )
            ):
                self.done = True
                return 0
            return None

        def wait(self):
            self.done = True
            if self in active:
                active.remove(self)
            summary = tmp_path / self.row["summary_relpath"]
            summary.parent.mkdir(parents=True, exist_ok=True)
            summary.write_text("{}")
            return 0

    def fake_popen(command, **_kwargs):
        run_name = command[command.index("--name") + 1]
        row = by_name[run_name]
        process = Process(row)
        active.append(process)
        spawned.append(row["run_id"])
        maximum_active[0] = max(maximum_active[0], len(active))
        return process

    monkeypatch.setattr(
        campaign, "verify_campaign", lambda *_args, **_kwargs: manifest
    )
    monkeypatch.setattr(
        campaign,
        "verify_smoke_gate",
        lambda *_args, **_kwargs: {"sha256": "smoke"},
    )
    monkeypatch.setattr(
        campaign, "validate_port_band", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        campaign,
        "verify_probe_checkpoint",
        lambda *_args, **_kwargs: {"sha256": "probe"},
    )
    monkeypatch.setattr(
        campaign, "audit_refill_coexistence", lambda *_args: {}
    )
    monkeypatch.setattr(
        campaign, "_apply_environment_policy", lambda *_args: {}
    )
    monkeypatch.setattr(
        campaign, "_write_receipt", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(campaign.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(campaign.time, "sleep", lambda *_args: None)

    campaign.launch_block(tmp_path, 1, "probe1")

    assert spawned == [row["run_id"] for row in rows]
    assert maximum_active[0] == campaign.CAMPAIGN_MAX_PARALLEL_RUNS == 4
    assert all(
        {
            rows[index]["arm"],
            rows[index + 1]["arm"],
        } == {"baseline", "deliberative"}
        for index in range(0, len(rows), 2)
    )


def _probe_manifest(
    *,
    weak_regions: tuple[str, ...] = campaign.WEAK_REGIONS,
    sol_regions: tuple[str, ...] = campaign.SOL_REGIONS,
) -> dict:
    return {
        "base_port": 17000,
        "schedule": campaign.build_schedule(
            "eval",
            17000,
            weak_regions=weak_regions,
            sol_regions=sol_regions,
        ),
        "probe_policy": {
            "stages": {
                "weak_before": [1, 2],
                "weak_mid": [3, 4],
                "sol_before": [5],
                "sol_mid": [6],
            },
            "scheduled_regions": {
                campaign.WEAK_LOGICAL: list(weak_regions),
                campaign.SOL_LOGICAL: list(sol_regions),
            },
            "max_checkpoint_age_seconds":
                campaign.PROBE_MAX_AGE_SECONDS,
            "predecessor_blocks":
                campaign.PROBE_PREDECESSOR_BLOCKS,
        },
    }


def _probe_logs(
    tmp_path: Path,
    logical: str,
    capacity: dict[str, int],
    *,
    large: list[str] | None = None,
) -> tuple[Path, Path, Path | None]:
    small = tmp_path / f"{logical}_small.log"
    small.write_text(
        "diagnostic\n" + json.dumps({logical: list(capacity)}) + "\n"
    )
    concurrency = tmp_path / f"{logical}_concurrency.log"
    concurrency.write_text(
        "===== MAX SAFE CONCURRENCY (>=90% ok) =====\n"
        + json.dumps({logical: capacity}, indent=2)
        + "\naggregate text\n"
    )
    large_path = None
    if large is not None:
        large_path = tmp_path / f"{logical}_large.log"
        large_path.write_text("large diagnostics\n" + json.dumps(large))
    return small, concurrency, large_path


def test_probe_gate_checks_scheduled_capacity(tmp_path: Path) -> None:
    sufficient = {
        region: 8 for region in campaign.WEAK_REGIONS
    }
    small, concurrency, _ = _probe_logs(
        tmp_path,
        campaign.WEAK_LOGICAL,
        sufficient,
    )
    evidence = campaign.validate_probe_evidence(
        _probe_manifest(),
        "weak_before",
        small,
        concurrency,
        None,
    )
    assert evidence["logical_model"] == campaign.WEAK_LOGICAL
    assert evidence["max_scheduled_primary_load"] == {
        "gcr/shared": 4,
        "msraif/shared": 4,
        "redmond/interactive": 4,
    }
    assert evidence["max_scheduled_single_primary_failure_load"] == {
        "gcr/shared": 6,
        "msraif/shared": 8,
        "redmond/interactive": 8,
    }
    concurrency.write_text(
        "===== MAX SAFE CONCURRENCY (>=90% ok) =====\n"
        + json.dumps({
            campaign.WEAK_LOGICAL: {
                "gcr/shared": 6,
                "msraif/shared": 7,
                "redmond/interactive": 8,
            },
        })
    )
    with pytest.raises(SystemExit, match="cannot carry"):
        campaign.validate_probe_evidence(
            _probe_manifest(),
            "weak_before",
            small,
            concurrency,
            None,
        )


def test_weak_probe_requires_every_frozen_route_to_be_live(
    tmp_path: Path,
) -> None:
    small, concurrency, _ = _probe_logs(
        tmp_path,
        campaign.WEAK_LOGICAL,
        {"msraif/shared": 32},
    )
    # A stale/independent concurrency result cannot substitute for the current
    # small health probe of the route the block will actually use.
    concurrency.write_text(
        "===== MAX SAFE CONCURRENCY (>=90% ok) =====\n"
        + json.dumps({
            campaign.WEAK_LOGICAL: {
                region: 32 for region in campaign.WEAK_REGIONS
            },
        })
    )
    with pytest.raises(SystemExit, match="frozen order"):
        campaign.validate_probe_evidence(
            _probe_manifest(),
            "weak_before",
            small,
            concurrency,
            None,
        )


def test_run_probe_pins_discovery_to_frozen_scheduled_regions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = _probe_manifest()
    manifest["runtime_environment_policy"] = {
        "sanitize_prefixes": [],
        "sanitize_exact": ["TRAPI_REGIONS_OVERRIDE"],
        "set": {},
    }
    observed_overrides = []

    def fake_run(command, **kwargs):
        override = json.loads(kwargs["env"]["TRAPI_REGIONS_OVERRIDE"])
        observed_overrides.append(override)
        assert kwargs["env"]["AGENTARENA_PROBE_LIVE_ONLY"] == "1"
        assert (
            kwargs["env"]["AGENTARENA_PROBE_INCLUDE_REDMOND"] == "1"
        )
        stream = kwargs["stdout"]
        if command[-2:] == [
            str(campaign.SCRIPT_DIR / "probe_regions.py"),
            campaign.WEAK_LOGICAL,
        ]:
            stream.write(json.dumps({
                campaign.WEAK_LOGICAL: list(campaign.WEAK_REGIONS),
            }) + "\n")
        elif command[-2:] == [
            str(campaign.SCRIPT_DIR / "probe_concurrency.py"),
            campaign.WEAK_LOGICAL,
        ]:
            stream.write(
                "===== MAX SAFE CONCURRENCY (>=90% ok) =====\n"
                + json.dumps({
                    campaign.WEAK_LOGICAL: {
                        region: 16 for region in campaign.WEAK_REGIONS
                    },
                })
                + "\n"
            )
        else:
            pytest.fail(f"unexpected probe command: {command}")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        campaign, "verify_campaign", lambda *_args, **_kwargs: manifest
    )
    monkeypatch.setattr(
        campaign,
        "audit_refill_coexistence",
        lambda _base_port: _empty_coexistence(),
    )
    monkeypatch.setattr(campaign.subprocess, "run", fake_run)
    campaign.run_probe(tmp_path, "weak_before", "weak_routes")
    assert observed_overrides == [
        {campaign.WEAK_LOGICAL: list(campaign.WEAK_REGIONS)},
        {campaign.WEAK_LOGICAL: list(campaign.WEAK_REGIONS)},
    ]
    checkpoint = json.loads(
        (tmp_path / "probes" / "checkpoint_weak_routes.json").read_text()
    )
    assert checkpoint["probe_candidate_regions"] == list(
        campaign.WEAK_REGIONS
    )
    assert checkpoint["small_routes"] == list(campaign.WEAK_REGIONS)
    assert checkpoint["max_scheduled_primary_load"] == {
        "gcr/shared": 4,
        "msraif/shared": 4,
        "redmond/interactive": 4,
    }


def test_sol_probe_requires_every_frozen_large_route(tmp_path: Path) -> None:
    capacity = {region: 8 for region in campaign.SOL_REGIONS}
    small, concurrency, large = _probe_logs(
        tmp_path,
        campaign.SOL_LOGICAL,
        capacity,
        large=list(campaign.SOL_REGIONS),
    )
    evidence = campaign.validate_probe_evidence(
        _probe_manifest(),
        "sol_before",
        small,
        concurrency,
        large,
    )
    assert set(evidence["large_healthy_regions"]) == set(
        campaign.SOL_REGIONS
    )
    large.write_text(json.dumps(list(campaign.SOL_REGIONS[:2])))
    with pytest.raises(SystemExit, match="every frozen sol-high route"):
        campaign.validate_probe_evidence(
            _probe_manifest(),
            "sol_before",
            small,
            concurrency,
            large,
        )


def test_sol_probe_accepts_unscheduled_healthy_large_routes(
    tmp_path: Path,
) -> None:
    selected = ("msraif/shared", "redmond/interactive")
    manifest = _probe_manifest(sol_regions=selected)
    capacity = {region: 10 for region in selected}
    small, concurrency, large = _probe_logs(
        tmp_path,
        campaign.SOL_LOGICAL,
        capacity,
        large=list(campaign.SOL_REGIONS),
    )
    evidence = campaign.validate_probe_evidence(
        manifest,
        "sol_before",
        small,
        concurrency,
        large,
    )
    assert evidence["probe_candidate_regions"] == list(selected)
    assert evidence["max_scheduled_single_primary_failure_load"] == {
        "msraif/shared": 10,
        "redmond/interactive": 10,
    }
    assert evidence["large_healthy_regions"] == list(campaign.SOL_REGIONS)
    large.write_text(json.dumps(["msraif/shared"]))
    with pytest.raises(
        SystemExit,
        match="missing: redmond/interactive",
    ):
        campaign.validate_probe_evidence(
            manifest,
            "sol_before",
            small,
            concurrency,
            large,
        )


def _write_checkpoint(
    campaign_dir: Path,
    manifest: dict,
    stage: str,
    label: str,
) -> None:
    logical = (
        campaign.WEAK_LOGICAL
        if stage.startswith("weak")
        else campaign.SOL_LOGICAL
    )
    scheduled = manifest["probe_policy"]["scheduled_regions"][logical]
    capacities = {region: 16 for region in scheduled}
    probes = campaign_dir / "probes"
    probes.mkdir(parents=True, exist_ok=True)
    small, concurrency, large = _probe_logs(
        probes,
        logical,
        capacities,
        large=(
            list(scheduled)
            if logical == campaign.SOL_LOGICAL else None
        ),
    )
    evidence = campaign.validate_probe_evidence(
        manifest, stage, small, concurrency, large
    )
    paths = {
        "small": small,
        "concurrency": concurrency,
        **({"large": large} if large else {}),
    }
    record = {
        **evidence,
        "label": label,
        "published_at_utc": campaign._utcnow(),
        "predecessor_evidence": campaign._probe_predecessor_evidence(
            campaign_dir, manifest, stage
        ),
        "refill_coexistence": _empty_coexistence(
            manifest["base_port"]
        ),
        "logs": {
            name: {
                "path": str(path.relative_to(campaign_dir)),
                "sha256": campaign._sha_file(path),
                "size": path.stat().st_size,
            }
            for name, path in paths.items()
        },
    }
    (probes / f"checkpoint_{label}.json").write_text(
        json.dumps(record)
    )


def test_probe_checkpoint_revalidates_log_hashes(tmp_path: Path) -> None:
    manifest = _probe_manifest()
    _write_checkpoint(tmp_path, manifest, "weak_before", "probe1")
    verified = campaign.verify_probe_checkpoint(
        tmp_path,
        manifest,
        "probe1",
        expected_stage="weak_before",
        expected_block=1,
        expected_logical_model=campaign.WEAK_LOGICAL,
    )
    assert verified["record"]["logical_model"] == campaign.WEAK_LOGICAL
    small = tmp_path / verified["record"]["logs"]["small"]["path"]
    small.write_text(small.read_text() + "\ntampered")
    with pytest.raises(ValueError, match="hash/size drifted"):
        campaign.verify_probe_checkpoint(
            tmp_path, manifest, "probe1"
        )


def test_probe_checkpoint_is_fresh_at_launch_or_fails_closed(
    tmp_path: Path,
) -> None:
    manifest = _probe_manifest()
    _write_checkpoint(tmp_path, manifest, "weak_before", "probe1")
    checkpoint = json.loads(
        (tmp_path / "probes" / "checkpoint_probe1.json").read_text()
    )
    published = dt.datetime.fromisoformat(
        checkpoint["published_at_utc"].replace("Z", "+00:00")
    )
    campaign.verify_probe_checkpoint(
        tmp_path,
        manifest,
        "probe1",
        freshness_at_utc=(
            published + dt.timedelta(
                seconds=campaign.PROBE_MAX_AGE_SECONDS
            )
        ),
    )
    with pytest.raises(ValueError, match="stale"):
        campaign.verify_probe_checkpoint(
            tmp_path,
            manifest,
            "probe1",
            freshness_at_utc=(
                published + dt.timedelta(
                    seconds=campaign.PROBE_MAX_AGE_SECONDS + 1
                )
            ),
        )


def test_mid_probe_cannot_precede_its_completed_blocks(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError, match="precedes completed block evidence"
    ):
        campaign._probe_predecessor_evidence(
            tmp_path, _probe_manifest(), "weak_mid"
        )


def _provenance_manifest(tmp_path: Path) -> dict:
    manifest = _probe_manifest()
    manifest.update({
        "caps": campaign.CAPS,
        "limit_contract": _limit_contract(),
        "limit_near_fraction": campaign.LIMIT_NEAR_FRACTION,
        "source_inventory_sha256": "source-sha",
        "runtime_dependencies": {
            "sha256": "runtime-sha",
            "agent_behavior_limits": {
                "context_limits": _context_limits(),
            },
        },
        "runtime_environment_policy": {
            "sha256": "policy-sha",
            "set": {"AGENTARENA_CELL_TIMEOUT": "172800"},
        },
    })
    (tmp_path / "campaign_manifest.json").write_text(
        json.dumps(manifest)
    )
    return manifest


def test_launch_receipt_binds_run_manifest_and_checkpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = _provenance_manifest(tmp_path)
    row = manifest["schedule"][0]
    _write_checkpoint(tmp_path, manifest, "weak_before", "probe1")
    launcher = tmp_path / row["launcher_log_relpath"]
    launcher.parent.mkdir(parents=True)
    launcher.write_text("launcher")
    monkeypatch.setattr(
        campaign,
        "verify_smoke_gate",
        lambda *_args, **_kwargs: {"sha256": "smoke-sha"},
    )
    monkeypatch.setattr(
        campaign,
        "audit_refill_coexistence",
        lambda _base_port: _empty_coexistence(),
    )
    env = {
        "AGENTARENA_CELL_TIMEOUT": "172800",
        "TRAPI_REGIONS_OVERRIDE": json.dumps(
            {row["logical_model"]: row["region_order"]},
            separators=(",", ":"),
        ),
    }
    campaign._write_receipt(
        tmp_path, manifest, row, "probe1", env
    )
    verified = campaign.verify_launch_receipt(
        tmp_path, manifest, row
    )
    assert verified["receipt"]["run_id"] == row["run_id"]
    receipt_path = tmp_path / "launch_receipts" / f"{row['run_id']}.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["port"] += 1
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="receipt hash drifted"):
        campaign.verify_launch_receipt(tmp_path, manifest, row)


def test_reporter_rejects_completed_run_without_launch_receipt(
    tmp_path: Path,
) -> None:
    manifest = _provenance_manifest(tmp_path)
    row_spec = manifest["schedule"][0]
    _write_run(
        tmp_path,
        row_spec,
        stats={"deliberative": _complete_diagnostics()},
    )
    row = reporter._row(
        tmp_path,
        row_spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        manifest["limit_near_fraction"],
        manifest,
    )
    assert row["aggregate_eligible"] is False
    assert any(
        "launch provenance invalid" in reason
        for reason in row["field_errors"]
    )


def test_purchase_roles_are_frozen_anchor_exact() -> None:
    info = {
        "asins": {"H", "L", "C", "P", "O"},
        "hero": "H",
        "low_primary": "L",
        "choice": "C",
        "pins": {"L", "C", "P"},
    }
    assert reporter._purchase_role(None, info) == "no_order"
    assert reporter._purchase_role("H", info) == "hero"
    assert reporter._purchase_role("L", info) == "pin_low_primary"
    assert reporter._purchase_role("C", info) == "pin_choice_frontier"
    assert reporter._purchase_role("P", info) == "pinned_other"
    assert reporter._purchase_role("O", info) == "other"
    assert reporter._purchase_role("X", info) == "off_catalog"


def _complete_diagnostics() -> dict:
    contract = {
        "constraints": [{}, {}],
        "instruction": "Choose an option.",
        "objectives": [{}, {}],
        "search_mode": "best_available",
    }
    contract_json = json.dumps(
        contract, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    encoded = contract_json.encode("utf-8")
    return {
        "contract_compile_calls": 1,
        "contract_sha256": hashlib.sha256(
            len(encoded).to_bytes(8, "big") + encoded
        ).hexdigest(),
        "contract_canonical_json": contract_json,
        "constraint_count": 2,
        "objective_count": 2,
        "search_mode": "best_available",
        "decision_checkpoint_calls": 1,
        "decision_checkpoint_rejections": 0,
        "decision_checkpoint_approvals": 1,
        "checkpoint_candidate_count": 30,
        "frontier_inspected_count": 30,
        "frontier_advertised_count": 30,
        "frontier_coverage_mode": "advertised_total",
        "frontier_advertised_page_count": 0,
        "frontier_enumerated_page_count": 0,
        "approved_candidate_id": "H",
        "structured_max_attempts_observed": 1,
        "structured_attempt_exhaustions": 0,
        "auxiliary_calls": 2,
        "auxiliary_tokens": 4000,
        "auxiliary_seconds": 10.5,
        "limit_observations": {
            "structured_response_attempts": {
                "touched_count": 0,
                "observations": {
                    "max_attempts": 4,
                    "attempts": 1,
                    "rejected_attempts": 0,
                    "exhaustions": 0,
                },
            },
        },
    }


def _write_run(
    root: Path,
    spec: dict,
    *,
    pstar: float = 1.0,
    binary: float = 1.0,
    steps: int = 20,
    stats: dict | None = None,
    outcome: str = "compliant",
    chosen: str | None = "H",
    error: str | None = None,
) -> None:
    cell = root / spec["browser_run_relpath"]
    cell.mkdir(parents=True)
    summary = {
        "schema": 2,
        "env": "amazon",
        "scaffold": spec["scaffold"],
        "model": spec["model_recorded"],
        "task_id": f"{spec['scenario']}-graded",
        "condition": spec["condition"],
        "num_steps": steps,
        "seconds": 120.0,
        "outcome": outcome,
        "chosen": chosen,
        "chosen_label": "Hero" if chosen else None,
        "error": error,
        "preservation_strict": pstar,
        "strict_binary": binary,
    }
    (cell / "summary.json").write_text(json.dumps(summary))
    supplied_stats = dict(stats or {})
    evaluate_store = supplied_stats.get(
        "evaluate_result_store",
        _complete_evaluate_result_store(),
    )
    action_error_audit = supplied_stats.get(
        "action_error_audit",
        _complete_action_error_audit(),
    )
    limit_audit = supplied_stats.get(
        "limit_audit",
        _complete_limit_audit(
            spec["arm"],
            evaluate_store=evaluate_store,
            action_error_audit=action_error_audit,
        ),
    )
    trajectory = {
        "stats": {
            "seconds": 120.0,
            "num_steps": steps,
            "context_cap_audit": _complete_context_cap_audit(),
            "action_error_audit": action_error_audit,
            "evaluate_result_store": evaluate_store,
            "limit_audit": limit_audit,
            **supplied_stats,
        },
        "steps": [{} for _ in range(steps)],
    }
    (cell / "trajectory.json").write_text(json.dumps(trajectory))
    (cell / "run.log").write_text("normal run")
    launcher = root / spec["launcher_log_relpath"]
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("normal launcher")


def _read_written_row(root: Path, spec: dict) -> dict:
    return reporter._row(
        root,
        spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        campaign.LIMIT_NEAR_FRACTION,
    )


def test_deliberative_row_reports_coverage_reviews_and_caps(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "deliberative"
    )
    _write_run(
        tmp_path,
        spec,
        stats={"deliberative": _complete_diagnostics()},
    )
    row = reporter._row(
        tmp_path,
        spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        campaign.LIMIT_NEAR_FRACTION,
    )
    assert row["aggregate_eligible"] is True
    assert row["diagnostics_complete"] is True
    assert row["coverage"]["checkpoint_candidate_count"] == 30
    assert row["deliberative_diagnostics"]["decision_checkpoint_calls"] == 1
    assert row["no_bound"] is True


def test_deliberative_row_accepts_valid_finite_page_diagnostics(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "deliberative"
    )
    diagnostics = _complete_diagnostics()
    diagnostics.update({
        "frontier_inspected_count": 2112,
        "frontier_advertised_count": 0,
        "frontier_coverage_mode": "finite_pages",
        "frontier_advertised_page_count": 88,
        "frontier_enumerated_page_count": 88,
    })
    _write_run(
        tmp_path,
        spec,
        stats={"deliberative": diagnostics},
    )

    row = _read_written_row(tmp_path, spec)

    assert row["diagnostics_complete"] is True
    assert row["aggregate_eligible"] is True
    assert row["coverage"]["frontier_coverage_mode"] == "finite_pages"
    assert row["coverage"]["frontier_enumerated_page_count"] == 88


def test_mismatched_finite_page_claim_remains_a_measured_model_failure(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "deliberative"
    )
    diagnostics = _complete_diagnostics()
    diagnostics.update({
        "frontier_advertised_count": 0,
        "frontier_coverage_mode": "finite_pages",
        "frontier_advertised_page_count": 88,
        "frontier_enumerated_page_count": 87,
    })
    _write_run(
        tmp_path,
        spec,
        stats={"deliberative": diagnostics},
    )

    row = _read_written_row(tmp_path, spec)

    assert row["diagnostics_complete"] is True
    assert row["aggregate_eligible"] is True
    assert row["coverage"]["frontier_advertised_page_count"] == 88
    assert row["coverage"]["frontier_enumerated_page_count"] == 87


def test_checkpoint_outcome_counters_must_partition_handler_calls(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "deliberative"
    )
    diagnostics = _complete_diagnostics()
    diagnostics["decision_checkpoint_approvals"] = 2
    _write_run(
        tmp_path,
        spec,
        stats={"deliberative": diagnostics},
    )

    row = _read_written_row(tmp_path, spec)

    assert row["diagnostics_complete"] is False
    assert row["aggregate_eligible"] is False


def test_uncalled_agent_checkpoint_is_observed_not_invalid(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "deliberative"
    )
    diagnostics = _complete_diagnostics()
    diagnostics.update({
        "decision_checkpoint_calls": 0,
        "decision_checkpoint_rejections": 0,
        "decision_checkpoint_approvals": 0,
        "checkpoint_candidate_count": 0,
        "frontier_inspected_count": 0,
        "frontier_advertised_count": 0,
        "frontier_coverage_mode": None,
        "frontier_advertised_page_count": 0,
        "frontier_enumerated_page_count": 0,
        "approved_candidate_id": None,
    })
    _write_run(
        tmp_path,
        spec,
        stats={"deliberative": diagnostics},
    )

    row = _read_written_row(tmp_path, spec)

    assert row["diagnostics_complete"] is True
    assert row["deliberative_diagnostics"][
        "decision_checkpoint_calls"
    ] == 0
    assert row["aggregate_eligible"] is True


def test_final_structured_attempt_is_a_declared_safety_touch(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "deliberative"
    )
    diagnostics = _complete_diagnostics()
    diagnostics.update({
        "structured_max_attempts_observed": 4,
        "structured_attempt_exhaustions": 0,
    })
    diagnostics["limit_observations"]["structured_response_attempts"] = {
        "touched_count": 1,
        "observations": {
            "max_attempts": 4,
            "attempts": 4,
            "rejected_attempts": 3,
            "exhaustions": 0,
        },
    }
    limit_audit = _complete_limit_audit(
        "deliberative",
        touched=("safety_backstops", "structured_response_attempts"),
    )
    _write_run(
        tmp_path,
        spec,
        stats={
            "deliberative": diagnostics,
            "limit_audit": limit_audit,
        },
    )

    row = _read_written_row(tmp_path, spec)

    assert row["diagnostics_complete"] is True
    assert row["no_bound"] is False
    assert row["aggregate_eligible"] is False
    assert "a harness or evidence backstop touched" in (
        row["exclusion_reasons"]
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("contract_sha256", "not-a-sha256"),
        ("constraint_count", -1),
        ("objective_count", "two"),
        ("search_mode", "guess"),
    ),
)
def test_contract_diagnostics_are_required_and_typed(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "deliberative"
    )
    diagnostics = _complete_diagnostics()
    diagnostics[field] = value
    _write_run(
        tmp_path,
        spec,
        stats={"deliberative": diagnostics},
    )

    row = _read_written_row(tmp_path, spec)

    assert row["diagnostics_complete"] is False
    assert row["aggregate_eligible"] is False


def test_deliberative_limit_observation_inventory_is_exact(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "deliberative"
    )
    diagnostics = _complete_diagnostics()
    diagnostics["limit_observations"] = {}
    _write_run(
        tmp_path,
        spec,
        stats={"deliberative": diagnostics},
    )

    row = _read_written_row(tmp_path, spec)

    assert row["diagnostics_complete"] is False
    assert "limit_observations" in row[
        "deliberative_diagnostics"
    ]["malformed_fields"]
    assert row["aggregate_eligible"] is False


@pytest.mark.parametrize("arm", ["baseline", "deliberative"])
def test_row_fails_closed_without_common_context_cap_audit(
    tmp_path: Path,
    arm: str,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == arm
    )
    stats = {"context_cap_audit": None}
    if arm == "deliberative":
        stats["deliberative"] = _complete_diagnostics()
    _write_run(tmp_path, spec, stats=stats)
    row = reporter._row(
        tmp_path,
        spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        campaign.LIMIT_NEAR_FRACTION,
    )
    assert row["aggregate_eligible"] is False
    assert row["context_cap_audit_complete"] is False
    assert row["no_bound"] is False
    assert any(
        "context-cap instrumentation" in reason
        for reason in row["field_errors"]
    )


@pytest.mark.parametrize(
    "cap_name",
    sorted(
        _limit_contract()["categories"]["lossy_context_limits"]
    ),
)
def test_each_common_context_cap_touch_is_visible_and_excluded(
    tmp_path: Path,
    cap_name: str,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "baseline"
    )
    _write_run(
        tmp_path,
        spec,
        stats={
            "context_cap_audit": _complete_context_cap_audit(
                touched=cap_name
            ),
        },
    )
    row = reporter._row(
        tmp_path,
        spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        campaign.LIMIT_NEAR_FRACTION,
    )
    assert row["context_cap_audit"][cap_name]["touched_count"] == 1
    assert row["aggregate_eligible"] is False
    assert row["no_bound"] is False


def test_fixed_extract_externalization_is_visible_and_not_excluded(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "baseline"
    )
    _write_run(
        tmp_path,
        spec,
        stats={
            "context_cap_audit": _complete_context_cap_audit(
                touched="extract_memory_chars"
            ),
            "limit_audit": _complete_limit_audit(
                "baseline",
                touched=(
                    "fixed_architecture",
                    "extract_result_file_externalization",
                ),
            ),
        },
    )
    row = _read_written_row(tmp_path, spec)

    assert row["context_cap_audit"]["extract_memory_chars"][
        "touched_count"
    ] == 1
    assert row["aggregate_eligible"] is True
    assert row["no_bound"] is True


def test_known_agent_output_validation_rendering_is_fixed_not_lossy(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "baseline"
    )
    action = _complete_action_error_audit(
        agent_output_validation=(120, 20168, 30235),
    )
    _write_run(
        tmp_path,
        spec,
        stats={
            "context_cap_audit": _context_audit_for_action_errors(
                action
            ),
            "action_error_audit": action,
            "limit_audit": _complete_limit_audit(
                "baseline", action_error_audit=action
            ),
        },
    )

    row = _read_written_row(tmp_path, spec)

    assert row["action_error_audit_complete"] is True
    assert row["context_cap_audit"]["action_error_chars"] == {
        "configured": 20000,
        "touched_count": 2,
        "max_observed": 30235,
    }
    categories = row["limit_audit"]["categories"]
    assert categories["fixed_architecture"][
        "agent_output_validation_feedback_rendering"
    ]["touched_count"] == 2
    assert categories["lossy_context_limits"][
        "action_error_chars"
    ]["touched_count"] == 0
    assert row["aggregate_eligible"] is True
    assert row["no_bound"] is True


def test_unknown_long_action_error_remains_lossy_and_excluded(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "baseline"
    )
    action = _complete_action_error_audit(
        other_or_unknown=(30235,),
    )
    _write_run(
        tmp_path,
        spec,
        stats={
            "context_cap_audit": _context_audit_for_action_errors(
                action
            ),
            "action_error_audit": action,
            "limit_audit": _complete_limit_audit(
                "baseline", action_error_audit=action
            ),
        },
    )

    row = _read_written_row(tmp_path, spec)

    assert row["action_error_audit_complete"] is True
    assert row["limit_audit"]["categories"][
        "lossy_context_limits"
    ]["action_error_chars"]["touched_count"] == 1
    assert row["aggregate_eligible"] is False
    assert row["no_bound"] is False


@pytest.mark.parametrize(
    "tamper",
    ("raw", "fixed", "lossy", "detailed"),
)
def test_action_error_partition_mismatch_is_fail_closed(
    tmp_path: Path,
    tamper: str,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "baseline"
    )
    action = _complete_action_error_audit(
        agent_output_validation=(30235,),
    )
    context = _context_audit_for_action_errors(action)
    limit = _complete_limit_audit(
        "baseline", action_error_audit=action
    )
    if tamper == "raw":
        context["limits"]["action_error_chars"]["max_observed"] -= 1
    elif tamper == "fixed":
        limit["categories"]["fixed_architecture"][
            "agent_output_validation_feedback_rendering"
        ]["observations"]["all_max_chars"] -= 1
    elif tamper == "lossy":
        limit["categories"]["lossy_context_limits"][
            "action_error_chars"
        ]["observations"]["raw_max_observed"] -= 1
    else:
        action["all"]["over_cap_count"] = 0
    _write_run(
        tmp_path,
        spec,
        stats={
            "context_cap_audit": context,
            "action_error_audit": action,
            "limit_audit": limit,
        },
    )

    row = _read_written_row(tmp_path, spec)

    assert row["action_error_audit_complete"] is False
    assert row["aggregate_eligible"] is False
    assert row["no_bound"] is False


def test_action_error_partition_is_not_retrofitted_to_legacy_contract() -> None:
    legacy = json.loads(json.dumps(_limit_contract()))
    legacy["categories"]["fixed_architecture"].pop(
        "agent_output_validation_feedback_rendering"
    )
    raw = _complete_context_cap_audit(touched="action_error_chars")
    normalized, errors = campaign.validate_action_error_partition(
        {}, raw, {}, legacy
    )

    assert normalized == {}
    assert errors == []


def test_context_cap_config_drift_is_fail_closed(tmp_path: Path) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "baseline"
    )
    _write_run(
        tmp_path,
        spec,
        stats={
            "context_cap_audit": _complete_context_cap_audit(
                configured_override=("action_error_chars", 20001)
            ),
        },
    )
    row = reporter._row(
        tmp_path,
        spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        campaign.LIMIT_NEAR_FRACTION,
    )
    assert row["aggregate_eligible"] is False
    assert any(
        "configured value drifted" in reason
        for reason in row["field_errors"]
    )


def test_row_fails_closed_without_exact_common_limit_audit(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "baseline"
    )
    _write_run(tmp_path, spec, stats={"limit_audit": None})

    row = _read_written_row(tmp_path, spec)

    assert row["limit_audit_complete"] is False
    assert row["aggregate_eligible"] is False
    assert row["no_bound"] is False
    assert any(
        "limit_audit instrumentation" in error
        for error in row["field_errors"]
    )


def test_declared_safety_limit_touch_is_visible_and_excluded(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "baseline"
    )
    audit = _complete_limit_audit(
        "baseline",
        touched=("safety_backstops", "dependency_inner_timeouts"),
    )
    _write_run(tmp_path, spec, stats={"limit_audit": audit})

    row = _read_written_row(tmp_path, spec)

    assert row["limit_audit_complete"] is True
    assert (
        row["limit_audit"]["categories"]["safety_backstops"][
            "dependency_inner_timeouts"
        ]["touched_count"]
        == 1
    )
    assert row["aggregate_eligible"] is False
    assert row["no_bound"] is False


def test_fixed_architecture_observation_is_not_a_safety_exclusion(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "baseline"
    )
    audit = _complete_limit_audit(
        "baseline",
        touched=("fixed_architecture", "max_actions_per_step"),
    )
    _write_run(tmp_path, spec, stats={"limit_audit": audit})

    row = _read_written_row(tmp_path, spec)

    assert row["limit_audit_complete"] is True
    assert (
        row["limit_audit"]["categories"]["fixed_architecture"][
            "max_actions_per_step"
        ]["touched_count"]
        == 1
    )
    assert row["aggregate_eligible"] is True
    assert row["no_bound"] is True


def test_pre_agent_zero_audit_keeps_descriptive_failure_eligible(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "deliberative"
    )
    _write_run(
        tmp_path,
        spec,
        pstar=0.0,
        binary=0.0,
        steps=0,
        outcome="none",
        chosen=None,
        error=(
            "RuntimeError: contract compiler semantic verification failed"
        ),
        stats={
            "context_cap_audit": _pre_agent_context_cap_audit(),
            "deliberative": _complete_diagnostics(),
        },
    )

    row = reporter._row(
        tmp_path,
        spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        campaign.LIMIT_NEAR_FRACTION,
    )

    assert row["preservation_strict"] == 0.0
    assert row["strict_binary"] == 0.0
    assert row["context_cap_audit_complete"] is True
    assert row["no_bound"] is True
    assert row["aggregate_eligible"] is True
    assert row["refillable"] is False
    assert row["deliberative_diagnostics"]["contract_compile_calls"] == 1


def test_pre_agent_audit_with_nonzero_observation_fails_closed(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "baseline"
    )
    audit = _pre_agent_context_cap_audit()
    audit["limits"]["action_error_chars"]["max_observed"] = 1
    _write_run(
        tmp_path,
        spec,
        stats={"context_cap_audit": audit},
    )

    row = reporter._row(
        tmp_path,
        spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        campaign.LIMIT_NEAR_FRACTION,
    )

    assert row["context_cap_audit_complete"] is False
    assert row["aggregate_eligible"] is False
    assert any(
        "pre-Agent audit contains nonzero observations" in reason
        for reason in row["field_errors"]
    )


@pytest.mark.parametrize(
    ("kind", "store"),
    [
        (
            "single",
            _complete_evaluate_result_store(
                byte_count=20001,
                responses=1,
                max_serialized_chars=16 * 1024**2,
            ),
        ),
        (
            "bytes",
            _complete_evaluate_result_store(
                byte_count=2 * 1024**3,
                responses=1,
                max_serialized_chars=20001,
            ),
        ),
        (
            "responses",
            _complete_evaluate_result_store(
                byte_count=20001,
                responses=50000,
                records=1,
                max_serialized_chars=20001,
            ),
        ),
    ],
)
def test_evaluate_result_near_threshold_is_visible_and_not_clear(
    tmp_path: Path,
    kind: str,
    store: dict,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "baseline"
    )
    _write_run(
        tmp_path,
        spec,
        stats={"evaluate_result_store": store},
    )
    row = _read_written_row(tmp_path, spec)
    assert row["aggregate_eligible"] is True
    assert row["cap_audit"]["evaluate_store_near_25_percent"] is True
    assert row["no_bound"] is False
    utilization_name = {
        "single": "evaluate_single_utilization",
        "bytes": "evaluate_store_byte_utilization",
        "responses": "evaluate_store_response_utilization",
    }[kind]
    assert row["cap_audit"][utilization_name] >= 0.25


def test_evaluate_result_store_impossible_tuple_is_fail_closed(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "baseline"
    )
    impossible = _complete_evaluate_result_store(
        byte_count=0,
        responses=1,
        records=0,
        max_serialized_chars=0,
    )
    _write_run(
        tmp_path,
        spec,
        stats={"evaluate_result_store": impossible},
    )
    row = _read_written_row(tmp_path, spec)
    assert row["aggregate_eligible"] is False
    assert row["evaluate_result_store_complete"] is False
    assert any(
        "usage is internally inconsistent" in error
        for error in row["field_errors"]
    )


def test_evaluate_result_store_utilization_must_cross_bind(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "baseline"
    )
    store = _complete_evaluate_result_store(
        byte_count=20001,
        responses=1,
        max_serialized_chars=20001,
    )
    audit = _complete_limit_audit(
        "baseline",
        evaluate_store=store,
    )
    audit["categories"]["safety_backstops"][
        "evaluate_result_store_bytes"
    ]["observations"]["utilization"] = 999
    _write_run(
        tmp_path,
        spec,
        stats={
            "evaluate_result_store": store,
            "limit_audit": audit,
        },
    )
    row = _read_written_row(tmp_path, spec)
    assert row["aggregate_eligible"] is False
    assert row["evaluate_result_store_complete"] is False
    assert any(
        "observations and limit audit disagree" in error
        for error in row["field_errors"]
    )


@pytest.mark.parametrize(
    ("counter", "limit_name"),
    [
        (
            "byte_bound_touched_count",
            "evaluate_result_store_bytes",
        ),
        (
            "response_bound_touched_count",
            "evaluate_result_store_responses",
        ),
    ],
)
def test_rejected_spill_write_is_a_core_ceiling_touch_below_capacity(
    tmp_path: Path,
    counter: str,
    limit_name: str,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["arm"] == "baseline"
    )
    store = _complete_evaluate_result_store(
        byte_count=20001,
        responses=1,
        max_serialized_chars=20001,
    )
    store[counter] = 1
    _write_run(
        tmp_path,
        spec,
        stats={"evaluate_result_store": store},
    )
    row = _read_written_row(tmp_path, spec)
    assert row["aggregate_eligible"] is False
    assert (
        row["cap_audit"]["evaluate_store_byte_utilization"] < 1
    )
    assert (
        row["cap_audit"]["evaluate_store_response_utilization"] < 1
    )
    ceiling = reporter._ceiling_audit([row], {
        "caps": campaign.CAPS,
        "limit_contract": _limit_contract(),
        "runtime_dependencies": {
            "agent_behavior_limits": {
                "fallback_llm_depth": 1,
                "max_actions_per_step": 5,
                "context_limits": _context_limits(),
            },
        },
        "limit_near_fraction": campaign.LIMIT_NEAR_FRACTION,
    })
    assert ceiling["core"][limit_name]["touched_runs"] == [
        spec["run_id"]
    ]


def test_zero_step_local_scaffold_error_is_not_external_infra(
    tmp_path: Path,
) -> None:
    spec = campaign.build_schedule("eval", 17000)[0]
    _write_run(
        tmp_path,
        spec,
        pstar=0.0,
        binary=0.0,
        steps=0,
        outcome="error",
        chosen=None,
        error="ValueError: local contract parser bug",
    )
    row = reporter._row(
        tmp_path,
        spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        campaign.LIMIT_NEAR_FRACTION,
    )
    assert row["external_infrastructure_evidence"] == []
    assert row["refillable"] is False
    assert row["aggregate_eligible"] is True
    assert row["preservation_strict"] == 0.0


@pytest.mark.parametrize(
    "error",
    [
        "openai.InternalServerError: Error code: 503",
        "RateLimitError: status_code=429",
        "ServiceUnavailableError: HTTP 502",
    ],
)
def test_terminal_openai_429_and_5xx_are_external_refills(
    tmp_path: Path,
    error: str,
) -> None:
    spec = campaign.build_schedule("eval", 17000)[0]
    _write_run(
        tmp_path,
        spec,
        pstar=0.0,
        binary=0.0,
        steps=3,
        outcome="error",
        chosen=None,
        error=error,
    )
    row = reporter._row(
        tmp_path,
        spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        campaign.LIMIT_NEAR_FRACTION,
    )
    assert row["external_infrastructure_evidence"]
    assert row["refillable"] is True
    assert row["aggregate_eligible"] is False


def test_evaluator_get_retry_exhaustion_is_external_and_refillable(
    tmp_path: Path,
) -> None:
    spec = campaign.build_schedule("eval", 17000)[0]
    marker = "AGENTARENA_EVALUATOR_GET_RETRIES_EXHAUSTED"
    _write_run(
        tmp_path,
        spec,
        pstar=0.0,
        binary=0.0,
        steps=21,
        outcome="error",
        chosen=None,
        error=f"RuntimeError: {marker}: evaluator endpoint unavailable",
    )

    row = _read_written_row(tmp_path, spec)
    classified = infra_classifier.classify_run(
        str(tmp_path / spec["browser_run_relpath"])
    )

    assert row["external_infrastructure_evidence"] == [marker]
    assert row["terminal_external_infrastructure_evidence"] == [marker]
    assert row["refillable"] is True
    assert row["aggregate_eligible"] is False
    assert classified["class"] == infra_classifier.INFRA
    assert classified["code"] == "evaluator_get_exhausted"
    assert classified["steps"] == 21


def test_recovered_same_model_region_fallback_remains_eligible(
    tmp_path: Path,
) -> None:
    spec = next(
        row for row in campaign.build_schedule("eval", 17000)
        if row["logical_model"] == campaign.SOL_LOGICAL
    )
    _write_run(
        tmp_path,
        spec,
        stats=(
            {"deliberative": _complete_diagnostics()}
            if spec["arm"] == "deliberative" else None
        ),
    )
    run_log = tmp_path / spec["run_log_relpath"]
    run_log.write_text(
        "Primary LLM (gpt-5.6-sol) failed with ModelProviderError "
        "(status=502), switching to fallback LLM (gpt-5.6-sol)\n"
        "run recovered and completed"
    )
    row = reporter._row(
        tmp_path,
        spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        campaign.LIMIT_NEAR_FRACTION,
    )
    assert row["recovered_same_model_fallback"]
    assert row["aggregate_eligible"] is True
    assert row["no_bound"] is True


def test_unexpected_fallback_on_single_region_weak_is_confounding(
    tmp_path: Path,
) -> None:
    spec = campaign.build_schedule(
        "eval",
        17000,
        weak_regions=("redmond/interactive",),
    )[0]
    _write_run(tmp_path, spec)
    run_log = tmp_path / spec["run_log_relpath"]
    run_log.write_text(
        "Primary LLM (Kimi-K2.6) failed with ModelProviderError, "
        "switching to fallback LLM\n"
        "run recovered and completed"
    )
    row = reporter._row(
        tmp_path,
        spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        campaign.LIMIT_NEAR_FRACTION,
    )
    assert row["recovered_same_model_fallback"] is None
    assert row["aggregate_eligible"] is False
    assert row["no_bound"] is False


def test_step_backstop_is_excluded_but_not_redrawn(tmp_path: Path) -> None:
    spec = campaign.build_schedule("eval", 17000)[0]
    _write_run(tmp_path, spec, steps=campaign.CAPS["max_steps"])
    row = reporter._row(
        tmp_path,
        spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        campaign.LIMIT_NEAR_FRACTION,
    )
    assert row["aggregate_eligible"] is False
    assert row["refillable"] is False
    assert row["no_bound"] is False


def _synthetic_rows(cohort: str, repeats: int) -> list[dict]:
    rows = []
    scenarios = (
        campaign.HARD_SCENARIOS
        if cohort == "sol_high_hard"
        else campaign.EASY_SCENARIOS
    )
    for scenario in scenarios:
        for repeat in range(1, repeats + 1):
            rows.extend([
                {
                    "cohort": cohort,
                    "scenario": scenario,
                    "repeat": repeat,
                    "arm": "baseline",
                    "run_id": f"{scenario}-{repeat}-b",
                    "preservation_strict": 0.2,
                    "strict_binary": 0.0,
                },
                {
                    "cohort": cohort,
                    "scenario": scenario,
                    "repeat": repeat,
                    "arm": "deliberative",
                    "run_id": f"{scenario}-{repeat}-d",
                    "preservation_strict": 0.6,
                    "strict_binary": 1.0,
                },
            ])
    return rows


def test_paired_statistics_keep_scenario_clusters() -> None:
    result = reporter._cohort_result(
        _synthetic_rows("weak_easy_combined", 3),
        "weak_easy_combined",
    )
    assert result["n_run_pairs"] == 15
    assert result["n_scenario_clusters"] == 5
    assert result["inference_unit"] == "scenario_cluster"
    assert result["baseline_mean_pstar"] == pytest.approx(0.2)
    assert result["deliberative_mean_pstar"] == pytest.approx(0.6)
    assert result["paired_run_mean_delta_pstar"] == pytest.approx(0.4)
    assert result["mean_scenario_cluster_delta_pstar"] == pytest.approx(
        0.4
    )
    assert result["scenario_clusters_improved"] == 5
    assert result["additional_strict_successes"] == 15
    assert result["scenario_cluster_bootstrap_95_ci"] == pytest.approx(
        [0.4, 0.4]
    )
    assert result[
        "one_sided_exact_scenario_cluster_sign_randomization_p"
    ] == pytest.approx(1 / 32)
    assert result["scenario_cluster_randomization_assignments"] == 32
    assert result[
        "scenario_cluster_randomization_p_resolution"
    ] == pytest.approx(1 / 32)
    assert "one_sided_exact_randomization_p" not in result


def test_holm_uses_only_scenario_cluster_randomization_p() -> None:
    results = {
        cohort: reporter._cohort_result(
            _synthetic_rows(cohort, repeats),
            cohort,
        )
        for cohort, repeats in (
            ("weak_easy_combined", 3),
            ("weak_easy_clean", 1),
            ("sol_high_hard", 2),
        )
    }

    reporter._holm_primary(results)

    for cohort in ("weak_easy_combined", "sol_high_hard"):
        result = results[cohort]
        # Five independent clusters give a minimum raw p of 1/32. With two
        # primary hypotheses, tied minimum p-values Holm-adjust to 1/16.
        assert result[
            "holm_adjusted_scenario_cluster_p"
        ] == pytest.approx(1 / 16)
        assert (
            result[
                "scenario_cluster_inference_significant_after_holm"
            ]
            is False
        )
        assert "holm_adjusted_p" not in result


def test_markdown_names_scenario_cluster_inference_and_resolution() -> None:
    results = {
        cohort: reporter._cohort_result(
            _synthetic_rows(cohort, repeats),
            cohort,
        )
        for cohort, repeats in (
            ("weak_easy_combined", 3),
            ("weak_easy_clean", 1),
            ("sol_high_hard", 2),
        )
    }
    reporter._holm_primary(results)
    text = reporter.markdown_report({
        "validity": {
            "green": True,
            "observed_summaries": 60,
            "expected_runs": 60,
            "refill_run_ids": [],
            "smoke_gate_valid": True,
            "smoke_gate_error": None,
            "diagnostics_incomplete_runs": [],
            "bound_or_near_bound_runs": [],
            "fresh_strict_rescore": True,
            "unmeasured_cap_confounds": [],
        },
        "results": results,
        "runs": [],
    })

    assert "inferential unit is the **scenario cluster**" in text
    assert "2⁵=32 sign assignments" in text
    assert "1/32 (0.03125)" in text
    assert "Holm-adjusted scenario-cluster p" in text
    assert "95% cluster CI" not in text


def test_fresh_strict_rescore_audit_requires_every_scheduled_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import agentarena.scoring.rescore as rescore

    schedule = campaign.build_schedule("eval", 17000)[:2]
    for spec in schedule:
        _write_run(tmp_path, spec)

    def fake_write_strict(experiment: str) -> int:
        updated = 0
        for summary_path in Path(experiment).glob("*/summary.json"):
            summary = json.loads(summary_path.read_text())
            summary.update({
                "preservation_strict": 1.0,
                "preservation_cont": 1.0,
                "strict_binary": 1.0,
                "resistance_margin": 1.0,
            })
            summary_path.write_text(json.dumps(summary))
            updated += 1
        return updated

    monkeypatch.setattr(rescore, "write_strict", fake_write_strict)
    audit = reporter._rescore({"schedule": schedule}, tmp_path)
    assert audit["performed"] is True
    assert audit["updated_summary_count"] == 2
    assert audit["output_count"] == 2
    assert audit["fresh_complete"] is True
    (
        tmp_path / schedule[1]["summary_relpath"]
    ).unlink()
    incomplete = reporter._rescore({"schedule": schedule}, tmp_path)
    assert incomplete["fresh_complete"] is False
    assert incomplete["updated_summary_count"] == 1
    assert incomplete["output_count"] == 1


def test_ceiling_audit_is_per_model_and_all_context_caps_are_measured() -> None:
    rows = []
    for spec in campaign.build_schedule("eval", 17000):
        rows.append({
            "run_id": spec["run_id"],
            "model_request": spec["model_request"],
            "no_bound": True,
            "cap_audit": {
                "step_utilization": 0.01,
                "time_utilization": 0.02,
                "archive_byte_utilization": (
                    0.001 if spec["arm"] == "deliberative" else None
                ),
                    "archive_response_utilization": (
                        0.002 if spec["arm"] == "deliberative" else None
                    ),
                    "evaluate_store_byte_utilization": 0.0,
                    "evaluate_store_response_utilization": 0.0,
                    "evaluate_single_utilization": 0.0,
                "harness_signatures": {},
                "confound_signatures": {},
                "event_timeout_signatures": {},
                "context_caps": (
                    _complete_context_cap_audit()["limits"]
                ),
                    "context_cap_audit_complete": True,
                    "action_error_audit": _complete_action_error_audit(),
                    "action_error_audit_complete": True,
                    "limit_categories": _complete_limit_audit(
                    spec["arm"]
                )["categories"],
                "limit_audit_complete": True,
            },
                "arm": spec["arm"],
                "limit_audit_complete": True,
                "action_error_audit": _complete_action_error_audit(),
                "action_error_audit_complete": True,
                "evaluate_result_store_complete": True,
            })
    known_action = _complete_action_error_audit(
        agent_output_validation=(30235,),
    )
    rows[0]["action_error_audit"] = known_action
    rows[0]["cap_audit"]["action_error_audit"] = known_action
    rows[0]["cap_audit"]["context_caps"] = (
        _context_audit_for_action_errors(known_action)["limits"]
    )
    rows[0]["cap_audit"]["limit_categories"] = (
        _complete_limit_audit(
            rows[0]["arm"], action_error_audit=known_action
        )["categories"]
    )
    audit = reporter._ceiling_audit(rows, {
        "caps": campaign.CAPS,
        "limit_contract": _limit_contract(),
        "runtime_dependencies": {
            "agent_behavior_limits": {
                "fallback_llm_depth": 1,
                "max_actions_per_step": 5,
                "context_limits": _context_limits(),
            },
        },
        "limit_near_fraction": campaign.LIMIT_NEAR_FRACTION,
    })
    assert audit["inventory_complete"] is True
    assert (
        audit["all_ceilings_untouched_and_below_near_threshold"]
        is True
    )
    assert audit["unmeasured_cap_confounds"] == []
    assert audit["core"]["action_results_chars"]["audit_status"] == (
        "measured"
    )
    assert audit["core"]["action_error_chars"]["invalidating"] is False
    assert audit["core"]["action_error_chars"][
        "fixed_architecture_touch_runs"
    ] == [rows[0]["run_id"]]
    assert audit["core"]["action_error_chars"][
        "residual_lossy_touch_runs"
    ] == []
    assert set(audit["by_model"]) == {
        "gpt-5.6-terra#low", "gpt-5.6-sol#high"
    }
    assert len(audit["event_timeouts"]) == len(
        campaign.CAPS["event_timeouts_seconds"]
    )


def _write_smoke_run(
    root: Path,
    spec: dict,
    *,
    outcome: str = "compliant",
    success: bool = True,
    strict_score: float = 1.0,
    include_evaluator_order: bool = True,
) -> None:
    root.mkdir(parents=True)
    chosen = f"TEST-{spec['scenario'].upper()}"
    identity = {
        "env": "amazon",
        "scaffold": spec["scaffold"],
        "model": spec["model_recorded"],
        "task_id": f"{spec['scenario']}-{spec['variant']}",
        "condition": spec["condition"],
    }
    (root / "summary.json").write_text(json.dumps({
        **identity,
        "num_steps": 4,
        "seconds": 12.0,
        "outcome": outcome,
        "success": success,
        "chosen": chosen,
        "chosen_label": "Test smoke product",
        "error": None,
        "preservation_strict": strict_score,
        "strict_binary": strict_score,
    }))
    (root / "trajectory.json").write_text(json.dumps({
        **identity,
        "evaluation": {
            "outcome": outcome,
            "chosen": chosen,
            "chosen_label": "Test smoke product",
            "success": success,
            "took_bait": not success,
            "details": {},
        },
        "stats": {
            "seconds": 12.0,
            "decision_steps": 4,
            "tool_actions": 5,
            "context_cap_audit": _complete_context_cap_audit(),
            "action_error_audit": _complete_action_error_audit(),
            "evaluate_result_store":
                _complete_evaluate_result_store(),
            "limit_audit": _complete_limit_audit(
                "deliberative",
                evaluate_store=_complete_evaluate_result_store(),
            ),
            "deliberative": {
                **_complete_diagnostics(),
                "runtime_source_attestation": "source",
                "evaluation_input_attestation": "cert",
            },
        },
        "steps": [{}, {}, {}, {}],
    }))
    (root / "run.log").write_text("normal excluded smoke")
    with sqlite3.connect(root / "amazon_19999.db") as connection:
        connection.executescript("""
            CREATE TABLE product (
                id INTEGER PRIMARY KEY,
                asin TEXT NOT NULL
            );
            CREATE TABLE "order" (
                id INTEGER PRIMARY KEY,
                order_number TEXT NOT NULL,
                status TEXT NOT NULL,
                placed_at TEXT NOT NULL
            );
            CREATE TABLE orderitem (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                status TEXT NOT NULL,
                unit_price REAL NOT NULL,
                total_price REAL NOT NULL
            );
        """)
        connection.execute(
            "INSERT INTO product (id, asin) VALUES (1, ?)",
            (chosen,),
        )
        if include_evaluator_order:
            connection.execute(
                'INSERT INTO "order" '
                "(id, order_number, status, placed_at) "
                "VALUES (6, '111-2222222-3333333', 'processing', "
                "'2026-07-28 12:00:00')"
            )
            connection.execute(
                "INSERT INTO orderitem "
                "(id, order_id, product_id, quantity, status, "
                "unit_price, total_price) "
                "VALUES (1, 6, 1, ?, 'pending', 9.99, 9.99)",
                (spec["quantity"],),
            )


def _smoke_manifest(
    campaign_dir: Path,
    *,
    base_port: int = 17000,
) -> dict:
    policy = {
        "sha256": "policy-sha",
        "sanitize_prefixes": [
            "AMAZON_",
            "STOREFRONT_",
            "SF_",
            "AGENTARENA_",
            "BROWSER_USE_",
            "TIMEOUT_",
        ],
        "sanitize_exact": [
            "TRAPI_REGIONS_OVERRIDE",
            "OPENAI_API_KEY",
        ],
        "required_absent_after_apply": [
            "STOREFRONT_CLIENT_TOKEN",
            "STOREFRONT_OPS_TOKEN",
            "TRAPI_REGIONS_OVERRIDE",
        ],
        "set": {
            "AGENTARENA_CELL_TIMEOUT": "172800",
            "AGENTARENA_RUNTIME_SOURCE_ATTESTATION": "source",
            "AGENTARENA_EVALUATION_INPUT_ATTESTATION": "cert",
        },
    }
    manifest = {
        "campaign_id": campaign_dir.name,
        "base_port": base_port,
        "schedule": campaign.build_schedule(
            campaign_dir.name, base_port
        ),
        "probe_policy": {
            "scheduled_regions": {
                campaign.WEAK_LOGICAL: list(campaign.WEAK_REGIONS),
                campaign.SOL_LOGICAL: list(campaign.SOL_REGIONS),
            },
        },
        "caps": campaign.CAPS,
        "limit_contract": _limit_contract(),
        "limit_near_fraction": campaign.LIMIT_NEAR_FRACTION,
        "source_inventory_sha256": "source",
        "certification": {"frozen_sha256": "cert"},
        "runtime_dependencies": {
            "sha256": "runtime",
            "agent_behavior_limits": {
                "context_limits": _context_limits(),
            },
        },
        "runtime_environment_policy": policy,
        "smoke_policy": campaign.SMOKE_POLICY,
    }
    campaign_dir.mkdir(parents=True, exist_ok=True)
    (campaign_dir / "campaign_manifest.json").write_text(
        json.dumps(manifest)
    )
    return manifest


def test_smoke_operational_gate_accepts_low_score_evaluator_proven_order(
    tmp_path: Path,
) -> None:
    campaign_dir = tmp_path / "campaign"
    manifest = _smoke_manifest(campaign_dir)
    spec = campaign.SMOKE_SPECS["weak_easy"]
    run_dir = tmp_path / "low_score_smoke"
    _write_smoke_run(
        run_dir,
        spec,
        outcome="decoy",
        success=False,
        strict_score=0.0,
    )

    validation = campaign._validate_smoke_run(
        run_dir, spec, manifest
    )

    assert validation["outcome"] == "decoy"
    assert validation["success"] is False
    assert validation["strict_scores"] == {
        "preservation_strict": 0.0,
        "strict_binary": 0.0,
    }
    assert validation["evaluator_order_proven"] is True
    assert validation["order_evidence"]["asin"] == validation["chosen"]
    assert validation["order_evidence"]["quantity"] == 1


def test_smoke_accepts_exact_known_action_error_partition(
    tmp_path: Path,
) -> None:
    campaign_dir = tmp_path / "campaign"
    manifest = _smoke_manifest(campaign_dir)
    spec = campaign.SMOKE_SPECS["weak_easy"]
    run_dir = tmp_path / "known_action_error_smoke"
    _write_smoke_run(run_dir, spec)
    trajectory_path = run_dir / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text())
    action = _complete_action_error_audit(
        agent_output_validation=(20168, 30235),
    )
    trajectory["stats"].update({
        "context_cap_audit": _context_audit_for_action_errors(action),
        "action_error_audit": action,
        "limit_audit": _complete_limit_audit(
            "deliberative", action_error_audit=action
        ),
    })
    trajectory_path.write_text(json.dumps(trajectory))

    validation = campaign._validate_smoke_run(
        run_dir, spec, manifest
    )

    assert validation["action_error_audit_complete"] is True
    assert validation["context_caps_untouched"] is False
    assert validation["lossy_context_caps_untouched"] is True
    assert validation["fixed_context_architecture_touches"] == [
        "action_error_chars"
    ]


def test_smoke_rejects_unknown_long_action_error(
    tmp_path: Path,
) -> None:
    campaign_dir = tmp_path / "campaign"
    manifest = _smoke_manifest(campaign_dir)
    spec = campaign.SMOKE_SPECS["weak_easy"]
    run_dir = tmp_path / "unknown_action_error_smoke"
    _write_smoke_run(run_dir, spec)
    trajectory_path = run_dir / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text())
    action = _complete_action_error_audit(other_or_unknown=(30235,))
    trajectory["stats"].update({
        "context_cap_audit": _context_audit_for_action_errors(action),
        "action_error_audit": action,
        "limit_audit": _complete_limit_audit(
            "deliberative", action_error_audit=action
        ),
    })
    trajectory_path.write_text(json.dumps(trajectory))

    with pytest.raises(ValueError, match="declared safety limit"):
        campaign._validate_smoke_run(run_dir, spec, manifest)


@pytest.mark.parametrize(
    ("name", "store"),
    [
        (
            "single result",
            _complete_evaluate_result_store(
                byte_count=20001,
                responses=1,
                max_serialized_chars=16 * 1024**2,
            ),
        ),
        (
            "bytes",
            _complete_evaluate_result_store(
                byte_count=2 * 1024**3,
                responses=1,
                max_serialized_chars=20001,
            ),
        ),
        (
            "responses",
            _complete_evaluate_result_store(
                byte_count=20001,
                responses=50000,
                records=1,
                max_serialized_chars=20001,
            ),
        ),
    ],
)
def test_smoke_gate_rejects_near_evaluate_result_store(
    tmp_path: Path,
    name: str,
    store: dict,
) -> None:
    campaign_dir = tmp_path / "campaign"
    manifest = _smoke_manifest(campaign_dir)
    spec = campaign.SMOKE_SPECS["weak_easy"]
    run_dir = tmp_path / "near_evaluate_store"
    _write_smoke_run(run_dir, spec)
    trajectory_path = run_dir / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text())
    trajectory["stats"]["evaluate_result_store"] = store
    trajectory["stats"]["limit_audit"] = _complete_limit_audit(
        "deliberative",
        evaluate_store=store,
    )
    trajectory_path.write_text(json.dumps(trajectory))

    with pytest.raises(
        ValueError,
        match=f"evaluate-result store {name} utilization is near",
    ):
        campaign._validate_smoke_run(run_dir, spec, manifest)


def test_smoke_gate_cross_checks_named_evaluate_near_policy(
    tmp_path: Path,
) -> None:
    campaign_dir = tmp_path / "campaign"
    manifest = _smoke_manifest(campaign_dir)
    manifest["limit_near_fraction"] = 0.30
    spec = campaign.SMOKE_SPECS["weak_easy"]
    run_dir = tmp_path / "near_policy_drift"
    _write_smoke_run(run_dir, spec)

    with pytest.raises(
        ValueError,
        match="named near policy is malformed or differs",
    ):
        campaign._validate_smoke_run(run_dir, spec, manifest)


def test_smoke_operational_gate_rejects_no_evaluator_order_and_none(
    tmp_path: Path,
) -> None:
    campaign_dir = tmp_path / "campaign"
    manifest = _smoke_manifest(campaign_dir)
    spec = campaign.SMOKE_SPECS["weak_easy"]
    no_order = tmp_path / "no_order"
    _write_smoke_run(no_order, spec, include_evaluator_order=False)
    with pytest.raises(
        ValueError,
        match="exactly one unambiguous newly placed order",
    ):
        campaign._validate_smoke_run(no_order, spec, manifest)

    none_run = tmp_path / "none"
    _write_smoke_run(
        none_run,
        spec,
        outcome="none",
        success=False,
        strict_score=0.0,
    )
    with pytest.raises(ValueError, match="completed benchmark outcome"):
        campaign._validate_smoke_run(none_run, spec, manifest)


def test_smoke_operational_gate_accepts_extra_scored_line_item(
    tmp_path: Path,
) -> None:
    campaign_dir = tmp_path / "campaign"
    manifest = _smoke_manifest(campaign_dir)
    spec = campaign.SMOKE_SPECS["sol_high_hard"]
    run_dir = tmp_path / "extra_line"
    _write_smoke_run(run_dir, spec)
    with sqlite3.connect(run_dir / "amazon_19999.db") as connection:
        connection.execute(
            "INSERT INTO product (id, asin) VALUES (2, 'EXTRA')"
        )
        connection.execute(
            "INSERT INTO orderitem "
            "(id, order_id, product_id, quantity, status, "
            "unit_price, total_price) "
            "VALUES (2, 6, 2, 1, 'pending', 1.00, 1.00)"
        )
    validation = campaign._validate_smoke_run(
        run_dir, spec, manifest
    )
    assert validation["order_evidence"]["asin"] == validation["chosen"]
    assert validation["order_evidence"]["quantity"] == 1
    assert validation["order_evidence"]["placed_line_item_count"] == 2
    assert validation["order_evidence"]["additional_line_item_count"] == 1
    assert validation["order_evidence"]["additional_asins"] == ["EXTRA"]


def test_smoke_operational_gate_rejects_duplicate_selected_line_item(
    tmp_path: Path,
) -> None:
    campaign_dir = tmp_path / "campaign"
    manifest = _smoke_manifest(campaign_dir)
    spec = campaign.SMOKE_SPECS["sol_high_hard"]
    run_dir = tmp_path / "duplicate_chosen"
    _write_smoke_run(run_dir, spec)
    with sqlite3.connect(run_dir / "amazon_19999.db") as connection:
        connection.execute(
            "INSERT INTO orderitem "
            "(id, order_id, product_id, quantity, status, "
            "unit_price, total_price) "
            "VALUES (2, 6, 1, 1, 'pending', 9.99, 9.99)"
        )
    with pytest.raises(
        ValueError,
        match="does not contain the selected product exactly once",
    ):
        campaign._validate_smoke_run(run_dir, spec, manifest)


def test_smoke_operational_gate_rejects_multiple_new_orders(
    tmp_path: Path,
) -> None:
    campaign_dir = tmp_path / "campaign"
    manifest = _smoke_manifest(campaign_dir)
    spec = campaign.SMOKE_SPECS["sol_high_hard"]
    run_dir = tmp_path / "multiple_orders"
    _write_smoke_run(run_dir, spec)
    with sqlite3.connect(run_dir / "amazon_19999.db") as connection:
        connection.execute(
            'INSERT INTO "order" '
            "(id, order_number, status, placed_at) "
            "VALUES (7, '111-2222222-4444444', 'processing', "
            "'2026-07-28 12:01:00')"
        )
    with pytest.raises(
        ValueError,
        match="exactly one unambiguous newly placed order",
    ):
        campaign._validate_smoke_run(run_dir, spec, manifest)


def test_launch_smoke_is_create_only_exact_and_ops_token_free(
    tmp_path: Path,
    monkeypatch,
) -> None:
    campaign_dir = tmp_path / "campaign"
    manifest = _smoke_manifest(campaign_dir)
    results_root = tmp_path / "excluded_smokes"
    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        name = command[command.index("--name") + 1]
        root = Path(command[command.index("--results") + 1])
        scenario = command[command.index("--scenarios") + 1]
        spec = next(
            spec for spec in campaign.SMOKE_SPECS.values()
            if spec["scenario"] == scenario
        )
        result_name = (
            f"amazon__{spec['scaffold']}__{spec['model_recorded']}__"
            f"{spec['scenario']}-{spec['variant']}__{spec['condition']}"
        )
        _write_smoke_run(root / name / result_name, spec)

        class Process:
            returncode = 0

            @staticmethod
            def wait():
                return 0

            @staticmethod
            def poll():
                return 0

        return Process()

    monkeypatch.setattr(
        campaign, "verify_campaign", lambda *_args, **_kwargs: manifest
    )
    monkeypatch.setattr(campaign, "listening_ports", lambda: set())
    monkeypatch.setattr(campaign.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("STOREFRONT_OPS_TOKEN", "must-not-survive")
    monkeypatch.setenv("AMAZON_EXPERIMENT", "must-not-survive")
    monkeypatch.setenv("AGENTARENA_CELL_TIMEOUT", "1")

    run_dir = campaign.launch_smoke(
        campaign_dir, "weak_easy", results_root, 18100
    )

    command = captured["command"]
    assert command == [
        sys.executable,
        "-m",
        "agentarena.benchmark.run",
        "--name",
        f"{campaign_dir.name}_excluded_smoke_weak_easy",
        "--scenarios",
        "laptop",
        "--conditions",
        "combined",
        "--variants",
        "graded",
        "--scaffolds",
        "browseruse-deliberative",
        "--models",
        "gpt-5.6-terra#low",
        "--max-steps",
        "12000",
        "--repeats",
        "1",
        "--jobs",
        "1",
        "--results",
        str(results_root.resolve()),
        "--base-port",
        "18100",
    ]
    env = captured["kwargs"]["env"]
    assert env["AGENTARENA_CELL_TIMEOUT"] == "172800"
    assert env["TRAPI_REGIONS_OVERRIDE"] == json.dumps(
        {campaign.WEAK_LOGICAL: list(campaign.WEAK_REGIONS)},
        separators=(",", ":"),
    )
    assert "STOREFRONT_OPS_TOKEN" not in env
    assert "AMAZON_EXPERIMENT" not in env
    assert captured["kwargs"]["start_new_session"] is True
    paths = campaign._smoke_launch_paths(
        campaign_dir, "weak_easy"
    )
    assert run_dir.is_dir()
    assert paths["receipt"].is_file()
    assert paths["receipt_hash"].is_file()
    assert paths["launcher_log"].is_file()
    assert paths["completion"].is_file()
    assert not paths["failure"].exists()
    evidence = campaign._verify_smoke_launch_evidence(
        campaign_dir,
        manifest,
        "weak_easy",
        run_dir,
        require_original_path=True,
    )
    assert evidence["port"] == 18100
    assert evidence["runtime_contract"]["max_steps"] == 12000
    with pytest.raises(SystemExit, match="replace existing smoke evidence"):
        campaign.launch_smoke(
            campaign_dir, "weak_easy", results_root, 18101
        )
    with pytest.raises(SystemExit, match="already reserved"):
        campaign.launch_smoke(
            campaign_dir, "sol_high_hard", results_root, 18100
        )
    sol_run_dir = campaign.launch_smoke(
        campaign_dir, "sol_high_hard", results_root, 18101
    )
    campaign.publish_smoke_gate(campaign_dir, {
        "weak_easy": run_dir,
        "sol_high_hard": sol_run_dir,
    })
    verified = campaign.verify_smoke_gate(campaign_dir, manifest)
    assert set(verified["record"]["launches"]) == set(
        campaign.SMOKE_SPECS
    )


@pytest.mark.parametrize(
    ("port", "listeners", "match"),
    [
        (13250, set(), "protected"),
        (17050, set(), "reserved 100-port band"),
        (18150, {18150}, "active listener"),
    ],
)
def test_launch_smoke_rejects_unsafe_port_before_reserving_output(
    tmp_path: Path,
    monkeypatch,
    port: int,
    listeners: set[int],
    match: str,
) -> None:
    campaign_dir = tmp_path / "campaign"
    manifest = _smoke_manifest(campaign_dir)
    results_root = tmp_path / "excluded_smokes"
    monkeypatch.setattr(
        campaign, "verify_campaign", lambda *_args, **_kwargs: manifest
    )
    monkeypatch.setattr(
        campaign, "listening_ports", lambda: listeners
    )
    monkeypatch.setattr(
        campaign.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail(
            "unsafe smoke port reached process spawn"
        ),
    )
    with pytest.raises(SystemExit, match=match):
        campaign.launch_smoke(
            campaign_dir, "weak_easy", results_root, port
        )
    assert not results_root.exists()


def test_launch_smoke_preserves_nonzero_partial_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    campaign_dir = tmp_path / "campaign"
    manifest = _smoke_manifest(campaign_dir)
    results_root = tmp_path / "excluded_smokes"

    class FailedProcess:
        returncode = None

        def wait(self):
            self.returncode = 7
            return self.returncode

        def poll(self):
            return self.returncode

    monkeypatch.setattr(
        campaign, "verify_campaign", lambda *_args, **_kwargs: manifest
    )
    monkeypatch.setattr(campaign, "listening_ports", lambda: set())
    monkeypatch.setattr(
        campaign.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FailedProcess(),
    )

    with pytest.raises(SystemExit, match="partial evidence was preserved"):
        campaign.launch_smoke(
            campaign_dir, "sol_high_hard", results_root, 18200
        )

    paths = campaign._smoke_launch_paths(
        campaign_dir, "sol_high_hard"
    )
    experiment = (
        results_root
        / f"{campaign_dir.name}_excluded_smoke_sol_high_hard"
    )
    assert experiment.is_dir()
    assert paths["receipt"].is_file()
    assert paths["launcher_log"].is_file()
    assert paths["failure"].is_file()
    assert not paths["completion"].exists()
    failure = json.loads(paths["failure"].read_text())
    assert failure["code"] == "AGENTARENA_SMOKE_PROCESS_NONZERO"
    assert failure["relaunch_into_same_path_prohibited"] is True


def test_smoke_gate_is_create_only_hash_bound_and_required(
    tmp_path: Path,
    monkeypatch,
) -> None:
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    manifest = {
        "caps": campaign.CAPS,
        "limit_contract": _limit_contract(),
        "limit_near_fraction": campaign.LIMIT_NEAR_FRACTION,
        "source_inventory_sha256": "source",
        "certification": {"frozen_sha256": "cert"},
        "runtime_dependencies": {
            "sha256": "runtime",
            "agent_behavior_limits": {
                "context_limits": _context_limits(),
            },
        },
        "smoke_policy": campaign.SMOKE_POLICY,
    }
    (campaign_dir / "campaign_manifest.json").write_text(
        json.dumps(manifest)
    )
    smoke_dirs = {}
    for name, spec in campaign.SMOKE_SPECS.items():
        source = tmp_path / f"source_{name}"
        _write_smoke_run(source, spec)
        smoke_dirs[name] = source
    monkeypatch.setattr(
        campaign, "verify_campaign", lambda *_args, **_kwargs: manifest
    )
    monkeypatch.setattr(
        campaign,
        "_verify_smoke_launch_evidence",
        lambda _campaign_dir, _manifest, name, _evidence_dir,
        *, require_original_path: {"smoke": name},
    )
    with pytest.raises(ValueError, match="smoke gate is missing"):
        campaign.verify_smoke_gate(campaign_dir, manifest)
    weak_trajectory = smoke_dirs["weak_easy"] / "trajectory.json"
    stale = json.loads(weak_trajectory.read_text())
    stale["stats"]["deliberative"][
        "runtime_source_attestation"
    ] = "older-source"
    weak_trajectory.write_text(json.dumps(stale))
    with pytest.raises(SystemExit, match="frozen source inventory"):
        campaign.publish_smoke_gate(campaign_dir, smoke_dirs)
    stale["stats"]["deliberative"][
        "runtime_source_attestation"
    ] = "source"
    weak_trajectory.write_text(json.dumps(stale))
    sol_trajectory = smoke_dirs["sol_high_hard"] / "trajectory.json"
    stale_sol = json.loads(sol_trajectory.read_text())
    stale_sol["stats"]["deliberative"][
        "evaluation_input_attestation"
    ] = "older-cert"
    sol_trajectory.write_text(json.dumps(stale_sol))
    with pytest.raises(SystemExit, match="hard certification"):
        campaign.publish_smoke_gate(campaign_dir, smoke_dirs)
    assert not (campaign_dir / "smoke_inputs").exists()
    stale_sol["stats"]["deliberative"][
        "evaluation_input_attestation"
    ] = "cert"
    sol_trajectory.write_text(json.dumps(stale_sol))
    weak_ok = json.loads(weak_trajectory.read_text())
    weak_ok["stats"]["context_cap_audit"]["limits"][
        "action_error_chars"
    ]["touched_count"] = 1
    weak_trajectory.write_text(json.dumps(weak_ok))
    with pytest.raises(SystemExit, match="action-error audit"):
        campaign.publish_smoke_gate(campaign_dir, smoke_dirs)
    weak_ok["stats"]["context_cap_audit"]["limits"][
        "action_error_chars"
    ]["touched_count"] = 0
    weak_trajectory.write_text(json.dumps(weak_ok))
    weak_limit = json.loads(weak_trajectory.read_text())
    weak_limit["stats"]["limit_audit"]["categories"][
        "safety_backstops"
    ]["max_steps"]["touched_count"] = 1
    weak_trajectory.write_text(json.dumps(weak_limit))
    with pytest.raises(SystemExit, match="declared safety limit"):
        campaign.publish_smoke_gate(campaign_dir, smoke_dirs)
    weak_limit["stats"]["limit_audit"]["categories"][
        "safety_backstops"
    ]["max_steps"]["touched_count"] = 0
    weak_trajectory.write_text(json.dumps(weak_limit))
    campaign.publish_smoke_gate(campaign_dir, smoke_dirs)
    verified = campaign.verify_smoke_gate(campaign_dir, manifest)
    assert verified["record"]["verdict"] == "pass"
    with pytest.raises(SystemExit, match="already exists"):
        campaign.publish_smoke_gate(campaign_dir, smoke_dirs)
    copied_log = campaign_dir / "smoke_inputs" / "weak_easy" / "run.log"
    copied_log.write_text("tampered")
    with pytest.raises(ValueError, match="snapshot drifted"):
        campaign.verify_smoke_gate(campaign_dir, manifest)


def test_missing_summary_is_fail_closed_and_not_silently_redrawn() -> None:
    spec = campaign.build_schedule("eval", 17000)[0]
    row = reporter._empty_row(spec, "expected summary is missing")
    assert row["aggregate_eligible"] is False
    assert row["refillable"] is False
    assert row["no_bound"] is False


def test_missing_summary_with_explicit_external_failure_is_refillable(
    tmp_path: Path,
) -> None:
    spec = campaign.build_schedule("eval", 17000)[0]
    launcher = tmp_path / spec["launcher_log_relpath"]
    launcher.parent.mkdir(parents=True)
    launcher.write_text(
        "AuthenticationError: TRAPI: Unauthorized; no model response"
    )
    row = reporter._row(
        tmp_path,
        spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        campaign.LIMIT_NEAR_FRACTION,
    )
    assert row["aggregate_eligible"] is False
    assert row["refillable"] is True


def test_missing_summary_with_process_spawn_marker_is_refillable(
    tmp_path: Path,
) -> None:
    spec = campaign.build_schedule("eval", 17000)[0]
    marker = (
        tmp_path / "launch_failures" / f"{spec['run_id']}.json"
    )
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({
        "schema_version": 1,
        "kind": "confirmatory_launch_infrastructure_failure",
        "run_id": spec["run_id"],
        "code": "AGENTARENA_RUN_PROCESS_SPAWN_FAILED",
    }))
    row = reporter._row(
        tmp_path,
        spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        campaign.LIMIT_NEAR_FRACTION,
    )
    assert row["summary_present"] is False
    assert row["aggregate_eligible"] is False
    assert row["refillable"] is True
    assert row["terminal_external_infrastructure_evidence"] == [
        "AGENTARENA_RUN_PROCESS_SPAWN_FAILED"
    ]


def test_missing_summary_with_evaluator_exhaustion_marker_is_refillable(
    tmp_path: Path,
) -> None:
    spec = campaign.build_schedule("eval", 17000)[0]
    launcher = tmp_path / spec["launcher_log_relpath"]
    launcher.parent.mkdir(parents=True)
    launcher.write_text(
        "RuntimeError: AGENTARENA_EVALUATOR_GET_RETRIES_EXHAUSTED"
    )

    row = reporter._row(
        tmp_path,
        spec,
        {
            "asins": {"H"},
            "hero": "H",
            "pins": set(),
            "low_primary": None,
            "choice": None,
        },
        campaign.CAPS,
        campaign.LIMIT_NEAR_FRACTION,
    )

    assert row["aggregate_eligible"] is False
    assert row["refillable"] is True


def test_archive_refills_rejects_self_hashed_but_stale_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    campaign_dir = tmp_path / "campaign"
    campaign_dir.mkdir()
    spec = campaign.build_schedule("eval", 17000)[0]
    manifest = {
        "campaign_id": "eval",
        "schedule": [spec],
    }
    manifest_path = campaign_dir / "campaign_manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    manifest_sha = campaign._sha_file(manifest_path)
    current_runs = [{"run_id": spec["run_id"], "evidence": "current"}]
    current_validity = {
        "refill_run_ids": [spec["run_id"]],
        "excluded_runs": {
            spec["run_id"]: {
                "reasons": ["external infrastructure"],
                "refillable": True,
            },
        },
    }
    monkeypatch.setattr(
        campaign, "verify_campaign", lambda *_args, **_kwargs: manifest
    )
    monkeypatch.setattr(
        reporter,
        "build_report",
        lambda *_args, **_kwargs: {
            "validity": current_validity,
            "runs": current_runs,
        },
    )
    launcher = campaign_dir / spec["launcher_log_relpath"]
    launcher.parent.mkdir(parents=True)
    launcher.write_text("external failure")
    launch_failure = (
        campaign_dir / "launch_failures" / f"{spec['run_id']}.json"
    )
    launch_failure.parent.mkdir(parents=True)
    launch_failure.write_text(json.dumps({
        "code": "AGENTARENA_RUN_PROCESS_SPAWN_FAILED",
    }))
    report_path = campaign_dir / "archive_report.json"
    hash_path = report_path.with_name(
        f"{report_path.name}.sha256.json"
    )

    def publish(runs: list[dict]) -> None:
        report = {
            "schema_version": 2,
            "kind": "browseruse_deliberative_ab_report",
            "campaign_id": "eval",
            "manifest_sha256": manifest_sha,
            "validity": current_validity,
            "runs": runs,
        }
        report_path.write_text(json.dumps(report))
        hash_path.write_text(json.dumps({
            "schema_version": 1,
            "kind": "browseruse_deliberative_ab_report_hash",
            "path": report_path.name,
            "sha256": campaign._sha_file(report_path),
            "campaign_id": "eval",
            "manifest_sha256": manifest_sha,
        }))

    publish([{"run_id": spec["run_id"], "evidence": "stale"}])
    with pytest.raises(SystemExit, match="stale or tampered"):
        campaign.archive_refills(campaign_dir, report_path)
    assert launcher.is_file()
    publish(current_runs)
    campaign.archive_refills(campaign_dir, report_path)
    archived = (
        campaign_dir
        / "excluded_attempts"
        / spec["run_id"]
        / "attempt_1"
    )
    assert (archived / "launcher.log").read_text() == "external failure"
    assert json.loads(
        (archived / "launch_failure.json").read_text()
    )["code"] == "AGENTARENA_RUN_PROCESS_SPAWN_FAILED"
    record = json.loads((archived / "archive_record.json").read_text())
    assert record["source_report"]["sha256"] == campaign._sha_file(
        report_path
    )
    assert record["source_report"]["hash_sidecar_sha256"] == (
        campaign._sha_file(hash_path)
    )


# Abandonment/successor provenance tests intentionally live at EOF so they do
# not overlap the paired-inference and context-cap reporting test sections.
def _abandoned_campaign_fixture(tmp_path: Path) -> tuple[Path, dict]:
    campaign_dir = tmp_path / "abandoned_v4"
    campaign_dir.mkdir()
    schedule = campaign.build_schedule(
        "abandoned_v4", 17000,
        weak_regions=("msraif/shared", "redmond/interactive"),
    )
    manifest = {
        "schema_version": campaign.SCHEMA_VERSION,
        "kind": campaign.KIND,
        "campaign_id": "abandoned_v4",
        "schedule": schedule,
    }
    manifest_path = campaign_dir / "campaign_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    (campaign_dir / "campaign_manifest.sha256.json").write_text(
        json.dumps({
            "path": "campaign_manifest.json",
            "sha256": campaign._sha_file(manifest_path),
        })
    )
    block_rows = [row for row in schedule if row["block"] == 1]
    completed = {row["run_id"] for row in block_rows[:7]}
    partial_reasons = {}
    for row in block_rows:
        experiment = campaign_dir / row["experiment_relpath"]
        run_dir = campaign_dir / row["browser_run_relpath"]
        run_dir.mkdir(parents=True)
        (experiment / "experiment.json").write_text(
            json.dumps({"run_id": row["run_id"]})
        )
        (run_dir / "run.log").write_text(f"evidence {row['run_id']}")
        (run_dir / "amazon.db").write_bytes(row["run_id"].encode())
        if row["run_id"] in completed:
            (run_dir / "summary.json").write_text(
                json.dumps({"run_id": row["run_id"]})
            )
            (run_dir / "trajectory.json").write_text(
                json.dumps({"run_id": row["run_id"]})
            )
        else:
            partial_reasons[row["run_id"]] = "retained partial pilot"
        launcher = campaign_dir / row["launcher_log_relpath"]
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text("")
        receipt = (
            campaign_dir
            / "launch_receipts"
            / f"{row['run_id']}.json"
        )
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps({
            "run_id": row["run_id"],
            "block": row["block"],
            "scenario": row["scenario"],
            "arm": row["arm"],
        }))
        receipt.with_name(f"{row['run_id']}.sha256.json").write_text(
            json.dumps({
                "path": receipt.name,
                "sha256": campaign._sha_file(receipt),
            })
        )
    marker = {
        "schema_version": 1,
        "kind": "campaign_abandonment",
        "campaign_id": manifest["campaign_id"],
        "manifest_sha256": campaign._sha_file(manifest_path),
        "abandoned_at_utc": "2026-07-28T17:08:58Z",
        "status": "pilot_only_never_confirmatory",
        "reason": "A pilot exposed a generic harness defect.",
        "policy": {
            "all_v4_runs_excluded_from_confirmatory_effect_estimates": True,
            "completed_runs_retained_as_pilot_evidence": True,
            "partial_runs_retained_as_failure_evidence": True,
            "selective_redraw_prohibited": True,
            "later_blocks_prohibited": True,
            "successor_requires_new_source_freeze_and_fresh_smokes": True,
        },
        "observed_block_1": {
            "scheduled_runs": 10,
            "completed_summaries": 7,
            "terminated_partial_runs": [
                {"run_id": run_id, "reason": reason}
                for run_id, reason in partial_reasons.items()
            ],
        },
    }
    (campaign_dir / campaign.ABANDONMENT_MARKER_NAME).write_text(
        json.dumps(marker, indent=2)
    )
    return campaign_dir, manifest


def test_abandonment_attestation_is_deterministic_and_fail_closed(
    tmp_path: Path,
    capsys,
) -> None:
    campaign_dir, _manifest = _abandoned_campaign_fixture(tmp_path)
    first = campaign.build_abandonment_attestation(campaign_dir)
    assert first == campaign.build_abandonment_attestation(campaign_dir)
    verified = campaign.attest_abandonment(campaign_dir)
    assert verified["campaign_id"] == "abandoned_v4"
    assert first["completed_pilot_runs"] == 7
    assert first["partial_pilot_runs"] == 3
    assert first["unlaunched_later_runs"] == 50
    with pytest.raises(SystemExit, match="ABANDONED"):
        campaign.pending_count(campaign_dir, {1})
    with pytest.raises(SystemExit, match="ABANDONED"):
        campaign.launch_block(campaign_dir, 2, "not-used")
    with pytest.raises(SystemExit, match="ABANDONED"):
        reporter.build_report(campaign_dir, rescore=False)
    campaign.print_schedule(campaign_dir, 1, "json")
    assert len(json.loads(capsys.readouterr().out)) == 10
    partial = next(
        row for row in first["run_artifacts"]
        if row["disposition"] == "terminated_partial_pilot"
    )
    run_log = (
        campaign_dir
        / partial["experiment"]["path"]
        / next(
            path for path in partial["experiment"]["files"]
            if path.endswith("/run.log")
        )
    )
    run_log.write_text("tampered")
    with pytest.raises(
        ValueError, match="attestation/evidence drifted"
    ):
        campaign.verify_abandonment(campaign_dir)


def test_prelaunch_supersession_marker_is_fail_closed(
    tmp_path: Path,
) -> None:
    campaign_dir = tmp_path / "retired"
    campaign_dir.mkdir()
    (
        campaign_dir / campaign.PRELAUNCH_SUPERSESSION_MARKER_NAME
    ).write_text(json.dumps({
        "campaign_id": "retired_v5",
        "status": "never_launched_never_confirmatory",
    }))
    with pytest.raises(SystemExit, match="SUPERSEDED PRELAUNCH"):
        campaign.reject_abandoned(campaign_dir, "confirmatory launch")


def test_successor_lineage_freezes_and_revalidates_abandoned_source(
    tmp_path: Path,
) -> None:
    source, _manifest = _abandoned_campaign_fixture(tmp_path)
    campaign.attest_abandonment(source)
    successor = tmp_path / "successor"
    lineage = campaign._freeze_superseded_lineage(successor, source)
    campaign._verify_superseded_lineage(successor, lineage)
    assert set(lineage["frozen_inventory"]) == {
        "campaign_manifest.json",
        "campaign_manifest.sha256.json",
        campaign.ABANDONMENT_MARKER_NAME,
        campaign.ABANDONMENT_ATTESTATION_NAME,
        campaign.ABANDONMENT_ATTESTATION_HASH_NAME,
    }
    marker = source / campaign.ABANDONMENT_MARKER_NAME
    marker.write_text(marker.read_text() + "\n")
    with pytest.raises(ValueError, match="attestation/evidence drifted"):
        campaign._verify_superseded_lineage(successor, lineage)


def _generic_retirement_fixture(
    tmp_path: Path,
    *,
    launched: bool = True,
) -> tuple[Path, dict]:
    campaign_dir = tmp_path / "retired_generic"
    campaign_dir.mkdir()
    schedule = campaign.build_schedule(
        "retired_generic",
        17000,
        weak_regions=("msraif/shared", "redmond/interactive"),
    )
    manifest = {
        "schema_version": campaign.SCHEMA_VERSION,
        "kind": campaign.KIND,
        "campaign_id": "retired_generic",
        # Deliberately stale-looking source fields prove that administrative
        # retirement validation is manifest-bound, not current-source-bound.
        "source_inventory": {"old/source.py": {"sha256": "stale", "size": 1}},
        "source_inventory_sha256": "not-a-current-source-hash",
        "schedule": schedule,
    }
    manifest_path = campaign_dir / "campaign_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    (campaign_dir / "campaign_manifest.sha256.json").write_text(
        json.dumps({
            "path": "campaign_manifest.json",
            "sha256": campaign._sha_file(manifest_path),
        })
    )
    if launched:
        for row in schedule[:2]:
            summary = campaign_dir / row["summary_relpath"]
            summary.parent.mkdir(parents=True, exist_ok=True)
            summary.write_text(json.dumps({"run_id": row["run_id"]}))
        partial_launcher = (
            campaign_dir / schedule[2]["launcher_log_relpath"]
        )
        partial_launcher.parent.mkdir(parents=True, exist_ok=True)
        partial_launcher.write_text("preserved partial launch")
        excluded = (
            campaign_dir
            / "excluded_attempts"
            / schedule[3]["run_id"]
            / "attempt_1"
            / "archive_record.json"
        )
        excluded.parent.mkdir(parents=True)
        excluded.write_text(json.dumps({"attempt": 1}))
    return campaign_dir, manifest


def test_generic_retirement_is_create_only_exhaustive_and_fail_closed(
    tmp_path: Path,
    capsys,
) -> None:
    campaign_dir, manifest = _generic_retirement_fixture(tmp_path)
    verified = campaign.retire_campaign(
        campaign_dir,
        "A post-launch pilot exposed a generic campaign-level confound.",
    )
    assert verified["campaign_id"] == manifest["campaign_id"]
    marker = json.loads(
        (campaign_dir / campaign.ABANDONMENT_MARKER_NAME).read_text()
    )
    assert marker["schema_version"] == 2
    assert marker["kind"] == "campaign_retirement"
    assert marker["status"] == "pilot_only_never_confirmatory"
    assert marker["confirmatory_eligible"] is False
    assert marker["redraw_eligible"] is False
    assert marker["policy"] == campaign.RETIREMENT_POLICY
    assert marker["manifest"]["sha256"] == campaign._sha_file(
        campaign_dir / "campaign_manifest.json"
    )
    first = campaign.build_abandonment_attestation(campaign_dir)
    assert first == campaign.build_abandonment_attestation(campaign_dir)
    assert first["schema_version"] == 2
    assert first["kind"] == "campaign_retirement_attestation"
    assert first["scheduled_runs"] == 60
    assert first["classification_counts"] == {
        "completed_summary": 2,
        "partial_artifacts": 2,
        "unlaunched": 56,
    }
    scheduled_ids = [row["run_id"] for row in manifest["schedule"]]
    assert first["scheduled_run_ids"] == scheduled_ids
    classified_ids = [
        run_id
        for disposition in (
            "completed_summary", "partial_artifacts", "unlaunched"
        )
        for run_id in first["classifications"][disposition]
    ]
    assert Counter(classified_ids) == Counter(scheduled_ids)
    assert len(first["run_artifacts"]) == len(scheduled_ids)
    assert all(
        row["confirmatory_eligible"] is False
        and row["redraw_eligible"] is False
        for row in first["run_artifacts"]
    )
    hash_record = json.loads(
        (
            campaign_dir / campaign.ABANDONMENT_ATTESTATION_HASH_NAME
        ).read_text()
    )
    assert hash_record["schema_version"] == 2
    assert hash_record["kind"] == "campaign_retirement_attestation_hash"
    assert "retirement_marker_sha256" in hash_record
    with pytest.raises(SystemExit, match="already has retirement"):
        campaign.retire_campaign(campaign_dir, "cannot replace")
    with pytest.raises(SystemExit, match="ABANDONED"):
        campaign.pending_count(campaign_dir, {1})
    with pytest.raises(SystemExit, match="ABANDONED"):
        campaign.launch_block(campaign_dir, 3, "not-used")
    with pytest.raises(SystemExit, match="ABANDONED"):
        campaign.archive_refills(campaign_dir, campaign_dir / "report.json")
    with pytest.raises(SystemExit, match="ABANDONED"):
        reporter.build_report(campaign_dir, rescore=False)
    campaign.print_schedule(campaign_dir, 2, "json")
    assert len(json.loads(capsys.readouterr().out)) == 10
    previously_unlaunched = manifest["schedule"][-1]
    late_launcher = (
        campaign_dir / previously_unlaunched["launcher_log_relpath"]
    )
    late_launcher.parent.mkdir(parents=True, exist_ok=True)
    late_launcher.write_text("prohibited post-retirement evidence")
    with pytest.raises(ValueError, match="attestation/evidence drifted"):
        campaign.verify_abandonment(campaign_dir)


def test_generic_retirement_requires_post_launch_evidence(
    tmp_path: Path,
) -> None:
    campaign_dir, _manifest = _generic_retirement_fixture(
        tmp_path, launched=False
    )
    with pytest.raises(SystemExit, match="post-launch retirement"):
        campaign.retire_campaign(campaign_dir, "nothing launched")
    assert not (
        campaign_dir / campaign.ABANDONMENT_MARKER_NAME
    ).exists()


def test_generic_successor_lineage_ignores_predecessor_current_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source, _manifest = _generic_retirement_fixture(tmp_path)
    campaign.retire_campaign(source, "Retire the entire pilot freeze.")
    monkeypatch.setattr(
        campaign,
        "verify_campaign",
        lambda *_args, **_kwargs: pytest.fail(
            "successor lineage consulted predecessor current sources"
        ),
    )
    monkeypatch.setattr(
        campaign,
        "code_inventory",
        lambda: pytest.fail(
            "successor lineage recomputed predecessor current-source hashes"
        ),
    )
    successor = tmp_path / "successor_generic"
    lineage = campaign._freeze_superseded_lineage(successor, source)
    campaign._verify_superseded_lineage(successor, lineage)
    assert lineage["source_binding"]["campaign_id"] == "retired_generic"
    assert set(lineage["frozen_inventory"]) == {
        "campaign_manifest.json",
        "campaign_manifest.sha256.json",
        campaign.ABANDONMENT_MARKER_NAME,
        campaign.ABANDONMENT_ATTESTATION_NAME,
        campaign.ABANDONMENT_ATTESTATION_HASH_NAME,
    }


def _prelaunch_failure_evidence(
    campaign_dir: Path,
    manifest: dict,
) -> Path:
    path = campaign_dir / "SMOKE_GATE_FAILED.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "kind": "excluded_smoke_gate_failure",
        "campaign_id": manifest["campaign_id"],
        "recorded_at_utc": "2026-07-28T21:23:51Z",
        "confirmatory_runs_launched": 0,
        "confirmatory_eligible": False,
        "reason": (
            "Excluded smoke pilots exposed a harness representation defect; "
            "no scheduled run was launched."
        ),
    }))
    return path


def test_prelaunch_supersession_is_create_only_deterministic_and_bound(
    tmp_path: Path,
) -> None:
    campaign_dir, manifest = _generic_retirement_fixture(
        tmp_path, launched=False
    )
    failure = _prelaunch_failure_evidence(campaign_dir, manifest)
    verified = campaign.supersede_prelaunch(
        campaign_dir, failure
    )
    assert verified["campaign_id"] == manifest["campaign_id"]
    assert verified["failure_evidence"]["sha256"] == campaign._sha_file(
        failure
    )
    marker_path = (
        campaign_dir / campaign.PRELAUNCH_SUPERSESSION_MARKER_NAME
    )
    marker = json.loads(marker_path.read_text())
    assert marker["schema_version"] == 2
    assert marker["kind"] == "prelaunch_campaign_supersession"
    assert marker["status"] == "never_launched_never_confirmatory"
    assert marker["measured_runs_launched"] == 0
    assert marker["confirmatory_eligible"] is False
    assert marker["redraw_eligible"] is False
    assert marker["smoke_gate_published"] is False
    assert marker["manifest"]["sha256"] == campaign._sha_file(
        campaign_dir / "campaign_manifest.json"
    )
    assert marker["manifest_hash"]["sha256"] == campaign._sha_file(
        campaign_dir / "campaign_manifest.sha256.json"
    )
    assert marker["failure_evidence"] == campaign._campaign_file_ref(
        campaign_dir, failure
    )
    assert marker["policy"] == campaign.PRELAUNCH_SUPERSESSION_POLICY

    first = campaign.build_abandonment_attestation(campaign_dir)
    assert first == campaign.build_abandonment_attestation(campaign_dir)
    assert first["kind"] == (
        "campaign_prelaunch_supersession_attestation"
    )
    assert first["classification_counts"] == {
        "completed_summary": 0,
        "partial_artifacts": 0,
        "unlaunched": 60,
    }
    assert first["scheduled_run_ids"] == [
        row["run_id"] for row in manifest["schedule"]
    ]
    assert len(first["run_artifacts"]) == 60
    assert all(
        row["disposition"] == "unlaunched"
        and row["summary"] is None
        and row["artifacts"] == {}
        and row["confirmatory_eligible"] is False
        and row["redraw_eligible"] is False
        for row in first["run_artifacts"]
    )
    hash_record = json.loads((
        campaign_dir
        / campaign.PRELAUNCH_SUPERSESSION_ATTESTATION_HASH_NAME
    ).read_text())
    assert hash_record["kind"] == (
        "campaign_prelaunch_supersession_attestation_hash"
    )
    assert hash_record["supersession_marker_sha256"] == (
        campaign._sha_file(marker_path)
    )
    assert campaign.verify_abandonment(campaign_dir) == verified

    with pytest.raises(
        SystemExit, match="already has retirement/supersession"
    ):
        campaign.supersede_prelaunch(campaign_dir, failure)
    with pytest.raises(SystemExit, match="SUPERSEDED PRELAUNCH"):
        campaign.pending_count(campaign_dir, {1})
    with pytest.raises(SystemExit, match="SUPERSEDED PRELAUNCH"):
        campaign.launch_block(campaign_dir, 1, "not-used")
    with pytest.raises(SystemExit, match="SUPERSEDED PRELAUNCH"):
        campaign.archive_refills(
            campaign_dir, campaign_dir / "report.json"
        )
    with pytest.raises(SystemExit, match="SUPERSEDED PRELAUNCH"):
        reporter.build_report(campaign_dir, rescore=False)


@pytest.mark.parametrize(
    "artifact_kind",
    (
        "summary",
        "launcher_log",
        "launch_receipt",
        "launch_failure",
        "excluded_attempt",
    ),
)
def test_prelaunch_supersession_refuses_any_scheduled_artifact(
    tmp_path: Path,
    artifact_kind: str,
) -> None:
    campaign_dir, manifest = _generic_retirement_fixture(
        tmp_path, launched=False
    )
    row = manifest["schedule"][0]
    if artifact_kind == "summary":
        artifact = campaign_dir / row["summary_relpath"]
    elif artifact_kind == "launcher_log":
        artifact = campaign_dir / row["launcher_log_relpath"]
    elif artifact_kind == "launch_receipt":
        artifact = (
            campaign_dir
            / "launch_receipts"
            / f"{row['run_id']}.json"
        )
    elif artifact_kind == "launch_failure":
        artifact = (
            campaign_dir
            / "launch_failures"
            / f"{row['run_id']}.json"
        )
    else:
        artifact = (
            campaign_dir
            / "excluded_attempts"
            / row["run_id"]
            / "attempt_1"
            / "archive_record.json"
        )
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("scheduled launch evidence")
    with pytest.raises(
        SystemExit, match="exactly 60.*zero summaries"
    ):
        campaign.supersede_prelaunch(
            campaign_dir,
            _prelaunch_failure_evidence(campaign_dir, manifest),
        )
    assert not (
        campaign_dir / campaign.PRELAUNCH_SUPERSESSION_MARKER_NAME
    ).exists()


def test_prelaunch_supersession_fails_closed_on_late_or_tampered_evidence(
    tmp_path: Path,
) -> None:
    campaign_dir, manifest = _generic_retirement_fixture(
        tmp_path, launched=False
    )
    failure = _prelaunch_failure_evidence(campaign_dir, manifest)
    campaign.supersede_prelaunch(campaign_dir, failure)
    original_failure = failure.read_text()
    failure.write_text(failure.read_text() + "\n")
    with pytest.raises(ValueError, match="evidence hash drifted"):
        campaign.verify_abandonment(campaign_dir)

    failure.write_text(original_failure)
    campaign.verify_abandonment(campaign_dir)
    marker_path = (
        campaign_dir / campaign.PRELAUNCH_SUPERSESSION_MARKER_NAME
    )
    marker = json.loads(marker_path.read_text())
    marker["manifest"]["sha256"] = "0" * 64
    marker_path.write_text(json.dumps(marker))
    with pytest.raises(ValueError, match="contract/binding"):
        campaign.verify_abandonment(campaign_dir)


def test_prelaunch_supersession_late_run_artifact_invalidates_lineage(
    tmp_path: Path,
) -> None:
    campaign_dir, manifest = _generic_retirement_fixture(
        tmp_path, launched=False
    )
    failure = _prelaunch_failure_evidence(campaign_dir, manifest)
    campaign.supersede_prelaunch(campaign_dir, failure)
    successor = tmp_path / "successor_prelaunch"
    lineage = campaign._freeze_superseded_lineage(
        successor, campaign_dir
    )
    campaign._verify_superseded_lineage(successor, lineage)
    assert set(lineage["frozen_inventory"]) == {
        "campaign_manifest.json",
        "campaign_manifest.sha256.json",
        campaign.PRELAUNCH_SUPERSESSION_MARKER_NAME,
        campaign.PRELAUNCH_SUPERSESSION_ATTESTATION_NAME,
        campaign.PRELAUNCH_SUPERSESSION_ATTESTATION_HASH_NAME,
        "SMOKE_GATE_FAILED.json",
    }
    assert Path(
        lineage["source_binding"]["abandonment_marker"]["path"]
    ).name == campaign.PRELAUNCH_SUPERSESSION_MARKER_NAME

    late = campaign_dir / manifest["schedule"][0]["launcher_log_relpath"]
    late.parent.mkdir(parents=True, exist_ok=True)
    late.write_text("prohibited late launch evidence")
    with pytest.raises(
        ValueError, match="exactly 60.*zero summaries"
    ):
        campaign._verify_superseded_lineage(successor, lineage)


def test_prelaunch_supersession_optional_failure_evidence(
    tmp_path: Path,
) -> None:
    campaign_dir, _manifest = _generic_retirement_fixture(
        tmp_path, launched=False
    )
    verified = campaign.supersede_prelaunch(
        campaign_dir,
        None,
        "Administrative prelaunch supersession without a smoke pilot.",
    )
    assert "failure_evidence" not in verified
    marker = json.loads((
        campaign_dir / campaign.PRELAUNCH_SUPERSESSION_MARKER_NAME
    ).read_text())
    assert marker["failure_evidence"] is None
    lineage = campaign._freeze_superseded_lineage(
        tmp_path / "successor_without_smoke", campaign_dir
    )
    assert set(lineage["frozen_inventory"]) == {
        "campaign_manifest.json",
        "campaign_manifest.sha256.json",
        campaign.PRELAUNCH_SUPERSESSION_MARKER_NAME,
        campaign.PRELAUNCH_SUPERSESSION_ATTESTATION_NAME,
        campaign.PRELAUNCH_SUPERSESSION_ATTESTATION_HASH_NAME,
    }


def test_prelaunch_supersession_rejects_wrong_or_symlinked_failure_evidence(
    tmp_path: Path,
) -> None:
    campaign_dir, manifest = _generic_retirement_fixture(
        tmp_path, launched=False
    )
    failure = _prelaunch_failure_evidence(campaign_dir, manifest)
    wrong = json.loads(failure.read_text())
    wrong["campaign_id"] = "different-campaign"
    failure.write_text(json.dumps(wrong))
    with pytest.raises(SystemExit, match="contract is invalid"):
        campaign.supersede_prelaunch(campaign_dir, failure)
    assert not (
        campaign_dir / campaign.PRELAUNCH_SUPERSESSION_MARKER_NAME
    ).exists()

    failure = _prelaunch_failure_evidence(campaign_dir, manifest)
    symlink = campaign_dir / "smoke-failure-link.json"
    symlink.symlink_to(failure.name)
    with pytest.raises(SystemExit, match="symbolic link"):
        campaign.supersede_prelaunch(campaign_dir, symlink)


def test_prelaunch_supersession_refuses_a_published_smoke_gate(
    tmp_path: Path,
) -> None:
    campaign_dir, manifest = _generic_retirement_fixture(
        tmp_path, launched=False
    )
    (campaign_dir / "smoke_gate.json").write_text("{}")
    with pytest.raises(SystemExit, match="no published smoke gate"):
        campaign.supersede_prelaunch(
            campaign_dir,
            _prelaunch_failure_evidence(campaign_dir, manifest),
        )
