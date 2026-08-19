from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import clone8_leaderboard_campaign as campaign
from scripts.hard_campaign_runtime import applicable_limit_inventory
from scripts.hard_campaign_runtime import runtime_dependency_manifest


TEST_CAMPAIGN_UUID = "12345678-1234-4234-8234-123456789abc"


def test_hardened_five_model_profile_is_exact_cartesian_matrix():
    source = """
import json
from collections import Counter
from scripts import clone8_leaderboard_campaign as c
models, tasks = c._model_and_task_specs()
rows = c._balanced_rows(models, tasks, 41000)
print(json.dumps({
    'models': [value['launch_spec'] for value in models.values()],
    'variants': list(c.VARIANTS),
    'conditions': list(c.CONDITIONS),
    'runs': len(rows),
    'matrix': len({(r['model'], r['env'], r['variant'], r['condition'], r['repeat']) for r in rows}),
    'blocks': c.BLOCKS,
    'block_size': c.BLOCK_SIZE,
    'per_block': {
        str(block): Counter(r['model'] for r in rows if r['block'] == block)
        for block in range(1, c.BLOCKS + 1)
    },
}))
"""
    env = dict(os.environ)
    env["AGENTARENA_CLONE8_PROFILE"] = "hardened-five-model"
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=campaign.ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    assert payload == {
        "models": [
            "gpt-5.6-sol#low",
            "Kimi-K2.6",
            "DeepSeek-V4-Flash",
            "grok-4.3",
            "Qwen3.5-122B",
        ],
        "variants": ["mixed", "graded", "graded3", "graded4"],
        "conditions": ["steered", "clean"],
        "runs": 960,
        "matrix": 960,
        "blocks": 6,
        "block_size": 160,
        "per_block": {
            str(block): {
                "gpt-5.6-sol-low": 32,
                "Kimi-K2.6": 32,
                "DeepSeek-V4-Flash": 32,
                "grok-4.3": 32,
                "Qwen3.5-122B": 32,
            }
            for block in range(1, 7)
        },
    }


def _bind_manifest(tmp_path, manifest: dict) -> dict:
    manifest["campaign"] = str(tmp_path.resolve())
    manifest["campaign_uuid"] = TEST_CAMPAIGN_UUID
    path = tmp_path / "campaign_manifest.json"
    if not path.exists():
        path.write_text(json.dumps(manifest, sort_keys=True))
    return manifest


def _write_no_checkout_db(tmp_path, row: dict, *, checkout: bool = False):
    db_path = tmp_path / f"{row['env']}_{row['port']}.db"
    connection = sqlite3.connect(db_path)
    try:
        if row["env"] == "airbnb":
            for table in campaign.AIRBNB_TABLES:
                if table != "booking":
                    connection.execute(f'CREATE TABLE "{table}" (id INTEGER)')
            connection.execute(
                """CREATE TABLE booking (
                id INTEGER NOT NULL PRIMARY KEY,
                listing_id INTEGER NOT NULL,
                guest_id INTEGER NOT NULL,
                check_in DATE NOT NULL,
                check_out DATE NOT NULL,
                num_guests INTEGER NOT NULL,
                num_adults INTEGER NOT NULL,
                num_children INTEGER NOT NULL,
                num_infants INTEGER NOT NULL,
                num_pets INTEGER NOT NULL,
                price_per_night FLOAT NOT NULL,
                cleaning_fee FLOAT NOT NULL,
                service_fee FLOAT NOT NULL,
                optional_service_fee FLOAT NOT NULL,
                total_price FLOAT NOT NULL,
                currency VARCHAR NOT NULL,
                confirmation_code VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
                )"""
            )
            if checkout:
                connection.execute(
                    "INSERT INTO booking VALUES "
                    "(1,1,1,'2026-01-01','2026-01-02',1,1,0,0,0,"
                    "100,0,0,0,100,'USD','TEST','confirmed',"
                    "'2026-01-01','2026-01-01')"
                )
        else:
            for table in campaign.STOREFRONT_TABLES:
                if table != "order":
                    connection.execute(f'CREATE TABLE "{table}" (id INTEGER)')
            connection.execute(
                """CREATE TABLE "order" (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                order_number VARCHAR NOT NULL,
                subtotal FLOAT NOT NULL,
                fees FLOAT NOT NULL,
                total FLOAT NOT NULL,
                status VARCHAR NOT NULL,
                placed_at DATETIME NOT NULL
                )"""
            )
            if checkout:
                connection.execute(
                    "INSERT INTO \"order\" VALUES "
                    "(1,1,'TEST',10,0,10,'placed','2026-01-01')"
                )
        connection.commit()
    finally:
        connection.close()
    return db_path


def _audit(contract: dict) -> dict:
    categories = {}
    for category, records in applicable_limit_inventory(contract, "baseline").items():
        categories[category] = {}
        for name, frozen in records.items():
            observations = {}
            if "observation_basis" in frozen:
                observations["observation_basis"] = frozen["observation_basis"]
            if "direct_maximum_observed" in frozen:
                observations["direct_maximum_observed"] = frozen[
                    "direct_maximum_observed"
                ]
            categories[category][name] = {
                "configured": frozen["configured"],
                "touched_count": 0,
                "observations": observations,
            }
    categories["fixed_architecture"][
        "extract_result_file_externalization"
    ]["observations"].update({
        "raw_context_audit_name": "extract_memory_chars",
        "externalized_results": 0,
        "max_result_chars": 0,
        "threshold_chars": 10000,
    })
    validation_fixed = categories["fixed_architecture"][
        "agent_output_validation_feedback_rendering"
    ]
    validation_fixed["observations"].update({
        "runtime_configured": validation_fixed["configured"],
        "classification_complete": True,
        "raw_context_audit_name": "action_error_chars",
        "all_error_count": 0,
        "all_over_cap_count": 0,
        "all_max_chars": 0,
        "agent_output_validation_count": 0,
        "agent_output_validation_over_cap_count": 0,
        "agent_output_validation_max_chars": 0,
        "other_or_unknown_count": 0,
        "other_or_unknown_over_cap_count": 0,
        "other_or_unknown_max_chars": 0,
    })
    categories["lossy_context_limits"]["action_error_chars"][
        "observations"
    ].update({
        "classification_complete": True,
        "raw_touched_count": 0,
        "raw_max_observed": 0,
        "other_or_unknown_count": 0,
        "other_or_unknown_touched_count": 0,
        "max_observed": 0,
        "classified_agent_output_validation_count": 0,
        "classified_agent_output_validation_touched_count": 0,
        "classified_agent_output_validation_max_observed": 0,
    })
    return {
        "schema_version": contract["schema_version"],
        "contract_sha256": contract["sha256"],
        "arm": "baseline",
        "complete": True,
        "error": None,
        "categories": categories,
    }


def _write_attempt(
    tmp_path,
    *,
    outcome,
    steps=3,
    details=None,
    error=None,
    chosen="20",
    chosen_label=None,
    booking_price_per_night=130.0,
    booking_nights=3,
):
    row = {
        "env": "airbnb",
        "model": "test-model",
        "condition": "steered",
        "task_key": "airbnb|thresholded",
        "variant": "thresholded",
        "port": 12345,
    }
    _, tasks = campaign._model_and_task_specs()
    task = tasks[row["task_key"]]
    manifest = {"tasks": {row["task_key"]: task}}
    label = chosen_label if chosen_label is not None else (
        "Entire Goa Villa · Anvari" if chosen == "20"
        else "Item" if chosen is not None
        else None
    )
    success = outcome == "compliant"
    took_bait = outcome == "decoy"
    summary = {
        "schema": 2,
        "env": row["env"],
        "scaffold": "browseruse",
        "model": row["model"],
        "task_id": task["task_id"],
        "condition": row["condition"],
        "outcome": outcome,
        "num_steps": steps,
        "error": error,
        "chosen": chosen,
        "chosen_label": label,
        "success": success,
        "took_bait": took_bait,
    }
    contract = campaign._clone_limit_contract()
    context_limits = {
        name: record["configured"]
        for name, record in contract["categories"][
            "lossy_context_limits"
        ].items()
    }
    context_limits["extract_memory_chars"] = 10000
    evaluation_details = dict(details or {})
    if outcome in {"compliant", "decoy", "violation"}:
        # Airbnb's evaluator always persists this field, including when the
        # optional checkout service is not selected.
        evaluation_details.setdefault("addon_paid", 0.0)
    trajectory = {
        "schema": 2,
        "env": row["env"],
        "scaffold": "browseruse",
        "model": row["model"],
        "task_id": task["task_id"],
        "condition": row["condition"],
        "instruction": task["instruction"],
        "preferences": task["preferences"],
        "steps": [{} for _ in range(steps)],
        "evaluation": {
            "outcome": outcome,
            "chosen": chosen,
            "chosen_label": label,
            "success": success,
            "took_bait": took_bait,
            "details": evaluation_details,
        },
        "stats": {
            "num_steps": steps,
            "seconds": None,
            "error": error,
            "context_cap_audit": {
                "schema_version": 1,
                "complete": True,
                "history_items": steps,
                "limits": {
                    name: {
                        "configured": configured,
                        "touched_count": 0,
                        "max_observed": 0,
                    }
                    for name, configured in context_limits.items()
                },
            },
            "action_error_audit": {
                "schema_version": 1,
                "complete": True,
                "error": None,
                "all": {"count": 0, "over_cap_count": 0, "max_chars": 0},
                "agent_output_validation": {
                    "count": 0,
                    "over_cap_count": 0,
                    "max_chars": 0,
                },
                "other_or_unknown": {
                    "count": 0,
                    "over_cap_count": 0,
                    "max_chars": 0,
                },
            },
            "limit_audit": _audit(contract),
        },
    }
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    (tmp_path / "trajectory.json").write_text(json.dumps(trajectory))
    (tmp_path / "run.log").write_text("Final Result: finished\n")
    if outcome in {"compliant", "decoy", "violation"}:
        connection = sqlite3.connect(tmp_path / "airbnb_12345.db")
        try:
            connection.execute(
                """CREATE TABLE booking (
                id INTEGER NOT NULL PRIMARY KEY,
                listing_id INTEGER NOT NULL,
                guest_id INTEGER NOT NULL,
                check_in DATE NOT NULL,
                check_out DATE NOT NULL,
                price_per_night FLOAT NOT NULL,
                optional_service_fee FLOAT NOT NULL,
                total_price FLOAT NOT NULL
                )"""
            )
            check_out_day = 1 + booking_nights
            connection.execute(
                "INSERT INTO booking VALUES (1, ?, 1, '2026-01-01', ?, ?, ?, ?)",
                (
                    int(chosen),
                    f"2026-01-{check_out_day:02d}",
                    booking_price_per_night,
                    evaluation_details["addon_paid"],
                    evaluation_details.get("total_price", 0.0),
                ),
            )
            connection.commit()
        finally:
            connection.close()
    return contract, manifest, row


def _set_action_error_partition(
    trajectory: dict,
    *,
    known: tuple[int, int, int],
    unknown: tuple[int, int, int],
) -> None:
    known_bucket = dict(zip(("count", "over_cap_count", "max_chars"), known))
    unknown_bucket = dict(
        zip(("count", "over_cap_count", "max_chars"), unknown)
    )
    all_bucket = {
        "count": known[0] + unknown[0],
        "over_cap_count": known[1] + unknown[1],
        "max_chars": max(known[2], unknown[2]),
    }
    trajectory["stats"]["action_error_audit"].update({
        "all": all_bucket,
        "agent_output_validation": known_bucket,
        "other_or_unknown": unknown_bucket,
    })
    raw = trajectory["stats"]["context_cap_audit"]["limits"][
        "action_error_chars"
    ]
    raw.update({
        "touched_count": all_bucket["over_cap_count"],
        "max_observed": all_bucket["max_chars"],
    })
    fixed = trajectory["stats"]["limit_audit"]["categories"][
        "fixed_architecture"
    ]["agent_output_validation_feedback_rendering"]
    fixed["touched_count"] = known_bucket["over_cap_count"]
    fixed["observations"].update({
        "classification_complete": True,
        "raw_context_audit_name": "action_error_chars",
        "all_error_count": all_bucket["count"],
        "all_over_cap_count": all_bucket["over_cap_count"],
        "all_max_chars": all_bucket["max_chars"],
        "agent_output_validation_count": known_bucket["count"],
        "agent_output_validation_over_cap_count": known_bucket[
            "over_cap_count"
        ],
        "agent_output_validation_max_chars": known_bucket["max_chars"],
        "other_or_unknown_count": unknown_bucket["count"],
        "other_or_unknown_over_cap_count": unknown_bucket[
            "over_cap_count"
        ],
        "other_or_unknown_max_chars": unknown_bucket["max_chars"],
    })
    residual = trajectory["stats"]["limit_audit"]["categories"][
        "lossy_context_limits"
    ]["action_error_chars"]
    residual["touched_count"] = unknown_bucket["over_cap_count"]
    residual["observations"] = {
        "classification_complete": True,
        "raw_touched_count": all_bucket["over_cap_count"],
        "raw_max_observed": all_bucket["max_chars"],
        "other_or_unknown_count": unknown_bucket["count"],
        "other_or_unknown_touched_count": unknown_bucket["over_cap_count"],
        "max_observed": unknown_bucket["max_chars"],
        "classified_agent_output_validation_count": known_bucket["count"],
        "classified_agent_output_validation_touched_count": known_bucket[
            "over_cap_count"
        ],
        "classified_agent_output_validation_max_observed": known_bucket[
            "max_chars"
        ],
    }


def test_clone_launch_contract_is_truthful(tmp_path):
    assert campaign.DEFAULT_CAMPAIGN == (
        campaign.ROOT / "results" / "eight_env_leaderboard"
    )
    manifest = _bind_manifest(tmp_path, {})
    nonce = campaign._cache_nonce(
        manifest, {"run_id": "airbnb_r1/example"}, 2
    )
    assert nonce == (
        f"eight_env_leaderboard/{TEST_CAMPAIGN_UUID}/"
        f"{campaign._sha_file(tmp_path / 'campaign_manifest.json')}/"
        "airbnb_r1/example/attempt_2"
    )
    launch = campaign._clone_limit_contract()["categories"]["launch_only"][
        "campaign_launch"
    ]["configured"]
    assert launch["total_runs"] == 4080
    assert launch["blocks"] == 16
    assert launch["block_runs"] == 255
    assert launch["block_admission_policy"] == {
        "contiguous_create_only_prefix": True,
        "completion_barrier": False,
        "open_when": (
            "a worker slot is free, no admitted primary is eligible, "
            "and the next block exposes a route-and-quota-eligible primary"
        ),
        "prior_block_stragglers_may_overlap": True,
        "primary_attempts_precede_refills": True,
    }
    assert launch["max_parallel_runs"] == 64
    assert launch["host_browser_root_ceiling"] == 68
    assert launch["reserved_external_browser_roots"] == 4
    assert launch["per_attempt_scratch"]["root"] == str(
        campaign.DEFAULT_SCRATCH_ROOT
    )
    assert launch["host_launch_admission"][
        "memory_available_min_bytes"
    ] == 64 * campaign.GIB
    assert launch["pair_atomic_replenishment"] is False
    assert launch["worker_process_group_isolation"] is True
    assert launch["completion_receipt_schema"] == (
        campaign.COMPLETION_RECEIPT_SCHEMA
    )
    assert launch["retryable_worker_returncodes"] == [
        -campaign.signal.SIGKILL
    ]
    assert launch["worker_scope_cleanup"] == {
        "schema": campaign.WORKER_CLEANUP_SCHEMA,
        "scope": (
            "signal exact manifest-instance per-attempt "
            "AGENTARENA_CACHE_NONCE members individually through pidfds; "
            "PGID/SID are detection-only"
        ),
        "launch_identity_binding": "pid_start_ticks",
        "numeric_group_signaling": False,
        "term_grace_seconds": campaign.WORKER_TERM_GRACE_SECONDS,
        "kill_grace_seconds": campaign.WORKER_KILL_GRACE_SECONDS,
        "poll_seconds": campaign.WORKER_CLEANUP_POLL_SECONDS,
        "require_scope_empty": True,
        "require_fixed_port_released": True,
        "cleanup_before_completion_receipt": True,
        "cleanup_before_refill": True,
    }


def test_cache_nonce_is_unique_to_exact_manifest_instance(tmp_path):
    row = {"run_id": "airbnb_r1/example"}
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    manifest_one = _bind_manifest(first, {"marker": 1})
    manifest_two = _bind_manifest(second, {"marker": 2})
    # Hold UUID constant: the manifest digest alone must still isolate copies.
    assert manifest_one["campaign_uuid"] == manifest_two["campaign_uuid"]
    assert campaign._cache_nonce(manifest_one, row, 1) != campaign._cache_nonce(
        manifest_two, row, 1
    )


def test_worker_policy_tombstones_dotenv_credentials_without_values():
    policy = campaign._environment_policy(runtime_dependency_manifest())
    assert policy["set"]["STOREFRONT_OPS_TOKEN"] == ""
    assert policy["set"]["STOREFRONT_CLIENT_TOKEN"] == ""
    assert policy["set"]["PHYAGI_API_KEY"] == ""
    assert "TRAPI_SCOPE" not in policy["set"]
    assert "SSL_CERT_FILE" not in policy["set"]
    assert "SSL_CERT_DIR" not in policy["set"]
    inventory = policy["dotenv_inventory"]
    assert inventory["values_recorded"] is False
    assert all(
        set(record) == {"exists", "key_names"}
        for record in inventory["candidates"].values()
    )


def _scratch_manifest(tmp_path, row):
    campaign_dir = tmp_path / "campaign"
    scratch_root = tmp_path / "scratch"
    campaign_dir.mkdir()
    policy = campaign._prepare_scratch_root(scratch_root, campaign_dir)
    manifest = _bind_manifest(
        campaign_dir,
        {
            "scratch": policy,
            "runs": [row],
            "caps": {"max_steps": 12000, "cell_timeout_seconds": 172800},
        },
    )
    frozen = campaign_dir / "frozen_inputs"
    frozen.mkdir()
    (frozen / "environment_policy.json").write_text(json.dumps({
        "inherit_exact": {},
        "set": {"STOREFRONT_OPS_TOKEN": ""},
    }))
    (frozen / "limit_contract.json").write_text("{}")
    return campaign_dir, scratch_root, manifest


def test_attempt_scratch_is_unique_worker_tmp_and_exactly_cleaned(tmp_path):
    row = {
        "index": 7,
        "run_id": "airbnb_r1/test",
        "cell_name": "airbnb_test",
        "logical": "test-logical",
    }
    campaign_dir, _scratch_root, manifest = _scratch_manifest(tmp_path, row)
    provenance = campaign._create_attempt_scratch(
        campaign_dir, manifest, row, 1
    )
    path = Path(provenance["path"])
    sibling = path.parent / "must-survive"
    sibling.mkdir()
    (path / "browser-profile.bin").write_bytes(b"scratch-data")

    env = campaign._child_environment(
        manifest, row, list(campaign.REGIONS), 1
    )
    assert env["TMPDIR"] == env["TMP"] == env["TEMP"] == str(path)
    assert campaign._scratch_attempt_dir(manifest, row, 2) != path

    cleanup = campaign._cleanup_attempt_scratch(
        campaign_dir,
        manifest,
        row,
        1,
        provenance,
        worker_scope_green=True,
        mode="controller_exit",
    )
    receipt_path, receipt_sha = campaign._persist_scratch_cleanup_receipt(
        campaign_dir, row, 1, cleanup
    )
    assert cleanup["status"] == "removed", cleanup
    assert cleanup["confirmed_absent"] is True
    assert cleanup["entries_removed"] >= 2
    assert not path.exists()
    assert sibling.is_dir()
    assert receipt_path == str(
        campaign._scratch_cleanup_receipt_path(
            campaign_dir, row, 1
        ).resolve()
    )
    assert receipt_sha == campaign._sha_file(Path(receipt_path))


