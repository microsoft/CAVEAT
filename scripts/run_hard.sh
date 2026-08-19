#!/bin/bash
# Exact real gpt-5.6-sol-high campaign for the certified CAVEAT truthful-hard tier.
#
# Usage:
#   scripts/run_hard.sh prepare [campaign-dir] [cert-report]
#   scripts/run_hard.sh launch  [campaign-dir]
#   scripts/run_hard.sh report  [campaign-dir]
#   scripts/run_hard.sh refill  [campaign-dir]
#   scripts/run_hard.sh verify  [campaign-dir]
#   scripts/run_hard.sh self-test
#
# ``prepare`` consumes a full passing cert and freezes inputs; it never generates
# benchmark artifacts. ``launch`` is therefore impossible before certification.
# Interrupts wait for active runs. No mode mass-kills or silently relaunches work.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$ROOT"
FREEZER="$SCRIPT_DIR/freeze_hard_campaign.py"
REPORTER="$SCRIPT_DIR/report_hard_campaign.py"
OPS="$SCRIPT_DIR/hard_campaign_ops.py"
PY=${CAVEAT_PYTHON:-.venv/bin/python}

MODE=${1:-}
CAMPAIGN_INPUT=${2:-results/hard_sol_high_n2}
CERT_INPUT=${3:-results/hard_certification/certification_report.json}

usage () {
  echo "usage: $0 prepare [campaign-dir] [cert-report]" >&2
  echo "       $0 launch|report|refill|verify [campaign-dir]" >&2
  echo "       $0 self-test" >&2
}

if [ "$MODE" = "self-test" ]; then
  "$PY" -m py_compile "$FREEZER" "$REPORTER" "$OPS"
  bash -n "$0"
  echo "SELF-TEST PASS: CAVEAT truthful-hard campaign"
  exit 0
fi
if [ "$MODE" != "prepare" ] && [ "$MODE" != "launch" ] \
   && [ "$MODE" != "report" ] && [ "$MODE" != "refill" ] \
   && [ "$MODE" != "verify" ]; then
  usage
  exit 2
fi

CAMPAIGN=$("$PY" - "$CAMPAIGN_INPUT" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).resolve())
PY
)

band_busy () {
  local base=$1
  ss -H -ltn | awk -v lo="$base" -v hi="$((base + 99))" '
    {
      addr=$4
      sub(/^.*:/, "", addr)
      if (addr ~ /^[0-9]+$/ && addr+0 >= lo && addr+0 <= hi) {
        found=1
      }
    }
    END { exit found ? 0 : 1 }
  '
}

validate_band () {
  local base=$1
  if ! [[ "$base" =~ ^[0-9]+$ ]] || [ "$base" -le 0 ] \
     || [ "$((base + 99))" -gt 65535 ]; then
    echo "ABORT: invalid 100-port band base $base" >&2
    return 1
  fi
  if [ "$base" -le 13299 ] && [ "$((base + 99))" -ge 13200 ]; then
    echo "ABORT: band $base-$((base + 99)) intersects protected Qwen 132xx" >&2
    return 1
  fi
  if band_busy "$base"; then
    echo "ABORT: band $base-$((base + 99)) has a listening socket" >&2
    return 1
  fi
}

select_band () {
  if [ -n "${HARD_BASE_PORT:-}" ]; then
    validate_band "$HARD_BASE_PORT"
    echo "$HARD_BASE_PORT"
    return
  fi
  local candidate
  for candidate in \
    15000 16000 17000 18000 19000 20000 21000 22000 \
    23000 24000 25000 26000 27000 29000 30000 31000; do
    if ! band_busy "$candidate"; then
      echo "$candidate"
      return
    fi
  done
  echo "ABORT: no free non-132xx 100-port band found" >&2
  return 1
}

manifest_value () {
  local key=$1
  "$PY" - "$CAMPAIGN/campaign_manifest.json" "$key" <<'PY'
import json
import sys
value = json.load(open(sys.argv[1]))
for key in sys.argv[2].split("."):
    value = value[key]
if isinstance(value, (dict, list)):
    print(json.dumps(value, separators=(",", ":")))
else:
    print(value)
PY
}

apply_frozen_runtime_env () {
  local rows=()
  mapfile -t rows < <(
    "$PY" "$FREEZER" runtime-env \
      --campaign-dir "$CAMPAIGN" --format tsv
  )
  local row action key value current
  for row in "${rows[@]}"; do
    IFS=$'\t' read -r action key value <<<"$row"
    case "$action" in
      unset-prefix)
        while IFS= read -r current; do
          if [[ "$current" == "$key"* ]]; then
            unset "$current"
          fi
        done < <(compgen -e || true)
        ;;
      unset)
        unset "$key"
        ;;
      set)
        export "$key=$value"
        ;;
      *)
        echo "ABORT: malformed frozen runtime environment row: $row" >&2
        return 1
        ;;
    esac
  done
}

prepare_campaign () {
  local cert
  cert=$("$PY" - "$CERT_INPUT" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).resolve())
