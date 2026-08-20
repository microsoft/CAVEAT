"""Render, queue, and materialize fresh TRAIN-only PRECOMMIT-ANY pairs."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .hero50_contingency import HERO_BUCKETS
from .interactive_sol_dagger_ops import _healthy, _runtime_import_environment, _terminate
from .proxy import sha256_json

CAMPAIGN = "campaign2-step29-precommit-any-r2"
STUDENT_ALIAS = "qwen35-browser-action-step29-paired-buynow-9a73894a3055-exact-lora"
VARIANTS = ("graded", "graded3", "graded4", "mixed")
BUCKETS = (
    "exploration_checkpoint",
    "winner_current_pdp_rebind",
    "shortcut_cart",
)
BUCKET_TARGETS = {
    "exploration_checkpoint": 10,
    "winner_current_pdp_rebind": 4,
    "shortcut_cart": 2,
}
TARGET_MATRIX = {
    "graded": (
        "exploration_checkpoint",
        "exploration_checkpoint",
        "winner_current_pdp_rebind",
        "shortcut_cart",
        "exploration_checkpoint",
        "winner_current_pdp_rebind",
    ),
    "graded3": (
        "exploration_checkpoint",
        "exploration_checkpoint",
        "winner_current_pdp_rebind",
        "shortcut_cart",
        "exploration_checkpoint",
        "winner_current_pdp_rebind",
    ),
    "graded4": (
        "exploration_checkpoint",
        "exploration_checkpoint",
        "exploration_checkpoint",
        "winner_current_pdp_rebind",
        "shortcut_cart",
        "winner_current_pdp_rebind",
    ),
    "mixed": (
        "exploration_checkpoint",
        "exploration_checkpoint",
        "exploration_checkpoint",
        "winner_current_pdp_rebind",
        "shortcut_cart",
        "winner_current_pdp_rebind",
    ),
}
SCHEMA = "harness-distill.precommit-any-dagger-bundle.v1"
TRACE_SCHEMA = "harness-distill.precommit-any-dagger-trace.v1"
MATERIALIZATION_SCHEMA = "harness-distill.precommit-any-materialization.v1"
PRESERVED_SCHEMA = "harness-distill.precommit-any-preserved-r1.v1"


class PrecommitOpsError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(_canonical(value) + b"\n")


def _write_status(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(_canonical(value) + b"\n")
    temporary.replace(path)


def _seed(campaign_id: str, variant: str, replica: int) -> int:
    payload = f"{campaign_id}::{variant}::train::r{replica}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") & ((1 << 63) - 1)


def render_bundle(
    *,
    source_root: Path,
    output_root: Path,
    proxy_base_port: int,
    environment_base_port: int,
    campaign_id: str = CAMPAIGN,
    student_alias: str = STUDENT_ALIAS,
) -> dict[str, Any]:
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    if output_root.exists() or output_root.is_symlink():
        raise PrecommitOpsError("output root must be fresh")
    by_variant: dict[str, Path] = {}
    prior_seeds: set[int] = set()
    for path in sorted((source_root / "configs").glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        seed = value.get("block_seed")
        if type(seed) is int:
            prior_seeds.add(seed)
        variant = ((value.get("task") or {}).get("metadata") or {}).get("variant")
        if variant in VARIANTS:
            by_variant.setdefault(str(variant), path)
    if set(by_variant) != set(VARIANTS) or not student_alias.strip():
        raise PrecommitOpsError("source variants or student alias are invalid")
    output_root.mkdir(parents=True)
    for name in ("configs", "proxy_traces", "run_results", "runtime"):
        (output_root / name).mkdir()

    rows: list[dict[str, Any]] = []
    for variant in VARIANTS:
        source = json.loads(by_variant[variant].read_text(encoding="utf-8"))
        if (
            source.get("condition") != "combined"
            or source.get("scaffold") != "browseruse-deliberative"
            or source.get("max_steps") != 4000
            or source.get("run_timeout_seconds") != 36000
        ):
            raise PrecommitOpsError("source is not the fixed laptop combined cell")
        for replica, target_bucket in enumerate(TARGET_MATRIX[variant]):
            index = len(rows)
            run_id = f"{campaign_id}::{variant}::train::r{replica}"
            seed = _seed(campaign_id, variant, replica)
            if seed in prior_seeds:
                raise PrecommitOpsError("fresh train seed collides with source")
            config = json.loads(json.dumps(source))
            config["block_seed"] = seed
            config["model"] = {
                **source["model"],
                "base_url": f"http://127.0.0.1:{proxy_base_port + index}/v1",
                "deployment": student_alias,
                "name": student_alias,
            }
            config["port"] = environment_base_port + index
            config["run_id"] = run_id
            config["pair_id"] = run_id
            config["out_dir"] = str(output_root / "run_results" / f"{variant}-train-r{replica}")
            config["runtime_environment"] = {
                **source["runtime_environment"],
                "AGENTARENA_CACHE_NONCE": run_id,
                "AGENTARENA_EVALUATION_INPUT_ATTESTATION": hashlib.sha256(
                    f"{run_id}::fresh-precommit-any-train-only".encode()
                ).hexdigest(),
            }
            config["audit_contract"] = {
                **source["audit_contract"],
                "campaign": campaign_id,
                "collection_only": True,
                "evaluation_row": False,
                "target_bucket": target_bucket,
            }
            config_path = output_root / "configs" / f"{index:02d}_{variant}_train_r{replica}.json"
            _write_new(config_path, config)
            rows.append(
                {
                    "index": index,
                    "variant": variant,
                    "split": "train",
                    "replica": replica,
                    "target_bucket": target_bucket,
                    "run_id": run_id,
                    "block_seed": seed,
                    "config": str(config_path),
                    "trace_path": str(
                        output_root
                        / "proxy_traces"
                        / f"{index:02d}_{variant}_train_r{replica}.jsonl"
                    ),
                    "proxy_port": proxy_base_port + index,
                    "environment_port": environment_base_port + index,
                }
            )
    body = {
        "schema": SCHEMA,
        "campaign": campaign_id,
        "status": "rendered",
        "student_alias": student_alias,
        "source_root": str(source_root),
        "matrix": {
            "split": "train",
            "variants": list(VARIANTS),
            "replicas": list(range(6)),
            "runs": 24,
            "evaluation_rows": 0,
            "target_bucket_pool": {
                bucket: sum(row["target_bucket"] == bucket for row in rows) for bucket in BUCKETS
            },
        },
        "routing": {
            "roll_in": "step29_candidate_until_precommit_any",
            "intervention": "gpt-5.6-sol#low_same_request",
            "successor": "next_browseruse_request_with_current_tab_url",
        },
        "materialization_target": {
            "rows": 16,
            "variant_counts": {variant: 4 for variant in VARIANTS},
            "bucket_counts": BUCKET_TARGETS,
        },
        "rows": rows,
    }
    bundle = {**body, "bundle_sha256": hashlib.sha256(_canonical(body)).hexdigest()}
    _write_new(output_root / "bundle.json", bundle)
    return bundle


def combine_bundles(*, bundle_paths: list[Path], output_path: Path) -> dict[str, Any]:
    """Create one provenance-preserving input bundle for exact materialization."""

    if len(bundle_paths) < 2 or output_path.exists() or output_path.is_symlink():
        raise PrecommitOpsError("combined bundle requires fresh output and two sources")
    sources: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    aliases: set[str] = set()
    for path in bundle_paths:
        resolved = path.resolve()
        bundle = json.loads(resolved.read_text(encoding="utf-8"))
        if bundle.get("schema") != SCHEMA:
            raise PrecommitOpsError("combined source bundle is invalid")
        aliases.add(bundle.get("student_alias", ""))
        rows.extend(json.loads(json.dumps(bundle.get("rows", []))))
        sources.append(
            {
                "path": str(resolved),
                "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
                "bundle_sha256": bundle.get("bundle_sha256"),
            }
        )
    run_ids = [row.get("run_id") for row in rows]
    if aliases == {""} or len(aliases) != 1 or len(set(run_ids)) != len(run_ids):
        raise PrecommitOpsError("combined bundle aliases or run identities are invalid")
    body = {
        "schema": SCHEMA,
        "campaign": "combined-precommit-any-exact16",
        "status": "combined",
        "student_alias": next(iter(aliases)),
        "source_bundles": sources,
        "matrix": {
            "split": "train",
            "runs": len(rows),
            "evaluation_rows": 0,
            "variants": list(VARIANTS),
        },
        "routing": {
            "roll_in": "step29_candidate_until_precommit_any",
            "intervention": "gpt-5.6-sol#low_same_request",
            "successor": "next_browseruse_request_with_current_tab_url",
        },
        "materialization_target": {
            "rows": 16,
            "variant_counts": {variant: 4 for variant in VARIANTS},
            "bucket_counts": BUCKET_TARGETS,
        },
        "rows": rows,
    }
    combined = {**body, "bundle_sha256": hashlib.sha256(_canonical(body)).hexdigest()}
    _write_new(output_path, combined)
    return combined


def _assistant_message(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise PrecommitOpsError("rejected response has no unique choice")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise PrecommitOpsError("rejected response has no assistant message")
    return json.loads(json.dumps(message))


def collect_pairs(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for source in bundle.get("rows", []):
        trace_path = Path(source["trace_path"])
        if not trace_path.is_file():
            continue
        try:
            records = [json.loads(line) for line in trace_path.read_text().splitlines() if line]
        except json.JSONDecodeError:
            continue
        interventions = [
            row
            for row in records
            if row.get("schema") == TRACE_SCHEMA and row.get("route") == "teacher_intervention"
        ]
        successors = {
            row.get("state_id"): row
            for row in records
            if row.get("schema") == TRACE_SCHEMA
            and row.get("route") == "successor"
            and row.get("successor_validated") is True
            and row.get("before_url")
            and row.get("after_url")
        }
        for intervention in interventions:
            successor = successors.get(intervention.get("state_id"))
            if successor is None:
                continue
            bucket = intervention.get("training_bucket")
            request = intervention.get("effective_request")
            chosen = intervention.get("teacher_completion")
            rejected_response = intervention.get("qwen_rejected_response")
            if (
                bucket not in (*BUCKETS, *HERO_BUCKETS)
                or not isinstance(request, dict)
                or not isinstance(chosen, dict)
                or not isinstance(rejected_response, dict)
            ):
                continue
            rejected = _assistant_message(rejected_response)
            row_id = hashlib.sha256(
                _canonical(
                    {
                        "state_id": intervention["state_id"],
                        "request": request,
                        "chosen": chosen,
                        "rejected": rejected,
                    }
                )
            ).hexdigest()
            pairs.append(
                {
                    "schema": "harness-distill.precommit-any-preference-pair.v1",
                    "row_id": row_id,
                    "state_id": intervention["state_id"],
                    "run_id": source["run_id"],
                    "variant": source["variant"],
                    "source_split": "train",
                    "phase": intervention["trigger_kind"],
                    "training_bucket": bucket,
                    "trigger_kind": intervention["trigger_kind"],
                    "messages_before_action": request["messages"],
                    "tools": request.get("tools", []),
                    "tool_choice": request.get("tool_choice"),
                    "parallel_tool_calls": request.get("parallel_tool_calls"),
                    "response_format": request.get("response_format"),
                    "chosen": chosen,
                    "rejected": rejected,
                    "rejected_response_sha256": intervention["qwen_rejected_response_sha256"],
                    "chosen_sha256": intervention["teacher_completion_sha256"],
                    "hero_teacher_validation": intervention.get(
                        "hero_teacher_validation"
                    ),
                    "successor": {
                        key: successor.get(key)
                        for key in (
                            "successor_validated",
                            "request_changed",
                            "before_url",
                            "after_url",
                            "before_path",
                            "after_path",
                            "direct_checkout_avoided",
                            "order_not_placed",
                            "target_id",
                            "after_pdp_id",
                            "target_rebound",
                            "target_visible_on_list",
                            "r_stage",
                            "hero_transition_validated",
                            "approved_target_id",
                        )
                    },
                }
            )
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for row in pairs:
        unique.setdefault((row["run_id"], row["training_bucket"]), row)
    return sorted(unique.values(), key=lambda row: row["row_id"])


def select_exact16(pairs: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    by_variant = {
        variant: [row for row in pairs if row["variant"] == variant] for variant in VARIANTS
    }
    if any(len(rows) < 4 for rows in by_variant.values()):
        return None
    combinations = {
        variant: list(itertools.combinations(rows, 4)) for variant, rows in by_variant.items()
    }

    def search(
        position: int,
        selected: list[dict[str, Any]],
        counts: dict[str, int],
    ) -> list[dict[str, Any]] | None:
        if position == len(VARIANTS):
            return selected if counts == BUCKET_TARGETS else None
        variant = VARIANTS[position]
        for choice in combinations[variant]:
            updated = counts.copy()
            for row in choice:
                updated[row["training_bucket"]] += 1
            if any(updated[bucket] > BUCKET_TARGETS[bucket] for bucket in BUCKETS):
                continue
            result = search(position + 1, [*selected, *choice], updated)
            if result is not None:
                return result
        return None

    return search(0, [], {bucket: 0 for bucket in BUCKETS})


def materialize_pairs(*, bundle_path: Path, output_root: Path) -> dict[str, Any]:
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    if bundle.get("schema") != SCHEMA:
        raise PrecommitOpsError("precommit bundle is invalid")
    if output_root.exists() or output_root.is_symlink():
        raise PrecommitOpsError("materialized output root must be fresh")
    selected = select_exact16(collect_pairs(bundle))
    if selected is None:
        raise PrecommitOpsError("exact 16-row variant/bucket selection is unavailable")
    payload = b"".join(_canonical(row) + b"\n" for row in selected)
    output_root.mkdir(parents=True)
    preference_path = output_root / "chosen_rejected.jsonl"
    preference_path.write_bytes(payload)
    manifest_body = {
        "schema": MATERIALIZATION_SCHEMA,
        "status": "complete",
        "source_bundle": {
            "path": str(bundle_path.resolve()),
            "sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        },
        "rows": 16,
        "variant_counts": {variant: 4 for variant in VARIANTS},
        "bucket_counts": BUCKET_TARGETS,
        "split_counts": {"train": 16, "heldout": 0, "evaluation": 0},
        "files": {
            preference_path.name: {
                "rows": 16,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        },
    }
    manifest = {
        **manifest_body,
        "manifest_sha256": hashlib.sha256(_canonical(manifest_body)).hexdigest(),
    }
    _write_new(output_root / "manifest.json", manifest)
    return manifest


def preserve_r1_pair(*, bundle_path: Path, output_root: Path) -> dict[str, Any]:
    if output_root.exists() or output_root.is_symlink():
        raise PrecommitOpsError("preserved r1 output root must be fresh")
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    source = next(
        row for row in bundle["rows"] if row["variant"] == "graded3" and row["replica"] == 0
    )
    records = [json.loads(line) for line in Path(source["trace_path"]).read_text().splitlines()]
    intervention = next(row for row in records if row.get("route") == "teacher_intervention")
    successor = next(
        row
        for row in records
        if row.get("route") == "successor"
        and row.get("state_id") == intervention["state_id"]
        and row.get("successor_validated") is True
    )
    request = intervention["effective_request"]
    chosen = intervention["teacher_completion"]
    rejected = _assistant_message(intervention["qwen_rejected_response"])
    # Fixed-parser revalidation result, recomputed from the sealed trace.
    from .precommit_any_dagger import current_tab_url

    def browser_state(value: dict[str, Any]) -> str:
        for message in reversed(value.get("messages", [])):
            content = message.get("content") if isinstance(message, dict) else None
            if isinstance(content, str) and "<browser_state>" in content:
                return content.rsplit("<browser_state>", 1)[1].split("</browser_state>", 1)[0]
        return ""

    before_url = current_tab_url(browser_state(request))
    after_url = current_tab_url(browser_state(successor["effective_request"]))
    request_changed = sha256_json(successor["effective_request"]) != sha256_json(request)
    after_path = urlparse(after_url).path if after_url else ""
    after_state = browser_state(successor["effective_request"])
    safe = bool(
        before_url
        and after_url
        and request_changed
        and not after_path.startswith("/gp/buy")
        and "/gp/buy/thankyou" not in after_path
        and "order placed" not in after_state.casefold()
    )
    if not safe:
        raise PrecommitOpsError("preserved r1 pair failed fixed current-tab revalidation")
    row_id = hashlib.sha256(
        _canonical(
            {
                "state_id": intervention["state_id"],
                "request": request,
                "chosen": chosen,
                "rejected": rejected,
            }
        )
    ).hexdigest()
    row = {
        "schema": "harness-distill.precommit-any-preference-pair.v1",
        "row_id": row_id,
        "state_id": intervention["state_id"],
        "run_id": source["run_id"],
        "variant": "graded3",
        "source_split": "train",
        "phase": "visible_pdp_buy_now_click",
        "training_bucket": "shortcut_cart",
        "trigger_kind": "visible_pdp_buy_now_click",
        "messages_before_action": request["messages"],
        "tools": request.get("tools", []),
        "tool_choice": request.get("tool_choice"),
        "parallel_tool_calls": request.get("parallel_tool_calls"),
        "response_format": request.get("response_format"),
        "chosen": chosen,
        "rejected": rejected,
        "rejected_response_sha256": intervention["qwen_rejected_response_sha256"],
        "chosen_sha256": intervention["teacher_completion_sha256"],
        "successor": {
            "successor_validated": True,
            "request_changed": True,
            "before_url": before_url,
            "after_url": after_url,
            "direct_checkout_avoided": True,
            "order_not_placed": True,
            "fixed_current_tab_parser_revalidated": True,
        },
    }
    payload = _canonical(row) + b"\n"
    output_root.mkdir(parents=True)
    path = output_root / "preserved_r1_pair.jsonl"
    path.write_bytes(payload)
    manifest_body = {
        "schema": PRESERVED_SCHEMA,
        "status": "complete",
        "rows": 1,
        "source_bundle_sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        "state_id": row["state_id"],
        "fixed_current_tab_parser_revalidated": True,
        "file": {
            "path": path.name,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
    }
    manifest = {
        **manifest_body,
        "manifest_sha256": hashlib.sha256(_canonical(manifest_body)).hexdigest(),
    }
    _write_new(output_root / "manifest.json", manifest)
    return manifest


def _target_valid(path: Path, bucket: str) -> bool:
    if not path.is_file():
        return False
    try:
        return any(
            row.get("schema") == TRACE_SCHEMA
            and row.get("route") == "successor"
            and row.get("training_bucket") == bucket
            and row.get("successor_validated") is True
            for row in (json.loads(line) for line in path.read_text().splitlines() if line)
        )
    except json.JSONDecodeError:
        return False


def run_bundle(
    *,
    bundle_path: Path,
    upstream_base_url: str,
    python: Path,
    materialized_output: Path,
    max_workers: int = 4,
    timeout_seconds: int = 10800,
    expected_rows: int = 24,
    selector: Any = None,
    materializer: Any = None,
    worker_module: str = "harness_posttrain_eval.launch_one",
) -> int:
    selector = selector or select_exact16
    materializer = materializer or materialize_pairs
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    if (
        bundle.get("schema") != SCHEMA
        or len(bundle.get("rows", [])) != expected_rows
        or not upstream_base_url.startswith("http://127.0.0.1:")
        or max_workers not in {1, 2, 3, 4}
        or worker_module
        not in {
            "harness_posttrain_eval.launch_one",
            "harness_distill.hero50_curriculum_worker",
        }
    ):
        raise PrecommitOpsError("bundle, endpoint, or concurrency is invalid")
    environment, workspace = _runtime_import_environment(python)
    runtime = bundle_path.parent / "runtime"
    proxies: list[tuple[dict[str, Any], subprocess.Popen[bytes], Any]] = []
    active: dict[int, tuple[dict[str, Any], subprocess.Popen[bytes], Any, Any]] = {}
    attempted: set[int] = set()
    queue = sorted(
        bundle["rows"], key=lambda row: (row["replica"], VARIANTS.index(row["variant"]))
    )
    try:
        for row in bundle["rows"]:
            proxy_log = (runtime / f"proxy_{row['index']:02d}.log").open("xb")
            proxy_env = environment.copy()
            proxy_env.update(
                {
                    "HARNESS_DISTILL_UPSTREAM_BASE_URL": upstream_base_url,
                    "HARNESS_DISTILL_TRACE_JSONL": row["trace_path"],
                    "HARNESS_DISTILL_SESSION_ID": row["run_id"],
                    "HARNESS_DISTILL_ROLLOUT_ID": row["run_id"],
                    "HARNESS_DISTILL_VARIANT": row["variant"],
                    "HARNESS_DISTILL_TARGET_BUCKET": row["target_bucket"],
                    "HARNESS_DISTILL_CAMPAIGN_ID": bundle["campaign"],
                }
            )
            if row.get("teacher_policy") == "live_state_rule_expert":
                proxy_env["HARNESS_DISTILL_TEACHER_POLICY"] = row["teacher_policy"]
            process = subprocess.Popen(
                [
                    str(python),
                    "-m",
                    "uvicorn",
                    "harness_distill.precommit_any_dagger:app_from_environment",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(row["proxy_port"]),
                    "--log-level",
                    "warning",
                ],
                cwd=workspace,
                env=proxy_env,
                stdout=proxy_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            proxies.append((row, process, proxy_log))
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if all(
                _healthy(f"http://127.0.0.1:{row['proxy_port']}/healthz")
                for row in bundle["rows"]
            ):
                break
            if any(process.poll() is not None for _row, process, _log in proxies):
                raise PrecommitOpsError("proxy exited before readiness")
            time.sleep(0.5)
        else:
            raise PrecommitOpsError("proxies did not become ready")

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            for index, (row, process, stdout, stderr) in list(active.items()):
                if (
                    _target_valid(Path(row["trace_path"]), row["target_bucket"])
                    and process.poll() is None
                ):
                    _terminate(process)
                if process.poll() is not None:
                    stdout.close()
                    stderr.close()
                    attempted.add(index)
                    _write_new(
                        runtime / f"worker_{index:02d}.exit.json",
                        {
                            "exit_code": process.returncode,
                            "target_bucket": row["target_bucket"],
                            "target_valid": _target_valid(
                                Path(row["trace_path"]), row["target_bucket"]
                            ),
                        },
                    )
                    del active[index]

            pairs = collect_pairs(bundle)
            selected = selector(pairs)
            target_buckets = tuple(
                (bundle.get("materialization_target") or {}).get(
                    "bucket_counts", BUCKET_TARGETS
                )
            )
            bucket_counts = {
                bucket: sum(row["training_bucket"] == bucket for row in pairs)
                for bucket in target_buckets
            }
            variant_counts = {
                variant: sum(row["variant"] == variant for row in pairs) for variant in VARIANTS
            }
            _write_status(
                runtime / "structural_status.json",
                {
                    "timestamp": time.time(),
                    "active": [
                        {
                            "index": index,
                            "variant": row["variant"],
                            "target_bucket": row["target_bucket"],
                        }
                        for index, (row, _process, _stdout, _stderr) in sorted(active.items())
                    ],
                    "max_workers": max_workers,
                    "attempted": len(attempted),
                    "verified_pairs": len(pairs),
                    "variant_verified_pairs": variant_counts,
                    "bucket_verified_pairs": bucket_counts,
                    "exact16_selectable": selected is not None,
                },
            )
            if selected is not None:
                for _index, (_row, process, _stdout, _stderr) in active.items():
                    _terminate(process)
                active.clear()
                materializer(bundle_path=bundle_path, output_root=materialized_output)
                return 0

            for row in queue:
                if len(active) >= max_workers:
                    break
                index = row["index"]
                if index in attempted or index in active:
                    continue
                stdout = (runtime / f"worker_{index:02d}.stdout").open("xb")
                stderr = (runtime / f"worker_{index:02d}.stderr").open("xb")
                process = subprocess.Popen(
                    [
                        str(python),
                        "-m",
                        worker_module,
                        "--spec",
                        row["config"],
                    ],
                    cwd=workspace,
                    env=environment,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
                active[index] = (row, process, stdout, stderr)
            if not active and len(attempted) == len(queue):
                return 2
            time.sleep(1)
        return 3
    finally:
        for _index, (_row, process, stdout, stderr) in active.items():
            _terminate(process)
            stdout.close()
            stderr.close()
        for _row, process, stream in proxies:
            _terminate(process)
            stream.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render")
    render.add_argument("--source-root", type=Path, required=True)
    render.add_argument("--output-root", type=Path, required=True)
    render.add_argument("--proxy-base-port", type=int, required=True)
    render.add_argument("--environment-base-port", type=int, required=True)
    render.add_argument("--campaign-id", default=CAMPAIGN)
    render.add_argument("--student-alias", default=STUDENT_ALIAS)
    combine = commands.add_parser("combine")
    combine.add_argument("--bundle", type=Path, action="append", required=True)
    combine.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--bundle", type=Path, required=True)
    run.add_argument("--upstream-base-url", required=True)
    run.add_argument("--python", type=Path, default=Path(sys.executable))
    run.add_argument("--materialized-output", type=Path, required=True)
    run.add_argument("--max-workers", type=int, default=4)
    run.add_argument("--timeout-seconds", type=int, default=10800)
    materialize = commands.add_parser("materialize")
    materialize.add_argument("--bundle", type=Path, required=True)
    materialize.add_argument("--output-root", type=Path, required=True)
    preserve = commands.add_parser("preserve-r1")
    preserve.add_argument("--bundle", type=Path, required=True)
    preserve.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "render":
        print(
            json.dumps(
                render_bundle(
                    source_root=args.source_root,
                    output_root=args.output_root,
                    proxy_base_port=args.proxy_base_port,
                    environment_base_port=args.environment_base_port,
                    campaign_id=args.campaign_id,
                    student_alias=args.student_alias,
                ),
                sort_keys=True,
            )
        )
        return 0
    if args.command == "combine":
        print(
            json.dumps(
                combine_bundles(bundle_paths=args.bundle, output_path=args.output),
                sort_keys=True,
            )
        )
        return 0
    if args.command == "materialize":
        print(
            json.dumps(
                materialize_pairs(bundle_path=args.bundle, output_root=args.output_root),
                sort_keys=True,
            )
        )
        return 0
    if args.command == "preserve-r1":
        print(
            json.dumps(
                preserve_r1_pair(bundle_path=args.bundle, output_root=args.output_root),
                sort_keys=True,
            )
        )
        return 0
    return run_bundle(
        bundle_path=args.bundle,
        upstream_base_url=args.upstream_base_url,
        python=args.python,
        materialized_output=args.materialized_output,
        max_workers=args.max_workers,
        timeout_seconds=args.timeout_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
