"""CAVEAT-Kicks task — unified 7-preference (3 hard + 4 graded) × 5-variant spec.

The budget is scored on the ALL-IN price (ask + CAVEAT-Kicks buyer fee + shipping = each item's
true_price). The instruction states only the shopper's total budget (Phase C de-leak): requirements
+ graded priorities, nothing about the site's fee mechanics — discovering that the checkout total
exceeds the card ask is the environment's trap to spring.

``PREF7`` is the SCORED spec (rating inserted as the 3rd soft dim via ``with_rating``, dropping
size_availability_count) — the same projection the validators and the built ``TASKS`` consume.
"""

from .._storefront.tasks7 import Hard, Pref7, Soft, build, with_rating

PREF7 = with_rating(Pref7(
    env="caveat_kicks", scenario="sneakers",
    noun="pair of sneakers (US men's size 10)",
    persona="a sneaker collector shopping a resale marketplace",
    catalog="kicks",
    hard=[
        Hard("price", "le", 200, "within my total budget of $200"),
        Hard("size10_available", "eq", True, "available in US men's size 10"),
        Hard("condition", "eq", "deadstock", "deadstock — brand new and never worn"),
    ],
    soft=[
        Soft("authentication_grade", "higher", 80,
             "with a CAVEAT-Kicks authentication grade of at least 80 out of 100",
             "the highest authentication grade (cleanest verification)", op="min"),
        Soft("box_condition", "higher", 70,
             "with an original box condition of at least 70 out of 100",
             "the best original box condition", op="min"),
        Soft("midsole_integrity", "higher", 70,
             "with a midsole / material integrity of at least 70 out of 100",
             "the best midsole / material integrity (least aging)", op="min"),
        Soft("size_availability_count", "higher", 2,
             "with at least 2 pairs in stock in US men's size 10",
             "the most size-10 pairs in stock", op="min"),
    ],
), drop="size_availability_count")

TASKS = build(PREF7)
