"""Fail-closed, action-weighted CE continuation from the sealed step-25 DCP.

This lane deliberately uses PRIME-RL's numeric ``ce_weights`` stream and no
policy-gradient component.  The 72 browser-executed Sol completions are
rendered in the student's exact Qwen3.5 token space; action and non-action
suffixes receive phase-level mass targets.  Twenty-four train-only retention
states receive the remaining mass.  One state can contribute at most two per
cent of the effective CE mass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import tomllib
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

from .amazon_grpo import _link_tree, _require_dcp_inventory, _tree_identity
from .sol_dagger_cleanup_training import (
    LORA_ALPHA,
    LORA_RANK,
    PARENT_ADAPTER_TREE_SHA256,
    PARENT_DCP_TREE_SHA256,
    PARENT_RECEIPT_BODY_SHA256,
    PARENT_RECEIPT_SHA256,
    validate_step25_parent,
)
from .sol_dagger_training import (
    SEQ_LEN,
    _adapter_targets,
    _publish,
    _read_json,
    _self_hashed,
    _write_new,
    canonical_json,
    sha256_file,
)

PRIME_COMMIT = "d334ea52940b47f426293a7d146239e3fbf91caa"
RENDERERS_COMMIT = "5904fa24aa73f83c1694fd85a78d0e746d468284"
PLAN_SCHEMA = "harness-distill.action-weighted-ce-plan.v1"
RECEIPT_SCHEMA = "harness-distill.action-weighted-ce-receipt.v1"
COLLECTION_SCHEMA = "harness-distill.c2-action-collection.v1"
RENDER_AUDIT_SCHEMA = "harness-distill.action-weighted-ce-render-audit.v1"
SCIENTIFIC_LABEL = "executed_same_state_action_weighted_hard_distillation_ce"
SOURCE_STEP = 25
FINAL_STEP = 26
LEARNING_RATE = 1.0e-7
TRAINER_WORLD_SIZE = 4
MAX_STATE_FRACTION = Fraction(1, 50)
_HEX40 = re.compile(r"[0-9a-f]{40}")

EXECUTED_PHASE_COUNTS = {
    "frontier_exploration": 16,
    "checkpoint_grounding": 16,
    "approved_cart_entry": 16,
    "dirty_cart_cleanup": 16,
    "clean_checkout_order": 8,
}
RETENTION_PHASE = "generic_retention"
EXPECTED_EXECUTED_ROWS = 72
EXPECTED_RETENTION_ROWS = 24
EXPECTED_TOTAL_ROWS = 96

# Fractions are exact here.  Floating point is introduced only when PRIME's
# wire-level numeric stream is materialized.
BUCKET_FRACTIONS: dict[str, Fraction] = {
    "frontier_exploration/action": Fraction(15, 100),
    "frontier_exploration/nonaction": Fraction(5, 100),
    "checkpoint_grounding/action": Fraction(15, 100),
    "checkpoint_grounding/nonaction": Fraction(5, 100),
    "approved_cart_entry/action": Fraction(15, 100),
    "approved_cart_entry/nonaction": Fraction(5, 100),
    "dirty_cart_cleanup/action": Fraction(15, 100),
    "dirty_cart_cleanup/nonaction": Fraction(5, 100),
    "clean_checkout_order/action": Fraction(75, 1000),
    "clean_checkout_order/nonaction": Fraction(25, 1000),
    RETENTION_PHASE: Fraction(10, 100),
}
if sum(BUCKET_FRACTIONS.values(), Fraction()) != 1:  # pragma: no cover
    raise RuntimeError("action-weighted CE bucket fractions do not sum to one")

TOPOLOGIES = {
    "cp2_dp2": {
        "name": "cp2_dp2",
        "trainer_world_size": 4,
        "context_parallel_size": 2,
        "data_parallel_replicate": 2,
        "data_parallel_shard": 1,
        "data_parallel_size": 2,
        "fallback": False,
        "mesh_log": (
            "Building 3-D device mesh with ['dp_replicate', 'dp_shard', 'cp'], "
            "[2, 1, 2]"
        ),
    },
    "cp4_dp1": {
        "name": "cp4_dp1",
        "trainer_world_size": 4,
        "context_parallel_size": 4,
        "data_parallel_replicate": 1,
        "data_parallel_shard": 1,
        "data_parallel_size": 1,
        "fallback": True,
        "mesh_log": "Building 2-D device mesh with ['dp_shard', 'cp'], [1, 4]",
    },
}

# Native CE routing and exact Qwen3.5 rendering are source-bound separately
# from the already-reviewed cross-stage and grouped-MM launch patches.
PINNED_NUMERIC_CE_FILES = {
    "src/prime_rl/transport/types.py": (
        "23a721e4530a58dd6f65ae31457201d0c3f0ec7c396bb75abdc12d1a0ba008b7"
    ),
    "src/prime_rl/trainer/batch.py": (
        "278411fc2563b8e9c39f36280889e0035dad263b7aab145c596a1f8ceb65f97c"
    ),
    "src/prime_rl/trainer/rl/loss.py": (
        "102b14539a2aa7e4b807967f9c9caeaaa974373391a21602ce45b24414fb7863"
    ),
}
PINNED_RENDERER_FILES = {
    "deps/renderers/renderers/base.py": (
        "bfb2067a2b92f96b7c59a96f662cd4d584c0e9232d8855d1e2d54d7eeb27d366"
    ),
    "deps/renderers/renderers/qwen35.py": (
        "3064d34020b499744213b7b280ec5a30cc544ed7f05ade7a18c9f675deb5b435"
    ),
    "deps/renderers/renderers/configs.py": (
        "2ec52fff75ce7217a01076573d156ad43767c23f4137071255e9906250f4d949"
    ),
}
TRAIN_PREPATCH_SHA256 = "b4859d0133af2eec8809ac90e84d2a3cd58a49f6d388b646d1d8e7e25e3ff41c"
TRAIN_CROSS_STAGE_SHA256 = "0fe25f3434b144f04b320f65b5477946d0f4f01d0e294837c9b593e7269a6ede"
TRAIN_POSTPATCH_SHA256 = "90c57221bbfd2331f6e80266ee66a17f4099b2ad19da4fd61be59747f710498b"
GROUPED_MM_PREPATCH_SHA256 = "203e1704c8f0284dad024ff5b4061a40c48cb4411360d672b071e6c79792b4ba"
GROUPED_MM_POSTPATCH_SHA256 = "b109d0efc1753dcea7a132f9cc48ecacdb84b33b0cb346c7b333f9cc1a261767"
PINNED_RESUME_FILES = {
    "src/prime_rl/trainer/ckpt.py": (
        "b79cd42748fd79c4f24316b169e4717d10367ffc3564be5aa3342658b2b69447"
    ),
    "src/prime_rl/trainer/optim.py": (
        "72a6baddb1d7945a3b35aab21e5d4517fcfca016bcb8bb75e1aa49d73ded2fed"
    ),
    "src/prime_rl/trainer/runs.py": (
        "9fd6ee0fdcedee2f62fa4bb3fc3febf7154a138a0b7371ce23dd7e099cbb9b0c"
    ),
}
RESUME_ORDER_CONTRACT = {
    "run_registration": "before_optimizer_construction_and_dcp_load",
    "optimizer_binding": "exact_registered_run_0_lora_parameter_objects",
    "optimizer_construction": "before_dcp_load",
    "optimizer_state": "fresh_dcp_optimizer_state_skipped",
    "dcp_restore": "in_place_into_registered_run_0_lora",
    "pre_update_guard": "distributed_sampled_signature_equal_and_lora_b_nonzero",
}


class ActionWeightedCEError(RuntimeError):
    """A collection, render, weight, checkpoint, or launch invariant failed."""


def _file_identity(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ActionWeightedCEError(f"required regular file is absent or unsafe: {path}")
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": sha256_file(path)}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        raise ActionWeightedCEError(f"required JSONL is absent or unsafe: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ActionWeightedCEError(f"blank JSONL line {line_number}: {path}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ActionWeightedCEError(f"invalid JSONL line {line_number}: {path}") from exc
        if not isinstance(value, dict):
            raise ActionWeightedCEError(f"JSONL line {line_number} is not an object: {path}")
        rows.append(value)
    return rows


def _manifest_file(manifest_path: Path, manifest: Mapping[str, Any], name: str) -> Path:
    files = manifest.get("files")
    descriptor = files.get(name) if isinstance(files, Mapping) else None
    if not isinstance(descriptor, Mapping):
        raise ActionWeightedCEError(f"collection manifest lacks file descriptor: {name}")
    raw = descriptor.get("relative_path")
    if not isinstance(raw, str) or not raw:
        raise ActionWeightedCEError(f"collection file path is malformed: {name}")
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ActionWeightedCEError(f"collection file path must be relative and contained: {name}")
    path = (manifest_path.parent / candidate).resolve()
    if path.parent != manifest_path.parent.resolve() or path.name != name:
        raise ActionWeightedCEError(f"collection file must be a manifest sibling named {name}")
    identity = _file_identity(path)
    if (
        descriptor.get("sha256") != identity["sha256"]
        or descriptor.get("bytes") != identity["bytes"]
        or not isinstance(descriptor.get("rows"), int)
    ):
        raise ActionWeightedCEError(f"collection file descriptor drifted: {name}")
    return path


def _require_string(row: Mapping[str, Any], key: str, *, label: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ActionWeightedCEError(f"{label} has no nonempty {key}")
    return value


def _validate_messages(
    row: Mapping[str, Any], *, label: str
) -> tuple[list[dict[str, Any]], dict[str, Any], list[Any]]:
    messages = row.get("messages_before_action")
    teacher = row.get("teacher_message")
    tools = row.get("tools")
    if (
        not isinstance(messages, list)
        or not messages
        or any(not isinstance(message, dict) for message in messages)
        or not isinstance(teacher, dict)
        or teacher.get("role") != "assistant"
        or not isinstance(teacher.get("content"), str)
        or not teacher["content"].strip()
        or not isinstance(tools, list)
    ):
        raise ActionWeightedCEError(f"{label} message/teacher/tool contract is malformed")
    if messages[-1].get("role") == "assistant":
        raise ActionWeightedCEError(f"{label} messages_before_action already ends in assistant")
    return messages, teacher, tools


def _validate_train_row(row: Mapping[str, Any], *, executed: bool, line_number: int) -> None:
    label = f"{'selection' if executed else 'retention'} row {line_number}"
    _require_string(row, "row_id", label=label)
    _require_string(row, "state_id", label=label)
    phase = _require_string(row, "phase", label=label)
    if phase not in (EXECUTED_PHASE_COUNTS if executed else {RETENTION_PHASE: 1}):
        raise ActionWeightedCEError(f"{label} has an unreviewed phase: {phase}")
    if row.get("source_split") != "train":
        raise ActionWeightedCEError(f"{label} is not explicitly train-only")
    _, teacher, _ = _validate_messages(row, label=label)
    if executed:
        qwen = row.get("qwen_message")
        if (
            not isinstance(qwen, Mapping)
            or qwen.get("role") != "assistant"
            or not isinstance(qwen.get("content"), str)
            or teacher.get("tool_calls")
            or teacher.get("reasoning_content") not in (None, "")
            or not any(message.get("role") == "user" for message in row["messages_before_action"])
        ):
            raise ActionWeightedCEError(f"{label} lacks the same-state rejected Qwen message")
        # Executed BrowserUse labels must be the raw, action-final JSON object.
        final_action_member_start(teacher["content"])


def _audit_is_accepted(row: Mapping[str, Any]) -> bool:
    evidence_hashes = {
        key: value
        for key, value in row.items()
        if isinstance(key, str) and key.endswith("_sha256")
    }
    return (
        row.get("status") == "accepted"
        and row.get("accepted") is True
        and row.get("browser_executed") is True
        and row.get("executed") is True
        and row.get("successor_observed") is True
        and row.get("postcondition_verified") is True
        and bool(evidence_hashes)
        and all(
            isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
            for value in evidence_hashes.values()
        )
    )


def validate_collection_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    manifest = _read_json(manifest_path)
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if (
        manifest.get("schema") != COLLECTION_SCHEMA
        or manifest.get("status") != "ok"
        or manifest.get("manifest_sha256")
        != hashlib.sha256(canonical_json(body).encode()).hexdigest()
        or manifest.get("selection_rows") != EXPECTED_EXECUTED_ROWS
        or manifest.get("retention_rows") != EXPECTED_RETENTION_ROWS
        or manifest.get("evaluation_rows") != 0
        or manifest.get("heldout_rows") != 0
        or manifest.get("phase_counts") != EXECUTED_PHASE_COUNTS
        or manifest.get("teacher_model") != "gpt-5.6-sol"
        or manifest.get("teacher_reasoning_effort") != "low"
        or manifest.get("renderer")
        != {"name": "qwen3.5", "enable_thinking": True, "assistant_only": True}
        or manifest.get("state_overlap") != 0
    ):
        raise ActionWeightedCEError("collection manifest policy or self-hash drifted")

    selection_path = _manifest_file(manifest_path, manifest, "selection.jsonl")
    retention_path = _manifest_file(manifest_path, manifest, "retention.jsonl")
    audits_path = _manifest_file(manifest_path, manifest, "execution_audits.jsonl")
    selection = _jsonl(selection_path)
    retention = _jsonl(retention_path)
    audits = _jsonl(audits_path)
    descriptors = manifest["files"]
    if (
        len(selection) != EXPECTED_EXECUTED_ROWS
        or len(retention) != EXPECTED_RETENTION_ROWS
        or len(audits) != EXPECTED_EXECUTED_ROWS
        or descriptors["selection.jsonl"]["rows"] != len(selection)
        or descriptors["retention.jsonl"]["rows"] != len(retention)
        or descriptors["execution_audits.jsonl"]["rows"] != len(audits)
    ):
        raise ActionWeightedCEError("collection JSONL counts drifted")
    for number, row in enumerate(selection, 1):
        _validate_train_row(row, executed=True, line_number=number)
    for number, row in enumerate(retention, 1):
        _validate_train_row(row, executed=False, line_number=number)
    phase_counts = Counter(row["phase"] for row in selection)
    if dict(phase_counts) != EXECUTED_PHASE_COUNTS:
        raise ActionWeightedCEError(f"executed phase counts drifted: {dict(phase_counts)}")
    row_ids = [row["row_id"] for row in [*selection, *retention]]
    state_ids = [row["state_id"] for row in [*selection, *retention]]
    if len(row_ids) != len(set(row_ids)) or len(state_ids) != len(set(state_ids)):
        raise ActionWeightedCEError("all 96 row_id and state_id values must be globally unique")
    audit_ids = [_require_string(row, "row_id", label="execution audit") for row in audits]
    if set(audit_ids) != {row["row_id"] for row in selection} or len(audit_ids) != len(
        set(audit_ids)
    ):
        raise ActionWeightedCEError("execution audits do not join one-to-one with selection rows")
    if not all(_audit_is_accepted(row) for row in audits):
        raise ActionWeightedCEError("an execution audit is not browser-executed and accepted")
    selection_by_id = {row["row_id"]: row for row in selection}
    if any(
        audit.get("state_id") != selection_by_id[audit["row_id"]]["state_id"]
        or audit.get("phase") != selection_by_id[audit["row_id"]]["phase"]
        for audit in audits
    ):
        raise ActionWeightedCEError("execution audit state/phase join drifted")
    return {
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "manifest_body_sha256": manifest["manifest_sha256"],
        "selection_path": str(selection_path),
        "retention_path": str(retention_path),
        "audits_path": str(audits_path),
        "selection": selection,
        "retention": retention,
        "state_count": len(set(state_ids)),
    }


def final_action_member_start(content: str) -> int:
    """Return the character offset of the final top-level ``action`` key."""

    if content != content.strip():
        raise ActionWeightedCEError("executed teacher JSON must not have outer whitespace")
    decoder = json.JSONDecoder()
    length = len(content)

    def skip_ws(index: int) -> int:
        while index < length and content[index].isspace():
            index += 1
        return index

    index = skip_ws(0)
    if index >= length or content[index] != "{":
        raise ActionWeightedCEError("executed teacher content is not a JSON object")
    index += 1
    members: list[tuple[str, int, Any]] = []
    while True:
        index = skip_ws(index)
        if index < length and content[index] == "}":
            index += 1
            break
        key_start = index
        try:
            key, index = decoder.raw_decode(content, index)
        except json.JSONDecodeError as exc:
            raise ActionWeightedCEError("executed teacher JSON key is malformed") from exc
        if not isinstance(key, str):
            raise ActionWeightedCEError("executed teacher JSON object has a non-string key")
        index = skip_ws(index)
        if index >= length or content[index] != ":":
            raise ActionWeightedCEError("executed teacher JSON member lacks a colon")
        index = skip_ws(index + 1)
        try:
            value, index = decoder.raw_decode(content, index)
        except json.JSONDecodeError as exc:
            raise ActionWeightedCEError("executed teacher JSON value is malformed") from exc
        members.append((key, key_start, value))
        index = skip_ws(index)
        if index < length and content[index] == ",":
            index += 1
            continue
        if index < length and content[index] == "}":
            index += 1
            break
        raise ActionWeightedCEError("executed teacher JSON member delimiter is malformed")
    if skip_ws(index) != length or not members:
        raise ActionWeightedCEError("executed teacher JSON has trailing data or no members")
    keys = [key for key, _, _ in members]
    action = members[-1][2]
    if (
        keys[-1] != "action"
        or keys.count("action") != 1
        or not isinstance(action, list)
        or not action
    ):
        raise ActionWeightedCEError("executed teacher JSON must end in one nonempty action list")
    return members[-1][1]


def _stream_sha256(values: Sequence[Any], kind: Literal["int", "bool", "float32"]) -> str:
    digest = hashlib.sha256()
    for value in values:
        if kind == "int":
            digest.update(struct.pack("<q", int(value)))
        elif kind == "bool":
            digest.update(b"\x01" if value else b"\x00")
        else:
            digest.update(struct.pack("<f", float(value)))
    return digest.hexdigest()


def assign_state_balanced_ce_weights(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Populate row ``ce_weights`` with exact bucket mass and state balancing."""

    buckets: dict[str, list[tuple[dict[str, Any], int]]] = defaultdict(list)
    for row in rows:
        masks = row.get("bucket_masks")
        if not isinstance(masks, Mapping):
            raise ActionWeightedCEError("rendered row lacks bucket masks")
        for bucket, mask in masks.items():
            if bucket not in BUCKET_FRACTIONS or len(mask) != len(row["token_ids"]):
                raise ActionWeightedCEError("rendered row has an invalid bucket mask")
            for index, enabled in enumerate(mask):
                if enabled:
                    buckets[bucket].append((row, index))
    if set(buckets) != set(BUCKET_FRACTIONS):
        raise ActionWeightedCEError("one or more required CE buckets has no tokens")
    total_active = sum(len(members) for members in buckets.values())
    if total_active <= 0:
        raise ActionWeightedCEError("weighted CE corpus has no active tokens")
    for row in rows:
        row["ce_weights"] = [0.0] * len(row["token_ids"])

    bucket_audit: dict[str, dict[str, Any]] = {}
    for bucket, fraction in BUCKET_FRACTIONS.items():
        members = buckets[bucket]
        by_state: dict[str, list[tuple[dict[str, Any], int]]] = defaultdict(list)
        for row, index in members:
            by_state[row["state_id"]].append((row, index))
        state_mass = fraction * total_active / len(by_state)
        for state_members in by_state.values():
            token_weight = float(state_mass / len(state_members))
            if not math.isfinite(token_weight) or token_weight <= 0:
                raise ActionWeightedCEError("derived CE token weight is not finite and positive")
            for row, index in state_members:
                if row["ce_weights"][index] != 0.0:
                    raise ActionWeightedCEError("CE buckets overlap on a token")
                row["ce_weights"][index] = token_weight
        observed_mass = math.fsum(row["ce_weights"][index] for row, index in members)
        target_mass = float(fraction * total_active)
        if not math.isclose(observed_mass, target_mass, rel_tol=1e-12, abs_tol=1e-8):
            raise ActionWeightedCEError(f"CE bucket mass drifted: {bucket}")
        bucket_audit[bucket] = {
            "target_fraction": f"{fraction.numerator}/{fraction.denominator}",
            "target_mass": target_mass,
            "observed_mass": observed_mass,
            "nonzero_tokens": len(members),
            "unique_states": len(by_state),
        }

    observed_active = sum(
        sum(weight != 0.0 for weight in row["ce_weights"]) for row in rows
    )
    total_mass = math.fsum(weight for row in rows for weight in row["ce_weights"])
    if observed_active != total_active or not math.isclose(
        total_mass, total_active, rel_tol=1e-12, abs_tol=1e-8
    ):
        raise ActionWeightedCEError("global CE weights do not have mean one")
    state_masses: dict[str, float] = defaultdict(float)
    for row in rows:
        state_masses[row["state_id"]] += math.fsum(row["ce_weights"])
    max_state, max_mass = max(state_masses.items(), key=lambda item: item[1])
    max_fraction = max_mass / total_mass
    if max_fraction > float(MAX_STATE_FRACTION) + 1e-12:
        raise ActionWeightedCEError(
            f"state {max_state} exceeds the 2% CE mass cap: {max_fraction:.8f}"
        )
    return {
        "nonzero_ce_tokens": total_active,
        "total_ce_weight": total_mass,
        "mean_nonzero_ce_weight": total_mass / total_active,
        "buckets": bucket_audit,
        "unique_states": len(state_masses),
        "max_state_id": max_state,
        "max_state_mass": max_mass,
        "max_state_fraction": max_fraction,
        "state_cap_fraction": float(MAX_STATE_FRACTION),
    }


