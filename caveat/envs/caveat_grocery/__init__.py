"""CAVEAT-Grocery environment — the real harvested CAVEAT-Grocery static site served by our
FastAPI. Browse produce -> add to cart -> checkout -> order placed; we read back the
placed order and score the product (organic, price). The agent starts on the branded
landing (/) like every other env; the header "Shop produce" link, the store tiles and
the address flow all route to the functional products page."""

from __future__ import annotations

from pathlib import Path

from ...core.environment import ENVIRONMENTS
from .._storefront.adapter import StorefrontEnvironment
from .catalog import CATALOGS


@ENVIRONMENTS.register("caveat_grocery")
class CaveatGroceryEnvironment(StorefrontEnvironment):
    name = "caveat_grocery"
    brand = "CAVEAT-Grocery"
    server_dir = Path(__file__).resolve().parent / "server"
    catalogs = CATALOGS
    transaction = "order"
    # 2026-07-12 (owner): start on the branded landing like the other envs — starting
    # deep on the aisle skipped the realistic home->shop step. index.html routes to
    # products.html everywhere (header link, store tiles, address flow) and shows no
    # products, so clean/steered ordering is untouched by the landing itself.
    default_start_path = "/"
    # Cards use the symmetric truthful whitelist (name + price + rating), and the details
    # modal always receives the full product record. The shared request gate supplies the
    # recoverable Robot Check.


from .tasks import TASKS  # noqa: E402,F401