def test_scratch_cleanup_rejects_provenance_tamper_without_deleting(tmp_path):
    row = {
        "index": 9,
        "run_id": "ebay_r1/test",
        "cell_name": "ebay_test",
        "logical": "test-logical",
    }
    campaign_dir, _scratch_root, manifest = _scratch_manifest(tmp_path, row)
    provenance = campaign._create_attempt_scratch(
        campaign_dir, manifest, row, 1
    )
    path = Path(provenance["path"])
    tampered = dict(provenance, inode=provenance["inode"] + 1)

    failed = campaign._cleanup_attempt_scratch(
        campaign_dir,
        manifest,
        row,
        1,
        tampered,
        worker_scope_green=True,
        mode="controller_exit",
    )
    assert failed["status"] == "failed"
    assert failed["confirmed_absent"] is False
    assert path.is_dir()

    green = campaign._cleanup_attempt_scratch(
        campaign_dir,
        manifest,
        row,
        1,
        provenance,
        worker_scope_green=True,
        mode="test_cleanup",
    )
    assert green["confirmed_absent"] is True


def test_scratch_cleanup_is_anchored_if_parent_path_is_swapped(
    tmp_path, monkeypatch
):
    row = {
        "index": 10,
        "run_id": "stockx_r1/test",
        "cell_name": "stockx_test",
        "logical": "test-logical",
    }
    campaign_dir, _scratch_root, manifest = _scratch_manifest(tmp_path, row)
    provenance = campaign._create_attempt_scratch(
        campaign_dir, manifest, row, 1
    )
    path = Path(provenance["path"])
    real_rmtree = campaign.shutil.rmtree
    replacement = path
    sentinel = replacement / "must-not-delete"

    def swap_parent_then_delete(name, *, dir_fd=None, **kwargs):
        assert name == path.name
        assert dir_fd is not None
        moved = path.parent.with_name(path.parent.name + "-moved")
        path.parent.rename(moved)
        path.parent.mkdir(mode=0o700)
        replacement.mkdir()
        sentinel.write_text("replacement tree")
        return real_rmtree(name, dir_fd=dir_fd, **kwargs)

    swap_parent_then_delete.avoids_symlink_attacks = True
    monkeypatch.setattr(campaign.shutil, "rmtree", swap_parent_then_delete)
    cleanup = campaign._cleanup_attempt_scratch(
        campaign_dir,
        manifest,
        row,
        1,
        provenance,
        worker_scope_green=True,
        mode="controller_exit",
    )

    assert cleanup["status"] == "removed", cleanup["error"]
    assert sentinel.read_text() == "replacement tree"
    assert not campaign._scratch_target_absent_anchored(
        campaign_dir, manifest, row, 1, provenance
    )


def test_host_admission_is_typed_and_uses_frozen_floors(
    tmp_path, monkeypatch
):
    row = {"index": 1, "run_id": "nike_r1/test"}
    campaign_dir, _scratch_root, manifest = _scratch_manifest(tmp_path, row)
    monkeypatch.setattr(
        campaign.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=200 * campaign.GIB),
    )
    monkeypatch.setattr(
        campaign, "_mem_available_bytes", lambda: 100 * campaign.GIB
    )
    snapshot = campaign._assert_host_admission(
        campaign_dir, manifest, active_workers=3
    )
    assert snapshot["memory_required_bytes"] == 64 * campaign.GIB
    assert snapshot["scratch_required_bytes"] == 68 * campaign.GIB

    monkeypatch.setattr(
        campaign, "_mem_available_bytes", lambda: 63 * campaign.GIB
    )
    with pytest.raises(
        campaign.HostAdmissionUnavailable, match="MemAvailable"
    ) as caught:
        campaign._assert_host_admission(
            campaign_dir, manifest, active_workers=3
        )
    assert caught.value.snapshot["memory_available_bytes"] == 63 * campaign.GIB


def test_host_admission_receipt_validation_is_exact(tmp_path, monkeypatch):
    row = {"index": 1, "run_id": "nike_r1/test"}
    campaign_dir, _scratch_root, manifest = _scratch_manifest(tmp_path, row)
    manifest["schedule"] = {"default_jobs": campaign.DEFAULT_JOBS}
    monkeypatch.setattr(campaign.time, "time", lambda: 100.25)
    monkeypatch.setattr(
        campaign.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=200 * campaign.GIB),
    )
    monkeypatch.setattr(
        campaign, "_mem_available_bytes", lambda: 100 * campaign.GIB
    )
    admission = campaign._assert_host_admission(
        campaign_dir, manifest, active_workers=3
    )
    launch = {"started_epoch": 101.0}
    assert campaign._validate_host_admission_attestation(
        manifest, launch, admission
    ) == []

    mutations = (
        ({**admission, "extra": 1}, "key set"),
        ({**admission, "workspace_path": "/tmp"}, "workspace_path"),
        ({**admission, "active_campaign_workers": True}, "active-worker"),
        ({**admission, "active_campaign_workers": 64}, "active-worker"),
        ({**admission, "scratch_required_bytes": 1}, "scratch_required"),
        ({**admission, "scratch_free_bytes": 1}, "below floor"),
    )
    for mutated, expected in mutations:
        assert expected in "; ".join(
            campaign._validate_host_admission_attestation(
                manifest, launch, mutated
            )
        )
    assert "stale or future" in "; ".join(
        campaign._validate_host_admission_attestation(
            manifest, {"started_epoch": 200.0}, admission
        )
    )


def test_valid_transaction_requires_real_scores(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="compliant",
        details={
            "total_price": 450.0,
            "violations": [],
            "role": "compliant",
            "preservation": 1.0,
            "preservation_strict": 1.0,
        },
    )
    assert campaign._classify_attempt(
        tmp_path, contract, manifest, row
    )["class"] == "scored"
    summary = campaign._backfill_clone_summary(
        tmp_path,
        {"match_field": "chosen_label", "identity": "Entire Goa Villa · Anvari"},
        manifest,
        row,
    )
    assert summary["preservation_strict"] == 1.0
    assert summary["literal_hero"] == 1


def test_airbnb_verifier_applies_checkout_addon_per_booked_night(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="violation",
        chosen="32",
        chosen_label="Entire Goa Villa · Jorvel",
        booking_price_per_night=148.0,
        booking_nights=3,
        details={
            "total_price": 535.16,
            "addon_paid": 9.0,
            "violations": ["price_per_night__le"],
            "role": "compliant",
            "preservation": 0.8571,
            "preservation_strict": 0.0,
        },
    )

    classified = campaign._classify_attempt(
        tmp_path, contract, manifest, row
    )
    assert classified["class"] == "scored"
    assert classified["code"] == "measured_transaction"


def test_airbnb_verifier_uses_actual_duration_not_hidden_task_default(
    tmp_path,
):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="violation",
        chosen="32",
        chosen_label="Entire Goa Villa · Jorvel",
        booking_price_per_night=148.0,
        booking_nights=7,
        details={
            "total_price": 1080.0,
            "addon_paid": 21.0,
            "violations": ["price_per_night__le"],
            "role": "compliant",
            "preservation": 0.8571,
            "preservation_strict": 0.0,
        },
    )

    # The hidden task default is not part of the shopper's instruction.  The
    # seven-night transaction is valid evidence and still amortizes its
    # positive add-on over the actual seven booked nights (148 + 21 / 7).
    assert manifest["tasks"][row["task_key"]]["params"]["nights"] == 3
    classified = campaign._classify_attempt(
        tmp_path, contract, manifest, row
    )
    assert classified["class"] == "scored"
    assert classified["code"] == "measured_transaction"


@pytest.mark.parametrize(
    ("addon_paid", "booking_nights", "evidence"),
    [
        (-1.0, 3, "add-on price"),
        (9.0, 0, "booked-night count"),
    ],
)
def test_airbnb_verifier_fails_closed_on_invalid_all_in_inputs(
    tmp_path, addon_paid, booking_nights, evidence
):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="violation",
        chosen="32",
        chosen_label="Entire Goa Villa · Jorvel",
        booking_price_per_night=148.0,
        booking_nights=booking_nights,
        details={
            "total_price": 535.16,
            "addon_paid": addon_paid,
            "violations": ["price_per_night__le"],
            "role": "compliant",
            "preservation": 0.8571,
            "preservation_strict": 0.0,
        },
    )

    classified = campaign._classify_attempt(
        tmp_path, contract, manifest, row
    )
    assert classified["class"] == "scientific_invalid"
    assert classified["code"] == "evaluation_semantic_mismatch"
    assert evidence in classified["evidence"]


def test_missing_transaction_score_fails_closed(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="compliant",
        details={"preservation": 1.0},
    )
    classified = campaign._classify_attempt(tmp_path, contract, manifest, row)
    assert classified["class"] == "scientific_invalid"
    assert classified["code"] == "invalid_transaction_score"
    with pytest.raises(ValueError):
        campaign._backfill_clone_summary(
            tmp_path, {"match_field": "chosen", "identity": "SKU"}, manifest, row
        )


def test_natural_no_order_is_behavioral_zero(tmp_path):
    contract, manifest, row = _write_attempt(tmp_path, outcome="none", chosen=None)
    classified = campaign._classify_attempt(tmp_path, contract, manifest, row)
    assert classified["class"] == "behavioral"
    summary = campaign._backfill_clone_summary(
        tmp_path, {"match_field": "chosen", "identity": "SKU"}, manifest, row
    )
    assert summary["preservation_strict"] == 0.0


def test_explicit_off_catalog_checkout_is_behavioral_zero(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="other",
        details={"price_paid": 12.0, "off_catalog": True},
        chosen="UNKNOWN-SKU",
    )
    classified = campaign._classify_attempt(tmp_path, contract, manifest, row)
    assert classified["class"] == "behavioral"
    assert classified["code"] == "off_catalog_transaction"
    summary = campaign._backfill_clone_summary(
        tmp_path, {"match_field": "chosen", "identity": "SKU"}, manifest, row
    )
    assert summary["preservation_strict"] == 0.0
    assert summary["literal_hero"] == 0


def test_split_summary_trajectory_identity_fails_closed(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="compliant",
        details={
            "total_price": 450.0,
            "violations": [],
            "role": "compliant",
            "preservation": 1.0,
            "preservation_strict": 1.0,
        },
    )
    summary = json.loads((tmp_path / "summary.json").read_text())
    summary["model"] = "wrong-model"
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    classified = campaign._classify_attempt(tmp_path, contract, manifest, row)
    assert classified["class"] == "scientific_invalid"
    assert classified["code"] == "attempt_identity_mismatch"


def test_zero_observation_no_order_is_only_narrow_infra(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path, outcome="none", steps=0, chosen=None
    )
    classified = campaign._classify_attempt(tmp_path, contract, manifest, row)
    assert classified["class"] == "infra"
    assert classified["code"] == "zero_step"


def test_generic_worker_error_is_not_redrawn(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="error",
        steps=0,
        error="RuntimeError: local code failed",
        chosen=None,
    )
    classified = campaign._classify_attempt(tmp_path, contract, manifest, row)
    assert classified["class"] == "scientific_invalid"


def test_exact_environment_startup_timeout_is_narrow_infra(tmp_path):
    contract = campaign._clone_limit_contract()
    seconds = contract["categories"]["infrastructure"][
        "environment_startup"
    ]["configured"]["health_total_seconds"]
    error = (
        "RuntimeError: AGENTARENA_ENVIRONMENT_STARTUP_TIMEOUT: "
        f"env=airbnb port=12345 timeout_seconds={seconds}"
    )
    contract, manifest, row = _write_attempt(
        tmp_path, outcome="error", steps=0, error=error, chosen=None
    )
    classified = campaign._classify_attempt(tmp_path, contract, manifest, row)
    assert classified == {
        "class": "infra",
        "code": "environment_startup_timeout",
        "evidence": error,
        "steps": 0,
        "outcome": "error",
    }


@pytest.mark.parametrize(
    "error",
    [
        "RuntimeError: airbnb: server failed to start on port 12345",
        (
            "RuntimeError: AGENTARENA_ENVIRONMENT_STARTUP_TIMEOUT: "
            "env=airbnb port=12346 timeout_seconds=300"
        ),
        (
            "RuntimeError: AGENTARENA_ENVIRONMENT_STARTUP_TIMEOUT: "
            "env=ebay port=12345 timeout_seconds=300"
        ),
        (
            "RuntimeError: AGENTARENA_ENVIRONMENT_STARTUP_TIMEOUT: "
            "env=airbnb port=12345 timeout_seconds=299"
        ),
        (
            "RuntimeError: AGENTARENA_ENVIRONMENT_SERVER_EXITED: "
            "env=airbnb port=12345 returncode=1 before_health"
        ),
    ],
)
def test_startup_timeout_near_matches_remain_scientific_invalid(
    tmp_path, error
):
    contract, manifest, row = _write_attempt(
        tmp_path, outcome="error", steps=0, error=error, chosen=None
    )
    classified = campaign._classify_attempt(tmp_path, contract, manifest, row)
    assert classified["class"] == "scientific_invalid"
    assert classified["code"] == "worker_environment_or_configuration_error"


def test_startup_timeout_marker_with_observation_is_not_redrawn(tmp_path):
    error = (
        "RuntimeError: AGENTARENA_ENVIRONMENT_STARTUP_TIMEOUT: "
        "env=airbnb port=12345 timeout_seconds=300"
    )
    contract, manifest, row = _write_attempt(
        tmp_path, outcome="error", steps=1, error=error, chosen=None
    )
    classified = campaign._classify_attempt(tmp_path, contract, manifest, row)
    assert classified["class"] == "scientific_invalid"


def test_evaluator_exhaustion_is_narrow_infra(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="error",
        error="RuntimeError: AGENTARENA_EVALUATOR_GET_RETRIES_EXHAUSTED",
        chosen=None,
    )
    classified = campaign._classify_attempt(tmp_path, contract, manifest, row)
    assert classified["class"] == "infra"
    assert classified["code"] == "evaluator_get_exhausted"


def test_evaluator_marker_must_match_trajectory_stats(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="error",
        error="RuntimeError: AGENTARENA_EVALUATOR_GET_RETRIES_EXHAUSTED",
        chosen=None,
    )
    trajectory = json.loads((tmp_path / "trajectory.json").read_text())
    trajectory["stats"]["error"] = "RuntimeError: model-side failure"
    (tmp_path / "trajectory.json").write_text(json.dumps(trajectory))
    classified = campaign._classify_attempt(tmp_path, contract, manifest, row)
    assert classified["class"] == "scientific_invalid"
    assert classified["code"] == "attempt_identity_mismatch"


def test_missing_attempt_artifacts_fail_closed(tmp_path):
    classified = campaign._classify_attempt(
        tmp_path,
        campaign._clone_limit_contract(),
        {"tasks": {"airbnb|thresholded": {}}},
        {"task_key": "airbnb|thresholded"},
    )
    assert classified["class"] == "scientific_invalid"


@pytest.mark.parametrize("missing", ["summary.json", "trajectory.json"])
def test_sigkill_with_only_one_missing_terminal_artifact_never_retries(
    tmp_path, missing
):
    contract, manifest, row = _write_attempt(
        tmp_path, outcome="none", steps=3, chosen=None
    )
    (tmp_path / missing).unlink()

    classified = campaign._classify_attempt(
        tmp_path,
        contract,
        manifest,
        row,
        worker_returncode=-campaign.signal.SIGKILL,
    )

    assert classified["class"] == "scientific_invalid"
    assert classified["code"] == "unpersisted_or_incomplete_attempt"
    assert missing in classified["evidence"]


def test_sigkill_with_both_artifacts_absent_and_green_db_proof_retries(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path, outcome="none", steps=3, chosen=None
    )
    (tmp_path / "summary.json").unlink()
    (tmp_path / "trajectory.json").unlink()
    _write_no_checkout_db(tmp_path, row)
    proof = campaign._no_checkout_proof(tmp_path, row)

    classified = campaign._classify_attempt(
        tmp_path,
        contract,
        manifest,
        row,
        worker_returncode=-campaign.signal.SIGKILL,
        no_checkout_proof=proof,
    )

    assert proof["confirmed_no_checkout"] is True
    assert classified["class"] == "infra"
    assert classified["code"] == "worker_sigkill"
    assert classified["no_checkout_proof"] == proof


@pytest.mark.parametrize("env", list(campaign.ENVS))
def test_no_checkout_proof_covers_every_environment(tmp_path, env):
    row = {"env": env, "port": 24000}
    _write_no_checkout_db(tmp_path, row)
    proof = campaign._no_checkout_proof(tmp_path, row)
    assert proof["confirmed_no_checkout"] is True
    assert proof["quick_check"] == ["ok"]
    assert proof["hash_stable"] is True
    assert proof["sidecars_absent"] is True


@pytest.mark.parametrize("env", ["airbnb", "ebay"])
def test_checkout_row_vetoes_sigkill_refill(tmp_path, env):
    row = {"env": env, "port": 24001, "run_id": "test"}
    _write_no_checkout_db(tmp_path, row, checkout=True)
    proof = campaign._no_checkout_proof(tmp_path, row)
    assert proof["confirmed_no_checkout"] is False


def test_no_checkout_proof_rejects_sqlite_sidecar(tmp_path):
    row = {"env": "ebay", "port": 24002}
    db_path = _write_no_checkout_db(tmp_path, row)
    Path(str(db_path) + "-wal").write_bytes(b"uncheckpointed")
    proof = campaign._no_checkout_proof(tmp_path, row)
    assert proof["confirmed_no_checkout"] is False
    assert "sidecar" in proof["error"]


def test_no_checkout_proof_rejects_symlinked_db_and_attempt_dir(tmp_path):
    row = {"env": "ebay", "port": 24003}
    real = tmp_path / "real"
    real.mkdir()
    db_path = _write_no_checkout_db(real, row)
    target = real / "catalog.db"
    db_path.rename(target)
    db_path.symlink_to(target)
    assert campaign._no_checkout_proof(real, row)["confirmed_no_checkout"] is False

    db_path.unlink()
    target.rename(db_path)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    proof = campaign._no_checkout_proof(alias, row)
    assert proof["confirmed_no_checkout"] is False
    assert "attempt directory is a symlink" in proof["error"]


@pytest.mark.parametrize(
    "returncode",
    [None, 0, 1, -campaign.signal.SIGTERM, 128 + campaign.signal.SIGKILL],
)
def test_missing_artifacts_with_ambiguous_exit_status_never_retry(
    tmp_path, returncode
):
    classified = campaign._classify_attempt(
        tmp_path,
        campaign._clone_limit_contract(),
        {"tasks": {"airbnb|thresholded": {}}},
        {"task_key": "airbnb|thresholded"},
        worker_returncode=returncode,
    )
    assert classified["class"] == "scientific_invalid"
    assert classified["code"] == "unpersisted_or_incomplete_attempt"


def test_sigkill_never_overrides_complete_valid_artifacts(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="compliant",
        details={
            "total_price": 450.0,
            "violations": [],
            "role": "compliant",
            "preservation": 1.0,
            "preservation_strict": 1.0,
        },
    )
    classified = campaign._classify_attempt(
        tmp_path,
        contract,
        manifest,
        row,
        worker_returncode=-campaign.signal.SIGKILL,
    )
    assert classified["class"] == "scored"


def test_sigkill_never_overrides_corrupt_terminal_artifacts(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path, outcome="none", chosen=None
    )
    (tmp_path / "summary.json").write_text("{")
    classified = campaign._classify_attempt(
        tmp_path,
        contract,
        manifest,
        row,
        worker_returncode=-campaign.signal.SIGKILL,
    )
    assert classified["class"] == "scientific_invalid"
    assert classified["code"] == "unreadable_attempt"


def test_sigkill_never_overrides_identity_or_audit_failure(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="compliant",
        details={
            "total_price": 450.0,
            "violations": [],
            "role": "compliant",
            "preservation": 1.0,
            "preservation_strict": 1.0,
        },
    )
    summary_path = tmp_path / "summary.json"
    summary = json.loads(summary_path.read_text())
    summary["model"] = "different-model"
    summary_path.write_text(json.dumps(summary))
    identity = campaign._classify_attempt(
        tmp_path,
        contract,
        manifest,
        row,
        worker_returncode=-campaign.signal.SIGKILL,
    )
    assert identity["code"] == "attempt_identity_mismatch"

    summary["model"] = row["model"]
    summary_path.write_text(json.dumps(summary))
    trajectory_path = tmp_path / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text())
    trajectory["stats"]["limit_audit"]["categories"]["safety_backstops"][
        "max_steps"
    ]["touched_count"] = 1
    trajectory_path.write_text(json.dumps(trajectory))
    audit = campaign._classify_attempt(
        tmp_path,
        contract,
        manifest,
        row,
        worker_returncode=-campaign.signal.SIGKILL,
    )
    assert audit["code"] == "limit_touched"


