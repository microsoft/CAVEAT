"""Capture a live exact-LoRA vLLM endpoint without trusting hand-written records.

The exact-LoRA manifest and its publish-once receipt live on the cluster PVC.
This utility executes a small verifier inside the serving pod, using the exact
finalizer source that is present there.  The verifier rehashes the current
parent, adapter, tokenizer, manifest, and receipt, then reads PID 1's real
``cmdline`` and ``environ``.  Only the compact, non-secret attestation is
returned to the workstation.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

from .common import (
    IntegrityError,
    canonical_bytes,
    sha256_bytes,
    write_json_create_only,
)
from .launcher import vllm_serve_model_index

_CAPTURE_SCHEMA = "caveat-27b-eval.exact-live-capture.v2"
_CONTRACT_SCHEMA = "caveat-27b-eval.exact-lora-contract.v1"
_MANIFEST_SCHEMA = "caveat-27b.exact-lora-inference.v1"
_COMPOSITE_SCHEMA = "caveat-27b.exact-lora-composite.v1"
_RECEIPT_SCHEMA = "caveat-27b.exact-lora-manifest-receipt.v1"
_SHA256 = re.compile(r"[0-9a-f]{64}")


_REMOTE_VERIFIER = r"""
import hashlib
import json
import os
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1]).resolve()
receipt_path = Path(sys.argv[2]).resolve()
source_root = Path(sys.argv[3]).resolve()
arm = sys.argv[4]
sys.path.insert(0, str(source_root / "src"))

from caveat_27b.artifacts import read_json, sha256_file
from caveat_27b.exact_lora import component_identity, exact_lora_composite_sha256


def require(condition, message):
    if not condition:
        raise SystemExit(message)


manifest = read_json(manifest_path)
receipt = read_json(receipt_path)
manifest_sha256 = sha256_file(manifest_path)
receipt_sha256 = sha256_file(receipt_path)
require(manifest.get("schema") == "caveat-27b.exact-lora-inference.v1", "bad exact manifest schema")
require(manifest.get("status") == "ok", "exact manifest is not successful")
require(manifest.get("serving_mode") == "exact_peft_lora", "bad exact serving mode")
require(manifest.get("final_model_directory_published") is False, "manifest claims merged weights")
require(manifest.get("refinement_update") == 20, "exact manifest is not step 20")
require(set(manifest.get("arms", {})) == {"base", "trained"}, "manifest lacks two exact arms")
require(receipt.get("schema") == "caveat-27b.exact-lora-manifest-receipt.v1", "bad receipt schema")
require(receipt.get("status") == "ok", "exact manifest receipt is not successful")
require(receipt.get("campaign_digest") == manifest.get("campaign_digest"), "receipt campaign differs")
require(Path(str(receipt.get("manifest_path", ""))).resolve() == manifest_path, "receipt manifest path differs")
require(receipt.get("manifest_sha256") == manifest_sha256, "receipt does not bind manifest bytes")
require(receipt.get("composite_pair_sha256") == manifest.get("composite_pair_sha256"), "receipt pair differs")
require(receipt.get("base_composite_sha256") == manifest["arms"]["base"].get("composite_sha256"), "receipt base differs")
require(receipt.get("trained_composite_sha256") == manifest["arms"]["trained"].get("composite_sha256"), "receipt trained differs")
refinement = manifest.get("receipts", {}).get("refinement", {})
refinement_path = Path(str(refinement.get("path", ""))).resolve()
require(refinement_path.is_file(), "refinement receipt is absent")
require(refinement.get("sha256") == sha256_file(refinement_path), "refinement receipt bytes changed")
require(receipt.get("refinement_receipt_sha256") == refinement.get("sha256"), "manifest receipt refinement differs")
require(not (manifest_path.parent / "model").exists(), "ambiguous merged final model exists")
require(not (manifest_path.parent / "inference_manifest.json").exists(), "ambiguous merged manifest exists")

