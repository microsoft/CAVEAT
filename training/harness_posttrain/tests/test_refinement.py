from __future__ import annotations

from pathlib import Path

from conftest import sft_source, write_jsonl

from harness_posttrain.config import Campaign
from harness_posttrain.refinement import materialize_refinement


def _decision(ordinal: int, *, tool_valid: bool = True) -> dict[str, object]:
    return {
        "ordinal": ordinal,
        "public_state_sha256": f"{ordinal + 1:064x}",
        "checks": {
            "contract_valid": True,
            "contract_semantic": True,
            "tool_valid": tool_valid,
            "frontier_complete": True,
            "no_loop": True,
        },
        "messages": [
            {"role": "user", "content": "What is the next action?"},
            {"role": "assistant", "content": '{"action":"continue"}'},
        ],
        "tools": [],
    }


def test_refinement_keeps_successes_and_first_verified_correction(
    tmp_path: Path,
    campaign: Campaign,
    sealed_splits: tuple[Path, list[dict[str, object]]],
) -> None:
    manifest_path, _ = sealed_splits
    episodes = [
        {
            "schema": "harness-posttrain.on-policy-episode.v1",
            "episode_id": "success-1",
            "task_id": "procedural-train-1",
            "source": "procedural",
            "scenario": "generic_shop_a",
            "infrastructure_complete": True,
            "binding_backstop": False,
            "outcome": {"optimal": True},
            "decisions": [_decision(0), _decision(1), _decision(2)],
        },
        {
            "schema": "harness-posttrain.on-policy-episode.v1",
            "episode_id": "failure-1",
            "task_id": "procedural-train-2",
            "source": "procedural",
            "scenario": "generic_shop_b",
            "infrastructure_complete": True,
            "binding_backstop": False,
            "outcome": {"optimal": False},
            "decisions": [_decision(0, tool_valid=False), _decision(1)],
        },
    ]
    corrections = [
        {
            "schema": "harness-posttrain.verified-correction.v1",
            "episode_id": "failure-1",
            "decision_ordinal": 0,
            "public_state_sha256": f"{1:064x}",
            "public_evidence_only": True,
            "verifier_passed": True,
            "messages": [
                {"role": "user", "content": "What is the next action?"},
                {"role": "assistant", "content": '{"action":"valid_tool_call"}'},
            ],
            "tools": [],
        }
    ]
    result = materialize_refinement(
        campaign,
        split_manifest_path=manifest_path,
        episodes_path=write_jsonl(tmp_path / "episodes.jsonl", episodes),
        corrections_path=write_jsonl(tmp_path / "corrections.jsonl", corrections),
        rehearsal_path=write_jsonl(tmp_path / "rehearsal.jsonl", [sft_source("rehearse")]),
        output_dir=tmp_path / "refinement",
    )
    assert result["episodes"] == {
        "eligible": 2,
        "successful": 1,
        "failed": 1,
        "failed_without_verified_correction": 0,
    }
    assert result["candidate_counts"] == {"correction": 1, "success": 2, "rehearsal": 1}
    assert result["output"]["rows"] == 4
