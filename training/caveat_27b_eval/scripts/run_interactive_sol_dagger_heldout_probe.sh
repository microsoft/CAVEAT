#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

(( $# == 7 )) || fail "expected endpoint path/file/body, heldout path/file/body, output root"
source_root="$(readlink -f -- "$(dirname -- "${BASH_SOURCE[0]}")/..")"
python="${CAVEAT_27B_PYTHON:-$source_root/.venv/bin/python}"
[[ -x "$python" ]] || fail "set CAVEAT_27B_PYTHON to the evaluator Python"
[[ "$2" =~ ^[0-9a-f]{64}$ && "$3" =~ ^[0-9a-f]{64}$ \
  && "$5" =~ ^[0-9a-f]{64}$ && "$6" =~ ^[0-9a-f]{64}$ ]] \
  || fail "endpoint/heldout hashes must be lowercase SHA-256"
[[ ! -e "$7" && ! -L "$7" ]] || fail "next-action output root must be fresh"

exec env PYTHONPATH="$source_root/src" PYTHONDONTWRITEBYTECODE=1 \
  "$python" -m caveat_27b_eval.interactive_sol_dagger_heldout_probe run \
  --endpoint-receipt "$1" \
  --expected-endpoint-file-sha256 "$2" \
  --expected-endpoint-body-sha256 "$3" \
  --heldout-manifest "$4" \
  --expected-heldout-file-sha256 "$5" \
  --expected-heldout-body-sha256 "$6" \
  --output-root "$7"
