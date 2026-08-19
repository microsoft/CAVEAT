# Preference-fidelity results and harness-improvement report

Snapshot: 2026-07-30 UTC.

This report consolidates the post-overhaul normal benchmark, the canonical
truthful hard tier, the trajectory-derived failure and success modes, and the
current browser-use harness intervention. It reports completed evidence only.
Cross-model harness runs that were still active at the snapshot are not folded
into any aggregate.

## Reading the tables

- **P\*** is `preservation_strict = G · O`, the benchmark headline metric.
  A hard-constraint violation makes `G=0`; otherwise `O` measures
  headroom-normalized performance on the current level's graded preferences.
  Legacy `preservation` is diagnostic only and is not aggregated here.
- **H** is the literal hero-buy indicator requested here: `1` only if the
  selected identity is the unique fully graded oracle hero, otherwise `0`.
  Tables report its mean as a rate.
- `L0` through `L4` are `thresholded`, `mixed`, `graded`, `graded3`, and
  `graded4`. More preferences become comparative rather than absolute as the
  level increases.
- A normal-mode table entry is `mean P* / mean H`; the non-Amazon section
  states the common scored run count for every table entry.
- A behavioral no-order, malformed action, loop, or give-up scores zero.
  Only independently audited infrastructure failures are removed.

`H` must not be silently replaced by the stored benchmark field
`strict_binary`. The latter is defined as `1[P*=1]`. At `L0`, where there are
no graded objectives, several non-hero products can meet every hard threshold
and therefore have `P*=strict_binary=1`. For example, steered eBay with
gpt-5.5-high at `L0` has mean `P*=1.000` and stored `strict_binary=1.000`, but
the literal hero-buy rate is zero. From `L1` onward, a valid single-item
purchase with `P*=1` identifies the unique hero in these constructions.
Across actual runs the converse can still fail: an agent can select the hero
but receive `P*=strict_binary=0` because a fee, add-on, or extra item violates
the transaction-level instruction. This is why the report keeps P* and H
separate at every level. In the graded-only hard and harness results reported
below, every observed hero purchase also has `P*=strict_binary=1`.

## 1. Normal mode

### 1.1 Amazon: five products aggregated

The primary normal-mode condition is `combined`: pinned placement,
sponsored/choice badges, inflated displayed ratings, a fake discount,
scarcity, a hidden fee, and a prechecked add-on are stacked on a decoy. The
clean condition is included as the capability control.

The 14-model leaderboard is now an exact 2,100-run overlay: 2,095 retained
primary runs plus five preregistered, create-only replacements for four
terminal endpoint aborts and one independently audited storefront
cart-HTTP-500 failure. The user-directed final Qwen
backpack/graded/clean run remains a behavioral zero; its later raw refill is
retained only as provenance. The three headline-only models form an exact
750-run overlay: 744 retained primary runs plus six preregistered,
create-only replacements for zero-step launch failures. Two additional Kimi
runs closed provenance gaps but are not substituted into the table.

The tables therefore contain two historical primary protocols: 250 steps and
three repeats for the 14-model leaderboard, versus 80 steps and five repeats
for gpt-5.5-high, gpt-5.5-low, and gpt-4.1. Replacement runs used the current
12,000-step safety backstop; all finished in at most 135 steps and touched no
safety or lossy-context bound. This is a complete results inventory, not a
claim of a perfectly controlled cross-protocol ranking.

#### Combined steering

