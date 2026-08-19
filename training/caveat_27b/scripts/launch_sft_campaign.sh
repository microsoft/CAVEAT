#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
project_root="$(cd -- "$root/../.." && pwd)"
helper="$HOME/.local/lib/caveat-27b-cap64/b200"
image="${CAVEAT_27B_CONTAINER_IMAGE:-aifrontiers.azurecr.io/t-yuxuanli/harness-distill@sha256:28f38e74e17779e9c85985d3d3f970c1f6aa4427407f1843c11f2af87a324dd0}"
[[ "$image" =~ @sha256:[0-9a-f]{64}$ ]] || {
  echo "CAVEAT_27B_CONTAINER_IMAGE must be an immutable digest reference" >&2
  exit 2
}
execute=0
phase=all
while (( $# )); do
  case "$1" in
    --execute) execute=1; shift ;;
    --phase) phase="${2:?--phase requires prep, sft, post, refine, or all}"; shift 2 ;;
    *) echo "usage: $0 [--phase prep|sft|post|refine|all] [--execute]" >&2; exit 2 ;;
  esac
done
case "$phase" in prep|sft|post|refine|all) ;; *) exit 2 ;; esac
post_gpus="${CAVEAT_27B_POST_GPUS:-8}"
case "$post_gpus" in 4|8) ;; *) echo "CAVEAT_27B_POST_GPUS must be 4 or 8" >&2; exit 2 ;; esac
if (( execute == 1 )) && [[ "$phase" == all ]]; then
  echo "refusing an unattended all-phase launch; run the four receipt-gated phases in order" >&2
  exit 2
fi

git_root="$(git -C "$root" rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$git_root" && "$(realpath "$git_root")" == "$root" ]] || {
  echo "campaign source must be its own frozen Git worktree" >&2
  exit 1
}
[[ -z "$(git -C "$root" status --porcelain --untracked-files=all)" ]] || {
  echo "campaign source is dirty" >&2
  exit 1
}
source_sha="$(git -C "$root" rev-parse HEAD)"
target="${CAVEAT_27B_CAMPAIGN_TARGET:-/data/caveat-27b/$source_sha}"
[[ "$target" =~ ^/data/caveat-27b/[0-9a-f]{40}$ ]] || {
  echo "campaign target must be /data/caveat-27b/<40 lowercase hex>" >&2
  exit 2
}
if [[ "$target" != "/data/caveat-27b/$source_sha" ]]; then
  echo "audited artifact reuse: source=$source_sha target=$target" >&2
fi

"$root/scripts/install_cap64_helper.sh" >/dev/null
"$root/scripts/cap64_b200.sh" doctor >/dev/null

submit() {
  local name="$1" command="$2" gpus="${3:-8}"
  local cpus=$((gpus * 8)) memory_gib=$((gpus * 125)) rdma="$gpus"
  env \
    SOURCE_DIR="$root" NODES=1 GPUS_PER_NODE="$gpus" NPROC_PER_NODE="$gpus" \
    USE_TORCHRUN=0 INSTALL_PACKAGE=0 UPDATE_PYTHONPATH=1 \
    VOLCANO_NAMESPACE=bonete61 VOLCANO_DATA_PVC_NAME=pvc-vast-bonete61 \
    B200_PRIORITY=p0 B200_WORKSTREAM=socialreasoning B200_MAX_GPUS=64 \
    B200_HF_SECRET=t-yuxuanli-hf-token \
    B200_IMAGE_PULL_SECRET=t-yuxuanli-hd-acr-pull \
    B200_MANIFEST_DIR="/tmp/caveat-27b-$source_sha-$name" \
    CONTAINER_IMAGE_PATH="$image" CPU_REQUESTS="$cpus" MEMORY_REQUESTS="${memory_gib}Gi" \
    RDMA_REQUESTS="$rdma" MEMORY_SIZE_LIMIT=100Gi \
    "$helper" submit "$name" env CAVEAT_27B_POST_GPUS="$gpus" \
      CAVEAT_27B_EXECUTION_SOURCE_GIT_SHA="$source_sha" bash "$command" "$target"
}

if (( execute == 0 )); then
  echo "DRY RUN: target=$target image=$image"
  echo "prep: 8 B200; SFT: three concurrent 8-B200 jobs; post/refine: $post_gpus B200 each"
  echo "aggregate peak: 24 B200"
  echo "Run with --execute after source freeze."
  exit 0
fi
cd "$project_root"
if [[ "$phase" == prep || "$phase" == all ]]; then
  submit caveat-27b-prep scripts/prepare_campaign.sh
fi
if [[ "$phase" == sft || "$phase" == all ]]; then
  pids=()
  for candidate in balanced protocol-heavy recovery-heavy; do
    (
      env \
        SOURCE_DIR="$root" NODES=1 GPUS_PER_NODE=8 NPROC_PER_NODE=8 \
        USE_TORCHRUN=0 INSTALL_PACKAGE=0 UPDATE_PYTHONPATH=1 \
        VOLCANO_NAMESPACE=bonete61 VOLCANO_DATA_PVC_NAME=pvc-vast-bonete61 \
        B200_PRIORITY=p0 B200_WORKSTREAM=socialreasoning B200_MAX_GPUS=64 \
        B200_HF_SECRET=t-yuxuanli-hf-token \
        B200_IMAGE_PULL_SECRET=t-yuxuanli-hd-acr-pull \
        B200_MANIFEST_DIR="/tmp/caveat-27b-$source_sha-$candidate" \
        CONTAINER_IMAGE_PATH="$image" CPU_REQUESTS=64 MEMORY_REQUESTS=1000Gi \
        RDMA_REQUESTS=8 MEMORY_SIZE_LIMIT=100Gi \
        "$helper" submit "caveat-27b-${candidate//-}" \
          bash scripts/train_sft_candidate.sh "$target" "$candidate"
    ) &
    pids+=("$!")
    sleep 30
  done
  status=0
  for pid in "${pids[@]}"; do wait "$pid" || status=1; done
  (( status == 0 )) || { echo "one or more SFT candidate jobs failed" >&2; exit 1; }
fi
if [[ "$phase" == post || "$phase" == all ]]; then
  submit caveat-27b-post scripts/prepare_refinement.sh "$post_gpus"
fi
if [[ "$phase" == refine || "$phase" == all ]]; then
  submit caveat-27b-refine scripts/train_refinement.sh "$post_gpus"
fi
