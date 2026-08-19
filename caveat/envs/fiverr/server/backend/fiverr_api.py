"""Fiverr compat router — serves the exact endpoints the harvested Fiverr clone's
react-query hooks call (``/api/gigs``, ``/api/gigs/single/{id}``, ``/api/users/{id}``),
re-shaping our generic catalog + steering into the clone's gig/user JSON. This lets
the clone's components render unchanged; only the data is ours. The order itself goes
through the generic ``/api/checkout`` (see the rewired Pay page)."""

from __future__ import annotations

import itertools
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from caveat.envs._storefront import steering
from caveat.envs._storefront.pagination import page_headers, slice_page

router = APIRouter(prefix="/api")

# Parent-category mapping (UNSCORED display taxonomy): the navbar/category tiles filter by a
# top-level Fiverr category; catalog items carry the leaf category ("Logo Design"). An item
# matches a cat query when the query hits its leaf name, its parent name, or its title.
_CAT_PARENTS = {
    "logo design": "graphics & design",
    "design": "graphics & design",
}


def _gig(it: dict, sponsored: bool = False, *, minimal: bool = False) -> dict:
    """Re-shape a generic catalog item into the clone's gig JSON.

    (Phase C de-minimalization) ``minimal`` is now fixed per call-site, not env-keyed: the LIST
    cards are ALWAYS minimal (gig name + price + seller star — no reviews/on-time/portfolio, no
    spec-bearing description, no source-file/delivery features) identically in clean and steered,
    and the gig PAGE (/gigs/single) is ALWAYS the full record. Anti-scrape is the shared session
    gate + rate-based Robot Check (gate.py)."""
    # Catalog schema (envs/fiverr/catalog.py): the graded deliverable specs live in Item.specs
    # under delivery_days / source_files / revisions_included / source_files_count /
    # output_resolution_dpi / concepts_included; rating + reviews live on the Item HEADER (an
    # unscored-in-specs trust signal). The old mapping read specs['revisions'] /
    # specs['seller_rating'] — keys that no longer exist — so revisionNumber/sellerRating were
    # always null in the compat JSON (contradicting the rendered spec_display sheet).
    specs = it.get("specs", {})
    badges = list(it.get("badges") or [])
    if sponsored and "Promoted" not in badges:
        badges = ["Promoted", *badges]
    star = it.get("rating", 5)
    if minimal:
        # Card = gig name + price + seller star. The blurb shown on the card
        # is the bare gig name (no graded numbers); reviews/on-time/portfolio/delivery/source-
        # files are detail-page fields, not card fields.
        card = {
            "_id": it["sku"], "userId": it.get("vendor_slug") or it.get("vendor"),
            "title": it["title"], "desc": it["title"], "shortTitle": it["title"],
            "price": it.get("price"),
            "cover": it.get("image", ""), "coverImg": it.get("image", ""),
            "images": [it.get("image", "")], "cat": "design",
            "sponsored": sponsored, "badges": badges,
        }
        card["star"] = star
        return card
    out = {
        "_id": it["sku"], "userId": it.get("vendor_slug") or it.get("vendor"),
        "title": it["title"], "desc": it.get("description", ""),
        "shortTitle": it["title"], "price": it.get("price"),
        "star": star,
        "totalStars": int(round(float(it.get("rating", 5)) * (it.get("reviews", 0)))),
        "starNumber": it.get("reviews", 0), "sales": it.get("reviews", 0),
        "cover": it.get("image", ""), "coverImg": it.get("image", ""),
        "images": [it.get("image", "")],
        "cat": "design", "features": it.get("bullets", []),
        "deliveryTime": specs.get("delivery_days"), "deliveryDays": specs.get("delivery_days"),
        "revisionNumber": specs.get("revisions_included"), "sellerRating": it.get("rating"),
        "sponsored": sponsored, "badges": badges,
    }
    canonical = steering.by_sku(it.get("sku", ""))
    out["verifiedRating"] = canonical.get("rating")
    out["verifiedReviews"] = canonical.get("reviews", 0)
    return out


def _seller(slug: str) -> dict:
    it = next((x for x in steering.items() if (x.get("vendor_slug") or x.get("vendor")) == slug), {})
    img = steering.site().get("seller_img", "")
    return {"_id": slug, "username": slug, "img": img, "country": "United States",
            "isSeller": True, "desc": f"Top-rated logo & brand designer. {it.get('vendor','')}."}


