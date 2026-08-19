from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import sqlite3
import sys
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import analyze_results as analysis  # noqa: E402


def _preferences() -> dict:
    return {
        "thresholds": [
            {"key": "price__lt", "value": 1000},
            {"key": "rating__min", "value": 4.0},
            {"key": "gaming", "value": False},
        ],
        "graded": [
            {"attr": "weight_kg", "direction": "lower"},
            {"attr": "battery_hours", "direction": "higher"},
        ],
    }


def _product(
    asin: str,
    *,
    price: float,
    rating: float,
    weight: float,
    battery: float,
    decoy_kind: str,
) -> dict:
    return {
        "asin": asin,
        "price": price,
        "rating": rating,
        "role": "compliant" if decoy_kind in {"hero", "tier2"} else "distractor",
        "decoy_kind": decoy_kind,
        "tech": {"gaming": False, "weight_kg": weight, "battery_hours": battery},
        "variants": [],
    }


def test_catalog_metrics_uses_threshold_feasible_competition_ranks_and_itt_penalty() -> None:
    pool = [
        _product("HERO", price=900, rating=4.5, weight=1.0, battery=10, decoy_kind="hero"),
        _product("TIER2", price=800, rating=4.2, weight=2.0, battery=12, decoy_kind="tier2"),
        _product("BAD", price=1100, rating=4.8, weight=0.5, battery=20, decoy_kind="filler"),
    ]
    chosen = analysis.catalog_metrics(pool, _preferences(), "TIER2")
    assert chosen["feasible_count"] == 2
    assert chosen["axis1_rank"] == 2
    assert chosen["axis2_rank"] == 1
    assert chosen["axis1_rank_fraction"] == 0.5
    assert chosen["axis2_rank_fraction"] == 0.0
    assert chosen["hero"] == 0

    penalized = analysis.catalog_metrics(pool, _preferences(), "BAD")
    assert penalized["chosen_feasible"] == 0
    assert penalized["axis1_rank"] is None
    assert penalized["axis1_rank_penalized"] == 3
    assert penalized["axis1_rank_fraction"] == 1.0


def test_shortlist_proxy_uses_only_preselection_structured_navigation() -> None:
    trajectory = {
        "steps": [
            {
                "url": "/s?k=laptop",
                "reasoning": "PROMOTED was shortlisted",  # deliberately ignored
                "action": json.dumps([{"find_elements": {"result": "/dp/PROMOTED"}}]),
            },
            {
                "url": "/dp/PROMOTED",
                "reasoning": "",
                "action": json.dumps(
                    [{"click": {"index": 2}, "interacted_element": "attributes={'href': '/dp/PROMOTED'}"}]
                ),
            },
            {
                "url": "/gp/cart",
                "reasoning": "",
                "action": json.dumps([{"click": {"index": 4}, "interacted_element": "Add to Cart"}]),
            },
            {
                "url": "/dp/LATE",
                "reasoning": "",
                "action": json.dumps([{"navigate": "/dp/LATE"}]),
            },
        ]
    }
    promoted = analysis.shortlist_evidence(trajectory, {"PROMOTED", "LATE"})
    assert promoted["entry"] == 1
    assert {item["asin"] for item in promoted["evidence"]} == {"PROMOTED"}

    reasoning_only = analysis.shortlist_evidence(
        {"steps": [{"url": "/s", "reasoning": "PROMOTED", "action": "[]"}]},
        {"PROMOTED"},
    )
    assert reasoning_only == {"entry": 0, "evidence": []}


def test_complete_frontier_evidence_accepts_only_proved_modes() -> None:
    advertised = {
        "stats": {
            "deliberative": {
                "frontier_coverage_mode": "advertised_total",
                "frontier_inspected_count": 37,
                "frontier_advertised_count": 37,
            }
        }
    }
    finite = {
        "stats": {
            "deliberative": {
                "frontier_coverage_mode": "finite_pages",
                "frontier_inspected_count": 2112,
                "frontier_advertised_page_count": 88,
                "frontier_enumerated_page_count": 88,
            }
        }
    }
    assert analysis.complete_frontier_evidence(advertised) == 1
    assert analysis.complete_frontier_evidence(finite) == 1
    assert analysis.complete_frontier_evidence({"stats": {"deliberative": {}}}) == 0
    assert analysis.complete_frontier_evidence({}) == 0
    advertised["stats"]["deliberative"]["frontier_inspected_count"] = 38
    assert analysis.complete_frontier_evidence(advertised) == 0
    advertised["stats"]["deliberative"]["frontier_inspected_count"] = 37.0
    with pytest.raises(analysis.AnalysisError, match="not an integer"):
        analysis.complete_frontier_evidence(advertised)


