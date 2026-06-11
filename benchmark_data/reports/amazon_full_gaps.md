# Steering preservation gaps  —  results/amazon_full_r*

## gpt-4.1

P = mean preservation over scenarios (basket-folded). gap = P(clean) − P(cond).

| variant | clean | sponsored | ranking | promo | trust | scarcity | friction | drip | addon | combined |
|---|---|---|---|---|---|---|---|---|---|---|
| thresholded | 1.00 | 0.99 (Δ+0.01) | 1.00 (Δ+0.00) | 0.98 (Δ+0.02) | 1.00 (Δ+0.00) | 0.99 (Δ+0.01) | 1.00 (Δ+0.00) | 0.97 (Δ+0.03) | 0.97 (Δ+0.03) | 0.84 (Δ+0.16) |
| mixed | 0.82 | 0.86 (Δ-0.04) | 0.85 (Δ-0.03) | 0.85 (Δ-0.03) | 0.86 (Δ-0.04) | 0.76 (Δ+0.06) | 0.84 (Δ-0.02) | 0.74 (Δ+0.08) | 0.71 (Δ+0.11) | 0.70 (Δ+0.13) |
| graded | 0.73 | 0.49 (Δ+0.24) | 0.63 (Δ+0.10) | 0.59 (Δ+0.14) | 0.64 (Δ+0.09) | 0.71 (Δ+0.02) | 0.75 (Δ-0.02) | 0.56 (Δ+0.17) | 0.45 (Δ+0.28) | 0.61 (Δ+0.12) |

**Per-variant summary (clean P → strongest-steered P, max gap):**

- thresholded: clean 1.00 → steered 0.84  (max gap +0.16 via combined)
- mixed      : clean 0.82 → steered 0.70  (max gap +0.13 via combined)
- graded     : clean 0.73 → steered 0.45  (max gap +0.28 via addon)

## gpt-5.5

P = mean preservation over scenarios (basket-folded). gap = P(clean) − P(cond).

| variant | clean | sponsored | ranking | promo | trust | scarcity | friction | drip | addon | combined |
|---|---|---|---|---|---|---|---|---|---|---|
| thresholded | 1.00 | 1.00 (Δ+0.00) | 1.00 (Δ+0.00) | 1.00 (Δ+0.00) | 1.00 (Δ+0.00) | 1.00 (Δ+0.00) | 1.00 (Δ+0.00) | 0.99 (Δ+0.01) | 1.00 (Δ+0.00) | 0.99 (Δ+0.01) |
| mixed | 0.84 | 0.84 (Δ-0.00) | 0.84 (Δ+0.00) | 0.84 (Δ+0.00) | 0.85 (Δ-0.01) | 0.81 (Δ+0.03) | 0.85 (Δ-0.01) | 0.81 (Δ+0.03) | 0.82 (Δ+0.02) | 0.85 (Δ-0.01) |
| graded | 0.99 | 0.82 (Δ+0.17) | 0.77 (Δ+0.22) | 0.80 (Δ+0.19) | 0.86 (Δ+0.13) | 0.91 (Δ+0.08) | 0.84 (Δ+0.14) | 0.92 (Δ+0.07) | 0.92 (Δ+0.07) | 0.77 (Δ+0.22) |

**Per-variant summary (clean P → strongest-steered P, max gap):**

- thresholded: clean 1.00 → steered 0.99  (max gap +0.01 via combined)
- mixed      : clean 0.84 → steered 0.81  (max gap +0.03 via drip)
- graded     : clean 0.99 → steered 0.77  (max gap +0.22 via ranking)

