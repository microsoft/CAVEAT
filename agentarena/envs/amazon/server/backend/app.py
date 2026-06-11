import argparse
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.database import init_db, set_db_path
from backend.routes import router

static_dir: Path | None = None
# When True, a full document load of the SPA (initial visit or browser refresh)
# resets the database to a clean seeded state. Set via the --reset-on-load CLI
# flag (default on).
reset_on_load: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Amazon API", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)

    images_dir = Path(__file__).parent / "images"
    if images_dir.exists():
        app.mount("/images", StaticFiles(directory=images_dir), name="images")

    return app


def mount_static(app: FastAPI, static_path: Path):
    global static_dir
    static_dir = static_path
    if static_dir.exists():
        app.mount(
            "/assets", StaticFiles(directory=static_dir / "assets"), name="assets"
        )

        @app.get("/{full_path:path}")
        async def serve_spa(request: Request, full_path: str):
            file_path = static_dir / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            # Falling through to the SPA shell means a real document load
            # (initial visit or a browser refresh) — client-side route changes
            # never hit the server. Gate the reset on an HTML Accept header so
            # XHR/asset requests that fall through don't trigger it.
            if reset_on_load and "text/html" in request.headers.get("accept", ""):
                try:
                    from backend.seed import reset_database

                    reset_database()
                except Exception as e:  # never 500 the page over a reset
                    print(f"[reset_on_load] reset failed: {e}")
            # The SPA shell must never be cached: its content-hashed asset references change on
            # every rebuild, so a stale cached index.html would load a deleted bundle (or show
            # old behavior). Assets under /assets keep their long-cache default (hashed names).
            return FileResponse(
                static_dir / "index.html",
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
        help="Reset the database to a clean seeded state on every page refresh "
        "(default: on). Use --no-reset-on-load to disable.",
    )
    args = parser.parse_args()

    global reset_on_load
    reset_on_load = args.reset_on_load

    set_db_path(args.db)

    static_dir = Path(__file__).parent.parent / "frontend" / "dist"
    mount_static(app, static_dir)

    print(f"\n  Amazon App running at http://{args.host}:{args.port}")
    print(f"  Database: {args.db}")
    print(f"  API docs at http://{args.host}:{args.port}/docs\n")

    uvicorn.run(
        "backend.app:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
