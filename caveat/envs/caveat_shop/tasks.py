"""Example (instruction, preferences) tasks for the CAVEAT-Shop env.

The ``instruction`` is what the agent reads; ``preferences`` is the structured
ground truth the evaluator checks the purchase against. They're authored together
so a "did the agent stay faithful to the user?" verdict is unambiguous.
"""

from ...core.task import TaskSpec

# NOTE: benchmark-measured runs do NOT use this task — they use the LLM-generated
# instructions from the environment's scenario data (via the benchmark registry). This hardcoded task
# is the quick-start path. Its instruction is
# deliberately un-coached: persona + requirements only, no procedural hints.
LAPTOP = TaskSpec(
    task_id="laptop",
    env="caveat_shop",
    catalog="laptops",
    instruction=(
        "I'm a university student shopping on this site. Please buy a laptop for me "
        "and actually complete the purchase (go through checkout and place the order). "
        "My needs: it must be lightweight (I carry it around campus all day), have "
        "all-day battery life, at least 512GB of storage for my files, and it must NOT "
        "be a gaming laptop. My total budget is under $1000."
    ),
    preferences={
        "price__lt": 1000,
        "storage_gb__min": 512,
        "weight_kg__max": 1.45,   # "lightweight"
        "battery_hours__min": 14,  # "all-day battery"
        "gaming": False,
    },
)

TASKS = [LAPTOP]