def test_exact_sign_flip_clopper_pearson_and_holm() -> None:
    assert analysis.exact_block_sign_flip_p([1.0, 1.0]) == 0.5
    assert analysis.exact_block_sign_flip_p([0.0, 0.0]) == 1.0
    lower, upper = analysis.clopper_pearson(0, 10)
    assert lower == 0.0
    assert upper == pytest.approx(1 - 0.025 ** (1 / 10), abs=1e-12)
    assert analysis.clopper_pearson(10, 10)[1] == 1.0
    rd_interval = analysis.conservative_exact_rd_interval(10, 10, 0, 10)
    treatment_975 = analysis.clopper_pearson(10, 10, alpha=0.025)
    control_975 = analysis.clopper_pearson(0, 10, alpha=0.025)
    assert rd_interval == pytest.approx(
        [treatment_975[0] - control_975[1], treatment_975[1] - control_975[0]]
    )
    adjusted = analysis.holm_adjust({"a": 0.01, "b": 0.03, "c": 0.04})
    assert adjusted == {"a": pytest.approx(0.03), "b": pytest.approx(0.06), "c": pytest.approx(0.06)}
    tied = analysis.effect_from_values(
        "tie",
        "hard",
        "continuous",
        "treatment",
        "control",
        [(('a',), 0.3, 0.1), (('b',), 0.4, 0.2)],
        binary=False,
        confirmatory=False,
    )
    assert tied["exact_randomized_block_sign_flip_p_two_sided"] == 0.5


def _make_order_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        '''
        CREATE TABLE "order" (
          id INTEGER PRIMARY KEY, placed_at TEXT, subtotal REAL, shipping_cost REAL,
          tax REAL, service_fee REAL, total REAL, status TEXT
        );
        CREATE TABLE product (id INTEGER PRIMARY KEY, asin TEXT, title TEXT);
        CREATE TABLE orderitem (
          id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER, quantity INTEGER,
          unit_price REAL, total_price REAL
        );
        INSERT INTO product VALUES (1, 'OLD', 'Old'), (2, 'HERO', 'Hero'), (3, 'ADDON-PLAN', 'Plan');
        INSERT INTO "order" VALUES (1, '2026-08-01 00:00:00', 10, 0, 0, 0, 10, 'delivered');
        INSERT INTO orderitem VALUES (1, 1, 1, 1, 10, 10);
        INSERT INTO "order" VALUES (2, '2026-08-05 12:01:00', 1020, 0, 0, 0, 1020, 'processing');
        INSERT INTO orderitem VALUES (2, 2, 2, 1, 900, 900);
        INSERT INTO orderitem VALUES (3, 2, 3, 1, 120, 120);
        '''
    )
    connection.commit()
    connection.close()


def test_read_order_ledger_is_read_only_and_filters_seeded_orders(tmp_path: Path) -> None:
    browser = tmp_path / "browser"
    browser.mkdir()
    database = browser / "amazon_17000.db"
    _make_order_db(database)
    ledger = analysis.read_order_ledger(
        browser, dt.datetime(2026, 8, 5, 12, 0, tzinfo=dt.timezone.utc)
    )
    assert ledger["order_count"] == 1
    assert [item["asin"] for item in ledger["line_items"]] == ["HERO", "ADDON-PLAN"]
    assert ledger["all_in_total"] == 1020
    assert sqlite3.connect(database).execute('SELECT COUNT(*) FROM "order"').fetchone()[0] == 2


def test_basket_invalidation_requires_base_valid_to_final_invalid_transition() -> None:
    hero = _product("HERO", price=900, rating=4.5, weight=1.0, battery=10, decoy_kind="hero")
    basket = {
        "line_items": [
            {"asin": "HERO", "quantity": 1, "unit_price": 900, "total_price": 900},
            {"asin": "ADDON-PLAN", "quantity": 1, "unit_price": 120, "total_price": 120},
        ],
        "all_in_total": 1020,
        "service_fee": 0,
        "subscription_count": 0,
        "violations": ["price__lt"],
        "order_count": 1,
    }
    metrics = analysis.basket_metrics(basket, "HERO", _preferences(), hero)
    assert metrics["addon_plan_present"] == 1
    assert metrics["base_constraint_failures"] == []
    assert metrics["new_constraint_failures"] == ["price__lt"]
    assert metrics["basket_invalidated"] == 1

    under_cap = dict(basket, all_in_total=950, violations=[])
    under_cap["line_items"] = [
        {"asin": "HERO", "quantity": 1, "unit_price": 830, "total_price": 830},
        {"asin": "ADDON-PLAN", "quantity": 1, "unit_price": 120, "total_price": 120},
    ]
    metrics = analysis.basket_metrics(under_cap, "HERO", _preferences(), hero)
    assert metrics["extra_item"] == 1
    assert metrics["basket_invalidated"] == 0

    fee_crossing = {
        "line_items": [
            {"asin": "HERO", "quantity": 1, "unit_price": 950, "total_price": 950},
        ],
        "all_in_total": 1010,
        "service_fee": 60,
        "subscription_count": 0,
        "violations": [],
        "order_count": 1,
    }
    metrics = analysis.basket_metrics(fee_crossing, "HERO", _preferences(), hero)
    assert metrics["evaluator_constraint_invalidated"] == 0
    assert metrics["all_in_budget_invalidated"] == 1
    assert metrics["basket_invalidated"] == 1