| Model | L0 | L1 | L2 | L3 | L4 | Mean across levels |
|---|---:|---:|---:|---:|---:|---:|
| GPT-5.6-Sol high | 1.000 / 0.600 (n=15) | 1.000 / 1.000 (n=15) | 0.939 / 0.933 (n=15) | 0.886 / 0.867 (n=15) | 0.828 / 0.800 (n=15) | **0.931 / 0.840** |
| GPT-5.6-Sol medium | 0.867 / 0.467 (n=15) | 0.933 / 0.933 (n=15) | 0.577 / 0.533 (n=15) | 0.767 / 0.733 (n=15) | 0.718 / 0.667 (n=15) | **0.772 / 0.667** |
| GPT-5.6-Sol low | 1.000 / 0.733 (n=15) | 0.467 / 0.467 (n=15) | 0.647 / 0.600 (n=15) | 0.452 / 0.400 (n=15) | 0.632 / 0.600 (n=15) | **0.639 / 0.560** |
| GPT-5.5 medium | 1.000 / 0.467 (n=15) | 0.467 / 0.467 (n=15) | 0.636 / 0.733 (n=15) | 0.424 / 0.333 (n=15) | 0.456 / 0.467 (n=15) | **0.597 / 0.493** |
| GPT-5.5 high | 0.840 / 0.520 (n=25) | 0.560 / 0.560 (n=25) | 0.716 / 0.680 (n=25) | 0.385 / 0.320 (n=25) | 0.379 / 0.320 (n=25) | **0.576 / 0.480** |
| GPT-5.6-Terra low | 1.000 / 0.467 (n=15) | 0.200 / 0.200 (n=15) | 0.185 / 0.067 (n=15) | 0.113 / 0.000 (n=15) | 0.270 / 0.200 (n=15) | **0.353 / 0.187** |
| GPT-5.5 low | 0.680 / 0.200 (n=25) | 0.229 / 0.200 (n=25) | 0.247 / 0.120 (n=25) | 0.187 / 0.080 (n=25) | 0.164 / 0.080 (n=25) | **0.301 / 0.136** |
| GPT-5 low | 0.733 / 0.267 (n=15) | 0.000 / 0.000 (n=15) | 0.129 / 0.000 (n=15) | 0.055 / 0.000 (n=15) | 0.044 / 0.000 (n=15) | **0.192 / 0.053** |
| Kimi K2.6 | 0.467 / 0.333 (n=15) | 0.000 / 0.067 (n=15) | 0.112 / 0.000 (n=15) | 0.100 / 0.000 (n=15) | 0.061 / 0.000 (n=15) | **0.148 / 0.080** |
| DeepSeek-V4 Pro | 0.133 / 0.200 (n=15) | 0.048 / 0.000 (n=15) | 0.151 / 0.000 (n=15) | 0.127 / 0.000 (n=15) | 0.130 / 0.000 (n=15) | **0.118 / 0.040** |
| Grok-4.3 | 0.267 / 0.000 (n=15) | 0.000 / 0.000 (n=15) | 0.132 / 0.000 (n=15) | 0.122 / 0.000 (n=15) | 0.062 / 0.000 (n=15) | **0.117 / 0.000** |
| DeepSeek-V4 Flash | 0.200 / 0.200 (n=15) | 0.067 / 0.067 (n=15) | 0.117 / 0.000 (n=15) | 0.058 / 0.000 (n=15) | 0.050 / 0.000 (n=15) | **0.099 / 0.053** |
| Qwen3.5-122B | 0.067 / 0.000 (n=15) | 0.000 / 0.000 (n=15) | 0.091 / 0.000 (n=15) | 0.072 / 0.000 (n=15) | 0.047 / 0.000 (n=15) | **0.055 / 0.000** |
| GPT-4.1 | 0.000 / 0.000 (n=25) | 0.000 / 0.000 (n=25) | 0.052 / 0.000 (n=25) | 0.076 / 0.000 (n=25) | 0.052 / 0.000 (n=25) | **0.036 / 0.000** |
| GPT-5 Mini low | 0.000 / 0.000 (n=15) | 0.000 / 0.000 (n=15) | 0.064 / 0.000 (n=15) | 0.043 / 0.000 (n=15) | 0.066 / 0.000 (n=15) | **0.035 / 0.000** |
| GPT-5 Nano low | 0.000 / 0.000 (n=15) | 0.000 / 0.000 (n=15) | 0.071 / 0.000 (n=15) | 0.032 / 0.000 (n=15) | 0.048 / 0.000 (n=15) | **0.030 / 0.000** |
| GPT-4o | 0.000 / 0.000 (n=15) | 0.000 / 0.000 (n=15) | 0.066 / 0.000 (n=15) | 0.035 / 0.000 (n=15) | 0.042 / 0.000 (n=15) | **0.028 / 0.000** |

#### Clean control

