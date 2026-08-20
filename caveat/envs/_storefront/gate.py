"""Session-scoped storefront gate + rate-based anti-bot (env-agnostic).

Real marketplaces expose no public shopper JSON API: the SPA's XHR surface is
session-scoped (only the storefront's own client credential passes) and abusive
read rates meet a rate-based, full-page "Robot Check" or a 503 — never silent
content edits. This module retrofits exactly that onto a mock storefront:

    gate.install(app, gated_prefixes=("/api",), counted_paths=(...), exempt_paths=(...))

Secrets (read from the process environment on every request, so tests can flip them):

* ``STOREFRONT_CLIENT_TOKEN`` — the per-session client credential the served page
  carries (SPA: a ``<meta name="sf-client">`` tag; SSR: an ``sf_client`` cookie set on
  document responses). Requests under a gated prefix must present it via the
  ``X-Storefront-Client`` header or the ``sf_client`` cookie. The legacy constant
  ``'web'`` does NOT pass while the gate is armed. If the variable is unset the token
  check is skipped (standalone/dev server stays usable).
* ``STOREFRONT_OPS_TOKEN`` — evaluator/ops back-channel via ``X-Storefront-Ops``;
  bypasses the gate AND the rate limiter, and is never counted.

Kill switch: ``CAVEAT_SHOP_API_GATE=0`` or ``STOREFRONT_API_GATE=0`` disables gating and
rate limiting entirely (adversarial cloaking conditions key on raw header absence and
must see the legacy surface).

Rate limiter (per-process = per-cell session): counted content reads (catalog list /
search / product detail — passed in as ``counted_paths`` regexes — plus whatever the
server explicitly feeds through :func:`count`, e.g. SSR document GETs of ``/s`` and
``/dp/*``) roll THREE windows: a short burst window, a long window, and a SUSTAINED
window (5 min). The sustained window is the anti-enumeration knob: the short/long
pair alone let a paced in-page fetch sweep (~1 req/s, under both) enumerate a
90-item catalog in minutes; 80/300s stops that sweep while an honest deep dig
(~40 counted reads spread over >=10 min) never comes near it. On breach a challenge
arms: gated endpoints answer 503 + Retry-After, and any document request gets a
full-page "Robot Check" interstitial ("Type the characters you see") whose form
POSTs to ``/verify-human``. A correct code entered no sooner than
``SF_CHALLENGE_MIN_DELAY`` seconds after issue clears the challenge and drains the
windows; the challenge also self-expires after ``SF_CHALLENGE_TTL`` seconds so a
stuck client is never deadlocked.

Counting mode (``count_mode`` / ``SF_COUNT_MODE``, default ``"request"``):

* ``"request"`` — every counted HTTP GET rolls one unit. This is the historical behaviour
  and stays the default, so no existing condition changes.
* ``"distinct"`` — the unit of charge is the unit of INFORMATION, not the HTTP request:
  one charge per DISTINCT product detail served and per DISTINCT list query, whichever
  path it arrived on, with re-reads free. It exists because request-counting silently
  prices the two access styles differently: rendering one product page in the SPA costs
  three counted requests (the detail fetch plus two "also viewed" rail list calls) for one
  product's specs, while a scripted API fetch of the same specs costs one — a 3x subsidy
  for scraping. Distinct-counting equalises them (and ``SF_RATE_SUSTAINED_MAX`` comes down
  to hold absolute pressure constant). Identities come from host-supplied
  ``identity_rules``; :func:`link` merges two identities for the same object (the id-URL
  and asin-URL forms of one product) so the charge really is path-independent. The seen-set
  is bounded and FAILS CLOSED — past the cap every hit is charged again, so an agent can
  never mint unlimited free reads by walking a huge key space.

One request can disclose MANY objects (a wishlist, a registry, a browsing history: N
products' rows in one payload). Pricing that as one unit hands N objects over for the price
of one — the same subsidy in a new wrapper. :func:`count_identities` is the hook for it: the
host passes the identity of every object the response discloses, and the gate charges the
ones this session has not paid for yet. Reading a container is then exactly as expensive as
opening the detail pages it stands in for, and no more.

Env knobs (all optional): SF_RATE_SHORT_WINDOW=10 / SF_RATE_SHORT_MAX=12,
SF_RATE_LONG_WINDOW=60 / SF_RATE_LONG_MAX=60, SF_RATE_SUSTAINED_WINDOW=300 /
SF_RATE_SUSTAINED_MAX=80, SF_CHALLENGE_MIN_DELAY=2, SF_CHALLENGE_TTL=45,
SF_RATE_ENABLED=1, SF_COUNT_MODE=request|distinct.

This file deliberately imports NOTHING from the surrounding package (env-agnostic;
the caveat_shop backend loads it by file path so a standalone server never needs the
``caveat`` package importable).
"""

