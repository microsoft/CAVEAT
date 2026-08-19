# Environment Creation Playbook — Lessons from the Amazon Marketplace-Steering Build

*Hard-won lessons from adapting the Amazon clone + designing its catalog/steering/scoring, written so the
next 9 environments (airbnb, doordash, …) can skip the mistakes. Read this before starting a new env.*

---

## 0. What the benchmark actually measures (the north star)

**Preference fidelity under commercial steering.** A user states a request (budget + must-haves + "softer"
preferences); the agent shops a storefront and buys one item. We score how well the purchase matches the
user's *true* preference, and we compare a **clean** storefront against a **steered** one (sponsored
placement, burial, drip fees, prechecked add-ons, fake scarcity/trust, friction). The headline result is
the *gap*: how much steering degrades fidelity, at every relativeness level. Whether the gap *grows* with
relativeness is a **finding to be measured, not a design target** — the pre-2026-07 catalogs accidentally
baked that gradient into the metric (see §2.6), and the redesign exists to keep it out.

Every design decision below serves one goal: **make the only thing that changes between clean and steered
the manipulation itself** — so a fidelity drop is attributable to steering, not to a broken environment, a
structural impossibility, or a metric artifact.

---

## 1. The architecture in one screen

Two pipelines, two "faces" of the data.

**Offline generation** (`agentarena/benchmark/`, deterministic + LLM):
```
scenarios.py (authored core)
  → pool.py        generate_pool()      deterministic numbers/roles/prices  (the SCIENTIFIC CORE)
  → copy_gen.py    generate_copy()      LLM titles/bullets (validated: no leakage, no specs-in-title)
  → instruction_gen.py + faithfulness.py   LLM user request, judge-gated (3× primary + 1× cross-check)
  → steering.py    resolve_steering()   8 manipulation specs + `combined`
  → images.py      generate_images()    brand-neutral product photos (heroes unique, distractors reuse)
  → serialize.py   write_artifacts()    → benchmark_data/<env>/<scenario>/{catalog,pool,preferences,
                                            instructions,steering,attribute_schema,meta}.json + images/
```

**Runtime execution** (`agentarena/core/` + the env adapter):
```
registry.py  register_all_generated()         load catalog.json → Catalog objects (per subprocess)
registry.py  benchmark_tasks(scenario,variant) → TaskSpec[]  (instruction + preference DSL + graded_map)
Environment.seed_db(db, catalog, condition)    seed a fresh per-cell DB; apply steering for `condition`
Environment.start(port, task)                  launch server subprocess, wait health, after_start hook
  → agent shops in the browser (browseruse / playwright-mcp scaffold)
Environment.evaluate(handle, task)             read the order via API → Evaluation (outcome + scored later)
scoring/rescore.py --strict                    compute preservation_strict (P*) into every summary.json
```

**The two faces.** Keep them separate in your head:
- **Scientific core** = the numbers (prices, specs, roles, which item is the hero, steering params). Fully
  deterministic, seeded, validated. This is what the science rests on.
- **LLM skin** = titles, bullets, the natural-language instruction, images. Must be *faithful* to the core
  (never leak the answer, never contradict a spec) but is otherwise cosmetic.

**The Environment contract** (`core/environment.py`) — a new env implements only two methods; the base
class handles the server lifecycle:
| Method | Required | Contract |
|---|---|---|
| `seed_db(db_path, *, catalog, condition, params)` | **yes** | create a fresh DB seeded with the catalog, with the `condition`'s steering applied |
| `evaluate(handle, task)` | **yes** | read what the agent bought via the API, score vs `task.preferences`, return `Evaluation` |
| `server_env(catalog, condition, params)` | no | extra env vars for the server process |
| `after_start(handle, task)` | no | one-time prep once the server is up (e.g. snapshot pre-existing orders) |
| `server_command` / `server_module` / `health_path` | no | how/where to launch the server |

