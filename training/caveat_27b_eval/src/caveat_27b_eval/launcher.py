from __future__ import annotations

import copy
import ipaddress
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .common import (
    IntegrityError,
    canonical_bytes,
    sha256_bytes,
    write_json_create_only,
)
from .matrix import audit_matrix

TUNNEL_RECORD_FIELDS = {
    "transport",
    "listen_host",
    "listen_port",
    "target_pod",
    "target_port",
    "argv",
    "config",
}
ENDPOINT_RECORD_FIELDS = {
    "name",
    "base_url",
    "deployment",
    "weight_sha256",
    "container_image_digest",
    "model_spec_sha256",
    "server",
    "tunnel",
    "ready_probe",
}


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")


def parse_loopback_v1_url(value: str, *, label: str) -> tuple[str, str, int]:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise IntegrityError(f"{label} must be an HTTP(S) URL")
    try:
        local = parsed.hostname == "localhost" or ipaddress.ip_address(
            parsed.hostname
        ).is_loopback
    except ValueError:
        local = False
    if not local:
        raise IntegrityError(f"{label} must use a loopback host")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise IntegrityError(f"{label} may not contain credentials, query, or fragment")
    if parsed.path.rstrip("/") != "/v1":
        raise IntegrityError(f"{label} must end at /v1")
    try:
        port = parsed.port
    except ValueError as exc:
        raise IntegrityError(f"{label} has an invalid port") from exc
    if port is None:
        raise IntegrityError(f"{label} must contain the live local port")
    return value.rstrip("/"), parsed.hostname, port


def _verify_frozen_manifest_identity(manifest: dict[str, Any], campaign_id: str) -> None:
    core = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if manifest.get("manifest_sha256") != sha256_bytes(canonical_bytes(core)):
        raise IntegrityError("frozen manifest has an invalid manifest_sha256")
    if manifest.get("campaign", {}).get("campaign_id") != campaign_id:
        raise IntegrityError("frozen manifest and campaign identities differ")


def _verify_frozen_inputs(
    *,
    manifest: dict[str, Any],
    config: dict[str, Any],
    matrix: dict[str, Any],
) -> None:
    _verify_frozen_manifest_identity(manifest, config["campaign_id"])
    if manifest.get("campaign") != config:
        raise IntegrityError("runtime campaign config differs from the frozen manifest")
    expected_contract = sha256_bytes(canonical_bytes(config))
    if matrix.get("campaign_contract_sha256") != expected_contract:
        raise IntegrityError("matrix campaign contract differs from the frozen campaign")


def _normalized_server_contract(
    *,
    server: dict[str, Any],
    model_path: str,
    served_name: str,
    port: int,
    expected_weight_sha256: str,
) -> dict[str, Any]:
    """Normalize only attested per-arm weight/routing identities.

    The original final-evaluation contract served complete checkpoints, so the
    only arm-specific argv values were ``--model`` and
    ``--served-model-name``.  Exact PEFT serving has two additional identities:
    the private parent alias and the adapter path bound to the public routed
    model ID.  This branch validates that composition fail-closed before
    normalizing it; all execution flags remain byte-for-byte comparable.
    """

    argv = server["argv"]
    config = server["config"]
    if config.get("serving_mode") == "exact_peft_lora":
        return _normalized_exact_lora_server_contract(
            server=server,
            model_path=model_path,
            served_name=served_name,
            port=port,
            expected_weight_sha256=expected_weight_sha256,
        )

    normalized_argv = list(argv)
    model_path_occurrences = 0
    served_name_occurrences = 0
    port_occurrences = 0
    for index, token in enumerate(argv):
        if token == model_path:
            normalized_argv[index] = "<MODEL_PATH>"
            model_path_occurrences += 1
        elif token == f"--model={model_path}":
            normalized_argv[index] = "--model=<MODEL_PATH>"
            model_path_occurrences += 1
        elif token == f"--served-model-name={served_name}":
            normalized_argv[index] = "--served-model-name=<SERVED_MODEL_NAME>"
            served_name_occurrences += 1
        elif token == f"--port={port}":
            port_occurrences += 1
        elif index > 0 and argv[index - 1] == "--served-model-name" and token == served_name:
            normalized_argv[index] = "<SERVED_MODEL_NAME>"
            served_name_occurrences += 1
        elif index > 0 and argv[index - 1] == "--port" and token == str(port):
            port_occurrences += 1
    if not all((model_path_occurrences, served_name_occurrences, port_occurrences)):
        raise IntegrityError("server argv does not bind model/name/port")

    normalized_config = copy.deepcopy(server["config"])
    normalized_config["model_path"] = "<MODEL_PATH>"
    normalized_config["served_model_name"] = "<SERVED_MODEL_NAME>"
    return {"argv": normalized_argv, "config": normalized_config}


_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_EXACT_LORA_IDENTITY_FIELDS = {
    "parent_served_model_name",
    "adapter_path",
    "adapter_rank",
    "parent_tree_sha256",
    "adapter_tree_sha256",
    "adapter_config_sha256",
    "tokenizer_json_sha256",
    "chat_template_sha256",
    "dtype",
    "composite_weight_sha256",
    "exact_lora_manifest_sha256",
    "adapter_kind",
}

_EXACT_LORA_WEIGHT_SCHEMA = "caveat-27b.exact-lora-composite.v1"
_EXACT_LORA_CONTRACT_SCHEMA = "caveat-27b-eval.exact-lora-contract.v1"
_EXACT_LORA_MANIFEST_SCHEMA = "caveat-27b.exact-lora-inference.v1"


