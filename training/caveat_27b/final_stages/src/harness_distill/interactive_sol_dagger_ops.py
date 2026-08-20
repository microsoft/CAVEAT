"""Render, audit, and run the frozen 36-cell interactive Sol-DAgger matrix."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from .interactive_sol_dagger import HOLDOUT_HORIZON, TRAIN_HORIZONS, VARIANTS
from .sol_dagger_rollout_ops import (
    HARNESS_SHA256,
    LIMIT_SHA256,
    SOURCE_FILENAMES,
    _model_facing_contract,
)

SCHEMA = "harness-distill.interactive-sol-dagger-rollout-bundle.v1"
CAMPAIGN = "qwen-step25-interactive-sol-dagger-r1"
STUDENT_ALIAS = "qwen35-browser-action-sol-dagger-step25-6b4f66832c2e-exact-lora"
DEFAULT_PROXY_BASE_PORT = 18800
DEFAULT_ENVIRONMENT_BASE_PORT = 50000
DEFAULT_MAX_CONCURRENCY = 8
TRAIN_REPLICAS = (0, 1)
TRAIN_SCHEDULE = tuple(
    (horizon, replica) for horizon in TRAIN_HORIZONS for replica in TRAIN_REPLICAS
)


class InteractiveRolloutOpsError(RuntimeError):
    """Rendered configuration or runtime state violates the frozen matrix."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise InteractiveRolloutOpsError(f"unsafe or absent JSON: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise InteractiveRolloutOpsError(f"JSON is not an object: {path}")
    return value


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(value) + b"\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError as exc:
        raise InteractiveRolloutOpsError(f"create-only target exists: {path}") from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _seed(variant: str, split: str, horizon: int, replacement: int = 0) -> int:
    payload = f"{CAMPAIGN}::{variant}::{split}::h{horizon}::x{replacement}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") & ((1 << 63) - 1)


def _source_variant(config: dict[str, Any]) -> str:
    variant = ((config.get("task") or {}).get("metadata") or {}).get("variant")
    if variant not in VARIANTS:
        raise InteractiveRolloutOpsError("source config has an unexpected task variant")
    return str(variant)


def _all_source_seeds(source_bundle: Path) -> tuple[set[int], str]:
    """Inventory every config seed exposed by the sealed source bundle."""

    paths = set((source_bundle / "configs").glob("*.json"))
    manifest = _read_json(source_bundle / "bundle.json")
    manifest_rows = manifest.get("rows")
    if isinstance(manifest_rows, list):
        for row in manifest_rows:
            if not isinstance(row, dict) or not isinstance(row.get("config"), str):
                continue
            path = Path(row["config"])
            paths.add(path if path.is_absolute() else source_bundle / path)
    inventory: list[dict[str, Any]] = []
    seeds: set[int] = set()
    for path in sorted(paths, key=lambda item: str(item)):
        config = _read_json(path.resolve())
        seed = config.get("block_seed")
        if type(seed) is not int:
            raise InteractiveRolloutOpsError(f"source config has no integer block seed: {path}")
        seeds.add(seed)
        inventory.append(
            {
                "path": str(path.resolve()),
                "sha256": sha256_file(path.resolve()),
                "block_seed": seed,
            }
        )
    if len(inventory) < 4 or len(seeds) != len(inventory):
        raise InteractiveRolloutOpsError("source seed inventory is incomplete or duplicated")
    return seeds, hashlib.sha256(canonical_bytes(inventory)).hexdigest()


