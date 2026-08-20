"""uvicorn entry: generic /api + the caveat_craft compat endpoints (/caveat_craft/*) + the real CAVEAT-Craft
(CAVEAT-Craft) webpack bundle, one origin."""

from pathlib import Path

from caveat.envs._storefront.app import app, run  # noqa: F401
from backend.caveat_craft_api import router as caveat_craft_router

app.include_router(caveat_craft_router)

STATIC = Path(__file__).resolve().parent.parent / "frontend"

if __name__ == "__main__":
    run(STATIC)
