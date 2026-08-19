from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ablations.further_mode_ablation import analyze_results as analysis
from ablations.further_mode_ablation import campaign


def _rows() -> list[dict]:
    rows = campaign.build_schedule("synthetic", 17000)
    out = []
    for row in rows:
        item = dict(row)
        item.update(
            preservation_strict=0.2,
            hero=0,
            strict_binary=0.0,
            chosen=None,
            num_steps=20,
            seconds=100.0,
            process_endpoints={
                name: 0 for name in analysis.PROCESS_ENDPOINTS
                if name not in {"num_steps", "seconds"}
            },
        )
        if item["arm_id"] == "F_combined_original":
            item.update(preservation_strict=1.0, hero=1, strict_binary=1.0)
        if item["scenario"] == "laptop_hard":
            item.update(
                axis1_rank_fraction=0.1,
                axis2_rank_fraction=0.5,
                axis1_rank_penalized=2,
                axis2_rank_penalized=6,
            )
            if item["arm_id"] == "B_combined_reversed":
                item.update(axis1_rank_fraction=0.5, axis2_rank_fraction=0.1)
            if item["arm_id"] == "F_combined_reversed":
                item.update(axis1_rank_fraction=0.1, axis2_rank_fraction=0.5)
        out.append(item)
    return out


def test_exact_effect_families_holm_and_directional_rank() -> None:
    result = analysis.compute_effects(_rows(), include_directional_rank=True)
    assert len(result["effects"]) == 102  # 33 score contrasts*3 + 3 rank effects
    by_name = {effect["name"]: effect for effect in result["effects"]
               if effect["endpoint"].startswith("signed_first")}
    assert by_name["B_first_mentioned_directional_rank_shift"]["estimate"] == 0.4
    assert by_name["F_first_mentioned_directional_rank_shift"]["estimate"] == 0.0
    assert by_name["harness_attenuation_of_directional_rank_shift"]["estimate"] == -0.4
    assert all("holm_p_within_frozen_family_and_endpoint" in effect
               for effect in result["effects"])


def test_binary_endpoint_rejects_fractional_value() -> None:
    rows = _rows()
    rows[0]["strict_binary"] = 0.5
    try:
        analysis.compute_effects(rows)
    except analysis.AnalysisError as exc:
        assert "exactly binary" in str(exc)
    else:
        raise AssertionError("fractional strict_binary was accepted")


def test_process_descriptives_are_nonmediational_and_paired() -> None:
    result = analysis.compute_process_descriptives(_rows())
    assert "not causal mediators" in result["interpretation"]
    assert len(result["arm_aggregates"]) == 14
    assert any(item["contrast"] == "P-B" and item["endpoint"] == "num_steps"
               for item in result["paired_stage_descriptives"])


def test_exact_helpers() -> None:
    assert analysis.exact_sign_flip_p([1.0] * 8) == 2 / 256
    adjusted = analysis.holm_adjust({"a": 0.01, "b": 0.03, "c": 0.04})
    assert adjusted == {"a": 0.03, "b": 0.06, "c": 0.06}
    low, high = analysis.clopper_pearson(0, 8)
    assert low == 0.0 and 0.0 < high < 1.0


