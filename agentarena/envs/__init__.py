"""Environments. Importing this package registers every environment + its tasks.

    from agentarena.core.environment import ENVIRONMENTS
    import agentarena.envs            # registers "amazon", "airbnb"
    ENVIRONMENTS.names()              # ["airbnb", "amazon"]
"""

from . import airbnb, amazon  # noqa: F401  (registration side effects)

# Aggregate the example tasks for convenience.
ALL_TASKS = [*amazon.TASKS, *airbnb.TASKS]
