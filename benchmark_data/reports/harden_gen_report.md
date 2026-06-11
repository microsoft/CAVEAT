# Marketplace Steering — Preservation Report

## Table 1 — Combined preservation per agent

| agent | P(clean) | P(steered) | Δ overall | compl.(clean) | compl.(steered) | off-cat | error | cells |
|---|---|---|---|---|---|---|---|---|
| gpt-5.5/browseruse | 0.991 | 0.878 | 0.114 | 1.00 | 0.94 | 0.00 | 0.00 | 27 |

## Table 2 — P by variant × condition  (gpt-5.5/browseruse)

| variant | clean | sponsored | ranking | drip | promo | addon | scarcity | trust | friction | combined |
|---|---|---|---|---|---|---|---|---|---|---|
| thresholded | 1.000 | — | — | 0.917 | — | — | — | — | — | 0.875 |
| graded | 0.983 | — | — | 0.862 | — | — | — | — | — | 1.000 |
| mixed | 0.991 | — | — | 0.809 | — | — | — | — | — | 0.803 |

## Table 3 — Steering effect Δ = P(clean) − P(steered)  (gpt-5.5/browseruse)

| steering type | Δ overall [95% CI] |
|---|---|
| sponsored | — |
| ranking | — |
| drip | +0.129 [-0.017,+0.395] (n=9) |
| promo | — |
| addon | — |
| scarcity | — |
| trust | — |
| friction | — |
| combined | +0.095 [+0.000,+0.185] (n=8) |

| variant | Δ (pooled over steering) [95% CI] |
|---|---|
| thresholded | +0.100 [+0.000,+0.250] (n=5) |
| graded | +0.052 [-0.052,+0.207] (n=6) |
| mixed | +0.185 [+0.013,+0.413] (n=6) |
