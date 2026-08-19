"""Zillow task — unified 7-preference (3 hard + 4 graded) × 5-variant spec (LEAD: request a tour).

``PREF7`` is the SCORED spec (rating inserted as the 3rd soft dim via ``with_rating``, dropping
priceSqft) — the same projection the validators and the built ``TASKS`` consume. Dropping priceSqft
from scoring is what lets every card show sqft honestly without leaking the hero (see catalog.py).

Instruction phrasing (Phase C de-leak): requirements + graded priorities only — nothing about the
site's mechanics or where information lives.
"""

from .._storefront.tasks7 import Hard, Pref7, Soft, build, with_rating

PREF7 = with_rating(Pref7(
    env="zillow", scenario="austin",
    noun="family home in Austin to tour",
    persona="a family house-hunting in Austin",
    catalog="austin",
    hard=[
        Hard("beds", "min", 3, "at least 3 bedrooms"),
        Hard("bath", "min", 2, "at least 2 bathrooms"),
        Hard("price", "le", 750000, "within my total budget of $750,000"),
    ],
    soft=[
        Soft("priceSqft", "lower", 350, "at most $350 per square foot",
             "the best value (lowest price per square foot)", op="max"),
        Soft("yearBuilt", "higher", 2000, "built in the year 2000 or later",
             "the newest (most recently built)", op="min"),
        Soft("school_rating", "higher", 6, "with a school rating of at least 6 out of 10",
             "the best-rated schools", op="min"),
        Soft("condition", "higher", 60, "with a condition score of at least 60 out of 100",
             "the best condition (most move-in ready)", op="min"),
    ],
), drop="priceSqft")

TASKS = build(PREF7)