from __future__ import annotations

import os
import re
import secrets
import threading
import time
from collections import deque
from typing import Iterable, Optional

try:  # fastapi/starlette are the host server's deps; only install() needs them.
    from fastapi import Request  # noqa: F401  (resolves the handlers' annotations)
except Exception:  # pragma: no cover - counting/threshold logic stays importable
    Request = None  # type: ignore[assignment]

__all__ = ["install", "count", "count_identities", "reset_state", "RateChallenged",
           "challenge_active", "count_mode", "link", "seen_count"]


# --------------------------------------------------------------------------- #
# Module state (per-process = per-cell session)
# --------------------------------------------------------------------------- #
_LOCK = threading.Lock()
_SHORT: deque = deque()
_LONG: deque = deque()
_SUSTAINED: deque = deque()
# None when idle, else {"code": str, "issued_at": ts, "armed_at": ts}
_CHALLENGE: Optional[dict] = None

_CFG = {
    "gated_prefixes": ("/api",),
    "counted": [],           # compiled regexes over the path (GET only)
    "exempt_prefixes": (),
    "installed": False,
    "count_mode": "request",  # install-time default; SF_COUNT_MODE overrides per request
    "identity": [],          # [(compiled regex, template)] -> distinct-count identity
}

# distinct-mode bookkeeping (per-process = per-cell session, exactly like the rate windows)
_SEEN: set = set()           # identities already charged for
_ALIAS: dict = {}            # identity -> canonical identity (see link())
# Bounded so a session can never grow the set without limit, and FAIL CLOSED: once the cap
# is reached every further hit is charged as if it were new. The alternative (evicting old
# entries) would hand an agent free re-reads by cycling keys; the alternative (growing
# forever) is a memory leak an adversary controls. 4096 >> any honest session's distinct
# reads (the largest hard catalog is 330 products x a handful of list queries).
_SEEN_CAP = 4096

_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no confusables


class RateChallenged(Exception):
    """Raised by count() when a counted hit breaches (or meets an armed challenge)."""

    def __init__(self, redirect: str = "/"):
        self.redirect = redirect
        super().__init__("rate challenge active")


# --------------------------------------------------------------------------- #
# Env accessors (read per-request so conditions/tests can flip them)
# --------------------------------------------------------------------------- #
def _env_num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _switch_off() -> bool:
    return (os.environ.get("CAVEAT_SHOP_API_GATE", "") == "0"
            or os.environ.get("STOREFRONT_API_GATE", "") == "0")


def _client_token() -> str:
    return os.environ.get("STOREFRONT_CLIENT_TOKEN", "")


def _ops_token() -> str:
    return os.environ.get("STOREFRONT_OPS_TOKEN", "")


def _rate_enabled() -> bool:
    return os.environ.get("SF_RATE_ENABLED", "1") != "0" and not _switch_off()


# --------------------------------------------------------------------------- #
# Classification helpers
# --------------------------------------------------------------------------- #
def _is_document(request) -> bool:
    if request.headers.get("sec-fetch-dest", "").lower() == "document":
        return True
    return "text/html" in request.headers.get("accept", "")


def _is_exempt(request, path: str) -> bool:
    if any(path == p or path.startswith(p.rstrip("/") + "/") or path.startswith(p)
           for p in _CFG["exempt_prefixes"]):
        return True
    # image subresource loads (product shots referenced by served pages)
    if request.method == "GET" and request.headers.get("sec-fetch-dest", "").lower() == "image":
        return True
    return False


def _is_gated(path: str) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/")
               for p in _CFG["gated_prefixes"])


def _is_counted(path: str, method: str) -> bool:
    return method == "GET" and any(rx.match(path) for rx in _CFG["counted"])


# --------------------------------------------------------------------------- #
# Distinct counting: one charge per distinct piece of CONTENT, not per request
# --------------------------------------------------------------------------- #
def count_mode() -> str:
    """``"request"`` (default) or ``"distinct"`` — env wins so a condition/test can flip it."""
    m = (os.environ.get("SF_COUNT_MODE", "") or _CFG["count_mode"] or "request").strip().lower()
    return m if m in ("request", "distinct") else "request"


