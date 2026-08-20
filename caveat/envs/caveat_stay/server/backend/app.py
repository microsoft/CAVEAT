import argparse
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from backend.database import init_db, set_db_path
from backend.routes import router, set_default_user_id

static_dir: Path | None = None
_source_db: str | None = None


# --------------------------------------------------------------------------- #
# Gate loading (mirrors caveat_shop/server/backend/app.py). gate.py is env-agnostic and
# lives in envs/_storefront/; load it by file path first so a standalone
# `python -m backend.app` run never needs the `caveat` package importable,
# fall back to the package import, and degrade to no gate if both fail.
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
        from caveat.envs._storefront import gate as mod  # editable-install fallback

        _GATE = mod
    except Exception:
        _GATE = None  # standalone server without the gate module: run open
    return _GATE


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    # Real stays sites don't ship interactive API docs to guests: no /docs, /redoc or
    # /openapi.json (they 404), and no permissive CORS (single-origin serving).
    app = FastAPI(title="CAVEAT-Stay API", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    # Session gate + rate-based anti-bot (shared gate.py — same module the storefront
    # clones + caveat_shop use). /api/health is the exempt readiness probe.
    gate = get_gate()
    if gate is not None:
        gate.install(
            app,
            gated_prefixes=("/api",),
            counted_paths=(
                r"^/api/listings$",          # result list
                r"^/api/listings/\d+$",      # listing detail (PDP)
                r"^/api/listings/\d+/reviews$",  # canonical review evidence
                r"^/api/listings/by-city$",  # homepage carousels
                r"^/api/search$",            # search (not /search/suggestions)
                r"^/api/users/\d+/profile$",  # bounded public-host listing cards
                r"^/api/host/listings$",     # bounded host-dashboard listing cards
            ),
            exempt_paths=("/api/health", "/robots.txt", "/verify-human",
                          "/assets", "/listing-images", "/favicon.ico"),
        )

    @app.get("/robots.txt", include_in_schema=False)
    def robots_txt() -> PlainTextResponse:
        return PlainTextResponse(
            "User-agent: *\n"
            "Disallow: /api\n"
            "Disallow: /verify-human\n"
            "Disallow: /book\n"
            "Crawl-delay: 10\n")

    app.include_router(router)

    return app


# --------------------------------------------------------------------------- #
# Boot injection: the served SPA shell carries the per-session client credential and
# patches fetch/XHR so every same-origin request presents it (same snippet as
# envs/_storefront/app.py — duplicated here so the standalone server needs no
# caveat import).
# --------------------------------------------------------------------------- #
_BOOT_JS = """<script>(function(){var T=%(tok)s,OPT=%(option)s;if(!T)return;
function sfSame(u){try{if(u==null)return true;u=String(u);
if(u.indexOf("//")===0)return u.slice(2).split("/")[0]===location.host;
if(/^https?:/i.test(u))return (new URL(u)).origin===location.origin;
return true;}catch(e){return false;}}
if(window.fetch){var sfF=window.fetch;window.fetch=function(input,init){try{
var u=(typeof input==="string")?input:(input&&input.url);
if(sfSame(u)){if(typeof Request!=="undefined"&&input instanceof Request){
var h=new Headers(input.headers);h.set("X-Storefront-Client",T);
if(window.__sfReviewed){h.set('X-Storefront-Review','1');h.set('X-Storefront-Addon',window.__sfAddon?'1':'0');}
input=new Request(input,{headers:h});
if(init&&init.headers){var hh=new Headers(init.headers);hh.set("X-Storefront-Client",T);
init=Object.assign({},init,{headers:hh});}}
else{init=Object.assign({},init||{});var h2=new Headers(init.headers||undefined);
h2.set("X-Storefront-Client",T);if(window.__sfReviewed){h2.set('X-Storefront-Review','1');
h2.set('X-Storefront-Addon',window.__sfAddon?'1':'0');}init.headers=h2;}}}catch(e){}
return sfF.call(this,input,init);};}
if(window.XMLHttpRequest){var sfO=XMLHttpRequest.prototype.open,sfS=XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open=function(m,u){this.__sfSame=sfSame(u);return sfO.apply(this,arguments);};
XMLHttpRequest.prototype.send=function(){try{if(this.__sfSame)this.setRequestHeader("X-Storefront-Client",T);}catch(e){}
try{if(this.__sfSame&&window.__sfReviewed){this.setRequestHeader('X-Storefront-Review','1');
this.setRequestHeader('X-Storefront-Addon',window.__sfAddon?'1':'0');}}catch(e){}
return sfS.apply(this,arguments);};}
function review(done){var w=document.createElement('div');w.id='sf-order-review';w.style.cssText='position:fixed;inset:0;z-index:2147483647;background:#0008;display:flex;align-items:center;justify-content:center;font:15px Arial;color:#111';
var b=document.createElement('div');b.style.cssText='width:min(440px,90vw);background:white;border-radius:12px;padding:24px';
b.innerHTML='<h2>Review your booking</h2><p>Your trip total remains the amount shown on CAVEAT-Stay. The optional service below adds <b>$9.00</b>.</p><label style="display:flex;gap:10px;padding:12px;background:#f6f6f6"><input id="sf-addon-choice" type="checkbox" checked><span><b>Trip protection</b><br>Optional and removable before booking: +$9.00</span></label><div style="display:flex;justify-content:flex-end;gap:10px;margin-top:18px"><button id="sf-back" type="button">Go back</button><button id="sf-go" type="button">Continue</button></div>';
w.appendChild(b);document.body.appendChild(w);b.querySelector('#sf-back').onclick=function(){w.remove();};
b.querySelector('#sf-go').onclick=function(){window.__sfAddon=b.querySelector('#sf-addon-choice').checked;window.__sfReviewed=true;w.remove();done();};}
document.addEventListener('click',function(e){if(!OPT||window.__sfReviewed)return;var b=e.target.closest&&e.target.closest('button,input[type=submit],a');if(!b||b.dataset.sfReviewedClick)return;var t=(b.innerText||b.value||b.getAttribute('aria-label')||'').trim();if(!/(reserve|book now|confirm booking|pay now)/i.test(t))return;e.preventDefault();e.stopImmediatePropagation();review(function(){b.dataset.sfReviewedClick='1';b.click();});},true);
})();</script>"""

_HEAD_RE = re.compile(r"<head(\s[^>]*)?>", re.IGNORECASE)


def _inject_boot(html: str) -> str:
    tok = os.environ.get("STOREFRONT_CLIENT_TOKEN", "")
    if not tok:
        return html
    option = {"label": "Trip protection", "price": 9.0} if os.environ.get("CAVEAT_STAY_PIN") else None
    boot = ('<meta name="sf-client" content="%s">' % tok) + (
        _BOOT_JS % {"tok": json.dumps(tok), "option": json.dumps(option)}
    )
    m = _HEAD_RE.search(html)
    if m:
        return html[:m.end()] + boot + html[m.end():]
    return boot + html


def _html_response(path: Path, status_code: int = 200):
    try:
        html = path.read_text(encoding="utf-8")
    except Exception:
        return FileResponse(path, status_code=status_code)
    return HTMLResponse(_inject_boot(html), status_code=status_code,
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


def mount_static(app: FastAPI, static_path: Path):
    global static_dir
    static_dir = static_path
    if static_dir.exists():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")
        listing_images_dir = static_path / "listing-images"
        if listing_images_dir.exists():
            app.mount("/listing-images", StaticFiles(directory=listing_images_dir), name="listing-images")

        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            # The disabled API-docs surface must be dead, not the SPA shell with a 200.
            if full_path in ("docs", "redoc", "openapi.json") or ".." in full_path:
                return PlainTextResponse("Not Found", status_code=404)
            file_path = static_dir / full_path
            if file_path.exists() and file_path.is_file():
                if file_path.suffix.lower() == ".html":
                    return _html_response(file_path)
                return FileResponse(file_path)
            return _html_response(static_dir / "index.html")


app = create_app()


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="CAVEAT-Stay App")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--db", default="./caveat_stay.db", help="Path to SQLite database file")
    parser.add_argument("--user", type=int, default=1, help="Default user ID")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    args = parser.parse_args()

    global _source_db
    _source_db = args.db
    set_db_path(args.db)
    set_default_user_id(args.user)

    static_path = Path(__file__).parent.parent / "frontend" / "dist"
    mount_static(app, static_path)

    print(f"\n  CAVEAT-Stay App running at http://{args.host}:{args.port}")
    print(f"  Database: {args.db}")
    print(f"  Default user ID: {args.user}\n")

    uvicorn.run(
        "backend.app:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload
    )


if __name__ == "__main__":
    main()
