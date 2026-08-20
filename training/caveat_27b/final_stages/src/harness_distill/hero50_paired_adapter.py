"""Exact8 adapter for one step29/30 -> next-step paired HERO50 update.

The adapter deliberately stops at corpus and objective construction.  The
already-tested paired PRIME path supplies chosen CE, bounded rejected-token
unlikelihood, CP2 x DP2 normalization, checkpoint bridging, and receipts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any

from .hero50_contingency import HERO_ID
from .hero50_contingency_ops import (
    BUCKET_TARGET,
    MATERIALIZATION_SCHEMA,
    PAIR_COUNT,
    VARIANT_TARGET,
    _valid_hero_pair,
)
from .paired_buy_now_training import (
    CUSTOM_LOSS_IMPORT,
    PROBABILITY_CAP,
    _action_semantic_spans,
    _action_start_loose,
    _string_lexemes,
    validate_exact8_pairs,
)
from .precommit_any_dagger_ops import _canonical, _write_new

ADAPTER_SCHEMA = "harness-distill.hero50-next-step-paired-adapter.v1"
DEFAULT_SOURCE_STEP = 29
LEARNING_RATE = 5.0e-6
RETENTION_PAIR_COUNT = 4
TOTAL_PAIR_COUNT = PAIR_COUNT + RETENTION_PAIR_COUNT
SAMPLES_PER_STEP = TOTAL_PAIR_COUNT * 2
CHOSEN_TAIL_COEFFICIENT = Fraction(40, 100)
CHOSEN_ACTION_COEFFICIENT = Fraction(30, 100)
REJECTED_UNLIKELIHOOD_COEFFICIENT = Fraction(30, 100)
CATEGORY_COEFFICIENTS = {
    "hero_frontier_discovery": Fraction(40, 100),
    "hero_checkpoint_repair": Fraction(25, 100),
    "hero_approved_rebind": Fraction(15, 100),
    "shortcut_retention": Fraction(20, 100),
}
CATEGORY_STATE_COUNTS = {**BUCKET_TARGET, "shortcut_retention": RETENTION_PAIR_COUNT}


class Hero50AdapterError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_exact8_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    descriptor = (manifest.get("files") or {}).get("chosen_rejected.jsonl")
    pair_path = manifest_path.parent / "chosen_rejected.jsonl"
    if (
        manifest.get("schema") != MATERIALIZATION_SCHEMA
        or manifest.get("status") != "complete"
        or manifest.get("target_identity") != HERO_ID
        or manifest.get("manifest_sha256")
        != hashlib.sha256(_canonical(body)).hexdigest()
        or manifest.get("rows") != PAIR_COUNT
        or manifest.get("variant_counts") != VARIANT_TARGET
        or manifest.get("bucket_counts") != BUCKET_TARGET
        or manifest.get("split_counts")
        != {"train": PAIR_COUNT, "heldout": 0, "evaluation": 0}
        or not isinstance(descriptor, Mapping)
        or not pair_path.is_file()
        or pair_path.is_symlink()
        or descriptor.get("rows") != PAIR_COUNT
        or descriptor.get("bytes") != pair_path.stat().st_size
        or descriptor.get("sha256") != _sha256(pair_path)
    ):
        raise Hero50AdapterError("exact8 HERO50 manifest or pair bytes drifted")
    pairs = [
        json.loads(line)
        for line in pair_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    if (
        len(pairs) != PAIR_COUNT
        or Counter(row.get("variant") for row in pairs) != Counter(VARIANT_TARGET)
        or Counter(row.get("training_bucket") for row in pairs)
        != Counter(BUCKET_TARGET)
        or len({row.get("row_id") for row in pairs}) != PAIR_COUNT
        or len({row.get("state_id") for row in pairs}) != PAIR_COUNT
        or not all(_valid_hero_pair(row) for row in pairs)
    ):
        raise Hero50AdapterError("exact8 HERO50 pair balance or proof drifted")
    for row in pairs:
        if (
            not isinstance(row.get("messages_before_action"), list)
            or not isinstance(row.get("tools"), list)
            or not isinstance(row.get("chosen"), Mapping)
            or not isinstance(row.get("rejected"), Mapping)
            or row["chosen"].get("role") != "assistant"
            or row["rejected"].get("role") != "assistant"
            or not isinstance(row["chosen"].get("content"), str)
            or not isinstance(row["rejected"].get("content"), str)
        ):
            raise Hero50AdapterError("same-request paired assistant content is absent")
    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "manifest_file_sha256": _sha256(manifest_path),
        "pair_path": str(pair_path.resolve()),
        "pair_sha256": _sha256(pair_path),
        "pairs": pairs,
    }


def corpus_rows(pairs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        for kind, key in (("chosen", "chosen"), ("rejected", "rejected")):
            rows.append(
                {
                    "kind": kind,
                    "row_id": f"{pair['row_id']}:{kind}",
                    "pair_row_id": pair["row_id"],
                    "state_id": pair["state_id"],
                    "variant": pair["variant"],
                    "training_bucket": pair["training_bucket"],
                    "phase": pair["trigger_kind"],
                    "messages_before_action": deepcopy(pair["messages_before_action"]),
                    "assistant_message": deepcopy(pair[key]),
                    "tools": deepcopy(pair["tools"]),
                }
            )
    if (
        len(rows) != SAMPLES_PER_STEP
        or Counter(row["kind"] for row in rows)
        != {"chosen": TOTAL_PAIR_COUNT, "rejected": TOTAL_PAIR_COUNT}
        or set(Counter(row["state_id"] for row in rows).values()) != {2}
    ):
        raise Hero50AdapterError("paired corpus cardinality drifted")
    return rows


def retention_pairs(path: str | Path) -> dict[str, Any]:
    """Select one sealed TRAIN-only Buy-Now pair per variant for retention."""

    exact = validate_exact8_pairs(path)
    selected = []
    for variant in VARIANT_TARGET:
        candidates = sorted(
            (row for row in exact["pairs"] if row.get("variant") == variant),
            key=lambda row: row["row_id"],
        )
        if not candidates:
            raise Hero50AdapterError(f"sealed shortcut retention lacks {variant}")
        selected.append(
            {
                **deepcopy(candidates[0]),
                "training_bucket": "shortcut_retention",
                "trigger_kind": "sealed_premature_buy_now_retention",
            }
        )
    if len(selected) != RETENTION_PAIR_COUNT:
        raise Hero50AdapterError("shortcut retention cardinality drifted")
    return {
        "manifest_path": exact["manifest_path"],
        "manifest_file_sha256": exact["manifest_file_sha256"],
        "pair_path": exact["pair_path"],
        "pair_sha256": exact["pair_sha256"],
        "pairs": selected,
    }


def rejected_action_spans(content: str) -> tuple[list[tuple[int, int]], int]:
    """Mask the rejected action semantics without requiring a Buy-Now phrase."""

    lexemes = _string_lexemes(content)
    action_start = _action_start_loose(content, lexemes)
    spans = _action_semantic_spans(content, lexemes, action_start)
    if not spans:
        raise Hero50AdapterError("rejected HERO50 branch has no action semantics")
    return spans, action_start


def assign_targeted_weights(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Assign 40/30/30 objective mass and 50/30/20 category mass exactly."""

    if len(rows) != SAMPLES_PER_STEP:
        raise Hero50AdapterError("rendered targeted+retention corpus has wrong size")
    objective = {
        "chosen_tail": CHOSEN_TAIL_COEFFICIENT,
        "chosen_action": CHOSEN_ACTION_COEFFICIENT,
        "rejected_unlikelihood": REJECTED_UNLIKELIHOOD_COEFFICIENT,
    }
    members = {
        name: [
            (row, position)
            for row in rows
            for position in row["bucket_positions"][name]
        ]
        for name in objective
    }
    if any(not value for value in members.values()):
        raise Hero50AdapterError("a HERO50 semantic objective bucket is empty")
    ce_tokens = len(members["chosen_tail"]) + len(members["chosen_action"])
    denominators = {
        "chosen_tail": ce_tokens,
        "chosen_action": ce_tokens,
        "rejected_unlikelihood": len(members["rejected_unlikelihood"]),
    }
    for row in rows:
        length = len(row["token_ids"])
        row["ce_weights"] = [0.0] * length
        row["rl_weights"] = [0.0] * length

    audit: dict[str, Any] = {}
    for name, stream_members in members.items():
        stream = "rl_weights" if name == "rejected_unlikelihood" else "ce_weights"
        categories: dict[str, Any] = {}
        for category, category_coefficient in CATEGORY_COEFFICIENTS.items():
            category_members = [
                (row, position)
                for row, position in stream_members
                if row.get("training_bucket") == category
            ]
            by_state: dict[str, list[tuple[dict[str, Any], int]]] = {}
            for row, position in category_members:
                by_state.setdefault(row["state_id"], []).append((row, position))
            if len(by_state) != CATEGORY_STATE_COUNTS[category]:
                raise Hero50AdapterError(f"{name}/{category} state coverage drifted")
            target = float(objective[name] * denominators[name] * category_coefficient)
            per_state = target / len(by_state)
            for state_members in by_state.values():
                per_token = per_state / len(state_members)
                for row, position in state_members:
                    if row[stream][position] != 0.0:
                        raise Hero50AdapterError("HERO50 semantic weights overlap")
                    row[stream][position] = per_token
            observed = math.fsum(row[stream][position] for row, position in category_members)
            if not math.isclose(observed, target, rel_tol=1e-12, abs_tol=1e-8):
                raise Hero50AdapterError(f"{name}/{category} objective mass drifted")
            categories[category] = {
                "category_coefficient": (
                    f"{category_coefficient.numerator}/{category_coefficient.denominator}"
                ),
                "states": len(by_state),
                "tokens": len(category_members),
                "weight_sum": observed,
            }
        total = math.fsum(item["weight_sum"] for item in categories.values())
        expected = float(objective[name] * denominators[name])
        if not math.isclose(total, expected, rel_tol=1e-12, abs_tol=1e-8):
            raise Hero50AdapterError(f"{name} normalized mass drifted")
        audit[name] = {
            "objective_coefficient": (
                f"{objective[name].numerator}/{objective[name].denominator}"
            ),
            "normalizer_tokens": denominators[name],
            "weight_sum": total,
            "normalized_coefficient": total / denominators[name],
            "categories": categories,
        }
    return {
        "objective": "0.40_chosen_tail_ce+0.30_chosen_action_ce+0.30_rejected_action_unlikelihood",
        "category_mix": {
            key: f"{value.numerator}/{value.denominator}"
            for key, value in CATEGORY_COEFFICIENTS.items()
        },
        "buckets": audit,
        "prompt_tokens_weighted": 0,
        "state_balanced_within_category": True,
    }


