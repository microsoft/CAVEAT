"""Etsy (Epsy) compat router — serves the exact endpoints + JSON shapes the harvested
Rails/Redux clone's ``$.ajax`` calls expect (products keyed by id, categories, shops,
users, reviews, cart_items), re-shaping our catalog + steering. Integer product ids map
to our skus; in the steered condition the decoys get the low ids so they render first.
The cart writes through to our generic cart so ``/api/checkout`` reads it back."""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request
from sqlmodel import Session, select

from agentarena.envs._storefront import steering
from agentarena.envs._storefront.database import get_engine
from agentarena.envs._storefront.models import Cart, CartItem, Item

router = APIRouter(prefix="/etsy")   # separate prefix; generic /api stays for gate + checkout

_CATS = {1: "Jewelry", 2: "Home & Living", 3: "Housewarming", 4: "Wedding", 5: "Art", 6: "Clothing"}


@lru_cache(maxsize=1)
def _ordered() -> list:
    items = [dict(it) for it in steering.items()]
    if steering.pinned_skus():
        items = steering.apply_steering(items)   # decoys first -> they get the low ids
    return items


@lru_cache(maxsize=1)
def _maps():
    id_to_sku = {i + 1: it["sku"] for i, it in enumerate(_ordered())}
    return id_to_sku, {v: k for k, v in id_to_sku.items()}


def _sku(pid) -> str:
    return _maps()[0].get(int(pid), "")


def _pid(sku) -> int:
    return _maps()[1].get(sku, 0)


def _product(it: dict, pid: int) -> dict:
    return {"id": pid, "title": it["title"], "description": it.get("description", ""),
            "price": it.get("price"), "quantity": 99, "categoryId": 1, "shopId": pid,
            "shopName": it.get("vendor", ""), "ownerId": 9999,
            "reviewsLength": it.get("reviews", 0), "imageUrls": [it.get("image", "")],
            "handmade": (it.get("specs", {}) or {}).get("handmade"),
            "sponsored": it.get("sku") in set(steering.pinned_skus())}


@router.get("/products")
def products():
    return {str(pid): _product(steering.by_sku(sku), pid) for pid, sku in _maps()[0].items()}


@router.get("/products/{pid}")
def product(pid: int):
    sku = _sku(pid)
    if not sku:
        raise HTTPException(404, "not found")
    return _product(steering.by_sku(sku), pid)


@router.get("/categories")
def categories():
    return [{"id": cid, "name": name} for cid, name in _CATS.items()]


@router.get("/categories/{cid}")
def category(cid: int):
    return {"id": cid, "name": _CATS.get(cid, "Jewelry")}


@router.get("/shops")
def shops():
    return {str(pid): {"id": pid, "name": steering.by_sku(sku).get("vendor", ""),
                       "owner": {"id": 9999}, "products": [], "users_who_favorited_me": []}
            for pid, sku in _maps()[0].items()}


@router.get("/shops/{sid}")
def shop(sid: int):
    sku = _sku(sid)
    return {"id": sid, "name": steering.by_sku(sku).get("vendor", "") if sku else "Shop",
            "owner": {"id": 9999}, "products": [], "users_who_favorited_me": []}


def _u(uid: int) -> dict:
    fname = "Alex" if uid == 1 else "Maker"
    return {"id": uid, "fname": fname, "gender": "", "city": "San Francisco", "about": "",
            "birthday": "", "shopId": None}


@router.get("/users")
def users():
    # include the auto-logged-in user (id 1) so it survives RECEIVE_USERS replacing the store
    return {"1": _u(1), "9999": _u(9999)}


@router.get("/users/{uid}")
def user(uid: int):
    return _u(uid)


@router.get("/reviews")
def reviews():
    return {}


# --- cart (jQuery posts form-encoded `cart_item[...]`) ----------------------- #
def _session() -> Session:
    return Session(get_engine())


def _enriched(ci: CartItem, s: Session) -> dict:
    it = s.get(Item, ci.item_id)
    pid = _pid(it.sku) if it else 0
    return {"id": ci.id, "productId": pid, "productName": it.title if it else "",
            "price": it.price if it else 0, "quantity": ci.quantity, "maximumQuantity": 99,
            "shopId": pid, "shopName": it.vendor if it else "",
            "imageUrls": [it.image] if it and it.image else [""]}


@router.get("/cart_items")
def get_cart_items():
    with _session() as s:
        rows = list(s.exec(select(CartItem).where(CartItem.cart_id == 1)))
        return {str(ci.id): _enriched(ci, s) for ci in rows}


async def _form_ci(request: Request) -> dict:
    form = await request.form()
    out = {}
    for k, v in form.items():
        if k.startswith("cart_item[") and k.endswith("]"):
            out[k[len("cart_item["):-1]] = v
    return out


@router.post("/cart_items")
async def add_cart_item(request: Request):
    ci_in = await _form_ci(request)
    pid = ci_in.get("product_id") or ci_in.get("productId")
    qty = int(ci_in.get("quantity", 1) or 1)
    sku = _sku(pid)
    with _session() as s:
        it = s.exec(select(Item).where(Item.sku == sku)).first()
        if not it:
            raise HTTPException(404, "unknown product")
        if not s.get(Cart, 1):
            s.add(Cart(id=1, user_id=1))
            s.commit()
        ci = CartItem(cart_id=1, item_id=it.id, quantity=max(1, qty), unit_price=it.price)
        s.add(ci)
        s.commit()
        return _enriched(ci, s)


@router.patch("/cart_items/{cid}")
async def update_cart_item(cid: int, request: Request):
    ci_in = await _form_ci(request)
    with _session() as s:
        ci = s.get(CartItem, cid)
        if not ci:
            raise HTTPException(404, "not found")
        ci.quantity = max(1, int(ci_in.get("quantity", ci.quantity) or ci.quantity))
        s.add(ci)
        s.commit()
        return _enriched(ci, s)


@router.delete("/cart_items/{cid}")
def delete_cart_item(cid: int):
    with _session() as s:
        ci = s.get(CartItem, cid)
        if ci:
            s.delete(ci)
            s.commit()
        return {"id": cid}


_ASSETS = __import__("pathlib").Path(__file__).resolve().parent.parent / "_assets"


@router.get("/img/{sku}")
def product_image(sku: str):
    """Real bundled product photo for a sku (the clone's placehold.co host is blank in
    headless chromium), falling back to a clean SVG."""
    from fastapi.responses import FileResponse, Response
    p = _ASSETS / f"{sku}.jpg"
    if p.exists():
        return FileResponse(str(p), media_type="image/jpeg")
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="480">'
           '<rect width="100%" height="100%" fill="#f7f5f2"/>'
           f'<text x="50%" y="50%" font-family="Arial,sans-serif" font-size="22" '
           f'fill="#595959" text-anchor="middle">{sku}</text></svg>')
    return Response(content=svg, media_type="image/svg+xml")
