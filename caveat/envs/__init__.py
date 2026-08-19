"""Environments. Importing this package registers every environment + its tasks.

    from caveat.core.environment import ENVIRONMENTS
    import caveat.envs            # registers "amazon", "airbnb"
    ENVIRONMENTS.names()              # ["airbnb", "amazon"]
"""

from . import airbnb, amazon  # noqa: F401  (registration side effects)
from . import (doordash, ebay, etsy, fiverr, instacart,  # noqa: F401  (harvested real-clone envs)
               nike, stockx, zillow)

# Aggregate the example tasks for convenience.
ALL_TASKS = [*amazon.TASKS, *airbnb.TASKS, *doordash.TASKS, *ebay.TASKS, *etsy.TASKS,
             *fiverr.TASKS, *instacart.TASKS, *nike.TASKS, *stockx.TASKS, *zillow.TASKS]
