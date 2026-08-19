# scripts/ — the live benchmark pipeline

Everything here is part of the current PreferenceFidelity pipeline (9 envs = 8 self-contained
marketplace clones + the generated Amazon benchmark). Retired one-shot campaign middleware has
been removed; the exact pre-cleanup source used by the completed further-mode ablations is
preserved in a hash-bound measured-source snapshot.

Run everything with the repo venv: `.venv/bin/python` (not system python).

## Measure (browser-use agents)

| script | what it does |
|---|---|
| `pilot9.py` | The cell runner: P* across models × {clean, steered} × 5 relativeness variants for the clone envs. Uses `core.experiment.Runner`, resumable (done cells skipped). `--models "phyagi/gpt-5.5#high,gpt-4.1"` style specs pick provider + reasoning effort. |
| `run_pilot_selfheal.sh` | Canonical way to run `pilot9.py` to completion: loops delete-infra-crashed-cells (`_crash_sweep.py`) + resume until crash-free. |
| `_crash_sweep.py` | Detects (and optionally deletes) cells that failed on INFRA faults (CDP crash / LLM 4xx-5xx storm), never agent decisions, so re-runs stay honest. |
| `tune_env.sh` | Per-env re-measure: one env, both models, all levels, clean+steered, on freshly probed routing. Use after any env/data change. |
| `purge_503.py` | Deletes cell dirs poisoned by a TRAPI 503 outage so a resume actually retries them. |
| `run_supplement.py` | Re-runs an **explicit list of cells at a higher step budget** into a separately-labeled tree (never mixed into the uniform-budget matrix). Used for the at-cap give-up supplement: cells that exhausted the matrix's budget while still searching get one re-run at `--max-steps 80`, so budget starvation can be separated from genuine give-up. |

## Report + figures

| script | what it does |
|---|---|
| `pilot_report5.py` | Aggregates a results tree into the post-overhaul C1′/C2/C3′/C4 pilot-criteria report over all 5 levels, plus diagnostics (pinned-purchase rate, P* decomposition, per-level C_L echo). The viewer's `/api/pilot` matches it exactly. |
| `report_overhaul.py` | Rescores an experiment tree (strict P*) and prints per (variant × condition) mean P*, sd, B(met-or-0), M(margin), outcome mix, pin-buy rate, and clean−steered gaps — the Phase-A exit-gate table. `python scripts/report_overhaul.py 'results/overhaul_b80/overhaul_b80_r*'` |
| `build_figure_data.py` | Rebuilds `benchmark_data/reports/figure_data.json` (**schema 3**) from the post-overhaul trees: the b80 headline matrix (per model × level × condition: P*, B, M, pin-buy, give-up, n, per-run values), the 50-vs-80 budget arm + at-cap supplement, the leaderboard aggregates, the 9-env C4 table, and the Amazon C_L bands. Rescores first unless `--no-rescore`; `--skip-lb` leaves the leaderboard block out. |
| `fig_8envs.py` | Per-env results figure (2×4): gpt-5.5-high vs gpt-4.1, aggregated steered bar + the 5 levels, clean baselines as ticks. |
| `final_fig.py` + `figdata.py` | Main results figure `benchmark_data/reports/fig_final.{png,pdf}` from `figure_data.json`: (a) clean vs steered P* across relativeness per headline model, (b) model leaderboard under steering at the graded levels with the C_L band, (c) the 9 storefront clones with every run plotted, (d) 50-vs-80-step budget sensitivity. |
| `shoot_envs.py` | Launches each env clean and screenshots its homepage → `benchmark_data/reports/env_shots/`. |
| `env_grid_fig.py` | Composes the env_shots into the nine-env grid figure (`fig_environments.png`). |

> **`figure_data.json` changed shape in the 2026-07 overhaul rewrite.** It is now the schema-3
> aggregate written by `build_figure_data.py`. Historical data and figures are retained under
> `benchmark_data/reports/archive/` and in `.pre_overhaul_snapshot_20260723.tar.gz`; the retired
> flat-schema rendering scripts are no longer part of the live pipeline.

## Validate / act-as-agent (no browser agents)

