from __future__ import annotations

import copy
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest
from agentarena.benchmark import registry
from conftest import REPOSITORY_ROOT

from harness_posttrain_eval import corrected_capture, corrected_eval
from harness_posttrain_eval.common import (
    IntegrityError,
    canonical_bytes,
    sha256_bytes,
    sha256_file,
)
from harness_posttrain_eval.corrected_eval import (
    DEVELOPMENT_EVALUATION,
    FINAL_EVALUATION,
    _SourceBundle,
    audit_corrected_matrix,
    prepare_corrected_evaluations,
    qualify_corrected_endpoint,
    render_corrected_completion_bundle,
)
from harness_posttrain_eval.launcher import exact_lora_composite_sha256
from harness_posttrain_eval.corrected_report import (
    analyze_development_gate,
    bind_evaluation_observations,
)
from harness_posttrain_eval.matrix import final_matrix


def _fake_source(tmp_path: Path, config: dict) -> _SourceBundle:
    matrix = final_matrix(config, selected_arm="trained")
    root = tmp_path / "source-preparation"
    root.mkdir()
    results_root = tmp_path / "source-results"
    model_specs = {
        "base": {
            "name": "raw",
            "provider": "openai",
            "base_url": "http://127.0.0.1:18000/v1",
            "api_key": "env:KEY",
            "deployment": "qwen35-27b-base-exact-lora",
            "vision": False,
            "extra": {"frequency_penalty": None},
        },
        "trained": {
            "name": "step20",
            "provider": "openai",
            "base_url": "http://127.0.0.1:18100/v1",
            "api_key": "env:KEY",
            "deployment": "qwen35-harness-posttrained-exact-lora",
            "vision": False,
            "extra": {"frequency_penalty": None},
        },
    }
    harness_sha = "a" * 64
    tokenizer_sha = "d" * 64
    template_sha = "e" * 64
    raw_parent_sha = "4" * 64
    raw_adapter_sha = "5" * 64
    raw_config_sha = "6" * 64
    step20_parent_sha = "7" * 64
    step20_adapter_sha = "8" * 64
    step20_config_sha = "9" * 64
    raw_weight = exact_lora_composite_sha256(
        parent_tree_sha256=raw_parent_sha,
        adapter_tree_sha256=raw_adapter_sha,
        adapter_config_sha256=raw_config_sha,
        tokenizer_json_sha256=tokenizer_sha,
        chat_template_sha256=template_sha,
        dtype="bfloat16",
    )
    step20_weight = exact_lora_composite_sha256(
        parent_tree_sha256=step20_parent_sha,
        adapter_tree_sha256=step20_adapter_sha,
        adapter_config_sha256=step20_config_sha,
        tokenizer_json_sha256=tokenizer_sha,
        chat_template_sha256=template_sha,
        dtype="bfloat16",
    )
    frozen_core = {
        "schema_version": 1,
        "campaign": config,
        "source_contract": {"harness_sha256": harness_sha},
        "benchmark_lock": {},
        "model_contract": {
            "weight_identity_schema": "harness-posttrain.exact-lora-composite.v1",
            "base_weight_sha256": raw_weight,
            "trained_weight_sha256": step20_weight,
        },
        "inference_contract": {
            "tokenizer_sha256": tokenizer_sha,
            "chat_template_sha256": template_sha,
            "container_image_digest": "sha256:" + "f" * 64,
            "serving_stack": {
                "serving_mode": "exact_peft_lora",
                "exact_lora_contract": {
                    "refinement_update": 20,
                    "arms": {
                        "base": {"adapter_kind": "zero_control"},
                        "trained": {"adapter_kind": "refinement_step20"},
                    },
                },
            },
        },
        "treatment_difference": "test",
    }
    frozen = {
        **frozen_core,
        "manifest_sha256": sha256_bytes(canonical_bytes(frozen_core)),
    }
    task_cache: dict[tuple[str, str], dict] = {}
    launches = []
    specs = {}
    for index, row in enumerate(matrix["runs"]):
        task_key = (row["scenario"], row["variant"])
        if task_key not in task_cache:
            task_cache[task_key] = asdict(
                registry.benchmark_tasks(row["scenario"], variants=[row["variant"]])[0]
            )
        runtime = {
            "AGENTARENA_CACHE_NONCE": f"source/{row['run_id']}",
            "AGENTARENA_EVALUATION_INPUT_ATTESTATION": matrix["matrix_sha256"],
            "AGENTARENA_RUNTIME_SOURCE_ATTESTATION": harness_sha,
            "AGENTARENA_LIMIT_CONTRACT_JSON": "{}",
            "AGENTARENA_NO_VISION": "1",
        }
        audit = {
            "frozen_manifest_sha256": frozen["manifest_sha256"],
            "harness_sha256": harness_sha,
            "inference_contract_sha256": sha256_bytes(
                canonical_bytes(frozen["inference_contract"])
            ),
            "matrix_sha256": matrix["matrix_sha256"],
            "limit_contract_sha256": "1" * 64,
            "endpoint_manifest_sha256": "2" * 64,
        }
        result_dir = results_root / row["results_partition"] / f"cell-{index}"
        spec = {
            "env": row["environment"],
            "scaffold": row["scaffold"],
            "model": model_specs[row["arm"]],
            "task": task_cache[task_key],
            "condition": row["condition"],
            "port": 22000 + index,
            "out_dir": str(result_dir),
            "max_steps": config["limits"]["max_steps"],
            "headless": True,
            "run_id": row["run_id"],
            "pair_id": row["pair_id"],
            "block_seed": row["block_seed"],
            "arm": row["arm"],
            "campaign_id": config["campaign_id"],
            "matrix_sha256": matrix["matrix_sha256"],
            "run_timeout_seconds": config["limits"]["run_timeout_seconds"],
            "runtime_environment": runtime,
            "audit_contract": audit,
        }
        spec_path = root / "run_bundle/configs" / f"{index:04d}.json"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_bytes(canonical_bytes(spec) + b"\n")
        launch = {
            "run_id": row["run_id"],
            "pair_id": row["pair_id"],
            "arm": row["arm"],
            "port": spec["port"],
            "config": str(spec_path),
            "config_sha256": sha256_file(spec_path),
            "results": str(result_dir),
            "argv": [
                "python3",
                "-m",
                "harness_posttrain_eval.launch_one",
                "--spec",
                str(spec_path),
            ],
            "environment": runtime,
            "audit_contract": audit,
        }
        specs[row["run_id"]] = spec
        launches.append(launch)
    launch_core = {
        "schema_version": 1,
        "campaign_id": config["campaign_id"],
        "matrix_sha256": matrix["matrix_sha256"],
        "endpoint_manifest_sha256": "2" * 64,
        "base_port": 22000,
        "results_root": str(results_root),
        "launches": launches,
    }
    launch_manifest = {
        **launch_core,
        "launch_manifest_sha256": sha256_bytes(canonical_bytes(launch_core)),
    }
    source_server = {
        "model_path": "/models/raw",
        "served_model_name": model_specs["base"]["deployment"],
        "port": 8000,
        "argv": [
            "vllm",
            "serve",
            "/models/raw",
            "--tokenizer",
            "/models/source-tokenizer",
            "--served-model-name",
            "qwen35-exact-lora-parent",
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
            f"{model_specs['base']['deployment']}=/models/zero",
            "--data-parallel-size",
            "4",
            "--api-server-count",
            "4",
        ],
        "config": {
            "model_path": "/models/raw",
            "served_model_name": model_specs["base"]["deployment"],
            "port": 8000,
            "dtype": "bfloat16",
            "serving_mode": "exact_peft_lora",
            "frontend_launcher": "vllm serve",
            "data_parallel_size": 4,
            "api_server_count": 4,
            "parent_served_model_name": "qwen35-exact-lora-parent",
            "adapter_path": "/models/zero",
            "adapter_rank": 64,
            "parent_tree_sha256": raw_parent_sha,
            "adapter_tree_sha256": raw_adapter_sha,
            "adapter_config_sha256": raw_config_sha,
            "tokenizer_json_sha256": tokenizer_sha,
            "chat_template_sha256": template_sha,
            "composite_weight_sha256": raw_weight,
            "exact_lora_manifest_sha256": "1" * 64,
            "adapter_kind": "zero_control",
            "environment": {
                "FLA_TILELANG": "0",
                "VLLM_ALLOW_RUNTIME_LORA_UPDATING": None,
            },
        },
    }
    source_tunnel = {
        "transport": "kubectl-port-forward",
        "listen_host": "127.0.0.1",
        "listen_port": 18000,
        "target_pod": "raw-pod",
        "target_port": 8000,
        "argv": [
            "kubectl",
            "--context",
            "test-context",
            "--namespace",
            "eval",
            "port-forward",
            "--address",
            "127.0.0.1",
            "pod/raw-pod",
            "18000:8000",
        ],
        "config": {
            "context": "test-context",
            "namespace": "eval",
            "listen_host": "127.0.0.1",
            "listen_port": 18000,
            "target_pod": "raw-pod",
            "target_port": 8000,
        },
    }
    endpoint_core = {
        "arms": {
            "base": {
                "container_image_digest": frozen["inference_contract"][
                    "container_image_digest"
                ],
                "server": source_server,
                "tunnel": source_tunnel,
            }
        }
    }
    endpoint = {
        **endpoint_core,
        "endpoint_manifest_sha256": sha256_bytes(canonical_bytes(endpoint_core)),
    }
    (root / "model_specs.json").write_bytes(canonical_bytes(model_specs) + b"\n")
    (root / "endpoint_manifest.json").write_bytes(canonical_bytes(endpoint) + b"\n")
    (root / "frozen_manifest.json").write_bytes(canonical_bytes(frozen) + b"\n")
    preparation_core = {
        "frozen_manifest_sha256": frozen["manifest_sha256"],
        "matrix_sha256": matrix["matrix_sha256"],
        "launch_manifest_sha256": launch_manifest["launch_manifest_sha256"],
    }
    preparation = {
        **preparation_core,
        "preparation_sha256": sha256_bytes(canonical_bytes(preparation_core)),
    }
    return _SourceBundle(
        root=root,
        preparation=preparation,
        frozen_manifest=frozen,
        endpoint_manifest=endpoint,
        model_specs=model_specs,
        launch_manifest=launch_manifest,
        matrix=matrix,
        launches={launch["run_id"]: launch for launch in launches},
        specs=specs,
    )