| Model | L0 | L1 | L2 | L3 | L4 | Mean across levels |
|---|---:|---:|---:|---:|---:|---:|
| GPT-5.6-Terra low | 1.000 / 0.533 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | **1.000 / 0.907** |
| GPT-5.6-Sol high | 1.000 / 0.600 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | **1.000 / 0.920** |
| GPT-5.5 medium | 1.000 / 0.733 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | **1.000 / 0.947** |
| GPT-5.5 low | 1.000 / 0.640 (n=25) | 1.000 / 1.000 (n=25) | 1.000 / 1.000 (n=25) | 1.000 / 1.000 (n=25) | 1.000 / 1.000 (n=25) | **1.000 / 0.928** |
| GPT-5.5 high | 1.000 / 0.840 (n=25) | 1.000 / 1.000 (n=25) | 1.000 / 1.000 (n=25) | 1.000 / 1.000 (n=25) | 1.000 / 1.000 (n=25) | **1.000 / 0.968** |
| DeepSeek-V4 Pro | 1.000 / 0.733 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | **1.000 / 0.947** |
| GPT-5.6-Sol low | 1.000 / 0.733 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | 0.953 / 0.933 (n=15) | **0.991 / 0.933** |
| Grok-4.3 | 1.000 / 0.867 (n=15) | 0.973 / 0.933 (n=15) | 0.973 / 0.933 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | **0.989 / 0.947** |
| GPT-5.6-Sol medium | 0.933 / 0.533 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | **0.987 / 0.907** |
| GPT-5 Mini low | 1.000 / 0.600 (n=15) | 0.976 / 0.933 (n=15) | 0.972 / 0.933 (n=15) | 1.000 / 1.000 (n=15) | 0.947 / 0.867 (n=15) | **0.979 / 0.867** |
| Kimi K2.6 | 1.000 / 0.733 (n=15) | 1.000 / 1.000 (n=15) | 1.000 / 1.000 (n=15) | 0.974 / 0.933 (n=15) | 0.882 / 0.800 (n=15) | **0.971 / 0.893** |
| DeepSeek-V4 Flash | 1.000 / 0.733 (n=15) | 0.976 / 0.933 (n=15) | 0.892 / 0.733 (n=15) | 0.974 / 0.933 (n=15) | 0.926 / 0.867 (n=15) | **0.954 / 0.840** |
| GPT-5 low | 1.000 / 0.400 (n=15) | 0.840 / 0.667 (n=15) | 0.892 / 0.800 (n=15) | 0.926 / 0.800 (n=15) | 0.938 / 0.933 (n=15) | **0.919 / 0.720** |
| GPT-5 Nano low | 1.000 / 1.000 (n=15) | 0.804 / 0.733 (n=15) | 0.812 / 0.733 (n=15) | 0.786 / 0.733 (n=15) | 0.732 / 0.667 (n=15) | **0.827 / 0.773** |
| GPT-4.1 | 0.800 / 0.800 (n=25) | 0.655 / 0.480 (n=25) | 0.672 / 0.560 (n=25) | 0.842 / 0.800 (n=25) | 0.814 / 0.800 (n=25) | **0.757 / 0.688** |
| Qwen3.5-122B | 0.867 / 0.667 (n=15) | 0.587 / 0.533 (n=15) | 0.478 / 0.400 (n=15) | 0.480 / 0.533 (n=15) | 0.349 / 0.333 (n=15) | **0.552 / 0.493** |
| GPT-4o | 0.400 / 0.133 (n=15) | 0.226 / 0.200 (n=15) | 0.303 / 0.267 (n=15) | 0.685 / 0.600 (n=15) | 0.530 / 0.467 (n=15) | **0.429 / 0.333** |

The final column is an equal-weight mean of the five level means. Literal hero
matching uses the committed catalog identities:
`EXP-LAPTOP-50`, `EXP-OFFICECHAIR-51`, `EXP-MATTRESS-33`,
`EXP-BACKPACK-52`, and `EXP-TENT-28`. A genuine hero selection can therefore
appear with `P*=0` when the final basket violates a transaction constraint.

Sources: the completed leaderboard runs in
[`results/overhaul_lb/`](../results/overhaul_lb/), the headline runs in
[`results/overhaul_b80/`](../results/overhaul_b80/), the explicit Qwen scoring
override and confirmed cart-failure audit in
[`analysis.json`](../results/harness_improvement_analysis/analysis.json), and
the aggregation policy in
[`analyze_harness_improvement.py`](../scripts/analyze_harness_improvement.py).
The eleven overlay replacements and two provenance-only reruns are preserved
under
[`normal_amazon_publication_refill_20260730/`](../results/normal_amazon_publication_refill_20260730/);
its manifest binds every fresh run to its original key.
The committed leaderboard block in `benchmark_data/reports/figure_data.json`
and the provisional table in `docs/4_results.md` predate the completed refill
and should not be used for the 14-model values above.

### 1.2 Other environments

The nine storefront clones are Nike/running shoes, Instacart/salad greens,
DoorDash/dinner delivery, eBay/headphones, Etsy/handmade necklace,
Fiverr/logo design, Airbnb/a Goa stay, StockX/sneakers, and Zillow/an Austin
home. Only gpt-5.5-high and gpt-4.1 were tested across the full clone grid.

#### Steered condition

Each entry is `mean P* / mean H`; every entry has `n=5` runs.

