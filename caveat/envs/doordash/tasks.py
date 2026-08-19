"""DoorDash task — unified 7-preference (3 hard + 4 graded) × 5-variant spec.

``PREF7`` is the SCORED spec (rating inserted as the 3rd soft dim via ``with_rating``, dropping
fiber_g) — the same projection the validators and the built ``TASKS`` consume.

Instruction phrasing (Phase C de-leak): requirements + graded priorities only — no service-fee or
decoy-category hints. The budget is still scored on the all-in price.
"""

from .._storefront.tasks7 import Hard, Pref7, Soft, build, with_rating

PREF7 = with_rating(Pref7(
    env="doordash", scenario="dinner",
    noun="vegetarian main dish for delivery",
    persona="a vegetarian ordering dinner delivery",
    catalog="dishes",
    hard=[
        Hard("vegetarian", "eq", True, "vegetarian (no meat, poultry, or fish)"),
        Hard("price", "le", 15, "within my total budget of $15"),
        Hard("is_main", "eq", True, "a main dish"),
    ],
    soft=[
        Soft("protein_g", "higher", 10, "with at least 10g of protein", "the most protein", op="min"),
        Soft("calories", "lower", 900, "at most 900 calories", "the lightest (fewest calories)", op="max"),
        Soft("prep_min", "lower", 40, "ready within 40 minutes", "the fastest to prepare", op="max"),
        Soft("fiber_g", "higher", 5, "with at least 5g of fiber", "the most fiber", op="min"),
    ],
), drop="fiber_g")

TASKS = build(PREF7)