def _manifest_and_validator(
    tmp_path: Path, source: _SourceBundle, *, raw_weight: str | None = None
):
    manifest_dir = tmp_path / "correction" / "final"
    manifest_dir.mkdir(parents=True)
    manifest_path = manifest_dir / "exact_lora_manifest.json"
    corrected_weight = exact_lora_composite_sha256(
        parent_tree_sha256="6" * 64,
        adapter_tree_sha256="7" * 64,
        adapter_config_sha256="8" * 64,
        tokenizer_json_sha256=source.frozen_manifest["inference_contract"][
            "tokenizer_sha256"
        ],
        chat_template_sha256=source.frozen_manifest["inference_contract"][
            "chat_template_sha256"
        ],
        dtype="bfloat16",
    )
    raw_weight = (
        raw_weight or source.frozen_manifest["model_contract"]["base_weight_sha256"]
    )
    manifest = {
        "schema": "harness-posttrain.exact-lora-inference.v1",
        "status": "ok",
        "stage": "browser_action_correction",
        "serving_mode": "exact_peft_lora",
        "final_model_directory_published": False,
        "bf16_weights_merged": False,
        "shared_tokenizer": {
            "path": "/models/shared-tokenizer",
            "tokenizer_json_sha256": source.frozen_manifest["inference_contract"][
                "tokenizer_sha256"
            ],
            "chat_template_sha256": source.frozen_manifest["inference_contract"][
                "chat_template_sha256"
            ],
        },
        "arms": {
            "base": {
                "parent": {"path": "/models/raw", "tree_sha256": "4" * 64},
                "adapter": {"path": "/models/zero", "tree_sha256": "5" * 64},
                "served_model_name": "qwen35-27b-base-exact-lora",
                "composite_sha256": raw_weight,
            },
            "trained": {
                "parent": {"path": "/models/selected", "tree_sha256": "6" * 64},
                "adapter": {"path": "/models/corrected", "tree_sha256": "7" * 64},
                "adapter_config_sha256": "8" * 64,
                "served_model_name": "qwen35-browser-action-corrected-deadbeef-exact-lora",
                "composite_sha256": corrected_weight,
            },
        },
    }
    manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")
    receipt_path = manifest_dir / "exact_lora_manifest_receipt.json"
    receipt_path.write_bytes(canonical_bytes({"status": "ok"}) + b"\n")

    def validator(path: Path, *, arm: str):
        assert arm == "base"
        assert path.resolve() == manifest_path.resolve()
        return {
            "parent": "/models/raw",
            "adapter": "/models/zero",
            "tokenizer": "/models/shared-tokenizer",
            "served_model_name": "qwen35-27b-base-exact-lora",
            "composite_sha256": raw_weight,
            "manifest_path": str(manifest_path.resolve()),
            "manifest_sha256": sha256_file(manifest_path),
            "manifest_receipt_path": str(receipt_path),
            "manifest_receipt_sha256": sha256_file(receipt_path),
            "campaign_digest": "a" * 64,
            "artifact_source_git_sha": "d" * 40,
            "selected_checkpoint": {"name": "step24", "update": 24},
            "continuation_receipt": {"sha256": "a" * 64},
            "selection_receipt": {"sha256": "b" * 64},
        }

    return manifest_path, validator


