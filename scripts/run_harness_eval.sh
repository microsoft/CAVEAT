#!/bin/bash
# Frozen A/B evaluation for browseruse vs browseruse-deliberative.
#
# Usage:
#   scripts/run_harness_eval.sh prepare <campaign-dir> <cert-report> \
#       <lockdiff-after-report> <weak-regions> <sol-regions> \
#       [--superseded-campaign-dir <campaign-dir>]
#   scripts/run_harness_eval.sh launch  [campaign-dir]
#   scripts/run_harness_eval.sh report  [campaign-dir]
#   scripts/run_harness_eval.sh refill  [campaign-dir]
#   scripts/run_harness_eval.sh verify  [campaign-dir]
#   scripts/run_harness_eval.sh launch-smoke [campaign-dir] \
#       <weak_easy|sol_high_hard> <results-root> <port>
#   scripts/run_harness_eval.sh publish-smoke-gate [campaign-dir] \
#       <weak-run-dir> <sol-run-dir>
#   scripts/run_harness_eval.sh self-test
#
# weak-regions and sol-regions are independent ordered comma-separated subsets
# selected by fresh probes, for example:
# gcr/shared,msraif/shared,redmond/interactive.
# Set HARNESS_EVAL_BASE_PORT to override the base port explicitly; otherwise
# prepare asks the campaign tool to select a currently free band.
#
# Launches and numbered report snapshots are create-only and resumable.  An
# interrupt waits for active runs; this script never mass-kills, overwrites a
# prior report, or silently relaunches an attempt.
set -euo pipefail

ROOT=/home/t-yuxuanli/preference-fidelity
cd "$ROOT"

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
CAMPAIGN_TOOL="$SCRIPT_DIR/harness_eval_campaign.py"
REPORTER="$SCRIPT_DIR/report_harness_eval.py"
PY=.venv/bin/python

MODE=${1:-}
CAMPAIGN_INPUT=${2:-results/harness_deliberative_ab}
CERT_INPUT=${3:-}
LOCKDIFF_INPUT=${4:-}
WEAK_REGIONS_INPUT=${5:-}
SOL_REGIONS_INPUT=${6:-}
SUPERSEDED_CAMPAIGN_INPUT=

usage () {
  echo "usage: $0 prepare <campaign-dir> <cert-report> <lockdiff-after-report> <weak-regions> <sol-regions> [--superseded-campaign-dir <campaign-dir>]" >&2
  echo "       $0 launch|report|refill|verify [campaign-dir]" >&2
  echo "       $0 launch-smoke [campaign-dir] <weak_easy|sol_high_hard> <results-root> <port>" >&2
  echo "       $0 publish-smoke-gate [campaign-dir] <weak-run-dir> <sol-run-dir>" >&2
  echo "       $0 self-test" >&2
  echo "       weak-regions, sol-regions: independent ordered comma-separated fresh-probe routes" >&2
  echo "       base port: set HARNESS_EVAL_BASE_PORT, or omit it to select a free band" >&2
}

if [ "$MODE" = "self-test" ]; then
  "$PY" -m py_compile "$CAMPAIGN_TOOL" "$REPORTER"
  "$PY" -m pytest "$SCRIPT_DIR/test_harness_eval_campaign.py" -q
  bash -n "$0"
  echo "SELF-TEST PASS: deliberative A/B campaign harness"
  exit 0
fi

case "$MODE" in
  prepare|launch|report|refill|verify|launch-smoke|publish-smoke-gate) ;;
  *)
    usage
    exit 2
    ;;
esac

if [ "$MODE" = "prepare" ]; then
  if [ "$#" -ne 6 ] && [ "$#" -ne 8 ]; then
    echo "ABORT: prepare requires explicit campaign, certification, lockdiff, and fresh weak/sol model-region inputs; an abandoned predecessor is optional" >&2
    usage
    exit 2
  fi
  if [ -z "${2:-}" ]; then
    echo "ABORT: prepare campaign-dir must not be empty" >&2
    exit 2
  fi
  if [ -z "$CERT_INPUT" ]; then
    echo "ABORT: prepare cert-report must be provided explicitly" >&2
    exit 2
  fi
  if [ -z "$LOCKDIFF_INPUT" ]; then
    echo "ABORT: prepare lockdiff-after-report must be provided explicitly" >&2
    exit 2
  fi
  if [ -z "$WEAK_REGIONS_INPUT" ]; then
    echo "ABORT: prepare weak-regions must be provided from a fresh probe" >&2
    exit 2
  fi
  if [ -z "$SOL_REGIONS_INPUT" ]; then
    echo "ABORT: prepare sol-regions must be provided from a fresh probe" >&2
    exit 2
  fi
  if [ "$#" -eq 8 ]; then
    if [ "${7:-}" != "--superseded-campaign-dir" ] || [ -z "${8:-}" ]; then
      echo "ABORT: optional predecessor must be written as --superseded-campaign-dir <campaign-dir>" >&2
      exit 2
    fi
    SUPERSEDED_CAMPAIGN_INPUT=$8
  fi
fi

CAMPAIGN=$("$PY" - "$CAMPAIGN_INPUT" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).resolve())
PY
)

prepare_campaign () {
  local cert lockdiff base superseded
  local -a prepare_args
  if [ ! -f "$CERT_INPUT" ]; then
    echo "ABORT: prepare cert-report is not a file: $CERT_INPUT" >&2
    return 2
  fi
  if [ ! -f "$LOCKDIFF_INPUT" ]; then
    echo "ABORT: prepare lockdiff-after-report is not a file: $LOCKDIFF_INPUT" >&2
    return 2
  fi
  cert=$("$PY" - "$CERT_INPUT" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).resolve())
