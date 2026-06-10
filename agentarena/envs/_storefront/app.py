"""uvicorn entry shared by every env. Serves ``/api`` plus the env's OWN harvested
frontend (a Next.js static export ``out/``, a Vite ``dist/``, a CRA ``build/`` or a
plain static dir) on a single origin. Each env's ``backend/app.py`` calls ``run()``
with its static dir; the layout/look is the real clone's, only the data is ours.
"""

from __future__ import annotations

import argparse
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from agentarena.envs._storefront.database import init_db, set_db_path
from agentarena.envs._storefront.routes import router

_ASSET_DIRS = ("_next", "assets", "static", "images", "img", "fonts", "media")

_static: Path | None = None
_reset_on_load = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Storefront API", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    app.include_router(router)
    return app


app = create_app()


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
        for cand in (static_dir / full_path,
                     static_dir / f"{full_path}.html",
                     static_dir / full_path / "index.html"):
            if cand.is_file():
                return FileResponse(cand)
        # SPA / document fallback -> re-seed on a real page load (clean per run)
        if _reset_on_load and "text/html" in request.headers.get("accept", ""):
            try:
                from agentarena.envs._storefront.seed import reset_database
                reset_database()
            except Exception as e:  # never 500 the page over a reset
                print(f"[reset_on_load] {e}")
        return FileResponse(static_dir / "index.html")


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