def test_outcome_blind_protocol_freezes_gate_and_exact_reuse(
    tmp_path, config, monkeypatch
):
    source = _fake_source(tmp_path, config)
    manifest_path, validator = _manifest_and_validator(tmp_path, source)
    monkeypatch.setattr(corrected_eval, "_load_source_bundle", lambda **_kwargs: source)
    output = tmp_path / "prepared"
    receipt = prepare_corrected_evaluations(
        repository_root=REPOSITORY_ROOT,
        config_path=Path(__file__).parents[1] / "configs/campaign.json",
        source_preparation_dir=source.root,
        exact_lora_manifest_path=manifest_path,
        output_dir=output,
        manifest_validator=validator,
    )
    assert receipt["outcome_blind"] is True
    assert receipt["source_results_read"] is False
    assert receipt["run_counts"] == {
        "development_total": 24,
        "development_reused_step20": 12,
        "development_new_corrected": 12,
        "final_total": 320,
        "final_reused_raw": 160,
        "final_new_corrected": 160,
    }
    development = json.loads((output / "development/matrix.json").read_text())
    final = json.loads((output / "final/matrix.json").read_text())
    assert audit_corrected_matrix(development, config=config)["run_count"] == 24
    assert audit_corrected_matrix(final, config=config)["run_count"] == 320
    assert {row["scenario"] for row in development["runs"]} == {"laptop"}
    assert {row["arm"] for row in development["runs"]} == {"step20", "corrected"}
    reuse = json.loads((output / "final/reuse_launch_manifest.json").read_text())
    assert reuse["execution_mode"] == "reuse_only"
    assert len(reuse["launches"]) == 160
    assert {launch["arm"] for launch in reuse["launches"]} == {"base"}