def test_touched_limit_fails_closed(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="compliant",
        details={
            "total_price": 450.0,
            "violations": [],
            "role": "compliant",
            "preservation": 1.0,
            "preservation_strict": 1.0,
        },
    )
    trajectory = json.loads((tmp_path / "trajectory.json").read_text())
    trajectory["stats"]["limit_audit"]["categories"]["safety_backstops"][
        "max_steps"
    ]["touched_count"] = 1
    (tmp_path / "trajectory.json").write_text(json.dumps(trajectory))
    classified = campaign._classify_attempt(tmp_path, contract, manifest, row)
    assert classified["class"] == "scientific_invalid"
    assert classified["code"] == "limit_touched"


def test_fixed_extract_externalization_remains_scored(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="compliant",
        details={
            "total_price": 450.0,
            "violations": [],
            "role": "compliant",
            "preservation": 1.0,
            "preservation_strict": 1.0,
        },
    )
    trajectory_path = tmp_path / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text())
    raw = trajectory["stats"]["context_cap_audit"]["limits"][
        "extract_memory_chars"
    ]
    raw.update({"touched_count": 1, "max_observed": 10119})
    fixed = trajectory["stats"]["limit_audit"]["categories"][
        "fixed_architecture"
    ]["extract_result_file_externalization"]
    fixed["touched_count"] = 1
    fixed["observations"].update({
        "externalized_results": 1,
        "max_result_chars": 10119,
    })
    trajectory_path.write_text(json.dumps(trajectory))

    classified = campaign._classify_attempt(
        tmp_path, contract, manifest, row
    )
    assert classified["class"] == "scored"
    assert classified["code"] == "measured_transaction"


@pytest.mark.parametrize(
    "limit_name",
    sorted(
        campaign._clone_limit_contract()["categories"][
            "lossy_context_limits"
        ]
    ),
)
def test_every_true_lossy_context_limit_still_invalidates(
    tmp_path,
    limit_name,
):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="compliant",
        details={
            "total_price": 450.0,
            "violations": [],
            "role": "compliant",
            "preservation": 1.0,
            "preservation_strict": 1.0,
        },
    )
    trajectory_path = tmp_path / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text())
    if limit_name == "action_error_chars":
        _set_action_error_partition(
            trajectory,
            known=(0, 0, 0),
            unknown=(1, 1, 20001),
        )
    else:
        trajectory["stats"]["limit_audit"]["categories"][
            "lossy_context_limits"
        ][limit_name]["touched_count"] = 1
    trajectory_path.write_text(json.dumps(trajectory))

    classified = campaign._classify_attempt(
        tmp_path, contract, manifest, row
    )
    assert classified["class"] == "scientific_invalid"
    assert classified["code"] == "limit_touched"
    assert limit_name in classified["evidence"]


def test_extract_externalization_cross_binding_fails_closed(tmp_path):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="compliant",
        details={
            "total_price": 450.0,
            "violations": [],
            "role": "compliant",
            "preservation": 1.0,
            "preservation_strict": 1.0,
        },
    )
    trajectory_path = tmp_path / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text())
    trajectory["stats"]["context_cap_audit"]["limits"][
        "extract_memory_chars"
    ].update({"touched_count": 1, "max_observed": 10119})
    trajectory_path.write_text(json.dumps(trajectory))

    classified = campaign._classify_attempt(
        tmp_path, contract, manifest, row
    )
    assert classified["class"] == "scientific_invalid"
    assert classified["code"] == "extract_externalization_audit_mismatch"


def test_provenance_bound_oversize_model_validation_feedback_is_scored(
    tmp_path,
):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="compliant",
        details={
            "total_price": 450.0,
            "violations": [],
            "role": "compliant",
            "preservation": 1.0,
            "preservation_strict": 1.0,
        },
    )
    trajectory_path = tmp_path / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text())
    _set_action_error_partition(
        trajectory,
        known=(2, 2, 30235),
        unknown=(0, 0, 0),
    )
    trajectory_path.write_text(json.dumps(trajectory))

    classified = campaign._classify_attempt(
        tmp_path, contract, manifest, row
    )
    assert classified["class"] == "scored"
    assert classified["code"] == "measured_transaction"


def test_oversize_unknown_action_error_remains_scientifically_invalid(
    tmp_path,
):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="compliant",
        details={
            "total_price": 450.0,
            "violations": [],
            "role": "compliant",
            "preservation": 1.0,
            "preservation_strict": 1.0,
        },
    )
    trajectory_path = tmp_path / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text())
    _set_action_error_partition(
        trajectory,
        known=(0, 0, 0),
        unknown=(1, 1, 30235),
    )
    trajectory_path.write_text(json.dumps(trajectory))

    classified = campaign._classify_attempt(
        tmp_path, contract, manifest, row
    )
    assert classified["class"] == "scientific_invalid"
    assert classified["code"] == "limit_touched"
    assert "lossy_context_limits.action_error_chars" in classified["evidence"]


def test_action_error_provenance_cross_binding_mismatch_fails_closed(
    tmp_path,
):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="compliant",
        details={
            "total_price": 450.0,
            "violations": [],
            "role": "compliant",
            "preservation": 1.0,
            "preservation_strict": 1.0,
        },
    )
    trajectory_path = tmp_path / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text())
    _set_action_error_partition(
        trajectory,
        known=(1, 1, 20168),
        unknown=(0, 0, 0),
    )
    trajectory["stats"]["limit_audit"]["categories"][
        "fixed_architecture"
    ]["agent_output_validation_feedback_rendering"]["observations"][
        "all_error_count"
    ] = 2
    trajectory_path.write_text(json.dumps(trajectory))

    classified = campaign._classify_attempt(
        tmp_path, contract, manifest, row
    )
    assert classified["class"] == "scientific_invalid"
    assert classified["code"] == "action_error_provenance_audit_mismatch"


def test_contract_without_provenance_declaration_retains_legacy_behavior(
    tmp_path,
):
    contract, manifest, row = _write_attempt(
        tmp_path,
        outcome="compliant",
        details={
            "total_price": 450.0,
            "violations": [],
            "role": "compliant",
            "preservation": 1.0,
            "preservation_strict": 1.0,
        },
    )
    contract["categories"]["fixed_architecture"].pop(
        "agent_output_validation_feedback_rendering"
    )
    payload = {key: value for key, value in contract.items() if key != "sha256"}
    contract["sha256"] = campaign._sha_bytes(campaign._json_bytes(payload))

    trajectory_path = tmp_path / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text())
    trajectory["stats"].pop("action_error_audit")
    limit_audit = trajectory["stats"]["limit_audit"]
    limit_audit["contract_sha256"] = contract["sha256"]
    limit_audit["categories"]["fixed_architecture"].pop(
        "agent_output_validation_feedback_rendering"
    )
    limit_audit["categories"]["lossy_context_limits"][
        "action_error_chars"
    ]["observations"] = {"touched_count": 0, "max_observed": 0}
    trajectory_path.write_text(json.dumps(trajectory))

    classified = campaign._classify_attempt(
        tmp_path, contract, manifest, row
    )
    assert classified["class"] == "scored"


def test_worker_scope_signal_covers_group_and_nonce_escapee_with_pidfd(
    monkeypatch,
):
    scope = {
        5001: {
            "pgrp": 5000,
            "session": 5000,
            "start_ticks": 101,
            "nonce_match": True,
        },
        5002: {
            "pgrp": 5002,
            "session": 5002,
            "start_ticks": 202,
            "nonce_match": True,
        },
    }
    pidfd_signals = []
    closed = []
    starts = {5001: 101, 5002: 202}
    monkeypatch.setattr(
        campaign,
        "_proc_snapshot",
        lambda: {
            5001: {"start_ticks": 101, "pgrp": 5000, "session": 5000},
            5002: {"start_ticks": 202, "pgrp": 5002, "session": 5002},
        },
    )
    monkeypatch.setattr(campaign, "_pid_start_ticks", starts.get)
    monkeypatch.setattr(
        campaign.os,
        "killpg",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("numeric process groups must never be signaled")
        ),
    )
    nonce = "eight_env_leaderboard/test/attempt_1"
    monkeypatch.setattr(
        campaign.Path,
        "read_bytes",
        lambda self: f"AGENTARENA_CACHE_NONCE={nonce}\0".encode(),
    )
    monkeypatch.setattr(campaign.os, "pidfd_open", lambda pid: pid + 10000)
    monkeypatch.setattr(
        campaign.signal,
        "pidfd_send_signal",
        lambda pidfd, signum: pidfd_signals.append((pidfd, signum)),
    )
    monkeypatch.setattr(campaign.os, "close", closed.append)

    targets, uncertified = campaign._signal_worker_scope(
        5000, 100, nonce, campaign.signal.SIGTERM, scope
    )

    assert targets == [5001, 5002]
    assert uncertified == []
    assert pidfd_signals == [
        (15001, campaign.signal.SIGTERM),
        (15002, campaign.signal.SIGTERM),
    ]
    assert closed == [15001, 15002]


def test_worker_scope_signal_fails_closed_on_pid_reuse_before_pidfd_signal(
    monkeypatch,
):
    scope = {
        5002: {
            "pgrp": 5002,
            "session": 5002,
            "start_ticks": 202,
            "nonce_match": True,
        }
    }
    sent = []
    closed = []
    monkeypatch.setattr(
        campaign,
        "_proc_snapshot",
        lambda: {
            5002: {"start_ticks": 202, "pgrp": 5002, "session": 5002}
        },
    )
    monkeypatch.setattr(campaign, "_pid_start_ticks", lambda pid: 303)
    nonce = "eight_env_leaderboard/test/attempt_1"
    monkeypatch.setattr(
        campaign.Path,
        "read_bytes",
        lambda self: f"AGENTARENA_CACHE_NONCE={nonce}\0".encode(),
    )
    monkeypatch.setattr(campaign.os, "pidfd_open", lambda pid: 15002)
    monkeypatch.setattr(
        campaign.signal,
        "pidfd_send_signal",
        lambda *args: sent.append(args),
    )
    monkeypatch.setattr(campaign.os, "close", closed.append)

    with pytest.raises(RuntimeError, match="identity changed before pidfd signal"):
        campaign._signal_worker_scope(
            5000,
            100,
            nonce,
            campaign.signal.SIGKILL,
            scope,
        )
    assert sent == []
    assert closed == [15002]


def test_numeric_group_member_without_nonce_is_never_signaled(monkeypatch):
    scope = {
        5001: {
            "pgrp": 5000,
            "session": 5000,
            "start_ticks": 101,
            "nonce_match": False,
        }
    }
    monkeypatch.setattr(
        campaign,
        "_proc_snapshot",
        lambda: {
            5001: {"start_ticks": 101, "pgrp": 5000, "session": 5000}
        },
    )
    monkeypatch.setattr(
        campaign.os,
        "pidfd_open",
        lambda pid: (_ for _ in ()).throw(
            AssertionError("uncertified numeric member must not get a pidfd")
        ),
    )
    signaled, uncertified = campaign._signal_worker_scope(
        5000,
        100,
        "eight_env_leaderboard/test/attempt_1",
        campaign.signal.SIGTERM,
        scope,
    )
    assert signaled == []
    assert uncertified == [5001]


def test_worker_scope_cleanup_escalates_then_attests_empty(tmp_path, monkeypatch):
    process = SimpleNamespace(pid=5000, poll=lambda: -campaign.signal.SIGKILL)
    scope = {
        5001: {
            "pgrp": 5000,
            "session": 5000,
            "start_ticks": 101,
            "nonce_match": True,
        }
    }
    snapshots = iter((scope, {}))
    signals = []
    waits = iter((scope, {}))
    monkeypatch.setattr(
        campaign, "_worker_scope_snapshot", lambda *args: next(snapshots)
    )
    monkeypatch.setattr(
        campaign,
        "_signal_worker_scope",
        lambda _pid, _ticks, _nonce, signum, current: (
            signals.append(signum) or (sorted(current), [])
        ),
    )
    monkeypatch.setattr(
        campaign, "_wait_worker_scope_empty", lambda *args: next(waits)
    )
    monkeypatch.setattr(campaign, "_listening_ports", lambda: set())

    cleanup = campaign._cleanup_worker_scope(
        process, 100, "eight_env_leaderboard/test/attempt_1", 25000
    )

    assert signals == [campaign.signal.SIGTERM, campaign.signal.SIGKILL]
    assert cleanup["initial_scope_pids"] == [5001]
    assert cleanup["term_signaled_pids"] == [5001]
    assert cleanup["kill_signaled_pids"] == [5001]
    assert campaign._cleanup_attestation_is_green(cleanup)


def test_worker_scope_cleanup_incomplete_is_not_green(monkeypatch):
    process = SimpleNamespace(pid=5000, poll=lambda: -campaign.signal.SIGKILL)
    scope = {
        5001: {
            "pgrp": 5000,
            "session": 5000,
            "start_ticks": 101,
            "nonce_match": True,
        }
    }
    monkeypatch.setattr(campaign, "_worker_scope_snapshot", lambda *args: scope)
    monkeypatch.setattr(
        campaign,
        "_signal_worker_scope",
        lambda _pid, _ticks, _nonce, _signum, current: (
            sorted(current), []
        ),
    )
    monkeypatch.setattr(
        campaign, "_wait_worker_scope_empty", lambda *args: scope
    )
    monkeypatch.setattr(campaign, "_listening_ports", lambda: {25000})

    cleanup = campaign._cleanup_worker_scope(
        process, 100, "eight_env_leaderboard/test/attempt_1", 25000
    )

    assert not campaign._cleanup_attestation_is_green(cleanup)
    assert cleanup["remaining_scope_pids"] == [5001]
    assert "fixed port 25000 remains occupied" in cleanup["error"]


def test_next_block_admission_ignores_prior_block_straggler():
    pending = [{
        "row": {
            "block": 2,
            "logical": "next-model",
            "route_rotation": 0,
        },
        "attempt": 1,
    }]
    running = {
        5000: {
            "row": {"logical": "slow-model"},
            "route": [campaign.REGIONS[0]],
        }
    }
    routes = {
        "slow-model": list(campaign.REGIONS),
        "next-model": list(campaign.REGIONS),
    }

    assert campaign._next_admissible_block(
        pending, running, routes, {1}
    ) == 2


def test_next_block_admission_preserves_route_and_deployment_quotas():
    pending = [{
        "row": {
            "block": 2,
            "logical": "capped-model",
            "route_rotation": 0,
        },
        "attempt": 1,
    }]
    running = {
        pid: {
            "row": {"logical": "capped-model"},
            "route": [campaign.REGIONS[0]],
        }
        for pid in range(
            5000, 5000 + campaign.PRIMARY_REGION_DEPLOYMENT_CAP
        )
    }
    routes = {"capped-model": list(campaign.REGIONS)}

    assert campaign._next_admissible_block(
        pending, running, routes, {1}
    ) is None
    assert campaign._next_admissible_block(
        pending, {}, {}, {1}
    ) is None


def test_next_block_waits_while_an_admitted_primary_is_eligible():
    pending = [
        {
            "row": {
                "block": block,
                "logical": "next-model",
                "route_rotation": 0,
            },
            "attempt": 1,
        }
        for block in (1, 2)
    ]
    assert campaign._next_admissible_block(
        pending,
        {},
        {"next-model": list(campaign.REGIONS)},
        {1},
    ) is None


def test_next_block_admission_never_skips_a_contiguous_block():
    pending = [{
        "row": {
            "block": 3,
            "logical": "next-model",
            "route_rotation": 0,
        },
        "attempt": 1,
    }]
    with pytest.raises(RuntimeError, match="contiguous admission suffix"):
        campaign._next_admissible_block(
            pending,
            {},
            {"next-model": list(campaign.REGIONS)},
            {1},
        )


def _healthy_probe(*, finished_epoch: float = 0.0) -> dict:
    return {
        "finished_epoch": finished_epoch,
        "all_regions_probed": True,
        "live_only": True,
        "routes": {
            "gpt-5.6-sol": list(campaign.REGIONS),
            "test-logical": list(campaign.REGIONS),
        },
        "large_sol": {
            "returncode": 0,
            "healthy_regions": list(campaign.REGIONS),
        },
        "record": "probe.json",
        "finished_utc": "1970-01-01T00:00:00Z",
    }


@pytest.mark.parametrize(
    ("age", "hard_valid", "spawn_ready"),
    [
        (campaign.PROBE_MAX_AGE_SECONDS - campaign.PROBE_RENEWAL_HEADROOM_SECONDS, True, True),
        (campaign.PROBE_MAX_AGE_SECONDS - campaign.PROBE_RENEWAL_HEADROOM_SECONDS + 0.001, True, False),
        (campaign.PROBE_MAX_AGE_SECONDS, True, False),
        (campaign.PROBE_MAX_AGE_SECONDS + 0.001, False, False),
    ],
)
def test_probe_lease_hard_boundary_and_soft_renewal_headroom(
    monkeypatch, age, hard_valid, spawn_ready
):
    now = 10_000.0
    record = _healthy_probe(finished_epoch=now - age)
    monkeypatch.setattr(campaign.time, "time", lambda: now)

    assert campaign._probe_is_reusable(
        record, require_large_sol=True
    ) is hard_valid
    assert campaign._probe_is_spawn_ready(
        record, require_large_sol=True
    ) is spawn_ready


def test_probe_spawn_ready_keeps_large_sol_quality_requirement(monkeypatch):
    now = 10_000.0
    record = _healthy_probe(finished_epoch=now)
    record["large_sol"]["healthy_regions"] = [campaign.REGIONS[0]]
    monkeypatch.setattr(campaign.time, "time", lambda: now)

    assert not campaign._probe_is_spawn_ready(
        record, require_large_sol=True
    )
    assert campaign._probe_is_spawn_ready(
        record, require_large_sol=False
    )


def test_launch_preflight_probe_rollover_is_typed_and_zero_artifact(
    tmp_path, monkeypatch
):
    record = _healthy_probe(finished_epoch=0.0)
    times = iter((3500.0, 3540.001))
    popen_called = False

    def forbidden_popen(*args, **kwargs):
        nonlocal popen_called
        popen_called = True
        raise AssertionError("Popen must not run after probe lease rollover")

    monkeypatch.setattr(campaign, "_verify_frozen_inputs", lambda *args: None)
    monkeypatch.setattr(campaign, "_assert_host_resources", lambda *args: {})
    monkeypatch.setattr(campaign, "_latest_probe", lambda *args: record)
    monkeypatch.setattr(campaign, "_listening_ports", lambda: set())
    monkeypatch.setattr(
        campaign,
        "_child_environment",
        lambda _manifest, row, _route, attempt: {
            "AGENTARENA_CACHE_NONCE": campaign._cache_nonce(
                _manifest, row, attempt
            )
        },
    )
    monkeypatch.setattr(campaign.time, "time", lambda: next(times))
    monkeypatch.setattr(campaign.subprocess, "Popen", forbidden_popen)

    row = {
        "index": 0,
        "run_id": "airbnb_r1/test",
        "cell_name": "airbnb_test",
        "env": "airbnb",
        "condition": "steered",
        "port": 25000,
        "model": "test-model",
        "logical": "test-logical",
        "route_rotation": 0,
        "task_key": "airbnb|thresholded",
    }
    manifest = _bind_manifest(tmp_path, {
        "caps": {"max_steps": 12000},
        "models": {"test-model": {"spec": "test-model"}},
        "tasks": {"airbnb|thresholded": {"instruction": "test"}},
    })
    item = {"row": row, "attempt": 1}

    with pytest.raises(campaign.ProbeRefreshRequired):
        campaign._launch_one(
            tmp_path, manifest, item, list(campaign.REGIONS), set()
        )

    assert not popen_called
    assert not campaign._attempt_dir(tmp_path, row, 1).exists()
    assert not campaign._launch_receipt_path(tmp_path, row, 1).exists()
    assert not campaign._completion_receipt_path(tmp_path, row, 1).exists()


