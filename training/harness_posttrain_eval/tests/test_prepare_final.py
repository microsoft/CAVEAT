from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import PACKAGE_ROOT, REPOSITORY_ROOT

from harness_posttrain_eval import prepare_final
from harness_posttrain_eval.cli import parser
from harness_posttrain_eval.common import IntegrityError


def _server_record(
    path: Path, *, model_path: str, deployment: str, port: int
) -> None:
    path.write_text(
        json.dumps(
            {
                "model_path": model_path,
                "served_model_name": deployment,
                "port": port,
                "argv": [
                    "python3",
                    "-m",
                    "vllm.entrypoints.openai.api_server",
                    "--model",
                    model_path,
                    "--served-model-name",
                    deployment,
                    "--port",
                    str(port),
                    "--dtype",
                    "bfloat16",
                ],
                "config": {
                    "model_path": model_path,
                    "served_model_name": deployment,
                    "port": port,
                    "dtype": "bfloat16",
                },
            }
        ),
        encoding="utf-8",
    )


def _tunnel_record(path: Path, *, arm: str, listen_port: int) -> None:
    host = "127.0.0.1"
    target_port = 8000
    pod = f"{arm}-pod"
    path.write_text(
        json.dumps(
            {
                "transport": "kubectl-port-forward",
                "listen_host": host,
                "listen_port": listen_port,
                "target_pod": pod,
                "target_port": target_port,
                "argv": [
                    "kubectl",
                    "--namespace",
                    "eval",
                    "port-forward",
                    "--address",
                    host,
                    f"pod/{pod}",
                    f"{listen_port}:{target_port}",
                ],
                "config": {
                    "context": "production-cluster",
                    "namespace": "eval",
                    "listen_host": host,
                    "listen_port": listen_port,
                    "target_pod": pod,
                    "target_port": target_port,
                },
            }
        ),
        encoding="utf-8",
    )


