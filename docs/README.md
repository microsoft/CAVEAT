# Benchmark write-up materials

Four one-pagers + two figures for the project document. All four describe the
**post-overhaul** (2026-07-23) benchmark — token-gated serving, no-free-capitulation
catalogs, the unified per-variant P\* gate — and doc 4 now carries the post-overhaul
re-measurement (see `MIGRATION.md` for what changed and why pre-overhaul numbers are
not comparable).

| # | Document | Figure |
|---|---|---|
| 1 | [The environments](1_environments.md) | `benchmark_data/reports/fig_environments.png` — the 10 marketplaces |
| 2 | [The benchmark data-generation pipeline](2_data_generation.md) | — |
| 3 | [The evaluation suite](3_evaluation_suite.md) | — |
| 4 | [Experiments & results](4_results.md) | `benchmark_data/reports/fig_final.png` — the 4-panel post-overhaul results figure (the pre-overhaul `fig_final_vgeo.*` is superseded) |

## Regenerating the figures
```bash
# results figure (panels a-d, strict fidelity P*): rebuild the data record first, then render
python scripts/build_figure_data.py         # -> benchmark_data/reports/figure_data.json (schema 3)
python scripts/final_fig.py                 # -> benchmark_data/reports/fig_final.{png,pdf}
#   (post-overhaul runs only — the pre-overhaul figure is kept as fig_final_vgeo.* and the
#    pre-overhaul figure_data.json as benchmark_data/reports/archive/figure_data_pre_overhaul.json)

# environments figure: reads committed shots in benchmark_data/reports/env_shots/
python scripts/env_grid_fig.py              # -> benchmark_data/reports/fig_environments.{png,pdf}

# refresh the env homepage shots (optional): launches each env headless
python scripts/shoot_envs.py                # -> /tmp/env_shots/*.png  (then downscale into env_shots/)
```
Both figures are also written as PDF (vector) for print-quality inclusion.

## Hard-tier engineering records

- [Rounds 1–5 lessons](hard_mode_lessons.md) records why burial measures
  persistence rather than steering fidelity. It is a historical record; its
  old `*_hard` names refer to the retired burial tier.
- [Truthful steering hard tier](truthful_steering_hard.md) is the authoritative
  current contract: five canonical `*_hard` scenarios, 2,112 truthful products,
  complete SSR pagination and PDPs, no product-discovery JSON, and no deceptive
  specifications or benchmark-controlled exhaustion.
