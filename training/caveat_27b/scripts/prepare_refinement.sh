#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:?usage: prepare_refinement.sh TARGET_DIR}"
export FLA_TILELANG=0
export PYTHONPATH="$root/src:$root/vendor/harness_distill-0.1.0-py3-none-any.whl${PYTHONPATH:+:$PYTHONPATH}"
python_bin="${CAVEAT_27B_PYTHON:-python3}"
"$python_bin" -m caveat_27b.cli prepare-refinement \
  --campaign "$root/configs/campaign.yaml" --campaign-root "$target" \
  --smoke-model-config "$root/configs/smoke_model.yaml" \
  --prime-root "${CAVEAT_27B_PRIME_ROOT:-/opt/prime-rl}" --selection-concurrency 32 \
  --num-gpus "${CAVEAT_27B_POST_GPUS:-8}"
