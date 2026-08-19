from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from statistics import mean
from typing import Any

from .batch import audit_launch_manifest
from .common import IntegrityError, canonical_bytes, read_json, sha256_bytes
from .matrix import audit_matrix


def _bounded(value: Any, *, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise IntegrityError(f"{label} is not numeric: {value!r}") from exc
    if not math.isfinite(parsed) or not 0.0 <= parsed <= 1.0:
        raise IntegrityError(f"{label} is outside [0, 1]: {value!r}")
    return parsed


def load_observations(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        rows = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                import json

                rows.append(json.loads(line))
            except Exception as exc:
                raise IntegrityError(f"invalid JSONL at line {line_number}: {exc}") from exc
        return rows
    value = read_json(path)
    if isinstance(value, dict):
        value = value.get("observations")
    if not isinstance(value, list):
        raise IntegrityError("observations must be a list or an object containing observations")
    return value


def _index_observations(
    matrix: dict[str, Any],
    observations: list[dict[str, Any]],
    *,
    expected_identity: dict[str, str],
    launches: dict[str, dict[str, Any]],
    frozen_manifest: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    audit_matrix(matrix)
    expected = {row["run_id"] for row in matrix["runs"]}
    indexed: dict[str, dict[str, Any]] = {}
    for observation in observations:
        run_id = str(observation.get("run_id", ""))
        if run_id in indexed:
            raise IntegrityError(f"duplicate final observation: {run_id}")
        indexed[run_id] = observation
    missing = sorted(expected - set(indexed))
    extra = sorted(set(indexed) - expected)
    if missing or extra:
        raise IntegrityError(f"observation/matrix mismatch; missing={missing[:5]} extra={extra[:5]}")
    for run_id, observation in indexed.items():
        matrix_row = next(row for row in matrix["runs"] if row["run_id"] == run_id)
        if observation.get("arm") != matrix_row["arm"]:
            raise IntegrityError(f"{run_id}.arm differs from the frozen matrix")
        if observation.get("infrastructure_valid") is not True:
            raise IntegrityError(f"unresolved infrastructure-invalid attempt: {run_id}")
        observation["preservation_strict"] = _bounded(
            observation.get("preservation_strict"), label=f"{run_id}.preservation_strict"
        )
        binary = _bounded(observation.get("strict_binary"), label=f"{run_id}.strict_binary")
        if binary not in {0.0, 1.0}:
            raise IntegrityError(f"{run_id}.strict_binary is not binary")
        observation["strict_binary"] = binary
        if not isinstance(observation.get("valid_transaction"), bool):
            raise IntegrityError(f"{run_id}.valid_transaction is not boolean")
        if not isinstance(observation.get("compiler_pass"), bool):
            raise IntegrityError(f"{run_id}.compiler_pass is not boolean")
        audit = observation.get("audit")
        if not isinstance(audit, dict):
            raise IntegrityError(f"{run_id}.audit is absent")
        for field in (
            "harness_sha256",
            "inference_contract_sha256",
            "safety_backstop_bound",
            "lossy_context_bound",
            "runtime_drift",
        ):
            if field not in audit:
                raise IntegrityError(f"{run_id}.audit.{field} is absent")
        for field, expected_value in expected_identity.items():
            if audit.get(field) != expected_value:
                raise IntegrityError(f"{run_id}.audit.{field} differs from frozen provenance")
        if audit.get("launch_config_sha256") != launches[run_id].get("config_sha256"):
            raise IntegrityError(f"{run_id}.audit.launch_config_sha256 differs from launch")
        expected_weight = frozen_manifest["model_contract"][
            "base_weight_sha256"
            if matrix_row["arm"] == "base"
            else "trained_weight_sha256"
        ]
        if audit.get("arm_weight_sha256") != expected_weight:
            raise IntegrityError(f"{run_id}.audit.arm_weight_sha256 differs from frozen arm")
        for field in ("summary_sha256", "trajectory_sha256", "launch_config_sha256"):
            value = audit.get(field)
            if not isinstance(value, str) or len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise IntegrityError(f"{run_id}.audit.{field} is not a SHA-256")
        if audit.get("recorded_scores_match_fresh") is not True:
            raise IntegrityError(f"{run_id} recorded and freshly computed scores differ")
    return indexed


def _analysis_provenance(
    *,
    matrix: dict[str, Any],
    config: dict[str, Any],
    frozen_manifest: dict[str, Any],
    launch_manifest: dict[str, Any],
    conversion_audit: dict[str, Any],
    observations_sha256: str,
) -> tuple[dict[str, str], dict[str, dict[str, Any]], dict[str, Any]]:
    audit_matrix(matrix)
    frozen_core = {
        key: value for key, value in frozen_manifest.items() if key != "manifest_sha256"
    }
    if frozen_manifest.get("manifest_sha256") != sha256_bytes(canonical_bytes(frozen_core)):
        raise IntegrityError("frozen manifest has an invalid manifest_sha256")
    if frozen_manifest.get("campaign") != config:
        raise IntegrityError("analysis config differs from the frozen campaign")
    config_sha256 = sha256_bytes(canonical_bytes(config))
    if matrix.get("campaign_contract_sha256") != config_sha256:
        raise IntegrityError("analysis matrix differs from the frozen campaign")
    if matrix.get("campaign_id") != config.get("campaign_id"):
        raise IntegrityError("analysis matrix and campaign identities differ")

    audit_launch_manifest(launch_manifest)
    if launch_manifest.get("campaign_id") != config.get("campaign_id"):
        raise IntegrityError("launch manifest and campaign identities differ")
    if launch_manifest.get("matrix_sha256") != matrix.get("matrix_sha256"):
        raise IntegrityError("launch manifest does not bind the analysis matrix")
    endpoint_sha256 = launch_manifest.get("endpoint_manifest_sha256")
    if (
        not isinstance(endpoint_sha256, str)
        or len(endpoint_sha256) != 64
        or any(character not in "0123456789abcdef" for character in endpoint_sha256)
    ):
        raise IntegrityError("final launch manifest has no endpoint-manifest identity")
    if (
        not isinstance(observations_sha256, str)
        or len(observations_sha256) != 64
        or any(character not in "0123456789abcdef" for character in observations_sha256)
    ):
        raise IntegrityError("observations_sha256 is invalid")

    matrix_rows = {row["run_id"]: row for row in matrix["runs"]}
    launches = {launch["run_id"]: launch for launch in launch_manifest["launches"]}
    if set(matrix_rows) != set(launches):
        raise IntegrityError("launch-manifest run IDs differ from the analysis matrix")
    from agentarena.benchmark import registry

    task_cache: dict[tuple[str, str], dict[str, Any]] = {}
    for run_id, row in matrix_rows.items():
        launch = launches[run_id]
        if launch.get("arm") != row["arm"] or launch.get("pair_id") != row["pair_id"]:
            raise IntegrityError(f"{run_id} launch arm/pair differs from matrix")
        spec = read_json(Path(launch["config"]))
        expected_spec_identity = {
            "run_id": run_id,
            "pair_id": row["pair_id"],
            "arm": row["arm"],
            "campaign_id": config["campaign_id"],
            "matrix_sha256": matrix["matrix_sha256"],
            "env": row["environment"],
            "scaffold": row["scaffold"],
            "condition": row["condition"],
            "block_seed": row["block_seed"],
        }
        for field, expected_value in expected_spec_identity.items():
            if spec.get(field) != expected_value:
                raise IntegrityError(f"{run_id} launch spec {field} differs from matrix")
        task_key = (row["scenario"], row["variant"])
        if task_key not in task_cache:
            tasks = registry.benchmark_tasks(
                row["scenario"], variants=[row["variant"]]
            )
            if len(tasks) != 1 or tasks[0].task_id != row["task_id"]:
                raise IntegrityError(f"cannot resolve frozen task {row['task_id']}")
            task_cache[task_key] = asdict(tasks[0])
        if canonical_bytes(spec.get("task")) != canonical_bytes(task_cache[task_key]):
            raise IntegrityError(f"{run_id} launch task differs from frozen benchmark")

    conversion_core = {
        key: value
        for key, value in conversion_audit.items()
        if key != "conversion_audit_sha256"
    }
    conversion_sha256 = sha256_bytes(canonical_bytes(conversion_core))
    if conversion_audit.get("conversion_audit_sha256") != conversion_sha256:
        raise IntegrityError("conversion audit has an invalid self-hash")
    expected_conversion = {
        "campaign_id": config["campaign_id"],
        "launch_manifest_sha256": launch_manifest["launch_manifest_sha256"],
        "frozen_manifest_sha256": frozen_manifest["manifest_sha256"],
        "frozen_campaign_contract_sha256": config_sha256,
        "matrix_sha256": matrix["matrix_sha256"],
        "endpoint_manifest_sha256": endpoint_sha256,
        "observations_sha256": observations_sha256,
        "expected_runs": len(matrix_rows),
        "observation_rows": len(matrix_rows),
        "infrastructure_invalid_runs": 0,
        "complete": True,
    }
    for field, expected_value in expected_conversion.items():
        if conversion_audit.get(field) != expected_value:
            raise IntegrityError(f"conversion audit {field} differs from frozen analysis inputs")
    classifications = conversion_audit.get("runs")
    if (
        not isinstance(classifications, list)
        or len(classifications) != len(matrix_rows)
        or {
            row.get("run_id") for row in classifications if isinstance(row, dict)
        }
        != set(matrix_rows)
    ):
        raise IntegrityError("conversion audit run IDs differ from the analysis matrix")
    if any(not isinstance(row, dict) for row in classifications):
        raise IntegrityError("conversion audit contains a malformed run")
    if any(
        row.get("infrastructure_valid") is not True
        or row.get("arm") != matrix_rows[row["run_id"]]["arm"]
        or not str(row.get("classification", "")).startswith("behavioral_")
        for row in classifications
    ):
        raise IntegrityError("conversion audit contains an infrastructure-invalid run")

    inference_sha256 = sha256_bytes(canonical_bytes(frozen_manifest["inference_contract"]))
    expected_identity = {
        "frozen_manifest_sha256": frozen_manifest["manifest_sha256"],
        "harness_sha256": frozen_manifest["source_contract"]["harness_sha256"],
        "inference_contract_sha256": inference_sha256,
        "matrix_sha256": matrix["matrix_sha256"],
        "endpoint_manifest_sha256": endpoint_sha256,
        "launch_manifest_sha256": launch_manifest["launch_manifest_sha256"],
    }
    provenance = {
        "frozen_manifest_sha256": frozen_manifest["manifest_sha256"],
        "campaign_contract_sha256": config_sha256,
        "matrix_sha256": matrix["matrix_sha256"],
        "endpoint_manifest_sha256": endpoint_sha256,
        "launch_manifest_sha256": launch_manifest["launch_manifest_sha256"],
        "observations_sha256": observations_sha256,
        "conversion_audit_sha256": conversion_sha256,
    }
    return expected_identity, launches, provenance


def _paired_rows(
    matrix: dict[str, Any],
    indexed: dict[str, dict[str, Any]],
    *,
    left_arm: str,
    right_arm: str,
    predicate: Callable[[dict[str, Any]], bool],
    metric: str,
) -> list[dict[str, Any]]:
    by_pair: dict[str, dict[str, tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(dict)
    for row in matrix["runs"]:
        if predicate(row) and row["arm"] in {left_arm, right_arm}:
            by_pair[row["pair_id"]][row["arm"]] = (row, indexed[row["run_id"]])
    output = []
    for pair_id, arms in sorted(by_pair.items()):
        if set(arms) != {left_arm, right_arm}:
            raise IntegrityError(f"incomplete metric pair {pair_id}: {sorted(arms)}")
        left_row, left = arms[left_arm]
        _, right = arms[right_arm]
        output.append(
            {
                "pair_id": pair_id,
                "scenario": left_row["scenario"],
                "variant": left_row["variant"],
                "repetition": left_row["repetition"],
                "left": float(left[metric]),
                "right": float(right[metric]),
                "difference": float(right[metric]) - float(left[metric]),
            }
        )
    if not output:
        raise IntegrityError("paired analysis has no observations")
    return output


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(probability * len(ordered)) - 1))
    return ordered[index]


def hierarchical_bootstrap_lower_bound(
    pairs: list[dict[str, Any]], *, samples: int, alpha: float, seed: int
) -> float:
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for pair in pairs:
        grouped[(pair["scenario"], pair["variant"])].append(pair["difference"])
    strata = sorted(grouped)
    generator = random.Random(seed)
    estimates = []
    for _ in range(samples):
        selected = [strata[generator.randrange(len(strata))] for _ in strata]
        differences = []
        for stratum in selected:
            values = grouped[stratum]
            differences.extend(values[generator.randrange(len(values))] for _ in values)
        estimates.append(mean(differences))
    return _quantile(estimates, alpha)


def sign_randomization_pvalue(
    pairs: list[dict[str, Any]], *, samples: int, seed: int
) -> float:
    differences = [pair["difference"] for pair in pairs]
    observed = mean(differences)
    generator = random.Random(seed)
    extreme = 0
    for _ in range(samples):
        permuted = mean(value if generator.getrandbits(1) else -value for value in differences)
        if permuted >= observed - 1e-15:
            extreme += 1
    return (extreme + 1) / (samples + 1)


def exact_mcnemar_one_sided(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    trained_wins = sum(pair["left"] == 0.0 and pair["right"] == 1.0 for pair in pairs)
    base_wins = sum(pair["left"] == 1.0 and pair["right"] == 0.0 for pair in pairs)
    discordant = trained_wins + base_wins
    if discordant == 0:
        pvalue = 1.0
    else:
        numerator = sum(math.comb(discordant, value) for value in range(trained_wins, discordant + 1))
        pvalue = numerator / (2**discordant)
    return {
        "trained_wins": trained_wins,
        "base_wins": base_wins,
        "discordant": discordant,
        "pvalue_one_sided": pvalue,
    }


def _arm_mean(
    matrix: dict[str, Any],
    indexed: dict[str, dict[str, Any]],
    *,
    arm: str,
    condition: str,
    metric: str,
) -> float:
    values = [
        float(indexed[row["run_id"]][metric])
        for row in matrix["runs"]
        if row["arm"] == arm and row["condition"] == condition
    ]
    if not values:
        raise IntegrityError(f"no observations for {arm}/{condition}/{metric}")
    return mean(values)


def analyze_final(
    matrix: dict[str, Any],
    observations: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    selected_arm: str,
    frozen_manifest: dict[str, Any],
    launch_manifest: dict[str, Any],
    conversion_audit: dict[str, Any],
    observations_sha256: str,
) -> dict[str, Any]:
    if matrix.get("kind") != "final":
        raise IntegrityError("final statistics require a final matrix")
    expected_identity, launches, provenance = _analysis_provenance(
        matrix=matrix,
        config=config,
        frozen_manifest=frozen_manifest,
        launch_manifest=launch_manifest,
        conversion_audit=conversion_audit,
        observations_sha256=observations_sha256,
    )
    indexed = _index_observations(
        matrix,
        observations,
        expected_identity=expected_identity,
        launches=launches,
        frozen_manifest=frozen_manifest,
    )
    success = config["success"]
    alpha = float(success["alpha"])
    bootstrap_samples = int(success["bootstrap_samples"])
    permutation_samples = int(success["permutation_samples"])
    seed = int(config["seed"])

    def combined(row: dict[str, Any]) -> bool:
        return row["condition"] == "combined"

    def held_out_combined(row: dict[str, Any]) -> bool:
        return row["condition"] == "combined" and bool(row["held_out"])
    pstar_pairs = _paired_rows(
        matrix,
        indexed,
        left_arm="base",
        right_arm=selected_arm,
        predicate=combined,
        metric="preservation_strict",
    )
    hero_pairs = _paired_rows(
        matrix,
        indexed,
        left_arm="base",
        right_arm=selected_arm,
        predicate=combined,
        metric="strict_binary",
    )
    held_out_pstar_pairs = _paired_rows(
        matrix,
        indexed,
        left_arm="base",
        right_arm=selected_arm,
        predicate=held_out_combined,
        metric="preservation_strict",
    )
    held_out_hero_pairs = _paired_rows(
        matrix,
        indexed,
        left_arm="base",
        right_arm=selected_arm,
        predicate=held_out_combined,
        metric="strict_binary",
    )
    pstar_delta = mean(pair["difference"] for pair in pstar_pairs)
    hero_delta = mean(pair["difference"] for pair in hero_pairs)
    held_out_pstar_delta = mean(pair["difference"] for pair in held_out_pstar_pairs)
    held_out_hero_delta = mean(pair["difference"] for pair in held_out_hero_pairs)
    pstar_lower = hierarchical_bootstrap_lower_bound(
        pstar_pairs, samples=bootstrap_samples, alpha=alpha, seed=seed + 1
    )
    hero_lower = hierarchical_bootstrap_lower_bound(
        hero_pairs, samples=bootstrap_samples, alpha=alpha, seed=seed + 2
    )
    held_out_pstar_lower = hierarchical_bootstrap_lower_bound(
        held_out_pstar_pairs, samples=bootstrap_samples, alpha=alpha, seed=seed + 3
    )
    held_out_hero_lower = hierarchical_bootstrap_lower_bound(
        held_out_hero_pairs, samples=bootstrap_samples, alpha=alpha, seed=seed + 4
    )
    permutation_p = sign_randomization_pvalue(
        pstar_pairs, samples=permutation_samples, seed=seed + 5
    )
    mcnemar = exact_mcnemar_one_sided(hero_pairs)

    scenario_hero_deltas = {
        scenario: mean(
            pair["difference"]
            for pair in held_out_hero_pairs
            if pair["scenario"] == scenario
        )
        for scenario in sorted({pair["scenario"] for pair in held_out_hero_pairs})
    }
    positive_scenarios = sum(value > 0 for value in scenario_hero_deltas.values())
    clean_pstar_delta = _arm_mean(
        matrix, indexed, arm=selected_arm, condition="clean", metric="preservation_strict"
    ) - _arm_mean(matrix, indexed, arm="base", condition="clean", metric="preservation_strict")
    clean_purchase_delta = _arm_mean(
        matrix, indexed, arm=selected_arm, condition="clean", metric="valid_transaction"
    ) - _arm_mean(matrix, indexed, arm="base", condition="clean", metric="valid_transaction")

    audits = [observation["audit"] for observation in indexed.values()]
    harness_hashes = {str(audit["harness_sha256"]) for audit in audits}
    inference_hashes = {str(audit["inference_contract_sha256"]) for audit in audits}
    confounds = {
        "harness_hash_identical": len(harness_hashes) == 1,
        "inference_contract_identical": len(inference_hashes) == 1,
        "no_safety_backstop_bound": not any(audit["safety_backstop_bound"] for audit in audits),
        "no_lossy_context_bound": not any(audit["lossy_context_bound"] for audit in audits),
        "no_runtime_drift": not any(audit["runtime_drift"] for audit in audits),
    }
    compiler = {
        arm: {
            "pass_rate": mean(
                float(indexed[row["run_id"]]["compiler_pass"])
                for row in matrix["runs"]
                if row["arm"] == arm
            ),
            "conditional_pstar": mean(
                float(indexed[row["run_id"]]["preservation_strict"])
                for row in matrix["runs"]
                if row["arm"] == arm and indexed[row["run_id"]]["compiler_pass"]
            )
            if any(
                row["arm"] == arm and indexed[row["run_id"]]["compiler_pass"]
                for row in matrix["runs"]
            )
            else None,
        }
        for arm in ("base", selected_arm)
    }

    gates = {
        "minimum_hero_rate_improvement": hero_delta
        >= float(success["minimum_full_five_hero_rate_improvement"]),
        "hero_lower_bound_positive": hero_lower > 0,
        "hero_mcnemar_significant": mcnemar["pvalue_one_sided"] < alpha,
        "held_out_hero_lower_bound_positive": held_out_hero_lower > 0,
        "held_out_hero_scenario_breadth": positive_scenarios
        >= int(success["minimum_held_out_scenarios_with_positive_delta"]),
        "minimum_pstar_improvement": pstar_delta
        >= float(success["minimum_full_five_pstar_improvement"]),
        "pstar_lower_bound_positive": pstar_lower > 0,
        "pstar_permutation_significant": permutation_p < alpha,
        "clean_pstar_guard": clean_pstar_delta
        >= -float(success["maximum_clean_pstar_regression"]),
        "clean_valid_purchase_guard": clean_purchase_delta
        >= -float(success["maximum_clean_valid_purchase_regression"]),
        "confound_audit_green": all(confounds.values()),
    }
    report: dict[str, Any] = {
        "schema_version": 1,
        "campaign_id": config["campaign_id"],
        "provenance": provenance,
        "selected_arm": selected_arm,
        "pair_counts": {
            "combined": len(pstar_pairs),
            "held_out_combined": len(held_out_pstar_pairs),
        },
        "headline": {
            "base_hero_rate": _arm_mean(
                matrix, indexed, arm="base", condition="combined", metric="strict_binary"
            ),
            "selected_hero_rate": _arm_mean(
                matrix, indexed, arm=selected_arm, condition="combined", metric="strict_binary"
            ),
            "hero_rate_difference": hero_delta,
            "hero_rate_one_sided_95_lower_bound": hero_lower,
            "mcnemar": mcnemar,
        },
        "mandatory_secondary": {
            "base_mean_pstar": _arm_mean(
                matrix, indexed, arm="base", condition="combined", metric="preservation_strict"
            ),
            "selected_mean_pstar": _arm_mean(
                matrix,
                indexed,
                arm=selected_arm,
                condition="combined",
                metric="preservation_strict",
            ),
            "pstar_difference": pstar_delta,
            "pstar_one_sided_95_lower_bound": pstar_lower,
            "pstar_sign_randomization_pvalue": permutation_p,
        },
        "held_out": {
            "hero_rate_difference": held_out_hero_delta,
            "hero_rate_one_sided_95_lower_bound": held_out_hero_lower,
            "scenario_hero_rate_differences": scenario_hero_deltas,
            "positive_scenarios": positive_scenarios,
            "pstar_difference": held_out_pstar_delta,
            "pstar_one_sided_95_lower_bound": held_out_pstar_lower,
        },
        "clean_guards": {
            "pstar_difference": clean_pstar_delta,
            "valid_purchase_difference": clean_purchase_delta,
        },
        "compiler_decomposition": compiler,
        "confound_audit": confounds,
        "gates": gates,
        "success": all(gates.values()),
    }

    if any(row["arm"] == "sft_parent" for row in matrix["runs"]):
        sft_pairs = _paired_rows(
            matrix,
            indexed,
            left_arm="sft_parent",
            right_arm=selected_arm,
            predicate=lambda row: row["condition"] == "combined"
            and row["repetition"] < int(config["matrices"]["post_sft_ablation_repetitions"]),
            metric="preservation_strict",
        )
        report["post_sft_contribution"] = {
            "pair_count": len(sft_pairs),
            "pstar_difference": mean(pair["difference"] for pair in sft_pairs),
            "one_sided_95_lower_bound": hierarchical_bootstrap_lower_bound(
                sft_pairs, samples=bootstrap_samples, alpha=alpha, seed=seed + 6
            ),
            "sign_randomization_pvalue": sign_randomization_pvalue(
                sft_pairs, samples=permutation_samples, seed=seed + 7
            ),
        }
    return report
