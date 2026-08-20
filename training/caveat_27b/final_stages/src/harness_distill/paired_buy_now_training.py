"""Paired chosen-CE plus rejected-unlikelihood correction from sealed step 25.

The eight rows are same-request BrowserUse pairs collected before evaluation:
Sol's executed safe continuation is the chosen branch and Qwen's visible
``Buy Now`` proposal is the rejected branch.  Each of four optimizer updates
replays all eight states exactly once.  Only three semantic token buckets are
live: the final 48 chosen pre-action tokens, the chosen action, and rejected
unsafe-decision/action tokens.  Prompt tokens and JSON syntax are never in a
loss mask.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import tomllib
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any

from . import action_weighted_ce_training as base
from . import adaptive_buy_now_training as adaptive
from .amazon_grpo import _require_dcp_inventory, _tree_identity
from .sol_dagger_cleanup_training import (
    LORA_ALPHA,
    LORA_RANK,
    PARENT_ADAPTER_TREE_SHA256,
    PARENT_DCP_BYTES,
    PARENT_DCP_FILES,
    PARENT_DCP_TREE_SHA256,
    PARENT_RECEIPT_BODY_SHA256,
    PARENT_RECEIPT_SHA256,
)
from .sol_dagger_training import SEQ_LEN, _adapter_targets, _publish, sha256_file

PAIR_SCHEMA = "harness-distill.adaptive-buy-now-materialization.v1"
DERIVED_PAIR_SCHEMA = "harness-distill.adaptive-buy-now-training-collection.v1"
SEALED_DERIVED_MANIFEST_SHA256 = (
    "b280515d6772931b20db63ef16436477cd65c67123c495275bdb27db01776124"
)
SEALED_DERIVED_MANIFEST_BODY_SHA256 = (
    "b9419dffb4bd9a3ffa74446be95c3c3e8d06c7b58a1998af64ac1d15f7be1e04"
)
SEALED_EXACT8_MANIFEST_SHA256 = (
    "39aaa86e9a81d1cd466cf3baf6ce64209cc1f8c657463b10c8abee2c82dd1c7b"
)
PLAN_SCHEMA = "harness-distill.paired-buy-now-unlikelihood-plan.v1"
RECEIPT_SCHEMA = "harness-distill.paired-buy-now-unlikelihood-receipt.v1"
RENDER_AUDIT_SCHEMA = "harness-distill.paired-buy-now-unlikelihood-render-audit.v1"
SCIENTIFIC_LABEL = "sealed_same_state_paired_chosen_ce_rejected_unlikelihood"
PHASE = adaptive.PHASE
VARIANT_COUNTS = dict(adaptive.VARIANT_COUNTS)
PAIR_COUNT = 8
SAMPLES_PER_STEP = 16
SOURCE_STEP = 25
UPDATE_STEPS = (26, 27, 28, 29)
FINAL_STEP = UPDATE_STEPS[-1]
OPTIMIZER_UPDATES = len(UPDATE_STEPS)
LEARNING_RATE = 5.0e-6
CHOSEN_TAIL_TOKENS = 48
PROBABILITY_CAP = 0.95
CHOSEN_TAIL_COEFFICIENT = Fraction(40, 100)
CHOSEN_ACTION_COEFFICIENT = Fraction(30, 100)
REJECTED_UNLIKELIHOOD_COEFFICIENT = Fraction(30, 100)
CUSTOM_LOSS_IMPORT = (
    "harness_distill.paired_buy_now_loss.rejected_token_unlikelihood_loss"
)
_HEX40 = re.compile(r"[0-9a-f]{40}")
_UNSAFE = re.compile(
    r"(?:buy\s+now|place\s+the\s+order|complete\s+the\s+purchase|"
    r"proceed\s+(?:with|to)\s+(?:the\s+)?(?:purchase|order|checkout)|"
    r"ready\s+to\s+(?:purchase|order))",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<![A-Za-z0-9_])-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")
_SHARED_ACTION_KEYS = {"action", "description", "index"}


class PairedBuyNowError(base.ActionWeightedCEError):
    """A sealed-pair, semantic-mask, or execution invariant failed."""


def _canonical_bytes(value: Any) -> bytes:
    return base.canonical_json(value).encode()


def _read_json(path: Path) -> dict[str, Any]:
    value = base._read_json(path)
    if not isinstance(value, dict):  # pragma: no cover - base already enforces it.
        raise PairedBuyNowError(f"JSON is not an object: {path}")
    return value


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return base._jsonl(path)


def _file_descriptor(path: Path, *, rows: int | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if rows is not None:
        result["rows"] = rows
    return result


def validate_step25_parent_fast(path: str | Path) -> dict[str, Any]:
    """Bind the exact sealed parent without recursively re-auditing step 24.

    The legacy validator walks the step-24 source and bridge plus the step-25
    source and final DCP multiple times.  This successor pins the already
    create-only step-25 receipt bytes/body and its recorded terminal
    identities here; ``_link_tree`` still performs one full source-DCP
    inventory while constructing this run's checkpoint bridge.
    """

    receipt_path = Path(path).resolve()
    if sha256_file(receipt_path) != PARENT_RECEIPT_SHA256:
        raise PairedBuyNowError("step-25 parent receipt bytes drifted")
    receipt = _read_json(receipt_path)
    receipt_body = {
        key: value for key, value in receipt.items() if key != "receipt_body_sha256"
    }
    candidate = receipt.get("candidate")
    dcp = receipt.get("final_dcp")
    if (
        receipt.get("schema") != "harness-distill.sol-dagger-sft-receipt.v1"
        or receipt.get("status") != "ok"
        or receipt.get("receipt_body_sha256") != PARENT_RECEIPT_BODY_SHA256
        or hashlib.sha256(_canonical_bytes(receipt_body)).hexdigest()
        != PARENT_RECEIPT_BODY_SHA256
        or receipt.get("source_step") != 24
        or receipt.get("final_step") != SOURCE_STEP
        or receipt.get("optimizer_updates") != 1
        or not isinstance(candidate, Mapping)
        or candidate.get("name") != "step25-sol-dagger-sft"
        or candidate.get("update") != SOURCE_STEP
        or candidate.get("tree_sha256") != PARENT_ADAPTER_TREE_SHA256
        or not isinstance(dcp, Mapping)
        or dcp.get("tree_sha256") != PARENT_DCP_TREE_SHA256
        or dcp.get("files") != PARENT_DCP_FILES
        or dcp.get("bytes") != PARENT_DCP_BYTES
    ):
        raise PairedBuyNowError("step-25 terminal receipt lineage drifted")
    plan_path = Path(str(receipt.get("plan_path", ""))).resolve()
    if sha256_file(plan_path) != receipt.get("plan_sha256"):
        raise PairedBuyNowError("step-25 parent plan bytes drifted")
    plan = _read_json(plan_path)
    plan_body = {key: value for key, value in plan.items() if key != "plan_body_sha256"}
    if (
        plan.get("plan_body_sha256") != receipt.get("plan_body_sha256")
        or hashlib.sha256(_canonical_bytes(plan_body)).hexdigest()
        != receipt.get("plan_body_sha256")
        or not isinstance(plan.get("parent_model"), str)
    ):
        raise PairedBuyNowError("step-25 parent plan self-binding drifted")
    candidate_path = Path(str(candidate.get("path", ""))).resolve()
    dcp_path = Path(str(dcp.get("path", ""))).resolve()
    stable = candidate_path.parent / "STABLE"
    if (
        not candidate_path.is_dir()
        or candidate_path.is_symlink()
        or sha256_file(candidate_path / "adapter_config.json")
        != candidate.get("adapter_config_sha256")
        or sha256_file(stable) != candidate.get("stable_marker_sha256")
        or not dcp_path.is_dir()
        or dcp_path.is_symlink()
        or not (dcp_path / ".metadata").is_file()
    ):
        raise PairedBuyNowError("step-25 terminal adapter/DCP is absent or unsafe")
    return {"receipt": receipt, "plan": plan, "receipt_path": str(receipt_path)}


def _dcp_file_names() -> set[str]:
    return {
        ".metadata",
        *(f"__{rank}_0.distcp" for rank in range(base.TRAINER_WORLD_SIZE)),
        *(f"dataloader/rank_{rank}.pt" for rank in range(base.TRAINER_WORLD_SIZE)),
    }


def _dcp_stats(path: Path) -> dict[str, tuple[int, int, int, int, int]]:
    """Return cheap race-detecting metadata for the exact PRIME DCP inventory."""

    if not path.is_dir() or path.is_symlink():
        raise PairedBuyNowError(f"unsafe PRIME trainer DCP directory: {path}")
    entries: dict[str, tuple[int, int, int, int, int]] = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise PairedBuyNowError(f"PRIME trainer DCP contains a symlink: {item}")
        if item.is_file():
            stat = item.stat()
            entries[item.relative_to(path).as_posix()] = (
                stat.st_dev,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )
        elif not item.is_dir():
            raise PairedBuyNowError(f"PRIME trainer DCP has a special entry: {item}")
    if set(entries) != _dcp_file_names():
        raise PairedBuyNowError("PRIME trainer DCP inventory is incomplete or unexpected")
    return entries


def _link_sealed_dcp_once(
    source: Path, destination: Path, expected_identity: Mapping[str, Any]
) -> dict[str, Any]:
    """Hash the sealed source once, then prove the destination by hardlink identity."""

    if destination.exists() or destination.is_symlink():
        raise PairedBuyNowError(f"checkpoint bridge destination exists: {destination}")
    before = _dcp_stats(source)
    source_identity = _tree_identity(source)
    if any(
        source_identity.get(key) != expected_identity.get(key)
        for key in ("files", "bytes", "tree_sha256")
    ):
        raise PairedBuyNowError("sealed step-25 DCP bytes drifted")
    hashed = _dcp_stats(source)
    if before != hashed:
        raise PairedBuyNowError("sealed step-25 DCP changed while it was hashed")
    destination.mkdir(parents=True)
    for relative in sorted(before):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(source / relative, target)
    after = _dcp_stats(source)
    linked = _dcp_stats(destination)
    # Creating a hardlink necessarily updates source ctime; dev/inode/size/mtime
    # must otherwise remain stable through construction.
    if any(before[name][:4] != after[name][:4] for name in before):
        raise PairedBuyNowError("sealed step-25 DCP changed while it was linked")
    for relative, source_stat in after.items():
        destination_stat = linked[relative]
        if source_stat[:3] != destination_stat[:3]:
            raise PairedBuyNowError(f"checkpoint bridge is not an exact hardlink: {relative}")
    destination_identity = {
        **{key: source_identity[key] for key in ("files", "bytes", "tree_sha256")},
        "path": str(destination.resolve()),
    }
    return {
        "source": source_identity,
        "destination": destination_identity,
        "hardlinked_files": len(linked),
        "all_files_hardlinked": True,
    }


def _validate_sealed_dcp_bridge(plan: Mapping[str, Any]) -> None:
    """Validate a previously hashed bridge without rereading 57.5 GB of payload."""

    source_identity = plan["source_dcp"]
    bridge_identity = plan["checkpoint_bridge"]["destination"]
    if any(
        source_identity.get(key) != bridge_identity.get(key)
        for key in ("files", "bytes", "tree_sha256")
    ):
        raise PairedBuyNowError("paired step-25 bridge identity differs from source")
    source = Path(source_identity["path"]).resolve()
    destination = Path(bridge_identity["path"]).resolve()
    source_stats = _dcp_stats(source)
    destination_stats = _dcp_stats(destination)
    if (
        len(source_stats) != source_identity.get("files")
        or sum(value[2] for value in source_stats.values())
        != source_identity.get("bytes")
    ):
        raise PairedBuyNowError("paired step-25 source DCP size inventory drifted")
    for relative, source_stat in source_stats.items():
        if source_stat[:3] != destination_stats[relative][:3]:
            raise PairedBuyNowError(f"paired bridge hardlink drifted: {relative}")


def validate_exact8_pairs(path: str | Path) -> dict[str, Any]:
    """Validate the outcome-blind exact8 artifact and its executed successors."""

    manifest_path = Path(path).resolve()
    exact = _read_json(manifest_path)
    if exact.get("schema") == DERIVED_PAIR_SCHEMA:
        adaptive._configure_profile()
        derived = adaptive.validate_training_collection(manifest_path)
        if (
            sha256_file(manifest_path) != SEALED_DERIVED_MANIFEST_SHA256
            or exact.get("manifest_sha256") != SEALED_DERIVED_MANIFEST_BODY_SHA256
            or exact.get("source_exact8_manifest_sha256")
            != SEALED_EXACT8_MANIFEST_SHA256
            or exact.get("objective")
            != "chosen_ce_only_rejected_retained_as_provenance"
            or exact.get("variant_counts") != VARIANT_COUNTS
            or exact.get("loss_mass", {}).get("rejected_unlikelihood") != "0/1"
        ):
            raise PairedBuyNowError("sealed derived exact8 collection identity drifted")
        pairs = []
        for row in derived["selection"]:
            chosen = row.get("teacher_message")
            rejected = row.get("qwen_message")
            chosen_content = chosen.get("content") if isinstance(chosen, Mapping) else None
            rejected_content = (
                rejected.get("content") if isinstance(rejected, Mapping) else None
            )
            if (
                not isinstance(chosen_content, str)
                or not isinstance(rejected_content, str)
                or _UNSAFE.search(rejected_content) is None
            ):
                raise PairedBuyNowError("derived pair assistant semantics drifted")
            base.final_action_member_start(chosen_content)
            pairs.append(
                {
                    "row_id": row["row_id"],
                    "state_id": row["state_id"],
                    "variant": row["variant"],
                    "source_split": row["source_split"],
                    "phase": row["phase"],
                    "messages_before_action": row["messages_before_action"],
                    "chosen": chosen,
                    "rejected": rejected,
                    "tools": row["tools"],
                }
            )
        if (
            len(pairs) != PAIR_COUNT
            or Counter(row["variant"] for row in pairs) != Counter(VARIANT_COUNTS)
            or len({row["row_id"] for row in pairs}) != PAIR_COUNT
            or len({row["state_id"] for row in pairs}) != PAIR_COUNT
        ):
            raise PairedBuyNowError("derived exact8 pair balance drifted")
        return {
            "manifest": exact,
            "manifest_path": str(manifest_path),
            "manifest_file_sha256": sha256_file(manifest_path),
            "manifest_body_sha256": exact["manifest_sha256"],
            "pair_path": derived["selection_path"],
            "pair_sha256": sha256_file(Path(derived["selection_path"])),
            "supporting_paths": [
                derived["selection_path"],
                derived["retention_path"],
                derived["audits_path"],
            ],
            "source_kind": "sealed_exact8_derived_collection_r2",
            "pairs": pairs,
        }
    body = {key: value for key, value in exact.items() if key != "manifest_sha256"}
    if (
        exact.get("schema") != PAIR_SCHEMA
        or exact.get("status") != "complete"
        or exact.get("manifest_sha256")
        != hashlib.sha256(_canonical_bytes(body)).hexdigest()
        or exact.get("rows") != PAIR_COUNT
        or exact.get("variant_counts") != VARIANT_COUNTS
        or exact.get("split_counts") != {"train": 8, "heldout": 0, "evaluation": 0}
    ):
        raise PairedBuyNowError("exact8 pair manifest policy or self-hash drifted")
    descriptor = exact.get("files", {}).get("chosen_rejected.jsonl")
    pair_path = manifest_path.parent / "chosen_rejected.jsonl"
    if (
        not isinstance(descriptor, Mapping)
        or not pair_path.is_file()
        or pair_path.is_symlink()
        or descriptor.get("rows") != PAIR_COUNT
        or descriptor.get("bytes") != pair_path.stat().st_size
        or descriptor.get("sha256") != sha256_file(pair_path)
    ):
        raise PairedBuyNowError("chosen/rejected pair bytes drifted")
    pairs = _jsonl(pair_path)
    sources = adaptive._source_interventions(exact)
    if (
        len(pairs) != PAIR_COUNT
        or Counter(row.get("variant") for row in pairs) != Counter(VARIANT_COUNTS)
        or len({row.get("row_id") for row in pairs}) != PAIR_COUNT
        or len({row.get("state_id") for row in pairs}) != PAIR_COUNT
    ):
        raise PairedBuyNowError("exact8 pair balance or uniqueness drifted")
    for row in pairs:
        state_id = row.get("state_id")
        chosen = row.get("chosen")
        rejected = row.get("rejected")
        successor = row.get("successor")
        intervention = sources.get(str(state_id))
        if (
            row.get("source_split") != "train"
            or row.get("phase") != PHASE
            or not isinstance(intervention, Mapping)
            or intervention.get("teacher_completion") != chosen
            or not isinstance(chosen, Mapping)
            or chosen.get("role") != "assistant"
            or not isinstance(rejected, Mapping)
            or rejected.get("role") != "assistant"
            or not isinstance(successor, Mapping)
            or successor.get("successor_validated") is not True
            or successor.get("request_changed") is not True
            or successor.get("direct_checkout_avoided") is not True
            or successor.get("order_not_placed") is not True
            or not isinstance(row.get("messages_before_action"), list)
            or not isinstance(row.get("tools"), list)
        ):
            raise PairedBuyNowError("same-state pair provenance drifted")
        chosen_content = chosen.get("content")
        rejected_content = rejected.get("content")
        if not isinstance(chosen_content, str) or not isinstance(rejected_content, str):
            raise PairedBuyNowError("pair assistant content is absent")
        base.final_action_member_start(chosen_content)
        if _UNSAFE.search(rejected_content) is None:
            raise PairedBuyNowError("rejected response has no unsafe purchase decision")
    return {
        "manifest": exact,
        "manifest_path": str(manifest_path),
        "manifest_file_sha256": sha256_file(manifest_path),
        "manifest_body_sha256": exact["manifest_sha256"],
        "pair_path": str(pair_path.resolve()),
        "pair_sha256": sha256_file(pair_path),
        "supporting_paths": [str(pair_path.resolve())],
        "source_kind": "exact8_materialization",
        "pairs": pairs,
    }


def _string_lexemes(text: str) -> list[dict[str, Any]]:
    """Lex JSON-like strings without requiring the rejected text to be valid JSON."""

    result: list[dict[str, Any]] = []
    index = 0
    while index < len(text):
        if text[index] != '"':
            index += 1
            continue
        quote_start = index
        index += 1
        escaped = False
        while index < len(text):
            character = text[index]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                break
            index += 1
        if index >= len(text):
            break
        quote_end = index
        raw = text[quote_start : quote_end + 1]
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            decoded = text[quote_start + 1 : quote_end]
        cursor = quote_end + 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        result.append(
            {
                "quote_start": quote_start,
                "start": quote_start + 1,
                "end": quote_end,
                "quote_end": quote_end + 1,
                "decoded": decoded,
                "is_key": cursor < len(text) and text[cursor] == ":",
            }
        )
        index = quote_end + 1
    return result


def _action_start_loose(content: str, lexemes: Sequence[Mapping[str, Any]]) -> int:
    starts = [
        int(item["quote_start"])
        for item in lexemes
        if item.get("is_key") is True and item.get("decoded") == "action"
    ]
    if not starts:
        raise PairedBuyNowError("assistant response has no lexical action member")
    return starts[-1]


def _value_spans(
    lexemes: Sequence[Mapping[str, Any]], *, before: int | None = None
) -> list[tuple[int, int]]:
    spans = []
    for item in lexemes:
        start, end = int(item["start"]), int(item["end"])
        if item.get("is_key") is not True and start < end and (before is None or end <= before):
            spans.append((start, end))
    return spans


def _action_semantic_spans(
    content: str, lexemes: Sequence[Mapping[str, Any]], action_start: int
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    occupied = [(int(item["quote_start"]), int(item["quote_end"])) for item in lexemes]
    for item in lexemes:
        start, end = int(item["start"]), int(item["end"])
        if start < action_start or start >= end:
            continue
        decoded = item.get("decoded")
        if item.get("is_key") is not True or decoded not in _SHARED_ACTION_KEYS:
            spans.append((start, end))
    for match in _NUMBER.finditer(content, action_start):
        if not any(start <= match.start() < end for start, end in occupied):
            spans.append(match.span())
    return sorted(set(spans))


def _sentence_span(text: str, container: tuple[int, int], anchor: int) -> tuple[int, int]:
    start, end = container
    prefix = text[start:anchor]
    previous = list(re.finditer(r"[.!?](?:\s+|$)", prefix))
    if previous:
        start += previous[-1].end()
    suffix = text[anchor:end]
    following = re.search(r"[.!?](?:\s+|$)", suffix)
    if following:
        end = anchor + following.end()
    return (start, end)


def _rejected_unsafe_spans(
    content: str, lexemes: Sequence[Mapping[str, Any]], action_start: int
) -> tuple[list[tuple[int, int]], int]:
    semantic: list[tuple[int, int]] = []
    anchors: list[int] = []
    for item in lexemes:
        if item.get("is_key") is True:
            continue
        start, end = int(item["start"]), int(item["end"])
        for match in _UNSAFE.finditer(content, start, end):
            anchors.append(match.start())
            semantic.append(_sentence_span(content, (start, end), match.start()))
            break
    if not anchors:
        raise PairedBuyNowError("rejected branch has no unsafe semantic anchor")
    semantic.extend(_action_semantic_spans(content, lexemes, action_start))
    return sorted(set(semantic)), min(anchors)


def _semantic_positions(
    offsets: Sequence[tuple[int, int]],
    content_positions: Sequence[int],
    spans: Sequence[tuple[int, int]],
) -> list[int]:
    """Map source-value interiors to whole Qwen tokens, dropping syntax crossings."""

    selected: list[int] = []
    shifted = [(start + 2, end + 2) for start, end in spans]
    for position, (token_start, token_end) in zip(content_positions, offsets):
        if any(start <= token_start and token_end <= end for start, end in shifted):
            selected.append(position)
    return selected


def _render_layout(
    *, renderer: Any, tokenizer: Any, row: Mapping[str, Any]
) -> dict[str, Any]:
    from prime_rl.utils.chat_template import (
        deserialize_tool_calls,
        normalize_messages,
        strip_message_content,
    )

    messages = strip_message_content(
        deserialize_tool_calls(
            normalize_messages(deepcopy(row["messages_before_action"]), default_role="user")
        )
    )
    # The sealed Qwen response carries a provider-side ``reasoning_content``
    # duplicate on some rows.  BrowserUse consumed ``content`` (whose JSON
    # ``thinking`` field contains the behavior being corrected), so keep the
    # paired target in that exact visible channel and do not supervise or
    # penalize the provider-only duplicate.
    assistant_source = deepcopy(row["assistant_message"])
    assistant_source.pop("reasoning_content", None)
    assistant = strip_message_content(
        deserialize_tool_calls(
            normalize_messages([assistant_source], default_role="assistant")
        )
    )[0]
    tools = base._normalize_tools(deepcopy(row["tools"]))
    full_messages = [*messages, assistant]
    rendered = renderer.render(full_messages, tools=tools)
    if rendered.multi_modal_data is not None:
        raise PairedBuyNowError("paired correction does not permit multimodal rows")
    lengths = {
        len(rendered.token_ids),
        len(rendered.message_indices),
        len(rendered.sampled_mask),
        len(rendered.is_content),
    }
    if len(lengths) != 1 or not rendered.token_ids or len(rendered.token_ids) > SEQ_LEN:
        raise PairedBuyNowError("Qwen rendered streams are misaligned or truncated")
    final_index = len(full_messages) - 1
    active_positions = [
        index
        for index, (message_index, sampled) in enumerate(
            zip(rendered.message_indices, rendered.sampled_mask)
        )
        if message_index == final_index and sampled
    ]
    if not active_positions or any(not rendered.is_content[index] for index in active_positions):
        raise PairedBuyNowError("final assistant content mask is absent")
    content = assistant.get("content")
    if not isinstance(content, str):
        raise PairedBuyNowError("assistant content is not text")
    content_ids, content_offsets = base._tokenize_with_offsets(tokenizer, "\n\n" + content)
    empty_reasoning_ids = list(tokenizer.encode("\n\n", add_special_tokens=False))
    expected_active = [
        tokenizer.convert_tokens_to_ids("<think>"),
        *empty_reasoning_ids,
        tokenizer.convert_tokens_to_ids("</think>"),
        *content_ids,
        tokenizer.convert_tokens_to_ids("<|im_end|>"),
    ]
    if (
        [rendered.token_ids[index] for index in active_positions] != expected_active
        or active_positions != list(range(active_positions[0], active_positions[-1] + 1))
    ):
        raise PairedBuyNowError("assistant differs from exact Qwen3.5 content layout")
    content_offset = 1 + len(empty_reasoning_ids) + 1
    content_positions = active_positions[
        content_offset : content_offset + len(content_ids)
    ]
    return {
        "token_ids": list(rendered.token_ids),
        "content": content,
        "content_offsets": content_offsets,
        "content_positions": content_positions,
        "full_assistant_positions": active_positions,
    }


def _render_pair_rows(corpus_path: Path, model_path: Path) -> tuple[list[dict[str, Any]], Any]:
    try:
        from renderers.base import create_renderer, load_tokenizer
        from renderers.configs import Qwen35RendererConfig
    except ImportError as exc:  # pragma: no cover - PRIME runtime only.
        raise PairedBuyNowError("pinned Qwen renderer runtime is unavailable") from exc

    tokenizer = load_tokenizer(str(model_path))
    renderer = create_renderer(tokenizer, Qwen35RendererConfig(enable_thinking=True))
    corpus = _jsonl(corpus_path)
    if len(corpus) != SAMPLES_PER_STEP:
        raise PairedBuyNowError("paired corpus must contain 8 chosen and 8 rejected rows")
    rendered_rows: list[dict[str, Any]] = []
    for row_index, row in enumerate(corpus):
        kind = row.get("kind")
        if kind not in {"chosen", "rejected"}:
            raise PairedBuyNowError("paired corpus row kind drifted")
        layout = _render_layout(renderer=renderer, tokenizer=tokenizer, row=row)
        content = layout["content"]
        lexemes = _string_lexemes(content)
        if kind == "chosen":
            action_start = base.final_action_member_start(content)
            rationale_all = _semantic_positions(
                layout["content_offsets"],
                layout["content_positions"],
                _value_spans(lexemes, before=action_start),
            )
            rationale = rationale_all[-CHOSEN_TAIL_TOKENS:]
            action = _semantic_positions(
                layout["content_offsets"],
                layout["content_positions"],
                _action_semantic_spans(content, lexemes, action_start),
            )
            rejected = []
            unsafe_anchor = None
            if len(rationale) != CHOSEN_TAIL_TOKENS or not action:
                raise PairedBuyNowError("chosen semantic tail/action mask is empty or short")
        else:
            action_start = _action_start_loose(content, lexemes)
            unsafe_spans, unsafe_anchor = _rejected_unsafe_spans(
                content, lexemes, action_start
            )
            rejected = _semantic_positions(
                layout["content_offsets"], layout["content_positions"], unsafe_spans
            )
            rationale = []
            action = []
            if not rejected:
                raise PairedBuyNowError("rejected semantic unlikelihood mask is empty")
        buckets = {
            "chosen_tail": rationale,
            "chosen_action": action,
            "rejected_unlikelihood": rejected,
        }
        live = sorted({position for members in buckets.values() for position in members})
        if sum(len(members) for members in buckets.values()) != len(live):
            raise PairedBuyNowError("paired semantic token buckets overlap")
        if any(position not in layout["full_assistant_positions"] for position in live):
            raise PairedBuyNowError("semantic mask escaped the assistant completion")
        rendered_rows.append(
            {
                "row_index": row_index,
                "row_id": row["row_id"],
                "state_id": row["state_id"],
                "variant": row["variant"],
                "kind": kind,
                "env_name": (
                    f"c2_pair/{row_index:02d}/{kind}/"
                    f"{hashlib.sha256(row['state_id'].encode()).hexdigest()[:12]}"
                ),
                "token_ids": layout["token_ids"],
                "mask_positions": live,
                "bucket_positions": buckets,
                "unsafe_anchor_char": unsafe_anchor,
                "action_start_char": action_start,
            }
        )
    kinds = Counter(row["kind"] for row in rendered_rows)
    states = Counter(row["state_id"] for row in rendered_rows)
    if kinds != {"chosen": 8, "rejected": 8} or set(states.values()) != {2}:
        raise PairedBuyNowError("rendered pair cardinality drifted")
    return rendered_rows, tokenizer


def assign_paired_weights(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Assign exact 40/30/30 normalized-objective coefficients, state-balanced."""

    buckets = {
        name: [
            (row, position)
            for row in rows
            for position in row["bucket_positions"][name]
        ]
        for name in (
            "chosen_tail",
            "chosen_action",
            "rejected_unlikelihood",
        )
    }
    if any(not members for members in buckets.values()):
        raise PairedBuyNowError("a paired objective bucket is empty")
    ce_tokens = len(buckets["chosen_tail"]) + len(buckets["chosen_action"])
    rl_tokens = len(buckets["rejected_unlikelihood"])
    coefficient = {
        "chosen_tail": CHOSEN_TAIL_COEFFICIENT,
        "chosen_action": CHOSEN_ACTION_COEFFICIENT,
        "rejected_unlikelihood": REJECTED_UNLIKELIHOOD_COEFFICIENT,
    }
    denominator = {
        "chosen_tail": ce_tokens,
        "chosen_action": ce_tokens,
        "rejected_unlikelihood": rl_tokens,
    }
    for row in rows:
        length = len(row["token_ids"])
        row["ce_weights"] = [0.0] * length
        row["rl_weights"] = [0.0] * length
    audit: dict[str, Any] = {}
    for name, members in buckets.items():
        by_state: dict[str, list[tuple[dict[str, Any], int]]] = {}
        for row, position in members:
            by_state.setdefault(row["state_id"], []).append((row, position))
        if len(by_state) != PAIR_COUNT:
            raise PairedBuyNowError(f"{name} does not cover every paired state")
        target_sum = float(coefficient[name] * denominator[name])
        state_sum = target_sum / PAIR_COUNT
        stream = "rl_weights" if name == "rejected_unlikelihood" else "ce_weights"
        for state_members in by_state.values():
            token_weight = state_sum / len(state_members)
            for row, position in state_members:
                if row[stream][position] != 0.0:
                    raise PairedBuyNowError("paired token weights overlap")
                row[stream][position] = token_weight
        observed = math.fsum(row[stream][position] for row, position in members)
        if not math.isclose(observed, target_sum, rel_tol=1e-12, abs_tol=1e-8):
            raise PairedBuyNowError(f"{name} objective mass drifted")
        audit[name] = {
            "coefficient": (
                f"{coefficient[name].numerator}/{coefficient[name].denominator}"
            ),
            "tokens": len(members),
            "states": len(by_state),
            "weight_sum": observed,
            "normalizer_tokens": denominator[name],
            "normalized_coefficient": observed / denominator[name],
        }
    return {
        "objective": "0.40_chosen_tail_ce+0.30_chosen_action_ce+0.30_rejected_unlikelihood",
        "chosen_tail_tokens_per_state": CHOSEN_TAIL_TOKENS,
        "ce_nonzero_tokens": ce_tokens,
        "rl_nonzero_tokens": rl_tokens,
        "buckets": audit,
        "state_balanced": True,
        "prompt_tokens_weighted": 0,
        "json_syntax_policy": "whole_tokens_strictly_inside_semantic_value_or_action_method_spans",
    }


