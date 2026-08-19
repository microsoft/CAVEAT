from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pytest

from harness_posttrain_eval import interactive_sol_dagger_heldout_probe as probe
from harness_posttrain_eval.common import (
    IntegrityError,
    canonical_bytes,
    sha256_bytes,
    sha256_file,
)


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value) + b"\n")
    return path


def _write_jsonl(path: Path, rows: list[dict]) -> dict:
    payload = b"".join(canonical_bytes(row) + b"\n" for row in rows)
    path.write_bytes(payload)
    return {
        "relative_path": path.name,
        "sha256": sha256_bytes(payload),
        "bytes": len(payload),
        "rows": len(rows),
    }


def _message(index: int) -> dict:
    content = {
        "thinking": "Use the grounded button.",
        "action": [{"click": {"index": index}}],
    }
    return {
        "role": "assistant",
        "content": json.dumps(content, separators=(",", ":")),
    }


def _heldout(tmp_path: Path) -> tuple[Path, dict]:
    phase_subtypes = {
        "frontier_exploration": "exploration",
        "checkpoint_grounding": "decision_checkpoint",
        "approved_cart_entry": "add_to_cart",
        "dirty_cart_cleanup": "addon",
        "clean_checkout_order": "place_order",
    }
    rows: list[dict] = []
    audits: list[dict] = []
    index = 0
    for phase, count in probe.PHASE_COUNTS.items():
        for offset in range(count):
            row_id = f"{index + 1:064x}"
            state_id = f"{index + 1001:064x}"
            subtype = phase_subtypes[phase]
            audit = {
                "schema": probe.EXECUTION_AUDIT_SCHEMA,
                "row_id": row_id,
                "state_id": state_id,
                "source_split": "holdout",
                "phase": phase,
                "phase_subtype": subtype,
                "status": "accepted",
                "accepted": True,
                "browser_executed": True,
                "executed": True,
                "successor_observed": True,
                "postcondition_verified": True,
                "evaluator_read": False,
                "environment_transaction_db_read": True,
                "scorer_reward_read": False,
                "evidence_sha256": "a" * 64,
            }
            row = {
                "schema": probe.TRANSITION_SCHEMA,
                "schema_version": 1,
                "row_id": row_id,
                "state_id": state_id,
                "phase": phase,
                "phase_subtype": subtype,
                "trajectory_id": f"holdout-{index}",
                "task_id": "amazon-laptop",
                "variant": probe.VARIANTS[offset % len(probe.VARIANTS)],
                "horizon": 12,
                "source_split": "holdout",
                "source_sequence": index,
                "is_takeover_state": True,
                "messages_before_action": [
                    {"role": "system", "content": "BrowserUse system"},
                    {
                        "role": "user",
                        "content": (
                            "<browser_state>\n"
                            "[1]<button>Approved target</button>\n"
                            "[2]<button>Approved target</button>\n"
                            "</browser_state>"
                        ),
                    },
                ],
                "tools": [],
                "tool_choice": None,
                "parallel_tool_calls": False,
                "response_format": None,
                "teacher_message": _message(1),
                "qwen_message": _message(2),
                "chosen_by_executor": True,
                "execution": {
                    "attempted": True,
                    "valid": True,
                    "objective_success": True,
                    "successor_observed": True,
                    "postcondition_verified": True,
                },
                "source": {
                    "execution_audit_sha256": sha256_bytes(canonical_bytes(audit))
                },
            }
            rows.append(row)
            audits.append(audit)
            index += 1
    root = tmp_path / "heldout"
    root.mkdir()
    files = {
        "heldout_preflight.jsonl": _write_jsonl(
            root / "heldout_preflight.jsonl", rows
        ),
        "heldout_execution_audits.jsonl": _write_jsonl(
            root / "heldout_execution_audits.jsonl", audits
        ),
    }
    subtype_counts = dict(
        Counter(f"{row['phase']}/{row['phase_subtype']}" for row in rows)
    )
    body = {
        "schema": probe.HELDOUT_SCHEMA,
        "status": "ok",
        "teacher_model": "gpt-5.6-sol",
        "teacher_reasoning_effort": "low",
        "provenance": probe.COLLECTION_PROVENANCE,
        "target_rows": 24,
        "quota_derivation": "test",
        "materialized_rows": 24,
        "target_phase_counts": probe.PHASE_COUNTS,
        "phase_counts": probe.PHASE_COUNTS,
        "available_phase_counts": probe.PHASE_COUNTS,
        "phase_subtype_counts": subtype_counts,
        "available_phase_subtype_counts": subtype_counts,
        "phase_shortfalls": {phase: 0 for phase in probe.PHASES},
        "train_rows": 0,
        "heldout_only": True,
        "used_for_training": False,
        "files": files,
        "source": {"train_substitution": False},
    }
    manifest = {**body, "manifest_sha256": sha256_bytes(canonical_bytes(body))}
    path = _write(root / "manifest.json", manifest)
    return path, manifest


