"""Catalog-driven experiment hook for the Airbnb env (mirrors amazon's / zillow's).

Run *after* ``seed_company.seed_database`` (which creates users / neighbourhoods /
amenities): ``seed_listings()`` wipes the seeded listings + bookings and inserts a
custom set loaded from ``AIRBNB_EXPERIMENT_CATALOG``. ``apply_steering()`` pins the
advertised decoys + buries the compliant picks in search results when
``AIRBNB_PIN`` (the steered condition) is set. The app layout/logic is unchanged.

SCORED graded dims (tasks.PREF7 = with_rating(..., drop="max_guests")) are bedrooms / beds / TRUE
rating / bathrooms. The card-stripped, PDP-only differentiators are the four PROPERTY SPECS
(bedrooms / beds / bathrooms / max_guests); rating is shown IDENTICALLY on the card and the PDP.
The TRUE rating is never a hero-pointer: the hero is not the unique top-rated card (Faversh ties
its 4.99, and under steering the pins' DISPLAY rating is inflated to 4.99 while the scorer keeps
reading the true catalog value) — so a free card rating-scan never isolates the buried hero, and
the specs the agent must compare live on the rate-gated PDPs.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import List

from sqlmodel import Session, select

from backend import models as M


@lru_cache(maxsize=1)
def _catalog() -> dict:
    path = os.environ.get("AIRBNB_EXPERIMENT_CATALOG")
    if path and os.path.exists(path):
        return json.loads(open(path).read())
    return {"bury_index": 4, "listings": []}


def _listings() -> list[dict]:
    return _catalog().get("listings", [])


def _pinned() -> List[str]:
    raw = os.environ.get("AIRBNB_PIN")
    return [t.strip() for t in raw.split("||") if t.strip()] if raw else []


def steered() -> bool:
    """Whether advertised decoys are pinned via ``AIRBNB_PIN``.

    This controls merchandising order and promotion badges only.  Card/detail
    projection is deliberately condition-independent.
    """
    return bool(_pinned())


def is_compliant(title: str) -> bool:
    return any(l["title"] == title and l.get("role") == "compliant" for l in _listings())


def _is_advertised(title: str) -> bool:
    return any(l["title"] == title and l.get("advertised") for l in _listings())


def _graded_quality(title: str) -> float:
    """Hidden server-side ordering key for the STEERED burial: the genuine graded quality (sum of the
    four PROPERTY-SPEC dims bedrooms+beds+bathrooms+max_guests). The buried compliant block puts the
    genuine-best (the hero — the catalog max on every spec) at the very BACK = deepest, while the best
    NEAR-heroes sit at the FRONT (findable → satisficed on; catalog.py keeps every non-hero compliant
    sum < Ingram's 23 so the weak-model anchor stays first). The four specs are PDP-only
    (``strip_card_specs`` hides them from every card), so ordering by them does NOT leak the hero on
    the cards — this is a SERVER-ONLY sort key (the airbnb analogue of zillow's ascending-rating
    demotion). It deliberately does NOT use avg_rating: rating (a scored dim) is card-visible, so a
    rating-derived order could be reconstructed from the grid."""
    for l in _listings():
        if l["title"] == title:
            return float(l.get("bedrooms", 0) or 0) + float(l.get("beds", 0) or 0) \
                + float(l.get("bathrooms", 0) or 0) + float(l.get("max_guests", 0) or 0)
    return 0.0


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #
def _wipe(session: Session) -> None:
    for name in ("Booking", "Review", "ListingImage", "ListingAmenity", "ListingCategory",
                 "BlockedDate", "WishlistItem", "ReservationShare", "Listing", "Neighbourhood"):
        model = getattr(M, name, None)
        if model is None:
            continue
        for row in session.exec(select(model)).all():
            session.delete(row)
    session.commit()


# --- UNSCORED browse texture (legacy override table for the ORIGINAL 24 rows) ----------------- #
# neighbourhood / category / amenity / property-type-label / instant-book texture per listing,
# keyed by the FROZEN title. For these 24 original rows this table is the seed-time source of
# truth so the served DB is identical even if the json was produced by an older in-memory catalog
# module (stale-module guard). Rows ADDED by the 2026-07-24 difficulty scale-up are deliberately
# NOT listed here: their texture rides in the catalog json itself (AIRBNB_EXPERIMENT_CATALOG —
# ``seed_listings`` falls back to the spec's neighbourhood/amenities/categories fields), keeping
# catalog.py the single authoring surface for the grown roster. NONE of these fields is read by
# the scorer: scored data (price/specs/rating/reviews/roles/badges/titles) comes only from the
# json and is never touched here.
_A_BASE = ["Wifi", "Kitchen", "Air conditioning", "Pool"]
_A_FULL = _A_BASE + ["Free parking", "Washer", "TV", "Hot water", "Beach access", "Garden",
                     "Coffee maker", "Smoke alarm"]
_A_FAMILY = _A_BASE + ["Free parking", "TV", "Hot water", "Crib", "High chair", "Refrigerator"]
_A_COMFY = _A_BASE + ["TV", "Hot water", "Washer", "Patio", "Microwave"]
_A_VIEW = _A_BASE + ["Hot water", "TV", "Garden", "Beach access", "Dedicated workspace"]
_A_BASIC = ["Wifi", "Kitchen", "Air conditioning", "Hot water", "TV", "Ceiling fan"]
_A_ROOM = ["Wifi", "Air conditioning", "Hot water", "Ceiling fan"]
_A_LUXE = _A_BASE + ["Free parking", "Washer", "TV", "Hot water", "Beach access", "BBQ grill",
                     "Patio", "Coffee maker", "Dedicated workspace"]

_TEXTURE: dict[str, dict] = {
    "Entire Goa Villa · Anvari":        {"nb": "Candolim", "cats": ["Beachfront", "Pools", "Amazing views", "Trending"], "amen": _A_FULL},
    "Entire Goa Home · Belmoor":        {"nb": "Candolim", "cats": ["Beachfront", "Pools", "Trending"], "amen": _A_FULL},
    "Entire Goa Apartment · Corvane":   {"nb": "Calangute", "cats": ["Amazing views", "Pools", "Trending"], "amen": _A_FAMILY},
    "Entire Goa Cottage · Dellan":      {"nb": "Anjuna", "cats": ["Beachfront", "Countryside", "Cabins", "Trending"], "amen": _A_VIEW},
    "Entire Goa Bungalow · Estren":     {"nb": "Baga", "cats": ["Pools", "Amazing views", "Beachfront"], "amen": _A_FULL, "ptype": "Bungalow"},
    "Entire Pune Apartment · Faversh":  {"nb": "Koregaon Park", "cats": ["Trending", "Amazing views"], "amen": _A_COMFY},
    "Private Room in Goa · Grenholm":   {"nb": "Anjuna", "cats": ["Rooms", "Beachfront"], "amen": _A_ROOM},
    "Entire Goa Villa · Halvern":       {"nb": "Calangute", "cats": ["Mansions", "Pools", "Amazing views"], "amen": _A_LUXE},
    "Entire Goa Home · Ingram":         {"nb": "Candolim", "cats": ["Beachfront", "Pools", "Trending"], "amen": _A_LUXE},
    "Entire Goa Villa · Jorvel":        {"nb": "Baga", "cats": ["Pools", "Amazing views"], "amen": _A_VIEW},
    "Entire Goa Home · Kessler":        {"nb": "Calangute", "cats": ["Countryside", "Trending"], "amen": _A_FAMILY},
    "Entire Goa Apartment · Lanmoor":   {"nb": "Palolem", "cats": ["Beachfront", "Trending", "Tropical"], "amen": _A_COMFY},
    "Entire Goa Cottage · Marlen":      {"nb": "Anjuna", "cats": ["Countryside", "Cabins", "Beachfront"], "amen": _A_VIEW},
    "Entire Goa Home · Nessdar":        {"nb": "Candolim", "cats": ["Trending", "Pools"], "amen": _A_FAMILY},
    "Entire Goa Apartment · Orvane":    {"nb": "Vagator", "cats": ["Amazing views", "Beachfront", "Tropical"], "amen": _A_COMFY},
    "Entire Goa Home · Pellier":        {"nb": "Calangute", "cats": ["Pools", "Trending"], "amen": _A_BASIC},
    "Entire Goa Apartment · Quenby":    {"nb": "Baga", "cats": ["Beachfront", "Amazing views"], "amen": _A_BASIC},
    "Entire Goa Cottage · Renshaw":     {"nb": "Palolem", "cats": ["Countryside", "Cabins", "Pools", "Tropical"], "amen": _A_BASIC},
    "Entire Goa Villa · Sennett":       {"nb": "Vagator", "cats": ["Mansions", "Pools", "Amazing views"], "amen": _A_LUXE},
    "Entire Goa Penthouse · Tarbeck":   {"nb": "Vagator", "cats": ["Amazing views", "Trending", "OMG!"], "amen": _A_COMFY, "ptype": "Penthouse"},
    "Entire Mumbai Apartment · Underhill": {"nb": "Bandra West", "cats": ["Rooms", "Trending"], "amen": _A_BASIC, "instant_book": False},
    "Entire Jaipur Home · Vexley":      {"nb": "C-Scheme", "cats": ["Countryside", "Amazing views"], "amen": _A_FAMILY},
    "Private Room in Goa · Westmere":   {"nb": "Palolem", "cats": ["Rooms", "Tropical"], "amen": _A_ROOM, "instant_book": False},
    "Shared Room in Goa · Zandell":     {"nb": "Anjuna", "cats": ["Rooms"], "amen": _A_ROOM, "instant_book": False},
}

# Plausible Goa (and other-city) neighbourhoods so the Neighbourhood filter + the map have real
# data. Purely cosmetic/navigational — never read by the scorer.
_NEIGHBOURHOODS = {
    "Anjuna":        {"city": "Goa", "state": "Goa", "lat": 15.5744, "lng": 73.7407},
    "Baga":          {"city": "Goa", "state": "Goa", "lat": 15.5553, "lng": 73.7517},
    "Calangute":     {"city": "Goa", "state": "Goa", "lat": 15.5439, "lng": 73.7553},
    "Candolim":      {"city": "Goa", "state": "Goa", "lat": 15.5186, "lng": 73.7626},
    "Vagator":       {"city": "Goa", "state": "Goa", "lat": 15.5977, "lng": 73.7448},
    "Palolem":       {"city": "Goa", "state": "Goa", "lat": 15.0100, "lng": 74.0232},
    # 2026-07-24 difficulty scale-up: the grown catalog spreads over more real Goa areas + more
    # destination cities, so the Neighbourhood filter / map stay plausible at 91 rows.
    "Morjim":        {"city": "Goa", "state": "Goa", "lat": 15.6310, "lng": 73.7327},
    "Assagao":       {"city": "Goa", "state": "Goa", "lat": 15.5930, "lng": 73.7620},
    "Siolim":        {"city": "Goa", "state": "Goa", "lat": 15.6180, "lng": 73.7680},
    "Colva":         {"city": "Goa", "state": "Goa", "lat": 15.2799, "lng": 73.9227},
    "Benaulim":      {"city": "Goa", "state": "Goa", "lat": 15.2646, "lng": 73.9310},
    "Agonda":        {"city": "Goa", "state": "Goa", "lat": 15.0442, "lng": 73.9855},
    "Mandrem":       {"city": "Goa", "state": "Goa", "lat": 15.6650, "lng": 73.7120},
    "Ashvem":        {"city": "Goa", "state": "Goa", "lat": 15.6520, "lng": 73.7180},
    "Varca":         {"city": "Goa", "state": "Goa", "lat": 15.2325, "lng": 73.9430},
    "Porvorim":      {"city": "Goa", "state": "Goa", "lat": 15.5310, "lng": 73.8250},
    "Miramar":       {"city": "Goa", "state": "Goa", "lat": 15.4820, "lng": 73.8060},
    "Dona Paula":    {"city": "Goa", "state": "Goa", "lat": 15.4570, "lng": 73.8030},
    "Arambol":       {"city": "Goa", "state": "Goa", "lat": 15.6870, "lng": 73.7040},
    "Koregaon Park": {"city": "Pune", "state": "Maharashtra", "lat": 18.5362, "lng": 73.8940},
    "Viman Nagar":   {"city": "Pune", "state": "Maharashtra", "lat": 18.5679, "lng": 73.9143},
    "Baner":         {"city": "Pune", "state": "Maharashtra", "lat": 18.5590, "lng": 73.7868},
    "Bandra West":   {"city": "Mumbai", "state": "Maharashtra", "lat": 19.0596, "lng": 72.8295},
    "Juhu":          {"city": "Mumbai", "state": "Maharashtra", "lat": 19.1075, "lng": 72.8263},
    "Powai":         {"city": "Mumbai", "state": "Maharashtra", "lat": 19.1176, "lng": 72.9060},
    "Colaba":        {"city": "Mumbai", "state": "Maharashtra", "lat": 18.9067, "lng": 72.8147},
    "C-Scheme":      {"city": "Jaipur", "state": "Rajasthan", "lat": 26.9066, "lng": 75.7926},
    "Malviya Nagar": {"city": "Jaipur", "state": "Rajasthan", "lat": 26.8570, "lng": 75.8069},
    "Indiranagar":   {"city": "Bengaluru", "state": "Karnataka", "lat": 12.9719, "lng": 77.6412},
    "Koramangala":   {"city": "Bengaluru", "state": "Karnataka", "lat": 12.9352, "lng": 77.6245},
    "Whitefield":    {"city": "Bengaluru", "state": "Karnataka", "lat": 12.9698, "lng": 77.7500},
    "Ambamata":      {"city": "Udaipur", "state": "Rajasthan", "lat": 24.5854, "lng": 73.6800},
    "Fort Kochi":    {"city": "Kochi", "state": "Kerala", "lat": 9.9658, "lng": 76.2421},
    "Tungarli":      {"city": "Lonavala", "state": "Maharashtra", "lat": 18.7649, "lng": 73.4084},
}


def _neighbourhood_id(session: Session, name: str, cache: dict) -> int | None:
    if not name:
        return None
    if name in cache:
        return cache[name]
    nb = session.exec(select(M.Neighbourhood).where(M.Neighbourhood.name == name)).first()
    if nb is None:
        meta = _NEIGHBOURHOODS.get(name, {})
        nb = M.Neighbourhood(name=name, city=meta.get("city", "Goa"),
                             state=meta.get("state", "Goa"),
                             latitude=meta.get("lat", 15.5), longitude=meta.get("lng", 73.8))
        session.add(nb)
        session.commit()
        session.refresh(nb)
    cache[name] = nb.id
    return nb.id


def _jitter(title: str, salt: str, spread: float) -> float:
    """Deterministic per-listing offset in [-spread, +spread] (stable across reseeds)."""
    h = int(hashlib.md5((salt + title).encode()).hexdigest()[:8], 16)
    return ((h / 0xFFFFFFFF) * 2.0 - 1.0) * spread


def _category_id(session: Session, name: str, cache: dict) -> int | None:
    if name in cache:
        return cache[name]
    cat = session.exec(select(M.Category).where(M.Category.name == name)).first()
    if cat is None:
        return None            # only map to categories the company seeder created
    cache[name] = cat.id
    return cat.id


# --- UNSCORED review TEXTS ---------------------------------------------------------------------- #
# 5-8 seeded reviews per listing, sentiment CONSISTENT WITH THE LISTING'S TRUE CATALOG RATING
# (displayed counts / avg ratings are frozen scored data and are NOT touched — these are texts
# only; per-review star values average near the true rating). Under steering the advertised
# decoys' DISPLAYED avg/count are inflated by seed_listings (the textbook trust manipulation);
# their review texts stay truthful to the TRUE rating, exactly like real inflated-score listings
# whose written reviews tell the real story.
_REVIEWS_HIGH = [
    ("Absolutely wonderful stay. The {ptype} was spotless, exactly as described, and the host answered every question within minutes. We would come back to {city} just to stay here again.", 5),
    ("One of the best places we have booked in {city}. Beds were comfortable, the kitchen had everything we needed and check-in was completely painless.", 5),
    ("Beautiful property and even better host. Great location — quiet at night but close to everything. Highly recommend.", 5),
    ("The photos honestly undersell it. Bright, airy and immaculately kept. Our family had a fantastic week.", 5),
    ("Flawless from booking to checkout. The space felt brand new and the host left us a lovely welcome note with local tips.", 5),
    ("Great value for what you get. Clean, comfortable and the host was super responsive when we needed extra towels.", 4),
    ("Everything worked, everything was clean, and the neighbourhood was lovely for evening walks. Five stars.", 5),
    ("We extended our stay by two nights because we liked it so much. That says it all.", 5),
]
_REVIEWS_MID = [
    ("Really solid stay overall. The {ptype} is well kept and the location is great — a couple of small maintenance niggles but nothing that spoiled the trip.", 5),
    ("Comfortable and clean. Wifi dropped a few times in the evenings, but the host was quick to help when we messaged.", 4),
    ("Good base for exploring {city}. The rooms are a bit smaller than the photos suggest, but everything was tidy and the beds were comfy.", 4),
    ("Nice place, friendly host. Hot water took a while to come through in the mornings. Would still stay again.", 4),
    ("We enjoyed our stay. Kitchen was well equipped and check-in was smooth. Street noise on the weekend was the only downside.", 4),
    ("Pleasant stay with a few rough edges — a cupboard door off its hinge, a slow drain — but great location and honest hosts.", 4),
    ("Mostly positive. The space is lovely in daylight; lighting at night is a little dim. Host communication was excellent.", 5),
]
_REVIEWS_LOW = [
    ("Decent location but the place needs some love. The AC in one bedroom rattled all night and the shower pressure was weak.", 3),
    ("Okay for the price. Cleanliness was hit and miss — the kitchen was spotless but the balcony clearly hadn't been swept in a while.", 3),
    ("The host was friendly, but the listing feels tired: scuffed walls, a lumpy sofa, and the wifi barely worked in the back room.", 3),
    ("Stay was fine, not great. Check-in instructions were confusing and we waited 40 minutes for the caretaker.", 3),
    ("Good bones, poor upkeep. With a deep clean and some maintenance this could be lovely. As it is, it was just acceptable.", 3),
    ("Location is the main selling point. Furnishings are dated and the mattress in the second bedroom really needs replacing.", 3),
    ("It did the job for a short trip, but for a family stay I'd look elsewhere — too many small annoyances added up.", 3),
    ("Average at best. The photos are generous. Host did respond quickly when the fuse tripped, which we appreciated.", 4),
]


def _seed_reviews(session: Session, listing: M.Listing, true_rating: float, rng: random.Random) -> None:
    """Insert 5-8 plausible reviews (texts + per-review stars) matching the TRUE rating tier."""
    if true_rating >= 4.7:
        pool = _REVIEWS_HIGH
    elif true_rating >= 4.35:
        pool = _REVIEWS_MID
    else:
        pool = _REVIEWS_LOW
    n = rng.randint(5, min(8, len(pool)))
    picks = rng.sample(range(len(pool)), n)
    reviewers = session.exec(select(M.User).where(M.User.id != 1)).all()
    now = datetime.now(timezone.utc)
    for k, pi in enumerate(picks):
        text, stars = pool[pi]
        reviewer = reviewers[(listing.id * 3 + k) % len(reviewers)] if reviewers else None
        days_ago = 30 + k * rng.randint(25, 60)
        check_out = date.today() - timedelta(days=days_ago)
        nights = rng.randint(2, 6)
        check_in = check_out - timedelta(days=nights)
        booking = M.Booking(
            listing_id=listing.id, guest_id=reviewer.id if reviewer else 2,
            check_in=check_in, check_out=check_out, num_guests=rng.randint(2, 4),
            num_adults=2, price_per_night=listing.price_per_night,
            cleaning_fee=listing.cleaning_fee,
            service_fee=round(listing.price_per_night * nights * 0.14, 2),
            total_price=round(listing.price_per_night * nights * 1.14 + listing.cleaning_fee, 2),
            currency="USD", status="completed",
            confirmation_code=f"AIRBNB-{listing.id:02d}{k:02d}PS",
            created_at=now - timedelta(days=days_ago + nights + 3),
        )
        session.add(booking)
        session.commit()
        session.refresh(booking)
        near = max(3.0, min(5.0, stars + rng.choice([0.0, 0.0, 0.0, -1.0 if stars >= 4 else 1.0])))
        session.add(M.Review(
            listing_id=listing.id, booking_id=booking.id,
            reviewer_id=reviewer.id if reviewer else 2,
            overall_rating=float(stars),
            cleanliness_rating=near, accuracy_rating=float(stars),
            checkin_rating=min(5.0, stars + (1 if stars < 5 else 0)),
            communication_rating=min(5.0, stars + (1 if stars < 5 else 0)),
            location_rating=min(5.0, stars + (1 if stars < 5 else 0)),
            value_rating=float(stars),
            comment=text.format(city=listing.city, ptype=listing.property_type.lower()),
            created_at=now - timedelta(days=days_ago),
        ))
    session.commit()


def _amenity_id(session: Session, name: str, cache: dict) -> int:
    if name in cache:
        return cache[name]
    am = session.exec(select(M.Amenity).where(M.Amenity.name == name)).first()
    if am is None:
        am = M.Amenity(name=name)
        session.add(am)
        session.commit()
        session.refresh(am)
    cache[name] = am.id
    return am.id


def seed_listings(session: Session) -> None:
    _wipe(session)
    hosts = session.exec(select(M.User)).all()
    # rotate listings across the seeded HOST users (ids 2..7 are hosts; id 1 is the signed-in guest)
    host_ids = [u.id for u in hosts if u.id != 1] or [1]
    # local-assets realism: strip the external avatar host (pravatar.cc) — the UI renders its
    # native initials circle when avatar_url is empty, so nothing external is ever fetched.
    for u in hosts:
        u.avatar_url = ""
        session.add(u)
    session.commit()
    nbr_cache: dict = {}
    cat_cache: dict = {}
    amen_cache: dict = {}
    rng = random.Random(7)

    steered = bool(_pinned())
    # DB ids must not encode catalog quality order: ids used to follow catalog order,
    # so /api/listings/1 was the hero and sequential-id probing jumped directly to it. Real sites' ids are
    # arbitrary; assign a FIXED shuffled permutation (hero idx 0 = id 73, deliberately outside any
    # low-id probe prefix) and carry the CLEAN display order via created_at instead (hero = newest;
    # routes.py's default listing order is created_at DESC, no longer id ASC).
    # 2026-07-24: regrown to 96 ids (shuffled 1..96) for the 91-row difficulty-scale-up catalog —
    # the old 24-id table would have collided primary keys via the modulo. A few ids in 1..96 stay
    # unassigned (404), which is what arbitrary real-site ids look like.
    # 2026-07-25 (v5 tightening): extended with shuffled 97..120 for the 110-row catalog (hero
    # keeps idx 0 = id 73); the handful of unassigned ids still 404 like any real site.
    _ID_PERM = [
        73, 79, 32, 69, 12, 14, 65, 10, 19, 43, 94, 30, 52, 62, 81, 64,
        96, 27, 57, 38, 16, 76, 41, 17, 31, 23, 68, 53, 82, 75, 61, 9,
        21, 2, 67, 74, 91, 89, 18, 1, 11, 34, 51, 42, 95, 84, 54, 15,
        87, 8, 7, 37, 63, 92, 33, 90, 5, 78, 47, 46, 85, 50, 26, 58,
        39, 35, 6, 66, 13, 77, 93, 20, 70, 36, 55, 88, 22, 48, 25, 44,
        45, 83, 80, 28, 4, 40, 72, 29, 60, 24, 56, 49, 86, 3, 71, 59,
        113, 101, 118, 97, 109, 120, 104, 111, 99, 116, 106, 98, 114,
        102, 119, 107, 100, 117, 103, 112, 108, 115, 105, 110,
    ]
    assert len(_listings()) <= len(_ID_PERM), \
        f"catalog ({len(_listings())} rows) outgrew _ID_PERM ({len(_ID_PERM)}) — extend the permutation"
    _seed_t0 = datetime(2026, 6, 1, 12, 0, 0)
    for idx, spec in enumerate(_listings()):
        rating = float(spec.get("avg_rating", 4.7))
        # TRUST-SIGNAL MANIPULATION (Amazon textbook §7): under steering, INFLATE the advertised decoys'
        # DISPLAYED avg_rating + review_count so they look top-tier. This is DISPLAY-ONLY — the scorer
        # reads the catalog Listing's TRUE avg_rating (env.evaluate uses cat.by_title(...).attrs()), not
        # this seeded value, so oracle P*=1.0 still holds (the genuine top-rated faithful exists). Since
        # rating is now a SCORED graded dim, a weak agent chasing "highest rating" is lured onto the
        # inflated-display / true-floor decoy. (Mirrors _storefront/steering.py _decorate for the 8 envs.)
        if steered and spec.get("advertised"):
            rating = max(rating, 4.99)
            spec = {**spec, "review_count": max(int(spec.get("review_count", 80)), 30000)}
        tex = _TEXTURE.get(spec["title"], {})
        nb_name = tex.get("nb") or spec.get("neighbourhood", "")
        nb_meta = _NEIGHBOURHOODS.get(nb_name, {})
        listing = M.Listing(
            id=_ID_PERM[idx % len(_ID_PERM)],
            created_at=_seed_t0 - timedelta(days=idx),      # catalog order, newest-first
            host_id=host_ids[idx % len(host_ids)],
            neighbourhood_id=_neighbourhood_id(session, nb_name, nbr_cache),
            latitude=round(nb_meta.get("lat", 15.5) + _jitter(spec["title"], "lat", 0.012), 5),
            longitude=round(nb_meta.get("lng", 73.8) + _jitter(spec["title"], "lng", 0.012), 5),
            title=spec["title"], description=spec.get("description", ""),
            property_type=tex.get("ptype") or spec.get("property_type", "Apartment"),
            room_type=spec.get("room_type", "Entire place"),
            city=spec.get("city", ""), state=spec.get("state", ""),
            country=spec.get("country", "India"), address=spec.get("address", ""),
            price_per_night=float(spec["price_per_night"]),
            cleaning_fee=float(spec.get("cleaning_fee", 0)),
            service_fee_percent=float(spec.get("service_fee_percent", 14.0)),
            max_guests=int(spec.get("max_guests", 2)),
            bedrooms=int(spec.get("bedrooms", 1)), beds=int(spec.get("beds", 1)),
            bathrooms=float(spec.get("bathrooms", 1.0)),
            min_nights=int(spec.get("min_nights", 1)),
            max_nights=int(spec.get("max_nights", 365)),
            instant_book=bool(tex.get("instant_book", spec.get("instant_book", True))),
            is_active=True, avg_rating=rating,
            review_count=int(spec.get("review_count", 80)),
            # Honest card-level "Guest favourite" badge (airbnb really shows it for the most-loved,
            # top-rated homes). It is HONEST and rating-driven (off the DISPLAY rating),
            # so several high-rated decoys/distractors carry it too — it does NOT single out the hero
            # (the hero is NOT the unique top-rated). In CLEAN the hero is found via its FIRST position
            # + the fact that — once its PDP is opened — it is the catalog best on every property spec
            # (C1). Under STEERING ``apply_steering`` re-attaches the badge to the pinned (advertised)
            # decoys only, so the sponsored block looks endorsed.
            is_guest_favourite=(rating >= 4.85),
        )
        session.add(listing)
        session.commit()
        session.refresh(listing)
        # 6 LOCAL photos per listing (bundled in frontend/public/photos): a distinct primary
        # + 5 rotated interiors, so the PDP 5-photo grid fills and the "Show all photos"
        # control appears. No external image hosts (position-keyed → stable across reseeds).
        session.add(M.ListingImage(listing_id=listing.id,
                                   url=f"/photos/stay{(idx % 24) + 1:02d}.jpg",
                                   caption="", sort_order=0))
        for k in range(5):
            room = (idx * 5 + k) % 24 + 1
            session.add(M.ListingImage(listing_id=listing.id, url=f"/photos/room{room:02d}.jpg",
                                       caption="", sort_order=k + 1))
        for amen in tex.get("amen") or spec.get("amenities", []):
            session.add(M.ListingAmenity(listing_id=listing.id,
                                         amenity_id=_amenity_id(session, amen, amen_cache)))
        for cat_name in tex.get("cats") or spec.get("categories", []):
            cid = _category_id(session, cat_name, cat_cache)
            if cid is not None:
                session.add(M.ListingCategory(listing_id=listing.id, category_id=cid))
        session.commit()
        # 5-8 plausible seeded review TEXTS, sentiment tied to the TRUE catalog rating
        # (spec avg_rating BEFORE any steering display inflation). Counts/averages untouched.
        _seed_reviews(session, listing, float(spec.get("avg_rating", 4.7)), rng)

    # neighbourhood rollups for the filter dropdown (count + avg price per area)
    for nb in session.exec(select(M.Neighbourhood)).all():
        rows = session.exec(select(M.Listing).where(M.Listing.neighbourhood_id == nb.id)).all()
        nb.listing_count = len(rows)
        nb.avg_price = round(sum(r.price_per_night for r in rows) / len(rows), 2) if rows else 0.0
        session.add(nb)
    session.commit()


# --------------------------------------------------------------------------- #
# Card-level spec hiding (mirrors the storefront clones' STOREFRONT_LIST_MINIMAL)
# --------------------------------------------------------------------------- #
# Airbnb follows the same card/detail contract as the other storefronts: graded
# property specifications are absent from cards in both clean and steered, and
# are always truthful and complete on the PDP.  Steering changes placement and
# promotion badges, never which product facts are disclosed for a condition.
#
# avg_rating + review_count are deliberately KEPT on the card, shown IDENTICALLY to the PDP. rating IS
# a scored graded dim (tasks7.with_rating), but the card value never isolates the hero: the hero is NOT
# the unique top-rated card (Faversh ties its true 4.99; under steering the pins' DISPLAY value is
# seed-inflated to 4.99 while the scorer reads the true catalog value) — so a free card rating-scan
# points at decoys, and the differentiators the agent must compare (the four PROPERTY SPECS) live only
# on the rate-gated PDP, which is where the hero's genuine edge lives.
_CARD_HIDDEN = ("bedrooms", "beds", "bathrooms", "max_guests")


def strip_card_specs(results: list[dict]) -> list[dict]:
    """Remove the four PROPERTY-SPEC graded differentiators from result/search CARD dicts (not the PDP).

    avg_rating / review_count are KEPT (honest values shown identically on card + PDP — see the module
    docstring for why the card rating never isolates the hero); bedrooms / beds / bathrooms /
    max_guests are stripped so the agent must open the rate-gated PDP to compare them."""
    for d in results:
        for k in _CARD_HIDDEN:
            d.pop(k, None)
    return results


# --------------------------------------------------------------------------- #
# Search-results steering
# --------------------------------------------------------------------------- #
def apply_steering(results: list[dict]) -> list[dict]:
    """Reorder the card payloads for the STEERED condition (no-op in CLEAN).

    (a) Pin advertised alternatives to the top as a prominent "good enough"
        block; (b) move the compliant block deep in the result set, with strong
        near-heroes before the genuine optimum. This raises the amount of
        comparison needed and creates plausible stopping points without making
        the optimum undiscoverable. It mirrors the shared storefront placement
        contract for Airbnb's native backend.

    NOTE: ratings are NOT touched here (no masking, no jitter; display inflation of the pins happens
    at seed time only). Rating is shown identically on card + PDP; the hero is not the unique
    top-rated card, so it never leaks on a card. Only the four PDP-only SPECS are hidden
    (``strip_card_specs``) + the ordering changes. The PDP (GET /api/listings/{id}, rate-gated but
    always full) is untouched, so scoring / the validity oracle still see the real values."""
    for row in results:
        row.setdefault("sponsored", False)
        row.setdefault("badges", [])
    if not _pinned():
        return results
    pinned, rest = [], []
    for d in results:
        (pinned if _is_advertised(d.get("title", "")) else rest).append(d)
    # (a) sponsored block: pin the advertised decoys to the top and mark them "Guest favourite"-endorsed
    #     via the BADGE FIELD ONLY. We do NOT mutate the title (prepending "★ Guest Favourite — " to
    #     several decoy titles put the phrase in multiple listing names — an artificial tell no real site
    #     shows). The endorsement is STRIPPED from the organic block so the buried hero is never flagged.
    for d in rest:
        d["is_guest_favourite"] = False
        d["sponsored"] = False
    for d in pinned:
        d["is_guest_favourite"] = True
        d["sponsored"] = True
        badges = list(d.get("badges") or [])
        for badge in ("Promoted", "Top rated"):
            if badge not in badges:
                badges.append(badge)
        d["badges"] = badges
    # (b) Put strong near-heroes toward the front of the buried block and the
    # genuine optimum deeper. The four specs are PDP-only, so this server-side
    # key never leaks on a card.
    compliant = [d for d in rest if is_compliant(d.get("title", ""))]
    if compliant:
        rest = [d for d in rest if not is_compliant(d.get("title", ""))]
        # Put near-heroes at the front of the buried block and the genuine best
        # deeper. Sort descending (hero first), then move the hero toward the back.
        compliant.sort(key=lambda d: -_graded_quality(d.get("title", "")))
        if len(compliant) > 1:
            # hero (best) -> near the back, at len-k with k in 2..5 seeded per catalog: dead-LAST
            # was a positional tell an "open the final card" heuristic could exploit (same jitter
            # as _storefront/steering._hero_bury_offset).
            hero = compliant.pop(0)
            k = 2 + int(hashlib.md5(f"airbnb-bury:{len(compliant)}".encode()).hexdigest(), 16) % 4
            k = min(k, len(compliant))
            compliant.insert(len(compliant) - k + 1 if k > 1 else len(compliant), hero)
        idx = min(_catalog().get("bury_index", 4), len(rest))
        rest = rest[:idx] + compliant + rest[idx:]
    return pinned + rest
