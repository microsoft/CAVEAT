"""uvicorn entry: generic /api + the zillow GraphQL responder (/graphql) + home-photo
SVGs + the real harvested Next.js static export, one origin."""

from pathlib import Path

from caveat.envs._storefront.app import app, run  # noqa: F401
from backend.zillow_gql import router as zillow_router

app.include_router(zillow_router)

STATIC = Path(__file__).resolve().parent.parent / "frontend"

if __name__ == "__main__":
    run(STATIC)
