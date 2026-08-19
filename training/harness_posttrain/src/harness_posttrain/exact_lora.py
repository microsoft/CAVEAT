"""Fail-closed exact-LoRA finalization after a lossy BF16 merge.

The trained refinement adapter is defined relative to the selected, merged SFT
parent.  When folding that adapter into BF16 parent weights does not preserve
its behavior, the scientifically faithful inference artifact is the unmerged
PEFT composite.  The baseline arm receives a structurally identical all-zero
adapter so both arms exercise the same LoRA serving path.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import math
import os
import shutil
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .artifacts import (
    ArtifactError,
    canonical_json,
    publish_json,
    read_json,
    sha256_bytes,
    sha256_file,
    tree_digest,
)
from .config import Campaign
from .parent_merge import (
    _collect,
    _logits,
    _validate_adapter_parent,
    _validate_parent,
    checkpoint_kind,
    checkpoint_manifest,
)

EXACT_LORA_MANIFEST_SCHEMA = "harness-posttrain.exact-lora-inference.v1"
EXACT_LORA_COMPOSITE_SCHEMA = "harness-posttrain.exact-lora-composite.v1"
ZERO_ADAPTER_SCHEMA = "harness-posttrain.zero-adapter-attestation.v1"
MERGE_DIAGNOSTIC_SCHEMA = "harness-posttrain.bf16-merge-diagnostic.v1"

_MERGE_THRESHOLDS = {
    "max_relative_l2": 0.10,
    "min_cosine": 0.99,
    "max_symmetric_kl": 0.01,
    "min_top5_overlap": 0.8,
    "max_error_ratio": 0.5,
}

_TOKENIZER_SOURCE_KEYS = frozenset(
    {"name_or_path", "vocab_file", "merges_file", "tokenizer_file"}
)


def _checkpoint(receipt: dict[str, Any], update: int) -> dict[str, Any]:
    values = receipt.get("checkpoints")
    if not isinstance(values, list):
        raise ArtifactError("refinement receipt has no checkpoint inventory")
    matches = [row for row in values if isinstance(row, dict) and row.get("update") == update]
    if len(matches) != 1:
        raise ArtifactError(f"refinement receipt does not attest exactly one step-{update} adapter")
    return matches[0]


def _verified_refinement_inputs(
    campaign: Campaign, root: Path
) -> tuple[Path, dict[str, Any], Path, Path]:
    """Resolve the exact parent and step-20 adapter already bound by the receipt."""

    receipt_path = root / "refinement/training_receipt.json"
    receipt = read_json(receipt_path)
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != "harness-posttrain.refinement-training-receipt.v1"
        or receipt.get("status") != "ok"
        or receipt.get("campaign_digest") != campaign.digest
        or receipt.get("optimizer_updates") != 20
    ):
        raise ArtifactError("exact-LoRA finalization requires the successful refinement receipt")

    post_path = root / "post_sft_receipt.json"
    if receipt.get("post_sft_receipt_sha256") != sha256_file(post_path):
        raise ArtifactError("post-SFT receipt changed after refinement training")
    parent = (root / "selected/merged").resolve()
    if Path(str(receipt.get("parent_model", ""))).resolve() != parent or receipt.get(
        "parent_merge_provenance_sha256"
    ) != sha256_file(parent / "merge_provenance.json"):
        raise ArtifactError("selected SFT parent changed after refinement training")
    _validate_parent(parent)

    final_checkpoint = _checkpoint(receipt, 20)
    adapter = Path(str(final_checkpoint.get("path", ""))).resolve()
    if not adapter.is_dir() or checkpoint_kind(adapter) != "peft_adapter":
        raise ArtifactError("attested final refinement adapter is absent or not PEFT")
    if final_checkpoint.get("stable_marker_sha256") != sha256_file(adapter.parent / "STABLE"):
        raise ArtifactError("final refinement completion marker changed after its receipt")
    adapter_files = [item for item in adapter.rglob("*") if item.is_file()]
    if final_checkpoint.get("files") != len(adapter_files) or final_checkpoint.get(
        "sha256"
    ) != tree_digest(adapter_files, adapter):
        raise ArtifactError("final refinement adapter bytes changed after its receipt")
    _validate_adapter_parent(adapter, parent)
    return receipt_path, receipt, parent, adapter


def _verified_raw_base(campaign: Campaign, root: Path, raw_base: Path) -> Path:
    smoke_path = root / "smoke/smoke_report.json"
    smoke = read_json(smoke_path)
    snapshot = smoke.get("snapshot", {}) if isinstance(smoke, dict) else {}
    if (
        not isinstance(smoke, dict)
        or smoke.get("status") != "ok"
        or snapshot.get("model_id") != campaign.model["model_id"]
        or snapshot.get("revision") != campaign.model["revision"]
        or Path(str(smoke.get("resolved_snapshot", ""))).resolve() != raw_base
        or raw_base.name != campaign.model["revision"]
        or checkpoint_kind(raw_base) != "merged_hf"
    ):
        raise ArtifactError("raw base does not match the smoke-tested immutable snapshot")
    return smoke_path


def _regular_file_digest(path: Path) -> str:
    """Hash a resolved regular file, including an HF snapshot symlink target."""

    resolved = path.resolve(strict=True)
    metadata = resolved.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ArtifactError(f"component entry does not resolve to a regular file: {path}")
    digest = hashlib.sha256()
    with resolved.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def component_identity(path: str | Path) -> dict[str, Any]:
    """Hash a logical component tree without depending on HF symlink targets.

    Hugging Face snapshots contain file symlinks into a content-addressed blob
    store.  The logical relative name and target bytes define the component;
    symlink destination strings do not.  Directory symlinks and special files
    are rejected to keep traversal finite and unambiguous.
    """

    root = Path(path).resolve()
    if not root.is_dir():
        raise ArtifactError(f"component directory is absent: {root}")
    entries: dict[str, dict[str, Any]] = {}
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in directories:
            candidate = current_path / name
            if candidate.is_symlink():
                raise ArtifactError(f"component contains a directory symlink: {candidate}")
        for name in files:
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            resolved = candidate.resolve(strict=True)
            metadata = resolved.stat()
            if not stat.S_ISREG(metadata.st_mode):
                raise ArtifactError(f"component contains a non-regular file: {candidate}")
            entries[relative] = {
                "size": metadata.st_size,
                "sha256": _regular_file_digest(candidate),
            }
    if not entries:
        raise ArtifactError(f"component tree is empty: {root}")
    return {
        "path": str(root),
        "files": len(entries),
        "tree_sha256": sha256_bytes(canonical_json(entries).encode()),
    }


def _adapter_weight(adapter: Path) -> Path:
    safetensors = sorted(adapter.glob("adapter_model*.safetensors"))
    legacy = sorted(adapter.glob("adapter_model*.bin"))
    if len(safetensors) != 1 or legacy:
        raise ArtifactError("exact-LoRA finalization requires one safetensors adapter weight file")
    return safetensors[0]


def _tensor_inventory(weight: Path) -> dict[str, Any]:
    try:
        from safetensors import safe_open
    except ImportError as exc:  # pragma: no cover - cluster-only dependency
        raise ArtifactError("safetensors is required for exact-LoRA finalization") from exc

    tensors: dict[str, dict[str, Any]] = {}
    total_elements = 0
    total_bytes = 0
    total_nonzero = 0
    with safe_open(weight, framework="pt", device="cpu") as stream:
        metadata = dict(sorted((stream.metadata() or {}).items()))
        keys = sorted(stream.keys())
        if not keys:
            raise ArtifactError("adapter safetensors file contains no tensors")
        for key in keys:
            tensor = stream.get_tensor(key)
            try:
                import torch

                finite = bool(torch.isfinite(tensor).all().item())
                nonzero = int(torch.count_nonzero(tensor).item())
            except (ImportError, RuntimeError, TypeError) as exc:
                raise ArtifactError(f"cannot inspect adapter tensor {key!r}") from exc
            if not finite:
                raise ArtifactError(f"adapter tensor is non-finite: {key}")
            elements = int(tensor.numel())
            byte_count = elements * int(tensor.element_size())
            tensors[key] = {
                "shape": list(tensor.shape),
                "dtype": str(tensor.dtype).removeprefix("torch."),
                "elements": elements,
                "bytes": byte_count,
            }
            total_elements += elements
            total_bytes += byte_count
            total_nonzero += nonzero
    return {
        "tensors": tensors,
        "tensor_count": len(tensors),
        "element_count": total_elements,
        "byte_count": total_bytes,
        "nonzero_elements": total_nonzero,
        "all_zero": total_nonzero == 0,
        "metadata": metadata,
        "inventory_sha256": sha256_bytes(canonical_json(tensors).encode()),
    }


def _save_zero_weights(source: Path, output: Path) -> None:
    try:
        import torch
        from safetensors import safe_open
        from safetensors.torch import save_file
    except ImportError as exc:  # pragma: no cover - cluster-only dependency
        raise ArtifactError("torch and safetensors are required to write a zero adapter") from exc

    zeroes: dict[str, Any] = {}
    with safe_open(source, framework="pt", device="cpu") as stream:
        metadata = stream.metadata()
        for key in sorted(stream.keys()):
            tensor = stream.get_tensor(key)
            if not torch.isfinite(tensor).all():
                raise ArtifactError(f"source adapter tensor is non-finite: {key}")
            zeroes[key] = torch.zeros_like(tensor).contiguous()
    if not zeroes:
        raise ArtifactError("source adapter contains no tensors")
    save_file(zeroes, output, metadata=metadata)
    with output.open("rb") as stream:
        os.fsync(stream.fileno())


def _config_structure(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _config_structure(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_config_structure(item) for item in value]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    raise ArtifactError(f"unsupported adapter config value type: {type(value).__name__}")


def _semantic_json_value(value: Any) -> Any:
    """Convert tokenizer runtime values to a lossless canonical JSON value."""

    if isinstance(value, Mapping):
        return {
            str(key): _semantic_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_semantic_json_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    added_token_fields = ("content", "single_word", "lstrip", "rstrip", "normalized", "special")
    if all(hasattr(value, field) for field in added_token_fields):
        return {
            "type": f"{type(value).__module__}.{type(value).__qualname__}",
            **{field: _semantic_json_value(getattr(value, field)) for field in added_token_fields},
        }
    raise ArtifactError(
        f"unsupported tokenizer semantic value type: {type(value).__module__}."
        f"{type(value).__qualname__}"
    )


def _canonical_identity(value: Any) -> str:
    return sha256_bytes(canonical_json(_semantic_json_value(value)).encode())


def tokenizer_semantic_equivalence(
    raw_tokenizer: Any,
    shared_tokenizer: Any,
    *,
    raw_tokenizer_json: str | Path,
    shared_tokenizer_json: str | Path,
) -> dict[str, Any]:
    """Prove two differently serialized tokenizers load to one exact runtime.

    Saving a tokenizer can materialize config-supplied special tokens, normalize
    BPE merge representation, and write current ByteLevel defaults.  Raw file
    equality is therefore too weak a scientific criterion: it rejects an exact
    load/save normalization.  This gate compares the complete live Rust
    tokenizer graph plus the Python wrapper state after removing only four
    source-location fields.  Vocabulary, added-token, special-token, template,
    and wrapper identities are independently recorded for auditability.
    """

    raw_class = f"{type(raw_tokenizer).__module__}.{type(raw_tokenizer).__qualname__}"
    shared_class = f"{type(shared_tokenizer).__module__}.{type(shared_tokenizer).__qualname__}"
    if raw_class != shared_class:
        raise ArtifactError("selected parent changed the tokenizer class")

    try:
        raw_backend = json.loads(raw_tokenizer.backend_tokenizer.to_str(pretty=False))
        shared_backend = json.loads(shared_tokenizer.backend_tokenizer.to_str(pretty=False))
    except (AttributeError, TypeError, json.JSONDecodeError) as exc:
        raise ArtifactError("tokenizer backend cannot be canonically inspected") from exc
    if raw_backend != shared_backend:
        raise ArtifactError("selected parent changed the loaded tokenizer backend graph")

    raw_vocab = raw_tokenizer.get_vocab()
    shared_vocab = shared_tokenizer.get_vocab()
    if raw_vocab != shared_vocab:
        raise ArtifactError("selected parent changed the token-to-ID vocabulary")
    raw_added = raw_tokenizer.get_added_vocab()
    shared_added = shared_tokenizer.get_added_vocab()
    if raw_added != shared_added:
        raise ArtifactError("selected parent changed the added-token vocabulary")

    raw_special = {
        "map": raw_tokenizer.special_tokens_map,
        "ids": list(raw_tokenizer.all_special_ids),
        "tokens": list(raw_tokenizer.all_special_tokens),
    }
    shared_special = {
        "map": shared_tokenizer.special_tokens_map,
        "ids": list(shared_tokenizer.all_special_ids),
        "tokens": list(shared_tokenizer.all_special_tokens),
    }
    if raw_special != shared_special:
        raise ArtifactError("selected parent changed special-token semantics")

    raw_init = {
        key: value
        for key, value in raw_tokenizer.init_kwargs.items()
        if key not in _TOKENIZER_SOURCE_KEYS
    }
    shared_init = {
        key: value
        for key, value in shared_tokenizer.init_kwargs.items()
        if key not in _TOKENIZER_SOURCE_KEYS
    }
    if raw_init != shared_init:
        raise ArtifactError("selected parent changed tokenizer wrapper settings")
    raw_template = str(getattr(raw_tokenizer, "chat_template", ""))
    shared_template = str(getattr(shared_tokenizer, "chat_template", ""))
    if not raw_template or raw_template != shared_template:
        raise ArtifactError("selected parent changed the tokenizer chat template")
    if (
        len(raw_tokenizer) != len(shared_tokenizer)
        or raw_tokenizer.vocab_size != shared_tokenizer.vocab_size
    ):
        raise ArtifactError("selected parent changed tokenizer dimensions")

    backend_sha256 = _canonical_identity(raw_backend)
    vocab_sha256 = _canonical_identity(raw_vocab)
    added_sha256 = _canonical_identity(raw_added)
    special_sha256 = _canonical_identity(raw_special)
    wrapper_sha256 = _canonical_identity(raw_init)
    template_sha256 = sha256_bytes(raw_template.encode())
    semantic_sha256 = sha256_bytes(
        canonical_json(
            {
                "schema": "harness-posttrain.tokenizer-semantics.v1",
                "tokenizer_class": raw_class,
                "backend_sha256": backend_sha256,
                "vocab_sha256": vocab_sha256,
                "added_vocab_sha256": added_sha256,
                "special_tokens_sha256": special_sha256,
                "wrapper_settings_sha256": wrapper_sha256,
                "chat_template_sha256": template_sha256,
                "length": len(raw_tokenizer),
                "vocab_size": raw_tokenizer.vocab_size,
            }
        ).encode()
    )
    return {
        "schema": "harness-posttrain.tokenizer-semantic-equivalence.v1",
        "status": "ok",
        "exact_loaded_semantics_equal": True,
        "source_locator_fields_ignored": sorted(_TOKENIZER_SOURCE_KEYS),
        "raw_tokenizer_json_sha256": sha256_file(raw_tokenizer_json),
        "shared_tokenizer_json_sha256": sha256_file(shared_tokenizer_json),
        "serialized_bytes_equal": sha256_file(raw_tokenizer_json)
        == sha256_file(shared_tokenizer_json),
        "tokenizer_class": raw_class,
        "length": len(raw_tokenizer),
        "vocab_size": raw_tokenizer.vocab_size,
        "backend_sha256": backend_sha256,
        "vocab_sha256": vocab_sha256,
        "added_vocab_sha256": added_sha256,
        "special_tokens_sha256": special_sha256,
        "wrapper_settings_sha256": wrapper_sha256,
        "chat_template_sha256": template_sha256,
        "semantic_sha256": semantic_sha256,
    }


def zero_adapter_config(source_config: dict[str, Any], raw_base: Path) -> dict[str, Any]:
    """Return the source config with only its declared parent changed."""

    if not isinstance(source_config, dict) or "base_model_name_or_path" not in source_config:
        raise ArtifactError("source adapter config has no declared base model")
    if not isinstance(source_config["base_model_name_or_path"], str):
        raise ArtifactError("source adapter declared base model is not a string")
    result = copy.deepcopy(source_config)
    result["base_model_name_or_path"] = str(raw_base.resolve())
    if _config_structure(result) != _config_structure(source_config):
        raise ArtifactError("zero adapter changed the LoRA config structure")
    expected = copy.deepcopy(result)
    expected["base_model_name_or_path"] = source_config["base_model_name_or_path"]
    if expected != source_config:
        raise ArtifactError("zero adapter changed fields other than its declared parent")
    return result


def _validate_zero_pair(
    source_adapter: Path, zero_adapter: Path, raw_base: Path
) -> dict[str, Any]:
    source_config = read_json(source_adapter / "adapter_config.json")
    zero_config = read_json(zero_adapter / "adapter_config.json")
    if not isinstance(source_config, dict) or not isinstance(zero_config, dict):
        raise ArtifactError("adapter configuration is not an object")
    expected_config = zero_adapter_config(source_config, raw_base)
    if zero_config != expected_config:
        raise ArtifactError("zero adapter configuration differs from the exact derived config")
    expected_files = {"adapter_config.json", _adapter_weight(source_adapter).name}
    actual_files = {
        item.relative_to(zero_adapter).as_posix()
        for item in zero_adapter.rglob("*")
        if item.is_file()
    }
    if actual_files != expected_files:
        raise ArtifactError("zero adapter has missing or unexpected files")
    source_inventory = _tensor_inventory(_adapter_weight(source_adapter))
    zero_inventory = _tensor_inventory(_adapter_weight(zero_adapter))
    for key in ("tensors", "tensor_count", "element_count", "byte_count", "metadata"):
        if zero_inventory.get(key) != source_inventory.get(key):
            raise ArtifactError(f"zero adapter tensor inventory differs from source: {key}")
    if zero_inventory.get("all_zero") is not True or zero_inventory.get("nonzero_elements") != 0:
        raise ArtifactError("derived baseline adapter contains a nonzero tensor element")
    return {
        "schema": ZERO_ADAPTER_SCHEMA,
        "status": "ok",
        "source_adapter": str(source_adapter),
        "zero_adapter": str(zero_adapter),
        "source_config": source_config,
        "zero_config": zero_config,
        "config_structure_sha256": sha256_bytes(
            canonical_json(_config_structure(source_config)).encode()
        ),
        "source_tensor_inventory": source_inventory,
        "zero_tensor_inventory": zero_inventory,
    }


def create_zero_adapter(
    source_adapter: str | Path,
    raw_base: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Atomically publish an exact-shape, all-zero counterpart to a trained LoRA."""

    source = Path(source_adapter).resolve()
    raw = Path(raw_base).resolve()
    destination = Path(output_dir).resolve()
    if checkpoint_kind(source) != "peft_adapter" or not raw.is_dir():
        raise ArtifactError("zero-adapter construction requires a PEFT source and raw base")
    if destination in {source, raw}:
        raise ArtifactError("zero adapter output aliases an immutable source component")
    source_before = checkpoint_manifest(source)
    source_config = read_json(source / "adapter_config.json")
    if not isinstance(source_config, dict):
        raise ArtifactError("source adapter configuration is not an object")

    if destination.exists() or destination.is_symlink():
        if not destination.is_dir() or destination.is_symlink():
            raise ArtifactError(f"zero adapter output is not a regular directory: {destination}")
        if not any(destination.iterdir()):
            destination.rmdir()
        else:
            report = _validate_zero_pair(source, destination, raw)
            if checkpoint_manifest(source) != source_before:
                raise ArtifactError("source adapter changed while validating the zero adapter")
            return report

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        publish_json(
            staging / "adapter_config.json",
            zero_adapter_config(source_config, raw),
        )
        _save_zero_weights(_adapter_weight(source), staging / _adapter_weight(source).name)
        report = _validate_zero_pair(source, staging, raw)
        if checkpoint_manifest(source) != source_before:
            raise ArtifactError("source adapter changed during zero-adapter construction")
        staging.replace(destination)
        return _validate_zero_pair(source, destination, raw)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _merge_gate_checks(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "adapter_merged_relative_l2": {
            "value": metrics["adapter_merged_relative_l2"],
            "operator": "<=",
            "threshold": _MERGE_THRESHOLDS["max_relative_l2"],
            "passed": metrics["adapter_merged_relative_l2"]
            <= _MERGE_THRESHOLDS["max_relative_l2"],
        },
        "adapter_merged_cosine": {
            "value": metrics["adapter_merged_cosine"],
            "operator": ">=",
            "threshold": _MERGE_THRESHOLDS["min_cosine"],
            "passed": metrics["adapter_merged_cosine"] >= _MERGE_THRESHOLDS["min_cosine"],
        },
        "adapter_merged_symmetric_kl": {
            "value": metrics["adapter_merged_symmetric_kl"],
            "operator": "<=",
            "threshold": _MERGE_THRESHOLDS["max_symmetric_kl"],
            "passed": metrics["adapter_merged_symmetric_kl"]
            <= _MERGE_THRESHOLDS["max_symmetric_kl"],
        },
        "adapter_merged_top5_overlap": {
            "value": metrics["adapter_merged_top5_overlap"],
            "operator": ">=",
            "threshold": _MERGE_THRESHOLDS["min_top5_overlap"],
            "passed": metrics["adapter_merged_top5_overlap"]
            >= _MERGE_THRESHOLDS["min_top5_overlap"],
        },
        "relative_l2_error_ratio": {
            "value": metrics["relative_l2_error_ratio"],
            "operator": "<=",
            "threshold": _MERGE_THRESHOLDS["max_error_ratio"],
            "passed": metrics["relative_l2_error_ratio"]
            <= _MERGE_THRESHOLDS["max_error_ratio"],
        },
        "symmetric_kl_error_ratio": {
            "value": metrics["symmetric_kl_error_ratio"],
            "operator": "<=",
            "threshold": _MERGE_THRESHOLDS["max_error_ratio"],
            "passed": metrics["symmetric_kl_error_ratio"]
            <= _MERGE_THRESHOLDS["max_error_ratio"],
        },
    }


