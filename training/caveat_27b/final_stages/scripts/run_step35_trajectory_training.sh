#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

[[ $# -eq 7 ]] || {
  echo "usage: $0 PLAN PROFILE STAGED_SOURCE_ROOT EXECUTOR_GIT_SHA SOURCE_TREE_SHA256 RUNNER_SHA256 PRIME_ROOT" >&2
  exit 2
}
plan="$1"
profile="$2"
source_checkout="$3"
executor_git_sha="$4"
source_tree_sha256="$5"
runner_sha256="$6"
prime_root="$7"

[[ "$profile" == A || "$profile" == B ]]
[[ -f "$plan" && ! -L "$plan" ]]
[[ "$source_checkout" == /data/runs/t-yuxuanli/* ]]
[[ -d "$source_checkout" && ! -L "$source_checkout" && ! -e "$source_checkout/.git" ]]
[[ "$executor_git_sha" =~ ^[0-9a-f]{40}$ ]]
[[ "$source_tree_sha256" =~ ^[0-9a-f]{64}$ ]]
[[ "$runner_sha256" =~ ^[0-9a-f]{64}$ ]]
[[ "$prime_root" == /opt/prime-rl && -d "$prime_root/.git" && ! -L "$prime_root" ]]
[[ "$(git -C "$prime_root" rev-parse HEAD)" == d334ea52940b47f426293a7d146239e3fbf91caa ]]

python_bin="$prime_root/.venv/bin/python"
torchrun_bin="$prime_root/.venv/bin/torchrun"
runner="$source_checkout/scripts/run_step35_trajectory_training.sh"
resume_patcher="$source_checkout/scripts/apply_prime_rl_cross_stage_resume_patch.py"
grouped_mm_patcher="$source_checkout/scripts/apply_prime_grouped_mm_contiguous_grad_patch.py"
resume_order_patcher="$source_checkout/scripts/apply_prime_single_run_lora_resume_order_patch.py"
for path in "$runner" "$resume_patcher" "$grouped_mm_patcher" "$resume_order_patcher"; do
  [[ -f "$path" && ! -L "$path" ]]
done
for path in "$python_bin" "$torchrun_bin"; do
  resolved="$(readlink -f -- "$path")"
  [[ -x "$path" && -f "$resolved" && ! -L "$resolved" ]]
done
[[ "$(readlink -f -- "$0")" == "$(readlink -f -- "$runner")" ]]
[[ "$(sha256sum -- "$runner" | cut -d' ' -f1)" == "$runner_sha256" ]]
[[ "$($python_bin -c 'import torch; print(torch.cuda.device_count())')" == 4 ]]

export FLA_TILELANG=0
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$source_checkout/src:$prime_root/packages/prime-rl-configs/src:$prime_root/src:$prime_root/deps/renderers${PYTHONPATH:+:$PYTHONPATH}"
export WANDB_MODE=disabled
export ANONYMIZED_TELEMETRY=false
export STEP35_PROXIMAL_PROFILE="$profile"

observed_source_tree="$($python_bin - "$source_checkout" <<'PY'
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
rows = []
for base, directories, files in os.walk(root, followlinks=False):
    directory = Path(base)
    directories.sort()
    files.sort()
    for name in directories:
        child = directory / name
        if child.is_symlink() or not stat.S_ISDIR(child.lstat().st_mode):
            raise SystemExit(f"unsafe staged source directory: {child}")
    for name in files:
        child = directory / name
        if child.is_symlink() or not stat.S_ISREG(child.lstat().st_mode):
            raise SystemExit(f"unsafe staged source member: {child}")
        payload = child.read_bytes()
        rows.append({
            "path": child.relative_to(root).as_posix(),
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
encoded = json.dumps(sorted(rows, key=lambda row: row["path"]), sort_keys=True, separators=(",", ":")).encode()
print(hashlib.sha256(encoded).hexdigest())
PY
)"
[[ "$observed_source_tree" == "$source_tree_sha256" ]]

plan_values="$($python_bin - "$plan" "$profile" <<'PY'
import json
import sys
from pathlib import Path

plan = json.loads(Path(sys.argv[1]).read_text())
profile = sys.argv[2]
expected = {
    "A": {"learning_rate": 2e-7, "optimizer_updates": 3, "final_step": 35},
    "B": {"learning_rate": 5e-7, "optimizer_updates": 2, "final_step": 34},
}[profile]
if (
    plan.get("schema") != "harness-distill.step35-trajectory-action-proximal-plan.v1"
    or plan.get("profile") != profile
    or plan.get("source_step") != 32
    or plan.get("learning_rate") != expected["learning_rate"]
    or plan.get("optimizer_updates") != expected["optimizer_updates"]
    or plan.get("final_step") != expected["final_step"]
    or plan.get("update_steps") != list(range(33, expected["final_step"] + 1))
):
    raise SystemExit("Step35 profile plan binding drifted")
print(plan["artifact_git_sha"])
print(plan["prime"]["root"])
print(plan["training_output"])
print(plan["trainer_config"]["path"])
PY
)"
mapfile -t values <<<"$plan_values"
[[ "${#values[@]}" -eq 4 ]]
[[ "${values[0]}" == "$executor_git_sha" ]]
[[ "${values[1]}" == "$prime_root" ]]
training_output="${values[2]}"
trainer_config="${values[3]}"
[[ "$training_output" == /data/* && -d "$training_output" && ! -L "$training_output" ]]
[[ -f "$trainer_config" && ! -L "$trainer_config" ]]

evidence="$(dirname -- "$plan")/launch_evidence"
[[ ! -e "$evidence" && ! -L "$evidence" ]]
install -d -m 0700 -- "$evidence"
"$python_bin" "$resume_patcher" "$prime_root" >"$evidence/cross_stage_patch.json"
"$python_bin" "$grouped_mm_patcher" "$prime_root" >"$evidence/grouped_mm_contiguous_grad_patch.json"
"$python_bin" "$resume_order_patcher" "$prime_root" >"$evidence/single_run_lora_resume_order_patch.json"
"$python_bin" -m harness_distill.step35_proximal_patch apply \
  --prime-root "$prime_root" \
  --evidence "$evidence/step35_proximal_patch.json" >/dev/null

"$python_bin" -m harness_distill.step35_trajectory_training validate-plan \
  --plan "$plan" --require-launch-patches >/dev/null

[[ ! -e "$(dirname -- "$plan")/training_receipt.json" ]]
"$torchrun_bin" \
  --standalone \
  --nnodes=1 \
  --nproc-per-node=4 \
  --role=trainer \
  --log-dir="$training_output/logs/trainer/torchrun" \
  --redirects=3 \
  --tee=3 \
  --local-ranks-filter=0 \
  -m prime_rl.trainer.rl.train @ "$trainer_config"

"$python_bin" -m harness_distill.step35_trajectory_training write-receipt \
  --plan "$plan" --executor-git-sha "$executor_git_sha" >/dev/null
receipt="$(dirname -- "$plan")/training_receipt.json"
"$python_bin" -m harness_distill.step35_trajectory_training validate-receipt \
  --receipt "$receipt" >/dev/null

if [[ "$profile" == A ]]; then final_step=35; else final_step=34; fi
for step in $(seq 33 "$final_step"); do
  [[ -f "$training_output/checkpoints/step_${step}/trainer/.metadata" ]]
  [[ -f "$training_output/weights/step_${step}/STABLE" ]]
  [[ -f "$training_output/run_default/token_exports/step_${step}/STABLE" ]]
done
[[ ! -e "$training_output/checkpoints/step_$((final_step + 1))" ]]
[[ ! -e "$training_output/weights/step_$((final_step + 1))" ]]
