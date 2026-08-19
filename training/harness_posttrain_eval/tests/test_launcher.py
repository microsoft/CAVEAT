from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from harness_posttrain_eval.common import IntegrityError, canonical_bytes, sha256_bytes
from harness_posttrain_eval.launcher import (
    exact_lora_composite_sha256,
    render_run_bundle,
    verify_endpoint_manifest,
)
from harness_posttrain_eval.matrix import diagnostic_matrix, final_matrix


def _tunnel(*, arm: str, listen_port: int, target_port: int = 8000):
    pod = f"{arm}-pod"
    host = "127.0.0.1"
    return {
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


def test_diagnostic_and_final_matrices_render_to_executable_configs(
    config, frozen_manifest, tmp_path
):
    model_specs = {
        "base": {
            "name": "base",
            "provider": "openai",
            "base_url": "http://127.0.0.1:18000/v1",
            "api_key": "env:KEY",
            "deployment": "base",
        },
        "trained": {
            "name": "trained",
            "provider": "openai",
            "base_url": "http://127.0.0.1:18100/v1",
            "api_key": "env:KEY",
            "deployment": "trained",
        },
        "base_trapi_diagnostic": "Qwen/Qwen3.5-27B",
    }
    complete_final = final_matrix(config)
    inference_sha = sha256_bytes(canonical_bytes(frozen_manifest["inference_contract"]))
    endpoint_arms = {}
    for index, arm in enumerate(("base", "trained")):
        model_path = f"/models/{arm}"
        deployment = model_specs[arm]["deployment"]
        listen_port = 18000 + index * 100
        server_port = 8000
        endpoint_arms[arm] = {
            "name": model_specs[arm]["name"],
            "base_url": model_specs[arm]["base_url"],
            "deployment": deployment,
            "weight_sha256": frozen_manifest["model_contract"][
                f"{'base' if arm == 'base' else 'trained'}_weight_sha256"
            ],
            "container_image_digest": frozen_manifest["inference_contract"][
                "container_image_digest"
            ],
            "model_spec_sha256": sha256_bytes(canonical_bytes(model_specs[arm])),
            "server": {
                "model_path": model_path,
                "served_model_name": deployment,
                "port": server_port,
                "argv": [
                    "vllm",
                    "serve",
                    model_path,
                    "--served-model-name",
                    deployment,
                    "--port",
                    str(server_port),
                    "--dtype",
                    "bfloat16",
                ],
                "config": {
                    "model_path": model_path,
                    "served_model_name": deployment,
                    "port": server_port,
                    "dtype": "bfloat16",
                },
            },
            "tunnel": _tunnel(arm=arm, listen_port=listen_port),
            "ready_probe": {
                "http_status": 200,
                "served_models": [deployment],
                "model_bindings": [
                    {"id": deployment, "root": model_path, "parent": None}
                ],
                "checked_at": "2026-08-12T00:00:00Z",
                "response_sha256": "e" * 64,
            },
        }
    endpoint_core = {
        "schema_version": 2,
        "frozen_manifest_sha256": frozen_manifest["manifest_sha256"],
        "inference_contract_sha256": inference_sha,
        "arms": endpoint_arms,
    }
    endpoint_manifest = {
        **endpoint_core,
        "endpoint_manifest_sha256": sha256_bytes(canonical_bytes(endpoint_core)),
    }
    final_sample = copy.deepcopy(complete_final)
    sampled_pair = complete_final["runs"][0]["pair_id"]
    final_sample["runs"] = [
        row for row in complete_final["runs"] if row["pair_id"] == sampled_pair
    ]
    final_core = {
        key: value for key, value in final_sample.items() if key != "matrix_sha256"
    }
    final_sample["matrix_sha256"] = sha256_bytes(canonical_bytes(final_core))
    for name, matrix, base_port in (
        ("diagnostic", diagnostic_matrix(config), 20000),
        ("final", final_sample, 21000),
    ):
        bundle = render_run_bundle(
            matrix=matrix,
            config=config,
            frozen_manifest=frozen_manifest,
            endpoint_manifest=endpoint_manifest if matrix["kind"] == "final" else None,
            model_specs=model_specs,
            output_dir=tmp_path / name,
            results_root=tmp_path / "results",
            base_port=base_port,
        )
        assert len(bundle["launches"]) == len(matrix["runs"])
        assert len({entry["results"] for entry in bundle["launches"]}) == len(
            matrix["runs"]
        )
        first = bundle["launches"][0]
        run_config = json.loads(Path(first["config"]).read_text(encoding="utf-8"))
        assert run_config["task"]["task_id"] in {
            f"{scenario}-{variant}"
            for scenario in ("laptop", "office_chair", "mattress", "backpack", "tent")
            for variant in config["amazon"]["variants"]
        }
        assert first["argv"][1:3] == ["-m", "harness_posttrain_eval.launch_one"]
        assert run_config["max_steps"] == 4000
        assert run_config["task"]["catalog"] in {
            "laptop", "office_chair", "mattress", "backpack", "tent"
        }
        expected_environment = {
            key: value
            for key, value in first["environment"].items()
        }
        assert expected_environment["AGENTARENA_CELL_TIMEOUT"] == "36000"
        assert expected_environment["AGENTARENA_LLM_TIMEOUT"] == "7200"
        assert expected_environment["AGENTARENA_MAX_FAILURES"] == "1000"
        assert expected_environment["AGENTARENA_NO_VISION"] == "1"
        assert expected_environment["AGENTARENA_NO_SHOT_PERSIST"] == "1"
        assert expected_environment["BROWSER_USE_ACTION_TIMEOUT_S"] == "7500"
        assert expected_environment["AGENTARENA_CACHE_NONCE"].endswith(
            run_config["run_id"]
        )
        assert "AGENTARENA_LIMIT_CONTRACT_JSON" in expected_environment
        limit_contract = json.loads(
            expected_environment["AGENTARENA_LIMIT_CONTRACT_JSON"]
        )
        assert "structured_response_attempts" not in limit_contract["categories"][
            "safety_backstops"
        ]
        assert limit_contract["categories"]["fixed_architecture"][
            "structured_response_attempts"
        ]["configured"] == 4
        assert first["environment"] == expected_environment
        assert run_config["runtime_environment"] == expected_environment
        with pytest.raises(IntegrityError, match="refusing to overwrite"):
            render_run_bundle(
                matrix=matrix,
                config=config,
                frozen_manifest=frozen_manifest,
                endpoint_manifest=endpoint_manifest if matrix["kind"] == "final" else None,
                model_specs=model_specs,
                output_dir=tmp_path / name,
                results_root=tmp_path / "results",
                base_port=base_port,
            )


def test_bound_harness_hashes_match_current_source(config):
    assert config["expected_harness_file_sha256"] == {
        "agentarena/scaffolds/browseruse.py":
        "1db5dad9bf8950aca2602c0ef5c33673f415ad1662c5c8e5de853b338082d52e",
        "agentarena/scaffolds/browseruse_deliberative.py":
        "6336e052bd94617dabea9ea0a7fb4b3c23923728b90f09abab4040d5146ad3ed",
        "agentarena/scaffolds/_deliberative_core.py":
        "9800f3a408c5c966b5bd01b3453ea7ab60e41c80b7420531aa1cf68211885cbb",
    }


def test_qwen_inference_contract_matches_production_non_reasoning_path(config):
    assert config["metrics"] == {
        "headline": {
            "name": "optimal_product_selection_rate",
            "source": "strict_binary",
        },
        "mandatory_secondary": {
            "name": "preservation_strict",
            "source": "preservation_strict",
        },
    }
    assert config["inference"]["qwen_is_reasoning_model"] is False
    assert config["inference"]["qwen_thinking_mode"] == "tokenizer_native"
    assert config["inference"]["use_vision"] is False
    assert config["inference"]["request_parameters"] == {
        "temperature": 0.0,
        "frequency_penalty": None,
        "max_completion_tokens": None,
        "dont_force_structured_output": True,
        "add_schema_to_system_prompt": True,
        "max_retries": 16,
        "http_timeout_seconds": 7260,
    }


def _endpoint_fixture(frozen_manifest):
    model_specs = {}
    arms = {}
    for index, arm in enumerate(("base", "trained")):
        deployment = f"qwen-{arm}"
        model_path = f"/models/{arm}"
        listen_port = 18000 + index * 100
        server_port = 8000
        spec = {
            "name": f"Qwen {arm}",
            "provider": "openai",
            "base_url": f"http://127.0.0.1:{listen_port}/v1",
            "api_key": "env:HARNESS_POSTTRAIN_API_KEY",
            "deployment": deployment,
            "extra": {"reasoning_effort": "low", "temperature": 0.0},
        }
        model_specs[arm] = spec
        arms[arm] = {
            "name": spec["name"],
            "base_url": spec["base_url"],
            "deployment": deployment,
            "weight_sha256": frozen_manifest["model_contract"][
                "base_weight_sha256" if arm == "base" else "trained_weight_sha256"
            ],
            "container_image_digest": frozen_manifest["inference_contract"][
                "container_image_digest"
            ],
            "model_spec_sha256": sha256_bytes(canonical_bytes(spec)),
            "server": {
                "model_path": model_path,
                "served_model_name": deployment,
                "port": server_port,
                "argv": [
                    "vllm",
                    "serve",
                    model_path,
                    "--served-model-name",
                    deployment,
                    "--port",
                    str(server_port),
                    "--dtype",
                    "bfloat16",
                ],
                "config": {
                    "model_path": model_path,
                    "served_model_name": deployment,
                    "port": server_port,
                    "dtype": "bfloat16",
                },
            },
            "tunnel": _tunnel(arm=arm, listen_port=listen_port),
            "ready_probe": {
                "http_status": 200,
                "served_models": [deployment],
                "model_bindings": [
                    {"id": deployment, "root": model_path, "parent": None}
                ],
                "checked_at": "2026-08-12T00:00:00Z",
                "response_sha256": "e" * 64,
            },
        }
    core = {
        "schema_version": 2,
        "frozen_manifest_sha256": frozen_manifest["manifest_sha256"],
        "inference_contract_sha256": sha256_bytes(
            canonical_bytes(frozen_manifest["inference_contract"])
        ),
        "arms": arms,
    }
    return model_specs, {
        **core,
        "endpoint_manifest_sha256": sha256_bytes(canonical_bytes(core)),
    }


def _rehash_endpoint(endpoint_manifest):
    core = {
        key: value
        for key, value in endpoint_manifest.items()
        if key != "endpoint_manifest_sha256"
    }
    endpoint_manifest["endpoint_manifest_sha256"] = sha256_bytes(canonical_bytes(core))


def _rehash_frozen(frozen_manifest):
    core = {
        key: value
        for key, value in frozen_manifest.items()
        if key != "manifest_sha256"
    }
    frozen_manifest["manifest_sha256"] = sha256_bytes(canonical_bytes(core))


def _exact_lora_endpoint_fixture(frozen_manifest):
    frozen_manifest = copy.deepcopy(frozen_manifest)
    frozen_manifest["model_contract"]["weight_identity_schema"] = (
        "harness-posttrain.exact-lora-composite.v1"
    )
    model_specs = {}
    arms = {}
    frozen_arms = {}
    manifest_sha = "9" * 64
    for index, arm in enumerate(("base", "trained")):
        deployment = f"qwen-{arm}"
        parent_path = f"/models/{arm}-parent"
        parent_name = "qwen-private-parent"
        adapter_path = f"/models/{arm}-adapter"
        parent_sha = ("1" if arm == "base" else "2") * 64
        adapter_sha = ("3" if arm == "base" else "4") * 64
        adapter_config_sha = ("5" if arm == "base" else "6") * 64
        tokenizer_sha = "7" * 64
        template_sha = "8" * 64
        composite_sha = exact_lora_composite_sha256(
            parent_tree_sha256=parent_sha,
            adapter_tree_sha256=adapter_sha,
            adapter_config_sha256=adapter_config_sha,
            tokenizer_json_sha256=tokenizer_sha,
            chat_template_sha256=template_sha,
            dtype="bfloat16",
        )
        frozen_manifest["model_contract"][
            "base_weight_sha256" if arm == "base" else "trained_weight_sha256"
        ] = composite_sha
        spec = {
            "name": f"Qwen {arm}",
            "provider": "openai",
            "base_url": f"http://127.0.0.1:{18000 + index * 100}/v1",
            "api_key": "env:HARNESS_POSTTRAIN_API_KEY",
            "deployment": deployment,
            "extra": {"reasoning_effort": "low", "temperature": 0.0},
        }
        model_specs[arm] = spec
        server_config = {
            "model_path": parent_path,
            "served_model_name": deployment,
            "port": 8000,
            "dtype": "bfloat16",
            "serving_mode": "exact_peft_lora",
            "frontend_launcher": "vllm serve",
            "data_parallel_size": 4,
            "api_server_count": 4,
            "parent_served_model_name": parent_name,
            "adapter_path": adapter_path,
            "adapter_rank": 64,
            "parent_tree_sha256": parent_sha,
            "adapter_tree_sha256": adapter_sha,
            "adapter_config_sha256": adapter_config_sha,
            "tokenizer_json_sha256": tokenizer_sha,
            "chat_template_sha256": template_sha,
            "composite_weight_sha256": composite_sha,
            "exact_lora_manifest_sha256": manifest_sha,
            "adapter_kind": (
                "zero_control" if arm == "base" else "refinement_step20"
            ),
            "environment": {"VLLM_ALLOW_RUNTIME_LORA_UPDATING": None},
        }
        arms[arm] = {
            "name": spec["name"],
            "base_url": spec["base_url"],
            "deployment": deployment,
            "weight_sha256": composite_sha,
            "container_image_digest": frozen_manifest["inference_contract"][
                "container_image_digest"
            ],
            "model_spec_sha256": sha256_bytes(canonical_bytes(spec)),
            "server": {
                "model_path": parent_path,
                "served_model_name": deployment,
                "port": 8000,
                "argv": [
                    "python3",
                    "/opt/vllm/bin/vllm",
                    "serve",
                    parent_path,
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
                    f"{deployment}={adapter_path}",
                    "--data-parallel-size",
                    "4",
                    "--api-server-count",
                    "4",
                ],
                "config": server_config,
            },
            "tunnel": _tunnel(
                arm=arm, listen_port=18000 + index * 100, target_port=8000
            ),
            "ready_probe": {
                "http_status": 200,
                "served_models": [deployment],
                "model_bindings": [
                    {
                        "id": deployment,
                        "root": adapter_path,
                        "parent": parent_name,
                    },
                    {
                        "id": parent_name,
                        "root": parent_path,
                        "parent": None,
                    },
                ],
                "checked_at": "2026-08-12T00:00:00Z",
                "response_sha256": "e" * 64,
            },
        }
        frozen_arms[arm] = {
            "adapter_kind": server_config["adapter_kind"],
            "parent_path": parent_path,
            "parent_tree_sha256": parent_sha,
            "adapter_path": adapter_path,
            "adapter_tree_sha256": adapter_sha,
            "adapter_config_sha256": adapter_config_sha,
            "composite_weight_sha256": composite_sha,
        }
    pair_sha = sha256_bytes(
        canonical_bytes(
            {
                "schema": "harness-posttrain.exact-lora-inference.v1",
                "base": frozen_arms["base"]["composite_weight_sha256"],
                "trained": frozen_arms["trained"]["composite_weight_sha256"],
            }
        )
    )
    frozen_manifest["inference_contract"]["serving_stack"] = {
        "weight_identity_schema": "harness-posttrain.exact-lora-composite.v1",
        "exact_lora_contract": {
            "schema": "harness-posttrain-eval.exact-lora-contract.v1",
            "serving_mode": "exact_peft_lora",
            "manifest_sha256": manifest_sha,
            "manifest_receipt_sha256": "a" * 64,
            "composite_pair_sha256": pair_sha,
            "refinement_receipt_sha256": "b" * 64,
            "refinement_update": 20,
            "shared_tokenizer": {
                "path": "/models/shared-tokenizer",
                "tokenizer_json_sha256": "7" * 64,
                "chat_template_sha256": "8" * 64,
            },
            "zero_control": {
                "all_zero": True,
                "nonzero_elements": 0,
                "exact_logits_equal": True,
                "maximum_absolute_logit_difference": 0.0,
            },
            "trained_nonnoop": {
                "all_zero": False,
                "maximum_absolute_logit_difference": 0.25,
                "relative_l2_difference": 0.01,
            },
            "arms": frozen_arms,
        },
    }
    _rehash_frozen(frozen_manifest)
    core = {
        "schema_version": 3,
        "frozen_manifest_sha256": frozen_manifest["manifest_sha256"],
        "inference_contract_sha256": sha256_bytes(
            canonical_bytes(frozen_manifest["inference_contract"])
        ),
        "arms": arms,
    }
    return frozen_manifest, model_specs, {
        **core,
        "endpoint_manifest_sha256": sha256_bytes(canonical_bytes(core)),
    }


def test_exact_lora_composite_fixed_vector_matches_training_contract():
    assert exact_lora_composite_sha256(
        parent_tree_sha256="1" * 64,
        adapter_tree_sha256="2" * 64,
        adapter_config_sha256="3" * 64,
        tokenizer_json_sha256="4" * 64,
        chat_template_sha256="5" * 64,
        dtype="bfloat16",
    ) == "c1346da9574a5d0a2e5e444f460588cb36f47048c866e96c184ef6d7c3acc85d"


def test_endpoint_contract_accepts_symmetric_exact_lora(frozen_manifest):
    frozen_manifest, model_specs, endpoint_manifest = _exact_lora_endpoint_fixture(
        frozen_manifest
    )
    verify_endpoint_manifest(
        endpoint_manifest=endpoint_manifest,
        frozen_manifest=frozen_manifest,
        model_specs=model_specs,
        arms={"base", "trained"},
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("schema", "require endpoint schema 3"),
        ("asymmetric_mode", "must both use exact LoRA"),
        ("same_adapter", "composition differs from the frozen finalizer contract"),
        ("wrong_composite", "composition differs from the frozen finalizer contract"),
        ("missing_flag", "must contain exactly one --enable-lora"),
        ("second_adapter", "must bind exactly one public adapter"),
        ("legacy_launcher", "supported vllm serve launcher"),
        ("missing_api_frontends", "--api-server-count"),
        ("runtime_updates", "runtime adapter mutation must be explicitly disabled"),
        ("wrong_live_root", "live public model is not the frozen exact-LoRA adapter"),
    ),
)
def test_endpoint_contract_rejects_invalid_exact_lora(
    frozen_manifest, mutation, message
):
    frozen_manifest, model_specs, endpoint_manifest = _exact_lora_endpoint_fixture(
        frozen_manifest
    )
    base = endpoint_manifest["arms"]["base"]
    trained = endpoint_manifest["arms"]["trained"]
    if mutation == "schema":
        endpoint_manifest["schema_version"] = 2
    elif mutation == "asymmetric_mode":
        del trained["server"]["config"]["serving_mode"]
    elif mutation == "same_adapter":
        trained_path = trained["server"]["config"]["adapter_path"]
        base_path = base["server"]["config"]["adapter_path"]
        trained["server"]["config"]["adapter_path"] = base_path
        binding = f"{trained['deployment']}={trained_path}"
        trained["server"]["argv"][trained["server"]["argv"].index(binding)] = (
            f"{trained['deployment']}={base_path}"
        )
        trained["ready_probe"]["model_bindings"][0]["root"] = base_path
    elif mutation == "wrong_composite":
        trained["server"]["config"]["composite_weight_sha256"] = "9" * 64
    elif mutation == "missing_flag":
        trained["server"]["argv"].remove("--enable-lora")
    elif mutation == "second_adapter":
        binding_index = trained["server"]["argv"].index(
            f"{trained['deployment']}={trained['server']['config']['adapter_path']}"
        )
        trained["server"]["argv"].insert(
            binding_index + 1, "rogue=/models/rogue-adapter"
        )
    elif mutation == "legacy_launcher":
        argv = trained["server"]["argv"]
        model_path = trained["server"]["model_path"]
        model_index = argv.index(model_path)
        trained["server"]["argv"] = [
            argv[0],
            "-m",
            "vllm.entrypoints.openai.api_server",
            "--model",
            model_path,
            *argv[model_index + 1 :],
        ]
    elif mutation == "missing_api_frontends":
        argv = trained["server"]["argv"]
        option_index = argv.index("--api-server-count")
        del argv[option_index : option_index + 2]
    elif mutation == "runtime_updates":
        trained["server"]["config"]["environment"][
            "VLLM_ALLOW_RUNTIME_LORA_UPDATING"
        ] = "1"
    elif mutation == "wrong_live_root":
        trained["ready_probe"]["model_bindings"][0]["root"] = "/models/rogue"
    _rehash_endpoint(endpoint_manifest)
    with pytest.raises(IntegrityError, match=message):
        verify_endpoint_manifest(
            endpoint_manifest=endpoint_manifest,
            frozen_manifest=frozen_manifest,
            model_specs=model_specs,
            arms={"base", "trained"},
        )


