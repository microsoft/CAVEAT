# Prompt-only browser-use ablation

This ablation keeps the existing `BrowserUseScaffold` as the execution
delegate. It clones the run context and task, appends one fixed three-sentence
guidance passage to the cloned instruction, and passes that context to the
unchanged baseline. The original task remains untouched for environment
evaluation and trajectory persistence.

The scaffold is intentionally absent from the core scaffold package. Register
it only for one command by placing this directory on that command's
`PYTHONPATH`:

```bash
env PYTHONPATH="$PWD/ablations/prompt_only" \
  .venv/bin/python -m agentarena.benchmark.run \
  --name prompt_only_example \
  --scenarios laptop \
  --conditions combined \
  --variants graded \
  --scaffolds browseruse-prompt-only \
  --models 'gpt-5.6-terra#low' \
  --max-steps 12000 \
  --repeats 1 \
  --jobs 1 \
  --results results \
  --base-port 19000
```

Do not export this `PYTHONPATH` globally and do not install a `.pth` file. The
command-local `sitecustomize.py` registers the new name in both the launcher
and its worker subprocess without changing the baseline or deliberative
scaffolds.

Run the focused tests with:

```bash
env PYTHONPATH="$PWD/ablations/prompt_only" \
  .venv/bin/python -m pytest \
  ablations/prompt_only/test_prompt_only.py -q
```