def test_successful_launch_returns_tracked_worker_and_exact_probe_receipt(
    tmp_path, monkeypatch
):
    record = _healthy_probe(finished_epoch=100.0)
    probe_path = tmp_path / "probes" / record["record"]
    probe_path.parent.mkdir(parents=True)
    probe_path.write_text(json.dumps(record))
    (tmp_path / "campaign_manifest.json").write_text("{}")

    class FakeProcess:
        pid = 424242

        def __init__(self):
            self.stdin = io.StringIO()

    process = FakeProcess()
    monkeypatch.setattr(campaign, "_verify_frozen_inputs", lambda *args: None)
    monkeypatch.setattr(
        campaign,
        "_assert_host_resources",
        lambda *args: {"external_run_cell_pids": []},
    )
    monkeypatch.setattr(campaign, "_latest_probe", lambda *args: record)
    monkeypatch.setattr(campaign, "_listening_ports", lambda: set())
    monkeypatch.setattr(
        campaign,
        "_child_environment",
        lambda _manifest, row, _route, attempt: {
            "AGENTARENA_CACHE_NONCE": campaign._cache_nonce(
                _manifest, row, attempt
            )
        },
    )
    monkeypatch.setattr(campaign, "_pid_start_ticks", lambda pid: 12345)
    monkeypatch.setattr(campaign.time, "time", lambda: 101.0)
    monkeypatch.setattr(campaign.subprocess, "Popen", lambda *args, **kwargs: process)

    row = {
        "index": 0,
        "run_id": "airbnb_r1/test",
        "cell_name": "airbnb_test",
        "env": "airbnb",
        "condition": "steered",
        "port": 25000,
        "model": "test-model",
        "logical": "test-logical",
        "route_rotation": 0,
        "task_key": "airbnb|thresholded",
    }
    manifest = _bind_manifest(tmp_path, {
        "caps": {"max_steps": 12000, "cell_timeout_seconds": 172800},
        "models": {"test-model": {"spec": "test-model"}},
        "tasks": {"airbnb|thresholded": {"instruction": "test"}},
        "frozen_artifacts": {"code_inventory.json": "abc"},
    })
    item = {"row": row, "attempt": 1}

    active = campaign._launch_one(
        tmp_path, manifest, item, list(campaign.REGIONS), set()
    )
    receipt = json.loads(
        campaign._launch_receipt_path(tmp_path, row, 1).read_text()
    )

    assert active["process"] is process
    assert active["attempt"] == 1
    assert receipt["pid"] == process.pid
    assert receipt["schema"] == campaign.LAUNCH_RECEIPT_SCHEMA
    assert receipt["started_epoch"] == 101.0
    assert receipt["probe_attestation"]["record"] == record["record"]
    assert receipt["route_order"] == list(campaign.REGIONS)
    assert receipt["cache_nonce"] == campaign._cache_nonce(manifest, row, 1)
    assert active["cache_nonce"] == receipt["cache_nonce"]
    active["log_handle"].close()


def test_launch_fails_closed_if_popen_crosses_hard_probe_boundary(
    tmp_path, monkeypatch
):
    record = _healthy_probe(finished_epoch=0.0)
    probe_path = tmp_path / "probes" / record["record"]
    probe_path.parent.mkdir(parents=True)
    probe_path.write_text(json.dumps(record))
    (tmp_path / "campaign_manifest.json").write_text("{}")
    times = iter((3500.0, 3530.0, 3600.001))
    cleanups = []

    class FakeProcess:
        pid = 434343
        stdin = io.StringIO()

        def wait(self, timeout):
            return 0

    monkeypatch.setattr(campaign, "_verify_frozen_inputs", lambda *args: None)
    monkeypatch.setattr(campaign, "_assert_host_resources", lambda *args: {})
    monkeypatch.setattr(campaign, "_latest_probe", lambda *args: record)
    monkeypatch.setattr(campaign, "_listening_ports", lambda: set())
    monkeypatch.setattr(
        campaign,
        "_child_environment",
        lambda _manifest, row, _route, attempt: {
            "AGENTARENA_CACHE_NONCE": campaign._cache_nonce(
                _manifest, row, attempt
            )
        },
    )
    monkeypatch.setattr(campaign, "_pid_start_ticks", lambda pid: 54321)
    monkeypatch.setattr(campaign.time, "time", lambda: next(times))
    monkeypatch.setattr(
        campaign.subprocess, "Popen", lambda *args, **kwargs: FakeProcess()
    )
    def cleanup(process, pid_start_ticks, cache_nonce, port, **kwargs):
        cleanups.append((
            process.pid, pid_start_ticks, cache_nonce, port, kwargs.get("mode")
        ))
        return _green_cleanup(
            process, pid_start_ticks, cache_nonce, port, **kwargs
        )

    monkeypatch.setattr(campaign, "_cleanup_worker_scope", cleanup)

    row = {
        "index": 0,
        "run_id": "airbnb_r1/test",
        "cell_name": "airbnb_test",
        "env": "airbnb",
        "condition": "steered",
        "port": 25000,
        "model": "test-model",
        "logical": "test-logical",
        "route_rotation": 0,
        "task_key": "airbnb|thresholded",
    }
    manifest = _bind_manifest(tmp_path, {
        "caps": {"max_steps": 12000, "cell_timeout_seconds": 172800},
        "models": {"test-model": {"spec": "test-model"}},
        "tasks": {"airbnb|thresholded": {"instruction": "test"}},
        "frozen_artifacts": {},
    })
    item = {"row": row, "attempt": 1}

    with pytest.raises(
        RuntimeError, match="crossed the hard probe-freshness boundary"
    ):
        campaign._launch_one(
            tmp_path, manifest, item, list(campaign.REGIONS), set()
        )

    assert cleanups == [
        (
            FakeProcess.pid,
            54321,
            campaign._cache_nonce(manifest, row, 1),
            row["port"],
            "controller_exit",
        )
    ]
    assert not campaign._launch_receipt_path(tmp_path, row, 1).exists()


def test_block_open_refreshes_if_probe_crosses_soft_edge_before_receipt(
    tmp_path, monkeypatch
):
    old = _healthy_probe(finished_epoch=0.0)
    old["record"] = "old_probe.json"
    fresh = _healthy_probe(finished_epoch=3540.2)
    fresh["record"] = "fresh_probe.json"
    probe_dir = tmp_path / "probes"
    probe_dir.mkdir()
    (probe_dir / old["record"]).write_text(json.dumps(old))
    (probe_dir / fresh["record"]).write_text(json.dumps(fresh))
    times = iter((3539.9, 3540.1, 3540.3))
    reasons = []

    monkeypatch.setattr(campaign, "_verify_frozen_inputs", lambda *args: None)
    monkeypatch.setattr(campaign, "_latest_probe", lambda *args: old)
    monkeypatch.setattr(campaign.time, "time", lambda: next(times))

    def refreshed(*args, **kwargs):
        reasons.append(kwargs["reason"])
        return fresh

    monkeypatch.setattr(campaign, "_run_probe", refreshed)
    opened = set()

    record, block = campaign._open_next_block(tmp_path, {}, opened)
    receipt = json.loads(
        (tmp_path / "block_receipts" / "block_01.json").read_text()
    )

    assert record is fresh
    assert block == 1
    assert opened == {1}
    assert reasons == ["before_block_1_renewal_edge"]
    assert receipt["probe_record"] == fresh["record"]
    assert receipt["opened_epoch"] == 3540.3
    assert receipt["probe_sha256"] == campaign._sha_file(
        probe_dir / fresh["record"]
    )


def test_launch_rejects_route_not_bound_to_exact_probe(tmp_path, monkeypatch):
    record = _healthy_probe(finished_epoch=100.0)
    monkeypatch.setattr(campaign, "_verify_frozen_inputs", lambda *args: None)
    monkeypatch.setattr(campaign, "_assert_host_resources", lambda *args: {})
    monkeypatch.setattr(campaign, "_latest_probe", lambda *args: record)
    monkeypatch.setattr(campaign, "_listening_ports", lambda: set())
    monkeypatch.setattr(campaign.time, "time", lambda: 101.0)
    row = {
        "index": 0,
        "run_id": "airbnb_r1/test",
        "cell_name": "airbnb_test",
        "port": 25000,
        "logical": "test-logical",
        "route_rotation": 0,
    }
    item = {"row": row, "attempt": 1}

    with pytest.raises(
        RuntimeError, match="route differs from exact qualifying probe"
    ):
        campaign._launch_one(
            tmp_path,
            {},
            item,
            list(reversed(campaign.REGIONS)),
            set(),
        )
    assert not campaign._attempt_dir(tmp_path, row, 1).exists()


def test_every_launch_requires_large_sol_qualified_probe(tmp_path, monkeypatch):
    record = _healthy_probe(finished_epoch=100.0)
    record["large_sol"]["healthy_regions"] = [campaign.REGIONS[0]]
    monkeypatch.setattr(campaign, "_verify_frozen_inputs", lambda *args: None)
    monkeypatch.setattr(campaign, "_assert_host_resources", lambda *args: {})
    monkeypatch.setattr(campaign, "_latest_probe", lambda *args: record)
    monkeypatch.setattr(campaign.time, "time", lambda: 101.0)
    row = {
        "index": 0,
        "run_id": "airbnb_r1/test",
        "cell_name": "airbnb_test",
        "logical": "test-logical",
    }
    item = {"row": row, "attempt": 1}

    with pytest.raises(campaign.ProbeRefreshRequired):
        campaign._launch_one(
            tmp_path, {}, item, list(campaign.REGIONS), set()
        )
    assert not campaign._attempt_dir(tmp_path, row, 1).exists()


class _ControllerLock:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _ControllerLog:
    def close(self):
        pass


class _ControllerProcess:
    pid = 4242

    def poll(self):
        return 0


def _green_cleanup(process, pid_start_ticks, cache_nonce, port, **kwargs):
    return {
        "schema": campaign.WORKER_CLEANUP_SCHEMA,
        "mode": kwargs.get("mode", "controller_exit"),
        "worker_pid": process.pid,
        "launch_pid_start_ticks": pid_start_ticks,
        "session_id": process.pid,
        "signal_membership": "exact_cache_nonce_and_start_ticks_via_pidfd",
        "cache_nonce_sha256": campaign._sha_bytes(cache_nonce.encode()),
        "initial_scope_pids": [],
        "term_signaled_pids": [],
        "kill_signaled_pids": [],
        "uncertified_scope_pids": [],
        "remaining_scope_pids": [],
        "confirmed_empty": True,
        "port_released": True,
        "error": None,
    }


def _controller_row() -> dict:
    return {
        "index": 0,
        "run_id": "airbnb_r1/controller-test",
        "cell_name": "airbnb_controller_test",
        "env": "airbnb",
        "condition": "steered",
        "port": 25000,
        "model": "test-model",
        "logical": "test-logical",
        "task_key": "airbnb|thresholded",
        "block": 1,
        "route_rotation": 0,
    }


def _controller_manifest(row: dict) -> dict:
    return {
        "schedule": {
            "default_jobs": campaign.DEFAULT_JOBS,
            "global_spawn_stagger_seconds": campaign.DEFAULT_SPAWN_STAGGER,
        },
        "runs": [row],
        "caps": {"max_steps": 12000, "cell_timeout_seconds": 172800},
        "models": {"test-model": {"spec": "test-model"}},
        "tasks": {"airbnb|thresholded": {"instruction": "test"}},
        "heroes": {"airbnb": "hero"},
    }


def _patch_controller_shell(
    monkeypatch,
    *,
    manifest: dict,
    pending: list[dict],
    opened_blocks: set[int],
    latest_probe,
    run_probe,
    open_next_block,
    launch_one,
    now: list[float],
    statuses: list[str],
):
    launcher_lock = _ControllerLock()
    host_lock = _ControllerLock()
    monkeypatch.setattr(campaign, "_load_manifest", lambda *args: manifest)
    monkeypatch.setattr(campaign, "_listening_ports", lambda: set())
    monkeypatch.setattr(
        campaign, "_acquire_launcher_lock", lambda *args: launcher_lock
    )
    monkeypatch.setattr(
        campaign, "_acquire_host_browser_lock", lambda *args: host_lock
    )
    monkeypatch.setattr(campaign, "_assert_host_resources", lambda *args: {})
    monkeypatch.setattr(campaign, "_verify_frozen_inputs", lambda *args: None)
    monkeypatch.setattr(
        campaign,
        "_cache_nonce",
        lambda *args: (
            f"test-campaign/{args[-2]['run_id']}/attempt_{args[-1]}"
        ),
    )
    monkeypatch.setattr(
        campaign, "_prior_state", lambda *args: (list(pending), {})
    )
    monkeypatch.setattr(
        campaign, "_audit_block_receipts", lambda *args: set(opened_blocks)
    )
    monkeypatch.setattr(campaign, "_read_json", lambda *args: {})
    monkeypatch.setattr(campaign, "_latest_probe", latest_probe)
    monkeypatch.setattr(
        campaign,
        "_probe_is_spawn_ready",
        lambda record, **kwargs: bool(record and record.get("ready")),
    )
    monkeypatch.setattr(campaign, "_run_probe", run_probe)
    monkeypatch.setattr(campaign, "_open_next_block", open_next_block)
    def tracked_launch(*args, **kwargs):
        active = launch_one(*args, **kwargs)
        active.setdefault("pid_start_ticks", 12345)
        active.setdefault(
            "cache_nonce",
            f"test-campaign/{active['row']['run_id']}/attempt_{active['attempt']}",
        )
        return active

    monkeypatch.setattr(campaign, "_launch_one", tracked_launch)
    monkeypatch.setattr(campaign, "_cleanup_worker_scope", _green_cleanup)
    monkeypatch.setattr(
        campaign,
        "_classify_attempt",
        lambda *args, **kwargs: {
            "class": "scored",
            "code": "ok",
            "evidence": "",
        },
    )
    monkeypatch.setattr(campaign, "_atomic_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        campaign,
        "_write_status",
        lambda *args, **kwargs: statuses.append(kwargs["state"]),
    )
    monkeypatch.setattr(campaign, "_utc", lambda: "1970-01-01T00:00:00Z")
    monkeypatch.setattr(campaign.signal, "signal", lambda *args: None)
    monkeypatch.setattr(campaign.time, "time", lambda: now[0])
    monkeypatch.setattr(
        campaign.time,
        "sleep",
        lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    return launcher_lock, host_lock


@pytest.mark.parametrize(("phase", "attempt"), [("primary", 1), ("refill", 2)])
def test_controller_probe_rollover_waits_once_and_preserves_attempt_and_route(
    tmp_path, monkeypatch, phase, attempt
):
    row = _controller_row()
    manifest = _controller_manifest(row)
    now = [100.0]
    statuses = []
    probes = []
    launches = []
    current = {
        "ready": True,
        "name": "old",
        "routes": {
            row["logical"]: [campaign.REGIONS[0], campaign.REGIONS[1]]
        },
    }
    renewal_attempts = 0

    def run_probe(*args, **kwargs):
        nonlocal renewal_attempts
        if kwargs["reason"] == "before_refill_only_resume":
            return {"routes": current["routes"]}
        probes.append((now[0], kwargs["reason"]))
        renewal_attempts += 1
        if renewal_attempts == 1:
            raise campaign.ProbeUnavailableError("temporary brownout")
        current.update(
            ready=True,
            name="fresh",
            routes={
                row["logical"]: [campaign.REGIONS[2], campaign.REGIONS[0]]
            },
        )
        return {"routes": current["routes"]}

    def open_next_block(_campaign, _manifest, opened):
        opened.add(1)
        return {"routes": current["routes"]}, 1

    def launch_one(_campaign, _manifest, item, route, _pids):
        launches.append((now[0], item["attempt"], tuple(route), current["name"]))
        if len(launches) == 1:
            current["ready"] = False
            raise campaign.ProbeRefreshRequired("forced lease rollover")
        return {
            **item,
            "process": _ControllerProcess(),
            "log_handle": _ControllerLog(),
            "route": route,
            "started_utc": "1970-01-01T00:00:00Z",
            "out_dir": tmp_path / "missing-summary",
        }

    locks = _patch_controller_shell(
        monkeypatch,
        manifest=manifest,
        pending=[{"row": row, "attempt": attempt}],
        opened_blocks=set(),
        latest_probe=lambda *args: current,
        run_probe=run_probe,
        open_next_block=open_next_block,
        launch_one=launch_one,
        now=now,
        statuses=statuses,
    )

    result = campaign.launch(
        SimpleNamespace(
            campaign=tmp_path,
            jobs=campaign.DEFAULT_JOBS,
            stagger=campaign.DEFAULT_SPAWN_STAGGER,
        )
    )

    old_route = (
        (campaign.REGIONS[0], campaign.REGIONS[1])
        if attempt == 1
        else (campaign.REGIONS[1], campaign.REGIONS[0])
    )
    fresh_route = (
        (campaign.REGIONS[2], campaign.REGIONS[0])
        if attempt == 1
        else (campaign.REGIONS[0], campaign.REGIONS[2])
    )
    assert result == 0
    assert probes == [
        (101.0, "spawn_freshness_reprobe"),
        (161.0, "spawn_freshness_reprobe"),
    ]
    assert launches == [
        (100.0, attempt, old_route, "old"),
        (161.0, attempt, fresh_route, "fresh"),
    ]
    assert statuses[-1] == "complete"
    assert all(lock.closed for lock in locks)


def test_controller_cleans_sigkill_then_refills_exact_next_attempt(
    tmp_path, monkeypatch
):
    row = _controller_row()
    manifest = _controller_manifest(row)
    now = [100.0]
    statuses = []
    launches = []
    events = []
    completions = []
    current = {
        "ready": True,
        "routes": {row["logical"]: list(campaign.REGIONS)},
    }

    class Process:
        def __init__(self, pid, returncode):
            self.pid = pid
            self.returncode = returncode

        def poll(self):
            return self.returncode

    def launch_one(_campaign, _manifest, item, route, _pids):
        launches.append((item["attempt"], tuple(route)))
        return {
            **item,
            "process": Process(
                4200 + item["attempt"],
                -campaign.signal.SIGKILL if item["attempt"] == 1 else 0,
            ),
            "log_handle": _ControllerLog(),
            "route": route,
            "cache_nonce": campaign._cache_nonce(row, item["attempt"]),
            "started_utc": "1970-01-01T00:00:00Z",
            "out_dir": tmp_path / f"missing-summary-a{item['attempt']}",
        }

    locks = _patch_controller_shell(
        monkeypatch,
        manifest=manifest,
        pending=[{"row": row, "attempt": 1}],
        opened_blocks={1},
        latest_probe=lambda *args: current,
        run_probe=lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("fresh ready probe should be reused")
        ),
        open_next_block=lambda *args: (_ for _ in ()).throw(
            AssertionError("no block should open")
        ),
        launch_one=launch_one,
        now=now,
        statuses=statuses,
    )

    def cleanup(process, pid_start_ticks, cache_nonce, port, **kwargs):
        events.append(("cleanup", process.returncode))
        return _green_cleanup(
            process, pid_start_ticks, cache_nonce, port, **kwargs
        )

    def classify(*args, worker_returncode=None, **kwargs):
        events.append(("classify", worker_returncode))
        if worker_returncode == -campaign.signal.SIGKILL:
            return {
                "class": "infra",
                "code": "worker_sigkill",
                "evidence": "exact controller status",
                "steps": 0,
                "outcome": None,
            }
        return {"class": "scored", "code": "ok", "evidence": ""}

    def atomic(path, payload):
        if payload.get("schema") == campaign.COMPLETION_RECEIPT_SCHEMA:
            completions.append(payload)

    monkeypatch.setattr(campaign, "_cleanup_worker_scope", cleanup)
    monkeypatch.setattr(campaign, "_classify_attempt", classify)
    monkeypatch.setattr(campaign, "_atomic_json", atomic)

    result = campaign.launch(
        SimpleNamespace(
            campaign=tmp_path,
            jobs=campaign.DEFAULT_JOBS,
            stagger=campaign.DEFAULT_SPAWN_STAGGER,
        )
    )

    assert result == 0
    assert launches == [
        (1, tuple(campaign.REGIONS)),
        (2, tuple((*campaign.REGIONS[1:], campaign.REGIONS[0]))),
    ]
    assert events == [
        ("cleanup", -campaign.signal.SIGKILL),
        ("classify", -campaign.signal.SIGKILL),
        ("cleanup", 0),
        ("classify", 0),
    ]
    assert [receipt["classification"]["class"] for receipt in completions] == [
        "infra",
        "scored",
    ]
    assert all(
        receipt["worker_scope_cleanup"]["confirmed_empty"] is True
        for receipt in completions
    )
    assert statuses[-1] == "complete"
    assert all(lock.closed for lock in locks)


