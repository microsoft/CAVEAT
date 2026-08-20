"""uvicorn entry: generic /api + the caveat_services compat endpoints + the real CAVEAT-Services Vite
build (frontend/dist), one origin."""

from pathlib import Path

from caveat.envs._storefront.app import app, run  # noqa: F401
from backend.caveat_services_api import router as caveat_services_router

app.include_router(caveat_services_router)

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if __name__ == "__main__":
    run(DIST)