def exact_lora_composite_sha256(
    *,
    parent_tree_sha256: str,
    adapter_tree_sha256: str,
    adapter_config_sha256: str,
    tokenizer_json_sha256: str,
    chat_template_sha256: str,
    dtype: str,
) -> str:
    """Canonical identity for a parent checkpoint plus an exact PEFT adapter."""

    digests = {
        "adapter_config_sha256": adapter_config_sha256,
        "adapter_tree_sha256": adapter_tree_sha256,
        "chat_template_sha256": chat_template_sha256,
        "parent_tree_sha256": parent_tree_sha256,
        "tokenizer_json_sha256": tokenizer_json_sha256,
    }
    if any(_SHA256_RE.fullmatch(value) is None for value in digests.values()):
        raise IntegrityError("exact-LoRA component identity is not a lowercase SHA-256")
    if dtype != "bfloat16":
        raise IntegrityError("exact-LoRA inference dtype must be bfloat16")
    payload = {
        "schema": "caveat-27b.exact-lora-composite.v1",
        "execution": "peft_unmerged_exact_lora",
        "parent_tree_sha256": parent_tree_sha256,
        "adapter_tree_sha256": adapter_tree_sha256,
        "adapter_config_sha256": adapter_config_sha256,
        "tokenizer_json_sha256": tokenizer_json_sha256,
        "chat_template_sha256": chat_template_sha256,
        "dtype": dtype,
    }
    return sha256_bytes(canonical_bytes(payload))


def _option_values(argv: list[str], option: str) -> list[str]:
    """Return values from either ``--flag value`` or ``--flag=value`` forms."""

    values: list[str] = []
    for index, token in enumerate(argv):
        if token == option:
            if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                raise IntegrityError(f"server argv has an unbound {option}")
            values.append(argv[index + 1])
        elif token.startswith(option + "="):
            values.append(token.split("=", 1)[1])
    return values


def vllm_serve_model_index(argv: list[str]) -> int:
    """Return the positional model index for the supported DP-aware CLI.

    A console script can appear in ``/proc/1/cmdline`` either directly or
    behind its shebang interpreter.  Both forms are accepted, but the legacy
    single-frontend ``python -m ...openai.api_server`` form and ``--model``
    binding are deliberately rejected.
    """

    if len(argv) >= 3 and Path(argv[0]).name == "vllm" and argv[1] == "serve":
        model_index = 2
    elif (
        len(argv) >= 4
        and re.fullmatch(r"python(?:3(?:\.\d+)?)?", Path(argv[0]).name) is not None
        and Path(argv[1]).name == "vllm"
        and argv[2] == "serve"
    ):
        model_index = 3
    else:
        raise IntegrityError("server argv is not the supported vllm serve launcher")
    if argv[model_index].startswith("-"):
        raise IntegrityError("vllm serve has no positional model path")
    if _option_values(argv, "--model"):
        raise IntegrityError("vllm serve must bind its model positionally")
    return model_index


def _replace_option_value(
    argv: list[str], option: str, expected: str, replacement: str
) -> list[str]:
    values = _option_values(argv, option)
    if values != [expected]:
        raise IntegrityError(f"server argv must bind exactly one {option}={expected}")
    result = list(argv)
    for index, token in enumerate(argv):
        if token == option:
            result[index + 1] = replacement
        elif token == f"{option}={expected}":
            result[index] = f"{option}={replacement}"
    return result


def _variadic_option_groups(argv: list[str], option: str) -> list[list[str]]:
    """Parse every argparse-style ``nargs='+'`` occurrence without hiding tails."""

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
                raise IntegrityError(f"server argv has an unbound {option}")
            groups.append(values)
            continue
        if token.startswith(option + "="):
            value = token.split("=", 1)[1]
            if not value:
                raise IntegrityError(f"server argv has an unbound {option}")
            groups.append([value])
        index += 1
    return groups


