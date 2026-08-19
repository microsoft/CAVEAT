"""Fiverr environment — the real harvested Vite/React Fiverr clone served by our
FastAPI. Browse gigs -> gig detail -> continue -> confirm order; we read back the
placed order and score the gig (price, delivery time, seller rating)."""

from __future__ import annotations

from pathlib import Path

from ...core.environment import ENVIRONMENTS
from .._storefront.adapter import StorefrontEnvironment
from .catalog import CATALOGS


@ENVIRONMENTS.register("fiverr")
class FiverrEnvironment(StorefrontEnvironment):
    name = "fiverr"
    brand = "Fiverr"
    server_dir = Path(__file__).resolve().parent / "server"
    catalogs = CATALOGS
    transaction = "order"
    # Cards use the symmetric card whitelist (gig name + price + seller star); every gig
    # detail is full. The shared request gate supplies the recoverable Robot Check.


from .tasks import TASKS  # noqa: E402,F401
