#!/usr/bin/env python3
"""Clone the proven laptop specs for a new receipt-bound candidate endpoint.

Only endpoint identity and non-model-facing output/port fields may change.
The task, scaffold, harness, limits, prompts, and runtime behavior remain byte
identical to the already-executed campaign-2 macro4 or exact8 specs.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


WORKSPACE = Path("/home/t-yuxuanli/preference-fidelity")
PYTHON = WORKSPACE / ".venv/bin/python"
TEMPLATES = {
    "macro4": (
        WORKSPACE
        / "results/harness_posttrain_campaign2_20260814/"
        "macro_recovery_candidate_dev_r3/launch_manifest.json"
    ),
    "exact8": (
        WORKSPACE
        / "results/harness_posttrain_campaign2_20260814/"
        "macro_recovery_candidate_exact_r4_r3/bundle/launch_manifest.json"
    ),
}
OLD_ALIAS = "qwen35-browser-action-step26-macro-recovery-ce-7c0a420c0536-exact-lora"
OLD_BASE_URL = "http://127.0.0.1:18543/v1"
HEX64 = set("0123456789abcdef")


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


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, sort_keys=True, indent=2).encode() + b"\n"
    with path.open("xb") as stream:
        stream.write(payload)


def _strip_allowed(spec: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(spec)
    model = value["model"]
    for field in ("name", "deployment", "base_url"):
        model[field] = f"<ENDPOINT:{field}>"
    value["port"] = "<PORT>"
    value["out_dir"] = "<OUTPUT>"
    audit = value["audit_contract"]
    for field in (
        "endpoint_manifest_sha256",
        "sol_dagger_endpoint_receipt_sha256",
        "sol_dagger_candidate_composite_sha256",
    ):
        if field in audit:
            audit[field] = f"<ENDPOINT:{field}>"
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=tuple(TEMPLATES), required=True)
    parser.add_argument("--alias", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--endpoint-binding-sha256", required=True)
    parser.add_argument("--candidate-composite-sha256", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--base-port", type=int, required=True)
    arguments = parser.parse_args()

    for label, value in (
        ("endpoint binding", arguments.endpoint_binding_sha256),
        ("candidate composite", arguments.candidate_composite_sha256),
    ):
        if len(value) != 64 or any(character not in HEX64 for character in value):
            parser.error(f"{label} must be a lowercase SHA-256")
    output = arguments.output_root.resolve()
    if output.exists():
        parser.error(f"output root already exists: {output}")
    if not PYTHON.is_file():
        parser.error(f"proven workspace interpreter is absent: {PYTHON}")

    source_manifest_path = TEMPLATES[arguments.mode]
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    launches: list[dict[str, Any]] = []
    invariance: list[dict[str, Any]] = []
    results_root = output / "run_results"
    for index, source_launch in enumerate(source_manifest["launches"]):
        source_config_path = Path(source_launch["config"])
        source_spec = json.loads(source_config_path.read_text(encoding="utf-8"))
        if (
            source_spec["model"]["name"] != OLD_ALIAS
            or source_spec["model"]["deployment"] != OLD_ALIAS
            or source_spec["model"]["base_url"] != OLD_BASE_URL
        ):
            parser.error(f"template endpoint changed: {source_config_path}")
        spec = copy.deepcopy(source_spec)
        spec["model"]["name"] = arguments.alias
        spec["model"]["deployment"] = arguments.alias
        spec["model"]["base_url"] = arguments.base_url
        spec["port"] = arguments.base_port + index
        result = results_root / Path(source_launch["results"]).name
        spec["out_dir"] = str(result)
        audit = spec["audit_contract"]
        audit["endpoint_manifest_sha256"] = arguments.endpoint_binding_sha256
        if "sol_dagger_endpoint_receipt_sha256" in audit:
            audit["sol_dagger_endpoint_receipt_sha256"] = (
                arguments.endpoint_binding_sha256
            )
        if "sol_dagger_candidate_composite_sha256" in audit:
            audit["sol_dagger_candidate_composite_sha256"] = (
                arguments.candidate_composite_sha256
            )
        if _strip_allowed(source_spec) != _strip_allowed(spec):
            parser.error(f"model-facing harness changed: {source_config_path}")

        config = output / "configs" / f"{index:02d}_{source_config_path.name}"
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
                "config_sha256": file_digest(config),
                "environment": spec["runtime_environment"],
                "port": spec["port"],
                "results": spec["out_dir"],
            }
        )
        launches.append(launch)
        invariance.append(
            {
                "run_id": spec["run_id"],
                "source_config": str(source_config_path),
                "source_config_sha256": file_digest(source_config_path),
                "candidate_config": str(config),
                "candidate_config_sha256": file_digest(config),
                "stripped_projection_sha256": digest(_strip_allowed(spec)),
            }
        )

    manifest = copy.deepcopy(source_manifest)
    manifest.update(
        {
            "base_port": arguments.base_port,
            "endpoint_manifest_sha256": arguments.endpoint_binding_sha256,
            "results_root": str(results_root),
            "launches": launches,
        }
    )
    manifest.pop("launch_manifest_sha256", None)
    manifest["launch_manifest_sha256"] = digest(manifest)
    manifest_path = output / "launch_manifest.json"
    write_new(manifest_path, manifest)
    invariance_value = {
        "schema": "c2-corrective-fast-eval-invariance.v1",
        "status": "exact_except_endpoint_output_and_port",
        "mode": arguments.mode,
        "source_manifest": str(source_manifest_path),
        "source_manifest_sha256": file_digest(source_manifest_path),
        "launch_manifest": str(manifest_path),
        "launch_manifest_sha256": manifest["launch_manifest_sha256"],
        "allowed_differences": [
            "model.name",
            "model.deployment",
            "model.base_url",
            "port",
            "out_dir",
            "audit_contract.endpoint identity",
        ],
        "configs": invariance,
    }
    write_new(output / "config_invariance.json", invariance_value)
    print(
        json.dumps(
            {
                "status": "prepared",
                "mode": arguments.mode,
                "runs": len(launches),
                "launch_manifest": str(manifest_path),
                "launch_manifest_sha256": manifest["launch_manifest_sha256"],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
