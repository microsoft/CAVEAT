#!/bin/bash
# Run pilot9 to completion, then iteratively delete CDP-crashed cells + resume (re-run only those)
# until crash-free. A browser CDP crash is an INFRA failure (not an agent outcome), so re-running the
# cell is infra robustness — the agent's prompt/reasoning are untouched.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
cd "$ROOT"
RES=results/pilot9_v8
for i in $(seq 1 7); do
  DEL=$(.venv/bin/python scripts/_crash_sweep.py "$RES" delete)
  echo "=== selfheal iter $i: deleted $DEL crashed cells, running pilot (resume) ==="
  for p in $(seq 8900 9020); do fuser -k ${p}/tcp 2>/dev/null; done
  sleep 2
  TMPDIR=$ROOT/.tmprun AGENTARENA_CELL_TIMEOUT=1500 .venv/bin/python scripts/pilot9.py \
    nike instacart ebay etsy fiverr stockx doordash zillow airbnb \
    --repeats 3 --jobs 10 --models "gpt-5.5#high,gpt-4.1" \
    --variants thresholded,graded,graded4 --conditions clean,steered \
    --results "$RES" --base-port 8900 --max-steps 40
  REM=$(.venv/bin/python scripts/_crash_sweep.py "$RES" count)
  echo "=== selfheal iter $i complete: $REM crashed cells remain ==="
  if [ "$REM" = "0" ]; then echo "CRASH-FREE after iter $i"; break; fi
done
echo "SELFHEAL COMPLETE"
