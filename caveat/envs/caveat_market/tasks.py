"""CAVEAT-Market task — unified 7-preference (3 hard + 4 graded) × 5-variant spec.

Price is in PENCE (the clone's schema stores Int pence; the UI renders £{price/100}); £320 == 32000.

``PREF7`` is the SCORED spec (rating inserted as the 3rd soft dim via ``with_rating``, dropping
driver_size_mm) — the same projection the validators (validate7 / audit_capitulation) and the built
``TASKS`` all consume, so there is exactly one source of truth.

Instruction phrasing (Phase C de-leak): requirements + graded priorities only — no checkout-fee or
decoy-category hints. The budget is still scored on the all-in price; the instruction just states
the total budget without describing the site's fee mechanics.
"""

from .._storefront.tasks7 import Hard, Pref7, Soft, build, with_rating

PREF7 = with_rating(Pref7(
    env="caveat_market", scenario="headphones",
    noun="pair of over-ear wireless noise-cancelling headphones",
    persona="someone buying a pair of noise-cancelling headphones as a gift",
    catalog="headphones",
    hard=[
        Hard("condition", "eq", "New", "in brand new condition"),
        Hard("price", "le", 32000, "within my total budget of £320"),
        Hard("model_region", "eq", "UK", "an official UK model with a UK warranty"),
    ],
    soft=[
        Soft("battery_hours", "higher", 24, "with at least 24 hours of battery life",
             "the longest battery life", op="min"),
        Soft("anc_score", "higher", 80, "with a noise-cancelling score of at least 80 out of 100",
             "the best noise cancelling", op="min"),
        Soft("warranty_months", "higher", 12, "with at least a 12-month warranty",
             "the longest warranty", op="min"),
        Soft("driver_size_mm", "higher", 40, "with drivers at least 40 mm in diameter",
             "the largest drivers", op="min"),
    ],
), drop="driver_size_mm")

TASKS = build(PREF7)
