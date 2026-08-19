# Migration notes

## 2026-07-23 — the validity overhaul

On 2026-07-23 the environments, benchmark catalogs, scoring gate, and harness were
redesigned after a validity audit. **Numbers measured before this date are not comparable
with numbers measured after it**, for three independent reasons:

1. **The relativeness confound is gone.** In the old catalogs every pinned steered lure
   passed *every* hard requirement, so under the strict metric a fully-steered purchase
   scored P\* = 1.0 at relativeness 0 and ≈ 0 at relativeness 4 *by construction* — the
   old headline "gap widens with relativeness" curve was partly the catalog/metric's
   arithmetic, not agent behavior. The new rosters enforce "no free capitulation": every
   pinned lure fails ≥ 1 level-0 requirement on a PDP-only dimension just below its cut,
   so the capitulation ceiling is C_0 = C_1 = 0 and a flat, bounded ≈ 0.17–0.28 at levels
   2–4 (`benchmark/validate.py` invariants 1–6; `scripts/audit_capitulation.py` is the
   standing regression lock). Heroes were de-extremed and ASINs shuffled at the same time.
2. **The gate semantics changed.** The headline P\* = G·O now uses one unified
   **per-variant** gate everywhere (G = all dims hard *at the current level*; O = mean s²
   over the current level's graded dims, headroom normalized over the fully-compliant
   set; O = 1 at level 0, so P\* is binary there). Amazon previously gated on the
   cross-variant intersection — the same trajectory rescored under the two gates gives
   different numbers. `vgeo` is demoted to an appendix ablation; the weighted-mean P is
   legacy-only.
3. **The serving layer changed.** All ten envs now serve a session-token-gated API
   (`STOREFRONT_CLIENT_TOKEN` in the served page, `STOREFRONT_OPS_TOKEN` for the
   evaluator only); direct endpoint navigation gets a 403 Robot-Check page, `/docs` /
   `/openapi.json` are dead, list/search rows are a whitelist identical in clean and
   steered (airbnb excepted), and anti-scrape is rate-based (solvable robot-check interstitial with
   `/verify-human` + TTL recovery) instead of the old silent spec-strip budgets
   (`AMAZON_SPEC_BUDGET` / `STOREFRONT_SPEC_BUDGET` / `grant_specs` — gone from measured
   conditions; `adv-*` conditions keep the legacy surface via `AMAZON_API_GATE=0`).
   Old runs could read data surfaces (bulk `/api/search`, full-spec list rows, clean-vs-
   steered exposure asymmetry) that no longer exist. Harness prompts were de-coached in
   the same pass.

**Pre-overhaul snapshot.** The complete pre-overhaul state of the benchmark data, reports,
and findings is archived in `.pre_overhaul_snapshot_20260723.tar.gz` at the repo root.
The committed pre-overhaul figures carry a `_vgeo` suffix (e.g. `fig_final_vgeo.png` —
scored with the vgeo aggregation, now demoted to an appendix ablation) and are superseded;
re-measured figures will land as `fig_final.*` / `fig_8envs.*`. `docs/4_results.md`
carries the erratum until the re-measurement lands.

**Old run trees cannot be silently rescored.** Catalog regeneration reassigned ASINs, so
`agentarena/scoring/rescore.py` carries a staleness guard: a recorded cell is scored only
when the basket title recorded for the chosen ASIN matches the *current* pool. Cells from
pre-regeneration runs fail that check and get `P* = None` (refused) rather than a
fabricated, mis-attributed score. To compare eras, use the snapshot archive — do not mix
pre- and post-overhaul cells in one figure.

---

# Migrating this repo to another host (e.g. an A100 server)

The repo is portable: no user-specific absolute paths remain in code, scripts compute their own
root, and the browser scaffold auto-discovers Chromium under `~/.cache/ms-playwright`. What is *not*
shipped is everything that is machine-specific or regenerable — the Python venv, the Chromium
binary, `node_modules`, the results tree, and secrets. Rebuild those on the target.

## What travels vs. what you recreate

