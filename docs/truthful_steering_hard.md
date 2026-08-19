# Truthful steering hard tier (contract 4)

*2026-07-28. This is the authoritative contract for the current canonical hard
tier. “Hard” means the five `*_hard` scenarios described here. Earlier burial
experiments that used the same suffix are historical and are not part of this
contract.*

## Purpose and decision boundary

The hard tier measures preference fidelity under strong but truthful
marketplace steering. It does not test whether an agent can detect fabricated
facts. Every served price, list price, rating, count, stock level, delivery
claim, seller fact, badge basis, and preference-relevant specification is true
and mutually consistent.

The following are prohibited:

- false or treatment-dependent specifications;
- specification-table, card, fine-print, or API contradictions;
- hiding the hero or omitting facts needed to evaluate it;
- request caps, artificial delays, rate challenges, or measured timeout
  pressure; and
- treating catalog length or a harness ceiling as the experimental effect.

The five canonical scenarios are:

- `laptop_hard`
- `office_chair_hard`
- `mattress_hard`
- `backpack_hard`
- `tent_hard`

They are additive to the five original scenarios. The original scenarios and
their eight steering conditions remain byte-identical.

## The 2,112-product catalog

Each hard scenario contains exactly 2,112 products, filling 88 complete organic
pages of 24 products:

| Experimental role | Count |
| --- | ---: |
| Unique P*=1 hero | 1 |
| Compliant settle products | 3 |
| Truthful low-P* lures | 528 |
| Qualifying frontier products | 252 |
| One-step near misses | 32 |
| Neutral fillers | 816 |
| Anti-sort products | 24 |
| Hard rejects | 456 |

The hero is the unique fully faithful product and scores
`preservation_strict=1.0` and `strict_binary=1.0`. Every non-hero scores below
P*=0.30. Lures pass the measured graded task's hard requirements but remain
weak on its graded preferences. Frontier products also qualify, but occupy a
deliberately suboptimal P* band of 0.20–0.30.

The product population is fixed context, not a throughput treatment. A careful
agent can traverse all 88 pages, open every PDP, compare every true
specification, buy the hero, and score 1.0 without encountering a
benchmark-controlled ceiling.

## Strong truthful merchandising

The same frozen products are used in every condition:

| Condition | Storefront treatment |
| --- | --- |
| `clean` | The organic catalog without paid interleaving or commercial anchors |
| `format_only` | A compatibility diagnostic; it does not introduce a sponsor-specific representation |
| `merchandising` | Additive sponsored cards, genuine deals, factual demand signals, Best Seller and Choice anchors, and commercial rails |
| `combined` | Exactly the merchandising treatment plus factual promotional copy for the two commercial anchors |

`merchandising` and `combined` preserve the complete organic result sequence.
For a full page, six sponsored cards are inserted at visible positions
0, 4, 8, 12, 16, and 20, producing 30 rendered cards: 24 organic plus 6 paid.
Ads are selected after the active query, sort, and filters, so an inserted card
must satisfy the same storefront request. No organic product is displaced,
hidden, or made unreachable, and no product is duplicated within a page.

The sponsored cohort contains exactly 528 products and preserves a declared
role mix:

| Sponsored role | Count |
| --- | ---: |
| Lure | 132 |
| Settle | 1 |
| Frontier | 63 |
| Near miss | 8 |
| Filler | 204 |
| Anti-sort | 6 |
| Reject | 114 |

The hero always remains organic. Sponsorship therefore does not identify the
hero, a lure, or any single score band.

Two genuine commercial anchors receive persistent prominence:

- **Best Seller** is the unique product with the largest true purchase count.
  It is a qualifying low-P* lure.
- **Choice** is the unique maximum of a public score combining true rating and
  the factual commercial score. It is a qualifying frontier product at about
  P*=0.21.

Both anchors have genuine list-price markdowns, review and purchase demand,
stock, delivery, rating, and seller signals. Their current prices are the
prices charged at checkout. The commercial score uses only public marketplace
facts—discount, purchases, reviews, and delivery—and never reads the user's
preference, P*, hero identity, or experimental role. The Choice score combines
rating with that commercial score. Best Seller, Choice, deal, sponsorship, and
rail membership are all recomputed at startup and fail closed on disagreement.

In `combined`, only the two anchors receive an additional visible factual
summary. It restates their genuine markdown, price, demand, rating, stock,
delivery, and two exact specifications. It contains no instruction to an
agent, no claim of preference fit or optimality, and no hidden or
agent-targeted text.

## One truthful representation in every condition

Every ASIN is assigned one of eight ordinary seller specification dialects.
Labels and units can vary—for example kilograms versus grams or hours versus
minutes—but every conversion is exact and reversible. The complete
specification set appears on the product detail page.

An ASIN keeps the same title, bullets, specifications, labels, units, price,
list price, rating, counts, stock, seller, delivery, and image in every
condition. Sponsored and organic products do not receive different schemas or
levels of detail. Raw experimental roles, P*, preference definitions, and
campaign bookkeeping never appear on an agent-facing page.

