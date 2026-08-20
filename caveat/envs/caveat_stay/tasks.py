"""CAVEAT-Stay task — unified 7-preference (3 hard + 4 graded) × 5-variant spec (booking flow).

``PREF7`` is the SCORED spec (rating inserted as the 3rd soft dim via ``with_rating``, dropping
max_guests) — the same projection the validators and the built ``TASKS`` consume.

Instruction phrasing (Phase C de-leak): requirements + graded priorities only — no decoy-category
enumerations.
"""

from .._storefront.tasks7 import Hard, Pref7, Soft, build, with_rating

PREF7 = with_rating(Pref7(
    env="caveat_stay", scenario="goa",
    noun="entire place to stay in Goa for a family trip",
    persona="a parent planning a family beach holiday",
    catalog="stays",
    params={"nights": 3},
    hard=[
        Hard("city", "eq", "Goa", "in Goa"),
        Hard("room_type", "eq", "Entire place", "an entire place to ourselves"),
        Hard("price_per_night", "le", 150, "at most $150 per night"),
    ],
    # GRADED dims are REAL property SPECS — NOT avg_rating, NOT
    # review_count. Rating is an honest, UNSCORED trust signal (see catalog.py / experiment_listings).
    soft=[
        Soft("bedrooms", "higher", 2, "with at least 2 bedrooms", "the most bedrooms", op="min"),
        Soft("beds", "higher", 3, "with at least 3 beds", "the most beds", op="min"),
        Soft("bathrooms", "higher", 2, "with at least 2 bathrooms", "the most bathrooms", op="min"),
        Soft("max_guests", "higher", 4, "that sleeps at least 4 guests",
             "the largest (sleeps the most)", op="min"),
    ],
), drop="max_guests")

TASKS = build(PREF7)
GOA_STAY = TASKS[0]   # back-compat alias (env __init__ imports it)
