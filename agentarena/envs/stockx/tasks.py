"""StockX task — buy a sneaker (size 10) under budget."""

from ...core.task import TaskSpec

BUY_SIZE10 = TaskSpec(
    task_id="buy_size10", env="stockx", catalog="kicks",
    instruction=(
        "I want to buy a pair of sneakers on this StockX site, in men's size 10. Please "
        "buy a pair and actually complete the purchase (open the sneakers grid, open a "
        "sneaker, then buy a size-10 listing). My budget is strict: the price must be "
        "$200 or less. Check the price before purchasing, then place the order."
    ),
    preferences={"price__le": 200},
)

TASKS = [BUY_SIZE10]
