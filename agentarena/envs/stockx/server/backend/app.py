"""uvicorn entry: generic /api + the stockx compat (/stockx/*) + the real CopX
webpack bundle, one origin."""

from pathlib import Path

from agentarena.envs._storefront.app import app, run  # noqa: F401
from backend.stockx_api import api_router as stockx_follows_router
from backend.stockx_api import router as stockx_router

app.include_router(stockx_router)
app.include_router(stockx_follows_router)

STATIC = Path(__file__).resolve().parent.parent / "frontend"

if __name__ == "__main__":
    run(STATIC)
