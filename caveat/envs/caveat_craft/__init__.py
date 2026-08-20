"""CAVEAT-Craft (CAVEAT-Craft) environment — the real harvested React+Redux+Rails clone: webpack
bundle + compiled SCSS, data rewired to our /caveat_craft compat API, cart written through to
our generic cart. Browse a category -> item -> add to cart -> proceed to checkout; we
read back the placed order and score the item (handmade, price). The agent starts on
the Jewelry category; the branded homepage remains at /."""

from __future__ import annotations

from pathlib import Path

from ...core.environment import ENVIRONMENTS
from .._storefront.adapter import StorefrontEnvironment
from .catalog import CATALOGS


@ENVIRONMENTS.register("caveat_craft")
class CaveatCraftEnvironment(StorefrontEnvironment):
    name = "caveat_craft"
    brand = "CAVEAT-Craft"
    server_dir = Path(__file__).resolve().parent / "server"
    catalogs = CATALOGS
    transaction = "order"
    # (Phase C) list_minimal/spec_budget removed: cards always card-shaped (name + price +
    # honest rating), detail always full — anti-scrape is the shared gate + rate-based
    # Robot Check (gate.py).
    # Start on the homepage (it pre-fetches products); the agent opens a category tile.
    default_start_path = "/"


from .tasks import TASKS  # noqa: E402,F401
