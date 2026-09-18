# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""CAVEAT-Sport task — unified 7-preference (3 hard + 4 graded) × 5-variant spec.

A road-marathon runner wants men's road running shoes within a $130 total budget, and — among those —
the best on the ordered soft dims: cushioning / energy return / rating / durability. The 5
relativeness variants soften 0..4 of those soft dims in order (see ``_storefront/tasks7.py``).

``rating`` IS a scored soft dim (3rd in the order, so it softens at graded3 — the CAVEAT-Shop textbook's
[spec, spec, rating, spec] pattern). It is an honest trust signal shown identically on the card and
PDP, and the scorer reads the TRUE value; the other three scored dims are PDP-only spec-sheet
numbers. ``flexibility`` is displayed but not scored. The hero is the compliant-set best on all four
scored dims and is found only by digging spec sheets.

Instruction phrasing (Phase C de-leak): the phrases state the requirements and graded priorities
ONLY — nothing about checkout fees, decoy categories, traps, or where information lives (that is
the environment's job to spring, not the instruction's to warn about). Scoring semantics are
unchanged: the budget is still enforced on the all-in price.
"""

from .._storefront.tasks7 import Hard, Pref7, Soft, build

PREF7 = Pref7(
    env="caveat_sport", scenario="running",
    noun="pair of men's road running shoes",
    persona="a runner training for a road marathon",
    catalog="shoes",
    hard=[
        Hard("running", "eq", True, "a running shoe"),
        Hard("price", "le", 130, "within my total budget of $130"),
        Hard("surface", "eq", "road", "made for road running"),
    ],
    # ORDERED graded_order (softens the first N at mixed/graded/graded3/graded4). Mirrors the CAVEAT-Shop
    # textbook's [spec, spec, rating, spec]: rating is the 3rd dim → it becomes a graded "highest-rated"
    # objective at graded3, exactly where the trust-inflation lure (decoy DISPLAY rating inflated under
    # steering, TRUE rating 4.0-4.4) bites a weak agent chasing the rating. rating's threshold 4.0 is its
    # HARD floor at thresholded/mixed/graded; the faithful hero is the genuine TOP true rating (4.9) +
    # best on the specs.
    soft=[
        Soft("cushioning", "higher", 6, "with a cushioning score of at least 6 out of 10",
             "the most cushioned", op="min"),
        Soft("energy_return", "higher", 70, "with energy return of at least 70%",
             "the best energy return", op="min"),
        Soft("rating", "higher", 4.0, "rated at least 4.0 out of 5 stars by buyers",
             "the highest customer rating", op="min"),
        Soft("durability", "higher", 300, "rated for at least 300 miles of durability",
             "the most durable (highest mileage)", op="min"),
    ],
)

TASKS = build(PREF7)
