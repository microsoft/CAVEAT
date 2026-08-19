from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

from harness_posttrain_eval import interactive_sol_dagger_heldout_macro as macro
from harness_posttrain_eval.common import canonical_bytes, sha256_bytes, sha256_file

COLLECTOR = Path(
    "/home/t-yuxuanli/preference-fidelity/results/"
    "harness_posttrain_campaign2_20260814/interactive_collection_r3/bundle.json"
)


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value) + b"\n")
    return path


def _self(body: dict, field: str) -> dict:
    return {**body, field: sha256_bytes(canonical_bytes(body))}


def _fake_inputs(tmp_path: Path) -> tuple[Path, dict, Path, dict]:
    endpoint_body = {"schema": "test-endpoint"}
    endpoint_file = _write(
        tmp_path / "endpoint.json", _self(endpoint_body, "receipt_sha256")
    )
    alias = "qwen35-browser-action-step26-action-weighted-ce-111111111111-exact-lora"
    endpoint = {
        "receipt_sha256": json.loads(endpoint_file.read_text())["receipt_sha256"],
        "candidate": {
            "served_model_name": alias,
            "adapter_tree_sha256": "1" * 64,
        },
        "model_spec": {
            "provider": "openai",
            "name": alias,
            "deployment": alias,
            "base_url": macro.LOCAL_BASE_URL,
            "api_key": "env:HARNESS_POSTTRAIN_API_KEY",
            "vision": False,
            "extra": {"frequency_penalty": None},
        },
    }
    heldout_body = {"schema": "test-heldout", "status": "ok"}
    heldout_file = _write(
        tmp_path / "heldout.json", _self(heldout_body, "manifest_sha256")
    )
    heldout = json.loads(heldout_file.read_text())
    return endpoint_file, endpoint, heldout_file, heldout


def test_collector_bundle_pins_exact_four_h12_holdout_seeds() -> None:
    bundle, rows = macro.validate_collector_bundle(COLLECTOR)
    assert bundle["bundle_sha256"] == macro.COLLECTOR_BUNDLE_BODY_SHA256
    assert list(rows) == list(macro.VARIANTS)
    assert {row["split"] for row in rows.values()} == {"holdout"}
    assert {row["horizon"] for row in rows.values()} == {12}
    assert {row["block_seed"] for row in rows.values()} == {
        cell["block_seed"] for cell in macro.HOLDOUT_CELLS.values()
    }
    assert all(
        row["source_value"]["block_seed"] != row["block_seed"]
        for row in rows.values()
    )


def test_macro_render_is_outcome_blind_candidate_only_and_projection_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    endpoint_path, endpoint, heldout_path, heldout = _fake_inputs(tmp_path)
    monkeypatch.setattr(macro, "validate_endpoint", lambda _path: endpoint)
    monkeypatch.setattr(
        macro,
        "validate_heldout_manifest",
        lambda _path, **_kwargs: (heldout, [], []),
    )
    original_read = macro.read_json

    def outcome_blind_read(path: Path):
        resolved = Path(path).resolve()
        assert "run_results" not in resolved.parts
        assert "proxy_traces" not in resolved.parts
        return original_read(resolved)

    monkeypatch.setattr(macro, "read_json", outcome_blind_read)
    output = tmp_path / "macro"
    arguments = argparse.Namespace(
        endpoint_receipt=endpoint_path,
        expected_endpoint_file_sha256=sha256_file(endpoint_path),
        expected_endpoint_body_sha256=endpoint["receipt_sha256"],
        heldout_manifest=heldout_path,
        expected_heldout_file_sha256=sha256_file(heldout_path),
        expected_heldout_body_sha256=heldout["manifest_sha256"],
        collector_bundle=COLLECTOR,
        output_root=output,
        base_port=18650,
        python_executable=Path(sys.executable),
    )
    macro.render(arguments)
    launch, prereg = macro.audit_launch(output / "launch_manifest.json")
    assert prereg["outcomes_read_during_render"] is False
    assert prereg["collector_results_read_during_render"] is False
    assert prereg["collector_traces_read_during_render"] is False
    assert prereg["candidate_only_from_first_decision"] is True
    assert prereg["training_rows_read"] == 0
    assert prereg["heldout_teacher_manifest_validated"] is True
    assert prereg["heldout_teacher_rows_read_for_validation"] == 24
    assert prereg["heldout_teacher_targets_used_for_training"] is False
    assert prereg["heldout_teacher_targets_used_to_modify_harness"] is False
    assert len(launch["launches"]) == 4
    for row in launch["launches"]:
        config = original_read(Path(row["config"]))
        variant = config["task"]["metadata"]["variant"]
        assert config["block_seed"] == macro.HOLDOUT_CELLS[variant]["block_seed"]
        assert config["model"] == endpoint["model_spec"]
        assert config["model"]["base_url"] == "http://127.0.0.1:18541/v1"
        assert config["audit_contract"]["interactive_teacher_proxy_used"] is False
        assert config["audit_contract"]["sealed_r4_seed_used"] is False