shared = manifest.get("shared_tokenizer", {})
shared_path = Path(str(shared.get("path", ""))).resolve()
require(shared_path.is_dir(), "shared tokenizer directory is absent")
require(sha256_file(shared_path / "tokenizer.json") == shared.get("tokenizer_json_sha256"), "tokenizer bytes changed")
from transformers import AutoTokenizer
tokenizer = AutoTokenizer.from_pretrained(shared_path, local_files_only=True, trust_remote_code=False)
template_sha256 = hashlib.sha256(str(tokenizer.chat_template).encode("utf-8")).hexdigest()
require(template_sha256 == shared.get("chat_template_sha256"), "chat template changed")

composites = {}
contract_arms = {}
for name, kind in (("base", "zero_control"), ("trained", "refinement_step20")):
    descriptor = manifest["arms"][name]
    parent = Path(str(descriptor.get("parent", {}).get("path", ""))).resolve()
    adapter = Path(str(descriptor.get("adapter", {}).get("path", ""))).resolve()
    require(parent.is_dir() and adapter.is_dir(), f"{name} component is absent")
    if name == arm:
        require(component_identity(parent) == descriptor.get("parent"), f"{name} parent bytes changed")
        require(component_identity(adapter) == descriptor.get("adapter"), f"{name} adapter bytes changed")
        require(sha256_file(adapter / "adapter_config.json") == descriptor.get("adapter_config_sha256"), f"{name} adapter config changed")
    composite = exact_lora_composite_sha256(
        parent_tree_sha256=descriptor["parent"]["tree_sha256"],
        adapter_tree_sha256=descriptor["adapter"]["tree_sha256"],
        adapter_config_sha256=descriptor["adapter_config_sha256"],
        tokenizer_json_sha256=shared["tokenizer_json_sha256"],
        chat_template_sha256=shared["chat_template_sha256"],
        dtype=manifest.get("inference", {}).get("dtype", ""),
    )
    require(composite == descriptor.get("composite_sha256"), f"{name} composite differs")
    composites[name] = composite
    contract_arms[name] = {
        "adapter_kind": kind,
        "parent_path": str(parent),
        "parent_tree_sha256": descriptor["parent"]["tree_sha256"],
        "adapter_path": str(adapter),
        "adapter_tree_sha256": descriptor["adapter"]["tree_sha256"],
        "adapter_config_sha256": descriptor["adapter_config_sha256"],
        "composite_weight_sha256": composite,
    }