| | Ships in the bundle? | On the new host |
|---|---|---|
| Source (`agentarena/`, `scripts/`, `pyproject.toml`) | ✅ | — |
| Product images / built frontends (`envs/*/server`) | ✅ (tracked) | — |
| Benchmark data (`benchmark_data/`, incl. `adversarial.json`) | ✅ | — |
| `.venv/` (811 MB, platform-specific) | ❌ gitignored | `bash scripts/setup_new_host.sh` |
| Chromium (`~/.cache/ms-playwright`) | ❌ | `playwright install chromium` (in setup script) |
| `node_modules/` | ❌ gitignored | only if you rebuild a frontend (`npm install`) |
| `results/` (measurement data, ~14 GB) | ❌ gitignored | **copy separately if you want it** (see below) |
| `.env` (holds `PHYAGI_API_KEY`) | ❌ gitignored | recreate by hand |
| `.tmprun/`, `__pycache__/`, `*.log` | ❌ gitignored | recreated at runtime |

## Option A — via git (cleanest; needs the work committed first)

The remote is `github.com/microsoft/preference-fidelity`. **There are uncommitted changes** (the
adversarial-taxonomy work + this cleanup), so commit and push first, or they won't come across:

```bash
git add -A && git commit -m "adversarial taxonomy + cleanup"   # review first
git push origin <branch>
# on the A100:
git clone https://github.com/microsoft/preference-fidelity.git && cd preference-fidelity
git checkout <branch>
bash scripts/setup_new_host.sh
```

`results/` is gitignored, so a fresh clone has **code but no past results** — fine if you'll re-run.

## Option B — compress + upload (captures uncommitted work too)

Use git to enumerate exactly the tracked + new-but-not-ignored files, so the bundle honours
`.gitignore` automatically (no `.venv`, `results/`, `.env`, `node_modules`, `.tmp*`, logs):

```bash
# ~1.1 GB, code + data + uncommitted work, NO venv/results/secrets
tar -czf ../pf-code.tar.gz -T <(git ls-files -c -o --exclude-standard)
```

On the A100:
```bash
mkdir preference-fidelity && tar -xzf pf-code.tar.gz -C preference-fidelity && cd preference-fidelity
bash scripts/setup_new_host.sh
```

`scripts/make_migration_bundle.sh` wraps this and can also bundle `results/` — see below.

## Moving `results/` (optional — only if you want the viewer/figures for *past* runs)

The 14 GB tree is 96 % step screenshots. If you only need scores/trajectories/figures re-derivable,
ship the small part; if you want in-viewer replay of old runs, ship all of it.

```bash
# small: JSON + logs only (~0.6 GB) — every score/figure recomputable, no in-viewer frame replay
tar -czf ../pf-results-json.tar.gz $(find results -name '*.json' -o -name 'run.log' -o -name 'index.json')
# full: everything incl. screenshots (~14 GB)
tar -czf ../pf-results-full.tar.gz results/
```
Unpack into `results/` on the target. (The viewer default reads `results/_viewer9`, whose symlinks
point into `results/byenv_v2`; keep both.)

## Secrets / auth on the new host

- **TRAPI** (primary, the `gpt-5.5#low` etc. deployments): auth is an Azure CLI bearer token minted
  per request — no key file. Install the `az` CLI and run `az login` (device-code works headless).
- **PhyAGI** (overflow fallback): `echo 'PHYAGI_API_KEY=<key>' > .env`. **Do not** copy this via a
  public channel; set it directly on the box.

## Scaling on the bigger machine

- **Cells** (each = browser + server, RAM-bound): raise per-runner `--jobs` and the cap at
  `agentarena/core/experiment.py:48` (`min(cpu, mem_gb/1.5, 24)`) — with more RAM/CPU you can lift
  the hard `24`.
- **Raw API concurrency** is still bounded by the *provider* (~32 concurrent per region per model on
  TRAPI), not by your cores. Spread concurrent runners across regions
  (`TRAPI_REGIONS_OVERRIDE`, as the `run_*_selfheal.sh` scripts already do) to go wider.
- The A100 GPU is not used by this project — the models are remote API calls. More CPU cores and RAM
  are what help (more concurrent browser cells); the GPU sits idle unless you add local inference.

## Verify the move

```bash
.venv/bin/python -m agentarena.benchmark.validate                          # catalog invariants 1-6
.venv/bin/python scripts/audit_capitulation.py                             # C_L regression lock
.venv/bin/python -m agentarena.scoring.strict_variants --selftest          # vgeo (appendix) scorer
.venv/bin/python -m agentarena.benchmark.run --name smoke --scenarios laptop \
   --conditions clean --variants thresholded --scaffolds browseruse \
   --models 'gpt-5.5#low' --jobs 1 --max-steps 40 --repeats 1              # one live cell
```
