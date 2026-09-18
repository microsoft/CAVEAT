# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""CAVEAT-Food environment — the REAL harvested Next.js clone (static-exported) served by
our FastAPI, with its data wired to our generic /api. Browse restaurants -> dish ->
add to cart -> checkout -> order placed; we read back the order and score the dish.
"""

from __future__ import annotations

from pathlib import Path

from ...core.environment import ENVIRONMENTS
from .._storefront.adapter import StorefrontEnvironment
from .catalog import CATALOGS


@ENVIRONMENTS.register("caveat_food")
class CaveatFoodEnvironment(StorefrontEnvironment):
    name = "caveat_food"
    brand = "CAVEAT-Food"
    server_dir = Path(__file__).resolve().parent / "server"
    catalogs = CATALOGS
    transaction = "order"
    # (Phase C) list_minimal/spec_budget removed: menu cards always card-shaped (name +
    # price + rating keyed off steering pins), dish detail always full — anti-scrape is
    # the shared gate + rate-based Robot Check (gate.py).


from .tasks import TASKS  # noqa: E402,F401
