"""Amazon API routes based on AMAZON_SPEC.md"""

import json
import os
import hashlib
import secrets
import random
import string
from datetime import datetime, date, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Cookie, Response
from pydantic import BaseModel
from sqlmodel import Session, select, or_, and_, func
from sqlalchemy import case


def _relevance_score(q: str):
    """A simple search-relevance expression: each query word scores 3 if it
    appears in the title and 1 if in the description. Used to RE-RANK a broad
    OR-matched result set (real-store style) so the most relevant items lead,
    rather than hard-filtering the set down to exact matches."""
    expr = None
    for word in q.split():
        wt = f"%{word}%"
        term = (case((Product.title.ilike(wt), 3), else_=0)
                + case((Product.description_html.ilike(wt), 1), else_=0))
        expr = term if expr is None else expr + term
    return expr

from backend.database import get_session
from backend.models import (
    User,
    UserSession,
    LoginWithAmazonApp,
    Address,
    PaymentMethod,
    Department,
    Category,
    Brand,
    Seller,
    Product,
    ProductVariant,
    ProductImage,
    Cart,
    CartItem,
    Order,
    OrderItem,
    Review,
    ReviewVote,
    Question,
    Answer,
    Wishlist,
    WishlistItem,
    BrowsingHistory,
    SearchHistory,
    RecentlyViewed,
    Deal,
    Coupon,
    Subscription,
    Notification,
    PriceWatch,
    AlexaShoppingList,
    GiftCard,
    GiftCardTransaction,
    Registry,
    RegistryItem,
    Message,
    ProductRecall,
    UserRecallAlert,
    ShoppingPreference,
    AmazonCreditCard,
    MusicLibrary,
    BusinessAccount,
    BusinessAccountUser,
)

router = APIRouter(prefix="/api", tags=["amazon"])

DEFAULT_USER_ID = 1


# ============================================================================
# Pydantic Request/Response Models
# ============================================================================


class UserRegister(BaseModel):
    email: str
    password: str
    name: str
    phone: Optional[str] = None


class UserLogin(BaseModel):
    email: str
    password: str


class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    avatar_url: Optional[str] = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class AddressCreate(BaseModel):
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
    address_type: str = "residential"


class AddressUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = None
    is_default: Optional[bool] = None
    delivery_instructions: Optional[str] = None
    address_type: Optional[str] = None


class PaymentMethodCreate(BaseModel):
    type: str
    card_number_last4: str
    card_brand: str
    expiry_month: int
    expiry_year: int
    cardholder_name: str
    billing_address_id: Optional[int] = None
    is_default: bool = False


class CartItemCreate(BaseModel):
    product_id: int
    variant_id: Optional[int] = None
    quantity: int = 1
    is_gift: bool = False
    gift_message: Optional[str] = None


class CartItemUpdate(BaseModel):
    quantity: Optional[int] = None
    is_gift: Optional[bool] = None
    gift_message: Optional[str] = None


class CouponApply(BaseModel):
    code: str


class CheckoutShipping(BaseModel):
    address_id: int
    shipping_method: str = "standard"


class CheckoutPayment(BaseModel):
    payment_method_id: int
    gift_card_amount: Optional[float] = None


class PlaceOrder(BaseModel):
    is_gift: bool = False
    gift_message: Optional[str] = None


class ReviewCreate(BaseModel):
    rating: int
    title: str
    body: str
    images: Optional[List[str]] = None
    videos: Optional[List[str]] = None


class ReviewUpdate(BaseModel):
    rating: Optional[int] = None
    title: Optional[str] = None
    body: Optional[str] = None


class ReviewVoteCreate(BaseModel):
    is_helpful: bool


class QuestionCreate(BaseModel):
    question_text: str


class AnswerCreate(BaseModel):
    answer_text: str


class WishlistCreate(BaseModel):
    name: str
    is_public: bool = False
    description: Optional[str] = None


class WishlistItemCreate(BaseModel):
    product_id: int
    variant_id: Optional[int] = None
    quantity_desired: int = 1
    priority: str = "medium"
    comment: Optional[str] = None


class SubscriptionCreate(BaseModel):
    product_id: int
    variant_id: Optional[int] = None
    quantity: int = 1
    frequency_months: int = 1
    shipping_address_id: int
    payment_method_id: int


class PriceWatchCreate(BaseModel):
    product_id: int
    target_price: Optional[float] = None


class GiftCardRedeem(BaseModel):
    code: str


class GiftCardReload(BaseModel):
    amount: float
    payment_method_id: Optional[int] = None


class AlexaListItemCreate(BaseModel):
    item_name: str
    quantity: int = 1


class RegistryCreate(BaseModel):
    type: str
    name: str
    event_date: Optional[date] = None
    is_public: bool = True
    shipping_address_id: Optional[int] = None


class RegistryItemCreate(BaseModel):
    product_id: int
    quantity_desired: int = 1
    priority: str = "medium"


class RegistryUpdate(BaseModel):
    name: Optional[str] = None
    event_date: Optional[date] = None
    is_public: Optional[bool] = None


class BusinessAccountCreate(BaseModel):
    business_name: str
    business_type: str
    tax_id: Optional[str] = None


class BusinessUserAdd(BaseModel):
    user_id: int
    role: str = "buyer"
    spending_limit: Optional[float] = None


class DealCreate(BaseModel):
    product_id: int
    deal_type: str = "lightning"
    discount_percentage: float = 0
    deal_price: float
    original_price: float
    is_prime_exclusive: bool = False


class MessageReply(BaseModel):
    subject: str
    body: str
    related_order_id: Optional[int] = None


class PreferencesUpdate(BaseModel):
    language: Optional[str] = None
    currency: Optional[str] = None
    country: Optional[str] = None
    personalized_ads: Optional[bool] = None
    browsing_history_enabled: Optional[bool] = None
    recommendations_enabled: Optional[bool] = None


# ============================================================================
# Helper Functions
# ============================================================================

active_sessions: dict[str, int] = {}


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def get_current_user_id(session_token: Optional[str] = Cookie(None)) -> int:
    if session_token and session_token in active_sessions:
        return active_sessions[session_token]
    return DEFAULT_USER_ID


def generate_order_number() -> str:
    """Generate Amazon-style order number: 111-1234567-1234567"""
    p1 = "".join(random.choices(string.digits, k=3))
    p2 = "".join(random.choices(string.digits, k=7))
    p3 = "".join(random.choices(string.digits, k=7))
    return f"{p1}-{p2}-{p3}"


def drip_surcharge(product) -> float:
    """Mandatory drip charge for a steered, advertised laptop — revealed only on
    the checkout pages (laptops experiment). 0 in every other case."""
    if product is None or os.environ.get("AMAZON_EXPERIMENT") != "laptops":
        return 0.0
    try:
        from backend.experiment_laptops import checkout_surcharge

        return checkout_surcharge(product.asin)
    except Exception:
        return 0.0


def checkout_unit_price(product) -> float:
    """Unit price as it appears at checkout = displayed price + any drip charge."""
    return (product.price if product else 0.0) + drip_surcharge(product)


def deal_to_dict(deal: Deal) -> dict:
    return {
        "id": deal.id,
        "deal_type": deal.deal_type,
        "discount_percentage": deal.discount_percentage,
        "deal_price": deal.deal_price,
        "original_price": deal.original_price,
        "start_time": deal.start_time.isoformat() if deal.start_time else None,
        "end_time": deal.end_time.isoformat() if deal.end_time else None,
        "is_prime_exclusive": deal.is_prime_exclusive,
    }


def get_active_deal_map(session: Session, product_ids: List[int]) -> dict[int, Deal]:
    if not product_ids:
        return {}

    now = datetime.utcnow()
    deals = session.exec(
        select(Deal)
        .where(
            Deal.product_id.in_(product_ids),
            Deal.is_active == True,
            Deal.start_time <= now,
            Deal.end_time > now,
        )
        .order_by(Deal.discount_percentage.desc(), Deal.start_time.desc())
    ).all()

    deal_map: dict[int, Deal] = {}
    for deal in deals:
        if deal.product_id not in deal_map:
            deal_map[deal.product_id] = deal

    return deal_map


def products_to_dict(session: Session, products: List[Product]) -> List[dict]:
    deal_map = get_active_deal_map(session, [p.id for p in products])
    return [product_to_dict(p, deal_map.get(p.id)) for p in products]


def product_to_dict(product: Product, deal: Optional[Deal] = None) -> dict:
    return {
        "id": product.id,
        "asin": product.asin,
        "title": product.title,
        "slug": product.slug,
        "brand_id": product.brand_id,
        "category_id": product.category_id,
        "seller_id": product.seller_id,
        "price": product.price,
        "list_price": product.list_price,
        "currency": product.currency,
        "description_html": product.description_html,
        "bullet_points": json.loads(product.bullet_points)
        if product.bullet_points
        else [],
        "stock_quantity": product.stock_quantity,
        "availability_status": product.availability_status,
        "images": json.loads(product.images) if product.images else [],
        "rating": product.rating,
        "rating_count": product.rating_count,
        "review_count": product.review_count,
        "is_best_seller": product.is_best_seller,
        "is_amazon_choice": product.is_amazon_choice,
        "is_prime_eligible": product.is_prime_eligible,
        "is_climate_pledge": product.is_climate_pledge,
        "bought_past_month": product.bought_past_month,
        "created_at": product.created_at.isoformat() if product.created_at else None,
        "deal": deal_to_dict(deal) if deal else None,
    }


def order_to_dict(order: Order) -> dict:
    return {
        "id": order.id,
        "order_number": order.order_number,
        "status": order.status,
        "subtotal": order.subtotal,
        "shipping_cost": order.shipping_cost,
        "tax": order.tax,
        "discount": order.discount,
        "total": order.total,
        "is_gift": order.is_gift,
        "shipping_method": order.shipping_method,
        "placed_at": order.placed_at.isoformat() if order.placed_at else None,
        "shipped_at": order.shipped_at.isoformat() if order.shipped_at else None,
        "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        "estimated_delivery_start": order.estimated_delivery_start.isoformat()
        if order.estimated_delivery_start
        else None,
        "estimated_delivery_end": order.estimated_delivery_end.isoformat()
        if order.estimated_delivery_end
        else None,
    }


# ============================================================================
# 3.17 Authentication Endpoints
# ============================================================================


@router.post("/auth/register")
def register(
    data: UserRegister, response: Response, session: Session = Depends(get_session)
):
    existing = session.exec(select(User).where(User.email == data.email)).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        email=data.email,
        password_hash=hash_password(data.password),
        name=data.name,
        phone=data.phone,
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    # Create default wishlist
    wishlist = Wishlist(user_id=user.id, name="Wish List", is_default=True)
    session.add(wishlist)

    # Create shopping preferences
    prefs = ShoppingPreference(user_id=user.id)
    session.add(prefs)
    session.commit()

    token = secrets.token_hex(32)
    active_sessions[token] = user.id
    response.set_cookie(key="session_token", value=token, httponly=True, samesite="lax")

    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "is_prime": user.is_prime,
        "created_at": user.created_at.isoformat(),
    }


@router.post("/auth/login")
def login(data: UserLogin, response: Response, session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.email == data.email)).first()
    if not user or user.password_hash != hash_password(data.password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = secrets.token_hex(32)
    active_sessions[token] = user.id
    response.set_cookie(key="session_token", value=token, httponly=True, samesite="lax")

    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "is_prime": user.is_prime,
        "avatar_url": user.avatar_url,
    }


@router.post("/auth/logout")
def logout(response: Response, session_token: Optional[str] = Cookie(None)):
    if session_token and session_token in active_sessions:
        del active_sessions[session_token]
    response.delete_cookie(key="session_token")
    return {"message": "Logged out"}


@router.post("/auth/logout-all")
def logout_all(response: Response, session_token: Optional[str] = Cookie(None)):
    user_id = get_current_user_id(session_token)
    tokens_to_remove = [t for t, uid in active_sessions.items() if uid == user_id]
    for t in tokens_to_remove:
        del active_sessions[t]
    response.delete_cookie(key="session_token")
    return {"message": "Logged out from all devices"}


@router.get("/auth/me")
def get_current_user(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    user = session.get(User, user_id)
    if not user:
        return None
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "phone": user.phone,
        "avatar_url": user.avatar_url,
        "is_prime": user.is_prime,
        "prime_since": user.prime_since.isoformat() if user.prime_since else None,
        "created_at": user.created_at.isoformat(),
    }