def _provided_merge_diagnostic(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    value = read_json(path)
    if not isinstance(value, dict):
        raise ArtifactError("provided merge failure diagnostic is not a JSON object")
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "record": value,
    }


def verify_exact_lora_behavior(
    raw_base: Path,
    zero_adapter: Path,
    selected_parent: Path,
    trained_adapter: Path,
    *,
    device: str,
    merge_failure_diagnostic: Path | None = None,
    nonnoop_atol: float = 1e-7,
    nonnoop_rtol: float = 1e-6,
) -> dict[str, Any]:
    """Attest zero no-op, trained non-noop, and reproduce the lossy merge."""

    model_smoke = importlib.import_module("harness_distill.model_smoke")
    merge_verifier = importlib.import_module("harness_distill.model_merge")
    runtime = model_smoke.assert_runtime_dependencies(require_cuda=device.startswith("cuda"))

    import torch
    from peft import PeftModel

    torch.manual_seed(20260812)
    raw_processor, raw_tokenizer = model_smoke._load_processor_tokenizer(raw_base)  # noqa: SLF001
    processor, tokenizer = model_smoke._load_processor_tokenizer(selected_parent)  # noqa: SLF001
    raw_template_hash = model_smoke._chat_template_sha256(raw_tokenizer)  # noqa: SLF001
    template_hash = model_smoke._chat_template_sha256(tokenizer)  # noqa: SLF001
    if raw_template_hash != template_hash:
        raise ArtifactError("selected parent changed the raw model chat template")
    tokenizer_equivalence = tokenizer_semantic_equivalence(
        raw_tokenizer,
        tokenizer,
        raw_tokenizer_json=raw_base / "tokenizer.json",
        shared_tokenizer_json=selected_parent / "tokenizer.json",
    )
    tokenizer_json = tokenizer_equivalence["shared_tokenizer_json_sha256"]
    raw_inputs = model_smoke._prompt_tensors(raw_tokenizer, device=device)  # noqa: SLF001
    inputs = model_smoke._prompt_tensors(tokenizer, device=device)  # noqa: SLF001
    if not torch.equal(
        raw_inputs["input_ids"].detach().cpu(), inputs["input_ids"].detach().cpu()
    ):
        raise ArtifactError("selected parent changed deterministic prompt token IDs")
    input_ids_hash = hashlib.sha256(
        inputs["input_ids"].detach().cpu().numpy().tobytes()
    ).hexdigest()
    processor_class = f"{type(processor).__module__}.{type(processor).__qualname__}"
    raw_processor_class = f"{type(raw_processor).__module__}.{type(raw_processor).__qualname__}"
    if raw_processor_class != processor_class:
        raise ArtifactError("selected parent changed the processor class")
    del raw_processor, raw_tokenizer, processor, tokenizer, raw_inputs
    cuda = device.startswith("cuda")

    raw_model = model_smoke._load_model(raw_base, device=device)  # noqa: SLF001
    raw_architectures = tuple(getattr(raw_model.config, "architectures", ()) or ())
    raw_logits = _logits(raw_model, inputs)
    raw_with_zero = PeftModel.from_pretrained(
        raw_model, zero_adapter, is_trainable=False, local_files_only=True
    )
    del raw_model
    zero_logits = _logits(raw_with_zero, inputs)
    zero_max_abs = float((raw_logits - zero_logits).abs().max())
    if not torch.equal(raw_logits, zero_logits) or zero_max_abs != 0.0:
        raise ArtifactError("raw base plus zero LoRA is not an exact deterministic no-op")
    del raw_with_zero, raw_logits, zero_logits
    _collect(cuda=cuda)

    parent_model = model_smoke._load_model(selected_parent, device=device)  # noqa: SLF001
    parent_architectures = tuple(getattr(parent_model.config, "architectures", ()) or ())
    if parent_architectures != raw_architectures:
        raise ArtifactError("selected parent changed the raw model architecture")
    parent_logits = _logits(parent_model, inputs)
    trained_model = PeftModel.from_pretrained(
        parent_model, trained_adapter, is_trainable=False, local_files_only=True
    )
    del parent_model
    trained_logits = _logits(trained_model, inputs)
    trained_max_abs = float((parent_logits - trained_logits).abs().max())
    if torch.allclose(
        parent_logits, trained_logits, atol=nonnoop_atol, rtol=nonnoop_rtol
    ) or trained_max_abs <= nonnoop_atol:
        raise ArtifactError("step-20 adapter is a numerical no-op relative to selected parent")
    trained_relative_l2 = float(
        torch.linalg.vector_norm((parent_logits - trained_logits).reshape(-1))
        / torch.linalg.vector_norm(parent_logits.reshape(-1))
    )
    if not math.isfinite(trained_relative_l2):
        raise ArtifactError("step-20 adapter non-noop metric is non-finite")

    merged_model = trained_model.merge_and_unload(safe_merge=True)
    del trained_model
    merged_logits = _logits(merged_model, inputs)
    behavioral = merge_verifier.behavioral_equivalence_metrics(
        parent_logits, trained_logits, merged_logits
    )
    behavioral_metrics = asdict(behavioral)
    try:
        merge_verifier.validate_behavioral_equivalence(
            behavioral, **_MERGE_THRESHOLDS
        )
    except merge_verifier.MergeVerificationError as exc:
        merge_status = "rejected"
        merge_error = str(exc)
    else:
        merge_status = "accepted"
        merge_error = None
    if merge_status != "rejected":
        raise ArtifactError(
            "recomputed BF16 merge passed; exact-LoRA recovery requires a rejected merge"
        )
    merge_checks = _merge_gate_checks(behavioral_metrics)
    if all(check["passed"] for check in merge_checks.values()):
        raise ArtifactError("merge diagnostic says rejected but records no failed gate")
    adapter_merged_max_abs = float((trained_logits - merged_logits).abs().max())
    del merged_model, parent_logits, trained_logits, merged_logits, inputs
    _collect(cuda=cuda)

    return {
        "status": "ok",
        "runtime": runtime,
        "input_ids_sha256": input_ids_hash,
        "chat_template_sha256": template_hash,
        "tokenizer_json_sha256": tokenizer_json,
        "tokenizer_semantic_equivalence": tokenizer_equivalence,
        "processor_class": processor_class,
        "architectures": list(raw_architectures),
        "zero_noop": {
            "exact_logits_equal": True,
            "maximum_absolute_logit_difference": zero_max_abs,
        },
        "trained_nonnoop": {
            "allclose": False,
            "maximum_absolute_logit_difference": trained_max_abs,
            "relative_l2_difference": trained_relative_l2,
            "atol": nonnoop_atol,
            "rtol": nonnoop_rtol,
        },
        "merge_failure_diagnostic": {
            "schema": MERGE_DIAGNOSTIC_SCHEMA,
            "status": merge_status,
            "error": merge_error,
            "metrics": behavioral_metrics,
            "thresholds": dict(_MERGE_THRESHOLDS),
            "checks": merge_checks,
            "adapter_merged_max_abs": adapter_merged_max_abs,
            "provided_input": _provided_merge_diagnostic(merge_failure_diagnostic),
        },
    }


