"""Outcome-blind preparation for the browser-action-corrected exact-LoRA model.

The existing 320-cell exact-LoRA evaluation is useful as a source of matched
control observations, but it freezes the old refinement-step-20 treatment.
This module builds a new, create-only protocol around an independently
validated corrected exact-LoRA manifest.  It deliberately reads preparation
artifacts and launch specifications only; preparation never opens a result,
summary, trajectory, observation, or analysis file.

There are two prospective evaluations:

* a 24-cell laptop development gate (step20 versus corrected; combined n=2
  and clean n=1 for each of four nonabsolute variants), and
* the canonical 320-cell Amazon-five final matrix (raw zero-control versus
  corrected; combined n=5 and clean n=3).

Only the corrected cells need new execution.  Reuse is authorized cell by
cell and only after task, harness, causal runtime configuration, model, seed,
and source launch hashes have been checked.
"""

from __future__ import annotations

import copy
import os
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .batch import audit_launch_manifest
from .common import (
    IntegrityError,
    canonical_bytes,
    read_json,
    sha256_bytes,
    sha256_file,
    stable_int,
    write_json_create_only,
)
from .launcher import (
    TUNNEL_RECORD_FIELDS,
    _normalized_server_contract,
    _normalized_tunnel_contract,
    _safe,
    parse_loopback_v1_url,
    verify_endpoint_manifest,
)
from .manifest import verify_manifest
from .matrix import final_matrix

CORRECTED_PROTOCOL_SCHEMA = "caveat-27b-eval.corrected-protocol.v1"
EVALUATION_MATRIX_SCHEMA = "caveat-27b-eval.corrected-matrix.v1"
CORRECTED_ENDPOINT_SCHEMA = "caveat-27b-eval.corrected-endpoint.v1"
CORRECTED_LAUNCH_SCHEMA = "caveat-27b-eval.corrected-completion-launch.v1"
DERIVED_FREEZE_SCHEMA = 2

DEVELOPMENT_EVALUATION = "laptop_development_gate"
FINAL_EVALUATION = "amazon_five_final"
DEVELOPMENT_LEFT_ARM = "step20"
DEVELOPMENT_RIGHT_ARM = "corrected"
FINAL_LEFT_ARM = "base"
FINAL_RIGHT_ARM = "trained"

DEVELOPMENT_CRITERIA = {
    "combined_binary_hero_delta_minimum": 0.25,
    "clean_binary_hero_regression_maximum": 0.10,
    "minimum_variants_with_positive_combined_binary_hero_delta": 3,
    "safety_backstop_bound_allowed": False,
}

_SHA256_FIELDS = (
    "manifest_sha256",
    "manifest_receipt_sha256",
    "composite_sha256",
)
_SPEC_PROVENANCE_ENVIRONMENT = frozenset(
    {
        "CAVEAT_CACHE_NONCE",
        "CAVEAT_EVALUATION_INPUT_ATTESTATION",
    }
)
_MODEL_TREATMENT_FIELDS = frozenset({"name", "base_url", "deployment"})
_SERVER_RECORD_FIELDS = frozenset(
    {"model_path", "served_model_name", "port", "argv", "config"}
)
_PROBE_FIELDS = frozenset(
    {
        "http_status",
        "served_models",
        "model_bindings",
        "checked_at",
        "response_sha256",
    }
)

ManifestValidator = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class _SourceBundle:
    root: Path
    preparation: dict[str, Any]
    frozen_manifest: dict[str, Any]
    endpoint_manifest: dict[str, Any]
    model_specs: dict[str, Any]
    launch_manifest: dict[str, Any]
    matrix: dict[str, Any]
    launches: dict[str, dict[str, Any]]
    specs: dict[str, dict[str, Any]]


def _sha256(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise IntegrityError(f"{label} is not a lowercase SHA-256")
    return value


def _self_hash(value: Mapping[str, Any], field: str, *, label: str) -> str:
    core = {key: item for key, item in value.items() if key != field}
    digest = sha256_bytes(canonical_bytes(core))
    if value.get(field) != digest:
        raise IntegrityError(f"{label} has an invalid {field}")
    return digest


def _authoritative_manifest_validator() -> ManifestValidator:
    try:
        from caveat_27b.browser_action_finalization import (
            validate_browser_action_serving_manifest,
        )
    except ImportError as exc:  # pragma: no cover - depends on operator environment
        raise IntegrityError(
            "corrected manifest validation requires caveat_27b on PYTHONPATH"
        ) from exc
    return validate_browser_action_serving_manifest


def validate_corrected_exact_lora_manifest(
    manifest_path: Path,
    *,
    validator: ManifestValidator | None = None,
) -> dict[str, Any]:
    """Validate and bind both arms through the training finalizer's authority.

    The training package owns the component-tree and receipt semantics.  The
    evaluation package does not infer paths from a campaign root and does not
    duplicate that validator.  Tests or embedding applications may inject the
    same-signature validator explicitly.
    """

    path = manifest_path.resolve()
    selected_validator = validator or _authoritative_manifest_validator()
    try:
        # The authoritative validator deliberately re-hashes both arms on every
        # invocation.  Invoke it once, then read the already-validated trained
        # descriptor from those same manifest bytes.
        base = selected_validator(path, arm="base")
    except Exception as exc:
        raise IntegrityError(
            f"corrected exact-LoRA manifest is invalid: {exc}"
        ) from exc
    if not isinstance(base, dict):
        raise IntegrityError("corrected manifest validator returned a non-object")
    if Path(str(base.get("manifest_path", ""))).resolve() != path:
        raise IntegrityError("corrected validator returned a different manifest path")
    if base.get("manifest_sha256") != sha256_file(path):
        raise IntegrityError(
            "corrected manifest changed after authoritative validation"
        )

    manifest = read_json(path)
    if base.get("manifest_sha256") != sha256_file(path):
        raise IntegrityError("corrected manifest changed while it was being bound")
    receipt_path = Path(str(base.get("manifest_receipt_path", ""))).resolve()
    if base.get("manifest_receipt_sha256") != sha256_file(receipt_path):
        raise IntegrityError(
            "corrected manifest receipt changed while it was being bound"
        )
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "caveat-27b.exact-lora-inference.v1"
        or manifest.get("status") != "ok"
        or manifest.get("stage") != "browser_action_correction"
        or manifest.get("serving_mode") != "exact_peft_lora"
        or manifest.get("final_model_directory_published") is not False
        or manifest.get("bf16_weights_merged") is not False
    ):
        raise IntegrityError(
            "corrected manifest lacks the finalized exact-LoRA contract"
        )
    shared = manifest.get("shared_tokenizer")
    if not isinstance(shared, dict):
        raise IntegrityError("corrected manifest lacks shared-tokenizer hashes")
    descriptors = manifest.get("arms")
    if not isinstance(descriptors, dict) or set(descriptors) != {"base", "trained"}:
        raise IntegrityError("corrected manifest lacks the exact two-arm descriptor")
    trained_descriptor = descriptors["trained"]
    if not isinstance(trained_descriptor, dict):
        raise IntegrityError("corrected trained descriptor is not an object")
    selected_checkpoint = base.get("selected_checkpoint")
    if (
        not isinstance(selected_checkpoint, dict)
        or not isinstance(selected_checkpoint.get("name"), str)
        or not selected_checkpoint["name"]
    ):
        raise IntegrityError("corrected manifest has no selected checkpoint name")
    trained_parent = trained_descriptor.get("parent")
    trained_adapter = trained_descriptor.get("adapter")
    if not isinstance(trained_parent, dict) or not isinstance(trained_adapter, dict):
        raise IntegrityError("corrected trained components are malformed")
    trained = {
        **{
            key: base.get(key)
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
                "tokenizer",
            )
        },
        "parent": str(Path(str(trained_parent.get("path", ""))).resolve()),
        "adapter": str(Path(str(trained_adapter.get("path", ""))).resolve()),
        "served_model_name": trained_descriptor.get("served_model_name"),
        "composite_sha256": trained_descriptor.get("composite_sha256"),
        "parent_tree_sha256": trained_parent.get("tree_sha256"),
        "adapter_tree_sha256": trained_adapter.get("tree_sha256"),
        "adapter_config_sha256": trained_descriptor.get("adapter_config_sha256"),
    }
    for record_name, record in (("base", base), ("trained", trained)):
        for field in _SHA256_FIELDS:
            _sha256(record.get(field), label=f"corrected {record_name}.{field}")
        for field in ("parent", "adapter", "tokenizer", "served_model_name"):
            if not isinstance(record.get(field), str) or not record[field]:
                raise IntegrityError(f"corrected {record_name}.{field} is absent")
    for field in (
        "parent_tree_sha256",
        "adapter_tree_sha256",
        "adapter_config_sha256",
    ):
        _sha256(trained.get(field), label=f"corrected trained.{field}")
    if base["composite_sha256"] == trained["composite_sha256"]:
        raise IntegrityError("corrected raw and trained composite identities alias")
    if base["served_model_name"] == trained["served_model_name"]:
        raise IntegrityError("corrected raw and trained served-model names alias")
    if base["parent"] == trained["parent"] or base["adapter"] == trained["adapter"]:
        raise IntegrityError("corrected raw and trained components are not distinct")
    tokenizer_sha256 = _sha256(
        shared.get("tokenizer_json_sha256"),
        label="corrected tokenizer_json_sha256",
    )
    chat_template_sha256 = _sha256(
        shared.get("chat_template_sha256"),
        label="corrected chat_template_sha256",
    )
    core = {
        "schema": "caveat-27b-eval.corrected-exact-lora-binding.v1",
        "manifest_path": str(path),
        "manifest_sha256": base["manifest_sha256"],
        "manifest_receipt_path": str(receipt_path),
        "manifest_receipt_sha256": base["manifest_receipt_sha256"],
        "campaign_digest": base["campaign_digest"],
        "artifact_source_git_sha": base["artifact_source_git_sha"],
        "selected_checkpoint": selected_checkpoint,
        "continuation_receipt": base["continuation_receipt"],
        "selection_receipt": base["selection_receipt"],
        "tokenizer": str(Path(str(base["tokenizer"])).resolve()),
        "tokenizer_json_sha256": tokenizer_sha256,
        "chat_template_sha256": chat_template_sha256,
        "arms": {
            "base": {
                key: base[key]
                for key in (
                    "parent",
                    "adapter",
                    "served_model_name",
                    "composite_sha256",
                )
            },
            "trained": {
                key: trained[key]
                for key in (
                    "parent",
                    "adapter",
                    "served_model_name",
                    "composite_sha256",
                    "parent_tree_sha256",
                    "adapter_tree_sha256",
                    "adapter_config_sha256",
                )
            },
        },
    }
    return {
        **core,
        "binding_sha256": sha256_bytes(canonical_bytes(core)),
    }