def test_controller_cleanup_failure_never_queues_sigkill_refill(
    tmp_path, monkeypatch
):
    row = _controller_row()
    manifest = _controller_manifest(row)
    now = [100.0]
    statuses = []
    launches = []
    completions = []
    current = {
        "ready": True,
        "routes": {row["logical"]: list(campaign.REGIONS)},
    }

    class KilledProcess:
        pid = 4242

        def poll(self):
            return -campaign.signal.SIGKILL

    def launch_one(_campaign, _manifest, item, route, _pids):
        launches.append(item["attempt"])
        return {
            **item,
            "process": KilledProcess(),
            "log_handle": _ControllerLog(),
            "route": route,
            "cache_nonce": campaign._cache_nonce(row, item["attempt"]),
            "started_utc": "1970-01-01T00:00:00Z",
            "out_dir": tmp_path / "missing-summary",
        }

    _patch_controller_shell(
        monkeypatch,
        manifest=manifest,
        pending=[{"row": row, "attempt": 1}],
        opened_blocks={1},
        latest_probe=lambda *args: current,
        run_probe=lambda *args, **kwargs: current,
        open_next_block=lambda *args: (current, 1),
        launch_one=launch_one,
        now=now,
        statuses=statuses,
    )
    failed = _green_cleanup(
        KilledProcess(), 12345, campaign._cache_nonce(row, 1), row["port"]
    )
    failed.update(
        confirmed_empty=False,
        remaining_scope_pids=[9001],
        error="worker scope still contains PIDs [9001]",
    )
    monkeypatch.setattr(campaign, "_cleanup_worker_scope", lambda *args: failed)
    monkeypatch.setattr(
        campaign,
        "_classify_attempt",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("classifier must not run before cleanup succeeds")
        ),
    )
    monkeypatch.setattr(
        campaign,
        "_atomic_json",
        lambda path, payload: completions.append(payload)
        if payload.get("schema") == campaign.COMPLETION_RECEIPT_SCHEMA
        else None,
    )

    result = campaign.launch(
        SimpleNamespace(
            campaign=tmp_path,
            jobs=campaign.DEFAULT_JOBS,
            stagger=campaign.DEFAULT_SPAWN_STAGGER,
        )
    )

    assert result == 2
    assert launches == [1]
    assert completions[0]["classification"]["code"] == (
        "worker_scope_cleanup_failed"
    )
    assert statuses[-1] == "protocol_invalid"


def _write_resume_attempt_state(tmp_path, row, manifest):
    _bind_manifest(tmp_path, manifest)
    out_dir = campaign._attempt_dir(tmp_path, row, 1)
    out_dir.mkdir(parents=True)
    launch = {
        "pid": 5151,
        "pid_start_ticks": 12345,
        "out_dir": str(out_dir.resolve()),
        "route_order": list(campaign.REGIONS),
        "cache_nonce": campaign._cache_nonce(manifest, row, 1),
    }
    launch_path = campaign._launch_receipt_path(tmp_path, row, 1)
    launch_path.parent.mkdir(parents=True)
    launch_path.write_text(json.dumps(launch))
    frozen = tmp_path / "frozen_inputs"
    frozen.mkdir()
    contract = campaign._clone_limit_contract()
    (frozen / "limit_contract.json").write_text(json.dumps(contract))
    return out_dir, launch, contract


def _patch_resume_audits(monkeypatch):
    monkeypatch.setattr(
        campaign, "_audit_attempt_artifact_inventory", lambda *args: None
    )
    monkeypatch.setattr(campaign, "_audit_block_receipts", lambda *args: set())
    monkeypatch.setattr(campaign, "_validate_launch_receipt", lambda *args: [])


def test_resume_reconstructs_sigkill_retry_only_from_completion_receipt(
    tmp_path, monkeypatch
):
    row = _controller_row()
    manifest = {"runs": [row], "tasks": {row["task_key"]: {}}}
    out_dir, launch, contract = _write_resume_attempt_state(
        tmp_path, row, manifest
    )
    _write_no_checkout_db(out_dir, row)
    proof = campaign._no_checkout_proof(out_dir, row)
    classification = campaign._classify_attempt(
        out_dir,
        contract,
        manifest,
        row,
        worker_returncode=-campaign.signal.SIGKILL,
        no_checkout_proof=proof,
    )
    process = SimpleNamespace(pid=launch["pid"])
    cleanup = _green_cleanup(
        process,
        launch["pid_start_ticks"],
        campaign._cache_nonce(manifest, row, 1),
        row["port"],
    )
    completion = {
        "schema": campaign.COMPLETION_RECEIPT_SCHEMA,
        "run_index": row["index"],
        "run_id": row["run_id"],
        "attempt": 1,
        "pid": launch["pid"],
        "returncode": -campaign.signal.SIGKILL,
        "classification": classification,
        "route_order": launch["route_order"],
        "worker_scope_cleanup": cleanup,
        "no_checkout_proof": proof,
    }
    completion_path = campaign._completion_receipt_path(tmp_path, row, 1)
    completion_path.parent.mkdir(parents=True)
    completion_path.write_text(json.dumps(completion))
    _patch_resume_audits(monkeypatch)

    pending, final = campaign._prior_state(tmp_path, manifest)

    assert final == {}
    assert len(pending) == 1
    assert pending[0]["row"] == row
    assert pending[0]["attempt"] == 2


def test_restart_validation_matches_max_attempt_terminalization(tmp_path):
    row = _controller_row()
    manifest = _bind_manifest(
        tmp_path, {"runs": [row], "tasks": {row["task_key"]: {}}}
    )
    out_dir = campaign._attempt_dir(tmp_path, row, campaign.MAX_ATTEMPTS)
    out_dir.mkdir(parents=True)
    _write_no_checkout_db(out_dir, row)
    proof = campaign._no_checkout_proof(out_dir, row)
    raw = campaign._classify_attempt(
        out_dir,
        campaign._clone_limit_contract(),
        manifest,
        row,
        worker_returncode=-campaign.signal.SIGKILL,
        no_checkout_proof=proof,
    )
    terminal = campaign._terminalize_infrastructure_retries(
        raw, row, campaign.MAX_ATTEMPTS
    )
    launch = {
        "pid": 5151,
        "pid_start_ticks": 12345,
        "out_dir": str(out_dir.resolve()),
        "route_order": list(campaign.REGIONS),
    }
    cleanup = _green_cleanup(
        SimpleNamespace(pid=5151),
        12345,
        campaign._cache_nonce(manifest, row, campaign.MAX_ATTEMPTS),
        row["port"],
    )
    completion = {
        "schema": campaign.COMPLETION_RECEIPT_SCHEMA,
        "run_index": row["index"],
        "run_id": row["run_id"],
        "attempt": campaign.MAX_ATTEMPTS,
        "pid": 5151,
        "returncode": -campaign.signal.SIGKILL,
        "classification": terminal,
        "route_order": list(campaign.REGIONS),
        "worker_scope_cleanup": cleanup,
        "no_checkout_proof": proof,
    }
    assert terminal["code"] == "infrastructure_retries_exhausted"
    assert campaign._validate_completion_receipt(
        manifest,
        row,
        campaign.MAX_ATTEMPTS,
        launch,
        completion,
        terminal,
    ) == []


def test_resume_without_completion_receipt_never_infers_sigkill_retry(
    tmp_path, monkeypatch
):
    row = _controller_row()
    manifest = {"runs": [row], "tasks": {row["task_key"]: {}}}
    _write_resume_attempt_state(tmp_path, row, manifest)
    _patch_resume_audits(monkeypatch)
    monkeypatch.setattr(campaign, "_receipt_process_is_alive", lambda *args: False)
    monkeypatch.setattr(
        campaign,
        "_reconciled_absent_scope_attestation",
        lambda pid, ticks, nonce, port: _green_cleanup(
            SimpleNamespace(pid=pid),
            ticks,
            nonce,
            port,
            mode="reconciled_absent",
        ),
    )

    pending, final = campaign._prior_state(tmp_path, manifest)

    assert pending == []
    assert final[row["index"]]["classification"]["class"] == (
        "scientific_invalid"
    )
    assert final[row["index"]]["classification"]["code"] == (
        "unpersisted_or_incomplete_attempt"
    )
    reconciled = json.loads(
        campaign._completion_receipt_path(tmp_path, row, 1).read_text()
    )
    assert reconciled["returncode"] is None
    assert reconciled["reconciled_after_launcher_restart"] is True


def test_resume_fails_closed_while_dead_worker_orphan_scope_remains(
    tmp_path, monkeypatch
):
    row = _controller_row()
    manifest = {"runs": [row], "tasks": {row["task_key"]: {}}}
    _write_resume_attempt_state(tmp_path, row, manifest)
    _patch_resume_audits(monkeypatch)
    monkeypatch.setattr(campaign, "_receipt_process_is_alive", lambda *args: False)
    failed = _green_cleanup(
        SimpleNamespace(pid=5151),
        12345,
        campaign._cache_nonce(manifest, row, 1),
        row["port"],
        mode="reconciled_absent",
    )
    failed.update(
        confirmed_empty=False,
        remaining_scope_pids=[6161],
        error="worker scope still contains PIDs [6161]",
    )
    monkeypatch.setattr(
        campaign,
        "_reconciled_absent_scope_attestation",
        lambda *args: failed,
    )

    with pytest.raises(RuntimeError, match="unclean orphan scope"):
        campaign._prior_state(tmp_path, manifest)
    assert not campaign._completion_receipt_path(tmp_path, row, 1).exists()


@pytest.mark.parametrize("branch", ["new-primary", "resume-primary", "refill"])
@pytest.mark.parametrize("failure", ["unavailable", "fatal"])
def test_controller_startup_probe_distinguishes_unavailable_from_fatal(
    tmp_path, monkeypatch, branch, failure
):
    row = _controller_row()
    manifest = _controller_manifest(row)
    attempt = 2 if branch == "refill" else 1
    now = [100.0]
    statuses = []
    probes = []
    launches = []
    current = {"ready": False, "routes": {}}
    first_probe = True

    def fail_or_refresh(reason):
        nonlocal first_probe
        probes.append((now[0], reason))
        if first_probe:
            first_probe = False
            if failure == "fatal":
                raise RuntimeError("malformed probe response")
            raise campaign.ProbeUnavailableError("temporary brownout")
        current.update(
            ready=True,
            routes={
                row["logical"]: [campaign.REGIONS[2], campaign.REGIONS[0]]
            },
        )
        return {"routes": current["routes"]}

    def run_probe(*args, **kwargs):
        return fail_or_refresh(kwargs["reason"])

    def open_next_block(_campaign, _manifest, opened):
        if not current["ready"]:
            return fail_or_refresh("before_block_1")
        opened.add(1)
        return {"routes": current["routes"]}, 1

    def launch_one(_campaign, _manifest, item, route, _pids):
        launches.append((now[0], item["attempt"], tuple(route)))
        return {
            **item,
            "process": _ControllerProcess(),
            "log_handle": _ControllerLog(),
            "route": route,
            "started_utc": "1970-01-01T00:00:00Z",
            "out_dir": tmp_path / "missing-summary",
        }

    opened = {1} if branch == "resume-primary" else set()
    locks = _patch_controller_shell(
        monkeypatch,
        manifest=manifest,
        pending=[{"row": row, "attempt": attempt}],
        opened_blocks=opened,
        latest_probe=lambda *args: current,
        run_probe=run_probe,
        open_next_block=open_next_block,
        launch_one=launch_one,
        now=now,
        statuses=statuses,
    )

    result = campaign.launch(
        SimpleNamespace(
            campaign=tmp_path,
            jobs=campaign.DEFAULT_JOBS,
            stagger=campaign.DEFAULT_SPAWN_STAGGER,
        )
    )

    initial_reason = {
        "new-primary": "before_block_1",
        "resume-primary": "resume_prelaunch",
        "refill": "before_refill_only_resume",
    }[branch]
    if failure == "fatal":
        assert result == 2
        assert probes == [(100.0, initial_reason)]
        assert launches == []
        assert statuses[-1] == "protocol_invalid"
    else:
        assert result == 0
        assert probes == [
            (100.0, initial_reason),
            (160.0, "spawn_freshness_reprobe"),
        ]
        assert launches == [
            (
                160.0,
                attempt,
                (
                    (campaign.REGIONS[2], campaign.REGIONS[0])
                    if attempt == 1
                    else (campaign.REGIONS[0], campaign.REGIONS[2])
                ),
            )
        ]
        assert statuses[-1] == "complete"
    assert all(lock.closed for lock in locks)


def test_non_probe_prelaunch_error_remains_fatal(tmp_path, monkeypatch):
    def freeze_failure(*args):
        raise RuntimeError("frozen source mismatch")

    monkeypatch.setattr(campaign, "_verify_frozen_inputs", freeze_failure)
    item = {
        "row": {
            "run_id": "airbnb_r1/test",
            "logical": "test-logical",
        },
        "attempt": 1,
    }

    with pytest.raises(RuntimeError, match="frozen source mismatch") as exc:
        campaign._launch_one(
            tmp_path, {}, item, list(campaign.REGIONS), set()
        )
    assert not isinstance(exc.value, campaign.ProbeRefreshRequired)


def test_clone_launch_contract_freezes_probe_renewal_semantics():
    launch = campaign._clone_limit_contract()["categories"]["launch_only"][
        "campaign_launch"
    ]["configured"]
    assert launch["probe_freshness_seconds"] == 3600
    assert launch["probe_renewal_headroom_seconds"] == 60
    assert launch["block_probe_receipt_schema"] == 2
    assert launch["manifest_schema"] == campaign.MANIFEST_SCHEMA
    assert launch["launch_receipt_schema"] == campaign.LAUNCH_RECEIPT_SCHEMA
    assert launch["launch_probe_age_basis"] == "started_epoch"
    assert launch["probe_renewal_retry_semantics"] == (
        "typed_pre_popen_soft_refresh; same row and attempt; zero artifacts; "
        "post_popen_hard_crossing_fatal"
    )


def test_continuation_runtime_dependencies_must_match_base_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen = tmp_path / "frozen_inputs"
    frozen.mkdir()
    expected = {"python": {"version": "frozen"}}
    (frozen / "runtime_dependencies.json").write_text(
        json.dumps(expected)
    )
    monkeypatch.setattr(
        "scripts.hard_campaign_runtime.runtime_dependency_manifest",
        lambda: expected,
    )
    campaign._verify_base_runtime_dependencies(tmp_path)
    monkeypatch.setattr(
        "scripts.hard_campaign_runtime.runtime_dependency_manifest",
        lambda: {"python": {"version": "drifted"}},
    )
    with pytest.raises(RuntimeError, match="runtime dependencies"):
        campaign._verify_base_runtime_dependencies(tmp_path)


def _verifier_correction_controller_state(tmp_path: Path):
    row = {
        "index": 0,
        "run_id": "airbnb_r1/corrected",
        "cell_name": "corrected",
        "env": "airbnb",
        "port": 24567,
        "condition": "steered",
        "logical": "gpt-test",
    }
    frozen = tmp_path / "frozen_inputs"
    frozen.mkdir()
    (frozen / "limit_contract.json").write_text(
        json.dumps({"contract": "base"})
    )
    out_dir = campaign._attempt_dir(tmp_path, row, 1)
    out_dir.mkdir(parents=True)
    summary_path = out_dir / "summary.json"
    trajectory_path = out_dir / "trajectory.json"
    db_path = out_dir / f"airbnb_{row['port']}.db"
    summary_path.write_text(json.dumps({
        "outcome": "violation", "preservation_strict": None,
    }))
    trajectory_path.write_text(json.dumps({"steps": [1]}))
    db_path.write_bytes(b"immutable booking")
    launch_path = campaign._launch_receipt_path(tmp_path, row, 1)
    completion_path = campaign._completion_receipt_path(tmp_path, row, 1)
    launch_path.parent.mkdir(parents=True)
    completion_path.parent.mkdir(parents=True)
    launch_path.write_text(json.dumps({
        "run_index": 0, "run_id": row["run_id"], "attempt": 1,
    }))
    recorded = {
        "class": "scientific_invalid",
        "code": "invalid_score_backfill",
        "evidence": "stale verifier",
        "steps": 1,
        "outcome": "violation",
    }
    completion_path.write_text(json.dumps({
        "run_index": 0,
        "run_id": row["run_id"],
        "attempt": 1,
        "classification": recorded,
        "returncode": 0,
    }))
    corrected = {
        "class": "scored",
        "code": "measured_transaction",
        "evidence": "",
        "steps": 1,
        "outcome": "violation",
    }
    proof = {
        "derived_summary": {
            "outcome": "violation",
            "chosen": "32",
            "chosen_label": "Villa",
            "preservation": 0.7667,
            "preservation_strict": 0.0,
            "strict_binary": 0,
            "literal_hero": 0,
            "hero_identity": "hero",
        }
    }
    item = {
        "run_index": 0,
        "run_id": row["run_id"],
        "env": "airbnb",
        "attempt": 1,
        "promotion_basis": "fresh_read_only_semantic_replay",
        "recorded_classification": recorded,
        "corrected_classification": corrected,
        "launch_receipt": launch_path.relative_to(tmp_path).as_posix(),
        "launch_sha256": campaign._sha_file(launch_path),
        "completion_receipt": completion_path.relative_to(tmp_path).as_posix(),
        "completion_sha256": campaign._sha_file(completion_path),
        "authoritative_artifacts": {
            "summary.json": campaign._sha_file(summary_path),
            "trajectory.json": campaign._sha_file(trajectory_path),
            db_path.name: campaign._sha_file(db_path),
        },
        "verifier_proof": proof,
    }
    continuation_dir = tmp_path / "correction"
    continuation_dir.mkdir()
    (continuation_dir / "continuation_limit_contract.json").write_text(
        json.dumps({"contract": "base"})
    )
    verified = campaign.VerifiedAmendment(
        campaign=tmp_path,
        directory=continuation_dir,
        amendment={
            "amendment_id": campaign.VERIFIER_CORRECTION_AMENDMENT_DIRECTORY
        },
        checkpoint={
            "preserved_terminal_results": [{
                key: value for key, value in item.items()
                if key not in {
                    "recorded_classification", "corrected_classification",
                    "promotion_basis", "verifier_proof",
                }
            }],
            "verifier_corrections": [item],
        },
        scheduler={"active_limits": {}},
        amendment_sha256="e" * 64,
        scheduler_sha256="f" * 64,
    )
    manifest = {
        "campaign": str(tmp_path),
        "campaign_uuid": TEST_CAMPAIGN_UUID,
        "runs": [row],
        "heroes": {"airbnb": {"match_field": "chosen", "identity": "hero"}},
        "_continuation_context": verified,
    }
    return row, manifest, item, corrected, summary_path, completion_path


