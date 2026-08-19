#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

(( $# == 13 )) || fail "expected self hash plus twelve receipt/source/runtime arguments"
self_sha="$1"
shift
self="$(readlink -f -- "${BASH_SOURCE[0]}")"
source_root="$(readlink -f -- "$(dirname -- "$self")/..")"
module="$source_root/src/caveat_27b_eval/interactive_sol_dagger_candidate_serve.py"
trainer_source="$(readlink -f -- "${7}")"

[[ "$self_sha" =~ ^[0-9a-f]{64}$ \
  && "$(sha256sum -- "$self" | cut -d' ' -f1)" == "$self_sha" \
  && "$self" == "$source_root/scripts/run_interactive_sol_dagger_candidate_serve.sh" \
  && -f "$module" && ! -L "$module" ]] \
  || fail "staged interactive candidate serve source identity changed"
[[ "$source_root" == /data/runs/t-yuxuanli/* \
  || "$source_root" == /data/caveat-27b/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30/* ]] \
  || fail "candidate source escaped immutable data roots"
[[ "$trainer_source" == /data/runs/t-yuxuanli/caveat-27b-c2-action-weighted-ce-w1-20260814/source_distill_900dba58 \
  && -d "$trainer_source" && ! -L "$trainer_source" \
  && ! -e "$trainer_source/.git" ]] \
  || fail "trainer source is not a VCS-free staged archive"
[[ "${8}" =~ ^[0-9a-f]{40}$ && "${9}" =~ ^[0-9a-f]{64}$ ]] \
  || fail "trainer source identity is malformed"
[[ "${8}" == 900dba5898ccbc4775b17ff6cad873a0b3c30c0c \
  && "${9}" == f68ce98efe9bbe107441293139b2f6747bff3b18c55796e74a674af0053d2474 \
  && "${10}" == caveat-27b-c2-candidate-serve-w1 \
  && "${11}" == caveat-27b-c2-candidate-serve-w1-master-0 \
  && "${POD_NAME:-${HOSTNAME:-}}" == "${11}" \
  && "${HOSTNAME:-}" == "${11}" \
  && "${POD_UID:-}" == "${12}" \
  && "${POD_UID:-}" == 3af8b6ee-8e58-4606-a987-580ff6d28bdb ]] \
  || fail "candidate serve pod identity changed"

prime_root=/opt/prime-rl
runtime_python="$prime_root/.venv/bin/python"
[[ -x "$runtime_python" \
  && "$($runtime_python -c 'import torch; print(torch.cuda.device_count())')" == 4 ]] \
  || fail "candidate serve requires exactly four visible GPUs"

trainer_pythonpath="$trainer_source/src:$prime_root/packages/prime-rl-configs/src:$prime_root/src:$prime_root/deps/renderers"
env PYTHONPATH="$source_root/src" PYTHONDONTWRITEBYTECODE=1 \
  "$runtime_python" -m caveat_27b_eval.interactive_sol_dagger_candidate_serve \
  audit-trainer-source --root "$trainer_source" --git-sha "${8}" --tree-sha256 "${9}" \
  >/dev/null
for patcher in \
  apply_prime_rl_cross_stage_resume_patch.py \
  apply_prime_grouped_mm_contiguous_grad_patch.py \
  apply_prime_single_run_lora_resume_order_patch.py; do
  env PYTHONPATH="$trainer_pythonpath" PYTHONDONTWRITEBYTECODE=1 FLA_TILELANG=0 \
    "$runtime_python" "$trainer_source/scripts/$patcher" "$prime_root"
done
env PYTHONPATH="$trainer_pythonpath" PYTHONDONTWRITEBYTECODE=1 FLA_TILELANG=0 \
  "$runtime_python" "$trainer_source/scripts/prepare_action_weighted_ce_training.py" \
  validate-receipt --receipt "${1}" >/dev/null

printf '%s validated weighted-CE direct-LoRA lineage\n' "$(date -u +%FT%TZ)"
exec env PYTHONPATH="$source_root/src" PYTHONDONTWRITEBYTECODE=1 FLA_TILELANG=0 \
  "$runtime_python" -m caveat_27b_eval.interactive_sol_dagger_candidate_serve \
  run-server "$@"