def render_bundle(
    *,
    source_bundle: Path,
    output_root: Path,
    proxy_base_port: int = DEFAULT_PROXY_BASE_PORT,
    environment_base_port: int = DEFAULT_ENVIRONMENT_BASE_PORT,
) -> dict[str, Any]:
    source_bundle = source_bundle.resolve()
    output_root = output_root.resolve()
    if output_root.exists() or output_root.is_symlink():
        raise InteractiveRolloutOpsError("rollout output root must be fresh")
    if not (1024 <= proxy_base_port <= 64000 and 1024 <= environment_base_port <= 64000):
        raise InteractiveRolloutOpsError("port range is invalid")
    output_root.mkdir(parents=True)
    for name in ("configs", "proxy_traces", "run_results", "runtime"):
        (output_root / name).mkdir()

    rows: list[dict[str, Any]] = []
    source_seeds, source_seed_inventory_sha256 = _all_source_seeds(source_bundle)
    index = 0
    for variant in VARIANTS:
        source_path = source_bundle / "configs" / SOURCE_FILENAMES[variant]
        source = _read_json(source_path)
        if (
            _source_variant(source) != variant
            or source.get("condition") != "combined"
            or source.get("scaffold") != "browseruse-deliberative"
            or source.get("max_steps") != 4000
            or source.get("run_timeout_seconds") != 36000
            or (source.get("audit_contract") or {}).get("harness_sha256") != HARNESS_SHA256
            or (source.get("audit_contract") or {}).get("limit_contract_sha256") != LIMIT_SHA256
        ):
            raise InteractiveRolloutOpsError("source is not the frozen fixed-v7 cell")
        facing = _model_facing_contract(source)
        schedule = [("train", horizon, replica) for horizon, replica in TRAIN_SCHEDULE] + [
            ("holdout", HOLDOUT_HORIZON, 0)
        ]
        for split, horizon, replica in schedule:
            config = json.loads(json.dumps(source))
            run_id = f"{CAMPAIGN}::{variant}::{split}::h{horizon}::r{replica}"
            result_path = output_root / "run_results" / f"{variant}-{split}-h{horizon}-r{replica}"
            config["arm"] = "trained"
            config["block_seed"] = _seed(variant, split, horizon, replica)
            config["model"] = {
                **source["model"],
                "base_url": f"http://127.0.0.1:{proxy_base_port + index}/v1",
                "deployment": STUDENT_ALIAS,
                "name": STUDENT_ALIAS,
            }
            config["out_dir"] = str(result_path)
            config["pair_id"] = run_id
            config["port"] = environment_base_port + index
            config["run_id"] = run_id
            config["runtime_environment"] = {
                **source["runtime_environment"],
                "AGENTARENA_CACHE_NONCE": run_id,
                "AGENTARENA_EVALUATION_INPUT_ATTESTATION": hashlib.sha256(
                    canonical_bytes(
                        {
                            "campaign": CAMPAIGN,
                            "variant": variant,
                            "split": split,
                            "horizon": horizon,
                            "replica": replica,
                        }
                    )
                ).hexdigest(),
            }
            config["audit_contract"] = {
                "campaign": CAMPAIGN,
                "collection_only": True,
                "evaluation_row": False,
                "teacher_or_outcome_used_for_render": False,
                "harness_sha256": HARNESS_SHA256,
                "limit_contract_sha256": LIMIT_SHA256,
                "source_config_sha256": sha256_file(source_path),
            }
            if _model_facing_contract(config) != facing:
                raise InteractiveRolloutOpsError("render changed model-facing harness")
            config_path = (
                output_root
                / "configs"
                / f"{index:02d}_{variant}_{split}_h{horizon}_r{replica}.json"
            )
            _write_new(config_path, config)
            rows.append(
                {
                    "index": index,
                    "variant": variant,
                    "split": split,
                    "horizon": horizon,
                    "replica": replica,
                    "run_id": run_id,
                    "block_seed": config["block_seed"],
                    "source_config": str(source_path),
                    "source_config_sha256": sha256_file(source_path),
                    "config": str(config_path),
                    "config_sha256": sha256_file(config_path),
                    "proxy_port": proxy_base_port + index,
                    "environment_port": environment_base_port + index,
                    "trace_path": str(
                        output_root
                        / "proxy_traces"
                        / f"{index:02d}_{variant}_{split}_h{horizon}_r{replica}.jsonl"
                    ),
                    "result_path": str(result_path),
                }
            )
            index += 1
    if len(rows) != 36 or len({row["block_seed"] for row in rows}) != 36:
        raise InteractiveRolloutOpsError("interactive matrix is incomplete")
    if any(row["block_seed"] in source_seeds for row in rows):
        raise InteractiveRolloutOpsError("collection seed overlaps a frozen source seed")
    source_manifest = source_bundle / "bundle.json"
    body = {
        "schema": SCHEMA,
        "status": "preregistered",
        "campaign": CAMPAIGN,
        "student": {
            "candidate": "step25-sol-dagger-sft",
            "served_alias": STUDENT_ALIAS,
            "adapter_tree_sha256": (
                "6b4f66832c2e11acbdb03413898999143f70bd60d3d020dd27aa7705617dbdba"
            ),
        },
        "source_bundle": str(source_bundle),
        "source_bundle_manifest_sha256": sha256_file(source_manifest),
        "source_seed_inventory": {
            "config_count": len(source_seeds),
            "unique_seed_count": len(source_seeds),
            "sha256": source_seed_inventory_sha256,
        },
        "matrix": {
            "condition": "combined",
            "variants": list(VARIANTS),
            "train_horizons": list(TRAIN_HORIZONS),
            "train_replicas": list(TRAIN_REPLICAS),
            "holdout_horizon": HOLDOUT_HORIZON,
            "train_runs": 32,
            "holdout_runs": 4,
            "runs": 36,
        },
        "invariance": {
            "model_facing_harness_unchanged": True,
            "scaffold": "browseruse-deliberative",
            "harness_sha256": HARNESS_SHA256,
            "limit_contract_sha256": LIMIT_SHA256,
            "max_steps": 4000,
            "run_timeout_seconds": 36000,
            "evaluation_rows": 0,
            "teacher_or_outcome_used_for_render": False,
        },
        "routing": {
            "student_roll_in": "qwen-step25",
            "teacher": "gpt-5.6-sol#low",
            "teacher_transport": "trapi-responses",
            "sticky_takeover": True,
            "qwen_shadow": True,
            "checkout_guard": True,
        },
        "rows": rows,
    }
    bundle = {**body, "bundle_sha256": hashlib.sha256(canonical_bytes(body)).hexdigest()}
    _write_new(output_root / "bundle.json", bundle)
    return bundle