def _canon_query(query: str) -> str:
    """Canonical form of a query string for identity purposes.

    Drops empty-valued params and folds ``offset`` into the equivalent ``page`` so the SPA's
    ``offset=(page-1)*limit`` form and a hand-written ``page=N`` request are ONE identity —
    two spellings of the same result set must not cost two charges."""
    if not query:
        return ""
    pairs = []
    for part in query.split("&"):
        if not part:
            continue
        k, _, v = part.partition("=")
        if v == "":
            continue
        pairs.append((k, v))
    d = dict(pairs)
    if "offset" in d:
        try:
            lim = int(d.get("limit") or 24) or 24
            d["page"] = str(int(d["offset"]) // lim + 1)
        except (TypeError, ValueError):
            pass
        d.pop("offset", None)
    if d.get("page") == "1":
        d.pop("page")                     # page 1 is the implicit default
    return "&".join(f"{k}={d[k]}" for k in sorted(d))


def _identity(path: str, query: str = "", method: str = "GET"):
    """The distinct-count identity of a request, or None to always charge.

    Host-supplied ``identity_rules`` map content paths onto a stable object identity (e.g.
    both ``/api/products/asin/B0X`` and ``/dp/B0X`` -> ``product:B0X``). Anything unmatched
    falls back to the canonical path+query, which is the right unit for a LIST read: a
    different query or a different page is different content, re-reading the same one is not.
    """
    for rx, template in _CFG["identity"]:
        m = rx.match(path)
        if m:
            try:
                return template.format(**m.groupdict())
            except (KeyError, IndexError):
                return template
    q = _canon_query(query)
    return f"{method} {path}" + (f"?{q}" if q else "")


def _resolve(key: str) -> str:
    seen = set()
    while key in _ALIAS and key not in seen:
        seen.add(key)
        key = _ALIAS[key]
    return key


def link(*keys: str) -> None:
    """Declare identities equivalent — e.g. the id-URL and asin-URL forms of one product.

    The host calls this from a handler that has resolved BOTH spellings (it holds the row),
    so from the first read of a product onward either URL form resolves to one identity and
    the charge is genuinely path-independent. No-op outside distinct mode."""
    if count_mode() != "distinct":
        return
    ks = [str(k) for k in keys if k]
    if len(ks) < 2:
        return
    with _LOCK:
        if len(_ALIAS) >= _SEEN_CAP:      # fail closed: stop merging rather than grow forever
            return
        resolved = [_resolve(k) for k in ks]
        canon = next((r for r in resolved if r in _SEEN), resolved[0])
        charged = any(r in _SEEN for r in resolved)
        for k, r in zip(ks, resolved):
            if r != canon:
                _ALIAS[r] = canon
            if k != canon:
                _ALIAS[k] = canon
        if charged:
            _SEEN.add(canon)


def seen_count() -> int:
    """Distinct identities charged so far (test/diagnostic hook)."""
    with _LOCK:
        return len(_SEEN)


def _charge_key(key: str) -> bool:
    """True when this IDENTITY must roll a unit (and mark it charged). Distinct mode only."""
    if not key:
        return True
    with _LOCK:
        key = _resolve(key)
        if key in _SEEN:
            return False                  # re-read of content already paid for: free
        if len(_SEEN) >= _SEEN_CAP:
            return True                   # FAIL CLOSED: past the cap nothing is free again
        _SEEN.add(key)
    return True


def _should_charge(path: str, query: str = "", method: str = "GET") -> bool:
    """True when this hit must roll the rate windows. Always True in request mode."""
    if count_mode() != "distinct":
        return True
    return _charge_key(_identity(path, query, method))


# --------------------------------------------------------------------------- #
# Rate windows + challenge state
# --------------------------------------------------------------------------- #
def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))


def _drain_windows_locked() -> None:
    _SHORT.clear()
    _LONG.clear()
    _SUSTAINED.clear()


def _challenge_status() -> Optional[dict]:
    """Current challenge dict, honouring TTL auto-expiry (clears state on expiry)."""
    global _CHALLENGE
    with _LOCK:
        ch = _CHALLENGE
        if ch is None:
            return None
        if time.time() - ch["armed_at"] > _env_num("SF_CHALLENGE_TTL", 45):
            _CHALLENGE = None
            _drain_windows_locked()
            return None
        return ch