---

## 2. Catalog & data-generation lessons (the heart of it)

### 2.1 The role taxonomy is the vocabulary of difficulty
Every catalog item has a **role** and a **kind** (`scenarios.py`):
- **compliant / hero** — compliant-set best on every soft dim, in budget, satisfies all must-haves — but
  deliberately **not** the catalog extreme on any dim (see anti-sort below). *The right answer.* There
  must always be one whose P\* = 1, priced so its checkout total (×1.08 tax) stays under budget.
- **tier2/3/4** (compliant) — also satisfy the cuts but are graded-worse; the "honest compromises."
  Pareto-decorrelate them (one tier pricier than the hero, one the winner of a single soft dim) so
  price never proxies quality.
- **satisfice** (the pinned lures) — *flawless on the card* (in-budget `.99` price, passing title specs,
  solid rating) but each **fails ≥1 level-0 requirement on a PDP-only dim, just below its cut**, with
  decent values on its passing dims. This is the satisficing trap under the no-free-capitulation
  contract (§2.6): capitulation scores 0 while the flawed dim is hard, and a bounded ~0.2 once it softens.
- **distractor** — procedural filler to make the catalog realistically large.
- **anti-sort distractor** — owns the catalog extreme on ONE soft dim while failing an always-hard cut
  (over budget / wrong category / under-spec), so sorting by any single dim surfaces a must-reject item.
- **decoy** — the advertised/steered lure (what sponsorship/burial pushes you toward).
- **config-drip** — one item whose cheap base config is shown on the card but whose requirement-meeting
  upgrade (on the PDP) is over budget (no config satisfies both spec and budget).

If your new domain doesn't map cleanly onto these, **extend the taxonomy deliberately** — don't smear the
roles. The whole satisficing/steering story is built on "hero vs good-enough-lure vs advertised-decoy."

### 2.2 The VALIDITY INVARIANT — the single most important rule
> **Under *every* condition (clean AND every steered condition), there must exist a selectable, in-budget,
> graded-best item that scores P = 1.** Equivalently: **every cost that can push a purchase over budget must
> be AVOIDABLE** — a removable prechecked add-on, or a fee on a *decoy* the agent can decline — **never a
> mandatory charge on the faithful item.**

Why this is non-negotiable: we once put a mandatory drip fee on the hero so its all-in went over budget.
A *perfect* agent then scored 0.63, not 1.0 — the decline was **structural** (the optimum was deleted),
not behavioral. "Which item do we expect the agent to buy?" had no good answer. The owner rewound the whole
build. `validate.py` (`check_pool_explicit`) now asserts **P_oracle == 1 in every variant** and **no
mandatory fee on a compliant item** — run it and treat a failure as a hard stop.

### 2.3 Make steering bite via *budget headroom*, not impossible traps
The defensible way to make strong models fail honestly: a single **broad, realistic, prechecked add-on**
(warranty/protection plan — Amazon prechecks these widely) sized so it **tips a near-budget pick over
budget but leaves the cheap lures' all-in under budget**. Then:
- **A careless agent** that never re-totals the cart overspends on any near-budget purchase (hard
  gate → 0) — a real, continuous fidelity loss that is **fully avoidable** (uncheck → P\* = 1).
- **A satisficing agent** that settles on a cheap promoted lure isn't budget-punished — its loss
  comes from the lure's own failed requirement (§2.6), which is the honest mechanism for that error.
The add-on's harm is scored *through the budget preference*, not as a separate "didn't-uncheck-a-box"
rule (a standalone binary criterion just made careful agents abort). This is *emergent from price
calibration*, not a fee aimed at the hero — the hero itself is never fee'd, and its checkout total
(with tax) stays in budget.

**Price calibration recipe:** hero base price **near** budget (but in, incl. the 8% checkout tax),
tier items with **headroom below** budget (tier + add-on still ≤ budget), pins in
`[0.75×budget, budget)` (pin + add-on still < budget). Calibrate offline before any agent runs.

