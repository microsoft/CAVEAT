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
    default_start_path = "/#/sneakers"   # HashRouter; the sneakers grid


from .tasks import TASKS  # noqa: E402,F401
