#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
phase="${1:?usage: train_browser_action_fixed_v5.sh prep|train|all CAMPAIGN_ROOT}"
target="${2:?usage: train_browser_action_fixed_v5.sh prep|train|all CAMPAIGN_ROOT}"
source_sha="${CAVEAT_27B_EXECUTION_SOURCE_GIT_SHA:?CAVEAT_27B_EXECUTION_SOURCE_GIT_SHA is required}"
artifact_sha="${CAVEAT_27B_ARTIFACT_SOURCE_GIT_SHA:?CAVEAT_27B_ARTIFACT_SOURCE_GIT_SHA is required}"

case "$phase" in
  prep|train|all) ;;
  *)
    echo "phase must be prep, train, or all" >&2
    exit 2
    ;;
esac
[[ "$target" =~ ^/data/caveat-27b/[0-9a-f]{40}$ ]] || {
  echo "campaign target must be /data/caveat-27b/<40 lowercase hex>" >&2
  exit 2
}
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "execution source SHA must be 40 lowercase hexadecimal characters" >&2
  exit 2
}
[[ "$artifact_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "artifact source SHA must be 40 lowercase hexadecimal characters" >&2
  exit 2
}

export FLA_TILELANG=0
export PYTHONPATH="$root/src:$root/vendor/harness_distill-0.1.0-py3-none-any.whl${PYTHONPATH:+:$PYTHONPATH}"
python_bin="${CAVEAT_27B_PYTHON:-python3}"
stage="$target/browser_action_fixed_v5/$artifact_sha"

if [[ "$phase" == prep || "$phase" == all ]]; then
  "$python_bin" -m caveat_27b.cli prepare-browser-action-fixed-v5 \
    --campaign "$root/configs/campaign.yaml" \
    --campaign-root "$target" \
    --output "$stage" \
    --num-gpus 4
  if [[ "$phase" == prep ]]; then
    exit 0
  fi
fi

config="$stage/training/browser_action_fixed_v5.toml"
plan="$stage/training/plan.json"
[[ -f "$config" && -f "$plan" ]] || {
  echo "fixed-v5 preparation artifacts are absent" >&2
  exit 1
}

sft @ "$config"

adapter="$stage/training/prime_output/weights/step_26/lora_adapters"
[[ -f "$adapter/adapter_config.json" && -f "$adapter/adapter_model.safetensors" ]] || {
  echo "stable fixed-v5 step-26 adapter is absent" >&2
  exit 1
}

"$python_bin" -m caveat_27b.cli write-browser-action-fixed-v5-receipt \
  --campaign "$root/configs/campaign.yaml" \
  --campaign-root "$target" \
  --stage "$stage" \
  --artifact-source-git-sha "$artifact_sha" \
  --execution-source-git-sha "$source_sha"
