#!/usr/bin/env bash
set -euo pipefail

campaign_root="${1:?usage: serve_evaluation_arm.sh CAMPAIGN_ROOT base|trained}"
arm="${2:?usage: serve_evaluation_arm.sh CAMPAIGN_ROOT base|trained}"
base_model="/data/cache/huggingface/hub/models--Qwen--Qwen3.5-27B/snapshots/fc05daec18b0a78c049392ed2e771dde82bdf654"
shared_tokenizer="$campaign_root/selected/merged"
poll_seconds="${HPT_EVAL_SERVE_POLL_SECONDS:-15}"
[[ "$poll_seconds" =~ ^[1-9][0-9]*$ ]] || {
  echo "HPT_EVAL_SERVE_POLL_SECONDS must be a positive integer" >&2
  exit 2
}

case "$arm" in
  base)
    model="$base_model"
    served_name="qwen35-27b-base"
    ;;
  trained)
    manifest="$campaign_root/final/inference_manifest.json"
    model="$campaign_root/final/model"
    served_name="qwen35-harness-posttrained-final"
    # Finalization publishes the model directory atomically and writes this
    # manifest only after numerical and provenance checks pass.  Waiting on it
    # lets a serving allocation queue without observing a partial checkpoint.
    while [[ ! -f "$manifest" ]]; do
      sleep "$poll_seconds"
    done
    python3 - "$manifest" "$model" "$served_name" <<'PY'
import json
import re
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1]).resolve()
model_path = Path(sys.argv[2]).resolve()
served_name = sys.argv[3]
record = json.loads(manifest_path.read_text(encoding="utf-8"))
if record.get("status") != "ok":
    raise SystemExit("final inference manifest is not successful")
if Path(str(record.get("model_path", ""))).resolve() != model_path:
    raise SystemExit("final inference manifest points at a different model")
if record.get("inference", {}).get("served_model_name") != served_name:
    raise SystemExit("final inference manifest has an unexpected served-model name")
if re.fullmatch(r"[0-9a-f]{64}", str(record.get("model_tree_sha256", ""))) is None:
    raise SystemExit("final inference manifest has no model-tree identity")
if not model_path.is_dir():
    raise SystemExit("final model directory is absent")
PY
    ;;
  *)
    echo "arm must be base or trained" >&2
    exit 2
    ;;
esac

[[ -d "$shared_tokenizer" ]] || {
  echo "shared tokenizer directory is absent: $shared_tokenizer" >&2
  exit 1
}

# Both arms use one explicit tokenizer.  After the permitted --model and
# --served-model-name values are normalized, these vLLM commands are identical.
exec env FLA_TILELANG=0 python3 -m vllm.entrypoints.openai.api_server \
  --model "$model" \
  --tokenizer "$shared_tokenizer" \
  --served-model-name "$served_name" \
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
  --data-parallel-size 4
