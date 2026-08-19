#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:?usage: train_sft_candidate.sh TARGET_DIR CANDIDATE}"
candidate="${2:?usage: train_sft_candidate.sh TARGET_DIR CANDIDATE}"
case "$candidate" in balanced|protocol-heavy|recovery-heavy) ;; *) exit 2 ;; esac
export FLA_TILELANG=0
export PYTHONPATH="$root/src:$root/vendor/harness_distill-0.1.0-py3-none-any.whl${PYTHONPATH:+:$PYTHONPATH}"
python_bin="${HPT_PYTHON:-python3}"
config="$target/configs/$candidate/$candidate.toml"
[[ -f "$target/prep_receipt.json" && -f "$config" ]] || {
  echo "successful prep receipt/config is absent" >&2
  exit 1
}

sft @ "$config"
"$python_bin" -m harness_posttrain.cli write-receipt \
  --campaign "$root/configs/campaign.yaml" --kind candidate \
  --candidate "$candidate" --target "$target"