def test_protocol_refuses_raw_identity_drift(tmp_path, config, monkeypatch):
    source = _fake_source(tmp_path, config)
    manifest_path, validator = _manifest_and_validator(
        tmp_path, source, raw_weight="0" * 64
    )
    monkeypatch.setattr(corrected_eval, "_load_source_bundle", lambda **_kwargs: source)
    with pytest.raises(IntegrityError, match="raw composite differs"):
        prepare_corrected_evaluations(
            repository_root=REPOSITORY_ROOT,
            config_path=Path(__file__).parents[1] / "configs/campaign.json",
            source_preparation_dir=source.root,
            exact_lora_manifest_path=manifest_path,
            output_dir=tmp_path / "prepared",
            manifest_validator=validator,
        )


def test_renderer_emits_only_corrected_laptop_cells(tmp_path, config, monkeypatch):
    source = _fake_source(tmp_path, config)
    manifest_path, validator = _manifest_and_validator(tmp_path, source)
    monkeypatch.setattr(corrected_eval, "_load_source_bundle", lambda **_kwargs: source)
    preparation = tmp_path / "prepared"
    prepare_corrected_evaluations(
        repository_root=REPOSITORY_ROOT,
        config_path=Path(__file__).parents[1] / "configs/campaign.json",
        source_preparation_dir=source.root,
        exact_lora_manifest_path=manifest_path,
        output_dir=preparation,
        manifest_validator=validator,
    )
    endpoint = {
        "model_spec": {
            **source.model_specs["base"],
            "name": "corrected",
            "base_url": "http://127.0.0.1:18200/v1",
            "deployment": "qwen35-browser-action-corrected-deadbeef-exact-lora",
        },
        "endpoint_binding_sha256": "4" * 64,
    }
    monkeypatch.setattr(
        corrected_eval, "audit_corrected_endpoint", lambda **_kwargs: endpoint
    )
    invalid_bundle = tmp_path / "invalid-bundle"
    with pytest.raises(IntegrityError, match="nonempty Python"):
        render_corrected_completion_bundle(
            preparation_dir=preparation,
            repository_root=REPOSITORY_ROOT,
            endpoint_binding_path=tmp_path / "endpoint.json",
            evaluation=DEVELOPMENT_EVALUATION,
            output_dir=invalid_bundle,
            results_root=tmp_path / "invalid-results",
            base_port=23900,
            python_executable="",
            manifest_validator=validator,
        )
    assert not invalid_bundle.exists()
    bundle_dir = tmp_path / "development-bundle"
    receipt = render_corrected_completion_bundle(
        preparation_dir=preparation,
        repository_root=REPOSITORY_ROOT,
        endpoint_binding_path=tmp_path / "endpoint.json",
        evaluation=DEVELOPMENT_EVALUATION,
        output_dir=bundle_dir,
        results_root=tmp_path / "new-results",
        base_port=24000,
        python_executable="python3",
        manifest_validator=validator,
    )
    launch = json.loads((bundle_dir / "launch_manifest.json").read_text())
    assert receipt["new_run_count"] == 12
    assert launch["execution_mode"] == "single_arm_completion"
    assert {row["arm"] for row in launch["launches"]} == {"corrected"}
    assert not (tmp_path / "new-results").exists()
    with pytest.raises(IntegrityError, match="requires a development gate"):
        render_corrected_completion_bundle(
            preparation_dir=preparation,
            repository_root=REPOSITORY_ROOT,
            endpoint_binding_path=tmp_path / "endpoint.json",
            evaluation=FINAL_EVALUATION,
            output_dir=tmp_path / "final-bundle",
            results_root=tmp_path / "final-results",
            base_port=25000,
            python_executable="python3",
            manifest_validator=validator,
        )


