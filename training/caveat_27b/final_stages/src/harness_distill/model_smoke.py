"""Fail-closed Qwen model, kernel, LoRA-target, and update smoke tests.

Heavy dependencies are imported only inside the cluster entrypoint.  Static
target selection and configuration validation therefore remain unit-testable on
a CPU-only development machine without downloading model weights.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import traceback
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

# Defense in depth: leave a conflicting value untouched so the runtime guard
# rejects it, but choose the known-safe pure-PyTorch path when it is unspecified.
os.environ.setdefault("FLA_TILELANG", "0")

EXPECTED_TRANSFORMERS = "5.12.1"
FULL_REVISION = re.compile(r"^[0-9a-f]{40}$")
FORBIDDEN_DISTRIBUTIONS = ("flash-linear-attention", "causal-conv1d")
FORBIDDEN_IMPORTS = ("fla", "causal_conv1d")
EXCLUDED_NAME_FRAGMENTS = ("vision", "visual", "mtp", "multi_token_prediction")
DENSE_ATTENTION_SUFFIXES = ("q_proj", "k_proj", "v_proj", "o_proj")
MLP_SUFFIXES = ("gate_proj", "up_proj", "down_proj")
GDN_SUFFIXES = (
    "in_proj_qkvz",
    "in_proj_ba",
    "out_proj",
    "in_proj_qkv",
    "in_proj_z",
    "in_proj_b",
    "in_proj_a",
)
ALL_TARGET_SUFFIXES = frozenset(DENSE_ATTENTION_SUFFIXES + MLP_SUFFIXES + GDN_SUFFIXES)


class ModelSmokeError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelSnapshot:
    role: str
    model_id: str
    revision: str
    dtype: str
    context_tokens: int
    preserve_thinking: bool
    preserve_multimodal_wrapper: bool
    preserve_processor_and_chat_template: bool


@dataclass(frozen=True)
class TargetSelection:
    targets: tuple[str, ...]
    suffix_counts: Mapping[str, int]
    dense_attention_count: int
    mlp_count: int
    gdn_present: bool
    gdn_count: int
    excluded_linear_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "targets": list(self.targets),
            "suffix_counts": dict(self.suffix_counts),
            "dense_attention_count": self.dense_attention_count,
            "mlp_count": self.mlp_count,
            "gdn_present": self.gdn_present,
            "gdn_count": self.gdn_count,
            "excluded_linear_count": self.excluded_linear_count,
        }


def load_model_snapshot(config_path: str | Path, *, role: str = "primary") -> ModelSnapshot:
    path = Path(config_path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ModelSmokeError(f"unsupported model configuration: {path}")
    if role not in {"primary", "fallback"} or not isinstance(payload.get(role), dict):
        raise ModelSmokeError(f"unknown configured model role {role!r}")
    model = payload[role]
    shared = payload.get("shared")
    if not isinstance(shared, dict):
        raise ModelSmokeError("model configuration has no shared runtime block")
    model_id = str(model.get("model_id", ""))
    revision = str(model.get("revision", ""))
    if not model_id.startswith("Qwen/"):
        raise ModelSmokeError(f"configured model must be an official Qwen repository: {model_id!r}")
    if FULL_REVISION.fullmatch(revision) is None:
        raise ModelSmokeError("model revision must be a full lowercase 40-character commit hash")
    if shared.get("freeze_vision_tower") is not True:
        raise ModelSmokeError("vision tower must be frozen")
    if shared.get("language_model_only") is not True:
        raise ModelSmokeError("smoke input must remain language-model-only")
    if shared.get("preserve_multimodal_wrapper") is not True:
        raise ModelSmokeError("multimodal wrapper preservation may not be disabled")
    if shared.get("preserve_processor_and_chat_template") is not True:
        raise ModelSmokeError("processor/chat-template preservation may not be disabled")
    if shared.get("preserve_thinking") is not True:
        raise ModelSmokeError("native reasoning history preservation may not be disabled")
    if shared.get("dtype") != "bfloat16":
        raise ModelSmokeError("Qwen smoke must use bfloat16")
    if int(shared.get("context_tokens", 0)) < 131_072:
        raise ModelSmokeError("configured context must remain at least 131072 tokens")
    return ModelSnapshot(
        role=role,
        model_id=model_id,
        revision=revision,
        dtype=str(shared.get("dtype")),
        context_tokens=int(shared.get("context_tokens", 0)),
        preserve_thinking=bool(shared.get("preserve_thinking")),
        preserve_multimodal_wrapper=True,
        preserve_processor_and_chat_template=True,
    )


def runtime_violations(
    installed_versions: Mapping[str, str | None],
    importable_modules: Iterable[str],
    environ: Mapping[str, str],
    *,
    expected_transformers: str = EXPECTED_TRANSFORMERS,
) -> tuple[str, ...]:
    """Pure runtime-policy check used by both tests and the heavy entrypoint."""

    errors: list[str] = []
    if installed_versions.get("transformers") != expected_transformers:
        errors.append(
            "transformers must be exactly "
            f"{expected_transformers}, found {installed_versions.get('transformers') or 'absent'}"
        )
    for distribution in FORBIDDEN_DISTRIBUTIONS:
        if installed_versions.get(distribution) is not None:
            errors.append(f"forbidden distribution installed: {distribution}")
    importable = set(importable_modules)
    for module in FORBIDDEN_IMPORTS:
        if module in importable:
            errors.append(f"forbidden module importable: {module}")
    if environ.get("FLA_TILELANG") != "0":
        errors.append("FLA_TILELANG must be exactly 0")
    return tuple(errors)


def assert_runtime_dependencies(*, require_cuda: bool = True) -> dict[str, Any]:
    def version(name: str) -> str | None:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return None

    versions = {name: version(name) for name in ("transformers", *FORBIDDEN_DISTRIBUTIONS)}
    importable = [name for name in FORBIDDEN_IMPORTS if importlib.util.find_spec(name) is not None]
    errors = list(runtime_violations(versions, importable, os.environ))
    try:
        import torch

        cuda_count = torch.cuda.device_count()
        if require_cuda and cuda_count < 1:
            errors.append("at least one CUDA device is required for the full model smoke")
    except ImportError:
        cuda_count = 0
        errors.append("PyTorch is not installed")
    for package in ("huggingface-hub", "peft"):
        if version(package) is None:
            errors.append(f"required distribution is absent: {package}")
    if errors:
        raise ModelSmokeError("; ".join(errors))
    return {"versions": versions, "cuda_devices": cuda_count, "fla_tilelang": "0"}


def _default_linear_predicate(module: Any) -> bool:
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - heavy dependency path
        raise ModelSmokeError("PyTorch is required to inspect a live model") from exc
    return isinstance(module, torch.nn.Linear)


def select_lora_targets(
    named_modules: Iterable[tuple[str, Any]],
    *,
    is_linear: Callable[[Any], bool] | None = None,
) -> TargetSelection:
    """Select full module names and prove coverage of Qwen's hybrid decoder.

    Full names avoid PEFT suffix collisions with visual and MTP modules.  Any
    GDN projection seen in the language model is required to appear in the
    selected set; absence of GDN projections is valid for a non-hybrid fallback.
    """

    predicate = is_linear or _default_linear_predicate
    targets: list[str] = []
    suffix_counts: Counter[str] = Counter()
    excluded = 0
    gdn_present = False
    for name, module in named_modules:
        lowered = name.lower()
        leaf = lowered.rsplit(".", 1)[-1]
        is_excluded = any(fragment in lowered for fragment in EXCLUDED_NAME_FRAGMENTS)
        if not is_excluded and leaf in GDN_SUFFIXES:
            gdn_present = True
        if not predicate(module):
            continue
        if is_excluded:
            excluded += 1
            continue
        if leaf not in ALL_TARGET_SUFFIXES:
            continue
        targets.append(name)
        suffix_counts[leaf] += 1

    if not targets:
        raise ModelSmokeError("no eligible LoRA target modules matched")
    dense_attention_count = sum(suffix_counts[name] for name in DENSE_ATTENTION_SUFFIXES)
    mlp_count = sum(suffix_counts[name] for name in MLP_SUFFIXES)
    gdn_count = sum(suffix_counts[name] for name in GDN_SUFFIXES)
    if dense_attention_count == 0:
        raise ModelSmokeError("LoRA target set has no dense-attention projections")
    if mlp_count == 0:
        raise ModelSmokeError("LoRA target set has no MLP projections")
    if gdn_present and gdn_count == 0:
        raise ModelSmokeError("GatedDeltaNet projections exist but none were selected")
    if any(
        any(fragment in target.lower() for fragment in EXCLUDED_NAME_FRAGMENTS)
        for target in targets
    ):
        raise ModelSmokeError("vision/visual/MTP module escaped the exclusion guard")
    if len(set(targets)) != len(targets):
        raise ModelSmokeError("named_modules returned duplicate LoRA target names")
    return TargetSelection(
        targets=tuple(targets),
        suffix_counts=dict(sorted(suffix_counts.items())),
        dense_attention_count=dense_attention_count,
        mlp_count=mlp_count,
        gdn_present=gdn_present,
        gdn_count=gdn_count,
        excluded_linear_count=excluded,
    )


def inspect_model_modules(model: Any) -> TargetSelection:
    return select_lora_targets(model.named_modules())


def snapshot_download_exact(snapshot: ModelSnapshot, *, token: str | None = None) -> Path:
    from huggingface_hub import snapshot_download

    resolved = Path(
        snapshot_download(
            repo_id=snapshot.model_id,
            revision=snapshot.revision,
            token=token,
        )
    ).resolve()
    if resolved.name != snapshot.revision:
        raise ModelSmokeError(
            f"snapshot resolved to {resolved.name!r}, expected exact revision {snapshot.revision!r}"
        )
    return resolved


def _load_processor_tokenizer(snapshot_path: Path) -> tuple[Any, Any]:
    from transformers import AutoProcessor, AutoTokenizer

    processor = AutoProcessor.from_pretrained(
        snapshot_path, local_files_only=True, trust_remote_code=False
    )
    tokenizer = AutoTokenizer.from_pretrained(
        snapshot_path, local_files_only=True, trust_remote_code=False
    )
    if not getattr(tokenizer, "chat_template", None):
        raise ModelSmokeError("tokenizer has no chat template")
    return processor, tokenizer


def _load_model(snapshot_path: Path, *, device: str) -> Any:
    import torch
    from transformers import AutoModelForImageTextToText

    model = AutoModelForImageTextToText.from_pretrained(
        snapshot_path,
        local_files_only=True,
        trust_remote_code=False,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        low_cpu_mem_usage=True,
    )
    return model.to(device)


def _activate_and_verify_prime_qwen35_patches(model_config: Any) -> dict[str, Any]:
    """Prove the pinned trainer recognizes a SHA-path Qwen config at runtime."""

    try:
        from prime_rl.trainer.model import (
            _activate_qwen3_5_packed_varlen_patches,
            _is_qwen3_5_config,
        )
        from transformers.models.qwen3_5.modeling_qwen3_5 import Qwen3_5GatedDeltaNet
    except (ImportError, AttributeError) as exc:
        raise ModelSmokeError(
            "pinned PRIME lacks the reviewed Qwen loaded-config activation patch"
        ) from exc
    if not _is_qwen3_5_config(model_config):
        outer = getattr(model_config, "model_type", None)
        nested = getattr(getattr(model_config, "text_config", None), "model_type", None)
        raise ModelSmokeError(
            f"PRIME did not recognize loaded Qwen config (outer={outer!r}, nested={nested!r})"
        )
    if _activate_qwen3_5_packed_varlen_patches(model_config) is not True:
        raise ModelSmokeError("PRIME declined to activate Qwen packed-varlen safeguards")
    if not getattr(Qwen3_5GatedDeltaNet.forward, "_prl_varlen_patched", False):
        raise ModelSmokeError("Qwen GatedDeltaNet varlen forward was not patched")
    return {
        "status": "active",
        "outer_model_type": getattr(model_config, "model_type", None),
        "text_model_type": getattr(getattr(model_config, "text_config", None), "model_type", None),
        "detection_source": "loaded_config",
        "gated_delta_net_varlen": True,
    }


def _set_training_cache(model: Any, enabled: bool) -> None:
    model.config.use_cache = enabled
    text_config = getattr(model.config, "text_config", None)
    if text_config is not None:
        text_config.use_cache = enabled


def _prompt_tensors(tokenizer: Any, *, device: str) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": "Use the available tools and follow the user's instruction."},
        {"role": "user", "content": "Compare the visible options carefully, then choose one."},
    ]
    try:
        rendered = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
            preserve_thinking=True,
        )
    except TypeError as exc:
        raise ModelSmokeError("tokenizer does not support native preserve_thinking") from exc
    encoded = tokenizer(rendered, return_tensors="pt", add_special_tokens=False)
    return {key: value.to(device) for key, value in encoded.items()}


def _chat_template_sha256(tokenizer: Any) -> str:
    template = str(getattr(tokenizer, "chat_template", ""))
    if not template:
        raise ModelSmokeError("chat template was lost")
    return hashlib.sha256(template.encode("utf-8")).hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_with_accelerator_cleanup(
    operation: Callable[[], dict[str, Any]],
    *,
    torch_module: Any,
    device: str,
) -> dict[str, Any]:
    """Run an isolated model lifecycle and release its allocator state.

    The model work executes in a separate Python frame.  On success that frame
    is gone before cache collection.  On failure, clearing the inactive
    traceback frames drops model references before the exception is re-raised;
    otherwise a caller that records the exception can retain an entire model.
    """

    try:
        return operation()
    except BaseException as exc:
        traceback.clear_frames(exc.__traceback__)
        raise
    finally:
        gc.collect()
        if device.startswith("cuda"):
            torch_module.cuda.empty_cache()


def _run_full_model_smoke_loaded(
    model_config: str | Path,
    output_dir: str | Path,
    *,
    role: str = "primary",
    device: str = "cuda:0",
    rank: int = 8,
    alpha: int = 16,
    learning_rate: float = 1e-4,
    hf_token: str | None = None,
) -> dict[str, Any]:
    runtime = assert_runtime_dependencies(require_cuda=device.startswith("cuda"))
    snapshot = load_model_snapshot(model_config, role=role)
    token = hf_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")
    snapshot_path = snapshot_download_exact(snapshot, token=token)
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    adapter_dir = destination / "adapter"
    assets_dir = destination / "assets"

    import torch
    from peft import LoraConfig, PeftModel, get_peft_model

    torch.manual_seed(20260807)
    processor, tokenizer = _load_processor_tokenizer(snapshot_path)
    processor_class = f"{type(processor).__module__}.{type(processor).__qualname__}"
    template_hash = _chat_template_sha256(tokenizer)
    model = _load_model(snapshot_path, device=device)
    prime_qwen_patch = _activate_and_verify_prime_qwen35_patches(model.config)
    _set_training_cache(model, False)
    targets = inspect_model_modules(model)
    model = get_peft_model(
        model,
        LoraConfig(
            r=rank,
            lora_alpha=alpha,
            lora_dropout=0.0,
            bias="none",
            task_type=None,
            target_modules=list(targets.targets),
        ),
    )
    if hasattr(model, "enable_input_require_grads"):
        model.enable_input_require_grads()
    if hasattr(model, "gradient_checkpointing_enable"):
        model.gradient_checkpointing_enable()

    trainable = [
        (name, parameter) for name, parameter in model.named_parameters() if parameter.requires_grad
    ]
    if not trainable:
        raise ModelSmokeError("PEFT produced no trainable parameters")
    snapshots = {
        name: parameter.detach().float().cpu().clone()
        for name, parameter in trainable[: min(24, len(trainable))]
    }
    inputs = _prompt_tensors(tokenizer, device=device)
    labels = inputs["input_ids"].clone()
    optimizer = torch.optim.AdamW((parameter for _, parameter in trainable), lr=learning_rate)
    optimizer.zero_grad(set_to_none=True)
    output = model(**inputs, labels=labels)
    loss = output.loss
    if loss is None or not torch.isfinite(loss):
        raise ModelSmokeError("forward loss is absent or non-finite")
    initial_loss = float(loss.detach().cpu())
    loss.backward()
    nonzero_gradients = sum(
        parameter.grad is not None
        and torch.isfinite(parameter.grad).all().item()
        and torch.count_nonzero(parameter.grad).item() > 0
        for _, parameter in trainable
    )
    if nonzero_gradients == 0:
        raise ModelSmokeError("backward produced no finite nonzero adapter gradient")
    optimizer.step()
    trainable_by_name = dict(trainable)
    changed = [
        name
        for name, before in snapshots.items()
        if not torch.equal(before, trainable_by_name[name].detach().float().cpu())
    ]
    if not changed:
        raise ModelSmokeError("optimizer step did not change a sampled adapter parameter")

    model.save_pretrained(adapter_dir, safe_serialization=True)
    processor.save_pretrained(assets_dir)
    tokenizer.save_pretrained(assets_dir)
    del output, loss, optimizer, model, inputs, labels, trainable, trainable_by_name
    gc.collect()
    if device.startswith("cuda"):
        torch.cuda.empty_cache()

    reload_processor, reload_tokenizer = _load_processor_tokenizer(assets_dir)
    reload_processor_class = (
        f"{type(reload_processor).__module__}.{type(reload_processor).__qualname__}"
    )
    if reload_processor_class != processor_class:
        raise ModelSmokeError("processor class changed across save/reload")
    if _chat_template_sha256(reload_tokenizer) != template_hash:
        raise ModelSmokeError("chat template changed across save/reload")
    del reload_processor
    reloaded_base = _load_model(snapshot_path, device=device)
    reloaded = PeftModel.from_pretrained(reloaded_base, adapter_dir, is_trainable=False)
    del reloaded_base
    reloaded.eval()
    reload_inputs = _prompt_tensors(reload_tokenizer, device=device)
    with torch.no_grad():
        reload_output = reloaded(
            **reload_inputs,
            labels=reload_inputs["input_ids"],
        )
        reload_logits = reload_output.logits
    if not torch.isfinite(reload_logits).all():
        raise ModelSmokeError("reloaded adapter produced non-finite logits")
    if reload_output.loss is None or not torch.isfinite(reload_output.loss):
        raise ModelSmokeError("reloaded adapter produced a non-finite loss")

    report = {
        "status": "ok",
        "snapshot": asdict(snapshot),
        "resolved_snapshot": str(snapshot_path),
        "runtime": runtime,
        "target_selection": targets.to_dict(),
        "rank": rank,
        "alpha": alpha,
        "learning_rate": learning_rate,
        "initial_update_loss": initial_loss,
        "reload_loss": float(reload_output.loss.detach().cpu()),
        "nonzero_gradient_parameters": nonzero_gradients,
        "sampled_changed_parameters": changed,
        "chat_template_sha256": template_hash,
        "processor_class": processor_class,
        "prime_qwen_packed_varlen": prime_qwen_patch,
        "adapter_dir": str(adapter_dir),
        "assets_dir": str(assets_dir),
    }
    _write_json(destination / "smoke_report.json", report)
    return report


def run_full_model_smoke(
    model_config: str | Path,
    output_dir: str | Path,
    *,
    role: str = "primary",
    device: str = "cuda:0",
    rank: int = 8,
    alpha: int = 16,
    learning_rate: float = 1e-4,
    hf_token: str | None = None,
) -> dict[str, Any]:
    """Load exact Qwen weights and prove one real LoRA update survives reload."""

    import torch

    def operation() -> dict[str, Any]:
        return _run_full_model_smoke_loaded(
            model_config,
            output_dir,
            role=role,
            device=device,
            rank=rank,
            alpha=alpha,
            learning_rate=learning_rate,
            hf_token=hf_token,
        )

    return _run_with_accelerator_cleanup(
        operation,
        torch_module=torch,
        device=device,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--role", choices=("primary", "fallback"), default="primary")
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--rank", type=int, default=8)
    parser.add_argument("--alpha", type=int, default=16)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_full_model_smoke(
            args.model_config,
            args.output_dir,
            role=args.role,
            device=args.device,
            rank=args.rank,
            alpha=args.alpha,
            learning_rate=args.learning_rate,
        )
    except Exception as exc:  # noqa: BLE001 - CLI must serialize the failure
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
