"""uvicorn entry: generic /api + the etsy compat endpoints (/etsy/*) + the real Etsy
(Epsy) webpack bundle, one origin."""

from pathlib import Path

from caveat.envs._storefront.app import app, run  # noqa: F401
from backend.etsy_api import router as etsy_router

app.include_router(etsy_router)

STATIC = Path(__file__).resolve().parent.parent / "frontend"

if __name__ == "__main__":
    run(STATIC)
