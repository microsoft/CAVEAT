#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

(( $# == 2 )) || fail "expected runner SHA and served alias"
self_sha="$1"
alias="$2"
self="$(readlink -f -- "${BASH_SOURCE[0]}")"
source_root="$(readlink -f -- "$(dirname -- "$self")/..")"
runtime_python=/opt/prime-rl/.venv/bin/python

receipt=/data/runs/t-yuxuanli/t-yuxuanli-hpt-q35-sol-dagger-sft-698a7c6-w4-20260814/training_r1/training/training_receipt.json
parent=/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/selected/merged
adapter=/data/runs/t-yuxuanli/t-yuxuanli-hpt-q35-sol-dagger-sft-698a7c6-w4-20260814/training_r1/training/prime_output/weights/step_25/lora_adapters

[[ "$self_sha" =~ ^[0-9a-f]{64}$ \
  && "$(sha256sum -- "$self" | cut -d' ' -f1)" == "$self_sha" \
  && "$self" == "$source_root/scripts/run_campaign2_step25_parent_serve.sh" ]] \
  || fail "campaign-2 parent runner identity changed"
[[ "$source_root" == /data/runs/t-yuxuanli/* \
  && "$alias" == qwen35-browser-action-sol-dagger-step25-6b4f66832c2e-exact-lora \
  && "${POD_UID:-}" =~ ^[0-9a-f-]{36}$ \
  && -x "$runtime_python" \
  && "$($runtime_python -c 'import torch; print(torch.cuda.device_count())')" == 4 ]] \
  || fail "campaign-2 parent runtime contract changed"

mapfile -t audited < <(
  env PYTHONPATH="$source_root/src" "$runtime_python" - "$receipt" "$parent" "$adapter" <<'PY'
import sys
from pathlib import Path

from harness_posttrain_eval.sol_dagger_step26_candidate_serve import (
    PARENT_ADAPTER_TREE,
    PARENT_PATH,
    PARENT_RECEIPT_PATH,
    _tree_identity,
    validate_step25_parent,
)
from harness_posttrain_eval.sol_dagger_candidate_serve import PARENT_TREE

receipt, parent, adapter = (Path(value).resolve() for value in sys.argv[1:])
value = validate_step25_parent(receipt)
candidate = value["candidate"]
if receipt != PARENT_RECEIPT_PATH or parent != PARENT_PATH:
    raise SystemExit("campaign-2 step-25 parent paths changed")
if Path(candidate["path"]).resolve() != adapter:
    raise SystemExit("campaign-2 step-25 adapter path changed")
identity = _tree_identity(adapter)
parent_identity = _tree_identity(parent)
if parent_identity["tree_sha256"] != PARENT_TREE:
    raise SystemExit("campaign-2 step-25 base parent tree changed")
if identity["tree_sha256"] != PARENT_ADAPTER_TREE:
    raise SystemExit("campaign-2 step-25 adapter tree changed")
if any(identity[key] != candidate[key] for key in ("files", "bytes", "tree_sha256")):
    raise SystemExit("campaign-2 step-25 adapter receipt identity changed")
print(parent)
print(adapter)
PY
)
[[ ${#audited[@]} -eq 2 \
  && "${audited[0]}" == "$parent" \
  && "${audited[1]}" == "$adapter" ]] \
  || fail "campaign-2 parent lineage audit returned different components"

printf '%s campaign2_step25_parent_validated alias=%s pod_uid=%s\n' \
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
