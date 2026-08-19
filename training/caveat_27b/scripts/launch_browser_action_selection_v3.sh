#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
helper="$HOME/.local/lib/caveat-27b-cap64/b200"
image="${CAVEAT_27B_CONTAINER_IMAGE:-aifrlenvironments.azurecr.io/t-yuxuanli/harness-distill@sha256:28f38e74e17779e9c85985d3d3f970c1f6aa4427407f1843c11f2af87a324dd0}"
target="${CAVEAT_27B_CAMPAIGN_TARGET:-/data/caveat-27b/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30}"
artifact_sha="${CAVEAT_27B_ACTION_ARTIFACT_SOURCE_SHA:-d505f9a9242328cc786a48d33b128b498731274b}"
[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || {
  echo "CAVEAT_27B_CONTAINER_IMAGE must be an immutable digest reference" >&2
  exit 2
}
[[ "$target" =~ ^/data/caveat-27b/[0-9a-f]{40}$ ]] || {
  echo "campaign target must be /data/caveat-27b/<40 lowercase hex>" >&2
  exit 2
}
[[ "$artifact_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "artifact source SHA must be 40 lowercase hexadecimal characters" >&2
  exit 2
}
[[ -z "$(git -C "$root" status --porcelain --untracked-files=all)" ]] || {
  echo "campaign source is dirty" >&2
  exit 1
}
source_sha="$(git -C "$root" rev-parse HEAD)"

"$root/scripts/install_cap64_helper.sh" >/dev/null
"$root/scripts/cap64_b200.sh" doctor >/dev/null

env \
  SOURCE_DIR="$root" USE_TORCHRUN=0 INSTALL_PACKAGE=0 UPDATE_PYTHONPATH=1 \
  VOLCANO_NAMESPACE=bonete61 VOLCANO_DATA_PVC_NAME=pvc-vast-bonete61 \
  B200_PRIORITY=p0 B200_WORKSTREAM=socialreasoning B200_MAX_GPUS=64 \
  B200_HF_SECRET=t-yuxuanli-hf-token B200_IMAGE_PULL_SECRET=t-yuxuanli-hd-acr-pull \
  CONTAINER_IMAGE_PATH="$image" MEMORY_SIZE_LIMIT=100Gi \
  NODES=1 GPUS_PER_NODE=4 NPROC_PER_NODE=1 \
  CPU_REQUESTS=32 MEMORY_REQUESTS=500Gi RDMA_REQUESTS=4 \
  B200_MANIFEST_DIR="/tmp/caveat-27b-$source_sha-action-selection-v3" \
  "$helper" submit caveat-27b-action-select-v3 \
    env CAVEAT_27B_ARTIFACT_SOURCE_GIT_SHA="$artifact_sha" \
    CAVEAT_27B_ACTION_SELECTION_NAME=selection_nonbinding_v3 \
    bash scripts/select_finalize_browser_action_correction_v3.sh "$target"
