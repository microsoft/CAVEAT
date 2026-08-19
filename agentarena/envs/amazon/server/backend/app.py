import argparse
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from backend.database import init_db, set_db_path
from backend.routes import router

static_dir: Path | None = None
# When True, the FIRST full document load of the SPA in this server process resets
# the database to a clean seeded state (mirrors ssr.py's _reset_once). Subsequent
# refreshes/navigations must NOT re-reset — a refresh that wiped the cart mid-session
# is not how any real storefront behaves. Set via the --reset-on-load CLI flag
# (default on).
reset_on_load: bool = False
_did_initial_reset = False


def _reset_once() -> None:
    global _did_initial_reset
    if _did_initial_reset:
        return
    _did_initial_reset = True
    try:
        from backend.seed import reset_database

        reset_database()
    except Exception as e:  # never 500 the page over a reset
        print(f"[reset_on_load] reset failed: {e}")


# --------------------------------------------------------------------------- #
# Gate loading. gate.py is env-agnostic and lives in envs/_storefront/; load it by
# file path first so a standalone `python -m backend.app` run never needs the
# `agentarena` package importable (and never pays its import side effects), fall
# back to the package import, and degrade to no gate if both fail.
# --------------------------------------------------------------------------- #
_GATE = None
_GATE_TRIED = False


def get_gate():
    global _GATE, _GATE_TRIED
    if _GATE_TRIED:
        return _GATE
    _GATE_TRIED = True
    if "storefront_gate" in sys.modules:
        _GATE = sys.modules["storefront_gate"]
        return _GATE
    gate_path = Path(__file__).resolve().parents[3] / "_storefront" / "gate.py"
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("storefront_gate", gate_path)
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            sys.modules["storefront_gate"] = mod
            spec.loader.exec_module(mod)
            _GATE = mod
            return _GATE
    except Exception as e:
        sys.modules.pop("storefront_gate", None)
        print(f"[gate] file load failed ({e}); trying package import")
    try:
        from agentarena.envs._storefront import gate as mod  # editable-install fallback

        _GATE = mod
    except Exception:
        _GATE = None  # standalone server without the gate module: run open
    return _GATE


def _count_mode_default() -> str:
    """The gate's charging unit for THIS storefront, from the served catalog's serving.rate.

    Returns "request" (today's behaviour) for every catalog without a ``serving.rate.mode``
    — i.e. all five original scenarios and the stock demo store. Never raises: a storefront
    that cannot read its experiment catalog simply counts requests.
    """
    try:
        from backend.experiment_laptops import rate_cfg

        mode = str(rate_cfg().get("mode") or "request").strip().lower()
        return mode if mode in ("request", "distinct") else "request"
    except Exception:  # noqa: BLE001
        return "request"


def _counted_paths() -> tuple:
    """The gate's counted surface for THIS storefront (see ``backend/counting.py``).

    A catalog WITHOUT a ``serving`` object gets ``counting.LEGACY_COUNTED`` — the four
    historical regexes, unchanged — so every original scenario keeps its exact rate
    behaviour. A hard catalog additionally counts every OTHER endpoint that can enumerate
    the catalog (seller storefront, category browse, PDP rails, home shelves), because a
    surface that hands over rows for free is a complete bypass of the rate gate no matter
    how carefully the SERP is priced. Never raises: on any failure the legacy tuple stands.
    """
    try:
        from backend.counting import counted_paths
        from backend.experiment_laptops import serving

        return counted_paths(serving())
    except Exception as e:  # noqa: BLE001
        print(f"[gate] counted-path policy unavailable ({e}); using the legacy surface")
        return (r"^/api/products$", r"^/api/search$",
                r"^/api/products/asin/[^/]+$", r"^/api/products/\d+$")


def _identity_rules() -> tuple:
    """Distinct-count identity rules (see ``backend/counting.py``); inert in request mode."""
    try:
        from backend.counting import IDENTITY_RULES

        return IDENTITY_RULES
    except Exception:  # noqa: BLE001
        return ((r"^/api/products/asin/(?P<asin>[^/?]+)$", "product:{asin}"),
                (r"^/dp/(?P<asin>[^/?]+)$", "product:{asin}"),
                (r"^/api/products/(?P<pid>\d+)(?:/.*)?$", "product#{pid}"))