def test_endpoint_contract_rejects_model_behavior_drift(frozen_manifest):
    model_specs, endpoint_manifest = _endpoint_fixture(frozen_manifest)
    verify_endpoint_manifest(
        endpoint_manifest=endpoint_manifest,
        frozen_manifest=frozen_manifest,
        model_specs=model_specs,
        arms={"base", "trained"},
    )
    model_specs["trained"]["extra"]["reasoning_effort"] = "high"
    endpoint_manifest["arms"]["trained"]["model_spec_sha256"] = sha256_bytes(
        canonical_bytes(model_specs["trained"])
    )
    _rehash_endpoint(endpoint_manifest)
    with pytest.raises(IntegrityError, match="model specs differ outside"):
        verify_endpoint_manifest(
            endpoint_manifest=endpoint_manifest,
            frozen_manifest=frozen_manifest,
            model_specs=model_specs,
            arms={"base", "trained"},
        )


def test_endpoint_contract_rejects_server_behavior_drift(frozen_manifest):
    model_specs, endpoint_manifest = _endpoint_fixture(frozen_manifest)
    endpoint_manifest["arms"]["trained"]["server"]["config"]["dtype"] = "float16"
    _rehash_endpoint(endpoint_manifest)
    with pytest.raises(IntegrityError, match="server argv/config differ"):
        verify_endpoint_manifest(
            endpoint_manifest=endpoint_manifest,
            frozen_manifest=frozen_manifest,
            model_specs=model_specs,
            arms={"base", "trained"},
        )