def audit_bundle(path: Path) -> dict[str, Any]:
    bundle = _read_json(path.resolve())
    body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    if (
        bundle.get("schema") != SCHEMA
        or bundle.get("status") != "preregistered"
        or bundle.get("bundle_sha256") != hashlib.sha256(canonical_bytes(body)).hexdigest()
        or bundle.get("matrix")
        != {
            "condition": "combined",
            "variants": list(VARIANTS),
            "train_horizons": list(TRAIN_HORIZONS),
            "train_replicas": list(TRAIN_REPLICAS),
            "holdout_horizon": HOLDOUT_HORIZON,
            "train_runs": 32,
            "holdout_runs": 4,
            "runs": 36,
        }
        or (bundle.get("invariance") or {}).get("model_facing_harness_unchanged") is not True
        or (bundle.get("invariance") or {}).get("evaluation_rows") != 0
        or len(bundle.get("rows", [])) != 36
    ):
        raise InteractiveRolloutOpsError("rollout bundle policy or self-hash changed")
    source_bundle = Path(str(bundle["source_bundle"]))
    if sha256_file(source_bundle / "bundle.json") != bundle["source_bundle_manifest_sha256"]:
        raise InteractiveRolloutOpsError("source bundle manifest changed")
    source_seeds, source_seed_inventory_sha256 = _all_source_seeds(source_bundle)
    if bundle.get("source_seed_inventory") != {
        "config_count": len(source_seeds),
        "unique_seed_count": len(source_seeds),
        "sha256": source_seed_inventory_sha256,
    }:
        raise InteractiveRolloutOpsError("source seed inventory changed")
    expected_cells = {
        *(
            (variant, "train", horizon, replica)
            for variant in VARIANTS
            for horizon, replica in TRAIN_SCHEDULE
        ),
        *((variant, "holdout", HOLDOUT_HORIZON, 0) for variant in VARIANTS),
    }
    actual_cells = {
        (row.get("variant"), row.get("split"), row.get("horizon"), row.get("replica"))
        for row in bundle["rows"]
    }
    if actual_cells != expected_cells:
        raise InteractiveRolloutOpsError("rendered matrix cell policy changed")
    seen: set[int] = set()
    for row in bundle["rows"]:
        config_path = Path(str(row["config"]))
        source_path = Path(str(row["source_config"]))
        if sha256_file(config_path) != row.get("config_sha256") or sha256_file(
            source_path
        ) != row.get("source_config_sha256"):
            raise InteractiveRolloutOpsError("rendered or source config bytes changed")
        config = _read_json(config_path)
        source = _read_json(source_path)
        if (
            _model_facing_contract(config) != _model_facing_contract(source)
            or config.get("block_seed") in source_seeds
            or config.get("block_seed") in seen
            or (config.get("model") or {}).get("name") != STUDENT_ALIAS
            or config.get("scaffold") != "browseruse-deliberative"
            or row.get("split") not in {"train", "holdout"}
            or row.get("horizon")
            not in (TRAIN_HORIZONS if row.get("split") == "train" else (HOLDOUT_HORIZON,))
            or row.get("replica") not in (TRAIN_REPLICAS if row.get("split") == "train" else (0,))
        ):
            raise InteractiveRolloutOpsError("rendered config no longer satisfies policy")
        seen.add(int(config["block_seed"]))
    return bundle