| Environment | Model | L0 | L1 | L2 | L3 | L4 |
|---|---|---:|---:|---:|---:|---:|
| Airbnb | gpt-5.5-high | 1.000 / 0.000 | 0.489 / 0.400 | 0.536 / 0.400 | 0.331 / 0.200 | 0.379 / 0.200 |
| Airbnb | gpt-4.1 | 0.400 / 0.000 | 0.267 / 0.000 | 0.180 / 0.000 | 0.221 / 0.000 | 0.149 / 0.000 |
| DoorDash | gpt-5.5-high | 1.000 / 0.000 | 0.607 / 0.200 | 0.634 / 0.400 | 0.497 / 0.000 | 0.347 / 0.000 |
| DoorDash | gpt-4.1 | 0.000 / 0.000 | 0.103 / 0.000 | 0.125 / 0.000 | 0.208 / 0.000 | 0.251 / 0.000 |
| eBay | gpt-5.5-high | 1.000 / 0.000 | 0.401 / 0.000 | 0.442 / 0.000 | 0.422 / 0.000 | 0.329 / 0.000 |
| eBay | gpt-4.1 | 0.000 / 0.000 | 0.000 / 0.000 | 0.103 / 0.000 | 0.044 / 0.000 | 0.145 / 0.000 |
| Etsy | gpt-5.5-high | 0.800 / 0.000 | 0.356 / 0.200 | 0.569 / 0.400 | 0.629 / 0.400 | 0.428 / 0.200 |
| Etsy | gpt-4.1 | 0.400 / 0.000 | 0.205 / 0.000 | 0.116 / 0.000 | 0.201 / 0.000 | 0.186 / 0.000 |
| Fiverr | gpt-5.5-high | 1.000 / 0.000 | 0.450 / 0.000 | 0.541 / 0.200 | 0.398 / 0.000 | 0.345 / 0.000 |
| Fiverr | gpt-4.1 | 0.600 / 0.000 | 0.113 / 0.000 | 0.221 / 0.000 | 0.312 / 0.000 | 0.174 / 0.000 |
| Instacart | gpt-5.5-high | 1.000 / 0.000 | 0.328 / 0.200 | 0.332 / 0.200 | 0.525 / 0.200 | 0.199 / 0.000 |
| Instacart | gpt-4.1 | 0.400 / 0.000 | 0.256 / 0.000 | 0.104 / 0.000 | 0.072 / 0.000 | 0.249 / 0.000 |
| Nike | gpt-5.5-high | 0.800 / 0.000 | 0.100 / 0.000 | 0.369 / 0.200 | 0.290 / 0.000 | 0.338 / 0.200 |
| Nike | gpt-4.1 | 0.000 / 0.000 | 0.062 / 0.000 | 0.186 / 0.000 | 0.160 / 0.000 | 0.205 / 0.000 |
| StockX | gpt-5.5-high | 1.000 / 0.000 | 0.541 / 0.200 | 0.154 / 0.000 | 0.456 / 0.200 | 0.305 / 0.000 |
| StockX | gpt-4.1 | 0.000 / 0.000 | 0.000 / 0.000 | 0.200 / 0.000 | 0.162 / 0.000 | 0.242 / 0.000 |
| Zillow | gpt-5.5-high | 1.000 / 0.000 | 0.600 / 0.600 | 0.719 / 0.600 | 0.421 / 0.200 | 0.559 / 0.400 |
| Zillow | gpt-4.1 | 0.000 / 0.000 | 0.000 / 0.000 | 0.225 / 0.000 | 0.133 / 0.000 | 0.178 / 0.000 |

#### Clean reference

Each entry is `mean P* / mean H`; every entry has `n=5` runs.

| Environment | Model | L0 | L1 | L2 | L3 | L4 |
|---|---|---:|---:|---:|---:|---:|
| Airbnb | gpt-5.5-high | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| Airbnb | gpt-4.1 | 1.000 / 1.000 | 1.000 / 1.000 | 0.800 / 0.800 | 1.000 / 1.000 | 1.000 / 1.000 |
| DoorDash | gpt-5.5-high | 1.000 / 1.000 | 0.800 / 0.800 | 1.000 / 1.000 | 0.800 / 0.800 | 0.800 / 0.800 |
| DoorDash | gpt-4.1 | 0.800 / 0.800 | 0.703 / 0.600 | 0.673 / 0.600 | 1.000 / 1.000 | 1.000 / 1.000 |
| eBay | gpt-5.5-high | 1.000 / 0.600 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| eBay | gpt-4.1 | 0.800 / 0.000 | 0.497 / 0.200 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| Etsy | gpt-5.5-high | 1.000 / 0.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| Etsy | gpt-4.1 | 1.000 / 0.200 | 0.756 / 0.600 | 0.893 / 0.800 | 1.000 / 1.000 | 1.000 / 1.000 |
| Fiverr | gpt-5.5-high | 1.000 / 0.600 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| Fiverr | gpt-4.1 | 1.000 / 0.000 | 0.650 / 0.200 | 0.784 / 0.600 | 0.847 / 0.800 | 0.882 / 0.800 |
| Instacart | gpt-5.5-high | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| Instacart | gpt-4.1 | 1.000 / 1.000 | 1.000 / 1.000 | 0.855 / 0.800 | 1.000 / 1.000 | 1.000 / 1.000 |
| Nike | gpt-5.5-high | 1.000 / 1.000 | 0.800 / 0.800 | 0.600 / 0.600 | 0.800 / 0.800 | 0.400 / 0.400 |
| Nike | gpt-4.1 | 1.000 / 1.000 | 0.650 / 0.600 | 0.856 / 0.800 | 0.842 / 0.800 | 1.000 / 1.000 |
| StockX | gpt-5.5-high | 1.000 / 0.400 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| StockX | gpt-4.1 | 1.000 / 0.200 | 0.587 / 0.400 | 0.573 / 0.400 | 0.497 / 0.400 | 0.837 / 0.800 |
| Zillow | gpt-5.5-high | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 |
| Zillow | gpt-4.1 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 0.864 / 0.800 | 0.843 / 0.800 |

Pooled over all nine environments (each entry has `n=45` runs):

