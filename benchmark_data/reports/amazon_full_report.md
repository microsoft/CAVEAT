# Marketplace Steering — Preservation Report

## Table 1 — Combined preservation per agent

| agent | P(clean) | P(steered) | Δ overall | compl.(clean) | compl.(steered) | off-cat | error | cells |
|---|---|---|---|---|---|---|---|---|
| gpt-4.1/browseruse | 0.851 | 0.778 | 0.073 | 0.94 | 0.83 | 0.05 | 0.00 | 480 |
| gpt-5.5/browseruse | 0.943 | 0.893 | 0.050 | 1.00 | 1.00 | 0.00 | 0.00 | 480 |

## Table 2 — P by variant × condition  (gpt-4.1/browseruse)

| variant | clean | sponsored | ranking | drip | promo | addon | scarcity | trust | friction | combined |
|---|---|---|---|---|---|---|---|---|---|---|
| thresholded | 1.000 | 0.986 | 1.000 | 0.975 | 0.976 | 0.974 | 0.986 | 1.000 | 1.000 | 0.845 |
| graded | 0.732 | 0.489 | 0.630 | 0.562 | 0.594 | 0.453 | 0.711 | 0.642 | 0.755 | 0.615 |
| mixed | 0.822 | 0.864 | 0.853 | 0.737 | 0.850 | 0.709 | 0.760 | 0.860 | 0.841 | 0.695 |

## Table 3 — Steering effect Δ = P(clean) − P(steered)  (gpt-4.1/browseruse)

| steering type | Δ overall [95% CI] |
|---|---|
| sponsored | +0.234 [+0.106,+0.386] (n=7) |
| ranking | +0.101 [+0.060,+0.164] (n=10) |
| drip | +0.212 [+0.030,+0.370] (n=11) |
| promo | +0.196 [+0.101,+0.314] (n=10) |
| addon | +0.206 [+0.074,+0.318] (n=11) |
| scarcity | +0.129 [+0.091,+0.184] (n=9) |
| trust | +0.177 [+0.098,+0.267] (n=9) |
| friction | +0.085 [-0.002,+0.123] (n=11) |
| combined | +0.308 [+0.180,+0.536] (n=10) |

| variant | Δ (pooled over steering) [95% CI] |
|---|---|
| thresholded | +0.031 [+0.024,+0.036] (n=29) |
| graded | +0.308 [+0.128,+0.536] (n=36) |
| mixed | +0.174 [+0.108,+0.220] (n=23) |

## Table 2 — P by variant × condition  (gpt-5.5/browseruse)

| variant | clean | sponsored | ranking | drip | promo | addon | scarcity | trust | friction | combined |
|---|---|---|---|---|---|---|---|---|---|---|
| thresholded | 1.000 | 1.000 | 1.000 | 0.994 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.992 |
| graded | 0.988 | 0.820 | 0.767 | 0.922 | 0.802 | 0.922 | 0.908 | 0.856 | 0.844 | 0.771 |
| mixed | 0.842 | 0.844 | 0.838 | 0.811 | 0.840 | 0.819 | 0.813 | 0.854 | 0.849 | 0.851 |

## Table 3 — Steering effect Δ = P(clean) − P(steered)  (gpt-5.5/browseruse)

| steering type | Δ overall [95% CI] |
|---|---|
| sponsored | +0.042 [+0.003,+0.096] (n=12) |
| ranking | +0.076 [+0.041,+0.111] (n=12) |
| drip | +0.032 [-0.016,+0.080] (n=12) |
| promo | +0.092 [+0.045,+0.128] (n=12) |
| addon | +0.030 [-0.029,+0.102] (n=11) |
| scarcity | +0.021 [-0.049,+0.079] (n=12) |
| trust | +0.076 [+0.041,+0.111] (n=12) |
| friction | +0.045 [-0.034,+0.111] (n=12) |
| combined | +0.046 [-0.040,+0.132] (n=12) |

| variant | Δ (pooled over steering) [95% CI] |
|---|---|
| thresholded | +0.002 [+0.000,+0.004] (n=36) |
| graded | +0.155 [+0.095,+0.214] (n=36) |
| mixed | -0.004 [-0.129,+0.096] (n=35) |


wrote results/amazon_full_all/_report/report.md
