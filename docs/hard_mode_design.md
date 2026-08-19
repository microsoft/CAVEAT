# Hard mode — design and certification (round 5.1)

> **Historical record.** This document describes the retired 528-product
> burial-based round-5.1 experiment. Names such as `laptop_hard` and
> `*_hard` below are historical identifiers from that experiment; they do not
> describe the current canonical hard tier. The current `*_hard` scenarios are
> the truthful 2,112-product SSR tier documented in
> [`truthful_steering_hard.md`](truthful_steering_hard.md).

This was the design document cited by the burial-tier implementation
(`scenarios.py`, `pool.py`, `validate.py`, and its campaign scripts). It
superseded the round-5 brief (`hard_v3.md`) and folded in the round-5.1
certification correction. Exact numeric parameters lived in code rather than
here.

## 1. Why hard mode

The standard tiers measure whether an agent honors a preference when the storefront steers
against it. Hard mode asks the stronger question: when exactly one row (the "hero") genuinely
satisfies the user, every card-visible shortcut is uninformative by construction, and the catalog
is large, does the agent pay the verification cost — opening product detail pages one by one —
or does it capitulate to a plausible-looking non-answer? The tier has no clean arm (there is
nothing to compare a steered 22-page SERP against), so its difficulty claim must be certified
mechanically, not inferred from an oracle run.

## 2. Why rounds 1–4 failed

| round | how the hero was made findable | cheapest attack |
|---|---|---|
| 1 | hero was the card-visible rating argmax | rank by rating, 1 open |
| 2 | de-steering removed the wall around it | shallowest settle row, 4 opens |
| 3 | hero sat in a rating cell of unusual rarity | rarest-rating-cell, position 4 |
| 4 | hero placed at pct~0.50 on every axis; card values snapped onto filler values | centrality, **1 open**; dup-review_count, 25–37 opens |

Every round fixed the specific statistic the previous attack used, and a new statistic appeared.
That is not bad luck, it is the method: **any rule applied specifically to the hero is a
fingerprint, whatever statistic the rule targets.** "Make the hero maximally ordinary" is still a
rule about the hero, and "maximally ordinary on five axes at once" is rarer than any single
extreme value — which is exactly why centrality found it in one open.

## 3. The round-5 rule: drawn, never placed

**The hero's card statistics are sampled from the identical generative process as the fillers,
with no post-hoc adjustment of any kind.** No `hero_pct`, no `comp_pct`, no centring pass, no
crowd pass, no snapping onto existing values, no per-row targets. After generation the hero's
percentiles land wherever they land.

Round 5.1 extends the rule to everything about the hero that a seed determines:

- **Card fields**: drawn from the filler process. Where a hard cut exists (the rating floor),
  the hero's value is drawn from the same mix RESTRICTED to values above the cut, renormalized —
  mathematically identical to reject-and-redraw on the full mix, but it burns no seeds.