def _healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310
            return response.status == 200
    except Exception:  # noqa: BLE001
        return False


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=20)


def _runtime_import_environment(python: Path) -> tuple[dict[str, str], Path]:
    distill_src = Path(__file__).resolve().parents[1]
    workspace_root = Path(__file__).resolve().parents[4]
    evaluator_src = workspace_root / "training" / "harness_posttrain_eval" / "src"
    if (
        not (distill_src / "harness_distill" / "interactive_sol_dagger.py").is_file()
        or not (evaluator_src / "harness_posttrain_eval" / "launch_one.py").is_file()
    ):
        raise InteractiveRolloutOpsError("collector or evaluator import source is absent")
    environment = os.environ.copy()
    inherited = environment.get("PYTHONPATH", "")
    paths = [str(distill_src), str(evaluator_src)]
    if inherited:
        paths.append(inherited)
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    probe = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import harness_distill.interactive_sol_dagger; "
                "import harness_posttrain_eval.launch_one"
            ),
        ],
        cwd=workspace_root,
        env=environment,
        capture_output=True,
        timeout=60,
        check=False,
    )
    if probe.returncode != 0:
        raise InteractiveRolloutOpsError(
            "collector/evaluator import preflight failed: "
            + probe.stderr.decode("utf-8", errors="replace")[-1000:]
        )
    return environment, workspace_root


