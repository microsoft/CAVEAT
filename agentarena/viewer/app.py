"""Viewer backend: a small FastAPI app that serves the SPA + a JSON API over the
results directory. ``serve(results_dir, port)`` is what the CLI calls.

API
    GET /api/experiments                      -> [{name, n, dims, kpis}]
    GET /api/experiments/{name}               -> {name, manifest, cells:[summary]}
    GET /api/trajectory/{name}/{cell}         -> full trajectory.json
    GET /api/image/{name}/{cell}/{step}       -> png
"""

from __future__ import annotations

import json
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"


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

    app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
    return app


def serve(results_dir: str | Path = "results", port: int = 8800) -> None:
    import uvicorn
    print(f"\n  agentarena viewer → http://localhost:{port}/   (results: {results_dir})\n")
    uvicorn.run(create_app(results_dir), host="127.0.0.1", port=port, log_level="warning")
