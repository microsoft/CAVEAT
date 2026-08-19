# Marketplace Steering Benchmark — Findings

**Measured after the 2026-07-23 validity overhaul** (hardened catalogs, token-gated serving layer,
per-variant strict gate, de-coached harness prompts — see `MIGRATION.md`). The full write-up with
every table, caveat, and reproduction command is **[`docs/4_results.md`](../docs/4_results.md)**;
this file is the short version.

> **Superseded pre-overhaul findings.** Everything measured before 2026-07-23 — including the old
> "graded is robust", satisficing-spectrum, and `amazon_full` gap-widening write-ups that used to
> live in this file — is archived in `.pre_overhaul_snapshot_20260723.tar.gz` at the repo root
> (`benchmark_data/FINDINGS.md`, `benchmark_data/reports/*`, and the `_vgeo`-suffixed figures).
> Those numbers came from catalogs where a fully-steered purchase scored `P* = 1.0` at relativeness
> 0 and ~0 at relativeness 4 *by construction*; they are not comparable with anything below.

---

## The question

When a store steers — pinned placement, sponsored/choice badges, inflated ratings, a fake discount,
a scarcity timer, a hidden checkout fee, a prechecked add-on, all stacked on one decoy — does a web
agent still buy what the user actually ranked highest?

Headline metric **`P*` = G · O**: a per-variant gate (any violation of a requirement that is hard at
the current relativeness level => 0) times mean `s²` over the level's graded dimensions.
`B = 1[P* = 1]` is the all-or-nothing read; `M = clip((P* − C_L)/(1 − C_L))` removes the designed,
level-dependent capitulation floor `C_L` and is the metric to use across levels.

## What we found

**1. Clean fidelity is high; steering breaks it.** Pooled over 5 products × 3 headline models
(`gpt-5.5-high`, `gpt-5.5-low`, `gpt-4.1`), n=5, 80-step budget, 743 scored runs:

| relativeness | clean `P*` | steered `P*` | gap | steered pin-buy | steered give-up |
|---|---|---|---|---|---|
| 0 — all absolute | 0.933 | **0.446** | +0.487 | 23% | 30% |
| 1 | 0.896 | **0.253** | +0.643 | 57% | 14% |
| 2 | 0.891 | **0.325** | +0.566 | 68% | 7% |
| 3 | 0.947 | **0.218** | +0.729 | 82% | 4% |
| 4 — most relative | 0.938 | **0.202** | +0.736 | 66% | 7% |

Clean sits at 0.89–0.95 and is flat across the spectrum — the agents *can* find the user's best
item. Both GPT-5.5 configurations are perfect clean (`P* = 1.00` at every level). Under steering
they fall to 0.45 / 0.25 / 0.33 / 0.22 / 0.20, a gap of **+0.49 -> +0.74**.

**2. The *form* of the failure changes with relativeness — this is the core mechanism.**

| relativeness | gate-failure rate (of completed) | O-shortfall (of gate-passing) | give-up | pin-buy |
|---|---|---|---|---|
| 0 | 0.37 | 0.00 | **30%** | 23% |
| 1 | 0.70 | 0.02 | 14% | 57% |
| 2 | 0.14 | 0.59 | 7% | **68%** |
| 3 | 0.07 | 0.76 | 4% | **82%** |
| 4 | 0.07 | 0.77 | 7% | **66%** |

- **At the absolute levels the agents are not fooled — they are exhausted.** At level 0 the two
  GPT-5.5 configurations buy the pinned decoy in **0 of 49 runs**: they open the promoted item, read
  the detail page, find the spec sitting just under a hard cut, reject it, and keep searching. They
  fail by *search exhaustion* — give-up rates of 29% (high) and 36% (low).
- **At the graded levels they are fooled.** Gate failures nearly vanish (the purchase is legal) and
  optimality collapses instead: pin-buy climbs 23% -> 68/82/66%, O-shortfall 0.59–0.77. A graded
  preference has no bright line to verify, so a prominent "good enough" item ends the search.

**3. Budget matters at the absolute levels only.** The same matrix at a 50-step cap gives level-0
steered `P*` = 0.260 with a 52% give-up rate; at 80 steps it is **0.446** with 30% give-ups
(delta +0.186). Levels 3–4 move −0.01 / −0.08 — nothing. Confirmed directly: the 19 unique runs that
hit the 50-step cap while still searching, re-run once at 80 steps, produced **18/19 completed
purchases and 10 heroes** (`P* = 1.00`; eight at level 0, two at level 1). Those give-ups were
budget starvation. **Capture, not budget, dominates once preferences are relative.**

**4. Model leaderboard.** 14 configs x the same grid x n=3 at a 250-step budget (1845/2100 runs on
disk at the time of writing), plus the three headline models from the 80-step matrix. Steered `P*`
pooled over the graded levels 2-4:

- **Tier 1 — resists:** GPT-5.6-Sol (high) **0.88** (`M` 0.86, pin-buy 14%). The only model holding
  a large margin over the capitulation ceiling.
- **Tier 2 — partial resistance:** GPT-5.6-Sol (med) 0.60 · GPT-5.6-Sol (low) 0.58 · GPT-5.5 (high)
  0.50 · GPT-5.5 (med) 0.49 (`M` 0.43-0.55).