def materialize_trainer_adapter(
    *,
    manifest_path: str | Path,
    retention_manifest_path: str | Path,
    output_root: Path,
    source_step: int = DEFAULT_SOURCE_STEP,
) -> dict[str, Any]:
    exact = validate_exact8_manifest(manifest_path)
    retention = retention_pairs(retention_manifest_path)
    if source_step not in {29, 30}:
        raise Hero50AdapterError("parent source step must be exactly 29 or 30")
    final_step = source_step + 1
    if output_root.exists() or output_root.is_symlink():
        raise Hero50AdapterError("trainer adapter output must be fresh")
    output_root.mkdir(parents=True)
    fresh_payload = b"".join(_canonical(row) + b"\n" for row in exact["pairs"])
    retention_payload = b"".join(
        _canonical(row) + b"\n" for row in retention["pairs"]
    )
    fresh_path = output_root / "fresh_hero50_pairs.jsonl"
    retention_path = output_root / "shortcut_retention_pairs.jsonl"
    fresh_path.write_bytes(fresh_payload)
    retention_path.write_bytes(retention_payload)
    rows = corpus_rows([*exact["pairs"], *retention["pairs"]])
    corpus_payload = b"".join(_canonical(row) + b"\n" for row in rows)
    corpus_path = output_root / "paired_corpus.jsonl"
    corpus_path.write_bytes(corpus_payload)
    body = {
        "schema": ADAPTER_SCHEMA,
        "status": "ready_for_parent_receipt_binding",
        "target_identity": HERO_ID,
        "source_step": source_step,
        "update_steps": [final_step],
        "final_step": final_step,
        "optimizer_updates": 1,
        "learning_rate": LEARNING_RATE,
        "fresh_optimizer": True,
        "fresh_scheduler": True,
        "fresh_dataloader": True,
        "pair_counts": {
            "states": TOTAL_PAIR_COUNT,
            "chosen": TOTAL_PAIR_COUNT,
            "rejected": TOTAL_PAIR_COUNT,
            "fresh_hero50_states": PAIR_COUNT,
            "sealed_shortcut_retention_states": RETENTION_PAIR_COUNT,
            "fresh_variants": VARIANT_TARGET,
            "total_variants": {
                variant: VARIANT_TARGET[variant] + 1 for variant in VARIANT_TARGET
            },
            "categories": CATEGORY_STATE_COUNTS,
            "evaluation": 0,
            "heldout": 0,
        },
        "objective": {
            "chosen_tail_ce": "40/100",
            "chosen_action_ce": "30/100",
            "rejected_action_unlikelihood": "30/100",
            "category_mix": {
                key: f"{value.numerator}/{value.denominator}"
                for key, value in CATEGORY_COEFFICIENTS.items()
            },
        },
        "custom_loss": {
            "import_path": CUSTOM_LOSS_IMPORT,
            "formula": "-log(1-probability_cap*p_theta(rejected_token|rejected_prefix))",
            "probability_cap": PROBABILITY_CAP,
            "precision": "float32",
        },
        "semantic_mask": {
            "chosen": "pre_action_tail_plus_action_values",
            "rejected": "action_values_without_buy_now_anchor_requirement",
            "prompt": "excluded",
            "json_syntax": "excluded",
        },
        "source_manifest": {
            "path": exact["manifest_path"],
            "sha256": exact["manifest_file_sha256"],
        },
        "source_pairs": {
            "path": exact["pair_path"],
            "sha256": exact["pair_sha256"],
        },
        "materialized_fresh_pairs": {
            "path": fresh_path.name,
            "rows": PAIR_COUNT,
            "bytes": len(fresh_payload),
            "sha256": hashlib.sha256(fresh_payload).hexdigest(),
        },
        "shortcut_retention": {
            "manifest_path": retention["manifest_path"],
            "manifest_sha256": retention["manifest_file_sha256"],
            "pair_path": retention["pair_path"],
            "pair_sha256": retention["pair_sha256"],
            "selection": "one_deterministic_train_pair_per_variant",
            "states": RETENTION_PAIR_COUNT,
        },
        "materialized_retention_pairs": {
            "path": retention_path.name,
            "rows": RETENTION_PAIR_COUNT,
            "bytes": len(retention_payload),
            "sha256": hashlib.sha256(retention_payload).hexdigest(),
        },
        "corpus": {
            "path": str(corpus_path.resolve()),
            "rows": len(rows),
            "bytes": len(corpus_payload),
            "sha256": hashlib.sha256(corpus_payload).hexdigest(),
        },
    }
    adapter = {**body, "adapter_sha256": hashlib.sha256(_canonical(body)).hexdigest()}
    _write_new(output_root / "adapter.json", adapter)
    return adapter


