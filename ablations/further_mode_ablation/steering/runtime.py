"""Command-local hooks for the Standard steering controls.

Only ``abl-*`` resolution and the two no-burial controls are intercepted.  Every canonical
condition delegates to the original adapter/backend functions without inspecting or rewriting
their result.
"""

from __future__ import annotations

import functools
import os
import sys
from pathlib import Path

from ablations.further_mode_ablation.steering.registry import (
    CUSTOM_STEERING_ARMS,
    resolve_standard_arm,
)


_ADAPTER_MARKER = "_further_mode_standard_steering_v1"
_BACKEND_MARKER = "_further_mode_standard_steering_backend_v1"
_CONTROL_SCHEMA = "further-mode-standard-placement-v1"


def install_adapter_hook() -> None:
    """Teach this command's Amazon adapter to resolve the five custom conditions."""

    from agentarena.envs.amazon import AmazonEnvironment, _split_condition

    current = AmazonEnvironment._steering_spec_for
    if getattr(current, _ADAPTER_MARKER, False):
        return

    @functools.wraps(current)
    def resolve(self, cat, condition):
        base, _, _ = _split_condition(condition)
        if base.startswith("abl-"):
            # resolve_standard_arm performs both exact-name and exact-catalog checks.
            return resolve_standard_arm(cat.name, base)
        return current(self, cat, condition)

    setattr(resolve, _ADAPTER_MARKER, True)
    setattr(resolve, "_production_delegate", current)
    AmazonEnvironment._steering_spec_for = resolve


def install_backend_hook() -> bool:
    """Install the no-burial execution path inside an Amazon server process.

    The standard backend always buries compliant products for any non-clean spec.  Substrate-only
    and pin-only controls require the same non-clean rails/images while preserving organic hero
    placement, so those two arms need this narrow path.  Burial and every canonical behavior still
    execute through the production function.
    """

    try:
        from backend import experiment_laptops as module
    except ModuleNotFoundError:
        # Expected in the outer launcher process; the adapter hook above is the only one needed
        # there.  Seed/server subprocesses run from the Amazon server directory and import backend.
        return False

    current = module.apply_steering
    if getattr(current, _BACKEND_MARKER, False):
        return True

    @functools.wraps(current)
    def apply(session, products, to_dict, truthful_featured=False):
        spec = module._steering()
        params = spec.get("params") or {}
        control = params.get("placement")
        if not isinstance(control, dict) or control.get("schema") != _CONTROL_SCHEMA:
            return current(
                session,
                products,
                to_dict,
                truthful_featured=truthful_featured,
            )

        pin = control.get("pin")
        bury = control.get("bury")
        if not isinstance(pin, bool) or not isinstance(bury, bool):
            raise ValueError("malformed command-local placement control")
        if bury:
            # Pure-burial and placement-depth controls are already expressible by production.
            return current(
                session,
                products,
                to_dict,
                truthful_featured=truthful_featured,
            )
        if module._type() != "ranking":
            raise ValueError("no-burial control must use the undecorated ranking path")
        if not pin:
            # Substrate-only: non-clean rails/enhanced imagery are active elsewhere; the SERP is
            # returned in its honest organic order with no factual or decorative modifications.
            return products

        # Pin-only: preserve production's filterability rule by pinning only lures present in the
        # caller's already-filtered rows.  There is no unique decoration in this control.
        by_asin = {row.get("asin"): row for row in products}
        pinned = [by_asin[asin] for asin in module._decoy_order() if asin in by_asin]
        decoys = set(module._decoy_order())
        rest = [row for row in products if row.get("asin") not in decoys]
        return pinned + rest

    setattr(apply, _BACKEND_MARKER, True)
    setattr(apply, "_production_delegate", current)
    module.apply_steering = apply
    return True


def install_command_local_hooks() -> None:
    install_adapter_hook()
    # At ``sitecustomize`` time CPython has not yet placed the server process's working
    # directory on sys.path reliably.  The adapter passes AMAZON_EXPERIMENT_CATALOG to both
    # seed and live-server subprocesses, so use that as the narrow process gate and add the
    # known server root explicitly.  The outer campaign process never imports backend.
    if os.environ.get("AMAZON_EXPERIMENT_CATALOG"):
        root = Path(__file__).resolve().parents[3]
        server = root / "agentarena" / "envs" / "amazon" / "server"
        if str(server) not in sys.path:
            sys.path.insert(0, str(server))
        if not install_backend_hook():
            raise RuntimeError("failed to install Standard steering backend hook")


def validate_runtime_registration() -> dict:
    """Return a small fail-closed registration receipt for launcher certification."""

    from agentarena.envs.amazon import AmazonEnvironment

    adapter = AmazonEnvironment._steering_spec_for
    if not getattr(adapter, _ADAPTER_MARKER, False):
        raise RuntimeError("Standard steering adapter hook is not installed")
    return {
        "version": "standard-steering-ablation-v1",
        "custom_conditions": list(CUSTOM_STEERING_ARMS),
        "adapter_hook": True,
    }