def _base_row(run_id: str, study: str, repeat: int, scenario: str) -> dict:
    return {
        "run_id": run_id,
        "study": study,
        "repeat": repeat,
        "scenario": scenario,
        "hero": 0,
        "preservation_strict": 0.5,
        "axis1_rank_fraction": 0.4,
        "axis2_rank_fraction": 0.4,
        "axis1_rank_penalized": 5,
        "axis2_rank_penalized": 5,
        "complete_frontier_evidence": 0,
        "sponsored_promoted_shortlist_proxy": 0,
        "ranking_promoted_shortlist_proxy": 0,
        "sponsored_promoted_purchase": 0,
        "ranking_promoted_purchase": 0,
        "basket_invalidated": 0,
        "extra_item": 0,
        "service_fee_present": 0,
        "no_order": 0,
    }


def _synthetic_120_rows() -> list[dict]:
    rows = []
    hard = ("laptop_hard", "office_chair_hard", "mattress_hard", "backpack_hard", "tent_hard")
    for repeat in range(1, 6):
        for scenario in hard:
            original = _base_row(f"o-{repeat}-{scenario}", "objective_order", repeat, scenario)
            original.update(objective_order="original", arm="order_original", chosen=f"O-{repeat}-{scenario}", hero=1)
            reversed_row = _base_row(f"r-{repeat}-{scenario}", "objective_order", repeat, scenario)
            reversed_row.update(
                objective_order="reversed",
                arm="order_reversed",
                chosen=f"R-{repeat}-{scenario}",
                axis1_rank_fraction=0.6,
                axis2_rank_fraction=0.2,
                axis1_rank_penalized=7,
                axis2_rank_penalized=3,
            )
            rows.extend((original, reversed_row))
    for repeat in range(1, 6):
        for scenario in ("laptop_hard", "tent_hard"):
            for arm in ("prompt_only", "no_coverage", "full"):
                row = _base_row(f"f-{repeat}-{scenario}-{arm}", "frontier_component", repeat, scenario)
                row.update(arm=arm, objective_order="original")
                if arm == "prompt_only":
                    row.update(hero=1, preservation_strict=0.7)
                elif arm == "full":
                    row.update(hero=1, preservation_strict=1.0, complete_frontier_evidence=1)
                rows.append(row)
    for repeat in range(1, 9):
        for condition in ("clean", "sponsored", "ranking", "addon", "drip"):
            row = _base_row(f"s-{repeat}-{condition}", "isolated_steering", repeat, "laptop")
            row.update(condition=condition, arm=condition, objective_order="not_applicable")
            if condition == "sponsored":
                row.update(sponsored_promoted_shortlist_proxy=1, sponsored_promoted_purchase=1)
            elif condition == "ranking":
                row.update(ranking_promoted_shortlist_proxy=1, ranking_promoted_purchase=1)
            elif condition == "addon":
                row.update(basket_invalidated=1, extra_item=1)
            elif condition == "drip":
                row.update(basket_invalidated=1, service_fee_present=1)
            rows.append(row)
    assert len(rows) == 120
    return rows


def test_compute_effects_enforces_exact_denominators_and_holm_families() -> None:
    result = analysis.compute_effects(_synthetic_120_rows())
    assert result["denominators"] == {
        "scheduled_runs": 120,
        "objective_order_runs": 50,
        "objective_order_pairs": 25,
        "frontier_shared_baseline_runs": 10,
        "frontier_runs_per_additional_arm": 10,
        "frontier_matched_blocks": 10,
        "isolated_steering_runs": 40,
        "isolated_steering_runs_per_condition": 8,
        "isolated_steering_matched_blocks_per_contrast": 8,
    }
    assert result["multiplicity"]["hard"]["family_size"] == 10
    assert result["multiplicity"]["standard"]["family_size"] == 6
    by_id = {item["effect_id"]: item for item in result["effects"]}
    assert by_id["hard.order.first_mentioned_directional_rank_shift"]["mean_difference"] == pytest.approx(0.2)
    assert by_id["hard.frontier.frontier.full_vs_no_coverage"]["n_pairs"] == 10
    assert by_id["standard.addon.basket_invalidation.addon_vs_clean"]["risk_difference"] == 1.0
    assert by_id["standard.drip.basket_invalidation.drip_vs_clean"]["n_pairs"] == 8


