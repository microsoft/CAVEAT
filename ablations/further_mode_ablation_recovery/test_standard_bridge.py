from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ablations.further_mode_ablation_recovery import standard_bridge as bridge


def _manifest() -> dict:
    return json.loads(
        (bridge.DEFAULT_SOURCE / "campaign_manifest.json").read_text()
    )


def test_amendment_discloses_post_execution_recovery_and_pilot_exclusion():
    amendment = bridge._amendment()
    assert amendment["preserved_study"]["scheduled_denominator"] == 128
    assert amendment["retired_hard_execution"]["block5_disposition"] == (
        "operational_pilot_excluded"
    )
    assert amendment["replacement_hard_study"]["study_id"] == (
        "further_mode_ablation_hard_v2"
    )
    assert "post-execution" in amendment["disclosure"]


def test_standard_selection_is_exact_original_schedule_partition():
    manifest = _manifest()
    rows = bridge._selected_schedule(manifest)
    assert len(rows) == 128
    assert {row["block"] for row in rows} == {1, 2, 3, 4}
    assert {row["study"] for row in rows} == {"standard_steering"}
    assert {row["arm_id"] for row in rows} == set(bridge.EXPECTED_ARMS)
    for arm in bridge.EXPECTED_ARMS:
        assert sorted(
            row["repeat"] for row in rows if row["arm_id"] == arm
        ) == list(range(1, 9))


def test_pilot_exclusion_records_schedule_only_and_all_retired_ids():
    record = bridge._pilot_exclusion(_manifest())
    assert record["block5_disposition"] == "operational_pilot_excluded"
    assert len(record["block5_run_ids"]) == 28
    assert len(record["all_retired_hard_run_ids"]) == 112
    assert record["artifact_inventory_policy"] == "not_read_or_imported"
    assert not any("artifact" in key and isinstance(value, dict)
                   for key, value in record.items())


def test_frozen_bundle_round_trip_and_row_tamper_fails_closed(tmp_path: Path):
    bundle_path = bridge.freeze_standard(bridge.DEFAULT_SOURCE, tmp_path / "frozen")
    bundle = json.loads(bundle_path.read_text())
    assert bundle["study_id"] == bridge.STUDY_ID
    assert len(bundle["runs"]) == 128
    assert set(bundle["row_evidence"]) == set(bundle["exact_expected_run_ids"])

    # Re-hash the outer bundle after tampering: independent source-row
    # revalidation, not merely the sidecar, must detect the changed metric.
    raw = json.loads(bundle_path.read_text())
    raw["runs"][0]["preservation_strict"] = 0.123
    bundle_path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n")
    sidecar = bundle_path.with_name("study_bundle.sha256.json")
    sidecar.write_text(json.dumps({
        "path": bundle_path.name,
        "sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
    }, indent=2, sort_keys=True) + "\n")
    with pytest.raises(bridge.BridgeError, match="canonical row digest"):
        bridge.validate_standard_bundle(bundle_path)


def test_create_only_freeze_refuses_overwrite(tmp_path: Path):
    output = tmp_path / "occupied"
    output.mkdir()
    (output / "study_bundle.json").write_text("{}\n")
    with pytest.raises(bridge.BridgeError, match="refusing to replace"):
        bridge.freeze_standard(bridge.DEFAULT_SOURCE, output)
