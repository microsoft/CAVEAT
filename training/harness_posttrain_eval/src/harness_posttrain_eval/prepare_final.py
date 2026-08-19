from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .common import (
    IntegrityError,
    canonical_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json_create_only,
)
from .launcher import (
    TUNNEL_RECORD_FIELDS,
    parse_loopback_v1_url,
    render_run_bundle,
    verify_endpoint_manifest,
)
from .manifest import freeze_manifest, verify_manifest
from .matrix import audit_matrix, final_matrix
from .split import audit_split

_SERVER_FIELDS = {
    "model_path",
    "served_model_name",
    "port",
    "argv",
    "config",
}
BASE_DEPLOYMENT = "qwen35-27b-base-exact-lora"
TRAINED_DEPLOYMENT = "qwen35-harness-posttrained-exact-lora"


def _server_record(path: Path, *, deployment: str) -> dict[str, Any]:
    record = read_json(path)
    if not isinstance(record, dict) or set(record) != _SERVER_FIELDS:
        raise IntegrityError(
            f"{path} must contain exactly the complete server fields {sorted(_SERVER_FIELDS)}"
        )
    if record.get("served_model_name") != deployment:
        raise IntegrityError(f"{path} served_model_name differs from the deployment")
    return record


def _tunnel_record(
    path: Path,
    *,
    endpoint_host: str,
    endpoint_port: int,
    server_port: int,
) -> dict[str, Any]:
    record = read_json(path)
    if not isinstance(record, dict) or set(record) != TUNNEL_RECORD_FIELDS:
        raise IntegrityError(
            f"{path} must contain exactly the complete tunnel fields "
            f"{sorted(TUNNEL_RECORD_FIELDS)}"
        )
    if record.get("listen_host") != endpoint_host or record.get(
        "listen_port"
    ) != endpoint_port:
        raise IntegrityError(f"{path} listener differs from its local base URL")
    if record.get("target_port") != server_port:
        raise IntegrityError(f"{path} target port differs from its vLLM server port")
    return record


def _probe_models(
    *, base_url: str, deployment: str, api_key: str, timeout_seconds: float
) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url}/models",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            body = response.read(4 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise IntegrityError(
            f"{deployment} /v1/models probe returned HTTP {exc.code}"
        ) from exc
    except (OSError, urllib.error.URLError) as exc:
        raise IntegrityError(
            f"{deployment} /v1/models probe failed: {type(exc).__name__}: {exc}"
        ) from exc
    if status != 200:
        raise IntegrityError(f"{deployment} /v1/models probe returned HTTP {status}")
    if len(body) > 4 * 1024 * 1024:
        raise IntegrityError(f"{deployment} /v1/models response exceeds 4 MiB")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"{deployment} /v1/models returned invalid JSON") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise IntegrityError(f"{deployment} /v1/models lacks a data list")
    served_models = sorted(
        {
            item["id"]
            for item in data
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item["id"]
        }
    )
    if deployment not in served_models:
        raise IntegrityError(
            f"{deployment} is absent from its live /v1/models response: {served_models}"
        )
    model_bindings = sorted(
        (
            {
                "id": item["id"],
                "root": item.get("root"),
                "parent": item.get("parent"),
            }
            for item in data
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item["id"]
        ),
        key=lambda item: (
            item["id"],
            str(item["root"]),
            str(item["parent"]),
        ),
    )
    return {
        "http_status": status,
        "served_models": served_models,
        "model_bindings": model_bindings,
        "checked_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "response_sha256": sha256_bytes(body),
    }


def _model_specs(
    *,
    base_name: str,
    trained_name: str,
    base_url: str,
    trained_url: str,
    base_deployment: str,
    trained_deployment: str,
    api_key_environment: str,
    frequency_penalty: float | None,
) -> dict[str, dict[str, Any]]:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_environment) is None:
        raise IntegrityError("api-key environment name is invalid")
    common = {
        "provider": "openai",
        "api_key": f"env:{api_key_environment}",
        "vision": False,
        # browser-use otherwise contributes its provider-agnostic 0.3 default.
        # The Qwen training/selection request omitted this field, represented
        # by an explicit null model override that ChatOpenAI leaves off wire.
        "extra": {"frequency_penalty": frequency_penalty},
    }
    return {
        "base": {
            "name": base_name,
            **common,
            "base_url": base_url,
            "deployment": base_deployment,
        },
        "trained": {
            "name": trained_name,
            **common,
            "base_url": trained_url,
            "deployment": trained_deployment,
        },
    }


