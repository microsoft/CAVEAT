"""StockX (CopX) compat router — serves the endpoints/shapes the harvested Rails/Redux
clone calls (sneakers keyed by id, per-sneaker listingitems, purchaseditems, users),
re-shaped from our catalog + steering. Integer sneaker ids map to skus; in steered the
decoys get the low ids so they render first. listingitem_id == sneaker_id (one size-10
listing per sneaker). A purchase writes through to our generic cart + checkout so
/api/orders reads it back."""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from sqlmodel import Session, select

from agentarena.envs._storefront import steering
from agentarena.envs._storefront.database import get_engine
from agentarena.envs._storefront.models import Cart, CartItem, Item

router = APIRouter(prefix="/stockx")


@lru_cache(maxsize=1)
def _ordered() -> list:
    items = [dict(it) for it in steering.items()]
    if steering.pinned_skus():
        items = steering.apply_steering(items)
    return items


@lru_cache(maxsize=1)
def _maps():
    id_to_sku = {i + 1: it["sku"] for i, it in enumerate(_ordered())}
    return id_to_sku, {v: k for k, v in id_to_sku.items()}


def _sku(sid) -> str:
    return _maps()[0].get(int(sid), "")


_ASSETS = __import__("pathlib").Path(__file__).resolve().parent.parent / "_assets"


def _img_url(sku: str) -> str:
    """Real bundled product photo when we have one, else the placeholder SVG."""
    return f"/stockx/img/{sku}" if (_ASSETS / f"{sku}.jpg").exists() else f"/stockx/ph/{sku}"


def _sneaker(it: dict, sid: int) -> dict:
    sd = it.get("spec_display", {})
    return {"id": sid, "name": it["title"], "brand": it.get("vendor", ""),
            "style": sd.get("colorway", ""), "ticker": it["sku"], "description": it.get("description", ""),
            "release_date": "2024", "colorway": sd.get("colorway", ""),
            "retail_price": (it.get("specs", {}) or {}).get("retail", 150),
            "price": it.get("price"),
            "followed_by_current_user": False, "photoUrl": _img_url(it["sku"]),
            "sponsored": it.get("sku") in set(steering.pinned_skus())}


def _listing(sid: int) -> dict:
    sku = _sku(sid)
    it = steering.by_sku(sku)
    return {"id": sid, "sneaker_id": sid, "size": "10", "price": it.get("price"),
            "sneakerName": it.get("title", ""), "photoUrl": _img_url(sku)}


@router.get("/sneakers")
def sneakers():
    return {str(sid): _sneaker(steering.by_sku(sku), sid) for sid, sku in _maps()[0].items()}


@router.get("/sneakers/{sid}")
def sneaker(sid: int):
    sku = _sku(sid)
    if not sku:
        raise HTTPException(404, "not found")
    return _sneaker(steering.by_sku(sku), sid)


@router.get("/sneakers/{sid}/listingitems")
def sneaker_listings(sid: int):
    return {str(sid): _listing(sid)}


@router.get("/listingitems/{iid}")
def listingitem(iid: int):
    if not _sku(iid):
        raise HTTPException(404, "not found")
    return _listing(iid)


@router.get("/users/{uid}")
def user(uid: int):
    return {"id": uid, "username": "Alex" if uid == 1 else "seller", "email": "", "following": []}


# --- purchase (writes through to our cart + checkout) ----------------------- #
def _session() -> Session:
    return Session(get_engine())


async def _form(request: Request) -> dict:
    try:
        form = await request.form()
        if form:
            return {k: v for k, v in form.items()}
    except Exception:
        pass
    try:
        return await request.json()
    except Exception:
        return {}


def _purchase_payload(order_id: int, sku: str) -> dict:
    it = steering.by_sku(sku)
    return {"id": order_id, "purchasedItem": {"id": order_id}, "sneaker_id": _maps()[1].get(sku, 0),
            "size": "10", "price": it.get("price"), "sneakerName": it.get("title", ""),
            "photoUrl": _img_url(sku)}


@router.post("/purchaseditems")
async def purchase(request: Request):
    body = await _form(request)
    sid = body.get("sneaker_id") or body.get("listingitem_id") or body.get("id")
    sku = _sku(sid)
    with _session() as s:
        it = s.exec(select(Item).where(Item.sku == sku)).first()
        if not it:
            raise HTTPException(404, "unknown sneaker")
        if not s.get(Cart, 1):
            s.add(Cart(id=1, user_id=1))
            s.commit()
        s.add(CartItem(cart_id=1, item_id=it.id, quantity=1, unit_price=it.price))
        s.commit()
    # place the order through the generic checkout (reads our cart)
    from agentarena.envs._storefront.routes import checkout, Checkout
    order = checkout(Checkout())
    return _purchase_payload(order["id"], sku)


@router.get("/purchaseditems/{pid}")
def get_purchase(pid: int):
    from agentarena.envs._storefront.routes import get_order
    try:
        order = get_order(pid)
    except Exception:
        return {"id": pid, "purchasedItem": {"id": pid}}
    sku = (order["items"][0]["sku"] if order.get("items") else "")
    return _purchase_payload(pid, sku)


@router.get("/purchaseditems")
def purchases():
    from agentarena.envs._storefront.routes import list_orders
    data = list_orders()
    return {str(o["id"]): _purchase_payload(o["id"], (o["items"][0]["sku"] if o.get("items") else ""))
            for o in data.get("orders", [])}


@router.get("/img/{sku}")
def product_image(sku: str):
    """The real bundled product photo for a sku (falls back to the SVG placeholder)."""
    from fastapi.responses import FileResponse
    p = _ASSETS / f"{sku}.jpg"
    if p.exists():
        return FileResponse(str(p), media_type="image/jpeg")
    return placeholder(sku)


@router.get("/ph/{label}")
def placeholder(label: str):
    text = label.replace("-", " ").replace("_", " ").replace("%20", " ").strip()
    if label.lower() == "stockx":   # the header wordmark
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="120" height="34">'
               '<text x="0" y="26" font-family="Arial Black,Arial,sans-serif" font-size="28" '
               'font-weight="900" fill="#000">Stock<tspan fill="#00a046">X</tspan></text></svg>')
        return Response(content=svg, media_type="image/svg+xml")
    # clean product placeholder (no emoji — emoji has no glyph in headless chromium)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="420" height="320">'
           f'<rect width="100%" height="100%" fill="#fafafa"/>'
           f'<rect x="60" y="120" width="300" height="14" rx="7" fill="#e3e3e3"/>'
           f'<ellipse cx="210" cy="150" rx="150" ry="40" fill="#eee"/>'
           f'<text x="50%" y="58%" font-family="Arial,sans-serif" font-size="18" font-weight="700" '
           f'fill="#1a1a1a" text-anchor="middle">{text[:34]}</text></svg>')
    return Response(content=svg, media_type="image/svg+xml")
