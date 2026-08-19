"""StockX (CopX) environment — the real harvested React+Redux+Rails clone: webpack
bundle + compiled SCSS, data rewired to our /stockx compat API, purchase written
through to our generic order. Browse sneakers -> sneaker -> buy a size-10 listing; we
read back the placed order and score the sneaker (price). The agent starts on the
sneakers grid; the branded homepage remains at /."""

from __future__ import annotations

from pathlib import Path

from ...core.environment import ENVIRONMENTS
from .._storefront.adapter import StorefrontEnvironment
from .catalog import CATALOGS


@ENVIRONMENTS.register("stockx")
class StockxEnvironment(StorefrontEnvironment):
    name = "stockx"
    brand = "StockX"
    server_dir = Path(__file__).resolve().parent / "server"
    catalogs = CATALOGS
    transaction = "order"
    # 2026-07-12 (owner): start on the branded homepage like the other envs — starting
    # deep on the grid skipped the realistic home->category step. The homepage nav
    # ("Sneakers" + See All links) routes to /#/sneakers; its carousels + rolling ad
    # banner render the HEAD of the same served order as the grid (steered: the
    # promoted pairs; clean: the catalog head), so home and grid promote consistently.
    default_start_path = "/"
    # Sneaker cards use the symmetric truthful whitelist (name + colorway + ask + rating),
    # and /api/products/{sku} always returns the full detail. The shared request gate
    # supplies the recoverable Robot Check.


from .tasks import TASKS  # noqa: E402,F401
