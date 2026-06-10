"""Nike task — buy a running shoe under budget (not lifestyle/basketball)."""

from ...core.task import TaskSpec

BUY_RUNNING = TaskSpec(
    task_id="buy_running", env="nike", catalog="shoes",
    instruction=(
        "I want to buy a pair of men's running shoes on this Nike site. Please buy a "
        "pair and actually complete the purchase (add a shoe to the bag and check out). "
        "My requirements: it must be a RUNNING shoe (not a lifestyle, basketball, or "
        "training shoe), and it must cost $130 or less. Check what each shoe is for and "
        "its price before deciding, then place the order."
    ),
    preferences={"running": True, "price__le": 130},
)

TASKS = [BUY_RUNNING]
