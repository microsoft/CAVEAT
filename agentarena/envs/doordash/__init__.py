"""DoorDash environment — the REAL harvested Next.js clone (static-exported) served by
our FastAPI, with its data wired to our generic /api. Browse restaurants -> dish ->
add to cart -> checkout -> order placed; we read back the order and score the dish.
"""

from __future__ import annotations

from pathlib import Path

from ...core.environment import ENVIRONMENTS
from .._storefront.adapter import StorefrontEnvironment
from .catalog import CATALOGS


@ENVIRONMENTS.register("doordash")
class DoorDashEnvironment(StorefrontEnvironment):
    name = "doordash"
    brand = "DoorDash"
    server_dir = Path(__file__).resolve().parent / "server"
    catalogs = CATALOGS
    transaction = "order"


from .tasks import TASKS  # noqa: E402,F401