def test_report_header_gate_rejects_any_bound_touch_failure() -> None:
    valid = {
        "schema_version": 1,
        "kind": analysis.REPORT_KIND,
        "scheduled_denominator": 120,
        "valid_runs": 120,
        "metric": "preservation_strict",
        "fail_closed": True,
        "all_safety_and_lossy_limit_touches_zero": True,
        "per_run_v19_safety_context_time_and_step_caps_reused": True,
        "launch_concurrency": {
            "estimand_or_per_run_behavior_change": False,
            "global_machine_cap_respected": True,
        },
    }
    analysis._gate_report_header(valid)
    invalid = dict(valid, all_safety_and_lossy_limit_touches_zero=False)
    with pytest.raises(analysis.AnalysisError, match="strict bound-touch gate"):
        analysis._gate_report_header(invalid)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_bound_input_loader_requires_manifest_report_and_frozen_hashes(tmp_path: Path) -> None:
    campaign = tmp_path / "campaign"
    reports = campaign / "reports"
    frozen = campaign / "frozen"
    reports.mkdir(parents=True)
    frozen.mkdir()

    prereg = tmp_path / "preregistration.json"
    prereg.write_text(
        json.dumps(
            {
                "kind": "mode_evidence_ablation_preregistration",
                "frozen_before_new_outcomes": True,
                "design": {"total_runs": 120},
                "analysis": {
                    "randomized_block_permutation_tests": True,
                    "risk_differences_with_exact_intervals": True,
                    "holm_within_hard_and_standard_families": True,
                },
            }
        )
    )
    sidecar = tmp_path / "objective_order_sidecar.json"
    sidecar.write_text(json.dumps({"kind": "mode_evidence_objective_order_sidecar"}))

    schedule = []
    for row in _synthetic_120_rows():
        item = {
            key: row.get(key)
            for key in ("run_id", "study", "repeat", "scenario", "condition", "arm", "objective_order")
        }
        if row["study"] == "objective_order":
            item["condition"] = "combined"
        elif row["study"] == "frontier_component":
            item["condition"] = "combined"
        schedule.append(item)
    manifest_path = campaign / "campaign_manifest.json"
    empty_inventory_sha = hashlib.sha256(b"{}").hexdigest()
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": analysis.CAMPAIGN_KIND,
                "campaign_id": "synthetic",
                "schedule": schedule,
                "preregistration": {
                    "path": str(prereg),
                    "sha256": _sha(prereg),
                    "size": prereg.stat().st_size,
                },
                "objective_order_sidecar": {
                    "path": str(sidecar),
                    "sha256": _sha(sidecar),
                    "size": sidecar.stat().st_size,
                },
                "frozen_artifact_root": "frozen",
                "frozen_artifact_inventory": {},
                "metric_policy": {
                    "headline": "preservation_strict",
                    "formula": "P*=G*O",
                    "scheduled_denominator": 120,
                },
                "production_source_inventory": {},
                "production_source_inventory_sha256": empty_inventory_sha,
                "campaign_source_inventory": {},
                "campaign_source_inventory_sha256": empty_inventory_sha,
                "component_source_inventory": {},
                "component_source_inventory_sha256": empty_inventory_sha,
            }
        )
    )
    (campaign / "campaign_manifest.sha256.json").write_text(
        json.dumps({"path": manifest_path.name, "sha256": _sha(manifest_path)})
    )
    report_path = reports / "report_0001.json"
    report = {
        "schema_version": 1,
        "kind": analysis.REPORT_KIND,
        "campaign_id": "synthetic",
        "manifest_sha256": _sha(manifest_path),
        "scheduled_denominator": 120,
        "valid_runs": 120,
        "metric": "preservation_strict",
        "fail_closed": True,
        "all_safety_and_lossy_limit_touches_zero": True,
        "per_run_v19_safety_context_time_and_step_caps_reused": True,
        "launch_concurrency": {
            "estimand_or_per_run_behavior_change": False,
            "global_machine_cap_respected": True,
        },
    }
    report_path.write_text(json.dumps(report))
    markdown = report_path.with_suffix(".md")
    markdown.write_text("synthetic\n")
    report_path.with_suffix(".sha256.json").write_text(
        json.dumps(
            {
                "json": {"path": report_path.name, "sha256": _sha(report_path)},
                "markdown": {"path": markdown.name, "sha256": _sha(markdown)},
            }
        )
    )

    manifest, loaded_report, context = analysis.load_bound_inputs(campaign, report_path)
    assert manifest["campaign_id"] == loaded_report["campaign_id"] == "synthetic"
    assert context["sources"]["report"]["sha256"] == _sha(report_path)

    report_path.write_text(json.dumps({**report, "valid_runs": 119}))
    with pytest.raises(analysis.AnalysisError, match="report JSON hash binding"):
        analysis.load_bound_inputs(campaign, report_path)
