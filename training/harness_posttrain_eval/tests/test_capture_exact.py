from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from harness_posttrain_eval import capture_exact
from harness_posttrain_eval.common import IntegrityError


def _contract() -> dict[str, Any]:
    shared = {
        "path": "/campaign/selected/merged",
        "tokenizer_json_sha256": "7" * 64,
        "chat_template_sha256": "8" * 64,
    }
    arms: dict[str, dict[str, Any]] = {}
    for arm, values in {
        "base": ("zero_control", "1", "3", "5"),
        "trained": ("refinement_step20", "2", "4", "6"),
    }.items():
        kind, parent_digit, adapter_digit, config_digit = values
        record = {
            "adapter_kind": kind,
            "parent_path": f"/models/{arm}-parent",
            "parent_tree_sha256": parent_digit * 64,
            "adapter_path": f"/models/{arm}-adapter",
            "adapter_tree_sha256": adapter_digit * 64,
            "adapter_config_sha256": config_digit * 64,
        }
        record["composite_weight_sha256"] = capture_exact._composite_sha256(  # noqa: SLF001
            record, shared
        )
        arms[arm] = record
    pair = capture_exact._digest(  # noqa: SLF001
        {
            "schema": "harness-posttrain.exact-lora-inference.v1",
            "base": arms["base"]["composite_weight_sha256"],
            "trained": arms["trained"]["composite_weight_sha256"],
        }
    )
    return {
        "schema": "harness-posttrain-eval.exact-lora-contract.v1",
        "serving_mode": "exact_peft_lora",
        "manifest_sha256": "9" * 64,
        "manifest_receipt_sha256": "a" * 64,
        "composite_pair_sha256": pair,
        "refinement_receipt_sha256": "b" * 64,
        "refinement_update": 20,
        "shared_tokenizer": shared,
        "zero_control": {
            "all_zero": True,
            "nonzero_elements": 0,
            "exact_logits_equal": True,
            "maximum_absolute_logit_difference": 0.0,
        },
        "trained_nonnoop": {
            "all_zero": False,
            "maximum_absolute_logit_difference": 0.25,
            "relative_l2_difference": 0.062,
        },
        "arms": arms,
    }


def _payload(arm: str = "base") -> dict[str, Any]:
    contract = _contract()
    composition = contract["arms"][arm]
    served = f"qwen-{arm}-exact-lora"
    return {
        "schema": "harness-posttrain-eval.exact-live-capture.v2",
        "arm": arm,
        "manifest_sha256": contract["manifest_sha256"],
        "manifest_receipt_sha256": contract["manifest_receipt_sha256"],
        "served_model_name": served,
        "composition": composition,
        "contract": contract,
        "argv": [
            "/usr/bin/python3",
            "/opt/vllm/bin/vllm",
            "serve",
            composition["parent_path"],
            "--tokenizer",
            contract["shared_tokenizer"]["path"],
            "--served-model-name",
            "qwen-private-parent",
            "--port",
            "8000",
            "--dtype",
            "bfloat16",
            "--enable-lora",
            "--max-loras",
            "1",
            "--max-cpu-loras",
            "1",
            "--max-lora-rank",
            "64",
            "--lora-dtype",
            "bfloat16",
            "--lora-modules",
            f"{served}={composition['adapter_path']}",
            "--data-parallel-size",
            "4",
            "--api-server-count",
            "4",
        ],
        "runtime": {
            "runtime_lora_updating_absent": True,
            "fla_tilelang": "0",
        },
    }


def _capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    *,
    returncode: int = 0,
    stderr: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, **kwargs})
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout=json.dumps(payload),
            stderr=stderr,
        )

    monkeypatch.setattr(capture_exact.subprocess, "run", fake_run)
    result = capture_exact.capture_exact_endpoint(
        arm=str(payload.get("arm", "base")),
        pod="base-pod",
        namespace="eval",
        context="oidc@cluster",
        manifest_path="/campaign/final/exact_lora_manifest.json",
        receipt_path="/campaign/final/exact_lora_manifest_receipt.json",
        source_root="/runs/exact-source",
        output_path=tmp_path / "server.json",
        contract_output_path=tmp_path / "contract.json",
        kubeconfig=tmp_path / "kubeconfig",
        timeout_seconds=42,
    )
    return result, calls


