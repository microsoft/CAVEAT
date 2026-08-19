"""Numerically verify a refinement LoRA against its selected-SFT parent.

The first campaign merge is relative to the immutable Hugging Face base.  The
refinement adapter is different: PRIME trains it on the already merged selected
SFT model.  Treating the raw base as its parent would silently test the wrong
model, so this verifier makes the local, attested selected model the numerical
reference for every comparison.
"""

from __future__ import annotations

import gc
import hashlib
import importlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .artifacts import ArtifactError, read_json, sha256_file

PARENT_MERGE_SCHEMA = "harness-posttrain.parent-aware-merge.v2"


@dataclass(frozen=True)
class ParentLogitMetrics:
    parent_adapter_max_abs: float
    parent_merged_max_abs: float
    adapter_merged_max_abs: float
    merged_reload_max_abs: float
    adapter_abs_max: float
    merged_abs_max: float


def checkpoint_manifest(path: str | Path) -> dict[str, dict[str, Any]]:
    """Hash the immutable inference files, excluding this manifest's provenance."""

    root = Path(path).resolve()
    result: dict[str, dict[str, Any]] = {}
    for candidate in sorted(item for item in root.rglob("*") if item.is_file()):
        if candidate.name == "merge_provenance.json":
            continue
        result[str(candidate.relative_to(root))] = {
            "size": candidate.stat().st_size,
            "sha256": sha256_file(candidate),
        }
    return result


def checkpoint_kind(path: str | Path) -> str:
    root = Path(path).resolve()
    if not root.is_dir():
        raise ArtifactError(f"checkpoint directory does not exist: {root}")
    adapter_weights = list(root.glob("adapter_model*.safetensors")) + list(
        root.glob("adapter_model*.bin")
    )
    if (root / "adapter_config.json").is_file() and adapter_weights:
        return "peft_adapter"
    full_weights = (
        list(root.glob("*.safetensors"))
        + list(root.glob("pytorch_model*.bin"))
        + list(root.glob("*.safetensors.index.json"))
    )
    if (root / "config.json").is_file() and full_weights:
        return "merged_hf"
    raise ArtifactError("checkpoint is neither a PEFT adapter nor a complete HF model")


def validate_parent_logit_metrics(
    metrics: ParentLogitMetrics,
    *,
    nonnoop_atol: float,
    reload_atol: float,
    reload_rtol: float,
) -> None:
    """Validate compact non-noop and lossless save/reload bounds."""

    if metrics.parent_adapter_max_abs <= nonnoop_atol:
        raise ArtifactError("refinement adapter is a numerical no-op relative to its parent")
    if metrics.parent_merged_max_abs <= nonnoop_atol:
        raise ArtifactError("final merged model is a numerical no-op relative to its parent")
    reload_limit = reload_atol + reload_rtol * metrics.merged_abs_max
    if metrics.merged_reload_max_abs > reload_limit:
        raise ArtifactError("saved and reloaded final model logits exceed tolerance")


def _collect(*, cuda: bool) -> None:
    gc.collect()
    if cuda:
        import torch

        torch.cuda.empty_cache()


def _logits(model: Any, inputs: dict[str, Any]) -> Any:
    import torch

    model.eval()
    with torch.no_grad():
        result = model(**inputs).logits[:, -1, :].detach().float().cpu()
    if not torch.isfinite(result).all():
        raise ArtifactError("checkpoint produced non-finite deterministic logits")
    return result


def _validate_behavioral_merge(
    parent_logits: Any,
    adapter_logits: Any,
    merged_logits: Any,
    *,
    max_relative_l2: float,
    min_cosine: float,
    max_symmetric_kl: float,
    min_top5_overlap: float,
    max_error_ratio: float,
) -> Any:
    """Use the audited first-stage verifier with the selected model as parent."""

    merge_verifier = importlib.import_module("harness_distill.model_merge")
    metrics = merge_verifier.behavioral_equivalence_metrics(
        parent_logits,
        adapter_logits,
        merged_logits,
    )
    try:
        merge_verifier.validate_behavioral_equivalence(
            metrics,
            max_relative_l2=max_relative_l2,
            min_cosine=min_cosine,
            max_symmetric_kl=max_symmetric_kl,
            min_top5_overlap=min_top5_overlap,
            max_error_ratio=max_error_ratio,
        )
    except merge_verifier.MergeVerificationError as exc:
        raise ArtifactError(f"refinement merge changed adapter behavior: {exc}") from exc
    return metrics


