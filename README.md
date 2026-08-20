# CAVEAT

CAVEAT evaluates whether a web agent follows a user's stated preferences and ultimately makes an optimal selection. It ships nine high-fidelity local environments, two benchmark tiers, the BrowserUse baseline, and CAVEAT-Harness.

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

## CAVEAT-27B

We post-train Qwen3.5-27B with general SFT, iterative SFT, and on-policy distillation. The CAVEAT-27B model checkpoint is forthcoming.

## Repository layout

```text
caveat/                 benchmark runtime
├── benchmark/          canonical tier definitions and artifact validation
├── core/               model, task, experiment, and plugin interfaces
├── envs/               all nine environments and benchmark data
├── scaffolds/          BrowserUse and CAVEAT-Harness
└── scoring/            optimal-selection rate
```

The project is licensed under the [MIT License](LICENSE). Report security vulnerabilities using [SECURITY.md](SECURITY.md), not a public issue.