### 2.4 Hold the catalog CONSTANT across relativeness tiers (isolate the causal variable)
The relativeness variants (`thresholded → mixed → graded → graded3 → graded4`) are generated by
`ScenarioSpec.preference(variant)` softening the first *N* dims of `graded_order` into degrees while
`always_hard` dims (budget, must-haves) **never** soften. **The catalog does not change between tiers — only
the preference projection does.** This is the owner's standing rule: experiments hold everything constant
except the one variable. If you regenerate the catalog per tier you've confounded the result. Check for
confounds before redesigning anything.

### 2.5 The other catalog invariants — `validate.py` enforces six, at all five levels
1. **Validity** — oracle P\* = 1.0 at every level; the argmax is the unique hero (level ≥ 1).
2. **No free lunch** — every advertised row fails ≥1 level-0 requirement, with ≥1 failed dim PDP-only
   (never a title spec, price, or rating); no fully-compliant row is ever advertised.
3. **Capitulation ceiling** — C_L (max P\* over the combined pins) = 0 at levels 0–1, ≤ 0.35 at 2–4,
   spread ≤ 0.15 (flat — the ceiling must not track the level).
4. **Lure pricing** — hero × 1.08 checkout tax < budget (the oracle item must survive the order-review
   step); ≥ half the pins undercut the hero; every pin in `[0.75×budget, budget)`.
5. **Settle band** — the non-hero compliant tier spans a real spectrum at the deepest level (every P\*
   in [0.10, 0.70], spread ≥ 0.20).
6. **Anti-sort** — each graded dim's extreme outside the compliant set belongs to an item failing an
   always-hard cut.

Plus the standing checks: config-drip has **no** config satisfying *both* spec and budget, and no
steering spec puts a mandatory fee on a compliant item. `scripts/audit_capitulation.py` recomputes (3)
from the **committed** artifacts on every pass — treat it as CI.

### 2.6 The NO-FREE-CAPITULATION invariant — the confound we shipped once
The pre-2026-07 rosters let every pinned lure **pass every hard requirement** and sit exactly at the
cuts on the softenable dims. Under the strict metric that meant a fully-steered purchase scored
P\* = 1.0 at relativeness 0, then 0.75 / 0.50 / 0.25 / ≈0 as levels rose — the headline "gap widens
with relativeness" curve was substantially the *catalog's arithmetic*, not agent behavior (and the
old `verify_env.py` even asserted that shape). The fix is the roster above: every pin fails one
PDP-only requirement just below its cut, so the capitulation ceiling is 0 at levels 0–1 and a flat,
bounded ~0.2 at 2–4 — capitulating costs about the same at every level, and any measured decline is
behavior. **Lesson: before claiming a trend over an axis, compute the mechanical ceiling/floor of
your metric along that axis for the degenerate policy ("always buy the pinned item"). If the
degenerate policy already produces the trend, the catalog — not the agent — is generating it.**
"Not everything promoted is bad" realism lives in value-badged compliant *organics*, never in the
pins (a compliant pin ⇒ C_0 = 1.0 ⇒ free capitulation).

---

## 3. Distinct-site fidelity lessons

- **Each clone must look like its real brand. Do NOT share one template across envs.** A shared-template
  build was rewound. airbnb must read as airbnb, doordash as doordash — distinct layout, nav, card shape.
- **Headless Chromium has no emoji font** → emoji render as tofu boxes. **Bundle real product photos**
  (gpt-image-1 / loremflickr) and **FontAwesome locally**; don't rely on system fonts/emoji.
