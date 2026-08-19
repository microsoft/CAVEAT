#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:?usage: prepare_campaign.sh TARGET_DIR}"
case "$target" in
  /data/caveat-27b/[0-9a-f][0-9a-f]*) ;;
  /output|/output/*) ;; # audited local-container mount
  *) echo "target must be a campaign-owned /data or /output path" >&2; exit 2 ;;
esac

export FLA_TILELANG=0
export PYTHONPATH="$root/src:$root/vendor/harness_distill-0.1.0-py3-none-any.whl${PYTHONPATH:+:$PYTHONPATH}"
python_bin="${CAVEAT_27B_PYTHON:-python3}"
prime_root="${CAVEAT_27B_PRIME_ROOT:-/opt/prime-rl}"
campaign="$root/configs/campaign.yaml"
smoke="$target/smoke/smoke_report.json"
corpus="${CAVEAT_27B_CORPUS_DIR:-$target/corpus}"

(cd "$root/vendor" && sha256sum -c SHA256SUMS)
"$python_bin" -m caveat_27b.cli validate-config --campaign "$campaign"

if [[ ! -f "$smoke" ]]; then
  "$python_bin" -m harness_distill.model_smoke \
    --model-config "$root/configs/smoke_model.yaml" \
    --output-dir "$target/smoke" --role primary --device cuda:0 \
    --rank 8 --alpha 16 --learning-rate 1e-4
fi

if [[ ! -f "$corpus/manifest.json" ]]; then
  "$python_bin" -m caveat_27b.cli build-procedural-corpora \
    --campaign "$campaign" \
    --generator-wheel "$root/vendor/harness_distill-0.1.0-py3-none-any.whl" \
    --output "$corpus"
fi
[[ "$(realpath "$corpus")" == "$(realpath "$target/corpus")" ]] || {
  echo "CAVEAT_27B_CORPUS_DIR must be mounted at TARGET/corpus so later phases share one identity" >&2
  exit 2
}

for candidate in balanced protocol-heavy recovery-heavy; do
  source_dir="$corpus/candidates/$candidate"
  prime_dir="$target/prime/$candidate"
  config_dir="$target/configs/$candidate"
  "$python_bin" -m caveat_27b.cli materialize-prime \
    --campaign "$campaign" --stage targeted_sft --candidate "$candidate" \
    --source-jsonl "$source_dir/train.jsonl" \
    --source-manifest "$source_dir/manifest.json" \
    --smoke-report "$smoke" --prime-root "$prime_root" --output "$prime_dir"
  "$python_bin" -m caveat_27b.cli generate-sft-configs \
    --campaign "$campaign" --stage targeted_sft --candidate "$candidate" \
    --smoke-report "$smoke" \
    --parent-model "$(jq -r .resolved_snapshot "$smoke")" \
    --dataset "$prime_dir" --output "$config_dir" --num-gpus 8
done

"$python_bin" -m caveat_27b.cli write-receipt \
  --campaign "$campaign" --kind prep --target "$target"