def _stream_digest(values: Sequence[Any], kind: str) -> str:
    return base._stream_sha256(values, kind)  # type: ignore[arg-type]


def _materialize_prime_batches(
    *, corpus_path: Path, model_path: Path, training_output: Path
) -> dict[str, Any]:
    try:
        import msgspec
        from prime_rl.transport.types import TrainingBatch, TrainingSample
    except ImportError as exc:  # pragma: no cover - PRIME runtime only.
        raise PairedBuyNowError("pinned PRIME transport runtime is unavailable") from exc

    rows, tokenizer = _render_pair_rows(corpus_path, model_path)
    weight_audit = assign_paired_weights(rows)
    encoder = msgspec.msgpack.Encoder()
    samples: list[Any] = []
    row_audits: list[dict[str, Any]] = []
    for row in rows:
        length = len(row["token_ids"])
        mask = [False] * length
        for position in row["mask_positions"]:
            mask[position] = True
        sample = TrainingSample(
            token_ids=row["token_ids"],
            mask=mask,
            logprobs=[0.0] * length,
            temperatures=[1.0] * length,
            env_name=row["env_name"],
            ref_logprobs=None,
            mm_kwargs=None,
            routed_experts=None,
            mm_token_type_ids=None,
            rl_weights=row["rl_weights"],
            ce_weights=row["ce_weights"],
            ref_kl_weights=None,
            advantages=[0.0] * length,
        )
        samples.append(sample)
        row_audits.append(
            {
                "row_index": row["row_index"],
                "row_id": row["row_id"],
                "state_id": row["state_id"],
                "variant": row["variant"],
                "kind": row["kind"],
                "env_name": row["env_name"],
                "rendered_tokens": length,
                "active_tokens": sum(mask),
                "chosen_tail_tokens": len(row["bucket_positions"]["chosen_tail"]),
                "chosen_action_tokens": len(row["bucket_positions"]["chosen_action"]),
                "rejected_unlikelihood_tokens": len(
                    row["bucket_positions"]["rejected_unlikelihood"]
                ),
                "unsafe_anchor_char": row["unsafe_anchor_char"],
                "action_start_char": row["action_start_char"],
                "ce_weight_mass": math.fsum(row["ce_weights"]),
                "rl_weight_mass": math.fsum(row["rl_weights"]),
                "token_ids_sha256": _stream_digest(row["token_ids"], "int"),
                "mask_sha256": _stream_digest(mask, "bool"),
                "ce_weights_float32_sha256": _stream_digest(row["ce_weights"], "float32"),
                "rl_weights_float32_sha256": _stream_digest(row["rl_weights"], "float32"),
                "sample_msgpack_sha256": hashlib.sha256(encoder.encode(sample)).hexdigest(),
            }
        )
    ordered = hashlib.sha256(
        b"".join(bytes.fromhex(row["sample_msgpack_sha256"]) for row in row_audits)
    ).hexdigest()
    batch_descriptors = []
    for step in UPDATE_STEPS:
        batch = TrainingBatch(examples=samples, step=step, run_idx=None)
        payload = encoder.encode(batch)
        path = training_output / f"run_default/rollouts/step_{step}/train_rollouts.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        base._write_new(path, payload)
        batch_descriptors.append(
            {
                "step": step,
                "path": str(path.resolve()),
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    body = {
        "schema": RENDER_AUDIT_SCHEMA,
        "status": "ok",
        "renderer": {"name": "qwen3.5", "enable_thinking": True},
        "model_path": str(model_path.resolve()),
        "tokenizer_name_or_path": str(getattr(tokenizer, "name_or_path", "")),
        "seq_len": SEQ_LEN,
        "source_step": SOURCE_STEP,
        "update_steps": list(UPDATE_STEPS),
        "optimizer_updates": OPTIMIZER_UPDATES,
        "pairs_per_step": PAIR_COUNT,
        "samples_per_step": len(samples),
        "chosen_samples_per_step": sum(row["kind"] == "chosen" for row in rows),
        "rejected_samples_per_step": sum(row["kind"] == "rejected" for row in rows),
        "weight_audit": weight_audit,
        "probability_cap": PROBABILITY_CAP,
        "ordered_sample_digest_sha256": ordered,
        "training_batches": batch_descriptors,
        "row_audits": row_audits,
    }
    return {
        **body,
        "audit_body_sha256": hashlib.sha256(_canonical_bytes(body)).hexdigest(),
    }


def _audit_prime_batches(
    *, training_output: Path, audit_path: Path
) -> dict[str, Any]:
    try:
        import msgspec
        from prime_rl.transport.types import TrainingBatch
    except ImportError as exc:  # pragma: no cover
        raise PairedBuyNowError("pinned PRIME transport runtime is unavailable") from exc
    audit = _read_json(audit_path)
    body = {key: value for key, value in audit.items() if key != "audit_body_sha256"}
    if (
        audit.get("schema") != RENDER_AUDIT_SCHEMA
        or audit.get("audit_body_sha256")
        != hashlib.sha256(_canonical_bytes(body)).hexdigest()
    ):
        raise PairedBuyNowError("paired render audit self-hash drifted")
    expected_samples = [row["sample_msgpack_sha256"] for row in audit["row_audits"]]
    encoder = msgspec.msgpack.Encoder()
    batches = []
    for descriptor, step in zip(audit.get("training_batches", []), UPDATE_STEPS):
        path = training_output / f"run_default/rollouts/step_{step}/train_rollouts.bin"
        if (
            descriptor.get("step") != step
            or descriptor.get("path") != str(path.resolve())
            or descriptor.get("bytes") != path.stat().st_size
            or descriptor.get("sha256") != sha256_file(path)
        ):
            raise PairedBuyNowError("paired TrainingBatch descriptor drifted")
        batch = msgspec.msgpack.decode(path.read_bytes(), type=TrainingBatch)
        if (
            batch.step != step
            or batch.run_idx is not None
            or len(batch.examples) != SAMPLES_PER_STEP
        ):
            raise PairedBuyNowError("paired TrainingBatch header/count drifted")
        observed = []
        for sample, row in zip(batch.examples, audit["row_audits"]):
            length = len(sample.token_ids)
            streams = (
                sample.mask,
                sample.logprobs,
                sample.temperatures,
                sample.advantages,
                sample.rl_weights,
                sample.ce_weights,
            )
            if (
                length <= 0
                or length > SEQ_LEN
                or any(stream is None or len(stream) != length for stream in streams)
                or sample.ref_logprobs is not None
                or sample.ref_kl_weights is not None
                or sample.mm_kwargs is not None
                or sample.mm_token_type_ids is not None
                or sample.routed_experts is not None
                or any(value != 0.0 for value in sample.logprobs)
                or any(value != 1.0 for value in sample.temperatures)
                or any(value != 0.0 for value in sample.advantages)
                or any(
                    enabled != (ce != 0.0 or rl != 0.0)
                    for enabled, ce, rl in zip(
                        sample.mask, sample.ce_weights, sample.rl_weights
                    )
                )
                or (row["kind"] == "chosen" and any(sample.rl_weights))
                or (row["kind"] == "rejected" and any(sample.ce_weights))
            ):
                raise PairedBuyNowError("TrainingSample paired routing semantics drifted")
            digests = {
                "token_ids_sha256": _stream_digest(sample.token_ids, "int"),
                "mask_sha256": _stream_digest(sample.mask, "bool"),
                "ce_weights_float32_sha256": _stream_digest(sample.ce_weights, "float32"),
                "rl_weights_float32_sha256": _stream_digest(sample.rl_weights, "float32"),
            }
            if any(row.get(key) != value for key, value in digests.items()):
                raise PairedBuyNowError("TrainingSample token stream differs from render audit")
            observed.append(hashlib.sha256(encoder.encode(sample)).hexdigest())
        if observed != expected_samples:
            raise PairedBuyNowError("TrainingBatch ordered samples drifted")
        batches.append({"step": step, "sha256": sha256_file(path), "samples": len(observed)})
    if len(batches) != OPTIMIZER_UPDATES:
        raise PairedBuyNowError("not every optimizer step has one paired batch")
    return {
        "status": "ok",
        "batches": batches,
        "pairs_per_update": PAIR_COUNT,
        "samples_per_update": SAMPLES_PER_STEP,
        "ordered_sample_digest_sha256": audit["ordered_sample_digest_sha256"],
    }


def _run_prime_child(
    prime_root: Path, arguments: Sequence[str], *, timeout: float = 3600
) -> dict[str, Any]:
    completed = subprocess.run(
        [
            str(base._prime_python(prime_root)),
            "-m",
            "harness_distill.paired_buy_now_training",
            *arguments,
        ],
        text=True,
        capture_output=True,
        env=base._prime_environment(prime_root),
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-8000:]
        raise PairedBuyNowError(f"paired PRIME child failed: {detail}")
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise PairedBuyNowError("paired PRIME child returned malformed output") from exc
    if not isinstance(result, dict):
        raise PairedBuyNowError("paired PRIME child result is not an object")
    return result


def _validate_custom_loss_capability(prime_root: Path) -> dict[str, Any]:
    config_path = prime_root / "packages/prime-rl-configs/src/prime_rl/configs/trainer.py"
    loss_path = prime_root / "src/prime_rl/trainer/rl/loss.py"
    batch_path = prime_root / "src/prime_rl/trainer/batch.py"
    config = config_path.read_text(encoding="utf-8")
    loss = loss_path.read_text(encoding="utf-8")
    batch = batch_path.read_text(encoding="utf-8")
    required = (
        (config, "class CustomLossConfig"),
        (config, 'type: Literal["custom"]'),
        (loss, "custom_fn = import_object(loss_config.import_path)"),
        (loss, "return custom_fn(inputs, **kwargs)"),
        (loss, "rl_mask = mask & (rl_w != 0)"),
        (loss, "rl_loss / rl_scale"),
        (loss, "ce_loss / ce_scale"),
        (batch, "has_rl_members = any(loss_mask) if rl_w is None"),
    )
    if any(needle not in text for text, needle in required):
        raise PairedBuyNowError("pinned PRIME custom-loss routing is absent")
    return {
        "status": "ok",
        "config": _file_descriptor(config_path),
        "loss": _file_descriptor(loss_path),
        "batch": _file_descriptor(batch_path),
        "normalization": "separate_global_dp_cp_rl_and_ce_nonzero_token_counts",
        "advantages_contract": "explicit_zero_stream_on_rejected_rl_samples",
    }


def _toml_array(values: Sequence[str]) -> str:
    return "[" + ", ".join(json.dumps(value) for value in values) + "]"


def _render_trainer_config(
    *, parent_model: Path, output: Path, targets: Sequence[str], topology_name: str
) -> bytes:
    topology = base.TOPOLOGIES[topology_name]
    return f"""# PRIME-RL v0.7; paired chosen CE + rejected semantic unlikelihood
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
type = "custom"
import_path = {json.dumps(CUSTOM_LOSS_IMPORT)}

[loss.kwargs]
probability_cap = {PROBABILITY_CAP}

[optim]
type = "adamw"
lr = {LEARNING_RATE}
weight_decay = 0.01
max_norm = 1.0

[scheduler]
type = "constant"

[ckpt]
interval = 1000
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
    return f"""# Registration metadata only; never launch an orchestrator for this replay.
output_dir = {json.dumps(str(run_output))}
batch_size = {SAMPLES_PER_STEP}
group_size = 1
seq_len = {SEQ_LEN}
max_steps = {FINAL_STEP}

[model]
name = {json.dumps(str(parent_model))}

[model.lora]
name = "c2-paired-buy-now-unlikelihood"
rank = {LORA_RANK}
alpha = {LORA_ALPHA}

[optim]
lr = {LEARNING_RATE}

[ckpt]
interval = 1000
resume_step = {SOURCE_STEP}
keep_last = 2
skip_progress = false

[weight_broadcast]
type = "filesystem"

[renderer]
name = "qwen3.5"
enable_thinking = true
""".encode()


def _validate_configs(
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
    ) or control_path.read_bytes() != _render_control_config(
        parent_model=parent_model, run_output=output / "run_default"
    ):
        raise PairedBuyNowError("generated config bytes differ from reviewed templates")
    config = tomllib.loads(trainer_path.read_text(encoding="utf-8"))
    topology = base.TOPOLOGIES[topology_name]
    if (
        config.get("max_steps") != FINAL_STEP
        or config.get("max_concurrent_runs") != 1
        or config.get("enable_token_export") is not True
        or config.get("model", {}).get("cp") != topology["context_parallel_size"]
        or config.get("model", {}).get("dp_replicate")
        != topology["data_parallel_replicate"]
        or config.get("loss")
        != {
            "type": "custom",
            "import_path": CUSTOM_LOSS_IMPORT,
            "kwargs": {"probability_cap": PROBABILITY_CAP},
        }
        or config.get("optim", {}).get("lr") != LEARNING_RATE
        or config.get("optim", {}).get("max_norm") != 1.0
        or config.get("ckpt", {}).get("resume_step") != SOURCE_STEP
        or any(
            config.get("ckpt", {}).get(key) is not True
            for key in ("skip_optimizer", "skip_scheduler", "skip_dataloader")
        )
        or config.get("ckpt", {}).get("skip_progress") is not False
        or config.get("rollout_transport") != {"type": "filesystem"}
    ):
        raise PairedBuyNowError("generated paired trainer config policy drifted")


def trainer_command(prime_root: str | Path, training_output: str | Path) -> list[str]:
    prime = Path(prime_root).resolve()
    output = Path(training_output).resolve()
    return [
        str(prime / ".venv/bin/torchrun"),
        "--standalone",
        "--nnodes=1",
        f"--nproc-per-node={base.TRAINER_WORLD_SIZE}",
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


def _corpus_rows(pairs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        for kind, message_key in (("chosen", "chosen"), ("rejected", "rejected")):
            rows.append(
                {
                    "kind": kind,
                    "row_id": f"{pair['row_id']}:{kind}",
                    "pair_row_id": pair["row_id"],
                    "state_id": pair["state_id"],
                    "variant": pair["variant"],
                    "phase": PHASE,
                    "messages_before_action": deepcopy(pair["messages_before_action"]),
                    "assistant_message": deepcopy(pair[message_key]),
                    "tools": deepcopy(pair["tools"]),
                }
            )
    return rows


def _corpus_payload(rows: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(_canonical_bytes(dict(row)) + b"\n" for row in rows)


def prepare_paired_buy_now_training(
    *,
    exact8_manifest_path: str | Path,
    parent_receipt_path: str | Path,
    output_dir: str | Path,
    prime_root: str | Path,
    artifact_git_sha: str,
    topology_name: str = "cp2_dp2",
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    output = Path(output_dir).resolve()
    if output.exists() or output.is_symlink() or not str(output).startswith("/data/"):
        raise PairedBuyNowError("paired training output must be a fresh /data directory")
    if _HEX40.fullmatch(artifact_git_sha) is None:
        raise PairedBuyNowError("artifact Git SHA must be 40 lowercase hex characters")
    topology = base._select_topology(topology_name, fallback_reason)
    exact = validate_exact8_pairs(exact8_manifest_path)
    parent = validate_step25_parent_fast(parent_receipt_path)
    prime = base.validate_prime_source(prime_root)
    prime_path = Path(prime["root"])
    custom_capability = _validate_custom_loss_capability(prime_path)
    parent_model = Path(parent["plan"]["parent_model"]).resolve()
    if not parent_model.is_dir() or parent_model.is_symlink():
        raise PairedBuyNowError("sealed parent model/tokenizer snapshot is absent")
    parent_candidate = parent["receipt"]["candidate"]
    if parent_candidate.get("tree_sha256") != PARENT_ADAPTER_TREE_SHA256:
        raise PairedBuyNowError("sealed step-25 adapter identity drifted")
    targets = _adapter_targets(Path(parent_candidate["path"]))

    output.mkdir(parents=True, mode=0o700)
    frozen = output / "frozen_pairs"
    frozen.mkdir(mode=0o700)
    manifest_copy = frozen / "manifest.json"
    base._write_new(manifest_copy, Path(exact["manifest_path"]).read_bytes())
    for raw_path in exact["supporting_paths"]:
        source = Path(raw_path)
        base._write_new(frozen / source.name, source.read_bytes())
    frozen_exact = validate_exact8_pairs(manifest_copy)
    if (
        frozen_exact["manifest_file_sha256"] != exact["manifest_file_sha256"]
        or frozen_exact["pair_sha256"] != exact["pair_sha256"]
    ):
        raise PairedBuyNowError("frozen pair copy differs from sealed source")

    materialized = output / "materialized"
    materialized.mkdir(mode=0o700)
    corpus = _corpus_rows(frozen_exact["pairs"])
    corpus_path = materialized / "paired_corpus.jsonl"
    base._write_new(corpus_path, _corpus_payload(corpus))
    training_output = output / "training/prime_output"
    render_audit = _run_prime_child(
        prime_path,
        [
            "_prime-materialize",
            "--corpus",
            str(corpus_path),
            "--model",
            str(parent_model),
            "--training-output",
            str(training_output),
        ],
    )
    render_audit_path = materialized / "render_audit.json"
    base._write_new(
        render_audit_path, (base.canonical_json(render_audit) + "\n").encode()
    )
    batch_audit = _run_prime_child(
        prime_path,
        [
            "_prime-audit",
            "--training-output",
            str(training_output),
            "--audit",
            str(render_audit_path),
        ],
    )

    source_dcp = parent["receipt"]["final_dcp"]
    if source_dcp.get("tree_sha256") != PARENT_DCP_TREE_SHA256:
        raise PairedBuyNowError("sealed step-25 DCP identity drifted")
    bridge = _link_sealed_dcp_once(
        Path(source_dcp["path"]),
        training_output / f"checkpoints/step_{SOURCE_STEP}/trainer",
        source_dcp,
    )
    trainer_path = training_output / "configs/trainer.toml"
    control_path = training_output / "run_default/control/orch.toml"
    base._write_new(
        trainer_path,
        _render_trainer_config(
            parent_model=parent_model,
            output=training_output,
            targets=targets,
            topology_name=topology_name,
        ),
    )
    base._write_new(
        control_path,
        _render_control_config(
            parent_model=parent_model, run_output=training_output / "run_default"
        ),
    )
    _validate_configs(
        trainer_path=trainer_path,
        control_path=control_path,
        parent_model=parent_model,
        output=training_output,
        targets=targets,
        topology_name=topology_name,
    )
    config_runtime = base._validate_prime_configs(
        prime_path, trainer_path, control_path
    )
    body = {
        "schema": PLAN_SCHEMA,
        "status": "prepared",
        "scientific_label": SCIENTIFIC_LABEL,
        "objective": "paired_chosen_ce_plus_bounded_rejected_token_unlikelihood",
        "objective_coefficients": {
            "chosen_pre_action_tail_ce": "40/100",
            "chosen_action_ce": "30/100",
            "rejected_semantic_unlikelihood": "30/100",
        },
        "custom_loss": {
            "import_path": CUSTOM_LOSS_IMPORT,
            "formula": "-log(1-probability_cap*p_theta(rejected_token|rejected_prefix))",
            "probability_cap": PROBABILITY_CAP,
            "precision": "float32",
        },
        "on_policy": False,
        "policy_gradient": False,
        "reference_logprobs": False,
        "artifact_git_sha": artifact_git_sha,
        "prime": prime,
        "custom_loss_capability": custom_capability,
        "parent_receipt_path": str(Path(parent_receipt_path).resolve()),
        "parent_receipt_sha256": PARENT_RECEIPT_SHA256,
        "parent_receipt_body_sha256": PARENT_RECEIPT_BODY_SHA256,
        "parent_model": str(parent_model),
        "parent_candidate": parent_candidate,
        "source_dcp": source_dcp,
        "checkpoint_bridge": bridge,
        "frozen_exact8_manifest": _file_descriptor(manifest_copy),
        "frozen_pair_file": _file_descriptor(
            Path(frozen_exact["pair_path"]), rows=PAIR_COUNT
        ),
        "pair_source_kind": frozen_exact["source_kind"],
        "pair_counts": {
            "states": PAIR_COUNT,
            "chosen": PAIR_COUNT,
            "rejected": PAIR_COUNT,
            "per_update": PAIR_COUNT,
            "updates": OPTIMIZER_UPDATES,
            "evaluation": 0,
            "heldout": 0,
            "variants": VARIANT_COUNTS,
        },
        "corpus": _file_descriptor(corpus_path, rows=SAMPLES_PER_STEP),
        "render_audit": _file_descriptor(render_audit_path),
        "render_audit_body_sha256": render_audit["audit_body_sha256"],
        "weight_audit": render_audit["weight_audit"],
        "training_batches": render_audit["training_batches"],
        "training_batch_audit": batch_audit,
        "trainer_config": _file_descriptor(trainer_path),
        "control_config": _file_descriptor(control_path),
        "prime_config_validation": config_runtime,
        "training_output": str(training_output),
        "candidate_path": str(
            training_output / f"weights/step_{FINAL_STEP}/lora_adapters"
        ),
        "candidate_name": "step29-paired-buy-now-unlikelihood",
        "trainer_topology": topology,
        "topology_fallback_reason": fallback_reason,
        "renderer": {"name": "qwen3.5", "enable_thinking": True},
        "semantic_mask": {
            "chosen_pre_action_tail_tokens": CHOSEN_TAIL_TOKENS,
            "rejected_start": "first_unsafe_purchase_decision_phrase",
            "json_syntax": "excluded",
            "prompt": "excluded",
        },
        "lora": {"rank": LORA_RANK, "alpha": LORA_ALPHA, "targets": targets},
        "source_step": SOURCE_STEP,
        "update_steps": list(UPDATE_STEPS),
        "final_step": FINAL_STEP,
        "optimizer_updates": OPTIMIZER_UPDATES,
        "learning_rate": LEARNING_RATE,
        "fresh_optimizer": True,
        "fresh_scheduler": True,
        "fresh_dataloader": True,
        "resume_order": base.RESUME_ORDER_CONTRACT,
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


def _validate_render_audit(path: Path, training_output: Path) -> dict[str, Any]:
    audit = _read_json(path)
    body = {key: value for key, value in audit.items() if key != "audit_body_sha256"}
    weights = audit.get("weight_audit")
    if (
        audit.get("schema") != RENDER_AUDIT_SCHEMA
        or audit.get("status") != "ok"
        or audit.get("audit_body_sha256")
        != hashlib.sha256(_canonical_bytes(body)).hexdigest()
        or audit.get("source_step") != SOURCE_STEP
        or audit.get("update_steps") != list(UPDATE_STEPS)
        or audit.get("optimizer_updates") != OPTIMIZER_UPDATES
        or audit.get("pairs_per_step") != PAIR_COUNT
        or audit.get("samples_per_step") != SAMPLES_PER_STEP
        or audit.get("chosen_samples_per_step") != PAIR_COUNT
        or audit.get("rejected_samples_per_step") != PAIR_COUNT
        or audit.get("probability_cap") != PROBABILITY_CAP
        or not isinstance(weights, Mapping)
        or weights.get("prompt_tokens_weighted") != 0
        or weights.get("chosen_tail_tokens_per_state") != CHOSEN_TAIL_TOKENS
    ):
        raise PairedBuyNowError("paired render audit policy drifted")
    expected_coefficients = {
        "chosen_tail": CHOSEN_TAIL_COEFFICIENT,
        "chosen_action": CHOSEN_ACTION_COEFFICIENT,
        "rejected_unlikelihood": REJECTED_UNLIKELIHOOD_COEFFICIENT,
    }
    for name, expected in expected_coefficients.items():
        item = weights.get("buckets", {}).get(name, {})
        expected_text = f"{expected.numerator}/{expected.denominator}"
        if (
            item.get("coefficient") != expected_text
            or item.get("states") != PAIR_COUNT
            or not math.isclose(
                item.get("normalized_coefficient", -1.0),
                float(expected),
                abs_tol=1e-12,
            )
        ):
            raise PairedBuyNowError(f"paired {name} coefficient drifted")
    rows = audit.get("row_audits")
    if not isinstance(rows, list) or len(rows) != SAMPLES_PER_STEP:
        raise PairedBuyNowError("paired render row inventory drifted")
    for row in rows:
        if row.get("kind") == "chosen":
            if (
                row.get("chosen_tail_tokens") != CHOSEN_TAIL_TOKENS
                or row.get("chosen_action_tokens", 0) <= 0
                or row.get("rejected_unlikelihood_tokens") != 0
                or row.get("rl_weight_mass") != 0.0
            ):
                raise PairedBuyNowError("chosen render mask drifted")
        elif row.get("kind") == "rejected":
            if (
                row.get("chosen_tail_tokens") != 0
                or row.get("chosen_action_tokens") != 0
                or row.get("rejected_unlikelihood_tokens", 0) <= 0
                or row.get("ce_weight_mass") != 0.0
                or not isinstance(row.get("unsafe_anchor_char"), int)
            ):
                raise PairedBuyNowError("rejected render mask drifted")
        else:
            raise PairedBuyNowError("paired render kind drifted")
    _audit_prime_batches(training_output=training_output, audit_path=path)
    return audit


def validate_training_plan(
    path: str | Path, *, require_launch_patches: bool = False
) -> dict[str, Any]:
    plan_path = Path(path).resolve()
    plan = base._self_hashed(
        plan_path, schema=PLAN_SCHEMA, hash_field="plan_body_sha256"
    )
    topology = plan.get("trainer_topology")
    topology_name = topology.get("name") if isinstance(topology, Mapping) else None
    base._select_topology(str(topology_name), plan.get("topology_fallback_reason"))
    if (
        plan.get("status") != "prepared"
        or plan.get("scientific_label") != SCIENTIFIC_LABEL
        or plan.get("objective")
        != "paired_chosen_ce_plus_bounded_rejected_token_unlikelihood"
        or plan.get("objective_coefficients")
        != {
            "chosen_pre_action_tail_ce": "40/100",
            "chosen_action_ce": "30/100",
            "rejected_semantic_unlikelihood": "30/100",
        }
        or plan.get("custom_loss")
        != {
            "import_path": CUSTOM_LOSS_IMPORT,
            "formula": "-log(1-probability_cap*p_theta(rejected_token|rejected_prefix))",
            "probability_cap": PROBABILITY_CAP,
            "precision": "float32",
        }
        or plan.get("on_policy") is not False
        or plan.get("policy_gradient") is not False
        or plan.get("reference_logprobs") is not False
        or _HEX40.fullmatch(str(plan.get("artifact_git_sha"))) is None
        or plan.get("source_step") != SOURCE_STEP
        or plan.get("update_steps") != list(UPDATE_STEPS)
        or plan.get("final_step") != FINAL_STEP
        or plan.get("optimizer_updates") != OPTIMIZER_UPDATES
        or plan.get("learning_rate") != LEARNING_RATE
        or plan.get("fresh_optimizer") is not True
        or plan.get("fresh_scheduler") is not True
        or plan.get("fresh_dataloader") is not True
        or plan.get("resume_order") != base.RESUME_ORDER_CONTRACT
        or plan.get("launch_authorized") is not True
    ):
        raise PairedBuyNowError("paired training plan policy drifted")
    if plan_path != Path(plan["training_output"]).resolve().parent / "plan.json":
        raise PairedBuyNowError("paired training plan is outside its training directory")
    artifacts = [
        (Path(plan["parent_receipt_path"]), plan["parent_receipt_sha256"]),
        (Path(plan["frozen_exact8_manifest"]["path"]), plan["frozen_exact8_manifest"]["sha256"]),
        (Path(plan["frozen_pair_file"]["path"]), plan["frozen_pair_file"]["sha256"]),
        (Path(plan["corpus"]["path"]), plan["corpus"]["sha256"]),
        (Path(plan["render_audit"]["path"]), plan["render_audit"]["sha256"]),
        (Path(plan["trainer_config"]["path"]), plan["trainer_config"]["sha256"]),
        (Path(plan["control_config"]["path"]), plan["control_config"]["sha256"]),
    ]
    artifacts.extend((Path(item["path"]), item["sha256"]) for item in plan["training_batches"])
    if any(sha256_file(source) != expected for source, expected in artifacts):
        raise PairedBuyNowError("plan-bound paired artifact changed")
    parent = validate_step25_parent_fast(plan["parent_receipt_path"])
    if (
        plan.get("parent_model") != parent["plan"]["parent_model"]
        or plan.get("parent_candidate") != parent["receipt"]["candidate"]
        or plan.get("source_dcp") != parent["receipt"]["final_dcp"]
    ):
        raise PairedBuyNowError("paired plan parent differs from sealed step 25")
    exact = validate_exact8_pairs(plan["frozen_exact8_manifest"]["path"])
    if Path(plan["corpus"]["path"]).read_bytes() != _corpus_payload(
        _corpus_rows(exact["pairs"])
    ):
        raise PairedBuyNowError("paired corpus differs from frozen pairs")
    training_output = Path(plan["training_output"])
    render = _validate_render_audit(Path(plan["render_audit"]["path"]), training_output)
    if (
        render["audit_body_sha256"] != plan.get("render_audit_body_sha256")
        or render["weight_audit"] != plan.get("weight_audit")
    ):
        raise PairedBuyNowError("paired render evidence differs from plan")
    prime_root = Path(plan["prime"]["root"])
    prime = base.validate_prime_source(
        prime_root, require_launch_patches=require_launch_patches
    )
    if prime["commit"] != plan.get("prime", {}).get("commit"):
        raise PairedBuyNowError("paired plan PRIME identity drifted")
    if _validate_custom_loss_capability(prime_root) != plan.get("custom_loss_capability"):
        raise PairedBuyNowError("PRIME custom loss capability drifted")
    targets = plan["lora"]["targets"]
    _validate_configs(
        trainer_path=Path(plan["trainer_config"]["path"]),
        control_path=Path(plan["control_config"]["path"]),
        parent_model=Path(plan["parent_model"]),
        output=training_output,
        targets=targets,
        topology_name=str(topology_name),
    )
    runtime = base._validate_prime_configs(
        prime_root,
        Path(plan["trainer_config"]["path"]),
        Path(plan["control_config"]["path"]),
    )
    if runtime != plan.get("prime_config_validation"):
        raise PairedBuyNowError("paired PRIME config validation drifted")
    _validate_sealed_dcp_bridge(plan)
    if plan.get("trainer_command") != trainer_command(prime_root, training_output):
        raise PairedBuyNowError("paired trainer command drifted")
    return plan


def _trainer_log_audit(log_root: Path, topology: Mapping[str, Any]) -> dict[str, Any]:
    if not log_root.is_dir() or log_root.is_symlink():
        raise PairedBuyNowError("paired torchrun logs are absent")
    texts = [
        path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(log_root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]
    joined = "\n".join(texts)
    required = [
        topology["mesh_log"],
        "Registering single run before checkpoint restore",
        "SINGLE_RUN_OPTIMIZER_BINDING_AUDIT",
        f"Resuming training from checkpoint step {SOURCE_STEP}",
        "DCP_LOADED_LORA_SIGNATURE",
        f"Starting from step {UPDATE_STEPS[0]}",
        "RESUMED_LORA_PRE_UPDATE_AUDIT",
        *(f"Step {step} |" for step in UPDATE_STEPS),
        "Writing final checkpoint",
        "Writing final weight checkpoint",
        "RL trainer finished!",
    ]
    forbidden = (
        f"Step {FINAL_STEP + 1} |",
        "Traceback (most recent call last)",
        "CUDA out of memory",
    )
    if any(item not in joined for item in required) or any(item in joined for item in forbidden):
        raise PairedBuyNowError("trainer logs do not prove four clean paired updates")
    return {
        "tree": _tree_identity(log_root),
        "required_milestones": required,
        "forbidden_milestones_absent": list(forbidden),
    }


def write_training_receipt(
    *, plan_path: str | Path, executor_git_sha: str
) -> dict[str, Any]:
    if _HEX40.fullmatch(executor_git_sha) is None:
        raise PairedBuyNowError("executor Git SHA must be 40 lowercase hex characters")
    plan = validate_training_plan(plan_path, require_launch_patches=True)
    if executor_git_sha != plan["artifact_git_sha"]:
        raise PairedBuyNowError("executor source differs from planned source")
    output = Path(plan["training_output"])
    if (output / f"checkpoints/step_{FINAL_STEP + 1}").exists() or (
        output / f"weights/step_{FINAL_STEP + 1}"
    ).exists():
        raise PairedBuyNowError("trainer executed more than four updates")
    candidate_path = Path(plan["candidate_path"])
    candidate = _tree_identity(candidate_path)
    stable = candidate_path.parent / "STABLE"
    if not stable.is_file() or stable.is_symlink():
        raise PairedBuyNowError("paired final adapter is not STABLE")
    if _adapter_targets(candidate_path) != sorted(plan["lora"]["targets"]):
        raise PairedBuyNowError("paired final adapter targets drifted")
    final_dcp = _require_dcp_inventory(
        output / f"checkpoints/step_{FINAL_STEP}/trainer",
        trainer_world_size=base.TRAINER_WORLD_SIZE,
        includes_dataloader=False,
    )
    token_exports = {}
    for step in UPDATE_STEPS:
        export = output / f"run_default/token_exports/step_{step}"
        stable_export = export / "STABLE"
        rank_files = sorted(export.glob("rank_*.jsonl")) if export.is_dir() else []
        if (
            not stable_export.is_file()
            or stable_export.is_symlink()
            or len(rank_files) != plan["trainer_topology"]["data_parallel_size"]
        ):
            raise PairedBuyNowError(f"step {step} token exports are not complete")
        token_exports[str(step)] = {
            "tree": _tree_identity(export),
            "stable_sha256": sha256_file(stable_export),
            "rank_files": len(rank_files),
        }
    body = {
        "schema": RECEIPT_SCHEMA,
        "status": "ok",
        "scientific_label": SCIENTIFIC_LABEL,
        "artifact_source_git_sha": plan["artifact_git_sha"],
        "execution_source_git_sha": executor_git_sha,
        "prime_commit": base.PRIME_COMMIT,
        "plan_path": str(Path(plan_path).resolve()),
        "plan_sha256": sha256_file(Path(plan_path)),
        "plan_body_sha256": plan["plan_body_sha256"],
        "source_step": SOURCE_STEP,
        "update_steps": list(UPDATE_STEPS),
        "final_step": FINAL_STEP,
        "optimizer_updates": OPTIMIZER_UPDATES,
        "learning_rate": LEARNING_RATE,
        "objective": plan["objective"],
        "objective_coefficients": plan["objective_coefficients"],
        "custom_loss": plan["custom_loss"],
        "trainer_topology": plan["trainer_topology"],
        "weight_audit": plan["weight_audit"],
        "training_batches": plan["training_batches"],
        "token_exports": token_exports,
        "trainer_logs": _trainer_log_audit(
            output / "logs/trainer/torchrun", plan["trainer_topology"]
        ),
        "prime_launch_patches": base._patch_evidence(
            Path(plan_path).resolve().parent / "launch_evidence"
        ),
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
        Path(plan_path).resolve().with_name("training_receipt.json"),
        body,
        hash_field="receipt_body_sha256",
    )


def validate_training_receipt(path: str | Path) -> dict[str, Any]:
    receipt_path = Path(path).resolve()
    receipt = base._self_hashed(
        receipt_path, schema=RECEIPT_SCHEMA, hash_field="receipt_body_sha256"
    )
    if (
        receipt.get("status") != "ok"
        or receipt.get("scientific_label") != SCIENTIFIC_LABEL
        or receipt.get("artifact_source_git_sha")
        != receipt.get("execution_source_git_sha")
        or receipt.get("prime_commit") != base.PRIME_COMMIT
        or receipt.get("source_step") != SOURCE_STEP
        or receipt.get("update_steps") != list(UPDATE_STEPS)
        or receipt.get("final_step") != FINAL_STEP
        or receipt.get("optimizer_updates") != OPTIMIZER_UPDATES
        or receipt.get("learning_rate") != LEARNING_RATE
    ):
        raise PairedBuyNowError("paired training receipt policy drifted")
    plan_path = Path(receipt["plan_path"])
    if sha256_file(plan_path) != receipt.get("plan_sha256"):
        raise PairedBuyNowError("receipt-bound paired plan changed")
    plan = validate_training_plan(plan_path, require_launch_patches=True)
    if (
        receipt.get("plan_body_sha256") != plan["plan_body_sha256"]
        or receipt.get("objective") != plan["objective"]
        or receipt.get("objective_coefficients") != plan["objective_coefficients"]
        or receipt.get("custom_loss") != plan["custom_loss"]
        or receipt.get("weight_audit") != plan["weight_audit"]
        or receipt.get("training_batches") != plan["training_batches"]
    ):
        raise PairedBuyNowError("paired receipt provenance differs from plan")
    output = Path(plan["training_output"])
    final_dcp = _require_dcp_inventory(
        output / f"checkpoints/step_{FINAL_STEP}/trainer",
        expected_identity=receipt["final_dcp"],
        trainer_world_size=base.TRAINER_WORLD_SIZE,
        includes_dataloader=False,
    )
    candidate_path = Path(plan["candidate_path"])
    candidate = _tree_identity(candidate_path)
    recorded = receipt["candidate"]
    if (
        recorded.get("name") != "step29-paired-buy-now-unlikelihood"
        or recorded.get("update") != FINAL_STEP
        or any(
            candidate[key] != recorded.get(key)
            for key in ("path", "files", "bytes", "tree_sha256")
        )
        or sha256_file(candidate_path / "adapter_config.json")
        != recorded.get("adapter_config_sha256")
        or sha256_file(candidate_path.parent / "STABLE")
        != recorded.get("stable_marker_sha256")
    ):
        raise PairedBuyNowError("paired receipt candidate changed")
    if final_dcp != receipt["final_dcp"] or receipt.get("trainer_logs") != _trainer_log_audit(
        output / "logs/trainer/torchrun", plan["trainer_topology"]
    ):
        raise PairedBuyNowError("paired receipt execution evidence changed")
    return receipt


def _prime_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    commands = parser.add_subparsers(dest="command", required=True)
    materialize = commands.add_parser("_prime-materialize")
    materialize.add_argument("--corpus", type=Path, required=True)
    materialize.add_argument("--model", type=Path, required=True)
    materialize.add_argument("--training-output", type=Path, required=True)
    audit = commands.add_parser("_prime-audit")
    audit.add_argument("--training-output", type=Path, required=True)
    audit.add_argument("--audit", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "_prime-materialize":
        result = _materialize_prime_batches(
            corpus_path=args.corpus.resolve(),
            model_path=args.model.resolve(),
            training_output=args.training_output.resolve(),
        )
    else:
        result = _audit_prime_batches(
            training_output=args.training_output.resolve(), audit_path=args.audit.resolve()
        )
    print(base.canonical_json(result))
    return 0


if __name__ == "__main__":  # pragma: no cover - PRIME subprocess entrypoint.
    raise SystemExit(_prime_main())


__all__ = [
    "CHOSEN_ACTION_COEFFICIENT",
    "CHOSEN_TAIL_COEFFICIENT",
    "CHOSEN_TAIL_TOKENS",
    "FINAL_STEP",
    "LEARNING_RATE",
    "PROBABILITY_CAP",
    "REJECTED_UNLIKELIHOOD_COEFFICIENT",
    "SOURCE_STEP",
    "UPDATE_STEPS",
    "assign_paired_weights",
    "prepare_paired_buy_now_training",
    "trainer_command",
    "validate_exact8_pairs",
    "validate_training_plan",
    "validate_training_receipt",
    "write_training_receipt",
]