- **Keep the agent on the clone.** A hard nav-block / `prohibited_domains` stops the agent wandering to the
  real brand site. (Weak models *hallucinate* URLs — e.g. an agent navigated to `mercato.local/cart` and
  DNS-failed. You can't fix model hallucination, but clear in-site nav + a blocklist contains it.)
- **Anti-scrape must look like real anti-bot, not silent content edits.** The serving layer is
  session-gated (`_storefront/gate.py`): data endpoints require the client token only the served page
  carries (tokenless → full-page 403 Robot Check; `/docs`/`/openapi.json` dead), and abusive read
  rates trip a rolling-window limiter whose document response is a *solvable* robot-check
  interstitial (`/verify-human`, TTL auto-recovery). Human-speed browsing never trips it; scripted
  enumeration does in seconds (calibrate with `scripts/calibrate_rate_gate.py`; verify the whole
  endpoint matrix with `scripts/audit_lockdown.py`). The old silent per-session "spec budget" — strip
  PDP specs after N views, no signal to the agent — is retired from measured conditions: real sites
  never silently edit content, and the agent got no cue it was being limited. **Keep serving
  identical in clean and steered** (same card whitelist, same gate) — only the manipulation itself
  may differ, to isolate the steering effect.
- **Brand-neutral, leakage-free copy** (`copy_gen.validate_copy`): invented brands only (no Dell/Sony/…),
  the title is the *name only* (no specs, no adjectives, ≤ 7 words), all specs live in bullets, and no
  editorializing ("best value", "#1", "deal"). The instruction generator is judge-gated the same way.

---

## 4. Scoring & metric lessons (subtle, and they cost the most time)

### 4.1 The scorer prices the chosen ITEM (unit price), not the basket total — *know this cold*
`scoring/basket.py:chosen_price_all_in` uses the chosen item's **paid unit price** (+ its drip fee/add-on),
**not** the cart total. Consequence: **quantity/duplication bugs do not corrupt P\* for purchased items.**
This single fact saved a multi-day pointless re-run — we suspected a cart-doubling bug was depressing the
bars via "violations," but 621/742 violations already scored P*≥0.75 because the scorer never saw the
doubled total. **Before "fixing" outcomes by re-running, check whether the metric already absorbs the bug.**

### 4.2 The binary `outcome` field can disagree with P\* — trust P\*
`outcome` (compliant/decoy/violation/none) is set at *run time* from the (possibly buggy) basket; `P*` is
the *rescored* truth. A cell can read `outcome=violation` yet `preservation_strict=1.0` (bought the right
item, but the run-time binary check tripped on a doubled/over-counted total). **The figure uses P\*, not
`outcome`.** Use `outcome` only to detect `none` (no purchase).

### 4.3 The scores — ONE headline, the rest diagnostics
- **`preservation_strict` (P\*)** — **the headline, and the only reported fidelity metric.**
  P\* = G·O with the **unified per-variant gate** (identical semantics in `_storefront/scoring.py`,
  `benchmark/validate.py` and `rescore._must_haves(scenario, variant)`): G gates on every dim hard
  *at the current relativeness level* (any violation → 0); O is the mean of squared headrooms over
  the current level's graded dims, normalized over the fully-compliant set; O = 1 at level 0, so P\*
  is binary there. Faithful hero = 1.0, overspend/any hard miss = 0, satisfice = its bounded graded
  score. Sharp and defensible ("a hard budget is hard; an in-budget faithful option existed").
- `preservation` (P) — the LEGACY compensatory weighted-mean, written at run time. Diagnostic only:
  a violated hard cut only nudges it, so it must never be reported as fidelity.
- `preservation_cont` — the legacy soft-gate variant (slightly-over-budget decays instead of
  zeroing). Kept for comparability with historical runs; not reported.
- `vgeo` — geometric-mean aggregation (`scoring/strict_variants.py`); strictly harsher than P\*.
  Appendix/ablation only (a lower-bound sensitivity check on capitulation ceilings).
- **`rescore --strict` is REQUIRED after every run.** Fresh cells get `preservation` but `preservation_strict
  = None`. **A `None` P\* is silently DROPPED by the figure loader** (counted as neither valid nor none) →
  silent sample loss. Always `python -m agentarena.scoring.rescore --glob "results/<run>/*" --strict`.
  Note the **staleness guard**: rescore refuses cells whose recorded basket title doesn't match the
  current pool (ASINs are reassigned across catalog regenerations) — pre-regen runs come back `None`
  by design, not by accident.

### 4.4 The bail crux → DISENTANGLE the metric
A steered **bail** (none = no purchase) is genuinely ambiguous: it's part **resistance** (the agent refused
the lure — good) and part **failure** (it never bought the preferred item — bad). No single number cleanly
equals "fidelity." So **disentangle**:
- **Bars = fidelity *given completion*** (valid-only P\*, averaged only over cells that bought something).
- **Completion rate** shown as a *separate* per-model marker (how often it bought at all).
Read together: a model can choose well when it buys but rarely buy (Qwen: mid bar, low completion) vs buy
often but choose badly (GPT-4o: low bar, high completion). One number would hide that.

