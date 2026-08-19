# Harness-component ablations

This analysis-only package exposes two command-local scaffolds:

- `browseruse-prompt-only`, imported unchanged from
  `ablations/prompt_only/prompt_only_scaffold.py`;
- `browseruse-deliberative-no-coverage`, which reuses the production literal
  contract compiler, decision core, and browser-use transport while ranking
  only the locally submitted candidates.

The no-coverage input contains candidate facts and a proposed identity only.
It has no global candidate-count equation, exhaustion declaration, unresolved
global count, advertised-result total, page count, pager quote, or rendered
coverage witness. Submitted candidates must still have complete known facts,
must satisfy all literal hard constraints, and are ranked only by literal
contract objectives.

Neither scaffold is imported by `agentarena.scaffolds`. Register them for a
single command by placing only this directory on that command's `PYTHONPATH`:

```bash
env PYTHONPATH="$PWD/ablations/harness_components" \
  .venv/bin/python -m agentarena.benchmark.run \
  --scaffolds browseruse-deliberative-no-coverage \
  ...
```

The same command-local path can select `browseruse-prompt-only`; the existing
wrapper is reused rather than copied.

Run the focused tests with:

```bash
env PYTHONPATH="$PWD/ablations/harness_components" \
  .venv/bin/python -m pytest \
  ablations/harness_components/test_no_coverage.py -q
```
