"""uvicorn entry: generic /api + the fiverr compat endpoints + the real Fiverr Vite
build (frontend/dist), one origin."""

from pathlib import Path

from agentarena.envs._storefront.app import app, run  # noqa: F401
from backend.fiverr_api import router as fiverr_router

app.include_router(fiverr_router)

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if __name__ == "__main__":
    run(DIST)
