#!/usr/bin/env python
"""Score whatever I (acting as the agent) just transacted against a LIVE serve_env.py server.

Reconstructs the env + the variant task, points a lightweight handle at the running server, runs the
env's own evaluate() over the most recent order/lead/booking, and also reports the validity oracle
(best P* any catalog item achieves — must be 1.0). No pre-snapshot, so the single txn I placed counts.

  python scripts/score_env.py instacart --condition steered --variant graded4 --port 8801
"""
import argparse
import dataclasses
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentarena.core.environment import ENVIRONMENTS  # noqa: E402
from agentarena.envs._storefront.scoring import oracle_pstar  # noqa: E402


def load(env):
    importlib.import_module(f"agentarena.envs.{env}")
    mod = sys.modules[f"agentarena.envs.{env}"]
    return ENVIRONMENTS.get(env)(), getattr(mod, "TASKS", [])


def pick_task(tasks, variant):
    for t in tasks:
        if t.task_id.endswith("-" + variant):
            return t
    return tasks[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("env")
    ap.add_argument("--condition", default="steered")
    ap.add_argument("--variant", default="graded4")
    ap.add_argument("--port", type=int, default=8801)
    a = ap.parse_args()

    env, tasks = load(a.env)
    task = dataclasses.replace(pick_task(tasks, a.variant), condition=a.condition)
    meta = getattr(task, "metadata", None) or {}
    handle = SimpleNamespace(base_url=f"http://127.0.0.1:{a.port}", proc=None)
    # no pre-snapshot: the one txn I placed is the "new" one
    env._pre = set()
    env._pre_bookings = set()
    ev = env.evaluate(handle, task)

    cat = env._catalog_obj(task.catalog)
    if a.env == "airbnb":
        cands = [l.attrs() for l in cat.listings]
    else:
        cands = [it.attrs() for it in cat.items if getattr(it, "role", "") != "addon"]
    orac = oracle_pstar(cands, task.preferences, meta.get("graded", {}),
                        variant=meta.get("variant", "graded"))

    out = {
        "env": a.env, "condition": a.condition, "variant": a.variant,
        "outcome": ev.outcome, "success": ev.success,
        "chosen_label": ev.chosen_label, "chosen_sku": ev.chosen,
        "took_bait": getattr(ev, "took_bait", None),
        "P": (ev.details or {}).get("preservation"),
        "P_star": (ev.details or {}).get("preservation_strict"),
        "all_in": (ev.details or {}).get("all_in") or (ev.details or {}).get("total_price"),
        "role": (ev.details or {}).get("role"),
        "violations": (ev.details or {}).get("violations"),
        "ORACLE_P_star_must_be_1.0": orac,
        "details": ev.details,
    }
    print("SCORE_JSON_BEGIN")
    print(json.dumps(out, indent=2, default=str))
    print("SCORE_JSON_END")
    return 0


if __name__ == "__main__":
    sys.exit(main())