def _validate_behavior_report(report: dict[str, Any]) -> None:
    if (
        not isinstance(report, dict)
        or report.get("status") != "ok"
        or report.get("zero_noop", {}).get("exact_logits_equal") is not True
        or report.get("zero_noop", {}).get("maximum_absolute_logit_difference") != 0.0
        or report.get("trained_nonnoop", {}).get("allclose") is not False
        or float(report.get("trained_nonnoop", {}).get("maximum_absolute_logit_difference", 0.0))
        <= 0.0
        or report.get("merge_failure_diagnostic", {}).get("status") != "rejected"
    ):
        raise ArtifactError("exact-LoRA behavioral attestation is incomplete")
    checks = report["merge_failure_diagnostic"].get("checks")
    if not isinstance(checks, dict) or not checks or all(
        isinstance(value, dict) and value.get("passed") is True for value in checks.values()
    ):
        raise ArtifactError("exact-LoRA manifest has no reproduced merge-gate failure")


def exact_lora_composite_sha256(
    *,
    parent_tree_sha256: str,
    adapter_tree_sha256: str,
    adapter_config_sha256: str,
    tokenizer_json_sha256: str,
    chat_template_sha256: str,
    dtype: str,
) -> str:
    """Canonical identity shared by finalization, serving, and evaluation."""

    digests = (
        parent_tree_sha256,
        adapter_tree_sha256,
        adapter_config_sha256,
        tokenizer_json_sha256,
        chat_template_sha256,
    )
    if any(
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        for value in digests
    ):
        raise ArtifactError("exact-LoRA component identity is not a lowercase SHA-256")
    if dtype != "bfloat16":
        raise ArtifactError("exact-LoRA inference dtype must be bfloat16")
    value = {
        "schema": EXACT_LORA_COMPOSITE_SCHEMA,
        "execution": "peft_unmerged_exact_lora",
        "parent_tree_sha256": parent_tree_sha256,
        "adapter_tree_sha256": adapter_tree_sha256,
        "adapter_config_sha256": adapter_config_sha256,
        "tokenizer_json_sha256": tokenizer_json_sha256,
        "chat_template_sha256": chat_template_sha256,
        "dtype": dtype,
    }
    return sha256_bytes(canonical_json(value).encode())