def test_endpoint_qualification_derives_selected_adapter_kind(
    tmp_path, config, monkeypatch
):
    source = _fake_source(tmp_path, config)
    manifest_path, validator = _manifest_and_validator(tmp_path, source)
    monkeypatch.setattr(corrected_eval, "_load_source_bundle", lambda **_kwargs: source)
    preparation = tmp_path / "prepared"
    prepare_corrected_evaluations(
        repository_root=REPOSITORY_ROOT,
        config_path=Path(__file__).parents[1] / "configs/campaign.json",
        source_preparation_dir=source.root,
        exact_lora_manifest_path=manifest_path,
        output_dir=preparation,
        manifest_validator=validator,
    )
    protocol = json.loads((preparation / "protocol.json").read_text())
    trained = protocol["corrected_exact_lora"]["arms"]["trained"]
    deployment = trained["served_model_name"]
    parent_name = "qwen35-exact-lora-parent"
    model_spec = {
        **source.model_specs["base"],
        "name": "corrected",
        "base_url": "http://127.0.0.1:18200/v1",
        "deployment": deployment,
    }
    server = {
        "model_path": trained["parent"],
        "served_model_name": deployment,
        "port": 8000,
        "argv": [
            "vllm",
            "serve",
            trained["parent"],
            "--tokenizer",
            protocol["corrected_exact_lora"]["tokenizer"],
            "--served-model-name",
            parent_name,
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
            f"{deployment}={trained['adapter']}",
            "--data-parallel-size",
            "4",
            "--api-server-count",
            "4",
        ],
        "config": {
            "model_path": trained["parent"],
            "served_model_name": deployment,
            "port": 8000,
            "adapter_path": trained["adapter"],
            "parent_tree_sha256": trained["parent_tree_sha256"],
            "adapter_tree_sha256": trained["adapter_tree_sha256"],
            "adapter_config_sha256": trained["adapter_config_sha256"],
            "composite_weight_sha256": trained["composite_sha256"],
            "tokenizer_json_sha256": protocol["corrected_exact_lora"][
                "tokenizer_json_sha256"
            ],
            "chat_template_sha256": protocol["corrected_exact_lora"][
                "chat_template_sha256"
            ],
            "exact_lora_manifest_sha256": protocol["corrected_exact_lora"][
                "manifest_sha256"
            ],
            "adapter_kind": "browser_action_correction_step24",
            "serving_mode": "exact_peft_lora",
            "parent_served_model_name": parent_name,
            "dtype": "bfloat16",
            "frontend_launcher": "vllm serve",
            "data_parallel_size": 4,
            "api_server_count": 4,
            "adapter_rank": 64,
            "environment": {
                "FLA_TILELANG": "0",
                "VLLM_ALLOW_RUNTIME_LORA_UPDATING": None,
            },
        },
    }
    tunnel = {
        "transport": "kubectl-port-forward",
        "listen_host": "127.0.0.1",
        "listen_port": 18200,
        "target_pod": "corrected-pod",
        "target_port": 8000,
        "argv": [
            "kubectl",
            "--context",
            "test-context",
            "--namespace",
            "eval",
            "port-forward",
            "--address",
            "127.0.0.1",
            "pod/corrected-pod",
            "18200:8000",
        ],
        "config": {
            "context": "test-context",
            "namespace": "eval",
            "listen_host": "127.0.0.1",
            "listen_port": 18200,
            "target_pod": "corrected-pod",
            "target_port": 8000,
        },
    }
    probe = {
        "http_status": 200,
        "served_models": sorted([deployment, parent_name]),
        "model_bindings": [
            {"id": deployment, "root": trained["adapter"], "parent": parent_name},
            {"id": parent_name, "root": trained["parent"], "parent": None},
        ],
        "checked_at": "2026-08-12T00:00:00Z",
        "response_sha256": "f" * 64,
    }
    records = {
        "model": model_spec,
        "server": server,
        "tunnel": tunnel,
        "probe": probe,
    }
    paths = {}
    for name, record in records.items():
        path = tmp_path / f"{name}.json"
        path.write_bytes(canonical_bytes(record) + b"\n")
        paths[name] = path
    binding = qualify_corrected_endpoint(
        preparation_dir=preparation,
        repository_root=REPOSITORY_ROOT,
        model_spec_path=paths["model"],
        server_record_path=paths["server"],
        tunnel_record_path=paths["tunnel"],
        probe_record_path=paths["probe"],
        output_path=tmp_path / "qualified.json",
        manifest_validator=validator,
    )
    assert binding["server"]["config"]["adapter_kind"] == (
        "browser_action_correction_step24"
    )

    server["config"]["adapter_kind"] = "browser_action_correction_step20"
    paths["server"].write_bytes(canonical_bytes(server) + b"\n")
    with pytest.raises(IntegrityError, match="finalized composition"):
        qualify_corrected_endpoint(
            preparation_dir=preparation,
            repository_root=REPOSITORY_ROOT,
            model_spec_path=paths["model"],
            server_record_path=paths["server"],
            tunnel_record_path=paths["tunnel"],
            probe_record_path=paths["probe"],
            output_path=tmp_path / "rejected.json",
            manifest_validator=validator,
        )


