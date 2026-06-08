"""Comprehensive API tests for Mercato mockup application."""

import json


class TestProductEndpoints:
    """Tests for product-related endpoints."""

    def test_list_products(self, seeded_client):
        response = seeded_client.get("/api/products")
        assert response.status_code == 200
        data = response.json()
        assert "products" in data
        assert len(data["products"]) == 2

    def test_list_products_with_search(self, seeded_client):
        response = seeded_client.get("/api/products?q=iPhone")
        assert response.status_code == 200
        data = response.json()
        assert len(data["products"]) == 1
        assert "iPhone" in data["products"][0]["title"]

    def test_list_products_with_category(self, seeded_client):
        response = seeded_client.get("/api/products?category_id=1")
        assert response.status_code == 200
        data = response.json()
        assert len(data["products"]) == 2

    def test_list_products_pagination(self, seeded_client):
        response = seeded_client.get("/api/products?limit=1&offset=0")
        assert response.status_code == 200
        data = response.json()
        assert len(data["products"]) == 1

    def test_get_product_by_id(self, seeded_client):
        response = seeded_client.get("/api/products/1")
        assert response.status_code == 200
        data = response.json()
        assert data["asin"] == "B09V3KXJPB"
        assert data["title"] == "Apple iPhone 14 Pro"
        assert data["price"] == 999.00

    def test_get_best_sellers(self, seeded_client):
        response = seeded_client.get("/api/products/best-sellers")
        assert response.status_code == 200
        data = response.json()
        assert "products" in data

    def test_get_product_not_found(self, seeded_client):
        response = seeded_client.get("/api/products/999")
        assert response.status_code == 404


class TestCartEndpoints:
    """Tests for cart-related endpoints."""

    def test_get_cart(self, auth_client):
        response = auth_client.get("/api/cart")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data

    def test_add_to_cart(self, auth_client):
        response = auth_client.post(
            "/api/cart/items", json={"product_id": 2, "quantity": 1}
        )
        assert response.status_code == 200
        assert "message" in response.json()

    def test_update_cart_quantity(self, auth_client):
        response = auth_client.put("/api/cart/items/1", json={"quantity": 3})
        assert response.status_code == 200

    def test_remove_from_cart(self, auth_client):
        response = auth_client.delete("/api/cart/items/1")
        assert response.status_code == 200


class TestWishlistEndpoints:
    """Tests for wishlist-related endpoints."""

    def test_get_wishlists(self, auth_client):
        response = auth_client.get("/api/wishlists")
        assert response.status_code == 200
        data = response.json()
        assert "wishlists" in data
        assert len(data["wishlists"]) == 1

    def test_get_wishlist_items(self, auth_client):
        response = auth_client.get("/api/wishlists/1")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert len(data["items"]) == 1

    def test_create_wishlist(self, auth_client):
        response = auth_client.post("/api/wishlists", json={"name": "Birthday List"})
        assert response.status_code == 200
        assert "id" in response.json()

    def test_add_to_wishlist(self, auth_client):
        response = auth_client.post("/api/wishlists/1/items", json={"product_id": 1})
        assert response.status_code == 200

    def test_remove_from_wishlist(self, auth_client):
        response = auth_client.delete("/api/wishlists/1/items/1")
        assert response.status_code == 200


class TestOrderEndpoints:
    """Tests for order-related endpoints."""

    def test_get_orders(self, auth_client):
        response = auth_client.get("/api/orders")
        assert response.status_code == 200
        data = response.json()
        assert "orders" in data
        assert len(data["orders"]) == 1

    def test_get_order_details(self, auth_client):
        response = auth_client.get("/api/orders/1")
        assert response.status_code == 200
        data = response.json()
        assert data["order_number"] == "112-3456789-0123456"
        assert data["status"] == "delivered"

    def test_get_order_not_found(self, auth_client):
        response = auth_client.get("/api/orders/999")
        assert response.status_code == 404


