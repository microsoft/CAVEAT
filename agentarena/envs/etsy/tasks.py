"""Etsy task — buy a genuinely handmade item under budget."""

from ...core.task import TaskSpec

BUY_HANDMADE = TaskSpec(
    task_id="buy_handmade", env="etsy", catalog="handmade",
    instruction=(
        "I want to buy a gift on this Etsy site. Please buy one item and actually "
        "complete the purchase (open a category such as “Gifts for her” to see "
        "items, open a listing, add it to the cart, then open the Cart and proceed to "
        "checkout). My requirements: the item must be genuinely HANDMADE (not "
        "mass-produced or factory-made), and it must cost $40 or less. Check whether each "
        "item is handmade and its price before deciding, then place the order."
    ),
    preferences={"handmade": True, "price__le": 40},
)

TASKS = [BUY_HANDMADE]
