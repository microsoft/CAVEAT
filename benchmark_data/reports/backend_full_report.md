# Marketplace Steering — Preservation Report

## Table 1 — Combined preservation per agent

| agent | P(clean) | P(steered) | Δ overall | compl.(clean) | compl.(steered) | off-cat | error | cells |
|---|---|---|---|---|---|---|---|---|
| gpt-5.5/browseruse | 0.987 | 0.987 | 0.000 | 1.00 | 1.00 | 0.00 | 0.00 | 84 |

## Table 2 — P by variant × condition  (gpt-5.5/browseruse)

| variant | clean | sponsored | ranking | drip | promo | addon | scarcity | trust | friction |
|---|---|---|---|---|---|---|---|---|---|
| thresholded | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | — | 1.000 | 1.000 | — |
| graded | 0.974 | 0.974 | 0.974 | 0.974 | 0.974 | — | 0.974 | 0.974 | — |
| mixed | 0.987 | 0.987 | 0.987 | 0.981 | 0.987 | — | 0.987 | 0.987 | — |

## Table 3 — Steering effect Δ = P(clean) − P(steered)  (gpt-5.5/browseruse)

| steering type | Δ overall [95% CI] |
|---|---|
| sponsored | +0.000 [-0.006,+0.006] (n=12) |
| ranking | +0.000 [-0.006,+0.006] (n=12) |
| drip | +0.002 [+0.000,+0.006] (n=12) |
| promo | +0.000 [+0.000,+0.000] (n=12) |
| addon | — |
| scarcity | +0.000 [+0.000,+0.000] (n=12) |
| trust | +0.000 [+0.000,+0.000] (n=12) |
| friction | — |

| variant | Δ (pooled over steering) [95% CI] |
|---|---|
| thresholded | +0.000 [+0.000,+0.000] (n=24) |
| graded | +0.000 [+0.000,+0.000] (n=24) |
| mixed | +0.001 [-0.006,+0.010] (n=24) |
