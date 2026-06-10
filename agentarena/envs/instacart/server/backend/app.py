"""uvicorn entry: serves /api + the real Instacart static site (frontend/) on one origin."""

from pathlib import Path

from agentarena.envs._storefront.app import app, run  # noqa: F401

STATIC = Path(__file__).resolve().parent.parent / "frontend"

if __name__ == "__main__":
    run(STATIC)
