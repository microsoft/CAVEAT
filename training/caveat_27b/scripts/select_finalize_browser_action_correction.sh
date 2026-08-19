#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:?usage: select_finalize_browser_action_correction.sh CAMPAIGN_ROOT}"
artifact_sha="${CAVEAT_27B_ARTIFACT_SOURCE_GIT_SHA:?CAVEAT_27B_ARTIFACT_SOURCE_GIT_SHA is required}"
selection_name="${CAVEAT_27B_ACTION_SELECTION_NAME:-selection_nonbinding_v2}"
[[ "$artifact_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "artifact source SHA must be 40 lowercase hexadecimal characters" >&2
  exit 2
}
[[ "$selection_name" == "selection_nonbinding_v2" ]] || {
  echo "CAVEAT_27B_ACTION_SELECTION_NAME must be selection_nonbinding_v2" >&2
  exit 2
}

export FLA_TILELANG=0
export PYTHONPATH="$root/src:$root/vendor/harness_distill-0.1.0-py3-none-any.whl${PYTHONPATH:+:$PYTHONPATH}"
stage="$target/browser_action_correction/$artifact_sha"
receipt="$stage/training/training_receipt.json"
output="$stage/$selection_name"
[[ -f "$receipt" ]] || {
  echo "completed browser-action continuation receipt is absent" >&2
  exit 1
}
[[ ! -e "$output" && ! -L "$output" ]] || {
  echo "fresh create-only selector output already exists: $output" >&2
  exit 1
}

"${CAVEAT_27B_PYTHON:-python3}" -m caveat_27b.cli \
  select-browser-action-checkpoint \
  --campaign "$root/configs/campaign.yaml" \
  --campaign-root "$target" \
  --continuation-dir "$stage/training" \
  --output "$output" \
  --concurrency "${CAVEAT_27B_ACTION_SELECTION_CONCURRENCY:-64}" \
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