_IDENTITY_RULES = _identity_rules()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    # Real storefronts don't ship interactive API docs to shoppers: no /docs,
    # /redoc, or /openapi.json (they 404), and no permissive CORS.
    app = FastAPI(title="Amazon API", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    # Adversarial "client cloaking" family: mark whether this request came from the storefront's
    # own web client. The SPA's api.ts sends X-Storefront-Client on every call, so a request
    # WITHOUT it is a program reading the JSON API directly rather than the page a human is
    # looking at. Real storefronts separate official-client traffic from raw API traffic exactly
    # this way; here it is the signal a cloaking condition keys on to serve the two audiences
    # different product data. Inert for every non-adversarial condition.
    @app.middleware("http")
    async def _mark_client(request: Request, call_next):
        from backend.adversarial import OFFICIAL_CLIENT
        tok = OFFICIAL_CLIENT.set(bool(request.headers.get("x-storefront-client")))
        try:
            return await call_next(request)
        finally:
            OFFICIAL_CLIENT.reset(tok)

    # Session gate + rate-based anti-bot. Registered AFTER _mark_client (Starlette runs
    # the last-added middleware first) so the gate sits OUTSIDE it: when the gate is off
    # (AMAZON_API_GATE=0 — the adversarial conditions) every request still flows through
    # _mark_client untouched and header-absence semantics are preserved.
    gate = get_gate()
    if gate is not None:
        gate.install(
            app,
            gated_prefixes=("/api", "/ssr"),
            # The counted surface is DATA (backend/counting.py), not a literal here: the
            # validator's H12 and the server tests read the same tuple, so an endpoint that
            # can enumerate the catalog cannot quietly stay uncounted.
            counted_paths=_counted_paths(),
            exempt_paths=("/api/health", "/robots.txt", "/verify-human",
                          "/assets", "/images", "/favicon.ico", "/vite.svg"),
            # Charging unit. "request" (the historical default, and what every original
            # scenario gets) counts HTTP GETs; the hard tier's catalog can ask for
            # "distinct" via serving.rate.mode, which counts distinct CONTENT instead — see
            # _storefront/gate.py. SF_COUNT_MODE overrides this per request.
            count_mode=_count_mode_default(),
            # How a counted path maps onto a distinct-count identity. The two URL spellings
            # of a product detail (asin under the SPA, id under a hand-rolled API caller) and
            # the SSR document form all collapse onto one identity; routes.py additionally
            # gate.link()s the id and asin forms once it has resolved a row, which closes the
            # remaining gap so the charge is fully path-independent. The third rule also
            # swallows the PDP RAILS (/related, /similar, /frequently-bought), so a product
            # page rendered in the SPA costs ONE unit rather than three — counting the rails
            # (which the hard tier now does) must not re-introduce the scraping subsidy with
            # the sign flipped. Everything else (list / search / seller / category / SSR
            # SERP) falls through to the canonical path+query identity, i.e. one charge per
            # distinct list query, which is exactly what a SERP page costs.
            identity_rules=_IDENTITY_RULES,
        )

    # Restrictive, Amazon-style robots.txt: the data surface is explicitly off-limits.
    @app.get("/robots.txt", include_in_schema=False)
    def robots_txt() -> PlainTextResponse:
        return PlainTextResponse(
            "User-agent: *\n"
            "Disallow: /api\n"
            "Disallow: /ssr/\n"
            "Disallow: /verify-human\n"
            "Disallow: /gp/\n"
            "Disallow: /checkout\n"
            "Disallow: /cart\n"
            "Crawl-delay: 10\n"
        )

    app.include_router(router)

    images_dir = Path(__file__).parent / "images"
    if images_dir.exists():
        app.mount("/images", StaticFiles(directory=images_dir), name="images")

    return app


def _index_html_with_meta(index_path: Path) -> str:
    """The SPA shell with the per-session client credential injected at serve time.

    api.ts reads <meta name="sf-client"> and echoes it as X-Storefront-Client on every
    XHR — the same session-scoped pattern a real SPA marketplace uses. Falls back to
    the legacy 'web' constant when no token is configured (dev server, gate off)."""
    raw = index_path.read_text()
    tok = os.environ.get("STOREFRONT_CLIENT_TOKEN") or "web"
    tag = f'<meta name="sf-client" content="{tok}" />'
    if 'name="sf-client"' in raw:
        return raw
    if "<head>" in raw:
        return raw.replace("<head>", f"<head>\n    {tag}", 1)
    return tag + raw


def mount_static(app: FastAPI, static_path: Path):
    global static_dir
    static_dir = static_path
    if static_dir.exists():
        app.mount(
            "/assets", StaticFiles(directory=static_dir / "assets"), name="assets"
        )

        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            # The disabled API-docs surface must be dead, not "the SPA shell with a
            # 200": a real storefront 404s these, and returning the shell here would
            # look like a live docs endpoint to a probing agent.
            if full_path in ("docs", "redoc", "openapi.json"):
                return PlainTextResponse("Not Found", status_code=404)
            file_path = static_dir / full_path
            if (full_path and full_path != "index.html"
                    and file_path.exists() and file_path.is_file()):
                return FileResponse(file_path)
            # Falling through to the SPA shell means a real document load (initial
            # visit or a browser refresh) — client-side route changes never hit the
            # server. Reset ONCE per server process (initial load only): a refresh
            # must keep the cart/session, like a real store. Gate the reset on an
            # HTML Accept header so XHR/asset requests that fall through don't
            # trigger it.
            if reset_on_load and "text/html" in request.headers.get("accept", ""):
                _reset_once()
            # The SPA shell must never be cached: its content-hashed asset references change on
            # every rebuild, so a stale cached index.html would load a deleted bundle (or show
            # old behavior). Assets under /assets keep their long-cache default (hashed names).
            return HTMLResponse(
                _index_html_with_meta(static_dir / "index.html"),
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )


app = create_app()


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="Amazon App")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument(
        "--db", default="./amazon.db", help="Path to SQLite database file"
    )
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    parser.add_argument(
        "--reset-on-load",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reset the database to a clean seeded state on the FIRST page load of "
        "this server process (default: on). Use --no-reset-on-load to disable.",
    )
    args = parser.parse_args()

    global reset_on_load
    reset_on_load = args.reset_on_load

    set_db_path(args.db)

    if os.environ.get("AMAZON_SSR") == "1":
        # `-ssr` transport control: serve the storefront as server-rendered HTML and 404
        # the product-data JSON surface (see backend/ssr.py). The SPA is not mounted, so
        # reset_on_load never fires; ssr.py does the one-time initial reset itself.
        from backend.ssr import ssr_api_guard_middleware, ssr_router
        ssr_api_guard_middleware(app)
        app.include_router(ssr_router)
    else:
        static_dir = Path(__file__).parent.parent / "frontend" / "dist"
        mount_static(app, static_dir)

    print(f"\n  Amazon App running at http://{args.host}:{args.port}")
    print(f"  Database: {args.db}\n")

    uvicorn.run(
        "backend.app:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
