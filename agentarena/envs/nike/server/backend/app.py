"""uvicorn entry: serves /api + the real Nike (CRA) build on one origin."""

from pathlib import Path

from agentarena.envs._storefront.app import app, run  # noqa: F401

BUILD = Path(__file__).resolve().parent.parent / "frontend" / "build"

if __name__ == "__main__":
    run(BUILD)
