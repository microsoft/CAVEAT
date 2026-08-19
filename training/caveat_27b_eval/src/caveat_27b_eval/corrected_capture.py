"""Live, create-only capture for a browser-action-corrected exact-LoRA endpoint."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .common import (
    IntegrityError,
    canonical_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json_create_only,
)
from .corrected_eval import (
    ManifestValidator,
    _corrected_endpoint_core,
    audit_corrected_preparation,
)
from .launcher import parse_loopback_v1_url, vllm_serve_model_index
from .prepare_final import _probe_models

CAPTURE_SCHEMA = "caveat-27b-eval.corrected-live-capture.v1"

_REMOTE_VERIFIER = r"""
import json
import os
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1]).resolve()
source_root = Path(sys.argv[2]).resolve()
sys.path.insert(0, str(source_root / "src"))

from caveat_27b.artifacts import read_json
from caveat_27b.browser_action_finalization import (
    validate_browser_action_serving_manifest,
)

resolved = validate_browser_action_serving_manifest(manifest_path, arm="trained")
manifest = read_json(manifest_path)
descriptor = manifest["arms"]["trained"]
shared = manifest["shared_tokenizer"]
cmdline = Path("/proc/1/cmdline").read_bytes()
argv = [item.decode("utf-8", "strict") for item in cmdline.split(b"\0") if item]
if not argv:
    raise SystemExit("PID 1 has an empty command line")
environment = Path("/proc/1/environ").read_bytes().split(b"\0")
runtime_update = b"VLLM_ALLOW_RUNTIME_LORA_UPDATING"
if any(item == runtime_update or item.startswith(runtime_update + b"=") for item in environment):
    raise SystemExit("runtime LoRA updating is present in PID 1 environment")
fla = [
    item.split(b"=", 1)[1].decode("utf-8", "strict")
    for item in environment
    if item.startswith(b"FLA_TILELANG=")
]
if fla != ["0"]:
    raise SystemExit("PID 1 FLA_TILELANG is not exactly 0")

print(json.dumps({
    "schema": "caveat-27b-eval.corrected-live-capture.v1",
    "validated": resolved,
    "component_identity": {
        "parent_tree_sha256": descriptor["parent"]["tree_sha256"],
        "adapter_tree_sha256": descriptor["adapter"]["tree_sha256"],
        "adapter_config_sha256": descriptor["adapter_config_sha256"],
        "tokenizer_json_sha256": shared["tokenizer_json_sha256"],
        "chat_template_sha256": shared["chat_template_sha256"],
    },
    "argv": argv,
    "runtime": {
        "runtime_lora_updating_absent": True,
        "fla_tilelang": "0",
    },
}, sort_keys=True, separators=(",", ":")))
"""

_REMOTE_MANIFEST_VALIDATOR = r"""
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1]).resolve()
source_root = Path(sys.argv[2]).resolve()
arm = sys.argv[3]
sys.path.insert(0, str(source_root / "src"))

from caveat_27b.browser_action_finalization import (
    validate_browser_action_serving_manifest,
)

