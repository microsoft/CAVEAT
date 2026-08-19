#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:?usage: prepare_browser_action_continuation.sh CAMPAIGN_ROOT}"
source_sha="${HPT_EXECUTION_SOURCE_GIT_SHA:?HPT_EXECUTION_SOURCE_GIT_SHA is required}"
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "execution source SHA must be 40 lowercase hexadecimal characters" >&2
  exit 2
}

export FLA_TILELANG=0
export PYTHONPATH="$root/src:$root/vendor/harness_distill-0.1.0-py3-none-any.whl${PYTHONPATH:+:$PYTHONPATH}"
python_bin="${HPT_PYTHON:-python3}"
stage="$target/browser_action_correction/$source_sha"

"$python_bin" -m harness_posttrain.cli materialize-browser-action-curriculum \
  --campaign "$root/configs/campaign.yaml" \
  --split-manifest "$target/corpus/splits/manifest.json" \
  --rehearsal "$target/corpus/raw/rehearsal.jsonl" \
  --contract-replay "$target/corpus/raw/contract.jsonl" \
  --output "$stage/curriculum" \
  --task-limit 256 \
  --contract-replay-limit 112

"$python_bin" -m harness_posttrain.cli materialize-prime \
  --campaign "$root/configs/campaign.yaml" \
  --source-jsonl "$stage/curriculum/train.jsonl" \
  --source-manifest "$stage/curriculum/manifest.json" \
  --smoke-report "$target/smoke/smoke_report.json" \
  --stage refinement \
  --output "$stage/prime" \
  --prime-root "${HPT_PRIME_ROOT:-/opt/prime-rl}"

"$python_bin" -m harness_posttrain.cli prepare-browser-action-continuation \
  --campaign "$root/configs/campaign.yaml" \
  --campaign-root "$target" \
  --curriculum-manifest "$stage/curriculum/manifest.json" \
  --dataset "$stage/prime" \
  --smoke-report "$target/smoke/smoke_report.json" \
  --output "$stage/training" \
  --num-gpus 4