def test_exact_verifier_correction_promotes_without_retrying_or_rewriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, manifest, item, corrected, summary_path, _ = (
        _verifier_correction_controller_state(tmp_path)
    )
    monkeypatch.setattr(
        campaign, "_audit_attempt_artifact_inventory", lambda *_: None
    )
    monkeypatch.setattr(campaign, "_audit_block_receipts", lambda *_: set())
    monkeypatch.setattr(campaign, "_validate_launch_receipt", lambda *_: [])
    observed = []

    def validate(*_args, **kwargs):
        observed.append(kwargs.get("recorded_classification"))
        return []

    monkeypatch.setattr(campaign, "_validate_completion_receipt", validate)
    monkeypatch.setattr(
        campaign, "_classify_attempt", lambda *_args, **_kwargs: corrected
    )
    before = campaign._sha_file(summary_path)

    pending, final = campaign._prior_state(tmp_path, manifest)
    selected = {
        "attempt": 1,
        "out_dir": str(campaign._attempt_dir(tmp_path, row, 1)),
    }
    derived = campaign._selected_summary(tmp_path, manifest, row, selected)

    assert pending == []
    assert final[0]["classification"] == corrected
    assert observed == [item["recorded_classification"]]
    assert derived["preservation_strict"] == 0.0
    assert derived["strict_binary"] == 0
    assert campaign._sha_file(summary_path) == before


def test_verifier_correction_requires_exact_completion_hash(
    tmp_path: Path,
) -> None:
    row, manifest, _item, _corrected, _summary, completion = (
        _verifier_correction_controller_state(tmp_path)
    )
    assert campaign._checkpointed_verifier_correction_item(
        tmp_path, manifest, row, 1
    ) is not None
    completion.write_text(completion.read_text() + "\n")
    assert campaign._checkpointed_verifier_correction_item(
        tmp_path, manifest, row, 1
    ) is None


def test_protocol_postmortem_inherits_exact_verifier_corrections(
    tmp_path: Path,
) -> None:
    row, manifest, item, _corrected, _summary, _completion = (
        _verifier_correction_controller_state(tmp_path)
    )
    continuation = manifest["_continuation_context"]
    continuation.amendment[
        "amendment_id"
    ] = campaign.PROTOCOL_POSTMORTEM_AMENDMENT_DIRECTORY

    assert campaign._checkpointed_verifier_correction_item(
        tmp_path, manifest, row, 1
    ) == item


def _protocol_recovery_controller_state(tmp_path: Path):
    row = {
        "index": campaign.PROTOCOL_RECOVERY_RUN_INDEX,
        "run_id": campaign.LOSSLESS_READ_STATE_RUN_ID,
        "cell_name": "instacart_lossless_recovery",
        "env": "instacart",
        "condition": "clean",
        "port": 24598,
        "model": "test-model",
        "logical": "test-logical",
        "route_rotation": 0,
        "task_key": "instacart|graded4",
    }
    other = {
        **row,
        "index": campaign.PROTOCOL_RECOVERY_RUN_INDEX + 1,
        "run_id": "instacart_r1/unrelated-clean-run",
        "cell_name": "instacart_unrelated",
        "port": 24599,
    }
    manifest = _bind_manifest(tmp_path, {
        "runs": [row, other],
        "caps": {"max_steps": 12000, "cell_timeout_seconds": 172800},
        "models": {"test-model": {"spec": "test-model"}},
        "tasks": {"instacart|graded4": {"instruction": "test"}},
        "frozen_artifacts": {"code_inventory.json": "frozen"},
        "schedule": {"reserved_external_browser_roots": 0},
    })
    frozen = tmp_path / "frozen_inputs"
    frozen.mkdir()
    base_contract = {"generation": "base"}
    (frozen / "limit_contract.json").write_text(json.dumps(base_contract))
    (frozen / "environment_policy.json").write_text(json.dumps({
        "inherit_exact": {},
        "set": {
            "STOREFRONT_OPS_TOKEN": "",
            # Prove the controller tombstones ambient/policy activation for
            # every run not carrying the exact amendment authorization.
            campaign.LOSSLESS_READ_STATE_RECOVERY_ENV: "unauthorized",
        },
    }))

    continuation_dir = tmp_path / "protocol_recovery_002"
    continuation_dir.mkdir()
    active_contract = {"generation": "protocol_recovery_002"}
    (continuation_dir / "continuation_limit_contract.json").write_text(
        json.dumps(active_contract)
    )
    retry = {
        "run_index": row["index"],
        "run_id": row["run_id"],
        "attempt": 1,
        "superseded_by_attempt": 2,
        "completion_sha256": (
            campaign.LOSSLESS_READ_STATE_ATTEMPT1_COMPLETION_SHA256
        ),
        "authoritative_artifacts": {
            "trajectory.json": (
                campaign.LOSSLESS_READ_STATE_ATTEMPT1_TRAJECTORY_SHA256
            ),
        },
        "classification": {
            "class": "scientific_invalid",
            "code": "limit_touched",
            "evidence": "lossy_context_limits.read_state_chars",
        },
    }
    verified = campaign.VerifiedAmendment(
        campaign=tmp_path,
        directory=continuation_dir,
        amendment={
            "amendment_id": campaign.PROTOCOL_RECOVERY_AMENDMENT_DIRECTORY,
            "retry_authorization": {
                "from_attempt": 1,
                "to_attempt": 2,
                "run_indices": [campaign.PROTOCOL_RECOVERY_RUN_INDEX],
                "completion_receipt_sha256": {
                    str(campaign.PROTOCOL_RECOVERY_RUN_INDEX): (
                        campaign.LOSSLESS_READ_STATE_ATTEMPT1_COMPLETION_SHA256
                    ),
                },
                "scope": "exact_hash_allowlist_lossless_read_state_only",
            },
            "hash_chain": {
                "new": {
                    "source_inventory_sha256": "1" * 64,
                    "limit_contract_sha256": "2" * 64,
                }
            },
        },
        checkpoint={"authorized_retry_attempts": [retry]},
        scheduler={"active_limits": {}},
        amendment_sha256="3" * 64,
        scheduler_sha256="4" * 64,
    )
    manifest["_continuation_context"] = verified
    return row, other, manifest, verified, base_contract, active_contract


def test_lossless_read_state_authorization_is_exact_and_singleton(
    tmp_path: Path,
) -> None:
    row, other, manifest, verified, _base, _active = (
        _protocol_recovery_controller_state(tmp_path)
    )
    nonce = "explicitly-bound-cache-nonce"
    expected = {
        "schema": campaign.LOSSLESS_READ_STATE_AUTHORIZATION_SCHEMA,
        "mode": "hash_bound_singleton_retry",
        "campaign_uuid": TEST_CAMPAIGN_UUID,
        "manifest_sha256": campaign._sha_file(
            tmp_path / "campaign_manifest.json"
        ),
        "amendment_sha256": verified.amendment_sha256,
        "scheduler_sha256": verified.scheduler_sha256,
        "run_index": campaign.PROTOCOL_RECOVERY_RUN_INDEX,
        "run_id": campaign.LOSSLESS_READ_STATE_RUN_ID,
        "prior_attempt": 1,
        "attempt": 2,
        "prior_completion_sha256": (
            campaign.LOSSLESS_READ_STATE_ATTEMPT1_COMPLETION_SHA256
        ),
        "prior_trajectory_sha256": (
            campaign.LOSSLESS_READ_STATE_ATTEMPT1_TRAJECTORY_SHA256
        ),
        "cache_nonce_sha256": campaign._sha_bytes(nonce.encode()),
    }
    assert campaign._lossless_read_state_authorization(
        manifest, row, 2, cache_nonce=nonce
    ) == expected

    rebound = campaign._lossless_read_state_authorization(
        manifest, row, 2, cache_nonce=nonce + "-different"
    )
    assert rebound == {
        **expected,
        "cache_nonce_sha256": campaign._sha_bytes(
            (nonce + "-different").encode()
        ),
    }
    assert campaign._lossless_read_state_authorization(manifest, row, 1) is None
    assert campaign._lossless_read_state_authorization(manifest, other, 2) is None
    assert campaign._lossless_read_state_authorization(
        manifest, row, campaign.MAX_ATTEMPTS + 1
    ) is None

    predecessor = campaign.VerifiedAmendment(
        campaign=verified.campaign,
        directory=verified.directory,
        amendment={
            **verified.amendment,
            "amendment_id": campaign.VERIFIER_CORRECTION_AMENDMENT_DIRECTORY,
        },
        checkpoint=verified.checkpoint,
        scheduler=verified.scheduler,
        amendment_sha256=verified.amendment_sha256,
        scheduler_sha256=verified.scheduler_sha256,
    )
    predecessor_manifest = {
        **manifest,
        "_continuation_context": predecessor,
    }
    assert campaign._lossless_read_state_authorization(
        predecessor_manifest, row, 2, cache_nonce=nonce
    ) is None


def _protocol_postmortem_controller_state(tmp_path: Path):
    row, other, manifest, predecessor, base_contract, _active_contract = (
        _protocol_recovery_controller_state(tmp_path)
    )
    postmortem_row = {
        **other,
        "index": campaign.PROTOCOL_POSTMORTEM_RUN_INDEX,
        "run_id": campaign.PROTOCOL_POSTMORTEM_RUN_ID,
        "cell_name": "airbnb_qwen_postmortem_recovery",
        "env": "airbnb",
        "condition": "clean",
        "port": 26026,
        "model": "Qwen3.5-122B",
        "logical": "Qwen3.5-122B",
        "task_key": "airbnb|mixed",
    }
    inherited = predecessor.retry_by_index[
        campaign.PROTOCOL_RECOVERY_RUN_INDEX
    ]
    fresh = {
        "run_index": campaign.PROTOCOL_POSTMORTEM_RUN_INDEX,
        "run_id": campaign.PROTOCOL_POSTMORTEM_RUN_ID,
        "attempt": 1,
        "superseded_by_attempt": 2,
        "completion_sha256": "9" * 64,
        "classification": {
            "class": "scientific_invalid",
            "code": "worker_scope_cleanup_failed",
        },
    }
    continuation_dir = tmp_path / "protocol_recovery_003"
    continuation_dir.mkdir()
    active_contract = {"generation": "protocol_recovery_003"}
    (continuation_dir / "continuation_limit_contract.json").write_text(
        json.dumps(active_contract)
    )
    verified = campaign.VerifiedAmendment(
        campaign=tmp_path,
        directory=continuation_dir,
        amendment={
            "amendment_id": campaign.PROTOCOL_POSTMORTEM_AMENDMENT_DIRECTORY,
            "retry_authorization": {
                "from_attempt": 1,
                "to_attempt": 2,
                "run_indices": [
                    campaign.PROTOCOL_RECOVERY_RUN_INDEX,
                    campaign.PROTOCOL_POSTMORTEM_RUN_INDEX,
                ],
                "inherited_run_indices": [
                    campaign.PROTOCOL_RECOVERY_RUN_INDEX
                ],
                "fresh_run_indices": [
                    campaign.PROTOCOL_POSTMORTEM_RUN_INDEX
                ],
                "completion_receipt_sha256": {
                    str(campaign.PROTOCOL_RECOVERY_RUN_INDEX): (
                        campaign.LOSSLESS_READ_STATE_ATTEMPT1_COMPLETION_SHA256
                    ),
                    str(campaign.PROTOCOL_POSTMORTEM_RUN_INDEX): "9" * 64,
                },
                "scope_by_run_index": {
                    str(campaign.PROTOCOL_RECOVERY_RUN_INDEX): (
                        "exact_hash_allowlist_lossless_read_state_only"
                    ),
                    str(campaign.PROTOCOL_POSTMORTEM_RUN_INDEX): (
                        "exact_hash_allowlist_postmortem_no_checkout_only"
                    ),
                },
            },
            "hash_chain": {
                "new": {
                    "source_inventory_sha256": "5" * 64,
                    "limit_contract_sha256": "6" * 64,
                }
            },
        },
        checkpoint={"authorized_retry_attempts": [inherited, fresh]},
        scheduler={"active_limits": {}},
        amendment_sha256="7" * 64,
        scheduler_sha256="8" * 64,
    )
    manifest["runs"].append(postmortem_row)
    manifest["_continuation_context"] = verified
    return (
        row, other, postmortem_row, manifest, verified,
        base_contract, active_contract,
    )


def test_protocol_postmortem_keeps_lossless_delivery_exclusive_to_598(
    tmp_path: Path,
) -> None:
    row, other, postmortem, manifest, verified, _base, active = (
        _protocol_postmortem_controller_state(tmp_path)
    )
    nonce = "protocol-003-lossless-singleton"
    payload = campaign._lossless_read_state_authorization(
        manifest, row, 2, cache_nonce=nonce
    )
    assert payload is not None
    assert payload["run_index"] == campaign.PROTOCOL_RECOVERY_RUN_INDEX
    assert payload["amendment_sha256"] == verified.amendment_sha256

    for ineligible in (other, postmortem):
        assert campaign._lossless_read_state_authorization(
            manifest, ineligible, 2, cache_nonce=nonce
        ) is None
        child_env = campaign._child_environment(
            manifest, ineligible, list(campaign.REGIONS), 2
        )
        assert campaign.LOSSLESS_READ_STATE_RECOVERY_ENV not in child_env
        assert json.loads(
            child_env["AGENTARENA_LIMIT_CONTRACT_JSON"]
        ) == active

    verified.amendment["retry_authorization"]["run_indices"] = [
        campaign.PROTOCOL_RECOVERY_RUN_INDEX
    ]
    with pytest.raises(RuntimeError, match="malformed read-state authority"):
        campaign._lossless_read_state_authorization(
            manifest, row, 2, cache_nonce=nonce
        )


def test_recovery_child_environment_is_exact_and_absent_elsewhere(
    tmp_path: Path,
) -> None:
    row, other, manifest, _verified, _base, active = (
        _protocol_recovery_controller_state(tmp_path)
    )
    target_env = campaign._child_environment(
        manifest, row, list(campaign.REGIONS), 2
    )
    payload = json.loads(
        target_env[campaign.LOSSLESS_READ_STATE_RECOVERY_ENV]
    )
    assert payload == campaign._lossless_read_state_authorization(
        manifest,
        row,
        2,
        cache_nonce=target_env["AGENTARENA_CACHE_NONCE"],
    )
    assert json.loads(target_env["AGENTARENA_LIMIT_CONTRACT_JSON"]) == active
    assert target_env["AGENTARENA_CACHE_NONCE"].endswith(
        "/attempt_2/continuation-" + "3" * 64
    )

    other_env = campaign._child_environment(
        manifest, other, list(campaign.REGIONS), 2
    )
    assert campaign.LOSSLESS_READ_STATE_RECOVERY_ENV not in other_env


def test_recovery_launch_persists_and_validates_exact_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, other, manifest, _verified, _base, _active = (
        _protocol_recovery_controller_state(tmp_path)
    )
    record = _healthy_probe(finished_epoch=100.0)
    probe_path = tmp_path / "probes" / record["record"]
    probe_path.parent.mkdir(parents=True)
    probe_path.write_text(json.dumps(record))

    class FakeProcess:
        pid = 459802

        def __init__(self):
            self.stdin = io.StringIO()

    process = FakeProcess()
    monkeypatch.setattr(campaign, "_verify_frozen_inputs", lambda *_: None)
    monkeypatch.setattr(
        campaign,
        "_assert_host_resources",
        lambda *_: {
            "external_run_cell_pids": [],
            "external_browser_root_pids": [],
        },
    )
    monkeypatch.setattr(campaign, "_assert_host_admission", lambda *_: {})
    monkeypatch.setattr(campaign, "_latest_probe", lambda *_: record)
    monkeypatch.setattr(campaign, "_listening_ports", lambda: set())
    monkeypatch.setattr(campaign, "_pid_start_ticks", lambda _pid: 98765)
    monkeypatch.setattr(campaign.time, "time", lambda: 101.0)
    monkeypatch.setattr(campaign.subprocess, "Popen", lambda *a, **k: process)

    route = campaign._rotate_route(list(campaign.REGIONS), 1)
    active = campaign._launch_one(
        tmp_path,
        manifest,
        {"row": row, "attempt": 2},
        route,
        set(),
    )
    receipt = json.loads(
        campaign._launch_receipt_path(tmp_path, row, 2).read_text()
    )
    expected = campaign._lossless_read_state_authorization(
        manifest, row, 2, cache_nonce=receipt["cache_nonce"]
    )
    assert receipt["lossless_read_state_recovery_attestation"] == expected
    assert campaign._validate_launch_receipt(
        tmp_path, manifest, row, 2, receipt
    ) == []

    mismatched = {
        **receipt,
        "lossless_read_state_recovery_attestation": {
            **expected,
            "cache_nonce_sha256": "0" * 64,
        },
    }
    assert "launch read-state recovery attestation mismatch" in (
        campaign._validate_launch_receipt(
            tmp_path, manifest, row, 2, mismatched
        )
    )

    unauthorized = {
        **receipt,
        "run_index": other["index"],
        "run_id": other["run_id"],
        "out_dir": str(campaign._attempt_dir(tmp_path, other, 2).resolve()),
        "port": other["port"],
        "cache_nonce": campaign._cache_nonce(manifest, other, 2),
    }
    errors = campaign._validate_launch_receipt(
        tmp_path, manifest, other, 2, unauthorized
    )
    assert "launch receipt has an unauthorized read-state recovery" in errors
    active["log_handle"].close()


def test_lossless_read_state_proof_has_exact_read_only_schema(
    tmp_path: Path,
) -> None:
    row, _other, manifest, _verified, _base, _active = (
        _protocol_recovery_controller_state(tmp_path)
    )
    out_dir = campaign._attempt_dir(tmp_path, row, 1)
    out_dir.mkdir(parents=True)
    contract = campaign._clone_limit_contract()
    limit_audit = _audit(contract)
    raw = limit_audit["categories"]["lossy_context_limits"][
        "read_state_chars"
    ]
    raw["touched_count"] = 1
    raw["observations"].update({
        "touched_count": 1,
        "max_observed": 125074,
    })
    summary = {
        "outcome": "compliant",
        "preservation_strict": 1.0,
        "strict_binary": 1,
        "literal_hero": 1,
        "hero_identity": "IC-ORG-MESCLUN",
        "chosen": "IC-ORG-MESCLUN",
    }
    trajectory = {
        "stats": {
            "context_cap_audit": {
                "complete": True,
                "limits": {
                    "read_state_chars": {
                        "configured": 60000,
                        "touched_count": 1,
                        "max_observed": 125074,
                    }
                },
            },
            "limit_audit": limit_audit,
        }
    }
    summary_path = out_dir / "summary.json"
    trajectory_path = out_dir / "trajectory.json"
    summary_path.write_text(json.dumps(summary))
    trajectory_path.write_text(json.dumps(trajectory))
    before = {
        path.name: campaign._sha_file(path)
        for path in (summary_path, trajectory_path)
    }
    launch = {
        "run_index": row["index"],
        "run_id": row["run_id"],
        "attempt": 1,
    }
    completion = {
        **launch,
        "classification": {
            "class": "scientific_invalid",
            "code": "limit_touched",
            "evidence": "lossy_context_limits.read_state_chars",
        },
    }

    proof = campaign._lossless_read_state_proof(
        tmp_path, manifest, row, 1, out_dir, launch, completion
    )
    assert proof == {
        "schema": campaign.LOSSLESS_READ_STATE_PROOF_SCHEMA,
        "read_only_replay": True,
        "run_index": campaign.PROTOCOL_RECOVERY_RUN_INDEX,
        "run_id": campaign.LOSSLESS_READ_STATE_RUN_ID,
        "attempt": 1,
        "trajectory_sha256": campaign._sha_file(trajectory_path),
        "cause": "lossy_browser_use_read_state_history_rendering",
        "raw_read_state": {
            "configured_chars": 60000,
            "touched_count": 1,
            "max_observed_chars": 125074,
        },
        "raw_limit_audit": {
            "limit_audit_complete": True,
            "only_lossy_limit_touched": "read_state_chars",
            "raw_context_audit_matches": True,
            "all_safety_backstops_untouched": True,
        },
        "measured_result": summary,
        "remedy": {
            "activation_scope": "run_598_attempt_2_only",
            "delivery": "lossless_read_state",
            "action_results_limit": "unchanged",
            "all_other_runs": "unchanged",
            "catalog_tasks_and_steering": "unchanged",
            "upstream_guards": "fail_closed",
        },
    }
    assert {
        path.name: campaign._sha_file(path)
        for path in (summary_path, trajectory_path)
    } == before


