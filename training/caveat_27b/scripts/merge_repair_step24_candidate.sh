#!/usr/bin/env bash
set -euo pipefail
umask 077

usage='usage: merge_repair_step24_candidate.sh TRAINING_RECEIPT PARENT_MODEL ADAPTER OUTPUT_DIR MERGE_RECEIPT TRAINING_FILE_SHA256 TRAINING_BODY_SHA256 PARENT_TREE_SHA256 ADAPTER_TREE_SHA256 EXECUTOR_GIT_SHA CONTAINER_IMAGE_DIGEST PYTHON_BIN'
[[ "$#" -eq 12 ]] || {
  echo "$usage" >&2
  exit 2
}

training_receipt="$1"
parent_model="$2"
adapter="$3"
output_dir="$4"
merge_receipt="$5"
training_file_sha256="$6"
training_body_sha256="$7"
parent_tree_sha256="$8"
adapter_tree_sha256="$9"
executor_git_sha="${10}"
container_image_digest="${11}"
python_bin="${12}"

source_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
[[ "$executor_git_sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$container_image_digest" =~ ^.+@sha256:[0-9a-f]{64}$ ]]
for digest in \
  "$training_file_sha256" \
  "$training_body_sha256" \
  "$parent_tree_sha256" \
  "$adapter_tree_sha256"; do
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]]
done
[[ "$training_receipt" = /* && -f "$training_receipt" && ! -L "$training_receipt" ]]
for required in "$parent_model" "$adapter"; do
  [[ "$required" = /* && -d "$required" && ! -L "$required" ]]
done
for fresh in "$output_dir" "$merge_receipt"; do
  [[ "$fresh" = /* && ! -e "$fresh" && ! -L "$fresh" ]]
done
[[ "$output_dir" != "$parent_model"/* && "$output_dir" != "$adapter"/* ]]
[[ "$parent_model" != "$output_dir"/* && "$adapter" != "$output_dir"/* ]]

[[ "$python_bin" == "/opt/prime-rl/.venv/bin/python" && -x "$python_bin" ]]
resolved_python="$(realpath -e -- "$python_bin")"
[[ -f "$resolved_python" && -x "$resolved_python" ]]
[[ "$resolved_python" =~ ^/root/\.local/share/uv/python/[^/]+/bin/python3\.12$ ]]

export LAST_CONTAINER_IMAGE_DIGEST="$container_image_digest"
export LAST_SOURCE_GIT_SHA="$executor_git_sha"
export LAST_ALLOCATED_GPUS=1
export PYTHONPATH="$source_root/src${PYTHONPATH:+:$PYTHONPATH}"

# Invoke the venv symlink itself.  Executing its resolved UV interpreter would
# lose pyvenv.cfg discovery and therefore the pinned torch/transformers stack.
exec "$python_bin" -m caveat_27b.repair_step24_candidate_merge \
  --training-receipt "$training_receipt" \
  --parent-model "$parent_model" \
  --adapter "$adapter" \
  --output-dir "$output_dir" \
  --merge-receipt "$merge_receipt" \
  --training-receipt-file-sha256 "$training_file_sha256" \
  --training-receipt-body-sha256 "$training_body_sha256" \
  --parent-tree-sha256 "$parent_tree_sha256" \
  --adapter-tree-sha256 "$adapter_tree_sha256" \
  --executor-git-sha "$executor_git_sha" \
  --container-image-digest "$container_image_digest" \
  --device cuda:0