def challenge_active() -> bool:
    return _challenge_status() is not None


def _record_hit(weight: int = 1) -> bool:
    """Roll one counted request into all three windows; True if this hit arms a challenge.

    ``weight`` lets a caller count a single request as N units, so a request that returns
    multiple content units can be charged like separate REST detail reads."""
    global _CHALLENGE
    now = time.time()
    sw = _env_num("SF_RATE_SHORT_WINDOW", 10)
    lw = _env_num("SF_RATE_LONG_WINDOW", 60)
    uw = _env_num("SF_RATE_SUSTAINED_WINDOW", 300)
    smax = _env_num("SF_RATE_SHORT_MAX", 12)
    lmax = _env_num("SF_RATE_LONG_MAX", 60)
    umax = _env_num("SF_RATE_SUSTAINED_MAX", 80)
    with _LOCK:
        for _ in range(max(1, int(weight))):
            _SHORT.append(now)
            _LONG.append(now)
            _SUSTAINED.append(now)
        while _SHORT and now - _SHORT[0] > sw:
            _SHORT.popleft()
        while _LONG and now - _LONG[0] > lw:
            _LONG.popleft()
        while _SUSTAINED and now - _SUSTAINED[0] > uw:
            _SUSTAINED.popleft()
        if len(_SHORT) > smax or len(_LONG) > lmax or len(_SUSTAINED) > umax:
            if _CHALLENGE is None:
                _CHALLENGE = {"code": _new_code(), "issued_at": now, "armed_at": now}
            return True
    return False


def count(request=None, weight: int = 1) -> None:
    """Explicit counting hook for server-rendered content pages (e.g. /s, /dp/*).

    Call at the top of a counted document handler. Raises :class:`RateChallenged`
    when the hit breaches the windows (install() registers an exception handler
    that renders the Robot Check interstitial), no-op when rate limiting is off
    or the caller presented the ops token. ``weight`` counts the request as N
    units in every window (see :func:`_record_hit`).
    """
    if not _rate_enabled():
        return
    redirect = "/"
    path, query, method = "", "", "GET"
    if request is not None:
        ops = _ops_token()
        if ops and request.headers.get("x-storefront-ops") == ops:
            return
        try:
            path, query = request.url.path, request.url.query
            method = request.method
            redirect = path + (("?" + query) if query else "")
        except Exception:
            pass
    if _challenge_status() is not None:
        raise RateChallenged(redirect)
    # distinct mode: an SSR document re-read of a product page already paid for is free,
    # and costs the same as the JSON detail fetch of the same product (identity_rules map
    # /dp/<asin> and /api/products/asin/<asin> onto one key).
    if not _should_charge(path, query, method):
        return
    if _record_hit(weight):
        raise RateChallenged(redirect)


def count_identities(request=None, identities: Iterable[str] = ()) -> None:
    """Charge one unit per DISTINCT object a single response reveals.

    :func:`count` prices a REQUEST. That is the right unit for a page of results, and the
    wrong one for a container: a wishlist / registry / browsing-history read hands back N
    products' rows in ONE request, so counting the request charges N objects the price of
    one — precisely the subsidy distinct-counting exists to remove. The host passes the
    identity of every object the response discloses, built from the SAME template the
    detail read uses (``product#{id}``), which buys three properties:

      * a product the session already read — through its PDP, through another container, or
        through an earlier read of this one — is FREE, because re-reads are free;
      * a product first disclosed here costs exactly what opening its detail page costs;
      * a session's total is the number of DISTINCT products it has seen, whatever mix of
        channels it used. There is no cheap channel left to find.

    In ``request`` mode there is no seen-set, so every disclosed row rolls a unit — the same
    pressure the N detail requests it replaces would have applied.

    Call it from the handler once the rows are resolved. Like :func:`count` it raises
    :class:`RateChallenged` on breach (install() renders the interstitial / 503) and is a
    no-op when rate limiting is off or the caller presented the ops token.
    """
    if not _rate_enabled():
        return
    redirect = "/"
    if request is not None:
        ops = _ops_token()
        if ops and request.headers.get("x-storefront-ops") == ops:
            return
        try:
            q = request.url.query
            redirect = request.url.path + (("?" + q) if q else "")
        except Exception:
            pass
    if _challenge_status() is not None:
        raise RateChallenged(redirect)
    distinct = count_mode() == "distinct"
    units, seen_here = 0, set()
    for key in identities:
        key = str(key or "")
        if not key or key in seen_here:
            continue                       # one row twice in one payload is one disclosure
        seen_here.add(key)
        if not distinct or _charge_key(key):
            units += 1
    if units and _record_hit(units):
        raise RateChallenged(redirect)


