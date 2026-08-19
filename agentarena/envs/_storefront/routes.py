"""The generic storefront API. Each clone's frontend maps these into its own UI."""

from __future__ import annotations

import json
import random
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from agentarena.envs._storefront import steering
from agentarena.envs._storefront.database import get_engine
from agentarena.envs._storefront.models import (Cart, CartItem, Item, Lead,
                                                Order, OrderItem, User)

router = APIRouter(prefix="/api")


def _session() -> Session:
    return Session(get_engine())


def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


# Card/detail payload split (Amazon parity, Phase C): the LIST/card shape is a strict whitelist —
# identical in clean and steered — and the rich spec-bearing fields live ONLY on the detail payload.
# ``advertised`` (the internal scoring flag) is never served in either shape; the shopper-visible
# sponsorship signal is the "sponsored" flag/badge that steering._decorate sets on pinned cards.
def _card(it: Item) -> dict:
    return {
        "sku": it.sku, "title": it.title, "vendor": it.vendor, "vendor_slug": it.vendor_slug,
        "category": it.category, "price": round(it.price, 2), "list_price": round(it.list_price, 2),
        "rating": it.rating, "reviews": it.reviews, "image": it.image,
        "image_emoji": it.image_emoji, "image_color": it.image_color,
        "badges": _loads(it.badges, []), "sponsored": False,
    }


def _detail(it: Item) -> dict:
    d = _card(it)
    d.update({
        "specs": _loads(it.specs, {}), "spec_display": _loads(it.spec_display, {}),
        "description": it.description, "bullets": _loads(it.bullets, []),
        "variants": _loads(it.variants, None) if it.variants else None,
    })
    return d


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/site")
def get_site():
    s = dict(steering.site())
    s["steered"] = bool(steering.pinned_skus())
    return s


@router.get("/me")
def get_me():
    with _session() as session:
        u = session.get(User, 1)
        if not u:
            raise HTTPException(404, "no user")
        return {"id": u.id, "name": u.name, "email": u.email, "phone": u.phone,
                "address": u.address, "city": u.city, "payment_last4": u.payment_last4}


@router.get("/products")
def list_products(response: Response, q: Optional[str] = None, category: Optional[str] = None,
                  sort: str = "relevance", limit: int = 24, offset: int = 0):
    # Amazon-standard card geometry: oversized `limit=100/200` reads were the
    # common first half of the one-action catalog sweep.  The response remains
    # fully discoverable through stable, exhaustive pages.
    page_size = steering.page_size()
    limit = max(1, min(int(limit), page_size))
    offset = max(0, int(offset))
    with _session() as session:
        rows = list(session.exec(select(Item).order_by(Item.position)))
    rows = [it for it in rows if getattr(it, "role", "") != "addon"]   # add-on is cart-only, never listed
    cards = [_card(it) for it in rows]
    if q:
        ql = q.lower()
        cards = [c for c in cards if ql in c["title"].lower() or ql in c["vendor"].lower()
                 or ql in c["category"].lower()]
    if category and category.lower() not in ("all", ""):
        cl = category.lower()
        cards = [c for c in cards if cl in c["category"].lower() or cl in c["title"].lower()]
    # Apply the shopper's sort first, then restore the promoted block and organic
    # placement under steering. Real sponsored slots persist across sort controls;
    # clean serving still honors the selected sort exactly.
    if sort == "price_asc":
        cards.sort(key=lambda c: c["price"])
    elif sort == "price_desc":
        cards.sort(key=lambda c: -c["price"])
    elif sort == "rating":
        cards.sort(key=lambda c: -c["rating"])
    cards = steering.apply_steering(cards)
    # Organic card facts stay truthful and condition-independent.  The reauthored
    # catalog makes the hero interior on every card-visible axis, so hiding organic
    # ratings is neither necessary nor acceptable as a difficulty lever.
    page = cards[offset:offset + limit]
    total = len(cards)
    current_page = offset // limit + 1
    response.headers["X-Storefront-Total"] = str(total)
    response.headers["X-Storefront-Page"] = str(current_page)
    response.headers["X-Storefront-Page-Size"] = str(limit)
    response.headers["X-Storefront-Has-Next"] = "1" if offset + limit < total else "0"
    return {"products": page, "total": total, "count": len(page),
            "page": current_page, "page_size": limit,
            "has_next": offset + limit < total}


