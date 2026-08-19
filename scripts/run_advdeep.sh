#!/bin/bash
# "-deep" arm: the four verification-attacking families, but with every conforming item sunk to
# page 3, plus a burial-only control. Tests whether those families measured no effect only because
# the agent never had to search or verify (0 paginations / 0 API calls in 306 recorded runs).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
cd "$ROOT"
RES=results/advdeep_v1
CONDS="adv-clean-deep adv-apighost-deep adv-subllm-deep adv-suppress-deep adv-budget-deep"
mkdir -p "$RES" .tmprun
export PYTHONDONTWRITEBYTECODE=1
find agentarena -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null
run_one() {   # 25 cells/repeat -> 100-apart bases are disjoint
  case $(( ($1 - 1) % 2 )) in
    0) OV='{"gpt-5.5": ["msraif/shared", "gcr/shared"]}' ;;
    *) OV='{"gpt-5.5": ["gcr/shared", "redmond/interactive"]}' ;;
  esac
  TMPDIR=$ROOT/.tmprun AGENTARENA_CELL_TIMEOUT=2400 \
  TRAPI_REGIONS_OVERRIDE="$OV" \
  .venv/bin/python -m agentarena.benchmark.run \
    --name "deep_w$1" --scenarios laptop backpack mattress office_chair tent \
    --conditions $CONDS --variants thresholded --scaffolds browseruse \
    --models 'gpt-5.5#low' --results "$RES" --jobs 4 --max-steps 60 --repeats 3 \
    --base-port $(( 9600 + $1 * 100 )) >> "$RES/deep_w$1.log" 2>&1
}
for iter in 1 2; do
  .venv/bin/python scripts/_crash_sweep.py "$RES" delete >/dev/null 2>&1
  P=""; for i in 1 2 3 4; do run_one "$i" & P="$P $!"; done; wait $P
  echo "iter $iter done: $(find "$RES" -name summary.json | wc -l) cells"
done