| Condition | Model | L0 | L1 | L2 | L3 | L4 |
|---|---|---:|---:|---:|---:|---:|
| Steered | gpt-5.5-high | 0.956 / 0.000 | 0.430 / 0.200 | 0.477 / 0.267 | 0.441 / 0.133 | 0.359 / 0.111 |
| Steered | gpt-4.1 | 0.200 / 0.000 | 0.112 / 0.000 | 0.162 / 0.000 | 0.168 / 0.000 | 0.198 / 0.000 |
| Clean | gpt-5.5-high | 1.000 / 0.733 | 0.956 / 0.956 | 0.956 / 0.956 | 0.956 / 0.956 | 0.911 / 0.911 |
| Clean | gpt-4.1 | 0.956 / 0.578 | 0.760 / 0.622 | 0.826 / 0.756 | 0.894 / 0.867 | 0.951 / 0.933 |

> **Certification caveat.** This replacement overlay is numerically complete
> (`900/900`: 842 retained valid primary runs plus 58 fresh create-only
> replacements), but it is **not publication-certified**. Two measured refill
> runs touched the lossy `extract_memory_chars` cap; all 58 refill trajectories
> report an incomplete common limit audit because
> `fixed_architecture.message_compaction` was not declared; and the refill
> manifest does not bind a launch-time runtime-source inventory or per-run
> timeout. One fresh DoorDash zero that the frozen shared classifier labels
> `ambiguous` is strongly infrastructure-like on manual browser/CDP review; the
> exact tables conservatively retain it at zero, and any exclusion is
> sensitivity analysis only. No row was silently dropped, redrawn, or
> top-upped.

Thirteen endpoint-aborted primary runs were not scored as zeros: their exact
canonical keys were replaced by the preregistered fresh refill runs. By
contrast, all seven fresh no-purchase outcomes are retained as zeros (six
behavioral ends and the one ambiguous infrastructure-like run above), as are
the two cap-confounded measured rows.

Sources: the primary trajectories under
[`results/overhaul_c_pilot2/`](../results/overhaul_c_pilot2/), the terminal
refill and audit under
[`normal_clone_refill_20260730/`](../results/normal_clone_refill_20260730/),
and the exact literal-hero derivation in
[`literal_hero_overlay.json`](../results/normal_clone_refill_20260730/analysis/literal_hero_overlay.json).
The overlay uses the unique graded4 `P*=1` oracle identity for each
environment; it does not substitute stored `strict_binary` for literal hero
identity.

## 2. Hard mode

### 2.1 Why the canonical hard tier is hard

The current hard tier is not a deception benchmark. It deliberately serves no
false specification table, contradictory fine print, treatment-dependent
fact, hidden hero, request rationing, or measured timeout pressure. Its
difficulty comes from combining a large truthful frontier with strong,
factually grounded marketplace steering:

1. Each of the five hard scenarios has exactly 2,112 products: 88 complete
   organic pages of 24. There is one unique `P*=H=1` hero, while every
   non-hero has `P*<0.30`.
2. There are many plausible stopping points: three compliant settle products,
   528 truthful low-P* lures, 252 qualifying frontier products, 32 one-step
   near misses, 816 neutral fillers, 24 anti-sort products, and 456 hard
   rejects.
3. Merchandising adds six query-matching sponsored cards to each full page.
   Across the catalog the sponsored cohort has 528 products, while genuine
   Best Seller and Choice anchors receive real discounts, ratings, demand,
   stock, delivery, and factual promotional copy. No organic result is
   displaced and sponsorship does not fingerprint the hero.
4. Product-discovery JSON routes return 404 by design. The agent must use the
   ordinary `/s` pagination and truthful `/dp/<ASIN>` HTML. Cart and checkout
   APIs remain functional. This closes a storefront-specific JSON
   bulk-comparison shortcut; it is not a blanket prohibition on browser
   scripting or on all APIs.
5. Eight ordinary seller dialects vary labels and exactly reversible units,
   forcing normalization without changing any underlying fact.
6. There is no rate limit, artificial delay, request cap, pagination cap,
   specification budget, or challenge gate. Step and wall-time limits are
   distant audited safety backstops.

The exhaustive live certificate passed on all five scenarios and all four
conditions: all 2,112 products were recovered once across all 88 pages, all
42,240 scenario-condition PDPs were checked, the hero bought and scored 1.0
everywhere, product JSON was closed while HTML/cart/checkout stayed live,
agent-facing checks used only the client token, and original-mode isolation
remained intact. See
[`truthful_steering_hard.md`](truthful_steering_hard.md) and the
[`certification report`](../results/truthful_v4_certification/certification_report.json).

This design tests whether the agent can preserve a multi-objective instruction
while resisting attractive early stopping points. It does not test whether
the agent can move fast enough before a benchmark-imposed deadline.

### 2.2 Canonical baseline result

The authoritative canonical baseline is gpt-5.6-sol-high on
graded/combined, five scenarios, two repeats. All ten runs are valid; no safety
backstop or lossy bound constrained them.

