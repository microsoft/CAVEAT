# Qwen3.5 harness-coupled post-training

This is an isolated, provenance-bound campaign for one narrow claim:

> Post-trained `Qwen/Qwen3.5-27B` with the unchanged
> `browseruse-deliberative` harness should select the optimal Amazon product
> more often than the exact raw model with that same harness.

The archived `training/harness_distill` campaign is not modified. This campaign
does not try to compress the whole harness into the model. It targets the raw
model's observed weak interfaces—literal contract compilation, recovery from a
rejected structured draft, and production of a complete decision checkpoint—so
the model cooperates more reliably with the existing harness.

## Leakage-resistant split

- Training: generated procedural `train` families only.
- Checkpoint selection: generated procedural `validation` families only.
- Development transfer check: Amazon laptop only; it never selects a checkpoint
  and no laptop product fact enters training.
- Confirmatory evaluation: Amazon office chair, mattress, backpack, and tent,
  plus generated procedural `test` families. These remain sealed until the
  final model is frozen.

Every derived artifact binds the campaign digest and frozen split manifest.
Training materialization rejects Amazon rows and any task outside procedural
train. The real 4,800-row corpora contain no Amazon-five literal facts, private
catalog fields, evaluator labels, or storefront operations token.

## Campaign

1. Generate 1,600 compatible procedural tasks using the deterministic audited
   generator. Each contributes two literal-contract examples, two recovery
   examples, and one decision-checkpoint rehearsal verified by the unchanged
   decision kernel. This produces an 8,000-row raw pool.
2. Train three independent 8-B200 LoRA candidates for 20 updates:
   `balanced` (40/40/20), `protocol-heavy` (60/30/10), and
   `recovery-heavy` (30/60/10). Each final corpus is exactly 4,800 rows and has
   predeclared checkpoints at updates 5, 10, and 20.
3. Serve all nine adapters together and score them on 64 fixed procedural
   validation contracts plus eight checkpoint-tool rehearsals. Select by exact
   contract semantics, then syntax validity, then checkpoint-tool validity;
   Amazon data is not used.
4. Numerically verify and merge the selected adapter into the pinned full base.
   The merge must differ from base logits, preserve the adapter's output
   distribution under a BF16-aware behavioral-equivalence gate, reload as a
   complete Hugging Face model, and preserve the processor and chat template.
5. Run one bounded ReST/DAgger-style refinement round on 64 fresh procedural
   contract prompts, four samples each (256 samples; 75% truthfully steered,
   25% clean). Retain operationally equivalent successes and give each failed
   sample one authored public-contract correction. Operational equivalence
   ignores free-form internal IDs and prose while requiring a one-to-one match
   of every literal criterion and all values that affect feasibility or ranking.
   Materialize an exact 60/30/10 correction / success / rehearsal corpus and
   train for 20 updates at `5e-6`.
6. Attest all predeclared refinement checkpoints, then numerically verify the
   step-20 LoRA against the selected merged SFT parent. A merged checkpoint is
   published only when it preserves adapter behavior under the BF16-aware gate.
   If BF16 folding fails that gate, it remains a recorded diagnostic: no
   threshold is relaxed and no merged `final/model` is published. Instead, the
   exact PEFT recovery below preserves the trained adapter as a runtime LoRA.

Step 5 is reward-filtered on-policy supervised refinement, not policy-gradient
RL. The optional 12-update reverse-KL OPD smoke remains disabled by default and
is not required for the result.

## Local validation and corpus materialization

```bash
python3 -m pip install -e '.[dev]'
make lint test validate

harness-posttrain build-procedural-corpora \
  --campaign configs/campaign.yaml \
  --generator-wheel vendor/harness_distill-0.1.0-py3-none-any.whl \
  --output artifacts/corpus
```

The vendored wheel was mechanically built from clean commit
`71d1cc3e76c4b7ab001100845d789080a26a0d01`. Before import, the builder verifies
the wheel and the exact source hashes of the deterministic generator, schemas,
and split inventory.

## Local immutable-image prep

The same prep runner works in the pinned training container. Mount the frozen
source, a writable output directory, and the Hugging Face cache containing the
exact Qwen snapshot:

```bash
docker run --rm --gpus all \
  -e HF_HOME=/hf -e HPT_CORPUS_DIR=/output/corpus \
  -v "$PWD:/campaign:ro" -v /path/to/hf-cache:/hf \
  -v /path/to/final-corpus:/output/corpus:ro \
  -v /path/to/output:/output \
  aifrontiers.azurecr.io/t-yuxuanli/harness-distill@sha256:28f38e74e17779e9c85985d3d3f970c1f6aa4427407f1843c11f2af87a324dd0 \
  bash /campaign/scripts/prepare_campaign.sh /output
```

