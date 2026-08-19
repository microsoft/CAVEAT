#!/usr/bin/env python3
"""Render the matched S32 Exact8 prompt-only ablation, primary cohort first."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
PYTHON = WORKSPACE / ".venv/bin/python"
SOURCE_ROOT = WORKSPACE / ".worktrees/c2_s32_precommit_prompt_w1"
SOURCE_GIT_ROOT = SOURCE_ROOT
SOURCE_COMMIT = "2dc96be428b7d4025b28a77237414fc4e2e6ed79"
DELIBERATIVE_SHA256 = (
    "3cf6c38ad3f6077ecf16a87e79e03fb923e122e3404d98576603fc8b9f935c02"
)
HARNESS_SHA256 = (
    "8d7d4ca7b48442faf5adae963260ed57873f74ef22e4a3a695f21b74f73043ec"
)
ENDPOINT_SHA256 = (
    "46438be5e013bf5a8c98f1f0e4a30bfcdf4ce1333cc4cc191ec572a6639e6b79"
)
COMPOSITE_SHA256 = (
    "e5670a822f091dc16fbba84a1d35a024512588b03d56aa6da80a82a23ed133f5"
)
BASE_URL = "http://127.0.0.1:19320/v1"
BASE_PORT = 52700
CAMPAIGN_ROOT = WORKSPACE / "results/harness_posttrain_campaign2_20260814"
BASELINE_ROOT = CAMPAIGN_ROOT / "step32_rebind_candidate_exact8_r2"
BASELINE_MANIFEST = BASELINE_ROOT / "launch_manifest.json"
OUTPUT = CAMPAIGN_ROOT / "s32_prompt_ablate_exact8_r1"

# Predeclared before launch: the four baseline S32 cells that selected HERO50.
PRIMARY_INDICES = (3, 4, 5, 6)
SECONDARY_INDICES = (0, 1, 2, 7)
LAUNCH_ORDER = PRIMARY_INDICES + SECONDARY_INDICES


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def stripped(spec: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(spec)
    value["model"]["base_url"] = "<ENDPOINT>"
    value["port"] = "<PORT>"
    value["out_dir"] = "<OUTPUT>"
    audit = value["audit_contract"]
    for field in (
        "endpoint_manifest_sha256",
        "sol_dagger_endpoint_receipt_sha256",
        "harness_sha256",
        "sol_dagger_evaluation_harness_modified",
        "prompt_ablation_source_git_sha",
        "prompt_ablation_deliberative_file_sha256",
    ):
        audit.pop(field, None)
    value["runtime_environment"]["AGENTARENA_RUNTIME_SOURCE_ATTESTATION"] = (
        "<HARNESS>"
    )
    return value


def main() -> None:
    if OUTPUT.exists():
        raise RuntimeError(f"output already exists: {OUTPUT}")
    if (
        subprocess.check_output(
            ["git", "-C", str(SOURCE_GIT_ROOT), "rev-parse", "HEAD"], text=True
        ).strip()
        != SOURCE_COMMIT
    ):
        raise RuntimeError("prompt source commit changed")
    deliberative = SOURCE_ROOT / "agentarena/scaffolds/browseruse_deliberative.py"
    if file_sha(deliberative) != DELIBERATIVE_SHA256:
        raise RuntimeError("prompt source deliberative file changed")
    subprocess.run(
        [
            "git",
            "-C",
            str(SOURCE_GIT_ROOT),
            "diff",
            "--quiet",
            "HEAD",
            "--",
            "agentarena/scaffolds/browseruse_deliberative.py",
        ],
        check=True,
    )

    baseline = json.loads(BASELINE_MANIFEST.read_text(encoding="utf-8"))
    if baseline.get("matrix_sha256") != (
        "9a9d1d5d7eb52b3f1063b54e19bb96823ac53b5a03e7857434a89cfca952bcac"
    ) or len(baseline.get("launches") or []) != 8:
        raise RuntimeError("baseline Exact8 matrix changed")

    configs: dict[int, dict[str, Any]] = {}
    launches_by_index: dict[int, dict[str, Any]] = {}
    invariance: list[dict[str, Any]] = []
    results_root = OUTPUT / "run_results"
    for index, source_launch in enumerate(baseline["launches"]):
        source_config = Path(source_launch["config"])
        before = json.loads(source_config.read_text(encoding="utf-8"))
        spec = copy.deepcopy(before)
        spec["model"]["base_url"] = BASE_URL
        spec["port"] = BASE_PORT + index
        result = results_root / Path(source_launch["results"]).name
        spec["out_dir"] = str(result)
        audit = spec["audit_contract"]
        audit["endpoint_manifest_sha256"] = ENDPOINT_SHA256
        audit["sol_dagger_endpoint_receipt_sha256"] = ENDPOINT_SHA256
        audit["sol_dagger_candidate_composite_sha256"] = COMPOSITE_SHA256
        audit["harness_sha256"] = HARNESS_SHA256
        audit["sol_dagger_evaluation_harness_modified"] = True
        audit["prompt_ablation_source_git_sha"] = SOURCE_COMMIT
        audit["prompt_ablation_deliberative_file_sha256"] = DELIBERATIVE_SHA256
        spec["runtime_environment"][
            "AGENTARENA_RUNTIME_SOURCE_ATTESTATION"
        ] = HARNESS_SHA256
        if stripped(before) != stripped(spec):
            raise RuntimeError(f"unexpected model-facing difference: {source_config}")

        config = OUTPUT / "configs" / source_config.name
        write_new(config, spec)
        launch = copy.deepcopy(source_launch)
        launch.update(
            {
                "argv": [
                    str(PYTHON),
                    "-m",
                    "harness_posttrain_eval.launch_one",
                    "--spec",
                    str(config),
                ],
                "audit_contract": audit,
                "config": str(config),
                "config_sha256": file_sha(config),
                "environment": spec["runtime_environment"],
                "port": spec["port"],
                "results": spec["out_dir"],
            }
        )
        configs[index] = spec
        launches_by_index[index] = launch
        invariance.append(
            {
                "index": index,
                "run_id": spec["run_id"],
                "cohort": "primary" if index in PRIMARY_INDICES else "secondary",
                "source_config": str(source_config),
                "source_config_sha256": file_sha(source_config),
                "candidate_config": str(config),
                "candidate_config_sha256": file_sha(config),
                "stripped_projection_sha256": digest(stripped(spec)),
            }
        )

    manifest = copy.deepcopy(baseline)
    manifest.update(
        {
            "base_port": BASE_PORT,
            "endpoint_manifest_sha256": ENDPOINT_SHA256,
            "results_root": str(results_root),
            "launches": [launches_by_index[index] for index in LAUNCH_ORDER],
            "prompt_ablation": {
                "kind": "site_and_task_neutral_pretransaction_reconciliation_prompt_only",
                "source_root": str(SOURCE_ROOT),
                "source_git_sha": SOURCE_COMMIT,
                "deliberative_file_sha256": DELIBERATIVE_SHA256,
                "harness_sha256": HARNESS_SHA256,
                "baseline_harness_sha256": (
                    "d14915ced60a940a27e5d36823acac57762c9754aee71d23b97c40eb506b7da4"
                ),
                "baseline_manifest": str(BASELINE_MANIFEST),
                "baseline_manifest_file_sha256": file_sha(BASELINE_MANIFEST),
            },
            "predeclared_cohorts": {
                "primary": {
                    "criterion": "baseline S32 Exact8 selected HERO50",
                    "source_indices": list(PRIMARY_INDICES),
                    "run_ids": [configs[index]["run_id"] for index in PRIMARY_INDICES],
                },
                "secondary": {
                    "criterion": "remaining full Exact8 cells",
                    "source_indices": list(SECONDARY_INDICES),
                    "run_ids": [configs[index]["run_id"] for index in SECONDARY_INDICES],
                },
                "launch_order_source_indices": list(LAUNCH_ORDER),
                "concurrency": 4,
            },
        }
    )
    manifest.pop("launch_manifest_sha256", None)
    manifest["launch_manifest_sha256"] = digest(manifest)
    manifest_path = OUTPUT / "launch_manifest.json"
    write_new(manifest_path, manifest)
    write_new(
        OUTPUT / "config_invariance.json",
        {
            "schema": "c2-s32-prompt-only-exact8-invariance.v1",
            "status": "exact_except_endpoint_output_port_and_prompt_attestation",
            "baseline_manifest": str(BASELINE_MANIFEST),
            "baseline_manifest_sha256": file_sha(BASELINE_MANIFEST),
            "launch_manifest": str(manifest_path),
            "launch_manifest_file_sha256": file_sha(manifest_path),
            "launch_manifest_body_sha256": manifest["launch_manifest_sha256"],
            "source_root": str(SOURCE_ROOT),
            "source_git_sha": SOURCE_COMMIT,
            "deliberative_file_sha256": DELIBERATIVE_SHA256,
            "harness_sha256": HARNESS_SHA256,
            "allowed_differences": [
                "model.base_url",
                "port",
                "out_dir",
                "audit_contract.endpoint identity",
                "audit_contract.harness/prompt source attestation",
                "runtime_environment.AGENTARENA_RUNTIME_SOURCE_ATTESTATION",
            ],
            "predeclared_primary_indices": list(PRIMARY_INDICES),
            "predeclared_secondary_indices": list(SECONDARY_INDICES),
            "launch_order_source_indices": list(LAUNCH_ORDER),
            "configs": invariance,
        },
    )
    print(
        json.dumps(
            {
                "status": "prepared",
                "runs": 8,
                "primary_first": list(PRIMARY_INDICES),
                "launch_manifest": str(manifest_path),
                "launch_manifest_sha256": manifest["launch_manifest_sha256"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
