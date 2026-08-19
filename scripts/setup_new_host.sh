#!/usr/bin/env bash
# One-shot environment setup on a fresh host (e.g. the A100 server).
# Run from the repo root after unpacking / cloning:  bash scripts/setup_new_host.sh
#
# Rebuilds everything that is intentionally NOT shipped (see .gitignore): the Python venv,
# the Playwright Chromium browser, and the frontend node_modules if you need to rebuild a
# storefront bundle. The measurement DATA (results/) and secrets (.env) are handled separately
# — see MIGRATION.md.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
cd "$ROOT"
echo "== setup in $ROOT =="

# --- 1. Python venv + package (editable) ------------------------------------------------------
PY="${PYTHON:-python3}"
if [ ! -d .venv ]; then
  echo "== creating .venv with $PY ($($PY --version 2>&1)) =="
  "$PY" -m venv .venv
fi
./.venv/bin/python -m pip install -U pip wheel >/dev/null
echo "== installing agentarena (editable) + browser-use + plotting =="
# core + the browseruse scaffold extra; matplotlib is only needed for the figure scripts.
./.venv/bin/pip install -e '.[browseruse]'
./.venv/bin/pip install matplotlib >/dev/null
# websurfer / magentic-one (computer-use) scaffolds are optional and pin-heavy; install on demand:
#   ./.venv/bin/pip install -e '.[websurfer]'

# --- 2. Chromium for browser-use / playwright -------------------------------------------------
# The scaffold auto-discovers Chromium under ~/.cache/ms-playwright (find_chromium in
# agentarena/scaffolds/_browser.py), so a plain playwright install is enough. --with-deps pulls
# the system libraries headless Chromium needs (may need sudo; drop it if already present).
echo "== installing Playwright Chromium =="
./.venv/bin/python -m playwright install chromium || true
./.venv/bin/python -m playwright install-deps chromium 2>/dev/null || \
  echo "   (playwright install-deps needs root; if browser launch fails, run it with sudo)"

# --- 3. .tmprun off any small tmpfs -----------------------------------------------------------
# browser-use leaks ~130MB of Chromium profile per cell; keep it on the big disk, not /tmp.
mkdir -p "$ROOT/.tmprun"

# --- 4. Sanity checks -------------------------------------------------------------------------
echo "== sanity =="
./.venv/bin/python - <<'PY'
import importlib, shutil, os, sys
ok = True
try:
    import agentarena; print("  agentarena import      OK", os.path.dirname(agentarena.__file__))
except Exception as e: ok = False; print("  agentarena import      FAIL", e)
from agentarena.benchmark import scenarios as S
print("  scenarios              ", sorted(S.SCENARIOS))
try:
    from agentarena.scaffolds._browser import find_chromium
    print("  chromium               ", find_chromium() or "NOT FOUND — run `playwright install chromium`")
except Exception as e: print("  chromium check         skipped:", e)
print("  az CLI                 ", shutil.which("az") or "NOT on PATH (needed for TRAPI: `az login`)")
print("  .env                   ", "present" if os.path.exists(".env") else "MISSING (create it — see MIGRATION.md)")
sys.exit(0 if ok else 1)
PY

cat <<'EOF'

== next steps ==
  1. Model auth:
       TRAPI  -> `az login`   (bearer token minted per request; no key stored)
       PhyAGI -> create .env with:  PHYAGI_API_KEY=<key>
  2. (optional) copy the existing results/ tree over if you want the viewer/figures to show
     past runs — it is gitignored and not in the bundle. See MIGRATION.md.
  3. Smoke test one cell:
       PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m agentarena.benchmark.run \
         --name smoke --scenarios laptop --conditions clean --variants thresholded \
         --scaffolds browseruse --models 'gpt-5.5#low' --jobs 1 --max-steps 40 --repeats 1
  4. Scale up: with more CPUs/RAM, raise the per-runner --jobs and the auto_jobs cap
     (agentarena/core/experiment.py:48, currently min(cpu, mem/1.5, 24)); API concurrency is
     still bounded by ~32/region/model on TRAPI, so spread runners across regions.
EOF
echo "== done =="