def test_live_capture_produces_manifest_bound_endpoint_bundle(
    tmp_path, config, monkeypatch
):
    source = _fake_source(tmp_path, config)
    manifest_path, validator = _manifest_and_validator(tmp_path, source)
    monkeypatch.setattr(corrected_eval, "_load_source_bundle", lambda **_kwargs: source)
    preparation = tmp_path / "prepared"
    prepare_corrected_evaluations(
        repository_root=REPOSITORY_ROOT,
        config_path=Path(__file__).parents[1] / "configs/campaign.json",
        source_preparation_dir=source.root,
        exact_lora_manifest_path=manifest_path,
        output_dir=preparation,
        manifest_validator=validator,
    )
    protocol = json.loads((preparation / "protocol.json").read_text())
    binding = protocol["corrected_exact_lora"]
    trained = binding["arms"]["trained"]
    validated = {
        key: binding[key]
        for key in (
            "manifest_path",
            "manifest_sha256",
            "manifest_receipt_path",
            "manifest_receipt_sha256",
            "campaign_digest",
            "artifact_source_git_sha",
            "selected_checkpoint",
            "continuation_receipt",
            "selection_receipt",
        )
    }
    validated.update(
        parent=trained["parent"],
        adapter=trained["adapter"],
        tokenizer=binding["tokenizer"],
        served_model_name=trained["served_model_name"],
        composite_sha256=trained["composite_sha256"],
    )
    server_argv = copy.deepcopy(
        source.endpoint_manifest["arms"]["base"]["server"]["argv"]
    )
    replacements = {
        "/models/raw": trained["parent"],
        "/models/source-tokenizer": binding["tokenizer"],
        f"{source.model_specs['base']['deployment']}=/models/zero": (
            f"{trained['served_model_name']}={trained['adapter']}"
        ),
    }
    server_argv = [replacements.get(value, value) for value in server_argv]
    payload = {
        "schema": corrected_capture.CAPTURE_SCHEMA,
        "validated": validated,
        "component_identity": {
            "parent_tree_sha256": trained["parent_tree_sha256"],
            "adapter_tree_sha256": trained["adapter_tree_sha256"],
            "adapter_config_sha256": trained["adapter_config_sha256"],
            "tokenizer_json_sha256": binding["tokenizer_json_sha256"],
            "chat_template_sha256": binding["chat_template_sha256"],
        },
        "argv": server_argv,
        "runtime": {
            "runtime_lora_updating_absent": True,
            "fla_tilelang": "0",
        },
    }

    def fake_run(command, **kwargs):
        if "get" in command:
            pod_record = {
                "metadata": {"name": "corrected-pod", "uid": "pod-uid-1"},
                "spec": {"containers": [{"name": "serving"}]},
                "status": {
                    "containerStatuses": [
                        {
                            "name": "serving",
                            "ready": True,
                            "started": True,
                            "containerID": "containerd://container-1",
                            "imageID": (
                                "registry.example/serving@"
                                + source.endpoint_manifest["arms"]["base"][
                                    "container_image_digest"
                                ]
                            ),
                        }
                    ]
                },
            }
            return subprocess.CompletedProcess(
                command, 0, json.dumps(pod_record), ""
            )
        assert command[-2:] == [str(manifest_path.resolve()), "/remote/source"]
        assert "/proc/1/cmdline" in kwargs["input"]
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr(corrected_capture.subprocess, "run", fake_run)
    parent_name = "qwen35-exact-lora-parent"
    monkeypatch.setattr(
        corrected_capture,
        "_probe_models",
        lambda **_kwargs: {
            "http_status": 200,
            "served_models": sorted([trained["served_model_name"], parent_name]),
            "model_bindings": [
                {
                    "id": trained["served_model_name"],
                    "root": trained["adapter"],
                    "parent": parent_name,
                },
                {"id": parent_name, "root": trained["parent"], "parent": None},
            ],
            "checked_at": "2026-08-12T00:00:00Z",
            "response_sha256": "f" * 64,
        },
    )
    proc = tmp_path / "proc" / "123"
    proc.mkdir(parents=True)
    tunnel_argv = [
        value.replace("raw-pod", "corrected-pod").replace("18000:8000", "18200:8000")
        for value in source.endpoint_manifest["arms"]["base"]["tunnel"]["argv"]
    ]
    (proc / "cmdline").write_bytes(
        b"\0".join(value.encode() for value in tunnel_argv) + b"\0"
    )
    stat_fields = ["S", *("0" for _ in range(18)), "123", *("0" for _ in range(5))]
    (proc / "stat").write_text(f"123 (kubectl) {' '.join(stat_fields)}\n")
    output = tmp_path / "capture"
    receipt = corrected_capture.capture_corrected_endpoint(
        preparation_dir=preparation,
        repository_root=REPOSITORY_ROOT,
        pod="corrected-pod",
        remote_manifest_path=str(manifest_path.resolve()),
        remote_source_root="/remote/source",
        base_url="http://127.0.0.1:18200/v1",
        api_key="not-persisted",
        tunnel_pid=123,
        output_dir=output,
        manifest_validator=validator,
        proc_root=tmp_path / "proc",
    )
    server = json.loads((output / "server_record.json").read_text())
    assert receipt["api_key_persisted"] is False
    assert receipt["pod_runtime"]["image_digest"] == source.endpoint_manifest[
        "arms"
    ]["base"]["container_image_digest"]
    assert server["config"]["adapter_kind"] == "browser_action_correction_step24"
    assert (output / "endpoint_binding.json").is_file()