def finalize_exact_lora(
    campaign: Campaign,
    *,
    campaign_root: str | Path,
    raw_base: str | Path,
    device: str = "cuda:0",
    merge_failure_diagnostic: str | Path | None = None,
) -> dict[str, Any]:
    """Publish a symmetric, exact-LoRA inference manifest without merged weights."""

    root = Path(campaign_root).resolve()
    raw = Path(raw_base).resolve()
    final_root = root / "final"
    if (final_root / "model").exists() or (final_root / "inference_manifest.json").exists():
        raise ArtifactError("refusing exact-LoRA finalization beside a merged final model")
    receipt_path, receipt, parent, trained_adapter = _verified_refinement_inputs(
        campaign, root
    )
    smoke_path = _verified_raw_base(campaign, root, raw)
    if raw == parent:
        raise ArtifactError("raw and trained parent paths unexpectedly alias")

    receipt_sha256 = sha256_file(receipt_path)
    post_path = root / "post_sft_receipt.json"
    post_sha256 = sha256_file(post_path)
    trained_before = component_identity(trained_adapter)
    zero_adapter = final_root / "base_zero_adapter"
    zero_attestation = create_zero_adapter(trained_adapter, raw, zero_adapter)
    if zero_attestation["source_tensor_inventory"].get("all_zero") is True:
        raise ArtifactError("trained step-20 adapter unexpectedly contains only zeros")

    behavior = verify_exact_lora_behavior(
        raw,
        zero_adapter,
        parent,
        trained_adapter,
        device=device,
        merge_failure_diagnostic=(
            Path(merge_failure_diagnostic).resolve()
            if merge_failure_diagnostic is not None
            else None
        ),
    )
    _validate_behavior_report(behavior)

    raw_identity = component_identity(raw)
    parent_identity = component_identity(parent)
    trained_identity = component_identity(trained_adapter)
    zero_identity = component_identity(zero_adapter)
    if trained_identity != trained_before:
        raise ArtifactError("trained step-20 adapter changed during exact-LoRA finalization")
    if receipt_sha256 != sha256_file(receipt_path) or post_sha256 != sha256_file(post_path):
        raise ArtifactError("a training receipt changed during exact-LoRA finalization")

    trained_config_path = trained_adapter / "adapter_config.json"
    zero_config_path = zero_adapter / "adapter_config.json"
    tokenizer_sha256 = behavior["tokenizer_json_sha256"]
    template_sha256 = behavior["chat_template_sha256"]
    tokenizer_equivalence = behavior.get("tokenizer_semantic_equivalence")
    if (
        not isinstance(tokenizer_equivalence, dict)
        or tokenizer_equivalence.get("status") != "ok"
        or tokenizer_equivalence.get("exact_loaded_semantics_equal") is not True
        or tokenizer_equivalence.get("shared_tokenizer_json_sha256") != tokenizer_sha256
        or tokenizer_equivalence.get("chat_template_sha256") != template_sha256
    ):
        raise ArtifactError("exact-LoRA finalization has no tokenizer semantic attestation")
    base_composite_sha256 = exact_lora_composite_sha256(
        parent_tree_sha256=raw_identity["tree_sha256"],
        adapter_tree_sha256=zero_identity["tree_sha256"],
        adapter_config_sha256=sha256_file(zero_config_path),
        tokenizer_json_sha256=tokenizer_sha256,
        chat_template_sha256=template_sha256,
        dtype=campaign.model["dtype"],
    )
    trained_composite_sha256 = exact_lora_composite_sha256(
        parent_tree_sha256=parent_identity["tree_sha256"],
        adapter_tree_sha256=trained_identity["tree_sha256"],
        adapter_config_sha256=sha256_file(trained_config_path),
        tokenizer_json_sha256=tokenizer_sha256,
        chat_template_sha256=template_sha256,
        dtype=campaign.model["dtype"],
    )
    pair_sha256 = sha256_bytes(
        canonical_json(
            {
                "schema": EXACT_LORA_MANIFEST_SCHEMA,
                "base": base_composite_sha256,
                "trained": trained_composite_sha256,
            }
        ).encode()
    )

    result = {
        "schema": EXACT_LORA_MANIFEST_SCHEMA,
        "status": "ok",
        "campaign_digest": campaign.digest,
        "objective_scaffold": campaign.campaign["objective"]["scaffold"],
        "model_id": campaign.model["model_id"],
        "base_revision": campaign.model["revision"],
        "serving_mode": "exact_peft_lora",
        "final_model_directory_published": False,
        "refinement_update": 20,
        "receipts": {
            "refinement": {
                "path": str(receipt_path),
                "schema": receipt["schema"],
                "status": receipt["status"],
                "sha256": receipt_sha256,
            },
            "post_sft": {
                "path": str(post_path),
                "sha256": post_sha256,
            },
            "selected_parent_merge": {
                "path": str(parent / "merge_provenance.json"),
                "sha256": sha256_file(parent / "merge_provenance.json"),
            },
            "raw_base_smoke": {
                "path": str(smoke_path),
                "sha256": sha256_file(smoke_path),
            },
        },
        "shared_tokenizer": {
            "path": str(parent),
            "tokenizer_json_sha256": tokenizer_sha256,
            "raw_tokenizer_json_sha256": tokenizer_equivalence[
                "raw_tokenizer_json_sha256"
            ],
            "semantic_sha256": tokenizer_equivalence["semantic_sha256"],
            "serialized_bytes_equal": tokenizer_equivalence["serialized_bytes_equal"],
            "chat_template_sha256": template_sha256,
            "input_ids_sha256": behavior["input_ids_sha256"],
            "processor_class": behavior["processor_class"],
        },
        "arms": {
            "base": {
                "parent": raw_identity,
                "adapter": zero_identity,
                "adapter_config": zero_attestation["zero_config"],
                "adapter_config_sha256": sha256_file(zero_config_path),
                "served_model_name": "qwen35-27b-base-exact-lora",
                "composite_sha256": base_composite_sha256,
            },
            "trained": {
                "parent": parent_identity,
                "adapter": trained_identity,
                "adapter_config": zero_attestation["source_config"],
                "adapter_config_sha256": sha256_file(trained_config_path),
                "served_model_name": "qwen35-harness-posttrained-exact-lora",
                "composite_sha256": trained_composite_sha256,
            },
        },
        "composite_pair_sha256": pair_sha256,
        "zero_adapter_attestation": zero_attestation,
        "behavioral_attestation": behavior,
        "inference": {
            "api": "openai_chat_completions",
            "dtype": campaign.model["dtype"],
            "native_context_tokens": campaign.model["native_context_tokens"],
            "language_model_only": campaign.model["language_model_only"],
            "reasoning_parser": campaign.model["reasoning_parser"],
            "tool_call_parser": campaign.model["tool_call_parser"],
            "enable_auto_tool_choice": True,
            "enable_prefix_caching": campaign.model["enable_prefix_caching"],
            "preserve_thinking": campaign.model["preserve_thinking"],
            "lora_rank": 64,
            "lora_alpha": 128,
        },
    }
    manifest_path = final_root / "exact_lora_manifest.json"
    publish_json(manifest_path, result)
    if read_json(manifest_path) != result:
        raise ArtifactError("published exact-LoRA manifest changed during publication")
    manifest_receipt = {
        "schema": "harness-posttrain.exact-lora-manifest-receipt.v1",
        "status": "ok",
        "campaign_digest": campaign.digest,
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "composite_pair_sha256": pair_sha256,
        "base_composite_sha256": base_composite_sha256,
        "trained_composite_sha256": trained_composite_sha256,
        "refinement_receipt_sha256": receipt_sha256,
    }
    publish_json(final_root / "exact_lora_manifest_receipt.json", manifest_receipt)
    if manifest_receipt["manifest_sha256"] != sha256_file(manifest_path):
        raise ArtifactError("exact-LoRA manifest changed after its receipt was published")
    if receipt_sha256 != sha256_file(receipt_path):
        raise ArtifactError("refinement receipt changed after exact-LoRA publication")
    if (final_root / "model").exists() or (final_root / "inference_manifest.json").exists():
        raise ArtifactError("exact-LoRA finalization published or observed a merged final model")
    return result
