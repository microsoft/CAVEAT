"""Instacart environment — the real harvested Instacart static site served by our
FastAPI. Browse produce -> add to cart -> checkout -> order placed; we read back the
placed order and score the product (organic, price). The agent starts on the
functional products page; the branded landing remains at /."""

from __future__ import annotations

from pathlib import Path

from ...core.environment import ENVIRONMENTS
from .._storefront.adapter import StorefrontEnvironment
from .catalog import CATALOGS


@ENVIRONMENTS.register("instacart")
class InstacartEnvironment(StorefrontEnvironment):
    name = "instacart"
    brand = "Instacart"
    server_dir = Path(__file__).resolve().parent / "server"
    catalogs = CATALOGS
    transaction = "order"
    default_start_path = "/products.html"


from .tasks import TASKS  # noqa: E402,F401
