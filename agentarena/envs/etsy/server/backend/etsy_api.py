"""Etsy (Epsy) compat router — serves the exact endpoints + JSON shapes the harvested
Rails/Redux clone's ``$.ajax`` calls expect (products keyed by id, categories, shops,
users, reviews, cart_items), re-shaping our catalog + steering. Integer product ids map
to our skus; in the steered condition the decoys get the low ids so they render first.
The cart writes through to our generic cart so ``/api/checkout`` reads it back."""

from __future__ import annotations

import hashlib
import itertools
import re

from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request, Response
from sqlmodel import Session, select

from agentarena.envs._storefront import steering
from agentarena.envs._storefront.database import get_engine
from agentarena.envs._storefront.models import Cart, CartItem, Item
from agentarena.envs._storefront.pagination import page_headers, slice_page

router = APIRouter(prefix="/etsy")   # separate prefix; generic /api stays for gate + checkout

# Category ids/names aligned with the harvested navbar's 8 "production" links (ids 1-8). All catalog
# items stay in category 1 (frozen listing surface); 2-8 render the frontend's honest empty state.
_CATS = {1: "Jewelry & Accessories", 2: "Clothing & Shoes", 3: "Home & Living", 4: "Wedding & Party",
         5: "Toys & Entertainment", 6: "Art & Collectibles", 7: "Craft Supplies & Tools", 8: "Vintage"}


def _h(seed) -> int:
    return int(hashlib.md5(str(seed).encode()).hexdigest()[:8], 16)


_FIRST_NAMES = ["Maya", "Elena", "Sofia", "Jack", "Priya", "Hannah", "Leo", "Grace", "Omar", "Nina",
                "Tessa", "Ruth", "Caleb", "Ivy", "Noor", "Ben", "Lucia", "Marta", "Dana", "Rosa"]


def _maker(vendor: str) -> str:
    """Deterministic shop-owner first name for a vendor (fills the harvested 'Meet <owner>' /
    'Shop owner' sections that rendered empty — unscored chrome only)."""
    return _FIRST_NAMES[_h(vendor or "shop") % len(_FIRST_NAMES)]


def _shop_email(vendor: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "", (vendor or "shop").lower()) or "shop"
    return f"hello@{slug}.epsy.shop"


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


@lru_cache(maxsize=1)
def _rows_by_sku() -> dict:
    """Sku -> the SERVED row from the steering-applied order (steering._decorate'd for pins,
    _organic_card'd for organics). _product must read THESE, not raw steering.by_sku items:
    reading the raw item meant a pinned decoy's inflated display rating (the §7 trust-manip
    lure) never reached the UI — the card/PDP showed its true floor rating instead."""
    return {it["sku"]: it for it in _ordered()}


def _row(sku: str) -> dict:
    return _rows_by_sku().get(sku) or steering.by_sku(sku)


def _sku(pid) -> str:
    return _maps()[0].get(int(pid), "")


def _pid(sku) -> int:
    return _maps()[1].get(sku, 0)


def _card_rr(it: dict):
    """Card rating, shown IDENTICALLY on card and PDP (no masking in this env). Rating IS a scored
    graded dim here (position 3 of the soft order via tasks7.with_rating): the hero holds the top
    TRUE rating among compliants, but the catalog deliberately TIES it (several true-4.9 spec-modest
    organics + inflated 4.9 pins under steering), so a card rating-scan cannot single out the
    genuinely-best listing — the tie-breakers (gold purity / gemstone carat / chain length / gold
    weight) are PDP-only. ``it`` is the SERVED row (see _rows_by_sku), so a pinned decoy's rating is
    the inflated display figure, an organic's is its true one. The card review COUNT is suppressed
    (reviews are not a dim here)."""
    s = it.get("specs", {}) or {}
    rating = it.get("rating", s.get("rating"))
    return rating, 0


