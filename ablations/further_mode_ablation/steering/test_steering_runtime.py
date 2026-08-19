from __future__ import annotations

import os
import subprocess
import sys
import importlib
from types import SimpleNamespace

import pytest

from ablations.further_mode_ablation.steering import registry, runtime


def test_adapter_hook_intercepts_only_exact_custom_names(monkeypatch) -> None:
    from agentarena.envs.amazon import AmazonEnvironment

    runtime.install_adapter_hook()
    env = AmazonEnvironment()
    cat = SimpleNamespace(name="laptop")
    for condition in registry.CUSTOM_STEERING_ARMS:
        assert env._steering_spec_for(cat, condition) == registry.resolve_standard_arm(
            "laptop", condition
        )
    with pytest.raises(ValueError, match="unknown Standard steering ablation"):
        env._steering_spec_for(cat, "abl-not-real")


def test_adapter_hook_delegates_clean_and_all_canonical_names_unchanged() -> None:
    from agentarena.envs.amazon import AmazonEnvironment

    runtime.install_adapter_hook()
    wrapped = AmazonEnvironment._steering_spec_for
    delegate = wrapped._production_delegate
    env = AmazonEnvironment()
    cat = SimpleNamespace(name="laptop", serving={}, products=[])
    for condition in ("clean", *registry.CANONICAL_STEERING_ARMS):
        assert wrapped(env, cat, condition) == delegate(env, cat, condition)


def test_no_burial_paths_preserve_order_or_pin_without_facts(monkeypatch) -> None:
    # Exercise the server hook against the real backend module without starting a server.
    import sys

    server = registry.ROOT / "agentarena" / "envs" / "amazon" / "server"
    monkeypatch.syspath_prepend(str(server))
    assert runtime.install_backend_hook()
    from backend import experiment_laptops as module

    rows = [
        {"asin": "organic-a", "price": 10},
        {"asin": "lure-2", "price": 20},
        {"asin": "hero", "price": 30},
        {"asin": "lure-1", "price": 40},
    ]
    monkeypatch.setattr(
        module,
        "_steering",
        lambda: {
            "steering_id": "ranking",
            "decoy_skus": ["lure-1", "lure-2"],
            "params": {
                "pin": False,
                "placement": {
                    "schema": "further-mode-standard-placement-v1",
                    "pin": False,
                    "bury": False,
                    "bury_index": None,
                },
            },
        },
    )
    assert module.apply_steering(None, rows, lambda value: value) == rows

    module._steering()["params"]["placement"]["pin"] = True
    # The lambda above returns a fresh object each call, so replace it for the pin-only assertion.
    spec = {
        "steering_id": "ranking",
        "decoy_skus": ["lure-1", "lure-2"],
        "params": {
            "pin": True,
            "placement": {
                "schema": "further-mode-standard-placement-v1",
                "pin": True,
                "bury": False,
                "bury_index": None,
            },
        },
    }
    monkeypatch.setattr(module, "_steering", lambda: spec)
    assert module.apply_steering(None, rows, lambda value: value) == [
        rows[3], rows[1], rows[0], rows[2]
    ]


def test_sitecustomize_installs_backend_hook_in_server_subprocess() -> None:
    steering_dir = registry.HERE
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(steering_dir), str(registry.ROOT))),
        "AMAZON_EXPERIMENT_CATALOG": str(registry.CANONICAL_CATALOG_PATH),
    }
    code = (
        "from backend import experiment_laptops as m; "
        "assert getattr(m.apply_steering, "
        "'_further_mode_standard_steering_backend_v1', False)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=registry.ROOT / "agentarena" / "envs" / "amazon" / "server",
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_backend_hook_delegates_specs_without_private_control(monkeypatch) -> None:
    server = registry.ROOT / "agentarena" / "envs" / "amazon" / "server"
    monkeypatch.syspath_prepend(str(server))
    from backend import experiment_laptops as imported

    module = importlib.reload(imported)
    calls = []

    def production_delegate(session, products, to_dict, truthful_featured=False):
        calls.append((session, products, to_dict, truthful_featured))
        return ["production-result"]

    monkeypatch.setattr(module, "apply_steering", production_delegate)
    monkeypatch.setattr(module, "_steering", lambda: {"steering_id": "combined", "params": {}})
    assert runtime.install_backend_hook()
    assert module.apply_steering("session", ["rows"], "serializer", True) == [
        "production-result"
    ]
    assert calls == [("session", ["rows"], "serializer", True)]
