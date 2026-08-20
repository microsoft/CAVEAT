"""Database seed script for CAVEAT-Shop mockup application."""

import json
import hashlib
import random
import secrets
from datetime import datetime, timedelta, date
from pathlib import Path
from sqlmodel import Session

from backend.database import get_engine, init_db
from backend.models import (
    User,
    UserSession,
    Address,
    PaymentMethod,
    Department,
    Category,
    Brand,
    Seller,
    Product,
    ProductImage,
    Cart,
    CartItem,
    Order,
    OrderItem,
    Review,
    Wishlist,
    WishlistItem,
    BrowsingHistory,
    SearchHistory,
    Deal,
    Coupon,
    Subscription,
    Notification,
    AlexaShoppingList,
    GiftCard,
    GiftCardTransaction,
    Message,
    ShoppingPreference,
)


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def seed_database():
    """Seed the database with sample data."""
    init_db()
    engine = get_engine()

    with Session(engine) as session:
        # Check if already seeded
        existing_user = session.get(User, 1)
        if existing_user:
            print("Database already seeded, skipping...")
            return

        # =====================================================================
        # Create Users
        # =====================================================================
        users_data = [
            {
                "id": 1,
                "email": "john.doe@example.com",
                "name": "John Doe",
                "phone": "+1-555-123-4567",
                "is_prime": True,
                "prime_since": datetime.utcnow() - timedelta(days=365),
            },
            {
                "id": 2,
                "email": "sarah.miller@example.com",
                "name": "Sarah Miller",
                "phone": "+1-555-234-5678",
                "is_prime": True,
                "prime_since": datetime.utcnow() - timedelta(days=180),
            },
            {
                "id": 3,
                "email": "mike.johnson@example.com",
                "name": "Mike Johnson",
                "phone": "+1-555-345-6789",
                "is_prime": False,
                "prime_since": None,
            },
            {
                "id": 4,
                "email": "emily.chen@example.com",
                "name": "Emily Chen",
                "phone": "+1-555-456-7890",
                "is_prime": True,
                "prime_since": datetime.utcnow() - timedelta(days=730),
            },
            {
                "id": 5,
                "email": "david.wilson@example.com",
                "name": "David Wilson",
                "phone": "+1-555-567-8901",
                "is_prime": False,
                "prime_since": None,
            },
            {
                "id": 6,
                "email": "jessica.brown@example.com",
                "name": "Jessica Brown",
                "phone": "+1-555-678-9012",
                "is_prime": True,
                "prime_since": datetime.utcnow() - timedelta(days=90),
            },
            {
                "id": 7,
                "email": "chris.martinez@example.com",
                "name": "Chris Martinez",
                "phone": "+1-555-789-0123",
                "is_prime": False,
                "prime_since": None,
            },
            {
                "id": 8,
                "email": "amanda.taylor@example.com",
                "name": "Amanda Taylor",
                "phone": "+1-555-890-1234",
                "is_prime": True,
                "prime_since": datetime.utcnow() - timedelta(days=450),
            },
            {
                "id": 9,
                "email": "ryan.anderson@example.com",
                "name": "Ryan Anderson",
                "phone": "+1-555-901-2345",
                "is_prime": False,
                "prime_since": None,
            },
            {
                "id": 10,
                "email": "lisa.thomas@example.com",
                "name": "Lisa Thomas",
                "phone": "+1-555-012-3456",
                "is_prime": True,
                "prime_since": datetime.utcnow() - timedelta(days=60),
            },
        ]
        for user_data in users_data:
            session.add(User(password_hash=hash_password("password123"), **user_data))
        session.commit()

        # Create a session token for the user
        user_session = UserSession(
            user_id=1,
            session_token="test_session_token_12345",
            device_type="desktop",
            device_name="Chrome on macOS",
            browser="Chrome",
            os="macOS",
            ip_address="127.0.0.1",
            location="San Francisco, CA",
            is_current=True,
            expires_at=datetime.utcnow() + timedelta(days=30),
        )
        session.add(user_session)

        # =====================================================================
        # Create Addresses
        # =====================================================================
        addresses_data = [
            {
                "user_id": 1,
                "full_name": "John Doe",
                "phone": "+1-555-123-4567",
                "address_line1": "123 Main Street",
                "address_line2": "Apt 4B",
                "city": "San Francisco",
                "state": "CA",
                "zip_code": "94102",
                "country": "United States",
                "is_default": True,
                "address_type": "residential",
            },
            {
                "user_id": 1,
                "full_name": "John Doe",
                "phone": "+1-555-123-4567",
                "address_line1": "456 Market Street",
                "city": "San Francisco",
                "state": "CA",
                "zip_code": "94103",
                "country": "United States",
                "is_default": False,
                "address_type": "commercial",
            },
        ]
        for addr_data in addresses_data:
            session.add(Address(**addr_data))
        session.commit()

        # =====================================================================
        # Create Payment Methods
        # =====================================================================
        payments_data = [
            {
                "user_id": 1,
                "type": "credit_card",
                "card_number_last4": "4242",
                "card_brand": "visa",
                "expiry_month": 12,
                "expiry_year": 2027,
                "cardholder_name": "John Doe",
                "billing_address_id": 1,
                "is_default": True,
            },
            {
                "user_id": 1,
                "type": "credit_card",
                "card_number_last4": "1234",
                "card_brand": "mastercard",
                "expiry_month": 6,
                "expiry_year": 2026,
                "cardholder_name": "John Doe",
                "billing_address_id": 1,
                "is_default": False,
            },
        ]
        for pay_data in payments_data:
            session.add(PaymentMethod(**pay_data))
        session.commit()

        # =====================================================================
        # Create Departments
        # =====================================================================
        departments_path = Path(__file__).resolve().parent / "departments.json"
        with departments_path.open("r") as f:
            departments_data = json.load(f)

        # departments_data = [
        #     {"name": "Electronics", "slug": "electronics", "display_order": 1},
        #     {"name": "Computers", "slug": "computers", "display_order": 2},
        #     {"name": "Home & Kitchen", "slug": "home-kitchen", "display_order": 3},
        #     {"name": "Clothing", "slug": "clothing", "display_order": 4},
        #     {"name": "Books", "slug": "books", "display_order": 5},
        #     {
        #         "name": "Sports & Outdoors",
        #         "slug": "sports-outdoors",
        #         "display_order": 6,
        #     },
        #     {"name": "Beauty", "slug": "beauty", "display_order": 7},
        #     {"name": "Toys & Games", "slug": "toys-games", "display_order": 8},
        # ]
        for dept_data in departments_data:
            session.add(Department(**dept_data))
        session.commit()

        # =====================================================================
        # Create Categories
        # =====================================================================
        categories_data = [
            {"department_id": 1, "name": "Smartphones", "slug": "smartphones"},
            {"department_id": 1, "name": "Headphones", "slug": "headphones"},
            {"department_id": 1, "name": "TVs", "slug": "tvs"},
            {"department_id": 1, "name": "Cameras", "slug": "cameras"},
            {"department_id": 2, "name": "Laptops", "slug": "laptops"},
            {"department_id": 2, "name": "Monitors", "slug": "monitors"},
            {
                "department_id": 2,
                "name": "Computer Accessories",
                "slug": "computer-accessories",
            },
            {
                "department_id": 3,
                "name": "Kitchen Appliances",
                "slug": "kitchen-appliances",
            },
            {"department_id": 3, "name": "Furniture", "slug": "furniture"},
            {"department_id": 3, "name": "Bedding", "slug": "bedding"},
            {"department_id": 4, "name": "Men's Clothing", "slug": "mens-clothing"},
            {"department_id": 4, "name": "Women's Clothing", "slug": "womens-clothing"},
            {"department_id": 5, "name": "Fiction", "slug": "fiction"},
            {"department_id": 5, "name": "Non-Fiction", "slug": "non-fiction"},
            # Sports & Outdoors (department_id: 6)
            {
                "department_id": 6,
                "name": "Exercise & Fitness",
                "slug": "exercise-fitness",
            },
            {
                "department_id": 6,
                "name": "Outdoor Recreation",
                "slug": "outdoor-recreation",
            },
            {
                "department_id": 6,
                "name": "Sports Equipment",
                "slug": "sports-equipment",
            },
            # Beauty (department_id: 7)
            {"department_id": 7, "name": "Skincare", "slug": "skincare"},
            {"department_id": 7, "name": "Makeup", "slug": "makeup"},
            {"department_id": 7, "name": "Hair Care", "slug": "hair-care"},
            # Toys & Games (department_id: 8)
            {"department_id": 8, "name": "Board Games", "slug": "board-games"},
            {"department_id": 8, "name": "Action Figures", "slug": "action-figures"},
            {"department_id": 8, "name": "Building Toys", "slug": "building-toys"},
        ]
        for cat_data in categories_data:
            session.add(Category(**cat_data))
        session.commit()

        # =====================================================================
        # Create Brands
        # =====================================================================
        brands_data = [
            {"name": "Apple", "slug": "apple", "is_verified": True},
            {"name": "Samsung", "slug": "samsung", "is_verified": True},
            {"name": "Sony", "slug": "sony", "is_verified": True},
            {"name": "LG", "slug": "lg", "is_verified": True},
            {"name": "Dell", "slug": "dell", "is_verified": True},
            {"name": "HP", "slug": "hp", "is_verified": True},
            {"name": "Bose", "slug": "bose", "is_verified": True},
            {"name": "CAVEAT-Sport", "slug": "caveat_sport", "is_verified": True},
            {"name": "Instant Pot", "slug": "instant-pot", "is_verified": True},
            {"name": "CAVEAT-Shop Basics", "slug": "caveat_shopbasics", "is_verified": True},
            # Sports & Outdoors brands
            {"name": "Bowflex", "slug": "bowflex", "is_verified": True},
            {"name": "Coleman", "slug": "coleman", "is_verified": True},
            {"name": "Wilson", "slug": "wilson", "is_verified": True},
            # Beauty brands
            {"name": "CeraVe", "slug": "cerave", "is_verified": True},
            {"name": "Maybelline", "slug": "maybelline", "is_verified": True},
            {"name": "Olaplex", "slug": "olaplex", "is_verified": True},
            # Toys & Games brands
            {"name": "Hasbro", "slug": "hasbro", "is_verified": True},
            {"name": "LEGO", "slug": "lego", "is_verified": True},
            {"name": "Funko", "slug": "funko", "is_verified": True},
        ]
        for brand_data in brands_data:
            session.add(Brand(**brand_data))
        session.commit()

        # =====================================================================
        # Create Sellers
        # =====================================================================
        sellers_data = [
            {
                "name": "CAVEAT-Shop.com",
                "slug": "caveat_shop",
                "rating": 4.9,
                "rating_count": 1000000,
                "is_caveat_shop": True,
                "feedback_percentage": 99.9,
            },
            {
                "name": "TechStore Pro",
                "slug": "techstore-pro",
                "rating": 4.7,
                "rating_count": 15000,
                "is_caveat_shop": False,
                "feedback_percentage": 98.5,
            },
            {
                "name": "HomeGoods Plus",
                "slug": "homegoods-plus",
                "rating": 4.5,
                "rating_count": 8500,
                "is_caveat_shop": False,
                "feedback_percentage": 97.2,
            },
        ]
        for seller_data in sellers_data:
            session.add(Seller(**seller_data))
        session.commit()

        # =====================================================================
        # Create Products
        # =====================================================================
        products_path = Path(__file__).resolve().parent / "products.json"
        with products_path.open("r") as f:
            products_data = json.load(f)

        for product_data in products_data:
            product_data["images"] = json.dumps(product_data.get("images", []))

            # Convert bullet_points list to JSON string for database storage
            if "bullet_points" in product_data and isinstance(
                product_data["bullet_points"], list
            ):
                product_data["bullet_points"] = json.dumps(
                    product_data["bullet_points"]
                )
            session.add(Product(**product_data))
        session.commit()

        # =====================================================================
        # Create Product Images
        # =====================================================================
        for i, product in enumerate(products_data):
            images = json.loads(product.get("images", []))
            session.add(
                ProductImage(
                    product_id=i + 1,
                    url=images[0] if images else None,
                    alt_text=f"Product {i} main image",
                    is_primary=True,
                    display_order=0,
                )
            )
            session.add(
                ProductImage(
                    product_id=i + 1,
                    url=images[0] if images else None,
                    alt_text=f"Product {i} alternate view",
                    is_primary=False,
                    display_order=1,
                )
            )
        session.commit()

        # =====================================================================
        # Create Cart and Cart Items
        # =====================================================================
        cart = Cart(user_id=1)
        session.add(cart)
        session.commit()

        cart_items_data = [
            {"cart_id": 1, "product_id": 2, "quantity": 1},
            {"cart_id": 1, "product_id": 7, "quantity": 2},
        ]
        for item_data in cart_items_data:
            session.add(CartItem(**item_data))
        session.commit()

        # =====================================================================
        # Create Orders
        # =====================================================================
        orders_data = [
            {
                "order_number": "112-3456789-0123456",
                "user_id": 1,
                "shipping_address_id": 1,
                "billing_address_id": 1,
                "payment_method_id": 1,
                "subtotal": 999.00,
                "shipping_cost": 0.00,
                "tax": 82.42,
                "total": 1081.42,
                "status": "delivered",
                "placed_at": datetime.utcnow() - timedelta(days=30),
                "shipped_at": datetime.utcnow() - timedelta(days=28),
                "delivered_at": datetime.utcnow() - timedelta(days=26),
                "shipping_method": "priority",
            },
            {
                "order_number": "112-9876543-0987654",
                "user_id": 1,
                "shipping_address_id": 1,
                "billing_address_id": 1,
                "payment_method_id": 1,
                "subtotal": 79.95,
                "shipping_cost": 0.00,
                "tax": 6.60,
                "total": 86.55,
                "status": "shipped",
                "placed_at": datetime.utcnow() - timedelta(days=3),
                "shipped_at": datetime.utcnow() - timedelta(days=1),
                "shipping_method": "standard",
                "estimated_delivery_start": date.today() + timedelta(days=2),
                "estimated_delivery_end": date.today() + timedelta(days=4),
            },
            {
                "order_number": "112-5555555-1234567",
                "user_id": 1,
                "shipping_address_id": 1,
                "billing_address_id": 1,
                "payment_method_id": 1,
                "subtotal": 169.99,
                "shipping_cost": 0.00,
                "tax": 14.03,
                "total": 184.02,
                "status": "cancelled",
                "placed_at": datetime.utcnow() - timedelta(days=10),
                "cancelled_at": datetime.utcnow() - timedelta(days=9),
                "shipping_method": "standard",
            },
            # Order with return initiated
            {
                "order_number": "112-7777777-8888888",
                "user_id": 1,
                "shipping_address_id": 1,
                "billing_address_id": 1,
                "payment_method_id": 1,
                "subtotal": 449.98,
                "shipping_cost": 0.00,
                "tax": 37.12,
                "total": 487.10,
                "status": "delivered",
                "placed_at": datetime.utcnow() - timedelta(days=14),
                "shipped_at": datetime.utcnow() - timedelta(days=12),
                "delivered_at": datetime.utcnow() - timedelta(days=10),
                "shipping_method": "priority",
            },
            # Order with completed refund
            {
                "order_number": "112-2222222-3333333",
                "user_id": 1,
                "shipping_address_id": 1,
                "billing_address_id": 1,
                "payment_method_id": 1,
                "subtotal": 299.99,
                "shipping_cost": 0.00,
                "tax": 24.75,
                "total": 324.74,
                "status": "delivered",
                "placed_at": datetime.utcnow() - timedelta(days=45),
                "shipped_at": datetime.utcnow() - timedelta(days=43),
                "delivered_at": datetime.utcnow() - timedelta(days=40),
                "shipping_method": "standard",
            },
        ]
        for order_data in orders_data:
            session.add(Order(**order_data))
        session.commit()

        # Create Order Items
        order_items_data = [
            {
                "order_id": 1,
                "product_id": 1,
                "seller_id": 1,
                "quantity": 1,
                "unit_price": 999.00,
                "total_price": 999.00,
                "status": "delivered",
                "tracking_number": "1Z999AA10123456784",
                "carrier": "UPS",
                "is_returnable": True,
                "return_deadline": date.today() + timedelta(days=4),
            },
            {
                "order_id": 2,
                "product_id": 5,
                "seller_id": 1,
                "quantity": 1,
                "unit_price": 79.95,
                "total_price": 79.95,
                "status": "shipped",
                "tracking_number": "TBA123456789000",
                "carrier": "CAVEAT-Shop Logistics",
                "is_returnable": True,
            },
            {
                "order_id": 3,
                "product_id": 17,
                "seller_id": 1,
                "quantity": 1,
                "unit_price": 169.99,
                "total_price": 169.99,
                "status": "cancelled",
                "is_returnable": False,
            },
            # Order 4: Items with return in progress
            {
                "order_id": 4,
                "product_id": 2,  # Sony Headphones
                "seller_id": 1,
                "quantity": 1,
                "unit_price": 349.99,
                "total_price": 349.99,
                "status": "delivered",
                "tracking_number": "1Z999AA10123456790",
                "carrier": "UPS",
                "is_returnable": True,
                "return_deadline": date.today() + timedelta(days=20),
                "return_status": "approved",  # Return approved, waiting for item to be shipped
            },
            {
                "order_id": 4,
                "product_id": 7,  # CAVEAT-Shop Basics item
                "seller_id": 1,
                "quantity": 1,
                "unit_price": 99.99,
                "total_price": 99.99,
                "status": "delivered",
                "tracking_number": "1Z999AA10123456791",
                "carrier": "UPS",
                "is_returnable": True,
                "return_deadline": date.today() + timedelta(days=20),
                "return_status": None,  # Not returned, still returnable
            },
            # Order 5: Item with completed refund
            {
                "order_id": 5,
                "product_id": 6,  # Bose Speaker
                "seller_id": 1,
                "quantity": 1,
                "unit_price": 299.99,
                "total_price": 299.99,
                "status": "returned",
                "tracking_number": "1Z999AA10123456792",
                "carrier": "UPS",
                "is_returnable": False,
                "return_deadline": date.today() - timedelta(days=15),
                "return_status": "refunded",  # Return complete, refund issued
            },
        ]
        for item_data in order_items_data:
            session.add(OrderItem(**item_data))
        session.commit()

        # =====================================================================
        # Create Reviews (varied ratings, distributed across users)
        # Each user only reviews a product once
        # =====================================================================
        reviews_data = [
            # ---- Product 1: iPhone 14 Pro (5 reviews, avg ~4.6) ----
            {
                "product_id": 1,
                "user_id": 1,
                "rating": 5,
                "title": "Best phone I've ever owned",
                "body": "The iPhone 14 Pro is absolutely incredible. The camera system is stunning, Dynamic Island is innovative, and the battery life is great. Highly recommend!",
                "is_verified_purchase": True,
                "helpful_votes": 342,
                "total_votes": 365,
            },
            {
                "product_id": 1,
                "user_id": 2,
                "rating": 5,
                "title": "Amazing camera system",
                "body": "The 48MP main sensor captures incredible detail. Night mode is fantastic and the video quality is professional grade.",
                "is_verified_purchase": True,
                "helpful_votes": 156,
                "total_votes": 168,
            },
            {
                "product_id": 1,
                "user_id": 3,
                "rating": 4,
                "title": "Great but expensive",
                "body": "Love the phone but the price is steep. Dynamic Island is cool but feels gimmicky sometimes. Camera makes it worth it though.",
                "is_verified_purchase": True,
                "helpful_votes": 89,
                "total_votes": 112,
            },
            {
                "product_id": 1,
                "user_id": 4,
                "rating": 5,
                "title": "Smooth upgrade from 12 Pro",
                "body": "The always-on display and Dynamic Island are game changers. Battery life is noticeably better.",
                "is_verified_purchase": True,
                "helpful_votes": 67,
                "total_votes": 74,
            },
            {
                "product_id": 1,
                "user_id": 5,
                "rating": 4,
                "title": "Solid but not revolutionary",
                "body": "Good phone, good camera, good performance. But for the price, I expected more innovation.",
                "is_verified_purchase": False,
                "helpful_votes": 234,
                "total_votes": 298,
            },
            # ---- Product 2: Samsung Galaxy S23 Ultra (4 reviews, avg ~4.5) ----
            {
                "product_id": 2,
                "user_id": 3,
                "rating": 5,
                "title": "Android perfection",
                "body": "The S23 Ultra is the best Android phone I've used. The camera is phenomenal and the S Pen integration is seamless.",
                "is_verified_purchase": True,
                "helpful_votes": 189,
                "total_votes": 205,
            },
            {
                "product_id": 2,
                "user_id": 5,
                "rating": 5,
                "title": "200MP camera is insane",
                "body": "The detail you can capture is mind-blowing. Night photography is excellent. Best camera phone I've owned.",
                "is_verified_purchase": True,
                "helpful_votes": 145,
                "total_votes": 158,
            },
            {
                "product_id": 2,
                "user_id": 7,
                "rating": 4,
                "title": "Great but heavy",
                "body": "Amazing phone but it's quite heavy and large. The S Pen is useful for note-taking. Camera quality is excellent.",
                "is_verified_purchase": True,
                "helpful_votes": 78,
                "total_votes": 92,
            },
            {
                "product_id": 2,
                "user_id": 9,
                "rating": 4,
                "title": "Excellent for productivity",
                "body": "S Pen, DeX mode, and multitasking features make this perfect for work. Battery could be better though.",
                "is_verified_purchase": True,
                "helpful_votes": 56,
                "total_votes": 67,
            },
            # ---- Product 3: Google Pixel 7 Pro (3 reviews, avg ~4.3) ----
            {
                "product_id": 3,
                "user_id": 2,
                "rating": 5,
                "title": "Pure Android bliss",
                "body": "Love the clean Android experience. Magic Eraser is actually useful. 7 years of updates is incredible value.",
                "is_verified_purchase": True,
                "helpful_votes": 123,
                "total_votes": 135,
            },
            {
                "product_id": 3,
                "user_id": 6,
                "rating": 4,
                "title": "Great camera, some bugs",
                "body": "Camera quality rivals iPhone and Samsung. However, I've experienced some software bugs that Google needs to fix.",
                "is_verified_purchase": True,
                "helpful_votes": 89,
                "total_votes": 105,
            },
            {
                "product_id": 3,
                "user_id": 8,
                "rating": 4,
                "title": "Best value flagship",
                "body": "For the price, you can't beat what Google offers. The AI features are genuinely useful.",
                "is_verified_purchase": True,
                "helpful_votes": 67,
                "total_votes": 78,
            },
            # ---- Product 4: Sony WH-1000XM5 (6 reviews, avg ~4.5) ----
            {
                "product_id": 4,
                "user_id": 1,
                "rating": 5,
                "title": "Best noise canceling ever!",
                "body": "These headphones are incredible. The noise cancellation is the best I've experienced. Perfect for flights.",
                "is_verified_purchase": True,
                "helpful_votes": 456,
                "total_votes": 478,
            },
            {
                "product_id": 4,
                "user_id": 2,
                "rating": 5,
                "title": "Worth every penny",
                "body": "Upgraded from XM4s and the improvement is noticeable. More comfortable, better ANC, clearer calls.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 250,
            },
            {
                "product_id": 4,
                "user_id": 4,
                "rating": 4,
                "title": "Great but don't fold",
                "body": "Sound quality and ANC are excellent. My only complaint is they don't fold like the XM4s.",
                "is_verified_purchase": True,
                "helpful_votes": 189,
                "total_votes": 215,
            },
            {
                "product_id": 4,
                "user_id": 6,
                "rating": 5,
                "title": "Office essential",
                "body": "Game changer for working from home. Blocks out everything and the mic quality for calls is excellent.",
                "is_verified_purchase": True,
                "helpful_votes": 156,
                "total_votes": 168,
            },
            {
                "product_id": 4,
                "user_id": 8,
                "rating": 4,
                "title": "Almost perfect",
                "body": "Sound is amazing, ANC is top-tier. Wish the multipoint connection was smoother.",
                "is_verified_purchase": True,
                "helpful_votes": 78,
                "total_votes": 95,
            },
            {
                "product_id": 4,
                "user_id": 10,
                "rating": 4,
                "title": "Great for travel",
                "body": "Used these on a 14-hour flight and they were amazing. Battery lasted the whole trip.",
                "is_verified_purchase": False,
                "helpful_votes": 67,
                "total_votes": 82,
            },
            # ---- Product 5: Bose QuietComfort Earbuds II (2 reviews, avg ~4.0) ----
            {
                "product_id": 5,
                "user_id": 1,
                "rating": 4,
                "title": "Excellent ANC for earbuds",
                "body": "The noise cancellation is impressive for earbuds. Sound quality is great. Battery life could be better.",
                "is_verified_purchase": True,
                "helpful_votes": 123,
                "total_votes": 145,
            },
            {
                "product_id": 5,
                "user_id": 4,
                "rating": 4,
                "title": "Good but pricey",
                "body": "Great earbuds with excellent ANC. The fit is comfortable. Hard to justify the price over competitors.",
                "is_verified_purchase": True,
                "helpful_votes": 89,
                "total_votes": 102,
            },
            # ---- Product 6: AirPods Pro 2nd Gen (8 reviews, avg ~4.6) ----
            {
                "product_id": 6,
                "user_id": 1,
                "rating": 5,
                "title": "Perfect for Apple users",
                "body": "Seamless integration with iPhone and Mac. Spatial audio is incredible for movies. ANC is much improved.",
                "is_verified_purchase": True,
                "helpful_votes": 567,
                "total_votes": 590,
            },
            {
                "product_id": 6,
                "user_id": 2,
                "rating": 5,
                "title": "The best got better",
                "body": "Upgraded from original AirPods Pro and the improvements are significant. Better ANC, better sound, better case.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 365,
            },
            {
                "product_id": 6,
                "user_id": 3,
                "rating": 5,
                "title": "My daily drivers",
                "body": "Use these every day for work calls, music, and podcasts. Battery life is great and they're so comfortable.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 250,
            },
            {
                "product_id": 6,
                "user_id": 4,
                "rating": 4,
                "title": "Great but not cheap",
                "body": "Excellent earbuds with top-tier ANC. Apple ecosystem integration is amazing. Expensive though.",
                "is_verified_purchase": True,
                "helpful_votes": 189,
                "total_votes": 220,
            },
            {
                "product_id": 6,
                "user_id": 5,
                "rating": 5,
                "title": "Finally got Adaptive Audio",
                "body": "The new Adaptive Audio feature is a game changer. Automatically adjusts ANC based on environment.",
                "is_verified_purchase": True,
                "helpful_votes": 156,
                "total_votes": 168,
            },
            {
                "product_id": 6,
                "user_id": 6,
                "rating": 4,
                "title": "Solid upgrade",
                "body": "Better than Gen 1 in every way. The H2 chip makes a real difference. Transparency mode is incredibly natural.",
                "is_verified_purchase": True,
                "helpful_votes": 123,
                "total_votes": 142,
            },
            {
                "product_id": 6,
                "user_id": 8,
                "rating": 5,
                "title": "Best wireless earbuds",
                "body": "After trying many earbuds, these are the best for Apple users. The touch controls and Find My integration are clutch.",
                "is_verified_purchase": True,
                "helpful_votes": 89,
                "total_votes": 95,
            },
            {
                "product_id": 6,
                "user_id": 10,
                "rating": 4,
                "title": "Great for workouts",
                "body": "Stay in place during runs, sweat resistant, and the case charges fast. Only wish they had more bass.",
                "is_verified_purchase": False,
                "helpful_votes": 67,
                "total_votes": 85,
            },
            # ---- Product 7: Samsung 65" OLED TV (2 reviews, avg ~4.5) ----
            {
                "product_id": 7,
                "user_id": 1,
                "rating": 5,
                "title": "Stunning picture quality",
                "body": "The OLED blacks are incredible. Perfect for movies in a dark room. Gaming features are excellent too.",
                "is_verified_purchase": True,
                "helpful_votes": 145,
                "total_votes": 158,
            },
            {
                "product_id": 7,
                "user_id": 5,
                "rating": 4,
                "title": "Great TV, pricey",
                "body": "Picture quality is amazing. The Tizen OS is decent. Wish it had more HDMI 2.1 ports.",
                "is_verified_purchase": True,
                "helpful_votes": 89,
                "total_votes": 105,
            },
            # ---- Product 8: LG C3 OLED (3 reviews, avg ~4.7) ----
            {
                "product_id": 8,
                "user_id": 3,
                "rating": 5,
                "title": "Best TV for the price",
                "body": "Amazing picture quality with perfect blacks. WebOS is intuitive and gaming features are top-notch.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 250,
            },
            {
                "product_id": 8,
                "user_id": 7,
                "rating": 5,
                "title": "Gaming perfection",
                "body": "4 HDMI 2.1 ports, 120Hz, VRR, low input lag. This is THE gaming TV. Picture quality is stunning too.",
                "is_verified_purchase": True,
                "helpful_votes": 178,
                "total_votes": 192,
            },
            {
                "product_id": 8,
                "user_id": 9,
                "rating": 4,
                "title": "Excellent but watch for burn-in",
                "body": "Outstanding picture quality. Just be careful with static content to avoid burn-in. Worth the investment.",
                "is_verified_purchase": True,
                "helpful_votes": 156,
                "total_votes": 180,
            },
            # ---- Product 9: Sony Alpha 7 IV (2 reviews, avg ~4.5) ----
            {
                "product_id": 9,
                "user_id": 2,
                "rating": 5,
                "title": "Professional quality camera",
                "body": "Upgraded from A7 III and the improvements are significant. Autofocus is incredibly fast. 4K video is gorgeous.",
                "is_verified_purchase": True,
                "helpful_votes": 145,
                "total_votes": 158,
            },
            {
                "product_id": 9,
                "user_id": 6,
                "rating": 4,
                "title": "Great hybrid shooter",
                "body": "Excellent for both photo and video. The new menu system is much better. Battery life improved too.",
                "is_verified_purchase": True,
                "helpful_votes": 89,
                "total_votes": 102,
            },
            # ---- Product 10: Canon EOS R6 Mark II (1 review) ----
            {
                "product_id": 10,
                "user_id": 4,
                "rating": 4,
                "title": "Great hybrid camera",
                "body": "Excellent for both photo and video. The autofocus is remarkable. Would love more resolution but 24MP is fine.",
                "is_verified_purchase": True,
                "helpful_votes": 78,
                "total_votes": 92,
            },
            # ---- Product 11: Dell XPS 15 (2 reviews, avg ~3.5) ----
            {
                "product_id": 11,
                "user_id": 5,
                "rating": 4,
                "title": "Beautiful display, solid performance",
                "body": "The OLED screen is stunning for content creation. Gets warm under load but performance is excellent.",
                "is_verified_purchase": True,
                "helpful_votes": 123,
                "total_votes": 145,
            },
            {
                "product_id": 11,
                "user_id": 9,
                "rating": 3,
                "title": "Great screen, QC issues",
                "body": "Display is gorgeous but had to exchange due to keyboard issues. Customer support was helpful though.",
                "is_verified_purchase": True,
                "helpful_votes": 89,
                "total_votes": 120,
            },
            # ---- Product 12: MacBook Air M3 (7 reviews, avg ~4.9) ----
            {
                "product_id": 12,
                "user_id": 1,
                "rating": 5,
                "title": "The perfect laptop",
                "body": "M3 chip is blazing fast, battery lasts all day, and it runs completely silent. The 15-inch screen is gorgeous.",
                "is_verified_purchase": True,
                "helpful_votes": 567,
                "total_votes": 590,
            },
            {
                "product_id": 12,
                "user_id": 2,
                "rating": 5,
                "title": "Best laptop I've ever owned",
                "body": "Coming from Windows, this is a revelation. Everything is fast, smooth, and the battery is incredible.",
                "is_verified_purchase": True,
                "helpful_votes": 456,
                "total_votes": 478,
            },
            {
                "product_id": 12,
                "user_id": 3,
                "rating": 5,
                "title": "Worth the upgrade from M1",
                "body": "The 15-inch screen is perfect for productivity. M3 handles everything I throw at it effortlessly.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 365,
            },
            {
                "product_id": 12,
                "user_id": 4,
                "rating": 5,
                "title": "Developer's dream machine",
                "body": "Compiles code fast, runs VMs smoothly, and the battery lasts through a full work day. Perfect.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 250,
            },
            {
                "product_id": 12,
                "user_id": 6,
                "rating": 5,
                "title": "Silent and powerful",
                "body": "No fan noise ever. It's like magic. The performance is incredible for such a thin laptop.",
                "is_verified_purchase": True,
                "helpful_votes": 189,
                "total_votes": 200,
            },
            {
                "product_id": 12,
                "user_id": 8,
                "rating": 5,
                "title": "Perfect for students",
                "body": "Light, powerful, great battery. Takes notes, runs apps, lasts all day on campus. Highly recommend.",
                "is_verified_purchase": True,
                "helpful_votes": 145,
                "total_votes": 158,
            },
            {
                "product_id": 12,
                "user_id": 10,
                "rating": 4,
                "title": "Almost perfect",
                "body": "Love everything about it except the limited ports. Get a USB-C hub and you're set.",
                "is_verified_purchase": True,
                "helpful_votes": 123,
                "total_votes": 150,
            },
            # ---- Product 13: HP 27" 4K Monitor (1 review) ----
            {
                "product_id": 13,
                "user_id": 3,
                "rating": 4,
                "title": "Great monitor for work",
                "body": "Colors are accurate and 4K resolution is crisp. USB-C connectivity is convenient. Stand could be sturdier.",
                "is_verified_purchase": True,
                "helpful_votes": 67,
                "total_votes": 78,
            },
            # ---- Product 14: Samsung Odyssey G7 (2 reviews, avg ~4.5) ----
            {
                "product_id": 14,
                "user_id": 7,
                "rating": 5,
                "title": "Gaming monitor beast",
                "body": "240Hz is butter smooth and the curve is immersive. G-Sync works flawlessly. Best gaming monitor I've owned.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 250,
            },
            {
                "product_id": 14,
                "user_id": 9,
                "rating": 4,
                "title": "Great for competitive gaming",
                "body": "Super fast response time and the refresh rate is amazing. The curve takes some getting used to.",
                "is_verified_purchase": True,
                "helpful_votes": 156,
                "total_votes": 178,
            },
            # ---- Product 15: CAVEAT-Shop Basics USB-C Cable (3 reviews, avg ~4.0) ----
            {
                "product_id": 15,
                "user_id": 1,
                "rating": 5,
                "title": "Best value cables",
                "body": "These cables are durable and work great for fast charging. Can't beat the price for a 2-pack.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 250,
            },
            {
                "product_id": 15,
                "user_id": 5,
                "rating": 4,
                "title": "Reliable cables",
                "body": "Good quality for the price. Fast charging works well. Haven't frayed after months of use.",
                "is_verified_purchase": True,
                "helpful_votes": 145,
                "total_votes": 165,
            },
            {
                "product_id": 15,
                "user_id": 8,
                "rating": 3,
                "title": "Decent but stiff",
                "body": "They work fine but the cables are quite stiff. Not the most flexible for travel.",
                "is_verified_purchase": True,
                "helpful_votes": 78,
                "total_votes": 112,
            },
            # ---- Product 16: Logitech MX Master 3S (4 reviews, avg ~4.8) ----
            {
                "product_id": 16,
                "user_id": 1,
                "rating": 5,
                "title": "Best mouse for productivity",
                "body": "The scroll wheel is incredible, gestures are useful, and it's comfortable for all-day use.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 365,
            },
            {
                "product_id": 16,
                "user_id": 3,
                "rating": 5,
                "title": "Quiet clicks are amazing",
                "body": "The silent clicks are perfect for office use. Multi-device switching is seamless. Love this mouse.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 250,
            },
            {
                "product_id": 16,
                "user_id": 6,
                "rating": 5,
                "title": "Worth the premium price",
                "body": "Upgraded from MX Master 2S. The improvements in tracking and the silent clicks are worth it.",
                "is_verified_purchase": True,
                "helpful_votes": 189,
                "total_votes": 200,
            },
            {
                "product_id": 16,
                "user_id": 9,
                "rating": 4,
                "title": "Great but big",
                "body": "Excellent ergonomics and features. Might be too large for smaller hands.",
                "is_verified_purchase": True,
                "helpful_votes": 123,
                "total_votes": 145,
            },
            # ---- Product 17: Instant Pot Duo (5 reviews, avg ~4.6) ----
            {
                "product_id": 17,
                "user_id": 2,
                "rating": 5,
                "title": "Game changer for meal prep",
                "body": "This Instant Pot has transformed how I cook. So versatile and easy to use. Perfect for busy families.",
                "is_verified_purchase": True,
                "helpful_votes": 456,
                "total_votes": 478,
            },
            {
                "product_id": 17,
                "user_id": 4,
                "rating": 5,
                "title": "Best kitchen investment",
                "body": "Makes perfect rice, beans, soups, and even cheesecake! Can't imagine my kitchen without it now.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 365,
            },
            {
                "product_id": 17,
                "user_id": 6,
                "rating": 4,
                "title": "Great but learning curve",
                "body": "Once you get the hang of it, it's amazing. Took a few tries to understand the pressure cooking times.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 280,
            },
            {
                "product_id": 17,
                "user_id": 8,
                "rating": 5,
                "title": "Perfect for batch cooking",
                "body": "Make a week's worth of food in one afternoon. The keep warm function is super useful.",
                "is_verified_purchase": True,
                "helpful_votes": 189,
                "total_votes": 200,
            },
            {
                "product_id": 17,
                "user_id": 10,
                "rating": 4,
                "title": "Solid pressure cooker",
                "body": "Does everything well. Wish it had an air fryer lid option. Still, excellent value.",
                "is_verified_purchase": True,
                "helpful_votes": 123,
                "total_votes": 145,
            },
            # ---- Product 18: Ninja Foodi (2 reviews, avg ~4.5) ----
            {
                "product_id": 18,
                "user_id": 1,
                "rating": 5,
                "title": "Pressure cook then air fry - genius!",
                "body": "Love that I can pressure cook and then crisp the top. Makes perfect wings and pot roast.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 250,
            },
            {
                "product_id": 18,
                "user_id": 7,
                "rating": 4,
                "title": "Versatile but large",
                "body": "Does everything well - pressure cook, air fry, bake. Just takes up a lot of counter space.",
                "is_verified_purchase": True,
                "helpful_votes": 156,
                "total_votes": 180,
            },
            # ---- Product 19: Keurig K-Elite (2 reviews, avg ~3.5) ----
            {
                "product_id": 19,
                "user_id": 3,
                "rating": 4,
                "title": "Convenient coffee maker",
                "body": "Quick and easy coffee every morning. Iced coffee setting is great for summer. Easy to clean.",
                "is_verified_purchase": True,
                "helpful_votes": 123,
                "total_votes": 145,
            },
            {
                "product_id": 19,
                "user_id": 5,
                "rating": 3,
                "title": "Convenient but wasteful",
                "body": "Makes decent coffee quickly. Feel guilty about the K-cup waste though. Get a reusable filter.",
                "is_verified_purchase": True,
                "helpful_votes": 189,
                "total_votes": 250,
            },
            # ---- Product 20: Zinus Mattress (4 reviews, avg ~4.0) ----
            {
                "product_id": 20,
                "user_id": 2,
                "rating": 5,
                "title": "Best budget mattress",
                "body": "Incredibly comfortable for the price. Took a few days to expand but now sleeps perfectly.",
                "is_verified_purchase": True,
                "helpful_votes": 456,
                "total_votes": 478,
            },
            {
                "product_id": 20,
                "user_id": 4,
                "rating": 4,
                "title": "Great for guest room",
                "body": "Comfortable and affordable. Perfect for our guest bedroom. Guests love it.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 265,
            },
            {
                "product_id": 20,
                "user_id": 7,
                "rating": 4,
                "title": "Good value",
                "body": "Comfortable mattress at a great price. Initial off-gassing but went away after a few days.",
                "is_verified_purchase": True,
                "helpful_votes": 189,
                "total_votes": 220,
            },
            {
                "product_id": 20,
                "user_id": 10,
                "rating": 3,
                "title": "Decent but soft",
                "body": "A bit softer than I expected. Good for side sleepers but might be too soft for back sleepers.",
                "is_verified_purchase": True,
                "helpful_votes": 145,
                "total_votes": 200,
            },
            # ---- Product 21: Walker Edison TV Stand (1 review) ----
            {
                "product_id": 21,
                "user_id": 6,
                "rating": 4,
                "title": "Looks great, easy assembly",
                "body": "Beautiful farmhouse style that matches our decor. Assembly took about an hour. Sturdy construction.",
                "is_verified_purchase": True,
                "helpful_votes": 89,
                "total_votes": 102,
            },
            # ---- Product 22: Bedsure Fleece Blanket (3 reviews, avg ~4.7) ----
            {
                "product_id": 22,
                "user_id": 2,
                "rating": 5,
                "title": "So soft and cozy!",
                "body": "This blanket is incredibly soft and warm. Washes well without pilling. Great value!",
                "is_verified_purchase": True,
                "helpful_votes": 567,
                "total_votes": 590,
            },
            {
                "product_id": 22,
                "user_id": 6,
                "rating": 5,
                "title": "Perfect for movie nights",
                "body": "Super soft and lightweight but still warm. The grey color matches everything.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 365,
            },
            {
                "product_id": 22,
                "user_id": 8,
                "rating": 4,
                "title": "Good quality blanket",
                "body": "Soft and cozy. Sheds a little at first but stopped after a few washes.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 280,
            },
            # ---- Product 23: Beckham Hotel Pillows (4 reviews, avg ~4.5) ----
            {
                "product_id": 23,
                "user_id": 1,
                "rating": 5,
                "title": "Hotel quality at home",
                "body": "These pillows are so fluffy and comfortable. Don't flatten like cheaper pillows. Best sleep ever!",
                "is_verified_purchase": True,
                "helpful_votes": 678,
                "total_votes": 700,
            },
            {
                "product_id": 23,
                "user_id": 4,
                "rating": 5,
                "title": "Finally good sleep",
                "body": "Replaced my old flat pillows with these and what a difference. Supportive yet soft.",
                "is_verified_purchase": True,
                "helpful_votes": 456,
                "total_votes": 478,
            },
            {
                "product_id": 23,
                "user_id": 7,
                "rating": 4,
                "title": "Great pillows",
                "body": "Very comfortable and well made. A bit too firm for stomach sleepers initially.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 280,
            },
            {
                "product_id": 23,
                "user_id": 10,
                "rating": 4,
                "title": "Good value",
                "body": "Nice quality pillows for the price. Held up well after months of use.",
                "is_verified_purchase": True,
                "helpful_votes": 189,
                "total_votes": 220,
            },
            # ---- Product 24: Hanes EcoSmart Sweatshirt (1 review) ----
            {
                "product_id": 24,
                "user_id": 5,
                "rating": 4,
                "title": "Comfortable everyday sweatshirt",
                "body": "Soft and comfortable for the price. Holds up well after multiple washes. Runs slightly large.",
                "is_verified_purchase": True,
                "helpful_votes": 145,
                "total_votes": 165,
            },
            # ---- Product 25: Levi's 505 Jeans (2 reviews, avg ~4.5) ----
            {
                "product_id": 25,
                "user_id": 3,
                "rating": 5,
                "title": "Classic fit that works",
                "body": "Can't go wrong with Levi's 505s. Comfortable fit, durable denim. Been buying these for years.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 365,
            },
            {
                "product_id": 25,
                "user_id": 9,
                "rating": 4,
                "title": "Reliable jeans",
                "body": "Good quality denim that lasts. Sizing is consistent which I appreciate.",
                "is_verified_purchase": True,
                "helpful_votes": 189,
                "total_votes": 220,
            },
            # ---- Product 26: CAVEAT-Shop Essentials T-Shirt (1 review) ----
            {
                "product_id": 26,
                "user_id": 7,
                "rating": 4,
                "title": "Good basic tees",
                "body": "Nice quality for the price. Comfortable cotton that breathes well. Perfect for layering.",
                "is_verified_purchase": True,
                "helpful_votes": 123,
                "total_votes": 145,
            },
            # ---- Product 27: Lululemon Align Leggings (3 reviews, avg ~4.7) ----
            {
                "product_id": 27,
                "user_id": 2,
                "rating": 5,
                "title": "Worth every penny",
                "body": "The Nulu fabric is like wearing butter. So comfortable for yoga and everyday wear.",
                "is_verified_purchase": True,
                "helpful_votes": 456,
                "total_votes": 478,
            },
            {
                "product_id": 27,
                "user_id": 6,
                "rating": 5,
                "title": "Best leggings ever",
                "body": "Once you try Aligns, you can't go back. So soft and flattering. Worth the splurge.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 365,
            },
            {
                "product_id": 27,
                "user_id": 8,
                "rating": 4,
                "title": "Amazing but delicate",
                "body": "Incredibly comfortable but the fabric is delicate. Don't wear for anything abrasive.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 280,
            },
            # ---- Product 28: Atomic Habits (6 reviews, avg ~4.8) ----
            {
                "product_id": 28,
                "user_id": 1,
                "rating": 5,
                "title": "Life-changing book",
                "body": "This book completely changed how I think about habits and goals. Practical, actionable advice.",
                "is_verified_purchase": True,
                "helpful_votes": 892,
                "total_votes": 915,
            },
            {
                "product_id": 28,
                "user_id": 2,
                "rating": 5,
                "title": "Must read for everyone",
                "body": "Simple concepts that are easy to implement. Already seeing results after a month.",
                "is_verified_purchase": True,
                "helpful_votes": 678,
                "total_votes": 700,
            },
            {
                "product_id": 28,
                "user_id": 4,
                "rating": 5,
                "title": "Best self-help book",
                "body": "Unlike other self-help books, this one actually gives you a system. Not just motivation.",
                "is_verified_purchase": True,
                "helpful_votes": 567,
                "total_votes": 590,
            },
            {
                "product_id": 28,
                "user_id": 6,
                "rating": 5,
                "title": "Practical and actionable",
                "body": "Every chapter has takeaways you can implement immediately. Highly recommend.",
                "is_verified_purchase": True,
                "helpful_votes": 456,
                "total_votes": 478,
            },
            {
                "product_id": 28,
                "user_id": 8,
                "rating": 4,
                "title": "Good concepts, some repetition",
                "body": "Great ideas but could be more concise. Still worth reading for the core concepts.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 320,
            },
            {
                "product_id": 28,
                "user_id": 10,
                "rating": 5,
                "title": "Changed my morning routine",
                "body": "Applied the habit stacking concept and now I actually exercise every morning. Works!",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 365,
            },
            # ---- Product 29: The Subtle Art (2 reviews, avg ~4.0) ----
            {
                "product_id": 29,
                "user_id": 3,
                "rating": 4,
                "title": "Refreshingly honest",
                "body": "A different take on self-help that resonated with me. Language may not be for everyone.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 400,
            },
            {
                "product_id": 29,
                "user_id": 7,
                "rating": 4,
                "title": "Interesting perspective",
                "body": "Makes you think differently about what matters. Some parts felt repetitive.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 290,
            },
            # ---- Product 30: Fourth Wing (3 reviews, avg ~4.7) ----
            {
                "product_id": 30,
                "user_id": 2,
                "rating": 5,
                "title": "Could not put it down!",
                "body": "Dragons, romance, action - this book has it all! Stayed up way too late finishing it.",
                "is_verified_purchase": True,
                "helpful_votes": 567,
                "total_votes": 590,
            },
            {
                "product_id": 30,
                "user_id": 6,
                "rating": 5,
                "title": "Best fantasy in years",
                "body": "The world-building is incredible. Characters you actually care about. Need the sequel NOW.",
                "is_verified_purchase": True,
                "helpful_votes": 456,
                "total_votes": 478,
            },
            {
                "product_id": 30,
                "user_id": 10,
                "rating": 4,
                "title": "Great but predictable",
                "body": "Enjoyed the story and romance. Some plot points were predictable but still a fun read.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 300,
            },
            # ---- Product 31: Where the Crawdads Sing (3 reviews, avg ~4.7) ----
            {
                "product_id": 31,
                "user_id": 1,
                "rating": 5,
                "title": "Beautifully written",
                "body": "The prose is gorgeous and the story is captivating. The marsh setting comes alive.",
                "is_verified_purchase": True,
                "helpful_votes": 789,
                "total_votes": 820,
            },
            {
                "product_id": 31,
                "user_id": 4,
                "rating": 5,
                "title": "Emotional and gripping",
                "body": "Couldn't stop thinking about Kya. Beautiful story about resilience and nature.",
                "is_verified_purchase": True,
                "helpful_votes": 567,
                "total_votes": 590,
            },
            {
                "product_id": 31,
                "user_id": 8,
                "rating": 4,
                "title": "Slow start but worth it",
                "body": "Takes a while to get going but the payoff is worth it. The ending was unexpected.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 420,
            },
            # ---- Product 32: Bowflex SelectTech Dumbbells (3 reviews, avg ~4.7) ----
            {
                "product_id": 32,
                "user_id": 3,
                "rating": 5,
                "title": "Space-saving home gym essential",
                "body": "Replaces so many dumbbells! The dial system is easy to use. Solid build quality.",
                "is_verified_purchase": True,
                "helpful_votes": 456,
                "total_votes": 478,
            },
            {
                "product_id": 32,
                "user_id": 5,
                "rating": 5,
                "title": "Worth the investment",
                "body": "Expensive but worth it. Takes up so much less space than traditional dumbbells.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 365,
            },
            {
                "product_id": 32,
                "user_id": 9,
                "rating": 4,
                "title": "Great but handle is wide",
                "body": "Love the adjustability. The handle is a bit wider than traditional dumbbells though.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 280,
            },
            # ---- Product 33: Fit Simplify Resistance Bands (2 reviews, avg ~4.0) ----
            {
                "product_id": 33,
                "user_id": 2,
                "rating": 4,
                "title": "Great for home workouts",
                "body": "Good variety of resistance levels. Durable after 6 months of use. Carry bag is convenient.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 380,
            },
            {
                "product_id": 33,
                "user_id": 7,
                "rating": 4,
                "title": "Good starter set",
                "body": "Perfect for beginners. The different resistances help you progress. Good value.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 280,
            },
            # ---- Product 34: Coleman Sundome Tent (2 reviews, avg ~4.0) ----
            {
                "product_id": 34,
                "user_id": 1,
                "rating": 4,
                "title": "Reliable camping tent",
                "body": "Easy to set up, kept us dry in light rain. Good ventilation. Perfect for casual camping.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 380,
            },
            {
                "product_id": 34,
                "user_id": 5,
                "rating": 4,
                "title": "Good budget tent",
                "body": "Not the most rugged but perfect for occasional camping. Setup is straightforward.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 280,
            },
            # ---- Product 35: YETI Rambler Bottle (2 reviews, avg ~4.5) ----
            {
                "product_id": 35,
                "user_id": 3,
                "rating": 5,
                "title": "Keeps drinks cold all day",
                "body": "Ice stays frozen for 24+ hours. Durable construction that survives drops. Worth the price.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 365,
            },
            {
                "product_id": 35,
                "user_id": 9,
                "rating": 4,
                "title": "Premium quality",
                "body": "Excellent insulation and durability. Pricey but you get what you pay for.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 280,
            },
            # ---- Product 36: Wilson NBA Basketball (1 review) ----
            {
                "product_id": 36,
                "user_id": 7,
                "rating": 5,
                "title": "Great feel and grip",
                "body": "Nice grip and bounce. Holds up well on outdoor courts. Feels like a quality ball.",
                "is_verified_purchase": True,
                "helpful_votes": 123,
                "total_votes": 135,
            },
            # ---- Product 37: Franklin Football (2 reviews, avg ~3.5) ----
            {
                "product_id": 37,
                "user_id": 5,
                "rating": 4,
                "title": "Good backyard football",
                "body": "Great for casual play in the backyard. Good grip and spirals well. Affordable option.",
                "is_verified_purchase": True,
                "helpful_votes": 89,
                "total_votes": 102,
            },
            {
                "product_id": 37,
                "user_id": 9,
                "rating": 3,
                "title": "Decent for the price",
                "body": "Fine for kids playing in the yard. Not for serious use. Grip wore off quickly.",
                "is_verified_purchase": True,
                "helpful_votes": 67,
                "total_votes": 95,
            },
            # ---- Product 38: CeraVe Moisturizing Cream (5 reviews, avg ~4.8) ----
            {
                "product_id": 38,
                "user_id": 2,
                "rating": 5,
                "title": "Holy grail moisturizer",
                "body": "My dermatologist recommended this and it transformed my dry skin. Non-greasy, absorbs quickly.",
                "is_verified_purchase": True,
                "helpful_votes": 892,
                "total_votes": 915,
            },
            {
                "product_id": 38,
                "user_id": 4,
                "rating": 5,
                "title": "Saved my winter skin",
                "body": "Finally found a moisturizer that actually works for my dry, sensitive skin. No breakouts!",
                "is_verified_purchase": True,
                "helpful_votes": 678,
                "total_votes": 700,
            },
            {
                "product_id": 38,
                "user_id": 6,
                "rating": 5,
                "title": "Best drugstore moisturizer",
                "body": "Better than many expensive brands. The ceramides really make a difference.",
                "is_verified_purchase": True,
                "helpful_votes": 567,
                "total_votes": 590,
            },
            {
                "product_id": 38,
                "user_id": 8,
                "rating": 4,
                "title": "Great but thick",
                "body": "Very moisturizing but quite thick. Use a small amount. Works great for body too.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 420,
            },
            {
                "product_id": 38,
                "user_id": 10,
                "rating": 5,
                "title": "Derm recommended",
                "body": "Use this for my eczema-prone skin. Gentle, effective, and affordable. Can't ask for more.",
                "is_verified_purchase": True,
                "helpful_votes": 456,
                "total_votes": 478,
            },
            # ---- Product 39: The Ordinary Niacinamide (2 reviews, avg ~4.0) ----
            {
                "product_id": 39,
                "user_id": 2,
                "rating": 4,
                "title": "Helped with my pores",
                "body": "Noticed a difference in my pores after a few weeks. A little goes a long way.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 400,
            },
            {
                "product_id": 39,
                "user_id": 6,
                "rating": 4,
                "title": "Good for oily skin",
                "body": "Helps control sebum production. Takes time to see results but worth it.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 290,
            },
            # ---- Product 40: Maybelline Sky High Mascara (2 reviews, avg ~4.0) ----
            {
                "product_id": 40,
                "user_id": 2,
                "rating": 4,
                "title": "Great drugstore mascara",
                "body": "Good length and volume without clumping. Stays on all day. Easy to remove.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 400,
            },
            {
                "product_id": 40,
                "user_id": 8,
                "rating": 4,
                "title": "Good for everyday",
                "body": "Not the most dramatic but perfect for daily wear. Doesn't flake.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 290,
            },
            # ---- Product 41: NYX Epic Ink Liner (2 reviews, avg ~4.5) ----
            {
                "product_id": 41,
                "user_id": 4,
                "rating": 5,
                "title": "Best drugstore liner!",
                "body": "Super precise tip for sharp wings. Stays on all day without smudging. Better than high-end!",
                "is_verified_purchase": True,
                "helpful_votes": 567,
                "total_votes": 590,
            },
            {
                "product_id": 41,
                "user_id": 6,
                "rating": 4,
                "title": "Great for cat eyes",
                "body": "Perfect tip for winged liner. Pigmented and long-lasting. Love it.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 400,
            },
            # ---- Product 42: Olaplex No. 3 (3 reviews, avg ~4.7) ----
            {
                "product_id": 42,
                "user_id": 2,
                "rating": 5,
                "title": "Saved my damaged hair",
                "body": "My bleached hair was so damaged and this brought it back to life. Use it weekly.",
                "is_verified_purchase": True,
                "helpful_votes": 678,
                "total_votes": 700,
            },
            {
                "product_id": 42,
                "user_id": 6,
                "rating": 5,
                "title": "Actually works",
                "body": "Skeptical at first but this really repairs damaged hair. Worth every penny.",
                "is_verified_purchase": True,
                "helpful_votes": 567,
                "total_votes": 590,
            },
            {
                "product_id": 42,
                "user_id": 10,
                "rating": 4,
                "title": "Good but pricey",
                "body": "Definitely helps with hair damage. Wish it wasn't so expensive for the size.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 420,
            },
            # ---- Product 43: Moroccanoil Treatment (2 reviews, avg ~4.5) ----
            {
                "product_id": 43,
                "user_id": 4,
                "rating": 5,
                "title": "Holy grail hair product",
                "body": "Makes my hair shiny and smooth without being greasy. The scent is amazing.",
                "is_verified_purchase": True,
                "helpful_votes": 456,
                "total_votes": 478,
            },
            {
                "product_id": 43,
                "user_id": 8,
                "rating": 4,
                "title": "Great finishing oil",
                "body": "Perfect for taming frizz and adding shine. A little goes a long way.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 400,
            },
            # ---- Product 44: Monopoly Classic (2 reviews, avg ~4.0) ----
            {
                "product_id": 44,
                "user_id": 1,
                "rating": 5,
                "title": "Classic family fun",
                "body": "Can't go wrong with Monopoly! Great for family game nights. Brings back memories.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 250,
            },
            {
                "product_id": 44,
                "user_id": 7,
                "rating": 3,
                "title": "Classic but long",
                "body": "Fun but games take forever. Make sure you have a few hours. Quality is good.",
                "is_verified_purchase": True,
                "helpful_votes": 156,
                "total_votes": 220,
            },
            # ---- Product 45: Exploding Kittens (3 reviews, avg ~4.7) ----
            {
                "product_id": 45,
                "user_id": 3,
                "rating": 5,
                "title": "Hilarious and fun!",
                "body": "Quick to learn and so funny. Great for parties. Everyone ends up laughing.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 365,
            },
            {
                "product_id": 45,
                "user_id": 5,
                "rating": 5,
                "title": "Party favorite",
                "body": "Bring this to every game night. Easy rules but strategic. Always a hit.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 250,
            },
            {
                "product_id": 45,
                "user_id": 9,
                "rating": 4,
                "title": "Fun but gets repetitive",
                "body": "Great game but can feel samey after many plays. Still worth having.",
                "is_verified_purchase": True,
                "helpful_votes": 156,
                "total_votes": 200,
            },
            # ---- Product 46: LEGO Star Wars Millennium Falcon (4 reviews, avg ~4.8) ----
            {
                "product_id": 46,
                "user_id": 1,
                "rating": 5,
                "title": "Incredible build!",
                "body": "Took about 8 hours to build and loved every minute. Detail is amazing. Looks fantastic.",
                "is_verified_purchase": True,
                "helpful_votes": 567,
                "total_votes": 590,
            },
            {
                "product_id": 46,
                "user_id": 3,
                "rating": 5,
                "title": "Perfect for Star Wars fans",
                "body": "Every Star Wars fan needs this. The minifigures are great and the build is satisfying.",
                "is_verified_purchase": True,
                "helpful_votes": 456,
                "total_votes": 478,
            },
            {
                "product_id": 46,
                "user_id": 7,
                "rating": 5,
                "title": "Display worthy",
                "body": "Built this with my kids and it's now proudly displayed. So much detail.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 365,
            },
            {
                "product_id": 46,
                "user_id": 9,
                "rating": 4,
                "title": "Great but fragile",
                "body": "Amazing build and detail. Just be careful once built - some pieces pop off easily.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 300,
            },
            # ---- Product 47: LEGO Technic Bugatti (2 reviews, avg ~4.5) ----
            {
                "product_id": 47,
                "user_id": 5,
                "rating": 5,
                "title": "Engineering marvel",
                "body": "The working gearbox and steering are impressive. Beautiful display piece. Rewarding build.",
                "is_verified_purchase": True,
                "helpful_votes": 345,
                "total_votes": 365,
            },
            {
                "product_id": 47,
                "user_id": 10,
                "rating": 4,
                "title": "Challenging and fun",
                "body": "Complex build that's satisfying to complete. Some parts are fiddly but worth it.",
                "is_verified_purchase": True,
                "helpful_votes": 234,
                "total_votes": 280,
            },
        ]
        for review_data in reviews_data:
            session.add(Review(**review_data))
        session.commit()

        # =====================================================================
        # Create Wishlists
        # =====================================================================
        wishlist = Wishlist(
            user_id=1,
            name="My Wish List",
            is_default=True,
            is_public=False,
        )
        session.add(wishlist)
        session.commit()

        wishlist_items_data = [
            {
                "wishlist_id": 1,
                "product_id": 3,
                "priority": "high",
                "price_when_added": 1799.99,
            },
            {
                "wishlist_id": 1,
                "product_id": 4,
                "priority": "medium",
                "price_when_added": 1499.99,
            },
            {
                "wishlist_id": 1,
                "product_id": 6,
                "priority": "low",
                "price_when_added": 279.00,
            },
        ]
        for item_data in wishlist_items_data:
            session.add(WishlistItem(**item_data))
        session.commit()

        # =====================================================================
        # Create Browsing History
        # =====================================================================
        for i, product_id in enumerate([1, 2, 3, 5, 7, 8]):
            session.add(
                BrowsingHistory(
                    user_id=1,
                    product_id=product_id,
                    viewed_at=datetime.utcnow() - timedelta(hours=i * 2),
                )
            )
        session.commit()

        # =====================================================================
        # Create Deals
        # =====================================================================
        deals_data = [
            {
                "product_id": 2,
                "deal_type": "lightning",
                "discount_percentage": 13,
                "deal_price": 348.00,
                "original_price": 399.99,
                "start_time": datetime.utcnow() - timedelta(hours=2),
                "end_time": datetime.utcnow() + timedelta(hours=4),
                "claimed_percentage": 65,
                "is_prime_exclusive": False,
            },
            {
                "product_id": 5,
                "deal_type": "deal_of_day",
                "discount_percentage": 20,
                "deal_price": 79.95,
                "original_price": 99.99,
                "start_time": datetime.utcnow().replace(hour=0, minute=0),
                "end_time": datetime.utcnow().replace(hour=23, minute=59),
                "is_prime_exclusive": False,
            },
        ]
        for deal_data in deals_data:
            session.add(Deal(**deal_data))
        session.commit()

        # =====================================================================
        # Create Coupons
        # =====================================================================
        coupons_data = [
            {
                "code": "SAVE10",
                "description": "Save $10 on orders over $50",
                "discount_type": "fixed",
                "discount_value": 10.00,
                "min_order_amount": 50.00,
                "valid_from": datetime.utcnow() - timedelta(days=7),
                "valid_until": datetime.utcnow() + timedelta(days=30),
            },
            {
                "code": "PRIME15",
                "description": "15% off for Prime members",
                "discount_type": "percentage",
                "discount_value": 15.00,
                "max_discount": 50.00,
                "valid_from": datetime.utcnow(),
                "valid_until": datetime.utcnow() + timedelta(days=14),
            },
        ]
        for coupon_data in coupons_data:
            session.add(Coupon(**coupon_data))
        session.commit()

        # =====================================================================
        # Create Subscriptions (Subscribe & Save)
        # =====================================================================
        subscription = Subscription(
            user_id=1,
            product_id=7,
            quantity=2,
            frequency_months=2,
            discount_percentage=10,
            next_delivery_date=date.today() + timedelta(days=45),
            shipping_address_id=1,
            payment_method_id=1,
            status="active",
        )
        session.add(subscription)
        session.commit()

        # =====================================================================
        # Create Notifications
        # =====================================================================
        notifications_data = [
            {
                "user_id": 1,
                "type": "delivery",
                "title": "Your package is arriving today",
                "message": "Order #112-9876543-0987654 is out for delivery.",
                "link": "/gp/your-account/order-details/2",
                "is_read": False,
            },
            {
                "user_id": 1,
                "type": "deal_alert",
                "title": "Deal on item in your list",
                "message": 'Samsung 65" OLED 4K Smart TV is now $400 off!',
                "link": "/dp/B0BDJMKHF3",
                "is_read": True,
            },
            {
                "user_id": 1,
                "type": "return",
                "title": "Return label ready",
                "message": "Your return shipping label for order #112-7777777-8888888 is ready. Print it and ship within 7 days.",
                "link": "/gp/your-account/order-details/4",
                "is_read": False,
            },
            {
                "user_id": 1,
                "type": "refund",
                "title": "Refund processed",
                "message": "Your refund of $299.99 for order #112-2222222-3333333 has been processed.",
                "link": "/gp/your-account/order-details/5",
                "is_read": True,
            },
        ]
        for notif_data in notifications_data:
            session.add(Notification(**notif_data))
        session.commit()

        # =====================================================================
        # Create Shopping Preferences
        # =====================================================================
        prefs = ShoppingPreference(
            user_id=1,
            language="en_US",
            currency="USD",
            country="US",
            personalized_ads=True,
            browsing_history_enabled=True,
            recommendations_enabled=True,
        )
        session.add(prefs)
        session.commit()

        # =====================================================================
        # Create Gift Cards
        # =====================================================================
        gift_card = GiftCard(
            user_id=1,
            code="AMZN-GIFT-1234-5678",
            original_amount=50.00,
            current_balance=35.50,
            redeemed_at=datetime.utcnow() - timedelta(days=14),
        )
        session.add(gift_card)
        session.commit()

        gift_transaction = GiftCardTransaction(
            user_id=1,
            amount=-14.50,
            type="purchase",
            order_id=2,
            balance_after=35.50,
        )
        session.add(gift_transaction)
        session.commit()

        # =====================================================================
        # Create Alexa Shopping List
        # =====================================================================
        alexa_items_data = [
            {
                "user_id": 1,
                "item_name": "Milk",
                "quantity": 1,
                "is_completed": False,
                "added_via": "alexa",
            },
            {
                "user_id": 1,
                "item_name": "Bread",
                "quantity": 2,
                "is_completed": True,
                "added_via": "alexa",
            },
            {
                "user_id": 1,
                "item_name": "Coffee",
                "quantity": 1,
                "is_completed": False,
                "added_via": "web",
            },
        ]
        for item_data in alexa_items_data:
            session.add(AlexaShoppingList(**item_data))
        session.commit()

        # =====================================================================
        # Create Messages
        # =====================================================================
        messages_data = [
            {
                "user_id": 1,
                "sender_type": "caveat_shop",
                "subject": "Your order has shipped!",
                "body": "Good news! Your order #112-9876543-0987654 has shipped and is on its way.",
                "related_order_id": 2,
                "is_read": True,
            },
            {
                "user_id": 1,
                "sender_type": "seller",
                "sender_id": 2,
                "subject": "Thank you for your purchase",
                "body": "Thank you for purchasing from TechStore Pro! We hope you enjoy your new laptop.",
                "is_read": False,
            },
            {
                "user_id": 1,
                "sender_type": "caveat_shop",
                "subject": "Return request approved",
                "body": "Your return request for Sony WH-1000XM5 Wireless Headphones from order #112-7777777-8888888 has been approved. Please ship the item back within 7 days using the prepaid shipping label we've provided. Once we receive and inspect the item, your refund will be processed within 3-5 business days.",
                "related_order_id": 4,
                "is_read": False,
            },
            {
                "user_id": 1,
                "sender_type": "caveat_shop",
                "subject": "Your refund has been processed",
                "body": "We've processed your refund of $299.99 for the Bose SoundLink Revolve+ Speaker from order #112-2222222-3333333. The refund should appear on your original payment method within 5-7 business days.",
                "related_order_id": 5,
                "is_read": True,
            },
        ]
        for msg_data in messages_data:
            session.add(Message(**msg_data))
        session.commit()

        # =====================================================================
        # Create Search History
        # =====================================================================
        searches = [
            "iphone 14 pro",
            "noise canceling headphones",
            "instant pot",
            "laptop",
        ]
        for i, query in enumerate(searches):
            session.add(
                SearchHistory(
                    user_id=1,
                    query=query,
                    results_count=random.randint(100, 10000),
                    searched_at=datetime.utcnow() - timedelta(hours=i * 3),
                )
            )
        session.commit()

        # --- preference-preservation experiment add-on (opt-in) ---
        import os as _os

        if _os.environ.get("CAVEAT_SHOP_EXPERIMENT") == "laptops":
            from backend.experiment_laptops import seed_laptops

            seed_laptops(session)
            # Start from an empty cart so the agent's final choice is unambiguous.
            for _item in session.query(CartItem).all():
                session.delete(_item)
            session.commit()

        print("Database seeded successfully!")


def reset_database(empty_cart: bool = True) -> None:
    """Drop everything, recreate the schema, and reseed to the baseline state.

    Used by the "reset on page refresh" behavior. By default the cart is left
    empty (the baseline seed otherwise pre-populates a couple of demo items),
    so a refresh gives a genuinely clean slate.
    """
    from sqlmodel import SQLModel

    import backend.models  # noqa: F401 — ensure all tables are registered

    engine = get_engine()
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    seed_database()

    if empty_cart:
        with Session(engine) as session:
            for item in session.query(CartItem).all():
                session.delete(item)
            session.commit()


if __name__ == "__main__":
    seed_database()