class TestReviewEndpoints:
    """Tests for review-related endpoints."""

    def test_get_product_reviews(self, seeded_client):
        response = seeded_client.get("/api/products/1/reviews")
        assert response.status_code == 200
        data = response.json()
        assert "reviews" in data
        assert len(data["reviews"]) == 1

    def test_create_review(self, auth_client):
        response = auth_client.post(
            "/api/products/2/reviews",
            json={
                "rating": 4,
                "title": "Good headphones",
                "body": "Really nice sound quality!",
            },
        )
        assert response.status_code == 200
        assert "id" in response.json()


class TestAuthEndpoints:
    """Tests for authentication endpoints."""

    def test_register(self, client):
        response = client.post(
            "/api/auth/register",
            json={
                "email": "newuser@example.com",
                "password": "password123",
                "name": "New User",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["email"] == "newuser@example.com"

    def test_register_duplicate_email(self, seeded_client):
        response = seeded_client.post(
            "/api/auth/register",
            json={
                "email": "john.doe@example.com",
                "password": "password123",
                "name": "John Doe",
            },
        )
        assert response.status_code == 400

    def test_login(self, seeded_client):
        # First register a user with known password
        seeded_client.post(
            "/api/auth/register",
            json={
                "email": "testlogin@example.com",
                "password": "testpass123",
                "name": "Test Login",
            },
        )

        # Then login
        response = seeded_client.post(
            "/api/auth/login",
            json={"email": "testlogin@example.com", "password": "testpass123"},
        )
        assert response.status_code == 200
        assert "session_token" in response.cookies

    def test_login_invalid_credentials(self, seeded_client):
        response = seeded_client.post(
            "/api/auth/login",
            json={"email": "john.doe@example.com", "password": "wrongpassword"},
        )
        assert response.status_code == 401

    def test_logout(self, auth_client):
        response = auth_client.post("/api/auth/logout")
        assert response.status_code == 200

    def test_get_current_user(self, auth_client):
        response = auth_client.get("/api/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "john.doe@example.com"
        assert data["name"] == "John Doe"


class TestAddressEndpoints:
    """Tests for address-related endpoints."""

    def test_get_addresses(self, auth_client):
        response = auth_client.get("/api/user/addresses")
        assert response.status_code == 200
        data = response.json()
        assert "addresses" in data
        assert len(data["addresses"]) == 1

    def test_create_address(self, auth_client):
        response = auth_client.post(
            "/api/user/addresses",
            json={
                "full_name": "Jane Doe",
                "phone": "+1-555-999-8888",
                "address_line1": "456 Oak Street",
                "city": "Los Angeles",
                "state": "CA",
                "zip_code": "90001",
                "country": "United States",
            },
        )
        assert response.status_code == 200
        assert "id" in response.json()

    def test_update_address(self, auth_client):
        response = auth_client.put(
            "/api/user/addresses/1",
            json={
                "full_name": "John Updated",
                "phone": "+1-555-123-4567",
                "address_line1": "789 New Street",
                "city": "San Francisco",
                "state": "CA",
                "zip_code": "94103",
                "country": "United States",
            },
        )
        assert response.status_code == 200

    def test_delete_address(self, auth_client):
        # First create an address
        create_resp = auth_client.post(
            "/api/user/addresses",
            json={
                "full_name": "Delete Me",
                "phone": "+1-555-000-0000",
                "address_line1": "Delete Street",
                "city": "Delete City",
                "state": "CA",
                "zip_code": "00000",
                "country": "United States",
            },
        )
        addr_id = create_resp.json()["id"]

        # Then delete it
        response = auth_client.delete(f"/api/user/addresses/{addr_id}")
        assert response.status_code == 200


class TestPaymentMethodEndpoints:
    """Tests for payment method endpoints."""

    def test_get_payment_methods(self, auth_client):
        response = auth_client.get("/api/user/payment-methods")
        assert response.status_code == 200
        data = response.json()
        assert "payment_methods" in data
        assert len(data["payment_methods"]) == 1


class TestDepartmentEndpoints:
    """Tests for department and category endpoints."""

    def test_get_departments(self, seeded_client):
        response = seeded_client.get("/api/departments")
        assert response.status_code == 200
        data = response.json()
        assert "departments" in data
        assert len(data["departments"]) == 1

    def test_get_department_by_slug(self, seeded_client):
        response = seeded_client.get("/api/departments/electronics")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Electronics"

    def test_get_categories(self, seeded_client):
        response = seeded_client.get("/api/categories")
        assert response.status_code == 200
        data = response.json()
        assert "categories" in data


class TestDealEndpoints:
    """Tests for deal-related endpoints."""

    def test_get_deals(self, seeded_client):
        response = seeded_client.get("/api/deals")
        assert response.status_code == 200
        data = response.json()
        assert "deals" in data
        assert len(data["deals"]) >= 1

    def test_get_lightning_deals(self, seeded_client):
        response = seeded_client.get("/api/deals/lightning")
        assert response.status_code == 200
        data = response.json()
        assert "deals" in data


class TestSubscriptionEndpoints:
    """Tests for Subscribe & Save endpoints."""

    def test_get_subscriptions(self, auth_client):
        response = auth_client.get("/api/subscriptions")
        assert response.status_code == 200
        data = response.json()
        assert "subscriptions" in data
        assert len(data["subscriptions"]) == 1

    def test_update_subscription(self, auth_client):
        response = auth_client.put(
            "/api/subscriptions/1", json={"frequency_months": 2, "quantity": 2}
        )
        assert response.status_code == 200


class TestNotificationEndpoints:
    """Tests for notification endpoints."""

    def test_get_notifications(self, auth_client):
        response = auth_client.get("/api/user/notifications")
        assert response.status_code == 200
        data = response.json()
        assert "notifications" in data
        assert len(data["notifications"]) == 1

    def test_mark_notification_read(self, auth_client):
        response = auth_client.put("/api/user/notifications/1/read")
        assert response.status_code == 200


class TestSearchEndpoints:
    """Tests for search-related endpoints."""

    def test_search_products(self, seeded_client):
        response = seeded_client.get("/api/search?q=iPhone")
        assert response.status_code == 200
        data = response.json()
        assert "products" in data
        assert len(data["products"]) >= 1

    def test_search_suggestions(self, seeded_client):
        response = seeded_client.get("/api/search/suggestions?q=iph")
        assert response.status_code == 200
        data = response.json()
        assert "suggestions" in data


class TestHistoryEndpoints:
    """Tests for browsing history endpoints."""

    def test_get_browsing_history(self, auth_client):
        response = auth_client.get("/api/history")
        assert response.status_code == 200
        data = response.json()
        assert "history" in data
        assert len(data["history"]) == 1


class TestSellerEndpoints:
    """Tests for seller-related endpoints."""

    def test_get_seller_by_id(self, seeded_client):
        response = seeded_client.get("/api/sellers/1")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Mercato.com"
        assert data["is_amazon"] is True

    def test_get_seller_products(self, seeded_client):
        response = seeded_client.get("/api/sellers/1/products")
        assert response.status_code == 200
        data = response.json()
        assert "products" in data


class TestCouponEndpoints:
    """Tests for coupon endpoints."""

    def test_get_coupons(self, seeded_client):
        response = seeded_client.get("/api/deals/coupons")
        assert response.status_code == 200
        data = response.json()
        assert "coupons" in data

    def test_clip_coupon(self, auth_client):
        response = auth_client.post("/api/coupons/1/clip")
        assert response.status_code == 200


class TestRecommendationEndpoints:
    """Tests for recommendation endpoints."""

    def test_get_recommendations(self, auth_client):
        response = auth_client.get("/api/recommendations")
        assert response.status_code == 200
        data = response.json()
        assert "recommendations" in data


class TestPreferenceEndpoints:
    """Tests for shopping preference endpoints."""

    def test_get_preferences(self, auth_client):
        response = auth_client.get("/api/preferences")
        assert response.status_code == 200
        data = response.json()
        assert data["language"] == "en_US"
        assert data["currency"] == "USD"

    def test_update_preferences(self, auth_client):
        response = auth_client.put(
            "/api/preferences", json={"language": "es_ES", "currency": "EUR"}
        )
        assert response.status_code == 200
