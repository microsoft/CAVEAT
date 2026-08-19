# Hard-v2 integrity recovery

This create-only, command-local campaign reruns the exact 112-run hard study
after excluding all of v1 block 5 as a pilot. The sole behavioral-code delta
is an outcome-inert audit fix: arm C now reports the required
`structured_response_attempts` inventory with zero calls, attempts, rejections,
exhaustions, and touches. No prompt, tool schema, checkpoint logic, browser
action, task, catalog, condition, model, route, cap, score, contrast, or
production benchmark file changes.

The frozen design is 14 arms × 8 repetitions in four route-matched 28-run
blocks. Blocks 1–2 require `sol_high_before`; blocks 3–4 require
`sol_high_mid`. Every v1 block-5 run ID is bound as an excluded pilot and is
disjoint from this campaign.

Prepare and launch commands are printed in the parent handoff; `--base-port`
must be chosen from a currently free 28-port band. Reports fail closed unless
all 112 exact rows validate.

The canonical bridge interface is:

```bash
.venv/bin/python ablations/further_mode_ablation_hard_v2/campaign.py \
  export-bundle --campaign-dir results/further_mode_ablation_hard_v2 \
  --report results/further_mode_ablation_hard_v2/reports/report_0001.json \
  --output results/further_mode_ablation_hard_v2/hard_study_bundle.json
```

Consumers can independently revalidate it via the public Python function
`ablations.further_mode_ablation_hard_v2.campaign.validate_study_bundle` or:

```bash
.venv/bin/python ablations/further_mode_ablation_hard_v2/campaign.py \
  validate-bundle \
  --bundle results/further_mode_ablation_hard_v2/hard_study_bundle.json
```

