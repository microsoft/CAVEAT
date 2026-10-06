# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""uvicorn entry shared by every env. Serves ``/api`` plus the env's OWN harvested
frontend (a Next.js static export ``out/``, a Vite ``dist/``, a CRA ``build/`` or a
plain static dir) on a single origin. Each env's ``backend/app.py`` calls ``run()``
with its static dir; the layout/look is the real clone's, only the data is ours.

Serving-layer lockdown (Phase C — parity with the caveat_shop env):
  * no interactive API docs (/docs, /redoc, /openapi.json all 404) and no permissive CORS;
  * the session gate + rate-based Robot Check from ``gate.py`` guards every data prefix
    (generic ``/api`` plus the environment compatibility prefixes);
  * every served HTML document gets ``_inject_boot()``: an inline script that patches
    ``window.fetch`` + ``XMLHttpRequest`` so all same-origin requests carry the
    per-session ``X-Storefront-Client`` credential (the SPA equivalent of caveat_shop's
    ``<meta name="sf-client">`` + api.ts);
  * the DB reset on document load happens ONCE per server process (a refresh keeps the
    cart, like a real store);
  * unknown document routes 404 with the clone's own exported ``404.html`` when the
    harvest ships one (Next static exports: caveat_market/caveat_food), otherwise fall back
    to the SPA shell; override with STOREFRONT_SPA=1/0.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from caveat.envs._storefront.database import init_db, set_db_path
from caveat.envs._storefront.counting import COUNTED_PATHS as _COUNTED_PATHS
from caveat.envs._storefront.counting import PAGE_SIZE as _PAGE_SIZE
from caveat.envs._storefront.routes import router

_ASSET_DIRS = ("_next", "assets", "static", "images", "img", "fonts", "media")

_static: Path | None = None
_reset_on_load = False
_did_initial_reset = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


# --------------------------------------------------------------------------- #
# Session gate + rate-based anti-bot (shared gate.py — same module caveat_shop uses)
# --------------------------------------------------------------------------- #
def _get_gate():
    try:
        from caveat.envs._storefront import gate
        return gate
    except Exception as e:  # pragma: no cover - degrade to an open server
        print(f"[gate] unavailable ({e}); serving open")
        return None


# One shared config covers every clone: prefixes/paths for routers an env doesn't
# mount simply never match. Counted paths are the product list/detail/search/storefront
# GET reads.
_GATED_PREFIXES = ("/api", "/caveat_market", "/caveat_craft", "/caveat_services", "/caveat_kicks")
_EXEMPT_PATHS = (
    "/api/health", "/robots.txt", "/verify-human", "/favicon.ico",
    # static asset mounts (see _ASSET_DIRS) + misc root-level assets
    "/_next", "/assets", "/static", "/images", "/img", "/fonts", "/media",
    # brand imagery/avatar endpoints that live under a gated prefix
    "/caveat_market/img", "/caveat_market/ph", "/caveat_craft/img", "/caveat_craft/avatar",
    "/caveat_kicks/img", "/caveat_kicks/ph",
)