def test_prepare_final_builds_create_only_live_attested_bundle(tmp_path, monkeypatch):
    base_record = tmp_path / "base-server.json"
    trained_record = tmp_path / "trained-server.json"
    base_tunnel_record = tmp_path / "base-tunnel.json"
    trained_tunnel_record = tmp_path / "trained-tunnel.json"
    _server_record(
        base_record,
        model_path="/models/qwen-base",
        deployment=prepare_final.BASE_DEPLOYMENT,
        port=8000,
    )
    _server_record(
        trained_record,
        model_path="/final/model",
        deployment=prepare_final.TRAINED_DEPLOYMENT,
        port=8000,
    )
    _tunnel_record(base_tunnel_record, arm="base", listen_port=18000)
    _tunnel_record(trained_tunnel_record, arm="trained", listen_port=18100)
    calls = []

    def probe(*, base_url, deployment, api_key, timeout_seconds):
        calls.append((base_url, deployment, api_key, timeout_seconds))
        model_path = (
            "/models/qwen-base"
            if deployment == prepare_final.BASE_DEPLOYMENT
            else "/final/model"
        )
        return {
            "http_status": 200,
            "served_models": [deployment],
            "model_bindings": [
                {"id": deployment, "root": model_path, "parent": None}
            ],
            "checked_at": "2026-08-12T00:00:00Z",
            "response_sha256": ("a" if deployment == "qwen-base" else "b") * 64,
        }

    monkeypatch.setattr(prepare_final, "_probe_models", probe)
    monkeypatch.setenv("TEST_VLLM_API_KEY", "secret-not-written-literally")
    output = tmp_path / "prepared"
    kwargs = {
        "repository_root": REPOSITORY_ROOT,
        "config_path": PACKAGE_ROOT / "configs/campaign.json",
        "split_path": PACKAGE_ROOT / "manifests/shadow_split.json",
        "matrix_path": PACKAGE_ROOT / "manifests/final_matrix.json",
        "output_dir": output,
        "results_root": tmp_path / "results",
        "storefront_base_port": 22000,
        "python_executable": "/usr/bin/python3",
        "base_weight_sha256": "1" * 64,
        "trained_weight_sha256": "2" * 64,
        "tokenizer_sha256": "3" * 64,
        "chat_template_sha256": "4" * 64,
        "container_image_digest": "sha256:" + "5" * 64,
        "serving_stack": {"engine": "vllm", "version": "test"},
        "base_name": "Qwen base",
        "trained_name": "Qwen trained",
        "base_url": "http://127.0.0.1:18000/v1",
        "trained_url": "http://127.0.0.1:18100/v1",
        "base_deployment": prepare_final.BASE_DEPLOYMENT,
        "trained_deployment": prepare_final.TRAINED_DEPLOYMENT,
        "base_server_record_path": base_record,
        "trained_server_record_path": trained_record,
        "base_tunnel_record_path": base_tunnel_record,
        "trained_tunnel_record_path": trained_tunnel_record,
        "api_key_environment": "TEST_VLLM_API_KEY",
        "probe_timeout_seconds": 12.0,
    }
    report = prepare_final.prepare_final_bundle(**kwargs)
    assert report["schema_version"] == 2
    assert report["run_count"] == 320
    assert len(calls) == 2
    model_specs = json.loads((output / "model_specs.json").read_text(encoding="utf-8"))
    assert model_specs["base"]["api_key"] == "env:TEST_VLLM_API_KEY"
    assert model_specs["base"]["extra"] == {"frequency_penalty": None}
    assert model_specs["trained"]["extra"] == {"frequency_penalty": None}
    assert "secret-not-written-literally" not in (output / "model_specs.json").read_text()
    endpoint = json.loads(
        (output / "endpoint_manifest.json").read_text(encoding="utf-8")
    )
    assert endpoint["arms"]["base"]["ready_probe"]["served_models"] == [
        prepare_final.BASE_DEPLOYMENT
    ]
    assert endpoint["arms"]["base"]["server"]["port"] == 8000
    assert endpoint["arms"]["base"]["tunnel"]["listen_port"] == 18000
    assert endpoint["arms"]["base"]["tunnel"]["target_port"] == 8000
    assert report["base_tunnel_record_sha256"]
    assert report["trained_tunnel_record_sha256"]
    launch = json.loads(
        (output / "run_bundle/launch_manifest.json").read_text(encoding="utf-8")
    )
    assert len(launch["launches"]) == 320
    assert launch["endpoint_manifest_sha256"] == endpoint[
        "endpoint_manifest_sha256"
    ]
    with pytest.raises(IntegrityError, match="refusing to overwrite"):
        prepare_final.prepare_final_bundle(**kwargs)


def test_prepare_final_rejects_nonlocal_model_endpoint():
    with pytest.raises(IntegrityError, match="loopback"):
        prepare_final.parse_loopback_v1_url(
            "https://models.example.com:18000/v1", label="base URL"
        )


def test_prepare_final_cli_uses_exact_production_deployments():
    arguments = parser().parse_args(
        [
            "prepare-final",
            "--output-dir",
            "/tmp/new-preparation",
            "--results-root",
            "/tmp/new-results",
            "--storefront-base-port",
            "22000",
            "--base-weight-sha256",
            "1" * 64,
            "--trained-weight-sha256",
            "2" * 64,
            "--tokenizer-sha256",
            "3" * 64,
            "--chat-template-sha256",
            "4" * 64,
            "--container-image-digest",
            "sha256:" + "5" * 64,
            "--serving-stack-json",
            "/tmp/serving-stack.json",
            "--base-url",
            "http://127.0.0.1:18000/v1",
            "--trained-url",
            "http://127.0.0.1:18100/v1",
            "--base-server-record",
            "/tmp/base-server.json",
            "--trained-server-record",
            "/tmp/trained-server.json",
            "--base-tunnel-record",
            "/tmp/base-tunnel.json",
            "--trained-tunnel-record",
            "/tmp/trained-tunnel.json",
        ]
    )
    assert arguments.base_deployment == prepare_final.BASE_DEPLOYMENT
    assert arguments.trained_deployment == prepare_final.TRAINED_DEPLOYMENT
