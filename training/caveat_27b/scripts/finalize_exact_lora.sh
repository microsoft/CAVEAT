#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:?usage: finalize_exact_lora.sh CAMPAIGN_ROOT RAW_BASE [MERGE_DIAGNOSTIC_JSON]}"
raw_base="${2:?usage: finalize_exact_lora.sh CAMPAIGN_ROOT RAW_BASE [MERGE_DIAGNOSTIC_JSON]}"
diagnostic="${3:-}"
python_bin="${CAVEAT_27B_PYTHON:-python3}"

export FLA_TILELANG=0
export PYTHONPATH="$root/src:$root/vendor/harness_distill-0.1.0-py3-none-any.whl${PYTHONPATH:+:$PYTHONPATH}"

[[ -f "$target/refinement/training_receipt.json" ]] || {
  echo "successful refinement training receipt is absent" >&2
  exit 1
}
[[ -d "$raw_base" ]] || {
  echo "immutable raw model snapshot is absent: $raw_base" >&2
  exit 1
}

args=(
  --campaign "$root/configs/campaign.yaml"
  --campaign-root "$target"
  --raw-base "$raw_base"
  --device "${CAVEAT_27B_FINALIZE_DEVICE:-cuda:0}"
)
if [[ -n "$diagnostic" ]]; then
  args+=(--merge-failure-diagnostic "$diagnostic")
fi

exec "$python_bin" -m caveat_27b.cli finalize-exact-lora "${args[@]}"
