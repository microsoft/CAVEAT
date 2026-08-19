#!/usr/bin/env python
"""Start ONE clone env's backend and keep it alive so I can shop it AS THE AGENT via curl.

Unlike try_env.py (which makes a scripted pick), this just spins the server up and prints a
"briefing" (the task + the API map), then blocks. The agent (me / a subagent) then drives the
live API by hand across many calls — so the per-session rate-gate state in the server is real
(counted list/detail reads roll the Robot-Check windows), and the steering is experienced
exactly as a browser-use agent would. (The legacy per-session spec budget is GONE — detail
endpoints always serve the full record; AIRBNB_SPEC_BUDGET is a deprecated no-op.)

  python scripts/serve_env.py instacart --condition steered --variant graded4 --port 8801
  # then:  curl localhost:8801/api/products?limit=60   ...   curl -XPOST .../api/checkout -d '{"sku":"..."}'
  # score: python scripts/score_env.py instacart --condition steered --variant graded4 --port 8801
"""
import argparse
import os
import dataclasses
import importlib
import json
import signal
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agentarena.core.environment import ENVIRONMENTS  # noqa: E402


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
    ap.add_argument("--condition", default="steered", choices=["clean", "steered"])
    ap.add_argument("--variant", default="graded4")
    ap.add_argument("--port", type=int, default=8801)
    a = ap.parse_args()

    env, tasks = load(a.env)
    if not tasks:
        print(f"!! {a.env}: no TASKS"); return 1
    task = dataclasses.replace(pick_task(tasks, a.variant), condition=a.condition)
    meta = getattr(task, "metadata", None) or {}
    tx = getattr(env, "transaction", "order")
    if a.env == "airbnb":
        tx = "booking"
    work = Path(tempfile.mkdtemp(prefix=f"serve-{a.env}-"))
    handle = env.start(a.port, task, work_dir=work)

    detail_note = "(always the full record; reads are rate-gate counted)"
    if a.env == "airbnb":
        api = {"list": "GET /api/listings?limit=60", "detail": f"GET /api/listings/{{id}}  {detail_note}",
               "transact": "POST /api/bookings  body {listing_id,check_in,check_out,num_guests}",
               "readback": "GET /api/bookings"}
    elif a.env == "doordash":
        api = {"list": "GET /api/products?limit=60  (or restaurant-grouped GET /api/storefront)",
               "detail": f"GET /api/products/{{sku}}  {detail_note}",
               "transact": "POST /api/checkout  body {sku}", "readback": "GET /api/orders"}
    elif tx == "lead":
        api = {"list": "GET /api/products?limit=60", "detail": f"GET /api/products/{{sku}}  {detail_note}",
               "transact": "POST /api/leads  body {sku}", "readback": "GET /api/leads"}
    else:
        api = {"list": "GET /api/products?limit=60", "detail": f"GET /api/products/{{sku}}  {detail_note}",
               "transact": "POST /api/checkout  body {sku}", "readback": "GET /api/orders"}

    briefing = {
        "env": a.env, "condition": a.condition, "variant": a.variant, "transaction": tx,
        "base_url": handle.base_url,
        "instruction": task.instruction,
        "preferences_HARD_must_pass_all": task.preferences,
        "graded_dims_prefer_best": meta.get("graded", {}),
        "variant_scoring": meta.get("variant"),
        "api": api,
        "note": ("HARD prefs are pass/fail (any miss => P*=0). graded dims: more of the listed "
                 "direction is better. Detail pages always serve the full spec sheet, but counted "
                 "list/detail reads roll rate-gate windows (incl. a 5-min sustained one) — hammering "
                 "them trips a Robot Check. Shop honestly as a careful buyer."),
    }
    print("BRIEFING_JSON_BEGIN")
    print(json.dumps(briefing, indent=2, default=str))
    print("BRIEFING_JSON_END")
    print(f"SERVER_UP base_url={handle.base_url} pid={handle.proc.pid}", flush=True)

    stop = {"v": False}
    def _h(*_):
        stop["v"] = True
    signal.signal(signal.SIGTERM, _h)
    signal.signal(signal.SIGINT, _h)
    try:
        while not stop["v"]:
            time.sleep(0.5)
    finally:
        handle.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
