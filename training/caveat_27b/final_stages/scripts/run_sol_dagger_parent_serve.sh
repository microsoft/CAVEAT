#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

(( $# == 2 )) || fail "expected runner SHA and served alias"
self_sha="$1"
alias="$2"
self="$(readlink -f -- "${BASH_SOURCE[0]}")"

receipt=/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/browser_action_fixed_v7_amazon_r00_repair/86e7f2cab46dedcb2b44b7fe0931d1ed60fc8dc1/training_v4_r3/training/training_receipt.json
parent=/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/selected/merged
adapter=/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/browser_action_fixed_v7_amazon_r00_repair/86e7f2cab46dedcb2b44b7fe0931d1ed60fc8dc1/training_v4_r3/training/prime_output/weights/step_24/lora_adapters

[[ "$self_sha" =~ ^[0-9a-f]{64}$ \
  && "$(sha256sum -- "$self" | cut -d' ' -f1)" == "$self_sha" ]] \
  || fail "student serve runner identity changed"
[[ "$alias" == qwen35-browser-action-fixed-v7-repair-6cf2d5a0154f-exact-lora ]] \
  || fail "student served alias changed"
[[ "${POD_UID:-}" =~ ^[0-9a-f-]{36}$ ]] || fail "pod identity is absent"
runtime_python=/opt/prime-rl/.venv/bin/python
[[ -x "$runtime_python" ]] || fail "pinned runtime Python is absent"
[[ "$($runtime_python -c 'import torch; print(torch.cuda.device_count())')" == 4 ]] \
  || fail "student serve requires exactly four visible GPUs"

mapfile -t audited < <(/usr/bin/python3 - "$receipt" "$parent" "$adapter" <<'PY'
import hashlib
import json
import os
import stat
import sys
from pathlib import Path


class Error(RuntimeError):
    pass


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def file_sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_file(path):
    return not path.is_symlink() and path.is_file() and stat.S_ISREG(path.lstat().st_mode)


def tree(root):
    if root.is_symlink() or not root.is_dir() or root.resolve() != root:
        raise Error("unsafe component root")
    entries = {}
    total = 0
    for current, directories, files in os.walk(root, followlinks=False):
        here = Path(current)
        for name in directories:
            child = here / name
            if child.is_symlink() or not stat.S_ISDIR(child.lstat().st_mode):
                raise Error("unsafe component directory")
        for name in files:
            path = here / name
            if not safe_file(path):
                raise Error("unsafe component file")
            relative = path.relative_to(root).as_posix()
            size = path.stat().st_size
            entries[relative] = {"size": size, "sha256": file_sha(path)}
            total += size
    if not entries:
        raise Error("empty component tree")
    return {
        "files": len(entries),
        "bytes": total,
        "tree_sha256": hashlib.sha256(canonical(entries)).hexdigest(),
    }


receipt_path, parent, adapter = map(lambda value: Path(value).resolve(), sys.argv[1:])
if (
    str(receipt_path)
    != "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/browser_action_fixed_v7_amazon_r00_repair/86e7f2cab46dedcb2b44b7fe0931d1ed60fc8dc1/training_v4_r3/training/training_receipt.json"
    or str(parent)
    != "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/selected/merged"
    or str(adapter)
    != "/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/browser_action_fixed_v7_amazon_r00_repair/86e7f2cab46dedcb2b44b7fe0931d1ed60fc8dc1/training_v4_r3/training/prime_output/weights/step_24/lora_adapters"
):
    raise Error("repair parent paths changed")
if not safe_file(receipt_path) or file_sha(receipt_path) != "85d5c779c8fb1ab4a1a87d4e4f4ab9bd7809675622bfe845a0632b2ab6ae4bf1":
    raise Error("repair training receipt bytes changed")
receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
body = {key: value for key, value in receipt.items() if key != "receipt_body_sha256"}
candidate = receipt.get("candidate") or {}
if (
    receipt.get("schema")
    != "harness-distill.amazon-r00-repair-sft-training-receipt.v1"
    or receipt.get("receipt_body_sha256")
    != "aa60d14b36e2bd5ae3cdab34cc74665ed883a03e59384f1174a17c624235fb1e"
    or hashlib.sha256(canonical(body)).hexdigest() != receipt.get("receipt_body_sha256")
    or receipt.get("status") != "ok"
    or receipt.get("source_step") != 23
    or receipt.get("final_step") != 24
    or receipt.get("optimizer_updates") != 1
    or receipt.get("learning_rate") != 5e-7
    or receipt.get("assistant_tokens_only") is not True
    or receipt.get("laptop_r00_used") is not True
    or receipt.get("laptop_r01_used") is not False
    or receipt.get("office_chair_used") is not False
    or candidate.get("name") != "step24-amazon-r00-repair-sft"
    or candidate.get("update") != 24
    or candidate.get("path") != str(adapter)
):
    raise Error("repair training receipt semantics changed")
parent_identity = tree(parent)
adapter_identity = tree(adapter)
if (
    parent_identity["tree_sha256"]
    != "5939382fbc6db775972dc9cebf3e5be20654149a0412b4f00072bad12a2215ca"
    or adapter_identity
    != {
        "files": 2,
        "bytes": 933975509,
        "tree_sha256": "49e66d189603142232d0e4f1b3549d32b783ebed5fa4d3efd0e04af3197a5508",
    }
    or any(adapter_identity[key] != candidate.get(key) for key in ("files", "bytes", "tree_sha256"))
    or file_sha(adapter / "adapter_config.json")
    != "65aaf22799f16fe45c4b52cab14e14e480f28c5817fc2a808b0aeebd193ae6c7"
    or file_sha(adapter.parent / "STABLE")
    != "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
):
    raise Error("repair model component identity changed")
print(parent)
print(adapter)
PY
)
[[ ${#audited[@]} -eq 2 ]] || fail "student serve audit returned an invalid component set"
[[ "${audited[0]}" == "$parent" && "${audited[1]}" == "$adapter" ]] \
  || fail "student serve audit resolved different components"

printf '%s repair_parent_validated alias=%s pod_uid=%s\n' \
  "$(date -u +%FT%TZ)" "$alias" "$POD_UID"

exec env -u VLLM_ALLOW_RUNTIME_LORA_UPDATING -u VLLM_API_KEY FLA_TILELANG=0 \
  vllm serve "$parent" --tokenizer "$parent" \
  --served-model-name qwen35-exact-lora-parent --host 0.0.0.0 --port 8000 \
  --dtype bfloat16 --generation-config vllm --language-model-only \
  --max-model-len 32768 --reasoning-parser qwen3 --tool-call-parser qwen3_coder \
  --enable-auto-tool-choice --no-enable-prefix-caching --no-enable-log-requests \
  --enable-lora --max-loras 1 --max-cpu-loras 1 --max-lora-rank 64 \
  --lora-dtype bfloat16 --lora-modules "$alias=$adapter" \
  --data-parallel-size 4 --api-server-count 4