| script | what it does |
|---|---|
| `certify_hard.py` | Canonical exhaustive certificate for the five 2,112-product `*_hard` scenarios. Checks committed generation, every organic page and PDP under all four conditions, closed product-data JSON routes, client-token-only shopping, and hero purchase/evaluation at P*=1.0. |
| `enumerate_oracle.py` | Scripted non-LLM storefront enumerator for a canonical hard scenario; proves ordinary pagination/PDP discovery and hero solvability through agent-visible surfaces. |
| `validate7.py` | Offline validity check (strict by default): oracle P* = 1.0 at every variant, hero argmax, no compliant item advertised, every advertised item fails a PDP-only L0 cut, capitulation-ceiling bands. `--legacy` for pre-overhaul archaeology. |
| `verify_env.py` | Read-only P*-curve verification of an env's current catalog: hero flat 1.0, C_0=C_1=0, C_2-4 ≤ 0.35, mid-item band, per-level pin table. |
| `audit_capitulation.py` | Standing regression lock on "no free capitulation": recomputes C_L (max strict P* any PINNED lure can reach, per level) from COMMITTED artifacts for the 5 original Amazon products (`--clones` for the 9 clone envs, `--only laptop,tent` to subset). The canonical truthful `*_hard` tier is intentionally out of scope and routes to `certify_hard.py`. |
| `audit_lockdown.py` | Boots an env per condition the way the harness does (`--env amazon|<clone>|airbnb`, `--catalog`, `--conditions`) and runs the serving-parity matrix over real HTTP: token gate + legacy-`web` rejection, card whitelist (no specs in list rows), clean==steered key-sets, truthful organic card signals, 24-row limit clamps, tokenless cart/checkout closed, docs 404, robot-check trigger + `/verify-human` recovery, and the adv-* gate-off regression (those conditions run with the gate OFF by design). |
| `audit_trajectories.py` | Post-run trajectory audit over a results tree: exploit navigations (`/api`, `/docs`, `/openapi.json`, `/redoc`, `/graphql` — should be zero after the lockdown), robot-check encounters (~zero for honest browsing), PDP coverage before purchase (verification effort), and the outcome/P* table. `--fail-on-exploit` for CI, `--json` for the full record. |
| `calibrate_rate_gate.py` | Answers "would this run have tripped the Robot Check?" offline, without booting a server: replays a recorded `trajectory.json` URL stream (or a synthetic trace) against all three gate windows — short burst, long, and the 5-min SUSTAINED anti-enumeration window — and reports would-trigger counts. Use it to calibrate `SF_RATE_*` before committing a preset. |
| `serve_env.py` | Spins up ONE env's backend and keeps it alive so a human/Claude can shop it as the agent via curl. |
| `score_env.py` | Scores whatever was just transacted against a live `serve_env.py` server (reconstructs the variant task). |
| `try_env.py` | Scripted end-to-end pick through an env's backend (no browser) — fast smoke that checkout + evaluation work. |

## Hard-tier campaign

| script | what it does |
|---|---|
| `run_hard.sh` | Canonical fail-closed gpt-5.6-sol-high campaign entry point. `prepare` consumes a passing `certify_hard.py` report, `launch` runs the frozen combined/graded n=2 matrix, and `report`, `refill`, and `verify` preserve the exact denominator and reject bounded or infrastructure-confounded runs. |
| `freeze_hard_campaign.py` | Freezes the certification, five committed catalogs, source tree, runtime dependencies, high safety backstops, routing schedule, and sanitized runtime environment. |
| `report_hard_campaign.py` | Rescores `preservation_strict`, reports `strict_binary`, purchase class, steps/duration, and refuses a headline for partial or backstop-bound evidence. |
| `hard_campaign_ops.py` | Create-only probe checkpoints, launch receipts, and recoverable refill evidence for `run_hard.sh`. |
| `hard_campaign_runtime.py` | Shared frozen cap, dependency, environment, and source-inventory contract used by the campaign freezer. |
| `test_hard_campaign.py` | Unit tests for the schedule, certificate gate, transport contract, purchase classification, and harness-bound detection. |

## Ops (TRAPI routing)

| script | what it does |
|---|---|
| `probe_regions.py` | Probes region health per model, emits a `TRAPI_REGIONS_OVERRIDE` routing JSON. Re-probe before every run — health flaps. |
| `probe_concurrency.py` | Finds max safe concurrency per (region, model) with escalating batches. |

## Assets

| script | what it does |
|---|---|
| `gen_env_product_images.py` | Worklist-driven product-photo generation via TRAPI `gpt-image-1` (region-pooled, resumable, per-env post modes). Used to create the 246 bundled product images. |