def test_remote_manifest_validator_localizes_identical_copies(
    tmp_path, config, monkeypatch
):
    source = _fake_source(tmp_path, config)
    manifest_path, local_validator = _manifest_and_validator(tmp_path, source)
    remote_path = "/remote/correction/final/exact_lora_manifest.json"
    remote_result = local_validator(manifest_path, arm="base")
    remote_result["manifest_path"] = remote_path
    remote_result["manifest_receipt_path"] = (
        "/remote/correction/final/exact_lora_manifest_receipt.json"
    )

    def fake_run(command, **kwargs):
        assert command[-3:] == [remote_path, "/remote/source", "base"]
        assert "validate_browser_action_serving_manifest" in kwargs["input"]
        return subprocess.CompletedProcess(command, 0, json.dumps(remote_result), "")

    monkeypatch.setattr(corrected_capture.subprocess, "run", fake_run)
    validator = corrected_capture.remote_manifest_validator(
        source_preparation_dir=source.root,
        pod="corrected-pod",
        local_manifest_path=manifest_path,
        remote_manifest_path=remote_path,
        remote_source_root="/remote/source",
    )
    localized = validator(manifest_path, arm="base")
    assert localized["manifest_path"] == str(manifest_path.resolve())
    assert localized["manifest_receipt_path"] == str(
        (manifest_path.parent / "exact_lora_manifest_receipt.json").resolve()
    )


def _write_conversion(
    *,
    launch_path: Path,
    frozen_path: Path,
    observations_path: Path,
    audit_path: Path,
    bind_backstop: bool,
) -> None:
    launch = json.loads(launch_path.read_text())
    frozen = json.loads(frozen_path.read_text())
    inference_sha = sha256_bytes(canonical_bytes(frozen["inference_contract"]))
    rows = []
    classifications = []
    for index, item in enumerate(launch["launches"]):
        spec = json.loads(Path(item["config"]).read_text())
        combined = spec["condition"] == "combined"
        hero = 0.0 if combined and item["arm"] in {"trained", "step20"} else 1.0
        weight = (
            frozen["model_contract"]["base_weight_sha256"]
            if item["arm"] == "base"
            else frozen["model_contract"]["trained_weight_sha256"]
        )
        rows.append(
            {
                "run_id": item["run_id"],
                "arm": item["arm"],
                "preservation_strict": hero,
                "strict_binary": hero,
                "valid_transaction": True,
                "compiler_pass": True,
                "infrastructure_valid": True,
                "audit": {
                    "frozen_manifest_sha256": frozen["manifest_sha256"],
                    "harness_sha256": frozen["source_contract"]["harness_sha256"],
                    "inference_contract_sha256": inference_sha,
                    "matrix_sha256": launch["matrix_sha256"],
                    "endpoint_manifest_sha256": launch["endpoint_manifest_sha256"],
                    "launch_manifest_sha256": launch["launch_manifest_sha256"],
                    "launch_config_sha256": item["config_sha256"],
                    "summary_sha256": "6" * 64,
                    "trajectory_sha256": "7" * 64,
                    "arm_weight_sha256": weight,
                    "recorded_scores_match_fresh": True,
                    "safety_backstop_bound": bind_backstop and index == 0,
                    "lossy_context_bound": False,
                    "runtime_drift": False,
                },
            }
        )
        classifications.append(
            {
                "run_id": item["run_id"],
                "infrastructure_valid": True,
                "classification": "behavioral_purchase",
            }
        )
    observations_path.write_text(
        "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in rows
        )
    )
    core = {
        "campaign_id": launch["campaign_id"],
        "launch_manifest_sha256": launch["launch_manifest_sha256"],
        "frozen_manifest_sha256": frozen["manifest_sha256"],
        "matrix_sha256": launch["matrix_sha256"],
        "endpoint_manifest_sha256": launch["endpoint_manifest_sha256"],
        "observations_sha256": sha256_file(observations_path),
        "expected_runs": len(rows),
        "observation_rows": len(rows),
        "infrastructure_invalid_runs": 0,
        "complete": True,
        "runs": classifications,
    }
    audit_path.write_bytes(
        canonical_bytes(
            {
                **core,
                "conversion_audit_sha256": sha256_bytes(canonical_bytes(core)),
            }
        )
        + b"\n"
    )