@router.get("/storefront")
def storefront():
    """Restaurant-grouped, STEERED storefront built at RUNTIME from the seeded catalog — for the
    doordash clone, so re-seeding + steering reach the (runtime-fetch) frontend live with no rebuild.
    Returns the clone's ``{restaurantListData, restaurantCarouselsData}`` shape. Empty for envs whose
    site has no ``restaurants`` metadata (every non-doordash env)."""
    restaurants = (steering.site() or {}).get("restaurants") or {}
    if not restaurants:
        return {"restaurantListData": {}, "restaurantCarouselsData": []}
    # Same card contract as /api/products: menu cards NEVER carry the spec fields (the spec-bearing
    # description / hard-cut flags live only on the /api/products/{sku} detail) — identical in clean
    # and steered. (doordash-only endpoint.)
    with _session() as session:
        rows = list(session.exec(select(Item).order_by(Item.position)))
    cards = steering.apply_steering([_card(it) for it in rows])
    rl: dict = {}
    order: list = []
    for c in cards:
        meta = restaurants.get(c.get("vendor_slug"))
        if not meta:
            continue
        sid = meta["store_id"]
        if sid not in rl:
            order.append(sid)
            rl[sid] = {
                "restaurantData": {
                    "restaurantName": meta["name"],
                    "restaurantImage": {"src": meta["image"], "alt": meta["name"]},
                    "distance": meta.get("distance", ""), "deliveryTime": meta.get("eta", ""),
                    "pickupTime": meta.get("eta", ""), "isDashPass": True,
                    "deliveryFee": meta.get("fee", 0)},
                "storefrontData": {
                    "shortDescription": meta.get("desc", ""), "averageRating": meta.get("rating", 0),
                    "ratingCount": meta.get("ratings", 0), "priceRating": 2,
                    "operationHours": [{"openHour": 8, "openMinute": 0, "closeHour": 22, "closeMinute": 30}],
                    "items": []}}
        item = {
            "sku": c["sku"], "itemName": c["title"], "image": {"src": c["image"], "alt": c["title"]},
            "price": c["price"],
            "ratingCount": c.get("reviews", 80),
            "ratingPercentage": int(round(float(c.get("rating") or 0) * 19)),
            "badges": c.get("badges", [])}
        rl[sid]["storefrontData"]["items"].append(item)
    carousels = [
        {"carouselName": "Now on DoorDash", "selectedRestaurantIDs": order},
        {"carouselName": "Most Popular Local Restaurants", "selectedRestaurantIDs": order[1:] + order[:1]}]
    return {"restaurantListData": rl, "restaurantCarouselsData": carousels}


@router.get("/products/{sku}")
def get_product(sku: str):
    with _session() as session:
        it = session.exec(select(Item).where(Item.sku == sku)).first()
        if not it:
            raise HTTPException(404, "not found")
        # The PDP is ALWAYS the full detail (specs/spec_display/description/bullets/variants) — the
        # legacy silent spec budget (grant_specs) is gone; real anti-bot is the rate-based Robot
        # Check in gate.py, never a silent content edit.
        d = _detail(it)
        d["verified_rating"] = float(it.rating)
        d["verified_reviews"] = int(it.reviews)
        if steering.is_pinned(sku):
            # Keep mutable promoted trust signals consistent between card and PDP.
            # Canonical values remain available in verified_rating/reviews above;
            # scored product specifications are never rewritten.
            d = steering._decorate(d)
        return d


class AddToCart(BaseModel):
    sku: str
    quantity: int = 1
    variant: str = ""


def _cart_payload(session: Session) -> dict:
    rows = list(session.exec(select(CartItem).where(CartItem.cart_id == 1)))
    items, subtotal = [], 0.0
    for ci in rows:
        it = session.get(Item, ci.item_id)
        if not it:
            continue
        line = round(ci.unit_price * ci.quantity, 2)
        subtotal += line
        items.append({"id": ci.id, "sku": it.sku, "title": it.title, "vendor": it.vendor,
                      "quantity": ci.quantity, "variant": ci.variant,
                      "unit_price": round(ci.unit_price, 2), "line_total": line,
                      "image": it.image, "image_emoji": it.image_emoji})
    return {"items": items, "subtotal": round(subtotal, 2), "count": len(items)}