def validate_trainer_adapter(path: str | Path) -> dict[str, Any]:
    """Validate the portable 12-state adapter without reopening source traces."""

    adapter_path = Path(path).resolve()
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
    body = {key: value for key, value in adapter.items() if key != "adapter_sha256"}
    expected_categories = {
        key: f"{value.numerator}/{value.denominator}"
        for key, value in CATEGORY_COEFFICIENTS.items()
    }
    if (
        adapter.get("schema") != ADAPTER_SCHEMA
        or adapter.get("status") != "ready_for_parent_receipt_binding"
        or adapter.get("target_identity") != HERO_ID
        or adapter.get("source_step") != DEFAULT_SOURCE_STEP
        or adapter.get("update_steps") != [DEFAULT_SOURCE_STEP + 1]
        or adapter.get("final_step") != DEFAULT_SOURCE_STEP + 1
        or adapter.get("optimizer_updates") != 1
        or adapter.get("learning_rate") != LEARNING_RATE
        or adapter.get("adapter_sha256")
        != hashlib.sha256(_canonical(body)).hexdigest()
        or adapter.get("pair_counts", {}).get("states") != TOTAL_PAIR_COUNT
        or adapter.get("pair_counts", {}).get("fresh_hero50_states") != PAIR_COUNT
        or adapter.get("pair_counts", {}).get("sealed_shortcut_retention_states")
        != RETENTION_PAIR_COUNT
        or adapter.get("pair_counts", {}).get("categories")
        != CATEGORY_STATE_COUNTS
        or adapter.get("pair_counts", {}).get("evaluation") != 0
        or adapter.get("pair_counts", {}).get("heldout") != 0
        or adapter.get("objective", {}).get("category_mix")
        != expected_categories
    ):
        raise Hero50AdapterError("portable HERO50 trainer adapter policy drifted")

    def read_rows(key: str, expected: int) -> tuple[Path, list[dict[str, Any]]]:
        descriptor = adapter.get(key)
        if not isinstance(descriptor, Mapping):
            raise Hero50AdapterError(f"{key} descriptor is absent")
        relative = descriptor.get("path")
        if not isinstance(relative, str) or Path(relative).name != relative:
            raise Hero50AdapterError(f"{key} path is not portable")
        file_path = adapter_path.parent / relative
        if (
            not file_path.is_file()
            or file_path.is_symlink()
            or descriptor.get("rows") != expected
            or descriptor.get("bytes") != file_path.stat().st_size
            or descriptor.get("sha256") != _sha256(file_path)
        ):
            raise Hero50AdapterError(f"{key} bytes drifted")
        rows = [json.loads(line) for line in file_path.read_text().splitlines() if line]
        if len(rows) != expected:
            raise Hero50AdapterError(f"{key} row count drifted")
        return file_path, rows

    fresh_path, fresh = read_rows("materialized_fresh_pairs", PAIR_COUNT)
    retention_path, retention = read_rows(
        "materialized_retention_pairs", RETENTION_PAIR_COUNT
    )
    if (
        Counter(row.get("variant") for row in fresh) != Counter(VARIANT_TARGET)
        or Counter(row.get("training_bucket") for row in fresh)
        != Counter(BUCKET_TARGET)
        or not all(_valid_hero_pair(row) for row in fresh)
        or Counter(row.get("variant") for row in retention)
        != Counter({variant: 1 for variant in VARIANT_TARGET})
        or Counter(row.get("training_bucket") for row in retention)
        != {"shortcut_retention": RETENTION_PAIR_COUNT}
        or any(row.get("source_split") != "train" for row in retention)
        or len({row.get("state_id") for row in [*fresh, *retention]})
        != TOTAL_PAIR_COUNT
    ):
        raise Hero50AdapterError("portable HERO50 pair balance or proof drifted")
    expected_corpus = b"".join(
        _canonical(row) + b"\n" for row in corpus_rows([*fresh, *retention])
    )
    corpus = adapter.get("corpus")
    corpus_path = adapter_path.parent / "paired_corpus.jsonl"
    if (
        not isinstance(corpus, Mapping)
        or Path(str(corpus.get("path"))).name != corpus_path.name
        or not corpus_path.is_file()
        or corpus_path.is_symlink()
        or corpus.get("rows") != SAMPLES_PER_STEP
        or corpus.get("bytes") != len(expected_corpus)
        or corpus.get("sha256") != hashlib.sha256(expected_corpus).hexdigest()
        or corpus_path.read_bytes() != expected_corpus
    ):
        raise Hero50AdapterError("portable HERO50 corpus drifted")
    return {
        "adapter": adapter,
        "adapter_path": str(adapter_path),
        "adapter_file_sha256": _sha256(adapter_path),
        "fresh_path": str(fresh_path.resolve()),
        "retention_path": str(retention_path.resolve()),
        "corpus_path": str(corpus_path.resolve()),
        "pairs": [*fresh, *retention],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--manifest", type=Path, required=True)
    materialize = commands.add_parser("materialize")
    materialize.add_argument("--manifest", type=Path, required=True)
    materialize.add_argument("--retention-manifest", type=Path, required=True)
    materialize.add_argument("--output-root", type=Path, required=True)
    materialize.add_argument("--source-step", type=int, choices=(29, 30), default=29)
    args = parser.parse_args(argv)
    if args.command == "validate":
        result = validate_exact8_manifest(args.manifest)
        result = {key: value for key, value in result.items() if key != "pairs"}
    else:
        result = materialize_trainer_adapter(
            manifest_path=args.manifest,
            retention_manifest_path=args.retention_manifest,
            output_root=args.output_root,
            source_step=args.source_step,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CATEGORY_COEFFICIENTS",
    "DEFAULT_SOURCE_STEP",
    "LEARNING_RATE",
    "assign_targeted_weights",
    "corpus_rows",
    "materialize_trainer_adapter",
    "rejected_action_spans",
    "retention_pairs",
    "validate_exact8_manifest",
    "validate_trainer_adapter",
]