def test_development_report_uses_only_preregistered_binary_gates(
    tmp_path, config, monkeypatch
):
    source = _fake_source(tmp_path, config)
    manifest_path, validator = _manifest_and_validator(tmp_path, source)
    monkeypatch.setattr(corrected_eval, "_load_source_bundle", lambda **_kwargs: source)
    preparation = tmp_path / "prepared"
    prepare_corrected_evaluations(
        repository_root=REPOSITORY_ROOT,
        config_path=Path(__file__).parents[1] / "configs/campaign.json",
        source_preparation_dir=source.root,
        exact_lora_manifest_path=manifest_path,
        output_dir=preparation,
        manifest_validator=validator,
    )
    endpoint = {
        "model_spec": {
            **source.model_specs["base"],
            "name": "corrected",
            "base_url": "http://127.0.0.1:18200/v1",
            "deployment": "qwen35-browser-action-corrected-deadbeef-exact-lora",
        },
        "endpoint_binding_sha256": "4" * 64,
    }
    monkeypatch.setattr(
        corrected_eval, "audit_corrected_endpoint", lambda **_kwargs: endpoint
    )
    bundle = tmp_path / "development-bundle"
    render_corrected_completion_bundle(
        preparation_dir=preparation,
        repository_root=REPOSITORY_ROOT,
        endpoint_binding_path=tmp_path / "endpoint.json",
        evaluation=DEVELOPMENT_EVALUATION,
        output_dir=bundle,
        results_root=tmp_path / "new-results",
        base_port=24000,
        python_executable="python3",
        manifest_validator=validator,
    )
    reuse_observations = tmp_path / "reuse.jsonl"
    reuse_audit = tmp_path / "reuse-audit.json"
    corrected_observations = tmp_path / "corrected.jsonl"
    corrected_audit = tmp_path / "corrected-audit.json"
    _write_conversion(
        launch_path=preparation / "development/reuse_launch_manifest.json",
        frozen_path=source.root / "frozen_manifest.json",
        observations_path=reuse_observations,
        audit_path=reuse_audit,
        bind_backstop=False,
    )
    _write_conversion(
        launch_path=bundle / "launch_manifest.json",
        frozen_path=bundle / "frozen_manifest.json",
        observations_path=corrected_observations,
        audit_path=corrected_audit,
        bind_backstop=False,
    )
    observations = tmp_path / "gate.jsonl"
    binding = tmp_path / "binding.json"
    bind_evaluation_observations(
        preparation_dir=preparation,
        repository_root=REPOSITORY_ROOT,
        evaluation=DEVELOPMENT_EVALUATION,
        reuse_observations_path=reuse_observations,
        reuse_conversion_audit_path=reuse_audit,
        corrected_bundle_dir=bundle,
        corrected_observations_path=corrected_observations,
        corrected_conversion_audit_path=corrected_audit,
        output_path=observations,
        receipt_path=binding,
        manifest_validator=validator,
    )
    report = analyze_development_gate(
        preparation_dir=preparation,
        repository_root=REPOSITORY_ROOT,
        observations_path=observations,
        binding_receipt_path=binding,
        manifest_validator=validator,
    )
    assert report["selected"] is True
    assert report["rates"]["combined_binary_hero_delta"] == 1.0
    assert report["rates"]["clean_binary_hero_regression"] == 0.0
    assert report["positive_variants"] == 4
    assert report["selection_inputs"]["heldout_rows"] == 0
    report_path = tmp_path / "gate-report.json"
    report_path.write_bytes(canonical_bytes(report) + b"\n")
    final_bundle = tmp_path / "final-bundle"
    final_receipt = render_corrected_completion_bundle(
        preparation_dir=preparation,
        repository_root=REPOSITORY_ROOT,
        endpoint_binding_path=tmp_path / "endpoint.json",
        evaluation=FINAL_EVALUATION,
        output_dir=final_bundle,
        results_root=tmp_path / "final-results",
        base_port=25000,
        python_executable="python3",
        gate_report_path=report_path,
        manifest_validator=validator,
    )
    final_launch = json.loads((final_bundle / "launch_manifest.json").read_text())
    assert final_receipt["new_run_count"] == 160
    assert {item["arm"] for item in final_launch["launches"]} == {"trained"}

    forged = copy.deepcopy(report)
    forged["gates"] = {"invented": True}
    forged_core = {
        key: value for key, value in forged.items() if key != "report_sha256"
    }
    forged["report_sha256"] = sha256_bytes(canonical_bytes(forged_core))
    forged_path = tmp_path / "forged-report.json"
    forged_path.write_bytes(canonical_bytes(forged) + b"\n")
    with pytest.raises(IntegrityError, match="preregistered gate"):
        render_corrected_completion_bundle(
            preparation_dir=preparation,
            repository_root=REPOSITORY_ROOT,
            endpoint_binding_path=tmp_path / "endpoint.json",
            evaluation=FINAL_EVALUATION,
            output_dir=tmp_path / "forged-final",
            results_root=tmp_path / "forged-results",
            base_port=25000,
            python_executable="python3",
            gate_report_path=forged_path,
            manifest_validator=validator,
        )