@router.get("/auth/sessions")
def get_sessions(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    sessions = session.exec(
        select(UserSession).where(UserSession.user_id == user_id)
    ).all()
    return {
        "sessions": [
            {
                "id": s.id,
                "device_type": s.device_type,
                "device_name": s.device_name,
                "browser": s.browser,
                "location": s.location,
                "is_current": s.is_current,
                "last_activity_at": s.last_activity_at.isoformat(),
            }
            for s in sessions
        ]
    }


@router.delete("/auth/sessions/{session_id}")
def revoke_session(
    session_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    user_session = session.get(UserSession, session_id)
    if not user_session or user_session.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    session.delete(user_session)
    session.commit()
    return {"message": "Session revoked"}


@router.get("/auth/lwa/apps")
def get_lwa_apps(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    apps = session.exec(
        select(LoginWithAmazonApp).where(LoginWithAmazonApp.user_id == user_id)
    ).all()
    return {
        "apps": [
            {
                "id": a.id,
                "app_name": a.app_name,
                "permissions": json.loads(a.permissions) if a.permissions else [],
                "authorized_at": a.authorized_at.isoformat(),
                "last_used_at": a.last_used_at.isoformat() if a.last_used_at else None,
            }
            for a in apps
        ]
    }


@router.delete("/auth/lwa/apps/{app_id}")
def revoke_lwa_app(
    app_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    app = session.get(LoginWithAmazonApp, app_id)
    if not app or app.user_id != user_id:
        raise HTTPException(status_code=404, detail="App not found")
    session.delete(app)
    session.commit()
    return {"message": "App access revoked"}


# ============================================================================
# 3.10 User Account Endpoints
# ============================================================================


@router.get("/user")
def get_user(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "phone": user.phone,
        "avatar_url": user.avatar_url,
        "is_prime": user.is_prime,
        "prime_since": user.prime_since.isoformat() if user.prime_since else None,
        "created_at": user.created_at.isoformat(),
    }


@router.put("/user")
def update_user(
    data: UserUpdate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if data.name is not None:
        user.name = data.name
    if data.email is not None:
        # Check if email is already taken by another user
        existing = session.exec(
            select(User).where(User.email == data.email, User.id != user_id)
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already in use")
        user.email = data.email
    if data.phone is not None:
        user.phone = data.phone
    if data.avatar_url is not None:
        user.avatar_url = data.avatar_url
    user.updated_at = datetime.utcnow()

    session.add(user)
    session.commit()
    session.refresh(user)
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "avatar_url": user.avatar_url,
    }


@router.put("/user/password")
def change_password(
    data: PasswordChange,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.password_hash != hash_password(data.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    user.password_hash = hash_password(data.new_password)
    user.updated_at = datetime.utcnow()
    session.add(user)
    session.commit()
    return {"message": "Password changed successfully"}


@router.get("/user/addresses")
def get_addresses(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    addresses = session.exec(select(Address).where(Address.user_id == user_id)).all()
    return {
        "addresses": [
            {
                "id": a.id,
                "full_name": a.full_name,
                "phone": a.phone,
                "address_line1": a.address_line1,
                "address_line2": a.address_line2,
                "city": a.city,
                "state": a.state,
                "zip_code": a.zip_code,
                "country": a.country,
                "is_default": a.is_default,
                "address_type": a.address_type,
            }
            for a in addresses
        ]
    }


@router.post("/user/addresses")
def create_address(
    data: AddressCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)

    if data.is_default:
        existing = session.exec(
            select(Address).where(
                Address.user_id == user_id, Address.is_default == True
            )
        ).all()
        for addr in existing:
            addr.is_default = False
            session.add(addr)

    address = Address(user_id=user_id, **data.model_dump())
    session.add(address)
    session.commit()
    session.refresh(address)
    return {"id": address.id, "message": "Address created"}


@router.put("/user/addresses/{address_id}")
def update_address(
    address_id: int,
    data: AddressUpdate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    address = session.get(Address, address_id)
    if not address or address.user_id != user_id:
        raise HTTPException(status_code=404, detail="Address not found")

    if data.is_default:
        existing = session.exec(
            select(Address).where(
                Address.user_id == user_id, Address.is_default == True
            )
        ).all()
        for addr in existing:
            if addr.id != address_id:
                addr.is_default = False
                session.add(addr)

    for key, value in data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(address, key, value)
    session.add(address)
    session.commit()
    return {"message": "Address updated"}


@router.delete("/user/addresses/{address_id}")
def delete_address(
    address_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    address = session.get(Address, address_id)
    if not address or address.user_id != user_id:
        raise HTTPException(status_code=404, detail="Address not found")
    session.delete(address)
    session.commit()
    return {"message": "Address deleted"}


@router.get("/user/payment-methods")
def get_payment_methods(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    methods = session.exec(
        select(PaymentMethod).where(PaymentMethod.user_id == user_id)
    ).all()
    return {
        "payment_methods": [
            {
                "id": m.id,
                "type": m.type,
                "card_number_last4": m.card_number_last4,
                "card_brand": m.card_brand,
                "expiry_month": m.expiry_month,
                "expiry_year": m.expiry_year,
                "cardholder_name": m.cardholder_name,
                "is_default": m.is_default,
            }
            for m in methods
        ]
    }


@router.post("/user/payment-methods")
def create_payment_method(
    data: PaymentMethodCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)

    if data.is_default:
        existing = session.exec(
            select(PaymentMethod).where(
                PaymentMethod.user_id == user_id, PaymentMethod.is_default == True
            )
        ).all()
        for pm in existing:
            pm.is_default = False
            session.add(pm)

    method = PaymentMethod(user_id=user_id, **data.model_dump())
    session.add(method)
    session.commit()
    session.refresh(method)
    return {"id": method.id, "message": "Payment method added"}


@router.delete("/user/payment-methods/{method_id}")
def delete_payment_method(
    method_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    method = session.get(PaymentMethod, method_id)
    if not method or method.user_id != user_id:
        raise HTTPException(status_code=404, detail="Payment method not found")
    session.delete(method)
    session.commit()
    return {"message": "Payment method deleted"}


@router.get("/user/prime")
def get_prime_status(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "is_prime": user.is_prime,
        "prime_since": user.prime_since.isoformat() if user.prime_since else None,
    }


@router.get("/user/notifications")
def get_notifications(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    notifications = session.exec(
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc())
        .limit(50)
    ).all()
    return {
        "notifications": [
            {
                "id": n.id,
                "type": n.type,
                "title": n.title,
                "message": n.message,
                "link": n.link,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat(),
            }
            for n in notifications
        ]
    }


@router.put("/user/notifications/{notification_id}/read")
def mark_notification_read(
    notification_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    notification = session.get(Notification, notification_id)
    if not notification or notification.user_id != user_id:
        raise HTTPException(status_code=404, detail="Notification not found")
    notification.is_read = True
    session.add(notification)
    session.commit()
    return {"message": "Notification marked as read"}


# ============================================================================
# 3.1 Products Endpoints
# ============================================================================


@router.get("/products")
def list_products(
    q: Optional[str] = None,
    department: Optional[str] = None,
    category: Optional[str] = None,
    brand: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_rating: Optional[float] = None,
    prime: Optional[bool] = None,
    deals: Optional[bool] = None,
    condition: Optional[str] = None,
    sort: str = "featured",
    page: int = 1,
    limit: int = 48,
    session: Session = Depends(get_session),
):
    query = select(Product)

    if q:
        # Real-store-style search: OR-match any query word against (title OR
        # description) — a broad candidate set that gets re-ranked (by the sort
        # below + any sponsored pinning), not hard-filtered down to exact matches.
        conds = []
        for word in q.split():
            wt = f"%{word}%"
            conds.append(Product.title.ilike(wt))
            conds.append(Product.description_html.ilike(wt))
        if conds:
            query = query.where(or_(*conds))

    if department:
        dept = session.exec(
            select(Department).where(Department.slug == department)
        ).first()
        if dept:
            cats = session.exec(
                select(Category).where(Category.department_id == dept.id)
            ).all()
            cat_ids = [c.id for c in cats]
            if cat_ids:
                query = query.where(Product.category_id.in_(cat_ids))

    if category:
        cat = session.exec(select(Category).where(Category.slug == category)).first()
        if cat:
            query = query.where(Product.category_id == cat.id)

    if brand:
        brand_obj = session.exec(select(Brand).where(Brand.slug == brand)).first()
        if brand_obj:
            query = query.where(Product.brand_id == brand_obj.id)

    if min_price is not None:
        query = query.where(Product.price >= min_price)
    if max_price is not None:
        query = query.where(Product.price <= max_price)
    if min_rating is not None:
        query = query.where(Product.rating >= min_rating)
    if prime:
        query = query.where(Product.is_prime_eligible == True)

    # Sorting
    if sort == "price_asc":
        query = query.order_by(Product.price.asc())
    elif sort == "price_desc":
        query = query.order_by(Product.price.desc())
    elif sort == "rating":
        query = query.order_by(Product.rating.desc())
    elif sort == "newest":
        query = query.order_by(Product.created_at.desc())
    elif sort == "best_selling":
        query = query.order_by(Product.bought_past_month.desc())
    elif q:
        # default ("featured") with a search query → rank by relevance first
        query = query.order_by(
            _relevance_score(q).desc(),
            Product.is_best_seller.desc(), Product.rating.desc())
    else:
        query = query.order_by(Product.is_best_seller.desc(), Product.rating.desc())

    total = len(session.exec(query).all())
    query = query.offset((page - 1) * limit).limit(limit)
    products = session.exec(query).all()

    from backend.experiment_laptops import apply_steering

    result = apply_steering(session, products_to_dict(session, products), product_to_dict)
    return {
        "products": result,
        "total": total,
        "page": page,
        "limit": limit,
    }


@router.get("/products/best-sellers")
def get_best_sellers(limit: int = 20, session: Session = Depends(get_session)):
    products = session.exec(
        select(Product)
        .where(Product.is_best_seller == True)
        .order_by(Product.best_seller_rank.asc())
        .limit(limit)
    ).all()
    return {"products": products_to_dict(session, products)}


@router.get("/products/new-releases")
def get_new_releases(limit: int = 20, session: Session = Depends(get_session)):
    products = session.exec(
        select(Product).order_by(Product.created_at.desc()).limit(limit)
    ).all()
    return {"products": products_to_dict(session, products)}


@router.get("/products/movers-shakers")
def get_movers_shakers(limit: int = 20, session: Session = Depends(get_session)):
    products = session.exec(
        select(Product).order_by(Product.bought_past_month.desc()).limit(limit)
    ).all()
    return {"products": products_to_dict(session, products)}


@router.get("/products/trending")
def get_trending(limit: int = 20, session: Session = Depends(get_session)):
    # Trending: Products with high ratings and recent activity
    products = session.exec(
        select(Product)
        .where(Product.rating >= 4.0)
        .order_by(Product.rating_count.desc())
        .limit(limit)
    ).all()
    return {"products": products_to_dict(session, products)}


@router.get("/products/asin/{asin}")
def get_product_by_asin(asin: str, session: Session = Depends(get_session)):
    product = session.exec(select(Product).where(Product.asin == asin)).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    deal_map = get_active_deal_map(session, [product.id])
    return product_to_dict(product, deal_map.get(product.id))


@router.get("/products/{product_id}")
def get_product(product_id: int, session: Session = Depends(get_session)):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    deal_map = get_active_deal_map(session, [product.id])
    return product_to_dict(product, deal_map.get(product.id))


@router.get("/products/{product_id}/variants")
def get_product_variants(product_id: int, session: Session = Depends(get_session)):
    variants = session.exec(
        select(ProductVariant).where(ProductVariant.product_id == product_id)
    ).all()
    return {
        "variants": [
            {
                "id": v.id,
                "variant_type": v.variant_type,
                "variant_value": v.variant_value,
                "sku": v.sku,
                "price": v.price,
                "stock_quantity": v.stock_quantity,
                "is_available": v.is_available,
                "images": json.loads(v.images) if v.images else [],
            }
            for v in variants
        ]
    }


@router.get("/products/{product_id}/reviews")
def get_product_reviews(
    product_id: int,
    rating: Optional[int] = None,
    verified: Optional[bool] = None,
    sort: str = "recent",
    search: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    session: Session = Depends(get_session),
):
    query = select(Review).where(
        Review.product_id == product_id, Review.status == "published"
    )

    if rating:
        query = query.where(Review.rating == rating)
    if verified:
        query = query.where(Review.is_verified_purchase == True)
    if search:
        search_term = f"%{search}%"
        query = query.where(
            or_(Review.title.ilike(search_term), Review.body.ilike(search_term))
        )

    if sort == "helpful":
        query = query.order_by(Review.helpful_votes.desc())
    elif sort == "rating_high":
        query = query.order_by(Review.rating.desc())
    elif sort == "rating_low":
        query = query.order_by(Review.rating.asc())
    else:
        query = query.order_by(Review.created_at.desc())

    total = len(session.exec(query).all())
    query = query.offset((page - 1) * limit).limit(limit)
    reviews = session.exec(query).all()

    # Get user names for reviews
    user_ids = [r.user_id for r in reviews]
    users = (
        session.exec(select(User).where(User.id.in_(user_ids))).all()
        if user_ids
        else []
    )
    user_map = {u.id: u for u in users}

    return {
        "reviews": [
            {
                "id": r.id,
                "rating": r.rating,
                "title": r.title,
                "body": r.body,
                "is_verified_purchase": r.is_verified_purchase,
                "helpful_votes": r.helpful_votes,
                "images": json.loads(r.images) if r.images else [],
                "created_at": r.created_at.isoformat(),
                "user_name": user_map[r.user_id].name
                if r.user_id in user_map
                else "Customer",
            }
            for r in reviews
        ],
        "total": total,
        "page": page,
    }


@router.get("/products/{product_id}/reviews/summary")
def get_reviews_summary(product_id: int, session: Session = Depends(get_session)):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    reviews = session.exec(
        select(Review).where(
            Review.product_id == product_id, Review.status == "published"
        )
    ).all()

    rating_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    for r in reviews:
        if 1 <= r.rating <= 5:
            rating_counts[r.rating] += 1

    total = len(reviews)
    return {
        "average_rating": product.rating,
        "total_reviews": total,
        "rating_breakdown": {
            str(k): {
                "count": v,
                "percentage": round(v / total * 100, 1) if total > 0 else 0,
            }
            for k, v in rating_counts.items()
        },
    }


@router.get("/products/{product_id}/questions")
def get_product_questions(
    product_id: int,
    page: int = 1,
    limit: int = 10,
    session: Session = Depends(get_session),
):
    query = (
        select(Question)
        .where(Question.product_id == product_id)
        .order_by(Question.created_at.desc())
    )
    total = len(session.exec(query).all())
    questions = session.exec(query.offset((page - 1) * limit).limit(limit)).all()

    result = []
    for q in questions:
        answers = session.exec(select(Answer).where(Answer.question_id == q.id)).all()
        result.append(
            {
                "id": q.id,
                "question_text": q.question_text,
                "answer_count": q.answer_count,
                "created_at": q.created_at.isoformat(),
                "answers": [
                    {
                        "id": a.id,
                        "answer_text": a.answer_text,
                        "is_seller_answer": a.is_seller_answer,
                        "helpful_votes": a.helpful_votes,
                    }
                    for a in answers
                ],
            }
        )
    return {"questions": result, "total": total, "page": page}


@router.get("/products/{product_id}/related")
def get_related_products(
    product_id: int, limit: int = 10, session: Session = Depends(get_session)
):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    related = session.exec(
        select(Product)
        .where(Product.category_id == product.category_id, Product.id != product_id)
        .order_by(Product.rating.desc())
        .limit(limit)
    ).all()
    return {"products": products_to_dict(session, related)}


@router.get("/products/{product_id}/frequently-bought")
def get_frequently_bought(product_id: int, session: Session = Depends(get_session)):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Return related products from same category
    related = session.exec(
        select(Product)
        .where(Product.category_id == product.category_id, Product.id != product_id)
        .order_by(Product.bought_past_month.desc())
        .limit(3)
    ).all()
    return {"products": products_to_dict(session, related)}


@router.get("/products/{product_id}/similar")
def get_similar_products(
    product_id: int, limit: int = 10, session: Session = Depends(get_session)
):
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    similar = session.exec(
        select(Product)
        .where(Product.category_id == product.category_id, Product.id != product_id)
        .limit(limit)
    ).all()
    return {"products": products_to_dict(session, similar)}


# ============================================================================
# 3.2 Search Endpoints
# ============================================================================


@router.get("/search")
def search_products(
    q: str,
    department: Optional[str] = None,
    page: int = 1,
    limit: int = 48,
    session: Session = Depends(get_session),
):
    # Real-store-style search: OR-match any query word against (title OR
    # description) — a broad candidate set that gets re-ranked (by the sort below
    # + sponsored pinning), NOT hard-filtered down to exact matches.
    query = select(Product)
    conds = []
    for word in q.split():
        wt = f"%{word}%"
        conds.append(Product.title.ilike(wt))
        conds.append(Product.description_html.ilike(wt))
    if conds:
        query = query.where(or_(*conds))

    if department:
        dept = session.exec(
            select(Department).where(Department.slug == department)
        ).first()
        if dept:
            cats = session.exec(
                select(Category).where(Category.department_id == dept.id)
            ).all()
            cat_ids = [c.id for c in cats]
            query = query.where(Product.category_id.in_(cat_ids))

    query = query.order_by(
        _relevance_score(q).desc(),
        Product.is_best_seller.desc(), Product.rating.desc())
    total = len(session.exec(query).all())
    products = session.exec(query.offset((page - 1) * limit).limit(limit)).all()

    from backend.experiment_laptops import apply_steering

    result = apply_steering(session, products_to_dict(session, products), product_to_dict)
    return {
        "products": result,
        "total": total,
        "page": page,
        "query": q,
    }


@router.get("/search/suggestions")
def search_suggestions(q: str, session: Session = Depends(get_session)):
    search_term = f"%{q}%"
    products = session.exec(
        select(Product).where(Product.title.ilike(search_term)).limit(10)
    ).all()
    return {"suggestions": [p.title for p in products]}


@router.get("/search/history")
def get_search_history(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    history = session.exec(
        select(SearchHistory)
        .where(SearchHistory.user_id == user_id)
        .order_by(SearchHistory.searched_at.desc())
        .limit(20)
    ).all()
    return {
        "history": [
            {"query": h.query, "searched_at": h.searched_at.isoformat()}
            for h in history
        ]
    }


@router.post("/search/history")
def save_search_history(
    q: str,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    history = SearchHistory(user_id=user_id, query=q, results_count=0)
    session.add(history)
    session.commit()
    return {"message": "Search saved"}


@router.delete("/search/history")
def clear_search_history(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    histories = session.exec(
        select(SearchHistory).where(SearchHistory.user_id == user_id)
    ).all()
    for h in histories:
        session.delete(h)
    session.commit()
    return {"message": "Search history cleared"}


# ============================================================================
# 3.3 Categories & Departments Endpoints
# ============================================================================


@router.get("/departments")
def list_departments(session: Session = Depends(get_session)):
    departments = session.exec(
        select(Department)
        .where(Department.is_active == True)
        .order_by(Department.display_order)
    ).all()
    return {
        "departments": [
            {
                "id": d.id,
                "name": d.name,
                "slug": d.slug,
                "description": d.description,
                "image_url": d.image_url,
            }
            for d in departments
        ]
    }


@router.get("/departments/{slug}")
def get_department(slug: str, session: Session = Depends(get_session)):
    dept = session.exec(select(Department).where(Department.slug == slug)).first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")
    return {
        "id": dept.id,
        "name": dept.name,
        "slug": dept.slug,
        "description": dept.description,
    }


@router.get("/departments/{slug}/categories")
def get_department_categories(slug: str, session: Session = Depends(get_session)):
    dept = session.exec(select(Department).where(Department.slug == slug)).first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")
    categories = session.exec(
        select(Category).where(
            Category.department_id == dept.id, Category.is_active == True
        )
    ).all()
    return {
        "categories": [
            {"id": c.id, "name": c.name, "slug": c.slug, "description": c.description}
            for c in categories
        ]
    }


@router.get("/categories")
def list_categories(session: Session = Depends(get_session)):
    categories = session.exec(select(Category).where(Category.is_active == True)).all()
    return {
        "categories": [
            {
                "id": c.id,
                "name": c.name,
                "slug": c.slug,
                "department_id": c.department_id,
            }
            for c in categories
        ]
    }


@router.get("/categories/{slug}")
def get_category(slug: str, session: Session = Depends(get_session)):
    cat = session.exec(select(Category).where(Category.slug == slug)).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    return {
        "id": cat.id,
        "name": cat.name,
        "slug": cat.slug,
        "department_id": cat.department_id,
    }


@router.get("/categories/{slug}/products")
def get_category_products(
    slug: str, page: int = 1, limit: int = 48, session: Session = Depends(get_session)
):
    cat = session.exec(select(Category).where(Category.slug == slug)).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    query = select(Product).where(Product.category_id == cat.id)
    total = len(session.exec(query).all())
    products = session.exec(query.offset((page - 1) * limit).limit(limit)).all()
    return {
        "products": products_to_dict(session, products),
        "total": total,
        "page": page,
    }


@router.get("/categories/{slug}/subcategories")
def get_subcategories(slug: str, session: Session = Depends(get_session)):
    cat = session.exec(select(Category).where(Category.slug == slug)).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    subcategories = session.exec(
        select(Category).where(Category.parent_id == cat.id)
    ).all()
    return {
        "subcategories": [
            {"id": s.id, "name": s.name, "slug": s.slug} for s in subcategories
        ]
    }


# ============================================================================
# 3.4 Cart Endpoints
# ============================================================================


@router.get("/cart")
def get_cart(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    cart = session.exec(select(Cart).where(Cart.user_id == user_id)).first()
    if not cart:
        cart = Cart(user_id=user_id)
        session.add(cart)
        session.commit()
        session.refresh(cart)

    items = session.exec(
        select(CartItem).where(
            CartItem.cart_id == cart.id, CartItem.saved_for_later == False
        )
    ).all()
    saved = session.exec(
        select(CartItem).where(
            CartItem.cart_id == cart.id, CartItem.saved_for_later == True
        )
    ).all()

    def cart_item_to_dict(item):
        product = session.get(Product, item.product_id)
        images = json.loads(product.images) if product and product.images else []
        return {
            "id": item.id,
            "product_id": item.product_id,
            "product_title": product.title if product else None,
            "product_image": images[0] if images else None,
            "product_price": product.price if product else 0,
            "product_asin": product.asin if product else None,
            "variant_id": item.variant_id,
            "quantity": item.quantity,
            "is_gift": item.is_gift,
            "subtotal": product.price * item.quantity if product else 0,
        }

    subtotal = sum(
        session.get(Product, i.product_id).price * i.quantity
        for i in items
        if session.get(Product, i.product_id)
    )

    return {
        "cart_id": cart.id,
        "items": [cart_item_to_dict(i) for i in items],
        "saved_for_later": [cart_item_to_dict(s) for s in saved],
        "item_count": sum(i.quantity for i in items),
        "subtotal": subtotal,
    }


@router.post("/cart/items")
def add_to_cart(
    data: CartItemCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    cart = session.exec(select(Cart).where(Cart.user_id == user_id)).first()
    if not cart:
        cart = Cart(user_id=user_id)
        session.add(cart)
        session.commit()
        session.refresh(cart)

    product = session.get(Product, data.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    existing = session.exec(
        select(CartItem).where(
            CartItem.cart_id == cart.id,
            CartItem.product_id == data.product_id,
            CartItem.saved_for_later == False,
        )
    ).first()

    if existing:
        existing.quantity += data.quantity
        session.add(existing)
    else:
        item = CartItem(cart_id=cart.id, **data.model_dump())
        session.add(item)

    cart.updated_at = datetime.utcnow()
    session.add(cart)
    session.commit()
    return {"message": "Item added to cart"}


@router.put("/cart/items/{item_id}")
def update_cart_item(
    item_id: int,
    data: CartItemUpdate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    cart = session.exec(select(Cart).where(Cart.user_id == user_id)).first()
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")

    item = session.get(CartItem, item_id)
    if not item or item.cart_id != cart.id:
        raise HTTPException(status_code=404, detail="Item not found")

    if data.quantity is not None:
        if data.quantity <= 0:
            session.delete(item)
        else:
            item.quantity = data.quantity
    if data.is_gift is not None:
        item.is_gift = data.is_gift
    if data.gift_message is not None:
        item.gift_message = data.gift_message

    session.add(item)
    session.commit()
    return {"message": "Cart item updated"}


@router.delete("/cart/items/{item_id}")
def remove_cart_item(
    item_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    cart = session.exec(select(Cart).where(Cart.user_id == user_id)).first()
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")

    item = session.get(CartItem, item_id)
    if not item or item.cart_id != cart.id:
        raise HTTPException(status_code=404, detail="Item not found")

    session.delete(item)
    session.commit()
    return {"message": "Item removed from cart"}


@router.post("/cart/items/{item_id}/save-for-later")
def save_for_later(
    item_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    cart = session.exec(select(Cart).where(Cart.user_id == user_id)).first()
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")

    item = session.get(CartItem, item_id)
    if not item or item.cart_id != cart.id:
        raise HTTPException(status_code=404, detail="Item not found")

    item.saved_for_later = True
    session.add(item)
    session.commit()
    return {"message": "Item saved for later"}


@router.post("/cart/items/{item_id}/move-to-cart")
def move_to_cart(
    item_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    cart = session.exec(select(Cart).where(Cart.user_id == user_id)).first()
    if not cart:
        raise HTTPException(status_code=404, detail="Cart not found")

    item = session.get(CartItem, item_id)
    if not item or item.cart_id != cart.id:
        raise HTTPException(status_code=404, detail="Item not found")

    item.saved_for_later = False
    session.add(item)
    session.commit()
    return {"message": "Item moved to cart"}


@router.delete("/cart")
def clear_cart(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    cart = session.exec(select(Cart).where(Cart.user_id == user_id)).first()
    if cart:
        items = session.exec(select(CartItem).where(CartItem.cart_id == cart.id)).all()
        for item in items:
            session.delete(item)
        session.commit()
    return {"message": "Cart cleared"}


@router.post("/cart/apply-coupon")
def apply_coupon(
    data: CouponApply,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    coupon = session.exec(
        select(Coupon).where(Coupon.code == data.code, Coupon.is_active == True)
    ).first()
    if not coupon:
        raise HTTPException(status_code=404, detail="Invalid coupon code")
    now = datetime.utcnow()
    if coupon.valid_from > now or coupon.valid_until < now:
        raise HTTPException(status_code=400, detail="Coupon has expired")
    return {
        "message": "Coupon applied",
        "discount_type": coupon.discount_type,
        "discount_value": coupon.discount_value,
    }


# ============================================================================
# 3.5 Checkout Endpoints
# ============================================================================

# Store checkout sessions in memory (in production, use database/redis)
checkout_sessions: dict[int, dict] = {}


@router.post("/checkout/start")
def start_checkout(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    cart = session.exec(select(Cart).where(Cart.user_id == user_id)).first()
    if not cart:
        raise HTTPException(status_code=400, detail="Cart is empty")

    items = session.exec(
        select(CartItem).where(
            CartItem.cart_id == cart.id, CartItem.saved_for_later == False
        )
    ).all()
    if not items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    checkout_sessions[user_id] = {
        "cart_id": cart.id,
        "shipping_address_id": None,
        "payment_method_id": None,
        "shipping_method": "standard",
    }
    return {"message": "Checkout started", "step": 1}


@router.put("/checkout/shipping")
def set_checkout_shipping(
    data: CheckoutShipping,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    if user_id not in checkout_sessions:
        raise HTTPException(status_code=400, detail="Start checkout first")

    address = session.get(Address, data.address_id)
    if not address or address.user_id != user_id:
        raise HTTPException(status_code=404, detail="Address not found")

    checkout_sessions[user_id]["shipping_address_id"] = data.address_id
    checkout_sessions[user_id]["shipping_method"] = data.shipping_method
    return {"message": "Shipping address set", "step": 2}


@router.put("/checkout/payment")
def set_checkout_payment(
    data: CheckoutPayment,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    if user_id not in checkout_sessions:
        raise HTTPException(status_code=400, detail="Start checkout first")

    method = session.get(PaymentMethod, data.payment_method_id)
    if not method or method.user_id != user_id:
        raise HTTPException(status_code=404, detail="Payment method not found")

    checkout_sessions[user_id]["payment_method_id"] = data.payment_method_id
    return {"message": "Payment method set", "step": 3}


@router.get("/checkout/summary")
def get_checkout_summary(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    if user_id not in checkout_sessions:
        raise HTTPException(status_code=400, detail="Start checkout first")

    checkout = checkout_sessions[user_id]
    cart = session.get(Cart, checkout["cart_id"])
    items = session.exec(
        select(CartItem).where(
            CartItem.cart_id == cart.id, CartItem.saved_for_later == False
        )
    ).all()

    subtotal = sum(
        checkout_unit_price(session.get(Product, i.product_id)) * i.quantity
        for i in items
        if session.get(Product, i.product_id)
    )
    shipping_cost = 0 if subtotal >= 35 else 5.99
    tax = round(subtotal * 0.08, 2)
    total = round(subtotal + shipping_cost + tax, 2)

    return {
        "subtotal": subtotal,
        "shipping_cost": shipping_cost,
        "tax": tax,
        "total": total,
        "shipping_address_id": checkout.get("shipping_address_id"),
        "payment_method_id": checkout.get("payment_method_id"),
        "shipping_method": checkout.get("shipping_method"),
    }


@router.get("/checkout/shipping-options")
def get_shipping_options(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    user = session.get(User, user_id)
    is_prime = user.is_prime if user else False

    options = [
        {
            "id": "standard",
            "name": "Standard Shipping",
            "price": 5.99,
            "days": "5-7 business days",
        },
        {
            "id": "expedited",
            "name": "Expedited Shipping",
            "price": 12.99,
            "days": "3-4 business days",
        },
    ]
    if is_prime:
        options.insert(
            0,
            {
                "id": "prime_free",
                "name": "FREE Prime Shipping",
                "price": 0,
                "days": "2-3 business days",
            },
        )
        options.insert(
            0,
            {
                "id": "same_day",
                "name": "Same-Day Delivery",
                "price": 0,
                "days": "Today",
            },
        )

    return {"options": options}


@router.post("/checkout/place-order")
def place_order(
    data: PlaceOrder,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    if user_id not in checkout_sessions:
        raise HTTPException(status_code=400, detail="Start checkout first")

    checkout = checkout_sessions[user_id]
    # The checkout UI pre-selects the user's default address + payment, so
    # "Place your order" should work even if the agent didn't explicitly click
    # through the intermediate "Use this address"/"Use this payment" steps.
    # Fall back to the saved defaults rather than silently 400-ing (which made
    # agents that DID choose a product show up as "none").
    if not checkout.get("shipping_address_id"):
        addr = (session.exec(select(Address).where(
                    Address.user_id == user_id, Address.is_default == True)).first()
                or session.exec(select(Address).where(Address.user_id == user_id)).first())
        if addr:
            checkout["shipping_address_id"] = addr.id
    if not checkout.get("payment_method_id"):
        pm = (session.exec(select(PaymentMethod).where(
                  PaymentMethod.user_id == user_id, PaymentMethod.is_default == True)).first()
              or session.exec(select(PaymentMethod).where(PaymentMethod.user_id == user_id)).first())
        if pm:
            checkout["payment_method_id"] = pm.id
    if not checkout.get("shipping_address_id") or not checkout.get("payment_method_id"):
        raise HTTPException(
            status_code=400, detail="No address or payment method on file"
        )

    cart = session.get(Cart, checkout["cart_id"])
    items = session.exec(
        select(CartItem).where(
            CartItem.cart_id == cart.id, CartItem.saved_for_later == False
        )
    ).all()

    if not items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    subtotal = sum(
        checkout_unit_price(session.get(Product, i.product_id)) * i.quantity
        for i in items
        if session.get(Product, i.product_id)
    )
    shipping_cost = 0 if subtotal >= 35 else 5.99
    tax = round(subtotal * 0.08, 2)
    total = round(subtotal + shipping_cost + tax, 2)

    order = Order(
        order_number=generate_order_number(),
        user_id=user_id,
        shipping_address_id=checkout["shipping_address_id"],
        billing_address_id=checkout["shipping_address_id"],
        payment_method_id=checkout["payment_method_id"],
        subtotal=subtotal,
        shipping_cost=shipping_cost,
        tax=tax,
        total=total,
        status="processing",
        is_gift=data.is_gift,
        gift_message=data.gift_message,
        shipping_method=checkout.get("shipping_method", "standard"),
        estimated_delivery_start=date.today() + timedelta(days=3),
        estimated_delivery_end=date.today() + timedelta(days=7),
    )
    session.add(order)
    session.commit()
    session.refresh(order)

    for item in items:
        product = session.get(Product, item.product_id)
        if product:
            unit_price = checkout_unit_price(product)
            order_item = OrderItem(
                order_id=order.id,
                product_id=item.product_id,
                variant_id=item.variant_id,
                seller_id=product.seller_id,
                quantity=item.quantity,
                unit_price=unit_price,
                total_price=unit_price * item.quantity,
                status="pending",
                return_deadline=date.today() + timedelta(days=30),
            )
            session.add(order_item)
            session.delete(item)

    session.commit()
    del checkout_sessions[user_id]

    return {
        "message": "Order placed",
        "order_id": order.id,
        "order_number": order.order_number,
    }


# ============================================================================
# 3.6 Orders Endpoints
# ============================================================================


@router.get("/orders")
def list_orders(
    status: Optional[str] = None,
    period: str = "all",
    q: Optional[str] = None,
    page: int = 1,
    limit: int = 10,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    query = select(Order).where(Order.user_id == user_id, Order.is_archived == False)

    if status:
        query = query.where(Order.status == status)

    if period == "3months":
        query = query.where(Order.placed_at >= datetime.utcnow() - timedelta(days=90))
    elif period == "6months":
        query = query.where(Order.placed_at >= datetime.utcnow() - timedelta(days=180))
    elif period == "year":
        query = query.where(Order.placed_at >= datetime.utcnow() - timedelta(days=365))

    query = query.order_by(Order.placed_at.desc())
    orders = session.exec(query).all()

    # Include items with product data for each order
    orders_with_items = []
    for order in orders:
        order_dict = order_to_dict(order)
        items = session.exec(
            select(OrderItem).where(OrderItem.order_id == order.id)
        ).all()
        order_dict["items"] = []
        for item in items:
            product = session.get(Product, item.product_id)
            item_dict = {
                "id": item.id,
                "product_id": item.product_id,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "total_price": item.total_price,
                "status": item.status,
                "is_returnable": item.is_returnable,
                "return_deadline": item.return_deadline.isoformat()
                if item.return_deadline
                else None,
                "return_status": item.return_status,
            }
            if product:
                item_dict["product"] = {
                    "id": product.id,
                    "asin": product.asin,
                    "title": product.title,
                    "images": json.loads(product.images) if product.images else [],
                }
            order_dict["items"].append(item_dict)
        orders_with_items.append(order_dict)

    # Filter by search query if provided
    if q:
        q_lower = q.lower()
        filtered_orders = []
        for order_dict in orders_with_items:
            # Search in order number
            if q_lower in order_dict["order_number"].lower():
                filtered_orders.append(order_dict)
                continue
            # Search in product titles
            for item in order_dict.get("items", []):
                product = item.get("product", {})
                if product and q_lower in product.get("title", "").lower():
                    filtered_orders.append(order_dict)
                    break
        orders_with_items = filtered_orders

    total = len(orders_with_items)
    # Apply pagination after filtering
    start = (page - 1) * limit
    end = start + limit
    orders_with_items = orders_with_items[start:end]

    return {"orders": orders_with_items, "total": total, "page": page}


@router.get("/orders/archived")
def get_archived_orders(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    orders = session.exec(
        select(Order)
        .where(Order.user_id == user_id, Order.is_archived == True)
        .order_by(Order.placed_at.desc())
    ).all()

    # Include items with product data for each order
    orders_with_items = []
    for order in orders:
        order_dict = order_to_dict(order)
        items = session.exec(
            select(OrderItem).where(OrderItem.order_id == order.id)
        ).all()
        order_dict["items"] = []
        for item in items:
            product = session.get(Product, item.product_id)
            item_dict = {
                "id": item.id,
                "product_id": item.product_id,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "total_price": item.total_price,
                "status": item.status,
                "is_returnable": item.is_returnable,
                "return_deadline": item.return_deadline.isoformat()
                if item.return_deadline
                else None,
                "return_status": item.return_status,
            }
            if product:
                item_dict["product"] = {
                    "id": product.id,
                    "asin": product.asin,
                    "title": product.title,
                    "images": json.loads(product.images) if product.images else [],
                }
            order_dict["items"].append(item_dict)
        orders_with_items.append(order_dict)

    return {"orders": orders_with_items}


@router.get("/orders/{order_id}")
def get_order(
    order_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    order = session.get(Order, order_id)
    if not order or order.user_id != user_id:
        raise HTTPException(status_code=404, detail="Order not found")

    items = session.exec(select(OrderItem).where(OrderItem.order_id == order_id)).all()
    order_dict = order_to_dict(order)
    order_dict["items"] = []
    for i in items:
        product = session.get(Product, i.product_id)
        item_dict = {
            "id": i.id,
            "product_id": i.product_id,
            "quantity": i.quantity,
            "unit_price": i.unit_price,
            "total_price": i.total_price,
            "status": i.status,
            "tracking_number": i.tracking_number,
            "carrier": i.carrier,
            "is_returnable": i.is_returnable,
            "return_deadline": i.return_deadline.isoformat()
            if i.return_deadline
            else None,
            "return_status": i.return_status,
        }
        if product:
            item_dict["product"] = {
                "id": product.id,
                "asin": product.asin,
                "title": product.title,
                "images": json.loads(product.images) if product.images else [],
            }
        order_dict["items"].append(item_dict)
    return order_dict


@router.get("/orders/{order_id}/tracking")
def get_order_tracking(
    order_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    order = session.get(Order, order_id)
    if not order or order.user_id != user_id:
        raise HTTPException(status_code=404, detail="Order not found")

    items = session.exec(select(OrderItem).where(OrderItem.order_id == order_id)).all()
    return {
        "order_number": order.order_number,
        "status": order.status,
        "tracking": [
            {
                "item_id": i.id,
                "tracking_number": i.tracking_number,
                "carrier": i.carrier,
                "status": i.status,
            }
            for i in items
        ],
    }


@router.post("/orders/{order_id}/cancel")
def cancel_order(
    order_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    order = session.get(Order, order_id)
    if not order or order.user_id != user_id:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status not in ["pending", "processing"]:
        raise HTTPException(status_code=400, detail="Order cannot be cancelled")

    order.status = "cancelled"
    order.cancelled_at = datetime.utcnow()
    session.add(order)
    session.commit()
    return {"message": "Order cancelled"}


@router.post("/orders/{order_id}/items/{item_id}/return")
def initiate_return(
    order_id: int,
    item_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    order = session.get(Order, order_id)
    if not order or order.user_id != user_id:
        raise HTTPException(status_code=404, detail="Order not found")

    item = session.get(OrderItem, item_id)
    if not item or item.order_id != order_id:
        raise HTTPException(status_code=404, detail="Item not found")
    if not item.is_returnable:
        raise HTTPException(status_code=400, detail="Item is not returnable")

    item.return_status = "requested"
    session.add(item)
    session.commit()
    return {"message": "Return initiated"}


@router.post("/orders/{order_id}/archive")
def archive_order(
    order_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    order = session.get(Order, order_id)
    if not order or order.user_id != user_id:
        raise HTTPException(status_code=404, detail="Order not found")
    order.is_archived = True
    session.add(order)
    session.commit()
    return {"message": "Order archived"}


# ============================================================================
# 3.7 Reviews Endpoints
# ============================================================================


@router.get("/reviews")
def get_user_reviews(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    reviews = session.exec(select(Review).where(Review.user_id == user_id)).all()
    return {
        "reviews": [
            {
                "id": r.id,
                "product_id": r.product_id,
                "rating": r.rating,
                "title": r.title,
                "body": r.body,
                "created_at": r.created_at.isoformat(),
            }
            for r in reviews
        ]
    }


@router.post("/products/{product_id}/reviews")
def create_review(
    product_id: int,
    data: ReviewCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    existing = session.exec(
        select(Review).where(Review.product_id == product_id, Review.user_id == user_id)
    ).first()
    if existing:
        raise HTTPException(
            status_code=400, detail="You have already reviewed this product"
        )

    review = Review(
        product_id=product_id,
        user_id=user_id,
        rating=data.rating,
        title=data.title,
        body=data.body,
        images=json.dumps(data.images) if data.images else None,
        videos=json.dumps(data.videos) if data.videos else None,
    )
    session.add(review)

    # Update product rating
    all_reviews = session.exec(
        select(Review).where(Review.product_id == product_id)
    ).all()
    total_rating = sum(r.rating for r in all_reviews) + data.rating
    product.rating = round(total_rating / (len(all_reviews) + 1), 1)
    product.review_count = len(all_reviews) + 1
    session.add(product)

    session.commit()
    session.refresh(review)
    return {"id": review.id, "message": "Review created"}


@router.put("/reviews/{review_id}")
def update_review(
    review_id: int,
    data: ReviewUpdate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    review = session.get(Review, review_id)
    if not review or review.user_id != user_id:
        raise HTTPException(status_code=404, detail="Review not found")

    if data.rating is not None:
        review.rating = data.rating
    if data.title is not None:
        review.title = data.title
    if data.body is not None:
        review.body = data.body
    review.updated_at = datetime.utcnow()

    session.add(review)
    session.commit()
    return {"message": "Review updated"}


@router.delete("/reviews/{review_id}")
def delete_review(
    review_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    review = session.get(Review, review_id)
    if not review or review.user_id != user_id:
        raise HTTPException(status_code=404, detail="Review not found")
    session.delete(review)
    session.commit()
    return {"message": "Review deleted"}


@router.post("/reviews/{review_id}/vote")
def vote_review(
    review_id: int,
    data: ReviewVoteCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    review = session.get(Review, review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")

    existing = session.exec(
        select(ReviewVote).where(
            ReviewVote.review_id == review_id, ReviewVote.user_id == user_id
        )
    ).first()
    if existing:
        existing.is_helpful = data.is_helpful
        session.add(existing)
    else:
        vote = ReviewVote(
            review_id=review_id, user_id=user_id, is_helpful=data.is_helpful
        )
        session.add(vote)

    # Update vote counts
    votes = session.exec(
        select(ReviewVote).where(ReviewVote.review_id == review_id)
    ).all()
    review.helpful_votes = sum(1 for v in votes if v.is_helpful)
    review.total_votes = len(votes)
    session.add(review)

    session.commit()
    return {"message": "Vote recorded"}


# ============================================================================
# 3.8 Questions & Answers Endpoints
# ============================================================================


@router.post("/products/{product_id}/questions")
def create_question(
    product_id: int,
    data: QuestionCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    question = Question(
        product_id=product_id, user_id=user_id, question_text=data.question_text
    )
    session.add(question)
    session.commit()
    session.refresh(question)
    return {"id": question.id, "message": "Question submitted"}


@router.post("/questions/{question_id}/answers")
def create_answer(
    question_id: int,
    data: AnswerCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    question = session.get(Question, question_id)
    if not question:
        raise HTTPException(status_code=404, detail="Question not found")

    answer = Answer(
        question_id=question_id, user_id=user_id, answer_text=data.answer_text
    )
    session.add(answer)
    question.answer_count += 1
    session.add(question)
    session.commit()
    session.refresh(answer)
    return {"id": answer.id, "message": "Answer submitted"}


@router.post("/answers/{answer_id}/vote")
def vote_answer(
    answer_id: int,
    data: ReviewVoteCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    answer = session.get(Answer, answer_id)
    if not answer:
        raise HTTPException(status_code=404, detail="Answer not found")

    if data.is_helpful:
        answer.helpful_votes += 1
    session.add(answer)
    session.commit()
    return {"message": "Vote recorded"}


# ============================================================================
# 3.9 Wishlists Endpoints
# ============================================================================


@router.get("/wishlists")
def get_wishlists(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    wishlists = session.exec(select(Wishlist).where(Wishlist.user_id == user_id)).all()

    result = []
    for w in wishlists:
        items = session.exec(
            select(WishlistItem).where(WishlistItem.wishlist_id == w.id)
        ).all()
        # Get preview images from first 3 products
        preview_images = []
        for item in items[:3]:
            product = session.get(Product, item.product_id)
            if product and product.images:
                images = (
                    json.loads(product.images)
                    if isinstance(product.images, str)
                    else product.images
                )
                if images:
                    preview_images.append(images[0])

        result.append(
            {
                "id": w.id,
                "name": w.name,
                "is_default": w.is_default,
                "is_public": w.is_public,
                "item_count": len(items),
                "preview_images": preview_images,
            }
        )

    return {"wishlists": result}


@router.post("/wishlists")
def create_wishlist(
    data: WishlistCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    wishlist = Wishlist(user_id=user_id, **data.model_dump())
    session.add(wishlist)
    session.commit()
    session.refresh(wishlist)
    return {"id": wishlist.id, "message": "Wishlist created"}


@router.get("/wishlists/{wishlist_id}")
def get_wishlist(
    wishlist_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    wishlist = session.get(Wishlist, wishlist_id)
    if not wishlist or (wishlist.user_id != user_id and not wishlist.is_public):
        raise HTTPException(status_code=404, detail="Wishlist not found")

    items = session.exec(
        select(WishlistItem).where(WishlistItem.wishlist_id == wishlist_id)
    ).all()

    # Build items with product details
    items_with_products = []
    for i in items:
        product = session.get(Product, i.product_id)
        item_data = {
            "id": i.id,
            "product_id": i.product_id,
            "quantity_desired": i.quantity_desired,
            "priority": i.priority,
            "price_when_added": i.price_when_added,
            "added_at": i.added_at.isoformat() if i.added_at else None,
        }
        if product:
            item_data["product"] = product_to_dict(product)
        items_with_products.append(item_data)

    return {
        "id": wishlist.id,
        "name": wishlist.name,
        "is_public": wishlist.is_public,
        "is_default": wishlist.is_default,
        "items": items_with_products,
    }


@router.put("/wishlists/{wishlist_id}")
def update_wishlist(
    wishlist_id: int,
    data: WishlistCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    wishlist = session.get(Wishlist, wishlist_id)
    if not wishlist or wishlist.user_id != user_id:
        raise HTTPException(status_code=404, detail="Wishlist not found")

    for key, value in data.model_dump().items():
        setattr(wishlist, key, value)
    wishlist.updated_at = datetime.utcnow()
    session.add(wishlist)
    session.commit()
    return {"message": "Wishlist updated"}


@router.delete("/wishlists/{wishlist_id}")
def delete_wishlist(
    wishlist_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    wishlist = session.get(Wishlist, wishlist_id)
    if not wishlist or wishlist.user_id != user_id:
        raise HTTPException(status_code=404, detail="Wishlist not found")
    if wishlist.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete default wishlist")

    items = session.exec(
        select(WishlistItem).where(WishlistItem.wishlist_id == wishlist_id)
    ).all()
    for item in items:
        session.delete(item)
    session.delete(wishlist)
    session.commit()
    return {"message": "Wishlist deleted"}


@router.post("/wishlists/{wishlist_id}/items")
def add_wishlist_item(
    wishlist_id: int,
    data: WishlistItemCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    wishlist = session.get(Wishlist, wishlist_id)
    if not wishlist or wishlist.user_id != user_id:
        raise HTTPException(status_code=404, detail="Wishlist not found")

    product = session.get(Product, data.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    item = WishlistItem(
        wishlist_id=wishlist_id, price_when_added=product.price, **data.model_dump()
    )
    session.add(item)
    session.commit()
    return {"message": "Item added to wishlist"}


@router.delete("/wishlists/{wishlist_id}/items/{item_id}")
def remove_wishlist_item(
    wishlist_id: int,
    item_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    wishlist = session.get(Wishlist, wishlist_id)
    if not wishlist or wishlist.user_id != user_id:
        raise HTTPException(status_code=404, detail="Wishlist not found")

    item = session.get(WishlistItem, item_id)
    if not item or item.wishlist_id != wishlist_id:
        raise HTTPException(status_code=404, detail="Item not found")

    session.delete(item)
    session.commit()
    return {"message": "Item removed from wishlist"}


# ============================================================================
# 3.11 Browsing History
# ============================================================================


@router.get("/history")
def get_browsing_history(
    limit: int = Query(50, le=100),
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    history = session.exec(
        select(BrowsingHistory)
        .where(BrowsingHistory.user_id == user_id)
        .order_by(BrowsingHistory.viewed_at.desc())
        .limit(limit)
    ).all()

    product_ids = [h.product_id for h in history]
    products = session.exec(select(Product).where(Product.id.in_(product_ids))).all()
    product_map = {p.id: p for p in products}

    return {
        "history": [
            {
                "id": h.id,
                "product": product_to_dict(product_map[h.product_id])
                if h.product_id in product_map
                else None,
                "viewed_at": h.viewed_at.isoformat(),
            }
            for h in history
        ]
    }


@router.post("/history/{product_id}")
def add_to_history(
    product_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    history = BrowsingHistory(user_id=user_id, product_id=product_id)
    session.add(history)
    session.commit()
    return {"message": "Added to history"}


@router.delete("/history")
def clear_history(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    session.exec(
        select(BrowsingHistory).where(BrowsingHistory.user_id == user_id)
    ).all()
    from sqlmodel import delete

    session.exec(delete(BrowsingHistory).where(BrowsingHistory.user_id == user_id))
    session.commit()
    return {"message": "History cleared"}


@router.delete("/history/{product_id}")
def remove_from_history(
    product_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    history = session.exec(
        select(BrowsingHistory).where(
            BrowsingHistory.user_id == user_id, BrowsingHistory.product_id == product_id
        )
    ).first()
    if history:
        session.delete(history)
        session.commit()
    return {"message": "Removed from history"}


# ============================================================================
# 3.12 Recommendations
# ============================================================================


@router.get("/recommendations")
def get_recommendations(
    limit: int = Query(20, le=50),
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    # Get products from browsing history categories
    history = session.exec(
        select(BrowsingHistory).where(BrowsingHistory.user_id == user_id).limit(10)
    ).all()

    if history:
        product_ids = [h.product_id for h in history]
        products = session.exec(
            select(Product).where(Product.id.in_(product_ids))
        ).all()
        category_ids = list(set(p.category_id for p in products))

        recommended = session.exec(
            select(Product)
            .where(
                Product.category_id.in_(category_ids), Product.id.notin_(product_ids)
            )
            .order_by(Product.rating.desc())
            .limit(limit)
        ).all()
    else:
        recommended = session.exec(
            select(Product).order_by(Product.rating.desc()).limit(limit)
        ).all()

    return {"recommendations": products_to_dict(session, recommended)}


@router.get("/recommendations/deals")
def get_deal_recommendations(
    limit: int = Query(20, le=50), session: Session = Depends(get_session)
):
    deals = session.exec(
        select(Deal)
        .where(Deal.is_active == True, Deal.end_time > datetime.utcnow())
        .order_by(Deal.discount_percentage.desc())
        .limit(limit)
    ).all()

    product_ids = [d.product_id for d in deals]
    products = session.exec(select(Product).where(Product.id.in_(product_ids))).all()
    product_map = {p.id: p for p in products}

    return {
        "deals": [
            {
                "deal_id": d.id,
                "product": product_to_dict(product_map[d.product_id])
                if d.product_id in product_map
                else None,
                "discount_percentage": d.discount_percentage,
                "deal_price": d.deal_price,
                "end_time": d.end_time.isoformat(),
            }
            for d in deals
        ]
    }


@router.get("/recommendations/buy-again")
def get_buy_again_recommendations(
    limit: int = Query(20, le=50),
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    order_items = session.exec(
        select(OrderItem)
        .join(Order)
        .where(Order.user_id == user_id, Order.status == "delivered")
        .order_by(Order.delivered_at.desc())
        .limit(limit)
    ).all()

    product_ids = list(set(oi.product_id for oi in order_items))
    products = session.exec(select(Product).where(Product.id.in_(product_ids))).all()

    return {"products": products_to_dict(session, products)}


@router.get("/recommendations/inspired-by")
def get_inspired_by_history(
    limit: int = Query(20, le=50),
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    recent = session.exec(
        select(RecentlyViewed)
        .where(RecentlyViewed.user_id == user_id)
        .order_by(RecentlyViewed.viewed_at.desc())
        .limit(5)
    ).all()

    if recent:
        product_ids = [r.product_id for r in recent]
        products = session.exec(
            select(Product).where(Product.id.in_(product_ids))
        ).all()
        category_ids = list(set(p.category_id for p in products))

        inspired = session.exec(
            select(Product)
            .where(
                Product.category_id.in_(category_ids), Product.id.notin_(product_ids)
            )
            .order_by(Product.bought_past_month.desc())
            .limit(limit)
        ).all()
    else:
        inspired = session.exec(
            select(Product).order_by(Product.bought_past_month.desc()).limit(limit)
        ).all()

    return {"products": products_to_dict(session, inspired)}


# ============================================================================
# 3.13 Deals
# ============================================================================


@router.get("/deals")
def get_deals(
    department: Optional[str] = None,
    discount_min: Optional[float] = None,
    deal_type: Optional[str] = None,
    prime: bool = False,
    limit: int = Query(50, le=100),
    session: Session = Depends(get_session),
):
    query = select(Deal).where(
        Deal.is_active == True, Deal.end_time > datetime.utcnow()
    )

    if deal_type:
        query = query.where(Deal.deal_type == deal_type)
    if discount_min:
        query = query.where(Deal.discount_percentage >= discount_min)
    if prime:
        query = query.where(Deal.is_prime_exclusive == True)

    deals = session.exec(query.order_by(Deal.end_time.asc()).limit(limit)).all()

    product_ids = [d.product_id for d in deals]
    products = session.exec(select(Product).where(Product.id.in_(product_ids))).all()
    product_map = {p.id: p for p in products}

    return {
        "deals": [
            {
                "id": d.id,
                "deal_type": d.deal_type,
                "product": product_to_dict(product_map[d.product_id])
                if d.product_id in product_map
                else None,
                "discount_percentage": d.discount_percentage,
                "deal_price": d.deal_price,
                "original_price": d.original_price,
                "start_time": d.start_time.isoformat(),
                "end_time": d.end_time.isoformat(),
                "claimed_percentage": d.claimed_percentage,
                "is_prime_exclusive": d.is_prime_exclusive,
            }
            for d in deals
        ]
    }


@router.get("/deals/lightning")
def get_lightning_deals(
    limit: int = Query(20, le=50), session: Session = Depends(get_session)
):
    deals = session.exec(
        select(Deal)
        .where(
            Deal.deal_type == "lightning",
            Deal.is_active == True,
            Deal.end_time > datetime.utcnow(),
        )
        .order_by(Deal.end_time.asc())
        .limit(limit)
    ).all()

    product_ids = [d.product_id for d in deals]
    products = session.exec(select(Product).where(Product.id.in_(product_ids))).all()
    product_map = {p.id: p for p in products}

    return {
        "deals": [
            {
                "id": d.id,
                "product": product_to_dict(product_map[d.product_id])
                if d.product_id in product_map
                else None,
                "discount_percentage": d.discount_percentage,
                "deal_price": d.deal_price,
                "end_time": d.end_time.isoformat(),
                "claimed_percentage": d.claimed_percentage,
            }
            for d in deals
        ]
    }


@router.get("/deals/today")
def get_deal_of_the_day(session: Session = Depends(get_session)):
    deal = session.exec(
        select(Deal)
        .where(
            Deal.deal_type == "deal_of_day",
            Deal.is_active == True,
            Deal.end_time > datetime.utcnow(),
        )
        .order_by(Deal.start_time.desc())
    ).first()

    if not deal:
        return {"deal": None}

    product = session.get(Product, deal.product_id)
    return {
        "deal": {
            "id": deal.id,
            "product": product_to_dict(product) if product else None,
            "discount_percentage": deal.discount_percentage,
            "deal_price": deal.deal_price,
            "original_price": deal.original_price,
            "end_time": deal.end_time.isoformat(),
        }
    }


@router.get("/deals/coupons")
def get_coupons(
    limit: int = Query(50, le=100), session: Session = Depends(get_session)
):
    coupons = session.exec(
        select(Coupon)
        .where(Coupon.is_active == True, Coupon.valid_until > datetime.utcnow())
        .order_by(Coupon.discount_value.desc())
        .limit(limit)
    ).all()

    return {
        "coupons": [
            {
                "id": c.id,
                "code": c.code,
                "description": c.description,
                "discount_type": c.discount_type,
                "discount_value": c.discount_value,
                "min_order_amount": c.min_order_amount,
                "valid_until": c.valid_until.isoformat(),
            }
            for c in coupons
        ]
    }


@router.post("/deals/{deal_id}/claim")
def claim_deal(
    deal_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    deal = session.get(Deal, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")
    if not deal.is_active or deal.end_time < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Deal is no longer available")
    if deal.max_claims and deal.current_claims >= deal.max_claims:
        raise HTTPException(status_code=400, detail="Deal is sold out")

    deal.current_claims += 1
    deal.claimed_percentage = (
        (deal.current_claims / deal.max_claims * 100) if deal.max_claims else 0
    )
    session.add(deal)
    session.commit()

    return {"message": "Deal claimed", "deal_price": deal.deal_price}


@router.post("/deals")
def create_deal(
    data: DealCreate,
    session: Session = Depends(get_session),
):
    product = session.get(Product, data.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    now = datetime.utcnow()
    deal = Deal(
        product_id=data.product_id,
        deal_type=data.deal_type,
        discount_percentage=data.discount_percentage,
        deal_price=data.deal_price,
        original_price=data.original_price,
        start_time=now,
        end_time=now + timedelta(hours=24),
        is_active=True,
        is_prime_exclusive=data.is_prime_exclusive,
    )
    session.add(deal)
    session.commit()
    session.refresh(deal)

    return {"message": "Deal created", "deal_id": deal.id}


@router.delete("/deals/{deal_id}")
def remove_deal(
    deal_id: int,
    session: Session = Depends(get_session),
):
    deal = session.get(Deal, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found")

    deal.is_active = False
    session.add(deal)
    session.commit()

    return {"message": "Deal removed", "deal_id": deal_id}


@router.post("/coupons/{coupon_id}/clip")
def clip_coupon(
    coupon_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    coupon = session.get(Coupon, coupon_id)
    if not coupon:
        raise HTTPException(status_code=404, detail="Coupon not found")
    if not coupon.is_active or coupon.valid_until < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Coupon is no longer valid")

    return {"message": "Coupon clipped", "code": coupon.code}


# ============================================================================
# 3.14 Sellers
# ============================================================================


@router.get("/sellers/{seller_id}")
def get_seller(seller_id: int, session: Session = Depends(get_session)):
    seller = session.get(Seller, seller_id)
    if not seller:
        raise HTTPException(status_code=404, detail="Seller not found")

    return {
        "id": seller.id,
        "name": seller.name,
        "slug": seller.slug,
        "description": seller.description,
        "logo_url": seller.logo_url,
        "rating": seller.rating,
        "rating_count": seller.rating_count,
        "is_amazon": seller.is_amazon,
        "feedback_percentage": seller.feedback_percentage,
        "ships_from": seller.ships_from,
        "return_policy": seller.return_policy,
    }


@router.get("/sellers/{seller_id}/products")
def get_seller_products(
    seller_id: int,
    limit: int = Query(48, le=100),
    page: int = Query(1, ge=1),
    session: Session = Depends(get_session),
):
    seller = session.get(Seller, seller_id)
    if not seller:
        raise HTTPException(status_code=404, detail="Seller not found")

    offset = (page - 1) * limit
    products = session.exec(
        select(Product)
        .where(Product.seller_id == seller_id)
        .order_by(Product.rating.desc())
        .offset(offset)
        .limit(limit)
    ).all()

    total = session.exec(
        select(func.count(Product.id)).where(Product.seller_id == seller_id)
    ).one()

    return {
        "products": products_to_dict(session, products),
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit,
    }


@router.get("/sellers/{seller_id}/reviews")
def get_seller_reviews(
    seller_id: int,
    limit: int = Query(20, le=50),
    session: Session = Depends(get_session),
):
    seller = session.get(Seller, seller_id)
    if not seller:
        raise HTTPException(status_code=404, detail="Seller not found")

    return {
        "seller_id": seller_id,
        "rating": seller.rating,
        "rating_count": seller.rating_count,
        "feedback_percentage": seller.feedback_percentage,
    }


# ============================================================================
# 3.15 Subscriptions (Subscribe & Save)
# ============================================================================


@router.get("/subscriptions")
def get_subscriptions(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    subs = session.exec(
        select(Subscription).where(Subscription.user_id == user_id)
    ).all()

    product_ids = [s.product_id for s in subs]
    products = session.exec(select(Product).where(Product.id.in_(product_ids))).all()
    product_map = {p.id: p for p in products}

    return {
        "subscriptions": [
            {
                "id": s.id,
                "product": product_to_dict(product_map[s.product_id])
                if s.product_id in product_map
                else None,
                "quantity": s.quantity,
                "frequency_months": s.frequency_months,
                "discount_percentage": s.discount_percentage,
                "next_delivery_date": s.next_delivery_date.isoformat(),
                "status": s.status,
            }
            for s in subs
        ]
    }


@router.post("/subscriptions")
def create_subscription(
    data: SubscriptionCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    product = session.get(Product, data.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    sub = Subscription(
        user_id=user_id,
        product_id=data.product_id,
        variant_id=data.variant_id,
        quantity=data.quantity,
        frequency_months=data.frequency_months,
        shipping_address_id=data.shipping_address_id,
        payment_method_id=data.payment_method_id,
        next_delivery_date=date.today() + timedelta(days=data.frequency_months * 30),
    )
    session.add(sub)
    session.commit()
    session.refresh(sub)

    return {
        "id": sub.id,
        "message": "Subscription created",
        "next_delivery_date": sub.next_delivery_date.isoformat(),
    }


@router.put("/subscriptions/{sub_id}")
def update_subscription(
    sub_id: int,
    quantity: Optional[int] = None,
    frequency_months: Optional[int] = None,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    sub = session.get(Subscription, sub_id)
    if not sub or sub.user_id != user_id:
        raise HTTPException(status_code=404, detail="Subscription not found")

    if quantity is not None:
        sub.quantity = quantity
    if frequency_months is not None:
        sub.frequency_months = frequency_months

    session.add(sub)
    session.commit()
    return {"message": "Subscription updated"}


@router.post("/subscriptions/{sub_id}/skip")
def skip_subscription_delivery(
    sub_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    sub = session.get(Subscription, sub_id)
    if not sub or sub.user_id != user_id:
        raise HTTPException(status_code=404, detail="Subscription not found")

    sub.next_delivery_date = sub.next_delivery_date + timedelta(
        days=sub.frequency_months * 30
    )
    session.add(sub)
    session.commit()
    return {
        "message": "Next delivery skipped",
        "new_delivery_date": sub.next_delivery_date.isoformat(),
    }


@router.delete("/subscriptions/{sub_id}")
def cancel_subscription(
    sub_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    sub = session.get(Subscription, sub_id)
    if not sub or sub.user_id != user_id:
        raise HTTPException(status_code=404, detail="Subscription not found")

    sub.status = "cancelled"
    session.add(sub)
    session.commit()
    return {"message": "Subscription cancelled"}


# ============================================================================
# 3.16 Price Watch
# ============================================================================


@router.get("/price-watch")
def get_price_watch(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    watches = session.exec(
        select(PriceWatch).where(PriceWatch.user_id == user_id)
    ).all()

    product_ids = [w.product_id for w in watches]
    products = session.exec(select(Product).where(Product.id.in_(product_ids))).all()
    product_map = {p.id: p for p in products}

    return {
        "watches": [
            {
                "id": w.id,
                "product": product_to_dict(product_map[w.product_id])
                if w.product_id in product_map
                else None,
                "target_price": w.target_price,
                "created_at": w.created_at.isoformat(),
            }
            for w in watches
        ]
    }


@router.post("/price-watch")
def add_price_watch(
    data: PriceWatchCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    product = session.get(Product, data.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    existing = session.exec(
        select(PriceWatch).where(
            PriceWatch.user_id == user_id, PriceWatch.product_id == data.product_id
        )
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Already watching this product")

    watch = PriceWatch(
        user_id=user_id, product_id=data.product_id, target_price=data.target_price
    )
    session.add(watch)
    session.commit()
    session.refresh(watch)

    return {"id": watch.id, "message": "Added to price watch"}


@router.delete("/price-watch/{watch_id}")
def remove_price_watch(
    watch_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    watch = session.get(PriceWatch, watch_id)
    if not watch or watch.user_id != user_id:
        raise HTTPException(status_code=404, detail="Watch not found")

    session.delete(watch)
    session.commit()
    return {"message": "Removed from price watch"}


# ============================================================================
# 3.19 Buy Again
# ============================================================================


@router.get("/buy-again")
def get_buy_again(
    limit: int = Query(50, le=100),
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    order_items = session.exec(
        select(OrderItem)
        .join(Order)
        .where(Order.user_id == user_id, Order.status == "delivered")
        .order_by(Order.delivered_at.desc())
    ).all()

    product_ids = list(set(oi.product_id for oi in order_items))[:limit]
    products = session.exec(select(Product).where(Product.id.in_(product_ids))).all()

    return {"products": products_to_dict(session, products)}


@router.get("/buy-again/subscribe-eligible")
def get_subscribe_eligible(
    limit: int = Query(20, le=50),
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    order_items = session.exec(
        select(OrderItem)
        .join(Order)
        .where(Order.user_id == user_id, Order.status == "delivered")
    ).all()

    product_ids = list(set(oi.product_id for oi in order_items))
    # For simplicity, all products are subscribe-eligible in this mock
    products = session.exec(
        select(Product).where(Product.id.in_(product_ids)).limit(limit)
    ).all()

    return {"products": products_to_dict(session, products)}


# ============================================================================
# 3.20 Gift Cards
# ============================================================================


@router.get("/gift-cards")
def get_gift_cards(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    cards = session.exec(select(GiftCard).where(GiftCard.user_id == user_id)).all()

    return {
        "gift_cards": [
            {
                "id": c.id,
                "code": c.code[-4:].rjust(len(c.code), "*"),  # Mask code
                "original_amount": c.original_amount,
                "current_balance": c.current_balance,
                "redeemed_at": c.redeemed_at.isoformat() if c.redeemed_at else None,
            }
            for c in cards
        ]
    }


@router.get("/gift-cards/balance")
def get_gift_card_balance(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    cards = session.exec(select(GiftCard).where(GiftCard.user_id == user_id)).all()
    total_balance = sum(c.current_balance for c in cards)

    return {"balance": total_balance, "currency": "USD"}


@router.post("/gift-cards/redeem")
def redeem_gift_card(
    data: GiftCardRedeem,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)

    card = session.exec(select(GiftCard).where(GiftCard.code == data.code)).first()
    if not card:
        raise HTTPException(status_code=404, detail="Invalid gift card code")
    if card.user_id:
        raise HTTPException(status_code=400, detail="Gift card already redeemed")

    card.user_id = user_id
    card.redeemed_at = datetime.utcnow()
    session.add(card)

    transaction = GiftCardTransaction(
        user_id=user_id,
        amount=card.current_balance,
        type="redemption",
        balance_after=card.current_balance,
    )
    session.add(transaction)
    session.commit()

    return {"message": "Gift card redeemed", "amount": card.current_balance}


@router.post("/gift-cards/reload")
def reload_gift_card(
    data: GiftCardReload,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)

    cards = session.exec(select(GiftCard).where(GiftCard.user_id == user_id)).all()
    if not cards:
        # Create a new gift card
        card = GiftCard(
            user_id=user_id,
            code="".join(random.choices(string.ascii_uppercase + string.digits, k=16)),
            original_amount=data.amount,
            current_balance=data.amount,
            redeemed_at=datetime.utcnow(),
        )
        session.add(card)
    else:
        card = cards[0]
        card.current_balance += data.amount
        session.add(card)

    transaction = GiftCardTransaction(
        user_id=user_id,
        amount=data.amount,
        type="reload",
        balance_after=card.current_balance,
    )
    session.add(transaction)
    session.commit()

    return {"message": "Gift card reloaded", "new_balance": card.current_balance}


@router.get("/gift-cards/transactions")
def get_gift_card_transactions(
    limit: int = Query(50, le=100),
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    transactions = session.exec(
        select(GiftCardTransaction)
        .where(GiftCardTransaction.user_id == user_id)
        .order_by(GiftCardTransaction.created_at.desc())
        .limit(limit)
    ).all()

    return {
        "transactions": [
            {
                "id": t.id,
                "amount": t.amount,
                "type": t.type,
                "balance_after": t.balance_after,
                "created_at": t.created_at.isoformat(),
            }
            for t in transactions
        ]
    }


# ============================================================================
# 3.20 Alexa Shopping List
# ============================================================================


@router.get("/alexa-list")
def get_alexa_list(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    items = session.exec(
        select(AlexaShoppingList)
        .where(AlexaShoppingList.user_id == user_id)
        .order_by(AlexaShoppingList.created_at.desc())
    ).all()

    return {
        "items": [
            {
                "id": i.id,
                "item_name": i.item_name,
                "quantity": i.quantity,
                "is_completed": i.is_completed,
                "added_via": i.added_via,
                "created_at": i.created_at.isoformat(),
            }
            for i in items
        ]
    }


@router.post("/alexa-list")
def add_to_alexa_list(
    data: AlexaListItemCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)

    item = AlexaShoppingList(
        user_id=user_id,
        item_name=data.item_name,
        quantity=data.quantity,
        added_via="web",
    )
    session.add(item)
    session.commit()
    session.refresh(item)

    return {"id": item.id, "message": "Item added to list"}


@router.put("/alexa-list/{item_id}")
def update_alexa_list_item(
    item_id: int,
    item_name: Optional[str] = None,
    quantity: Optional[int] = None,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    item = session.get(AlexaShoppingList, item_id)
    if not item or item.user_id != user_id:
        raise HTTPException(status_code=404, detail="Item not found")

    if item_name:
        item.item_name = item_name
    if quantity:
        item.quantity = quantity

    session.add(item)
    session.commit()
    return {"message": "Item updated"}


@router.delete("/alexa-list/{item_id}")
def remove_from_alexa_list(
    item_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    item = session.get(AlexaShoppingList, item_id)
    if not item or item.user_id != user_id:
        raise HTTPException(status_code=404, detail="Item not found")

    session.delete(item)
    session.commit()
    return {"message": "Item removed"}


@router.post("/alexa-list/{item_id}/complete")
def complete_alexa_list_item(
    item_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    item = session.get(AlexaShoppingList, item_id)
    if not item or item.user_id != user_id:
        raise HTTPException(status_code=404, detail="Item not found")

    item.is_completed = True
    item.completed_at = datetime.utcnow()
    session.add(item)
    session.commit()
    return {"message": "Item marked as complete"}


@router.post("/alexa-list/{item_id}/add-to-cart")
def add_alexa_item_to_cart(
    item_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    item = session.get(AlexaShoppingList, item_id)
    if not item or item.user_id != user_id:
        raise HTTPException(status_code=404, detail="Item not found")

    # Search for a matching product
    product = session.exec(
        select(Product).where(Product.title.contains(item.item_name))
    ).first()

    if not product:
        raise HTTPException(status_code=404, detail="No matching product found")

    # Get or create cart
    cart = session.exec(select(Cart).where(Cart.user_id == user_id)).first()
    if not cart:
        cart = Cart(user_id=user_id)
        session.add(cart)
        session.commit()
        session.refresh(cart)

    cart_item = CartItem(cart_id=cart.id, product_id=product.id, quantity=item.quantity)
    session.add(cart_item)
    session.commit()

    return {"message": "Added to cart", "product_id": product.id}


# ============================================================================
# 3.21 Messages
# ============================================================================


@router.get("/messages")
def get_messages(
    limit: int = Query(50, le=100),
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    messages = session.exec(
        select(Message)
        .where(Message.user_id == user_id)
        .order_by(Message.created_at.desc())
        .limit(limit)
    ).all()

    return {
        "messages": [
            {
                "id": m.id,
                "sender_type": m.sender_type,
                "subject": m.subject,
                "body": m.body[:100] + "..." if len(m.body) > 100 else m.body,
                "related_order_id": m.related_order_id,
                "is_read": m.is_read,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ]
    }


@router.get("/messages/{message_id}")
def get_message_details(
    message_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    message = session.get(Message, message_id)
    if not message or message.user_id != user_id:
        raise HTTPException(status_code=404, detail="Message not found")

    return {
        "id": message.id,
        "sender_type": message.sender_type,
        "subject": message.subject,
        "body": message.body,
        "related_order_id": message.related_order_id,
        "is_read": message.is_read,
        "created_at": message.created_at.isoformat(),
    }


@router.put("/messages/{message_id}/read")
def mark_message_read(
    message_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    message = session.get(Message, message_id)
    if not message or message.user_id != user_id:
        raise HTTPException(status_code=404, detail="Message not found")

    message.is_read = True
    session.add(message)
    session.commit()
    return {"message": "Marked as read"}


@router.delete("/messages/{message_id}")
def delete_message(
    message_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    message = session.get(Message, message_id)
    if not message or message.user_id != user_id:
        raise HTTPException(status_code=404, detail="Message not found")

    session.delete(message)
    session.commit()
    return {"message": "Message deleted"}


@router.post("/messages/reply")
def reply_to_message(
    data: MessageReply,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)

    message = Message(
        user_id=user_id,
        sender_type="customer",
        subject=data.subject,
        body=data.body,
        related_order_id=data.related_order_id,
    )
    session.add(message)
    session.commit()
    session.refresh(message)

    return {"id": message.id, "message": "Reply sent"}


# ============================================================================
# 3.29 Recalls & Safety
# ============================================================================


@router.get("/recalls")
def get_recalls(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    alerts = session.exec(
        select(UserRecallAlert).where(UserRecallAlert.user_id == user_id)
    ).all()

    recall_ids = [a.recall_id for a in alerts]
    recalls = session.exec(
        select(ProductRecall).where(ProductRecall.id.in_(recall_ids))
    ).all()
    recall_map = {r.id: r for r in recalls}

    return {
        "recalls": [
            {
                "alert_id": a.id,
                "recall": {
                    "id": recall_map[a.recall_id].id,
                    "title": recall_map[a.recall_id].title,
                    "description": recall_map[a.recall_id].description,
                    "severity": recall_map[a.recall_id].severity,
                    "action_required": recall_map[a.recall_id].action_required,
                    "recall_date": recall_map[a.recall_id].recall_date.isoformat(),
                }
                if a.recall_id in recall_map
                else None,
                "is_acknowledged": a.is_acknowledged,
                "notified_at": a.notified_at.isoformat(),
            }
            for a in alerts
        ]
    }


@router.get("/recalls/{alert_id}")
def get_recall_details(
    alert_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    alert = session.get(UserRecallAlert, alert_id)
    if not alert or alert.user_id != user_id:
        raise HTTPException(status_code=404, detail="Recall alert not found")

    recall = session.get(ProductRecall, alert.recall_id)
    product = session.get(Product, recall.product_id) if recall else None

    return {
        "alert_id": alert.id,
        "recall": {
            "id": recall.id,
            "title": recall.title,
            "description": recall.description,
            "severity": recall.severity,
            "action_required": recall.action_required,
            "recall_date": recall.recall_date.isoformat(),
            "product": product_to_dict(product) if product else None,
        }
        if recall
        else None,
        "is_acknowledged": alert.is_acknowledged,
        "notified_at": alert.notified_at.isoformat(),
    }


@router.post("/recalls/{alert_id}/acknowledge")
def acknowledge_recall(
    alert_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    alert = session.get(UserRecallAlert, alert_id)
    if not alert or alert.user_id != user_id:
        raise HTTPException(status_code=404, detail="Recall alert not found")

    alert.is_acknowledged = True
    session.add(alert)
    session.commit()
    return {"message": "Recall acknowledged"}


# ============================================================================
# 3.30 Shopping Preferences
# ============================================================================


@router.get("/preferences")
def get_preferences(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    prefs = session.exec(
        select(ShoppingPreference).where(ShoppingPreference.user_id == user_id)
    ).first()

    if not prefs:
        prefs = ShoppingPreference(user_id=user_id)
        session.add(prefs)
        session.commit()
        session.refresh(prefs)

    return {
        "language": prefs.language,
        "currency": prefs.currency,
        "country": prefs.country,
        "personalized_ads": prefs.personalized_ads,
        "browsing_history_enabled": prefs.browsing_history_enabled,
        "recommendations_enabled": prefs.recommendations_enabled,
        "email_preferences": json.loads(prefs.email_preferences)
        if prefs.email_preferences and prefs.email_preferences != "{}"
        else {},
    }


@router.put("/preferences")
def update_preferences(
    data: PreferencesUpdate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    prefs = session.exec(
        select(ShoppingPreference).where(ShoppingPreference.user_id == user_id)
    ).first()

    if not prefs:
        prefs = ShoppingPreference(user_id=user_id)

    if data.language is not None:
        prefs.language = data.language
    if data.currency is not None:
        prefs.currency = data.currency
    if data.country is not None:
        prefs.country = data.country
    if data.personalized_ads is not None:
        prefs.personalized_ads = data.personalized_ads
    if data.browsing_history_enabled is not None:
        prefs.browsing_history_enabled = data.browsing_history_enabled
    if data.recommendations_enabled is not None:
        prefs.recommendations_enabled = data.recommendations_enabled

    prefs.updated_at = datetime.utcnow()
    session.add(prefs)
    session.commit()
    return {"message": "Preferences updated"}


@router.put("/preferences/personalization")
def toggle_personalization(
    personalized_ads: Optional[bool] = None,
    browsing_history: Optional[bool] = None,
    recommendations: Optional[bool] = None,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    prefs = session.exec(
        select(ShoppingPreference).where(ShoppingPreference.user_id == user_id)
    ).first()

    if not prefs:
        prefs = ShoppingPreference(user_id=user_id)

    if personalized_ads is not None:
        prefs.personalized_ads = personalized_ads
    if browsing_history is not None:
        prefs.browsing_history_enabled = browsing_history
    if recommendations is not None:
        prefs.recommendations_enabled = recommendations

    prefs.updated_at = datetime.utcnow()
    session.add(prefs)
    session.commit()
    return {"message": "Personalization settings updated"}


# ============================================================================
# 3.31 Amazon Credit Cards
# ============================================================================


@router.get("/credit-cards")
def get_credit_cards(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    cards = session.exec(
        select(AmazonCreditCard).where(AmazonCreditCard.user_id == user_id)
    ).all()

    return {
        "cards": [
            {
                "id": c.id,
                "card_type": c.card_type,
                "card_number_last4": c.card_number_last4,
                "rewards_balance": c.rewards_balance,
                "cashback_rate": c.cashback_rate,
                "is_primary": c.is_primary,
                "opened_at": c.opened_at.isoformat(),
            }
            for c in cards
        ]
    }


@router.get("/credit-cards/{card_id}/rewards")
def get_card_rewards(
    card_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    card = session.get(AmazonCreditCard, card_id)
    if not card or card.user_id != user_id:
        raise HTTPException(status_code=404, detail="Card not found")

    return {
        "card_id": card.id,
        "rewards_balance": card.rewards_balance,
        "cashback_rate": card.cashback_rate,
    }


@router.get("/credit-cards/{card_id}/transactions")
def get_card_transactions(
    card_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    card = session.get(AmazonCreditCard, card_id)
    if not card or card.user_id != user_id:
        raise HTTPException(status_code=404, detail="Card not found")

    # Mock transactions - in real app would come from a transactions table
    return {"card_id": card.id, "transactions": []}


# ============================================================================
# 3.32 Music Library
# ============================================================================


@router.get("/music/library")
def get_music_library(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    tracks = session.exec(
        select(MusicLibrary).where(MusicLibrary.user_id == user_id)
    ).all()

    return {
        "tracks": [
            {
                "id": t.id,
                "track_id": t.track_id,
                "title": t.title,
                "artist": t.artist,
                "album": t.album,
                "album_art_url": t.album_art_url,
                "duration_seconds": t.duration_seconds,
                "is_purchased": t.is_purchased,
                "is_uploaded": t.is_uploaded,
                "added_at": t.added_at.isoformat(),
            }
            for t in tracks
        ]
    }


@router.get("/music/purchases")
def get_music_purchases(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    tracks = session.exec(
        select(MusicLibrary).where(
            MusicLibrary.user_id == user_id, MusicLibrary.is_purchased == True
        )
    ).all()

    return {
        "tracks": [
            {
                "id": t.id,
                "track_id": t.track_id,
                "title": t.title,
                "artist": t.artist,
                "album": t.album,
                "album_art_url": t.album_art_url,
                "duration_seconds": t.duration_seconds,
                "added_at": t.added_at.isoformat(),
            }
            for t in tracks
        ]
    }


@router.get("/music/uploads")
def get_music_uploads(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    tracks = session.exec(
        select(MusicLibrary).where(
            MusicLibrary.user_id == user_id, MusicLibrary.is_uploaded == True
        )
    ).all()

    return {
        "tracks": [
            {
                "id": t.id,
                "track_id": t.track_id,
                "title": t.title,
                "artist": t.artist,
                "album": t.album,
                "duration_seconds": t.duration_seconds,
                "added_at": t.added_at.isoformat(),
            }
            for t in tracks
        ]
    }


@router.post("/music/uploads")
def upload_music(
    title: str,
    artist: str,
    album: Optional[str] = None,
    duration_seconds: int = 180,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)

    track = MusicLibrary(
        user_id=user_id,
        track_id="".join(random.choices(string.ascii_uppercase + string.digits, k=12)),
        title=title,
        artist=artist,
        album=album,
        duration_seconds=duration_seconds,
        is_uploaded=True,
    )
    session.add(track)
    session.commit()
    session.refresh(track)

    return {"id": track.id, "message": "Track uploaded"}


@router.delete("/music/uploads/{track_id}")
def delete_uploaded_music(
    track_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    track = session.get(MusicLibrary, track_id)
    if not track or track.user_id != user_id:
        raise HTTPException(status_code=404, detail="Track not found")
    if not track.is_uploaded:
        raise HTTPException(status_code=400, detail="Cannot delete purchased music")

    session.delete(track)
    session.commit()
    return {"message": "Track deleted"}


# ============================================================================
# 3.33 Registries
# ============================================================================


@router.get("/registries")
def get_registries(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)
    registries = session.exec(select(Registry).where(Registry.user_id == user_id)).all()

    return {
        "registries": [
            {
                "id": r.id,
                "type": r.type,
                "name": r.name,
                "event_date": r.event_date.isoformat() if r.event_date else None,
                "is_public": r.is_public,
                "created_at": r.created_at.isoformat(),
            }
            for r in registries
        ]
    }


@router.post("/registries")
def create_registry(
    data: RegistryCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)

    registry = Registry(
        user_id=user_id,
        type=data.type,
        name=data.name,
        event_date=data.event_date,
        is_public=data.is_public,
        shipping_address_id=data.shipping_address_id,
    )
    session.add(registry)
    session.commit()
    session.refresh(registry)

    return {"id": registry.id, "message": "Registry created"}


@router.get("/registries/search")
def search_registries(
    name: Optional[str] = Query(None, min_length=2),
    type: Optional[str] = None,
    session: Session = Depends(get_session),
):
    query = select(Registry).where(Registry.is_public == True)
    if name:
        query = query.where(Registry.name.contains(name))
    if type:
        query = query.where(Registry.type == type)

    registries = session.exec(query.limit(20)).all()

    return {
        "registries": [
            {
                "id": r.id,
                "type": r.type,
                "name": r.name,
                "event_date": r.event_date.isoformat() if r.event_date else None,
            }
            for r in registries
        ]
    }


@router.get("/registries/{registry_id}")
def get_registry_details(
    registry_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    registry = session.get(Registry, registry_id)
    if not registry:
        raise HTTPException(status_code=404, detail="Registry not found")
    if not registry.is_public and registry.user_id != user_id:
        raise HTTPException(status_code=403, detail="Registry is private")

    items = session.exec(
        select(RegistryItem).where(RegistryItem.registry_id == registry_id)
    ).all()
    product_ids = [i.product_id for i in items]
    products = session.exec(select(Product).where(Product.id.in_(product_ids))).all()
    product_map = {p.id: p for p in products}

    return {
        "id": registry.id,
        "user_id": registry.user_id,
        "type": registry.type,
        "name": registry.name,
        "event_date": registry.event_date.isoformat() if registry.event_date else None,
        "is_public": registry.is_public,
        "items": [
            {
                "id": i.id,
                "product_id": i.product_id,
                "product": product_to_dict(product_map[i.product_id])
                if i.product_id in product_map
                else None,
                "quantity_desired": i.quantity_desired,
                "quantity_purchased": i.quantity_purchased,
                "priority": i.priority,
                "added_at": i.added_at.isoformat() if i.added_at else None,
            }
            for i in items
        ],
    }


@router.put("/registries/{registry_id}")
def update_registry(
    registry_id: int,
    data: RegistryUpdate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    registry = session.get(Registry, registry_id)
    if not registry or registry.user_id != user_id:
        raise HTTPException(status_code=404, detail="Registry not found")

    if data.name is not None:
        registry.name = data.name
    if data.event_date is not None:
        registry.event_date = data.event_date
    if data.is_public is not None:
        registry.is_public = data.is_public

    session.add(registry)
    session.commit()
    return {"message": "Registry updated"}


@router.delete("/registries/{registry_id}")
def delete_registry(
    registry_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    registry = session.get(Registry, registry_id)
    if not registry or registry.user_id != user_id:
        raise HTTPException(status_code=404, detail="Registry not found")

    # Delete items first
    items = session.exec(
        select(RegistryItem).where(RegistryItem.registry_id == registry_id)
    ).all()
    for item in items:
        session.delete(item)

    session.delete(registry)
    session.commit()
    return {"message": "Registry deleted"}


@router.post("/registries/{registry_id}/items")
def add_registry_item(
    registry_id: int,
    data: RegistryItemCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    registry = session.get(Registry, registry_id)
    if not registry or registry.user_id != user_id:
        raise HTTPException(status_code=404, detail="Registry not found")

    product = session.get(Product, data.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    item = RegistryItem(
        registry_id=registry_id,
        product_id=data.product_id,
        quantity_desired=data.quantity_desired,
        priority=data.priority,
    )
    session.add(item)
    session.commit()
    session.refresh(item)

    return {"id": item.id, "message": "Item added to registry"}


@router.delete("/registries/{registry_id}/items/{item_id}")
def remove_registry_item(
    registry_id: int,
    item_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)
    registry = session.get(Registry, registry_id)
    if not registry or registry.user_id != user_id:
        raise HTTPException(status_code=404, detail="Registry not found")

    item = session.get(RegistryItem, item_id)
    if not item or item.registry_id != registry_id:
        raise HTTPException(status_code=404, detail="Item not found")

    session.delete(item)
    session.commit()
    return {"message": "Item removed from registry"}


# ============================================================================
# 3.34 Business Account
# ============================================================================


@router.get("/business")
def get_business_account(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)

    # Check if user is part of a business account
    membership = session.exec(
        select(BusinessAccountUser).where(BusinessAccountUser.user_id == user_id)
    ).first()
    if not membership:
        return {"business_account": None}

    account = session.get(BusinessAccount, membership.business_account_id)

    return {
        "business_account": {
            "id": account.id,
            "business_name": account.business_name,
            "business_type": account.business_type,
            "is_business_prime": account.is_business_prime,
            "user_role": membership.role,
            "spending_limit": membership.spending_limit,
        }
        if account
        else None
    }


@router.post("/business")
def create_business_account(
    data: BusinessAccountCreate,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)

    # Check if user already has a business account
    existing = session.exec(
        select(BusinessAccountUser).where(BusinessAccountUser.user_id == user_id)
    ).first()
    if existing:
        raise HTTPException(
            status_code=400, detail="Already part of a business account"
        )

    account = BusinessAccount(
        user_id=user_id,
        business_name=data.business_name,
        business_type=data.business_type,
        tax_id=data.tax_id,
    )
    session.add(account)
    session.commit()
    session.refresh(account)

    # Add creator as admin
    membership = BusinessAccountUser(
        business_account_id=account.id, user_id=user_id, role="admin"
    )
    session.add(membership)
    session.commit()

    return {"id": account.id, "message": "Business account created"}


@router.put("/business")
def update_business_account(
    business_name: Optional[str] = None,
    business_type: Optional[str] = None,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)

    membership = session.exec(
        select(BusinessAccountUser).where(
            BusinessAccountUser.user_id == user_id, BusinessAccountUser.role == "admin"
        )
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Not authorized")

    account = session.get(BusinessAccount, membership.business_account_id)
    if business_name:
        account.business_name = business_name
    if business_type:
        account.business_type = business_type

    session.add(account)
    session.commit()
    return {"message": "Business account updated"}


@router.get("/business/users")
def get_business_users(
    session: Session = Depends(get_session), session_token: Optional[str] = Cookie(None)
):
    user_id = get_current_user_id(session_token)

    membership = session.exec(
        select(BusinessAccountUser).where(BusinessAccountUser.user_id == user_id)
    ).first()
    if not membership:
        raise HTTPException(status_code=404, detail="Not part of a business account")

    users = session.exec(
        select(BusinessAccountUser).where(
            BusinessAccountUser.business_account_id == membership.business_account_id
        )
    ).all()

    user_ids = [u.user_id for u in users]
    user_data = session.exec(select(User).where(User.id.in_(user_ids))).all()
    user_map = {u.id: u for u in user_data}

    return {
        "users": [
            {
                "id": u.id,
                "user": {
                    "id": user_map[u.user_id].id,
                    "name": user_map[u.user_id].name,
                    "email": user_map[u.user_id].email,
                }
                if u.user_id in user_map
                else None,
                "role": u.role,
                "spending_limit": u.spending_limit,
                "added_at": u.added_at.isoformat(),
            }
            for u in users
        ]
    }


@router.post("/business/users")
def add_business_user(
    data: BusinessUserAdd,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)

    membership = session.exec(
        select(BusinessAccountUser).where(
            BusinessAccountUser.user_id == user_id, BusinessAccountUser.role == "admin"
        )
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Check if user exists
    new_user = session.get(User, data.user_id)
    if not new_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already in account
    existing = session.exec(
        select(BusinessAccountUser).where(
            BusinessAccountUser.business_account_id == membership.business_account_id,
            BusinessAccountUser.user_id == data.user_id,
        )
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="User already in business account")

    new_membership = BusinessAccountUser(
        business_account_id=membership.business_account_id,
        user_id=data.user_id,
        role=data.role,
        spending_limit=data.spending_limit,
    )
    session.add(new_membership)
    session.commit()

    return {"message": "User added to business account"}


@router.put("/business/users/{member_id}")
def update_business_user(
    member_id: int,
    role: Optional[str] = None,
    spending_limit: Optional[float] = None,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)

    membership = session.exec(
        select(BusinessAccountUser).where(
            BusinessAccountUser.user_id == user_id, BusinessAccountUser.role == "admin"
        )
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Not authorized")

    target = session.get(BusinessAccountUser, member_id)
    if not target or target.business_account_id != membership.business_account_id:
        raise HTTPException(status_code=404, detail="User not found")

    if role:
        target.role = role
    if spending_limit is not None:
        target.spending_limit = spending_limit

    session.add(target)
    session.commit()
    return {"message": "User updated"}


@router.delete("/business/users/{member_id}")
def remove_business_user(
    member_id: int,
    session: Session = Depends(get_session),
    session_token: Optional[str] = Cookie(None),
):
    user_id = get_current_user_id(session_token)

    membership = session.exec(
        select(BusinessAccountUser).where(
            BusinessAccountUser.user_id == user_id, BusinessAccountUser.role == "admin"
        )
    ).first()
    if not membership:
        raise HTTPException(status_code=403, detail="Not authorized")

    target = session.get(BusinessAccountUser, member_id)
    if not target or target.business_account_id != membership.business_account_id:
        raise HTTPException(status_code=404, detail="User not found")

    if target.user_id == user_id:
        raise HTTPException(status_code=400, detail="Cannot remove yourself")

    session.delete(target)
    session.commit()
    return {"message": "User removed from business account"}
