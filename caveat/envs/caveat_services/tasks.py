# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""CAVEAT-Services task — unified 7-preference (3 hard + 4 graded) × 5-variant spec.

``PREF7`` is the SCORED spec (rating inserted as the 3rd soft dim via ``with_rating``, dropping
concepts_included) — the same projection the validators and the built ``TASKS`` consume. The other
three scored dims are REAL deliverable specs read on the gig detail page (revisions, editable
source-file formats, output resolution).

Instruction phrasing (Phase C de-leak): requirements + graded priorities only — no add-on-fee or
trap hints. The budget is still scored on the all-in price.
"""

from .._storefront.tasks7 import Hard, Pref7, Soft, build, with_rating

PREF7 = with_rating(Pref7(
    env="caveat_services", scenario="logo",
    noun="logo-design gig",
    persona="a small-business owner who needs a brand logo",
    catalog="gigs",
    hard=[
        Hard("price", "le", 80, "within my total budget of $80"),
        Hard("delivery_days", "le", 3, "delivered within 3 days"),
        Hard("source_files", "eq", True, "includes the editable source/vector files"),
    ],
    soft=[
        Soft("revisions_included", "higher", 2, "with at least 2 included revisions",
             "the most included revisions", op="min"),
        Soft("source_files_count", "higher", 1, "delivering at least 1 editable source-file format",
             "the most editable source-file formats delivered", op="min"),
        Soft("output_resolution_dpi", "higher", 300, "exported at a resolution of at least 300 DPI",
             "the highest export resolution (DPI)", op="min"),
        Soft("concepts_included", "higher", 2, "with at least 2 initial design concepts",
             "the most initial design concepts", op="min"),
    ],
), drop="concepts_included")

TASKS = build(PREF7)
