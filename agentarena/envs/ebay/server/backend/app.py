"""uvicorn entry: generic /api + the eBay compat (/ebay/*) + the real harvested Next.js
static export, served from one origin. The exported 404.html for unknown document
routes (the '/address' dead-end fix) is now served by the shared engine
(_storefront/app.py auto-detects the export's 404.html), so no local middleware."""

from pathlib import Path

from agentarena.envs._storefront.app import app, run  # noqa: F401
from backend.ebay_api import router as ebay_router

app.include_router(ebay_router)

STATIC = Path(__file__).resolve().parent.parent / "frontend"

if __name__ == "__main__":
    run(STATIC)
