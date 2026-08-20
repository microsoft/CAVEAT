# CAVEAT

CAVEAT evaluates whether a web agent follows a user's stated preferences and ultimately makes an optimal selection. It ships nine high-fidelity local environments, two benchmark tiers, the BrowserUse baseline, CAVEAT-Harness, and the code lineage used to train CAVEAT-27B.

The CAVEAT environment names and visual identities are fictional. This project is not affiliated with or endorsed by any commercial platform represented by the underlying shopping and service tasks.

The only reported metric is **optimal-selection rate**: the fraction of valid runs that select an item satisfying every hard requirement and tied for best on every relative preference. Infrastructure-invalid runs are excluded from the denominator and reported separately.

## Install

CAVEAT requires Python 3.10 or newer and Chromium.

```bash
git clone https://github.com/microsoft/CAVEAT.git
cd CAVEAT
python -m venv .venv
source .venv/bin/activate
pip install -e ".[browser]"
python -m playwright install chromium
caveat validate
```

The bundled harnesses are guarded against `browser-use==0.13.6`; the `browser` extra installs that exact version.

## Run a model endpoint

CAVEAT accepts any OpenAI-compatible chat-completions endpoint. Put secrets in environment variables:

```bash
export MODEL_API_KEY=your-key
```

Create `run.yaml`:

```yaml
name: my-model-standard
tier: CAVEAT-Standard
scaffolds: [caveat-harness]
models:
  - name: my-model
    deployment: organization/model-id
    base_url: http://localhost:8000/v1
    api_key: env:MODEL_API_KEY
    vision: true
jobs: 2
```

Then run and score it:

```bash
caveat run run.yaml
caveat score results/my-model-standard --output results/my-model-standard/report.json
```

Runs are process-isolated, resumable, and written beneath `results/<name>/`. Re-running the same config skips completed cells and retries errored cells. Use `--force` only when completed cells should be replaced.

For a small endpoint smoke test, add:

```yaml
environments: [caveat_sport]
repeats: 1
```

Such a subset is useful for development but is not a complete tier result. `caveat run --help`, `caveat list`, and `caveat score --help` show the remaining options.

## Benchmark tiers

| Tier | Released matrix | Repeats | Runs per model/harness |
| --- | --- | ---: | ---: |
| **CAVEAT-Standard** | Five CAVEAT-Shop scenarios × four relative-preference variants under combined steering, plus eight environments × five variants under clean and steered conditions | 3 | 300 |
| **CAVEAT-Hard** | Five truthful 2,112-product CAVEAT-Shop scenarios × the graded variant under combined steering | 2 | 10 |

The additional CAVEAT-Standard environments are CAVEAT-Stay, CAVEAT-Food, CAVEAT-Market, CAVEAT-Craft, CAVEAT-Services, CAVEAT-Grocery, CAVEAT-Sport, and CAVEAT-Kicks. Tier names in configuration and result manifests are exactly `CAVEAT-Standard` and `CAVEAT-Hard`.

Run the hard tier by changing one line:

```yaml
tier: CAVEAT-Hard
```

`caveat validate` checks every committed tier oracle without regenerating benchmark data.

## Harnesses

The release includes:

- `browseruse`: the BrowserUse baseline.
- `caveat-harness`: CAVEAT-Harness, the improved harness.

Both receive the same model endpoint, task, browser start URL, step backstop, and environment evaluator.

To use your own harness, create an importable module that registers a scaffold:

```python
from caveat import RawTrajectory, Scaffold, SCAFFOLDS

@SCAFFOLDS.register("my-harness")
class MyHarness(Scaffold):
    name = "my-harness"

    def run(self, ctx):
        # Drive ctx.start_url with ctx.model, and record any steps you need.
        return RawTrajectory(steps=[], answer="")
```

Then select it in the run config:

```yaml
plugins: [my_harness]
scaffolds: [my-harness]
```

Plugin modules are imported in both the controller and each isolated worker. Environment startup, evaluation, persistence, parallelism, and scoring remain handled by CAVEAT.

## CAVEAT-27B training lineage

CAVEAT-27B is the released name for the S33 checkpoint. It is a multi-stage LoRA post-training result, not a model trained from scratch:

1. Start from pinned `Qwen/Qwen3.5-27B` revision `fc05daec18b0a78c049392ed2e771dde82bdf654` in BF16.
2. Train three 20-update procedural SFT mixtures, select the best checkpoint, and merge that adapter into the raw model.
3. Train a 20-update, reward-filtered on-policy ReST/DAgger-style SFT refinement.
4. Continue the LoRA through fixed-v7 and Sol/DAgger laptop adaptation, then paired action, checkout, cart, and retention updates through step 32.
5. Run three trajectory-level on-policy action-distillation updates and publish the step-33 microcheckpoint as CAVEAT-27B.

The final optimization is supervised action-token cross-entropy/behavior cloning. On-policy and teacher-corrected data collection is used, but the lineage is not PPO, GRPO, or DPO. Laptop evaluation is same-task adaptation; non-laptop categories provide the stronger generalization test.

The published rank-64, alpha-128 step-33 adapter has tree SHA-256 `8eb0b3bc397d8dde41a294acf067be51d5e14d8c7de315e6a696264e3c5cd4aa`. It must be served on the selected merged parent with tree SHA-256 `5939382fbc6db775972dc9cebf3e5be20654149a0412b4f00072bad12a2215ca`, not directly on raw Qwen weights.

Training source is organized as follows:

```text
training/caveat_27b/
├── configs/          pinned parent, stack, campaign, and fixed-v7 inputs
├── src/caveat_27b/   broad SFT, selected-parent, refinement, and fixed-v7 code
├── scripts/          exact entry points used for those stages
├── final_stages/     receipt-bound Sol/DAgger and steps 26–33 source
├── lineage.json      checkpoint identities and source commits
└── vendor/           checksummed broad-stage generator/merge dependency
```

The late-stage source retains its historical `harness_distill` Python import path and a few internal stage labels because plans, receipts, and PRIME-RL child-module invocations bind those identifiers. The public model and directory name remain CAVEAT-27B. These scripts reproduce the artifact checks and update logic used in the campaign; executing them also requires the referenced parent checkpoints, immutable receipts, four-GPU PRIME-RL environment, and training data, which are not embedded in this repository.

## Repository layout

```text
caveat/                 benchmark runtime
├── benchmark/          canonical tier definitions and artifact validation
├── core/               model, task, experiment, and plugin interfaces
├── envs/               all nine environments and benchmark data
├── scaffolds/          BrowserUse and CAVEAT-Harness
└── scoring/            optimal-selection rate
training/caveat_27b/    CAVEAT-27B training lineage
```

The project is licensed under the [MIT License](LICENSE). Report security vulnerabilities using [SECURITY.md](SECURITY.md), not a public issue.
