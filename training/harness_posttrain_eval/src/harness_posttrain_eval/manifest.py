from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import (
    IntegrityError,
    canonical_bytes,
    hash_tree,
    read_json,
    sha256_bytes,
    sha256_file,
    write_json_create_only,
)

HARNESS_FILES = (
    "agentarena/scaffolds/browseruse_deliberative.py",
    "agentarena/scaffolds/_deliberative_core.py",
    "agentarena/scaffolds/browseruse.py",
)
SCIENTIFIC_FILES = (
    "agentarena/benchmark/__init__.py",
    "agentarena/benchmark/build.py",
    "agentarena/benchmark/copy_gen.py",
    "agentarena/benchmark/faithfulness.py",
    "agentarena/benchmark/instruction_gen.py",
    "agentarena/benchmark/pool.py",
    "agentarena/benchmark/preferences.py",
    "agentarena/benchmark/registry.py",
    "agentarena/benchmark/run.py",
    "agentarena/benchmark/scenarios.py",
    "agentarena/benchmark/schema.py",
    "agentarena/benchmark/serialize.py",
    "agentarena/benchmark/steering.py",
    "agentarena/benchmark/validate.py",
    "agentarena/core/environment.py",
    "agentarena/core/experiment.py",
    "agentarena/core/models.py",
    "agentarena/core/registry.py",
    "agentarena/core/scaffold.py",
    "agentarena/core/task.py",
    "agentarena/core/trajectory.py",
    "agentarena/envs/_storefront/counting.py",
    "agentarena/envs/_storefront/gate.py",
    "agentarena/envs/_storefront/placement.py",
    "agentarena/envs/_storefront/scoring.py",
    "agentarena/envs/__init__.py",
    "agentarena/envs/amazon/__init__.py",
    "agentarena/envs/amazon/catalog.py",
    "agentarena/envs/amazon/tasks.py",
    "agentarena/envs/amazon/server/backend/adversarial.py",
    "agentarena/envs/amazon/server/backend/app.py",
    "agentarena/envs/amazon/server/backend/counting.py",
    "agentarena/envs/amazon/server/backend/database.py",
    "agentarena/envs/amazon/server/backend/experiment_laptops.py",
    "agentarena/envs/amazon/server/backend/models.py",
    "agentarena/envs/amazon/server/backend/routes.py",
    "agentarena/envs/amazon/server/backend/seed.py",
    "agentarena/envs/amazon/server/backend/ssr.py",
    "agentarena/envs/amazon/server/backend/truthful.py",
    "agentarena/llm_client.py",
    "agentarena/run_cell.py",
    "agentarena/scaffolds/__init__.py",
    "agentarena/scaffolds/_browser.py",
    "agentarena/scoring/basket.py",
    "agentarena/scoring/continuous.py",
    "agentarena/scoring/rescore.py",
    "scripts/hard_campaign_runtime.py",
)
ORIGINAL_SCENARIOS = ("laptop", "office_chair", "mattress", "backpack", "tent")
SCENARIO_DATA_FILES = (
    "adversarial.json",
    "attribute_schema.json",
    "catalog.json",
    "instructions.json",
    "meta.json",
    "pool.json",
    "preferences.json",
    "scenario.json",
    "steering.json",
)


