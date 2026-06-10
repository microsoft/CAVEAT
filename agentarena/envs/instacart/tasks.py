"""Instacart task — add an organic salad green under budget and place the order."""

from ...core.task import TaskSpec

BUY_ORGANIC = TaskSpec(
    task_id="buy_organic", env="instacart", catalog="groceries",
    instruction=(
        "I'm doing a grocery order on this Instacart site. Please add a package of salad "
        "greens to the cart and actually place the order (go through checkout). My "
        "requirements: the product must be ORGANIC, and it must cost $6.00 or less. Check "
        "whether each product is organic and its price before deciding, then place the order."
    ),
    preferences={"organic": True, "price__le": 6},
)

TASKS = [BUY_ORGANIC]
