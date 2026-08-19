# Further mode-evidence ablations

This command-local package freezes the exact 240-run follow-up campaign:
128 Standard laptop steering runs and 112 hard laptop harness/context runs.
Production benchmark tasks, catalogs, steering files, frontend assets, and
scaffolds are not edited.

Before `prepare`, run the excluded 32-root host check and preserve its receipt:

```bash
.venv/bin/python ablations/further_mode_ablation/campaign.py \
  probe-host-capacity --output results/further_host_capacity_final.json \
  --base-port 17100
```

The launcher reserves four of the 36 certified browser-root slots for protected
132xx refill lanes. The campaign-specific coexistence postcondition is checked
before the host probe, every model probe, each child run, and every spawn; the
older inherited four-run coexistence arithmetic is not used as the capacity
decision.

Then use `prepare`, the four pre/mid `probe` stages, and `launch-block` in block
order. Launches require the literal confirmation printed by `--help`. Every
artifact is create-only. A terminal attempt may be archived only after
`classify-attempt` records the frozen campaign classifier's exact `infra`
decision; capability and ambiguous failures stay in the denominator as zero.
This score-zero rule requires enough terminal identity/evidence to validate a
measured result; generic early failures with missing telemetry block for manual
adjudication. At most three attempts are allowed for each immutable row and
route.

A missing-summary attempt is never silently relaunched. It is replacement-
eligible only when an attempt-numbered controller receipt proves process exit,
zero checkout delta from the Amazon seed (five historical order headers and no
order items), no logged agent behavior, and an exact external termination
marker. All evidence is hash-bound and archived; any ambiguity blocks the
report. Generic early failures with missing telemetry also block for manual
adjudication. The sole audit exception is an exact zero-step pre-agent compiler
semantic/output capability failure: it scores zero and is disclosed as an
uninitialized audit, never described as an untouched audit.

`report` requires all 240 measured terminal runs, complete attempt chains,
strict metrics, and every applicable limit audit green. Run
`analyze_results.py` only on that
numbered report. The analyzer applies the frozen exact contrasts, within-family
Holm correction, objective-order rank endpoint, and descriptive process/uptake
analysis.