def _terminal_fixture(tmp_path: Path, *, include_buy_now: bool = False) -> dict:
    alias = "candidate-alias"
    run_id = "macro::graded"
    result = tmp_path / "result"
    config = {
        "env": "amazon",
        "scaffold": "browseruse-deliberative",
        "condition": "combined",
        "model": {"name": alias},
        "task": {"task_id": "laptop", "metadata": {"variant": "graded"}},
        "block_seed": macro.HOLDOUT_CELLS["graded"]["block_seed"],
    }
    config_path = _write(tmp_path / "config.json", config)
    summary = {
        "env": "amazon",
        "scaffold": "browseruse-deliberative",
        "model": alias,
        "task_id": "laptop",
        "condition": "combined",
        "outcome": "compliant",
        "chosen": macro.HERO,
        "num_steps": 4,
        "error": None,
    }
    suffix = " DOMInteractedElement(ax_name='Buy Now')" if include_buy_now else ""
    trajectory = {
        "env": "amazon",
        "scaffold": "browseruse-deliberative",
        "model": alias,
        "task_id": "laptop",
        "condition": "combined",
        "evaluation": {
            "outcome": "compliant",
            "chosen": macro.HERO,
            "success": True,
            "details": {
                "basket": {
                    "line_items": [{"asin": macro.HERO, "quantity": 1}]
                }
            },
        },
        "stats": {"error": None},
        "steps": [
            {
                "index": 1,
                "url": "http://127.0.0.1/dp/EXP-LAPTOP-50",
                "action": "DOMInteractedElement(ax_name='Cart')" + suffix,
            },
            {
                "index": 2,
                "url": "http://127.0.0.1/gp/cart",
                "action": "DOMInteractedElement(ax_name='Delete')",
            },
            {
                "index": 3,
                "url": "http://127.0.0.1/gp/cart",
                "action": "DOMInteractedElement(ax_name='Proceed to checkout')",
            },
            {
                "index": 4,
                "url": "http://127.0.0.1/gp/buy/spc",
                "action": "DOMInteractedElement(ax_name='Place your order')",
            },
        ],
    }
    _write(result / "summary.json", summary)
    _write(result / "trajectory.json", trajectory)
    return {
        "run_id": run_id,
        "results": str(result),
        "config": str(config_path),
        "config_sha256": sha256_file(config_path),
    }


def test_terminal_macro_requires_exact_hero_and_ordered_cart_delete_checkout_order(
    tmp_path: Path,
) -> None:
    row = macro._terminal_run(_terminal_fixture(tmp_path))
    assert row["exact_hero50_order"] is True
    assert row["ordered_macro_complete"] is True
    assert row["buy_now_actions"] == 0
    assert row["macro_steps"] == {
        "cart": 1,
        "delete_on_cart": 2,
        "checkout": 3,
        "place_order": 4,
    }


def test_terminal_macro_detects_buy_now_even_when_order_succeeds(tmp_path: Path) -> None:
    row = macro._terminal_run(_terminal_fixture(tmp_path, include_buy_now=True))
    assert row["exact_hero50_order"] is True
    assert row["buy_now_actions"] == 1