**None-handling traps:** valid-only alone = **survivorship bias** (a model that only completes the easy
cells looks great); none-as-0 = conflates navigation with choice and punishes weak navigators. The chosen
path: **disentangle + re-run env-bug nones** (with the bug fixed) so completion reflects capability, not
environment flakiness.

### 4.5 Aggregate with mean-of-means, NOT pooled-over-cells
When you collapse the 5 relativeness levels into one "aggregate" bar, use the **equal-weight mean of the 5
per-level means** (`mom`), not a pool of all valid cells. Pooling weights each level by *how many cells the
model completed there* — a high-bail model that completes mostly the easy levels gets its aggregate yanked
upward (Qwen ranked #1 at 0.72 pooled vs 0.45 true). This produced a figure where small per-level bars
summed to a *higher* aggregate — a dead giveaway. Equal-weight per level fixes it.

---

## 5. Environment-bug bestiary + how to hunt them

Steering legitimately raises the bail rate, but **environment bugs masquerade as bails/violations** and
must be removed first. Known bugs and the general method:

| Bug | Symptom | Root cause | Fix |
|---|---|---|---|
| **Cart quantity inflation** | "$1840 = 2 laptops", over-budget bails | repeated Add-to-cart did `quantity += n` (weak models re-click when no confirmation; harness retries clicks) | idempotent add: `quantity = max(existing, requested)` |
| **Blank/unresponsive cart** | cart renders empty after edit | usually *downstream* of the above (agent tries to remove the dup, UI breaks) | mostly resolved by the qty fix |
| **Multi-item basket** | 3 different items / 11 in cart | **model behavior** (adds many while comparing, never clears) — *not* an env bug; idempotent-add doesn't touch it | not fixable in env; it's a capability signal |
| **Hallucinated domain** | `mercato.local` DNS fail | **model error** | mitigate with in-site nav + `prohibited_domains` |

**The hunting method (reusable):**
1. Find the failing cells (`outcome == none`, or `violation` with low P\*).
2. **Grep their `run.log` for error signatures** (cart/checkout/4xx/5xx, "2 items", blank, traceback,
   out-of-stock, max-steps) and **quantify each** — don't fix the rare ones.
3. **Confirm the root cause in code**, not by guessing (read the actual route handler).
4. **Reproduce + verify with a live test** (spin the server, hit the endpoint) **and add a regression test**
   before declaring it fixed.
5. **Snapshot the data before any destructive re-run** (see §8).

**Validation guards before every eval**: `validate.py` (the six invariants of §2.5 — oracle P\*=1 at
every level, no-free-lunch on advertised rows, C_L bands, lure pricing incl. the hero tax check,
settle band, anti-sort; plus no config meets both spec & budget and no mandatory fee on a compliant
item) and `scripts/audit_capitulation.py` (C_L from committed artifacts). Treat any failure as a
release blocker.

---

## 6. Running the eval — infrastructure lessons

### 6.1 Model routing & the spec convention
- Spec strings: `"gpt-5.5#low"` (TRAPI, reasoning effort low), `"phyagi/gpt-5.5#low"` (PhyAGI gateway),
  `"gpt-4.1"` (non-reasoning). `ModelSpec.parse` (`core/models.py`) turns `#effort` into the recorded name
  (`gpt-5.5-low`) and the provider prefix into routing. **The recorded model name is identical regardless
  of provider/region** → a cell run via PhyAGI or TRAPI lands in the *same* directory, so you can re-route a
  model between providers without orphaning its existing cells.
- TRAPI needs `az login` (AzureCliCredential, ~1h tokens, auto-refreshed). PhyAGI needs a static key.

### 6.2 ENDPOINTS ARE VOLATILE — probe with a real chat call before every run
Do **not** trust stale region notes. In one week: PhyAGI's key **expired** (gpt-5.5 had to move to TRAPI);
TRAPI **decommissioned** `gpt-5.1` and `gpt-5.4` (404 in *every* region); `models.list()` 404s even when
chat works. **Probe each model you intend to run with an actual 1-token chat through its scaffold endpoint**
(`ModelSpec.parse(spec).openai_endpoint()` → `chat.completions.create`). Models that vanish are *frozen* —
keep their prior data, don't pretend you can re-run them.

### 6.3 Concurrency is bounded by RAM, not the API — and `/tmp` is RAM
- **`/tmp` is a tmpfs (RAM-backed).** Browser-use/chrome drop temp there, so tmpfs growth *is* RAM
  pressure. The old "~12–13 cell ceiling, 14 crashes it" was really a **RAM** ceiling disguised as a
  `/tmp` one. **Fix: `export TMPDIR=<disk path>`** (browser-use/chrome temp honor it) to move temp onto
  disk (886 G free) — this frees ~6 GB of RAM and roughly **doubles** the safe cell count.
- **Measure per-cell RAM from `free` deltas, not RSS.** Chrome's RSS (~1.4 GB/cell) double-counts shared
  pages; the true marginal cost (idle `used` vs N-cell `used`) was **~0.76 GB/cell** — so with temp on
  disk, 24 cells fit in ~15 GB of 31 GB. CPU is a non-issue (cells are network-bound; load ~1 on 22 cores).
- **The real ceilings, in order:** (1) RAM (~0.76 GB/cell), (2) the **browser-launch thundering herd** —
  N chrome instances launching at t=0 contend; the pool only herds the *first* batch (after that cells
  start one-at-a-time as others finish), so a big first batch ramps slowly but recovers, (3) TRAPI's
  ~5–6 concurrent/region. Use **PhyAGI as the overflow** for OpenAI models to keep TRAPI's 3 regions
  unsaturated; cross-family models (Qwen/Kimi/DeepSeek/grok/gpt-oss) can ONLY go on TRAPI.
- **Don't panic-diagnose a slow ramp.** The benchmark's stdout is block-buffered to the log (looks dead),
  and in-progress cells may show 0 completions for ~10 min when the remaining set is all hard cells
  (300–500 s each). Confirm health by **`-mmin -N` on run.logs + grepping for step markers** (`🎯`,
  `Step N`, `Clicked`), not by the buffered log or `summary.json` counts. (`-newermt "HH:MM"` is flaky.)
- Run a **janitor**: reap orphaned chrome (`ppid==1`) + delete temp dirs older than ~12 min in BOTH
  `/tmp` and `$TMPDIR`, every ~90 s. **`max_retries=16` with Retry-After backoff** (browseruse) self-heals
  TRAPI throttling, and **cell ordering interleaves models** so concurrent cells span regions naturally.

### 6.4 Run mechanics that bite
- **Resume skips any cell with a `summary.json`.** To re-run a `none`, **delete its directory first**, then
  re-run (the runner recreates missing cells, skips valid ones).
- **`--name` groups + `--repeats`:** cells live under `results/<run>/<name>_r<rep>/…`. If a model's existing
  cells were created under `--name mm_ds`, you **must** re-run it under the same `--name` or you create
  *duplicate* cells in a different directory. Map (name-group → models) before any re-run.
- **Graceful pause:** kill only the `benchmark.run` **parents** (`pgrep -f "agentarena[.]benchmark[.]run"`)
  and let in-flight `run_cell` workers drain. `TaskStop` tree-kills and aborts live cells.
- **Smoke-test 2 cells** (including the hardest variant, e.g. graded4/combined) before committing to a
  multi-hour run — it catches routing/seed/scoring breakage cheaply.

### 6.5 The retry-until-valid pattern (env-bug recovery)
To recover env-bug bails after fixing the bug: loop `delete none cells → re-run (recreates them) → repeat`
up to *K* times; a cell that flips to a purchase stops being deleted (kept), one that stays none after *K*
is left as none. Keep *K* small (3) — most env-bug nones flip on the first pass; the rest are genuine
weak-model bails that won't flip at temperature 0 and just cost wall-clock.

---

## 7. Model-specific gotchas

- **Vision:** cross-family models (Qwen, Kimi, DeepSeek, grok, gpt-oss) are often **text-only** → run
  browseruse **DOM-only** (`use_vision=False`; add the base name to `_KNOWN_NO_VISION` in `core/models.py`).
  The DOM carries enough; an isolated vision probe is unreliable, so default DOM-only and upgrade only when
  vision is confirmed.
- **Reasoning model + function tools + `reasoning_effort` → 400** ("use /v1/responses"). The playwright-mcp
  scaffold (function tools) routes reasoning models through the **Responses API**; browseruse parses JSON
  from *text* (no function tools) so it's unaffected. If you add a tool-calling scaffold, replicate the
  responses-API path.
- **Markdown-fenced JSON:** some models wrap output in ```json fences → strip a whole-string fence before
  parsing (browseruse patches this universally).
- **Degenerate quantized models:** Qwen-Int4 emitted malformed JSON (94% none, control-char loops, timeouts)
  → prefer the non-Int4 build.
- **Weak navigators** (some models bail by running out of steps) are a **capability signal, not a bug** —
  report them via the completion-rate axis with wide CIs, never as P*=0.

---

## 8. Methodology & rigor (process lessons)

- **Validity first.** A perfect agent must be able to score P=1 under *every* condition; otherwise the
  decline is structural, not behavioral. Prove it offline (oracle) before running a single agent.
- **Isolate the causal variable.** Change exactly one thing between compared cells (catalog constant across
  relativeness; serving — card whitelist, gate, rate limits — identical clean vs steered). Hunt confounds
  before redesigning.
- **Snapshot before destructive operations.** A retry once deleted the `none` cells' `run.log`s, erasing the
  evidence of *why* they bailed. `first_attempt_snapshot.json` (a complete pre-retry record) became the
  figure's source of truth. Snapshot first, always.
- **Save enough to reconstruct.** That snapshot omitted the *repeat index*, so individual cells couldn't be
  regenerated one-by-one. Record every key you might need to re-derive a result.
- **Examine before deleting/overwriting; find the *root* cause.** Read the actual handler/log; don't guess
  and don't mass-delete on a hunch.
- **Confirm every fix** with a live reproduction + a regression test (e.g. `tests/test_cart_idempotent.py`),
  and report outcomes faithfully (if a model is frozen because its endpoint died, say so).

---

## 9. Checklist for a NEW environment (airbnb, doordash, …)

1. **Model the preference.** What is the *budget*, the *must-haves* (hard cuts), and the *graded* dims that
   soften with relativeness? (airbnb: nightly price = budget; must-haves = dates/guests/location/amenities;
   graded = rating, distance, review count. doordash: total price = budget; must-haves = cuisine/dietary/
   delivery-radius; graded = ETA, rating, fees.)
2. **Author the catalog** with the role taxonomy + the **validity invariant** + the
   **no-free-capitulation contract** (§2.6: every pinned lure fails one PDP-only level-0 cut just
   below its threshold; anti-sort distractors own the extremes; hero de-extremed) + price calibration
   (hero near budget but tax-safe, tiers with headroom, pins in `[0.75×budget, budget)` with retail
   `.99` endings). One unique hero with P\*=1 in every variant.
3. **Map the 8 steering categories to the domain.** The "avoidable budget trap" generalizes cleanly: the
   prechecked add-on becomes airbnb's **cleaning/service fee** or doordash's **delivery+service fee** — a
   realistic, *avoidable-or-decline-able* cost that tips the near-budget hero over but leaves lures in.
   Sponsored/ranking/scarcity/trust/promo/friction map to "Featured listing", "Superhost/Top Rated" badges,
   "2 left at this price", inflated review counts, fake discounts, hidden filters.
4. **Implement the adapter** (`seed_db` + `evaluate`). Reuse the Amazon server shape if the domain is
   listing→detail→cart→checkout; otherwise build a domain server but keep the same DB-per-cell + health +
   API-readback contract.
5. **Wire the scorer:** unit-price all-in, hard gate (over-budget/missing must-have → 0), `graded_map` for
   the soft dims. Confirm `outcome` and P\* agree on a few hand cases.
6. **Run `validate.py` + `audit_capitulation.py`** — the six invariants (oracle P\*=1 at every level,
   advertised rows fail a PDP-only cut, C_L bands flat, lure pricing, settle band, anti-sort). Hard
   stop on failure.
7. **Generate copy + instructions** (faithfulness-gated) **and images** (distinct brand look, no emoji
   reliance, real photos).
8. **Probe endpoints → smoke-test 2 cells → scale** with the janitor at the ~12-cell ceiling.
9. **`rescore --strict` → disentangled figure** (bars = fidelity|completion, completion-rate markers,
   mean-of-means aggregate).

---

## 10. Pitfalls quick-reference

- ❌ Mandatory cost on the hero → structural decline (no P=1). ✅ Avoidable add-on tips a *near-budget* hero.
- ❌ Pinned lures that pass every hard cut → free capitulation at level 0, a mechanical relativeness
  gradient (the shipped confound, §2.6). ✅ Every pin fails one PDP-only cut; C_L flat and bounded.
- ❌ Silent spec-strip "anti-scrape" / clean-vs-steered data-exposure asymmetry. ✅ Token gate + rate-based
  solvable Robot Check, identical card whitelist in both conditions.
- ❌ Regenerating the catalog per relativeness tier → confounded. ✅ Catalog constant; only preference softens.
- ❌ Trusting `outcome` for the figure. ✅ Use P\* (scorer prices the *item's unit price*, not the basket).
- ❌ Forgetting `rescore --strict` → `None` P\* silently dropped. ✅ Always rescore after a run.
- ❌ Pooled aggregate across levels → survivorship-weighted ranking. ✅ Mean-of-means.
- ❌ One "fidelity" number for bails. ✅ Disentangle fidelity|completion + completion rate.
- ❌ Trusting stale endpoint/region notes. ✅ Probe with a real chat call every time; freeze dead models.
- ❌ >13 concurrent chrome cells. ✅ ~12 with a chrome/`/tmp` janitor.
- ❌ Re-running a model under the wrong `--name` → duplicate cells. ✅ Preserve the name-group mapping.
- ❌ Destructive re-run without a snapshot. ✅ Snapshot the complete record first.
- ❌ Shared site template across envs / emoji in headless. ✅ Distinct brand look + bundled real photos/fonts.

---

*Living document — append new lessons as the next environments surface them.*