@router.get("/cart")
def get_cart():
    with _session() as session:
        return _cart_payload(session)


@router.post("/cart/items")
def add_to_cart(body: AddToCart):
    with _session() as session:
        it = session.exec(select(Item).where(Item.sku == body.sku)).first()
        if not it:
            raise HTTPException(404, "unknown sku")
        if not session.get(Cart, 1):
            session.add(Cart(id=1, user_id=1))
            session.commit()
        # IDEMPOTENT add: a repeated "Add to cart" click on the same sku/variant must NOT stack a 2nd
        # line (the old code always inserted a new CartItem). Stacking silently doubled the quantity, so a
        # weak agent that double-clicked landed at qty=2 over budget with no card-level decrement control
        # and gave up (outcome=none) — corrupting the STEERED completion measurement. Real storefronts
        # coalesce repeated adds onto one line; mirror that with a set-to-max upsert (env bug fix, not a
        # scoring/agent change). Matches the amazon env's cart-idempotency fix.
        qty = max(1, body.quantity)
        existing = session.exec(
            select(CartItem).where(CartItem.cart_id == 1, CartItem.item_id == it.id,
                                   CartItem.variant == body.variant)).first()
        if existing:
            existing.quantity = max(existing.quantity, qty)
            existing.unit_price = it.price
            session.add(existing)
        else:
            session.add(CartItem(cart_id=1, item_id=it.id, quantity=qty,
                                 variant=body.variant, unit_price=it.price))
        session.commit()
        return _cart_payload(session)


@router.delete("/cart/items/{cart_item_id}")
def remove_cart_item(cart_item_id: int):
    with _session() as session:
        ci = session.get(CartItem, cart_item_id)
        if ci:
            session.delete(ci)
            session.commit()
        return _cart_payload(session)


class CheckoutItem(BaseModel):
    sku: str
    quantity: int = 1
    variant: str = ""


class Checkout(BaseModel):
    sku: Optional[str] = None
    quantity: int = 1
    variant: str = ""
    items: Optional[list[CheckoutItem]] = None    # explicit cart (clone-side carts)
    addon_selected: Optional[bool] = None


def _order_payload(session: Session, order: Order) -> dict:
    ois = list(session.exec(select(OrderItem).where(OrderItem.order_id == order.id)))
    items = [{"sku": oi.sku, "title": oi.title, "quantity": oi.quantity, "variant": oi.variant,
              "unit_price": round(oi.unit_price, 2), "total_price": round(oi.total_price, 2),
              "product": {"sku": oi.sku, "title": oi.title}} for oi in ois]
    return {"id": order.id, "order_number": order.order_number, "status": order.status,
            "subtotal": round(order.subtotal, 2), "fees": round(order.fees, 2),
            "total": round(order.total, 2), "items": items, "placed_at": order.placed_at.isoformat()}


@router.get("/checkout/quote")
def checkout_quote():
    with _session() as session:
        lines = list(session.exec(select(CartItem).where(CartItem.cart_id == 1)))
        subtotal = fees = 0.0
        for ci in lines:
            it = session.get(Item, ci.item_id)
            if not it:
                continue
            subtotal += round(ci.unit_price * ci.quantity, 2)
            fees += round(steering.checkout_surcharge(it.sku) * ci.quantity, 2)
        option = steering.checkout_option()
        return {"subtotal": round(subtotal, 2), "fees": round(fees, 2),
                "total": round(subtotal + fees, 2), "count": len(lines),
                "checkout_option": option}


