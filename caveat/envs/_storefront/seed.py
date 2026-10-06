# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""Seed a fresh DB from the catalog the adapter wrote: one auto-logged-in user, an
empty cart, and the catalog items (card price baked in). No sample orders/leads, so
the agent's transaction is the only one read back. ``reset_database`` re-seeds on a
real page load so every run starts clean.
"""

from __future__ import annotations

import json

from sqlmodel import Session, SQLModel

from caveat.envs._storefront import steering
from caveat.envs._storefront.database import get_engine, init_db
from caveat.envs._storefront.models import Cart, Item, User, UserSession

_USER = dict(id=1, email="alex.rivera@example.com", name="Alex Rivera",
             phone="+1-555-200-4242", address="742 Evergreen Terrace",
             city="San Francisco, CA 94102", payment_last4="4242")


def _seed(session: Session) -> None:
    session.add(User(**_USER))
    session.add(UserSession(user_id=1, session_token="storefront_session_token", is_current=True))
    session.add(Cart(id=1, user_id=1))
    session.commit()
    for pos, it in enumerate(steering.items()):
        sku = it["sku"]
        session.add(Item(
            sku=sku, title=it["title"], vendor=it.get("vendor", ""),
            vendor_slug=it.get("vendor_slug", ""), category=it.get("category", ""),
            price=steering.card_price(sku),
            list_price=float(it.get("list_price") or it.get("price", 0.0)),
            rating=float(it.get("rating", 4.6)), reviews=int(it.get("reviews", 0)),
            image=it.get("image", ""), image_emoji=it.get("image_emoji", "\U0001F6CD"),
            image_color=it.get("image_color", "#eef0f3"),
            role=it.get("role", "distractor"), advertised=bool(it.get("advertised")),
            description=it.get("description", ""), bullets=json.dumps(it.get("bullets", [])),
            badges=json.dumps(it.get("badges", [])), specs=json.dumps(it.get("specs", {})),
            spec_display=json.dumps(it.get("spec_display", {})),
            variants=json.dumps(it["variants"]) if it.get("variants") else "",
            display_price=it.get("display_price"), true_price=it.get("true_price"), position=pos,
        ))
    # The optional pre-commit service is a transaction line, never a shopper
    # product.  Keep it in the DB so a selected option is persisted and scored,
    # while every list/search route excludes role=addon and the authored catalog
    # remains exactly 74 shopper-visible items.
    option = steering.checkout_option()
    if option and option.get("removable", True):
        session.add(Item(
            sku="SF-ADDON", title=str(option.get("label") or "Order protection"),
            vendor="Storefront services", category="service",
            price=float(option.get("price") or 0.0), list_price=float(option.get("price") or 0.0),
            rating=0.0, reviews=0, role="addon", advertised=False,
            description="Optional service selected during order review.", position=len(steering.items()),
        ))
    session.commit()


def seed_database() -> None:
    init_db()
    with Session(get_engine()) as session:
        if session.get(User, 1):
            return
        _seed(session)


def reset_database() -> None:
    engine = get_engine()
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        _seed(session)