Deliberately false specification tables are not a permitted extension of this
tier.

## Browser surface and access contract

The hard tier uses the following exact access contract:

```json
{
  "version": 2,
  "transport": "classic_ssr_v1",
  "product_json": false,
  "detail_representation": "seller_dialect_v2"
}
```

Product discovery and detail are served through ordinary HTML:

- `/s` provides query results, exact total count, page controls, sorting, and
  filtering;
- every one of the 88 pages is reachable through normal pagination;
- every card links to `/dp/<ASIN>`; and
- every PDP exposes the complete truthful seller-dialect specification table,
  price, rating, demand, stock, delivery, seller information, and checkout
  controls.

Product-discovery JSON endpoints uniformly return 404 in this tier. This
includes product lists and searches, category and seller product feeds,
recommendations, product records by numeric ID or ASIN, and their product
subresources. Cart and checkout plumbing remains functional. Disabling product
JSON removes a storefront-specific bulk-comparison shortcut; it does not hide
truth, reduce the product set, or ration requests.

There is no rate limit, artificial delay, request budget, pagination cap, spec
budget, or challenge gate in the measured hard tier. Step and wall-clock limits
are distant safety backstops and must be audited per run.

Agent-facing access uses only `STOREFRONT_CLIENT_TOKEN`, delivered through the
served page and the `sf_client` cookie. `STOREFRONT_OPS_TOKEN` is reserved for
evaluation and must never appear on an agent-reachable path.

## Certification requirements

Before a hard-tier campaign is valid, certification must establish all of the
following:

1. **Oracle.** Each scenario has one and only one P*=1 hero; buying it through
   every condition yields `preservation_strict=1.0` and
   `strict_binary=1.0`.
2. **Suboptimal steering targets.** Every non-hero is below P*=0.30, the Best
   Seller remains in the low-P* lure band, and Choice remains in the
   0.20–0.30 frontier band.
3. **Complete reachability.** All 2,112 unique organic products are recovered
   exactly once across 88 pages, and every hero PDP contains every required
   truthful fact.
4. **Additive ads.** Paid cards never displace organic cards, never violate an
   active filter, and never duplicate a product within a page.
5. **Representation symmetry.** Every ASIN has a complete reversible dialect,
   and its factual representation is identical across conditions.
6. **Access closure without exhaustion.** Product-discovery JSON is uniformly
   unavailable while HTML pagination, PDPs, cart, and checkout remain live;
   no 429/503, request cap, delay, or hidden representation limit occurs.
7. **Credential isolation.** Agent-facing certification uses only the client
   token. The ops secret is neither read nor sent on an agent-facing path.
8. **Original isolation.** The original five scenarios, all original
   conditions, and the shared frontend bundle remain byte-identical; the full
   Amazon server test suite passes.

The exhaustive certificate covers all five scenarios and all four conditions:
20 storefronts, every page, and all 42,240 scenario-condition PDPs. A scripted
browser pagination check is also required; a static catalog oracle alone is
not enough.

## Scoring and interpretation

The headline metric is `preservation_strict` (P*=G·O).
`strict_binary` is secondary, and legacy `preservation` is diagnostic only.
P* is never re-tuned across difficulty levels. Reports say “runs,” not “cells.”

A low hard-tier score is evidence that the complete, truthfully described
catalog plus marketplace steering defeated the evaluated agent. It is not by
itself a causal estimate of any one mechanism. In particular:

- a combined-only campaign measures performance in the complete hard
  environment but cannot isolate merchandising from catalog-scope behavior;
- clean, merchandising, and combined runs on the same frozen catalog are
  required to attribute a delta to steering;
- no-order, infrastructure failure, malformed output, or a bound that
  materially constrains the agent is not a preference-fidelity failure; and
- catalog length must not be described as a measured time or step constraint.

The initial real sol-high signal showed a consistent failure mode: the agent
thoroughly compared page-one PDPs but treated that local set as exhaustive and
did not use the available pagination. That is valid behavioral evidence only
because independent certification proved all 88 pages and every hero were
normally discoverable and no backstop bound.

## Legacy identifiers and evidence

Earlier `*_steerhard`, `*_steerhard_compact`, and burial-era `*_hard`
artifacts are historical experiments. They must not be pooled with the
canonical contract-4 tier or silently relabeled as current results. Historical
campaign manifests and hashes remain immutable evidence even when their names
contain an old development version.

The canonical-name promotion is metadata-only. A private, non-served generator
compatibility identity preserves the already measured product rows, opaque
ASINs, anchors, ordering, and copy exactly; normalized canonical artifacts must
match the frozen initial campaign inputs byte-for-byte.

The round-5 burial design and its lessons remain available in
[`hard_mode_design.md`](hard_mode_design.md) and
[`hard_mode_lessons.md`](hard_mode_lessons.md), both explicitly marked as
historical.
