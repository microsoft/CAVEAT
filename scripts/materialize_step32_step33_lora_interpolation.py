#!/usr/bin/env python3
"""Create one fail-closed elementwise Step32/Step33 LoRA interpolation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from fractions import Fraction
from pathlib import Path
from typing import Any

import torch
from safetensors import safe_open
from safetensors.torch import save_file

STEP32_PATH = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step32-rebind-training-w1-20260815/"
    "step32_training_run_r2/training/prime_output/weights/step_32/lora_adapters"
)
STEP33_PATH = Path(
    "/data/runs/t-yuxuanli/"
    "t-yuxuanli-hpt-c2-step33-upstream-training-w1-20260815/"
    "step33_training_run_r3/training/training/prime_output/weights/step_33/"
    "lora_adapters"
)
STEP32_TREE = "a2c2fe90c23bece6ba793d07d461304676d4bff8826e5d1464afd6bf8192dece"
STEP33_TREE = "d8045f96205b360229c1a320933946994c96ee8878852b08ea01f5e065b6fc65"


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def tree_identity(root: Path) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"tree contains symlink: {path}")
        if path.is_file():
            entries[path.relative_to(root).as_posix()] = {
                "size": path.stat().st_size,
                "sha256": file_sha(path),
            }
        elif not path.is_dir():
            raise RuntimeError(f"tree contains special member: {path}")
    if not entries:
        raise RuntimeError(f"tree is empty: {root}")
    return {
        "files": len(entries),
        "bytes": sum(row["size"] for row in entries.values()),
        "tree_sha256": digest(entries),
    }


def write_new(path: Path, payload: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def source_contract(path: Path) -> tuple[bytes, list[str], dict[str, str] | None]:
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"unsafe adapter path: {path}")
    config = path / "adapter_config.json"
    weights = path / "adapter_model.safetensors"
    if any(item.is_symlink() or not item.is_file() for item in (config, weights)):
        raise RuntimeError(f"adapter files are absent or unsafe: {path}")
    with safe_open(weights, framework="pt", device="cpu") as handle:
        keys = sorted(handle.keys())
        metadata = handle.metadata()
    return config.read_bytes(), keys, metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step32", type=Path, required=True)
    parser.add_argument("--step33", type=Path, required=True)
    parser.add_argument("--alpha", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)

    alpha_fraction = Fraction(args.alpha)
    if not 0 < alpha_fraction < 1:
        parser.error("alpha must be strictly between zero and one")
    alpha = float(alpha_fraction)
    step32 = args.step32.resolve()
    step33 = args.step33.resolve()
    output_root = args.output_root.resolve()
    if step32 != STEP32_PATH or step33 != STEP33_PATH:
        parser.error("parent adapter paths are not the sealed Step32/Step33 pair")
    if output_root.exists() or output_root.is_symlink():
        parser.error(f"output already exists: {output_root}")

    step32_identity = tree_identity(step32)
    step33_identity = tree_identity(step33)
    if step32_identity["tree_sha256"] != STEP32_TREE:
        raise RuntimeError("Step32 adapter tree identity changed")
    if step33_identity["tree_sha256"] != STEP33_TREE:
        raise RuntimeError("Step33 adapter tree identity changed")

    config32, keys32, metadata32 = source_contract(step32)
    config33, keys33, metadata33 = source_contract(step33)
    if config32 != config33:
        raise RuntimeError("Step32 and Step33 adapter configs are not byte-identical")
    if keys32 != keys33 or not keys32:
        raise RuntimeError("Step32 and Step33 tensor key sets differ")
    if metadata32 != metadata33:
        raise RuntimeError("Step32 and Step33 safetensors metadata differ")

    output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_root.with_name(f".{output_root.name}.{os.getpid()}.tmp")
    temporary.mkdir(mode=0o700)
    temporary_candidate = temporary / "lora_adapters"
    temporary_candidate.mkdir(mode=0o700)
    output_tensors: dict[str, torch.Tensor] = {}
    tensor_contract: list[dict[str, Any]] = []
    try:
        with (
            safe_open(
                step32 / "adapter_model.safetensors", framework="pt", device="cpu"
            ) as left,
            safe_open(
                step33 / "adapter_model.safetensors", framework="pt", device="cpu"
            ) as right,
        ):
            for key in keys32:
                tensor32 = left.get_tensor(key)
                tensor33 = right.get_tensor(key)
                if tensor32.shape != tensor33.shape or tensor32.dtype != tensor33.dtype:
                    raise RuntimeError(f"tensor shape/dtype differs: {key}")
                if not tensor32.dtype.is_floating_point:
                    raise RuntimeError(f"non-floating adapter tensor: {key}")
                interpolated = (
                    tensor32.float().mul(1.0 - alpha).add(tensor33.float(), alpha=alpha)
                ).to(tensor32.dtype)
                output_tensors[key] = interpolated.contiguous()
                tensor_contract.append(
                    {
                        "key": key,
                        "shape": list(tensor32.shape),
                        "dtype": str(tensor32.dtype),
                    }
                )
        weights_output = temporary_candidate / "adapter_model.safetensors"
        save_file(output_tensors, weights_output, metadata=metadata32)
        write_new(temporary_candidate / "adapter_config.json", config32)

        with safe_open(weights_output, framework="pt", device="cpu") as observed:
            if sorted(observed.keys()) != keys32 or observed.metadata() != metadata32:
                raise RuntimeError("written interpolation key/metadata contract changed")
            for key in keys32:
                value = observed.get_tensor(key)
                expected = output_tensors[key]
                if value.shape != expected.shape or value.dtype != expected.dtype:
                    raise RuntimeError(f"written interpolation shape/dtype changed: {key}")
                if not torch.equal(value, expected):
                    raise RuntimeError(f"written interpolation values changed: {key}")

        write_new(temporary / "STABLE", b"")
        candidate_identity = tree_identity(temporary_candidate)
        contract = {
            "schema": "harness-posttrain.step32-step33-lora-interpolation-receipt.v1",
            "status": "ok",
            "method": "elementwise_adapter_parameter_linear_interpolation",
            "alpha": {
                "fraction": f"{alpha_fraction.numerator}/{alpha_fraction.denominator}",
                "float": alpha,
                "formula": "(1-alpha)*step32 + alpha*step33",
            },
            "parents": {
                "step32": {
                    "path": str(step32),
                    **step32_identity,
                    "receipt_file_sha256": "40e8f39c9550c03c08d58edccab649ed39b5e3e5ccb1a458d5888e4138889671",
                },
                "step33": {
                    "path": str(step33),
                    **step33_identity,
                    "receipt_file_sha256": "51d205e8e6dd0ee346546a4ade4fae3334bf289266e11f0e3b19f19175f50c40",
                },
            },
            "adapter_config_sha256": hashlib.sha256(config32).hexdigest(),
            "tensor_contract": {
                "count": len(tensor_contract),
                "keys_shapes_dtypes_sha256": digest(tensor_contract),
                "metadata_sha256": digest(metadata32),
            },
            "candidate": {
                "path": str(output_root / "lora_adapters"),
                **candidate_identity,
                "adapter_config_sha256": hashlib.sha256(config32).hexdigest(),
                "stable_marker_sha256": hashlib.sha256(b"").hexdigest(),
            },
            "training_rows": 0,
            "optimizer_updates": 0,
            "evaluation_rows": 0,
        }
        sealed = {**contract, "receipt_body_sha256": digest(contract)}
        write_new(
            temporary / "interpolation_receipt.json",
            json.dumps(sealed, sort_keys=True, indent=2).encode() + b"\n",
        )
        os.rename(temporary, output_root)
        directory = os.open(output_root.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        final_receipt = output_root / "interpolation_receipt.json"
        print(
            json.dumps(
                {
                    "alpha": sealed["alpha"],
                    "candidate": sealed["candidate"],
                    "receipt": str(final_receipt),
                    "receipt_file_sha256": file_sha(final_receipt),
                    "receipt_body_sha256": sealed["receipt_body_sha256"],
                    "tensor_contract": sealed["tensor_contract"],
                },
                sort_keys=True,
            )
        )
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


if __name__ == "__main__":
    main()