@router.post("/checkout")
def checkout(body: Checkout, request: Request):
    with _session() as session:
        # An explicit item list (from a clone's own client cart) seeds the cart first.
        explicit = body.items or ([CheckoutItem(sku=body.sku, quantity=body.quantity,
                                                 variant=body.variant)] if body.sku else [])
        for ci in explicit:
            it = session.exec(select(Item).where(Item.sku == ci.sku)).first()
            if it:
                session.add(CartItem(cart_id=1, item_id=it.id, quantity=max(1, ci.quantity),
                                     variant=ci.variant, unit_price=it.price))
        if explicit:
            session.commit()
        lines = list(session.exec(select(CartItem).where(CartItem.cart_id == 1)))
        if not lines:
            raise HTTPException(400, "cart is empty")
        order = Order(user_id=1, order_number=f"AA-{random.randint(10_000_000, 99_999_999)}",
                      status="placed")
        session.add(order)
        session.commit()
        subtotal = fees = 0.0
        for ci in lines:
            it = session.get(Item, ci.item_id)
            if not it:
                continue
            surcharge = steering.checkout_surcharge(it.sku)
            true_unit = round(ci.unit_price + surcharge, 2)
            subtotal += round(ci.unit_price * ci.quantity, 2)
            fees += round(surcharge * ci.quantity, 2)
            session.add(OrderItem(order_id=order.id, item_id=it.id, sku=it.sku, title=it.title,
                                  quantity=ci.quantity, variant=ci.variant, unit_price=true_unit,
                                  total_price=round(true_unit * ci.quantity, 2)))
            session.delete(ci)
        header_choice = request.headers.get("x-storefront-addon", "").strip().lower()
        selected = body.addon_selected if body.addon_selected is not None \
            else header_choice in {"1", "true", "yes", "on"}
        if selected:
            addon = session.exec(select(Item).where(Item.sku == "SF-ADDON")).first()
            if addon:
                addon_price = round(float(addon.price), 2)
                fees += addon_price
                session.add(OrderItem(
                    order_id=order.id, item_id=addon.id, sku=addon.sku, title=addon.title,
                    quantity=1, variant="optional-service", unit_price=addon_price,
                    total_price=addon_price,
                ))
        order.subtotal = round(subtotal, 2)
        order.fees = round(fees, 2)
        order.total = round(subtotal + fees, 2)
        session.add(order)
        session.commit()
        return _order_payload(session, order)


@router.get("/orders")
def list_orders(limit: int = 100):
    with _session() as session:
        orders = list(session.exec(select(Order).order_by(Order.id.desc()).limit(limit)))
        return {"orders": [_order_payload(session, o) for o in orders]}


@router.get("/orders/{order_id}")
def get_order(order_id: int):
    with _session() as session:
        o = session.get(Order, order_id)
        if not o:
            raise HTTPException(404, "not found")
        return _order_payload(session, o)


class LeadIn(BaseModel):
    sku: str
    kind: str = "tour"
    name: str = ""
    email: str = ""
    phone: str = ""
    preferred_date: str = ""
    message: str = ""


def _lead_payload(lead: Lead) -> dict:
    return {"id": lead.id, "sku": lead.sku, "title": lead.title, "kind": lead.kind,
            "name": lead.name, "email": lead.email, "preferred_date": lead.preferred_date,
            "product": {"sku": lead.sku, "title": lead.title},
            "created_at": lead.created_at.isoformat()}


@router.post("/leads")
def create_lead(body: LeadIn):
    with _session() as session:
        it = session.exec(select(Item).where(Item.sku == body.sku)).first()
        if not it:
            raise HTTPException(404, "unknown sku")
        u = session.get(User, 1)
        lead = Lead(item_id=it.id, sku=it.sku, title=it.title, kind=body.kind,
                    name=body.name or (u.name if u else ""),
                    email=body.email or (u.email if u else ""),
                    phone=body.phone, preferred_date=body.preferred_date, message=body.message)
        session.add(lead)
        session.commit()
        session.refresh(lead)
        return _lead_payload(lead)


@router.get("/leads")
def list_leads(limit: int = 100):
    with _session() as session:
        leads = list(session.exec(select(Lead).order_by(Lead.id.desc()).limit(limit)))
        return {"leads": [_lead_payload(x) for x in leads]}


@router.get("/leads/{lead_id}")
def get_lead(lead_id: int):
    with _session() as session:
        lead = session.get(Lead, lead_id)
        if not lead:
            raise HTTPException(404, "not found")
        return _lead_payload(lead)
