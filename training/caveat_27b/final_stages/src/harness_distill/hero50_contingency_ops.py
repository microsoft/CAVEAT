"""Render and materialize the inert step29/30 HERO50 contingency."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .hero50_contingency import HERO_BUCKETS, HERO_ID, HERO_PROFILE
from .precommit_any_dagger_ops import (
    SCHEMA,
    VARIANTS,
    PrecommitOpsError,
    _canonical,
    _seed,
    _write_new,
    collect_pairs,
    run_bundle,
)

CAMPAIGN = "campaign2-hero50-contingency-r1"
MATERIALIZATION_SCHEMA = "harness-distill.hero50-exact8-materialization.v1"
VARIANT_TARGET = {variant: 2 for variant in VARIANTS}
BUCKET_TARGET = {
    "hero_frontier_discovery": 4,
    "hero_checkpoint_repair": 2,
    "hero_approved_rebind": 2,
}
PAIR_COUNT = 8


class Hero50OpsError(PrecommitOpsError):
    pass


def combine_hero50_bundles(
    *, bundle_paths: list[Path], output_path: Path
) -> dict[str, Any]:
    """Bind multiple TRAIN-only collection pools for one unchanged exact8 selection."""

    if len(bundle_paths) < 2 or output_path.exists() or output_path.is_symlink():
        raise Hero50OpsError("combined HERO50 bundle requires fresh output and two sources")
    sources: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    aliases: set[str] = set()
    run_ids: set[str] = set()
    for path in bundle_paths:
        resolved = path.resolve()
        bundle = json.loads(resolved.read_text(encoding="utf-8"))
        body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
        matrix = bundle.get("matrix") or {}
        source_rows = bundle.get("rows")
        if (
            bundle.get("schema") != SCHEMA
            or bundle.get("bundle_sha256")
            != hashlib.sha256(_canonical(body)).hexdigest()
            or bundle.get("target_identity") != HERO_ID
            or matrix.get("split") != "train"
            or matrix.get("evaluation_rows") != 0
            or not isinstance(source_rows, list)
        ):
            raise Hero50OpsError("combined source is not a sealed TRAIN-only HERO50 bundle")
        alias = bundle.get("student_alias")
        if not isinstance(alias, str) or not alias:
            raise Hero50OpsError("combined HERO50 source alias is absent")
        aliases.add(alias)
        for row in source_rows:
            run_id = row.get("run_id") if isinstance(row, dict) else None
            if (
                not isinstance(run_id, str)
                or not run_id
                or run_id in run_ids
                or row.get("split") != "train"
                or row.get("variant") not in VARIANTS
            ):
                raise Hero50OpsError("combined HERO50 run identities are invalid")
            run_ids.add(run_id)
            rows.append(json.loads(json.dumps(row)))
        sources.append(
            {
                "path": str(resolved),
                "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
                "bundle_sha256": bundle["bundle_sha256"],
            }
        )
    if len(aliases) != 1:
        raise Hero50OpsError("combined HERO50 aliases differ")
    combined_body = {
        "schema": SCHEMA,
        "campaign": "combined-hero50-contingency-exact8",
        "status": "combined",
        "student_alias": next(iter(aliases)),
        "source_bundles": sources,
        "target_identity": HERO_ID,
        "matrix": {
            "split": "train",
            "variants": list(VARIANTS),
            "runs": len(rows),
            "evaluation_rows": 0,
        },
        "routing": {
            "roll_in": "sealed_step29_candidate_until_exact_hero50_failure",
            "intervention": "gpt-5.6-sol#low_same_request",
            "acceptance": (
                "teacher_semantic_transition_plus_executed_immediate_successor"
            ),
        },
        "materialization_target": {
            "rows": PAIR_COUNT,
            "variant_counts": VARIANT_TARGET,
            "bucket_counts": BUCKET_TARGET,
        },
        "rows": rows,
    }
    combined = {
        **combined_body,
        "bundle_sha256": hashlib.sha256(_canonical(combined_body)).hexdigest(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_new(output_path, combined)
    return combined


def _source_configs(source_root: Path) -> dict[str, Path]:
    by_variant: dict[str, Path] = {}
    for path in sorted((source_root / "configs").glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        variant = ((value.get("task") or {}).get("metadata") or {}).get("variant")
        if variant in VARIANTS:
            by_variant.setdefault(str(variant), path)
    if set(by_variant) != set(VARIANTS):
        raise Hero50OpsError("four fixed laptop source variants are required")
    return by_variant


def render_hero50_bundle(
    *,
    source_root: Path,
    output_root: Path,
    proxy_base_port: int,
    environment_base_port: int,
    student_alias: str,
    campaign_id: str = CAMPAIGN,
) -> dict[str, Any]:
    """Render exactly one fresh TRAIN rollout per variant and target bucket."""

    source_root = source_root.resolve()
    output_root = output_root.resolve()
    if output_root.exists() or output_root.is_symlink():
        raise Hero50OpsError("output root must be fresh")
    alias = student_alias.casefold()
    if not student_alias.strip() or not any(step in alias for step in ("step29", "step30")):
        raise Hero50OpsError("the contingency must roll in from an explicit step29/30 alias")
    by_variant = _source_configs(source_root)
    prior_seeds = {
        value["block_seed"]
        for path in sorted((source_root / "configs").glob("*.json"))
        if type((value := json.loads(path.read_text(encoding="utf-8"))).get("block_seed"))
        is int
    }
    output_root.mkdir(parents=True)
    for name in ("configs", "proxy_traces", "run_results", "runtime"):
        (output_root / name).mkdir()

    rows: list[dict[str, Any]] = []
    for variant in VARIANTS:
        source = json.loads(by_variant[variant].read_text(encoding="utf-8"))
        if (
            source.get("condition") != "combined"
            or source.get("scaffold") != "browseruse-deliberative"
            or source.get("max_steps") != 4000
            or source.get("run_timeout_seconds") != 36000
        ):
            raise Hero50OpsError("source is not the fixed laptop combined cell")
        for replica in range(2):
            target_bucket = HERO_PROFILE
            index = len(rows)
            run_id = f"{campaign_id}::{variant}::train::r{replica}"
            seed = _seed(campaign_id, variant, replica)
            if seed in prior_seeds:
                raise Hero50OpsError("fresh TRAIN seed collides with its source pool")
            config = json.loads(json.dumps(source))
            config["block_seed"] = seed
            config["model"] = {
                **source["model"],
                "base_url": f"http://127.0.0.1:{proxy_base_port + index}/v1",
                "deployment": student_alias,
                "name": student_alias,
            }
            config["port"] = environment_base_port + index
            config["run_id"] = run_id
            config["pair_id"] = run_id
            config["out_dir"] = str(
                output_root / "run_results" / f"{variant}-train-r{replica}"
            )
            config["runtime_environment"] = {
                **source.get("runtime_environment", {}),
                "AGENTARENA_CACHE_NONCE": run_id,
                "AGENTARENA_EVALUATION_INPUT_ATTESTATION": hashlib.sha256(
                    f"{run_id}::fresh-hero50-train-only".encode()
                ).hexdigest(),
            }
            config["audit_contract"] = {
                **source.get("audit_contract", {}),
                "campaign": campaign_id,
                "collection_only": True,
                "evaluation_row": False,
                "target_bucket": target_bucket,
                "target_identity": HERO_ID,
            }
            config_path = (
                output_root / "configs" / f"{index:02d}_{variant}_train_r{replica}.json"
            )
            _write_new(config_path, config)
            rows.append(
                {
                    "index": index,
                    "variant": variant,
                    "split": "train",
                    "replica": replica,
                    "target_bucket": target_bucket,
                    "target_identity": HERO_ID,
                    "run_id": run_id,
                    "block_seed": seed,
                    "config": str(config_path),
                    "trace_path": str(
                        output_root
                        / "proxy_traces"
                        / f"{index:02d}_{variant}_train_r{replica}.jsonl"
                    ),
                    "proxy_port": proxy_base_port + index,
                    "environment_port": environment_base_port + index,
                }
            )
    body = {
        "schema": SCHEMA,
        "campaign": campaign_id,
        "status": "rendered",
        "student_alias": student_alias,
        "source_root": str(source_root),
        "target_identity": HERO_ID,
        "matrix": {
            "split": "train",
            "variants": list(VARIANTS),
            "replicas": [0, 1],
            "runs": PAIR_COUNT,
            "evaluation_rows": 0,
        },
        "routing": {
            "roll_in": "declared_step29_or_step30_candidate_until_exact_hero50_failure",
            "intervention": "gpt-5.6-sol#low_same_request",
            "acceptance": "teacher_semantic_transition_plus_executed_immediate_successor",
        },
        "materialization_target": {
            "rows": PAIR_COUNT,
            "variant_counts": VARIANT_TARGET,
            "bucket_counts": BUCKET_TARGET,
        },
        "rows": rows,
    }
    bundle = {**body, "bundle_sha256": hashlib.sha256(_canonical(body)).hexdigest()}
    _write_new(output_root / "bundle.json", bundle)
    return bundle


def _valid_hero_pair(row: dict[str, Any]) -> bool:
    bucket = row.get("training_bucket")
    successor = row.get("successor")
    teacher = row.get("hero_teacher_validation")
    if (
        bucket not in HERO_BUCKETS
        or row.get("source_split") != "train"
        or not isinstance(successor, dict)
        or successor.get("successor_validated") is not True
        or successor.get("hero_transition_validated") is not True
        or successor.get("target_id") != HERO_ID
        or not isinstance(teacher, dict)
        or teacher.get("valid") is not True
    ):
        return False
    if bucket == "hero_checkpoint_repair":
        return successor.get("approved_target_id") == HERO_ID
    return successor.get("after_pdp_id") == HERO_ID


def select_exact8(pairs: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    valid = [row for row in pairs if _valid_hero_pair(row)]
    by_variant = {
        variant: [row for row in valid if row.get("variant") == variant]
        for variant in VARIANTS
    }
    if any(len(rows) < VARIANT_TARGET[variant] for variant, rows in by_variant.items()):
        return None
    choices = {
        variant: [
            choice
            for choice in itertools.combinations(rows, VARIANT_TARGET[variant])
            if len({row.get("run_id") for row in choice}) == VARIANT_TARGET[variant]
        ]
        for variant, rows in by_variant.items()
    }

    def search(position: int, selected: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        if position == len(VARIANTS):
            counts = Counter(row["training_bucket"] for row in selected)
            return selected if counts == Counter(BUCKET_TARGET) else None
        variant = VARIANTS[position]
        for choice in choices[variant]:
            result = search(position + 1, [*selected, *choice])
            if result is not None:
                return result
        return None

    return search(0, [])


def materialize_hero50_pairs(*, bundle_path: Path, output_root: Path) -> dict[str, Any]:
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle_body = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    matrix = bundle.get("matrix") or {}
    if (
        bundle.get("schema") != SCHEMA
        or bundle.get("bundle_sha256")
        != hashlib.sha256(_canonical(bundle_body)).hexdigest()
        or bundle.get("target_identity") != HERO_ID
        or matrix.get("split") != "train"
        or matrix.get("evaluation_rows") != 0
    ):
        raise Hero50OpsError("HERO50 bundle is not a TRAIN-only collection")
    if output_root.exists() or output_root.is_symlink():
        raise Hero50OpsError("materialized output root must be fresh")
    selected = select_exact8(collect_pairs(bundle))
    if selected is None:
        raise Hero50OpsError("exact variant x HERO50-category matrix is unavailable")
    if (
        Counter(row["variant"] for row in selected) != Counter(VARIANT_TARGET)
        or Counter(row["training_bucket"] for row in selected) != Counter(BUCKET_TARGET)
    ):
        raise Hero50OpsError("selected HERO50 balance drifted")
    payload = b"".join(_canonical(row) + b"\n" for row in selected)
    output_root.mkdir(parents=True)
    pair_path = output_root / "chosen_rejected.jsonl"
    pair_path.write_bytes(payload)
    body = {
        "schema": MATERIALIZATION_SCHEMA,
        "status": "complete",
        "target_identity": HERO_ID,
        "source_bundle": {
            "path": str(bundle_path.resolve()),
            "sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
            "bundle_sha256": bundle.get("bundle_sha256"),
        },
        "rows": PAIR_COUNT,
        "variant_counts": VARIANT_TARGET,
        "bucket_counts": BUCKET_TARGET,
        "split_counts": {"train": PAIR_COUNT, "heldout": 0, "evaluation": 0},
        "acceptance": {
            "same_request_chosen_rejected": True,
            "teacher_semantic_transition_validated": PAIR_COUNT,
            "executed_successor_validated": PAIR_COUNT,
        },
        "files": {
            pair_path.name: {
                "rows": PAIR_COUNT,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        },
    }
    manifest = {**body, "manifest_sha256": hashlib.sha256(_canonical(body)).hexdigest()}
    _write_new(output_root / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render")
    render.add_argument("--source-root", type=Path, required=True)
    render.add_argument("--output-root", type=Path, required=True)
    render.add_argument("--proxy-base-port", type=int, required=True)
    render.add_argument("--environment-base-port", type=int, required=True)
    render.add_argument("--student-alias", required=True)
    render.add_argument("--campaign-id", default=CAMPAIGN)
    run = commands.add_parser("run")
    run.add_argument("--bundle", type=Path, required=True)
    run.add_argument("--upstream-base-url", required=True)
    run.add_argument("--python", type=Path, default=Path(sys.executable))
    run.add_argument("--materialized-output", type=Path, required=True)
    run.add_argument("--max-workers", type=int, default=4)
    run.add_argument("--timeout-seconds", type=int, default=10800)
    combine = commands.add_parser("combine")
    combine.add_argument("--bundle", type=Path, action="append", required=True)
    combine.add_argument("--output", type=Path, required=True)
    materialize = commands.add_parser("materialize")
    materialize.add_argument("--bundle", type=Path, required=True)
    materialize.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "render":
        result = render_hero50_bundle(
            source_root=args.source_root,
            output_root=args.output_root,
            proxy_base_port=args.proxy_base_port,
            environment_base_port=args.environment_base_port,
            student_alias=args.student_alias,
            campaign_id=args.campaign_id,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.command == "materialize":
        result = materialize_hero50_pairs(
            bundle_path=args.bundle, output_root=args.output_root
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.command == "combine":
        result = combine_hero50_bundles(
            bundle_paths=args.bundle, output_path=args.output
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    return run_bundle(
        bundle_path=args.bundle,
        upstream_base_url=args.upstream_base_url,
        python=args.python,
        materialized_output=args.materialized_output,
        max_workers=args.max_workers,
        timeout_seconds=args.timeout_seconds,
        expected_rows=PAIR_COUNT,
        selector=select_exact8,
        materializer=materialize_hero50_pairs,
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BUCKET_TARGET",
    "PAIR_COUNT",
    "VARIANT_TARGET",
    "combine_hero50_bundles",
    "materialize_hero50_pairs",
    "render_hero50_bundle",
    "select_exact8",
]