def _hash_files(root: Path, relative_paths: tuple[str, ...] | list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in relative_paths:
        path = root / relative
        if not path.is_file():
            raise IntegrityError(f"required source file is absent: {relative}")
        result[relative] = sha256_file(path)
    return result


def benchmark_lock(root: Path) -> dict[str, Any]:
    files = list(SCIENTIFIC_FILES)
    for scenario in ORIGINAL_SCENARIOS:
        files.extend(
            f"benchmark_data/amazon/{scenario}/{name}"
            for name in SCENARIO_DATA_FILES
        )
    files.append("benchmark_data/amazon/laptop/ai_injection.json")
    hashes = _hash_files(root, files)
    frontend = root / "agentarena/envs/amazon/server/frontend/dist"
    if not frontend.is_dir():
        raise IntegrityError("prebuilt Amazon frontend is absent")
    return {
        "files": hashes,
        "frontend_dist_sha256": hash_tree(frontend),
        "original_catalog_product_ids": {
            scenario: sorted(
                str(product["asin"])
                for product in read_json(
                    root / f"benchmark_data/amazon/{scenario}/catalog.json"
                )["products"]
            )
            for scenario in ORIGINAL_SCENARIOS
        },
    }


def freeze_manifest(
    *,
    root: Path,
    config_path: Path,
    split_path: Path,
    output_path: Path,
    base_weight_sha256: str,
    trained_weight_sha256: str,
    tokenizer_sha256: str,
    chat_template_sha256: str,
    container_image_digest: str,
    serving_stack: dict[str, Any],
) -> dict[str, Any]:
    config = read_json(config_path)
    split = read_json(split_path)
    if split.get("campaign_id") != config.get("campaign_id"):
        raise IntegrityError("split and campaign identities differ")
    for label, digest in (
        ("base_weight_sha256", base_weight_sha256),
        ("trained_weight_sha256", trained_weight_sha256),
        ("tokenizer_sha256", tokenizer_sha256),
        ("chat_template_sha256", chat_template_sha256),
    ):
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise IntegrityError(f"{label} must be a lowercase SHA-256")
    if base_weight_sha256 == trained_weight_sha256:
        raise IntegrityError("trained and base weight identities must differ")
    if not container_image_digest.startswith("sha256:") or len(container_image_digest) != 71:
        raise IntegrityError("container image must be pinned by sha256 digest")
    weight_identity_schema = serving_stack.get(
        "weight_identity_schema", "complete_checkpoint_tree.v1"
    )
    if weight_identity_schema not in {
        "complete_checkpoint_tree.v1",
        "harness-posttrain.exact-lora-composite.v1",
    }:
        raise IntegrityError("serving stack has an unsupported weight identity schema")

    source_files = _hash_files(root, list(HARNESS_FILES))
    if source_files != config.get("expected_harness_file_sha256"):
        raise IntegrityError("current harness bytes do not match the preregistered source hashes")
    harness_sha = sha256_bytes(canonical_bytes(source_files))
    inference = {
        **config["inference"],
        "container_image_digest": container_image_digest,
        "serving_stack": serving_stack,
        "tokenizer_sha256": tokenizer_sha256,
        "chat_template_sha256": chat_template_sha256,
    }
    core = {
        "schema_version": 1,
        "campaign": config,
        "split_sha256": split["split_sha256"],
        "source_contract": {
            "harness_files": source_files,
            "harness_sha256": harness_sha,
            "scaffold": config["scaffold"],
        },
        "benchmark_lock": benchmark_lock(root),
        "model_contract": {
            **config["base_model"],
            "weight_identity_schema": weight_identity_schema,
            "base_weight_sha256": base_weight_sha256,
            "trained_weight_sha256": trained_weight_sha256,
        },
        "inference_contract": inference,
        "treatment_difference": (
            "The final base and trained arms differ only in model weight identity."
        ),
    }
    manifest = {**core, "manifest_sha256": sha256_bytes(canonical_bytes(core))}
    write_json_create_only(output_path, manifest)
    write_json_create_only(
        output_path.with_suffix(output_path.suffix + ".sha256.json"),
        {"sha256": sha256_file(output_path)},
    )
    return manifest


def verify_manifest(manifest: dict[str, Any], *, root: Path) -> None:
    core = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if manifest.get("manifest_sha256") != sha256_bytes(canonical_bytes(core)):
        raise IntegrityError("manifest_sha256 mismatch")
    expected_harness = _hash_files(root, list(HARNESS_FILES))
    if manifest["source_contract"]["harness_files"] != expected_harness:
        raise IntegrityError("canonical harness bytes drifted after freeze")
    if manifest["benchmark_lock"] != benchmark_lock(root):
        raise IntegrityError("canonical benchmark bytes drifted after freeze")
