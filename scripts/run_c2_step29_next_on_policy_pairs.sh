#!/usr/bin/env bash
set -euo pipefail

workspace=/home/t-yuxuanli/preference-fidelity
python_bin="$workspace/.venv/bin/python"
distill_source="$workspace/training/harness_distill/src"
eval_source="$workspace/training/harness_posttrain_eval/src"
collection_root="$workspace/results/harness_posttrain_campaign2_20260814/step29_next_on_policy_buy_now_dagger_r1"
materialized_root="$workspace/results/harness_posttrain_campaign2_20260814/step29_next_on_policy_buy_now_exact16_r1"
upstream_base_url="${1:-http://127.0.0.1:18546}"

export PYTHONPATH="$distill_source:$eval_source"

set +e
"$python_bin" -m harness_distill.adaptive_buy_now_dagger_ops run \
  --bundle "$collection_root/bundle.json" \
  --upstream-base-url "$upstream_base_url" \
  --python "$python_bin" \
  --timeout-seconds 2700 \
  > "$collection_root/run.stdout" \
  2> "$collection_root/run.stderr"
set -e

"$python_bin" -m harness_distill.adaptive_buy_now_dagger_ops materialize \
  --bundle "$collection_root/bundle.json" \
  --output-root "$materialized_root" \
  --quota-per-variant 4 \
  > "$collection_root/materialize.stdout" \
  2> "$collection_root/materialize.stderr"