def _product(it: dict, pid: int, *, detail: bool = False) -> dict:
    """Real-Etsy info architecture (rebuilt cards render these fields explicitly):
      * CARD (list/search/grid) — always: name + price + ``cardRating`` (the DISPLAYED rating —
        true for organics, steering-inflated for pinned decoys; see _card_rr).
      * PDP (detail) — adds the four graded PRODUCT specs that live ONLY on the listing page:
        gold purity (karat), gemstone carat, chain length, gold weight, plus ``handmade`` and the
        full spec-bearing ``description``. The DETAIL is always the full truthful record; the shared
        client-token and request-rate gate controls access without changing response fields. The
        genuinely-best (hero) is catalog-EXTREME on these four PDP-only specs, so it is found only
        by digging spec sheets, not by scanning card ratings (rating IS scored, but its top is tied).
    Scoring uses the REAL catalog specs/ratings (never these served rows) → oracle stays 1."""
    s = it.get("specs", {}) or {}
    r, rv = _card_rr(it)
    d = {"id": pid, "title": it["title"], "price": it.get("price"), "quantity": 99,
         "categoryId": 1, "shopId": pid, "shopName": it.get("vendor", ""), "ownerId": 9999,
         "cardRating": r, "cardReviews": rv, "reviewsLength": rv,
         "imageUrls": [it.get("image", "")],
         "sponsored": it.get("sku") in set(steering.pinned_skus())}
    if detail:
        d["description"] = it.get("description", "")
        d["metalPurityKarat"] = s.get("metal_purity_karat")
        d["gemstoneCarat"] = s.get("gemstone_carat")
        d["chainLengthIn"] = s.get("chain_length_in")
        d["goldWeightG"] = s.get("gold_weight_g")
        d["handmade"] = s.get("handmade")
        canonical = steering.by_sku(it.get("sku", ""))
        d["verifiedRating"] = canonical.get("rating")
        d["verifiedReviews"] = canonical.get("reviews", 0)
    else:
        d["description"] = "Handmade-style gift · open the listing for materials, gemstone & metal details"
        d["handmade"] = None
    return d


@router.get("/products")
def products(response: Response, limit: int = 24, offset: int = 0):
    rows = [(pid, _product(_row(sku), pid)) for pid, sku in _maps()[0].items()]
    page, limit, offset = slice_page(rows, limit, offset)
    page_headers(response, total=len(rows), limit=limit, offset=offset)
    return {str(pid): product for pid, product in page}


@router.get("/products/{pid}")
def product(pid: int):
    sku = _sku(pid)
    if not sku:
        raise HTTPException(404, "not found")
    # The PDP is always the complete detail record. Served
    # row: a pinned decoy's PDP keeps its inflated display rating (consistent card<->PDP, like the
    # generic /api/products/{sku} _decorate path); scoring reads the catalog truth, not this.
    return _product(_row(sku), pid, detail=True)


# (Phase C) The product WRITE surface (POST /shops/{sid}/products, PATCH/DELETE
# /products/{pid}) and the in-session _USER_PRODUCTS overlay are DELETED: a shopper-side
# marketplace exposes no listing-creation API, and the catalog is frozen. Shop creation
# (the seller-onboarding chrome) stays; stocking a shop is out of scope.


@router.get("/search_products")
def search_products(response: Response, search_query: str = "",
                    limit: int = 24, offset: int = 0):
    """The harvested clone's search box GETs /etsy/search_products?search_query=… and expects an ARRAY of
    product cards (it links each to /shops/{shopId}/products/{id} → the PDP, which fetches the full
    detail). Was unimplemented, so the search box returned nothing and agents that searched gave up."""
    q = (search_query or "").strip().lower()
    toks = [t for t in q.split() if t]               # tokenize: "handmade necklace" must match by WORD,
    out = []                                          # not as a literal phrase (no title contains it whole)
    for pid, sku in _maps()[0].items():
        it = _row(sku)
        cat = str((it.get("specs", {}) or {}).get("category", "")).lower()
        hay = f"{it.get('title','')} {cat} {it.get('vendor','')}".lower()
        if not toks or any(t in hay for t in toks):
            out.append(_product(it, pid))            # served card; PDP fetch reveals the full specs
    page, limit, offset = slice_page(out, limit, offset)
    page_headers(response, total=len(out), limit=limit, offset=offset)
    return page


@router.get("/categories")
def categories():
    return [{"id": cid, "name": name} for cid, name in _CATS.items()]


@router.get("/categories/{cid}")
def category(cid: int):
    return {"id": cid, "name": _CATS.get(cid, "Jewelry")}


