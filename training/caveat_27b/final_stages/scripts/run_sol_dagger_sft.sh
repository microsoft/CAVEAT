#!/usr/bin/env bash
set -euo pipefail

source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
plan="${1:?Sol DAgger prepared plan is required}"
prime_root="${2:-/opt/prime-rl}"
executor_git_sha="${3:?executor git SHA is required}"
[[ -f "$plan" && ! -L "$plan" ]]
[[ "$prime_root" == /opt/prime-rl && -d "$prime_root/.git" && ! -L "$prime_root" ]]
[[ "$(git -C "$prime_root" rev-parse HEAD)" == d334ea52940b47f426293a7d146239e3fbf91caa ]]
[[ "$executor_git_sha" =~ ^[0-9a-f]{40}$ ]]

python_bin="$prime_root/.venv/bin/python"
[[ -x "$python_bin" ]]
[[ "$($python_bin -c 'import torch; print(torch.cuda.device_count())')" == 4 ]]
export PYTHONPATH="$source_root/src:$prime_root/src${PYTHONPATH:+:$PYTHONPATH}"

validated="$("$python_bin" "$source_root/scripts/prepare_sol_dagger_training.py" validate-plan --plan "$plan")"
config="$("$python_bin" -c 'import json,sys; print(json.load(sys.stdin)["config_path"])' <<<"$validated")"
sft_bin="$prime_root/.venv/bin/sft"
[[ -x "$sft_bin" && -f "$config" && ! -L "$config" ]]
"$sft_bin" @ "$config"
"$python_bin" "$source_root/scripts/prepare_sol_dagger_training.py" write-receipt \
  --plan "$plan" --executor-git-sha "$executor_git_sha" >/dev/null
"$python_bin" "$source_root/scripts/prepare_sol_dagger_training.py" validate-receipt \
  --receipt "$(dirname -- "$plan")/training_receipt.json"
