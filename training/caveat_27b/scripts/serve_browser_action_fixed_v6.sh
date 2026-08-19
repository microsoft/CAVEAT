#!/usr/bin/env bash
set -euo pipefail

self_sha="${1:?expected entrypoint SHA-256 is required}"
source_root="${2:?staged source root is required}"
source_tree_sha="${3:?staged source tree SHA-256 is required}"
execution_sha="${4:?execution source Git SHA is required}"
artifact_sha="${5:?artifact source Git SHA is required}"
campaign_root="${6:?campaign root is required}"
training_receipt="${7:?training receipt is required}"
raw_base="${8:?raw base is required}"

[[ "$self_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$source_tree_sha" =~ ^[0-9a-f]{64}$ ]]
[[ "$execution_sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$artifact_sha" =~ ^[0-9a-f]{40}$ ]]
[[ -f "$0" && ! -L "$0" ]]
[[ "$(sha256sum -- "$0" | cut -d' ' -f1)" == "$self_sha" ]] || {
  echo "fixed-v6 serve entrypoint identity changed" >&2
  exit 1
}
for path in "$source_root" "$campaign_root" "$training_receipt" "$raw_base"; do
  [[ "$path" == /data/* ]]
done
[[ -d "$source_root" && ! -L "$source_root" ]]
[[ "${CAVEAT_27B_EXECUTION_SOURCE_GIT_SHA:-}" == "$execution_sha" ]]
[[ "${CAVEAT_27B_ARTIFACT_SOURCE_GIT_SHA:-}" == "$artifact_sha" ]]

observed_tree="$(python3 - "$source_root" <<'PY'
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
digest = hashlib.sha256()
for path in sorted(
    candidate
    for candidate in root.rglob("*")
    if candidate.is_file() and ".git" not in candidate.parts
):
    relative = path.relative_to(root).as_posix().encode()
    payload = path.read_bytes()
    digest.update(len(relative).to_bytes(8, "big"))
    digest.update(relative)
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)
print(digest.hexdigest())
PY
)"
[[ "$observed_tree" == "$source_tree_sha" ]] || {
  echo "fixed-v6 staged source identity changed" >&2
  exit 1
}

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$source_root/src:$source_root/vendor/harness_distill-0.1.0-py3-none-any.whl"
stage="$campaign_root/browser_action_fixed_v6/$artifact_sha"
readarray -t serving < <(
  python3 -m caveat_27b.browser_action_fixed_v6_serving \
    --campaign "$source_root/configs/campaign.yaml" \
    --campaign-root "$campaign_root" \
    --training-receipt "$training_receipt" \
    --raw-base "$raw_base" \
    --artifact-source-git-sha "$artifact_sha" \
    --execution-source-git-sha "$execution_sha" \
    --output "$stage/pre_gate"
)
[[ "${#serving[@]}" -eq 5 ]] || {
  echo "fixed-v6 paired serving identities are incomplete" >&2
  exit 1
}
parent="${serving[0]}"
step20_adapter="${serving[1]}"
step20_name="${serving[2]}"
candidate_adapter="${serving[3]}"
candidate_name="${serving[4]}"

exec env -u VLLM_ALLOW_RUNTIME_LORA_UPDATING FLA_TILELANG=0 \
  vllm serve "$parent" \
  --tokenizer "$parent" \
  --served-model-name caveat-27b-parent-exact-lora \
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
  --max-loras 2 \
  --max-cpu-loras 2 \
  --max-lora-rank 64 \
  --lora-dtype bfloat16 \
  --lora-modules "$step20_name=$step20_adapter" "$candidate_name=$candidate_adapter" \
  --data-parallel-size 4 \
  --api-server-count 4
