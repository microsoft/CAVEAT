#!/usr/bin/env bash
set -euo pipefail

arm="${1:?usage: serve_exact_lora_evaluation_arm.sh base|trained [CAMPAIGN_ROOT]}"
campaign_root="${2:-/data/caveat-27b/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30}"
manifest="$campaign_root/final/exact_lora_manifest.json"
shared_tokenizer="$campaign_root/selected/merged"
poll_seconds="${CAVEAT_27B_SERVE_POLL_SECONDS:-20}"
source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

[[ "$poll_seconds" =~ ^[1-9][0-9]*$ ]] || {
  echo "CAVEAT_27B_SERVE_POLL_SECONDS must be a positive integer" >&2
  exit 2
}
case "$arm" in
  base)
    served_name="qwen35-27b-base-exact-lora"
    ;;
  trained)
    served_name="caveat-27b-exact-lora"
    ;;
  *)
    echo "arm must be base or trained" >&2
    exit 2
    ;;
esac

# Both arms wait for the same publish-once manifest.  The validator prints the
# exact manifest-bound parent and adapter paths on separate lines; readarray
# avoids evaluating artifact-controlled shell text.
while [[ ! -f "$manifest" ]]; do
  sleep "$poll_seconds"
done
readarray -t component_paths < <(python3 - \
  "$manifest" "$arm" "$served_name" "$shared_tokenizer" "$source_root/src" <<'PY'
import hashlib
import json
import re
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1]).resolve()
arm = sys.argv[2]
served_name = sys.argv[3]
shared_tokenizer = Path(sys.argv[4]).resolve()
sys.path.insert(0, sys.argv[5])

from caveat_27b.artifacts import sha256_file
from caveat_27b.exact_lora import (
    component_identity,
    exact_lora_composite_sha256,
)

record = json.loads(manifest_path.read_text(encoding="utf-8"))
if record.get("schema") != "caveat-27b.exact-lora-inference.v1":
    raise SystemExit("unexpected exact-LoRA manifest schema")
if record.get("status") != "ok" or record.get("serving_mode") != "exact_peft_lora":
    raise SystemExit("exact-LoRA inference manifest is not successful")
if record.get("final_model_directory_published") is not False:
    raise SystemExit("exact-LoRA manifest ambiguously claims merged weights")
descriptor = record.get("arms", {}).get(arm, {})
if descriptor.get("served_model_name") != served_name:
    raise SystemExit("exact-LoRA manifest has an unexpected served-model name")
parent = Path(str(descriptor.get("parent", {}).get("path", ""))).resolve()
adapter = Path(str(descriptor.get("adapter", {}).get("path", ""))).resolve()
for label, value in (
    ("parent", descriptor.get("parent", {}).get("tree_sha256")),
    ("adapter", descriptor.get("adapter", {}).get("tree_sha256")),
    ("composite", descriptor.get("composite_sha256")),
):
    if re.fullmatch(r"[0-9a-f]{64}", str(value)) is None:
        raise SystemExit(f"exact-LoRA manifest has no {label} identity")
if not parent.is_dir() or not adapter.is_dir():
    raise SystemExit("an exact-LoRA component directory is absent")
if not (adapter / "adapter_config.json").is_file():
    raise SystemExit("exact-LoRA adapter configuration is absent")
if component_identity(parent) != descriptor.get("parent"):
    raise SystemExit("exact-LoRA parent bytes differ from the frozen manifest")
if component_identity(adapter) != descriptor.get("adapter"):
    raise SystemExit("exact-LoRA adapter bytes differ from the frozen manifest")
if sha256_file(adapter / "adapter_config.json") != descriptor.get("adapter_config_sha256"):
    raise SystemExit("exact-LoRA adapter config bytes differ from the frozen manifest")
shared = record.get("shared_tokenizer", {})
if Path(str(shared.get("path", ""))).resolve() != shared_tokenizer:
    raise SystemExit("exact-LoRA shared-tokenizer path differs from the frozen manifest")
if sha256_file(shared_tokenizer / "tokenizer.json") != shared.get("tokenizer_json_sha256"):
    raise SystemExit("exact-LoRA tokenizer bytes differ from the frozen manifest")
from transformers import AutoTokenizer
tokenizer = AutoTokenizer.from_pretrained(
    shared_tokenizer, local_files_only=True, trust_remote_code=False
)
template_sha256 = hashlib.sha256(str(tokenizer.chat_template).encode()).hexdigest()
if template_sha256 != shared.get("chat_template_sha256"):
    raise SystemExit("exact-LoRA chat template differs from the frozen manifest")
composite = exact_lora_composite_sha256(
    parent_tree_sha256=descriptor["parent"]["tree_sha256"],
    adapter_tree_sha256=descriptor["adapter"]["tree_sha256"],
    adapter_config_sha256=descriptor["adapter_config_sha256"],
    tokenizer_json_sha256=shared["tokenizer_json_sha256"],
    chat_template_sha256=shared["chat_template_sha256"],
    dtype=record.get("inference", {}).get("dtype", ""),
)
if composite != descriptor.get("composite_sha256"):
    raise SystemExit("exact-LoRA composite identity differs from the frozen manifest")
print(parent)
print(adapter)
PY
)
[[ "${#component_paths[@]}" -eq 2 ]] || {
  echo "failed to resolve exact-LoRA component paths" >&2
  exit 1
}
model="${component_paths[0]}"
adapter="${component_paths[1]}"

[[ -d "$shared_tokenizer" ]] || {
  echo "shared tokenizer directory is absent: $shared_tokenizer" >&2
  exit 1
}

# Requests address the LoRA alias, ensuring both baseline and trained arms use
# the same unmerged PEFT execution path.  Only the attested parent, adapter,
# and public alias differ between arms.  Use vLLM's supported ``serve`` CLI so
# its DP-aware launcher creates one API frontend per replica; a single legacy
# OpenAI-server frontend can pin staggered long requests to engine zero.
exec env -u VLLM_ALLOW_RUNTIME_LORA_UPDATING FLA_TILELANG=0 \
  vllm serve "$model" \
  --tokenizer "$shared_tokenizer" \
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