def test_predecessor_launch_uses_base_contract_not_active_successor_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _target, row, manifest, active, base_contract, active_contract = (
        _protocol_recovery_controller_state(tmp_path)
    )
    manifest["runs"] = [row]
    out_dir = campaign._attempt_dir(tmp_path, row, 1)
    out_dir.mkdir(parents=True)
    launch_path = campaign._launch_receipt_path(tmp_path, row, 1)
    completion_path = campaign._completion_receipt_path(tmp_path, row, 1)
    launch_path.parent.mkdir(parents=True)
    completion_path.parent.mkdir(parents=True)
    predecessor = campaign.VerifiedAmendment(
        campaign=tmp_path,
        directory=active.directory,
        amendment=active.amendment,
        checkpoint=active.checkpoint,
        scheduler=active.scheduler,
        amendment_sha256="5" * 64,
        scheduler_sha256="6" * 64,
    )
    predecessor_attestation = campaign._expected_continuation_attestation(
        tmp_path, predecessor
    )
    active_attestation = campaign._expected_continuation_attestation(
        tmp_path, active
    )
    launch_path.write_text(json.dumps({
        "continuation_attestation": predecessor_attestation,
    }))
    completion_path.write_text(json.dumps({"returncode": 0}))
    assert not campaign._is_active_continuation_launch(
        tmp_path, active, {"continuation_attestation": predecessor_attestation}
    )
    assert campaign._is_active_continuation_launch(
        tmp_path, active, {"continuation_attestation": active_attestation}
    )

    observed = []
    result = {"class": "scored", "code": "measured_transaction", "evidence": ""}
    monkeypatch.setattr(campaign, "_audit_attempt_artifact_inventory", lambda *_: None)
    monkeypatch.setattr(campaign, "_audit_block_receipts", lambda *_: set())
    monkeypatch.setattr(campaign, "_validate_launch_receipt", lambda *_: [])
    monkeypatch.setattr(campaign, "_validate_completion_receipt", lambda *_a, **_k: [])

    def classify(_out, contract, *_args, **_kwargs):
        observed.append(contract)
        return result

    monkeypatch.setattr(campaign, "_classify_attempt", classify)
    pending, final = campaign._prior_state(tmp_path, manifest)
    assert pending == []
    assert final[row["index"]]["classification"] == result
    assert observed == [base_contract]

    launch_path.write_text(json.dumps({
        "continuation_attestation": active_attestation,
    }))
    observed.clear()
    pending, final = campaign._prior_state(tmp_path, manifest)
    assert pending == []
    assert final[row["index"]]["classification"] == result
    assert observed == [active_contract]


def test_active_continuation_preserves_checkpoint_and_queues_exact_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen = tmp_path / "frozen_inputs"
    frozen.mkdir()
    base_contract = {"contract": "base"}
    (frozen / "limit_contract.json").write_text(json.dumps(base_contract))
    (frozen / "environment_policy.json").write_text(
        json.dumps({"inherit_exact": {}, "set": {}})
    )
    continuation_dir = tmp_path / "continuation"
    continuation_dir.mkdir()
    active_contract = {"contract": "continuation"}
    (continuation_dir / "continuation_limit_contract.json").write_text(
        json.dumps(active_contract)
    )
    rows = [
        {
            "index": index,
            "run_id": f"ebay_r1/run_{index}",
            "cell_name": f"cell_{index}",
            "condition": "steered",
            "logical": "gpt-test",
        }
        for index in range(2)
    ]
    (tmp_path / "campaign_manifest.json").write_text("{}")
    checkpoint_items = []
    for row in rows:
        out_dir = campaign._attempt_dir(tmp_path, row, 1)
        out_dir.mkdir(parents=True)
        launch_path = campaign._launch_receipt_path(tmp_path, row, 1)
        completion_path = campaign._completion_receipt_path(tmp_path, row, 1)
        launch_path.parent.mkdir(exist_ok=True)
        completion_path.parent.mkdir(exist_ok=True)
        launch_path.write_text(json.dumps({"run_index": row["index"]}))
        completion_path.write_text(json.dumps({"run_index": row["index"]}))
        checkpoint_items.append({
            "run_index": row["index"],
            "run_id": row["run_id"],
            "attempt": 1,
            "launch_receipt": launch_path.relative_to(tmp_path).as_posix(),
            "launch_sha256": campaign._sha_file(launch_path),
            "completion_receipt": completion_path.relative_to(tmp_path).as_posix(),
            "completion_sha256": campaign._sha_file(completion_path),
        })
    interrupted_classification = {
        "class": "scientific_invalid",
        "code": "unpersisted_or_incomplete_attempt",
        "evidence": "operator-stopped checkpoint",
    }
    checkpoint_items[0]["classification"] = {
        "class": "scored", "code": "measured_transaction", "evidence": ""
    }
    checkpoint_items[1].update({
        "classification": interrupted_classification,
        "superseded_by_attempt": 2,
    })
    verified = campaign.VerifiedAmendment(
        campaign=tmp_path,
        directory=continuation_dir,
        amendment={},
        checkpoint={
            "preserved_terminal_results": [checkpoint_items[0]],
            "interrupted_attempts": [checkpoint_items[1]],
        },
        scheduler={"active_limits": {}},
        amendment_sha256="a" * 64,
        scheduler_sha256="b" * 64,
    )
    manifest = {
        "campaign": str(tmp_path),
        "campaign_uuid": TEST_CAMPAIGN_UUID,
        "runs": rows,
        "scratch": {"root": str(tmp_path / "scratch")},
        "_continuation_context": verified,
    }

    monkeypatch.setattr(campaign, "_audit_block_receipts", lambda *_: set())
    monkeypatch.setattr(campaign, "_validate_launch_receipt", lambda *_: [])
    monkeypatch.setattr(campaign, "_validate_completion_receipt", lambda *_: [])
    monkeypatch.setattr(
        campaign,
        "_classify_attempt",
        lambda _out, _contract, _manifest, row, **_kwargs: (
            checkpoint_items[row["index"]]["classification"]
        ),
    )
    pending, final = campaign._prior_state(tmp_path, manifest)
    assert [(item["row"]["index"], item["attempt"]) for item in pending] == [
        (1, 2)
    ]
    assert final[0]["attempt"] == 1
    assert final[0]["classification"]["class"] == "scored"
    assert campaign._validate_scratch_cleanup_attestation(
        tmp_path,
        manifest,
        rows[0],
        1,
        json.loads(campaign._launch_receipt_path(tmp_path, rows[0], 1).read_text()),
        json.loads(
            campaign._completion_receipt_path(tmp_path, rows[0], 1).read_text()
        ),
        checkpoint_items[0]["classification"],
    ) == []

    campaign._write_status(
        tmp_path,
        manifest,
        state="running",
        pending=[],
        retries=pending,
        running={},
        final=final,
        opened_blocks=set(),
        jobs=2,
        stagger=10,
    )
    status = json.loads((tmp_path / "status.json").read_text())
    assert status[campaign.STATUS_AMENDMENT_FIELD] == "a" * 64
    assert status[campaign.STATUS_SCHEDULER_FIELD] == "b" * 64

    child_manifest = {**manifest, "scratch": {"root": str(tmp_path / "scratch")}}
    child_env = campaign._child_environment(
        child_manifest,
        {**rows[1], "port": 24000},
        ["gcr/shared", "msraif/shared"],
        2,
    )
    assert json.loads(child_env["AGENTARENA_LIMIT_CONTRACT_JSON"]) == active_contract
    assert (
        campaign._sha_bytes(
            child_env["AGENTARENA_LIMIT_CONTRACT_JSON"].encode()
        )
        != campaign._sha_bytes(
            json.dumps(base_contract, separators=(",", ":")).encode()
        )
    )


def _c3_recovery_state(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(campaign, "RECOVERY_RUN_INDEX", 0)
    row = _controller_row()
    manifest = _bind_manifest(
        tmp_path,
        {
            "runs": [row],
            "tasks": {row["task_key"]: {}},
        },
    )
    out_dir = campaign._attempt_dir(tmp_path, row, 1)
    out_dir.mkdir(parents=True)
    launch_path = campaign._launch_receipt_path(tmp_path, row, 1)
    completion_path = campaign._completion_receipt_path(tmp_path, row, 1)
    launch_path.parent.mkdir(parents=True)
    completion_path.parent.mkdir(parents=True)
    old_nonce = "c2-bound-nonce"
    launch = {
        "pid": 5151,
        "pid_start_ticks": 12345,
        "out_dir": str(out_dir.resolve()),
        "route_order": list(campaign.REGIONS),
        "cache_nonce": old_nonce,
    }
    launch_path.write_text(json.dumps(launch))
    cleanup = _green_cleanup(
        SimpleNamespace(pid=5151), 12345, old_nonce, row["port"]
    )
    cleanup.update({
        "uncertified_scope_pids": [6161],
        "error": "numeric PGID/SID member lacked nonce certification",
    })
    classification = {
        "class": "scientific_invalid",
        "code": "worker_scope_cleanup_failed",
        "evidence": "uncertified descendant",
    }
    completion = {
        "schema": campaign.COMPLETION_RECEIPT_SCHEMA,
        "run_index": row["index"],
        "run_id": row["run_id"],
        "attempt": 1,
        "pid": 5151,
        "returncode": -campaign.signal.SIGKILL,
        "classification": classification,
        "route_order": list(campaign.REGIONS),
        "worker_scope_cleanup": cleanup,
        "no_checkout_proof": None,
    }
    completion_path.write_text(json.dumps(completion))
    item = {
        "run_index": 0,
        "run_id": row["run_id"],
        "attempt": 1,
        "authorization_basis": "postmortem_operator_sigkill",
        "classification": classification,
        "superseded_by_attempt": 2,
        "launch_receipt": launch_path.relative_to(tmp_path).as_posix(),
        "launch_sha256": campaign._sha_file(launch_path),
        "completion_receipt": completion_path.relative_to(tmp_path).as_posix(),
        "completion_sha256": campaign._sha_file(completion_path),
    }
    continuation_dir = tmp_path / "continuation"
    continuation_dir.mkdir()
    contract = {"contract": "c3"}
    (continuation_dir / "continuation_limit_contract.json").write_text(
        json.dumps(contract)
    )
    verified = campaign.VerifiedAmendment(
        campaign=tmp_path,
        directory=continuation_dir,
        amendment={"amendment_id": campaign.RECOVERY_AMENDMENT_DIRECTORY},
        checkpoint={
            "preserved_terminal_results": [],
            "authorized_retry_attempts": [item],
        },
        scheduler={"active_limits": {}},
        amendment_sha256="c" * 64,
        scheduler_sha256="d" * 64,
    )
    manifest["_continuation_context"] = verified
    frozen = tmp_path / "frozen_inputs"
    frozen.mkdir()
    (frozen / "limit_contract.json").write_text(json.dumps({"contract": "base"}))
    return row, manifest, launch, completion, item


def _protocol_postmortem_recovery_state(tmp_path: Path, monkeypatch):
    row, manifest, launch, completion, item = _c3_recovery_state(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(campaign, "PROTOCOL_POSTMORTEM_RUN_INDEX", 0)
    monkeypatch.setattr(
        campaign, "PROTOCOL_POSTMORTEM_RUN_ID", row["run_id"]
    )
    continuation = manifest["_continuation_context"]
    continuation.amendment[
        "amendment_id"
    ] = campaign.PROTOCOL_POSTMORTEM_AMENDMENT_DIRECTORY

    predecessor_attestation = {
        "amendment_sha256": "1" * 64,
        "scheduler_sha256": "2" * 64,
    }
    finished_utc = "2026-08-03T15:12:34Z"
    launch.update({"continuation_attestation": predecessor_attestation})
    completion.update({"finished_utc": finished_utc})
    launch_path = campaign._launch_receipt_path(tmp_path, row, 1)
    completion_path = campaign._completion_receipt_path(tmp_path, row, 1)
    launch_path.write_text(json.dumps(launch))
    completion_path.write_text(json.dumps(completion))

    out_dir = campaign._attempt_dir(tmp_path, row, 1)
    artifact_paths = {
        "run.log": out_dir / "run.log",
        "environment_server.log": out_dir / "environment_server.log",
        f"{row['env']}_{row['port']}.db": (
            out_dir / f"{row['env']}_{row['port']}.db"
        ),
    }
    for name, path in artifact_paths.items():
        path.write_text(f"immutable {name}")
    scratch_path = campaign._scratch_cleanup_receipt_path(
        tmp_path, row, 1
    )
    scratch_path.parent.mkdir(parents=True, exist_ok=True)
    scratch_path.write_text(json.dumps({
        "status": "skipped_worker_scope_not_green",
        "confirmed_absent": False,
    }))
    completion_sha256 = campaign._sha_file(completion_path)
    monkeypatch.setattr(
        campaign,
        "PROTOCOL_POSTMORTEM_COMPLETION_SHA256",
        completion_sha256,
    )
    item.clear()
    item.update({
        "run_index": row["index"],
        "run_id": row["run_id"],
        "attempt": 1,
        "pid": launch["pid"],
        "finished_utc": finished_utc,
        "launch_receipt": launch_path.relative_to(tmp_path).as_posix(),
        "launch_sha256": campaign._sha_file(launch_path),
        "completion_receipt": completion_path.relative_to(tmp_path).as_posix(),
        "completion_sha256": completion_sha256,
        "classification": completion["classification"],
        "cache_nonce": launch["cache_nonce"],
        "predecessor_attestation": predecessor_attestation,
        "authorization_basis": "postmortem_operator_sigkill",
        "authoritative_artifacts": {
            name: campaign._sha_file(path)
            for name, path in artifact_paths.items()
        },
        "scratch_cleanup_receipt": scratch_path.relative_to(
            tmp_path
        ).as_posix(),
        "scratch_cleanup_sha256": campaign._sha_file(scratch_path),
        "postmortem_proof": {
            "schema": campaign.POSTMORTEM_PROOF_SCHEMA,
            "run_index": row["index"],
            "run_id": row["run_id"],
            "attempt": 1,
            "immutable_completion_sha256": completion_sha256,
            "operator_intervention": True,
            "benchmark_or_harness_changed": False,
            "inactivity_timeout_policy_created": False,
            "trajectory_examined_before_intervention": True,
            "preconfigured_timeout_did_not_fire": True,
            "selection_bias_risk": True,
            "confirmed_recoverable": True,
            "error": None,
        },
        "terminal_artifacts_absent": [
            "summary.json", "trajectory.json",
        ],
        "superseded_by_attempt": 2,
    })
    return row, manifest, launch, completion, item


def test_recovery_postmortem_ignores_same_port_reuse_and_binds_old_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _controller_row()
    row["index"] = campaign.RECOVERY_RUN_INDEX
    manifest = {"scratch": {"root": str(tmp_path / "scratch")}}
    out_dir = campaign._attempt_dir(tmp_path, row, 1)
    out_dir.mkdir(parents=True)
    completion_path = campaign._completion_receipt_path(tmp_path, row, 1)
    completion_path.parent.mkdir(parents=True)
    completion_path.write_text("immutable-c2-completion")
    launch = {
        "pid": 5151,
        "pid_start_ticks": 12345,
        "cache_nonce": "c2-bound-nonce",
        "port": row["port"],
    }
    no_checkout = {
        "confirmed_no_checkout": True,
        "database_sha256": "a" * 64,
    }
    quarantine = {
        "confirmed_quarantined": True,
        "tree_sha256": "b" * 64,
        "path": "r0368a1",
    }
    calls = []
    monkeypatch.setattr(campaign, "_no_checkout_proof", lambda *_: no_checkout)
    monkeypatch.setattr(
        campaign, "_no_checkout_proof_is_green", lambda *args: True
    )
    monkeypatch.setattr(campaign, "_pid_start_ticks", lambda _pid: 99999)
    monkeypatch.setattr(campaign, "_exact_nonce_pids", lambda nonce: [])

    def scope(pid, nonce):
        calls.append((pid, nonce))
        return {}

    monkeypatch.setattr(campaign, "_worker_scope_snapshot", scope)
    monkeypatch.setattr(
        campaign, "_retained_scratch_quarantine_proof",
        lambda *_: quarantine,
    )
    monkeypatch.setattr(
        campaign, "_listening_ports",
        lambda: (_ for _ in ()).throw(
            AssertionError("postmortem must not treat same-port a2 as C2 scope")
        ),
    )

    proof = campaign._index368_postmortem_proof(
        tmp_path, manifest, out_dir, row, launch, {}
    )

    assert calls == [(5151, "c2-bound-nonce")]
    assert proof["confirmed_recoverable"] is True
    assert proof["process_absence"]["worker_scope_snapshot"] == []
    assert proof["no_checkout"] == no_checkout
    assert proof["scratch_quarantine"] == quarantine


def test_exact_c3_invalid_checkpoint_queues_attempt_two_without_reclassifying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, manifest, _launch, _completion, _item = _c3_recovery_state(
        tmp_path, monkeypatch
    )
    monkeypatch.setattr(
        campaign, "_audit_attempt_artifact_inventory", lambda *args: None
    )
    monkeypatch.setattr(campaign, "_audit_block_receipts", lambda *args: set())
    monkeypatch.setattr(campaign, "_validate_launch_receipt", lambda *args: [])
    monkeypatch.setattr(
        campaign,
        "_classify_attempt",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("exact C3 recovery must not be generically reclassified")
        ),
    )

    pending, final = campaign._prior_state(tmp_path, manifest)

    assert final == {}
    assert [(item["row"]["index"], item["attempt"]) for item in pending] == [
        (0, 2)
    ]
    assert campaign._cache_nonce(manifest, row, 2).endswith(
        "/continuation-" + "c" * 64
    )


def test_completion_no_checkout_exception_requires_exact_c3_hash_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, manifest, launch, completion, item = _c3_recovery_state(
        tmp_path, monkeypatch
    )
    classification = item["classification"]
    assert campaign._validate_completion_receipt(
        manifest, row, 1, launch, completion, classification
    ) == []

    manifest["_continuation_context"].amendment[
        "amendment_id"
    ] = "continuation_002"
    errors = campaign._validate_completion_receipt(
        manifest, row, 1, launch, completion, classification
    )
    assert "completion no-checkout proof differs from attempt DB" in errors

    manifest["_continuation_context"].amendment[
        "amendment_id"
    ] = campaign.RECOVERY_AMENDMENT_DIRECTORY
    item["completion_sha256"] = "0" * 64
    errors = campaign._validate_completion_receipt(
        manifest, row, 1, launch, completion, classification
    )
    assert "completion no-checkout proof differs from attempt DB" in errors


def test_exact_protocol_postmortem_checkpoint_queues_ordinary_attempt_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, manifest, launch, completion, item = (
        _protocol_postmortem_recovery_state(tmp_path, monkeypatch)
    )
    monkeypatch.setattr(
        campaign, "_audit_attempt_artifact_inventory", lambda *_: None
    )
    monkeypatch.setattr(campaign, "_audit_block_receipts", lambda *_: set())
    monkeypatch.setattr(campaign, "_validate_launch_receipt", lambda *_: [])
    monkeypatch.setattr(
        campaign,
        "_classify_attempt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError(
                "exact postmortem recovery must not be generically reclassified"
            )
        ),
    )

    assert campaign._checkpointed_invalid_recovery_item(
        tmp_path, manifest, row, 1
    ) == item
    assert campaign._validate_completion_receipt(
        manifest, row, 1, launch, completion, item["classification"]
    ) == []
    pending, final = campaign._prior_state(tmp_path, manifest)

    assert final == {}
    assert [(entry["row"]["index"], entry["attempt"]) for entry in pending] == [
        (row["index"], 2)
    ]


def test_protocol_postmortem_null_checkout_exception_is_proof_and_hash_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, manifest, launch, completion, item = (
        _protocol_postmortem_recovery_state(tmp_path, monkeypatch)
    )
    classification = item["classification"]

    item["postmortem_proof"]["selection_bias_risk"] = False
    assert campaign._checkpointed_invalid_recovery_item(
        tmp_path, manifest, row, 1
    ) is None
    item["postmortem_proof"]["selection_bias_risk"] = True
    item["postmortem_proof"]["confirmed_recoverable"] = False
    assert campaign._checkpointed_invalid_recovery_item(
        tmp_path, manifest, row, 1
    ) is None
    errors = campaign._validate_completion_receipt(
        manifest, row, 1, launch, completion, classification
    )
    assert "completion no-checkout proof differs from attempt DB" in errors

    item["postmortem_proof"]["confirmed_recoverable"] = True
    run_log = campaign._attempt_dir(tmp_path, row, 1) / "run.log"
    run_log.write_text(run_log.read_text() + "tampered")
    assert campaign._checkpointed_invalid_recovery_item(
        tmp_path, manifest, row, 1
    ) is None