def _normalized_exact_lora_server_contract(
    *,
    server: dict[str, Any],
    model_path: str,
    served_name: str,
    port: int,
    expected_weight_sha256: str,
) -> dict[str, Any]:
    argv = server["argv"]
    config = server["config"]
    missing = sorted(_EXACT_LORA_IDENTITY_FIELDS - set(config))
    if missing:
        raise IntegrityError(f"exact-LoRA server config lacks identity fields: {missing}")
    parent_name = config.get("parent_served_model_name")
    adapter_path = config.get("adapter_path")
    if not isinstance(parent_name, str) or not parent_name:
        raise IntegrityError("exact-LoRA parent served-model name is invalid")
    if not isinstance(adapter_path, str) or not adapter_path:
        raise IntegrityError("exact-LoRA adapter path is invalid")
    if parent_name == served_name:
        raise IntegrityError("exact-LoRA public and private model names must differ")
    if not Path(model_path).is_absolute() or not Path(adapter_path).is_absolute():
        raise IntegrityError("exact-LoRA parent and adapter paths must be absolute")
    if Path(model_path) == Path(adapter_path):
        raise IntegrityError("exact-LoRA parent and adapter paths must differ")
    if config.get("adapter_rank") != 64:
        raise IntegrityError("exact-LoRA adapter rank must be 64")
    component_digest = exact_lora_composite_sha256(
        parent_tree_sha256=config.get("parent_tree_sha256", ""),
        adapter_tree_sha256=config.get("adapter_tree_sha256", ""),
        adapter_config_sha256=config.get("adapter_config_sha256", ""),
        tokenizer_json_sha256=config.get("tokenizer_json_sha256", ""),
        chat_template_sha256=config.get("chat_template_sha256", ""),
        dtype=config.get("dtype", ""),
    )
    if (
        config.get("composite_weight_sha256") != component_digest
        or component_digest != expected_weight_sha256
    ):
        raise IntegrityError("exact-LoRA composite weight identity is not frozen")

    model_index = vllm_serve_model_index(argv)
    if argv[model_index] != model_path:
        raise IntegrityError("vllm serve positional model differs from model_path")
    if config.get("frontend_launcher") != "vllm serve":
        raise IntegrityError("exact-LoRA server does not attest the DP-aware launcher")
    if config.get("data_parallel_size") != 4 or config.get("api_server_count") != 4:
        raise IntegrityError("exact-LoRA server does not use four DP/API replicas")

    required_bare_flags = ("--enable-lora",)
    for flag in required_bare_flags:
        if argv.count(flag) != 1:
            raise IntegrityError(f"exact-LoRA server argv must contain exactly one {flag}")
    exact_options = {
        "--max-loras": "1",
        "--max-cpu-loras": "1",
        "--max-lora-rank": "64",
        "--lora-dtype": "bfloat16",
        "--data-parallel-size": "4",
        "--api-server-count": "4",
    }
    for option, expected in exact_options.items():
        if _option_values(argv, option) != [expected]:
            raise IntegrityError(
                f"exact-LoRA server argv must bind exactly one {option}={expected}"
            )
    lora_binding = f"{served_name}={adapter_path}"
    if _variadic_option_groups(argv, "--lora-modules") != [[lora_binding]]:
        raise IntegrityError(
            "exact-LoRA server argv must bind exactly one public adapter"
        )
    environment = config.get("environment")
    if not isinstance(environment, dict) or environment.get(
        "VLLM_ALLOW_RUNTIME_LORA_UPDATING", object()
    ) is not None:
        raise IntegrityError("exact-LoRA runtime adapter mutation must be explicitly disabled")

    normalized_argv = list(argv)
    normalized_argv[model_index] = "<MODEL_PATH>"
    normalized_argv = _replace_option_value(
        normalized_argv,
        "--served-model-name",
        parent_name,
        parent_name,
    )
    normalized_argv = _replace_option_value(
        normalized_argv, "--port", str(port), str(port)
    )
    normalized_argv = _replace_option_value(
        normalized_argv,
        "--lora-modules",
        lora_binding,
        "<SERVED_MODEL_NAME>=<ADAPTER_PATH>",
    )

    normalized_config = copy.deepcopy(config)
    replacements = {
        "model_path": "<MODEL_PATH>",
        "served_model_name": "<SERVED_MODEL_NAME>",
        "adapter_path": "<ADAPTER_PATH>",
        "parent_tree_sha256": "<PARENT_TREE_SHA256>",
        "adapter_tree_sha256": "<ADAPTER_TREE_SHA256>",
        "adapter_config_sha256": "<ADAPTER_CONFIG_SHA256>",
        "composite_weight_sha256": "<COMPOSITE_WEIGHT_SHA256>",
        "adapter_kind": "<ADAPTER_KIND>",
    }
    normalized_config.update(replacements)
    return {"argv": normalized_argv, "config": normalized_config}