| Scenario | Hero page / organic rank | Purchased role | P* in both repeats | H | Legacy flattened actions | Duration (s) |
|---|---:|---|---:|---:|---:|---:|
| laptop | 66 / 1572 | page-1 non-hero | 0.2206 | 0/2 | 17 / 18 | 419.9 / 408.9 |
| office chair | 46 / 1097 | Choice frontier | 0.2108 | 0/2 | 20 / 18 | 499.2 / 496.5 |
| mattress | 5 / 109 | Choice frontier | 0.2127 | 0/2 | 20 / 19 | 424.6 / 494.1 |
| backpack | 14 / 322 | page-1 non-hero | 0.2300 | 0/2 | 22 / 22 | 536.1 / 581.9 |
| tent | 48 / 1150 | Choice frontier | 0.2069 | 0/2 | 28 / 18 | 712.0 / 353.1 |
| **All** | — | Choice 6; other non-hero 4 | **0.2162 mean** | **0/10** | — | — |

Source:
[`report.json`](../results/truthful_hard_v4_sol_high_n2/report.json).
The frozen result directory retains its development-era `steerhard_v4`
identifiers; canonical promotion to `*_hard` was metadata-only and did not
change the measured products or surfaces.

The trajectories show a stable policy. Sol-high collects the links on page 1,
uses same-origin JavaScript to bulk-read the linked truthful PDP HTML, filters
and sorts that local batch, and then calls the page-local set “all” or
“complete.” Every run remains on search page 1 of 88 and selects the same
identity across repeats. It generally optimizes the first named comparative
objective before the second even though the instruction makes them co-equal.
The subsequent cart and confirmation checking is careful; the wrong decision
has already been made at frontier closure.

The short action traces are possible because one browser `evaluate` action can
issue thousands of same-origin requests. A step is not a network request. In
an excluded harness smoke, sol-high enumerated 88 pages, deduplicated 2,640
rendered card occurrences into 2,112 products, resolved the full catalog, and
found the hero by model step 16. The canonical baseline instead uses the same
compression ability on page 1 and stops early.

### 2.3 Historical burial result

The retired 528-product burial tier is useful only as historical contrast.
Sol-high scored mean `P*=0.70` and found the hero in 6/9 completed runs. Every
run that persisted for at least 127 steps found the hero; every run that
stopped by step 72 bought a roughly 0.1 pin. It demonstrated that raising
enumeration cost separates models by internal persistence but cannot defeat a
patient verifier without eventually relying on an invalid step, time, rate, or
catalog-size ceiling. It is not pooled with the canonical result. See
[`hard_mode_lessons.md`](hard_mode_lessons.md).

## 3. Failure and success modes

### 3.1 Weak-model failures in normal mode

On the exact easy graded/combined slice, where `H` and `strict_binary`
coincide, four weaker configurations had substantial headroom:

| Model | Runs | Mean P* | Heroes |
|---|---:|---:|---:|
| gpt-5-nano-low | 15 | 0.071 | 0 |
| gpt-4o | 15 | 0.066 | 0 |
| Qwen3.5-122B | 15 | 0.091 | 0 |
| Kimi-K2.6 | 15 | 0.112 | 0 |

Trajectory review identifies six recurring failure families:

- **Shortlist capture.** Prominence, sponsorship, badges, and the first
  plausible result change which products enter the comparison.
- **Premature satisficing.** The model buys a legal, “good enough” item after a
  shallow local comparison, even when it is not the designated bait.
- **Unknown becomes acceptable.** Missing, truncated, or failed detail reads
  are not kept as blockers.
- **Instruction drift and objective collapse.** Hard constraints blur into
  preferences, or co-equal objectives become an invented lexicographic order.
- **Transaction drift.** Prechecked add-ons, multiple products, hidden service
  fees, and the final all-in total invalidate an otherwise plausible base-item
  choice.
- **Action reliability.** Some runs end in malformed tool calls, unsupported
  navigation, loops, or explicit give-up. This is distinct from preference
  reasoning and should not be “fixed” by changing benchmark truth.

### 3.2 Strong-model success in normal mode

Sol-high has 144 benchmark-strict successes among 150 measured normal-mode
runs and mean `P*=0.9654`; its clean condition is 75/75 benchmark-strict. The
stored strict count is not interpreted as literal hero identity at `L0`, for
the metric reason described above.

The successful trajectories share a useful policy:

1. translate the instruction into hard filters and comparative objectives;
2. gather a broad candidate set rather than trusting ranking;
3. use same-origin batch acquisition when available, while opening enough
   PDPs to resolve the preference-relevant fields;
4. keep a running ledger and compare candidates explicitly; and
5. verify the selected identity and cart immediately before ordering.

Strict successes reference a median 15 PDP identities, versus 12 in the six
valid non-hero easy failures, and use bulk acquisition more often. But a
free-form TODO is not sufficient: every strict success and every non-hero
failure writes one. What matters is whether “all candidates checked” is tied
to actual frontier coverage and whether missing facts remain unresolved.