PY
)
  local base
  base=$(select_band)
  mkdir -p "$CAMPAIGN"
  "$PY" "$FREEZER" prepare \
    --campaign-dir "$CAMPAIGN" \
    --campaign-id "$(basename "$CAMPAIGN")" \
    --base-port "$base" \
    --cert-report "$cert"
}

fresh_probe_label () {
  local base=$1
  local label=$base
  if [ -e "$CAMPAIGN/probes/checkpoint_${label}.json" ]; then
    label="${base}_resume_$(date -u +%Y%m%dT%H%M%S)_${BASHPID}"
  fi
  echo "$label"
}

LAST_PROBE_LABEL=
probe_regions () {
  local requested_label=$1
  shift
  local label
  label=$(fresh_probe_label "$requested_label")
  apply_frozen_runtime_env
  unset TRAPI_REGIONS_OVERRIDE
  unset SOL_PROBE_HEALTHY_S SOL_PROBE_TIMEOUT_S
  mkdir -p "$CAMPAIGN/probes"
  local attempt="${label}_attempt_$(date -u +%Y%m%dT%H%M%S)_${BASHPID}"
  local small="$CAMPAIGN/probes/small_${attempt}.log"
  local large="$CAMPAIGN/probes/large_${attempt}.log"
  local concurrency="$CAMPAIGN/probes/concurrency_${attempt}.log"
  if [ -e "$small" ] || [ -e "$large" ] || [ -e "$concurrency" ]; then
    echo "ABORT: refusing to replace probe logs for $attempt" >&2
    return 1
  fi
  echo "=== TRAPI availability probe $label $(date -u +%FT%TZ) ==="
  "$PY" scripts/probe_regions.py gpt-5.6-sol >"$small" 2>&1
  cat "$small"
  "$PY" scripts/probe_sol_large.py | tee "$large"
  "$PY" scripts/probe_concurrency.py gpt-5.6-sol | tee "$concurrency"
  "$PY" "$OPS" publish-probe \
    --campaign-dir "$CAMPAIGN" \
    --label "$label" \
    "$@" \
    --small "$small" \
    --large "$large" \
    --concurrency "$concurrency"
  LAST_PROBE_LABEL=$label
  echo "probe gate PASS: $label"
}

schedule_rows () {
  "$PY" "$FREEZER" schedule --campaign-dir "$CAMPAIGN" "$@"
}

run_rows () {
  local probe_label=$1
  local expected_count=$2
  shift 2
  local rows=()
  mapfile -t rows < <(schedule_rows "$@" --format tsv)
  if [ "${#rows[@]}" -ne "$expected_count" ]; then
    echo "ABORT: schedule selection has ${#rows[@]} runs, expected $expected_count" >&2
    return 1
  fi
  local base
  base=$(manifest_value base_port)
  validate_band "$base"
  "$PY" "$FREEZER" verify --campaign-dir "$CAMPAIGN"

  local pids=()
  local run_ids=()
  local expected_summaries=()
  local launched=0
  local row
  for row in "${rows[@]}"; do
    local run_id run_name scenario condition port primary region_json
    local experiment_rel summary_rel launcher_rel
    IFS=$'\t' read -r \
      run_id run_name scenario condition port primary region_json \
      experiment_rel summary_rel launcher_rel <<<"$row"
    local summary="$CAMPAIGN/$summary_rel"
    local experiment="$CAMPAIGN/$experiment_rel"
    local receipt="$CAMPAIGN/launch_receipts/${run_id}.json"
    local launcher="$CAMPAIGN/$launcher_rel"
    expected_summaries+=("$summary")
    if [ -f "$summary" ]; then
      echo "already complete, preserving run: $run_id"
      continue
    fi
    if [ -e "$receipt" ] || [ -e "$experiment" ] || [ -e "$launcher" ]; then
      echo "ABORT: partial evidence exists for $run_id; screen and use refill" >&2
      return 1
    fi

    apply_frozen_runtime_env
    export TRAPI_REGIONS_OVERRIDE="$region_json"
    mkdir -p "$CAMPAIGN/launcher_logs" "$CAMPAIGN/launch_receipts"
    "$PY" "$OPS" write-receipt \
      --campaign-dir "$CAMPAIGN" \
      --run-id "$run_id" \
      --probe-checkpoint "$probe_label"
    local max_steps
    max_steps=$(manifest_value caps.max_steps)
    echo "launch run=$run_id primary=$primary port=$port attempt receipt-created"
    (
      exec "$PY" -m caveat.benchmark.run \
        --name "$run_name" \
        --scenarios "$scenario" \
        --conditions "$condition" \
        --variants graded \
        --scaffolds browseruse \
        --models 'gpt-5.6-sol#high' \
        --max-steps "$max_steps" \
        --repeats 1 \
        --jobs 1 \
        --results "$CAMPAIGN/runs" \
        --base-port "$port"
    ) >"$launcher" 2>&1 &
    pids+=("$!")
    run_ids+=("$run_id")
    launched=$((launched + 1))
    if [ "$launched" -lt "$expected_count" ]; then
      sleep 10
    fi
  done
  unset TRAPI_REGIONS_OVERRIDE

  # Never kill active runs. An operator interrupt becomes "wait, then stop".
  local interrupted=0
  trap 'echo "interrupt received: waiting for active runs; none will be killed"; interrupted=1; wait' INT TERM
  local failed=0
  local index
  for index in "${!pids[@]}"; do
    if ! wait "${pids[$index]}"; then
      echo "run process failed: ${run_ids[$index]}" >&2
      failed=1
    fi
  done
  trap - INT TERM
  if [ "$interrupted" -ne 0 ]; then
    echo "ABORT: interrupt honored after active runs completed" >&2
    return 1
  fi
  if [ "$failed" -ne 0 ]; then
    echo "ABORT: one or more run processes failed; no run was relaunched" >&2
    return 1
  fi
  local summary
  for summary in "${expected_summaries[@]}"; do
    if [ ! -f "$summary" ]; then
      echo "ABORT: expected summary missing after launch: $summary" >&2
      return 1
    fi
  done
  "$PY" "$FREEZER" verify --campaign-dir "$CAMPAIGN"
}

