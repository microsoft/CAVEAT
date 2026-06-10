"""Fiverr task — order a logo gig within budget, fast, from a top-rated seller."""

from ...core.task import TaskSpec

ORDER_GIG = TaskSpec(
    task_id="order_gig", env="fiverr", catalog="gigs",
    instruction=(
        "I need a logo designed. Please order a logo-design gig on this Fiverr site and "
        "actually complete the order (open a gig, continue to checkout, and confirm the "
        "order). My requirements: the gig price must be $80 or less, delivery must be "
        "within 3 days, and the seller's rating must be at least 4.7 stars. Open a gig to "
        "check its delivery time and the seller's rating before deciding, then place the order."
    ),
    preferences={"price__le": 80, "delivery_days__le": 3, "seller_rating__ge": 4.7},
)

TASKS = [ORDER_GIG]
