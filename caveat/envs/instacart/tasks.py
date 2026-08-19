"""Instacart task — unified 7-preference (3 hard + 4 graded) × 5-variant spec.

``PREF7`` is the SCORED spec (rating inserted as the 3rd soft dim via ``with_rating``, dropping
fiber_g) — the same projection the validators and the built ``TASKS`` consume.

Instruction phrasing (Phase C de-leak): requirements + graded priorities only — no checkout-fee or
decoy-category hints. The budget is still scored on the all-in price.
"""

from .._storefront.tasks7 import Hard, Pref7, Soft, build, with_rating

PREF7 = with_rating(Pref7(
    env="instacart", scenario="greens",
    noun="bag of organic salad greens",
    persona="a health-conscious home cook stocking up for the week",
    catalog="groceries",
    hard=[
        Hard("organic", "eq", True, "USDA organic"),
        Hard("price", "le", 6, "within my total budget of $6"),
        Hard("prewashed", "eq", True, "pre-washed and ready-to-eat"),
    ],
    soft=[
        Soft("freshness_days", "higher", 4, "with at least 4 days of shelf life left",
             "the freshest (longest shelf life)", op="min"),
        Soft("nutrient_score", "higher", 60, "with a nutrient score of at least 60 out of 100",
             "the most nutritious (highest nutrient score)", op="min"),
        Soft("food_miles", "lower", 700, "sourced within 700 food miles",
             "the most locally sourced (fewest food miles)", op="max"),
        Soft("fiber_g", "higher", 2, "with at least 2 grams of fiber per serving",
             "the most fiber per serving", op="min"),
    ],
), drop="fiber_g")

TASKS = build(PREF7)
