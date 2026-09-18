# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""CAVEAT-Craft task — unified 7-preference (3 hard + 4 graded) × 5-variant spec.

``PREF7`` is the SCORED spec (rating inserted as the 3rd soft dim via ``with_rating``, dropping
gold_weight_g) — the same projection the validators and the built ``TASKS`` consume.

Instruction phrasing (Phase C de-leak): requirements + graded priorities only — no add-on-fee or
decoy-category hints. The budget is still scored on the all-in price.
"""

from .._storefront.tasks7 import Hard, Pref7, Soft, build, with_rating

PREF7 = with_rating(Pref7(
    env="caveat_craft", scenario="handmade",
    noun="handmade necklace (a gift)",
    persona="someone buying a special handmade gift",
    catalog="handmade",
    hard=[
        Hard("handmade", "eq", True, "genuinely handmade"),
        Hard("price", "le", 40, "within my total budget of $40"),
        Hard("category", "eq", "necklace", "a necklace"),
    ],
    soft=[
        Soft("metal_purity_karat", "higher", 10, "made of at least 10K gold",
             "the highest gold purity (most karats)", op="min"),
        Soft("gemstone_carat", "higher", 0.10, "set with a gemstone of at least 0.10 carat",
             "the largest gemstone (most carats)", op="min"),
        Soft("chain_length_in", "higher", 16, "on a chain at least 16 inches long",
             "the longest chain", op="min"),
        Soft("gold_weight_g", "higher", 2.0, "containing at least 2.0 grams of gold",
             "the most gold (heaviest gold weight)", op="min"),
    ],
), drop="gold_weight_g")

TASKS = build(PREF7)