This performs a fresh full-model LoRA update/reload smoke, exact PRIME rendering
audits for all 14,400 candidate rows, deterministic Parquet materialization,
and generation of all three validated PRIME TOMLs. `HPT_CORPUS_DIR` must resolve
to `TARGET/corpus`; omit it to generate the corpus inside the container.

## Cluster launch

First freeze the source once:

```bash
scripts/freeze_source.sh --write
scripts/launch_sft_campaign.sh --phase prep --execute
scripts/launch_sft_campaign.sh --phase sft --execute
scripts/launch_sft_campaign.sh --phase post --execute
scripts/launch_sft_campaign.sh --phase refine --execute
```

The refine phase trains only when its step-20 adapter is absent, writes the
publish-once `refinement/training_receipt.json`, and finishes in the same GPU
job. Its evaluation handoff is `final/inference_manifest.json`; the manifest
hash-binds `final/model`, the refinement adapter, the selected-SFT parent, and
all inference settings needed to serve the frozen model. Re-running the phase
accepts only byte-identical receipts and an already verified final model.

The launcher installs an owner-private, mechanically audited cap-64 copy of the
shared B200 helper. The helper retains its atomic submission lock and counts all
active or queued jobs for this user. Prep uses one 8-GPU node; the three SFT
jobs run concurrently on 24 GPUs; selection/merge/on-policy prep and refinement
each use one 8-GPU node. The immutable image, P0 socialreasoning workstream,
secret injection, exact source commit, manifests, and per-phase receipts are
fixed by the launcher.

Execution deliberately requires explicit phases. Each later phase checks the
prior immutable receipt, so an asynchronous cluster submission cannot race
ahead of its inputs. `--phase all` is a dry-run summary only.

Individual phases are resumable without changing an existing artifact:

```bash
scripts/launch_sft_campaign.sh --phase prep --execute
scripts/launch_sft_campaign.sh --phase sft --execute
scripts/launch_sft_campaign.sh --phase post --execute
scripts/launch_sft_campaign.sh --phase refine --execute
```

The campaign deliberately stops before using the confirmatory Amazon scenarios
for model or checkpoint decisions. Final baseline-vs-trained Amazon evaluation
must use the unchanged harness, identical run matrix and generous safety
backstops, report `strict_binary` as the primary metric, and audit that no
backstop bound any run.

## Exact-LoRA recovery from a rejected BF16 fold

After a successful 20-update refinement receipt, a rejected parent-aware merge
can be finalized without rounding the LoRA update into BF16 parent weights:

```bash
scripts/finalize_exact_lora.sh \
  /data/harness-posttrain/CAMPAIGN_DIGEST \
  /data/cache/huggingface/hub/models--Qwen--Qwen3.5-27B/snapshots/REVISION \
  /optional/path/to/captured_merge_failure.json
```

The command atomically creates `final/base_zero_adapter` with the exact step-20
tensor keys, shapes, dtypes, metadata, and LoRA configuration structure, while
changing only the declared parent and setting every tensor element to zero. It
then proves deterministic raw logits equal raw-plus-zero logits exactly, proves
the step-20 adapter changes its selected parent, and recomputes the original
BF16 merge rejection. Only after those checks pass does it publish
`final/exact_lora_manifest.json`; it never publishes `final/model` or the merged
inference manifest.

The final manifest hash-binds every raw/parent/adapter component, the training
and parent receipts, both adapter configs, the shared tokenizer and chat
template, the no-op/non-noop evidence, and the failed merge metrics. Both eval
arms use the same execution path: raw base plus zero LoRA versus selected SFT
parent plus step-20 LoRA. The serving wrapper rehashes all components before
launch and explicitly disables runtime adapter mutation:

```bash
scripts/serve_exact_lora_evaluation_arm.sh base /data/harness-posttrain/CAMPAIGN_DIGEST
scripts/serve_exact_lora_evaluation_arm.sh trained /data/harness-posttrain/CAMPAIGN_DIGEST
```

The wrapper uses the supported `vllm serve` launcher with four data-parallel
replicas and four API frontends. After starting each endpoint, run the
evaluation package's concurrent `dp-canary` before any route gate or Amazon
run. A valid canary receipt proves that one live request burst occupied engines
0, 1, 2, and 3; launch flags alone are not sufficient evidence of balanced
serving.
