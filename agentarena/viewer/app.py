"""Viewer backend: a small FastAPI app that serves the SPA + a JSON API over the
results directory. ``serve(results_dir, port)`` is what the CLI calls.

API
    GET /api/experiments                      -> [{name, n, dims, kpis}]
    GET /api/experiments/{name}               -> {name, manifest, cells:[summary]}
    GET /api/trajectory/{name}/{cell}         -> full trajectory.json
    GET /api/image/{name}/{cell}/{step}       -> png
"""

from __future__ import annotations

import atexit
import importlib
import tempfile
import threading
from pathlib import Path

import json

STATIC = Path(__file__).resolve().parent / "static"

# --- manual "browse" mode: live env servers the user drives themselves (no agent) --- #
BROWSE_ENVS = ["amazon", "airbnb", "doordash", "ebay", "etsy", "fiverr",
               "instacart", "nike", "stockx", "zillow"]
_BROWSE_PORT_BASE = 9400
_live: dict[str, dict] = {}            # env -> {handle, url, condition}
_live_lock = threading.Lock()


def _alive(rec: dict) -> bool:
    try:
        return rec["handle"].proc.poll() is None
    except Exception:
        return False


def _stop_all() -> None:
    with _live_lock:
        for rec in list(_live.values()):
            try:
                rec["handle"].stop()
            except Exception:
                pass
        _live.clear()


atexit.register(_stop_all)


def _cells(exp_dir: Path) -> list[dict]:
    out = []
    for d in sorted(exp_dir.iterdir()):
        f = d / "summary.json"
        if d.is_dir() and f.exists():
            try:
                s = json.loads(f.read_text())
                s["cell"] = d.name
                out.append(s)
            except Exception:
                pass
    return out


def _kpis(cells: list[dict]) -> dict:
    n = len(cells) or 1
    done = [c for c in cells if c.get("outcome") not in ("error", "skipped", None)]
    return {
        "n": len(cells),
        "success": round(100 * sum(bool(c.get("success")) for c in cells) / n),
        "bait": round(100 * sum(bool(c.get("took_bait")) for c in cells) / n),
        "completed": round(100 * len([c for c in done if c.get("outcome") != "none"]) / n),
    }


def _experiments(res: Path) -> list[dict]:
    if not res.exists():
        return []
    out = []
    for d in sorted(res.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        cells = _cells(d)
        if not cells:
            continue
        dims = {k: sorted({str(c.get(k)) for c in cells if c.get(k) is not None})
                for k in ("env", "scaffold", "model", "task_id", "condition")}
        out.append({"name": d.name, "kpis": _kpis(cells), "dims": dims})
    return out


def create_app(results_dir: str | Path):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    res = Path(results_dir)
    app = FastAPI(title="agentarena viewer")

    @app.get("/api/experiments")
    def experiments():
        return JSONResponse(_experiments(res))

    @app.get("/api/experiments/{name}")
    def experiment(name: str):
        d = res / name
        if not d.exists():
            raise HTTPException(404)
        manifest = {}
        mf = d / "experiment.json"
        if mf.exists():
            manifest = json.loads(mf.read_text())
        return {"name": name, "manifest": manifest, "cells": _cells(d)}

    @app.get("/api/trajectory/{name}/{cell}")
    def trajectory(name: str, cell: str):
        f = res / name / cell / "trajectory.json"
        if not f.exists():
            raise HTTPException(404)
        return json.loads(f.read_text())

    @app.get("/api/image/{name}/{cell}/{step}")
    def image(name: str, cell: str, step: int):
        f = res / name / cell / f"step_{step:03d}.png"
        if not f.exists():
            raise HTTPException(404)
        return FileResponse(f, media_type="image/png")

    # ---- manual browse: launch a live env to navigate by hand (no agent) ------ #
    @app.get("/api/envs")
    def browse_envs():
        import agentarena.envs  # noqa: F401  (registers environments)
        from agentarena.core.environment import ENVIRONMENTS
        avail = set(ENVIRONMENTS.names())
        with _live_lock:
            return [{"env": e, "running": (e in _live and _alive(_live[e])),
                     "url": _live.get(e, {}).get("url"),
                     "condition": _live.get(e, {}).get("condition")}
                    for e in BROWSE_ENVS if e in avail]

    @app.post("/api/launch")
    def browse_launch(body: dict):
        import agentarena.envs  # noqa: F401
        from dataclasses import replace

        from agentarena.core.environment import ENVIRONMENTS
        name = (body or {}).get("env")
        condition = (body or {}).get("condition", "clean")
        if name not in BROWSE_ENVS:
            raise HTTPException(400, "unknown env")
        with _live_lock:
            cur = _live.get(name)
            if cur and _alive(cur) and cur["condition"] == condition:
                return {"env": name, "url": cur["url"], "condition": condition, "reused": True}
            if cur:
                try:
                    cur["handle"].stop()
                except Exception:
                    pass
                _live.pop(name, None)
            try:
                mod = importlib.import_module(f"agentarena.envs.{name}")
                task = replace(mod.TASKS[0], condition=condition)
                env = ENVIRONMENTS.create(name)
                port = _BROWSE_PORT_BASE + BROWSE_ENVS.index(name)
                handle = env.start(port, task, work_dir=Path(tempfile.mkdtemp(prefix=f"browse_{name}_")))
            except Exception as e:  # noqa: BLE001
                raise HTTPException(500, f"failed to launch {name}: {e}")
            url = env.start_url(handle.port, task)
            _live[name] = {"handle": handle, "url": url, "condition": condition}
            return {"env": name, "url": url, "condition": condition, "reused": False}

    @app.post("/api/stop")
    def browse_stop(body: dict):
        name = (body or {}).get("env")
        with _live_lock:
            cur = _live.pop(name, None)
        if cur:
            try:
                cur["handle"].stop()
            except Exception:
                pass
        return {"env": name, "stopped": bool(cur)}

    @app.on_event("shutdown")
    def _on_shutdown():
        _stop_all()

    app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
    return app


def serve(results_dir: str | Path = "results", port: int = 8800) -> None:
    import uvicorn
    print(f"\n  agentarena viewer → http://localhost:{port}/   (results: {results_dir})\n")
    uvicorn.run(create_app(results_dir), host="127.0.0.1", port=port, log_level="warning")