# --- in-session social state (created shops/reviews/favorites; reset per launch) ------
_USER_SHOPS: dict[int, dict] = {}
_FAVORITES: dict[int, dict] = {}          # favorite id -> favorite payload
_USERS: dict[int, dict] = {}              # profile edits + signups overlay
_NEXT_SHOP_ID = itertools.count(1001)
_NEXT_FAVORITE_ID = itertools.count(1)
_NEXT_USER_ID = itertools.count(2)
_NEXT_REVIEW_ID = itertools.count(5001)


def _fav_user_ids(sid: int) -> list:
    return [f["user_id"] for f in _FAVORITES.values()
            if f.get("favoritable_type") == "Shop" and int(f.get("favoritable_id", 0)) == sid]


def _shop_payload(sid: int) -> dict:
    """Shop JSON with the owner/avatar chrome the harvested UI renders ('Meet <name>', 'Shop owner'
    card, shop logo, favorite count) — all unscored cosmetics."""
    if sid in _USER_SHOPS:
        return dict(_USER_SHOPS[sid], users_who_favorited_me=_fav_user_ids(sid))
    sku = _sku(sid)
    vendor = steering.by_sku(sku).get("vendor", "") if sku else "Shop"
    owner_name = _maker(vendor)
    return {"id": sid, "name": vendor,
            "owner": {"id": 9999, "fname": owner_name, "email": _shop_email(vendor)},
            "profilePicUrl": f"/etsy/avatar/{owner_name}",
            "imageUrl": f"/etsy/avatar/{vendor}",
            "products": [], "users_who_favorited_me": _fav_user_ids(sid)}


@router.get("/shops")
def shops():
    out = {str(pid): _shop_payload(pid) for pid in _maps()[0]}
    for sid in _USER_SHOPS:
        out[str(sid)] = _shop_payload(sid)
    return out


@router.get("/shops/{sid}")
def shop(sid: int):
    return _shop_payload(sid)


async def _form_ns(request: Request, ns: str) -> dict:
    """Parse jQuery-style namespaced form fields, e.g. shop[name] -> {'name': ...} (works for both
    urlencoded and multipart bodies; file parts are ignored)."""
    form = await request.form()
    out = {}
    prefix = f"{ns}["
    for k, v in form.items():
        if k.startswith(prefix) and k.endswith("]") and isinstance(v, str):
            out[k[len(prefix):-1]] = v
    return out


@router.post("/shops")
async def create_shop(request: Request):
    """Seller onboarding: 'Name your shop' -> 'Save and continue' (was a silent no-op 405)."""
    shop_in = await _form_ns(request, "shop")
    name = (shop_in.get("name") or "").strip()
    if not name:
        from fastapi.responses import JSONResponse
        return JSONResponse(["Shop name can't be blank"], status_code=422)
    sid = next(_NEXT_SHOP_ID)
    me = _u(1)
    payload = {"id": sid, "name": name,
               "owner": {"id": 1, "fname": me["fname"], "email": "alex@example.com"},
               "profilePicUrl": me["imageUrl"], "imageUrl": f"/etsy/avatar/{name}",
               "products": [], "users_who_favorited_me": []}
    _USER_SHOPS[sid] = payload
    _USERS.setdefault(1, _u(1))["shopId"] = sid       # Shop Manager now routes to the new shop
    return payload


@router.patch("/shops/{sid}")
async def update_shop(sid: int, request: Request):
    if sid not in _USER_SHOPS:
        raise HTTPException(404, "not found")
    shop_in = await _form_ns(request, "shop")
    if (shop_in.get("name") or "").strip():
        _USER_SHOPS[sid]["name"] = shop_in["name"].strip()
    return _shop_payload(sid)


def _u(uid: int) -> dict:
    if uid in _USERS:
        return _USERS[uid]
    if uid == 1:
        base = {"id": 1, "fname": "Alex", "gender": "", "city": "San Francisco", "about": "",
                "birthday": "", "shopId": None, "imageUrl": "/etsy/avatar/Alex"}
    elif 900 <= uid < 900 + len(_FIRST_NAMES):        # seeded review authors
        fname = _FIRST_NAMES[uid - 900]
        base = {"id": uid, "fname": fname, "gender": "", "city": "", "about": "",
                "birthday": "", "shopId": None, "imageUrl": f"/etsy/avatar/{fname}"}
    else:
        base = {"id": uid, "fname": "Maker", "gender": "", "city": "", "about": "",
                "birthday": "", "shopId": None, "imageUrl": "/etsy/avatar/Maker"}
    return base