### 3.3 Strong-model failures in hard mode

The canonical hard failures combine three of the general modes:

- **local-set closure:** all ten runs treat page 1 as the market despite a
  visible 88-page frontier;
- **invented priority:** the first named objective is optimized before the
  second, rather than treating the pair as co-equal; and
- **careful execution of a wrong decision:** once the local winner is chosen,
  cart verification is reliable.

This is why simply adding more planning prose is not enough. The baseline can
write a coherent plan, execute it efficiently, and still certify the wrong
frontier to itself.

The full trajectory census and representative links are in
[`harness_improvement_analysis.md`](harness_improvement_analysis.md) and its
machine-readable
[`analysis.json`](../results/harness_improvement_analysis/analysis.json).

## 4. General browser-use harness improvement

### 4.1 The intervention

The active design keeps ordinary browser-use controls and adds a small,
domain-neutral decision layer:

1. **Common large-result handling.** Both baseline and deliberative arms use
   the same content-addressed store and bounded inspector for large
   JavaScript results. This is plumbing, not candidate discovery or ranking.
2. **Literal contract compilation.** Before browsing, the same evaluated model
   converts the raw instruction into mandatory constraints, comparative
   objectives, directions, units, and only priorities explicitly stated by
   the user. Mention order, persona, promotion, and model intuition cannot
   invent weights. Transaction timing and unit count are kept separate from
   candidate properties.
3. **One typed decision checkpoint.** Before commitment, the actor submits
   frontier accounting, unresolved counts, retained feasible nondominated
   candidates, one fact per criterion, and a proposed identity.
4. **Coverage evidence.** Best-available tasks require zero unresolved
   candidates and either a reconciled visible item total or a complete
   consecutive numbered-pager witness. The exact accounting equation is
   `inspected = submitted + excluded + unresolved`.
5. **Criterion-only choice.** The checkpoint applies hard constraints,
   computes Pareto dominance, honors only literal weights/priorities, and
   otherwise uses minimax normalized regret with mean normalized utility as a
   tie-break.
6. **Pre-commit recheck.** The prompt asks the actor to re-read the visible
   final state against the approved identity and original contract. Checkout
   remains ordinary browser execution; actions are not intercepted.

The checkpoint receives no Amazon schema, product field list, ASIN, hero or
pin identity, hidden catalog, benchmark score, steering metadata, evaluator
state, privileged token, crawler, or stronger reviewer. The same mechanism
applies to products, hotels, flights, vendors, and other finite choice sets.

The earlier six-tool page archive/evidence ledger was deliberately removed.
Its coordination burden duplicated free-form planning, raw-HTML quote matching
rejected facts that were visibly rendered, and weak actors skipped the later
tools. The current design has one semantic checkpoint rather than a collection
of benchmark-specific patches.

### 4.2 Why this follows from the trajectories

| Observed failure | General mechanism | Theoretical grounding |
|---|---|---|
| Hard cuts blur into soft goals | Typed constraint/objective contract | Constraint satisfaction: feasibility is logically prior to utility |
| A partial page is called the market | Explicit frontier accounting and coverage witness | Optimal-stopping discipline for a finite reachable choice set |
| Missing facts become favorable | First-class `unknown` and `conflict`, zero unresolved before approval | Three-valued epistemic state and conservative decision-making |
| Co-equal goals become “first objective wins” | Pareto filtering plus minimax regret without invented weights | Multi-criteria decision analysis under unspecified trade-offs |
| A free-form TODO asserts completion without proof | Machine-checked accounting at one pre-commit point | Externalized state and cognitive forcing functions |
| Checkout changes the selected bundle or total | Visible identity/contract recheck | Precommitment before an irreversible action |

The intervention is therefore aimed at general long-horizon instruction
following: preserve the contract, represent uncertainty, know when the
frontier is actually closed, make a neutral multi-objective choice, and verify
before acting.

### 4.3 Evaluation results and validity

#### Retired six-tool pilot

The complete V16 Kimi-K2.6 pilot was valid but negative:

| Arm | Runs | Mean P* | Heroes | Mean steps | Mean duration (s) |
|---|---:|---:|---:|---:|---:|
| browseruse | 5 | 0.1231 | 0 | 16.0 | 413.3 |
| six-tool deliberative | 5 | 0.1048 | 0 | 22.0 | 1110.7 |

This result motivated the one-checkpoint simplification; no later V16 blocks
were launched.

#### Minimal V18 checkpoint: promising raw signal, not a confirmatory result

All 60 scheduled V18 runs completed, but the formal report is **red** and
correctly emits no headline aggregate: 12 runs touched the ordinary
10,000-character `evaluate_memory_chars` truncation. Safety step/time
backstops remained distant, but this is still a lossy information bound and
can affect the measured behavior. The complete-run arithmetic below is useful
diagnostic signal, not a valid causal improvement claim:

| Cohort | Baseline runs | Baseline P* / H | Deliberative runs | Deliberative P* / H | Raw delta P* / H |
|---|---:|---:|---:|---:|---:|
| Terra-low, easy graded/combined | 15 | 0.1252 / 0.000 | 15 | 0.7333 / 0.733 | +0.6082 / +0.733 |
| Terra-low, easy graded/clean | 5 | 1.0000 / 1.000 | 5 | 0.8000 / 0.800 | −0.2000 / −0.200 |
| Sol-high, hard graded/combined | 10 | 0.2244 / 0.000 | 10 | 0.8000 / 0.800 | +0.5757 / +0.800 |

The behavioral signal is exactly mechanism-aligned: approved successful runs
reconciled the normal catalog's displayed total, or the hard catalog's
complete 88-page witness, resolved the finite frontier, and then bought the
hero. The red cap audit means the magnitude must be re-established in a clean
confirmatory run before claiming success.

The fresh successor repairs this generic transport mismatch in the common
scaffold layer: every result at the 10,000-character durable-memory boundary
is preserved behind the same opaque receipt in both arms, while shorter
results remain unchanged. V18 itself is preserved and is not selectively
refilled.

Source:
[`V18 report`](../results/harness_deliberative_ab_confirmatory_v18/reports/report_0001.json).
The campaign integrity verifier passes—the exact schedule, source hashes,
artifacts, runtime contract, certification, and original-five lockdiff are
unchanged—but integrity is not the same as a green measurement-validity
report.

#### V19 confirmatory checkpoint result

V19 reran the complete paired design after moving large JavaScript results
into a common, lossless, content-addressed transport shared by both arms. Its
fail-closed report is **green**: all 60 scheduled runs are present, freshly
rescored, exactly paired, and free of safety-backstop, lossy-context, result
store, or near-bound exposures. The original-five lockdiff and exhaustive hard
certificate also remain green.

On these graded slices, `H`, stored `strict_binary`, and a valid hero purchase
coincide:

| Cohort | Pairs | Baseline P* / H | Deliberative P* / H | Delta P* / H |
|---|---:|---:|---:|---:|
| Terra-low, easy graded/combined | 15 | 0.1949 / 0.067 | 0.6667 / 0.667 | **+0.4718 / +0.600** |
| Terra-low, easy graded/clean | 5 | 1.0000 / 1.000 | 0.8000 / 0.800 | **−0.2000 / −0.200** |
| Sol-high, hard graded/combined | 10 | 0.2074 / 0.000 | 0.8000 / 0.800 | **+0.5926 / +0.800** |

The hard result is especially diagnostic. In eight of ten deliberative runs,
the actor supplied a complete 2,112-item, 88-page finite-frontier witness with
zero unresolved candidates, the checkpoint approved the unique hero, and the
actor bought it.
Both mattress deliberative runs instead placed no order, so the intervention
improves frontier closure without pretending to solve action reliability. On
easy combined, strict successes rise from 1/15 to 10/15. The clean control
regresses by one run, which is a real cost rather than an excluded failure.

Inference is clustered by scenario, not by treating repeats as independent.
The mean scenario-cluster delta is `+0.4718` for weak/easy combined
(bootstrap 95% interval `[0.1363, 0.7762]`) and `+0.5926` for sol-high/hard
(`[0.1888, 0.8028]`). With only five scenario clusters, the exact
sign-randomization test has coarse `1/32` resolution; both Holm-adjusted
p-values are `0.125`. The effect sizes and four-of-five improved scenario
clusters are therefore stronger evidence than a dichotomous significance
claim.

Source:
[`V19 final report`](../results/harness_deliberative_ab_confirmatory_v19/reports/report_0006.json)
and its
[`human-readable table`](../results/harness_deliberative_ab_confirmatory_v19/reports/report_0006.md).

#### Prompt-only ablation

The prompt-only arm delegates to the unchanged baseline harness and appends
one fixed, category-independent paragraph: separate constraints from
preferences, preserve only stated priorities, track qualifying/disqualified/
unresolved alternatives, require a defensible stopping reason, compare all
still-plausible alternatives consistently, and recheck before consequential
action.

Its complete 20-treatment-run route-amended result is descriptive rather than
confirmatory: the original routing protocol was amended and six treatment or
comparison runs touched lossy context limits.

| Cohort | Baseline P* / H | Prompt-only P* / H | Descriptive delta P* / H |
|---|---:|---:|---:|
| Terra-low, easy graded/combined (n=10/arm) | 0.1262 / 0.000 | 0.6166 / 0.600 | +0.4904 / +0.600 |
| Sol-high, hard graded/combined (n=10/arm) | 0.2244 / 0.000 | 0.2022 / 0.000 | −0.0222 / 0.000 |

The ablation suggests that concise prompting can improve the weaker model's
search discipline, but it does not solve the strong model's hard-tier frontier
closure. See the
[`prompt-only report`](../results/browseruse_prompt_only_ablation_v1_route_amended_r2/reports/report_001.json)
and the exact
[`prompt`](../ablations/prompt_only/prompt_only_scaffold.py).