def reset_state() -> None:
    """Test hook: clear windows, any armed challenge, and the distinct-count bookkeeping."""
    global _CHALLENGE
    with _LOCK:
        _CHALLENGE = None
        _drain_windows_locked()
        _SEEN.clear()
        _ALIAS.clear()


# --------------------------------------------------------------------------- #
# Response bodies (brand-neutral, self-contained: no assets, inline styles)
# --------------------------------------------------------------------------- #
_PAGE_CSS = ("body{font-family:Arial,Helvetica,sans-serif;max-width:560px;margin:80px auto;"
             "padding:0 16px;color:#111}h1{font-size:22px}p{font-size:14px;line-height:1.5;"
             "color:#333}code{font-size:26px;letter-spacing:6px;font-weight:bold;"
             "background:#f5f5f5;padding:6px 14px;display:inline-block;margin:10px 0}"
             "input[type=text]{font-size:16px;padding:6px;width:180px}"
             "button{font-size:14px;padding:7px 18px;margin-left:6px}"
             "hr{border:none;border-top:1px solid #ddd;margin:24px 0}")


def _robot_check_403_html() -> str:
    return ("<!doctype html><html><head><meta charset='utf-8'><title>Robot Check</title>"
            f"<style>{_PAGE_CSS}</style></head><body>"
            "<h1>Robot Check</h1>"
            "<p>Sorry, we just need to make sure you're not a robot. This page could not be "
            "displayed because your request did not come from the storefront application.</p>"
            "<p>To continue shopping, please return to the store and browse as usual.</p>"
            "<hr><p><a href='/'>Back to the store</a></p></body></html>")


_WAF_403_HTML = ("<html><head><title>403 Forbidden</title></head><body>"
                 "<center><h1>403 Forbidden</h1></center>"
                 "<hr><center>storefront-edge</center></body></html>")


def _interstitial_html(code: str, redirect: str) -> str:
    from html import escape
    return ("<!doctype html><html><head><meta charset='utf-8'><title>Robot Check</title>"
            f"<style>{_PAGE_CSS}</style></head><body>"
            "<h1>Robot Check</h1>"
            "<p>Sorry, we just need to make sure you're not a robot. For best results, please "
            "make sure your browser is accepting cookies.</p>"
            f"<p>Type the characters you see:</p><code>{escape(code)}</code>"
            "<form method='post' action='/verify-human'>"
            f"<input type='hidden' name='redirect' value='{escape(redirect, quote=True)}'>"
            "<input type='text' name='code' autocomplete='off' autofocus>"
            "<button type='submit'>Continue shopping</button></form>"
            "<hr><p>Something went wrong? Wait a moment and try your request again.</p>"
            "</body></html>")


def _retry_after() -> int:
    ch = _challenge_status()
    if ch is None:
        return 5
    ttl = _env_num("SF_CHALLENGE_TTL", 45)
    return max(1, int(ttl - (time.time() - ch["armed_at"])) + 1)


def _redirect_target(request) -> str:
    try:
        q = request.url.query
        return request.url.path + (("?" + q) if q else "")
    except Exception:
        return "/"


