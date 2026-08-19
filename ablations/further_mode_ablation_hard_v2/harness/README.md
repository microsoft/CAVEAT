# Further harness-component ablation profiles

This isolated package supplies the eight profiles used by the hard-v2
integrity recovery. It does not edit or globally register any production
scaffold. Relative to v1, only arm C's post-run `stats_snapshot` audit inventory
is completed; all agent-visible and decision behavior is unchanged.

| Arm | Scaffold CLI value | Behavior |
|---|---|---|
| B | `browseruse` | unchanged baseline |
| P | `browseruse-prompt-only` | unchanged prompt-only wrapper |
| K | `browseruse-deliberative-contract-only` | P plus the production same-model literal-contract compiler |
| E | `browseruse-deliberative-feasibility` | K plus proposed-item fact and hard-feasibility validation; no comparative ranking |
| D | `browseruse-deliberative-no-coverage` | unchanged existing local exact-choice profile |
| C | `browseruse-deliberative-coverage-only` | P plus count/pager/exhaustion enforcement; accepts no contract facts and performs no ranking |
| A | `browseruse-deliberative-coverage-advisory` | full-shaped contract/checkpoint interface with local exact-choice enforcement; global coverage is shadow-logged only |
| F | `browseruse-deliberative` | unchanged production full harness |

For A, `stats.deliberative.first_submission_would_pass_full` is the frozen
first-call shadow outcome. Shadow coverage can never reject or change the
agent-facing checkpoint result.

The component flags and intended contrasts are frozen in `ARM_METADATA`.
Arm D is the pre-existing no-coverage implementation, reused byte-for-byte. It
is semantically aligned with the preceding fact/feasibility stage, but it is
not a token-identical one-line layer over E. Accordingly, D−E is interpreted as
the **local exact-choice package contrast**, not as a literal one-line software
toggle. The same package-level wording is used for the other behavioral
contrasts; process telemetry verifies actual treatment uptake.

Activate the registrations only for one command:

```bash
env PYTHONPATH="$PWD/ablations/further_mode_ablation_hard_v2/harness" \
  .venv/bin/python -m agentarena.benchmark.run \
  --scaffolds browseruse-deliberative-feasibility ...
```

Machine-readable registration and metadata:

```bash
env PYTHONPATH="$PWD/ablations/further_mode_ablation_hard_v2/harness" \
  .venv/bin/python ablations/further_mode_ablation_hard_v2/harness/validate.py
```

Focused tests:

```bash
env PYTHONPATH="$PWD/ablations/further_mode_ablation_hard_v2/harness" \
  .venv/bin/python -m pytest \
  ablations/further_mode_ablation_hard_v2/harness/test_component_scaffolds.py -q
```