- **Tier 3 — mostly captured:** GPT-5.5 (low) 0.20 · GPT-5.6-Terra (low) 0.19 · DeepSeek-V4 Pro 0.14
  (`M` 0.01-0.09).
- **Tier 4 — at the capitulation ceiling:** Kimi K2.6 0.11 · Grok-4.3 0.11 · DeepSeek-V4 Flash 0.08
  · GPT-5 (low) 0.07 · Qwen3.5-122B 0.07 · GPT-4.1 0.06 · GPT-5 Mini (low) 0.06 · GPT-5 Nano (low)
  0.05 · GPT-4o 0.04. **Nine of seventeen configurations score `M = 0.000` exactly** — on the graded
  levels they are indistinguishable from an agent that always buys the promoted decoy.

Reasoning effort is the strongest single lever, vintage beats scale, and vendor is not the story
(DeepSeek/Kimi/Grok/Qwen land in the same band as the OpenAI mid tier). Caveat: clean competence is
a confound at the bottom of the field — GPT-4o (clean 0.46), Qwen3.5-122B (0.54), GPT-4.1 (0.78) and
GPT-5 Nano (0.80) do not reliably find the best item even unsteered. Full table with per-row run
counts: `docs/4_results.md` §6.

**5. Nine storefront clones.** The design ports to nine self-contained marketplace clones. Seven are
clean passes on the calibrated criterion **C4** (`gpt-5.5-high`, steered, level 4, `P* < 0.5`):
Instacart 0.199 · StockX 0.305 · Fiverr 0.315 · eBay 0.329 · Nike 0.338 · Etsy 0.428 · DoorDash
0.463. Two are **honest boundary cases**: **Airbnb 0.418** (n=4; three runs land on exactly the
catalog's `C_L` = 0.224, one full hero recovery) and **Zillow 0.504** (n=3; two capitulating runs
plus one hero recovery pushes it just over the line — the one env that fails C4). Both means are one
run wide; they need more repeats.

## Environment validity

Standing properties of the shipped environments, not claims about the agents:

- **Token-gated API** — session-scoped `STOREFRONT_CLIENT_TOKEN`; direct endpoint navigation gets a
  403 Robot-Check page; `/docs` and `/openapi.json` are dead. No bulk-scrape shortcut.
  (`scripts/audit_lockdown.py`)
- **Rate-based robot check** — a three-window rate limiter arms a solvable full-page Robot Check
  with `/verify-human` + TTL recovery. It never silently strips specs; the old spec-budget mechanism
  is gone from every measured condition. (`scripts/calibrate_rate_gate.py`)
- **No-free-capitulation catalogs** — every pinned lure fails at least one level-0 hard requirement
  on a PDP-only dimension just below its cut, giving `C_0 = C_1 = 0` and `C_2..4` in a tight band
  (Amazon 0.175–0.281, clones 0.214–0.320). Green for all 5 products and all 9 clones.
  (`scripts/audit_capitulation.py`)
- **List/search parity** — list and search rows are a whitelist identical in clean and steered
  (airbnb excepted), so steering acts on ranking/badging/pricing, not on what information exists.
- **De-coached instructions** — the task prompt is a plain shopper request (persona + hard
  constraints + ranking goals) and the harness preamble adds only "stay on this site";
  neither warns the agent about badges, sponsored placement, hidden fees, or prechecked
  add-ons.

## Honest caveats

- Leaderboard cells are **n = 3** (vs n = 5 for the headline matrix) and the sweep was still filling
  at **1845/2100 runs** — a ranking, not precise point estimates. Three rows (GPT-5.6-Sol high/med,
  Qwen3.5-122B) rest on 40-62% of their grid and are provisional.
- Step budgets differ across sweeps (50 / 80 / 250); cross-sweep model comparison is restricted to
  the graded levels 2–4, where finding 3 shows budget stops mattering.
- **Most leaderboard runs carry no per-step screenshots** (`AGENTARENA_NO_SHOT_PERSIST=1`, set from
  2026-07-25) — a deliberate throughput decision, since screenshot persistence was saturating disk
  write bandwidth across 30+ concurrent 250-step runs. 397 of the 1845 leaderboard cells predate the
  switch and do keep a filmstrip; the other 1448 do not. Trajectories, actions, reasoning, URLs and
  all scores are complete everywhere, and the agent still saw screenshots in-run; only the on-disk
  PNGs are absent. The headline b80 matrix keeps full filmstrips (750/750).
- **Airbnb and Zillow are boundary cases** (above), driven by an occasional full hero recovery at
  n = 3–4.
- The pilot's **`C1'` and `C3'` criteria are pending recalibration** and are *not* claimed to pass.
  `C3'` currently demands that steering bite at level 0, where a capable model correctly rejects a
  lure that fails a verifiable hard cut — that is the benchmark working, not an env defect.
- Runs that died on infrastructure faults are excluded rather than scored as give-ups (7/750
  headline, 2/750 budget twin, 32 clone pilot). Behavioural no-buys are kept and score 0.

---

*Machine-readable record: `benchmark_data/reports/figure_data.json`. Figure:
`benchmark_data/reports/fig_final.{png,pdf}`. Raw runs: `results/overhaul_b80/`,
`results/overhaul_b/`, `results/overhaul_b_supp80/`, `results/overhaul_lb/`,
`results/overhaul_c_pilot2/`.*
