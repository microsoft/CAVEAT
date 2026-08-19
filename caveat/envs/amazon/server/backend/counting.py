"""WHAT THE RATE GATE CHARGES FOR — the storefront's counted surface, in one auditable place.

``backend/app.py`` used to inline the ``counted_paths`` tuple in its ``gate.install(...)``
call. That made the list invisible to everything except the running server, and the gap it
hid was fatal: ``/api/sellers/{id}/products`` was never counted, so walking the seller
storefront (``?page=1..14&limit=100``) recovered the WHOLE catalog in steered order for zero
counted units — a free, complete enumeration alongside a SERP that charges one unit per 24
rows. Same for the PDP rails and the home shelves.

The policy therefore lives here, as data:

``LEGACY_COUNTED``
    the historical four regexes. Every catalog WITHOUT a ``serving`` object — i.e. all five
    original scenarios and the stock demo store — gets exactly this tuple, byte for byte, so
    their rate behaviour is unchanged by construction.
``HARD_EXTRA_COUNTED``
    added ON TOP for a catalog that carries a ``serving`` object: every other endpoint that
    can hand back rows drawn from a whole-table product query, INCLUDING the session
    containers (``CONTAINER_COUNTED``) — see below.
``IDENTITY_RULES``
    how a counted path maps onto a distinct-count identity (see ``_storefront/gate.py``).
    Inert outside ``count_mode == "distinct"``, which only the hard tier selects.
``CATALOG_ENDPOINTS``
    the DECLARED list of router paths that can enumerate the catalog, with the bound each
    one serves under. Three independent checks keep it honest:
      * ``benchmark/validate.py``'s H12 asserts every entry is matched by
        :func:`counted_paths` for a hard catalog — "able to enumerate => counted";
      * the CAVEAT certification scripts assert that the list still equals what the ROUTER
        actually exposes and exercise every ``CONTAINER_ENDPOINTS`` and
        ``PRODUCT_WRITE_ENDPOINTS`` entry against a live server, so those declarations
        cannot drift from behaviour.

THE SECOND GAP (closed 2026-07-26), same shape as the first. ``/api/wishlists/{id}``,
``/api/registries/{id}``, ``/api/history``, ``/api/price-watch`` and ``/api/subscriptions``
were exempted as "session-scoped" on the premise — written into this file's own
``SESSION_SCOPED`` docstring — that "their result set is the SESSION's own rows, so they
cannot list an item the agent has not already reached through a counted path". That premise
was false for four of them: ``POST /api/history/{product_id}``, ``POST
/api/wishlists/{id}/items``, ``POST /api/registries/{id}/items`` and ``POST
/api/price-watch`` all accept an ARBITRARY ``product_id`` with no prior read, product ids
are sequential ``1..N``, and writes are never counted. So the session could compose its own
listing of the whole catalog and read it back for zero units — measured live on
laptop_hard: 150 consecutive ``GET /api/wishlists/1`` gave 150x200 / 0x503 while the SERP
control gave 12x200 / 108x503, and one 436 KB response carried 400 products' full
``technical_details``. Both halves are now closed:

  * the payload is CARD-level (``routes.as_container_card_dict``), like every other listing;
  * the read is counted per DISTINCT product it discloses, not once
    (``routes._charge_container`` -> ``gate.count_identities`` with ``PRODUCT_IDENTITY``),
    and so is the write that names a product — so a product costs ONE unit no matter which
    channel discloses it, and composing a container is not a way to avoid paying.

This module imports nothing (not even fastapi): the validator loads it by file path, exactly
as it loads ``placement.py``.
"""

from __future__ import annotations

import re

__all__ = ["LEGACY_COUNTED", "HARD_EXTRA_COUNTED", "CONTAINER_COUNTED", "IDENTITY_RULES",
           "PRODUCT_IDENTITY", "product_identity", "CATALOG_ENDPOINTS",
           "CONTAINER_ENDPOINTS", "PRODUCT_WRITE_ENDPOINTS", "SESSION_SCOPED",
           "counted_paths", "uncovered"]

#: The historical counted surface. DO NOT EDIT: the original five scenarios' rate behaviour
#: is defined by this tuple, and lockdiff/pytest both pin it.
LEGACY_COUNTED: tuple = (
    r"^/api/products$",             # catalog list
    r"^/api/search$",               # search (not /search/suggestions|history)
    r"^/api/products/asin/[^/]+$",  # product detail by asin
    r"^/api/products/\d+$",         # product detail by id (not /related etc.)
)

#: SESSION CONTAINERS: listings the session COMPOSES for itself, one write per product, with
#: no read of the product required first. They are not "the session's own rows" in any sense
#: that bounds them — the session's own rows can be the whole catalog — so they are counted
#: exactly like any other listing, and (see ``routes._charge_container``) per DISTINCT product
#: disclosed rather than once per request.
CONTAINER_COUNTED: tuple = (
    r"^/api/wishlists/\d+$",                            # saved-items list (any product_id)
    r"^/api/registries/\d+$",                           # registry items (any product_id)
    r"^/api/history$",                                  # browsing history (any product_id)
    r"^/api/price-watch$",                              # watch list (any product_id)
    r"^/api/subscriptions$",                            # subscribe & save (any product_id)
)

