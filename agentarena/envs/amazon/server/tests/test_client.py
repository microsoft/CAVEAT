"""Unit tests for AmazonAPI client.

Tests verify the client correctly constructs URLs, query params, and request bodies
by mocking the requests library. No live server needed.
"""

from unittest.mock import patch, MagicMock

import pytest

from backend.client import AmazonAPI


@pytest.fixture
def api():
    return AmazonAPI(base_url="http://test:8000/api")


def _mock_response(json_data=None):
    mock = MagicMock()
    mock.json.return_value = json_data or {}
    mock.status_code = 200
    return mock


# ============================================================================
# Authentication
# ============================================================================


class TestAuthMethods:
    @patch("backend.client.requests.post")
    def test_register(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        result = api.register("a@b.com", "pass", "Alice", phone="555")
        mock_post.assert_called_once_with(
            "http://test:8000/api/auth/register",
            json={"email": "a@b.com", "password": "pass", "name": "Alice", "phone": "555"},
        )
        assert result == {"id": 1}

    @patch("backend.client.requests.post")
    def test_register_no_phone(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        api.register("a@b.com", "pass", "Alice")
        call_json = mock_post.call_args[1]["json"]
        assert "phone" not in call_json

    @patch("backend.client.requests.post")
    def test_login(self, mock_post, api):
        mock_post.return_value = _mock_response({"token": "abc"})
        result = api.login("a@b.com", "pass")
        mock_post.assert_called_once_with(
            "http://test:8000/api/auth/login",
            json={"email": "a@b.com", "password": "pass"},
        )
        assert result == {"token": "abc"}

    @patch("backend.client.requests.post")
    def test_logout(self, mock_post, api):
        mock_post.return_value = _mock_response({"message": "ok"})
        api.logout()
        mock_post.assert_called_once_with("http://test:8000/api/auth/logout")

    @patch("backend.client.requests.post")
    def test_logout_all(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.logout_all()
        mock_post.assert_called_once_with("http://test:8000/api/auth/logout-all")

    @patch("backend.client.requests.get")
    def test_get_current_auth_user(self, mock_get, api):
        mock_get.return_value = _mock_response({"id": 1, "name": "Alice"})
        result = api.get_current_auth_user()
        mock_get.assert_called_once_with("http://test:8000/api/auth/me")
        assert result["name"] == "Alice"

    @patch("backend.client.requests.get")
    def test_list_sessions(self, mock_get, api):
        mock_get.return_value = _mock_response({"sessions": []})
        api.list_sessions()
        mock_get.assert_called_once_with("http://test:8000/api/auth/sessions")

    @patch("backend.client.requests.delete")
    def test_delete_session(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.delete_session(5)
        mock_del.assert_called_once_with("http://test:8000/api/auth/sessions/5")

    @patch("backend.client.requests.get")
    def test_list_lwa_apps(self, mock_get, api):
        mock_get.return_value = _mock_response({"apps": []})
        api.list_lwa_apps()
        mock_get.assert_called_once_with("http://test:8000/api/auth/lwa/apps")

    @patch("backend.client.requests.delete")
    def test_delete_lwa_app(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.delete_lwa_app(3)
        mock_del.assert_called_once_with("http://test:8000/api/auth/lwa/apps/3")


# ============================================================================
# User / Account
# ============================================================================


class TestUserMethods:
    @patch("backend.client.requests.get")
    def test_get_user(self, mock_get, api):
        mock_get.return_value = _mock_response({"id": 1, "name": "John"})
        result = api.get_user()
        mock_get.assert_called_once_with("http://test:8000/api/user")
        assert result["name"] == "John"

    @patch("backend.client.requests.put")
    def test_update_user(self, mock_put, api):
        mock_put.return_value = _mock_response()
        api.update_user(name="Jane", phone="555")
        call_json = mock_put.call_args[1]["json"]
        assert call_json["name"] == "Jane"
        assert call_json["phone"] == "555"
        assert "email" not in call_json
        assert "avatar_url" not in call_json

    @patch("backend.client.requests.put")
    def test_change_password(self, mock_put, api):
        mock_put.return_value = _mock_response()
        api.change_password("old", "new")
        mock_put.assert_called_once_with(
            "http://test:8000/api/user/password",
            json={"current_password": "old", "new_password": "new"},
        )

    @patch("backend.client.requests.get")
    def test_list_addresses(self, mock_get, api):
        mock_get.return_value = _mock_response({"addresses": []})
        api.list_addresses()
        mock_get.assert_called_once_with("http://test:8000/api/user/addresses")

    @patch("backend.client.requests.post")
    def test_create_address(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        api.create_address(
            full_name="John",
            phone="555",
            address_line1="123 Main",
            city="SF",
            state="CA",
            zip_code="94102",
        )
        call_json = mock_post.call_args[1]["json"]
        assert call_json["full_name"] == "John"
        assert call_json["country"] == "United States"
        assert "address_line2" not in call_json

    @patch("backend.client.requests.post")
    def test_create_address_with_optional_fields(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        api.create_address(
            full_name="John",
            phone="555",
            address_line1="123 Main",
            city="SF",
            state="CA",
            zip_code="94102",
            address_line2="Apt 4",
            delivery_instructions="Leave at door",
        )
        call_json = mock_post.call_args[1]["json"]
        assert call_json["address_line2"] == "Apt 4"
        assert call_json["delivery_instructions"] == "Leave at door"

    @patch("backend.client.requests.put")
    def test_update_address(self, mock_put, api):
        mock_put.return_value = _mock_response()
        api.update_address(1, city="LA")
        mock_put.assert_called_once_with(
            "http://test:8000/api/user/addresses/1", json={"city": "LA"}
        )

    @patch("backend.client.requests.delete")
    def test_delete_address(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.delete_address(1)
        mock_del.assert_called_once_with("http://test:8000/api/user/addresses/1")

    @patch("backend.client.requests.get")
    def test_list_payment_methods(self, mock_get, api):
        mock_get.return_value = _mock_response({"payment_methods": []})
        api.list_payment_methods()
        mock_get.assert_called_once_with("http://test:8000/api/user/payment-methods")

    @patch("backend.client.requests.post")
    def test_create_payment_method(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        api.create_payment_method(
            type="credit_card",
            card_number_last4="4242",
            card_brand="visa",
            expiry_month=12,
            expiry_year=2027,
            cardholder_name="John",
        )
        call_json = mock_post.call_args[1]["json"]
        assert call_json["card_number_last4"] == "4242"
        assert call_json["is_default"] is False
        assert "billing_address_id" not in call_json

    @patch("backend.client.requests.delete")
    def test_delete_payment_method(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.delete_payment_method(1)
        mock_del.assert_called_once_with("http://test:8000/api/user/payment-methods/1")

    @patch("backend.client.requests.get")
    def test_get_prime_status(self, mock_get, api):
        mock_get.return_value = _mock_response({"is_prime": True})
        result = api.get_prime_status()
        mock_get.assert_called_once_with("http://test:8000/api/user/prime")
        assert result["is_prime"] is True

    @patch("backend.client.requests.get")
    def test_list_notifications(self, mock_get, api):
        mock_get.return_value = _mock_response({"notifications": []})
        api.list_notifications()
        mock_get.assert_called_once_with("http://test:8000/api/user/notifications")

    @patch("backend.client.requests.put")
    def test_mark_notification_read(self, mock_put, api):
        mock_put.return_value = _mock_response()
        api.mark_notification_read(7)
        mock_put.assert_called_once_with(
            "http://test:8000/api/user/notifications/7/read"
        )


# ============================================================================
# Products
# ============================================================================


class TestProductMethods:
    @patch("backend.client.requests.get")
    def test_list_products_defaults(self, mock_get, api):
        mock_get.return_value = _mock_response({"products": [], "total": 0})
        api.list_products()
        params = mock_get.call_args[1]["params"]
        assert params["sort"] == "featured"
        assert params["page"] == 1
        assert params["limit"] == 48
        assert "q" not in params

    @patch("backend.client.requests.get")
    def test_list_products_with_filters(self, mock_get, api):
        mock_get.return_value = _mock_response({"products": [], "total": 0})
        api.list_products(
            q="laptop",
            department="electronics",
            min_price=100,
            max_price=500,
            prime=True,
            sort="price_asc",
        )
        params = mock_get.call_args[1]["params"]
        assert params["q"] == "laptop"
        assert params["department"] == "electronics"
        assert params["min_price"] == 100
        assert params["max_price"] == 500
        assert params["prime"] is True
        assert params["sort"] == "price_asc"

    @patch("backend.client.requests.get")
    def test_get_best_sellers(self, mock_get, api):
        mock_get.return_value = _mock_response({"products": []})
        api.get_best_sellers(limit=10)
        mock_get.assert_called_once_with(
            "http://test:8000/api/products/best-sellers", params={"limit": 10}
        )

    @patch("backend.client.requests.get")
    def test_get_new_releases(self, mock_get, api):
        mock_get.return_value = _mock_response({"products": []})
        api.get_new_releases()
        mock_get.assert_called_once_with(
            "http://test:8000/api/products/new-releases", params={"limit": 20}
        )

    @patch("backend.client.requests.get")
    def test_get_product(self, mock_get, api):
        mock_get.return_value = _mock_response({"id": 1, "title": "iPhone"})
        result = api.get_product(1)
        mock_get.assert_called_once_with("http://test:8000/api/products/1")
        assert result["title"] == "iPhone"

    @patch("backend.client.requests.get")
    def test_get_product_by_asin(self, mock_get, api):
        mock_get.return_value = _mock_response({"asin": "B09V3"})
        api.get_product_by_asin("B09V3")
        mock_get.assert_called_once_with("http://test:8000/api/products/asin/B09V3")

    @patch("backend.client.requests.get")
    def test_get_product_variants(self, mock_get, api):
        mock_get.return_value = _mock_response({"variants": []})
        api.get_product_variants(1)
        mock_get.assert_called_once_with("http://test:8000/api/products/1/variants")

    @patch("backend.client.requests.get")
    def test_get_product_reviews(self, mock_get, api):
        mock_get.return_value = _mock_response({"reviews": []})
        api.get_product_reviews(1, rating=5, verified_only=True)
        params = mock_get.call_args[1]["params"]
        assert params["rating"] == 5
        assert params["verified_only"] is True

    @patch("backend.client.requests.get")
    def test_get_product_reviews_summary(self, mock_get, api):
        mock_get.return_value = _mock_response({"average": 4.5})
        api.get_product_reviews_summary(1)
        mock_get.assert_called_once_with(
            "http://test:8000/api/products/1/reviews/summary"
        )

    @patch("backend.client.requests.get")
    def test_get_product_questions(self, mock_get, api):
        mock_get.return_value = _mock_response({"questions": []})
        api.get_product_questions(1, sort="votes", page=2)
        params = mock_get.call_args[1]["params"]
        assert params["sort"] == "votes"
        assert params["page"] == 2

    @patch("backend.client.requests.get")
    def test_get_related_products(self, mock_get, api):
        mock_get.return_value = _mock_response({"products": []})
        api.get_related_products(1, limit=5)
        mock_get.assert_called_once_with(
            "http://test:8000/api/products/1/related", params={"limit": 5}
        )

    @patch("backend.client.requests.get")
    def test_get_frequently_bought_together(self, mock_get, api):
        mock_get.return_value = _mock_response({"products": []})
        api.get_frequently_bought_together(1)
        mock_get.assert_called_once_with(
            "http://test:8000/api/products/1/frequently-bought"
        )

    @patch("backend.client.requests.get")
    def test_get_similar_products(self, mock_get, api):
        mock_get.return_value = _mock_response({"products": []})
        api.get_similar_products(1)
        mock_get.assert_called_once_with(
            "http://test:8000/api/products/1/similar", params={"limit": 10}
        )

    @patch("backend.client.requests.get")
    def test_get_movers_and_shakers(self, mock_get, api):
        mock_get.return_value = _mock_response({"products": []})
        api.get_movers_and_shakers()
        mock_get.assert_called_once_with(
            "http://test:8000/api/products/movers-shakers", params={"limit": 20}
        )

    @patch("backend.client.requests.get")
    def test_get_trending(self, mock_get, api):
        mock_get.return_value = _mock_response({"products": []})
        api.get_trending(limit=5)
        mock_get.assert_called_once_with(
            "http://test:8000/api/products/trending", params={"limit": 5}
        )


# ============================================================================
# Search & Browse
# ============================================================================


class TestSearchMethods:
    @patch("backend.client.requests.get")
    def test_search(self, mock_get, api):
        mock_get.return_value = _mock_response({"products": [], "total": 0})
        api.search("laptop", department="electronics")
        params = mock_get.call_args[1]["params"]
        assert params["q"] == "laptop"
        assert params["department"] == "electronics"

    @patch("backend.client.requests.get")
    def test_search_suggestions(self, mock_get, api):
        mock_get.return_value = _mock_response({"suggestions": ["laptop"]})
        api.search_suggestions("lap")
        mock_get.assert_called_once_with(
            "http://test:8000/api/search/suggestions", params={"q": "lap"}
        )

    @patch("backend.client.requests.get")
    def test_get_search_history(self, mock_get, api):
        mock_get.return_value = _mock_response({"history": []})
        api.get_search_history()
        mock_get.assert_called_once_with("http://test:8000/api/search/history")

    @patch("backend.client.requests.post")
    def test_save_search_history(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.save_search_history("iphone")
        mock_post.assert_called_once_with(
            "http://test:8000/api/search/history", params={"q": "iphone"}
        )

    @patch("backend.client.requests.delete")
    def test_clear_search_history(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.clear_search_history()
        mock_del.assert_called_once_with("http://test:8000/api/search/history")

    @patch("backend.client.requests.get")
    def test_list_departments(self, mock_get, api):
        mock_get.return_value = _mock_response({"departments": []})
        api.list_departments()
        mock_get.assert_called_once_with("http://test:8000/api/departments")

    @patch("backend.client.requests.get")
    def test_get_department(self, mock_get, api):
        mock_get.return_value = _mock_response({"name": "Electronics"})
        api.get_department("electronics")
        mock_get.assert_called_once_with(
            "http://test:8000/api/departments/electronics"
        )

    @patch("backend.client.requests.get")
    def test_get_department_categories(self, mock_get, api):
        mock_get.return_value = _mock_response({"categories": []})
        api.get_department_categories("electronics")
        mock_get.assert_called_once_with(
            "http://test:8000/api/departments/electronics/categories"
        )

    @patch("backend.client.requests.get")
    def test_list_categories(self, mock_get, api):
        mock_get.return_value = _mock_response({"categories": []})
        api.list_categories()
        mock_get.assert_called_once_with("http://test:8000/api/categories")

    @patch("backend.client.requests.get")
    def test_get_category(self, mock_get, api):
        mock_get.return_value = _mock_response({"name": "Smartphones"})
        api.get_category("smartphones")
        mock_get.assert_called_once_with(
            "http://test:8000/api/categories/smartphones"
        )

    @patch("backend.client.requests.get")
    def test_get_category_products(self, mock_get, api):
        mock_get.return_value = _mock_response({"products": []})
        api.get_category_products("smartphones", page=2, limit=10)
        params = mock_get.call_args[1]["params"]
        assert params["page"] == 2
        assert params["limit"] == 10

    @patch("backend.client.requests.get")
    def test_get_category_subcategories(self, mock_get, api):
        mock_get.return_value = _mock_response({"subcategories": []})
        api.get_category_subcategories("electronics")
        mock_get.assert_called_once_with(
            "http://test:8000/api/categories/electronics/subcategories"
        )


# ============================================================================
# Cart
# ============================================================================


class TestCartMethods:
    @patch("backend.client.requests.get")
    def test_get_cart(self, mock_get, api):
        mock_get.return_value = _mock_response({"items": []})
        api.get_cart()
        mock_get.assert_called_once_with("http://test:8000/api/cart")

    @patch("backend.client.requests.post")
    def test_add_to_cart(self, mock_post, api):
        mock_post.return_value = _mock_response({"message": "added"})
        api.add_to_cart(product_id=1, quantity=2)
        call_json = mock_post.call_args[1]["json"]
        assert call_json["product_id"] == 1
        assert call_json["quantity"] == 2
        assert "variant_id" not in call_json

    @patch("backend.client.requests.post")
    def test_add_to_cart_with_variant_and_gift(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.add_to_cart(product_id=1, variant_id=5, is_gift=True, gift_message="Happy!")
        call_json = mock_post.call_args[1]["json"]
        assert call_json["variant_id"] == 5
        assert call_json["is_gift"] is True
        assert call_json["gift_message"] == "Happy!"

    @patch("backend.client.requests.put")
    def test_update_cart_item(self, mock_put, api):
        mock_put.return_value = _mock_response()
        api.update_cart_item(1, quantity=5)
        call_json = mock_put.call_args[1]["json"]
        assert call_json["quantity"] == 5
        assert "is_gift" not in call_json

    @patch("backend.client.requests.delete")
    def test_remove_cart_item(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.remove_cart_item(1)
        mock_del.assert_called_once_with("http://test:8000/api/cart/items/1")

    @patch("backend.client.requests.post")
    def test_save_for_later(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.save_for_later(1)
        mock_post.assert_called_once_with(
            "http://test:8000/api/cart/items/1/save-for-later"
        )

    @patch("backend.client.requests.post")
    def test_move_to_cart(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.move_to_cart(1)
        mock_post.assert_called_once_with(
            "http://test:8000/api/cart/items/1/move-to-cart"
        )

    @patch("backend.client.requests.delete")
    def test_clear_cart(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.clear_cart()
        mock_del.assert_called_once_with("http://test:8000/api/cart")

    @patch("backend.client.requests.post")
    def test_apply_coupon(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.apply_coupon("SAVE10")
        mock_post.assert_called_once_with(
            "http://test:8000/api/cart/apply-coupon", json={"code": "SAVE10"}
        )


# ============================================================================
# Checkout & Orders
# ============================================================================


class TestCheckoutOrderMethods:
    @patch("backend.client.requests.post")
    def test_start_checkout(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.start_checkout()
        mock_post.assert_called_once_with("http://test:8000/api/checkout/start")

    @patch("backend.client.requests.put")
    def test_set_checkout_shipping(self, mock_put, api):
        mock_put.return_value = _mock_response()
        api.set_checkout_shipping(address_id=1, shipping_method="express")
        mock_put.assert_called_once_with(
            "http://test:8000/api/checkout/shipping",
            json={"address_id": 1, "shipping_method": "express"},
        )

    @patch("backend.client.requests.put")
    def test_set_checkout_payment(self, mock_put, api):
        mock_put.return_value = _mock_response()
        api.set_checkout_payment(payment_method_id=1, gift_card_amount=25.0)
        call_json = mock_put.call_args[1]["json"]
        assert call_json["payment_method_id"] == 1
        assert call_json["gift_card_amount"] == 25.0

    @patch("backend.client.requests.get")
    def test_get_checkout_summary(self, mock_get, api):
        mock_get.return_value = _mock_response({"total": 100})
        api.get_checkout_summary()
        mock_get.assert_called_once_with("http://test:8000/api/checkout/summary")

    @patch("backend.client.requests.get")
    def test_get_shipping_options(self, mock_get, api):
        mock_get.return_value = _mock_response({"options": []})
        api.get_shipping_options()
        mock_get.assert_called_once_with(
            "http://test:8000/api/checkout/shipping-options"
        )

    @patch("backend.client.requests.post")
    def test_place_order(self, mock_post, api):
        mock_post.return_value = _mock_response({"order_id": 1})
        api.place_order(is_gift=True, gift_message="Enjoy!")
        call_json = mock_post.call_args[1]["json"]
        assert call_json["is_gift"] is True
        assert call_json["gift_message"] == "Enjoy!"

    @patch("backend.client.requests.get")
    def test_list_orders(self, mock_get, api):
        mock_get.return_value = _mock_response({"orders": [], "total": 0})
        api.list_orders(status="delivered", period="3months", q="iphone")
        params = mock_get.call_args[1]["params"]
        assert params["status"] == "delivered"
        assert params["period"] == "3months"
        assert params["q"] == "iphone"

    @patch("backend.client.requests.get")
    def test_list_orders_defaults(self, mock_get, api):
        mock_get.return_value = _mock_response({"orders": []})
        api.list_orders()
        params = mock_get.call_args[1]["params"]
        assert params["period"] == "all"
        assert "status" not in params
        assert "q" not in params

    @patch("backend.client.requests.get")
    def test_get_archived_orders(self, mock_get, api):
        mock_get.return_value = _mock_response({"orders": []})
        api.get_archived_orders()
        mock_get.assert_called_once_with("http://test:8000/api/orders/archived")

    @patch("backend.client.requests.get")
    def test_get_order(self, mock_get, api):
        mock_get.return_value = _mock_response({"id": 1})
        api.get_order(1)
        mock_get.assert_called_once_with("http://test:8000/api/orders/1")

    @patch("backend.client.requests.get")
    def test_get_order_tracking(self, mock_get, api):
        mock_get.return_value = _mock_response({"tracking": []})
        api.get_order_tracking(1)
        mock_get.assert_called_once_with("http://test:8000/api/orders/1/tracking")

    @patch("backend.client.requests.post")
    def test_cancel_order(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.cancel_order(1)
        mock_post.assert_called_once_with("http://test:8000/api/orders/1/cancel")

    @patch("backend.client.requests.post")
    def test_return_order_item(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.return_order_item(1, 2)
        mock_post.assert_called_once_with(
            "http://test:8000/api/orders/1/items/2/return"
        )

    @patch("backend.client.requests.post")
    def test_archive_order(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.archive_order(1)
        mock_post.assert_called_once_with("http://test:8000/api/orders/1/archive")


# ============================================================================
# Reviews & Q&A
# ============================================================================


class TestReviewMethods:
    @patch("backend.client.requests.get")
    def test_list_reviews(self, mock_get, api):
        mock_get.return_value = _mock_response({"reviews": []})
        api.list_reviews()
        mock_get.assert_called_once_with("http://test:8000/api/reviews")

    @patch("backend.client.requests.post")
    def test_create_review(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        api.create_review(
            product_id=1,
            rating=5,
            title="Great!",
            body="Loved it",
            images=["img1.jpg"],
        )
        call_json = mock_post.call_args[1]["json"]
        assert call_json["rating"] == 5
        assert call_json["images"] == ["img1.jpg"]
        assert "videos" not in call_json
        assert mock_post.call_args[0][0] == "http://test:8000/api/products/1/reviews"

    @patch("backend.client.requests.put")
    def test_update_review(self, mock_put, api):
        mock_put.return_value = _mock_response()
        api.update_review(1, rating=4, title="Updated")
        mock_put.assert_called_once_with(
            "http://test:8000/api/reviews/1",
            json={"rating": 4, "title": "Updated"},
        )

    @patch("backend.client.requests.delete")
    def test_delete_review(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.delete_review(1)
        mock_del.assert_called_once_with("http://test:8000/api/reviews/1")

    @patch("backend.client.requests.post")
    def test_vote_review(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.vote_review(1, is_helpful=True)
        mock_post.assert_called_once_with(
            "http://test:8000/api/reviews/1/vote", json={"is_helpful": True}
        )

    @patch("backend.client.requests.post")
    def test_create_question(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        api.create_question(1, "Does this come in blue?")
        mock_post.assert_called_once_with(
            "http://test:8000/api/products/1/questions",
            json={"question_text": "Does this come in blue?"},
        )

    @patch("backend.client.requests.post")
    def test_create_answer(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        api.create_answer(1, "Yes, it does!")
        mock_post.assert_called_once_with(
            "http://test:8000/api/questions/1/answers",
            json={"answer_text": "Yes, it does!"},
        )

    @patch("backend.client.requests.post")
    def test_vote_answer(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.vote_answer(1, is_helpful=False)
        mock_post.assert_called_once_with(
            "http://test:8000/api/answers/1/vote", json={"is_helpful": False}
        )


# ============================================================================
# Wishlists
# ============================================================================


class TestWishlistMethods:
    @patch("backend.client.requests.get")
    def test_list_wishlists(self, mock_get, api):
        mock_get.return_value = _mock_response({"wishlists": []})
        api.list_wishlists()
        mock_get.assert_called_once_with("http://test:8000/api/wishlists")

    @patch("backend.client.requests.post")
    def test_create_wishlist(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        api.create_wishlist("Birthday", is_public=True, description="My bday list")
        call_json = mock_post.call_args[1]["json"]
        assert call_json["name"] == "Birthday"
        assert call_json["is_public"] is True
        assert call_json["description"] == "My bday list"

    @patch("backend.client.requests.get")
    def test_get_wishlist(self, mock_get, api):
        mock_get.return_value = _mock_response({"id": 1, "items": []})
        api.get_wishlist(1)
        mock_get.assert_called_once_with("http://test:8000/api/wishlists/1")

    @patch("backend.client.requests.put")
    def test_update_wishlist(self, mock_put, api):
        mock_put.return_value = _mock_response()
        api.update_wishlist(1, name="Updated")
        mock_put.assert_called_once_with(
            "http://test:8000/api/wishlists/1", json={"name": "Updated"}
        )

    @patch("backend.client.requests.delete")
    def test_delete_wishlist(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.delete_wishlist(1)
        mock_del.assert_called_once_with("http://test:8000/api/wishlists/1")

    @patch("backend.client.requests.post")
    def test_add_wishlist_item(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.add_wishlist_item(1, product_id=5, priority="high", comment="Want this!")
        call_json = mock_post.call_args[1]["json"]
        assert call_json["product_id"] == 5
        assert call_json["priority"] == "high"
        assert call_json["comment"] == "Want this!"

    @patch("backend.client.requests.delete")
    def test_remove_wishlist_item(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.remove_wishlist_item(1, 2)
        mock_del.assert_called_once_with("http://test:8000/api/wishlists/1/items/2")


# ============================================================================
# Browsing History
# ============================================================================


class TestHistoryMethods:
    @patch("backend.client.requests.get")
    def test_get_browsing_history(self, mock_get, api):
        mock_get.return_value = _mock_response({"history": []})
        api.get_browsing_history(limit=10)
        mock_get.assert_called_once_with(
            "http://test:8000/api/history", params={"limit": 10}
        )

    @patch("backend.client.requests.post")
    def test_add_to_history(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.add_to_history(5)
        mock_post.assert_called_once_with("http://test:8000/api/history/5")

    @patch("backend.client.requests.delete")
    def test_clear_browsing_history(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.clear_browsing_history()
        mock_del.assert_called_once_with("http://test:8000/api/history")

    @patch("backend.client.requests.delete")
    def test_remove_from_history(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.remove_from_history(5)
        mock_del.assert_called_once_with("http://test:8000/api/history/5")


# ============================================================================
# Deals & Coupons
# ============================================================================


class TestDealMethods:
    @patch("backend.client.requests.get")
    def test_list_deals(self, mock_get, api):
        mock_get.return_value = _mock_response({"deals": []})
        api.list_deals(deal_type="lightning", discount_min=20, prime=True)
        params = mock_get.call_args[1]["params"]
        assert params["deal_type"] == "lightning"
        assert params["discount_min"] == 20
        assert params["prime"] is True

    @patch("backend.client.requests.get")
    def test_get_lightning_deals(self, mock_get, api):
        mock_get.return_value = _mock_response({"deals": []})
        api.get_lightning_deals()
        mock_get.assert_called_once_with(
            "http://test:8000/api/deals/lightning", params={"limit": 20}
        )

    @patch("backend.client.requests.get")
    def test_get_deals_today(self, mock_get, api):
        mock_get.return_value = _mock_response({"deals": []})
        api.get_deals_today()
        mock_get.assert_called_once_with(
            "http://test:8000/api/deals/today", params={"limit": 20}
        )

    @patch("backend.client.requests.get")
    def test_get_deal_coupons(self, mock_get, api):
        mock_get.return_value = _mock_response({"coupons": []})
        api.get_deal_coupons()
        mock_get.assert_called_once_with(
            "http://test:8000/api/deals/coupons", params={"limit": 50}
        )

    @patch("backend.client.requests.post")
    def test_claim_deal(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.claim_deal(1)
        mock_post.assert_called_once_with("http://test:8000/api/deals/1/claim")

    @patch("backend.client.requests.post")
    def test_clip_coupon(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.clip_coupon(1)
        mock_post.assert_called_once_with("http://test:8000/api/coupons/1/clip")


# ============================================================================
# Sellers
# ============================================================================


class TestSellerMethods:
    @patch("backend.client.requests.get")
    def test_get_seller(self, mock_get, api):
        mock_get.return_value = _mock_response({"id": 1, "name": "Amazon"})
        api.get_seller(1)
        mock_get.assert_called_once_with("http://test:8000/api/sellers/1")

    @patch("backend.client.requests.get")
    def test_get_seller_products(self, mock_get, api):
        mock_get.return_value = _mock_response({"products": []})
        api.get_seller_products(1, page=2, limit=10)
        params = mock_get.call_args[1]["params"]
        assert params["page"] == 2
        assert params["limit"] == 10

    @patch("backend.client.requests.get")
    def test_get_seller_reviews(self, mock_get, api):
        mock_get.return_value = _mock_response({"reviews": []})
        api.get_seller_reviews(1)
        mock_get.assert_called_once_with(
            "http://test:8000/api/sellers/1/reviews", params={"limit": 20}
        )


# ============================================================================
# Subscriptions
# ============================================================================


class TestSubscriptionMethods:
    @patch("backend.client.requests.get")
    def test_list_subscriptions(self, mock_get, api):
        mock_get.return_value = _mock_response({"subscriptions": []})
        api.list_subscriptions()
        mock_get.assert_called_once_with("http://test:8000/api/subscriptions")

    @patch("backend.client.requests.post")
    def test_create_subscription(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        api.create_subscription(
            product_id=1,
            quantity=2,
            frequency_months=3,
            shipping_address_id=1,
            payment_method_id=1,
        )
        call_json = mock_post.call_args[1]["json"]
        assert call_json["product_id"] == 1
        assert call_json["frequency_months"] == 3
        assert "variant_id" not in call_json

    @patch("backend.client.requests.put")
    def test_update_subscription(self, mock_put, api):
        mock_put.return_value = _mock_response()
        api.update_subscription(1, frequency_months=2)
        mock_put.assert_called_once_with(
            "http://test:8000/api/subscriptions/1", json={"frequency_months": 2}
        )

    @patch("backend.client.requests.post")
    def test_skip_subscription_delivery(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.skip_subscription_delivery(1)
        mock_post.assert_called_once_with("http://test:8000/api/subscriptions/1/skip")

    @patch("backend.client.requests.delete")
    def test_cancel_subscription(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.cancel_subscription(1)
        mock_del.assert_called_once_with("http://test:8000/api/subscriptions/1")


# ============================================================================
# Price Watch & Buy Again
# ============================================================================


class TestPriceWatchMethods:
    @patch("backend.client.requests.get")
    def test_list_price_watches(self, mock_get, api):
        mock_get.return_value = _mock_response({"watches": []})
        api.list_price_watches()
        mock_get.assert_called_once_with("http://test:8000/api/price-watch")

    @patch("backend.client.requests.post")
    def test_create_price_watch(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        api.create_price_watch(product_id=1, target_price=50.0)
        call_json = mock_post.call_args[1]["json"]
        assert call_json["product_id"] == 1
        assert call_json["target_price"] == 50.0

    @patch("backend.client.requests.post")
    def test_create_price_watch_no_target(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        api.create_price_watch(product_id=1)
        call_json = mock_post.call_args[1]["json"]
        assert "target_price" not in call_json

    @patch("backend.client.requests.delete")
    def test_delete_price_watch(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.delete_price_watch(1)
        mock_del.assert_called_once_with("http://test:8000/api/price-watch/1")

    @patch("backend.client.requests.get")
    def test_get_buy_again(self, mock_get, api):
        mock_get.return_value = _mock_response({"products": []})
        api.get_buy_again()
        mock_get.assert_called_once_with("http://test:8000/api/buy-again")

    @patch("backend.client.requests.get")
    def test_get_subscribe_eligible(self, mock_get, api):
        mock_get.return_value = _mock_response({"products": []})
        api.get_subscribe_eligible()
        mock_get.assert_called_once_with(
            "http://test:8000/api/buy-again/subscribe-eligible"
        )


# ============================================================================
# Gift Cards
# ============================================================================


class TestGiftCardMethods:
    @patch("backend.client.requests.get")
    def test_list_gift_cards(self, mock_get, api):
        mock_get.return_value = _mock_response({"gift_cards": []})
        api.list_gift_cards()
        mock_get.assert_called_once_with("http://test:8000/api/gift-cards")

    @patch("backend.client.requests.get")
    def test_get_gift_card_balance(self, mock_get, api):
        mock_get.return_value = _mock_response({"balance": 50.0})
        api.get_gift_card_balance()
        mock_get.assert_called_once_with("http://test:8000/api/gift-cards/balance")

    @patch("backend.client.requests.post")
    def test_redeem_gift_card(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.redeem_gift_card("ABCD-1234")
        mock_post.assert_called_once_with(
            "http://test:8000/api/gift-cards/redeem", json={"code": "ABCD-1234"}
        )

    @patch("backend.client.requests.post")
    def test_reload_gift_card(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.reload_gift_card(100.0, payment_method_id=1)
        call_json = mock_post.call_args[1]["json"]
        assert call_json["amount"] == 100.0
        assert call_json["payment_method_id"] == 1

    @patch("backend.client.requests.get")
    def test_get_gift_card_transactions(self, mock_get, api):
        mock_get.return_value = _mock_response({"transactions": []})
        api.get_gift_card_transactions()
        mock_get.assert_called_once_with(
            "http://test:8000/api/gift-cards/transactions"
        )


# ============================================================================
# Alexa Shopping List
# ============================================================================


class TestAlexaMethods:
    @patch("backend.client.requests.get")
    def test_get_alexa_list(self, mock_get, api):
        mock_get.return_value = _mock_response({"items": []})
        api.get_alexa_list()
        mock_get.assert_called_once_with("http://test:8000/api/alexa-list")

    @patch("backend.client.requests.post")
    def test_add_alexa_list_item(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        api.add_alexa_list_item("Milk", quantity=2)
        mock_post.assert_called_once_with(
            "http://test:8000/api/alexa-list",
            json={"item_name": "Milk", "quantity": 2},
        )

    @patch("backend.client.requests.delete")
    def test_delete_alexa_list_item(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.delete_alexa_list_item(1)
        mock_del.assert_called_once_with("http://test:8000/api/alexa-list/1")

    @patch("backend.client.requests.post")
    def test_complete_alexa_list_item(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.complete_alexa_list_item(1)
        mock_post.assert_called_once_with(
            "http://test:8000/api/alexa-list/1/complete"
        )

    @patch("backend.client.requests.post")
    def test_alexa_item_add_to_cart(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.alexa_item_add_to_cart(1)
        mock_post.assert_called_once_with(
            "http://test:8000/api/alexa-list/1/add-to-cart"
        )


# ============================================================================
# Messages
# ============================================================================


class TestMessageMethods:
    @patch("backend.client.requests.get")
    def test_list_messages(self, mock_get, api):
        mock_get.return_value = _mock_response({"messages": []})
        api.list_messages()
        mock_get.assert_called_once_with("http://test:8000/api/messages")

    @patch("backend.client.requests.get")
    def test_get_message(self, mock_get, api):
        mock_get.return_value = _mock_response({"id": 1})
        api.get_message(1)
        mock_get.assert_called_once_with("http://test:8000/api/messages/1")

    @patch("backend.client.requests.put")
    def test_mark_message_read(self, mock_put, api):
        mock_put.return_value = _mock_response()
        api.mark_message_read(1)
        mock_put.assert_called_once_with("http://test:8000/api/messages/1/read")

    @patch("backend.client.requests.delete")
    def test_delete_message(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.delete_message(1)
        mock_del.assert_called_once_with("http://test:8000/api/messages/1")

    @patch("backend.client.requests.post")
    def test_reply_to_message(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.reply_to_message("Re: Order", "Thanks!", related_order_id=1)
        call_json = mock_post.call_args[1]["json"]
        assert call_json["subject"] == "Re: Order"
        assert call_json["body"] == "Thanks!"
        assert call_json["related_order_id"] == 1


# ============================================================================
# Recommendations
# ============================================================================


class TestRecommendationMethods:
    @patch("backend.client.requests.get")
    def test_get_recommendations(self, mock_get, api):
        mock_get.return_value = _mock_response({"recommendations": []})
        api.get_recommendations(limit=5)
        mock_get.assert_called_once_with(
            "http://test:8000/api/recommendations", params={"limit": 5}
        )

    @patch("backend.client.requests.get")
    def test_get_deal_recommendations(self, mock_get, api):
        mock_get.return_value = _mock_response({"recommendations": []})
        api.get_deal_recommendations()
        mock_get.assert_called_once_with(
            "http://test:8000/api/recommendations/deals", params={"limit": 20}
        )

    @patch("backend.client.requests.get")
    def test_get_buy_again_recommendations(self, mock_get, api):
        mock_get.return_value = _mock_response({"recommendations": []})
        api.get_buy_again_recommendations()
        mock_get.assert_called_once_with(
            "http://test:8000/api/recommendations/buy-again", params={"limit": 20}
        )

    @patch("backend.client.requests.get")
    def test_get_inspired_by(self, mock_get, api):
        mock_get.return_value = _mock_response({"recommendations": []})
        api.get_inspired_by()
        mock_get.assert_called_once_with(
            "http://test:8000/api/recommendations/inspired-by", params={"limit": 20}
        )


# ============================================================================
# Recalls, Preferences, Credit Cards, Music, Registries, Business
# ============================================================================


class TestMiscMethods:
    @patch("backend.client.requests.get")
    def test_list_recalls(self, mock_get, api):
        mock_get.return_value = _mock_response({"recalls": []})
        api.list_recalls()
        mock_get.assert_called_once_with("http://test:8000/api/recalls")

    @patch("backend.client.requests.post")
    def test_acknowledge_recall(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.acknowledge_recall(1)
        mock_post.assert_called_once_with(
            "http://test:8000/api/recalls/1/acknowledge"
        )

    @patch("backend.client.requests.get")
    def test_get_preferences(self, mock_get, api):
        mock_get.return_value = _mock_response({"language": "en"})
        api.get_preferences()
        mock_get.assert_called_once_with("http://test:8000/api/preferences")

    @patch("backend.client.requests.put")
    def test_update_preferences(self, mock_put, api):
        mock_put.return_value = _mock_response()
        api.update_preferences(language="es")
        mock_put.assert_called_once_with(
            "http://test:8000/api/preferences", json={"language": "es"}
        )

    @patch("backend.client.requests.get")
    def test_list_credit_cards(self, mock_get, api):
        mock_get.return_value = _mock_response({"cards": []})
        api.list_credit_cards()
        mock_get.assert_called_once_with("http://test:8000/api/credit-cards")

    @patch("backend.client.requests.get")
    def test_get_credit_card_rewards(self, mock_get, api):
        mock_get.return_value = _mock_response({"rewards": 100})
        api.get_credit_card_rewards(1)
        mock_get.assert_called_once_with(
            "http://test:8000/api/credit-cards/1/rewards"
        )

    @patch("backend.client.requests.get")
    def test_get_music_library(self, mock_get, api):
        mock_get.return_value = _mock_response({"tracks": []})
        api.get_music_library()
        mock_get.assert_called_once_with("http://test:8000/api/music/library")

    @patch("backend.client.requests.post")
    def test_upload_music(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        api.upload_music("Song", "Artist", album="Album")
        call_json = mock_post.call_args[1]["json"]
        assert call_json["title"] == "Song"
        assert call_json["artist"] == "Artist"
        assert call_json["album"] == "Album"

    @patch("backend.client.requests.delete")
    def test_delete_music_upload(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.delete_music_upload(1)
        mock_del.assert_called_once_with("http://test:8000/api/music/uploads/1")

    @patch("backend.client.requests.get")
    def test_list_registries(self, mock_get, api):
        mock_get.return_value = _mock_response({"registries": []})
        api.list_registries()
        mock_get.assert_called_once_with("http://test:8000/api/registries")

    @patch("backend.client.requests.post")
    def test_create_registry(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        api.create_registry("wedding", "Our Wedding", event_date="2025-06-15")
        call_json = mock_post.call_args[1]["json"]
        assert call_json["type"] == "wedding"
        assert call_json["event_date"] == "2025-06-15"

    @patch("backend.client.requests.get")
    def test_search_registries(self, mock_get, api):
        mock_get.return_value = _mock_response({"registries": []})
        api.search_registries("Smith", type="wedding")
        params = mock_get.call_args[1]["params"]
        assert params["q"] == "Smith"
        assert params["type"] == "wedding"

    @patch("backend.client.requests.get")
    def test_get_business_account(self, mock_get, api):
        mock_get.return_value = _mock_response({"business_name": "Acme"})
        api.get_business_account()
        mock_get.assert_called_once_with("http://test:8000/api/business")

    @patch("backend.client.requests.post")
    def test_create_business_account(self, mock_post, api):
        mock_post.return_value = _mock_response({"id": 1})
        api.create_business_account("Acme", "llc", tax_id="12-345")
        call_json = mock_post.call_args[1]["json"]
        assert call_json["business_name"] == "Acme"
        assert call_json["tax_id"] == "12-345"

    @patch("backend.client.requests.post")
    def test_add_business_user(self, mock_post, api):
        mock_post.return_value = _mock_response()
        api.add_business_user(user_id=2, role="admin", spending_limit=1000)
        call_json = mock_post.call_args[1]["json"]
        assert call_json["user_id"] == 2
        assert call_json["role"] == "admin"
        assert call_json["spending_limit"] == 1000

    @patch("backend.client.requests.delete")
    def test_remove_business_user(self, mock_del, api):
        mock_del.return_value = _mock_response()
        api.remove_business_user(3)
        mock_del.assert_called_once_with("http://test:8000/api/business/users/3")