def _validate_adapter_parent(adapter: Path, parent: Path) -> dict[str, Any]:
    config = read_json(adapter / "adapter_config.json")
    if not isinstance(config, dict):
        raise ArtifactError("refinement adapter configuration is not an object")
    declared = str(config.get("base_model_name_or_path", ""))
    if declared and Path(declared).resolve() != parent:
        raise ArtifactError(
            "refinement adapter parent differs from the selected merged SFT checkpoint"
        )
    try:
        correct_lora_shape = config.get("r") == 64 and float(config.get("lora_alpha", -1)) == 128.0
    except (TypeError, ValueError):
        correct_lora_shape = False
    targets = config.get("target_modules")
    if (
        not correct_lora_shape
        or not isinstance(targets, list)
        or not targets
        or not all(isinstance(target, str) and target for target in targets)
    ):
        raise ArtifactError("refinement adapter rank, alpha, or target modules drifted")
    return config


def _validate_parent(parent: Path) -> dict[str, Any]:
    if checkpoint_kind(parent) != "merged_hf":
        raise ArtifactError("refinement parent is not a complete merged Hugging Face model")
    provenance_path = parent / "merge_provenance.json"
    provenance = read_json(provenance_path)
    if (
        not isinstance(provenance, dict)
        or provenance.get("status") != "ok"
        or Path(str(provenance.get("output_dir", ""))).resolve() != parent
        or not isinstance(provenance.get("snapshot"), dict)
        or not provenance.get("logit_metrics")
    ):
        raise ArtifactError("selected SFT parent has incompatible merge provenance")
    if provenance.get("merged_manifest") != checkpoint_manifest(parent):
        raise ArtifactError("selected SFT parent bytes differ from its merge provenance")
    return provenance


def _completed_merge(
    parent: Path, adapter: Path, output: Path, parent_provenance_sha256: str
) -> dict[str, Any] | None:
    path = output / "merge_provenance.json"
    if not path.is_file():
        return None
    provenance = read_json(path)
    expected = {
        "schema": PARENT_MERGE_SCHEMA,
        "status": "ok",
        "parent_model": str(parent),
        "adapter_path": str(adapter),
        "output_dir": str(output),
        "parent_provenance_sha256": parent_provenance_sha256,
    }
    if not isinstance(provenance, dict):
        raise ArtifactError("final merge provenance is not an object")
    for key, value in expected.items():
        if provenance.get(key) != value:
            raise ArtifactError(f"published final merge provenance changed: {key}")
    if provenance.get("parent_manifest") != checkpoint_manifest(parent):
        raise ArtifactError("published final merge parent bytes changed")
    if provenance.get("adapter_manifest") != checkpoint_manifest(adapter):
        raise ArtifactError("published refinement adapter bytes changed")
    if provenance.get("merged_manifest") != checkpoint_manifest(output):
        raise ArtifactError("published final model bytes changed")
    return provenance