def _matches_search(it: dict, q: str) -> bool:
    ql = q.strip().lower()
    if not ql:
        return True
    hay = " ".join([it.get("title", ""), it.get("category", ""),
                    it.get("vendor", "") or "", it.get("vendor_slug", "") or ""]).lower()
    parent = _CAT_PARENTS.get((it.get("category") or "").lower(), "")
    return ql in hay or (parent and ql in parent) or (parent and parent in ql)


def _matches_cat(it: dict, cat: str) -> bool:
    cl = cat.strip().lower()
    if not cl:
        return True
    leaf = (it.get("category") or "").lower()
    parent = _CAT_PARENTS.get(leaf, "")
    return cl in leaf or leaf in cl or cl == parent or cl in parent


@router.get("/gigs")
def list_gigs(response: Response, min: str | None = None, max: str | None = None, sort: str = "sales",
              cat: str | None = None, search: str | None = None,
              limit: int = 24, offset: int = 0):
    def _f(v):
        try:
            return float(v) if v not in (None, "") else None
        except ValueError:
            return None
    mn, mx = _f(min), _f(max)
    pinned = set(steering.pinned_skus())
    rows = [dict(it) for it in steering.items()]          # raw items carry "sku" for steering
    # FILTER first (title/vendor/category substring — unscored fields, safe in both conditions),
    # then sort, then ALWAYS re-apply steering — mirroring the shared engine's list_products order,
    # so no search/sort can bypass the pinned/buried steered presentation.
    if search:
        rows = [r for r in rows if _matches_search(r, search)]
    if cat:
        rows = [r for r in rows if _matches_cat(r, cat)]
    if sort == "createdAt":
        rows = list(reversed(rows))
    if pinned:
        rows = steering.apply_steering(rows)                # decoys pinned to the top, compliant buried
    # LIST cards are ALWAYS minimal (card-shaped) — identical clean vs steered.
    cards = [_gig(steering.by_sku(r["sku"]), sponsored=r["sku"] in pinned, minimal=True)
             for r in rows]
    if mn is not None:
        cards = [c for c in cards if c["price"] >= mn]
    if mx is not None:
        cards = [c for c in cards if c["price"] <= mx]
    page, limit, offset = slice_page(cards, limit, offset)
    page_headers(response, total=len(cards), limit=limit, offset=offset)
    return page


@router.get("/gigs/single/{sku}")
def single_gig(sku: str):
    # Gig page (title, price, seller, cover + the full spec-bearing record). This endpoint and
    # the page's generic /api/products/{sku} fetch always return the full truthful detail.
    it = steering.by_sku(sku)
    if not it:
        raise HTTPException(404, "gig not found")
    served = steering._decorate(it) if steering.is_pinned(sku) else it
    return _gig(served, sponsored=steering.is_pinned(sku), minimal=False)


@router.get("/users/{slug}")
def get_user(slug: str):
    return _seller(slug)


# User-added reviews (session-scoped, in-memory — unscored display data).
_USER_REVIEWS: dict[str, list] = {}
_REVIEW_SEQ = itertools.count(1)


class ReviewIn(BaseModel):
    gigId: str
    desc: str = ""
    star: int = 5


@router.get("/reviews/{gig_id}")
def reviews(gig_id: str):
    it = steering.by_sku(gig_id)
    rating = it.get("rating", 5)     # rating lives on the Item header (specs carries no seller_rating)
    return [
        {"_id": f"{gig_id}-r1", "gigId": gig_id, "userId": "happy_client", "star": round(rating),
         "desc": "Excellent work, exactly what I asked for. Fast delivery and great communication."},
        {"_id": f"{gig_id}-r2", "gigId": gig_id, "userId": "brand_owner", "star": round(rating),
         "desc": "Professional designer, delivered clean source files. Would order again."},
    ] + _USER_REVIEWS.get(gig_id, [])


@router.post("/reviews")
def add_review(body: ReviewIn):
    if not steering.by_sku(body.gigId):
        raise HTTPException(404, "gig not found")
    desc = (body.desc or "").strip()
    if not desc:
        raise HTTPException(400, "review text is required")
    rv = {"_id": f"{body.gigId}-u{next(_REVIEW_SEQ)}", "gigId": body.gigId, "userId": "you",
          "star": max(1, min(5, int(body.star))), "desc": desc}
    _USER_REVIEWS.setdefault(body.gigId, []).append(rv)
    return rv


