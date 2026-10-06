# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Environments. Importing this package registers every environment + its tasks.

    from caveat.core.environment import ENVIRONMENTS
    import caveat.envs            # registers "caveat_shop", "caveat_stay"
    ENVIRONMENTS.names()              # ["caveat_stay", "caveat_shop"]
"""

from . import caveat_stay, caveat_shop  # noqa: F401  (registration side effects)
from . import (caveat_food, caveat_market, caveat_craft, caveat_services, caveat_grocery,  # noqa: F401  (harvested environments)
               caveat_sport, caveat_kicks)

# Aggregate the example tasks for convenience.
ALL_TASKS = [*caveat_shop.TASKS, *caveat_stay.TASKS, *caveat_food.TASKS, *caveat_market.TASKS, *caveat_craft.TASKS,
             *caveat_services.TASKS, *caveat_grocery.TASKS, *caveat_sport.TASKS, *caveat_kicks.TASKS]
