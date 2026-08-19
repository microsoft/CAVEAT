from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from harness_posttrain import cli


def test_fixed_v5_gate_cli_dispatches_every_argument(
    monkeypatch: Any, tmp_path: Path, capsys: Any
) -> None:
    campaign = object()
    observed: dict[str, Any] = {}

    monkeypatch.setattr(cli, "_campaign", lambda _args: campaign)

    def fake_gate(received_campaign: object, **kwargs: Any) -> dict[str, Any]:
        observed["campaign"] = received_campaign
        observed.update(kwargs)
        return {"status": "complete"}

    monkeypatch.setattr(cli, "run_fixed_v5_gate", fake_gate)
    campaign_root = tmp_path / "campaign"
    receipt = tmp_path / "receipt.json"
    baseline = tmp_path / "baseline"
    output = tmp_path / "gate"
    result = cli.main(
        [
            "gate-browser-action-fixed-v5",
            "--campaign",
            str(tmp_path / "campaign.yaml"),
            "--campaign-root",
            str(campaign_root),
            "--training-receipt",
            str(receipt),
            "--baseline-selection-dir",
            str(baseline),
            "--output",
            str(output),
            "--concurrency",
            "17",
            "--num-gpus",
            "4",
            "--port",
            "18123",
        ]
    )
    assert result == 0
    assert observed == {
        "campaign": campaign,
        "campaign_root": campaign_root,
        "training_receipt": receipt,
        "baseline_selection_dir": baseline,
        "output_dir": output,
        "concurrency": 17,
        "num_gpus": 4,
        "port": 18123,
    }
    assert json.loads(capsys.readouterr().out) == {"status": "complete"}


def test_fixed_v5_gate_scripts_are_safe_and_four_gpu() -> None:
    root = Path(__file__).parents[1]
    entrypoint = root / "scripts/gate_browser_action_fixed_v5.sh"
    launcher = root / "scripts/launch_browser_action_fixed_v5_gate.sh"
    subprocess.run(["bash", "-n", str(entrypoint)], check=True)
    subprocess.run(["bash", "-n", str(launcher)], check=True)

    entrypoint_text = entrypoint.read_text(encoding="utf-8")
    assert "gate-browser-action-fixed-v5" in entrypoint_text
    assert '--training-receipt "$receipt"' in entrypoint_text
    assert '--baseline-selection-dir "$baseline"' in entrypoint_text
    assert '--num-gpus 4' in entrypoint_text
    assert 'browser_action_fixed_v5/$artifact_sha' in entrypoint_text
    assert "selection_nonbinding_v4" in entrypoint_text

    launcher_text = launcher.read_text(encoding="utf-8")
    assert "@sha256:" in launcher_text
    assert '[[ -z "$(git -C "$root" status --porcelain --untracked-files=all)" ]]' in (
        launcher_text
    )
    assert "GPUS_PER_NODE=4" in launcher_text
    assert "NPROC_PER_NODE=1" in launcher_text
    assert "B200_PRIORITY=p0" in launcher_text
    assert "HPT_ARTIFACT_SOURCE_GIT_SHA" in launcher_text
    assert "gate_browser_action_fixed_v5.sh" in launcher_text
