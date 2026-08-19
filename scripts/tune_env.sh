#!/usr/bin/env bash
# Per-env relentless measurement: one env, both models, all 5 levels, clean+steered, on HEALTHY routing.
#   bash scripts/tune_env.sh <env> [reps] [jobs]
# Results -> results/byenv/<env>_*. Then: PILOT_RESULTS=results/byenv python scripts/pilot_report5.py <env>
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
cd "$ROOT"
SCR=/tmp/claude-1000/-home-t-yuxuanli-agent-arena/70db66b6-d604-42ab-b86c-43f576b04c7d/scratchpad
ENV="${1:?usage: tune_env.sh <env> [reps] [jobs]}"
REPS="${2:-2}"
JOBS="${3:-8}"
export TMPDIR=$ROOT/.tmprun
mkdir -p .tmprun results/byenv
# always probe live routing (never hard-code a region — gcr flaps 503)
export TRAPI_REGIONS_OVERRIDE="$(.venv/bin/python scripts/probe_regions.py gpt-5.5 gpt-4.1 2>$SCR/probe_$ENV.err)"
echo "[$ENV] OVERRIDE=$TRAPI_REGIONS_OVERRIDE"
.venv/bin/python scripts/pilot9.py "$ENV" \
  --repeats "$REPS" --jobs "$JOBS" --models "gpt-5.5#high,gpt-4.1" \
  --variants "thresholded,mixed,graded,graded3,graded4" --conditions "clean,steered" \
  --base-port 8940 --results results/byenv
echo "[$ENV] DONE"