print(json.dumps(
    validate_browser_action_serving_manifest(manifest_path, arm=arm),
    sort_keys=True,
    separators=(",", ":"),
))
"""


def _option_values(argv: list[str], option: str) -> list[str]:
    values: list[str] = []
    for index, token in enumerate(argv):
        if token == option:
            if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                raise IntegrityError(f"live corrected server has an unbound {option}")
            values.append(argv[index + 1])
        elif token.startswith(option + "="):
            values.append(token.split("=", 1)[1])
    return values


def _one_option(argv: list[str], option: str) -> str:
    values = _option_values(argv, option)
    if len(values) != 1:
        raise IntegrityError(f"live corrected server must bind exactly one {option}")
    return values[0]


def _remote_command(
    *, source_tunnel: dict[str, Any], pod: str, manifest_path: str, source_root: str
) -> list[str]:
    argv = source_tunnel.get("argv")
    if not isinstance(argv, list) or argv.count("port-forward") != 1:
        raise IntegrityError("source tunnel command cannot authorize corrected capture")
    prefix = argv[: argv.index("port-forward")]
    return [
        *prefix,
        "exec",
        "-i",
        pod,
        "--",
        "python3",
        "-",
        manifest_path,
        source_root,
    ]


def _pod_command(*, source_tunnel: dict[str, Any], pod: str) -> list[str]:
    argv = source_tunnel.get("argv")
    if not isinstance(argv, list) or argv.count("port-forward") != 1:
        raise IntegrityError("source tunnel command cannot authorize pod inspection")
    return [*argv[: argv.index("port-forward")], "get", "pod", pod, "-o", "json"]


def _pod_runtime_identity(
    *,
    source_tunnel: dict[str, Any],
    pod: str,
    expected_image_digest: str,
    timeout_seconds: float,
) -> dict[str, str]:
    command = _pod_command(source_tunnel=source_tunnel, pod=pod)
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IntegrityError(f"cannot inspect corrected serving pod: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip()[-4000:] or completed.stdout.strip()[-4000:]
        raise IntegrityError(
            f"corrected pod inspection failed with exit {completed.returncode}: {detail}"
        )
    try:
        record = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise IntegrityError("corrected pod inspection returned invalid JSON") from exc
    if not isinstance(record, dict):
        raise IntegrityError("corrected pod inspection returned a non-object")
    metadata = record.get("metadata")
    spec = record.get("spec")
    status = record.get("status")
    containers = spec.get("containers") if isinstance(spec, dict) else None
    statuses = status.get("containerStatuses") if isinstance(status, dict) else None
    if (
        not isinstance(metadata, dict)
        or metadata.get("name") != pod
        or not isinstance(metadata.get("uid"), str)
        or not metadata["uid"]
        or not isinstance(containers, list)
        or len(containers) != 1
        or not isinstance(statuses, list)
        or len(statuses) != 1
    ):
        raise IntegrityError("corrected pod must contain exactly one serving container")
    container = containers[0]
    live = statuses[0]
    if (
        not isinstance(container, dict)
        or not isinstance(live, dict)
        or not isinstance(container.get("name"), str)
        or live.get("name") != container["name"]
        or live.get("ready") is not True
        or live.get("started") is not True
        or not isinstance(live.get("containerID"), str)
        or not live["containerID"]
        or not isinstance(live.get("imageID"), str)
    ):
        raise IntegrityError("corrected serving container is not uniquely ready/started")
    image_id = live["imageID"]
    if not image_id.endswith(f"@{expected_image_digest}"):
        raise IntegrityError("corrected serving container image digest differs from raw")
    return {
        "pod_uid": metadata["uid"],
        "container_name": container["name"],
        "container_id": live["containerID"],
        "image_id": image_id,
        "image_digest": expected_image_digest,
    }


def remote_manifest_validator(
    *,
    source_preparation_dir: Path,
    pod: str,
    local_manifest_path: Path,
    remote_manifest_path: str,
    remote_source_root: str,
    timeout_seconds: float = 1800.0,
) -> ManifestValidator:
    """Return an authoritative validator callable for cluster-only components.

    The local manifest and receipt must be byte-identical copies of the files
    revalidated inside the serving pod.  Only their paths are localized in the
    returned record; component paths and all hashes remain the remote
    validator's values.
    """

    local = local_manifest_path.resolve()
    local_receipt = local.parent / "exact_lora_manifest_receipt.json"
    if not local.is_file() or not local_receipt.is_file():
        raise IntegrityError("local corrected manifest and receipt copies are required")
    if not pod or timeout_seconds <= 0:
        raise IntegrityError("remote corrected validator pod/timeout is invalid")
    for label, value in (
        ("remote manifest", remote_manifest_path),
        ("remote source root", remote_source_root),
    ):
        if not PurePosixPath(value).is_absolute():
            raise IntegrityError(f"{label} must be an absolute pod path")
    endpoint = read_json(source_preparation_dir.resolve() / "endpoint_manifest.json")
    try:
        source_tunnel = endpoint["arms"]["base"]["tunnel"]
    except (KeyError, TypeError) as exc:
        raise IntegrityError(
            "source raw tunnel cannot authorize remote validation"
        ) from exc
    command = _remote_command(
        source_tunnel=source_tunnel,
        pod=pod,
        manifest_path=remote_manifest_path,
        source_root=remote_source_root,
    )

    def validate(path: Path, *, arm: str) -> dict[str, Any]:
        if path.resolve() != local or arm not in {"base", "trained"}:
            raise IntegrityError("remote validator received an unexpected manifest/arm")
        remote_command = [*command, arm]
        try:
            completed = subprocess.run(
                remote_command,
                input=_REMOTE_MANIFEST_VALIDATOR,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise IntegrityError(
                f"cannot revalidate remote corrected manifest: {exc}"
            ) from exc
        if completed.returncode != 0:
            detail = (
                completed.stderr.strip()[-4000:] or completed.stdout.strip()[-4000:]
            )
            raise IntegrityError(
                "remote corrected manifest validator failed with exit "
                f"{completed.returncode}: {detail}"
            )
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise IntegrityError(
                "remote corrected validator returned invalid JSON"
            ) from exc
        if not isinstance(result, dict):
            raise IntegrityError("remote corrected validator returned a non-object")
        if (
            result.get("manifest_path") != str(PurePosixPath(remote_manifest_path))
            or result.get("manifest_sha256") != sha256_file(local)
            or result.get("manifest_receipt_sha256") != sha256_file(local_receipt)
        ):
            raise IntegrityError(
                "local corrected manifest copies differ from remote validation"
            )
        localized = copy.deepcopy(result)
        localized["manifest_path"] = str(local)
        localized["manifest_receipt_path"] = str(local_receipt.resolve())
        return localized

    return validate


def _server_record(
    payload: Any,
    *,
    binding: dict[str, Any],
    remote_manifest_path: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "validated",
        "component_identity",
        "argv",
        "runtime",
    }:
        raise IntegrityError("remote corrected endpoint capture is not exact")
    if payload.get("schema") != CAPTURE_SCHEMA:
        raise IntegrityError("remote corrected endpoint capture schema changed")
    validated = payload.get("validated")
    trained = binding["arms"]["trained"]
    if not isinstance(validated, dict):
        raise IntegrityError("remote corrected validator result is absent")
    expected_validated = {
        "parent": trained["parent"],
        "adapter": trained["adapter"],
        "tokenizer": binding["tokenizer"],
        "served_model_name": trained["served_model_name"],
        "composite_sha256": trained["composite_sha256"],
        "manifest_path": str(PurePosixPath(remote_manifest_path)),
        "manifest_sha256": binding["manifest_sha256"],
        "manifest_receipt_path": str(
            PurePosixPath(remote_manifest_path).parent
            / "exact_lora_manifest_receipt.json"
        ),
        "manifest_receipt_sha256": binding["manifest_receipt_sha256"],
        "campaign_digest": binding["campaign_digest"],
        "artifact_source_git_sha": binding["artifact_source_git_sha"],
        "selected_checkpoint": binding["selected_checkpoint"],
        "continuation_receipt": binding["continuation_receipt"],
        "selection_receipt": binding["selection_receipt"],
    }
    if validated != expected_validated:
        raise IntegrityError("remote corrected validation differs from local binding")
    identity = payload.get("component_identity")
    expected_identity = {
        "parent_tree_sha256": trained["parent_tree_sha256"],
        "adapter_tree_sha256": trained["adapter_tree_sha256"],
        "adapter_config_sha256": trained["adapter_config_sha256"],
        "tokenizer_json_sha256": binding["tokenizer_json_sha256"],
        "chat_template_sha256": binding["chat_template_sha256"],
    }
    if identity != expected_identity:
        raise IntegrityError("remote corrected component identity differs")
    if payload.get("runtime") != {
        "runtime_lora_updating_absent": True,
        "fla_tilelang": "0",
    }:
        raise IntegrityError("remote corrected PID 1 environment is unsafe")
    argv = payload.get("argv")
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(value, str) and value for value in argv)
    ):
        raise IntegrityError("remote corrected PID 1 command is malformed")
    model_index = vllm_serve_model_index(argv)
    if argv[model_index] != trained["parent"]:
        raise IntegrityError("remote corrected PID 1 parent differs")
    required = {
        "--tokenizer": binding["tokenizer"],
        "--dtype": "bfloat16",
        "--max-loras": "1",
        "--max-cpu-loras": "1",
        "--max-lora-rank": "64",
        "--lora-dtype": "bfloat16",
        "--data-parallel-size": "4",
        "--api-server-count": "4",
    }
    for option, expected in required.items():
        if _one_option(argv, option) != expected:
            raise IntegrityError(f"remote corrected PID 1 {option} differs")
    if argv.count("--enable-lora") != 1:
        raise IntegrityError("remote corrected PID 1 does not enable exactly one LoRA")
    lora = f"{trained['served_model_name']}={trained['adapter']}"
    if _option_values(argv, "--lora-modules") != [lora]:
        raise IntegrityError("remote corrected PID 1 adapter route differs")
    parent_name = _one_option(argv, "--served-model-name")
    if not parent_name or parent_name == trained["served_model_name"]:
        raise IntegrityError("remote corrected public/private routes alias")
    try:
        port = int(_one_option(argv, "--port"))
    except ValueError as exc:
        raise IntegrityError("remote corrected vLLM port is not an integer") from exc
    if not 1 <= port <= 65535:
        raise IntegrityError("remote corrected vLLM port is invalid")
    config = {
        "model_path": trained["parent"],
        "served_model_name": trained["served_model_name"],
        "port": port,
        "dtype": "bfloat16",
        "serving_mode": "exact_peft_lora",
        "frontend_launcher": "vllm serve",
        "data_parallel_size": 4,
        "api_server_count": 4,
        "parent_served_model_name": parent_name,
        "adapter_path": trained["adapter"],
        "adapter_rank": 64,
        **expected_identity,
        "composite_weight_sha256": trained["composite_sha256"],
        "exact_lora_manifest_sha256": binding["manifest_sha256"],
        "adapter_kind": (
            f"browser_action_correction_{binding['selected_checkpoint']['name']}"
        ),
        "environment": {
            "FLA_TILELANG": "0",
            "VLLM_ALLOW_RUNTIME_LORA_UPDATING": None,
        },
    }
    return {
        "model_path": trained["parent"],
        "served_model_name": trained["served_model_name"],
        "port": port,
        "argv": argv,
        "config": config,
    }


def _process_identity(proc_root: Path, pid: int) -> tuple[list[str], str]:
    if type(pid) is not int or pid <= 1:
        raise IntegrityError("corrected tunnel PID must be an integer greater than one")
    try:
        argv = [
            item.decode("utf-8", "strict")
            for item in (proc_root / str(pid) / "cmdline").read_bytes().split(b"\0")
            if item
        ]
        fields = (
            (proc_root / str(pid) / "stat")
            .read_text(encoding="utf-8")
            .rsplit(") ", 1)[1]
            .split()
        )
        start_ticks = fields[19]
    except (OSError, IndexError, UnicodeDecodeError) as exc:
        raise IntegrityError("cannot attest corrected tunnel process") from exc
    if not argv or not start_ticks:
        raise IntegrityError("corrected tunnel process identity is empty")
    return argv, start_ticks


def _tunnel_record(
    *,
    source_tunnel: dict[str, Any],
    pod: str,
    endpoint_host: str,
    endpoint_port: int,
    server_port: int,
    actual_argv: list[str],
) -> dict[str, Any]:
    source_argv = source_tunnel["argv"]
    old_pod = source_tunnel["target_pod"]
    old_listen = source_tunnel["listen_port"]
    old_target = source_tunnel["target_port"]
    expected_argv = [
        (
            f"pod/{pod}"
            if token == f"pod/{old_pod}"
            else f"{endpoint_port}:{server_port}"
            if token == f"{old_listen}:{old_target}"
            else token
        )
        for token in source_argv
    ]
    if actual_argv != expected_argv:
        raise IntegrityError(
            "live corrected tunnel command differs from matched raw tunnel"
        )
    config = copy.deepcopy(source_tunnel["config"])
    config.update(
        listen_host=endpoint_host,
        listen_port=endpoint_port,
        target_pod=pod,
        target_port=server_port,
    )
    return {
        "transport": source_tunnel["transport"],
        "listen_host": endpoint_host,
        "listen_port": endpoint_port,
        "target_pod": pod,
        "target_port": server_port,
        "argv": actual_argv,
        "config": config,
    }


def capture_corrected_endpoint(
    *,
    preparation_dir: Path,
    repository_root: Path,
    pod: str,
    remote_manifest_path: str,
    remote_source_root: str,
    base_url: str,
    api_key: str,
    tunnel_pid: int,
    output_dir: Path,
    display_name: str = "CAVEAT-27B corrected exact-LoRA",
    kubectl_timeout_seconds: float = 1800.0,
    probe_timeout_seconds: float = 60.0,
    manifest_validator: ManifestValidator | None = None,
    proc_root: Path = Path("/proc"),
) -> dict[str, Any]:
    """Capture and qualify the trained corrected endpoint; never start a process."""

    output = output_dir.resolve()
    if output.exists() or output.is_symlink():
        raise IntegrityError(
            f"refusing to overwrite corrected endpoint capture: {output}"
        )
    if not pod or not display_name or not api_key:
        raise IntegrityError(
            "corrected capture pod, display name, and API key are required"
        )
    for label, value in (
        ("remote manifest", remote_manifest_path),
        ("remote source root", remote_source_root),
    ):
        if not PurePosixPath(value).is_absolute():
            raise IntegrityError(f"{label} must be an absolute pod path")
    if kubectl_timeout_seconds <= 0 or probe_timeout_seconds <= 0:
        raise IntegrityError("corrected capture timeouts must be positive")
    audited = audit_corrected_preparation(
        preparation_dir=preparation_dir,
        repository_root=repository_root,
        manifest_validator=manifest_validator,
    )
    protocol = audited["protocol"]
    binding = protocol["corrected_exact_lora"]
    source_endpoint_path = Path(protocol["source"]["preparation_dir"]) / (
        "endpoint_manifest.json"
    )
    source_endpoint = json.loads(source_endpoint_path.read_text(encoding="utf-8"))
    source_raw = source_endpoint["arms"]["base"]
    source_tunnel = source_raw["tunnel"]
    pod_runtime = _pod_runtime_identity(
        source_tunnel=source_tunnel,
        pod=pod,
        expected_image_digest=source_raw["container_image_digest"],
        timeout_seconds=kubectl_timeout_seconds,
    )
    command = _remote_command(
        source_tunnel=source_tunnel,
        pod=pod,
        manifest_path=remote_manifest_path,
        source_root=remote_source_root,
    )
    try:
        completed = subprocess.run(
            command,
            input=_REMOTE_VERIFIER,
            text=True,
            capture_output=True,
            check=False,
            timeout=kubectl_timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IntegrityError(f"cannot capture corrected serving pod: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip()[-4000:] or completed.stdout.strip()[-4000:]
        raise IntegrityError(
            f"remote corrected verifier failed with exit {completed.returncode}: {detail}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise IntegrityError("remote corrected verifier returned invalid JSON") from exc
    server = _server_record(
        payload,
        binding=binding,
        remote_manifest_path=remote_manifest_path,
    )
    base_url, endpoint_host, endpoint_port = parse_loopback_v1_url(
        base_url, label="corrected capture base URL"
    )
    source_spec = audited["protocol"]["source"]
    source_specs_path = Path(source_spec["preparation_dir"]) / "model_specs.json"
    source_specs = json.loads(source_specs_path.read_text(encoding="utf-8"))
    model_spec = copy.deepcopy(source_specs["base"])
    model_spec.update(
        name=display_name,
        base_url=base_url,
        deployment=binding["arms"]["trained"]["served_model_name"],
    )
    tunnel_argv, tunnel_start = _process_identity(proc_root, tunnel_pid)
    tunnel = _tunnel_record(
        source_tunnel=source_tunnel,
        pod=pod,
        endpoint_host=endpoint_host,
        endpoint_port=endpoint_port,
        server_port=server["port"],
        actual_argv=tunnel_argv,
    )
    probe = _probe_models(
        base_url=base_url,
        deployment=model_spec["deployment"],
        api_key=api_key,
        timeout_seconds=probe_timeout_seconds,
    )
    tunnel_argv_after, tunnel_start_after = _process_identity(proc_root, tunnel_pid)
    if (tunnel_argv_after, tunnel_start_after) != (tunnel_argv, tunnel_start):
        raise IntegrityError("corrected tunnel process changed during live probe")
    pod_runtime_after = _pod_runtime_identity(
        source_tunnel=source_tunnel,
        pod=pod,
        expected_image_digest=source_raw["container_image_digest"],
        timeout_seconds=kubectl_timeout_seconds,
    )
    if pod_runtime_after != pod_runtime:
        raise IntegrityError("corrected serving pod changed during live capture")
    endpoint_core = _corrected_endpoint_core(
        audited=audited,
        model_spec=model_spec,
        server=server,
        tunnel=tunnel,
        probe=probe,
    )
    endpoint_binding = {
        **endpoint_core,
        "endpoint_binding_sha256": sha256_bytes(canonical_bytes(endpoint_core)),
    }
    capture_core = {
        "schema": "caveat-27b-eval.corrected-capture-receipt.v1",
        "protocol_sha256": protocol["protocol_sha256"],
        "endpoint_binding_sha256": endpoint_binding["endpoint_binding_sha256"],
        "exact_lora_manifest_sha256": binding["manifest_sha256"],
        "pod": pod,
        "remote_manifest_path": remote_manifest_path,
        "remote_source_root": remote_source_root,
        "tunnel_pid": tunnel_pid,
        "tunnel_process_start_ticks": tunnel_start,
        "pod_runtime": pod_runtime,
        "api_key_persisted": False,
    }
    receipt = {
        **capture_core,
        "capture_sha256": sha256_bytes(canonical_bytes(capture_core)),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}.staging-", dir=output.parent
    ) as temporary:
        stage = Path(temporary) / "capture"
        stage.mkdir()
        for name, value in (
            ("model_spec.json", model_spec),
            ("server_record.json", server),
            ("tunnel_record.json", tunnel),
            ("probe_record.json", probe),
            ("endpoint_binding.json", endpoint_binding),
            ("capture.json", receipt),
        ):
            write_json_create_only(stage / name, value)
        if output.exists() or output.is_symlink():
            raise IntegrityError(f"corrected endpoint capture appeared: {output}")
        os.rename(stage, output)
    return receipt