def _verify_into(
    parent: Path,
    adapter: Path,
    destination: Path,
    *,
    published_destination: Path,
    device: str,
    nonnoop_atol: float,
    nonnoop_rtol: float,
    max_relative_l2: float,
    min_cosine: float,
    max_symmetric_kl: float,
    min_top5_overlap: float,
    max_error_ratio: float,
    reload_atol: float,
    reload_rtol: float,
) -> dict[str, Any]:
    model_smoke = importlib.import_module("harness_distill.model_smoke")
    runtime = model_smoke.assert_runtime_dependencies(require_cuda=device.startswith("cuda"))
    parent_provenance = _validate_parent(parent)
    adapter_config = _validate_adapter_parent(adapter, parent)
    if checkpoint_kind(adapter) != "peft_adapter":
        raise ArtifactError("refinement checkpoint must be a PEFT adapter")
    destination.mkdir(parents=True, exist_ok=False)

    import torch
    from peft import PeftModel

    torch.manual_seed(20260812)
    processor, tokenizer = model_smoke._load_processor_tokenizer(parent)  # noqa: SLF001
    processor_class = f"{type(processor).__module__}.{type(processor).__qualname__}"
    template_hash = model_smoke._chat_template_sha256(tokenizer)  # noqa: SLF001
    inputs = model_smoke._prompt_tensors(tokenizer, device=device)  # noqa: SLF001
    input_ids_hash = hashlib.sha256(
        inputs["input_ids"].detach().cpu().numpy().tobytes()
    ).hexdigest()
    cuda = device.startswith("cuda")

    parent_model = model_smoke._load_model(parent, device=device)  # noqa: SLF001
    parent_logits = _logits(parent_model, inputs)
    parent_architectures = tuple(getattr(parent_model.config, "architectures", ()) or ())
    del parent_model
    _collect(cuda=cuda)

    adapter_base = model_smoke._load_model(parent, device=device)  # noqa: SLF001
    adapter_model = PeftModel.from_pretrained(
        adapter_base, adapter, is_trainable=False, local_files_only=True
    )
    del adapter_base
    adapter_logits = _logits(adapter_model, inputs)
    if torch.allclose(parent_logits, adapter_logits, atol=nonnoop_atol, rtol=nonnoop_rtol):
        raise ArtifactError("refinement adapter is a numerical no-op relative to its parent")

    merged_model = adapter_model.merge_and_unload(safe_merge=True)
    del adapter_model
    merged_logits = _logits(merged_model, inputs)
    if torch.allclose(parent_logits, merged_logits, atol=nonnoop_atol, rtol=nonnoop_rtol):
        raise ArtifactError("final merged model is a numerical no-op relative to its parent")
    behavioral_metrics = _validate_behavioral_merge(
        parent_logits,
        adapter_logits,
        merged_logits,
        max_relative_l2=max_relative_l2,
        min_cosine=min_cosine,
        max_symmetric_kl=max_symmetric_kl,
        min_top5_overlap=min_top5_overlap,
        max_error_ratio=max_error_ratio,
    )

    merged_model.save_pretrained(destination, safe_serialization=True, max_shard_size="10GB")
    processor.save_pretrained(destination)
    tokenizer.save_pretrained(destination)
    del merged_model
    _collect(cuda=cuda)

    if checkpoint_kind(destination) != "merged_hf":
        raise ArtifactError("saved final model is not a complete Hugging Face checkpoint")
    reload_processor, reload_tokenizer = model_smoke._load_processor_tokenizer(  # noqa: SLF001
        destination
    )
    reload_processor_class = (
        f"{type(reload_processor).__module__}.{type(reload_processor).__qualname__}"
    )
    if reload_processor_class != processor_class:
        raise ArtifactError("final model changed the processor class")
    if model_smoke._chat_template_sha256(reload_tokenizer) != template_hash:  # noqa: SLF001
        raise ArtifactError("final model changed the chat template")
    del reload_processor
    reload_inputs = model_smoke._prompt_tensors(  # noqa: SLF001
        reload_tokenizer, device=device
    )
    if not torch.equal(
        inputs["input_ids"].detach().cpu(), reload_inputs["input_ids"].detach().cpu()
    ):
        raise ArtifactError("final model changed deterministic input token IDs")
    reloaded = model_smoke._load_model(destination, device=device)  # noqa: SLF001
    reload_architectures = tuple(getattr(reloaded.config, "architectures", ()) or ())
    if reload_architectures != parent_architectures:
        raise ArtifactError("final model changed the parent model architecture")
    reload_logits = _logits(reloaded, reload_inputs)
    if not torch.allclose(merged_logits, reload_logits, atol=reload_atol, rtol=reload_rtol):
        raise ArtifactError("saved and reloaded final model logits are not equivalent")

    metrics = ParentLogitMetrics(
        parent_adapter_max_abs=float((parent_logits - adapter_logits).abs().max()),
        parent_merged_max_abs=float((parent_logits - merged_logits).abs().max()),
        adapter_merged_max_abs=float((adapter_logits - merged_logits).abs().max()),
        merged_reload_max_abs=float((merged_logits - reload_logits).abs().max()),
        adapter_abs_max=float(adapter_logits.abs().max()),
        merged_abs_max=float(merged_logits.abs().max()),
    )
    validate_parent_logit_metrics(
        metrics,
        nonnoop_atol=nonnoop_atol,
        reload_atol=reload_atol,
        reload_rtol=reload_rtol,
    )
    del reloaded
    _collect(cuda=cuda)

    provenance = {
        "schema": PARENT_MERGE_SCHEMA,
        "status": "ok",
        "parent_model": str(parent),
        "adapter_path": str(adapter),
        "output_dir": str(published_destination),
        "parent_provenance_sha256": sha256_file(parent / "merge_provenance.json"),
        "base_snapshot": parent_provenance["snapshot"],
        "runtime": runtime,
        "input_ids_sha256": input_ids_hash,
        "chat_template_sha256": template_hash,
        "processor_class": processor_class,
        "architectures": list(parent_architectures),
        "adapter_declared_parent": adapter_config["base_model_name_or_path"],
        "tolerances": {
            "nonnoop_atol": nonnoop_atol,
            "nonnoop_rtol": nonnoop_rtol,
            "reload_atol": reload_atol,
            "reload_rtol": reload_rtol,
        },
        "logit_metrics": asdict(metrics),
        "behavioral_equivalence": {
            "metrics": asdict(behavioral_metrics),
            "thresholds": {
                "max_relative_l2": max_relative_l2,
                "min_cosine": min_cosine,
                "max_symmetric_kl": max_symmetric_kl,
                "min_top5_overlap": min_top5_overlap,
                "max_error_ratio": max_error_ratio,
            },
        },
        "parent_manifest": checkpoint_manifest(parent),
        "adapter_manifest": checkpoint_manifest(adapter),
        "merged_manifest": checkpoint_manifest(destination),
        "launcher_provenance": {
            key: os.environ.get(key)
            for key in (
                "LAST_CONTAINER_IMAGE_DIGEST",
                "LAST_SOURCE_GIT_SHA",
                "LAST_CLUSTER_PRIORITY_REQUEST",
                "LAST_CLUSTER_PRIORITY_CLASS",
                "LAST_ALLOCATED_GPUS",
            )
        },
    }
    (destination / "merge_provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return provenance


def verify_parent_aware_merge(
    parent_model: str | Path,
    adapter_path: str | Path,
    output_dir: str | Path,
    *,
    device: str = "cuda:0",
    nonnoop_atol: float = 1e-7,
    nonnoop_rtol: float = 1e-6,
    max_relative_l2: float = 0.10,
    min_cosine: float = 0.99,
    max_symmetric_kl: float = 0.01,
    min_top5_overlap: float = 0.8,
    max_error_ratio: float = 0.5,
    reload_atol: float = 5e-2,
    reload_rtol: float = 5e-3,
) -> dict[str, Any]:
    """Atomically verify and publish the refinement merge, or reuse an exact one."""

    parent = Path(parent_model).resolve()
    adapter = Path(adapter_path).resolve()
    destination = Path(output_dir).resolve()
    parent_provenance_sha256 = sha256_file(parent / "merge_provenance.json")
    if destination.exists():
        if not destination.is_dir():
            raise ArtifactError(f"final model output is not a directory: {destination}")
        if not any(destination.iterdir()):
            destination.rmdir()
        else:
            completed = _completed_merge(parent, adapter, destination, parent_provenance_sha256)
            if completed is None:
                raise ArtifactError(f"unattested final model output exists: {destination}")
            return completed

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    staging = staging_root / "model"
    try:
        provenance = _verify_into(
            parent,
            adapter,
            staging,
            published_destination=destination,
            device=device,
            nonnoop_atol=nonnoop_atol,
            nonnoop_rtol=nonnoop_rtol,
            max_relative_l2=max_relative_l2,
            min_cosine=min_cosine,
            max_symmetric_kl=max_symmetric_kl,
            min_top5_overlap=min_top5_overlap,
            max_error_ratio=max_error_ratio,
            reload_atol=reload_atol,
            reload_rtol=reload_rtol,
        )
        persisted = read_json(staging / "merge_provenance.json")
        if persisted != provenance or provenance.get("status") != "ok":
            raise ArtifactError("staged final merge provenance is incomplete")
        staging.replace(destination)
        staging_root.rmdir()
        return provenance
    except BaseException:
        if staging_root.exists():
            shutil.rmtree(staging_root)
        raise