@pytest.mark.parametrize("state", ["stopped", "running", "complete"])
def test_recovery_prepare_rejects_every_non_protocol_invalid_frontier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    predecessor = campaign.VerifiedAmendment(
        campaign=tmp_path,
        directory=tmp_path / "continuation_002",
        amendment={"amendment_id": "continuation_002"},
        checkpoint={
            "preserved_terminal_results": [],
            "authorized_retry_attempts": [],
        },
        scheduler={"active_limits": {}},
        amendment_sha256="a" * 64,
        scheduler_sha256="b" * 64,
    )
    monkeypatch.setattr(campaign, "load_amendment", lambda *_: predecessor)
    monkeypatch.setattr(
        campaign, "_acquire_launcher_lock", lambda *_: _ControllerLock()
    )
    monkeypatch.setattr(
        campaign, "_acquire_host_browser_lock", lambda *_: _ControllerLock()
    )
    status = {
        "state": state,
        "running": [],
        "running_count": 0,
        "total_runs": campaign.TOTAL_RUNS,
        "final_count": 1,
        "pending_primary": campaign.TOTAL_RUNS - 1,
        "pending_refill": 0,
        "final_classes": {"scientific_invalid": 1},
        campaign.STATUS_AMENDMENT_FIELD: predecessor.amendment_sha256,
        campaign.STATUS_SCHEDULER_FIELD: predecessor.scheduler_sha256,
    }
    (tmp_path / "status.json").write_text(json.dumps(status))

    with pytest.raises(
        RuntimeError, match="exact quiescent C2 protocol-invalid frontier"
    ):
        campaign.prepare_recovery_continuation(
            SimpleNamespace(
                campaign=tmp_path,
                scratch_root=tmp_path / "scratch",
            )
        )


def _protocol_postmortem_prepare_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        campaign, "TOTAL_RUNS", campaign.PROTOCOL_POSTMORTEM_MANIFEST_ROWS
    )
    rows = [
        {
            "index": index,
            "run_id": f"airbnb_r1/placeholder-{index}",
            "cell_name": f"placeholder-{index}",
            "env": "airbnb",
            "condition": "clean",
            "model": "test-model",
            "port": 30000 + index,
        }
        for index in range(campaign.PROTOCOL_POSTMORTEM_MANIFEST_ROWS)
    ]
    rows[campaign.PROTOCOL_RECOVERY_RUN_INDEX].update({
        "run_id": campaign.LOSSLESS_READ_STATE_RUN_ID,
        "env": "instacart",
    })
    rows[campaign.PROTOCOL_POSTMORTEM_RUN_INDEX].update({
        "run_id": campaign.PROTOCOL_POSTMORTEM_RUN_ID,
        "env": "airbnb",
        "condition": "clean",
        "model": "Qwen3.5-122B",
        "port": 26026,
    })
    scratch_root = tmp_path / "scratch"
    scratch_root.mkdir()
    base = {
        "campaign": str(tmp_path),
        "campaign_uuid": TEST_CAMPAIGN_UUID,
        "total_runs": campaign.PROTOCOL_POSTMORTEM_MANIFEST_ROWS,
        "runs": rows,
        "scratch": {
            "schema": campaign.SCRATCH_POLICY_SCHEMA,
            "root": str(scratch_root),
        },
    }
    manifest_path = tmp_path / "campaign_manifest.json"
    manifest_path.write_text(json.dumps(base))
    (tmp_path / "campaign_manifest.sha256").write_text(
        campaign._sha_file(manifest_path) + "  campaign_manifest.json\n"
    )
    predecessor_directory = tmp_path / "protocol_recovery_002"
    predecessor_directory.mkdir()
    predecessor_limit = {"limit": "same"}
    scheduler_contract = {
        "scheduler": "unchanged",
        "active_limits": {
            "jobs": 160,
            "spawn_stagger_seconds": 10,
        },
    }
    (predecessor_directory / "continuation_limit_contract.json").write_text(
        json.dumps(predecessor_limit)
    )
    (
        predecessor_directory / "continuation_scheduler_contract.json"
    ).write_text(json.dumps(scheduler_contract))
    predecessor = campaign.VerifiedAmendment(
        campaign=tmp_path,
        directory=predecessor_directory,
        amendment={
            "amendment_id": campaign.PROTOCOL_RECOVERY_AMENDMENT_DIRECTORY
        },
        checkpoint={
            "preserved_terminal_results": [],
            "authorized_retry_attempts": [],
        },
        scheduler=scheduler_contract,
        amendment_sha256="a" * 64,
        scheduler_sha256="b" * 64,
    )
    status = {
        "state": "protocol_invalid",
        "running": [],
        "running_count": 0,
        "pending_primary": (
            campaign.PROTOCOL_POSTMORTEM_PENDING_PRIMARY_ROWS
        ),
        "pending_refill": len(
            campaign.PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES
        ),
        "final_count": campaign.PROTOCOL_POSTMORTEM_FRONTIER_ROWS - 1,
        "total_runs": campaign.PROTOCOL_POSTMORTEM_MANIFEST_ROWS,
        "final_classes": {
            "behavioral": 139,
            "scientific_invalid": 1,
            "scored": 804,
        },
        "final_by_condition": {"clean": 464, "steered": 480},
        campaign.STATUS_AMENDMENT_FIELD: predecessor.amendment_sha256,
        campaign.STATUS_SCHEDULER_FIELD: predecessor.scheduler_sha256,
    }
    (tmp_path / "status.json").write_text(json.dumps(status))
    launcher_lock = _ControllerLock()
    host_lock = _ControllerLock()
    monkeypatch.setattr(campaign, "load_amendment", lambda *_: predecessor)
    monkeypatch.setattr(
        campaign, "_acquire_launcher_lock", lambda *_: launcher_lock
    )
    monkeypatch.setattr(
        campaign, "_acquire_host_browser_lock", lambda *_: host_lock
    )
    monkeypatch.setattr(campaign, "_assert_host_resources", lambda *_: {})
    monkeypatch.setattr(campaign, "_campaign_nonce_pids", lambda *_: [])
    monkeypatch.setattr(campaign, "_listening_ports", lambda: set())
    monkeypatch.setattr(
        campaign,
        "_protocol_postmortem_frontier_attestation",
        lambda *_: {"frontier": "exact"},
    )
    monkeypatch.setattr(
        campaign, "_validate_scratch_policy", lambda *_: scratch_root
    )
    monkeypatch.setattr(
        campaign,
        "_continuation_scheduler_contract",
        lambda *_: scheduler_contract,
    )
    monkeypatch.setattr(
        campaign, "_clone_code_inventory", lambda: {"source": "new"}
    )
    monkeypatch.setattr(
        campaign, "_clone_limit_contract", lambda *_: predecessor_limit
    )
    return base, status, predecessor, launcher_lock, host_lock, scratch_root


def test_protocol_postmortem_frontier_binds_aggregates_and_missing_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(campaign, "ROOT", tmp_path)
    campaign_dir = tmp_path / "results" / "synthetic-protocol-frontier"
    campaign_dir.mkdir(parents=True)
    rows = [
        {
            "index": index,
            "run_id": f"airbnb_r1/synthetic-{index}",
            "cell_name": f"synthetic-{index}",
            "env": "airbnb",
            "port": 30000 + index,
        }
        for index in range(campaign.PROTOCOL_POSTMORTEM_MANIFEST_ROWS)
    ]
    manifest = {"runs": rows}
    present = (
        frozenset(range(campaign.PROTOCOL_POSTMORTEM_MANIFEST_ROWS))
        - campaign.PROTOCOL_POSTMORTEM_MISSING_PRIMARY_INDICES
    )
    path_functions = (
        campaign._launch_receipt_path,
        campaign._completion_receipt_path,
        campaign._scratch_cleanup_receipt_path,
    )
    for index in sorted(present):
        row = rows[index]
        payload = {
            "run_index": index,
            "run_id": row["run_id"],
            "attempt": 1,
        }
        for path_function in path_functions:
            path = path_function(campaign_dir, row, 1)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, sort_keys=True))

    completion_642 = campaign._completion_receipt_path(
        campaign_dir, rows[campaign.PROTOCOL_POSTMORTEM_RUN_INDEX], 1
    )
    monkeypatch.setattr(
        campaign,
        "PROTOCOL_POSTMORTEM_COMPLETION_SHA256",
        campaign._sha_file(completion_642),
    )
    for attribute, directory_name in (
        (
            "PROTOCOL_POSTMORTEM_LAUNCH_RECEIPT_AGGREGATE_SHA256",
            "launch_receipts",
        ),
        (
            "PROTOCOL_POSTMORTEM_COMPLETION_RECEIPT_AGGREGATE_SHA256",
            "completion_receipts",
        ),
        (
            "PROTOCOL_POSTMORTEM_SCRATCH_RECEIPT_AGGREGATE_SHA256",
            "scratch_cleanup_receipts",
        ),
    ):
        paths = sorted((campaign_dir / directory_name).iterdir())
        monkeypatch.setattr(
            campaign,
            attribute,
            campaign._sha256sum_record_aggregate(paths),
        )

    attestation = campaign._protocol_postmortem_frontier_attestation(
        campaign_dir, manifest
    )
    assert attestation["missing_primary_indices"] == sorted(
        campaign.PROTOCOL_POSTMORTEM_MISSING_PRIMARY_INDICES
    )
    assert len(attestation["present_indices"]) == 945

    completion_642.write_text(completion_642.read_text() + "\n")
    with pytest.raises(RuntimeError, match="completion_receipts aggregate"):
        campaign._protocol_postmortem_frontier_attestation(
            campaign_dir, manifest
        )


def test_protocol_postmortem_prepare_wires_exact_create_only_successor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _base, _status, predecessor, launcher_lock, host_lock, scratch_root = (
        _protocol_postmortem_prepare_state(tmp_path, monkeypatch)
    )
    observed = {}

    def create(campaign_path, **kwargs):
        observed["campaign"] = campaign_path
        observed.update(kwargs)
        directory = (
            tmp_path / "continuation_amendments"
            / campaign.PROTOCOL_POSTMORTEM_AMENDMENT_DIRECTORY
        )
        directory.mkdir(parents=True)
        (directory / "continuation_limit_contract.json").write_text(
            json.dumps(kwargs["continuation_limit_contract"])
        )
        (directory / "continuation_scheduler_contract.json").write_text(
            json.dumps(kwargs["continuation_scheduler_contract"])
        )
        retry_indices = sorted(
            campaign.PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES
            | {campaign.PROTOCOL_POSTMORTEM_RUN_INDEX}
        )
        retry_authorization = {
            "from_attempt": 1,
            "to_attempt": 2,
            "run_indices": retry_indices,
            "inherited_run_indices": sorted(
                campaign.PROTOCOL_POSTMORTEM_PREDECESSOR_RETRY_INDICES
            ),
            "fresh_run_indices": [campaign.PROTOCOL_POSTMORTEM_RUN_INDEX],
            "completion_receipt_sha256": {
                str(campaign.PROTOCOL_RECOVERY_RUN_INDEX): (
                    campaign.LOSSLESS_READ_STATE_ATTEMPT1_COMPLETION_SHA256
                ),
                str(campaign.PROTOCOL_POSTMORTEM_RUN_INDEX): (
                    campaign.PROTOCOL_POSTMORTEM_COMPLETION_SHA256
                ),
            },
            "scope_by_run_index": {
                str(campaign.PROTOCOL_RECOVERY_RUN_INDEX): (
                    "exact_hash_allowlist_lossless_read_state_only"
                ),
                str(campaign.PROTOCOL_POSTMORTEM_RUN_INDEX): (
                    "exact_hash_allowlist_postmortem_no_checkout_only"
                ),
            },
        }
        counts = {
            "checkpoint_completion_receipts": 945,
            "preserved_terminal_rows": 943,
            "authorized_retry_rows": 2,
            "inherited_authorized_retry_rows": 1,
            "fresh_authorized_retry_rows": 1,
            "verifier_corrected_rows": 3,
            "inherited_verifier_corrected_rows": 3,
            "fresh_verifier_corrected_rows": 0,
            "raw_recorded_classes": {
                "behavioral": 139,
                "scientific_invalid": 5,
                "scored": 801,
            },
            "effective_pre_retry_classes": {
                "behavioral": 139,
                "scientific_invalid": 2,
                "scored": 804,
            },
            "selected_frontier_classes": {
                "behavioral": 139,
                "scientific_invalid": 1,
                "scored": 804,
            },
            "selected_frontier_by_condition": {
                "clean": 464,
                "steered": 480,
            },
            "raw_invalid_indices": sorted(
                campaign.PROTOCOL_POSTMORTEM_RAW_INVALID_INDICES
            ),
            "effective_invalid_indices": sorted(
                campaign.PROTOCOL_POSTMORTEM_EFFECTIVE_INVALID_INDICES
            ),
            "unresolved_invalid_indices": sorted(
                campaign.PROTOCOL_POSTMORTEM_UNRESOLVED_INVALID_INDICES
            ),
            "missing_primary_indices": sorted(
                campaign.PROTOCOL_POSTMORTEM_MISSING_PRIMARY_INDICES
            ),
            "pending_primary_rows": (
                campaign.PROTOCOL_POSTMORTEM_PENDING_PRIMARY_ROWS
            ),
        }
        retries = [
            {
                "run_index": campaign.PROTOCOL_RECOVERY_RUN_INDEX,
                "attempt": 1,
                "superseded_by_attempt": 2,
                "completion_sha256": (
                    campaign.LOSSLESS_READ_STATE_ATTEMPT1_COMPLETION_SHA256
                ),
            },
            {
                "run_index": campaign.PROTOCOL_POSTMORTEM_RUN_INDEX,
                "attempt": 1,
                "superseded_by_attempt": 2,
                "completion_sha256": (
                    campaign.PROTOCOL_POSTMORTEM_COMPLETION_SHA256
                ),
                "postmortem_proof": {
                    "trajectory_examined_before_intervention": True,
                    "preconfigured_timeout_did_not_fire": True,
                    "selection_bias_risk": True,
                },
            },
        ]
        return campaign.VerifiedAmendment(
            campaign=tmp_path,
            directory=directory,
            amendment={
                "amendment_id": (
                    campaign.PROTOCOL_POSTMORTEM_AMENDMENT_DIRECTORY
                ),
                "base_manifest": {
                    "total_runs": campaign.PROTOCOL_POSTMORTEM_MANIFEST_ROWS
                },
                "retry_authorization": retry_authorization,
                "denominator": {
                    "manifest_rows": (
                        campaign.PROTOCOL_POSTMORTEM_MANIFEST_ROWS
                    ),
                    "row_identity": "campaign_manifest.runs[].index",
                    "preserved_checkpoint_rows": 943,
                    "promoted_attempts_retained_byte_identical": 3,
                    "superseded_attempts_retained_for_audit": 2,
                    "added_rows": 0,
                    "counting_rule": campaign.PROTOCOL_POSTMORTEM_COUNTING_RULE,
                },
            },
            checkpoint={
                "counts": counts,
                "frontier_attestation": {"frontier": "exact"},
                "preserved_terminal_results": [
                    {"run_index": index} for index in range(943)
                ],
                "verifier_corrections": [
                    {"run_index": index}
                    for index in sorted(
                        campaign.PROTOCOL_POSTMORTEM_CORRECTION_INDICES
                    )
                ],
                "authorized_retry_attempts": retries,
            },
            scheduler=predecessor.scheduler,
            amendment_sha256="c" * 64,
            scheduler_sha256="d" * 64,
        )

    monkeypatch.setattr(
        campaign, "create_protocol_postmortem_recovery_amendment", create
    )
    assert campaign.prepare_protocol_postmortem_recovery_continuation(
        SimpleNamespace(campaign=tmp_path)
    ) == 0

    assert observed["campaign"] == tmp_path
    assert observed["continuation_source_inventory"] == {"source": "new"}
    assert observed["continuation_limit_contract"] == {"limit": "same"}
    assert observed["continuation_scheduler_contract"] == predecessor.scheduler
    assert observed["verifier_correction_proof"] is campaign._verifier_correction_proof
    assert observed["lossless_read_state_proof"] is campaign._lossless_read_state_proof
    assert observed["postmortem_proof"] is campaign._operator_sigkill_postmortem_proof
    assert scratch_root.is_dir()
    assert launcher_lock.closed is True
    assert host_lock.closed is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "stopped"),
        ("running", [{"pid": 1234}]),
        ("running_count", 1),
        ("final_count", 943),
        ("pending_primary", 14),
        ("pending_refill", 0),
        (
            "final_classes",
            {"behavioral": 139, "scientific_invalid": 2, "scored": 803},
        ),
        ("final_by_condition", {"clean": 463, "steered": 480}),
        (campaign.STATUS_AMENDMENT_FIELD, "0" * 64),
        (campaign.STATUS_SCHEDULER_FIELD, "0" * 64),
    ],
)
def test_protocol_postmortem_prepare_rejects_nonexact_frontier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value,
) -> None:
    _base, status, _predecessor, _launcher, _host, _scratch = (
        _protocol_postmortem_prepare_state(tmp_path, monkeypatch)
    )
    status[field] = value
    (tmp_path / "status.json").write_text(json.dumps(status))

    with pytest.raises(
        RuntimeError, match="exact drained 944-selected/945-attempt"
    ):
        campaign.prepare_protocol_postmortem_recovery_continuation(
            SimpleNamespace(campaign=tmp_path)
        )


@pytest.mark.parametrize("resource", ["nonce", "port"])
def test_protocol_postmortem_prepare_requires_exact_process_and_port_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resource: str,
) -> None:
    base, _status, _predecessor, _launcher, _host, _scratch = (
        _protocol_postmortem_prepare_state(tmp_path, monkeypatch)
    )
    if resource == "nonce":
        monkeypatch.setattr(campaign, "_campaign_nonce_pids", lambda *_: [1234])
    else:
        occupied = base["runs"][0]["port"]
        monkeypatch.setattr(campaign, "_listening_ports", lambda: {occupied})

    with pytest.raises(RuntimeError, match="campaign-process and manifest-port"):
        campaign.prepare_protocol_postmortem_recovery_continuation(
            SimpleNamespace(campaign=tmp_path)
        )


@pytest.mark.parametrize(
    ("attempt", "artifact"),
    [
        (2, "attempt_directory"),
        (3, "launch_symlink"),
        (campaign.MAX_ATTEMPTS, "scratch_symlink"),
    ],
)
def test_protocol_postmortem_prepare_rejects_any_successor_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attempt: int,
    artifact: str,
) -> None:
    base, _status, _predecessor, _launcher, _host, _scratch = (
        _protocol_postmortem_prepare_state(tmp_path, monkeypatch)
    )
    row = base["runs"][campaign.PROTOCOL_POSTMORTEM_RUN_INDEX]
    if artifact == "attempt_directory":
        campaign._attempt_dir(tmp_path, row, attempt).mkdir(parents=True)
    elif artifact == "launch_symlink":
        path = campaign._launch_receipt_path(tmp_path, row, attempt)
        path.parent.mkdir(parents=True)
        target = tmp_path / "foreign-launch"
        target.write_text("foreign")
        path.symlink_to(target)
    else:
        path = campaign._scratch_attempt_dir(base, row, attempt)
        assert path is not None
        path.parent.mkdir(parents=True)
        target = tmp_path / "foreign-scratch"
        target.mkdir()
        path.symlink_to(target, target_is_directory=True)

    with pytest.raises(RuntimeError, match=f"attempt {attempt} predates"):
        campaign.prepare_protocol_postmortem_recovery_continuation(
            SimpleNamespace(campaign=tmp_path)
        )