def _normalize_tools(raw_tools: list[Any]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for tool in raw_tools:
        if not isinstance(tool, Mapping):
            raise ActionWeightedCEError("tool definition is not an object")
        if tool.get("type") == "function" and isinstance(tool.get("function"), Mapping):
            tools.append(deepcopy(dict(tool)))
            continue
        name = tool.get("name")
        parameters = tool.get("parameters")
        if not isinstance(name, str) or not name or not isinstance(parameters, Mapping):
            raise ActionWeightedCEError("tool definition lacks function name/parameters")
        function = {
            "name": name,
            "description": tool.get("description", ""),
            "parameters": deepcopy(dict(parameters)),
        }
        if tool.get("strict") is not None:
            function["strict"] = tool["strict"]
        tools.append({"type": "function", "function": function})
    return tools


def _tokenize_with_offsets(tokenizer: Any, text: str) -> tuple[list[int], list[tuple[int, int]]]:
    """Tokenize one renderer text span and retain exact source-character offsets."""

    try:
        encoded = tokenizer(
            text,
            add_special_tokens=False,
            return_attention_mask=False,
            return_token_type_ids=False,
            return_offsets_mapping=True,
        )
        token_ids = encoded["input_ids"]
        raw_offsets = encoded["offset_mapping"]
    except Exception as exc:  # pragma: no cover - pinned tokenizer runtime.
        raise ActionWeightedCEError(
            "Qwen3.5 tokenizer cannot expose exact action-boundary offsets"
        ) from exc
    if (
        not isinstance(token_ids, list)
        or not isinstance(raw_offsets, list)
        or len(token_ids) != len(raw_offsets)
        or not token_ids
    ):
        raise ActionWeightedCEError("Qwen3.5 tokenizer returned malformed offset streams")
    offsets: list[tuple[int, int]] = []
    for raw in raw_offsets:
        if (
            not isinstance(raw, (list, tuple))
            or len(raw) != 2
            or not all(isinstance(value, int) for value in raw)
            or raw[0] < 0
            or raw[1] <= raw[0]
            or raw[1] > len(text)
        ):
            raise ActionWeightedCEError("Qwen3.5 tokenizer emitted an unsafe text offset")
        offsets.append((raw[0], raw[1]))
    if list(tokenizer.encode(text, add_special_tokens=False)) != token_ids:
        raise ActionWeightedCEError("Qwen3.5 renderer/tokenizer token paths disagree")
    return token_ids, offsets


def _render_corpus(corpus_path: Path, model_path: Path) -> tuple[list[dict[str, Any]], Any]:
    """Run only inside the pinned PRIME interpreter."""

    try:
        from prime_rl.utils.chat_template import (
            deserialize_tool_calls,
            normalize_messages,
            strip_message_content,
        )
        from renderers.base import create_renderer, load_tokenizer
        from renderers.configs import Qwen35RendererConfig
    except ImportError as exc:  # pragma: no cover - exercised in PRIME runtime.
        raise ActionWeightedCEError("pinned PRIME renderer runtime is unavailable") from exc

    tokenizer = load_tokenizer(str(model_path))
    renderer = create_renderer(
        tokenizer,
        Qwen35RendererConfig(enable_thinking=True),
    )
    rows = _jsonl(corpus_path)
    if len(rows) != EXPECTED_TOTAL_ROWS:
        raise ActionWeightedCEError("materialized corpus must contain exactly 96 rows")
    rendered_rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(rows):
        kind = row.get("kind")
        if kind not in {"executed", "retention"}:
            raise ActionWeightedCEError("materialized corpus row kind drifted")
        messages = strip_message_content(
            deserialize_tool_calls(
                normalize_messages(deepcopy(row["messages_before_action"]), default_role="user")
            )
        )
        teacher = strip_message_content(
            deserialize_tool_calls(
                normalize_messages([deepcopy(row["teacher_message"])], default_role="assistant")
            )
        )[0]
        tools = _normalize_tools(deepcopy(row["tools"]))
        full_messages = [*messages, teacher]
        rendered = renderer.render(full_messages, tools=tools)
        if rendered.multi_modal_data is not None:
            raise ActionWeightedCEError("weighted CE does not permit multimodal training rows")
        lengths = {
            len(rendered.token_ids),
            len(rendered.message_indices),
            len(rendered.sampled_mask),
            len(rendered.is_content),
        }
        if len(lengths) != 1 or not rendered.token_ids or len(rendered.token_ids) > SEQ_LEN:
            raise ActionWeightedCEError("Qwen3.5 rendered streams are misaligned or truncated")
        final_index = len(full_messages) - 1
        active = [
            msg_index == final_index and sampled
            for msg_index, sampled in zip(rendered.message_indices, rendered.sampled_mask)
        ]
        if not any(active) or any(
            enabled and not rendered.is_content[index] for index, enabled in enumerate(active)
        ):
            raise ActionWeightedCEError(
                "final teacher completion lacks exact assistant content mask"
            )
        # No earlier assistant turn may leak into this row's supervision.
        if any(
            enabled and rendered.message_indices[index] != final_index
            for index, enabled in enumerate(active)
        ):
            raise ActionWeightedCEError("prior assistant tokens leaked into the target mask")

        bucket_masks: dict[str, list[bool]]
        action_start_token: int | None = None
        action_boundary_audit: dict[str, Any] | None = None
        if kind == "executed":
            content = teacher.get("content")
            if not isinstance(content, str):
                raise ActionWeightedCEError("executed teacher content is not text")
            action_start_char = final_action_member_start(content)
            # Qwen3.5 emits this final assistant as five exact active spans:
            # <think>, encoded empty reasoning, </think>, encoded content,
            # <|im_end|>.  Bind the source boundary with the fast tokenizer's
            # offsets rather than searching decoded text or re-rendering an
            # invalid, truncated JSON prefix.  A BPE token that crosses the
            # boundary is conservatively assigned to the action bucket.
            content_span = "\n\n" + content
            content_ids, content_offsets = _tokenize_with_offsets(tokenizer, content_span)
            boundary_char = 2 + action_start_char
            boundary_members = [
                index
                for index, (start, end) in enumerate(content_offsets)
                if start <= boundary_char < end
            ]
            if len(boundary_members) != 1:
                raise ActionWeightedCEError(
                    "Qwen3.5 token offsets do not cover the action member exactly once"
                )
            content_boundary_token = boundary_members[0]
            empty_reasoning_ids = list(tokenizer.encode("\n\n", add_special_tokens=False))
            think_id = tokenizer.convert_tokens_to_ids("<think>")
            think_end_id = tokenizer.convert_tokens_to_ids("</think>")
            im_end_id = tokenizer.convert_tokens_to_ids("<|im_end|>")
            expected_active_ids = [
                think_id,
                *empty_reasoning_ids,
                think_end_id,
                *content_ids,
                im_end_id,
            ]
            active_positions = [index for index, enabled in enumerate(active) if enabled]
            if (
                [rendered.token_ids[index] for index in active_positions]
                != expected_active_ids
                or active_positions
                != list(range(active_positions[0], active_positions[-1] + 1))
            ):
                raise ActionWeightedCEError(
                    "executed assistant differs from the exact Qwen3.5 active layout"
                )
            active_boundary = 1 + len(empty_reasoning_ids) + 1 + content_boundary_token
            action_start_token = active_positions[active_boundary]
            boundary_start, boundary_end = content_offsets[content_boundary_token]
            action_source = content[action_start_char:]
            if not action_source.startswith('"action"'):
                raise ActionWeightedCEError("action source suffix is not the final action member")
            action_mask = [
                enabled and index >= action_start_token for index, enabled in enumerate(active)
            ]
            nonaction_mask = [
                enabled and index < action_start_token for index, enabled in enumerate(active)
            ]
            if not any(action_mask) or not any(nonaction_mask):
                raise ActionWeightedCEError("action/non-action token split is empty")
            if not action_mask[max(index for index, enabled in enumerate(active) if enabled)]:
                raise ActionWeightedCEError("Qwen assistant stop token is not in the action suffix")
            action_boundary_audit = {
                "source_action_member_start_char": action_start_char,
                "content_span_boundary_char": boundary_char,
                "content_boundary_token": content_boundary_token,
                "boundary_token_start_char": boundary_start,
                "boundary_token_end_char": boundary_end,
                "boundary_token_crosses_source_boundary": boundary_start < boundary_char,
                "source_prefix_sha256": hashlib.sha256(
                    content[:action_start_char].encode()
                ).hexdigest(),
                "source_action_member_sha256": hashlib.sha256(
                    action_source.encode()
                ).hexdigest(),
                "content_token_ids_sha256": _stream_sha256(content_ids, "int"),
                "proof": "first_qwen_token_whose_offset_intersects_final_top_level_action_member",
            }
            phase = row["phase"]
            bucket_masks = {
                f"{phase}/action": action_mask,
                f"{phase}/nonaction": nonaction_mask,
            }
        else:
            bucket_masks = {RETENTION_PHASE: active}

        env_name = (
            f"c2_ce/{row_index:03d}/{row['phase']}/"
            f"{hashlib.sha256(row['row_id'].encode()).hexdigest()[:12]}"
        )
        rendered_rows.append(
            {
                "row_index": row_index,
                "row_id": row["row_id"],
                "state_id": row["state_id"],
                "phase": row["phase"],
                "kind": kind,
                "env_name": env_name,
                "token_ids": list(rendered.token_ids),
                "active_mask": active,
                "bucket_masks": bucket_masks,
                "action_start_token": action_start_token,
                "action_boundary_audit": action_boundary_audit,
                "rendered_tokens": len(rendered.token_ids),
                "active_tokens": sum(active),
            }
        )
    return rendered_rows, tokenizer


def _materialize_prime_batch(
    *, corpus_path: Path, model_path: Path, batch_path: Path
) -> dict[str, Any]:
    """Exact renderer-to-TrainingBatch bridge, invoked in the PRIME venv."""

    try:
        import msgspec
        from prime_rl.transport.types import TrainingBatch, TrainingSample
    except ImportError as exc:  # pragma: no cover - exercised in PRIME runtime.
        raise ActionWeightedCEError(
            "pinned PRIME msgspec transport runtime is unavailable"
        ) from exc
    rendered_rows, tokenizer = _render_corpus(corpus_path, model_path)
    weight_audit = assign_state_balanced_ce_weights(rendered_rows)
    encoder = msgspec.msgpack.Encoder()
    samples: list[Any] = []
    row_audits: list[dict[str, Any]] = []
    for row in rendered_rows:
        token_ids = row["token_ids"]
        active = row["active_mask"]
        ce_weights = row["ce_weights"]
        if any((weight != 0.0) != enabled for weight, enabled in zip(ce_weights, active)):
            raise ActionWeightedCEError("CE weights and exact assistant target mask differ")
        sample = TrainingSample(
            token_ids=token_ids,
            mask=active,
            logprobs=[0.0] * len(token_ids),
            temperatures=[1.0] * len(token_ids),
            env_name=row["env_name"],
            ref_logprobs=None,
            mm_kwargs=None,
            routed_experts=None,
            mm_token_type_ids=None,
            rl_weights=[0.0] * len(token_ids),
            ce_weights=ce_weights,
            ref_kl_weights=None,
            advantages=None,
        )
        sample_payload = encoder.encode(sample)
        samples.append(sample)
        row_audits.append(
            {
                "row_index": row["row_index"],
                "row_id": row["row_id"],
                "state_id": row["state_id"],
                "phase": row["phase"],
                "kind": row["kind"],
                "env_name": row["env_name"],
                "rendered_tokens": len(token_ids),
                "active_tokens": sum(active),
                "action_tokens": sum(row["bucket_masks"].get(f"{row['phase']}/action", [])),
                "nonaction_tokens": sum(
                    row["bucket_masks"].get(f"{row['phase']}/nonaction", [])
                ),
                "action_start_token": row["action_start_token"],
                "action_boundary_audit": row["action_boundary_audit"],
                "ce_weight_mass": math.fsum(ce_weights),
                "token_ids_sha256": _stream_sha256(token_ids, "int"),
                "mask_sha256": _stream_sha256(active, "bool"),
                "rl_weights_float32_sha256": _stream_sha256([0.0] * len(token_ids), "float32"),
                "ce_weights_float32_sha256": _stream_sha256(ce_weights, "float32"),
                "sample_msgpack_sha256": hashlib.sha256(sample_payload).hexdigest(),
            }
        )
    batch = TrainingBatch(examples=samples, step=FINAL_STEP, run_idx=None)
    payload = encoder.encode(batch)
    if batch_path.exists() or batch_path.is_symlink():
        raise ActionWeightedCEError("TrainingBatch destination must be new")
    batch_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(batch_path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        batch_path.unlink(missing_ok=True)
        raise
    ordered_samples = hashlib.sha256(
        b"".join(bytes.fromhex(row["sample_msgpack_sha256"]) for row in row_audits)
    ).hexdigest()
    body = {
        "schema": RENDER_AUDIT_SCHEMA,
        "status": "ok",
        "renderer": {"name": "qwen3.5", "enable_thinking": True},
        "model_path": str(model_path.resolve()),
        "tokenizer_name_or_path": str(getattr(tokenizer, "name_or_path", "")),
        "seq_len": SEQ_LEN,
        "source_step": SOURCE_STEP,
        "training_step": FINAL_STEP,
        "rows": len(samples),
        "executed_rows": sum(row["kind"] == "executed" for row in rendered_rows),
        "retention_rows": sum(row["kind"] == "retention" for row in rendered_rows),
        "rendered_tokens": sum(len(row["token_ids"]) for row in rendered_rows),
        "assistant_target_tokens": sum(sum(row["active_mask"]) for row in rendered_rows),
        "weight_audit": weight_audit,
        "rl_nonzero_tokens": 0,
        "advantages_present": False,
        "ordered_sample_digest_sha256": ordered_samples,
        "training_batch": {
            "path": str(batch_path.resolve()),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        },
        "row_audits": row_audits,
    }
    return {**body, "audit_body_sha256": hashlib.sha256(canonical_json(body).encode()).hexdigest()}


def _audit_prime_batch(*, batch_path: Path, audit_path: Path) -> dict[str, Any]:
    try:
        import msgspec
        from prime_rl.transport.types import TrainingBatch
    except ImportError as exc:  # pragma: no cover
        raise ActionWeightedCEError(
            "pinned PRIME msgspec transport runtime is unavailable"
        ) from exc
    audit = _read_json(audit_path)
    body = {key: value for key, value in audit.items() if key != "audit_body_sha256"}
    if (
        audit.get("schema") != RENDER_AUDIT_SCHEMA
        or audit.get("audit_body_sha256")
        != hashlib.sha256(canonical_json(body).encode()).hexdigest()
        or audit.get("training_batch", {}).get("sha256") != sha256_file(batch_path)
        or audit.get("training_batch", {}).get("bytes") != batch_path.stat().st_size
    ):
        raise ActionWeightedCEError("render audit or TrainingBatch identity drifted")
    batch = msgspec.msgpack.decode(batch_path.read_bytes(), type=TrainingBatch)
    if (
        batch.step != FINAL_STEP
        or batch.run_idx is not None
        or len(batch.examples) != EXPECTED_TOTAL_ROWS
    ):
        raise ActionWeightedCEError("TrainingBatch header/count drifted")
    encoder = msgspec.msgpack.Encoder()
    observed_digests: list[str] = []
    active_tokens = 0
    weight_mass = 0.0
    for sample in batch.examples:
        length = len(sample.token_ids)
        streams = (
            sample.mask,
            sample.logprobs,
            sample.temperatures,
            sample.rl_weights,
            sample.ce_weights,
        )
        if (
            length <= 0
            or length > SEQ_LEN
            or any(stream is None or len(stream) != length for stream in streams)
            or sample.advantages is not None
            or sample.ref_logprobs is not None
            or sample.ref_kl_weights is not None
            or sample.mm_kwargs is not None
            or sample.mm_token_type_ids is not None
            or sample.routed_experts is not None
            or any(sample.rl_weights)
            or any(value != 0.0 for value in sample.logprobs)
            or any(value != 1.0 for value in sample.temperatures)
            or any(
                (weight != 0.0) != enabled
                for weight, enabled in zip(sample.ce_weights, sample.mask)
            )
        ):
            raise ActionWeightedCEError("TrainingSample is not exact numeric-CE-only data")
        active_tokens += sum(sample.mask)
        weight_mass += math.fsum(sample.ce_weights)
        observed_digests.append(hashlib.sha256(encoder.encode(sample)).hexdigest())
    expected_digests = [row["sample_msgpack_sha256"] for row in audit["row_audits"]]
    ordered = hashlib.sha256(
        b"".join(bytes.fromhex(value) for value in observed_digests)
    ).hexdigest()
    if (
        observed_digests != expected_digests
        or ordered != audit.get("ordered_sample_digest_sha256")
        or active_tokens != audit.get("assistant_target_tokens")
        or not math.isclose(weight_mass, active_tokens, rel_tol=1e-12, abs_tol=1e-8)
    ):
        raise ActionWeightedCEError("TrainingBatch numeric CE semantics drifted")
    return {
        "status": "ok",
        "examples": len(batch.examples),
        "active_tokens": active_tokens,
        "ce_weight_mass": weight_mass,
        "ordered_sample_digest_sha256": ordered,
        "batch_sha256": sha256_file(batch_path),
    }


def _prime_python(prime_root: Path) -> Path:
    python = prime_root / ".venv/bin/python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ActionWeightedCEError("pinned PRIME virtualenv interpreter is absent")
    return python.absolute()


def _prime_environment(prime_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    package_src = Path(__file__).resolve().parents[1]
    paths = [
        str(package_src),
        str(prime_root / "packages/prime-rl-configs/src"),
        str(prime_root / "src"),
        str(prime_root / "deps/renderers"),
    ]
    if environment.get("PYTHONPATH"):
        paths.append(environment["PYTHONPATH"])
    environment["PYTHONPATH"] = os.pathsep.join(paths)
    return environment


def _git_head(path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def validate_prime_source(
    prime_root: str | Path, *, require_launch_patches: bool = False
) -> dict[str, Any]:
    root = Path(prime_root).resolve()
    if not root.is_dir() or root.is_symlink() or _git_head(root) != PRIME_COMMIT:
        raise ActionWeightedCEError("PRIME checkout is not the pinned v0.7 commit")
    identities: dict[str, dict[str, Any]] = {}
    for relative, expected in {
        **PINNED_NUMERIC_CE_FILES,
        **PINNED_RENDERER_FILES,
        **PINNED_RESUME_FILES,
    }.items():
        identity = _file_identity(root / relative)
        if identity["sha256"] != expected:
            raise ActionWeightedCEError(f"pinned CE/renderer semantics drifted: {relative}")
        identities[relative] = identity
    renderer_root = root / "deps/renderers"
    if _git_head(renderer_root) != RENDERERS_COMMIT:
        raise ActionWeightedCEError("Qwen3.5 renderer checkout is not pinned")
    train_path = root / "src/prime_rl/trainer/rl/train.py"
    train_identity = _file_identity(train_path)
    allowed_train = {TRAIN_POSTPATCH_SHA256} if require_launch_patches else {
        TRAIN_PREPATCH_SHA256,
        TRAIN_CROSS_STAGE_SHA256,
        TRAIN_POSTPATCH_SHA256,
    }
    if train_identity["sha256"] not in allowed_train:
        raise ActionWeightedCEError("PRIME RL resume-order trainer source drifted")
    grouped_mm_path = root / "src/prime_rl/trainer/models/layers/lora/multi_linear.py"
    grouped_mm_identity = _file_identity(grouped_mm_path)
    allowed_grouped_mm = (
        {GROUPED_MM_POSTPATCH_SHA256}
        if require_launch_patches
        else {GROUPED_MM_PREPATCH_SHA256, GROUPED_MM_POSTPATCH_SHA256}
    )
    if grouped_mm_identity["sha256"] not in allowed_grouped_mm:
        raise ActionWeightedCEError("PRIME grouped-MM LoRA source drifted")
    types_text = (root / "src/prime_rl/transport/types.py").read_text(encoding="utf-8")
    batch_text = (root / "src/prime_rl/trainer/batch.py").read_text(encoding="utf-8")
    loss_text = (root / "src/prime_rl/trainer/rl/loss.py").read_text(encoding="utf-8")
    train_text = train_path.read_text(encoding="utf-8")
    required = (
        (types_text, "ce_weights: list[float] | None = None"),
        (batch_text, 'STREAM_FILL = {"rl_weights": 1.0, "ce_weights": 0.0'),
        (batch_text, "has_rl_members = any(loss_mask) if rl_w is None"),
        (loss_text, "def ce_loss_fn(inputs: LossInputs)"),
        (loss_text, "nll = nll * inputs.loss_weights"),
        (train_text, "local_ce_scale += int((micro_batch[\"ce_weights\"] != 0).sum())"),
        (train_text, "ce_scale=ce_scale"),
    )
    if any(needle not in text for text, needle in required):
        raise ActionWeightedCEError("native PRIME numeric CE routing semantics are absent")
    if require_launch_patches:
        order_needles = (
            "Registering single run before checkpoint restore",
            'logger.info(f"Initializing optimizer ({config.optim})")',
            "SINGLE_RUN_OPTIMIZER_BINDING_AUDIT",
            "# Optionally, resume training from a checkpoint",
            "DCP_LOADED_LORA_SIGNATURE",
            "Broadcasting startup policy weights",
            "dataloader.wait_for_batch()",
            "RESUMED_LORA_PRE_UPDATE_AUDIT",
            "optimizer.step()",
        )
        positions = [train_text.find(needle) for needle in order_needles]
        if (
            any(position < 0 for position in positions)
            or positions != sorted(positions)
            or train_text.count("multi_run_manager.wait_for_run(0)") != 2
            or "reset_run_parameters" in train_text
        ):
            raise ActionWeightedCEError("reviewed single-run LoRA resume order is absent")
    return {
        "root": str(root),
        "commit": PRIME_COMMIT,
        "renderers_commit": RENDERERS_COMMIT,
        "python": str(_prime_python(root)),
        "files": identities,
        "train": train_identity,
        "grouped_mm": grouped_mm_identity,
        "resume_order": RESUME_ORDER_CONTRACT,
        "launch_patches_required": True,
        "launch_patches_present": (
            train_identity["sha256"] == TRAIN_POSTPATCH_SHA256
            and grouped_mm_identity["sha256"] == GROUPED_MM_POSTPATCH_SHA256
        ),
    }


def _run_prime_child(
    prime_root: Path,
    arguments: Sequence[str],
    *,
    timeout: float = 3600,
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(_prime_python(prime_root)),
            "-m",
            "harness_distill.action_weighted_ce_training",
            *arguments,
        ],
        text=True,
        capture_output=True,
        env=_prime_environment(prime_root),
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-8000:]
        raise ActionWeightedCEError(f"pinned PRIME child failed: {detail}")
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise ActionWeightedCEError("pinned PRIME child returned malformed output") from exc
    if not isinstance(result, dict):
        raise ActionWeightedCEError("pinned PRIME child result is not an object")
    return result


def _validate_prime_configs(prime_root: Path, trainer: Path, control: Path) -> dict[str, Any]:
    script = r"""
import json, sys, tomllib
from pathlib import Path
from prime_rl.configs.trainer import TrainerConfig
from prime_rl.configs.orchestrator import OrchestratorConfig
t = TrainerConfig.model_validate(tomllib.loads(Path(sys.argv[1]).read_text()))
o = OrchestratorConfig.model_validate(tomllib.loads(Path(sys.argv[2]).read_text()))
print(json.dumps({
    "status": "ok",
    "trainer_type": type(t).__name__,
    "control_type": type(o).__name__,
    "trainer_cp": t.model.cp,
    "trainer_dp_replicate": t.model.dp_replicate,
    "trainer_lr": t.optim.lr,
    "control_resume_step": o.ckpt.resume_step,
    "control_lora_rank": o.model.lora.rank,
}, sort_keys=True))
"""
    completed = subprocess.run(
        [str(_prime_python(prime_root)), "-c", script, str(trainer), str(control)],
        text=True,
        capture_output=True,
        env=_prime_environment(prime_root),
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-8000:]
        raise ActionWeightedCEError(f"PRIME rejected generated configs: {detail}")
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise ActionWeightedCEError("PRIME config validation returned malformed output") from exc
    if result.get("status") != "ok":
        raise ActionWeightedCEError("PRIME config validation failed")
    return result


def _toml_array(values: Sequence[str]) -> str:
    return "[" + ", ".join(json.dumps(value) for value in values) + "]"


def _render_trainer_config(
    *, parent_model: Path, output: Path, targets: Sequence[str], topology_name: str
) -> bytes:
    topology = TOPOLOGIES[topology_name]
    return f"""# PRIME-RL v0.7 ({PRIME_COMMIT}); action-weighted numeric CE only
max_steps = {FINAL_STEP}
max_concurrent_runs = 1
output_dir = {json.dumps(str(output))}
matmul_precision = "high"
enable_token_export = true

[env_vars]
FLA_TILELANG = "0"
WANDB_MODE = "disabled"

[model]
name = {json.dumps(str(parent_model))}
seq_len = {SEQ_LEN}
impl = "hf"
attn = "flash_attention_2"
optimization_dtype = "bfloat16"
reduce_dtype = "bfloat16"
optim_cpu_offload = true
dp_replicate = {topology['data_parallel_replicate']}
ep = 1
cp = {topology['context_parallel_size']}
cp_style = "ulysses"
fused_lm_head_token_chunk_size = 1024

[model.ac]
mode = "full"
freq = 1

[model.ac_offloading]
pin_memory = true
max_inflight_activations = 5

[model.lora]
rank = {LORA_RANK}
alpha = {LORA_ALPHA}
dropout = 0.0
target_modules = {_toml_array(targets)}
modules_to_save = []

[data]

[loss]
type = "default"

[optim]
type = "adamw"
lr = {LEARNING_RATE}
weight_decay = 0.01
max_norm = 1.0

[scheduler]
type = "constant"

[ckpt]
interval = 1
resume_step = {SOURCE_STEP}
keep_last = 2
skip_optimizer = true
skip_scheduler = true
skip_dataloader = true
skip_progress = false

[ckpt.weights]
save_sharded = true
save_format = "safetensors"
save_adapter_separately = true

[weight_broadcast]
type = "filesystem"
save_sharded = true
save_format = "safetensors"

[rollout_transport]
type = "filesystem"

[log]
level = "info"
ranks_filter = [0]
""".encode()


def _render_control_config(*, parent_model: Path, run_output: Path) -> bytes:
    # This file is registration metadata for SinglePacker.  No orchestrator,
    # environment, inference server, or fresh rollout is launched.
    return f"""# Trainer-only replay control registration; never launch this as an orchestrator.
output_dir = {json.dumps(str(run_output))}
batch_size = {EXPECTED_TOTAL_ROWS}
group_size = 1
seq_len = {SEQ_LEN}
max_steps = {FINAL_STEP}

[model]
name = {json.dumps(str(parent_model))}

[model.lora]
name = "c2-action-weighted-ce"
rank = {LORA_RANK}
alpha = {LORA_ALPHA}

[optim]
lr = {LEARNING_RATE}

[ckpt]
interval = 1
resume_step = {SOURCE_STEP}
keep_last = 2
skip_progress = false

[weight_broadcast]
type = "filesystem"

[renderer]
name = "qwen3.5"
enable_thinking = true
""".encode()


def _validate_generated_configs(
    *,
    trainer_path: Path,
    control_path: Path,
    parent_model: Path,
    output: Path,
    targets: Sequence[str],
    topology_name: str,
) -> None:
    if trainer_path.read_bytes() != _render_trainer_config(
        parent_model=parent_model,
        output=output,
        targets=targets,
        topology_name=topology_name,
    ):
        raise ActionWeightedCEError("trainer config bytes differ from the reviewed template")
    if control_path.read_bytes() != _render_control_config(
        parent_model=parent_model, run_output=output / "run_default"
    ):
        raise ActionWeightedCEError("control config bytes differ from the reviewed template")
    config = tomllib.loads(trainer_path.read_text(encoding="utf-8"))
    topology = TOPOLOGIES[topology_name]
    if (
        config.get("max_steps") != FINAL_STEP
        or config.get("max_concurrent_runs") != 1
        or config.get("enable_token_export") is not True
        or config.get("model", {}).get("cp") != topology["context_parallel_size"]
        or config.get("model", {}).get("dp_replicate")
        != topology["data_parallel_replicate"]
        or config.get("model", {}).get("lora")
        != {
            "rank": LORA_RANK,
            "alpha": LORA_ALPHA,
            "dropout": 0.0,
            "target_modules": list(targets),
            "modules_to_save": [],
        }
        or config.get("optim", {}).get("lr") != LEARNING_RATE
        or config.get("ckpt", {}).get("resume_step") != SOURCE_STEP
        or config.get("ckpt", {}).get("skip_optimizer") is not True
        or config.get("ckpt", {}).get("skip_scheduler") is not True
        or config.get("ckpt", {}).get("skip_dataloader") is not True
        or config.get("ckpt", {}).get("skip_progress") is not False
        or config.get("rollout_transport") != {"type": "filesystem"}
    ):
        raise ActionWeightedCEError("generated trainer config policy drifted")


def trainer_command(prime_root: str | Path, training_output: str | Path) -> list[str]:
    prime = Path(prime_root).resolve()
    output = Path(training_output).resolve()
    return [
        str(prime / ".venv/bin/torchrun"),
        "--standalone",
        "--nnodes=1",
        f"--nproc-per-node={TRAINER_WORLD_SIZE}",
        "--role=trainer",
        f"--log-dir={output / 'logs/trainer/torchrun'}",
        "--redirects=3",
        "--tee=3",
        "--local-ranks-filter=0",
        "-m",
        "prime_rl.trainer.rl.train",
        "@",
        str(output / "configs/trainer.toml"),
    ]


def _normalized_corpus(collection: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for kind, source_rows in (
        ("executed", collection["selection"]),
        ("retention", collection["retention"]),
    ):
        for row in source_rows:
            rows.append(
                {
                    "kind": kind,
                    "row_id": row["row_id"],
                    "state_id": row["state_id"],
                    "phase": row["phase"],
                    "messages_before_action": deepcopy(row["messages_before_action"]),
                    "teacher_message": deepcopy(row["teacher_message"]),
                    "tools": deepcopy(row["tools"]),
                }
            )
    return rows


def _corpus_payload(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join((canonical_json(dict(row)) + "\n").encode() for row in rows)


def _select_topology(name: str, fallback_reason: str | None) -> dict[str, Any]:
    if name not in TOPOLOGIES:
        raise ActionWeightedCEError(f"unreviewed trainer topology: {name}")
    topology = TOPOLOGIES[name]
    if topology["fallback"]:
        if not isinstance(fallback_reason, str) or len(fallback_reason.strip()) < 12:
            raise ActionWeightedCEError("CP4 fallback requires a specific recorded reason")
    elif fallback_reason is not None:
        raise ActionWeightedCEError("fallback reason is forbidden for preferred CP2xDP2")
    return topology


def prepare_action_weighted_ce_training(
    *,
    collection_manifest_path: str | Path,
    parent_receipt_path: str | Path,
    output_dir: str | Path,
    prime_root: str | Path,
    artifact_git_sha: str,
    topology_name: str = "cp2_dp2",
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    output = Path(output_dir).resolve()
    if output.exists() or output.is_symlink():
        raise ActionWeightedCEError("action-weighted CE output must be a new directory")
    if not str(output).startswith("/data/"):
        raise ActionWeightedCEError("action-weighted CE output must be on /data")
    if _HEX40.fullmatch(artifact_git_sha) is None:
        raise ActionWeightedCEError("artifact Git SHA must be 40 lowercase hex characters")
    topology = _select_topology(topology_name, fallback_reason)
    collection = validate_collection_manifest(collection_manifest_path)
    parent = validate_step25_parent(parent_receipt_path)
    prime = validate_prime_source(prime_root)
    prime_path = Path(prime["root"])
    parent_model = Path(parent["plan"]["parent_model"]).resolve()
    if not parent_model.is_dir() or parent_model.is_symlink():
        raise ActionWeightedCEError("sealed parent model/tokenizer snapshot is absent")
    parent_candidate = parent["receipt"]["candidate"]
    if parent_candidate.get("tree_sha256") != PARENT_ADAPTER_TREE_SHA256:
        raise ActionWeightedCEError("sealed step-25 adapter identity drifted")
    targets = _adapter_targets(Path(parent_candidate["path"]))

    output.mkdir(parents=True, mode=0o700)
    collection_copy = output / "collection"
    collection_copy.mkdir(mode=0o700)
    source_manifest = Path(collection["manifest_path"])
    for source in (
        source_manifest,
        Path(collection["selection_path"]),
        Path(collection["retention_path"]),
        Path(collection["audits_path"]),
    ):
        _write_new(collection_copy / source.name, source.read_bytes())
    frozen_collection = validate_collection_manifest(collection_copy / source_manifest.name)
    if frozen_collection["manifest_sha256"] != collection["manifest_sha256"]:
        raise ActionWeightedCEError("frozen collection copy differs from source")

    materialized = output / "materialized"
    materialized.mkdir(mode=0o700)
    corpus_rows = _normalized_corpus(frozen_collection)
    corpus_path = materialized / "corpus.jsonl"
    _write_new(corpus_path, _corpus_payload(corpus_rows))
    training_output = output / "training/prime_output"
    batch_path = training_output / f"run_default/rollouts/step_{FINAL_STEP}/train_rollouts.bin"
    render_audit = _run_prime_child(
        prime_path,
        [
            "_prime-materialize",
            "--corpus",
            str(corpus_path),
            "--model",
            str(parent_model),
            "--batch",
            str(batch_path),
        ],
    )
    render_audit_path = materialized / "render_audit.json"
    _write_new(render_audit_path, (canonical_json(render_audit) + "\n").encode())
    batch_audit = _run_prime_child(
        prime_path,
        [
            "_prime-audit",
            "--batch",
            str(batch_path),
            "--audit",
            str(render_audit_path),
        ],
    )

    source_dcp = parent["receipt"]["final_dcp"]
    if source_dcp.get("tree_sha256") != PARENT_DCP_TREE_SHA256:
        raise ActionWeightedCEError("sealed step-25 DCP identity drifted")
    bridge = _link_tree(
        Path(source_dcp["path"]),
        training_output / f"checkpoints/step_{SOURCE_STEP}/trainer",
    )
    trainer_path = training_output / "configs/trainer.toml"
    control_path = training_output / "run_default/control/orch.toml"
    _write_new(
        trainer_path,
        _render_trainer_config(
            parent_model=parent_model,
            output=training_output,
            targets=targets,
            topology_name=topology_name,
        ),
    )
    _write_new(
        control_path,
        _render_control_config(
            parent_model=parent_model,
            run_output=training_output / "run_default",
        ),
    )
    _validate_generated_configs(
        trainer_path=trainer_path,
        control_path=control_path,
        parent_model=parent_model,
        output=training_output,
        targets=targets,
        topology_name=topology_name,
    )
    config_runtime = _validate_prime_configs(prime_path, trainer_path, control_path)
    body = {
        "schema": PLAN_SCHEMA,
        "status": "prepared",
        "scientific_label": SCIENTIFIC_LABEL,
        "objective": "weighted_behavioral_cloning",
        "on_policy": False,
        "policy_gradient": False,
        "native_prime_component": "ce",
        "artifact_git_sha": artifact_git_sha,
        "prime": prime,
        "parent_receipt_path": str(Path(parent_receipt_path).resolve()),
        "parent_receipt_sha256": PARENT_RECEIPT_SHA256,
        "parent_receipt_body_sha256": PARENT_RECEIPT_BODY_SHA256,
        "parent_model": str(parent_model),
        "parent_candidate": parent_candidate,
        "source_dcp": source_dcp,
        "checkpoint_bridge": bridge,
        "frozen_collection_manifest": str((collection_copy / source_manifest.name).resolve()),
        "frozen_collection_manifest_sha256": frozen_collection["manifest_sha256"],
        "frozen_collection_manifest_body_sha256": frozen_collection["manifest_body_sha256"],
        "collection_counts": {
            "executed": EXPECTED_EXECUTED_ROWS,
            "retention": EXPECTED_RETENTION_ROWS,
            "total": EXPECTED_TOTAL_ROWS,
            "phases": EXECUTED_PHASE_COUNTS,
            "unique_states": frozen_collection["state_count"],
            "evaluation": 0,
            "heldout": 0,
        },
        "corpus_path": str(corpus_path),
        "corpus_sha256": sha256_file(corpus_path),
        "render_audit_path": str(render_audit_path),
        "render_audit_sha256": sha256_file(render_audit_path),
        "render_audit_body_sha256": render_audit["audit_body_sha256"],
        "weight_audit": render_audit["weight_audit"],
        "training_batch": render_audit["training_batch"],
        "training_batch_audit": batch_audit,
        "trainer_config": _file_identity(trainer_path),
        "control_config": _file_identity(control_path),
        "prime_config_validation": config_runtime,
        "training_output": str(training_output),
        "candidate_path": str(training_output / f"weights/step_{FINAL_STEP}/lora_adapters"),
        "candidate_name": "step26-action-weighted-ce",
        "trainer_topology": topology,
        "topology_fallback_reason": fallback_reason,
        "renderer": {"name": "qwen3.5", "enable_thinking": True},
        "lora": {"rank": LORA_RANK, "alpha": LORA_ALPHA, "targets": targets},
        "source_step": SOURCE_STEP,
        "final_step": FINAL_STEP,
        "optimizer_updates": 1,
        "learning_rate": LEARNING_RATE,
        "fresh_optimizer": True,
        "fresh_scheduler": True,
        "fresh_dataloader": True,
        "resume_order": RESUME_ORDER_CONTRACT,
        "process_policy": {
            "trainer_only": True,
            "orchestrator": False,
            "inference": False,
            "environment": False,
            "new_rollouts": False,
        },
        "trainer_command": trainer_command(prime_path, training_output),
        "launch_authorized": True,
    }
    return _publish(output / "training/plan.json", body, hash_field="plan_body_sha256")


def _validate_render_audit(path: Path, *, batch_path: Path) -> dict[str, Any]:
    audit = _read_json(path)
    body = {key: value for key, value in audit.items() if key != "audit_body_sha256"}
    weights = audit.get("weight_audit")
    if (
        audit.get("schema") != RENDER_AUDIT_SCHEMA
        or audit.get("status") != "ok"
        or audit.get("audit_body_sha256")
        != hashlib.sha256(canonical_json(body).encode()).hexdigest()
        or audit.get("renderer") != {"name": "qwen3.5", "enable_thinking": True}
        or audit.get("rows") != EXPECTED_TOTAL_ROWS
        or audit.get("executed_rows") != EXPECTED_EXECUTED_ROWS
        or audit.get("retention_rows") != EXPECTED_RETENTION_ROWS
        or audit.get("rl_nonzero_tokens") != 0
        or audit.get("advantages_present") is not False
        or audit.get("training_batch", {}).get("path") != str(batch_path.resolve())
        or audit.get("training_batch", {}).get("sha256") != sha256_file(batch_path)
        or not isinstance(weights, Mapping)
        or weights.get("unique_states") != EXPECTED_TOTAL_ROWS
        or weights.get("state_cap_fraction") != float(MAX_STATE_FRACTION)
        or weights.get("max_state_fraction", 1.0) > float(MAX_STATE_FRACTION) + 1e-12
        or not math.isclose(weights.get("mean_nonzero_ce_weight", 0.0), 1.0, abs_tol=1e-12)
        or set(weights.get("buckets", {})) != set(BUCKET_FRACTIONS)
    ):
        raise ActionWeightedCEError("render/weight audit policy drifted")
    for bucket, fraction in BUCKET_FRACTIONS.items():
        item = weights["buckets"][bucket]
        if item.get("target_fraction") != f"{fraction.numerator}/{fraction.denominator}":
            raise ActionWeightedCEError(f"render audit target fraction drifted: {bucket}")
    row_audits = audit.get("row_audits", [])
    if len(row_audits) != EXPECTED_TOTAL_ROWS:
        raise ActionWeightedCEError("render audit row inventory drifted")
    for row in row_audits:
        boundary = row.get("action_boundary_audit")
        if row.get("kind") == "executed":
            if (
                not isinstance(boundary, Mapping)
                or boundary.get("proof")
                != "first_qwen_token_whose_offset_intersects_final_top_level_action_member"
                or not isinstance(row.get("action_start_token"), int)
                or row.get("action_tokens", 0) <= 0
                or row.get("nonaction_tokens", 0) <= 0
                or boundary.get("boundary_token_start_char", -1)
                > boundary.get("content_span_boundary_char", -1)
                or boundary.get("boundary_token_end_char", -1)
                <= boundary.get("content_span_boundary_char", -1)
            ):
                raise ActionWeightedCEError("render audit action-boundary proof drifted")
        elif row.get("kind") != "retention" or boundary is not None:
            raise ActionWeightedCEError("render audit row kind/boundary drifted")
    return audit


def validate_action_weighted_ce_plan(
    path: str | Path, *, require_launch_patches: bool = False
) -> dict[str, Any]:
    plan_path = Path(path).resolve()
    plan = _self_hashed(plan_path, schema=PLAN_SCHEMA, hash_field="plan_body_sha256")
    topology = plan.get("trainer_topology")
    topology_name = topology.get("name") if isinstance(topology, Mapping) else None
    _select_topology(str(topology_name), plan.get("topology_fallback_reason"))
    if (
        plan.get("status") != "prepared"
        or plan.get("scientific_label") != SCIENTIFIC_LABEL
        or plan.get("objective") != "weighted_behavioral_cloning"
        or plan.get("on_policy") is not False
        or plan.get("policy_gradient") is not False
        or plan.get("native_prime_component") != "ce"
        or _HEX40.fullmatch(str(plan.get("artifact_git_sha"))) is None
        or plan.get("parent_receipt_sha256") != PARENT_RECEIPT_SHA256
        or plan.get("parent_receipt_body_sha256") != PARENT_RECEIPT_BODY_SHA256
        or plan.get("collection_counts")
        != {
            "executed": EXPECTED_EXECUTED_ROWS,
            "retention": EXPECTED_RETENTION_ROWS,
            "total": EXPECTED_TOTAL_ROWS,
            "phases": EXECUTED_PHASE_COUNTS,
            "unique_states": EXPECTED_TOTAL_ROWS,
            "evaluation": 0,
            "heldout": 0,
        }
        or plan.get("renderer") != {"name": "qwen3.5", "enable_thinking": True}
        or plan.get("lora", {}).get("rank") != LORA_RANK
        or plan.get("lora", {}).get("alpha") != LORA_ALPHA
        or plan.get("source_step") != SOURCE_STEP
        or plan.get("final_step") != FINAL_STEP
        or plan.get("optimizer_updates") != 1
        or plan.get("learning_rate") != LEARNING_RATE
        or plan.get("fresh_optimizer") is not True
        or plan.get("fresh_scheduler") is not True
        or plan.get("fresh_dataloader") is not True
        or plan.get("resume_order") != RESUME_ORDER_CONTRACT
        or plan.get("process_policy")
        != {
            "trainer_only": True,
            "orchestrator": False,
            "inference": False,
            "environment": False,
            "new_rollouts": False,
        }
        or plan.get("launch_authorized") is not True
    ):
        raise ActionWeightedCEError("action-weighted CE plan policy drifted")
    expected_plan = Path(plan["training_output"]).resolve().parent / "plan.json"
    if plan_path != expected_plan:
        raise ActionWeightedCEError("action-weighted CE plan is outside its output training dir")
    artifacts = (
        (Path(plan["parent_receipt_path"]), plan["parent_receipt_sha256"]),
        (Path(plan["frozen_collection_manifest"]), plan["frozen_collection_manifest_sha256"]),
        (Path(plan["corpus_path"]), plan["corpus_sha256"]),
        (Path(plan["render_audit_path"]), plan["render_audit_sha256"]),
        (Path(plan["training_batch"]["path"]), plan["training_batch"]["sha256"]),
        (Path(plan["trainer_config"]["path"]), plan["trainer_config"]["sha256"]),
        (Path(plan["control_config"]["path"]), plan["control_config"]["sha256"]),
    )
    if any(sha256_file(source) != expected for source, expected in artifacts):
        raise ActionWeightedCEError("plan-bound artifact changed after publication")
    parent = validate_step25_parent(plan["parent_receipt_path"])
    if (
        plan.get("parent_model") != parent["plan"]["parent_model"]
        or plan.get("parent_candidate") != parent["receipt"]["candidate"]
        or plan.get("source_dcp") != parent["receipt"]["final_dcp"]
    ):
        raise ActionWeightedCEError("plan parent lineage differs from sealed step 25")
    collection = validate_collection_manifest(plan["frozen_collection_manifest"])
    corpus_rows = _normalized_corpus(collection)
    if Path(plan["corpus_path"]).read_bytes() != _corpus_payload(corpus_rows):
        raise ActionWeightedCEError("materialized corpus differs from frozen collection")
    batch_path = Path(plan["training_batch"]["path"])
    render_audit = _validate_render_audit(Path(plan["render_audit_path"]), batch_path=batch_path)
    if (
        render_audit["audit_body_sha256"] != plan.get("render_audit_body_sha256")
        or render_audit["weight_audit"] != plan.get("weight_audit")
    ):
        raise ActionWeightedCEError("plan weight/render audit drifted")
    prime_root = Path(plan["prime"]["root"])
    prime = validate_prime_source(prime_root, require_launch_patches=require_launch_patches)
    if prime["commit"] != plan.get("prime", {}).get("commit"):
        raise ActionWeightedCEError("plan PRIME identity drifted")
    batch_audit = _run_prime_child(
        prime_root,
        ["_prime-audit", "--batch", str(batch_path), "--audit", plan["render_audit_path"]],
    )
    if batch_audit != plan.get("training_batch_audit"):
        raise ActionWeightedCEError("plan TrainingBatch audit drifted")
    targets = plan["lora"]["targets"]
    _validate_generated_configs(
        trainer_path=Path(plan["trainer_config"]["path"]),
        control_path=Path(plan["control_config"]["path"]),
        parent_model=Path(plan["parent_model"]),
        output=Path(plan["training_output"]),
        targets=targets,
        topology_name=str(topology_name),
    )
    runtime = _validate_prime_configs(
        prime_root,
        Path(plan["trainer_config"]["path"]),
        Path(plan["control_config"]["path"]),
    )
    if runtime != plan.get("prime_config_validation"):
        raise ActionWeightedCEError("PRIME config validation result drifted")
    source = _require_dcp_inventory(
        Path(plan["source_dcp"]["path"]),
        expected_identity=plan["source_dcp"],
        trainer_world_size=TRAINER_WORLD_SIZE,
    )
    bridge = _require_dcp_inventory(
        Path(plan["checkpoint_bridge"]["destination"]["path"]),
        expected_identity=plan["checkpoint_bridge"]["destination"],
        trainer_world_size=TRAINER_WORLD_SIZE,
    )
    if any(source[key] != bridge[key] for key in ("files", "bytes", "tree_sha256")):
        raise ActionWeightedCEError("step-25 checkpoint bridge differs from sealed source")
    expected_command = trainer_command(prime_root, plan["training_output"])
    if plan.get("trainer_command") != expected_command:
        raise ActionWeightedCEError("trainer command drifted")
    return plan


def _trainer_log_audit(log_root: Path, topology: Mapping[str, Any]) -> dict[str, Any]:
    if not log_root.is_dir() or log_root.is_symlink():
        raise ActionWeightedCEError("torchrun trainer log directory is absent or unsafe")
    texts: list[str] = []
    for path in sorted(log_root.rglob("*")):
        if path.is_symlink():
            raise ActionWeightedCEError("trainer log tree contains a symlink")
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
    joined = "\n".join(texts)
    required = (
        topology["mesh_log"],
        "Registering single run before checkpoint restore",
        "SINGLE_RUN_OPTIMIZER_BINDING_AUDIT",
        f"Resuming training from checkpoint step {SOURCE_STEP}",
        "DCP_LOADED_LORA_SIGNATURE",
        f"Starting from step {FINAL_STEP}",
        "Broadcasting startup policy weights",
        "RESUMED_LORA_PRE_UPDATE_AUDIT",
        f"Step {FINAL_STEP} |",
        "Writing final checkpoint",
        "Writing final weight checkpoint",
        "RL trainer finished!",
    )
    forbidden = (
        f"Step {FINAL_STEP + 1} |",
        "Traceback (most recent call last)",
        "RuntimeError:",
        "CUDA out of memory",
    )
    if any(item not in joined for item in required) or any(item in joined for item in forbidden):
        raise ActionWeightedCEError("trainer logs do not prove one clean numeric-CE step")

    binding_matches = set(
        re.findall(
            r"SINGLE_RUN_OPTIMIZER_BINDING_AUDIT parameters=([1-9][0-9]*) status=ok",
            joined,
        )
    )
    loaded_matches = set(
        re.findall(
            r"DCP_LOADED_LORA_SIGNATURE dcp_step=([0-9]+) "
            r"digest=([0-9a-f]{64}) lora_b_nonzero=([1-9][0-9]*) status=ok",
            joined,
        )
    )
    pre_update_matches = set(
        re.findall(
            r"RESUMED_LORA_PRE_UPDATE_AUDIT dcp_step=([0-9]+) "
            r"digest=([0-9a-f]{64}) lora_b_nonzero=([1-9][0-9]*) "
            r"parameters_per_rank=([1-9][0-9]*) status=ok",
            joined,
        )
    )
    if (
        len(binding_matches) != 1
        or len(loaded_matches) != 1
        or len(pre_update_matches) != 1
    ):
        raise ActionWeightedCEError("trainer logs lack a unique LoRA resume audit")
    binding_parameters = int(next(iter(binding_matches)))
    loaded_step, loaded_digest, loaded_nonzero = next(iter(loaded_matches))
    pre_step, pre_digest, pre_nonzero, pre_parameters = next(iter(pre_update_matches))
    if (
        int(loaded_step) != SOURCE_STEP
        or int(pre_step) != SOURCE_STEP
        or loaded_digest != pre_digest
        or int(loaded_nonzero) != int(pre_nonzero)
        or binding_parameters != int(pre_parameters)
    ):
        raise ActionWeightedCEError("DCP-loaded LoRA signature changed before the update")

    ordered_markers = (
        "Registering single run before checkpoint restore",
        "Initializing optimizer",
        "SINGLE_RUN_OPTIMIZER_BINDING_AUDIT",
        f"Resuming training from checkpoint step {SOURCE_STEP}",
        "DCP_LOADED_LORA_SIGNATURE",
        "Broadcasting startup policy weights",
        "RESUMED_LORA_PRE_UPDATE_AUDIT",
        f"Step {FINAL_STEP} |",
    )
    if not any(
        all(marker in text for marker in ordered_markers)
        and [text.find(marker) for marker in ordered_markers]
        == sorted(text.find(marker) for marker in ordered_markers)
        for text in texts
    ):
        raise ActionWeightedCEError("trainer log milestones violate the reviewed resume order")
    return {
        "tree": _tree_identity(log_root),
        "required_milestones": list(required),
        "forbidden_milestones_absent": list(forbidden),
        "resume_lora_pre_update_audit": {
            "dcp_step": SOURCE_STEP,
            "sampled_signature_sha256": loaded_digest,
            "lora_b_nonzero": int(loaded_nonzero),
            "parameters_per_rank": int(pre_parameters),
            "optimizer_bound_parameters": binding_parameters,
            "signature_unchanged": True,
            "milestone_order": list(ordered_markers),
        },
    }


def _token_export_audit(
    export_dir: Path, *, render_audit: Mapping[str, Any], topology: Mapping[str, Any]
) -> dict[str, Any]:
    if not export_dir.is_dir() or export_dir.is_symlink():
        raise ActionWeightedCEError("stable per-token export directory is absent")
    stable = export_dir / "STABLE"
    if not stable.is_file() or stable.is_symlink():
        raise ActionWeightedCEError("per-token exports are not marked STABLE")
    rank_files = sorted(export_dir.glob("rank_*.jsonl"))
    if len(rank_files) != topology["data_parallel_size"]:
        raise ActionWeightedCEError("per-token export rank-file count differs from topology")
    expected = {row["env_name"]: row for row in render_audit["row_audits"]}
    observed: dict[str, dict[str, Any]] = {}
    for rank_path in rank_files:
        for export in _jsonl(rank_path):
            env_name = export.get("env_name")
            token_ids = export.get("token_ids")
            mask = export.get("loss_mask")
            rl_weights = export.get("rl_weights")
            ce_weights = export.get("ce_weights")
            length = len(token_ids) if isinstance(token_ids, list) else -1
            aligned = (
                token_ids,
                mask,
                export.get("advantages"),
                export.get("inference_logprobs"),
                export.get("trainer_logprobs"),
                export.get("entropy"),
                export.get("mismatch_kl"),
                export.get("log_importance_ratio"),
                export.get("importance_ratio"),
                export.get("prob_delta"),
                export.get("is_masked"),
                export.get("is_masked_high"),
                export.get("is_masked_low"),
                rl_weights,
                ce_weights,
                export.get("ref_kl_weights"),
            )
            if (
                export.get("schema_version") != 1
                or export.get("step") != FINAL_STEP
                or export.get("export_step") != FINAL_STEP
                or export.get("run_id") != "run_default"
                or not isinstance(env_name, str)
                or env_name not in expected
                or env_name in observed
                or length <= 0
                or any(not isinstance(values, list) or len(values) != length for values in aligned)
                or any(rl_weights)
                or any(value is not None for value in export["ref_kl_weights"])
                or any(value is not None for value in export["mismatch_kl"])
                or any(value is not None for value in export["log_importance_ratio"])
                or any(value is not None for value in export["importance_ratio"])
                or any(value is not None for value in export["prob_delta"])
                or any(value is not None for value in export["is_masked"])
                or any(value is not None for value in export["is_masked_high"])
                or any(value is not None for value in export["is_masked_low"])
                or any(value != 0.0 for value in export["advantages"])
                or any(value != 0.0 for value in export["inference_logprobs"])
                or any((weight != 0.0) != enabled for weight, enabled in zip(ce_weights, mask))
            ):
                raise ActionWeightedCEError(
                    "per-token export is not exact CE-only execution evidence"
                )
            expected_row = expected[env_name]
            hashes = {
                "token_ids_sha256": _stream_sha256(token_ids, "int"),
                "mask_sha256": _stream_sha256(mask, "bool"),
                "rl_weights_float32_sha256": _stream_sha256(rl_weights, "float32"),
                "ce_weights_float32_sha256": _stream_sha256(ce_weights, "float32"),
            }
            if any(expected_row.get(key) != value for key, value in hashes.items()):
                raise ActionWeightedCEError(
                    "executed token stream differs from planned TrainingSample"
                )
            observed[env_name] = {
                **hashes,
                "tokens": length,
                "active_tokens": sum(mask),
                "ce_weight_mass": math.fsum(ce_weights),
            }
    if set(observed) != set(expected):
        raise ActionWeightedCEError("per-token exports do not cover all 96 planned samples once")
    total_active = sum(row["active_tokens"] for row in observed.values())
    total_mass = math.fsum(row["ce_weight_mass"] for row in observed.values())
    planned_active = render_audit["weight_audit"]["nonzero_ce_tokens"]
    # Export weights pass through torch.float32, so allow its expected rounding.
    if total_active != planned_active or not math.isclose(
        total_mass, planned_active, rel_tol=2e-7, abs_tol=2e-4
    ):
        raise ActionWeightedCEError("executed CE denominator/mass differs from the plan")
    return {
        "tree": _tree_identity(export_dir),
        "stable_sha256": sha256_file(stable),
        "rank_files": len(rank_files),
        "samples": len(observed),
        "active_tokens": total_active,
        "ce_weight_mass_float32": total_mass,
        "mean_nonzero_ce_weight_float32": total_mass / total_active,
        "rl_nonzero_tokens": 0,
    }


def _patch_evidence(root: Path) -> dict[str, Any]:
    expected = {
        "cross_stage_patch.json": {
            "patch_sha256": "fe9b1cd31302f8bdafb95a5835424b84a8b54c48b667b588910402ad5ebbc246",
            "target_sha256": TRAIN_CROSS_STAGE_SHA256,
        },
        "grouped_mm_contiguous_grad_patch.json": {
            "patch_sha256": "642705146bf30890d210d0a3fe27fb2343d128a44b76e3b97a5f3214d3f7e486",
            "target_sha256": GROUPED_MM_POSTPATCH_SHA256,
        },
        "single_run_lora_resume_order_patch.json": {
            "patch_sha256": "d9718426220042abbc2390bfff8536dd7ef78c909962ef8a454ece60227068bd",
            "target_sha256": TRAIN_POSTPATCH_SHA256,
        },
    }
    evidence: dict[str, Any] = {}
    for name, values in expected.items():
        path = root / name
        payload = _read_json(path)
        if (
            payload.get("status") != "ok"
            or payload.get("mode") not in {"applied", "already_applied"}
            or payload.get("prime_commit") != PRIME_COMMIT
            or any(payload.get(key) != value for key, value in values.items())
        ):
            raise ActionWeightedCEError(f"reviewed PRIME patch evidence drifted: {name}")
        evidence[name] = {"file": _file_identity(path), "payload": payload}
    return evidence


def write_action_weighted_ce_receipt(
    *, plan_path: str | Path, executor_git_sha: str
) -> dict[str, Any]:
    if _HEX40.fullmatch(executor_git_sha) is None:
        raise ActionWeightedCEError("executor Git SHA must be 40 lowercase hex characters")
    plan_path = Path(plan_path).resolve()
    plan = validate_action_weighted_ce_plan(plan_path, require_launch_patches=True)
    if executor_git_sha != plan["artifact_git_sha"]:
        raise ActionWeightedCEError("artifact and execution source Git identities differ")
    output = Path(plan["training_output"])
    if (output / f"checkpoints/step_{FINAL_STEP + 1}").exists() or (
        output / f"weights/step_{FINAL_STEP + 1}"
    ).exists():
        raise ActionWeightedCEError("trainer executed more than one optimizer update")
    candidate_path = Path(plan["candidate_path"])
    candidate = _tree_identity(candidate_path)
    stable = candidate_path.parent / "STABLE"
    if not stable.is_file() or stable.is_symlink():
        raise ActionWeightedCEError("final adapter is not marked STABLE")
    if _adapter_targets(candidate_path) != sorted(plan["lora"]["targets"]):
        raise ActionWeightedCEError("final adapter target modules differ from the plan")
    final_dcp = _require_dcp_inventory(
        output / f"checkpoints/step_{FINAL_STEP}/trainer",
        trainer_world_size=TRAINER_WORLD_SIZE,
        includes_dataloader=False,
    )
    render_audit = _validate_render_audit(
        Path(plan["render_audit_path"]), batch_path=Path(plan["training_batch"]["path"])
    )
    token_exports = _token_export_audit(
        output / f"run_default/token_exports/step_{FINAL_STEP}",
        render_audit=render_audit,
        topology=plan["trainer_topology"],
    )
    logs = _trainer_log_audit(output / "logs/trainer/torchrun", plan["trainer_topology"])
    patches = _patch_evidence(plan_path.parent / "launch_evidence")
    body = {
        "schema": RECEIPT_SCHEMA,
        "status": "ok",
        "scientific_label": SCIENTIFIC_LABEL,
        "artifact_source_git_sha": plan["artifact_git_sha"],
        "execution_source_git_sha": executor_git_sha,
        "prime_commit": PRIME_COMMIT,
        "plan_path": str(plan_path),
        "plan_sha256": sha256_file(plan_path),
        "plan_body_sha256": plan["plan_body_sha256"],
        "parent_receipt_sha256": PARENT_RECEIPT_SHA256,
        "parent_candidate": "step25-sol-dagger-sft",
        "source_step": SOURCE_STEP,
        "final_step": FINAL_STEP,
        "optimizer_updates": 1,
        "learning_rate": LEARNING_RATE,
        "fresh_optimizer": True,
        "fresh_scheduler": True,
        "fresh_dataloader": True,
        "resume_order": RESUME_ORDER_CONTRACT,
        "objective": "weighted_behavioral_cloning",
        "on_policy": False,
        "policy_gradient": False,
        "native_prime_component": "ce",
        "trainer_topology": plan["trainer_topology"],
        "collection_counts": plan["collection_counts"],
        "weight_audit": plan["weight_audit"],
        "training_batch": plan["training_batch"],
        "token_execution_audit": token_exports,
        "prime_launch_patches": patches,
        "trainer_logs": logs,
        "source_dcp": plan["source_dcp"],
        "final_dcp": final_dcp,
        "trainer_config": plan["trainer_config"],
        "candidate": {
            "name": plan["candidate_name"],
            "update": FINAL_STEP,
            **candidate,
            "adapter_config_sha256": sha256_file(candidate_path / "adapter_config.json"),
            "stable_marker_sha256": sha256_file(stable),
        },
    }
    return _publish(
        plan_path.with_name("training_receipt.json"),
        body,
        hash_field="receipt_body_sha256",
    )


def validate_action_weighted_ce_receipt(path: str | Path) -> dict[str, Any]:
    receipt_path = Path(path).resolve()
    receipt = _self_hashed(
        receipt_path, schema=RECEIPT_SCHEMA, hash_field="receipt_body_sha256"
    )
    if (
        receipt.get("status") != "ok"
        or receipt.get("scientific_label") != SCIENTIFIC_LABEL
        or receipt.get("artifact_source_git_sha") != receipt.get("execution_source_git_sha")
        or _HEX40.fullmatch(str(receipt.get("execution_source_git_sha"))) is None
        or receipt.get("prime_commit") != PRIME_COMMIT
        or receipt.get("parent_receipt_sha256") != PARENT_RECEIPT_SHA256
        or receipt.get("parent_candidate") != "step25-sol-dagger-sft"
        or receipt.get("source_step") != SOURCE_STEP
        or receipt.get("final_step") != FINAL_STEP
        or receipt.get("optimizer_updates") != 1
        or receipt.get("learning_rate") != LEARNING_RATE
        or receipt.get("fresh_optimizer") is not True
        or receipt.get("fresh_scheduler") is not True
        or receipt.get("fresh_dataloader") is not True
        or receipt.get("resume_order") != RESUME_ORDER_CONTRACT
        or receipt.get("objective") != "weighted_behavioral_cloning"
        or receipt.get("on_policy") is not False
        or receipt.get("policy_gradient") is not False
        or receipt.get("native_prime_component") != "ce"
    ):
        raise ActionWeightedCEError("action-weighted CE receipt policy drifted")
    plan_path = Path(receipt["plan_path"])
    if sha256_file(plan_path) != receipt.get("plan_sha256"):
        raise ActionWeightedCEError("receipt-bound plan bytes changed")
    plan = validate_action_weighted_ce_plan(plan_path, require_launch_patches=True)
    if (
        receipt.get("plan_body_sha256") != plan["plan_body_sha256"]
        or receipt.get("artifact_source_git_sha") != plan["artifact_git_sha"]
        or receipt.get("trainer_topology") != plan["trainer_topology"]
        or receipt.get("collection_counts") != plan["collection_counts"]
        or receipt.get("weight_audit") != plan["weight_audit"]
        or receipt.get("training_batch") != plan["training_batch"]
        or receipt.get("resume_order") != plan["resume_order"]
        or receipt.get("source_dcp") != plan["source_dcp"]
        or receipt.get("trainer_config") != plan["trainer_config"]
    ):
        raise ActionWeightedCEError("receipt provenance differs from its plan")
    output = Path(plan["training_output"])
    if (output / f"checkpoints/step_{FINAL_STEP + 1}").exists() or (
        output / f"weights/step_{FINAL_STEP + 1}"
    ).exists():
        raise ActionWeightedCEError("receipt output contains an extra optimizer update")
    final_dcp = _require_dcp_inventory(
        output / f"checkpoints/step_{FINAL_STEP}/trainer",
        expected_identity=receipt["final_dcp"],
        trainer_world_size=TRAINER_WORLD_SIZE,
        includes_dataloader=False,
    )
    candidate_path = Path(plan["candidate_path"])
    candidate = _tree_identity(candidate_path)
    recorded = receipt["candidate"]
    if (
        recorded.get("name") != "step26-action-weighted-ce"
        or recorded.get("update") != FINAL_STEP
        or any(
            candidate[key] != recorded.get(key)
            for key in ("path", "files", "bytes", "tree_sha256")
        )
        or sha256_file(candidate_path / "adapter_config.json")
        != recorded.get("adapter_config_sha256")
        or sha256_file(candidate_path.parent / "STABLE")
        != recorded.get("stable_marker_sha256")
        or _adapter_targets(candidate_path) != sorted(plan["lora"]["targets"])
    ):
        raise ActionWeightedCEError("receipt candidate adapter changed")
    render_audit = _validate_render_audit(
        Path(plan["render_audit_path"]), batch_path=Path(plan["training_batch"]["path"])
    )
    if receipt.get("token_execution_audit") != _token_export_audit(
        output / f"run_default/token_exports/step_{FINAL_STEP}",
        render_audit=render_audit,
        topology=plan["trainer_topology"],
    ):
        raise ActionWeightedCEError("receipt per-token execution evidence changed")
    if receipt.get("trainer_logs") != _trainer_log_audit(
        output / "logs/trainer/torchrun", plan["trainer_topology"]
    ):
        raise ActionWeightedCEError("receipt trainer log evidence changed")
    if receipt.get("prime_launch_patches") != _patch_evidence(
        plan_path.parent / "launch_evidence"
    ):
        raise ActionWeightedCEError("receipt PRIME patch evidence changed")
    if final_dcp != receipt["final_dcp"]:
        raise ActionWeightedCEError("receipt final DCP identity changed")
    return receipt


def _prime_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    commands = parser.add_subparsers(dest="command", required=True)
    materialize = commands.add_parser("_prime-materialize")
    materialize.add_argument("--corpus", type=Path, required=True)
    materialize.add_argument("--model", type=Path, required=True)
    materialize.add_argument("--batch", type=Path, required=True)
    audit = commands.add_parser("_prime-audit")
    audit.add_argument("--batch", type=Path, required=True)
    audit.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "_prime-materialize":
        result = _materialize_prime_batch(
            corpus_path=args.corpus.resolve(),
            model_path=args.model.resolve(),
            batch_path=args.batch.resolve(),
        )
    else:
        result = _audit_prime_batch(
            batch_path=args.batch.resolve(), audit_path=args.audit.resolve()
        )
    print(canonical_json(result))
    return 0


__all__ = [
    "ActionWeightedCEError",
    "BUCKET_FRACTIONS",
    "COLLECTION_SCHEMA",
    "EXECUTED_PHASE_COUNTS",
    "PLAN_SCHEMA",
    "RECEIPT_SCHEMA",
    "TOPOLOGIES",
    "assign_state_balanced_ce_weights",
    "final_action_member_start",
    "prepare_action_weighted_ce_training",
    "trainer_command",
    "validate_action_weighted_ce_plan",
    "validate_action_weighted_ce_receipt",
    "validate_collection_manifest",
    "validate_prime_source",
    "write_action_weighted_ce_receipt",
]


if __name__ == "__main__":  # pragma: no cover - pinned PRIME subprocess entrypoint.
    raise SystemExit(_prime_main())