def test_endpoint_contract_accepts_separate_server_and_forward_ports(frozen_manifest):
    model_specs, endpoint_manifest = _endpoint_fixture(frozen_manifest)
    assert endpoint_manifest["arms"]["base"]["server"]["port"] == 8000
    assert endpoint_manifest["arms"]["base"]["tunnel"]["listen_port"] == 18000
    assert endpoint_manifest["arms"]["trained"]["tunnel"]["listen_port"] == 18100
    verify_endpoint_manifest(
        endpoint_manifest=endpoint_manifest,
        frozen_manifest=frozen_manifest,
        model_specs=model_specs,
        arms={"base", "trained"},
    )


def test_endpoint_contract_rejects_tunnel_target_not_bound_to_server(frozen_manifest):
    model_specs, endpoint_manifest = _endpoint_fixture(frozen_manifest)
    endpoint_manifest["arms"]["trained"]["tunnel"] = _tunnel(
        arm="trained", listen_port=18100, target_port=8001
    )
    _rehash_endpoint(endpoint_manifest)
    with pytest.raises(IntegrityError, match="target port differs"):
        verify_endpoint_manifest(
            endpoint_manifest=endpoint_manifest,
            frozen_manifest=frozen_manifest,
            model_specs=model_specs,
            arms={"base", "trained"},
        )


