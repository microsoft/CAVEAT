#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
helper="$HOME/.local/lib/harness-posttrain-cap64/b200"
image="${HPT_CONTAINER_IMAGE:-aifrlenvironments.azurecr.io/t-yuxuanli/harness-distill@sha256:28f38e74e17779e9c85985d3d3f970c1f6aa4427407f1843c11f2af87a324dd0}"
target="${HPT_CAMPAIGN_TARGET:-/data/harness-posttrain/4e6c4fe10d62d660f85c1063e3c1cecd0aed6e30}"
phase="${1:?usage: launch_browser_action_fixed_v6.sh prep|train|all}"

case "$phase" in
  prep|train|all) ;;
  *)
    echo "phase must be prep, train, or all" >&2
    exit 2
    ;;
esac
[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || {
  echo "HPT_CONTAINER_IMAGE must be an immutable digest reference" >&2
  exit 2
}
[[ "$target" =~ ^/data/harness-posttrain/[0-9a-f]{40}$ ]] || {
  echo "campaign target must be /data/harness-posttrain/<40 lowercase hex>" >&2
  exit 2
}
[[ -z "$(git -C "$root" status --porcelain --untracked-files=all)" ]] || {
  echo "campaign source is dirty" >&2
  exit 1
}
source_sha="$(git -C "$root" rev-parse HEAD)"
[[ "$source_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "execution source has no immutable Git identity" >&2
  exit 1
}
artifact_sha="${HPT_ACTION_ARTIFACT_SOURCE_SHA:-$source_sha}"
[[ "$artifact_sha" =~ ^[0-9a-f]{40}$ ]] || {
  echo "HPT_ACTION_ARTIFACT_SOURCE_SHA must be 40 lowercase hexadecimal characters" >&2
  exit 2
}
if [[ "$phase" != train && "$artifact_sha" != "$source_sha" ]]; then
  echo "fixed-v6 prep/all requires artifact SHA to equal the clean source commit" >&2
  exit 2
fi

"$root/scripts/install_cap64_helper.sh" >/dev/null
"$root/scripts/cap64_b200.sh" doctor >/dev/null

env \
  SOURCE_DIR="$root" USE_TORCHRUN=0 INSTALL_PACKAGE=0 UPDATE_PYTHONPATH=1 \
  VOLCANO_NAMESPACE=bonete61 VOLCANO_DATA_PVC_NAME=pvc-vast-bonete61 \
  B200_PRIORITY=p0 B200_WORKSTREAM=socialreasoning B200_MAX_GPUS=64 \
  B200_HF_SECRET=t-yuxuanli-hf-token B200_IMAGE_PULL_SECRET=t-yuxuanli-hd-acr-pull \
  CONTAINER_IMAGE_PATH="$image" MEMORY_SIZE_LIMIT=100Gi \
  NODES=1 GPUS_PER_NODE=4 NPROC_PER_NODE=4 \
  CPU_REQUESTS=8 MEMORY_REQUESTS=256Gi RDMA_REQUESTS=4 \
  B200_MANIFEST_DIR="/tmp/hpt-$source_sha-action-fixed-v6-$phase" \
  "$helper" submit "hpt-q35-action-fixed-v6-$phase" \
    env HPT_EXECUTION_SOURCE_GIT_SHA="$source_sha" \
    HPT_ARTIFACT_SOURCE_GIT_SHA="$artifact_sha" \
    bash scripts/train_browser_action_fixed_v6.sh "$phase" "$target"