PY
)
  lockdiff=$("$PY" - "$LOCKDIFF_INPUT" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).resolve())
PY
)
  if [ -n "${HARNESS_EVAL_BASE_PORT:-}" ]; then
    base=$HARNESS_EVAL_BASE_PORT
  else
    base=$("$PY" "$CAMPAIGN_TOOL" select-band)
  fi
  prepare_args=(
    --campaign-dir "$CAMPAIGN"
    --campaign-id "$(basename "$CAMPAIGN")"
    --base-port "$base"
    --cert-report "$cert"
    --lockdiff-report "$lockdiff"
    --weak-regions "$WEAK_REGIONS_INPUT"
    --sol-regions "$SOL_REGIONS_INPUT"
  )
  if [ -n "$SUPERSEDED_CAMPAIGN_INPUT" ]; then
    if [ ! -d "$SUPERSEDED_CAMPAIGN_INPUT" ]; then
      echo "ABORT: superseded-campaign-dir is not a directory: $SUPERSEDED_CAMPAIGN_INPUT" >&2
      return 2
    fi
    superseded=$("$PY" - "$SUPERSEDED_CAMPAIGN_INPUT" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).resolve())
PY
)
    prepare_args+=(--superseded-campaign-dir "$superseded")
  fi
  "$PY" "$CAMPAIGN_TOOL" prepare "${prepare_args[@]}"
}

report_campaign () {
  local allocation json_path markdown_path
  allocation=$("$PY" "$CAMPAIGN_TOOL" next-report-paths \
    --campaign-dir "$CAMPAIGN")
  json_path=$("$PY" -c \
    'import json,sys; print(json.loads(sys.argv[1])["json"])' \
    "$allocation")
  markdown_path=$("$PY" -c \
    'import json,sys; print(json.loads(sys.argv[1])["markdown"])' \
    "$allocation")
  LAST_REPORT_JSON=$json_path
  set +e
  "$PY" "$REPORTER" "$CAMPAIGN" \
    --json "$json_path" \
    --markdown "$markdown_path"
  local code=$?
  set -e
  return "$code"
}

fresh_label () {
  local stage=$1
  echo "${stage}_$(date -u +%Y%m%dT%H%M%S)_${BASHPID}"
}

pending_blocks () {
  local args=()
  local block
  for block in "$@"; do
    args+=(--block "$block")
  done
  "$PY" "$CAMPAIGN_TOOL" pending \
    --campaign-dir "$CAMPAIGN" "${args[@]}"
}

launch_phase () {
  local stage=$1
  shift
  local blocks=("$@")
  local pending
  pending=$(pending_blocks "${blocks[@]}")
  if [ "$pending" -eq 0 ]; then
    echo "$stage: all scheduled runs already complete"
    return
  fi
  local label
  label=$(fresh_label "$stage")
  "$PY" "$CAMPAIGN_TOOL" probe \
    --campaign-dir "$CAMPAIGN" --stage "$stage" --label "$label"
  local block
  for block in "${blocks[@]}"; do
    if ! "$PY" "$CAMPAIGN_TOOL" launch-block \
      --campaign-dir "$CAMPAIGN" \
      --block "$block" \
      --checkpoint "$label"; then
      report_campaign || true
      return 1
    fi
  done
}

launch_campaign () {
  "$PY" "$CAMPAIGN_TOOL" verify --campaign-dir "$CAMPAIGN"
  "$PY" "$CAMPAIGN_TOOL" verify-smoke-gate --campaign-dir "$CAMPAIGN"

  # Before and mid-campaign probes for each deployment.  Completed runs are
  # preserved, so a resumed phase probes only if it still has pending work.
  launch_phase weak_before 1 2
  launch_phase weak_mid 3 4
  launch_phase sol_before 5
  launch_phase sol_mid 6
  report_campaign
}

refill_campaign () {
  "$PY" "$CAMPAIGN_TOOL" verify --campaign-dir "$CAMPAIGN"
  report_campaign || true
  local refillable
  refillable=$("$PY" - "$LAST_REPORT_JSON" <<'PY'
import json
import sys
report = json.load(open(sys.argv[1]))
print("1" if report.get("validity", {}).get("refillable") else "0")
PY
)
  if [ "$refillable" != "1" ]; then
    echo "ABORT: no infrastructure-confounded attempt is refillable" >&2
    return 1
  fi
  "$PY" "$CAMPAIGN_TOOL" archive-refills \
    --campaign-dir "$CAMPAIGN" \
    --report "$LAST_REPORT_JSON"
  launch_campaign
}

case "$MODE" in
  prepare)
    prepare_campaign
    ;;
  launch)
    launch_campaign
    ;;
  report)
    report_campaign
    ;;
  refill)
    refill_campaign
    ;;
  verify)
    "$PY" "$CAMPAIGN_TOOL" verify --campaign-dir "$CAMPAIGN"
    ;;
  launch-smoke)
    if [ "$#" -ne 5 ]; then
      usage
      exit 2
    fi
    "$PY" "$CAMPAIGN_TOOL" launch-smoke \
      --campaign-dir "$CAMPAIGN" \
      --smoke "$3" \
      --results-root "$4" \
      --port "$5"
    ;;
  publish-smoke-gate)
    if [ "$#" -ne 4 ]; then
      usage
      exit 2
    fi
    "$PY" "$CAMPAIGN_TOOL" publish-smoke-gate \
      --campaign-dir "$CAMPAIGN" \
      --weak-run-dir "$3" \
      --sol-run-dir "$4"
    ;;
esac
