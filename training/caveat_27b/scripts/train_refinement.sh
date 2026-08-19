#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:?usage: train_refinement.sh TARGET_DIR}"
export FLA_TILELANG=0
export PYTHONPATH="$root/src:$root/vendor/harness_distill-0.1.0-py3-none-any.whl${PYTHONPATH:+:$PYTHONPATH}"
python_bin="${CAVEAT_27B_PYTHON:-python3}"
[[ -f "$target/post_sft_receipt.json" ]] || {
  echo "post-SFT selection/merge/refinement prep receipt is absent" >&2
  exit 1
}
receipt="$target/refinement/training_receipt.json"
final_adapter="$target/refinement/config/prime_output/weights/step_20/lora_adapters"
if [[ ! -f "$receipt" ]]; then
  if [[ ! -f "$final_adapter/adapter_config.json" ]]; then
    sft @ "$target/refinement/config/refinement.toml"
  fi
  "$python_bin" -m caveat_27b.cli write-receipt \
    --campaign "$root/configs/campaign.yaml" --kind refinement --target "$target"
fi
"$python_bin" -m caveat_27b.cli finalize-refinement \
  --campaign "$root/configs/campaign.yaml" --campaign-root "$target" --device cuda:0
