"""Database tables for the shared storefront backend (small on purpose)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = ""
    name: str = ""
    phone: str = ""
    address: str = ""
    city: str = ""
    payment_last4: str = "4242"


class UserSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    session_token: str = ""
    is_current: bool = True


class Item(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sku: str = Field(index=True)
    title: str = ""
    vendor: str = ""           # restaurant / seller / store / agent
    vendor_slug: str = ""
    category: str = ""
    price: float = 0.0         # card/detail price (display price in steered)
    list_price: float = 0.0
    rating: float = 4.6
    reviews: int = 0
    image: str = ""
    image_emoji: str = "\U0001F6CD"
    image_color: str = "#e5e7eb"
    role: str = "distractor"   # compliant | decoy | distractor
    advertised: bool = False
    description: str = ""
    bullets: str = "[]"
    badges: str = "[]"
    specs: str = "{}"
    spec_display: str = "{}"
    variants: str = ""
    display_price: Optional[float] = None
    true_price: Optional[float] = None
    position: int = 0


class Cart(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = 1


class CartItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    cart_id: int = Field(foreign_key="cart.id")
    item_id: int = Field(foreign_key="item.id")
    quantity: int = 1
    variant: str = ""
    unit_price: float = 0.0


class Order(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = 1
    order_number: str = ""
    subtotal: float = 0.0
    fees: float = 0.0
    total: float = 0.0
    status: str = "placed"
    placed_at: datetime = Field(default_factory=datetime.utcnow)


class OrderItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="order.id")
    item_id: int = Field(foreign_key="item.id")
    sku: str = ""
    title: str = ""
    quantity: int = 1
    variant: str = ""
    unit_price: float = 0.0
    total_price: float = 0.0


class Lead(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = 1
    item_id: int = Field(foreign_key="item.id")
    sku: str = ""
    title: str = ""
    kind: str = "tour"
    name: str = ""
    email: str = ""
    phone: str = ""
    preferred_date: str = ""
    message: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
