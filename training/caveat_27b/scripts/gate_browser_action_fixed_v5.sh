#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:?usage: gate_browser_action_fixed_v5.sh CAMPAIGN_ROOT}"
artifact_sha="${CAVEAT_27B_ARTIFACT_SOURCE_GIT_SHA:?CAVEAT_27B_ARTIFACT_SOURCE_GIT_SHA is required}"
baseline_sha="${CAVEAT_27B_BASELINE_ACTION_ARTIFACT_SOURCE_SHA:?CAVEAT_27B_BASELINE_ACTION_ARTIFACT_SOURCE_SHA is required}"

[[ "$target" =~ ^/data/caveat-27b/[0-9a-f]{40}$ ]] || {
  echo "campaign target must be /data/caveat-27b/<40 lowercase hex>" >&2
  exit 2
}
[[ "$artifact_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "fixed-v5 artifact source SHA must be 40 lowercase hexadecimal characters" >&2
  exit 2
}
[[ "$baseline_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "baseline artifact source SHA must be 40 lowercase hexadecimal characters" >&2
  exit 2
}

export FLA_TILELANG=0
export PYTHONPATH="$root/src:$root/vendor/harness_distill-0.1.0-py3-none-any.whl${PYTHONPATH:+:$PYTHONPATH}"
stage="$target/browser_action_fixed_v5/$artifact_sha"
receipt="$stage/training/training_receipt.json"
baseline="$target/browser_action_correction/$baseline_sha/selection_nonbinding_v4"
output="$stage/procedural_gate"

wait_seconds="${CAVEAT_27B_ACTION_FIXED_V5_GATE_WAIT_SECONDS:-0}"
[[ "$wait_seconds" =~ ^[0-9]+$ ]] || {
  echo "CAVEAT_27B_ACTION_FIXED_V5_GATE_WAIT_SECONDS must be a nonnegative integer" >&2
  exit 2
}
deadline=$((SECONDS + wait_seconds))
while [[ ! -f "$receipt" ]]; do
  if (( SECONDS >= deadline )); then
    echo "exact fixed-v5 training receipt did not appear before the wait deadline: $receipt" >&2
    exit 1
  fi
  sleep 10
done

[[ -f "$receipt" && ! -L "$receipt" ]] || {
  echo "exact fixed-v5 training receipt is absent or unsafe: $receipt" >&2
  exit 1
}
[[ -d "$baseline" && ! -L "$baseline" ]] || {
  echo "exact adaptive-v4 baseline directory is absent or unsafe: $baseline" >&2
  exit 1
}
[[ -f "$baseline/selected_checkpoint.json" && -f "$baseline/raw_evidence.jsonl" ]] || {
  echo "adaptive-v4 baseline evidence is incomplete: $baseline" >&2
  exit 1
}
[[ ! -L "$output" ]] || {
  echo "fixed-v5 gate output cannot be a symlink: $output" >&2
  exit 1
}
if [[ -e "$output" && ! -d "$output" ]]; then
  echo "fixed-v5 gate output exists but is not a directory: $output" >&2
  exit 1
fi

"${CAVEAT_27B_PYTHON:-python3}" -m caveat_27b.cli \
  gate-browser-action-fixed-v5 \
  --campaign "$root/configs/campaign.yaml" \
  --campaign-root "$target" \
  --training-receipt "$receipt" \
  --baseline-selection-dir "$baseline" \
  --output "$output" \
  --concurrency "${CAVEAT_27B_ACTION_FIXED_V5_GATE_CONCURRENCY:-32}" \
  --num-gpus 4 \
  --port "${CAVEAT_27B_ACTION_FIXED_V5_GATE_PORT:-8000}"
