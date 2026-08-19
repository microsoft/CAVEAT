#!/bin/bash
# Adversarial agent-targeted steering taxonomy: 16 adversarial conditions + clean, at the
# fully-absolute (`thresholded`) preference, gpt-5.5-low + browser-use, scored with vgeo.
#
#   scripts/run_advtax.sh pilot     # laptop only, 4 reps  (17 cells/rep -> 68 cells)
#   scripts/run_advtax.sh full      # 5 scenarios, 10 reps (85 cells/rep -> 850 cells)
#
# Ports are allocated PER CELL as base+i over one repeat's cell list, so concurrent runners must
# have base ports at least (cells-per-repeat) apart or free_port() kills a sibling's live server
# mid-cell (this silently corrupted 38 cells in a past run). Full mode has 85 cells/rep -> 200 apart.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
cd "$ROOT"
MODE="${1:-pilot}"
CONDS="clean adv-hidden adv-apighost adv-subllm adv-suppress adv-metrology adv-flood \
adv-promptfmt adv-filter adv-precomputed adv-costblind adv-budget adv-principal adv-policy \
adv-consensus adv-exec adv-all"

if [ "$MODE" = "pilot" ]; then
  SCEN="laptop";                                  RES=results/advtax_pilot
  REPS=2; RUNNERS=2; JOBS="${ADV_JOBS:-6}";       SPACING=40
else
  SCEN="laptop backpack mattress office_chair tent"; RES=results/advtax_v1
  REPS="${ADV_REPS:-3}"; RUNNERS="${ADV_RUNNERS:-4}"; JOBS="${ADV_JOBS:-4}"; SPACING=200
fi

mkdir -p "$RES" .tmprun
export PYTHONDONTWRITEBYTECODE=1
find agentarena -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null   # stale .pyc has
                                                                          # invalidated a run before

run_one() {   # $1 = runner index (1..RUNNERS)
  case $(( ($1 - 1) % 3 )) in
    0) OV='{"gpt-5.5": ["msraif/shared", "gcr/shared"]}' ;;
    1) OV='{"gpt-5.5": ["gcr/shared", "redmond/interactive"]}' ;;
    *) OV='{"gpt-5.5": ["redmond/interactive", "msraif/shared"]}' ;;
  esac
  TMPDIR=$ROOT/.tmprun AGENTARENA_CELL_TIMEOUT=2400 \
  TRAPI_REGIONS_OVERRIDE="$OV" \
  .venv/bin/python -m agentarena.benchmark.run \
    --name "adv_w$1" --scenarios $SCEN --conditions $CONDS \
    --variants thresholded --scaffolds browseruse --models 'gpt-5.5#low' \
    --results "$RES" --jobs "$JOBS" --max-steps 60 --repeats "$REPS" \
    --base-port $(( 9400 + $1 * SPACING )) >> "$RES/adv_w$1.log" 2>&1
}

for iter in $(seq 1 "${ADV_ITERS:-3}"); do
  DEL=$(.venv/bin/python scripts/_crash_sweep.py "$RES" delete 2>/dev/null || echo 0)
  echo "=== advtax $MODE selfheal iter $iter: deleted $DEL infra-crashed cells (jobs=$JOBS x $RUNNERS runners) ==="
  PIDS=""
  for i in $(seq 1 "$RUNNERS"); do run_one "$i" & PIDS="$PIDS $!"; done
  wait $PIDS
  echo "=== iter $iter done: $(find "$RES" -name summary.json | wc -l) cells on disk ==="
done
echo "ALL DONE: $(find "$RES" -name summary.json | wc -l) cells"