def _causal_model_spec(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise IntegrityError("launch model spec is not an object")
    return {
        key: copy.deepcopy(value)
        for key, value in sorted(spec.items())
        if key not in _MODEL_TREATMENT_FIELDS
    }


def _causal_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    runtime = spec.get("runtime_environment")
    if not isinstance(runtime, dict):
        raise IntegrityError("launch spec has no runtime environment")
    return {
        "schema": "caveat-27b-eval.causal-cell-config.v1",
        "env": spec.get("env"),
        "scaffold": spec.get("scaffold"),
        "model": _causal_model_spec(spec.get("model")),
        "task": spec.get("task"),
        "condition": spec.get("condition"),
        "max_steps": spec.get("max_steps"),
        "headless": spec.get("headless"),
        "block_seed": spec.get("block_seed"),
        "campaign_id": spec.get("campaign_id"),
        "run_timeout_seconds": spec.get("run_timeout_seconds"),
        "runtime_environment": {
            key: value
            for key, value in sorted(runtime.items())
            if key not in _SPEC_PROVENANCE_ENVIRONMENT
        },
    }


def _cell_hashes(
    spec: Mapping[str, Any],
    launch: Mapping[str, Any],
    *,
    harness_sha256: str,
) -> dict[str, str]:
    task = spec.get("task")
    if not isinstance(task, dict):
        raise IntegrityError(f"{launch.get('run_id')} launch task is absent")
    audit = spec.get("audit_contract")
    if not isinstance(audit, dict):
        raise IntegrityError(f"{launch.get('run_id')} audit contract is absent")
    if audit.get("harness_sha256") != harness_sha256:
        raise IntegrityError(
            f"{launch.get('run_id')} launch uses a different harness hash"
        )
    return {
        "task_sha256": sha256_bytes(canonical_bytes(task)),
        "harness_sha256": harness_sha256,
        "causal_config_sha256": sha256_bytes(canonical_bytes(_causal_spec(spec))),
        "launch_config_sha256": _sha256(
            launch.get("config_sha256"),
            label=f"{launch.get('run_id')} launch config hash",
        ),
        "limit_contract_sha256": _sha256(
            audit.get("limit_contract_sha256"),
            label=f"{launch.get('run_id')} limit contract hash",
        ),
    }


def _canonical_task(*, scenario: str, variant: str) -> dict[str, Any]:
    from dataclasses import asdict

    from caveat.benchmark import registry

    tasks = registry.benchmark_tasks(scenario, variants=[variant])
    task_id = f"{scenario}-{variant}"
    if len(tasks) != 1 or tasks[0].task_id != task_id:
        raise IntegrityError(f"cannot resolve exact benchmark task: {task_id}")
    return asdict(tasks[0])


def _validate_source_spec(
    *,
    row: Mapping[str, Any],
    launch: Mapping[str, Any],
    spec: Mapping[str, Any],
    config: Mapping[str, Any],
) -> None:
    expected = {
        "run_id": row["run_id"],
        "pair_id": row["pair_id"],
        "arm": row["arm"],
        "env": row["environment"],
        "scaffold": row["scaffold"],
        "condition": row["condition"],
        "block_seed": row["block_seed"],
        "campaign_id": config["campaign_id"],
    }
    for field, value in expected.items():
        if spec.get(field) != value:
            raise IntegrityError(
                f"{row['run_id']} source spec {field} differs from matrix"
            )
    if spec.get("matrix_sha256") != launch.get("audit_contract", {}).get(
        "matrix_sha256"
    ):
        raise IntegrityError(f"{row['run_id']} source matrix attestations differ")
    task = _canonical_task(scenario=row["scenario"], variant=row["variant"])
    if canonical_bytes(spec.get("task")) != canonical_bytes(task):
        raise IntegrityError(
            f"{row['run_id']} source task differs from benchmark bytes"
        )


def _load_source_bundle(
    *,
    source_preparation_dir: Path,
    repository_root: Path,
    config: dict[str, Any],
) -> _SourceBundle:
    root = source_preparation_dir.resolve()
    if not root.is_dir():
        raise IntegrityError(f"source preparation directory is absent: {root}")
    paths = {
        "preparation": root / "preparation.json",
        "frozen": root / "frozen_manifest.json",
        "endpoint": root / "endpoint_manifest.json",
        "models": root / "model_specs.json",
        "launch": root / "run_bundle/launch_manifest.json",
    }
    preparation = read_json(paths["preparation"])
    frozen = read_json(paths["frozen"])
    endpoint = read_json(paths["endpoint"])
    model_specs = read_json(paths["models"])
    launch_manifest = read_json(paths["launch"])
    if not all(
        isinstance(value, dict)
        for value in (
            preparation,
            frozen,
            endpoint,
            model_specs,
            launch_manifest,
        )
    ):
        raise IntegrityError("source preparation contains a non-object artifact")
    _self_hash(preparation, "preparation_sha256", label="source preparation")
    verify_manifest(frozen, root=repository_root)
    if frozen.get("campaign") != config:
        raise IntegrityError("source frozen campaign differs from requested campaign")
    matrix = final_matrix(config, selected_arm="trained")
    if len(matrix["runs"]) != 320:
        raise IntegrityError("canonical source final matrix is not 320 cells")
    expected_artifacts = {
        "frozen_manifest_sha256": frozen["manifest_sha256"],
        "matrix_sha256": matrix["matrix_sha256"],
        "model_specs_sha256": sha256_file(paths["models"]),
        "endpoint_manifest_sha256": endpoint.get("endpoint_manifest_sha256"),
        "launch_manifest_sha256": launch_manifest.get("launch_manifest_sha256"),
        "run_count": 320,
    }
    for field, value in expected_artifacts.items():
        if preparation.get(field) != value:
            raise IntegrityError(f"source preparation {field} is stale")
    if model_specs.keys() != {"base", "trained"}:
        raise IntegrityError(
            "source preparation does not contain base and step20 specs"
        )
    verify_endpoint_manifest(
        endpoint_manifest=endpoint,
        frozen_manifest=frozen,
        model_specs=model_specs,
        arms={"base", "trained"},
    )
    audit_launch_manifest(launch_manifest)
    if (
        launch_manifest.get("matrix_sha256") != matrix["matrix_sha256"]
        or launch_manifest.get("endpoint_manifest_sha256")
        != endpoint["endpoint_manifest_sha256"]
        or len(launch_manifest.get("launches", [])) != 320
    ):
        raise IntegrityError(
            "source launch manifest is not the canonical 320-cell bundle"
        )

    launches = {launch["run_id"]: launch for launch in launch_manifest["launches"]}
    rows = {row["run_id"]: row for row in matrix["runs"]}
    if set(launches) != set(rows):
        raise IntegrityError("source launch IDs differ from the canonical final matrix")
    specs: dict[str, dict[str, Any]] = {}
    expected_launch_audit = {
        "frozen_manifest_sha256": frozen["manifest_sha256"],
        "harness_sha256": frozen["source_contract"]["harness_sha256"],
        "inference_contract_sha256": sha256_bytes(
            canonical_bytes(frozen["inference_contract"])
        ),
        "matrix_sha256": matrix["matrix_sha256"],
        "endpoint_manifest_sha256": endpoint["endpoint_manifest_sha256"],
    }
    for run_id, row in rows.items():
        launch = launches[run_id]
        spec = read_json(Path(str(launch["config"])))
        if not isinstance(spec, dict):
            raise IntegrityError(f"{run_id} source launch spec is not an object")
        _validate_source_spec(row=row, launch=launch, spec=spec, config=config)
        audit_contract = spec.get("audit_contract")
        if not isinstance(audit_contract, dict) or any(
            audit_contract.get(field) != value
            for field, value in expected_launch_audit.items()
        ):
            raise IntegrityError(f"{run_id} source launch provenance is stale")
        specs[run_id] = spec

    # Every source pair must already be matched outside its treatment identity.
    paired: dict[str, dict[str, str]] = defaultdict(dict)
    harness_sha256 = frozen["source_contract"]["harness_sha256"]
    for row in matrix["runs"]:
        paired[row["pair_id"]][row["arm"]] = _cell_hashes(
            specs[row["run_id"]],
            launches[row["run_id"]],
            harness_sha256=harness_sha256,
        )["causal_config_sha256"]
    mismatched = [
        pair_id for pair_id, values in paired.items() if len(set(values.values())) != 1
    ]
    if mismatched:
        raise IntegrityError(
            f"source base/step20 causal configurations differ: {mismatched[:3]}"
        )
    source_contract = (
        frozen.get("inference_contract", {})
        .get("serving_stack", {})
        .get("exact_lora_contract")
    )
    if (
        not isinstance(source_contract, dict)
        or source_contract.get("refinement_update") != 20
        or source_contract.get("arms", {}).get("base", {}).get("adapter_kind")
        != "zero_control"
        or source_contract.get("arms", {}).get("trained", {}).get("adapter_kind")
        != "refinement_step20"
    ):
        raise IntegrityError("source preparation is not raw zero-control versus step20")
    return _SourceBundle(
        root=root,
        preparation=preparation,
        frozen_manifest=frozen,
        endpoint_manifest=endpoint,
        model_specs=model_specs,
        launch_manifest=launch_manifest,
        matrix=matrix,
        launches=launches,
        specs=specs,
    )


def development_gate_matrix(
    config: dict[str, Any], source_matrix: dict[str, Any]
) -> dict[str, Any]:
    """Build the preregistered 24-cell, laptop-only corrected gate."""

    variants = tuple(config.get("amazon", {}).get("variants", ()))
    if (
        len(variants) != 4
        or len(set(variants)) != 4
        or any("absolute" in str(variant).casefold() for variant in variants)
    ):
        raise IntegrityError(
            "development gate requires exactly four nonabsolute variants"
        )
    development_scenarios = config.get("amazon", {}).get("development_scenarios")
    if development_scenarios != ["laptop"]:
        raise IntegrityError(
            "development gate requires laptop as the sole development scenario"
        )
    expected_source = final_matrix(config, selected_arm="trained")
    if source_matrix != expected_source:
        raise IntegrityError(
            "development gate source is not the canonical final matrix"
        )

    selected_source = [
        row
        for row in source_matrix["runs"]
        if row["arm"] == "trained"
        and row["scenario"] == "laptop"
        and (
            (row["condition"] == "combined" and row["repetition"] in {0, 1})
            or (row["condition"] == "clean" and row["repetition"] == 0)
        )
    ]
    if len(selected_source) != 12:
        raise IntegrityError(
            "source matrix does not contain the exact 12-cell laptop subset"
        )
    rows: list[dict[str, Any]] = []
    for source_row in selected_source:
        pair_id = source_row["pair_id"].replace(
            "final::", "corrected_development_gate::", 1
        )
        for arm in (DEVELOPMENT_LEFT_ARM, DEVELOPMENT_RIGHT_ARM):
            row = copy.deepcopy(source_row)
            row.update(
                {
                    "run_id": f"{pair_id}::{arm}",
                    "pair_id": pair_id,
                    "phase": "corrected_development_gate",
                    "arm": arm,
                    "launch_order_key": stable_int(
                        config["seed"], "corrected-development-gate", pair_id, arm
                    ),
                    "results_partition": (
                        f"corrected_development_gate_r{source_row['repetition'] + 1:02d}"
                    ),
                }
            )
            rows.append(row)
    rows.sort(key=lambda row: (row["launch_order_key"], row["run_id"]))
    core = {
        "schema_version": 1,
        "schema": EVALUATION_MATRIX_SCHEMA,
        "campaign_id": config["campaign_id"],
        "campaign_contract_sha256": sha256_bytes(canonical_bytes(config)),
        "kind": "corrected_development_gate",
        "left_arm": DEVELOPMENT_LEFT_ARM,
        "right_arm": DEVELOPMENT_RIGHT_ARM,
        "source_matrix_sha256": source_matrix["matrix_sha256"],
        "criteria": DEVELOPMENT_CRITERIA,
        "runs": rows,
    }
    matrix = {**core, "matrix_sha256": sha256_bytes(canonical_bytes(core))}
    audit_corrected_matrix(matrix, config=config)
    return matrix


def audit_corrected_matrix(
    matrix: dict[str, Any], *, config: dict[str, Any]
) -> dict[str, Any]:
    """Audit either prospective corrected evaluation matrix."""

    core = {key: value for key, value in matrix.items() if key != "matrix_sha256"}
    if matrix.get("matrix_sha256") != sha256_bytes(canonical_bytes(core)):
        raise IntegrityError("corrected matrix_sha256 mismatch")
    rows = matrix.get("runs")
    if not isinstance(rows, list) or not rows:
        raise IntegrityError("corrected matrix has no runs")
    run_ids = [row.get("run_id") for row in rows if isinstance(row, dict)]
    if len(run_ids) != len(rows) or len(run_ids) != len(set(run_ids)):
        raise IntegrityError("corrected matrix has malformed or duplicate run IDs")
    pairs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        pairs[str(row.get("pair_id"))].append(row)

    if matrix.get("kind") == "corrected_development_gate":
        expected_arms = {DEVELOPMENT_LEFT_ARM, DEVELOPMENT_RIGHT_ARM}
        if matrix.get("criteria") != DEVELOPMENT_CRITERIA:
            raise IntegrityError("development criteria changed after preregistration")
        if len(rows) != 24 or len(pairs) != 12:
            raise IntegrityError("development gate must contain 24 cells in 12 pairs")
        if {row.get("scenario") for row in rows} != {"laptop"} or any(
            row.get("held_out") is not False for row in rows
        ):
            raise IntegrityError(
                "development gate may contain only unheld laptop cells"
            )
        counts = Counter((row.get("arm"), row.get("condition")) for row in rows)
        expected_counts = {
            (arm, condition): repetitions * 4
            for arm in expected_arms
            for condition, repetitions in (("combined", 2), ("clean", 1))
        }
        if counts != expected_counts:
            raise IntegrityError("development gate repetitions differ from n=2/n=1")
        variants = {row.get("variant") for row in rows}
        if variants != set(config["amazon"]["variants"]) or any(
            "absolute" in str(variant).casefold() for variant in variants
        ):
            raise IntegrityError(
                "development gate variants are not the four nonabsolute variants"
            )
    elif matrix.get("kind") == "final":
        expected_arms = {FINAL_LEFT_ARM, FINAL_RIGHT_ARM}
        expected = final_matrix(config, selected_arm=FINAL_RIGHT_ARM)
        if matrix != expected:
            raise IntegrityError(
                "corrected final matrix is not the canonical 320-cell matrix"
            )
        if len(rows) != 320 or len(pairs) != 160:
            raise IntegrityError(
                "corrected final matrix must contain 320 cells in 160 pairs"
            )
    else:
        raise IntegrityError(f"unsupported corrected matrix kind: {matrix.get('kind')}")

    for pair_id, pair_rows in pairs.items():
        if (
            len(pair_rows) != 2
            or {row.get("arm") for row in pair_rows} != expected_arms
        ):
            raise IntegrityError(f"corrected matrix pair is incomplete: {pair_id}")
        seeds = {row.get("block_seed") for row in pair_rows}
        tasks = {row.get("task_id") for row in pair_rows}
        conditions = {row.get("condition") for row in pair_rows}
        if len(seeds) != 1 or len(tasks) != 1 or len(conditions) != 1:
            raise IntegrityError(
                f"corrected matrix pair is not task/seed matched: {pair_id}"
            )
    return {
        "valid": True,
        "run_count": len(rows),
        "pair_count": len(pairs),
        "arms": dict(Counter(str(row["arm"]) for row in rows)),
        "conditions": dict(Counter(str(row["condition"]) for row in rows)),
    }


def _target_row_contract(
    *,
    target_row: dict[str, Any],
    source_run_id: str,
    source: _SourceBundle,
    evidence_kind: str,
    arm_weight_sha256: str,
) -> dict[str, Any]:
    launch = source.launches[source_run_id]
    spec = source.specs[source_run_id]
    hashes = _cell_hashes(
        spec,
        launch,
        harness_sha256=source.frozen_manifest["source_contract"]["harness_sha256"],
    )
    core = {
        "target_run_id": target_row["run_id"],
        "target_pair_id": target_row["pair_id"],
        "target_arm": target_row["arm"],
        "target_row_sha256": sha256_bytes(canonical_bytes(target_row)),
        "source_run_id": source_run_id,
        "source_pair_id": spec["pair_id"],
        "source_arm": spec["arm"],
        "evidence_kind": evidence_kind,
        "block_seed": target_row["block_seed"],
        "arm_weight_sha256": _sha256(
            arm_weight_sha256, label=f"{target_row['run_id']} arm weight"
        ),
        **hashes,
    }
    return {**core, "cell_contract_sha256": sha256_bytes(canonical_bytes(core))}


def _reuse_launch_manifest(
    *,
    source: _SourceBundle,
    target_matrix: dict[str, Any],
    source_run_ids: list[str],
    evaluation: str,
) -> dict[str, Any]:
    launches = [copy.deepcopy(source.launches[run_id]) for run_id in source_run_ids]
    core = {
        "schema_version": 2,
        "execution_mode": "reuse_only",
        "evaluation": evaluation,
        "campaign_id": source.launch_manifest["campaign_id"],
        # Reused specs retain and attest the original canonical matrix.
        "matrix_sha256": source.matrix["matrix_sha256"],
        "target_matrix_sha256": target_matrix["matrix_sha256"],
        "endpoint_manifest_sha256": source.launch_manifest["endpoint_manifest_sha256"],
        "base_port": source.launch_manifest["base_port"],
        "results_root": source.launch_manifest["results_root"],
        "source_launch_manifest_sha256": source.launch_manifest[
            "launch_manifest_sha256"
        ],
        "launches": launches,
    }
    return {
        **core,
        "launch_manifest_sha256": sha256_bytes(canonical_bytes(core)),
    }


def _derived_frozen_manifest(
    *, source: _SourceBundle, binding: dict[str, Any]
) -> dict[str, Any]:
    core = copy.deepcopy(
        {
            key: value
            for key, value in source.frozen_manifest.items()
            if key != "manifest_sha256"
        }
    )
    core["schema_version"] = DERIVED_FREEZE_SCHEMA
    core["model_contract"]["trained_weight_sha256"] = binding["arms"]["trained"][
        "composite_sha256"
    ]
    serving_stack = core["inference_contract"]["serving_stack"]
    serving_stack.pop("exact_lora_contract", None)
    serving_stack["corrected_exact_lora_binding"] = {
        "schema": "caveat-27b-eval.corrected-exact-lora-serving.v1",
        "manifest_sha256": binding["manifest_sha256"],
        "manifest_receipt_sha256": binding["manifest_receipt_sha256"],
        "binding_sha256": binding["binding_sha256"],
        "selected_checkpoint": binding["selected_checkpoint"],
        "base_composite_sha256": binding["arms"]["base"]["composite_sha256"],
        "trained_composite_sha256": binding["arms"]["trained"]["composite_sha256"],
    }
    serving_stack["shared_tokenizer_path"] = binding["tokenizer"]
    core["treatment_difference"] = (
        "The prospective raw and corrected arms differ only in their exact-LoRA "
        "parent/adapter composition; the development comparator is the separately "
        "frozen current step20 composition."
    )
    return {**core, "manifest_sha256": sha256_bytes(canonical_bytes(core))}


def _build_protocol_artifacts(
    *,
    source: _SourceBundle,
    binding: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    if (
        binding["arms"]["base"]["composite_sha256"]
        != source.frozen_manifest["model_contract"]["base_weight_sha256"]
    ):
        raise IntegrityError(
            "corrected manifest raw composite differs from reusable raw"
        )
    if (
        binding["arms"]["base"]["served_model_name"]
        != source.model_specs["base"]["deployment"]
    ):
        raise IntegrityError(
            "corrected manifest does not preserve the exact raw endpoint alias"
        )
    if (
        binding["arms"]["trained"]["composite_sha256"]
        == source.frozen_manifest["model_contract"]["trained_weight_sha256"]
    ):
        raise IntegrityError(
            "corrected trained composite aliases the current step20 model"
        )
    if binding["arms"]["trained"]["served_model_name"] in {
        source.model_specs["base"]["deployment"],
        source.model_specs["trained"]["deployment"],
    }:
        raise IntegrityError(
            "corrected trained model must use a new served-model alias"
        )
    inference = source.frozen_manifest["inference_contract"]
    if binding["tokenizer_json_sha256"] != inference.get("tokenizer_sha256"):
        raise IntegrityError(
            "corrected tokenizer differs from the evaluated step20 tokenizer"
        )
    if binding["chat_template_sha256"] != inference.get("chat_template_sha256"):
        raise IntegrityError(
            "corrected chat template differs from the evaluated step20 template"
        )

    development = development_gate_matrix(config, source.matrix)
    final = final_matrix(config, selected_arm=FINAL_RIGHT_ARM)
    audit_corrected_matrix(development, config=config)
    audit_corrected_matrix(final, config=config)

    source_rows = {row["run_id"]: row for row in source.matrix["runs"]}
    development_contracts: dict[str, dict[str, Any]] = {}
    development_reuse_ids: list[str] = []
    for row in development["runs"]:
        suffix = row["pair_id"].removeprefix("corrected_development_gate::")
        source_run_id = f"final::{suffix}::trained"
        if source_run_id not in source_rows:
            raise IntegrityError(f"development source row is absent: {source_run_id}")
        evidence_kind = "reuse" if row["arm"] == DEVELOPMENT_LEFT_ARM else "new"
        if evidence_kind == "reuse":
            development_reuse_ids.append(source_run_id)
            weight = source.frozen_manifest["model_contract"]["trained_weight_sha256"]
        else:
            weight = binding["arms"]["trained"]["composite_sha256"]
        development_contracts[row["run_id"]] = _target_row_contract(
            target_row=row,
            source_run_id=source_run_id,
            source=source,
            evidence_kind=evidence_kind,
            arm_weight_sha256=weight,
        )
    if len(development_reuse_ids) != 12 or len(set(development_reuse_ids)) != 12:
        raise IntegrityError("development reuse did not resolve 12 unique step20 cells")

    final_contracts: dict[str, dict[str, Any]] = {}
    final_reuse_ids: list[str] = []
    for row in final["runs"]:
        source_run_id = row["run_id"]
        evidence_kind = "reuse" if row["arm"] == FINAL_LEFT_ARM else "new"
        if evidence_kind == "reuse":
            final_reuse_ids.append(source_run_id)
            weight = source.frozen_manifest["model_contract"]["base_weight_sha256"]
        else:
            # Corrected cells clone the raw counterpart's causal configuration.
            source_run_id = f"{row['pair_id']}::base"
            weight = binding["arms"]["trained"]["composite_sha256"]
        final_contracts[row["run_id"]] = _target_row_contract(
            target_row=row,
            source_run_id=source_run_id,
            source=source,
            evidence_kind=evidence_kind,
            arm_weight_sha256=weight,
        )
    if len(final_reuse_ids) != 160 or len(set(final_reuse_ids)) != 160:
        raise IntegrityError("final reuse did not resolve 160 unique raw cells")

    development_reuse = _reuse_launch_manifest(
        source=source,
        target_matrix=development,
        source_run_ids=sorted(development_reuse_ids),
        evaluation=DEVELOPMENT_EVALUATION,
    )
    final_reuse = _reuse_launch_manifest(
        source=source,
        target_matrix=final,
        source_run_ids=sorted(final_reuse_ids),
        evaluation=FINAL_EVALUATION,
    )
    derived_frozen = _derived_frozen_manifest(source=source, binding=binding)
    evaluations = {
        DEVELOPMENT_EVALUATION: {
            "matrix_sha256": development["matrix_sha256"],
            "left_role": "current_step20",
            "left_arm": DEVELOPMENT_LEFT_ARM,
            "right_role": "corrected_selected",
            "right_arm": DEVELOPMENT_RIGHT_ARM,
            "pair_count": 12,
            "total_run_count": 24,
            "reuse_run_count": 12,
            "new_run_count": 12,
            "criteria": DEVELOPMENT_CRITERIA,
            "cell_contracts": development_contracts,
            "reuse_launch_manifest_sha256": development_reuse["launch_manifest_sha256"],
        },
        FINAL_EVALUATION: {
            "matrix_sha256": final["matrix_sha256"],
            "left_role": "raw_zero_control",
            "left_arm": FINAL_LEFT_ARM,
            "right_role": "corrected_selected",
            "right_arm": FINAL_RIGHT_ARM,
            "pair_count": 160,
            "total_run_count": 320,
            "reuse_run_count": 160,
            "new_run_count": 160,
            "requires_successful_development_gate": True,
            "cell_contracts": final_contracts,
            "reuse_launch_manifest_sha256": final_reuse["launch_manifest_sha256"],
        },
    }
    protocol_core = {
        "schema": CORRECTED_PROTOCOL_SCHEMA,
        "campaign_id": config["campaign_id"],
        "campaign_contract_sha256": sha256_bytes(canonical_bytes(config)),
        "outcome_blind_preparation": True,
        "heldout_results_used_for_selection": False,
        "source": {
            "preparation_dir": str(source.root),
            "preparation_sha256": source.preparation["preparation_sha256"],
            "frozen_manifest_sha256": source.frozen_manifest["manifest_sha256"],
            "endpoint_manifest_sha256": source.endpoint_manifest[
                "endpoint_manifest_sha256"
            ],
            "model_specs_sha256": sha256_bytes(canonical_bytes(source.model_specs)),
            "launch_manifest_sha256": source.launch_manifest["launch_manifest_sha256"],
            "matrix_sha256": source.matrix["matrix_sha256"],
            "harness_sha256": source.frozen_manifest["source_contract"][
                "harness_sha256"
            ],
            "raw_composite_sha256": source.frozen_manifest["model_contract"][
                "base_weight_sha256"
            ],
            "step20_composite_sha256": source.frozen_manifest["model_contract"][
                "trained_weight_sha256"
            ],
        },
        "corrected_exact_lora": binding,
        "derived_frozen_manifest_sha256": derived_frozen["manifest_sha256"],
        "evaluations": evaluations,
    }
    protocol = {
        **protocol_core,
        "protocol_sha256": sha256_bytes(canonical_bytes(protocol_core)),
    }
    return {
        "protocol": protocol,
        "development_matrix": development,
        "final_matrix": final,
        "development_reuse": development_reuse,
        "final_reuse": final_reuse,
        "derived_frozen_manifest": derived_frozen,
    }


_PREPARATION_FILES = {
    "campaign": "campaign.json",
    "protocol": "protocol.json",
    "derived_frozen_manifest": "derived_frozen_manifest.json",
    "development_matrix": "development/matrix.json",
    "development_reuse": "development/reuse_launch_manifest.json",
    "final_matrix": "final/matrix.json",
    "final_reuse": "final/reuse_launch_manifest.json",
}


def _write_protocol_artifacts(
    root: Path,
    *,
    config: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, str]:
    values = {"campaign": config, **artifacts}
    paths: dict[str, Path] = {}
    for name, relative in _PREPARATION_FILES.items():
        path = root / relative
        write_json_create_only(path, values[name])
        paths[name] = path
    return {name: sha256_file(path) for name, path in paths.items()}


def prepare_corrected_evaluations(
    *,
    repository_root: Path,
    config_path: Path,
    source_preparation_dir: Path,
    exact_lora_manifest_path: Path,
    output_dir: Path,
    manifest_validator: ManifestValidator | None = None,
) -> dict[str, Any]:
    """Create both prospective designs without inspecting or launching results.

    This is the first of two preparation stages.  It freezes matrices, reuse
    eligibility, model identity, and selection rules.  Corrected endpoint
    qualification and corrected-only run rendering happen later, after an
    operator has captured the endpoint records.
    """

    output = output_dir.resolve()
    if output.exists() or output.is_symlink():
        raise IntegrityError(f"refusing to overwrite corrected preparation: {output}")
    repository = repository_root.resolve()
    if not repository.is_dir():
        raise IntegrityError(f"repository root is absent: {repository}")
    config = read_json(config_path)
    if not isinstance(config, dict):
        raise IntegrityError("campaign config is not an object")
    source = _load_source_bundle(
        source_preparation_dir=source_preparation_dir,
        repository_root=repository,
        config=config,
    )
    binding = validate_corrected_exact_lora_manifest(
        exact_lora_manifest_path,
        validator=manifest_validator,
    )
    artifacts = _build_protocol_artifacts(
        source=source,
        binding=binding,
        config=config,
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}.staging-", dir=output.parent
    ) as temporary:
        stage = Path(temporary) / "prepared"
        stage.mkdir()
        file_sha256 = _write_protocol_artifacts(
            stage,
            config=config,
            artifacts=artifacts,
        )
        core = {
            "schema": "caveat-27b-eval.corrected-preparation.v1",
            "campaign_id": config["campaign_id"],
            "outcome_blind": True,
            "source_results_read": False,
            "protocol_sha256": artifacts["protocol"]["protocol_sha256"],
            "corrected_exact_lora_manifest_sha256": binding["manifest_sha256"],
            "corrected_exact_lora_manifest_receipt_sha256": binding[
                "manifest_receipt_sha256"
            ],
            "source_preparation_sha256": source.preparation["preparation_sha256"],
            "source_launch_manifest_sha256": source.launch_manifest[
                "launch_manifest_sha256"
            ],
            "development_matrix_sha256": artifacts["development_matrix"][
                "matrix_sha256"
            ],
            "development_reuse_launch_manifest_sha256": artifacts["development_reuse"][
                "launch_manifest_sha256"
            ],
            "final_matrix_sha256": artifacts["final_matrix"]["matrix_sha256"],
            "final_reuse_launch_manifest_sha256": artifacts["final_reuse"][
                "launch_manifest_sha256"
            ],
            "derived_frozen_manifest_sha256": artifacts["derived_frozen_manifest"][
                "manifest_sha256"
            ],
            "run_counts": {
                "development_total": 24,
                "development_reused_step20": 12,
                "development_new_corrected": 12,
                "final_total": 320,
                "final_reused_raw": 160,
                "final_new_corrected": 160,
            },
            "files": {
                name: {"path": relative, "sha256": file_sha256[name]}
                for name, relative in _PREPARATION_FILES.items()
            },
        }
        preparation = {
            **core,
            "preparation_sha256": sha256_bytes(canonical_bytes(core)),
        }
        write_json_create_only(stage / "preparation.json", preparation)
        if output.exists() or output.is_symlink():
            raise IntegrityError(
                f"corrected preparation appeared during publication: {output}"
            )
        os.rename(stage, output)
    return preparation


def audit_corrected_preparation(
    *,
    preparation_dir: Path,
    repository_root: Path,
    manifest_validator: ManifestValidator | None = None,
) -> dict[str, Any]:
    """Revalidate every prospective input and return its in-memory artifacts."""

    root = preparation_dir.resolve()
    preparation = read_json(root / "preparation.json")
    if not isinstance(preparation, dict):
        raise IntegrityError("corrected preparation receipt is not an object")
    _self_hash(preparation, "preparation_sha256", label="corrected preparation")
    if (
        preparation.get("schema") != "caveat-27b-eval.corrected-preparation.v1"
        or preparation.get("outcome_blind") is not True
        or preparation.get("source_results_read") is not False
        or preparation.get("run_counts")
        != {
            "development_total": 24,
            "development_reused_step20": 12,
            "development_new_corrected": 12,
            "final_total": 320,
            "final_reused_raw": 160,
            "final_new_corrected": 160,
        }
    ):
        raise IntegrityError("corrected preparation safety/count contract changed")
    values: dict[str, dict[str, Any]] = {}
    files = preparation.get("files")
    if not isinstance(files, dict) or set(files) != set(_PREPARATION_FILES):
        raise IntegrityError("corrected preparation file inventory is not exact")
    for name, relative in _PREPARATION_FILES.items():
        descriptor = files.get(name)
        if not isinstance(descriptor, dict) or descriptor.get("path") != relative:
            raise IntegrityError(
                f"corrected preparation file descriptor changed: {name}"
            )
        path = root / relative
        if descriptor.get("sha256") != sha256_file(path):
            raise IntegrityError(f"corrected preparation file hash changed: {name}")
        value = read_json(path)
        if not isinstance(value, dict):
            raise IntegrityError(f"corrected preparation file is not an object: {name}")
        values[name] = value
    config = values["campaign"]
    protocol = values["protocol"]
    _self_hash(protocol, "protocol_sha256", label="corrected protocol")
    if protocol.get("schema") != CORRECTED_PROTOCOL_SCHEMA:
        raise IntegrityError("corrected protocol schema changed")
    if (
        preparation.get("campaign_id") != config.get("campaign_id")
        or protocol.get("campaign_id") != config.get("campaign_id")
        or protocol.get("campaign_contract_sha256")
        != sha256_bytes(canonical_bytes(config))
        or protocol.get("outcome_blind_preparation") is not True
        or protocol.get("heldout_results_used_for_selection") is not False
    ):
        raise IntegrityError("corrected protocol campaign/blinding contract changed")
    source = _load_source_bundle(
        source_preparation_dir=Path(protocol["source"]["preparation_dir"]),
        repository_root=repository_root,
        config=config,
    )
    binding = validate_corrected_exact_lora_manifest(
        Path(protocol["corrected_exact_lora"]["manifest_path"]),
        validator=manifest_validator,
    )
    expected = _build_protocol_artifacts(
        source=source,
        binding=binding,
        config=config,
    )
    for name in (
        "protocol",
        "derived_frozen_manifest",
        "development_matrix",
        "development_reuse",
        "final_matrix",
        "final_reuse",
    ):
        if values[name] != expected[name]:
            raise IntegrityError(f"corrected preparation no longer reproduces {name}")
    expected_receipt = {
        "protocol_sha256": protocol["protocol_sha256"],
        "corrected_exact_lora_manifest_sha256": binding["manifest_sha256"],
        "corrected_exact_lora_manifest_receipt_sha256": binding[
            "manifest_receipt_sha256"
        ],
        "source_preparation_sha256": source.preparation["preparation_sha256"],
        "source_launch_manifest_sha256": source.launch_manifest[
            "launch_manifest_sha256"
        ],
        "development_matrix_sha256": values["development_matrix"]["matrix_sha256"],
        "development_reuse_launch_manifest_sha256": values["development_reuse"][
            "launch_manifest_sha256"
        ],
        "final_matrix_sha256": values["final_matrix"]["matrix_sha256"],
        "final_reuse_launch_manifest_sha256": values["final_reuse"][
            "launch_manifest_sha256"
        ],
        "derived_frozen_manifest_sha256": values["derived_frozen_manifest"][
            "manifest_sha256"
        ],
    }
    for field, expected_value in expected_receipt.items():
        if preparation.get(field) != expected_value:
            raise IntegrityError(f"corrected preparation receipt changed: {field}")
    return {"preparation_root": str(root), "preparation": preparation, **values}


def _cross_manifest_server_contract(
    *,
    server: dict[str, Any],
    expected_weight_sha256: str,
    expected_tokenizer_path: str,
) -> dict[str, Any]:
    normalized = _normalized_server_contract(
        server=server,
        model_path=server["model_path"],
        served_name=server["served_model_name"],
        port=server["port"],
        expected_weight_sha256=expected_weight_sha256,
    )
    config = normalized.get("config")
    if not isinstance(config, dict):  # pragma: no cover - launcher guarantees this
        raise IntegrityError("normalized exact-LoRA server config is absent")
    if "exact_lora_manifest_sha256" not in config:
        raise IntegrityError("exact-LoRA server has no manifest identity")
    # The two evaluated treatments come from different, independently frozen
    # exact-LoRA manifests.  Their hashes are verified against each arm before
    # this treatment-only field is normalized for execution-config comparison.
    config["exact_lora_manifest_sha256"] = "<EXACT_LORA_MANIFEST_SHA256>"
    argv = normalized.get("argv")
    if not isinstance(argv, list):  # pragma: no cover - launcher guarantees this
        raise IntegrityError("normalized exact-LoRA server argv is absent")
    tokenizer_occurrences = 0
    for index, token in enumerate(argv):
        if token == f"--tokenizer={expected_tokenizer_path}":
            argv[index] = "--tokenizer=<TOKENIZER_PATH>"
            tokenizer_occurrences += 1
        elif (
            index > 0
            and argv[index - 1] == "--tokenizer"
            and token == expected_tokenizer_path
        ):
            argv[index] = "<TOKENIZER_PATH>"
            tokenizer_occurrences += 1
    if tokenizer_occurrences != 1:
        raise IntegrityError(
            "exact-LoRA server argv does not uniquely bind tokenizer path"
        )
    return normalized


def _server_tokenizer_path(server: dict[str, Any], *, label: str) -> str:
    argv = server.get("argv")
    if not isinstance(argv, list):
        raise IntegrityError(f"{label} server argv is absent")
    values: list[str] = []
    for index, token in enumerate(argv):
        if token == "--tokenizer":
            if index + 1 >= len(argv):
                raise IntegrityError(f"{label} server has an unbound --tokenizer")
            values.append(argv[index + 1])
        elif isinstance(token, str) and token.startswith("--tokenizer="):
            values.append(token.split("=", 1)[1])
    if len(values) != 1 or not Path(values[0]).is_absolute():
        raise IntegrityError(f"{label} server must bind one absolute tokenizer path")
    return values[0]


def _read_exact_record(
    path: Path, fields: frozenset[str], *, label: str
) -> dict[str, Any]:
    value = read_json(path)
    if not isinstance(value, dict) or set(value) != set(fields):
        raise IntegrityError(f"{label} must contain exactly {sorted(fields)}")
    return value


def _validated_probe(
    probe: dict[str, Any],
    *,
    deployment: str,
    parent_name: str,
    parent_path: str,
    adapter_path: str,
) -> None:
    if set(probe) != set(_PROBE_FIELDS):
        raise IntegrityError("corrected endpoint probe fields are not exact")
    if (
        probe.get("http_status") != 200
        or probe.get("served_models") != sorted([deployment, parent_name])
        or not isinstance(probe.get("checked_at"), str)
        or not probe["checked_at"]
    ):
        raise IntegrityError("corrected endpoint lacks a successful exact-model probe")
    _sha256(probe.get("response_sha256"), label="corrected endpoint probe response")
    bindings = probe.get("model_bindings")
    if not isinstance(bindings, list) or not all(
        isinstance(item, dict) and set(item) == {"id", "root", "parent"}
        for item in bindings
    ):
        raise IntegrityError("corrected endpoint probe model bindings are malformed")
    public = [item for item in bindings if item["id"] == deployment]
    private = [item for item in bindings if item["id"] == parent_name]
    if public != [{"id": deployment, "root": adapter_path, "parent": parent_name}]:
        raise IntegrityError(
            "corrected endpoint public route is not the selected adapter"
        )
    if private != [{"id": parent_name, "root": parent_path, "parent": None}]:
        raise IntegrityError(
            "corrected endpoint private route is not the selected parent"
        )


def _corrected_endpoint_core(
    *,
    audited: dict[str, Any],
    model_spec: dict[str, Any],
    server: dict[str, Any],
    tunnel: dict[str, Any],
    probe: dict[str, Any],
) -> dict[str, Any]:
    if not all(
        isinstance(value, dict)
        for value in (audited, model_spec, server, tunnel, probe)
    ):
        raise IntegrityError("corrected endpoint inputs must all be objects")
    protocol = audited["protocol"]
    binding = protocol["corrected_exact_lora"]
    trained = binding["arms"]["trained"]
    source_endpoint = read_json(
        Path(protocol["source"]["preparation_dir"]) / "endpoint_manifest.json"
    )
    source_specs = read_json(
        Path(protocol["source"]["preparation_dir"]) / "model_specs.json"
    )
    if not isinstance(source_endpoint, dict) or not isinstance(source_specs, dict):
        raise IntegrityError("source endpoint/model specs are unavailable")
    _self_hash(source_endpoint, "endpoint_manifest_sha256", label="source endpoint")
    if (
        source_endpoint.get("endpoint_manifest_sha256")
        != protocol["source"]["endpoint_manifest_sha256"]
        or sha256_bytes(canonical_bytes(source_specs))
        != protocol["source"]["model_specs_sha256"]
    ):
        raise IntegrityError("source endpoint/model specs changed after preparation")
    source_base_spec = source_specs.get("base")
    if not isinstance(source_base_spec, dict):
        raise IntegrityError("source preparation has no raw model spec")
    source_arms = source_endpoint.get("arms")
    if not isinstance(source_arms, dict):
        raise IntegrityError("source preparation has no endpoint arms")
    source_raw = source_arms.get("base")
    if not isinstance(source_raw, dict):
        raise IntegrityError("source preparation has no raw endpoint record")
    if set(model_spec) != set(source_base_spec):
        raise IntegrityError(
            "corrected model-spec fields differ from the source endpoint"
        )
    if _causal_model_spec(model_spec) != _causal_model_spec(source_base_spec):
        raise IntegrityError(
            "corrected model request configuration differs from raw/step20"
        )
    if model_spec.get("deployment") != trained["served_model_name"]:
        raise IntegrityError(
            "corrected model deployment differs from the finalized alias"
        )
    if not isinstance(model_spec.get("name"), str) or not model_spec["name"]:
        raise IntegrityError("corrected model spec has no display name")
    base_url, endpoint_host, endpoint_port = parse_loopback_v1_url(
        str(model_spec.get("base_url", "")), label="corrected model base URL"
    )
    if base_url == str(source_base_spec.get("base_url", "")).rstrip("/"):
        raise IntegrityError("corrected endpoint aliases the reusable raw listener")

    if set(server) != set(_SERVER_RECORD_FIELDS):
        raise IntegrityError("corrected server record fields are not exact")
    if (
        server.get("model_path") != trained["parent"]
        or server.get("served_model_name") != trained["served_model_name"]
        or type(server.get("port")) is not int
    ):
        raise IntegrityError("corrected server does not bind the finalized parent/name")
    server_config = server.get("config")
    if not isinstance(server_config, dict):
        raise IntegrityError("corrected server config is absent")
    expected_server_identity = {
        "model_path": trained["parent"],
        "served_model_name": trained["served_model_name"],
        "port": server["port"],
        "adapter_path": trained["adapter"],
        "parent_tree_sha256": trained["parent_tree_sha256"],
        "adapter_tree_sha256": trained["adapter_tree_sha256"],
        "adapter_config_sha256": trained["adapter_config_sha256"],
        "composite_weight_sha256": trained["composite_sha256"],
        "tokenizer_json_sha256": binding["tokenizer_json_sha256"],
        "chat_template_sha256": binding["chat_template_sha256"],
        "exact_lora_manifest_sha256": binding["manifest_sha256"],
        "adapter_kind": (
            f"browser_action_correction_{binding['selected_checkpoint'].get('name')}"
        ),
        "serving_mode": "exact_peft_lora",
    }
    differences = [
        field
        for field, expected in expected_server_identity.items()
        if server_config.get(field) != expected
    ]
    if differences:
        raise IntegrityError(
            f"corrected server differs from finalized composition: {differences}"
        )
    source_server = source_raw.get("server")
    if not isinstance(source_server, dict):
        raise IntegrityError("source raw server record is absent")
    corrected_server_contract = _cross_manifest_server_contract(
        server=server,
        expected_weight_sha256=trained["composite_sha256"],
        expected_tokenizer_path=binding["tokenizer"],
    )
    if _server_tokenizer_path(server, label="corrected") != binding["tokenizer"]:
        raise IntegrityError(
            "corrected server tokenizer path differs from the manifest"
        )
    source_server_contract = _cross_manifest_server_contract(
        server=source_server,
        expected_weight_sha256=protocol["source"]["raw_composite_sha256"],
        expected_tokenizer_path=_server_tokenizer_path(
            source_server, label="source raw"
        ),
    )
    if corrected_server_contract != source_server_contract:
        raise IntegrityError(
            "corrected and raw server argv/config differ beyond treatment identity"
        )

    corrected_tunnel_contract = _normalized_tunnel_contract(
        tunnel=tunnel,
        endpoint_host=endpoint_host,
        endpoint_port=endpoint_port,
        server_port=server["port"],
    )
    _, raw_host, raw_port = parse_loopback_v1_url(
        source_base_spec["base_url"], label="source raw model base URL"
    )
    source_tunnel_contract = _normalized_tunnel_contract(
        tunnel=source_raw.get("tunnel"),
        endpoint_host=raw_host,
        endpoint_port=raw_port,
        server_port=source_server["port"],
    )
    if corrected_tunnel_contract != source_tunnel_contract:
        raise IntegrityError(
            "corrected and raw tunnel configs differ beyond pod/listen identity"
        )
    parent_name = server_config.get("parent_served_model_name")
    if not isinstance(parent_name, str) or not parent_name:
        raise IntegrityError("corrected server has no private parent alias")
    _validated_probe(
        probe,
        deployment=trained["served_model_name"],
        parent_name=parent_name,
        parent_path=trained["parent"],
        adapter_path=trained["adapter"],
    )
    if source_raw.get("container_image_digest") != audited["derived_frozen_manifest"][
        "inference_contract"
    ].get("container_image_digest"):
        raise IntegrityError("source raw endpoint container identity is stale")
    return {
        "schema": CORRECTED_ENDPOINT_SCHEMA,
        "protocol_sha256": protocol["protocol_sha256"],
        "exact_lora_binding_sha256": binding["binding_sha256"],
        "exact_lora_manifest_sha256": binding["manifest_sha256"],
        "weight_sha256": trained["composite_sha256"],
        "container_image_digest": source_raw["container_image_digest"],
        "source_raw_endpoint_manifest_sha256": source_endpoint[
            "endpoint_manifest_sha256"
        ],
        "model_spec": model_spec,
        "model_spec_sha256": sha256_bytes(canonical_bytes(model_spec)),
        "server": server,
        "tunnel": tunnel,
        "ready_probe": probe,
    }


def qualify_corrected_endpoint(
    *,
    preparation_dir: Path,
    repository_root: Path,
    model_spec_path: Path,
    server_record_path: Path,
    tunnel_record_path: Path,
    probe_record_path: Path,
    output_path: Path,
    manifest_validator: ManifestValidator | None = None,
) -> dict[str, Any]:
    """Freeze an already-captured corrected endpoint without making a request."""

    if output_path.exists() or output_path.is_symlink():
        raise IntegrityError(
            f"refusing to overwrite corrected endpoint binding: {output_path}"
        )
    audited = audit_corrected_preparation(
        preparation_dir=preparation_dir,
        repository_root=repository_root,
        manifest_validator=manifest_validator,
    )
    model_spec = read_json(model_spec_path)
    if not isinstance(model_spec, dict):
        raise IntegrityError("corrected model spec is not an object")
    server = _read_exact_record(
        server_record_path,
        _SERVER_RECORD_FIELDS,
        label="corrected server record",
    )
    tunnel = _read_exact_record(
        tunnel_record_path,
        frozenset(TUNNEL_RECORD_FIELDS),
        label="corrected tunnel record",
    )
    probe = _read_exact_record(
        probe_record_path,
        _PROBE_FIELDS,
        label="corrected endpoint probe",
    )
    core = _corrected_endpoint_core(
        audited=audited,
        model_spec=model_spec,
        server=server,
        tunnel=tunnel,
        probe=probe,
    )
    result = {
        **core,
        "endpoint_binding_sha256": sha256_bytes(canonical_bytes(core)),
    }
    write_json_create_only(output_path, result)
    return result


def audit_corrected_endpoint(
    *,
    endpoint_binding_path: Path,
    audited_preparation: dict[str, Any],
) -> dict[str, Any]:
    value = read_json(endpoint_binding_path)
    if not isinstance(value, dict):
        raise IntegrityError("corrected endpoint binding is not an object")
    _self_hash(value, "endpoint_binding_sha256", label="corrected endpoint binding")
    if value.get("schema") != CORRECTED_ENDPOINT_SCHEMA:
        raise IntegrityError("corrected endpoint binding schema changed")
    expected_core = _corrected_endpoint_core(
        audited=audited_preparation,
        model_spec=value.get("model_spec"),
        server=value.get("server"),
        tunnel=value.get("tunnel"),
        probe=value.get("ready_probe"),
    )
    expected = {
        **expected_core,
        "endpoint_binding_sha256": sha256_bytes(canonical_bytes(expected_core)),
    }
    if value != expected:
        raise IntegrityError("corrected endpoint binding no longer reproduces")
    return value


def _successful_gate_report(
    path: Path,
    *,
    audited_preparation: dict[str, Any],
    repository_root: Path,
    manifest_validator: ManifestValidator | None = None,
) -> dict[str, Any]:
    report = read_json(path)
    if not isinstance(report, dict):
        raise IntegrityError("development gate report is not an object")
    _self_hash(report, "report_sha256", label="development gate report")
    protocol = audited_preparation["protocol"]
    if (
        report.get("schema")
        != "caveat-27b-eval.corrected-development-gate-report.v1"
        or report.get("protocol_sha256") != protocol["protocol_sha256"]
        or report.get("matrix_sha256")
        != protocol["evaluations"][DEVELOPMENT_EVALUATION]["matrix_sha256"]
        or report.get("corrected_exact_lora_manifest_sha256")
        != protocol["corrected_exact_lora"]["manifest_sha256"]
        or report.get("criteria") != DEVELOPMENT_CRITERIA
        or report.get("selected") is not True
    ):
        raise IntegrityError(
            "final rendering requires the matching successful laptop gate"
        )
    gates = report.get("gates")
    expected_gate_names = {
        "combined_binary_hero_delta",
        "clean_binary_hero_regression",
        "positive_variant_breadth",
        "no_safety_backstop_bound",
    }
    if (
        not isinstance(gates, dict)
        or set(gates) != expected_gate_names
        or not all(value is True for value in gates.values())
    ):
        raise IntegrityError(
            "development gate report does not pass every preregistered gate"
        )
    evidence = report.get("evidence")
    if (
        not isinstance(evidence, dict)
        or set(evidence) != {"observations_path", "binding_receipt_path"}
        or not all(
            isinstance(value, str) and Path(value).is_absolute()
            for value in evidence.values()
        )
    ):
        raise IntegrityError("development gate report lacks exact evidence paths")
    # Import lazily: corrected_report imports this module for protocol helpers.
    from .corrected_report import analyze_development_gate

    reproduced = analyze_development_gate(
        preparation_dir=Path(audited_preparation["preparation_root"]),
        repository_root=repository_root,
        observations_path=Path(evidence["observations_path"]),
        binding_receipt_path=Path(evidence["binding_receipt_path"]),
        manifest_validator=manifest_validator,
    )
    if report != reproduced:
        raise IntegrityError("development gate report does not reproduce from evidence")
    return report


def _path_overlaps(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def render_corrected_completion_bundle(
    *,
    preparation_dir: Path,
    repository_root: Path,
    endpoint_binding_path: Path,
    evaluation: str,
    output_dir: Path,
    results_root: Path,
    base_port: int,
    python_executable: str,
    gate_report_path: Path | None = None,
    manifest_validator: ManifestValidator | None = None,
) -> dict[str, Any]:
    """Render only the missing corrected arm; never execute it.

    ``amazon_five_final`` is fail-closed behind a matching successful laptop
    gate report.  The static final matrix may be preregistered before the gate,
    but no held-out launch config can be rendered before selection is frozen.
    """

    if evaluation not in {DEVELOPMENT_EVALUATION, FINAL_EVALUATION}:
        raise IntegrityError(f"unknown corrected evaluation: {evaluation}")
    if not isinstance(python_executable, str) or not python_executable:
        raise IntegrityError("corrected bundle requires a nonempty Python executable")
    output = output_dir.resolve()
    if output.exists() or output.is_symlink():
        raise IntegrityError(f"refusing to overwrite corrected run bundle: {output}")
    audited = audit_corrected_preparation(
        preparation_dir=preparation_dir,
        repository_root=repository_root,
        manifest_validator=manifest_validator,
    )
    protocol = audited["protocol"]
    endpoint = audit_corrected_endpoint(
        endpoint_binding_path=endpoint_binding_path,
        audited_preparation=audited,
    )
    gate_report = None
    if evaluation == FINAL_EVALUATION:
        if gate_report_path is None:
            raise IntegrityError(
                "final corrected bundle requires a development gate report"
            )
        gate_report = _successful_gate_report(
            gate_report_path,
            audited_preparation=audited,
            repository_root=repository_root,
            manifest_validator=manifest_validator,
        )
        matrix = audited["final_matrix"]
        right_arm = FINAL_RIGHT_ARM
        reuse_manifest = audited["final_reuse"]
    else:
        if gate_report_path is not None:
            raise IntegrityError(
                "development rendering may not consume a prior gate report"
            )
        matrix = audited["development_matrix"]
        right_arm = DEVELOPMENT_RIGHT_ARM
        reuse_manifest = audited["development_reuse"]
    audit_corrected_matrix(matrix, config=audited["campaign"])

    new_results = results_root.resolve()
    source_results = Path(reuse_manifest["results_root"]).resolve()
    if _path_overlaps(new_results, source_results):
        raise IntegrityError("corrected results root overlaps reusable source results")
    new_rows = [row for row in matrix["runs"] if row["arm"] == right_arm]
    expected_new = protocol["evaluations"][evaluation]["new_run_count"]
    if len(new_rows) != expected_new:
        raise IntegrityError("corrected completion row count differs from protocol")
    if base_port < 1024 or base_port + len(new_rows) > 65535:
        raise IntegrityError("corrected storefront port band is invalid")
    _, _, endpoint_port = parse_loopback_v1_url(
        endpoint["model_spec"]["base_url"], label="corrected model base URL"
    )
    if endpoint_port in range(base_port, base_port + len(new_rows)):
        raise IntegrityError("corrected storefront ports overlap the model endpoint")

    config = audited["campaign"]
    source = _load_source_bundle(
        source_preparation_dir=Path(protocol["source"]["preparation_dir"]),
        repository_root=repository_root,
        config=config,
    )
    derived_frozen = audited["derived_frozen_manifest"]
    inference_sha256 = sha256_bytes(
        canonical_bytes(derived_frozen["inference_contract"])
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}.staging-", dir=output.parent
    ) as temporary:
        stage = Path(temporary) / "bundle"
        configs = stage / "configs"
        configs.mkdir(parents=True)
        launches: list[dict[str, Any]] = []
        rows = sorted(
            new_rows, key=lambda row: (row["launch_order_key"], row["run_id"])
        )
        for index, row in enumerate(rows):
            contract = protocol["evaluations"][evaluation]["cell_contracts"].get(
                row["run_id"]
            )
            if not isinstance(contract, dict) or contract.get("evidence_kind") != "new":
                raise IntegrityError(f"{row['run_id']} has no frozen new-cell contract")
            source_run_id = contract["source_run_id"]
            reference = source.specs.get(source_run_id)
            if reference is None:
                raise IntegrityError(
                    f"corrected reference config is absent: {source_run_id}"
                )
            spec = copy.deepcopy(reference)
            port = base_port + index
            cell_name = "__".join(
                _safe(str(value))
                for value in (
                    row["environment"],
                    row["scaffold"],
                    row["arm"],
                    row["task_id"],
                    row["condition"],
                    row["run_id"],
                )
            )
            result_dir = new_results / row["results_partition"] / cell_name
            spec.update(
                {
                    "model": copy.deepcopy(endpoint["model_spec"]),
                    "port": port,
                    "out_dir": str(result_dir),
                    "run_id": row["run_id"],
                    "pair_id": row["pair_id"],
                    "block_seed": row["block_seed"],
                    "arm": row["arm"],
                    "matrix_sha256": matrix["matrix_sha256"],
                }
            )
            runtime = copy.deepcopy(spec["runtime_environment"])
            runtime["CAVEAT_CACHE_NONCE"] = (
                f"{config['campaign_id']}/{row['run_id']}"
            )
            runtime["CAVEAT_EVALUATION_INPUT_ATTESTATION"] = matrix["matrix_sha256"]
            spec["runtime_environment"] = runtime
            audit_contract = {
                "frozen_manifest_sha256": derived_frozen["manifest_sha256"],
                "harness_sha256": protocol["source"]["harness_sha256"],
                "inference_contract_sha256": inference_sha256,
                "matrix_sha256": matrix["matrix_sha256"],
                "limit_contract_sha256": contract["limit_contract_sha256"],
                "endpoint_manifest_sha256": endpoint["endpoint_binding_sha256"],
                "protocol_sha256": protocol["protocol_sha256"],
                "cell_contract_sha256": contract["cell_contract_sha256"],
                "exact_lora_manifest_sha256": protocol["corrected_exact_lora"][
                    "manifest_sha256"
                ],
            }
            spec["audit_contract"] = audit_contract
            if (
                sha256_bytes(canonical_bytes(_causal_spec(spec)))
                != contract["causal_config_sha256"]
            ):
                raise IntegrityError(
                    f"{row['run_id']} corrected causal config differs from its matched source"
                )
            if sha256_bytes(canonical_bytes(spec["task"])) != contract["task_sha256"]:
                raise IntegrityError(f"{row['run_id']} corrected task hash changed")
            path = configs / f"{index:04d}_{_safe(row['run_id'])}.json"
            write_json_create_only(path, spec)
            launch = {
                "run_id": row["run_id"],
                "pair_id": row["pair_id"],
                "arm": row["arm"],
                "port": port,
                "config": str((output / "configs" / path.name).resolve()),
                "config_sha256": sha256_file(path),
                "results": str(result_dir),
                "argv": [
                    python_executable,
                    "-m",
                    "caveat_27b_eval.launch_one",
                    "--spec",
                    str((output / "configs" / path.name).resolve()),
                ],
                "environment": runtime,
                "audit_contract": audit_contract,
            }
            launches.append(launch)

        gate_report_sha256 = (
            gate_report["report_sha256"] if gate_report is not None else None
        )
        launch_core = {
            "schema_version": 2,
            "schema": CORRECTED_LAUNCH_SCHEMA,
            "execution_mode": "single_arm_completion",
            "evaluation": evaluation,
            "campaign_id": config["campaign_id"],
            "matrix_sha256": matrix["matrix_sha256"],
            "endpoint_manifest_sha256": endpoint["endpoint_binding_sha256"],
            "protocol_sha256": protocol["protocol_sha256"],
            "reuse_launch_manifest_sha256": reuse_manifest["launch_manifest_sha256"],
            "gate_report_sha256": gate_report_sha256,
            "base_port": base_port,
            "results_root": str(new_results),
            "launches": launches,
        }
        launch_manifest = {
            **launch_core,
            "launch_manifest_sha256": sha256_bytes(canonical_bytes(launch_core)),
        }
        write_json_create_only(stage / "launch_manifest.json", launch_manifest)
        write_json_create_only(stage / "matrix.json", matrix)
        write_json_create_only(stage / "frozen_manifest.json", derived_frozen)
        write_json_create_only(stage / "endpoint_binding.json", endpoint)
        receipt_core = {
            "schema": "caveat-27b-eval.corrected-completion-bundle.v1",
            "evaluation": evaluation,
            "protocol_sha256": protocol["protocol_sha256"],
            "matrix_sha256": matrix["matrix_sha256"],
            "endpoint_binding_sha256": endpoint["endpoint_binding_sha256"],
            "reuse_launch_manifest_sha256": reuse_manifest["launch_manifest_sha256"],
            "gate_report_sha256": gate_report_sha256,
            "new_run_count": len(launches),
            "launch_manifest_sha256": launch_manifest["launch_manifest_sha256"],
            "results_root": str(new_results),
            "storefront_port_band": [base_port, base_port + len(launches) - 1],
        }
        bundle_receipt = {
            **receipt_core,
            "bundle_sha256": sha256_bytes(canonical_bytes(receipt_core)),
        }
        write_json_create_only(stage / "bundle.json", bundle_receipt)
        # Manifest paths are final absolute paths.  Audit against a temporary
        # projection of the staged configs before publishing the create-only
        # directory, then audit the same bytes again at their final paths.
        staged_launch = copy.deepcopy(launch_manifest)
        for launch in staged_launch["launches"]:
            config_name = Path(launch["config"]).name
            launch["config"] = str(configs / config_name)
            launch["argv"][-1] = str(configs / config_name)
        staged_core = {
            key: value
            for key, value in staged_launch.items()
            if key != "launch_manifest_sha256"
        }
        staged_launch["launch_manifest_sha256"] = sha256_bytes(
            canonical_bytes(staged_core)
        )
        audit_launch_manifest(staged_launch)
        if output.exists() or output.is_symlink():
            raise IntegrityError(
                f"corrected run bundle appeared during publication: {output}"
            )
        os.rename(stage, output)
    # Paths in the manifest point at the published directory, so audit only now.
    audit_launch_manifest(launch_manifest)
    return bundle_receipt
