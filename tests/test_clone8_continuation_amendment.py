from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import clone8_continuation_amendment as continuation


STOP_UTC = "2026-08-01T08:55:47Z"
INTERRUPTED_EVIDENCE = (
    "missing terminal artifacts: summary.json, trajectory.json; exact SIGKILL "
    "plus both-artifacts-absent plus a green authoritative no-checkout proof "
    "is required for refill"
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _limit(marker: str) -> dict:
    payload = {
        "schema_version": 1,
        "categories": {
            "launch_only": {
                "campaign_launch": {
                    "configured": {"marker": marker},
                    "source": "test",
                }
            }
        },
    }
    return {**payload, "sha256": continuation._sha_payload(payload)}


def _source(marker: str) -> dict:
    return {
        "controller.py": {
            "sha256": continuation._sha_bytes(marker.encode()),
            "size": len(marker),
        }
    }


def _recovery_source(marker: str) -> dict:
    def record(value: str) -> dict:
        return {
            "sha256": continuation._sha_bytes(value.encode()),
            "size": len(value),
        }

    return {
        path: record(f"{path}:{marker}")
        for path in continuation.RECOVERY_ALLOWED_SOURCE_CHANGES
    } | {"scripts/unchanged.py": record("stable")}


def _green_cleanup() -> dict:
    return {
        "confirmed_empty": True,
        "port_released": True,
        "remaining_scope_pids": [],
        "uncertified_scope_pids": [],
        "error": None,
    }


def _fixture(
    tmp_path: Path, *, extra_unlaunched_rows: int = 0
) -> tuple[Path, dict, dict, dict]:
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    frozen = campaign / "frozen_inputs"
    frozen.mkdir()
    old_source = _source("old")
    old_limit = _limit("old")
    _write_json(frozen / "code_inventory.json", old_source)
    _write_json(frozen / "limit_contract.json", old_limit)
    frozen_hashes = {
        name: continuation._sha_file(frozen / name)
        for name in ("code_inventory.json", "limit_contract.json")
    }
    rows = [
        {
            "index": index,
            "run_id": f"env_r1/cell_{index}",
            "cell_name": f"cell_{index}",
            "env": "env",
            "port": 21000 + index,
            "model": "model",
            "logical": "logical",
        }
        for index in range(5 + extra_unlaunched_rows)
    ]
    manifest = {
        "campaign": str(campaign.resolve()),
        "campaign_uuid": "12345678-1234-4234-8234-123456789abc",
        "total_runs": len(rows),
        "runs": rows,
        "models": {"model": {"logical": "logical"}},
        "schedule": {
            "regions": ["one", "two", "three"],
            "default_jobs": 2,
            "host_browser_root_ceiling": 3,
            "reserved_external_browser_roots": 1,
        },
        "frozen_artifacts": frozen_hashes,
    }
    _write_json(campaign / "campaign_manifest.json", manifest)
    manifest_hash = continuation._sha_file(campaign / "campaign_manifest.json")
    (campaign / "campaign_manifest.sha256").write_text(
        f"{manifest_hash}  campaign_manifest.json\n"
    )

    interrupted = []
    for row in rows[:5]:
        index = row["index"]
        pid = 3000 + index
        out_dir = campaign / "runs" / "attempt_1" / row["run_id"]
        out_dir.mkdir(parents=True)
        (out_dir / f"env_{row['port']}.db").write_bytes(f"db-{index}".encode())
        if index < 2:
            _write_json(out_dir / "summary.json", {"row": index})
            _write_json(out_dir / "trajectory.json", {"row": index, "steps": []})
            classification = {
                "class": "scored" if index == 0 else "behavioral",
                "code": "measured",
            }
            finished = "2026-08-01T08:54:00Z"
        else:
            classification = {
                "class": "scientific_invalid",
                "code": "unpersisted_or_incomplete_attempt",
                "evidence": INTERRUPTED_EVIDENCE,
            }
            finished = f"2026-08-01T08:55:{45 + index:02d}Z"
            interrupted.append({"attempt": 1, "pid": pid, "run_id": row["run_id"]})
        stem = f"{index:04d}_{row['cell_name']}.attempt1"
        _write_json(campaign / "launch_receipts" / f"{stem}.launch.json", {
            "schema": "agentarena.clone8-launch-receipt.v3",
            "campaign_uuid": manifest["campaign_uuid"],
            "manifest_sha256": manifest_hash,
            "run_index": index,
            "run_id": row["run_id"],
            "attempt": 1,
            "pid": pid,
            "out_dir": str(out_dir.resolve()),
        })
        _write_json(campaign / "completion_receipts" / f"{stem}.complete.json", {
            "schema": "agentarena.clone8-completion-receipt.v3",
            "run_index": index,
            "run_id": row["run_id"],
            "attempt": 1,
            "pid": pid,
            "returncode": 0,
            "finished_utc": finished,
            "classification": classification,
            "worker_scope_cleanup": _green_cleanup(),
        })
    _write_json(campaign / "operator_stop_receipt.json", {
        "schema": "agentarena.clone8-operator-stop.v1",
        "requested_utc": STOP_UTC,
        "signal": 15,
        "worker_sessions_isolated": True,
        "workers_signaled": False,
        "running": interrupted,
    })

    new_source = _source("new")
    new_limit = _limit("new")
    scheduler = continuation.make_scheduler_contract(
        manifest,
        jobs=2,
        spawn_stagger_seconds=10.0,
        host_browser_root_ceiling=3,
        reserved_external_browser_roots=1,
        primary_region_deployment_caps={
            "logical": {"one": 1, "two": 1, "three": 1}
        },
        logical_deployment_caps={"logical": 2},
        admission_policy={"completion_barrier": False},
    )
    return campaign, new_source, new_limit, scheduler


def _proof(out_dir: Path, row: dict) -> dict:
    db_path = out_dir / f"{row['env']}_{row['port']}.db"
    return {
        "schema": "test-no-checkout.v1",
        "confirmed_no_checkout": True,
        "hash_stable": True,
        "sidecars_absent": True,
        "error": None,
        "env": row["env"],
        "db_path": str(db_path.resolve()),
        "db_sha256": continuation._sha_file(db_path),
    }


def _correction_source(marker: str) -> dict:
    records = {
        path: {
            "sha256": continuation._sha_bytes(f"{path}:{marker}".encode()),
            "size": len(f"{path}:{marker}"),
        }
        for path in continuation.VERIFIER_CORRECTION_ALLOWED_SOURCE_CHANGES
    }
    stable = "scripts/stable.py"
    records[stable] = {
        "sha256": continuation._sha_bytes(stable.encode()),
        "size": len(stable),
    }
    return records


def _correction_fixture(tmp_path: Path):
    campaign = tmp_path / "correction"
    campaign.mkdir()
    frozen = campaign / "frozen_inputs"
    frozen.mkdir()
    old_source = _correction_source("old")
    admission = {"completion_barrier": False, "mode": "opportunistic"}
    limit_payload = {
        "schema_version": 1,
        "categories": {
            "launch_only": {
                "campaign_launch": {
                    "configured": {"block_admission_policy": admission},
                    "source": "test",
                }
            }
        },
    }
    limits = {
        **limit_payload,
        "sha256": continuation._sha_payload(limit_payload),
    }
    _write_json(frozen / "code_inventory.json", old_source)
    _write_json(frozen / "limit_contract.json", limits)
    rows = [
        {
            "index": index,
            "run_id": f"airbnb_r1/cell_{index}",
            "cell_name": f"cell_{index}",
            "env": "airbnb",
            "port": 25000 + index,
            "model": "model",
            "logical": "logical",
        }
        for index in range(3)
    ]
    scratch = {"schema": "test-scratch.v1", "root": "/tmp/test-scratch"}
    manifest = {
        "campaign": str(campaign.resolve()),
        "campaign_uuid": "12345678-1234-4234-8234-123456789abc",
        "total_runs": 3,
        "runs": rows,
        "models": {"model": {"logical": "logical"}},
        "scratch": scratch,
        "schedule": {
            "regions": ["one", "two", "three"],
            "default_jobs": 2,
            "global_spawn_stagger_seconds": 10.0,
            "host_browser_root_ceiling": 3,
            "reserved_external_browser_roots": 1,
            "primary_region_deployment_cap": 1,
            "logical_deployment_cap": 2,
        },
        "frozen_artifacts": {
            "code_inventory.json": continuation._sha_file(
                frozen / "code_inventory.json"
            ),
            "limit_contract.json": continuation._sha_file(
                frozen / "limit_contract.json"
            ),
        },
    }
    _write_json(campaign / "campaign_manifest.json", manifest)
    manifest_sha = continuation._sha_file(campaign / "campaign_manifest.json")
    (campaign / "campaign_manifest.sha256").write_text(
        f"{manifest_sha}  campaign_manifest.json\n"
    )
    classes = {}
    for row in rows[:2]:
        index = row["index"]
        out_dir = campaign / "runs" / "attempt_1" / row["run_id"]
        out_dir.mkdir(parents=True)
        _write_json(out_dir / "summary.json", {"outcome": "violation"})
        _write_json(out_dir / "trajectory.json", {"steps": [1]})
        (out_dir / f"airbnb_{row['port']}.db").write_bytes(
            f"db-{index}".encode()
        )
        classification = (
            {"class": "scored", "code": "measured_transaction"}
            if index == 0
            else {
                "class": "scientific_invalid",
                "code": "invalid_score_backfill",
                "evidence": "stale verifier expected a different price",
                "steps": 1,
                "outcome": "violation",
            }
        )
        classes[classification["class"]] = (
            classes.get(classification["class"], 0) + 1
        )
        stem = f"{index:04d}_{row['cell_name']}.attempt1"
        launch = campaign / "launch_receipts" / f"{stem}.launch.json"
        completion = (
            campaign / "completion_receipts" / f"{stem}.complete.json"
        )
        _write_json(launch, {
            "schema": "agentarena.clone8-launch-receipt.v3",
            "campaign_uuid": manifest["campaign_uuid"],
            "manifest_sha256": manifest_sha,
            "run_index": index,
            "run_id": row["run_id"],
            "attempt": 1,
            "pid": 7000 + index,
            "out_dir": str(out_dir.resolve()),
        })
        _write_json(completion, {
            "schema": "agentarena.clone8-completion-receipt.v3",
            "run_index": index,
            "run_id": row["run_id"],
            "attempt": 1,
            "pid": 7000 + index,
            "returncode": 0,
            "finished_utc": "2026-08-03T00:00:00Z",
            "classification": classification,
            "worker_scope_cleanup": _green_cleanup(),
            "outcome": "violation",
        })
    _write_json(campaign / "status.json", {
        "state": "protocol_invalid",
        "total_runs": 3,
        "running": [],
        "running_count": 0,
        "pending_primary": 1,
        "pending_refill": 0,
        "final_count": 2,
        "final_classes": classes,
    })
    current_source = _correction_source("new")
    scheduler = continuation.make_scheduler_contract(
        manifest,
        jobs=2,
        spawn_stagger_seconds=10.0,
        host_browser_root_ceiling=3,
        reserved_external_browser_roots=1,
        primary_region_deployment_caps={
            "logical": {"one": 1, "two": 1, "three": 1}
        },
        logical_deployment_caps={"logical": 2},
        admission_policy=admission,
        scratch_policy=scratch,
    )
    return campaign, current_source, limits, scheduler


def _correction_proof(
    _campaign, _manifest, row, _attempt, _out_dir, _launch, _completion
):
    return {
        "schema": continuation.VERIFIER_CORRECTION_PROOF_SCHEMA,
        "verifier": "current_frozen_semantic_classifier",
        "read_only_replay": True,
        "corrected_classification": {
            "class": "scored",
            "code": "measured_transaction",
            "evidence": "",
            "steps": 1,
            "outcome": "violation",
        },
        "derived_summary": {
            "outcome": "violation",
            "chosen": "32",
            "chosen_label": "Villa",
            "preservation": 0.7667,
            "preservation_strict": 0.0,
            "strict_binary": 0,
            "literal_hero": 0,
            "hero_identity": "hero",
        },
    }


def _protocol_source(marker_by_path: dict[str, str]) -> dict:
    paths = (
        continuation.PROTOCOL_RECOVERY_ALLOWED_SOURCE_CHANGES
        | {"scripts/stable.py"}
    )
    result = {}
    for path in paths:
        marker = marker_by_path.get(path, "stable")
        raw = f"{path}:{marker}"
        result[path] = {
            "sha256": continuation._sha_bytes(raw.encode()),
            "size": len(raw),
        }
    return result


def _protocol_trajectory(*, touched: bool) -> dict:
    configured = 60000
    maximum = 125074 if touched else 1234
    count = int(touched)
    lossy = {
        name: {
            "configured": configured,
            "touched_count": count if name == "read_state_chars" else 0,
            "observations": {
                "touched_count": count if name == "read_state_chars" else 0,
                "max_observed": maximum if name == "read_state_chars" else 100,
            },
        }
        for name in ("read_state_chars", "action_results_chars")
    }
    return {
        "stats": {
            "context_cap_audit": {
                "complete": True,
                "limits": {
                    "read_state_chars": {
                        "configured": configured,
                        "touched_count": count,
                        "max_observed": maximum,
                    }
                },
            },
            "limit_audit": {
                "complete": True,
                "error": None,
                "categories": {
                    "lossy_context_limits": lossy,
                    "safety_backstops": {
                        "max_steps": {
                            "configured": 12000,
                            "touched_count": 0,
                            "observations": {},
                        },
                        "whole_run_timeout_seconds": {
                            "configured": 172800,
                            "touched_count": 0,
                            "observations": {},
                        },
                    },
                },
            },
        }
    }


def _write_protocol_attempt(
    campaign: Path,
    manifest: dict,
    index: int,
    classification: dict,
    *,
    touched: bool = False,
    outcome: str = "violation",
) -> None:
    row = manifest["runs"][index]
    out_dir = campaign / "runs" / "attempt_1" / row["run_id"]
    out_dir.mkdir(parents=True)
    hero = f"hero-{index}"
    _write_json(out_dir / "summary.json", {
        "outcome": outcome,
        "preservation_strict": 1.0 if outcome == "compliant" else 0.0,
        "strict_binary": 1 if outcome == "compliant" else 0,
        "literal_hero": 1 if outcome == "compliant" else 0,
        "hero_identity": hero,
        "chosen": hero if outcome == "compliant" else f"other-{index}",
    })
    _write_json(out_dir / "trajectory.json", _protocol_trajectory(touched=touched))
    (out_dir / f"{row['env']}_{row['port']}.db").write_bytes(
        f"db-{index}".encode()
    )
    stem = f"{index:04d}_{row['cell_name']}.attempt1"
    manifest_sha = continuation._sha_file(campaign / "campaign_manifest.json")
    _write_json(campaign / "launch_receipts" / f"{stem}.launch.json", {
        "schema": "agentarena.clone8-launch-receipt.v3",
        "campaign_uuid": manifest["campaign_uuid"],
        "manifest_sha256": manifest_sha,
        "run_index": index,
        "run_id": row["run_id"],
        "attempt": 1,
        "pid": 9000 + index,
        "out_dir": str(out_dir.resolve()),
    })
    scratch = {
        "schema": "agentarena.clone8-scratch-cleanup.v1",
        "run_index": index,
        "run_id": row["run_id"],
        "attempt": 1,
        "status": "removed",
        "worker_scope_green": True,
        "readable_holder_pids": [],
        "confirmed_absent": True,
        "error": None,
    }
    scratch_path = (
        campaign / "scratch_cleanup_receipts" / f"{stem}.scratch.json"
    )
    _write_json(scratch_path, scratch)
    _write_json(campaign / "completion_receipts" / f"{stem}.complete.json", {
        "schema": "agentarena.clone8-completion-receipt.v3",
        "run_index": index,
        "run_id": row["run_id"],
        "attempt": 1,
        "pid": 9000 + index,
        "returncode": 0,
        "finished_utc": "2026-08-03T09:00:00Z",
        "classification": classification,
        "worker_scope_cleanup": _green_cleanup(),
        "outcome": outcome,
        "scratch_cleanup_receipt": str(scratch_path.resolve()),
        "scratch_cleanup_receipt_sha256": continuation._sha_file(scratch_path),
        "scratch_cleanup": scratch,
    })


def _protocol_correction_proof(
    _campaign, _manifest, _row, _attempt, _out_dir, _launch, completion
):
    outcome = completion["outcome"]
    compliant = outcome == "compliant"
    return {
        "schema": continuation.VERIFIER_CORRECTION_PROOF_SCHEMA,
        "verifier": "current_frozen_semantic_classifier",
        "read_only_replay": True,
        "corrected_classification": {
            "class": "scored",
            "code": "measured_transaction",
            "evidence": "",
            "steps": 5,
            "outcome": outcome,
        },
        "derived_summary": {
            "outcome": outcome,
            "chosen": "hero" if compliant else "other",
            "chosen_label": "Listing",
            "preservation": 1.0 if compliant else 0.4,
            "preservation_strict": 1.0 if compliant else 0.0,
            "strict_binary": int(compliant),
            "literal_hero": int(compliant),
            "hero_identity": "hero",
        },
    }


def _lossless_proof(
    _campaign, _manifest, row, _attempt, out_dir, _launch, _completion
):
    trajectory = json.loads((out_dir / "trajectory.json").read_text())
    summary = json.loads((out_dir / "summary.json").read_text())
    record = trajectory["stats"]["context_cap_audit"]["limits"][
        "read_state_chars"
    ]
    return {
        "schema": continuation.LOSSLESS_READ_STATE_PROOF_SCHEMA,
        "read_only_replay": True,
        "run_index": continuation.PROTOCOL_RECOVERY_RUN_INDEX,
        "run_id": row["run_id"],
        "attempt": 1,
        "trajectory_sha256": continuation._sha_file(out_dir / "trajectory.json"),
        "cause": "lossy_browser_use_read_state_history_rendering",
        "raw_read_state": {
            "configured_chars": record["configured"],
            "touched_count": record["touched_count"],
            "max_observed_chars": record["max_observed"],
        },
        "raw_limit_audit": {
            "limit_audit_complete": True,
            "only_lossy_limit_touched": "read_state_chars",
            "raw_context_audit_matches": True,
            "all_safety_backstops_untouched": True,
        },
        "measured_result": {
            key: summary[key]
            for key in (
                "outcome",
                "preservation_strict",
                "strict_binary",
                "literal_hero",
                "hero_identity",
                "chosen",
            )
        },
        "remedy": {
            "activation_scope": "run_598_attempt_2_only",
            "delivery": "lossless_read_state",
            "action_results_limit": "unchanged",
            "all_other_runs": "unchanged",
            "catalog_tasks_and_steering": "unchanged",
            "upstream_guards": "fail_closed",
        },
    }


def _protocol_fixture(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(continuation, "PROTOCOL_RECOVERY_RUN_INDEX", 3)
    monkeypatch.setattr(continuation, "PROTOCOL_RECOVERY_CORRECTION_INDEX", 4)
    monkeypatch.setattr(
        continuation,
        "PROTOCOL_RECOVERY_INHERITED_CORRECTION_INDICES",
        frozenset({1, 2}),
    )
    monkeypatch.setattr(
        continuation,
        "PROTOCOL_RECOVERY_RAW_INVALID_INDICES",
        frozenset({1, 2, 3, 4}),
    )
    monkeypatch.setattr(
        continuation,
        "PROTOCOL_RECOVERY_EFFECTIVE_INVALID_INDICES",
        frozenset({3, 4}),
    )
    monkeypatch.setattr(continuation, "PROTOCOL_RECOVERY_MANIFEST_ROWS", 6)
    monkeypatch.setattr(continuation, "PROTOCOL_RECOVERY_FRONTIER_ROWS", 5)
    monkeypatch.setattr(continuation, "PROTOCOL_RECOVERY_PENDING_ROWS", 1)

    campaign = tmp_path / "protocol"
    campaign.mkdir()
    frozen = campaign / "frozen_inputs"
    frozen.mkdir()
    old_source = _protocol_source({
        path: "base" for path in continuation.PROTOCOL_RECOVERY_ALLOWED_SOURCE_CHANGES
    })
    admission = {"completion_barrier": False, "mode": "opportunistic"}
    limit_payload = {
        "schema_version": 1,
        "categories": {
            "launch_only": {
                "campaign_launch": {
                    "configured": {"block_admission_policy": admission},
                    "source": "test",
                }
            }
        },
    }
    limits = {**limit_payload, "sha256": continuation._sha_payload(limit_payload)}
    _write_json(frozen / "code_inventory.json", old_source)
    _write_json(frozen / "limit_contract.json", limits)
    envs = ["airbnb", "airbnb", "airbnb", "instacart", "airbnb", "airbnb"]
    rows = [
        {
            "index": index,
            "run_id": f"{envs[index]}_r1/cell_{index}",
            "cell_name": f"cell_{index}",
            "env": envs[index],
            "port": 28000 + index,
            "model": "Qwen3.5-122B" if index == 5 else "model",
            "logical": "logical",
            "condition": "clean",
        }
        for index in range(6)
    ]
    scratch = {"schema": "test-scratch.v1", "root": "/tmp/test-scratch"}
    manifest = {
        "campaign": str(campaign.resolve()),
        "campaign_uuid": "12345678-1234-4234-8234-123456789abc",
        "total_runs": 6,
        "runs": rows,
        "models": {"model": {"logical": "logical"}},
        "scratch": scratch,
        "schedule": {
            "regions": ["one", "two", "three"],
            "default_jobs": 2,
            "global_spawn_stagger_seconds": 10.0,
            "host_browser_root_ceiling": 3,
            "reserved_external_browser_roots": 1,
            "primary_region_deployment_cap": 1,
            "logical_deployment_cap": 2,
        },
        "frozen_artifacts": {
            "code_inventory.json": continuation._sha_file(frozen / "code_inventory.json"),
            "limit_contract.json": continuation._sha_file(frozen / "limit_contract.json"),
        },
    }
    _write_json(campaign / "campaign_manifest.json", manifest)
    manifest_sha = continuation._sha_file(campaign / "campaign_manifest.json")
    (campaign / "campaign_manifest.sha256").write_text(
        f"{manifest_sha}  campaign_manifest.json\n"
    )
    invalid_backfill = {
        "class": "scientific_invalid",
        "code": "invalid_score_backfill",
        "evidence": "stale verifier",
        "steps": 5,
        "outcome": "violation",
    }
    _write_protocol_attempt(
        campaign, manifest, 0,
        {"class": "scored", "code": "measured_transaction"},
    )
    _write_protocol_attempt(campaign, manifest, 1, dict(invalid_backfill))
    _write_protocol_attempt(campaign, manifest, 2, dict(invalid_backfill))
    _write_json(campaign / "status.json", {
        "state": "protocol_invalid",
        "total_runs": 6,
        "running": [],
        "running_count": 0,
        "pending_primary": 3,
        "pending_refill": 0,
        "final_count": 3,
        "final_classes": {"scored": 1, "scientific_invalid": 2},
    })
    correction_source = _protocol_source({
        path: (
            "correction"
            if path in continuation.VERIFIER_CORRECTION_ALLOWED_SOURCE_CHANGES
            else "base"
        )
        for path in continuation.PROTOCOL_RECOVERY_ALLOWED_SOURCE_CHANGES
    })
    scheduler = continuation.make_scheduler_contract(
        manifest,
        jobs=2,
        spawn_stagger_seconds=10.0,
        host_browser_root_ceiling=3,
        reserved_external_browser_roots=1,
        primary_region_deployment_caps={
            "logical": {"one": 1, "two": 1, "three": 1}
        },
        logical_deployment_caps={"logical": 2},
        admission_policy=admission,
        scratch_policy=scratch,
    )
    predecessor = continuation.create_verifier_correction_amendment(
        campaign,
        continuation_source_inventory=correction_source,
        continuation_limit_contract=limits,
        continuation_scheduler_contract=scheduler,
        verifier_correction_proof=_protocol_correction_proof,
        created_utc="2026-08-03T08:00:00Z",
    )
    _write_protocol_attempt(
        campaign,
        manifest,
        3,
        {
            "class": "scientific_invalid",
            "code": "limit_touched",
            "evidence": "lossy_context_limits.read_state_chars",
            "outcome": "compliant",
        },
        touched=True,
        outcome="compliant",
    )
    fresh_invalid = dict(invalid_backfill)
    fresh_invalid["outcome"] = "compliant"
    _write_protocol_attempt(
        campaign, manifest, 4, fresh_invalid, outcome="compliant"
    )
    _write_json(campaign / "status.json", {
        "state": "protocol_invalid",
        "total_runs": 6,
        "running": [],
        "running_count": 0,
        "pending_primary": 1,
        "pending_refill": 0,
        "final_count": 5,
        "final_classes": {"scored": 3, "scientific_invalid": 2},
    })
    recovery_source = _protocol_source({
        path: "recovery"
        for path in continuation.PROTOCOL_RECOVERY_ALLOWED_SOURCE_CHANGES
    })
    return campaign, manifest, predecessor, recovery_source, limits, scheduler


def _protocol_postmortem_source(predecessor: dict) -> dict:
    result = json.loads(json.dumps(predecessor))
    for path in continuation.PROTOCOL_POSTMORTEM_ALLOWED_SOURCE_CHANGES:
        raw = f"{path}:postmortem"
        result[path] = {
            "sha256": continuation._sha_bytes(raw.encode()),
            "size": len(raw),
        }
    return result


def _write_protocol_postmortem_attempt(
    campaign: Path,
    manifest: dict,
    predecessor,
    row: dict,
) -> tuple[Path, Path]:
    index = row["index"]
    out_dir = campaign / "runs" / "attempt_1" / row["run_id"]
    out_dir.mkdir(parents=True)
    (out_dir / f"{row['env']}_{row['port']}.db").write_bytes(b"db-postmortem")
    (out_dir / "run.log").write_text("operator sigkill\n")
    (out_dir / "environment_server.log").write_text("server stopped\n")
    stem = f"{index:04d}_{row['cell_name']}.attempt1"
    launch_path = campaign / "launch_receipts" / f"{stem}.launch.json"
    completion_path = (
        campaign / "completion_receipts" / f"{stem}.complete.json"
    )
    scratch_path = (
        campaign / "scratch_cleanup_receipts" / f"{stem}.scratch.json"
    )
    attestation, nonce = continuation._predecessor_launch_identity(
        campaign, manifest, predecessor, row, 1
    )
    provenance = {
        "schema": "agentarena.clone8-scratch-provenance.v1",
        "campaign_uuid": manifest["campaign_uuid"],
        "manifest_sha256": continuation._sha_file(
            campaign / "campaign_manifest.json"
        ),
        "run_index": index,
        "run_id": row["run_id"],
        "attempt": 1,
        "root": "/tmp/test-scratch",
        "root_device": 11,
        "root_inode": 12,
        "namespace_device": 11,
        "namespace_inode": 13,
        "device": 11,
        "inode": 14,
        "owner_uid": 1000,
        "mode": 0o700,
        "path": f"/tmp/test-scratch/ns/r{index:04d}a1",
        "cache_nonce_sha256": continuation._sha_bytes(nonce.encode()),
    }
    _write_json(launch_path, {
        "schema": "agentarena.clone8-launch-receipt.v3",
        "campaign_uuid": manifest["campaign_uuid"],
        "manifest_sha256": continuation._sha_file(
            campaign / "campaign_manifest.json"
        ),
        "run_index": index,
        "run_id": row["run_id"],
        "attempt": 1,
        "pid": 9500 + index,
        "pid_start_ticks": 123456,
        "out_dir": str(out_dir.resolve()),
        "cache_nonce": nonce,
        "continuation_attestation": attestation,
        "scratch_provenance": provenance,
    })
    evidence = (
        "numeric PGID/SID members lacked exact nonce certification: "
        f"{list(continuation.PROTOCOL_POSTMORTEM_UNCERTIFIED_SCOPE_PIDS)}"
    )
    scratch = {
        "schema": "agentarena.clone8-scratch-cleanup.v1",
        "campaign_uuid": manifest["campaign_uuid"],
        "manifest_sha256": continuation._sha_file(
            campaign / "campaign_manifest.json"
        ),
        "run_index": index,
        "run_id": row["run_id"],
        "attempt": 1,
        "path": provenance["path"],
        "provenance_sha256": continuation._sha_bytes(
            continuation._canonical_bytes(provenance)
        ),
        "status": "skipped_worker_scope_not_green",
        "worker_scope_green": False,
        "readable_holder_pids": None,
        "confirmed_absent": False,
        "error": "worker scope was not proven empty; scratch retained",
    }
    _write_json(scratch_path, scratch)
    _write_json(completion_path, {
        "schema": "agentarena.clone8-completion-receipt.v3",
        "run_index": index,
        "run_id": row["run_id"],
        "attempt": 1,
        "pid": 9500 + index,
        "returncode": -9,
        "finished_utc": "2026-08-03T11:00:00Z",
        "classification": {
            "class": "scientific_invalid",
            "code": "worker_scope_cleanup_failed",
            "evidence": evidence,
        },
        "worker_scope_cleanup": {
            "schema": "agentarena.clone8-worker-scope-cleanup.v2",
            "worker_pid": 9500 + index,
            "launch_pid_start_ticks": 123456,
            "cache_nonce_sha256": continuation._sha_bytes(nonce.encode()),
            "mode": "controller_exit",
            "signal_membership": "exact_cache_nonce_and_start_ticks_via_pidfd",
            "confirmed_empty": True,
            "port_released": True,
            "remaining_scope_pids": [],
            "kill_signaled_pids": [],
            "uncertified_scope_pids": list(
                continuation.PROTOCOL_POSTMORTEM_UNCERTIFIED_SCOPE_PIDS
            ),
            "error": evidence,
        },
        "no_checkout_proof": None,
        "outcome": None,
        "num_steps": None,
        "preservation_strict": None,
        "literal_hero": None,
        "scratch_cleanup_receipt": str(scratch_path.resolve()),
        "scratch_cleanup_receipt_sha256": continuation._sha_file(scratch_path),
        "scratch_cleanup": scratch,
    })
    return completion_path, out_dir


def _protocol_postmortem_proof(
    campaign, _manifest, out_dir, row, launch, _completion
):
    db_path = out_dir / f"{row['env']}_{row['port']}.db"
    provenance = launch["scratch_provenance"]
    completion_path = (
        campaign / "completion_receipts"
        / f"{row['index']:04d}_{row['cell_name']}.attempt1.complete.json"
    )
    return {
        "schema": continuation.POSTMORTEM_PROOF_SCHEMA,
        "run_index": row["index"],
        "run_id": row["run_id"],
        "attempt": 1,
        "immutable_completion_sha256": continuation._sha_file(completion_path),
        "operator_intervention": True,
        "benchmark_or_harness_changed": False,
        "inactivity_timeout_policy_created": False,
        "trajectory_examined_before_intervention": True,
        "preconfigured_timeout_did_not_fire": True,
        "selection_bias_risk": True,
        "no_checkout": {
            "schema": "agentarena.clone8-no-checkout-proof.v1",
            "env": row["env"],
            "db_path": str(db_path.resolve()),
            "db_sha256": continuation._sha_file(db_path),
            "sidecars_absent": True,
            "hash_stable": True,
            "readable_fd_holder_pids_before": [],
            "readable_fd_holder_pids_after": [],
            "quick_check": ["ok"],
            "confirmed_no_checkout": True,
            "error": None,
        },
        "process_absence": {
            "worker_pid": launch["pid"],
            "launch_pid_start_ticks": launch["pid_start_ticks"],
            "cache_nonce_sha256": continuation._sha_bytes(
                launch["cache_nonce"].encode()
            ),
            "launch_identity_absent": True,
            "exact_nonce_pids": [],
            "worker_scope_snapshot": [],
            "confirmed_absent": True,
            "error": None,
        },
        "scratch_quarantine": {
            "schema": "agentarena.clone8-retained-scratch-quarantine.v1",
            "policy": "retain_in_place_until_terminal_audit",
            "path": provenance["path"],
            "provenance_sha256": continuation._sha_bytes(
                continuation._canonical_bytes(provenance)
            ),
            "marker_sha256": "a" * 64,
            "device": provenance["device"],
            "inode": provenance["inode"],
            "owner_uid": provenance["owner_uid"],
            "mode": provenance["mode"],
            "entries": 1,
            "bytes": 0,
            "tree_sha256": continuation.PROTOCOL_POSTMORTEM_SCRATCH_TREE_SHA256,
            "readable_holder_pids": [],
            "confirmed_quarantined": True,
            "error": None,
        },
        "confirmed_recoverable": True,
        "error": None,
    }


def _protocol_postmortem_fixture(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(continuation, "PROTOCOL_POSTMORTEM_RUN_INDEX", 5)
    monkeypatch.setattr(continuation, "PROTOCOL_POSTMORTEM_MANIFEST_ROWS", 6)
    monkeypatch.setattr(continuation, "PROTOCOL_POSTMORTEM_FRONTIER_ROWS", 6)
    monkeypatch.setattr(
        continuation, "PROTOCOL_POSTMORTEM_PENDING_PRIMARY_ROWS", 0
    )
    monkeypatch.setattr(
        continuation,
        "PROTOCOL_POSTMORTEM_CORRECTION_INDICES",
        frozenset({1, 2, 4}),
    )
    monkeypatch.setattr(
        continuation,
        "PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES",
        frozenset({3}),
    )
    monkeypatch.setattr(
        continuation,
        "PROTOCOL_POSTMORTEM_RAW_INVALID_INDICES",
        frozenset({1, 2, 3, 4, 5}),
    )
    monkeypatch.setattr(
        continuation,
        "PROTOCOL_POSTMORTEM_EFFECTIVE_INVALID_INDICES",
        frozenset({3, 5}),
    )
    monkeypatch.setattr(
        continuation,
        "PROTOCOL_POSTMORTEM_UNRESOLVED_INVALID_INDICES",
        frozenset({5}),
    )
    monkeypatch.setattr(
        continuation,
        "PROTOCOL_POSTMORTEM_MISSING_PRIMARY_INDICES",
        frozenset(),
    )
    monkeypatch.setattr(
        continuation,
        "PROTOCOL_POSTMORTEM_UNCERTIFIED_SCOPE_PIDS",
        (9511, 9512),
    )
    monkeypatch.setattr(
        continuation,
        "PROTOCOL_POSTMORTEM_SCRATCH_TREE_SHA256",
        "f" * 64,
    )
    campaign, manifest, _correction, source, limits, scheduler = (
        _protocol_fixture(tmp_path, monkeypatch)
    )
    predecessor = continuation.create_protocol_recovery_amendment(
        campaign,
        continuation_source_inventory=source,
        continuation_limit_contract=limits,
        continuation_scheduler_contract=scheduler,
        verifier_correction_proof=_protocol_correction_proof,
        lossless_read_state_proof=_lossless_proof,
        created_utc="2026-08-03T10:00:00Z",
    )
    completion, out_dir = _write_protocol_postmortem_attempt(
        campaign, manifest, predecessor, manifest["runs"][5]
    )
    monkeypatch.setattr(
        continuation,
        "PROTOCOL_POSTMORTEM_COMPLETION_SHA256",
        continuation._sha_file(completion),
    )
    monkeypatch.setattr(
        continuation,
        "PROTOCOL_POSTMORTEM_RECEIPT_AGGREGATE_ROOT",
        tmp_path,
    )
    for directory, constant in (
        (
            "launch_receipts",
            "PROTOCOL_POSTMORTEM_LAUNCH_RECEIPT_AGGREGATE_SHA256",
        ),
        (
            "completion_receipts",
            "PROTOCOL_POSTMORTEM_COMPLETION_RECEIPT_AGGREGATE_SHA256",
        ),
        (
            "scratch_cleanup_receipts",
            "PROTOCOL_POSTMORTEM_SCRATCH_RECEIPT_AGGREGATE_SHA256",
        ),
    ):
        monkeypatch.setattr(
            continuation,
            constant,
            continuation._protocol_postmortem_receipt_aggregate(
                sorted((campaign / directory).glob("*.json"))
            ),
        )
    _write_json(campaign / "status.json", {
        "state": "protocol_invalid",
        "total_runs": 6,
        "running": [],
        "running_count": 0,
        "pending_primary": 0,
        "pending_refill": 1,
        "final_count": 5,
        "final_classes": {"scored": 4, "scientific_invalid": 1},
        "final_by_condition": {"clean": 5},
        continuation.STATUS_AMENDMENT_FIELD: predecessor.amendment_sha256,
        continuation.STATUS_SCHEDULER_FIELD: predecessor.scheduler_sha256,
    })
    post_source = _protocol_postmortem_source(source)
    return (
        campaign, manifest, predecessor, post_source, limits, scheduler,
        completion, out_dir,
    )


def test_verifier_correction_promotes_exact_hash_without_retry_or_redraw(
    tmp_path: Path,
) -> None:
    campaign, source, limits, scheduler = _correction_fixture(tmp_path)
    artifact_paths = sorted(
        path
        for root in ("runs", "launch_receipts", "completion_receipts")
        for path in (campaign / root).rglob("*")
        if path.is_file()
    )
    before = {path: continuation._sha_file(path) for path in artifact_paths}

    verified = continuation.create_verifier_correction_amendment(
        campaign,
        continuation_source_inventory=source,
        continuation_limit_contract=limits,
        continuation_scheduler_contract=scheduler,
        verifier_correction_proof=_correction_proof,
        created_utc="2026-08-03T00:30:00Z",
    )

    assert sorted(verified.correction_by_index) == [1]
    assert verified.retry_by_index == {}
    assert verified.amendment["denominator"] == {
        "manifest_rows": 3,
        "row_identity": "campaign_manifest.runs[].index",
        "preserved_checkpoint_rows": 2,
        "promoted_attempts_retained_byte_identical": 1,
        "retried_rows": 0,
        "added_rows": 0,
        "counting_rule": continuation.VERIFIER_CORRECTION_COUNTING_RULE,
    }
    item = verified.correction_by_index[1]
    completion = campaign / item["completion_receipt"]
    assert item["completion_sha256"] == continuation._sha_file(completion)
    assert item["recorded_classification"]["code"] == "invalid_score_backfill"
    assert item["corrected_classification"]["class"] == "scored"
    assert {path: continuation._sha_file(path) for path in artifact_paths} == before
    assert continuation.read_monitor_overlay(campaign)[
        "verifier_corrected_indices"
    ] == [1]


def test_verifier_correction_fails_closed_on_receipt_drift(
    tmp_path: Path,
) -> None:
    campaign, source, limits, scheduler = _correction_fixture(tmp_path)
    verified = continuation.create_verifier_correction_amendment(
        campaign,
        continuation_source_inventory=source,
        continuation_limit_contract=limits,
        continuation_scheduler_contract=scheduler,
        verifier_correction_proof=_correction_proof,
        created_utc="2026-08-03T00:30:00Z",
    )
    receipt = campaign / verified.correction_by_index[1]["completion_receipt"]
    receipt.write_text(receipt.read_text() + "\n")
    with pytest.raises(
        continuation.ContinuationAmendmentError, match="drifted"
    ):
        continuation.load_amendment(campaign)


def test_verifier_correction_refuses_any_other_scientific_invalid(
    tmp_path: Path,
) -> None:
    campaign, source, limits, scheduler = _correction_fixture(tmp_path)
    path = next((campaign / "completion_receipts").glob("0001_*.json"))
    completion = json.loads(path.read_text())
    completion["classification"]["code"] = "limit_touched"
    _write_json(path, completion)
    with pytest.raises(
        continuation.ContinuationAmendmentError,
        match="outside the exact Airbnb invalid_score_backfill",
    ):
        continuation.create_verifier_correction_amendment(
            campaign,
            continuation_source_inventory=source,
            continuation_limit_contract=limits,
            continuation_scheduler_contract=scheduler,
            verifier_correction_proof=_correction_proof,
            created_utc="2026-08-03T00:30:00Z",
        )
    assert not (campaign / continuation.AMENDMENT_ROOT).exists()


def test_verifier_correction_refuses_scheduler_drift(tmp_path: Path) -> None:
    campaign, source, limits, scheduler = _correction_fixture(tmp_path)
    scheduler["active_limits"]["jobs"] = 1
    with pytest.raises(
        continuation.ContinuationAmendmentError,
        match="may not change the base scheduler",
    ):
        continuation.create_verifier_correction_amendment(
            campaign,
            continuation_source_inventory=source,
            continuation_limit_contract=limits,
            continuation_scheduler_contract=scheduler,
            verifier_correction_proof=_correction_proof,
            created_utc="2026-08-03T00:30:00Z",
        )


def test_create_verify_and_exact_retry_authorization(tmp_path: Path) -> None:
    campaign, source, limits, scheduler = _fixture(tmp_path)
    derived = continuation.derive_authorization(campaign)
    assert derived.preserved_classes == (("behavioral", 1), ("scored", 1))
    assert [item.run_index for item in derived.interrupted] == [2, 3, 4]

    verified = continuation.create_amendment(
        campaign,
        continuation_source_inventory=source,
        continuation_limit_contract=limits,
        continuation_scheduler_contract=scheduler,
        checkout_proof=_proof,
        created_utc="2026-08-01T09:30:00Z",
    )
    assert verified.amendment["denominator"] == {
        "manifest_rows": 5,
        "row_identity": "campaign_manifest.runs[].index",
        "preserved_checkpoint_rows": 2,
        "superseded_attempts_retained_for_audit": 3,
        "added_rows": 0,
        "counting_rule": continuation.DENOMINATOR_COUNTING_RULE,
    }
    item = verified.retry_by_index[2]
    receipt = campaign / item["completion_receipt"]
    assert continuation.authorized_retry_attempt(
        verified,
        run_index=2,
        run_id="env_r1/cell_2",
        prior_attempt=1,
        completion_receipt=receipt,
    ) == 2
    assert continuation.authorized_retry_attempt(
        verified,
        run_index=1,
        run_id="env_r1/cell_1",
        prior_attempt=1,
        completion_receipt=receipt,
    ) is None
    assert verified.amendment["hash_chain"]["old"]["manifest_sha256"] == (
        continuation._sha_file(campaign / "campaign_manifest.json")
    )
    assert continuation.read_monitor_overlay(campaign)["active_limits"]["jobs"] == 2


def test_receipt_mutation_fails_closed(tmp_path: Path) -> None:
    campaign, source, limits, scheduler = _fixture(tmp_path)
    verified = continuation.create_amendment(
        campaign,
        continuation_source_inventory=source,
        continuation_limit_contract=limits,
        continuation_scheduler_contract=scheduler,
        checkout_proof=_proof,
        created_utc="2026-08-01T09:30:00Z",
    )
    receipt = campaign / verified.retry_by_index[2]["completion_receipt"]
    receipt.write_text(receipt.read_text() + "\n")
    with pytest.raises(continuation.ContinuationAmendmentError, match="drifted"):
        continuation.verify_amendment(
            campaign,
            continuation_source_inventory=source,
            continuation_limit_contract=limits,
            continuation_scheduler_contract=scheduler,
            checkout_proof=_proof,
        )


def test_runtime_drift_and_second_publication_fail_closed(tmp_path: Path) -> None:
    campaign, source, limits, scheduler = _fixture(tmp_path)
    continuation.create_amendment(
        campaign,
        continuation_source_inventory=source,
        continuation_limit_contract=limits,
        continuation_scheduler_contract=scheduler,
        checkout_proof=_proof,
        created_utc="2026-08-01T09:30:00Z",
    )
    with pytest.raises(continuation.ContinuationAmendmentError, match="current runtime"):
        continuation.verify_amendment(
            campaign,
            continuation_source_inventory=_source("different"),
            continuation_limit_contract=limits,
            continuation_scheduler_contract=scheduler,
            checkout_proof=_proof,
        )
    with pytest.raises(continuation.ContinuationAmendmentError, match="create-only"):
        continuation.create_amendment(
            campaign,
            continuation_source_inventory=source,
            continuation_limit_contract=limits,
            continuation_scheduler_contract=scheduler,
            checkout_proof=_proof,
            created_utc="2026-08-01T09:31:00Z",
        )


def test_non_green_checkout_proof_never_publishes(tmp_path: Path) -> None:
    campaign, source, limits, scheduler = _fixture(tmp_path)

    def bad_proof(out_dir: Path, row: dict) -> dict:
        proof = _proof(out_dir, row)
        proof["confirmed_no_checkout"] = False
        return proof

    with pytest.raises(continuation.ContinuationAmendmentError, match="checkout proof"):
        continuation.create_amendment(
            campaign,
            continuation_source_inventory=source,
            continuation_limit_contract=limits,
            continuation_scheduler_contract=scheduler,
            checkout_proof=bad_proof,
            created_utc="2026-08-01T09:30:00Z",
        )
    assert not (campaign / continuation.AMENDMENT_ROOT).exists()


def test_successor_amendment_checkpoints_predecessor_infra_and_chains(
    tmp_path: Path,
) -> None:
    campaign, source, limits, scheduler = _fixture(
        tmp_path, extra_unlaunched_rows=1
    )
    predecessor = continuation.create_amendment(
        campaign,
        continuation_source_inventory=source,
        continuation_limit_contract=limits,
        continuation_scheduler_contract=scheduler,
        checkout_proof=_proof,
        created_utc="2026-08-01T09:30:00Z",
    )
    manifest = json.loads((campaign / "campaign_manifest.json").read_text())
    row = manifest["runs"][5]
    out_dir = campaign / "runs" / "attempt_1" / row["run_id"]
    out_dir.mkdir(parents=True)
    (out_dir / f"env_{row['port']}.db").write_bytes(b"db-5")
    _write_json(out_dir / "summary.json", {
        "outcome": "none", "num_steps": 0,
    })
    _write_json(out_dir / "trajectory.json", {
        "steps": [],
        "evaluation": {"outcome": "none"},
        "stats": {"num_steps": 0},
    })
    stem = "0005_cell_5.attempt1"
    launch_path = campaign / "launch_receipts" / f"{stem}.launch.json"
    completion_path = (
        campaign / "completion_receipts" / f"{stem}.complete.json"
    )
    attestation = {
        "amendment_sha256": predecessor.amendment_sha256,
        "scheduler_sha256": predecessor.scheduler_sha256,
        "base_manifest_sha256": continuation._sha_file(
            campaign / "campaign_manifest.json"
        ),
        "source_inventory_sha256": predecessor.amendment["hash_chain"][
            "new"
        ]["source_inventory_sha256"],
        "limit_contract_sha256": predecessor.amendment["hash_chain"][
            "new"
        ]["limit_contract_sha256"],
    }
    predecessor_nonce = (
        "eight_env_leaderboard/"
        f"{manifest['campaign_uuid']}/"
        f"{continuation._sha_file(campaign / 'campaign_manifest.json')}/"
        f"{row['run_id']}/attempt_1/"
        f"continuation-{predecessor.amendment_sha256}"
    )
    _write_json(launch_path, {
        "schema": "agentarena.clone8-launch-receipt.v3",
        "run_index": 5,
        "run_id": row["run_id"],
        "attempt": 1,
        "pid": 4005,
        "cache_nonce": predecessor_nonce,
        "continuation_attestation": attestation,
    })
    scratch_path = campaign / "scratch_cleanup_receipts" / f"{stem}.scratch.json"
    scratch = {
        "confirmed_absent": True,
        "worker_scope_green": True,
        "readable_holder_pids": [],
        "status": "removed",
        "error": None,
    }
    _write_json(scratch_path, scratch)
    classification = {
        "class": "infra",
        "code": "zero_step",
        "evidence": "steps=0; agent never received an observation",
        "outcome": "none",
        "steps": 0,
    }
    cleanup = _green_cleanup()
    cleanup["cache_nonce_sha256"] = continuation._sha_bytes(
        predecessor_nonce.encode()
    )
    _write_json(completion_path, {
        "schema": "agentarena.clone8-completion-receipt.v3",
        "run_index": 5,
        "run_id": row["run_id"],
        "attempt": 1,
        "finished_utc": "2026-08-01T09:40:00Z",
        "classification": classification,
        "worker_scope_cleanup": cleanup,
        "no_checkout_proof": None,
        "scratch_cleanup_receipt": str(scratch_path.resolve()),
        "scratch_cleanup_receipt_sha256": continuation._sha_file(scratch_path),
        "scratch_cleanup": scratch,
    })

    successor_source = _source("successor")
    successor_limits = _limit("successor")
    successor = continuation.create_successor_amendment(
        campaign,
        continuation_source_inventory=successor_source,
        continuation_limit_contract=successor_limits,
        continuation_scheduler_contract=scheduler,
        checkout_proof=_proof,
        created_utc="2026-08-01T10:00:00Z",
    )
    assert successor.amendment["hash_chain"][
        "predecessor_amendment_sha256"
    ] == predecessor.amendment_sha256
    assert sorted(successor.retry_by_index) == [2, 3, 4, 5]
    assert successor.checkpoint["counts"] == {
        "checkpoint_completion_receipts": 6,
        "preserved_terminal_rows": 2,
        "authorized_retry_rows": 4,
        "predecessor_infrastructure_rows": 1,
    }
    loaded = continuation.load_amendment(campaign)
    assert loaded is not None
    assert loaded.amendment_sha256 == successor.amendment_sha256


def _c2_identity(campaign: Path, predecessor, manifest: dict, row: dict):
    attestation = {
        "amendment_sha256": predecessor.amendment_sha256,
        "scheduler_sha256": predecessor.scheduler_sha256,
        "base_manifest_sha256": continuation._sha_file(
            campaign / "campaign_manifest.json"
        ),
        "source_inventory_sha256": predecessor.amendment["hash_chain"][
            "new"
        ]["source_inventory_sha256"],
        "limit_contract_sha256": predecessor.amendment["hash_chain"][
            "new"
        ]["limit_contract_sha256"],
    }
    nonce = (
        "eight_env_leaderboard/"
        f"{manifest['campaign_uuid']}/"
        f"{continuation._sha_file(campaign / 'campaign_manifest.json')}/"
        f"{row['run_id']}/attempt_1/"
        f"continuation-{predecessor.amendment_sha256}"
    )
    return attestation, nonce


def _write_c2_attempt(
    campaign: Path,
    predecessor,
    manifest: dict,
    row: dict,
    *,
    classification: dict,
    cleanup: dict,
    scratch: dict,
    returncode: int = 0,
    terminal: bool,
) -> tuple[Path, Path, Path]:
    index = row["index"]
    out_dir = campaign / "runs" / "attempt_1" / row["run_id"]
    out_dir.mkdir(parents=True)
    (out_dir / f"{row['env']}_{row['port']}.db").write_bytes(
        f"db-{index}".encode()
    )
    (out_dir / "run.log").write_text(f"run-{index}\n")
    (out_dir / "environment_server.log").write_text(f"env-{index}\n")
    if terminal:
        _write_json(out_dir / "summary.json", {"outcome": "hero"})
        _write_json(out_dir / "trajectory.json", {"steps": [{"n": 1}]})
    stem = f"{index:04d}_{row['cell_name']}.attempt1"
    launch_path = campaign / "launch_receipts" / f"{stem}.launch.json"
    completion_path = campaign / "completion_receipts" / f"{stem}.complete.json"
    scratch_path = campaign / "scratch_cleanup_receipts" / f"{stem}.scratch.json"
    attestation, nonce = _c2_identity(campaign, predecessor, manifest, row)
    _write_json(launch_path, {
        "schema": "agentarena.clone8-launch-receipt.v3",
        "campaign_uuid": manifest["campaign_uuid"],
        "manifest_sha256": continuation._sha_file(
            campaign / "campaign_manifest.json"
        ),
        "run_index": index,
        "run_id": row["run_id"],
        "attempt": 1,
        "pid": 5000 + index,
        "out_dir": str(out_dir.resolve()),
        "cache_nonce": nonce,
        "continuation_attestation": attestation,
    })
    _write_json(scratch_path, scratch)
    cleanup = dict(cleanup)
    cleanup["cache_nonce_sha256"] = continuation._sha_bytes(nonce.encode())
    _write_json(completion_path, {
        "schema": "agentarena.clone8-completion-receipt.v3",
        "run_index": index,
        "run_id": row["run_id"],
        "attempt": 1,
        "pid": 5000 + index,
        "returncode": returncode,
        "finished_utc": "2026-08-01T10:10:00Z",
        "classification": classification,
        "worker_scope_cleanup": cleanup,
        "no_checkout_proof": None,
        "scratch_cleanup_receipt": str(scratch_path.resolve()),
        "scratch_cleanup_receipt_sha256": continuation._sha_file(scratch_path),
        "scratch_cleanup": scratch,
    })
    return launch_path, completion_path, out_dir


def _green_postmortem(
    campaign: Path, manifest: dict, out_dir: Path, row: dict,
    launch: dict, completion: dict,
) -> dict:
    return {
        "schema": continuation.POSTMORTEM_PROOF_SCHEMA,
        "confirmed_recoverable": True,
        "operator_intervention": True,
        "benchmark_or_harness_changed": False,
        "inactivity_timeout_policy_created": False,
        "error": None,
        "no_checkout": {
            "confirmed_no_checkout": True,
            "hash_stable": True,
            "sidecars_absent": True,
            "error": None,
        },
        "process_absence": {
            "launch_identity_absent": True,
            "exact_nonce_pids": [],
            "worker_scope_snapshot": [],
            "confirmed_absent": True,
            "error": None,
        },
        "scratch_quarantine": {
            "policy": "retain_in_place_until_terminal_audit",
            "readable_holder_pids": [],
            "confirmed_quarantined": True,
            "error": None,
        },
    }


def _recovery_fixture(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(continuation, "RECOVERY_RUN_INDEX", 7)
    campaign, source, limits, scheduler = _fixture(
        tmp_path, extra_unlaunched_rows=3
    )
    scheduler = dict(scheduler)
    scheduler["scratch_policy"] = {
        "schema": "test-scratch.v1",
        "root": str(tmp_path / "scratch"),
    }
    c1 = continuation.create_amendment(
        campaign,
        continuation_source_inventory=source,
        continuation_limit_contract=limits,
        continuation_scheduler_contract=scheduler,
        checkout_proof=_proof,
        created_utc="2026-08-01T09:30:00Z",
    )
    manifest = json.loads((campaign / "campaign_manifest.json").read_text())
    row5 = manifest["runs"][5]
    zero_step = {
        "class": "infra",
        "code": "zero_step",
        "evidence": "steps=0; agent never received an observation",
        "outcome": "none",
        "steps": 0,
    }
    scratch_green = {
        "confirmed_absent": True,
        "worker_scope_green": True,
        "readable_holder_pids": [],
        "status": "removed",
        "error": None,
    }
    launch5, completion5, out5 = _write_c2_attempt(
        campaign, c1, manifest, row5,
        classification=zero_step,
        cleanup=_green_cleanup(),
        scratch=scratch_green,
        terminal=True,
    )
    _write_json(out5 / "summary.json", {"outcome": "none", "num_steps": 0})
    _write_json(out5 / "trajectory.json", {
        "steps": [], "evaluation": {"outcome": "none"},
        "stats": {"num_steps": 0},
    })
    c2 = continuation.create_successor_amendment(
        campaign,
        continuation_source_inventory=_recovery_source("c2"),
        continuation_limit_contract=_limit("c2"),
        continuation_scheduler_contract=scheduler,
        checkout_proof=_proof,
        created_utc="2026-08-01T10:00:00Z",
    )
    row6 = manifest["runs"][6]
    _write_c2_attempt(
        campaign, c2, manifest, row6,
        classification={"class": "scored", "code": "measured_transaction"},
        cleanup=_green_cleanup(),
        scratch=scratch_green,
        terminal=True,
    )
    row7 = manifest["runs"][7]
    failed_cleanup = _green_cleanup()
    failed_cleanup.update({
        "uncertified_scope_pids": [9007],
        "error": "numeric PGID/SID members lacked exact nonce certification",
    })
    scratch_retained = {
        "confirmed_absent": False,
        "worker_scope_green": False,
        "readable_holder_pids": None,
        "status": "skipped_worker_scope_not_green",
        "error": "worker scope was not proven empty; scratch retained",
    }
    launch7, completion7, out7 = _write_c2_attempt(
        campaign, c2, manifest, row7,
        classification={
            "class": "scientific_invalid",
            "code": "worker_scope_cleanup_failed",
            "evidence": "uncertified descendants",
        },
        cleanup=failed_cleanup,
        scratch=scratch_retained,
        returncode=-9,
        terminal=False,
    )
    return (
        campaign, scheduler, c2, manifest, launch7, completion7, out7
    )


def test_recovery_amendment_preserves_c2_frontier_and_adds_one_exact_retry(
    tmp_path: Path, monkeypatch,
) -> None:
    campaign, scheduler, c2, _manifest, _launch, completion, _out = (
        _recovery_fixture(tmp_path, monkeypatch)
    )
    base_manifest = json.loads(
        (campaign / "campaign_manifest.json").read_text()
    )
    assert base_manifest.get("scratch") is None
    observed_scratch = []

    def proof_with_active_scratch(*args):
        observed_scratch.append(args[1].get("scratch"))
        return _green_postmortem(*args)

    c3 = continuation.create_recovery_amendment(
        campaign,
        continuation_source_inventory=_recovery_source("c3"),
        continuation_limit_contract=_limit("c2"),
        continuation_scheduler_contract=scheduler,
        checkout_proof=_proof,
        postmortem_proof=proof_with_active_scratch,
        created_utc="2026-08-01T10:20:00Z",
    )
    assert c3.amendment["amendment_id"] == "continuation_003"
    assert c3.amendment["hash_chain"][
        "predecessor_amendment_sha256"
    ] == c2.amendment_sha256
    assert c3.checkpoint["counts"] == {
        "checkpoint_completion_receipts": 8,
        "preserved_terminal_rows": 3,
        "authorized_retry_rows": 5,
        "predecessor_retry_rows": 4,
        "recovery_rows": 1,
        "preserved_classes": {"behavioral": 1, "scored": 2},
    }
    assert sorted(c3.retry_by_index) == [2, 3, 4, 5, 7]
    assert c3.retry_by_index[7]["authorization_basis"] == (
        "postmortem_operator_sigkill"
    )
    assert continuation.authorized_retry_attempt(
        c3,
        run_index=7,
        run_id="env_r1/cell_7",
        prior_attempt=1,
        completion_receipt=completion,
    ) == 2
    assert c3.amendment["denominator"] == {
        "manifest_rows": 8,
        "row_identity": "campaign_manifest.runs[].index",
        "preserved_checkpoint_rows": 3,
        "superseded_attempts_retained_for_audit": 5,
        "added_rows": 0,
        "counting_rule": continuation.DENOMINATOR_COUNTING_RULE,
    }
    assert c3.amendment["recovery"] == {
        "cause": (
            "operator_initiated_sigkill_during_forensic_stall_investigation"
        ),
        "operator_intervention": True,
        "benchmark_or_harness_changed": False,
        "inactivity_timeout_policy_created": False,
        "measured_behavior_changed": False,
        "scratch_disposition": "retain_in_place_until_terminal_audit",
        "predecessor_retry_rows": 4,
        "recovery_run_indices": [7],
    }
    assert continuation.load_amendment(campaign).amendment_sha256 == (
        c3.amendment_sha256
    )
    assert observed_scratch
    assert all(
        value == c2.scheduler["scratch_policy"]
        for value in observed_scratch
    )


def test_recovery_amendment_rejects_non_green_postmortem(
    tmp_path: Path, monkeypatch,
) -> None:
    campaign, scheduler, _c2, *_ = _recovery_fixture(tmp_path, monkeypatch)

    def bad(*args):
        proof = _green_postmortem(*args)
        proof["confirmed_recoverable"] = False
        return proof

    with pytest.raises(
        continuation.ContinuationAmendmentError, match="postmortem proof"
    ):
        continuation.create_recovery_amendment(
            campaign,
            continuation_source_inventory=_recovery_source("c3"),
            continuation_limit_contract=_limit("c2"),
            continuation_scheduler_contract=scheduler,
            checkout_proof=_proof,
            postmortem_proof=bad,
            created_utc="2026-08-01T10:20:00Z",
        )
    assert not (
        campaign / continuation.AMENDMENT_ROOT
        / continuation.RECOVERY_AMENDMENT_DIRECTORY
    ).exists()


def test_recovery_amendment_rejects_predecessor_successor_attempt(
    tmp_path: Path, monkeypatch,
) -> None:
    campaign, scheduler, _c2, manifest, *_ = _recovery_fixture(
        tmp_path, monkeypatch
    )
    predecessor_row = manifest["runs"][2]
    (
        campaign / "runs" / "attempt_2" / predecessor_row["run_id"]
    ).mkdir(parents=True)
    with pytest.raises(
        continuation.ContinuationAmendmentError,
        match="predecessor-authorized successor attempt",
    ):
        continuation.create_recovery_amendment(
            campaign,
            continuation_source_inventory=_recovery_source("c3"),
            continuation_limit_contract=_limit("c2"),
            continuation_scheduler_contract=scheduler,
            checkout_proof=_proof,
            postmortem_proof=_green_postmortem,
            created_utc="2026-08-01T10:20:00Z",
        )


def test_recovery_receipt_mutation_fails_closed(
    tmp_path: Path, monkeypatch,
) -> None:
    campaign, scheduler, _c2, _manifest, _launch, completion, _out = (
        _recovery_fixture(tmp_path, monkeypatch)
    )
    continuation.create_recovery_amendment(
        campaign,
        continuation_source_inventory=_recovery_source("c3"),
        continuation_limit_contract=_limit("c2"),
        continuation_scheduler_contract=scheduler,
        checkout_proof=_proof,
        postmortem_proof=_green_postmortem,
        created_utc="2026-08-01T10:20:00Z",
    )
    completion.write_text(completion.read_text() + "\n")
    with pytest.raises(
        continuation.ContinuationAmendmentError, match="receipt drifted"
    ):
        continuation.load_amendment(campaign)


@pytest.mark.parametrize("drift", ["extra_key", "missing_allowed_change"])
def test_recovery_amendment_rejects_nonexact_source_delta(
    tmp_path: Path, monkeypatch, drift: str,
) -> None:
    campaign, scheduler, _c2, *_ = _recovery_fixture(tmp_path, monkeypatch)
    source = _recovery_source("c3")
    if drift == "extra_key":
        source["scripts/unexpected.py"] = {
            "sha256": continuation._sha_bytes(b"unexpected"),
            "size": len("unexpected"),
        }
        expected = "source inventory key set differs"
    else:
        path = next(iter(continuation.RECOVERY_ALLOWED_SOURCE_CHANGES))
        source[path] = _recovery_source("c2")[path]
        expected = "exact C3 implementation allowlist"

    with pytest.raises(continuation.ContinuationAmendmentError, match=expected):
        continuation.create_recovery_amendment(
            campaign,
            continuation_source_inventory=source,
            continuation_limit_contract=_limit("c2"),
            continuation_scheduler_contract=scheduler,
            checkout_proof=_proof,
            postmortem_proof=_green_postmortem,
            created_utc="2026-08-01T10:20:00Z",
        )


def test_recovery_amendment_rejects_limit_drift(
    tmp_path: Path, monkeypatch,
) -> None:
    campaign, scheduler, _c2, *_ = _recovery_fixture(tmp_path, monkeypatch)

    with pytest.raises(
        continuation.ContinuationAmendmentError, match="C2 limit contract"
    ):
        continuation.create_recovery_amendment(
            campaign,
            continuation_source_inventory=_recovery_source("c3"),
            continuation_limit_contract=_limit("drift"),
            continuation_scheduler_contract=scheduler,
            checkout_proof=_proof,
            postmortem_proof=_green_postmortem,
            created_utc="2026-08-01T10:20:00Z",
        )


def test_recovery_static_load_and_monitor_survive_authorized_attempt_two(
    tmp_path: Path, monkeypatch,
) -> None:
    campaign, scheduler, _c2, manifest, *_ = _recovery_fixture(
        tmp_path, monkeypatch
    )
    c3 = continuation.create_recovery_amendment(
        campaign,
        continuation_source_inventory=_recovery_source("c3"),
        continuation_limit_contract=_limit("c2"),
        continuation_scheduler_contract=scheduler,
        checkout_proof=_proof,
        postmortem_proof=_green_postmortem,
        created_utc="2026-08-01T10:20:00Z",
    )
    row = manifest["runs"][7]
    (campaign / "runs" / "attempt_2" / row["run_id"]).mkdir(
        parents=True
    )

    loaded = continuation.load_amendment(campaign)
    overlay = continuation.read_monitor_overlay(campaign)

    assert loaded is not None
    assert loaded.amendment_sha256 == c3.amendment_sha256
    assert overlay == {
        "amendment_sha256": c3.amendment_sha256,
        "scheduler_sha256": c3.scheduler_sha256,
        "active_limits": dict(c3.active_limits),
        "scratch_policy": c3.scheduler.get("scratch_policy"),
        "authorized_retry_indices": [2, 3, 4, 5, 7],
        "manifest_denominator": 8,
    }


def test_protocol_recovery_composes_corrections_and_one_exact_retry(
    tmp_path: Path, monkeypatch
) -> None:
    campaign, _manifest, predecessor, source, limits, scheduler = (
        _protocol_fixture(tmp_path, monkeypatch)
    )
    artifact_paths = sorted(
        path
        for root in ("runs", "launch_receipts", "completion_receipts")
        for path in (campaign / root).rglob("*")
        if path.is_file()
    )
    before = {path: continuation._sha_file(path) for path in artifact_paths}

    verified = continuation.create_protocol_recovery_amendment(
        campaign,
        continuation_source_inventory=source,
        continuation_limit_contract=limits,
        continuation_scheduler_contract=scheduler,
        verifier_correction_proof=_protocol_correction_proof,
        lossless_read_state_proof=_lossless_proof,
        created_utc="2026-08-03T10:00:00Z",
    )

    assert verified.amendment["hash_chain"][
        "predecessor_amendment_sha256"
    ] == predecessor.amendment_sha256
    assert sorted(verified.correction_by_index) == [1, 2, 4]
    assert sorted(verified.retry_by_index) == [3]
    assert verified.correction_by_index[1] == predecessor.correction_by_index[1]
    assert verified.correction_by_index[2] == predecessor.correction_by_index[2]
    assert verified.checkpoint["counts"] == {
        "checkpoint_completion_receipts": 5,
        "preserved_terminal_rows": 4,
        "authorized_retry_rows": 1,
        "verifier_corrected_rows": 3,
        "inherited_verifier_corrected_rows": 2,
        "fresh_verifier_corrected_rows": 1,
        "raw_recorded_classes": {"scientific_invalid": 4, "scored": 1},
        "effective_pre_recovery_classes": {
            "scientific_invalid": 2,
            "scored": 3,
        },
        "raw_invalid_indices": [1, 2, 3, 4],
        "effective_invalid_indices": [3, 4],
        "read_state_zero_preserved_rows": 4,
        "pending_primary_rows": 1,
    }
    assert verified.amendment["denominator"] == {
        "manifest_rows": 6,
        "row_identity": "campaign_manifest.runs[].index",
        "preserved_checkpoint_rows": 4,
        "promoted_attempts_retained_byte_identical": 3,
        "superseded_attempts_retained_for_audit": 1,
        "added_rows": 0,
        "counting_rule": continuation.PROTOCOL_RECOVERY_COUNTING_RULE,
    }
    for name in (
        "continuation_limit_contract.json",
        "continuation_scheduler_contract.json",
    ):
        assert verified.amendment["sidecars"][name] == (
            predecessor.amendment["sidecars"][name]
        )
    assert {path: continuation._sha_file(path) for path in artifact_paths} == before
    receipt = campaign / verified.retry_by_index[3]["completion_receipt"]
    assert continuation.authorized_retry_attempt(
        verified,
        run_index=3,
        run_id="instacart_r1/cell_3",
        prior_attempt=1,
        completion_receipt=receipt,
    ) == 2
    overlay = continuation.read_monitor_overlay(campaign)
    assert overlay["authorized_retry_indices"] == [3]
    assert overlay["verifier_corrected_indices"] == [1, 2, 4]
    assert continuation.load_amendment(campaign).amendment_sha256 == (
        verified.amendment_sha256
    )
    (campaign / "runs" / "attempt_2" / "instacart_r1" / "cell_3").mkdir(
        parents=True
    )
    assert continuation.load_amendment(campaign).amendment_sha256 == (
        verified.amendment_sha256
    )


def test_protocol_recovery_rejects_preserved_read_state_touch(
    tmp_path: Path, monkeypatch
) -> None:
    campaign, manifest, _predecessor, source, limits, scheduler = (
        _protocol_fixture(tmp_path, monkeypatch)
    )
    out_dir = campaign / "runs" / "attempt_1" / manifest["runs"][4]["run_id"]
    _write_json(out_dir / "trajectory.json", _protocol_trajectory(touched=True))
    with pytest.raises(
        continuation.ContinuationAmendmentError,
        match="preserved row touched",
    ):
        continuation.create_protocol_recovery_amendment(
            campaign,
            continuation_source_inventory=source,
            continuation_limit_contract=limits,
            continuation_scheduler_contract=scheduler,
            verifier_correction_proof=_protocol_correction_proof,
            lossless_read_state_proof=_lossless_proof,
            created_utc="2026-08-03T10:00:00Z",
        )
    assert not (
        campaign
        / continuation.AMENDMENT_ROOT
        / continuation.PROTOCOL_RECOVERY_AMENDMENT_DIRECTORY
    ).exists()


def test_protocol_recovery_rejects_preexisting_attempt_two(
    tmp_path: Path, monkeypatch
) -> None:
    campaign, manifest, _predecessor, source, limits, scheduler = (
        _protocol_fixture(tmp_path, monkeypatch)
    )
    target = campaign / "runs" / "attempt_2" / manifest["runs"][3]["run_id"]
    target.mkdir(parents=True)
    with pytest.raises(
        continuation.ContinuationAmendmentError,
        match="retry frontier is not exact",
    ):
        continuation.create_protocol_recovery_amendment(
            campaign,
            continuation_source_inventory=source,
            continuation_limit_contract=limits,
            continuation_scheduler_contract=scheduler,
            verifier_correction_proof=_protocol_correction_proof,
            lossless_read_state_proof=_lossless_proof,
            created_utc="2026-08-03T10:00:00Z",
        )


def test_protocol_recovery_rejects_source_or_limit_drift(
    tmp_path: Path, monkeypatch
) -> None:
    campaign, _manifest, _predecessor, source, limits, scheduler = (
        _protocol_fixture(tmp_path, monkeypatch)
    )
    missing_delta = dict(source)
    path = "agentarena/scaffolds/browseruse.py"
    predecessor_source = json.loads((
        campaign
        / continuation.AMENDMENT_ROOT
        / continuation.VERIFIER_CORRECTION_AMENDMENT_DIRECTORY
        / "continuation_source_inventory.json"
    ).read_text())
    missing_delta[path] = predecessor_source[path]
    with pytest.raises(
        continuation.ContinuationAmendmentError,
        match="exact six-file allowlist",
    ):
        continuation.create_protocol_recovery_amendment(
            campaign,
            continuation_source_inventory=missing_delta,
            continuation_limit_contract=limits,
            continuation_scheduler_contract=scheduler,
            verifier_correction_proof=_protocol_correction_proof,
            lossless_read_state_proof=_lossless_proof,
            created_utc="2026-08-03T10:00:00Z",
        )
    drifted_limits = json.loads(json.dumps(limits))
    drifted_limits["categories"]["launch_only"]["campaign_launch"][
        "source"
    ] = "drift"
    payload = {
        key: value for key, value in drifted_limits.items() if key != "sha256"
    }
    drifted_limits["sha256"] = continuation._sha_payload(payload)
    with pytest.raises(
        continuation.ContinuationAmendmentError,
        match="changed the predecessor limit contract",
    ):
        continuation.create_protocol_recovery_amendment(
            campaign,
            continuation_source_inventory=source,
            continuation_limit_contract=drifted_limits,
            continuation_scheduler_contract=scheduler,
            verifier_correction_proof=_protocol_correction_proof,
            lossless_read_state_proof=_lossless_proof,
            created_utc="2026-08-03T10:00:00Z",
        )


def test_protocol_recovery_static_load_fails_on_receipt_drift(
    tmp_path: Path, monkeypatch
) -> None:
    campaign, _manifest, _predecessor, source, limits, scheduler = (
        _protocol_fixture(tmp_path, monkeypatch)
    )
    verified = continuation.create_protocol_recovery_amendment(
        campaign,
        continuation_source_inventory=source,
        continuation_limit_contract=limits,
        continuation_scheduler_contract=scheduler,
        verifier_correction_proof=_protocol_correction_proof,
        lossless_read_state_proof=_lossless_proof,
        created_utc="2026-08-03T10:00:00Z",
    )
    receipt = campaign / verified.retry_by_index[3]["completion_receipt"]
    receipt.write_text(receipt.read_text() + "\n")
    with pytest.raises(
        continuation.ContinuationAmendmentError, match="drifted"
    ):
        continuation.load_amendment(campaign)


def test_protocol_postmortem_composes_existing_authority_and_one_retry(
    tmp_path: Path, monkeypatch
) -> None:
    (
        campaign, _manifest, predecessor, source, limits, scheduler,
        completion, _out_dir,
    ) = _protocol_postmortem_fixture(tmp_path, monkeypatch)
    artifact_paths = sorted(
        path
        for root in (
            "runs", "launch_receipts", "completion_receipts",
            "scratch_cleanup_receipts",
        )
        for path in (campaign / root).rglob("*")
        if path.is_file()
    )
    before = {path: continuation._sha_file(path) for path in artifact_paths}

    verified = continuation.create_protocol_postmortem_recovery_amendment(
        campaign,
        continuation_source_inventory=source,
        continuation_limit_contract=limits,
        continuation_scheduler_contract=scheduler,
        verifier_correction_proof=_protocol_correction_proof,
        lossless_read_state_proof=_lossless_proof,
        postmortem_proof=_protocol_postmortem_proof,
        created_utc="2026-08-03T12:00:00Z",
    )

    assert verified.amendment["hash_chain"][
        "predecessor_amendment_sha256"
    ] == predecessor.amendment_sha256
    assert sorted(verified.correction_by_index) == [1, 2, 4]
    assert sorted(verified.retry_by_index) == [3, 5]
    assert verified.retry_by_index[3] == predecessor.retry_by_index[3]
    assert verified.amendment["retry_authorization"] == {
        "from_attempt": 1,
        "to_attempt": 2,
        "run_indices": [3, 5],
        "inherited_run_indices": [3],
        "fresh_run_indices": [5],
        "completion_receipt_sha256": {
            "3": predecessor.retry_by_index[3]["completion_sha256"],
            "5": continuation._sha_file(completion),
        },
        "scope_by_run_index": {
            "3": "exact_hash_allowlist_lossless_read_state_only",
            "5": "exact_hash_allowlist_postmortem_no_checkout_only",
        },
    }
    assert verified.checkpoint["counts"]["raw_invalid_indices"] == [1, 2, 3, 4, 5]
    assert verified.checkpoint["counts"]["effective_invalid_indices"] == [3, 5]
    assert verified.checkpoint["counts"]["unresolved_invalid_indices"] == [5]
    assert verified.amendment["protocol_postmortem_recovery"][
        "exact_descendant_ancestry_claimed"
    ] is False
    assert verified.amendment["protocol_postmortem_recovery"][
        "inactivity_timeout_fired"
    ] is False
    assert verified.amendment["protocol_postmortem_recovery"][
        "preconfigured_timeout_did_not_fire"
    ] is True
    assert verified.amendment["denominator"]["counting_rule"] == (
        "each of the 960 immutable manifest run indices contributes exactly one "
        "selected terminal result; the three verifier-corrected attempts remain "
        "byte-identical, exact hash-bound runs 598 and 642 attempt 1 may each be "
        "superseded by attempt 2 or its terminal positive-infrastructure successor, "
        "and no row is added, removed, redrawn, or reweighted"
    )
    assert {path: continuation._sha_file(path) for path in artifact_paths} == before
    assert continuation.authorized_retry_attempt(
        verified,
        run_index=5,
        run_id="airbnb_r1/cell_5",
        prior_attempt=1,
        completion_receipt=completion,
    ) == 2
    overlay = continuation.read_monitor_overlay(campaign)
    assert overlay["authorized_retry_indices"] == [3, 5]
    assert overlay["verifier_corrected_indices"] == [1, 2, 4]
    assert continuation.load_amendment(campaign).amendment_sha256 == (
        verified.amendment_sha256
    )


def test_protocol_postmortem_rejects_proof_or_source_drift(
    tmp_path: Path, monkeypatch
) -> None:
    campaign, _manifest, _predecessor, source, limits, scheduler, *_ = (
        _protocol_postmortem_fixture(tmp_path, monkeypatch)
    )

    def bad_proof(*args):
        proof = _protocol_postmortem_proof(*args)
        proof.pop("trajectory_examined_before_intervention")
        return proof

    with pytest.raises(
        continuation.ContinuationAmendmentError,
        match="exact quiescent evidence",
    ):
        continuation.create_protocol_postmortem_recovery_amendment(
            campaign,
            continuation_source_inventory=source,
            continuation_limit_contract=limits,
            continuation_scheduler_contract=scheduler,
            verifier_correction_proof=_protocol_correction_proof,
            lossless_read_state_proof=_lossless_proof,
            postmortem_proof=bad_proof,
            created_utc="2026-08-03T12:00:00Z",
        )
    assert not (
        campaign / continuation.AMENDMENT_ROOT
        / continuation.PROTOCOL_POSTMORTEM_AMENDMENT_DIRECTORY
    ).exists()

    def biased_proof_without_disclosure(*args):
        proof = _protocol_postmortem_proof(*args)
        proof["selection_bias_risk"] = False
        return proof

    with pytest.raises(
        continuation.ContinuationAmendmentError,
        match="exact quiescent evidence",
    ):
        continuation.create_protocol_postmortem_recovery_amendment(
            campaign,
            continuation_source_inventory=source,
            continuation_limit_contract=limits,
            continuation_scheduler_contract=scheduler,
            verifier_correction_proof=_protocol_correction_proof,
            lossless_read_state_proof=_lossless_proof,
            postmortem_proof=biased_proof_without_disclosure,
            created_utc="2026-08-03T12:00:00Z",
        )

    drifted = json.loads(json.dumps(source))
    path = next(iter(continuation.PROTOCOL_POSTMORTEM_ALLOWED_SOURCE_CHANGES))
    predecessor_source = json.loads((
        campaign / continuation.AMENDMENT_ROOT
        / continuation.PROTOCOL_RECOVERY_AMENDMENT_DIRECTORY
        / "continuation_source_inventory.json"
    ).read_text())
    drifted[path] = predecessor_source[path]
    with pytest.raises(
        continuation.ContinuationAmendmentError,
        match="exact four-file allowlist",
    ):
        continuation.create_protocol_postmortem_recovery_amendment(
            campaign,
            continuation_source_inventory=drifted,
            continuation_limit_contract=limits,
            continuation_scheduler_contract=scheduler,
            verifier_correction_proof=_protocol_correction_proof,
            lossless_read_state_proof=_lossless_proof,
            postmortem_proof=_protocol_postmortem_proof,
            created_utc="2026-08-03T12:00:00Z",
        )


@pytest.mark.parametrize("attempt", [2, 6])
def test_protocol_postmortem_rejects_preexisting_successor_attempt(
    tmp_path: Path, monkeypatch, attempt: int
) -> None:
    campaign, manifest, _predecessor, source, limits, scheduler, *_ = (
        _protocol_postmortem_fixture(tmp_path, monkeypatch)
    )
    target = (
        campaign / "runs" / f"attempt_{attempt}" / manifest["runs"][5]["run_id"]
    )
    target.mkdir(parents=True)
    with pytest.raises(
        continuation.ContinuationAmendmentError,
        match="attempt 2 or later predates",
    ):
        continuation.create_protocol_postmortem_recovery_amendment(
            campaign,
            continuation_source_inventory=source,
            continuation_limit_contract=limits,
            continuation_scheduler_contract=scheduler,
            verifier_correction_proof=_protocol_correction_proof,
            lossless_read_state_proof=_lossless_proof,
            postmortem_proof=_protocol_postmortem_proof,
            created_utc="2026-08-03T12:00:00Z",
        )