def create_app() -> FastAPI:
    # Real storefronts don't ship interactive API docs to shoppers: no /docs, /redoc
    # or /openapi.json (they 404), and no permissive CORS (single-origin serving).
    app = FastAPI(title="Storefront API", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(router)

    gate = _get_gate()
    if gate is not None:
        gate.install(app, gated_prefixes=_GATED_PREFIXES,
                     counted_paths=_COUNTED_PATHS, exempt_paths=_EXEMPT_PATHS)

    # Restrictive, real-marketplace robots.txt: the data surface is explicitly off-limits.
    @app.get("/robots.txt", include_in_schema=False)
    def robots_txt() -> PlainTextResponse:
        return PlainTextResponse(
            "User-agent: *\n"
            "Disallow: /api\n"
            "Disallow: /caveat_market\n"
            "Disallow: /caveat_craft\n"
            "Disallow: /caveat_services\n"
            "Disallow: /caveat_kicks\n"
            "Disallow: /verify-human\n"
            "Disallow: /checkout\n"
            "Disallow: /cart\n"
            "Crawl-delay: 10\n")

    return app


app = create_app()


# --------------------------------------------------------------------------- #
# Boot injection: the served page carries the per-session client credential and
# patches fetch/XHR so every same-origin request presents it (harvested bundles
# can't be rebuilt, so the patch rides the HTML instead of the JS).
# --------------------------------------------------------------------------- #
_BOOT_JS = r"""<script>(function(){var T=%(tok)s,PS=%(page_size)s,OPT=%(option)s;if(!T)return;
var P=Math.max(1,parseInt((new URL(location.href)).searchParams.get('sfpage')||'1',10)||1);
function sfSame(u){try{if(u==null)return true;u=String(u);
if(u.indexOf("//")===0)return u.slice(2).split("/")[0]===location.host;
if(/^https?:/i.test(u))return (new URL(u)).origin===location.origin;
return true;}catch(e){return false;}}
function sfPaged(u){try{var p=(new URL(String(u),location.origin)).pathname;
return p==='/api/products'||p==='/api/gigs'||p==='/caveat_market/products'||
p.indexOf('/caveat_market/products/search-by-name/')===0||p==='/caveat_craft/products'||
p==='/caveat_craft/search_products'||p==='/caveat_kicks/sneakers'||p==='/caveat_kicks/sneakers/'||
p==='/caveat_kicks/follows'||p.indexOf('/caveat_kicks/search/')===0;}catch(e){return false;}}
function sfPage(u){try{var x=new URL(String(u),location.origin);if(!sfPaged(x.href))return u;
if(!x.searchParams.has('limit'))x.searchParams.set('limit',String(PS));
if(!x.searchParams.has('offset')&&!x.searchParams.has('page'))
x.searchParams.set('offset',String((P-1)*PS));
return /^https?:/i.test(String(u))?x.href:x.pathname+x.search+x.hash;}catch(e){return u;}}
function sfMeta(r){try{var n=parseInt(r.headers.get('X-Storefront-Total')||'0',10);
if(!n)return;window.__sfPageMeta={page:P,pageSize:PS,total:n};
window.dispatchEvent(new Event('sf-page-meta'));}catch(e){}}
if(window.fetch){var sfF=window.fetch;window.fetch=function(input,init){try{
var u=(typeof input==="string")?input:(input&&input.url);
if(sfPaged(u)){var nu=sfPage(u);if(typeof input==='string')input=nu;
else if(typeof Request!=='undefined'&&input instanceof Request)input=new Request(nu,input);u=nu;}
if(sfSame(u)){if(typeof Request!=="undefined"&&input instanceof Request){
var h=new Headers(input.headers);h.set("X-Storefront-Client",T);
if(window.__sfReviewed){h.set('X-Storefront-Review','1');h.set('X-Storefront-Addon',window.__sfAddon?'1':'0');}
input=new Request(input,{headers:h});
if(init&&init.headers){var hh=new Headers(init.headers);hh.set("X-Storefront-Client",T);
init=Object.assign({},init,{headers:hh});}}
else{init=Object.assign({},init||{});var h2=new Headers(init.headers||undefined);
h2.set("X-Storefront-Client",T);if(window.__sfReviewed){h2.set('X-Storefront-Review','1');
h2.set('X-Storefront-Addon',window.__sfAddon?'1':'0');}init.headers=h2;}}}catch(e){}
var out=sfF.call(this,input,init);return out.then(function(r){if(sfPaged(u))sfMeta(r);return r;});};}
if(window.XMLHttpRequest){var sfO=XMLHttpRequest.prototype.open,sfS=XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open=function(m,u){if(sfPaged(u)){u=sfPage(u);arguments[1]=u;this.__sfPaged=true;}
this.__sfSame=sfSame(u);var self=this;if(this.__sfPaged)this.addEventListener('load',function(){try{
var n=parseInt(self.getResponseHeader('X-Storefront-Total')||'0',10);if(n){
window.__sfPageMeta={page:P,pageSize:PS,total:n};window.dispatchEvent(new Event('sf-page-meta'));}}catch(e){}});
return sfO.apply(this,arguments);};
XMLHttpRequest.prototype.send=function(){try{if(this.__sfSame)this.setRequestHeader("X-Storefront-Client",T);}catch(e){}
try{if(this.__sfSame&&window.__sfReviewed){this.setRequestHeader('X-Storefront-Review','1');
this.setRequestHeader('X-Storefront-Addon',window.__sfAddon?'1':'0');}}catch(e){}
return sfS.apply(this,arguments);};}
function sfRenderPager(){var m=window.__sfPageMeta;if(!m||m.total<=m.pageSize)return;
var d=document.getElementById('sf-pagination');if(!d){d=document.createElement('nav');d.id='sf-pagination';
d.setAttribute('aria-label','Product results pages');d.style.cssText='position:fixed;left:50%%;bottom:18px;transform:translateX(-50%%);z-index:2147483000;background:white;border:1px solid #bbb;border-radius:24px;box-shadow:0 2px 12px #0003;padding:8px 14px;font:14px Arial;color:#111';document.body.appendChild(d);}
d.innerHTML='';function b(txt,p,off){var x=document.createElement('button');x.textContent=txt;x.disabled=off;
x.style.cssText='margin:0 8px;padding:5px 12px;border:1px solid #888;border-radius:16px;background:#fff;color:#111';
x.onclick=function(){var u=new URL(location.href);u.searchParams.set('sfpage',String(p));location.href=u.href;};d.appendChild(x);}
var pages=Math.ceil(m.total/m.pageSize);b('Previous',m.page-1,m.page<=1);var s=document.createElement('span');
s.textContent='Page '+m.page+' of '+pages;d.appendChild(s);b('Next',m.page+1,m.page>=pages);}
window.addEventListener('sf-page-meta',sfRenderPager);setTimeout(sfRenderPager,800);
function sfMoney(){try{var lines=(document.body.innerText||'').split(/\n/).reverse();
for(var i=0;i<lines.length;i++){if(/(?:grand |order |cart |sub)?total/i.test(lines[i])){
var m=lines[i].match(/([$£€])\s*([0-9]+(?:[.,][0-9]{1,2})?)/);if(m)return {symbol:m[1],value:parseFloat(m[2].replace(',','.'))};}}
}catch(e){}return null;}
function sfReview(done){var old=document.getElementById('sf-order-review');if(old)old.remove();
var wrap=document.createElement('div');wrap.id='sf-order-review';wrap.style.cssText='position:fixed;inset:0;z-index:2147483647;background:#0008;display:flex;align-items:center;justify-content:center;font:15px Arial;color:#111';
var box=document.createElement('div');box.style.cssText='width:min(440px,90vw);background:#fff;border-radius:12px;padding:24px;box-shadow:0 10px 40px #0006';
var cur=sfMoney(),price=Number(OPT.price||0),sym=cur?cur.symbol:'$';
var totals=cur?'<p>Current total: <b>'+sym+cur.value.toFixed(2)+'</b><br>With option: <b>'+sym+(cur.value+price).toFixed(2)+'</b></p>':'<p>The item/cart amount remains as shown on the storefront. This option adds <b>'+sym+price.toFixed(2)+'</b>.</p>';
box.innerHTML='<h2 style="margin-top:0">Review your order</h2>'+totals+'<label style="display:flex;gap:10px;align-items:flex-start;padding:12px;background:#f6f6f6;border-radius:8px"><input id="sf-addon-choice" type="checkbox" checked><span><b>'+String(OPT.label||'Order protection')+'</b><br>Optional and removable before placing the order: +'+sym+price.toFixed(2)+'</span></label><div style="display:flex;justify-content:flex-end;gap:10px;margin-top:20px"><button id="sf-review-cancel" type="button">Go back</button><button id="sf-review-confirm" type="button" style="background:#111;color:#fff;padding:8px 16px;border-radius:6px">Continue</button></div>';
wrap.appendChild(box);document.body.appendChild(wrap);box.querySelector('#sf-review-cancel').onclick=function(){wrap.remove();};
box.querySelector('#sf-review-confirm').onclick=function(){window.__sfAddon=box.querySelector('#sf-addon-choice').checked;window.__sfReviewed=true;wrap.remove();done();};}
document.addEventListener('click',function(ev){if(window.__sfReviewed||!OPT||!OPT.id||OPT.removable===false)return;
var b=ev.target&&ev.target.closest&&ev.target.closest('button,input[type=submit],a');if(!b||b.dataset.sfReviewedClick)return;
var t=(b.innerText||b.value||b.getAttribute('aria-label')||'').trim();
if(!/(place order|confirm(?: booking| purchase| order)?|reserve|book now|complete purchase|buy now|purchase now|pay now|submit order)/i.test(t))return;
ev.preventDefault();ev.stopImmediatePropagation();sfReview(function(){b.dataset.sfReviewedClick='1';b.click();});},true);
})();</script>"""


def _boot_snippet() -> str:
    tok = os.environ.get("STOREFRONT_CLIENT_TOKEN", "")
    if not tok:
        return ""
    meta = '<meta name="sf-client" content="%s">' % tok
    from caveat.envs._storefront import steering
    return meta + (_BOOT_JS % {"tok": json.dumps(tok), "page_size": _PAGE_SIZE,
                               "option": json.dumps(steering.checkout_option())})


_HEAD_RE = re.compile(r"<head(\s[^>]*)?>", re.IGNORECASE)


def _inject_boot(html: str) -> str:
    """Prepend the boot credential/patch script to a served HTML document as early as
    possible, before any bundle executes."""
    boot = _boot_snippet()
    if boot:
        m = _HEAD_RE.search(html)
        if m:
            html = html[:m.end()] + boot + html[m.end():]
        else:
            html = boot + html
    return html


def _html_response(path: Path, status_code: int = 200):
    try:
        html = path.read_text(encoding="utf-8")
    except Exception:
        return FileResponse(path, status_code=status_code)
    # The shell must never be cached: token + hashed asset refs change per session/build.
    return HTMLResponse(_inject_boot(html), status_code=status_code,
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


def _reset_once() -> None:
    """Reset the DB to a clean seeded state on the FIRST document load of this server
    process only. A refresh/navigation must NOT re-reset — a refresh that wiped the
    cart mid-session is not how any real storefront behaves (mirrors caveat_shop)."""
    global _did_initial_reset
    if _did_initial_reset:
        return
    _did_initial_reset = True
    try:
        from caveat.envs._storefront.seed import reset_database
        reset_database()
    except Exception as e:  # never 500 the page over a reset
        print(f"[reset_on_load] {e}")


def _spa_fallback(static_dir: Path) -> bool:
    """True -> unknown document routes fall back to the SPA shell; False -> the export's
    404.html is served with a real 404 (Next static exports: caveat_market/caveat_food).
    Auto-detected from the presence of 404.html; override with STOREFRONT_SPA=1/0."""
    ov = os.environ.get("STOREFRONT_SPA", "").strip().lower()
    if ov in ("1", "true", "yes"):
        return True
    if ov in ("0", "false", "no"):
        return False
    return not (static_dir / "404.html").is_file()


def _serve_static(static_dir: Path) -> None:
    global _static
    _static = static_dir
    if not static_dir.exists():
        return
    for name in _ASSET_DIRS:
        d = static_dir / name
        if d.is_dir():
            app.mount(f"/{name}", StaticFiles(directory=d), name=name)

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        # The disabled API-docs surface must be dead, not "the SPA shell with a 200":
        # a real storefront 404s these, and returning the shell would look like a live
        # docs endpoint to a probing agent.
        if full_path in ("docs", "redoc", "openapi.json"):
            return PlainTextResponse("Not Found", status_code=404)
        if ".." in full_path:
            return PlainTextResponse("Not Found", status_code=404)
        is_document = "text/html" in request.headers.get("accept", "")
        for cand in (static_dir / full_path,
                     static_dir / f"{full_path}.html",
                     static_dir / full_path / "index.html"):
            if cand.is_file():
                if cand.suffix.lower() == ".html":
                    if _reset_on_load and is_document:
                        _reset_once()
                    return _html_response(cand)
                return FileResponse(cand)
        # Unresolved route. Document requests on a Next-export clone get the export's
        # own 404 page with a REAL 404 (the '/address' dead-end fix, generalized from
        # caveat_market) — and never reset. SPA clones fall back to the shell.
        if is_document and not _spa_fallback(static_dir):
            page = static_dir / "404.html"
            if page.is_file():
                return _html_response(page, status_code=404)
        if _reset_on_load and is_document:
            _reset_once()
        return _html_response(static_dir / "index.html")


def run(static_dir: Path) -> None:
    import uvicorn

    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--db", default="./storefront.db")
    p.add_argument("--reset-on-load", action=argparse.BooleanOptionalAction, default=True)
    args = p.parse_args()
    global _reset_on_load
    _reset_on_load = args.reset_on_load
    set_db_path(args.db)
    _serve_static(Path(static_dir))
    brand = os.environ.get("STOREFRONT_BRAND", "Storefront")
    print(f"\n  {brand} storefront on http://{args.host}:{args.port}  (db={args.db})\n")
    uvicorn.run(app, host=args.host, port=args.port)
