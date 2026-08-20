import requests
from typing import Optional


class CaveatShopAPI:
    def __init__(self, base_url: str = "http://localhost:8000/api"):
        self.base_url = base_url

    # ========================================================================
    # Authentication
    # ========================================================================

    def register(self, email: str, password: str, name: str, phone: str = None) -> dict:
        data = {"email": email, "password": password, "name": name}
        if phone:
            data["phone"] = phone
        res = requests.post(f"{self.base_url}/auth/register", json=data)
        return res.json()

    def login(self, email: str, password: str) -> dict:
        res = requests.post(
            f"{self.base_url}/auth/login", json={"email": email, "password": password}
        )
        return res.json()

    def logout(self) -> dict:
        res = requests.post(f"{self.base_url}/auth/logout")
        return res.json()

    def logout_all(self) -> dict:
        res = requests.post(f"{self.base_url}/auth/logout-all")
        return res.json()

    def get_current_auth_user(self) -> dict:
        res = requests.get(f"{self.base_url}/auth/me")
        return res.json()

    def list_sessions(self) -> dict:
        res = requests.get(f"{self.base_url}/auth/sessions")
        return res.json()

    def delete_session(self, session_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/auth/sessions/{session_id}")
        return res.json()

    def list_lwa_apps(self) -> dict:
        res = requests.get(f"{self.base_url}/auth/lwa/apps")
        return res.json()

    def delete_lwa_app(self, app_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/auth/lwa/apps/{app_id}")
        return res.json()

    # ========================================================================
    # User / Account
    # ========================================================================

    def get_user(self) -> dict:
        res = requests.get(f"{self.base_url}/user")
        return res.json()

    def update_user(
        self,
        name: str = None,
        email: str = None,
        phone: str = None,
        avatar_url: str = None,
    ) -> dict:
        data = {}
        if name is not None:
            data["name"] = name
        if email is not None:
            data["email"] = email
        if phone is not None:
            data["phone"] = phone
        if avatar_url is not None:
            data["avatar_url"] = avatar_url
        res = requests.put(f"{self.base_url}/user", json=data)
        return res.json()

    def change_password(self, current_password: str, new_password: str) -> dict:
        res = requests.put(
            f"{self.base_url}/user/password",
            json={"current_password": current_password, "new_password": new_password},
        )
        return res.json()

    # Addresses

    def list_addresses(self) -> dict:
        res = requests.get(f"{self.base_url}/user/addresses")
        return res.json()

    def create_address(
        self,
        full_name: str,
        phone: str,
        address_line1: str,
        city: str,
        state: str,
        zip_code: str,
        country: str = "United States",
        address_line2: str = None,
        is_default: bool = False,
        delivery_instructions: str = None,
        address_type: str = "residential",
    ) -> dict:
        data = {
            "full_name": full_name,
            "phone": phone,
            "address_line1": address_line1,
            "city": city,
            "state": state,
            "zip_code": zip_code,
            "country": country,
            "is_default": is_default,
            "address_type": address_type,
        }
        if address_line2:
            data["address_line2"] = address_line2
        if delivery_instructions:
            data["delivery_instructions"] = delivery_instructions
        res = requests.post(f"{self.base_url}/user/addresses", json=data)
        return res.json()

    def update_address(self, address_id: int, **kwargs) -> dict:
        res = requests.put(f"{self.base_url}/user/addresses/{address_id}", json=kwargs)
        return res.json()

    def delete_address(self, address_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/user/addresses/{address_id}")
        return res.json()

    # Payment Methods

    def list_payment_methods(self) -> dict:
        res = requests.get(f"{self.base_url}/user/payment-methods")
        return res.json()

    def create_payment_method(
        self,
        type: str,
        card_number_last4: str,
        card_brand: str,
        expiry_month: int,
        expiry_year: int,
        cardholder_name: str,
        billing_address_id: int = None,
        is_default: bool = False,
    ) -> dict:
        data = {
            "type": type,
            "card_number_last4": card_number_last4,
            "card_brand": card_brand,
            "expiry_month": expiry_month,
            "expiry_year": expiry_year,
            "cardholder_name": cardholder_name,
            "is_default": is_default,
        }
        if billing_address_id is not None:
            data["billing_address_id"] = billing_address_id
        res = requests.post(f"{self.base_url}/user/payment-methods", json=data)
        return res.json()

    def delete_payment_method(self, method_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/user/payment-methods/{method_id}")
        return res.json()

    # Prime & Notifications

    def get_prime_status(self) -> dict:
        res = requests.get(f"{self.base_url}/user/prime")
        return res.json()

    def list_notifications(self) -> dict:
        res = requests.get(f"{self.base_url}/user/notifications")
        return res.json()

    def mark_notification_read(self, notification_id: int) -> dict:
        res = requests.put(
            f"{self.base_url}/user/notifications/{notification_id}/read"
        )
        return res.json()

    # ========================================================================
    # Products
    # ========================================================================

    def list_products(
        self,
        q: str = None,
        department: str = None,
        category: str = None,
        brand: str = None,
        min_price: float = None,
        max_price: float = None,
        min_rating: float = None,
        prime: bool = None,
        deals: bool = None,
        condition: str = None,
        sort: str = "featured",
        page: int = 1,
        limit: int = 48,
    ) -> dict:
        params = {"sort": sort, "page": page, "limit": limit}
        if q:
            params["q"] = q
        if department:
            params["department"] = department
        if category:
            params["category"] = category
        if brand:
            params["brand"] = brand
        if min_price is not None:
            params["min_price"] = min_price
        if max_price is not None:
            params["max_price"] = max_price
        if min_rating is not None:
            params["min_rating"] = min_rating
        if prime is not None:
            params["prime"] = prime
        if deals is not None:
            params["deals"] = deals
        if condition:
            params["condition"] = condition
        res = requests.get(f"{self.base_url}/products", params=params)
        return res.json()

    def get_best_sellers(self, limit: int = 20) -> dict:
        res = requests.get(f"{self.base_url}/products/best-sellers", params={"limit": limit})
        return res.json()

    def get_new_releases(self, limit: int = 20) -> dict:
        res = requests.get(f"{self.base_url}/products/new-releases", params={"limit": limit})
        return res.json()

    def get_movers_and_shakers(self, limit: int = 20) -> dict:
        res = requests.get(
            f"{self.base_url}/products/movers-shakers", params={"limit": limit}
        )
        return res.json()

    def get_trending(self, limit: int = 20) -> dict:
        res = requests.get(f"{self.base_url}/products/trending", params={"limit": limit})
        return res.json()

    def get_product_by_asin(self, asin: str) -> dict:
        res = requests.get(f"{self.base_url}/products/asin/{asin}")
        return res.json()

    def get_product(self, product_id: int) -> dict:
        res = requests.get(f"{self.base_url}/products/{product_id}")
        return res.json()

    def get_product_variants(self, product_id: int) -> dict:
        res = requests.get(f"{self.base_url}/products/{product_id}/variants")
        return res.json()

    def get_product_reviews(
        self,
        product_id: int,
        rating: int = None,
        sort: str = "recent",
        verified_only: bool = False,
        page: int = 1,
        limit: int = 10,
    ) -> dict:
        params = {"sort": sort, "verified_only": verified_only, "page": page, "limit": limit}
        if rating is not None:
            params["rating"] = rating
        res = requests.get(
            f"{self.base_url}/products/{product_id}/reviews", params=params
        )
        return res.json()

    def get_product_reviews_summary(self, product_id: int) -> dict:
        res = requests.get(f"{self.base_url}/products/{product_id}/reviews/summary")
        return res.json()

    def get_product_questions(
        self, product_id: int, sort: str = "recent", page: int = 1, limit: int = 10
    ) -> dict:
        params = {"sort": sort, "page": page, "limit": limit}
        res = requests.get(
            f"{self.base_url}/products/{product_id}/questions", params=params
        )
        return res.json()

    def get_related_products(self, product_id: int, limit: int = 10) -> dict:
        res = requests.get(
            f"{self.base_url}/products/{product_id}/related", params={"limit": limit}
        )
        return res.json()

    def get_frequently_bought_together(self, product_id: int) -> dict:
        res = requests.get(f"{self.base_url}/products/{product_id}/frequently-bought")
        return res.json()

    def get_similar_products(self, product_id: int, limit: int = 10) -> dict:
        res = requests.get(
            f"{self.base_url}/products/{product_id}/similar", params={"limit": limit}
        )
        return res.json()

    # ========================================================================
    # Search & Browse
    # ========================================================================

    def search(
        self, q: str, department: str = None, page: int = 1, limit: int = 48
    ) -> dict:
        params = {"q": q, "page": page, "limit": limit}
        if department:
            params["department"] = department
        res = requests.get(f"{self.base_url}/search", params=params)
        return res.json()

    def search_suggestions(self, q: str) -> dict:
        res = requests.get(f"{self.base_url}/search/suggestions", params={"q": q})
        return res.json()

    def get_search_history(self) -> dict:
        res = requests.get(f"{self.base_url}/search/history")
        return res.json()

    def save_search_history(self, q: str) -> dict:
        res = requests.post(f"{self.base_url}/search/history", params={"q": q})
        return res.json()

    def clear_search_history(self) -> dict:
        res = requests.delete(f"{self.base_url}/search/history")
        return res.json()

    def list_departments(self) -> dict:
        res = requests.get(f"{self.base_url}/departments")
        return res.json()

    def get_department(self, slug: str) -> dict:
        res = requests.get(f"{self.base_url}/departments/{slug}")
        return res.json()

    def get_department_categories(self, slug: str) -> dict:
        res = requests.get(f"{self.base_url}/departments/{slug}/categories")
        return res.json()

    def list_categories(self) -> dict:
        res = requests.get(f"{self.base_url}/categories")
        return res.json()

    def get_category(self, slug: str) -> dict:
        res = requests.get(f"{self.base_url}/categories/{slug}")
        return res.json()

    def get_category_products(
        self, slug: str, page: int = 1, limit: int = 48
    ) -> dict:
        res = requests.get(
            f"{self.base_url}/categories/{slug}/products",
            params={"page": page, "limit": limit},
        )
        return res.json()

    def get_category_subcategories(self, slug: str) -> dict:
        res = requests.get(f"{self.base_url}/categories/{slug}/subcategories")
        return res.json()

    # ========================================================================
    # Cart
    # ========================================================================

    def get_cart(self) -> dict:
        res = requests.get(f"{self.base_url}/cart")
        return res.json()

    def add_to_cart(
        self,
        product_id: int,
        variant_id: int = None,
        quantity: int = 1,
        is_gift: bool = False,
        gift_message: str = None,
    ) -> dict:
        data = {"product_id": product_id, "quantity": quantity, "is_gift": is_gift}
        if variant_id is not None:
            data["variant_id"] = variant_id
        if gift_message:
            data["gift_message"] = gift_message
        res = requests.post(f"{self.base_url}/cart/items", json=data)
        return res.json()

    def update_cart_item(
        self,
        item_id: int,
        quantity: int = None,
        is_gift: bool = None,
        gift_message: str = None,
    ) -> dict:
        data = {}
        if quantity is not None:
            data["quantity"] = quantity
        if is_gift is not None:
            data["is_gift"] = is_gift
        if gift_message is not None:
            data["gift_message"] = gift_message
        res = requests.put(f"{self.base_url}/cart/items/{item_id}", json=data)
        return res.json()

    def remove_cart_item(self, item_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/cart/items/{item_id}")
        return res.json()

    def save_for_later(self, item_id: int) -> dict:
        res = requests.post(f"{self.base_url}/cart/items/{item_id}/save-for-later")
        return res.json()

    def move_to_cart(self, item_id: int) -> dict:
        res = requests.post(f"{self.base_url}/cart/items/{item_id}/move-to-cart")
        return res.json()

    def clear_cart(self) -> dict:
        res = requests.delete(f"{self.base_url}/cart")
        return res.json()

    def apply_coupon(self, code: str) -> dict:
        res = requests.post(f"{self.base_url}/cart/apply-coupon", json={"code": code})
        return res.json()

    # ========================================================================
    # Checkout & Orders
    # ========================================================================

    def start_checkout(self) -> dict:
        res = requests.post(f"{self.base_url}/checkout/start")
        return res.json()

    def set_checkout_shipping(self, address_id: int, shipping_method: str = "standard") -> dict:
        res = requests.put(
            f"{self.base_url}/checkout/shipping",
            json={"address_id": address_id, "shipping_method": shipping_method},
        )
        return res.json()

    def set_checkout_payment(
        self, payment_method_id: int, gift_card_amount: float = None
    ) -> dict:
        data = {"payment_method_id": payment_method_id}
        if gift_card_amount is not None:
            data["gift_card_amount"] = gift_card_amount
        res = requests.put(f"{self.base_url}/checkout/payment", json=data)
        return res.json()

    def get_checkout_summary(self) -> dict:
        res = requests.get(f"{self.base_url}/checkout/summary")
        return res.json()

    def get_shipping_options(self) -> dict:
        res = requests.get(f"{self.base_url}/checkout/shipping-options")
        return res.json()

    def place_order(self, is_gift: bool = False, gift_message: str = None) -> dict:
        data = {"is_gift": is_gift}
        if gift_message:
            data["gift_message"] = gift_message
        res = requests.post(f"{self.base_url}/checkout/place-order", json=data)
        return res.json()

    def list_orders(
        self,
        status: str = None,
        period: str = "all",
        q: str = None,
        page: int = 1,
        limit: int = 10,
    ) -> dict:
        params = {"period": period, "page": page, "limit": limit}
        if status:
            params["status"] = status
        if q:
            params["q"] = q
        res = requests.get(f"{self.base_url}/orders", params=params)
        return res.json()

    def get_archived_orders(self) -> dict:
        res = requests.get(f"{self.base_url}/orders/archived")
        return res.json()

    def get_order(self, order_id: int) -> dict:
        res = requests.get(f"{self.base_url}/orders/{order_id}")
        return res.json()

    def get_order_tracking(self, order_id: int) -> dict:
        res = requests.get(f"{self.base_url}/orders/{order_id}/tracking")
        return res.json()

    def cancel_order(self, order_id: int) -> dict:
        res = requests.post(f"{self.base_url}/orders/{order_id}/cancel")
        return res.json()

    def return_order_item(self, order_id: int, item_id: int) -> dict:
        res = requests.post(
            f"{self.base_url}/orders/{order_id}/items/{item_id}/return"
        )
        return res.json()

    def archive_order(self, order_id: int) -> dict:
        res = requests.post(f"{self.base_url}/orders/{order_id}/archive")
        return res.json()

    # ========================================================================
    # Reviews & Q&A
    # ========================================================================

    def list_reviews(self) -> dict:
        res = requests.get(f"{self.base_url}/reviews")
        return res.json()

    def create_review(
        self,
        product_id: int,
        rating: int,
        title: str,
        body: str,
        images: list[str] = None,
        videos: list[str] = None,
    ) -> dict:
        data = {"rating": rating, "title": title, "body": body}
        if images:
            data["images"] = images
        if videos:
            data["videos"] = videos
        res = requests.post(
            f"{self.base_url}/products/{product_id}/reviews", json=data
        )
        return res.json()

    def update_review(self, review_id: int, **kwargs) -> dict:
        res = requests.put(f"{self.base_url}/reviews/{review_id}", json=kwargs)
        return res.json()

    def delete_review(self, review_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/reviews/{review_id}")
        return res.json()

    def vote_review(self, review_id: int, is_helpful: bool) -> dict:
        res = requests.post(
            f"{self.base_url}/reviews/{review_id}/vote",
            json={"is_helpful": is_helpful},
        )
        return res.json()

    def create_question(self, product_id: int, question_text: str) -> dict:
        res = requests.post(
            f"{self.base_url}/products/{product_id}/questions",
            json={"question_text": question_text},
        )
        return res.json()

    def create_answer(self, question_id: int, answer_text: str) -> dict:
        res = requests.post(
            f"{self.base_url}/questions/{question_id}/answers",
            json={"answer_text": answer_text},
        )
        return res.json()

    def vote_answer(self, answer_id: int, is_helpful: bool) -> dict:
        res = requests.post(
            f"{self.base_url}/answers/{answer_id}/vote",
            json={"is_helpful": is_helpful},
        )
        return res.json()

    # ========================================================================
    # Wishlists
    # ========================================================================

    def list_wishlists(self) -> dict:
        res = requests.get(f"{self.base_url}/wishlists")
        return res.json()

    def create_wishlist(
        self, name: str, is_public: bool = False, description: str = None
    ) -> dict:
        data = {"name": name, "is_public": is_public}
        if description:
            data["description"] = description
        res = requests.post(f"{self.base_url}/wishlists", json=data)
        return res.json()

    def get_wishlist(self, wishlist_id: int) -> dict:
        res = requests.get(f"{self.base_url}/wishlists/{wishlist_id}")
        return res.json()

    def update_wishlist(self, wishlist_id: int, **kwargs) -> dict:
        res = requests.put(f"{self.base_url}/wishlists/{wishlist_id}", json=kwargs)
        return res.json()

    def delete_wishlist(self, wishlist_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/wishlists/{wishlist_id}")
        return res.json()

    def add_wishlist_item(
        self,
        wishlist_id: int,
        product_id: int,
        variant_id: int = None,
        quantity_desired: int = 1,
        priority: str = "medium",
        comment: str = None,
    ) -> dict:
        data = {
            "product_id": product_id,
            "quantity_desired": quantity_desired,
            "priority": priority,
        }
        if variant_id is not None:
            data["variant_id"] = variant_id
        if comment:
            data["comment"] = comment
        res = requests.post(
            f"{self.base_url}/wishlists/{wishlist_id}/items", json=data
        )
        return res.json()

    def remove_wishlist_item(self, wishlist_id: int, item_id: int) -> dict:
        res = requests.delete(
            f"{self.base_url}/wishlists/{wishlist_id}/items/{item_id}"
        )
        return res.json()

    # ========================================================================
    # Browsing History
    # ========================================================================

    def get_browsing_history(self, limit: int = 50) -> dict:
        res = requests.get(f"{self.base_url}/history", params={"limit": limit})
        return res.json()

    def add_to_history(self, product_id: int) -> dict:
        res = requests.post(f"{self.base_url}/history/{product_id}")
        return res.json()

    def clear_browsing_history(self) -> dict:
        res = requests.delete(f"{self.base_url}/history")
        return res.json()

    def remove_from_history(self, product_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/history/{product_id}")
        return res.json()

    # ========================================================================
    # Recommendations
    # ========================================================================

    def get_recommendations(self, limit: int = 20) -> dict:
        res = requests.get(f"{self.base_url}/recommendations", params={"limit": limit})
        return res.json()

    def get_deal_recommendations(self, limit: int = 20) -> dict:
        res = requests.get(
            f"{self.base_url}/recommendations/deals", params={"limit": limit}
        )
        return res.json()

    def get_buy_again_recommendations(self, limit: int = 20) -> dict:
        res = requests.get(
            f"{self.base_url}/recommendations/buy-again", params={"limit": limit}
        )
        return res.json()

    def get_inspired_by(self, limit: int = 20) -> dict:
        res = requests.get(
            f"{self.base_url}/recommendations/inspired-by", params={"limit": limit}
        )
        return res.json()

    # ========================================================================
    # Deals & Coupons
    # ========================================================================

    def list_deals(
        self,
        department: str = None,
        discount_min: float = None,
        deal_type: str = None,
        prime: bool = False,
        limit: int = 50,
    ) -> dict:
        params = {"prime": prime, "limit": limit}
        if department:
            params["department"] = department
        if discount_min is not None:
            params["discount_min"] = discount_min
        if deal_type:
            params["deal_type"] = deal_type
        res = requests.get(f"{self.base_url}/deals", params=params)
        return res.json()

    def get_lightning_deals(self, limit: int = 20) -> dict:
        res = requests.get(f"{self.base_url}/deals/lightning", params={"limit": limit})
        return res.json()

    def get_deals_today(self, limit: int = 20) -> dict:
        res = requests.get(f"{self.base_url}/deals/today", params={"limit": limit})
        return res.json()

    def get_deal_coupons(self, limit: int = 50) -> dict:
        res = requests.get(f"{self.base_url}/deals/coupons", params={"limit": limit})
        return res.json()

    def claim_deal(self, deal_id: int) -> dict:
        res = requests.post(f"{self.base_url}/deals/{deal_id}/claim")
        return res.json()

    def clip_coupon(self, coupon_id: int) -> dict:
        res = requests.post(f"{self.base_url}/coupons/{coupon_id}/clip")
        return res.json()

    def create_deal(self, **kwargs) -> dict:
        res = requests.post(f"{self.base_url}/deals", json=kwargs)
        return res.json()

    def remove_deal(self, deal_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/deals/{deal_id}")
        return res.json()

    # ========================================================================
    # Sellers
    # ========================================================================

    def get_seller(self, seller_id: int) -> dict:
        res = requests.get(f"{self.base_url}/sellers/{seller_id}")
        return res.json()

    def get_seller_products(
        self, seller_id: int, page: int = 1, limit: int = 20
    ) -> dict:
        res = requests.get(
            f"{self.base_url}/sellers/{seller_id}/products",
            params={"page": page, "limit": limit},
        )
        return res.json()

    def get_seller_reviews(self, seller_id: int, limit: int = 20) -> dict:
        res = requests.get(
            f"{self.base_url}/sellers/{seller_id}/reviews", params={"limit": limit}
        )
        return res.json()

    # ========================================================================
    # Subscriptions (Subscribe & Save)
    # ========================================================================

    def list_subscriptions(self) -> dict:
        res = requests.get(f"{self.base_url}/subscriptions")
        return res.json()

    def create_subscription(
        self,
        product_id: int,
        quantity: int,
        frequency_months: int,
        shipping_address_id: int,
        payment_method_id: int,
        variant_id: int = None,
    ) -> dict:
        data = {
            "product_id": product_id,
            "quantity": quantity,
            "frequency_months": frequency_months,
            "shipping_address_id": shipping_address_id,
            "payment_method_id": payment_method_id,
        }
        if variant_id is not None:
            data["variant_id"] = variant_id
        res = requests.post(f"{self.base_url}/subscriptions", json=data)
        return res.json()

    def update_subscription(self, sub_id: int, **kwargs) -> dict:
        res = requests.put(f"{self.base_url}/subscriptions/{sub_id}", json=kwargs)
        return res.json()

    def skip_subscription_delivery(self, sub_id: int) -> dict:
        res = requests.post(f"{self.base_url}/subscriptions/{sub_id}/skip")
        return res.json()

    def cancel_subscription(self, sub_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/subscriptions/{sub_id}")
        return res.json()

    # ========================================================================
    # Price Watch & Buy Again
    # ========================================================================

    def list_price_watches(self) -> dict:
        res = requests.get(f"{self.base_url}/price-watch")
        return res.json()

    def create_price_watch(
        self, product_id: int, target_price: float = None
    ) -> dict:
        data = {"product_id": product_id}
        if target_price is not None:
            data["target_price"] = target_price
        res = requests.post(f"{self.base_url}/price-watch", json=data)
        return res.json()

    def delete_price_watch(self, watch_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/price-watch/{watch_id}")
        return res.json()

    def get_buy_again(self) -> dict:
        res = requests.get(f"{self.base_url}/buy-again")
        return res.json()

    def get_subscribe_eligible(self) -> dict:
        res = requests.get(f"{self.base_url}/buy-again/subscribe-eligible")
        return res.json()

    # ========================================================================
    # Gift Cards
    # ========================================================================

    def list_gift_cards(self) -> dict:
        res = requests.get(f"{self.base_url}/gift-cards")
        return res.json()

    def get_gift_card_balance(self) -> dict:
        res = requests.get(f"{self.base_url}/gift-cards/balance")
        return res.json()

    def redeem_gift_card(self, code: str) -> dict:
        res = requests.post(f"{self.base_url}/gift-cards/redeem", json={"code": code})
        return res.json()

    def reload_gift_card(
        self, amount: float, payment_method_id: int = None
    ) -> dict:
        data = {"amount": amount}
        if payment_method_id is not None:
            data["payment_method_id"] = payment_method_id
        res = requests.post(f"{self.base_url}/gift-cards/reload", json=data)
        return res.json()

    def get_gift_card_transactions(self) -> dict:
        res = requests.get(f"{self.base_url}/gift-cards/transactions")
        return res.json()

    # ========================================================================
    # Alexa Shopping List
    # ========================================================================

    def get_alexa_list(self) -> dict:
        res = requests.get(f"{self.base_url}/alexa-list")
        return res.json()

    def add_alexa_list_item(self, item_name: str, quantity: int = 1) -> dict:
        res = requests.post(
            f"{self.base_url}/alexa-list",
            json={"item_name": item_name, "quantity": quantity},
        )
        return res.json()

    def update_alexa_list_item(self, item_id: int, **kwargs) -> dict:
        res = requests.put(f"{self.base_url}/alexa-list/{item_id}", json=kwargs)
        return res.json()

    def delete_alexa_list_item(self, item_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/alexa-list/{item_id}")
        return res.json()

    def complete_alexa_list_item(self, item_id: int) -> dict:
        res = requests.post(f"{self.base_url}/alexa-list/{item_id}/complete")
        return res.json()

    def alexa_item_add_to_cart(self, item_id: int) -> dict:
        res = requests.post(f"{self.base_url}/alexa-list/{item_id}/add-to-cart")
        return res.json()

    # ========================================================================
    # Messages
    # ========================================================================

    def list_messages(self) -> dict:
        res = requests.get(f"{self.base_url}/messages")
        return res.json()

    def get_message(self, message_id: int) -> dict:
        res = requests.get(f"{self.base_url}/messages/{message_id}")
        return res.json()

    def mark_message_read(self, message_id: int) -> dict:
        res = requests.put(f"{self.base_url}/messages/{message_id}/read")
        return res.json()

    def delete_message(self, message_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/messages/{message_id}")
        return res.json()

    def reply_to_message(
        self, subject: str, body: str, related_order_id: int = None
    ) -> dict:
        data = {"subject": subject, "body": body}
        if related_order_id is not None:
            data["related_order_id"] = related_order_id
        res = requests.post(f"{self.base_url}/messages/reply", json=data)
        return res.json()

    # ========================================================================
    # Product Recalls & Safety
    # ========================================================================

    def list_recalls(self) -> dict:
        res = requests.get(f"{self.base_url}/recalls")
        return res.json()

    def get_recall(self, alert_id: int) -> dict:
        res = requests.get(f"{self.base_url}/recalls/{alert_id}")
        return res.json()

    def acknowledge_recall(self, alert_id: int) -> dict:
        res = requests.post(f"{self.base_url}/recalls/{alert_id}/acknowledge")
        return res.json()

    # ========================================================================
    # Preferences
    # ========================================================================

    def get_preferences(self) -> dict:
        res = requests.get(f"{self.base_url}/preferences")
        return res.json()

    def update_preferences(self, **kwargs) -> dict:
        res = requests.put(f"{self.base_url}/preferences", json=kwargs)
        return res.json()

    def update_personalization(self, **kwargs) -> dict:
        res = requests.put(f"{self.base_url}/preferences/personalization", json=kwargs)
        return res.json()

    # ========================================================================
    # Credit Cards & Rewards
    # ========================================================================

    def list_credit_cards(self) -> dict:
        res = requests.get(f"{self.base_url}/credit-cards")
        return res.json()

    def get_credit_card_rewards(self, card_id: int) -> dict:
        res = requests.get(f"{self.base_url}/credit-cards/{card_id}/rewards")
        return res.json()

    def get_credit_card_transactions(self, card_id: int) -> dict:
        res = requests.get(f"{self.base_url}/credit-cards/{card_id}/transactions")
        return res.json()

    # ========================================================================
    # Music Library
    # ========================================================================

    def get_music_library(self) -> dict:
        res = requests.get(f"{self.base_url}/music/library")
        return res.json()

    def get_music_purchases(self) -> dict:
        res = requests.get(f"{self.base_url}/music/purchases")
        return res.json()

    def get_music_uploads(self) -> dict:
        res = requests.get(f"{self.base_url}/music/uploads")
        return res.json()

    def upload_music(self, title: str, artist: str, **kwargs) -> dict:
        data = {"title": title, "artist": artist, **kwargs}
        res = requests.post(f"{self.base_url}/music/uploads", json=data)
        return res.json()

    def delete_music_upload(self, track_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/music/uploads/{track_id}")
        return res.json()

    # ========================================================================
    # Registries
    # ========================================================================

    def list_registries(self) -> dict:
        res = requests.get(f"{self.base_url}/registries")
        return res.json()

    def create_registry(
        self,
        type: str,
        name: str,
        event_date: str = None,
        is_public: bool = True,
        shipping_address_id: int = None,
    ) -> dict:
        data = {"type": type, "name": name, "is_public": is_public}
        if event_date:
            data["event_date"] = event_date
        if shipping_address_id is not None:
            data["shipping_address_id"] = shipping_address_id
        res = requests.post(f"{self.base_url}/registries", json=data)
        return res.json()

    def search_registries(self, q: str, type: str = None) -> dict:
        params = {"q": q}
        if type:
            params["type"] = type
        res = requests.get(f"{self.base_url}/registries/search", params=params)
        return res.json()

    def get_registry(self, registry_id: int) -> dict:
        res = requests.get(f"{self.base_url}/registries/{registry_id}")
        return res.json()

    def update_registry(self, registry_id: int, **kwargs) -> dict:
        res = requests.put(f"{self.base_url}/registries/{registry_id}", json=kwargs)
        return res.json()

    def delete_registry(self, registry_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/registries/{registry_id}")
        return res.json()

    def add_registry_item(
        self,
        registry_id: int,
        product_id: int,
        quantity_desired: int = 1,
        priority: str = "medium",
    ) -> dict:
        data = {
            "product_id": product_id,
            "quantity_desired": quantity_desired,
            "priority": priority,
        }
        res = requests.post(
            f"{self.base_url}/registries/{registry_id}/items", json=data
        )
        return res.json()

    def remove_registry_item(self, registry_id: int, item_id: int) -> dict:
        res = requests.delete(
            f"{self.base_url}/registries/{registry_id}/items/{item_id}"
        )
        return res.json()

    # ========================================================================
    # Business Account
    # ========================================================================

    def get_business_account(self) -> dict:
        res = requests.get(f"{self.base_url}/business")
        return res.json()

    def create_business_account(
        self, business_name: str, business_type: str, tax_id: str = None
    ) -> dict:
        data = {"business_name": business_name, "business_type": business_type}
        if tax_id:
            data["tax_id"] = tax_id
        res = requests.post(f"{self.base_url}/business", json=data)
        return res.json()

    def update_business_account(self, **kwargs) -> dict:
        res = requests.put(f"{self.base_url}/business", json=kwargs)
        return res.json()

    def list_business_users(self) -> dict:
        res = requests.get(f"{self.base_url}/business/users")
        return res.json()

    def add_business_user(
        self, user_id: int, role: str = "buyer", spending_limit: float = None
    ) -> dict:
        data = {"user_id": user_id, "role": role}
        if spending_limit is not None:
            data["spending_limit"] = spending_limit
        res = requests.post(f"{self.base_url}/business/users", json=data)
        return res.json()

    def update_business_user(self, member_id: int, **kwargs) -> dict:
        res = requests.put(f"{self.base_url}/business/users/{member_id}", json=kwargs)
        return res.json()

    def remove_business_user(self, member_id: int) -> dict:
        res = requests.delete(f"{self.base_url}/business/users/{member_id}")
        return res.json()


if __name__ == "__main__":
    api = CaveatShopAPI()

    print("=== CAVEAT-Shop API Client ===\n")

    print("1. Get user:")
    print(api.get_user())

    print("\n2. List products (first 5):")
    result = api.list_products(limit=5)
    print(f"Found {result.get('total', 0)} products")
    for p in result.get("products", [])[:3]:
        print(f"  - {p['title'][:60]}... ${p['price']}")

    print("\n3. Get cart:")
    print(api.get_cart())

    print("\n4. List orders:")
    result = api.list_orders(limit=3)
    print(f"Found {result.get('total', 0)} orders")

    print("\n5. List wishlists:")
    print(api.list_wishlists())

    print("\n6. List departments:")
    print(api.list_departments())

    print("\n7. Search for 'laptop':")
    result = api.search("laptop", limit=3)
    print(f"Found {result.get('total', 0)} results")
