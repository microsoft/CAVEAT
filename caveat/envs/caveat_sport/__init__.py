# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

"""CAVEAT-Sport environment — the real harvested CAVEAT-Sport (CRA + Redux) store served by our
FastAPI. Browse shoes -> add to bag -> check out; we read back the placed order and
score the shoe (running vs lifestyle, price)."""

from __future__ import annotations

from pathlib import Path

from ...core.environment import ENVIRONMENTS
from .._storefront.adapter import StorefrontEnvironment
from .catalog import CATALOGS


@ENVIRONMENTS.register("caveat_sport")
class CaveatSportEnvironment(StorefrontEnvironment):
    name = "caveat_sport"
    brand = "CAVEAT-Sport"
    server_dir = Path(__file__).resolve().parent / "server"
    catalogs = CATALOGS
    transaction = "order"
    # The list uses the same truthful card whitelist in clean and steered serving; every
    # PDP is full. The shared request gate supplies a recoverable Robot Check.


from .tasks import TASKS  # noqa: E402,F401