# --------------------------------------------------------------------------- #
# install()
# --------------------------------------------------------------------------- #
def install(app, *, gated_prefixes: Iterable[str] = ("/api",),
            counted_paths: Iterable[str] = (), exempt_paths: Iterable[str] = (),
            count_mode: str = "request",
            identity_rules: Iterable = ()) -> None:
    """Wire the gate + rate limiter onto a FastAPI/Starlette app.

    Register AFTER any middleware whose behavior must be preserved when the gate is
    off (Starlette runs the last-added middleware first, so this one lands outermost).

    ``count_mode`` is the install-time default for the charging unit (``SF_COUNT_MODE``
    overrides it per request). ``identity_rules`` is an iterable of ``(pattern, template)``
    pairs, applied in order, mapping a counted path onto a distinct-count identity via named
    groups, e.g. ``(r"^/api/products/asin/(?P<asin>[^/]+)$", "product:{asin}")``. They live
    on the HOST side so this module stays env-agnostic.
    """
    from fastapi.responses import HTMLResponse, RedirectResponse, Response

    _CFG["gated_prefixes"] = tuple(gated_prefixes)
    _CFG["counted"] = [re.compile(p) for p in counted_paths]
    _CFG["exempt_prefixes"] = tuple(exempt_paths)
    _CFG["count_mode"] = (count_mode or "request")
    _CFG["identity"] = [(re.compile(p), t) for p, t in identity_rules]

    def _interstitial_response(request) -> Response:
        ch = _challenge_status()
        if ch is None:  # raced with TTL expiry: plain 503, next request is clean
            return Response("Service Unavailable", status_code=503,
                            headers={"Retry-After": "1"})
        return HTMLResponse(_interstitial_html(ch["code"], _redirect_target(request)),
                            status_code=503, headers={"Retry-After": str(_retry_after())})

    def _rate_503() -> Response:
        return Response(
            '{"detail":"Request throttled. Slow down and retry."}', status_code=503,
            media_type="application/json", headers={"Retry-After": str(_retry_after())})

    def _forbidden(request) -> Response:
        if _is_document(request):
            return HTMLResponse(_robot_check_403_html(), status_code=403)
        return HTMLResponse(_WAF_403_HTML, status_code=403)

    if not _CFG["installed"]:
        _CFG["installed"] = True

    @app.middleware("http")
    async def _storefront_gate(request, call_next):
        if _switch_off():
            return await call_next(request)
        path = request.url.path
        ops = _ops_token()
        if ops and request.headers.get("x-storefront-ops") == ops:
            return await call_next(request)                       # ops: allow, uncounted
        if _is_exempt(request, path):
            return await call_next(request)                       # health/robots/assets/...
        if _rate_enabled() and _challenge_status() is not None:
            if _is_document(request):
                return _interstitial_response(request)            # full-page Robot Check
            if _is_gated(path):
                return _rate_503()                                # XHR surface: 503 + Retry-After
            return await call_next(request)
        if _is_gated(path):
            tok = _client_token()
            if tok:
                supplied = (request.headers.get("x-storefront-client")
                            or request.cookies.get("sf_client"))
                if supplied != tok:
                    return _forbidden(request)
            if (_rate_enabled() and _is_counted(path, request.method)
                    and _should_charge(path, request.url.query, request.method)):
                if _record_hit():
                    if _is_document(request):
                        return _interstitial_response(request)
                    return _rate_503()
        return await call_next(request)

    async def _rate_challenged_handler(request, exc: RateChallenged):
        # Same split the middleware makes: a DOCUMENT request gets the full-page Robot Check,
        # a gated XHR gets the JSON 503 + Retry-After it can act on. Handler-side charging
        # (SSR documents via count(), container reads via count_identities()) used to render
        # the HTML interstitial into an XHR response body, which no storefront does.
        try:
            gated_xhr = _is_gated(request.url.path) and not _is_document(request)
        except Exception:
            gated_xhr = False
        return _rate_503() if gated_xhr else _interstitial_response(request)

    app.add_exception_handler(RateChallenged, _rate_challenged_handler)

    @app.get("/verify-human", include_in_schema=False)
    async def _verify_human_page(request: Request):
        ch = _challenge_status()
        if ch is None:
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(_interstitial_html(ch["code"], "/"))

    @app.post("/verify-human", include_in_schema=False)
    async def _verify_human(request: Request):
        global _CHALLENGE
        try:
            form = await request.form()
        except Exception:
            form = {}
        code = str(form.get("code") or "").strip().upper()
        redirect = str(form.get("redirect") or "/")
        if not redirect.startswith("/") or redirect.startswith("//"):
            redirect = "/"                                       # same-site only
        ch = _challenge_status()
        if ch is None:
            return RedirectResponse(redirect, status_code=303)
        min_delay = _env_num("SF_CHALLENGE_MIN_DELAY", 2)
        with _LOCK:
            ch = _CHALLENGE
            if ch is not None and code == ch["code"] and (time.time() - ch["issued_at"]) >= min_delay:
                _CHALLENGE = None
                _drain_windows_locked()
                return RedirectResponse(redirect, status_code=303)
            # wrong code, or answered faster than a human could read it: fresh code
            if ch is not None:
                ch["code"] = _new_code()
                ch["issued_at"] = time.time()
                fresh = ch["code"]
            else:
                return RedirectResponse(redirect, status_code=303)
        return HTMLResponse(_interstitial_html(fresh, redirect), status_code=200)
