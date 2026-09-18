# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""CAVEAT-Market environment — the real harvested Next.js 13 app-router clone (CAVEAT-Market UI + Tailwind),
statically exported and wired to our backend. The clone's pages already fetch /api/* at
runtime and keep the cart in localStorage; we re-point those fetches at a /caveat_market compat
router (CAVEAT-Market's product/order shapes) and write the placed order through to our generic
order so evaluate() reads it back. Browse the results -> open a listing -> add to cart ->
checkout -> place order; we score the chosen listing on condition + price. The agent
starts on the home results page."""

from __future__ import annotations

from pathlib import Path

from ...core.environment import ENVIRONMENTS
from .._storefront.adapter import StorefrontEnvironment
from .catalog import CATALOGS


@ENVIRONMENTS.register("caveat_market")
class CaveatMarketEnvironment(StorefrontEnvironment):
    name = "caveat_market"
    brand = "CAVEAT-Market"
    server_dir = Path(__file__).resolve().parent / "server"
    catalogs = CATALOGS
    transaction = "order"
    default_start_path = "/"
    # (Phase C) list_minimal/spec_budget removed: cards always card-shaped, detail always
    # full — anti-scrape is the shared gate + rate-based Robot Check (gate.py).


from .tasks import TASKS  # noqa: E402,F401
