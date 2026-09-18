# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""CAVEAT-Shop database models."""

from datetime import datetime, date
from typing import Optional, List
from sqlmodel import SQLModel, Field
import json


# ============================================================================
# User & Authentication Models
# ============================================================================

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    name: str
    phone: Optional[str] = None
    avatar_url: Optional[str] = None
    is_prime: bool = False
    prime_since: Optional[datetime] = None
    default_address_id: Optional[int] = None
    default_payment_id: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    session_token: str = Field(unique=True, index=True)
    device_type: str  # desktop, mobile, tablet, app
    device_name: str
    browser: Optional[str] = None
    os: Optional[str] = None
    ip_address: str
    location: Optional[str] = None
    is_current: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_activity_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime


class LoginWithCaveatShopApp(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    app_name: str
    app_id: str
    permissions: str  # JSON array
    authorized_at: datetime = Field(default_factory=datetime.utcnow)
    last_used_at: Optional[datetime] = None


# ============================================================================
# Address & Payment Models
# ============================================================================

class Address(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    full_name: str
    phone: str
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    state: str
    zip_code: str
    country: str = "United States"
    is_default: bool = False
    delivery_instructions: Optional[str] = None
    address_type: str = "residential"  # residential, commercial
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PaymentMethod(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    type: str  # credit_card, debit_card, gift_card, caveat_shop_store_card
    card_number_last4: str
    card_brand: str  # visa, mastercard, amex, discover
    expiry_month: int
    expiry_year: int
    cardholder_name: str
    billing_address_id: Optional[int] = Field(default=None, foreign_key="address.id")
    is_default: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Product Catalog Models
# ============================================================================

class Department(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    slug: str = Field(unique=True, index=True)
    description: Optional[str] = None
    image_url: Optional[str] = None
    parent_id: Optional[int] = Field(default=None, foreign_key="department.id")
    display_order: int = 0
    is_active: bool = True


class Category(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    department_id: int = Field(foreign_key="department.id", index=True)
    name: str
    slug: str = Field(index=True)
    description: Optional[str] = None
    image_url: Optional[str] = None
    parent_id: Optional[int] = Field(default=None, foreign_key="category.id")
    display_order: int = 0
    is_active: bool = True


class Brand(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    slug: str = Field(unique=True, index=True)
    logo_url: Optional[str] = None
    description: Optional[str] = None
    store_url: Optional[str] = None
    is_verified: bool = False


class Seller(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    slug: str = Field(unique=True, index=True)
    description: Optional[str] = None
    logo_url: Optional[str] = None
    rating: float = 0.0
    rating_count: int = 0
    is_caveat_shop: bool = False
    feedback_percentage: float = 100.0
    ships_from: str = "United States"
    return_policy: str = "30-day return"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Product(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    asin: str = Field(unique=True, index=True)
    title: str
    slug: str = Field(index=True)
    brand_id: Optional[int] = Field(default=None, foreign_key="brand.id")
    category_id: int = Field(foreign_key="category.id", index=True)
    seller_id: int = Field(foreign_key="seller.id", index=True)

    # Pricing
    price: float
    list_price: Optional[float] = None
    currency: str = "USD"

    # Description
    description_html: str = ""
    bullet_points: str = "[]"  # JSON array

    # Inventory
    stock_quantity: int = 0
    availability_status: str = "in_stock"  # in_stock, low_stock, out_of_stock, preorder
    max_order_quantity: int = 30

    # Media
    images: str = "[]"  # JSON array of URLs
    videos: Optional[str] = None  # JSON array

    # Ratings
    rating: float = 0.0
    rating_count: int = 0
    review_count: int = 0

    # Badges
    is_best_seller: bool = False
    best_seller_rank: Optional[int] = None
    best_seller_category: Optional[str] = None
    is_caveat_shop_choice: bool = False
    caveat_shop_choice_keyword: Optional[str] = None
    is_prime_eligible: bool = True
    is_climate_pledge: bool = False

    # Shipping
    weight_pounds: Optional[float] = None
    dimensions: Optional[str] = None  # JSON
    shipping_weight: Optional[float] = None
    ships_from: str = "CAVEAT-Shop"

    # Technical
    technical_details: str = "{}"  # JSON

    # Stats
    bought_past_month: int = 0

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def get_images(self) -> List[str]:
        return json.loads(self.images) if self.images else []

    def get_bullet_points(self) -> List[str]:
        return json.loads(self.bullet_points) if self.bullet_points else []


class ProductVariant(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id", index=True)
    variant_type: str  # color, size, style, configuration
    variant_value: str
    sku: str = Field(unique=True)
    price: float
    stock_quantity: int = 0
    images: str = "[]"  # JSON array
    is_available: bool = True


class ProductImage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id", index=True)
    url: str
    alt_text: str = ""
    is_primary: bool = False
    display_order: int = 0
    type: str = "main"  # main, variant, lifestyle, size_chart


# ============================================================================
# Cart Models
# ============================================================================

class Cart(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    session_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CartItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    cart_id: int = Field(foreign_key="cart.id", index=True)
    product_id: int = Field(foreign_key="product.id")
    variant_id: Optional[int] = Field(default=None, foreign_key="productvariant.id")
    quantity: int = 1
    is_gift: bool = False
    gift_message: Optional[str] = None
    saved_for_later: bool = False
    selected: bool = True          # cart checkbox: only selected items are ordered
    added_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Order Models
# ============================================================================

class Order(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    order_number: str = Field(unique=True, index=True)
    user_id: int = Field(foreign_key="user.id", index=True)

    # Addresses
    shipping_address_id: int = Field(foreign_key="address.id")
    billing_address_id: int = Field(foreign_key="address.id")

    # Payment
    payment_method_id: int = Field(foreign_key="paymentmethod.id")

    # Totals
    subtotal: float
    shipping_cost: float = 0.0
    tax: float = 0.0
    discount: float = 0.0
    service_fee: float = 0.0       # mandatory drip fee, disclosed at checkout (steered)
    total: float
    currency: str = "USD"

    # Status
    status: str = "pending"  # pending, processing, shipped, delivered, cancelled, returned
    is_archived: bool = False

    # Gift
    is_gift: bool = False
    gift_message: Optional[str] = None

    # Timestamps
    placed_at: datetime = Field(default_factory=datetime.utcnow)
    shipped_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None

    # Shipping
    shipping_method: str = "standard"  # standard, expedited, priority, same_day
    estimated_delivery_start: Optional[date] = None
    estimated_delivery_end: Optional[date] = None

    # Promo
    promo_code: Optional[str] = None
    promo_discount: float = 0.0


class OrderItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="order.id", index=True)
    product_id: int = Field(foreign_key="product.id")
    variant_id: Optional[int] = Field(default=None, foreign_key="productvariant.id")
    seller_id: int = Field(foreign_key="seller.id")

    quantity: int
    unit_price: float
    total_price: float

    # Status
    status: str = "pending"  # pending, shipped, delivered, returned

    # Tracking
    tracking_number: Optional[str] = None
    carrier: Optional[str] = None  # UPS, USPS, FedEx, CAVEAT-Shop Logistics

    # Return
    is_returnable: bool = True
    return_deadline: Optional[date] = None
    return_status: Optional[str] = None


# ============================================================================
# Review Models
# ============================================================================

class Review(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    order_item_id: Optional[int] = Field(default=None, foreign_key="orderitem.id")

    rating: int  # 1-5
    title: str
    body: str

    is_verified_purchase: bool = False

    images: Optional[str] = None  # JSON array
    videos: Optional[str] = None  # JSON array

    helpful_votes: int = 0
    total_votes: int = 0

    review_country: str = "United States"
    status: str = "published"  # pending, published, rejected

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ReviewVote(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    review_id: int = Field(foreign_key="review.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    is_helpful: bool
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Question(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id", index=True)
    user_id: int = Field(foreign_key="user.id")
    question_text: str
    answer_count: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Answer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="question.id", index=True)
    user_id: int = Field(foreign_key="user.id")
    answer_text: str
    is_seller_answer: bool = False
    helpful_votes: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Wishlist Models
# ============================================================================

class Wishlist(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    name: str = "Wish List"
    is_default: bool = False
    is_public: bool = False
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class WishlistItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    wishlist_id: int = Field(foreign_key="wishlist.id", index=True)
    product_id: int = Field(foreign_key="product.id")
    variant_id: Optional[int] = Field(default=None, foreign_key="productvariant.id")
    quantity_desired: int = 1
    quantity_received: int = 0
    priority: str = "medium"  # highest, high, medium, low, lowest
    comment: Optional[str] = None
    price_when_added: float
    added_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# History & Search Models
# ============================================================================

class BrowsingHistory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    product_id: int = Field(foreign_key="product.id")
    viewed_at: datetime = Field(default_factory=datetime.utcnow)


class SearchHistory(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    session_id: Optional[str] = None
    query: str
    department_filter: Optional[str] = None
    results_count: int = 0
    searched_at: datetime = Field(default_factory=datetime.utcnow)


class RecentlyViewed(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    product_id: int = Field(foreign_key="product.id")
    viewed_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Deal & Coupon Models
# ============================================================================

class Deal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id", index=True)
    deal_type: str  # lightning, deal_of_day, coupon, prime_early, subscribe_save
    discount_percentage: float
    deal_price: float
    original_price: float

    start_time: datetime
    end_time: datetime
    claimed_percentage: float = 0.0
    max_claims: Optional[int] = None
    current_claims: int = 0

    coupon_code: Optional[str] = None
    coupon_terms: Optional[str] = None

    is_active: bool = True
    is_prime_exclusive: bool = False


class Coupon(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(unique=True, index=True)
    description: str
    discount_type: str  # percentage, fixed
    discount_value: float
    min_order_amount: Optional[float] = None
    max_discount: Optional[float] = None
    valid_from: datetime
    valid_until: datetime
    usage_limit: Optional[int] = None
    usage_count: int = 0
    is_active: bool = True


# ============================================================================
# Subscription Models
# ============================================================================

class Subscription(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    product_id: int = Field(foreign_key="product.id")
    variant_id: Optional[int] = Field(default=None, foreign_key="productvariant.id")
    quantity: int = 1
    frequency_months: int = 1  # 1, 2, 3, 4, 5, 6
    discount_percentage: float = 5.0
    next_delivery_date: date
    shipping_address_id: int = Field(foreign_key="address.id")
    payment_method_id: int = Field(foreign_key="paymentmethod.id")
    status: str = "active"  # active, paused, cancelled
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Notification Models
# ============================================================================

class Notification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    type: str  # order_update, deal_alert, price_drop, back_in_stock, delivery
    title: str
    message: str
    link: Optional[str] = None
    is_read: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PriceWatch(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    product_id: int = Field(foreign_key="product.id")
    target_price: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Alexa Models
# ============================================================================

class AlexaShoppingList(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    item_name: str
    quantity: int = 1
    is_completed: bool = False
    added_via: str = "alexa"  # alexa, web, app
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None


# ============================================================================
# Gift Card Models
# ============================================================================

class GiftCard(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    code: str = Field(unique=True)
    original_amount: float
    current_balance: float
    currency: str = "USD"
    redeemed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class GiftCardTransaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    amount: float
    type: str  # redemption, reload, purchase, refund
    order_id: Optional[int] = Field(default=None, foreign_key="order.id")
    balance_after: float
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Registry Models
# ============================================================================

class Registry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    type: str  # wedding, baby, birthday, custom
    name: str
    event_date: Optional[date] = None
    is_public: bool = True
    shipping_address_id: Optional[int] = Field(default=None, foreign_key="address.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class RegistryItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    registry_id: int = Field(foreign_key="registry.id", index=True)
    product_id: int = Field(foreign_key="product.id")
    quantity_desired: int = 1
    quantity_purchased: int = 0
    priority: str = "medium"
    added_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Message Models
# ============================================================================

class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    sender_type: str  # caveat_shop, seller, system
    sender_id: Optional[int] = None
    subject: str
    body: str
    related_order_id: Optional[int] = Field(default=None, foreign_key="order.id")
    is_read: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Safety & Recall Models
# ============================================================================

class ProductRecall(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="product.id", index=True)
    title: str
    description: str
    severity: str  # high, medium, low
    action_required: str
    recall_date: date
    is_active: bool = True


class UserRecallAlert(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    recall_id: int = Field(foreign_key="productrecall.id")
    order_item_id: int = Field(foreign_key="orderitem.id")
    is_acknowledged: bool = False
    notified_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Preference Models
# ============================================================================

class ShoppingPreference(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", unique=True)
    language: str = "en_US"
    currency: str = "USD"
    country: str = "US"
    personalized_ads: bool = True
    browsing_history_enabled: bool = True
    recommendations_enabled: bool = True
    email_preferences: str = "{}"  # JSON
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class CaveatShopCreditCard(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    card_type: str  # store_card, prime_visa, secured
    card_number_last4: str
    rewards_balance: float = 0.0
    cashback_rate: float = 5.0
    is_primary: bool = False
    opened_at: datetime = Field(default_factory=datetime.utcnow)


class MusicLibrary(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    track_id: str
    title: str
    artist: str
    album: Optional[str] = None
    album_art_url: Optional[str] = None
    duration_seconds: int
    is_purchased: bool = False
    is_uploaded: bool = False
    added_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================================
# Business Account Models
# ============================================================================

class BusinessAccount(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    business_name: str
    business_type: str  # sole_proprietor, llc, corporation, nonprofit
    tax_id: Optional[str] = None
    is_business_prime: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BusinessAccountUser(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    business_account_id: int = Field(foreign_key="businessaccount.id", index=True)
    user_id: int = Field(foreign_key="user.id")
    role: str  # admin, buyer, viewer
    spending_limit: Optional[float] = None
    added_at: datetime = Field(default_factory=datetime.utcnow)