def _verify_frozen_exact_lora_contract(
    frozen_manifest: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate the finalizer receipt projected into the frozen serving stack."""

    model_contract = frozen_manifest.get("model_contract", {})
    schema = model_contract.get("weight_identity_schema")
    stack = frozen_manifest.get("inference_contract", {}).get("serving_stack", {})
    value = stack.get("exact_lora_contract") if isinstance(stack, dict) else None
    if schema != _EXACT_LORA_WEIGHT_SCHEMA:
        if value is not None:
            raise IntegrityError("complete-checkpoint freeze contains an exact-LoRA contract")
        return None
    if not isinstance(value, dict) or value.get("schema") != _EXACT_LORA_CONTRACT_SCHEMA:
        raise IntegrityError("exact-LoRA weight semantics lack a frozen finalizer contract")
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
    if set(value) != required or value.get("serving_mode") != "exact_peft_lora":
        raise IntegrityError("frozen exact-LoRA finalizer contract is not exact")
    for key in (
        "manifest_sha256",
        "manifest_receipt_sha256",
        "composite_pair_sha256",
        "refinement_receipt_sha256",
    ):
        if _SHA256_RE.fullmatch(str(value.get(key, ""))) is None:
            raise IntegrityError(f"frozen exact-LoRA contract has no valid {key}")
    if value.get("refinement_update") != 20:
        raise IntegrityError("frozen exact-LoRA contract is not refinement step 20")
    shared = value.get("shared_tokenizer")
    if not isinstance(shared, dict) or set(shared) != {
        "path",
        "tokenizer_json_sha256",
        "chat_template_sha256",
    }:
        raise IntegrityError("frozen exact-LoRA shared tokenizer contract is invalid")
    if not isinstance(shared["path"], str) or not Path(shared["path"]).is_absolute():
        raise IntegrityError("frozen exact-LoRA tokenizer path is not absolute")
    if any(
        _SHA256_RE.fullmatch(str(shared[key])) is None
        for key in ("tokenizer_json_sha256", "chat_template_sha256")
    ):
        raise IntegrityError("frozen exact-LoRA tokenizer identity is invalid")
    zero = value.get("zero_control")
    if zero != {
        "all_zero": True,
        "nonzero_elements": 0,
        "exact_logits_equal": True,
        "maximum_absolute_logit_difference": 0.0,
    }:
        raise IntegrityError("frozen baseline adapter is not an attested exact zero control")
    nonnoop = value.get("trained_nonnoop")
    if (
        not isinstance(nonnoop, dict)
        or set(nonnoop) != {
            "all_zero",
            "maximum_absolute_logit_difference",
            "relative_l2_difference",
        }
        or nonnoop.get("all_zero") is not False
        or not isinstance(nonnoop.get("maximum_absolute_logit_difference"), (int, float))
        or nonnoop["maximum_absolute_logit_difference"] <= 0
        or not isinstance(nonnoop.get("relative_l2_difference"), (int, float))
        or nonnoop["relative_l2_difference"] <= 0
    ):
        raise IntegrityError("frozen trained adapter is not an attested non-noop")
    arm_records = value.get("arms")
    if not isinstance(arm_records, dict) or set(arm_records) != {"base", "trained"}:
        raise IntegrityError("frozen exact-LoRA contract lacks two exact arms")
    composites: dict[str, str] = {}
    for arm, kind in (("base", "zero_control"), ("trained", "refinement_step20")):
        record = arm_records[arm]
        expected_fields = {
            "adapter_kind",
            "parent_path",
            "parent_tree_sha256",
            "adapter_path",
            "adapter_tree_sha256",
            "adapter_config_sha256",
            "composite_weight_sha256",
        }
        if not isinstance(record, dict) or set(record) != expected_fields:
            raise IntegrityError(f"frozen exact-LoRA {arm} composition is not exact")
        if record.get("adapter_kind") != kind:
            raise IntegrityError(f"frozen exact-LoRA {arm} adapter role is invalid")
        if any(
            not isinstance(record.get(key), str)
            or not Path(record[key]).is_absolute()
            for key in ("parent_path", "adapter_path")
        ):
            raise IntegrityError(f"frozen exact-LoRA {arm} component path is invalid")
        computed = exact_lora_composite_sha256(
            parent_tree_sha256=record.get("parent_tree_sha256", ""),
            adapter_tree_sha256=record.get("adapter_tree_sha256", ""),
            adapter_config_sha256=record.get("adapter_config_sha256", ""),
            tokenizer_json_sha256=shared["tokenizer_json_sha256"],
            chat_template_sha256=shared["chat_template_sha256"],
            dtype="bfloat16",
        )
        expected_weight = model_contract.get(
            "base_weight_sha256" if arm == "base" else "trained_weight_sha256"
        )
        if record.get("composite_weight_sha256") != computed or computed != expected_weight:
            raise IntegrityError(f"frozen exact-LoRA {arm} composition hash is invalid")
        composites[arm] = computed
    pair = sha256_bytes(
        canonical_bytes(
            {
                "schema": _EXACT_LORA_MANIFEST_SCHEMA,
                "base": composites["base"],
                "trained": composites["trained"],
            }
        )
    )
    if pair != value["composite_pair_sha256"]:
        raise IntegrityError("frozen exact-LoRA pair identity is invalid")
    return value


def _normalized_tunnel_contract(
    *,
    tunnel: Any,
    endpoint_host: str,
    endpoint_port: int,
    server_port: int,
) -> dict[str, Any]:
    if not isinstance(tunnel, dict) or set(tunnel) != TUNNEL_RECORD_FIELDS:
        raise IntegrityError("tunnel attestation is not exact")
    if tunnel.get("transport") != "kubectl-port-forward":
        raise IntegrityError("tunnel transport is not kubectl-port-forward")
    listen_host = tunnel.get("listen_host")
    listen_port = tunnel.get("listen_port")
    target_pod = tunnel.get("target_pod")
    target_port = tunnel.get("target_port")
    if listen_host != endpoint_host or listen_port != endpoint_port:
        raise IntegrityError("tunnel listener differs from the model base URL")
    if type(listen_port) is not int or not 1 <= listen_port <= 65535:
        raise IntegrityError("tunnel listen port is invalid")
    if type(target_port) is not int or target_port != server_port:
        raise IntegrityError("tunnel target port differs from the vLLM server port")
    if not isinstance(target_pod, str) or not target_pod:
        raise IntegrityError("tunnel target pod is invalid")
    argv = tunnel.get("argv")
    config = tunnel.get("config")
    if not isinstance(argv, list) or not argv or not all(
        isinstance(value, str) and value for value in argv
    ):
        raise IntegrityError("tunnel argv is invalid")
    if Path(argv[0]).name != "kubectl" or argv.count("port-forward") != 1:
        raise IntegrityError("tunnel argv is not an exact kubectl port-forward command")
    if not isinstance(config, dict) or any(
        config.get(field) != tunnel[field]
        for field in (
            "listen_host",
            "listen_port",
            "target_pod",
            "target_port",
        )
    ):
        raise IntegrityError("tunnel config does not bind listener/pod/target")

    target_token = f"pod/{target_pod}"
    port_token = f"{listen_port}:{target_port}"
    normalized_argv = list(argv)
    target_occurrences = 0
    port_occurrences = 0
    address_occurrences = 0
    for index, token in enumerate(argv):
        if token == target_token:
            normalized_argv[index] = "pod/<TARGET_POD>"
            target_occurrences += 1
        elif token == port_token:
            normalized_argv[index] = f"<LISTEN_PORT>:{server_port}"
            port_occurrences += 1
        elif token == f"--address={listen_host}" or (
            index > 0 and argv[index - 1] == "--address" and token == listen_host
        ):
            address_occurrences += 1
    if (target_occurrences, port_occurrences, address_occurrences) != (1, 1, 1):
        raise IntegrityError("tunnel argv does not uniquely bind listener/pod/target")

    normalized_config = copy.deepcopy(config)
    normalized_config["listen_port"] = "<LISTEN_PORT>"
    normalized_config["target_pod"] = "<TARGET_POD>"
    return {
        "transport": tunnel["transport"],
        "listen_host": listen_host,
        "listen_port": "<LISTEN_PORT>",
        "target_pod": "<TARGET_POD>",
        "target_port": server_port,
        "argv": normalized_argv,
        "config": normalized_config,
    }


def verify_endpoint_manifest(
    *,
    endpoint_manifest: dict[str, Any],
    frozen_manifest: dict[str, Any],
    model_specs: dict[str, Any],
    arms: set[str],
) -> str:
    frozen_exact_lora = _verify_frozen_exact_lora_contract(frozen_manifest)
    core = {
        key: value
        for key, value in endpoint_manifest.items()
        if key != "endpoint_manifest_sha256"
    }
    digest = sha256_bytes(canonical_bytes(core))
    if endpoint_manifest.get("endpoint_manifest_sha256") != digest:
        raise IntegrityError("endpoint manifest has an invalid endpoint_manifest_sha256")
    schema_version = endpoint_manifest.get("schema_version")
    if schema_version not in {2, 3}:
        raise IntegrityError("endpoint manifest has an unsupported schema")
    if endpoint_manifest.get("frozen_manifest_sha256") != frozen_manifest.get(
        "manifest_sha256"
    ):
        raise IntegrityError("endpoint manifest differs from the frozen inference manifest")
    expected_inference = sha256_bytes(
        canonical_bytes(frozen_manifest["inference_contract"])
    )
    if endpoint_manifest.get("inference_contract_sha256") != expected_inference:
        raise IntegrityError("endpoint manifest differs from the frozen inference contract")
    records = endpoint_manifest.get("arms")
    if not isinstance(records, dict) or set(records) != arms:
        raise IntegrityError("endpoint manifest arms differ from the final matrix")
    declared_exact_lora_arms = {
        arm
        for arm in arms
        if isinstance(records.get(arm), dict)
        and isinstance(records[arm].get("server"), dict)
        and isinstance(records[arm]["server"].get("config"), dict)
        and records[arm]["server"]["config"].get("serving_mode")
        == "exact_peft_lora"
    }
    if declared_exact_lora_arms and declared_exact_lora_arms != arms:
        raise IntegrityError("base and trained endpoints must both use exact LoRA")

    identities: set[tuple[str, str]] = set()
    model_contracts: dict[str, dict[str, Any]] = {}
    server_contracts: dict[str, Any] = {}
    tunnel_contracts: dict[str, Any] = {}
    model_paths: set[str] = set()
    endpoint_listeners: set[tuple[str, int]] = set()
    target_pods: set[str] = set()
    exact_lora_arms: set[str] = set()
    adapter_paths: set[str] = set()
    weight_identities: set[str] = set()
    contract = frozen_manifest["model_contract"]
    image = frozen_manifest["inference_contract"].get("container_image_digest")
    for arm in sorted(arms):
        spec = model_specs.get(arm)
        record = records.get(arm)
        if not isinstance(spec, dict) or not isinstance(record, dict):
            raise IntegrityError(f"{arm} requires object model/endpoint records")
        if set(record) != ENDPOINT_RECORD_FIELDS:
            raise IntegrityError(f"{arm} endpoint attestation is not exact")
        if spec.get("provider") != "openai":
            raise IntegrityError(f"{arm} final endpoint must use provider=openai")
        for field in ("name", "base_url", "deployment"):
            value = spec.get(field)
            if not isinstance(value, str) or not value:
                raise IntegrityError(f"{arm} model spec has no {field}")
            if record.get(field) != value:
                raise IntegrityError(f"{arm} endpoint {field} differs from its model spec")
        _, endpoint_host, endpoint_port = parse_loopback_v1_url(
            spec["base_url"], label=f"{arm} model base URL"
        )
        endpoint_listeners.add((endpoint_host, endpoint_port))
        expected_weight = contract.get(
            "base_weight_sha256" if arm == "base" else "trained_weight_sha256"
        )
        if record.get("weight_sha256") != expected_weight:
            raise IntegrityError(f"{arm} endpoint weight identity is not frozen")
        weight_identities.add(expected_weight)
        if record.get("container_image_digest") != image:
            raise IntegrityError(f"{arm} endpoint container image is not frozen")
        if record.get("model_spec_sha256") != sha256_bytes(canonical_bytes(spec)):
            raise IntegrityError(f"{arm} endpoint model-spec hash is invalid")
        model_contracts[arm] = {
            key: value
            for key, value in spec.items()
            if key not in {"name", "base_url", "deployment"}
        }

        server = record.get("server")
        if not isinstance(server, dict):
            raise IntegrityError(f"{arm} endpoint lacks a server launch attestation")
        if set(server) != {
            "model_path",
            "served_model_name",
            "port",
            "argv",
            "config",
        }:
            raise IntegrityError(f"{arm} server launch attestation is not exact")
        model_path = server.get("model_path")
        served_name = server.get("served_model_name")
        port = server.get("port")
        argv = server.get("argv")
        server_config = server.get("config")
        if not isinstance(model_path, str) or not model_path:
            raise IntegrityError(f"{arm} server model_path is invalid")
        if served_name != spec["deployment"]:
            raise IntegrityError(f"{arm} server served-model name differs from deployment")
        if type(port) is not int or not 1 <= port <= 65535:
            raise IntegrityError(f"{arm} server port is invalid")
        if not isinstance(argv, list) or not argv or not all(
            isinstance(value, str) and value for value in argv
        ):
            raise IntegrityError(f"{arm} server argv is invalid")
        if not isinstance(server_config, dict) or (
            server_config.get("model_path") != model_path
            or server_config.get("served_model_name") != served_name
            or server_config.get("port") != port
        ):
            raise IntegrityError(f"{arm} server config does not bind model/name/port")
        if server_config.get("serving_mode") == "exact_peft_lora":
            exact_lora_arms.add(arm)
            adapter_path = server_config.get("adapter_path")
            if isinstance(adapter_path, str):
                adapter_paths.add(adapter_path)
            if frozen_exact_lora is None:
                raise IntegrityError(f"{arm} exact-LoRA serving is not frozen")
            composition = frozen_exact_lora["arms"][arm]
            expected_server_identity = {
                "adapter_kind": composition["adapter_kind"],
                "model_path": composition["parent_path"],
                "parent_tree_sha256": composition["parent_tree_sha256"],
                "adapter_path": composition["adapter_path"],
                "adapter_tree_sha256": composition["adapter_tree_sha256"],
                "adapter_config_sha256": composition["adapter_config_sha256"],
                "composite_weight_sha256": composition[
                    "composite_weight_sha256"
                ],
                "tokenizer_json_sha256": frozen_exact_lora["shared_tokenizer"][
                    "tokenizer_json_sha256"
                ],
                "chat_template_sha256": frozen_exact_lora["shared_tokenizer"][
                    "chat_template_sha256"
                ],
                "exact_lora_manifest_sha256": frozen_exact_lora[
                    "manifest_sha256"
                ],
            }
            if any(
                server_config.get(key) != value
                for key, value in expected_server_identity.items()
            ):
                raise IntegrityError(
                    f"{arm} live server composition differs from the frozen finalizer contract"
                )
        try:
            server_contracts[arm] = _normalized_server_contract(
                server=server,
                model_path=model_path,
                served_name=served_name,
                port=port,
                expected_weight_sha256=expected_weight,
            )
        except IntegrityError as exc:
            raise IntegrityError(f"{arm} {exc}") from exc
        try:
            tunnel_contracts[arm] = _normalized_tunnel_contract(
                tunnel=record.get("tunnel"),
                endpoint_host=endpoint_host,
                endpoint_port=endpoint_port,
                server_port=port,
            )
        except IntegrityError as exc:
            raise IntegrityError(f"{arm} {exc}") from exc
        target_pods.add(record["tunnel"]["target_pod"])
        model_paths.add(model_path)
        probe = record.get("ready_probe")
        if (
            not isinstance(probe, dict)
            or probe.get("http_status") != 200
            or spec["deployment"] not in probe.get("served_models", [])
            or not isinstance(probe.get("checked_at"), str)
            or not probe["checked_at"]
            or not isinstance(probe.get("response_sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", probe["response_sha256"]) is None
        ):
            raise IntegrityError(f"{arm} endpoint lacks a successful model-identity probe")
        bindings = probe.get("model_bindings")
        if not isinstance(bindings, list) or not all(
            isinstance(item, dict)
            and set(item) == {"id", "root", "parent"}
            for item in bindings
        ):
            raise IntegrityError(f"{arm} endpoint lacks live model bindings")
        if server_config.get("serving_mode") == "exact_peft_lora":
            public = [item for item in bindings if item["id"] == served_name]
            private = [
                item
                for item in bindings
                if item["id"] == server_config["parent_served_model_name"]
            ]
            if public != [
                {
                    "id": served_name,
                    "root": server_config["adapter_path"],
                    "parent": server_config["parent_served_model_name"],
                }
            ]:
                raise IntegrityError(
                    f"{arm} live public model is not the frozen exact-LoRA adapter"
                )
            if private != [
                {
                    "id": server_config["parent_served_model_name"],
                    "root": model_path,
                    "parent": None,
                }
            ]:
                raise IntegrityError(
                    f"{arm} live private model is not the frozen parent"
                )
        identity = (spec["base_url"].rstrip("/"), spec["deployment"])
        if identity in identities:
            raise IntegrityError("base and trained endpoint identities are not distinct")
        identities.add(identity)
    if len(model_contracts) != 2 or len({canonical_bytes(v) for v in model_contracts.values()}) != 1:
        raise IntegrityError(
            "base and trained model specs differ outside display name/deployment/base_url"
        )
    if len(server_contracts) != 2 or len({canonical_bytes(v) for v in server_contracts.values()}) != 1:
        raise IntegrityError(
            "base and trained server argv/config differ beyond model path/name"
        )
    if len(tunnel_contracts) != 2 or len(
        {canonical_bytes(value) for value in tunnel_contracts.values()}
    ) != 1:
        raise IntegrityError(
            "base and trained tunnel argv/config differ beyond pod/listen port"
        )
    if len(model_paths) != 2:
        raise IntegrityError("base and trained endpoints do not use distinct model paths")
    if len(weight_identities) != 2:
        raise IntegrityError("base and trained endpoints do not use distinct weight identities")
    if exact_lora_arms:
        if exact_lora_arms != arms:
            raise IntegrityError("base and trained endpoints must both use exact LoRA")
        if schema_version != 3:
            raise IntegrityError("exact-LoRA endpoints require endpoint schema 3")
        if contract.get("weight_identity_schema") != (
            "caveat-27b.exact-lora-composite.v1"
        ):
            raise IntegrityError("exact-LoRA composite weight semantics are not frozen")
        if len(adapter_paths) != 2:
            raise IntegrityError("base and trained endpoints do not use distinct adapters")
    else:
        if frozen_exact_lora is not None:
            raise IntegrityError("frozen exact-LoRA weights require exact-LoRA endpoints")
        if schema_version != 2:
            raise IntegrityError("complete-checkpoint endpoints require endpoint schema 2")
        if contract.get(
            "weight_identity_schema", "complete_checkpoint_tree.v1"
        ) != "complete_checkpoint_tree.v1":
            raise IntegrityError("complete-checkpoint weight semantics are not frozen")
    if len(endpoint_listeners) != 2:
        raise IntegrityError("base and trained endpoints do not use distinct listeners")
    if len(target_pods) != 2:
        raise IntegrityError("base and trained tunnels do not target distinct pods")
    return digest


def _limit_contract(config: dict[str, Any]) -> dict[str, Any]:
    """Materialize the source-audited browser-use limit inventory for this campaign.

    The authoritative inventory lives with CAVEAT's campaign runtime helper.  This
    package only substitutes the values already frozen in ``campaign.json``; it does not
    invent a second list of browser-use limits that could silently drift from runtime.
    """

    try:
        from scripts.hard_campaign_runtime import runtime_limit_contract
    except ImportError as exc:  # pragma: no cover - exercised by installed-package use
        raise IntegrityError(
            "cannot import scripts.hard_campaign_runtime from the CAVEAT repository"
        ) from exc
    contract = copy.deepcopy(runtime_limit_contract())
    records = contract["categories"]["safety_backstops"]
    # Four compiler repair attempts are a fixed harness protocol: exhaustion is a
    # measured policy failure, not a resource ceiling.  Preserve the runtime record
    # verbatim but place it with fixed architecture so it cannot invalidate a run as
    # though compute, time, context, or browser capacity had bound.
    protocol_record = records.pop("structured_response_attempts")
    contract["categories"]["fixed_architecture"][
        "structured_response_attempts"
    ] = protocol_record
    limits = config["limits"]
    llm_timeout = int(limits["model_call_timeout_seconds"])
    browser_timeout = int(limits["browser_action_timeout_seconds"])
    replacements = {
        "max_steps": int(limits["max_steps"]),
        "whole_run_timeout_seconds": int(limits["run_timeout_seconds"]),
        "llm_timeout_seconds": llm_timeout,
        "llm_http_timeout_seconds": llm_timeout + 60,
        "step_timeout_seconds": llm_timeout + 300,
        "extract_llm_timeout_seconds": browser_timeout,
        "cdp_request_timeout_seconds": browser_timeout,
        "browser_action_timeout_seconds": browser_timeout,
        "max_consecutive_failures": int(limits["maximum_consecutive_failures"]),
    }
    for name, value in replacements.items():
        records[name]["configured"] = value
    completion_cap = limits.get("completion_token_cap")
    contract["categories"]["fixed_architecture"]["max_completion_tokens"][
        "configured"
    ] = "off" if completion_cap is None else int(completion_cap)
    core = {key: value for key, value in contract.items() if key != "sha256"}
    return {**core, "sha256": sha256_bytes(canonical_bytes(core))}


def render_run_bundle(
    *,
    matrix: dict[str, Any],
    config: dict[str, Any],
    frozen_manifest: dict[str, Any],
    endpoint_manifest: dict[str, Any] | None,
    model_specs: dict[str, Any],
    output_dir: Path,
    results_root: Path,
    base_port: int,
    python_executable: str = "python3",
) -> dict[str, Any]:
    """Render one directly runnable ``caveat.run_cell`` spec per matrix row."""

    audit_matrix(matrix)
    _verify_frozen_inputs(manifest=frozen_manifest, config=config, matrix=matrix)
    if matrix.get("campaign_id") != config["campaign_id"]:
        raise IntegrityError("matrix and campaign identities differ")
    if matrix["kind"] not in {"diagnostic", "final"}:
        raise IntegrityError("run bundles are supported only for canonical CAVEAT-Shop eval matrices")
    arms = {row["arm"] for row in matrix["runs"]}
    missing = sorted(arms - set(model_specs))
    if missing:
        raise IntegrityError(f"model-spec mapping lacks matrix arms: {missing}")
    endpoint_manifest_sha256 = None
    if matrix["kind"] == "final":
        if arms != {"base", "trained"}:
            raise IntegrityError("confirmatory final matrix must contain exactly base and trained")
        if endpoint_manifest is None:
            raise IntegrityError("final run bundle requires a frozen endpoint manifest")
        endpoint_manifest_sha256 = verify_endpoint_manifest(
            endpoint_manifest=endpoint_manifest,
            frozen_manifest=frozen_manifest,
            model_specs=model_specs,
            arms=arms,
        )
    if output_dir.exists():
        raise IntegrityError(f"refusing to overwrite run bundle directory: {output_dir}")
    if base_port < 1024 or base_port + len(matrix["runs"]) > 65535:
        raise IntegrityError("base port does not leave a valid unique port for every run")
    output_dir.mkdir(parents=True)
    configs_dir = output_dir / "configs"
    configs_dir.mkdir()
    launches = []
    from caveat.benchmark import registry

    harness_sha256 = frozen_manifest["source_contract"]["harness_sha256"]
    inference_sha256 = sha256_bytes(
        canonical_bytes(frozen_manifest["inference_contract"])
    )
    frozen_manifest_sha256 = frozen_manifest["manifest_sha256"]
    limit_contract = _limit_contract(config)
    common_environment = {
        "CAVEAT_CELL_TIMEOUT": str(config["limits"]["run_timeout_seconds"]),
        "CAVEAT_LLM_TIMEOUT": str(config["limits"]["model_call_timeout_seconds"]),
        "CAVEAT_MAX_FAILURES": str(
            config["limits"]["maximum_consecutive_failures"]
        ),
        "CAVEAT_MAX_COMPLETION_TOKENS": (
            "off"
            if config["limits"].get("completion_token_cap") is None
            else str(config["limits"]["completion_token_cap"])
        ),
        "CAVEAT_LLM_CACHE": "0",
        "CAVEAT_NO_VISION": "1",
        "CAVEAT_NO_SHOT_PERSIST": "1",
        "CAVEAT_RUNTIME_SOURCE_ATTESTATION": harness_sha256,
        "CAVEAT_EVALUATION_INPUT_ATTESTATION": matrix["matrix_sha256"],
        "CAVEAT_LIMIT_CONTRACT_JSON": json.dumps(
            limit_contract, sort_keys=True, separators=(",", ":")
        ),
        "PYTHON_DOTENV_DISABLED": "1",
        "PHYAGI_API_KEY": "",
        "phyagi_apikey": "",
        "phyagi_api_key": "",
        "ANONYMIZED_TELEMETRY": "false",
        "BROWSER_USE_CLOUD_SYNC": "false",
        "BROWSER_USE_CDP_TIMEOUT_S": str(
            config["limits"]["browser_action_timeout_seconds"]
        ),
        "BROWSER_USE_ACTION_TIMEOUT_S": str(
            config["limits"]["browser_action_timeout_seconds"]
        ),
        "BROWSER_USE_EXTRACT_TIMEOUT_S": str(
            config["limits"]["browser_action_timeout_seconds"]
        ),
    }
    for name, seconds in limit_contract["categories"]["safety_backstops"][
        "event_timeouts_seconds"
    ]["configured"].items():
        common_environment[f"TIMEOUT_{name}"] = str(seconds)

    task_cache = {}
    for index, row in enumerate(matrix["runs"]):
        path = configs_dir / f"{index:04d}_{_safe(row['run_id'])}.json"
        cache_key = (row["scenario"], row["variant"])
        if cache_key not in task_cache:
            tasks = registry.benchmark_tasks(row["scenario"], variants=[row["variant"]])
            if len(tasks) != 1 or tasks[0].task_id != row["task_id"]:
                raise IntegrityError(f"cannot resolve exact benchmark task: {row['task_id']}")
            task_cache[cache_key] = asdict(tasks[0])
        cell_name = "__".join(
            _safe(value)
            for value in (
                config["environment"],
                config["scaffold"],
                row["arm"],
                row["task_id"],
                row["condition"],
                row["run_id"],
            )
        )
        result_dir = results_root / row["results_partition"] / cell_name
        runtime_environment = {
            **common_environment,
            # Prevent inference-cache aliasing across repetitions while preserving
            # a stable identity when an interrupted bundle is resumed.
            "CAVEAT_CACHE_NONCE": f"{config['campaign_id']}/{row['run_id']}",
        }
        audit_contract = {
            "frozen_manifest_sha256": frozen_manifest_sha256,
            "harness_sha256": harness_sha256,
            "inference_contract_sha256": inference_sha256,
            "matrix_sha256": matrix["matrix_sha256"],
            "limit_contract_sha256": limit_contract["sha256"],
            "endpoint_manifest_sha256": endpoint_manifest_sha256,
        }
        cell_spec = {
            "env": config["environment"],
            "scaffold": config["scaffold"],
            "model": model_specs[row["arm"]],
            "task": task_cache[cache_key],
            "condition": row["condition"],
            "port": base_port + index,
            "out_dir": str(result_dir.resolve()),
            "max_steps": int(config["limits"]["max_steps"]),
            "headless": True,
            "run_id": row["run_id"],
            "pair_id": row["pair_id"],
            "block_seed": row["block_seed"],
            "arm": row["arm"],
            "campaign_id": config["campaign_id"],
            "matrix_sha256": matrix["matrix_sha256"],
            "run_timeout_seconds": int(config["limits"]["run_timeout_seconds"]),
            "runtime_environment": runtime_environment,
            "audit_contract": audit_contract,
        }
        write_json_create_only(path, cell_spec)
        config_sha256 = sha256_bytes(path.read_bytes())
        launches.append(
            {
                "run_id": row["run_id"],
                "pair_id": row["pair_id"],
                "arm": row["arm"],
                "port": base_port + index,
                "config": str(path.resolve()),
                "config_sha256": config_sha256,
                "results": str(result_dir.resolve()),
                "argv": [
                    python_executable,
                    "-m",
                    "caveat_27b_eval.launch_one",
                    "--spec",
                    str(path.resolve()),
                ],
                "environment": runtime_environment,
                "audit_contract": audit_contract,
            }
        )
    core = {
        "schema_version": 1,
        "campaign_id": config["campaign_id"],
        "matrix_sha256": matrix["matrix_sha256"],
        "endpoint_manifest_sha256": endpoint_manifest_sha256,
        "base_port": base_port,
        "results_root": str(results_root.resolve()),
        "launches": launches,
    }
    manifest = {**core, "launch_manifest_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(output_dir / "launch_manifest.json", manifest)
    return manifest