def _endpoint_manifest(
    *,
    frozen_manifest: dict[str, Any],
    model_specs: dict[str, dict[str, Any]],
    servers: dict[str, dict[str, Any]],
    tunnels: dict[str, dict[str, Any]],
    probes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    serving_modes = {
        servers[arm].get("config", {}).get("serving_mode")
        for arm in ("base", "trained")
    }
    if serving_modes == {"exact_peft_lora"}:
        schema_version = 3
    elif serving_modes == {None}:
        schema_version = 2
    else:
        raise IntegrityError(
            "base and trained servers must use the same complete-checkpoint or exact-LoRA mode"
        )
    arms = {}
    for arm in ("base", "trained"):
        spec = model_specs[arm]
        arms[arm] = {
            "name": spec["name"],
            "base_url": spec["base_url"],
            "deployment": spec["deployment"],
            "weight_sha256": frozen_manifest["model_contract"][
                "base_weight_sha256"
                if arm == "base"
                else "trained_weight_sha256"
            ],
            "container_image_digest": frozen_manifest["inference_contract"][
                "container_image_digest"
            ],
            "model_spec_sha256": sha256_bytes(canonical_bytes(spec)),
            "server": servers[arm],
            "tunnel": tunnels[arm],
            "ready_probe": probes[arm],
        }
    core = {
        "schema_version": schema_version,
        "frozen_manifest_sha256": frozen_manifest["manifest_sha256"],
        "inference_contract_sha256": sha256_bytes(
            canonical_bytes(frozen_manifest["inference_contract"])
        ),
        "arms": arms,
    }
    manifest = {
        **core,
        "endpoint_manifest_sha256": sha256_bytes(canonical_bytes(core)),
    }
    verify_endpoint_manifest(
        endpoint_manifest=manifest,
        frozen_manifest=frozen_manifest,
        model_specs=model_specs,
        arms={"base", "trained"},
    )
    return manifest


def prepare_final_bundle(
    *,
    repository_root: Path,
    config_path: Path,
    split_path: Path,
    matrix_path: Path,
    output_dir: Path,
    results_root: Path,
    storefront_base_port: int,
    python_executable: str,
    base_weight_sha256: str,
    trained_weight_sha256: str,
    tokenizer_sha256: str,
    chat_template_sha256: str,
    container_image_digest: str,
    serving_stack: dict[str, Any],
    base_name: str,
    trained_name: str,
    base_url: str,
    trained_url: str,
    base_deployment: str,
    trained_deployment: str,
    base_server_record_path: Path,
    trained_server_record_path: Path,
    base_tunnel_record_path: Path,
    trained_tunnel_record_path: Path,
    api_key_environment: str,
    probe_timeout_seconds: float,
) -> dict[str, Any]:
    """Create all frozen inputs for, but never execute, the 320-run final matrix."""

    if output_dir.exists():
        raise IntegrityError(f"refusing to overwrite final preparation directory: {output_dir}")
    if probe_timeout_seconds <= 0:
        raise IntegrityError("probe timeout must be positive")
    if not repository_root.is_dir():
        raise IntegrityError(f"repository root is absent: {repository_root}")
    if not isinstance(serving_stack, dict) or not serving_stack:
        raise IntegrityError("serving-stack JSON must be a nonempty object")
    if base_deployment != BASE_DEPLOYMENT:
        raise IntegrityError(f"base deployment must be {BASE_DEPLOYMENT!r}")
    if trained_deployment != TRAINED_DEPLOYMENT:
        raise IntegrityError(
            f"trained deployment must be {TRAINED_DEPLOYMENT!r}"
        )
    config = read_json(config_path)
    split = read_json(split_path)
    audit_split(split, config)
    matrix = read_json(matrix_path)
    expected_matrix = final_matrix(config, selected_arm="trained")
    if matrix != expected_matrix:
        raise IntegrityError("supplied final matrix is not the exact canonical final matrix")
    matrix_audit = audit_matrix(matrix)
    if matrix.get("kind") != "final" or matrix_audit["run_count"] != 320:
        raise IntegrityError("final preparation requires the canonical 320-run matrix")

    base_url, base_endpoint_host, base_endpoint_port = parse_loopback_v1_url(
        base_url, label="base URL"
    )
    trained_url, trained_endpoint_host, trained_endpoint_port = parse_loopback_v1_url(
        trained_url, label="trained URL"
    )
    if base_url == trained_url:
        raise IntegrityError("base and trained URLs must be distinct")
    if base_endpoint_port == trained_endpoint_port:
        raise IntegrityError("base and trained endpoints must use distinct ports")
    endpoint_ports = {base_endpoint_port, trained_endpoint_port}
    storefront_ports = set(
        range(storefront_base_port, storefront_base_port + matrix_audit["run_count"])
    )
    if endpoint_ports & storefront_ports:
        raise IntegrityError("model endpoint ports overlap the storefront port band")

    servers = {
        "base": _server_record(
            base_server_record_path,
            deployment=base_deployment,
        ),
        "trained": _server_record(
            trained_server_record_path,
            deployment=trained_deployment,
        ),
    }
    tunnels = {
        "base": _tunnel_record(
            base_tunnel_record_path,
            endpoint_host=base_endpoint_host,
            endpoint_port=base_endpoint_port,
            server_port=servers["base"]["port"],
        ),
        "trained": _tunnel_record(
            trained_tunnel_record_path,
            endpoint_host=trained_endpoint_host,
            endpoint_port=trained_endpoint_port,
            server_port=servers["trained"]["port"],
        ),
    }
    model_specs = _model_specs(
        base_name=base_name,
        trained_name=trained_name,
        base_url=base_url,
        trained_url=trained_url,
        base_deployment=base_deployment,
        trained_deployment=trained_deployment,
        api_key_environment=api_key_environment,
        frequency_penalty=config["inference"]["request_parameters"][
            "frequency_penalty"
        ],
    )
    api_key = os.environ.get(api_key_environment)
    if not api_key:
        raise IntegrityError(
            f"{api_key_environment} must be set for both live endpoint probes"
        )

    # Stage the freeze first so no requested output exists if source/data locking fails
    # or either live endpoint is unavailable.
    with tempfile.TemporaryDirectory(prefix="harness-posttrain-final-") as temporary:
        temporary_root = Path(temporary)
        staged_manifest = freeze_manifest(
            root=repository_root,
            config_path=config_path,
            split_path=split_path,
            output_path=temporary_root / "frozen_manifest.json",
            base_weight_sha256=base_weight_sha256,
            trained_weight_sha256=trained_weight_sha256,
            tokenizer_sha256=tokenizer_sha256,
            chat_template_sha256=chat_template_sha256,
            container_image_digest=container_image_digest,
            serving_stack=serving_stack,
        )
        verify_manifest(staged_manifest, root=repository_root)
        probes = {
            arm: _probe_models(
                base_url=model_specs[arm]["base_url"],
                deployment=model_specs[arm]["deployment"],
                api_key=api_key,
                timeout_seconds=probe_timeout_seconds,
            )
            for arm in ("base", "trained")
        }
        endpoint_manifest = _endpoint_manifest(
            frozen_manifest=staged_manifest,
            model_specs=model_specs,
            servers=servers,
            tunnels=tunnels,
            probes=probes,
        )
        # Exercise exact task resolution and all rendering checks before publishing.
        render_run_bundle(
            matrix=matrix,
            config=config,
            frozen_manifest=staged_manifest,
            endpoint_manifest=endpoint_manifest,
            model_specs=model_specs,
            output_dir=temporary_root / "preflight_run_bundle",
            results_root=results_root,
            base_port=storefront_base_port,
            python_executable=python_executable,
        )

    frozen_path = output_dir / "frozen_manifest.json"
    frozen_manifest = freeze_manifest(
        root=repository_root,
        config_path=config_path,
        split_path=split_path,
        output_path=frozen_path,
        base_weight_sha256=base_weight_sha256,
        trained_weight_sha256=trained_weight_sha256,
        tokenizer_sha256=tokenizer_sha256,
        chat_template_sha256=chat_template_sha256,
        container_image_digest=container_image_digest,
        serving_stack=serving_stack,
    )
    if frozen_manifest != staged_manifest:
        raise IntegrityError("repository inputs drifted during final preparation")
    verify_manifest(frozen_manifest, root=repository_root)
    model_specs_path = output_dir / "model_specs.json"
    endpoint_path = output_dir / "endpoint_manifest.json"
    write_json_create_only(model_specs_path, model_specs)
    write_json_create_only(endpoint_path, endpoint_manifest)
    launch_manifest = render_run_bundle(
        matrix=matrix,
        config=config,
        frozen_manifest=frozen_manifest,
        endpoint_manifest=endpoint_manifest,
        model_specs=model_specs,
        output_dir=output_dir / "run_bundle",
        results_root=results_root,
        base_port=storefront_base_port,
        python_executable=python_executable,
    )
    core = {
        "schema_version": 2,
        "campaign_id": config["campaign_id"],
        "run_count": matrix_audit["run_count"],
        "frozen_manifest_sha256": frozen_manifest["manifest_sha256"],
        "matrix_sha256": matrix["matrix_sha256"],
        "model_specs_sha256": sha256_file(model_specs_path),
        "endpoint_manifest_sha256": endpoint_manifest[
            "endpoint_manifest_sha256"
        ],
        "launch_manifest_sha256": launch_manifest["launch_manifest_sha256"],
        "base_server_record_sha256": sha256_file(base_server_record_path),
        "trained_server_record_sha256": sha256_file(trained_server_record_path),
        "base_tunnel_record_sha256": sha256_file(base_tunnel_record_path),
        "trained_tunnel_record_sha256": sha256_file(trained_tunnel_record_path),
        "results_root": str(results_root.resolve()),
        "storefront_port_band": [
            storefront_base_port,
            storefront_base_port + matrix_audit["run_count"] - 1,
        ],
    }
    report = {
        **core,
        "preparation_sha256": sha256_bytes(canonical_bytes(core)),
    }
    write_json_create_only(output_dir / "preparation.json", report)
    return report