pair_payload = {
    "schema": "caveat-27b.exact-lora-inference.v1",
    "base": composites["base"],
    "trained": composites["trained"],
}
pair_sha256 = hashlib.sha256(
    json.dumps(pair_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
).hexdigest()
require(pair_sha256 == manifest.get("composite_pair_sha256"), "composite pair differs")

zero_inventory = manifest.get("zero_adapter_attestation", {}).get("zero_tensor_inventory", {})
source_inventory = manifest.get("zero_adapter_attestation", {}).get("source_tensor_inventory", {})
zero_behavior = manifest.get("behavioral_attestation", {}).get("zero_noop", {})
trained_behavior = manifest.get("behavioral_attestation", {}).get("trained_nonnoop", {})
require(zero_inventory.get("all_zero") is True and zero_inventory.get("nonzero_elements") == 0, "baseline adapter is not all-zero")
require(zero_behavior.get("exact_logits_equal") is True and zero_behavior.get("maximum_absolute_logit_difference") == 0.0, "zero adapter is not an exact no-op")
require(source_inventory.get("all_zero") is False, "trained adapter is all-zero")
require(trained_behavior.get("allclose") is False, "trained adapter is a no-op")
require(float(trained_behavior.get("maximum_absolute_logit_difference", 0.0)) > 0.0, "trained max difference is absent")
require(float(trained_behavior.get("relative_l2_difference", 0.0)) > 0.0, "trained relative difference is absent")

cmdline = Path("/proc/1/cmdline").read_bytes()
argv = [value.decode("utf-8", "strict") for value in cmdline.split(b"\0") if value]
require(argv, "PID 1 has an empty command line")
environ = Path("/proc/1/environ").read_bytes().split(b"\0")
runtime_update = b"VLLM_ALLOW_RUNTIME_LORA_UPDATING"
runtime_update_present = any(
    item == runtime_update or item.startswith(runtime_update + b"=") for item in environ if item
)
fla_values = [
    item.split(b"=", 1)[1].decode("utf-8", "strict")
    for item in environ
    if item.startswith(b"FLA_TILELANG=")
]
require(not runtime_update_present, "runtime LoRA updating is present in PID 1 environment")
require(fla_values == ["0"], "PID 1 FLA_TILELANG is not exactly 0")

contract = {
    "schema": "caveat-27b-eval.exact-lora-contract.v1",
    "serving_mode": "exact_peft_lora",
    "manifest_sha256": manifest_sha256,
    "manifest_receipt_sha256": receipt_sha256,
    "composite_pair_sha256": pair_sha256,
    "refinement_receipt_sha256": refinement["sha256"],
    "refinement_update": 20,
    "shared_tokenizer": {
        "path": str(shared_path),
        "tokenizer_json_sha256": shared["tokenizer_json_sha256"],
        "chat_template_sha256": shared["chat_template_sha256"],
    },
    "zero_control": {
        "all_zero": True,
        "nonzero_elements": 0,
        "exact_logits_equal": True,
        "maximum_absolute_logit_difference": 0.0,
    },
    "trained_nonnoop": {
        "all_zero": False,
        "maximum_absolute_logit_difference": trained_behavior["maximum_absolute_logit_difference"],
        "relative_l2_difference": trained_behavior["relative_l2_difference"],
    },
    "arms": contract_arms,
}

print(json.dumps({
    "schema": "caveat-27b-eval.exact-live-capture.v2",
    "arm": arm,
    "manifest_sha256": manifest_sha256,
    "manifest_receipt_sha256": receipt_sha256,
    "served_model_name": manifest["arms"][arm]["served_model_name"],
    "composition": contract_arms[arm],
    "contract": contract,
    "argv": argv,
    "runtime": {
        "runtime_lora_updating_absent": True,
        "fla_tilelang": "0",
    },
}, sort_keys=True, separators=(",", ":")))
"""


def _digest(value: Any) -> str:
    return sha256_bytes(canonical_bytes(value))


def _valid_sha(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _option_values(argv: list[str], option: str) -> list[str]:
    values: list[str] = []
    for index, token in enumerate(argv):
        if token == option:
            if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                raise IntegrityError(f"live PID 1 command has an unbound {option}")
            values.append(argv[index + 1])
        elif token.startswith(option + "="):
            values.append(token.split("=", 1)[1])
    return values


def _one_option(argv: list[str], option: str) -> str:
    values = _option_values(argv, option)
    if len(values) != 1:
        raise IntegrityError(f"live PID 1 command must bind exactly one {option}")
    return values[0]


def _variadic_groups(argv: list[str], option: str) -> list[list[str]]:
    groups: list[list[str]] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == option:
            index += 1
            values: list[str] = []
            while index < len(argv) and not argv[index].startswith("--"):
                values.append(argv[index])
                index += 1
            if not values:
                raise IntegrityError(f"live PID 1 command has an unbound {option}")
            groups.append(values)
            continue
        if token.startswith(option + "="):
            value = token.split("=", 1)[1]
            if not value:
                raise IntegrityError(f"live PID 1 command has an unbound {option}")
            groups.append([value])
        index += 1
    return groups


def _composite_sha256(record: dict[str, Any], shared: dict[str, Any]) -> str:
    payload = {
        "schema": _COMPOSITE_SCHEMA,
        "execution": "peft_unmerged_exact_lora",
        "parent_tree_sha256": record["parent_tree_sha256"],
        "adapter_tree_sha256": record["adapter_tree_sha256"],
        "adapter_config_sha256": record["adapter_config_sha256"],
        "tokenizer_json_sha256": shared["tokenizer_json_sha256"],
        "chat_template_sha256": shared["chat_template_sha256"],
        "dtype": "bfloat16",
    }
    return _digest(payload)


def _validate_contract(contract: Any) -> dict[str, Any]:
    required = {
        "schema",
        "serving_mode",
        "manifest_sha256",
        "manifest_receipt_sha256",
        "composite_pair_sha256",
        "refinement_receipt_sha256",
        "refinement_update",
        "shared_tokenizer",
        "zero_control",
        "trained_nonnoop",
        "arms",
    }
    if not isinstance(contract, dict) or set(contract) != required:
        raise IntegrityError("remote exact-LoRA contract is not exact")
    if (
        contract.get("schema") != _CONTRACT_SCHEMA
        or contract.get("serving_mode") != "exact_peft_lora"
    ):
        raise IntegrityError(
            "remote exact-LoRA contract has an unsupported schema or mode"
        )
    if contract.get("refinement_update") != 20 or any(
        not _valid_sha(contract.get(key))
        for key in (
            "manifest_sha256",
            "manifest_receipt_sha256",
            "composite_pair_sha256",
            "refinement_receipt_sha256",
        )
    ):
        raise IntegrityError(
            "remote exact-LoRA contract has invalid receipt identities"
        )
    shared = contract.get("shared_tokenizer")
    if not isinstance(shared, dict) or set(shared) != {
        "path",
        "tokenizer_json_sha256",
        "chat_template_sha256",
    }:
        raise IntegrityError(
            "remote exact-LoRA contract has an invalid shared tokenizer"
        )
    if not PurePosixPath(str(shared.get("path", ""))).is_absolute() or any(
        not _valid_sha(shared.get(key))
        for key in ("tokenizer_json_sha256", "chat_template_sha256")
    ):
        raise IntegrityError("remote exact-LoRA tokenizer identity is invalid")
    if contract.get("zero_control") != {
        "all_zero": True,
        "nonzero_elements": 0,
        "exact_logits_equal": True,
        "maximum_absolute_logit_difference": 0.0,
    }:
        raise IntegrityError("remote baseline adapter is not an attested zero control")
    nonnoop = contract.get("trained_nonnoop")
    if (
        not isinstance(nonnoop, dict)
        or set(nonnoop)
        != {
            "all_zero",
            "maximum_absolute_logit_difference",
            "relative_l2_difference",
        }
        or nonnoop.get("all_zero") is not False
        or not isinstance(
            nonnoop.get("maximum_absolute_logit_difference"), (int, float)
        )
        or nonnoop["maximum_absolute_logit_difference"] <= 0
        or not isinstance(nonnoop.get("relative_l2_difference"), (int, float))
        or nonnoop["relative_l2_difference"] <= 0
    ):
        raise IntegrityError("remote trained adapter is not an attested non-noop")
    arms = contract.get("arms")
    if not isinstance(arms, dict) or set(arms) != {"base", "trained"}:
        raise IntegrityError("remote exact-LoRA contract does not contain two arms")
    composites: dict[str, str] = {}
    fields = {
        "adapter_kind",
        "parent_path",
        "parent_tree_sha256",
        "adapter_path",
        "adapter_tree_sha256",
        "adapter_config_sha256",
        "composite_weight_sha256",
    }
    for arm, kind in (("base", "zero_control"), ("trained", "refinement_step20")):
        record = arms[arm]
        if not isinstance(record, dict) or set(record) != fields:
            raise IntegrityError(f"remote {arm} exact-LoRA composition is not exact")
        if record.get("adapter_kind") != kind or any(
            not PurePosixPath(str(record.get(key, ""))).is_absolute()
            for key in ("parent_path", "adapter_path")
        ):
            raise IntegrityError(
                f"remote {arm} exact-LoRA component role/path is invalid"
            )
        if any(
            not _valid_sha(record.get(key))
            for key in (
                "parent_tree_sha256",
                "adapter_tree_sha256",
                "adapter_config_sha256",
                "composite_weight_sha256",
            )
        ):
            raise IntegrityError(
                f"remote {arm} exact-LoRA component identity is invalid"
            )
        computed = _composite_sha256(record, shared)
        if record["composite_weight_sha256"] != computed:
            raise IntegrityError(f"remote {arm} exact-LoRA composite hash is invalid")
        composites[arm] = computed
    pair = _digest(
        {
            "schema": _MANIFEST_SCHEMA,
            "base": composites["base"],
            "trained": composites["trained"],
        }
    )
    if contract["composite_pair_sha256"] != pair:
        raise IntegrityError("remote exact-LoRA pair hash is invalid")
    return contract


def _server_record(payload: Any, *, arm: str) -> tuple[dict[str, Any], dict[str, Any]]:
    expected_fields = {
        "schema",
        "arm",
        "manifest_sha256",
        "manifest_receipt_sha256",
        "served_model_name",
        "composition",
        "contract",
        "argv",
        "runtime",
    }
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise IntegrityError("remote live exact-LoRA capture is not exact")
    if payload.get("schema") != _CAPTURE_SCHEMA or payload.get("arm") != arm:
        raise IntegrityError(
            "remote live exact-LoRA capture has the wrong schema or arm"
        )
    contract = _validate_contract(payload.get("contract"))
    if (
        payload.get("manifest_sha256") != contract["manifest_sha256"]
        or payload.get("manifest_receipt_sha256") != contract["manifest_receipt_sha256"]
        or payload.get("composition") != contract["arms"][arm]
    ):
        raise IntegrityError("remote live capture differs from its exact-LoRA contract")
    served_name = payload.get("served_model_name")
    argv = payload.get("argv")
    if (
        not isinstance(served_name, str)
        or not served_name
        or not isinstance(argv, list)
    ):
        raise IntegrityError("remote live capture has no served name or PID 1 command")
    if not argv or not all(isinstance(value, str) and value for value in argv):
        raise IntegrityError("remote PID 1 command is malformed")
    if payload.get("runtime") != {
        "runtime_lora_updating_absent": True,
        "fla_tilelang": "0",
    }:
        raise IntegrityError(
            "remote PID 1 environment is not the frozen safe environment"
        )
    model_index = vllm_serve_model_index(argv)

    composition = contract["arms"][arm]
    shared = contract["shared_tokenizer"]
    if argv[model_index] != composition["parent_path"]:
        raise IntegrityError("live PID 1 positional model differs from the exact contract")
    required_options = {
        "--tokenizer": shared["path"],
        "--dtype": "bfloat16",
        "--max-loras": "1",
        "--max-cpu-loras": "1",
        "--max-lora-rank": "64",
        "--lora-dtype": "bfloat16",
        "--data-parallel-size": "4",
        "--api-server-count": "4",
    }
    for option, expected in required_options.items():
        if _one_option(argv, option) != expected:
            raise IntegrityError(f"live PID 1 {option} differs from the exact contract")
    if argv.count("--enable-lora") != 1:
        raise IntegrityError("live PID 1 does not enable exactly one LoRA path")
    binding = f"{served_name}={composition['adapter_path']}"
    if _variadic_groups(argv, "--lora-modules") != [[binding]]:
        raise IntegrityError(
            "live PID 1 does not bind exactly the frozen public adapter"
        )
    parent_name = _one_option(argv, "--served-model-name")
    if not parent_name or parent_name == served_name:
        raise IntegrityError(
            "live exact-LoRA public and private names are not distinct"
        )
    port_text = _one_option(argv, "--port")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise IntegrityError("live vLLM port is not an integer") from exc
    if not 1 <= port <= 65535:
        raise IntegrityError("live vLLM port is outside the TCP range")

    config = {
        "model_path": composition["parent_path"],
        "served_model_name": served_name,
        "port": port,
        "dtype": "bfloat16",
        "serving_mode": "exact_peft_lora",
        "frontend_launcher": "vllm serve",
        "data_parallel_size": 4,
        "api_server_count": 4,
        "parent_served_model_name": parent_name,
        "adapter_path": composition["adapter_path"],
        "adapter_rank": 64,
        "parent_tree_sha256": composition["parent_tree_sha256"],
        "adapter_tree_sha256": composition["adapter_tree_sha256"],
        "adapter_config_sha256": composition["adapter_config_sha256"],
        "tokenizer_json_sha256": shared["tokenizer_json_sha256"],
        "chat_template_sha256": shared["chat_template_sha256"],
        "composite_weight_sha256": composition["composite_weight_sha256"],
        "exact_lora_manifest_sha256": contract["manifest_sha256"],
        "adapter_kind": composition["adapter_kind"],
        "environment": {
            "FLA_TILELANG": "0",
            "VLLM_ALLOW_RUNTIME_LORA_UPDATING": None,
        },
    }
    return {
        "model_path": composition["parent_path"],
        "served_model_name": served_name,
        "port": port,
        "argv": argv,
        "config": config,
    }, contract


def _remote_command(
    *,
    kubectl: str,
    kubeconfig: Path | None,
    context: str,
    namespace: str,
    pod: str,
    manifest_path: str,
    receipt_path: str,
    source_root: str,
    arm: str,
) -> list[str]:
    command = [kubectl]
    if kubeconfig is not None:
        command.extend(("--kubeconfig", str(kubeconfig.resolve())))
    command.extend(
        (
            "--context",
            context,
            "--namespace",
            namespace,
            "exec",
            "-i",
            pod,
            "--",
            "python3",
            "-",
            manifest_path,
            receipt_path,
            source_root,
            arm,
        )
    )
    return command


def capture_exact_endpoint(
    *,
    arm: str,
    pod: str,
    namespace: str,
    context: str,
    manifest_path: str,
    receipt_path: str,
    source_root: str,
    output_path: Path,
    contract_output_path: Path | None = None,
    kubeconfig: Path | None = None,
    kubectl: str = "kubectl",
    timeout_seconds: float = 1800.0,
) -> dict[str, Any]:
    """Capture one arm from the live pod and publish create-only records."""

    if arm not in {"base", "trained"}:
        raise IntegrityError("exact-LoRA arm must be base or trained")
    for label, value in (
        ("pod", pod),
        ("namespace", namespace),
        ("context", context),
        ("kubectl", kubectl),
    ):
        if not isinstance(value, str) or not value.strip():
            raise IntegrityError(f"{label} must be a nonempty string")
    for label, value in (
        ("manifest path", manifest_path),
        ("receipt path", receipt_path),
        ("source root", source_root),
    ):
        if not PurePosixPath(value).is_absolute():
            raise IntegrityError(f"remote {label} must be absolute")
    if timeout_seconds <= 0:
        raise IntegrityError("capture timeout must be positive")
    output = output_path.resolve()
    contract_output = contract_output_path.resolve() if contract_output_path else None
    if contract_output == output:
        raise IntegrityError("server and exact-LoRA contract outputs must differ")
    for path in (output, contract_output):
        if path is not None and (path.exists() or path.is_symlink()):
            raise IntegrityError(f"refusing to overwrite frozen artifact: {path}")

    command = _remote_command(
        kubectl=kubectl,
        kubeconfig=kubeconfig,
        context=context,
        namespace=namespace,
        pod=pod,
        manifest_path=manifest_path,
        receipt_path=receipt_path,
        source_root=source_root,
        arm=arm,
    )
    try:
        completed = subprocess.run(
            command,
            input=_REMOTE_VERIFIER,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IntegrityError(f"cannot capture live exact-LoRA endpoint: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip()[-4000:] or completed.stdout.strip()[-4000:]
        raise IntegrityError(
            f"remote exact-LoRA verifier failed with exit {completed.returncode}: {detail}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise IntegrityError(
            "remote exact-LoRA verifier returned invalid JSON"
        ) from exc
    server, contract = _server_record(payload, arm=arm)

    wrote_server = False
    try:
        write_json_create_only(output, server)
        wrote_server = True
        if contract_output is not None:
            write_json_create_only(contract_output, contract)
    except Exception:
        if wrote_server:
            output.unlink(missing_ok=True)
        raise
    return {
        "server_record": server,
        "exact_lora_contract": contract,
        "server_record_sha256": sha256_bytes(output.read_bytes()),
        "contract_output": str(contract_output) if contract_output else None,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", required=True, choices=("base", "trained"))
    parser.add_argument("--pod", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--manifest-receipt", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--contract-output", type=Path)
    parser.add_argument("--kubeconfig", type=Path)
    parser.add_argument("--kubectl", default="kubectl")
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = capture_exact_endpoint(
            arm=arguments.arm,
            pod=arguments.pod,
            namespace=arguments.namespace,
            context=arguments.context,
            manifest_path=arguments.manifest,
            receipt_path=arguments.manifest_receipt,
            source_root=arguments.source_root,
            output_path=arguments.output,
            contract_output_path=arguments.contract_output,
            kubeconfig=arguments.kubeconfig,
            kubectl=arguments.kubectl,
            timeout_seconds=arguments.timeout_seconds,
        )
    except IntegrityError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "status": "ok",
                "server_record_sha256": result["server_record_sha256"],
                "contract_output": result["contract_output"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