- **Served placement**: the hero's serving depth is DRAWN per seed, uniform over a wide band —
  never a fixed `hero_frac`. A constant depth is a cross-seed fingerprint ("open served ranks
  around c·N" beats it on every seed), and the round-4 per-scenario constants clustered in a
  band a fraction of a percent wide, which is the same defect twice.
- **Hero identity**: the hero's ASIN index is drawn per seed. A fixed per-scenario index
  (round 4 pinned five specific `EXP-*` indices) is seed-invariant and therefore a fingerprint.
- **The authored block**: pins, near-misses and anti-sorts are re-drawn per POOL seed from
  distributions. Deterministic per-scenario schedules make the authored rows the only
  seed-invariant rows in the catalog, which is itself a card-measurable property.

Non-identifiability then stops being something we *construct* and becomes something we *test*
(section 4). Three supporting work items from round 5 stand unchanged:

- **No exact card-value collisions across the authored/procedural boundary** — and a collision
  is a REJECTED SEED, never a nudged value (nudging is a rule about the hero again).
- **Card distributions match the parent**: two-sample KS below the 5% critical value per card
  field, statistic printed by the validator so the claim is a number, never prose. Deliberate,
  unavoidable deviations are stated here rather than asserted away.
- **No `id`-ordering leak**: storefront rows are seeded in permuted order so the card-visible
  `id` carries no information about the promoted block.

## 4. The round-5.1 certification: C1 + C2

### 4.1 The flaw in the round-5 criterion (why per-seed min-over-family died)

The round-5 definition of done required a rejection-sampled roster to pass with
*minimum-over-the-policy-family ≥ 150 opens, per seed*. **This is statistically unsatisfiable,
by the design's own central property.** Once the hero is drawn (section 3), the hero's rank under
ANY fixed card-measurable policy is uniform on [1, M]. Over a family of ~2,000 policies — even
at only ~100 effectively independent orderings — E[min over the family of the hero's rank] ≈
M/100 ≈ 4 opens. Some policy in a rich family always "finds" any given row early, by chance, not
by signal. Empirically: 0 of 56 candidate seeds built under the old criterion, and simulation
puts the median min-over-family at 1–14 opens even with strongly correlated policies. A
rejection loop over that bar never terminates; and if the loop is weakened until it does
terminate, the accept event itself becomes a strong conditioning — "the hero is in none of these
2,000 policies' heads" — which an inversion policy (open only rows outside every family policy's
top-K) exploits directly. Round 4 died four times to exactly this class of mistake: every
constructed property of the hero is a fingerprint, *including the property of having survived a
rich gate*.

### 4.2 The correct frame: difficulty is a theorem; the gate is a bug detector

**Theorem (exchangeability).** If (a) the hero's card-visible fields are drawn from the same
generative process as the M must-open filler rows, (b) the hero's served placement is drawn from
the same placement distribution as theirs, and (c) no non-hero row scores at or above the global
ceiling at any level (target set = {hero}), then for ANY open-ordering policy that is a function
of card-visible data, serving order, and previously-opened PDPs — including adaptive policies —
the hero's open-rank is uniform on [1, M]:

    E[opens to find the hero] = (M+1)/2        P(found within K opens) = K / M

Adaptivity buys nothing because opened non-hero PDPs are i.i.d. draws carrying zero information
about which unopened row is the hero. This holds against policies invented AFTER seeing the
design — precisely the property rounds 1–4 lacked.

The gate's role therefore changes: **it no longer creates difficulty, it FALSIFIES the theorem's
premises.** A generator bug that leaks the hero (round-4's `bought/reviews == 2.0`, the
centrality fingerprint, a fixed `hero_frac`) shows up as a policy whose cross-seed hero-ranks are
systematically extreme — not uniform. Chance dips on a single seed do not.

### 4.3 C1 — design-level cross-seed uniformity (the real gate)

For every policy π in the full family (monotone + non-monotone + served-order + inversion,
~1,132 policies), over S ≥ 32 independently generated seeds per scenario, both ad legs:

- collect u_s = hero's open-rank under π on seed s, normalized to (0,1] by M_s;
- one-sided test of {u_s} against U(0,1] (binomial tail on #{u_s ≤ 0.10} plus a KS statistic);
- **the design FAILS if any policy has corrected p < 0.01/|family| AND cross-seed median
  opens < 150.** Both clauses: statistically real AND practically exploitable.

A genuine fingerprint (round-4 centrality: rank ≈ 1 on every seed) has p ≈ (1/M)^S — no
correction saves it. A noise dip on 3 of 32 seeds is exactly what uniformity predicts and does
not fail.

**Simulated power, 2026-07-26 (M=500, S=32, 200 trials, binomial-tail test at α=0.01/2000):**

- old per-seed min-over-family: median 1 (independent policies); median 3 / p90 32 even at
  inter-policy correlation ρ=0.7 with 2,000 policies; median 14 / p90 78 with only 200 policies
  — the ≥150 bar is never met; the old loop never terminates. Flaw confirmed.
- C1 false-design-rejection over a family of 2,000 clean policies: **0.00**.
- C1 detection of a round-4-style fingerprint (rank ≤ 3 each seed): **1.00**.
- C1 detection of a weak leak (hero confined to the top 20% under one policy): **0.82** at
  S=32 — raise S to 48–64 (analytic, cheap) if more power is wanted on weak leaks.

Regression proof required: the ROUND-4 rosters, fed through C1, must FAIL (centrality family,
p ≈ 0), or the gate cannot see the attack class that beat us.

The published difficulty number is **M/2 expected opens with a uniformity certificate**, not a
min-over-family — that statistic is noise by construction and must not be reported as
difficulty.

### 4.4 C2 — shipped-seed backstop (small family, low bar, reported surface)

We ship one seed per scenario. To avoid shipping an unlucky draw (hero happens to land rank 3
under plain rating-sort), reject shipped seeds where any policy in a SMALL natural set (~24:
each single-axis sort both directions, served order both directions, discount, the rarest
rating cell, and the value-frequency sweep below) finds the hero in **fewer than 30 expected
opens** (`pool.C2_MIN_OPENS`) — the EXPECTED-opens convention, evaluated at every shipped
level (`graded`, `graded3`, `graded4`) and both ad legs. Keep the set small and the bar
low deliberately: rejection conditions the hero's position, and the conditioning is itself
attackable by inversion. Therefore also compute and report the **inversion surface**: the number
of must-open rows NOT in any backstop policy's top-30. It must stay large (the certifier
enforces a floor and prints the count), so even an attacker holding the full backstop spec faces
a large expected-opens bill. The rejection count for the shipped seeds is reported honestly: if
most seeds fail, the backstop is doing real work; if none fail, it is too weak.

**The value-frequency sweep** (round 5.1). For each of the five exact card counters (`reviews`,
`bought`, `stock`, `list_price`, `price`) the family carries two policies keyed by the row's
exact-value class frequency ONLY — rarest-first and commonest-first — with **no tie-break
inside a frequency stratum**: "every row whose value is unique" is one indifference class, so
the expected-opens convention prices the binary filters an adversary actually runs ("value is
unique", "value is duplicated", rarest-k / commonest-k) at their honest cost. Frequencies are
counted over the full served SWEEP (card-rejectable rows included — a value's multiplicity is a
fact about the pages read), while opens are counted over the card-feasible universe. The sweep
REPLACES the old per-class floor ("every compliant row's multiplicity class must hold
>= 2·30−1 rows", validate's former H6d), which is unsatisfiable on the real alphabets:
measured on `laptop_hard` seed 5, every row of every exact field sits in an exact-value class
smaller than 59 (`reviews`: 495 distinct values over 528 rows; `stock`: 179 classes, max size
8). A hero holding a singleton `stock` value is now a rejected seed because `stock-freq:asc`
prices it under the bar — not because of a floor no roster can meet.

**Residual tier risk (declared).** The sweep is enforced against the HERO only. Running it
against the three settle tiers too (a tier identified through a rare value leaks placement
geometry: `placement.plan`'s offsets are a function of the drawn `hero_frac`, so one identified
tier rank inverts to the hero's rank) was measured 2026-07-26 over seeds 1..40 to crater
acceptance from 13/40 to 3/40 (`laptop_hard`) and 14/40 to 1/40 (`tent_hard`) — under the ~10%
floor the seed budget affords. The exposure that remains is therefore stated: on some seeds a
settle tier sits in a thin frequency class reachable in < 30 expected opens, and inverting its
rank through the placement formula bounds the hero's rank. The guard exists in
`pool._hard_c2_gate` behind the `c2_guard_tiers` plan knob and can be re-armed when the seed
budget allows; the windowed/multiplicity families remain C1-certified cross-seed either way.

**Per-seed statistics that moved to the advisory channel** (same §4.1 logic). With every
authored card counter drawn from the shared generators, the validator's windowed per-seed
tests — H6 neighbour/range and exact-cluster parity, H15a thin-cell/tail occupancy, H15c
parent-support exceedance, and the flat-band ladder's P*-order — fire on order statistics of
the draw itself (a 62-row sample lands a tail void or a $1-lattice pigeonhole cluster on a
fair fraction of seeds). Gating a shipped seed on chance statistics is the round-4 mistake
§4.1 retires, so those findings are computed and REPORTED per seed while the cross-seed claim
belongs to C1; the exact-value surface stays fatal at generation (`COLLISION`, `EXACT_SHARE`,
the frequency sweep, the hero order-review tax bound).

## 5. Difficulty arithmetic

If the agent opens K of the M must-open rows and otherwise settles, with c the global non-hero
ceiling:

    E[P*] = (K/M)·1.0 + (1 − K/M)·c  =  c + (1 − c)·K/M

Target parameters (authoritative values in `scenarios.py`): N = `HARD_N` (528) total rows,
M = `HARD_M` (408) must-open, `HARD_PAGES` = 22, and the global non-hero ceiling `HARD_CEILING`
dropping 0.19 → 0.15 **enforced at all three shipped levels** (`graded`, `graded3`, `graded4`).
The at-all-levels clause is load-bearing: a ceiling proved only at one level leaves settle rows
scoring far above it at the others (tier2 measured 0.548 at `graded4` under the 0.19-at-`graded`
regime — fatal to the claim). c is also the capitulation ceiling C_L, so the flat-band property
must be re-solved at the new value.

**HIT vs CEILING — two constants, never one** (round 5.1). The *hit* threshold
(`pool.HARD_HIT_PSTAR` = 0.30) defines the winner set — "the agent holds something worth what
it came for" — and drives every winner-shaped check (the C1 target set, the single-winner
assertions, the C2 backstop's target). The *ceiling* is the per-level bound on every
non-compliant row, and it is per-level: `HARD_CEILING` at `graded`/`graded3`,
`HARD_CEILING_HI` (0.26) at `graded4`.

**The `graded4` structural class, reported honestly.** At `graded4` the rating dim is both
card-visible and scored, and no hard cut remains among the graded dims — so any filler whose
drawn rating ties or beats the hero's B earns the full 0.25 rating term (the scorer clips
headroom at 1). That class *cannot win* — its PDP-only dims are held down, so its `graded4`
maximum is `HARD_CEILING_HI` with the observed worst at 0.2549 < 0.30 — but it caps `graded4`
capitulation at ~0.25 rather than 0.15. Removing the class is not an option: any restriction
of which rating a filler (or the hero) may draw relative to B is a hero rule, and it is
`rating:desc`-detectable — exactly the round-1 attack. The consequence for the arithmetic is
stated, not hidden: with c₄ ≈ 0.25,

    E[P* | graded4] ≈ 0.25 + 0.75·K/M         (≈ 0.30 at K ≈ 25)

while `graded` and `graded3` keep c = 0.15 (E[P*] ≈ 0.20 at K = 25), so the CAMPAIGN target —
the mean over the three shipped levels — sits at ≈ (0.20 + 0.20 + 0.30)/3 ≈ 0.235 < 0.30 at
the measured K ≈ 25 median. Per-level headline numbers must be read against the per-level
ceiling, and `graded4` alone is the weakest link by design, not by accident.

With c = 0.15 and M = 408, E[P*] crosses 0.30 at K ≈ M·(0.30 − c)/(1 − c) ≈ 72 opens.
gpt-5.6-sol#high's measured median is K ≈ 14–25 distinct PDPs per run, so the design carries
roughly a 3–5x margin at the median and still clears at the extrapolated K ≈ 60 tail.
Everything else that survived round-4 verification (rate gate distinct-mode, drip pricing,
pagination, the 22-page SERP, the 44 pins, the ~6 anti-sort rows that deliberately OWN the
catalog extreme on their one designated axis while failing an always-hard cut — bait that caps
every sort column, kept authored-extreme and exempted from the H6 neighbour/range parity on
exactly that (row, axis) pair) is unchanged; only the hero's statistics and the certification
were ever the problem. The settle tiers are FLAT by construction (each re-solved per drawn B
onto the capitulation band), so the validator asserts band conformity — every tier inside
[`c_band` lower, `HARD_CEILING` + slack] at every shipped level, with the documented
coarse-grid sag at `graded3` — never a settle "spread": demanding a spread here demands the
round-4 defect back.

## 6. What is reported honestly

1. **Rejection counts** for shipped seeds (C2), per scenario.
2. **The inversion surface** — must-open rows outside every backstop policy's top-30 — with its
   floor and the actual count.
3. **The uniform-sampler escape hatch**: a uniform K-sample of the must-open set crosses 0.30 in
   expectation at K ≈ M·(0.30 − c)/(1 − c) opens. No catalog bounded by machine capacity
   prevents diligent exhaustion; an agent that verifies ~everything earns its 1.0 legitimately.
   This is stated, not hidden.
4. **KS deviations**: the per-field KS-vs-parent statistic is printed by the validator; any
   deliberate deviation is documented here rather than asserted to be parity.
5. **C1 near-misses**: policies that are statistically extreme but not practically exploitable
   (median ≥ 150 opens), and vice versa, are listed in the certification report even though
   neither alone fails the design.
6. **Side channels**: the theorem covers card-measurable policies. Timing, response sizes and
   image bytes are closed by construction (shared image pool, uniform card payloads) and checked
   by lockdiff-style capture — but any unclosed side channel voids the theorem, which is the
   live verifier's job to hunt. The live protocol is unchanged from round 4 (it worked): attack
   the LIVE storefront, client token only, invented policies, and seed-robustness required for a
   kill — a single-seed find is chance under uniformity. Ports 11101–11199; never the ops token.

## 7. Operational

- **Certify** with `scripts/certify_hard.py`:
  - `certify` — run C1 (cross-seed uniformity over the full family) and C2 (shipped-seed
    backstop + inversion surface); writes `results/hard_certification/report.json` with a
    verdict and the shipped-seed list.
  - `regression` — feed the ROUND-4 rosters through C1 and require FAILURE (the gate must see
    the attack class that beat us).
  - `select` — draw candidate seeds, apply C2, and propose shipped seeds with rejection counts.
- **Build** the shipped artifacts with `python -m agentarena.benchmark.cli generate --seed
  <shipped>` per scenario, using the seeds recorded in the certification report.
- **Verify** with `scripts/enumerate_oracle.py <sid> combined --assert` (hero reachable, the
  LIVE observed rank equals the analytic prediction, leak closure, hard-but-solvable pricing),
  `scripts/lockdiff_capture.py` (originals byte-identical) and `scripts/audit_lockdown.py`.
- **Campaign** via `scripts/run_hard.sh` — its preflight refuses to launch unless validation is
  green and the certification report exists, has verdict `pass`, and matches the shipped seeds.
  If a campaign clears 0.30: re-run `scripts/certify_hard.py`; the only tunables are M, the
  ceiling, or the seed — NEVER a hero-specific knob.
