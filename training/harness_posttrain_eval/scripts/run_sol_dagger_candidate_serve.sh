#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

(( $# == 16 )) || fail "expected self hash plus fifteen lineage/runtime arguments"
self_sha="$1"
shift
self="$(readlink -f -- "${BASH_SOURCE[0]}")"
source_root="$(readlink -f -- "$(dirname -- "$self")/..")"
module="$source_root/src/harness_posttrain_eval/sol_dagger_candidate_serve.py"

[[ "$self_sha" =~ ^[0-9a-f]{64}$ \
  && "$(sha256sum -- "$self" | cut -d' ' -f1)" == "$self_sha" \
  && "$self" == "$source_root/scripts/run_sol_dagger_candidate_serve.sh" \
  && -f "$module" && ! -L "$module" ]] \
  || fail "staged Sol DAgger serve source identity changed"
[[ "$source_root" == /data/runs/t-yuxuanli/* \
  || "$source_root" == /data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/* ]] \
  || fail "staged Sol DAgger serve source escaped the immutable data roots"
[[ "${13}" =~ ^t-yuxuanli-hpt-q35-sol-dagger-candidate-serve-w[1-9][0-9]*$ \
  && "${14}" == "${13}-master-0" \
  && "${POD_NAME:-${HOSTNAME:-}}" == "${14}" \
  && "${HOSTNAME:-}" == "${14}" \
  && "${POD_UID:-}" == "${15}" \
  && "${POD_UID:-}" =~ ^[0-9a-f-]{36}$ ]] \
  || fail "candidate serve pod identity changed"

runtime_python=/opt/prime-rl/.venv/bin/python
[[ -x "$runtime_python" \
  && "$($runtime_python -c 'import torch; print(torch.cuda.device_count())')" == 4 ]] \
  || fail "candidate serve requires the pinned runtime and exactly four visible GPUs"

printf '%s validating Sol DAgger step25 direct-LoRA lineage\n' "$(date -u +%FT%TZ)"
exec env PYTHONPATH="$source_root/src" \
  "$runtime_python" -m harness_posttrain_eval.sol_dagger_candidate_serve run-server "$@"
