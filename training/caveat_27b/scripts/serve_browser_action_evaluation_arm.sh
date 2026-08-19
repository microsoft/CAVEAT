#!/usr/bin/env bash
set -euo pipefail

manifest="${1:?usage: serve_browser_action_evaluation_arm.sh MANIFEST_PATH base|trained}"
arm="${2:?usage: serve_browser_action_evaluation_arm.sh MANIFEST_PATH base|trained}"
source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

case "$arm" in
  base|trained) ;;
  *)
    echo "arm must be base or trained" >&2
    exit 2
    ;;
esac

# The Python gate resolves artifact-controlled paths without evaluating shell
# text and re-hashes the manifest receipt, parent, adapter, tokenizer, config,
# and exact-LoRA composite immediately before vLLM starts.
readarray -t serving < <(
  PYTHONPATH="$source_root/src${PYTHONPATH:+:$PYTHONPATH}" \
    "${CAVEAT_27B_PYTHON:-python3}" - "$manifest" "$arm" <<'PY'
import sys

from caveat_27b.browser_action_finalization import (
    validate_browser_action_serving_manifest,
)

resolved = validate_browser_action_serving_manifest(sys.argv[1], arm=sys.argv[2])
for key in ("parent", "adapter", "tokenizer", "served_model_name"):
    value = resolved[key]
    if "\n" in value or "\r" in value:
        raise SystemExit(f"invalid newline in serving field: {key}")
    print(value)
PY
)
[[ "${#serving[@]}" -eq 4 ]] || {
  echo "failed to resolve corrected exact-LoRA serving components" >&2
  exit 1
}

model="${serving[0]}"
adapter="${serving[1]}"
tokenizer="${serving[2]}"
served_name="${serving[3]}"

exec env -u VLLM_ALLOW_RUNTIME_LORA_UPDATING FLA_TILELANG=0 \
  vllm serve "$model" \
  --tokenizer "$tokenizer" \
  --served-model-name "caveat-27b-parent-exact-lora" \
  --host 0.0.0.0 \
  --port 8000 \
  --dtype bfloat16 \
  --generation-config vllm \
  --language-model-only \
  --max-model-len 32768 \
  --reasoning-parser qwen3 \
  --tool-call-parser qwen3_coder \
  --enable-auto-tool-choice \
  --no-enable-prefix-caching \
  --no-enable-log-requests \
  --enable-lora \
  --max-loras 1 \
  --max-cpu-loras 1 \
  --max-lora-rank 64 \
  --lora-dtype bfloat16 \
  --lora-modules "$served_name=$adapter" \
  --data-parallel-size 4 \
  --api-server-count 4