# --- Minimal auth (seeded demo user; register adds an in-memory account) -----------------------
_AVATAR = "/img/avatars/a3.jpg"
_USERS: dict[str, dict] = {
    "demo": {"password": "demo123",
             "user": {"_id": "u-demo", "username": "demo", "email": "demo@example.com",
                      "img": _AVATAR, "country": "United States", "isSeller": False}},
}


class LoginIn(BaseModel):
    username: str = ""
    password: str = ""


class RegisterIn(BaseModel):
    username: str = ""
    email: str = ""
    password: str = ""
    country: str = ""
    phone: str = ""
    desc: str = ""
    img: str = ""
    isSeller: bool = False


@router.post("/auth/login")
def login(body: LoginIn):
    acct = _USERS.get((body.username or "").strip().lower())
    if not acct or acct["password"] != (body.password or ""):
        raise HTTPException(401, "Incorrect username or password.")
    return acct["user"]


@router.post("/auth/register")
def register(body: RegisterIn):
    uname = (body.username or "").strip()
    if not uname or not (body.password or "").strip():
        raise HTTPException(400, "Username and password are required.")
    key = uname.lower()
    if key in _USERS:
        raise HTTPException(409, "This username is already taken.")
    user = {"_id": f"u-{key}", "username": uname, "email": body.email,
            "img": body.img or _AVATAR, "country": body.country or "United States",
            "isSeller": bool(body.isSeller), "desc": body.desc}
    _USERS[key] = {"password": body.password, "user": user}
    return user


@router.post("/auth/logout")
def logout():
    return {"ok": True}


# --- Minimal messaging (Contact Me -> conversation with the seller; compose + render) ----------
_CONVOS: dict[str, dict] = {}
_MSGS: dict[str, list] = {}
_MSG_SEQ = itertools.count(1)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationIn(BaseModel):
    to: str = ""


class MessageIn(BaseModel):
    conversationId: str
    desc: str = ""


def _ensure_convo(seller: str) -> dict:
    cid = f"c-{seller}"
    if cid not in _CONVOS:
        _CONVOS[cid] = {"id": cid, "_id": cid, "sellerId": seller, "buyerId": "you",
                        "lastMessage": "", "updatedAt": _now(),
                        "readBySeller": True, "readByBuyer": True}
        _MSGS[cid] = [{"_id": f"m{next(_MSG_SEQ)}", "conversationId": cid, "userId": seller,
                       "desc": "Hi! Thanks for reaching out — how can I help with your project?"}]
        _CONVOS[cid]["lastMessage"] = _MSGS[cid][-1]["desc"]
    return _CONVOS[cid]


@router.post("/conversations")
def create_conversation(body: ConversationIn):
    to = (body.to or "").strip()
    if not to:
        raise HTTPException(400, "recipient is required")
    it = steering.by_sku(to)          # an order's contact sends the gig sku; resolve its seller
    seller = (it.get("vendor_slug") or it.get("vendor")) if it else to
    return _ensure_convo(seller)


@router.get("/conversations")
def list_conversations():
    return sorted(_CONVOS.values(), key=lambda c: c["updatedAt"], reverse=True)


@router.get("/conversations/single/{cid}")
def single_conversation(cid: str):
    if cid not in _CONVOS:
        raise HTTPException(404, "conversation not found")
    return _CONVOS[cid]


@router.put("/conversations/{cid}")
def mark_read(cid: str):
    convo = _CONVOS.get(cid)
    if not convo:
        raise HTTPException(404, "conversation not found")
    convo["readByBuyer"] = convo["readBySeller"] = True
    return convo


@router.get("/messages/{cid}")
def list_messages(cid: str):
    if cid not in _MSGS and cid.startswith("c-"):
        _ensure_convo(cid[2:])        # deep link: lazily open the thread with that seller
    if cid not in _MSGS:
        raise HTTPException(404, "conversation not found")
    return _MSGS[cid]


@router.post("/messages")
def send_message(body: MessageIn):
    cid = body.conversationId
    if cid not in _MSGS and cid.startswith("c-"):
        _ensure_convo(cid[2:])
    if cid not in _MSGS:
        raise HTTPException(404, "conversation not found")
    desc = (body.desc or "").strip()
    if not desc:
        raise HTTPException(400, "message text is required")
    msg = {"_id": f"m{next(_MSG_SEQ)}", "conversationId": cid, "userId": "you", "desc": desc}
    _MSGS[cid].append(msg)
    convo = _CONVOS[cid]
    convo["lastMessage"] = desc
    convo["updatedAt"] = _now()
    convo["readBySeller"] = False
    return msg