def test_endpoint_contract_rejects_asymmetric_tunnel_configuration(frozen_manifest):
    model_specs, endpoint_manifest = _endpoint_fixture(frozen_manifest)
    endpoint_manifest["arms"]["trained"]["tunnel"]["config"][
        "namespace"
    ] = "other"
    _rehash_endpoint(endpoint_manifest)
    with pytest.raises(IntegrityError, match="tunnel argv/config differ"):
        verify_endpoint_manifest(
            endpoint_manifest=endpoint_manifest,
            frozen_manifest=frozen_manifest,
            model_specs=model_specs,
            arms={"base", "trained"},
        )


def test_endpoint_contract_rejects_asymmetric_actual_server_ports(frozen_manifest):
    model_specs, endpoint_manifest = _endpoint_fixture(frozen_manifest)
    trained = endpoint_manifest["arms"]["trained"]
    trained["server"]["port"] = 8001
    trained["server"]["argv"][trained["server"]["argv"].index("8000")] = "8001"
    trained["server"]["config"]["port"] = 8001
    trained["tunnel"] = _tunnel(
        arm="trained", listen_port=18100, target_port=8001
    )
    _rehash_endpoint(endpoint_manifest)
    with pytest.raises(IntegrityError, match="server argv/config differ"):
        verify_endpoint_manifest(
            endpoint_manifest=endpoint_manifest,
            frozen_manifest=frozen_manifest,
            model_specs=model_specs,
            arms={"base", "trained"},
        )


