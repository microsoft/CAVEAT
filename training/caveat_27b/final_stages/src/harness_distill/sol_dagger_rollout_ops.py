"""Render and run fresh Qwen-state rollouts without changing the fixed-v7 harness."""

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

SCHEMA = "harness-distill.sol-dagger-rollout-bundle.v1"
CAMPAIGN = "sol-dagger-qwen-state-collection-v1"
VARIANTS = ("graded", "graded3", "graded4", "mixed")
SOURCE_FILENAMES = {
    "graded": "00_laptop-graded-combined-r00-grpo.json",
    "graded3": "02_laptop-graded3-combined-r00-grpo.json",
    "graded4": "04_laptop-graded4-combined-r00-grpo.json",
    "mixed": "06_laptop-mixed-combined-r00-grpo.json",
}
STUDENT_ALIAS = "qwen35-browser-action-fixed-v7-repair-6cf2d5a0154f-exact-lora"
HARNESS_SHA256 = "d14915ced60a940a27e5d36823acac57762c9754aee71d23b97c40eb506b7da4"
LIMIT_SHA256 = "c0d3219add3dec27fd38d1d504370f52f7051260691259859f60429de0747e2a"


class RolloutOpsError(RuntimeError):
    """A rollout configuration or runtime artifact violated the frozen contract."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RolloutOpsError(f"unsafe or absent JSON: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RolloutOpsError(f"JSON is not an object: {path}")
    return value


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(value) + b"\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    except FileExistsError as exc:
        raise RolloutOpsError(f"create-only target exists: {path}") from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _variant(config: dict[str, Any]) -> str:
    value = config.get("task", {}).get("metadata", {}).get("variant")
    if value not in VARIANTS:
        raise RolloutOpsError("source config has an unexpected task variant")
    return str(value)


def _model_facing_contract(config: dict[str, Any]) -> dict[str, Any]:
    runtime = dict(config.get("runtime_environment") or {})
    runtime.pop("AGENTARENA_CACHE_NONCE", None)
    runtime.pop("AGENTARENA_EVALUATION_INPUT_ATTESTATION", None)
    model = dict(config.get("model") or {})
    for field in ("base_url", "deployment", "name"):
        model.pop(field, None)
    return {
        "campaign_id": config.get("campaign_id"),
        "condition": config.get("condition"),
        "env": config.get("env"),
        "headless": config.get("headless"),
        "max_steps": config.get("max_steps"),
        "run_timeout_seconds": config.get("run_timeout_seconds"),
        "scaffold": config.get("scaffold"),
        "task": config.get("task"),
        "model_nonrouting": model,
        "runtime_environment_nonidentity": runtime,
    }


def _block_seed(variant: str, replica: int) -> int:
    digest = hashlib.sha256(f"{CAMPAIGN}::{variant}::{replica}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def render_bundle(
    *,
    source_bundle: Path,
    output_root: Path,
    upstream_proxy_port: int,
    environment_base_port: int,
    replicas_per_variant: int,
) -> dict[str, Any]:
    source_bundle = source_bundle.resolve()
    output_root = output_root.resolve()
    if output_root.exists() or output_root.is_symlink():
        raise RolloutOpsError("rollout output root must be fresh")
    if replicas_per_variant != 6:
        raise RolloutOpsError("campaign requires exactly six fresh rollouts per variant")
    if not (1024 <= upstream_proxy_port <= 64000 and 1024 <= environment_base_port <= 64000):
        raise RolloutOpsError("port range is invalid")
    output_root.mkdir(parents=True)
    configs_dir = output_root / "configs"
    traces_dir = output_root / "proxy_traces"
    results_dir = output_root / "run_results"
    runtime_dir = output_root / "runtime"
    for directory in (configs_dir, traces_dir, results_dir, runtime_dir):
        directory.mkdir()

    rows: list[dict[str, Any]] = []
    source_seeds: set[int] = set()
    index = 0
    for variant in VARIANTS:
        source_path = source_bundle / "configs" / SOURCE_FILENAMES[variant]
        source = _read_json(source_path)
        if (
            _variant(source) != variant
            or source.get("condition") != "combined"
            or source.get("scaffold") != "browseruse-deliberative"
            or source.get("max_steps") != 4000
            or source.get("run_timeout_seconds") != 36000
            or source.get("audit_contract", {}).get("harness_sha256") != HARNESS_SHA256
            or source.get("audit_contract", {}).get("limit_contract_sha256") != LIMIT_SHA256
        ):
            raise RolloutOpsError("source config is not the frozen combined fixed-v7 cell")
        source_seeds.add(int(source["block_seed"]))
        facing = _model_facing_contract(source)
        for replica in range(replicas_per_variant):
            config = json.loads(json.dumps(source))
            run_id = f"{CAMPAIGN}::{variant}::r{replica:02d}"
            proxy_port = upstream_proxy_port + index
            environment_port = environment_base_port + index
            result_path = results_dir / f"laptop-{variant}-combined-r{replica:02d}"
            config["arm"] = "trained"
            config["block_seed"] = _block_seed(variant, replica)
            config["model"] = {
                **source["model"],
                "base_url": f"http://127.0.0.1:{proxy_port}/v1",
                "deployment": STUDENT_ALIAS,
                "name": STUDENT_ALIAS,
            }
            config["out_dir"] = str(result_path)
            config["pair_id"] = f"{CAMPAIGN}::{variant}::r{replica:02d}"
            config["port"] = environment_port
            config["run_id"] = run_id
            config["runtime_environment"] = {
                **source["runtime_environment"],
                "AGENTARENA_CACHE_NONCE": run_id,
                "AGENTARENA_EVALUATION_INPUT_ATTESTATION": hashlib.sha256(
                    canonical_bytes({"campaign": CAMPAIGN, "variant": variant, "replica": replica})
                ).hexdigest(),
            }
            config["audit_contract"] = {
                "campaign": CAMPAIGN,
                "collection_only": True,
                "harness_sha256": HARNESS_SHA256,
                "limit_contract_sha256": LIMIT_SHA256,
                "source_config_sha256": sha256_file(source_path),
                "teacher_or_outcome_used_for_render": False,
            }
            if _model_facing_contract(config) != facing:
                raise RolloutOpsError("render changed the model-facing harness contract")
            config_path = configs_dir / f"{index:02d}_{variant}_r{replica:02d}.json"
            _write_new(config_path, config)
            rows.append(
                {
                    "index": index,
                    "variant": variant,
                    "replica": replica,
                    "run_id": run_id,
                    "block_seed": config["block_seed"],
                    "source_config": str(source_path),
                    "source_config_sha256": sha256_file(source_path),
                    "config": str(config_path),
                    "config_sha256": sha256_file(config_path),
                    "proxy_port": proxy_port,
                    "environment_port": environment_port,
                    "trace_path": str(traces_dir / f"{index:02d}_{variant}_r{replica:02d}.jsonl"),
                    "result_path": str(result_path),
                }
            )
            index += 1
    if len(rows) != 24 or len({row["block_seed"] for row in rows}) != 24:
        raise RolloutOpsError("rendered rollout matrix is incomplete or has duplicate seeds")
    if any(row["block_seed"] in source_seeds for row in rows):
        raise RolloutOpsError("collection block seed overlaps the frozen evaluation sources")
    body = {
        "schema": SCHEMA,
        "status": "preregistered",
        "campaign": CAMPAIGN,
        "student": {
            "candidate": "step24-amazon-r00-repair-sft",
            "served_alias": STUDENT_ALIAS,
        },
        "source_bundle": str(source_bundle),
        "source_bundle_manifest_sha256": sha256_file(source_bundle / "bundle.json"),
        "matrix": {
            "condition": "combined",
            "variants": list(VARIANTS),
            "replicas_per_variant": replicas_per_variant,
            "runs": len(rows),
        },
        "invariance": {
            "model_facing_harness_unchanged": True,
            "scaffold": "browseruse-deliberative",
            "harness_sha256": HARNESS_SHA256,
            "limit_contract_sha256": LIMIT_SHA256,
            "max_steps": 4000,
            "run_timeout_seconds": 36000,
            "evaluation_cells_used_for_training": False,
        },
        "selection": {
            "teacher_or_outcome_used_for_render": False,
            "train_corrections": 64,
            "heldout_corrections": 24,
            "retention_rows": 32,
            "score_selection": False,
        },
        "rows": rows,
    }
    payload = {**body, "bundle_sha256": hashlib.sha256(canonical_bytes(body)).hexdigest()}
    _write_new(output_root / "bundle.json", payload)
    return payload


def audit_bundle(path: Path) -> dict[str, Any]:
    path = path.resolve()
    bundle = _read_json(path)
    body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    if (
        bundle.get("schema") != SCHEMA
        or bundle.get("status") != "preregistered"
        or bundle.get("bundle_sha256") != hashlib.sha256(canonical_bytes(body)).hexdigest()
        or bundle.get("invariance", {}).get("model_facing_harness_unchanged") is not True
        or bundle.get("selection", {}).get("score_selection") is not False
        or len(bundle.get("rows", [])) != 24
    ):
        raise RolloutOpsError("rollout bundle policy or self-hash changed")
    source_bundle = Path(bundle["source_bundle"])
    if sha256_file(source_bundle / "bundle.json") != bundle["source_bundle_manifest_sha256"]:
        raise RolloutOpsError("source evaluation bundle changed")
    source_seeds: set[int] = set()
    for variant in VARIANTS:
        source = _read_json(source_bundle / "configs" / SOURCE_FILENAMES[variant])
        source_seeds.add(int(source["block_seed"]))
    seen_seeds: set[int] = set()
    for row in bundle["rows"]:
        config_path = Path(row["config"])
        source_path = Path(row["source_config"])
        if (
            sha256_file(config_path) != row["config_sha256"]
            or sha256_file(source_path) != row["source_config_sha256"]
        ):
            raise RolloutOpsError("rollout config bytes changed")
        config = _read_json(config_path)
        source = _read_json(source_path)
        if (
            _model_facing_contract(config) != _model_facing_contract(source)
            or config["block_seed"] in source_seeds
            or config["block_seed"] in seen_seeds
            or config["model"]["name"] != STUDENT_ALIAS
            or config["scaffold"] != "browseruse-deliberative"
        ):
            raise RolloutOpsError("rollout config no longer satisfies invariance")
        seen_seeds.add(config["block_seed"])
    return bundle


def _healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310
            return response.status == 200
    except Exception:  # noqa: BLE001
        return False


def run_bundle(*, bundle_path: Path, upstream_base_url: str, python: Path) -> int:
    bundle = audit_bundle(bundle_path)
    if not upstream_base_url.startswith("http://127.0.0.1:"):
        raise RolloutOpsError("student upstream must be a loopback endpoint")
    if not python.is_file():
        raise RolloutOpsError("runtime Python is absent")
    runtime = bundle_path.parent / "runtime"
    proxies: list[tuple[subprocess.Popen[bytes], Any]] = []
    workers: list[tuple[dict[str, Any], subprocess.Popen[bytes], Any, Any]] = []
    try:
        for row in bundle["rows"]:
            trace = Path(row["trace_path"])
            if trace.exists() or trace.is_symlink():
                raise RolloutOpsError("proxy trace target is not fresh")
            proxy_log = runtime / f"proxy_{row['index']:02d}.log"
            proxy_stream = proxy_log.open("xb")
            environment = os.environ.copy()
            environment.update(
                {
                    "HARNESS_DISTILL_UPSTREAM_BASE_URL": upstream_base_url,
                    "HARNESS_DISTILL_TRACE_JSONL": str(trace),
                    "HARNESS_DISTILL_SESSION_ID": row["run_id"],
                }
            )
            process = subprocess.Popen(
                [
                    str(python),
                    "-m",
                    "uvicorn",
                    "harness_distill.proxy:app_from_environment",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(row["proxy_port"]),
                ],
                env=environment,
                stdout=proxy_stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            proxies.append((process, proxy_stream))
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if all(
                _healthy(f"http://127.0.0.1:{row['proxy_port']}/healthz") for row in bundle["rows"]
            ):
                break
            if any(process.poll() is not None for process, _stream in proxies):
                raise RolloutOpsError("a Qwen capture proxy exited before readiness")
            time.sleep(1)
        else:
            raise RolloutOpsError("Qwen capture proxies did not become ready")

        for row in bundle["rows"]:
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
        _write_new(
            runtime / "structural_status.json",
            {"runs": len(workers), "complete": len(workers) - failures, "failed": failures},
        )
        return 0 if failures == 0 else 1
    finally:
        for process, _stream in proxies:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process, stream in proxies:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=20)
            stream.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render")
    render.add_argument("--source-bundle", type=Path, required=True)
    render.add_argument("--output-root", type=Path, required=True)
    render.add_argument("--proxy-base-port", type=int, default=18600)
    render.add_argument("--environment-base-port", type=int, default=49000)
    render.add_argument("--replicas-per-variant", type=int, default=6)
    audit = commands.add_parser("audit")
    audit.add_argument("--bundle", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--bundle", type=Path, required=True)
    run.add_argument("--upstream-base-url", required=True)
    run.add_argument("--python", type=Path, default=Path(sys.executable))
    arguments = parser.parse_args(argv)
    if arguments.command == "render":
        value = render_bundle(
            source_bundle=arguments.source_bundle,
            output_root=arguments.output_root,
            upstream_proxy_port=arguments.proxy_base_port,
            environment_base_port=arguments.environment_base_port,
            replicas_per_variant=arguments.replicas_per_variant,
        )
        print(json.dumps(value, sort_keys=True))
        return 0
    if arguments.command == "audit":
        print(json.dumps(audit_bundle(arguments.bundle), sort_keys=True))
        return 0
    return run_bundle(
        bundle_path=arguments.bundle,
        upstream_base_url=arguments.upstream_base_url,
        python=arguments.python,
    )


if __name__ == "__main__":
    raise SystemExit(main())
