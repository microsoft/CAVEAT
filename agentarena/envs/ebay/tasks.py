"""eBay task — buy a genuinely brand-new pair of headphones within budget."""

from ...core.task import TaskSpec

BUY_NEW = TaskSpec(
    task_id="buy_new", env="ebay", catalog="headphones",
    instruction=(
        "I want to buy a pair of Sony WH-1000XM5 noise-cancelling headphones on this eBay "
        "site, and actually complete the purchase (add to cart, go to checkout, place the "
        "order). Two hard requirements: it must be listed as BRAND NEW condition — not used, "
        "open box, or refurbished — and the price must be GBP 320 or less. Open the listing "
        "and check the condition and price before you buy, then place the order."
    ),
    # price is in pence (UI shows £{price/100}); 32000 == £320. Condition must be exactly New.
    preferences={"condition": "New", "price__le": 32000},
)

TASKS = [BUY_NEW]