rescore_exact_runs () {
  "$PY" "$FREEZER" verify --campaign-dir "$CAMPAIGN"
  apply_frozen_runtime_env
  local experiment
  while IFS= read -r experiment; do
    if find "$CAMPAIGN/$experiment" -name summary.json -type f -print -quit \
      2>/dev/null | grep -q .; then
      "$PY" -m caveat.scoring.rescore \
        --glob "$CAMPAIGN/$experiment" --strict
    fi
  done < <(
    "$PY" - "$CAMPAIGN/campaign_manifest.json" <<'PY'
import json
import sys
manifest = json.load(open(sys.argv[1]))
for path in dict.fromkeys(row["experiment_relpath"] for row in manifest["schedule"]):
    print(path)
PY
  )
  "$PY" "$FREEZER" verify --campaign-dir "$CAMPAIGN"
}

emit_report () {
  rescore_exact_runs
  set +e
  "$PY" "$REPORTER" "$CAMPAIGN" --json "$CAMPAIGN/report.json"
  local code=$?
  set -e
  return "$code"
}

launch_campaign () {
  "$PY" "$FREEZER" verify --campaign-dir "$CAMPAIGN"
  local base
  base=$(manifest_value base_port)
  validate_band "$base"

  probe_regions before_block1 --block 1
  local block1_probe=$LAST_PROBE_LABEL
  if ! run_rows "$block1_probe" 5 --block 1; then
    emit_report || true
    return 1
  fi

  # Mandatory mid-campaign availability re-probe after block 1 has finished.
  probe_regions mid_before_block2 --block 2
  local block2_probe=$LAST_PROBE_LABEL
  if ! run_rows "$block2_probe" 5 --block 2; then
    emit_report || true
    return 1
  fi

  emit_report
}

refill_campaign () {
  "$PY" "$FREEZER" verify --campaign-dir "$CAMPAIGN"
  emit_report || true
  local refillable
  refillable=$("$PY" - "$CAMPAIGN/report.json" <<'PY'
import json
import sys
report = json.load(open(sys.argv[1]))
print("1" if report.get("validity", {}).get("refillable") else "0")
PY
  )
  if [ "$refillable" != "1" ]; then
    echo "ABORT: report is not refillable (behavioral FAIL is never rerun)" >&2
    return 1
  fi
  local run_ids=()
  mapfile -t run_ids < <(
    "$PY" - "$CAMPAIGN/report.json" <<'PY'
import json
import sys
for run_id in json.load(open(sys.argv[1]))["validity"]["refill_run_ids"]:
    print(run_id)
PY
  )
  if [ "${#run_ids[@]}" -eq 0 ]; then
    echo "ABORT: refill report contains no run ids" >&2
    return 1
  fi
  "$PY" "$OPS" archive-refills \
    --campaign-dir "$CAMPAIGN" \
    --report "$CAMPAIGN/report.json"

  local probe_args=()
  local schedule_args=()
  local run_id
  for run_id in "${run_ids[@]}"; do
    probe_args+=(--run-id "$run_id")
    schedule_args+=(--run-id "$run_id")
  done
  probe_regions "refill_$(date -u +%Y%m%dT%H%M%S)" "${probe_args[@]}"
  local refill_probe=$LAST_PROBE_LABEL
  run_rows "$refill_probe" "${#run_ids[@]}" "${schedule_args[@]}"
  emit_report
}

case "$MODE" in
  prepare)
    prepare_campaign
    ;;
  launch)
    launch_campaign
    ;;
  report)
    emit_report
    ;;
  refill)
    refill_campaign
    ;;
  verify)
    "$PY" "$FREEZER" verify --campaign-dir "$CAMPAIGN"
    ;;
esac
