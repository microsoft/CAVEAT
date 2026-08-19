#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:?usage: train_browser_action_continuation.sh CAMPAIGN_ROOT}"
source_sha="${HPT_EXECUTION_SOURCE_GIT_SHA:?HPT_EXECUTION_SOURCE_GIT_SHA is required}"
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "execution source SHA must be 40 lowercase hexadecimal characters" >&2
  exit 2
}
artifact_sha="${HPT_ARTIFACT_SOURCE_GIT_SHA:-$source_sha}"
[[ "$artifact_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "artifact source SHA must be 40 lowercase hexadecimal characters" >&2
  exit 2
}

export FLA_TILELANG=0
export PYTHONPATH="$root/src:$root/vendor/harness_distill-0.1.0-py3-none-any.whl${PYTHONPATH:+:$PYTHONPATH}"
stage="$target/browser_action_correction/$artifact_sha"
config="$stage/training/browser_action_continuation.toml"
plan="$stage/training/plan.json"
[[ -f "$config" && -f "$plan" ]] || {
  echo "browser-action continuation preparation receipt is absent" >&2
  exit 1
}

sft @ "$config"

for update in 22 24 26 28; do
  adapter="$stage/training/prime_output/weights/step_${update}/lora_adapters"
  [[ -f "$adapter/adapter_config.json" && -f "$adapter/adapter_model.safetensors" ]] || {
    echo "stable adapter for candidate step $update is absent" >&2
    exit 1
  }
done

"${HPT_PYTHON:-python3}" -m harness_posttrain.cli \
  write-browser-action-continuation-receipt \
  --campaign "$root/configs/campaign.yaml" \
  --campaign-root "$target" \
  --continuation-dir "$stage/training" \
  --artifact-source-git-sha "$artifact_sha" \
  --execution-source-git-sha "$source_sha"

"${HPT_PYTHON:-python3}" -m harness_posttrain.cli \
  select-browser-action-checkpoint \
  --campaign "$root/configs/campaign.yaml" \
  --campaign-root "$target" \
  --continuation-dir "$stage/training" \
  --output "$stage/selection" \
  --concurrency 64 \
  --num-gpus 4 \
  --port 8000

raw_base="$("${HPT_PYTHON:-python3}" - "$target/smoke/smoke_report.json" <<'PY'
import json
import sys

record = json.load(open(sys.argv[1], encoding="utf-8"))
value = record.get("resolved_snapshot")
if not isinstance(value, str) or not value.startswith("/data/"):
    raise SystemExit("smoke report has no absolute raw snapshot")
print(value)
PY
)"
"${HPT_PYTHON:-python3}" -m harness_posttrain.cli \
  finalize-browser-action-correction \
  --campaign "$root/configs/campaign.yaml" \
  --campaign-root "$target" \
  --continuation-receipt "$stage/training/training_receipt.json" \
  --selection-manifest "$stage/selection/selected_checkpoint.json" \
  --raw-base "$raw_base"