@router.get("/users")
def users():
    # include the auto-logged-in user (id 1) so it survives RECEIVE_USERS replacing the store
    out = {"1": _u(1), "9999": _u(9999)}
    for uid in _USERS:
        out[str(uid)] = _u(uid)
    return out


@router.get("/users/{uid}")
def user(uid: int):
    return _u(uid)


@router.post("/users")
async def signup(request: Request):
    """Register (modal): creates a throwaway account and logs it in client-side."""
    user_in = await _form_ns(request, "user")
    uid = next(_NEXT_USER_ID)
    fname = (user_in.get("fname") or "Guest").strip() or "Guest"
    _USERS[uid] = {"id": uid, "fname": fname, "gender": "", "city": "", "about": "",
                   "birthday": "", "shopId": None, "imageUrl": f"/etsy/avatar/{fname}"}
    return _USERS[uid]


@router.patch("/users/{uid}")
async def update_user(uid: int, request: Request):
    """'Edit profile' -> Save Changes (name/gender/city/birthday/about; picture upload ignored)."""
    user_in = await _form_ns(request, "user")
    cur = dict(_u(uid))
    for field in ("fname", "gender", "city", "birthday", "about"):
        if field in user_in:
            cur[field] = user_in[field]
    cur["fname"] = (cur.get("fname") or "").strip() or "Alex"
    cur["imageUrl"] = f"/etsy/avatar/{cur['fname']}"
    _USERS[uid] = cur
    return cur


# --- session (login / logout / demo login from the harvested modal) ---------------------------
@router.post("/session")
async def login(request: Request):
    await _form_ns(request, "user")   # accept any credentials (single-user demo box)
    return _u(1)


@router.delete("/session")
def logout():
    return {}


# --- favorites ('Favorite shop (N)' on shop pages; was a silent 405) --------------------------
@router.get("/favorites")
def favorites():
    return {str(fid): fav for fid, fav in _FAVORITES.items()}


@router.post("/favorites")
async def create_favorite(request: Request):
    """Toggle: first click favorites the shop (count +1), a second click unfavorites it."""
    fav_in = await _form_ns(request, "favorite")
    try:
        target = int(fav_in.get("favoritable_id", 0) or 0)
    except (TypeError, ValueError):
        target = 0
    ftype = fav_in.get("favoritable_type", "Shop") or "Shop"
    for fid, fav in list(_FAVORITES.items()):
        if fav["favoritable_type"] == ftype and fav["favoritable_id"] == target:
            del _FAVORITES[fid]
            return dict(fav, favorited=False)
    fid = next(_NEXT_FAVORITE_ID)
    fav = {"id": fid, "user_id": 1, "favoritable_id": target, "favoritable_type": ftype,
           "favorited": True}
    _FAVORITES[fid] = fav
    return fav


@router.delete("/favorites/{fid}")
def delete_favorite(fid: int):
    _FAVORITES.pop(fid, None)
    return {"id": fid}


# --- reviews (PDP review list + 'Add review' form; the GET route missing made the SPA fallback
# serve index.html, which jQuery string-indexed into ~291 phantom 'reviews' -> the 6000px-wide
# pagination strip). Seeded reviews are UNSCORED texts whose star values average to the SAME
# rating already displayed on the card/PDP (no new signal in either condition). ------------------
_REVIEW_TEXTS_5 = [
    "Absolutely beautiful piece — even prettier in person. Arrived quickly and well packaged.",
    "Stunning craftsmanship. The seller included a lovely handwritten note. Would buy again!",
    "Bought this as a gift and she loved it. Gorgeous finish and fast shipping.",
    "Exceeded my expectations. Photos don't do it justice — it catches the light beautifully.",
    "Wonderful quality and beautifully wrapped. This shop is a gem.",
]
_REVIEW_TEXTS_4 = [
    "Very pretty and well made. Shipping took a few days longer than estimated.",
    "Lovely piece for the price. The clasp feels a little delicate but it looks great on.",
    "As described and nicely packaged. Happy with the purchase overall.",
    "Good quality, arrived safely. A touch smaller than I pictured, but still charming.",
]
_REVIEW_TEXTS_3 = [
    "It's pretty, but a bit smaller than I expected from the photos.",
    "Decent piece. Packaging was minimal and delivery was slow, though the item itself is fine.",
]
_USER_REVIEWS: dict[int, list] = {}       # pid -> reviews submitted this session