#: Everything else that can return rows from a whole-table ``select(Product)``. Only applied
#: when the served catalog carries a ``serving`` object (the hard tier).
HARD_EXTRA_COUNTED: tuple = (
    r"^/api/sellers/\d+/products$",                     # seller storefront (the F5 leak)
    r"^/api/categories/[^/]+/products$",                # category browse == a second SERP
    r"^/api/products/\d+/(related|similar|frequently-bought)$",   # PDP rails
    r"^/api/products/\d+/variants$",                    # per-product configuration prices
    r"^/api/products/(best-sellers|new-releases|movers-shakers|trending)$",  # home shelves
    r"^/api/recommendations(/[^/]+)?$",                 # rating/bought-ranked shelves
    r"^/api/deals(/(lightning|today|coupons))?$",       # deal shelves carry product rows
    r"^/api/search/suggestions$",                       # prefix-walkable title enumeration
    r"^/api/buy-again$",                                # order-derived, but joins Product
) + CONTAINER_COUNTED

#: Distinct-count identities. The product rules collapse the two URL spellings of one product
#: AND its rails onto a single identity, so rendering a PDP in the SPA (detail + "also
#: viewed" + "similar") costs ONE unit — the same as a scripted fetch of that product's
#: specs. Without this, counting the rails would have re-created the scraping subsidy in the
#: opposite direction (3 units for a browser agent, 1 for a script).
IDENTITY_RULES: tuple = (
    (r"^/api/products/asin/(?P<asin>[^/?]+)$", "product:{asin}"),
    (r"^/dp/(?P<asin>[^/?]+)$", "product:{asin}"),
    (r"^/api/products/(?P<pid>\d+)(?:/.*)?$", "product#{pid}"),
)

#: The id-form product identity, as a template — the SAME one ``IDENTITY_RULES``' third rule
#: produces for ``/api/products/{id}``. A container read (and a write that names a product)
#: charges through this, so "disclosed in a wishlist" and "opened as a PDP" are one identity
#: and one charge, in whichever order they happen.
PRODUCT_IDENTITY = "product#{pid}"


def product_identity(pid) -> str:
    """``product#<id>`` — the distinct-count identity of one product row."""
    return PRODUCT_IDENTITY.format(pid=pid)

#: Router paths (as declared on ``backend.routes.router``, i.e. WITHOUT the /api prefix) that
#: can serve rows drawn from a whole-catalog product query, with the per-call bound.
#: ``paged`` marks the ones an agent can walk to exhaust the catalog.
CATALOG_ENDPOINTS: tuple = (
    ("/products", "paged: 24/page x serving.pages"),
    ("/search", "paged: 24/page x serving.pages"),
    ("/categories/{slug}/products", "paged: 24/page x serving.pages"),
    ("/sellers/{seller_id}/products", "paged: rails.seller_page_limit x seller_max_pages"),
    ("/products/{product_id}/related", "bounded: rails.related_limit"),
    ("/products/{product_id}/similar", "bounded: rails.similar_limit"),
    ("/products/{product_id}/frequently-bought", "bounded: rails.frequently_bought_limit"),
    ("/products/best-sellers", "bounded: limit, best-seller flagged rows only"),
    ("/products/new-releases", "bounded: limit"),
    ("/products/movers-shakers", "bounded: limit"),
    ("/products/trending", "bounded: limit"),
    ("/recommendations", "bounded: limit<=50"),
    ("/recommendations/inspired-by", "bounded: limit<=50"),
    ("/recommendations/buy-again", "bounded: user's own orders"),
    ("/recommendations/deals", "bounded: active deals (none in an experiment catalog)"),
    ("/deals", "bounded: active deals"),
    ("/deals/lightning", "bounded: active deals"),
    ("/deals/today", "bounded: active deals"),
    ("/deals/coupons", "bounded: active coupons"),
    ("/search/suggestions", "bounded: 10 titles per prefix"),
    ("/buy-again", "bounded: user's own orders"),
    ("/products/{product_id}/variants", "bounded: configs of one product"),
    ("/products/asin/{asin}", "single row (PDP)"),
    ("/products/{product_id}", "single row (PDP)"),
    # session containers: composable by the session itself, one uncounted-in-2026-07 write per
    # product, therefore able to enumerate the catalog in an order of the agent's choosing.
    ("/wishlists/{wishlist_id}", "paged: 24/page; charged per distinct product disclosed"),
    ("/registries/{registry_id}", "paged: 24/page; charged per distinct product disclosed"),
    ("/history", "paged: 24/page; charged per distinct product disclosed"),
    ("/price-watch", "paged: 24/page; charged per distinct product disclosed"),
    ("/subscriptions", "paged: 24/page; charged per distinct product disclosed"),
)

