# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""uvicorn entry: generic /api + the caveat_kicks compat (/caveat_kicks/*) + the real CAVEAT-Kicks
webpack bundle, one origin."""

from pathlib import Path

from caveat.envs._storefront.app import app, run  # noqa: F401
from backend.caveat_kicks_api import api_router as caveat_kicks_follows_router
from backend.caveat_kicks_api import router as caveat_kicks_router

app.include_router(caveat_kicks_router)
app.include_router(caveat_kicks_follows_router)

STATIC = Path(__file__).resolve().parent.parent / "frontend"

if __name__ == "__main__":
    run(STATIC)