def test_capture_publishes_manifest_bound_pid1_record_and_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, calls = _capture(tmp_path, monkeypatch, _payload())
    server = json.loads((tmp_path / "server.json").read_text(encoding="utf-8"))
    contract = json.loads((tmp_path / "contract.json").read_text(encoding="utf-8"))

    assert result["server_record"] == server
    assert result["exact_lora_contract"] == contract
    assert set(server) == {"model_path", "served_model_name", "port", "argv", "config"}
    assert server["model_path"] == contract["arms"]["base"]["parent_path"]
    assert server["config"]["adapter_kind"] == "zero_control"
    assert server["config"]["exact_lora_manifest_sha256"] == contract["manifest_sha256"]
    assert server["config"]["environment"] == {
        "FLA_TILELANG": "0",
        "VLLM_ALLOW_RUNTIME_LORA_UPDATING": None,
    }
    assert server["config"]["frontend_launcher"] == "vllm serve"
    assert server["config"]["data_parallel_size"] == 4
    assert server["config"]["api_server_count"] == 4
    assert calls[0]["command"][:3] == [
        "kubectl",
        "--kubeconfig",
        str((tmp_path / "kubeconfig").resolve()),
    ]
    exec_index = calls[0]["command"].index("exec")
    assert calls[0]["command"][exec_index + 1] == "-i"
    assert calls[0]["command"][-4:] == [
        "/campaign/final/exact_lora_manifest.json",
        "/campaign/final/exact_lora_manifest_receipt.json",
        "/runs/exact-source",
        "base",
    ]
    assert "/proc/1/cmdline" in calls[0]["input"]
    assert "/proc/1/environ" in calls[0]["input"]
    assert calls[0]["timeout"] == 42


def test_capture_is_create_only_before_remote_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capture(tmp_path, monkeypatch, _payload())
    calls = 0

    def unexpected_run(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        raise AssertionError("remote command must not run for an existing output")

    monkeypatch.setattr(capture_exact.subprocess, "run", unexpected_run)
    with pytest.raises(IntegrityError, match="refusing to overwrite frozen artifact"):
        capture_exact.capture_exact_endpoint(
            arm="base",
            pod="base-pod",
            namespace="eval",
            context="oidc@cluster",
            manifest_path="/campaign/final/exact_lora_manifest.json",
            receipt_path="/campaign/final/exact_lora_manifest_receipt.json",
            source_root="/runs/exact-source",
            output_path=tmp_path / "server.json",
        )
    assert calls == 0


def test_capture_rejects_runtime_lora_updating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload()
    payload["runtime"]["runtime_lora_updating_absent"] = False
    with pytest.raises(IntegrityError, match="safe environment"):
        _capture(tmp_path, monkeypatch, payload)
    assert not (tmp_path / "server.json").exists()
    assert not (tmp_path / "contract.json").exists()


def test_capture_rejects_pid1_adapter_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload("trained")
    binding_index = payload["argv"].index("--lora-modules") + 1
    payload["argv"][binding_index] = "qwen-trained-exact-lora=/models/rogue"
    with pytest.raises(IntegrityError, match="frozen public adapter"):
        _capture(tmp_path, monkeypatch, payload)
    assert not (tmp_path / "server.json").exists()


def test_capture_rejects_legacy_single_frontend_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload()
    model = payload["composition"]["parent_path"]
    argv = payload["argv"]
    model_index = argv.index(model)
    payload["argv"] = [
        argv[0],
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        model,
        *argv[model_index + 1 :],
    ]
    with pytest.raises(IntegrityError, match="supported vllm serve launcher"):
        _capture(tmp_path, monkeypatch, payload)
    assert not (tmp_path / "server.json").exists()


def test_capture_rejects_missing_api_frontends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _payload()
    option_index = payload["argv"].index("--api-server-count")
    del payload["argv"][option_index : option_index + 2]
    with pytest.raises(IntegrityError, match="--api-server-count"):
        _capture(tmp_path, monkeypatch, payload)
    assert not (tmp_path / "server.json").exists()


def test_capture_rejects_remote_verifier_failure_without_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(IntegrityError, match="receipt does not bind manifest"):
        _capture(
            tmp_path,
            monkeypatch,
            _payload(),
            returncode=1,
            stderr="receipt does not bind manifest bytes",
        )
    assert not (tmp_path / "server.json").exists()


def test_composite_fixed_vector_matches_finalizer_and_launcher() -> None:
    shared = {
        "tokenizer_json_sha256": "4" * 64,
        "chat_template_sha256": "5" * 64,
    }
    record = {
        "parent_tree_sha256": "1" * 64,
        "adapter_tree_sha256": "2" * 64,
        "adapter_config_sha256": "3" * 64,
    }
    assert capture_exact._composite_sha256(record, shared) == (  # noqa: SLF001
        "c1346da9574a5d0a2e5e444f460588cb36f47048c866e96c184ef6d7c3acc85d"
    )


def test_capture_rejects_contract_mutation() -> None:
    payload = _payload()
    mutated = copy.deepcopy(payload)
    mutated["contract"]["arms"]["base"]["parent_tree_sha256"] = "f" * 64
    with pytest.raises(IntegrityError, match="composite hash"):
        capture_exact._server_record(mutated, arm="base")  # noqa: SLF001
