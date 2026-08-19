# Further-mode cross-study recovery bridge

This isolated package preserves the 128 completed, green Standard rows from
`further_mode_ablation_v1` and later joins them to the separate 112-run
`further_mode_ablation_hard_v2` study. It does not edit or relabel either source
campaign.

The recovery is intentionally explicit:

- Standard-v1 blocks 1--4 are a separately named, 128-run preserved study.
- The entire v1 hard execution is ineligible for measured inference; v1 block
  5 is recorded as an excluded operational pilot and its live artifacts are not
  read by the preservation bridge.
- Hard-v2 is a distinct 112-run source study with its own manifest, amendment,
  report, evidence, and source hashes.
- The combined 240-row artifact retains each original run identity and adds a
  source-provenance record. It invokes the SHA-bound, already-frozen v1 exact
  statistical kernels (33 linear contrasts over three endpoints plus three
  directional-rank effects).

## Preserved Standard study

The create-only artifact is already frozen at:

`results/further_mode_ablation_standard_preserved_v1/study_bundle.json`

Revalidate all 128 rows, evidence inventories, metrics, source code, manifest,
and sidecars with:

```bash
.venv/bin/python \
  ablations/further_mode_ablation_recovery/standard_bridge.py \
  validate-standard \
  --bundle results/further_mode_ablation_standard_preserved_v1/study_bundle.json
```

The validator reruns the original v1 campaign validator and `_validated_result`
for every row. A re-hashed but altered bundle therefore fails; the JSON is not
trusted as a self-attestation.

## Combine after hard-v2 finishes

First have the hard-v2 campaign build its exact report and export its validated
study bundle. Then run:

```bash
.venv/bin/python \
  ablations/further_mode_ablation_recovery/combine_studies.py \
  combine-studies \
  --standard-bundle \
    results/further_mode_ablation_standard_preserved_v1/study_bundle.json \
  --hard-bundle /absolute/path/to/hard_v2_study_bundle.json \
  --output-dir results/further_mode_ablation_combined_recovery_v1
```

Before writing, the adapter independently reruns both source campaigns' row
validators, requires exact 128+112 disjoint rows and repetitions 1--8 for every
frozen arm, recomputes hard rank metrics only from hard-v2 frozen inputs, and
uses the original analyzer source at SHA-256
`1ce48c04fda4bbadd59fa2f1bfa959af400be649c7507292677d61498e13bd0c`.

The two create-only outputs are `combined_row_bundle.json` and
`combined_exact_effects.json`, each with a SHA-256 sidecar. Revalidate both and
all underlying source evidence with `validate-combined`.

## Tests

```bash
.venv/bin/python -m pytest -q \
  ablations/further_mode_ablation_recovery/test_standard_bridge.py \
  ablations/further_mode_ablation_recovery/test_combine_studies.py
```