def test_endpoint_contract_rejects_same_target_pod(frozen_manifest):
    model_specs, endpoint_manifest = _endpoint_fixture(frozen_manifest)
    endpoint_manifest["arms"]["trained"]["tunnel"] = _tunnel(
        arm="base", listen_port=18100
    )
    _rehash_endpoint(endpoint_manifest)
    with pytest.raises(IntegrityError, match="do not target distinct pods"):
        verify_endpoint_manifest(
            endpoint_manifest=endpoint_manifest,
            frozen_manifest=frozen_manifest,
            model_specs=model_specs,
            arms={"base", "trained"},
        )


def test_endpoint_contract_does_not_mask_behavior_value_equal_to_a_port(
    frozen_manifest,
):
    model_specs, endpoint_manifest = _endpoint_fixture(frozen_manifest)
    endpoint_manifest["arms"]["base"]["server"]["argv"].extend(
        ["--max-model-len", "8000"]
    )
    endpoint_manifest["arms"]["trained"]["server"]["argv"].extend(
        ["--max-model-len", "8001"]
    )
    endpoint_manifest["arms"]["base"]["server"]["config"]["max_model_len"] = 8000
    endpoint_manifest["arms"]["trained"]["server"]["config"]["max_model_len"] = 8001
    _rehash_endpoint(endpoint_manifest)
    with pytest.raises(IntegrityError, match="server argv/config differ"):
        verify_endpoint_manifest(
            endpoint_manifest=endpoint_manifest,
            frozen_manifest=frozen_manifest,
            model_specs=model_specs,
            arms={"base", "trained"},
        )