def _endpoint(tmp_path: Path) -> tuple[Path, dict]:
    alias = "qwen35-browser-action-step26-action-weighted-ce-111111111111-exact-lora"
    value = {
        "receipt_sha256": "e" * 64,
        "candidate": {
            "served_model_name": alias,
            "adapter_tree_sha256": "1" * 64,
            "composite_sha256": "2" * 64,
        },
        "model_spec": {"base_url": probe.LOCAL_BASE_URL},
        "artifacts": {
            "pvc_lineage_attestation": {
                "path": "/data/lineage.json",
                "file_sha256": "3" * 64,
                "body_sha256": "4" * 64,
            }
        },
    }
    return _write(tmp_path / "endpoint.json", {"test": True}), value


def _response(alias: str, index: int = 1) -> dict:
    return {
        "id": "test",
        "object": "chat.completion",
        "model": alias,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": _message(index),
            }
        ],
        "usage": None,
    }


def test_exact_and_semantic_signatures_distinguish_equivalent_dom_indices() -> None:
    messages = [
        {
            "role": "user",
            "content": "[1]<button>Same target</button>\n[2]<button>Same target</button>",
        }
    ]
    left = probe._message_actions(_message(1), label="left")
    right = probe._message_actions(_message(2), label="right")
    assert probe.exact_signature(left) != probe.exact_signature(right)
    assert probe.semantic_signature(left, messages) == probe.semantic_signature(
        right, messages
    )


def test_heldout_manifest_is_exact24_holdout_only_and_source_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, manifest = _heldout(tmp_path)
    monkeypatch.setattr(probe, "ALLOWED_SOURCE_ROOTS", (tmp_path,))
    value, rows, audits = probe.validate_heldout_manifest(
        path,
        expected_file_sha256=sha256_file(path),
        expected_body_sha256=manifest["manifest_sha256"],
    )
    assert value["train_rows"] == 0
    assert len(rows) == len(audits) == 24
    assert {row["source_split"] for row in rows} == {"holdout"}

    changed = json.loads(path.read_text())
    changed["provenance"]["validator_materializer_commit"] = "0" * 40
    core = {key: item for key, item in changed.items() if key != "manifest_sha256"}
    path.write_bytes(
        canonical_bytes(
            {**core, "manifest_sha256": sha256_bytes(canonical_bytes(core))}
        )
        + b"\n"
    )
    with pytest.raises(IntegrityError):
        probe.validate_heldout_manifest(
            path,
            expected_file_sha256=sha256_file(path),
            expected_body_sha256=json.loads(path.read_text())["manifest_sha256"],
        )


def test_probe_run_and_audit_are_create_only_recomputable_and_never_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    heldout_path, heldout = _heldout(tmp_path)
    endpoint_path, endpoint = _endpoint(tmp_path)
    monkeypatch.setattr(probe, "ALLOWED_SOURCE_ROOTS", (tmp_path,))
    monkeypatch.setattr(probe, "validate_endpoint", lambda _path: endpoint)
    monkeypatch.setattr(
        probe,
        "_post",
        lambda _body, *, api_key, timeout: (
            200,
            _response(endpoint["candidate"]["served_model_name"]),
        ),
    )
    output = tmp_path / "probe-output"
    arguments = argparse.Namespace(
        endpoint_receipt=endpoint_path,
        expected_endpoint_file_sha256=sha256_file(endpoint_path),
        expected_endpoint_body_sha256=endpoint["receipt_sha256"],
        heldout_manifest=heldout_path,
        expected_heldout_file_sha256=sha256_file(heldout_path),
        expected_heldout_body_sha256=heldout["manifest_sha256"],
        output_root=output,
        timeout_seconds=900,
    )
    probe.run_probe(arguments)
    report = probe.audit_report(output / "report.json")
    assert report["gate"]["passed"] is True
    assert report["overall"]["exact_matches"] == 24
    assert report["browser_executed"] is False
    assert report["training_rows_read"] == 0
    assert report["heldout_teacher_manifest_validated"] is True
    assert report["heldout_teacher_targets_read_for_comparison"] == 24
    assert report["heldout_teacher_targets_used_for_training"] is False
    with pytest.raises(IntegrityError):
        probe.run_probe(arguments)


def test_action_final_parser_rejects_markdown_or_nonfinal_action() -> None:
    with pytest.raises(IntegrityError):
        probe._message_actions(
            {"role": "assistant", "content": "```json\n{}\n```"}, label="candidate"
        )
    content = canonical_bytes({"action": [{"click": {"index": 1}}], "memory": "x"}).decode()
    with pytest.raises(IntegrityError):
        probe._message_actions(
            {"role": "assistant", "content": content}, label="candidate"
        )


def test_request_replays_collector_qwen_policy_without_new_sampling_fields(
    tmp_path: Path,
) -> None:
    _path, _manifest = _heldout(tmp_path)
    row = json.loads((tmp_path / "heldout/heldout_preflight.jsonl").read_text().splitlines()[0])
    body = probe.request_body(row, "candidate")
    assert set(body) == {"model", "messages", "parallel_tool_calls", *probe.REQUEST_POLICY}
    assert "stream" not in body
    assert "n" not in body
    assert "max_completion_tokens" not in body
    assert "tools" not in body
