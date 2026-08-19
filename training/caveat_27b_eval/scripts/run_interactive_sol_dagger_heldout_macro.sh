#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 2; }

(( $# == 2 )) || fail "expected rendered macro root and repository working directory"
macro_root="$(readlink -f -- "$1")"
working_directory="$(readlink -f -- "$2")"
source_root="$(readlink -f -- "$(dirname -- "${BASH_SOURCE[0]}")/..")"
python="${CAVEAT_27B_PYTHON:-$source_root/.venv/bin/python}"
launch="$macro_root/launch_manifest.json"
[[ -x "$python" && -f "$launch" && -d "$working_directory" ]] \
  || fail "macro Python, launch manifest, or working directory is absent"

env PYTHONPATH="$source_root/src" PYTHONDONTWRITEBYTECODE=1 \
  "$python" -m caveat_27b_eval.interactive_sol_dagger_heldout_macro \
  audit-launch --path "$launch" >/dev/null
exec env PYTHONPATH="$source_root/src" PYTHONDONTWRITEBYTECODE=1 \
  "$python" -m caveat_27b_eval.cli run-bundle \
  --launch-manifest "$launch" \
  --state-dir "$macro_root/executor_state" \
  --working-directory "$working_directory" \
  --jobs 4
