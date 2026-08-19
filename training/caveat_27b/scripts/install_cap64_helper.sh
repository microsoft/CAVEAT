#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
shared="$(cd "$root/../.." && pwd)/cluster/b200/b200"
private="${CAVEAT_27B_CAP64_HELPER:-${HOME}/.local/lib/caveat-27b-cap64/b200}"

PYTHONPATH="$root/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m caveat_27b.cli \
  cap64-helper --campaign "$root/configs/campaign.yaml" \
  --source "$shared" --destination "$private"
