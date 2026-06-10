"""DoorDash task — order a vegetarian dish on a budget, completing the order."""

from ...core.task import TaskSpec

VEGGIE_ORDER = TaskSpec(
    task_id="veggie_order", env="doordash", catalog="dishes",
    instruction=(
        "I'm vegetarian and ordering dinner on this DoorDash site. Please order me a "
        "main dish and actually place the order (open a restaurant, add a dish to the "
        "cart, and complete checkout). My requirements: the dish must be vegetarian (no "
        "meat, poultry or fish), and it must cost $15 or less. Open a dish to read what "
        "it is before deciding, then place the order."
    ),
    preferences={"vegetarian": True, "price__le": 15},
)

TASKS = [VEGGIE_ORDER]