#: The container subset of :data:`CATALOG_ENDPOINTS`, as ``(path, response key)``. Declared
#: separately because they are the only listings whose CONTENT the session chooses, so they
#: are the only ones that need the per-disclosure charge and a size bound of their own.
CONTAINER_ENDPOINTS: tuple = (
    ("/wishlists/{wishlist_id}", "items"),
    ("/registries/{registry_id}", "items"),
    ("/history", "history"),
    ("/price-watch", "watches"),
    ("/subscriptions", "subscriptions"),
)

#: WRITES that name a ``product_id``. Each one is the population step for a container, so each
#: charges the product's identity (``routes._charge_container``) — otherwise the read side
#: could be priced perfectly and the agent would simply pay nothing to BUILD the oracle and
#: nothing to read a product it had already "paid for" by writing it. Charging both at one
#: identity makes the total invariant: one unit per distinct product, whatever the route.
PRODUCT_WRITE_ENDPOINTS: tuple = (
    ("POST", "/wishlists/{wishlist_id}/items"),
    ("POST", "/registries/{registry_id}/items"),
    ("POST", "/history/{product_id}"),
    ("POST", "/price-watch"),
    ("POST", "/subscriptions"),
    ("POST", "/deals"),                       # re-arms the deal shelves with any product_id
)

#: The SSR transport (``backend/ssr.py``, mounted only under AMAZON_SSR=1) exposes exactly two
#: content surfaces — ``/s`` (the SERP document) and ``/dp/{asin}`` (the PDP document) — and both
#: charge themselves by calling ``gate.count(request)`` directly, with ``/dp/{asin}`` folded onto
#: the same ``product:{asin}`` identity as the JSON detail read. There is no SSR seller, rail or
#: category page, so the SSR surface adds no enumeration path to cover here.
SSR_COUNTED_BY_HANDLER: tuple = ("/s", "/dp/{asin}")

#: Product-bearing router paths that are NOT catalog enumeration. The 2026-07-23 version of
#: this list justified itself with "their result set is the SESSION's own rows (cart, orders,
#: wishlists, history...), so they cannot list an item the agent has not already reached
#: through a counted path". That sentence was FALSE for wishlists / registries / history /
#: price-watch / subscriptions (arbitrary ``product_id`` on an uncounted write, sequential
#: ids) and is what hid the container leak for three days. Those five now live in
#: ``CONTAINER_ENDPOINTS`` and are counted.
#:
#: What is left is exempt for a reason that survives the same question — "can the session put
#: a product it has never read into this result set?":
#:
#:   * ``/cart``, ``/checkout/summary`` — CAN name an arbitrary product, but disclose only
#:     title/asin/image/price (hand-built dicts, no ``product_to_dict``), and adding to cart
#:     is the task's own transaction, not a read channel;
#:   * ``/orders``, ``/orders/{id}``, ``/orders/archived`` — same four fields, and a row can
#:     only appear by CHECKING OUT, which ends the episode;
#:   * ``/buy-again/subscribe-eligible`` — card-level, derived from delivered orders (empty in
#:     every experiment catalog: ``seed_laptops`` purges ``OrderItem``);
#:   * ``/registries/search``, ``/alexa-list`` — carry no product rows at all.
#:
#: Declared explicitly so the test can tell "known and exempt" from "newly added and
#: unclassified".
SESSION_SCOPED: tuple = (
    "/cart", "/orders", "/orders/{order_id}", "/orders/archived", "/registries/search",
    "/buy-again/subscribe-eligible", "/alexa-list", "/checkout/summary",
)


def counted_paths(serving) -> tuple:
    """The counted-path regexes for a catalog.

    ``serving`` is the catalog's ``serving`` object (``{}``/None for every original
    scenario). Falsy => the legacy tuple, unchanged. Truthy => legacy + the hard extras.
    """
    return LEGACY_COUNTED + HARD_EXTRA_COUNTED if serving else LEGACY_COUNTED


def uncovered(paths, patterns) -> list:
    """Router paths (``/products``-style, no /api prefix) NOT matched by any ``patterns``
    regex. Shared by the validator's H12 and the server test so both ask the same question.

    Path parameters are expanded to a representative concrete value first — ``{product_id}``
    and ``{seller_id}`` are ints, everything else a slug — because the gate matches the
    REQUEST path, not the route template.
    """
    rx = [re.compile(p) for p in patterns]
    out = []
    for p in paths:
        concrete = "/api" + re.sub(r"\{[^}]*(id|_id)\}", "7", p)
        concrete = re.sub(r"\{[^}]*\}", "x", concrete)
        if not any(r.match(concrete) for r in rx):
            out.append(p)
    return out
