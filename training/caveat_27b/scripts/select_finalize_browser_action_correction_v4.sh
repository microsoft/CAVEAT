#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:?usage: select_finalize_browser_action_correction_v4.sh CAMPAIGN_ROOT}"
artifact_sha="${CAVEAT_27B_ARTIFACT_SOURCE_GIT_SHA:?CAVEAT_27B_ARTIFACT_SOURCE_GIT_SHA is required}"
selection_name="${CAVEAT_27B_ACTION_SELECTION_NAME:-selection_nonbinding_v4}"
[[ "$artifact_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "artifact source SHA must be 40 lowercase hexadecimal characters" >&2
  exit 2
}
[[ "$selection_name" == "selection_nonbinding_v4" ]] || {
  echo "CAVEAT_27B_ACTION_SELECTION_NAME must be selection_nonbinding_v4" >&2
  exit 2
}

export FLA_TILELANG=0
export PYTHONPATH="$root/src:$root/vendor/harness_distill-0.1.0-py3-none-any.whl${PYTHONPATH:+:$PYTHONPATH}"
stage="$target/browser_action_correction/$artifact_sha"
receipt="$stage/training/training_receipt.json"
v3="$stage/selection_nonbinding_v3"
output="$stage/$selection_name"
[[ -f "$receipt" ]] || {
  echo "completed browser-action continuation receipt is absent" >&2
  exit 1
}
[[ -d "$v3" && ! -L "$v3" ]] || {
  echo "exact failed v3 selector directory is absent or unsafe" >&2
  exit 1
}
[[ ! -L "$output" ]] || {
  echo "adaptive selector output cannot be a symlink: $output" >&2
  exit 1
}
if [[ -e "$output" && ! -d "$output" ]]; then
  echo "adaptive selector output exists but is not a directory: $output" >&2
  exit 1
fi
# V4 independently validates and hash-binds the exact failed v3 attempt. It
# imports all 470 natural-stop rows without reading their scores, freezes the
# exact ten-row objective complement, and executes only that complement once.
"${CAVEAT_27B_PYTHON:-python3}" -m caveat_27b.cli \
  select-browser-action-checkpoint-v4 \
  --campaign "$root/configs/campaign.yaml" \
  --campaign-root "$target" \
  --continuation-dir "$stage/training" \
  --v3-selection-dir "$v3" \
  --output "$output" \
  --concurrency "${CAVEAT_27B_ACTION_SELECTION_CONCURRENCY:-10}" \
  --num-gpus 4 \
  --port "${CAVEAT_27B_ACTION_SELECTION_PORT:-8000}"

raw_base="$("${CAVEAT_27B_PYTHON:-python3}" - "$target/smoke/smoke_report.json" <<'PY'
import json
import sys

record = json.load(open(sys.argv[1], encoding="utf-8"))
value = record.get("resolved_snapshot")
if not isinstance(value, str) or not value.startswith("/data/"):
    raise SystemExit("smoke report has no absolute raw snapshot")
print(value)
PY
)"
"${CAVEAT_27B_PYTHON:-python3}" -m caveat_27b.cli \
  finalize-browser-action-correction \
  --campaign "$root/configs/campaign.yaml" \
  --campaign-root "$target" \
  --continuation-receipt "$receipt" \
  --selection-manifest "$output/selected_checkpoint.json" \
  --raw-base "$raw_base"