def _run_wave(
    rows: list[dict[str, Any]],
    *,
    upstream_base_url: str,
    python: Path,
    runtime: Path,
    base_environment: dict[str, str],
    workspace_root: Path,
) -> int:
    proxies: list[tuple[dict[str, Any], subprocess.Popen[bytes], Any]] = []
    workers: list[tuple[dict[str, Any], subprocess.Popen[bytes], Any, Any]] = []
    try:
        for row in rows:
            trace = Path(row["trace_path"])
            if trace.exists() or trace.is_symlink():
                raise InteractiveRolloutOpsError("proxy trace target is not fresh")
            stream = (runtime / f"proxy_{row['index']:02d}.log").open("xb")
            environment = base_environment.copy()
            environment.update(
                {
                    "HARNESS_DISTILL_UPSTREAM_BASE_URL": upstream_base_url,
                    "HARNESS_DISTILL_TRACE_JSONL": str(trace),
                    "HARNESS_DISTILL_SESSION_ID": row["run_id"],
                    "HARNESS_DISTILL_ROLLOUT_ID": row["run_id"],
                    "HARNESS_DISTILL_SPLIT": row["split"],
                    "HARNESS_DISTILL_VARIANT": row["variant"],
                    "HARNESS_DISTILL_HORIZON": str(row["horizon"]),
                    "HARNESS_DISTILL_CAMPAIGN_ID": CAMPAIGN,
                }
            )
            process = subprocess.Popen(
                [
                    str(python),
                    "-m",
                    "uvicorn",
                    "harness_distill.interactive_sol_dagger:app_from_environment",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(row["proxy_port"]),
                    "--log-level",
                    "warning",
                ],
                env=environment,
                cwd=workspace_root,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            proxies.append((row, process, stream))
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if all(_healthy(f"http://127.0.0.1:{row['proxy_port']}/healthz") for row in rows):
                break
            if any(process.poll() is not None for _row, process, _stream in proxies):
                raise InteractiveRolloutOpsError("interactive proxy exited before readiness")
            time.sleep(1)
        else:
            raise InteractiveRolloutOpsError("interactive proxies did not become ready")
        for row in rows:
            stdout = (runtime / f"worker_{row['index']:02d}.stdout").open("xb")
            stderr = (runtime / f"worker_{row['index']:02d}.stderr").open("xb")
            process = subprocess.Popen(
                [
                    str(python),
                    "-m",
                    "harness_posttrain_eval.launch_one",
                    "--spec",
                    row["config"],
                ],
                stdout=stdout,
                stderr=stderr,
                env=base_environment,
                cwd=workspace_root,
                start_new_session=True,
            )
            workers.append((row, process, stdout, stderr))
        failures = 0
        for row, process, stdout, stderr in workers:
            returncode = process.wait()
            stdout.close()
            stderr.close()
            _write_new(runtime / f"worker_{row['index']:02d}.exit.json", {"exit_code": returncode})
            failures += int(returncode != 0)
        return failures
    finally:
        for _row, process, stream in proxies:
            _terminate(process)
            stream.close()


def run_bundle(
    *,
    bundle_path: Path,
    upstream_base_url: str,
    python: Path,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> int:
    bundle = audit_bundle(bundle_path)
    python = Path(os.path.abspath(python))
    if not upstream_base_url.startswith("http://127.0.0.1:"):
        raise InteractiveRolloutOpsError("student upstream must be loopback")
    if not python.is_file() or not 1 <= max_concurrency <= 8:
        raise InteractiveRolloutOpsError("runtime Python or concurrency is invalid")
    base_environment, workspace_root = _runtime_import_environment(python)
    runtime = bundle_path.parent / "runtime"
    failures = 0
    rows = list(bundle["rows"])
    for offset in range(0, len(rows), max_concurrency):
        failures += _run_wave(
            rows[offset : offset + max_concurrency],
            upstream_base_url=upstream_base_url,
            python=python,
            runtime=runtime,
            base_environment=base_environment,
            workspace_root=workspace_root,
        )
    _write_new(
        runtime / "structural_status.json",
        {"runs": len(rows), "complete": len(rows) - failures, "failed": failures},
    )
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render")
    render.add_argument("--source-bundle", type=Path, required=True)
    render.add_argument("--output-root", type=Path, required=True)
    render.add_argument("--proxy-base-port", type=int, default=DEFAULT_PROXY_BASE_PORT)
    render.add_argument("--environment-base-port", type=int, default=DEFAULT_ENVIRONMENT_BASE_PORT)
    audit = commands.add_parser("audit")
    audit.add_argument("--bundle", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--bundle", type=Path, required=True)
    run.add_argument("--upstream-base-url", required=True)
    run.add_argument("--python", type=Path, default=Path(sys.executable))
    run.add_argument("--max-concurrency", type=int, default=DEFAULT_MAX_CONCURRENCY)
    args = parser.parse_args(argv)
    if args.command == "render":
        print(
            json.dumps(
                render_bundle(
                    source_bundle=args.source_bundle,
                    output_root=args.output_root,
                    proxy_base_port=args.proxy_base_port,
                    environment_base_port=args.environment_base_port,
                ),
                sort_keys=True,
            )
        )
        return 0
    if args.command == "audit":
        print(json.dumps(audit_bundle(args.bundle), sort_keys=True))
        return 0
    return run_bundle(
        bundle_path=args.bundle,
        upstream_base_url=args.upstream_base_url,
        python=args.python,
        max_concurrency=args.max_concurrency,
    )


if __name__ == "__main__":
    raise SystemExit(main())
