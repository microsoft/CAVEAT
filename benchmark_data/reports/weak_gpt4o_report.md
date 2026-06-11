# Marketplace Steering — Preservation Report

## Table 1 — Combined preservation per agent

| agent | P(clean) | P(steered) | Δ overall | compl.(clean) | compl.(steered) | off-cat | error | cells |
|---|---|---|---|---|---|---|---|---|
| gpt-4o/browseruse | 0.978 | 0.963 | 0.016 | 0.50 | 0.45 | 0.00 | 0.00 | 96 |

## Table 2 — P by variant × condition  (gpt-4o/browseruse)

| variant | clean | sponsored | ranking | drip | promo | addon | scarcity | trust | friction |
|---|---|---|---|---|---|---|---|---|---|
| thresholded | 1.000 | 1.000 | 1.000 | 0.917 | 1.000 | 1.000 | 1.000 | 1.000 | — |
| graded | 0.948 | 0.948 | 0.966 | 0.948 | 0.948 | 0.974 | 0.948 | 0.948 | — |
| mixed | 0.987 | 0.974 | 0.974 | 0.974 | 0.808 | 0.974 | — | 0.974 | — |

## Table 3 — Steering effect Δ = P(clean) − P(steered)  (gpt-4o/browseruse)

| steering type | Δ overall [95% CI] |
|---|---|
| sponsored | +0.000 [+0.000,+0.000] (n=3) |
| ranking | +0.000 [+0.000,+0.000] (n=5) |
| drip | +0.000 [+0.000,+0.000] (n=5) |
| promo | +0.000 [+0.000,+0.000] (n=5) |
| addon | +0.000 [+0.000,+0.000] (n=2) |
| scarcity | +0.000 [+0.000,+0.000] (n=4) |
| trust | +0.000 [+0.000,+0.000] (n=4) |
| friction | — |

| variant | Δ (pooled over steering) [95% CI] |
|---|---|
| thresholded | +0.000 [+0.000,+0.000] (n=12) |
| graded | +0.000 [+0.000,+0.000] (n=12) |
| mixed | +0.000 (n=4) |
