"""Regression test for the cart quantity-inflation bug.

Repeated "Add to cart" clicks on the SAME product (common when a model doesn't register the add
confirmation, or the browser harness retries the click) used to stack quantity via `+= data.quantity`,
inflating single-item carts to 2x+ and pushing faithful in-budget purchases over budget -> spurious
bails/violations. Adding the same product is now idempotent.
"""


class TestCartIdempotentAdd:
    def test_repeated_add_does_not_stack_quantity(self, auth_client):
        # Measured as a DELTA against whatever the fixture already seeded (conftest puts
        # product_id=1 in the cart). Asserting an absolute `item_count` made this test a
        # statement about the fixture AND about which of the two live definitions of
        # `item_count` (line-item count vs sum of quantities) the cart endpoint returns;
        # neither is what the regression is about. Four idempotent adds of the same product
        # must move the cart by exactly one unit, under either definition.
        before = auth_client.get("/api/cart").json()["item_count"]
        for _ in range(4):
            r = auth_client.post("/api/cart/items", json={"product_id": 2, "quantity": 1})
            assert r.status_code == 200, r.text
        cart = auth_client.get("/api/cart").json()
        items = [i for i in cart["items"] if i["product_id"] == 2]
        assert len(items) == 1, f"expected one line item, got {len(items)}"
        assert items[0]["quantity"] == 1, f"quantity must stay 1, got {items[0]['quantity']}"
        assert cart["item_count"] == before + 1, (
            f"item_count must grow by exactly 1 (was {before}), got {cart['item_count']}")

    def test_explicit_larger_quantity_is_honored(self, auth_client):
        auth_client.post("/api/cart/items", json={"product_id": 2, "quantity": 1})
        auth_client.post("/api/cart/items", json={"product_id": 2, "quantity": 3})
        cart = auth_client.get("/api/cart").json()
        items = [i for i in cart["items"] if i["product_id"] == 2]
        assert items[0]["quantity"] == 3, f"explicit qty=3 should win, got {items[0]['quantity']}"