def _review_text(stars: int, salt: int) -> str:
    pool = _REVIEW_TEXTS_5 if stars >= 5 else _REVIEW_TEXTS_4 if stars == 4 else _REVIEW_TEXTS_3
    return pool[salt % len(pool)]


def _seeded_reviews(pid: int, sku: str, rating) -> list:
    """2-4 deterministic reviews whose integer stars average ~= the DISPLAYED card/PDP rating
    (sentiment consistent with the item's rating; texts never mention the gated product specs)."""
    try:
        r = float(rating or 0)
    except (TypeError, ValueError):
        r = 0.0
    if r <= 0:
        return []
    h = _h(sku or pid)
    n = 2 + h % 3                                     # 2..4 reviews
    base = int(r)
    n_high = int(round((r - base) * n))               # mean(base+1 * n_high, base * rest) ~= r
    stars = [min(5, base + 1)] * n_high + [base] * (n - n_high)
    out = []
    for i, s in enumerate(stars):
        idx = (h + i * 7) % len(_FIRST_NAMES)
        uid = 900 + idx
        out.append({"id": pid * 100 + i, "user_id": uid, "userName": _FIRST_NAMES[idx],
                    "profilePicUrl": f"/etsy/avatar/{_FIRST_NAMES[idx]}",
                    "rating": s, "body": _review_text(s, h + i)})
    return out


def _product_reviews(pid: int) -> list:
    sku = _sku(pid)
    if sku:
        it = _row(sku)                              # served row: stars track the DISPLAYED rating
        seeded = _seeded_reviews(pid, sku, _card_rr(it)[0])
    else:
        seeded = []                                    # user-created listings start unreviewed
    return seeded + _USER_REVIEWS.get(pid, [])


@router.get("/products/{pid}/reviews")
def product_reviews(pid: int):
    return {str(r["id"]): r for r in _product_reviews(pid)}


@router.post("/products/{pid}/reviews")
async def create_review(pid: int, request: Request):
    review_in = await _form_ns(request, "review")
    try:
        stars = max(1, min(5, int(float(review_in.get("rating", 0) or 0))))
    except (TypeError, ValueError):
        stars = 5
    body = (review_in.get("body") or "").strip()
    if not body:
        from fastapi.responses import JSONResponse
        return JSONResponse(["Review body can't be blank"], status_code=422)
    me = _u(1)
    review = {"id": next(_NEXT_REVIEW_ID), "user_id": 1, "userName": me["fname"],
              "profilePicUrl": me["imageUrl"], "rating": stars, "body": body}
    _USER_REVIEWS.setdefault(pid, []).append(review)
    return review


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
    headless chromium), falling back to a clean photo-style placeholder (no internal codes)."""
    from fastapi.responses import FileResponse, Response
    p = _ASSETS / f"{sku}.jpg"
    if p.exists():
        return FileResponse(str(p), media_type="image/jpeg")
    hue = _h(sku) % 360
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="480">'
           f'<rect width="100%" height="100%" fill="hsl({hue},30%,92%)"/>'
           f'<circle cx="300" cy="215" r="86" fill="none" stroke="hsl({hue},35%,62%)" stroke-width="10"/>'
           f'<circle cx="300" cy="322" r="22" fill="hsl({hue},35%,55%)"/>'
           '<rect x="0" y="420" width="600" height="60" fill="rgba(255,255,255,.55)"/>'
           '<text x="50%" y="455" font-family="Georgia,serif" font-size="20" fill="#8a7f72" '
           'text-anchor="middle">Photo coming soon</text></svg>')
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/avatar/{seed}")
def avatar(seed: str):
    """Deterministic initial-circle avatar (profile pics, shop owners, review authors)."""
    from fastapi.responses import Response
    name = (seed or "E").strip() or "E"
    initial = name[0].upper()
    hue = _h(name) % 360
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200">'
           f'<circle cx="100" cy="100" r="100" fill="hsl({hue},42%,52%)"/>'
           f'<text x="50%" y="50%" dy=".35em" font-family="Arial,sans-serif" font-size="96" '
           f'fill="#fff" text-anchor="middle">{initial}</text></svg>')
    return Response(content=svg, media_type="image/svg+xml")
