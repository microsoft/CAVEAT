"""uvicorn entry (``python -m backend.app``): serves /api + the real DoorDash static
export (``frontend/out``) on one origin."""

from pathlib import Path

from agentarena.envs._storefront.app import app, run  # noqa: F401

OUT = Path(__file__).resolve().parent.parent / "frontend" / "out"

if __name__ == "__main__":
    run(OUT)
