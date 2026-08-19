# Amazon-parity hardening for the eight non-Amazon environments

This is the serving and catalog contract for Airbnb, DoorDash, eBay, Etsy,
Fiverr, Instacart, Nike, and StockX. It replaces their previous standard
catalogs in place. Zillow is not part of this benchmark matrix.

The target is the difficulty structure of the five **standard** Amazon
scenarios, not Amazon's 2,112-item hard tier. Difficulty comes from realistic
choice breadth, promoted low-fidelity alternatives, deep-but-reachable organic
results, and rate-accounted detail inspection. It does not come from false or
silently missing product specifications.

## Frozen contract

- 74 shopper choices per environment: 6 advertised decoys, 4 compliant
  products, and 64 distractors. Checkout services are transaction lines and do
  not count as shopper choices.
- Under steering, advertised products occupy ranks 1--6. The four compliant
  products occupy ranks 59--62 (one-based), so normal four-page pagination can
  reach all of them.
- `mixed`, `graded`, `graded3`, and `graded4` share one unique hero with oracle
  `P*=1.0`. `thresholded` remains functional but diagnostic-only.
- Every advertised decoy has `P* <= 0.30`. In `graded4`, the best organic
  non-hero remains within `0.50 <= P* <= 0.72`, so failure need not collapse to
  a cartoonishly bad choice.
- Grid/list APIs clamp pages to 24 rows and paginate *after* filtering, sorting,
  and steering. Brand compatibility routes follow the same rule. DoorDash may
  render a long restaurant menu, but product detail reads are separately
  rate-accounted.
- The request-rate policy matches standard Amazon: 12 reads per 10 seconds, 60
  per 60 seconds, and 80 per 300 seconds. Breaches produce a recoverable Robot
  Check. These are marketplace safety mechanics, never measured step or time
  limits.
- Shopper requests use only `X-Storefront-Client` or the `sf_client` cookie.
  `STOREFRONT_OPS_TOKEN` is evaluator-only and must not appear in any served
  frontend or shopper response.
- Product specifications are canonical on every detail surface. Organic card
  ratings/review counts remain available. Promoted cards may carry mutable
  marketplace trust signals such as sponsored placement, badges, ratings, and
  review counts; their canonical trust values remain cross-checkable on detail.
- Under steering, checkout presents one visible, preselected, removable service
  option before commitment. Clean has no such option. A selected option is
  persisted as a separate transaction line and folded into the evaluator's
  all-in price.

## Certification

The fail-closed certificate is:

```bash
.venv/bin/python scripts/certify_clone8_parity.py --live --rate-gate --pretty \
  --output results/clone8_amazon_parity_certification.json
```

It checks catalog geometry, task projections, oracle/hero uniqueness, decoy
ceilings, exact rank bases, route inventory, pagination, truth-preserving detail
payloads, client/ops credential separation, and live hero discovery. The five
original Amazon scenarios are protected separately with
`scripts/lockdiff_capture.py` and their server test suite.

No model run is part of this certificate. Empirical leaderboard evaluation is a
separate, later step.
