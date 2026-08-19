from __future__ import annotations

import json
from pathlib import Path

from harness_posttrain.config import Campaign
from harness_posttrain.gates import evaluate_selection_gate


def _arm(path: Path, campaign: Campaign, optimal: set[int]) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": "harness-posttrain.selection-arm.v1",
                "campaign_digest": campaign.digest,
                "split": "selection",
                "source": "procedural",
                "runs": [
                    {
                        "run_id": f"run-{index:02d}",
                        "task_id": f"shadow-{index:02d}",
                        "contract_valid": True,
                        "tool_valid": True,
                        "optimal": index in optimal,
                        "infrastructure_complete": True,
                        "binding_backstop": False,
                    }
                    for index in range(64)
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_gate_promotes_only_clear_paired_gain(tmp_path: Path, campaign: Campaign) -> None:
    parent = _arm(tmp_path / "parent.json", campaign, {0, 1, 2, 3})
    passing = _arm(tmp_path / "passing.json", campaign, set(range(8)))
    failing = _arm(tmp_path / "failing.json", campaign, {0, 1, 2, 3, 4})
    passed = evaluate_selection_gate(
        campaign, parent_path=parent, candidate_path=passing
    )
    failed = evaluate_selection_gate(
        campaign, parent_path=parent, candidate_path=failing
    )
    assert passed["passed"] is True
    assert passed["selection"] == "candidate"
    assert failed["passed"] is False
    assert failed["selection"] == "parent"
