"""Zillow environment — the real harvested Next.js 13 (pages-router) clone: the actual
Zillow-style UI (Mapbox map + property cards + listing page + contact-agent form), data
rewired from urql/GraphQL to a small same-origin /graphql responder backed by our catalog
+ steering. This is a LEAD env: the agent browses homes, opens a listing, and submits the
"schedule a tour" form (createMessage), which we write through to our generic lead so
evaluate() reads it back and scores the home on beds/baths/price. The agent starts on the
homes results page."""

from __future__ import annotations

from pathlib import Path

from ...core.environment import ENVIRONMENTS
from .._storefront.adapter import StorefrontEnvironment
from .catalog import CATALOGS


@ENVIRONMENTS.register("zillow")
class ZillowEnvironment(StorefrontEnvironment):
    name = "zillow"
    brand = "Zillow"
    server_dir = Path(__file__).resolve().parent / "server"
    catalogs = CATALOGS
    transaction = "lead"
    default_start_path = "/homes"
    # (Phase C) list_minimal/spec_budget removed: the /graphql search path always serves
    # CARD-shaped properties, GetHome* always the full record — anti-scrape is the shared
    # gate + rate-based Robot Check (gate.py; /graphql content ops are counted via
    # gate.count() in server/backend/zillow_gql.py).


from .tasks import TASKS  # noqa: E402,F401
