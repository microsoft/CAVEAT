"""Auditable clone-storefront surface policy.

Every shopper route that discloses catalog information is classified here.  The
shared app and the certification suite consume the same table, preventing a new
compatibility endpoint from becoming an unmetered enumeration bypass.
"""

from __future__ import annotations

from dataclasses import dataclass

PAGE_SIZE = 24


@dataclass(frozen=True)
class Surface:
    pattern: str
    kind: str                 # card_list | detail | card_rail | transaction
    paged: bool = False


SURFACES = (
    Surface(r"^/api/products$", "card_list", True),
    Surface(r"^/api/storefront$", "card_list", False),       # DoorDash long menu
    Surface(r"^/api/products/[^/]+$", "detail"),
    Surface(r"^/api/gigs$", "card_list", True),
    Surface(r"^/api/gigs/single/[^/]+$", "detail"),
    Surface(r"^/api/reviews/[^/]+$", "detail"),
    Surface(r"^/ebay/products$", "card_list", True),
    Surface(r"^/ebay/products/search-by-name/.*$", "card_list", True),
    Surface(r"^/ebay/products/get-random$", "card_rail"),
    Surface(r"^/ebay/product/[^/]+$", "detail"),
    Surface(r"^/etsy/products$", "card_list", True),
    Surface(r"^/etsy/search_products$", "card_list", True),
    Surface(r"^/etsy/products/\d+$", "detail"),
    Surface(r"^/etsy/products/\d+/reviews$", "detail"),
    Surface(r"^/stockx/sneakers/?$", "card_list", True),
    Surface(r"^/stockx/search/.*$", "card_list", True),
    Surface(r"^/stockx/sneakers/\d+$", "detail"),
    Surface(r"^/stockx/sneakers/\d+/listingitems$", "card_rail"),
    Surface(r"^/stockx/listingitems/\d+$", "card_rail"),
    Surface(r"^/stockx/follows$", "card_list", True),
    # POST/DELETE both return the caller-selected product card.  Middleware only
    # prices GET reads, so stockx_api explicitly charges this declared mutation via
    # gate.count_identities().
    Surface(r"^/api/follows$", "card_mutation"),
)

COUNTED_PATHS = tuple(s.pattern for s in SURFACES if s.kind != "transaction")
PAGED_PATHS = tuple(s.pattern for s in SURFACES if s.paged)


def inventory() -> list[dict]:
    return [{"pattern": s.pattern, "kind": s.kind, "paged": s.paged} for s in SURFACES]