def test_report_bundle_hash_sidecar_is_fail_closed(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    reports.mkdir()
    report = reports / "report_0001.json"
    markdown = reports / "report_0001.md"
    sidecar = reports / "report_0001.sha256.json"
    report.write_text(json.dumps({"kind": "test"}) + "\n")
    markdown.write_text("# test\n")
    sidecar.write_text(json.dumps({
        "json": {"path": report.name, "sha256": analysis._sha_file(report)},
        "markdown": {
            "path": markdown.name, "sha256": analysis._sha_file(markdown),
        },
    }))
    assert analysis._validate_report_bundle(report) == {"kind": "test"}
    report.write_text(json.dumps({"kind": "tampered"}) + "\n")
    with pytest.raises(analysis.AnalysisError, match="hash sidecar"):
        analysis._validate_report_bundle(report)


def test_analyzer_must_match_frozen_source_inventory() -> None:
    source = Path(analysis.__file__).resolve()
    inventory = {
        source.name: {
            "sha256": analysis._sha_file(source), "size": source.stat().st_size,
        }
    }
    manifest = {
        "campaign_source_inventory": inventory,
        "campaign_source_inventory_sha256": hashlib.sha256(
            analysis._json_bytes(inventory)
        ).hexdigest(),
    }
    analysis._validate_analyzer_binding(manifest)
    manifest["campaign_source_inventory_sha256"] = "0" * 64
    with pytest.raises(analysis.AnalysisError, match="inventory digest"):
        analysis._validate_analyzer_binding(manifest)


def test_applicable_audit_status_contract_and_exception_count() -> None:
    rows = [
        {"run_id": f"r{index}", "limit_audit_status": "complete_and_untouched"}
        for index in range(239)
    ]
    rows.append({
        "run_id": "compiler_failure",
        "limit_audit_status": (
            "not_initialized_pre_agent_compiler_capability_failure"
        ),
        "terminal_infrastructure_classification": {
            "class": "capability",
            "code": "compiler_semantic_or_output_failure",
        },
        "behavioral_failure_zero": True,
        "preservation_strict": 0.0,
        "strict_binary": 0.0,
    })
    report = {
        "all_applicable_safety_and_lossy_limit_touches_zero": True,
        "all_applicable_evaluate_result_bounds_not_near": True,
        "pre_agent_compiler_capability_audit_exceptions": 1,
    }
    analysis._validate_audit_statuses(report, rows)
    report["pre_agent_compiler_capability_audit_exceptions"] = 0
    with pytest.raises(analysis.AnalysisError, match="statuses disagree"):
        analysis._validate_audit_statuses(report, rows)


def test_report_rows_bind_exact_schedule_and_evidence(tmp_path: Path) -> None:
    schedule = campaign.build_schedule("synthetic", 17000)
    report_rows = _rows()
    by_id = {row["run_id"]: row for row in report_rows}
    for frozen in schedule:
        observed = by_id[frozen["run_id"]]
        for digest_field, path_field in {
            "summary_sha256": "summary_relpath",
            "trajectory_sha256": "trajectory_relpath",
            "run_log_sha256": "run_log_relpath",
            "launcher_log_sha256": "launcher_log_relpath",
        }.items():
            path = tmp_path / frozen[path_field]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{frozen['run_id']}:{digest_field}\n")
            observed[digest_field] = analysis._sha_file(path)
        receipt = tmp_path / "launch_receipts" / f"{frozen['run_id']}.json"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps({
            "kind": "further_mode_ablation_launch_receipt",
            "run_id": frozen["run_id"],
            "row": frozen,
            "attempt": 1,
        }))
        observed["launch_receipt_sha256"] = analysis._sha_file(receipt)
        observed["attempt"] = 1
        terminal = (
            tmp_path / "attempt_terminals"
            / f"{frozen['run_id']}_attempt_1.json"
        )
        terminal.parent.mkdir(parents=True, exist_ok=True)
        terminal.write_text(json.dumps({
            "kind": "further_mode_ablation_attempt_terminal",
            "run_id": frozen["run_id"],
            "attempt": 1,
            "row_sha256": hashlib.sha256(
                analysis._json_bytes(frozen)
            ).hexdigest(),
            "process_ended": True,
            "launch_receipt_sha256": analysis._sha_file(receipt),
        }))
        terminal_hash = terminal.with_suffix(".sha256.json")
        terminal_hash.write_text(json.dumps({
            "path": terminal.name,
            "sha256": analysis._sha_file(terminal),
        }))
        observed["terminal_receipt_sha256"] = analysis._sha_file(terminal)
        observed["terminal_receipt_hash_sha256"] = analysis._sha_file(
            terminal_hash
        )

    report = {"runs": report_rows}
    manifest = {"schedule": schedule}
    validated = analysis._validate_report_schedule(report, manifest, tmp_path)
    assert len(validated) == 240

    report_rows[0]["condition"] = "tampered"
    with pytest.raises(analysis.AnalysisError, match="condition differs"):
        analysis._validate_report_schedule(report, manifest, tmp_path)

    report_rows[0]["condition"] = schedule[0]["condition"]
    report_rows[0]["attempt"] = 2
    with pytest.raises(analysis.AnalysisError, match="reported attempt"):
        analysis._validate_report_schedule(report, manifest, tmp_path)

    report_rows[0]["attempt"] = 1
    first_terminal = (
        tmp_path / "attempt_terminals"
        / f"{schedule[0]['run_id']}_attempt_1.json"
    )
    first_terminal.with_suffix(".sha256.json").write_text(json.dumps({
        "path": first_terminal.name,
        "sha256": "0" * 64,
    }))
    with pytest.raises(analysis.AnalysisError, match="hash sidecar is invalid"):
        analysis._validate_report_schedule(report, manifest, tmp_path)
